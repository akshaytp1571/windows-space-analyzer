"""Tests for apps: registry parsing, size handling, and the uninstall launch rules."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from space_analyzer import apps, system

GUID = "{12345678-1234-1234-1234-123456789012}"

# A stand-in for the real System32 path, so the launch tests run on any platform.
_FAKE_MSIEXEC = r"C:\Windows\System32\msiexec.exe"


class ParseSizeTests(unittest.TestCase):
    """EstimatedSize is recorded in kilobytes; everything is returned as bytes."""

    def test_kilobytes_become_bytes(self) -> None:
        self.assertEqual(apps.parse_size(1234), 1234 * 1024)

    def test_a_numeric_string_is_accepted(self) -> None:
        # A few installers write REG_SZ instead of REG_DWORD.
        self.assertEqual(apps.parse_size("99"), 99 * 1024)

    def test_padding_is_ignored(self) -> None:
        self.assertEqual(apps.parse_size("  42  "), 42 * 1024)

    def test_missing_and_unusable_values_are_zero(self) -> None:
        for value in (None, "", "abc", 0, -5, [], {}):
            self.assertEqual(apps.parse_size(value), 0)


class UninstallProblemTests(unittest.TestCase):
    """Only a publisher's own uninstaller may be launched, and nothing else."""

    def assertAllowed(self, command: str) -> None:
        self.assertIsNone(apps.uninstall_problem(command), command)

    def assertRefused(self, command: str, fragment: str = "") -> None:
        reason = apps.uninstall_problem(command)
        self.assertIsNotNone(reason, command)
        if fragment:
            self.assertIn(fragment, reason)

    def test_every_msi_spelling_is_accepted(self) -> None:
        # Installers register /X, /I and /uninstall; all mean the same product.
        self.assertAllowed(f"MsiExec.exe /X{GUID}")
        self.assertAllowed(f"msiexec /x {GUID}")
        self.assertAllowed(f"MsiExec.exe /I{GUID}")
        self.assertAllowed(f"Msiexec.exe /uninstall {GUID} /quiet")

    def test_an_msi_command_without_a_product_code_is_refused(self) -> None:
        self.assertRefused("MsiExec.exe /X", "product code")
        self.assertRefused("MsiExec.exe /I not-a-guid")

    def test_vendor_uninstaller_paths_are_accepted(self) -> None:
        self.assertAllowed(r'"C:\Program Files\App\uninst.exe" /S')
        self.assertAllowed(r"C:\Program Files\App\uninst.exe /S")
        self.assertAllowed(r'"C:\Program Files\App\uninst.exe"')

    def test_shells_and_deletion_tools_are_refused(self) -> None:
        for command in (
            r"cmd.exe /c del /f /q C:\Users",
            r"powershell -Command Remove-Item C:\ -Recurse",
            r"pwsh -c rm -rf /",
            r"cscript //nologo evil.vbs",
            r"del C:\Windows\System32\x.dll",
            r"format C:",
        ):
            self.assertRefused(command, "shell or a file-deletion tool")

    def test_a_relative_path_is_refused(self) -> None:
        # A bare name would be resolved on PATH, not necessarily to the publisher's file.
        self.assertRefused("uninst.exe /S", "relative path")

    def test_a_network_share_is_refused(self) -> None:
        self.assertRefused(r"\\server\share\x.exe", "network share")

    def test_empty_commands_are_refused(self) -> None:
        self.assertRefused("", "No uninstaller")
        self.assertRefused("   ", "No uninstaller")

    def test_a_package_manager_command_is_refused(self) -> None:
        self.assertRefused(
            "winget uninstall --product-code Foo", "does not start with a program"
        )


class MsiProductCodeTests(unittest.TestCase):
    """Only the product code is taken; extra switches never survive."""

    def test_the_code_is_extracted_from_every_form(self) -> None:
        for command in (
            f"MsiExec.exe /X{GUID}",
            f"msiexec /x {GUID}",
            f"MsiExec.exe /I{GUID}",
            f"Msiexec.exe /uninstall {GUID}",
        ):
            self.assertEqual(apps.msi_product_code(command), GUID)

    def test_extra_switches_are_not_carried_through(self) -> None:
        command = f'Msiexec.exe /x {GUID} /qn /norestart /L*v "C:\\temp\\x.log"'
        with mock.patch.object(apps, "_msiexec_path", return_value=_FAKE_MSIEXEC):
            self.assertEqual(apps._split_command(command), [_FAKE_MSIEXEC, "/x", GUID])

    def test_a_command_without_a_code_gives_none(self) -> None:
        self.assertIsNone(apps.msi_product_code("msiexec /x"))
        self.assertIsNone(apps._split_command("msiexec /x"))

    @unittest.skipUnless(system.IS_WINDOWS, "needs a real System32")
    def test_msiexec_is_resolved_to_an_absolute_path(self) -> None:
        # A bare name would be looked up on PATH. The app starts programs only from
        # a path it can point at, the same rule applied to vendor uninstallers.
        resolved = apps._msiexec_path()
        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isabs(resolved))
        self.assertTrue(resolved.lower().endswith("msiexec.exe"))

    def test_msiexec_path_is_built_from_the_windows_folder(self) -> None:
        # Portable check of the resolution rule, with the folder supplied.
        with mock.patch.object(system, "windows_dir", return_value=r"C:\Windows"):
            with mock.patch.object(apps.os.path, "isfile", return_value=True):
                self.assertEqual(
                    apps._msiexec_path(), os.path.join(r"C:\Windows", "System32", "msiexec.exe")
                )

    def test_no_windows_folder_means_no_msiexec(self) -> None:
        # Off Windows there is no System32, so the launch is refused rather than
        # falling back to a PATH lookup.
        with mock.patch.object(system, "windows_dir", return_value=""):
            self.assertIsNone(apps._msiexec_path())

    def test_a_missing_msiexec_file_means_no_msiexec(self) -> None:
        with mock.patch.object(system, "windows_dir", return_value=r"C:\Windows"):
            with mock.patch.object(apps.os.path, "isfile", return_value=False):
                self.assertIsNone(apps._msiexec_path())

    def test_a_missing_msiexec_refuses_the_launch(self) -> None:
        with mock.patch.object(apps, "_msiexec_path", return_value=None):
            self.assertIsNone(apps._split_command(f"MsiExec.exe /X{GUID}"))

    def test_a_launch_without_msiexec_reports_a_reason(self) -> None:
        app = apps.InstalledApp(
            name="Test", version="1", publisher="P", install_location="",
            uninstall_string=f"MsiExec.exe /X{GUID}", size_bytes=0,
            scope="machine", key="k",
        )
        with mock.patch.object(system, "IS_WINDOWS", True), \
                mock.patch.object(apps, "_msiexec_path", return_value=None), \
                mock.patch.object(system, "spawn") as spawn:
            reason = apps.launch_uninstall(app)
        self.assertIn("could not be read", reason)
        spawn.assert_not_called()


class SplitProgramTests(unittest.TestCase):
    """The program and its arguments are separated without a shell."""

    def test_a_quoted_path_keeps_its_arguments(self) -> None:
        program, args = apps._split_program(r'"C:\App\uninst.exe" /S /quiet')
        self.assertEqual(program, r"C:\App\uninst.exe")
        self.assertEqual(args, ["/S", "/quiet"])

    def test_an_unquoted_path_with_spaces_is_one_program(self) -> None:
        program, args = apps._split_program(r"C:\Program Files\App\uninst.exe /S")
        self.assertEqual(program, r"C:\Program Files\App\uninst.exe")
        self.assertEqual(args, ["/S"])

    def test_no_arguments_gives_an_empty_list(self) -> None:
        program, args = apps._split_program(r"C:\App\uninst.exe")
        self.assertEqual(program, r"C:\App\uninst.exe")
        self.assertEqual(args, [])

    def test_a_quoted_argument_stays_whole(self) -> None:
        program, args = apps._split_program(r'"C:\App\uninst.exe" /D="C:\Some Folder"')
        self.assertEqual(program, r"C:\App\uninst.exe")
        self.assertEqual(args, ['/D="C:\\Some Folder"'])


class LaunchUninstallTests(unittest.TestCase):
    """The app launches the publisher's uninstaller and nothing else."""

    def _app(self, command: str) -> apps.InstalledApp:
        return apps.InstalledApp(
            name="Test", version="1", publisher="P", install_location="",
            uninstall_string=command, size_bytes=0, scope="machine", key="k",
        )

    def test_an_msi_uninstall_runs_msiexec_with_the_product_code(self) -> None:
        with mock.patch.object(system, "IS_WINDOWS", True), \
                mock.patch.object(apps, "_msiexec_path", return_value=_FAKE_MSIEXEC), \
                mock.patch.object(system, "spawn", return_value=None) as spawn:
            result = apps.launch_uninstall(self._app(f"MsiExec.exe /I{GUID}"))
        self.assertIsNone(result)
        spawn.assert_called_once_with([_FAKE_MSIEXEC, "/x", GUID])

    def test_a_vendor_uninstaller_runs_its_own_path(self) -> None:
        command = r'"C:\Program Files\App\uninst.exe" /S'
        with mock.patch.object(system, "IS_WINDOWS", True), \
                mock.patch.object(system, "spawn", return_value=None) as spawn:
            result = apps.launch_uninstall(self._app(command))
        self.assertIsNone(result)
        spawn.assert_called_once_with([r"C:\Program Files\App\uninst.exe", "/S"])

    def test_a_refused_command_never_reaches_spawn(self) -> None:
        with mock.patch.object(system, "IS_WINDOWS", True), \
                mock.patch.object(system, "spawn") as spawn:
            result = apps.launch_uninstall(self._app(r"cmd.exe /c del C:\ /s"))
        self.assertIn("shell or a file-deletion tool", result)
        spawn.assert_not_called()

    def test_a_spawn_failure_is_reported_not_raised(self) -> None:
        with mock.patch.object(system, "IS_WINDOWS", True), \
                mock.patch.object(system, "spawn", return_value="access denied"):
            result = apps.launch_uninstall(self._app(r'"C:\App\uninst.exe"'))
        self.assertEqual(result, "access denied")


class DeduplicateTests(unittest.TestCase):
    """The same app can appear in two registry views; it is listed once."""

    def _app(self, name: str, size: int, version: str = "1", scope: str = "machine"):
        return apps.InstalledApp(
            name=name, version=version, publisher="P", install_location="",
            uninstall_string="", size_bytes=size, scope=scope, key=name,
        )

    def test_duplicates_are_removed(self) -> None:
        rows = apps._deduplicate([self._app("A", 10), self._app("A", 10)])
        self.assertEqual(len(rows), 1)

    def test_two_versions_of_one_product_are_both_kept(self) -> None:
        rows = apps._deduplicate([self._app("A", 10, "1"), self._app("A", 20, "2")])
        self.assertEqual(len(rows), 2)

    def test_sized_apps_come_first_largest_first(self) -> None:
        rows = apps._deduplicate(
            [self._app("small", 10), self._app("none", 0), self._app("big", 100)]
        )
        self.assertEqual([row.name for row in rows], ["big", "small", "none"])

    def test_apps_without_a_size_are_ordered_by_name(self) -> None:
        rows = apps._deduplicate([self._app("zeta", 0), self._app("alpha", 0)])
        self.assertEqual([row.name for row in rows], ["alpha", "zeta"])


class SizeCaveatTests(unittest.TestCase):
    """The total is a lower bound, and the UI says so."""

    def _app(self, size: int) -> apps.InstalledApp:
        return apps.InstalledApp(
            name="A", version="1", publisher="P", install_location="",
            uninstall_string="", size_bytes=size, scope="machine", key="k",
        )

    def test_no_apps_gives_no_note(self) -> None:
        self.assertEqual(apps.size_caveat([]), "")

    def test_all_sized_says_so(self) -> None:
        self.assertEqual(apps.size_caveat([self._app(10)]), "Every app here reports a size.")

    def test_missing_sizes_are_counted(self) -> None:
        note = apps.size_caveat([self._app(10), self._app(0), self._app(0)])
        self.assertIn("2 of 3 apps report no size", note)
        self.assertIn("lower bound", note)


class NonWindowsTests(unittest.TestCase):
    """The page never breaks on a platform without a registry."""

    def test_installed_apps_is_empty_off_windows(self) -> None:
        with mock.patch.object(system, "IS_WINDOWS", False):
            self.assertEqual(apps.installed_apps(), [])

    def test_launch_reports_that_windows_is_required(self) -> None:
        app = apps.InstalledApp(
            name="A", version="1", publisher="P", install_location="",
            uninstall_string=r'"C:\App\uninst.exe"', size_bytes=0,
            scope="machine", key="k",
        )
        with mock.patch.object(system, "IS_WINDOWS", False):
            self.assertIn("only supported on Windows", apps.launch_uninstall(app))


if __name__ == "__main__":
    unittest.main()
