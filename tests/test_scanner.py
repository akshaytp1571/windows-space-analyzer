"""Tests for space_analyzer.scanner. Standard library only; temp folders only."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import types
import unittest
from unittest import mock

from space_analyzer import models, scanner


def _symlinks_supported() -> bool:
    """Return True when this process can create a symbolic link."""
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(tmp, "target")
        with open(target, "wb"):
            pass
        os.symlink(target, os.path.join(tmp, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_SYMLINKS = _symlinks_supported()


class ScanTestCase(unittest.TestCase):
    """Each test gets its own temp directory, removed afterwards."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _path(self, *parts: str) -> str:
        """Return a path under this test's temp directory."""
        return os.path.join(self.tmp, *parts)

    def _write(self, rel: str, size: int) -> str:
        """Create a file of `size` bytes (rel uses '/') and return its path."""
        full = self._path(*rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(b"x" * size)
        return full

    def _check_totals(self, node: models.ScanNode, max_children: int) -> None:
        """Every node keeps the child limit, and its totals equal the sum over its children."""
        self.assertLessEqual(len(node.children), max_children)
        if node.children:
            self.assertEqual(node.size, sum(c.size for c in node.children))
            self.assertEqual(node.file_count, sum(c.file_count for c in node.children))
            # The summary row (empty path) stands for its folders, so it adds no +1.
            self.assertEqual(
                node.dir_count,
                sum(c.dir_count + (1 if c.path else 0) for c in node.children),
            )
        for child in node.children:
            self._check_totals(child, max_children)


class ScanTotalsTests(ScanTestCase):
    def test_totals_and_counts(self):
        self._write("a.bin", 1000)
        self._write("sub1/b.bin", 2000)
        self._write("sub1/deep/c.bin", 3000)
        self._write("sub2/d.bin", 4000)
        self._write("sub2/e.bin", 500)
        os.makedirs(self._path("empty"))
        calls = []
        result = scanner.scan(self.tmp, progress=calls.append)
        self.assertEqual(result.total_size, 10500)
        self.assertEqual(result.file_count, 5)
        self.assertEqual(result.dir_count, 4)  # sub1, deep, sub2, empty
        self.assertEqual(result.root_node.path, self.tmp)
        self.assertEqual(result.root_node.size, 10500)
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.skipped, [])
        self.assertFalse(result.cancelled)
        children = result.root_node.children
        self.assertEqual([c.name for c in children], ["sub1", "sub2", "empty"])
        self.assertEqual((children[0].size, children[0].file_count, children[0].dir_count), (5000, 2, 1))
        self.assertTrue(calls)
        self.assertEqual(calls[0].root, self.tmp)

    def test_largest_files_are_sorted_and_limited(self):
        self._write("small.txt", 10)
        self._write("zero.txt", 0)
        self._write("mid/medium.bin", 5000)
        self._write("mid/big.bin", 90000)
        self._write("deep/x/huge.dat", 250000)
        result = scanner.scan(self.tmp, top_files=3)
        self.assertEqual([f.size for f in result.largest_files], [250000, 90000, 5000])
        self.assertEqual(
            [os.path.basename(f.path) for f in result.largest_files],
            ["huge.dat", "big.bin", "medium.bin"],
        )
        self.assertTrue(all(isinstance(f.modified, float) for f in result.largest_files))
        everything = scanner.scan(self.tmp, top_files=50)
        self.assertEqual(len(everything.largest_files), 4)  # zero-byte files are not listed
        self.assertEqual(scanner.scan(self.tmp, top_files=0).largest_files, [])

    def test_folders_below_max_depth_fold_into_their_ancestor(self):
        self._write("a/b/c/file.bin", 500)
        result = scanner.scan(self.tmp, max_depth=1)
        children = result.root_node.children
        self.assertEqual([c.name for c in children], ["a"])
        self.assertEqual(children[0].children, [])
        self.assertEqual((children[0].size, children[0].file_count, children[0].dir_count), (500, 1, 2))
        self.assertEqual(result.root_node.dir_count, 3)
        self.assertEqual((result.total_size, result.file_count, result.dir_count), (500, 1, 3))

    def test_max_children_prunes_into_summary_row_with_exact_totals(self):
        for i in range(6):
            self._write(f"f{i}/data.bin", 1000 * (i + 1))
        result = scanner.scan(self.tmp, max_children=3)
        root = result.root_node
        self.assertEqual(len(root.children), 3)
        self.assertEqual([c.name for c in root.children[:2]], ["f5", "f4"])
        summary = root.children[2]
        self.assertEqual(summary.path, "")
        self.assertEqual(summary.name, "4 smaller folders")
        self.assertEqual((summary.size, summary.file_count, summary.dir_count), (10000, 4, 4))
        self.assertEqual((result.total_size, result.file_count, result.dir_count), (21000, 6, 6))
        self._check_totals(root, max_children=3)

    def test_large_tree_totals_stay_exact_under_pruning(self):
        expected_bytes = 0
        for i in range(20):
            for j in range(2):
                for k in range(3):
                    size = (i * 7 + j * 3 + k) % 500 + 1
                    self._write(f"big/d{i:02d}/s{j}/f{k}.bin", size)
                    expected_bytes += size
        result = scanner.scan(self._path("big"), max_children=5)
        self.assertEqual(result.total_size, expected_bytes)
        self.assertEqual(result.file_count, 120)
        self.assertEqual(result.dir_count, 60)  # 20 d-folders + 40 s-folders
        self._check_totals(result.root_node, max_children=5)

    def test_progress_is_throttled(self):
        for i in range(30):
            self._write(f"d{i:02d}/f.bin", 10)
        calls = []
        result = scanner.scan(self.tmp, progress=calls.append)
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), int(result.elapsed_seconds * 4) + 2)


class ScanCancelTests(ScanTestCase):
    def test_cancel_from_progress_returns_partial_result(self):
        self._write("top.bin", 100)
        for name in ("a", "b", "c"):
            self._write(f"{name}/data.bin", 1000)
        cancel = threading.Event()

        def stop_after_first(_snapshot) -> None:
            cancel.set()

        result = scanner.scan(self.tmp, progress=stop_after_first, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.total_size, 100)
        self.assertEqual(result.file_count, 1)

    def test_cancel_set_before_start_scans_nothing(self):
        self._write("top.bin", 100)
        cancel = threading.Event()
        cancel.set()
        result = scanner.scan(self.tmp, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.root_node.path, self.tmp)


@unittest.skipUnless(_SYMLINKS, "symbolic links cannot be created in this environment")
class ScanLinkTests(ScanTestCase):
    def test_links_are_recorded_as_skipped_and_not_followed(self):
        self._write("real/data.bin", 1000)
        os.symlink(self._path("real"), self._path("linked"), target_is_directory=True)
        os.symlink(self._path("real", "data.bin"), self._path("alias.bin"))
        result = scanner.scan(self.tmp)
        self.assertEqual((result.total_size, result.file_count, result.dir_count), (1000, 1, 1))
        self.assertEqual(result.error_count, 0)
        reasons = {os.path.basename(s.path): s.reason for s in result.skipped}
        self.assertEqual(reasons, {"linked": "link or junction", "alias.bin": "link or junction"})
        self.assertEqual(scanner.measure_path(self._path("linked")), (0, 0))
        self.assertEqual(scanner.measure_path(self._path("real")), (1000, 1))


class ScanErrorTests(ScanTestCase):
    def test_missing_root_is_recorded(self):
        result = scanner.scan(self._path("missing"))
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.root_node.error, "not found")
        self.assertEqual(len(result.skipped), 1)

    def test_bad_path_text_is_recorded_without_raising(self):
        result = scanner.scan("bad\x00name")
        self.assertEqual(result.error_count, 1)
        self.assertTrue(result.root_node.error)

    def test_file_given_as_root_is_recorded(self):
        path = self._write("solo.bin", 10)
        result = scanner.scan(path)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.root_node.error, "not a folder")

    def test_unreadable_subfolder_is_recorded_and_scan_continues(self):
        self._write("ok.bin", 700)
        self._write("locked/hidden.bin", 900)
        locked = os.path.normcase(os.path.abspath(self._path("locked")))
        real_scandir = os.scandir

        def fake_scandir(path):
            if os.path.normcase(os.path.abspath(path)) == locked:
                raise PermissionError(13, "Permission denied", path)
            return real_scandir(path)

        with mock.patch("os.scandir", side_effect=fake_scandir):
            result = scanner.scan(self.tmp)
        self.assertEqual(result.total_size, 700)
        self.assertEqual(result.error_count, 1)
        self.assertIn("access denied", [s.reason for s in result.skipped])
        locked_node = next(c for c in result.root_node.children if c.name == "locked")
        self.assertEqual(locked_node.error, "access denied")
        self.assertEqual(locked_node.size, 0)


class ReparseRuleTests(unittest.TestCase):
    """Windows reparse rules, checked with fake stat values so they run on any OS."""

    @staticmethod
    def _st(attrs: int, mode: int, *, tag: int | None = None, size: int = 0):
        fields = {"st_file_attributes": attrs, "st_mode": mode, "st_size": size}
        if tag is not None:
            fields["st_reparse_tag"] = tag
        return types.SimpleNamespace(**fields)

    def test_plain_file_and_folder_are_counted(self):
        file_st = self._st(0x20, stat.S_IFREG | 0o644, size=42)
        self.assertIsNone(scanner._skip_reason("f", file_st))
        self.assertEqual(scanner._file_bytes(file_st), 42)
        self.assertIsNone(scanner._skip_reason("d", self._st(0x10, stat.S_IFDIR | 0o755)))

    def test_cloud_only_file_counts_as_a_file_with_zero_bytes(self):
        for attrs in (0x400 | 0x1000, 0x400 | 0x400000):  # OFFLINE, RECALL_ON_DATA_ACCESS
            st = self._st(attrs, stat.S_IFREG | 0o644, tag=0x9000301A, size=4096)
            self.assertIsNone(scanner._skip_reason("cloud.bin", st))
            self.assertEqual(scanner._file_bytes(st), 0)

    def test_symlink_junction_and_wsl_symlink_are_links(self):
        cases = (
            self._st(0x400, stat.S_IFREG | 0o777, tag=0xA000000C),  # symlink file
            self._st(0x410, stat.S_IFDIR | 0o777, tag=0xA0000003),  # junction
            self._st(0x400, stat.S_IFREG | 0o777, tag=0xA000001D),  # WSL symlink
        )
        for st in cases:
            self.assertEqual(scanner._skip_reason("x", st), "link or junction")

    def test_other_reparse_folder_is_skipped_as_reparse_point(self):
        cloud_folder = self._st(0x410, stat.S_IFDIR | 0o755, tag=0x9000301A)
        self.assertEqual(scanner._skip_reason("x", cloud_folder), "reparse point")


class MeasurePathTests(ScanTestCase):
    def test_file_gives_its_own_size(self):
        path = self._write("solo.bin", 1234)
        self.assertEqual(scanner.measure_path(path), (1234, 1))

    def test_directory_is_walked_recursively(self):
        self._write("d/a.bin", 100)
        self._write("d/b/c.bin", 200)
        self._write("d/b/e/f.bin", 300)
        self.assertEqual(scanner.measure_path(self._path("d")), (600, 3))

    def test_missing_path_gives_zero(self):
        self.assertEqual(scanner.measure_path(self._path("nope")), (0, 0))

    def test_cancel_set_before_start_returns_zero(self):
        self._write("d/a.bin", 100)
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(scanner.measure_path(self._path("d"), cancel=cancel), (0, 0))


if __name__ == "__main__":
    unittest.main()
