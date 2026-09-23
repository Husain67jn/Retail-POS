from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.category import Category


class CategoryRepository:
    def get(self, session: Session, category_id: int) -> Category | None:
        return session.get(Category, category_id)

    def list(self, session: Session, search: str = "", active_only: bool = False) -> list[Category]:
        stmt = select(Category).order_by(Category.name.asc())
        if search.strip():
            stmt = stmt.where(Category.name.ilike(f"%{search.strip()}%"))
        if active_only:
            stmt = stmt.where(Category.is_active.is_(True))
        return list(session.scalars(stmt).all())

    def get_by_name_ci(self, session: Session, name: str) -> Category | None:
        return session.scalar(select(Category).where(func.lower(Category.name) == name.lower()))

    def add(self, session: Session, category: Category) -> Category:
        session.add(category)
        return category
