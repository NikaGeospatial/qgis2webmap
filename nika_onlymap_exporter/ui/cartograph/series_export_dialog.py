"""The format choice of "Export series…".

Three outputs, each with one line saying what it is. An output that cannot be
made right now says why beside its choice and, when switching the series' maps
to Interactive would fix it, offers that directly.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...core.atlas_series import Operation, SeriesState, SeriesStatus
from ..fitted_label import FittedLabel
from ..i18n import plural, tr

PDF, IMAGES, HTML = "pdf", "images", "html"


class FormatRow(QWidget):
    """One format: its radio button, what it makes, and why it is unavailable."""

    def __init__(self, key: str, title: str, about: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.key = key
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 8)
        column.setSpacing(2)
        self.radio = QRadioButton(title, self)
        column.addWidget(self.radio)
        self.about = FittedLabel(about, self)
        self.about.setProperty("role", "hint")
        column.addWidget(self.about)
        self.reason = FittedLabel("", self)
        self.reason.setProperty("role", "checkVerdict")
        self.reason.setProperty("tone", "warning")
        column.addWidget(self.reason)
        self.switch = QPushButton(tr("Switch to Interactive"), self)
        column.addWidget(self.switch)

    def show_operation(self, operation: Operation, extra: str = "") -> None:
        self.radio.setEnabled(operation.ok)
        text = operation.reason if not operation.ok else extra
        if operation.limitation:
            text = f"{operation.limitation} {text}"
        if operation.needs_interactive:
            text += " " + tr(
                "Switching changes how the map behaves in the exported HTML."
            )
        self.reason.setText(text.strip())
        self.reason.setVisible(bool(text.strip()))
        self.switch.setVisible(bool(operation.needs_interactive))


class SeriesExportDialog(QDialog):
    """Choose PDF, images or a web page for the whole series."""

    def __init__(
        self,
        status: Callable[[], SeriesStatus],
        switch_to_interactive: Callable[[Sequence[str]], None],
        saved_problems: Callable[[], tuple[str, ...]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Export series"))
        self._status = status
        self._switch = switch_to_interactive
        self._saved_problems = saved_problems
        column = QVBoxLayout(self)
        self.scope = FittedLabel("", self)
        column.addWidget(self.scope)

        self.rows = (
            FormatRow(
                PDF,
                tr("PDF document"),
                tr("All pages in one PDF file, made by QGIS."),
                self,
            ),
            FormatRow(
                IMAGES,
                tr("Images (ZIP)"),
                tr("One PNG image per page, downloaded together as a ZIP file."),
                self,
            ),
            FormatRow(
                HTML,
                tr("Web page (HTML)"),
                tr("An interactive page you can step through page by page."),
                self,
            ),
        )
        group = QButtonGroup(self)
        for row in self.rows:
            group.addButton(row.radio)
            column.addWidget(row)
            row.radio.toggled.connect(self._update_export_button)
            row.switch.clicked.connect(self._on_switch)
        self.buttons = QDialogButtonBox(self)
        self.export_button = self.buttons.addButton(
            tr("Export"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        column.addWidget(self.buttons)
        self.rows[0].radio.setChecked(True)
        self.refresh()

    def refresh(self) -> None:
        status = self._status()
        ready = status.state is SeriesState.READY
        if ready and status.sheets_per_page > 1:
            scope = plural(
                status.sheets, "Exports all {n} sheet.", "Exports all {n} sheets."
            )
        elif ready:
            scope = plural(
                status.pages, "Exports all {n} page.", "Exports all {n} pages."
            )
        else:
            scope = status.message
        self.scope.setText(scope)
        problems = " ".join(self._saved_problems())
        self.rows[0].show_operation(status.pdf)
        self.rows[1].show_operation(status.images, problems)
        self.rows[2].show_operation(status.html, problems)
        self._pending = tuple(ref.item_id for ref in status.images.needs_interactive)
        # A format that became unavailable cannot stay chosen.
        for row in self.rows:
            if row.radio.isChecked() and not row.radio.isEnabled():
                self.rows[0].radio.setChecked(self.rows[0].radio.isEnabled())
        self._update_export_button()

    def _on_switch(self) -> None:
        self._switch(self._pending)
        self.refresh()

    def _update_export_button(self) -> None:
        chosen = self.chosen_format()
        self.export_button.setEnabled(chosen is not None)

    def chosen_format(self) -> str | None:
        for row in self.rows:
            if row.radio.isChecked() and row.radio.isEnabled():
                return row.key
        return None
