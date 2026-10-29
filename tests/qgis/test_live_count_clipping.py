"""Live counts and clipping: a counter counts what the map shows, and a map
clipped to a feature (the atlas page's, or a shape on the page) does not show
what lies outside the clip.

The expectations are the points listed in `clip_count_fixtures` and what the
native QGIS render of the map frame draws (read from pixels), not QGIS's own
counting.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from clip_count_fixtures import (
    BLUE,
    RED,
    build_clip_counts,
    point_layer,
)

pytest.importorskip("qgis.core")
from pdf_text import pdf_pages_text
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLayoutExporter,
    QgsLayoutItemPolygon,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPointF, QSize
from qgis.PyQt.QtGui import QPolygonF
from qgis.PyQt.QtPrintSupport import QPrinter

from nika_onlymap_exporter.core import live_count as lc
from nika_onlymap_exporter.core.print_settings import PrintSettings
from nika_onlymap_exporter.exporters.print_native import print_series
from nika_onlymap_exporter.exporters.print_pdf import export_print_pdf

SIZE = 300


def _count(layout, number) -> int:
    outcome = lc.evaluate(layout, number)
    assert outcome.state is lc.CountState.OK, outcome.message
    assert outcome.count is not None
    return outcome.count


def _drawn(layout, map_item, colour, x: float, y: float) -> bool:
    """Whether the native render of the map frame has `colour` at the ground
    position (x, y)."""
    image = QgsLayoutExporter(layout).renderRegionToImage(
        map_item.sceneBoundingRect(), QSize(SIZE, SIZE), 96
    )
    extent = map_item.extent()
    px = int((x - extent.xMinimum()) / extent.width() * SIZE)
    py = int((extent.yMaximum() - y) / extent.height() * SIZE)
    pixel = image.pixelColor(px, py)
    return (pixel.red(), pixel.green(), pixel.blue()) == colour


@pytest.fixture
def built(project):
    out = build_clip_counts(project)
    out.layout.atlas().beginRender()
    out.layout.atlas().seekTo(0)  # the triangle
    return out


class TestAtlasClipping:
    def test_a_point_the_clip_hides_is_not_counted(self, built) -> None:
        # The native render draws the point inside the triangle and not the
        # one outside it ...
        assert _drawn(built.layout, built.map_item, RED, 2, 2)
        assert not _drawn(built.layout, built.map_item, RED, 8, 8)
        # ... and the counter agrees.
        assert _count(built.layout, built.clipped_total) == 1

    def test_a_layer_left_out_of_the_clipping_keeps_all_its_points(self, built) -> None:
        assert _drawn(built.layout, built.map_item, BLUE, 1, 6)
        assert _drawn(built.layout, built.map_item, BLUE, 6, 6)
        assert _count(built.layout, built.free_total) == 2

    def test_clipping_switched_off_counts_everything_in_view(self, project) -> None:
        out = build_clip_counts(project, atlas_clipping=False)
        out.layout.atlas().beginRender()
        out.layout.atlas().seekTo(0)
        assert _drawn(out.layout, out.map_item, RED, 8, 8)
        assert _count(out.layout, out.clipped_total) == 2

    def test_clipping_every_layer_when_no_restriction_is_set(self, built) -> None:
        built.map_item.atlasClippingSettings().setRestrictToLayers(False)
        assert not _drawn(built.layout, built.map_item, BLUE, 6, 6)
        assert _count(built.layout, built.free_total) == 1

    def test_the_source_data_is_left_alone(self, built) -> None:
        before = [f.geometry().asWkt() for f in built.clipped.getFeatures()]
        cover_before = [f.geometry().asWkt() for f in built.coverage.getFeatures()]
        _count(built.layout, built.clipped_total)
        assert [f.geometry().asWkt() for f in built.clipped.getFeatures()] == before
        assert [
            f.geometry().asWkt() for f in built.coverage.getFeatures()
        ] == cover_before

    def test_a_feature_in_two_parts_counts_once(self, project) -> None:
        out = build_clip_counts(project)
        multi = point_layer("multi", [], "#00aa00")
        feature = next(iter(out.clipped.getFeatures()))
        feature.setGeometry(QgsGeometry.fromWkt("MultiPoint((2 2),(1 1),(8 8))"))
        multi.dataProvider().addFeatures([feature])
        project.addMapLayer(multi)
        out.map_item.setLayers([multi])
        lc.write_spec(
            out.clipped_total,
            lc.CountSpec(
                map_uuid=out.map_item.uuid(),
                layer_id=multi.id(),
                layer_name=multi.name(),
            ),
        )
        out.map_item.atlasClippingSettings().setLayersToClip([multi])
        out.layout.atlas().beginRender()
        out.layout.atlas().seekTo(0)
        assert _count(out.layout, out.clipped_total) == 1

    def test_a_line_crossing_the_clip_edge_counts_once(self, project) -> None:
        out = build_clip_counts(project)
        # one line that crosses the triangle's edge several times
        roads = QgsVectorLayer("LineString?crs=EPSG:4326&field=n:string", "r", "memory")
        feature = QgsFeature(roads.fields())
        feature.setGeometry(QgsGeometry.fromWkt("LineString(1 1,9 9,9 1,1 9,5 5)"))
        roads.dataProvider().addFeatures([feature])
        project.addMapLayer(roads)
        out.map_item.setLayers([roads])
        lc.write_spec(
            out.clipped_total,
            lc.CountSpec(
                map_uuid=out.map_item.uuid(),
                layer_id=roads.id(),
                layer_name="r",
            ),
        )
        out.map_item.atlasClippingSettings().setLayersToClip([roads])
        out.layout.atlas().beginRender()
        out.layout.atlas().seekTo(0)
        assert _count(out.layout, out.clipped_total) == 1

    def test_the_next_page_is_clipped_to_its_own_feature(self, built) -> None:
        built.layout.atlas().seekTo(1)  # the square, no point of `clipped` hidden
        assert _count(built.layout, built.clipped_total) == 1
        assert _count(built.layout, built.free_total) == 1


class TestClippingWithRotationAndCrs:
    def test_a_rotated_map_in_another_crs_still_agrees_with_the_render(
        self, project
    ) -> None:
        out = build_clip_counts(project)
        # Show the page in Web Mercator, turned: points and the clip are
        # carried across the CRS.
        out.map_item.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        out.map_item.setMapRotation(20)
        out.layout.atlas().beginRender()
        out.layout.atlas().seekTo(0)
        extent = out.map_item.visibleExtentPolygon()
        assert not extent.isEmpty()
        # The marker for (2,2) is on screen, the one for (8,8) is hidden by the clip.
        inside_count = _count(out.layout, out.clipped_total)
        assert inside_count == 1
        assert _count(out.layout, out.free_total) == 2


class TestItemClipping:
    def _shape_clip(self, project):
        out = build_clip_counts(project, atlas_clipping=False)
        out.layout.atlas().setEnabled(False)
        out.map_item.setAtlasDriven(False)
        out.map_item.zoomToExtent(QgsRectangle(-1, -1, 11, 11))
        rect = out.map_item.sceneBoundingRect()

        def to_page(x: float, y: float) -> QPointF:
            return QPointF(
                rect.left() + (x + 1) / 12 * rect.width(),
                rect.top() + (11 - y) / 12 * rect.height(),
            )

        polygon = QPolygonF([to_page(0, 0), to_page(10, 0), to_page(0, 10)])
        shape = QgsLayoutItemPolygon(polygon, out.layout)
        out.layout.addLayoutItem(shape)
        clip = out.map_item.itemClippingSettings()
        clip.setSourceItem(shape)
        clip.setEnabled(True)
        return out

    def test_a_shape_that_clips_the_map_hides_what_lies_outside_it(
        self, project
    ) -> None:
        out = self._shape_clip(project)
        assert _drawn(out.layout, out.map_item, RED, 2, 2)
        assert not _drawn(out.layout, out.map_item, RED, 8, 8)
        assert _count(out.layout, out.clipped_total) == 1

    def test_a_layer_left_out_of_the_atlas_clip_is_still_cut_by_a_shape(
        self, project
    ) -> None:
        out = self._shape_clip(project)
        drawn = _drawn(out.layout, out.map_item, BLUE, 6, 6)
        assert _count(out.layout, out.free_total) == (2 if drawn else 1)


def _read(page_text: str, caption: str) -> int:
    found = re.search(rf"{caption}\s+(\S+)", page_text)
    assert found is not None, f"{caption!r} is not on the page:\n{page_text}"
    return int(found.group(1))


class TestPrintedCountsMatchThePreview:
    def test_pdf_and_printed_pages_carry_the_clipped_numbers(
        self, project, tmp_path
    ) -> None:
        out = build_clip_counts(project)
        out_pdf = tmp_path / "atlas.pdf"
        export_print_pdf(out.layout, PrintSettings(), out_pdf, atlas=True)
        pages = pdf_pages_text(out_pdf)
        assert [_read(p, "CLIPPED") for p in pages] == [1, 1]
        assert [_read(p, "FREE") for p in pages] == [2, 1]

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printed = tmp_path / "printed.pdf"
        printer.setOutputFileName(str(printed))
        print_series(out.layout, printer)
        pages = pdf_pages_text(printed)
        assert [_read(p, "CLIPPED") for p in pages] == [1, 1]
        assert [_read(p, "FREE") for p in pages] == [2, 1]
