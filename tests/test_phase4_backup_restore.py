from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.database.connection import DatabaseManager
from app.database.models.category import Category
from app.database.models.customer import Customer
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.database.models.invoice import Invoice
from app.database.models.invoice_item import InvoiceItem
from app.database.models.product import Product
from app.database.models.settings import Setting
from app.database.models.stock_movement import StockMovement
from app.services.backup_restore_service import BackupRestoreError, BackupRestoreService
from app.services.customer_service import CustomerService
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService
from app.services.settings_service import SettingsService


def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "data" / "pos.db")
    manager.initialize()
    return manager


def seed_retailpos_data(manager: DatabaseManager, *, name: str = "Original Wire") -> int:
    product = ProductService(manager).create_product(
        name=name,
        selling_price="125.50",
        unit="meter",
        stock="25.5",
        minimum_stock="2",
    )
    with manager.SessionLocal.begin() as session:
        customer = Customer(name="Ali Store")
        session.add(customer)
        session.flush()
    cart = Cart()
    cart.add(product, "2.5")
    invoice = SalesService(manager, settings_service=SettingsService(manager)).finalize_sale(
        cart,
        "credit",
        Decimal("100.00"),
        customer_id=customer.id,
        customer_name="Ali Store",
    )
    with manager.SessionLocal.begin() as session:
        session.add(Category(name="Electrical"))
        session.add(Setting(key="custom_marker", value="keep-me"))
        # Ensure every current core table contains meaningful data where practical.
        session.add(StockMovement(product_id=product.id, quantity_change=Decimal("1.5"), movement_type="adjustment"))
        session.add(CustomerLedgerEntry(customer_id=customer.id, amount=Decimal("3.00"), transaction_type="test"))
    return invoice[0]


def table_counts(manager: DatabaseManager) -> dict[str, int]:
    tables = [
        "settings",
        "categories",
        "products",
        "stock_movements",
        "customers",
        "invoices",
        "invoice_items",
        "customer_ledger_entries",
    ]
    with manager.engine.connect() as conn:
        return {table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() for table in tables}


def test_create_valid_backup_and_integrity(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    backup = BackupRestoreService(manager, tmp_path / "backups").create_backup(tmp_path / "backups" / "manual.db")
    assert backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    manager.dispose()


def test_backup_contains_settings_products_customers_invoices_and_items(tmp_path: Path):
    manager = db(tmp_path)
    invoice_id = seed_retailpos_data(manager)
    backup = BackupRestoreService(manager).create_backup(tmp_path / "backup.db")
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='custom_marker'").fetchone()[0] == "keep-me"
        assert conn.execute("SELECT name FROM products WHERE name='Original Wire'").fetchone()
        assert conn.execute("SELECT name FROM customers WHERE name='Ali Store'").fetchone()
        assert conn.execute("SELECT id FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        assert conn.execute("SELECT product_name FROM invoice_items WHERE invoice_id=?", (invoice_id,)).fetchone()[0] == "Original Wire"
    manager.dispose()


def test_backup_preserves_historical_invoice_customer_name(tmp_path: Path):
    manager = db(tmp_path)
    invoice_id = seed_retailpos_data(manager)
    backup = BackupRestoreService(manager).create_backup(tmp_path / "backup.db")
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT customer_name FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0] == "Ali Store"
    manager.dispose()


def test_backup_captures_committed_wal_data(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    with manager.engine.connect() as conn:
        assert str(conn.execute(text("PRAGMA journal_mode")).scalar_one()).lower() == "wal"
    backup = BackupRestoreService(manager).create_backup(tmp_path / "wal_backup.db")
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    manager.dispose()


def test_invalid_non_sqlite_file_rejected_and_live_database_unchanged(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    invalid = tmp_path / "not_a_db.db"
    invalid.write_text("not sqlite")
    before = table_counts(manager)
    with pytest.raises(BackupRestoreError):
        BackupRestoreService(manager).restore(invalid)
    assert table_counts(manager) == before
    manager.dispose()


def test_corrupt_sqlite_backup_rejected(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    corrupt = tmp_path / "corrupt.db"
    with sqlite3.connect(corrupt) as conn:
        conn.execute("CREATE TABLE x(id INTEGER)")
    data = corrupt.read_bytes()
    corrupt.write_bytes(data[:20])
    with pytest.raises(BackupRestoreError):
        BackupRestoreService(manager).validate_sqlite(corrupt)
    manager.dispose()


def test_incompatible_sqlite_schema_rejected(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE products(id INTEGER PRIMARY KEY)")
        conn.commit()
    with pytest.raises(BackupRestoreError, match="compatible RetailPOS"):
        BackupRestoreService(manager).restore(other)
    assert table_counts(manager)["invoices"] == 1
    manager.dispose()


def test_pre_restore_safety_backup_created_and_valid(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    safety = service.create_pre_restore_backup()
    assert safety.name.startswith("RetailPOS_PreRestore_")
    with sqlite3.connect(safety) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    manager.dispose()


def test_restore_known_state_and_keep_safety_backup(tmp_path: Path):
    manager = db(tmp_path)
    invoice_id = seed_retailpos_data(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    backup = service.create_backup(tmp_path / "backup_a.db")

    ProductService(manager).create_product(name="Later Product", selling_price="9.00", unit="piece", stock="3", minimum_stock="0")
    with manager.SessionLocal.begin() as session:
        session.add(Setting(key="later_marker", value="gone-after-restore"))
    assert table_counts(manager)["products"] == 2

    safety = service.restore(backup)
    assert safety.exists()
    assert table_counts(manager)["products"] == 1
    assert table_counts(manager)["invoices"] == 1
    with manager.SessionLocal() as session:
        assert session.get(Invoice, invoice_id) is not None
        assert session.scalar(select(Product).where(Product.name == "Later Product")) is None
        assert session.get(Setting, "later_marker") is None
    manager.dispose()


def test_restore_preserves_all_current_retailpos_tables(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    expected = table_counts(manager)
    backup = BackupRestoreService(manager).create_backup(tmp_path / "backup.db")
    with manager.SessionLocal.begin() as session:
        session.add(Setting(key="temporary", value="remove"))
    BackupRestoreService(manager, tmp_path / "backups").restore(backup)
    assert table_counts(manager) == expected
    manager.dispose()


def test_restore_failure_recovers_original_database(tmp_path: Path, monkeypatch):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    backup = service.create_backup(tmp_path / "backup.db")
    with manager.SessionLocal.begin() as session:
        session.add(Setting(key="must_survive", value="yes"))

    original_initialize = manager.initialize
    calls = {"count": 0}

    def fail_first_initialize():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated reopen failure")
        original_initialize()

    monkeypatch.setattr(manager, "initialize", fail_first_initialize)
    with pytest.raises(BackupRestoreError, match="original database was restored"):
        service.restore(backup)
    with manager.SessionLocal() as session:
        assert session.get(Setting, "must_survive").value == "yes"
    manager.dispose()


def test_restore_does_not_proceed_if_safety_backup_fails(tmp_path: Path, monkeypatch):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    backup = service.create_backup(tmp_path / "backup.db")
    before = table_counts(manager)

    def fail_backup(_destination):
        raise BackupRestoreError("simulated safety backup failure")

    monkeypatch.setattr(service, "_backup_live_database", fail_backup)
    with pytest.raises(BackupRestoreError, match="safety backup failure"):
        service.restore(backup)
    assert table_counts(manager) == before
    manager.dispose()


def test_backup_and_restore_do_not_unexpectedly_change_data(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    before = table_counts(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    backup = service.create_backup(tmp_path / "backup.db")
    assert table_counts(manager) == before
    service.restore(backup)
    assert table_counts(manager) == before
    manager.dispose()


def test_backup_rejects_existing_destination_without_overwrite(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    destination = tmp_path / "backup.db"
    destination.write_text("keep")
    with pytest.raises(BackupRestoreError, match="already exists"):
        BackupRestoreService(manager).create_backup(destination)
    assert destination.read_text() == "keep"
    manager.dispose()


def test_restore_selected_backup_is_not_accepted_when_required_column_missing(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    invalid = tmp_path / "missing_column.db"
    backup = BackupRestoreService(manager).create_backup(invalid)
    with sqlite3.connect(invalid) as conn:
        conn.execute("ALTER TABLE invoices RENAME TO invoices_old")
        conn.execute("CREATE TABLE invoices AS SELECT id, invoice_number, created_at, subtotal, discount_total, grand_total, payment_type, paid_amount, remaining_amount, customer_id FROM invoices_old")
        conn.execute("DROP TABLE invoices_old")
        conn.commit()
    with pytest.raises(BackupRestoreError, match="missing column"):
        BackupRestoreService(manager).restore(invalid)
    assert table_counts(manager)["invoices"] == 1
    manager.dispose()


def test_default_backup_location_and_timestamped_name(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    backup_dir = tmp_path / "backups"
    result = BackupRestoreService(manager, backup_dir).create_backup()
    assert result.parent == backup_dir
    assert result.name.startswith("RetailPOS_Backup_")
    assert result.suffix == ".db"
    manager.dispose()


def test_default_backup_does_not_overwrite_existing_timestamped_backup(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    fixed_now = lambda: __import__("datetime").datetime(2026, 8, 17, 12, 0, 0)
    service = BackupRestoreService(manager, tmp_path / "backups", now_provider=fixed_now)
    first = service.create_backup()
    second = service.create_backup()
    assert first != second
    assert first.exists() and second.exists()
    manager.dispose()


def test_backup_schema_contains_all_current_model_tables(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    backup = BackupRestoreService(manager).create_backup(tmp_path / "backup.db")
    with sqlite3.connect(backup) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "settings",
        "categories",
        "products",
        "stock_movements",
        "customers",
        "invoices",
        "invoice_items",
        "customer_ledger_entries",
    } <= names
    manager.dispose()


def test_failed_validation_does_not_create_pre_restore_safety_backup(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    service = BackupRestoreService(manager, tmp_path / "backups")
    invalid = tmp_path / "invalid.db"
    invalid.write_text("not sqlite")
    with pytest.raises(BackupRestoreError):
        service.restore(invalid)
    assert not (tmp_path / "backups").exists()
    manager.dispose()


def test_restore_reapplies_wal_and_foreign_keys(tmp_path: Path):
    manager = db(tmp_path)
    seed_retailpos_data(manager)
    backup = BackupRestoreService(manager).create_backup(tmp_path / "backup.db")
    BackupRestoreService(manager, tmp_path / "backups").restore(backup)
    with manager.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert str(conn.execute(text("PRAGMA journal_mode")).scalar_one()).lower() == "wal"
    manager.dispose()
