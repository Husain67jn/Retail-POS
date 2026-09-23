from decimal import Decimal
import pytest
from app.database.connection import DatabaseManager
from app.services.product_service import ProductService
from app.services.sales_service import Cart, SalesService
from app.services.customer_service import CustomerService
from app.services.settings_service import SettingsService
from app.database.models.invoice import Invoice
from app.database.models.invoice_item import InvoiceItem
from app.database.models.stock_movement import StockMovement
from app.database.models.customer_ledger import CustomerLedgerEntry

def db(tmp_path):
 d=DatabaseManager(tmp_path/'pos.db'); d.initialize(); return d

def product(d,name='Wire',price='10.00',stock='100'):
 return ProductService(d).create_product(name=name,selling_price=price,unit='meter',stock=stock,minimum_stock='5')

def test_cart_decimal_and_discounts(tmp_path):
 d=db(tmp_path); p=product(d); c=Cart(); c.add(p,'5.5','5'); assert c.totals()==(Decimal('55.00'),Decimal('5.00'),Decimal('50.00'))
 c.set_bill_discount('10'); assert c.totals()==(Decimal('55.00'),Decimal('15.00'),Decimal('40.00'))

def test_cart_rejects_invalid_discount(tmp_path):
 d=db(tmp_path); p=product(d); c=Cart(); c.add(p,'1')
 with pytest.raises(ValueError): c.set_item_discount(p.id,'10.01')
 with pytest.raises(ValueError): c.set_bill_discount('10.01')

def test_cash_sale_does_not_touch_stock(tmp_path):
 d=db(tmp_path); p=product(d,stock='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'5.5'); iid,num=s.finalize_sale(c,'cash','55'); assert iid and num.startswith('INV-'); assert ProductService(d).get_product(p.id).current_stock==Decimal('10.000000')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==1; assert x.query(StockMovement).filter_by(movement_type='sale').count()==0

def test_cash_partial_rejected(tmp_path):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2')
 with pytest.raises(ValueError): s.finalize_sale(c,'cash','10')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==0

def test_customer_credit_zero_partial_full_and_payment(tmp_path):
 d=db(tmp_path); p=product(d); cs=CustomerService(d); cust=cs.create('Ali'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'10')
 iid,_=s.finalize_sale(c,'credit','0',cust.id); assert cs.outstanding(cust.id)==Decimal('100.00')
 assert cs.record_payment(cust.id,'40')==Decimal('60.00'); assert cs.outstanding(cust.id)==Decimal('60.00'); assert cs.record_payment(cust.id,'60')==Decimal('0.00')
 with pytest.raises(ValueError): cs.record_payment(cust.id,'1')
 with d.SessionLocal() as x: assert x.query(CustomerLedgerEntry).count()==3

def test_credit_requires_customer(tmp_path):
 d=db(tmp_path); p=product(d); c=Cart(); c.add(p,'1'); s=SalesService(d,settings_service=SettingsService(d))
 with pytest.raises(ValueError): s.finalize_sale(c,'credit','0')

def test_invoice_snapshot_and_prefix(tmp_path):
 d=db(tmp_path); p=product(d,name='Old'); ss=SettingsService(d); x=ss.get_settings(); ss.save_settings(x.__class__(**{**x.__dict__,'invoice_prefix':'SALE'})); s=SalesService(d,settings_service=ss); c=Cart(); c.add(p,'1'); iid,num=s.finalize_sale(c,'cash','10'); ProductService(d).update_product(p.id,name='New',selling_price='20',unit='meter',minimum_stock='0'); inv=s.get_invoice(iid); assert num.startswith('SALE-'); assert inv.items[0].product_name=='Old'; assert inv.items[0].unit_price==Decimal('10.00')

def test_stock_setting_no_longer_affects_sales(tmp_path):
 d=db(tmp_path); p=product(d,stock='1'); ss=SettingsService(d); x=ss.get_settings(); ss.save_settings(x.__class__(**{**x.__dict__,'allow_negative_stock':True})); s=SalesService(d,settings_service=ss); c=Cart(); c.add(p,'2'); s.finalize_sale(c,'cash','20'); assert ProductService(d).get_product(p.id).current_stock==Decimal('1.000000')

def test_sales_are_not_blocked_by_stock(tmp_path):
 d=db(tmp_path); p=product(d,stock='1'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2'); s.finalize_sale(c,'cash','20')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==1; assert x.query(InvoiceItem).count()==1; assert x.query(StockMovement).filter_by(movement_type='sale').count()==0

def test_invoice_sequence_rolls_back_on_failure(tmp_path,monkeypatch):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 def fail(*a,**k): raise RuntimeError('invoice item failure')
 monkeypatch.setattr(s.invoice_items,'add',fail)
 with pytest.raises(RuntimeError): s.finalize_sale(c,'cash','10')
 with d.SessionLocal() as x: assert x.get(__import__('app.database.models.settings',fromlist=['Setting']).Setting,'invoice_sequence').value=='0'; assert x.query(Invoice).count()==0

def test_invoice_number_unique_sequence(tmp_path):
 d=db(tmp_path); p=product(d,stock='10'); s=SalesService(d,settings_service=SettingsService(d))
 for _ in range(2): c=Cart(); c.add(p,'1'); s.finalize_sale(c,'cash','10')
 assert len({i.invoice_number for i in s.list_invoices()})==2

def test_atomic_invoice_insertion_failure(tmp_path,monkeypatch):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 monkeypatch.setattr(s.invoices,'add',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('invoice fail')))
 with pytest.raises(RuntimeError): s.finalize_sale(c,'cash','10')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==0; assert x.query(InvoiceItem).count()==0; assert x.query(StockMovement).filter_by(movement_type='sale').count()==0

def test_atomic_invoice_item_failure(tmp_path,monkeypatch):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 monkeypatch.setattr(s.invoice_items,'add',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('item fail')))
 with pytest.raises(RuntimeError): s.finalize_sale(c,'cash','10')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==0; assert x.query(InvoiceItem).count()==0; assert x.query(ProductService(d).products.__class__ if False else StockMovement).filter_by(movement_type='sale').count()==0
 assert ProductService(d).get_product(p.id).current_stock==Decimal('100.000000')

def test_sale_transaction_remains_atomic_without_stock(tmp_path,monkeypatch):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 monkeypatch.setattr(s.invoice_items,'add',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('item fail')))
 with pytest.raises(RuntimeError): s.finalize_sale(c,'cash','10')
 with d.SessionLocal() as x: assert x.query(Invoice).count()==0; assert x.query(InvoiceItem).count()==0
 assert ProductService(d).get_product(p.id).current_stock==Decimal('100.000000')

def test_atomic_ledger_failure(tmp_path,monkeypatch):
 d=db(tmp_path); p=product(d); cust=CustomerService(d).create('Ali'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 monkeypatch.setattr(s.ledger,'add',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('ledger fail')))
 with pytest.raises(RuntimeError): s.finalize_sale(c,'credit','0',cust.id)
 with d.SessionLocal() as x: assert x.query(Invoice).count()==0; assert x.query(CustomerLedgerEntry).count()==0; assert x.query(StockMovement).filter_by(movement_type='sale').count()==0
 assert ProductService(d).get_product(p.id).current_stock==Decimal('100.000000')

def test_product_search_is_case_insensitive_and_active(tmp_path):
 d=db(tmp_path); ps=ProductService(d); p=ps.create_product(name='Copper Wire',selling_price='12.50',unit='meter',stock='10',minimum_stock='1'); ps.create_product(name='Switch',selling_price='20',unit='piece',stock='5',minimum_stock='1'); ps.set_product_active(p.id,False); assert [x.name for x in ps.list_products('wire')]==['Copper Wire']

def test_cart_combined_discounts_calculation(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); p2=product(d,name='Switch',price='20'); c=Cart(); c.add(p,'5','5'); c.add(p2,'2','10'); c.set_bill_discount('10'); assert c.totals()==(Decimal('90.00'),Decimal('25.00'),Decimal('65.00'))

def test_invoice_subtotal_no_discounts_is_gross_and_reconciles(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2'); iid,_=s.finalize_sale(c,'cash','20');
 with d.SessionLocal() as session:
  inv=session.get(Invoice,iid); assert inv.subtotal==Decimal('20.00'); assert inv.discount_total==Decimal('0.00'); assert inv.grand_total==Decimal('20.00'); assert inv.subtotal-inv.discount_total==inv.grand_total

def test_invoice_subtotal_item_discount_reconciles(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2','2'); iid,_=s.finalize_sale(c,'cash','18');
 with d.SessionLocal() as session:
  inv=session.get(Invoice,iid); assert inv.subtotal==Decimal('20.00'); assert inv.discount_total==Decimal('2.00'); assert inv.grand_total==Decimal('18.00'); assert inv.subtotal-inv.discount_total==inv.grand_total

def test_invoice_subtotal_bill_discount_reconciles(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2'); c.set_bill_discount('3'); iid,_=s.finalize_sale(c,'cash','17');
 with d.SessionLocal() as session:
  inv=session.get(Invoice,iid); assert inv.subtotal==Decimal('20.00'); assert inv.discount_total==Decimal('3.00'); assert inv.grand_total==Decimal('17.00'); assert inv.subtotal-inv.discount_total==inv.grand_total

def test_invoice_subtotal_item_and_bill_discount_reconcile(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2','2'); c.set_bill_discount('1'); iid,_=s.finalize_sale(c,'cash','17');
 with d.SessionLocal() as session:
  inv=session.get(Invoice,iid); assert inv.subtotal==Decimal('20.00'); assert inv.discount_total==Decimal('3.00'); assert inv.grand_total==Decimal('17.00'); assert inv.subtotal-inv.discount_total==inv.grand_total

def test_negative_payment_rejected(tmp_path):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 with pytest.raises(ValueError): s.finalize_sale(c,'cash','-1')

def test_credit_overpayment_rejected(tmp_path):
 d=db(tmp_path); p=product(d); cust=CustomerService(d).create('Ali'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1')
 with pytest.raises(ValueError): s.finalize_sale(c,'credit','11',cust.id)

def test_stock_changes_do_not_affect_finalize(tmp_path):
 d=db(tmp_path); p=product(d,stock='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'6'); InventoryService=__import__('app.services.inventory_service',fromlist=['InventoryService']).InventoryService; InventoryService(d).adjust_stock(p.id,'-5');
 iid,_=s.finalize_sale(c,'cash','60'); assert iid is not None
 assert ProductService(d).get_product(p.id).current_stock==Decimal('5.000000')

def test_price_reread_at_finalize(tmp_path):
 d=db(tmp_path); p=product(d,price='10'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'2'); ProductService(d).update_product(p.id,name='Wire',selling_price='12',unit='meter',minimum_stock='0'); iid,_=s.finalize_sale(c,'cash','24'); assert s.get_invoice(iid).items[0].unit_price==Decimal('12.00')

def test_customer_search(tmp_path):
 d=db(tmp_path); cs=CustomerService(d); cs.create('Ali'); cs.create('Bilal'); assert [c.name for c in cs.list('ali')]==['Ali']

def test_customer_payment_creates_negative_ledger_entry(tmp_path):
 d=db(tmp_path); cs=CustomerService(d); c=cs.create('Ali'); s=SalesService(d,settings_service=SettingsService(d)); cart=Cart(); p=product(d); cart.add(p,'2'); s.finalize_sale(cart,'credit','0',c.id); cs.record_payment(c.id,'5'); hist=cs.history(c.id); assert hist[-1].transaction_type=='payment' and hist[-1].amount==Decimal('-5.00')

def test_invoice_history_lists_sales(tmp_path):
 d=db(tmp_path); p=product(d); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1'); s.finalize_sale(c,'cash','10'); assert len(s.list_invoices())==1 and s.list_invoices()[0].invoice_number

def test_decimal_money_over_two_places_rejected_in_cart(tmp_path):
 d=db(tmp_path); p=product(d); c=Cart()
 with pytest.raises(ValueError): c.add(p,'1','1.001')

def test_bill_discount_cannot_make_negative_total(tmp_path):
 d=db(tmp_path); p=product(d); c=Cart(); c.add(p,'1')
 with pytest.raises(ValueError): c.set_bill_discount('10.01')

def test_credit_full_payment_has_zero_outstanding(tmp_path):
 d=db(tmp_path); p=product(d); cust=CustomerService(d).create('Ali'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1'); s.finalize_sale(c,'credit','10',cust.id); assert CustomerService(d).outstanding(cust.id)==Decimal('0.00')

def test_invoice_sequence_prefix_persists(tmp_path):
 d=db(tmp_path); ss=SettingsService(d); x=ss.get_settings(); ss.save_settings(x.__class__(**{**x.__dict__,'invoice_prefix':'MUG'})); assert SettingsService(d).get_settings().invoice_prefix=='MUG'

def test_sequence_collision_retries_once(tmp_path):
 d=db(tmp_path); p=product(d,stock='3'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'1'); _,first=s.finalize_sale(c,'cash','10');
 with d.SessionLocal.begin() as sess:
  from app.database.models.settings import Setting
  sess.get(Setting,'invoice_sequence').value='0'
 c2=Cart(); c2.add(p,'1'); _,second=s.finalize_sale(c2,'cash','10'); assert second.endswith('-0002')


def test_customer_balance_adjustments_are_ledger_entries(tmp_path):
 d=db(tmp_path); p=product(d); cs=CustomerService(d); cust=cs.create('Adjust Me'); s=SalesService(d,settings_service=SettingsService(d)); c=Cart(); c.add(p,'5'); s.finalize_sale(c,'credit','0',customer_id=cust.id); assert cs.outstanding(cust.id)==Decimal('50.00')
 assert cs.adjust_balance(cust.id,'20','manual increase') == Decimal('70.00')
 assert cs.adjust_balance(cust.id,'-15','manual correction') == Decimal('55.00')
 assert cs.outstanding(cust.id)==Decimal('55.00')
 with d.SessionLocal() as session:
  entries=session.query(CustomerLedgerEntry).filter_by(customer_id=cust.id).all()
  assert [e.transaction_type for e in entries].count('balance_adjustment') == 2
  assert any(e.amount == Decimal('20.00') and e.note == 'manual increase' for e in entries)
  assert any(e.amount == Decimal('-15.00') and e.note == 'manual correction' for e in entries)

def test_customer_balance_adjustment_cannot_create_negative_outstanding(tmp_path):
 d=db(tmp_path); cs=CustomerService(d); cust=cs.create('Adjust Me')
 with pytest.raises(ValueError): cs.adjust_balance(cust.id,'-1','bad')
 assert cs.outstanding(cust.id)==Decimal('0.00')
