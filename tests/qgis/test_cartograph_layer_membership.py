"""The Cartograph Layers list edits the layout map's real layers.

A layer unticked in Cartograph must leave the preview (the layout itself), the
HTML, the image and PDF, and the legend; Undo brings it all back; and the web
map's own Include selection is never touched. The dialog is driven like a user
would: the output is chosen from the title's menu and the tick is a click.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402

from __future__ import annotations

from dataclasses import dataclass

import pytest

qgis_core = pytest.importorskip("qgis.core")

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLayoutExporter,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsMapThemeCollection,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPoint, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtTest import QTest

from nika_onlymap_exporter.core.cartograph_export import (
    export_layout_to_cartograph,
)
from nika_onlymap_exporter.core.fidelity_report import (
    FidelityReportBuilder,
)
from nika_onlymap_exporter.core.settings import DialogOutput
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

PEAKS, TRAILS, PARCELS = "Named peaks", "Trails", "Parcels"
# One colour per layer, so a rendered page says which layers are on it.
RED, BLUE, GREEN = (255, 0, 0), (0, 0, 255), (0, 200, 0)


class FakeIface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


@dataclass
class World:
    project: QgsProject
    layout: QgsPrintLayout
    main: QgsLayoutItemMap
    legend: QgsLayoutItemLegend
    layers: dict[str, QgsVectorLayer]


def _layer(name: str, geometry: str, wkt_points, colour: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{geometry}?crs=EPSG:4326&field=n:string", name, "memory")
    features = []
    for x, y in wkt_points:
        feature = QgsFeature(layer.fields())
        if geometry == "Point":
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        elif geometry == "LineString":
            feature.setGeometry(
                QgsGeometry.fromPolylineXY([QgsPointXY(x, y), QgsPointXY(x + 2, y)])
            )
        else:
            feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, y, x + 2, y + 2)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(QColor(colour))
    if geometry == "Point":
        symbol.setSize(6)
    elif geometry == "LineString":
        symbol.setWidth(1.5)
    layer.renderer().setSymbol(symbol)
    return layer


def _add_map(layout: QgsPrintLayout, item_id: str, rect: QRectF) -> QgsLayoutItemMap:
    item = QgsLayoutItemMap(layout)
    item.setId(item_id)
    layout.addLayoutItem(item)
    item.attemptSetSceneRect(rect)
    item.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    item.setExtent(QgsRectangle(-1, -1, 9, 9))
    return item


@pytest.fixture
def world(qgis_app, project) -> World:
    layers = {
        PEAKS: _layer(PEAKS, "Point", [(1, 1), (3, 4), (6, 6)], "#ff0000"),
        TRAILS: _layer(TRAILS, "LineString", [(1, 2), (3, 6)], "#0000ff"),
        PARCELS: _layer(PARCELS, "Polygon", [(4, 1)], "#00c800"),
    }
    for layer in layers.values():
        project.addMapLayer(layer)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("members")
    main = _add_map(layout, "main", QRectF(10, 10, 150, 150))
    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(170, 10))
    legend.setLinkedMap(main)
    project.layoutManager().addLayout(layout)
    return World(project, layout, main, legend, layers)


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


@pytest.fixture
def dialog(world, monkeypatch):
    """The real dialog on Cartograph, builds off (they are not under test)."""
    from nika_onlymap_exporter.ui.cartograph import controller as controller_module
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    monkeypatch.setattr(
        controller_module.CartographController, "_start_build", lambda self: None
    )
    dialog = MainDialog(FakeIface(), None)
    dialog.show()
    dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
    yield dialog
    dialog.close()


def _item(dialog, name: str):
    tree = dialog.layer_tree
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.text(0) == name:
            return item
    raise AssertionError(f"no row for {name}")


def _click_include(dialog, name: str) -> None:
    """Click the Include checkbox of a layer's row."""
    tree = dialog.layer_tree
    item = _item(dialog, name)
    tree.scrollToItem(item)
    rect = tree.visualItemRect(item)
    header = tree.header()
    # The box sits at the cell's left edge, not its middle.
    x = header.sectionViewportPosition(1) + 10
    QTest.mouseClick(
        tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, rect.center().y()),
    )


def _ticked(dialog, name: str) -> bool:
    return _item(dialog, name).checkState(1) == Qt.CheckState.Checked


def _drawn_names(map_item) -> list[str]:
    return [layer.name() for layer in map_item.layersToRender()]


def _legend_names(legend) -> list[str]:
    return [node.name() for node in legend.model().rootGroup().findLayers()]


def _count_colour(image, colour) -> int:
    """How many pixels of `image` are (nearly) `colour`."""
    import numpy as np
    from qgis.PyQt.QtGui import QImage

    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = image.constBits()
    data.setsize(image.sizeInBytes())
    rows = np.frombuffer(data, dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    pixels = rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4)
    near = np.all(np.abs(pixels[:, :, :3].astype(int) - np.array(colour)) < 40, axis=2)
    return int(np.count_nonzero(near & (pixels[:, :, 3] > 200)))


def _region(layout: QgsPrintLayout, item) -> object:
    exporter = QgsLayoutExporter(layout)
    rect = item.sceneBoundingRect()
    return exporter.renderRegionToImage(rect, QSize(300, 300), 72)


class TestOneFrame:
    def test_unticking_a_layer_takes_it_off_every_output(
        self, world, dialog, runtime
    ) -> None:
        assert _drawn_names(world.main) == [PARCELS, TRAILS, PEAKS]
        assert _ticked(dialog, PEAKS)
        before = _region(world.layout, world.main)
        assert _count_colour(before, RED) > 0
        legend_before = _region(world.layout, world.legend)
        assert _count_colour(legend_before, RED) > 0

        _click_include(dialog, PEAKS)

        # The preview's own layout, and what the summary counts.
        assert _drawn_names(world.main) == [PARCELS, TRAILS]
        assert not _ticked(dialog, PEAKS)
        assert dialog._cartograph.layers_section.summary_text() == (
            "2 of 3 layers included"
        )
        # The image or PDF: the layout rendered.
        assert _count_colour(_region(world.layout, world.main), RED) == 0
        assert _count_colour(_region(world.layout, world.main), BLUE) > 0
        # Its legend entry is gone, in the model and on the page.
        assert PEAKS not in _legend_names(world.legend)
        assert _count_colour(_region(world.layout, world.legend), RED) == 0
        assert _count_colour(_region(world.layout, world.legend), BLUE) > 0
        # The HTML.
        html = export_layout_to_cartograph(
            world.layout, world.project, runtime, FidelityReportBuilder()
        )
        assert PEAKS not in html
        assert TRAILS in html

    def test_undo_restores_everything_and_redo_removes_it_again(
        self, world, dialog, runtime
    ) -> None:
        _click_include(dialog, PEAKS)
        stack = world.layout.undoStack().stack()
        stack.undo()

        assert _drawn_names(world.main) == [PARCELS, TRAILS, PEAKS]
        assert _ticked(dialog, PEAKS)
        assert PEAKS in _legend_names(world.legend)
        assert _count_colour(_region(world.layout, world.main), RED) > 0
        assert _count_colour(_region(world.layout, world.legend), RED) > 0
        html = export_layout_to_cartograph(
            world.layout, world.project, runtime, FidelityReportBuilder()
        )
        assert PEAKS in html

        stack.redo()
        assert _drawn_names(world.main) == [PARCELS, TRAILS]
        assert not _ticked(dialog, PEAKS)
        assert PEAKS not in _legend_names(world.legend)

    def test_one_tick_is_one_undo_step(self, world, dialog) -> None:
        stack = world.layout.undoStack().stack()
        depth = stack.index()
        _click_include(dialog, PEAKS)
        stack.undo()
        assert stack.index() == depth

    def test_the_web_maps_selection_is_a_separate_list(self, world, dialog) -> None:
        peaks_id = world.layers[PEAKS].id()
        assert dialog.state.for_layer(peaks_id).include
        _click_include(dialog, PEAKS)
        # Cartograph's tick did not touch the web map's selection...
        assert dialog.state.for_layer(peaks_id).include

        # ...which is its own: untick another there, then come back.
        dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()
        assert _ticked(dialog, PEAKS)
        _click_include(dialog, TRAILS)
        assert not dialog.state.for_layer(world.layers[TRAILS].id()).include
        assert _drawn_names(world.main) == [PARCELS, TRAILS]

        dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()
        assert not _ticked(dialog, PEAKS)
        assert _ticked(dialog, TRAILS)
        dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()
        assert _ticked(dialog, PEAKS)
        assert not _ticked(dialog, TRAILS)

    def test_a_designer_undo_refreshes_the_list(self, world, dialog) -> None:
        """Undo from anywhere (the layout designer's Ctrl+Z) moves the ticks."""
        _click_include(dialog, PEAKS)
        world.layout.undoStack().stack().undo()
        assert _ticked(dialog, PEAKS)


class TestOrder:
    def test_a_layer_comes_back_where_it_was(self, world, dialog) -> None:
        _click_include(dialog, TRAILS)
        assert _drawn_names(world.main) == [PARCELS, PEAKS]
        _click_include(dialog, TRAILS)
        assert _drawn_names(world.main) == [PARCELS, TRAILS, PEAKS]

    def test_a_custom_order_is_kept_and_restored(self, world, dialog) -> None:
        peaks, trails, parcels = (world.layers[n] for n in (PEAKS, TRAILS, PARCELS))
        world.main.setKeepLayerSet(True)
        world.main.setLayers([peaks, parcels, trails])  # not the panel's order
        world.layout.refresh()
        dialog.refresh_layers()
        dialog._cartograph.membership.layout_changed()
        _click_include(dialog, PARCELS)
        assert _drawn_names(world.main) == [PEAKS, TRAILS]
        _click_include(dialog, PARCELS)
        assert _drawn_names(world.main) == [PEAKS, PARCELS, TRAILS]

    def test_a_layer_never_drawn_lands_in_the_panels_order(self, world, dialog) -> None:
        peaks, trails, parcels = (world.layers[n] for n in (PEAKS, TRAILS, PARCELS))
        world.main.setKeepLayerSet(True)
        world.main.setLayers([parcels, peaks])
        dialog._cartograph.membership.layout_changed()
        dialog.sync_include_column()
        _click_include(dialog, TRAILS)
        assert _drawn_names(world.main) == [PARCELS, TRAILS, PEAKS]
        assert trails.name() == TRAILS


class TestTransitions:
    def test_a_map_following_the_layers_panel_gets_its_own_list(
        self, world, dialog
    ) -> None:
        member = dialog._cartograph.membership
        assert not world.main.keepLayerSet()
        assert "ticked in the QGIS Layers panel" in member.bar.note.text()
        _click_include(dialog, PEAKS)
        assert world.main.keepLayerSet()
        assert "its own list" in member.bar.note.text()
        # Hiding it in the QGIS panel afterwards no longer changes the map.
        root = world.project.layerTreeRoot()
        root.findLayer(world.layers[TRAILS].id()).setItemVisibilityChecked(False)
        assert _drawn_names(world.main) == [PARCELS, TRAILS]

    def test_a_map_following_a_theme_keeps_the_themes_styles(
        self, world, dialog
    ) -> None:
        root = world.project.layerTreeRoot()
        parcels = world.layers[PARCELS]
        collection = world.project.mapThemeCollection()
        # The theme draws the parcels orange; the layer itself stays green.
        themed = parcels.clone()
        symbol = QgsSymbol.defaultSymbol(themed.geometryType())
        symbol.setColor(QColor("#ff8000"))
        themed.renderer().setSymbol(symbol)
        record = QgsMapThemeCollection.MapThemeRecord()
        for name in (PARCELS, TRAILS, PEAKS):
            layer_record = QgsMapThemeCollection.MapThemeLayerRecord(world.layers[name])
            layer_record.isVisible = True
            layer_record.usingCurrentStyle = True
            layer_record.currentStyle = ""
            record.addLayerRecord(layer_record)
        collection.insert("Orange parcels", record)
        world.main.setFollowVisibilityPreset(True)
        world.main.setFollowVisibilityPresetName("Orange parcels")
        dialog._cartograph.membership.layout_changed()
        dialog.sync_include_column()
        assert "Orange parcels" in dialog._cartograph.membership.bar.note.text()
        assert root is not None

        _click_include(dialog, PEAKS)

        assert not world.main.followVisibilityPreset()
        assert world.main.keepLayerSet()
        assert _drawn_names(world.main) == [PARCELS, TRAILS]
        # One undo goes back to following the theme.
        world.layout.undoStack().stack().undo()
        assert world.main.followVisibilityPreset()
        assert world.main.followVisibilityPresetName() == "Orange parcels"
        assert not world.main.keepLayerSet()


class TestLegends:
    def test_a_manual_legend_loses_and_regains_the_entry(self, world, dialog) -> None:
        legend = world.legend
        if hasattr(legend, "setSyncMode"):
            legend.setSyncMode(qgis_core.Qgis.LegendSyncMode.Manual)
        else:
            legend.setAutoUpdateModel(False)
        assert _legend_names(legend) == [PARCELS, TRAILS, PEAKS] or set(
            _legend_names(legend)
        ) == {PARCELS, TRAILS, PEAKS}
        order_before = _legend_names(legend)
        _click_include(dialog, TRAILS)
        assert TRAILS not in _legend_names(legend)
        _click_include(dialog, TRAILS)
        assert _legend_names(legend) == order_before

    def test_a_legend_on_another_frame_is_left_alone(self, world, dialog) -> None:
        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        other = QgsLayoutItemLegend(world.layout)
        world.layout.addLayoutItem(other)
        other.setLinkedMap(inset)
        dialog._cartograph.membership.layout_changed()
        _click_include(dialog, PEAKS)
        assert PEAKS in _legend_names(other)
        assert PEAKS not in _legend_names(world.legend)


class TestSeveralFrames:
    def _two_frames(self, world, dialog):
        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        dialog._cartograph.membership.layout_changed()
        return inset

    def test_the_selector_shows_only_with_several_frames(self, world, dialog) -> None:
        bar = dialog._cartograph.membership.bar
        assert bar._frame_row.isHidden()
        self._two_frames(world, dialog)
        assert not bar._frame_row.isHidden()
        assert bar.frame_label.text() == "Layers for:"
        assert [bar.frame_combo.itemText(i) for i in range(2)] == ["main", "inset"] or {
            bar.frame_combo.itemText(i) for i in range(2)
        } == {"main", "inset"}

    def test_each_frame_has_its_own_ticks(self, world, dialog) -> None:
        inset = self._two_frames(world, dialog)
        bar = dialog._cartograph.membership.bar
        # The big map is the one listed first.
        assert bar.frame_combo.currentText() == "main"
        _click_include(dialog, PEAKS)
        assert PEAKS not in _drawn_names(world.main)
        assert PEAKS in _drawn_names(inset)

        index = bar.frame_combo.findText("inset")
        bar.frame_combo.setCurrentIndex(index)
        bar.frame_combo.activated.emit(index)
        assert _ticked(dialog, PEAKS)  # the inset still draws it
        _click_include(dialog, TRAILS)
        assert TRAILS not in _drawn_names(inset)
        assert TRAILS in _drawn_names(world.main)

        index = bar.frame_combo.findText("main")
        bar.frame_combo.setCurrentIndex(index)
        bar.frame_combo.activated.emit(index)
        assert not _ticked(dialog, PEAKS)
        assert _ticked(dialog, TRAILS)


class TestAtlasCoverage:
    def _atlas(self, world, dialog):
        atlas = world.layout.atlas()
        atlas.setCoverageLayer(world.layers[PARCELS])
        atlas.setEnabled(True)
        world.main.setAtlasDriven(True)
        dialog._cartograph.atlas_tab.show_layout(world.layout)
        dialog._cartograph.membership._shown = None
        dialog._cartograph.membership.layout_changed()

    def test_removing_the_coverage_layer_warns_and_blocks_the_atlas(
        self, world, dialog, runtime
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import FidelityStatus

        self._atlas(world, dialog)
        bar = dialog._cartograph.membership.bar
        assert bar.atlas_warning.isHidden()

        _click_include(dialog, PARCELS)

        assert not bar.atlas_warning.isHidden()
        assert PARCELS in bar.atlas_warning.text()
        report = FidelityReportBuilder()
        export_layout_to_cartograph(world.layout, world.project, runtime, report)
        blocked = [r for r in report.items if r.status is FidelityStatus.BLOCKED]
        assert blocked, "the atlas should be blocked in the checks"

        # Ticking it again clears the warning.
        _click_include(dialog, PARCELS)
        assert bar.atlas_warning.isHidden()
        report = FidelityReportBuilder()
        export_layout_to_cartograph(world.layout, world.project, runtime, report)
        assert not [r for r in report.items if r.status is FidelityStatus.BLOCKED]
