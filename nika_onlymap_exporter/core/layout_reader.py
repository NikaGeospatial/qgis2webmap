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

One gap against issue #1's table, because `core/cartograph_ir.py`'s
dataclasses (frozen before this module existed, and shared with the writer
and its tests) have no attribute for it yet:

- **Group membership** is flattened with no `data-group` marker -- the
  runtime vocabulary mirrored into `cartograph_ir.py` carries no such
  attribute on any item. Each item keeps its own final absolute position
  (verified headlessly: `positionWithUnits()` is already absolute for a
  grouped item, not group-relative), so the *layout* survives; only the
  grouping relationship itself is recorded as approximated.

It is reported as a finding, not silently special-cased -- see
`_record_unrepresentable_item_properties`.

**Per-item opacity** is carried: each item's `opacity` (its own
`itemOpacity()`) is written as `opacity:` in the item's
inline style, and recorded as preserved.

**Atlas** (issue #1 section 2's `QgsLayoutAtlas` row, corrected against the
runtime's "Atlas" section) is read into `LayoutReading.atlas` when the
layout's atlas is enabled -- see `_read_atlas` for the rules. The runtime
pages an atlas through a *live* frame whose map carries the coverage layer's
rows, so an atlas that cannot meet that is recorded as blocked, with the
remedy, and left out rather than written as an `<om-atlas>` that pages
nothing.

**Ids.** Every emitted id comes from `cartograph_ir.element_id` -- a QGIS
uuid with its braces and hyphens removed -- because the runtime's
`{{frame.<id>.scale}}` token cannot carry either. `frame_modes` and
`frame_dpis` stay keyed by the raw `item.uuid()`, which is what the dialog
and the Processing algorithm persist.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsGraduatedSymbolRenderer,
    QgsLayerTreeLayer,
    QgsLayoutItem,
    QgsLayoutItemGroup,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurementConverter,
    QgsLayoutObject,
    QgsLegendStyle,
    QgsMapLayer,
    QgsMapLayerLegendUtils,
    QgsNumericFormatContext,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRuleBasedRenderer,
    QgsSimpleFillSymbolLayer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QFile, QIODevice, QSizeF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QTextDocument

from ..qt_compat import enum_value, legend_is_manual
from .atlas_reader import ATLAS_SUBJECT as ATLAS_SUBJECT
from .atlas_reader import (
    _atlas_fields_used,
    _read_atlas,
    _reads_atlas_feature,
    _reads_only_atlas,
)
from .atlas_reader import _MapState as _MapState
from .atlas_reader import atlas_page_count as atlas_page_count
from .atlas_reader import expression_error as expression_error
from .atlas_reader import layout_state_restored as layout_state_restored
from .cartograph_ir import (
    TEXT_KINDS,
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
    element_id,
    item_id_tokens,
    live_layer_dom_ids,
)
from .expression_tokens import (
    translate_label,
)
from .fidelity_report import FidelityReportBuilder
from .frame_layers import rendered_layers
from .layout_contrast import _record_low_contrast_text
from .layout_reading import AtlasRequirement as AtlasRequirement
from .layout_reading import FrameMode as FrameMode
from .layout_reading import LayoutReading as LayoutReading
from .layout_reading import MapFrameReading as MapFrameReading
from .layout_reading import _name
from .legend_edit import support_left_out
from .locator_context import has_wider_context
from .raster_legend import layers_with_unavailable_range
from .renderer_translator import drawn_rules
from .runtime_legend import ClassedLayer, describe_runtime_legend_changes
from .settings import default_frame_mode

#: A label's cartograph text kind, stored on the item by
#: `core/layout_builder.py` so a layout built from a cartograph reads back
#: with the same kinds (QGIS has no field for it).
TEXT_KIND_PROPERTY = "qgis2webmap/cartograph_text_kind"
#: Set on a legend's layer node whose heading a template broke onto lines
#: itself, for a QGIS (3.44) that does not wrap a heading at the legend's
#: wrap width (`core/canvas_cartograph.py`): the same name, so no rename.
WRAPPED_TITLE_PROPERTY = "qgis2webmap/cartograph_wrapped_title"
#: Set on a template's legend that left entries out because its box could
#: not hold them at a readable size (`core/canvas_cartograph.py`), so the
#: checks say so for as long as the legend still leaves some out.
LEGEND_LEFT_OUT_PROPERTY = "qgis2webmap/cartograph_legend_left_out"


def chosen_style_property(role: str) -> str:
    """The custom property `core/layout_builder.py` records a style a design
    chose under -- `role` is the attribute it becomes: "bg" (page or
    legend), "frame-bg", "frame-stroke" or "color" (legend text), as
    `#aarrggbb`, or "font-size" (legend rows). QGIS gives every new page,
    map and legend the same white, black and 12 pt, and those are not
    carried so ordinary layouts keep the theme's look; a design that chose
    them on purpose is."""
    return f"qgis2webmap/cartograph_chosen_{role}"


def _was_chosen(item: QgsLayoutItem, role: str, color: QColor) -> bool:
    stored = item.customProperty(chosen_style_property(role))
    if not isinstance(stored, str):
        return False
    chosen = QColor(stored)
    return chosen.isValid() and chosen.rgba() == color.rgba()


#: The QGIS item names (`item_id_tokens`) of the layout being read, so every
#: helper names its element consistently without threading a map through.
_ITEM_TOKENS: ContextVar[dict[str, str] | None] = ContextVar(
    "cartograph_item_tokens", default=None
)

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
# `style()` returns the renderer *id*, not the visible name: "hollow" and
# "stepped" (shown as "Hollow" and "Stepped Line") on both 3.44 and 4.2; the
# visible names are kept for projects written by older QGIS versions.
_SCALEBAR_KIND_BY_STYLE = {
    "Single Box": "single",
    "Double Box": "double",
    "Line Ticks Up": "ticks",
    "Line Ticks Down": "ticks",
    "Line Ticks Middle": "ticks",
    "stepped": "single",
    "Stepped Line": "single",
    "hollow": "single",
    "Hollow": "single",
}

#: Styles in the map above only as the nearest kind the runtime draws.
_NEAREST_ONLY_STYLES = frozenset({"stepped", "Stepped Line", "hollow", "Hollow"})

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


def layout_item_tokens(layout: QgsPrintLayout) -> dict[str, str]:
    """The name each of `layout`'s items gets in element ids (see
    `item_id_tokens`), so a caller can find the item an element came from."""
    return item_id_tokens(
        [
            (item.uuid(), item.id())
            for item in layout.items()
            if isinstance(item, QgsLayoutItem)
        ]
    )


def read_layout(
    layout: QgsPrintLayout,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
) -> LayoutReading:
    """Read `layout`'s first page into a `LayoutReading` (see `_read_layout`).

    Element ids are named after the QGIS item ids where the user gave one
    (`core/cartograph_ir.item_id_tokens`); the names are worked out once for
    the whole layout, so two items called "Legend" stay distinct.
    """
    reset = _ITEM_TOKENS.set(layout_item_tokens(layout))
    try:
        return _read_layout(layout, report, frame_modes=frame_modes)
    finally:
        _ITEM_TOKENS.reset(reset)


def _eid(prefix: str, uuid: str) -> str:
    """`element_id` with the names of the layout being read."""
    return element_id(prefix, uuid, _ITEM_TOKENS.get())


def _read_layout(
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
        # Not Blocked: the cartograph is page 1 and is saved regardless, so
        # the honest verdict is that the other pages are left out.
        report.unsupported(
            "Page count",
            f"This layout has {page_count} pages. A cartograph is one page, so "
            f"only page 1 is exported; pages 2 to {page_count} are left out. "
            "For one page per feature, use an atlas.",
        )
    page = pages.page(0)
    page_size = _box(page, converter)
    page_bg = _page_background(page, report)
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
    # QGIS leaves an item excluded from exports out of every export, so it is
    # dropped before anything references or rasterises it.
    included = []
    for item in items:
        if _is_excluded_from_exports(item):
            report.preserved(
                f"Layout item {_name(item)}",
                "This item is set to be excluded from exports in QGIS, so it "
                "was left out, as QGIS does in its own exports.",
            )
        else:
            included.append(item)
    items = included
    # Document order = zValue order (issue #1 section 2's last row): sorting
    # here means every downstream list -- frames, furniture, the writer's
    # eventual DOM order -- agrees with it for free.
    items.sort(key=lambda item: item.zValue())

    frames: list[MapFrameReading] = []
    furniture: list[CartographItem] = []
    # The Fidelity subject of every label's `<om-text>`, by element id, for
    # the contrast check once every item is read.
    text_subjects: dict[str, str] = {}
    map_frame_id_by_uuid: dict[str, str] = {}
    # The mode each frame actually got, overrides included: a legend or label
    # on a frame forced static must be static too.
    frame_mode_by_uuid: dict[str, FrameMode] = {}

    map_items = [item for item in items if isinstance(item, QgsLayoutItemMap)]
    reference_map = layout.referenceMap()
    single_map = map_items[0] if len(map_items) == 1 else None

    for item in map_items:
        mode = _frame_mode(item, frame_modes)
        box = _box(item, converter)
        frame_id = _eid("frame", item.uuid())
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
                opacity=_item_opacity(item),
                overview_of=None,  # resolved in the second pass below
                frame_bg=_map_background(item),
                frame_stroke=_map_frame_stroke(item),
            )
        )
        if mode == "static":
            report.approximated(
                f"Map frame {_name(item)}",
                "Rendered as a pixel-exact image rather than an interactive map.",
                item_id=item.uuid(),
            )
            _record_rasterised_layers(item, report)
        else:
            _record_live_frame_projection(item, report)

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
                        item_id=mf.map_item.uuid(),
                    )
                    if has_wider_context(mf.map_item) is False:
                        report.approximated(
                            f"Locator inset {_name(mf.map_item)}",
                            "The layers it draws reach no further than the map "
                            "it outlines, so it shows only a margin round it "
                            "and has no wider context to place the map in. "
                            "Select it on the page and delete it, or add a "
                            "layer that covers the wider area and choose the "
                            "inset's layers.",
                            item_id=mf.map_item.uuid(),
                        )
                    break

    # The atlas decides which live frames refit per page and which layer is
    # exported hidden; both have to be known before the layer ids are fixed.
    atlas_plan = _read_atlas(layout, frames, report)
    frames = _assign_layer_dom_ids(list(atlas_plan.frames))
    dom_ids_by_frame = {mf.frame_id: dict(mf.layer_dom_ids) for mf in frames}
    atlas_spec = atlas_plan.spec
    if atlas_spec is not None:
        # `_read_atlas` named the coverage layer by its QGIS id; the runtime
        # finds it by its `<om-layer id>` inside the driving frame.
        atlas_spec = replace(
            atlas_spec, layer=dom_ids_by_frame[atlas_spec.for_][atlas_spec.layer]
        )
    in_atlas = atlas_spec is not None

    for item in items:
        if isinstance(item, QgsLayoutItemMap):
            _record_unrepresentable_item_properties(item, report)
            grids = item.grids().asList() if item.grids() is not None else []
            frame_id = map_frame_id_by_uuid[item.uuid()]
            for index, grid in enumerate(grids):
                graticule = _read_grid(
                    item, grid, frame_id, converter, report, index=index
                )
                if graticule is not None:
                    furniture.append(
                        replace(
                            graticule,
                            locked=item.isLocked(),
                            hidden=not item.isVisible(),
                            opacity=_item_opacity(item),
                        )
                    )
            continue
        spec: CartographItem | None
        if isinstance(item, QgsLayoutItemLegend):
            spec = _read_legend(
                item,
                map_frame_id_by_uuid,
                frame_mode_by_uuid,
                dom_ids_by_frame,
                converter,
                report,
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
                in_atlas=in_atlas,
            )
            if spec is not None:
                text_subjects[spec.id] = f"Text {_name(item)}"
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
            _record_unrepresentable_item_properties(item, report)
            furniture.append(
                replace(
                    spec,
                    locked=item.isLocked(),
                    hidden=not item.isVisible(),
                    opacity=_item_opacity(item),
                )
            )

    _record_low_contrast_text(text_subjects, frames, furniture, page_bg, report)

    requirement = atlas_plan.requirement
    if requirement is not None and atlas_spec is not None:
        requirement = replace(
            requirement, fields=_atlas_fields_used(atlas_spec, furniture)
        )

    return LayoutReading(
        page_width=page_size.w,
        page_height=page_size.h,
        orientation=orientation,
        frames=tuple(frames),
        furniture=tuple(furniture),
        atlas=atlas_spec,
        atlas_requirement=requirement,
        bg=page_bg,
    )


def _with_overview_of(mf: MapFrameReading, overview_of: str) -> MapFrameReading:
    return replace(mf, overview_of=overview_of)


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
    pos = converter.convert(
        item.positionWithUnits(), QgsUnitTypes.LayoutUnit.LayoutMillimeters
    )
    size = converter.convert(
        item.sizeWithUnits(), QgsUnitTypes.LayoutUnit.LayoutMillimeters
    )
    if item.itemRotation():
        # A rotated item's position is its *rotated* top-left corner (an item
        # at (10, 20), 100 x 50, rotated 20 degrees reads (21.57, 4.41)),
        # but the runtime rotates the x/y/w/h box about its centre. So the
        # box is the unrotated one around the item's scene centre -- scene
        # units are the layout's *native* units (inches, points, ...), so the
        # centre is converted to millimetres like every other length here.
        scene_centre = item.mapToScene(item.rect().center())
        centre = (
            item.layout()
            .convertFromLayoutUnits(
                scene_centre, QgsUnitTypes.LayoutUnit.LayoutMillimeters
            )
            .toQPointF()
        )
        return Box(
            x=centre.x() - size.width() / 2.0,
            y=centre.y() - size.height() / 2.0,
            w=size.width(),
            h=size.height(),
        )
    return Box(x=pos.x(), y=pos.y(), w=size.width(), h=size.height())


def _is_excluded_from_exports(item: QgsLayoutItem) -> bool:
    """Whether QGIS leaves `item` out of exports: its own setting (a
    data-defined one is evaluated for this layout), or that of a group it is in."""
    prop = QgsLayoutObject.DataDefinedProperty.ExcludeFromExports
    defined = item.dataDefinedProperties()
    if defined.isActive(prop):
        excluded = defined.valueAsBool(
            prop, item.createExpressionContext(), item.excludeFromExports()
        )
    else:
        excluded = item.excludeFromExports()
    group = item.parentGroup()
    return excluded or (group is not None and _is_excluded_from_exports(group))


def _item_opacity(item: QgsLayoutItem) -> float:
    """The opacity the item is painted at, 0-1.

    Its own `itemOpacity()` only: QGIS does not compose a group's opacity onto
    its members when it paints (checked against `renderPageToImage`), so the
    group's is not folded in.
    """
    return round(min(max(item.itemOpacity(), 0.0), 1.0), 4)


def _record_unrepresentable_item_properties(
    item: QgsLayoutItem, report: FidelityReportBuilder
) -> None:
    """The one gap the module docstring explains: groups.

    `core/cartograph_ir.py` has no field for group membership, so it cannot be
    approximated into the output -- only reported as missing. Opacity is
    carried (`_item_opacity`) and so is not a gap.
    """
    label = _name(item)
    if item.parentGroup() is not None:
        report.approximated(
            f"Group of {label}",
            "This item is part of a group in QGIS. Groups are flattened on "
            "export -- the item keeps its own position, but the grouping "
            "relationship itself is not preserved.",
        )
    opacity = _item_opacity(item)
    if opacity < 1.0:
        report.preserved(
            f"Opacity of {label}",
            f"QGIS opacity {opacity * 100:.0f}% is kept on the exported item.",
        )


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def _frame_mode(
    item: QgsLayoutItemMap,
    frame_modes: Mapping[str, FrameMode],
) -> FrameMode:
    """The caller's override, else `default_mode_for_map` -- the same rule
    the Frames tab shows."""
    override = frame_modes.get(item.uuid())
    if override is not None:
        return override
    return default_mode_for_map(item)


def default_mode_for_map(item: QgsLayoutItemMap) -> FrameMode:
    """Issue #1 section 3's default, widened now that a live frame can draw
    in a projected CRS: live in one of the runtime's own camera projections,
    and live in any other CRS the frame draws in exactly (`draws_in_own_plane`
    with every layer able to follow); static (pixel-exact, never reprojected)
    for anything else, so a default never makes a map less like QGIS.

    A map the layout's enabled atlas drives is live whatever its CRS: only a
    live map can follow the pages, so a static default would export none."""
    if _atlas_drives(item):
        return "live"
    crs = item.crs()
    if default_frame_mode(crs.authid() if crs.isValid() else "") == "live":
        return "live"
    if draws_in_own_plane(item) and not plane_blocking_layers(item):
        return "live"
    return "static"


def _atlas_drives(item: QgsLayoutItemMap) -> bool:
    """True when `item` is controlled by an atlas that is turned on."""
    if not item.atlasDriven():
        return False
    layout = item.layout()
    atlas = layout.atlas() if layout is not None else None
    return atlas is not None and atlas.enabled()


def plane_blocking_layers(item: QgsLayoutItemMap) -> list[str]:
    """The names of `item`'s layers that keep a live frame in Web Mercator:
    a file raster (embedded, never projected) or an XYZ layer whose tiles are
    not images. The same test `core/cartograph_export.py` makes on the read
    layers, made here on QGIS's own so a default needs no export."""
    from .cartograph_export import is_image_tile_template
    from .layer_reader import xyz_template

    blockers = []
    for layer in rendered_layers(item):
        if not isinstance(layer, QgsRasterLayer):
            continue
        provider = (layer.providerType() or "").lower()
        tiles = xyz_template(layer) if provider == "wms" else None
        if (tiles is not None and not is_image_tile_template(tiles["url"])) or (
            provider == "gdal"
        ):
            blockers.append(layer.name())
    return blockers


def draws_in_own_plane(item: QgsLayoutItemMap) -> bool:
    """Whether a live frame of `item` can draw in the map's own CRS.

    The runtime draws a map with `crs=` in that projection's plane: a
    projected one, or a geographic one as plate carree (one scale in degrees
    on both axes, as QGIS draws it; runtime 0.13.0 and later). A plane view
    has no bearing, so a rotated map stays in Web Mercator, which keeps the
    rotation. Whether the frame's layers can follow is only known once they
    are read (`core/cartograph_export.py`), which also reports the outcome.
    """
    crs = item.crs()
    return (
        crs.isValid()
        and crs.authid() != "EPSG:3857"
        and bool(crs.toProj())
        and item.mapRotation() == 0
    )


def _record_live_frame_projection(
    item: QgsLayoutItemMap, report: FidelityReportBuilder
) -> None:
    """A live frame is drawn by the map runtime in Web Mercator, whatever CRS
    the QGIS map uses (issue #1 section 2's "approximated (live, reprojected
    to Web Mercator)"). Exact only for an EPSG:3857 map -- and for a map
    that `draws_in_own_plane`, whose row is written once its layers are read."""
    crs = item.crs()
    authid = crs.authid() if crs.isValid() else ""
    label = f"Map frame {_name(item)}"
    if authid == "EPSG:3857":
        report.preserved(
            label, "Exported as an interactive frame.", item_id=item.uuid()
        )
        return
    if draws_in_own_plane(item):
        return
    report.approximated(
        label,
        "Exported as an interactive frame, which the cartograph draws in Web Mercator "
        f"(EPSG:3857); QGIS draws this map in {authid or 'its own CRS'}, so "
        "shapes, and distances away from the centre, differ slightly. Set the "
        "frame to Image in Map frames to keep QGIS's projection exactly.",
        item_id=item.uuid(),
    )


def _record_rasterised_layers(
    item: QgsLayoutItemMap, report: FidelityReportBuilder
) -> None:
    """One Rasterised row per layer a static frame draws, under that layer.

    Rasterised is literally what happens: QGIS paints every one of these
    layers into the frame's picture (`exporters/frame_raster.py`), so they
    arrive exactly as QGIS draws them and stop being layers a reader can
    click or restyle. Recorded per layer so the Fidelity tab can show it
    beside each layer's other rows.
    """
    for layer in rendered_layers(item):
        report.raster_fallback(
            f"Map frame {_name(item)}: layer '{layer.name()}'",
            "Drawn into this image frame's picture by QGIS, exactly as QGIS "
            "draws it; in the cartograph it is part of the picture, not a "
            "layer a reader can click.",
            layer.id(),
            item_id=item.uuid(),
        )


def _frame_layer_ids(frame: MapFrameReading) -> list[str]:
    """The QGIS ids of the layers a live frame is written with: what the map
    draws, then any hidden layer it does not draw (an atlas coverage layer
    the map left out, which the atlas still pages through)."""
    ids = [layer.id() for layer in rendered_layers(frame.map_item)]
    ids.extend(layer_id for layer_id in frame.hidden_layer_ids if layer_id not in ids)
    return ids


def _assign_layer_dom_ids(frames: list[MapFrameReading]) -> list[MapFrameReading]:
    """Fill each live frame's `layer_dom_ids`, in `layersToRender()` order
    (top first -- the order `read_project`'s `layer_order` expects)."""
    live = [(mf.frame_id, _frame_layer_ids(mf)) for mf in frames if mf.mode == "live"]
    dom_ids = live_layer_dom_ids(live)
    layer_ids_by_frame = dict(live)
    result = []
    for mf in frames:
        if mf.mode != "live":
            result.append(mf)
            continue
        pairs = tuple(
            (layer_id, dom_ids[(mf.frame_id, layer_id)])
            for layer_id in dict.fromkeys(layer_ids_by_frame[mf.frame_id])
        )
        result.append(replace(mf, layer_dom_ids=pairs))
    return result


# --------------------------------------------------------------------------
# Page and frame backgrounds, and text contrast against them
# --------------------------------------------------------------------------


def _is_default_white(color: QColor) -> bool:
    return color.alpha() == 255 and color.rgb() & 0xFFFFFF == 0xFFFFFF


def _is_default_black(color: QColor) -> bool:
    return color.alpha() == 255 and color.rgb() & 0xFFFFFF == 0


def _page_background(
    page: QgsLayoutItemPage, report: FidelityReportBuilder
) -> str | None:
    """Page 1's background as `<om-cartograph bg>`, or `None` to keep the
    theme's page colour.

    Only a colour QGIS does not give every page is carried: a new layout's
    page is plain opaque white, and writing `bg="#ffffff"` for it would change
    the markup of every cartograph already exported for a near-identical
    colour -- unless the layout builder recorded it as a design's choice
    (`chosen_style_property`). A transparent page keeps the theme's colour
    too. The colour is the
    fill's own times the symbol's opacity, as `_shape_fill_opacity` reads a
    shape's; a fill QGIS draws as more than one plain colour is exported as
    its first colour, and says so.
    """
    symbol = page.pageStyleSymbol()
    if symbol is None or symbol.symbolLayerCount() == 0:
        return None
    color = QColor(symbol.color())
    alpha = symbol.opacity() * color.alphaF()
    layer = symbol.symbolLayer(0)
    plain = isinstance(layer, QgsSimpleFillSymbolLayer)
    if plain and layer.brushStyle() == Qt.BrushStyle.NoBrush:
        alpha = 0.0
    color.setAlpha(round(min(max(alpha, 0.0), 1.0) * 255))
    if color.alpha() == 0:
        return None
    if _is_default_white(color) and not _was_chosen(page, "bg", color):
        return None
    css = _css_color(color)
    if symbol.symbolLayerCount() > 1 or not (
        plain and layer.brushStyle() == Qt.BrushStyle.SolidPattern
    ):
        report.approximated(
            "Page background",
            f"The page background is not one plain colour; the cartograph page "
            f"is filled with its main colour, {css}.",
        )
    return css


def _map_background(item: QgsLayoutItemMap) -> str | None:
    """A map item's background as `<om-frame frame-bg>`, or `None` to keep
    the theme's frame colour.

    QGIS gives every new map item an opaque white background, so that colour
    is not carried: `frame-bg` is only what the frame shows before, or
    without, its map (the runtime fills a frame with `--om-carto-frame-bg`
    otherwise), and writing white for every ordinary map would change every
    exported cartograph's markup for no visible gain. Any other colour, as on
    a dark layout, is carried so the frame is never a pale box under light
    text. A map with no background keeps the theme's colour as well.
    """
    if not item.hasBackground():
        return None
    color = item.backgroundColor()
    if color.alpha() == 0:
        return None
    if _is_default_white(color) and not _was_chosen(item, "frame-bg", color):
        return None
    return _css_color(color)


def _map_frame_stroke(item: QgsLayoutItemMap) -> str | None:
    """A map item's frame colour as `<om-frame frame-stroke>`, or `None` to
    keep the theme's.

    The runtime always draws a thin edge round a frame, in
    `--om-carto-frame-stroke` (near black) unless told otherwise. A QGIS
    frame in its default black is that edge already, so it is not written --
    ordinary layouts keep their markup; nor is a map with no frame, which
    the runtime cannot leave edgeless without changing every such page. Any
    other colour is carried, a transparent one included: that is how a dark
    page keeps its frame from showing a pale line, and how a design asks for
    no visible edge at all.
    """
    if not item.frameEnabled():
        return None
    color = item.frameStrokeColor()
    if _is_default_black(color) and not _was_chosen(item, "frame-stroke", color):
        return None
    return _css_color(color)


# --------------------------------------------------------------------------
# Grid -> GraticuleSpec
# --------------------------------------------------------------------------


def _read_grid(
    map_item: QgsLayoutItemMap,
    grid,
    frame_id: str,
    converter: QgsLayoutMeasurementConverter,
    report: FidelityReportBuilder,
    *,
    index: int = 0,
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
    interval_x: float | None = grid.intervalX() or None
    interval_y: float | None = grid.intervalY() or None
    if int(grid.units()) != _GRID_MAP_UNITS:
        # Page-unit and dynamic intervals are distances on paper, not in the
        # grid's CRS -- written as an interval they would be read as metres
        # or degrees. The runtime's automatic 1-2-5 choice is the honest
        # substitute.
        interval_x = interval_y = None
        report.approximated(
            label,
            "The grid interval is set in page units, which the cartograph has "
            "no equivalent for; the interval is chosen automatically instead.",
        )
    return GraticuleSpec(
        # A map can carry several grids; each needs its own id.
        id=_eid("graticule", map_item.uuid()) + (f"_{index + 1}" if index else ""),
        box=_box(map_item, converter),
        for_=frame_id,
        crs=crs.authid() or None,
        crs_def=crs.toProj() or None,
        interval_x=interval_x,
        interval_y=interval_y,
        kind=kind,
        labels=_grid_labels(grid, label, report),
        format=_grid_format(grid, label, report),
        color=_grid_color(grid),
        z=round(map_item.zValue()),
    )


#: `QgsLayoutItemMapGrid.GridUnit` "map units" -- 0 on every version, but
#: spelled `MapUnit` on QGIS 3 and `MapUnits` on QGIS 4.
_GRID_MAP_UNITS = 0

#: `QgsLayoutItemMapGrid.AnnotationFormat` values printed as degrees and
#: minutes (and seconds): DegreeMinute, DegreeMinuteSecond and their
#: NoSuffix/Padded variants. The runtime's one alternative format is `dms`.
_DMS_FORMATS = frozenset({1, 2, 4, 5, 6, 7})
_DEGREE_MINUTE_FORMATS = frozenset({1, 4, 5})
_CUSTOM_FORMAT = 8

_GRID_SIDES = (
    QgsLayoutItemMapGrid.BorderSide.Left,
    QgsLayoutItemMapGrid.BorderSide.Right,
    QgsLayoutItemMapGrid.BorderSide.Top,
    QgsLayoutItemMapGrid.BorderSide.Bottom,
)


def _grid_labels(grid, label: str, report: FidelityReportBuilder) -> str:
    """`<om-graticule labels>` from where QGIS draws the annotations.

    QGIS sets inside/outside and what to show per side; the runtime has one
    placement for the whole grid. Sides with annotations hidden do not
    vote; a split vote goes to `outside`, the runtime's default.
    """
    if not grid.annotationEnabled():
        return "none"
    hide_all = int(QgsLayoutItemMapGrid.DisplayMode.HideAll)
    shown = [
        side for side in _GRID_SIDES if int(grid.annotationDisplay(side)) != hide_all
    ]
    if not shown:
        return "none"
    inside = int(QgsLayoutItemMapGrid.AnnotationPosition.InsideMapFrame)
    positions = {int(grid.annotationPosition(side)) == inside for side in shown}
    if len(shown) < len(_GRID_SIDES):
        report.approximated(
            label,
            "QGIS shows grid annotations on some sides only; the cartograph "
            "labels every side.",
        )
    if positions == {True}:
        return "inside"
    if len(positions) > 1:
        report.approximated(
            label,
            "QGIS draws some grid annotations inside the map and some outside; "
            "the cartograph puts them all outside.",
        )
    return "outside"


def _grid_format(grid, label: str, report: FidelityReportBuilder) -> str | None:
    annotation_format = int(grid.annotationFormat())
    if not grid.annotationEnabled():
        return None
    if annotation_format in _DEGREE_MINUTE_FORMATS:
        report.approximated(
            label,
            "Grid annotations in degrees and minutes are printed as degrees, "
            "minutes and seconds.",
        )
    if annotation_format in _DMS_FORMATS:
        return "dms"
    if annotation_format == _CUSTOM_FORMAT:
        report.approximated(
            label,
            "The grid's custom annotation expression has no cartograph "
            "equivalent; annotations are printed as plain coordinates.",
        )
    return None


def _grid_color(grid) -> str | None:
    symbol = grid.lineSymbol()
    return _css_color(symbol.color()) if symbol is not None else None


# --------------------------------------------------------------------------
# Legend -> LegendSpec
# --------------------------------------------------------------------------


def _read_legend(
    item: QgsLayoutItemLegend,
    map_frame_id_by_uuid: Mapping[str, str],
    frame_mode_by_uuid: Mapping[str, FrameMode],
    dom_ids_by_frame: Mapping[str, Mapping[str, str]],
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
            "This legend is on an image frame, so its rows were derived once "
            "from the layers' renderers at export time rather than kept "
            "live.",
        )
    else:
        rows = _live_legend_rows(
            item, linked, dom_ids_by_frame.get(frame_id, {}), label, report
        )
        changes = describe_runtime_legend_changes(
            _classed_legend_layers(item, linked), item.columnCount() or 1
        )
        if changes:
            report.approximated(
                label,
                "Legend rows derive from the interactive frame, with QGIS's labels "
                "and line and point swatches, but the browser's legend "
                + _join_phrases(changes)
                + ".",
            )
        else:
            report.preserved(label, "Legend rows derive from the interactive frame.")
    for layer_name in layers_with_unavailable_range(item, linked):
        report.approximated(
            label,
            f"QGIS has no finite range stored for '{layer_name}', so its own "
            'legend prints that layer\'s range as "nan" in the layout, the '
            "image and the PDF. The cartograph's legend shows no range for it. "
            "Set its minimum and maximum in the layer's Symbology to fix it in QGIS.",
        )
    left_out = _legend_left_out(item, linked)
    if left_out:
        report.approximated(label, left_out_detail(left_out))

    if not derived and not rows:
        # A static legend with nothing to show (e.g. every layer unreadable)
        # would fail LegendSpec's own validation; skip it instead, with the
        # reason already recorded above as unsupported for whichever layer
        # produced nothing.
        report.unsupported(label, "No legend rows could be derived; left out.")
        return None

    bg, color, stroke = _legend_style(item, label, report)
    font_size = None
    if item.customProperty(chosen_style_property("font-size")) is not None:
        row_text = item.style(QgsLegendStyle.Style.SymbolLabel).textFormat()
        points_per_unit = _POINTS_PER_UNIT.get(int(row_text.sizeUnit()))
        if points_per_unit is not None and row_text.size() > 0:
            font_size = round(row_text.size() * points_per_unit, 2)
    return LegendSpec(
        id=_eid("legend", item.uuid()),
        box=_box(item, converter),
        for_=frame_id,
        title=item.title() or None,
        columns=item.columnCount() or None,
        derived=derived,
        bg=bg,
        color=color,
        stroke=stroke,
        font_size=font_size,
        rows=rows,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


#: The legend text levels `<om-legend color>` stands for, as the layout
#: builder writes them.
_LEGEND_TEXT_STYLES = (
    QgsLegendStyle.Style.Title,
    QgsLegendStyle.Style.Group,
    QgsLegendStyle.Style.Subgroup,
    QgsLegendStyle.Style.SymbolLabel,
)


def _legend_style(
    item: QgsLayoutItemLegend, label: str, report: FidelityReportBuilder
) -> tuple[str | None, str | None, str | None]:
    """The legend's `bg`, `color` (its text ink) and `stroke` (its border),
    each `None` where QGIS's default stands.

    A new QGIS legend is opaque white with black text and no frame; those
    keep the theme's look, so ordinary legends write the markup they always
    did. A legend with its background switched off keeps the theme's panel
    too, and a frameless one the theme's border unless its panel is carried.
    The runtime has one ink for every level of legend text: the row
    labels' colour is used, and a legend whose levels differ says so.
    """
    bg = None
    if item.hasBackground():
        background = item.backgroundColor()
        if background.alpha() > 0 and (
            not _is_default_white(background) or _was_chosen(item, "bg", background)
        ):
            bg = _css_color(background)
    if item.frameEnabled():
        stroke = _css_color(item.frameStrokeColor())
    else:
        # The runtime always borders a legend in its theme's pale line; on a
        # panel of the layout's own colour that line is a box QGIS does not
        # draw, so it is made transparent there -- and only there, so a
        # plain legend's markup stays as it was.
        stroke = "#00000000" if bg is not None else None

    colors = []
    for style in _LEGEND_TEXT_STYLES:
        if style == QgsLegendStyle.Style.Title and not item.title():
            continue
        colors.append(QColor(item.style(style).textFormat().color()))
    ink = colors[-1]
    color = None
    if not _is_default_black(ink) or _was_chosen(item, "color", ink):
        color = _css_color(ink)
    if any(other.rgba() != ink.rgba() for other in colors[:-1]):
        report.approximated(
            label,
            "The legend's headings and rows are in different colours; the "
            f"cartograph legend writes all its text in the rows' colour, "
            f"{_css_color(ink)}.",
        )
    return bg, color, stroke


def _join_phrases(phrases: list[str]) -> str:
    """The phrases as one: "a, b and c"."""
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def _classed_legend_layers(
    item: QgsLayoutItemLegend, map_item: QgsLayoutItemMap
) -> list[ClassedLayer]:
    """The layers this legend lists that are drawn with classes."""
    listed = {node.layerId() for node in item.model().rootGroup().findLayers()}
    classed = []
    for layer in rendered_layers(map_item):
        if layer.id() not in listed or not isinstance(layer, QgsVectorLayer):
            continue
        renderer = layer.renderer()
        catch_all = False
        if isinstance(renderer, QgsCategorizedSymbolRenderer):
            count = len(renderer.categories())
            catch_all = any(
                category.renderState() and category.value() in (None, "")
                for category in renderer.categories()
            )
        elif isinstance(renderer, QgsGraduatedSymbolRenderer):
            count = len(renderer.ranges())
        elif isinstance(renderer, QgsRuleBasedRenderer):
            # Exported as categories, one per rule that draws.
            count = len(drawn_rules(renderer))
        else:
            continue
        classed.append(
            ClassedLayer(
                geometry=_legend_shape_for_geometry(layer) or "polygon",
                categorized=not isinstance(renderer, QgsGraduatedSymbolRenderer),
                class_count=count,
                catch_all=catch_all,
            )
        )
    return classed


def left_out_detail(count: int) -> str:
    """Checks wording for a template's legend that leaves out `count` entries."""
    return (
        f"The legend leaves out {count} "
        + ("entry" if count == 1 else "entries")
        + " that did not fit its box at a readable size. Use a larger "
        "page, shorten the layer names, or make the legend bigger in the "
        "layout designer and add them back in its item properties."
    )


def legend_left_out(item: QgsLayoutItemLegend, map_item: QgsLayoutItemMap) -> int:
    """`_legend_left_out`, for callers outside this module."""
    return _legend_left_out(item, map_item)


def _legend_left_out(item: QgsLayoutItemLegend, map_item: QgsLayoutItemMap) -> int:
    """How many entries of `map_item`'s layers -- class rows, or a layer's
    one row -- a template's legend still leaves out for want of room: 0
    for a legend no template marked (`LEGEND_LEFT_OUT_PROPERTY`), whose
    left-out rows are the user's own choice. Counted afresh, so entries the
    user has put back since are not reported."""
    if not item.customProperty(LEGEND_LEFT_OUT_PROPERTY):
        return 0
    model = item.model()
    shown = {
        node.layerId(): max(len(model.layerLegendNodes(node)), 1)
        for node in model.rootGroup().findLayers()
    }
    left_out = 0
    # Layers the template left out as technical (a hillshade, a basemap) did
    # not fail to fit, and the user can restore them: not counted.
    support = support_left_out(item)
    for layer in rendered_layers(map_item):
        if layer.id() in support:
            continue
        layer_legend = layer.legend()
        own = 0
        if layer_legend is not None:
            node = QgsLayerTreeLayer(layer)
            own = len(layer_legend.createLayerTreeModelLegendNodes(node))
        left_out += max(max(own, 1) - shown.get(layer.id(), 0), 0)
    return left_out


def _live_legend_rows(
    item: QgsLayoutItemLegend,
    map_item: QgsLayoutItemMap,
    dom_ids: Mapping[str, str],
    label: str,
    report: FidelityReportBuilder,
) -> tuple[LegendRowSpec, ...]:
    """`<om-legend-row>` overrides that make a live frame's derived legend
    read like the QGIS one (issue #1 section 2's legend row).

    The runtime derives one row per layer of the frame's map, in the map's
    own layer order -- bottom first, since that is document order -- and
    titles each with the layer's name. So every layer row gets an `order`
    (QGIS lists the top layer first), and, when the legend is edited by hand
    in QGIS: a renamed layer gets `label`, a layer removed from the legend
    gets `hide`, a renamed category gets `match` + `label` (matched on its
    original QGIS label, which `runtime_legend.fit_legend_rows` rekeys on
    the runtime's own label at export) and a removed one `match` + `hide`.
    What the runtime cannot express -- groups, reordered
    categories, symbol-level edits, layers from outside the map -- is
    reported as approximated.
    """
    layers_by_id = {layer.id(): layer for layer in rendered_layers(map_item)}
    if not legend_is_manual(item):
        ordered = [layer_id for layer_id in layers_by_id if layer_id in dom_ids]
        return tuple(
            LegendRowSpec(layer=dom_ids[layer_id], order=index)
            for index, layer_id in enumerate(ordered)
        )

    root = item.model().rootGroup()
    nodes = root.findLayers()
    if any(not hasattr(child, "layerId") for child in root.children()):
        report.approximated(
            label,
            "The legend's groups are not kept: the cartograph lists its layers "
            "without group headings.",
        )
    rows: list[LegendRowSpec] = []
    listed: set[str] = set()
    order = 0
    for node in nodes:
        layer_id = node.layerId()
        layer = layers_by_id.get(layer_id)
        if layer is None or layer_id not in dom_ids:
            report.approximated(
                label,
                f"The legend lists '{node.name()}', which is not drawn in the "
                "map it belongs to; the cartograph's legend only lists that "
                "map's layers.",
            )
            continue
        if layer_id in listed:
            continue
        listed.add(layer_id)
        dom_id = dom_ids[layer_id]
        title = node.customProperty("legend/title-label") or node.name()
        if node.customProperty(WRAPPED_TITLE_PROPERTY) and (
            " ".join(str(title).split()) == layer.name()
        ):
            # Only broken onto lines for QGIS: the runtime wraps it itself.
            title = layer.name()
        rows.append(
            LegendRowSpec(
                layer=dom_id,
                label=title if title != layer.name() else None,
                order=order,
            )
        )
        order += 1
        if node.customProperty("legend/title-style") == "hidden":
            report.approximated(
                label,
                f"The legend hides the title of '{layer.name()}' in QGIS; the "
                "cartograph shows it.",
            )
        rows += _legend_entry_rows(node, layer, dom_id, label, report)
    for layer_id, dom_id in dom_ids.items():
        if layer_id not in listed:
            rows.append(LegendRowSpec(layer=dom_id, hide=True))
    return tuple(rows)


def _legend_entry_rows(
    node,
    layer: QgsMapLayer,
    dom_id: str,
    label: str,
    report: FidelityReportBuilder,
) -> list[LegendRowSpec]:
    """Per-category overrides for one hand-edited layer node."""
    renderer = layer.renderer() if isinstance(layer, QgsVectorLayer) else None
    if renderer is None:
        return []
    originals = [entry.label() for entry in renderer.legendSymbolItems()]
    rows: list[LegendRowSpec] = []
    for index, original in enumerate(originals):
        user_label = QgsMapLayerLegendUtils.legendNodeUserLabel(node, index)
        if user_label and user_label != original:
            if len(originals) > 1:
                rows.append(
                    LegendRowSpec(layer=dom_id, match=original, label=user_label)
                )
            else:
                # A single-symbol layer has no entry rows in the runtime's
                # legend, only the layer's own row -- titled separately.
                report.approximated(
                    label,
                    f"The legend's symbol label for '{layer.name()}' "
                    f"('{user_label}') is not shown; the cartograph shows the "
                    "layer's title beside its swatch.",
                )
        symbol_size = QgsMapLayerLegendUtils.legendNodeSymbolSize(node, index)
        symbol_edited = QgsMapLayerLegendUtils.legendNodeCustomSymbol(
            node, index
        ) is not None or (symbol_size.isValid() and not symbol_size.isEmpty())
        if symbol_edited:
            report.approximated(
                label,
                f"A legend symbol of '{layer.name()}' was edited in the legend "
                "only; the cartograph draws the layer's own swatch.",
            )
    if QgsMapLayerLegendUtils.hasLegendNodeOrder(node):
        node_order = list(QgsMapLayerLegendUtils.legendNodeOrder(node))
        for index, original in enumerate(originals):
            if index not in node_order:
                rows.append(LegendRowSpec(layer=dom_id, match=original, hide=True))
        kept = [index for index in node_order if 0 <= index < len(originals)]
        if kept != sorted(kept):
            report.approximated(
                label,
                f"The legend reorders the entries of '{layer.name()}'; the "
                "cartograph keeps the layer's own order.",
            )
    return rows


def _literal_legend_rows(map_item: QgsLayoutItemMap) -> tuple[LegendRowSpec, ...]:
    rows: list[LegendRowSpec] = []
    for layer in rendered_layers(map_item):
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
    """The `<om-legend-row shape>` for `layer`: the runtime's own words,
    `point`, `line` or `polygon` -- anything else it draws as a square."""
    geometry_type = layer.geometryType()
    # 0 = Point, 1 = Line, 2 = Polygon (QgsWkbTypes.GeometryType).
    return {0: "point", 1: "line", 2: "polygon"}.get(int(geometry_type))


# --------------------------------------------------------------------------
# Scale bar -> ScalebarSpec | TextSpec (numeric)
# --------------------------------------------------------------------------


def _segment_length_metres(item: QgsLayoutItemScaleBar) -> float | None:
    """`segment-length` is ground metres in the runtime; QGIS stores it in
    the bar's own units (a "2 km" segment is `unitsPerSegment() == 2`)."""
    per_segment = item.unitsPerSegment()
    if not per_segment:
        return None
    factor = QgsUnitTypes.fromUnitToUnitFactor(
        item.units(), QgsUnitTypes.DistanceUnit.DistanceMeters
    )
    return per_segment * factor


#: The runtime's unit tiers per system, finest first (`scalebar.ts`): the
#: largest whose segment is at least one unit labels the bar.
_RUNTIME_SCALEBAR_UNITS = {
    "metric": ((1.0, "m"), (1000.0, "km")),
    "imperial": ((0.3048, "ft"), (1609.344, "mi")),
    "nautical": ((1852.0, "nmi"),),
}


def runtime_scalebar_labels(
    segment_metres: float, segments: int, units: str
) -> list[str]:
    """The labels the runtime writes under a bar of `segments` segments of
    `segment_metres` each: its own unit, fixed decimals, no separators."""
    tiers = _RUNTIME_SCALEBAR_UNITS.get(units, _RUNTIME_SCALEBAR_UNITS["metric"])
    per_unit, unit = tiers[0]
    for tier_per_unit, tier_unit in tiers:
        if segment_metres / tier_per_unit >= 1:
            per_unit, unit = tier_per_unit, tier_unit
    step = segment_metres / per_unit
    decimals = 2 if step < 1 else 1 if step < 10 and step != int(step) else 0
    labels = [f"{index * step:.{decimals}f}" for index in range(segments + 1)]
    labels[-1] += f" {unit}"
    return labels


def _scalebar_label_changes(item: QgsLayoutItemScaleBar, units: str) -> list[str]:
    """What the browser's labels on this bar change, as sentences for Checks.

    The runtime has no attribute for where the labels sit, their unit or
    their number format: it always writes them below the bar, in the unit
    it picks for the segment length, as plain fixed-point numbers.
    """
    notes = []
    placement = item.labelVerticalPlacement()
    if enum_value(placement) == 0:  # above the segment
        notes.append("The browser draws the bar's labels below it, not above.")
    segment_metres = _segment_length_metres(item)
    segments = item.numberOfSegments()
    if segment_metres and segments > 0:
        context = QgsNumericFormatContext()
        number_format = item.numericFormat()
        qgis_labels = [
            number_format.formatDouble(index * item.unitsPerSegment(), context)
            for index in range(segments + 1)
        ]
        if item.unitLabel():
            qgis_labels[-1] += f" {item.unitLabel()}"
        browser_labels = runtime_scalebar_labels(segment_metres, segments, units)
        if browser_labels != qgis_labels:
            notes.append(
                f"Its labels read '{' · '.join(browser_labels)}' rather than "
                f"'{' · '.join(qgis_labels)}': the browser picks the unit and "
                "writes plain numbers."
            )
    if item.numberOfSegmentsLeft() > 0:
        notes.append("The segments left of zero are not drawn.")
    return notes


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
            id=_eid("scalebar", item.uuid()),
            box=_box(item, converter),
            body=f"1:{{{{frame.{frame_id}.scale}}}}",
            kind="text",
            rotation=item.itemRotation(),
            z=round(item.zValue()),
        )

    kind = _SCALEBAR_KIND_BY_STYLE.get(style_name, "single")
    units_map = {
        QgsUnitTypes.DistanceUnit.DistanceMeters: "metric",
        QgsUnitTypes.DistanceUnit.DistanceKilometers: "metric",
        QgsUnitTypes.DistanceUnit.DistanceFeet: "imperial",
        QgsUnitTypes.DistanceUnit.DistanceMiles: "imperial",
        QgsUnitTypes.DistanceUnit.DistanceNauticalMiles: "nautical",
    }
    units = units_map.get(item.units(), "metric")

    notes = []
    if style_name not in _SCALEBAR_KIND_BY_STYLE or style_name in _NEAREST_ONLY_STYLES:
        # Mapped to the nearest kind the runtime draws is still a change the
        # reader sees: "Kept" would be a silent one.
        notes.append(
            f"QGIS scale bar style {style_name!r} has no exact cartograph "
            f"equivalent; exported as a {kind} scale bar."
        )
    notes += _scalebar_label_changes(item, units)
    if notes:
        report.approximated(label, " ".join(notes))
    else:
        report.preserved(label, "Scale bar preserved.")

    line_symbol = item.lineSymbol()
    return ScalebarSpec(
        id=_eid("scalebar", item.uuid()),
        box=_box(item, converter),
        for_=frame_id,
        units=units,
        segments=item.numberOfSegments() or None,
        segment_length=_segment_length_metres(item),
        kind=kind,
        # The runtime has one ink for bar and labels; the bar's line is the
        # one every QGIS style draws.
        color=_css_color(line_symbol.color()) if line_symbol is not None else None,
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


def _north_color(item: QgsLayoutItemPicture) -> str | None:
    """The colour a parametrised north-arrow SVG is drawn in.

    QGIS's own north arrows are SVGs with `param(fill)`, coloured by the
    picture's fill colour; an SVG without the parameter keeps its own
    colours, which the runtime's glyph cannot copy -- so it gets the theme
    ink (`None`) rather than a colour QGIS never used.
    """
    path = item.evaluatedPath() or item.picturePath() or ""
    if not path.lower().endswith(".svg"):
        return None
    if path.startswith(":/"):
        # A Qt resource -- QGIS's own default arrow, which the layout builder
        # uses. New pictures draw it white with a black outline, which the
        # theme's glyph already stands for; any other colour was chosen.
        resource = QFile(path)
        if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
            return None
        svg = bytes(resource.readAll()).decode("utf-8", errors="replace")
        resource.close()
        fill, outline = item.svgFillColor(), item.svgStrokeColor()
        if _is_default_white(fill) and _is_default_black(outline):
            return None
    else:
        try:
            svg = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    if "param(fill)" in svg:
        return _css_color(item.svgFillColor())
    if "param(outline)" in svg or "param(stroke)" in svg:
        return _css_color(item.svgStrokeColor())
    return None


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
            id=_eid("north", item.uuid()),
            box=_box(item, converter),
            for_=frame_id,
            kind="arrow",
            color=_north_color(item),
            rotation=item.itemRotation(),
            z=round(item.zValue()),
        )

    path = item.evaluatedPath() or item.picturePath()
    src = (
        _embedded_data_uri(path, report, label)
        if path.startswith("base64:")
        else _image_data_uri(path, report, label)
    )
    if src is None:
        return None
    report.preserved(label, "Picture preserved.")
    return ImageSpec(
        id=_eid("image", item.uuid()),
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
    suffix = Path(path).suffix.lower()
    mime = _IMAGE_MIME_BY_SUFFIX.get(suffix) or _sniff_image_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _embedded_data_uri(
    path: str, report: FidelityReportBuilder, label: str
) -> str | None:
    """A picture embedded in the project (`base64:<data>`, the picture
    item's "Embed file" option) -- there is no file to read."""
    try:
        data = base64.b64decode(path[len("base64:") :], validate=True)
    except (binascii.Error, ValueError) as error:
        report.unsupported(
            label, f"The embedded picture could not be decoded ({error}); left out."
        )
        return None
    if not data:
        report.unsupported(label, "The embedded picture is empty, so it was left out.")
        return None
    mime = _sniff_image_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


#: Leading bytes -> media type, for pictures with no (or a wrong) suffix.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_image_mime(data: bytes) -> str:
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    # QGIS's own raster pictures are overwhelmingly PNG.
    return "image/png"


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
    *,
    in_atlas: bool = False,
) -> TextSpec | None:
    label = f"Text {_name(item)}"
    frame = _label_frame(item, map_frame_id_by_uuid, reference_map, single_map)
    frame_id = frame[0] if frame else ""
    map_item = frame[1] if frame else None
    is_live = map_item is not None and frame_mode_by_uuid[map_item.uuid()] == "live"

    text = item.text()
    if int(item.mode()) == int(QgsLayoutItemLabel.Mode.ModeHtml):
        # `<om-text>` is text-only by contract (token values never become
        # DOM), so HTML formatting cannot travel; its text can.
        document = QTextDocument()
        document.setHtml(text)
        text = document.toPlainText()
        report.approximated(
            label,
            "This label is rendered as HTML in QGIS; its text is kept, its HTML "
            "formatting is not.",
        )

    translated = translate_label(
        text,
        frame_id=frame_id or "frame_unknown",
        frame_is_live=is_live,
        in_atlas=in_atlas,
        frame_item_id=map_item.id() if map_item is not None else None,
    )
    if translated.frozen_expressions:
        context = _label_expression_context(layout, map_item)
        values = []
        for expression_text in translated.frozen_expressions:
            expression = QgsExpression(expression_text)
            value = expression.evaluate(context)
            if value is None and not in_atlas and _reads_only_atlas(expression):
                # Atlas variables are NULL outside an atlas, by design: a
                # page number such as Atlas Ledger's is blank on one page.
                values.append("")
                report.preserved(
                    label,
                    f"`{expression_text}` shows the atlas page; with no atlas "
                    "on, it is blank, as in QGIS.",
                )
            elif expression.hasEvalError() or value is None:
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
            if in_atlas and _reads_atlas_feature(expression, layout):
                report.approximated(
                    label,
                    f"`{expression_text}` depends on the atlas feature, but has "
                    "no cartograph equivalent, so it reads the same on every "
                    "page. Fields, @atlas_featurenumber, @atlas_totalfeatures "
                    "and text joined with || stay live.",
                )
        body = translated.assemble(values)
    else:
        body = translated.template
        report.preserved(label, "Label preserved.")

    style = _label_style(item, label, report)
    kind = _text_kind(item)
    # A regular weight is the runtime's default for plain text, so it is left
    # out; a title's default is the theme's heavy one, so a regular title must
    # say so or the page shows it bold while QGIS draws it regular.
    weight = style.weight or (REGULAR_WEIGHT if kind == "title" else None)
    return TextSpec(
        id=_eid("label", item.uuid()),
        box=_box(item, converter),
        body=body,
        kind=kind,
        font_size=style.font_size,
        weight=weight,
        align=style.align,
        font=style.font,
        color=style.color,
        bg=style.bg,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


#: Words in an item's id or name that say which `<om-text kind>` it is.
#: QGIS has no "title" item -- a title is a label someone called "Title" --
#: so the item's own naming is the only signal. `kind` matters to the runtime
#: (the theme styles each kind, and attribution takes part in the credit
#: rule), but every visible property is also written explicitly, so a wrong
#: guess changes semantics, never the look.
_TITLE_WORDS = re.compile(r"\btitle\b", re.IGNORECASE)
_ATTRIBUTION_WORDS = re.compile(
    r"\b(attribution|credits?|copyright|sources?)\b", re.IGNORECASE
)


def _text_kind(item: QgsLayoutItemLabel) -> str:
    stored = item.customProperty(TEXT_KIND_PROPERTY)
    if stored in TEXT_KINDS:
        return str(stored)
    names = f"{item.id()} {item.displayName()}".replace("_", " ")
    if _TITLE_WORDS.search(names):
        return "title"
    if _ATTRIBUTION_WORDS.search(names):
        return "attribution"
    return "text"


@dataclass(frozen=True)
class _TextStyle:
    font_size: float | None
    weight: str | None
    align: str | None
    font: str | None
    color: str | None
    bg: str | None


#: `Qt.AlignmentFlag` -> `<om-text align>`. The runtime spells it start/
#: center/end (CSS logical values), not left/right.
_ALIGN_BY_FLAG = (
    (Qt.AlignmentFlag.AlignHCenter, "center"),
    (Qt.AlignmentFlag.AlignRight, "end"),
    (Qt.AlignmentFlag.AlignJustify, "start"),
    (Qt.AlignmentFlag.AlignLeft, "start"),
)

#: Points per text-size unit QGIS can express without a map scale.
_POINTS_PER_UNIT = {
    int(QgsUnitTypes.RenderUnit.RenderPoints): 1.0,
    int(QgsUnitTypes.RenderUnit.RenderMillimeters): 72.0 / 25.4,
    int(QgsUnitTypes.RenderUnit.RenderInches): 72.0,
    # CSS reference pixel: 96 per inch.
    int(QgsUnitTypes.RenderUnit.RenderPixels): 72.0 / 96.0,
}


def _label_style(
    item: QgsLayoutItemLabel, label: str, report: FidelityReportBuilder
) -> _TextStyle:
    """The label's look in the runtime's text vocabulary: `font-size` (pt),
    `weight`, `align`, `font`, `color`, `bg`."""
    text_format = item.textFormat()
    font = text_format.font()

    font_size = None
    points_per_unit = _POINTS_PER_UNIT.get(int(text_format.sizeUnit()))
    if text_format.size() > 0 and points_per_unit is not None:
        font_size = round(text_format.size() * points_per_unit, 2)
    elif text_format.size() > 0:
        report.approximated(
            label,
            "The text size is in map units, which a page has no scale for; the "
            "theme's text size is used instead.",
        )

    weight = _css_font_weight(font)
    if text_format.forcedBold():
        weight = "bold"

    if font.italic() or text_format.forcedItalic():
        report.approximated(
            label, "The text is italic in QGIS; the cartograph has no italic text."
        )
    if text_format.buffer().enabled() or text_format.background().enabled():
        report.approximated(
            label,
            "The text buffer or text background is not kept; a plain item "
            "background is.",
        )

    h_align = item.hAlign()
    align = "start"
    for flag, value in _ALIGN_BY_FLAG:
        if h_align & flag:
            align = value
            break
    if h_align & Qt.AlignmentFlag.AlignJustify:
        report.approximated(label, "Justified text is exported left-aligned (start).")
    v_align = item.vAlign()
    if v_align & (Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignBottom):
        report.approximated(
            label,
            "The text is vertically centred or bottom-aligned in QGIS; the "
            "cartograph sets text from the top of its box.",
        )

    family = font.family()
    bg = (
        _css_color(item.backgroundColor())
        if item.hasBackground() and item.backgroundColor().alpha() > 0
        else None
    )
    if item.frameEnabled():
        report.approximated(
            label, "The item's frame (border) is not kept; text items have none."
        )
    return _TextStyle(
        font_size=font_size,
        weight=weight,
        align=align,
        font=f'"{family}", sans-serif' if family else None,
        color=_css_color(text_format.color()),
        bg=bg,
    )


#: The CSS weight written for a regular title (see the label's `TextSpec`).
REGULAR_WEIGHT = "400"


def _css_font_weight(font: QFont) -> str | None:
    """A QFont weight as a CSS weight; `None` for regular (the default).

    Compared against `QFont.Weight` members rather than raw numbers: Qt 5
    numbers them 0-99 (Normal 50, Bold 75), Qt 6 100-900 like CSS.
    """
    weight = enum_value(font.weight())
    steps = (
        (QFont.Weight.Black, "900"),
        (QFont.Weight.ExtraBold, "800"),
        (QFont.Weight.Bold, "bold"),
        (QFont.Weight.DemiBold, "600"),
        (QFont.Weight.Medium, "500"),
        (QFont.Weight.Normal, None),
        (QFont.Weight.Light, "300"),
        (QFont.Weight.ExtraLight, "200"),
    )
    for threshold, css in steps:
        if weight >= enum_value(threshold):
            return css
    return "100"


def _css_color(color: QColor) -> str | None:
    """`#rrggbb`, or `#rrggbbaa` when the colour is not opaque -- `name()`
    alone would quietly drop a translucent colour's alpha."""
    if not color.isValid():
        return None
    if color.alpha() == 255:
        return color.name()
    return f"{color.name()}{color.alpha():02x}"


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
    atlas = layout.atlas()
    if atlas is not None and atlas.enabled():
        # So `@atlas_totalfeatures` and friends freeze to QGIS's own values
        # rather than blank (the label's own context would carry them).
        context.appendScope(QgsExpressionContextUtils.atlasScope(atlas))
    if map_item is not None:
        # The size is in PIXELS at the dpi given, not the item's millimetres:
        # passing mm made `@map_scale` read 96/25.4 times too large.
        converter = QgsLayoutMeasurementConverter()
        converter.setDpi(layout.renderContext().dpi())
        size = converter.convert(
            map_item.sizeWithUnits(), QgsUnitTypes.LayoutUnit.LayoutMillimeters
        )
        pixels = QSizeF(size.width() / 25.4 * 96, size.height() / 25.4 * 96)
        settings = map_item.mapSettings(map_item.extent(), pixels, 96, False)
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
    fill_opacity = _shape_fill_opacity(item)
    stroke, stroke_width = _shape_stroke(item, report, label)
    radius = converter.convert(
        item.cornerRadius(), QgsUnitTypes.LayoutUnit.LayoutMillimeters
    ).length()

    return ShapeSpec(
        id=_eid("shape", item.uuid()),
        box=_box(item, converter),
        kind=kind,
        fill=fill,
        fill_opacity=fill_opacity,
        stroke=stroke,
        stroke_width=stroke_width,
        radius=radius or None,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _shape_fill_opacity(item: QgsLayoutItemShape) -> float | None:
    """The fill's opacity, 0-1, or `None` when fully opaque (the default).

    Symbol opacity times the fill colour's own alpha -- QGIS keeps both --
    and 0 for a simple fill with no brush, which QGIS draws as an outline
    only.
    """
    symbol = item.symbol()
    if symbol is None:
        return None
    opacity = symbol.opacity() * symbol.color().alphaF()
    if symbol.symbolLayerCount() > 0:
        layer = symbol.symbolLayer(0)
        if (
            isinstance(layer, QgsSimpleFillSymbolLayer)
            and layer.brushStyle() == Qt.BrushStyle.NoBrush
        ):
            opacity = 0.0
    opacity = round(min(max(opacity, 0.0), 1.0), 4)
    return None if opacity >= 1.0 else opacity


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
        # No outline in QGIS: a zero width says so, where leaving the
        # attribute out gives the runtime's own 0.3 mm theme outline.
        return None, 0.0
    width = layer.strokeWidth()
    if layer.strokeWidthUnit() != QgsUnitTypes.RenderUnit.RenderMillimeters:
        report.approximated(
            label,
            "The outline width is not in millimetres, so it was exported at "
            "its numeric value as millimetres.",
        )
    # 0 is QGIS's "hairline"; the runtime needs a visible width.
    return layer.strokeColor().name(), width if width > 0 else 0.1
