"""A live frame in a projected CRS draws in that CRS (`<om-map crs>`), when
every layer it carries can; otherwise it stays in Web Mercator and says why.

Against the survey fixture's real UTM 48N layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import pytest
from cartograph_fixtures import build_survey_layout

from nika_onlymap_exporter.core.cartograph_export import (
    _plane_camera,
    finish_cartograph,
    plane_metres_per_unit,
    plane_view_width,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_import import parse_cartograph_html
from nika_onlymap_exporter.core.cartograph_ir import Box, CartographSpec, FrameSpec
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_builder import build_layout
from nika_onlymap_exporter.core.layout_reader import (
    default_mode_for_map,
    plane_blocking_layers,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def survey(project, tmp_path):
    built = build_survey_layout(project, tmp_path)
    built.layout.pageCollection().deletePage(1)
    return built


@pytest.fixture
def fake_runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


def _export(survey, runtime, report: FidelityReportBuilder) -> str:
    modes = {survey.main_map.uuid(): "live", survey.inset_map.uuid(): "static"}
    prepared = prepare_cartograph(
        survey.layout,
        survey.project,
        runtime,
        report,
        frame_modes=modes,
        raster_cache=None,
    )
    return finish_cartograph(prepared, survey.project, report)


def _om_map_tag(html: str) -> str:
    match = re.search(r"<om-map\b[^>]*>", html, re.S)
    assert match is not None
    return match.group(0)


def _live_frame(html: str) -> ET.Element:
    match = re.search(r'<template id="sheet">(.*)</template>', html, re.S)
    assert match is not None
    root = ET.fromstring(f"<root>{match.group(1)}</root>")
    return next(f for f in root.iter("om-frame") if f.get("mode") != "static")


def _rows(report: FidelityReportBuilder, status: FidelityStatus) -> list[str]:
    return [f"{item.subject}: {item.detail}" for item in report.by_status(status)]


class TestDrawnInItsOwnCrs:
    def test_the_map_is_drawn_in_utm_and_said_to_be(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        html = _export(survey, fake_runtime, report)
        tag = _om_map_tag(html)
        assert 'crs="EPSG:32648"' in tag
        assert 'crs-def="+proj=utm +zone=48' in tag
        assert 'basemap="none"' in tag
        assert any(
            "drawn in EPSG:32648" in row
            for row in _rows(report, FidelityStatus.PRESERVED)
        )
        assert not any(
            "Web Mercator" in row for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_the_camera_is_the_plane_extent_at_its_metres_per_pixel(
        self, survey, fake_runtime
    ) -> None:
        """The runtime's plane camera: centre unprojected, zoom the Web
        Mercator scale at that latitude (deck's 40.03e6 m circumference)."""
        extent = survey.main_map.extent()
        to_wgs84 = qgis_core.QgsCoordinateTransform(
            survey.main_map.crs(),
            qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"),
            survey.project,
        )
        centre = to_wgs84.transform(extent.center())
        width_px = survey.main_map.sizeWithUnits().width() * 96 / 25.4
        want_zoom = math.log2(
            40.03e6
            * math.cos(math.radians(centre.y()))
            / 512
            / (extent.width() / width_px)
        )

        frame = _live_frame(_export(survey, fake_runtime, FidelityReportBuilder()))
        lon, lat = (
            float(v) for v in (frame.get("center") or "").strip("[]").split(",")
        )
        assert (lon, lat) == pytest.approx((centre.x(), centre.y()), abs=1e-7)
        assert float(frame.get("zoom") or 0) == pytest.approx(want_zoom, abs=1e-4)


class TestStaysInWebMercator:
    def test_a_rotated_map_keeps_its_rotation_in_web_mercator(
        self, survey, fake_runtime
    ) -> None:
        survey.main_map.setMapRotation(15)
        report = FidelityReportBuilder()
        html = _export(survey, fake_runtime, report)
        assert "crs=" not in _om_map_tag(html)
        assert _live_frame(html).get("bearing") == "15"
        assert any(
            "Web Mercator" in row for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_a_geographic_map_is_not_drawn_as_a_stretched_plane(
        self, survey, fake_runtime
    ) -> None:
        """The runtime refuses a degree-based plane, so EPSG:4326 stays in
        Web Mercator, as before."""
        survey.main_map.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"))
        report = FidelityReportBuilder()
        html = _export(survey, fake_runtime, report)
        assert "crs=" not in _om_map_tag(html)
        assert any(
            "Web Mercator" in row for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_a_raster_keeps_the_frame_in_web_mercator_and_is_named(
        self, survey, fake_runtime, make_raster_layer
    ) -> None:
        raster = make_raster_layer(
            "dem", crs="EPSG:32648", west=372000, north=145000, pixel_size=100
        )
        survey.project.addMapLayer(raster)
        survey.main_map.setLayers([survey.parcels, raster])
        report = FidelityReportBuilder()
        html = _export(survey, fake_runtime, report)
        assert "crs=" not in _om_map_tag(html)
        assert any(
            "'dem'" in row and "Web Mercator" in row
            for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_image_tiles_are_warped_into_the_plane(self, survey, fake_runtime) -> None:
        source = "type=xyz&url=" + quote(
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png", safe=":/"
        )
        tiles = qgis_core.QgsRasterLayer(source, "OSM", "wms")
        survey.project.addMapLayer(tiles)
        survey.main_map.setLayers([survey.parcels, tiles])
        html = _export(survey, fake_runtime, FidelityReportBuilder())
        assert 'crs="EPSG:32648"' in _om_map_tag(html)


class TestAtlasInAPlane:
    def test_an_atlas_map_in_utm_follows_qgis_pages_in_the_plane(
        self, survey, fake_runtime
    ) -> None:
        import json

        layout = survey.layout
        survey.main_map.setAtlasDriven(True)
        survey.main_map.setAtlasScalingMode(
            qgis_core.QgsLayoutItemMap.AtlasScalingMode.Auto
        )
        atlas = layout.atlas()
        atlas.setCoverageLayer(survey.parcels)
        atlas.setEnabled(True)
        html = _export(survey, fake_runtime, FidelityReportBuilder())
        frame = _live_frame(html)
        # The runtime's feature fit is Web Mercator maths; QGIS's pages are not.
        assert frame.get("atlas-fit") == "fixed"
        cameras = json.loads(frame.get("data-qgis2webmap-atlas-cameras") or "{}")
        assert len(cameras) == survey.parcels.featureCount()


class TestCanvasMode:
    def test_a_live_canvas_frame_draws_in_the_canvas_crs(
        self, survey, fake_runtime
    ) -> None:
        """The canvas becomes a layout first, so its live frame goes through
        the same projected-plane path as any layout's."""
        from nika_onlymap_exporter.core.canvas_cartograph import (
            cartograph_from_view,
        )

        canvas = cartograph_from_view(
            survey.main_map.extent(),
            survey.main_map.crs(),
            [survey.parcels],
            survey.project,
        )
        prepared = prepare_cartograph(
            canvas.layout,
            survey.project,
            fake_runtime,
            FidelityReportBuilder(),
            frame_modes=dict.fromkeys(canvas.frame_modes, "live"),
            raster_cache=None,
        )
        html = finish_cartograph(prepared, survey.project, FidelityReportBuilder())
        assert 'crs="EPSG:32648"' in _om_map_tag(html)


class TestImportedBackIntoItsCrs:
    """Importing the cartograph builds the map item back in the plane's CRS,
    over the extent QGIS had."""

    def test_the_survey_map_comes_back_in_utm(
        self, survey, fake_runtime, project
    ) -> None:
        original = survey.main_map.extent()
        imported = parse_cartograph_html(
            _export(survey, fake_runtime, FidelityReportBuilder())
        )
        frame = next(
            item
            for item in imported.spec.items
            if isinstance(item, FrameSpec) and item.mode == "live"
        )
        assert frame.plane_crs == "EPSG:32648"
        built = build_layout(imported.spec, project)
        rebuilt = built.items_by_id[frame.id]
        assert rebuilt.crs().authid() == "EPSG:32648"
        assert rebuilt.mapRotation() == 0
        extent = rebuilt.extent()
        # Zoom is written to 4 places (0.003% of the width) and the centre
        # to 6 (0.1 m); a Web Mercator rebuild is off by kilometres.
        tolerance = original.width() * 1e-4
        for got, want in (
            (extent.xMinimum(), original.xMinimum()),
            (extent.yMinimum(), original.yMinimum()),
            (extent.xMaximum(), original.xMaximum()),
            (extent.yMaximum(), original.yMaximum()),
        ):
            assert got == pytest.approx(want, abs=tolerance)

    def test_a_crs_qgis_cannot_use_stays_in_web_mercator_and_says_so(
        self, project
    ) -> None:
        spec = CartographSpec(
            cartograph_id="c",
            size="A4",
            orientation="landscape",
            items=(
                FrameSpec(
                    id="main",
                    box=Box(10, 10, 200, 120),
                    center=(103.8, 1.3),
                    zoom=12,
                    plane_crs="EPSG:999999",
                ),
            ),
        )
        built = build_layout(spec, project)
        assert built.items_by_id["main"].crs().authid() == "EPSG:3857"
        assert any("EPSG:999999" in issue.detail for issue in built.issues)


@pytest.mark.parametrize("zoom_shift", [0.0, 2.5, -1.25])
def test_plane_view_width_is_the_camera_run_backwards(survey, zoom_shift) -> None:
    crs = survey.main_map.crs()
    rect = survey.main_map.extent()
    rect.scale(2.0**-zoom_shift)
    width_mm = survey.main_map.sizeWithUnits().width()
    (_lon, lat), zoom = _plane_camera(crs, rect, width_mm, survey.project)
    assert plane_view_width(lat, zoom, width_mm, crs.toProj()) == pytest.approx(
        rect.width(), rel=1e-9
    )


@pytest.mark.parametrize(
    ("definition", "metres"),
    [
        ("+proj=utm +zone=48 +datum=WGS84 +units=m +no_defs", 1.0),
        ("+proj=lcc +lat_1=33 +units=us-ft +no_defs", 0.3048),
        ("+proj=tmerc +k=1 +to_meter=0.201168 +no_defs", 0.201168),
        ("+proj=tmerc +k=1 +no_defs", 1.0),
    ],
)
def test_plane_units_are_priced_like_the_runtime(
    definition: str, metres: float
) -> None:
    assert plane_metres_per_unit(definition) == pytest.approx(metres)


class TestDefaultMode:
    """A projected map starts live exactly when it would draw in its own
    CRS, so the default never makes a map less like QGIS."""

    def test_a_utm_map_of_vectors_starts_live(self, survey) -> None:
        assert default_mode_for_map(survey.main_map) == "live"

    def test_a_rotated_utm_map_starts_static(self, survey) -> None:
        survey.main_map.setMapRotation(15)
        assert default_mode_for_map(survey.main_map) == "static"

    def test_a_raster_file_keeps_it_static(self, survey, make_raster_layer) -> None:
        raster = make_raster_layer(
            "dem", crs="EPSG:32648", west=372000, north=145000, pixel_size=100
        )
        survey.project.addMapLayer(raster)
        survey.main_map.setLayers([survey.parcels, raster])
        assert default_mode_for_map(survey.main_map) == "static"
        assert plane_blocking_layers(survey.main_map) == ["dem"]

    @pytest.mark.parametrize(
        ("template", "mode"),
        [
            ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "live"),
            ("https://tiles.example.com/{z}/{x}/{y}.pbf", "static"),
        ],
    )
    def test_xyz_tiles_follow_the_runtime_image_rule(
        self, survey, template: str, mode: str
    ) -> None:
        source = "type=xyz&url=" + quote(template, safe=":/")
        tiles = qgis_core.QgsRasterLayer(source, "tiles", "wms")
        survey.project.addMapLayer(tiles)
        survey.main_map.setLayers([survey.parcels, tiles])
        assert default_mode_for_map(survey.main_map) == mode

    def test_web_mercator_and_wgs84_start_live_as_before(self, survey) -> None:
        for authid in ("EPSG:3857", "EPSG:4326"):
            survey.main_map.setCrs(qgis_core.QgsCoordinateReferenceSystem(authid))
            assert default_mode_for_map(survey.main_map) == "live"

    def test_an_export_without_a_choice_uses_it(self, survey, fake_runtime) -> None:
        prepared = prepare_cartograph(
            survey.layout,
            survey.project,
            fake_runtime,
            FidelityReportBuilder(),
            frame_modes={},
            raster_cache=None,
        )
        html = finish_cartograph(prepared, survey.project, FidelityReportBuilder())
        assert 'crs="EPSG:32648"' in _om_map_tag(html)

    def test_the_frames_tab_and_processing_agree(self, survey) -> None:
        from nika_onlymap_exporter.processing.export_layout import _frame_modes
        from nika_onlymap_exporter.ui.cartograph.frames import layout_map_items

        listed = {f.item_id: f.default_mode for f in layout_map_items(survey.layout)}
        assert listed == _frame_modes(survey.layout, "auto")
        assert listed[survey.main_map.uuid()] == "live"
