"""Suggestions page: grouped cleanup items, check boxes, and a confirmed cleanup run.

The page shows what analyzer.build_suggestions found, grouped by category, lets the
user choose the items to clean, and runs cleaner.perform for each of them in a
background worker. Nothing is removed before the user confirms, and permanent
actions say so in the confirmation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from space_analyzer import analyzer, cleaner, util
from space_analyzer.models import (
    CATEGORY_ORDER,
    ActionKind,
    ActionReport,
    Category,
    CleanupItem,
    Risk,
)
from space_analyzer.ui import dialogs, icons, theme
from space_analyzer.ui import page_logic as logic
from space_analyzer.ui.state import AppState
from space_analyzer.ui.theme import RISK_COLOR, RISK_LABEL
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
_SUBTITLE = (
    "What can be cleaned up, grouped by category. Nothing is removed until you confirm — "
    "the items that are safe to remove are already ticked."
)
_ANALYZE_HINT = "IDLE — click Analyze to measure what can be cleaned up"
_ANALYZING = "ANALYZING — measuring candidates"
_NOTHING_FOUND = "Nothing to clean up was found on this PC."
_ANALYZE_FAILED = "FAILED — "
_CLEANING = "CLEANING — running the selected items"
_CLEANED = "COMPLETE — cleanup finished"
_NO_SELECTION = "Nothing selected."
_NO_ACTIONABLE = "None of the ticked items can be run on this PC."
_CHECKBOX_HINT = "items needing administrator rights cannot be ticked as a standard user"
_ORDER_HINT = "the Recycle Bin is emptied last"

_NAME_COLUMN = 0
_RISK_COLUMN = 1
_SIZE_COLUMN = 2
_IMPACT_COLUMN = 3


def _has_checkbox(item: CleanupItem) -> bool:
    """True when the user may choose whether this item runs. Information-only items have none."""
    return item.action is not ActionKind.NONE


def _can_check(item: CleanupItem, admin: bool) -> bool:
    """True when the check box of this item may be ticked by the current user."""
    return not (item.requires_admin and not admin)


def _size_text(item: CleanupItem) -> str:
    """The item's size, or "unknown" when it could not be measured."""
    return util.format_bytes(item.size_bytes if item.size_known else None)


def _impact_text(item: CleanupItem) -> str:
    """What is lost when the item is removed, with the item's note in brackets."""
    if item.note:
        return f"{item.impact}  [{item.note}]"
    return item.impact


def _row_text(item: CleanupItem) -> Tuple[str, ...]:
    """The columns of one item row: title, risk, size and what is lost when it is removed."""
    return (
        item.title,
        RISK_LABEL.get(item.risk, ""),
        _size_text(item),
        _impact_text(item),
    )


class _ItemRow:
    """One CleanupItem and the tree row that shows it."""

    __slots__ = ("item", "row")

    def __init__(self, item: CleanupItem, row: QTreeWidgetItem) -> None:
        self.item = item
        self.row = row


class _CategoryGroup:
    """One category heading with the item rows under it, in the order the analyzer gave."""

    __slots__ = ("category", "heading", "rows")

    def __init__(self, category, heading: QTreeWidgetItem, rows: List[_ItemRow]) -> None:
        self.category = category
        self.heading = heading
        self.rows = rows


def _make_item_row(item: CleanupItem, admin: bool) -> _ItemRow:
    """Build the tree row for one item. Only actionable items get a check box."""
    row = QTreeWidgetItem(list(_row_text(item)))
    row.setData(0, Qt.ItemDataRole.UserRole, item)
    row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if _has_checkbox(item):
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        checked = item.default_selected and _can_check(item, admin)
        row.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    row.setToolTip(_NAME_COLUMN, item.description)
    row.setForeground(_RISK_COLUMN, QBrush(QColor(RISK_COLOR.get(item.risk, theme.TEXT_DIM))))
    risk_font = row.font(_RISK_COLUMN)
    risk_font.setBold(True)
    risk_font.setPointSize(8)
    row.setFont(_RISK_COLUMN, risk_font)
    row.setFont(_SIZE_COLUMN, theme.mono_font(9, bold=True))
    row.setTextAlignment(_SIZE_COLUMN, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.setForeground(_SIZE_COLUMN, QBrush(QColor(theme.TEXT)))
    row.setForeground(_IMPACT_COLUMN, QBrush(QColor(theme.TEXT_DIM)))
    locked = item.requires_admin and not admin
    row.setForeground(_NAME_COLUMN, QBrush(QColor(theme.TEXT_DIM if locked else theme.TEXT)))
    return _ItemRow(item, row)


def _make_heading(category: Category, rows: List[_ItemRow]) -> QTreeWidgetItem:
    """Build the category heading row with a count and the summed size on the right."""
    total = sum(entry.item.size_bytes for entry in rows if entry.item.size_known)
    heading = QTreeWidgetItem(
        [category.value.upper(), "", util.format_bytes(total), f"{len(rows)} ITEM(S)"]
    )
    heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
    font = heading.font(_NAME_COLUMN)
    font.setBold(True)
    font.setPointSize(9)
    heading.setFont(_NAME_COLUMN, font)
    heading.setFont(_SIZE_COLUMN, theme.mono_font(9, bold=True))
    heading.setForeground(_NAME_COLUMN, QBrush(QColor(theme.SIGNAL)))
    heading.setForeground(_SIZE_COLUMN, QBrush(QColor(theme.SIGNAL)))
    heading.setTextAlignment(
        _SIZE_COLUMN, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    heading.setForeground(_IMPACT_COLUMN, QBrush(QColor(theme.TEXT_DIM)))
    return heading


def _empty_bin_item() -> CleanupItem:
    """The Recycle Bin item used when the page offers to empty the bin after a move."""
    return CleanupItem(
        item_id="recycle.bin",
        title="Recycle Bin",
        category=Category.RECYCLE,
        risk=Risk.LOW,
        description="Files you deleted that are waiting in the Recycle Bin.",
        impact="Files in the Recycle Bin are gone for good. You can no longer restore them.",
        regenerates="No",
        action=ActionKind.EMPTY_RECYCLE_BIN,
    )


def _say(progress: Optional[Callable[[str], None]], text: str) -> None:
    """Send a progress message when a callback was given."""
    if progress is not None:
        progress(text)


class SuggestionsPage(QWidget):
    """Lists cleanup suggestions by category and runs the chosen ones after confirmation."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._busy = False
        self._analyzing = False
        self._updating = False  # True while the page changes check boxes itself
        self._pending: List[CleanupItem] = []  # items the running worker works on
        self._groups: List[_CategoryGroup] = []

        self._analyze_button = QPushButton("Analyze")
        self._analyze_button.setIcon(icons.icon("spark", "#10130a", 15))
        self._analyze_button.setProperty("primary", "true")
        self._analyze_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._analyze_button.clicked.connect(self._start_analyze)
        self._clean_button = QPushButton("Clean selected")
        self._clean_button.setIcon(icons.icon("check", "#10130a", 15))
        self._clean_button.setProperty("primary", "true")
        self._clean_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_button.setEnabled(False)
        self._clean_button.clicked.connect(self._clean_selected)
        self._tick_all_button = QPushButton("Tick all")
        self._tick_all_button.setProperty("ghost", "true")
        self._tick_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tick_all_button.clicked.connect(self._tick_all)
        self._untick_all_button = QPushButton("Untick all")
        self._untick_all_button.setProperty("ghost", "true")
        self._untick_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._untick_all_button.clicked.connect(self._untick_all)

        self._status = set_role(QLabel(_ANALYZE_HINT), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._selection_label = set_role(QLabel(_NO_SELECTION), "muted")
        self._selection_label.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_label.setWordWrap(True)
        self._selection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._read_found = Readout("Found", color=theme.TEXT)
        self._read_selected = Readout("Selected", color=theme.SIGNAL)
        self._read_safe = Readout("Safe", color=theme.SIGNAL)
        self._read_admin = Readout("Locked", color=theme.WARN)

        self._tree = self._build_tree()
        self._empty = EmptyState(
            "NOTHING ANALYZED",
            "Click Analyze to measure what can be cleaned up on this PC.",
            glyph="spark",
        )
        self._build_layout()
        self._state.suggestions_changed.connect(self._refresh)
        self._refresh()

    def shutdown(self) -> None:
        """Cancel the running worker and wait up to 5 seconds for its thread to stop."""
        worker = self._worker
        if worker is None:
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ---------------------------------------------------------------- widgets

    def _build_tree(self) -> QTreeWidget:
        """Create the suggestion tree with its columns and check-box handling."""
        tree = QTreeWidget()
        tree.setHeaderLabels(["ITEM", "RISK", "SIZE", "WHAT HAPPENS WHEN IT IS REMOVED"])
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
        for column in (_NAME_COLUMN, _RISK_COLUMN, _SIZE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_IMPACT_COLUMN, QHeaderView.ResizeMode.Stretch)
        tree.itemChanged.connect(self._on_item_changed)
        return tree

    def _build_layout(self) -> None:
        """Arrange the header, the buttons, the readouts and the tree."""
        header = PageHeader("02 / cleanup candidates", "Suggestions", _SUBTITLE)

        controls = QHBoxLayout()
        controls.setSpacing(9)
        controls.addWidget(self._analyze_button)
        controls.addWidget(self._clean_button)
        controls.addSpacing(4)
        controls.addWidget(self._tick_all_button)
        controls.addWidget(self._untick_all_button)
        controls.addStretch(1)

        reads = QHBoxLayout()
        reads.setSpacing(0)
        for index, read in enumerate(
            (self._read_found, self._read_selected, self._read_safe, self._read_admin)
        ):
            if index:
                reads.addWidget(rule(horizontal=False))
            reads.addWidget(read, 1)

        panel = Panel(
            title="Candidates",
            glyph="spark",
            caption="grouped by category — expand to read each item",
            margins=(12, 10, 12, 10),
        )
        panel.add(self._empty, 1)
        panel.add(self._tree, 1)
        self._empty.setVisible(False)

        scroll = PageScroll(spacing=12)
        layout = scroll.body()
        layout.addWidget(header)
        layout.addLayout(controls)
        layout.addLayout(reads)
        layout.addWidget(self._status)
        layout.addWidget(panel, 1)
        layout.addWidget(self._selection_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ---------------------------------------------------------------- analysis

    def _start_analyze(self) -> None:
        """Run analyzer.build_suggestions in a background worker."""
        if self._busy:
            return
        worker = Worker(analyzer.build_suggestions, admin=self._state.admin)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_analyze_succeeded)
        worker.errored.connect(self._on_analyze_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._busy = True
        self._analyzing = True
        self._status.setText(_ANALYZING)
        self._refresh_controls()
        worker.start()

    def _on_progress(self, message: object) -> None:
        """Show the engine's status line."""
        self._status.setText(str(message))

    def _on_analyze_succeeded(self, items: List[CleanupItem]) -> None:
        """Store the items, which rebuilds the tree through suggestions_changed."""
        self._state.set_suggestions(items)
        if not items:
            self._status.setText(_NOTHING_FOUND)

    def _on_analyze_failed(self, message: str) -> None:
        """Report a failed analysis. The list stays as it was."""
        self._status.setText(_ANALYZE_FAILED + message)

    def _on_worker_finished(self) -> None:
        """Unlock the controls once the worker thread has ended."""
        self._busy = False
        self._analyzing = False
        self._refresh_controls()

    # ---------------------------------------------------------------- the tree

    def _refresh(self) -> None:
        """Rebuild the tree from state.suggestions."""
        self._show_items(self._state.suggestions)

    def _show_items(self, items: List[CleanupItem]) -> None:
        """Replace the tree with one heading per category and one row per item."""
        self._tree.clear()
        self._groups = []
        order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
        categories = sorted({item.category for item in items}, key=lambda c: order[c])
        self._updating = True
        try:
            for category in categories:
                rows: List[_ItemRow] = []
                for item in items:
                    if item.category != category:
                        continue
                    rows.append(_make_item_row(item, self._state.admin))
                heading = _make_heading(category, rows)
                for entry in rows:
                    heading.addChild(entry.row)
                self._tree.addTopLevelItem(heading)
                heading.setExpanded(True)
                self._groups.append(_CategoryGroup(category, heading, rows))
        finally:
            self._updating = False
        has_rows = bool(self._groups)
        self._tree.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        if not has_rows:
            self._empty.set_text(
                "NOTHING ANALYZED",
                "Click Analyze to measure what can be cleaned up on this PC.",
            )
        self._refresh_controls()

    def _all_rows(self) -> List[_ItemRow]:
        """Every item row in tree order."""
        return [entry for group in self._groups for entry in group.rows]

    def _checked_rows(self) -> List[_ItemRow]:
        """The item rows whose check box is ticked, in tree order."""
        return [
            entry
            for entry in self._all_rows()
            if _has_checkbox(entry.item)
            and entry.row.checkState(0) == Qt.CheckState.Checked
        ]

    def _tickable_rows(self) -> List[_ItemRow]:
        """The rows whose check box the current user may tick."""
        return [
            entry
            for entry in self._all_rows()
            if _has_checkbox(entry.item) and _can_check(entry.item, self._state.admin)
        ]

    def _on_item_changed(self, row: QTreeWidgetItem, column: int) -> None:
        """Refresh the controls and the summary after any check-box change."""
        if self._updating or column != 0:
            return
        self._refresh_controls()

    def _set_checks(self, checked: Callable[[CleanupItem], bool]) -> None:
        """Set every checkable box from `checked(item)`, leaving admin-only items alone."""
        self._updating = True
        try:
            for entry in self._tickable_rows():
                state = Qt.CheckState.Checked if checked(entry.item) else Qt.CheckState.Unchecked
                entry.row.setCheckState(0, state)
        finally:
            self._updating = False
        self._refresh_controls()

    def _tick_all(self) -> None:
        """Tick every item the current user may tick."""
        self._set_checks(lambda _item: True)

    def _untick_all(self) -> None:
        """Untick every item, including the ones the analyzer pre-selected."""
        self._set_checks(lambda _item: False)

    # ---------------------------------------------------------------- cleanup

    def _clean_selected(self) -> None:
        """Confirm the selection, then run cleaner.perform for each chosen item in a worker."""
        if self._busy:
            return
        selected = [entry.item for entry in self._checked_rows()]
        if not selected:
            self._status.setText(_NO_SELECTION)
            return
        runnable = [item for item in selected if item.action is not ActionKind.NONE]
        if not runnable:
            self._status.setText(_NO_ACTIONABLE)
            return
        if not self._confirm(runnable):
            return
        self._pending = logic.cleanup_order(runnable)
        worker = Worker(self._run_items, self._pending)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_clean_succeeded)
        worker.errored.connect(self._on_clean_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._busy = True
        self._status.setText(_CLEANING)
        self._refresh_controls()
        worker.start()

    def _confirm(self, items: List[CleanupItem]) -> bool:
        """Ask the user to confirm. Permanent actions use the destructive wording."""
        details = logic.cleanup_details(items)
        text = (
            f"Clean {len(items)} item(s), about "
            f"{util.format_bytes(logic.selection_bytes(items))}? "
            + " ".join(logic.cleanup_notices(items))
        )
        return dialogs.confirm(
            self, "Clean selected items?", text, details, destructive=logic.is_permanent(items)
        )

    def _run_items(
        self,
        items: List[CleanupItem],
        *,
        progress: Optional[Callable[[str], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> List[ActionReport]:
        """Run cleaner.perform for each item in order, stopping when the user cancels."""
        reports: List[ActionReport] = []
        for item in items:
            if cancel is not None and cancel.is_set():
                break
            _say(progress, item.title)
            reports.append(
                cleaner.perform(item, admin=self._state.admin, progress=progress, cancel=cancel)
            )
        return reports

    def _on_clean_succeeded(self, reports: List[ActionReport]) -> None:
        """Show what each item did, then offer to empty the bin when files were moved there."""
        items = self._pending
        self._pending = []
        dialogs.show_reports(self, "Cleanup finished", reports)
        self._status.setText(_CLEANED)
        if logic.moved_to_recycle_bin(items, reports):
            self._offer_empty_bin()
        self._refresh_controls()

    def _on_clean_failed(self, message: str) -> None:
        """Report a failed cleanup. The list stays as it was."""
        self._pending = []
        self._status.setText("FAILED — " + message)
        self._refresh_controls()

    def _offer_empty_bin(self) -> None:
        """Ask whether to empty the Recycle Bin now, which frees the space the moves took."""
        text = "The files were moved to the Recycle Bin. " + logic.EMPTY_BIN_NOTICE + " Empty it now?"
        if not dialogs.confirm(self, "Empty the Recycle Bin?", text, destructive=True):
            return
        item = _empty_bin_item()
        worker = Worker(cleaner.perform, item, admin=self._state.admin)
        worker.succeeded.connect(self._on_empty_succeeded)
        worker.errored.connect(self._on_clean_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._busy = True
        self._status.setText("EMPTYING — clearing the Recycle Bin")
        self._refresh_controls()
        worker.start()

    def _on_empty_succeeded(self, reports: List[ActionReport]) -> None:
        """Show the result of emptying the bin."""
        dialogs.show_reports(self, "Recycle Bin emptied", reports)
        self._status.setText("COMPLETE — the Recycle Bin is empty")
        self._refresh_controls()

    # ---------------------------------------------------------------- controls

    def _refresh_controls(self) -> None:
        """Enable each control for the current state and show the selection summary."""
        idle = not self._busy
        selected = self._checked_rows()
        runnable = [entry.item for entry in selected if entry.item.action is not ActionKind.NONE]
        tickable = self._tickable_rows()
        self._analyze_button.setEnabled(idle)
        self._clean_button.setEnabled(idle and bool(runnable))
        self._tick_all_button.setEnabled(idle and len(tickable) > 0)
        self._untick_all_button.setEnabled(idle and bool(selected))
        self._tree.setEnabled(idle)
        self._selection_label.setText(self._selection_text(selected, tickable))
        self._update_reads(selected)
        self._sync_idle_status()

    def _sync_idle_status(self) -> None:
        """Show the idle hint only when nothing is listed and nothing is running.

        The status line is set by the analyze and cleanup flows, but a suggestion
        list can also arrive from state without either flow running. Leaving the
        idle hint on screen beside a full list contradicts what is shown.
        """
        if self._busy:
            return
        if self._all_rows():
            if self._status.text() == _ANALYZE_HINT:
                self._status.setText("LISTED — tick what you want to remove, then Clean selected")
            return
        self._status.setText(_ANALYZE_HINT)

    def _update_reads(self, selected: List[_ItemRow]) -> None:
        """Keep the four readouts in step with what was found and what is ticked."""
        rows = self._all_rows()
        found = sum(entry.item.size_bytes for entry in rows if entry.item.size_known)
        found_number, found_unit = split_bytes(found if rows else None)
        self._read_found.set_value(
            found_number,
            found_unit,
            f"{len(rows)} items" if rows else "run an analysis",
        )
        picked = logic.selection_bytes([entry.item for entry in selected])
        picked_number, picked_unit = split_bytes(picked if selected else None)
        self._read_selected.set_value(
            picked_number,
            picked_unit,
            f"{len(selected)} ticked" if selected else "nothing ticked",
        )
        safe = [entry for entry in rows if entry.item.risk is Risk.SAFE]
        self._read_safe.set_value(
            str(len(safe)), "items", "regenerated automatically" if safe else "none found"
        )
        locked = [entry for entry in rows if entry.item.requires_admin]
        self._read_admin.set_value(
            str(len(locked)),
            "items",
            "restart as administrator" if locked and not self._state.admin else "available",
        )

    def _selection_text(self, selected: List[_ItemRow], tickable: List[_ItemRow]) -> str:
        """Summary of the current selection, with the notes about admin items and the run order."""
        if not selected:
            return _NO_SELECTION
        parts = [
            logic.selection_summary(
                len(selected), logic.selection_bytes([entry.item for entry in selected])
            )
        ]
        if len(tickable) < len(self._all_rows()):
            parts.append(_CHECKBOX_HINT)
        parts.append(_ORDER_HINT)
        return "  ·  ".join(parts)
