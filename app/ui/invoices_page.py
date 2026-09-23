from __future__ import annotations
from decimal import Decimal
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFileDialog,QDialog,QDialogButtonBox,QFrame,QHBoxLayout,QHeaderView,QLabel,QLineEdit,QMessageBox,QPushButton,QTabWidget,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget
from app.services.invoice_output_service import InvoiceOutputError,InvoiceOutputService
from app.services.sales_service import SalesService
from app.ui.responsive import size_to_screen
from app.ui.widgets import left_align_items, reserve_visible_rows
from app.utils.formatting import invoice_display_number,money,quantity

def format_invoice_datetime(value):
    return value.strftime("%d %B %Y, %I:%M %p").replace(" 0", " ", 1)

# -- Dark, "luxury minimalist" dashboard theme for InvoiceDetailDialog --
# One shared palette so the table, summary card and buttons all read as one
# surface instead of separately-styled widgets.
_DIALOG_BG = "#0f172a"
_SURFACE = "#1e293b"
_SURFACE_ALT = "#162032"
_BORDER = "#334155"
_TEXT = "#f8fafc"
_TEXT_MUTED = "#94a3b8"
_ACCENT = "#38bdf8"
_ACCENT_GREEN = "#22c55e"
_DANGER = "#f87171"

# Search-results row-selection highlight for the Cash/Credit invoice tables:
# yellow with black text, matching the POS/Products search-match highlight so a
# selected search hit reads the same on every screen instead of the app-wide
# blue. Scoped to each InvoiceTable's own QTableWidget instance via
# setStyleSheet (no object name needed), so no other table is affected and the
# theme still supplies gridlines, padding and alternating rows.
_SEARCH_SELECT_QSS = (
    "QTableWidget { selection-background-color: #FFE066; selection-color: #000000; }"
    "QTableWidget::item:selected { background: #FFE066; color: #000000; }"
)

_DIALOG_QSS = (
    f"QDialog {{ background: {_DIALOG_BG}; color: {_TEXT}; }}"
    f"QLabel {{ color: {_TEXT}; }}"
    f"QLabel#muted {{ color: {_TEXT_MUTED}; font-size: 11px; }}"
    f"QTableWidget {{ background: {_SURFACE}; color: {_TEXT}; gridline-color: {_BORDER};"
    f"  border: 1px solid {_BORDER}; border-radius: 8px; selection-background-color: #334155; }}"
    f"QTableWidget::item {{ border: none; padding: 4px; }}"
    f"QTableWidget::item:hover {{ background-color: #1e293b; color: {_TEXT}; }}"
    f"QTableWidget::item:selected {{ background-color: #263449; color: {_TEXT}; }}"
    f"QHeaderView::section {{ background: {_SURFACE_ALT}; color: {_TEXT_MUTED}; border: none;"
    f"  border-bottom: 1px solid {_BORDER}; padding: 6px; font-weight: 600; }}"
)

# Pill-shaped action buttons. Primary = the main "produce output" actions;
# Secondary = neutral/structural (Edit, Close); Danger = destructive
# (Delete). Distinct hover/pressed states on every variant so the row of
# five-plus buttons still reads clearly against the dark surface.
_PRIMARY_BUTTON_QSS=(
    "QPushButton {"
    f" padding: 6px 16px; min-height: 24px; border-radius: 13px;"
    f" border: 1px solid {_ACCENT}; background: rgba(56,189,248,0.12); color: {_ACCENT};"
    " font-weight: 600;"
    "}"
    f"QPushButton:hover {{ background: rgba(56,189,248,0.22); color: {_TEXT}; }}"
    f"QPushButton:pressed {{ background: rgba(56,189,248,0.32); }}"
)
_SECONDARY_BUTTON_QSS=(
    "QPushButton {"
    f" padding: 6px 16px; min-height: 24px; border-radius: 13px;"
    f" border: 1px solid {_BORDER}; background: rgba(255,255,255,0.04); color: {_TEXT};"
    " font-weight: 600;"
    "}"
    "QPushButton:hover { background: rgba(255,255,255,0.09); }"
    "QPushButton:pressed { background: rgba(255,255,255,0.14); }"
)
_ACTION_BUTTON_QSS=_PRIMARY_BUTTON_QSS
_DELETE_BUTTON_QSS=(
    "QPushButton#deleteInvoiceButton {"
    f" padding: 6px 16px; min-height: 24px; border-radius: 13px; font-weight: 600;"
    f" color: {_DANGER}; border: 1px solid {_DANGER}; background: rgba(248,113,113,0.10);"
    "}"
    "QPushButton#deleteInvoiceButton:hover { background: rgba(248,113,113,0.20); color: #fecaca; }"
    "QPushButton#deleteInvoiceButton:pressed { background: rgba(248,113,113,0.30); }"
)

class InvoiceDetailDialog(QDialog):
    def __init__(self,invoice,settings,output_service,parent=None,edit_callback=None,sales_service=None,delete_callback=None):
        super().__init__(parent)
        self.invoice=invoice
        self.settings=settings
        self.output_service=output_service
        self.edit_callback=edit_callback
        self.sales_service=sales_service
        self.delete_callback=delete_callback
        self.setWindowTitle(f"{invoice_display_number(invoice)} ({invoice.invoice_number})")
        size_to_screen(self,0.6,0.78,min_width=760,min_height=640)
        self.setStyleSheet(_DIALOG_QSS)

        # -- Minimalist dark header: the clean "Invoice #<n>" label is the
        # first thing shown, large and bold, right at the top of the preview
        # modal — followed by the shop name on the same line and the full
        # stored reference (e.g. "INV-0001") underneath in smaller, muted
        # text for anyone who needs the padded/prefixed form.
        l=QVBoxLayout(self)
        l.setContentsMargins(18,16,18,16)
        l.setSpacing(10)
        header_row=QHBoxLayout()
        number_label=QLabel(invoice_display_number(invoice))
        number_label.setStyleSheet(f"font-size:22px;font-weight:700;color:{_TEXT};")
        header_row.addWidget(number_label)
        header_row.addStretch()
        shop_label=QLabel(settings.shop_name)
        shop_label.setStyleSheet(f"font-weight:600;color:{_TEXT_MUTED};")
        header_row.addWidget(shop_label)
        l.addLayout(header_row)

        reference_label=QLabel(invoice.invoice_number)
        reference_label.setObjectName("muted")
        l.addWidget(reference_label)

        payment_text="CREDIT (UDHAAR)" if invoice.payment_type=="credit" else "CASH"
        meta_text=f"{format_invoice_datetime(invoice.created_at)}  •  {payment_text}"
        if invoice.customer_name:
            meta_text+=f"  •  {invoice.customer_name}"
        meta_label=QLabel(meta_text)
        meta_label.setObjectName("muted")
        l.addWidget(meta_label)

        if getattr(invoice,"voided",False):
            voided_label=QLabel("VOIDED — this invoice was edited and replaced by a newer one")
            voided_label.setObjectName("voidedBanner")
            voided_label.setStyleSheet(f"color:{_DANGER};font-weight:bold;")
            l.addWidget(voided_label)

        table=QTableWidget(0,7)
        table.setHorizontalHeaderLabels(["S.No","Product","Unit","Quantity","Unit Price","Item Discount","Line Total"])
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Table polish: no vertical index column, borderless rows with a
        # subtle hover/selection highlight (handled by _DIALOG_QSS above),
        # and no focus rectangle around individual cells.
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(False)
        l.addWidget(table,1)
        for idx,item in enumerate(invoice.items,start=1):
            r=table.rowCount();table.insertRow(r)
            vals=[str(idx),item.product_name,item.unit,quantity(item.quantity),money(item.unit_price),money(item.item_discount),money(item.line_total)]
            for c,v in enumerate(vals):table.setItem(r,c,QTableWidgetItem(v))
        left_align_items(table)
        table.resizeColumnsToContents()
        # Every column sizes to its own content first (so numeric columns
        # stay compact), then the Product column (index 1) is switched to
        # Stretch so it — not empty space to the right of the table — absorbs
        # whatever width the dialog has left over. Order matters: Stretch
        # must be applied AFTER resizeColumnsToContents(), or Qt has nothing
        # sensible to measure the other columns against.
        header=table.horizontalHeader()
        for col in range(table.columnCount()):
            header.setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)

        # Reserve enough vertical space for ~10 line items so a typical
        # invoice's products are all visible at once without cramped
        # scrolling; longer lists still scroll within the table, which keeps
        # its stretch factor (addWidget(table,1)) and grows with the dialog as
        # size_to_screen sizes it to the screen. Pinning a fixed row height
        # makes the reservation exact rather than dependent on the platform's
        # default section size.
        _INVOICE_ITEM_ROWS = 10
        _INVOICE_ROW_HEIGHT = 32
        table.verticalHeader().setDefaultSectionSize(_INVOICE_ROW_HEIGHT)
        reserve_visible_rows(table, _INVOICE_ITEM_ROWS, row_height=_INVOICE_ROW_HEIGHT)

        # -- Modern metrics-card summary: a translucent panel instead of the
        # old flat bordered strip, with Grand Total pulled out in a bold
        # accent color so it's the one number that draws the eye.
        gross=sum(((Decimal(i.quantity)*Decimal(i.unit_price)).quantize(Decimal('0.01')) for i in invoice.items),Decimal('0.00'))
        item_disc=sum((Decimal(i.item_discount) for i in invoice.items),Decimal('0.00'))
        bill_disc=Decimal(invoice.discount_total)-item_disc
        summary=QFrame()
        summary.setObjectName("invoiceSummaryStrip")
        summary.setStyleSheet(
            "QFrame#invoiceSummaryStrip {"
            " background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);"
            " border-radius: 8px; padding: 12px;"
            "}"
        )
        summary_layout=QHBoxLayout(summary)
        summary_layout.setContentsMargins(12,10,12,10)
        summary_layout.setSpacing(18)

        def _metric(label,value_text,accent=None):
            box=QVBoxLayout(); box.setSpacing(2)
            lab=QLabel(label); lab.setStyleSheet(f"color:{_TEXT_MUTED};font-size:10px;letter-spacing:0.5px;")
            val=QLabel(value_text)
            val.setStyleSheet(f"color:{accent or _TEXT};font-size:14px;font-weight:{'700' if accent else '600'};")
            box.addWidget(lab); box.addWidget(val)
            wrap=QWidget(); wrap.setLayout(box)
            return wrap

        for label,v in [("SUBTOTAL",gross),("DISCOUNT",invoice.discount_total),("BILL DISCOUNT",bill_disc)]:
            summary_layout.addWidget(_metric(label,money(v)))
        summary_layout.addStretch()
        # Grand Total gets the accent color (green if fully paid, blue
        # otherwise) so it reads as the headline metric of the card.
        grand_accent=_ACCENT_GREEN if Decimal(invoice.remaining_amount)<=0 else _ACCENT
        summary_layout.addWidget(_metric("GRAND TOTAL",money(invoice.grand_total),grand_accent))
        summary_layout.addWidget(_metric("PAID",money(invoice.paid_amount)))
        summary_layout.addWidget(_metric("REMAINING",money(invoice.remaining_amount)))
        l.addWidget(summary)

        # -- Output actions: compact, modern buttons (see _ACTION_BUTTON_QSS).
        actions=QHBoxLayout()
        actions.setSpacing(6)
        actions.setContentsMargins(0,4,0,0)
        for label,fmt,printer in [("Generate A4 PDF","A4",False),("Print A4","A4",True),("Generate 180mm PDF","180mm",False),("Print 180mm","180mm",True)]:
            b=QPushButton(label)
            b.setStyleSheet(_ACTION_BUTTON_QSS)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False,f=fmt,p=printer:self._output(f,p))
            actions.addWidget(b)
        actions.addStretch()
        l.addLayout(actions)

        # -- Edit / Delete row: destructive/structural actions kept apart from
        # the output actions above them.
        edit_row=QHBoxLayout()
        edit_row.setSpacing(6)
        if edit_callback is not None:
            self.edit_button=QPushButton("Edit Invoice")
            self.edit_button.setObjectName("editInvoiceButton")
            self.edit_button.setStyleSheet(_SECONDARY_BUTTON_QSS)
            self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_button.setEnabled(not getattr(invoice,"voided",False))
            self.edit_button.setToolTip("Already voided — this invoice was already replaced" if getattr(invoice,"voided",False) else "Void this invoice and reload its items for editing")
            self.edit_button.clicked.connect(self._edit_invoice)
            edit_row.addWidget(self.edit_button)
        # Delete Invoice: permanently removes the invoice (and its
        # customer-ledger impact) via SalesService.delete_invoice, guarded by
        # a confirmation dialog since this cannot be undone. Only offered when
        # a sales_service was actually supplied.
        self.delete_button=None
        if self.sales_service is not None:
            self.delete_button=QPushButton("Delete Invoice")
            self.delete_button.setObjectName("deleteInvoiceButton")
            self.delete_button.setStyleSheet(_DELETE_BUTTON_QSS)
            self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.delete_button.setToolTip("Permanently delete this invoice — this cannot be undone")
            self.delete_button.clicked.connect(self._delete_invoice)
            edit_row.addWidget(self.delete_button)
        edit_row.addStretch()
        if edit_callback is not None or self.delete_button is not None:
            l.addLayout(edit_row)

        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.setStyleSheet(_SECONDARY_BUTTON_QSS)
        close_btn=close.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close.rejected.connect(self.reject)
        l.addWidget(close)

    def _edit_invoice(self):
        confirm=QMessageBox.question(
            self,"Edit Invoice",
            f"This will void invoice {self.invoice.invoice_number} and reload its items into "
            "the Billing screen so you can update and re-finalize the sale. This cannot be undone.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
        )
        if confirm!=QMessageBox.StandardButton.Yes:return
        self.edit_callback(self.invoice)
        self.accept()

    def _delete_invoice(self):
        confirm=QMessageBox.question(
            self,"Delete Invoice",
            f"Permanently delete invoice {self.invoice.invoice_number}?\n\n"
            "This removes the invoice and its items entirely, and reverses any "
            "customer-ledger (Udhaar) balance it created. This cannot be undone.",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm!=QMessageBox.StandardButton.Yes:return
        try:
            self.sales_service.delete_invoice(self.invoice.id)
        except ValueError as e:
            QMessageBox.warning(self,"Cannot delete invoice",str(e))
            return
        except Exception as e:
            QMessageBox.critical(self,"Delete failed",str(e))
            return
        if self.delete_callback is not None:
            self.delete_callback()
        QMessageBox.information(self,"Invoice deleted",f"Invoice {self.invoice.invoice_number} has been deleted.")
        self.accept()

    def _output(self,fmt,printer):
        try:
            if printer:
                # Silent print pipeline (a QPrinter bound to the OS default
                # printer + PyMuPDF rasterization) — the same one
                # app/ui/pos_page.py uses, never a web browser / PDF viewer /
                # OS "open/print file" shell. generate_and_print returns True
                # once the job is sent, or raises on failure (e.g. no default
                # printer configured); there is no dialog to cancel.
                confirmed=self.output_service.generate_and_print(self.invoice,self.settings,fmt)
                if confirmed:QMessageBox.information(self,"Print started","Invoice sent to the printer.")
                return
            suggested=self.output_service.default_path(self.invoice,fmt); path,_=QFileDialog.getSaveFileName(self,f"Save {fmt} Invoice PDF",str(suggested),"PDF files (*.pdf)")
            if not path:return
            if not path.lower().endswith('.pdf'):path+='.pdf'
            self.output_service.generate(self.invoice,self.settings,path,fmt); QMessageBox.information(self,"PDF generated",f"Invoice PDF saved to:\n{path}")
        except (InvoiceOutputError,OSError,ValueError) as e:QMessageBox.warning(self,"Invoice output failed",str(e))

class InvoiceTable(QWidget):
    def __init__(self,service,settings,output,parent=None):
        super().__init__(parent); self.service=service; self.settings=settings; self.output=output; self._credit_cache=None; self.edit_callback=None; l=QVBoxLayout(self); top=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText("Search invoice number or date (e.g. 21 February)..."); self.open=QPushButton("Open Selected"); self.edit=QPushButton("Edit Invoice"); self.edit.setObjectName("editInvoiceButton"); top.addWidget(self.search,1); top.addWidget(self.open); top.addWidget(self.edit); l.addLayout(top); self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(["Invoice","Date/Time","Customer","Payment","Total","Paid","Remaining"]); self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter); self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.verticalHeader().setVisible(False); self.table.setAlternatingRowColors(True); self.table.setStyleSheet(_SEARCH_SELECT_QSS); l.addWidget(self.table); self.search.textChanged.connect(self.refresh); self.open.clicked.connect(self.open_selected); self.edit.clicked.connect(self.edit_selected); self.table.doubleClicked.connect(lambda _:self.open_selected())
    def refresh(self):
        term=self.search.text().strip()
        invoices=self.service.list_invoices(term, self.payment_type)
        self.table.setRowCount(len(invoices))
        for r,i in enumerate(invoices):
            # Matches the Dashboard's Recent Sales display so a blank cash
            # customer never reads differently on two screens (Known Issue #7).
            customer=i.customer_name or "Walk-in"; pay="CREDIT (UDHAAR)" if i.payment_type=="credit" else "CASH"
            if getattr(i,"voided",False): pay += " (VOIDED)"
            vals=[invoice_display_number(i),format_invoice_datetime(i.created_at),customer,pay,money(i.grand_total),money(i.paid_amount),money(i.remaining_amount)]
            x=QTableWidgetItem(vals[0]); x.setData(Qt.ItemDataRole.UserRole,i.id); x.setToolTip(i.invoice_number)
            font=x.font(); font.setBold(True); x.setFont(font)
            self.table.setItem(r,0,x)
            for c,v in enumerate(vals[1:],1):
                cell=QTableWidgetItem(v)
                if c==2:cell.setToolTip(customer)
                if getattr(i,"voided",False):cell.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(r,c,cell)
        left_align_items(self.table)
        self.table.resizeColumnsToContents()
        # Customer is the one unpredictable-length field here; letting it
        # flex (with the full name always in its tooltip) keeps invoice
        # number/date/payment/totals from ever being squeezed (Section 22).
        header=self.table.horizontalHeader()
        for col in (0,1,3,4,5,6):
            header.setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)

    def _selected_invoice(self):
        r=self.table.currentRow()
        if r<0:return None
        iid=self.table.item(r,0).data(Qt.ItemDataRole.UserRole)
        return self.service.get_invoice(iid)

    def open_selected(self):
        invoice=self._selected_invoice()
        if invoice:InvoiceDetailDialog(invoice,self.settings.get_settings(),self.output,self,edit_callback=self.edit_callback,sales_service=self.service,delete_callback=self.refresh).exec_()

    def edit_selected(self):
        invoice=self._selected_invoice()
        if invoice is None:return
        if getattr(invoice,"voided",False):
            QMessageBox.information(self,"Already voided","This invoice was already edited/voided and replaced by a newer one.")
            return
        if self.edit_callback is None:return
        confirm=QMessageBox.question(
            self,"Edit Invoice",
            f"This will void invoice {invoice.invoice_number} and reload its items into "
            "the Billing screen so you can update and re-finalize the sale. This cannot be undone.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
        )
        if confirm!=QMessageBox.StandardButton.Yes:return
        self.edit_callback(invoice)

class InvoicesPage(QWidget):
    def __init__(self,service,settings_service=None,output_service=None,parent=None):
        super().__init__(parent); self.service=service; self.settings_service=settings_service; self.output_service=output_service or InvoiceOutputService(); self.edit_invoice=None; l=QVBoxLayout(self); self.tabs=QTabWidget(); self.cash=InvoiceTable(service,settings_service,self.output_service); self.cash.payment_type='cash'; self.credit=InvoiceTable(service,settings_service,self.output_service); self.credit.payment_type='credit'; self.credit.search.setPlaceholderText('Search customer name or date...'); self.tabs.addTab(self.cash,'Cash Invoices'); self.tabs.addTab(self.credit,'Credit (Udhaar) Invoices'); l.addWidget(self.tabs,1); self.refresh()
    def refresh(self):
        # edit_invoice may be wired up (by MainWindow) after __init__ runs,
        # so each InvoiceTable's callback is synced here rather than only once.
        self.cash.edit_callback=self.edit_invoice; self.credit.edit_callback=self.edit_invoice
        self.cash.refresh(); self.credit._credit_cache=None; self.credit.refresh()
    def open_invoice_by_id(self,invoice_id):
        invoice=self.service.get_invoice(invoice_id)
        if invoice:InvoiceDetailDialog(invoice,self.settings_service.get_settings(),self.output_service,self,edit_callback=self.edit_invoice,sales_service=self.service,delete_callback=self.refresh).exec_()
