"""The one entry point for turning a `QgsPrintLayout` into a cartograph.

Both the dialog and the `exportlayout` Processing algorithm call this single
function, the same way `writers/onlymap_writer.py`'s `write`/`write_preview`
are the one path for the web-map export (this module's docstring reason is
the same one `writers/onlymap_writer.py` gives: a second implementation is
how a fix gets made in one place and not the other).

It is the glue the architecture diagram in `specs/cartograph/
qgis2carto-issues-1-5.md` section 1 draws between three things that each stay
pure on their own:

- `core/layout_reader.py` decides *what* every layout item is and *where* it
  goes, but a map item comes back as a `MapFrameReading`, not a finished
  `FrameSpec` -- it still needs either a raster or a rendered `<om-map>`.
- `exporters/frame_raster.py` renders a static frame's raster and corners.
- `writers/onlymap_writer.py` (qgis2webmap's own writer, reused rather than
  reimplemented) renders a live frame's `<om-map>` markup from the project's
  layers, restricted to the map item's own layer set via
  `layersToRender()`.

This module is where those three results get assembled into one
`CartographSpec` and handed to `writers/cartograph_writer.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
)

from ..exporters.frame_raster import render_static_frame
from ..packaging.runtime_manager import RuntimeBundle
from ..writers.cartograph_writer import CartographRuntimeBundle, write_cartograph_html
from ..writers.onlymap_writer import OnlyMapWriter
from .cartograph_ir import CartographItem, CartographSpec, FrameSpec
from .export_ir import Extent
from .extent_math import extent_from_longitudes_latitudes
from .fidelity_report import FidelityReportBuilder
from .layout_reader import FrameMode, MapFrameReading, read_layout
from .project_reader import read_project
from .zoom_range import EQUATOR_METRES, WORLD_PIXELS_AT_ZOOM_0

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: A CSS pixel, same definition `core/zoom_range.py` uses, so a live frame's
#: camera is measured in the runtime's own units.
_CSS_PIXELS_PER_MM = 96.0 / 25.4

_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def export_layout_to_cartograph(
    layout: QgsPrintLayout,
    project: QgsProject,
    runtime: RuntimeBundle,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
    static_dpi: int = 200,
    title_override: str | None = None,
) -> str:
    """Read `layout` and render it to a standalone cartograph HTML document.

    Raises `ValueError` up front when the runtime bundle has no cartograph
    dist files at all -- that is a packaging problem, not a fidelity one, so
    it is not softened into a `FidelityItem` the way a missing layer would be.
    """
    if runtime.cartograph_javascript is None or runtime.cartograph_css is None:
        raise ValueError(
            "the OnlyMap runtime bundle has no cartograph dist files "
            "(cartograph_javascript/cartograph_css); re-fetch or re-lock the "
            "runtime before exporting a cartograph"
        )

    reading = read_layout(
        layout, report, frame_modes=frame_modes, static_dpi=static_dpi
    )

    writer = OnlyMapWriter()
    items: list[CartographItem] = list(reading.furniture)
    live_markup: dict[str, str] = {}
    has_live = False

    for frame in reading.frames:
        if frame.mode == "static":
            items.append(_build_static_frame(layout, frame, static_dpi))
        else:
            has_live = True
            frame_spec, markup = _build_live_frame(frame, project, writer, report)
            items.append(frame_spec)
            live_markup[frame.frame_id] = markup

    items.sort(key=lambda item: item.z if item.z is not None else 0)

    cartograph_id = _cartograph_id(layout)
    title = title_override if title_override is not None else (project.title() or "")

    spec = CartographSpec(
        cartograph_id=cartograph_id,
        title=title,
        width=reading.page_width,
        height=reading.page_height,
        orientation=reading.orientation,
        items=tuple(items),
    )

    bundle = CartographRuntimeBundle(
        cartograph_js=runtime.cartograph_javascript,
        cartograph_css=runtime.cartograph_css.decode("utf-8"),
        core_js=runtime.javascript if has_live else None,
        core_css=runtime.css.decode("utf-8") if has_live else None,
    )
    return write_cartograph_html(spec, bundle, live_markup)


def _cartograph_id(layout: QgsPrintLayout) -> str:
    name = layout.name() or "cartograph"
    sanitized = _ID_SANITIZE_RE.sub("-", name).strip("-")
    return sanitized or "cartograph"


def _build_static_frame(
    layout: QgsPrintLayout, frame: MapFrameReading, static_dpi: int
) -> FrameSpec:
    render = render_static_frame(layout, frame.map_item, dpi=static_dpi)
    return FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="static",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        crs=render.crs_authid,
        crs_def=render.crs_def,
        corners=render.corners,
        image_bytes=render.png_bytes,
        dpi=float(static_dpi),
        overview_of=frame.overview_of,
    )


def _build_live_frame(
    frame: MapFrameReading,
    project: QgsProject,
    writer: OnlyMapWriter,
    report: FidelityReportBuilder,
) -> tuple[FrameSpec, str]:
    map_item = frame.map_item
    layer_order = tuple(layer.id() for layer in map_item.layersToRender())
    extent = _frame_extent_wgs84(map_item, project)

    export_project = read_project(
        project,
        report,
        layer_order=layer_order,
        canvas_extent=extent,
    )
    verdict = writer.license_policy.evaluate(export_project)
    markup = writer.render_map_element(export_project, verdict)

    center, zoom = _camera_for_extent(extent, frame.box.w)
    frame_spec = FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="live",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        center=center,
        zoom=zoom,
        bearing=map_item.mapRotation(),
        overview_of=frame.overview_of,
    )
    return frame_spec, markup


def _frame_extent_wgs84(map_item, project: QgsProject) -> Extent:
    """The map item's axis-aligned extent, reprojected to WGS84.

    Reuses `core/extent_math.py`'s antimeridian-aware union rather than a
    naive min/max over the transformed corners, for the same reason every
    other reader in this plugin does: a naive box over a dateline-crossing
    view opens at the whole planet's width.
    """
    crs = map_item.crs()
    transform = QgsCoordinateTransform(crs, WGS84, project)
    rect = map_item.extent()
    corners = [
        (rect.xMinimum(), rect.yMinimum()),
        (rect.xMinimum(), rect.yMaximum()),
        (rect.xMaximum(), rect.yMinimum()),
        (rect.xMaximum(), rect.yMaximum()),
    ]
    longitudes = []
    latitudes = []
    for x, y in corners:
        point = transform.transform(QgsPointXY(x, y))
        longitudes.append(point.x())
        latitudes.append(point.y())

    extent = extent_from_longitudes_latitudes(longitudes, latitudes)
    if extent is None:  # pragma: no cover - defensive; four corners are never empty
        raise ValueError(f"could not compute an extent for map item {map_item.id()!r}")
    return extent


def _camera_for_extent(
    extent: Extent, frame_width_mm: float
) -> tuple[tuple[float, float], float]:
    """A live frame's `center`/`zoom`, from its extent and its box width.

    The zoom maths mirrors `core/zoom_range.py`'s `web_zoom_for_scale` (same
    deck.gl Web Mercator world, 512 CSS pixels wide at zoom 0) run in
    reverse: there, a ground scale is turned into a zoom; here, a ground
    distance (the extent's width) and a pixel distance (the frame's own
    width) are turned into the zoom that makes one cover the other.
    """
    center_lat = (extent.south + extent.north) / 2.0
    if extent.crosses_antimeridian:
        center_lon = extent.east + ((extent.west - extent.east) % 360.0) / 2.0
        if center_lon > 180.0:
            center_lon -= 360.0
        width_degrees = (extent.west - extent.east) % 360.0
    else:
        center_lon = (extent.west + extent.east) / 2.0
        width_degrees = extent.east - extent.west

    frame_width_px = frame_width_mm * _CSS_PIXELS_PER_MM
    if width_degrees <= 0 or frame_width_px <= 0:
        return (center_lon, center_lat), 0.0

    width_metres = (
        width_degrees * (EQUATOR_METRES / 360.0) * math.cos(math.radians(center_lat))
    )
    if width_metres <= 0:
        return (center_lon, center_lat), 0.0

    metres_per_pixel = width_metres / frame_width_px
    metres_per_pixel_at_zoom_0 = (
        EQUATOR_METRES / WORLD_PIXELS_AT_ZOOM_0 * math.cos(math.radians(center_lat))
    )
    zoom = math.log2(metres_per_pixel_at_zoom_0 / metres_per_pixel)
    return (center_lon, center_lat), max(0.0, zoom)
