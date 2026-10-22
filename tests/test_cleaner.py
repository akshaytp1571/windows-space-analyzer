"""Tests for cleaner: a fake recycler for deletes, and patched commands for cleanup actions."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from space_analyzer import cleaner, recycle, system
from space_analyzer.models import ActionFailure, ActionKind, Category, CleanupItem, Risk

_REG_HEADER = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
_VOLUME_CACHES = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
_REG_QUERY_ALL = (
    f"\r\n{_REG_HEADER}\r\n"
    "    Active Setup Temp Folders\r\n"
    "    Previous Installations\r\n"
    "    Temporary Files\r\n"
    "    StateFlags0065    REG_DWORD    0x2\r\n"
)
_REG_QUERY_NO_PREVIOUS = f"\r\n{_REG_HEADER}\r\n    Temporary Files\r\n"


class FakeRecycler:
    """Stands in for recycle.send_to_recycle_bin. Records each batch and refuses the paths in `refuse`."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.calls: list[list[str]] = []
        self._refuse = refuse

    def __call__(self, paths: list[str]) -> tuple[list[str], list[ActionFailure]]:
        self.calls.append(list(paths))
        sent = [path for path in paths if path not in self._refuse]
        failed = [ActionFailure(path, "in use") for path in paths if path in self._refuse]
        return sent, failed


def _item(
    action: ActionKind,
    *,
    paths: list[str] | None = None,
    allowed_roots: list[str] | None = None,
    requires_admin: bool = False,
    action_params: dict | None = None,
) -> CleanupItem:
    """Build a CleanupItem with the given action and targets."""
    return CleanupItem(
        item_id="test.item",
        title="Test item",
        category=Category.TEMP,
        risk=Risk.SAFE,
        description="Test files.",
        impact="Test impact.",
        regenerates="Yes",
        action=action,
        paths=list(paths or []),
        allowed_roots=list(allowed_roots or []),
        action_params=dict(action_params or {}),
        requires_admin=requires_admin,
    )


def _flag_command(name: str, value: str) -> list[str]:
    """The reg add command that sets StateFlags0065 on one VolumeCaches subkey."""
    return [
        "reg", "add", _VOLUME_CACHES + "\\" + name,
        "/v", "StateFlags0065", "/t", "REG_DWORD", "/d", value, "/f",
    ]


class CleanerTestCase(unittest.TestCase):
    """Builds a fake disk inside a temp folder and points the system helpers at it."""

    def setUp(self) -> None:
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="space_cleaner_test_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.disk = os.path.join(self.root, "disk")
        self.profile = os.path.join(self.disk, "Users", "tester")
        self.temp = os.path.join(self.profile, "AppData", "Local", "Temp")
        self.windows = os.path.join(self.disk, "Windows")
        self.download = os.path.join(self.windows, "SoftwareDistribution", "Download")
        self.windows_old = os.path.join(self.disk, "Windows.old")
        self.outside = os.path.join(self.root, "outside")
        for folder in (self.temp, self.download, self.outside):
            os.makedirs(folder)
        self._patch(system, "fixed_drives", return_value=[self.disk + os.sep])
        self._patch(system, "system_drive", return_value=self.disk + os.sep)
        self._patch(system, "user_profile", return_value=self.profile)
        self._patch(system, "protected_roots", return_value=[self.windows])
        self._patch(system, "system_allowlist", return_value=[self.download])

    def _patch(self, target: object, name: str, **kwargs: object) -> mock.MagicMock:
        patcher = mock.patch.object(target, name, **kwargs)
        patched = patcher.start()
        self.addCleanup(patcher.stop)
        return patched

    def _file(self, folder: str, name: str, size: int = 10) -> str:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path


class DeleteToRecycleTests(CleanerTestCase):
    """delete_to_recycle with a fake recycler."""

    def test_dry_run_never_calls_the_recycler(self) -> None:
        first = self._file(self.temp, "a.tmp", size=100)
        second = self._file(self.temp, "b.tmp", size=250)
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle(
            [first, second], [self.temp], dry_run=True, recycler=recycler
        )
        self.assertEqual(recycler.calls, [])
        self.assertTrue(report.dry_run)
        self.assertEqual(report.removed_count, 2)
        self.assertEqual(report.bytes_removed, 350)
        self.assertIn("Dry run. Nothing was changed.", report.messages)
        self.assertTrue(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_missing_path_is_skipped_with_a_message(self) -> None:
        missing = os.path.join(self.temp, "gone.tmp")
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle([missing], [self.temp], recycler=recycler)
        self.assertEqual(report.messages, [f"Already gone: {missing}"])
        self.assertEqual(report.failed, [])
        self.assertEqual(recycler.calls, [])

    def test_path_failing_validation_is_reported_and_not_sent(self) -> None:
        outside = self._file(self.outside, "loose.bin")
        good = self._file(self.temp, "ok.tmp")
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle([outside, good], [self.temp], recycler=recycler)
        self.assertEqual(report.failed, [ActionFailure(outside, "Outside the allowed locations")])
        self.assertEqual(recycler.calls, [[good]])
        self.assertTrue(os.path.exists(outside))

    def test_valid_paths_reach_the_recycler_and_bytes_match_what_was_sent(self) -> None:
        first = self._file(self.temp, "a.tmp", size=100)
        second = self._file(self.temp, "b.tmp", size=250)
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle([first, second], [self.temp], recycler=recycler)
        self.assertEqual(recycler.calls, [[first, second]])
        self.assertEqual(report.action, "recycle")
        self.assertEqual(report.removed_count, 2)
        self.assertEqual(report.bytes_removed, 350)
        self.assertEqual(report.failed, [])

    def test_only_sent_paths_count_toward_bytes_removed(self) -> None:
        first = self._file(self.temp, "a.tmp", size=100)
        second = self._file(self.temp, "b.tmp", size=250)
        recycler = FakeRecycler(refuse=frozenset({second}))
        report = cleaner.delete_to_recycle([first, second], [self.temp], recycler=recycler)
        self.assertEqual(report.removed_count, 1)
        self.assertEqual(report.bytes_removed, 100)
        self.assertEqual([failure.path for failure in report.failed], [second])

    def test_cancel_stops_early_and_sends_nothing(self) -> None:
        first = self._file(self.temp, "a.tmp")
        cancel = threading.Event()
        cancel.set()
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle([first], [self.temp], recycler=recycler, cancel=cancel)
        self.assertTrue(report.cancelled)
        self.assertEqual(recycler.calls, [])
        self.assertTrue(os.path.exists(first))

    def test_empty_list_never_calls_the_recycler(self) -> None:
        recycler = FakeRecycler()
        report = cleaner.delete_to_recycle([], [self.temp], recycler=recycler)
        self.assertEqual(recycler.calls, [])
        self.assertEqual(report.removed_count, 0)
        self.assertEqual(report.failed, [])


class PerformTests(CleanerTestCase):
    """perform(): admin refusal, dry runs and each cleanup action."""

    def test_admin_only_item_is_refused_without_admin_and_runs_no_command(self) -> None:
        item = _item(ActionKind.CLEAN_WINDOWS_OLD, requires_admin=True, paths=[self.windows_old])
        with mock.patch.object(system, "run_command") as run:
            report = cleaner.perform(item, admin=False)
        run.assert_not_called()
        self.assertEqual(report.failed, [ActionFailure(item.title, "Requires administrator")])
        self.assertEqual(report.title, item.title)
        self.assertEqual(report.action, "clean_windows_old")

    def test_admin_only_delete_item_is_refused_without_admin_and_nothing_is_sent(self) -> None:
        logs = self._file(self.temp, "old.log")
        item = _item(
            ActionKind.DELETE_PATHS, paths=[logs], allowed_roots=[self.temp], requires_admin=True
        )
        recycler = FakeRecycler()
        with mock.patch.object(recycle, "send_to_recycle_bin", recycler):
            report = cleaner.perform(item, admin=False)
        self.assertEqual(recycler.calls, [])
        self.assertEqual(report.failed, [ActionFailure(item.title, "Requires administrator")])
        self.assertTrue(os.path.exists(logs))

    def test_dry_run_of_empty_recycle_bin_changes_nothing(self) -> None:
        item = _item(ActionKind.EMPTY_RECYCLE_BIN)
        with mock.patch.object(recycle, "empty_recycle_bin") as empty, \
                mock.patch.object(recycle, "recycle_bin_info") as info, \
                mock.patch.object(system, "run_command") as run:
            report = cleaner.perform(item, admin=True, dry_run=True)
        empty.assert_not_called()
        info.assert_not_called()
        run.assert_not_called()
        self.assertTrue(report.dry_run)
        self.assertEqual(len(report.messages), 1)
        self.assertTrue(report.messages[0].startswith("Would run: "))

    def test_empty_recycle_bin_reports_the_bytes_it_removed(self) -> None:
        item = _item(ActionKind.EMPTY_RECYCLE_BIN)
        with mock.patch.object(recycle, "recycle_bin_info", side_effect=[(5000, 3), (0, 0)]), \
                mock.patch.object(recycle, "empty_recycle_bin", return_value=True) as empty:
            report = cleaner.perform(item, admin=True)
        empty.assert_called_once()
        self.assertEqual(report.bytes_removed, 5000)
        self.assertEqual(report.removed_count, 3)
        self.assertEqual(report.failed, [])

    def test_disable_hibernation_runs_the_exact_powercfg_command(self) -> None:
        item = _item(ActionKind.DISABLE_HIBERNATION, requires_admin=True)
        with mock.patch.object(system, "run_command", return_value=(0, "", "")) as run:
            report = cleaner.perform(item, admin=True)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["powercfg", "/hibernate", "off"])
        self.assertEqual(report.failed, [])
        self.assertIn("Hibernation is off. Windows removes hiberfil.sys.", report.messages)

    def test_restore_points_delete_only_the_copies_beyond_the_newest(self) -> None:
        item = _item(
            ActionKind.DELETE_RESTORE_POINTS, requires_admin=True, action_params={"keep_newest": 1}
        )
        with mock.patch.object(system, "system_drive", return_value="C:\\"), \
                mock.patch.object(system, "count_restore_points", side_effect=[3, 2, 1]) as count, \
                mock.patch.object(system, "run_command", return_value=(0, "", "")) as run:
            report = cleaner.perform(item, admin=True)
        self.assertEqual(count.call_count, 3)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(
                call.args[0], ["vssadmin", "delete", "shadows", "/for=C:", "/oldest", "/quiet"]
            )
        self.assertEqual(report.removed_count, 2)
        self.assertEqual(report.failed, [])

    def test_restore_points_never_delete_more_even_if_the_count_does_not_drop(self) -> None:
        item = _item(
            ActionKind.DELETE_RESTORE_POINTS, requires_admin=True, action_params={"keep_newest": 1}
        )
        with mock.patch.object(system, "system_drive", return_value="C:\\"), \
                mock.patch.object(system, "count_restore_points", return_value=3), \
                mock.patch.object(system, "run_command", return_value=(0, "", "")) as run:
            report = cleaner.perform(item, admin=True)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report.removed_count, 2)

    def test_windows_old_stops_when_the_registry_cannot_be_read(self) -> None:
        item = _item(ActionKind.CLEAN_WINDOWS_OLD, requires_admin=True, paths=[self.windows_old])
        with mock.patch.object(
            system, "run_command", return_value=(1, "", "ERROR: Access is denied.")
        ) as run:
            report = cleaner.perform(item, admin=True)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["reg"])
        self.assertEqual(len(report.failed), 1)
        self.assertIn("Access is denied", report.failed[0].reason)

    def test_windows_old_sets_flags_then_runs_cleanmgr_once(self) -> None:
        item = _item(ActionKind.CLEAN_WINDOWS_OLD, requires_admin=True, paths=[self.windows_old])

        def fake_run(args: list[str], timeout: float = 120) -> tuple[int, str, str]:
            if args[:2] == ["reg", "query"]:
                return 0, _REG_QUERY_ALL, ""
            return 0, "", ""

        with mock.patch.object(system, "run_command", side_effect=fake_run) as run:
            report = cleaner.perform(item, admin=True)
        expected = [
            ["reg", "query", _VOLUME_CACHES],
            _flag_command("Active Setup Temp Folders", "0"),
            _flag_command("Previous Installations", "2"),
            _flag_command("Temporary Files", "0"),
            ["cleanmgr", "/sagerun:65"],
        ]
        self.assertEqual([call.args[0] for call in run.call_args_list], expected)
        self.assertEqual(run.call_args_list[-1].kwargs.get("timeout"), 3600)
        self.assertEqual(report.failed, [])

    def test_windows_old_is_refused_when_previous_installations_is_missing(self) -> None:
        item = _item(ActionKind.CLEAN_WINDOWS_OLD, requires_admin=True, paths=[self.windows_old])

        def fake_run(args: list[str], timeout: float = 120) -> tuple[int, str, str]:
            if args[:2] == ["reg", "query"]:
                return 0, _REG_QUERY_NO_PREVIOUS, ""
            return 0, "", ""

        with mock.patch.object(system, "run_command", side_effect=fake_run) as run:
            report = cleaner.perform(item, admin=True)
        self.assertEqual(
            report.failed,
            [ActionFailure(item.title, "Windows.old cleanup is not available on this PC")],
        )
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["reg"])


class UpdateCacheTests(CleanerTestCase):
    """CLEAN_UPDATE_CACHE: services are stopped, the cache is recycled, services restart."""

    def setUp(self) -> None:
        super().setUp()
        self.package = self._file(self.download, "pkg.cab")
        self.item = _item(
            ActionKind.CLEAN_UPDATE_CACHE,
            requires_admin=True,
            paths=[self.package],
            allowed_roots=[self.download],
        )

    def test_services_restart_and_the_cache_is_recycled(self) -> None:
        recycler = FakeRecycler()
        with mock.patch.object(recycle, "send_to_recycle_bin", recycler), \
                mock.patch.object(system, "run_command", return_value=(0, "", "")) as run:
            report = cleaner.perform(self.item, admin=True)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["net", "stop", "wuauserv"],
                ["net", "stop", "bits"],
                ["net", "start", "bits"],
                ["net", "start", "wuauserv"],
            ],
        )
        self.assertEqual(recycler.calls, [[self.package]])
        self.assertEqual(report.removed_count, 1)
        self.assertEqual(report.failed, [])

    def test_failed_stop_aborts_before_the_cache_is_touched(self) -> None:
        recycler = FakeRecycler()

        def fake_run(args: list[str], timeout: float = 120) -> tuple[int, str, str]:
            if args == ["net", "stop", "bits"]:
                return 5, "", "Access is denied."
            return 0, "", ""

        with mock.patch.object(recycle, "send_to_recycle_bin", recycler), \
                mock.patch.object(system, "run_command", side_effect=fake_run) as run:
            report = cleaner.perform(self.item, admin=True)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["net", "stop", "wuauserv"], ["net", "stop", "bits"], ["net", "start", "wuauserv"]],
        )
        self.assertEqual(recycler.calls, [])
        self.assertTrue(os.path.exists(self.package))
        self.assertEqual(len(report.failed), 1)
        self.assertIn("Could not stop bits", report.failed[0].reason)


if __name__ == "__main__":
    unittest.main()
