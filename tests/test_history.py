"""Tests for history: snapshots, the delta view, and a corrupt file never breaking."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from space_analyzer import history
from space_analyzer.models import ScanNode, ScanResult


def _scan(root: str = "C:\\", folders=None, total: int = 1000, elapsed: float = 1.0) -> ScanResult:
    node = ScanNode(path=root, name=root, size=total, file_count=10, dir_count=2)
    for name, size, files in (folders or []):
        node.children.append(
            ScanNode(path=root.rstrip("\\") + "\\" + name, name=name,
                     size=size, file_count=files, dir_count=0)
        )
    return ScanResult(
        root=root, root_node=node, total_size=total, file_count=10,
        dir_count=2, elapsed_seconds=elapsed,
    )


class SnapshotTests(unittest.TestCase):
    """A snapshot keeps the summary and the largest folders, nothing more."""

    def test_a_snapshot_records_the_totals(self) -> None:
        snap = history.make_snapshot(_scan(total=5000), free_bytes=70, capacity_bytes=100)
        self.assertEqual(snap.total_size, 5000)
        self.assertEqual(snap.free_bytes, 70)
        self.assertEqual(snap.capacity_bytes, 100)

    def test_folders_are_kept_largest_first(self) -> None:
        scan = _scan(folders=[("small", 10, 1), ("big", 900, 9), ("mid", 100, 5)])
        snap = history.make_snapshot(scan)
        self.assertEqual([one.path for one in snap.folders],
                         ["C:\\big", "C:\\mid", "C:\\small"])

    def test_the_folder_list_is_capped(self) -> None:
        scan = _scan(folders=[(f"f{i}", i, 1) for i in range(100)])
        snap = history.make_snapshot(scan)
        self.assertLessEqual(len(snap.folders), history._MAX_FOLDERS)

    def test_only_top_level_folders_are_kept(self) -> None:
        # Nested paths must not leak into the history file.
        scan = _scan()
        deep = ScanNode(path="C:\\Users\\me\\deep", name="deep", size=5)
        scan.root_node.children.append(deep)
        snap = history.make_snapshot(scan)
        self.assertEqual(len(snap.folders), 1)

    def test_a_dict_round_trip_preserves_the_fields(self) -> None:
        snap = history.make_snapshot(_scan(total=42), free_bytes=1, capacity_bytes=2)
        again = history.ScanSnapshot.from_dict(snap.to_dict())
        self.assertEqual(again.total_size, 42)
        self.assertEqual(again.free_bytes, 1)

    def test_a_bad_dict_gives_none(self) -> None:
        self.assertIsNone(history.ScanSnapshot.from_dict("nonsense"))
        self.assertIsNone(history.ScanSnapshot.from_dict({}))
        self.assertIsNone(history.ScanSnapshot.from_dict({"timestamp": "x"}))


class StorageTests(unittest.TestCase):
    """The file is written atomically and a broken file is treated as empty."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_history_test_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "history.json")

    def _snap(self, total: int = 100, stamp: float = 1.0) -> history.ScanSnapshot:
        return history.make_snapshot(_scan(total=total), timestamp=stamp)

    def test_an_appended_snapshot_can_be_read_back(self) -> None:
        self.assertTrue(history.append(self._snap(), self.path))
        entries = history.load(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].total_size, 100)

    def test_several_snapshots_keep_their_order(self) -> None:
        for total in (1, 2, 3):
            history.append(self._snap(total=total, stamp=float(total)), self.path)
        self.assertEqual([one.total_size for one in history.load(self.path)], [1, 2, 3])

    def test_the_limit_drops_the_oldest(self) -> None:
        for total in range(10):
            history.append(self._snap(total=total, stamp=float(total)), self.path, limit=3)
        self.assertEqual([one.total_size for one in history.load(self.path)], [7, 8, 9])

    def test_a_missing_file_gives_an_empty_list(self) -> None:
        self.assertEqual(history.load(os.path.join(self.folder, "nope.json")), [])

    def test_a_corrupt_file_gives_an_empty_list(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        self.assertEqual(history.load(self.path), [])

    def test_a_truncated_json_file_gives_an_empty_list(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"version": 1, "scans": [')
        self.assertEqual(history.load(self.path), [])

    def test_an_unknown_version_gives_an_empty_list(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 99, "scans": [{"timestamp": 1, "root": "C:\\",
                                                 "total_size": 5}]}, handle)
        self.assertEqual(history.load(self.path), [])

    def test_a_bad_entry_is_dropped_but_good_ones_survive(self) -> None:
        payload = {
            "version": history._HISTORY_VERSION,
            "scans": [
                {"root": "C:\\"},  # no timestamp: unusable
                {"timestamp": 5.0, "root": "C:\\", "total_size": 7},
            ],
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        entries = history.load(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].total_size, 7)

    def test_no_temp_file_is_left_behind(self) -> None:
        history.append(self._snap(), self.path)
        leftovers = [one for one in os.listdir(self.folder) if one.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_clear_removes_the_file(self) -> None:
        history.append(self._snap(), self.path)
        self.assertTrue(history.clear(self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_clear_on_a_missing_file_is_success(self) -> None:
        self.assertTrue(history.clear(os.path.join(self.folder, "absent.json")))


class LookupTests(unittest.TestCase):
    """Lookups are per drive and ignore case and a trailing separator."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_history_lookup_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "history.json")

    def test_the_previous_snapshot_is_the_one_before_the_latest(self) -> None:
        history.append(history.make_snapshot(_scan(total=1), timestamp=1.0), self.path)
        history.append(history.make_snapshot(_scan(total=2), timestamp=2.0), self.path)
        self.assertEqual(history.previous_for("C:\\", self.path).total_size, 1)
        self.assertEqual(history.latest_for("C:\\", self.path).total_size, 2)

    def test_one_snapshot_has_no_previous(self) -> None:
        history.append(history.make_snapshot(_scan(), timestamp=1.0), self.path)
        self.assertIsNone(history.previous_for("C:\\", self.path))

    def test_an_empty_history_has_no_entries(self) -> None:
        self.assertIsNone(history.latest_for("C:\\", self.path))

    def test_lookups_are_scoped_to_a_drive(self) -> None:
        history.append(history.make_snapshot(_scan(root="C:\\", total=1), timestamp=1.0), self.path)
        history.append(history.make_snapshot(_scan(root="D:\\", total=2), timestamp=2.0), self.path)
        self.assertEqual(history.latest_for("C:\\", self.path).total_size, 1)
        self.assertEqual(history.latest_for("D:\\", self.path).total_size, 2)

    def test_a_trailing_separator_and_case_are_ignored(self) -> None:
        history.append(history.make_snapshot(_scan(root="C:\\", total=9), timestamp=1.0), self.path)
        self.assertIsNotNone(history.latest_for("c:", self.path))


class CompareTests(unittest.TestCase):
    """The delta view names what grew, shrank, appeared and vanished."""

    def _pair(self, before, after):
        old = history.make_snapshot(_scan(folders=before, total=sum(size for _, size, _ in before) or 1), timestamp=1.0)
        new = history.make_snapshot(_scan(folders=after, total=sum(size for _, size, _ in after) or 1), timestamp=2.0)
        return old, new

    def test_a_grown_folder_is_reported(self) -> None:
        old, new = self._pair([("Users", 100, 1)], [("Users", 300, 2)])
        delta = history.compare(old, new)
        self.assertEqual(len(delta.grew), 1)
        self.assertEqual(delta.grew[0].delta, 200)

    def test_a_shrunk_folder_is_reported(self) -> None:
        old, new = self._pair([("Users", 300, 1)], [("Users", 100, 1)])
        delta = history.compare(old, new)
        self.assertEqual(len(delta.shrank), 1)
        self.assertEqual(delta.shrank[0].delta, -200)

    def test_a_new_folder_is_reported_as_appeared(self) -> None:
        old, new = self._pair([("Users", 100, 1)], [("Users", 100, 1), ("Games", 500, 1)])
        delta = history.compare(old, new)
        self.assertEqual(len(delta.appeared), 1)
        self.assertEqual(delta.appeared[0].path, "C:\\Games")

    def test_a_removed_folder_is_reported_as_vanished(self) -> None:
        old, new = self._pair([("Users", 100, 1), ("Games", 500, 1)], [("Users", 100, 1)])
        delta = history.compare(old, new)
        self.assertEqual(len(delta.vanished), 1)
        self.assertEqual(delta.vanished[0].path, "C:\\Games")

    def test_an_unchanged_scan_has_no_changes(self) -> None:
        old, new = self._pair([("Users", 100, 1)], [("Users", 100, 1)])
        self.assertFalse(history.compare(old, new).has_changes)

    def test_small_changes_are_hidden_by_min_bytes(self) -> None:
        old, new = self._pair([("Users", 100, 1)], [("Users", 105, 1)])
        self.assertEqual(history.compare(old, new, min_bytes=1000).grew, [])

    def test_the_largest_change_comes_first(self) -> None:
        old, new = self._pair(
            [("a", 100, 1), ("b", 100, 1)], [("a", 200, 1), ("b", 900, 1)]
        )
        delta = history.compare(old, new)
        self.assertEqual([one.path for one in delta.grew], ["C:\\b", "C:\\a"])

    def test_the_total_and_free_deltas_are_exposed(self) -> None:
        before = history.make_snapshot(_scan(total=100), free_bytes=10, timestamp=1.0)
        after = history.make_snapshot(_scan(total=150), free_bytes=40, timestamp=2.0)
        delta = history.compare(before, after)
        self.assertEqual(delta.total_delta, 50)
        self.assertEqual(delta.free_delta, 30)

    def test_folder_change_exposes_before_and_after(self) -> None:
        change = history.FolderChange(path="C:\\a", before=10, after=30)
        self.assertEqual(change.delta, 20)


if __name__ == "__main__":
    unittest.main()