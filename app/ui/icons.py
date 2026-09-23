"""Programmatically-drawn icon set.

The app ships no image/SVG assets, so icons are drawn with QPainter at a
fixed stroke width and a single accent color. This keeps the icon set
visually consistent (Section 23 of the design brief) without adding any
new dependency or bundled resource file.
"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_SIZE = 20
_STROKE = 1.6


def _new_painter(color: str) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(_STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    return pixmap, painter


def _finish(pixmap: QPixmap, painter: QPainter) -> QIcon:
    painter.end()
    return QIcon(pixmap)


def _dashboard(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    for x, y in ((2.5, 2.5), (11.5, 2.5), (2.5, 11.5), (11.5, 11.5)):
        painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.4, 1.4)
    return _finish(pixmap, painter)


def _products(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    painter.drawRoundedRect(QRectF(3, 6, 14, 11), 1.6, 1.6)
    painter.drawLine(QPointF(3, 9.5), QPointF(17, 9.5))
    painter.drawLine(QPointF(7, 2.5), QPointF(13, 2.5))
    painter.drawLine(QPointF(7, 2.5), QPointF(5.5, 6))
    painter.drawLine(QPointF(13, 2.5), QPointF(14.5, 6))
    return _finish(pixmap, painter)


def _billing(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    painter.drawRoundedRect(QRectF(4, 2.5, 12, 15), 1.6, 1.6)
    for y in (7, 10, 13):
        painter.drawLine(QPointF(6.5, y), QPointF(15.5, y))
    return _finish(pixmap, painter)


def _invoices(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    path = QPainterPath(QPointF(4.5, 2.5))
    path.lineTo(12, 2.5)
    path.lineTo(15.5, 6)
    path.lineTo(15.5, 17.5)
    path.lineTo(4.5, 17.5)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(QPointF(12, 2.5), QPointF(12, 6))
    painter.drawLine(QPointF(12, 6), QPointF(15.5, 6))
    for y in (10, 13, 15.5):
        painter.drawLine(QPointF(7, y), QPointF(13, y))
    return _finish(pixmap, painter)


def _customer_udhaar(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    painter.drawLine(QPointF(10, 3.5), QPointF(10, 16.5))
    left = QPainterPath(QPointF(10, 3.5))
    left.cubicTo(QPointF(6, 2), QPointF(2.5, 3), QPointF(2.5, 4.5))
    left.lineTo(QPointF(2.5, 15))
    left.cubicTo(QPointF(2.5, 16), QPointF(6, 15), QPointF(10, 16.5))
    painter.drawPath(left)
    right = QPainterPath(QPointF(10, 3.5))
    right.cubicTo(QPointF(14, 2), QPointF(17.5, 3), QPointF(17.5, 4.5))
    right.lineTo(QPointF(17.5, 15))
    right.cubicTo(QPointF(17.5, 16), QPointF(14, 15), QPointF(10, 16.5))
    painter.drawPath(right)
    return _finish(pixmap, painter)


def _settings(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    center = QPointF(10, 10)
    painter.drawEllipse(center, 3.1, 3.1)
    for i in range(8):
        import math
        angle = math.radians(i * 45)
        inner = QPointF(center.x() + 4.6 * math.cos(angle), center.y() + 4.6 * math.sin(angle))
        outer = QPointF(center.x() + 7.4 * math.cos(angle), center.y() + 7.4 * math.sin(angle))
        painter.drawLine(inner, outer)
    return _finish(pixmap, painter)


def _backup(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    painter.drawEllipse(QRectF(3.5, 2.5, 13, 5))
    painter.drawLine(QPointF(3.5, 5), QPointF(3.5, 15))
    painter.drawLine(QPointF(16.5, 5), QPointF(16.5, 15))
    path = QPainterPath()
    path.moveTo(3.5, 15)
    path.cubicTo(3.5, 16.4, 6.4, 17.5, 10, 17.5)
    path.cubicTo(13.6, 17.5, 16.5, 16.4, 16.5, 15)
    painter.drawPath(path)
    painter.drawArc(QRectF(3.5, 9, 13, 5), 0, -180 * 16)
    return _finish(pixmap, painter)


def _cash(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    painter.drawEllipse(QRectF(2.5, 5, 12, 12))
    painter.drawEllipse(QRectF(5.5, 2.5, 12, 12))
    painter.drawLine(QPointF(11.5, 6), QPointF(11.5, 13))
    painter.drawLine(QPointF(9, 9.5), QPointF(14, 9.5))
    return _finish(pixmap, painter)


def _lightning(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    poly = QPolygonF([
        QPointF(10.5, 2), QPointF(5, 11), QPointF(9, 11),
        QPointF(7.5, 18), QPointF(15, 8), QPointF(10.5, 8),
    ])
    painter.setBrush(QColor(color))
    painter.drawPolygon(poly)
    return _finish(pixmap, painter)


def _notes(color: str) -> QIcon:
    pixmap, painter = _new_painter(color)
    path = QPainterPath()
    path.moveTo(5, 2.5)
    path.lineTo(12, 2.5)
    path.lineTo(15.5, 6)
    path.lineTo(15.5, 17.5)
    path.lineTo(5, 17.5)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(QPointF(12, 2.5), QPointF(12, 6))
    painter.drawLine(QPointF(12, 6), QPointF(15.5, 6))
    for y in (9.5, 12, 14.5):
        painter.drawLine(QPointF(7, y), QPointF(13.5, y))
    return _finish(pixmap, painter)


_BUILDERS = {
    "dashboard": _dashboard,
    "products": _products,
    "billing": _billing,
    "invoices": _invoices,
    "customer_udhaar": _customer_udhaar,
    "settings": _settings,
    "backup": _backup,
    "lightning": _lightning,
    "cash": _cash,
    "notes": _notes,
}

NAV_ICON_ORDER = ["dashboard", "products", "billing", "invoices", "customer_udhaar", "notes", "settings", "backup"]


def nav_icon(kind: str, color: str = "#c3d0e0") -> QIcon:
    """Return a themed icon for a sidebar nav entry or dashboard card."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        return QIcon()
    return builder(color)
