"""The Colour vision dialog behind the corner's Accessibility button (issue #4 §1).

One non-modal tool window does simulate *and* check: pick a condition and the
embedded layout preview shows it at once, with the table of every legend class
and its simulated colour and the classes it collapses into. Apply keeps it on
in the plugin's previews, also puts it on the QGIS canvas if that box is
ticked, and, if asked, reopens the browser preview with it. Closing the dialog
discards what was not applied; what was applied is remembered per user.

The preview, the canvas and the browser use different models, and that is
deliberate. The preview and the canvas are filtered by QGIS's own
`QgsPreviewEffect` (a display effect: symbology and exports keep their
colours); the table and the browser preview use the cartograph runtime's
matrices (`core.cvd`), because those are what the printed page and its own
validator will be judged by. The views are a look; the table is the verdict.

Everything outside the dialog arrives as a callable, so the controller owns
the wiring and the tests drive the real widgets: `entries_provider` returns
the legend classes of the last saved cartograph, `open_preview` reopens the
browser preview with a runtime `cvd` value (or `None` for normal vision).
`preview_view` is the embedded layout view the simulation shows in first.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, cast

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSettings, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QCloseEvent,
    QColor,
    QHideEvent,
    QIcon,
    QPainter,
    QPixmap,
    QShowEvent,
)
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
from ..i18n import N_, tr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgsLayoutView, QgsMapCanvas

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

# The applied simulation and whether it also went to the QGIS canvas, per user.
CONDITION_SETTING = "qgis2webmap/cartographColourVision"
CANVAS_SETTING = "qgis2webmap/cartographColourVisionCanvas"
# Where the canvas's own preview mode is parked while this plugin's simulation
# is on it, so a dialog built later (the language switch rebuilds the whole
# window) still gives the user's mode back instead of keeping ours.
CANVAS_ORIGINAL_PROPERTY = "q2wColourVisionOriginal"

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
        "protanopia": QgsPreviewEffect.PreviewMode.PreviewProtanope,
        "deuteranopia": QgsPreviewEffect.PreviewMode.PreviewDeuteranope,
        "tritanopia": QgsPreviewEffect.PreviewMode.PreviewTritanope,
        "achromatopsia": QgsPreviewEffect.PreviewMode.PreviewGrayscale,
    }[condition]


def _enum_number(mode: object) -> int:
    """A preview mode's number: a sip enum (Qt 5) is an int already, a Python
    enum (Qt 6) carries it as `.value`."""
    return int(getattr(mode, "value", mode))  # type: ignore[call-overload]


def stored_condition() -> Condition | None:
    """The applied simulation remembered from the last session (None: normal
    vision, or nothing usable stored)."""
    value = str(QSettings().value(CONDITION_SETTING, "", type=str))
    return cast("Condition", value) if value in CONDITION_NAMES else None


def stored_canvas_choice() -> bool:
    """Whether Apply was last set to reach the QGIS canvas too."""
    return bool(QSettings().value(CANVAS_SETTING, False, type=bool))


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


class AccessibilityDialog(QDialog):
    """Simulate a colour-vision condition and check the legend against it.

    Choosing a condition changes the embedded layout preview and the table,
    nothing else. `Apply` keeps it on in the plugin's previews (and on the
    QGIS canvas too if that box is ticked, and in the browser preview if that
    one is) and keeps the dialog open; closing it, by Close, Escape or the
    window, goes back to the last applied state. The applied state is stored
    per user and put back when the dialog is next built.

    `mode_changed` carries the applied condition, or `""` for normal vision,
    so the corner button can stay checked with a tooltip while a simulation
    is on. The plugin calls `reset_canvas()` on unload.
    """

    mode_changed = pyqtSignal(str)

    def __init__(
        self,
        entries_provider: Callable[[], Sequence[LegendEntry]],
        *,
        canvas: QgsMapCanvas | None = None,
        preview_view: QgsLayoutView | None = None,
        open_preview: Callable[[str | None], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries_provider = entries_provider
        self._canvas = canvas
        self._preview_view = preview_view
        self._open_preview = open_preview
        self._entries: tuple[LegendEntry, ...] = ()
        self._lint = LegendLint()
        # The simulation last applied (None: normal vision), and whether it
        # also went to the canvas: what the previews show and what closing the
        # dialog goes back to. Remembered from the last session.
        self._applied: Condition | None = stored_condition()
        self._applied_canvas = stored_canvas_choice()
        # The canvas's own preview mode before this dialog first changed it
        # (enabled, mode), so a user who chose one from View > Preview Mode
        # keeps it, and gets it back when the simulation is cleared.
        self._canvas_original: tuple[bool, object] | None = None

        self.setWindowTitle(tr("Colour vision"))
        # An ordinary modeless dialog window over its parent, not a Tool window
        # (those hide themselves when the application is deactivated). It is
        # one owned instance: Close, Escape and the window's own close hide it
        # and drop what was not applied, and the corner button shows it again.
        self.setModal(False)
        # True only while `present` unmaps and re-maps the window.
        self._remapping = False
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

        # Unchecked every time the dialog opens: a browser tab appears only
        # when the user asks for it, on Apply.
        self.browser_check = QCheckBox(tr("Also open browser preview when applying"))
        self.browser_check.setChecked(False)
        if open_preview is None:
            self.browser_check.setEnabled(False)
            self.browser_check.setToolTip(
                tr("There is no cartograph to preview in the browser.")
            )
        else:
            self.browser_check.setToolTip(
                tr("Reopen the cartograph in your browser with the same simulation.")
            )
        layout.addWidget(self.browser_check)

        # The one thing the table cannot say for itself when it is empty.
        self.empty_label = FittedLabel(
            tr(
                "No legend classes to check yet. Save the cartograph with an "
                "Interactive map frame first."
            )
        )
        layout.addWidget(self.empty_label)

        # Where Apply goes besides the plugin's own previews.
        self.canvas_check = QCheckBox(tr("Also apply to the QGIS map canvas"))
        self.canvas_check.setToolTip(
            tr("Show this simulation on the main QGIS window's map as well.")
        )
        self.canvas_check.setChecked(self._applied_canvas)
        layout.addWidget(self.canvas_check)

        buttons = QHBoxLayout()
        self.close_button = QPushButton(tr("Close"))
        self.apply_button = QPushButton(tr("Apply"))
        self.apply_button.setToolTip(
            tr("Keep this simulation on in the plugin's previews.")
        )
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.canvas_check.toggled.connect(self._update_apply_button)
        self.close_button.clicked.connect(self.close)
        self.apply_button.clicked.connect(self.apply)

        self.set_condition(self._applied)
        self.refresh()
        self._update_apply_button()
        # What was applied last session is on again before the dialog is ever
        # opened: the previews, and the canvas if that was chosen.
        self._show_in_preview(self._applied)
        if self._applied_canvas:
            self._apply_canvas(self._applied)

    # ---- State -------------------------------------------------------------

    def current_condition(self) -> Condition | None:
        """The condition selected now, applied or not."""
        value = self.mode_combo.currentData()
        return cast("Condition", value) if value else None

    def applied_condition(self) -> Condition | None:
        """The condition last applied (None: normal vision)."""
        return self._applied

    def applied_to_canvas(self) -> bool:
        """Whether the applied simulation is on the QGIS canvas."""
        return self._applied is not None and self._applied_canvas

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

    # ---- Preview, Apply, discard -------------------------------------------

    def _on_mode_changed(self, _index: int) -> None:
        self._show_in_preview(self.current_condition())
        self._fill_table()
        self._update_apply_button()

    def _update_apply_button(self, *_args: object) -> None:
        condition = self.current_condition()
        # The box alone changes nothing while there is no simulation to send.
        canvas_changed = (
            condition is not None
            and self.canvas_check.isChecked() != self._applied_canvas
        )
        self.apply_button.setEnabled(condition != self._applied or canvas_changed)

    def _show_in_preview(self, condition: Condition | None) -> None:
        """The embedded layout preview, filtered as a reader would see it."""
        view = self._preview_view
        if view is None or sip.isdeleted(view):
            return
        if condition is None:
            view.setPreviewModeEnabled(False)
            return
        view.setPreviewModeEnabled(True)
        view.setPreviewMode(preview_mode_for(condition))

    def apply(self) -> None:
        """Make the selected simulation the applied one: kept in the plugin's
        previews, on the canvas if that box is ticked, and in the browser
        preview if that box is. The dialog stays, and the choice is
        remembered."""
        condition = self.current_condition()
        to_canvas = self.canvas_check.isChecked()
        self._applied = condition
        self._applied_canvas = to_canvas
        self._apply_canvas(condition if to_canvas else None)
        self._remember(condition, to_canvas)
        self._update_apply_button()
        if self.browser_check.isChecked() and self._open_preview is not None:
            self._open_preview(condition)
        self.mode_changed.emit(condition or "")

    @staticmethod
    def _remember(condition: Condition | None, to_canvas: bool) -> None:
        settings = QSettings()
        settings.setValue(CONDITION_SETTING, condition or "")
        settings.setValue(CANVAS_SETTING, to_canvas)

    def _discard_unapplied(self) -> None:
        """Back to the last applied simulation (normal vision if none)."""
        self.set_condition(self._applied)
        self.canvas_check.setChecked(self._applied_canvas)

    def present(self) -> None:
        """Show the window and put it in front of its parent.

        On Wayland an application cannot raise or activate its own window
        (`raise_` and `activateWindow` ask the compositor and are ignored), so
        a window left behind its parent could never be brought back. Hiding
        and showing the same window maps it afresh, in front; what was chosen
        and not yet applied stays as it is.
        """
        if self.isVisible():
            self._remapping = True
            try:
                self.hide()
                self.show()
            finally:
                self._remapping = False
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 - Qt override
        self._discard_unapplied()
        super().closeEvent(event)

    def reject(self) -> None:
        # Escape and the Close button end up here.
        self._discard_unapplied()
        super().reject()

    def hideEvent(self, event: QHideEvent | None) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        # Hidden by anything but the window system minimising it (its parent
        # going away, a stray hide()): that is leaving, so what was not applied
        # goes. `present` hides and shows in one go and keeps it.
        if event is not None and not event.spontaneous() and not self._remapping:
            self._discard_unapplied()

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if event is not None and not event.spontaneous() and not self._remapping:
            self.browser_check.setChecked(False)

    # ---- Canvas ------------------------------------------------------------

    def _canvas_original_state(
        self, canvas: QgsMapCanvas
    ) -> tuple[bool, object] | None:
        """The canvas's own preview mode from before any simulation of ours,
        if one is parked on it (by this dialog or an earlier one)."""
        if self._canvas_original is not None:
            return self._canvas_original
        parked = canvas.property(CANVAS_ORIGINAL_PROPERTY)
        if not parked:
            return None
        from qgis.gui import QgsPreviewEffect

        enabled, _, mode = str(parked).partition(":")
        try:
            return enabled == "1", QgsPreviewEffect.PreviewMode(int(mode))
        except (TypeError, ValueError):
            return None

    def _apply_canvas(self, condition: Condition | None) -> None:
        canvas = self._canvas
        if canvas is None or sip.isdeleted(canvas):
            return
        original = self._canvas_original_state(canvas)
        if condition is None:
            if original is not None:
                enabled, mode = original
                canvas.setPreviewModeEnabled(enabled)
                if enabled:
                    canvas.setPreviewMode(mode)
                self._canvas_original = None
                canvas.setProperty(CANVAS_ORIGINAL_PROPERTY, None)
            return
        if original is None:
            original = (canvas.previewModeEnabled(), canvas.previewMode())
            canvas.setProperty(
                CANVAS_ORIGINAL_PROPERTY,
                f"{int(original[0])}:{_enum_number(original[1])}",
            )
        self._canvas_original = original
        canvas.setPreviewModeEnabled(True)
        canvas.setPreviewMode(preview_mode_for(condition))

    def reset_canvas(self) -> None:
        """Give the canvas back the preview mode it had before this dialog
        changed it. The plugin calls this on unload."""
        self._apply_canvas(None)
        self._show_in_preview(None)

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

        self.empty_label.setVisible(not self._entries)

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
