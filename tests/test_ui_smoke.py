"""Smoke tests for the desktop UI. They are skipped when PySide6 cannot be imported."""

from __future__ import annotations

import importlib
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pyside_available() -> bool:
    """Return True when the PySide6 widgets module can be imported."""
    try:
        importlib.import_module("PySide6.QtWidgets")
    except (ImportError, OSError):
        return False
    return True


@unittest.skipUnless(_pyside_available(), "PySide6 is not installed")
class UiSmokeTest(unittest.TestCase):
    """Builds the main window and checks navigation, closing and the dialog helpers."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["windows-space-analyzer-tests"])

    def _make_window(self):
        """Build the main window for a non-admin session. No scan or cleanup is started."""
        from space_analyzer.ui.main_window import MainWindow
        from space_analyzer.ui.state import AppState

        window = MainWindow(AppState(admin=False))
        self.addCleanup(window.close)
        return window

    def test_application_can_be_created(self) -> None:
        self.assertIsNotNone(self.app)

    def test_main_window_constructs(self) -> None:
        from space_analyzer import __version__

        window = self._make_window()
        # The title carries the version so a user can report which build they ran.
        self.assertEqual(window.windowTitle(), f"Windows Space Analyzer {__version__}")
        self.assertFalse(window.windowIcon().isNull(), "the window must carry an app icon")

    def test_navigation_lists_pages_in_order(self) -> None:
        from PySide6.QtWidgets import QListWidget

        window = self._make_window()
        navigation = window.findChild(QListWidget, "navigation")
        self.assertIsNotNone(navigation)
        titles = [navigation.item(row).text() for row in range(navigation.count())]
        self.assertEqual(
            titles,
            ["Overview", "Suggestions", "Large files", "Duplicates", "Apps", "Watch"],
        )

    def test_every_rail_entry_opens_a_page(self) -> None:
        from PySide6.QtWidgets import QListWidget

        window = self._make_window()
        navigation = window.findChild(QListWidget, "navigation")
        # A rail entry with no page behind it would show a blank panel.
        self.assertEqual(navigation.count(), window._stack.count())

    def test_closing_window_does_not_raise(self) -> None:
        window = self._make_window()
        window.show()
        self.assertTrue(window.close())

    def test_dialog_helpers_exist_and_are_callable(self) -> None:
        from space_analyzer.ui import dialogs

        self.assertTrue(callable(dialogs.confirm))
        self.assertTrue(callable(dialogs.show_reports))

    def test_app_icon_has_every_size(self) -> None:
        from space_analyzer.ui import icons

        icon = icons.app_icon()
        self.assertFalse(icon.isNull())
        widths = sorted(size.width() for size in icon.availableSizes())
        # Taskbar, Alt-Tab and Explorer all ask for different sizes.
        self.assertIn(16, widths)
        self.assertIn(32, widths)
        self.assertIn(256, widths)

    def test_logging_writes_to_a_file(self) -> None:
        import logging
        import os
        import tempfile
        from unittest import mock

        from space_analyzer.ui import app as app_module

        self._close_root_handlers_on_cleanup()
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "nested", "space-analyzer.log")
            try:
                with mock.patch.object(app_module, "log_file_path", return_value=target):
                    path = app_module.configure_logging()
                self.assertEqual(path, target)
                self.assertTrue(os.path.exists(path), "the log file must be created")
                logging.getLogger("space_analyzer.test").warning("written by the test")
                self._flush_root_handlers()
                with open(path, encoding="utf-8") as handle:
                    self.assertIn("written by the test", handle.read())
            finally:
                self._close_root_handlers()

    def test_uncaught_exception_is_logged_not_swallowed(self) -> None:
        import os
        import sys
        import tempfile
        from unittest import mock

        from space_analyzer.ui import app as app_module

        original = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", original)
        self._close_root_handlers_on_cleanup()
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "log.txt")
            try:
                with mock.patch.object(app_module, "log_file_path", return_value=target), \
                        mock.patch.object(app_module, "QMessageBox") as box:
                    app_module.configure_logging()
                    app_module._install_excepthook()
                    try:
                        raise ValueError("boom for the test")
                    except ValueError:
                        sys.excepthook(*sys.exc_info())
                    box.return_value.exec.assert_called_once()
                self._flush_root_handlers()
                with open(target, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("CRITICAL", text)
                self.assertIn("boom for the test", text)
            finally:
                self._close_root_handlers()

    def _flush_root_handlers(self) -> None:
        """Push buffered records to disk so a test can read them back."""
        import logging

        for handler in logging.getLogger().handlers:
            handler.flush()

    def _close_root_handlers(self) -> None:
        """Detach and close the root log handlers.

        Windows will not delete a file that another handle still has open, so a
        handler left on the root logger makes TemporaryDirectory cleanup fail with
        PermissionError. Called from a finally block, before the folder goes away.
        """
        import logging

        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    def _close_root_handlers_on_cleanup(self) -> None:
        """Also close the root handlers at teardown, in case a test left one behind."""
        self.addCleanup(self._close_root_handlers)

    def test_usage_color_thresholds(self) -> None:
        from space_analyzer.ui import theme

        self.assertEqual(theme.usage_color(50), theme._USAGE_OK)
        self.assertEqual(theme.usage_color(75), theme._USAGE_WARN)
        self.assertEqual(theme.usage_color(90), theme._USAGE_FULL)
        # Green below 75, amber below 90, red from 90 up.
        self.assertNotEqual(theme.usage_color(74.9), theme.usage_color(75))
        self.assertNotEqual(theme.usage_color(89.9), theme.usage_color(90))
