"""The layout's atlas -> `<om-atlas>`, against a real QGIS atlas over three
parcels with a `lot` field.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from atlas_fixtures import add_map, build_atlas_layout

from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_ir import TextSpec
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_reader import atlas_page_count, read_layout
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def atlas_layout(project):
    return build_atlas_layout(project)


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


def _rows(report: FidelityReportBuilder, status: FidelityStatus) -> list[str]:
    return [f"{item.subject}: {item.detail}" for item in report.by_status(status)]


def _sheet(html: str) -> ET.Element:
    match = re.search(r'<template id="sheet">(.*)</template>', html, re.S)
    assert match is not None
    # The live frame's <om-map> markup is HTML, not XML (boolean attributes,
    # inline JSON); wrap it so ElementTree can still walk ids and nesting.
    return ET.fromstring(f"<root>{match.group(1)}</root>")


def _export(atlas_layout, runtime, report=None) -> str:
    # Not `report or ...`: an empty builder is falsy (it has a __len__).
    report = FidelityReportBuilder() if report is None else report
    prepared = prepare_cartograph(
        atlas_layout.layout, atlas_layout.project, runtime, report, raster_cache=None
    )
    return finish_cartograph(prepared, atlas_layout.project, report)


class TestAtlasSpec:
    def test_an_enabled_atlas_is_read(self, atlas_layout) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)

        atlas = reading.atlas
        assert atlas is not None
        main = reading.frames[0]
        assert atlas.for_ == main.frame_id
        assert atlas.layer == atlas_layout.parcels.id()
        assert atlas.filename == "lot_{{atlas.lot}}"
        assert atlas.page_name == "{{atlas.lot}}"
        assert atlas.filter is None and atlas.sort is None
        assert not report.has_blockers

    def test_a_disabled_atlas_writes_nothing(self, atlas_layout) -> None:
        atlas_layout.atlas.setEnabled(False)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        assert reading.atlas is None
        assert reading.frames[0].atlas_fit is None

    def test_qgis_default_filename_keeps_the_page_number(self, atlas_layout) -> None:
        atlas_layout.atlas.setFilenameExpression("'output_'||@atlas_featurenumber")
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        assert reading.atlas.filename == "output_{{atlas.index}}"

    def test_sort_descending(self, atlas_layout) -> None:
        atlas_layout.atlas.setSortFeatures(True)
        atlas_layout.atlas.setSortExpression('"lot"')
        atlas_layout.atlas.setSortAscending(False)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        assert reading.atlas.sort == "-lot"

    def test_an_untranslatable_sort_is_reported(self, atlas_layout) -> None:
        atlas_layout.atlas.setSortFeatures(True)
        atlas_layout.atlas.setSortExpression('length("lot")')
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas.sort is None
        assert any(
            "sorted by" in row for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_filter_is_translated_to_the_core_language(self, atlas_layout) -> None:
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"size\" = 'small'")
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        assert reading.atlas.filter == '($size == "small")'

    def test_an_untranslatable_filter_reports_both_page_counts(
        self, atlas_layout
    ) -> None:
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"lot\" LIKE 'A%'")
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas.filter is None
        rows = _rows(report, FidelityStatus.APPROXIMATED)
        assert any("3 features, where QGIS has 1" in row for row in rows)

    def test_page_count_follows_qgis(self, atlas_layout) -> None:
        assert atlas_page_count(atlas_layout.layout) == 3
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"size\" = 'small'")
        assert atlas_page_count(atlas_layout.layout) == 2
        # Same answer QGIS's own atlas gives.
        assert atlas_layout.atlas.updateFeatures() == 2
        atlas_layout.atlas.setEnabled(False)
        assert atlas_page_count(atlas_layout.layout) is None


class TestAtlasFit:
    def test_auto_scaling_refits_with_half_the_qgis_margin(self, atlas_layout) -> None:
        """QGIS scales the fitted extent by 1 + margin (each side gets half);
        the runtime pads each side by its margin -- so 0.10 becomes 0.05."""
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        main = reading.frames[0]
        assert main.atlas_fit == "feature"
        assert main.atlas_margin == pytest.approx(0.05)

    def test_the_margin_maths_matches_qgis_itself(self, atlas_layout) -> None:
        """Seek QGIS's own atlas to the 1000 x 500 m parcel and measure the
        map: the runtime's padBounds with the written margin must agree."""
        atlas = atlas_layout.atlas
        atlas.beginRender()
        try:
            atlas.seekTo(1)  # A-1: 1000 x 500, wide -> height is the binding side
            map_height = atlas_layout.main_map.extent().height()
        finally:
            atlas.endRender()
        margin = (
            read_layout(atlas_layout.layout, FidelityReportBuilder())
            .frames[0]
            .atlas_margin
        )
        # The map is wider than the parcel's aspect, so its height is the
        # feature's padded height: h * (1 + 2 * runtime margin).
        assert map_height == pytest.approx(500 * (1 + 2 * margin), rel=1e-6)

    def test_zero_margin_is_not_read_as_the_runtime_default(self, atlas_layout) -> None:
        atlas_layout.main_map.setAtlasMargin(0.0)
        main = read_layout(atlas_layout.layout, FidelityReportBuilder()).frames[0]
        assert 0 < main.atlas_margin < 0.001

    @pytest.mark.parametrize("mode", ["Fixed", "Predefined"])
    def test_fixed_and_predefined_scales_follow_qgis_page_by_page(
        self, atlas_layout, mode: str
    ) -> None:
        from qgis.core import QgsLayoutItemMap

        atlas_layout.main_map.setAtlasScalingMode(
            getattr(QgsLayoutItemMap.AtlasScalingMode, mode)
        )
        report = FidelityReportBuilder()
        main = read_layout(atlas_layout.layout, report).frames[0]
        # The runtime leaves the frame alone; the plugin's cameras move it.
        assert main.atlas_fit == "fixed"
        assert main.atlas_margin is None
        assert main.atlas_cameras_from_qgis
        assert not _rows(report, FidelityStatus.APPROXIMATED)
        assert any(
            row.startswith("Map frame") and "scale" in row
            for row in _rows(report, FidelityStatus.PRESERVED)
        )


class TestAtlasCameras:
    """Fixed and predefined scales, and points: each page's camera is where
    QGIS's own atlas puts the map, keyed by the feature id the page script
    finds on the runtime's `om-atlas-page` event."""

    @staticmethod
    def _cameras(html: str) -> dict[str, list[float]]:
        import json

        frame = next(_sheet(html).iter("om-frame"))
        return json.loads(frame.get("data-qgis2webmap-atlas-cameras") or "{}")

    @staticmethod
    def _lonlat(x: float, y: float) -> tuple[float, float]:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsPointXY,
            QgsProject,
        )

        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:3857"),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance(),
        )
        point = transform.transform(QgsPointXY(x, y))
        return point.x(), point.y()

    def test_fixed_scale_keeps_the_zoom_and_centres_each_feature(
        self, atlas_layout, fake_runtime
    ) -> None:
        from qgis.core import QgsLayoutItemMap

        atlas_layout.main_map.setAtlasScalingMode(
            QgsLayoutItemMap.AtlasScalingMode.Fixed
        )
        html = _export(atlas_layout, fake_runtime)
        frame = next(_sheet(html).iter("om-frame"))
        cameras = self._cameras(html)
        assert sorted(cameras) == ["1", "2", "3"]
        for feature in atlas_layout.parcels.getFeatures():
            lon, lat, zoom = cameras[str(feature.id())]
            centre = feature.geometry().boundingBox().center()
            assert (lon, lat) == pytest.approx(
                self._lonlat(centre.x(), centre.y()), abs=1e-7
            )
            # The map's own scale on every page: the authored zoom.
            assert zoom == pytest.approx(float(frame.get("zoom") or 0), abs=1e-4)

    def test_predefined_scales_use_the_scale_qgis_picks(
        self, atlas_layout, fake_runtime
    ) -> None:
        import math

        from qgis.core import QgsLayoutItemMap

        authored_scale = atlas_layout.main_map.scale()
        atlas_layout.main_map.setAtlasScalingMode(
            QgsLayoutItemMap.AtlasScalingMode.Predefined
        )
        atlas_layout.layout.renderContext().setPredefinedScales([50000.0])
        html = _export(atlas_layout, fake_runtime)
        authored_zoom = float(next(_sheet(html).iter("om-frame")).get("zoom") or 0)
        want = authored_zoom - math.log2(50000.0 / authored_scale)
        for _lon, _lat, zoom in self._cameras(html).values():
            assert zoom == pytest.approx(want, abs=1e-3)

    def test_auto_scaling_over_points_keeps_the_scale_like_qgis(
        self, atlas_layout, fake_runtime
    ) -> None:
        """QGIS centres a point at the map's own scale even in Auto mode; the
        runtime's feature fit would zoom to the point instead."""
        from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

        points = QgsVectorLayer(
            "Point?crs=EPSG:3857&field=lot:string", "sites", "memory"
        )
        for x in (100.0, 5200.0):
            feature = QgsFeature(points.fields())
            feature.setAttribute("lot", f"site-{x:.0f}")
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 300.0)))
            points.dataProvider().addFeatures([feature])
        atlas_layout.project.addMapLayer(points)
        atlas_layout.main_map.setLayers([points, atlas_layout.parcels])
        atlas_layout.atlas.setCoverageLayer(points)
        report = FidelityReportBuilder()
        html = _export(atlas_layout, fake_runtime, report)
        frame = next(_sheet(html).iter("om-frame"))
        assert frame.get("atlas-fit") == "fixed"
        cameras = self._cameras(html)
        lon, lat, _zoom = cameras["2"]
        assert (lon, lat) == pytest.approx(self._lonlat(5200.0, 300.0), abs=1e-7)

    def test_auto_scaling_over_polygons_is_left_to_the_runtime(
        self, atlas_layout, fake_runtime
    ) -> None:
        html = _export(atlas_layout, fake_runtime)
        frame = next(_sheet(html).iter("om-frame"))
        assert frame.get("atlas-fit") == "feature"
        assert frame.get("data-qgis2webmap-atlas-cameras") is None

    def test_paging_qgis_atlas_leaves_the_layout_as_it_was(
        self, atlas_layout, fake_runtime
    ) -> None:
        """QGIS's atlas leaves the map on the last page's extent when it is
        done; an autosave must not move the author's map."""
        from qgis.core import QgsLayoutItemMap, QgsRectangle

        atlas_layout.main_map.setAtlasScalingMode(
            QgsLayoutItemMap.AtlasScalingMode.Fixed
        )
        before = QgsRectangle(atlas_layout.main_map.extent())
        _export(atlas_layout, fake_runtime)
        after = atlas_layout.main_map.extent()
        assert after.toString(6) == before.toString(6)


class TestAtlasBlocked:
    def test_a_static_atlas_map_blocks_the_atlas_with_the_remedy(
        self, atlas_layout
    ) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(
            atlas_layout.layout,
            report,
            frame_modes={atlas_layout.main_map.uuid(): "static"},
        )
        assert reading.atlas is None
        blocked = _rows(report, FidelityStatus.BLOCKED)
        assert any("Set the atlas map to Live in the Frames tab" in r for r in blocked)

    def test_coverage_missing_from_the_live_map_blocks_with_the_remedy(
        self, atlas_layout
    ) -> None:
        atlas_layout.main_map.setLayers([atlas_layout.roads])
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas is None
        assert reading.frames[0].atlas_fit is None
        blocked = _rows(report, FidelityStatus.BLOCKED)
        assert any("Add the coverage layer 'parcels'" in r for r in blocked)

    def test_no_coverage_layer_is_blocked(self, atlas_layout) -> None:
        atlas_layout.atlas.setCoverageLayer(None)
        report = FidelityReportBuilder()
        assert read_layout(atlas_layout.layout, report).atlas is None
        assert any(
            "no coverage layer" in r for r in _rows(report, FidelityStatus.BLOCKED)
        )

    def test_a_blocked_atlas_writes_no_om_atlas(
        self, atlas_layout, fake_runtime
    ) -> None:
        atlas_layout.main_map.setLayers([atlas_layout.roads])
        html = _export(atlas_layout, fake_runtime)
        assert "<om-atlas" not in html
        assert "atlas-fit" not in html


class TestAtlasLabels:
    def test_feature_fields_and_page_numbers_stay_live(self, atlas_layout) -> None:
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        (text,) = [i for i in reading.furniture if isinstance(i, TextSpec)]
        assert (
            text.body == "Lot {{atlas.lot}} -- page {{atlas.index}} of {{atlas.count}}"
        )

    def test_without_an_atlas_the_same_label_is_frozen(self, atlas_layout) -> None:
        atlas_layout.atlas.setEnabled(False)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        (text,) = [i for i in reading.furniture if isinstance(i, TextSpec)]
        assert "{{atlas." not in text.body

    def test_a_frozen_atlas_expression_says_it_reads_the_same_on_every_page(
        self, atlas_layout
    ) -> None:
        atlas_layout.label.setText('[% upper("lot") %]')
        report = FidelityReportBuilder()
        read_layout(atlas_layout.layout, report)
        rows = _rows(report, FidelityStatus.APPROXIMATED)
        assert any("same on every page" in row for row in rows)

    def test_the_requirement_lists_every_field_the_atlas_reads(
        self, atlas_layout
    ) -> None:
        atlas_layout.atlas.setSortFeatures(True)
        atlas_layout.atlas.setSortExpression('"area"')
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"size\" = '$x'")
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        assert reading.atlas_requirement is not None
        assert set(reading.atlas_requirement.fields) == {"lot", "size", "area"}


class TestAtlasWritten:
    def test_om_atlas_points_at_a_layer_inside_its_frame(
        self, atlas_layout, fake_runtime
    ) -> None:
        html = _export(atlas_layout, fake_runtime)
        sheet = _sheet(html)
        (atlas,) = sheet.iter("om-atlas")
        frame = next(
            f for f in sheet.iter("om-frame") if f.get("id") == atlas.get("for")
        )
        layer_ids = [layer.get("id") for layer in frame.iter("om-layer")]
        assert atlas.get("layer") in layer_ids
        assert frame.get("atlas-fit") == "feature"
        assert frame.get("atlas-margin") == "0.05"
        assert atlas.get("filename") == "lot_{{atlas.lot}}"

    def test_hide_coverage_exports_the_layer_hidden(
        self, atlas_layout, fake_runtime
    ) -> None:
        atlas_layout.atlas.setHideCoverage(True)
        sheet = _sheet(_export(atlas_layout, fake_runtime))
        (atlas,) = sheet.iter("om-atlas")
        coverage = next(
            layer
            for layer in sheet.iter("om-layer")
            if layer.get("id") == atlas.get("layer")
        )
        assert coverage.get("visible") == "false"

    def test_a_field_missing_from_the_export_is_reported(
        self, atlas_layout, fake_runtime
    ) -> None:
        config = atlas_layout.parcels.attributeTableConfig()
        config.update(atlas_layout.parcels.fields())
        columns = config.columns()
        for column in columns:
            if column.name == "lot":
                column.hidden = True
        config.setColumns(columns)
        atlas_layout.parcels.setAttributeTableConfig(config)
        report = FidelityReportBuilder()
        html = _export(atlas_layout, fake_runtime, report)
        assert "<om-atlas" in html
        rows = _rows(report, FidelityStatus.APPROXIMATED)
        assert any("field 'lot'" in row for row in rows)

    def test_an_unexportable_coverage_layer_drops_the_atlas(
        self, atlas_layout, fake_runtime
    ) -> None:
        """A coverage layer read_project cannot export (here: removed from the
        project after the layout referenced it) blocks rather than writing an
        atlas the runtime rejects."""
        report = FidelityReportBuilder()
        prepared = prepare_cartograph(
            atlas_layout.layout,
            atlas_layout.project,
            fake_runtime,
            report,
            raster_cache=None,
        )
        atlas_layout.project.layerTreeRoot().removeLayer(atlas_layout.parcels)
        html = finish_cartograph(prepared, atlas_layout.project, report)
        assert "<om-atlas" not in html
        assert "atlas-fit" not in html
        assert any(
            "could not be exported" in row
            for row in _rows(report, FidelityStatus.BLOCKED)
        )

    def test_a_second_atlas_driven_live_map_refits_too(
        self, atlas_layout, fake_runtime
    ) -> None:
        detail = add_map(atlas_layout.layout, [atlas_layout.roads], x=170, w=60, h=60)
        detail.setAtlasDriven(True)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        fits = {f.frame_id: f.atlas_fit for f in reading.frames}
        assert list(fits.values()) == ["feature", "feature"]
        # The page turner is the one that carries the coverage layer.
        assert reading.atlas.for_ == reading.frames[0].frame_id
