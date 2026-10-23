"""The layout's atlas -> `<om-atlas>`, against a real QGIS atlas over three
parcels with a `lot` field.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import ClassVar

import pytest
from atlas_fixtures import (
    add_map,
    build_atlas_layout,
    qgis_atlas_page_ids,
    use_ranked_parcels,
)

from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_ir import ATLAS_ORDER_KEY, TextSpec
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_reader import atlas_page_count, read_layout
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

#: The hidden key column's first value in the hidden-key atlas test.
HIDDEN_KEY_BASE = 4105829371


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


def _export(atlas_layout, runtime, report=None, layer_settings=None) -> str:
    # Not `report or ...`: an empty builder is falsy (it has a __len__).
    report = FidelityReportBuilder() if report is None else report
    prepared = prepare_cartograph(
        atlas_layout.layout, atlas_layout.project, runtime, report, raster_cache=None
    )
    return finish_cartograph(
        prepared, atlas_layout.project, report, layer_settings=layer_settings
    )


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
        assert reading.atlas.sort == ATLAS_ORDER_KEY
        assert reading.atlas.sort_source == '"lot"'
        assert reading.atlas.sort_ascending is False
        # C-3, B-2, A-1: the feature ids QGIS's atlas pages in that order.
        assert reading.atlas_requirement.page_order == (1, 3, 2)

    def test_a_sort_that_is_not_a_field_is_carried_too(self, atlas_layout) -> None:
        atlas_layout.atlas.setSortFeatures(True)
        atlas_layout.atlas.setSortExpression('"area" * -1')
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas.sort == ATLAS_ORDER_KEY
        assert list(reading.atlas_requirement.page_order) == qgis_atlas_page_ids(
            atlas_layout.layout
        )
        assert not _rows(report, FidelityStatus.APPROXIMATED)

    def test_filter_is_written_as_the_ids_qgis_pages(self, atlas_layout) -> None:
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"size\" = 'small'")
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        # Features 1 and 3 are the small ones; the shorter way to say so.
        assert reading.atlas.filter == "!(d.id == 2)"
        assert reading.atlas.filter_source == "\"size\" = 'small'"
        assert any(
            "2 of 3 features" in row for row in _rows(report, FidelityStatus.PRESERVED)
        )

    def test_a_filter_with_no_equivalent_is_still_exact(self, atlas_layout) -> None:
        """`LIKE` has no counterpart in the runtime's expression language;
        the ids QGIS pages need none."""
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression("\"lot\" LIKE 'A%'")
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas.filter == "d.id == 2"
        assert not _rows(report, FidelityStatus.APPROXIMATED)

    def test_an_invalid_filter_is_reported_not_guessed(self, atlas_layout) -> None:
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression('"lot" = = 1')
        report = FidelityReportBuilder()
        reading = read_layout(atlas_layout.layout, report)
        assert reading.atlas.filter is None
        assert any(
            "not a valid QGIS expression" in row
            for row in _rows(report, FidelityStatus.APPROXIMATED)
        )

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

    def test_a_hidden_key_column_leaves_the_ids_and_the_cameras_still_agree(
        self, atlas_layout, fake_runtime, tmp_path
    ) -> None:
        """A GeoPackage's key column is its feature id, and so the exported
        `id`. Hidden from the file, it is replaced by 1, 2, 3 - and the cameras,
        which QGIS keys by its own id, must follow or no page would move."""
        import json

        from osgeo import ogr, osr
        from qgis.core import QgsLayoutItemMap, QgsVectorLayer

        from nika_onlymap_exporter.core.settings import LayerSettings

        path = str(tmp_path / "parcels.gpkg")
        source = ogr.GetDriverByName("GPKG").CreateDataSource(path)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        table = source.CreateLayer(
            "parcels", srs, ogr.wkbPolygon, options=["FID=parcel_pk"]
        )
        table.CreateField(ogr.FieldDefn("lot", ogr.OFTString))
        centres = []
        for offset, feature in enumerate(atlas_layout.parcels.getFeatures()):
            row = ogr.Feature(table.GetLayerDefn())
            # Ten digits: too long to turn up by chance in a 6-digit colour
            # such as the random symbol colour "#987db7", which once did.
            row.SetFID(HIDDEN_KEY_BASE + offset)
            row.SetField("lot", feature.attribute("lot"))
            row.SetGeometry(
                ogr.CreateGeometryFromWkb(bytes(feature.geometry().asWkb()))
            )
            table.CreateFeature(row)
            centres.append(feature.geometry().boundingBox().center())
        source = None
        keyed = QgsVectorLayer(f"{path}|layername=parcels", "keyed", "ogr")
        assert keyed.isValid() and keyed.primaryKeyAttributes()
        atlas_layout.project.addMapLayer(keyed)
        atlas_layout.main_map.setLayers([keyed])
        atlas_layout.atlas.setCoverageLayer(keyed)
        atlas_layout.main_map.setAtlasScalingMode(
            QgsLayoutItemMap.AtlasScalingMode.Fixed
        )

        html = _export(
            atlas_layout,
            fake_runtime,
            layer_settings={keyed.id(): LayerSettings(popup=False)},
        )

        for offset in range(len(centres)):
            assert str(HIDDEN_KEY_BASE + offset) not in html
        assert "parcel_pk" not in html
        cameras = self._cameras(html)
        assert sorted(cameras) == ["1", "2", "3"]
        data = re.search(
            r'<script type="application/json">\s*(\{.*?\})\s*</script>', html, re.S
        )
        assert data is not None
        features = json.loads(data.group(1))["features"]
        assert [f["id"] for f in features] == [1, 2, 3]
        for feature in features:
            lon, lat, _zoom = cameras[str(feature["id"])]
            centre = centres[feature["id"] - 1]
            assert (lon, lat) == pytest.approx(
                self._lonlat(centre.x(), centre.y()), abs=1e-7
            )

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
        assert any("Set the atlas map to Live in Map frames" in r for r in blocked)

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
        # The filter and the sort read feature ids and the carried order, not
        # fields: only the page names and labels need any.
        assert set(reading.atlas_requirement.fields) == {"lot"}
        assert list(reading.atlas_requirement.page_order) == qgis_atlas_page_ids(
            atlas_layout.layout
        )


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


def _layout_state(layout) -> dict:
    """Everything paging an atlas can touch: every map item (extent, scale,
    rotation, size, position, atlas settings) and the atlas itself."""
    from qgis.core import QgsLayoutItemMap

    state: dict = {}
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap):
            size = item.sizeWithUnits()
            position = item.positionWithUnits()
            state[item.uuid()] = {
                "extent": item.extent().toString(6),
                "scale": round(item.scale(), 6),
                "rotation": item.mapRotation(),
                "size": (round(size.width(), 6), round(size.height(), 6)),
                "position": (round(position.x(), 6), round(position.y(), 6)),
                "atlas_driven": item.atlasDriven(),
                "atlas_mode": int(item.atlasScalingMode()),
                "atlas_margin": item.atlasMargin(),
            }
    atlas = layout.atlas()
    context = layout.reportContext()
    coverage = atlas.coverageLayer()
    state["atlas"] = {
        "enabled": atlas.enabled(),
        "page": atlas.currentFeatureNumber(),
        "preview": layout.renderContext().isPreviewRender(),
        "feature": (context.feature().isValid(), context.feature().id()),
        "context_layer": context.layer().id() if context.layer() else None,
        "subset": coverage.subsetString(),
        "filter": (atlas.filterFeatures(), atlas.filterExpression()),
        "sort": (atlas.sortFeatures(), atlas.sortExpression(), atlas.sortAscending()),
    }
    return state


class TestAtlasExtractionLeavesTheLayoutAlone:
    """Reading each atlas page's camera pages the real atlas, which moves
    every atlas-driven map: an Auto map beside a Fixed inset, say, or a
    static frame nobody asked for a camera. None of it may stay."""

    @staticmethod
    def _with_fixed_inset(atlas_layout):
        from qgis.core import QgsLayoutItemMap

        inset = add_map(
            atlas_layout.layout,
            [atlas_layout.parcels],
            x=100,
            y=150,
            w=40,
            h=40,
        )
        inset.setAtlasDriven(True)
        inset.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
        return inset

    def test_an_auto_map_and_a_fixed_inset_are_both_put_back(
        self, atlas_layout, fake_runtime
    ) -> None:
        inset = self._with_fixed_inset(atlas_layout)
        before = _layout_state(atlas_layout.layout)
        _export(atlas_layout, fake_runtime)
        assert _layout_state(atlas_layout.layout) == before
        assert inset.atlasDriven()

    def test_the_designers_atlas_preview_page_survives(
        self, atlas_layout, fake_runtime
    ) -> None:
        self._with_fixed_inset(atlas_layout)
        atlas = atlas_layout.atlas
        atlas.updateFeatures()
        assert atlas.seekTo(1)
        assert atlas.currentFeatureNumber() == 1
        before = _layout_state(atlas_layout.layout)
        _export(atlas_layout, fake_runtime)
        assert _layout_state(atlas_layout.layout) == before

    def test_a_map_nobody_asked_a_camera_for_is_put_back_too(
        self, atlas_layout
    ) -> None:
        from nika_onlymap_exporter.core.cartograph_export import _atlas_page_extents

        self._with_fixed_inset(atlas_layout)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        auto_frame, fixed_frame = reading.frames
        assert auto_frame.atlas_fit == "feature" and fixed_frame.atlas_cameras_from_qgis
        before = _layout_state(atlas_layout.layout)
        pages = _atlas_page_extents(atlas_layout.layout, [fixed_frame])
        assert list(pages) == [fixed_frame.frame_id]
        assert _layout_state(atlas_layout.layout) == before

    def test_a_failure_part_way_leaves_it_alone_as_well(
        self, atlas_layout, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core import cartograph_export

        self._with_fixed_inset(atlas_layout)
        reading = read_layout(atlas_layout.layout, FidelityReportBuilder())
        frames = [f for f in reading.frames if f.atlas_cameras_from_qgis]
        before = _layout_state(atlas_layout.layout)
        real = cartograph_export.QgsRectangle
        calls: list[int] = []

        def failing(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("injected mid-extraction failure")
            return real(*args)

        monkeypatch.setattr(cartograph_export, "QgsRectangle", failing)
        with pytest.raises(RuntimeError, match="injected"):
            cartograph_export._atlas_page_extents(atlas_layout.layout, frames)
        monkeypatch.undo()
        assert len(calls) == 2
        assert _layout_state(atlas_layout.layout) == before


def _ranked_export(atlas_layout, runtime, *, filter_expression=None, sort=None):
    """Export the ranked parcels with the given atlas filter and sort
    (`(expression, ascending)`), returning the document, the `<om-atlas>`
    element and the finished export (for the layer rows)."""
    from nika_onlymap_exporter.core.cartograph_export import finish_cartograph_full

    use_ranked_parcels(atlas_layout)
    atlas = atlas_layout.atlas
    if filter_expression is not None:
        atlas.setFilterFeatures(True)
        atlas.setFilterExpression(filter_expression)
    if sort is not None:
        atlas.setSortFeatures(True)
        atlas.setSortExpression(sort[0])
        atlas.setSortAscending(sort[1])
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(
        atlas_layout.layout, atlas_layout.project, runtime, report, raster_cache=None
    )
    finished = finish_cartograph_full(prepared, atlas_layout.project, report)
    (element,) = _sheet(finished.html).iter("om-atlas")
    return finished, element, report


def _exported_pages(finished, element, all_ids: list[int]) -> list[int]:
    """The feature ids the exported atlas pages, in order: the runtime's
    rule (keep what the filter keeps, order by the carried place, features
    without one last), applied to the exported rows."""
    from atlas_filter_eval import evaluate_filter

    expression = element.get("filter")
    kept = (
        all_ids
        if expression is None
        else [i for i, ok in zip(all_ids, evaluate_filter(expression, all_ids)) if ok]
    )
    (export_project,) = finished.projects
    coverage = next(
        layer
        for layer in export_project.exportable_layers
        if layer.geojson and layer.name == "ranked"
    )
    places = {
        feature["id"]: feature.get(ATLAS_ORDER_KEY)
        for feature in coverage.geojson["features"]
    }
    if element.get("sort") != ATLAS_ORDER_KEY:
        return kept
    return sorted(kept, key=lambda i: (places[i] is None, places[i] or 0))


class TestAtlasMatchesQgis:
    """The exported atlas pages exactly what QGIS's own atlas pages, in the
    same order, wherever JavaScript and QGIS part ways: NULL in comparisons,
    arithmetic, `IN` and the logic operators, NULL placement in a sort, text
    order, and functions the runtime does not have. QGIS's `QgsLayoutAtlas`
    is the oracle."""

    ALL_IDS: ClassVar[list[int]] = [1, 2, 3, 4, 5, 6]

    @pytest.mark.parametrize(
        "expression",
        [
            '"score" <> 1',
            'NOT ("score" = 1)',
            '"score" + 2 > 3',
            '"score" IN (1, 3)',
            '"score" NOT IN (1, 3)',
            '"rank" IS NULL',
            '"rank" IS NOT NULL',
            '"score" > 1 AND "rank" IS NOT NULL',
            '"score" > 1 OR "rank" = \'a1\'',
            'NOT ("score" > 1 AND "rank" = \'B2\')',
            '"score" > 1 OR NOT ("rank" = \'a1\')',
            '"rank" = \'b9\' OR "score" IS NULL',
            "\"rank\" LIKE 'b%'",
            'length("rank") > 2',
            'coalesce("score", 0) > 1',
            '"score" > 100',
            '"score" IS NULL OR "score" IS NOT NULL',
        ],
    )
    def test_filter_pages_what_qgis_pages(
        self, atlas_layout, fake_runtime, expression
    ) -> None:
        finished, element, report = _ranked_export(
            atlas_layout, fake_runtime, filter_expression=expression
        )
        want = sorted(qgis_atlas_page_ids(atlas_layout.layout))
        assert sorted(_exported_pages(finished, element, self.ALL_IDS)) == want
        assert not _rows(report, FidelityStatus.APPROXIMATED)

    @pytest.mark.parametrize(
        ("expression", "ascending"),
        [
            ('"rank"', True),
            ('"rank"', False),
            ('"score"', True),
            ('"score"', False),
            ('length("rank")', True),
            ('"lot"', False),
            ('lower("rank")', True),
        ],
    )
    def test_sort_orders_like_qgis(
        self, atlas_layout, fake_runtime, expression, ascending
    ) -> None:
        finished, element, _report = _ranked_export(
            atlas_layout, fake_runtime, sort=(expression, ascending)
        )
        want = qgis_atlas_page_ids(atlas_layout.layout)
        assert len(want) == 6
        assert _exported_pages(finished, element, self.ALL_IDS) == want

    def test_filter_and_sort_together(self, atlas_layout, fake_runtime) -> None:
        finished, element, _report = _ranked_export(
            atlas_layout,
            fake_runtime,
            filter_expression='"score" <> 1',
            sort=('"rank"', True),
        )
        want = qgis_atlas_page_ids(atlas_layout.layout)
        # NULL first in QGIS' ascending order -- the runtime would put it last.
        # (How "a1" and "B2" compare differs between QGIS versions.)
        assert want[0] == 5 and sorted(want) == [3, 4, 5]
        assert _exported_pages(finished, element, self.ALL_IDS) == want


class TestAtlasRoundTrip:
    """Export an atlas to HTML, import that file as a layout, and compare the
    rebuilt layout's atlas with the original: same coverage layer, filter,
    sort, margin and page names -- and the same pages, in the same order."""

    @staticmethod
    def _round_trip(atlas_layout, runtime, tmp_path):
        from nika_onlymap_exporter.core.layout_builder import import_cartograph_file

        html = _export(atlas_layout, runtime)
        path = tmp_path / "atlas.html"
        path.write_text(html, encoding="utf-8")
        return import_cartograph_file(path, atlas_layout.project)

    def test_the_rebuilt_atlas_matches_the_original(
        self, atlas_layout, fake_runtime, tmp_path
    ) -> None:
        ranked = use_ranked_parcels(atlas_layout)
        atlas = atlas_layout.atlas
        atlas.setFilterFeatures(True)
        atlas.setFilterExpression('"score" <> 1')
        atlas.setSortFeatures(True)
        atlas.setSortExpression('"rank"')
        atlas.setSortAscending(False)
        atlas_layout.main_map.setAtlasMargin(0.10)

        built = self._round_trip(atlas_layout, fake_runtime, tmp_path)

        rebuilt = built.layout.atlas()
        assert rebuilt.enabled()
        assert rebuilt.coverageLayer() is ranked
        assert (rebuilt.filterFeatures(), rebuilt.filterExpression()) == (
            True,
            '"score" <> 1',
        )
        assert (
            rebuilt.sortFeatures(),
            rebuilt.sortExpression(),
            rebuilt.sortAscending(),
        ) == (True, '"rank"', False)
        (driven,) = [
            item
            for item in built.items_by_id.values()
            if getattr(item, "atlasDriven", lambda: False)()
        ]
        assert driven.atlasMargin() == pytest.approx(0.10)
        assert driven.atlasScalingMode() == atlas_layout.main_map.atlasScalingMode()
        # The same pages, in the same order, with the same names.
        assert qgis_atlas_page_ids(built.layout) == qgis_atlas_page_ids(
            atlas_layout.layout
        )
        assert [rebuilt.nameForPage(i) for i in range(rebuilt.count())] == [
            atlas.nameForPage(i) for i in range(atlas.count())
        ]
        assert not [issue for issue in built.issues if "tlas" in issue.subject]

    def test_a_filter_written_for_the_cartograph_is_reported_not_run_by_qgis(
        self, atlas_layout, fake_runtime, tmp_path
    ) -> None:
        """Editing the exported filter means QGIS's text no longer describes
        it; the runtime's language is never handed to QGIS."""
        from nika_onlymap_exporter.core.layout_builder import import_cartograph_file

        use_ranked_parcels(atlas_layout)
        atlas_layout.atlas.setFilterFeatures(True)
        atlas_layout.atlas.setFilterExpression('"score" <> 1')
        html = _export(atlas_layout, fake_runtime).replace(
            'filter="d.id &gt;= 3 &amp;&amp; d.id &lt;= 5"', 'filter="$score &gt; 1"'
        )
        assert 'filter="$score &gt; 1"' in html
        path = tmp_path / "edited.html"
        path.write_text(html, encoding="utf-8")
        built = import_cartograph_file(path, atlas_layout.project)
        assert not built.layout.atlas().filterFeatures()
        assert any("$score > 1" in issue.detail for issue in built.issues)

    def test_the_margin_comes_back_as_it_went(
        self, atlas_layout, fake_runtime, tmp_path
    ) -> None:
        atlas_layout.main_map.setAtlasMargin(0.30)
        built = self._round_trip(atlas_layout, fake_runtime, tmp_path)
        (driven,) = [
            item
            for item in built.items_by_id.values()
            if getattr(item, "atlasDriven", lambda: False)()
        ]
        assert driven.atlasMargin() == pytest.approx(0.30)
