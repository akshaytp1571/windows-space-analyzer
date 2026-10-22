"""Palette tokens, risk labels and the application stylesheet.

The look is a measurement instrument: a near-black backdrop ruled with a faint grid,
hairline square panels instead of rounded cards, monospace numerals for every
measured quantity, and a single acid-lime signal colour. Nothing here is decorative
for its own sake — colour marks a reading, a risk level or an active control.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from space_analyzer.models import Risk

# --------------------------------------------------------------------- palette

BG_DEEP = "#0b0c0e"        # window backdrop
BG_BASE = "#101114"        # page surface
BG_PANEL = "#15171b"       # panel fill
BG_RAISED = "#1c1f24"      # inputs, hovered rows
BG_SUNKEN = "#0d0e11"      # wells and tracks

LINE = "#262a31"           # primary hairline
LINE_SOFT = "#1b1e23"      # dividers
LINE_BRIGHT = "#3a4049"    # hovered hairline

TEXT = "#e9ebee"
TEXT_MUTED = "#9aa2ad"
TEXT_DIM = "#61676f"

SIGNAL = "#c8f751"         # the one accent: acid lime
SIGNAL_DEEP = "#a8d63a"
SIGNAL_DIM = "#5d7a1e"
SIGNAL_WASH = "rgba(200, 247, 81, 0.10)"

ALERT = "#ff6b5e"
WARN = "#ffb84d"
COOL = "#63b3ff"

# ------------------------------------------------------------------ risk tokens

RISK_LABEL: dict[Risk, str] = {
    Risk.SAFE: "SAFE",
    Risk.LOW: "LOW",
    Risk.ADVANCED: "ADV",
    Risk.INFO: "INFO",
}

RISK_COLOR: dict[Risk, str] = {
    Risk.SAFE: SIGNAL,
    Risk.LOW: COOL,
    Risk.ADVANCED: WARN,
    Risk.INFO: TEXT_DIM,
}

# ------------------------------------------------------------------- usage tone

_USAGE_OK = SIGNAL
_USAGE_WARN = WARN
_USAGE_FULL = ALERT

_MONO_FAMILIES = (
    "Cascadia Mono",
    "Cascadia Code",
    "JetBrains Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Courier New",
)


def usage_color(percent: float) -> str:
    """Return the signal colour below 75 percent, amber below 90, and alert from 90 up."""
    if percent < 75:
        return _USAGE_OK
    if percent < 90:
        return _USAGE_WARN
    return _USAGE_FULL


def mono_font(point_size: float = 9.5, *, bold: bool = False) -> QFont:
    """Return the monospace face used for every measured number in the app."""
    available = set(QFontDatabase.families())
    family = next((name for name in _MONO_FAMILIES if name in available), "monospace")
    font = QFont(family, point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setBold(bold)
    return font


# ------------------------------------------------------------------- stylesheet

_STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {BG_DEEP};
}}
QWidget {{
    color: {TEXT};
    font-size: 12.5px;
}}
QLabel {{
    background: transparent;
}}
QLabel[role="wordmark"] {{
    font-size: 13px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel[role="eyebrow"] {{
    font-size: 10px;
    font-weight: 700;
    color: {TEXT_DIM};
}}
QLabel[role="page-title"] {{
    font-size: 25px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel[role="page-subtitle"] {{
    font-size: 12.5px;
    color: {TEXT_MUTED};
}}
QLabel[role="panel-title"] {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel[role="caption"] {{
    font-size: 9.5px;
    font-weight: 700;
    color: {TEXT_DIM};
}}
QLabel[role="muted"] {{
    font-size: 11.5px;
    color: {TEXT_MUTED};
}}
QLabel[role="status"] {{
    font-size: 11.5px;
    color: {TEXT_MUTED};
}}
QLabel[role="readout"] {{
    color: {TEXT};
}}
QLabel[role="signal"] {{
    color: {SIGNAL};
}}

/* ---------------------------------------------------------------- surfaces */

QFrame[panel="true"] {{
    background-color: {BG_PANEL};
    border: 1px solid {LINE};
}}
QFrame[panel="sunken"] {{
    background-color: {BG_SUNKEN};
    border: 1px solid {LINE_SOFT};
}}
QFrame[panel="bare"] {{
    background-color: transparent;
    border: none;
}}
QFrame#rail {{
    background-color: {BG_DEEP};
    border: none;
    border-right: 1px solid {LINE};
}}
QFrame#rule {{
    background-color: {LINE};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#vrule {{
    background-color: {LINE};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}

/* ----------------------------------------------------------------- buttons */

QPushButton {{
    background-color: {BG_RAISED};
    border: 1px solid {LINE};
    padding: 7px 14px;
    color: {TEXT};
    font-size: 11.5px;
    font-weight: 600;
    text-align: center;
}}
QPushButton:hover {{
    background-color: #23272d;
    border-color: {LINE_BRIGHT};
}}
QPushButton:pressed {{
    background-color: {BG_SUNKEN};
}}
QPushButton:disabled {{
    background-color: {BG_PANEL};
    border-color: {LINE_SOFT};
    color: {TEXT_DIM};
}}
QPushButton[primary="true"] {{
    background-color: {SIGNAL};
    border: 1px solid {SIGNAL};
    color: #10130a;
    font-weight: 700;
}}
QPushButton[primary="true"]:hover {{
    background-color: #d6ff63;
    border-color: #d6ff63;
}}
QPushButton[primary="true"]:pressed {{
    background-color: {SIGNAL_DEEP};
}}
QPushButton[primary="true"]:disabled {{
    background-color: #2b3316;
    border-color: #2b3316;
    color: #6b7548;
}}
QPushButton[ghost="true"] {{
    background-color: transparent;
    border: 1px solid {LINE};
    color: {TEXT_MUTED};
}}
QPushButton[ghost="true"]:hover {{
    background-color: {BG_RAISED};
    border-color: {LINE_BRIGHT};
    color: {TEXT};
}}
QPushButton[ghost="true"]:disabled {{
    background-color: transparent;
    color: {TEXT_DIM};
}}
QPushButton[danger="true"] {{
    background-color: transparent;
    border: 1px solid #55292a;
    color: {ALERT};
}}
QPushButton[danger="true"]:hover {{
    background-color: rgba(255, 107, 94, 0.12);
    border-color: {ALERT};
}}
QPushButton[danger="true"]:disabled {{
    border-color: {LINE_SOFT};
    color: #5c3a38;
}}

/* ------------------------------------------------------------------ inputs */

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {BG_SUNKEN};
    border: 1px solid {LINE};
    padding: 6px 10px;
    color: {TEXT};
    min-height: 17px;
    selection-background-color: {SIGNAL_DIM};
    selection-color: {TEXT};
}}
QComboBox:hover, QLineEdit:hover {{
    border-color: {LINE_BRIGHT};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{
    border-color: {SIGNAL_DIM};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 4px solid {TEXT_MUTED};
    width: 0;
    height: 0;
    margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED};
    border: 1px solid {LINE_BRIGHT};
    padding: 3px;
    selection-background-color: {SIGNAL_WASH};
    selection-color: {SIGNAL};
    color: {TEXT};
}}

/* ------------------------------------------------------- trees and tables */

QTreeWidget, QTreeView, QTableWidget, QTableView, QListWidget, QListView {{
    background-color: transparent;
    alternate-background-color: rgba(255, 255, 255, 0.014);
    border: none;
    color: {TEXT};
    selection-background-color: {SIGNAL_WASH};
    selection-color: {TEXT};
}}
QTreeWidget::item, QTableWidget::item, QTreeView::item, QTableView::item {{
    padding: 5px 4px;
    border: none;
}}
QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: rgba(255, 255, 255, 0.035);
}}
QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {SIGNAL_WASH};
    color: {TEXT};
}}
QTreeWidget::branch {{
    background: transparent;
}}
QHeaderView {{
    background-color: transparent;
    border: none;
}}
QHeaderView::section {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 7px 5px;
    font-size: 9.5px;
    font-weight: 700;
}}
QTableCornerButton::section {{
    background-color: transparent;
    border: none;
}}

/* -------------------------------------------------------------------- rail */

QListWidget#navigation {{
    background-color: transparent;
    border: none;
    padding: 0;
    color: {TEXT_MUTED};
}}
QListWidget#navigation::item {{
    border: none;
    border-left: 2px solid transparent;
    padding: 10px 14px;
    margin: 1px 0;
    color: {TEXT_MUTED};
    font-size: 12px;
    font-weight: 600;
}}
QListWidget#navigation::item:hover {{
    background-color: {BG_PANEL};
    color: {TEXT};
}}
QListWidget#navigation::item:selected {{
    background-color: {BG_PANEL};
    border-left: 2px solid {SIGNAL};
    color: {TEXT};
}}

/* -------------------------------------------------------------- scrollbars */

QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #2b3037;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #3d444d;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 9px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #2b3037;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #3d444d;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
    border: none;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* -------------------------------------------------------------- progressbar */

QProgressBar {{
    background-color: {BG_SUNKEN};
    border: 1px solid {LINE};
    min-height: 10px;
    max-height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {SIGNAL};
}}

/* --------------------------------------------------------------------- misc */

QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {LINE_BRIGHT};
    background-color: {BG_SUNKEN};
}}
QCheckBox::indicator:hover {{
    border-color: {SIGNAL_DIM};
}}
QCheckBox::indicator:checked {{
    background-color: {SIGNAL};
    border-color: {SIGNAL};
    image: none;
}}
QCheckBox::indicator:disabled {{
    background-color: {BG_PANEL};
    border-color: {LINE_SOFT};
}}
QTextBrowser, QTextEdit, QPlainTextEdit {{
    background-color: {BG_SUNKEN};
    border: 1px solid {LINE};
    color: {TEXT};
    padding: 10px;
    selection-background-color: {SIGNAL_DIM};
    selection-color: {TEXT};
}}
QStatusBar {{
    background-color: {BG_DEEP};
    border-top: 1px solid {LINE};
    color: {TEXT_MUTED};
    min-height: 30px;
}}
QStatusBar::item {{
    border: none;
}}
QToolTip {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {LINE_BRIGHT};
    padding: 5px 8px;
    font-size: 11px;
}}
QMessageBox {{
    background-color: {BG_PANEL};
}}
QMessageBox QLabel {{
    color: {TEXT};
}}
QMenu {{
    background-color: {BG_RAISED};
    border: 1px solid {LINE_BRIGHT};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
}}
QMenu::item:selected {{
    background-color: {SIGNAL_WASH};
    color: {SIGNAL};
}}
"""


def _dark_palette() -> QPalette:
    """Build a dark palette so native dialogs match the styled widgets."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG_DEEP))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG_SUNKEN))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(BG_RAISED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(SIGNAL_DIM))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_RAISED))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DIM))
    palette.setColor(QPalette.ColorRole.Link, QColor(SIGNAL))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.Text, QColor(TEXT_DIM))
    palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(TEXT_DIM))
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DIM))
    return palette


def apply_theme(app: QApplication) -> None:
    """Set the Fusion style, the dark palette, the base font and the stylesheet."""
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    available = set(QFontDatabase.families())
    family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in available else "Segoe UI"
    app.setFont(QFont(family, 9))
    app.setStyleSheet(_STYLESHEET)


def mark_primary(button: QPushButton) -> None:
    """Give a button the signal-filled style used for the main action of a page."""
    _restyle(button, "primary", "true")


def mark_ghost(button: QPushButton) -> None:
    """Give a button the quiet outline style used for secondary actions."""
    _restyle(button, "ghost", "true")


def mark_danger(button: QPushButton) -> None:
    """Give a button the alert outline style used for permanent removal."""
    _restyle(button, "danger", "true")


def _restyle(widget: QWidget, name: str, value: str) -> None:
    """Set a style property and repolish the widget so the stylesheet picks it up."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
