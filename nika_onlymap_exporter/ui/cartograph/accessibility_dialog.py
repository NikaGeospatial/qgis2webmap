"""The Colour vision dialog behind the corner's Accessibility button (issue #4 §1).

One non-modal tool window does simulate *and* check: pick a condition and the
QGIS canvas switches to that preview mode, the browser preview reopens with
the same simulation, and the table shows every legend class with its
simulated colour and the classes it collapses into.

The two halves use different models, and that is deliberate. The canvas is
filtered by QGIS's own `QgsPreviewEffect`, the only way to filter a live
canvas; the table and the browser preview use the cartograph runtime's
matrices (`core.cvd`), because those are what the printed page and its own
validator will be judged by. The canvas is a look; the table is the verdict.

Everything outside the dialog arrives as a callable, so the controller owns
the wiring and the tests drive the real widgets: `entries_provider` returns
the legend classes of the last saved cartograph, `open_preview` reopens the
browser preview with a runtime `cvd` value (or `None` for normal vision), and
`ask_nika` receives the findings text for the hand-off dialog.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.cvd import (
    Condition,
    LegendEntry,
    LegendLint,
    hex_colour,
    lint_legend_colours,
    parse_colour,
    simulate,
)
from ..fitted_label import FittedLabel
from ..i18n import N_, plural, tr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgsMapCanvas

# What each condition is called on screen, in the combo's order. Grayscale
# leads its own label because that is QGIS's word for the same preview, and
# the name a user looking for it in View > Preview Mode already knows.
CONDITION_NAMES: dict[Condition, str] = {
    "protanopia": N_("Protanopia"),
    "deuteranopia": N_("Deuteranopia"),
    "tritanopia": N_("Tritanopia"),
    "achromatopsia": N_("Grayscale (achromatopsia)"),
}

# The same names mid-sentence ("Simulating deuteranopia").
CONDITION_WORDS: dict[Condition, str] = {
    "protanopia": N_("protanopia"),
    "deuteranopia": N_("deuteranopia"),
    "tritanopia": N_("tritanopia"),
    "achromatopsia": N_("achromatopsia"),
}

COLUMN_LAYER, COLUMN_CLASS, COLUMN_COLOUR, COLUMN_SIMULATED, COLUMN_NOTE = range(5)
SWATCH_SIZE = 14
NAME_COLUMN_MAX = 120


def preview_mode_for(condition: Condition) -> object:
    """The `QgsPreviewEffect.PreviewMode` that shows `condition` on the canvas.

    All four exist on every QGIS this plugin supports - Tritanope since 3.22,
    and `qgisMinimumVersion` is 3.44 - so no option has to be hidden. Looked up
    on the class rather than the scoped enum, which is the spelling both
    PyQt5 (QGIS 3) and PyQt6 (QGIS 4) accept.
    """
    from qgis.gui import QgsPreviewEffect

    return {
        "protanopia": QgsPreviewEffect.PreviewProtanope,
        "deuteranopia": QgsPreviewEffect.PreviewDeuteranope,
        "tritanopia": QgsPreviewEffect.PreviewTritanope,
        "achromatopsia": QgsPreviewEffect.PreviewGrayscale,
    }[condition]


def simulating_tooltip(condition: Condition | None) -> str:
    """The corner button's tooltip: what is being simulated, if anything."""
    if condition is None:
        return tr("Colour vision: simulate and check the legend colours.")
    return tr("Simulating {condition}").format(condition=tr(CONDITION_WORDS[condition]))


def swatch_icon(colour: str) -> QIcon:
    """A small filled square with a hairline border, so a white or very pale
    class still reads as a swatch rather than a gap in the table."""
    pixmap = QPixmap(SWATCH_SIZE, SWATCH_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setPen(QColor(0, 0, 0, 110))
        painter.setBrush(QColor(colour))
        painter.drawRect(0, 0, SWATCH_SIZE - 1, SWATCH_SIZE - 1)
    finally:
        painter.end()
    return QIcon(pixmap)


def _display_colour(entry: LegendEntry) -> str:
    rgb = parse_colour(entry.color)
    return hex_colour(rgb) if rgb is not None else entry.color


def findings_text(lint: LegendLint) -> str:
    """The findings, as a plain list for the NIKA hand-off prompt.

    One line per collapsing pair, naming every condition it collapses under
    and the closest distance, so the agent can tell a near miss from a pair
    that has become one colour - and knows the fix must clear 10.
    """
    lines = []
    for pair in lint.pairs():
        conditions = ", ".join(tr(CONDITION_WORDS[c]) for c in pair.conditions)
        lines.append(
            tr(
                "- Layer '{layer}': '{first}' ({first_colour}) and '{second}' "
                "({second_colour}) collapse under {conditions} "
                "(CIEDE2000 {distance:.1f}, below 10)."
            ).format(
                layer=pair.a.display_layer,
                first=pair.a.label,
                first_colour=_display_colour(pair.a),
                second=pair.b.label,
                second_colour=_display_colour(pair.b),
                conditions=conditions,
                distance=pair.distance,
            )
        )
    return "\n".join(lines)


class AccessibilityDialog(QDialog):
    """Simulate a colour-vision condition and check the legend against it.

    `mode_changed` carries the active condition, or `""` for normal vision,
    so the corner button can stay checked with a tooltip while a simulation
    is on. Closing the dialog leaves the canvas as the user set it; the plugin
    calls `reset_canvas()` on unload.
    """

    mode_changed = pyqtSignal(str)

    def __init__(
        self,
        entries_provider: Callable[[], Sequence[LegendEntry]],
        *,
        canvas: QgsMapCanvas | None = None,
        open_preview: Callable[[str | None], None] | None = None,
        ask_nika: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries_provider = entries_provider
        self._canvas = canvas
        self._open_preview = open_preview
        self._ask_nika = ask_nika
        self._entries: tuple[LegendEntry, ...] = ()
        self._lint = LegendLint()
        # Whether WE put the canvas into a preview mode. A user who chose one
        # from View > Preview Mode before opening this dialog keeps it: only a
        # mode this dialog set is ever undone by it.
        self._canvas_driven = False

        self.setWindowTitle(tr("Colour vision"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        self.setModal(False)
        self.setMinimumWidth(360)
        self.resize(520, 520)

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        simulate_label = QLabel(tr("&Simulate"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("Normal vision"), "")
        for condition, name in CONDITION_NAMES.items():
            self.mode_combo.addItem(tr(name), condition)
        self.mode_combo.setToolTip(
            tr("How a reader with this colour-vision condition sees the map.")
        )
        simulate_label.setBuddy(self.mode_combo)
        row.addWidget(simulate_label)
        row.addWidget(self.mode_combo, stretch=1)
        layout.addLayout(row)

        targets = QHBoxLayout()
        self.canvas_check = QCheckBox(tr("QGIS canvas"))
        self.canvas_check.setChecked(True)
        self.browser_check = QCheckBox(tr("Browser preview"))
        self.browser_check.setChecked(True)
        if canvas is None:
            self.canvas_check.setChecked(False)
            self.canvas_check.setEnabled(False)
            self.canvas_check.setToolTip(tr("The QGIS map canvas is not available."))
        else:
            self.canvas_check.setToolTip(
                tr("Filter the QGIS map canvas, as View > Preview Mode does.")
            )
        if open_preview is None:
            self.browser_check.setChecked(False)
            self.browser_check.setEnabled(False)
            self.browser_check.setToolTip(
                tr("There is no cartograph to preview in the browser.")
            )
        else:
            self.browser_check.setToolTip(
                tr("Reopen the cartograph in your browser with the same simulation.")
            )
        targets.addWidget(self.canvas_check)
        targets.addWidget(self.browser_check)
        targets.addStretch(1)
        layout.addLayout(targets)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Layer"), tr("Class"), tr("Colour"), tr("Simulated"), tr("Note")]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setIconSize(QSize(SWATCH_SIZE, SWATCH_SIZE))
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for column in (COLUMN_COLOUR, COLUMN_SIMULATED):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        # Names fit their text up to a cap and are user-resizable beyond it, so
        # one long class name cannot squeeze Note out of a narrow dialog.
        for column in (COLUMN_LAYER, COLUMN_CLASS):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COLUMN_NOTE, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=1)

        self.summary_label = FittedLabel()
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        self.ask_button = QPushButton(tr("Ask NIKA to fix ↗"))
        self.close_button = QPushButton(tr("Close"))
        buttons.addWidget(self.ask_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.canvas_check.toggled.connect(self._on_canvas_toggled)
        self.browser_check.toggled.connect(self._on_browser_toggled)
        self.ask_button.clicked.connect(self._on_ask_nika)
        self.close_button.clicked.connect(self.close)

        self.refresh()

    # ---- State -------------------------------------------------------------

    def current_condition(self) -> Condition | None:
        value = self.mode_combo.currentData()
        return value if value else None

    def set_condition(self, condition: Condition | None) -> None:
        """Select `condition` (None for normal vision), as the combo would."""
        index = self.mode_combo.findData(condition or "")
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    @property
    def lint(self) -> LegendLint:
        return self._lint

    def refresh(self) -> None:
        """Re-read the legend classes and re-run the check. Call after every
        cartograph save, so the table describes the file as it now is."""
        self._entries = tuple(self._entries_provider())
        self._lint = lint_legend_colours(self._entries)
        self._fill_table()

    # ---- Targets -----------------------------------------------------------

    def _on_mode_changed(self, _index: int) -> None:
        condition = self.current_condition()
        if self.canvas_check.isChecked():
            self._apply_canvas(condition)
        if self.browser_check.isChecked() and self._open_preview is not None:
            self._open_preview(condition)
        self._fill_table()
        self.mode_changed.emit(condition or "")

    def _on_canvas_toggled(self, checked: bool) -> None:
        # Unticking stops filtering the canvas now; leaving a filter the user
        # just switched off would be the dialog contradicting its own control.
        self._apply_canvas(self.current_condition() if checked else None)

    def _on_browser_toggled(self, checked: bool) -> None:
        # Ticking it shows the current simulation straight away. Unticking
        # opens nothing: a tab appearing because a box was cleared is noise.
        if checked and self._open_preview is not None and self.current_condition():
            self._open_preview(self.current_condition())

    def _apply_canvas(self, condition: Condition | None) -> None:
        if self._canvas is None:
            return
        if condition is None:
            if self._canvas_driven:
                self._canvas.setPreviewModeEnabled(False)
                self._canvas_driven = False
            return
        self._canvas.setPreviewModeEnabled(True)
        self._canvas.setPreviewMode(preview_mode_for(condition))
        self._canvas_driven = True

    def reset_canvas(self) -> None:
        """Turn off a canvas preview this dialog switched on. The plugin calls
        this on unload; closing the dialog deliberately does not."""
        self._apply_canvas(None)

    # ---- Table -------------------------------------------------------------

    def _fill_table(self) -> None:
        condition = self.current_condition()
        relevant = self._lint.under(condition) if condition else self._lint.collapses
        partners: dict[LegendEntry, list[str]] = {}
        for collapse in relevant:
            suffix = (
                ""
                if condition
                else tr(" ({condition})").format(
                    condition=tr(CONDITION_WORDS[collapse.condition])
                )
            )
            partners.setdefault(collapse.a, []).append(collapse.b.label + suffix)
            partners.setdefault(collapse.b, []).append(collapse.a.label + suffix)

        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            rgb = parse_colour(entry.color)
            simulated = (
                hex_colour(simulate(rgb, condition))
                if rgb is not None and condition
                else (hex_colour(rgb) if rgb is not None else "")
            )
            notes = partners.get(entry)
            if rgb is None:
                note = tr("not checked - colour not readable")
            elif notes:
                note = ", ".join(
                    tr("like {other}").format(other=other)
                    for other in dict.fromkeys(notes)
                )
            else:
                note = tr("ok")

            colour = hex_colour(rgb) if rgb is not None else entry.color
            cells = {
                COLUMN_LAYER: QTableWidgetItem(entry.display_layer),
                COLUMN_CLASS: QTableWidgetItem(entry.label),
                COLUMN_COLOUR: self._swatch_item(colour, rgb is not None),
                COLUMN_SIMULATED: self._swatch_item(simulated, bool(simulated)),
                COLUMN_NOTE: QTableWidgetItem(note),
            }
            for column, item in cells.items():
                if not item.toolTip():
                    item.setToolTip(item.text())
                self.table.setItem(row, column, item)

        for column in (COLUMN_LAYER, COLUMN_CLASS):
            self.table.resizeColumnToContents(column)
            self.table.setColumnWidth(
                column, min(self.table.columnWidth(column), NAME_COLUMN_MAX)
            )

        self.summary_label.setText(self._summary(condition))
        self._update_ask_button()

    @staticmethod
    def _swatch_item(colour: str, paint: bool) -> QTableWidgetItem:
        """A swatch with its hex in the tooltip and for screen readers, not
        as text: two columns of hex codes pushed the Note column - the one
        that matters - out of a dialog this narrow."""
        item = QTableWidgetItem()
        if paint:
            item.setIcon(swatch_icon(colour))
        elif colour:
            item.setText("?")
        item.setToolTip(colour)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, colour)
        return item

    def _summary(self, condition: Condition | None) -> str:
        if not self._entries:
            return tr(
                "No legend classes to check yet. Save the cartograph with a "
                "Interactive map frame first."
            )
        if condition:
            count = len(self._lint.under(condition))
            word = tr(CONDITION_WORDS[condition])
            if not count:
                return tr("All classes stay distinguishable under {condition}.").format(
                    condition=word
                )
            return plural(
                count,
                "{n} pair of classes collapses under {condition}.",
                "{n} pairs of classes collapse under {condition}.",
                condition=word,
            )
        pairs = len(self._lint.pairs())
        if not pairs or self._lint.worst_condition is None:
            return tr("All classes stay distinguishable under every condition.")
        return plural(
            pairs,
            "{n} pair of classes collapses for some readers; most under {condition}.",
            "{n} pairs of classes collapse for some readers; most under {condition}.",
            condition=tr(CONDITION_WORDS[self._lint.worst_condition]),
        )

    # ---- NIKA --------------------------------------------------------------

    def _update_ask_button(self) -> None:
        if self._ask_nika is None:
            self.ask_button.setEnabled(False)
            self.ask_button.setToolTip(tr("Not available for cartographs yet"))
        elif not self._lint.collapses:
            self.ask_button.setEnabled(False)
            self.ask_button.setToolTip(tr("Nothing to fix: no classes collapse."))
        else:
            self.ask_button.setEnabled(True)
            self.ask_button.setToolTip(
                tr("Hand these findings to NIKA and ask for a safer palette.")
            )

    def _on_ask_nika(self) -> None:
        if self._ask_nika is not None and self._lint.collapses:
            self._ask_nika(findings_text(self._lint))
