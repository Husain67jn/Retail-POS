from sqlalchemy import or_, select, and_, func
from sqlalchemy.orm import selectinload
from datetime import datetime
from app.database.models.invoice import Invoice
from app.database.models.customer import Customer

_MONTHS = {}
for i,m in enumerate(("January","February","March","April","May","June","July","August","September","October","November","December"),1):
    _MONTHS[m.lower()] = i
    _MONTHS[m[:3].lower()] = i

def _date_filter(stmt, search):
    term=search.strip()
    if not term: return stmt, False
    for fmt in ("%d/%m/%Y","%Y-%m-%d","%d-%m-%Y"):
        try:
            d=datetime.strptime(term,fmt).date()
            return stmt.where(Invoice.created_at >= datetime(d.year,d.month,d.day), Invoice.created_at < datetime(d.year,d.month,d.day)+__import__('datetime').timedelta(days=1)), True
        except ValueError: pass
    parts=term.lower().replace(',','').split()
    if len(parts) in (2,3):
        try:
            day=int(parts[0]); month=_MONTHS.get(parts[1]); year=int(parts[2]) if len(parts)==3 else None
            if month and 1<=day<=31:
                cond=[func.strftime('%d',Invoice.created_at)==f'{day:02d}',func.strftime('%m',Invoice.created_at)==f'{month:02d}']
                if year: cond.append(func.strftime('%Y',Invoice.created_at)==str(year))
                return stmt.where(and_(*cond)), True
        except ValueError: pass
    return stmt, False

class InvoiceRepository:
    def add(self, s, invoice): s.add(invoice); return invoice
    def get(self, s, invoice_id):
        return s.scalar(select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.customer)).where(Invoice.id == invoice_id))
    def get_by_number(self, s, number):
        return s.scalar(select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.customer)).where(Invoice.invoice_number == number))
    def list(self, s, search="", payment_type=None):
        stmt = select(Invoice).options(selectinload(Invoice.customer)).order_by(Invoice.created_at.desc(), Invoice.id.desc())
        if payment_type: stmt = stmt.where(Invoice.payment_type == payment_type)
        stmt, is_date = _date_filter(stmt, search)
        if search.strip() and not is_date:
            term=f"%{search.strip()}%"
            stmt=stmt.join(Invoice.customer, isouter=True).where(or_(Invoice.invoice_number.ilike(term),Invoice.customer_name.ilike(term),Customer.name.ilike(term)))
        return list(s.scalars(stmt).unique().all())
