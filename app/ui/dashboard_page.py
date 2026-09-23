from __future__ import annotations
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QFrame,QGraphicsDropShadowEffect,QGridLayout,QHBoxLayout,QHeaderView,QLabel,QPushButton,QSizePolicy,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget
from app.ui import icons
from app.ui.widgets import enable_smooth_scrolling, left_align_items
from app.utils.formatting import money

CARD_SPECS = [("Today's Sales", "billing"), ("Today's Invoices", "invoices"), ("Today's Cash", "cash"), ("Customer Udhaar", "customer_udhaar")]

def short_invoice_reference(invoice_number: str) -> str:
    """A compact, still-unique reference for narrow table columns.

    The sequence suffix after the last '-' in e.g. INV-20260823-0123 is a
    single global counter (see SalesService._allocate_invoice_number) that
    never repeats, so "#0123" is unambiguous on its own — unlike an
    arbitrary character-count trim, which could cut a date digit in half.
    The full number is always still available via tooltip and is what is
    actually stored/opened (only the *display* text is shortened).
    """
    if not invoice_number:
        return ""
    tail = invoice_number.rsplit("-", 1)[-1]
    return f"#{tail}" if tail else invoice_number

class SummaryCard(QFrame):
    def __init__(self,title,icon_key=None,parent=None):
        super().__init__(parent); self.setObjectName("dashboardCard")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(15, 23, 42, 35))
        self.setGraphicsEffect(shadow)
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        l=QVBoxLayout(self); l.setContentsMargins(16,14,16,14)
        head=QHBoxLayout(); head.setSpacing(6)
        if icon_key:
            icon_label=QLabel(); icon_label.setObjectName("cardIcon"); icon_label.setPixmap(icons.nav_icon(icon_key,"#7c8aa3").pixmap(QSize(15,15))); head.addWidget(icon_label)
        t=QLabel(title); t.setObjectName("muted"); head.addWidget(t); head.addStretch()
        self.value=QLabel("0"); self.value.setObjectName("dashboardValue")
        l.addLayout(head); l.addWidget(self.value)
    def set_value(self,v): self.value.setText(v)

class DashboardPage(QWidget):
    def __init__(self,service,settings_service=None,open_invoice=None,parent=None):
        super().__init__(parent); self.service=service; self.settings_service=settings_service; self.open_invoice=open_invoice
        layout=QVBoxLayout(self); header=QHBoxLayout(); header.addStretch(); b=QPushButton("Refresh"); header.addWidget(b); layout.addLayout(header); self.refresh_button=b
        cards=QGridLayout(); cards.setSpacing(12); self.cards=[]
        for i,(t,icon_key) in enumerate(CARD_SPECS):
            c=SummaryCard(t,icon_key); self.cards.append(c); cards.addWidget(c,0,i)
        layout.addLayout(cards)
        sections=QHBoxLayout(); sections.setSpacing(12)
        left=QFrame(); left.setObjectName("dashboardCard"); ll=QVBoxLayout(left); ll.setContentsMargins(14,12,14,14); st=QLabel("Customer Udhaar"); st.setObjectName("sectionTitle"); ll.addWidget(st)
        self.udhaar_table=QTableWidget(0,2); self.udhaar_table.setHorizontalHeaderLabels(["Metric","Amount / Count"]); self.udhaar_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter); self.udhaar_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.udhaar_table.verticalHeader().setVisible(False)
        enable_smooth_scrolling(self.udhaar_table)
        ll.addWidget(self.udhaar_table,1); sections.addWidget(left,2)
        right=QFrame(); right.setObjectName("dashboardCard"); rl=QVBoxLayout(right); rl.setContentsMargins(14,12,14,14); rt=QLabel("Recent Sales"); rt.setObjectName("sectionTitle"); rl.addWidget(rt)
        self.recent_sales_table=QTableWidget(0,5); self.recent_sales_table.setHorizontalHeaderLabels(["Invoice","Date/Time","Customer","Payment","Total"]); self.recent_sales_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        self.recent_sales_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.recent_sales_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_sales_table.verticalHeader().setVisible(False)
        enable_smooth_scrolling(self.recent_sales_table)
        self.recent_sales_table.setToolTip("Double-click a row to open the full invoice")
        rl.addWidget(self.recent_sales_table,1); sections.addWidget(right,3); layout.addLayout(sections,1)
        b.clicked.connect(self.refresh); self.recent_sales_table.doubleClicked.connect(self._open_recent); self.refresh()
    def _currency(self,v):
        settings=self.settings_service.get_settings() if self.settings_service else None; return f"{settings.currency_symbol if settings else 'Rs.'} {money(v)}"
    def refresh(self):
        s=self.service.summary(); self.cards[0].set_value(self._currency(s.today_sales)); self.cards[1].set_value(str(s.today_invoices)); self.cards[2].set_value(self._currency(s.today_cash_received)); self.cards[3].set_value(self._currency(s.total_outstanding))
        self.udhaar_table.setRowCount(2)
        # "Customers owing" is a shorter, equally clear label than "Customers
        # with balance" so it no longer needs to elide in a narrow card
        # column (Known Issue #5).
        self.udhaar_table.setItem(0,0,QTableWidgetItem("Outstanding credit")); self.udhaar_table.setItem(0,1,QTableWidgetItem(self._currency(s.total_outstanding)))
        self.udhaar_table.setItem(1,0,QTableWidgetItem("Customers owing")); self.udhaar_table.setItem(1,1,QTableWidgetItem(str(s.customers_with_outstanding)))
        left_align_items(self.udhaar_table)
        self.udhaar_table.resizeColumnsToContents()
        self.udhaar_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)

        recent=self.service.recent_sales(10); self.recent_sales_table.setRowCount(len(recent))
        for row,sale in enumerate(recent):
            item=QTableWidgetItem(short_invoice_reference(sale.invoice_number))
            item.setData(Qt.ItemDataRole.UserRole,sale.invoice_id)
            item.setToolTip(sale.invoice_number)
            self.recent_sales_table.setItem(row,0,item)
            vals=[sale.created_at.strftime('%d %B %Y, %I:%M %p').replace(' 0', ' ', 1),sale.customer_name,sale.payment_type.title(),self._currency(sale.grand_total)]
            for c,v in enumerate(vals,1):
                cell=QTableWidgetItem(v)
                if c==2:cell.setToolTip(sale.customer_name)
                self.recent_sales_table.setItem(row,c,cell)
        left_align_items(self.recent_sales_table)
        self.recent_sales_table.resizeColumnsToContents()
        # Invoice/Date/Payment/Total are compact, fixed-shape fields — they
        # must always show in full and never get squeezed by layout space.
        # Customer is the one free-length field, so it is the column allowed
        # to flex/elide (with the full name always available via tooltip)
        # rather than a short, structured column losing characters instead.
        header=self.recent_sales_table.horizontalHeader()
        for col in (0,1,3,4):
            header.setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
    def _open_recent(self,_):
        if self.open_invoice is None:return
        row=self.recent_sales_table.currentRow()
        if row>=0 and self.recent_sales_table.item(row,0): self.open_invoice(self.recent_sales_table.item(row,0).data(Qt.ItemDataRole.UserRole))
