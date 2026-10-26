"""`core/legend_edit.py`: a layout legend's entries, labels and order.

Every edit is one undo step on the layout's stack, legend visibility is never
map visibility, a label edit never renames the source layer, and the edits
survive saving and reopening the project and reach the HTML legend.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from legend_fixtures import CLASS_COUNT, LegendWorld, build_world
from qgis.core import QgsLayoutItemLegend, QgsPrintLayout, QgsProject

from nika_onlymap_exporter.core import legend_edit as le
from nika_onlymap_exporter.core.cartograph_export import export_layout_to_cartograph
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

SUMMITS, LAND, ELEV, NAMED, SHADE = (
    "summits",
    "land",
    "elevation",
    "named",
    "hillshade",
)


@pytest.fixture
def world(qgis_app, project, tmp_path) -> LegendWorld:
    return build_world(project, tmp_path / "data")


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


def _legend(layout: QgsPrintLayout) -> QgsLayoutItemLegend:
    legends = [i for i in layout.items() if isinstance(i, QgsLayoutItemLegend)]
    assert len(legends) == 1
    return legends[0]


def _listed(legend) -> list[str]:
    return [node.layerId() for node in legend.model().rootGroup().findLayers()]


def _map_layers(world: LegendWorld) -> list[str]:
    from qgis.core import QgsLayoutItemMap

    maps = [i for i in world.layout.items() if isinstance(i, QgsLayoutItemMap)]
    return [layer.id() for layer in maps[0].layersToRender()]


def _by(entries, layer, index=None):
    for entry in entries:
        if entry.layer_id == layer.id() and entry.index == index:
            return entry
    raise AssertionError(f"no entry {layer.name()} {index}")


class Steps:
    """Counts undo steps a block of edits adds to the layout's stack."""

    def __init__(self, layout) -> None:
        self.stack = layout.undoStack().stack()

    def __enter__(self) -> Steps:
        self.before = self.stack.index()
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    @property
    def added(self) -> int:
        return self.stack.index() - self.before


class TestTemplateDefaults:
    def test_support_layers_start_out_but_stay_restorable(self, world) -> None:
        legend = _legend(world.layout)
        shade = world.layers[SHADE]
        assert shade.id() not in _listed(legend)
        entries = le.entries(legend, world.project)
        entry = _by(entries, shade)
        assert not entry.included and entry.support
        # Still on the map: only the legend left it out.
        assert shade.id() in _map_layers(world)
        assert shade.id() in {e.layer_id for e in le.restorable(legend, world.project)}

    def test_meaningful_layers_stay_with_their_labels_and_bands(self, world) -> None:
        legend = _legend(world.layout)
        entries = le.entries(legend, world.project)
        for key in (SUMMITS, LAND, ELEV, NAMED):
            assert _by(entries, world.layers[key]).included
        land = [
            e
            for e in entries
            if e.layer_id == world.layers[LAND].id() and e.index is not None
        ]
        assert [e.label for e in land][:2] == ["Class 01", "Class 02"]
        assert len(land) == CLASS_COUNT
        elevation = world.layers[ELEV]
        bands = [
            e for e in entries if e.layer_id == elevation.id() and e.index is not None
        ]
        assert bands[0].label == "Band 1 (Gray)"  # band identification kept

    def test_an_elevation_ramp_gets_its_unit(self, world) -> None:
        entries = le.entries(_legend(world.layout), world.project)
        # The file declares metres; the name did not say so.
        assert _by(entries, world.layers[ELEV]).label == "Elevation tint (m)"
        # A name that already says it is left alone.
        assert _by(entries, world.layers[NAMED]).label == "Relief height (m)"
        # The layer itself keeps its name.
        assert world.layers[ELEV].name() == "Elevation tint"

    def test_an_arbitrary_raster_is_not_discarded(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import hillshade_layer, write_tif

        grey = hillshade_layer(write_tif(tmp_path / "g.tif"), "Survey scan")
        assert le.support_reason(grey) == ""
        assert (
            le.support_reason(
                hillshade_layer(write_tif(tmp_path / "h.tif"), "Shaded relief")
            )
            == "hillshade"
        )

    def test_a_legend_of_only_support_layers_is_left_whole(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import EXTENT, WGS84, hillshade_layer, write_tif

        from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

        shade = hillshade_layer(write_tif(tmp_path / "h.tif"))
        project.addMapLayer(shade)
        built = cartograph_from_view(
            EXTENT, WGS84, [shade], project, template_id="classic"
        )
        legends = [
            i for i in built.layout.items() if isinstance(i, QgsLayoutItemLegend)
        ]
        for legend in legends:
            assert shade.id() in _listed(legend)


class TestEntryEdits:
    def test_a_layer_leaves_and_returns_as_one_undo_step_each(self, world) -> None:
        legend = _legend(world.layout)
        summits = world.layers[SUMMITS]
        map_before = _map_layers(world)
        with Steps(world.layout) as steps:
            assert le.set_layer_listed(
                world.layout, legend, world.project, summits.id(), False
            )
        assert steps.added == 1
        assert summits.id() not in _listed(legend)
        # Legend visibility is not map visibility.
        assert _map_layers(world) == map_before
        assert world.project.mapLayer(summits.id()) is summits
        assert _by(le.entries(legend, world.project), summits).included is False

        stack = world.layout.undoStack().stack()
        stack.undo()
        assert summits.id() in _listed(legend)
        stack.redo()
        assert summits.id() not in _listed(legend)
        stack.undo()

        with Steps(world.layout) as steps:
            assert le.set_layer_listed(
                world.layout, legend, world.project, summits.id(), False
            )
            assert le.set_layer_listed(
                world.layout, legend, world.project, summits.id(), True
            )
        assert steps.added == 2
        assert _listed(legend)[0] == summits.id()  # back where it was

    def test_a_class_leaves_and_returns(self, world) -> None:
        legend = _legend(world.layout)
        land = world.layers[LAND]
        with Steps(world.layout) as steps:
            assert le.set_class_listed(
                world.layout, legend, world.project, land.id(), 3, False
            )
        assert steps.added == 1
        entry = _by(le.entries(legend, world.project), land, 3)
        assert not entry.included and entry.label == "Class 04"
        shown = [
            e.label
            for e in le.entries(legend, world.project)
            if e.layer_id == land.id() and e.included and e.index is not None
        ]
        assert "Class 04" not in shown and len(shown) == CLASS_COUNT - 1
        stack = world.layout.undoStack().stack()
        stack.undo()
        assert _by(le.entries(legend, world.project), land, 3).included
        stack.redo()
        assert not _by(le.entries(legend, world.project), land, 3).included
        assert le.set_class_listed(
            world.layout, legend, world.project, land.id(), 3, True
        )
        order = [
            e.index
            for e in le.entries(legend, world.project)
            if e.layer_id == land.id() and e.index is not None
        ]
        assert order == list(range(CLASS_COUNT))
        assert _map_layers(world).count(land.id()) == 1

    def test_the_last_class_cannot_be_taken_out_alone(self, world) -> None:
        legend = _legend(world.layout)
        land = world.layers[LAND]
        for index in range(CLASS_COUNT - 1):
            le.set_class_listed(
                world.layout, legend, world.project, land.id(), index, False
            )
        assert not le.set_class_listed(
            world.layout, legend, world.project, land.id(), CLASS_COUNT - 1, False
        )

    def test_a_label_edit_never_renames_the_source(self, world) -> None:
        legend = _legend(world.layout)
        land = world.layers[LAND]
        entries = le.entries(legend, world.project)
        with Steps(world.layout) as steps:
            assert le.set_entry_label(
                world.layout, legend, world.project, _by(entries, land), "Cover types"
            )
            assert le.set_entry_label(
                world.layout, legend, world.project, _by(entries, land, 0), "Forest"
            )
        assert steps.added == 2
        after = le.entries(legend, world.project)
        assert _by(after, land).label == "Cover types"
        assert _by(after, land, 0).label == "Forest"
        assert land.name() == "Land cover"
        assert land.renderer().categories()[0].label() == "Class 01"
        stack = world.layout.undoStack().stack()
        stack.undo()
        stack.undo()
        again = le.entries(legend, world.project)
        assert _by(again, land).label == "Land cover"
        assert _by(again, land, 0).label == "Class 01"

    def test_clearing_a_label_returns_the_source_name(self, world) -> None:
        legend = _legend(world.layout)
        land = world.layers[LAND]
        entry = _by(le.entries(legend, world.project), land)
        le.set_entry_label(world.layout, legend, world.project, entry, "Cover types")
        entry = _by(le.entries(legend, world.project), land)
        assert le.set_entry_label(world.layout, legend, world.project, entry, "")
        assert _by(le.entries(legend, world.project), land).label == "Land cover"
        assert not le.set_entry_label(
            world.layout,
            legend,
            world.project,
            _by(le.entries(legend, world.project), land),
            "Land cover",
        )

    def test_layers_and_classes_reorder(self, world) -> None:
        legend = _legend(world.layout)
        summits, land = world.layers[SUMMITS], world.layers[LAND]
        assert _listed(legend)[:2] == [summits.id(), land.id()]
        with Steps(world.layout) as steps:
            assert le.move_entry(
                world.layout,
                legend,
                world.project,
                _by(le.entries(legend, world.project), land),
                -1,
            )
        assert steps.added == 1
        assert _listed(legend)[:2] == [land.id(), summits.id()]
        # The class edits travelled with the layer.
        assert (
            len(
                [
                    e
                    for e in le.entries(legend, world.project)
                    if e.layer_id == land.id() and e.index is not None
                ]
            )
            == CLASS_COUNT
        )
        with Steps(world.layout) as steps:
            assert le.move_entry(
                world.layout,
                legend,
                world.project,
                _by(le.entries(legend, world.project), land, 0),
                1,
            )
        assert steps.added == 1
        order = [
            e.index
            for e in le.entries(legend, world.project)
            if e.layer_id == land.id() and e.index is not None
        ]
        assert order[:3] == [1, 0, 2]
        assert not le.move_entry(
            world.layout,
            legend,
            world.project,
            _by(le.entries(legend, world.project), land, 1),
            -1,
        )
        stack = world.layout.undoStack().stack()
        stack.undo()
        stack.undo()
        assert _listed(legend)[:2] == [summits.id(), land.id()]

    def test_restore_removed_brings_everything_back_in_one_step(self, world) -> None:
        legend = _legend(world.layout)
        land, summits, shade = (
            world.layers[LAND],
            world.layers[SUMMITS],
            world.layers[SHADE],
        )
        le.set_layer_listed(world.layout, legend, world.project, summits.id(), False)
        le.set_class_listed(world.layout, legend, world.project, land.id(), 2, False)
        entry = _by(le.entries(legend, world.project), land)
        le.set_entry_label(world.layout, legend, world.project, entry, "Cover")
        with Steps(world.layout) as steps:
            assert le.restore_removed(world.layout, legend, world.project) == 3
        assert steps.added == 1
        assert not le.restorable(legend, world.project)
        assert {summits.id(), shade.id()} <= set(_listed(legend))
        assert _by(le.entries(legend, world.project), land).label == "Cover"
        world.layout.undoStack().stack().undo()
        assert shade.id() not in _listed(legend)
        assert summits.id() not in _listed(legend)

    def test_a_removed_layer_keeps_its_edits_for_the_return(self, world) -> None:
        legend = _legend(world.layout)
        land = world.layers[LAND]
        entries = le.entries(legend, world.project)
        le.set_entry_label(
            world.layout, legend, world.project, _by(entries, land), "Cover"
        )
        le.set_class_listed(world.layout, legend, world.project, land.id(), 1, False)
        le.set_layer_listed(world.layout, legend, world.project, land.id(), False)
        gone = _by(le.entries(legend, world.project), land)
        assert gone.label == "Cover" and not gone.included
        le.set_layer_listed(world.layout, legend, world.project, land.id(), True)
        after = le.entries(legend, world.project)
        assert _by(after, land).label == "Cover"
        assert not _by(after, land, 1).included

    def test_an_automatic_legend_becomes_manual_on_edit(self, world) -> None:
        legend = _legend(world.layout)
        if hasattr(legend, "setSyncMode"):
            legend.setSyncMode(qgis_core.Qgis.LegendSyncMode.AllProjectLayers)
        else:
            legend.setAutoUpdateModel(True)
        assert not le.is_manual(legend)
        assert le.set_layer_listed(
            world.layout, legend, world.project, world.layers[SUMMITS].id(), False
        )
        assert le.is_manual(legend)
        assert world.layers[SUMMITS].id() not in _listed(legend)
        assert world.layers[SUMMITS].id() in _map_layers(world)

    def test_columns_and_spacing_are_undoable(self, world) -> None:
        legend = _legend(world.layout)
        with Steps(world.layout) as steps:
            assert le.set_columns(world.layout, legend, 3)
            assert le.set_row_spacing(world.layout, legend, 4.0)
        assert steps.added == 2
        assert legend.columnCount() == 3 and abs(le.row_spacing_mm(legend) - 4.0) < 0.01
        assert not le.set_columns(world.layout, legend, 3)
        world.layout.undoStack().stack().undo()
        assert abs(le.row_spacing_mm(legend) - 4.0) > 0.01


class TestUnavailableRange:
    def test_a_ramp_without_a_range_is_flagged_not_rewritten(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import EXTENT, WGS84, ramp_layer, write_tif

        from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

        layer = ramp_layer(
            write_tif(tmp_path / "n.tif"), "Elevation (m)", nan_range=True
        )
        project.addMapLayer(layer)
        built = cartograph_from_view(
            EXTENT, WGS84, [layer], project, template_id="classic"
        )
        legend = _legend(built.layout)
        ramp = [
            e
            for e in le.entries(legend, project)
            if e.index is not None and not e.editable
        ]
        assert ramp and ramp[0].note == le.RANGE_UNAVAILABLE
        # The user's style is untouched.
        shader = layer.renderer().shader().rasterShaderFunction()
        assert shader.minimumValue() != shader.minimumValue()  # still nan

    def test_a_ramp_with_a_range_has_no_note(self, world) -> None:
        notes = [e.note for e in le.entries(_legend(world.layout), world.project)]
        assert set(notes) == {""}


class TestPersistence:
    def _edit(self, world) -> None:
        legend = _legend(world.layout)
        land, summits = world.layers[LAND], world.layers[SUMMITS]
        le.set_entry_label(
            world.layout,
            legend,
            world.project,
            _by(le.entries(legend, world.project), land),
            "Cover",
        )
        le.set_entry_label(
            world.layout,
            legend,
            world.project,
            _by(le.entries(legend, world.project), land, 0),
            "Forest",
        )
        le.set_class_listed(world.layout, legend, world.project, land.id(), 5, False)
        le.move_entry(
            world.layout,
            legend,
            world.project,
            _by(le.entries(legend, world.project), land),
            -1,
        )
        le.set_layer_listed(world.layout, legend, world.project, summits.id(), False)

    def test_edits_survive_save_and_reopen(self, world, tmp_path) -> None:
        self._edit(world)
        path = str(tmp_path / "p.qgz")
        assert world.project.write(path)
        fresh = QgsProject()
        assert fresh.read(path)
        layout = fresh.layoutManager().layouts()[0]
        legend = _legend(layout)
        land = fresh.mapLayersByName("Land cover")[0]
        summits = fresh.mapLayersByName("Named summits")[0]
        hillshade = fresh.mapLayersByName("Hillshade")[0]
        entries = le.entries(legend, fresh)
        assert _by(entries, land).label == "Cover"
        assert _by(entries, land, 0).label == "Forest"
        assert not _by(entries, land, 5).included
        assert not _by(entries, summits).included
        assert not _by(entries, hillshade).included and _by(entries, hillshade).support
        assert _listed(legend)[0] == land.id()
        # Restorable after reopening, and the map still draws all of them.
        assert le.restore_removed(layout, legend, fresh) == 3
        assert land.name() == "Land cover"
        maps = [i for i in layout.items() if i.__class__.__name__ == "QgsLayoutItemMap"]
        assert summits.id() in [x.id() for x in maps[0].layersToRender()]

    def test_the_html_legend_reads_the_same_entries(self, world, runtime) -> None:
        self._edit(world)
        html = export_layout_to_cartograph(
            world.layout, world.project, runtime, FidelityReportBuilder()
        )
        assert "Cover" in html and "Forest" in html
        assert "Class 06" not in html  # the class taken out
        assert "Class 05" in html
        assert "Hillshade" not in html or "hide" in html

    def test_removal_never_removes_the_layer_from_the_exported_map(
        self, world, runtime
    ) -> None:
        legend = _legend(world.layout)
        summits = world.layers[SUMMITS]
        le.set_layer_listed(world.layout, legend, world.project, summits.id(), False)
        html = export_layout_to_cartograph(
            world.layout, world.project, runtime, FidelityReportBuilder()
        )
        assert "Named summits" in html  # drawn by the map frame, if not legended
