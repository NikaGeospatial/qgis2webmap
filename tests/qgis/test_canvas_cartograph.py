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
        assert result.plan.title is None

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
        box = result.plan.frame
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
        for item_id in ("legend", "scalebar", "north", "title"):
            assert result.layout.itemById(item_id) is not None, item_id
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
        assert grids[0].intervalX() == pytest.approx(result.plan.graticule_interval)
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
        assert frame.box.w == pytest.approx(result.plan.frame.w, abs=0.01)
        assert spec.title == "Survey"

    def test_empty_extent_is_rejected(self, project, parcels) -> None:
        with pytest.raises(ValueError):
            cartograph_from_view(qgis_core.QgsRectangle(), UTM, [parcels], project)
