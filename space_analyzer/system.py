"""Windows environment, admin rights, drives, protected paths and command running.

Windows-only code sits inside functions behind IS_WINDOWS and is marked as such, so
the module imports on Linux and its pure helpers can be tested there.
"""

from __future__ import annotations

import ctypes
import functools
import os
import re
import shutil
import subprocess
import sys

from space_analyzer.models import DriveInfo

IS_WINDOWS: bool = sys.platform == "win32"

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_CREATE_NO_WINDOW = 0x08000000
_DRIVE_FIXED = 3
_SW_SHOWNORMAL = 1
_RESTORE_POINT_TIMEOUT = 300
_DRIVE_RE = re.compile(r"[A-Za-z]:")
_SHADOW_ID_RE = re.compile(r"^[ \t]*Shadow Copy ID[ \t]*:", re.IGNORECASE | re.MULTILINE)
_NO_ITEMS_RE = re.compile(r"No items found", re.IGNORECASE)

# Items directly under the system drive that are protected.
_PROTECTED_SYSTEM_ITEMS = (
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Windows.old",
    "Recovery",
    "Boot",
    "EFI",
    "$WINDOWS.~BT",
    "$WinREAgent",
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
)
# Items protected on every fixed drive.
_PROTECTED_DRIVE_ITEMS = ("System Volume Information", "$Recycle.Bin")
# Cleanup is allowed inside these protected areas. Paths are relative to the system drive.
_ALLOWLIST_ITEMS = (
    ("Windows", "Temp"),
    ("Windows", "SoftwareDistribution", "Download"),
    ("Windows", "Logs"),
    ("Windows", "Minidump"),
    ("Windows", "MEMORY.DMP"),
    ("ProgramData", "Microsoft", "Windows", "WER", "ReportArchive"),
    ("ProgramData", "Microsoft", "Windows", "WER", "ReportQueue"),
)


def run_command(args: list[str], timeout: float = 120) -> tuple[int, str, str]:
    """Run a command without a shell and return (exit code, stdout, stderr). Never raises.

    Returns (-1, "", message) on timeout, a missing executable or bad arguments.
    """
    if not args:
        return -1, "", "No command given"
    extra: dict[str, int] = {}
    if IS_WINDOWS:
        # Windows-only: not runnable in this sandbox. Hides the console window.
        extra["creationflags"] = _CREATE_NO_WINDOW
    try:
        program = [_system_tool(args[0]), *args[1:]]
        done = subprocess.run(
            program,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            **extra,
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout:g} seconds"
    except Exception as exc:  # the contract says run_command never raises
        return -1, "", str(exc) or type(exc).__name__
    return done.returncode, done.stdout, done.stderr


def _system_tool(name: str) -> str:
    """Return the System32 copy of a bare tool name such as "vssadmin", when it exists.

    On Windows a bare name is searched in the current folder first, so a planted file
    there could run instead. Names with a path or drive are returned unchanged.
    """
    if not IS_WINDOWS or any(sep in name for sep in ("\\", "/", ":")):
        return name
    folder = os.path.join(windows_dir(), "System32")
    for candidate in (name, name + ".exe"):
        full = os.path.join(folder, candidate)
        if os.path.isfile(full):  # Windows-only: not runnable in this sandbox
            return full
    return name


def is_admin() -> bool:
    """True when the process runs with administrator rights. Always False off Windows."""
    if not IS_WINDOWS:
        return False
    # Windows-only: not runnable in this sandbox
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_as_admin() -> bool:
    """Start this program elevated (UAC prompt). Returns True when the new process started.

    The caller should then exit. Always False off Windows.
    """
    if not IS_WINDOWS:
        return False
    # Windows-only: not runnable in this sandbox
    script = os.path.abspath(sys.argv[0])
    frozen = bool(getattr(sys, "frozen", False))
    params = "" if frozen else f'"{script}"'
    workdir = os.path.dirname(sys.executable if frozen else script)
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(None, "runas", sys.executable, params, workdir, _SW_SHOWNORMAL)
    return (result or 0) > 32  # ShellExecuteW returns a value above 32 on success


def fixed_drives() -> list[str]:
    """Return fixed drive roots such as ["C:\\", "D:\\"]. Off Windows: ["/"]."""
    if not IS_WINDOWS:
        return ["/"]
    # Windows-only: not runnable in this sandbox
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives: list[str] = []
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(ord('A') + index)}:\\"
            if kernel32.GetDriveTypeW(root) == _DRIVE_FIXED:
                drives.append(root)
    return drives


def drive_info(root: str) -> DriveInfo:
    """Return total, used and free bytes for a drive. Zeros when it cannot be read."""
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return DriveInfo(root=root, total=0, used=0, free=0)
    return DriveInfo(root=root, total=usage.total, used=usage.used, free=usage.free)


def _env_value(name: str) -> str | None:
    """Read an environment variable at call time. Unset or empty gives None."""
    return os.environ.get(name) or None


def _drive_from_env(value: str | None) -> str | None:
    """Turn a SystemDrive value such as "C:" into "C:\\". Anything else gives None."""
    text = (value or "").strip().rstrip("\\/")
    if not _DRIVE_RE.fullmatch(text):
        return None
    return text + "\\"


def _is_below(path: str, root: str) -> bool:
    """True when path lies strictly inside root. Equal paths give False."""
    target, base = norm(path), norm(root)
    return target != base and _within_normalized(target, base)


def system_drive() -> str:
    """Return the system drive root, such as "C:\\". Off Windows: "/".

    A SystemDrive value that is not a drive letter is ignored, and C: is used instead.
    """
    if not IS_WINDOWS:
        return "/"
    return _drive_from_env(os.environ.get("SystemDrive")) or "C:\\"


def windows_dir() -> str:
    """Return the Windows folder (%SystemRoot%). Off Windows: "/Windows".

    A SystemRoot that is not a folder below the system drive is ignored.
    """
    if not IS_WINDOWS:
        return "/Windows"
    sd = system_drive()
    candidate = os.environ.get("SystemRoot") or ""
    if candidate and _is_below(candidate, sd):
        return candidate
    return os.path.join(sd, "Windows")


def user_profile() -> str | None:
    """Return %USERPROFILE%, or None when it is not set."""
    return _env_value("USERPROFILE")


def local_app_data() -> str | None:
    """Return %LOCALAPPDATA%, or None when it is not set."""
    return _env_value("LOCALAPPDATA")


def roaming_app_data() -> str | None:
    """Return %APPDATA%, or None when it is not set."""
    return _env_value("APPDATA")


def program_data() -> str | None:
    """Return %ProgramData%, or None when it is not set."""
    return _env_value("ProgramData")


def user_temp_dir() -> str | None:
    """Return %TEMP%, or None when it is not set."""
    return _env_value("TEMP")


def system_temp_dir() -> str | None:
    """Return the Windows Temp folder, which is windows_dir() plus Temp."""
    return os.path.join(windows_dir(), "Temp")


def protected_roots() -> list[str]:
    """Return the protected system locations and the protected folders on each fixed drive."""
    sd = system_drive()
    roots = [os.path.join(sd, item) for item in _PROTECTED_SYSTEM_ITEMS]
    for drive in fixed_drives():
        roots.extend(os.path.join(drive, item) for item in _PROTECTED_DRIVE_ITEMS)
    return roots


def system_allowlist() -> list[str]:
    """Return the protected areas where cleanup is allowed. The allowlist wins over the roots."""
    sd = system_drive()
    return [os.path.join(sd, *parts) for parts in _ALLOWLIST_ITEMS]


def norm(path: str) -> str:
    """Return the absolute, link-resolved, case-folded form of a path."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


@functools.lru_cache(maxsize=1024)
def _norm_cached(path: str) -> str:
    """norm() for protected and allowlist entries, which are compared once per file in a walk.

    Only fixed path strings are cached, never environment values.
    """
    return norm(path)


def _within_normalized(path: str, root: str) -> bool:
    """Inclusive containment for two paths that have already been through norm()."""
    if path == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(prefix)


def is_within(path: str, root: str) -> bool:
    """True when path equals root or lies inside it. Empty arguments give False."""
    if not path or not root:
        return False
    return _within_normalized(norm(path), norm(root))


def is_link(path: str) -> bool:
    """True for symlinks, junctions and other reparse points.

    A path that exists but cannot be inspected counts as a link, so callers refuse it.
    """
    if os.path.islink(path):
        return True
    if not IS_WINDOWS:
        return False
    try:
        # Windows-only: not runnable in this sandbox
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return True  # unreadable: treat as a link so the caller refuses it
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_protected_path(
    path: str,
    roots: list[str] | None = None,
    allowlist: list[str] | None = None,
) -> bool:
    """True when path lies inside a protected root and outside every allowlist entry.

    The allowlist wins over the roots. Pass precomputed lists to avoid rebuilding them.
    An empty path counts as protected. Empty list entries are ignored.
    """
    if not path:
        return True
    protected = [entry for entry in (protected_roots() if roots is None else roots) if entry]
    allowed = [entry for entry in (system_allowlist() if allowlist is None else allowlist) if entry]
    target = norm(path)
    if any(_within_normalized(target, _norm_cached(entry)) for entry in allowed):
        return False
    return any(_within_normalized(target, _norm_cached(entry)) for entry in protected)


def open_in_explorer(path: str) -> None:
    """Show a folder in Explorer, or select a file in its folder. Files are never opened.

    Does nothing when not Windows.
    """
    if not IS_WINDOWS:
        return
    # Windows-only: not runnable in this sandbox
    try:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.Popen(f'explorer /select,"{path}"')
    except OSError:
        return  # Explorer failures are not actionable from here


def spawn(args: list[str]) -> str | None:
    """Start a program detached, with no shell and no window of ours.

    Returns None when it started, or a reason when it did not. The argument list
    goes straight to CreateProcess, so a registry value can never be re-parsed by
    a shell. Used to launch a publisher's own uninstaller.
    """
    if not IS_WINDOWS:
        return "Starting programs is only supported on Windows"
    if not args or not args[0]:
        return "No program to start"
    try:
        subprocess.Popen(
            args,
            shell=False,
            close_fds=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return str(exc) or type(exc).__name__
    return None


def count_restore_points(drive_root: str) -> int | None:
    """Count the restore points (shadow copies) on a drive.

    Returns None when the count cannot be read or when not Windows.
    """
    if not IS_WINDOWS:
        return None
    # Windows-only: not runnable in this sandbox
    drive = drive_root.rstrip("\\/")
    code, output, _stderr = run_command(
        ["vssadmin", "list", "shadows", f"/for={drive}"], timeout=_RESTORE_POINT_TIMEOUT
    )
    if code != 0:
        return None
    return _parse_shadow_count(output)


def _parse_shadow_count(output: str) -> int | None:
    """Count the "Shadow Copy ID" lines in vssadmin output.

    Returns the count when copies are listed, 0 when the English "No items found"
    message is present, and None otherwise (for example localized output), so an
    unreadable result is never mistaken for zero copies.
    """
    count = len(_SHADOW_ID_RE.findall(output))
    if count:
        return count
    if _NO_ITEMS_RE.search(output):
        return 0
    return None
