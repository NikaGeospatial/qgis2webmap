"""`exporters/frame_raster.py`: static-frame rendering and georeferencing.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import build_survey_layout

from nika_onlymap_exporter.exporters.frame_raster import render_static_frame

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def survey(project, tmp_path):
    return build_survey_layout(project, tmp_path)


def _corners_to_item_mm(survey, map_item, corners):
    """Transform each (lon, lat) corner back into the map item's own
    millimetre coordinates, via `mapToItemCoords` -- the same comparison
    `specs/cartograph/spikes/render.py` used to prove 0.0000 mm error."""
    from qgis.core import QgsCoordinateTransform, QgsPointXY
    from qgis.PyQt.QtCore import QPointF

    wgs84 = qgis_core.QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(wgs84, map_item.crs(), survey.project)
    placed = []
    for lon, lat in corners:
        mapped = transform.transform(QgsPointXY(lon, lat))
        item_point = map_item.mapToItemCoords(QPointF(mapped.x(), mapped.y()))
        placed.append((item_point.x(), item_point.y()))
    return placed


class TestStaticFrameRender:
    def test_renders_png_bytes(self, survey) -> None:
        render = render_static_frame(survey.layout, survey.main_map, dpi=100)
        assert render.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(render.png_bytes) > 100

    def test_crs_fields(self, survey) -> None:
        render = render_static_frame(survey.layout, survey.main_map, dpi=100)
        assert render.crs_authid == "EPSG:32648"
        assert "+proj=utm" in render.crs_def

    def test_corners_match_item_geometry_within_tolerance(self, survey) -> None:
        """The four corners, transformed back through `mapToItemCoords`,
        must land on the item's own (0,0)-(w,h) rectangle within 0.01 mm --
        the same check `specs/cartograph/spike-findings.md` #2 ran, which
        found exactly 0.0000 mm of error on an unrotated frame."""
        render = render_static_frame(survey.layout, survey.main_map, dpi=100)
        placed = _corners_to_item_mm(survey, survey.main_map, render.corners)
        size = survey.main_map.sizeWithUnits()
        expected = [
            (0.0, 0.0),
            (size.width(), 0.0),
            (size.width(), size.height()),
            (0.0, size.height()),
        ]
        for (ex, ey), (px, py) in zip(expected, placed):
            assert px == pytest.approx(ex, abs=0.01)
            assert py == pytest.approx(ey, abs=0.01)

    def test_corners_match_item_geometry_when_rotated(self, survey) -> None:
        survey.main_map.setMapRotation(25)
        survey.main_map.refresh()
        render = render_static_frame(survey.layout, survey.main_map, dpi=100)
        placed = _corners_to_item_mm(survey, survey.main_map, render.corners)
        size = survey.main_map.sizeWithUnits()
        expected = [
            (0.0, 0.0),
            (size.width(), 0.0),
            (size.width(), size.height()),
            (0.0, size.height()),
        ]
        for (ex, ey), (px, py) in zip(expected, placed):
            assert px == pytest.approx(ex, abs=0.01)
            assert py == pytest.approx(ey, abs=0.01)

    def test_other_items_are_hidden_during_render_then_restored(self, survey) -> None:
        assert survey.legend.isVisible()
        assert survey.scalebar.isVisible()

        seen_visible_during_render = {}

        original_render_region = __import__(
            "nika_onlymap_exporter.exporters.frame_raster", fromlist=["_render_region"]
        )._render_region

        def _spy(layout, map_item, dpi):
            seen_visible_during_render["legend"] = survey.legend.isVisible()
            seen_visible_during_render["scalebar"] = survey.scalebar.isVisible()
            seen_visible_during_render["main_map"] = map_item.isVisible()
            return original_render_region(layout, map_item, dpi)

        import nika_onlymap_exporter.exporters.frame_raster as frame_raster

        monkeypatched = frame_raster._render_region
        frame_raster._render_region = _spy
        try:
            render_static_frame(survey.layout, survey.main_map, dpi=100)
        finally:
            frame_raster._render_region = monkeypatched

        assert seen_visible_during_render["legend"] is False
        assert seen_visible_during_render["scalebar"] is False
        assert seen_visible_during_render["main_map"] is True

        # Restored afterwards.
        assert survey.legend.isVisible()
        assert survey.scalebar.isVisible()

    def test_visibility_is_restored_even_if_the_render_raises(self, survey) -> None:
        import nika_onlymap_exporter.exporters.frame_raster as frame_raster

        original = frame_raster._render_region

        def _boom(layout, map_item, dpi):
            raise RuntimeError("simulated render failure")

        frame_raster._render_region = _boom
        try:
            with pytest.raises(RuntimeError):
                render_static_frame(survey.layout, survey.main_map, dpi=100)
        finally:
            frame_raster._render_region = original

        assert survey.legend.isVisible()
        assert survey.scalebar.isVisible()

    def test_own_grid_and_overview_are_left_to_the_runtime(self, survey) -> None:
        """The runtime draws `<om-graticule>` and `overview-of` over a static
        frame itself, so a raster that also baked QGIS's own grid or inset
        outline in would show both, slightly apart."""
        grid = survey.main_map.grids().asList()[0]
        overview = survey.inset_map.overviews().asList()[0]

        with_grid = render_static_frame(survey.layout, survey.main_map, dpi=100)
        with_overview = render_static_frame(survey.layout, survey.inset_map, dpi=100)
        assert grid.enabled() and overview.enabled()  # restored afterwards

        grid.setEnabled(False)
        overview.setEnabled(False)
        without_grid = render_static_frame(survey.layout, survey.main_map, dpi=100)
        without_overview = render_static_frame(survey.layout, survey.inset_map, dpi=100)

        assert with_grid.png_bytes == without_grid.png_bytes
        assert with_overview.png_bytes == without_overview.png_bytes


class TestRotatedItem:
    """An item rotated on the page is rasterised in its own local space; the
    cartograph turns the placed frame, so the raster must not turn with it."""

    @staticmethod
    def _image(render):
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtGui import QImage

        return QImage.fromData(QByteArray(render.png_bytes))

    def test_a_rotated_item_renders_the_same_pixels_as_unrotated(self, survey) -> None:
        upright = self._image(
            render_static_frame(survey.layout, survey.main_map, dpi=50)
        )
        survey.main_map.setItemRotation(30)
        before = (survey.main_map.pos(), survey.main_map.itemRotation())
        turned = self._image(
            render_static_frame(survey.layout, survey.main_map, dpi=50)
        )
        assert turned == upright
        assert (survey.main_map.pos(), survey.main_map.itemRotation()) == before

    def test_the_rotated_raster_fills_its_own_rectangle(self, survey) -> None:
        survey.main_map.setItemRotation(45)
        image = self._image(render_static_frame(survey.layout, survey.main_map, dpi=50))
        size = survey.main_map.sizeWithUnits()
        assert image.width() == round(size.width() / 25.4 * 50)
        assert image.height() == round(size.height() / 25.4 * 50)
        opaque = [
            (x, y)
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0
        ]
        xs, ys = zip(*opaque)
        assert (min(xs), min(ys), max(xs), max(ys)) == (
            0,
            0,
            image.width() - 1,
            image.height() - 1,
        )

    def test_a_hidden_map_still_gets_its_raster_and_stays_hidden(self, survey) -> None:
        upright = self._image(
            render_static_frame(survey.layout, survey.main_map, dpi=50)
        )
        survey.main_map.setVisible(False)
        hidden = self._image(
            render_static_frame(survey.layout, survey.main_map, dpi=50)
        )
        assert hidden == upright
        assert not survey.main_map.isVisible()
