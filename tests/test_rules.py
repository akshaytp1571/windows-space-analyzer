"""Tests for space_analyzer.rules. Every test builds a fake disk inside a temp folder."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import tempfile
import unittest
from unittest import mock

from space_analyzer import rules, system
from space_analyzer.models import ActionKind, Category, CleanupItem, Risk

TEMP_PREFIX = "wsa-test-"
ADMIN_NOTE = "Requires administrator"
RESTORE_NOTE = "Requires administrator to check restore points"
BROWSER_WARNING = "Close the browser first. If it is still open, Windows may remove only part of its cache."
LOG_IMPACT = "Only old log and report files are removed. Windows and apps create new ones when needed."
HIBERNATE_IMPACT = (
    "Hibernate and Fast Startup stop working. Normal sleep still works, and you can turn "
    "hibernation back on with powercfg /hibernate on."
)
WINDOWS_OLD_IMPACT = (
    "You can no longer roll back to the previous Windows version. Windows also deletes "
    "this folder by itself after about 10 days."
)

# item_id: (title, category, risk, requires_admin, action), from the contract table.
CONTRACT_TABLE = {
    "temp.user": ("Your temporary files", Category.TEMP, Risk.SAFE, False, ActionKind.DELETE_PATHS),
    "temp.system": ("Windows temporary files", Category.TEMP, Risk.SAFE, True, ActionKind.DELETE_PATHS),
    "cache.chrome": ("Chrome cache", Category.CACHE, Risk.SAFE, False, ActionKind.DELETE_PATHS),
    "cache.edge": ("Edge cache", Category.CACHE, Risk.SAFE, False, ActionKind.DELETE_PATHS),
    "cache.firefox": ("Firefox cache", Category.CACHE, Risk.SAFE, False, ActionKind.DELETE_PATHS),
    "cache.thumbnails": ("Thumbnail and icon cache", Category.CACHE, Risk.SAFE, False, ActionKind.DELETE_PATHS),
    "logs.crashdumps": ("Your crash dumps and error reports", Category.LOGS, Risk.LOW, False, ActionKind.DELETE_PATHS),
    "logs.wer": ("System error reports", Category.LOGS, Risk.LOW, True, ActionKind.DELETE_PATHS),
    "logs.windows": ("Windows log files", Category.LOGS, Risk.LOW, True, ActionKind.DELETE_PATHS),
    "logs.memdump": ("Memory dump and minidumps", Category.LOGS, Risk.LOW, True, ActionKind.DELETE_PATHS),
    "updates.download": (
        "Windows Update download cache", Category.OLD_WINDOWS, Risk.SAFE, True, ActionKind.CLEAN_UPDATE_CACHE,
    ),
    "oldwin.windows_old": (
        "Previous Windows installation", Category.OLD_WINDOWS, Risk.ADVANCED, True, ActionKind.CLEAN_WINDOWS_OLD,
    ),
    "recycle.bin": ("Recycle Bin", Category.RECYCLE, Risk.LOW, False, ActionKind.EMPTY_RECYCLE_BIN),
    "hibernate.file": (
        "Hibernation file (hiberfil.sys)", Category.HIBERNATION_RESTORE, Risk.ADVANCED, True,
        ActionKind.DISABLE_HIBERNATION,
    ),
    "restore.points": (
        "System Restore points", Category.HIBERNATION_RESTORE, Risk.ADVANCED, True,
        ActionKind.DELETE_RESTORE_POINTS,
    ),
    "system.pagefile": ("Page file (pagefile.sys)", Category.SYSTEM_FILES, Risk.INFO, False, ActionKind.NONE),
}

EXPECTED_REGENERATES = {
    "temp.user": "Yes, new ones are created as needed",
    "temp.system": "Yes, new ones are created as needed",
    "cache.chrome": "Yes, rebuilt automatically",
    "cache.edge": "Yes, rebuilt automatically",
    "cache.firefox": "Yes, rebuilt automatically",
    "cache.thumbnails": "Yes, rebuilt automatically",
    "logs.crashdumps": "Yes, new ones are created as needed",
    "logs.wer": "Yes, new ones are created as needed",
    "logs.windows": "Yes, new ones are created as needed",
    "logs.memdump": "Yes, new ones are created as needed",
    "updates.download": "Yes, Windows Update downloads them again when needed",
    "oldwin.windows_old": "No",
    "recycle.bin": "No",
    "hibernate.file": "Yes, when hibernation is turned back on",
    "restore.points": "No",
    "system.pagefile": "Recreated automatically",
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
        """Create every folder and file the rules look for. Expected sizes are listed in the tests."""
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


class FakeDiskTestCase(unittest.TestCase):
    """Gives each test its own fake disk and removes it afterwards."""

    def setUp(self) -> None:
        self.root = make_root()
        self.addCleanup(remove_root, self.root)
        self.disk = FakeDisk(self.root)

    def collect(self, admin: bool = False, restore_count: int | None = 3) -> dict[str, CleanupItem]:
        """Run every rule on the fake disk and return the items keyed by item_id."""
        with mock.patch.object(system, "count_restore_points", return_value=restore_count):
            items = rules.collect_items(self.disk.context(admin))
        return {item.item_id: item for item in items}


class ContextTests(unittest.TestCase):
    """make_context reads every value from the system helpers."""

    def test_make_context_reads_the_system_helpers(self) -> None:
        values = {
            "system_drive": "D:\\",
            "windows_dir": "D:\\Windows",
            "local_app_data": "D:\\Users\\a\\AppData\\Local",
            "program_data": "D:\\ProgramData",
            "user_temp_dir": "D:\\Users\\a\\AppData\\Local\\Temp",
            "system_temp_dir": "D:\\Windows\\Temp",
        }
        with contextlib.ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(mock.patch.object(system, name, return_value=value))
            ctx = rules.make_context(admin=True)
        self.assertEqual(
            ctx,
            rules.RuleContext(
                admin=True,
                system_drive="D:\\",
                windows_dir="D:\\Windows",
                local_app_data="D:\\Users\\a\\AppData\\Local",
                program_data="D:\\ProgramData",
                user_temp="D:\\Users\\a\\AppData\\Local\\Temp",
                system_temp="D:\\Windows\\Temp",
            ),
        )


class ContractTableTests(FakeDiskTestCase):
    """Every row of the rules table in docs/ARCHITECTURE.md."""

    def setUp(self) -> None:
        super().setUp()
        self.disk.add_everything()

    def test_table_has_sixteen_rows(self) -> None:
        self.assertEqual(len(CONTRACT_TABLE), 16)

    def test_admin_true_gives_every_row(self) -> None:
        self.assertEqual(set(self.collect(admin=True)), set(CONTRACT_TABLE))

    def test_admin_false_gives_every_row(self) -> None:
        self.assertEqual(set(self.collect(admin=False)), set(CONTRACT_TABLE))

    def test_each_row_matches_title_category_risk_admin_and_action(self) -> None:
        items = self.collect(admin=True)
        for item_id, (title, category, risk, admin, action) in CONTRACT_TABLE.items():
            with self.subTest(item_id=item_id):
                item = items[item_id]
                self.assertEqual(item.title, title)
                self.assertEqual(item.category, category)
                self.assertEqual(item.risk, risk)
                self.assertEqual(item.requires_admin, admin)
                self.assertEqual(item.action, action)

    def test_regenerates_text(self) -> None:
        items = self.collect(admin=True)
        for item_id, text in EXPECTED_REGENERATES.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(items[item_id].regenerates, text)

    def test_descriptions_are_one_plain_sentence(self) -> None:
        for item in self.collect(admin=True).values():
            with self.subTest(item_id=item.item_id):
                self.assertTrue(item.description.endswith("."))
                self.assertNotIn(". ", item.description)

    def test_every_impact_is_set(self) -> None:
        for item in self.collect(admin=True).values():
            with self.subTest(item_id=item.item_id):
                self.assertTrue(item.impact.strip())

    def test_restore_points_action_params_keep_newest_one(self) -> None:
        self.assertEqual(self.collect(admin=True)["restore.points"].action_params, {"keep_newest": 1})

    def test_rules_never_preselect(self) -> None:
        for item in self.collect(admin=True).values():
            with self.subTest(item_id=item.item_id):
                self.assertFalse(item.default_selected)

    def test_every_rule_returns_a_list(self) -> None:
        ctx = self.disk.context(admin=True)
        with mock.patch.object(system, "count_restore_points", return_value=3):
            for rule in rules.RULES:
                with self.subTest(rule=rule.__name__):
                    self.assertIsInstance(rule(ctx), list)


class WordingTests(FakeDiskTestCase):
    """Required wording for impact, warnings and the recycle bin."""

    def setUp(self) -> None:
        super().setUp()
        self.disk.add_everything()
        self.items = self.collect(admin=True)

    def test_hibernate_impact_is_the_required_text(self) -> None:
        self.assertEqual(self.items["hibernate.file"].impact, HIBERNATE_IMPACT)

    def test_every_logs_item_has_the_required_sentence(self) -> None:
        for item_id in ("logs.crashdumps", "logs.wer", "logs.windows", "logs.memdump"):
            with self.subTest(item_id=item_id):
                self.assertIn(LOG_IMPACT, self.items[item_id].impact)

    def test_windows_old_impact_is_the_required_text(self) -> None:
        self.assertEqual(self.items["oldwin.windows_old"].impact, WINDOWS_OLD_IMPACT)

    def test_browser_items_carry_the_close_the_browser_warning(self) -> None:
        for item_id in ("cache.chrome", "cache.edge", "cache.firefox"):
            with self.subTest(item_id=item_id):
                self.assertIn(BROWSER_WARNING, self.items[item_id].warnings)

    def test_update_cache_warnings_cover_recycle_bin_emptying_and_pause(self) -> None:
        text = " ".join(self.items["updates.download"].warnings)
        self.assertIn("Recycle Bin", text)
        self.assertIn("emptied", text)
        self.assertIn("Windows Update is paused briefly", text)

    def test_windows_old_warning_says_permanent_and_not_recycle_bin(self) -> None:
        text = " ".join(self.items["oldwin.windows_old"].warnings)
        self.assertIn("permanently", text)
        self.assertIn("its own tool, not the Recycle Bin", text)

    def test_restore_point_warnings_cover_permanence_rollback_and_backups(self) -> None:
        text = " ".join(self.items["restore.points"].warnings)
        self.assertIn("removed permanently", text)
        self.assertIn("roll back", text)
        self.assertIn("backup software", text)

    def test_recycle_bin_warns_that_everything_is_deleted(self) -> None:
        self.assertIn(
            "This permanently deletes everything in the Recycle Bin, including files you put there yourself.",
            self.items["recycle.bin"].warnings,
        )

    def test_hibernate_warns_that_the_file_is_deleted_permanently(self) -> None:
        text = " ".join(self.items["hibernate.file"].warnings)
        self.assertIn("permanently", text)
        self.assertIn("Recycle Bin", text)


class PathTests(FakeDiskTestCase):
    """Paths and allowed roots match the contract."""

    def setUp(self) -> None:
        super().setUp()
        self.disk.add_everything()
        self.items = self.collect(admin=True)

    def test_paths_match_the_contract(self) -> None:
        d = self.disk
        expected = {
            "temp.user": {os.path.join(d.user_temp, "a.tmp"), os.path.join(d.user_temp, "sub")},
            "temp.system": {os.path.join(d.system_temp, "setup.log")},
            "cache.chrome": {
                os.path.join(d.chrome, "Default", "Cache"),
                os.path.join(d.chrome, "Default", "Code Cache"),
                os.path.join(d.chrome, "Default", "GPUCache"),
                os.path.join(d.chrome, "Profile 1", "Cache"),
            },
            "cache.edge": {os.path.join(d.edge, "Default", "Cache")},
            "cache.firefox": {os.path.join(d.firefox, "abc.default-release", "cache2")},
            "cache.thumbnails": {
                os.path.join(d.explorer, "thumbcache_256.db"),
                os.path.join(d.explorer, "iconcache_32.db"),
            },
            "logs.crashdumps": {os.path.join(d.crash_dumps, "app.dmp"), os.path.join(d.local_wer, "ReportQueue")},
            "logs.wer": {
                os.path.join(d.program_wer, "ReportArchive", "ra1"),
                os.path.join(d.program_wer, "ReportQueue", "rq1"),
            },
            "logs.windows": {os.path.join(d.logs, "CBS")},
            "logs.memdump": {d.memory_dump, os.path.join(d.minidump, "mini1.dmp")},
            "updates.download": {os.path.join(d.update_download, "KB123")},
            "oldwin.windows_old": {d.windows_old},
            "hibernate.file": {d.hiberfil},
            "system.pagefile": {d.pagefile},
            "restore.points": set(),
            "recycle.bin": set(),
        }
        for item_id, paths in expected.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(set(self.items[item_id].paths), paths)

    def test_folder_items_list_children_not_the_folder(self) -> None:
        d = self.disk
        folders = {
            "temp.user": d.user_temp,
            "temp.system": d.system_temp,
            "logs.crashdumps": d.crash_dumps,
            "logs.wer": d.program_wer,
            "logs.windows": d.logs,
            "updates.download": d.update_download,
            "cache.chrome": d.chrome,
        }
        for item_id, folder in folders.items():
            with self.subTest(item_id=item_id):
                self.assertNotIn(folder, self.items[item_id].paths)

    def test_every_path_lies_inside_an_allowed_root(self) -> None:
        for item in self.items.values():
            for path in item.paths:
                with self.subTest(item_id=item.item_id, path=path):
                    self.assertTrue(any(system.is_within(path, root) for root in item.allowed_roots))


class ExistenceTests(FakeDiskTestCase):
    """Items appear only when the folder or file they need exists."""

    def test_empty_disk_gives_only_items_that_need_no_folder(self) -> None:
        self.assertEqual(set(self.collect(admin=True, restore_count=None)), {"recycle.bin"})
        self.assertEqual(set(self.collect(admin=False)), {"recycle.bin", "restore.points"})

    def test_temp_user_needs_children_in_the_temp_folder(self) -> None:
        os.makedirs(self.disk.user_temp)
        self.assertNotIn("temp.user", self.collect())
        write_file(os.path.join(self.disk.user_temp, "a.tmp"), 1)
        self.assertIn("temp.user", self.collect())

    def test_temp_user_skipped_when_temp_is_outside_local_app_data(self) -> None:
        outside = os.path.join(self.root, "Elsewhere")
        write_file(os.path.join(outside, "a.tmp"), 1)
        ctx = dataclasses.replace(self.disk.context(admin=False), user_temp=outside)
        self.assertEqual(rules.rule_temp_user(ctx), [])

    def test_temp_user_skipped_when_temp_equals_local_app_data(self) -> None:
        write_file(os.path.join(self.disk.local, "a.tmp"), 1)
        ctx = dataclasses.replace(self.disk.context(admin=False), user_temp=self.disk.local)
        self.assertEqual(rules.rule_temp_user(ctx), [])

    def test_temp_system_skipped_when_outside_windows(self) -> None:
        outside = os.path.join(self.root, "Other")
        write_file(os.path.join(outside, "x.log"), 1)
        ctx = dataclasses.replace(self.disk.context(admin=True), system_temp=outside)
        self.assertEqual(rules.rule_temp_system(ctx), [])

    def test_temp_system_skipped_when_equal_to_windows(self) -> None:
        write_file(os.path.join(self.disk.windows, "x.log"), 1)
        ctx = dataclasses.replace(self.disk.context(admin=True), system_temp=self.disk.windows)
        self.assertEqual(rules.rule_temp_system(ctx), [])

    def test_chrome_needs_a_cache_folder_inside_a_profile(self) -> None:
        os.makedirs(self.disk.chrome)
        self.assertNotIn("cache.chrome", self.collect())
        write_file(os.path.join(self.disk.chrome, "Default", "Cache", "data_0"), 10)
        self.assertIn("cache.chrome", self.collect())

    def test_edge_needs_a_cache_folder_inside_a_profile(self) -> None:
        os.makedirs(self.disk.edge)
        self.assertNotIn("cache.edge", self.collect())
        write_file(os.path.join(self.disk.edge, "Default", "Cache", "data_0"), 10)
        self.assertIn("cache.edge", self.collect())

    def test_firefox_needs_cache2_inside_a_profile(self) -> None:
        write_file(os.path.join(self.disk.firefox, "abc.default", "prefs.js"), 10)
        self.assertNotIn("cache.firefox", self.collect())
        os.makedirs(os.path.join(self.disk.firefox, "abc.default", "cache2"))
        self.assertIn("cache.firefox", self.collect())

    def test_thumbnails_need_a_matching_database_file(self) -> None:
        write_file(os.path.join(self.disk.explorer, "other.db"), 10)
        self.assertNotIn("cache.thumbnails", self.collect())
        write_file(os.path.join(self.disk.explorer, "iconcache_32.db"), 10)
        self.assertIn("cache.thumbnails", self.collect())

    def test_crash_dumps_need_a_child_in_crashdumps_or_wer(self) -> None:
        os.makedirs(self.disk.crash_dumps)
        self.assertNotIn("logs.crashdumps", self.collect())
        write_file(os.path.join(self.disk.crash_dumps, "app.dmp"), 10)
        self.assertIn("logs.crashdumps", self.collect())

    def test_wer_item_needs_a_report_inside_programdata(self) -> None:
        os.makedirs(os.path.join(self.disk.program_wer, "ReportQueue"))
        self.assertNotIn("logs.wer", self.collect(admin=True))
        write_file(os.path.join(self.disk.program_wer, "ReportQueue", "r.wer"), 10)
        self.assertIn("logs.wer", self.collect(admin=True))

    def test_windows_logs_need_a_child_in_the_logs_folder(self) -> None:
        os.makedirs(self.disk.logs)
        self.assertNotIn("logs.windows", self.collect(admin=True))
        write_file(os.path.join(self.disk.logs, "x.log"), 10)
        self.assertIn("logs.windows", self.collect(admin=True))

    def test_memdump_needs_memory_dmp_or_a_minidump_child(self) -> None:
        self.assertNotIn("logs.memdump", self.collect(admin=True))
        write_file(os.path.join(self.disk.minidump, "mini1.dmp"), 10)
        self.assertIn("logs.memdump", self.collect(admin=True))

    def test_update_download_needs_the_download_folder(self) -> None:
        os.makedirs(os.path.join(self.disk.windows, "SoftwareDistribution"))
        self.assertNotIn("updates.download", self.collect(admin=True))
        write_file(os.path.join(self.disk.update_download, "KB1", "f.cab"), 10)
        self.assertIn("updates.download", self.collect(admin=True))

    def test_windows_old_needs_its_folder(self) -> None:
        self.assertNotIn("oldwin.windows_old", self.collect(admin=True))
        write_file(os.path.join(self.disk.windows_old, "f.bin"), 10)
        self.assertIn("oldwin.windows_old", self.collect(admin=True))

    def test_hibernate_file_needs_hiberfil_sys(self) -> None:
        self.assertNotIn("hibernate.file", self.collect(admin=True))
        write_file(self.disk.hiberfil, 10)
        self.assertIn("hibernate.file", self.collect(admin=True))

    def test_pagefile_needs_pagefile_sys(self) -> None:
        self.assertNotIn("system.pagefile", self.collect())
        write_file(self.disk.pagefile, 10)
        self.assertIn("system.pagefile", self.collect())


class RestorePointTests(FakeDiskTestCase):
    """restore.points: count rules for admin and the note for non-admin."""

    def test_without_admin_the_count_is_not_attempted(self) -> None:
        with mock.patch.object(system, "count_restore_points", return_value=5) as count:
            result = rules.rule_restore_points(self.disk.context(admin=False))
        count.assert_not_called()
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item.note, RESTORE_NOTE)
        self.assertTrue(item.requires_admin)
        self.assertFalse(item.default_selected)

    def test_with_admin_shown_only_when_more_than_one_copy_exists(self) -> None:
        for value, shown in ((None, False), (0, False), (1, False), (2, True), (9, True)):
            with self.subTest(count=value):
                with mock.patch.object(system, "count_restore_points", return_value=value) as count:
                    result = rules.rule_restore_points(self.disk.context(admin=True))
                count.assert_called_once_with(self.disk.system_drive)
                self.assertEqual(bool(result), shown)

    def test_with_admin_the_note_is_empty(self) -> None:
        with mock.patch.object(system, "count_restore_points", return_value=2):
            (item,) = rules.rule_restore_points(self.disk.context(admin=True))
        self.assertEqual(item.note, "")
        self.assertEqual(item.action_params, {"keep_newest": 1})


class AdminNoteTests(FakeDiskTestCase):
    """Admin-only items are shown with a note when not admin, and carry no note when admin."""

    def setUp(self) -> None:
        super().setUp()
        self.disk.add_everything()

    def test_admin_items_are_shown_with_the_note_when_not_admin(self) -> None:
        items = self.collect(admin=False)
        for item_id, item in items.items():
            if not item.requires_admin:
                continue
            with self.subTest(item_id=item_id):
                expected = RESTORE_NOTE if item_id == "restore.points" else ADMIN_NOTE
                self.assertEqual(item.note, expected)
                self.assertFalse(item.default_selected)

    def test_admin_items_have_no_note_when_admin(self) -> None:
        items = self.collect(admin=True)
        for item_id, item in items.items():
            if item.requires_admin:
                with self.subTest(item_id=item_id):
                    self.assertEqual(item.note, "")


class NoChangeTests(FakeDiskTestCase):
    """Rules describe candidates only. They must never change the disk."""

    def test_collecting_items_changes_no_files(self) -> None:
        self.disk.add_everything()
        before = snapshot(self.root)
        self.collect(admin=True)
        self.collect(admin=False)
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
