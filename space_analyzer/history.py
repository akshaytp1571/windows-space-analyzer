"""Scan history: keep a small record of past scans so the app can show what changed.

One JSON file per user, in %LOCALAPPDATA%\\WindowsSpaceAnalyzer. Each entry stores
only aggregate figures — the drive total, the free space, the largest folders — not
the full folder tree. Storing every path of a scan would put a copy of the user's
file names on disk for no benefit, so the history stays small and discloses little.

The file is written atomically: a scan that is killed while saving must not leave a
half-written history that breaks the next launch. Every read is defensive for the
same reason; a corrupt file is treated as empty rather than raised.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from space_analyzer.models import ScanResult

_HISTORY_VERSION = 1
_MAX_ENTRIES = 60          # a year of weekly scans, or a couple of months of daily ones
_MAX_FOLDERS = 40          # largest folders kept per entry
_FILE_NAME = "scan-history.json"


def history_path() -> str:
    """Where the history lives: %LOCALAPPDATA%\\WindowsSpaceAnalyzer, or the temp folder."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "WindowsSpaceAnalyzer", _FILE_NAME)


@dataclass(frozen=True)
class FolderSnapshot:
    """One folder's size at the time of a scan."""

    path: str
    size: int
    file_count: int


@dataclass
class ScanSnapshot:
    """The summary of one finished scan."""

    timestamp: float
    root: str
    total_size: int
    file_count: int
    dir_count: int
    free_bytes: int
    capacity_bytes: int
    elapsed_seconds: float
    folders: list[FolderSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "root": self.root,
            "total_size": self.total_size,
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "free_bytes": self.free_bytes,
            "capacity_bytes": self.capacity_bytes,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "folders": [
                {"path": one.path, "size": one.size, "file_count": one.file_count}
                for one in self.folders
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ScanSnapshot"]:
        """Build a snapshot from stored JSON, or None when it is not usable."""
        if not isinstance(data, dict):
            return None
        try:
            folders = [
                FolderSnapshot(
                    path=str(one["path"]),
                    size=int(one["size"]),
                    file_count=int(one.get("file_count", 0)),
                )
                for one in data.get("folders", [])
                if isinstance(one, dict) and "path" in one and "size" in one
            ]
            return cls(
                timestamp=float(data["timestamp"]),
                root=str(data["root"]),
                total_size=int(data["total_size"]),
                file_count=int(data.get("file_count", 0)),
                dir_count=int(data.get("dir_count", 0)),
                free_bytes=int(data.get("free_bytes", 0)),
                capacity_bytes=int(data.get("capacity_bytes", 0)),
                elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
                folders=folders,
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class FolderChange:
    """How one folder changed between two scans."""

    path: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        """Positive when the folder grew, negative when it shrank."""
        return self.after - self.before


@dataclass
class Delta:
    """What changed between two scans of the same drive."""

    before: ScanSnapshot
    after: ScanSnapshot
    grew: list[FolderChange] = field(default_factory=list)
    shrank: list[FolderChange] = field(default_factory=list)
    appeared: list[FolderChange] = field(default_factory=list)
    vanished: list[FolderChange] = field(default_factory=list)

    @property
    def total_delta(self) -> int:
        """Change in scanned bytes."""
        return self.after.total_size - self.before.total_size

    @property
    def free_delta(self) -> int:
        """Change in free space. Positive means space was recovered."""
        return self.after.free_bytes - self.before.free_bytes

    @property
    def has_changes(self) -> bool:
        """True when anything at all differs."""
        return bool(
            self.grew or self.shrank or self.appeared or self.vanished
            or self.total_delta
        )


def make_snapshot(scan: ScanResult, *, free_bytes: int = 0, capacity_bytes: int = 0,
                  timestamp: Optional[float] = None) -> ScanSnapshot:
    """Summarise a finished scan into a snapshot.

    Only the top-level folders below the scan root are kept, largest first, capped
    at _MAX_FOLDERS. That is enough to answer "which folder grew?" without writing
    the whole tree to disk.
    """
    children = sorted(scan.root_node.children, key=lambda node: -node.size)
    folders = [
        FolderSnapshot(path=node.path, size=node.size, file_count=node.file_count)
        for node in children[:_MAX_FOLDERS]
    ]
    return ScanSnapshot(
        timestamp=float(timestamp if timestamp is not None else time.time()),
        root=scan.root,
        total_size=scan.total_size,
        file_count=scan.file_count,
        dir_count=scan.dir_count,
        free_bytes=free_bytes,
        capacity_bytes=capacity_bytes,
        elapsed_seconds=scan.elapsed_seconds,
        folders=folders,
    )


def load(path: Optional[str] = None) -> list[ScanSnapshot]:
    """Read the stored history, oldest first. A missing or broken file gives []."""
    target = path or history_path()
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    if data.get("version") != _HISTORY_VERSION:
        return []
    entries = data.get("scans")
    if not isinstance(entries, list):
        return []
    snapshots = [ScanSnapshot.from_dict(one) for one in entries]
    return [one for one in snapshots if one is not None]


def save(snapshots: Iterable[ScanSnapshot], path: Optional[str] = None) -> bool:
    """Write the history atomically. Returns False when it could not be written."""
    target = path or history_path()
    payload = {
        "version": _HISTORY_VERSION,
        "scans": [one.to_dict() for one in snapshots],
    }
    folder = os.path.dirname(os.path.abspath(target))
    handle_path = ""
    try:
        if folder:
            os.makedirs(folder, exist_ok=True)
        # Write to a sibling temp file, then replace, so a crash cannot truncate
        # the history that is already there.
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


def append(snapshot: ScanSnapshot, path: Optional[str] = None,
           limit: int = _MAX_ENTRIES) -> bool:
    """Add a snapshot to the history, dropping the oldest past `limit`."""
    snapshots = load(path)
    snapshots.append(snapshot)
    if len(snapshots) > limit:
        snapshots = snapshots[-limit:]
    return save(snapshots, path)


def previous_for(root: str, path: Optional[str] = None) -> Optional[ScanSnapshot]:
    """The newest stored snapshot for `root` other than the latest one."""
    entries = [one for one in load(path) if _same_root(one.root, root)]
    if len(entries) < 2:
        return None
    return entries[-2]


def latest_for(root: str, path: Optional[str] = None) -> Optional[ScanSnapshot]:
    """The newest stored snapshot for `root`."""
    entries = [one for one in load(path) if _same_root(one.root, root)]
    return entries[-1] if entries else None


def compare(before: ScanSnapshot, after: ScanSnapshot, *, min_bytes: int = 0) -> Delta:
    """Work out which folders grew, shrank, appeared or vanished.

    `min_bytes` hides changes too small to care about, which keeps the list short
    when a folder changes by a few kilobytes between scans.
    """
    delta = Delta(before=before, after=after)
    old = {one.path: one for one in before.folders}
    new = {one.path: one for one in after.folders}

    for path, now in new.items():
        was = old.get(path)
        if was is None:
            if now.size >= min_bytes:
                delta.appeared.append(FolderChange(path=path, before=0, after=now.size))
            continue
        change = now.size - was.size
        if abs(change) < min_bytes or change == 0:
            continue
        entry = FolderChange(path=path, before=was.size, after=now.size)
        if change > 0:
            delta.grew.append(entry)
        else:
            delta.shrank.append(entry)

    for path, was in old.items():
        if path in new:
            continue
        if was.size >= min_bytes:
            delta.vanished.append(FolderChange(path=path, before=was.size, after=0))

    delta.grew.sort(key=lambda one: -one.delta)
    delta.shrank.sort(key=lambda one: one.delta)
    delta.appeared.sort(key=lambda one: -one.after)
    delta.vanished.sort(key=lambda one: -one.before)
    return delta


def clear(path: Optional[str] = None) -> bool:
    """Delete the history file. Returns False when it could not be removed."""
    target = path or history_path()
    try:
        os.remove(target)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _same_root(first: str, second: str) -> bool:
    """Compare two drive roots, ignoring case and a trailing separator."""
    return first.rstrip("\\/").casefold() == second.rstrip("\\/").casefold()