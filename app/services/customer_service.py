from decimal import Decimal
from app.database.models.customer import Customer
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.repositories.customer_repository import CustomerRepository
from app.repositories.customer_ledger_repository import CustomerLedgerRepository
from app.services.product_service import validate_money
class CustomerService:
    def __init__(self,db,customer_repository=None,ledger_repository=None):
        self.db=db; self.customers=customer_repository or CustomerRepository(); self.ledger=ledger_repository or CustomerLedgerRepository()
    def create(self,name):
        name=name.strip()
        if not name: raise ValueError("Customer name is required")
        if len(name)>200: raise ValueError("Customer name must be 200 characters or fewer")
        with self.db.SessionLocal.begin() as s:return self.customers.add(s,Customer(name=name))
    def update(self,customer_id,name):
        name=name.strip()
        if not name: raise ValueError("Customer name is required")
        with self.db.SessionLocal.begin() as s:
            c=self.customers.get(s,customer_id)
            if not c: raise ValueError("Customer not found")
            c.name=name; return c
    def delete_customer(self, customer_id):
        """Soft-delete: flips is_active off rather than removing the row.

        CustomerLedgerEntry.customer_id and Invoice.customer_id are both
        ON DELETE RESTRICT, so a real SQL DELETE would raise for almost any
        customer who has ever had a single udhaar transaction — which is
        the normal case for this table, not an edge case. is_active already
        exists for exactly this: CustomerRepository.search's default
        active_only=True means a deleted customer simply stops appearing.
        The outstanding-balance guard is re-checked here even though the UI
        already checks it, so this can never be bypassed by another caller.
        """
        with self.db.SessionLocal.begin() as s:
            c=self.customers.get(s,customer_id)
            if c is None or not c.is_active: raise ValueError("Customer not found")
            entries=self.ledger.list_for_customer(s,customer_id)
            outstanding=sum((Decimal(e.amount) for e in entries),Decimal("0.00"))
            if outstanding != 0:
                raise ValueError("Cannot delete a customer with an outstanding balance")
            c.is_active=False
    def list(self,search=""): 
        with self.db.SessionLocal() as s:return self.customers.search(s,search)
    def get(self,customer_id):
        with self.db.SessionLocal() as s:return self.customers.get(s,customer_id)
    def outstanding(self,customer_id):
        with self.db.SessionLocal() as s:
            entries=self.ledger.list_for_customer(s,customer_id)
            return sum((Decimal(e.amount) for e in entries),Decimal("0.00"))
    def history(self,customer_id):
        with self.db.SessionLocal() as s:return self.ledger.list_for_customer(s,customer_id)

    @staticmethod
    def filter_history(entries, query="", customer_name=""):
        """Filter an already-loaded customer ledger without changing database state."""
        q = str(query or "").strip().lower()
        customer = str(customer_name or "").strip().lower()
        if not q:
            return list(entries)
        matches = []
        for entry in entries:
            dt = entry.created_at
            searchable = " ".join((
                dt.strftime("%d %B %Y, %I:%M %p"),
                dt.strftime("%d %B"),
                dt.strftime("%d %b"),
                dt.strftime("%d/%m/%Y"),
                dt.strftime("%Y-%m-%d"),
                dt.strftime("%I:%M %p"),
                dt.strftime("%H:%M"),
                dt.strftime("%B"),
                dt.strftime("%b"),
                entry.transaction_type.replace("_", " "),
                f"{entry.amount:.2f}",
                entry.reference or "",
                entry.note or "",
                customer,
            )).lower()
            if q in searchable:
                matches.append(entry)
        return matches

    def adjust_balance(self, customer_id, amount, note=None):
        """Apply a signed outstanding-balance adjustment through the ledger."""
        adjustment = validate_money(amount, "Adjustment")
        if adjustment == 0:
            raise ValueError("Adjustment cannot be zero")
        clean_note = str(note or "").strip() or None
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            from sqlalchemy.orm import Session
            s=Session(bind=conn,autoflush=False,expire_on_commit=False)
            try:
                c=self.customers.get(s,customer_id)
                if c is None or not c.is_active: raise ValueError("Customer not found")
                entries=self.ledger.list_for_customer(s,customer_id)
                outstanding=sum((Decimal(e.amount) for e in entries),Decimal("0.00"))
                new_balance=outstanding+adjustment
                if new_balance < 0:
                    raise ValueError("Adjustment cannot reduce outstanding below zero")
                s.add(CustomerLedgerEntry(customer_id=customer_id,amount=adjustment,transaction_type="balance_adjustment",note=clean_note))
                s.flush(); conn.commit(); return new_balance
            except Exception:
                s.rollback(); conn.rollback(); raise
            finally: s.close()

    def record_payment(self,customer_id,amount,note=None):
        amount=validate_money(amount,"Payment")
        if amount<=0: raise ValueError("Payment must be greater than zero")
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            from sqlalchemy.orm import Session
            s=Session(bind=conn,autoflush=False,expire_on_commit=False)
            try:
                c=self.customers.get(s,customer_id)
                if c is None or not c.is_active: raise ValueError("Customer not found")
                entries=self.ledger.list_for_customer(s,customer_id)
                outstanding=sum((Decimal(e.amount) for e in entries),Decimal("0.00"))
                if amount>outstanding: raise ValueError("Payment cannot exceed the outstanding balance")
                s.add(CustomerLedgerEntry(customer_id=customer_id,amount=-amount,transaction_type="payment",note=note))
                s.flush(); conn.commit(); return outstanding-amount
            except Exception:
                s.rollback(); conn.rollback(); raise
            finally:s.close()
