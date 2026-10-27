"""Overview page: drive usage, a background scan of every fixed drive and the folder tree."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from space_analyzer import history, scanner, system, util
from space_analyzer.models import DriveInfo, ScanNode, ScanProgress, ScanResult
from space_analyzer.ui import icons, theme
from space_analyzer.ui.state import AppState
from space_analyzer.ui.widgets import (
    EmptyState,
    LegendRow,
    PageHeader,
    PageScroll,
    Panel,
    Readout,
    SegmentBar,
    TickGauge,
    rule,
    set_role,
    split_bytes,
)
from space_analyzer.ui.workers import Worker

_LOG = logging.getLogger(__name__)

_STOP_WAIT_MS = 5000
_PATH_LIMIT = 74
_ADMIN_NOTE = "some folders need administrator rights to read"
_NUMERIC_COLUMNS = (1, 2, 3, 4)
_NAME_COLUMN = 0
_SIZE_COLUMN = 1
_SHARE_COLUMN = 2
_FILES_COLUMN = 3
_FOLDERS_COLUMN = 4

_SEG_READ = "#c8f751"
_SEG_UNREAD = "#ffb84d"
_SEG_FREE = "#4a5563"


def _scan_drives(
    drives: list[str],
    progress: Callable[[ScanProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[ScanResult]:
    """Scan each drive in turn and return the results. No new drive starts once cancel is set."""
    results: list[ScanResult] = []
    for drive in drives:
        if cancel is not None and cancel.is_set():
            break
        results.append(scanner.scan(drive, progress=progress, cancel=cancel))
    return results


def _progress_text(message: object) -> str:
    """Describe a scan in progress as one line of status text."""
    if not isinstance(message, ScanProgress):
        return str(message)
    return (
        f"SCANNING {message.root}   {util.format_count(message.files_scanned)} files   "
        f"{util.format_bytes(message.bytes_scanned)}   {_shorten(message.current_path)}"
    )


def _shorten(path: str) -> str:
    """Keep the end of a long path so the status line stays a readable width."""
    if len(path) <= _PATH_LIMIT:
        return path
    return "…" + path[-(_PATH_LIMIT - 1):]


def _node_of(item: QTreeWidgetItem | None) -> ScanNode | None:
    """Return the ScanNode stored on a tree row, or None for placeholders and missing rows."""
    if item is None:
        return None
    node = item.data(0, Qt.ItemDataRole.UserRole)
    return node if isinstance(node, ScanNode) else None


def _ordered_children(node: ScanNode) -> list[ScanNode]:
    """Return sub-folders largest first, with the summary row (empty path) last."""
    folders = sorted(
        (child for child in node.children if child.path),
        key=lambda child: child.size,
        reverse=True,
    )
    summaries = [child for child in node.children if not child.path]
    return folders + summaries


def _placeholder() -> QTreeWidgetItem:
    """Return the stand-in child that gives a folder its expand arrow until it is opened."""
    item = QTreeWidgetItem(["…"])
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item


def _record_history(result: ScanResult) -> None:
    """Append a summary of a finished scan to the stored history."""
    try:
        info = system.drive_info(result.root)
        history.append(
            history.make_snapshot(
                result, free_bytes=info.free, capacity_bytes=info.total
            )
        )
    except Exception:  # a history write must never break a finished scan
        _LOG.exception("Could not record the scan history for %s", result.root)


class OverviewPage(QWidget):
    """Drive usage, one background scan of every fixed drive, and the folder tree of the selected drive."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._cancel_requested = False
        self._share_base = 0

        self._scan_button = QPushButton("Scan all drives")
        self._scan_button.setIcon(icons.icon("radar", "#10130a", 15))
        self._scan_button.setProperty("primary", "true")
        self._scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_button.clicked.connect(self._start_scan)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setProperty("ghost", "true")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel_scan)

        self._status = set_role(QLabel("IDLE — nothing scanned yet"), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._drive_combo = QComboBox()
        self._drive_combo.setMinimumWidth(96)
        self._drive_combo.addItems(system.fixed_drives())

        self._gauge = TickGauge(height=58)
        self._read_capacity = Readout("Capacity", color=theme.TEXT)
        self._read_used = Readout("Used", color=theme.SIGNAL)
        self._read_free = Readout("Free", color=theme.SIGNAL)
        self._read_scanned = Readout("Scanned", color=theme.COOL)
        self._read_unread = Readout("Not read", color=theme.WARN)

        self._segment = SegmentBar(height=20)
        self._legend_read = LegendRow("Files the scan could read", "—", _SEG_READ)
        self._legend_unread = LegendRow("System areas it could not read", "—", _SEG_UNREAD)
        self._legend_free = LegendRow("Free space", "—", _SEG_FREE)

        self._scan_label = set_role(QLabel(), "muted")
        self._scan_label.setWordWrap(True)
        self._errors_label = set_role(QLabel(), "muted")
        self._errors_label.setWordWrap(True)
        self._errors_label.setVisible(False)

        self._open_button = QPushButton("Open")
        self._open_button.setIcon(icons.icon("arrow", theme.TEXT_MUTED, 14))
        self._open_button.setProperty("ghost", "true")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_selected)
        self._tree = self._build_tree()
        self._empty = EmptyState(
            "NO SCAN YET",
            "Run a scan to measure what uses the space on this drive.",
            glyph="radar",
        )

        self._build_layout()
        self._drive_combo.currentIndexChanged.connect(self._refresh)
        self._state.scans_changed.connect(self._refresh)
        self._refresh()

    def shutdown(self) -> None:
        """Cancel the running scan and wait up to 5 seconds for its thread to stop."""
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ------------------------------------------------------------------ layout

    def _build_tree(self) -> QTreeWidget:
        """Create the folder tree with its columns, lazy expansion and selection handling."""
        tree = QTreeWidget()
        tree.setHeaderLabels(["NAME", "SIZE", "SHARE", "FILES", "FOLDERS"])
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.setUniformRowHeights(True)
        tree.setIndentation(16)
        tree.setAnimated(True)
        header = tree.header()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.setSectionResizeMode(_NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        for column in _NUMERIC_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        tree.itemExpanded.connect(self._on_item_expanded)
        tree.currentItemChanged.connect(self._update_open_button)
        return tree

    def _build_layout(self) -> None:
        """Arrange the header, the action row, the drive panel and the folder panel."""
        header = PageHeader(
            "01 / drive telemetry",
            "Overview",
            "What fills your drives. The scan only reads; nothing is changed until you confirm a cleanup.",
        )

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(self._scan_button)
        actions.addWidget(self._cancel_button)
        actions.addSpacing(4)
        actions.addWidget(self._status, 1)

        drive_panel = Panel(
            title="Drive",
            glyph="gauge",
            caption="select a drive to read its usage",
            margins=(16, 14, 16, 14),
        )
        chooser = QHBoxLayout()
        chooser.setSpacing(8)
        chooser.addWidget(set_role(QLabel("VOLUME"), "caption"))
        chooser.addWidget(self._drive_combo)
        chooser.addStretch(1)
        drive_panel.add_layout(chooser)
        drive_panel.add(self._gauge)

        reads = QGridLayout()
        reads.setHorizontalSpacing(0)
        reads.setVerticalSpacing(0)
        for index, read in enumerate(
            (
                self._read_capacity,
                self._read_used,
                self._read_free,
                self._read_scanned,
                self._read_unread,
            )
        ):
            reads.addWidget(read, 0, index * 2)
            if index:
                reads.addWidget(rule(horizontal=False), 0, index * 2 - 1)
        drive_panel.add_layout(reads)
        drive_panel.add(rule())

        accounting = QVBoxLayout()
        accounting.setSpacing(4)
        accounting.addWidget(set_role(QLabel("SPACE ACCOUNTING"), "caption"))
        accounting.addWidget(self._segment)
        accounting.addSpacing(2)
        accounting.addWidget(self._legend_read)
        accounting.addWidget(self._legend_unread)
        accounting.addWidget(self._legend_free)
        drive_panel.add_layout(accounting)
        drive_panel.add(self._scan_label)
        drive_panel.add(self._errors_label)

        folder_panel = Panel(
            title="Folders",
            glyph="folder",
            caption="largest first — expand a folder to see inside",
            margins=(12, 10, 12, 10),
        )
        folder_panel.header().add_action(self._open_button)
        folder_panel.add(self._empty, 1)
        folder_panel.add(self._tree, 1)
        # A floor keeps the tree at a usable height. Without it the layout takes the
        # whole shortfall out of this panel, so the tree shows a few rows while the
        # page scrollbar is also present.
        folder_panel.setMinimumHeight(300)
        self._empty.setVisible(False)

        scroll = PageScroll(spacing=13)
        layout = scroll.body()
        layout.addWidget(header)
        layout.addLayout(actions)
        layout.addWidget(drive_panel)
        layout.addWidget(folder_panel, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------- scan

    def _start_scan(self) -> None:
        """Scan every fixed drive in one background worker."""
        if self._worker is not None:
            if self._worker.isRunning():
                return
            self._worker.wait()  # finished, but the thread may still be winding down
        drives = system.fixed_drives()
        if not drives:
            self._status.setText("No fixed drives were found.")
            return
        self._cancel_requested = False
        worker = Worker(_scan_drives, drives)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_scan_succeeded)
        worker.errored.connect(self._on_scan_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._set_running(True)
        self._status.setText(f"SCANNING — starting {len(drives)} drive(s)")
        worker.start()

    def _cancel_scan(self) -> None:
        """Ask the running scan to stop at its next check."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        self._cancel_requested = True
        worker.cancel()
        self._cancel_button.setEnabled(False)
        self._status.setText("CANCELLING — waiting for the current folder")

    def _set_running(self, running: bool) -> None:
        """Enable Cancel only while a scan runs, and Scan all drives only when none runs."""
        self._scan_button.setEnabled(not running)
        self._cancel_button.setEnabled(running)

    def _on_progress(self, message: object) -> None:
        """Show the latest scan progress in the status line."""
        self._status.setText(_progress_text(message))

    def _on_scan_succeeded(self, results: list[ScanResult]) -> None:
        """Store each drive's result, and record it in the history.

        The history entry is what lets the Watch page show what changed between two
        scans, so it is written only for a finished, uncancelled scan: a partial scan
        would look like folders shrank.
        """
        for result in results:
            self._state.set_scan(result)
            if not result.cancelled:
                _record_history(result)
        if self._cancel_requested or any(result.cancelled for result in results):
            self._status.setText("CANCELLED — the figures shown are partial")
            return
        elapsed = sum(result.elapsed_seconds for result in results)
        self._status.setText(f"COMPLETE — {elapsed:.0f} s for {len(results)} drive(s)")

    def _on_scan_failed(self, message: str) -> None:
        """Report a failed scan. Earlier results stay as they were."""
        self._status.setText(f"FAILED — {message}")

    def _on_worker_finished(self) -> None:
        """Re-enable Scan all drives once the worker thread has ended."""
        self._set_running(False)

    # ------------------------------------------------------------------ render

    def _refresh(self) -> None:
        """Redraw the gauge, the readouts and the folder tree for the selected drive."""
        root = self._drive_combo.currentText()
        if not root:
            self._show_unscanned("No fixed drives were found.")
            return
        info = system.drive_info(root)
        self._show_usage(info)
        result = self._state.scans.get(root)
        if result is None:
            self._show_unscanned("Not scanned yet — click Scan all drives.")
        else:
            self._show_scan(result, info)
        self._update_open_button()

    def _show_usage(self, info: DriveInfo) -> None:
        """Show the used share on the gauge, plus the five readouts."""
        percent = info.used * 100.0 / info.total if info.total > 0 else 0.0
        volume = self._drive_combo.currentText() or "drive"
        self._gauge.set_values(
            percent,
            caption=f"{volume} utilisation",
            reading=f"{percent:.0f}% used",
        )
        total_number, total_unit = split_bytes(info.total)
        used_number, used_unit = split_bytes(info.used)
        free_number, free_unit = split_bytes(info.free)
        self._read_capacity.set_value(total_number, total_unit, "drive capacity")
        self._read_used.set_value(used_number, used_unit, f"{percent:.1f}% of the drive")
        self._read_free.set_value(free_number, free_unit, "available to write")
        self._read_used.set_color(theme.usage_color(percent))

    def _show_unscanned(self, message: str) -> None:
        """Show that the selected drive has no scan yet and clear the folder tree."""
        self._scan_label.setText(message)
        # The status line reflects what is on screen, not only what the last scan did.
        # Scans can also arrive from state (another page, or a restored session), so
        # leaving it on stale text would contradict the figures shown below it.
        if not self._cancel_button.isEnabled():
            self._status.setText("IDLE — nothing scanned yet")
        self._read_scanned.set_value("—", "", "run a scan")
        self._read_unread.set_value("—", "", "")
        self._errors_label.setVisible(False)
        self._tree.clear()
        self._tree.setVisible(False)
        self._empty.set_text("NO SCAN YET", message)
        self._empty.setVisible(True)
        self._segment.set_segments([])
        for legend in (self._legend_read, self._legend_unread, self._legend_free):
            legend.set_value("—")

    def _show_scan(self, result: ScanResult, info: DriveInfo) -> None:
        """Show the scan figures for a drive and fill the folder tree with its largest folders."""
        self._share_base = info.total
        scanned_number, scanned_unit = split_bytes(result.total_size)
        self._read_scanned.set_value(
            scanned_number,
            scanned_unit,
            f"{util.format_count(result.file_count)} files",
        )
        unaccounted = max(0, info.used - result.total_size)
        unread_number, unread_unit = split_bytes(unaccounted)
        self._read_unread.set_value(
            unread_number,
            unread_unit,
            "system areas or unreadable files",
        )

        scanned_line = (
            f"{util.format_count(result.file_count)} files  ·  "
            f"{util.format_count(result.dir_count)} folders  ·  "
            f"read in {result.elapsed_seconds:.0f} s"
        )
        if result.cancelled:
            scanned_line += "  ·  partial, the scan was cancelled"
        self._scan_label.setText(scanned_line)
        # A scan already in state (loaded, or made on another page) still counts as
        # scanned, so the status line must not claim nothing has been scanned.
        if not self._cancel_button.isEnabled():
            self._status.setText("SCANNED — figures below are from the last scan")

        self._segment.set_segments(
            [
                ("read", result.total_size, _SEG_READ),
                ("unread", unaccounted, _SEG_UNREAD),
                ("free", info.free, _SEG_FREE),
            ]
        )
        self._legend_read.set_value(util.format_bytes(result.total_size))
        self._legend_unread.set_value(util.format_bytes(unaccounted))
        self._legend_free.set_value(util.format_bytes(info.free))

        if result.error_count > 0:
            text = f"{util.format_count(result.error_count)} items could not be read"
            if not self._state.admin:
                text += f" — {_ADMIN_NOTE}"
            self._errors_label.setText(text)
            self._errors_label.setVisible(True)
        else:
            self._errors_label.setVisible(False)

        self._tree.clear()
        for node in _ordered_children(result.root_node):
            self._tree.addTopLevelItem(self._make_item(node))
        has_rows = self._tree.topLevelItemCount() > 0
        self._tree.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        if not has_rows:
            self._empty.set_text("NOTHING TO SHOW", "This drive reported no readable folders.")

    def _share_text(self, size: int) -> str:
        """Return a size as a share of the drive, such as '12.5%'."""
        if self._share_base <= 0:
            return "—"
        return f"{size * 100.0 / self._share_base:.1f}%"

    def _make_item(self, node: ScanNode) -> QTreeWidgetItem:
        """Build the row for one folder. A folder with sub-folders gets a placeholder so it can expand."""
        item = QTreeWidgetItem(
            [
                node.name,
                util.format_bytes(node.size),
                self._share_text(node.size),
                util.format_count(node.file_count),
                util.format_count(node.dir_count),
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, node)
        for column in _NUMERIC_COLUMNS:
            item.setTextAlignment(
                column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        item.setFont(_SIZE_COLUMN, theme.mono_font(9, bold=True))
        item.setFont(_SHARE_COLUMN, theme.mono_font(9))
        item.setFont(_FILES_COLUMN, theme.mono_font(9))
        item.setFont(_FOLDERS_COLUMN, theme.mono_font(9))

        if not node.path:
            # The synthetic "N smaller folders" summary row is muted and never opened.
            muted = QBrush(QColor(theme.TEXT_DIM))
            for column in range(5):
                item.setForeground(column, muted)
            font = item.font(_NAME_COLUMN)
            font.setItalic(True)
            item.setFont(_NAME_COLUMN, font)
        else:
            share = node.size / self._share_base if self._share_base > 0 else 0.0
            item.setForeground(_SIZE_COLUMN, QBrush(QColor(theme.SIGNAL)))
            item.setForeground(_SHARE_COLUMN, QBrush(QColor(theme.TEXT_MUTED)))
            item.setForeground(_FILES_COLUMN, QBrush(QColor(theme.TEXT_MUTED)))
            item.setForeground(_FOLDERS_COLUMN, QBrush(QColor(theme.TEXT_DIM)))
            name_color = theme.TEXT if share >= 0.20 else theme.TEXT_MUTED
            item.setForeground(_NAME_COLUMN, QBrush(QColor(name_color)))
        if node.error:
            item.setToolTip(0, node.error)
        if node.children:
            item.addChild(_placeholder())
        return item

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """Replace the placeholder with the real sub-folders the first time a row is opened."""
        node = _node_of(item)
        if node is None or item.childCount() != 1 or _node_of(item.child(0)) is not None:
            return
        while item.childCount():
            item.removeChild(item.child(0))
        for child in _ordered_children(node):
            item.addChild(self._make_item(child))

    def _update_open_button(self) -> None:
        """Enable Open only when the selected row is a real folder."""
        node = _node_of(self._tree.currentItem())
        self._open_button.setEnabled(node is not None and bool(node.path))

    def _open_selected(self) -> None:
        """Show the selected folder in Explorer. The summary row has no path and is skipped."""
        node = _node_of(self._tree.currentItem())
        if node is not None and node.path:
            system.open_in_explorer(node.path)
