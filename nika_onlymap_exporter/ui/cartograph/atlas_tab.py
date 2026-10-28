"""The Cartograph output's Atlas tab: "Create a map series", one page per feature.

The series is the print layout's own atlas (`QgsLayoutAtlas`), so this tab edits
the layout - the same settings QGIS's Atlas panel shows - and every change goes
on the layout's undo stack, where the layout designer's Undo takes it back. The
cartograph follows on the next autosave like any other layout edit.

The tab is a short path: choose what receives a page, preview the series in the
page beside the panel, export or print it. Nothing in that path asks for an
expression or for knowledge of QGIS's atlas panel; the rest of QGIS's atlas
settings sit in two closed sections underneath, and open by themselves when one
of them is set or wrong.

The preview is the page itself: moving through the series seeks QGIS's atlas
(`core/atlas_preview.py`), so the maps, labels and scale bars of the main
renderer change. It runs only while this tab is on screen and puts the layout
back when it is not. Readiness is decided per output (`core/atlas_series.py`):
the saved cartograph's page count gates nothing here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from qgis.core import (
    QgsLayoutItemMap,
    QgsLayoutUndoObjectInterface,
    QgsMapLayerProxyModel,
    QgsPrintLayout,
)
from qgis.gui import QgsFieldExpressionWidget, QgsMapLayerComboBox
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QHideEvent, QShowEvent
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.atlas_preview import AtlasPreview
from ...core.atlas_series import (
    Operation,
    SeriesState,
    SeriesStatus,
    driven_maps,
    feature_summary,
    layer_problem,
    layout_maps,
    map_ref,
    series_status,
)
from ...core.layout_reader import expression_error
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from ..theme import PANEL_PANE_GAP
from .item_controls import SELECTED_HEADER_CLEARANCE
from .panel_section import PanelSection

if TYPE_CHECKING:  # pragma: no cover - typing only
    # QtWidgets on Qt 5, QtGui on Qt 6.
    from qgis.PyQt.QtGui import QUndoStack

#: The margin a map gets when this tab first makes it follow the atlas: QGIS's
#: own default in the Item Properties panel.
DEFAULT_ATLAS_MARGIN = 0.1

#: The ways a map frames each page, in the order the tab lists them.
_FRAMING = (
    (QgsLayoutItemMap.AtlasScalingMode.Auto, "Fit area with margin"),
    (QgsLayoutItemMap.AtlasScalingMode.Fixed, "Fixed scale"),
    (QgsLayoutItemMap.AtlasScalingMode.Predefined, "Predefined scales"),
)


@dataclass
class AtlasHost:
    """What the tab asks of the Cartograph controller."""

    # The map's "live" / "static" choice from the Map frames list.
    frame_mode: Callable[[str], str]
    # Switch these maps to Interactive, as one undoable step.
    switch_to_interactive: Callable[[Sequence[str]], None]
    # Save the series as one PDF through QGIS itself.
    export_pdf: Callable[[], None]
    # Open the saved cartograph on its pages: "zip", "print" or "html".
    open_pages: Callable[[str], None]
    # The existing layout-template route, for a layout with no map.
    browse_templates: Callable[[], None]


class AtlasTab(QWidget):
    """Mirrors and edits one layout's series. `changed` fires after an edit."""

    changed = pyqtSignal()

    def __init__(self, host: AtlasHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._layout: QgsPrintLayout | None = None
        self._loading = False
        # True while this tab pushes its own undo command, so only an Undo or
        # Redo made elsewhere (Ctrl+Z, the layout designer) re-reads the atlas.
        self._editing = False
        self._watched_stack: QUndoStack | None = None
        # The atlas as last shown or edited here; an undo-stack move that
        # leaves it alone (another tab's own command) is not an atlas change.
        self._shown: tuple[object, ...] = ()
        self._status = SeriesStatus(SeriesState.OFF)
        # Problems the last save reported for the atlas (cartograph outputs).
        self._saved_problems: tuple[str, ...] = ()
        self._filter_error: str | None = None
        self._sort_error: str | None = None
        # A coverage layer taken out of the project under an enabled series.
        self._layer_removed = False
        # Advanced fields whose error has already been scrolled into view.
        self._revealed: set[str] = set()
        # The coverage layer the series was last seen on.
        self._coverage_id = ""
        self._preview: AtlasPreview | None = None
        self._page_index = 0
        self._page_count = 0
        self._page_feature: int | None = None
        # Counts every change of setup, so a result prepared for an older one
        # is dropped instead of shown over the newer.
        self._generation = 0
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.setInterval(0)
        self._prepare_timer.timeout.connect(self._run_prepare)

        page = QVBoxLayout(self)
        # The first heading sits as far below the tab underline as the Edit
        # tab's header does; the panel's pane already gives PANEL_PANE_GAP.
        margins = page.contentsMargins()
        page.setContentsMargins(
            margins.left(),
            SELECTED_HEADER_CLEARANCE - PANEL_PANE_GAP,
            margins.right(),
            margins.bottom(),
        )
        heading = QLabel(tr("Create a map series"), self)
        heading.setProperty("role", "panelHeading")
        page.addWidget(heading)
        self.intro_label = FittedLabel(
            tr("Use this layout to make a separate map for each area or location."),
            self,
        )
        page.addWidget(self.intro_label)

        self.enabled_check = QCheckBox(tr("Create a map series"), self)
        self.enabled_check.toggled.connect(self._on_enabled)
        page.addWidget(self.enabled_check)

        # No box: it sits in the panel's Atlas tab, under that name already.
        settings = QWidget(self)
        column = QVBoxLayout(settings)
        column.setContentsMargins(0, 4, 0, 0)
        self.settings_box = settings
        page.addWidget(settings)

        self._build_source(column, settings)
        self._build_map(column, settings)
        self._build_preview(column, settings)
        self._build_actions(column, settings)
        self._build_framing(column, settings)
        self._build_advanced(column, settings)
        page.addStretch(1)

        self.show_layout(None)

    # ---- Building ----------------------------------------------------------

    def _build_source(self, column: QVBoxLayout, parent: QWidget) -> None:
        self.source_label = FittedLabel(tr("Make one page for each feature in"), parent)
        column.addWidget(self.source_label)
        self.coverage_combo = QgsMapLayerComboBox(parent)
        self.coverage_combo.setFilters(
            QgsMapLayerProxyModel.Filter.PointLayer
            | QgsMapLayerProxyModel.Filter.LineLayer
            | QgsMapLayerProxyModel.Filter.PolygonLayer
        )
        self.coverage_combo.setAllowEmptyLayer(True, tr("Choose a layer…"))
        self.coverage_combo.setAccessibleName(tr("Layer that receives a page"))
        self.coverage_combo.layerChanged.connect(self._on_coverage)
        column.addWidget(self.coverage_combo)
        self.source_help = FittedLabel(
            tr("Each area or location in this layer becomes a page."), parent
        )
        self.source_help.setProperty("role", "hint")
        column.addWidget(self.source_help)
        # How many features the layer gives, or why it cannot be used.
        self.source_note = FittedLabel("", parent)
        column.addWidget(self.source_note)

    def _build_map(self, column: QVBoxLayout, parent: QWidget) -> None:
        # One map: a quiet line naming it.
        self.map_summary = FittedLabel("", parent)
        column.addWidget(self.map_summary)
        # Several maps: which one the pages update.
        self.map_chooser = QWidget(parent)
        chooser = QVBoxLayout(self.map_chooser)
        chooser.setContentsMargins(0, 0, 0, 0)
        chooser.setSpacing(4)
        chooser.addWidget(FittedLabel(tr("Map to update on each page"), parent))
        self.map_combo = QComboBox(self.map_chooser)
        self.map_combo.setPlaceholderText(tr("Choose a map…"))
        self.map_combo.setAccessibleName(tr("Map to update on each page"))
        self.map_combo.currentIndexChanged.connect(self._on_map)
        chooser.addWidget(self.map_combo)
        other = FittedLabel(tr("Other maps keep their current view."), parent)
        other.setProperty("role", "hint")
        chooser.addWidget(other)
        column.addWidget(self.map_chooser)
        # No map at all: say so and offer the way to get one.
        self.no_map = QWidget(parent)
        none = QVBoxLayout(self.no_map)
        none.setContentsMargins(0, 0, 0, 0)
        none.setSpacing(4)
        self.no_map_label = FittedLabel(
            tr("This layout needs a map to update on each page."), self.no_map
        )
        none.addWidget(self.no_map_label)
        self.templates_button = QPushButton(tr("Browse templates…"), self.no_map)
        self.templates_button.clicked.connect(lambda: self._host.browse_templates())
        none.addWidget(self.templates_button)
        column.addWidget(self.no_map)

    def _build_preview(self, column: QVBoxLayout, parent: QWidget) -> None:
        self.status_label = FittedLabel("", parent)
        self.status_label.setProperty("role", "panelHeading")
        column.addWidget(self.status_label)
        self.navigator = QWidget(parent)
        row = QHBoxLayout(self.navigator)
        row.setContentsMargins(0, 0, 0, 0)
        self.previous_button = QPushButton(tr("Previous"), self.navigator)
        self.previous_button.clicked.connect(
            lambda: self.go_to_page(self._page_index - 1)
        )
        row.addWidget(self.previous_button)
        self.position_label = FittedLabel("", self.navigator)
        row.addWidget(self.position_label, 1)
        self.next_button = QPushButton(tr("Next"), self.navigator)
        self.next_button.clicked.connect(lambda: self.go_to_page(self._page_index + 1))
        row.addWidget(self.next_button)
        column.addWidget(self.navigator)

    def _build_actions(self, column: QVBoxLayout, parent: QWidget) -> None:
        self.scope_label = FittedLabel("", parent)
        column.addWidget(self.scope_label)
        buttons = QVBoxLayout()
        self.export_button = QPushButton(tr("Export series…"), parent)
        self.export_button.clicked.connect(self._on_export)
        buttons.addWidget(self.export_button)
        self.print_button = QPushButton(tr("Print series…"), parent)
        self.print_button.clicked.connect(self._on_print)
        buttons.addWidget(self.print_button)
        column.addLayout(buttons)
        # Why printing is out of reach while exporting is not, with the fix.
        self.actions_note = FittedLabel("", parent)
        self._tone(self.actions_note)
        column.addWidget(self.actions_note)
        self.switch_button = QPushButton(tr("Switch to Interactive"), parent)
        self.switch_button.clicked.connect(self._on_switch_to_interactive)
        column.addWidget(self.switch_button)
        # A click gives no lingering focus ring; Tab still reaches them.
        for button in (
            self.previous_button,
            self.next_button,
            self.export_button,
            self.print_button,
            self.switch_button,
        ):
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _build_framing(self, column: QVBoxLayout, parent: QWidget) -> None:
        self.framing_section = PanelSection("atlas-framing", tr("Page framing"), parent)
        column.addWidget(self.framing_section)
        self.framing_combo = QComboBox(parent)
        for mode, label in _FRAMING:
            self.framing_combo.addItem(tr(label), int(mode))
        self.framing_combo.setAccessibleName(tr("Page framing"))
        self.framing_combo.currentIndexChanged.connect(self._on_framing)
        self.framing_section.body_layout.addWidget(self.framing_combo)

    def _build_advanced(self, column: QVBoxLayout, parent: QWidget) -> None:
        # What most series never touch; it opens by itself when something in
        # it is set or wrong.
        self.advanced_section = PanelSection(
            "atlas-advanced", tr("Advanced settings"), parent
        )
        column.addWidget(self.advanced_section)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        # Labels above their fields: the tab is a side panel a third of the
        # dialog wide, where label and field side by side ran off its edge.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.advanced_section.body_layout.addLayout(form)

        self.filter_edit = _typed_expression_widget(parent)
        self.filter_edit.setToolTip(
            tr("Only features for which this is true get a page. Blank: all.")
        )
        self.filter_edit.fieldChanged.connect(self._on_filter)
        self.filter_error_label = self._error_label(parent)
        form.addRow(
            tr("Include pages"),
            self._with_error(self.filter_edit, self.filter_error_label),
        )

        self.sort_edit = _typed_expression_widget(parent)
        self.sort_edit.setAllowEmptyFieldName(True)
        self.sort_edit.fieldChanged.connect(self._on_sort_expression)
        self.direction_combo = QComboBox(parent)
        self.direction_combo.addItem(tr("Ascending (A to Z, 1 to 9)"), True)
        self.direction_combo.addItem(tr("Descending (Z to A, 9 to 1)"), False)
        self.direction_combo.setAccessibleName(tr("Direction of the page order"))
        self.direction_combo.currentIndexChanged.connect(self._on_sort_direction)
        self.sort_error_label = self._error_label(parent)
        order = QWidget(parent)
        order_column = QVBoxLayout(order)
        order_column.setContentsMargins(0, 0, 0, 0)
        order_column.addWidget(self.sort_edit)
        order_column.addWidget(self.direction_combo)
        order_column.addWidget(self.sort_error_label)
        form.addRow(tr("Page order"), order)

        self.page_name_edit = _typed_expression_widget(parent)
        self.page_name_edit.fieldChanged.connect(self._on_page_name)
        self.page_name_error_label = self._error_label(parent)
        form.addRow(
            tr("Page names"),
            self._with_error(self.page_name_edit, self.page_name_error_label),
        )

        self.filename_edit = _typed_expression_widget(parent)
        self.filename_edit.setToolTip(
            tr("How each page's image is named when all pages are exported.")
        )
        self.filename_edit.fieldChanged.connect(self._on_filename)
        self.filename_error_label = self._error_label(parent)
        form.addRow(
            tr("Export filenames"),
            self._with_error(self.filename_edit, self.filename_error_label),
        )
        self._expression_widgets = (
            self.sort_edit,
            self.filter_edit,
            self.page_name_edit,
            self.filename_edit,
        )

    @staticmethod
    def _with_error(widget: QWidget, label: QLabel) -> QWidget:
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(widget)
        box.addWidget(label)
        return holder

    @staticmethod
    def _tone(label: FittedLabel) -> None:
        # The Checks panel's own warning colour; nothing else styles a label.
        label.setProperty("role", "checkVerdict")
        label.setProperty("tone", "warning")
        label.setVisible(False)

    @staticmethod
    def _error_label(parent: QWidget) -> FittedLabel:
        label = FittedLabel("", parent)
        AtlasTab._tone(label)
        return label

    # ---- Showing -----------------------------------------------------------

    def show_layout(self, layout: QgsPrintLayout | None) -> None:
        """Show `layout`'s series, without the display counting as an edit."""
        self._release_preview()
        self._layout = layout
        self._layer_removed = False
        self._page_index = 0
        self._page_feature = None
        self._watch_undo(layout)
        self._load()
        self._shown = _atlas_state(layout)
        self.refresh_now()

    def _load(self) -> None:
        """Put the layout's atlas settings into the controls."""
        layout = self._layout
        self._loading = True
        try:
            atlas = layout.atlas() if layout is not None else None
            self.enabled_check.setEnabled(layout is not None)
            self.enabled_check.setChecked(
                bool(atlas and (atlas.enabled() or self._layer_removed))
            )
            coverage = atlas.coverageLayer() if atlas is not None else None
            self.coverage_combo.setLayer(coverage)
            for widget in self._expression_widgets:
                widget.setLayer(coverage)
            if atlas is not None:
                self.sort_edit.setExpression(
                    atlas.sortExpression() if atlas.sortFeatures() else ""
                )
                # The stored direction, sorted or not, as QGIS shows it - and
                # only editable while there is an order for it to apply to.
                self.direction_combo.setCurrentIndex(0 if atlas.sortAscending() else 1)
                self.direction_combo.setEnabled(atlas.sortFeatures())
                self.filter_edit.setExpression(
                    atlas.filterExpression() if atlas.filterFeatures() else ""
                )
                self.page_name_edit.setExpression(atlas.pageNameExpression())
                self.filename_edit.setExpression(atlas.filenameExpression())
            self._fill_maps()
            self._load_framing()
        finally:
            self._loading = False

    def _fill_maps(self) -> None:
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        if self._layout is not None:
            driven = {map_ref(item).item_id for item in driven_maps(self._layout)}
            chosen = -1
            # Named and ordered as the Map frames list names them.
            for index, ref in enumerate(layout_maps(self._layout)):
                self.map_combo.addItem(ref.name, ref.item_id)
                if ref.item_id in driven and chosen < 0:
                    chosen = index
            self.map_combo.setCurrentIndex(chosen)
        self.map_combo.blockSignals(False)

    def _load_framing(self) -> None:
        layout = self._layout
        maps = driven_maps(layout) if layout is not None else []
        self.framing_combo.setEnabled(bool(maps))
        self.framing_combo.blockSignals(True)
        if maps:
            index = self.framing_combo.findData(int(maps[0].atlasScalingMode()))
            self.framing_combo.setCurrentIndex(max(index, 0))
        self.framing_combo.blockSignals(False)

    def rename_maps(self) -> None:
        """Show the maps' current names without rebuilding the list, so the
        choice (kept by item uuid) and the combo's focus stay as they are."""
        if self._layout is None:
            return
        names = {ref.item_id: ref.name for ref in layout_maps(self._layout)}
        for index in range(self.map_combo.count()):
            name = names.get(self.map_combo.itemData(index))
            if name is not None and name != self.map_combo.itemText(index):
                self.map_combo.setItemText(index, name)
        self._request_refresh()

    def show_outcome(self, problems: tuple[str, ...], pages: int | None) -> None:
        """What the last save made of the atlas for the cartograph's own
        outputs: why it does not page, if it does not. The native PDF and the
        preview do not depend on it, and neither does the saved page count
        (`pages`), which gates nothing."""
        del pages
        self._saved_problems = problems
        self._update_actions()

    def refresh_now(self) -> None:
        """Bring every part of the tab up to date with the layout, now."""
        self._prepare_timer.stop()
        self._generation += 1
        self._refresh()

    def _request_refresh(self) -> None:
        """Show "Preparing preview…" and refresh on the next event-loop turn."""
        self._generation += 1
        if self.enabled_check.isChecked():
            self.status_label.setText(tr("Preparing preview…"))
            self.status_label.setVisible(True)
            self.navigator.setVisible(False)
        self._prepare_timer.start()

    def _run_prepare(self) -> None:
        self._refresh()

    # ---- Refreshing --------------------------------------------------------

    def _refresh(self) -> None:
        layout = self._layout
        if layout is not None and sip.isdeleted(layout):
            self._layout = layout = None
        generation = self._generation
        self._validate_expressions()
        status = series_status(
            layout, self._host.frame_mode, layer_was_removed=self._layer_removed
        )
        if self._sort_error is not None or self._filter_error is not None:
            status = _invalid(status)
        if generation != self._generation:
            return  # a newer setup began while this one was prepared
        self._status = status
        if layout is not None:
            shown = layout.atlas().enabled() or self._layer_removed
            if self.enabled_check.isChecked() != shown:
                self._loading = True
                try:
                    self.enabled_check.setChecked(shown)
                finally:
                    self._loading = False
        coverage = layout.atlas().coverageLayer() if layout is not None else None
        if coverage is not None:
            self._coverage_id = coverage.id()
        self.settings_box.setVisible(self.enabled_check.isChecked())
        self._update_source(status)
        self._update_map(status)
        self._load_framing()
        self._sync_preview(status)
        self._update_status_text(status)
        self._update_actions()

    def _update_source(self, status: SeriesStatus) -> None:
        layer = self.coverage_combo.currentLayer()
        note = ""
        if status.state is SeriesState.LAYER_MISSING:
            note = status.message
        elif layer is not None:
            note = layer_problem(layer) or feature_summary(layer)
        self.source_note.setText(note)
        self.source_note.setVisible(bool(note))
        warn = status.state in (SeriesState.LAYER_MISSING, SeriesState.LAYER_UNUSABLE)
        self.source_note.setProperty("role", "checkVerdict" if warn else "")
        self.source_note.setProperty("tone", "warning" if warn else "")
        style = self.source_note.style()
        style.unpolish(self.source_note)
        style.polish(self.source_note)

    def _update_map(self, status: SeriesStatus) -> None:
        count = len(status.maps)
        self.map_chooser.setVisible(count > 1)
        self.no_map.setVisible(count == 0)
        summary = ""
        if count == 1 and status.updated:
            summary = tr("Updating map: {name}").format(name=status.maps[0].name)
        self.map_summary.setText(summary)
        self.map_summary.setVisible(bool(summary))

    def _update_status_text(self, status: SeriesStatus) -> None:
        if status.state is SeriesState.READY:
            if status.sheets_per_page > 1:
                text = plural(
                    status.pages,
                    "{n} page ready ({sheets} sheets each)",
                    "{n} pages ready ({sheets} sheets each)",
                    sheets=status.sheets_per_page,
                )
            else:
                text = plural(status.pages, "{n} page ready", "{n} pages ready")
        elif status.state in (
            SeriesState.OFF,
            SeriesState.NO_MAP,  # the map area already says it
            SeriesState.LAYER_MISSING,  # said beside the layer
            SeriesState.LAYER_UNUSABLE,
        ):
            text = ""
        else:
            text = status.message
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _update_actions(self) -> None:
        status = self._status
        ready = status.state is SeriesState.READY
        scope = ""
        if ready and status.sheets_per_page > 1:
            scope = plural(
                status.sheets, "Exports all {n} sheet.", "Exports all {n} sheets."
            )
        elif ready:
            scope = plural(
                status.pages, "Exports all {n} page.", "Exports all {n} pages."
            )
        self.scope_label.setText(scope)
        self.scope_label.setVisible(bool(scope))
        self.export_button.setEnabled(ready)
        self.print_button.setEnabled(ready and status.print.ok)
        needs = status.print.needs_interactive if ready else ()
        note = ""
        if ready and not status.print.ok:
            note = (
                tr('Printing needs the map "{name}" to be Interactive.').format(
                    name=", ".join(ref.name for ref in needs)
                )
                + " "
                + tr("Switching changes how the map behaves in the exported HTML.")
            )
        elif ready and self._saved_problems:
            note = " ".join(self._saved_problems)
        self.actions_note.setText(note)
        self.actions_note.setVisible(bool(note))
        self.switch_button.setVisible(bool(needs))
        self.navigator.setVisible(ready)

    def _validate_expressions(self) -> None:
        """Show beside each advanced field why QGIS cannot use what is typed
        there; a series with an invalid sort or filter is not exported."""
        coverage = self.coverage_combo.currentLayer()
        atlas = self._layout.atlas() if self._layout is not None else None
        sorting = bool(atlas and atlas.sortFeatures())
        filtering = bool(atlas and atlas.filterFeatures())
        fields = (
            ("sort", self.sort_edit, self.sort_error_label, sorting),
            ("filter", self.filter_edit, self.filter_error_label, filtering),
            ("page", self.page_name_edit, self.page_name_error_label, True),
            ("file", self.filename_edit, self.filename_error_label, True),
        )
        for kind, edit, label, active in fields:
            text = edit.expression().strip()
            error = (
                expression_error(text, coverage)
                if text and active and coverage is not None
                else None
            )
            if kind == "sort":
                self._sort_error = error
            elif kind == "filter":
                self._filter_error = error
            label.setText(
                tr("Not a valid expression: {error}").format(error=error)
                if error
                else ""
            )
            label.setVisible(error is not None)
            if error is not None or (text and active and kind in ("sort", "filter")):
                self.advanced_section.set_open(True)
            if error is None:
                self._revealed.discard(kind)
            elif kind not in self._revealed:
                # Newly wrong: bring the field and its error into view.
                self._revealed.add(kind)
                QTimer.singleShot(0, lambda e=edit, m=label: self._reveal(e, m))

    def _reveal(self, edit: QWidget, message: QWidget) -> None:
        """Scroll the panel to `edit` and its error, and focus the field."""
        if sip.isdeleted(edit) or not edit.isVisible():
            return
        area = self.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if isinstance(area, QScrollArea):
            area.ensureWidgetVisible(message)
            area.ensureWidgetVisible(edit)
            # The error sits under the field: keep both in view.
            area.ensureWidgetVisible(message)
        line = edit.findChild(QLineEdit)
        (line or edit).setFocus()

    # ---- Preview -----------------------------------------------------------

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._layout is not None:
            self._sync_preview(self._status)

    def hideEvent(self, event: QHideEvent | None) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._release_preview()

    def _sync_preview(self, status: SeriesStatus) -> None:
        """Keep the layout showing the series' current page - only while this
        tab is on screen and the series is ready; otherwise put it back."""
        layout = self._layout
        if (
            layout is None
            or sip.isdeleted(layout)
            or status.state is not SeriesState.READY
            or not self.isVisible()
        ):
            self._release_preview()
            return
        if self._preview is None or not self._preview.active:
            self._preview = AtlasPreview(layout)
            self._preview.begin()
        preview = self._preview
        count = preview.prepare()
        self._page_count = count
        target: int | None = None
        if self._page_feature is not None:
            target = preview.find_page(self._page_feature, count, self._page_index)
        # A page that no longer exists starts the series over, predictably.
        self._show_page(preview, 0 if target is None else target)

    def _show_page(self, preview: AtlasPreview, index: int) -> None:
        index = max(0, min(index, self._page_count - 1))
        if not preview.go_to(index):
            return
        self._page_index = index
        self._page_feature = preview.current_feature_id()
        name = preview.page_name(index) or tr("Page {n}").format(n=index + 1)
        self.position_label.setText(
            tr("{page} of {total} · {name}").format(
                page=index + 1, total=self._page_count, name=name
            )
        )
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < self._page_count - 1)

    def go_to_page(self, index: int) -> None:
        """Show page `index` (0-based) of the series in the main renderer."""
        preview = self._preview
        if preview is None or not preview.active or self._layout is None:
            return
        self._show_page(preview, index)

    def _release_preview(self) -> None:
        preview = self._preview
        self._preview = None
        if preview is None or not preview.active:
            return
        moved = preview.moved
        preview.release()
        if moved:
            # The layout was autosaved while it showed a page; save it as it is.
            with contextlib.suppress(RuntimeError):
                self.changed.emit()

    def _release_preview_quietly(self) -> None:
        """Put the layout back before an edit, which announces itself."""
        preview = self._preview
        self._preview = None
        if preview is not None and preview.active:
            preview.release()

    def preview_page(self) -> int | None:
        """The 0-based page the main renderer shows; `None` when not previewing."""
        preview = self._preview
        return self._page_index if preview is not None and preview.active else None

    def status(self) -> SeriesStatus:
        return self._status

    # ---- Actions -----------------------------------------------------------

    def _on_export(self) -> None:
        from .series_export_dialog import SeriesExportDialog

        self.refresh_now()
        if self._status.state is not SeriesState.READY:
            return
        dialog = SeriesExportDialog(
            self._current_status, self._switch_maps, lambda: self._saved_problems, self
        )
        if not dialog.exec():
            return
        chosen = dialog.chosen_format()
        if chosen == "pdf":
            self._host.export_pdf()
        elif chosen == "images":
            self._host.open_pages("zip")
        elif chosen == "html":
            self._host.open_pages("html")

    def _on_print(self) -> None:
        self.refresh_now()
        if self._status.state is SeriesState.READY and self._status.print.ok:
            self._host.open_pages("print")

    def _on_switch_to_interactive(self) -> None:
        self._switch_maps(
            tuple(ref.item_id for ref in self._status.print.needs_interactive)
        )

    def _switch_maps(self, item_ids: Sequence[str]) -> None:
        self._host.switch_to_interactive(item_ids)
        self.refresh_now()

    def _current_status(self) -> SeriesStatus:
        self.refresh_now()
        return self._status

    # ---- Undo ---------------------------------------------------------------

    def _watch_undo(self, layout: QgsPrintLayout | None) -> None:
        """Follow `layout`'s undo stack, so an Undo or Redo of an atlas edit
        made anywhere shows here and is saved."""
        stack = layout.undoStack().stack() if layout is not None else None
        if stack is self._watched_stack:
            return
        if self._watched_stack is not None:
            # Gone with its layout, or already disconnected: nothing to undo.
            with contextlib.suppress(TypeError, RuntimeError):
                self._watched_stack.indexChanged.disconnect(self._on_undo_redo)
        self._watched_stack = stack
        if stack is not None:
            stack.indexChanged.connect(self._on_undo_redo)

    def _on_undo_redo(self, _index: int) -> None:
        layout = self._layout
        if self._editing or layout is None or sip.isdeleted(layout):
            return
        if _atlas_state(layout) == self._shown:
            return
        self._release_preview_quietly()
        self._load()
        self._shown = _atlas_state(layout)
        self.refresh_now()
        self.changed.emit()

    # ---- Editing -----------------------------------------------------------

    def _edit(
        self,
        description: str,
        apply: Callable[[QgsPrintLayout], None],
        touches: Callable[[QgsPrintLayout], Sequence[QgsLayoutUndoObjectInterface]]
        | None = None,
    ) -> None:
        """One undoable change to the layout's atlas, then `changed`.

        `touches` names the layout objects `apply` changes when that is not
        the atlas alone; their before-states are what Undo restores, in one
        step. The preview is put back first (the maps' authored views are what
        an edit and its undo are about) and shown again after.
        """
        layout = self._layout
        if self._loading or layout is None:
            return
        if sip.isdeleted(layout):
            # The project closed under us (its layers going make the combo
            # boxes emit); there is no layout left to edit.
            self._layout = None
            return
        self._release_preview_quietly()
        stack = layout.undoStack()
        objects = [layout.atlas()] if touches is None else touches(layout)
        # A macro only when it groups: an empty one would still be an Undo step.
        grouped = len(objects) > 1
        self._editing = True
        try:
            if grouped:
                stack.beginMacro(description)
            for target in objects:
                stack.beginCommand(target, description)
            apply(layout)
            for _target in objects:
                stack.endCommand()
            if grouped:
                stack.endMacro()
        finally:
            self._editing = False
        self._shown = _atlas_state(layout)
        self._request_refresh()
        self.changed.emit()

    @staticmethod
    def _association_targets(layout: QgsPrintLayout) -> list[QgsLayoutItemMap]:
        """The map a series should start updating, when none does yet and the
        choice is not ambiguous: the layout's only map, else the one map the
        user has selected. Never an arbitrary one."""
        if driven_maps(layout):
            return []
        maps = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
        if len(maps) == 1:
            return maps
        selected = [
            i for i in layout.selectedLayoutItems() if isinstance(i, QgsLayoutItemMap)
        ]
        return selected if len(selected) == 1 else []

    def _associate(self, layout: QgsPrintLayout) -> None:
        for item in self._association_targets(layout):
            item.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
            item.setAtlasMargin(DEFAULT_ATLAS_MARGIN)
            item.setAtlasDriven(True)

    def _atlas_and_new_map(
        self, layout: QgsPrintLayout
    ) -> list[QgsLayoutUndoObjectInterface]:
        return [layout.atlas(), *self._association_targets(layout)]

    def _on_enabled(self, enabled: bool) -> None:
        self.settings_box.setVisible(enabled)
        if self._loading:
            return

        def apply(layout: QgsPrintLayout) -> None:
            layout.atlas().setEnabled(enabled)
            if enabled and layout.atlas().coverageLayer() is not None:
                self._associate(layout)

        self._layer_removed = False
        self._edit(tr("Toggle Atlas"), apply, touches=self._atlas_and_new_map)

    def _on_coverage(self, layer) -> None:
        for widget in self._expression_widgets:
            widget.setLayer(layer)
        if self._loading:
            return

        def apply(layout: QgsPrintLayout) -> None:
            atlas = layout.atlas()
            # By id: sip may hand back a new wrapper for the same layer.
            current = atlas.coverageLayer()
            if (current.id() if current else None) != (layer.id() if layer else None):
                atlas.setCoverageLayer(layer)
            if layer is not None:
                if not atlas.enabled():
                    atlas.setEnabled(True)
                self._associate(layout)

        self._layer_removed = False
        self._edit(tr("Change Atlas Coverage"), apply, touches=self._atlas_and_new_map)

    def _on_map(self, _index: int) -> None:
        chosen = self.map_combo.currentData()

        def switching(layout: QgsPrintLayout) -> list[QgsLayoutItemMap]:
            return [
                item
                for item in layout.items()
                if isinstance(item, QgsLayoutItemMap)
                and item.atlasDriven() != (item.uuid() == chosen)
            ]

        def apply(layout: QgsPrintLayout) -> None:
            for item in switching(layout):
                follows = item.uuid() == chosen
                if follows:
                    item.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
                    item.setAtlasMargin(DEFAULT_ATLAS_MARGIN)
                item.setAtlasDriven(follows)

        self._edit(tr("Change Atlas Map"), apply, touches=switching)

    def _on_framing(self, _index: int) -> None:
        mode = QgsLayoutItemMap.AtlasScalingMode(self.framing_combo.currentData())

        def changing(layout: QgsPrintLayout) -> list[QgsLayoutItemMap]:
            return [i for i in driven_maps(layout) if i.atlasScalingMode() != mode]

        def apply(layout: QgsPrintLayout) -> None:
            for item in changing(layout):
                item.setAtlasScalingMode(mode)

        self._edit(tr("Change Atlas Framing"), apply, touches=changing)

    # Expression and direction are separate edits, as in QGIS: flipping the
    # direction never rewrites the expression, whatever the picker holds.
    def _on_sort_expression(self, *_args: object) -> None:
        # A plain field as its bare name, like QGIS's own atlas panel stores it.
        expression = self.sort_edit.currentField()[0].strip()

        def apply(layout: QgsPrintLayout) -> None:
            atlas = layout.atlas()
            atlas.setSortFeatures(bool(expression))
            atlas.setSortExpression(expression)

        self._edit(tr("Change Atlas Sort"), apply)
        if not self._loading:
            self.direction_combo.setEnabled(bool(expression))

    def _on_sort_direction(self, _index: int) -> None:
        ascending = bool(self.direction_combo.currentData())
        self._edit(
            tr("Change Atlas Sort"),
            lambda layout: layout.atlas().setSortAscending(ascending),
        )

    def _on_filter(self, *_args: object) -> None:
        expression = self.filter_edit.expression().strip()

        def apply(layout: QgsPrintLayout) -> None:
            atlas = layout.atlas()
            atlas.setFilterFeatures(bool(expression))
            atlas.setFilterExpression(expression)

        self._edit(tr("Change Atlas Filter"), apply)

    def _on_page_name(self, *_args: object) -> None:
        expression = self.page_name_edit.expression().strip()
        self._edit(
            tr("Change Atlas Page Names"),
            lambda layout: layout.atlas().setPageNameExpression(expression),
        )

    def _on_filename(self, *_args: object) -> None:
        expression = self.filename_edit.expression().strip()
        self._edit(
            tr("Change Atlas Filename"),
            lambda layout: layout.atlas().setFilenameExpression(expression),
        )

    # ---- Project changes ---------------------------------------------------

    def layer_will_be_removed(self, removed: object) -> None:
        """The project is about to take a layer away. When it is the coverage
        layer, QGIS turns the atlas off and forgets the layer; the series stays
        on screen, saying so, until another layer is chosen."""
        layout = self._layout
        if (
            layout is None
            or sip.isdeleted(layout)
            or not self.enabled_check.isChecked()
        ):
            return
        # Either overload of `QgsProject.layerWillBeRemoved`: the id or the layer.
        layer_id = removed if isinstance(removed, str) else removed.id()  # type: ignore[attr-defined]
        # The atlas may already have dropped the layer (it listens too).
        if layer_id != self._coverage_id:
            return
        self._layer_removed = True
        self._release_preview()
        # Once the layer is really gone.
        QTimer.singleShot(0, self._after_layer_removed)

    def _after_layer_removed(self) -> None:
        if self._layout is None or sip.isdeleted(self._layout):
            return
        self._loading = True
        try:
            self.coverage_combo.setLayer(None)
            self.enabled_check.setChecked(True)
        finally:
            self._loading = False
        self.refresh_now()


def _invalid(status: SeriesStatus) -> SeriesStatus:
    """`status` as the series cannot be prepared: a sort or filter QGIS cannot
    evaluate blocks every output."""
    if status.state in (
        SeriesState.OFF,
        SeriesState.CHOOSE_LAYER,
        SeriesState.LAYER_MISSING,
        SeriesState.LAYER_UNUSABLE,
        SeriesState.NO_MAP,
        SeriesState.CHOOSE_MAP,
    ):
        return status
    message = tr("Couldn't prepare the series. Check the highlighted setting.")
    none = Operation(False, message)
    return replace(
        status,
        state=SeriesState.INVALID,
        message=message,
        pages=0,
        pdf=none,
        images=none,
        html=none,
        print=none,
    )


def _typed_expression_widget(parent: QWidget) -> QgsFieldExpressionWidget:
    """An expression picker whose Enter applies what was typed, like Tab.

    The picker's editable combo inserts a typed entry at the bottom on Enter,
    which resets the field to empty; "no insert" leaves the typed text for the
    picker to commit as the expression."""
    widget = QgsFieldExpressionWidget(parent)
    combo = widget.findChild(QComboBox)
    if combo is not None:
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    return widget


def _atlas_state(layout: QgsPrintLayout | None) -> tuple[object, ...]:
    """Everything this tab shows of `layout`'s atlas, comparable."""
    if layout is None:
        return ()
    atlas = layout.atlas()
    coverage = atlas.coverageLayer()
    return (
        atlas.enabled(),
        coverage.id() if coverage is not None else "",
        atlas.sortFeatures(),
        atlas.sortExpression(),
        atlas.sortAscending(),
        atlas.filterFeatures(),
        atlas.filterExpression(),
        atlas.filenameExpression(),
        atlas.pageNameExpression(),
        tuple(
            (item.uuid(), int(item.atlasScalingMode()))
            for item in layout.items()
            if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()
        ),
    )
