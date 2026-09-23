from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func, select
from app.database.models.customer import Customer
from app.database.models.product import Product
from app.database.models.customer_ledger import CustomerLedgerEntry
from app.database.models.invoice import Invoice

@dataclass(frozen=True)
class DashboardSummary:
    today_sales: Decimal
    today_invoices: int
    today_cash_received: Decimal
    today_credit_sales: Decimal
    total_outstanding: Decimal
    customers_with_outstanding: int
    total_products: int = 0

@dataclass(frozen=True)
class LowStockProduct:
    product_id: int
    name: str
    unit: str
    current_stock: Decimal
    minimum_stock: Decimal
    status: str

@dataclass(frozen=True)
class RecentSale:
    invoice_id: int
    invoice_number: str
    created_at: datetime
    customer_name: str
    grand_total: Decimal
    payment_type: str
    remaining_amount: Decimal

class DashboardService:
    def __init__(self, db, now_provider=None): self.db=db; self._now_provider=now_provider or datetime.now
    def summary(self):
        now=self._now_provider(); start=datetime(now.year,now.month,now.day); end=start+timedelta(days=1)
        with self.db.SessionLocal() as s:
            # Voided invoices (Edit Invoice) are excluded from every revenue
            # figure below so a superseded sale is never double-counted
            # alongside its replacement.
            sales,count=s.execute(select(func.coalesce(func.sum(Invoice.grand_total),0),func.count(Invoice.id)).where(Invoice.created_at>=start,Invoice.created_at<end,Invoice.voided.is_(False))).one()
            cash=s.scalar(select(func.coalesce(func.sum(Invoice.paid_amount),0)).where(Invoice.created_at>=start,Invoice.created_at<end,Invoice.payment_type=="cash",Invoice.voided.is_(False)))
            credit=s.scalar(select(func.coalesce(func.sum(Invoice.grand_total),0)).where(Invoice.created_at>=start,Invoice.created_at<end,Invoice.payment_type=="credit",Invoice.voided.is_(False)))
            total_products=s.scalar(select(func.count(Product.id)).where(Product.is_active.is_(True)))
            balances = s.execute(select(Customer.id,func.coalesce(func.sum(CustomerLedgerEntry.amount),0)).join(CustomerLedgerEntry,CustomerLedgerEntry.customer_id==Customer.id).group_by(Customer.id)).all()
            outstanding=sum((Decimal(v) for _,v in balances if Decimal(v)>0),Decimal("0.00"))
            customers=sum(1 for _,v in balances if Decimal(v)>0)
            return DashboardSummary(Decimal(sales or 0),int(count or 0),Decimal(cash or 0),Decimal(credit or 0),outstanding,customers,int(total_products or 0))
    def low_stock_products(self):
        with self.db.SessionLocal() as s:
            products=s.scalars(select(Product).where(Product.is_active.is_(True),Product.current_stock<=Product.minimum_stock).order_by(Product.current_stock.asc(),Product.name.asc())).all()
            return [LowStockProduct(p.id,p.name,p.unit,Decimal(p.current_stock),Decimal(p.minimum_stock),"Out of stock" if Decimal(p.current_stock)<=0 else "Low stock") for p in products]

    def recent_sales(self,limit=10):
        with self.db.SessionLocal() as s:
            rows=s.execute(select(Invoice.id,Invoice.invoice_number,Invoice.created_at,Invoice.customer_name,Invoice.grand_total,Invoice.payment_type,Invoice.remaining_amount).where(Invoice.voided.is_(False)).order_by(Invoice.created_at.desc(),Invoice.id.desc()).limit(limit)).all()
            return [RecentSale(r.id,r.invoice_number,r.created_at,r.customer_name or "Walk-in",Decimal(r.grand_total),r.payment_type,Decimal(r.remaining_amount)) for r in rows]
