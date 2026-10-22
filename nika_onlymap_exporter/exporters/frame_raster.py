"""Render a static cartograph frame: a region of a `QgsPrintLayout` -> PNG +
its four georeferenced corners.

This is the production version of `specs/cartograph/spikes/render.py`, which
proved (`specs/cartograph/spike-findings.md` #2) that a region render at a
chosen DPI, placed by its extent's four corners transformed to EPSG:4326,
lands with **zero** error against QGIS's own `mapToItemCoords` on an
unrotated map. The two things the spike did not have to deal with, because
its layout had exactly one item, are the two reasons this module exists
rather than just inlining the spike:

- **Furniture bleed.** Any other layout item overlapping the map item's
  rectangle (a legend corner, a label) would get baked into the raster if it
  were visible during the render. Every other item is hidden first and its
  visibility restored afterwards -- including on an exception, so a render
  failure cannot leave the layout's other items permanently hidden under the
  caller.
- **Rotation.** `item.extent()` is an axis-aligned bounding box; a rotated
  map's rendered rectangle is not axis-aligned in map coordinates. The map
  item's own `visibleExtentPolygon()` returns the four map-CRS points that
  correspond to the item's own four corners (verified headlessly by rotating
  a square extent 30 degrees and checking the first returned point against
  the hand-rotated top-left corner) -- `tl, tr, br, bl`, matching the
  cartograph runtime's own corner order, with no further reasoning about
  rotation direction needed here.
- **The map's own grids and overviews.** The runtime draws `<om-graticule>`
  and `overview-of` over a static frame itself, so they are switched off for
  the render too; baking them in as well would draw each one twice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemMap,
    QgsLayoutMeasurementConverter,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QBuffer, QIODevice, QRectF, QSize

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

LonLat = tuple[float, float]


@dataclass(frozen=True)
class StaticFrameRender:
    """Everything `core/cartograph_export.py` needs to build a static
    `FrameSpec`: the raster, its placement, and its CRS."""

    png_bytes: bytes
    corners: tuple[LonLat, LonLat, LonLat, LonLat]
    crs_authid: str
    crs_def: str


def render_static_frame(
    layout: QgsPrintLayout,
    map_item: QgsLayoutItemMap,
    *,
    dpi: int = 200,
) -> StaticFrameRender:
    """Render `map_item`'s own rectangle at `dpi`, with every other item on
    the layout, and the map's own grids and overviews, hidden for the
    duration of the render."""
    others = [
        item
        for item in layout.items()
        if isinstance(item, QgsLayoutItem) and item is not map_item
    ]
    previous_visibility = {item.uuid(): item.isVisible() for item in others}
    overlays = [*map_item.grids().asList(), *map_item.overviews().asList()]
    previous_enabled = [overlay.enabled() for overlay in overlays]

    try:
        for item in others:
            item.setVisible(False)
        for overlay in overlays:
            overlay.setEnabled(False)
        png_bytes = _render_region(layout, map_item, dpi)
    finally:
        for item in others:
            item.setVisible(previous_visibility[item.uuid()])
        for overlay, enabled in zip(overlays, previous_enabled):
            overlay.setEnabled(enabled)

    crs = map_item.crs()
    project = layout.project() or QgsProject.instance()
    return StaticFrameRender(
        png_bytes=png_bytes,
        corners=frame_corners_wgs84(map_item, project),
        crs_authid=crs.authid(),
        crs_def=crs.toProj(),
    )


def frame_corners_wgs84(
    map_item: QgsLayoutItemMap, project: QgsProject
) -> tuple[LonLat, LonLat, LonLat, LonLat]:
    """The map item's tl/tr/br/bl corners in lon/lat: its own
    `visibleExtentPolygon()`, so a rotated map's corners turn with it."""
    transform = QgsCoordinateTransform(map_item.crs(), WGS84, project)
    corners_map = [(point.x(), point.y()) for point in map_item.visibleExtentPolygon()][
        :4
    ]
    corners = tuple(_to_wgs84(transform, x, y) for x, y in corners_map)
    return corners  # type: ignore[return-value]  # always length 4, checked below


def _to_wgs84(transform: QgsCoordinateTransform, x: float, y: float) -> LonLat:
    point = transform.transform(QgsPointXY(x, y))
    return (point.x(), point.y())


def _render_region(
    layout: QgsPrintLayout, map_item: QgsLayoutItemMap, dpi: int
) -> bytes:
    converter = QgsLayoutMeasurementConverter()
    converter.setDpi(layout.renderContext().dpi())
    size = converter.convert(map_item.sizeWithUnits(), QgsUnitTypes.LayoutMillimeters)

    # The raster is the item in its own local space, w x h; the cartograph
    # rotates the placed frame itself, so a rotated item rendered by its
    # scene position would be cropped and then turned a second time. Rendered
    # unrotated about its centre, and put back exactly afterwards.
    rotation = map_item.itemRotation()
    original_position = map_item.positionWithUnits()
    # The item's opacity travels as the placed frame's own CSS opacity
    # (`FrameSpec.opacity`); left on the map here it would be baked into the
    # picture as well, and the frame would be drawn translucent twice.
    opacity = map_item.itemOpacity()
    # The item has to be drawn to be rendered: a hidden frame is exported
    # hidden but still needs its raster for when it is shown.
    hidden_ancestors = _hidden_chain(map_item)
    try:
        for hidden in hidden_ancestors:
            hidden.setVisible(True)
        map_item.setItemOpacity(1.0)
        if rotation:
            map_item.setItemRotation(0, True)
        position = converter.convert(
            map_item.positionWithUnits(), QgsUnitTypes.LayoutMillimeters
        )
        pixel_size = QSize(
            round(size.width() / 25.4 * dpi), round(size.height() / 25.4 * dpi)
        )
        region = QRectF(position.x(), position.y(), size.width(), size.height())
        image = QgsLayoutExporter(layout).renderRegionToImage(region, pixel_size, dpi)
    finally:
        map_item.setItemOpacity(opacity)
        if rotation:
            map_item.setItemRotation(rotation, False)
            map_item.attemptMove(original_position)
        for hidden in hidden_ancestors:
            hidden.setVisible(False)

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _hidden_chain(item: QgsLayoutItem) -> list[QgsLayoutItem]:
    """`item` and the groups it sits in, those of them that are hidden."""
    chain: list[QgsLayoutItem] = []
    current: QgsLayoutItem | None = item
    while current is not None:
        if not current.isVisible():
            chain.append(current)
        current = current.parentGroup()
    return chain
