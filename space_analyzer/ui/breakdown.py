"""File-type breakdown: group the largest files by kind so the page can show what fills a drive.

This works only from `ScanResult.largest_files`, which the scanner already collects.
It never walks the disk again, so opening the page costs nothing and cannot be slow.

A "kind" is the filename extension, folded to lower case and mapped to a short
friendly name for the extensions people actually recognise. Everything else is
grouped under its own extension, and files with no extension are grouped together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from space_analyzer.models import FileEntry

# Extensions that deserve a readable label, grouped so the legend reads like a
# sentence rather than a list of dot-suffixes.
_FRIENDLY = {
    "video": ("mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "ts"),
    "image": ("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "raw", "psd"),
    "audio": ("mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "aiff"),
    "archive": ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "cab", "tgz"),
    "installer": ("msi", "exe", "msix", "appx", "dmg", "pkg", "deb", "rpm"),
    "document": ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "rtf", "txt", "epub"),
    "code": ("py", "js", "ts", "java", "c", "cpp", "h", "cs", "go", "rs", "rb", "php", "json"),
    "database": ("db", "sqlite", "mdb", "accdb", "mdf", "bak", "dump"),
    "disk image": ("vhd", "vhdx", "vmdk", "qcow2", "img"),
    "log": ("log", "etl", "dmp", "evtx"),
}

# Extension to friendly label, built once from the table above.
_LABELS = {ext: label for label, extensions in _FRIENDLY.items() for ext in extensions}

NO_EXTENSION = "no extension"


@dataclass(frozen=True)
class TypeGroup:
    """Every file of one kind, with its total size."""

    label: str
    size: int
    count: int


@dataclass(frozen=True)
class Breakdown:
    """The whole picture: groups largest first, and the total they add up to."""

    groups: List[TypeGroup]
    total: int

    @property
    def share_of(self) -> dict[str, float]:
        """Each group's fraction of the total, keyed by label, as 0..1."""
        if self.total <= 0:
            return {group.label: 0.0 for group in self.groups}
        return {group.label: group.size / self.total for group in self.groups}

    @property
    def biggest(self) -> TypeGroup | None:
        """The largest group, or None when there is nothing to show."""
        return self.groups[0] if self.groups else None


def classify(path: str) -> str:
    """Return the friendly kind for a file path.

    The extension is taken from the last dot in the file name, lower-cased, and
    mapped through the table. An unknown extension keeps its own name, so a drive
    full of `.xyz` files shows `.xyz` rather than hiding behind "other".
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name or name.startswith(".") and name.count(".") == 1:
        return NO_EXTENSION
    extension = name.rsplit(".", 1)[-1].strip().lower()
    if not extension or len(extension) > 8:
        # A very long "extension" is almost always part of a name, not a real type.
        return NO_EXTENSION
    return _LABELS.get(extension, f".{extension}")


def breakdown(files: Iterable[FileEntry], *, limit: int = 6) -> Breakdown:
    """Group these files by kind, largest total first.

    Only the first `limit` groups are returned; the rest are folded into a final
    "other" group so the total still accounts for every file.
    """
    totals: dict[str, list[int]] = {}
    total = 0
    for entry in files:
        label = classify(entry.path)
        bucket = totals.setdefault(label, [0, 0])
        bucket[0] += max(0, entry.size)
        bucket[1] += 1
        total += max(0, entry.size)

    ordered = sorted(
        ((label, size, count) for label, (size, count) in totals.items()),
        key=lambda one: (-one[1], one[0].casefold()),
    )
    if limit > 0 and len(ordered) > limit:
        head = ordered[: limit - 1]
        tail = ordered[limit - 1:]
        rest_size = sum(size for _label, size, _count in tail)
        rest_count = sum(count for _label, _size, count in tail)
        ordered = head + [("other", rest_size, rest_count)]

    groups = [
        TypeGroup(label=label, size=size, count=count) for label, size, count in ordered
    ]
    return Breakdown(groups=groups, total=total)


def segments(data: Breakdown, colors: Sequence[str]) -> list[tuple[str, float, str]]:
    """Turn a breakdown into (label, value, colour) triples for a SegmentBar."""
    result: list[tuple[str, float, str]] = []
    for index, group in enumerate(data.groups):
        color = colors[index % len(colors)] if colors else "#888888"
        result.append((group.label, float(group.size), color))
    return result


def summary_line(data: Breakdown) -> str:
    """A one-line summary for the panel caption."""
    if not data.groups or data.total <= 0:
        return "No files to break down."
    biggest = data.biggest
    if biggest is None:
        return "No files to break down."
    share = int(round(100 * biggest.size / data.total))
    return (
        f"{len(data.groups)} kind(s) — {biggest.label} takes {share}% of the listed size."
    )