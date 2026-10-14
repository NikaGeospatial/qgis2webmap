"""One-click mode, the PyQGIS half: the map canvas -> a cartograph spec AND a
real `QgsPrintLayout` in the project.

Issue #1 section 4, step 5 is the differentiator this module exists for: the
canvas is not exported straight to HTML, it becomes a print layout named
"carto: <project title>" that the user can keep editing in QGIS -- and the
cartograph then follows that layout like any other (the dialog selects it as
the source). So the plugin is not a dead end in either direction.

The arithmetic lives in `core/canvas_layout.py` (pure, unit-tested); this
module only reads the canvas, hands the numbers over, and builds the result
through `core/layout_builder.py`, the same builder a cartograph imported from
a file goes through.

**The frame keeps the canvas's own CRS and view.** It is described by its
four corners in that CRS -- exactly how a static frame is georeferenced -- so
the builder puts the map item back on the canvas extent and rotation
bit-for-bit, whatever the CRS. Whether the cartograph then draws it live or
as a raster is an export-time choice, so `CanvasCartograph.frame_modes`
carries the usual CRS default (`core/settings.default_frame_mode`) for the
caller to pass to `prepare_cartograph`, and the user can still flip it in
the Frames tab.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayer,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvas

from .canvas_layout import (
    CanvasLayoutInput,
    CanvasLayoutPlan,
    canvas_spec,
    plan_canvas_layout,
)
from .cartograph_import import RoundTripIssue
from .cartograph_ir import CartographSpec, FrameSpec
from .layer_reader import read_attribution
from .layout_builder import build_layout
from .layout_reader import FrameMode
from .settings import default_frame_mode

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: The frame's id in the spec and the map item's id in the layout.
FRAME_ID = "main"
#: What the layout is called when the project has no title.
_UNTITLED = "map"
_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class CanvasCartograph:
    """The one-click result.

    - `layout` is already in the project's layout manager, uniquely named
      "carto: <title>" -- select it as the cartograph's source.
    - `spec` is what the layout was built from. Its frame is a georeference
      only (corners in the canvas CRS, no raster yet): export from `layout`
      with `prepare_cartograph(..., frame_modes=frame_modes)`, never by
      writing `spec` directly.
    - `frame_modes` is keyed by map item uuid, as `prepare_cartograph` takes.
    """

    spec: CartographSpec
    layout: QgsPrintLayout
    plan: CanvasLayoutPlan
    issues: tuple[RoundTripIssue, ...]
    frame_modes: Mapping[str, FrameMode]


def cartograph_from_canvas(
    canvas: QgsMapCanvas,
    project: QgsProject,
    *,
    page_size: str = "A4",
    orientation: str = "auto",
) -> CanvasCartograph:
    """Plan and build the one-click cartograph for what `canvas` shows now:
    its extent, rotation, destination CRS and layers (in drawing order)."""
    settings = canvas.mapSettings()
    return cartograph_from_view(
        settings.extent(),
        settings.destinationCrs(),
        canvas.layers(),
        project,
        rotation=settings.rotation(),
        page_size=page_size,
        orientation=orientation,
    )


def cartograph_from_view(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
) -> CanvasCartograph:
    """`cartograph_from_canvas` for a view given as plain values -- the same
    thing without a widget, for callers (and tests) that have no canvas.

    `extent` is the *unrotated* extent in `crs` (`QgsMapSettings.extent()`),
    `rotation` the canvas rotation in degrees, `layers` top layer first.
    Raises `ValueError` for an empty or non-finite extent or an invalid CRS.
    """
    if not crs.isValid():
        raise ValueError("the canvas has no valid CRS")
    if extent.isNull() or not extent.isFinite() or extent.isEmpty():
        raise ValueError("the canvas extent is empty")

    title = project.title().strip()
    plan = plan_canvas_layout(
        CanvasLayoutInput(
            extent_width=extent.width(),
            extent_height=extent.height(),
            crs_is_geographic=crs.isGeographic(),
            page_size=page_size,
            orientation=orientation,
            title=title,
            layer_names=tuple(layer.name() for layer in layers),
            credits=tuple(_credits(layers)),
        )
    )
    frame = FrameSpec(
        id=FRAME_ID,
        box=plan.frame,
        mode="static",
        crs=crs.authid() or None,
        crs_def=crs.toProj() or None,
        corners=_corners_wgs84(extent, rotation, crs, project),
        # A placeholder, never written: the frame only carries the view here,
        # and the export renders its real raster from the layout (see the
        # `CanvasCartograph.spec` note).
        image_bytes=b"",
    )
    spec = canvas_spec(
        plan,
        frame,
        cartograph_id=_ID_SANITIZE_RE.sub("-", title).strip("-") or "cartograph",
    )
    built = build_layout(
        spec,
        project,
        name=f"carto: {title or _UNTITLED}",
        frame_layers={FRAME_ID: list(layers)},
        add_to_project=True,
    )
    map_item = built.items_by_id[FRAME_ID]
    mode: FrameMode = "live" if default_frame_mode(crs.authid()) == "live" else "static"
    return CanvasCartograph(
        spec=spec,
        layout=built.layout,
        plan=plan,
        issues=built.issues,
        frame_modes={map_item.uuid(): mode},
    )


def _credits(layers: Sequence[QgsMapLayer]) -> list[str]:
    credits = []
    for layer in layers:
        credit = read_attribution(layer)
        if credit:
            credits.append(credit)
    return credits


def _corners_wgs84(
    extent: QgsRectangle,
    rotation: float,
    crs: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> tuple[
    tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]
]:
    """The view's tl/tr/br/bl corners in lon/lat, in the order and rotation
    sense `exporters/frame_raster.py` reads them from `visibleExtentPolygon()`
    (the top edge turns counter-clockwise in map units by `rotation`)."""
    cx, cy = extent.center().x(), extent.center().y()
    half_w, half_h = extent.width() / 2, extent.height() / 2
    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    transform = QgsCoordinateTransform(crs, WGS84, project)
    corners = []
    for dx, dy in (
        (-half_w, half_h),
        (half_w, half_h),
        (half_w, -half_h),
        (-half_w, -half_h),
    ):
        x = cx + dx * cos_a - dy * sin_a
        y = cy + dx * sin_a + dy * cos_a
        point = transform.transform(QgsPointXY(x, y))
        corners.append((point.x(), point.y()))
    return corners[0], corners[1], corners[2], corners[3]
