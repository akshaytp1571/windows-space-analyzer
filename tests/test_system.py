"""Tests for the pure helpers in space_analyzer.system. They run on Linux and touch only temp folders."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from space_analyzer import system

TEMP_PREFIX = "wsa-test-"

VSSADMIN_TWO_COPIES = """vssadmin 1.1 - Volume Shadow Copy management command-line tool
(C) Copyright 2001-2013 Microsoft Corp.

Contents of shadow copy set ID: {11111111-2222-3333-4444-555555555555}
   Contained 1 shadow copies at creation time: 10/20/2026 2:00:00 AM
      Shadow Copy ID: {aaaaaaaa-0000-0000-0000-000000000001}
         Original Volume: (C:)
         Shadow Copy Volume: example-volume-1

Contents of shadow copy set ID: {66666666-7777-8888-9999-000000000000}
   Contained 1 shadow copies at creation time: 10/21/2026 2:00:00 AM
      Shadow Copy ID: {bbbbbbbb-0000-0000-0000-000000000002}
         Original Volume: (C:)
         Shadow Copy Volume: example-volume-2
"""

VSSADMIN_NO_ITEMS = """vssadmin 1.1 - Volume Shadow Copy management command-line tool
(C) Copyright 2001-2013 Microsoft Corp.

No items found that satisfy the query.
"""


def make_base() -> str:
    """Create an empty temp folder for one test."""
    return tempfile.mkdtemp(prefix=TEMP_PREFIX)


def remove_base(path: str) -> None:
    """Delete a folder made by make_base. Refuses any other path, so tests cannot touch real data."""
    real = os.path.realpath(path)
    parent = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    if os.path.normcase(os.path.dirname(real)) != parent or not os.path.basename(real).startswith(TEMP_PREFIX):
        raise RuntimeError(f"Refusing to delete {path}: it is not a test folder")
    shutil.rmtree(real, ignore_errors=True)


def symlinks_work() -> bool:
    """True when this process can create symbolic links. Windows may need developer mode."""
    base = make_base()
    try:
        target = os.path.join(base, "target.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        os.symlink(target, os.path.join(base, "link.txt"))
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        remove_base(base)


CAN_SYMLINK = symlinks_work()


class ParseShadowCountTests(unittest.TestCase):
    """_parse_shadow_count reads vssadmin output."""

    def test_counts_each_shadow_copy_id_line(self) -> None:
        self.assertEqual(system._parse_shadow_count(VSSADMIN_TWO_COPIES), 2)

    def test_no_items_found_means_zero_copies(self) -> None:
        self.assertEqual(system._parse_shadow_count(VSSADMIN_NO_ITEMS), 0)

    def test_garbage_or_empty_output_is_unknown(self) -> None:
        self.assertIsNone(system._parse_shadow_count("this is not vssadmin output"))
        self.assertIsNone(system._parse_shadow_count(""))


class IsWithinTests(unittest.TestCase):
    """is_within is inclusive and compares whole path parts."""

    def setUp(self) -> None:
        self.base = make_base()
        self.addCleanup(remove_base, self.base)
        self.users = os.path.join(self.base, "Users")

    def test_equal_path_is_within_itself(self) -> None:
        self.assertTrue(system.is_within(self.users, self.users))

    def test_path_inside_root_is_within(self) -> None:
        self.assertTrue(system.is_within(os.path.join(self.users, "a", "file.txt"), self.users))

    def test_sibling_sharing_a_prefix_is_not_within(self) -> None:
        short = os.path.join(self.users, "a")
        longer = os.path.join(self.users, "ab")
        self.assertFalse(system.is_within(short, longer))
        self.assertFalse(system.is_within(longer, short))

    def test_root_is_not_within_its_child(self) -> None:
        self.assertFalse(system.is_within(self.users, os.path.join(self.users, "a")))

    def test_empty_arguments_give_false(self) -> None:
        self.assertFalse(system.is_within("", self.users))
        self.assertFalse(system.is_within(self.users, ""))
        self.assertFalse(system.is_within("", ""))

    @unittest.skipUnless(system.IS_WINDOWS, "Windows path rules")
    def test_windows_sibling_prefix_with_literal_paths(self) -> None:
        # Windows-only: not runnable in this sandbox
        self.assertFalse(system.is_within(r"C:\Users\ab", r"C:\Users\a"))
        self.assertTrue(system.is_within(r"C:\Users\a\x", r"C:\Users\a"))


class IsProtectedPathTests(unittest.TestCase):
    """is_protected_path with explicit roots and allowlist, plus the real default allowlist."""

    def setUp(self) -> None:
        self.base = make_base()
        self.addCleanup(remove_base, self.base)
        self.windows = os.path.join(self.base, "Windows")
        self.temp = os.path.join(self.windows, "Temp")
        self.roots = [self.windows]
        self.allowlist = [self.temp]

    def test_path_inside_a_protected_root_is_protected(self) -> None:
        self.assertTrue(system.is_protected_path(os.path.join(self.windows, "System32"), self.roots, self.allowlist))

    def test_protected_root_itself_is_protected(self) -> None:
        self.assertTrue(system.is_protected_path(self.windows, self.roots, []))

    def test_allowlist_wins_inside_a_protected_root(self) -> None:
        self.assertFalse(system.is_protected_path(os.path.join(self.temp, "setup.log"), self.roots, self.allowlist))
        self.assertFalse(system.is_protected_path(self.temp, self.roots, self.allowlist))

    def test_path_outside_every_root_is_not_protected(self) -> None:
        self.assertFalse(system.is_protected_path(os.path.join(self.base, "Users"), self.roots, self.allowlist))

    def test_sibling_sharing_a_prefix_is_not_protected(self) -> None:
        self.assertFalse(system.is_protected_path(os.path.join(self.base, "WindowsOld"), self.roots, self.allowlist))

    def test_empty_path_counts_as_protected(self) -> None:
        self.assertTrue(system.is_protected_path("", self.roots, self.allowlist))
        self.assertTrue(system.is_protected_path("", [], []))

    def test_empty_list_entries_are_ignored(self) -> None:
        self.assertFalse(system.is_protected_path(os.path.join(self.windows, "x"), [""], []))

    def test_no_roots_protects_nothing(self) -> None:
        self.assertFalse(system.is_protected_path(self.windows, [], []))

    def test_default_allowlist_entries_are_never_protected(self) -> None:
        for entry in system.system_allowlist():
            with self.subTest(entry=entry):
                self.assertFalse(system.is_protected_path(entry))


class RunCommandTests(unittest.TestCase):
    """run_command never raises. Only the Python interpreter and a missing name are run."""

    def test_missing_executable_returns_minus_one(self) -> None:
        code, stdout, stderr = system.run_command(["wsa-no-such-program-12345"], timeout=10)
        self.assertEqual(code, -1)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr)

    def test_timeout_returns_minus_one(self) -> None:
        code, _stdout, stderr = system.run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5
        )
        self.assertEqual(code, -1)
        self.assertIn("Timed out", stderr)

    def test_successful_command_returns_zero_and_output(self) -> None:
        code, stdout, _stderr = system.run_command([sys.executable, "-c", "print('hi')"], timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("hi", stdout)

    def test_empty_argument_list_returns_minus_one(self) -> None:
        self.assertEqual(system.run_command([])[0], -1)


class DriveInfoTests(unittest.TestCase):
    """drive_info reads sizes and gives zeros when they cannot be read."""

    def test_existing_folder_reports_sizes(self) -> None:
        base = make_base()
        self.addCleanup(remove_base, base)
        info = system.drive_info(base)
        self.assertEqual(info.root, base)
        self.assertGreater(info.total, 0)

    def test_missing_path_gives_zeros(self) -> None:
        base = make_base()
        self.addCleanup(remove_base, base)
        info = system.drive_info(os.path.join(base, "missing"))
        self.assertEqual((info.total, info.used, info.free), (0, 0, 0))


class IsLinkTests(unittest.TestCase):
    """is_link recognises symbolic links and leaves regular files alone."""

    @unittest.skipUnless(CAN_SYMLINK, "symbolic links cannot be created on this system")
    def test_symlink_is_a_link_and_regular_file_is_not(self) -> None:
        base = make_base()
        self.addCleanup(remove_base, base)
        target = os.path.join(base, "target.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        link = os.path.join(base, "link.txt")
        os.symlink(target, link)
        self.assertTrue(system.is_link(link))
        self.assertFalse(system.is_link(target))
        self.assertFalse(system.is_link(os.path.join(base, "missing")))


@unittest.skipIf(system.IS_WINDOWS, "non-Windows fallbacks")
class NonWindowsFallbackTests(unittest.TestCase):
    """Behaviour off Windows: no admin, only the root drive, and no-op Windows helpers."""

    def test_is_admin_is_false(self) -> None:
        self.assertFalse(system.is_admin())

    def test_fixed_drives_is_the_root_only(self) -> None:
        self.assertEqual(system.fixed_drives(), ["/"])

    def test_open_in_explorer_does_nothing_and_does_not_raise(self) -> None:
        base = make_base()
        self.addCleanup(remove_base, base)
        with mock.patch.object(subprocess, "Popen") as popen, \
                mock.patch.object(os, "startfile", create=True) as startfile:
            system.open_in_explorer(base)
            system.open_in_explorer(os.path.join(base, "missing.txt"))
        popen.assert_not_called()
        startfile.assert_not_called()

    def test_relaunch_and_restore_point_count_are_inert(self) -> None:
        self.assertFalse(system.relaunch_as_admin())
        self.assertIsNone(system.count_restore_points("C:\\"))

    def test_system_drive_and_windows_folder(self) -> None:
        self.assertEqual(system.system_drive(), "/")
        self.assertEqual(system.windows_dir(), "/Windows")


if __name__ == "__main__":
    unittest.main()
