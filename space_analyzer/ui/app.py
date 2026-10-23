"""Start-up for the desktop app: Qt application, shared state and main window."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tempfile
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from space_analyzer import APP_NAME, __version__, system
from space_analyzer.ui import icons
from space_analyzer.ui.main_window import MainWindow
from space_analyzer.ui.state import AppState
from space_analyzer.ui.theme import apply_theme

_LOG = logging.getLogger(__name__)

_window: MainWindow | None = None  # module-level reference keeps the window alive


def log_file_path() -> str:
    """Where the log goes: %LOCALAPPDATA%\\WindowsSpaceAnalyzer, or the temp folder."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "WindowsSpaceAnalyzer", "space-analyzer.log")


def configure_logging() -> str:
    """Send warnings and errors to a rotating file. Returns the path used.

    A windowed build has no console, so without this an unexpected exception would
    vanish with no trace at all. The file is the only way a user can report what
    went wrong, so it is set up before anything else runs.
    """
    path = log_file_path()
    handler: logging.Handler
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
    except OSError:
        # A read-only profile must not stop the app from starting.
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    return path


def _install_excepthook() -> None:
    """Log an uncaught exception and tell the user, instead of dying silently."""

    def handle(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _LOG.critical("Unhandled exception\n%s", text)
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(f"{APP_NAME} — unexpected error")
            box.setText("Something went wrong and the action was stopped.")
            box.setInformativeText(
                f"No files were changed by the failed step.\n\n"
                f"A report was written to:\n{log_file_path()}"
            )
            box.setDetailedText(text)
            box.exec()
        except Exception:  # the hook must never raise, or the process dies badly
            _LOG.exception("Could not show the error dialog")

    sys.excepthook = handle


def run() -> int:
    """Show the main window, run the Qt event loop and return the exit code."""
    global _window
    log_path = configure_logging()
    _install_excepthook()
    _LOG.info("%s %s starting", APP_NAME, __version__)

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(icons.app_icon())
    apply_theme(app)

    state = AppState(admin=system.is_admin())
    _LOG.info("Running as %s; log at %s", "administrator" if state.admin else "standard user", log_path)
    _window = MainWindow(state)
    _window.show()
    return app.exec()
