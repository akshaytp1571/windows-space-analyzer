"""Find files with identical content, grouped by size and then by two SHA-256 hashes."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import time
from collections.abc import Callable
from typing import NamedTuple

from space_analyzer import system
from space_analyzer.models import DuplicateGroup

_HEAD_BYTES = 64 * 1024  # bytes hashed in the first pass
_CHUNK_BYTES = 1024 * 1024  # read size when hashing a whole file
_PROGRESS_INTERVAL = 0.25  # seconds between progress calls, so at most 4 per second
_REPARSE_POINT = 0x400  # symlinks, junctions and other reparse points
_OFFLINE = 0x1000  # cloud-only file
_RECALL_ON_DATA_ACCESS = 0x400000  # cloud file whose content downloads when it is opened
_CLOUD_ONLY = _OFFLINE | _RECALL_ON_DATA_ACCESS


class _Candidate(NamedTuple):
    """A file that passed the walk checks, with what grouping and ordering need."""

    path: str
    size: int
    mtime: float


class _Reporter:
    """Passes folder names to the progress callback at most 4 times per second."""

    def __init__(self, callback: Callable[[str], None] | None) -> None:
        self._callback = callback
        self._last = float("-inf")

    def __call__(self, folder: str) -> None:
        """Report a folder when the throttle interval has passed."""
        if self._callback is None:
            return
        now = time.monotonic()
        if now - self._last >= _PROGRESS_INTERVAL:
            self._last = now
            self._callback(folder)


def find_duplicates(
    root: str,
    *,
    min_size: int = 1_048_576,
    max_groups: int = 500,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[DuplicateGroup]:
    """Return groups of identical files under root, most reclaimable space first.

    Each group has two or more copies, oldest modified first, and at most max_groups
    groups are returned. Files smaller than min_size are skipped. Empty files are never
    grouped because they free no space. Hard links to one file count as one copy.
    Links, cloud-only files and protected paths are skipped. Cancelling returns the
    groups confirmed so far.
    """
    if not root or _cancelled(cancel) or system.is_link(root) or not os.path.isdir(root):
        return []
    protected = system.protected_roots()
    allowlist = system.system_allowlist()
    if system.is_protected_path(root, protected, allowlist):
        return []
    report = _Reporter(progress)
    by_size = _walk(root, max(1, int(min_size)), protected, allowlist, report, cancel)
    if _cancelled(cancel):
        return []
    groups = _confirm(by_size, report, cancel)
    groups.sort(key=lambda group: (-group.reclaimable_bytes, group.paths[0]))
    return groups[: max(0, int(max_groups))]


def _walk(
    root: str,
    min_bytes: int,
    protected: list[str],
    allowlist: list[str],
    report: _Reporter,
    cancel: threading.Event | None,
) -> dict[int, list[_Candidate]]:
    """Collect candidate files under root, keyed by size. Folders are walked with a stack."""
    by_size: dict[int, list[_Candidate]] = {}
    seen: set[tuple[int, int]] = set()
    pending = [root]
    while pending and not _cancelled(cancel):
        folder = pending.pop()
        report(folder)
        try:
            with os.scandir(folder) as iterator:
                entries = list(iterator)
        except OSError:
            continue  # unreadable folder: skipped silently
        for entry in entries:
            if _cancelled(cancel):
                break
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue  # unreadable entry: skipped silently
            if stat.S_ISDIR(st.st_mode):
                if _has_reparse(st) or system.is_link(entry.path):
                    continue
                if not system.is_protected_path(entry.path, protected, allowlist):
                    pending.append(entry.path)
                continue
            if not _is_candidate(st, min_bytes):
                continue
            if system.is_protected_path(entry.path, protected, allowlist):
                continue
            identity = _link_identity(entry.path, st)
            if identity is not None:
                if identity in seen:
                    continue  # another name for a file that is already listed
                seen.add(identity)
            by_size.setdefault(st.st_size, []).append(
                _Candidate(entry.path, st.st_size, st.st_mtime)
            )
    return by_size


def _confirm(
    by_size: dict[int, list[_Candidate]],
    report: _Reporter,
    cancel: threading.Event | None,
) -> list[DuplicateGroup]:
    """Split each size group by a 64 KB hash, then by a whole-file hash. Largest first."""
    groups: list[DuplicateGroup] = []
    for size in sorted(by_size, reverse=True):
        candidates = by_size[size]
        if len(candidates) < 2:
            continue
        heads = _bucket(candidates, _HEAD_BYTES, report, cancel)
        if _cancelled(cancel):
            return groups  # first-pass buckets are not confirmed, so they are not returned
        for same_head in heads.values():
            if len(same_head) < 2:
                continue
            groups.extend(_make_groups(size, _bucket(same_head, None, report, cancel)))
            if _cancelled(cancel):
                return groups  # keeps the copies whose whole-file hash has finished
    return groups


def _bucket(
    candidates: list[_Candidate],
    limit: int | None,
    report: _Reporter,
    cancel: threading.Event | None,
) -> dict[str, list[_Candidate]]:
    """Group candidates by the digest of their first `limit` bytes (all bytes when None).

    Stops early when cancelled. Files that cannot be read are left out.
    """
    buckets: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        if _cancelled(cancel):
            break
        report(os.path.dirname(candidate.path))
        digest = _digest(candidate.path, limit)
        if digest is not None:
            buckets.setdefault(digest, []).append(candidate)
    return buckets


def _make_groups(size: int, buckets: dict[str, list[_Candidate]]) -> list[DuplicateGroup]:
    """Turn buckets of equal whole-file digests into groups of two or more copies."""
    groups: list[DuplicateGroup] = []
    for digest, members in buckets.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda candidate: (candidate.mtime, candidate.path))
        groups.append(
            DuplicateGroup(digest=digest, size=size, paths=[item.path for item in ordered])
        )
    return groups


def _digest(path: str, limit: int | None) -> str | None:
    """Return the SHA-256 hex digest of the first `limit` bytes (all bytes when None).

    The file is opened for reading only. Returns None when it cannot be read.
    """
    hasher = hashlib.sha256()
    left = limit
    try:
        with open(path, "rb") as handle:
            while left is None or left > 0:
                want = _CHUNK_BYTES if left is None else min(_CHUNK_BYTES, left)
                chunk = handle.read(want)
                if not chunk:
                    break
                hasher.update(chunk)
                if left is not None:
                    left -= len(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def _link_identity(path: str, st: os.stat_result) -> tuple[int, int] | None:
    """Return (volume, file index) for a file that has more than one name, otherwise None.

    Hard links share one identity, so only one of their names may be listed. The entry's
    own stat is used when it reports link counts. Otherwise the file is asked directly.
    """
    links, index = st.st_nlink, st.st_ino
    if not links or not index:  # not reported for this entry, so ask the file itself
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        links, index = st.st_nlink, st.st_ino
    if links <= 1 or not index:
        return None
    return st.st_dev, index


def _is_candidate(st: os.stat_result, min_bytes: int) -> bool:
    """True for a regular file of at least min_bytes that is not a link or cloud-only."""
    return (
        stat.S_ISREG(st.st_mode)
        and st.st_size >= min_bytes
        and not _has_reparse(st)
        and not _is_cloud_only(st)
    )


def _has_reparse(st: os.stat_result) -> bool:
    """True when the entry is a symlink, junction or other reparse point."""
    # Windows-only: not runnable in this sandbox (the attribute exists only on Windows).
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _is_cloud_only(st: os.stat_result) -> bool:
    """True for files whose content lives in the cloud. Reading them would download them."""
    # Windows-only: not runnable in this sandbox (the attributes exist only on Windows).
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY)


def _cancelled(cancel: threading.Event | None) -> bool:
    """True when the caller has asked the search to stop."""
    return cancel is not None and cancel.is_set()
