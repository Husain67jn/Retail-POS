from __future__ import annotations
from decimal import Decimal

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.responsive import size_to_screen
from app.ui.widgets import left_align_items
from app.utils.formatting import money

try:
    # Raised by InvoiceOutputService when PDF generation/printing fails --
    # caught specifically so "Print Duplicate Receipt" can show a clean
    # message instead of a raw traceback. Printing still degrades to a
    # generic Exception handler if this import ever moves.
    from app.services.invoice_output_service import InvoiceOutputError
except ImportError:  # pragma: no cover - defensive only
    class InvoiceOutputError(RuntimeError):
        pass


def _format_quantity(value) -> str:
    """1.500000 -> '1.5', 2.000000 -> '2' -- mirrors
    InvoiceOutputService.quantity so the on-screen breakdown matches what
    gets printed, without importing a static method off that service just
    for formatting."""
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class _PromptDialog(QDialog):
    """A small, app-styled replacement for QInputDialog.

    QInputDialog renders as a bare OS dialog that doesn't pick up the
    application's own styling, which reads as inconsistent next to every
    other dialog in the app (Section 21). This gives Customer Udhaar's
    prompts the same title/fields/primary-action/cancel shape as the rest
    of the app, without changing what data they collect or how it's used.
    Accepts one or more (label, initial-value) fields so a single related
    action (e.g. an adjustment amount + its reason) is one compact dialog
    rather than a chain of separate popups.
    """
    def __init__(self, title, subtitle, fields, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        heading = QLabel(title); heading.setObjectName("dialogTitle"); layout.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle); sub.setObjectName("dialogSubtitle"); sub.setWordWrap(True); layout.addWidget(sub)
        form = QFormLayout()
        self._fields = []
        for field_label, initial in fields:
            field = QLineEdit(initial)
            form.addRow(field_label, field)
            self._fields.append(field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self, index: int = 0) -> str:
        return self._fields[index].text().strip()


class InvoiceDetailDialog(QDialog):
    """Level 3: exact item-level breakdown of one credit invoice.

    Pure display + a "Print Duplicate Receipt" action that reuses the
    existing InvoiceOutputService thermal pipeline -- this dialog never
    mutates the invoice, the ledger, or any balance.
    """
    def __init__(self, detail, sales_service, output_service, settings_service, parent=None):
        super().__init__(parent)
        self.detail = detail
        self.sales_service = sales_service
        self.output_service = output_service
        self.settings_service = settings_service

        self.setWindowTitle(f"Invoice {detail['invoice_number']}")
        size_to_screen(self, 0.55, 0.6, min_width=560, min_height=420)
        l = QVBoxLayout(self)

        heading = QLabel(f"Invoice {detail['invoice_number']}")
        heading.setObjectName("dialogTitle")
        l.addWidget(heading)

        customer_name = detail.get("customer_name") or "—"
        meta = QLabel(
            f"Customer: {customer_name}    ·    "
            f"Date: {detail['created_at']:%d %B %Y, %I:%M %p}"
        )
        meta.setObjectName("dialogSubtitle")
        l.addWidget(meta)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Item Description", "Quantity", "Unit Price", "Total"])
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setRowCount(len(detail["items"]))
        for r, item in enumerate(detail["items"]):
            values = (
                item["product_name"],
                _format_quantity(item["quantity"]),
                money(item["unit_price"]),
                money(item["line_total"]),
            )
            for col, value in enumerate(values):
                table.setItem(r, col, QTableWidgetItem(value))
        left_align_items(table)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        l.addWidget(table, 1)

        summary_rows = [
            ("Subtotal", detail["subtotal"]),
            ("Discounts", detail["discount_total"]),
            ("Grand Total", detail["grand_total"]),
            ("Paid", detail["paid_amount"]),
            ("Balance", detail["remaining_amount"]),
        ]
        summary_form = QFormLayout()
        for label_text, value in summary_rows:
            value_label = QLabel(money(value))
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            if label_text in ("Grand Total", "Balance"):
                value_label.setObjectName("dialogTitle")
            summary_form.addRow(label_text, value_label)
        l.addLayout(summary_form)

        button_row = QHBoxLayout()
        print_btn = QPushButton("Print Duplicate Receipt")
        print_btn.setObjectName("primaryButton")
        print_btn.clicked.connect(self._print_duplicate)
        button_row.addWidget(print_btn)
        button_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_row.addWidget(close_btn)
        l.addLayout(button_row)

    def _print_duplicate(self):
        if not (self.sales_service and self.output_service and self.settings_service):
            QMessageBox.warning(self, "Print", "Printing is not available right now.")
            return
        try:
            invoice = self.sales_service.get_invoice(self.detail["id"])
            if invoice is None:
                QMessageBox.warning(self, "Print", "This invoice no longer exists.")
                return
            settings = self.settings_service.get_settings()
            self.output_service.generate_and_print(invoice, settings, format_name="80mm")
        except InvoiceOutputError as e:
            QMessageBox.warning(self, "Print", str(e))
        except Exception as e:
            QMessageBox.warning(self, "Print", f"Could not print the duplicate receipt: {e}")


class _CustomerInvoiceHistoryView(QWidget):
    """Level 2: every credit invoice for one customer."""
    back_requested = pyqtSignal()

    def __init__(self, sales_service, output_service, settings_service, parent=None):
        super().__init__(parent)
        self.sales_service = sales_service
        self.output_service = output_service
        self.settings_service = settings_service
        self.customer_id = None
        self._invoices_by_id = {}

        l = QVBoxLayout(self)
        top = QHBoxLayout()
        back = QPushButton("← Back to Customers")
        back.clicked.connect(self.back_requested.emit)
        top.addWidget(back)
        top.addStretch(1)
        l.addLayout(top)

        self.heading = QLabel("")
        self.heading.setObjectName("dialogTitle")
        l.addWidget(self.heading)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        view_items = QPushButton("View Items")
        view_items.clicked.connect(self._open_selected)
        action_row.addWidget(view_items)
        l.addLayout(action_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Invoice #", "Date & Time", "Total Bill Amount", "Paid Amount", "Remaining Balance"]
        )
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemDoubleClicked.connect(lambda _: self._open_selected())
        l.addWidget(self.table, 1)

    def load_customer(self, customer_id, customer_name):
        self.customer_id = customer_id
        self.heading.setText(f"Invoice History · {customer_name}")
        self.refresh()

    def refresh(self):
        if self.customer_id is None:
            return
        invoices = self.sales_service.list_invoices_for_customer(self.customer_id)
        self._invoices_by_id = {inv["id"]: inv for inv in invoices}
        self.table.setRowCount(len(invoices))
        for r, inv in enumerate(invoices):
            item = QTableWidgetItem(inv["invoice_number"])
            item.setData(Qt.ItemDataRole.UserRole, inv["id"])
            self.table.setItem(r, 0, item)
            self.table.setItem(r, 1, QTableWidgetItem(f"{inv['created_at']:%d %B %Y, %I:%M %p}"))
            self.table.setItem(r, 2, QTableWidgetItem(money(inv["grand_total"])))
            self.table.setItem(r, 3, QTableWidgetItem(money(inv["paid_amount"])))
            self.table.setItem(r, 4, QTableWidgetItem(money(inv["remaining_amount"])))
        left_align_items(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

    def _selected_invoice_id(self):
        r = self.table.currentRow()
        return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def _open_selected(self):
        invoice_id = self._selected_invoice_id()
        if invoice_id is None:
            return
        detail = self.sales_service.invoice_detail(invoice_id)
        if detail is None:
            QMessageBox.warning(self, "Invoice", "This invoice no longer exists.")
            self.refresh()
            return
        dialog = InvoiceDetailDialog(detail, self.sales_service, self.output_service, self.settings_service, parent=self)
        dialog.exec_()


class CustomersPage(QWidget):
    # Emitted after any customer create/payment/adjust/delete so MainWindow can
    # lazily refresh the Dashboard (outstanding receivables) on its next visit.
    customers_changed = pyqtSignal()

    def __init__(self, service, sales_service=None, output_service=None, settings_service=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.sales_service = sales_service
        self.output_service = output_service
        self.settings_service = settings_service

        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        self.stack.addWidget(self._build_customer_list_view())

        self.invoice_history_view = _CustomerInvoiceHistoryView(
            self.sales_service, self.output_service, self.settings_service
        )
        self.invoice_history_view.back_requested.connect(self._show_customer_list)
        self.stack.addWidget(self.invoice_history_view)

        self.refresh()

    # ---- Level 1: customer table ----
    def _build_customer_list_view(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        h = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search customer name...")
        h.addWidget(self.search, 1)
        add = QPushButton("Add Customer"); add.setObjectName("primaryButton")
        pay = QPushButton("Record Payment")
        invoices = QPushButton("Invoice History")
        history = QPushButton("View Ledger")
        adjust = QPushButton("Adjust Balance")
        delete = QPushButton("Delete Customer"); delete.setObjectName("destructiveButton")
        h.addWidget(add); h.addWidget(pay); h.addWidget(adjust); h.addWidget(invoices); h.addWidget(history); h.addWidget(delete)
        l.addLayout(h)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Customer", "Outstanding Balance"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        # Requirement #3: headers align to the extreme left, like their cells.
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        l.addWidget(self.table)

        self.search.textChanged.connect(self.refresh)
        add.clicked.connect(self.add_customer)
        pay.clicked.connect(self.record_payment)
        adjust.clicked.connect(self.adjust_balance)
        invoices.clicked.connect(self.open_invoice_history)
        history.clicked.connect(self.view_history)
        delete.clicked.connect(self.delete_customer)
        # Double-clicking a customer row jumps straight to Level 2.
        self.table.itemDoubleClicked.connect(lambda _: self.open_invoice_history())
        return page

    def refresh(self):
        customers = self.service.list(self.search.text()); self.table.setRowCount(len(customers))
        for r, c in enumerate(customers):
            i = QTableWidgetItem(c.name); i.setData(Qt.ItemDataRole.UserRole, c.id); self.table.setItem(r, 0, i)
            self.table.setItem(r, 1, QTableWidgetItem(money(self.service.outstanding(c.id))))
        left_align_items(self.table)
        self._size_columns()

    def _size_columns(self):
        width = max(420, self.table.viewport().width())
        name_width = int(width * 0.60)
        balance_width = width - name_width
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, name_width)
        self.table.setColumnWidth(1, balance_width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "table"):
            self._size_columns()

    def selected_id(self):
        r = self.table.currentRow(); return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def add_customer(self):
        dialog = _PromptDialog("Add Customer", "New Udhaar customer.", [("Customer name", "")], parent=self)
        if dialog.exec_() == QDialog.DialogCode.Accepted:
            try:
                self.service.create(dialog.value()); self.refresh(); self.customers_changed.emit()
            except ValueError as e:
                QMessageBox.warning(self, "Customer", str(e))

    def record_payment(self):
        cid = self.selected_id()
        if cid is None: return
        c = self.service.get(cid)
        dialog = _PromptDialog("Record Udhaar Payment", f"Applied against {c.name}'s outstanding balance." if c else "", [("Amount received", "")], parent=self)
        if dialog.exec_() == QDialog.DialogCode.Accepted:
            try:
                self.service.record_payment(cid, dialog.value()); self.refresh(); self.customers_changed.emit()
            except ValueError as e:
                QMessageBox.warning(self, "Payment", str(e))

    def adjust_balance(self):
        cid = self.selected_id()
        if cid is None: return
        c = self.service.get(cid)
        dialog = _PromptDialog(
            "Adjust Outstanding Balance",
            f"For {c.name}. Enter a positive amount to add outstanding, or a negative amount to reduce it." if c else "",
            [("Adjustment", ""), ("Reason / reference", "")],
            parent=self,
        )
        if dialog.exec_() != QDialog.DialogCode.Accepted: return
        try:
            new_balance = self.service.adjust_balance(cid, dialog.value(0), dialog.value(1))
            QMessageBox.information(self, "Balance adjusted", f"New outstanding balance: {money(new_balance)}")
            self.refresh()
            self.customers_changed.emit()
        except ValueError as e:
            QMessageBox.warning(self, "Balance adjustment", str(e))

    def delete_customer(self):
        cid = self.selected_id()
        if cid is None: return
        c = self.service.get(cid)
        if c is None: return
        outstanding = self.service.outstanding(cid)
        if outstanding != 0:
            QMessageBox.warning(
                self, "Cannot delete customer",
                f'"{c.name}" has an outstanding balance of {money(outstanding)}. '
                "Settle the balance to zero before deleting this customer.",
            )
            return
        if QMessageBox.question(
            self, "Delete customer", f'Delete "{c.name}"? This cannot be undone.',
        ) != QMessageBox.StandardButton.Yes: return
        try:
            self.service.delete_customer(cid)
            self.refresh()
            self.customers_changed.emit()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete customer", str(e))

    def view_history(self):
        """The existing full ledger dialog (payments, adjustments, and credit
        sales) -- unchanged. Kept distinct from Invoice History (Level 2),
        which shows only the invoices themselves with their item-level
        breakdown available in Level 3."""
        cid = self.selected_id()
        if cid is None: return
        c = self.service.get(cid)
        if c is None: return
        entries = self.service.history(cid)
        running = []
        balance = Decimal("0.00")
        for e in entries:
            balance += Decimal(e.amount)
            running.append(balance)
        balances_by_entry = {id(e): running[idx] for idx, e in enumerate(entries)}

        d = QDialog(self)
        d.setWindowTitle(f"Customer Ledger · {c.name}")
        size_to_screen(d, 0.6, 0.66, min_width=720, min_height=480)
        l = QVBoxLayout(d)
        heading = QLabel(f"Customer Ledger · {c.name}"); heading.setObjectName("dialogTitle"); l.addWidget(heading)
        outstanding = QLabel(f"Outstanding: {money(self.service.outstanding(cid))}"); outstanding.setObjectName("dialogSubtitle"); l.addWidget(outstanding)

        search = QLineEdit()
        search.setPlaceholderText("Search ledger: date, month, time, type, amount, reference...")
        search.setClearButtonEnabled(True)
        l.addWidget(search)

        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Date / Time", "Type", "Amount", "Running Balance", "Reference", "Note"])
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        l.addWidget(table, 1)

        def apply_filter(text=""):
            filtered = self.service.filter_history(entries, text, c.name)
            table.setRowCount(0)
            for e in filtered:
                r = table.rowCount()
                table.insertRow(r)
                values = (
                    e.created_at.strftime("%d %B %Y, %I:%M %p"),
                    e.transaction_type.replace("_", " ").title(),
                    money(e.amount),
                    money(balances_by_entry.get(id(e), e.amount)),
                    e.reference or "—",
                    e.note or "—",
                )
                for col, value in enumerate(values):
                    table.setItem(r, col, QTableWidgetItem(value))
            left_align_items(table)
            table.resizeColumnsToContents()
            header = table.horizontalHeader()
            for col in (0, 1, 2, 3, 4):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        search.textChanged.connect(apply_filter)
        apply_filter()
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        b.rejected.connect(d.reject)
        l.addWidget(b)
        d.exec_()

    # ---- Level 2 navigation ----
    def open_invoice_history(self):
        if self.sales_service is None:
            QMessageBox.warning(self, "Invoice History", "Invoice history is not available right now.")
            return
        cid = self.selected_id()
        if cid is None: return
        c = self.service.get(cid)
        if c is None: return
        self.invoice_history_view.load_customer(cid, c.name)
        self.stack.setCurrentWidget(self.invoice_history_view)

    def _show_customer_list(self):
        self.stack.setCurrentIndex(0)
        self.refresh()
