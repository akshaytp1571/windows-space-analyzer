"""Tests for space_analyzer.duplicates. Every test works inside a temp folder it creates."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
import types
import unittest
from unittest import mock

from space_analyzer import duplicates, system
from space_analyzer.models import DuplicateGroup

TEMP_PREFIX = "wsa-test-"
KIB = 1024
MIB = 1024 * KIB
REAL_OPEN = open  # kept so patched open() calls can still read real files


def make_root() -> str:
    """Create an empty temp folder for one test."""
    return tempfile.mkdtemp(prefix=TEMP_PREFIX)


def remove_root(path: str) -> None:
    """Delete a folder made by make_root. Refuses any other path, so tests cannot touch real data."""
    real = os.path.realpath(path)
    parent = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    if os.path.normcase(os.path.dirname(real)) != parent or not os.path.basename(real).startswith(TEMP_PREFIX):
        raise RuntimeError(f"Refusing to delete {path}: it is not a test folder")
    shutil.rmtree(real, ignore_errors=True)


def content(size: int, seed: int) -> bytes:
    """Deterministic bytes of the given size. Different seeds give different content."""
    block = hashlib.sha256(f"seed-{seed}".encode("ascii")).digest()
    return (block * (size // len(block) + 1))[:size]


def write(path: str, data: bytes) -> str:
    """Create a file with the given content, making parent folders as needed. Returns the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def symlinks_work() -> bool:
    """True when this process can create symbolic links. Windows may need developer mode."""
    base = make_root()
    try:
        target = os.path.join(base, "target.bin")
        write(target, b"x")
        os.symlink(target, os.path.join(base, "link.bin"))
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        remove_root(base)


CAN_SYMLINK = symlinks_work()


class DuplicateTestCase(unittest.TestCase):
    """Gives each test its own temp folder and empty protected-path lists."""

    def setUp(self) -> None:
        self.root = make_root()
        self.addCleanup(remove_root, self.root)
        for name in ("protected_roots", "system_allowlist"):
            patcher = mock.patch.object(system, name, return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)

    def path(self, *parts: str) -> str:
        """A path inside this test's temp folder."""
        return os.path.join(self.root, *parts)


class FindDuplicatesTests(DuplicateTestCase):
    """find_duplicates groups identical files and skips everything else."""

    def test_identical_pair_forms_one_group_and_the_different_file_is_left_out(self) -> None:
        same = content(200 * KIB, 1)
        a = write(self.path("a.bin"), same)
        b = write(self.path("sub", "b.bin"), same)
        other = write(self.path("c.bin"), content(200 * KIB, 2))
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertIsInstance(group, DuplicateGroup)
        self.assertEqual(set(group.paths), {a, b})
        self.assertNotIn(other, group.paths)
        self.assertEqual(group.size, 200 * KIB)
        self.assertEqual(group.digest, hashlib.sha256(same).hexdigest())

    def test_all_different_files_give_no_groups(self) -> None:
        for index in range(3):
            write(self.path(f"f{index}.bin"), content(200 * KIB, index))
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=1), [])

    def test_files_that_match_only_in_the_first_64_kib_are_not_grouped(self) -> None:
        head = content(64 * KIB, 1)
        a = write(self.path("a.bin"), head + content(100 * KIB, 2))
        b = write(self.path("b.bin"), head + content(100 * KIB, 3))
        c = write(self.path("c.bin"), head + content(100 * KIB, 2))
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual([set(group.paths) for group in groups], [{a, c}])
        self.assertNotIn(b, groups[0].paths)

    def test_files_of_different_sizes_are_never_grouped(self) -> None:
        shared = content(200 * KIB, 1)
        write(self.path("a.bin"), shared)
        write(self.path("b.bin"), shared + b"!")
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=1), [])

    def test_min_size_excludes_small_files(self) -> None:
        small = content(10 * KIB, 1)
        write(self.path("small1.bin"), small)
        write(self.path("small2.bin"), small)
        big = content(300 * KIB, 2)
        big1 = write(self.path("big1.bin"), big)
        big2 = write(self.path("big2.bin"), big)
        groups = duplicates.find_duplicates(self.root, min_size=100 * KIB)
        self.assertEqual([set(group.paths) for group in groups], [{big1, big2}])
        self.assertEqual(len(duplicates.find_duplicates(self.root, min_size=1)), 2)

    def test_default_min_size_is_one_mebibyte(self) -> None:
        write(self.path("a.bin"), content(200 * KIB, 1))
        write(self.path("b.bin"), content(200 * KIB, 1))
        self.assertEqual(duplicates.find_duplicates(self.root), [])
        big = content(MIB, 2)
        write(self.path("c.bin"), big)
        write(self.path("d.bin"), big)
        self.assertEqual(len(duplicates.find_duplicates(self.root)), 1)

    def test_zero_byte_files_are_never_grouped(self) -> None:
        write(self.path("a.bin"), b"")
        write(self.path("b.bin"), b"")
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=0), [])

    def test_paths_are_ordered_oldest_modified_first(self) -> None:
        data = content(120 * KIB, 4)
        newest = write(self.path("a-newest.bin"), data)
        middle = write(self.path("b-middle.bin"), data)
        oldest = write(self.path("c-oldest.bin"), data)
        os.utime(oldest, (1_000_000_000, 1_000_000_000))
        os.utime(middle, (1_500_000_000, 1_500_000_000))
        os.utime(newest, (2_000_000_000, 2_000_000_000))
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual(groups[0].paths, [oldest, middle, newest])

    def test_result_is_sorted_by_reclaimable_space_and_capped(self) -> None:
        three_copies = content(300 * KIB, 7)  # reclaims 2 x 300 KB = 600 KB
        two_big = content(500 * KIB, 8)  # reclaims 1 x 500 KB = 500 KB
        two_small = content(100 * KIB, 9)  # reclaims 1 x 100 KB = 100 KB
        for name in ("x1", "x2", "x3"):
            write(self.path(f"{name}.bin"), three_copies)
        for name in ("y1", "y2"):
            write(self.path(f"{name}.bin"), two_big)
        for name in ("z1", "z2"):
            write(self.path(f"{name}.bin"), two_small)
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual([group.size for group in groups], [300 * KIB, 500 * KIB, 100 * KIB])
        self.assertEqual(
            [group.reclaimable_bytes for group in groups], [600 * KIB, 500 * KIB, 100 * KIB]
        )
        capped = duplicates.find_duplicates(self.root, min_size=1, max_groups=2)
        self.assertEqual([group.size for group in capped], [300 * KIB, 500 * KIB])
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=1, max_groups=0), [])

    def test_cancel_event_that_is_already_set_returns_no_groups(self) -> None:
        data = content(120 * KIB, 5)
        write(self.path("a.bin"), data)
        write(self.path("b.bin"), data)
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=1, cancel=cancel), [])

    def test_missing_root_gives_no_groups(self) -> None:
        self.assertEqual(duplicates.find_duplicates(self.path("missing"), min_size=1), [])

    @unittest.skipUnless(CAN_SYMLINK, "symbolic links cannot be created on this system")
    def test_links_are_not_followed(self) -> None:
        data = content(120 * KIB, 6)
        real_a = write(self.path("real", "a.bin"), data)
        real_b = write(self.path("real", "b.bin"), data)
        try:
            os.symlink(self.path("real"), self.path("alias"), target_is_directory=True)
        except OSError:
            self.skipTest("folder links cannot be created on this system")
        os.symlink(real_a, self.path("file-link.bin"))  # a link to a file must not count as a copy
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual([sorted(group.paths) for group in groups], [sorted([real_a, real_b])])

    def test_protected_folders_are_skipped(self) -> None:
        data = content(120 * KIB, 10)
        locked = self.path("Locked")
        write(os.path.join(locked, "a.bin"), data)
        write(os.path.join(locked, "b.bin"), data)
        with mock.patch.object(system, "protected_roots", return_value=[locked]):
            self.assertEqual(duplicates.find_duplicates(self.root, min_size=1), [])
            self.assertEqual(duplicates.find_duplicates(locked, min_size=1), [])

    def test_allowlisted_folder_in_a_protected_folder_is_scanned_only_as_the_root(self) -> None:
        data = content(120 * KIB, 11)
        locked = self.path("Locked")
        open_part = os.path.join(locked, "Open")
        a = write(os.path.join(open_part, "a.bin"), data)
        b = write(os.path.join(open_part, "b.bin"), data)
        write(os.path.join(locked, "c.bin"), data)
        with mock.patch.object(system, "protected_roots", return_value=[locked]), \
                mock.patch.object(system, "system_allowlist", return_value=[open_part]):
            as_root = duplicates.find_duplicates(open_part, min_size=1)
            from_above = duplicates.find_duplicates(self.root, min_size=1)
            protected_root = duplicates.find_duplicates(locked, min_size=1)
        self.assertEqual([set(group.paths) for group in as_root], [{a, b}])
        self.assertEqual(from_above, [])  # protected folders are never entered from above
        self.assertEqual(protected_root, [])

    def test_cloud_and_reparse_attributes_exclude_a_file(self) -> None:
        # Windows attributes are only present on Windows, so the check is made on fake stat data.
        def entry(attributes: int) -> types.SimpleNamespace:
            return types.SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_size=2 * MIB,
                st_nlink=1,
                st_ino=1,
                st_dev=1,
                st_file_attributes=attributes,
            )

        self.assertTrue(duplicates._is_candidate(entry(0x20), 1))  # archive attribute only
        self.assertFalse(duplicates._is_candidate(entry(0x1000), 1))  # OFFLINE: cloud-only
        self.assertFalse(duplicates._is_candidate(entry(0x400000), 1))  # RECALL_ON_DATA_ACCESS
        self.assertFalse(duplicates._is_candidate(entry(0x400), 1))  # reparse point
        self.assertFalse(duplicates._is_candidate(entry(0x20), 3 * MIB))  # below min_size

    def test_hard_links_count_as_one_copy(self) -> None:
        data = content(120 * KIB, 15)
        original = write(self.path("original.bin"), data)
        alias = self.path("alias.bin")
        try:
            os.link(original, alias)
        except OSError:
            self.skipTest("hard links cannot be created on this file system")
        # Two names for one file free no space, so they are not a duplicate group.
        self.assertEqual(duplicates.find_duplicates(self.root, min_size=1), [])
        copy = write(self.path("copy.bin"), data)
        groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual(len(groups), 1)
        names = {os.path.basename(path) for path in groups[0].paths}
        self.assertIn("copy.bin", names)
        self.assertEqual(len(names & {"original.bin", "alias.bin"}), 1)
        self.assertIn(copy, groups[0].paths)
        self.assertEqual(groups[0].reclaimable_bytes, 120 * KIB)

    def test_files_are_only_ever_opened_for_reading(self) -> None:
        data = content(120 * KIB, 13)
        a = write(self.path("a.bin"), data)
        b = write(self.path("b.bin"), data)
        before = {path: os.stat(path).st_mtime_ns for path in (a, b)}
        modes: list[str] = []

        def recording_open(path: str, mode: str = "r", *args: object, **kwargs: object) -> object:
            modes.append(mode)
            return REAL_OPEN(path, mode, *args, **kwargs)

        with mock.patch.object(duplicates, "open", recording_open, create=True):
            groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual(len(groups), 1)
        self.assertTrue(modes)
        self.assertEqual(set(modes), {"rb"})
        self.assertEqual({path: os.stat(path).st_mtime_ns for path in (a, b)}, before)
        with REAL_OPEN(a, "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_unreadable_files_are_skipped_silently(self) -> None:
        data = content(120 * KIB, 14)
        a = write(self.path("a.bin"), data)
        b = write(self.path("b.bin"), data)
        write(self.path("locked.bin"), data)

        def refusing_open(path: str, mode: str = "r", *args: object, **kwargs: object) -> object:
            if os.path.basename(path) == "locked.bin":
                raise PermissionError("access denied")
            return REAL_OPEN(path, mode, *args, **kwargs)

        with mock.patch.object(duplicates, "open", refusing_open, create=True):
            groups = duplicates.find_duplicates(self.root, min_size=1)
        self.assertEqual([set(group.paths) for group in groups], [{a, b}])

    def test_progress_reports_folder_names_as_text(self) -> None:
        for index in range(5):
            write(self.path(f"folder{index}", "file.bin"), content(2 * KIB, index))
        calls: list[str] = []
        duplicates.find_duplicates(self.root, min_size=1, progress=calls.append)
        self.assertTrue(calls)
        self.assertTrue(all(isinstance(text, str) for text in calls))
        self.assertEqual(calls[0], self.root)

    def test_progress_calls_are_at_least_a_quarter_second_apart(self) -> None:
        clock = [0.0]
        fake_time = mock.Mock()
        fake_time.monotonic.side_effect = lambda: clock[0]
        stamps: list[float] = []
        reporter = duplicates._Reporter(lambda _text: stamps.append(clock[0]))
        with mock.patch.object(duplicates, "time", fake_time):
            for step in range(300):  # three simulated seconds, one call every 0.01 s
                clock[0] = step * 0.01
                reporter("folder")
        self.assertGreaterEqual(len(stamps), 4)
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        self.assertTrue(all(gap >= 0.25 - 1e-9 for gap in gaps))


if __name__ == "__main__":
    unittest.main()
