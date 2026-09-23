from datetime import datetime, timedelta
from decimal import Decimal

from app.database.connection import DatabaseManager
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.database.models.invoice import Invoice
from app.services.customer_service import CustomerService
from app.services.dashboard_service import DashboardService
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService
from app.services.settings_service import SettingsService


def db(tmp_path):
    d = DatabaseManager(tmp_path / "pos.db")
    d.initialize()
    return d


def sale(d, *, name="Wire", price="10.00", quantity="1", payment="cash", paid=None, customer_id=None, customer_name=None):
    p = ProductService(d).create_product(
        name=name, selling_price=price, unit="meter", stock="100", minimum_stock="1"
    )
    cart = Cart()
    cart.add(p, quantity)
    paid = paid if paid is not None else (Decimal(price) * Decimal(quantity)).quantize(Decimal("0.01"))
    return SalesService(d, settings_service=SettingsService(d)).finalize_sale(
        cart, payment, paid, customer_id, customer_name
    )


def set_created_at(d, invoice_id, created_at):
    with d.SessionLocal.begin() as session:
        invoice = session.get(Invoice, invoice_id)
        invoice.created_at = created_at


def test_dashboard_today_sales_total(tmp_path):
    d = db(tmp_path)
    ids = [sale(d, price="10", quantity="2", paid="20")[0], sale(d, price="5", quantity="3", paid="15")[0]]
    for invoice_id in ids:
        set_created_at(d, invoice_id, datetime(2026, 8, 17, 10, 0, 0))
    summary = DashboardService(d, now_provider=lambda: datetime(2026, 8, 17, 12, 0, 0)).summary()
    assert summary.today_sales == Decimal("35.00")


def test_dashboard_today_invoice_count(tmp_path):
    d = db(tmp_path)
    ids = [sale(d, paid="10")[0], sale(d, paid="10")[0]]
    for invoice_id in ids:
        set_created_at(d, invoice_id, datetime(2026, 8, 17, 10, 0, 0))
    assert DashboardService(d, now_provider=lambda: datetime(2026, 8, 17, 12, 0, 0)).summary().today_invoices == 2


def test_dashboard_today_cash_received(tmp_path):
    d = db(tmp_path)
    ids = [sale(d, price="10", quantity="2", paid="20")[0]]
    customer = CustomerService(d).create("Ali")
    ids.append(sale(d, price="10", quantity="3", payment="credit", paid="5", customer_id=customer.id)[0])
    for invoice_id in ids:
        set_created_at(d, invoice_id, datetime(2026, 8, 17, 10, 0, 0))
    summary = DashboardService(d, now_provider=lambda: datetime(2026, 8, 17, 12, 0, 0)).summary()
    assert summary.today_cash_received == Decimal("20.00")


def test_dashboard_today_credit_sales_uses_credit_invoice_total(tmp_path):
    d = db(tmp_path)
    customer = CustomerService(d).create("Ali")
    invoice_id = sale(d, price="10", quantity="3", payment="credit", paid="5", customer_id=customer.id)[0]
    set_created_at(d, invoice_id, datetime(2026, 8, 17, 10, 0, 0))
    summary = DashboardService(d, now_provider=lambda: datetime(2026, 8, 17, 12, 0, 0)).summary()
    assert summary.today_credit_sales == Decimal("30.00")


def test_dashboard_total_outstanding_udhaar(tmp_path):
    d = db(tmp_path)
    customer = CustomerService(d).create("Ali")
    sale(d, price="10", quantity="10", payment="credit", paid="25", customer_id=customer.id)
    assert DashboardService(d).summary().total_outstanding == Decimal("75.00")


def test_yesterday_invoice_excluded_from_today(tmp_path):
    d = db(tmp_path)
    invoice_id, _ = sale(d, price="100", paid="100")
    today = datetime(2026, 8, 17, 10, 0, 0)
    set_created_at(d, invoice_id, today - timedelta(days=1))
    summary = DashboardService(d, now_provider=lambda: today).summary()
    assert summary.today_sales == Decimal("0.00")
    assert summary.today_invoices == 0
    assert summary.today_cash_received == Decimal("0.00")


def test_empty_dashboard(tmp_path):
    summary = DashboardService(db(tmp_path), now_provider=lambda: datetime(2026, 8, 17, 12, 0, 0)).summary()
    assert summary.today_sales == Decimal("0.00")
    assert summary.today_invoices == 0
    assert summary.today_cash_received == Decimal("0.00")
    assert summary.today_credit_sales == Decimal("0.00")
    assert summary.total_outstanding == Decimal("0.00")


def test_recent_sales_ordering_and_limit(tmp_path):
    d = db(tmp_path)
    ids = []
    for _ in range(12):
        ids.append(sale(d, paid="10")[0])
    with d.SessionLocal.begin() as session:
        for index, invoice_id in enumerate(ids):
            session.get(Invoice, invoice_id).created_at = datetime(2026, 8, 17, 10, 0, 0) + timedelta(minutes=index)
    recent = DashboardService(d).recent_sales(10)
    assert len(recent) == 10
    assert recent[0].invoice_id == ids[-1]
    assert recent[-1].invoice_id == ids[-10]


def test_recent_sales_walk_in_display(tmp_path):
    d = db(tmp_path)
    invoice_id, _ = sale(d, paid="10")
    recent = DashboardService(d).recent_sales()
    assert recent[0].invoice_id == invoice_id
    assert recent[0].customer_name == "Walk-in"


def test_recent_sales_optional_customer_name_display(tmp_path):
    d = db(tmp_path)
    invoice_id, _ = sale(d, paid="10", customer_name="Walk-in Ahmed")
    recent = DashboardService(d).recent_sales()
    assert recent[0].invoice_id == invoice_id
    assert recent[0].customer_name == "Walk-in Ahmed"


def test_low_stock_products(tmp_path):
    d = db(tmp_path)
    ps = ProductService(d)
    ps.create_product(name="Low", selling_price="10", unit="piece", stock="2", minimum_stock="5")
    low = DashboardService(d).low_stock_products()
    assert [(x.name, x.status) for x in low] == [("Low", "Low stock")]


def test_out_of_stock_products(tmp_path):
    d = db(tmp_path)
    ps = ProductService(d)
    ps.create_product(name="Empty", selling_price="10", unit="piece", stock="0", minimum_stock="5")
    low = DashboardService(d).low_stock_products()
    assert low[0].status == "Out of stock"


def test_decimal_meter_stock_is_preserved_for_dashboard(tmp_path):
    d = db(tmp_path)
    ps = ProductService(d)
    ps.create_product(name="Cable", selling_price="10", unit="meter", stock="5.250000", minimum_stock="6")
    low = DashboardService(d).low_stock_products()
    assert low[0].current_stock == Decimal("5.250000")
    assert low[0].minimum_stock == Decimal("6.000000")


def test_discrete_product_whole_number_stock(tmp_path):
    d = db(tmp_path)
    ps = ProductService(d)
    ps.create_product(name="Switch", selling_price="10", unit="piece", stock="5", minimum_stock="6")
    low = DashboardService(d).low_stock_products()
    assert low[0].current_stock == Decimal("5.000000")


def test_dashboard_refresh_reads_new_sale_data(tmp_path):
    d = db(tmp_path)
    service = DashboardService(d)
    before = service.summary()
    sale(d, price="25", paid="25")
    after = service.summary()
    assert before.today_sales == Decimal("0.00")
    assert after.today_sales == Decimal("25.00")
    assert after.today_invoices == 1


def test_dashboard_queries_do_not_modify_records(tmp_path):
    d = db(tmp_path)
    invoice_id, _ = sale(d, price="25", paid="25")
    before = ProductService(d).get_product(1)
    with d.SessionLocal() as session:
        invoice_before = session.get(Invoice, invoice_id)
        values_before = (invoice_before.grand_total, invoice_before.paid_amount, invoice_before.remaining_amount, invoice_before.customer_name)
    service = DashboardService(d)
    service.summary(); service.recent_sales(); service.low_stock_products()
    with d.SessionLocal() as session:
        invoice_after = session.get(Invoice, invoice_id)
        values_after = (invoice_after.grand_total, invoice_after.paid_amount, invoice_after.remaining_amount, invoice_after.customer_name)
    product_after = ProductService(d).get_product(1)
    assert values_after == values_before
    assert product_after.current_stock == before.current_stock


def test_dashboard_counts_only_active_products(tmp_path):
    d = db(tmp_path)
    ps = ProductService(d)
    active = ps.create_product(name="Active", selling_price="10", unit="piece", stock="5", minimum_stock="1")
    inactive = ps.create_product(name="Inactive", selling_price="20", unit="piece", stock="5", minimum_stock="1")
    ps.set_product_active(inactive.id, False)
    summary = DashboardService(d).summary()
    assert summary.total_products == 1

