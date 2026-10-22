"""Large files page: the biggest files of a scanned drive, and a confirmed Recycle Bin move."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from space_analyzer import cleaner, system, util
from space_analyzer.models import ActionReport, FileEntry
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
_NO_SCAN_HINT = "Run a scan on the Overview page first."
_HEADERS = ("NAME", "FOLDER", "SIZE", "MODIFIED", "AGE")
_NAME_COLUMN = 0
_FOLDER_COLUMN = 1
_SIZE_COLUMN = 2
_MODIFIED_COLUMN = 3
_AGE_COLUMN = 4
_FOLDER_WIDTH = 380
_OLD_FILE_DAYS = 365


class _SortItem(QTableWidgetItem):
    """A table cell that sorts by sort_key (bytes, timestamp or text), not by its display text."""

    def __init__(self, text: str, sort_key: int | float | str) -> None:
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _SortItem):
            return self.sort_key < other.sort_key
        return self.text() < other.text()


class LargeFilesPage(QWidget):
    """Lists the largest files of each scanned drive and moves chosen files to the Recycle Bin."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._busy = False
        self._pending_paths: list[str] = []
        self._moved: set[str] = set()
        self._sizes: dict[str, int] = {}
        self._modified: dict[str, float] = {}

        self._drive_combo = QComboBox()
        self._drive_combo.setMinimumWidth(96)
        self._size_combo = QComboBox()
        self._size_combo.setMinimumWidth(104)
        for label, size in logic.LARGE_FILE_SIZES:
            self._size_combo.addItem(label, userData=size)
        self._find = QLineEdit()
        self._find.setPlaceholderText("filter by part of a name or folder")
        self._find.setClearButtonEnabled(True)
        self._find.addAction(
            icons.icon("search", theme.TEXT_DIM, 14), QLineEdit.ActionPosition.LeadingPosition
        )
        self._find.setMinimumWidth(200)

        self._read_listed = Readout("Listed", color=theme.TEXT)
        self._read_total = Readout("Total size", color=theme.SIGNAL)
        self._read_old = Readout("Unchanged 1y+", color=theme.WARN)

        self._table = self._build_table()
        self._empty = EmptyState(_NO_SCAN_HINT, "The list comes from the drive scans.", glyph="file")
        self._totals = set_role(QLabel(), "muted")
        self._totals.setTextFormat(Qt.TextFormat.PlainText)
        self._totals.setWordWrap(True)
        self._totals.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._status = set_role(QLabel(), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._open_button = QPushButton("Open location")
        self._open_button.setIcon(icons.icon("arrow", theme.TEXT_MUTED, 14))
        self._open_button.setProperty("ghost", "true")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_selected)
        self._move_button = QPushButton("Move to Recycle Bin")
        self._move_button.setIcon(icons.icon("trash", theme.ALERT, 14))
        self._move_button.setProperty("danger", "true")
        self._move_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._move_button.setEnabled(False)
        self._move_button.clicked.connect(self._move_selected)

        self._build_layout()
        self._drive_combo.currentIndexChanged.connect(self._refresh)
        self._size_combo.currentIndexChanged.connect(self._refresh)
        self._find.textChanged.connect(self._refresh)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self._state.scans_changed.connect(self._on_scans_changed)
        self._on_scans_changed()

    def shutdown(self) -> None:
        """Cancel a running move and wait up to 5 seconds for its thread to stop."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ------------------------------------------------------------------ layout

    def _build_table(self) -> QTableWidget:
        """Create the file table with its columns, selection rules, widths and sorting."""
        table = QTableWidget()
        table.setColumnCount(len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(_NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_FOLDER_COLUMN, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(_FOLDER_COLUMN, _FOLDER_WIDTH)
        for column in (_SIZE_COLUMN, _MODIFIED_COLUMN, _AGE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.setSortingEnabled(True)
        header.setSortIndicator(_SIZE_COLUMN, Qt.SortOrder.DescendingOrder)
        return table

    def _build_layout(self) -> None:
        """Arrange the header, the filters, the readouts, the table and the action row."""
        header = PageHeader(
            "03 / file inventory",
            "Large files",
            "The biggest files on a scanned drive. Move one to the Recycle Bin only if you no "
            "longer need it — nothing moves until you confirm.",
        )

        filters = QHBoxLayout()
        filters.setSpacing(9)
        filters.addWidget(set_role(QLabel("VOLUME"), "caption"))
        filters.addWidget(self._drive_combo)
        filters.addSpacing(8)
        filters.addWidget(set_role(QLabel("MIN SIZE"), "caption"))
        filters.addWidget(self._size_combo)
        filters.addSpacing(8)
        filters.addWidget(set_role(QLabel("FILTER"), "caption"))
        filters.addWidget(self._find, 1)

        reads = QHBoxLayout()
        reads.setSpacing(0)
        for index, read in enumerate((self._read_listed, self._read_total, self._read_old)):
            if index:
                reads.addWidget(rule(horizontal=False))
            reads.addWidget(read, 1)
        reads.addStretch(1)

        panel = Panel(
            title="Files",
            glyph="file",
            caption="largest first — click a column heading to sort",
            margins=(12, 10, 12, 10),
        )
        panel.add(self._totals)
        panel.add(self._empty, 1)
        panel.add(self._table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(self._status, 1)
        actions.addWidget(self._open_button)
        actions.addWidget(self._move_button)

        scroll = PageScroll(spacing=12)
        layout = scroll.body()
        layout.addWidget(header)
        layout.addLayout(filters)
        layout.addLayout(reads)
        layout.addWidget(panel, 1)
        layout.addLayout(actions)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------ state

    def _on_scans_changed(self) -> None:
        """Rebuild the drive list and the table. A new scan replaces the list, so old moves clear."""
        self._moved.clear()
        self._repopulate_drives()
        self._refresh()

    def _repopulate_drives(self) -> None:
        """Fill the drive list from the scans, keeping the current drive when it is still scanned."""
        current = self._drive_combo.currentText()
        drives = sorted(self._state.scans)
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        self._drive_combo.addItems(drives)
        if drives:
            self._drive_combo.setCurrentIndex(drives.index(current) if current in drives else 0)
        self._drive_combo.blockSignals(False)

    def _refresh(self) -> None:
        """Redraw the table for the chosen drive, minimum size and name filter."""
        has_scans = bool(self._state.scans)
        for control in (self._drive_combo, self._size_combo, self._find, self._table):
            control.setEnabled(has_scans)
        scan = self._state.scans.get(self._drive_combo.currentText()) if has_scans else None
        rows: list[FileEntry] = []
        if scan is not None:
            protected = system.protected_roots()
            allowlist = system.system_allowlist()
            is_hidden = lambda path: (  # noqa: E731
                path in self._moved
                or system.is_protected_path(path, roots=protected, allowlist=allowlist)
            )
            rows = logic.filter_large_files(
                scan.largest_files,
                min_size=self._size_combo.currentData() or 0,
                text=self._find.text(),
                is_hidden=is_hidden,
            )
        self._fill_table(rows)
        if has_scans:
            self._update_totals()
        else:
            self._totals.clear()
            self._reset_reads()
        has_rows = bool(rows)
        self._table.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        if has_scans and not has_rows:
            self._empty.set_text("NO MATCH", "Try a smaller minimum size, or clear the filter.")
        elif not has_scans:
            self._empty.set_text(_NO_SCAN_HINT, "The list comes from the drive scans.")
        self._update_buttons()

    def _fill_table(self, rows: list[FileEntry]) -> None:
        """Replace the table rows with these files, keeping sizes for the totals and the confirmation."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        self._sizes = {}
        self._modified = {}
        for row, entry in enumerate(rows):
            self._fill_row(row, entry)
            self._sizes[entry.path] = entry.size
            self._modified[entry.path] = entry.modified
        self._table.setSortingEnabled(True)

    def _fill_row(self, row: int, entry: FileEntry) -> None:
        """Write one file into a row. The name cell holds the full path, which the actions read back."""
        name = os.path.basename(entry.path)
        folder = os.path.dirname(entry.path)
        age = util.format_age(entry.modified)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        name_cell = _SortItem(name, name.lower())
        name_cell.setData(Qt.ItemDataRole.UserRole, entry.path)
        size_cell = _SortItem(util.format_bytes(entry.size), entry.size)
        size_cell.setTextAlignment(right)
        size_cell.setForeground(QBrush(QColor(theme.SIGNAL)))
        size_cell.setFont(theme.mono_font(9, bold=True))
        age_cell = _SortItem(age, age.lower())
        age_cell.setTextAlignment(right)
        age_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))
        folder_cell = _SortItem(folder, folder.lower())
        folder_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))
        modified_cell = _SortItem(util.format_timestamp(entry.modified), entry.modified)
        modified_cell.setTextAlignment(right)
        modified_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))
        modified_cell.setFont(theme.mono_font(9))

        self._table.setItem(row, _NAME_COLUMN, name_cell)
        self._table.setItem(row, _FOLDER_COLUMN, folder_cell)
        self._table.setItem(row, _SIZE_COLUMN, size_cell)
        self._table.setItem(row, _MODIFIED_COLUMN, modified_cell)
        self._table.setItem(row, _AGE_COLUMN, age_cell)

    def _reset_reads(self) -> None:
        """Clear the three readouts when there is no scan."""
        self._read_listed.set_value("—", "", "no scan yet")
        self._read_total.set_value("—", "", "")
        self._read_old.set_value("—", "", "")

    def _update_totals(self) -> None:
        """Show how many files are listed, their total size and how many are old."""
        total = sum(self._sizes.values())
        self._totals.setText(
            f"{len(self._sizes)} files  ·  protected system files are hidden  ·  "
            "double-click a row to show it in Explorer"
        )
        number, unit = split_bytes(total)
        self._read_listed.set_value(str(len(self._sizes)), "files", "in the current list")
        self._read_total.set_value(number, unit, "if every file here were removed")
        now = time.time()
        old = [
            path
            for path, modified in self._modified.items()
            if modified and (now - modified) / 86400 >= _OLD_FILE_DAYS
        ]
        self._read_old.set_value(str(len(old)), "files", "not touched for a year")

    def _update_buttons(self) -> None:
        """Enable Open location for one selected file, and Move for any selection while idle."""
        has_scans = bool(self._state.scans)
        selected = len(self._selected_rows())
        self._open_button.setEnabled(has_scans and selected == 1)
        self._move_button.setEnabled(has_scans and selected > 0 and not self._busy)

    def _selected_rows(self) -> list[int]:
        """Row numbers with at least one selected cell, in table order."""
        return sorted({index.row() for index in self._table.selectedIndexes()})

    def _path_at(self, row: int) -> str | None:
        """Full path stored on the name cell of a row, or None when the row holds no file."""
        item = self._table.item(row, _NAME_COLUMN)
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return path if isinstance(path, str) else None

    def _selected_paths(self) -> list[str]:
        """Paths of the selected files, in table order."""
        paths = [self._path_at(row) for row in self._selected_rows()]
        return [path for path in paths if path is not None]

    def _open_selected(self) -> None:
        """Show the one selected file in Explorer."""
        rows = self._selected_rows()
        if len(rows) == 1:
            self._open_row(rows[0])

    def _open_row(self, row: int) -> None:
        """Show the file in one row in Explorer. Explorer selects the file and does not open it."""
        path = self._path_at(row)
        if path is not None:
            system.open_in_explorer(path)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """Double-clicking any cell of a row shows its file in Explorer."""
        self._open_row(row)

    def _move_selected(self) -> None:
        """Confirm the selected files, then move them to the Recycle Bin in a worker."""
        paths = self._selected_paths()
        if self._busy or not paths:
            return
        sizes = [self._sizes.get(path, 0) for path in paths]
        details = [f"{path} ({util.format_bytes(size)})" for path, size in zip(paths, sizes)]
        if any(logic.is_appdata_path(path) for path in paths):
            details.append(logic.APPDATA_WARNING)
        text = (
            f"Move {len(paths)} file(s), {util.format_bytes(sum(sizes))} in total, to the "
            "Recycle Bin? You can restore them from the Recycle Bin until you empty it."
        )
        if not dialogs.confirm(self, "Move files to the Recycle Bin?", text, details):
            return
        self._start_move(paths, self._drive_combo.currentText())

    def _start_move(self, paths: list[str], drive_root: str) -> None:
        """Start the Recycle Bin move in a worker. The table changes only when the worker reports back."""
        if self._worker is not None:
            self._worker.wait()  # the previous run has finished, so its thread exits at once
        self._pending_paths = paths
        worker = Worker(cleaner.delete_to_recycle, paths, [drive_root])
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_move_succeeded)
        worker.errored.connect(self._on_move_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._busy = True
        self._update_buttons()
        self._status.setText("MOVING — sending files to the Recycle Bin")
        worker.start()

    def _on_progress(self, message: object) -> None:
        """Show the path the move is working on in the status line."""
        self._status.setText(f"MOVING  ·  {logic.shorten_path(str(message))}")

    def _on_move_succeeded(self, report: ActionReport) -> None:
        """Show the report, drop the files that moved, and say that space returns after emptying."""
        dialogs.show_reports(self, "Moved to Recycle Bin", [report])
        moved = set(logic.paths_not_failed(self._pending_paths, [report]))
        self._pending_paths = []
        self._moved.update(moved)
        for row in range(self._table.rowCount() - 1, -1, -1):
            path = self._path_at(row)
            if path is not None and path in moved:
                self._table.removeRow(row)
                self._sizes.pop(path, None)
                self._modified.pop(path, None)
        self._update_totals()
        self._status.setText("DONE — space is freed after you empty the Recycle Bin")

    def _on_move_failed(self, message: str) -> None:
        """Report a failed move. The rows stay in the table, so the user can try again."""
        self._pending_paths = []
        self._status.setText(f"FAILED — {message}")

    def _on_worker_finished(self) -> None:
        """Re-enable the buttons once the worker thread has finished."""
        self._busy = False
        self._update_buttons()
