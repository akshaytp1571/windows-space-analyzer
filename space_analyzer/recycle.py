"""Recycle Bin helpers: send paths to the bin, empty it, and report what it holds."""

from __future__ import annotations

import os
from typing import Callable

from space_analyzer import system
from space_analyzer.models import ActionFailure

_BATCH_SIZE = 50
_BIN_FOLDER = "$Recycle.Bin"
_SHERB_ALL = 0x7  # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND


def send_to_recycle_bin(paths: list[str]) -> tuple[list[str], list[ActionFailure]]:
    """Move paths to the Recycle Bin. Returns (sent, failed).

    Sends in batches. When a batch fails, its paths are retried one at a time. A path
    that has already left its place after a failed batch counts as sent.
    """
    if not paths:
        return [], []
    try:
        from send2trash import send2trash  # lazy, so the engine imports without send2trash
    except ImportError as exc:
        reason = f"send2trash is not available: {exc}"
        return [], [ActionFailure(path, reason) for path in paths]
    sent: list[str] = []
    failed: list[ActionFailure] = []
    for start in range(0, len(paths), _BATCH_SIZE):
        batch = list(paths[start:start + _BATCH_SIZE])
        try:
            send2trash(batch)
        except Exception:
            _retry_one_by_one(batch, send2trash, sent, failed)
        else:
            sent.extend(batch)
    return sent, failed


def empty_recycle_bin() -> bool:
    """Empty the Recycle Bin on every fixed drive. Returns True only when it is verified empty."""
    if not system.IS_WINDOWS:
        return False
    try:
        # Windows-only: not runnable in this sandbox
        import ctypes

        # The return value is not trusted. The bin is counted again below.
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, _SHERB_ALL)
    except Exception:
        return False
    _, items, unreadable = _survey()
    return items == 0 and unreadable == 0


def recycle_bin_info() -> tuple[int, int]:
    """Return (bytes, item count) of the Recycle Bins on all fixed drives. Errors are ignored.

    Items are the $R entries directly under each per-user folder. Links and junctions
    inside the bin are never followed.
    """
    total_bytes, items, _ = _survey()
    return total_bytes, items


def _retry_one_by_one(batch: list[str], send: Callable[[list[str]], object],
                      sent: list[str], failed: list[ActionFailure]) -> None:
    """Send each path of a failed batch alone and record the result of each."""
    for path in batch:
        if not os.path.lexists(path):
            sent.append(path)  # the failed batch already moved it
            continue
        try:
            send([path])
        except Exception as exc:
            failed.append(ActionFailure(path, _reason(exc)))
        else:
            sent.append(path)


def _survey() -> tuple[int, int, int]:
    """Return (bytes, $R item count, unreadable folder count) for every fixed drive."""
    total_bytes = 0
    items = 0
    unreadable = 0
    for drive in system.fixed_drives():
        bin_root = os.path.join(drive, _BIN_FOLDER)
        if not os.path.isdir(bin_root):
            continue
        user_dirs = _scandir(bin_root)
        if user_dirs is None:
            unreadable += 1
            continue
        for user_dir in user_dirs:
            if not _is_real_dir(user_dir):
                continue
            entries = _scandir(user_dir.path)
            if entries is None:
                unreadable += 1
                continue
            for entry in entries:
                if entry.name.upper().startswith("$R"):
                    items += 1
                size, bad = _entry_bytes(entry)
                total_bytes += size
                unreadable += bad
    return total_bytes, items, unreadable


def _scandir(folder: str) -> list[os.DirEntry] | None:
    """List a folder, or return None when it cannot be read."""
    try:
        with os.scandir(folder) as iterator:
            return list(iterator)
    except OSError:
        return None


def _is_real_dir(entry: os.DirEntry) -> bool:
    """True for a folder that is not a symlink, junction or other reparse point."""
    try:
        return entry.is_dir(follow_symlinks=False) and not system.is_link(entry.path)
    except OSError:
        return False


def _entry_bytes(entry: os.DirEntry) -> tuple[int, int]:
    """Return (bytes, unreadable count) under an entry, walking folders without following links."""
    if not _is_real_dir(entry):
        try:
            return entry.stat(follow_symlinks=False).st_size, 0
        except OSError:
            return 0, 1
    total = 0
    unreadable = 0
    pending = [entry.path]
    while pending:
        children = _scandir(pending.pop())
        if children is None:
            unreadable += 1
            continue
        for child in children:
            try:
                if _is_real_dir(child):
                    pending.append(child.path)
                else:
                    total += child.stat(follow_symlinks=False).st_size
            except OSError:
                unreadable += 1
    return total, unreadable


def _reason(exc: Exception) -> str:
    """Return the exception text, or its class name when the text is empty."""
    return str(exc).strip() or type(exc).__name__
