"""Tests for safety.validate_target, run against a fake disk inside a temporary folder."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from space_analyzer import safety, system


class ValidateTargetTests(unittest.TestCase):
    """Each refusal reason, plus the cases that must stay allowed."""

    def setUp(self) -> None:
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="space_safety_test_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.disk = os.path.join(self.root, "disk")
        self.windows = os.path.join(self.disk, "Windows")
        self.allowlisted = os.path.join(self.windows, "Temp")
        self.profile = os.path.join(self.disk, "Users", "tester")
        self.desktop = os.path.join(self.profile, "Desktop")
        self.user_temp = os.path.join(self.profile, "AppData", "Local", "Temp")
        self.outside = os.path.join(self.root, "outside")
        for folder in (self.allowlisted, self.desktop, self.user_temp, self.outside):
            os.makedirs(folder)
        self._patch(system, "fixed_drives", return_value=[self.disk + os.sep])
        self._patch(system, "system_drive", return_value=self.disk + os.sep)
        self._patch(system, "user_profile", return_value=self.profile)
        self._patch(system, "protected_roots", return_value=[self.windows])
        self._patch(system, "system_allowlist", return_value=[self.allowlisted])

    def _patch(self, target: object, name: str, **kwargs: object) -> mock.MagicMock:
        patcher = mock.patch.object(target, name, **kwargs)
        patched = patcher.start()
        self.addCleanup(patcher.stop)
        return patched

    def _file(self, *parts: str) -> str:
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"data")
        return path

    def test_empty_path_refused(self) -> None:
        self.assertEqual(safety.validate_target("", [self.disk]), "No path given")

    def test_drive_root_refused(self) -> None:
        self.assertEqual(
            safety.validate_target(self.disk + os.sep, [self.disk]),
            "Drive roots cannot be removed",
        )

    def test_top_level_folder_refused(self) -> None:
        stuff = os.path.join(self.disk, "Stuff")
        os.makedirs(stuff)
        self.assertEqual(
            safety.validate_target(stuff, [self.disk]),
            "Top-level folders cannot be removed",
        )

    def test_user_profile_folder_itself_refused(self) -> None:
        self.assertEqual(
            safety.validate_target(self.profile, [self.disk]),
            "Personal folders cannot be removed",
        )

    def test_desktop_folder_refused(self) -> None:
        self.assertEqual(
            safety.validate_target(self.desktop, [self.profile]),
            "Personal folders cannot be removed",
        )

    def test_file_on_desktop_allowed(self) -> None:
        notes = self._file(self.desktop, "notes.txt")
        self.assertIsNone(safety.validate_target(notes, [self.profile]))

    def test_file_directly_under_drive_root_not_refused_by_folder_checks(self) -> None:
        readme = self._file(self.disk, "readme.txt")
        self.assertIsNone(safety.validate_target(readme, [self.disk]))

    def test_link_refused(self) -> None:
        link = self._file(self.user_temp, "link.lnk")
        with mock.patch.object(system, "is_link", side_effect=lambda path: path == link):
            self.assertEqual(
                safety.validate_target(link, [self.user_temp]),
                "Links and junctions are not followed",
            )

    def test_path_outside_allowed_root_refused(self) -> None:
        loose = self._file(self.outside, "loose.txt")
        self.assertEqual(
            safety.validate_target(loose, [self.user_temp]),
            "Outside the allowed locations",
        )

    def test_empty_allowed_roots_refuse_everything(self) -> None:
        cache = self._file(self.user_temp, "cache.bin")
        self.assertEqual(
            safety.validate_target(cache, []),
            "Outside the allowed locations",
        )

    def test_path_inside_protected_root_refused(self) -> None:
        kernel = self._file(self.windows, "System32", "kernel.dll")
        self.assertEqual(
            safety.validate_target(kernel, [self.disk]),
            "Protected system location",
        )

    def test_allowlisted_path_inside_protected_root_allowed(self) -> None:
        old_log = self._file(self.allowlisted, "old.log")
        self.assertIsNone(safety.validate_target(old_log, [self.disk]))

    def test_file_inside_allowed_root_allowed(self) -> None:
        cache = self._file(self.user_temp, "cache.bin")
        self.assertIsNone(safety.validate_target(cache, [self.user_temp]))

    def test_explicit_lists_replace_the_system_lists(self) -> None:
        kernel = self._file(self.windows, "System32", "kernel.dll")
        self.assertIsNone(
            safety.validate_target(kernel, [self.disk], protected=[], allowlist=[])
        )


if __name__ == "__main__":
    unittest.main()
