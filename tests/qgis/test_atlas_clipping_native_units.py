"""Atlas clipping on the synchronous static-frame path is the same in every
layout unit.

A layout whose native units are not millimetres is drawn from a millimetre
copy of the map item; the copy must clip to the atlas page's feature exactly as
the millimetre layout does. The map-level blend mode keeps these frames on the
synchronous path (`AsyncFrameRender` declines them). The fixture is the clip
counts atlas: page one a triangle, page two a square, a `clipped` layer the
atlas cuts and a `free` layer it may leave alone.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from clip_count_fixtures import (
    CLIPPED_POINTS,
    FREE_POINTS,
    SQUARE,
    TRIANGLE,
    WGS84,
    coverage_layer,
    point_layer,
)

qgis_core = pytest.importorskip("qgis.core")

from nika_onlymap_exporter.exporters.frame_raster import (  # noqa: E402
    AsyncFrameRender,
    render_static_frame,
)

DPI = 100
UNITS = {
    "mm": ("LayoutMillimeters", 1.0),
    "inch": ("LayoutInches", 25.4),
    "cm": ("LayoutCentimeters", 10.0),
    "pt": ("LayoutPoints", 25.4 / 72.0),
}
#: 90 x 60 mm showing x -3..33, y -3..21 (3:2, as the frame).
EXTENT = (-3.0, -3.0, 33.0, 21.0)
FRAME_MM = (90.0, 60.0)
CLIPPED_RGB = (255, 0, 0)
FREE_RGB = (0, 0, 255)


def _build(project, unit_name: str, *, restrict: bool = True, clipping: bool = True):
    from qgis.core import (
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsRectangle,
    )
    from qgis.PyQt.QtGui import QPainter

    clipped = point_layer("clipped", CLIPPED_POINTS, "#ff0000")
    free = point_layer("free", FREE_POINTS, "#0000ff")
    coverage = coverage_layer(TRIANGLE, SQUARE)
    project.addMapLayers([clipped, free, coverage])
    layout = QgsPrintLayout(project)
    layout.setUnits(getattr(qgis_core.QgsUnitTypes, UNITS[unit_name][0]))
    layout.initializeDefaults()
    layout.setName(f"atlas-{unit_name}")
    unit = getattr(qgis_core.QgsUnitTypes, UNITS[unit_name][0])
    mm = UNITS[unit_name][1]
    item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptResize(QgsLayoutSize(FRAME_MM[0] / mm, FRAME_MM[1] / mm, unit))
    item.attemptMove(QgsLayoutPoint(20 / mm, 20 / mm, unit))
    item.setCrs(WGS84)
    item.setLayers([clipped, free])
    item.setKeepLayerSet(True)
    item.setExtent(QgsRectangle(*EXTENT))
    # A map-level blend mode keeps the frame on the synchronous path.
    item.setBlendMode(QPainter.CompositionMode.CompositionMode_Multiply)
    settings = item.atlasClippingSettings()
    settings.setEnabled(clipping)
    settings.setRestrictToLayers(restrict)
    settings.setLayersToClip([clipped])
    atlas = layout.atlas()
    atlas.setCoverageLayer(coverage)
    atlas.setEnabled(True)
    atlas.setSortFeatures(True)
    atlas.setSortExpression("name")
    assert not AsyncFrameRender.supports(layout, item)
    return layout, item


def _page(layout, index: int) -> None:
    atlas = layout.atlas()
    assert atlas.beginRender()
    assert atlas.seekTo(index)


def _render(layout, item):
    from qgis.PyQt.QtGui import QImage

    render = render_static_frame(layout, item, dpi=DPI)
    image = QImage.fromData(render.png_bytes)
    assert not image.isNull()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _has_marker(image, lon: float, lat: float, rgb) -> bool:
    """Whether a marker of `rgb` is drawn at the map position (`lon`, `lat`)."""
    x = round((lon - EXTENT[0]) / (EXTENT[2] - EXTENT[0]) * image.width())
    y = round((EXTENT[3] - lat) / (EXTENT[3] - EXTENT[1]) * image.height())
    pixel = image.pixelColor(x, y)
    return pixel.alpha() > 200 and all(
        abs(a - b) <= 60
        for a, b in zip((pixel.red(), pixel.green(), pixel.blue()), rgb)
    )


def _pictures_match(a, b) -> None:
    assert a.size() == b.size()
    import numpy as np

    def array(image):
        ptr = image.constBits()
        ptr.setsize(image.sizeInBytes())
        return np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), -1, 4)

    different = np.abs(array(a).astype(int) - array(b).astype(int)).max(axis=2) > 24
    assert different.mean() < 0.002, f"{different.mean():.4f} of the pixels differ"


@pytest.mark.parametrize("unit_name", ["mm", "inch", "cm", "pt"])
class TestAtlasClippingOnTheSynchronousPath:
    def test_the_marker_outside_the_page_feature_is_not_drawn(
        self, qgis_app, project, unit_name
    ) -> None:
        layout, item = _build(project, unit_name)
        _page(layout, 0)  # the triangle
        image = _render(layout, item)
        assert _has_marker(image, 2, 2, CLIPPED_RGB)
        assert not _has_marker(image, 8, 8, CLIPPED_RGB)
        assert not _has_marker(image, 22, 2, CLIPPED_RGB)

    def test_a_layer_left_out_of_the_clip_is_drawn_whole(
        self, qgis_app, project, unit_name
    ) -> None:
        layout, item = _build(project, unit_name)
        _page(layout, 0)
        image = _render(layout, item)
        assert _has_marker(image, 1, 6, FREE_RGB)
        assert _has_marker(image, 6, 6, FREE_RGB)  # outside the triangle, not clipped

    def test_without_restricting_to_layers_every_layer_is_clipped(
        self, qgis_app, project, unit_name
    ) -> None:
        layout, item = _build(project, unit_name, restrict=False)
        _page(layout, 0)
        image = _render(layout, item)
        assert _has_marker(image, 1, 6, FREE_RGB)
        assert not _has_marker(image, 6, 6, FREE_RGB)

    def test_the_second_page_clips_to_its_own_feature(
        self, qgis_app, project, unit_name
    ) -> None:
        layout, item = _build(project, unit_name)
        _page(layout, 1)  # the square
        image = _render(layout, item)
        assert _has_marker(image, 22, 2, CLIPPED_RGB)
        assert not _has_marker(image, 2, 2, CLIPPED_RGB)
        assert not _has_marker(image, 8, 8, CLIPPED_RGB)

    def test_clipping_off_draws_everything(self, qgis_app, project, unit_name) -> None:
        layout, item = _build(project, unit_name, clipping=False)
        _page(layout, 0)
        image = _render(layout, item)
        assert _has_marker(image, 8, 8, CLIPPED_RGB)

    def test_the_picture_matches_the_millimetre_layout(
        self, qgis_app, project, unit_name
    ) -> None:
        pictures = []
        for name in ("mm", unit_name):
            layout, item = _build(project, name)
            _page(layout, 0)
            pictures.append(_render(layout, item))
        _pictures_match(*pictures)

    @pytest.mark.parametrize("clip_type", ["ClipPainterOnly", "ClipToIntersection"])
    def test_every_feature_clipping_type_matches_the_millimetre_layout(
        self, qgis_app, project, unit_name, clip_type
    ) -> None:
        pictures = []
        for name in ("mm", unit_name):
            layout, item = _build(project, name)
            settings = item.atlasClippingSettings()
            settings.setFeatureClippingType(
                getattr(qgis_core.QgsMapClippingRegion.FeatureClippingType, clip_type)
            )
            settings.setForceLabelsInsideFeature(True)
            _page(layout, 0)
            pictures.append(_render(layout, item))
        assert not _has_marker(pictures[1], 8, 8, CLIPPED_RGB)
        _pictures_match(*pictures)

    def test_rendering_leaves_the_atlas_and_layout_as_they_were(
        self, qgis_app, project, unit_name
    ) -> None:
        layout, item = _build(project, unit_name)
        _page(layout, 1)
        before = (
            layout.atlas().currentFeatureNumber(),
            int(layout.units()),
            item.extent().toString(6),
            item.atlasClippingSettings().enabled(),
        )
        _render(layout, item)
        assert before == (
            layout.atlas().currentFeatureNumber(),
            int(layout.units()),
            item.extent().toString(6),
            item.atlasClippingSettings().enabled(),
        )
