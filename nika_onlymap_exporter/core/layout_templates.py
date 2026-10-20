"""Layout templates: ten page designs the map canvas can become.

The one-click layout (`core/canvas_layout.py`) plans one page; this module
offers a choice of ten, modelled on what GIS users print most (Esri's print
service set: A3/A4/Letter/Tabloid in either orientation, plus "map only") and
on the recurring designs in Esri's 2025 user-conference map gallery
(locator insets, poster side panels, story maps with text over the map,
infographic dashboards, dark full-bleed maps). The page size and orientation
are chosen separately, so each template works on any of `PLAN_PAGE_SIZES`.

Like `canvas_layout`, this is arithmetic only: map-unit sizes and names in,
millimetre boxes out, no `qgis` import. A plan names the map frames it wants
(`FrameSlot`) -- the main map, and for some templates a locator inset -- and
`core/canvas_cartograph.py` fills them from the real canvas.

**Frames keep their designed shape.** Except in Classic and Legend below
(which keep the canvas's aspect, as the one-click layout always has), a
template's frame is the box the design gives it, and the layout builder's
`zoomToExtent()` pads the canvas extent out to that box -- so the whole
canvas view is always on the page, with more of the surroundings where the
box is relatively wider or taller.

**"auto" orientation** tries both and keeps the one that shows the canvas
extent largest in the main frame.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from .canvas_layout import (
    ORIENTATION_CHOICES,
    PLAN_PAGE_SIZES,
    CanvasLayoutInput,
    canvas_furniture,
    nice_interval,
    page_dimensions,
    page_spec_fields,
    plan_canvas_layout,
)
from .cartograph_ir import (
    Box,
    CartographItem,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)


def N_(text: str) -> str:  # noqa: N802 - the conventional gettext marker name
    """Mark a template's name or description for the translation catalogue.

    `ui/i18n.py` has the same marker, but it imports Qt, which this module
    must not; `scripts/update_translations.py` finds markers by name, and the
    UI translates the marked text with `tr()` where it shows it.
    """
    return text


#: The page sizes offered first, in the order the template picker lists them.
PAGE_SIZE_ORDER = ("A4", "A3", "Letter", "Tabloid", "A5", "A2", "A1", "A0", "Legal")

MAIN_FRAME_ID = "main"
LOCATOR_FRAME_ID = "locator"

#: How much more of the world a locator inset shows than the main map, per
#: side: enough to see where the main map is, close enough to recognise it.
LOCATOR_ZOOM_OUT = 8.0

_MARGIN = 10.0
_GAP = 4.0
#: The page every size factor is relative to: on A4 a template's text and
#: bands are the sizes written here, and a poster on A1 scales them up.
_A4_SHORT_SIDE = 210.0

# Colours for the designs that set their own look. Plain values rather than a
# theme: the page is a QGIS layout the user goes on editing, and these are the
# colours they will find there.
_INK = "#2a1f2d"
_PANEL = "rgba(255, 255, 255, 0.88)"
_DARK_PAGE = "#14161a"
_DARK_INK = "#e8e6e3"
_DARK_PANEL = "rgba(20, 22, 26, 0.82)"
_BAND = "#2a1f2d"
_BAND_INK = "#ffffff"
_TILE = "#f1ebf1"


@dataclass(frozen=True)
class TemplateInput:
    """What the canvas contributes, and the page asked for. `notes` is the
    text for the templates that have a text panel (the project's abstract),
    and `placeholders` the words a panel shows when there is no text yet --
    passed in, so the user's language reaches the page."""

    extent_width: float
    extent_height: float
    crs_is_geographic: bool
    crs_authid: str = ""
    crs_def: str | None = None
    page_size: str = "A4"
    orientation: str = "auto"
    title: str = ""
    layer_names: Sequence[str] = ()
    credits: Sequence[str] = ()
    notes: str = ""
    placeholders: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FrameSlot:
    """A map frame a template places. `overview_of` makes it a locator inset
    that outlines that frame; `zoom_out` is how much more it shows, per side,
    than the main map."""

    id: str
    box: Box
    overview_of: str | None = None
    zoom_out: float = 1.0
    frame_bg: str | None = None
    frame_stroke: str | None = None


@dataclass(frozen=True)
class TemplatePlan:
    """A planned page: its frames (the main map first), everything else on
    it, and the page's own colours when the design sets them."""

    template_id: str
    page_size: str
    orientation: str
    page_width: float
    page_height: float
    frames: tuple[FrameSlot, ...]
    items: tuple[CartographItem, ...]
    title_text: str
    attribution_text: str
    notes_text: str = ""
    bg: str | None = None
    color: str | None = None


@dataclass(frozen=True)
class _Page:
    """One concrete page for a template's design function."""

    data: TemplateInput
    width: float
    height: float
    title: str
    credits: str

    @property
    def scale(self) -> float:
        """How much bigger than A4 this page is, by its short side, for the
        templates whose text and bands grow with the paper."""
        return max(0.7, min(4.0, min(self.width, self.height) / _A4_SHORT_SIDE))

    @property
    def aspect(self) -> float:
        return self.data.extent_width / self.data.extent_height

    @property
    def landscape(self) -> bool:
        return self.width >= self.height

    def placeholder(self, key: str) -> str:
        return self.data.placeholders.get(key, _PLACEHOLDERS[key])


#: English fallbacks for `TemplateInput.placeholders`.
_PLACEHOLDERS = {
    "notes": "Describe the map here: what it shows, where the data comes from.",
    "figure": "Key figure",
    "figure_note": "What the number means",
    "author": "Author",
}

#: A page's own colours, when a design sets them: `bg` and `color`.
_Style = dict[str, str]
_Layout = tuple[tuple[FrameSlot, ...], list[CartographItem], _Style]
_Design = Callable[[_Page], _Layout]


@dataclass(frozen=True)
class LayoutTemplate:
    """One design: an id that never changes, a name and a one-line
    description (English; the UI translates them)."""

    id: str
    name: str
    description: str
    design: _Design


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def template_ids() -> tuple[str, ...]:
    return tuple(template.id for template in TEMPLATES)


def get_template(template_id: str) -> LayoutTemplate:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    raise ValueError(f"template {template_id!r} is not one of {template_ids()}")


def plan_template(template_id: str, data: TemplateInput) -> TemplatePlan:
    """Plan `data`'s page in the template `template_id`. Raises `ValueError`
    for an unknown template, page size or orientation, or an empty or
    non-finite extent."""
    template = get_template(template_id)
    for name in ("extent_width", "extent_height"):
        value = getattr(data, name)
        if not (value > 0) or math.isinf(value):
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    if data.orientation not in ORIENTATION_CHOICES:
        raise ValueError(
            f"orientation {data.orientation!r} is not one of {ORIENTATION_CHOICES}"
        )
    if data.page_size not in PLAN_PAGE_SIZES:
        raise ValueError(
            f"page size {data.page_size!r} is not one of {PLAN_PAGE_SIZES}"
        )
    orientations = (
        ("landscape", "portrait") if data.orientation == "auto" else (data.orientation,)
    )
    plans = [_plan_one(template, data, orientation) for orientation in orientations]
    # max() keeps the first of equals, so a tie goes to landscape.
    return max(plans, key=lambda plan: _shown_area(plan, data))


def template_spec(
    plan: TemplatePlan,
    frames: Mapping[str, FrameSpec],
    *,
    cartograph_id: str,
) -> CartographSpec:
    """Assemble the page around the caller's frames: one `FrameSpec` per
    `plan.frames` slot, by id, each on its planned box."""
    placed: list[CartographItem] = []
    for z, slot in enumerate(plan.frames):
        frame = frames.get(slot.id)
        if frame is None:
            raise ValueError(f"no frame given for the planned slot {slot.id!r}")
        if not _same_box(frame.box, slot.box):
            raise ValueError(
                f"frame {slot.id!r} box {frame.box} is not the planned {slot.box}"
            )
        placed.append(
            replace(
                frame,
                z=z if frame.z is None else frame.z,
                overview_of=slot.overview_of,
                frame_bg=slot.frame_bg or frame.frame_bg,
                frame_stroke=slot.frame_stroke or frame.frame_stroke,
            )
        )
    # Frames under everything else, then the template's own stacking.
    offset = len(placed)
    others = [replace(item, z=(item.z or 0) + offset) for item in plan.items]
    return CartographSpec(
        cartograph_id=cartograph_id,
        title=plan.title_text,
        attribution=plan.attribution_text,
        notes=plan.notes_text or None,
        orientation=plan.orientation,
        bg=plan.bg,
        color=plan.color,
        items=(*placed, *others),
        **page_spec_fields(plan.page_size, plan.orientation),
    )


def _plan_one(
    template: LayoutTemplate, data: TemplateInput, orientation: str
) -> TemplatePlan:
    width, height = page_dimensions(data.page_size, orientation)
    credits = " · ".join(_unique(data.credits))
    page = _Page(data, width, height, data.title.strip(), credits)
    frames, items, style = template.design(page)
    if not frames or frames[0].id != MAIN_FRAME_ID:
        raise ValueError(f"template {template.id} must place the main frame first")
    # A design writes its title box whatever the project says; a page with
    # no title gets no empty box to find in the layout designer.
    if not page.title:
        items = [item for item in items if item.id != "title"]
    return TemplatePlan(
        template_id=template.id,
        page_size=data.page_size,
        orientation=orientation,
        page_width=width,
        page_height=height,
        frames=frames,
        items=tuple(items),
        title_text=page.title,
        attribution_text=credits,
        notes_text=data.notes.strip(),
        bg=style.get("bg"),
        color=style.get("color"),
    )


def _shown_area(plan: TemplatePlan, data: TemplateInput) -> float:
    """How large the canvas extent comes out in the main frame: the frame's
    area when it keeps the extent's aspect, less the padding otherwise."""
    box = plan.frames[0].box
    w, h = _fit(data.extent_width / data.extent_height, box.w, box.h)
    return w * h


# --------------------------------------------------------------------------
# The ten designs
# --------------------------------------------------------------------------


def _classic(page: _Page, legend: str = "auto") -> _Layout:
    """The one-click layout itself: the frame keeps the canvas's aspect, the
    legend goes right of it or below, scale bar and north arrow around it."""
    data = page.data
    plan = plan_canvas_layout(
        CanvasLayoutInput(
            extent_width=data.extent_width,
            extent_height=data.extent_height,
            crs_is_geographic=data.crs_is_geographic,
            page_size=data.page_size,
            orientation="landscape" if page.landscape else "portrait",
            title=data.title,
            layer_names=data.layer_names,
            credits=data.credits,
            legend=legend,
        )
    )
    return (
        (FrameSlot(MAIN_FRAME_ID, plan.frame),),
        canvas_furniture(plan, MAIN_FRAME_ID),
        {},
    )


def _legend_below(page: _Page) -> _Layout:
    return _classic(page, legend="below" if page.data.layer_names else "auto")


def _map_only(page: _Page) -> _Layout:
    """Esri's MAP_ONLY: the map and nothing else -- but the data credits stay,
    because the licences most basemaps and open data come under require them."""
    m = _MARGIN
    items: list[CartographItem] = []
    bottom = page.height - m
    if page.credits:
        items.append(_attribution(Box(m, bottom - 4.0, page.width - 2 * m, 4.0)))
        bottom -= 4.0 + _GAP / 2
    frame = Box(m, m, page.width - 2 * m, bottom - m)
    return (FrameSlot(MAIN_FRAME_ID, frame),), items, {}


def _report_figure(page: _Page) -> _Layout:
    """A map to drop into a report: the frame at the canvas's aspect, a
    caption, scale bar and small north arrow under it, no legend."""
    m = _MARGIN
    caption_h = 10.0
    credits_h = 4.0 if page.credits else 0.0
    under = _GAP + caption_h + (credits_h + _GAP / 2 if credits_h else 0.0)
    content_w = page.width - 2 * m
    w, h = _fit(page.aspect, content_w, page.height - 2 * m - under)
    left = m + (content_w - w) / 2
    top = m + (page.height - 2 * m - h - under) / 2
    frame = Box(left, top, w, h)
    row = top + h + _GAP
    scalebar_w = min(45.0, w * 0.35)
    north_w = 8.0
    items: list[CartographItem] = [
        NorthSpec(
            id="north",
            box=Box(left + w - north_w, row, north_w, caption_h),
            for_=MAIN_FRAME_ID,
            z=2,
        ),
        ScalebarSpec(
            id="scalebar",
            box=Box(left + w - north_w - _GAP - scalebar_w, row, scalebar_w, caption_h),
            for_=MAIN_FRAME_ID,
            z=1,
        ),
    ]
    caption_w = w - north_w - scalebar_w - 2 * _GAP
    if page.title and caption_w > 20:
        items.append(
            TextSpec(
                id="title",
                box=Box(left, row, caption_w, caption_h),
                body="{{title}}",
                kind="title",
                font_size=10.0,
                weight="bold",
                z=3,
            )
        )
    if credits_h:
        items.append(_attribution(Box(left, row + caption_h + _GAP / 2, w, credits_h)))
    return (FrameSlot(MAIN_FRAME_ID, frame),), items, {}


def _field_map(page: _Page) -> _Layout:
    """A survey or field sheet: a bordered page, the map under a labelled
    grid in its own units, and a title block -- title, date, scale, CRS,
    north arrow and scale bar -- with the legend beside it."""
    m = _MARGIN
    data = page.data
    label_room = 6.0
    border = Box(m, m, page.width - 2 * m, page.height - 2 * m)
    inner = _inset(border, _GAP)
    if page.landscape:
        column_w = min(70.0, inner.w * 0.3)
        map_area = Box(inner.x, inner.y, inner.w - column_w - _GAP, inner.h)
        column = Box(map_area.x + map_area.w + _GAP, inner.y, column_w, inner.h)
        block_h = min(62.0, column.h * 0.5)
        block = Box(column.x, column.y + column.h - block_h, column.w, block_h)
        legend = Box(column.x, column.y, column.w, column.h - block_h - _GAP)
    else:
        band_h = min(62.0, inner.h * 0.3)
        map_area = Box(inner.x, inner.y, inner.w, inner.h - band_h - _GAP)
        band_y = map_area.y + map_area.h + _GAP
        block_w = min(90.0, inner.w * 0.55)
        block = Box(inner.x + inner.w - block_w, band_y, block_w, band_h)
        legend = Box(inner.x, band_y, inner.w - block_w - _GAP, band_h)
    frame = _inset(map_area, label_room)
    grid_crs = None if data.crs_is_geographic else (data.crs_authid or None)
    interval = nice_interval(min(data.extent_width, data.extent_height) / 4)
    items: list[CartographItem] = [
        ShapeSpec(id="border", box=border, stroke=_INK, stroke_width=0.5, z=0),
        GraticuleSpec(
            id="graticule",
            box=frame,
            for_=MAIN_FRAME_ID,
            crs="EPSG:4326" if data.crs_is_geographic else grid_crs,
            crs_def=None if data.crs_is_geographic else data.crs_def,
            interval=interval,
            labels="outside",
            z=1,
        ),
        ShapeSpec(id="title-block", box=block, stroke=_INK, stroke_width=0.35, z=2),
    ]
    if data.layer_names and legend.h > 12 and legend.w > 20:
        items.append(
            LegendSpec(
                id="legend",
                box=legend,
                for_=MAIN_FRAME_ID,
                columns=1 if page.landscape else 2,
                z=3,
            )
        )
    pad = 3.0
    cell = _inset(block, pad)
    line = 6.0
    north_w = 12.0
    text_w = cell.w - north_w - _GAP
    y = cell.y
    items.append(
        TextSpec(
            id="title",
            box=Box(cell.x, y, text_w, 10.0),
            body="{{title}}",
            kind="title",
            font_size=12.0,
            weight="bold",
            z=4,
        )
    )
    items.append(
        NorthSpec(
            id="north",
            box=Box(cell.x + cell.w - north_w, y, north_w, 16.0),
            for_=MAIN_FRAME_ID,
            z=5,
        )
    )
    y += 11.0
    for index, body in enumerate(
        ("Scale 1:{{scale}}", "{{crs}}", f"{page.placeholder('author')} · {{{{date}}}}")
    ):
        items.append(
            TextSpec(
                id=f"block-{index}",
                box=Box(cell.x, y, text_w, line),
                body=body,
                font_size=8.0,
                z=6 + index,
            )
        )
        y += line
    scalebar_h = 12.0
    items.append(
        ScalebarSpec(
            id="scalebar",
            box=Box(
                cell.x, cell.y + cell.h - scalebar_h, min(55.0, cell.w), scalebar_h
            ),
            for_=MAIN_FRAME_ID,
            z=9,
        )
    )
    if page.credits:
        items.append(_attribution(Box(m, page.height - m + 1.0, border.w, 4.0)))
    return (FrameSlot(MAIN_FRAME_ID, frame),), items, {}


def _poster(page: _Page) -> _Layout:
    """A wall poster: a title band across the top, the map large, a side
    panel (below the map on a portrait page) with the story, the legend,
    scale bar and north arrow."""
    m = _MARGIN * page.scale
    s = page.scale
    band_h = 26.0 * s
    footer_h = 5.0 * s if page.credits else 0.0
    body = Box(
        m,
        m + band_h + _GAP * s,
        page.width - 2 * m,
        page.height
        - 2 * m
        - band_h
        - _GAP * s
        - (footer_h + _GAP if footer_h else 0.0),
    )
    if page.landscape:
        panel_w = body.w * 0.28
        frame = Box(body.x, body.y, body.w - panel_w - _GAP * s, body.h)
        panel = Box(frame.x + frame.w + _GAP * s, body.y, panel_w, body.h)
    else:
        panel_h = body.h * 0.3
        frame = Box(body.x, body.y, body.w, body.h - panel_h - _GAP * s)
        panel = Box(body.x, frame.y + frame.h + _GAP * s, body.w, panel_h)
    items: list[CartographItem] = [
        TextSpec(
            id="title",
            box=Box(m, m, page.width - 2 * m, band_h * 0.62),
            body="{{title}}",
            kind="title",
            font_size=34.0 * s,
            weight="bold",
            z=1,
        ),
        TextSpec(
            id="subtitle",
            box=Box(m, m + band_h * 0.62, page.width - 2 * m, band_h * 0.38),
            body="{{date}}",
            font_size=12.0 * s,
            z=2,
        ),
        ShapeSpec(
            id="rule",
            box=Box(m, m + band_h, page.width - 2 * m, 0.6 * s),
            kind="rect",
            fill=_INK,
            z=3,
        ),
    ]
    items += _side_panel(page, panel, s, z=4)
    if footer_h:
        items.append(
            _attribution(
                Box(m, page.height - m - footer_h, page.width - 2 * m, footer_h),
                font_size=7.0 * s,
            )
        )
    return (FrameSlot(MAIN_FRAME_ID, frame),), items, {}


def _main_and_locator(page: _Page) -> _Layout:
    """The map with a locator inset beside it, outlining where it is, the
    legend under the inset; title above, scale bar and north arrow below."""
    m = _MARGIN
    header_h = 14.0
    footer_h = 14.0 + (4.0 + _GAP / 2 if page.credits else 0.0)
    body = Box(
        m,
        m + header_h + _GAP,
        page.width - 2 * m,
        page.height - 2 * m - header_h - footer_h - 2 * _GAP,
    )
    if page.landscape:
        column_w = min(80.0, body.w * 0.3)
        frame = Box(body.x, body.y, body.w - column_w - _GAP, body.h)
        locator = Box(
            frame.x + frame.w + _GAP, body.y, column_w, min(column_w, body.h * 0.45)
        )
        legend = Box(
            locator.x, locator.y + locator.h + _GAP, column_w, body.h - locator.h - _GAP
        )
    else:
        band_h = min(70.0, body.h * 0.3)
        frame = Box(body.x, body.y, body.w, body.h - band_h - _GAP)
        locator = Box(
            body.x, frame.y + frame.h + _GAP, min(band_h, body.w * 0.4), band_h
        )
        legend = Box(
            locator.x + locator.w + _GAP, locator.y, body.w - locator.w - _GAP, band_h
        )
    items = _header_and_footer(page, frame, header_h, body.y + body.h + _GAP)
    if page.data.layer_names and legend.h > 12:
        items.append(
            LegendSpec(
                id="legend",
                box=legend,
                for_=MAIN_FRAME_ID,
                columns=1 if page.landscape else 2,
                z=5,
            )
        )
    return (
        (
            FrameSlot(MAIN_FRAME_ID, frame),
            FrameSlot(
                LOCATOR_FRAME_ID,
                locator,
                overview_of=MAIN_FRAME_ID,
                zoom_out=LOCATOR_ZOOM_OUT,
                frame_stroke=_INK,
            ),
        ),
        items,
        {},
    )


def _story_map(page: _Page) -> _Layout:
    """A story map: the map fills the page, with the title, a text panel and
    a locator inset laid over it, and the scale bar on a light plate."""
    m = _MARGIN
    s = page.scale
    frame = Box(m, m, page.width - 2 * m, page.height - 2 * m)
    pad = 6.0
    panel_w = min(frame.w * 0.36, 110.0 * s)
    title_h = 22.0 * s
    title = Box(frame.x + pad, frame.y + pad, panel_w, title_h)
    notes = Box(
        frame.x + pad, title.y + title.h + _GAP, panel_w, min(frame.h * 0.18, 36.0 * s)
    )
    locator_w = min(frame.w, frame.h) * 0.24
    locator = Box(
        frame.x + frame.w - pad - locator_w,
        frame.y + frame.h - pad - locator_w,
        locator_w,
        locator_w,
    )
    plate = Box(frame.x + pad, frame.y + frame.h - pad - 16.0, 62.0, 16.0)
    items: list[CartographItem] = [
        TextSpec(
            id="title",
            box=title,
            body="{{title}}",
            kind="title",
            font_size=20.0 * s,
            weight="bold",
            bg=_PANEL,
            z=1,
        ),
        TextSpec(
            id="notes",
            box=notes,
            body="{{notes}}" if page.data.notes.strip() else page.placeholder("notes"),
            font_size=9.0 * s,
            bg=_PANEL,
            z=2,
        ),
        ShapeSpec(id="scale-plate", box=plate, fill="#ffffff", fill_opacity=0.88, z=3),
        ScalebarSpec(id="scalebar", box=_inset(plate, 1.5), for_=MAIN_FRAME_ID, z=4),
        NorthSpec(
            id="north",
            box=Box(frame.x + frame.w - pad - 12.0, frame.y + pad, 12.0, 16.0),
            for_=MAIN_FRAME_ID,
            z=5,
        ),
    ]
    credits_w = locator.x - plate.x - plate.w - 2 * _GAP
    if page.credits and credits_w > 10:
        items.append(
            _attribution(
                Box(plate.x + plate.w + _GAP, plate.y + plate.h - 4.0, credits_w, 4.0),
                bg=_PANEL,
            )
        )
    return (
        (
            FrameSlot(MAIN_FRAME_ID, frame),
            FrameSlot(
                LOCATOR_FRAME_ID,
                locator,
                overview_of=MAIN_FRAME_ID,
                zoom_out=LOCATOR_ZOOM_OUT,
                frame_stroke=_INK,
            ),
        ),
        items,
        {},
    )


def _dashboard(page: _Page) -> _Layout:
    """An infographic: a bold title band, the map with the legend beside it,
    and a row of tiles for key figures under it."""
    m = _MARGIN
    s = page.scale
    band_h = 22.0 * s
    tiles_h = min(page.height * 0.18, 42.0 * s)
    footer_h = 4.0 if page.credits else 0.0
    band = Box(0.0, 0.0, page.width, band_h + m)
    body_top = band.h + _GAP
    body_h = (
        page.height
        - body_top
        - tiles_h
        - _GAP
        - m
        - (footer_h + _GAP / 2 if footer_h else 0.0)
    )
    legend_w = (
        min(70.0 * s, (page.width - 2 * m) * 0.28) if page.data.layer_names else 0.0
    )
    frame = Box(
        m, body_top, page.width - 2 * m - (legend_w + _GAP if legend_w else 0.0), body_h
    )
    items: list[CartographItem] = [
        ShapeSpec(id="band", box=band, fill=_BAND, z=0),
        TextSpec(
            id="title",
            box=Box(m, m * 0.6, page.width - 2 * m, band_h),
            body="{{title}}",
            kind="title",
            font_size=26.0 * s,
            weight="bold",
            color=_BAND_INK,
            z=1,
        ),
    ]
    if legend_w:
        items.append(
            LegendSpec(
                id="legend",
                box=Box(frame.x + frame.w + _GAP, body_top, legend_w, body_h * 0.6),
                for_=MAIN_FRAME_ID,
                z=2,
            )
        )
        items.append(
            NorthSpec(
                id="north",
                box=Box(frame.x + frame.w + _GAP, body_top + body_h - 16.0, 12.0, 16.0),
                for_=MAIN_FRAME_ID,
                z=3,
            )
        )
    else:
        items.append(
            NorthSpec(
                id="north",
                box=Box(frame.x + frame.w - 14.0, frame.y + 2.0, 12.0, 16.0),
                for_=MAIN_FRAME_ID,
                z=3,
            )
        )
    items.append(
        ScalebarSpec(
            id="scalebar",
            box=Box(
                frame.x + 2.0, frame.y + frame.h - 14.0, min(50.0, frame.w * 0.4), 12.0
            ),
            for_=MAIN_FRAME_ID,
            z=4,
        )
    )
    tiles_top = frame.y + frame.h + _GAP
    count = 3
    tile_w = (page.width - 2 * m - (count - 1) * _GAP) / count
    for index in range(count):
        tile = Box(m + index * (tile_w + _GAP), tiles_top, tile_w, tiles_h)
        items.append(
            ShapeSpec(id=f"tile-{index}", box=tile, fill=_TILE, radius=2.0, z=5)
        )
        inner = _inset(tile, 4.0)
        items.append(
            TextSpec(
                id=f"tile-{index}-figure",
                box=Box(inner.x, inner.y, inner.w, inner.h * 0.58),
                body=page.placeholder("figure"),
                font_size=20.0 * s,
                weight="bold",
                z=6,
            )
        )
        items.append(
            TextSpec(
                id=f"tile-{index}-note",
                box=Box(inner.x, inner.y + inner.h * 0.6, inner.w, inner.h * 0.4),
                body=page.placeholder("figure_note"),
                font_size=8.0 * s,
                z=7,
            )
        )
    if footer_h:
        items.append(
            _attribution(
                Box(m, page.height - m - footer_h, page.width - 2 * m, footer_h)
            )
        )
    return (FrameSlot(MAIN_FRAME_ID, frame),), items, {}


def _dark(page: _Page) -> _Layout:
    """A dark full-bleed map: a dark page, the map to within a few mm of its
    edges, a light title, and the legend and scale bar on dark plates over
    the map. No north arrow, as on most dark data maps."""
    m = 6.0
    s = page.scale
    frame = Box(m, m, page.width - 2 * m, page.height - 2 * m)
    pad = 6.0
    items: list[CartographItem] = [
        TextSpec(
            id="title",
            box=Box(frame.x + pad, frame.y + pad, frame.w * 0.6, 20.0 * s),
            body="{{title}}",
            kind="title",
            font_size=24.0 * s,
            weight="bold",
            color=_DARK_INK,
            z=1,
        ),
    ]
    if page.data.layer_names:
        legend_w = min(60.0 * s, frame.w * 0.3)
        legend_h = min(frame.h * 0.4, 8.0 + 6.0 * len(page.data.layer_names))
        items.append(
            LegendSpec(
                id="legend",
                box=Box(
                    frame.x + pad,
                    frame.y + frame.h - pad - legend_h,
                    legend_w,
                    legend_h,
                ),
                for_=MAIN_FRAME_ID,
                bg=_DARK_PANEL,
                color=_DARK_INK,
                z=2,
            )
        )
    plate = Box(
        frame.x + frame.w - pad - 60.0, frame.y + frame.h - pad - 20.0, 60.0, 14.0
    )
    items.append(
        ShapeSpec(id="scale-plate", box=plate, fill=_DARK_PAGE, fill_opacity=0.82, z=3)
    )
    items.append(
        ScalebarSpec(
            id="scalebar",
            box=_inset(plate, 1.5),
            for_=MAIN_FRAME_ID,
            color=_DARK_INK,
            z=4,
        )
    )
    if page.credits:
        items.append(
            _attribution(
                Box(plate.x - 40.0, plate.y + plate.h + 1.0, plate.w + 40.0, 4.0),
                color=_DARK_INK,
                align="end",
            )
        )
    return (
        (
            FrameSlot(
                MAIN_FRAME_ID, frame, frame_bg=_DARK_PAGE, frame_stroke=_DARK_PAGE
            ),
        ),
        items,
        {"bg": _DARK_PAGE, "color": _DARK_INK},
    )


TEMPLATES: tuple[LayoutTemplate, ...] = (
    LayoutTemplate(
        "classic",
        N_("Classic"),
        N_("Title, map, legend beside it, scale bar and north arrow: the standard."),
        _classic,
    ),
    LayoutTemplate(
        "legend-below",
        N_("Legend below"),
        N_("The map across the page with the legend in a band under it."),
        _legend_below,
    ),
    LayoutTemplate(
        "map-only",
        N_("Map only"),
        N_("Just the map and its data credits, for placing in another document."),
        _map_only,
    ),
    LayoutTemplate(
        "report-figure",
        N_("Report figure"),
        N_("The map with a caption, scale bar and north arrow under it."),
        _report_figure,
    ),
    LayoutTemplate(
        "field-map",
        N_("Field / survey map"),
        N_(
            "A bordered sheet with a labelled grid and a title block: scale, CRS, date."
        ),
        _field_map,
    ),
    LayoutTemplate(
        "poster",
        N_("Poster"),
        N_("A large title band, the map, and a side panel for the story and legend."),
        _poster,
    ),
    LayoutTemplate(
        "locator",
        N_("Map with locator"),
        N_("The map with a locator inset showing where it is, and the legend."),
        _main_and_locator,
    ),
    LayoutTemplate(
        "story-map",
        N_("Story map"),
        N_("The map fills the page, with the title, text and a locator over it."),
        _story_map,
    ),
    LayoutTemplate(
        "dashboard",
        N_("Infographic"),
        N_("A bold title band, the map and legend, and tiles for key figures."),
        _dashboard,
    ),
    LayoutTemplate(
        "dark",
        N_("Dark full-bleed"),
        N_("A dark page with the map to the edges and light text over it."),
        _dark,
    ),
)


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


def _header_and_footer(
    page: _Page, frame: Box, header_h: float, footer_top: float
) -> list[CartographItem]:
    """Title above `frame` with the north arrow at its right, scale bar and
    credits from `footer_top` down -- the Classic arrangement, around any
    frame box and whatever sits under it."""
    north_w = 12.0
    top = frame.y - _GAP - header_h
    items: list[CartographItem] = [
        NorthSpec(
            id="north",
            box=Box(frame.x + frame.w - north_w, top, north_w, header_h),
            for_=MAIN_FRAME_ID,
            z=1,
        ),
        ScalebarSpec(
            id="scalebar",
            box=Box(frame.x, footer_top, min(50.0, frame.w), 14.0),
            for_=MAIN_FRAME_ID,
            z=2,
        ),
    ]
    if page.title:
        items.append(
            TextSpec(
                id="title",
                box=Box(frame.x, top, frame.w - north_w - _GAP, header_h),
                body="{{title}}",
                kind="title",
                font_size=16.0,
                weight="bold",
                z=3,
            )
        )
    if page.credits:
        items.append(
            _attribution(
                Box(
                    _MARGIN,
                    footer_top + 14.0 + _GAP / 2,
                    page.width - 2 * _MARGIN,
                    4.0,
                )
            )
        )
    return items


def _side_panel(page: _Page, panel: Box, s: float, *, z: int) -> list[CartographItem]:
    """The poster's panel: the story text, the legend, then the scale bar and
    north arrow at its foot."""
    items: list[CartographItem] = []
    foot_h = 16.0 * min(s, 2.0)
    has_legend = bool(page.data.layer_names)
    notes_h = (panel.h - foot_h - _GAP) * (0.45 if has_legend else 1.0)
    items.append(
        TextSpec(
            id="notes",
            box=Box(panel.x, panel.y, panel.w, notes_h),
            body="{{notes}}" if page.data.notes.strip() else page.placeholder("notes"),
            font_size=10.0 * s,
            z=z,
        )
    )
    if has_legend:
        legend_top = panel.y + notes_h + _GAP
        items.append(
            LegendSpec(
                id="legend",
                box=Box(
                    panel.x,
                    legend_top,
                    panel.w,
                    panel.y + panel.h - foot_h - _GAP - legend_top,
                ),
                for_=MAIN_FRAME_ID,
                z=z + 1,
            )
        )
    foot_y = panel.y + panel.h - foot_h
    north_w = 12.0 * min(s, 2.0)
    items.append(
        ScalebarSpec(
            id="scalebar",
            box=Box(
                panel.x,
                foot_y,
                min(60.0 * min(s, 2.0), panel.w - north_w - _GAP),
                foot_h,
            ),
            for_=MAIN_FRAME_ID,
            z=z + 2,
        )
    )
    items.append(
        NorthSpec(
            id="north",
            box=Box(panel.x + panel.w - north_w, foot_y, north_w, foot_h),
            for_=MAIN_FRAME_ID,
            z=z + 3,
        )
    )
    return items


def _attribution(
    box: Box,
    *,
    font_size: float = 7.0,
    color: str | None = None,
    bg: str | None = None,
    align: str | None = None,
) -> TextSpec:
    return TextSpec(
        id="attribution",
        box=box,
        body="{{attribution}}",
        kind="attribution",
        font_size=font_size,
        color=color,
        bg=bg,
        align=align,
        z=20,
    )


def _inset(box: Box, by: float) -> Box:
    return Box(
        box.x + by, box.y + by, max(box.w - 2 * by, 1.0), max(box.h - 2 * by, 1.0)
    )


def _fit(aspect: float, width: float, height: float) -> tuple[float, float]:
    """The largest `aspect` (w/h) box inside `width` x `height`."""
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    w = min(width, height * aspect)
    return w, w / aspect


def _same_box(a: Box, b: Box) -> bool:
    return all(
        math.isclose(getattr(a, k), getattr(b, k), abs_tol=1e-6)
        for k in ("x", "y", "w", "h")
    )


def _unique(credits: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for credit in credits:
        text = credit.strip()
        if text and text not in seen:
            seen.append(text)
    return seen


__all__ = [
    "LOCATOR_FRAME_ID",
    "MAIN_FRAME_ID",
    "PAGE_SIZE_ORDER",
    "TEMPLATES",
    "FrameSlot",
    "LayoutTemplate",
    "TemplateInput",
    "TemplatePlan",
    "get_template",
    "plan_template",
    "template_ids",
    "template_spec",
]
