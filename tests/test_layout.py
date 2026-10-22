"""Layout regression tests: every page must stay usable when the window is short.

These guard the bug where a page taller than the window had no scroll container, so
Qt crushed the last stretch-1 widget (the folder tree showed one clipped row).
They are skipped when PySide6 cannot be imported.
"""

from __future__ import annotations

import importlib
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The smallest height at which a page's content should still be readable. Below this
# the page scrolls instead of shrinking its panels further.
_USABLE_TREE = 120
_SHORT_WINDOW = 700
_TALL_WINDOW = 1027


def _pyside_available() -> bool:
    """Return True when the PySide6 widgets module can be imported."""
    try:
        importlib.import_module("PySide6.QtWidgets")
    except (ImportError, OSError):
        return False
    return True


@unittest.skipUnless(_pyside_available(), "PySide6 is not installed")
class PageScrollTests(unittest.TestCase):
    """Every page scrolls instead of crushing its content when the window is short."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["windows-space-analyzer-layout-tests"])

    def setUp(self) -> None:
        from space_analyzer.ui.main_window import MainWindow
        from space_analyzer.ui.state import AppState

        self.window = MainWindow(AppState(admin=False))
        self.window.resize(1913, _TALL_WINDOW)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def _scrolls(self, page_index: int):
        """The PageScroll of one page."""
        from space_analyzer.ui.widgets import PageScroll

        return self.window._pages[page_index].findChild(PageScroll)

    def test_every_page_has_a_scroll_container(self) -> None:
        for index in range(len(self.window._pages)):
            with self.subTest(page=index):
                self.assertIsNotNone(self._scrolls(index))

    def test_every_page_reports_a_small_minimum_height(self) -> None:
        # A page that needs hundreds of pixels of minimum height cannot fit a short
        # window; the scroll container is what keeps this number small.
        for index, page in enumerate(self.window._pages):
            with self.subTest(page=type(page).__name__):
                self.assertLess(page.minimumSizeHint().height(), 200)

    def _show_page(self, index: int) -> None:
        """Make one page the visible page, so Qt gives it a real geometry."""
        self.window._rail.set_current(index)
        self.app.processEvents()
        self.app.processEvents()

    def test_short_window_never_shrinks_content_below_its_minimum(self) -> None:
        # The real guarantee: when the window is short, the content keeps its minimum
        # height and the page scrolls. A page whose content already fits may not scroll.
        for index in range(len(self.window._pages)):
            with self.subTest(page=index):
                self.window.resize(1913, _SHORT_WINDOW)
                self._show_page(index)
                scroll = self._scrolls(index)
                content = scroll.content()
                self.assertGreaterEqual(content.height(), content.minimumSizeHint().height())
                if content.height() > scroll.viewport().height():
                    self.assertGreater(
                        scroll.verticalScrollBar().maximum(),
                        0,
                        "content taller than the viewport must be reachable by scrolling",
                    )

    def test_overview_scrolls_at_a_short_window(self) -> None:
        # The Overview page is the tallest, so it must scroll at a short height.
        self.window.resize(1913, _SHORT_WINDOW)
        self._show_page(0)
        self.assertGreater(self._scrolls(0).verticalScrollBar().maximum(), 0)

    def test_tall_window_does_not_scroll(self) -> None:
        for index in range(len(self.window._pages)):
            with self.subTest(page=index):
                self.window.resize(1913, _TALL_WINDOW)
                self._show_page(index)
                scroll = self._scrolls(index)
                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)

    def test_folder_tree_keeps_a_usable_height_when_the_window_is_short(self) -> None:
        page = self.window._pages[0]
        self.window.resize(1913, _SHORT_WINDOW)
        self._show_page(0)
        self.assertGreaterEqual(page._tree.height(), _USABLE_TREE)

    def test_folder_tree_keeps_a_usable_height_when_the_window_is_tall(self) -> None:
        page = self.window._pages[0]
        self.window.resize(1913, _TALL_WINDOW)
        self._show_page(0)
        self.assertGreaterEqual(page._tree.height(), _USABLE_TREE)

    def test_scroll_content_holds_the_page_header(self) -> None:
        from space_analyzer.ui.widgets import PageHeader

        for index in range(len(self.window._pages)):
            with self.subTest(page=index):
                scroll = self._scrolls(index)
                self.assertIsNotNone(scroll.content().findChild(PageHeader))


if __name__ == "__main__":
    unittest.main()
