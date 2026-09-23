from decimal import Decimal
import pytest
from app.database.connection import DatabaseManager
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.database.models.invoice import Invoice
from app.database.models.invoice_item import InvoiceItem
from app.services.customer_service import CustomerService
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService
from app.services.settings_service import SettingsService


def db(tmp_path):
    d = DatabaseManager(tmp_path / 'pos.db'); d.initialize(); return d


def product(d, name='Wire', price='10.00', stock='100'):
    return ProductService(d).create_product(name=name, selling_price=price, unit='meter', stock=stock, minimum_stock='5')


def test_delete_cash_invoice_removes_invoice_and_items(tmp_path):
    d = db(tmp_path); p = product(d)
    s = SalesService(d, settings_service=SettingsService(d))
    c = Cart(); c.add(p, '3')
    iid, num = s.finalize_sale(c, 'cash', '30')

    result = s.delete_invoice(iid)

    assert result.invoice_number == num
    assert result.customer_id is None
    assert result.balance_reduction == Decimal('0.00')
    with d.SessionLocal() as x:
        assert x.query(Invoice).count() == 0
        assert x.query(InvoiceItem).count() == 0
    # No stock tracking: the product's stock figure is untouched by the delete.
    assert ProductService(d).get_product(p.id).current_stock == Decimal('100.000000')


def test_delete_credit_invoice_reduces_outstanding_balance(tmp_path):
    d = db(tmp_path); p = product(d)
    cs = CustomerService(d); cust = cs.create('Ali')
    s = SalesService(d, settings_service=SettingsService(d))
    c = Cart(); c.add(p, '10')
    iid, _ = s.finalize_sale(c, 'credit', '40', cust.id)   # 100 total, 40 paid
    assert cs.outstanding(cust.id) == Decimal('60.00')

    result = s.delete_invoice(iid)

    assert result.customer_id == cust.id
    assert result.balance_reduction == Decimal('60.00')
    assert cs.outstanding(cust.id) == Decimal('0.00')
    with d.SessionLocal() as x:
        assert x.query(Invoice).count() == 0
        assert x.query(InvoiceItem).count() == 0
        # RESTRICT FK: the sale's ledger rows must be gone, not orphaned to NULL.
        assert x.query(CustomerLedgerEntry).count() == 0


def test_delete_voided_invoice_leaves_balance_unchanged(tmp_path):
    d = db(tmp_path); p = product(d)
    cs = CustomerService(d); cust = cs.create('Ali')
    s = SalesService(d, settings_service=SettingsService(d))
    c = Cart(); c.add(p, '10')
    iid, _ = s.finalize_sale(c, 'credit', '0', cust.id)
    s.void_invoice(iid)
    assert cs.outstanding(cust.id) == Decimal('0.00')

    result = s.delete_invoice(iid)

    # Already reversed by the void, so deleting removes nothing further.
    assert result.balance_reduction == Decimal('0.00')
    assert cs.outstanding(cust.id) == Decimal('0.00')
    with d.SessionLocal() as x:
        assert x.query(CustomerLedgerEntry).count() == 0


def test_delete_invoice_rejects_unknown_id_and_saves_nothing(tmp_path):
    d = db(tmp_path); p = product(d)
    s = SalesService(d, settings_service=SettingsService(d))
    c = Cart(); c.add(p, '1'); s.finalize_sale(c, 'cash', '10')
    with pytest.raises(ValueError):
        s.delete_invoice(9999)
    with d.SessionLocal() as x:
        assert x.query(Invoice).count() == 1


def test_delete_udhar_entry_subtracts_from_balance(tmp_path):
    d = db(tmp_path)
    cs = CustomerService(d); cust = cs.create('Ali')
    cs.adjust_balance(cust.id, '500', 'opening khata')
    cs.record_payment(cust.id, '200')
    assert cs.outstanding(cust.id) == Decimal('300.00')

    payment = [e for e in cs.history(cust.id) if e.transaction_type == 'payment'][0]
    customer_id, amount, invoice_id, new_balance = cs.delete_udhar_entry(payment.id)

    assert customer_id == cust.id
    assert amount == Decimal('-200.00')
    assert invoice_id is None
    assert new_balance == Decimal('500.00')
    assert cs.outstanding(cust.id) == Decimal('500.00')
    with d.SessionLocal() as x:
        assert x.query(CustomerLedgerEntry).count() == 1


def test_delete_udhar_entry_reports_source_invoice(tmp_path):
    d = db(tmp_path); p = product(d)
    cs = CustomerService(d); cust = cs.create('Ali')
    s = SalesService(d, settings_service=SettingsService(d))
    c = Cart(); c.add(p, '10')
    iid, _ = s.finalize_sale(c, 'credit', '0', cust.id)

    sale_entry = cs.history(cust.id)[0]
    _cid, amount, invoice_id, new_balance = cs.delete_udhar_entry(sale_entry.id)

    assert amount == Decimal('100.00')
    assert invoice_id == iid          # so the UI can warn about the orphaned invoice
    assert new_balance == Decimal('0.00')
    # The invoice itself is deliberately left alone by an udhaar-entry delete.
    with d.SessionLocal() as x:
        assert x.query(Invoice).count() == 1


def test_delete_udhar_entry_rejects_unknown_id(tmp_path):
    d = db(tmp_path)
    cs = CustomerService(d); cust = cs.create('Ali')
    cs.adjust_balance(cust.id, '100', 'opening')
    with pytest.raises(ValueError):
        cs.delete_udhar_entry(9999)
    assert cs.outstanding(cust.id) == Decimal('100.00')
