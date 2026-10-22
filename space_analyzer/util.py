"""Small formatting helpers shared by the engine and the UI."""

from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(n: Optional[float]) -> str:
    """Return a human-readable size such as '1.5 GB'. None gives 'unknown'."""
    if n is None:
        return "unknown"
    value = float(max(0, n))
    for unit in _UNITS[:-1]:
        if value < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"


def format_count(n: int) -> str:
    """Return a count with thousands separators, e.g. '12,345'."""
    return f"{n:,}"


def format_timestamp(ts: Optional[float]) -> str:
    """Return a local date and time such as '2026-10-20 14:03'."""
    if not ts:
        return "unknown"
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def format_age(ts: Optional[float], now: Optional[float] = None) -> str:
    """Return a relative age such as 'today', '3 days ago' or '2 years ago'."""
    if not ts:
        return "unknown"
    current = time.time() if now is None else now
    days = int(max(0.0, current - ts) // 86400)
    if days < 1:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"
