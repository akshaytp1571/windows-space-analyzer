"""Duplicates page: find identical files on a drive, keep one copy, move the rest to the Bin."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from space_analyzer import cleaner, system, util
from space_analyzer import duplicates as duplicate_finder
from space_analyzer.models import ActionReport, DuplicateGroup, Risk
from space_analyzer.ui import dialogs, icons, theme
from space_analyzer.ui import page_logic as logic
from space_analyzer.ui.state import AppState
from space_analyzer.ui.widgets import (
    EmptyState,
    PageScroll,
    PageHeader,
    Panel,
    Readout,
    rule,
    set_role,
    split_bytes,
)
from space_analyzer.ui.workers import Worker

_STOP_WAIT_MS = 5000
_NAME_COLUMN = 0
_SIZE_COLUMN = 1
_MODIFIED_COLUMN = 2
_FOLDER_COLUMN = 3
_SUBTITLE = (
    "Identical copies of the same file. Keep one copy and move the others to the Recycle Bin. "
    "A group can never lose every copy, and nothing moves until you confirm."
)
_WARNING = "A whole-drive search can take several minutes. Files are compared, never changed."
_FREED_NOTE = "space returns only after you empty the Recycle Bin"
_HINT = "Choose a drive and click Find duplicates."


def _drive_index(drives: list[str], wanted: str) -> int:
    """Return the index of the drive equal to wanted (case ignored), or 0 when none matches."""
    target = os.path.normcase(wanted)
    for index, drive in enumerate(drives):
        if os.path.normcase(drive) == target:
            return index
    return 0


def _modified_time(path: str) -> float:
    """Return the file's last-modified time, or 0 when it cannot be read, which shows as unknown."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _group_of(item: QTreeWidgetItem | None) -> DuplicateGroup | None:
    """Return the DuplicateGroup stored on a group row. None for copy rows and missing rows."""
    if item is None:
        return None
    group = item.data(0, Qt.ItemDataRole.UserRole)
    return group if isinstance(group, DuplicateGroup) else None


def _path_of(item: QTreeWidgetItem | None) -> str | None:
    """Return the file path stored on a copy row. None for group rows and missing rows."""
    if item is None:
        return None
    path = item.data(0, Qt.ItemDataRole.UserRole)
    return path if isinstance(path, str) else None


def _make_copy_item(path: str, size: int, *, oldest: bool) -> QTreeWidgetItem:
    """Build the checkable row for one copy. The first copy of a group is marked as the oldest."""
    name = os.path.basename(path) + ("   ·  keep" if oldest else "")
    item = QTreeWidgetItem(
        [
            name,
            util.format_bytes(size),
            util.format_timestamp(_modified_time(path)),
            os.path.dirname(path),
        ]
    )
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(0, Qt.CheckState.Unchecked)
    item.setData(0, Qt.ItemDataRole.UserRole, path)
    item.setTextAlignment(_SIZE_COLUMN, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setFont(_SIZE_COLUMN, theme.mono_font(9, bold=True))
    item.setFont(_MODIFIED_COLUMN, theme.mono_font(9))
    item.setForeground(_SIZE_COLUMN, QBrush(QColor(theme.SIGNAL)))
    item.setForeground(_FOLDER_COLUMN, QBrush(QColor(theme.TEXT_DIM)))
    item.setForeground(_MODIFIED_COLUMN, QBrush(QColor(theme.TEXT_MUTED)))
    if oldest:
        item.setForeground(_NAME_COLUMN, QBrush(QColor(theme.RISK_COLOR[Risk.SAFE])))
    return item


def _make_group_item(group: DuplicateGroup) -> QTreeWidgetItem:
    """Build the bold, non-checkable heading row of a group, with one child per copy."""
    heading = QTreeWidgetItem([logic.group_summary(group), "", "", ""])
    heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
    heading.setData(0, Qt.ItemDataRole.UserRole, group)
    font = heading.font(_NAME_COLUMN)
    font.setBold(True)
    font.setPointSize(9)
    heading.setFont(_NAME_COLUMN, font)
    heading.setForeground(_NAME_COLUMN, QBrush(QColor(theme.SIGNAL)))
    for index, path in enumerate(group.paths):
        heading.addChild(_make_copy_item(path, group.size, oldest=index == 0))
    return heading


class DuplicatesPage(QWidget):
    """Lists groups of identical files. The user checks the copies to remove and moves them."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._busy = False
        self._searching = False
        self._cancel_requested = False
        self._updating = False
        self._root = ""
        self._move_paths: list[str] = []

        drives = system.fixed_drives()
        self._drive_combo = QComboBox()
        self._drive_combo.setMinimumWidth(96)
        self._drive_combo.addItems(drives)
        if drives:
            self._drive_combo.setCurrentIndex(_drive_index(drives, system.system_drive()))
        self._size_combo = QComboBox()
        self._size_combo.setMinimumWidth(104)
        for label, size in logic.DUPLICATE_SIZES:
            self._size_combo.addItem(label, userData=size)

        self._find_button = QPushButton("Find duplicates")
        self._find_button.setIcon(icons.icon("copy", "#10130a", 15))
        self._find_button.setProperty("primary", "true")
        self._find_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._find_button.clicked.connect(self._start_search)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setProperty("ghost", "true")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self._cancel_search)

        first_line = _HINT if drives else "No fixed drives were found."
        self._status = set_role(QLabel(first_line), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._selection_label = set_role(QLabel(), "muted")
        self._selection_label.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_label.setWordWrap(True)
        self._warning = set_role(QLabel(_WARNING), "muted")
        self._warning.setWordWrap(True)

        self._read_groups = Readout("Groups", color=theme.TEXT)
        self._read_reclaim = Readout("Reclaimable", color=theme.SIGNAL)
        self._read_checked = Readout("Ticked", color=theme.COOL)

        self._keep_oldest_button = QPushButton("Keep oldest")
        self._keep_oldest_button.setProperty("ghost", "true")
        self._keep_oldest_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_oldest_button.clicked.connect(self._keep_oldest)
        self._clear_button = QPushButton("Clear")
        self._clear_button.setProperty("ghost", "true")
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.clicked.connect(self._clear_selection)
        self._open_button = QPushButton("Open location")
        self._open_button.setIcon(icons.icon("arrow", theme.TEXT_MUTED, 14))
        self._open_button.setProperty("ghost", "true")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.clicked.connect(self._open_selected)
        self._move_button = QPushButton("Move selected to Recycle Bin")
        self._move_button.setIcon(icons.icon("trash", theme.ALERT, 14))
        self._move_button.setProperty("danger", "true")
        self._move_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._move_button.clicked.connect(self._move_selected)

        self._tree = self._build_tree()
        self._empty = EmptyState(
            _HINT,
            "Only identical files larger than the minimum size are listed.",
            glyph="copy",
        )
        self._build_layout()
        self._refresh_controls()

    def shutdown(self) -> None:
        """Cancel the running search or move and wait up to 5 seconds for its thread to stop."""
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ------------------------------------------------------------------ layout

    def _build_tree(self) -> QTreeWidget:
        """Create the results tree with its columns, check-box handling and selection handling."""
        tree = QTreeWidget()
        tree.setHeaderLabels(["FILE", "SIZE", "MODIFIED", "FOLDER"])
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.setUniformRowHeights(True)
        tree.setIndentation(14)
        tree.setAnimated(True)
        header = tree.header()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.setSectionResizeMode(_NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        for column in (_SIZE_COLUMN, _MODIFIED_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_FOLDER_COLUMN, QHeaderView.ResizeMode.Stretch)
        tree.itemChanged.connect(self._on_item_changed)
        tree.currentItemChanged.connect(self._update_open_button)
        return tree

    def _build_layout(self) -> None:
        """Arrange the header, the controls, the readouts, the tree and the action row."""
        header = PageHeader("04 / duplicate detection", "Duplicates", _SUBTITLE)

        controls = QHBoxLayout()
        controls.setSpacing(9)
        controls.addWidget(set_role(QLabel("VOLUME"), "caption"))
        controls.addWidget(self._drive_combo)
        controls.addSpacing(8)
        controls.addWidget(set_role(QLabel("MIN SIZE"), "caption"))
        controls.addWidget(self._size_combo)
        controls.addStretch(1)
        controls.addWidget(self._find_button)
        controls.addWidget(self._cancel_button)

        reads = QHBoxLayout()
        reads.setSpacing(0)
        for index, read in enumerate((self._read_groups, self._read_reclaim, self._read_checked)):
            if index:
                reads.addWidget(rule(horizontal=False))
            reads.addWidget(read, 1)
        reads.addStretch(1)

        panel = Panel(
            title="Identical files",
            glyph="copy",
            caption="one heading per group — tick the copies you no longer need",
            margins=(12, 10, 12, 10),
        )
        panel.add(self._warning)
        panel.add(self._empty, 1)
        panel.add(self._tree, 1)
        self._empty.setVisible(False)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(self._keep_oldest_button)
        actions.addWidget(self._clear_button)
        actions.addWidget(self._open_button)
        actions.addStretch(1)
        actions.addWidget(self._move_button)

        scroll = PageScroll(spacing=12)
        layout = scroll.body()
        layout.addWidget(header)
        layout.addLayout(controls)
        layout.addLayout(reads)
        layout.addWidget(self._status)
        layout.addWidget(panel, 1)
        layout.addWidget(self._selection_label)
        layout.addLayout(actions)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ----------------------------------------------------------------- running

    def _launch(self, worker: Worker) -> None:
        """Keep a reference to the worker, lock the controls it conflicts with, and start it."""
        if self._worker is not None:
            self._worker.wait()  # the previous thread has finished, so this returns at once
        self._worker = worker
        self._busy = True
        self._cancel_requested = False
        worker.finished.connect(self._on_worker_finished)
        self._refresh_controls()
        worker.start()

    def _start_search(self) -> None:
        """Search the chosen drive for duplicate files in a background worker."""
        if self._busy:
            return
        root = self._drive_combo.currentText()
        if not root:
            self._status.setText("No fixed drives were found.")
            return
        self._show_groups([])
        self._root = root
        worker = Worker(
            duplicate_finder.find_duplicates, root, min_size=self._size_combo.currentData()
        )
        worker.progressed.connect(self._on_search_progress)
        worker.succeeded.connect(self._on_search_succeeded)
        worker.errored.connect(self._on_search_failed)
        self._searching = True
        self._status.setText(f"SEARCHING — starting on {root}")
        self._launch(worker)

    def _cancel_search(self) -> None:
        """Ask the running search to stop. It keeps the groups confirmed so far."""
        if self._worker is None or not (self._busy and self._searching):
            return
        self._cancel_requested = True
        self._worker.cancel()
        self._status.setText("CANCELLING — keeping the groups confirmed so far")
        self._refresh_controls()

    def _on_search_progress(self, message: object) -> None:
        """Show the folder being checked, shortened to fit on one line."""
        self._status.setText(f"SEARCHING  ·  {logic.shorten_path(str(message))}")

    def _on_search_succeeded(self, groups: list[DuplicateGroup]) -> None:
        """Show the groups found. A cancelled search says so, because its results may be partial."""
        self._show_groups(groups)
        count = len(groups)
        freed = util.format_bytes(sum(group.reclaimable_bytes for group in groups))
        if self._cancel_requested:
            if count:
                self._status.setText(
                    f"CANCELLED — {count} groups so far, {freed} can be freed"
                )
            else:
                self._status.setText("CANCELLED — no groups confirmed")
        elif count:
            self._status.setText(f"COMPLETE — {count} groups, {freed} can be freed")
        else:
            self._status.setText("COMPLETE — no duplicate files were found")

    def _on_search_failed(self, message: str) -> None:
        """Report a failed search. The results stay empty."""
        self._status.setText(f"FAILED — {message}")

    def _on_worker_finished(self) -> None:
        """Unlock the controls once the worker thread has ended."""
        self._busy = False
        self._searching = False
        self._refresh_controls()

    def _show_groups(self, groups: list[DuplicateGroup]) -> None:
        """Replace the tree with one expanded row per group. Built under the _updating guard."""
        self._tree.clear()
        self._updating = True
        try:
            for group in groups:
                group_item = _make_group_item(group)
                self._tree.addTopLevelItem(group_item)
                group_item.setExpanded(True)
        finally:
            self._updating = False
        has_rows = bool(groups)
        self._tree.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        self._read_groups.set_value(
            str(len(groups)) if groups else "—",
            "groups",
            f"{sum(len(g.paths) for g in groups)} copies" if groups else "no search yet",
        )
        reclaim_number, reclaim_unit = split_bytes(
            sum(g.reclaimable_bytes for g in groups) if groups else None
        )
        self._read_reclaim.set_value(
            reclaim_number,
            reclaim_unit,
            "if every extra copy goes" if groups else "",
        )
        self._refresh_controls()
        # The status line follows what is listed, so a result set that arrived without
        # a search this session does not sit under the "choose a drive" prompt.
        if not self._busy:
            self._status.setText(
                f"LISTED — {len(groups)} group(s), tick the copies you no longer need"
                if groups
                else _HINT
            )

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Refuse a check that would leave every copy of a group checked, then refresh."""
        if self._updating or column != 0 or item.parent() is None:
            return
        group_item = item.parent()
        checked = [
            group_item.child(index).checkState(0) == Qt.CheckState.Checked
            for index in range(group_item.childCount())
        ]
        if logic.keep_one_blocked(checked):
            self._updating = True
            try:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            finally:
                self._updating = False
            self._status.setText(logic.KEEP_ONE_MESSAGE)
        self._refresh_controls()

    def _set_checks(self, flags_for: Callable[[int], list[bool]]) -> None:
        """Set each copy's check box from flags_for(copies in its group), without re-checking."""
        self._updating = True
        try:
            for index in range(self._tree.topLevelItemCount()):
                group_item = self._tree.topLevelItem(index)
                for child_index, checked in enumerate(flags_for(group_item.childCount())):
                    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    group_item.child(child_index).setCheckState(0, state)
        finally:
            self._updating = False
        self._refresh_controls()

    def _keep_oldest(self) -> None:
        """Check every copy except the oldest (first) in each group."""
        self._set_checks(logic.keep_oldest_flags)

    def _clear_selection(self) -> None:
        """Uncheck every copy."""
        self._set_checks(lambda copies: [False] * copies)

    def _checked_copies(self) -> list[tuple[str, int]]:
        """Return (path, group size) for every checked copy, in tree order."""
        copies: list[tuple[str, int]] = []
        for index in range(self._tree.topLevelItemCount()):
            group_item = self._tree.topLevelItem(index)
            group = _group_of(group_item)
            if group is None:
                continue
            for child_index in range(group_item.childCount()):
                child = group_item.child(child_index)
                path = _path_of(child)
                if path is not None and child.checkState(0) == Qt.CheckState.Checked:
                    copies.append((path, group.size))
        return copies

    def _refresh_controls(self) -> None:
        """Enable each control for the current state and show the selection summary."""
        idle = not self._busy
        copies = self._checked_copies()
        has_groups = self._tree.topLevelItemCount() > 0
        self._drive_combo.setEnabled(idle)
        self._size_combo.setEnabled(idle)
        self._tree.setEnabled(idle)
        self._find_button.setEnabled(idle and self._drive_combo.count() > 0)
        self._cancel_button.setEnabled(self._busy and self._searching and not self._cancel_requested)
        self._keep_oldest_button.setEnabled(idle and has_groups)
        self._clear_button.setEnabled(idle and has_groups)
        self._move_button.setEnabled(idle and bool(copies))
        total = sum(size for _path, size in copies)
        self._selection_label.setText(
            f"{logic.selection_summary(len(copies), total)}  ·  {_FREED_NOTE}"
        )
        checked_number, checked_unit = split_bytes(total if copies else None)
        self._read_checked.set_value(
            checked_number,
            checked_unit,
            f"{len(copies)} copies ticked" if copies else "nothing ticked",
        )
        self._update_open_button()

    def _update_open_button(self) -> None:
        """Enable Open location only when a copy row is selected and nothing is running."""
        has_copy = _path_of(self._tree.currentItem()) is not None
        self._open_button.setEnabled(not self._busy and has_copy)

    def _open_selected(self) -> None:
        """Show the selected copy in Explorer. The file is selected, never opened."""
        path = _path_of(self._tree.currentItem())
        if path is not None:
            system.open_in_explorer(path)

    def _move_selected(self) -> None:
        """Confirm, then move the checked copies to the Recycle Bin in a worker."""
        copies = self._checked_copies()
        if self._busy or not copies:
            return
        paths = [path for path, _size in copies]
        details = [f"{path} ({util.format_bytes(size)})" for path, size in copies]
        if any(logic.is_appdata_path(path) for path in paths):
            details.append(logic.APPDATA_WARNING)
        total = util.format_bytes(sum(size for _path, size in copies))
        text = (
            f"Move {len(paths)} copy/copies, {total} in total, to the Recycle Bin? "
            "At least one copy of each file stays. You can restore them until you empty the bin."
        )
        if not dialogs.confirm(self, "Move copies to the Recycle Bin?", text, details):
            return
        self._move_paths = paths
        worker = Worker(cleaner.delete_to_recycle, paths, [self._root])
        worker.progressed.connect(self._on_move_progress)
        worker.succeeded.connect(self._on_move_succeeded)
        worker.errored.connect(self._on_move_failed)
        self._searching = False
        self._status.setText("MOVING — sending copies to the Recycle Bin")
        self._launch(worker)

    def _on_move_progress(self, message: object) -> None:
        """Show the engine's progress line, shortened to fit."""
        self._status.setText(f"MOVING  ·  {logic.shorten_path(str(message))}")

    def _on_move_succeeded(self, report: ActionReport) -> None:
        """Show what happened, remove the copies that moved, and keep the ones that failed."""
        dialogs.show_reports(self, "Moved to Recycle Bin", [report])
        moved = set(logic.paths_not_failed(self._move_paths, [report]))
        self._move_paths = []
        self._remove_rows(moved)
        self._status.setText("DONE — space is freed after you empty the Recycle Bin")

    def _on_move_failed(self, message: str) -> None:
        """Report a failed move. The rows stay as they were."""
        self._move_paths = []
        self._status.setText(f"FAILED — {message}")

    def _remove_rows(self, moved: set[str]) -> None:
        """Remove the copies that moved. A group left with fewer than two copies is removed too."""
        self._updating = True
        try:
            for index in range(self._tree.topLevelItemCount() - 1, -1, -1):
                group_item = self._tree.topLevelItem(index)
                group = _group_of(group_item)
                if group is None:
                    continue
                for child_index in range(group_item.childCount() - 1, -1, -1):
                    if _path_of(group_item.child(child_index)) in moved:
                        group_item.takeChild(child_index)
                remaining = [path for path in group.paths if path not in moved]
                if len(remaining) < 2:
                    self._tree.takeTopLevelItem(index)
                    continue
                updated = DuplicateGroup(digest=group.digest, size=group.size, paths=remaining)
                group_item.setData(0, Qt.ItemDataRole.UserRole, updated)
                group_item.setText(0, logic.group_summary(updated))
        finally:
            self._updating = False
        self._refresh_controls()
