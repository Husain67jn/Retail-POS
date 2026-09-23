from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Self-referential: a NULL parent_id is a standalone product OR the
    # general group row shown as the parent in the Inventory accordion (e.g.
    # "Bearing 6204"); a set parent_id is a brand variant under that group
    # (e.g. "Bearmax", "China", "Gleason"), each keeping its own prices.
    # One level deep only — a variant is never itself a parent.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    wholesale_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    selling_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    current_stock: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False, default=Decimal("0"))
    minimum_stock: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False, default=Decimal("0"))
    # Freeform — the business records purchase dates in whatever format they
    # got the paperwork in ("2 Sep 2026", "15/08/2026", etc.), so this is a
    # plain string, not a Date column that would force one strict format and
    # reject anything else.
    purchase_date: Mapped[str | None] = mapped_column(String(100), nullable=True, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    category = relationship("Category", back_populates="products")
    stock_movements = relationship("StockMovement", back_populates="product")
    parent = relationship("Product", remote_side=[id], back_populates="variants")
    variants = relationship(
        "Product", back_populates="parent", cascade="save-update, merge",
        order_by="Product.name",
    )
