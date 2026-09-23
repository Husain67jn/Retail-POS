from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_number: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True, index=True)
    # Historical display snapshot. This is intentionally independent of Customer
    # so an invoice remains readable even if the customer is renamed/deactivated.
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Edit Invoice support: a voided invoice is never deleted (its rows and any
    # posted ledger entries remain for audit history) — it is only flagged so
    # it reads clearly as superseded once a replacement sale is finalized.
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    customer = relationship("Customer", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    ledger_entries = relationship("CustomerLedgerEntry", back_populates="invoice")
