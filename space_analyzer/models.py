"""Shared data models for Windows Space Analyzer.

This module is the single source of truth for data passed between the scanner,
analyzer, cleaner and user interface. Field names are part of the architecture
contract (docs/ARCHITECTURE.md). Change them only with the lead's approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Risk(str, Enum):
    """How much harm removing an item can cause."""

    SAFE = "safe"            # Regenerated automatically; no meaningful loss.
    LOW = "low"              # Harmless, but may cost time or convenience later.
    ADVANCED = "advanced"    # System-level change with a real trade-off.
    INFO = "info"            # Shown for awareness only; no action is offered.


class Category(str, Enum):
    """Groups shown in the Suggestions list. Values are the display titles."""

    TEMP = "Temporary files"
    CACHE = "Caches"
    LOGS = "Logs & crash dumps"
    OLD_WINDOWS = "Old Windows files"
    RECYCLE = "Recycle Bin"
    HIBERNATION_RESTORE = "Hibernation & restore points"
    SYSTEM_FILES = "System files"


CATEGORY_ORDER = (
    Category.TEMP,
    Category.CACHE,
    Category.LOGS,
    Category.OLD_WINDOWS,
    Category.RECYCLE,
    Category.HIBERNATION_RESTORE,
    Category.SYSTEM_FILES,
)


class ActionKind(str, Enum):
    """What happens when the user confirms a CleanupItem."""

    NONE = "none"                                   # information only
    DELETE_PATHS = "delete_paths"                   # cleaner.delete_paths(item.paths)
    EMPTY_RECYCLE_BIN = "empty_recycle_bin"
    DISABLE_HIBERNATION = "disable_hibernation"
    DELETE_RESTORE_POINTS = "delete_restore_points"  # action_params["keep_newest"]
    CLEAN_WINDOWS_OLD = "clean_windows_old"
    CLEAN_UPDATE_CACHE = "clean_update_cache"


@dataclass(frozen=True)
class DriveInfo:
    root: str        # "C:\\" on Windows, "/" elsewhere
    total: int       # bytes
    used: int        # bytes
    free: int        # bytes


@dataclass
class ScanNode:
    """A folder in the scanned tree. Sizes include all sub-folders."""

    path: str
    name: str
    size: int = 0
    file_count: int = 0
    dir_count: int = 0
    children: List["ScanNode"] = field(default_factory=list)  # sub-folders, largest first
    error: Optional[str] = None


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    modified: float  # POSIX timestamp in seconds


@dataclass(frozen=True)
class SkippedPath:
    path: str
    reason: str      # e.g. "access denied", "reparse point", "system-managed"


@dataclass
class ScanResult:
    root: str
    root_node: ScanNode
    total_size: int = 0
    file_count: int = 0
    dir_count: int = 0
    largest_files: List[FileEntry] = field(default_factory=list)  # largest first
    skipped: List[SkippedPath] = field(default_factory=list)
    error_count: int = 0
    cancelled: bool = False
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class ScanProgress:
    root: str
    current_path: str
    dirs_scanned: int
    files_scanned: int
    bytes_scanned: int


@dataclass
class DuplicateGroup:
    digest: str       # SHA-256 hex digest of the content
    size: int         # bytes per copy
    paths: List[str]  # two or more copies, oldest modified first

    @property
    def reclaimable_bytes(self) -> int:
        """Space freed if every copy except the first (oldest) is removed."""
        return self.size * max(0, len(self.paths) - 1)


@dataclass
class CleanupItem:
    item_id: str
    title: str
    category: Category
    risk: Risk
    description: str                 # what this is
    impact: str                      # what happens if it is removed
    regenerates: str                 # e.g. "Yes, automatically" or "No"
    action: ActionKind = ActionKind.DELETE_PATHS
    size_bytes: int = 0
    size_known: bool = True          # False when the size cannot be measured
    file_count: int = 0
    paths: List[str] = field(default_factory=list)           # concrete deletion targets
    allowed_roots: List[str] = field(default_factory=list)   # deletions must stay inside these
    action_params: Dict[str, Any] = field(default_factory=dict)
    requires_admin: bool = False
    default_selected: bool = False   # pre-ticked in the UI (SAFE items only)
    warnings: List[str] = field(default_factory=list)
    note: str = ""                   # short status, e.g. "Requires administrator"


@dataclass(frozen=True)
class ActionFailure:
    path: str
    reason: str


@dataclass
class ActionReport:
    action: str
    removed_count: int = 0
    bytes_removed: int = 0
    failed: List[ActionFailure] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    cancelled: bool = False
    dry_run: bool = False
    requires_restart: bool = False
    title: str = ""  # set by cleaner.perform to the item title, shown in reports
