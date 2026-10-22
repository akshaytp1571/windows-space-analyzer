"""Confirmation prompts and the dialog that reports what each cleanup action did."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from space_analyzer import util
from space_analyzer.models import ActionKind, ActionReport, Risk
from space_analyzer.ui import icons, theme
from space_analyzer.ui.widgets import Panel, icon_label, rule, set_role

_FAILURE_LIMIT = 20
_RECYCLE_ACTIONS = ("recycle", ActionKind.DELETE_PATHS.value, ActionKind.CLEAN_UPDATE_CACHE.value)
_UNTITLED_NAMES = {"recycle": "Moved to the Recycle Bin"}


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    details: list[str] | None = None,
    *,
    destructive: bool = False,
) -> bool:
    """Ask a Yes or No question. Returns True only when the user clicks Yes."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    if details:
        box.setDetailedText("\n".join(details))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    yes_button = box.button(QMessageBox.StandardButton.Yes)
    no_button = box.button(QMessageBox.StandardButton.No)
    if destructive:
        yes_button.setProperty("danger", "true")
        yes_button.setText("Yes, continue")
    else:
        yes_button.setProperty("primary", "true")
    no_button.setProperty("ghost", "true")
    box.setDefaultButton(
        QMessageBox.StandardButton.No if destructive else QMessageBox.StandardButton.Yes
    )
    box.setEscapeButton(no_button)
    box.exec()
    chosen = box.clickedButton() == yes_button
    box.deleteLater()
    return chosen


def show_reports(parent: QWidget | None, title: str, reports: list[ActionReport]) -> None:
    """Show what each cleanup action did: counts, space freed, messages and failures."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(760, 580)

    heading_row = QHBoxLayout()
    heading_row.setSpacing(9)
    heading_row.addWidget(icon_label("check", theme.SIGNAL, 18))
    heading = set_role(QLabel(title.upper()), "page-title")
    heading_row.addWidget(heading, 1)

    browser = QTextBrowser()
    browser.setReadOnly(True)
    browser.setOpenExternalLinks(False)
    browser.setHtml(_reports_html(reports))

    close_button = QPushButton("Close")
    close_button.setProperty("primary", "true")
    close_button.setCursor(Qt.CursorShape.PointingHandCursor)
    close_button.setDefault(True)
    close_button.clicked.connect(dialog.accept)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    buttons.addWidget(close_button)

    panel = Panel(margins=(14, 12, 14, 12))
    panel.add(browser, 1)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)
    layout.addLayout(heading_row)
    layout.addWidget(rule())
    layout.addWidget(panel, 1)
    layout.addLayout(buttons)

    dialog.exec()
    dialog.deleteLater()


def _reports_html(reports: list[ActionReport]) -> str:
    """Build the HTML body of the reports dialog. All text is escaped."""
    if not reports:
        return "<p>No actions were run.</p>"
    return "".join(_report_html(report) for report in reports)


def _report_html(report: ActionReport) -> str:
    """Build the section for one report: title, counts, notes, messages and failures."""
    name = report.title or _UNTITLED_NAMES.get(report.action, "Action")
    muted = theme.TEXT_MUTED
    signal = theme.SIGNAL
    good = theme.RISK_COLOR[Risk.SAFE]
    warn = theme.WARN
    bad = theme.ALERT

    parts = [
        f'<h3 style="color:{signal}; margin:0 0 4px 0; font-size:13px;">{html.escape(name)}</h3>'
    ]
    parts.append(
        f'<p style="color:{muted}; margin:0 0 8px 0; font-size:12px;">'
        f"removed <b style='color:{theme.TEXT};'>{util.format_count(report.removed_count)}</b>"
        f" &nbsp;·&nbsp; freed "
        f"<b style='color:{good};'>{util.format_bytes(report.bytes_removed)}</b></p>"
    )
    if report.dry_run:
        parts.append(f'<p style="color:{warn};"><i>Dry run. Nothing was changed.</i></p>')
    elif report.action in _RECYCLE_ACTIONS and report.removed_count > 0:
        parts.append(
            f'<p style="color:{muted}; font-size:12px;">Items were sent to the Recycle Bin. '
            "Space is only freed after the bin is emptied.</p>"
        )
    if report.requires_restart:
        parts.append(f'<p style="color:{warn};">Restart Windows to finish this action.</p>')
    if report.messages:
        items = "".join(
            f'<li style="color:{muted}; font-size:12px;">{html.escape(message)}</li>'
            for message in report.messages
        )
        parts.append(f"<ul>{items}</ul>")
    if report.failed:
        parts.append(_failures_html(report))
    if report.cancelled:
        parts.append(f'<p style="color:{warn};"><b>Stopped early.</b></p>')
    parts.append(f'<hr style="border:none; border-top:1px solid {theme.LINE};">')
    return "".join(parts)


def _failures_html(report: ActionReport) -> str:
    """List the first failures as path and reason, then how many more there are."""
    shown = report.failed[:_FAILURE_LIMIT]
    rows = "".join(
        f'<li style="color:{theme.TEXT_MUTED}; font-size:12px;">'
        f"{html.escape(failure.path)}: {html.escape(failure.reason)}</li>"
        for failure in shown
    )
    text = (
        f'<p style="color:{theme.ALERT};">Problems ({util.format_count(len(report.failed))}):</p>'
        f"<ul>{rows}</ul>"
    )
    remaining = len(report.failed) - len(shown)
    if remaining > 0:
        text += f'<p style="color:{theme.TEXT_MUTED};">and {util.format_count(remaining)} more</p>'
    return text
