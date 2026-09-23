from __future__ import annotations

from decimal import Decimal

from app.database.models.stock_movement import StockMovement
from app.repositories.product_repository import ProductRepository
from app.repositories.stock_movement_repository import StockMovementRepository
from app.services.product_service import decimal_value


class InventoryService:
    def __init__(self, db, product_repository: ProductRepository | None = None,
                 movement_repository: StockMovementRepository | None = None):
        self.db = db
        self.products = product_repository or ProductRepository()
        self.movements = movement_repository or StockMovementRepository()

    def change_stock(self, product_id: int, quantity_change: object, *, movement_type: str,
                     note: str | None = None, reference: str | None = None) -> Decimal:
        change = decimal_value(quantity_change, "Stock change")
        if change == 0:
            raise ValueError("Stock change cannot be zero")
        if movement_type not in {"stock_addition", "manual_adjustment"}:
            raise ValueError("Unsupported stock movement type")

        with self.db.SessionLocal.begin() as session:
            product = self.products.get(session, product_id)
            if product is None:
                raise ValueError("Product not found")
            before = Decimal(product.current_stock)
            after = before + change
            if after < 0:
                raise ValueError("Stock cannot become negative")
            product.current_stock = after
            session.flush()
            self.movements.add(session, StockMovement(
                product_id=product.id,
                quantity_change=change,
                movement_type=movement_type,
                note=note,
                reference=reference,
            ))
            return after

    def add_stock(self, product_id: int, quantity: object, note: str | None = None) -> Decimal:
        quantity_decimal = decimal_value(quantity, "Stock quantity")
        if quantity_decimal <= 0:
            raise ValueError("Stock addition must be greater than zero")
        return self.change_stock(product_id, quantity_decimal, movement_type="stock_addition", note=note)

    def adjust_stock(self, product_id: int, quantity_change: object, note: str | None = None) -> Decimal:
        return self.change_stock(product_id, quantity_change, movement_type="manual_adjustment", note=note)

    def list_movements(self, product_id: int):
        with self.db.SessionLocal() as session:
            return self.movements.list_for_product(session, product_id)

    def list_all_movements(self):
        with self.db.SessionLocal() as session:
            return self.movements.list_all(session)
