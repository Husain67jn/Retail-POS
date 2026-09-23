from __future__ import annotations
import re
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor, QIcon, QPainter, QPalette, QPen, QPixmap,
    QTextCharFormat, QTextLayout, QTextOption,
)
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QStackedWidget,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from app.services.product_service import SUPPORTED_UNITS, UNIT_LABELS
from app.ui.widgets import enable_smooth_scrolling
from app.utils.formatting import money, money_input

# Live-search match highlight colour, shared look with the POS/Billing search:
# the exact matched substring gets this yellow behind it (with black ink, set
# in the delegate below); everything else renders exactly as the default
# delegate would. Same #FFE066 used in pos_page.py so a search hit reads
# identically on every screen.
_SEARCH_HIGHLIGHT_COLOR = "#FFE066"
# Horizontal text inset used when the highlight delegate hand-draws a cell, so
# highlighted text sits on roughly the same margin the default painter uses.
_CELL_PADDING_H = 4

# Req 1 (perf) — the inventory search is debounced by this many milliseconds so
# a burst of fast typing triggers at most one heavy re-query + full tree/table
# rebuild once typing pauses, instead of one per keystroke (which pegged CPU
# and disk I/O). Direct refresh() calls (page load, add/edit/delete) stay
# immediate — only the per-keystroke path is debounced.
_SEARCH_DEBOUNCE_MS = 200

# Requirement #3 (dark-theme dropdowns) — Qt renders a QComboBox's popup as
# its own top-level window, which does not inherit a parent widget's
# stylesheet. Every widget in this file that owns a QComboBox (ProductsPage
# itself and ProductDialog, which is a separate top-level QDialog) applies
# this same rule locally so no popup ever falls back to the OS-native white
# list view.
_COMBOBOX_POPUP_QSS = (
    "QComboBox QAbstractItemView {"
    " background-color: #1e293b;"
    " color: #f8fafc;"
    " selection-background-color: #0284c7;"
    " selection-color: #ffffff;"
    " border: 1px solid #334155;"
    "}"
)


class SearchHighlightDelegate(QStyledItemDelegate):
    """Paints the active search term's matches with a yellow highlight.

    Row filtering stays in :meth:`ProductsPage.refresh`; this delegate only adds
    a highlight behind the exact substring each visible cell matched, so *why* a
    row survived the filter is obvious at a glance (e.g. "copper" inside
    "Ballistic Copper Wire"). With no active term — or for a cell that doesn't
    match — it defers entirely to the default painter, so ordinary rendering,
    eliding and alignment are completely unchanged when not searching.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._term = ""

    def set_term(self, term: str) -> None:
        """Set the substring to highlight (case-insensitive); '' clears it."""
        self._term = (term or "").strip()

    def _match_ranges(self, text: str):
        """(start, length) of every case-insensitive match of the active term
        in ``text`` — empty when there is no term or no match."""
        term = self._term
        if not term or not text:
            return []
        haystack = text.lower()
        needle = term.lower()
        ranges = []
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            ranges.append((index, len(needle)))
            start = index + len(needle)
        return ranges

    def _alignment(self, index) -> Qt.AlignmentFlag:
        value = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if value is None:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return Qt.AlignmentFlag(int(value))

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        text = "" if text is None else str(text)
        ranges = self._match_ranges(text)
        if not ranges:
            # No active search, or this cell doesn't match: render exactly as the
            # stock delegate would (default eliding, alignment, selection).
            super().paint(painter, option, index)
            return

        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        # Let the style paint the cell chrome (background, selection highlight,
        # alternating-row colour) with the text cleared, then draw the text by
        # hand with the match highlighted — so the theme still owns everything
        # except the glyphs, and the style never gets to elide the string.
        style_option.text = ""
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        formats = []
        for start, length in ranges:
            char_format = QTextCharFormat()
            char_format.setBackground(QColor(_SEARCH_HIGHLIGHT_COLOR))
            # Black ink on the yellow match keeps the highlighted run legible;
            # unmatched text uses the palette pen set later in paint().
            char_format.setForeground(QColor("#000000"))
            fmt_range = QTextLayout.FormatRange()
            fmt_range.start = start
            fmt_range.length = length
            fmt_range.format = char_format
            formats.append(fmt_range)

        alignment = self._alignment(index)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.NoWrap)
        layout = QTextLayout(text, style_option.font)
        layout.setTextOption(text_option)
        layout.setFormats(formats)
        rect = style_option.rect.adjusted(_CELL_PADDING_H, 0, -_CELL_PADDING_H, 0)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(max(1, rect.width()))
        line.setPosition(QPointF(0.0, 0.0))
        layout.endLayout()

        # Position the single line by hand: horizontally per the cell's own
        # alignment (computed from the natural text width, so it doesn't rely on
        # QTextLayout's alignment pass), and vertically centered in the cell.
        natural_width = line.naturalTextWidth()
        x = float(rect.left())
        if alignment & Qt.AlignmentFlag.AlignHCenter:
            x += max(0.0, (rect.width() - natural_width) / 2.0)
        elif alignment & Qt.AlignmentFlag.AlignRight:
            x += max(0.0, rect.width() - natural_width)
        y = float(rect.top()) + max(0.0, (rect.height() - line.height()) / 2.0)

        painter.save()
        painter.setClipRect(style_option.rect)
        group = (
            QPalette.ColorGroup.Normal
            if style_option.state & QStyle.StateFlag.State_Enabled
            else QPalette.ColorGroup.Disabled
        )
        role = (
            QPalette.ColorRole.HighlightedText
            if style_option.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )
        painter.setPen(style_option.palette.color(group, role))
        layout.draw(painter, QPointF(x, y))
        painter.restore()


class ZeroPriceLineEdit(QLineEdit):
    def focusInEvent(self, event):
        if self.text().strip() in {"0", "0.0", "0.00"}:
            self.clear()
        super().focusInEvent(event)


def _coerce_price_text(text: str) -> str:
    """Extract a numeric price string from freeform user input.

    The price fields deliberately carry no input validator (QDoubleValidator /
    strict regex) so a cashier can type freely; this pulls the first number out
    of whatever was entered and falls back to "0" when nothing numeric is
    present, so a stray letter or word is coerced silently instead of raising a
    "must be a valid decimal value" popup from the service layer. Thousands
    separators are ignored.

    Examples: "1200" -> "1200", "Rs 1,200.50" -> "1200.50", "abc" -> "0",
    "" -> "0". Legitimate business rules (negative or >2-decimal prices) are
    still left to the service to report with its own clear message.
    """
    cleaned = (text or "").strip().replace(",", "")
    match = re.search(r"-?\d*\.?\d+", cleaned)
    return match.group(0) if match else "0"


# Compact 28x28 icon-only action buttons for the Actions column — Edit/Delete
# on product rows, and a "+" (add product) on category rows. They deliberately
# do NOT reuse the app-wide #primaryButton / #destructiveButton tiers: those
# are tall (min-height 38–42px) toolbar buttons, and letting them size the
# cells made the actions render at mismatched heights. Each gets its own object
# name + a self-contained compact style (ID-specificity selectors so they win
# over the global QPushButton rules). Glyphs are hand-drawn with QPainter (same
# technique as Billing's trash icon) rather than emoji, since emoji coverage and
# rendering vary a lot across OS/fonts and can end up missing or inconsistent;
# a vector glyph always renders identically.
_ICON_SIZE = 20
# Compact square action buttons (spec: exact icon UI match) — 28x28 button box
# with a 14x14 icon, shared by the row Edit/Delete buttons and the category
# Add(+)/Rename buttons so every action icon across both views is identical.
_ADD_BUTTON_SIZE = QSize(28, 28)
_ADD_ICON_SIZE = 14
_ROW_BUTTON_SIZE = QSize(28, 28)
_ROW_ICON_SIZE = 14
_ICON_ACTION_QSS = {
    False: (  # Edit / Add — solid blue, no border, flush icon.
        "QPushButton#rowIconEdit {"
        " background-color: #2563eb; border: none; border-radius: 4px; padding: 0;"
        "}"
        "QPushButton#rowIconEdit:hover { background-color: #1d4ed8; }"
    ),
    True: (  # Delete — solid red, no border, flush icon.
        "QPushButton#rowIconDelete {"
        " background-color: #dc2626; border: none; border-radius: 4px; padding: 0;"
        "}"
        "QPushButton#rowIconDelete:hover { background-color: #b91c1c; }"
    ),
}


def _pencil_icon(color: str = "#F1F5F9") -> QIcon:
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Simple diagonal pencil body + angled tip.
    painter.drawLine(QPointF(3.5, 12.5), QPointF(10.5, 5.5))
    painter.drawLine(QPointF(10.5, 5.5), QPointF(12.5, 7.5))
    painter.drawLine(QPointF(12.5, 7.5), QPointF(5.5, 14.5))
    painter.drawLine(QPointF(5.5, 14.5), QPointF(3.5, 12.5))
    painter.drawLine(QPointF(3.5, 12.5), QPointF(3, 14.5))
    painter.drawLine(QPointF(3, 14.5), QPointF(5.5, 14.5))
    painter.end()
    return QIcon(pixmap)


def _trash_icon(color: str = "#F87171") -> QIcon:
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.3)
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(4, 5.2, 8, 8.4), 1, 1)
    painter.drawLine(QPointF(2.8, 5.2), QPointF(13.2, 5.2))
    painter.drawLine(QPointF(6.4, 5.2), QPointF(6.8, 3.4))
    painter.drawLine(QPointF(9.6, 5.2), QPointF(9.2, 3.4))
    painter.drawLine(QPointF(6.8, 3.4), QPointF(9.2, 3.4))
    for x in (6.1, 8, 9.9):
        painter.drawLine(QPointF(x, 7.2), QPointF(x, 11.6))
    painter.end()
    return QIcon(pixmap)


def _folder_icon(color: str = "#38BDF8") -> QIcon:
    """Small hand-drawn folder glyph for the Category (parent) accordion rows.
    Hand-drawn for the same reason as the pencil/trash icons above — emoji
    (📁) coverage and rendering vary across OS/fonts, a vector glyph never
    does — and tinted the same bright sky accent the category name uses."""
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.3)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Folder body + a raised tab along the top-left edge.
    painter.drawRoundedRect(QRectF(2.0, 5.2, 12.0, 8.2), 1.2, 1.2)
    painter.drawLine(QPointF(2.4, 5.2), QPointF(5.6, 5.2))
    painter.drawLine(QPointF(5.6, 5.2), QPointF(6.8, 3.5))
    painter.drawLine(QPointF(6.8, 3.5), QPointF(9.2, 3.5))
    painter.drawLine(QPointF(9.2, 3.5), QPointF(9.6, 5.2))
    painter.end()
    return QIcon(pixmap)


def _plus_icon(color: str = "#38BDF8") -> QIcon:
    """Small hand-drawn '+' glyph for a category row's compact "add product"
    button — replaces the old wide, labelled "Add Product" button so the
    Actions column can stay strictly narrow (see _ACTIONS_COLUMN_WIDTH)."""
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    mid = _ICON_SIZE / 2.0
    painter.drawLine(QPointF(mid, 3.5), QPointF(mid, _ICON_SIZE - 3.5))
    painter.drawLine(QPointF(3.5, mid), QPointF(_ICON_SIZE - 3.5, mid))
    painter.end()
    return QIcon(pixmap)


def _icon_action_button(
    icon: QIcon,
    tooltip: str,
    destructive: bool = False,
    size: QSize = _ROW_BUTTON_SIZE,
    icon_size: int = _ICON_SIZE,
) -> QPushButton:
    button = QPushButton()
    button.setObjectName("rowIconDelete" if destructive else "rowIconEdit")
    button.setIcon(icon)
    button.setIconSize(QSize(icon_size, icon_size))
    button.setFixedSize(size)
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(_ICON_ACTION_QSS[destructive])
    return button


class ProductDialog(QDialog):
    """Add/Edit dialog.

    Requirement #1 — the old self-referential "Parent Group" picker (which
    grouped products by making one product the literal parent of another) has
    been removed entirely. The ONLY grouping concept left is Category Name: a
    freeform, type-to-select-or-create QComboBox. Products sharing a Category
    are what the Inventory accordion groups together (see
    ProductsPage._populate_tree / ProductService.search_grouped_by_category).

    ``initial_category`` is a convenience used when this dialog is opened via
    a category row's "Add Product" action (see ProductsPage._add_product_to_category):
    it pre-fills the Category field with that category's name so the new
    product lands in the same group by default, while still leaving the field
    fully editable in case the cashier wants to file it elsewhere instead.
    """

    def __init__(self, service, product=None, parent=None, initial_category: str | None = None):
        super().__init__(parent)
        self.service = service
        self.product = product
        self.setWindowTitle("Edit Product" if product else "Add Product")
        # Requirement #4 — this dialog is its own top-level window, so the
        # dark theme applied elsewhere does not cascade down to it; it needs
        # its own copy of the QComboBox popup rule.
        self.setStyleSheet(_COMBOBOX_POPUP_QSS)

        form = QFormLayout(self)
        self.name = QLineEdit(product.name if product else "")
        self.name.setPlaceholderText("Product name")
        self.purchase = ZeroPriceLineEdit(money_input(product.purchase_price) if product else "0")
        self.wholesale = ZeroPriceLineEdit(money_input(product.wholesale_price) if product else "0")
        self.price = ZeroPriceLineEdit(money_input(product.selling_price) if product else "0")
        self.unit = QComboBox()
        self.unit.addItems([UNIT_LABELS[u] for u in SUPPORTED_UNITS])
        # Purchase Date: plain freeform text (no QDateEdit/calendar picker),
        # since the business records this in whatever format the paperwork
        # came in — "2 Sep 2026", "15/08/2026", etc. — and a strict date
        # widget would reject anything outside one fixed format.
        self.purchase_date = QLineEdit(product.purchase_date if product and product.purchase_date else "")
        self.purchase_date.setPlaceholderText("e.g. 2 Sep 2026")
        unit_default = product.unit if product else None
        if unit_default in SUPPORTED_UNITS:
            self.unit.setCurrentIndex(SUPPORTED_UNITS.index(unit_default))

        form.addRow("Product Name", self.name)
        form.addRow("Purchase Price", self.purchase)
        form.addRow("Wholesale Price", self.wholesale)
        form.addRow("Retail Price", self.price)
        form.addRow("Unit", self.unit)

        # ---- Category Name — the ONLY grouping concept left (Requirement #1) ----
        # Editable so a category can be picked from the list OR typed fresh to
        # create one on the fly (see ProductService.get_or_create_category).
        # e.g. creating "GFC Dimmer", "China Dimmer" and "HFC Dimmer" all with
        # Category = "Dimmer" is what makes them appear together, later, under
        # one "Dimmer" row when searching "Dimmer" in the Inventory accordion.
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.category.addItem("", None)
        for category in service.list_categories(active_only=True):
            self.category.addItem(category.name, category.id)
        if product and product.category_id is not None:
            idx = self.category.findData(product.category_id)
            if idx >= 0:
                self.category.setCurrentIndex(idx)
            elif product.category is not None:
                # Category exists but is deactivated, so it isn't in the
                # dropdown list above — still show its name as typed text
                # rather than silently reverting to blank.
                self.category.setEditText(product.category.name)
        elif initial_category:
            # Pre-filled from a category row's "Add Product" action. Still a
            # plain editable text — not locked — so the cashier can retype it
            # to file the new product under a different category instead.
            self.category.setEditText(initial_category)
        form.addRow("Category Name", self.category)

        form.addRow("Purchase Date:", self.purchase_date)

        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        b.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryButton")
        form.addRow(b)
        b.accepted.connect(self.save)
        b.rejected.connect(self.reject)

    def save(self):
        try:
            unit = SUPPORTED_UNITS[self.unit.currentIndex()]
            # Price fields carry no input validator (flexible entry): extract a
            # numeric value here and default to "0" for non-numeric text, so a
            # stray letter is silently coerced instead of blocking the save with
            # a "must be a valid decimal value" popup (see _coerce_price_text).
            purchase_price = _coerce_price_text(self.purchase.text())
            wholesale_price = _coerce_price_text(self.wholesale.text())
            selling_price = _coerce_price_text(self.price.text())
            # Resolving unconditionally (rather than only when the typed text
            # differs from the current item) keeps this simple: an unchanged,
            # already-existing name just resolves straight back to the same
            # category via the case-insensitive lookup, with no duplicate risk.
            category = self.service.get_or_create_category(self.category.currentText())
            category_id = category.id if category is not None else None
            if self.product:
                self.service.update_product(
                    self.product.id, name=self.name.text(), purchase_price=purchase_price,
                    wholesale_price=wholesale_price, selling_price=selling_price, unit=unit,
                    purchase_date=self.purchase_date.text(),
                    # None here means "not supplied" to update_product (it
                    # never clears an existing category) — so blanking this
                    # field on an edit leaves the product's existing category
                    # untouched rather than clearing it.
                    category_id=category_id,
                )
            else:
                self.service.create_product(
                    name=self.name.text(), purchase_price=purchase_price,
                    wholesale_price=wholesale_price, selling_price=selling_price, unit=unit,
                    purchase_date=self.purchase_date.text(),
                    category_id=category_id,
                )
            self.accept()
        except ValueError as e:
            QMessageBox.warning(self, "Product", str(e))


# Shared column layout for BOTH states, so the accordion (State B) reads as
# the same table just re-shaped into groups, not a different screen.
# Requirement #2 — this is the exact, fixed column set for the expanded
# Category sub-table: no extra "Brand" column, no logo/pill column.
_COLUMN_HEADERS = ["Product Name", "Unit", "Date", "Purchase Price", "WS Price", "Retail Price", "Actions"]
# Explicit fixed pixel widths for columns 1-5 (Unit, Date, Purchase Price,
# WS Price, Retail Price) so their header text never gets squashed/truncated
# regardless of viewport width. Column 0 (Product/Category Name) stretches to
# fill whatever space remains, and column 6 (Actions) is the separate strict
# fixed width below.
_UNIT_COLUMN_WIDTH = 80
_DATE_COLUMN_WIDTH = 100
_PURCHASE_PRICE_COLUMN_WIDTH = 110
_WS_PRICE_COLUMN_WIDTH = 90
_RETAIL_PRICE_COLUMN_WIDTH = 90
_FIXED_DATA_COLUMN_WIDTHS = [
    _UNIT_COLUMN_WIDTH,
    _DATE_COLUMN_WIDTH,
    _PURCHASE_PRICE_COLUMN_WIDTH,
    _WS_PRICE_COLUMN_WIDTH,
    _RETAIL_PRICE_COLUMN_WIDTH,
]

# Actions column: STRICT fixed width. Two centered 28px icon buttons (Edit +
# Delete) with 8px between them fit comfortably inside this, and a category
# row's single 28px "+" button sits centered in the same width — so the
# column never has to widen for a labelled button again.
_ACTIONS_COLUMN_WIDTH = 110


class ProductsPage(QWidget):
    # Emitted after any product/category create/edit/delete so MainWindow can
    # lazily refresh the POS product list and Dashboard on their next visit.
    products_changed = pyqtSignal()

    def __init__(self, service, settings_service=None, parent=None):
        super().__init__(parent)
        self.service = service
        # Requirement #4 — dark-theme QComboBox popups for anything hosted
        # directly on this page. ProductDialog carries its own copy too,
        # since it's a separate top-level QDialog this stylesheet can't reach.
        self.setStyleSheet(_COMBOBOX_POPUP_QSS)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search products... (type a product or Category name, e.g. \"Dimmer\")")
        # Same polished 'X' clear button as the Billing search field: the
        # native Qt clear action exists once setClearButtonEnabled(True) is
        # called, but its icon renders quite small by default, so it is
        # located as a QToolButton child and enlarged directly.
        self.search.setClearButtonEnabled(True)
        clear_button = self.search.findChild(QToolButton)
        if clear_button is not None:
            clear_button.setIconSize(QSize(18, 18))
        header.addWidget(self.search, 1)
        add = QPushButton("Add Product")
        add.setObjectName("primaryButton")
        header.addWidget(add)
        layout.addLayout(header)

        # ---- State A: flat table (search empty) ----
        self.table = QTableWidget(0, len(_COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        # Compact rows: 36px is enough for a 28px icon button plus its 4px
        # top/bottom cell margins (see _make_row_actions/_make_tree_row_actions),
        # with no wasted space — this is what actually fixes the fat/bloated
        # rows the old 48px floor produced (which was originally sized for
        # taller, labelled text buttons; those are now the compact icon
        # buttons below).
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setObjectName("productsFlatTable")
        # Dense, crisp cell text: smaller font and tighter padding than the
        # app-wide table default, scoped to this table only.
        self.table.setStyleSheet(
            "QTableWidget#productsFlatTable::item { height: 38px; padding: 4px 8px; font-size: 12px; }"
        )
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        enable_smooth_scrolling(self.table)
        # Live search highlighting: draws the matched substring with a yellow
        # background as rows are filtered; the term is pushed in from refresh().
        self._highlight_delegate = SearchHighlightDelegate(self.table)
        self.table.setItemDelegate(self._highlight_delegate)

        # ---- State B: Category accordion (search non-empty) ----
        # Requirement #1/#2 — this tree groups strictly by Category Name now
        # (see ProductService.search_grouped_by_category). A top-level row is
        # a Category ("Dimmer"); expanding it reveals a clean sub-table of the
        # real products filed under that category ("GFC Dimmer", "China
        # Dimmer", "HFC Dimmer", ...) with no divider/section-title row, no
        # logos, and no extra "Brand Comparison" chrome — just the plain
        # column set above.
        self.tree = QTreeWidget()
        self.tree.setObjectName("productsAccordionTree")
        # Same 36px compact row floor as the flat table, plus the same dense
        # item padding/font-size, applied via QSS since QTreeWidget has no
        # vertical-header section size to set directly. The #334155 row
        # divider lets each category header row (dark #1E293B background,
        # set per-item in _populate_tree) still read as a bordered band
        # against its neighbours at the new, tighter height.
        self.tree.setStyleSheet(
            "QTreeWidget#productsAccordionTree::item {"
            " height: 38px; padding-left: 4px; padding-right: 4px; font-size: 12px;"
            " border-bottom: 1px solid #334155;"
            "}"
            # Subtle child-row hover lift (spec #1e293b). Category header rows
            # already sit on #1E293B, so hover is invisibly a no-op on them and
            # only lifts the darker child rows — exactly the intent.
            "QTreeWidget#productsAccordionTree::item:hover { background: #1E293B; }"
            # Hide Qt's native branch triangle: this view carries its own
            # ▼/▶ text prefix (see _sync_category_arrow) plus a folder icon on
            # each category row, so the extra native glyph would just be clutter.
            "QTreeWidget#productsAccordionTree::branch { image: none; background: #1E293B; }"
        )
        self.tree.setColumnCount(len(_COLUMN_HEADERS))
        self.tree.setHeaderLabels(_COLUMN_HEADERS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(False)
        self.tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tree.header().setStretchLastSection(False)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        enable_smooth_scrolling(self.tree)
        self.tree.headerItem().setToolTip(
            0, "Category name at the top level (click a row, or its arrow, to expand); individual product names once expanded"
        )
        for col in (3, 4, 5):
            self.tree.headerItem().setToolTip(
                col, "Blank on a category row — these figures only apply to the individual product rows underneath it"
            )
        # Single click anywhere on a category row toggles it open/closed
        # (double-click no longer does, so a fast double-click doesn't
        # toggle twice and snap straight back to where it started). Product
        # rows have no children, so clicking one just selects it as normal.
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemClicked.connect(self._toggle_category_row)
        # However expansion actually happens (this click handler, the
        # tree's own native arrow indicator, or the auto-expand-on-search-
        # match in _populate_tree), keep the ▶/▼ prefix in sync with it.
        self.tree.itemExpanded.connect(self._sync_category_arrow)
        self.tree.itemCollapsed.connect(self._sync_category_arrow)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.tree)
        layout.addWidget(self.stack)

        # Req 1 (perf) — debounce the live search. Every keystroke only
        # (re)starts this single-shot timer; the expensive refresh (re-query +
        # full tree/table rebuild) runs once, _SEARCH_DEBOUNCE_MS after the LAST
        # keystroke, so typing fast no longer fires a query/repaint per
        # character. Parented to self so it is cleaned up with the page.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(self._search_debounce.start)
        add.clicked.connect(self.add_product)
        self.refresh()

    # ---- State A: flat table ----
    def selected_id(self):
        r = self.table.currentRow()
        return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def _populate_flat_table(self, products):
        self.table.setRowCount(len(products))
        for r, p in enumerate(products):
            values = [
                p.name, UNIT_LABELS.get(p.unit, p.unit), p.purchase_date or "",
                money(p.purchase_price), money(p.wholesale_price), money(p.selling_price),
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, p.id)
                self.table.setItem(r, c, item)
            self.table.setCellWidget(r, 6, self._make_row_actions(p))
        self._size_columns()

    def _make_row_actions(self, product) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        # Zero margins + a stretch on each side keeps the icon pair centered
        # inside the strict fixed-width Actions column (spec: centered icons).
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        edit_btn = _icon_action_button(
            _pencil_icon("#ffffff"), "Edit", size=_ROW_BUTTON_SIZE, icon_size=_ROW_ICON_SIZE
        )
        edit_btn.clicked.connect(lambda _=False, pid=product.id: self._edit_product_id(pid))
        delete_btn = _icon_action_button(
            _trash_icon("#ffffff"), "Delete", destructive=True,
            size=_ROW_BUTTON_SIZE, icon_size=_ROW_ICON_SIZE,
        )
        delete_btn.clicked.connect(lambda _=False, pid=product.id: self._delete_product_id(pid))
        row.addWidget(edit_btn)
        row.addWidget(delete_btn)
        row.addStretch(1)
        return holder

    def _size_columns(self):
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        self._apply_fixed_column_widths(header, self.table)

    @staticmethod
    def _apply_fixed_column_widths(header, view):
        """Shared column geometry for BOTH the flat table and the accordion
        tree: Product Name (col 0) stretches to fill remaining space; Unit,
        Date, Purchase/WS/Retail Price (cols 1-5) and Actions (col 6) are all
        strict fixed pixel widths, so header text never gets squashed
        regardless of viewport width."""
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, w in enumerate(_FIXED_DATA_COLUMN_WIDTHS, start=1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            view.setColumnWidth(col, w)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        view.setColumnWidth(6, _ACTIONS_COLUMN_WIDTH)

    # ---- State B: Category accordion ----
    # Requirement #2 (this turn) — the Category header row's visual identity:
    # bold, bright accent name text and a distinct dark card-like background,
    # so it reads unmistakably as a section header rather than just another
    # data row. Kept as named constants rather than inline literals so the
    # header/child contrast is defined in exactly one place.
    _CATEGORY_ROW_BG = QColor("#1E293B")
    _CATEGORY_ROW_BORDER = QColor("#334155")
    _CATEGORY_NAME_COLOR = QColor("#38BDF8")
    # Spec: bold 13px. Pixel size (not point size) keeps it consistent with
    # the 12px item text set in the tree QSS above.
    _CATEGORY_NAME_PX = 13

    def _populate_tree(self, groups, term: str):
        """``groups`` are ProductService.CategoryGroup instances (see
        product_service.py) — plain in-memory groupings by Category, each
        carrying the real Product rows filed under it as ``.products``.
        """
        self.tree.clear()
        needle = term.strip().lower()
        for group in groups:
            # Requirement #1 (this turn) — the Category (parent) row shows
            # ONLY the category name. Every detail/financial column (Unit,
            # Purchase Date, Purchase Price, Wholesale Price, Retail Price)
            # is left completely blank here — no rollup, no min/average, no
            # summary of any kind. Those real figures belong to the child
            # product rows below, once expanded.
            parent_item = QTreeWidgetItem(["", "", "", "", "", "", ""])
            parent_item.setData(0, Qt.ItemDataRole.UserRole, group.id)
            # The plain name is kept separately from the displayed text
            # (which carries the ▶/▼ prefix — see _sync_category_arrow) so
            # the prefix can be added/swapped repeatedly without ever
            # accumulating or having to strip characters back out of it.
            parent_item.setData(0, Qt.ItemDataRole.UserRole + 1, group.name)
            for col in range(6):
                parent_item.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
                # Distinct dark "card" shading across the whole row so the
                # category header reads as a clearly separate band from the
                # product rows nested under it. (QTreeWidgetItem has no
                # per-item border property to pair with it — the tree-wide
                # gridline/border colour below approximates the requested
                # #334155 border using the one mechanism QTreeWidget exposes
                # for it.)
                parent_item.setBackground(col, self._CATEGORY_ROW_BG)
            parent_item.setTextAlignment(0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # Bold, bright accent colour on the category name + a folder icon,
            # so the row reads unmistakably as a group header. Spec: folder
            # icon + bold name + an item-count pill; the pill is a small widget
            # dropped into the otherwise-blank Unit column right after the name
            # (see _make_count_pill), which keeps the name cell itself plain
            # text so a single click anywhere on it still toggles the group.
            name_font = parent_item.font(0)
            name_font.setBold(True)
            name_font.setPixelSize(self._CATEGORY_NAME_PX)
            parent_item.setFont(0, name_font)
            parent_item.setForeground(0, self._CATEGORY_NAME_COLOR)
            parent_item.setIcon(0, _folder_icon())
            self.tree.addTopLevelItem(parent_item)
            self.tree.setItemWidget(parent_item, 1, self._make_count_pill(len(group.products)))
            self.tree.setItemWidget(parent_item, 6, self._make_tree_row_actions(group, is_category=True))

            # Requirement #2 — no "Brand Comparison" divider row, no logos,
            # no pills: the expanded section is just the plain product rows
            # below, using the exact same column set as the flat table.
            for product in group.products:
                child_item = QTreeWidgetItem([
                    product.name, UNIT_LABELS.get(product.unit, product.unit), product.purchase_date or "",
                    money(product.purchase_price), money(product.wholesale_price),
                    money(product.selling_price), "",
                ])
                child_item.setData(0, Qt.ItemDataRole.UserRole, product.id)
                for col in range(6):
                    child_item.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
                parent_item.addChild(child_item)
                self.tree.setItemWidget(child_item, 6, self._make_tree_row_actions(product, is_category=False))

            # Auto-expand a category when the search matched one of its
            # products directly (e.g. typing "GFC"), so the hit is visible
            # without an extra click; a match on the category's own name
            # stays collapsed since the row itself already answers the search.
            product_hit = needle and any(needle in p.name.lower() for p in group.products)
            # setExpanded's own itemExpanded/itemCollapsed signal (connected
            # in __init__) writes the initial ▶/▼ prefix onto the row, so
            # there's no separate "set the starting arrow" step needed here.
            parent_item.setExpanded(bool(product_hit) or not group.products)
            if not parent_item.isExpanded():
                # setExpanded(False) when a row is already collapsed (the
                # common case: most categories start closed) does not emit
                # itemCollapsed, since Qt only signals on an actual state
                # change — so the very first arrow still needs writing here.
                self._sync_category_arrow(parent_item)

        self._size_tree_columns()

    def _toggle_category_row(self, item: QTreeWidgetItem, column: int) -> None:
        """Single click anywhere on a category (top-level) row expands or
        collapses it; product rows have no children, so a click on one just
        selects it as normal and this is a no-op for it."""
        if item.parent() is None:
            item.setExpanded(not item.isExpanded())

    def _sync_category_arrow(self, item: QTreeWidgetItem) -> None:
        """Keep the ▶ (collapsed) / ▼ (expanded) prefix on a category row's
        name in step with its actual expansion state, however that state
        changed — this click handler, the tree's native arrow indicator
        (still clickable; only double-click-to-expand is disabled), or the
        auto-expand-on-search-match in _populate_tree."""
        if item.parent() is not None:
            return  # Product rows carry no arrow/prefix.
        name = item.data(0, Qt.ItemDataRole.UserRole + 1) or ""
        arrow = "\u25bc" if item.isExpanded() else "\u25b6"
        item.setText(0, f"{arrow} {name}")

    def _make_tree_row_actions(self, entity, is_category: bool) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        # Zero margins + a stretch on each side centres the icon(s) inside the
        # strict fixed-width Actions column (spec: centered icons).
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        if is_category:
            # A category is a name, not a single product row. Two compact 28px
            # icon buttons keep the Actions column strictly narrow: a pencil to
            # rename the category (Req 5) and a "+" to add a product under it;
            # their tooltips spell out what each does. The synthetic
            # "Uncategorized" bucket (id is None) is not a real DB category, so
            # it gets no rename button — there is nothing to rename.
            if entity.id is not None:
                edit_cat_btn = _icon_action_button(
                    _pencil_icon("#ffffff"), "Rename this category",
                    size=_ADD_BUTTON_SIZE, icon_size=_ADD_ICON_SIZE,
                )
                edit_cat_btn.clicked.connect(
                    lambda _=False, cid=entity.id, name=entity.name: self._edit_category(cid, name)
                )
                row.addWidget(edit_cat_btn)
            add_btn = _icon_action_button(
                _plus_icon("#ffffff"), "Add product to this category",
                size=_ADD_BUTTON_SIZE, icon_size=_ADD_ICON_SIZE,
            )
            add_btn.clicked.connect(lambda _=False, name=entity.name: self._add_product_to_category(name))
            row.addWidget(add_btn)
        else:
            edit_btn = _icon_action_button(
                _pencil_icon("#ffffff"), "Edit", size=_ROW_BUTTON_SIZE, icon_size=_ROW_ICON_SIZE
            )
            edit_btn.clicked.connect(lambda _=False, pid=entity.id: self._edit_product_id(pid))
            row.addWidget(edit_btn)
            delete_btn = _icon_action_button(
                _trash_icon("#ffffff"), "Delete", destructive=True,
                size=_ROW_BUTTON_SIZE, icon_size=_ROW_ICON_SIZE,
            )
            delete_btn.clicked.connect(lambda _=False, pid=entity.id: self._delete_product_id(pid))
            row.addWidget(delete_btn)
        row.addStretch(1)
        return holder

    def _make_count_pill(self, count: int) -> QWidget:
        """Small rounded "N items" badge dropped into a category row's Unit
        column, just after the name — the spec's item-count pill. Wrapped in a
        stretch layout so the pill hugs its natural width against the name side
        rather than stretching across the whole cell."""
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{count} item" if count == 1 else f"{count} items")
        label.setObjectName("categoryCountPill")
        label.setStyleSheet(
            "QLabel#categoryCountPill {"
            " background: #0B2A3A; color: #38BDF8; border: 1px solid #1E4B60;"
            " border-radius: 9px; padding: 1px 8px; font-size: 10px; font-weight: bold;"
            "}"
        )
        lay.addWidget(label)
        lay.addStretch(1)
        return holder

    def _size_tree_columns(self):
        header = self.tree.header()
        header.setStretchLastSection(False)
        self._apply_fixed_column_widths(header, self.tree)

    # ---- Shared ----
    def refresh(self):
        term = self.search.text()
        # Live search highlighting: push the current term onto the delegate so
        # the matched substring is highlighted in the rows below. Cleared
        # automatically when the search box is emptied (term == "").
        self._highlight_delegate.set_term(term)
        if term.strip():
            search_words = term.lower().strip().split()
            # Passing the full multi-word term straight to the service would
            # have it match as one literal substring (e.g. "abid socket"
            # wouldn't match "abid pathar socket"). Query with only the
            # first word as a safe superset — any true match must contain
            # it — then AND-filter across every word ourselves below.
            groups = self.service.search_grouped_by_category(search_words[0] if search_words else term)
            filtered_groups = []
            for group in groups:
                cat_matches = all(word in group.name.lower() for word in search_words)
                if cat_matches:
                    # Category name matches the search — show all of its
                    # products, not just the ones whose own name matches.
                    filtered_groups.append(group)
                    continue
                matching_products = [
                    p for p in group.products
                    if all(word in p.name.lower() for word in search_words)
                ]
                if matching_products:
                    group.products = matching_products
                    filtered_groups.append(group)
            self._populate_tree(filtered_groups, term)
            self.stack.setCurrentWidget(self.tree)
        else:
            products = self.service.list_products("")
            self._populate_flat_table(products)
            self.stack.setCurrentWidget(self.table)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.stack.currentWidget() is self.table:
            self._size_columns()
        elif hasattr(self, "tree"):
            self._size_tree_columns()

    def add_product(self):
        if ProductDialog(self.service, parent=self).exec_() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.products_changed.emit()

    def _add_product_to_category(self, category_name: str):
        """Requirement #1 — replaces the old "Add Variant under a parent
        product" flow. Opens the same Add Product dialog, pre-filled with
        this category's name, so the new product is grouped alongside its
        siblings by default (still editable if the cashier wants otherwise).
        """
        if ProductDialog(self.service, parent=self, initial_category=category_name).exec_() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.products_changed.emit()

    def _edit_category(self, category_id: int, current_name: str):
        """Req 5 — rename a category from its tree row.

        Prompts for a new name pre-filled with the current one, writes it via
        ProductService.update_category, then refreshes the tree so the renamed
        category row (and its grouping) updates immediately. update_category
        itself rejects a blank name; a duplicate/lookup error surfaces as a
        warning rather than crashing the page.
        """
        new_name, ok = QInputDialog.getText(
            self, "Rename Category", "Category name:", text=current_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == current_name:
            # Nothing to do: cancelled-equivalent (blank) or unchanged.
            return
        try:
            self.service.update_category(category_id, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot rename category", str(e))
            return
        self.refresh()
        self.products_changed.emit()

    def _edit_product_id(self, product_id: int):
        p = self.service.get_product(product_id)
        if p and ProductDialog(self.service, p, self).exec_() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.products_changed.emit()

    def _delete_product_id(self, product_id: int):
        p = self.service.get_product(product_id)
        if not p:
            return
        if QMessageBox.question(self, "Delete product", f'Delete "{p.name}" permanently?') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_product(product_id)
            self.refresh()
            self.products_changed.emit()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete product", str(e))

    # Legacy call sites (Edit/Delete via a selected row) — kept for anything
    # still wired to toolbar-style actions on the currently selected row.
    def edit_product(self):
        pid = self.selected_id()
        if pid is not None:
            self._edit_product_id(pid)

    def delete_product(self):
        pid = self.selected_id()
        if pid is not None:
            self._delete_product_id(pid)
