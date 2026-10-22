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
        window = self._make_window()
        self.assertEqual(window.windowTitle(), "Windows Space Analyzer")

    def test_navigation_lists_pages_in_order(self) -> None:
        from PySide6.QtWidgets import QListWidget

        window = self._make_window()
        navigation = window.findChild(QListWidget, "navigation")
        self.assertIsNotNone(navigation)
        titles = [navigation.item(row).text() for row in range(navigation.count())]
        self.assertEqual(titles, ["Overview", "Suggestions", "Large files", "Duplicates"])

    def test_closing_window_does_not_raise(self) -> None:
        window = self._make_window()
        window.show()
        self.assertTrue(window.close())

    def test_dialog_helpers_exist_and_are_callable(self) -> None:
        from space_analyzer.ui import dialogs

        self.assertTrue(callable(dialogs.confirm))
        self.assertTrue(callable(dialogs.show_reports))

    def test_usage_color_thresholds(self) -> None:
        from space_analyzer.ui import theme

        self.assertEqual(theme.usage_color(50), theme._USAGE_OK)
        self.assertEqual(theme.usage_color(75), theme._USAGE_WARN)
        self.assertEqual(theme.usage_color(90), theme._USAGE_FULL)
        # Green below 75, amber below 90, red from 90 up.
        self.assertNotEqual(theme.usage_color(74.9), theme.usage_color(75))
        self.assertNotEqual(theme.usage_color(89.9), theme.usage_color(90))
