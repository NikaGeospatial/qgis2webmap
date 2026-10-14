"""`QgsPrintLayout` -> the pieces a `CartographSpec` is assembled from.

This is the PyQGIS half of issue #1 (`specs/cartograph/qgis2carto-issues-1-5.md`
section 2, corrected against the runtime per `specs/cartograph/
spike-findings.md`). It walks `layout.items()` and translates each one into a
`core/cartograph_ir.py` dataclass, or into a `MapFrameReading` for a map item --
map items are not finished `FrameSpec`s here because a *static* frame still
needs its PNG rendered (`exporters/frame_raster.py`) and a *live* frame still
needs its `<om-map>` markup (the reused `OnlyMapWriter`), and both of those
belong to `core/cartograph_export.py`, not to this reader. This module only
decides *what* each frame is and *where* it goes.

**Final geometry only.** `setExtent()` resizes a map item to the extent's
aspect ratio (verified headlessly, see the issue's "What I verified
headlessly" note and spike-findings.md #2's "Other behaviour"), so every box
here is read back with `positionWithUnits()`/`sizeWithUnits()`/`extent()`
*after* the layout was built -- never the values a caller asked for.

**Nothing vanishes silently.** Every approximated or unsupported item records
a `FidelityItem` with the layout item's id and a plain-English reason, per
`core/fidelity_report.py`'s non-negotiable.

Two gaps against issue #1's table, both because `core/cartograph_ir.py`'s
dataclasses (frozen before this module existed, and shared with the writer
and its tests) have no attribute for them yet:

- **Group membership** is flattened with no `data-group` marker -- the
  runtime vocabulary mirrored into `cartograph_ir.py` carries no such
  attribute on any item. Each item keeps its own final absolute position
  (verified headlessly: `positionWithUnits()` is already absolute for a
  grouped item, not group-relative), so the *layout* survives; only the
  grouping relationship itself is recorded as approximated.
- **Per-item opacity** has no field on any `CartographItem` dataclass either
  (`ShapeSpec.fill_opacity` is a fill property, not the whole item's). An
  item with `opacity() != 1.0` is exported at full opacity and recorded as
  approximated.

Both are reported as findings, not silently special-cased -- see
`_record_unrepresentable_item_properties`.

Atlas translation (issue #1 section 2's `QgsLayoutAtlas` row) is out of scope
for this module: the plan places it in v0.2 (`plan.md` phase 4), and nothing
here claims to read it. `LayoutReading.atlas` is always `None`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsLayoutItem,
    QgsLayoutItemGroup,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurementConverter,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsSimpleFillSymbolLayer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSizeF, Qt

from .cartograph_ir import (
    Box,
    CartographItem,
    GraticuleSpec,
    ImageSpec,
    LegendRowSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from .expression_tokens import translate_label
from .fidelity_report import FidelityReportBuilder
from .settings import default_frame_mode

FrameMode = Literal["live", "static"]

#: QgsLayoutItemMapGrid.GridStyle -> `<om-graticule kind>`. `Markers` is the
#: runtime's own name too. `FrameAndAnnotationsOnly` (the "zebra" border grid)
#: has no cartograph equivalent, so it is approximated to `cross`.
_GRID_KIND_BY_STYLE = {
    0: "solid",  # Lines
    1: "cross",  # LineCrosses
    2: "markers",  # Markers
    3: "cross",  # FrameAndAnnotationsOnly (approximated)
}

#: QgsLayoutItemScaleBar style *names* (there is no enum -- `setStyle` takes a
#: string) -> `<om-scalebar kind>`, per issue #1 section 2's style map.
# "Numeric" is handled separately: it becomes an `<om-text>`, not a scalebar.
_SCALEBAR_KIND_BY_STYLE = {
    "Single Box": "single",
    "Double Box": "double",
    "Line Ticks Up": "ticks",
    "Line Ticks Down": "ticks",
    "Line Ticks Middle": "ticks",
    "Stepped Line": "single",
    "Hollow": "single",
}

#: Picture suffix -> data-URI media type. Anything unlisted is sent as PNG,
#: which is what QGIS's own raster pictures overwhelmingly are.
_IMAGE_MIME_BY_SUFFIX = {
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass(frozen=True)
class MapFrameReading:
    """A `QgsLayoutItemMap`, with its placement and mode already decided.

    `map_item` is the live PyQGIS object, not a serialised copy: both
    `exporters/frame_raster.py` (static) and `core/project_reader.py` /
    `writers/onlymap_writer.py` (live, via `layersToRender()`) need the real
    object, and copying its state into a pure dataclass here would just be a
    second, driftable representation of the same thing.
    """

    frame_id: str
    map_item: QgsLayoutItemMap
    box: Box
    rotation: float
    mode: FrameMode
    z: int | None
    locked: bool
    hidden: bool
    overview_of: str | None


@dataclass(frozen=True)
class LayoutReading:
    """Everything `core/cartograph_export.py` needs to assemble a
    `CartographSpec`, short of the two things that need a render:
    `frames` still need their raster or their `<om-map>` markup."""

    page_width: float
    page_height: float
    orientation: str
    frames: tuple[MapFrameReading, ...]
    furniture: tuple[CartographItem, ...]
    atlas: None = None


def read_layout(
    layout: QgsPrintLayout,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
) -> LayoutReading:
    """Read `layout`'s first page into a `LayoutReading`.

    `frame_modes` overrides the live/static default per map item, keyed by
    `QgsLayoutItem.uuid()` -- the same id `MapFrameReading.frame_id` is built
    from, so a caller that read a previous `LayoutReading` can key its
    overrides straight off it.
    """
    frame_modes = frame_modes or {}
    converter = QgsLayoutMeasurementConverter()
    converter.setDpi(layout.renderContext().dpi())

    pages = layout.pageCollection()
    page_count = pages.pageCount()
    if page_count == 0:
        raise ValueError("layout has no pages")
    if page_count > 1:
        report.blocked(
            "Page count",
            f"This layout has {page_count} pages. Only page 1 is exported in "
            "v0.1 -- export page 1 only?",
        )
    page = pages.page(0)
    page_size = _box(page, converter)
    orientation = "landscape" if page_size.w >= page_size.h else "portrait"

    items = [
        item
        for item in layout.items()
        if isinstance(item, QgsLayoutItem)
        and not isinstance(item, (QgsLayoutItemPage, QgsLayoutItemGroup))
        and item.page() == 0
    ]
    # A 0 mm item cannot be placed (and `Box` refuses it): leave it out by
    # name rather than let one degenerate item fail the whole export.
    sized = []
    for item in items:
        size = item.sizeWithUnits()
        if size.width() > 0 and size.height() > 0:
            sized.append(item)
        else:
            report.unsupported(
                f"Layout item {_name(item)}",
                f"This {type(item).__name__} has no size on the page, so it was "
                "left out.",
            )
    items = sized
    # Document order = zValue order (issue #1 section 2's last row): sorting
    # here means every downstream list -- frames, furniture, the writer's
    # eventual DOM order -- agrees with it for free.
    items.sort(key=lambda item: item.zValue())

    frames: list[MapFrameReading] = []
    furniture: list[CartographItem] = []
    map_frame_id_by_uuid: dict[str, str] = {}
    # The mode each frame actually got, overrides included: a legend or label
    # on a frame forced static must be static too.
    frame_mode_by_uuid: dict[str, FrameMode] = {}

    map_items = [item for item in items if isinstance(item, QgsLayoutItemMap)]
    reference_map = layout.referenceMap()
    single_map = map_items[0] if len(map_items) == 1 else None

    for item in map_items:
        _record_unrepresentable_item_properties(item, report)
        mode = _frame_mode(item, frame_modes)
        box = _box(item, converter)
        frame_id = f"frame-{item.uuid()}"
        map_frame_id_by_uuid[item.uuid()] = frame_id
        frame_mode_by_uuid[item.uuid()] = mode
        frames.append(
            MapFrameReading(
                frame_id=frame_id,
                map_item=item,
                box=box,
                rotation=item.itemRotation(),
                mode=mode,
                z=round(item.zValue()),
                locked=item.isLocked(),
                hidden=not item.isVisible(),
                overview_of=None,  # resolved in the second pass below
            )
        )
        if mode == "static":
            report.approximated(
                f"Map frame {_name(item)}",
                "Rendered as a static, pixel-exact raster rather than a live map.",
            )
        else:
            report.preserved(f"Map frame {_name(item)}", "Exported as a live frame.")

    # Second pass: now every map item has a frame_id, resolve each inset's
    # `overview_of` and rebuild its MapFrameReading with that filled in.
    for index, mf in enumerate(frames):
        for overview in mf.map_item.overviews().asList():
            linked = overview.linkedMap()
            if overview.enabled() and linked is not None:
                target_id = map_frame_id_by_uuid.get(linked.uuid())
                if target_id is not None:
                    frames[index] = _with_overview_of(mf, target_id)
                    report.preserved(
                        f"Locator inset {_name(mf.map_item)}",
                        f"Locator inset drawing frame {target_id!r}'s footprint.",
                    )
                    break

    for item in items:
        if isinstance(item, QgsLayoutItemMap):
            grids = item.grids().asList() if item.grids() is not None else []
            frame_id = map_frame_id_by_uuid[item.uuid()]
            for grid in grids:
                graticule = _read_grid(item, grid, frame_id, converter, report)
                if graticule is not None:
                    furniture.append(graticule)
            continue
        spec: CartographItem | None
        if isinstance(item, QgsLayoutItemLegend):
            spec = _read_legend(
                item, map_frame_id_by_uuid, frame_mode_by_uuid, converter, report
            )
        elif isinstance(item, QgsLayoutItemScaleBar):
            spec = _read_scalebar(item, map_frame_id_by_uuid, converter, report)
        elif isinstance(item, QgsLayoutItemPicture):
            spec = _read_picture(item, map_frame_id_by_uuid, converter, report)
        elif isinstance(item, QgsLayoutItemLabel):
            spec = _read_label(
                item,
                layout,
                map_frame_id_by_uuid,
                frame_mode_by_uuid,
                reference_map,
                single_map,
                converter,
                report,
            )
        elif isinstance(item, QgsLayoutItemShape):
            spec = _read_shape(item, converter, report)
        else:
            # Everything else named in issue #1's table: polygon/polyline/html/
            # attribute table/elevation profile/3D map/chart -- unsupported,
            # named by class, per the "not vanish silently" rule.
            report.unsupported(
                f"Layout item {_name(item)}",
                f"{type(item).__name__} items are not supported in a cartograph "
                "export and were left out.",
            )
            continue
        if spec is not None:
            furniture.append(spec)

    return LayoutReading(
        page_width=page_size.w,
        page_height=page_size.h,
        orientation=orientation,
        frames=tuple(frames),
        furniture=tuple(furniture),
        atlas=None,
    )


def _name(item: QgsLayoutItem) -> str:
    """An item's name as the layout designer's Items panel shows it, quoted
    the way every other fidelity subject quotes a layer name."""
    return f"'{item.displayName()}'"


def _with_overview_of(mf: MapFrameReading, overview_of: str) -> MapFrameReading:
    return MapFrameReading(
        frame_id=mf.frame_id,
        map_item=mf.map_item,
        box=mf.box,
        rotation=mf.rotation,
        mode=mf.mode,
        z=mf.z,
        locked=mf.locked,
        hidden=mf.hidden,
        overview_of=overview_of,
    )


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def _box(item: QgsLayoutItem, converter: QgsLayoutMeasurementConverter) -> Box:
    """An item's *final* position and size, in millimetres.

    `positionWithUnits()`/`sizeWithUnits()` are already absolute even for a
    grouped item (verified headlessly -- see the module docstring's group
    note), and already reflect any resize `setExtent()` caused, as long as
    the caller read them after building the layout rather than asking for
    the values it originally requested.
    """
    pos = converter.convert(item.positionWithUnits(), QgsUnitTypes.LayoutMillimeters)
    size = converter.convert(item.sizeWithUnits(), QgsUnitTypes.LayoutMillimeters)
    return Box(x=pos.x(), y=pos.y(), w=size.width(), h=size.height())


def _record_unrepresentable_item_properties(
    item: QgsLayoutItem, report: FidelityReportBuilder
) -> None:
    """The two gaps the module docstring explains: groups and opacity.

    Both are properties `core/cartograph_ir.py` has no field for yet, so they
    cannot be approximated into the output -- only reported as missing.
    """
    label = _name(item)
    if item.parentGroup() is not None:
        report.approximated(
            f"Group of {label}",
            "This item is part of a group in QGIS. Groups are flattened on "
            "export -- the item keeps its own position, but the grouping "
            "relationship itself is not preserved.",
        )
    if item.itemOpacity() != 1.0:
        report.approximated(
            f"Opacity of {label}",
            f"QGIS opacity {item.itemOpacity() * 100:.0f}% is not representable "
            "on this item type in v0.1; it was exported fully opaque.",
        )


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def _frame_mode(
    item: QgsLayoutItemMap,
    frame_modes: Mapping[str, FrameMode],
) -> FrameMode:
    """The caller's override, else issue #1 section 3's default: live only in
    one of the runtime's own camera projections, static (pixel-exact, never
    reprojected) for anything else -- the same rule the Frames tab shows."""
    override = frame_modes.get(item.uuid())
    if override is not None:
        return override
    crs = item.crs()
    return (
        "live"
        if default_frame_mode(crs.authid() if crs.isValid() else "") == "live"
        else "static"
    )


# --------------------------------------------------------------------------
# Grid -> GraticuleSpec
# --------------------------------------------------------------------------


def _read_grid(
    map_item: QgsLayoutItemMap,
    grid,
    frame_id: str,
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> GraticuleSpec | None:
    if not grid.enabled():
        return None
    label = f"Grid of {_name(map_item)}"
    style = int(grid.style())
    kind = _GRID_KIND_BY_STYLE.get(style, "solid")
    if style == 3:
        report.approximated(
            label,
            "The frame-style (zebra border) grid has no cartograph "
            "equivalent; drawn as a cross graticule instead.",
        )
    else:
        report.preserved(label, "Graticule preserved.")

    crs = grid.crs() if grid.crs().isValid() else map_item.crs()
    labels = "outside" if grid.annotationEnabled() else "none"
    return GraticuleSpec(
        id=f"graticule-{map_item.uuid()}",
        box=_box(map_item, converter),
        for_=frame_id,
        crs=crs.authid() or None,
        crs_def=crs.toProj() or None,
        interval_x=grid.intervalX() or None,
        interval_y=grid.intervalY() or None,
        kind=kind,
        labels=labels,
        z=round(map_item.zValue()),
    )


# --------------------------------------------------------------------------
# Legend -> LegendSpec
# --------------------------------------------------------------------------


def _read_legend(
    item: QgsLayoutItemLegend,
    map_frame_id_by_uuid: Mapping[str, str],
    frame_mode_by_uuid: Mapping[str, FrameMode],
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> LegendSpec | None:
    label = f"Legend {_name(item)}"
    linked = item.linkedMap()
    if linked is None or linked.uuid() not in map_frame_id_by_uuid:
        report.unsupported(
            label, "This legend is not linked to a map frame, so it was left out."
        )
        return None
    frame_id = map_frame_id_by_uuid[linked.uuid()]
    is_static = frame_mode_by_uuid[linked.uuid()] == "static"

    rows: tuple[LegendRowSpec, ...] = ()
    derived = not is_static
    if is_static:
        rows = _literal_legend_rows(linked)
        report.approximated(
            label,
            "This legend is on a static frame, so its rows were derived once "
            "from the layers' renderers at export time rather than kept "
            "live.",
        )
    else:
        report.preserved(label, "Legend rows derive from the live frame.")

    if not derived and not rows:
        # A static legend with nothing to show (e.g. every layer unreadable)
        # would fail LegendSpec's own validation; skip it instead, with the
        # reason already recorded above as unsupported for whichever layer
        # produced nothing.
        report.unsupported(label, "No legend rows could be derived; left out.")
        return None

    return LegendSpec(
        id=f"legend-{item.uuid()}",
        box=_box(item, converter),
        for_=frame_id,
        title=item.title() or None,
        columns=item.columnCount() or None,
        derived=derived,
        rows=rows,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _literal_legend_rows(map_item: QgsLayoutItemMap) -> tuple[LegendRowSpec, ...]:
    rows: list[LegendRowSpec] = []
    for layer in map_item.layersToRender():
        if isinstance(layer, QgsVectorLayer) and layer.renderer() is not None:
            for symbol_item in layer.renderer().legendSymbolItems():
                color = None
                shape = None
                symbol = symbol_item.symbol()
                if symbol is not None:
                    try:
                        color = symbol.color().name()
                    except AttributeError:  # pragma: no cover - defensive
                        color = None
                    shape = _legend_shape_for_geometry(layer)
                rows.append(
                    LegendRowSpec(
                        layer=layer.name(),
                        label=symbol_item.label() or layer.name(),
                        color=color,
                        shape=shape,
                    )
                )
        elif isinstance(layer, QgsRasterLayer):
            rows.append(LegendRowSpec(layer=layer.name(), label=layer.name()))
    return tuple(rows)


def _legend_shape_for_geometry(layer: QgsVectorLayer) -> str | None:
    geometry_type = layer.geometryType()
    # 0 = Point, 1 = Line, 2 = Polygon (QgsWkbTypes.GeometryType).
    return {0: "circle", 1: "line", 2: "square"}.get(int(geometry_type))


# --------------------------------------------------------------------------
# Scale bar -> ScalebarSpec | TextSpec (numeric)
# --------------------------------------------------------------------------


def _read_scalebar(
    item: QgsLayoutItemScaleBar,
    map_frame_id_by_uuid: Mapping[str, str],
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> CartographItem | None:
    label = f"Scale bar {_name(item)}"
    linked = item.linkedMap()
    if linked is None or linked.uuid() not in map_frame_id_by_uuid:
        report.unsupported(
            label, "This scale bar is not linked to a map frame, so it was left out."
        )
        return None
    frame_id = map_frame_id_by_uuid[linked.uuid()]
    style_name = item.style() or ""

    if style_name == "Numeric":
        report.preserved(label, "Numeric scale bar exported as a live {{scale}} token.")
        return TextSpec(
            id=f"scalebar-{item.uuid()}",
            box=_box(item, converter),
            body=f"1:{{{{frame.{frame_id}.scale}}}}",
            kind="text",
            rotation=item.itemRotation(),
            z=round(item.zValue()),
        )

    kind = _SCALEBAR_KIND_BY_STYLE.get(style_name, "single")
    if style_name not in _SCALEBAR_KIND_BY_STYLE:
        report.approximated(
            label,
            f"QGIS scale bar style {style_name!r} has no exact cartograph "
            "equivalent; exported as a single-box scale bar.",
        )
    else:
        report.preserved(label, "Scale bar preserved.")

    units_map = {
        QgsUnitTypes.DistanceMeters: "metric",
        QgsUnitTypes.DistanceKilometers: "metric",
        QgsUnitTypes.DistanceFeet: "imperial",
        QgsUnitTypes.DistanceMiles: "imperial",
        QgsUnitTypes.DistanceNauticalMiles: "nautical",
    }
    units = units_map.get(item.units(), "metric")

    return ScalebarSpec(
        id=f"scalebar-{item.uuid()}",
        box=_box(item, converter),
        for_=frame_id,
        units=units,
        segments=item.numberOfSegments() or None,
        segment_length=item.unitsPerSegment() or None,
        kind=kind,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


# --------------------------------------------------------------------------
# Picture -> NorthSpec | ImageSpec
# --------------------------------------------------------------------------


def _is_north_arrow(item: QgsLayoutItemPicture) -> bool:
    if item.linkedMap() is not None:
        return True
    path = item.picturePath() or ""
    return "north" in Path(path).stem.lower()


def _read_picture(
    item: QgsLayoutItemPicture,
    map_frame_id_by_uuid: Mapping[str, str],
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> CartographItem | None:
    label = f"Picture {_name(item)}"
    if _is_north_arrow(item):
        linked = item.linkedMap()
        if linked is not None and linked.uuid() in map_frame_id_by_uuid:
            frame_id = map_frame_id_by_uuid[linked.uuid()]
        elif len(map_frame_id_by_uuid) == 1:
            frame_id = next(iter(map_frame_id_by_uuid.values()))
        else:
            report.unsupported(
                label,
                "This north arrow is not linked to a single map frame, so it "
                "was left out.",
            )
            return None
        report.approximated(
            label,
            "The QGIS north-arrow picture is replaced by the cartograph "
            "runtime's own north indicator glyph.",
        )
        return NorthSpec(
            id=f"north-{item.uuid()}",
            box=_box(item, converter),
            for_=frame_id,
            kind="arrow",
            rotation=item.itemRotation(),
            z=round(item.zValue()),
        )

    path = item.evaluatedPath() or item.picturePath()
    src = _image_data_uri(path, report, label)
    if src is None:
        return None
    report.preserved(label, "Picture preserved.")
    return ImageSpec(
        id=f"image-{item.uuid()}",
        box=_box(item, converter),
        src=src,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _image_data_uri(path: str, report: FidelityReportBuilder, label: str) -> str | None:
    if not path:
        report.unsupported(
            label, "This picture has no source image, so it was left out."
        )
        return None
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        report.unsupported(
            label, f"The picture source {path!r} could not be read ({error}); left out."
        )
        return None
    import base64

    suffix = Path(path).suffix.lower()
    mime = _IMAGE_MIME_BY_SUFFIX.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# --------------------------------------------------------------------------
# Label -> TextSpec
# --------------------------------------------------------------------------


def _label_frame(
    item: QgsLayoutItemLabel,
    map_frame_id_by_uuid: Mapping[str, str],
    reference_map: QgsLayoutItemMap | None,
    single_map: QgsLayoutItemMap | None,
) -> tuple[str, QgsLayoutItemMap] | None:
    """The map item a label's `@map_scale`/`@map_crs` resolve against.

    `QgsLayoutItemLabel` has no `linkedMap()` of its own (unlike the scale
    bar, legend and picture items) -- per issue #1 section 2.1, the choice is
    the layout's reference map, falling back to the layout's only map item
    when there is exactly one and no reference map was set.
    """
    candidate = reference_map or single_map
    if candidate is None or candidate.uuid() not in map_frame_id_by_uuid:
        return None
    return map_frame_id_by_uuid[candidate.uuid()], candidate


def _read_label(
    item: QgsLayoutItemLabel,
    layout: QgsPrintLayout,
    map_frame_id_by_uuid: Mapping[str, str],
    frame_mode_by_uuid: Mapping[str, FrameMode],
    reference_map: QgsLayoutItemMap | None,
    single_map: QgsLayoutItemMap | None,
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> TextSpec | None:
    label = f"Text {_name(item)}"
    frame = _label_frame(item, map_frame_id_by_uuid, reference_map, single_map)
    frame_id = frame[0] if frame else ""
    map_item = frame[1] if frame else None
    is_live = map_item is not None and frame_mode_by_uuid[map_item.uuid()] == "live"

    translated = translate_label(
        item.text(), frame_id=frame_id or "frame-unknown", frame_is_live=is_live
    )
    if translated.frozen_expressions:
        context = _label_expression_context(layout, map_item)
        values = []
        for expression_text in translated.frozen_expressions:
            expression = QgsExpression(expression_text)
            value = expression.evaluate(context)
            if expression.hasEvalError() or value is None:
                values.append("")
                report.approximated(
                    label,
                    f"The expression `{expression_text}` could not be "
                    "evaluated at export time and was left blank.",
                )
            else:
                values.append(str(value))
                report.approximated(
                    label, f"Expression `{expression_text}` frozen at export time."
                )
        body = translated.assemble(values)
    else:
        body = translated.template
        report.preserved(label, "Label preserved.")

    text_format = item.textFormat()
    font_size = text_format.size()
    color = text_format.color()
    return TextSpec(
        id=f"label-{item.uuid()}",
        box=_box(item, converter),
        body=body,
        kind="text",
        font_size=font_size if font_size > 0 else None,
        color=color.name() if color.isValid() else None,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _label_expression_context(
    layout: QgsPrintLayout, map_item: QgsLayoutItemMap | None
) -> QgsExpressionContext:
    """An explicit context, never `currentText()` (issue #1 section 2.1).

    Headlessly, `[% @map_scale %]` and `[% @map_crs %]` evaluate empty even
    through `item.createExpressionContext()` with a reference map set --
    nothing refreshes the label's own cached context after the map gets its
    extent. Appending `mapSettingsScope` built fresh from the map item's own
    `mapSettings()` is what makes them evaluate (verified headlessly).
    """
    project = layout.project() or QgsProject.instance()
    context = QgsExpressionContext()
    context.appendScope(QgsExpressionContextUtils.globalScope())
    context.appendScope(QgsExpressionContextUtils.projectScope(project))
    context.appendScope(QgsExpressionContextUtils.layoutScope(layout))
    if map_item is not None:
        size = map_item.sizeWithUnits()
        settings = map_item.mapSettings(
            map_item.extent(), QSizeF(size.width(), size.height()), 96, False
        )
        context.appendScope(QgsExpressionContextUtils.mapSettingsScope(settings))
        context.appendScope(QgsExpressionContextUtils.layoutItemScope(map_item))
    return context


# --------------------------------------------------------------------------
# Shape -> ShapeSpec
# --------------------------------------------------------------------------


def _read_shape(
    item: QgsLayoutItemShape,
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
) -> ShapeSpec | None:
    label = f"Shape {_name(item)}"
    shape_type = int(item.shapeType())
    # 0 = Ellipse, 1 = Rectangle, 2 = Triangle (QgsLayoutItemShape.Shape).
    if shape_type == 2:
        report.unsupported(
            label, "Triangle shapes have no cartograph equivalent and were left out."
        )
        return None
    kind = "ellipse" if shape_type == 0 else "rect"
    report.preserved(label, "Shape preserved.")

    symbol = item.symbol()
    fill = symbol.color().name() if symbol is not None else None
    stroke, stroke_width = _shape_stroke(item, report, label)
    radius = converter.convert(
        item.cornerRadius(), QgsUnitTypes.LayoutMillimeters
    ).length()

    return ShapeSpec(
        id=f"shape-{item.uuid()}",
        box=_box(item, converter),
        kind=kind,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        radius=radius or None,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _shape_stroke(
    item: QgsLayoutItemShape, report: FidelityReportBuilder, label: str
) -> tuple[str | None, float | None]:
    """The outline colour and width (mm) of a shape's first symbol layer.

    Only a simple fill's outline maps onto `<om-shape stroke>`; any other
    symbol layer type keeps its fill and loses its outline, reported.
    """
    symbol = item.symbol()
    if symbol is None or symbol.symbolLayerCount() == 0:
        return None, None
    layer = symbol.symbolLayer(0)
    if not isinstance(layer, QgsSimpleFillSymbolLayer):
        report.approximated(
            label,
            f"The {layer.layerType()} outline has no cartograph equivalent; "
            "the shape was exported without it.",
        )
        return None, None
    if layer.strokeStyle() == Qt.PenStyle.NoPen:
        return None, None
    width = layer.strokeWidth()
    if layer.strokeWidthUnit() != QgsUnitTypes.RenderMillimeters:
        report.approximated(
            label,
            "The outline width is not in millimetres, so it was exported at "
            "its numeric value as millimetres.",
        )
    # 0 is QGIS's "hairline"; the runtime needs a visible width.
    return layer.strokeColor().name(), width if width > 0 else 0.1
