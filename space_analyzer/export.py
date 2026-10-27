"""Export scan and cleanup results to CSV and JSON.

Everything written here comes from data the app already holds in memory: nothing is
re-read from disk and nothing is sent anywhere. A report is only produced when the
user asks for one, and the caller chooses the path.

The CSV writer is careful about two things that break spreadsheets:

- A cell that starts with `=`, `+`, `-` or `@` is prefixed with a single quote.
  Excel and LibreOffice treat those as formulas, so a file named
  `=cmd|calc.xlsx` would run a command when the report is opened.
- Every cell is quoted by the writer itself, so a comma or a quote inside a path
  cannot shift the columns.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from space_analyzer.models import (
    ActionReport,
    CleanupItem,
    DuplicateGroup,
    ScanResult,
)

_CSV_ENCODING = "utf-8-sig"  # a BOM, or Excel opens UTF-8 as mojibake on Windows
_JSON_ENCODING = "utf-8"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Return `value` as text that a spreadsheet will not treat as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _rows_to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Build CSV text with a header row and every cell made spreadsheet-safe."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([safe_cell(cell) for cell in header])
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buffer.getvalue()


def _write_text(path: str, text: str, encoding: str) -> None:
    """Write text to `path`, creating the parent folder when it is missing."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(text)


def scan_to_csv(scan: ScanResult) -> str:
    """One row per folder in the scanned tree, plus a totals row."""
    header = ["Path", "Name", "Size (bytes)", "Size (human)", "Files", "Folders", "Error"]
    rows: list[list[Any]] = []
    for node in _walk(scan.root_node):
        rows.append(
            [
                node.path,
                node.name,
                node.size,
                _human(node.size),
                node.file_count,
                node.dir_count,
                node.error or "",
            ]
        )
    rows.append(
        [
            scan.root,
            "TOTAL",
            scan.total_size,
            _human(scan.total_size),
            scan.file_count,
            scan.dir_count,
            "",
        ]
    )
    return _rows_to_csv(header, rows)


def scan_to_json(scan: ScanResult) -> str:
    """The whole scan as JSON, including the folder tree and the largest files."""
    payload = {
        "kind": "scan",
        "generated": _now(),
        "root": scan.root,
        "total_size": scan.total_size,
        "total_size_human": _human(scan.total_size),
        "file_count": scan.file_count,
        "dir_count": scan.dir_count,
        "elapsed_seconds": round(scan.elapsed_seconds, 3),
        "cancelled": scan.cancelled,
        "error_count": scan.error_count,
        "tree": _node_dict(scan.root_node),
        "largest_files": [
            {
                "path": entry.path,
                "size": entry.size,
                "size_human": _human(entry.size),
                "modified": _timestamp(entry.modified),
            }
            for entry in scan.largest_files
        ],
        "skipped": [{"path": one.path, "reason": one.reason} for one in scan.skipped],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def items_to_csv(items: Sequence[CleanupItem]) -> str:
    """One row per cleanup suggestion."""
    header = [
        "Id", "Title", "Category", "Risk", "Size (bytes)", "Size (human)",
        "Files", "Requires admin", "Selected by default", "Regenerates", "Impact",
    ]
    rows = [
        [
            item.item_id,
            item.title,
            item.category.value,
            item.risk.value,
            item.size_bytes,
            _human(item.size_bytes),
            item.file_count,
            "yes" if item.requires_admin else "no",
            "yes" if item.default_selected else "no",
            item.regenerates,
            item.impact,
        ]
        for item in items
    ]
    return _rows_to_csv(header, rows)


def items_to_json(items: Sequence[CleanupItem]) -> str:
    """The cleanup suggestions as JSON, with the total they add up to."""
    payload = {
        "kind": "suggestions",
        "generated": _now(),
        "count": len(items),
        "total_bytes": sum(item.size_bytes for item in items),
        "total_human": _human(sum(item.size_bytes for item in items)),
        "items": [
            {
                "id": item.item_id,
                "title": item.title,
                "category": item.category.value,
                "risk": item.risk.value,
                "size_bytes": item.size_bytes,
                "size_human": _human(item.size_bytes),
                "file_count": item.file_count,
                "requires_admin": item.requires_admin,
                "default_selected": item.default_selected,
                "regenerates": item.regenerates,
                "description": item.description,
                "impact": item.impact,
                "warnings": list(item.warnings),
            }
            for item in items
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def reports_to_csv(reports: Sequence[ActionReport]) -> str:
    """One row per cleanup action that ran, with its failures listed."""
    header = [
        "Action", "Title", "Removed", "Freed (bytes)", "Freed (human)",
        "Failed", "Cancelled", "Dry run", "Needs restart", "Messages",
    ]
    rows = []
    for report in reports:
        failures = "; ".join(f"{one.path}: {one.reason}" for one in report.failed)
        rows.append(
            [
                report.action,
                report.title,
                report.removed_count,
                report.bytes_removed,
                _human(report.bytes_removed),
                failures,
                "yes" if report.cancelled else "no",
                "yes" if report.dry_run else "no",
                "yes" if report.requires_restart else "no",
                " | ".join(report.messages),
            ]
        )
    return _rows_to_csv(header, rows)


def reports_to_json(reports: Sequence[ActionReport]) -> str:
    """The cleanup reports as JSON."""
    payload = {
        "kind": "cleanup_report",
        "generated": _now(),
        "total_bytes_removed": sum(report.bytes_removed for report in reports),
        "total_items_removed": sum(report.removed_count for report in reports),
        "reports": [
            {
                "action": report.action,
                "title": report.title,
                "removed_count": report.removed_count,
                "bytes_removed": report.bytes_removed,
                "bytes_removed_human": _human(report.bytes_removed),
                "cancelled": report.cancelled,
                "dry_run": report.dry_run,
                "requires_restart": report.requires_restart,
                "messages": list(report.messages),
                "failed": [{"path": one.path, "reason": one.reason} for one in report.failed],
            }
            for report in reports
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def duplicates_to_csv(groups: Sequence[DuplicateGroup]) -> str:
    """One row per duplicate copy, with its group and the reclaimable space."""
    header = [
        "Group", "Copy", "Path", "Size (bytes)", "Size (human)", "Reclaimable (bytes)",
    ]
    rows: list[list[Any]] = []
    for index, group in enumerate(groups, start=1):
        for copy_index, path in enumerate(group.paths, start=1):
            rows.append(
                [
                    index,
                    copy_index,
                    path,
                    group.size,
                    _human(group.size),
                    group.reclaimable_bytes if copy_index > 1 else 0,
                ]
            )
    return _rows_to_csv(header, rows)


def duplicates_to_json(groups: Sequence[DuplicateGroup]) -> str:
    """The duplicate groups as JSON."""
    payload = {
        "kind": "duplicates",
        "generated": _now(),
        "group_count": len(groups),
        "reclaimable_bytes": sum(group.reclaimable_bytes for group in groups),
        "reclaimable_human": _human(sum(group.reclaimable_bytes for group in groups)),
        "groups": [
            {
                "digest": group.digest,
                "size_bytes": group.size,
                "size_human": _human(group.size),
                "copies": len(group.paths),
                "reclaimable_bytes": group.reclaimable_bytes,
                "paths": list(group.paths),
            }
            for group in groups
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def write_scan(path: str, scan: ScanResult) -> None:
    """Write a scan report, choosing the format from the file extension."""
    if _is_json(path):
        _write_text(path, scan_to_json(scan), _JSON_ENCODING)
    else:
        _write_text(path, scan_to_csv(scan), _CSV_ENCODING)


def write_items(path: str, items: Sequence[CleanupItem]) -> None:
    """Write the cleanup suggestions, choosing the format from the extension."""
    if _is_json(path):
        _write_text(path, items_to_json(items), _JSON_ENCODING)
    else:
        _write_text(path, items_to_csv(items), _CSV_ENCODING)


def write_reports(path: str, reports: Sequence[ActionReport]) -> None:
    """Write the cleanup reports, choosing the format from the extension."""
    if _is_json(path):
        _write_text(path, reports_to_json(reports), _JSON_ENCODING)
    else:
        _write_text(path, reports_to_csv(reports), _CSV_ENCODING)


def write_duplicates(path: str, groups: Sequence[DuplicateGroup]) -> None:
    """Write the duplicate groups, choosing the format from the extension."""
    if _is_json(path):
        _write_text(path, duplicates_to_json(groups), _JSON_ENCODING)
    else:
        _write_text(path, duplicates_to_csv(groups), _CSV_ENCODING)


def write_apps(path: str, rows: Iterable[Any]) -> None:
    """Write installed apps, choosing the format from the extension.

    `rows` are space_analyzer.apps.InstalledApp values. The writer lives here rather
    than on the page so every export goes through the same quoting and encoding.
    """
    apps_list = list(rows)
    if _is_json(path):
        payload = {
            "kind": "apps",
            "generated": _now(),
            "count": len(apps_list),
            "total_bytes": sum(app.size_bytes for app in apps_list),
            "apps": [
                {
                    "name": app.name,
                    "publisher": app.publisher,
                    "version": app.version,
                    "size_bytes": app.size_bytes,
                    "scope": app.scope,
                    "install_location": app.install_location,
                    "uninstall": app.uninstall_string,
                    "can_uninstall": app.can_uninstall,
                }
                for app in apps_list
            ],
        }
        _write_text(path, json.dumps(payload, indent=2, ensure_ascii=False), _JSON_ENCODING)
        return

    header = [
        "Name", "Publisher", "Version", "Size (bytes)", "Size (human)",
        "Scope", "Install location", "Can uninstall",
    ]
    rows_out = [
        [
            app.name,
            app.publisher,
            app.version,
            app.size_bytes,
            _human(app.size_bytes) if app.size_known else "",
            app.scope,
            app.install_location,
            "yes" if app.can_uninstall else "no",
        ]
        for app in apps_list
    ]
    _write_text(path, _rows_to_csv(header, rows_out), _CSV_ENCODING)


def default_name(kind: str, extension: str = "csv") -> str:
    """A file name such as `space-analyzer-scan-20261027-0930.csv`."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"space-analyzer-{kind}-{stamp}.{extension.lstrip('.')}"


def _is_json(path: str) -> bool:
    """True when the path asks for JSON."""
    return os.path.splitext(path)[1].lower() == ".json"


def _walk(node: Any) -> Iterable[Any]:
    """Yield a node and every node below it, depth first."""
    yield node
    for child in getattr(node, "children", []):
        yield from _walk(child)


def _node_dict(node: Any) -> dict[str, Any]:
    """A ScanNode as a JSON-ready dict, children included."""
    return {
        "path": node.path,
        "name": node.name,
        "size": node.size,
        "size_human": _human(node.size),
        "file_count": node.file_count,
        "dir_count": node.dir_count,
        "error": node.error,
        "children": [_node_dict(child) for child in node.children],
    }


def _human(count: int) -> str:
    """Bytes as a short human string, using the same units as the UI."""
    step = 1024.0
    value = float(max(0, count))
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < step or unit == "PB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} PB"


def _timestamp(seconds: float) -> str:
    """A POSIX timestamp as an ISO-8601 string in UTC."""
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _now() -> str:
    """The current time as an ISO-8601 string in UTC, for the report header."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")