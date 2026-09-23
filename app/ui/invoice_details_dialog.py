"""The Invoice Details viewer.

Extracted from ``invoices_page.py`` so the viewer's layout can be a
first-class, readable module rather than a dense block inside the list page.

The layout is deliberately vertical-space-frugal, because the one thing this
dialog exists to do is show a bill's *items*: a compact two-column header, a
high-density item table that gets every spare pixel, a compact financial
summary, and a single horizontal action toolbar. Everything except the table is
sized to its content so the table is what grows when the dialog does.
"""
from __future__ import annotations

from decimal import Decimal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.invoice_output_service import InvoiceOutputError
from app.ui.responsive import size_to_screen
from app.ui.widgets import (
    COMPACT_ROW_HEIGHT,
    add_shadow,
    apply_compact_rows,
    enable_smooth_scrolling,
    left_align_items,
    reserve_visible_rows,
)
from app.utils.formatting import money, quantity

# Row height and the number of item rows the table reserves before it has to
# scroll. Matching the Billing cart's 24px baseline keeps the two screens
# reading as one system; reserving 10 rows is what delivers "7-10 products fit
# in view", and rows still grow past the baseline when a long product name
# wraps (see apply_compact_rows) so density never truncates a name.
_ROW_HEIGHT = COMPACT_ROW_HEIGHT
_MIN_VISIBLE_ROWS = 10

# The PDF/print formats InvoiceOutputService supports: a single 180mm thermal
# receipt alongside standard A4.
_THERMAL_FORMATS = ("180mm",)
_PDF_FORMATS = ("A4", "180mm")

_DIALOG_QSS = (
    # Main dialog background.
    "QDialog#invoiceDetailDialog { background: #1e293b; }"
    # Invoice Header container — dark card, light text, slate border.
    "QFrame#invoiceHeaderCard {"
    " background: #0f172a;"
    " border: 1px solid #334155;"
    " border-radius: 8px;"
    "}"
    "QLabel#invoiceShopName { font-size: 15px; font-weight: bold; color: #f8fafc; }"
    "QLabel#invoiceNumber { font-size: 13px; font-weight: bold; color: #f8fafc; }"
    "QLabel#metaKey { color: #94a3b8; }"
    "QLabel#metaValue { color: #f8fafc; font-weight: bold; }"
    # Summary/Totals box — slate background, lighter slate border, light text.
    "QLabel#summaryKey { color: #cbd5e1; }"
    "QLabel#summaryValue { color: #f8fafc; font-weight: bold; }"
    "QLabel#summaryNetKey { color: #f8fafc; font-weight: bold; }"
    "QLabel#summaryNetValue { font-size: 15px; font-weight: bold; color: #f8fafc; }"
)

# Soft grid lines and tight cell padding, scoped by object name so the app-wide
# theme.qss table rules (and every other table in the app) are untouched. The
# header's own padding is compacted here too: theme.qss spends ~9px on it, which
# would cost most of an item row's worth of viewport. Dark theme: dark
# background, slate header, light text throughout — no light/green panels.
_TABLE_QSS = (
    "QTableWidget#invoiceItemsTable {"
    " background-color: #0f172a; gridline-color: #334155;"
    " color: #f8fafc; border: 1px solid #334155;"
    "}"
    "QTableWidget#invoiceItemsTable::item { padding: 1px 4px; color: #f8fafc; }"
    "QTableWidget#invoiceItemsTable::item:alternate { background-color: #1e293b; }"
    "QTableWidget#invoiceItemsTable QHeaderView::section {"
    " padding: 3px 4px; background-color: #334155; color: #f8fafc; border: none;"
    "}"
)

# Summary card background — spec: #334155 fill, #475569 border, no green.
_SUMMARY_CARD_QSS = (
    "QFrame#invoiceSummaryCard {"
    " background: #334155;"
    " border: 1px solid #475569;"
    " border-radius: 8px;"
    "}"
)

# Toolbar button palettes — spec: blue for Edit/Save, slate gray for
# Print/Close, dark red for Delete/Void (applied directly on void_button).
_BLUE_BUTTON_QSS = (
    "QPushButton { background-color: #2563eb; color: #f8fafc; border: none;"
    " border-radius: 6px; padding: 4px 12px; }"
    "QPushButton:hover { background-color: #1d4ed8; }"
    "QPushButton:disabled { color: #94a3b8; background-color: #475569; }"
)
_SLATE_BUTTON_QSS = (
    "QPushButton { background-color: #475569; color: #f8fafc; border: none;"
    " border-radius: 6px; padding: 4px 12px; }"
    "QPushButton:hover { background-color: #334155; }"
    "QPushButton::menu-indicator { width: 0px; }"
)


def format_invoice_datetime(value):
    """Human-readable invoice timestamp, e.g. "3 March 2026, 4:05 PM"."""
    return value.strftime("%d %B %Y, %I:%M %p").replace(" 0", " ", 1)


def _meta_key(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("metaKey")
    return label


def _meta_value(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("metaValue")
    # Selectable so a cashier can copy an invoice number or customer name out.
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label


class InvoiceDetailDialog(QDialog):
    """Read-only view of one historical invoice, plus its output/void actions.

    ``sales_service`` is optional: without it the Void action is hidden rather
    than shown broken, which keeps the dialog constructible from contexts that
    only have the output service. ``edit_callback`` and ``refresh_callback`` are
    likewise optional and each hide/skip only their own concern.
    """

    def __init__(
        self,
        invoice,
        settings,
        output_service,
        parent=None,
        edit_callback=None,
        sales_service=None,
        refresh_callback=None,
    ):
        super().__init__(parent)
        self.invoice = invoice
        self.settings = settings
        self.output_service = output_service
        self.edit_callback = edit_callback
        self.sales_service = sales_service
        self.refresh_callback = refresh_callback

        self.setWindowTitle(f"Invoice {invoice.invoice_number}")
        self.setObjectName("invoiceDetailDialog")
        self.setStyleSheet(_DIALOG_QSS + _SUMMARY_CARD_QSS)
        # Shorter than the previous 0.78 height fraction: the compact header and
        # summary mean the same content needs noticeably less vertical room.
        size_to_screen(self, 0.62, 0.70, min_width=840, min_height=520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_header())
        self._voided_banner = self._build_voided_banner()
        root.addWidget(self._voided_banner)
        root.addWidget(self._build_items_table(), 1)
        root.addWidget(self._build_summary())
        root.addLayout(self._build_toolbar())

    # ------------------------------------------------------------------ header

    def _build_header(self) -> QWidget:
        """Shop name + invoice number on one line, then a 2-column meta grid.

        Invoice #, Customer, Date, Time and Payment status are paired into two
        key/value columns so all five facts cost two rows of height instead of
        the five stacked single-fact labels this replaces.
        """
        card = QFrame()
        card.setObjectName("invoiceHeaderCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        title_row = QHBoxLayout()
        shop = QLabel(self.settings.shop_name)
        shop.setObjectName("invoiceShopName")
        number = QLabel(self.invoice.invoice_number)
        number.setObjectName("invoiceNumber")
        title_row.addWidget(shop)
        title_row.addStretch()
        title_row.addWidget(number)
        outer.addLayout(title_row)

        created = self.invoice.created_at
        payment = "CREDIT (UDHAAR)" if self.invoice.payment_type == "credit" else "CASH"
        # Split so Date and Time read as separate facts (the requirement lists
        # them separately) while still coming from the one timestamp.
        date_text = format_invoice_datetime(created).split(", ")[0]
        time_text = created.strftime("%I:%M %p").lstrip("0")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        # Two key/value column pairs: keys hug their text, values take the slack,
        # which keeps the two columns aligned regardless of content length.
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        pairs = [
            ("Invoice #", self.invoice.invoice_number),
            ("Payment", payment),
            ("Customer", self.invoice.customer_name or "Walk-in"),
            ("Date", date_text),
            ("Status", "VOIDED" if self._is_voided() else "Completed"),
            ("Time", time_text),
        ]
        for index, (key, value) in enumerate(pairs):
            row, column = divmod(index, 2)
            grid.addWidget(_meta_key(key), row, column * 2)
            value_label = _meta_value(value)
            value_label.setToolTip(value)
            grid.addWidget(value_label, row, column * 2 + 1)
        outer.addLayout(grid)

        add_shadow(card, blur=12, y=2, alpha=28)
        return card

    def _build_voided_banner(self) -> QLabel:
        banner = QLabel("VOIDED — this invoice was cancelled and is kept for records only")
        banner.setObjectName("voidedBanner")
        banner.setStyleSheet(
            "color:#f8fafc;font-weight:bold;background:#991b1b;"
            "border:1px solid #475569;border-radius:6px;padding:6px 10px;"
        )
        banner.setVisible(self._is_voided())
        return banner

    # ------------------------------------------------------------------- table

    def _build_items_table(self) -> QTableWidget:
        table = QTableWidget(0, 7)
        self.table = table
        table.setObjectName("invoiceItemsTable")
        table.setHorizontalHeaderLabels(
            ["Product", "Unit", "Qty", "Unit Price", "Disc/Unit", "Line Discount", "Line Total"]
        )
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        table.setStyleSheet(_TABLE_QSS)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Compact baseline rows that still grow for a wrapped product name, and
        # room reserved for a full screenful of items before scrolling starts.
        apply_compact_rows(table, _ROW_HEIGHT)
        reserve_visible_rows(table, _MIN_VISIBLE_ROWS, _ROW_HEIGHT)
        enable_smooth_scrolling(table)

        for item in self.invoice.items:
            row = table.rowCount()
            table.insertRow(row)
            item_quantity = Decimal(item.quantity)
            line_discount = Decimal(item.item_discount)
            # The Billing screen now takes this discount per unit, so the same
            # figure is shown back here; the line discount stays beside it since
            # that is what the invoice actually stored.
            per_unit = (line_discount / item_quantity).quantize(Decimal("0.01")) if item_quantity else Decimal("0.00")
            values = [
                item.product_name,
                item.unit,
                quantity(item_quantity),
                money(item.unit_price),
                money(per_unit),
                money(line_discount),
                money(item.line_total),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(item.product_name)
                table.setItem(row, column, cell)

        left_align_items(table, vertical=Qt.AlignmentFlag.AlignTop)
        self._size_columns()
        # Must run after the columns are sized: how many lines a product name
        # wraps onto depends on the width its column ended up with.
        table.resizeRowsToContents()
        return table

    def _size_columns(self) -> None:
        """Let Product absorb the slack and pin the numeric columns to content.

        Product is the one unpredictable-length field, so stretching it (and
        letting a long name wrap into extra lines) is what keeps the six numeric
        columns from ever being squeezed, with no horizontal scrollbar.
        """
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    # ----------------------------------------------------------------- summary

    def _financials(self):
        """The five summary figures, derived the same way the PDF renderer does.

        Subtotal and the item-discount split are recomputed from the items
        because only the *combined* discount is stored on the invoice.
        """
        gross = sum(
            ((Decimal(i.quantity) * Decimal(i.unit_price)).quantize(Decimal("0.01")) for i in self.invoice.items),
            Decimal("0.00"),
        )
        item_discounts = sum((Decimal(i.item_discount) for i in self.invoice.items), Decimal("0.00"))
        global_discount = Decimal(self.invoice.discount_total) - item_discounts
        return gross, item_discounts, global_discount

    def _build_summary(self) -> QWidget:
        gross, item_discounts, global_discount = self._financials()
        card = QFrame()
        card.setObjectName("invoiceSummaryCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        # Three key/value column pairs -> six figures in two rows instead of the
        # six stacked labels this replaces. Net Total is emphasised as the one
        # figure the eye should land on first.
        rows = [
            ("Subtotal", money(gross), False),
            ("Item Discount", money(item_discounts), False),
            ("Global Discount", money(global_discount), False),
            ("Paid Amount", money(self.invoice.paid_amount), False),
            ("Balance", money(self.invoice.remaining_amount), False),
            ("Net Total", self._currency(self.invoice.grand_total), True),
        ]
        for index, (key, value, emphasise) in enumerate(rows):
            column, row = divmod(index, 2)
            key_label = QLabel(key)
            key_label.setObjectName("summaryNetKey" if emphasise else "summaryKey")
            value_label = QLabel(value)
            value_label.setObjectName("summaryNetValue" if emphasise else "summaryValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(key_label, row, column * 2)
            grid.addWidget(value_label, row, column * 2 + 1)
        for column in (1, 3, 5):
            grid.setColumnStretch(column, 1)

        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        add_shadow(card, blur=12, y=2, alpha=28)
        return card

    def _currency(self, value) -> str:
        return f"{self.settings.currency_symbol} {money(value)}"

    # ----------------------------------------------------------------- toolbar

    def _format_menu_button(self, text: str, formats, handler) -> QToolButton:
        """One button per *action*, with the paper size chosen from its menu.

        Folding the size choice into a menu (rather than a separate flat button
        per size) keeps every format reachable from a single-row toolbar. Save
        PDF still uses it for its A4 / 180mm choice; the thermal print action is
        now a single direct button.
        """
        button = QToolButton()
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(30)
        menu = QMenu(button)
        for format_name in formats:
            menu.addAction(format_name, lambda f=format_name: handler(f))
        button.setMenu(menu)
        # Kept as an attribute so the menu isn't garbage-collected with the local.
        button._menu = menu
        return button

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.print_thermal_button = QPushButton("Print 180mm")
        self.print_thermal_button.setMinimumHeight(30)
        self.print_thermal_button.setToolTip("Print this invoice as a 180mm thermal receipt")
        self.print_thermal_button.setStyleSheet(_SLATE_BUTTON_QSS)
        self.print_thermal_button.clicked.connect(lambda: self._output("180mm", True))
        toolbar.addWidget(self.print_thermal_button)

        self.print_a4_button = QPushButton("Print A4")
        self.print_a4_button.setMinimumHeight(30)
        self.print_a4_button.setToolTip("Print a full-page A4 invoice")
        self.print_a4_button.setStyleSheet(_SLATE_BUTTON_QSS)
        self.print_a4_button.clicked.connect(lambda: self._output("A4", True))
        toolbar.addWidget(self.print_a4_button)

        self.save_pdf_button = self._format_menu_button(
            "Save PDF  ▾", _PDF_FORMATS, lambda f: self._output(f, False)
        )
        self.save_pdf_button.setToolTip("Save this invoice as a PDF file")
        self.save_pdf_button.setStyleSheet(_SLATE_BUTTON_QSS)
        toolbar.addWidget(self.save_pdf_button)

        toolbar.addStretch()

        # Void is only offered when a sales service was supplied to act on it.
        self.void_button = None
        if self.sales_service is not None:
            self.void_button = QPushButton("Void Invoice")
            self.void_button.setObjectName("voidInvoiceButton")
            self.void_button.setMinimumHeight(30)
            self.void_button.setStyleSheet(
                "QPushButton#voidInvoiceButton { color:#f8fafc; border:none;"
                " background:#991b1b; border-radius:6px; padding:4px 12px; }"
                "QPushButton#voidInvoiceButton:hover { background:#7f1d1d; }"
                "QPushButton#voidInvoiceButton:disabled { color:#94a3b8; background:#475569; }"
            )
            self.void_button.clicked.connect(self._void_invoice)
            toolbar.addWidget(self.void_button)

        # "Re-open" is the existing Edit Invoice flow: it voids this invoice and
        # reloads its items into a Billing tab as a replacement bill.
        self.edit_button = None
        if self.edit_callback is not None:
            self.edit_button = QPushButton("Re-open in Billing")
            self.edit_button.setObjectName("editInvoiceButton")
            self.edit_button.setMinimumHeight(30)
            self.edit_button.setStyleSheet(_BLUE_BUTTON_QSS)
            self.edit_button.clicked.connect(self._edit_invoice)
            toolbar.addWidget(self.edit_button)

        self.close_button = QPushButton("Close")
        self.close_button.setMinimumHeight(30)
        self.close_button.setStyleSheet(_SLATE_BUTTON_QSS)
        self.close_button.clicked.connect(self.reject)
        toolbar.addWidget(self.close_button)

        self._sync_action_state()
        return toolbar

    # ------------------------------------------------------------------ state

    def _is_voided(self) -> bool:
        return bool(getattr(self.invoice, "voided", False))

    def _sync_action_state(self) -> None:
        """Enable/disable the destructive actions for the invoice's current state.

        Called after a void as well as at build time, so the dialog reflects the
        new state immediately instead of needing to be reopened.
        """
        voided = self._is_voided()
        if self.void_button is not None:
            self.void_button.setEnabled(not voided)
            self.void_button.setToolTip(
                "Already voided" if voided else "Cancel this invoice, keeping it on record"
            )
        if self.edit_button is not None:
            self.edit_button.setEnabled(not voided)
            self.edit_button.setToolTip(
                "Already voided — this invoice was already replaced"
                if voided
                else "Void this invoice and reload its items into a Billing tab for editing"
            )

    def _void_invoice(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Void Invoice",
            f"Void invoice {self.invoice.invoice_number}?\n\n"
            "The invoice is kept on record for audit, and a credit (Udhaar) balance "
            "it created is reversed. This cannot be undone.\n\n"
            "To edit and re-issue it instead, use Re-open in Billing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.sales_service.void_invoice(self.invoice.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot void invoice", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Void failed", str(exc))
            return
        # Reflect it locally: the invoice object in hand is a detached snapshot,
        # so the banner/actions would otherwise still read as completed.
        self.invoice.voided = True
        self._voided_banner.setVisible(True)
        self._sync_action_state()
        if self.refresh_callback is not None:
            self.refresh_callback()
        QMessageBox.information(
            self, "Invoice voided", f"Invoice {self.invoice.invoice_number} has been voided."
        )

    def _edit_invoice(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Edit Invoice",
            f"This will void invoice {self.invoice.invoice_number} and reload its items into "
            "the Billing screen so you can update and re-finalize the sale. This cannot be undone.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.edit_callback(self.invoice)
        self.accept()

    # ----------------------------------------------------------------- output

    def _output(self, fmt, printer) -> None:
        try:
            if printer:
                self.output_service.generate_and_print(self.invoice, self.settings, fmt)
                QMessageBox.information(
                    self, "Print started", "Invoice sent to the operating system printer."
                )
                return
            suggested = self.output_service.default_path(self.invoice, fmt)
            path, _ = QFileDialog.getSaveFileName(
                self, f"Save {fmt} Invoice PDF", str(suggested), "PDF files (*.pdf)"
            )
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.output_service.generate(self.invoice, self.settings, path, fmt)
            QMessageBox.information(self, "PDF generated", f"Invoice PDF saved to:\n{path}")
        except (InvoiceOutputError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Invoice output failed", str(exc))

    def showEvent(self, event):
        super().showEvent(event)
        # Rows are first measured during __init__, when the table has not been
        # laid out yet and the stretched Product column is still near-zero wide —
        # so a long name wraps onto far more lines than it really needs and the
        # row is left several times its proper height. Re-measuring once the
        # dialog is actually on screen (and its real geometry has propagated) is
        # what makes the compact baseline hold for the first paint.
        if hasattr(self, "table"):
            self.table.resizeRowsToContents()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Wrapping depends on the Product column's width, which follows the
        # dialog's, so rows have to be re-measured or a name that wrapped at the
        # old width keeps its now-wrong height.
        if hasattr(self, "table"):
            self.table.resizeRowsToContents()
