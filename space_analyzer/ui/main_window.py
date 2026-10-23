"""Main window: the navigation rail, the four pages and the administrator status bar."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QWidget,
)

from space_analyzer import __version__, system
from space_analyzer.ui import icons, theme
from space_analyzer.ui.pages.duplicates import DuplicatesPage
from space_analyzer.ui.pages.large_files import LargeFilesPage
from space_analyzer.ui.pages.overview import OverviewPage
from space_analyzer.ui.pages.suggestions import SuggestionsPage
from space_analyzer.ui.state import AppState
from space_analyzer.ui.widgets import RailNav, icon_label, set_role

_LOG = logging.getLogger(__name__)

_PAGES = (
    ("Overview", "gauge", "Drive usage and the folders that take the most space"),
    ("Suggestions", "spark", "What can be cleaned up, with the impact of each item"),
    ("Large files", "file", "The biggest files on a scanned drive"),
    ("Duplicates", "copy", "Identical files, grouped so you keep one copy"),
)


class MainWindow(QMainWindow):
    """Window with a navigation rail on the left, the pages on the right and a status bar."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setWindowTitle(f"Windows Space Analyzer {__version__}")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(1060, 700)
        self.resize(1400, 880)

        self._pages: list[QWidget] = [
            OverviewPage(state),
            SuggestionsPage(state),
            LargeFilesPage(state),
            DuplicatesPage(state),
        ]

        self._rail = RailNav()
        for title, glyph, hint in _PAGES:
            self._rail.add_page(title, glyph, hint)
        self._navigation: QListWidget = self._rail.nav_widget()

        self._stack = QStackedWidget()
        self._stack.setObjectName("pages")
        for page in self._pages:
            self._stack.addWidget(page)
        self._rail.current_changed.connect(self._stack.setCurrentIndex)
        self._rail.set_current(0)

        self._build_status_bar()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._rail)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

    def _build_status_bar(self) -> None:
        """Show the administrator status, with a restart button when not elevated."""
        status = self.statusBar()
        status.setSizeGripEnabled(False)

        elevated = self._state.admin
        status.addWidget(
            icon_label("shield", color=theme.SIGNAL if elevated else theme.WARN, size=14)
        )
        badge = set_role(
            QLabel("ADMINISTRATOR" if elevated else "STANDARD USER"),
            "caption",
        )
        badge.setStyleSheet(
            f"color: {theme.SIGNAL if elevated else theme.WARN};"
            "font-size: 9.5px; font-weight: 700;"
        )
        status.addWidget(badge)
        status.addWidget(QLabel("   "))

        note = set_role(
            QLabel(
                "Full access to every cleanup item."
                if elevated
                else "Restart as administrator to unlock every item."
            ),
            "status",
        )
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        status.addWidget(note, 1)

        if elevated:
            return
        restart = QPushButton("Restart as administrator")
        restart.setIcon(icons.icon("power", theme.WARN, 14))
        restart.setProperty("ghost", "true")
        restart.setCursor(Qt.CursorShape.PointingHandCursor)
        restart.clicked.connect(self._restart_as_admin)
        status.addPermanentWidget(restart)

    def _restart_as_admin(self) -> None:
        """Start an elevated copy of the app. This window closes only when that copy started."""
        if system.relaunch_as_admin():
            self.close()
            return
        QMessageBox.warning(
            self,
            "Restart as administrator",
            "The request was cancelled or failed. The app keeps running without administrator rights.",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop background work on every page, then let the window close."""
        for page in self._pages:
            shutdown = getattr(page, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except Exception:  # one failing page must not keep the window open
                _LOG.exception("Shutting down %s failed", type(page).__name__)
        event.accept()
