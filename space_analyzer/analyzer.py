"""Build the Suggestions list: run the rules, measure, validate, pre-select and sort."""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from space_analyzer import recycle, rules, safety, scanner, system
from space_analyzer.models import CATEGORY_ORDER, ActionKind, CleanupItem, Risk


def build_suggestions(
    *,
    admin: bool | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[CleanupItem]:
    """Return the cleanup items found on this PC, sorted by category and then size.

    admin=None uses system.is_admin(). Returns [] when cancelled. Items with no
    measurable content are dropped. Restore points are kept with an unknown size.
    """
    is_admin = system.is_admin() if admin is None else admin
    ctx = rules.make_context(is_admin)
    protected = system.protected_roots()
    allowlist = system.system_allowlist()
    items: List[CleanupItem] = []
    _report(progress, "Looking for cleanup candidates")
    for item in rules.collect_items(ctx):
        if _cancelled(cancel):
            return []
        _report(progress, f"Measuring {item.title}")
        if item.action == ActionKind.DELETE_PATHS:
            _keep_valid_paths(item, protected, allowlist)
            if not item.paths:
                continue
        _measure(item, cancel)
        if item.size_known and item.size_bytes <= 0:
            continue
        if item.requires_admin and not is_admin and not item.note:
            item.note = rules.ADMIN_NOTE
        item.default_selected = item.risk == Risk.SAFE and not item.requires_admin
        items.append(item)
    if _cancelled(cancel):
        return []
    order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    items.sort(key=lambda item: (order[item.category], -item.size_bytes))
    return items


def _cancelled(cancel: Optional[threading.Event]) -> bool:
    """True when the caller asked to stop."""
    return cancel is not None and cancel.is_set()


def _report(progress: Optional[Callable[[str], None]], message: str) -> None:
    """Send a status line to the caller when it asked for one."""
    if progress is not None:
        progress(message)


def _keep_valid_paths(
    item: CleanupItem, protected: List[str], allowlist: List[str]
) -> None:
    """Remove paths that fail safety.validate_target and add one warning for them."""
    valid: List[str] = []
    rejected: List[tuple[str, str]] = []
    for path in item.paths:
        reason = safety.validate_target(
            path, item.allowed_roots, protected=protected, allowlist=allowlist
        )
        if reason is None:
            valid.append(path)
        else:
            rejected.append((path, reason))
    item.paths = valid
    if rejected:
        first_path, first_reason = rejected[0]
        item.warnings.append(
            f"Left out {len(rejected)} path(s) that failed a safety check, "
            f"for example {first_path} ({first_reason})."
        )


def _measure(item: CleanupItem, cancel: Optional[threading.Event]) -> None:
    """Fill size_bytes, file_count and size_known for one item."""
    if item.action == ActionKind.DELETE_RESTORE_POINTS:
        item.size_known = False
        return
    if item.action == ActionKind.EMPTY_RECYCLE_BIN:
        item.size_bytes, item.file_count = recycle.recycle_bin_info()
        item.size_known = True
        return
    total_bytes = 0
    total_files = 0
    for path in item.paths:
        size, files = scanner.measure_path(path, cancel=cancel)
        total_bytes += size
        total_files += files
    item.size_bytes = total_bytes
    item.file_count = total_files
    item.size_known = True
