from __future__ import annotations

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QWidget

# Qt analogue of responsive CSS: instead of fixed pixel geometry we size windows
# and dialogs as a fraction of the *available* screen (the vw/vh equivalent) and
# clamp the result between sensible minimums and the physical screen so the UI
# fits any laptop/desktop resolution without overflowing or clipping.


def available_screen_rect(widget: QWidget | None = None) -> QRect:
    screen = None
    if widget is not None:
        handle = widget.window().windowHandle()
        if handle is not None:
            screen = handle.screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1280, 800)
    return screen.availableGeometry()


def _clamp(value: int, low: int, high: int) -> int:
    if high < low:
        high = low
    return max(low, min(value, high))


def size_to_screen(
    widget: QWidget,
    width_frac: float,
    height_frac: float,
    min_width: int = 0,
    min_height: int = 0,
    max_width: int | None = None,
    max_height: int | None = None,
) -> None:
    """Resize ``widget`` to a fraction of the available screen, clamped.

    Guarantees the widget never exceeds the available screen area (no overflow)
    while honouring the caller's minimums and optional maximums.
    """
    rect = available_screen_rect(widget)
    ceil_w = rect.width() if max_width is None else min(max_width, rect.width())
    ceil_h = rect.height() if max_height is None else min(max_height, rect.height())
    width = _clamp(round(rect.width() * width_frac), min(min_width, ceil_w), ceil_w)
    height = _clamp(round(rect.height() * height_frac), min(min_height, ceil_h), ceil_h)
    widget.resize(width, height)


def center_on_screen(widget: QWidget) -> None:
    rect = available_screen_rect(widget)
    frame = widget.frameGeometry()
    frame.moveCenter(rect.center())
    widget.move(frame.topLeft())
