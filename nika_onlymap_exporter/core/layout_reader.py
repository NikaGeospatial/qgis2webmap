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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from qgis.core import (
    Qgis,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
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
    QgsMapLayer,
    QgsMapLayerLegendUtils,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsSimpleFillSymbolLayer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSizeF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QTextDocument

from .cartograph_ir import (
    AtlasSpec,
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
    live_layer_dom_ids,
)
from .expression_tokens import (
    translate_atlas_pattern,
    translate_filter_expression,
    translate_label,
    translate_sort_expression,
)
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
    # Set on a live frame the atlas refits (`<om-frame atlas-fit atlas-margin>`).
    atlas_fit: str | None = None
    atlas_margin: float | None = None
    # Live frames only: `(QGIS layer id, <om-layer id>)` for every layer the
    # frame draws -- see `cartograph_ir.live_layer_dom_ids` for why the two
    # can differ -- and the layers to export hidden (the atlas coverage layer
    # under "Hide coverage layer"). Both are empty for a static frame.
    layer_dom_ids: tuple[tuple[str, str], ...] = ()
    hidden_layer_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtlasRequirement:
    """What the written atlas relies on in the *exported* coverage layer,
    which only `finish_cartograph` can check -- it is the one that reads the
    layer: the layer must reach `frame_id`'s `<om-map>` at all, and `fields`
    (named by the filter, sort, filename, page name and `{{atlas.*}}`
    labels) must survive the export's field pruning."""

    frame_id: str
    layer_id: str
    layer_name: str
    fields: tuple[str, ...] = ()


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
    atlas: AtlasSpec | None = None
    atlas_requirement: AtlasRequirement | None = None


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
        frame_id = element_id("frame", item.uuid())
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
    )


def _name(item: QgsLayoutItem) -> str:
    """An item's name as the layout designer's Items panel shows it, quoted
    the way every other fidelity subject quotes a layer name."""
    return f"'{item.displayName()}'"


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
    pos = converter.convert(item.positionWithUnits(), QgsUnitTypes.LayoutMillimeters)
    size = converter.convert(item.sizeWithUnits(), QgsUnitTypes.LayoutMillimeters)
    if item.itemRotation():
        # A rotated item's position is its *rotated* top-left corner (an item
        # at (10, 20), 100 x 50, rotated 20 degrees reads (21.57, 4.41)),
        # but the runtime rotates the x/y/w/h box about its centre. So the
        # box is the unrotated one around the item's scene centre -- scene
        # units are layout millimetres.
        centre = item.mapToScene(item.rect().center())
        return Box(
            x=centre.x() - size.width() / 2.0,
            y=centre.y() - size.height() / 2.0,
            w=size.width(),
            h=size.height(),
        )
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


def _record_live_frame_projection(
    item: QgsLayoutItemMap, report: FidelityReportBuilder
) -> None:
    """A live frame is drawn by the map runtime in Web Mercator, whatever CRS
    the QGIS map uses (issue #1 section 2's "approximated (live, reprojected
    to Web Mercator)"). Exact only for an EPSG:3857 map."""
    crs = item.crs()
    authid = crs.authid() if crs.isValid() else ""
    label = f"Map frame {_name(item)}"
    if authid == "EPSG:3857":
        report.preserved(label, "Exported as a live frame.")
        return
    report.approximated(
        label,
        "Exported as a live frame, which the cartograph draws in Web Mercator "
        f"(EPSG:3857); QGIS draws this map in {authid or 'its own CRS'}, so "
        "shapes, and distances away from the centre, differ slightly. Set the "
        "frame to Static in the Frames tab to keep QGIS's projection exactly.",
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
    for layer in item.layersToRender():
        report.raster_fallback(
            f"Map frame {_name(item)}: layer '{layer.name()}'",
            "Drawn into this static frame's picture by QGIS, exactly as QGIS "
            "draws it; in the cartograph it is part of the picture, not a "
            "layer a reader can click.",
            layer.id(),
        )


def _assign_layer_dom_ids(frames: list[MapFrameReading]) -> list[MapFrameReading]:
    """Fill each live frame's `layer_dom_ids`, in `layersToRender()` order
    (top first -- the order `read_project`'s `layer_order` expects)."""
    live = [
        (mf.frame_id, [layer.id() for layer in mf.map_item.layersToRender()])
        for mf in frames
        if mf.mode == "live"
    ]
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
# Atlas -> AtlasSpec
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _AtlasPlan:
    frames: tuple[MapFrameReading, ...]
    spec: AtlasSpec | None = None
    requirement: AtlasRequirement | None = None


def _draws_layer(frame: MapFrameReading, layer_id: str) -> bool:
    return any(layer.id() == layer_id for layer in frame.map_item.layersToRender())


def _read_atlas(
    layout: QgsPrintLayout,
    frames: Sequence[MapFrameReading],
    report: FidelityReportBuilder,
) -> _AtlasPlan:
    """The layout's atlas -> an `AtlasSpec`, and each atlas-driven frame's
    refit, per the runtime's three hard rules (`docs/cartograph.md`
    "Atlas"):

    - pages are turned by a **live** frame (`for=`) -- a static frame is a
      picture, with no features to page and a camera that cannot move;
    - the coverage layer must be **in that frame's map** with local rows
      (every vector layer the plugin exports is inline, so "in the map" is
      the condition left to check here; whether it actually survived the
      export is checked by `finish_cartograph` against `AtlasRequirement`);
    - the `filter` is the runtime's own expression language.

    When the first two cannot be met, nothing is written: an `<om-atlas>`
    that pages nothing would be worse than none. The reason and the remedy
    go in a Blocked row instead.
    """
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return _AtlasPlan(frames=tuple(frames))
    coverage = atlas.coverageLayer()
    if coverage is None or not coverage.isValid():
        report.blocked(
            "Layout item 'Atlas'",
            "The atlas is turned on but has no coverage layer, so no atlas "
            "pages were exported. Choose a coverage layer in the layout's "
            "Atlas panel, or turn the atlas off.",
        )
        return _AtlasPlan(frames=tuple(frames))
    coverage_id = coverage.id()
    coverage_name = coverage.name()

    updated = list(frames)
    driven_live: list[MapFrameReading] = []
    driven_static: list[MapFrameReading] = []
    for index, mf in enumerate(frames):
        if not mf.map_item.atlasDriven():
            continue
        if mf.mode == "static":
            driven_static.append(mf)
            report.blocked(
                f"Map frame {_name(mf.map_item)}",
                "This map follows the atlas, but it is a static frame: its "
                "picture is rendered once, so it would show the same place on "
                "every page. Set the atlas map to Live in the Frames tab.",
            )
            continue
        fit, margin = _atlas_fit(mf.map_item, report)
        updated[index] = replace(mf, atlas_fit=fit, atlas_margin=margin)
        driven_live.append(updated[index])

    driver = next(
        (mf for mf in driven_live if _draws_layer(mf, coverage_id)), None
    ) or next(
        (mf for mf in updated if mf.mode == "live" and _draws_layer(mf, coverage_id)),
        None,
    )
    if driver is None:
        if driven_live:
            remedy = (
                f"Add the coverage layer '{coverage_name}' to map "
                f"{_name(driven_live[0].map_item)}'s layers (tick \"Hide "
                'coverage layer" in the Atlas panel if it should not be '
                "drawn)."
            )
        elif driven_static:
            remedy = "Set the atlas map to Live in the Frames tab."
        else:
            remedy = (
                f"Show the coverage layer '{coverage_name}' in a map that is "
                "Live in the Frames tab."
            )
        report.blocked(
            "Layout item 'Atlas'",
            "No atlas pages were exported: the cartograph turns atlas pages "
            "with a live map that carries the coverage layer "
            f"'{coverage_name}', and this layout has none. {remedy}",
        )
        # No `<om-atlas>`, so nothing would refit these frames: drop the
        # attributes rather than leave the validator a dangling atlas-fit.
        return _AtlasPlan(
            frames=tuple(
                replace(mf, atlas_fit=None, atlas_margin=None) for mf in updated
            )
        )

    if atlas.hideCoverage():
        for index, mf in enumerate(updated):
            if not _draws_layer(mf, coverage_id):
                continue
            if mf.mode == "live":
                updated[index] = replace(
                    mf, hidden_layer_ids=(*mf.hidden_layer_ids, coverage_id)
                )
            else:
                report.approximated(
                    f"Map frame {_name(mf.map_item)}",
                    f"QGIS hides the atlas coverage layer '{coverage_name}' on "
                    "atlas pages, but this static frame's picture is rendered "
                    "outside an atlas page, so it shows the layer.",
                )
    limit_render = getattr(atlas, "limitCoverageLayerRenderToCurrentFeature", None)
    if limit_render is not None and limit_render():
        report.approximated(
            "Layout item 'Atlas'",
            "QGIS draws only the current feature of the coverage layer on each "
            "page; the cartograph draws all of them.",
        )

    spec = AtlasSpec(
        for_=driver.frame_id,
        layer=coverage_id,  # swapped for its <om-layer id> by read_layout
        filter=_atlas_filter(atlas, coverage, report),
        sort=_atlas_sort(atlas, report),
        filename=_atlas_filename(atlas, report),
        page_name=_atlas_page_name(atlas, coverage, report),
    )
    report.preserved(
        "Layout item 'Atlas'",
        f"One page per feature of '{coverage_name}', turned with the arrow "
        "keys or the on-sheet pager, and exported page by page.",
    )
    return _AtlasPlan(
        frames=tuple(updated),
        spec=spec,
        requirement=AtlasRequirement(
            frame_id=driver.frame_id,
            layer_id=coverage_id,
            layer_name=coverage_name,
        ),
    )


#: `QgsLayoutItemMap.AtlasScalingMode` values (stable across QGIS 3 and 4).
_ATLAS_FIXED = 0
_ATLAS_PREDEFINED = 1
_ATLAS_AUTO = 2

#: The runtime reads `atlas-margin="0"` as "unset" and pads by its 0.15
#: default, so a QGIS margin of zero is written as this instead -- 0.01% of
#: the feature's extent, which is no visible padding at all.
_SMALLEST_ATLAS_MARGIN = 0.0001


def _atlas_fit(
    map_item: QgsLayoutItemMap, report: FidelityReportBuilder
) -> tuple[str, float]:
    """An atlas-driven live map -> `atlas-fit`, `atlas-margin`.

    The margin is halved, not copied. Both are fractions, but QGIS scales
    the fitted extent by `1 + margin` about its centre (a 10% margin pads
    each side by 5% -- verified headlessly: a 1000 x 500 m feature at margin
    0.10 gives a 550 m tall map), whereas the runtime pads *each side* by
    `margin` (`padBounds`). So QGIS's `m` is the runtime's `m / 2`.

    The runtime only knows `feature` (refit to the feature) and `fixed`
    (do not move at all). QGIS Auto is `feature`. QGIS Fixed scale keeps the
    scale and recentres, and Predefined scales snaps to the nearest listed
    scale; neither exists in the runtime, and `fixed` would show the same
    place on every page, so both become `feature`, reported.
    """
    label = f"Map frame {_name(map_item)}"
    margin = max(map_item.atlasMargin() / 2.0, _SMALLEST_ATLAS_MARGIN)
    mode = int(map_item.atlasScalingMode())
    if mode == _ATLAS_AUTO:
        report.preserved(label, "Refits to each atlas feature, with QGIS's margin.")
    elif mode == _ATLAS_FIXED:
        report.approximated(
            label,
            "QGIS keeps this map at a fixed scale and centres it on each atlas "
            "feature; the cartograph fits each feature into the frame instead, "
            "so the scale changes from page to page.",
        )
    else:
        report.approximated(
            label,
            "QGIS shows each atlas feature at the nearest of the project's "
            "predefined scales; the cartograph fits each feature into the "
            "frame exactly instead, so pages are not at those scales.",
        )
    return "feature", round(margin, 6)


def _count_features(layer: QgsVectorLayer, expression: str | None) -> int:
    """Features of `layer` matching `expression` (all of them when `None`),
    respecting the layer's own subset filter -- what QGIS's atlas pages."""
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setNoAttributes()
    if expression:
        request.setFilterExpression(expression)
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        request.setExpressionContext(context)
    return sum(1 for _ in layer.getFeatures(request))


def atlas_page_count(layout: QgsPrintLayout) -> int | None:
    """How many pages the layout's atlas has in QGIS, or `None` when the
    atlas is off or has no coverage layer. For the Atlas tab's page count.

    Counted without touching the atlas itself (`QgsLayoutAtlas.updateFeatures`
    would move the layout designer's atlas preview): the coverage layer's
    features, through its subset filter and the atlas filter when that is
    on. The cartograph can page fewer: the runtime also skips features the
    exported map does not draw (a switched-off category), per its "the map's
    own filtering applies" rule.
    """
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return None
    coverage = atlas.coverageLayer()
    if coverage is None or not coverage.isValid():
        return None
    expression = atlas.filterExpression() if atlas.filterFeatures() else None
    return _count_features(coverage, expression or None)


def _atlas_filter(
    atlas, coverage: QgsVectorLayer, report: FidelityReportBuilder
) -> str | None:
    if not atlas.filterFeatures() or not atlas.filterExpression().strip():
        return None
    expression = atlas.filterExpression()
    translated = translate_filter_expression(expression)
    if translated is not None:
        report.preserved("Layout item 'Atlas'", f"Atlas filter `{expression}` kept.")
        return translated
    report.approximated(
        "Layout item 'Atlas'",
        f"The atlas filter `{expression}` has no equivalent in the cartograph's "
        "expression language, so it was left out: the cartograph has a page "
        f"for each of the layer's {_count_features(coverage, None)} features, "
        f"where QGIS has {_count_features(coverage, expression)}.",
    )
    return None


def _atlas_sort(atlas, report: FidelityReportBuilder) -> str | None:
    if not atlas.sortFeatures() or not atlas.sortExpression().strip():
        return None
    expression = atlas.sortExpression()
    translated = translate_sort_expression(expression, ascending=atlas.sortAscending())
    if translated is None:
        report.approximated(
            "Layout item 'Atlas'",
            f"Pages are sorted by `{expression}` in QGIS; the cartograph can "
            "only sort by a field, so its pages follow the layer's own order.",
        )
    return translated


def _atlas_filename(atlas, report: FidelityReportBuilder) -> str | None:
    expression = atlas.filenameExpression().strip()
    if not expression:
        return None
    translated = translate_atlas_pattern(expression)
    if translated is None:
        report.approximated(
            "Layout item 'Atlas'",
            f"The page file names `{expression}` could not be translated; "
            "exported pages are named page-1, page-2, ... instead.",
        )
    return translated


def _atlas_page_name(
    atlas, coverage: QgsVectorLayer, report: FidelityReportBuilder
) -> str | None:
    expression = atlas.pageNameExpression().strip()
    if not expression:
        return None
    # The Atlas panel stores a plain field choice as the bare field name,
    # which may need quoting to read as an expression (spaces, keywords).
    if coverage.fields().indexOf(expression) >= 0:
        expression = '"' + expression.replace('"', '""') + '"'
    translated = translate_atlas_pattern(expression)
    if translated is None:
        report.approximated(
            "Layout item 'Atlas'",
            f"The page names `{atlas.pageNameExpression()}` could not be "
            "translated; the pager shows each page's number instead.",
        )
    return translated


_ATLAS_TOKEN_RE = re.compile(r"\{\{\s*atlas\.([A-Za-z0-9_]+)\s*\}\}")
_CORE_FIELD_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_CORE_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _atlas_fields_used(
    atlas: AtlasSpec, furniture: Sequence[CartographItem]
) -> tuple[str, ...]:
    """Every coverage-layer field the written atlas reads, in first-use
    order: `{{atlas.<field>}}` in the filename, page name and text, `$field`
    in the filter, and the sort keys."""
    names: list[str] = []
    texts = [atlas.filename or "", atlas.page_name or ""]
    texts += [item.body for item in furniture if isinstance(item, TextSpec)]
    for text in texts:
        names += _ATLAS_TOKEN_RE.findall(text)
    if atlas.filter:
        names += _CORE_FIELD_RE.findall(_CORE_STRING_RE.sub("", atlas.filter))
    if atlas.sort:
        names += [key.strip().lstrip("-") for key in atlas.sort.split(",")]
    reserved = {"index", "count"}
    return tuple(name for name in dict.fromkeys(names) if name and name not in reserved)


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
        id=element_id("graticule", map_item.uuid()),
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
            "This legend is on a static frame, so its rows were derived once "
            "from the layers' renderers at export time rather than kept "
            "live.",
        )
    else:
        rows = _live_legend_rows(
            item, linked, dom_ids_by_frame.get(frame_id, {}), label, report
        )
        report.preserved(label, "Legend rows derive from the live frame.")

    if not derived and not rows:
        # A static legend with nothing to show (e.g. every layer unreadable)
        # would fail LegendSpec's own validation; skip it instead, with the
        # reason already recorded above as unsupported for whichever layer
        # produced nothing.
        report.unsupported(label, "No legend rows could be derived; left out.")
        return None

    return LegendSpec(
        id=element_id("legend", item.uuid()),
        box=_box(item, converter),
        for_=frame_id,
        title=item.title() or None,
        columns=item.columnCount() or None,
        derived=derived,
        rows=rows,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _legend_is_manual(item: QgsLayoutItemLegend) -> bool:
    """Whether the user edits this legend's content by hand.

    QGIS 4 replaced `autoUpdateModel()` with `syncMode()` (deprecated, still
    present); QGIS 3.44 only has the former.
    """
    sync_mode = getattr(item, "syncMode", None)
    if sync_mode is not None:
        return sync_mode() == Qgis.LegendSyncMode.Manual
    return not item.autoUpdateModel()


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
    original label, which is what the runtime keys on) and a removed one
    `match` + `hide`. What the runtime cannot express -- groups, reordered
    categories, symbol-level edits, layers from outside the map -- is
    reported as approximated.
    """
    layers_by_id = {layer.id(): layer for layer in map_item.layersToRender()}
    if not _legend_is_manual(item):
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


def _segment_length_metres(item: QgsLayoutItemScaleBar) -> float | None:
    """`segment-length` is ground metres in the runtime; QGIS stores it in
    the bar's own units (a "2 km" segment is `unitsPerSegment() == 2`)."""
    per_segment = item.unitsPerSegment()
    if not per_segment:
        return None
    factor = QgsUnitTypes.fromUnitToUnitFactor(
        item.units(), QgsUnitTypes.DistanceMeters
    )
    return per_segment * factor


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
            id=element_id("scalebar", item.uuid()),
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

    line_symbol = item.lineSymbol()
    return ScalebarSpec(
        id=element_id("scalebar", item.uuid()),
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
            id=element_id("north", item.uuid()),
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
        id=element_id("image", item.uuid()),
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
    return TextSpec(
        id=element_id("label", item.uuid()),
        box=_box(item, converter),
        body=body,
        kind=_text_kind(item),
        font_size=style.font_size,
        weight=style.weight,
        align=style.align,
        font=style.font,
        color=style.color,
        bg=style.bg,
        rotation=item.itemRotation(),
        z=round(item.zValue()),
    )


def _reads_atlas_feature(expression: QgsExpression, layout: QgsPrintLayout) -> bool:
    """Whether a frozen expression would change from page to page in QGIS:
    it reads an `@atlas_*` variable or a field of the coverage layer."""
    variables = {name.lower() for name in expression.referencedVariables()}
    if any(name.startswith("atlas_") for name in variables):
        return True
    coverage = layout.atlas().coverageLayer()
    if coverage is None:
        return False
    fields = {field.name() for field in coverage.fields()}
    return bool(set(expression.referencedColumns()) & fields)


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
    int(QgsUnitTypes.RenderPoints): 1.0,
    int(QgsUnitTypes.RenderMillimeters): 72.0 / 25.4,
    int(QgsUnitTypes.RenderInches): 72.0,
    # CSS reference pixel: 96 per inch.
    int(QgsUnitTypes.RenderPixels): 72.0 / 96.0,
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
    if (
        getattr(text_format, "forcedBold", None) is not None
        and text_format.forcedBold()
    ):
        weight = "bold"

    if font.italic() or (
        getattr(text_format, "forcedItalic", None) is not None
        and text_format.forcedItalic()
    ):
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


def _css_font_weight(font: QFont) -> str | None:
    """A QFont weight as a CSS weight; `None` for regular (the default).

    Compared against `QFont.Weight` members rather than raw numbers: Qt 5
    numbers them 0-99 (Normal 50, Bold 75), Qt 6 100-900 like CSS.
    """
    weight = _weight_value(font.weight())
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
        if weight >= _weight_value(threshold):
            return css
    return "100"


def _weight_value(weight: QFont.Weight | int) -> int:
    value = getattr(weight, "value", weight)
    return int(value)


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
    fill_opacity = _shape_fill_opacity(item)
    stroke, stroke_width = _shape_stroke(item, report, label)
    radius = converter.convert(
        item.cornerRadius(), QgsUnitTypes.LayoutMillimeters
    ).length()

    return ShapeSpec(
        id=element_id("shape", item.uuid()),
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
