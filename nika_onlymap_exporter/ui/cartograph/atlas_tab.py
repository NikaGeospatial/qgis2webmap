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

from collections.abc import Callable

from qgis.core import (
    QgsLayoutItemMap,
    QgsMapLayerProxyModel,
    QgsPrintLayout,
)
from qgis.gui import QgsFieldComboBox, QgsFieldExpressionWidget, QgsMapLayerComboBox
from qgis.PyQt import sip
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_reader import atlas_page_count
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from .frames import layout_map_items

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

        settings = QGroupBox(tr("Atlas"), self)
        form = QFormLayout(settings)
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

        sort_row = QHBoxLayout()
        self.sort_combo = QgsFieldComboBox(settings)
        self.sort_combo.setAllowEmptyFieldName(True)
        self.sort_combo.fieldChanged.connect(self._on_sort)
        sort_row.addWidget(self.sort_combo, 1)
        self.descending_check = QCheckBox(tr("Descending"), settings)
        self.descending_check.toggled.connect(self._on_sort)
        sort_row.addWidget(self.descending_check)
        form.addRow(tr("Sort by"), sort_row)

        self.filter_edit = QgsFieldExpressionWidget(settings)
        self.filter_edit.setToolTip(
            tr("Only features for which this is true get a page. Blank: all.")
        )
        self.filter_edit.fieldChanged.connect(self._on_filter)
        form.addRow(tr("Filter"), self.filter_edit)

        self.filename_edit = QgsFieldExpressionWidget(settings)
        self.filename_edit.setToolTip(
            tr("How each page's image is named when all pages are exported.")
        )
        self.filename_edit.fieldChanged.connect(self._on_filename)
        form.addRow(tr("File names"), self.filename_edit)
        page.addWidget(settings)
        self.settings_box = settings

        self.count_label = FittedLabel("", self)
        page.addWidget(self.count_label)
        self.problem_label = FittedLabel("", self)
        self.problem_label.setWordWrap(True)
        self.problem_label.setProperty("role", "warning")
        page.addWidget(self.problem_label)

        buttons = QHBoxLayout()
        self.zip_button = QPushButton(tr("Export all pages (PNG, ZIP)..."), self)
        self.zip_button.setToolTip(
            tr("Opens the cartograph and downloads every page as one ZIP")
        )
        self.zip_button.clicked.connect(lambda: self._open_all_pages("zip"))
        buttons.addWidget(self.zip_button)
        self.print_button = QPushButton(tr("Print all pages..."), self)
        self.print_button.setToolTip(
            tr(
                "Opens the cartograph with every page ready to print - choose "
                "Save as PDF for one multi-page PDF"
            )
        )
        self.print_button.clicked.connect(lambda: self._open_all_pages("print"))
        buttons.addWidget(self.print_button)
        buttons.addStretch(1)
        page.addLayout(buttons)
        page.addStretch(1)

        self.show_layout(None)
        self.show_outcome(problems=(), pages_written=False)

    # ---- Showing ---------------------------------------------------------

    def show_layout(self, layout: QgsPrintLayout | None) -> None:
        """Show `layout`'s atlas, without the display counting as an edit."""
        self._layout = layout
        self._loading = True
        try:
            has_layout = layout is not None
            self.enabled_check.setEnabled(has_layout)
            atlas = layout.atlas() if layout is not None else None
            self.enabled_check.setChecked(bool(atlas and atlas.enabled()))
            self.settings_box.setEnabled(self.enabled_check.isChecked())
            coverage = atlas.coverageLayer() if atlas is not None else None
            self.coverage_combo.setLayer(coverage)
            for widget in (self.sort_combo, self.filter_edit, self.filename_edit):
                widget.setLayer(coverage)
            if atlas is not None:
                self.sort_combo.setField(
                    atlas.sortExpression() if atlas.sortFeatures() else ""
                )
                self.descending_check.setChecked(
                    atlas.sortFeatures() and not atlas.sortAscending()
                )
                self.filter_edit.setExpression(
                    atlas.filterExpression() if atlas.filterFeatures() else ""
                )
                self.filename_edit.setExpression(atlas.filenameExpression())
            self._fill_maps()
        finally:
            self._loading = False
        self._update_count()

    def show_outcome(self, problems: tuple[str, ...], pages_written: bool) -> None:
        """What the last save made of the atlas: why it does not page, if it
        does not, and whether the all-pages buttons have pages to work on."""
        self.problem_label.setText("\n".join(problems))
        self.problem_label.setVisible(bool(problems))
        for button in (self.zip_button, self.print_button):
            button.setEnabled(pages_written)
            if not pages_written:
                button.setToolTip(tr("The saved cartograph has no atlas pages yet"))

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
        count = atlas_page_count(self._layout) if self._layout is not None else None
        self.count_label.setText(
            ""
            if count is None
            else plural(count, "{n} page in QGIS", "{n} pages in QGIS")
        )

    # ---- Editing -----------------------------------------------------------

    def _edit(self, description: str, apply: Callable[[QgsPrintLayout], None]) -> None:
        """One undoable change to the layout's atlas, then `changed`."""
        layout = self._layout
        if self._loading or layout is None:
            return
        if sip.isdeleted(layout):
            # The project closed under us (its layers going make the combo
            # boxes emit); there is no layout left to edit.
            self._layout = None
            return
        stack = layout.undoStack()
        stack.beginCommand(layout.atlas(), description)
        apply(layout)
        stack.endCommand()
        self._update_count()
        self.changed.emit()

    def _on_enabled(self, enabled: bool) -> None:
        self.settings_box.setEnabled(enabled)
        self._edit(
            tr("Toggle Atlas"), lambda layout: layout.atlas().setEnabled(enabled)
        )

    def _on_coverage(self, layer) -> None:
        for widget in (self.sort_combo, self.filter_edit, self.filename_edit):
            widget.setLayer(layer)

        def apply(layout: QgsPrintLayout) -> None:
            if layout.atlas().coverageLayer() is not layer:
                layout.atlas().setCoverageLayer(layer)

        self._edit(tr("Change Atlas Coverage"), apply)

    def _on_map(self, _index: int) -> None:
        chosen = self.map_combo.currentData()

        def apply(layout: QgsPrintLayout) -> None:
            for item in layout.items():
                if not isinstance(item, QgsLayoutItemMap):
                    continue
                follows = item.uuid() == chosen
                if follows and not item.atlasDriven():
                    item.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
                    item.setAtlasMargin(DEFAULT_ATLAS_MARGIN)
                item.setAtlasDriven(follows)

        self._edit(tr("Change Atlas Map"), apply)

    def _on_sort(self, *_args: object) -> None:
        field_name = self.sort_combo.currentField()
        descending = self.descending_check.isChecked()

        def apply(layout: QgsPrintLayout) -> None:
            atlas = layout.atlas()
            atlas.setSortFeatures(bool(field_name))
            atlas.setSortExpression(field_name)
            atlas.setSortAscending(not descending)

        self._edit(tr("Change Atlas Sort"), apply)

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
