from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.models.customer_ledger import CustomerLedgerEntry
class CustomerLedgerRepository:
    def add(self,s,e): s.add(e); return e
    def get(self,s,ledger_id): return s.get(CustomerLedgerEntry,ledger_id)
    def list_for_customer(self,s,customer_id):
        return list(s.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.customer_id==customer_id).order_by(CustomerLedgerEntry.created_at.asc(),CustomerLedgerEntry.id.asc())).all())
