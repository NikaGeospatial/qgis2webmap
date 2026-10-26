"""Taking a layer off a Cartograph map and putting it back loses nothing.

The Include tick is the map frame's membership only. A layer's legend
headings, class labels and left-out classes, a legend that never listed it, and
the map's own styling of it (smoothing, suppressed labels) all come back with
it, in one undo step, and survive a project save. The tick is a real click.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from legend_fixtures import hillshade_layer, ramp_layer, to_gpkg, write_tif
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRendererCategory,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QRectF
from test_cartograph_layer_membership import (
    FakeIface,
    _add_map,
    _click_include,
    _ticked,
)

from nika_onlymap_exporter.core import legend_edit as le
from nika_onlymap_exporter.core.locator_context import draw_quietly
from nika_onlymap_exporter.core.raster_smoothing import set_smoothing, smoothing_on

SITES, ELEVATION, HILLSHADE = "Survey sites", "Elevation", "Hillshade"


class Rich:
    """A map over a categorised, labelled vector layer, a continuous raster
    and a hillshade, with a manual legend linked to it."""

    def __init__(self, project: QgsProject, folder) -> None:
        sites = QgsVectorLayer("Point?crs=EPSG:4326&field=kind:string", SITES, "memory")
        features = []
        for index, key in enumerate("AB"):
            feature = QgsFeature(sites.fields())
            feature["kind"] = key
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(2 + index, 3)))
            features.append(feature)
        sites.dataProvider().addFeatures(features)
        sites.setRenderer(
            QgsCategorizedSymbolRenderer(
                "kind",
                [
                    QgsRendererCategory(
                        key, QgsMarkerSymbol.createSimple({"color": colour}), label
                    )
                    for key, colour, label in (
                        ("A", "red", "Source class A"),
                        ("B", "blue", "Source class B"),
                    )
                ],
            )
        )
        settings = QgsPalLayerSettings()
        settings.fieldName = "kind"
        sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        sites.setLabelsEnabled(True)
        # On disk, so a saved project can be opened again.
        self.sites = to_gpkg(sites, folder)
        self.sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        self.sites.setLabelsEnabled(True)
        self.elevation = ramp_layer(write_tif(folder / "elev.tif"), ELEVATION)
        self.hillshade = hillshade_layer(write_tif(folder / "hill.tif"), HILLSHADE)
        project.addMapLayers([self.hillshade, self.elevation, self.sites])
        self.project = project
        self.layout = QgsPrintLayout(project)
        self.layout.initializeDefaults()
        self.layout.setName("rich")
        self.main = _add_map(self.layout, "main", QRectF(10, 10, 150, 150))
        self.main.setLayers([self.sites, self.elevation, self.hillshade])
        self.main.setKeepLayerSet(True)
        self.legend = QgsLayoutItemLegend(self.layout)
        self.layout.addLayoutItem(self.legend)
        self.legend.setLinkedMap(self.main)
        self.legend.setResizeToContents(False)
        project.layoutManager().addLayout(self.layout)
        le._ensure_manual(self.legend, project)

    def entries(self) -> list[tuple[str, int | None, str, bool]]:
        return [
            (self.project.mapLayer(e.layer_id).name(), e.index, e.label, e.included)
            for e in le.entries(self.legend, self.project)
        ]

    def customise_legend(self) -> None:
        layer = next(
            e
            for e in le.entries(self.legend, self.project)
            if e.layer_id == self.sites.id() and e.index is None
        )
        le.set_entry_label(self.layout, self.legend, self.project, layer, "My sites")
        first = next(
            e
            for e in le.entries(self.legend, self.project)
            if e.layer_id == self.sites.id() and e.index == 0
        )
        le.set_entry_label(self.layout, self.legend, self.project, first, "Class one")
        le.set_class_listed(
            self.layout, self.legend, self.project, self.sites.id(), 1, False
        )
        le.set_layer_listed(
            self.layout, self.legend, self.project, self.hillshade.id(), False
        )

    def styling(self) -> tuple[list[str], bool, bool]:
        """Which layers the map styles by itself, and how (a style's XML is
        rewritten by an undo, so it is read, not compared)."""
        overrides = self.main.layerStyleOverrides()
        quiet = 'labelsEnabled="0"' in overrides.get(self.sites.id(), "")
        return sorted(overrides), smoothing_on(self.main, self.elevation), quiet

    def customise_map(self) -> None:
        set_smoothing(self.main, self.elevation, True)
        draw_quietly(self.main)


@pytest.fixture
def rich(qgis_app, project, tmp_path) -> Rich:
    return Rich(project, tmp_path)


@pytest.fixture
def dialog(rich, monkeypatch):
    """The real dialog on Cartograph, builds off (they are not under test)."""
    from nika_onlymap_exporter.core.settings import DialogOutput
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


def _toggle(dialog, name: str) -> None:
    _click_include(dialog, name)
    _click_include(dialog, name)


class TestLegendSurvivesInclude:
    def test_headings_labels_and_left_out_classes_come_back(self, rich, dialog) -> None:
        rich.customise_legend()
        before = rich.entries()
        assert ("Survey sites", None, "My sites", True) in before
        _toggle(dialog, SITES)
        assert rich.entries() == before

    def test_a_layer_left_out_of_the_legend_stays_out(self, rich, dialog) -> None:
        rich.customise_legend()
        _toggle(dialog, HILLSHADE)
        assert rich.main.layersToRender()[-1].name() == HILLSHADE
        assert le._node_for(rich.legend, rich.hillshade.id()) is None
        assert (HILLSHADE, None, HILLSHADE, False) in rich.entries()

    def test_the_legend_order_comes_back(self, rich, dialog) -> None:
        names = [n.name() for n in rich.legend.model().rootGroup().findLayers()]
        _toggle(dialog, ELEVATION)
        again = [n.name() for n in rich.legend.model().rootGroup().findLayers()]
        assert again == names

    def test_every_legend_property_of_the_node_comes_back(self, rich, dialog) -> None:
        # A continuous raster whose ramp rows are hidden carries
        # `legend/node-order` = "empty"; losing it brings the rows back.
        node = le._node_for(rich.legend, rich.elevation.id())
        node.setCustomProperty("legend/node-order", "empty")
        node.setCustomProperty("legend/example-extra", "kept")

        def properties(layer):
            found = le._node_for(rich.legend, layer.id())
            return {key: found.customProperty(key) for key in found.customProperties()}

        before = {
            layer.id(): properties(layer) for layer in (rich.sites, rich.elevation)
        }
        _toggle(dialog, ELEVATION)
        _toggle(dialog, SITES)
        after = {
            layer.id(): properties(layer) for layer in (rich.sites, rich.elevation)
        }
        assert after == before
        assert after[rich.elevation.id()]["legend/node-order"] == "empty"


class TestMapStylingSurvivesInclude:
    def test_smoothing_and_suppressed_labels_come_back(self, rich, dialog) -> None:
        rich.customise_map()
        assert smoothing_on(rich.main, rich.elevation)
        sites_style = rich.main.layerStyleOverrides()[rich.sites.id()]
        assert 'labelsEnabled="0"' in sites_style
        _toggle(dialog, ELEVATION)
        _toggle(dialog, SITES)
        assert smoothing_on(rich.main, rich.elevation)
        assert rich.main.layerStyleOverrides()[rich.sites.id()] == sites_style
        assert rich.sites.labelsEnabled()

    def test_another_frame_is_left_alone(self, rich, dialog) -> None:
        inset = _add_map(rich.layout, "inset", QRectF(10, 170, 50, 30))
        inset.setLayers([rich.elevation])
        rich.customise_map()
        _toggle(dialog, ELEVATION)
        assert not inset.keepLayerStyles()
        assert [layer.name() for layer in inset.layers()] == [ELEVATION]


class TestUndoAndSave:
    def test_undo_and_redo_restore_exactly(self, rich, dialog) -> None:
        rich.customise_legend()
        rich.customise_map()
        full = (rich.entries(), rich.styling())
        stack = rich.layout.undoStack().stack()
        _click_include(dialog, SITES)
        assert not _ticked(dialog, SITES)
        out = (rich.entries(), rich.styling())
        _click_include(dialog, SITES)
        back = (rich.entries(), rich.styling())
        assert back == full
        stack.undo()
        assert (rich.entries(), rich.styling()) == out
        stack.undo()
        assert (rich.entries(), rich.styling()) == full
        stack.redo()
        stack.redo()
        assert (rich.entries(), rich.styling()) == back

    def test_the_state_survives_save_and_reopen(self, rich, dialog, tmp_path) -> None:
        rich.customise_legend()
        rich.customise_map()
        _click_include(dialog, SITES)
        _click_include(dialog, ELEVATION)
        path = tmp_path / "saved.qgz"
        assert rich.project.write(str(path))
        reopened = QgsProject()
        assert reopened.read(str(path))
        layout = reopened.layoutManager().layoutByName("rich")
        main = next(
            i
            for i in layout.items()
            if isinstance(i, QgsLayoutItemMap) and i.id() == "main"
        )
        legend = next(i for i in layout.items() if isinstance(i, QgsLayoutItemLegend))
        legend.setLinkedMap(main)
        # The ticks go back on in the reopened layout, as a user would.
        from nika_onlymap_exporter.ui.cartograph.layer_membership import (
            set_layer_included,
        )

        set_layer_included(layout, reopened, main, rich.sites.id(), True)
        set_layer_included(layout, reopened, main, rich.elevation.id(), True)
        names = {
            (reopened.mapLayer(e.layer_id).name(), e.index): (e.label, e.included)
            for e in le.entries(legend, reopened)
        }
        assert names[SITES, None] == ("My sites", True)
        assert names[SITES, 0] == ("Class one", True)
        assert names[SITES, 1][1] is False
        assert not names[HILLSHADE, None][1]
        elevation = reopened.mapLayer(rich.elevation.id())
        assert smoothing_on(main, elevation)
        assert 'labelsEnabled="0"' in main.layerStyleOverrides()[rich.sites.id()]
