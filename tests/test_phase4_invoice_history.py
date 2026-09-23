from decimal import Decimal
from app.database.connection import DatabaseManager
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService


def db(tmp_path):
    d = DatabaseManager(tmp_path / "pos.db")
    d.initialize()
    return d


def make_sale(d, name="Wire", price="10.00", quantity="5.5", paid="20", customer_id=None, customer_name=None):
    p = ProductService(d).create_product(
        name=name, selling_price=price, unit="meter", stock="100", minimum_stock="1"
    )
    s = SalesService(d)
    cart = Cart()
    cart.add(p, quantity)
    return d, s, p, s.finalize_sale(cart, "credit" if customer_id else "cash", paid, customer_id, customer_name)


def test_invoice_history_listing_contains_required_fields(tmp_path):
    d, s, _, result = make_sale(db(tmp_path), paid="55")
    invoice = s.list_invoices()[0]
    assert invoice.invoice_number == result[1]
    assert invoice.payment_type == "cash"
    assert invoice.grand_total == Decimal("55.00")
    assert invoice.paid_amount == Decimal("55.00")
    assert invoice.remaining_amount == Decimal("0.00")


def test_invoice_history_searches_invoice_number(tmp_path):
    d, s, _, result = make_sale(db(tmp_path), paid="55")
    assert [x.invoice_number for x in s.list_invoices(result[1])] == [result[1]]


def test_invoice_history_searches_customer_name(tmp_path):
    from app.services.customer_service import CustomerService
    d = db(tmp_path)
    customer = CustomerService(d).create("Ali Khan")
    _, s, _, result = make_sale(d, quantity="1", price="10", paid="0", customer_id=customer.id)
    assert [x.invoice_number for x in s.list_invoices("Ali Khan")] == [result[1]]


def test_open_invoice_retrieves_items(tmp_path):
    _, s, _, result = make_sale(db(tmp_path), quantity="5.5", paid="55")
    invoice = s.get_invoice(result[0])
    assert len(invoice.items) == 1
    assert invoice.items[0].product_name == "Wire"


def test_historical_product_name_is_preserved(tmp_path):
    d, s, p, result = make_sale(db(tmp_path), name="Old Wire", paid="10", quantity="1")
    ProductService(d).update_product(p.id, name="New Wire", selling_price="20", unit="meter", minimum_stock="1")
    assert s.get_invoice(result[0]).items[0].product_name == "Old Wire"


def test_historical_selling_price_is_preserved(tmp_path):
    d, s, p, result = make_sale(db(tmp_path), name="Wire", price="10", paid="10", quantity="1")
    ProductService(d).update_product(p.id, name="Wire", selling_price="20", unit="meter", minimum_stock="1")
    assert s.get_invoice(result[0]).items[0].unit_price == Decimal("10.00")


def test_optional_customer_name_is_preserved_without_customer_record(tmp_path):
    d, s, _, result = make_sale(db(tmp_path), quantity="1", price="10", paid="10", customer_name="Walk-in Ahmed")
    invoice = s.get_invoice(result[0])
    assert invoice.customer_name == "Walk-in Ahmed"
    assert invoice.customer_id is None
    from app.database.models.customer import Customer
    with d.SessionLocal() as session:
        assert session.query(Customer).count() == 0


def test_walk_in_invoice_works(tmp_path):
    _, s, _, result = make_sale(db(tmp_path), quantity="1", price="10", paid="10")
    invoice = s.get_invoice(result[0])
    assert invoice.customer_id is None
    assert invoice.customer_name is None


def test_paid_and_remaining_are_correct(tmp_path):
    from app.services.customer_service import CustomerService
    d = db(tmp_path)
    customer = CustomerService(d).create("Credit Customer")
    _, s, _, result = make_sale(d, quantity="10", price="10", paid="25", customer_id=customer.id)
    invoice = s.get_invoice(result[0])
    assert invoice.grand_total == Decimal("100.00")
    assert invoice.paid_amount == Decimal("25.00")
    assert invoice.remaining_amount == Decimal("75.00")


def test_decimal_quantity_storage_is_unchanged(tmp_path):
    _, s, _, result = make_sale(db(tmp_path), quantity="5.250000", price="10", paid="52.50")
    item = s.get_invoice(result[0]).items[0]
    assert item.quantity == Decimal("5.250000")
    assert item.quantity != Decimal("5.25") or item.quantity == Decimal("5.250000")


def test_invoice_date_search_supports_human_readable_date(tmp_path):
    from datetime import datetime
    d, s, _, result = make_sale(db(tmp_path), quantity="1", price="10", paid="10")
    with d.SessionLocal.begin() as session:
        invoice = session.get(__import__('app.database.models.invoice', fromlist=['Invoice']).Invoice, result[0])
        invoice.created_at = datetime(2026, 2, 21, 10, 35, 42)
    assert len(s.list_invoices("21 February")) == 1
    assert len(s.list_invoices("21 Feb")) == 1
    assert len(s.list_invoices("21/02/2026")) == 1
    assert len(s.list_invoices("2026-02-21")) == 1

def test_invoice_date_filter_respects_payment_tabs(tmp_path):
    from datetime import datetime
    d=db(tmp_path); p=ProductService(d).create_product(name="Wire",selling_price="10",unit="meter")
    s=SalesService(d,settings_service=__import__('app.services.settings_service',fromlist=['SettingsService']).SettingsService(d))
    c=Cart(); c.add(p,"1"); cash,_=s.finalize_sale(c,"cash","10")
    c=Cart(); c.add(p,"2"); credit,_=s.finalize_sale(c,"credit","0",customer_name="Ahmed")
    with d.SessionLocal.begin() as session:
        session.get(__import__('app.database.models.invoice',fromlist=['Invoice']).Invoice,cash).created_at=datetime(2026,2,21,9,0)
        session.get(__import__('app.database.models.invoice',fromlist=['Invoice']).Invoice,credit).created_at=datetime(2026,2,21,10,0)
    assert len(s.list_invoices("21 February",payment_type="cash")) == 1
    assert len(s.list_invoices("21 February",payment_type="credit")) == 1


def test_product_purchase_and_retail_prices_are_distinct(tmp_path):
    d=db(tmp_path); p=ProductService(d).create_product(name="Socket",purchase_price="80",selling_price="120",unit="piece")
    assert p.purchase_price == Decimal("80.00")
    assert p.selling_price == Decimal("120.00")
    cart=Cart(); cart.add(p,"1")
    assert cart.lines[p.id].unit_price == Decimal("120.00")

def test_existing_product_nonzero_price_is_not_a_zero_placeholder(tmp_path):
    d=db(tmp_path); p=ProductService(d).create_product(name="Socket",purchase_price="80",selling_price="120",unit="piece")
    ProductService(d).update_product(p.id,name="Socket",purchase_price="85",selling_price="150",unit="piece")
    saved=ProductService(d).get_product(p.id)
    assert saved.purchase_price == Decimal("85.00")
    assert saved.selling_price == Decimal("150.00")
