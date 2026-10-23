"""Safety checks that decide whether a path may be removed by the cleaner."""

from __future__ import annotations

import os

from space_analyzer import system

_PERSONAL_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive")


def validate_target(
    path: str,
    allowed_roots: list[str],
    *,
    protected: list[str] | None = None,
    allowlist: list[str] | None = None,
) -> str | None:
    """Return None when path may be removed, otherwise a short reason it is refused.

    Checks run in order and stop at the first failure. The folder checks only apply to
    folders, so files are never refused by them. `protected` and `allowlist` default to
    the system lists; the cleaner passes them in once per call.
    """
    if not path or not path.strip():
        return "No path given"
    target = system.norm(path)
    if _is_drive_root(target):
        return "Drive roots cannot be removed"
    is_folder = os.path.isdir(path)
    if is_folder and _is_drive_root(os.path.dirname(target)):
        return "Top-level folders cannot be removed"
    if is_folder and _is_personal_folder(target):
        return "Personal folders cannot be removed"
    if system.is_link(path):
        return "Links and junctions are not followed"
    if not any(system.is_within(path, root) for root in allowed_roots):
        return "Outside the allowed locations"
    if protected is None:
        protected = system.protected_roots()
    if allowlist is None:
        allowlist = system.system_allowlist()
    if system.is_protected_path(path, roots=protected, allowlist=allowlist):
        return "Protected system location"
    return None


def validate_windows_old(path: str) -> str | None:
    """Return None when path is exactly this PC's Windows.old folder, otherwise a reason.

    Windows.old is a top-level folder on the system drive, so `validate_target` refuses it
    by design — that rule exists to stop a bad cleanup item naming an arbitrary top-level
    folder. This action needs the opposite guarantee: that the path is the one, fixed,
    named location the action is defined to target, and nothing else. It is a stricter
    check than validate_target, not a bypass of it.
    """
    if not path or not path.strip():
        return "No path given"
    expected = os.path.join(system.system_drive(), "Windows.old")
    if system.norm(path) != system.norm(expected):
        return "Not the Windows.old folder on this PC"
    if not os.path.isdir(path):
        return "Windows.old is no longer a folder"
    if system.is_link(path):
        return "Links and junctions are not followed"
    return None


def _is_drive_root(target: str) -> bool:
    """True for a filesystem root or a fixed drive root. `target` must already be normalized."""
    if os.path.dirname(target) == target:
        return True
    return any(system.norm(drive) == target for drive in system.fixed_drives())


def _is_personal_folder(target: str) -> bool:
    """True for the user profile folder or a well-known folder directly under it."""
    profile = system.user_profile()
    if not profile:
        return False
    base = system.norm(profile)
    if target == base:
        return True
    return any(target == system.norm(os.path.join(base, name)) for name in _PERSONAL_FOLDERS)
