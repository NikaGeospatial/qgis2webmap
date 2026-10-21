"""One-click mode: the current canvas -> a page plan, in millimetres.

Issue #1 section 4 (`specs/cartograph/qgis2carto-issues-1-5.md`): when the
user has no print layout, the cartograph is planned from the canvas alone --
AutoLayoutTool's arithmetic, then better. This module is that arithmetic and
nothing else: map-unit sizes and names in, millimetre boxes out. It never
imports `qgis`, so every placement rule is unit-tested in CI;
`core/canvas_cartograph.py` reads the real canvas and hands the numbers in.

The rules, in the order they are applied:

1. **Orientation** -- "auto" tries both and keeps the one whose map frame
   comes out larger. That is AutoLayoutTool's "landscape if the extent is
   wider than the page" rule made exact: it also accounts for the space the
   furniture takes, which a bare aspect comparison cannot.
2. **Frame** -- the largest box inside the margins (less the furniture) that
   keeps the extent's aspect, so the layout builder's `setExtent()` -- which
   resizes a map item to its extent's aspect -- leaves it exactly where it was
   planned.
3. **Furniture** -- legend right of the frame or below it, whichever leaves
   the larger frame; scale bar bottom-left under the frame; north arrow
   top-right above it; title beside the north arrow; attribution under the
   scale bar. Nothing overlaps the frame or anything else, and everything
   stays inside the margins -- the frame is never drawn over, because a
   QGIS user who keeps editing the layout would otherwise have to dig the
   furniture out from on top of the map first.
4. **Graticule** -- on only when the canvas CRS is geographic, where degrees
   are what a reader expects to see; its labels sit inside the frame for the
   same no-overlap reason.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TypedDict

from .cartograph_ir import (
    PAGE_SIZE_NAMES,
    Box,
    CartographItem,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    TextSpec,
)

#: Portrait page sizes in millimetres for every `PAGE_SIZE_NAMES` preset --
#: the same numbers the runtime's `size=` presets and QGIS's page size
#: registry use, kept here so the arithmetic needs neither.
_PORTRAIT_MM: dict[str, tuple[float, float]] = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
    # ANSI B. Not one of the runtime's `size=` presets, so a page this size is
    # written with its width and height instead (`page_spec_fields`).
    "Tabloid": (279.4, 431.8),
}

#: Every page size a plan can use: the runtime's presets plus Tabloid.
PLAN_PAGE_SIZES = tuple(_PORTRAIT_MM)

#: Placement constants, in millimetres. Small and fixed rather than scaled by
#: page size: furniture is read at arm's length on an A0 sheet too, and a
#: scale bar that grows with the paper only wastes map area.
MARGIN_MM = 10.0
_GAP = 4.0
_HEADER_H = 14.0
_NORTH_W = 12.0
_SCALEBAR_W = 50.0
#: QGIS's default scale bar text needs about 13 mm of height; the box leaves
#: it that, so the built bar never grows into the attribution line below.
_SCALEBAR_H = 14.0
_ATTRIBUTION_H = 5.0
_LEGEND_TITLE_H = 8.0
_LEGEND_ROW_H = 6.0
_LEGEND_MIN_W = 35.0
_LEGEND_MAX_W = 60.0
_LEGEND_MAX_COLUMNS = 3
#: Rough width of one character of legend text at the default size, used only
#: to pick a right-hand legend column wide enough for the longest layer name
#: or entry label.
_LEGEND_CHAR_W = 1.8
_LEGEND_SYMBOL_W = 10.0
#: Never let a legend below the frame take more than this share of the body.
_LEGEND_MAX_BODY_SHARE = 0.4

ORIENTATION_CHOICES = ("auto", "portrait", "landscape")
LEGEND_CHOICES = ("auto", "right", "below")


@dataclass(frozen=True)
class CanvasLayoutInput:
    """What the canvas contributes: extent size in map units, whether its CRS
    is geographic, the page preset, the project title, the visible layers'
    names and legend entries, and the layers' attribution credits."""

    extent_width: float
    extent_height: float
    crs_is_geographic: bool
    page_size: str = "A4"
    orientation: str = "auto"
    title: str = ""
    layer_names: Sequence[str] = ()
    credits: Sequence[str] = ()
    # Per layer, in `layer_names` order, the labels of its legend entries
    # (`legend_row_count`); a layer missing here is one row, its name.
    legend_labels: Sequence[Sequence[str]] = ()
    # "auto" puts the legend wherever the frame comes out larger.
    legend: str = "auto"


@dataclass(frozen=True)
class CanvasLayoutPlan:
    """The planned page. Every box is in millimetres from the page's top-left;
    `legend`, `title` and `attribution` are `None` when there is nothing to
    put in them."""

    page_size: str
    orientation: str
    page_width: float
    page_height: float
    margin: float
    frame: Box
    scalebar: Box
    north: Box
    legend: Box | None
    legend_position: str | None
    legend_columns: int | None
    title: Box | None
    title_text: str
    attribution: Box | None
    attribution_text: str
    graticule_interval: float | None


def page_dimensions(size: str, orientation: str) -> tuple[float, float]:
    """`(width, height)` in mm of a preset page in a concrete orientation."""
    if size not in _PORTRAIT_MM:
        raise ValueError(f"page size {size!r} is not one of {PLAN_PAGE_SIZES}")
    short, long = _PORTRAIT_MM[size]
    if orientation == "portrait":
        return short, long
    if orientation == "landscape":
        return long, short
    raise ValueError(f"orientation {orientation!r} must be portrait or landscape")


def nice_interval(raw: float) -> float:
    """The 1-2-5 series value nearest `raw` (in log space) -- the same series
    the runtime uses when a graticule's interval is omitted."""
    if not (raw > 0) or math.isinf(raw):
        raise ValueError(f"interval must be a positive finite number, got {raw!r}")
    exponent = math.floor(math.log10(raw))
    candidates = [m * 10.0**e for e in (exponent, exponent + 1) for m in (1, 2, 5)]
    return min(candidates, key=lambda c: abs(math.log10(c) - math.log10(raw)))


def plan_canvas_layout(data: CanvasLayoutInput) -> CanvasLayoutPlan:
    """Plan the page for `data`. Raises `ValueError` for an empty or
    non-finite extent, an unknown page size or an unknown orientation."""
    for name in ("extent_width", "extent_height"):
        value = getattr(data, name)
        if not (value > 0) or math.isinf(value):
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    if data.orientation not in ORIENTATION_CHOICES:
        raise ValueError(
            f"orientation {data.orientation!r} is not one of {ORIENTATION_CHOICES}"
        )
    if data.page_size not in _PORTRAIT_MM:
        raise ValueError(
            f"page size {data.page_size!r} is not one of {PLAN_PAGE_SIZES}"
        )
    if data.legend not in LEGEND_CHOICES:
        raise ValueError(f"legend {data.legend!r} is not one of {LEGEND_CHOICES}")

    credits = _unique_credits(data.credits)
    orientations = (
        ("landscape", "portrait") if data.orientation == "auto" else (data.orientation,)
    )
    plans = [_plan_for(data, orientation, credits) for orientation in orientations]
    # max() keeps the first of equals, so a tie goes to landscape.
    return max(plans, key=lambda plan: plan.frame.w * plan.frame.h)


def canvas_spec(
    plan: CanvasLayoutPlan, frame: FrameSpec, *, cartograph_id: str
) -> CartographSpec:
    """Assemble the `CartographSpec` for `plan` around `frame`.

    The frame's camera or georeference comes from the real canvas, which this
    module cannot read, so the caller builds `frame` -- but it must sit on the
    planned box, or every piece of furniture placed around it would be wrong.
    """
    if not _same_box(frame.box, plan.frame):
        raise ValueError(f"frame box {frame.box} is not the planned box {plan.frame}")
    return CartographSpec(
        cartograph_id=cartograph_id,
        title=plan.title_text,
        attribution=plan.attribution_text,
        orientation=plan.orientation,
        items=(_with_z(frame, 0), *canvas_furniture(plan, frame.id)),
        **page_spec_fields(plan.page_size, plan.orientation),
    )


class PageSpecFields(TypedDict):
    size: str | None
    width: float | None
    height: float | None


def page_spec_fields(page_size: str, orientation: str) -> PageSpecFields:
    """How a `CartographSpec` names this page: a runtime `size=` preset, or
    its width and height in mm for a size the runtime has no preset for."""
    if page_size in PAGE_SIZE_NAMES:
        return {"size": page_size, "width": None, "height": None}
    width, height = page_dimensions(page_size, orientation)
    return {"size": None, "width": width, "height": height}


def canvas_furniture(plan: CanvasLayoutPlan, frame_id: str) -> list[CartographItem]:
    """Everything `plan` places around the frame `frame_id`."""
    items: list[CartographItem] = []
    if plan.graticule_interval is not None:
        items.append(
            GraticuleSpec(
                id="graticule",
                box=plan.frame,
                for_=frame_id,
                crs="EPSG:4326",
                interval=plan.graticule_interval,
                labels="inside",
                z=1,
            )
        )
    if plan.legend is not None:
        items.append(
            LegendSpec(
                id="legend",
                box=plan.legend,
                for_=frame_id,
                columns=plan.legend_columns,
                z=2,
            )
        )
    items.append(ScalebarSpec(id="scalebar", box=plan.scalebar, for_=frame_id, z=3))
    items.append(NorthSpec(id="north", box=plan.north, for_=frame_id, z=4))
    if plan.title is not None:
        items.append(
            TextSpec(
                id="title",
                box=plan.title,
                body="{{title}}",
                kind="title",
                font_size=16.0,
                weight="bold",
                z=5,
            )
        )
    if plan.attribution is not None:
        items.append(
            TextSpec(
                id="attribution",
                box=plan.attribution,
                body="{{attribution}}",
                kind="attribution",
                font_size=7.0,
                z=6,
            )
        )
    return items


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _unique_credits(credits: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for credit in credits:
        text = credit.strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _same_box(a: Box, b: Box) -> bool:
    return all(
        math.isclose(getattr(a, k), getattr(b, k), abs_tol=1e-6)
        for k in ("x", "y", "w", "h")
    )


def _with_z(frame: FrameSpec, z: int) -> FrameSpec:
    return replace(frame, z=z) if frame.z is None else frame


def _fit(aspect: float, width: float, height: float) -> tuple[float, float]:
    """The largest `aspect` (w/h) box inside `width` x `height`."""
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    w = min(width, height * aspect)
    return w, w / aspect


def legend_row_count(
    layer_names: Sequence[str], legend_labels: Sequence[Sequence[str]] = ()
) -> int:
    """The rows a legend draws for these layers: one for a layer with a
    single entry (its symbol beside its name), and a heading plus a row per
    entry for one with several -- a categorized or graduated layer."""
    return sum(
        1 + len(labels) if len(labels) > 1 else 1
        for labels in _labels_per_layer(layer_names, legend_labels)
    )


def _labels_per_layer(
    layer_names: Sequence[str], legend_labels: Sequence[Sequence[str]]
) -> list[Sequence[str]]:
    labels = list(legend_labels[: len(layer_names)])
    return labels + [()] * (len(layer_names) - len(labels))


def _legend_right_width(
    layer_names: Sequence[str], legend_labels: Sequence[Sequence[str]] = ()
) -> float:
    texts = [*layer_names]
    for labels in _labels_per_layer(layer_names, legend_labels):
        texts.extend(labels)
    longest = max((len(text) for text in texts), default=0)
    wanted = _LEGEND_SYMBOL_W + longest * _LEGEND_CHAR_W
    return min(_LEGEND_MAX_W, max(_LEGEND_MIN_W, wanted))


def _plan_for(
    data: CanvasLayoutInput, orientation: str, credits: tuple[str, ...]
) -> CanvasLayoutPlan:
    page_w, page_h = page_dimensions(data.page_size, orientation)
    aspect = data.extent_width / data.extent_height
    content_w = page_w - 2 * MARGIN_MM
    content_h = page_h - 2 * MARGIN_MM
    footer_h = _SCALEBAR_H + ((_GAP / 2 + _ATTRIBUTION_H) if credits else 0.0)
    body_h = content_h - _HEADER_H - footer_h - 2 * _GAP
    layer_count = len(data.layer_names)
    row_count = legend_row_count(data.layer_names, data.legend_labels)

    # (frame w, frame h, legend position, legend w, legend h, columns)
    options: list[tuple[float, float, str | None, float, float, int | None]] = []
    if layer_count == 0:
        fw, fh = _fit(aspect, content_w, body_h)
        options.append((fw, fh, None, 0.0, 0.0, None))
    else:
        legend_w = _legend_right_width(data.layer_names, data.legend_labels)
        fw, fh = _fit(aspect, content_w - legend_w - _GAP, body_h)
        right_h = min(_LEGEND_TITLE_H + row_count * _LEGEND_ROW_H, fh)
        if data.legend in ("auto", "right"):
            options.append((fw, fh, "right", legend_w, right_h, 1))

        below_columns = min(_LEGEND_MAX_COLUMNS, row_count)
        rows = math.ceil(row_count / below_columns)
        below_h = min(
            _LEGEND_TITLE_H + rows * _LEGEND_ROW_H, body_h * _LEGEND_MAX_BODY_SHARE
        )
        fw, fh = _fit(aspect, content_w, body_h - below_h - _GAP)
        if data.legend in ("auto", "below"):
            options.append((fw, fh, "below", fw, below_h, below_columns))
    # max() keeps the first of equals, so a tie goes to the right-hand legend.
    fw, fh, legend_position, legend_w, legend_h, columns = max(
        options, key=lambda option: option[0] * option[1]
    )
    if fw <= 0 or fh <= 0:
        raise ValueError(f"no room for a map frame on a {data.page_size} page")

    group_w = fw + (legend_w + _GAP if legend_position == "right" else 0.0)
    group_h = (
        _HEADER_H
        + _GAP
        + fh
        + (legend_h + _GAP if legend_position == "below" else 0.0)
        + _GAP
        + footer_h
    )
    left = MARGIN_MM + (content_w - group_w) / 2
    top = MARGIN_MM + (content_h - group_h) / 2

    frame = Box(left, top + _HEADER_H + _GAP, fw, fh)
    north = Box(frame.x + frame.w - _NORTH_W, top, _NORTH_W, _HEADER_H)
    title_text = data.title.strip()
    title_w = north.x - _GAP - left
    title = Box(left, top, title_w, _HEADER_H) if title_text and title_w > 0 else None

    legend: Box | None = None
    below_frame = frame.y + frame.h + _GAP
    if legend_position == "right":
        legend = Box(frame.x + frame.w + _GAP, frame.y, legend_w, legend_h)
    elif legend_position == "below":
        legend = Box(frame.x, below_frame, legend_w, legend_h)
        below_frame += legend_h + _GAP

    scalebar = Box(frame.x, below_frame, min(_SCALEBAR_W, fw), _SCALEBAR_H)
    attribution_text = " · ".join(credits)
    attribution = (
        Box(left, scalebar.y + scalebar.h + _GAP / 2, group_w, _ATTRIBUTION_H)
        if credits
        else None
    )
    return CanvasLayoutPlan(
        page_size=data.page_size,
        orientation=orientation,
        page_width=page_w,
        page_height=page_h,
        margin=MARGIN_MM,
        frame=frame,
        scalebar=scalebar,
        north=north,
        legend=legend,
        legend_position=legend_position,
        legend_columns=columns,
        title=title,
        title_text=title_text,
        attribution=attribution,
        attribution_text=attribution_text,
        graticule_interval=(
            nice_interval(min(data.extent_width, data.extent_height) / 4)
            if data.crs_is_geographic
            else None
        ),
    )
