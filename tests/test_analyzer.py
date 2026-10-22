"""Tests for space_analyzer.analyzer. Every test builds a fake disk inside a temp folder."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from collections.abc import Callable
from unittest import mock

from space_analyzer import analyzer, recycle, rules, system
from space_analyzer.models import CATEGORY_ORDER, ActionKind, Category, CleanupItem, Risk

TEMP_PREFIX = "wsa-test-"
ADMIN_NOTE = "Requires administrator"
RESTORE_NOTE = "Requires administrator to check restore points"
DEFAULT_SELECTED = {"temp.user", "cache.chrome", "cache.edge", "cache.firefox", "cache.thumbnails"}
ADMIN_ITEMS = {
    "temp.system", "logs.wer", "logs.windows", "logs.memdump", "updates.download",
    "oldwin.windows_old", "hibernate.file", "restore.points",
}
# Order and sizes for the fake disk from FakeDisk.add_everything(), with admin=True.
EXPECTED_ORDER = [
    "temp.user", "temp.system",
    "cache.thumbnails", "cache.chrome", "cache.firefox", "cache.edge",
    "logs.memdump", "logs.wer", "logs.crashdumps", "logs.windows",
    "updates.download", "oldwin.windows_old",
    "hibernate.file", "restore.points",
    "system.pagefile",
]
EXPECTED_SIZES = {
    "temp.user": 3000,
    "temp.system": 100,
    "cache.chrome": 1000,
    "cache.edge": 500,
    "cache.firefox": 600,
    "cache.thumbnails": 4800,
    "logs.crashdumps": 1050,
    "logs.wer": 1200,
    "logs.windows": 50,
    "logs.memdump": 5096,
    "updates.download": 2000,
    "oldwin.windows_old": 300,
    "hibernate.file": 8192,
    "system.pagefile": 4096,
}


def write_file(path: str, size: int) -> str:
    """Create a file of the given size, with any missing parent folders. Returns the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\0" * size)
    return path


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


def snapshot(root: str) -> dict[str, int]:
    """Map every file under root to its size."""
    files: dict[str, int] = {}
    for folder, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(folder, name)
            files[full] = os.path.getsize(full)
    return files


class FakeDisk:
    """A fake system drive, Windows folder and user profile inside one temp folder."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.drive = os.path.join(root, "C")
        self.windows = os.path.join(self.drive, "Windows")
        self.program_data = os.path.join(self.drive, "ProgramData")
        self.profile = os.path.join(root, "Users", "tester")
        self.local = os.path.join(self.profile, "AppData", "Local")
        self.user_temp = os.path.join(self.local, "Temp")
        self.system_temp = os.path.join(self.windows, "Temp")
        self.chrome = os.path.join(self.local, "Google", "Chrome", "User Data")
        self.edge = os.path.join(self.local, "Microsoft", "Edge", "User Data")
        self.firefox = os.path.join(self.local, "Mozilla", "Firefox", "Profiles")
        self.explorer = os.path.join(self.local, "Microsoft", "Windows", "Explorer")
        self.crash_dumps = os.path.join(self.local, "CrashDumps")
        self.local_wer = os.path.join(self.local, "Microsoft", "Windows", "WER")
        self.program_wer = os.path.join(self.program_data, "Microsoft", "Windows", "WER")
        self.logs = os.path.join(self.windows, "Logs")
        self.memory_dump = os.path.join(self.windows, "MEMORY.DMP")
        self.minidump = os.path.join(self.windows, "Minidump")
        self.update_download = os.path.join(self.windows, "SoftwareDistribution", "Download")
        self.windows_old = os.path.join(self.drive, "Windows.old")
        self.hiberfil = os.path.join(self.drive, "hiberfil.sys")
        self.pagefile = os.path.join(self.drive, "pagefile.sys")

    @property
    def system_drive(self) -> str:
        """Drive root in the same form system.system_drive() returns it."""
        return self.drive + os.sep

    def context(self, admin: bool) -> rules.RuleContext:
        """A RuleContext that points at this fake disk."""
        return rules.RuleContext(
            admin=admin,
            system_drive=self.system_drive,
            windows_dir=self.windows,
            local_app_data=self.local,
            program_data=self.program_data,
            user_temp=self.user_temp,
            system_temp=self.system_temp,
        )

    def add_everything(self) -> None:
        """Create every folder and file the rules look for. Expected sizes are in EXPECTED_SIZES."""
        write_file(os.path.join(self.user_temp, "a.tmp"), 1000)
        write_file(os.path.join(self.user_temp, "sub", "b.tmp"), 2000)
        write_file(os.path.join(self.system_temp, "setup.log"), 100)
        write_file(os.path.join(self.chrome, "Default", "Cache", "data_0"), 400)
        write_file(os.path.join(self.chrome, "Default", "Code Cache", "js", "index"), 100)
        write_file(os.path.join(self.chrome, "Default", "GPUCache", "data_0"), 200)
        write_file(os.path.join(self.chrome, "Profile 1", "Cache", "data_0"), 300)
        write_file(os.path.join(self.chrome, "Crashpad", "Cache", "ignored"), 999)  # not a profile
        write_file(os.path.join(self.chrome, "Local State"), 50)  # file, not a profile
        write_file(os.path.join(self.edge, "Default", "Cache", "data_0"), 500)
        write_file(os.path.join(self.firefox, "abc.default-release", "cache2", "entries", "e1"), 600)
        write_file(os.path.join(self.firefox, "abc.default-release", "prefs.js"), 10)
        write_file(os.path.join(self.firefox, "other.default", "prefs.js"), 10)  # no cache2
        write_file(os.path.join(self.explorer, "thumbcache_256.db"), 4000)
        write_file(os.path.join(self.explorer, "iconcache_32.db"), 800)
        write_file(os.path.join(self.explorer, "other.db"), 9999)  # not a cache file
        write_file(os.path.join(self.crash_dumps, "app.dmp"), 900)
        write_file(os.path.join(self.local_wer, "ReportQueue", "rep1", "report.wer"), 150)
        write_file(os.path.join(self.program_wer, "ReportArchive", "ra1", "report.wer"), 500)
        write_file(os.path.join(self.program_wer, "ReportQueue", "rq1", "report.wer"), 700)
        write_file(os.path.join(self.logs, "CBS", "cbs.log"), 50)
        write_file(self.memory_dump, 4096)
        write_file(os.path.join(self.minidump, "mini1.dmp"), 1000)
        write_file(os.path.join(self.update_download, "KB123", "file.cab"), 2000)
        write_file(os.path.join(self.windows_old, "file.bin"), 300)
        write_file(self.hiberfil, 8192)
        write_file(self.pagefile, 4096)


class AnalyzerTestCase(unittest.TestCase):
    """Runs build_suggestions on a fake disk. The Windows helpers are patched to the fake drive."""

    def setUp(self) -> None:
        self.root = make_root()
        self.addCleanup(remove_root, self.root)
        self.disk = FakeDisk(self.root)
        sd = self.disk.system_drive
        self.patch_attr(system, "system_drive", return_value=sd)
        self.patch_attr(system, "fixed_drives", return_value=[sd])
        # Built from the fake drive, so the protected lists point inside the temp folder.
        protected = system.protected_roots()
        allowlist = system.system_allowlist()
        self.patch_attr(system, "user_profile", return_value=self.disk.profile)
        self.patch_attr(system, "protected_roots", return_value=protected)
        self.patch_attr(system, "system_allowlist", return_value=allowlist)
        self.is_admin = self.patch_attr(system, "is_admin", return_value=False)
        self.count_restore_points = self.patch_attr(system, "count_restore_points", return_value=3)
        self.patch_attr(recycle, "recycle_bin_info", return_value=(0, 0))  # never read the real bin
        self.patch_attr(rules, "make_context", side_effect=self.disk.context)

    def patch_attr(self, target: object, name: str, **kwargs: object) -> mock.MagicMock:
        """Patch one attribute until the test ends and return the mock."""
        patcher = mock.patch.object(target, name, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def build(
        self,
        admin: bool,
        progress: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[CleanupItem]:
        """Run build_suggestions with an explicit admin flag."""
        return analyzer.build_suggestions(admin=admin, progress=progress, cancel=cancel)

    @staticmethod
    def by_id(items: list[CleanupItem]) -> dict[str, CleanupItem]:
        """Index items by item_id."""
        return {item.item_id: item for item in items}


class FullDiskTests(AnalyzerTestCase):
    """Every safe folder exists, so every rule produces an item."""

    def setUp(self) -> None:
        super().setUp()
        self.disk.add_everything()

    def test_every_safe_folder_preselects_exactly_the_five_default_items(self) -> None:
        for admin in (True, False):
            with self.subTest(admin=admin):
                selected = {item.item_id for item in self.build(admin) if item.default_selected}
                self.assertEqual(selected, DEFAULT_SELECTED)

    def test_admin_only_items_are_never_preselected(self) -> None:
        for item in self.build(admin=True):
            if item.requires_admin:
                with self.subTest(item_id=item.item_id):
                    self.assertFalse(item.default_selected)

    def test_admin_only_items_are_the_contract_rows(self) -> None:
        items = self.build(admin=True)
        self.assertEqual({item.item_id for item in items if item.requires_admin}, ADMIN_ITEMS)

    def test_items_are_ordered_by_category_then_largest_first(self) -> None:
        items = self.build(admin=True)
        self.assertEqual([item.item_id for item in items], EXPECTED_ORDER)
        order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
        keys = [(order[item.category], -item.size_bytes) for item in items]
        self.assertEqual(keys, sorted(keys))

    def test_recycle_bin_with_content_sits_between_old_windows_and_hibernation(self) -> None:
        with mock.patch.object(recycle, "recycle_bin_info", return_value=(4096, 2)):
            items = self.build(admin=True)
        expected = list(EXPECTED_ORDER)
        expected.insert(expected.index("hibernate.file"), "recycle.bin")
        self.assertEqual([item.item_id for item in items], expected)
        bin_item = self.by_id(items)["recycle.bin"]
        self.assertEqual((bin_item.size_bytes, bin_item.file_count), (4096, 2))
        self.assertTrue(bin_item.size_known)
        self.assertFalse(bin_item.default_selected)

    def test_sizes_are_summed_from_the_fake_files(self) -> None:
        items = self.by_id(self.build(admin=True))
        for item_id, size in EXPECTED_SIZES.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(items[item_id].size_bytes, size)

    def test_size_only_items_keep_their_paths(self) -> None:
        items = self.by_id(self.build(admin=True))
        self.assertEqual(items["hibernate.file"].paths, [self.disk.hiberfil])
        self.assertEqual(items["system.pagefile"].paths, [self.disk.pagefile])
        self.assertEqual(items["system.pagefile"].action, ActionKind.NONE)

    def test_restore_points_are_kept_with_unknown_size(self) -> None:
        item = self.by_id(self.build(admin=True))["restore.points"]
        self.assertFalse(item.size_known)
        self.assertEqual(item.action_params, {"keep_newest": 1})

    def test_admin_only_items_are_shown_with_a_note_when_not_admin(self) -> None:
        items = self.build(admin=False)
        self.assertEqual({item.item_id for item in items if item.requires_admin}, ADMIN_ITEMS)
        for item in items:
            if item.requires_admin:
                with self.subTest(item_id=item.item_id):
                    expected = RESTORE_NOTE if item.item_id == "restore.points" else ADMIN_NOTE
                    self.assertEqual(item.note, expected)
                    self.assertFalse(item.default_selected)
        self.count_restore_points.assert_not_called()  # without admin the count is not attempted

    def test_admin_only_items_have_no_note_when_admin(self) -> None:
        for item in self.build(admin=True):
            if item.requires_admin:
                with self.subTest(item_id=item.item_id):
                    self.assertEqual(item.note, "")

    def test_admin_flag_defaults_to_the_running_process(self) -> None:
        self.is_admin.return_value = True
        elevated = {item.item_id: item.note for item in analyzer.build_suggestions() if item.requires_admin}
        self.assertEqual(set(elevated.values()), {""})
        self.is_admin.return_value = False
        plain = {item.item_id: item.note for item in analyzer.build_suggestions() if item.requires_admin}
        self.assertEqual(plain["temp.system"], ADMIN_NOTE)
        self.assertEqual(plain["restore.points"], RESTORE_NOTE)

    def test_cancel_event_that_is_already_set_returns_an_empty_list(self) -> None:
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.build(admin=True, cancel=cancel), [])

    def test_progress_receives_text(self) -> None:
        messages: list[str] = []
        self.build(admin=True, progress=messages.append)
        self.assertTrue(messages)
        self.assertTrue(all(isinstance(text, str) for text in messages))

    def test_building_suggestions_changes_no_files(self) -> None:
        before = snapshot(self.root)
        self.build(admin=True)
        self.build(admin=False)
        self.assertEqual(snapshot(self.root), before)


class ZeroSizeTests(AnalyzerTestCase):
    """Items that measure 0 bytes are dropped. Empty files inside a larger item are kept."""

    def test_item_whose_files_are_all_empty_is_dropped(self) -> None:
        write_file(os.path.join(self.disk.user_temp, "empty.tmp"), 0)
        items = self.by_id(self.build(admin=True))
        self.assertNotIn("temp.user", items)
        self.assertNotIn("recycle.bin", items)  # the Recycle Bin reads as 0 bytes here

    def test_empty_files_do_not_hide_their_siblings(self) -> None:
        write_file(os.path.join(self.disk.user_temp, "empty.tmp"), 0)
        write_file(os.path.join(self.disk.user_temp, "real.tmp"), 500)
        items = self.by_id(self.build(admin=True))
        self.assertEqual(items["temp.user"].size_bytes, 500)
        self.assertEqual(len(items["temp.user"].paths), 2)

    def test_empty_thumbnail_cache_is_dropped(self) -> None:
        write_file(os.path.join(self.disk.explorer, "thumbcache_256.db"), 0)
        self.assertNotIn("cache.thumbnails", self.by_id(self.build(admin=True)))


class RestorePointTests(AnalyzerTestCase):
    """restore.points is shown only when the count rules say there is something to remove."""

    def test_shown_with_unknown_size_when_more_than_one_copy_exists(self) -> None:
        self.count_restore_points.return_value = 2
        items = self.by_id(self.build(admin=True))
        self.assertIn("restore.points", items)
        self.assertFalse(items["restore.points"].size_known)
        self.assertFalse(items["restore.points"].default_selected)

    def test_hidden_when_zero_or_one_copy_or_the_count_is_unknown(self) -> None:
        for value in (None, 0, 1):
            with self.subTest(count=value):
                self.count_restore_points.return_value = value
                self.assertNotIn("restore.points", self.by_id(self.build(admin=True)))


class SafetyTests(AnalyzerTestCase):
    """DELETE_PATHS paths are checked with safety.validate_target before they are kept."""

    def test_path_outside_the_allowed_roots_is_removed_with_a_warning(self) -> None:
        keep = write_file(os.path.join(self.disk.user_temp, "keep.tmp"), 500)
        outside = write_file(os.path.join(self.disk.profile, "Documents", "notes.txt"), 300)
        candidate = CleanupItem(
            item_id="test.outside",
            title="Test items",
            category=Category.TEMP,
            risk=Risk.SAFE,
            description="Test items.",
            impact="Test impact.",
            regenerates="No",
            action=ActionKind.DELETE_PATHS,
            paths=[keep, outside],
            allowed_roots=[self.disk.user_temp],
        )
        with mock.patch.object(rules, "collect_items", return_value=[candidate]):
            items = self.build(admin=False)
        self.assertEqual([item.item_id for item in items], ["test.outside"])
        item = items[0]
        self.assertEqual(item.paths, [keep])
        self.assertEqual(item.size_bytes, 500)
        self.assertTrue(any(text.startswith("Left out 1 path") for text in item.warnings))
        self.assertTrue(os.path.exists(outside))  # the analyzer never deletes anything

    def test_drive_root_and_protected_folder_are_never_kept(self) -> None:
        protected_folder = os.path.join(self.disk.windows, "System32")
        write_file(os.path.join(protected_folder, "x.dll"), 100)
        candidate = CleanupItem(
            item_id="test.protected",
            title="Test items",
            category=Category.TEMP,
            risk=Risk.SAFE,
            description="Test items.",
            impact="Test impact.",
            regenerates="No",
            action=ActionKind.DELETE_PATHS,
            paths=[self.disk.system_drive, protected_folder],
            allowed_roots=[self.disk.system_drive],
        )
        with mock.patch.object(rules, "collect_items", return_value=[candidate]):
            self.assertEqual(self.build(admin=True), [])  # no valid path is left, so no item


if __name__ == "__main__":
    unittest.main()
