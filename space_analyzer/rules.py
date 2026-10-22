"""Cleanup rules. Each rule looks at the disk and returns candidate CleanupItem objects.

Rules only describe what could be removed. analyzer.build_suggestions measures the
items, validates their paths, sets pre-selection and sorts them. Nothing here deletes.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from space_analyzer import system
from space_analyzer.models import ActionKind, Category, CleanupItem, Risk

ADMIN_NOTE = "Requires administrator"
RESTORE_NOTE = "Requires administrator to check restore points"

REGEN_NEW = "Yes, new ones are created as needed"
REGEN_CACHE = "Yes, rebuilt automatically"
REGEN_UPDATE = "Yes, Windows Update downloads them again when needed"
REGEN_HIBERNATION = "Yes, when hibernation is turned back on"
REGEN_PAGEFILE = "Recreated automatically"
REGEN_NONE = "No"

LOG_IMPACT = (
    "Only old log and report files are removed. Windows and apps create new ones when needed."
)
BROWSER_IMPACT = (
    "Sites load a little slower the first time you visit them again. "
    "Passwords, bookmarks and history are not affected."
)
BROWSER_WARNING = "Close the browser first. If it is still open, Windows may remove only part of its cache."
HIBERNATION_IMPACT = (
    "Hibernate and Fast Startup stop working. Normal sleep still works, and you can turn "
    "hibernation back on with powercfg /hibernate on."
)

_CHROMIUM_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache")
_THUMBNAIL_PATTERNS = ("thumbcache_*.db", "iconcache_*.db")


@dataclass(frozen=True)
class RuleContext:
    """Environment paths the rules read. Tests build one with temp folders."""

    admin: bool
    system_drive: str              # drive root, e.g. "C:\\"
    windows_dir: Optional[str]     # %SystemRoot%
    local_app_data: Optional[str]  # %LOCALAPPDATA%
    program_data: Optional[str]    # %ProgramData%
    user_temp: Optional[str]       # %TEMP%
    system_temp: Optional[str]     # %SystemRoot%\\Temp


RuleFunc = Callable[[RuleContext], List[CleanupItem]]


def make_context(admin: bool) -> RuleContext:
    """Build the context from the real machine through the system helpers."""
    return RuleContext(
        admin=admin,
        system_drive=system.system_drive(),
        windows_dir=system.windows_dir(),
        local_app_data=system.local_app_data(),
        program_data=system.program_data(),
        user_temp=system.user_temp_dir(),
        system_temp=system.system_temp_dir(),
    )


def _join(base: Optional[str], *parts: str) -> Optional[str]:
    """os.path.join that returns None when the base path is missing."""
    return os.path.join(base, *parts) if base else None


def _is_dir(path: Optional[str]) -> bool:
    """True for a real folder. Links and junctions are never followed."""
    if not path or not os.path.isdir(path):
        return False
    return not system.is_link(path)


def _is_file(path: Optional[str]) -> bool:
    """True for a real file. Links are never followed."""
    if not path or not os.path.isfile(path):
        return False
    return not system.is_link(path)


def _children(folder: Optional[str]) -> List[str]:
    """Full paths of the immediate children of a real folder. [] when missing or unreadable."""
    if not _is_dir(folder):
        return []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, name) for name in names]


def _existing_dirs(*paths: Optional[str]) -> List[str]:
    """The given paths that are real folders, in the same order."""
    return [path for path in paths if path and _is_dir(path)]


def _inside(path: Optional[str], root: Optional[str]) -> bool:
    """True when path lies inside root and is not root itself."""
    if not path or not root:
        return False
    return system.is_within(path, root) and system.norm(path) != system.norm(root)


def _is_thumbnail_name(name: str) -> bool:
    """True for thumbcache_*.db and iconcache_*.db (case-insensitive)."""
    lower = name.lower()
    return any(fnmatch.fnmatchcase(lower, pattern) for pattern in _THUMBNAIL_PATTERNS)


def _is_profile_name(name: str) -> bool:
    """Chromium profile folder names: Default, Profile N, Guest and System profiles."""
    return name in ("Default", "Guest Profile", "System Profile") or name.startswith("Profile ")


def _chromium_caches(user_data: Optional[str]) -> List[str]:
    """Cache, Code Cache and GPUCache folders of every profile in a Chromium User Data folder."""
    found: List[str] = []
    for profile in _children(user_data):
        if not _is_profile_name(os.path.basename(profile)) or not _is_dir(profile):
            continue
        for name in _CHROMIUM_CACHE_DIRS:
            folder = os.path.join(profile, name)
            if _is_dir(folder):
                found.append(folder)
    return found


def _item(
    item_id: str,
    title: str,
    category: Category,
    risk: Risk,
    *,
    description: str,
    impact: str,
    regenerates: str,
    action: ActionKind = ActionKind.DELETE_PATHS,
    paths: Optional[List[str]] = None,
    allowed_roots: Optional[List[str]] = None,
    requires_admin: bool = False,
    warnings: Optional[List[str]] = None,
    action_params: Optional[Dict[str, Any]] = None,
    note: str = "",
) -> CleanupItem:
    """Create a CleanupItem. Sizes are filled in later by the analyzer."""
    return CleanupItem(
        item_id=item_id,
        title=title,
        category=category,
        risk=risk,
        description=description,
        impact=impact,
        regenerates=regenerates,
        action=action,
        paths=list(paths or []),
        allowed_roots=list(allowed_roots or []),
        requires_admin=requires_admin,
        warnings=list(warnings or []),
        action_params=dict(action_params or {}),
        note=note,
    )


def rule_temp_user(ctx: RuleContext) -> List[CleanupItem]:
    """Children of %TEMP%, only when %TEMP% is a folder inside %LOCALAPPDATA%."""
    temp = ctx.user_temp
    if not _inside(temp, ctx.local_app_data):
        return []
    paths = _children(temp)
    if not paths:
        return []
    return [
        _item(
            "temp.user",
            "Your temporary files",
            Category.TEMP,
            Risk.SAFE,
            description="Temporary files that apps create while they run and do not always clean up.",
            impact=(
                "Temporary data is removed. Apps that are open right now may lose unsaved "
                "work that exists only in a temp file."
            ),
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=[temp],
        )
    ]


def rule_temp_system(ctx: RuleContext) -> List[CleanupItem]:
    """Children of %SystemRoot%\\Temp, only when it is a folder inside %SystemRoot%."""
    temp = ctx.system_temp
    if not _inside(temp, ctx.windows_dir):
        return []
    paths = _children(temp)
    if not paths:
        return []
    return [
        _item(
            "temp.system",
            "Windows temporary files",
            Category.TEMP,
            Risk.SAFE,
            description="Temporary files that Windows and installers leave in the system temp folder.",
            impact=(
                "Leftover setup and installer files are removed. A program that is still "
                "installing may have to be run again."
            ),
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=[temp],
            requires_admin=True,
        )
    ]


def _browser_item(
    item_id: str,
    title: str,
    root: Optional[str],
    folders: List[str],
    description: str,
) -> List[CleanupItem]:
    """One browser cache item, or nothing when no cache folder exists."""
    if not root or not folders:
        return []
    return [
        _item(
            item_id,
            title,
            Category.CACHE,
            Risk.SAFE,
            description=description,
            impact=BROWSER_IMPACT,
            regenerates=REGEN_CACHE,
            paths=folders,
            allowed_roots=[root],
            warnings=[BROWSER_WARNING],
        )
    ]


def rule_cache_chrome(ctx: RuleContext) -> List[CleanupItem]:
    """Chrome Cache, Code Cache and GPUCache folders in every profile."""
    root = _join(ctx.local_app_data, "Google", "Chrome", "User Data")
    return _browser_item(
        "cache.chrome",
        "Chrome cache",
        root,
        _chromium_caches(root),
        "Web pages, images and scripts that Chrome stored on disk to load sites faster.",
    )


def rule_cache_edge(ctx: RuleContext) -> List[CleanupItem]:
    """Edge Cache, Code Cache and GPUCache folders in every profile."""
    root = _join(ctx.local_app_data, "Microsoft", "Edge", "User Data")
    return _browser_item(
        "cache.edge",
        "Edge cache",
        root,
        _chromium_caches(root),
        "Web pages, images and scripts that Edge stored on disk to load sites faster.",
    )


def rule_cache_firefox(ctx: RuleContext) -> List[CleanupItem]:
    """The cache2 folder of every Firefox profile."""
    root = _join(ctx.local_app_data, "Mozilla", "Firefox", "Profiles")
    folders: List[str] = []
    for profile in _children(root):
        cache = os.path.join(profile, "cache2")
        if _is_dir(profile) and _is_dir(cache):
            folders.append(cache)
    return _browser_item(
        "cache.firefox",
        "Firefox cache",
        root,
        folders,
        "Web pages, images and scripts that Firefox stored on disk to load sites faster.",
    )


def rule_cache_thumbnails(ctx: RuleContext) -> List[CleanupItem]:
    """thumbcache_*.db and iconcache_*.db files in the Explorer folder."""
    folder = _join(ctx.local_app_data, "Microsoft", "Windows", "Explorer")
    files = [
        path
        for path in _children(folder)
        if _is_file(path) and _is_thumbnail_name(os.path.basename(path))
    ]
    if not files:
        return []
    return [
        _item(
            "cache.thumbnails",
            "Thumbnail and icon cache",
            Category.CACHE,
            Risk.SAFE,
            description="Small preview images and icons that Windows Explorer saves so folders open faster.",
            impact="Folder previews and icons take a little longer to appear the first time you open a folder.",
            regenerates=REGEN_CACHE,
            paths=files,
            allowed_roots=[folder],
        )
    ]


def rule_logs_crashdumps(ctx: RuleContext) -> List[CleanupItem]:
    """Children of %LOCALAPPDATA%\\CrashDumps and %LOCALAPPDATA%\\Microsoft\\Windows\\WER."""
    roots = _existing_dirs(
        _join(ctx.local_app_data, "CrashDumps"),
        _join(ctx.local_app_data, "Microsoft", "Windows", "WER"),
    )
    paths = [child for root in roots for child in _children(root)]
    if not paths:
        return []
    return [
        _item(
            "logs.crashdumps",
            "Your crash dumps and error reports",
            Category.LOGS,
            Risk.LOW,
            description="Crash dumps and error reports saved when apps on your account crashed.",
            impact=LOG_IMPACT + " You lose the details of past app crashes, which help when you troubleshoot.",
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=roots,
        )
    ]


def rule_logs_wer(ctx: RuleContext) -> List[CleanupItem]:
    """Children of ReportArchive and ReportQueue under %ProgramData%\\Microsoft\\Windows\\WER."""
    wer = _join(ctx.program_data, "Microsoft", "Windows", "WER")
    roots = _existing_dirs(_join(wer, "ReportArchive"), _join(wer, "ReportQueue"))
    paths = [child for root in roots for child in _children(root)]
    if not paths:
        return []
    return [
        _item(
            "logs.wer",
            "System error reports",
            Category.LOGS,
            Risk.LOW,
            description="Error reports Windows saved for the whole PC, including reports queued to send to Microsoft.",
            impact=LOG_IMPACT + " You lose the details of past system errors.",
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=roots,
            requires_admin=True,
        )
    ]


def rule_logs_windows(ctx: RuleContext) -> List[CleanupItem]:
    """Children of %SystemRoot%\\Logs."""
    logs = _join(ctx.windows_dir, "Logs")
    paths = _children(logs)
    if not paths:
        return []
    return [
        _item(
            "logs.windows",
            "Windows log files",
            Category.LOGS,
            Risk.LOW,
            description="Log files that Windows writes about updates, setup and system events.",
            impact=LOG_IMPACT + " You lose the history of past Windows events that help when you troubleshoot.",
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=[logs],
            requires_admin=True,
        )
    ]


def rule_logs_memdump(ctx: RuleContext) -> List[CleanupItem]:
    """%SystemRoot%\\MEMORY.DMP plus the children of %SystemRoot%\\Minidump."""
    dump = _join(ctx.windows_dir, "MEMORY.DMP")
    minidump = _join(ctx.windows_dir, "Minidump")
    paths: List[str] = []
    roots: List[str] = []
    if dump and _is_file(dump):
        paths.append(dump)
        roots.append(dump)
    if minidump and _is_dir(minidump):
        roots.append(minidump)
        paths.extend(_children(minidump))
    if not paths:
        return []
    return [
        _item(
            "logs.memdump",
            "Memory dump and minidumps",
            Category.LOGS,
            Risk.LOW,
            description="Crash files Windows saves when the PC fails: the full memory dump (MEMORY.DMP) and small minidump files.",
            impact=LOG_IMPACT + " You lose the data needed to investigate past crashes, such as blue screens.",
            regenerates=REGEN_NEW,
            paths=paths,
            allowed_roots=roots,
            requires_admin=True,
        )
    ]


def rule_updates_download(ctx: RuleContext) -> List[CleanupItem]:
    """Children of %SystemRoot%\\SoftwareDistribution\\Download. Moved by CLEAN_UPDATE_CACHE."""
    folder = _join(ctx.windows_dir, "SoftwareDistribution", "Download")
    paths = _children(folder)
    if not paths:
        return []
    return [
        _item(
            "updates.download",
            "Windows Update download cache",
            Category.OLD_WINDOWS,
            Risk.SAFE,
            description="Update files that Windows Update downloaded and keeps before installing them.",
            impact=(
                "You lose the downloaded update files. Windows Update downloads them again "
                "when they are needed, which can use more internet data."
            ),
            regenerates=REGEN_UPDATE,
            action=ActionKind.CLEAN_UPDATE_CACHE,
            paths=paths,
            allowed_roots=[folder],
            requires_admin=True,
            warnings=[
                "The files go to the Recycle Bin. The bin must be emptied before the space is freed.",
                "Windows Update is paused briefly while the files are moved.",
            ],
        )
    ]


def rule_oldwin_windows_old(ctx: RuleContext) -> List[CleanupItem]:
    """%SystemDrive%\\Windows.old. Removed by cleanmgr, never by the Recycle Bin."""
    folder = os.path.join(ctx.system_drive, "Windows.old")
    if not _is_dir(folder):
        return []
    return [
        _item(
            "oldwin.windows_old",
            "Previous Windows installation",
            Category.OLD_WINDOWS,
            Risk.ADVANCED,
            description="The files of your previous Windows version, kept so you could go back to it.",
            impact=(
                "You can no longer roll back to the previous Windows version. Windows also "
                "deletes this folder by itself after about 10 days."
            ),
            regenerates=REGEN_NONE,
            action=ActionKind.CLEAN_WINDOWS_OLD,
            paths=[folder],
            allowed_roots=[folder],
            requires_admin=True,
            warnings=["Windows removes them permanently with its own tool, not the Recycle Bin."],
        )
    ]


def rule_recycle_bin(ctx: RuleContext) -> List[CleanupItem]:
    """The Recycle Bin. Size comes from recycle.recycle_bin_info in the analyzer."""
    return [
        _item(
            "recycle.bin",
            "Recycle Bin",
            Category.RECYCLE,
            Risk.LOW,
            description="Files you deleted that are waiting in the Recycle Bin.",
            impact="Files in the Recycle Bin are gone for good. You can no longer restore them.",
            regenerates=REGEN_NONE,
            action=ActionKind.EMPTY_RECYCLE_BIN,
            warnings=[
                "This permanently deletes everything in the Recycle Bin, including files you put there yourself.",
            ],
        )
    ]


def rule_hibernate_file(ctx: RuleContext) -> List[CleanupItem]:
    """%SystemDrive%\\hiberfil.sys. Size only: turning hibernation off removes it."""
    path = os.path.join(ctx.system_drive, "hiberfil.sys")
    if not _is_file(path):
        return []
    return [
        _item(
            "hibernate.file",
            "Hibernation file (hiberfil.sys)",
            Category.HIBERNATION_RESTORE,
            Risk.ADVANCED,
            description="A system file that stores the contents of memory when the PC hibernates.",
            impact=HIBERNATION_IMPACT,
            regenerates=REGEN_HIBERNATION,
            action=ActionKind.DISABLE_HIBERNATION,
            paths=[path],
            allowed_roots=[path],
            requires_admin=True,
            warnings=["Turning off hibernation deletes this file permanently. It does not go to the Recycle Bin."],
        )
    ]


def rule_restore_points(ctx: RuleContext) -> List[CleanupItem]:
    """System Restore points. Without admin the count is not attempted and the note is shown."""
    note = ""
    if ctx.admin:
        count = system.count_restore_points(ctx.system_drive)
        if count is None or count <= 1:
            return []
    else:
        note = RESTORE_NOTE
    item = _item(
        "restore.points",
        "System Restore points",
        Category.HIBERNATION_RESTORE,
        Risk.ADVANCED,
        description="Saved copies of Windows system files and settings from earlier points in time.",
        impact="You lose the ability to roll Windows back to the older restore points. The newest restore point is kept.",
        regenerates=REGEN_NONE,
        action=ActionKind.DELETE_RESTORE_POINTS,
        action_params={"keep_newest": 1},
        requires_admin=True,
        warnings=[
            "The copies are removed permanently, and you lose the ability to roll back to them.",
            "Shadow copies made by backup software on the same drive may also be removed.",
        ],
        note=note,
    )
    item.size_known = False
    return [item]


def rule_pagefile(ctx: RuleContext) -> List[CleanupItem]:
    """%SystemDrive%\\pagefile.sys. Information only: no checkbox and no action."""
    path = os.path.join(ctx.system_drive, "pagefile.sys")
    if not _is_file(path):
        return []
    return [
        _item(
            "system.pagefile",
            "Page file (pagefile.sys)",
            Category.SYSTEM_FILES,
            Risk.INFO,
            description="Disk space Windows uses as extra memory when RAM runs low.",
            impact="Information only. Nothing is removed here, because Windows needs this file when memory runs low.",
            regenerates=REGEN_PAGEFILE,
            action=ActionKind.NONE,
            paths=[path],
            allowed_roots=[path],
        )
    ]


RULES: Tuple[RuleFunc, ...] = (
    rule_temp_user,
    rule_temp_system,
    rule_cache_chrome,
    rule_cache_edge,
    rule_cache_firefox,
    rule_cache_thumbnails,
    rule_logs_crashdumps,
    rule_logs_wer,
    rule_logs_windows,
    rule_logs_memdump,
    rule_updates_download,
    rule_oldwin_windows_old,
    rule_recycle_bin,
    rule_hibernate_file,
    rule_restore_points,
    rule_pagefile,
)


def collect_items(ctx: RuleContext) -> List[CleanupItem]:
    """Run every rule. Returns unmeasured, unvalidated candidates.

    Admin-only items get the "Requires administrator" note when the process is not elevated.
    """
    items: List[CleanupItem] = []
    for rule in RULES:
        items.extend(rule(ctx))
    if not ctx.admin:
        for item in items:
            if item.requires_admin and not item.note:
                item.note = ADMIN_NOTE
    return items
