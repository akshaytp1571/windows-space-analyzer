"""Pure rules behind the cleanup pages: what is selected, confirmed, removed and listed.

Nothing here imports PySide6, so these rules can be tested on any machine. The page
modules call these functions and only deal with widgets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from space_analyzer import util
from space_analyzer.models import (
    ActionKind,
    ActionReport,
    CleanupItem,
    DuplicateGroup,
    FileEntry,
)

# Actions that cannot be undone. The confirmation must say so.
PERMANENT_ACTIONS = frozenset(
    {
        ActionKind.EMPTY_RECYCLE_BIN,
        ActionKind.DISABLE_HIBERNATION,
        ActionKind.DELETE_RESTORE_POINTS,
        ActionKind.CLEAN_WINDOWS_OLD,
    }
)
# Actions that move files to the Recycle Bin. The space comes back only once the bin is emptied.
RECYCLE_ACTIONS = frozenset({ActionKind.DELETE_PATHS, ActionKind.CLEAN_UPDATE_CACHE})

EMPTY_BIN_NOTICE = (
    "This permanently deletes everything in the Recycle Bin, "
    "including files you put there yourself."
)
RECYCLE_NOTICE = "Files go to the Recycle Bin. Space is only freed after you empty it."
PERMANENT_NOTICE = "This cannot be undone."
APPDATA_WARNING = "Some apps keep data here. Deleting it can reset those apps."
KEEP_ONE_MESSAGE = "At least one copy must stay in each group."

# Minimum-size choices for the page combo boxes, as (label, bytes).
LARGE_FILE_SIZES: tuple[tuple[str, int], ...] = (
    ("100 MB", 100 * 1024**2),
    ("500 MB", 500 * 1024**2),
    ("1 GB", 1024**3),
    ("5 GB", 5 * 1024**3),
)
DUPLICATE_SIZES: tuple[tuple[str, int], ...] = (
    ("1 MB", 1024**2),
    ("10 MB", 10 * 1024**2),
    ("100 MB", 100 * 1024**2),
)

_APPDATA_FOLDER = "\\appdata\\"


def cleanup_order(items: Iterable[CleanupItem]) -> list[CleanupItem]:
    """Return the items in the order they run. The Recycle Bin is emptied last, and the rest keep their order."""
    ordered = list(items)
    others = [item for item in ordered if item.action != ActionKind.EMPTY_RECYCLE_BIN]
    bins = [item for item in ordered if item.action == ActionKind.EMPTY_RECYCLE_BIN]
    return others + bins


def is_permanent(items: Iterable[CleanupItem]) -> bool:
    """True when any item is removed in a way that cannot be undone."""
    return any(item.action in PERMANENT_ACTIONS for item in items)


def selection_bytes(items: Iterable[CleanupItem]) -> int:
    """Total measured size of the items that have an action. Size-only items are not counted."""
    return sum(
        item.size_bytes
        for item in items
        if item.size_known and item.action != ActionKind.NONE
    )


def selection_summary(count: int, total_bytes: int) -> str:
    """Header line for a selection, for example '2 selected, about 1.5 KB'."""
    if count <= 0:
        return "Nothing selected"
    return f"{count} selected, about {util.format_bytes(total_bytes)}"


def cleanup_details(items: Iterable[CleanupItem]) -> list[str]:
    """Lines for the confirmation: each item with its size, followed by that item's warnings."""
    lines: list[str] = []
    for item in items:
        size = util.format_bytes(item.size_bytes if item.size_known else None)
        lines.append(f"{item.title}: {size}")
        lines.extend(item.warnings)
    return lines


def cleanup_notices(items: Iterable[CleanupItem]) -> list[str]:
    """Sentences the confirmation must show for this selection, in reading order."""
    actions = {item.action for item in items}
    notices: list[str] = []
    if ActionKind.EMPTY_RECYCLE_BIN in actions:
        notices.append(EMPTY_BIN_NOTICE)
    elif actions & RECYCLE_ACTIONS:
        notices.append(RECYCLE_NOTICE)
    if actions & PERMANENT_ACTIONS:
        notices.append(PERMANENT_NOTICE)
    return notices


def moved_to_recycle_bin(items: Sequence[CleanupItem], reports: Sequence[ActionReport]) -> bool:
    """True when a run moved files to the Recycle Bin and did not empty the bin itself."""
    if any(item.action == ActionKind.EMPTY_RECYCLE_BIN for item in items):
        return False
    return any(
        item.action in RECYCLE_ACTIONS and report.removed_count > 0
        for item, report in zip(items, reports)
    )


def keep_one_blocked(checked: Sequence[bool]) -> bool:
    """True when the check boxes of a group would leave no copy unchecked. Such a change must be undone."""
    return len(checked) > 0 and all(checked)


def keep_oldest_flags(copies: int) -> list[bool]:
    """Check state for 'Keep oldest' on a group sorted oldest first: the first copy stays unchecked."""
    if copies <= 0:
        return []
    return [False] + [True] * (copies - 1)


def paths_not_failed(paths: Iterable[str], reports: Iterable[ActionReport]) -> list[str]:
    """Paths that no report lists as failed. These are the rows to remove after a Recycle Bin move."""
    failed: set[str] = set()
    for report in reports:
        failed.update(failure.path for failure in report.failed)
    return [path for path in paths if path not in failed]


def is_appdata_path(path: str) -> bool:
    """True when the path lies inside an AppData folder, where deleting can reset apps."""
    return _APPDATA_FOLDER in path.lower().replace("/", "\\")


def filter_large_files(
    entries: Iterable[FileEntry],
    *,
    min_size: int,
    text: str,
    is_hidden: Callable[[str], bool],
) -> list[FileEntry]:
    """Files at least min_size big whose path contains text (any case) and that are not hidden, largest first."""
    needle = text.strip().lower()
    kept = [
        entry
        for entry in entries
        if entry.size >= min_size
        and (not needle or needle in entry.path.lower())
        and not is_hidden(entry.path)
    ]
    return sorted(kept, key=lambda entry: entry.size, reverse=True)


def group_summary(group: DuplicateGroup) -> str:
    """Heading for a duplicate group, for example '3 copies, 1.0 MB each, 2.0 MB can be freed'."""
    return (
        f"{len(group.paths)} copies, {util.format_bytes(group.size)} each, "
        f"{util.format_bytes(group.reclaimable_bytes)} can be freed"
    )


def shorten_path(path: str, limit: int = 70) -> str:
    """Keep the end of a long path so status text stays on one readable line."""
    if len(path) <= limit:
        return path
    return "..." + path[-(limit - 3):]
