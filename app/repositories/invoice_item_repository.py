from app.database.models.invoice_item import InvoiceItem
class InvoiceItemRepository:
    def add(self,s,item): s.add(item); return item
