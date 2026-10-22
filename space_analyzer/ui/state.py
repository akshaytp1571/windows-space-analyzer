"""Shared application state for the main window and its pages."""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QObject, Signal

from space_analyzer.models import CleanupItem, ScanResult


class AppState(QObject):
    """Holds what the pages share: admin status, drive scans and cleanup suggestions."""

    scans_changed = Signal()
    suggestions_changed = Signal()

    def __init__(self, admin: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.admin: bool = admin
        self.scans: Dict[str, ScanResult] = {}
        self.suggestions: List[CleanupItem] = []

    def set_scan(self, result: ScanResult) -> None:
        """Store the scan of one drive, keyed by its root, and notify the pages."""
        self.scans[result.root] = result
        self.scans_changed.emit()

    def clear_scans(self) -> None:
        """Forget every drive scan."""
        self.scans.clear()
        self.scans_changed.emit()

    def set_suggestions(self, items: List[CleanupItem]) -> None:
        """Replace the suggestion list and notify the pages."""
        self.suggestions = list(items)
        self.suggestions_changed.emit()
