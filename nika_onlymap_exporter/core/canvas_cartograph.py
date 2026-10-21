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
carries the usual default (`core/layout_reader.default_mode_for_map`) for the
caller to pass to `prepare_cartograph`, and the user can still flip it in
Map frames.

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
    QgsLayerTreeLayer,
    QgsLayoutExporter,
    QgsLayoutItemLegend,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMapLayer,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QImage

from .cartograph_import import RoundTripIssue
from .cartograph_ir import CartographSpec, FrameSpec, LegendSpec
from .layer_reader import read_attribution
from .layout_builder import BuiltLayout, build_layout
from .layout_reader import FrameMode, default_mode_for_map
from .layout_templates import (
    MAIN_FRAME_ID,
    FrameSlot,
    TemplateInput,
    TemplatePlan,
    plan_template,
    template_spec,
)

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: The main frame's id in the spec and the map item's id in the layout.
FRAME_ID = MAIN_FRAME_ID
#: What the layout is called when the project has no title.
_UNTITLED = "map"
_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
#: The smallest a legend's text, symbols and spacing are drawn to fit the
#: box its template planned, as a share of QGIS's default sizes (12 pt
#: labels -> 3.6 pt): a small legend still says everything, a clipped one
#: does not.
_LEGEND_MIN_SCALE = 0.3
_LEGEND_SCALE_STEP = 0.05
#: Only a bound for the search; the box's width stops it long before.
_LEGEND_MAX_COLUMNS = 12
_LEGEND_TEXT_STYLES = (
    QgsLegendStyle.Title,
    QgsLegendStyle.Group,
    QgsLegendStyle.Subgroup,
    QgsLegendStyle.SymbolLabel,
)
_LEGEND_SIDES = (
    QgsLegendStyle.Top,
    QgsLegendStyle.Bottom,
    QgsLegendStyle.Left,
    QgsLegendStyle.Right,
)


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
    plan: TemplatePlan
    issues: tuple[RoundTripIssue, ...]
    frame_modes: Mapping[str, FrameMode]


def cartograph_from_canvas(
    canvas: QgsMapCanvas,
    project: QgsProject,
    *,
    page_size: str = "A4",
    orientation: str = "auto",
    template_id: str = "classic",
    placeholders: Mapping[str, str] | None = None,
) -> CanvasCartograph:
    """Plan and build the one-click cartograph for what `canvas` shows now:
    its extent, rotation, destination CRS and layers (in drawing order), in
    the layout template `template_id` (`core/layout_templates.py`)."""
    settings = canvas.mapSettings()
    return cartograph_from_view(
        settings.extent(),
        settings.destinationCrs(),
        canvas.layers(),
        project,
        rotation=settings.rotation(),
        page_size=page_size,
        orientation=orientation,
        template_id=template_id,
        placeholders=placeholders,
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
    template_id: str = "classic",
    placeholders: Mapping[str, str] | None = None,
) -> CanvasCartograph:
    """`cartograph_from_canvas` for a view given as plain values -- the same
    thing without a widget, for callers (and tests) that have no canvas.

    `extent` is the *unrotated* extent in `crs` (`QgsMapSettings.extent()`),
    `rotation` the canvas rotation in degrees, `layers` top layer first.
    Raises `ValueError` for an empty or non-finite extent, an invalid CRS or
    an unknown template, page size or orientation.
    """
    plan, spec, built = _build_from_view(
        extent,
        crs,
        layers,
        project,
        rotation=rotation,
        page_size=page_size,
        orientation=orientation,
        template_id=template_id,
        placeholders=placeholders,
        add_to_project=True,
    )
    map_item = built.items_by_id[FRAME_ID]
    mode: FrameMode = default_mode_for_map(map_item)
    modes = {map_item.uuid(): mode}
    for slot in plan.frames[1:]:
        # A locator inset is context, not the subject: a static picture
        # keeps it exactly as QGIS draws it at no cost to the page.
        modes[built.items_by_id[slot.id].uuid()] = "static"
    return CanvasCartograph(
        spec=spec,
        layout=built.layout,
        plan=plan,
        issues=built.issues,
        frame_modes=modes,
    )


def template_preview(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    template_id: str,
    width_px: int,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
    placeholders: Mapping[str, str] | None = None,
) -> QImage:
    """The page `template_id` would make of this view, drawn by QGIS
    `width_px` wide -- the template picker's thumbnail. The layout is built
    outside the project and thrown away; nothing is added to it."""
    plan, _spec, built = _build_from_view(
        extent,
        crs,
        layers,
        project,
        rotation=rotation,
        page_size=page_size,
        orientation=orientation,
        template_id=template_id,
        placeholders=placeholders,
        add_to_project=False,
    )
    height_px = max(1, round(width_px * plan.page_height / plan.page_width))
    image = QgsLayoutExporter(built.layout).renderPageToImage(
        0, QSize(width_px, height_px)
    )
    return image


def _build_from_view(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float,
    page_size: str,
    orientation: str,
    template_id: str,
    placeholders: Mapping[str, str] | None,
    add_to_project: bool,
) -> tuple[TemplatePlan, CartographSpec, BuiltLayout]:
    if not crs.isValid():
        raise ValueError("the canvas has no valid CRS")
    if extent.isNull() or not extent.isFinite() or extent.isEmpty():
        raise ValueError("the canvas extent is empty")

    title = project.title().strip()
    plan = plan_template(
        template_id,
        TemplateInput(
            extent_width=extent.width(),
            extent_height=extent.height(),
            crs_is_geographic=crs.isGeographic(),
            crs_authid=crs.authid(),
            crs_def=crs.toProj() or None,
            page_size=page_size,
            orientation=orientation,
            title=title,
            layer_names=tuple(layer.name() for layer in layers),
            credits=tuple(_credits(layers)),
            legend_labels=tuple(_legend_labels(layer) for layer in layers),
            notes=project.metadata().abstract(),
            placeholders=dict(placeholders or {}),
        ),
    )
    frames = {
        slot.id: _frame(slot, extent, rotation, crs, project) for slot in plan.frames
    }
    spec = template_spec(
        plan,
        frames,
        cartograph_id=_ID_SANITIZE_RE.sub("-", title).strip("-") or "cartograph",
    )
    built = build_layout(
        spec,
        project,
        name=f"carto: {title or _UNTITLED}",
        frame_layers={slot.id: list(layers) for slot in plan.frames},
        add_to_project=add_to_project,
    )
    for item in plan.items:
        if isinstance(item, LegendSpec):
            legend = built.items_by_id[item.id]
            if isinstance(legend, QgsLayoutItemLegend):
                _fit_legend(legend)
    return plan, spec, built


def _legend_labels(layer: QgsMapLayer) -> tuple[str, ...]:
    """The labels of the entries `layer` adds to a legend under its name --
    one per class of a categorized or graduated layer."""
    layer_legend = layer.legend()
    if layer_legend is None:
        return ()
    node = QgsLayerTreeLayer(layer)
    return tuple(
        str(legend_node.data(Qt.ItemDataRole.DisplayRole) or "")
        for legend_node in layer_legend.createLayerTreeModelLegendNodes(node)
    )


def _fit_legend(legend: QgsLayoutItemLegend) -> None:
    """Make every row of `legend` fit the box its template planned, which
    the builder keeps fixed: QGIS clips a legend at its frame, so a layer
    with many classes would lose rows off the bottom. More columns first,
    then smaller text, symbols and spacing -- down to `_LEGEND_MIN_SCALE` --
    never a bigger box. A legend that already fits is left as it is."""
    box = legend.sizeWithUnits()
    context = QgsLayoutUtils.createRenderContextForLayout(legend.layout(), None)

    def measure() -> tuple[bool, bool]:
        """Whether the rows fit the box across, and down."""
        size = QgsLegendRenderer(legend.model(), legend.legendSettings()).minimumSize(
            context
        )
        return size.width() <= box.width(), size.height() <= box.height()

    if all(measure()):
        return
    # A class list may run on into the next column, or columns cannot help
    # a legend of one categorized layer.
    legend.setSplitLayer(True)
    base_columns = legend.columnCount()
    defaults = _LegendSizes.read(legend)
    steps = round((1.0 - _LEGEND_MIN_SCALE) / _LEGEND_SCALE_STEP)
    for step in range(steps + 1):
        defaults.apply(legend, 1.0 - step * _LEGEND_SCALE_STEP)
        columns = base_columns
        while True:
            legend.setColumnCount(columns)
            across, down = measure()
            if across and down:
                legend.refresh()
                return
            if not across or columns >= _LEGEND_MAX_COLUMNS:
                break
            columns += 1
        # The most columns that still fit across, for the next size down or,
        # at the smallest, to leave as little as possible past the edge.
        legend.setColumnCount(max(base_columns, columns - (0 if across else 1)))
    legend.refresh()


@dataclass(frozen=True)
class _LegendSizes:
    """A legend's own text sizes, margins, symbol size and spacing, to draw
    it at a share of them."""

    text: tuple[float, ...]
    margins: tuple[tuple[float, ...], ...]
    symbol: tuple[float, float]
    spacing: tuple[float, float]

    @classmethod
    def read(cls, legend: QgsLayoutItemLegend) -> _LegendSizes:
        styles = [legend.rstyle(style) for style in _LEGEND_TEXT_STYLES]
        return cls(
            text=tuple(style.textFormat().size() for style in styles),
            margins=tuple(
                tuple(style.margin(side) for side in _LEGEND_SIDES) for style in styles
            ),
            symbol=(legend.symbolWidth(), legend.symbolHeight()),
            spacing=(legend.boxSpace(), legend.columnSpace()),
        )

    def apply(self, legend: QgsLayoutItemLegend, scale: float) -> None:
        for style_id, size, margins in zip(
            _LEGEND_TEXT_STYLES, self.text, self.margins
        ):
            style = legend.rstyle(style_id)
            text_format = style.textFormat()
            text_format.setSize(size * scale)
            style.setTextFormat(text_format)
            for side, margin in zip(_LEGEND_SIDES, margins):
                style.setMargin(side, margin * scale)
        legend.setSymbolWidth(self.symbol[0] * scale)
        legend.setSymbolHeight(self.symbol[1] * scale)
        legend.setBoxSpace(self.spacing[0] * scale)
        legend.setColumnSpace(self.spacing[1] * scale)


def _frame(
    slot: FrameSlot,
    extent: QgsRectangle,
    rotation: float,
    crs: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> FrameSpec:
    """The frame for `slot`: the canvas view (a locator's zoomed out about
    the same centre), as four corners in the canvas CRS."""
    view = QgsRectangle(extent)
    if slot.zoom_out != 1.0:
        view.scale(slot.zoom_out)
    return FrameSpec(
        id=slot.id,
        box=slot.box,
        mode="static",
        crs=crs.authid() or None,
        crs_def=crs.toProj() or None,
        corners=_corners_wgs84(view, rotation, crs, project),
        # A placeholder, never written: the frame only carries the view here,
        # and the export renders its real raster from the layout (see the
        # `CanvasCartograph.spec` note).
        image_bytes=b"",
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
