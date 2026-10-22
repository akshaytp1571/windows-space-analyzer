"""Disk scanner: sizes a folder tree, keeps a pruned tree and the largest files.

Links and junctions are skipped and reparse folders are not walked. Cloud-only files
count as files with 0 bytes. Expected failures are recorded in the result, not raised.
"""

from __future__ import annotations

import heapq
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Tuple

from space_analyzer import models

_REPARSE_POINT = 0x400
_OFFLINE = 0x1000
_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD_MASK = _OFFLINE | _RECALL_ON_DATA_ACCESS
_TAG_SYMLINK = 0xA000000C
_TAG_MOUNT_POINT = 0xA0000003  # junctions
_TAG_LX_SYMLINK = 0xA000001D  # WSL symbolic links

_REASON_LINK = "link or junction"
_REASON_REPARSE = "reparse point"
_MAX_SKIPPED = 5000
_PROGRESS_INTERVAL = 0.25  # at most 4 progress calls per second
_WORKERS = 8


def _error_reason(exc: BaseException) -> str:
    """Return a short, plain reason for a failed filesystem call."""
    if isinstance(exc, PermissionError):
        return "access denied"
    if isinstance(exc, FileNotFoundError):
        return "not found"
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__


def _skip_reason(path: str, st: os.stat_result) -> Optional[str]:
    """Return why an entry is skipped (not walked or counted), or None when it is counted."""
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is None:  # no Windows attributes: only symbolic links count as links
        return _REASON_LINK if os.path.islink(path) else None
    # Windows-only: not runnable in this sandbox (needs real reparse data).
    if not attrs & _REPARSE_POINT:
        return None
    if getattr(st, "st_reparse_tag", 0) in (0, _TAG_SYMLINK, _TAG_MOUNT_POINT, _TAG_LX_SYMLINK):
        return _REASON_LINK
    if stat.S_ISDIR(st.st_mode):
        return _REASON_REPARSE  # never walk into other reparse folders (cloud, dedup, ...)
    return None  # other reparse files are counted from their own metadata, never opened


def _file_bytes(st: os.stat_result) -> int:
    """Return the bytes counted for a file. Cloud-only files count as 0."""
    # Windows-only: not runnable in this sandbox (cloud attributes exist only on Windows).
    if getattr(st, "st_file_attributes", 0) & _CLOUD_MASK:
        return 0
    return int(st.st_size)


def _offer(heap: List[tuple], limit: int, size: int, seq: int, value: object) -> None:
    """Keep the `limit` largest (size, seq, value) items in a min-heap."""
    if limit <= 0:
        return
    item = (size, seq, value)
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif size > heap[0][0]:
        heapq.heapreplace(heap, item)


class _Dir:
    """A folder record while the scan runs. Finished records become ScanNode objects."""

    __slots__ = (
        "path", "name", "parent", "capacity", "pending", "error",
        "direct_size", "direct_files", "direct_dirs",
        "child_size", "child_files", "child_dirs",
        "heap", "seq", "pruned_count", "pruned_size", "pruned_files", "pruned_dirs",
    )

    def __init__(self, path: str, name: str, parent: Optional[_Dir], capacity: int) -> None:
        self.path = path
        self.name = name
        self.parent = parent
        self.capacity = capacity  # children kept before pruning (max_children)
        self.pending = 1  # outstanding units: own listing, child folders, folded folders
        self.error: Optional[str] = None
        self.direct_size = 0  # bytes of files directly inside, plus folded folders
        self.direct_files = 0
        self.direct_dirs = 0  # folded folders below this one (deeper than max_depth)
        self.child_size = 0  # totals of finished child folders
        self.child_files = 0
        self.child_dirs = 0
        self.heap: List[tuple] = []  # largest child folders: (size, seq, ScanNode)
        self.seq = 0
        self.pruned_count = 0
        self.pruned_size = 0
        self.pruned_files = 0
        self.pruned_dirs = 0

    def attach(self, node: models.ScanNode) -> None:
        """Count a finished child folder and keep it only if it is among the largest."""
        self.child_size += node.size
        self.child_files += node.file_count
        self.child_dirs += 1 + node.dir_count
        self.seq += 1
        item = (node.size, self.seq, node)
        if len(self.heap) < self.capacity:
            heapq.heappush(self.heap, item)
        elif node.size > self.heap[0][0]:
            self._prune(heapq.heapreplace(self.heap, item)[2])
        else:
            self._prune(node)

    def _prune(self, node: models.ScanNode) -> None:
        """Roll a child that is not kept into the summary counters."""
        self.pruned_count += 1
        self.pruned_size += node.size
        self.pruned_files += node.file_count
        self.pruned_dirs += 1 + node.dir_count

    def finish(self) -> models.ScanNode:
        """Build this folder's ScanNode. Call only after every child has been attached."""
        if self.pruned_count:  # leave one slot for the summary row
            while len(self.heap) > self.capacity - 1:
                self._prune(heapq.heappop(self.heap)[2])
        children = [entry[2] for entry in sorted(self.heap, key=lambda entry: (-entry[0], entry[1]))]
        if self.pruned_count:
            children.append(models.ScanNode(
                path="",
                name=f"{self.pruned_count} smaller folders",
                size=self.pruned_size,
                file_count=self.pruned_files,
                dir_count=self.pruned_dirs,
            ))
        return models.ScanNode(
            path=self.path,
            name=self.name,
            size=self.direct_size + self.child_size,
            file_count=self.direct_files + self.child_files,
            dir_count=self.direct_dirs + self.child_dirs,
            children=children,
            error=self.error,
        )


class _ScanRun:
    """State shared by the pool workers of one scan. Tree and counters change only under `lock`."""

    def __init__(
        self,
        root: str,
        max_depth: int,
        max_children: int,
        top_files: int,
        progress: Optional[Callable[[models.ScanProgress], None]],
        cancel: Optional[threading.Event],
    ) -> None:
        self.root = root
        self.max_depth = max_depth
        self.max_children = max_children
        self.top_files = top_files
        self.progress = progress
        self.cancel = cancel
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.pool: Optional[ThreadPoolExecutor] = None
        self.root_node: Optional[models.ScanNode] = None
        self.top_heap: List[tuple] = []
        self.seq = 0
        self.skipped: List[models.SkippedPath] = []
        self.error_count = 0
        self.cancelled = False
        self.dirs_scanned = 0
        self.files_scanned = 0
        self.bytes_scanned = 0
        self.last_emit = float("-inf")

    def work(self, path: str, depth: int, rec: Optional[_Dir], absorber: _Dir) -> None:
        """Pool task: scan one folder, then release the unit it holds on its absorber."""
        try:
            self._scan_one(path, depth, rec, absorber)
        except Exception as exc:  # one bad folder must never stall the scan
            reason = _error_reason(exc)
            with self.lock:
                self.error_count += 1
                self._add_skip(models.SkippedPath(path, reason))
                if rec is not None:
                    rec.error = reason
        finally:
            self._release(absorber)

    def _add_skip(self, item: models.SkippedPath) -> None:
        """Record a skipped entry. Call with the lock held. The list is capped."""
        if len(self.skipped) < _MAX_SKIPPED:
            self.skipped.append(item)

    def _release(self, rec: _Dir) -> None:
        """Release one unit of `rec`. When none are left, finish it and release its parent."""
        while True:
            with self.lock:
                rec.pending -= 1
                if rec.pending > 0:
                    return
                node = rec.finish()
                parent = rec.parent
                if parent is not None:
                    parent.attach(node)
                else:
                    self.root_node = node
            if parent is None:
                self.done.set()
                return
            rec = parent

    def _scan_one(self, path: str, depth: int, rec: Optional[_Dir], absorber: _Dir) -> None:
        """List one folder, merge its results under the lock and queue its subfolders."""
        if self.cancel is not None and self.cancel.is_set():
            with self.lock:
                self.cancelled = True
            return
        files = 0
        size = 0
        top: List[tuple] = []
        subdirs: List[Tuple[str, str]] = []
        skipped_now: List[models.SkippedPath] = []
        errors: List[models.SkippedPath] = []
        listing_error: Optional[str] = None
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except (OSError, ValueError) as exc:
                        errors.append(models.SkippedPath(entry.path, _error_reason(exc)))
                        continue
                    reason = _skip_reason(entry.path, st)
                    if reason is not None:
                        skipped_now.append(models.SkippedPath(entry.path, reason))
                    elif stat.S_ISDIR(st.st_mode):
                        subdirs.append((entry.path, entry.name))
                    else:
                        nbytes = _file_bytes(st)
                        files += 1
                        size += nbytes
                        if nbytes > 0:
                            _offer(top, self.top_files, nbytes, files, (entry.path, st.st_mtime))
        except (OSError, ValueError) as exc:
            listing_error = _error_reason(exc)
            errors.append(models.SkippedPath(path, listing_error))

        child_jobs: List[tuple] = []
        snapshot: Optional[models.ScanProgress] = None
        with self.lock:
            target = rec if rec is not None else absorber
            if rec is not None and listing_error is not None:
                rec.error = listing_error
            target.direct_size += size
            target.direct_files += files
            if rec is None:
                target.direct_dirs += 1  # a folded folder counts once under its ancestor
            self.dirs_scanned += 1
            self.files_scanned += files
            self.bytes_scanned += size
            for nbytes, _seq, (file_path, mtime) in top:
                if len(self.top_heap) < self.top_files or nbytes > self.top_heap[0][0]:
                    self.seq += 1
                    _offer(
                        self.top_heap,
                        self.top_files,
                        nbytes,
                        self.seq,
                        models.FileEntry(path=file_path, size=nbytes, modified=mtime),
                    )
            for item in errors:
                self.error_count += 1
                self._add_skip(item)
            for item in skipped_now:
                self._add_skip(item)
            for sub_path, sub_name in subdirs:
                if depth + 1 <= self.max_depth:
                    child = _Dir(sub_path, sub_name, rec, self.max_children)
                    rec.pending += 1  # the child record holds one unit on its parent
                    child_jobs.append((sub_path, depth + 1, child, child))
                else:
                    absorber.pending += 1  # the folded folder holds one unit on the ancestor
                    child_jobs.append((sub_path, depth + 1, None, absorber))
            now = time.monotonic()
            if now - self.last_emit >= _PROGRESS_INTERVAL:
                self.last_emit = now
                snapshot = models.ScanProgress(
                    root=self.root,
                    current_path=path,
                    dirs_scanned=self.dirs_scanned,
                    files_scanned=self.files_scanned,
                    bytes_scanned=self.bytes_scanned,
                )
        if snapshot is not None and self.progress is not None:
            try:
                self.progress(snapshot)
            except Exception:  # a failing UI callback must not stop the scan
                pass
        for sub_path, sub_depth, sub_rec, sub_absorber in child_jobs:
            self.pool.submit(self.work, sub_path, sub_depth, sub_rec, sub_absorber)


def _root_problem(root: str) -> Optional[Tuple[str, bool]]:
    """Return (reason, counts_as_error) when the root cannot be walked, otherwise None."""
    try:
        st = os.lstat(root)
    except (OSError, ValueError) as exc:
        return _error_reason(exc), True
    reason = _skip_reason(root, st)
    if reason is not None:
        return reason, False
    if not stat.S_ISDIR(st.st_mode):
        return "not a folder", True
    return None


def scan(
    root: str,
    *,
    max_depth: int = 8,
    max_children: int = 200,
    top_files: int = 500,
    progress: Optional[Callable[[models.ScanProgress], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> models.ScanResult:
    """Scan a folder tree and return exact sizes, a pruned tree and the largest files.

    Folders below max_depth are folded into their ancestor at max_depth. Each node
    lists at most max_children children, largest first; the rest become one summary
    row with an empty path. Links are recorded in `skipped` and not followed.
    `dir_count` counts the folders below the root. Cancelling returns partial totals.
    The progress callback runs on worker threads.
    """
    started = time.monotonic()
    name = os.path.basename(root.rstrip("\\/")) or root
    problem = _root_problem(root)
    if problem is not None:
        reason, is_error = problem
        return models.ScanResult(
            root=root,
            root_node=models.ScanNode(path=root, name=name, error=reason),
            skipped=[models.SkippedPath(root, reason)],
            error_count=1 if is_error else 0,
            elapsed_seconds=time.monotonic() - started,
        )
    run = _ScanRun(
        root,
        max(0, int(max_depth)),
        max(1, int(max_children)),
        max(0, int(top_files)),
        progress,
        cancel,
    )
    top_rec = _Dir(root, name, None, run.max_children)
    with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="scan") as pool:
        run.pool = pool
        pool.submit(run.work, root, 0, top_rec, top_rec)
        run.done.wait()
    node = run.root_node
    with run.lock:
        largest = [entry[2] for entry in sorted(run.top_heap, key=lambda entry: (-entry[0], entry[1]))]
        skipped = list(run.skipped)
        error_count = run.error_count
        cancelled = run.cancelled
    return models.ScanResult(
        root=root,
        root_node=node,
        total_size=node.size,
        file_count=node.file_count,
        dir_count=node.dir_count,
        largest_files=largest,
        skipped=skipped,
        error_count=error_count,
        cancelled=cancelled,
        elapsed_seconds=time.monotonic() - started,
    )


def measure_path(path: str, *, cancel: Optional[threading.Event] = None) -> Tuple[int, int]:
    """Return (bytes, file_count) for a file or a folder tree.

    Links and reparse points are not followed. Missing paths and errors count as zero.
    """
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return 0, 0
    if _skip_reason(path, st) is not None:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return _file_bytes(st), 1
    total = 0
    count = 0
    pending = [path]
    while pending:
        if cancel is not None and cancel.is_set():
            break
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        child = entry.stat(follow_symlinks=False)
                    except (OSError, ValueError):
                        continue
                    if _skip_reason(entry.path, child) is not None:
                        continue
                    if stat.S_ISDIR(child.st_mode):
                        pending.append(entry.path)
                    else:
                        total += _file_bytes(child)
                        count += 1
        except (OSError, ValueError):
            continue
    return total, count
