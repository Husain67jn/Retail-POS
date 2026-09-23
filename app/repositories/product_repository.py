from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from app.database.models.product import Product

class ProductRepository:
    def get(self, session: Session, product_id: int) -> Product | None:
        # Eager-load variants: callers (e.g. ProductDialog) read
        # product.variants after the session that fetched it may already be
        # closed, and a lazy load on a detached instance would raise
        # DetachedInstanceError.
        return session.get(Product, product_id, options=[selectinload(Product.variants)])

    def list(self, session: Session, search: str = "", active_only: bool = False) -> list[Product]:
        """Every product, flat, regardless of group/variant relationship —
        backs the State A (search empty) inventory table."""
        stmt = select(Product).order_by(Product.name.asc())
        if search.strip():
            stmt = stmt.where(Product.name.ilike(f"%{search.strip()}%"))
        return list(session.scalars(stmt).all())

    def search_grouped(self, session: Session, term: str) -> list[Product]:
        """Root products (parent_id IS NULL) matching ``term`` either by their
        own name or by one of their brand-variants' name, variants
        eager-loaded — backs the State B accordion search view. A root whose
        own name matches is included with ALL of its variants (so the whole
        group is visible); a root that only matches through a child still
        brings every variant along, not just the matching one, so the group
        reads as a complete comparison table.
        """
        needle = f"%{term.strip()}%"
        matching_parent_ids = select(Product.parent_id).where(
            Product.parent_id.isnot(None), Product.name.ilike(needle)
        )
        stmt = (
            select(Product)
            .where(
                Product.parent_id.is_(None),
                (Product.name.ilike(needle)) | (Product.id.in_(matching_parent_ids)),
            )
            .options(selectinload(Product.variants))
            .order_by(Product.name.asc())
        )
        return list(session.scalars(stmt).all())

    def list_parent_choices(self, session: Session, exclude_id: int | None = None) -> list[Product]:
        """Root products eligible to be picked as a parent group in the Add/Edit
        dialog. Excludes ``exclude_id`` so a product being edited can never be
        offered as its own parent."""
        stmt = select(Product).where(Product.parent_id.is_(None)).order_by(Product.name.asc())
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        return list(session.scalars(stmt).all())

    def add(self, session: Session, product: Product) -> Product:
        session.add(product); return product
