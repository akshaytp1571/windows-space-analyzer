"""Installed applications: what they are, where they live, and how much space they take.

Sizes are read from the uninstall keys under HKLM and HKCU. Both the 64-bit and
32-bit views are read, because a 32-bit app registers only in WOW6432Node and would
otherwise be invisible on a 64-bit PC.

Nothing here deletes anything. Uninstalling launches the uninstaller that the
publisher registered, exactly as the Windows "Apps & features" page does, and the
app itself never removes an application's files. That way the publisher's own
uninstaller keeps responsibility for removing its files, registry entries and
services, which is the only supported way to remove software.

Every registered uninstall string is displayed to the user before it runs, and
checked against two rules: it must be a local executable (or MsiExec with a GUID),
and it must not be something that would remove files directly. A string that fails
either check is shown but not runnable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from space_analyzer import system

_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)
_HKCU_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

# A registered command that starts with one of these is never run. They either
# delete files directly or hand the whole string to a shell, which would let a
# malformed registry value run an unrelated command.
_REFUSED_COMMAND_PREFIXES = (
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "wscript", "wscript.exe", "cscript", "cscript.exe", "mshta", "mshta.exe",
    "rundll32", "regsvr32", "del", "erase", "rd", "rmdir", "format", "shutdown",
)

_MSIEXEC = re.compile(r"^msiexec(\.exe)?\s", re.IGNORECASE)
_MSI_GUID = re.compile(r"^\{[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}$")
_QUOTED_EXE = re.compile(r'^"([^"]+\.exe)"', re.IGNORECASE)
_BARE_EXE = re.compile(r'^([^\s"]+\.exe)\b', re.IGNORECASE)
# An unquoted path that contains spaces, e.g. C:\Program Files\App\uninst.exe /S.
# Matches up to the last ".exe" that is followed by whitespace or the end, so the
# space inside "Program Files" does not end the path early.
_UNQUOTED_SPACED_EXE = re.compile(r"^([A-Za-z]:[\\/].*?\.exe)(?=\s|$)", re.IGNORECASE)
_KB = 1024


@dataclass(frozen=True)
class InstalledApp:
    """One entry from the uninstall registry."""

    name: str
    version: str
    publisher: str
    install_location: str     # "" when the publisher did not record one
    uninstall_string: str     # "" when there is no uninstaller
    size_bytes: int           # 0 when unknown; the registry value is often missing
    scope: str                # "machine" or "user"
    key: str                  # full registry key, for display and de-duplication

    @property
    def size_known(self) -> bool:
        """True when the publisher recorded a size."""
        return self.size_bytes > 0

    @property
    def can_uninstall(self) -> bool:
        """True when the registered command is safe for this app to launch."""
        return uninstall_problem(self.uninstall_string) is None


def uninstall_problem(command: str) -> Optional[str]:
    """Return None when `command` is safe to launch, otherwise the reason it is not.

    This is a launch check, not a path check: the app never removes the files
    itself, so the question is only whether running this string is the publisher's
    own uninstaller and nothing more.
    """
    text = (command or "").strip()
    if not text:
        return "No uninstaller was registered"

    first = text.split()[0].strip('"').lower()
    first = os.path.basename(first)
    if first in _REFUSED_COMMAND_PREFIXES:
        return "The registered command is a shell or a file-deletion tool"

    if _MSIEXEC.match(text):
        guid = msi_product_code(text)
        if guid is None:
            return "The registered MSI command names no product code"
        return None

    match = (
        _QUOTED_EXE.match(text)
        or _UNQUOTED_SPACED_EXE.match(text)
        or _BARE_EXE.match(text)
    )
    if not match:
        return "The registered command does not start with a program"
    program = match.group(1)

    # A UNC path points at another machine. The app cannot vouch for a program it
    # would fetch over the network, and an offline share would hang the launch.
    # Checked before the absolute-path test because a UNC path is not "absolute" on
    # every platform, and "on a network share" is the accurate reason to give.
    if program.startswith("\\\\") or program.startswith("//"):
        return "The registered command is on a network share"

    if not _is_absolute(program):
        return "The registered command is a relative path"
    return None


def _is_absolute(program: str) -> bool:
    """True when `program` names a full path, including a bare drive letter."""
    return os.path.isabs(program) or bool(re.match(r"^[A-Za-z]:[\\/]", program))


def msi_product_code(command: str) -> Optional[str]:
    """Return the product code from an MsiExec command, or None.

    Installers register their uninstall entry in several shapes, and Windows' own
    Apps page treats every one of them as "uninstall this product":

        MsiExec.exe /X{GUID}      explicit uninstall
        MsiExec.exe /I{GUID}      repair/install form, which the shell maps to uninstall
        MsiExec.exe /uninstall {GUID}

    Only the product code is ever taken. This function does not run the command, so
    the meaning of the flag is irrelevant: the app always invokes
    `msiexec /x {GUID}`, which is unambiguous whatever the publisher wrote.
    """
    found = re.search(
        r"(?:/x|/i|/uninstall|/package|-x|-i)\s*[\"']?"
        r"(\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\})",
        command,
        re.IGNORECASE,
    )
    if found:
        return found.group(1)
    return None


def parse_size(value: object) -> int:
    """Turn an EstimatedSize registry value into bytes.

    Windows records this as a DWORD in kilobytes. A REG_SZ holding digits is also
    accepted, because a few installers write it that way.
    """
    if value is None:
        return 0
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    return number * _KB


def _read_view(root: str, key_path: str, scope: str) -> list[InstalledApp]:
    """Read one uninstall key and return the apps registered below it.

    Raises OSError when the key cannot be opened, which the caller treats as "none
    found" so a locked or absent key never stops the page from loading.
    """
    try:
        import winreg
    except ImportError:  # not Windows
        return []

    hives = {
        "machine": getattr(winreg, "HKEY_LOCAL_MACHINE", None),
        "user": getattr(winreg, "HKEY_CURRENT_USER", None),
    }
    hive = hives.get(scope)
    if hive is None:
        return []

    access = getattr(winreg, "KEY_READ", 0) | getattr(winreg, "KEY_WOW64_64KEY", 0)
    if root == "wow64":
        access = getattr(winreg, "KEY_READ", 0) | getattr(winreg, "KEY_WOW64_32KEY", 0)

    apps: list[InstalledApp] = []
    try:
        parent = winreg.OpenKey(hive, key_path, 0, access)
    except OSError:
        return []

    with parent:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(parent, index)
            except OSError:
                break  # EnumKey raises when the index runs past the last subkey
            index += 1
            app = _read_one(hive, f"{key_path}\\{subkey_name}", scope, access)
            if app is not None:
                apps.append(app)
    return apps


def _read_one(hive: object, key_path: str, scope: str, access: int) -> Optional[InstalledApp]:
    """Read a single uninstall subkey, or None when it does not describe an app."""
    import winreg

    try:
        key = winreg.OpenKey(hive, key_path, 0, access)
    except OSError:
        return None

    with key:
        values = _values(key)
        name = str(values.get("DisplayName", "")).strip()
        if not name:
            return None
        # Updates, patches and runtimes show a parent key and must not be listed
        # twice; Windows hides them behind SystemComponent as well.
        if _truthy(values.get("SystemComponent")):
            return None
        if values.get("ParentKeyName"):
            return None
        if _truthy(values.get("ReleaseType")) or str(values.get("ReleaseType", "")).strip():
            return None

        return InstalledApp(
            name=name,
            version=str(values.get("DisplayVersion", "")).strip(),
            publisher=str(values.get("Publisher", "")).strip(),
            install_location=str(values.get("InstallLocation", "")).strip(),
            uninstall_string=_uninstall_command(values),
            size_bytes=parse_size(values.get("EstimatedSize")),
            scope=scope,
            key=key_path,
        )


def _values(key: object) -> dict[str, object]:
    """Return every value under one registry key, as a plain dict."""
    import winreg

    result: dict[str, object] = {}
    index = 0
    while True:
        try:
            name, value, _kind = winreg.EnumValue(key, index)
        except OSError:
            break
        index += 1
        if name:
            result[name] = value
    return result


def _truthy(value: object) -> bool:
    """True for the registry's several spellings of "yes"."""
    if value is None:
        return False
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "yes", "true"}


def _uninstall_command(values: dict[str, object]) -> str:
    """Pick the uninstall command, preferring the quiet one when it exists.

    QuietUninstallString is the publisher's own silent switch, which is what the
    Windows settings page uses. When it is absent the normal string is used.
    """
    quiet = str(values.get("QuietUninstallString", "") or "").strip()
    if quiet:
        return quiet
    return str(values.get("UninstallString", "") or "").strip()


def installed_apps(*, progress: Callable[[str], None] | None = None) -> list[InstalledApp]:
    """Return installed applications, largest first.

    Apps with a recorded size come first, ordered by size; those without follow,
    ordered by name, because 0 bytes would otherwise sort them arbitrarily.
    """
    if not system.IS_WINDOWS:
        return []
    if progress is not None:
        progress("Reading installed applications")

    found: list[InstalledApp] = []
    for key_path in _UNINSTALL_KEYS:
        found.extend(_read_view("native", key_path, "machine"))
    found.extend(_read_view("wow64", _UNINSTALL_KEYS[0], "machine"))
    found.extend(_read_view("native", _HKCU_UNINSTALL_KEY, "user"))

    return _deduplicate(found)


def _deduplicate(apps: Iterable[InstalledApp]) -> list[InstalledApp]:
    """Drop repeats (the same app can appear in both registry views) and sort.

    The first entry wins, which puts the 64-bit machine view ahead of the 32-bit
    one, then the per-user view. Entries are keyed on name, version and publisher
    because the same product may legitimately be installed twice at two versions.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[InstalledApp] = []
    for app in apps:
        identity = (app.name.casefold(), app.version.casefold(), app.publisher.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(app)

    with_size = sorted(
        (app for app in unique if app.size_known), key=lambda app: (-app.size_bytes, app.name.casefold())
    )
    without_size = sorted(
        (app for app in unique if not app.size_known), key=lambda app: app.name.casefold()
    )
    return with_size + without_size


def total_size(apps: Iterable[InstalledApp]) -> int:
    """Bytes recorded for the given apps, which is what the registry claims."""
    return sum(app.size_bytes for app in apps)


def size_caveat(apps: Iterable[InstalledApp]) -> str:
    """A one-line note about how complete the recorded sizes are, for the UI."""
    apps = list(apps)
    if not apps:
        return ""
    known = sum(1 for app in apps if app.size_known)
    missing = len(apps) - known
    if missing == 0:
        return "Every app here reports a size."
    return (
        f"{missing} of {len(apps)} apps report no size. Publishers record it themselves, "
        "and many do not, so the total is a lower bound."
    )


def launch_uninstall(app: InstalledApp) -> Optional[str]:
    """Start the publisher's uninstaller. Returns None on success, or a reason.

    The command is passed to Windows as the publisher registered it. Nothing is
    removed by this app: the uninstaller runs, shows its own prompts, and reports
    its own errors.
    """
    problem = uninstall_problem(app.uninstall_string)
    if problem is not None:
        return problem
    if not system.IS_WINDOWS:
        return "Uninstalling is only supported on Windows"

    command = app.uninstall_string.strip()
    try:
        if _MSIEXEC.match(command):
            # Run the MSI service directly, so the GUID is passed as a real
            # argument rather than through a shell.
            args = _split_command(command)
            if args is None:
                return "The registered MSI command could not be read"
            return system.spawn(args)

        program, rest = _split_program(command)
        if program is None:
            return "The registered command could not be read"
        return system.spawn([program, *rest])
    except OSError as exc:
        return f"Could not start the uninstaller: {exc}"


def _split_command(command: str) -> Optional[list[str]]:
    """Split an MsiExec command into `["msiexec", "/x", guid]`.

    The product code is the only argument kept, and `/x` is always the flag used,
    even when the publisher registered `/I`. Anything else they appended is
    dropped, so a registry value cannot smuggle extra switches into the call.
    """
    guid = msi_product_code(command)
    if guid is None:
        return None
    return ["msiexec", "/x", guid]


def _split_program(command: str) -> tuple[Optional[str], list[str]]:
    """Split `"C:\\path\\app.exe" /flag` into the program and its arguments.

    Accepts a quoted path, an unquoted absolute path that contains spaces, and a
    plain absolute path. Whatever follows becomes the argument list.
    """
    text = command.strip()
    for pattern in (_QUOTED_EXE, _UNQUOTED_SPACED_EXE, _BARE_EXE):
        match = pattern.match(text)
        if match:
            return match.group(1), _arguments(text[len(match.group(0)):])
    return None, []


def _arguments(text: str) -> list[str]:
    """Split a trailing argument string on spaces, keeping quoted groups whole.

    A quote may start partway through an argument, as in `/D="C:\\Some Folder"`,
    so each match runs from an unquoted run through any quoted section after it.
    Splitting on bare whitespace would break that path in two.
    """
    pattern = r'[^\s"]*(?:"[^"]*"[^\s"]*\s*)*'
    return [part.strip() for part in re.findall(pattern, text.strip()) if part.strip()]
