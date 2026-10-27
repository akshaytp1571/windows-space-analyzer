"""Installed apps page: what is installed, how much space it claims, and a safe uninstall.

The page never removes an application's files. "Uninstall" runs the uninstaller the
publisher registered, exactly as the Windows settings page does, after showing the
user the exact command that will run. See space_analyzer.apps for the checks.
"""

from __future__ import annotations

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

from space_analyzer import apps as appmod
from space_analyzer import export, system, util
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
_HEADERS = ("NAME", "PUBLISHER", "VERSION", "SIZE", "SCOPE")
_NAME_COLUMN = 0
_PUBLISHER_COLUMN = 1
_VERSION_COLUMN = 2
_SIZE_COLUMN = 3
_SCOPE_COLUMN = 4
_IDLE_HINT = "Not listed yet — click List apps to read what is installed."
_NO_MATCH = "No app matches that filter."


class _SortItem(QTableWidgetItem):
    """A cell that sorts by a real key rather than by its display text."""

    def __init__(self, text: str, sort_key: int | float | str) -> None:
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _SortItem):
            return self.sort_key < other.sort_key
        return self.text() < other.text()


class AppsPage(QWidget):
    """Lists installed applications and launches a publisher's own uninstaller."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._worker: Worker | None = None
        self._busy = False
        self._apps: list[appmod.InstalledApp] = []
        self._shown: list[appmod.InstalledApp] = []

        self._find = QLineEdit()
        self._find.setPlaceholderText("filter by name or publisher")
        self._find.setClearButtonEnabled(True)
        self._find.addAction(
            icons.icon("search", theme.TEXT_DIM, 14), QLineEdit.ActionPosition.LeadingPosition
        )
        self._find.setMinimumWidth(220)
        self._sort = QComboBox()
        self._sort.setMinimumWidth(132)
        self._sort.addItem("Largest first", userData="size")
        self._sort.addItem("Name A–Z", userData="name")
        self._sort.addItem("With a size only", userData="sized")

        self._read_count = Readout("Installed", color=theme.TEXT)
        self._read_total = Readout("Recorded size", color=theme.SIGNAL)
        self._read_uninstall = Readout("Can uninstall", color=theme.COOL)

        self._table = self._build_table()
        self._empty = EmptyState(_IDLE_HINT, "Sizes come from the registry.", glyph="stack")
        self._caveat = set_role(QLabel(), "muted")
        self._caveat.setTextFormat(Qt.TextFormat.PlainText)
        self._caveat.setWordWrap(True)
        self._caveat.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._status = set_role(QLabel(), "status")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._list_button = QPushButton("List apps")
        self._list_button.setIcon(icons.icon("search", theme.BG_DEEP, 14))
        self._list_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list_button.clicked.connect(self._start_listing)

        self._export_button = QPushButton("Export list")
        self._export_button.setIcon(icons.icon("stack", theme.TEXT_MUTED, 14))
        self._export_button.setProperty("ghost", "true")
        self._export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export)

        self._open_button = QPushButton("Open folder")
        self._open_button.setIcon(icons.icon("arrow", theme.TEXT_MUTED, 14))
        self._open_button.setProperty("ghost", "true")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_selected)

        self._uninstall_button = QPushButton("Uninstall")
        self._uninstall_button.setIcon(icons.icon("trash", theme.ALERT, 14))
        self._uninstall_button.setProperty("danger", "true")
        self._uninstall_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._uninstall_button.setEnabled(False)
        self._uninstall_button.clicked.connect(self._uninstall_selected)

        self._build_layout()
        self._find.textChanged.connect(self._refresh)
        self._sort.currentIndexChanged.connect(self._refresh)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._on_admin_changed()
        self._state.admin_changed.connect(self._on_admin_changed)
        self._refresh()

    def shutdown(self) -> None:
        """Wait briefly for a running listing thread to stop."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        worker.wait(_STOP_WAIT_MS)

    # ------------------------------------------------------------------ layout

    def _build_table(self) -> QTableWidget:
        """Create the apps table with its columns, selection rules and sorting."""
        table = QTableWidget()
        table.setColumnCount(len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(_NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_PUBLISHER_COLUMN, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(_PUBLISHER_COLUMN, 220)
        for column in (_VERSION_COLUMN, _SIZE_COLUMN, _SCOPE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.setSortingEnabled(True)
        header.setSortIndicator(_SIZE_COLUMN, Qt.SortOrder.DescendingOrder)
        return table

    def _build_layout(self) -> None:
        """Arrange the header, filters, readouts, table and actions."""
        header = PageHeader(
            "05 / installed software",
            "Apps",
            "What is installed, and the space each publisher recorded for it. Uninstall runs "
            "the app's own uninstaller — this tool never deletes an application's files itself.",
        )

        filters = QHBoxLayout()
        filters.setSpacing(9)
        filters.addWidget(set_role(QLabel("SORT"), "caption"))
        filters.addWidget(self._sort)
        filters.addSpacing(8)
        filters.addWidget(set_role(QLabel("FILTER"), "caption"))
        filters.addWidget(self._find, 1)
        filters.addWidget(self._list_button)

        reads = QHBoxLayout()
        reads.setSpacing(0)
        for index, read in enumerate((self._read_count, self._read_total, self._read_uninstall)):
            if index:
                reads.addWidget(rule(horizontal=False))
            reads.addWidget(read, 1)
        reads.addStretch(1)

        panel = Panel(
            title="Applications",
            glyph="stack",
            caption="click a column heading to sort",
            margins=(12, 10, 12, 10),
        )
        panel.add(self._caveat)
        panel.add(self._empty, 1)
        panel.add(self._table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addWidget(self._status, 1)
        actions.addWidget(self._export_button)
        actions.addWidget(self._open_button)
        actions.addWidget(self._uninstall_button)

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

    # ------------------------------------------------------------------ listing

    def _start_listing(self) -> None:
        """Read the uninstall keys in a worker, since the registry can be slow."""
        if self._busy:
            return
        self._busy = True
        self._list_button.setEnabled(False)
        self._status.setText("LISTING — reading installed applications")
        worker = Worker(appmod.installed_apps)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_listed)
        worker.errored.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, message: object) -> None:
        """Show the worker's current step."""
        self._status.setText(f"LISTING  ·  {message}")

    def _on_listed(self, found: object) -> None:
        """Store the apps the worker found and redraw."""
        self._apps = list(found) if isinstance(found, list) else []
        self._refresh()
        if self._apps:
            self._status.setText(f"LISTED — {len(self._apps)} application(s) found")
        else:
            self._status.setText("EMPTY — no applications were found in the registry")

    def _on_failed(self, message: str) -> None:
        """Report a failed listing."""
        self._status.setText(f"FAILED — {message}")

    def _on_finished(self) -> None:
        """Re-enable the list button once the worker has stopped."""
        self._busy = False
        self._list_button.setEnabled(True)

    # ------------------------------------------------------------------ state

    def _on_admin_changed(self) -> None:
        """Uninstalling may need elevation, so the note follows the admin state."""
        self._update_buttons()

    def _refresh(self) -> None:
        """Redraw the table from the stored apps, the filter and the sort order."""
        mode = self._sort.currentData()
        text = self._find.text().strip().casefold()

        rows = [app for app in self._apps if self._matches(app, text)]
        if mode == "sized":
            rows = [app for app in rows if app.size_known]
        if mode == "name":
            rows.sort(key=lambda app: app.name.casefold())
        else:
            rows.sort(key=lambda app: (-app.size_bytes, app.name.casefold()))

        self._shown = rows
        self._fill_table(rows)
        self._update_reads()

        has_rows = bool(rows)
        self._table.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        if not self._apps:
            self._empty.set_text(_IDLE_HINT, "Sizes come from the registry.")
        elif not has_rows:
            self._empty.set_text(_NO_MATCH, "Clear the filter, or choose another sort.")
        self._update_buttons()

    def _matches(self, app: appmod.InstalledApp, text: str) -> bool:
        """True when the app matches the filter text, on name or publisher."""
        if not text:
            return True
        return text in app.name.casefold() or text in app.publisher.casefold()

    def _fill_table(self, rows: list[appmod.InstalledApp]) -> None:
        """Replace the table rows with these apps."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for row, app in enumerate(rows):
            self._fill_row(row, app)
        self._table.setSortingEnabled(True)

    def _fill_row(self, row: int, app: appmod.InstalledApp) -> None:
        """Write one app into a row. The name cell carries the registry key for lookups."""
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        name_cell = _SortItem(app.name, app.name.casefold())
        name_cell.setData(Qt.ItemDataRole.UserRole, app.key)
        publisher_cell = _SortItem(app.publisher or "—", app.publisher.casefold())
        publisher_cell.setForeground(QBrush(QColor(theme.TEXT_DIM)))
        version_cell = _SortItem(app.version or "—", app.version)
        version_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))
        version_cell.setFont(theme.mono_font(9))

        size_text = util.format_bytes(app.size_bytes) if app.size_known else "no size"
        size_cell = _SortItem(size_text, app.size_bytes)
        size_cell.setTextAlignment(right)
        if app.size_known:
            size_cell.setForeground(QBrush(QColor(theme.SIGNAL)))
            size_cell.setFont(theme.mono_font(9, bold=True))
        else:
            size_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))

        scope_cell = _SortItem(
            "this PC" if app.scope == "machine" else "just you", app.scope
        )
        scope_cell.setTextAlignment(right)
        scope_cell.setForeground(QBrush(QColor(theme.TEXT_MUTED)))

        self._table.setItem(row, _NAME_COLUMN, name_cell)
        self._table.setItem(row, _PUBLISHER_COLUMN, publisher_cell)
        self._table.setItem(row, _VERSION_COLUMN, version_cell)
        self._table.setItem(row, _SIZE_COLUMN, size_cell)
        self._table.setItem(row, _SCOPE_COLUMN, scope_cell)

    def _update_reads(self) -> None:
        """Refresh the three readouts and the size caveat."""
        if not self._apps:
            self._read_count.set_value("—", "", "click List apps")
            self._read_total.set_value("—", "", "")
            self._read_uninstall.set_value("—", "", "")
            self._caveat.clear()
            return

        total = appmod.total_size(self._apps)
        number, unit = split_bytes(total)
        runnable = sum(1 for app in self._apps if app.can_uninstall)
        sized = sum(1 for app in self._apps if app.size_known)
        self._read_count.set_value(str(len(self._apps)), "apps", f"{sized} report a size")
        self._read_total.set_value(number, unit, "as recorded by each publisher")
        self._read_uninstall.set_value(str(runnable), "apps", "have a usable uninstaller")
        self._caveat.setText(appmod.size_caveat(self._apps))

    def _update_buttons(self) -> None:
        """Enable the per-app actions for the one selected row."""
        selected = self._selected_app()
        has_selection = selected is not None
        self._open_button.setEnabled(has_selection and bool(selected.install_location))
        self._uninstall_button.setEnabled(
            has_selection and selected.can_uninstall and not self._busy
        )
        self._export_button.setEnabled(bool(self._apps))

    def _selected_app(self) -> appmod.InstalledApp | None:
        """The app on the selected row, or None."""
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._shown):
            return self._shown[index]
        return None

    # ------------------------------------------------------------------ actions

    def _open_selected(self) -> None:
        """Show the selected app's install folder in Explorer."""
        app = self._selected_app()
        if app is None or not app.install_location:
            return
        system.open_in_explorer(app.install_location)

    def _uninstall_selected(self) -> None:
        """Confirm, showing the exact command, then launch the publisher's uninstaller."""
        app = self._selected_app()
        if app is None:
            return
        problem = appmod.uninstall_problem(app.uninstall_string)
        if problem is not None:
            self._status.setText(f"CANNOT UNINSTALL — {problem}")
            return

        details = [
            f"Publisher: {app.publisher or 'not recorded'}",
            f"Version: {app.version or 'not recorded'}",
            f"Command that will run: {app.uninstall_string}",
            "The app's own uninstaller opens and shows its own prompts.",
        ]
        text = (
            f"Uninstall {app.name}?\n\nThis starts the uninstaller the publisher registered. "
            "This tool does not delete the application's files itself — the uninstaller does, "
            "and it may ask you to confirm again."
        )
        if not dialogs.confirm(self, "Run the uninstaller?", text, details):
            return

        reason = appmod.launch_uninstall(app)
        if reason is not None:
            self._status.setText(f"FAILED — {reason}")
            return
        self._status.setText(
            "STARTED — the uninstaller is opening; click List apps when it finishes"
        )

    def _export(self) -> None:
        """Write the listed apps to a CSV or JSON file."""
        if not self._shown:
            return
        path = dialogs.ask_save_path(
            self, "Export the app list", export.default_name("apps"), "CSV or JSON"
        )
        if not path:
            return
        try:
            export.write_apps(path, self._shown)
        except OSError as exc:
            self._status.setText(f"COULD NOT EXPORT — {exc}")
            return
        self._status.setText(f"EXPORTED — saved to {logic.shorten_path(path)}")
