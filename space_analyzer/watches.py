"""Folder watch: remember folders, keep a budget for them, and report what is over it.

A watch is a folder the user cares about plus an optional size budget in bytes. The
app measures watched folders on request and marks any that have grown past their
budget. Nothing here deletes or moves anything: a watch only reports.

Watches are stored beside the scan history, in the same per-user folder, and are
read defensively for the same reason: a corrupt file must not stop the app.

A watch never measures outside the folder it names. It uses scanner.measure_path,
which does not follow links or junctions, so a watch on a folder that contains a
junction to C:\\Windows reports the junction's own size, not the system folder's.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from space_analyzer import safety, scanner, system

_WATCH_VERSION = 1
_FILE_NAME = "watches.json"
_MAX_WATCHES = 50
_MIN_BUDGET = 1024 * 1024  # 1 MB; a smaller budget would fire on ordinary churn


def watches_path() -> str:
    """Where the watch list lives, beside the scan history."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "WindowsSpaceAnalyzer", _FILE_NAME)


@dataclass
class Watch:
    """One watched folder."""

    path: str
    budget_bytes: int = 0          # 0 means "watch, but do not alert"
    last_size: int = 0
    last_checked: float = 0.0

    @property
    def has_budget(self) -> bool:
        """True when a budget was set, so an alert is possible."""
        return self.budget_bytes > 0

    @property
    def used_fraction(self) -> float:
        """Size as a fraction of the budget, or 0 when no budget is set."""
        if not self.has_budget:
            return 0.0
        return self.last_size / self.budget_bytes

    @property
    def over_budget(self) -> bool:
        """True when the last measurement is above the budget."""
        return self.has_budget and self.last_size > self.budget_bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "budget_bytes": self.budget_bytes,
            "last_size": self.last_size,
            "last_checked": self.last_checked,
        }

    @classmethod
    def from_dict(cls, data: object) -> Optional["Watch"]:
        """Build a watch from stored JSON, or None when it is not usable."""
        if not isinstance(data, dict):
            return None
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        try:
            return cls(
                path=path,
                budget_bytes=max(0, int(data.get("budget_bytes", 0))),
                last_size=max(0, int(data.get("last_size", 0))),
                last_checked=float(data.get("last_checked", 0.0)),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class WatchReading:
    """The result of measuring one watch."""

    watch: Watch
    size: int
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the folder was measured without error."""
        return not self.error

    @property
    def over_budget(self) -> bool:
        """True when this reading is above the budget."""
        return self.watch.has_budget and self.ok and self.size > self.watch.budget_bytes

    @property
    def remaining(self) -> int:
        """Bytes left before the budget is reached; negative when over."""
        if not self.watch.has_budget:
            return 0
        return self.watch.budget_bytes - self.size


@dataclass
class WatchSummary:
    """Every reading from one check, plus what needs attention."""

    readings: list[WatchReading] = field(default_factory=list)

    @property
    def over(self) -> list[WatchReading]:
        """Readings that are above their budget, worst first."""
        return sorted(
            (one for one in self.readings if one.over_budget),
            key=lambda one: -(one.size - one.watch.budget_bytes),
        )

    @property
    def failures(self) -> list[WatchReading]:
        """Readings that could not be measured."""
        return [one for one in self.readings if not one.ok]

    @property
    def total_size(self) -> int:
        """The measured size of every watch that could be read."""
        return sum(one.size for one in self.readings if one.ok)

    @property
    def needs_attention(self) -> bool:
        """True when at least one watch is over its budget."""
        return any(one.over_budget for one in self.readings)


def budget_problem(path: str, budget_bytes: int) -> Optional[str]:
    """Return None when a watch may be created for this path, otherwise a reason."""
    if not path or not path.strip():
        return "No folder given"
    if not os.path.isdir(path):
        return "That folder does not exist"
    if budget_bytes and budget_bytes < _MIN_BUDGET:
        return "A budget must be at least 1 MB"
    if system.is_link(path):
        return "Links and junctions are not followed"
    reason = safety.validate_target(path, [path])
    if reason == "Protected system location":
        return "That is a protected system folder"
    return None


def add(watch: Watch, path: Optional[str] = None) -> Optional[str]:
    """Add a watch. Returns None on success, or the reason it was refused."""
    problem = budget_problem(watch.path, watch.budget_bytes)
    if problem is not None:
        return problem
    entries = load(path)
    if any(_same_folder(one.path, watch.path) for one in entries):
        return "That folder is already watched"
    if len(entries) >= _MAX_WATCHES:
        return f"Only {_MAX_WATCHES} folders can be watched"
    entries.append(watch)
    if not save(entries, path):
        return "The watch list could not be saved"
    return None


def remove(folder: str, path: Optional[str] = None) -> bool:
    """Remove a watch by folder. Returns True when the list was written."""
    entries = [one for one in load(path) if not _same_folder(one.path, folder)]
    return save(entries, path)


def load(path: Optional[str] = None) -> list[Watch]:
    """Read the watch list. A missing or broken file gives []."""
    target = path or watches_path()
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != _WATCH_VERSION:
        return []
    entries = data.get("watches")
    if not isinstance(entries, list):
        return []
    watches = [Watch.from_dict(one) for one in entries]
    return [one for one in watches if one is not None]


def save(watches: Iterable[Watch], path: Optional[str] = None) -> bool:
    """Write the watch list atomically. Returns False when it could not be written."""
    target = path or watches_path()
    payload = {"version": _WATCH_VERSION, "watches": [one.to_dict() for one in watches]}
    folder = os.path.dirname(os.path.abspath(target))
    handle_path = ""
    try:
        if folder:
            os.makedirs(folder, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=folder or ".", delete=False, suffix=".tmp"
        ) as handle:
            handle_path = handle.name
            json.dump(payload, handle, indent=1, ensure_ascii=False)
        os.replace(handle_path, target)
        return True
    except OSError:
        return False
    finally:
        if handle_path and os.path.exists(handle_path):
            try:
                os.remove(handle_path)
            except OSError:
                pass


def check(
    watches: Optional[Iterable[Watch]] = None,
    *,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> WatchSummary:
    """Measure every watch and return the readings.

    Measured folders have their `last_size` and `last_checked` updated in memory; the
    caller decides whether to save them. A folder that has been deleted reports an
    error rather than a size of zero, so it is not mistaken for a successful prune.
    """
    entries = list(watches) if watches is not None else load()
    summary = WatchSummary()
    now = time.time()
    for watch in entries:
        if cancel is not None and cancel.is_set():
            break
        if progress is not None:
            progress(f"Measuring {watch.path}")
        if not os.path.isdir(watch.path):
            summary.readings.append(
                WatchReading(watch=watch, size=0, error="That folder no longer exists")
            )
            continue
        try:
            size, _count = scanner.measure_path(watch.path, cancel=cancel)
        except OSError as exc:
            summary.readings.append(
                WatchReading(watch=watch, size=0, error=str(exc) or "Could not measure")
            )
            continue
        watch.last_size = size
        watch.last_checked = now
        summary.readings.append(WatchReading(watch=watch, size=size))
    return summary


def clear(path: Optional[str] = None) -> bool:
    """Delete the watch list. Returns False when it could not be removed."""
    target = path or watches_path()
    try:
        os.remove(target)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def describe(summary: WatchSummary) -> str:
    """A one-line status for the header, e.g. "3 watched, 1 over budget"."""
    total = len(summary.readings)
    if not total:
        return "No folders watched"
    over = len(summary.over)
    if over:
        return f"{total} watched — {over} over budget"
    return f"{total} watched — all within budget"


def _same_folder(first: str, second: str) -> bool:
    """Compare two folders, ignoring case and a trailing separator."""
    return first.rstrip("\\/").casefold() == second.rstrip("\\/").casefold()