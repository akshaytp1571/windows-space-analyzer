"""Vector icons drawn with QPainter, so the app ships no image files and needs no extra package.

Every icon is defined on a 24x24 grid and painted at the requested pixel size with a
stroke that scales with it. `icon()` returns a QIcon; `pixmap()` returns the raw image,
which is useful for painting icons inside a custom delegate.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from space_analyzer.ui import theme

# Each icon is a list of drawing steps: ("line", x1, y1, x2, y2),
# ("rect", x, y, w, h, radius), ("circle", cx, cy, r), ("poly", [(x, y), ...]),
# ("arc", x, y, w, h, start_deg, span_deg) or ("dot", cx, cy, r).
_Steps = List[Tuple]


def _drive() -> _Steps:
    """A hard disk seen from the side, with an activity dot."""
    return [
        ("rect", 2.5, 6.5, 19, 11, 2.5),
        ("line", 2.5, 12.5, 21.5, 12.5),
        ("dot", 6.2, 15.4, 1.15),
        ("line", 10.5, 15.4, 15.5, 15.4),
    ]


def _radar() -> _Steps:
    """A scan sweep: concentric arcs with a ray."""
    return [
        ("circle", 12, 12, 1.5),
        ("arc", 5.5, 5.5, 13, 13, 40, 260),
        ("arc", 2.5, 2.5, 19, 19, 60, 200),
        ("line", 12, 12, 19.5, 6.5),
    ]


def _spark() -> _Steps:
    """A four-point sparkle, used for cleanup and analysis actions."""
    return [
        ("poly", [(12, 2.5), (13.9, 9.1), (20.5, 11), (13.9, 12.9), (12, 19.5),
                  (10.1, 12.9), (3.5, 11), (10.1, 9.1)]),
        ("dot", 19.2, 4.8, 1.1),
    ]


def _file() -> _Steps:
    """A document with a folded corner."""
    return [
        ("poly", [(6, 2.8), (14.2, 2.8), (18.5, 7.1), (18.5, 21.2), (6, 21.2)]),
        ("poly", [(14.2, 2.8), (14.2, 7.1), (18.5, 7.1)]),
        ("line", 9, 12, 15.5, 12),
        ("line", 9, 16, 15.5, 16),
    ]


def _copy() -> _Steps:
    """Two overlapping sheets, used for duplicate groups."""
    return [
        ("rect", 8.5, 2.8, 12.7, 14.4, 2),
        ("poly", [(15.5, 17.2), (15.5, 21.2), (2.8, 21.2), (2.8, 6.8), (6.8, 6.8)]),
    ]


def _folder() -> _Steps:
    """A folder tab."""
    return [
        ("poly", [(2.8, 6.2), (9.2, 6.2), (11.2, 9.2), (21.2, 9.2), (21.2, 19.2), (2.8, 19.2)]),
        ("line", 2.8, 12, 21.2, 12),
    ]


def _gauge() -> _Steps:
    """A half gauge with a needle, used for drive usage."""
    return [
        ("arc", 3, 4, 18, 18, 200, 140),
        ("line", 12, 13, 17, 7.5),
        ("dot", 12, 13, 1.4),
        ("line", 5.6, 15.4, 7.4, 14.4),
        ("line", 18.4, 15.4, 16.6, 14.4),
    ]


def _shield() -> _Steps:
    """A shield, used for administrator rights."""
    return [
        ("poly", [(12, 2.6), (20, 5.6), (20, 12.2), (12, 21.4), (4, 12.2), (4, 5.6)]),
        ("line", 8.6, 11.6, 11.2, 14.2),
        ("line", 11.2, 14.2, 15.6, 9.4),
    ]


def _power() -> _Steps:
    """A power symbol, used for the restart action."""
    return [
        ("arc", 4.2, 4.6, 15.6, 15.6, 40, 280),
        ("line", 12, 2.6, 12, 11.4),
    ]


def _search() -> _Steps:
    """A magnifier, used for the file filter."""
    return [
        ("circle", 10.6, 10.6, 6.4),
        ("line", 15.3, 15.3, 21, 21),
    ]


def _clock() -> _Steps:
    """A clock face, used for file age."""
    return [
        ("circle", 12, 12, 9),
        ("line", 12, 7, 12, 12),
        ("line", 12, 12, 16.2, 14.4),
    ]


def _stack() -> _Steps:
    """Layered sheets, used for the breakdown legend."""
    return [
        ("poly", [(12, 2.8), (21.2, 7.6), (12, 12.4), (2.8, 7.6)]),
        ("poly", [(3.6, 12.4), (12, 16.8), (20.4, 12.4)]),
        ("poly", [(3.6, 16.6), (12, 21), (20.4, 16.6)]),
    ]


def _arrow() -> _Steps:
    """A right arrow, used on the open-in-Explorer action."""
    return [
        ("line", 3.5, 12, 19.5, 12),
        ("poly", [(13.5, 6), (19.5, 12), (13.5, 18)]),
    ]


def _trash() -> _Steps:
    """A bin, used for permanent removal."""
    return [
        ("line", 3.6, 6.6, 20.4, 6.6),
        ("poly", [(6, 6.6), (7.2, 21.2), (16.8, 21.2), (18, 6.6)]),
        ("poly", [(9, 6.6), (9.4, 3.4), (14.6, 3.4), (15, 6.6)]),
        ("line", 10, 10.4, 10, 17.4),
        ("line", 14, 10.4, 14, 17.4),
    ]


def _check() -> _Steps:
    """A tick, used for confirmation and selection."""
    return [
        ("poly", [(4.4, 12.4), (9.6, 17.6), (19.6, 7.6)]),
    ]


def _warning() -> _Steps:
    """A triangle with an exclamation mark."""
    return [
        ("poly", [(12, 3.2), (21.6, 20.2), (2.4, 20.2)]),
        ("line", 12, 9.2, 12, 14.2),
        ("dot", 12, 17.2, 1.05),
    ]


_ICONS: Dict[str, _Steps] = {
    "drive": _drive(),
    "radar": _radar(),
    "spark": _spark(),
    "file": _file(),
    "copy": _copy(),
    "folder": _folder(),
    "gauge": _gauge(),
    "shield": _shield(),
    "power": _power(),
    "search": _search(),
    "clock": _clock(),
    "stack": _stack(),
    "arrow": _arrow(),
    "trash": _trash(),
    "check": _check(),
    "warning": _warning(),
}


def names() -> List[str]:
    """Every icon the module can draw."""
    return sorted(_ICONS)


def _draw(painter: QPainter, steps: _Steps, color: QColor, size: float) -> None:
    """Paint one icon's steps onto the painter, scaled from the 24-unit design grid."""
    scale = size / 24.0
    pen = QPen(color)
    pen.setWidthF(max(1.35, 1.9 * scale))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    for step in steps:
        kind = step[0]
        if kind == "line":
            _x1, _y1, _x2, _y2 = (value * scale for value in step[1:5])
            painter.drawLine(QPointF(_x1, _y1), QPointF(_x2, _y2))
        elif kind == "rect":
            x, y, w, h, radius = step[1], step[2], step[3], step[4], step[5]
            painter.drawRoundedRect(
                QRectF(x * scale, y * scale, w * scale, h * scale),
                radius * scale,
                radius * scale,
            )
        elif kind == "circle":
            cx, cy, r = step[1], step[2], step[3]
            painter.drawEllipse(QPointF(cx * scale, cy * scale), r * scale, r * scale)
        elif kind == "dot":
            cx, cy, r = step[1], step[2], step[3]
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx * scale, cy * scale), r * scale, r * scale)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "poly":
            path = QPainterPath()
            points = step[1]
            path.moveTo(points[0][0] * scale, points[0][1] * scale)
            for px, py in points[1:]:
                path.lineTo(px * scale, py * scale)
            painter.drawPath(path)
        elif kind == "arc":
            x, y, w, h, start, span = step[1], step[2], step[3], step[4], step[5], step[6]
            painter.drawArc(
                QRectF(x * scale, y * scale, w * scale, h * scale),
                int(start * 16),
                int(span * 16),
            )


def pixmap(name: str, size: int = 18, color: str | None = None, ratio: float = 2.0) -> QPixmap:
    """Return an icon painted at `size` logical pixels, with a high-DPI backing store."""
    return _pixmap(name, size, color or theme.TEXT, ratio)


@lru_cache(maxsize=256)
def _pixmap(name: str, size: int, color: str, ratio: float) -> QPixmap:
    """Cached painter output, so a repeated icon is drawn once."""
    steps = _ICONS.get(name)
    physical = max(1, int(round(size * ratio)))
    canvas = QPixmap(physical, physical)
    canvas.fill(Qt.GlobalColor.transparent)
    if steps is None:
        return canvas
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _draw(painter, steps, QColor(color), float(physical))
    painter.end()
    canvas.setDevicePixelRatio(ratio)
    return canvas


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """Return a QIcon for the named glyph. Unknown names give an empty icon."""
    result = QIcon()
    tint = color or theme.TEXT
    for scale in (1, 2):
        result.addPixmap(_pixmap(name, size, tint, float(scale)))
    return result
