from __future__ import annotations

from decimal import Decimal
from math import ceil

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPalette, QTextCharFormat, QTextLayout, QTextOption
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.invoice_output_service import (
    InvoiceOutputError,
    InvoiceOutputService,
)
from app.services.product_service import UNIT_LABELS, validate_money
from app.services.sales_service import Cart, CartLine
from app.ui.icons import _finish, _new_painter
from app.ui.widgets import (
    COMPACT_ROW_HEIGHT,
    SegmentedControl,
    apply_compact_rows,
    enable_smooth_scrolling,
    reserve_visible_rows,
)
from app.utils.formatting import money, money_input, quantity

# Maximum number of bills a cashier can hold open at once. Every check that
# gates a new tab reads this single constant, so the ceiling can be raised or
# lowered in one place without leaving a stale hardcoded limit behind.
MAX_ACTIVE_BILLS = 5

# Compact *baseline* row height, plus the number of cart rows the bill panel
# reserves room for before it has to scroll. Rows are never shorter than this,
# but they are deliberately not pinned to it: a long product name wraps onto a
# second or third line and grows its own row (see apply_compact_rows +
# resizeRowsToContents), so density never costs readability and no name is ever
# truncated to an ellipsis.
_COMPACT_ROW_HEIGHT = COMPACT_ROW_HEIGHT
_MIN_VISIBLE_ROWS = 10

# Height arithmetic for the widgets hosted *inside* a row (Qty / Discount
# editors, Remove button). A hosted widget is given the whole cell rect minus
# the pixel Qt reserves at the row's bottom for the grid line — the ::item
# padding below applies to painted text, not to cell widgets — so the room
# actually available is _COMPACT_ROW_HEIGHT - _ROW_GRID_LINE. One more pixel is
# then given back on each side so a control sits on the same inset as the text
# in the cells beside it, which is what makes a row read as one aligned line
# rather than a box crammed against its dividers. Deriving the number instead of
# picking one also keeps it from ever exceeding the room available, which is what
# would silently inflate every row past the compact baseline.
_CELL_PADDING_V = 1
_ROW_GRID_LINE = 1
_CELL_INPUT_BORDER = 1
_CELL_INPUT_HEIGHT = _COMPACT_ROW_HEIGHT - _ROW_GRID_LINE - 2 * _CELL_PADDING_V

# Horizontal breathing room inside a painted cell. Shared between the table's
# QSS (_TABLE_QSS) and WrapAnywhereDelegate rather than written twice: the
# delegate paints into the raw cell rect, so it has to deflate by the same
# padding the stylesheet would otherwise have applied, or wrapped text would sit
# on a different inset than the header above it.
_CELL_PADDING_H = 4


_ALIGN_PRODUCT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
_ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter

# Per-column text alignment for the two Billing tables. The Product column stays
# left/top so a wrapped name reads as a normal block of text and keeps the rest
# of its row level with the name's first line. Every other column holds a short,
# single-line value (a unit label, a quantity, a money figure) and is dead
# centered — horizontally *and* vertically, so it sits in the middle of whatever
# height the row grew to when a neighbouring name wrapped.
_RESULTS_ALIGNMENT = {
    0: _ALIGN_PRODUCT,   # Product
    1: _ALIGN_CENTER,    # Unit
    2: _ALIGN_CENTER,    # Purchase Price
    3: _ALIGN_CENTER,    # Price (retail + wholesale, stacked)
}
_CART_ALIGNMENT = {
    0: _ALIGN_CENTER,    # ✓ Packed (cell widget — centered QCheckBox)
    1: _ALIGN_CENTER,    # # (serial number)
    2: _ALIGN_PRODUCT,   # Product
    3: _ALIGN_CENTER,    # Unit
    4: _ALIGN_CENTER,    # Qty (cell widget — see _quantity_editor)
    5: _ALIGN_CENTER,    # Unit Price
    6: _ALIGN_CENTER,    # Discount/Unit (cell widget — see _item_discount_editor)
    7: _ALIGN_CENTER,    # Line Total
    8: _ALIGN_CENTER,    # Remove (cell widget — see refresh_cart)
}


def _align_items(table: QTableWidget, alignment: dict) -> None:
    """Apply the per-column alignment above to every populated cell.

    Replaces the shared ``left_align_items`` helper for these two tables, which
    forced a single alignment across all columns. Cells hosting a widget carry no
    QTableWidgetItem and are skipped — those are centered by their own layout.
    """
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                item.setTextAlignment(alignment.get(col, _ALIGN_PRODUCT))


def _cell_input_height(font) -> int:
    """Height for a control hosted inside a cart row, for the *active* font.

    Normally this is just the row's usable height (:data:`_CELL_INPUT_HEIGHT`).
    It only grows if the cashier has picked a large Typography size whose line
    height would not fit in it — clipping a digit is never acceptable, and a
    taller control is safe here because ``resizeRowsToContents()`` folds hosted
    widgets into the row's height hint, so the row simply grows to match, exactly
    as it does for a wrapped product name.
    """
    line_height = QFontMetrics(font).height()
    return max(_CELL_INPUT_HEIGHT, line_height + 2 * _CELL_INPUT_BORDER)


# Compact item padding plus soft, subtle grid lines/row separators (instead
# of the app-wide theme's darker item border) so the two Billing tables read
# as calm content rather than a grid of heavy-bordered cells. Scoped via
# object names so other QTableWidget instances elsewhere in the app, and
# theme.qss itself, are left untouched. Vertical padding is held at
# _CELL_PADDING_V and the header's own padding is compacted here too — the
# app-wide QHeaderView::section rule (9px) would otherwise spend ~35px of
# viewport on the header alone, costing a full row of visible items.
# There is deliberately no ::item border-bottom: gridline-color already draws
# the row divider, and a border on top of it would both double the line and
# take a second pixel out of every cell's usable height.
_TABLE_QSS = (
    "QTableWidget#{name} {{ gridline-color: #334155;"
    " selection-background-color: {sel_bg}; selection-color: {sel_fg}; }}"
    "QTableWidget#{name}::item {{ padding: {pad}px 4px; }}"
    "QTableWidget#{name}::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}"
    "QTableWidget#{name} QHeaderView::section {{ padding: 3px 4px; }}"
)

# Selection palettes for the two POS tables. The product-search results use a
# yellow highlight (dark text for legibility on yellow) so a matched/selected
# product stands out at a glance while scanning search hits; the current-bill
# cart keeps the app's blue. Both are scoped by object name via _TABLE_QSS, so
# theme.qss's app-wide table selection colour and every other table are
# untouched.
_SEARCH_SELECT_BG = "#FFE066"
_SEARCH_SELECT_FG = "#000000"
_CART_SELECT_BG = "#2563EB"
_CART_SELECT_FG = "#F8FAFC"

# Dark-slate "card" surfaces for the three Billing section panels (Find
# Product, Current Bill, Payment & Checkout) plus the recessed TOTAL card. Set
# on the page itself so it cascades to the QFrame#posCard / QFrame#totalCard
# descendants without touching theme.qss's app-wide rules. The panels are
# plain QFrames (not QGroupBox) so there is no reserved title gap at the top —
# the 10px radius / 16px interior padding come from each card layout's 16px
# content margins. These match the shared dark design system (surface #1E293B,
# border #334155) so the POS page is not a light island inside the dark shell.
_PAGE_QSS = (
    "QFrame#posCard {"
    " background: #1E293B;"
    " border: 1px solid #334155;"
    " border-radius: 10px;"
    "}"
    "QFrame#totalCard {"
    " background: #162032;"
    " border: 1px solid #334155;"
    " border-radius: 8px;"
    "}"
    # Requirement #4 — QComboBox popup dark-theme fix. Qt renders a combo
    # box's *popup* (QAbstractItemView) as a top-level window that does not
    # inherit the parent widget's palette, so without an explicit rule here it
    # falls back to the OS-native white list view even though the combo box
    # itself is correctly styled dark. Scoped to any QComboBox living under
    # this page (there is none directly on POSPage today, but this keeps the
    # page consistent with the rest of the app's Dark Slate theme the moment
    # one is added, and matches the same rule applied in products_page.py).
    "QComboBox QAbstractItemView {"
    " background-color: #1e293b;"
    " color: #f8fafc;"
    " selection-background-color: #0284c7;"
    " selection-color: #ffffff;"
    " border: 1px solid #334155;"
    "}"
)

# Bill tabs ("Bill 1", "Bill 2"...): the app-wide QTabBar::tab rule is sized
# for denser navigation elsewhere and clips/crams this label at its default
# padding, so it is widened here, scoped to this one QTabBar by object name.
_BILL_TABS_QSS = (
    "QTabBar#billTabs::tab {"
    " padding: 6px 16px;"
    " margin-right: 4px;"
    " border: 1px solid #334155;"
    " border-bottom: none;"
    " border-top-left-radius: 6px;"
    " border-top-right-radius: 6px;"
    " background: #162032;"
    " color: #94A3B8;"
    "}"
    "QTabBar#billTabs::tab:selected {"
    " background: #1E293B;"
    " color: #F8FAFC;"
    " font-weight: bold;"
    "}"
    "QTabBar#billTabs::close-button { padding: 2px; }"
)

# Clean, minimal styling for the Qty / Discount cell editors so numbers stay
# centered and fully visible without the heavy default QLineEdit chrome.
# These fields are plain QLineEdit (no spin arrows exist to hide via
# setButtonSymbols, which only applies to QAbstractSpinBox), so the
# no-spin-arrow requirement is already inherently satisfied.
# The height is pinned here as well as in code, because QSS wins: a min-height
# from an app-wide rule overrides what setFixedHeight asks for, and a leftover
# `min-height: 0px` lets the cell layout collapse the field to its content hint.
# Note that Qt's QSS min/max-height size the *content* box, so the border is
# excluded and callers pass the content height, not the widget height — passing
# the widget height here silently renders the field 2px taller than assigned.
_CELL_INPUT_QSS = (
    "QLineEdit#quantityField {{"
    " border: {border}px solid #334155;"
    " background: #0F172A;"
    " color: #F1F5F9;"
    " padding: 0px 4px;"
    " margin: 0px;"
    " min-height: {content}px;"
    " max-height: {content}px;"
    " border-radius: 3px;"
    "}}"
    "QLineEdit#quantityField:focus {{ border: {border}px solid #3B82F6; }}"
)

# The Rs./% toggle that sits beside the per-item discount field. Styled as a
# small flat chip rather than a full SegmentedControl (see widgets.py) — that
# control's own padding/min sizes are tuned for a toolbar-level toggle and
# don't fit inside a compact table row, so this is a plain QToolButton with
# the same border/radius language as the field it sits next to instead.
_DISC_MODE_BUTTON_QSS = (
    "QToolButton#discModeButton {{"
    " border: {border}px solid #334155;"
    " background: #1E293B;"
    " color: #F1F5F9;"
    " font-weight: 600;"
    " padding: 0px 2px;"
    " margin: 0px;"
    " min-height: {content}px;"
    " max-height: {content}px;"
    " border-radius: 3px;"
    "}}"
    "QToolButton#discModeButton:hover {{ background: #263548; border-color: #3B82F6; }}"
    "QToolButton#discModeButton:pressed {{ background: #334155; }}"
)

# Frameless, compact remove button: no default button border/background, just
# a small centered trash icon. Height is pinned for the same reason as the cell
# editors above — with only `min-height: 0px` the top-aligned button shrank to
# its 16px icon hint instead of squaring up with the editors beside it. It has
# no border, so here the content height and the widget height are the same.
_REMOVE_BUTTON_QSS = (
    "QPushButton#rowRemoveButton {{"
    " border: none;"
    " background: transparent;"
    " padding: 0px;"
    " min-height: {content}px;"
    " max-height: {content}px;"
    "}}"
    "QPushButton#rowRemoveButton:hover {{ background: #3F1D24; border-radius: 4px; }}"
)


# Bright red, bold "Clear All" action for the bill summary row: one obvious,
# high-contrast control that empties the whole current bill at once (see
# POSPage._clear_all). Scoped by object name so no other push button on the
# page is restyled.
_CLEAR_ALL_BUTTON_QSS = (
    "QPushButton#clearAllButton {"
    " background: #dc2626;"
    " color: #ffffff;"
    " font-weight: bold;"
    " border: none;"
    " border-radius: 6px;"
    " padding: 6px 14px;"
    "}"
    "QPushButton#clearAllButton:hover { background: #b91c1c; }"
    "QPushButton#clearAllButton:pressed { background: #991b1b; }"
    "QPushButton#clearAllButton:disabled { background: #4C1D1D; color: #7F5D5D; }"
)

# Prominent, full-height checkout action. A dedicated object name (rather than
# the shared #primaryButton) so the success-green treatment and larger 15px/bold
# face + 8px radius can be pinned here without disturbing every other primary
# button in the app. Colours track the emerald success accent (#10B981) so
# "Complete Sale" reads as the confirming, terminal action of the bill.
_CHECKOUT_BUTTON_QSS = (
    "QPushButton#checkoutButton {"
    " background: #10B981;"
    " color: #ffffff;"
    " font-weight: bold;"
    " font-size: 15px;"
    " border: none;"
    " border-radius: 8px;"
    " padding: 10px 20px;"
    " max-width: 220px;"
    " min-height: 48px;"
    "}"
    "QPushButton#checkoutButton:hover { background: #059669; }"
    "QPushButton#checkoutButton:pressed { background: #047857; }"
    "QPushButton#checkoutButton:disabled { background: #14503F; color: #6B8079; }"
)

# Live-search match highlight: the exact substring a cell matched is painted
# with this yellow accent behind it (see WrapAnywhereDelegate.set_highlight_term).
# Case-insensitive, and cleared the instant the search box is emptied. Yellow
# background + black ink (below) is the one shared "search match" look used on
# every screen that highlights matches -- the POS results here, the Products
# inventory search and the Invoices selection -- so a hit reads identically
# app-wide instead of blue on one screen and yellow on another.
_SEARCH_HIGHLIGHT_COLOR = "#FFE066"
# Black ink drawn on the yellow highlight so the matched substring stays crisp
# and legible against the bright accent.
_SEARCH_HIGHLIGHT_TEXT_COLOR = "#000000"

# Req 1 (perf) — the live product search is debounced by this many
# milliseconds so a burst of fast typing triggers at most one heavy DB
# re-query + full results repaint once typing pauses, instead of one per
# keystroke (which pegged CPU and disk I/O). Real-time BILL calculations
# (subtotal/discount/total) are NOT routed through this — they stay instant.
_SEARCH_DEBOUNCE_MS = 200


class WrapAnywhereDelegate(QStyledItemDelegate):
    """Paints cell text that wraps rather than truncating — under any input.

    ``setWordWrap(True)`` + ``setTextElideMode(ElideNone)`` on the table only get
    part of the way there, which is what the ellipsis testing surfaced. Qt's
    built-in item painter wraps on ``QTextOption::WordWrap``, which breaks *at
    word boundaries only*: it will not split a token, so a long name with no
    convenient space in it ("SUPERDELUXECEILINGFAN2000") still overruns its
    column and gets cut. Inserting spaces made it wrap, which is exactly the
    signature of that word-boundary rule.

    This delegate lays the text out itself with ``WrapAtWordBoundaryOrAnywhere``,
    which prefers a word break and falls back to breaking mid-token when a single
    word cannot fit the column. Both ``sizeHint`` and ``paint`` go through the
    same layout helper, so the height the row is given always matches the number
    of lines actually drawn — the two disagreeing is what would clip the last
    line instead of eliding it.

    Cells that host a widget (Qty / Discount editors, Remove button) never reach
    a delegate, and are handled separately in the row builders.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Empty until a live search pushes a term in via set_highlight_term.
        # While set, every case-insensitive match of it is drawn with a
        # highlight background (see _highlight_formats). The cart's delegate is
        # never given a term, so only the searched results table highlights.
        self._highlight_term = ""

    def set_highlight_term(self, term: str) -> None:
        """Set the substring to highlight in painted cells (case-insensitive).

        An empty/whitespace term clears highlighting. Callers repaint the view
        (a live search already rebuilds it), so this only stores the term.
        """
        self._highlight_term = (term or "").strip()

    def _highlight_formats(self, text: str):
        """Format ranges that put a subtle dark-blue accent background behind
        every case-insensitive match of EACH word in the active search term in
        ``text`` (empty list when there is no term or no match) — so "abid
        socket" highlights both "abid" and "socket" independently wherever each
        appears, not just where they occur together. A background/foreground/
        weight format changes no glyph advance widths, so applying these in
        _layout_lines leaves sizeHint's measurement — and therefore the row
        height — exactly as it was without a search.
        """
        term = self._highlight_term
        if not term or not text:
            return []
        haystack = text.lower()
        words = [w for w in term.lower().split() if w]
        formats = []
        for needle in words:
            start = 0
            while True:
                index = haystack.find(needle, start)
                if index < 0:
                    break
                char_format = QTextCharFormat()
                char_format.setBackground(QColor(_SEARCH_HIGHLIGHT_COLOR))
                # Black ink on the yellow match keeps the highlighted
                # substring readable and accented; the surrounding text keeps
                # the light palette pen set in paint().
                char_format.setForeground(QColor(_SEARCH_HIGHLIGHT_TEXT_COLOR))
                # Bold weight makes the matched run stand out from the
                # surrounding text (the directive's font-weight: bold). Padding
                # and border-radius from the spec are CSS box properties that a
                # QTextCharFormat cannot express, so they are intentionally
                # omitted — the accent colour + bold carry the emphasis.
                char_format.setFontWeight(QFont.Weight.Bold)
                fmt_range = QTextLayout.FormatRange()
                fmt_range.start = index
                fmt_range.length = len(needle)
                fmt_range.format = char_format
                formats.append(fmt_range)
                start = index + len(needle)
        return formats

    def _layout_lines(self, text: str, font, width: int, alignment):
        """Break ``text`` to ``width``, returning (QTextLayout, total height).

        The layout is returned mid-flight (already ``endLayout``-ed) so paint can
        draw the very same line breaks that sizeHint measured.
        """
        option = QTextOption(alignment)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout = QTextLayout(text, font)
        layout.setTextOption(option)
        # Live-search highlight (if any): a background-only format behind each
        # match, so the same line breaking and height are produced whether or
        # not a term is active — paint and sizeHint stay in agreement.
        highlight = self._highlight_formats(text)
        if highlight:
            layout.setFormats(highlight)
        metrics = QFontMetrics(font)
        leading = metrics.leading()

        height = 0.0
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            # A column can be sized down to almost nothing mid-resize; a
            # non-positive width would make createLine loop without consuming
            # any characters, so the line width is floored at one pixel.
            line.setLineWidth(max(1, width))
            height += leading
            line.setPosition(QPointF(0.0, height))
            height += line.height()
        layout.endLayout()
        return layout, int(ceil(height))

    def _display_text(self, index) -> str:
        """The cell's text as a string, or "" when there is nothing to draw.

        Qt's DisplayRole can hand back a non-string (or None for an empty cell),
        and QTextLayout requires a str — so the conversion happens in one place
        for both sizeHint and paint.
        """
        value = index.data(Qt.ItemDataRole.DisplayRole)
        return "" if value is None else str(value)

    def _text_alignment(self, index) -> Qt.AlignmentFlag:
        value = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if value is None:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        return Qt.AlignmentFlag(int(value))

    def _wrap_width(self, rect_width: int, index) -> int:
        """Usable text width, falling back to the column's own width.

        Qt hands the delegate a null/near-empty rect on some passes (notably the
        very first measurement, before the view has assigned the column a
        geometry). Wrapping to that width would break the name one character per
        line, so the real section width is used instead — measuring against the
        width the cell will actually have.
        """
        width = rect_width - 2 * _CELL_PADDING_H
        if width > 0:
            return width
        table = self.parent()
        header = table.horizontalHeader() if table is not None else None
        if header is None:
            return 0
        return max(0, header.sectionSize(index.column()) - 2 * _CELL_PADDING_H)

    def sizeHint(self, option, index):
        text = self._display_text(index)
        if not text:
            return super().sizeHint(option, index)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        width = self._wrap_width(style_option.rect.width(), index)
        _layout, height = self._layout_lines(
            text, style_option.font, width, self._text_alignment(index)
        )
        return QSize(
            style_option.rect.width(), height + 2 * _CELL_PADDING_V + _ROW_GRID_LINE
        )

    def paint(self, painter, option, index):
        text = self._display_text(index)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        # The text is drawn by hand below, so it is cleared from the option
        # before the style paints the cell. That keeps the real background,
        # selection highlight and alternating row colour coming from the theme
        # (rather than being reimplemented here) while guaranteeing the style
        # never gets a chance to elide the string itself.
        style_option.text = ""
        style = style_option.widget.style() if style_option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, style_option.widget)
        if not text:
            return

        # Derived from the cell rect and the padding this file owns, rather than
        # from QStyle's SE_ItemViewItemText. That sub-element rect depends on
        # decoration/widget context the option does not always carry, and when
        # that context is missing it comes back a few pixels wide — which would
        # take the wrap width negative and lay the name out one character per
        # line. Neither table uses a checkbox or icon in a text column, so the
        # cell rect minus the padding *is* the text rect.
        rect = style_option.rect.adjusted(
            _CELL_PADDING_H, _CELL_PADDING_V, -_CELL_PADDING_H, -_CELL_PADDING_V
        )
        alignment = self._text_alignment(index)
        # Same width rule as sizeHint (via _wrap_width, hence the padding added
        # back) so the lines drawn are the lines that were measured. If the two
        # ever disagreed the row would be given a height for one line count and
        # painted with another, which is how a last line gets clipped.
        layout, height = self._layout_lines(
            text,
            style_option.font,
            self._wrap_width(rect.width() + 2 * _CELL_PADDING_H, index),
            alignment,
        )

        # Vertical placement of the wrapped block as a whole. AlignTop keeps a
        # tall wrapped row level with its neighbours' first line; AlignVCenter
        # is what dead-centers the short single-line numeric columns.
        y = float(rect.top())
        if alignment & Qt.AlignmentFlag.AlignVCenter:
            y += max(0.0, (rect.height() - height) / 2.0)
        elif alignment & Qt.AlignmentFlag.AlignBottom:
            y += max(0.0, rect.height() - height)

        painter.save()
        # Clipped to the cell so a row that has not yet been re-measured (mid
        # resize, before resizeRowsToContents runs) spills nothing into its
        # neighbour — it simply shows fewer lines for that one frame.
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
        layout.draw(painter, QPointF(float(rect.left()), y))
        painter.restore()


def _trash_icon(color: str = "#dc2626"):
    # Drawn with the same shared painter helpers as app/ui/icons.py rather
    # than adding a new function there, since this change is scoped to
    # pos_page.py only. The crisp-red default matches the mockup's destructive
    # accent (shared with the Clear All button).
    pixmap, painter = _new_painter(color)
    painter.drawRoundedRect(QRectF(5, 6.5, 10, 10.5), 1.2, 1.2)
    painter.drawLine(QPointF(3.5, 6.5), QPointF(16.5, 6.5))
    painter.drawLine(QPointF(8, 6.5), QPointF(8.5, 4.2))
    painter.drawLine(QPointF(12, 6.5), QPointF(11.5, 4.2))
    painter.drawLine(QPointF(8.5, 4.2), QPointF(11.5, 4.2))
    for x in (7.6, 10, 12.4):
        painter.drawLine(QPointF(x, 9), QPointF(x, 14.5))
    return _finish(pixmap, painter)


def _search_icon(color: str = "#64748b"):
    # Leading magnifier drawn into the search field so it reads as a search box
    # at a glance (the trailing clear button is Qt's native one). Muted slate to
    # match the mockup's subtle input affordances.
    pixmap, painter = _new_painter(color)
    painter.drawEllipse(QRectF(4, 4, 8, 8))
    painter.drawLine(QPointF(10.5, 10.5), QPointF(15.5, 15.5))
    return _finish(pixmap, painter)


def _coin_icon(color: str = "#64748b"):
    # Leading currency glyph for the Amount Received field — a coin with a
    # simple currency mark, kept generic so it suits whatever currency symbol
    # Settings is configured with.
    pixmap, painter = _new_painter(color)
    painter.drawEllipse(QRectF(3.5, 3.5, 13, 13))
    painter.drawLine(QPointF(10, 6), QPointF(10, 14))
    painter.drawLine(QPointF(7.5, 8.5), QPointF(12.5, 8.5))
    painter.drawLine(QPointF(7.5, 11.5), QPointF(12.5, 11.5))
    return _finish(pixmap, painter)


def _check_icon(color: str = "#ffffff"):
    # White checkmark for the blue Complete Sale button. Two round-joined
    # strokes rather than a QPolygonF so it needs no extra import.
    pixmap, painter = _new_painter(color)
    painter.drawLine(QPointF(4.5, 10.5), QPointF(8.5, 14.5))
    painter.drawLine(QPointF(8.5, 14.5), QPointF(15.5, 5.5))
    return _finish(pixmap, painter)


class BillCart(Cart):
    """A :class:`Cart` whose per-line discount is entered **per unit**, in
    either of two modes the cashier can flip between per row:

        FLAT    — the typed number is a Rs. amount off one item.
        PERCENT — the typed number is a percentage (0-100) off one item.

    Either way the line discount follows the quantity:

        per-unit money = raw value                    (FLAT)
                       = unit price x raw value / 100  (PERCENT)
        line discount  = per-unit money x quantity
        net line total = (unit price x quantity) - line discount

    e.g. FLAT, 5 @ 1,000 with 100 typed in -> 500 line discount, 4,500 net.
    e.g. PERCENT, 5 @ 1,000 with 10 typed in -> 10% of 1,000 = 100 per unit
    -> 500 line discount, 4,500 net — same result, different entry.

    The raw typed figure is the value the cashier owns, so it is what gets
    stored (in :attr:`unit_discounts`, alongside its mode in
    :attr:`discount_modes`) and the line discount is *always* derived from it.
    That direction matters: it is what makes a later quantity change rescale
    the discount (5 -> 6 items becomes 600, not a stale 500), which storing
    only the line total could not do without back-dividing and drifting on
    the cents.

    ``Cart.item_discount`` keeps its original meaning — the discount for the
    whole line — so the service layer, the invoice/DB schema and receipt
    rendering all stay exactly as they are; only how the number is *entered*
    and kept in sync changes. Both maps are carried on the cart itself rather
    than beside it in the page so they travel with a bill tab automatically.
    Neither the DB schema nor invoices persist which mode a line was entered
    in — only the resulting Rs. amount — so a reloaded invoice (see
    load_invoice_for_edit) always comes back in FLAT mode; that is a display
    detail, never a change to what was actually charged.
    """

    FLAT = "flat"
    PERCENT = "percent"

    def __init__(self):
        super().__init__()
        self.unit_discounts: dict[int, Decimal] = {}
        self.discount_modes: dict[int, str] = {}
        # product_ids the cashier has ticked as "packed / verified" in the cart.
        # Purely a fulfillment aid for the UI (it drives a struck-through, dimmed
        # row) — it is never read by totals() or the sales service, so it can
        # never change what a bill charges. Carried on the cart, like the two
        # discount maps above, so it travels with its bill tab and survives the
        # cart rebuilds a quantity/discount edit triggers.
        self.packed_ids: set[int] = set()

    def unit_discount(self, product_id) -> Decimal:
        """The raw value as typed by the cashier (Rs. or a bare percentage,
        depending on :meth:`discount_mode`) — never the money it resolves to.
        """
        return self.unit_discounts.get(product_id, Decimal("0.00"))

    def discount_mode(self, product_id) -> str:
        return self.discount_modes.get(product_id, self.FLAT)

    def per_unit_money(self, product_id) -> Decimal:
        """The actual Rs. discount one unit of this line carries right now,
        with the raw value resolved through whichever mode is active.
        """
        line = self.lines.get(product_id)
        if line is None:
            return Decimal("0.00")
        raw = self.unit_discount(product_id)
        if self.discount_mode(product_id) == self.PERCENT:
            pct = min(raw, Decimal("100"))
            return (line.unit_price * pct / Decimal("100")).quantize(Decimal("0.01"))
        return min(raw, line.unit_price)

    def _apply_unit_discount(self, product_id) -> None:
        """Re-derive the line discount from the stored per-unit figure.

        Called after anything that changes a line's quantity, since the line
        discount is a function of it.
        """
        line = self.lines.get(product_id)
        if line is None:
            return
        per_unit_money = self.per_unit_money(product_id)
        super().set_item_discount(product_id, (per_unit_money * line.quantity).quantize(Decimal("0.01")))

    def set_unit_discount(self, product_id, discount) -> None:
        line = self.lines.get(product_id)
        if line is None:
            raise ValueError("Product is not in the cart")
        raw = validate_money(discount, "Item discount")
        if raw < 0:
            raise ValueError("Item discount cannot be negative")
        if self.discount_mode(product_id) == self.PERCENT:
            # A percentage discount is capped at 100% of the unit price by
            # definition — anything past that isn't a discount any more.
            if raw > 100:
                raise ValueError("Percentage discount cannot exceed 100%")
        else:
            # Capping flat discount at the unit price is the per-unit
            # equivalent of the base cart's "discount cannot exceed the item
            # total" rule, but it fails earlier and reads far better: the
            # cashier is told the price of one item is the ceiling, instead
            # of being handed a line total to divide.
            if raw > line.unit_price:
                raise ValueError(
                    f"Per-item discount cannot exceed the unit price of {money(line.unit_price)}"
                )
        self.unit_discounts[product_id] = raw
        self._apply_unit_discount(product_id)

    def set_discount_mode(self, product_id, mode) -> None:
        if mode not in (self.FLAT, self.PERCENT):
            raise ValueError("Unknown discount mode")
        line = self.lines.get(product_id)
        if line is None:
            raise ValueError("Product is not in the cart")
        if self.discount_mode(product_id) == mode:
            return
        # Convert the *currently applied* per-unit money into the new mode's
        # units, so flipping the toggle never silently changes the discount
        # already showing on the line — only how the number is entered from
        # here on. (e.g. Rs.100 off a Rs.1,000 item becomes "10" once flipped
        # to %, not a discount that jumps to 0 or clips to 100.)
        current_money = self.per_unit_money(product_id)
        if mode == self.PERCENT:
            if line.unit_price > 0:
                raw = (current_money / line.unit_price * 100).quantize(Decimal("0.01"))
            else:
                raw = Decimal("0.00")
            raw = min(raw, Decimal("100.00"))
        else:
            raw = current_money
        self.discount_modes[product_id] = mode
        self.unit_discounts[product_id] = raw
        self._apply_unit_discount(product_id)

    def set_quantity(self, product_id, quantity) -> None:
        super().set_quantity(product_id, quantity)
        # The typed per-unit discount is unchanged, so the *line* discount has
        # to be recomputed against the new quantity.
        self._apply_unit_discount(product_id)

    def add(self, product, quantity=Decimal("1"), item_discount=Decimal("0"), unit_price=None):
        super().add(product, quantity, item_discount, unit_price)
        # Cart.add resets an existing line's discount to the value passed in, so
        # re-adding a product the cashier had already discounted has to restore
        # its per-unit figure — now against the increased quantity.
        self._apply_unit_discount(product.id)

    def remove(self, product_id) -> None:
        super().remove(product_id)
        # Drop the per-unit figure and its mode with the line, so re-adding the
        # product later doesn't silently inherit a discount the cashier can't
        # see any more.
        self.unit_discounts.pop(product_id, None)
        self.discount_modes.pop(product_id, None)
        # ...and forget it was packed, so a re-added line starts unchecked.
        self.packed_ids.discard(product_id)

    def clear(self) -> None:
        super().clear()
        self.unit_discounts.clear()
        self.discount_modes.clear()
        self.packed_ids.clear()


class POSPage(QWidget):
    # Emitted after a sale is finalized so MainWindow can lazily refresh the
    # data-backed views (Dashboard/Products/Invoices/Customers) the sale
    # affected — instead of re-querying every page on every tab switch.
    sale_completed = pyqtSignal()

    def __init__(self, product_service, sales_service, settings_service, output_service=None, parent=None):
        super().__init__(parent)
        self.products = product_service
        self.sales = sales_service
        self.settings = settings_service
        # 80mm thermal receipt output (height computed dynamically from the
        # bill's actual content — see app/services/invoice_output_service.py,
        # the same ReportLab pipeline the Invoices section uses) for a
        # just-completed sale. Injected by MainWindow (shared with Invoices);
        # defaulted so POSPage can still be constructed standalone (e.g. in
        # tests). All receipt PDF generation/printing is delegated entirely
        # to InvoiceOutputService / invoice_output_service.py — POSPage
        # builds no HTML and does no layout of its own.
        self.output = output_service or InvoiceOutputService()
        # Invoice id of the most recently completed sale. Set in complete_sale;
        # the two receipt actions beneath the payment fields act on it and stay
        # enabled after the cart clears, so its 80mm receipt can still be
        # printed or saved.
        self._last_invoice_id = None
        # Multi-bill support: each tab owns its own Cart (up to
        # MAX_ACTIVE_BILLS); self.cart always points at whichever one is
        # currently active so every other method in this class (add_selected,
        # refresh_cart, complete_sale, etc.) keeps working unchanged against
        # "the active cart".
        self._bills = [BillCart()]
        self.cart = self._bills[0]

        self._quantity_fields = {}
        self._discount_fields = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(14)

        # Card styling for the three section panels below (Find Product, Current
        # Bill, Payment & Checkout) plus the grey TOTAL card — set once here
        # since it cascades to the QFrame#posCard / QFrame#totalCard descendants.
        self.setStyleSheet(_PAGE_QSS)

        content = QHBoxLayout()
        content.setSpacing(14)

        find = QFrame()
        find.setObjectName("posCard")
        find_layout = QVBoxLayout(find)
        find_layout.setContentsMargins(16, 16, 16, 16)
        find_layout.setSpacing(12)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product name...")
        self.search.setClearButtonEnabled(True)
        # Leading magnifier so the field reads as a search box at a glance. The
        # native Qt clear action (a real QToolButton child once
        # setClearButtonEnabled is on) stays as the trailing control and is
        # enlarged from its small default.
        self.search.addAction(_search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        clear_button = self.search.findChild(QToolButton)
        if clear_button is not None:
            clear_button.setIconSize(QSize(18, 18))
        # Blue, "+"-prefixed primary action, matching the mockup's top bar.
        self.add_button = QPushButton("+ Add to Bill")
        self.add_button.setObjectName("primaryButton")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.add_button)
        find_layout.addLayout(search_row)

        # Price mode: a blue segmented control (Retail | Wholesale) rather than a
        # nested card. self.is_wholesale_mode reads the segment state directly (a
        # property, just below) rather than a separately tracked bool, so the two
        # can never drift out of sync. Checked == Wholesale; the default
        # (unchecked) is Retail, shown active/blue.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_caption = QLabel("Price mode")
        mode_caption.setObjectName("muted")
        self.mode_toggle = SegmentedControl("Retail", "Wholesale")
        self.mode_toggle.setToolTip(
            "Retail charges the selling price; Wholesale charges the wholesale price"
        )
        mode_row.addWidget(mode_caption)
        mode_row.addWidget(self.mode_toggle)
        mode_row.addStretch()
        find_layout.addLayout(mode_row)

        # Find Product must expose both cost and selling price so the cashier
        # has the information needed to identify the right product without
        # opening Product Management. The columns are explicitly sized below
        # to avoid a horizontal scrollbar at normal POS resolutions.
        self.results = QTableWidget(0, 4)
        self.results.setObjectName("findProductTable")
        self.results.setHorizontalHeaderLabels(["Product", "Unit", "Purchase Price", "Price"])
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setAlternatingRowColors(True)
        # Long product names wrap onto extra lines and grow their own row (see
        # apply_compact_rows / refresh_products) rather than being cut off with
        # an ellipsis, so the full name is always readable in place.
        apply_compact_rows(self.results)
        # Belt-and-braces with apply_compact_rows: ellipses are never acceptable
        # in either table, so the mode is asserted here at the call site too and
        # can't be lost if the shared helper is ever changed for another caller.
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideNone)
        # Wraps mid-word when a single word cannot fit the column, which plain
        # setWordWrap cannot do. See WrapAnywhereDelegate. Kept as an attribute
        # so the live-search term can be pushed onto it (see refresh_products),
        # which highlights the matched substring in the results.
        self._results_delegate = WrapAnywhereDelegate(self.results)
        self.results.setItemDelegate(self._results_delegate)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.verticalHeader().setVisible(False)
        self._align_results_header()
        self.results.setStyleSheet(_TABLE_QSS.format(name="findProductTable", pad=_CELL_PADDING_V, sel_bg=_SEARCH_SELECT_BG, sel_fg=_SEARCH_SELECT_FG))
        enable_smooth_scrolling(self.results)
        find_layout.addWidget(self.results, 1)
        # Req 1 (perf) — no QGraphicsDropShadowEffect on the POS cards: a
        # blurred drop shadow forces Qt to re-composite the whole card off-
        # screen on every repaint (each keystroke, scroll and live-total
        # update), which was a needless CPU cost on this hot screen. The cards
        # keep their border/rounded look from the stylesheet instead.
        content.addWidget(find, 5)

        bill = QFrame()
        bill.setObjectName("posCard")
        bill_layout = QVBoxLayout(bill)
        bill_layout.setContentsMargins(16, 16, 16, 16)
        bill_layout.setSpacing(12)

        # Multi-bill support: up to MAX_ACTIVE_BILLS active bills, each backed
        # by its own Cart, so a cashier can hold several customers' selections
        # at once without losing any of them when switching.
        bills_row = QHBoxLayout()
        self.bill_tabs = QTabBar()
        self.bill_tabs.setObjectName("billTabs")
        self.bill_tabs.setTabsClosable(True)
        self.bill_tabs.setExpanding(False)
        self.bill_tabs.setStyleSheet(_BILL_TABS_QSS)
        # With up to 5 tabs the strip can outgrow its column on a narrow
        # window; scroll buttons keep every bill reachable instead of
        # squeezing the bill table.
        self.bill_tabs.setUsesScrollButtons(True)
        self.bill_tabs.addTab("Bill 1")
        self.new_bill_button = QPushButton("+ New Bill")
        self.new_bill_button.setObjectName("newBillButton")
        bills_row.addWidget(self.bill_tabs, 1)
        bills_row.addWidget(self.new_bill_button)
        bill_layout.addLayout(bills_row)

        self.table = QTableWidget(0, 9)
        self.table.setObjectName("currentBillTable")
        self.table.setHorizontalHeaderLabels(
            ["✓", "#", "Product", "Unit", "Qty", "Unit Price", "Discount/Unit", "Line Total", "Remove"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # See the results table above: names wrap and the row grows to fit, so
        # a long item on the bill is never truncated.
        apply_compact_rows(self.table)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setItemDelegate(WrapAnywhereDelegate(self.table))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        self._align_cart_header()
        self.table.setStyleSheet(_TABLE_QSS.format(name="currentBillTable", pad=_CELL_PADDING_V, sel_bg=_CART_SELECT_BG, sel_fg=_CART_SELECT_FG))
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        enable_smooth_scrolling(self.table)
        bill_layout.addWidget(self.table, 1)

        # Bill discount lives inline with the TOTAL card in a single row
        # (rather than a standalone row above it) so the summary section
        # takes minimal vertical space.
        summary_row = QHBoxLayout()
        summary_row.addWidget(QLabel("Bill discount"))
        self.discount = QLineEdit("0")
        self.discount.setObjectName("billDiscountField")
        self.discount.setMaximumWidth(120)
        summary_row.addWidget(self.discount)
        # Clear All: one bright-red action that empties the whole current bill
        # at once (see _clear_all), sat between the Bill discount field and the
        # TOTAL card. Disabled while the bill is already empty (set in
        # refresh_cart) so it is only ever offered when there is something to
        # clear.
        self.clear_all_button = QPushButton("Clear All")
        self.clear_all_button.setObjectName("clearAllButton")
        self.clear_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_all_button.setToolTip("Remove every item from the current bill")
        self.clear_all_button.setStyleSheet(_CLEAR_ALL_BUTTON_QSS)
        summary_row.addWidget(self.clear_all_button)
        summary_row.addStretch()

        total = QFrame()
        total.setObjectName("totalCard")
        total_layout = QHBoxLayout(total)
        total_label = QLabel("TOTAL")
        total_label.setObjectName("sectionTitle")
        self.total = QLabel("Rs. 0")
        self.total.setObjectName("totalValue")
        total_layout.addWidget(total_label)
        total_layout.addStretch()
        total_layout.addWidget(self.total)
        summary_row.addWidget(total)
        bill_layout.addLayout(summary_row)
        content.addWidget(bill, 7)
        root.addLayout(content, 1)

        payment = QFrame()
        payment.setObjectName("posCard")
        payment_outer = QHBoxLayout(payment)
        payment_outer.setContentsMargins(16, 16, 16, 16)
        payment_outer.setSpacing(16)

        # Three stacked label-over-input groups (Payment method, Amount
        # received, Customer Name) in a grid, with payment_hint spanning
        # beneath the whole row and the full-height Complete Sale button to
        # the right.
        fields = QGridLayout()
        fields.setHorizontalSpacing(16)
        fields.setVerticalSpacing(6)

        method_label = QLabel("Payment method")
        method_label.setObjectName("muted")
        # Cash/Credit as a blue segmented control (checked == Credit). The
        # service layer still speaks "cash"/"credit"; that mapping lives in the
        # self.payment_method property below, so this widget stays domain-free.
        self.payment_toggle = SegmentedControl("Cash", "Credit")
        self.payment_toggle.setToolTip(
            "Cash sale, or Credit (Udhaar) added to a customer's balance"
        )
        fields.addWidget(method_label, 0, 0)
        fields.addWidget(self.payment_toggle, 1, 0, Qt.AlignmentFlag.AlignLeft)

        received_label = QLabel("Amount received")
        received_label.setObjectName("muted")
        self.paid = QLineEdit("0")
        self.paid.setObjectName("receivedAmountField")
        # Leading currency glyph so the field reads as a money input.
        self.paid.addAction(_coin_icon(), QLineEdit.ActionPosition.LeadingPosition)
        fields.addWidget(received_label, 0, 1)
        fields.addWidget(self.paid, 1, 1)

        customer_label = QLabel("Customer Name")
        customer_label.setObjectName("muted")
        self.customer = QLineEdit()
        self.customer.setObjectName("customerNameField")
        self.customer.setPlaceholderText("Optional for Cash")
        fields.addWidget(customer_label, 0, 2)
        fields.addWidget(self.customer, 1, 2)

        # Hint (computed change / remaining balance) spans beneath the fields.
        self.payment_hint = QLabel("")
        self.payment_hint.setObjectName("muted")
        fields.addWidget(self.payment_hint, 2, 0, 1, 3)

        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(2, 1)

        # 80mm thermal receipt actions for the just-completed sale, on their
        # own row beneath the payment fields. Disabled until a sale is
        # finalized (see complete_sale). The fields grid and this row are
        # stacked in a VBox so the full-height Complete Sale button to the
        # right is preserved. Page height is computed dynamically from the
        # bill's own content by invoice_output_service.py — there is no
        # fixed page length any more, so these labels name only the paper
        # width.
        receipt_row = QHBoxLayout()
        receipt_row.setSpacing(8)
        self.print_receipt_button = QPushButton("Print Receipt")
        self.print_receipt_button.setToolTip("Print the last completed sale as an 80mm thermal receipt")
        self.print_receipt_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.print_receipt_button.setEnabled(False)
        self.save_receipt_button = QPushButton("Generate Receipt PDF")
        self.save_receipt_button.setToolTip("Save the last completed sale as an 80mm thermal receipt PDF")
        self.save_receipt_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_receipt_button.setEnabled(False)
        receipt_row.addWidget(self.print_receipt_button)
        receipt_row.addWidget(self.save_receipt_button)
        receipt_row.addStretch()

        payment_left = QVBoxLayout()
        payment_left.setSpacing(10)
        payment_left.addLayout(fields, 1)
        payment_left.addLayout(receipt_row)
        payment_outer.addLayout(payment_left, 1)

        # Full-height primary action. A dedicated objectName + local QSS give it
        # the tall blue treatment from the mockup (distinct from the standard
        # #primaryButton height); the leading check icon reinforces "done".
        self.complete = QPushButton("Complete Sale")
        self.complete.setObjectName("checkoutButton")
        self.complete.setStyleSheet(_CHECKOUT_BUTTON_QSS)
        self.complete.setIcon(_check_icon("#ffffff"))
        self.complete.setIconSize(QSize(20, 20))
        self.complete.setCursor(Qt.CursorShape.PointingHandCursor)
        # Requirement #2 — sleek, bounded checkout button. The button used to
        # be told to Expand vertically to fill the whole card's height, which
        # is what let it stretch arbitrarily tall on bigger screens; it is now
        # capped to a sensible max-width and given a fixed min-height instead,
        # so it reads as a compact, professional action rather than a slab
        # that grows with the window.
        self.complete.setMinimumWidth(180)
        self.complete.setMaximumWidth(220)
        self.complete.setMinimumHeight(48)
        self.complete.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        payment_outer.addWidget(self.complete, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(payment)

        # Req 1 (perf) — debounce the live search. Every keystroke only
        # (re)starts this single-shot timer; the expensive refresh_products
        # (DB query + full results table rebuild) runs once, _SEARCH_DEBOUNCE_MS
        # after the LAST keystroke, so holding down or typing fast no longer
        # fires a query/repaint per character. The timer is parented to self so
        # it is cleaned up with the page.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self.refresh_products)
        self.search.textChanged.connect(self._search_debounce.start)
        # Fast keyboard loop: Enter in the search box jumps into the results
        # table (row 0) so the cashier never has to reach for the mouse. The
        # rest of the loop (results -> Qty -> Item Discount -> back to search)
        # lives in eventFilter below.
        self.search.returnPressed.connect(self._focus_results_from_search)
        self.results.cellDoubleClicked.connect(self._double_click)
        self.results.installEventFilter(self)
        self.add_button.clicked.connect(self.add_selected)
        self.discount.textChanged.connect(self._live_bill_discount)
        self.discount.editingFinished.connect(self._apply_discount)
        self.discount.installEventFilter(self)
        self.paid.installEventFilter(self)
        self.paid.textChanged.connect(self._update_payment_hint)
        self.payment_toggle.toggled.connect(self._payment_changed)
        self.complete.clicked.connect(self.complete_sale)
        self.print_receipt_button.clicked.connect(self._print_last_receipt)
        self.save_receipt_button.clicked.connect(self._save_last_receipt)
        self.bill_tabs.currentChanged.connect(self._switch_bill)
        self.bill_tabs.tabCloseRequested.connect(self._remove_bill)
        self.new_bill_button.clicked.connect(self._add_bill)
        self.clear_all_button.clicked.connect(self._clear_all)

        self._payment_changed()
        self._apply_dense_metrics()
        self._update_new_bill_button_state()
        self.refresh_products()
        self.refresh()

    @property
    def is_wholesale_mode(self) -> bool:
        # Checked == Wholesale on the Retail/Wholesale toggle.
        return self.mode_toggle.isChecked()

    @property
    def payment_method(self) -> str:
        """The active payment type in the vocabulary the service layer expects.

        The Cash/Credit toggle is generic (checked == the right-hand option), so
        the single place that maps it to the ``"cash"`` / ``"credit"`` strings
        SalesService and the invoice schema use lives here — mirroring the old
        QComboBox item-data of the same two values.
        """
        return "credit" if self.payment_toggle.isChecked() else "cash"

    def _align_header(self, table: QTableWidget, alignment: dict) -> None:
        """Point each header label at the same alignment as its column's data.

        ``QHeaderView.setDefaultAlignment`` is one value for the whole header, so
        the per-column alignments are written onto the header items themselves.
        A centered numeric column under a left-aligned label reads as a
        misalignment, which is the thing this avoids.
        """
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            if item is not None:
                item.setTextAlignment(
                    _ALIGN_CENTER
                    if alignment.get(col) is _ALIGN_CENTER
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )

    def _align_results_header(self) -> None:
        self._align_header(self.results, _RESULTS_ALIGNMENT)

    def _align_cart_header(self) -> None:
        self._align_header(self.table, _CART_ALIGNMENT)

    def eventFilter(self, obj, event):
        if obj is self.results and event.type() == event.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.add_selected()
            return True

        is_enter = event.type() == event.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter,
        )

        # Fast keyboard workflow (the whole loop, no mouse or Tab needed):
        #
        #   search  --Enter-->  results row 0        (returnPressed, wired above)
        #   results --Enter-->  add to bill + focus that row's Qty, selected
        #                                            (the self.results branch above)
        #   Qty     --Enter-->  apply qty, jump to this row's Item Discount, selected
        #   Item Disc --Enter-> apply discount, clear search box, focus search again
        #
        # So after pricing one line the cashier is already back in the search box
        # ready to type the next product. The Total Bill Discount field is
        # deliberately NOT in this loop — it stays mouse-operated, entered only
        # when a bill-wide discount is actually needed.
        #
        # Each branch explicitly calls the same "apply" method its
        # editingFinished signal would have (as the self.results branch already
        # does) and returns True: letting Qt's native Enter handling run on as
        # well would apply the value a second time and move focus a second time.
        if is_enter and obj in self._quantity_fields.values():
            product_id = next((pid for pid, field in self._quantity_fields.items() if field is obj), None)
            if product_id is not None:
                self._apply_row_quantity(product_id, obj)
                # _apply_row_quantity rebuilds the cart (refresh_cart), so the
                # discount editor for this row is a *new* widget — fetch it from
                # the freshly repopulated map rather than one captured earlier.
                discount_field = self._discount_fields.get(product_id)
                if discount_field is not None:
                    discount_field.setFocus()
                    discount_field.selectAll()
            return True

        if is_enter and obj in self._discount_fields.values():
            product_id = next((pid for pid, field in self._discount_fields.items() if field is obj), None)
            if product_id is not None:
                self._apply_row_discount(product_id, obj)
            # End of the loop for this line: clear the search box and hand focus
            # back to it so the next product name can be typed immediately.
            self.search.clear()
            self.search.setFocus()
            return True

        # Bill discount, amount received, and every per-row item discount
        # field share the same zero-as-placeholder behavior (Global
        # Interaction Rules) so a cashier can type straight over a "0.00"
        # instead of deleting it first.
        zero_placeholder_fields = (self.discount, self.paid)
        if event.type() == event.Type.FocusIn and (
            obj in zero_placeholder_fields or obj in self._discount_fields.values()
        ):
            text = obj.text().strip()
            if text in {"0", "0.0", "0.00"}:
                # A default zero is only a placeholder for data entry. Clear it
                # on focus so the user can type the desired value immediately.
                # Meaningful existing values are deliberately left untouched.
                obj.clear()
                return super().eventFilter(obj, event)
            # Requirement: focusing a discount field that already holds a real
            # value selects it all, so the cashier overwrites it in a single
            # keystroke instead of clearing it by hand first. Deferred to the
            # next event-loop tick with a 0ms timer because a mouse click
            # repositions the cursor (clearing any selection) *after* this
            # FocusIn — selecting the text now would be undone immediately.
            # Applies to the bill-wide discount and every per-row item discount
            # field; amount received keeps the clear-only behavior above.
            if obj is self.discount or obj in self._discount_fields.values():
                QTimer.singleShot(0, obj.selectAll)
                return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def _focus_results_from_search(self):
        """Enter in the search box: hand focus to the results table at row 0.

        First step of the fast keyboard loop. With a row selected and the table
        focused, the cashier drives the rest with Up/Down to pick an item and
        Enter to add it (see the self.results branch in eventFilter). No-op when
        the search returned nothing, so Enter on an empty result set doesn't
        strand focus in an empty table.
        """
        if self.results.rowCount() == 0:
            return
        self.results.setCurrentCell(0, 0)
        self.results.setFocus()

    def _double_click(self, *_):
        self.add_selected()

    def selected_product(self):
        row = self.results.currentRow()
        if row < 0:
            return None
        return self.results.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def add_selected(self):
        product = self.selected_product()
        if product is None:
            return
        try:
            ws_price = float(getattr(product, "wholesale_price", 0) or 0)
            retail_price = float(getattr(product, "selling_price", 0) or 0)
            unit_price = (ws_price if ws_price > 0 else retail_price) if self.is_wholesale_mode else None
            self.cart.add(product, Decimal("1"), unit_price=unit_price)
            self.refresh_cart()
            # Fast keyboard workflow: jump straight into the new/updated
            # row's Quantity field with its value pre-selected, so the
            # cashier can type the real quantity immediately with no mouse
            # click needed. Covers double-click, Enter-in-search, and the
            # "Add to Bill" button, since all three call this method.
            qty_field = self._quantity_fields.get(product.id)
            if qty_field is not None:
                qty_field.setFocus()
                qty_field.selectAll()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot add product", str(exc))

    def refresh_products(self):
        # Live search highlighting: push the current term onto the results
        # delegate so the matched substring is highlighted as the rows are
        # drawn. Cleared automatically when the search box is emptied (the
        # delegate treats an empty term as "no highlight").
        self._results_delegate.set_highlight_term(self.search.text())
        search_words = self.search.text().lower().strip().split()
        # Passing the full multi-word text straight to list_products would
        # have the service match it as one literal substring (e.g. "abid
        # socket" wouldn't match "abid pathar socket"). Query with only the
        # first word as a safe superset — any true match must contain it —
        # then AND-filter across every word ourselves below.
        products = self.products.list_products(search_words[0] if search_words else self.search.text())
        products = [
            p for p in products
            if all(word in p.name.lower() for word in search_words)
        ]
        self.results.setRowCount(len(products))
        for row, product in enumerate(products):
            item = QTableWidgetItem(product.name)
            item.setData(Qt.ItemDataRole.UserRole, product)
            item.setToolTip(product.name)
            self.results.setItem(row, 0, item)
            self.results.setItem(row, 1, QTableWidgetItem(UNIT_LABELS.get(product.unit, product.unit)))
            self.results.setItem(row, 2, QTableWidgetItem(money(product.purchase_price)))
            # Search Table Dual-Price Stacked Display: retail on line 1,
            # wholesale on line 2 in "(WS: ...)" secondary form, always shown
            # together regardless of the active mode — the toggle only
            # decides which one add_selected() actually charges. The literal
            # "\n" is a forced break in WrapAnywhereDelegate's QTextLayout
            # (same layout engine the wrapped Product column already relies
            # on), so this needs no delegate changes.
            price_item = QTableWidgetItem(
                f"{money(product.selling_price)}\n(WS: {money(product.wholesale_price)})"
            )
            price_item.setToolTip(
                f"Retail: {money(product.selling_price)}\nWholesale: {money(product.wholesale_price)}"
            )
            self.results.setItem(row, 3, price_item)
        # Product left/top so a wrapped name reads as a block; Unit, Purchase
        # Price and Price (retail + wholesale, stacked) dead centered.
        _align_items(self.results, _RESULTS_ALIGNMENT)
        self._size_product_results_columns()
        # Must run *after* the columns are sized: how many lines a name wraps
        # onto depends on how wide its column ended up, so measuring rows first
        # would size them against the previous layout.
        self.results.resizeRowsToContents()

    def _header_min_width(self, table: QTableWidget, text: str, padding: int = 34) -> int:
        # Column widths below start from a proportion of the viewport, but a
        # proportion alone breaks the moment the cashier picks a larger
        # Settings → Typography size: the pixel share that fit "Purchase
        # Price" at the default font is too narrow for the same text at a
        # bigger one, and the header clips (e.g. "Purchase Price" ->
        # "urchase Price"). Measuring the header's own font guarantees every
        # column stays wide enough for its label at whatever font is active.
        metrics = QFontMetrics(table.horizontalHeader().font())
        return metrics.horizontalAdvance(text) + padding

    def _apply_dense_metrics(self):
        """Give both Billing tables their compact baseline row height and
        reserve a full screenful of bill rows before the table has to scroll.

        Called again from refresh() because the header height it measures
        depends on the active Settings -> Typography size, so the reserved
        viewport stays correct after the cashier changes font size.
        """
        apply_compact_rows(self.table, _COMPACT_ROW_HEIGHT)
        apply_compact_rows(self.results, _COMPACT_ROW_HEIGHT)
        reserve_visible_rows(self.table, _MIN_VISIBLE_ROWS, _COMPACT_ROW_HEIGHT)
        reserve_visible_rows(self.results, _MIN_VISIBLE_ROWS, _COMPACT_ROW_HEIGHT)

    def _size_product_results_columns(self):
        header = self.results.horizontalHeader()
        viewport_width = max(420, self.results.viewport().width())
        # Fixed widths sized to comfortably fit each header label at the
        # active font (Unit ~65px, Purchase Price ~105px) so no header text
        # is ever truncated. The Price column additionally has to fit its
        # widest line of *content* — "(WS: 999,999)" — not just its header,
        # since it now stacks two price lines per cell.
        unit_width = max(65, self._header_min_width(self.results, "Unit"))
        purchase_width = max(105, self._header_min_width(self.results, "Purchase Price"))
        price_width = max(
            95,
            self._header_min_width(self.results, "Price"),
            self._header_min_width(self.results, "(WS: 999,999)", padding=20),
        )
        product_width = max(
            self._header_min_width(self.results, "Product"),
            viewport_width - unit_width - purchase_width - price_width,
        )
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.results.setColumnWidth(0, product_width)
        self.results.setColumnWidth(1, unit_width)
        self.results.setColumnWidth(2, purchase_width)
        self.results.setColumnWidth(3, price_width)

    def _quantity_field_min_width(self) -> int:
        # Enough room for a 4-digit quantity plus the field's own horizontal
        # padding/border, measured against the *active* font so it scales with
        # the chosen Settings -> Typography size instead of a hardcoded pixel
        # guess. This is what guarantees "10", "100", etc. never clip.
        metrics = QFontMetrics(self.table.font())
        return metrics.horizontalAdvance("0000") + 30

    def _size_cart_columns(self):
        header = self.table.horizontalHeader()
        viewport_width = max(640, self.table.viewport().width())
        # Fixed widths sized to fit each header label plus the editors they
        # host, so headers ("#", "Unit Price", "Discount", "Line Total",
        # "Remove") are never truncated and the Qty/Discount inputs are never
        # clipped. "#" only ever needs to fit a couple of digits, so it stays
        # narrow rather than taking space from Product.
        serial_width = max(30, self._header_min_width(self.table, "#", padding=20))
        # Packed/verified checkbox column: only needs to seat a centered
        # checkbox indicator, so it stays as narrow as the serial column.
        check_width = max(34, self._header_min_width(self.table, "✓", padding=20))
        unit_width = max(65, self._header_min_width(self.table, "Unit"))
        qty_width = max(70, self._quantity_field_min_width() + 12, self._header_min_width(self.table, "Qty"))
        price_width = max(90, self._header_min_width(self.table, "Unit Price"))
        disc_width = max(
            95,
            self._quantity_field_min_width() + self._discount_mode_button_min_width() + 18,
            self._header_min_width(self.table, "Discount/Unit"),
        )
        total_width = max(100, self._header_min_width(self.table, "Line Total"))
        remove_width = max(50, self._header_min_width(self.table, "Remove"))
        fixed = {3: unit_width, 4: qty_width, 5: price_width, 6: disc_width, 7: total_width, 8: remove_width}
        product_width = max(
            self._header_min_width(self.table, "Product"),
            viewport_width - check_width - serial_width - sum(fixed.values()),
        )
        for col in range(9):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, check_width)
        self.table.setColumnWidth(1, serial_width)
        self.table.setColumnWidth(2, product_width)
        for col, width in fixed.items():
            self.table.setColumnWidth(col, width)

    def _live_row_quantity(self, product_id: int, field: QLineEdit):
        if not field.text().strip() or product_id not in self.cart.lines:
            return
        try:
            self.cart.set_quantity(product_id, field.text())
            self._refresh_totals_only()
        except ValueError:
            return

    def _apply_row_quantity(self, product_id: int, field: QLineEdit):
        if product_id not in self.cart.lines:
            return
        try:
            self.cart.set_quantity(product_id, field.text())
            self.refresh_cart()
        except ValueError as exc:
            QMessageBox.warning(self, "Quantity", str(exc))
            self.refresh_cart()

    def _quantity_editor(self, line):
        # Quantity is edited by typing directly into the field. The old +/-
        # stepper buttons were removed from the cart rows; direct entry is the
        # single, unambiguous way to change a line quantity.
        widget = QWidget()
        layout = QHBoxLayout(widget)
        # Zero vertical margin: the editor is already sized to exactly the room
        # a single-line cell has (see _cell_input_height), so any top/bottom
        # margin would push the cell past the row and inflate it. When a
        # neighbouring name wraps and the row grows, the surplus goes to the
        # AlignCenter below instead of to margins.
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(0)

        field = QLineEdit(quantity(line.quantity))
        # Center horizontally and vertically so multi-digit values sit dead
        # center in the field and are never clipped at the top/bottom edges.
        field.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        field.setObjectName("quantityField")
        # Width is driven by the active font (see _quantity_field_min_width)
        # and the field is given the full column via the stretch factor below
        # rather than a fixed maximum, so multi-digit quantities like "10" or
        # "100" always render fully without clipping or horizontal overflow.
        field.setMinimumWidth(self._quantity_field_min_width())
        cell_height = _cell_input_height(field.font())
        field.setFixedHeight(cell_height)
        # Clean, minimal border/padding instead of the heavy default QLineEdit
        # chrome, so the number stays fully visible and isn't squeezed.
        field.setStyleSheet(_CELL_INPUT_QSS.format(
            content=cell_height - 2 * _CELL_INPUT_BORDER, border=_CELL_INPUT_BORDER))

        # Fast keyboard workflow (see eventFilter): Enter here applies the
        # quantity and jumps to this row's Item Discount field.
        field.installEventFilter(self)

        field.textChanged.connect(lambda _text, pid=line.product_id, editor=field: self._live_row_quantity(pid, editor))
        field.editingFinished.connect(lambda pid=line.product_id, editor=field: self._apply_row_quantity(pid, editor))

        layout.addWidget(field, 1)
        # Dead center, horizontally and vertically: the field keeps its fixed
        # single-line height, and when a wrapped product name grows the row the
        # editor sits in the middle of that taller cell rather than being pinned
        # to its top edge.
        layout.setAlignment(field, Qt.AlignmentFlag.AlignCenter)
        self._quantity_fields[line.product_id] = field
        return widget

    @staticmethod
    def _parse_discount_text(text: str, current_mode: str = BillCart.FLAT) -> tuple[str, str]:
        """Requirement #1 — dual discount system, typed directly.

        Reads the raw text a cashier typed into a Discount/Unit cell and
        decides *both* the mode and the value from it, so the cell doubles as
        the Rs./% toggle. A trailing '%' (e.g. "10%") is always a percentage
        of the unit price. A *bare* number carries no marker, so it inherits
        ``current_mode`` — whatever the Rs./% button is currently showing —
        rather than always meaning a flat Rs. amount: that is what lets
        selecting % mode and typing "10" compute 10% directly, without the
        cashier also typing the '%'. The mode_button beside the field still
        works exactly as before (clicking it flips the mode for whatever
        number is already there), and typing a trailing '%' still forces
        percentage mode regardless of the current toggle.
        """
        stripped = text.strip()
        if stripped.endswith("%"):
            return BillCart.PERCENT, stripped[:-1].strip() or "0"
        return current_mode, stripped or "0"

    def _apply_parsed_discount(self, product_id: int, field: QLineEdit) -> None:
        mode, value = self._parse_discount_text(field.text(), self.cart.discount_mode(product_id))
        if self.cart.discount_mode(product_id) != mode:
            # set_discount_mode converts the *currently applied* per-unit
            # money into the new mode's units first (see BillCart docstring),
            # which is immediately overwritten by set_unit_discount below —
            # harmless, and it keeps this path going through the same,
            # already-validated mode-switch logic the toggle button uses
            # rather than poking discount_modes directly.
            self.cart.set_discount_mode(product_id, mode)
        self.cart.set_unit_discount(product_id, value)

    def _live_row_discount(self, product_id: int, field: QLineEdit):
        if product_id not in self.cart.lines:
            return
        try:
            self._apply_parsed_discount(product_id, field)
            self._refresh_totals_only()
        except ValueError:
            return

    def _apply_row_discount(self, product_id: int, field: QLineEdit):
        if product_id not in self.cart.lines:
            return
        try:
            self._apply_parsed_discount(product_id, field)
            self.refresh_cart()
        except ValueError as exc:
            QMessageBox.warning(self, "Item discount", str(exc))
            self.refresh_cart()

    def _line_discount_tooltip(self, line) -> str:
        """Spell out the per-unit -> line arithmetic for one cart row.

        The column holds the per-unit figure, so the resulting line discount is
        surfaced here rather than in a column of its own — that keeps the check
        available on demand without spending horizontal room on a number the
        cashier only occasionally needs to confirm.
        """
        raw = self.cart.unit_discount(line.product_id)
        if raw <= 0:
            return "Discount for a single item (line discount = this x quantity)"
        per_unit_money = self.cart.per_unit_money(line.product_id)
        if self.cart.discount_mode(line.product_id) == BillCart.PERCENT:
            return (
                f"{quantity(raw)}% off each item ({money(per_unit_money)})\n"
                f"Line discount: {money(per_unit_money)} x {quantity(line.quantity)} = {money(line.item_discount)}"
            )
        return (
            f"{money(per_unit_money)} off each item\n"
            f"Line discount: {money(per_unit_money)} x {quantity(line.quantity)} = {money(line.item_discount)}"
        )

    def _discount_mode_button_min_width(self) -> int:
        # Enough room for the wider of the two labels ("100%") plus the
        # button's own padding/border, measured against the active font for
        # the same reason _quantity_field_min_width is.
        metrics = QFontMetrics(self.table.font())
        return metrics.horizontalAdvance("100%") + 14

    def _toggle_row_discount_mode(self, product_id: int):
        if product_id not in self.cart.lines:
            return
        new_mode = (
            BillCart.PERCENT if self.cart.discount_mode(product_id) == BillCart.FLAT else BillCart.FLAT
        )
        try:
            self.cart.set_discount_mode(product_id, new_mode)
        except ValueError as exc:
            QMessageBox.warning(self, "Item discount", str(exc))
        # A mode flip changes how the existing raw number resolves to money,
        # so the row's line total (and the grand total) needs the same
        # rebuild a typed discount gets — full refresh_cart(), not the
        # lighter _refresh_totals_only, since the field's own displayed text
        # (the raw value) also has to change to match the new mode.
        self.refresh_cart()

    def _item_discount_editor(self, line):
        # Requirement #5: every bill line carries its own discount, typed
        # directly into this per-row field, in either of two modes toggled
        # with the small Rs./% button beside it. The value is the discount for
        # a *single* item — the line discount is derived as (per-unit x
        # quantity) by BillCart — so entering 100 in Rs. mode against 5 items
        # takes 500 off the line, and entering 10 in % mode takes 10% of the
        # unit price off each of those 5 items instead. Editing either the
        # value or the mode recomputes that line's total and the grand total
        # live, in step with the bill-wide discount below the table.
        widget = QWidget()
        layout = QHBoxLayout(widget)
        # Zero vertical margin — see _quantity_editor.
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(3)

        mode = self.cart.discount_mode(line.product_id)
        cell_height = _cell_input_height(self.table.font())

        mode_button = QToolButton()
        mode_button.setObjectName("discModeButton")
        mode_button.setText("%" if mode == BillCart.PERCENT else "Rs.")
        mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Kept out of the Tab/Enter keyboard loop (see eventFilter) — it's a
        # mouse-operated toggle, not a stop on the fast per-row entry path.
        mode_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        mode_button.setFixedWidth(self._discount_mode_button_min_width())
        mode_button.setFixedHeight(cell_height)
        mode_button.setStyleSheet(_DISC_MODE_BUTTON_QSS.format(
            content=cell_height - 2 * _CELL_INPUT_BORDER, border=_CELL_INPUT_BORDER))
        mode_button.setToolTip(
            "Click to switch this item's discount between a flat Rs. amount and a %"
            " — or just type e.g. \"10%\" directly into the field for a percentage,"
            " or a plain number like \"30\" for a flat Rs. amount."
        )
        mode_button.clicked.connect(
            lambda _checked=False, pid=line.product_id: self._toggle_row_discount_mode(pid)
        )

        # A percent-mode line is redisplayed with a trailing '%' so the field's
        # own text stays a faithful round-trip of what it means: if the
        # cashier hits Enter without retyping anything, _parse_discount_text
        # must read the same mode straight back out of the text, not silently
        # fall back to FLAT because the '%' wasn't there to read.
        _raw_discount_text = money_input(self.cart.unit_discount(line.product_id))
        if mode == BillCart.PERCENT:
            _raw_discount_text += "%"
        field = QLineEdit(_raw_discount_text)
        field.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        field.setObjectName("quantityField")
        field.setMinimumWidth(self._quantity_field_min_width())
        field.setFixedHeight(cell_height)
        # Clean, minimal border/padding instead of the heavy default QLineEdit
        # chrome, so the number stays centered and fully visible.
        field.setStyleSheet(_CELL_INPUT_QSS.format(
            content=cell_height - 2 * _CELL_INPUT_BORDER, border=_CELL_INPUT_BORDER))
        # Shows the resulting line discount, so the per-unit entry can be
        # verified without a column of its own.
        field.setToolTip(self._line_discount_tooltip(line))
        # Share the zero-as-placeholder behavior (see eventFilter) so a default
        # "0" clears on focus and the cashier can type the amount straight in.
        field.installEventFilter(self)

        field.textChanged.connect(lambda _text, pid=line.product_id, editor=field: self._live_row_discount(pid, editor))
        field.editingFinished.connect(lambda pid=line.product_id, editor=field: self._apply_row_discount(pid, editor))

        layout.addWidget(mode_button, 0)
        layout.addWidget(field, 1)
        # Dead center within the row's full height — see _quantity_editor.
        layout.setAlignment(mode_button, Qt.AlignmentFlag.AlignCenter)
        layout.setAlignment(field, Qt.AlignmentFlag.AlignCenter)
        self._discount_fields[line.product_id] = field
        return widget

    def _sync_paid_with_total_if_cash(self):
        # Auto-fill "Amount received" with the current total whenever
        # payment method is Cash, since finalize_sale already requires cash
        # sales to be paid in full — this just saves retyping the total for
        # the common full-cash sale (Feature: Auto-Fill Amount Received).
        if self.payment_method != "cash":
            return
        try:
            total = self.cart.totals()[2]
        except ValueError:
            return
        self.paid.blockSignals(True)
        self.paid.setText(money_input(total))
        self.paid.blockSignals(False)
        self._update_payment_hint()

    def _live_bill_discount(self, text):
        try:
            self.cart.set_bill_discount(text.strip() or "0")
            self._refresh_totals_only()
        except ValueError:
            return

    def _apply_discount(self):
        try:
            self.cart.set_bill_discount(self.discount.text().strip() or "0")
            self.refresh_cart()
        except ValueError as exc:
            QMessageBox.warning(self, "Discount", str(exc))

    def _refresh_totals_only(self):
        for row, line in enumerate(self.cart.lines.values()):
            if row < self.table.rowCount():
                item = QTableWidgetItem(money(line.line_total))
                item.setTextAlignment(_CART_ALIGNMENT[7])
                # A packed row's line total is rebuilt here on every live
                # recalculation, so re-stamp the struck-through, dimmed look onto
                # the fresh cell (the checkbox state itself lives on the cart).
                if line.product_id in self.cart.packed_ids:
                    font = QFont(self.table.font())
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(self._PACKED_TEXT_COLOR)
                self.table.setItem(row, 7, item)
                # The line discount moves with both the per-unit figure and the
                # quantity, so the breakdown has to be re-stated on every live
                # recalculation, not only when the row is rebuilt.
                field = self._discount_fields.get(line.product_id)
                if field is not None:
                    field.setToolTip(self._line_discount_tooltip(line))
        try:
            self.total.setText(self._currency(self.cart.totals()[2]))
        except ValueError:
            self.total.setText("Invalid discount")
        self._sync_paid_with_total_if_cash()
        self._update_payment_hint()
        # Re-measured even on this lighter path: with eliding off, a line total
        # that grows past its column (a large quantity pushing it to
        # "1,234,567.00") now wraps onto a second line instead of being cut, and
        # the row has to be given the height for it.
        self.table.resizeRowsToContents()

    def _sync_all_inputs(self):
        # Complete Sale must not depend on the cashier having tabbed out of
        # whichever field they were last typing in (Section 14) — quantity,
        # every row's item discount, and the bill discount are all synced
        # from their current on-screen text before the sale is finalized.
        for pid, field in list(self._quantity_fields.items()):
            if field is not None and field.text().strip():
                self.cart.set_quantity(pid, field.text())
        for pid, field in list(self._discount_fields.items()):
            if field is not None and pid in self.cart.lines:
                self._apply_parsed_discount(pid, field)
        self.cart.set_bill_discount(self.discount.text().strip() or "0")

    def refresh_cart(self):
        # Performance: the cart is rebuilt from scratch on every add, remove,
        # quantity/discount edit and bill switch, and each row carries three
        # cell widgets (Qty editor, Discount editor, Remove button). For a
        # 50–100 line bill that is a lot of widget churn, and letting the table
        # repaint after every setItem/setCellWidget (and fire table-level
        # signals per cell) is what made adding or deleting many items lag and
        # briefly freeze. Freeze painting and mute the table's own signals for
        # the whole rebuild so it repaints exactly once, at the end, instead of
        # once per cell.
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self._quantity_fields = {}
            self._discount_fields = {}
            self.table.setRowCount(len(self.cart.lines))
            for row, line in enumerate(self.cart.lines.values()):
                # Serial number: plain 1, 2, 3... in display order, centered.
                # Purely a display index — it doesn't touch product_id/data roles,
                # so removing a middle row and re-running refresh_cart just
                # renumbers the remaining rows cleanly.
                # Packed / verified checkbox (col 0). Ticking it strikes through
                # and dims the whole row so the cashier can check items off as
                # they're packed. It only ever drives the row's appearance — the
                # value is stored on cart.packed_ids and is never read by
                # totals(), so it can never alter the bill (Area 3 requirement).
                check = QCheckBox()
                check.setChecked(line.product_id in self.cart.packed_ids)
                check.setCursor(Qt.CursorShape.PointingHandCursor)
                check.setToolTip("Mark this item as packed / verified")
                check.stateChanged.connect(
                    lambda state, pid=line.product_id: self._on_packed_toggled(
                        pid, state == Qt.CheckState.Checked
                    )
                )
                check_cell = QWidget()
                check_layout = QHBoxLayout(check_cell)
                check_layout.setContentsMargins(0, 0, 0, 0)
                check_layout.addStretch()
                check_layout.addWidget(check)
                check_layout.addStretch()
                check_layout.setAlignment(check, Qt.AlignmentFlag.AlignCenter)
                self.table.setCellWidget(row, 0, check_cell)
                # Serial number: plain 1, 2, 3... in display order, centered.
                # Purely a display index — it doesn't touch product_id/data roles,
                # so removing a middle row and re-running refresh_cart just
                # renumbers the remaining rows cleanly.
                serial_item = QTableWidgetItem(str(row + 1))
                serial_item.setTextAlignment(_CART_ALIGNMENT[1])
                self.table.setItem(row, 1, serial_item)
                product_item = QTableWidgetItem(line.product_name)
                product_item.setData(Qt.ItemDataRole.UserRole, line.product_id)
                # The name wraps rather than truncating, so the tooltip is a
                # convenience (e.g. while the column is narrow), not the only way
                # to read it.
                product_item.setToolTip(line.product_name)
                self.table.setItem(row, 2, product_item)
                self.table.setItem(row, 3, QTableWidgetItem(UNIT_LABELS.get(line.unit, line.unit)))
                self.table.setCellWidget(row, 4, self._quantity_editor(line))
                self.table.setItem(row, 5, QTableWidgetItem(money(line.unit_price)))
                self.table.setCellWidget(row, 6, self._item_discount_editor(line))
                line_total_item = QTableWidgetItem(money(line.line_total))
                # Net of the per-unit discount: (unit price - per-unit) x quantity.
                line_total_item.setToolTip(self._line_discount_tooltip(line))
                self.table.setItem(row, 7, line_total_item)
                remove = QPushButton()
                remove.setObjectName("rowRemoveButton")
                remove.setIcon(_trash_icon())
                remove.setIconSize(QSize(16, 16))
                remove.setCursor(Qt.CursorShape.PointingHandCursor)
                remove.setToolTip("Remove this item from the bill")
                # Square, and sized to the same usable cell height as the row
                # editors so the button can't be the thing that inflates a row.
                # The height goes through the stylesheet as well as setFixedSize
                # because QSS wins over both of setFixedSize's bounds.
                button_height = _cell_input_height(remove.font())
                remove.setStyleSheet(_REMOVE_BUTTON_QSS.format(content=button_height))
                remove.setFixedSize(button_height, button_height)
                remove.clicked.connect(lambda _=False, pid=line.product_id: self._remove(pid))
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.addStretch()
                cell_layout.addWidget(remove)
                cell_layout.addStretch()
                # Dead center in both axes: the surrounding stretches center it
                # horizontally, and AlignCenter keeps it in the middle of the row's
                # height when a wrapped product name has made that row taller.
                cell_layout.setAlignment(remove, Qt.AlignmentFlag.AlignCenter)
                self.table.setCellWidget(row, 8, cell)
                # Restore the struck-through, dimmed look for a row that was
                # already ticked as packed before this rebuild.
                if line.product_id in self.cart.packed_ids:
                    self._apply_packed_row_style(row, True)

            # #, Unit, Unit Price and Line Total dead centered, matching their
            # centered headers; Product left/top so a wrapped name keeps its own
            # row's text reading as a block. Qty, Discount/Unit and Remove host
            # widgets and are centered by their own layouts above.
            _align_items(self.table, _CART_ALIGNMENT)
            try:
                self.total.setText(self._currency(self.cart.totals()[2]))
            except ValueError:
                self.total.setText("Invalid discount")
            self._sync_paid_with_total_if_cash()
            self._update_payment_hint()
            self._size_cart_columns()
            # Run last, once the columns are final: a wrapped product name only
            # knows how many lines it needs after its column width is known.
            self.table.resizeRowsToContents()
        finally:
            # Always lift the freeze and unmute signals — even if a rebuild step
            # raised — so the table can never be left blank/unpainted, and the
            # single deferred repaint happens here.
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        # Clear All only makes sense when the bill actually has something in it.
        self.clear_all_button.setEnabled(bool(self.cart.lines))

    # Columns that hold a plain text item (not a hosted widget) and so can carry
    # the struck-through, dimmed "packed" styling. The checkbox (0), Qty (4),
    # Discount/Unit (6) and Remove (8) cells host widgets and are left untouched.
    _PACKED_TEXT_COLUMNS = (1, 2, 3, 5, 7)
    _PACKED_TEXT_COLOR = QColor("#64748b")

    def _on_packed_toggled(self, product_id, packed):
        # Record the packed/verified state on the cart (so it survives the cart
        # rebuilds a quantity/discount edit triggers) and restyle just this row.
        # This path deliberately never calls refresh_cart or touches the cart's
        # lines/discounts — packing an item only changes how its row looks, so
        # the bill's subtotals and grand total are left exactly as they were.
        if packed:
            self.cart.packed_ids.add(product_id)
        else:
            self.cart.packed_ids.discard(product_id)
        row = self._row_for_product(product_id)
        if row is not None:
            self._apply_packed_row_style(row, packed)

    def _row_for_product(self, product_id):
        # The Product cell (col 2) carries the product_id in its UserRole, so a
        # row can be found by identity no matter what its current serial is.
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == product_id:
                return row
        return None

    def _apply_packed_row_style(self, row, packed):
        # Base the font on the table's own font so only strike-out is toggled —
        # never the family/size — keeping packed and unpacked rows visually in
        # step under any Typography setting.
        font = QFont(self.table.font())
        font.setStrikeOut(packed)
        for col in self._PACKED_TEXT_COLUMNS:
            item = self.table.item(row, col)
            if item is None:
                continue
            item.setFont(font)
            if packed:
                item.setForeground(self._PACKED_TEXT_COLOR)
            else:
                # Drop the override so the cell returns to the theme's default
                # text colour instead of a hard-coded one.
                item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _currency(self, value):
        settings = self.settings.get_settings()
        return f"{settings.currency_symbol} {money(value)}"

    def _remove(self, product_id):
        self.cart.remove(product_id)
        self.refresh_cart()

    def _clear_all(self):
        """Empty the entire current bill at once, then refresh the UI.

        A single high-visibility action for wiping a bill (e.g. a cancelled
        sale) instead of removing rows one at a time. Guarded by a confirmation
        — like every other destructive action in the app — because it discards
        the whole cart; a no-op on an already-empty bill (the button is also
        disabled in that state).
        """
        if not self.cart.lines:
            return
        confirm = QMessageBox.question(
            self,
            "Clear All",
            "Remove all items from the current bill?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.cart.clear()
        # Cart.clear() already zeroes the bill discount; mirror that in the
        # field so the on-screen value matches the now-empty cart.
        self.discount.setText("0")
        self.refresh_cart()

    def _update_new_bill_button_state(self):
        at_limit = len(self._bills) >= MAX_ACTIVE_BILLS
        self.new_bill_button.setEnabled(not at_limit)
        self.new_bill_button.setToolTip(
            f"{MAX_ACTIVE_BILLS} bills are already open — close one to start another"
            if at_limit
            else f"Start another bill (up to {MAX_ACTIVE_BILLS})"
        )

    def _renumber_bill_tabs(self):
        # Keep labels contiguous ("Bill 1", "Bill 2"...) after a tab in the
        # middle is closed, rather than leaving a gap like "Bill 1", "Bill 3".
        self.bill_tabs.blockSignals(True)
        for i in range(self.bill_tabs.count()):
            self.bill_tabs.setTabText(i, f"Bill {i + 1}")
        self.bill_tabs.blockSignals(False)

    def _add_bill(self):
        if len(self._bills) >= MAX_ACTIVE_BILLS:
            return
        self._bills.append(BillCart())
        self.bill_tabs.addTab(f"Bill {len(self._bills)}")
        self._update_new_bill_button_state()
        # Selecting the new tab triggers _switch_bill via currentChanged.
        self.bill_tabs.setCurrentIndex(self.bill_tabs.count() - 1)

    def _remove_bill(self, index):
        # Always keep at least one bill open — closing the last remaining
        # tab would leave the cashier with no active cart at all.
        if len(self._bills) <= 1 or index < 0 or index >= len(self._bills):
            return
        self._bills.pop(index)
        self.bill_tabs.removeTab(index)
        self._renumber_bill_tabs()
        self._update_new_bill_button_state()
        # removeTab already moves currentChanged to the new current index
        # (and fires _switch_bill for it) when the closed tab was active; if
        # a non-active tab was closed instead, the active cart is unchanged
        # and the on-screen bill doesn't need to be rebuilt.

    def _switch_bill(self, index):
        if index < 0 or index >= len(self._bills):
            return
        self.cart = self._bills[index]
        self.discount.setText(money_input(self.cart.bill_discount))
        self.refresh_cart()

    def _update_payment_hint(self, *_):
        # Cash requires *exact* payment (see SalesService.finalize_sale:
        # "Cash sale requires full payment") rather than allowing overpayment
        # with change — so the useful live signal is how much more is
        # needed, not a change amount. Credit allows any partial payment up
        # to the total, so the useful signal there is what stays on Udhaar.
        # This is a read-only display of the existing rule, not a new one.
        try:
            total = self.cart.totals()[2]
        except ValueError:
            self.payment_hint.setText("")
            return
        try:
            paid = Decimal(self.paid.text().strip() or "0")
        except Exception:
            self.payment_hint.setText("")
            return
        diff = total - paid
        credit = self.payment_method == "credit"
        if credit:
            if paid <= 0:
                self.payment_hint.setText(f"Full {money(total)} added to Udhaar")
            elif diff > 0:
                self.payment_hint.setText(f"Remaining on Udhaar: {money(diff)}")
            else:
                self.payment_hint.setText("Fully paid — nothing added to Udhaar")
        else:
            if diff > 0:
                self.payment_hint.setText(f"Remaining to collect: {money(diff)}")
            elif diff < 0:
                self.payment_hint.setText(f"Over by {money(-diff)} — cash sale needs the exact amount")
            else:
                self.payment_hint.setText("Exact amount ✓")

    def _payment_changed(self, *_):
        # Accepts and ignores the toggled(bool) argument so it can be used both
        # as the toggle's slot and called directly (e.g. from __init__ and
        # load_invoice_for_edit).
        credit = self.payment_method == "credit"
        self.customer.setEnabled(True)
        self.customer.setPlaceholderText(
            "Required for Credit (Udhaar)" if credit else "Optional for Cash"
        )
        if credit:
            self.customer.setToolTip("Customer name is required for Credit (Udhaar)")
            # A direct style override (rather than only a tooltip/placeholder)
            # gives switching to Credit a visible state change a cashier
            # notices without reading text (Section 12).
            self.customer.setStyleSheet("border: 1.5px solid #D97706; background: #2A2417; color: #F8FAFC;")
        else:
            self.customer.setToolTip("Optional customer name for a Cash sale")
            self.customer.setStyleSheet("")
        self._sync_paid_with_total_if_cash()
        self._update_payment_hint()

    def complete_sale(self):
        try:
            self._sync_all_inputs()
            self._apply_discount()
            payment = self.payment_method
            customer = self.customer.text().strip() or None
            total = self.cart.totals()[2]
            paid = self.paid.text().strip() or "0"
            invoice_id, number = self.sales.finalize_sale(
                self.cart,
                payment,
                paid,
                customer_name=customer,
            )
            # Remember the finalized sale so its 80mm receipt can be printed or
            # saved from the actions beneath the payment fields; they stay
            # enabled after the cart is cleared below, acting on this invoice.
            self._last_invoice_id = invoice_id
            self.print_receipt_button.setEnabled(True)
            self.save_receipt_button.setEnabled(True)
            QMessageBox.information(self, "Sale completed", f"Sale {number} completed successfully.")
            self.cart.clear()
            self.paid.setText("0")
            self.customer.clear()
            self.discount.setText("0")
            self.refresh_cart()
            # Tell MainWindow the sale landed so it can mark the affected views
            # stale (lazy-refreshed on next visit); keeps tab switching instant.
            self.sale_completed.emit()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot complete sale", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Sale failed", str(exc))

    def _last_receipt_invoice(self):
        """Load the fully-populated invoice for the last completed sale.

        Returns None when there is no completed sale yet, or (after warning)
        when the invoice can no longer be loaded. The receipt buttons are only
        enabled once a sale has been finalized, so this is a defensive guard.
        """
        if self._last_invoice_id is None:
            return None
        invoice = self.sales.get_invoice(self._last_invoice_id)
        if invoice is None:
            QMessageBox.warning(
                self, "Receipt unavailable", "The last sale could not be loaded for printing."
            )
            return None
        # Attach the real customer-ledger (khaata) figures so the receipt can
        # print the account breakdown — previous balance, total payable
        # (today + previous), payment method / received, and net remaining
        # balance. These are computed read-only in one open session by
        # SalesService.receipt_context (no fabricated numbers). A failure here
        # must never stop the cashier printing the sale, so it degrades to
        # "no account block" rather than raising.
        try:
            invoice.receipt_ledger_context = self.sales.receipt_context(self._last_invoice_id)
        except Exception:
            invoice.receipt_ledger_context = None
        return invoice

    def _print_last_receipt(self):
        """Print the most recently completed sale as an 80mm thermal receipt.

        All PDF layout and native printing logic lives in InvoiceOutputService
        (app/services/invoice_output_service.py) — the same ReportLab
        pipeline used for Invoices, so the printed receipt is the exact same
        professional layout as an Invoices-section PDF, not the old HTML
        rendering. This method only loads the invoice and reports the
        outcome; it builds no layout and touches no printer/PDF API
        directly. The service sends the generated PDF straight to the
        Windows default printer (Req 3 — silent, with no web browser / PDF
        viewer / OS file-association handoff); if no default printer is
        configured or the print fails, that surfaces as a warning.
        """
        invoice = self._last_receipt_invoice()
        if invoice is None:
            return
        try:
            printed = self.output.generate_and_print(invoice, self.settings.get_settings(), "80mm")
            if printed:
                QMessageBox.information(
                    self, "Print started", "Receipt sent to the printer."
                )
        except (InvoiceOutputError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Receipt output failed", str(exc))

    def _save_last_receipt(self):
        """Save the most recently completed sale as an 80mm thermal receipt PDF.

        As with _print_last_receipt, this only picks the save path and
        delegates the actual PDF generation to InvoiceOutputService.
        """
        invoice = self._last_receipt_invoice()
        if invoice is None:
            return
        try:
            suggested = self.output.default_path(invoice, "80mm")
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Receipt PDF", str(suggested), "PDF files (*.pdf)"
            )
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.output.generate(invoice, self.settings.get_settings(), path, "80mm")
            QMessageBox.information(self, "PDF generated", f"Receipt PDF saved to:\n{path}")
        except (InvoiceOutputError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Receipt output failed", str(exc))

    def edit_invoice_blockers(self, invoice) -> str | None:
        """Return why this invoice can't be reloaded for editing right now,
        or None if it's safe to proceed. Callers should check this *before*
        voiding the invoice, so a sale is never irreversibly voided only to
        discover afterwards that it can't be replaced.
        """
        if not any(item.product_id is not None for item in invoice.items):
            return "None of this invoice's products still exist, so it can't be reloaded into a bill."
        if len(self._bills) >= MAX_ACTIVE_BILLS:
            return f"{MAX_ACTIVE_BILLS} bills are already open. Close one before editing another invoice."
        return None

    def load_invoice_for_edit(self, invoice) -> bool:
        """Reload a past invoice's items into a new bill tab for editing.

        The caller (MainWindow, wired from Invoices' "Edit Invoice") is
        responsible for checking edit_invoice_blockers and voiding the
        original invoice via SalesService.void_invoice before calling this
        — this method only rebuilds the on-screen cart from the invoice's
        own historical figures (original quantity/price/discount), so the
        cashier is editing what was actually sold rather than having every
        line silently repriced to today's product prices.
        """
        blocker = self.edit_invoice_blockers(invoice)
        if blocker:
            QMessageBox.warning(self, "Cannot edit invoice", blocker)
            return False
        editable_items = [item for item in invoice.items if item.product_id is not None]
        skipped_names = [item.product_name for item in invoice.items if item.product_id is None]

        cart = BillCart()
        for item in editable_items:
            cart.lines[item.product_id] = CartLine(
                product_id=item.product_id,
                quantity=Decimal(item.quantity),
                item_discount=Decimal(item.item_discount),
                unit=item.unit,
                unit_price=Decimal(item.unit_price),
                product_name=item.product_name,
                line_total=Decimal(item.line_total),
            )
            # Invoices store the discount for the whole line, so the per-unit
            # figure the cart now edits has to be recovered by dividing it back
            # out. Where the original doesn't divide evenly the per-unit value is
            # rounded to the paisa and the line discount is re-derived from it
            # below, which can shift a reloaded line by a cent — deliberately
            # preferred over showing the cashier a per-unit figure that doesn't
            # match the line it is sitting next to.
            original_quantity = Decimal(item.quantity)
            if original_quantity > 0:
                per_unit = (Decimal(item.item_discount) / original_quantity).quantize(Decimal("0.01"))
                # Can never exceed the unit price: the stored line discount was
                # already capped at the line's gross by the original sale.
                cart.set_unit_discount(item.product_id, per_unit)
        # Summed *after* the per-unit figures are applied, so the bill discount
        # absorbs any rounding above and the reloaded bill still totals what the
        # original invoice did.
        item_discount_total = sum((l.item_discount for l in cart.lines.values()), Decimal("0.00"))
        bill_discount = Decimal(invoice.discount_total) - item_discount_total
        cart.bill_discount = bill_discount if bill_discount > 0 else Decimal("0.00")

        self._bills.append(cart)
        bill_number = len(self._bills)
        self.bill_tabs.addTab(f"Bill {bill_number}")
        self._update_new_bill_button_state()
        self.bill_tabs.setCurrentIndex(self.bill_tabs.count() - 1)

        # Drive the Cash/Credit toggle from the reloaded invoice. setChecked
        # only emits (and re-runs _payment_changed) when the state actually
        # changes, so this both restores the right side and refreshes the
        # customer-field styling/hint to match.
        self.payment_toggle.setChecked(invoice.payment_type == "credit")
        self._payment_changed()
        self.customer.setText(invoice.customer_name or "")

        message = (
            f"Invoice {invoice.invoice_number} was voided and its items were loaded "
            f"into Bill {bill_number}. Update the bill and Complete Sale to finalize "
            f"the replacement invoice."
        )
        if skipped_names:
            message += "\n\nThese products no longer exist and were not reloaded:\n" + "\n".join(skipped_names)
        QMessageBox.information(self, "Invoice loaded for editing", message)
        return True

    def _relayout_tables(self):
        """Re-size both tables' columns, then re-measure their row heights.

        Always in that order and never one without the other: the columns follow
        the viewport, how many lines a name wraps onto follows the column width,
        and the row height follows the line count. Measuring rows against stale
        columns is what would leave a name at the height it needed *before* a
        resize.
        """
        if hasattr(self, "results"):
            self._size_product_results_columns()
            self.results.resizeRowsToContents()
        if hasattr(self, "table"):
            self._size_cart_columns()
            self.table.resizeRowsToContents()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_tables()
        # Run again once the event loop has finished laying the page out. A
        # table's viewport can still report an intermediate width while the
        # resize is being propagated to the child layouts, and wrapping is
        # measured against that width — so a row measured here alone can be left
        # at a line count that does not match the width it finally settles at.
        # The deferred pass re-measures against the settled geometry.
        QTimer.singleShot(0, self._relayout_tables)

    def refresh(self):
        # Re-measured here because a Settings -> Typography change alters the
        # header height this depends on (MainWindow calls refresh() after
        # re-applying the stylesheet).
        self._apply_dense_metrics()
        self.refresh_products()
        self.refresh_cart()
