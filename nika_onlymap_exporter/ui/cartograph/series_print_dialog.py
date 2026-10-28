"""The way to print a series: through QGIS, or from the exported web page.

Printing through QGIS is the normal choice: the pages are drawn by QGIS as
the PDF is, whatever each map's Interactive/Image mode, and the system print
dialog follows. Printing from the web page stays available beside it; only
that one needs the series' maps to be Interactive, and says so beside its own
choice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QWidget,
)

from ...core.atlas_series import SeriesState, SeriesStatus
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from .series_export_dialog import FormatRow

QGIS, WEB = "qgis", "web"


class SeriesPrintDialog(QDialog):
    """Choose how to print the whole series."""

    def __init__(
        self,
        status: Callable[[], SeriesStatus],
        switch_to_interactive: Callable[[Sequence[str]], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Print series"))
        self._status = status
        self._switch = switch_to_interactive
        column = QVBoxLayout(self)
        self.scope = FittedLabel("", self)
        column.addWidget(self.scope)
        self.rows = (
            FormatRow(
                QGIS,
                tr("Print with QGIS"),
                tr("All pages in order, drawn by QGIS as in the PDF."),
                self,
            ),
            FormatRow(
                WEB,
                tr("Print from the web page"),
                tr("Opens the exported page, which your browser prints."),
                self,
            ),
        )
        group = QButtonGroup(self)
        for row in self.rows:
            group.addButton(row.radio)
            column.addWidget(row)
            row.radio.toggled.connect(self._update_print_button)
            row.switch.clicked.connect(self._on_switch)
        self.buttons = QDialogButtonBox(self)
        self.print_button = self.buttons.addButton(
            tr("Print"), QDialogButtonBox.ButtonRole.AcceptRole
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
                status.sheets, "Prints all {n} sheet.", "Prints all {n} sheets."
            )
        elif ready:
            scope = plural(
                status.pages, "Prints all {n} page.", "Prints all {n} pages."
            )
        else:
            scope = status.message
        self.scope.setText(scope)
        self.rows[0].show_operation(status.native_print)
        self.rows[1].show_operation(status.print)
        self._pending = tuple(ref.item_id for ref in status.print.needs_interactive)
        for row in self.rows:
            if row.radio.isChecked() and not row.radio.isEnabled():
                self.rows[0].radio.setChecked(self.rows[0].radio.isEnabled())
        self._update_print_button()

    def _on_switch(self) -> None:
        self._switch(self._pending)
        self.refresh()

    def _update_print_button(self) -> None:
        self.print_button.setEnabled(self.chosen_way() is not None)

    def chosen_way(self) -> str | None:
        for row in self.rows:
            if row.radio.isChecked() and row.radio.isEnabled():
                return row.key
        return None
