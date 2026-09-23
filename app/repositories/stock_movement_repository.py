from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from app.database.models.stock_movement import StockMovement


class StockMovementRepository:
    def add(self, session: Session, movement: StockMovement) -> StockMovement:
        session.add(movement)
        return movement

    def list_for_product(self, session: Session, product_id: int) -> list[StockMovement]:
        stmt = select(StockMovement).where(StockMovement.product_id == product_id).order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        return list(session.scalars(stmt).all())

    def list_all(self, session: Session) -> list[StockMovement]:
        stmt = (
            select(StockMovement)
            .options(selectinload(StockMovement.product))
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        )
        return list(session.scalars(stmt).all())
