"""Start-up for the desktop app: Qt application, shared state and main window."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from space_analyzer import system
from space_analyzer.ui.main_window import MainWindow
from space_analyzer.ui.state import AppState
from space_analyzer.ui.theme import apply_theme

_window: MainWindow | None = None  # module-level reference keeps the window alive


def run() -> int:
    """Show the main window, run the Qt event loop and return the exit code."""
    global _window
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    app.setApplicationName("Windows Space Analyzer")
    apply_theme(app)
    state = AppState(admin=system.is_admin())
    _window = MainWindow(state)
    _window.show()
    return app.exec()
