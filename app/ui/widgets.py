"""Shared UI building blocks for the application's card-based design system.

Kept in one place so every page presents the same white, rounded, subtly
elevated containers ("cards") and the same strictly left-aligned tables with
clear grey row dividers — instead of each page re-deriving the look.
"""
from __future__ import annotations

from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFontMetrics, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

# Compact baseline row height for the dense Billing tables. Rows never render
# shorter than this, but they stay free to grow past it when wrapped text needs
# a second or third line (see apply_compact_rows).
COMPACT_ROW_HEIGHT = 24

# One wheel notch moves this many pixels once pixel-precise scrolling is on.
# Qt's default item-view scrolling moves a whole row (or a whole "page" of
# them) per notch, which is what reads as lag/stutter/jumpiness; scrolling by
# a small fixed pixel step instead makes the movement continuous. ~12px is
# fast enough to cover a list quickly without feeling weightless.
SMOOTH_SCROLL_STEP = 12


def add_shadow(widget, *, blur: int = 18, x: int = 0, y: int = 3, color=(15, 23, 42), alpha: int = 38) -> None:
    """Attach a soft, low-contrast drop shadow so a container reads as elevated.

    Shadows cannot be expressed in QSS, so every card/panel gets one here. The
    blur is wide but the shadow itself stays faint, keeping the look modern
    rather than heavy.
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setXOffset(x)
    shadow.setYOffset(y)
    shadow.setColor(QColor(color[0], color[1], color[2], alpha))
    widget.setGraphicsEffect(shadow)


def card_header(text: str) -> QLabel:
    """A bold sub-header shown inside a card (e.g. '1. Find Product')."""
    label = QLabel(text)
    label.setObjectName("cardHeader")
    return label


class Card(QFrame):
    """A white, rounded, bordered, subtly elevated container.

    Pass ``title`` to get a bold numbered/section sub-header at the top of the
    card. The inner :attr:`body` layout is where callers add their content.
    """

    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)
        if title:
            outer.addWidget(card_header(title))
        self.body = outer
        add_shadow(self)

    def add(self, widget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self.body.addLayout(layout, stretch)


def enable_smooth_scrolling(widget, step: int = SMOOTH_SCROLL_STEP) -> None:
    """Give a table/list/scroll area fluid, pixel-precise scrolling.

    Works for both item views (which need the explicit per-pixel scroll modes)
    and plain scroll areas (which only need the step), so a single call covers
    every scrollable surface in the app.
    """
    if isinstance(widget, QAbstractItemView):
        widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        widget.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    vertical = widget.verticalScrollBar()
    if vertical is not None:
        vertical.setSingleStep(step)
    horizontal = widget.horizontalScrollBar()
    if horizontal is not None:
        horizontal.setSingleStep(step)


def apply_compact_rows(table: QTableWidget, row_height: int = COMPACT_ROW_HEIGHT) -> None:
    """Give a table a compact *baseline* row height that content can grow past.

    The height is applied as the vertical header's minimum/default section size
    rather than as a ``Fixed`` resize mode on purpose: a ``Fixed`` vertical
    header makes ``resizeRowsToContents()`` a silent no-op, so a long product
    name could never claim its second line and would have to be truncated
    instead. With a *minimum* the two behaviours coexist — single-line rows sit
    at exactly ``row_height``, wrapped ones expand to fit — which is what lets
    the table stay dense without ever showing an ellipsis.

    Callers must still call ``table.resizeRowsToContents()`` after populating
    (and after column widths change, since wrapping depends on column width).
    """
    table.setWordWrap(True)
    # Wrapping and eliding are not mutually exclusive in Qt: with elide left on,
    # the *last* visible line is still cut short with "...". Off means long text
    # always resolves into more lines instead of being truncated.
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    vertical_header = table.verticalHeader()
    vertical_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    vertical_header.setMinimumSectionSize(row_height)
    vertical_header.setDefaultSectionSize(row_height)
    # A *hidden* vertical header still decides row heights: resizeRowsToContents
    # takes the larger of the row's content hint and the header's own section
    # hint, and that section hint includes whatever padding the app-wide
    # QHeaderView::section rule carries (9px in theme.qss) — which quietly makes
    # every row ~35px no matter how compact its contents are. Styling the header
    # widget directly beats the rule it inherits from its ancestors, so this is
    # what actually lets the baseline height apply.
    vertical_header.setStyleSheet(
        "QHeaderView::section { padding: 0px; margin: 0px; border: none; }"
    )


def reserve_visible_rows(table: QTableWidget, rows: int, row_height: int = COMPACT_ROW_HEIGHT) -> None:
    """Reserve enough height for ``rows`` compact rows before the table has to
    scroll at all.

    Uses the horizontal header's ``sizeHint`` rather than its current height so
    the reservation is already correct the first time the table is laid out,
    before it has ever been shown.
    """
    header_height = table.horizontalHeader().sizeHint().height()
    table.setMinimumHeight(header_height + rows * row_height + 2 * table.frameWidth() + 2)


def style_table(table: QTableWidget) -> None:
    """Apply the shared table look: left-aligned headers, no vertical header,
    row selection, clear grey grid dividers, comfortable row height."""
    header = table.horizontalHeader()
    # Requirement #3: column headers align to the extreme left, in step with
    # their data cells (see left_align_items), rather than centered.
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(True)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    enable_smooth_scrolling(table)
    if table.verticalHeader().defaultSectionSize() < 40:
        table.verticalHeader().setDefaultSectionSize(40)


def left_align_items(table: QTableWidget, vertical=Qt.AlignmentFlag.AlignVCenter) -> None:
    """Force every populated cell to the extreme left.

    ``vertical`` defaults to centering, which is right for tables whose rows are
    all one line. Tables that let long text wrap should pass ``AlignTop`` so a
    tall wrapped row keeps its neighbouring cells lined up with the first line
    of that text instead of floating them in the middle of the row.

    Cells that host widgets (e.g. the quantity editor / Remove button) have no
    QTableWidgetItem and are skipped.
    """
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | vertical)


class ToggleSwitch(QWidget):
    """A compact, animated two-option switch drawn as a sleek dark pill.

    Unlike a checkbox, *both* option labels are always visible: the pill is split
    into a left and a right half and a light rounded "thumb" glides under the
    active side. The cashier therefore always sees the current choice *and* the
    alternative in one small control — exactly what a POS mode/payment switch
    wants, and why this replaces the old radio-button / combo-box pairs.

    The widget is deliberately generic — a left label, a right label, an on/off
    state and a ``toggled(bool)`` signal — with no POS knowledge. Callers map the
    two states onto their own domain values (Retail/Wholesale, Cash/Credit, ...),
    which keeps it reusable across pages.

    :meth:`isChecked` is ``True`` when the *right* option is selected.
    :meth:`setChecked` animates the thumb and emits :attr:`toggled` only when the
    state actually changes, so it is safe to drive from code that syncs the
    switch to a model (e.g. reloading a saved invoice's payment type) without
    risking a feedback loop.
    """

    #: Emitted with the new state whenever it changes (True == right option).
    toggled = pyqtSignal(bool)

    # Palette — a dark track with a white thumb reads as a modern, minimalist
    # switch on the app's light cards. Kept as class attributes so the look can
    # be retuned in one place, or overridden per instance, without touching the
    # painting code below.
    TRACK_OFF = QColor("#334155")      # slate-700, when the left option is active
    TRACK_ON = QColor("#1e293b")       # slate-800, subtly darker once "on"
    THUMB_COLOR = QColor("#ffffff")
    ACTIVE_TEXT = QColor("#0f172a")    # dark text, drawn on the light thumb
    INACTIVE_TEXT = QColor("#cbd5e1")  # muted light text, drawn on the dark track

    _MARGIN = 3        # gap between the track edge and the thumb
    _H_PADDING = 18    # horizontal breathing room inside each label half
    _V_PADDING = 7     # vertical breathing room above/below the label
    _ANIM_MS = 160     # thumb slide duration

    def __init__(self, left_label: str, right_label: str, parent=None, *, checked: bool = False):
        super().__init__(parent)
        self._left = left_label
        self._right = right_label
        self._checked = bool(checked)
        # Animated 0.0 (thumb hard left) .. 1.0 (thumb hard right). paintEvent
        # reads this, so the thumb glides between halves instead of snapping.
        self._offset = 1.0 if self._checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Clickable and (when focused) arrow-key operable, but deliberately kept
        # out of the Tab chain: ClickFocus means it never steals focus during a
        # keyboard-only entry flow that walks between other fields with Enter.
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # -- state -----------------------------------------------------------------
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._animate_to(1.0 if checked else 0.0)
        self.toggled.emit(checked)

    def toggle(self) -> None:
        self.setChecked(not self._checked)

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(target)
        self._anim.start()

    # -- animated 'offset' property (thumb position) ---------------------------
    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    # Registering it as a Qt property is what lets QPropertyAnimation drive it.
    offset = pyqtProperty(float, fget=_get_offset, fset=_set_offset)

    # -- geometry --------------------------------------------------------------
    def _half_width(self) -> int:
        """Width of one label half — the wider of the two labels wins so both
        halves stay symmetric and the thumb covers each exactly."""
        metrics = QFontMetrics(self.font())
        text = max(metrics.horizontalAdvance(self._left), metrics.horizontalAdvance(self._right))
        return text + 2 * self._H_PADDING

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = 2 * self._half_width() + 2 * self._MARGIN
        height = metrics.height() + 2 * self._V_PADDING
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def changeEvent(self, event):
        # A global stylesheet/typography change alters the font; re-measure so
        # the pill grows or shrinks with it instead of clipping a label.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.updateGeometry()
            self.update()
        super().changeEvent(event)

    # -- input -----------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Any click anywhere on the pill flips the state — including a
            # click on the already-active side — rather than only selecting
            # whichever half was clicked. This matches how cashiers expect a
            # physical toggle to behave: tap it, it flips.
            self.setChecked(not self.isChecked())
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Home):
            self.setChecked(False)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_End):
            self.setChecked(True)
        elif key == Qt.Key.Key_Space:
            self.toggle()
        else:
            super().keyPressEvent(event)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect())
        radius = rect.height() / 2.0

        # 1) The pill track.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.TRACK_ON if self._checked else self.TRACK_OFF)
        painter.drawRoundedRect(rect, radius, radius)

        # 2) The sliding thumb — half the (inset) track wide, positioned between
        #    the two halves by the animated offset so it glides on toggle.
        inner = rect.adjusted(self._MARGIN, self._MARGIN, -self._MARGIN, -self._MARGIN)
        thumb_w = inner.width() / 2.0
        thumb_x = inner.left() + self._offset * thumb_w
        thumb = QRectF(thumb_x, inner.top(), thumb_w, inner.height())
        thumb_radius = thumb.height() / 2.0
        # A faint offset shadow first gives the thumb a subtle sense of lift.
        painter.setBrush(QColor(15, 23, 42, 45))
        painter.drawRoundedRect(thumb.translated(0, 1), thumb_radius, thumb_radius)
        painter.setBrush(self.THUMB_COLOR)
        painter.drawRoundedRect(thumb, thumb_radius, thumb_radius)

        # 3) Both labels, each centered in its half: the active side is dark text
        #    on the light thumb, the inactive side muted light text on the track.
        painter.setFont(self.font())
        half = rect.width() / 2.0
        left_rect = QRectF(rect.left(), rect.top(), half, rect.height())
        right_rect = QRectF(rect.left() + half, rect.top(), half, rect.height())
        painter.setPen(self.ACTIVE_TEXT if not self._checked else self.INACTIVE_TEXT)
        painter.drawText(left_rect, Qt.AlignmentFlag.AlignCenter, self._left)
        painter.setPen(self.ACTIVE_TEXT if self._checked else self.INACTIVE_TEXT)
        painter.drawText(right_rect, Qt.AlignmentFlag.AlignCenter, self._right)
        painter.end()


class SegmentedControl(QWidget):
    """A two-option segmented control: a recessed dark pill split into two
    buttons, the active one filled with the app's royal-blue accent.

    A drop-in, API-compatible alternative to :class:`ToggleSwitch` — same
    ``isChecked`` / ``setChecked`` / ``toggle`` / :attr:`toggled` surface and the
    same ``checked == the right option`` convention — so it can stand in wherever
    that switch is used without touching any of the surrounding logic. Where
    ``ToggleSwitch`` is a dark sliding pill, this renders as two crisp segment
    buttons (Retail | Wholesale, Cash | Credit), the flat "segmented control"
    look of a modern SaaS toolbar.

    It is composed from two exclusive checkable ``QPushButton``s in a
    ``QButtonGroup`` rather than painted by hand, so its fill/hover states come
    straight from the scoped stylesheet below and stay consistent with the rest
    of the themed buttons.
    """

    #: Emitted with the new state whenever it changes (True == right option).
    toggled = pyqtSignal(bool)

    # Scoped entirely to this widget's own object names so it never leaks onto
    # other buttons/frames on the page. The active segment is the royal-blue
    # accent; the inactive half is muted slate text on a dark, recessed track
    # (matching the app's input wells) so the control sits cleanly inside the
    # dark billing card instead of reading as a light island.
    _QSS = (
        "QFrame#segmentTrack {"
        " background: #0F172A; border: 1px solid #334155; border-radius: 8px;"
        "}"
        "QPushButton#segment {"
        " border: none; background: transparent; color: #94A3B8;"
        " font-weight: 600; padding: 6px 18px; border-radius: 6px; min-height: 0px;"
        "}"
        "QPushButton#segment:hover { color: #F8FAFC; }"
        "QPushButton#segment:checked { background: #2563EB; color: #F8FAFC; }"
        "QPushButton#segment:checked:hover { background: #1D4ED8; color: #F8FAFC; }"
    )

    def __init__(self, left_label: str, right_label: str, parent=None, *, checked: bool = False):
        super().__init__(parent)
        self._checked = bool(checked)

        track = QFrame(self)
        track.setObjectName("segmentTrack")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(track)

        row = QHBoxLayout(track)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)

        self._left_button = QPushButton(left_label)
        self._right_button = QPushButton(right_label)
        # Exclusive group: exactly one segment is ever active, and clicking the
        # inactive one flips the pair — the behaviour a segmented control wants.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, button in enumerate((self._left_button, self._right_button)):
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # Kept out of the Tab chain (like ToggleSwitch) so it never steals
            # focus during a keyboard-only entry flow that walks fields with Enter.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._group.addButton(button, index)
            row.addWidget(button)

        (self._right_button if self._checked else self._left_button).setChecked(True)
        self.setStyleSheet(self._QSS)

        # Toggle-switch semantics: a click anywhere on the control flips to the
        # OTHER option — clicking the inactive segment selects it, and clicking
        # the already-active segment switches away from it — so the whole pill
        # behaves like one two-state switch (what the cashier expects here).
        # _set_state still emits toggled only on a real change, and every flip
        # is a real change.
        self._left_button.clicked.connect(lambda: self.toggle())
        self._right_button.clicked.connect(lambda: self.toggle())

    # -- state (mirrors ToggleSwitch's public surface) -------------------------
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        # Emits toggled only when the state actually changes, so it is safe to
        # drive from code that syncs the control to a model (e.g. reloading a
        # saved invoice's payment type) without risking a feedback loop.
        self._set_state(bool(checked))

    def toggle(self) -> None:
        self._set_state(not self._checked)

    def _set_state(self, checked: bool) -> None:
        if checked == self._checked:
            return
        self._checked = checked
        button = self._right_button if checked else self._left_button
        # A no-op when the click already checked it; the real work when driven
        # from setChecked()/toggle() in code.
        if not button.isChecked():
            button.setChecked(True)
        self.toggled.emit(checked)
