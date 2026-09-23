from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.models.customer import Customer
class CustomerRepository:
    def add(self, session: Session, customer: Customer): session.add(customer); return customer
    def get(self, session: Session, customer_id: int): return session.get(Customer, customer_id)
    def search(self, session: Session, search: str = "", active_only=True):
        stmt=select(Customer).order_by(Customer.name.asc())
        if search.strip(): stmt=stmt.where(Customer.name.ilike(f"%{search.strip()}%"))
        if active_only: stmt=stmt.where(Customer.is_active.is_(True))
        return list(session.scalars(stmt).all())
