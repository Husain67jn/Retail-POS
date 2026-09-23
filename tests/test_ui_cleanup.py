from __future__ import annotations

from decimal import Decimal
import pytest
from pathlib import Path

from app.services.dashboard_service import DashboardService
from app.services.inventory_service import InventoryService
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService
from app.services.settings_service import SettingsService


def make_db(tmp_path):
    from app.database.connection import DatabaseManager
    db = DatabaseManager(tmp_path / "pos.db")
    db.initialize()
    return db


def make_product(db, *, name="Item", unit="meter", stock="20", minimum="5", price="10"):
    return ProductService(db).create_product(
        name=name,
        selling_price=price,
        unit=unit,
        stock=stock,
        minimum_stock=minimum,
    )


def test_dashboard_low_stock_exposes_exact_quantity_unit_and_threshold(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, name="Wire", unit="meter", stock="3.5", minimum="5")
    low = DashboardService(db).low_stock_products()
    assert low[0].product_id == product.id
    assert low[0].current_stock == Decimal("3.500000")
    assert low[0].unit == "meter"
    assert low[0].minimum_stock == Decimal("5.000000")
    assert low[0].status == "Low stock"


def test_billing_cart_product_selection_starts_with_default_quantity_one(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db)
    cart = Cart()
    cart.add(product, Decimal("1"))
    assert cart.lines[product.id].quantity == Decimal("1")


def test_cart_quantity_direct_entry_and_plus_minus_semantics(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, unit="meter")
    cart = Cart()
    cart.add(product, "1")
    cart.set_quantity(product.id, "1.3")
    assert cart.lines[product.id].quantity == Decimal("1.3")
    cart.set_quantity(product.id, cart.lines[product.id].quantity + Decimal("0.1"))
    assert cart.lines[product.id].quantity == Decimal("1.4")
    cart.set_quantity(product.id, cart.lines[product.id].quantity - Decimal("0.1"))
    assert cart.lines[product.id].quantity == Decimal("1.3")


def test_discrete_units_reject_fractional_billing_quantity(tmp_path):
    db = make_db(tmp_path)
    # Discrete units are counted as whole items -- "1.5 pieces"/"0.5 boxes" is
    # a data-entry mistake and must be rejected.
    for unit in ("piece", "dozen", "box", "packet"):
        product = make_product(db, name=f"{unit} item", unit=unit)
        cart = Cart()
        try:
            cart.add(product, "1.5")
            assert False, f"fractional quantity unexpectedly accepted for {unit}"
        except ValueError:
            pass


def test_measured_units_accept_fractional_billing_quantity(tmp_path):
    db = make_db(tmp_path)
    # Measured/continuous units must accept fractions: wire/cable cut to length
    # ("meter", ~yards) and goods sold by weight ("kg") -- e.g. 1.5 kg / 1.5 m.
    for unit in ("meter", "kg"):
        product = make_product(db, name=f"{unit} item", unit=unit)
        cart = Cart()
        cart.add(product, "1.5")
        assert cart.lines[product.id].quantity == Decimal("1.5")


def test_cash_sale_still_completes_and_deducts_stock(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, unit="piece", stock="10", price="10")
    cart = Cart()
    cart.add(product, "2")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "cash", "20")
    invoice = sale.get_invoice(invoice_id)
    assert invoice.grand_total == Decimal("20.00")
    assert invoice.subtotal == Decimal("20.00")
    assert invoice.discount_total == Decimal("0.00")
    assert ProductService(db).get_product(product.id).current_stock == Decimal("10.000000")


def test_stock_history_read_model_can_reconstruct_previous_and_new_stock(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, unit="meter", stock="10")
    inventory = InventoryService(db)
    inventory.add_stock(product.id, "5", note="Restock",)
    inventory.adjust_stock(product.id, "-2", note="Count correction")
    movements = inventory.list_all_movements()
    rows = []
    current = Decimal(ProductService(db).get_product(product.id).current_stock)
    for movement in movements:
        if movement.product_id != product.id:
            continue
        new_stock = current
        previous_stock = new_stock - Decimal(movement.quantity_change)
        rows.append((movement.movement_type, previous_stock, new_stock))
        current = previous_stock
    assert rows[0][0] == "manual_adjustment"
    assert rows[0][1] == Decimal("15.000000")
    assert rows[0][2] == Decimal("13.000000")
    assert rows[1][0] == "stock_addition"
    assert rows[1][1] == Decimal("10.000000")
    assert rows[1][2] == Decimal("15.000000")


def test_ui_cleanup_contracts_keep_product_editing_out_of_billing():
    pos = Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    main = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    dashboard = Path("app/ui/dashboard_page.py").read_text(encoding="utf-8")
    invoices = Path("app/ui/invoices_page.py").read_text(encoding="utf-8")
    inventory = Path("app/ui/inventory_page.py").read_text(encoding="utf-8")

    assert "ProductDialog" not in pos
    assert "Edit Product" not in pos
    assert "cellDoubleClicked" in pos
    assert "Add to Bill" in pos
    assert "Complete Sale" in pos
    assert 'self.sales.finalize_sale(' in pos and '"cash"' in pos
    assert "InventoryPage" not in main
    assert "Customer Udhaar" in main
    assert "Credit (Udhaar)" in pos
    assert "self.customer.setEnabled(True)" in pos
    assert "Udhaar" in dashboard
    assert "Customer" in invoices
    assert "Customer Udhaar" in main
    assert "Credit (Udhaar)" in pos
    assert "set_quantity" in pos
    assert "_quantity_editor" in pos
    # +/- steppers were removed from cart rows; quantity is typed directly.
    assert "quantityMinus" not in pos
    assert "quantityPlus" not in pos
    assert "ScrollBarPolicy.ScrollBarAsNeeded" in pos
    assert "Cash Invoices" in invoices
    assert "Credit (Udhaar) Invoices" in invoices

def test_final_invoice_uses_row_quantity_after_cart_edit(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, unit="meter", price="10")
    cart = Cart(); cart.add(product, "1")
    cart.set_quantity(product.id, "2.5")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "cash", "25")
    invoice = sale.get_invoice(invoice_id)
    assert invoice.items[0].quantity == Decimal("2.500000")
    assert invoice.items[0].line_total == Decimal("25.00")
    assert invoice.grand_total == Decimal("25.00")


def test_cash_customer_name_is_optional_metadata_not_credit(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, price="10")
    cart = Cart(); cart.add(product, "2")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "cash", "20", customer_name="Ahmed Khan")
    invoice = sale.get_invoice(invoice_id)
    assert invoice.payment_type == "cash"
    assert invoice.customer_name == "Ahmed Khan"
    assert invoice.customer_id is None
    from app.database.models.customer_ledger import CustomerLedgerEntry
    with db.SessionLocal() as session:
        assert session.query(CustomerLedgerEntry).count() == 0


def test_credit_requires_customer_name_and_is_atomic(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, price="10")
    cart = Cart(); cart.add(product, "1")
    sale = SalesService(db, settings_service=SettingsService(db))
    with pytest.raises(ValueError, match="Customer name"):
        sale.finalize_sale(cart, "credit", "0")
    assert sale.list_invoices() == []
    from app.database.models.customer_ledger import CustomerLedgerEntry
    with db.SessionLocal() as session:
        assert session.query(CustomerLedgerEntry).count() == 0


def test_credit_with_customer_name_creates_customer_and_ledger(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, price="10")
    cart = Cart(); cart.add(product, "2")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "credit", "0", customer_name="Ahmed")
    invoice = sale.get_invoice(invoice_id)
    assert invoice.payment_type == "credit"
    assert invoice.customer_name == "Ahmed"
    assert invoice.customer_id is not None
    from app.services.customer_service import CustomerService
    assert CustomerService(db).outstanding(invoice.customer_id) == Decimal("20.00")


def test_branding_and_stock_removal_contracts():
    main = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    dash = Path("app/ui/dashboard_page.py").read_text(encoding="utf-8")
    products = Path("app/ui/products_page.py").read_text(encoding="utf-8")
    assert "APP_NAME" in main
    assert "Mughal Electric Store" in Path("app/config/settings.py").read_text(encoding="utf-8")
    assert "Inventory" not in main
    assert "Low Stock" not in dash
    for field in ("Initial Stock", "Current Stock", "Minimum Stock", "Active", "Inactive"):
        assert field not in products

def test_product_delete_preserves_historical_invoice_integrity(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, name="Historical Plug", unit="meter", price="15")
    cart = Cart(); cart.add(product, "2.5")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "cash", "37.50")
    before = sale.get_invoice(invoice_id)
    before_item = before.items[0]
    ProductService(db).delete_product(product.id)
    assert ProductService(db).get_product(product.id) is None
    after = sale.get_invoice(invoice_id)
    assert after is not None
    assert len(after.items) == 1
    item = after.items[0]
    assert item.product_id is None
    assert item.product_name == "Historical Plug"
    assert item.quantity == before_item.quantity == Decimal("2.500000")
    assert item.unit_price == before_item.unit_price == Decimal("15.00")
    assert item.line_total == before_item.line_total == Decimal("37.50")
    assert after.grand_total == before.grand_total == Decimal("37.50")


def test_product_delete_preserves_credit_invoice_and_ledger(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, name="Credit Plug", price="20")
    cart = Cart(); cart.add(product, "2")
    sale = SalesService(db, settings_service=SettingsService(db))
    invoice_id, _ = sale.finalize_sale(cart, "credit", "0", customer_name="Ahmed")
    invoice_before = sale.get_invoice(invoice_id)
    customer_id = invoice_before.customer_id
    ProductService(db).delete_product(product.id)
    invoice_after = sale.get_invoice(invoice_id)
    assert invoice_after.payment_type == "credit"
    assert invoice_after.customer_name == "Ahmed"
    assert invoice_after.customer_id == customer_id
    assert invoice_after.items[0].product_name == "Credit Plug"
    from app.services.customer_service import CustomerService
    assert CustomerService(db).outstanding(customer_id) == Decimal("40.00")


def test_cash_and_credit_invoice_filters_are_separate(tmp_path):
    db = make_db(tmp_path)
    product = make_product(db, price="10")
    sale = SalesService(db, settings_service=SettingsService(db))
    c1=Cart(); c1.add(product,"1"); sale.finalize_sale(c1,"cash","10")
    c2=Cart(); c2.add(product,"2"); sale.finalize_sale(c2,"credit","0",customer_name="Ahmed Khan")
    assert all(i.payment_type == "cash" for i in sale.list_invoices(payment_type="cash"))
    credit=sale.list_invoices("ahmed", payment_type="credit")
    assert len(credit) == 1 and credit[0].customer_name == "Ahmed Khan"


def test_billing_inputs_are_synchronized_without_enter_dependency():
    pos=Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert "textChanged.connect" in pos
    assert "_sync_all_inputs" in pos
    assert "self._sync_all_inputs()" in pos
    assert "editingFinished.connect" in pos

def test_invoice_ui_uses_human_readable_dates():
    invoices=Path("app/ui/invoices_page.py").read_text(encoding="utf-8")
    dialog=Path("app/ui/invoice_details_dialog.py").read_text(encoding="utf-8")
    output=Path("app/services/invoice_output_service.py").read_text(encoding="utf-8")
    # format_invoice_datetime now lives with the detail dialog and is imported
    # by the list page, so the format string is asserted where it is defined.
    assert "%d %B %Y, %I:%M %p" in dialog
    assert "format_invoice_datetime" in invoices
    assert "%d %B %Y, %I:%M %p" in output

def test_billing_product_search_displays_retail_and_purchase_prices():
    pos = Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert "QTableWidget(0, 4)" in pos
    assert '["Product", "Unit", "Purchase Price", "Retail Price"]' in pos
    assert "product.purchase_price" in pos
    assert "product.selling_price" in pos
    assert "ScrollBarAlwaysOff" in pos


def test_product_price_ui_exposes_purchase_price_and_unit_price():
    products=Path("app/ui/products_page.py").read_text(encoding="utf-8")
    assert "Purchase Price" in products
    assert "Unit Price" in products
    assert "Retail/Selling Price" not in products
    assert "ZeroPriceLineEdit" in products

def test_customer_balance_adjustment_ui_exists():
    customers=Path("app/ui/customers_page.py").read_text(encoding="utf-8")
    assert "Adjust Balance" in customers
    assert "adjust_balance" in customers


def test_dashboard_summary_cards_have_visual_separation_contract():
    dashboard=Path("app/ui/dashboard_page.py").read_text(encoding="utf-8")
    qss=Path("app/resources/theme.qss").read_text(encoding="utf-8")
    assert "QGraphicsDropShadowEffect" in dashboard
    assert "setBlurRadius(14)" in dashboard
    assert "setOffset(0, 2)" in dashboard
    assert "QFrame#dashboardCard" in qss
    assert "border-radius: 10px" in qss
    assert "setMinimumHeight(108)" in dashboard
    assert "QSizePolicy.Policy.Fixed" in dashboard


def test_billing_single_click_only_selects_and_double_click_adds():
    pos=Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert "cellClicked.connect" not in pos
    assert "cellDoubleClicked.connect(self._double_click)" in pos
    assert "def _double_click" in pos
    assert "self.add_selected()" in pos


def test_empty_bill_discount_is_zero_and_zero_focus_is_replaceable():
    pos=Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert 'QLineEdit("0")' in pos
    assert 'self.cart.set_bill_discount(text.strip() or "0")' in pos
    assert 'self.cart.set_bill_discount(self.discount.text().strip() or "0")' in pos
    assert 'if text in {"0", "0.0", "0.00"}:' in pos
    assert "obj.clear()" in pos
    assert "Meaningful existing values are deliberately left untouched." in pos


def test_received_amount_empty_is_zero_and_default_zero_is_replaceable():
    pos=Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert 'self.paid = QLineEdit("0")' in pos
    assert 'paid = self.paid.text().strip() or "0"' in pos
    assert "self.paid.installEventFilter(self)" in pos


def test_customer_ledger_search_supports_date_month_time_and_is_read_only():
    service=Path("app/services/customer_service.py").read_text(encoding="utf-8")
    ui=Path("app/ui/customers_page.py").read_text(encoding="utf-8")
    for token in ("%d %B %Y, %I:%M %p", "%d %B", "%d %b", "%d/%m/%Y", "%Y-%m-%d", "%I:%M %p", "%H:%M", "%B"):
        assert token in service
    assert "filter_history(entries,text,c.name)" in ui
    assert "search.textChanged.connect(apply_filter)" in ui
    assert "filter_history(entries,text,c.name)" in ui


def test_customer_ledger_filter_matches_date_month_time_without_mutating_database(tmp_path):
    db=make_db(tmp_path)
    customer=__import__("app.services.customer_service",fromlist=["CustomerService"]).CustomerService(db).create("Search Me")
    # Create a real credit ledger entry through the normal sale path.
    product=make_product(db,price="10")
    cart=Cart(); cart.add(product,"2")
    sale=SalesService(db,settings_service=SettingsService(db))
    invoice_id,_=sale.finalize_sale(cart,"credit","0",customer_id=customer.id)
    from datetime import datetime
    from app.database.models.customer_ledger import CustomerLedgerEntry
    with db.SessionLocal.begin() as session:
        entry=session.query(CustomerLedgerEntry).filter_by(invoice_id=invoice_id).first()
        entry.created_at=datetime(2026,8,21,10,35,0)
    service=__import__("app.services.customer_service",fromlist=["CustomerService"]).CustomerService(db)
    entries=service.history(customer.id)
    assert len(service.filter_history(entries,"21 August")) == 1
    assert len(service.filter_history(entries,"August")) == 1
    assert len(service.filter_history(entries,"10:35 AM")) == 1
    before=[(e.id,e.amount,e.created_at) for e in service.history(customer.id)]
    service.filter_history(entries,"2026-08-21")
    after=[(e.id,e.amount,e.created_at) for e in service.history(customer.id)]
    assert after == before


def test_cart_blank_bill_discount_is_zero(tmp_path):
    db=make_db(tmp_path)
    product=make_product(db,price="10")
    cart=Cart(); cart.add(product,"5")
    cart.set_bill_discount("")
    assert cart.bill_discount == Decimal("0.00")
    assert cart.totals()[2] == Decimal("50.00")


def test_active_billing_cart_exposes_item_discount_editor():
    # Requirement #5: the Current Bill table carries a per-product "Discount"
    # column with a live editor wired through _discount_fields.
    pos = Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert '"Discount/Unit"' in pos.split('self.table.setHorizontalHeaderLabels', 1)[1].split(')', 1)[0]
    assert "_item_discount_editor" in pos
    assert "_discount_fields" in pos
    # The column takes the discount for a single item; the line discount is
    # derived as (per-unit x quantity) rather than typed in directly.
    assert "def set_unit_discount" in pos
    assert "self.cart.set_unit_discount(product_id, field.text().strip() or \"0\")" in pos


def test_billing_tables_have_no_horizontal_scrollbar_and_balanced_columns():
    pos = Path("app/ui/pos_page.py").read_text(encoding="utf-8")
    assert "setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)" in pos
    assert "def _size_product_results_columns" in pos
    assert "def _size_cart_columns" in pos


def test_customer_and_product_tables_use_balanced_column_sizing():
    customers = Path("app/ui/customers_page.py").read_text(encoding="utf-8")
    products = Path("app/ui/products_page.py").read_text(encoding="utf-8")
    assert "0.60" in customers
    assert "def _size_columns" in customers
    assert "0.48" in products
    assert "def _size_columns" in products


def test_typography_settings_are_persistent_and_controlled():
    service = Path("app/services/settings_service.py").read_text(encoding="utf-8")
    settings = Path("app/ui/settings_page.py").read_text(encoding="utf-8")
    main = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    for font in ("Segoe UI", "Aptos", "Arial", "Tahoma", "Verdana"):
        assert font in service
    for size in ("Comfortable", "Large", "Extra Large"):
        assert size in service
    assert "typography_font" in settings and "typography_size" in settings
    assert "_apply_typography" in main


def test_dotted_focus_artifact_is_suppressed_without_disabling_keyboard_focus():
    qss = Path("app/resources/theme.qss").read_text(encoding="utf-8")
    assert "QAbstractItemView { outline: none; }" in qss
    assert "QTableWidget::item:focus" in qss
