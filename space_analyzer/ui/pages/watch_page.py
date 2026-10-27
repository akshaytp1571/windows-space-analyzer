"""Watch page: folders you care about, their budgets, and what changed between scans.

Two panels share the page because they answer one question between them: "is this
folder getting bigger than I want?" The top panel holds the watches and their
budgets; the bottom panel shows what changed since the previous scan of a drive.
"""

from __future__ import annotations

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from space_analyzer import history, util, watches
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
_WATCH_HEADERS = ("FOLDER", "SIZE", "BUDGET", "USED", "STATE")
_WATCH_FOLDER = 0
_WATCH_SIZE = 1
_WATCH_BUDGET = 2
_WATCH_USED = 3
_WATCH_STATE = 4
_DELTA_HEADERS = ("FOLDER", "BEFORE", "AFTER", "CHANGE")
_DELTA_FOLDER = 0
_DELTA_BEFORE = 1
_DELTA_AFTER = 2
_DELTA_CHANGE = 3
_NO_WATCHES = "No folders watched yet — add one with Add folder."
_NO_HISTORY = "No previous scan — scan a drive twice to see what changed."


class _SortItem(QTableWidgetItem):
    """A cell that sorts by a real key rather than by its display text."""

    def __init__(self, text: str, sort_key: int | float | str) -> None:
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _SortItem):
            return self.sort_key < other.sort_key
        return self.text() < other.text()


class WatchPage(QWidget):
    """Holds the watched folders, their budgets, and the change view between scans."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._busy = False
        self._watches: list[watches.Watch] = []
        self._shown: list[watches.Watch] = []
        self._summary = watches.WatchSummary()

        self._folder = QLineEdit()
        self._folder.setPlaceholderText("paste or type a folder path")
        self._folder.setClearButtonEnabled(True)
        self._folder.setMinimumWidth(260)
        self._budget = QSpinBox()
        self._budget.setRange(0, 1_000_000)
        self._budget.setSuffix(" GB")
        self._budget.setSpecialValueText("no budget")
        self._budget.setMinimumWidth(120)

        self._read_count = Readout("Watched", color=theme.TEXT)
        self._read_total = Readout("Total size", color=theme.SIGNAL)
        self._read_over = Readout("Over budget", color=theme.WARN)

        self._watch_table = self._build_table(_WATCH_HEADERS, stretch=_WATCH_FOLDER)
        self._watch_empty = EmptyState(_NO_WATCHES, "A budget is optional.", glyph="radar")

        self._delta_drive = QComboBox()
        self._delta_drive.setMinimumWidth(96)
        self._delta_table = self._build_table(_DELTA_HEADERS, stretch=_DELTA_FOLDER)
        self._delta_empty = EmptyState(_NO_HISTORY, "The history keeps the last 60 scans.", glyph="clock")
        self._delta_note = set_role(QLabel(), "muted")
        self._delta_note.setTextFormat(Qt.TextFormat.PlainText)
        self._delta_note.setWordWrap(True)

        self._status = set_role(QLabel(), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._add_button = QPushButton("Add folder")
        self._add_button.setIcon(icons.icon("spark", theme.BG_DEEP, 14))
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.clicked.connect(self._add_watch)
        self._check_button = QPushButton("Check now")
        self._check_button.setIcon(icons.icon("radar", theme.BG_DEEP, 14))
        self._check_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_button.clicked.connect(self._start_check)
        self._remove_button = QPushButton("Remove")
        self._remove_button.setIcon(icons.icon("trash", theme.ALERT, 14))
        self._remove_button.setProperty("danger", "true")
        self._remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_button.setEnabled(False)
        self._remove_button.clicked.connect(self._remove_selected)

        self._build_layout()
        self._watch_table.itemSelectionChanged.connect(self._update_buttons)
        self._delta_drive.currentIndexChanged.connect(self._refresh_delta)
        self._state.scans_changed.connect(self._on_scans_changed)
        self._reload()
        self._repopulate_drives()
        self._refresh_delta()

    def shutdown(self) -> None:
        """Wait briefly for a running check to stop."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ------------------------------------------------------------------ layout

    def _build_table(self, headers: tuple[str, ...], *, stretch: int) -> QTableWidget:
        """Create one of the page's tables."""
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        for column in range(len(headers)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if column == stretch
                else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(column, mode)
        table.setSortingEnabled(True)
        return table

    def _build_layout(self) -> None:
        """Arrange the header, the add row, the readouts and the two panels."""
        header = PageHeader(
            "06 / watch and history",
            "Watch",
            "Folders you want to keep an eye on, with an optional size budget. Nothing here "
            "deletes or moves anything — a watch only reports.",
        )

        add_row = QHBoxLayout()
        add_row.setSpacing(9)
        add_row.addWidget(set_role(QLabel("FOLDER"), "caption"))
        add_row.addWidget(self._folder, 1)
        add_row.addSpacing(8)
        add_row.addWidget(set_role(QLabel("BUDGET"), "caption"))
        add_row.addWidget(self._budget)
        add_row.addSpacing(8)
        add_row.addWidget(self._add_button)

        reads = QHBoxLayout()
        reads.setSpacing(0)
        for index, read in enumerate((self._read_count, self._read_total, self._read_over)):
            if index:
                reads.addWidget(rule(horizontal=False))
            reads.addWidget(read, 1)
        reads.addStretch(1)

        watch_panel = Panel(
            title="Watched folders",
            glyph="radar",
            caption="measured on demand — nothing is measured in the background",
            margins=(12, 10, 12, 10),
        )
        watch_panel.add(self._watch_empty, 1)
        watch_panel.add(self._watch_table, 1)

        delta_panel = Panel(
            title="What changed",
            glyph="clock",
            caption="between the last two scans of the chosen drive",
            margins=(12, 10, 12, 10),
        )
        delta_panel.add(self._delta_note)
        delta_panel.add(self._delta_empty, 1)
        delta_panel.add(self._delta_table, 1)

        delta_head = QHBoxLayout()
        delta_head.setSpacing(9)
        delta_head.addWidget(set_role(QLabel("VOLUME"), "caption"))
        delta_head.addWidget(self._delta_drive)
        delta_head.addStretch(1)
        delta_head.addWidget(self._remove_button)
        delta_head.addWidget(self._check_button)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(self._status, 1)

        scroll = PageScroll(spacing=12)
        layout = scroll.body()
        layout.addWidget(header)
        layout.addLayout(add_row)
        layout.addLayout(reads)
        layout.addWidget(watch_panel, 1)
        layout.addLayout(delta_head)
        layout.addWidget(delta_panel, 1)
        layout.addLayout(actions)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------ watches

    def _reload(self) -> None:
        """Read the stored watches and redraw the table."""
        self._watches = watches.load()
        self._refresh_watches()

    def _refresh_watches(self) -> None:
        """Fill the watch table and the readouts from the stored watches."""
        self._shown = list(self._watches)
        self._watch_table.setSortingEnabled(False)
        self._watch_table.setRowCount(0)
        self._watch_table.setRowCount(len(self._shown))
        for row, watch in enumerate(self._shown):
            self._fill_watch_row(row, watch)
        self._watch_table.setSortingEnabled(True)

        has_rows = bool(self._shown)
        self._watch_table.setVisible(has_rows)
        self._watch_empty.setVisible(not has_rows)
        self._update_reads()
        self._update_buttons()

    def _fill_watch_row(self, row: int, watch: watches.Watch) -> None:
        """Write one watch into a row."""
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        folder_cell = _SortItem(logic.shorten_path(watch.path, 64), watch.path.casefold())
        folder_cell.setData(Qt.ItemDataRole.UserRole, watch.path)
        size_cell = _SortItem(
            util.format_bytes(watch.last_size) if watch.last_checked else "not measured",
            watch.last_size,
        )
        size_cell.setTextAlignment(right)
        if watch.last_checked:
            size_cell.setForeground(QBrush(QColor(theme.SIGNAL)))
            size_cell.setFont(theme.mono_font(9, bold=True))
        else:
            size_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))

        budget_cell = _SortItem(
            util.format_bytes(watch.budget_bytes) if watch.has_budget else "none",
            watch.budget_bytes,
        )
        budget_cell.setTextAlignment(right)
        budget_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))

        used_cell = _SortItem(
            f"{int(round(100 * watch.used_fraction))}%" if watch.has_budget else "—",
            watch.used_fraction,
        )
        used_cell.setTextAlignment(right)
        used_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))

        state_text, state_color = self._watch_state(watch)
        state_cell = _SortItem(state_text, state_text)
        state_cell.setForeground(QBrush(QColor(state_color)))

        self._watch_table.setItem(row, _WATCH_FOLDER, folder_cell)
        self._watch_table.setItem(row, _WATCH_SIZE, size_cell)
        self._watch_table.setItem(row, _WATCH_BUDGET, budget_cell)
        self._watch_table.setItem(row, _WATCH_USED, used_cell)
        self._watch_table.setItem(row, _WATCH_STATE, state_cell)

    def _watch_state(self, watch: watches.Watch) -> tuple[str, str]:
        """A short state word and its colour for one watch."""
        if not watch.last_checked:
            return "not measured", theme.TEXT_MUTED
        if not watch.has_budget:
            return "no budget", theme.TEXT_DIM
        if watch.over_budget:
            return "OVER BUDGET", theme.ALERT
        if watch.used_fraction >= 0.9:
            return "nearly full", theme.WARN
        return "within budget", theme.SIGNAL

    def _update_reads(self) -> None:
        """Refresh the three readouts."""
        if not self._shown:
            self._read_count.set_value("—", "", "add a folder to watch")
            self._read_total.set_value("—", "", "")
            self._read_over.set_value("—", "", "")
            return
        measured = [watch for watch in self._shown if watch.last_checked]
        total = sum(watch.last_size for watch in measured)
        over = [watch for watch in self._shown if watch.over_budget]
        number, unit = split_bytes(total)
        self._read_count.set_value(
            str(len(self._shown)), "folders", f"{len(measured)} measured so far"
        )
        self._read_total.set_value(number, unit, "across the measured watches")
        self._read_over.set_value(str(len(over)), "folders", "above their budget")

    def _update_buttons(self) -> None:
        """Enable Remove only when a watch is selected."""
        self._remove_button.setEnabled(self._selected_watch() is not None and not self._busy)

    def _selected_watch(self) -> watches.Watch | None:
        """The watch on the selected row, or None."""
        model = self._watch_table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._shown):
            return self._shown[index]
        return None

    def _add_watch(self) -> None:
        """Validate the folder and budget, then store the watch."""
        path = self._folder.text().strip().strip('"')
        budget = self._budget.value() * 1024**3
        problem = watches.budget_problem(path, budget)
        if problem is not None:
            self._status.setText(f"NOT ADDED — {problem}")
            return
        reason = watches.add(watches.Watch(path=path, budget_bytes=budget))
        if reason is not None:
            self._status.setText(f"NOT ADDED — {reason}")
            return
        self._folder.clear()
        self._reload()
        self._status.setText(f"ADDED — watching {logic.shorten_path(path)}")

    def _remove_selected(self) -> None:
        """Remove the selected watch. The folder itself is untouched."""
        watch = self._selected_watch()
        if watch is None:
            return
        if not dialogs.confirm(
            self,
            "Stop watching this folder?",
            f"Stop watching {watch.path}?\n\nThe folder and its files are not changed.",
            [],
        ):
            return
        watches.remove(watch.path)
        self._reload()
        self._status.setText("REMOVED — the folder itself was not touched")

    def _start_check(self) -> None:
        """Measure every watch in a worker."""
        if self._busy or not self._watches:
            return
        self._busy = True
        self._check_button.setEnabled(False)
        self._status.setText("CHECKING — measuring the watched folders")
        worker = Worker(watches.check, list(self._watches))
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_checked)
        worker.errored.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, message: object) -> None:
        """Show the worker's current step."""
        self._status.setText(f"CHECKING  ·  {logic.shorten_path(str(message), 60)}")

    def _on_checked(self, result: object) -> None:
        """Store the new sizes, then redraw and report."""
        if isinstance(result, watches.WatchSummary):
            self._summary = result
            watches.save(self._watches)
            self._refresh_watches()
            if result.needs_attention:
                over = len(result.over)
                self._status.setText(f"ATTENTION — {over} folder(s) are over budget")
            else:
                self._status.setText(watches.describe(result))
        else:
            self._status.setText("CHECKED — nothing to report")

    def _on_failed(self, message: str) -> None:
        """Report a failed check."""
        self._status.setText(f"FAILED — {message}")

    def _on_finished(self) -> None:
        """Re-enable the buttons once the worker has stopped."""
        self._busy = False
        self._check_button.setEnabled(True)
        self._update_buttons()

    # ------------------------------------------------------------------ history

    def _on_scans_changed(self) -> None:
        """A new scan may be the second one, so the change view can now show a delta."""
        self._repopulate_drives()
        self._refresh_delta()

    def _repopulate_drives(self) -> None:
        """Fill the drive list from the scans, keeping the current choice when possible."""
        current = self._delta_drive.currentText()
        drives = sorted(self._state.scans)
        self._delta_drive.blockSignals(True)
        self._delta_drive.clear()
        self._delta_drive.addItems(drives)
        if drives:
            self._delta_drive.setCurrentIndex(drives.index(current) if current in drives else 0)
        self._delta_drive.blockSignals(False)

    def _refresh_delta(self) -> None:
        """Show what changed on the chosen drive since the scan before it."""
        root = self._delta_drive.currentText()
        before = history.previous_for(root) if root else None
        after = history.latest_for(root) if root else None

        if before is None or after is None:
            self._delta_table.setRowCount(0)
            self._delta_table.setVisible(False)
            self._delta_empty.setVisible(True)
            self._delta_note.clear()
            return

        delta = history.compare(before, after)
        rows = _delta_rows(delta)
        self._delta_table.setSortingEnabled(False)
        self._delta_table.setRowCount(0)
        self._delta_table.setRowCount(len(rows))
        for index, (path, was, now, change) in enumerate(rows):
            self._fill_delta_row(index, path, was, now, change)
        self._delta_table.setSortingEnabled(True)

        has_rows = bool(rows)
        self._delta_table.setVisible(has_rows)
        self._delta_empty.setVisible(not has_rows)
        if not has_rows:
            self._delta_empty.set_text(
                "NO CHANGE", "Nothing moved between the last two scans of this drive."
            )
        number, unit = split_bytes(abs(delta.total_delta))
        direction = "grew" if delta.total_delta > 0 else (
            "shrank" if delta.total_delta < 0 else "did not change"
        )
        self._delta_note.setText(
            f"{_when(before.timestamp)} → {_when(after.timestamp)}  ·  "
            f"the scanned total {direction}"
            + (f" by {number} {unit}" if delta.total_delta else "")
        )

    def _fill_delta_row(self, row: int, path: str, before: int, after: int, change: int) -> None:
        """Write one folder change into a row."""
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        folder_cell = _SortItem(logic.shorten_path(path, 64), path.casefold())
        was_cell = _SortItem(util.format_bytes(before), before)
        was_cell.setTextAlignment(right)
        was_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))
        now_cell = _SortItem(util.format_bytes(after), after)
        now_cell.setTextAlignment(right)
        now_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))

        sign = "+" if change > 0 else "−" if change < 0 else ""
        change_cell = _SortItem(f"{sign}{util.format_bytes(abs(change))}", change)
        change_cell.setTextAlignment(right)
        change_cell.setFont(theme.mono_font(9, bold=True))
        color = theme.ALERT if change > 0 else theme.SIGNAL if change < 0 else theme.TEXT_MUTED
        change_cell.setForeground(QBrush(QColor(color)))

        self._delta_table.setItem(row, _DELTA_FOLDER, folder_cell)
        self._delta_table.setItem(row, _DELTA_BEFORE, was_cell)
        self._delta_table.setItem(row, _DELTA_AFTER, now_cell)
        self._delta_table.setItem(row, _DELTA_CHANGE, change_cell)


def _delta_rows(delta: history.Delta) -> list[tuple[str, int, int, int]]:
    """Flatten a delta into (path, before, after, change) rows, biggest change first."""
    rows: list[tuple[str, int, int, int]] = []
    for change in delta.grew:
        rows.append((change.path, change.before, change.after, change.delta))
    for change in delta.shrank:
        rows.append((change.path, change.before, change.after, change.delta))
    for change in delta.appeared:
        rows.append((change.path, 0, change.after, change.after))
    for change in delta.vanished:
        rows.append((change.path, change.before, 0, -change.before))
    rows.sort(key=lambda one: -abs(one[3]))
    return rows


def _when(timestamp: float) -> str:
    """A short local date and time for a snapshot."""
    try:
        return time.strftime("%d %b %H:%M", time.localtime(timestamp))
    except (OSError, OverflowError, ValueError):
        return "unknown time"