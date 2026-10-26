"""`core/canvas_cartograph.py`: the real map canvas -> a cartograph layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, all_static, build_parcels_layer

from nika_onlymap_exporter.core.canvas_cartograph import (
    FRAME_ID,
    cartograph_from_canvas,
    cartograph_from_view,
    project_figure_rows,
    project_figures,
    project_text_sizes,
    set_project_figures,
    set_project_text_sizes,
    view_input,
)
from nika_onlymap_exporter.core.cartograph_export import prepare_cartograph
from nika_onlymap_exporter.core.cartograph_ir import (
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    ScalebarSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_templates import TextSizes
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")
qgis_gui = pytest.importorskip("qgis.gui")


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


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _canvas(crs, layers, extent, rotation=0.0):
    canvas = qgis_gui.QgsMapCanvas()
    canvas.resize(800, 500)
    canvas.setDestinationCrs(crs)
    canvas.setLayers(layers)
    canvas.setRotation(rotation)
    canvas.setExtent(extent)
    return canvas


def _utm_canvas(parcels, rotation=0.0):
    extent = parcels.extent()
    extent.grow(200)
    return _canvas(UTM, [parcels], extent, rotation)


def _assert_same_rect(a, b, tolerance) -> None:
    for getter in ("xMinimum", "yMinimum", "xMaximum", "yMaximum"):
        assert getattr(b, getter)() == pytest.approx(
            getattr(a, getter)(), abs=tolerance
        )


class TestCanvasLayout:
    def test_layout_is_added_to_the_project_with_its_name(
        self, project, parcels
    ) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert result.layout.name() == "carto: Survey"
        assert project.layoutManager().layoutByName("carto: Survey") is result.layout

    def test_a_second_click_gets_a_unique_name(self, project, parcels) -> None:
        canvas = _utm_canvas(parcels)
        cartograph_from_canvas(canvas, project)
        second = cartograph_from_canvas(canvas, project)
        assert second.layout.name() == "carto: Survey (2)"

    def test_untitled_project(self, project, parcels) -> None:
        project.setTitle("")
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert result.layout.name() == "carto: map"
        assert not any(item.id == "title" for item in result.plan.items)

    def test_map_item_shows_the_canvas_view(self, project, parcels) -> None:
        canvas = _utm_canvas(parcels)
        result = cartograph_from_canvas(canvas, project)
        map_item = result.layout.itemById(FRAME_ID)
        assert map_item.crs().authid() == "EPSG:32648"
        _assert_same_rect(canvas.mapSettings().extent(), map_item.extent(), 0.01)
        assert map_item.keepLayerSet()
        assert [layer.id() for layer in map_item.layers()] == [parcels.id()]

    def test_frame_box_is_the_plan(self, project, parcels) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        map_item = result.layout.itemById(FRAME_ID)
        box = result.plan.frames[0].box
        pos, size = map_item.positionWithUnits(), map_item.sizeWithUnits()
        assert (pos.x(), pos.y(), size.width(), size.height()) == pytest.approx(
            (box.x, box.y, box.w, box.h), abs=0.01
        )
        page = result.layout.pageCollection().page(0).pageSize()
        assert (page.width(), page.height()) == (
            result.plan.page_width,
            result.plan.page_height,
        )

    def test_wide_canvas_is_landscape_with_furniture(self, project, parcels) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert result.plan.orientation == "landscape"
        kinds = {type(item) for item in result.spec.items}
        assert {FrameSpec, LegendSpec, ScalebarSpec, TextSpec} <= kinds
        assert GraticuleSpec not in kinds
        for item_id in ("legend", "scalebar", "title"):
            assert result.layout.itemById(item_id) is not None, item_id
        # A north-up map needs no arrow (a turned one gets one).
        assert result.layout.itemById("north") is None
        assert result.issues == ()

    def test_canvas_rotation_is_kept(self, project, parcels) -> None:
        canvas = _utm_canvas(parcels, rotation=20)
        result = cartograph_from_canvas(canvas, project)
        map_item = result.layout.itemById(FRAME_ID)
        assert map_item.mapRotation() == pytest.approx(20)
        _assert_same_rect(canvas.mapSettings().extent(), map_item.extent(), 0.01)

    def test_projected_canvas_of_vectors_defaults_to_a_live_frame(
        self, project, parcels
    ) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert list(result.frame_modes.values()) == ["live"]

    def test_rotated_projected_canvas_defaults_to_a_static_frame(
        self, project, parcels
    ) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels, rotation=15), project)
        assert list(result.frame_modes.values()) == ["static"]

    def test_geographic_canvas_gets_a_graticule_and_a_live_frame(
        self, project, parcels
    ) -> None:
        wgs84 = qgis_core.QgsCoordinateReferenceSystem("EPSG:4326")
        canvas = _canvas(
            wgs84, [parcels], qgis_core.QgsRectangle(103.7, 1.25, 103.9, 1.4)
        )
        result = cartograph_from_canvas(canvas, project)
        map_item = result.layout.itemById(FRAME_ID)
        assert map_item.crs().authid() == "EPSG:4326"
        grids = map_item.grids().asList()
        assert len(grids) == 1 and grids[0].enabled()
        (grid,) = [i for i in result.plan.items if i.id == "graticule"]
        assert grids[0].intervalX() == pytest.approx(grid.interval)
        assert list(result.frame_modes.values()) == ["live"]

    def test_credits_become_the_attribution_line(self, project, parcels) -> None:
        metadata = parcels.metadata()
        metadata.setRights(["© Survey Dept"])
        parcels.setMetadata(metadata)
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert result.spec.attribution == "© Survey Dept"
        assert result.layout.itemById("attribution").text() == "© Survey Dept"

    def test_title_label_follows_the_project_title(self, project, parcels) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        assert result.layout.itemById("title").text() == "[% @project_title %]"

    def test_the_layout_exports(self, project, parcels, runtime) -> None:
        result = cartograph_from_canvas(_utm_canvas(parcels), project)
        spec = prepare_cartograph(
            result.layout,
            project,
            runtime,
            FidelityReportBuilder(),
            frame_modes=all_static(result.layout),
        ).spec
        frame = next(item for item in spec.items if isinstance(item, FrameSpec))
        assert frame.mode == "static" and frame.image_bytes
        assert frame.box.w == pytest.approx(result.plan.frames[0].box.w, abs=0.01)
        assert spec.title == "Survey"

    def test_empty_extent_is_rejected(self, project, parcels) -> None:
        with pytest.raises(ValueError):
            cartograph_from_view(qgis_core.QgsRectangle(), UTM, [parcels], project)


class TestKeyFigureVariables:
    """The key figures kept as project variables: what the picker's rows
    show, and how they are written back."""

    def test_rows_keep_every_slot_and_the_figures_only_real_ones(self, project) -> None:
        project.setCustomVariables(
            {
                "nika_figure_1": " 214 | parcels surveyed ",
                "nika_figure_2": "| a label alone",
                "nika_figure_3": "12 km",
            }
        )
        assert project_figure_rows(project) == (
            ("214", "parcels surveyed"),
            ("", "a label alone"),
            ("12 km", ""),
        )
        assert project_figures(project) == (
            ("214", "parcels surveyed"),
            ("12 km", ""),
        )

    def test_written_rows_read_back_the_same(self, project) -> None:
        project.setCustomVariables({"other": "kept"})
        rows = (("214", "parcels surveyed"), ("", "a label alone"), ("12 km", ""))
        set_project_figures(project, rows)
        variables = project.customVariables()
        assert variables["nika_figure_1"] == "214 | parcels surveyed"
        assert variables["nika_figure_2"] == "| a label alone"
        # A value alone is written without the bar.
        assert variables["nika_figure_3"] == "12 km"
        assert variables["other"] == "kept"
        assert project_figure_rows(project) == rows

    def test_a_blank_row_removes_its_variable(self, project) -> None:
        project.setCustomVariables(
            {"nika_figure_1": "1 | one", "nika_figure_2": "2 | two"}
        )
        set_project_figures(project, (("", " "), ("2", "two")))
        variables = project.customVariables()
        assert "nika_figure_1" not in variables
        assert variables["nika_figure_2"] == "2 | two"
        assert "nika_figure_3" not in variables

    def test_a_bar_in_a_value_is_refused(self, project) -> None:
        project.setCustomVariables({})
        with pytest.raises(ValueError):
            set_project_figures(project, (("1|2", "split"),))
        assert project.customVariables() == {}


class TestTextSizeVariables:
    """The title, legend and notes sizes (percent) kept as project variables."""

    def test_written_percents_read_back_and_standard_leaves_no_variable(
        self, project
    ) -> None:
        project.setCustomVariables({"other": "kept"})
        assert project_text_sizes(project) == TextSizes()
        set_project_text_sizes(project, TextSizes(80, 100, 130))
        variables = project.customVariables()
        assert variables["nika_size_title"] == "80"
        assert "nika_size_legend" not in variables
        assert variables["other"] == "kept"
        assert project_text_sizes(project) == TextSizes(80, 100, 130)
        set_project_text_sizes(project, TextSizes())
        assert project.customVariables() == {"other": "kept"}

    def test_a_project_saved_with_step_ids_reads_as_their_percent(
        self, project
    ) -> None:
        project.setCustomVariables(
            {
                "nika_size_title": "smaller",
                "nika_size_legend": "large",
                "nika_size_notes": "nonsense",
            }
        )
        assert project_text_sizes(project) == TextSizes(80, 115, 100)
        set_project_text_sizes(project, project_text_sizes(project))
        assert project.customVariables()["nika_size_legend"] == "115"
        project.setCustomVariables({"nika_size_title": "400"})
        assert project_text_sizes(project).title == 130
        project.setCustomVariables({})

    def test_the_planner_input_takes_the_project_or_the_given_steps(
        self, project, parcels
    ) -> None:
        extent = parcels.extent()
        set_project_text_sizes(project, TextSizes(90, 115, 100))
        saved = view_input(extent, UTM, [parcels], project)
        assert (saved.title_scale, saved.legend_scale, saved.notes_scale) == (
            0.9,
            1.15,
            1.0,
        )
        given = view_input(extent, UTM, [parcels], project, text_sizes=TextSizes(130))
        assert (given.title_scale, given.legend_scale) == (1.3, 1.0)
        project.setCustomVariables({})
