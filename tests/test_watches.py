"""Tests for watches: budgets, measurement, storage, and the refusal rules."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from space_analyzer import system, watches

MB = 1024 * 1024


class WatchModelTests(unittest.TestCase):
    """The budget arithmetic on a single watch."""

    def test_a_watch_without_a_budget_can_never_alert(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=0, last_size=10**12)
        self.assertFalse(watch.has_budget)
        self.assertFalse(watch.over_budget)
        self.assertEqual(watch.used_fraction, 0.0)

    def test_a_size_above_the_budget_alerts(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=100, last_size=101)
        self.assertTrue(watch.over_budget)

    def test_a_size_equal_to_the_budget_does_not_alert(self) -> None:
        # "At budget" is within budget; only exceeding it counts.
        watch = watches.Watch(path="C:\\a", budget_bytes=100, last_size=100)
        self.assertFalse(watch.over_budget)

    def test_the_used_fraction_is_size_over_budget(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=100, last_size=250)
        self.assertEqual(watch.used_fraction, 2.5)

    def test_a_watch_round_trips_through_a_dict(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=5, last_size=7, last_checked=9.0)
        again = watches.Watch.from_dict(watch.to_dict())
        self.assertEqual(again.path, "C:\\a")
        self.assertEqual(again.last_size, 7)

    def test_a_bad_dict_gives_none(self) -> None:
        self.assertIsNone(watches.Watch.from_dict("nope"))
        self.assertIsNone(watches.Watch.from_dict({}))
        self.assertIsNone(watches.Watch.from_dict({"path": ""}))
        self.assertIsNone(watches.Watch.from_dict({"path": 5}))

    def test_a_negative_budget_is_clamped_to_zero(self) -> None:
        self.assertEqual(watches.Watch.from_dict({"path": "C:\\a", "budget_bytes": -5}).budget_bytes, 0)


class ReadingTests(unittest.TestCase):
    """A single reading and its arithmetic."""

    def test_a_reading_under_budget_is_not_over(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=100)
        self.assertFalse(watches.WatchReading(watch=watch, size=50).over_budget)

    def test_remaining_can_go_negative(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=100)
        self.assertEqual(watches.WatchReading(watch=watch, size=150).remaining, -50)

    def test_an_errored_reading_is_never_over_budget(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=100)
        reading = watches.WatchReading(watch=watch, size=0, error="gone")
        self.assertFalse(reading.over_budget)
        self.assertFalse(reading.ok)

    def test_remaining_is_zero_without_a_budget(self) -> None:
        watch = watches.Watch(path="C:\\a", budget_bytes=0)
        self.assertEqual(watches.WatchReading(watch=watch, size=99).remaining, 0)


class SummaryTests(unittest.TestCase):
    """The summary ranks what needs attention."""

    def _reading(self, path: str, budget: int, size: int, error: str = ""):
        return watches.WatchReading(
            watch=watches.Watch(path=path, budget_bytes=budget), size=size, error=error
        )

    def test_over_budget_readings_are_worst_first(self) -> None:
        summary = watches.WatchSummary(readings=[
            self._reading("a", 100, 150),
            self._reading("b", 100, 400),
        ])
        self.assertEqual([one.watch.path for one in summary.over], ["b", "a"])

    def test_failures_are_separated(self) -> None:
        summary = watches.WatchSummary(readings=[
            self._reading("a", 100, 150),
            self._reading("b", 100, 0, error="gone"),
        ])
        self.assertEqual(len(summary.failures), 1)

    def test_the_total_skips_failures(self) -> None:
        summary = watches.WatchSummary(readings=[
            self._reading("a", 0, 100),
            self._reading("b", 0, 0, error="gone"),
        ])
        self.assertEqual(summary.total_size, 100)

    def test_needs_attention_is_true_only_when_over(self) -> None:
        self.assertFalse(watches.WatchSummary(readings=[self._reading("a", 100, 50)]).needs_attention)
        self.assertTrue(watches.WatchSummary(readings=[self._reading("a", 100, 150)]).needs_attention)

    def test_the_description_counts_watches_and_overages(self) -> None:
        self.assertEqual(watches.describe(watches.WatchSummary()), "No folders watched")
        self.assertIn("all within budget", watches.describe(
            watches.WatchSummary(readings=[self._reading("a", 100, 10)])
        ))
        self.assertIn("1 over budget", watches.describe(
            watches.WatchSummary(readings=[self._reading("a", 100, 999)])
        ))


class StorageTests(unittest.TestCase):
    """The watch list survives a round trip and never breaks on a bad file."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_watch_test_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "watches.json")
        self.folder_a = os.path.join(self.folder, "a")
        os.makedirs(self.folder_a)

    def test_add_and_load_round_trip(self) -> None:
        self.assertIsNone(watches.add(watches.Watch(path=self.folder_a, budget_bytes=2 * MB), self.path))
        entries = watches.load(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, self.folder_a)

    def test_the_same_folder_cannot_be_added_twice(self) -> None:
        watches.add(watches.Watch(path=self.folder_a), self.path)
        self.assertEqual(
            watches.add(watches.Watch(path=self.folder_a.upper()), self.path),
            "That folder is already watched",
        )

    def test_remove_drops_the_watch(self) -> None:
        watches.add(watches.Watch(path=self.folder_a), self.path)
        self.assertTrue(watches.remove(self.folder_a, self.path))
        self.assertEqual(watches.load(self.path), [])

    def test_a_missing_file_gives_an_empty_list(self) -> None:
        self.assertEqual(watches.load(os.path.join(self.folder, "nope.json")), [])

    def test_a_corrupt_file_gives_an_empty_list(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ broken")
        self.assertEqual(watches.load(self.path), [])

    def test_an_unknown_version_gives_an_empty_list(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 42, "watches": [{"path": "C:\\a"}]}, handle)
        self.assertEqual(watches.load(self.path), [])

    def test_a_bad_entry_is_dropped(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": watches._WATCH_VERSION,
                       "watches": [{}, {"path": self.folder_a}]}, handle)
        self.assertEqual(len(watches.load(self.path)), 1)

    def test_no_temp_file_is_left_behind(self) -> None:
        watches.add(watches.Watch(path=self.folder_a), self.path)
        self.assertEqual([n for n in os.listdir(self.folder) if n.endswith(".tmp")], [])

    def test_clear_removes_the_file(self) -> None:
        watches.add(watches.Watch(path=self.folder_a), self.path)
        self.assertTrue(watches.clear(self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_too_many_watches_are_refused(self) -> None:
        many = [watches.Watch(path=self.folder_a)]
        with mock.patch.object(watches, "load", return_value=[
            watches.Watch(path=f"C:\\x{i}") for i in range(watches._MAX_WATCHES)
        ]):
            reason = watches.add(many[0], self.path)
        self.assertIn("can be watched", reason)


class RefusalTests(unittest.TestCase):
    """A watch is only created for a real folder, with a sane budget."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_watch_refuse_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "watches.json")

    def test_a_missing_folder_is_refused(self) -> None:
        self.assertEqual(
            watches.budget_problem(os.path.join(self.folder, "nope"), 0),
            "That folder does not exist",
        )

    def test_an_empty_path_is_refused(self) -> None:
        self.assertEqual(watches.budget_problem("", 0), "No folder given")
        self.assertEqual(watches.budget_problem("   ", 0), "No folder given")

    def test_a_tiny_budget_is_refused(self) -> None:
        self.assertIn("at least 1 MB", watches.budget_problem(self.folder, 1024))

    def test_no_budget_is_allowed(self) -> None:
        self.assertIsNone(watches.budget_problem(self.folder, 0))

    def test_a_protected_system_folder_is_refused(self) -> None:
        with mock.patch.object(watches.safety, "validate_target", return_value="Protected system location"):
            self.assertIn("protected system folder", watches.budget_problem(self.folder, 0))

    def test_a_link_is_refused(self) -> None:
        with mock.patch.object(system, "is_link", return_value=True):
            self.assertIn("not followed", watches.budget_problem(self.folder, 0))


class CheckTests(unittest.TestCase):
    """Measuring watches updates them and reports failures distinctly."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_watch_check_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.folder_a = os.path.join(self.folder, "a")
        self.folder_b = os.path.join(self.folder, "b")
        os.makedirs(self.folder_a)
        os.makedirs(self.folder_b)

    def test_a_measured_watch_records_its_size(self) -> None:
        watch = watches.Watch(path=self.folder_a, budget_bytes=2 * MB)
        with mock.patch.object(watches.scanner, "measure_path", return_value=(5 * MB, 3)):
            summary = watches.check([watch])
        self.assertEqual(summary.readings[0].size, 5 * MB)
        self.assertTrue(summary.readings[0].over_budget)
        self.assertGreater(watch.last_checked, 0)

    def test_a_missing_folder_reports_an_error_not_a_zero(self) -> None:
        watch = watches.Watch(path=os.path.join(self.folder, "gone"), budget_bytes=0)
        summary = watches.check([watch])
        self.assertEqual(summary.readings[0].error, "That folder no longer exists")
        self.assertFalse(summary.readings[0].ok)

    def test_a_measure_failure_is_reported(self) -> None:
        watch = watches.Watch(path=self.folder_a, budget_bytes=0)
        with mock.patch.object(watches.scanner, "measure_path", side_effect=OSError("denied")):
            summary = watches.check([watch])
        self.assertEqual(summary.readings[0].error, "denied")

    def test_a_cancelled_check_stops_early(self) -> None:
        import threading

        cancel = threading.Event()
        cancel.set()
        summary = watches.check(
            [watches.Watch(path=self.folder_a), watches.Watch(path=self.folder_b)], cancel=cancel
        )
        self.assertEqual(summary.readings, [])

    def test_progress_messages_are_sent(self) -> None:
        seen: list[str] = []
        with mock.patch.object(watches.scanner, "measure_path", return_value=(1, 1)):
            watches.check([watches.Watch(path=self.folder_a)], progress=seen.append)
        self.assertTrue(any("Measuring" in message for message in seen))

    def test_check_without_a_list_reads_the_stored_one(self) -> None:
        with mock.patch.object(watches, "load", return_value=[]) as loader:
            watches.check()
        loader.assert_called_once()

    def test_the_total_covers_every_readable_watch(self) -> None:
        both = [watches.Watch(path=self.folder_a), watches.Watch(path=self.folder_b)]
        with mock.patch.object(watches.scanner, "measure_path", return_value=(3 * MB, 1)):
            summary = watches.check(both)
        self.assertEqual(summary.total_size, 6 * MB)


if __name__ == "__main__":
    unittest.main()