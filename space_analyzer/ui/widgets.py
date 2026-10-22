"""Presentation widgets for the instrument look: grid backdrop, hairline panels,
monospace readouts, tick-marked gauges and an icon navigation rail.

These are pure view helpers. They hold no engine state and never delete anything;
pages compose them and feed them already-measured values.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from space_analyzer import util
from space_analyzer.ui import icons, theme


def repolish(widget: QWidget) -> None:
    """Reapply the stylesheet to a widget after its style properties changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(label: QLabel, role: str) -> QLabel:
    """Tag a label with a stylesheet role and repolish it. Returns the label."""
    label.setProperty("role", role)
    repolish(label)
    return label


def rule(horizontal: bool = True) -> QFrame:
    """A one-pixel separator line."""
    line = QFrame()
    line.setObjectName("rule" if horizontal else "vrule")
    line.setFrameShape(QFrame.Shape.NoFrame)
    if horizontal:
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    else:
        line.setFixedWidth(1)
        line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    return line


def icon_button(text: str, glyph: str, *, tint: str | None = None, size: int = 15) -> QPushButton:
    """A button with a painted icon on its left and a text label."""
    button = QPushButton(text)
    button.setIcon(icons.icon(glyph, tint or theme.TEXT_MUTED, size))
    button.setIconSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def icon_label(glyph: str, *, color: str | None = None, size: int = 16) -> QLabel:
    """A label that only holds a painted icon, for panel headers and legends."""
    label = QLabel()
    label.setPixmap(icons.pixmap(glyph, size, color or theme.TEXT_MUTED))
    label.setFixedSize(size, size)
    return label


# ------------------------------------------------------------------ backdrop


class GridBackdrop(QWidget):
    """A page body that paints a faint measurement grid behind its children.

    The grid is drawn from a cached tile so resizing stays cheap. It gives the
    interface the look of graph paper without costing an image asset.
    """

    def __init__(self, parent: QWidget | None = None, *, step: int = 24) -> None:
        super().__init__(parent)
        self._step = max(8, step)
        self._tile: Optional[QPixmap] = None

    def _grid_tile(self) -> QPixmap:
        """Build (once) the tiling pixmap that holds one grid cell with its corner dot."""
        if self._tile is not None:
            return self._tile
        ratio = 2.0
        side = self._step * int(ratio)
        tile = QPixmap(side, side)
        tile.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tile)
        pen = QPen(QColor(255, 255, 255, 10))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(0, 0, side, 0)
        painter.drawLine(0, 0, 0, side)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 18))
        dot = max(1.0, 1.2 * ratio)
        painter.drawEllipse(QPointF(0.0, 0.0), dot, dot)
        painter.end()
        tile.setDevicePixelRatio(ratio)
        self._tile = tile
        return tile

    def paintEvent(self, event: object) -> None:
        """Fill with the page colour, then tile the grid across the whole widget."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG_BASE))
        painter.drawTiledPixmap(self.rect(), self._grid_tile())
        painter.end()


class PageScroll(QScrollArea):
    """A page body that scrolls vertically when its content is taller than the window.

    Every page is taller than a short window, and several pages grow as data arrives.
    Without this container Qt squeezes the last stretch-1 widget until it is unusable
    (a tree showing one clipped row), which looks like a bug rather than a small window.
    The grid is painted on the viewport so it covers the visible area at any scroll
    position, and the content keeps a sane minimum height so nothing is ever crushed.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        step: int = 24,
        margins: Tuple[int, int, int, int] = (26, 22, 26, 18),
        spacing: int = 13,
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Horizontal scrolling stays available so a narrow window can never put a
        # page's right edge out of reach; vertical scrolling is the normal case.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._content = GridBackdrop(step=step)
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        self.setWidget(self._content)

    def body(self) -> QVBoxLayout:
        """The layout inside the scrolling content, where a page adds its widgets."""
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Add a widget to the scrolling content and return it."""
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: object) -> object:
        """Add a sub-layout to the scrolling content and return it."""
        self._layout.addLayout(layout)
        return layout

    def content(self) -> QWidget:
        """The widget that holds the page content, for tests and callers that need it."""
        return self._content


# --------------------------------------------------------------------- panel


class Panel(QFrame):
    """A square hairline panel with an optional titled header bar and a body area."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        glyph: str = "",
        caption: str = "",
        margins: Tuple[int, int, int, int] = (16, 14, 16, 14),
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._header: Optional[PanelHeader] = None
        if title:
            self._header = PanelHeader(title, glyph, caption)
            self._outer.addWidget(self._header)
            self._outer.addWidget(rule())

        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        self._outer.addWidget(self._body, 1)

    def body(self) -> QVBoxLayout:
        """The panel's own layout, for callers that add widgets directly."""
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Add a widget to the panel body and return it."""
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: object) -> object:
        """Add a sub-layout to the panel body and return it."""
        self._layout.addLayout(layout)
        return layout

    def header(self) -> Optional["PanelHeader"]:
        """The header bar, or None when the panel was built without a title."""
        return self._header


class PanelHeader(QWidget):
    """The header strip of a panel: an icon, a title, a caption and an action slot."""

    def __init__(
        self,
        title: str,
        glyph: str = "",
        caption: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 11, 12, 11)
        row.setSpacing(9)
        if glyph:
            row.addWidget(icon_label(glyph, color=theme.SIGNAL, size=15))
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self._title = set_role(QLabel(title.upper()), "panel-title")
        self._caption = set_role(QLabel(caption), "caption")
        # Captions carry real information, so they wrap rather than forcing the panel
        # to a width that a narrow window cannot give it.
        self._caption.setWordWrap(True)
        self._caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._caption.setVisible(bool(caption))
        text.addWidget(self._title)
        text.addWidget(self._caption)
        row.addLayout(text, 1)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(7)
        row.addLayout(self._actions)

    def add_action(self, widget: QWidget) -> QWidget:
        """Place a widget at the right end of the header."""
        self._actions.addWidget(widget)
        return widget

    def set_caption(self, text: str) -> None:
        """Replace the caption under the title."""
        self._caption.setText(text)
        self._caption.setVisible(bool(text))


# ------------------------------------------------------------------- readout


class Readout(QWidget):
    """A measured value: a small uppercase label above a large monospace number.

    The number is the loudest thing here, which is the point — on an instrument the
    reading is what you look at, not the frame around it. The number is painted by
    hand so it can shrink to fit its slot: a long reading never overlaps its unit.
    """

    _VALUE_POINT = 17.0
    _UNIT_POINT = 9.0
    _MIN_POINT = 9.0

    def __init__(
        self,
        label: str,
        value: str = "—",
        unit: str = "",
        hint: str = "",
        *,
        color: str = theme.TEXT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._color = color
        self._value_text = value
        self._unit_text = unit
        self._fitted_point = self._VALUE_POINT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(1)

        self._label = set_role(QLabel(label.upper()), "caption")
        self._label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._label.setWordWrap(True)
        self._figure = _Figure(self)
        self._figure.setMinimumHeight(int(self._VALUE_POINT * 2.1))
        self._figure.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._hint = set_role(QLabel(hint), "muted")
        # The hint wraps instead of being silently truncated: a clipped fragment reads
        # as a rendering bug, and these hints carry the meaning of the number above.
        self._hint.setWordWrap(True)
        self._hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._hint.setVisible(bool(hint))

        layout.addWidget(self._label)
        layout.addWidget(self._figure)
        layout.addWidget(self._hint)
        self.setMinimumWidth(122)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._paint()

    def _paint(self) -> None:
        """Push the colour and the current text into the painted figure."""
        self._figure.set_figure(self._value_text, self._unit_text, self._color)

    def set_value(self, value: str, unit: str = "", hint: str = "") -> None:
        """Replace the number, its unit and the hint line."""
        self._value_text = value
        self._unit_text = unit
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))
        self._paint()

    def set_color(self, color: str) -> None:
        """Change the number colour, for example when a drive fills up."""
        self._color = color
        self._paint()

    def value_label(self) -> "_Figure":
        """The widget that paints the number, for tests and callers that need it."""
        return self._figure

    def value_text(self) -> str:
        """The reading currently shown."""
        return self._value_text

    def unit_text(self) -> str:
        """The unit currently shown, or an empty string."""
        return self._unit_text


class _Figure(QWidget):
    """Paints a big monospace reading with a small unit beside it, shrinking to fit.

    Drawing the pair as one widget is what keeps them apart: the unit is placed after
    the number's measured width, and the number's point size drops until both fit.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = ""
        self._unit = ""
        self._color = theme.TEXT
        self._point = Readout._VALUE_POINT
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_figure(self, value: str, unit: str, color: str) -> None:
        """Set the reading, its unit and the colour, then repaint."""
        self._value = value
        self._unit = unit
        self._color = color
        self.update()

    def _fitted_fonts(self) -> Tuple[QFont, QFont]:
        """Return the number and unit fonts, shrinking the number until both fit the width.

        Point sizes are tried from the largest down in whole steps, because Qt stores a
        font's size as an integer and would otherwise round two neighbouring requests to
        the same size and loop without ever fitting. Reaching the minimum means the slot
        is very narrow; the number is then drawn at that size and clipped rather than
        overlapping the unit.
        """
        available = max(1, self.width())
        unit_font = theme.mono_font(Readout._UNIT_POINT)
        gap = 5.0 if self._unit else 0.0
        suffix = QFontMetricsF(unit_font).horizontalAdvance(self._unit)
        floor = int(Readout._MIN_POINT)
        point = int(Readout._VALUE_POINT)
        while point > floor:
            value_font = theme.mono_font(point, bold=True)
            number = QFontMetricsF(value_font).horizontalAdvance(self._value)
            if number + gap + suffix <= available:
                break
            point -= 1
        value_font = theme.mono_font(max(point, floor), bold=True)
        self._point = max(point, floor)
        return value_font, unit_font

    def paintEvent(self, event: object) -> None:
        """Draw the number, then the unit after it, both aligned to the same baseline."""
        if not self._value:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        value_font, unit_font = self._fitted_fonts()

        value_metrics = QFontMetricsF(value_font)
        unit_metrics = QFontMetricsF(unit_font)
        number_width = value_metrics.horizontalAdvance(self._value)
        baseline = self.height() - 4.0

        painter.setFont(value_font)
        painter.setPen(QColor(self._color))
        painter.drawText(
            QPointF(0.0, baseline),
            self._value,
        )
        if self._unit:
            painter.setFont(unit_font)
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(
                QPointF(number_width + 5.0, baseline - (value_metrics.descent() - unit_metrics.descent())),
                self._unit,
            )
        painter.end()

    def sizeHint(self) -> QSize:
        """Preferred size: wide enough for a five-character reading plus a unit."""
        return QSize(96, int(Readout._VALUE_POINT * 2.1))


# --------------------------------------------------------------------- gauge


class TickGauge(QWidget):
    """A horizontal gauge with a tick scale, a filled bar and a needle at the reading.

    This is the app's main usage display: the tick marks make the value readable at a
    glance, and the needle gives it the feel of a physical meter rather than a web bar.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        height: int = 58,
        divisions: int = 20,
    ) -> None:
        super().__init__(parent)
        self._percent = 0.0
        self._color = theme.SIGNAL
        self._caption = ""
        self._reading = ""
        self._divisions = max(4, divisions)
        self.setMinimumHeight(height)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, percent: float, *, caption: str = "", reading: str = "") -> None:
        """Set the filled share (0-100) and the two text lines drawn with the gauge."""
        self._percent = max(0.0, min(100.0, float(percent)))
        self._color = theme.usage_color(self._percent)
        self._caption = caption
        self._reading = reading
        self.update()

    def sizeHint(self) -> QSize:
        """Preferred size: a wide, short gauge."""
        return QSize(360, self.height())

    def paintEvent(self, event: object) -> None:
        """Draw the caption, the tick scale, the fill and the needle."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        caption_font = QFont(self.font())
        caption_font.setPointSize(8)
        caption_font.setBold(True)
        painter.setFont(caption_font)
        if self._caption:
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(
                QRectF(0, 0, self.width() / 2, 13),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._caption.upper(),
            )
        if self._reading:
            painter.setFont(theme.mono_font(9, bold=True))
            painter.setPen(QColor(self._color))
            painter.drawText(
                QRectF(self.width() / 2, 0, self.width() / 2, 13),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._reading,
            )

        bar = QRectF(0, 19, self.width(), 16)
        painter.fillRect(bar, QColor(theme.BG_SUNKEN))
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawRect(bar.adjusted(0.5, 0.5, -0.5, -0.5))

        fill_width = bar.width() * self._percent / 100.0
        if fill_width > 0.5:
            fill_rect = QRectF(bar.left() + 1, bar.top() + 1, max(0.0, fill_width - 1), bar.height() - 2)
            gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            base = QColor(self._color)
            gradient.setColorAt(0.0, base.darker(125))
            gradient.setColorAt(1.0, base)
            painter.fillRect(fill_rect, gradient)

        ticks_top = bar.bottom() + 5
        painter.setPen(QPen(QColor(theme.LINE_BRIGHT), 1))
        for index in range(self._divisions + 1):
            x = bar.left() + bar.width() * index / self._divisions
            major = index % 5 == 0
            painter.drawLine(
                QPointF(x, ticks_top),
                QPointF(x, ticks_top + (8 if major else 4)),
            )

        if self._percent > 0:
            needle_x = bar.left() + bar.width() * self._percent / 100.0
            painter.setPen(QPen(QColor(self._color), 2))
            painter.drawLine(QPointF(needle_x, bar.top() - 3), QPointF(needle_x, bar.bottom() + 3))
            painter.setBrush(QColor(self._color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(needle_x, bar.top() - 3), 2.6, 2.6)
        painter.end()


class SegmentBar(QWidget):
    """A single stacked bar that splits a total across parts, with a tick scale inside it.

    The scale is drawn along the bar's inner bottom edge rather than below the widget,
    so the bar can sit directly above a legend without its ticks crossing the text.
    """

    _TICK_ROOM = 5

    def __init__(self, parent: QWidget | None = None, *, height: int = 20) -> None:
        super().__init__(parent)
        self._segments: List[Tuple[str, float, str]] = []
        self._divisions = 10
        self.setFixedHeight(max(height, 20))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_segments(self, segments: Sequence[Tuple[str, float, str]]) -> None:
        """Replace the segments with (label, value, colour) triples."""
        self._segments = [
            (label, max(0.0, float(value)), color) for label, value, color in segments
        ]
        self.update()

    def sizeHint(self) -> QSize:
        """Preferred size: a wide, short bar."""
        return QSize(360, self.height())

    def paintEvent(self, event: object) -> None:
        """Draw the segments, the frame, then the tick scale inside the bottom edge."""
        painter = QPainter(self)
        bar = QRectF(0, 0, self.width(), self.height())

        painter.fillRect(bar, QColor(theme.BG_SUNKEN))
        total = sum(value for _label, value, _color in self._segments)
        if total > 0:
            painter.save()
            painter.setClipRect(bar)
            x = bar.left()
            for _label, value, color in self._segments:
                width = bar.width() * value / total
                if width <= 0:
                    continue
                painter.fillRect(QRectF(x, bar.top(), width, bar.height()), QColor(color))
                x += width
            painter.restore()

        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        tick_bottom = bar.bottom() - 3
        for index in range(self._divisions + 1):
            x = bar.left() + bar.width() * index / self._divisions
            painter.drawLine(
                QPointF(x, tick_bottom - self._TICK_ROOM), QPointF(x, tick_bottom)
            )

        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawRect(bar.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class LegendRow(QWidget):
    """One line of a legend: a colour chip, a label and a right-aligned value."""

    _ROW_HEIGHT = 20

    def __init__(self, label: str, value: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        chip = QLabel()
        chip.setFixedSize(8, 8)
        chip.setStyleSheet(f"background-color: {color};")
        self._label = set_role(QLabel(label), "muted")
        self._label.setMinimumHeight(self._ROW_HEIGHT)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._value = QLabel(value)
        self._value.setFont(theme.mono_font(9.5, bold=True))
        self._value.setStyleSheet(f"color: {theme.TEXT};")
        self._value.setMinimumHeight(self._ROW_HEIGHT)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(chip)
        row.addWidget(self._label, 1)
        row.addWidget(self._value)
        self.setMinimumHeight(self._ROW_HEIGHT)

    def set_value(self, value: str) -> None:
        """Replace the right-hand value."""
        self._value.setText(value)


# ------------------------------------------------------------------- headers


class PageHeader(QWidget):
    """The title block at the top of every page: an eyebrow, a title and a subtitle."""

    def __init__(
        self,
        eyebrow: str,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._eyebrow = set_role(QLabel(eyebrow.upper()), "eyebrow")
        self._title = set_role(QLabel(title), "page-title")
        self._subtitle = set_role(QLabel(subtitle), "page-subtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._eyebrow)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)

    def set_subtitle(self, text: str) -> None:
        """Replace the subtitle line."""
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))


# ---------------------------------------------------------------------- rail


class RailNav(QFrame):
    """The left navigation rail: a wordmark block, icon entries and a footer slot."""

    current_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, width: int = 200) -> None:
        super().__init__(parent)
        self.setObjectName("rail")
        self.setFixedWidth(width)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 18, 0, 12)
        layout.setSpacing(0)

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(16, 0, 16, 0)
        brand_layout.setSpacing(3)
        mark_row = QHBoxLayout()
        mark_row.setContentsMargins(0, 0, 0, 0)
        mark_row.setSpacing(7)
        mark_row.addWidget(icon_label("drive", color=theme.SIGNAL, size=16))
        mark_row.addWidget(set_role(QLabel("SPACE ANALYZER"), "wordmark"), 1)
        brand_layout.addLayout(mark_row)
        brand_layout.addWidget(set_role(QLabel("disk telemetry"), "caption"))
        layout.addWidget(brand)
        layout.addSpacing(16)
        layout.addWidget(rule())
        layout.addSpacing(8)

        self._nav = QListWidget()
        self._nav.setObjectName("navigation")
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setIconSize(QSize(16, 16))
        self._nav.currentRowChanged.connect(self.current_changed.emit)
        layout.addWidget(self._nav, 1)

        self._footer = QVBoxLayout()
        self._footer.setContentsMargins(12, 8, 12, 0)
        self._footer.setSpacing(6)
        layout.addLayout(self._footer)

    def add_page(self, title: str, glyph: str, hint: str = "") -> None:
        """Append a navigation entry with an icon. The hint becomes its tooltip."""
        item = QListWidgetItem(icons.icon(glyph, theme.TEXT_MUTED, 16), title)
        if hint:
            item.setToolTip(hint)
        item.setSizeHint(QSize(0, 38))
        self._nav.addItem(item)

    def set_current(self, index: int) -> None:
        """Select a page by index."""
        self._nav.setCurrentRow(index)

    def current(self) -> int:
        """The selected page index."""
        return self._nav.currentRow()

    def nav_widget(self) -> QListWidget:
        """The inner list widget, so tests can inspect it by object name."""
        return self._nav

    def add_footer_widget(self, widget: QWidget) -> QWidget:
        """Place a widget at the bottom of the rail."""
        self._footer.addWidget(widget)
        return widget


# ---------------------------------------------------------------- empty state


class EmptyState(QWidget):
    """A centred message for a panel that has nothing to show yet."""

    def __init__(
        self,
        title: str,
        hint: str = "",
        glyph: str = "radar",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 36, 24, 36)
        layout.setSpacing(9)
        layout.addStretch(1)
        layout.addWidget(icon_label(glyph, color=theme.TEXT_DIM, size=26), 0,
                         Qt.AlignmentFlag.AlignHCenter)
        self._title = QLabel(title)
        self._title.setFont(theme.mono_font(10, bold=True))
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self._hint = QLabel(hint)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11.5px;")
        self._hint.setVisible(bool(hint))
        layout.addWidget(self._title)
        layout.addWidget(self._hint)
        layout.addStretch(1)

    def set_text(self, title: str, hint: str = "") -> None:
        """Replace both lines of the message."""
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))


def human_bytes(value: Optional[float]) -> str:
    """Format bytes for the readouts, keeping "unknown" for an unmeasured value."""
    return util.format_bytes(value)


def split_bytes(value: Optional[float]) -> Tuple[str, str]:
    """Split a byte count into a number and its unit, for the monospace readouts."""
    if value is None:
        return "—", ""
    number, _, unit = util.format_bytes(value).partition(" ")
    return number, unit


def split_percent(percent: float) -> Tuple[str, str]:
    """Split a percentage into the number and the '%' sign."""
    return f"{percent:.0f}", "%"
