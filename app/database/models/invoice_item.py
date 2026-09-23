from __future__ import annotations
from decimal import Decimal
from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24,6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    item_discount: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    invoice = relationship("Invoice", back_populates="items")
