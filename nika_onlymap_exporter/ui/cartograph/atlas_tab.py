"""The Cartograph output's Atlas tab: one page per feature.

The atlas is the print layout's own (`QgsLayoutAtlas`), so this tab edits the
layout - the same settings QGIS's Atlas panel shows - and every change goes on
the layout's undo stack, where the layout designer's Undo takes it back. The
cartograph follows on the next autosave like any other layout edit.

What the tab adds that QGIS's panel cannot know is whether the cartograph
will actually page: the runtime pages only a LIVE frame that draws the
coverage layer (`core/layout_reader.py` records a Blocked row with the remedy
when it cannot), so the tab shows those rows' words next to the settings.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from qgis.core import (
    QgsLayoutItemMap,
    QgsLayoutUndoObjectInterface,
    QgsMapLayerProxyModel,
    QgsPrintLayout,
)
from qgis.gui import QgsFieldExpressionWidget, QgsMapLayerComboBox
from qgis.PyQt import sip
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_reader import atlas_page_count, expression_error
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from .frames import layout_map_items
from .panel_section import PanelSection

if TYPE_CHECKING:  # pragma: no cover - typing only
    # QtWidgets on Qt 5, QtGui on Qt 6.
    from qgis.PyQt.QtGui import QUndoStack

#: The margin a map gets when this tab first makes it follow the atlas: QGIS's
#: own default in the Item Properties panel.
DEFAULT_ATLAS_MARGIN = 0.1


class AtlasTab(QWidget):
    """Mirrors and edits one layout's atlas. `changed` fires after an edit."""

    changed = pyqtSignal()

    def __init__(
        self,
        open_all_pages: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._layout: QgsPrintLayout | None = None
        self._loading = False
        # True while this tab pushes its own undo command, so only an Undo or
        # Redo made elsewhere (Ctrl+Z, the layout designer) re-reads the atlas.
        self._editing = False
        self._watched_stack: QUndoStack | None = None
        # The atlas as last shown or edited here; an undo-stack move that
        # leaves it alone (another tab's own command) is not an atlas change.
        self._shown: tuple[object, ...] = ()
        # `open_all_pages("zip" | "print")`: the controller opens the saved
        # cartograph with this plugin's `?atlas=` trigger.
        self._open_all_pages = open_all_pages

        page = QVBoxLayout(self)
        intro = FittedLabel(
            tr(
                "One page per feature of a coverage layer, like QGIS's own "
                "atlas: the map that follows the atlas zooms to each feature "
                "in turn, and text can show the feature's fields."
            ),
            self,
        )
        intro.setWordWrap(True)
        page.addWidget(intro)

        self.enabled_check = QCheckBox(tr("Generate an atlas"), self)
        self.enabled_check.toggled.connect(self._on_enabled)
        page.addWidget(self.enabled_check)

        # No box: it sits in the panel's Atlas tab, under that name already,
        # and an untitled box kept an empty title's gap above its fields.
        settings = QWidget(self)
        settings_column = QVBoxLayout(settings)
        settings_column.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 4)
        # Labels above their fields: the tab is a side panel a third of the
        # dialog wide, where label and field side by side ran off its edge.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        settings_column.addLayout(form)
        self.coverage_combo = QgsMapLayerComboBox(settings)
        self.coverage_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        self.coverage_combo.setAllowEmptyLayer(True)
        self.coverage_combo.layerChanged.connect(self._on_coverage)
        form.addRow(tr("Coverage layer"), self.coverage_combo)

        self.map_combo = QComboBox(settings)
        self.map_combo.setToolTip(
            tr("The map that zooms to each feature. It must be a Live frame.")
        )
        self.map_combo.currentIndexChanged.connect(self._on_map)
        form.addRow(tr("Map that follows it"), self.map_combo)

        # What most atlases never touch sits under the choices every atlas
        # needs; it opens by itself when something in it is set or wrong.
        self.advanced_section = PanelSection(
            "atlas-advanced", tr("Sorting, filtering and file names"), settings
        )
        settings_column.addWidget(self.advanced_section)
        advanced = QFormLayout()
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.advanced_section.body_layout.addLayout(advanced)

        sort_row = QHBoxLayout()
        self.sort_edit = QgsFieldExpressionWidget(settings)
        self.sort_edit.setAllowEmptyFieldName(True)
        self.sort_edit.fieldChanged.connect(self._on_sort_expression)
        sort_row.addWidget(self.sort_edit, 1)
        self.descending_check = QCheckBox(tr("Descending"), settings)
        self.descending_check.toggled.connect(self._on_sort_direction)
        sort_row.addWidget(self.descending_check)
        sort_column = QVBoxLayout()
        sort_column.setContentsMargins(0, 0, 0, 0)
        sort_column.addLayout(sort_row)
        self.sort_error_label = self._error_label(settings)
        sort_column.addWidget(self.sort_error_label)
        advanced.addRow(tr("Sort by"), sort_column)

        self.filter_edit = QgsFieldExpressionWidget(settings)
        self.filter_edit.setToolTip(
            tr("Only features for which this is true get a page. Blank: all.")
        )
        self.filter_edit.fieldChanged.connect(self._on_filter)
        filter_column = QVBoxLayout()
        filter_column.setContentsMargins(0, 0, 0, 0)
        filter_column.addWidget(self.filter_edit)
        self.filter_error_label = self._error_label(settings)
        filter_column.addWidget(self.filter_error_label)
        advanced.addRow(tr("Filter"), filter_column)

        self.filename_edit = QgsFieldExpressionWidget(settings)
        self.filename_edit.setToolTip(
            tr("How each page's image is named when all pages are exported.")
        )
        self.filename_edit.fieldChanged.connect(self._on_filename)
        advanced.addRow(tr("File names"), self.filename_edit)
        page.addWidget(settings)
        self.settings_box = settings

        self.count_label = FittedLabel("", self)
        page.addWidget(self.count_label)
        # What the saved cartograph pages, as opposed to what QGIS pages.
        self.saved_label = FittedLabel("", self)
        self.saved_label.setWordWrap(True)
        page.addWidget(self.saved_label)
        self.problem_label = FittedLabel("", self)
        self.problem_label.setWordWrap(True)
        self.problem_label.setProperty("role", "warning")
        page.addWidget(self.problem_label)

        # One above the other, for the same narrow panel.
        buttons = QVBoxLayout()
        self.zip_button = QPushButton(tr("Export all pages (PNG, ZIP)..."), self)
        self.zip_button.clicked.connect(lambda: self._open_all_pages("zip"))
        buttons.addWidget(self.zip_button)
        self.print_button = QPushButton(tr("Print all pages..."), self)
        self.print_button.clicked.connect(lambda: self._open_all_pages("print"))
        # What each button says once there are pages; `show_outcome` swaps in
        # why it is greyed out, and back.
        self._ready_tips = {
            self.zip_button: tr(
                "Opens the cartograph and downloads every page as one ZIP"
            ),
            self.print_button: tr(
                "Opens the cartograph with every page ready to print - choose "
                "Save as PDF for one multi-page PDF"
            ),
        }
        buttons.addWidget(self.print_button)
        page.addLayout(buttons)
        page.addStretch(1)

        # Pages in the saved cartograph's atlas; `None` when it has none.
        self._saved_pages: int | None = None
        self._filter_error: str | None = None
        self._sort_error: str | None = None
        self.show_layout(None)
        self.show_outcome(problems=(), pages=None)

    @staticmethod
    def _error_label(parent: QWidget) -> FittedLabel:
        label = FittedLabel("", parent)
        label.setWordWrap(True)
        label.setProperty("role", "warning")
        label.setVisible(False)
        return label

    # ---- Showing ---------------------------------------------------------

    def show_layout(self, layout: QgsPrintLayout | None) -> None:
        """Show `layout`'s atlas, without the display counting as an edit."""
        self._layout = layout
        self._watch_undo(layout)
        self._loading = True
        try:
            has_layout = layout is not None
            self.enabled_check.setEnabled(has_layout)
            atlas = layout.atlas() if layout is not None else None
            self.enabled_check.setChecked(bool(atlas and atlas.enabled()))
            self.settings_box.setEnabled(self.enabled_check.isChecked())
            coverage = atlas.coverageLayer() if atlas is not None else None
            self.coverage_combo.setLayer(coverage)
            for widget in (self.sort_edit, self.filter_edit, self.filename_edit):
                widget.setLayer(coverage)
            if atlas is not None:
                self.sort_edit.setExpression(
                    atlas.sortExpression() if atlas.sortFeatures() else ""
                )
                # The stored direction, sorted or not, as QGIS shows it - and
                # only editable while there is a sort for it to apply to.
                self.descending_check.setChecked(not atlas.sortAscending())
                self.descending_check.setEnabled(atlas.sortFeatures())
                self.filter_edit.setExpression(
                    atlas.filterExpression() if atlas.filterFeatures() else ""
                )
                self.filename_edit.setExpression(atlas.filenameExpression())
            self._fill_maps()
        finally:
            self._loading = False
        self._shown = _atlas_state(layout)
        self._update_count()

    def show_outcome(self, problems: tuple[str, ...], pages: int | None) -> None:
        """What the last save made of the atlas: why it does not page, if it
        does not, and how many pages the saved atlas has (`None`: no atlas at
        all; 0: an atlas whose filter matches nothing). The all-pages buttons
        are enabled by that count alone, and only while the atlas settings
        are valid."""
        self.problem_label.setText("\n".join(problems))
        self.problem_label.setVisible(bool(problems))
        self._saved_pages = pages
        self._refresh_saved_state()

    def _refresh_saved_state(self) -> None:
        pages = self._saved_pages
        invalid = self._filter_error is not None or self._sort_error is not None
        if pages is None:
            saved = ""
        elif pages == 0:
            saved = tr("The saved cartograph has no pages: no feature matches.")
        else:
            saved = plural(
                pages,
                "{n} page saved in the cartograph",
                "{n} pages saved in the cartograph",
            )
        self.saved_label.setText(saved)
        self.saved_label.setVisible(bool(saved) and not invalid)
        usable = bool(pages) and not invalid
        if invalid:
            reason = tr("Correct the atlas filter or sort first")
        elif pages == 0:
            reason = tr("No feature matches the atlas, so there are no pages")
        else:
            reason = tr("The saved cartograph has no atlas pages yet")
        for button, ready_tip in self._ready_tips.items():
            button.setEnabled(usable)
            button.setToolTip(ready_tip if usable else reason)

    def _validate_expressions(self) -> None:
        """Show beside the Sort and Filter fields why QGIS cannot use what
        is typed there; an atlas with such a field is not exported."""
        coverage = self.coverage_combo.currentLayer()
        atlas = self._layout.atlas() if self._layout is not None else None
        sorting = bool(atlas and atlas.sortFeatures())
        filtering = bool(atlas and atlas.filterFeatures())
        for kind, edit, label, active in (
            ("sort", self.sort_edit, self.sort_error_label, sorting),
            ("filter", self.filter_edit, self.filter_error_label, filtering),
        ):
            text = edit.expression().strip()
            error = (
                expression_error(text, coverage)
                if text and active and coverage is not None
                else None
            )
            setattr(self, f"_{kind}_error", error)
            label.setText(
                tr("Not a valid expression: {error}").format(error=error)
                if error
                else ""
            )
            label.setVisible(error is not None)
            if error is not None or (text and active):
                self.advanced_section.set_open(True)
        self._refresh_saved_state()

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
        self.show_layout(layout)
        self.changed.emit()

    def _fill_maps(self) -> None:
        self.map_combo.clear()
        self.map_combo.addItem(tr("(none)"), "")
        if self._layout is None:
            return
        chosen = 0
        driven = {
            item.uuid()
            for item in self._layout.items()
            if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()
        }
        # Named and ordered as the Frames tab lists them.
        for frame in layout_map_items(self._layout):
            self.map_combo.addItem(frame.name, frame.item_id)
            if frame.item_id in driven and not chosen:
                chosen = self.map_combo.count() - 1
        self.map_combo.setCurrentIndex(chosen)

    def _update_count(self) -> None:
        self._validate_expressions()
        count = atlas_page_count(self._layout) if self._layout is not None else None
        self.count_label.setText(
            ""
            if count is None
            else plural(count, "{n} page in QGIS", "{n} pages in QGIS")
        )

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
        the atlas; their before-states are what Undo restores, in one step.
        """
        layout = self._layout
        if self._loading or layout is None:
            return
        if sip.isdeleted(layout):
            # The project closed under us (its layers going make the combo
            # boxes emit); there is no layout left to edit.
            self._layout = None
            return
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
        self._update_count()
        self.changed.emit()

    def _on_enabled(self, enabled: bool) -> None:
        self.settings_box.setEnabled(enabled)
        self._edit(
            tr("Toggle Atlas"), lambda layout: layout.atlas().setEnabled(enabled)
        )

    def _on_coverage(self, layer) -> None:
        for widget in (self.sort_edit, self.filter_edit, self.filename_edit):
            widget.setLayer(layer)

        def apply(layout: QgsPrintLayout) -> None:
            # By id: sip may hand back a new wrapper for the same layer.
            current = layout.atlas().coverageLayer()
            if (current.id() if current else None) != (layer.id() if layer else None):
                layout.atlas().setCoverageLayer(layer)

        self._edit(tr("Change Atlas Coverage"), apply)

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
            self.descending_check.setEnabled(bool(expression))

    def _on_sort_direction(self, descending: bool) -> None:
        self._edit(
            tr("Change Atlas Sort"),
            lambda layout: layout.atlas().setSortAscending(not descending),
        )

    def _on_filter(self, *_args: object) -> None:
        expression = self.filter_edit.expression().strip()

        def apply(layout: QgsPrintLayout) -> None:
            atlas = layout.atlas()
            atlas.setFilterFeatures(bool(expression))
            atlas.setFilterExpression(expression)

        self._edit(tr("Change Atlas Filter"), apply)

    def _on_filename(self, *_args: object) -> None:
        expression = self.filename_edit.expression().strip()
        self._edit(
            tr("Change Atlas Filename"),
            lambda layout: layout.atlas().setFilenameExpression(expression),
        )


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
        tuple(
            item.uuid()
            for item in layout.items()
            if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()
        ),
    )
