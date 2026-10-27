"""Tests for the Apps and Watch pages: they render, filter, and stay responsive."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from PySide6.QtCore import QItemSelectionModel

_QT = None


def _pyside() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_pyside(), "PySide6 is not installed")
class PageTestBase(unittest.TestCase):
    """Shared app instance and state for the page tests."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["space-pages-test"])

    def setUp(self) -> None:
        from space_analyzer.ui.state import AppState

        self.state = AppState(admin=False)


class AppsPageTests(PageTestBase):
    """The page lists apps, filters, and keeps a refused app un-uninstallable."""

    def _app(self, name: str = "App", size: int = 1000, uninstall: str = "",
             install: str = "", scope: str = "machine"):
        from space_analyzer.apps import InstalledApp

        return InstalledApp(
            name=name, version="1", publisher="Pub", install_location=install,
            uninstall_string=uninstall, size_bytes=size, scope=scope, key=name,
        )

    def _page(self, apps):
        from space_analyzer.ui.pages.apps_page import AppsPage

        page = AppsPage(self.state)
        self.addCleanup(page.close)
        page._apps = list(apps)
        page._refresh()
        return page

    def test_every_app_is_listed(self) -> None:
        page = self._page([
            self._app("Big", size=2_000_000),
            self._app("Small", size=500_000),
        ])
        self.assertEqual(len(page._shown), 2)

    def test_a_refused_app_is_not_uninstallable(self) -> None:
        page = self._page([self._app("Bad", uninstall="cmd.exe /c del")])
        self.assertEqual(len(page._shown), 1)
        # The button reflects the safety check.
        page._table.selectRow(0)
        page._update_buttons()
        self.assertFalse(page._uninstall_button.isEnabled())

    def test_a_safe_app_can_be_uninstalled(self) -> None:
        command = '"C:\\App\\uninst.exe" /S'
        page = self._page([self._app("Good", uninstall=command)])
        self.assertTrue(page._shown[0].can_uninstall)

    def test_a_refused_command_is_reported_by_the_page(self) -> None:
        page = self._page([self._app("Bad", uninstall="cmd.exe /c del C:\\ /s")])
        page._table.selectRow(0)
        page._uninstall_selected()
        self.assertIn("CANNOT UNINSTALL", page._status.text())

    def test_open_folder_is_enabled_only_with_a_location(self) -> None:
        page = self._page([
            self._app("HasDir", install=r"C:\App"),
            self._app("NoDir"),
        ])
        page._table.selectRow(0)
        page._update_buttons()
        self.assertTrue(page._open_button.isEnabled())

    def test_filter_narrows_the_list(self) -> None:
        page = self._page([self._app("Alpha"), self._app("Beta")])
        page._find.setText("alp")
        page._refresh()
        self.assertEqual(len(page._shown), 1)
        self.assertEqual(page._shown[0].name, "Alpha")

    def test_export_writer_produces_csv_and_json(self) -> None:
        import tempfile

        from space_analyzer import export

        app = self._app("A", size=2048, uninstall=r'"C:\App\uninst.exe"')
        with tempfile.TemporaryDirectory() as folder:
            csv_path = os.path.join(folder, "a.csv")
            json_path = os.path.join(folder, "a.json")
            export.write_apps(csv_path, [app])
            export.write_apps(json_path, [app])
            self.assertTrue(os.path.exists(csv_path))
            self.assertTrue(os.path.exists(json_path))
            with open(csv_path, "rb") as handle:
                self.assertEqual(handle.read(3), b"\xef\xbb\xbf")
            import json

            with open(json_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["apps"][0]["name"], "A")
            self.assertTrue(payload["apps"][0]["can_uninstall"])


class WatchPageTests(PageTestBase):
    """The page lists watches, their budgets, and the change view."""

    def _watch(self, path: str, budget: int = 0, size: int = 0, checked: bool = True):
        from space_analyzer.watches import Watch

        return Watch(
            path=path, budget_bytes=budget, last_size=size,
            last_checked=1.0 if checked else 0.0,
        )

    def _page(self, watches_list, drives=("C:\\",)):
        from space_analyzer.ui.pages.watch_page import WatchPage

        page = WatchPage(self.state)
        self.addCleanup(page.close)
        page._watches = list(watches_list)
        page._shown = list(watches_list)
        page._refresh_watches()
        drive = page._delta_drive
        drive.blockSignals(True)
        drive.clear()
        drive.addItems(list(drives))
        drive.blockSignals(False)
        return page

    def test_a_watch_over_budget_shows_it(self) -> None:
        page = self._page([self._watch(r"C:\Videos", budget=1000, size=2000)])
        model = page._watch_table.model()
        index = model.index(0, 4)  # STATE column
        self.assertEqual(page._watch_table.model().data(index), "OVER BUDGET")

    def test_an_unmeasured_watch_says_so(self) -> None:
        page = self._page([self._watch(r"C:\A", checked=False)])
        self.assertEqual(page._shown[0].last_checked, 0.0)

    def test_the_status_describes_the_summary(self) -> None:
        from space_analyzer import watches

        page = self._page([])
        page._summary = watches.WatchSummary()
        page._status.setText(watches.describe(page._summary))
        self.assertIn("No folders watched", page._status.text())

    def test_remove_disables_without_a_selection(self) -> None:
        page = self._page([self._watch(r"C:\A")])
        page._watch_table.clearSelection()
        page._update_buttons()
        self.assertFalse(page._remove_button.isEnabled())

    def test_delta_view_has_no_rows_without_history(self) -> None:
        page = self._page([], drives=("C:\\",))
        page._refresh_delta()
        self.assertEqual(page._delta_table.rowCount(), 0)

    def test_delta_rows_flatten_change_by_magnitude(self) -> None:
        from space_analyzer import history
        from space_analyzer.ui.pages import watch_page as wp

        before = history.ScanSnapshot(
            timestamp=1.0, root="C:\\", total_size=100, file_count=2, dir_count=1,
            free_bytes=0, capacity_bytes=0, elapsed_seconds=1.0,
            folders=[history.FolderSnapshot("C:\\a", 10, 1)],
        )
        after = history.ScanSnapshot(
            timestamp=2.0, root="C:\\", total_size=400, file_count=4, dir_count=2,
            free_bytes=0, capacity_bytes=0, elapsed_seconds=1.0,
            folders=[
                history.FolderSnapshot("C:\\a", 90, 1),
                history.FolderSnapshot("C:\\b", 300, 1),
            ],
        )
        rows = wp._delta_rows(history.compare(before, after))
        # Both grew; the larger one comes first.
        self.assertEqual(rows[0][0], "C:\\b")
        self.assertEqual(rows[1][0], "C:\\a")


if __name__ == "__main__":
    unittest.main()
