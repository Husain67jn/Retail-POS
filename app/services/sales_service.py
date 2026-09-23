from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.database.models.customer import Customer
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.database.models.invoice import Invoice
from app.database.models.invoice_item import InvoiceItem
from app.repositories.customer_ledger_repository import CustomerLedgerRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.invoice_item_repository import InvoiceItemRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.product_repository import ProductRepository
from app.services.product_service import decimal_value, validate_money
from app.utils.formatting import format_invoice_number

@dataclass
class CartLine:
    product_id: int
    quantity: Decimal
    item_discount: Decimal = Decimal("0.00")
    unit: str = ""
    unit_price: Decimal = Decimal("0.00")
    product_name: str = ""
    line_total: Decimal = Decimal("0.00")

class Cart:
    def __init__(self):
        self.lines: dict[int, CartLine] = {}
        self.bill_discount = Decimal("0.00")

    def add(self, product, quantity=Decimal("1"), item_discount=Decimal("0"), unit_price=None):
        q = _quantity(quantity, product.unit)
        d = validate_money(item_discount, "Item discount")
        if q <= 0: raise ValueError("Quantity must be greater than zero")
        if d < 0: raise ValueError("Item discount cannot be negative")
        # unit_price lets a caller (the POS wholesale/retail toggle) override
        # the price a line is added/re-priced at; omitted, behavior is
        # unchanged from before this parameter existed.
        line = self.lines.get(product.id)
        if line:
            line.quantity += q
            line.item_discount = d
            if unit_price is not None:
                price = validate_money(unit_price, "Unit price")
                if price < 0: raise ValueError("Unit price cannot be negative")
                line.unit_price = price
        else:
            if unit_price is not None:
                price = validate_money(unit_price, "Unit price")
                if price < 0: raise ValueError("Unit price cannot be negative")
            else:
                price = Decimal(product.selling_price)
            self.lines[product.id] = CartLine(product.id, q, d, product.unit, price, product.name)
            line = self.lines[product.id]
        self._calc_line(line)

    def set_quantity(self, product_id, quantity):
        line = self.lines.get(product_id)
        if line is None: raise ValueError("Product is not in the cart")
        q = _quantity(quantity, line.unit)
        if q <= 0: raise ValueError("Quantity must be greater than zero")
        line.quantity = q; self._calc_line(line)

    def set_item_discount(self, product_id, discount):
        d = validate_money(discount, "Item discount")
        if d < 0: raise ValueError("Item discount cannot be negative")
        self.lines[product_id].item_discount = d; self._calc_line(self.lines[product_id])

    def remove(self, product_id): self.lines.pop(product_id, None)
    def clear(self): self.lines.clear(); self.bill_discount = Decimal("0.00")

    def set_bill_discount(self, discount):
        d = validate_money(discount if str(discount).strip() else "0", "Bill discount")
        if d < 0: raise ValueError("Bill discount cannot be negative")
        self.bill_discount = d; self.totals()

    @staticmethod
    def _calc_line(line):
        gross = (line.quantity * line.unit_price).quantize(Decimal("0.01"))
        if line.item_discount > gross: raise ValueError("Discount cannot exceed the item total")
        line.line_total = gross - line.item_discount

    def totals(self):
        gross = sum(((l.quantity * l.unit_price).quantize(Decimal("0.01")) for l in self.lines.values()), Decimal("0.00"))
        item_discounts = sum((l.item_discount for l in self.lines.values()), Decimal("0.00"))
        if self.bill_discount > gross - item_discounts:
            raise ValueError("Bill discount cannot exceed the subtotal after item discounts")
        discount_total = item_discounts + self.bill_discount
        return gross, discount_total, gross - discount_total

# Units counted as whole items only -- a fractional "1.5 boxes"/"0.5 pieces"
# is a data-entry mistake, so these stay integer-only. The continuous/measured
# units are deliberately NOT listed: "meter" (wire/cable cut to length) and
# "kg" (goods sold by weight) must accept fractional quantities like 1.5 or
# 0.5 -- exactly what the counter needs. (To allow fractions for every unit,
# empty this set; to forbid them for a new discrete unit, add it here.)
DISCRETE_UNITS = {"piece", "dozen", "box", "packet"}

def _quantity(value, unit: str | None = None):
    d = decimal_value(value, "Quantity")
    if d.as_tuple().exponent < -6: raise ValueError("Quantity cannot have more than 6 decimal places")
    if unit in DISCRETE_UNITS and d != d.to_integral_value():
        raise ValueError(f"Fractional quantities are not allowed for {unit} products")
    return d

def _money(value, field): return validate_money(value, field)


@dataclass
class InvoiceDeletion:
    """What a delete_invoice() call actually removed, for the UI's confirmation."""
    invoice_number: str
    customer_id: int | None
    customer_name: str | None
    # How much this invoice was contributing to the customer's outstanding
    # (khata) balance. Removing the invoice's ledger rows takes exactly this
    # much off that balance. 0.00 for a cash sale, or for a credit sale whose
    # ledger impact was already reversed by void_invoice().
    balance_reduction: Decimal


def delete_invoice(session, invoice_id: int) -> InvoiceDeletion:
    """Permanently delete an invoice, its items, and its customer-ledger impact.

    No stock or product-quantity logic is involved: this app does not track
    inventory, so nothing is restored to any product.

    Two schema facts drive the order of operations here:

    * ``customer_ledger_entries.invoice_id`` is ``ON DELETE RESTRICT``, and the
      ``Invoice.ledger_entries`` relationship carries no delete cascade — so a
      bare ``session.delete(invoice)`` would either hit an IntegrityError or
      (because the column is nullable) have SQLAlchemy quietly NULL the FK out,
      orphaning the ledger rows and leaving the customer's balance overstated.
      The entries are therefore deleted explicitly, first.
    * There is no ``Customer.balance`` column. A customer's outstanding balance
      is *derived* by summing their ledger entries (see
      ``CustomerService.outstanding``), so deleting this invoice's entries IS
      the balance subtraction — there is nothing to decrement by hand, and the
      two can never drift apart.

    Invoice items are removed by the ``all, delete-orphan`` cascade on
    ``Invoice.items`` (and ``ON DELETE CASCADE`` in the database).

    The caller owns the transaction; see ``SalesService.delete_invoice``.
    """
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError("Invoice not found")

    number = invoice.invoice_number
    customer_id = invoice.customer_id
    customer_name = invoice.customer_name

    entries = list(invoice.ledger_entries)
    # Net contribution of this invoice to the customer's outstanding balance:
    # finalize_sale posts +grand_total (credit_sale) and -paid_amount (payment),
    # which nets to remaining_amount; a previously-voided invoice also has the
    # offsetting *_void entries here, so this correctly reports 0.00 for one.
    balance_reduction = sum((Decimal(e.amount) for e in entries), Decimal("0.00"))

    for entry in entries:
        session.delete(entry)
    session.delete(invoice)
    session.flush()

    return InvoiceDeletion(number, customer_id, customer_name, balance_reduction)


def delete_udhar_entry(session, ledger_id: int) -> tuple[int, Decimal, int | None]:
    """Delete a single customer Udhaar (khata) ledger entry.

    Returns ``(customer_id, amount, invoice_id)``. ``amount`` is the signed
    figure that was removed; because the outstanding balance is the sum of a
    customer's ledger entries, deleting this row subtracts that amount from the
    balance automatically. ``invoice_id`` is non-None when the entry was posted
    by a sale — the UI warns about that case, since removing only the ledger
    row leaves the invoice itself behind with a remaining amount that no longer
    shows up in the customer's khata.

    The caller owns the transaction; see ``CustomerService.delete_udhar_entry``.
    """
    entry = session.get(CustomerLedgerEntry, ledger_id)
    if entry is None:
        raise ValueError("Udhaar entry not found")

    customer_id = entry.customer_id
    amount = Decimal(entry.amount)
    invoice_id = entry.invoice_id

    session.delete(entry)
    session.flush()

    return customer_id, amount, invoice_id


class SalesService:
    def __init__(self, db, product_repository=None, invoice_repository=None, invoice_item_repository=None, customer_repository=None, ledger_repository=None, settings_service=None):
        self.db = db
        self.products = product_repository or ProductRepository()
        self.invoices = invoice_repository or InvoiceRepository()
        self.invoice_items = invoice_item_repository or InvoiceItemRepository()
        self.customers = customer_repository or CustomerRepository()
        self.ledger = ledger_repository or CustomerLedgerRepository()
        self.settings = settings_service

    def _resolve_credit_customer(self, session, customer_id, customer_name):
        clean = str(customer_name or "").strip()
        if customer_id is not None:
            customer = self.customers.get(session, customer_id)
            if customer is None or not customer.is_active:
                raise ValueError("Customer is unavailable")
            if not clean:
                clean = customer.name
            return customer
        if not clean:
            raise ValueError("Customer name is required for Credit sales.")
        if len(clean) > 200:
            raise ValueError("Customer name cannot exceed 200 characters")
        from sqlalchemy import select
        customer = session.scalar(select(Customer).where(Customer.name.ilike(clean)).limit(1))
        if customer is None:
            customer = self.customers.add(session, Customer(name=clean))
            session.flush()
        elif not customer.is_active:
            raise ValueError("Customer is unavailable")
        return customer

    def finalize_sale(self, cart: Cart, payment_type: str, paid_amount, customer_id=None, customer_name=None):
        if not cart.lines: raise ValueError("Cart is empty")
        if payment_type not in {"cash", "credit"}: raise ValueError("Unsupported payment method")
        paid = _money(paid_amount, "Paid amount")
        if paid < 0: raise ValueError("Payment cannot be negative")
        runtime_settings = self.settings.get_settings() if self.settings else None
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            from sqlalchemy.orm import Session
            session = Session(bind=conn, autoflush=False, expire_on_commit=False)
            try:
                gross_subtotal = Decimal("0.00")
                invoice_items = []
                for line in cart.lines.values():
                    product = self.products.get(session, line.product_id)
                    if product is None: raise ValueError("A selected product is unavailable")
                    q = _quantity(line.quantity, product.unit)
                    # Use the price the cart line was actually built with (set by
                    # Cart.add from product.selling_price, by a wholesale/retail
                    # toggle upstream, or restored verbatim by Edit Invoice) rather
                    # than re-deriving it from today's live product.selling_price.
                    # Re-deriving here would silently override a wholesale sale
                    # back to retail at the moment of finalizing — the invoice
                    # must charge what the cashier saw on screen.
                    price = _money(line.unit_price, "Unit price")
                    item_discount = _money(line.item_discount, "Item discount")
                    gross = (q * price).quantize(Decimal("0.01"))
                    if item_discount > gross: raise ValueError(f"Discount cannot exceed the total for {product.name}")
                    total = gross - item_discount
                    gross_subtotal += gross
                    invoice_items.append((product, q, price, item_discount, total))
                bill_discount = _money(cart.bill_discount, "Bill discount")
                item_discount_total = sum((x[3] for x in invoice_items), Decimal("0.00"))
                if bill_discount > gross_subtotal - item_discount_total:
                    raise ValueError("Bill discount cannot exceed the subtotal after item discounts")
                discount_total = item_discount_total + bill_discount
                grand = gross_subtotal - discount_total
                if grand < 0: raise ValueError("Final total cannot be negative")
                if payment_type == "cash" and paid != grand: raise ValueError("Cash sale requires full payment")
                if payment_type == "credit" and paid > grand: raise ValueError("Payment cannot exceed the credit sale total")

                customer = None
                clean_customer_name = str(customer_name or "").strip()
                if len(clean_customer_name) > 200:
                    raise ValueError("Customer name cannot exceed 200 characters")
                if payment_type == "credit":
                    customer = self._resolve_credit_customer(session, customer_id, clean_customer_name)
                    customer_id = customer.id
                    customer_name = customer.name
                else:
                    # A cash customer name is historical invoice metadata only.
                    # It must never create a customer ledger balance.
                    customer_id = None
                    customer_name = clean_customer_name or None

                remaining = grand - paid
                prefix = runtime_settings.invoice_prefix if runtime_settings else "INV"
                invoice = self._insert_invoice(
                    session, gross_subtotal, discount_total, grand, payment_type, paid, remaining,
                    customer_id, prefix, customer_name
                )
                number = invoice.invoice_number
                for product, q, price, discount, total in invoice_items:
                    self.invoice_items.add(session, InvoiceItem(invoice_id=invoice.id, product_id=product.id,
                        product_name=product.name, unit=product.unit, quantity=q, unit_price=price,
                        item_discount=discount, line_total=total))
                if payment_type == "credit":
                    self.ledger.add(session, CustomerLedgerEntry(customer_id=customer_id, amount=grand,
                        transaction_type="credit_sale", invoice_id=invoice.id, reference=number))
                    if paid > 0:
                        self.ledger.add(session, CustomerLedgerEntry(customer_id=customer_id, amount=-paid,
                            transaction_type="payment", invoice_id=invoice.id, reference=number))
                session.flush(); conn.commit()
                return invoice.id, invoice.invoice_number
            except Exception:
                session.rollback(); conn.rollback(); raise
            finally:
                session.close()

    def _insert_invoice(self, session, subtotal, discount_total, grand, payment_type, paid, remaining, customer_id, prefix, customer_name=None):
        # Clean sequential invoice numbers: rather than allocating a separate
        # date-stamped counter up front (the old "INV-20260908-0001" scheme,
        # which reset its padded tail every day and needed retry-on-collision
        # handling), we let the database assign the invoice its normal
        # autoincrementing primary key first, then stamp invoice_number from
        # that id. This guarantees a clean, gapless, always-unique sequence
        # (Invoice #1, #2, #3...) with no possibility of a collision, so the
        # old IntegrityError-retry loop is no longer needed at all.
        invoice = Invoice(invoice_number="", subtotal=subtotal, discount_total=discount_total, grand_total=grand,
                          payment_type=payment_type, paid_amount=paid, remaining_amount=remaining,
                          customer_id=customer_id, customer_name=customer_name)
        self.invoices.add(session, invoice)
        session.flush()  # assigns invoice.id
        invoice.invoice_number = format_invoice_number(invoice.id, prefix)
        session.flush()
        return invoice

    def list_invoices(self, search="", payment_type=None):
        with self.db.SessionLocal() as s:
            return self.invoices.list(s, search, payment_type=payment_type)

    def void_invoice(self, invoice_id: int) -> None:
        """Void an invoice for the Edit Invoice flow.

        The invoice and its items are never deleted (full audit history is
        kept); a credit sale's ledger impact is reversed with new offsetting
        entries rather than mutating or removing the original ones, since
        CustomerLedgerEntry.invoice_id is ON DELETE RESTRICT by design.
        """
        from datetime import datetime
        with self.db.SessionLocal.begin() as session:
            invoice = self.invoices.get(session, invoice_id)
            if invoice is None:
                raise ValueError("Invoice not found")
            if invoice.voided:
                raise ValueError("Invoice is already voided")
            if invoice.payment_type == "credit" and invoice.customer_id is not None:
                # Reverse exactly what finalize_sale posted: a credit_sale of
                # +grand_total, and if any amount was paid at sale time, a
                # payment of -paid_amount. Offsetting entries (rather than
                # deleting the originals) preserve the ledger's audit trail.
                self.ledger.add(session, CustomerLedgerEntry(
                    customer_id=invoice.customer_id, amount=-invoice.grand_total,
                    transaction_type="credit_sale_void", invoice_id=invoice.id,
                    reference=invoice.invoice_number, note="Reversed for Edit Invoice"))
                if invoice.paid_amount and invoice.paid_amount > 0:
                    self.ledger.add(session, CustomerLedgerEntry(
                        customer_id=invoice.customer_id, amount=invoice.paid_amount,
                        transaction_type="payment_void", invoice_id=invoice.id,
                        reference=invoice.invoice_number, note="Reversed for Edit Invoice"))
            invoice.voided = True
            invoice.voided_at = datetime.now()
            session.flush()

    def get_invoice(self, invoice_id):
        with self.db.SessionLocal() as s: return self.invoices.get(s, invoice_id)

    def receipt_context(self, invoice_id):
        """Real customer-ledger (khaata) figures for printing a sale receipt.

        Everything is read and reduced to plain Decimals/strings inside one
        open session, so the returned dict is safe to attach to a detached
        invoice at print time — nothing here is lazy-loaded after the session
        closes. Returns ``None`` if the invoice no longer exists.

        No number is invented. For a credit sale the customer's *current*
        outstanding balance is summed live from the ledger exactly as
        ``CustomerService.outstanding`` does, and the previous balance is
        derived as ``outstanding - this invoice's own ledger contribution``.
        finalize_sale posts that contribution as +grand_total / -paid_amount,
        which nets to ``remaining_amount`` and never changes afterwards, so:

            previous_balance + remaining_amount == total_outstanding

        holds at sale time (where previous_balance is the true pre-sale
        balance) and stays internally consistent on any later reprint. Cash
        sales create no ledger rows, so their balance fields are ``None``.
        """
        with self.db.SessionLocal() as s:
            invoice = self.invoices.get(s, invoice_id)
            if invoice is None:
                return None
            payment_type = invoice.payment_type
            customer_id = invoice.customer_id
            grand = Decimal(invoice.grand_total)
            paid = Decimal(invoice.paid_amount)
            remaining = Decimal(invoice.remaining_amount)
            customer_name = invoice.customer_name
            is_credit = payment_type == "credit" and customer_id is not None
            total_outstanding = None
            previous_balance = None
            if is_credit:
                entries = self.ledger.list_for_customer(s, customer_id)
                total_outstanding = sum((Decimal(e.amount) for e in entries), Decimal("0.00"))
                previous_balance = total_outstanding - remaining
        return {
            "is_credit": is_credit,
            "payment_type": payment_type,
            "customer_name": customer_name,
            "grand_total": grand,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "previous_balance": previous_balance,
            "total_outstanding": total_outstanding,
        }

    def delete_invoice(self, invoice_id: int) -> InvoiceDeletion:
        """Permanently delete an invoice (transaction wrapper).

        This is a hard delete and cannot be undone — unlike void_invoice, which
        keeps the row for audit history. Both are offered deliberately: Edit
        Invoice voids, while an outright mistake can be erased.

        No stock/inventory logic: nothing is returned to any product.
        """
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            from sqlalchemy.orm import Session
            session = Session(bind=conn, autoflush=False, expire_on_commit=False)
            try:
                # Resolves to the module-level delete_invoice() above, which does
                # the work against this session; this method owns the transaction.
                result = delete_invoice(session, invoice_id)
                session.flush()
                conn.commit()
                return result
            except Exception:
                session.rollback()
                conn.rollback()
                raise
            finally:
                session.close()

    # --- Customer Udhaar drill-down (Level 2 / Level 3 of the Customer page) ---

    def list_invoices_for_customer(self, customer_id):
        """Invoices tied to one customer, newest first, for the Customer Udhaar
        drill-down (Level 2: Invoice #, Date, Total, Paid, Remaining).

        Only invoices actually linked to this customer record are relevant here
        (a cash sale's customer_name is metadata only and never sets
        customer_id -- see _resolve_credit_customer / finalize_sale). Voided
        invoices are excluded: they no longer contribute to the customer's
        outstanding balance (void_invoice posts offsetting ledger entries) and
        showing them here as if still owed would be misleading; the full
        record still exists in Sales History / the void audit trail.

        Every field is reduced to a plain dict inside the open session so the
        result is safe to use after the session closes, matching the pattern
        used by receipt_context() below.
        """
        from sqlalchemy import select
        with self.db.SessionLocal() as s:
            rows = s.execute(
                select(Invoice)
                .where(Invoice.customer_id == customer_id, Invoice.voided == False)  # noqa: E712
                .order_by(Invoice.created_at.desc())
            ).scalars().all()
            return [
                {
                    "id": inv.id,
                    "invoice_number": inv.invoice_number,
                    "created_at": inv.created_at,
                    "grand_total": Decimal(inv.grand_total),
                    "paid_amount": Decimal(inv.paid_amount),
                    "remaining_amount": Decimal(inv.remaining_amount),
                }
                for inv in rows
            ]

    def invoice_detail(self, invoice_id):
        """Full item-level breakdown of one invoice for Level 3 (item
        description/qty/price/total plus the summary footer). Returns None if
        the invoice no longer exists. Everything -- including invoice.items,
        which is lazy by default -- is read out to plain values inside this
        one open session, so the result never depends on a detached ORM
        relationship being accessible later.
        """
        with self.db.SessionLocal() as s:
            invoice = self.invoices.get(s, invoice_id)
            if invoice is None:
                return None
            items = [
                {
                    "product_name": it.product_name,
                    "quantity": Decimal(it.quantity),
                    "unit_price": Decimal(it.unit_price),
                    "item_discount": Decimal(it.item_discount),
                    "line_total": Decimal(it.line_total),
                }
                for it in invoice.items
            ]
            item_discount_total = sum((i["item_discount"] for i in items), Decimal("0.00"))
            discount_total = Decimal(invoice.discount_total)
            # Bill-level discount is whatever of discount_total isn't
            # accounted for by the sum of item discounts -- the same
            # derivation InvoiceOutputService._bill_discount uses for the
            # printed receipt, kept consistent here for the on-screen view.
            bill_discount = discount_total - item_discount_total
            subtotal = sum(
                ((i["quantity"] * i["unit_price"]).quantize(Decimal("0.01")) for i in items),
                Decimal("0.00"),
            )
            return {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "created_at": invoice.created_at,
                "customer_name": invoice.customer_name,
                "items": items,
                "subtotal": subtotal,
                "item_discount_total": item_discount_total,
                "bill_discount": bill_discount,
                "discount_total": discount_total,
                "grand_total": Decimal(invoice.grand_total),
                "paid_amount": Decimal(invoice.paid_amount),
                "remaining_amount": Decimal(invoice.remaining_amount),
            }
