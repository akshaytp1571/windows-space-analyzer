"""Deletion and cleanup actions. Every removal is checked by safety.validate_target first."""

from __future__ import annotations

import os
import threading
from typing import Callable

from space_analyzer import recycle, safety, scanner, system
from space_analyzer.models import ActionFailure, ActionKind, ActionReport, CleanupItem

_VOLUME_CACHES = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
_LONG_HKLM = "HKEY_LOCAL_MACHINE"
_FLAG_NAME = "StateFlags0065"
_WINDOWS_OLD_KEY = "previous installations"  # VolumeCaches subkey, compared in lower case
_COMMAND_TIMEOUT = 120
_CLEANMGR_TIMEOUT = 3600
_MAX_RESTORE_DELETIONS = 50
_UPDATE_SERVICES = ("wuauserv", "bits")
_DESCRIPTIONS = {
    ActionKind.EMPTY_RECYCLE_BIN: "empty the Recycle Bin (permanent)",
    ActionKind.DISABLE_HIBERNATION: "turn hibernation off with powercfg (permanent)",
    ActionKind.CLEAN_WINDOWS_OLD: "remove Windows.old with Windows Disk Cleanup (permanent)",
    ActionKind.CLEAN_UPDATE_CACHE: "stop Windows Update and move its download cache to the Recycle Bin",
}


def delete_to_recycle(
    paths: list[str],
    allowed_roots: list[str],
    *,
    dry_run: bool = False,
    recycler: Callable[[list[str]], tuple[list[str], list[ActionFailure]]] | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> ActionReport:
    """Move paths to the Recycle Bin after checking each one. Returns the report.

    Paths already gone are skipped with a message. A path that fails safety.validate_target
    is reported as a failure and never sent. With dry_run, the checks and sizes run but
    nothing is sent. Cancelling stops the loop and sets cancelled=True. Paths already
    checked are still sent, but a path whose size was cut short by cancel is left out.
    """
    report = ActionReport(action="recycle")
    if not paths:
        return report
    protected = system.protected_roots()
    allowlist = system.system_allowlist()
    valid: list[str] = []
    sizes: dict[str, int] = {}
    seen: set[str] = set()
    for path in paths:
        if _cancel_requested(report, cancel):
            break
        if path in seen:
            continue
        seen.add(path)
        if path.strip() and not os.path.lexists(path):
            report.messages.append(f"Already gone: {path}")
            continue
        reason = safety.validate_target(
            path, allowed_roots, protected=protected, allowlist=allowlist
        )
        if reason is not None:
            report.failed.append(ActionFailure(path, reason))
            continue
        _say(progress, f"Measuring {path}")
        size, _count = scanner.measure_path(path, cancel=cancel)
        if _cancel_requested(report, cancel):
            break  # the size may be partial, so this path is left out
        valid.append(path)
        sizes[path] = size
    if dry_run:
        report.dry_run = True
        report.removed_count = len(valid)
        report.bytes_removed = sum(sizes[path] for path in valid)
        report.messages.append("Dry run. Nothing was changed.")
        return report
    if valid:
        _say(progress, f"Sending {len(valid)} item(s) to the Recycle Bin")
        send = recycler if recycler is not None else recycle.send_to_recycle_bin
        sent, failed = send(list(valid))
        report.failed.extend(failed)
        report.removed_count = len(sent)
        report.bytes_removed = sum(sizes.get(path, 0) for path in sent)
    return report


def perform(
    item: CleanupItem,
    *,
    admin: bool,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> ActionReport:
    """Run one cleanup item and return its report. Admin-only items are refused without admin."""
    report = _run(item, admin=admin, dry_run=dry_run, progress=progress, cancel=cancel)
    report.title = item.title
    report.action = item.action.value
    return report


def _run(
    item: CleanupItem,
    *,
    admin: bool,
    dry_run: bool,
    progress: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> ActionReport:
    """Dispatch one item by its action kind. Checks run in the order the contract gives."""
    report = ActionReport(action=item.action.value)
    if item.requires_admin and not admin:
        report.failed.append(ActionFailure(item.title, "Requires administrator"))
        return report
    if item.action is ActionKind.NONE:
        return report
    if dry_run and item.action is not ActionKind.DELETE_PATHS:
        report.dry_run = True
        report.messages.append(f"Would run: {_describe(item)}")
        return report
    if _cancel_requested(report, cancel):
        return report
    if item.action is ActionKind.DELETE_PATHS:
        return delete_to_recycle(
            item.paths,
            item.allowed_roots,
            dry_run=dry_run,
            progress=progress,
            cancel=cancel,
        )
    if item.action is ActionKind.EMPTY_RECYCLE_BIN:
        _empty_bin(report, cancel)
    elif item.action is ActionKind.DISABLE_HIBERNATION:
        _disable_hibernation(item, report)
    elif item.action is ActionKind.DELETE_RESTORE_POINTS:
        _delete_restore_points(item, report, progress, cancel)
    elif item.action is ActionKind.CLEAN_WINDOWS_OLD:
        _clean_windows_old(item, report, progress, cancel)
    elif item.action is ActionKind.CLEAN_UPDATE_CACHE:
        _clean_update_cache(item, report, progress, cancel)
    else:
        report.failed.append(ActionFailure(item.title, "Unknown action"))
    return report


def _empty_bin(report: ActionReport, cancel: threading.Event | None) -> None:
    """Empty the Recycle Bin and record how much it held."""
    before_bytes, before_items = recycle.recycle_bin_info()
    if _cancel_requested(report, cancel):
        return
    ok = recycle.empty_recycle_bin()
    after_bytes, after_items = recycle.recycle_bin_info()
    report.bytes_removed = max(0, before_bytes - after_bytes)
    report.removed_count = max(0, before_items - after_items)
    if not ok:
        report.failed.append(ActionFailure("Recycle Bin", "Some items could not be removed"))


def _disable_hibernation(item: CleanupItem, report: ActionReport) -> None:
    """Turn hibernation off, which removes hiberfil.sys."""
    code, out, err = system.run_command(
        ["powercfg", "/hibernate", "off"], timeout=_COMMAND_TIMEOUT
    )
    if code == 0:
        report.messages.append("Hibernation is off. Windows removes hiberfil.sys.")
    else:
        report.failed.append(ActionFailure(item.title, _first_line(out, err, code)))


def _delete_restore_points(
    item: CleanupItem,
    report: ActionReport,
    progress: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> None:
    """Delete the oldest restore points on the system drive, keeping the newest ones."""
    keep = _keep_newest(item)
    drive_root = system.system_drive()
    drive = drive_root.rstrip("\\/")
    allowed: int | None = None  # copies that may go: the first count seen, minus keep
    deleted = 0
    for _ in range(_MAX_RESTORE_DELETIONS):
        if _cancel_requested(report, cancel):
            break
        count = system.count_restore_points(drive_root)
        if count is None:
            report.failed.append(ActionFailure(item.title, "Could not read the restore point list"))
            break
        if count <= keep:
            break
        if allowed is None:
            allowed = count - keep
        if deleted >= allowed:
            break  # never remove more copies than were there beyond the kept ones
        _say(progress, f"Deleting the oldest restore point ({count} found)")
        code, out, err = system.run_command(
            ["vssadmin", "delete", "shadows", f"/for={drive}", "/oldest", "/quiet"],
            timeout=_COMMAND_TIMEOUT,
        )
        if code != 0:
            report.failed.append(ActionFailure(item.title, _first_line(out, err, code)))
            break
        deleted += 1
    report.removed_count = deleted
    if not report.failed and not report.cancelled:
        report.messages.append(f"Kept the newest {keep} restore point(s).")


def _clean_windows_old(
    item: CleanupItem,
    report: ActionReport,
    progress: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> None:
    """Run Windows Disk Cleanup for the Windows.old category only."""
    code, out, err = system.run_command(["reg", "query", _VOLUME_CACHES], timeout=_COMMAND_TIMEOUT)
    if code != 0:
        report.failed.append(ActionFailure(
            item.title,
            "Could not read the Windows Disk Cleanup settings: " + _first_line(out, err, code),
        ))
        return
    keys = _cache_keys(out)
    if not any(_is_windows_old_key(key) for key in keys):
        report.failed.append(
            ActionFailure(item.title, "Windows.old cleanup is not available on this PC")
        )
        return
    for key in keys:
        if _cancel_requested(report, cancel):
            return
        value = "2" if _is_windows_old_key(key) else "0"
        code, out, err = system.run_command(
            ["reg", "add", key, "/v", _FLAG_NAME, "/t", "REG_DWORD", "/d", value, "/f"],
            timeout=_COMMAND_TIMEOUT,
        )
        if code != 0:
            # Stop here. cleanmgr would otherwise run every category still set to 2.
            report.failed.append(ActionFailure(
                item.title,
                "Could not set up Windows Disk Cleanup: " + _first_line(out, err, code),
            ))
            return
    if _cancel_requested(report, cancel):
        return
    _say(progress, "Running Windows Disk Cleanup. This can take a long time.")
    code, out, err = system.run_command(["cleanmgr", "/sagerun:65"], timeout=_CLEANMGR_TIMEOUT)
    if code != 0:
        report.failed.append(ActionFailure(item.title, _first_line(out, err, code)))
        return
    if any(os.path.lexists(path) for path in item.paths):
        report.messages.append(
            "Some files remain. Restart the PC and run this action again if they are still there."
        )


def _clean_update_cache(
    item: CleanupItem,
    report: ActionReport,
    progress: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> None:
    """Pause Windows Update, move its download cache to the Recycle Bin, then restart services."""
    stopped: list[str] = []
    try:
        for name in _UPDATE_SERVICES:
            if _cancel_requested(report, cancel):
                return
            _say(progress, f"Stopping the {name} service")
            code, out, err = system.run_command(["net", "stop", name], timeout=_COMMAND_TIMEOUT)
            if code == 0:
                stopped.append(name)
            elif code == 2 and _has_text(out, err, "not started"):
                continue  # already stopped, which is fine
            else:
                report.failed.append(ActionFailure(
                    item.title, f"Could not stop {name}: " + _first_line(out, err, code)
                ))
                return
        if _cancel_requested(report, cancel):
            return
        _merge(report, delete_to_recycle(
            item.paths, item.allowed_roots, progress=progress, cancel=cancel
        ))
    finally:
        for name in reversed(stopped):
            code, out, err = system.run_command(["net", "start", name], timeout=_COMMAND_TIMEOUT)
            if code != 0 and not (code == 2 and _has_text(out, err, "already")):
                report.failed.append(ActionFailure(
                    item.title, f"Could not restart {name}: " + _first_line(out, err, code)
                ))


def _cancel_requested(report: ActionReport, cancel: threading.Event | None) -> bool:
    """True when cancel is set. Marks the report as cancelled."""
    if cancel is not None and cancel.is_set():
        report.cancelled = True
        return True
    return False


def _say(progress: Callable[[str], None] | None, text: str) -> None:
    """Send a progress message when a callback was given."""
    if progress is not None:
        progress(text)


def _merge(report: ActionReport, part: ActionReport) -> None:
    """Add the counts, failures, messages and cancel flag of a sub-step to the report."""
    report.removed_count += part.removed_count
    report.bytes_removed += part.bytes_removed
    report.failed.extend(part.failed)
    report.messages.extend(part.messages)
    report.cancelled = report.cancelled or part.cancelled


def _describe(item: CleanupItem) -> str:
    """Return a short plain description of what a non-delete action would do."""
    if item.action is ActionKind.DELETE_RESTORE_POINTS:
        return f"delete old restore points, keeping the newest {_keep_newest(item)} (permanent)"
    return _DESCRIPTIONS.get(item.action, item.title)


def _keep_newest(item: CleanupItem) -> int:
    """Return how many restore points to keep. Never less than one."""
    try:
        return max(1, int(item.action_params.get("keep_newest", 1)))
    except (TypeError, ValueError):
        return 1


def _first_line(out: str, err: str, code: int) -> str:
    """Return the first non-empty line of command output, or a generic message."""
    for text in (err, out):
        for line in text.splitlines():
            if line.strip():
                return line.strip()
    return f"The command failed with exit code {code}"


def _has_text(out: str, err: str, needle: str) -> bool:
    """True when the lower-case needle appears in the command output."""
    return needle in f"{out}\n{err}".lower()


def _short_root(text: str) -> str:
    """Write HKEY_LOCAL_MACHINE as HKLM, the form used when passing keys to reg."""
    prefix = _LONG_HKLM + "\\"
    if text.upper().startswith(prefix):
        return "HKLM\\" + text[len(prefix):]
    return text


def _cache_keys(output: str) -> list[str]:
    """Return the direct subkeys of VolumeCaches as HKLM paths.

    reg query may print each subkey as a full key path or as an indented name. Value rows
    are skipped: they carry a REG_ type column after four spaces.
    """
    prefix = _VOLUME_CACHES.upper() + "\\"
    keys: list[str] = []
    for raw in output.splitlines():
        line = _short_root(raw.strip())
        if not line:
            continue
        if line.upper().startswith(prefix):
            name = line[len(prefix):]
        elif raw[:1].isspace() and "    REG_" not in raw:
            name = line
        else:
            continue
        if name and "\\" not in name:
            keys.append(_VOLUME_CACHES + "\\" + name)
    return keys


def _is_windows_old_key(key: str) -> bool:
    """True for the VolumeCaches subkey that holds the Windows.old category."""
    return key.rsplit("\\", 1)[-1].lower() == _WINDOWS_OLD_KEY
