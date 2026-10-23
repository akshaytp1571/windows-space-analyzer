"""Tests for the Suggestions page. They are skipped when PySide6 cannot be imported."""

from __future__ import annotations

import importlib
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ANALYZE_HINT = "IDLE — click Analyze to measure what can be cleaned up"


def _pyside_available() -> bool:
    """Return True when the PySide6 widgets module can be imported."""
    try:
        importlib.import_module("PySide6.QtWidgets")
    except (ImportError, OSError):
        return False
    return True


@unittest.skipUnless(_pyside_available(), "PySide6 is not installed")
class SuggestionsPageTests(unittest.TestCase):
    """Builds the page with fake items and checks the selection and confirmation rules."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["windows-space-analyzer-tests"])

    def setUp(self) -> None:
        from space_analyzer.ui.pages.suggestions import SuggestionsPage
        from space_analyzer.ui.state import AppState

        self.state = AppState(admin=False)
        self.page = SuggestionsPage(self.state)
        self.addCleanup(self.page.shutdown)

    # ---------------------------------------------------------------- helpers

    def _item(self, item_id, action, *, size=1000, warnings=None, requires_admin=False):
        """Build a CleanupItem for the page tests."""
        from space_analyzer.models import Category, CleanupItem, Risk

        return CleanupItem(
            item_id=item_id,
            title=item_id.title(),
            category=Category.TEMP,
            risk=Risk.SAFE,
            description="",
            impact="",
            regenerates="",
            action=action,
            size_bytes=size,
            size_known=True,
            paths=[],
            allowed_roots=[],
            requires_admin=requires_admin,
            default_selected=False,
            warnings=list(warnings or []),
        )

    def _show(self, items) -> None:
        """Put the items in the state, which rebuilds the tree."""
        self.state.set_suggestions(list(items))

    def _checked(self) -> list:
        """Item ids of the ticked rows, in tree order."""
        return [entry.item.item_id for entry in self.page._checked_rows()]

    def _yes_then_no(self, *_args, **_kwargs) -> bool:
        """Confirm the main action, then decline the empty-the-bin offer that follows."""
        self._confirm_calls = getattr(self, "_confirm_calls", 0) + 1
        return self._confirm_calls == 1

    def _wait_idle(self, rounds: int = 2000) -> None:
        """Run the Qt event loop until the page has no worker left to finish.

        A fixed idle count is not enough: the page can chain a second worker from the
        first one's signal (the cleanup report, then the empty-bin offer), and there is
        a moment between the two when _busy is False. This waits for the worker object
        itself to be finished and gone, then drains the loop so its queued signals are
        delivered before the assertions run.
        """
        for _ in range(rounds):
            worker = getattr(self.page, "_worker", None)
            if worker is None:
                break
            self.app.processEvents()
            if not worker.isRunning():
                # Let the finished handler run, then check again: it may start another.
                self.app.processEvents()
                if getattr(self.page, "_worker", None) is not worker:
                    continue
                if not worker.isRunning():
                    break
            time.sleep(0.005)
        # Drain whatever the last signal queued, so status text and reports are applied.
        for _ in range(50):
            self.app.processEvents()
            if not self.page._busy:
                break
            time.sleep(0.005)
        self.app.processEvents()

    # ---------------------------------------------------------------- layout

    def test_page_shows_a_hint_before_analyzing(self) -> None:
        self.assertEqual(self.page._status.text(), _ANALYZE_HINT)
        self.assertEqual(self.page._selection_label.text(), "Nothing selected.")
        self.assertFalse(self.page._clean_button.isEnabled())
        self.assertFalse(self.page._tick_all_button.isEnabled())

    def test_grouping_uses_the_category_enum(self) -> None:
        from space_analyzer.models import ActionKind, Category

        bin_item = self._item("bin", ActionKind.EMPTY_RECYCLE_BIN)
        bin_item.category = Category.RECYCLE
        temp_item = self._item("temp", ActionKind.DELETE_PATHS)
        self._show([bin_item, temp_item])
        self.assertEqual(
            [group.category.value for group in self.page._groups],
            ["Temporary files", "Recycle Bin"],
        )
        self.assertEqual(
            [entry.item.item_id for entry in self.page._all_rows()], ["temp", "bin"]
        )

    def test_information_only_items_have_no_check_box(self) -> None:
        from PySide6.QtCore import Qt

        from space_analyzer.models import ActionKind

        self._show([self._item("pagefile", ActionKind.NONE)])
        (entry,) = self.page._all_rows()
        self.assertNotIn(Qt.ItemFlag.ItemIsUserCheckable, entry.row.flags())
        self.assertEqual(entry.row.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(self._checked(), [])

    def test_safe_items_are_ticked_by_default(self) -> None:
        from space_analyzer.models import ActionKind

        item = self._item("temp", ActionKind.DELETE_PATHS)
        item.default_selected = True
        self._show([item])
        self.assertEqual(self._checked(), ["temp"])

    def test_admin_only_items_are_not_ticked_and_cannot_be_ticked(self) -> None:
        from space_analyzer.models import ActionKind

        item = self._item("sys", ActionKind.DELETE_PATHS, requires_admin=True)
        item.default_selected = True
        self._show([item])
        self.assertEqual(self._checked(), [])
        self.page._tick_all()
        self.assertEqual(self._checked(), [])
        self.assertFalse(self.page._tick_all_button.isEnabled())

    def test_tick_all_and_untick_all_cover_the_tickable_items(self) -> None:
        from space_analyzer.models import ActionKind

        self._show([
            self._item("a", ActionKind.DELETE_PATHS),
            self._item("b", ActionKind.DELETE_PATHS),
            self._item("c", ActionKind.DELETE_PATHS, requires_admin=True),
        ])
        self.page._tick_all()
        self.assertEqual(self._checked(), ["a", "b"])
        self.page._untick_all()
        self.assertEqual(self._checked(), [])

    def test_clean_button_follows_the_selection(self) -> None:
        from space_analyzer.models import ActionKind

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.assertFalse(self.page._clean_button.isEnabled())
        self.page._tick_all()
        self.assertTrue(self.page._clean_button.isEnabled())
        self.page._untick_all()
        self.assertFalse(self.page._clean_button.isEnabled())

    def test_only_information_items_offers_nothing_to_tick(self) -> None:
        from space_analyzer.models import ActionKind

        self._show([self._item("pagefile", ActionKind.NONE)])
        self.assertFalse(self.page._clean_button.isEnabled())
        self.assertFalse(self.page._tick_all_button.isEnabled())
        self.assertEqual(self.page._selection_label.text(), "Nothing selected.")

    def test_selection_summary_mentions_admin_items_and_the_run_order(self) -> None:
        from space_analyzer.models import ActionKind

        self._show([
            self._item("a", ActionKind.DELETE_PATHS, size=2048),
            self._item("b", ActionKind.DELETE_PATHS, requires_admin=True),
        ])
        self.page._tick_all()
        text = self.page._selection_label.text()
        self.assertIn("1 selected, about 2.0 KB", text)
        self.assertIn("administrator rights", text)
        self.assertIn("emptied last", text)

    def test_rows_show_the_risk_label_and_the_size(self) -> None:
        from space_analyzer.models import ActionKind

        item = self._item("a", ActionKind.DELETE_PATHS, size=2048)
        self._show([item])
        (entry,) = self.page._all_rows()
        self.assertEqual(entry.row.text(1), "SAFE")
        self.assertEqual(entry.row.text(2), "2.0 KB")

    # ---------------------------------------------------------------- confirmation

    def test_confirmation_lists_each_item_with_its_size_and_warnings(self) -> None:
        from space_analyzer.models import ActionKind
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS, size=2048, warnings=["Careful"])])
        self.page._tick_all()
        with mock.patch.object(dialogs, "confirm", return_value=False) as confirm:
            self.page._clean_selected()
        # The first positional argument is the parent widget, the second the title.
        self.assertIs(confirm.call_args.args[0], self.page)
        self.assertEqual(confirm.call_args.args[1], "Clean selected items?")
        self.assertEqual(confirm.call_args.args[3], ["A: 2.0 KB", "Careful"])

    def test_permanent_selection_is_confirmed_as_destructive(self) -> None:
        from space_analyzer.models import ActionKind
        from space_analyzer.ui import dialogs

        self._show([self._item("bin", ActionKind.EMPTY_RECYCLE_BIN)])
        self.page._tick_all()
        with mock.patch.object(dialogs, "confirm", return_value=False) as confirm:
            self.page._clean_selected()
        self.assertTrue(confirm.call_args.kwargs["destructive"])
        self.assertIn("permanently deletes everything in the Recycle Bin", confirm.call_args.args[2])

    def test_recycle_move_selection_is_not_destructive(self) -> None:
        from space_analyzer.models import ActionKind
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        with mock.patch.object(dialogs, "confirm", return_value=False) as confirm:
            self.page._clean_selected()
        self.assertFalse(confirm.call_args.kwargs["destructive"])
        self.assertIn("Recycle Bin", confirm.call_args.args[2])

    def test_declining_the_confirmation_runs_nothing(self) -> None:
        from space_analyzer import cleaner
        from space_analyzer.models import ActionKind
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        with mock.patch.object(dialogs, "confirm", return_value=False), \
                mock.patch.object(cleaner, "perform") as perform:
            self.page._clean_selected()
            perform.assert_not_called()

    def test_cleaning_with_nothing_ticked_does_nothing(self) -> None:
        from space_analyzer.models import ActionKind

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._clean_selected()
        self.assertEqual(self.page._status.text(), "Nothing selected.")

    # ---------------------------------------------------------------- running

    def test_clean_runs_the_recycle_bin_last(self) -> None:
        from space_analyzer import cleaner
        from space_analyzer.models import ActionKind, ActionReport
        from space_analyzer.ui import dialogs

        order = []

        def fake_perform(item, **kwargs):
            order.append(item.item_id)
            return ActionReport(action=item.action.value, title=item.title)

        bin_item = self._item("bin", ActionKind.EMPTY_RECYCLE_BIN)
        self._show([bin_item, self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        with mock.patch.object(cleaner, "perform", side_effect=fake_perform), \
                mock.patch.object(dialogs, "confirm", return_value=True), \
                mock.patch.object(dialogs, "show_reports"):
            self.page._clean_selected()
            self._wait_idle()
        self.assertEqual(order, ["a", "bin"])

    def test_reports_are_shown_and_the_bin_is_offered_after_a_move(self) -> None:
        from space_analyzer import cleaner
        from space_analyzer.models import ActionKind, ActionReport
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        report = ActionReport(action=ActionKind.DELETE_PATHS.value, removed_count=1, bytes_removed=10)
        with mock.patch.object(cleaner, "perform", return_value=report), \
                mock.patch.object(dialogs, "confirm", side_effect=self._yes_then_no) as confirm, \
                mock.patch.object(dialogs, "show_reports") as show:
            self.page._clean_selected()
            self._wait_idle()
        self.assertEqual(show.call_count, 1)
        self.assertEqual(show.call_args.args[2], [report])
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(confirm.call_args_list[1].args[1], "Empty the Recycle Bin?")
        self.assertEqual(self.page._status.text(), "COMPLETE — cleanup finished")

    def test_no_bin_offer_when_nothing_was_moved(self) -> None:
        from space_analyzer import cleaner
        from space_analyzer.models import ActionKind, ActionReport
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        report = ActionReport(action=ActionKind.DELETE_PATHS.value, removed_count=0)
        with mock.patch.object(cleaner, "perform", return_value=report), \
                mock.patch.object(dialogs, "confirm", side_effect=self._yes_then_no) as confirm, \
                mock.patch.object(dialogs, "show_reports"):
            self.page._clean_selected()
            self._wait_idle()
        self.assertEqual(confirm.call_count, 1)  # the main confirmation only, no bin offer
        self.assertEqual(self.page._status.text(), "COMPLETE — cleanup finished")

    def test_accepting_the_bin_offer_empties_the_bin(self) -> None:
        from space_analyzer import cleaner
        from space_analyzer.models import ActionKind, ActionReport
        from space_analyzer.ui import dialogs

        self._show([self._item("a", ActionKind.DELETE_PATHS)])
        self.page._tick_all()
        moved = ActionReport(action=ActionKind.DELETE_PATHS.value, removed_count=1, bytes_removed=10)
        emptied = ActionReport(action=ActionKind.EMPTY_RECYCLE_BIN.value, removed_count=3)

        def fake_perform(item, **kwargs):
            return moved if item.action is ActionKind.DELETE_PATHS else emptied

        with mock.patch.object(cleaner, "perform", side_effect=fake_perform), \
                mock.patch.object(dialogs, "confirm", return_value=True) as confirm, \
                mock.patch.object(dialogs, "show_reports") as show:
            self.page._clean_selected()
            self._wait_idle()
        self.assertEqual(confirm.call_count, 2)  # the confirmation, then the bin offer
        self.assertEqual(show.call_count, 2)  # the cleanup report, then the bin report
        self.assertEqual(self.page._status.text(), "COMPLETE — the Recycle Bin is empty")

    def test_admin_only_item_is_refused_by_the_engine(self) -> None:
        from PySide6.QtCore import Qt

        from space_analyzer.models import ActionFailure, ActionKind
        from space_analyzer.ui import dialogs

        self._show([self._item("sys", ActionKind.DELETE_PATHS, requires_admin=True)])
        entry = self.page._all_rows()[0]
        # Tick it by hand: the page refuses to, but the engine must still refuse the run.
        entry.row.setFlags(entry.row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        entry.row.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(self._checked(), ["sys"])
        with mock.patch.object(dialogs, "confirm", return_value=True), \
                mock.patch.object(dialogs, "show_reports") as show:
            self.page._clean_selected()
            self._wait_idle()
        reports = show.call_args.args[2]
        self.assertEqual(reports[0].failed, [ActionFailure("Sys", "Requires administrator")])

    # ---------------------------------------------------------------- misc

    def test_status_line_is_updated_from_the_engine(self) -> None:
        self.page._on_progress("Measuring something")
        self.assertEqual(self.page._status.text(), "Measuring something")

    def test_analyze_failure_is_shown(self) -> None:
        self.page._on_analyze_failed("boom")
        self.assertEqual(self.page._status.text(), "FAILED — boom")

    def test_empty_result_says_that_nothing_was_found(self) -> None:
        self.page._on_analyze_succeeded([])
        self.assertEqual(self.page._status.text(), "Nothing to clean up was found on this PC.")
        self.assertEqual(self.page._groups, [])

    def test_shutdown_without_a_worker_is_safe(self) -> None:
        self.page.shutdown()


if __name__ == "__main__":
    unittest.main()
