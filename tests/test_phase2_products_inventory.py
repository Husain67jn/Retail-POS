from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.database.connection import DatabaseManager
from app.database.models.category import Category
from app.database.models.product import Product
from app.database.models.stock_movement import StockMovement
from app.services.inventory_service import InventoryService
from app.services.product_service import SUPPORTED_UNITS, ProductService


def make_manager(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "data" / "pos.db")
    manager.initialize()
    return manager


def test_product_creation(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        category = service.create_category("Electrical")
        product = service.create_product(
            name="Wire", selling_price=Decimal("10.00"), unit="meter",
            stock=Decimal("100.0"), minimum_stock=Decimal("10"), category_id=category.id,
        )
        assert product.id is not None
        assert product.name == "Wire"
        assert product.current_stock == Decimal("100.000000")
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM products")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar_one() == 1
    finally:
        db.dispose()


def test_product_validation(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        with pytest.raises(ValueError, match="Product name"):
            service.create_product(name="", selling_price="10", unit="meter", stock="1", minimum_stock="0")
        with pytest.raises(ValueError, match="negative"):
            service.create_product(name="Wire", selling_price="-1", unit="meter", stock="1", minimum_stock="0")
        with pytest.raises(ValueError, match="Unsupported"):
            service.create_product(name="Wire", selling_price="1", unit="liter", stock="1", minimum_stock="0")
        with pytest.raises(ValueError, match="negative"):
            service.create_product(name="Wire", selling_price="1", unit="meter", stock="-1", minimum_stock="0")
    finally:
        db.dispose()


def test_category_creation_and_duplicate_handling(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        service.create_category("Electrical")
        with pytest.raises(ValueError, match="already exists"):
            service.create_category("electrical")
    finally:
        db.dispose()


def test_product_search_and_update(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        product = service.create_product(name="Copper Wire", selling_price="10.00", unit="meter", stock="5.5", minimum_stock="2")
        service.create_product(name="Switch", selling_price="100", unit="piece", stock="10", minimum_stock="1")
        assert [p.name for p in service.list_products("copper")] == ["Copper Wire"]
        updated = service.update_product(product.id, name="Copper Wire 2", selling_price="12.50", unit="meter", minimum_stock="3")
        assert updated.selling_price == Decimal("12.50")
        assert updated.minimum_stock == Decimal("3.000000")
        assert service.get_product(product.id).current_stock == Decimal("5.500000")
    finally:
        db.dispose()


def test_decimal_price_and_stock_round_trip(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        product = service.create_product(name="Wire", selling_price=Decimal("10.25"), unit="meter", stock=Decimal("100.125"), minimum_stock=Decimal("5.5"))
        db.dispose()
        db2 = DatabaseManager(tmp_path / "data" / "pos.db")
        db2.initialize()
        try:
            loaded = ProductService(db2).get_product(product.id)
            assert isinstance(loaded.selling_price, Decimal)
            assert isinstance(loaded.current_stock, Decimal)
            assert loaded.selling_price == Decimal("10.25")
            assert loaded.current_stock == Decimal("100.125000")
        finally:
            db2.dispose()
    finally:
        if db.engine is not None:
            db.dispose()


def test_opening_stock_manual_addition_and_adjustment(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        products = ProductService(db)
        inventory = InventoryService(db)
        product = products.create_product(name="Wire", selling_price="10.00", unit="meter", stock="100", minimum_stock="5")
        assert product.current_stock == Decimal("100.000000")
        assert inventory.add_stock(product.id, "50.5") == Decimal("150.500000")
        assert inventory.adjust_stock(product.id, "-2.5") == Decimal("148.000000")
        movements = inventory.list_movements(product.id)
        assert [m.movement_type for m in reversed(movements)] == ["opening_stock", "stock_addition", "manual_adjustment"]
        assert [m.quantity_change for m in reversed(movements)] == [Decimal("100.000000"), Decimal("50.500000"), Decimal("-2.500000")]
    finally:
        db.dispose()


def test_low_stock_detection(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        product = service.create_product(name="Wire", selling_price="10", unit="meter", stock="5", minimum_stock="5")
        assert service.is_low_stock(product) is True
        service.update_product(product.id, name="Wire", selling_price="10", unit="meter", minimum_stock="4")
        assert service.is_low_stock(service.get_product(product.id)) is False
    finally:
        db.dispose()


def test_stock_update_and_movement_are_atomic(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        class FailingMovementRepository:
            def add(self, session, movement):
                raise RuntimeError("movement insert failed")

        products = ProductService(db)
        product = products.create_product(name="Wire", selling_price="10", unit="meter", stock="100", minimum_stock="5")
        inventory = InventoryService(db, movement_repository=FailingMovementRepository())
        with pytest.raises(RuntimeError, match="movement insert failed"):
            inventory.add_stock(product.id, "5")
        loaded = products.get_product(product.id)
        assert loaded.current_stock == Decimal("100.000000")
        with db.engine.connect() as conn:
            assert conn.execute(select(StockMovement).where(StockMovement.product_id == product.id)).fetchall()
            # Only the opening-stock movement exists.
            assert conn.execute(text("SELECT COUNT(*) FROM stock_movements WHERE product_id = :id"), {"id": product.id}).scalar_one() == 1
    finally:
        db.dispose()


def test_existing_settings_are_preserved(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        with db.engine.begin() as conn:
            conn.execute(text("INSERT OR REPLACE INTO settings(key, value) VALUES ('shop_name', 'Mughal Electric Store')"))
            conn.execute(text("UPDATE settings SET value='42' WHERE key='invoice_sequence'"))
        db.dispose()
        db2 = DatabaseManager(tmp_path / "data" / "pos.db")
        db2.initialize()
        try:
            with db2.engine.connect() as conn:
                assert conn.execute(text("SELECT value FROM settings WHERE key='shop_name'")).scalar_one() == "Mughal Electric Store"
                assert conn.execute(text("SELECT value FROM settings WHERE key='invoice_sequence'")).scalar_one() == "42"
        finally:
            db2.dispose()
    finally:
        if db.engine is not None:
            db.dispose()


def test_category_list_update_and_deactivation(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        category = service.create_category("Electrical")
        service.update_category(category.id, "Electrical Supplies")
        names = [item.name for item in service.list_categories()]
        assert names == ["Electrical Supplies"]
        service.set_category_active(category.id, False)
        assert service.list_categories(active_only=True) == []
    finally:
        db.dispose()


def test_supported_units_are_exactly_the_phase2_units(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        assert SUPPORTED_UNITS == ("piece", "meter", "dozen", "kg", "box")
        for unit in SUPPORTED_UNITS:
            product = service.create_product(name=f"{unit} item", selling_price="1.00", unit=unit, stock="1", minimum_stock="0")
            assert product.unit == unit
        with pytest.raises(ValueError):
            service.create_product(name="Liter item", selling_price="1.00", unit="liter", stock="1", minimum_stock="0")
    finally:
        db.dispose()


def test_negative_stock_is_rejected(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        products = ProductService(db)
        inventory = InventoryService(db)
        product = products.create_product(name="Wire", selling_price="10", unit="meter", stock="2", minimum_stock="0")
        with pytest.raises(ValueError, match="cannot become negative"):
            inventory.adjust_stock(product.id, "-2.1")
        assert products.get_product(product.id).current_stock == Decimal("2.000000")
    finally:
        db.dispose()


def test_decimal_stock_math_is_exact_for_business_operations(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        products = ProductService(db)
        inventory = InventoryService(db)
        product = products.create_product(name="Wire", selling_price="10.00", unit="meter", stock="100.0", minimum_stock="1")
        assert inventory.adjust_stock(product.id, "-5.5") == Decimal("94.500000")
        assert products.get_product(product.id).current_stock == Decimal("94.500000")
    finally:
        db.dispose()


def test_stock_movement_records_optional_note_and_reference(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        products = ProductService(db)
        inventory = InventoryService(db)
        product = products.create_product(name="Wire", selling_price="10", unit="meter", stock="10", minimum_stock="1")
        inventory.change_stock(product.id, "2.5", movement_type="manual_adjustment", note="Physical count", reference="COUNT-001")
        movement = inventory.list_movements(product.id)[0]
        assert movement.note == "Physical count"
        assert movement.reference == "COUNT-001"
        assert movement.quantity_change == Decimal("2.500000")
    finally:
        db.dispose()


def test_product_activation_state_can_be_changed(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        product = service.create_product(name="Wire", selling_price="10", unit="meter", stock="1", minimum_stock="0")
        service.set_product_active(product.id, False)
        assert service.get_product(product.id).is_active is False
        service.set_product_active(product.id, True)
        assert service.get_product(product.id).is_active is True
    finally:
        db.dispose()


def test_selling_price_accepts_zero_to_two_decimal_places(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        for index, price in enumerate(("10", "10.5", "10.50", "10.00"), start=1):
            product = service.create_product(
                name=f"Valid Price {index}",
                selling_price=price,
                unit="piece",
                stock="0",
                minimum_stock="0",
            )
            assert product.selling_price == Decimal(price)
    finally:
        db.dispose()


def test_selling_price_rejects_more_than_two_decimal_places(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        for price in ("10.125", "10.999"):
            with pytest.raises(ValueError, match="more than 2 decimal places"):
                service.create_product(
                    name=f"Invalid Price {price}",
                    selling_price=price,
                    unit="piece",
                    stock="0",
                    minimum_stock="0",
                )
    finally:
        db.dispose()


def test_rejected_money_precision_is_not_persisted(tmp_path: Path):
    db = make_manager(tmp_path)
    try:
        service = ProductService(db)
        with pytest.raises(ValueError, match="more than 2 decimal places"):
            service.create_product(
                name="Invalid Price",
                selling_price="10.125",
                unit="piece",
                stock="0",
                minimum_stock="0",
            )
        assert service.list_products("Invalid Price") == []
        with db.engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM products WHERE name = :name"),
                {"name": "Invalid Price"},
            ).scalar_one()
            assert count == 0
    finally:
        db.dispose()
