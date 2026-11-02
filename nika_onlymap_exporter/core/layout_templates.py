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

**Frames keep their designed shape.** In Whole view framing, Daylight
(`classic`) and Caption (`report-figure`) keep the canvas's aspect. Fill
framing lets them use all the available map space. The other templates use
the box their design gives them. Whole view pads the extent out to that box;
Fill crops the view instead. A design that lays
panels over its map names the frame's clear part (`FrameSlot.focus`), and
the view is fitted there instead of centred.

**"auto" orientation** tries both and keeps the one that shows the canvas
extent largest in the main frame.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from .canvas_layout import (
    ORIENTATION_CHOICES,
    PLAN_PAGE_SIZES,
    legend_row_count,
    nice_interval,
    page_dimensions,
    page_spec_fields,
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
from .page_style import (
    DAYLIGHT,
    GRAPHITE,
    MINIMUM_TYPE,
    PAPER,
    PagePalette,
    Spacing,
    page_scale,
    parse_color,
    spacing_for,
    type_size,
)
from .translatable import N_ as N_  # re-exported for the modules adding templates

#: The page sizes offered first, in the order the template picker lists them.
PAGE_SIZE_ORDER = ("A4", "A3", "Letter", "Tabloid", "A5", "A2", "A1", "A0", "Legal")

MAIN_FRAME_ID = "main"
LOCATOR_FRAME_ID = "locator"

#: How much more of the world a locator inset shows than the main map, per
#: side: enough to see where the main map is, close enough to recognise it.
LOCATOR_ZOOM_OUT = 8.0

_MARGIN = 10.0
# Colours for the designs that set their own look come from the named
# palettes in `core/page_style.py`: plain colour values rather than a theme,
# since the page is a QGIS layout the user goes on editing, and these are the
# colours they will find there.


@dataclass(frozen=True)
class TemplateInput:
    """What the canvas contributes, and the page asked for. `notes` is the
    text for the templates that have a text panel (the project's abstract),
    `author` the project's author for a title block. A design with nothing
    real to say in a panel leaves the panel out: no stand-in words ever reach
    the page. `placeholders` is kept only so older callers still construct
    an input; it is no longer drawn."""

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
    # As `CanvasLayoutInput.legend_labels`.
    legend_labels: Sequence[Sequence[str]] = ()
    notes: str = ""
    placeholders: Mapping[str, str] = field(default_factory=dict)
    author: str = ""
    #: The canvas rotation in degrees: a map that is not north-up gets a
    #: north arrow in every design, one that is needs none in most.
    rotation: float = 0.0
    #: Up to three key figures the user supplied, as (value, what it is):
    #: the Signal design's figures. Never computed or made up here.
    figures: Sequence[tuple[str, str]] = ()
    #: The canvas extent's centre in map units, when known: how long a grid
    #: label's coordinates run, for a design that labels its grid.
    extent_center: tuple[float, float] | None = None
    #: Fill framing may use all available map space instead of keeping the
    #: canvas aspect in Daylight and Caption. Whole-view planning is unchanged.
    fill_frame: bool = False
    #: How much larger or smaller than standard the user wants the title, the
    #: legend and the notes (and a key figure, which follows the notes), as a
    #: factor between `MIN_TEXT_PERCENT` and `MAX_TEXT_PERCENT` percent, 1.0
    #: being today's size. A smaller one never goes
    #: under the readable floors, and the map takes the room it frees.
    title_scale: float = 1.0
    legend_scale: float = 1.0
    notes_scale: float = 1.0


#: The range of a text size, in percent of today's size, and its default.
#: One slider position per percent; the designs are fitted within it.
MIN_TEXT_PERCENT = 80
MAX_TEXT_PERCENT = 130
STANDARD_PERCENT = 100

#: The named steps older versions stored, as percent. Read only, so a project
#: saved with one still opens at the size it had.
_LEGACY_STEP_PERCENT = {
    "smaller": 80,
    "small": 90,
    "standard": 100,
    "large": 115,
    "larger": 130,
}


def text_percent(value: object) -> int:
    """`value` as a whole percent within the allowed range: a number, a
    numeric string, or a legacy step id ("smaller" ... "larger"). Anything
    else reads as 100."""
    if isinstance(value, bool):
        return STANDARD_PERCENT
    if isinstance(value, str):
        text = value.strip().lower().removesuffix("%").strip()
        if text in _LEGACY_STEP_PERCENT:
            number: float = _LEGACY_STEP_PERCENT[text]
        else:
            try:
                number = float(text)
            except ValueError:
                return STANDARD_PERCENT
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return STANDARD_PERCENT
    if number != number or number in (float("inf"), float("-inf")):
        return STANDARD_PERCENT
    return min(max(round(number), MIN_TEXT_PERCENT), MAX_TEXT_PERCENT)


@dataclass(frozen=True)
class TextSizes:
    """The size, in percent, chosen for the title, the legend and the notes:
    what the picker holds and the project keeps. A value is read through
    `text_percent`, so a legacy step id or a stray value is made sound."""

    title: int = STANDARD_PERCENT
    legend: int = STANDARD_PERCENT
    notes: int = STANDARD_PERCENT

    def __post_init__(self) -> None:
        for name in ("title", "legend", "notes"):
            object.__setattr__(self, name, text_percent(getattr(self, name)))

    def apply(self, data: TemplateInput) -> TemplateInput:
        """`data` with these percentages set as its scales."""
        return replace(
            data,
            title_scale=self.title / 100,
            legend_scale=self.legend / 100,
            notes_scale=self.notes / 100,
        )


#: Which of the three user-sized elements a text role belongs to.
_ELEMENT_OF_ROLE = {
    "display": "title",
    "title": "title",
    "legend": "legend",
    "subtitle": "notes",
    "body": "notes",
}


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
    #: The part of `box`, in page mm, the canvas view is fitted into, for a
    #: design that lays text over the frame: the view sits in the clear part
    #: and the panels cover only the surroundings. `None` centres the view.
    focus: Box | None = None


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
    #: The smallest the legend's row text is ever drawn, in points: the
    #: design's readable floor (`LayoutTemplate.legend_floor`). QGIS sets a
    #: legend smaller to fit its box only down to this
    #: (`canvas_cartograph._fit_legend`), never below.
    legend_min_pt: float = MINIMUM_TYPE.legend


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
        return page_scale(self.width, self.height)

    @property
    def aspect(self) -> float:
        return self.data.extent_width / self.data.extent_height

    @property
    def landscape(self) -> bool:
        return self.width >= self.height

    @property
    def notes(self) -> str:
        """The project's own notes, or "" -- a panel with nothing real to
        say is left out."""
        return self.data.notes.strip()

    @property
    def spacing(self) -> Spacing:
        return spacing_for(self.width, self.height)

    def pt(self, role: str, element: str | None = None) -> float:
        """The size in points for a text `role` on this page, with the
        user's size step for its element (`element`, or the role's own:
        title, legend or notes) applied and never below the role's readable
        minimum. Other roles are not the user's to size."""
        element = element or _ELEMENT_OF_ROLE.get(role)
        factor = {
            "title": self.data.title_scale,
            "legend": self.data.legend_scale,
            "notes": self.data.notes_scale,
        }.get(element or "", 1.0)
        base = type_size(role, self.width, self.height)
        if factor == 1.0:
            return base
        factor = min(max(factor, MIN_TEXT_PERCENT / 100), MAX_TEXT_PERCENT / 100)
        return round(max(base * factor, MINIMUM_TYPE.size(role)), 2)

    @property
    def rotated(self) -> bool:
        """Whether the map is turned away from north-up, so needs an arrow."""
        turn = self.data.rotation % 360.0
        return min(turn, 360.0 - turn) > 0.5

    @property
    def figures(self) -> tuple[tuple[str, str], ...]:
        """The user's key figures, at most three, blank and repeated ones
        dropped: a figure is never shown twice or empty."""
        seen: list[tuple[str, str]] = []
        for value, label in self.data.figures:
            figure = (value.strip(), label.strip())
            if figure[0] and figure not in seen:
                seen.append(figure)
        return tuple(seen[:3])


#: The picker's two groups of designs.
ESSENTIAL = "essential"
CHARACTERFUL = "characterful"
CATEGORIES = (ESSENTIAL, CHARACTERFUL)

#: A page's own colours, when a design sets them: `bg` and `color`.
_Style = dict[str, str]
_Layout = tuple[tuple[FrameSlot, ...], list[CartographItem], _Style]
_Design = Callable[[_Page], _Layout]
#: A legend's content: its layer names, and each layer's class labels.
_LegendKey = tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]


@dataclass(frozen=True)
class LayoutTemplate:
    """One design: an id that never changes, a name and a one-line
    description (English; the UI translates them)."""

    id: str
    name: str
    description: str
    design: _Design
    #: ESSENTIAL (quiet, safe first choices) or CHARACTERFUL; the picker
    #: filters by it.
    category: str = "essential"
    #: Short English cues for the picker's "Includes ..." line, e.g.
    #: ("legend", "scale bar"); translated by the UI.
    includes: tuple[str, ...] = ()
    #: A short English "Best for: ..." line naming the jobs the design suits,
    #: shown in the card's tooltip; translated by the UI.
    best_for: str = ""
    #: The design's readable floor for legend rows on a page, in points
    #: (`TemplatePlan.legend_min_pt`); `None` is `MINIMUM_TYPE.legend`. A
    #: large-print design sets a higher one.
    legend_floor: Callable[[_Page], float] | None = None


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def plan_template(template_id: str, data: TemplateInput) -> TemplatePlan:
    """Plan `data`'s page in the template `template_id`. Raises `ValueError`
    for an unknown template, page size or orientation, or an empty or
    non-finite extent."""
    from .template_registry import get_template

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
    floor = (
        template.legend_floor(page) if template.legend_floor else MINIMUM_TYPE.legend
    )
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
        legend_min_pt=floor,
    )


def _shown_area(plan: TemplatePlan, data: TemplateInput) -> float:
    """How large the canvas extent comes out in the main frame: the frame's
    area when it keeps the extent's aspect, less the padding otherwise."""
    slot = plan.frames[0]
    box = slot.focus or slot.box
    if data.fill_frame:
        return box.w * box.h
    w, h = _fit(data.extent_width / data.extent_height, box.w, box.h)
    return w * h


# --------------------------------------------------------------------------
# The ten designs
# --------------------------------------------------------------------------


def _classic(page: _Page) -> _Layout:
    """Daylight: the everyday sheet. A title under a short accent mark, the
    map at the canvas's own shape in Whole view, or filling its available
    space in Fill frame, a
    compact legend beside it or under it -- whichever leaves the larger
    map -- and the scale bar and source line along the foot."""
    pal = DAYLIGHT
    sp = page.spacing
    m, g = sp.margin, sp.gutter
    content_w, content_h = page.width - 2 * m, page.height - 2 * m
    # The title runs as wide as the map group, which depends on the room
    # the title leaves: measured at the full width, then at the group's.
    # So does the foot: a row, or stacked under a narrow map.
    head_cap = content_h * _HEADING_MAX_SHARE
    head_h = _Heading(page, content_w, pal, max_height=head_cap).height
    foot_h = _bar_h(page)
    for _ in range(6):
        group = _daylight_group(page, head_h, content_w, content_h, foot_h)
        head = _Heading(page, group[0], pal, max_height=head_cap)
        foot = max(foot_h, _foot_height(page, group[0]))
        if head.height <= head_h + 1e-6 and foot <= foot_h:
            break
        head_h, foot_h = max(head_h, head.height), foot
    group_w, group_h, fw, fh, where, legend_w, legend_h = group
    legend_pt = _legend_pt(page)
    left = m + (content_w - group_w) / 2
    top = m + (content_h - group_h) / 2
    # A map narrower than the legend under it sits centred over it.
    frame_x = left + (group_w - fw) / 2 if where == "below" else left
    frame = Box(frame_x, top + head_h, fw, fh)
    items = head.items(left, top, group_w)
    if where == "right":
        legend_box = Box(frame.x + frame.w + g, frame.y, legend_w, legend_h)
        items += _legend(page, legend_box, pal, legend_pt, z=4)
    elif where == "below":
        legend_box = Box(left, frame.y + frame.h + g, legend_w, legend_h)
        items += _legend(page, legend_box, pal, legend_pt, z=4)
    foot_y = top + group_h - foot_h
    items += _foot(page, Box(left, foot_y, group_w, foot_h), pal, z=5)
    if page.data.crs_is_geographic:
        items.append(_degree_grid(page, frame))
    return (_main(frame, pal),), items, _page_style(pal)


def _daylight_group(
    page: _Page, head_h: float, content_w: float, content_h: float, foot_h: float
) -> tuple[float, float, float, float, str, float, float]:
    """Daylight's map group under a heading `head_h` high, over a foot
    `foot_h` high: its width and height, the frame's, where the legend goes
    ("right", "below" or "") and its box. The legend goes only where all
    its rows fit at the page's legend size (long ones wrapped): beside the
    map when it is no taller than the map, under it when it leaves the map
    half the room; failing both, under it across the page, the map giving
    way."""
    g = page.spacing.gutter
    body_h = content_h - head_h - foot_h - g
    legend_pt = _legend_pt(page)
    rows = _legend_rows(page)

    def frame_size(width: float, height: float) -> tuple[float, float]:
        return (
            (width, height)
            if page.data.fill_frame
            else _fit(page.aspect, width, height)
        )

    options: list[tuple[float, float, str, float, float]] = []
    if rows:
        side_w = min(_legend_width(page, legend_pt), content_w * 0.3)
        fw, fh = frame_size(content_w - side_w - g, body_h)
        side_h = _legend_need(page, legend_pt, side_w)
        if side_h <= fh:
            options.append((fw, fh, "right", side_w, side_h))
        # Under the map, as wide as the map -- or as its own rows need, up
        # to the page's width, under a narrow map; the two settle together.
        natural_w = min(_legend_width(page, legend_pt), content_w)
        below_w = content_w
        for _ in range(6):
            below_h = _legend_need(page, legend_pt, below_w)
            fw, fh = frame_size(content_w, body_h - below_h - g)
            settled = max(fw, natural_w)
            if abs(settled - below_w) < 0.01:
                break
            below_w = settled
        below_h = _legend_need(page, legend_pt, below_w)
        fw, fh = frame_size(content_w, body_h - below_h - g)
        if below_h <= body_h * 0.5 or not options:
            if below_h > body_h * 0.5:
                # Neither fits: across the page under the map, which keeps
                # half the room (`MAP_MIN_SHARE`); entries that still do not
                # fit are left out.
                below_w = content_w
                below_h = min(
                    _legend_need(page, legend_pt, below_w), legend_room_cap(body_h, g)
                )
                fw, fh = frame_size(content_w, body_h - below_h - g)
            options.append((fw, fh, "below", below_w, below_h))
    else:
        fw, fh = frame_size(content_w, body_h)
        options.append((fw, fh, "", 0.0, 0.0))
    # max() keeps the first of equals: a tie goes to the legend beside.
    fw, fh, where, legend_w, legend_h = max(options, key=lambda o: o[0] * o[1])
    group_w = fw + (legend_w + g if where == "right" else 0.0)
    if where == "below":
        group_w = max(fw, legend_w)
    group_h = head_h + fh + (legend_h + g if where == "below" else 0.0) + g + foot_h
    return group_w, group_h, fw, fh, where, legend_w, legend_h


def _legend_below(page: _Page) -> _Layout:
    """Clearline: the map across the whole width and as tall as the room
    allows (the view padded with its surroundings to fill it), a hairline,
    then a band with the legend in as many columns as the width allows and
    the scale bar and source line at its end. Made for wide views and for
    legends too long for a column."""
    pal = DAYLIGHT
    sp = page.spacing
    m, g = sp.margin, sp.gutter
    content_w, content_h = page.width - 2 * m, page.height - 2 * m
    head = _Heading(page, content_w - 2 * g, pal, accent=False)
    legend_pt = _legend_pt(page)
    rows = _legend_rows(page)
    side_w = min(60.0 * min(page.scale, 2.0), content_w * 0.34)
    # A legend too long for the band beside the scale bar takes the band's
    # whole width, the scale bar and source line in a row under it.
    full_width = False
    if rows:
        legend_w = content_w - side_w - 2 * g
        legend_h = _legend_need(page, legend_pt, legend_w)
        side_h = _stacked_foot_height(page, side_w)
        band_h = max(legend_h, side_h)
        if band_h > content_h * 0.4:
            wide_h = _legend_need(page, legend_pt, content_w)
            foot_h = _foot_height(page, content_w)
            if wide_h + g + foot_h < band_h:
                full_width = True
                legend_w, legend_h = content_w, wide_h
                band_h = wide_h + g + foot_h
        # The map keeps half the room it shares with the legend
        # (`MAP_MIN_SHARE`); entries that still do not fit are left out.
        shared = content_h - head.height - 2 * g - sp.rule
        if full_width:
            foot_h = _foot_height(page, content_w)
            band_h = min(band_h, legend_room_cap(shared - g - foot_h) + g + foot_h)
        else:
            band_h = min(band_h, legend_room_cap(shared))
    else:
        legend_w = 0.0
        band_h = _foot_height(page, content_w)
    rule_h = sp.rule
    room_h = content_h - head.height - band_h - 2 * g - rule_h
    # Across the whole width, as tall as the room allows but never taller
    # than a band: the view is padded with its surroundings to fill it.
    frame_h = min(room_h, content_w / (1.25 if page.landscape else 0.8))
    top = m + (room_h - frame_h) / 2
    frame = Box(m, top + head.height, content_w, frame_h)
    items = head.items(m + g, top + g / 4, content_w - 2 * g)
    # A quiet full-width masthead distinguishes this legend-led sheet from
    # Daylight's freestanding heading. These are ordinary editable items.
    if page.title:
        masthead = Box(m, top, content_w, max(head.height - g / 2, 1.0))
        items.insert(0, _solid("panel-masthead", masthead, pal.band, z=1))
        items = [
            replace(item, color=pal.band_ink)
            if isinstance(item, TextSpec) and item.id == "title"
            else item
            for item in items
        ]
    rule_y = frame.y + frame.h + g
    items.append(_solid("rule", Box(m, rule_y, content_w, rule_h), pal.hairline, z=3))
    band = Box(m, rule_y + rule_h + g, content_w, band_h)
    if full_width:
        foot_h = _foot_height(page, content_w)
        legend_h = band.h - g - foot_h
        items += _legend(
            page, Box(band.x, band.y, band.w, legend_h), pal, legend_pt, z=4
        )
        foot = Box(band.x, band.y + legend_h + g, band.w, foot_h)
        items += _foot(page, foot, pal, z=5)
    elif rows:
        items += _legend(
            page, Box(band.x, band.y, legend_w, band.h), pal, legend_pt, z=4
        )
        side = Box(band.x + band.w - side_w, band.y, side_w, band.h)
        items += _stacked_foot(page, side, pal, z=5)
    else:
        items += _foot(page, band, pal, z=5)
    if page.data.crs_is_geographic:
        items.append(_degree_grid(page, frame))
    return (_main(frame, pal),), items, _page_style(pal)


def _map_only(page: _Page) -> _Layout:
    """Bare Map: the map and nothing else, for placing in another document
    -- but the data credits stay, because the licences most basemaps and
    open data come under require them. No frame line: the page edge is the
    map's edge."""
    pal = DAYLIGHT
    sp = page.spacing
    m, g = sp.margin, sp.gutter
    content_w = page.width - 2 * m
    credit_pt = page.pt("credit")
    north_w = _NORTH_W if page.rotated else 0.0
    credits_w = content_w - (north_w + g if north_w else 0.0)
    credits_h = text_height(page.credits, credits_w, credit_pt)
    foot_h = max(credits_h, _NORTH_H if north_w else 0.0)
    bottom = page.height - m - (foot_h + g / 2 if foot_h else 0.0)
    frame = Box(m, m, content_w, bottom - m)
    items: list[CartographItem] = []
    foot_y = frame.y + frame.h + g / 2
    if north_w:
        items.append(_north(Box(m, foot_y, north_w, _NORTH_H), pal, z=2))
    credit = source_line(
        page.credits,
        Box(m + content_w - credits_w, foot_y, credits_w, credits_h or 1.0),
        palette=pal,
        font_size=credit_pt,
        align="end",
    )
    if credit is not None:
        items.append(credit)
    return (
        (FrameSlot(MAIN_FRAME_ID, frame, frame_bg=pal.page, frame_stroke=_NO_STROKE),),
        items,
        _page_style(pal),
    )


def _report_figure(page: _Page) -> _Layout:
    """Caption: a figure for a report. The map at the canvas's own shape
    in Whole view, or filling its available space in Fill frame,
    and under it a caption -- the title as its lead, the project's notes as
    its text, at a reading measure -- with the scale bar and source line
    beside it, or under it on a narrow page. No legend: the report's text
    says what the map shows."""
    pal = DAYLIGHT
    sp = page.spacing
    m, g = sp.margin, sp.gutter
    content_w, content_h = page.width - 2 * m, page.height - 2 * m

    def frame_size(height: float) -> tuple[float, float]:
        return (
            (content_w, height)
            if page.data.fill_frame
            else _fit(page.aspect, content_w, height)
        )

    measure = min(150.0 * page.scale, content_w)
    # The caption is as wide as the map -- whose size depends on the room
    # the caption leaves, so narrowed until the two agree -- but never
    # narrower than a readable column under a slim map.
    least = min(content_w, 110.0 * max(page.scale, 1.0))
    caption_w = content_w
    for _ in range(8):
        zone = _Caption(page, caption_w, measure, content_h)
        fw, fh = frame_size(content_h - zone.height - g)
        settled = abs(max(fw, least) - caption_w) < 0.01
        caption_w = max(fw, least)
        if settled:
            break
    zone = _Caption(page, caption_w, measure, content_h)
    fw, fh = frame_size(content_h - zone.height - g)
    left = m + (content_w - fw) / 2
    top = m + (content_h - fh - g - zone.height) / 2
    frame = Box(left, top, fw, fh)
    caption_x = m + (content_w - caption_w) / 2 if caption_w > fw else left
    items = zone.items(Box(caption_x, top + fh + g, caption_w, zone.height))
    if page.data.crs_is_geographic:
        items.append(_degree_grid(page, frame))
    return (_main(frame, pal),), items, _page_style(pal)


def _field_map(page: _Page) -> _Layout:
    """Fieldbook: a survey sheet. The map under a grid labelled in its own
    units, a neat line round it, and a ruled title block -- title, then
    scale, CRS and date, then north arrow and scale bar -- read before the
    legend: in a column at the right of the map or a band under it,
    whichever shows the canvas view larger. A landscape sheet tries the
    column first and a portrait one the band, so a tie keeps that order; a
    wide view on a landscape sheet takes the band, rather than a near-square
    frame that pads the view out with half again its height."""
    pal = PAPER
    data = page.data
    grid_crs = None if data.crs_is_geographic else (data.crs_authid or None)
    interval = nice_interval(min(data.extent_width, data.extent_height) / 4)
    side_room, end_room = _grid_label_room(page, interval)
    block = _TitleBlock(page, pal)
    legend_pt = _legend_pt(page)
    arrangements = [_field_column, _field_band]
    if not page.landscape:
        arrangements.reverse()
    aspect = data.extent_width / data.extent_height

    def shown(arrangement: tuple[Box, Box, Box]) -> float:
        map_area = arrangement[0]
        w, h = _fit(aspect, map_area.w - 2 * side_room, map_area.h - 2 * end_room)
        return w * h

    # max() keeps the first of equals: the orientation's own arrangement.
    map_area, block_box, legend_box = max(
        (arrange(page, block, legend_pt, end_room) for arrange in arrangements),
        key=shown,
    )
    frame = Box(
        map_area.x + side_room,
        map_area.y + end_room,
        map_area.w - 2 * side_room,
        map_area.h - 2 * end_room,
    )
    interval = _grid_interval(page, frame, interval, side_room)
    items: list[CartographItem] = [
        GraticuleSpec(
            id="graticule",
            box=frame,
            for_=MAIN_FRAME_ID,
            crs="EPSG:4326" if data.crs_is_geographic else grid_crs,
            crs_def=None if data.crs_is_geographic else data.crs_def,
            interval=interval,
            labels="outside",
            color="#657064",
            z=1,
        ),
    ]
    items += block.items(block_box)
    items += _legend(page, legend_box, pal, legend_pt, z=12)
    return (
        (FrameSlot(MAIN_FRAME_ID, frame, frame_bg=pal.panel, frame_stroke=pal.rule),),
        items,
        _page_style(pal),
    )


def _field_column(
    page: _Page, block: _TitleBlock, legend_pt: float, end_room: float
) -> tuple[Box, Box, Box]:
    """Fieldbook's map area, title block and legend with the block and
    legend in a column at the right."""
    m, g = page.spacing.margin, page.spacing.gutter
    content = Box(m, m, page.width - 2 * m, page.height - 2 * m)
    column_w = min(max(content.w * 0.27, 70.0), 95.0 * page.scale)
    map_area = Box(content.x, content.y, content.w - column_w - g, content.h)
    column = Box(map_area.x + map_area.w + g, content.y, column_w, content.h)
    block_box = Box(column.x, column.y, column.w, block.height(column.w))
    legend_box = Box(
        column.x,
        block_box.y + block_box.h + g,
        column.w,
        column.h - block_box.h - g,
    )
    if _legend_need(page, legend_pt, column.w) > legend_box.h:
        # A legend longer than the column under the block: a strip
        # across the whole sheet, under the map and the block, the map
        # keeping half the sheet's height (`MAP_MIN_SHARE`).
        strip_h = min(
            _legend_need(page, legend_pt, content.w),
            legend_room_cap(content.h - 2 * end_room, g),
            content.h - block_box.h - g,
        )
        map_area = Box(map_area.x, map_area.y, map_area.w, content.h - strip_h - g)
        legend_box = Box(content.x, map_area.y + map_area.h + g, content.w, strip_h)
    return map_area, block_box, legend_box


def _field_band(
    page: _Page, block: _TitleBlock, legend_pt: float, end_room: float
) -> tuple[Box, Box, Box]:
    """Fieldbook's map area, title block and legend with the block and
    legend in a band under the map."""
    m, g = page.spacing.margin, page.spacing.gutter
    content = Box(m, m, page.width - 2 * m, page.height - 2 * m)
    block_w = min(max(content.w * 0.5, 80.0), content.w)
    block_h = block.height(block_w)
    # The legend beside the block, as tall as it needs; or, when that
    # makes the band taller, across the sheet under the block.
    beside_w = content.w - block_w - g
    under, band_h = _band_layout(
        page, legend_pt, block_h, beside_w, content.w, content.h - 2 * end_room
    )
    if not page.data.layer_names:
        band_h = block_h
    map_area = Box(content.x, content.y, content.w, content.h - band_h - g)
    band_y = map_area.y + map_area.h + g
    block_box = Box(content.x, band_y, block_w, block_h)
    if under:
        legend_box = Box(
            content.x, band_y + block_h + g, content.w, band_h - block_h - g
        )
    else:
        legend_box = Box(content.x + block_w + g, band_y, beside_w, band_h)
    return map_area, block_box, legend_box


def _poster(page: _Page) -> _Layout:
    """Broadside: a public poster. A headline as large as the title allows
    -- a long one steps down in size and runs to more lines rather than
    clip -- a short accent rule, the map dominant, and a disciplined rail
    (a footer band on a portrait sheet, or under a wide view) with the
    story, the legend and the scale bar. The source line runs along the
    foot."""
    headline_floor = max(MINIMUM_TYPE.display, page.pt("title"))
    # The headline steps down, toward the page's title size, only when the
    # legend would otherwise be left short: the poster keeps its big
    # headline for a map with a short legend, and a crowded legend gets
    # the height instead of losing entries.
    largest = page.pt("display")
    sizes = [largest, (largest + headline_floor) / 2, headline_floor]
    for display_max in sizes[:-1]:
        layout, holds = _poster_page(page, display_max, headline_floor)
        if holds:
            return layout
    return _poster_page(page, sizes[-1], headline_floor)[0]


def _poster_page(
    page: _Page, display_max: float, headline_floor: float
) -> tuple[_Layout, bool]:
    """Broadside's page with the headline at most `display_max` pt, and
    whether its legend fits the rail or band planned for it."""
    pal = PAPER
    sp = page.spacing
    s = page.scale
    m, g = sp.margin * 1.2, sp.gutter
    content_w = page.width - 2 * m
    headline_pt, headline_h = _fit_title(
        page.title,
        content_w,
        display_max,
        headline_floor,
        max_lines=2,
    )
    items: list[CartographItem] = []
    y = m
    if page.title:
        items.append(_title(Box(m, y, content_w, headline_h), headline_pt, pal, z=1))
        y += headline_h + g * 0.75
        items.append(
            _solid(
                "rule",
                Box(m, y, min(30.0 * s, content_w), max(1.0 * s, 0.8)),
                pal.accent,
                z=2,
            )
        )
        y += max(1.0 * s, 0.8) + g * 1.5
    credit_pt = page.pt("credit")
    credits_h = text_height(page.credits, content_w, credit_pt)
    bottom = page.height - m - (credits_h + g if credits_h else 0.0)
    body = Box(m, y, content_w, bottom - y)
    story_pt = round(page.pt("body") * 1.15, 2)
    has_rail = bool(page.notes or page.data.layer_names)
    holds = True
    if not has_rail:
        frame = Box(body.x, body.y, body.w, body.h - _bar_h(page) - g)
        items += _foot_row(
            page,
            Box(body.x, frame.y + frame.h + g, body.w, _bar_h(page)),
            pal,
            z=3,
            credits=False,
        )
    else:

        def beside() -> tuple[Box, list[CartographItem], bool]:
            rail_w = min(max(body.w * 0.27, 60.0 * s), 110.0 * s)
            # A legend too long for the rail at the page's legend size
            # widens it, up to as wide as the map (`MAP_MIN_SHARE`).
            widest = max(legend_room_cap(body.w, 1.5 * g), rail_w)
            while rail_w < widest and not _rail_holds_legend(
                page, rail_w, body.h, story_pt
            ):
                rail_w = min(rail_w + 5.0, widest)
            frame = Box(body.x, body.y, body.w - rail_w - 1.5 * g, body.h)
            rail = Box(frame.x + frame.w + 1.5 * g, body.y, rail_w, body.h)
            fits = _rail_holds_legend(
                page, rail_w, body.h, story_pt, MINIMUM_TYPE.legend
            )
            return frame, _rail(page, rail, pal, story_pt, z=3), fits

        def under() -> tuple[Box, list[CartographItem], bool]:
            band = _PosterBand(page, body, story_pt)
            frame = Box(body.x, body.y, body.w, body.h - band.height - 1.5 * g)
            band_box = Box(body.x, frame.y + frame.h + 1.5 * g, body.w, band.height)
            return frame, band.items(band_box, pal, z=3), True

        # The rail beside the map on a landscape sheet and the band under it
        # on a portrait one, unless the other shows the canvas view larger:
        # a wide view on a landscape sheet takes the band. max() keeps the
        # first of equals, the orientation's own.
        arrangements = [beside, under] if page.landscape else [under, beside]
        aspect = page.data.extent_width / page.data.extent_height
        frame, rail_items, holds = max(
            (arrange() for arrange in arrangements),
            key=lambda option: _fit_area(aspect, option[0]),
        )
        items += rail_items
    credit = source_line(
        page.credits,
        Box(m, page.height - m - credits_h, content_w, credits_h or 1.0),
        palette=pal,
        font_size=credit_pt,
    )
    if credit is not None:
        items.append(credit)
    return ((_main(frame, pal, stroke=_NO_STROKE),), items, _page_style(pal)), holds


def _main_and_locator(page: _Page) -> _Layout:
    """Near & Far: the map, and beside it (under it on a portrait sheet, or
    under a wide view) a locator showing where it is, its extent outlined,
    above the legend; title over both, scale bar and source line at the
    foot."""
    pal = DAYLIGHT
    sp = page.spacing
    s = page.scale
    m, g = sp.margin, sp.gutter
    content_w, content_h = page.width - 2 * m, page.height - 2 * m
    head = _Heading(page, content_w, pal)
    legend_pt = _legend_pt(page)
    body = Box(m, m + head.height, content_w, content_h - head.height)
    items = head.items(m, m, content_w)

    def column() -> tuple[Box, Box, Box, Box]:
        """The locator, legend and scale bar in a column right of the map."""
        column_w = min(max(body.w * 0.26, 55.0 * s), 95.0 * s)
        # A legend longer than the room under the locator first shortens
        # the locator (to a wide strip), then widens the column, up to 38 %
        # of the sheet; failing that, it goes under the map.
        while True:
            column = Box(body.x + body.w - column_w, body.y, column_w, body.h)
            foot_h = _stacked_foot_height(page, column.w)
            room = column.h - foot_h - 2 * g
            need = _legend_need(page, legend_pt, column.w)
            side = min(column.w, column.h * 0.45)
            side = max(min(side, room - need), min(column.w * 0.6, column.h * 0.3))
            if need <= room - side or column_w >= body.w * 0.38:
                break
            column_w = min(column_w + 5.0, body.w * 0.38)
        frame = Box(body.x, body.y, body.w - column_w - g, body.h)
        locator = Box(column.x, column.y, column.w, side)
        legend_box = Box(column.x, locator.y + locator.h + g, column.w, room - side)
        if page.data.layer_names and need > room - side:
            # The map keeps half the body's height (`MAP_MIN_SHARE`).
            strip_h = min(
                _legend_need(page, legend_pt, frame.w), legend_room_cap(body.h, g)
            )
            frame = Box(frame.x, frame.y, frame.w, body.h - strip_h - g)
            legend_box = Box(frame.x, frame.y + frame.h + g, frame.w, strip_h)
            locator = Box(column.x, column.y, column.w, min(column.w, room))
        foot = Box(column.x, column.y + column.h - foot_h, column.w, foot_h)
        return frame, locator, legend_box, foot

    def band() -> tuple[Box, Box, Box, Box]:
        """The locator, legend and scale bar in a band under the map."""
        foot_w = min(60.0 * min(s, 2.0), body.w * 0.34)
        foot_h = _stacked_foot_height(page, foot_w)
        base_h = max(min(body.w * 0.3, body.h * 0.3), foot_h)
        locator_w = min(base_h * 1.25, body.w * 0.36)
        # The legend between the locator and the scale bar, the band as
        # tall as it needs; or, when that makes the band taller, across the
        # sheet under the two.
        beside_w = body.w - locator_w - foot_w - 2 * g
        under, band_h = _band_layout(page, legend_pt, base_h, beside_w, body.w, body.h)
        if not page.data.layer_names:
            band_h = base_h
        frame = Box(body.x, body.y, body.w, body.h - band_h - g)
        band_y = frame.y + frame.h + g
        locator = Box(body.x, band_y, locator_w, base_h)
        if under:
            legend_box = Box(body.x, band_y + base_h + g, body.w, band_h - base_h - g)
            foot_y = band_y + base_h - foot_h
        else:
            legend_box = Box(locator.x + locator_w + g, band_y, beside_w, band_h)
            foot_y = band_y + band_h - foot_h
        foot = Box(body.x + body.w - foot_w, foot_y, foot_w, foot_h)
        return frame, locator, legend_box, foot

    # The column on a landscape sheet and the band on a portrait one, unless
    # the other shows the canvas view larger: a wide view on a landscape
    # sheet takes the band. max() keeps the first of equals.
    arrangements = [column, band] if page.landscape else [band, column]
    aspect = page.data.extent_width / page.data.extent_height
    frame, locator, legend_box, foot = max(
        (arrange() for arrange in arrangements),
        key=lambda option: _fit_area(aspect, option[0]),
    )
    items += _stacked_foot(page, foot, pal, z=6)
    items += _legend(page, legend_box, pal, legend_pt, z=5)
    return (
        (
            _main(frame, pal),
            FrameSlot(
                LOCATOR_FRAME_ID,
                locator,
                overview_of=MAIN_FRAME_ID,
                zoom_out=LOCATOR_ZOOM_OUT,
                frame_bg=pal.page,
                frame_stroke=pal.muted,
            ),
        ),
        items,
        _page_style(pal),
    )


def _story_map(page: _Page) -> _Layout:
    """On the Map: the map fills the sheet and the story is laid over it --
    the title and the project's notes on an opaque panel, a locator inset
    and the scale bar on its own plate -- with the canvas view fitted into
    the clear part of the frame, so the panels cover only its surroundings.
    The source line sits on the paper under the map."""
    pal = PAPER
    sp = page.spacing
    s = page.scale
    m, g = sp.margin, sp.gutter
    credit_pt = page.pt("credit")
    content_w = page.width - 2 * m
    credits_h = text_height(page.credits, content_w, credit_pt)
    frame = Box(
        m, m, content_w, page.height - 2 * m - (credits_h + g / 2 if credits_h else 0.0)
    )
    inset = g
    pad = sp.pad * 1.5
    if page.landscape:
        panel_w = min(max(frame.w * 0.3, 70.0 * s), 115.0 * s, frame.w * 0.45)
    else:
        panel_w = min(max(frame.w * 0.55, 80.0 * s), 125.0 * s, frame.w - 2 * inset)
    inner_w = panel_w - 2 * pad
    title_pt, title_h = _fit_title(
        page.title,
        inner_w,
        round(page.pt("title") * 1.2, 2),
        MINIMUM_TYPE.title,
        max_lines=3,
    )
    notes_pt, notes_h = _fit_body(
        page.notes, inner_w, page.pt("body"), MINIMUM_TYPE.body, frame.h * 0.42
    )
    items: list[CartographItem] = []
    overlays: list[Box] = []
    text_h = title_h + (g / 2 if title_h and notes_h else 0.0) + notes_h
    if text_h:
        panel = Box(frame.x + inset, frame.y + inset, panel_w, text_h + 2 * pad)
        overlays.append(panel)
        items.append(_solid("panel-story", panel, pal.panel, z=1))
        y = panel.y + pad
        if title_h:
            items.append(
                text_on_panel(
                    "title",
                    Box(panel.x + pad, y, inner_w, title_h),
                    "{{title}}",
                    palette=pal,
                    font_size=title_pt,
                    weight="bold",
                    kind="title",
                    z=2,
                )
            )
            y += title_h + g / 2
        if notes_h:
            items.append(
                text_on_panel(
                    "notes",
                    Box(panel.x + pad, y, inner_w, notes_h),
                    "{{notes}}",
                    palette=pal,
                    font_size=notes_pt,
                    z=3,
                )
            )
    locator_side = min(frame.w, frame.h) * 0.22
    locator = Box(
        frame.x + frame.w - inset - locator_side,
        frame.y + frame.h - inset - locator_side,
        locator_side,
        locator_side,
    )
    overlays.append(locator)
    plate_w = min(_scale_bar_w(page) + 2 * sp.pad, frame.w * 0.45)
    plate = Box(
        frame.x + inset,
        frame.y + frame.h - inset - _bar_h(page) - 2 * sp.pad,
        plate_w,
        _bar_h(page) + 2 * sp.pad,
    )
    overlays.append(plate)
    items.append(_solid("panel-scale", plate, pal.panel, z=4))
    items.append(_scale_bar(_inset(plate, sp.pad), pal, font_pt=scalebar_pt(page), z=5))
    if page.rotated:
        north = Box(
            frame.x + frame.w - inset - _NORTH_W, frame.y + inset, _NORTH_W, _NORTH_H
        )
        overlays.append(north)
        items.append(_solid("panel-north", north, pal.panel, z=6))
        items.append(_north(north, pal, z=7))
    credit = source_line(
        page.credits,
        Box(m, frame.y + frame.h + g / 2, content_w, credits_h or 1.0),
        palette=pal,
        font_size=credit_pt,
        align="end",
    )
    if credit is not None:
        items.append(credit)
    focus = clear_area(frame, overlays, page.aspect, gap=inset)
    return (
        (
            FrameSlot(
                MAIN_FRAME_ID,
                frame,
                frame_bg=pal.panel,
                frame_stroke=_NO_STROKE,
                focus=focus,
            ),
            FrameSlot(
                LOCATOR_FRAME_ID,
                locator,
                overview_of=MAIN_FRAME_ID,
                zoom_out=LOCATOR_ZOOM_OUT,
                frame_bg=pal.panel,
                frame_stroke=pal.rule,
            ),
        ),
        items,
        _page_style(pal),
    )


def _dashboard(page: _Page) -> _Layout:
    """Signal: a data-led one-pager. Up to three key figures the user gave
    -- each a large value over what it counts -- beside the map (above it
    on a portrait sheet, or over a wide view), with the project's notes,
    the legend and the scale bar. Without figures it becomes a map-and-note
    page: no empty or stand-in tile is ever drawn, and no number is ever
    made up."""
    pal = DAYLIGHT
    sp = page.spacing
    s = page.scale
    m, g = sp.margin, sp.gutter
    content_w, content_h = page.width - 2 * m, page.height - 2 * m
    head = _Heading(page, content_w, pal)
    figures = page.figures
    items = head.items(m, m, content_w)
    body = Box(m, m + head.height, content_w, content_h - head.height)
    legend_pt = _legend_pt(page)
    note_pt = page.pt("body")
    if not (figures or page.notes or page.data.layer_names):
        # Nothing for a column to say: the map takes the page, the scale
        # bar and source line in a row under it.
        foot_h = _foot_height(page, body.w)
        frame = Box(body.x, body.y, body.w, body.h - foot_h - g)
        items += _foot(
            page, Box(body.x, frame.y + frame.h + g, body.w, foot_h), pal, z=10
        )
    else:

        def column() -> tuple[Box, list[CartographItem]]:
            """The figures, notes, legend and scale bar in a column right of the map."""
            items: list[CartographItem] = []
            column_w = min(max(body.w * 0.28, 62.0 * s), 105.0 * s)
            # A legend with too little room under the figures and notes widens
            # the column, up to as wide as the map (`MAP_MIN_SHARE`).
            widest = max(legend_room_cap(body.w, 1.5 * g), column_w)
            while True:
                column = Box(body.x + body.w - column_w, body.y, column_w, body.h)
                y = column.y
                figure_items: list[CartographItem] = []
                if figures:
                    # With a substantial key, three stacked values consume
                    # the room needed to explain the map. A wide enough
                    # column can keep the figures in a single readable row.
                    if _legend_rows(page) > 12 and len(figures) > 1:
                        figure_items, y = _figure_row(page, figures, column, pal)
                        y += g * 1.5
                    else:
                        figure_items, y = _figure_stack(page, figures, column, pal)
                foot_h = _stacked_foot_height(page, column.w)
                room = column.y + column.h - foot_h - g - y
                notes_pt, notes_h = _fit_body(
                    page.notes,
                    column.w,
                    note_pt,
                    MINIMUM_TYPE.body,
                    room * (0.5 if page.data.layer_names else 1.0),
                )
                legend_room = room - (notes_h + g if notes_h else 0.0)
                if (
                    _legend_need(page, legend_pt, column.w) <= legend_room
                    or column_w >= widest
                ):
                    break
                column_w = min(column_w + 5.0, widest)
            frame = Box(body.x, body.y, body.w - column_w - 1.5 * g, body.h)
            items += figure_items
            if notes_h:
                items.append(
                    _notes(Box(column.x, y, column.w, notes_h), notes_pt, pal, z=8)
                )
                y += notes_h + g
            items += _legend(
                page,
                Box(column.x, y, column.w, column.y + column.h - foot_h - g - y),
                pal,
                legend_pt,
                z=9,
            )
            items += _stacked_foot(
                page,
                Box(column.x, column.y + column.h - foot_h, column.w, foot_h),
                pal,
                z=10,
            )
            return frame, items

        def band() -> tuple[Box, list[CartographItem]]:
            """The figures over the map, the notes and legend in a band under it."""
            items: list[CartographItem] = []
            y = body.y
            if figures:
                figure_items, y = _figure_row(
                    page, figures, Box(body.x, y, body.w, 1.0), pal
                )
                items += figure_items
                y += g
            foot_h = _foot_height(page, body.w)
            band_w = body.w
            rows = _legend_rows(page)
            legend_h = 0.0
            # The notes give the legend more of the band's width when it needs
            # it to fit.
            for share in (0.56, 0.45, 0.35) if rows else (1.0,):
                notes_w = band_w * share
                notes_pt, notes_h = _fit_body(
                    page.notes, notes_w, note_pt, MINIMUM_TYPE.body, body.h * 0.22
                )
                if rows:
                    legend_w = band_w - (notes_w + g if notes_h else 0.0)
                    legend_h = _legend_need(page, legend_pt, legend_w)
                if not notes_h or legend_h <= body.h * 0.25:
                    break
            # The map keeps half the height it shares with the band
            # (`MAP_MIN_SHARE`).
            legend_h = min(
                legend_h, legend_room_cap(body.y + body.h - y - foot_h - g, g)
            )
            band_h = max(notes_h, legend_h)
            frame_h = body.y + body.h - y - foot_h - g - (band_h + g if band_h else 0.0)
            frame = Box(body.x, y, body.w, frame_h)
            band_y = frame.y + frame.h + g
            if notes_h:
                items.append(
                    _notes(Box(body.x, band_y, notes_w, notes_h), notes_pt, pal, z=8)
                )
            if legend_h:
                legend_x = body.x + (notes_w + g if notes_h else 0.0)
                items += _legend(
                    page,
                    Box(legend_x, band_y, body.x + body.w - legend_x, band_h),
                    pal,
                    legend_pt,
                    z=9,
                )
            items += _foot(
                page, Box(body.x, body.y + body.h - foot_h, body.w, foot_h), pal, z=10
            )
            return frame, items

        def legend_column() -> tuple[Box, list[CartographItem]]:
            """Give a substantial key its own full-height column.

            Figures and notes share the map's column instead of squeezing
            every class into the space below three large numbers.
            """
            arranged: list[CartographItem] = []
            foot_h = _foot_height(page, body.w)
            available_h = body.h - foot_h - g
            key_w = min(
                max(_legend_width(page, legend_pt), body.w * 0.28), body.w * 0.4
            )
            key = Box(body.x + body.w - key_w, body.y, key_w, available_h)
            left_w = body.w - key_w - 1.5 * g
            y = body.y
            if figures:
                figure_items, y = _figure_row(
                    page, figures, Box(body.x, y, left_w, 1.0), pal
                )
                arranged += figure_items
                y += g
            notes_pt, notes_h = _fit_body(
                page.notes,
                left_w,
                note_pt,
                MINIMUM_TYPE.body,
                available_h * 0.3,
            )
            map_h = body.y + available_h - y - (notes_h + g if notes_h else 0.0)
            frame = Box(body.x, y, left_w, map_h)
            if notes_h:
                arranged.append(
                    _notes(
                        Box(body.x, frame.y + frame.h + g, left_w, notes_h),
                        notes_pt,
                        pal,
                        z=8,
                    )
                )
            arranged += _legend(page, key, pal, legend_pt, z=9)
            arranged += _foot(
                page, Box(body.x, body.y + body.h - foot_h, body.w, foot_h), pal, z=10
            )
            return frame, arranged

        # Keep the whole key readable before maximising the map. Stacking
        # figures and prose above a dense key can otherwise win on map area
        # while losing most of the classes that explain it.
        arrangements = [column, band] if page.landscape else [band, column]
        if _legend_rows(page) > 12:
            arrangements.append(legend_column)
        aspect = page.data.extent_width / page.data.extent_height

        def content_score(option: tuple[Box, list[CartographItem]]) -> tuple:
            key = next((i for i in option[1] if isinstance(i, LegendSpec)), None)
            if key is None:
                return (True, legend_pt, _fit_area(aspect, option[0]))
            _, _, need = _legend_fit(
                page, cast(float, key.font_size), key.box.w, key.box.h
            )
            complete = need <= key.box.h + 1e-6
            readability = key.font_size if complete else key.box.h / max(need, 1.0)
            return (complete, readability, _fit_area(aspect, option[0]))

        frame, arranged = max(
            (arrange() for arrange in arrangements),
            key=content_score,
        )
        items += arranged
    return (_main(frame, pal),), items, _page_style(pal)


def _dark(page: _Page) -> _Layout:
    """After Hours: a dark sheet with an uninterrupted map.

    The heading, legend and foot get their own space. A dense legend belongs
    beside or below the map, never on top of terrain. Choose the arrangement
    that keeps the complete key readable and shows the largest canvas view.
    """
    pal = GRAPHITE
    sp = page.spacing
    m, g = max(6.0, sp.margin * 0.6), sp.gutter
    width = page.width - 2 * m
    pad = sp.pad
    title_pt, title_h = _fit_title(
        page.title,
        width - 2 * pad,
        round(page.pt("title") * 1.2, 2),
        MINIMUM_TYPE.title,
        max_lines=3,
    )
    items: list[CartographItem] = []
    head_h = title_h + 2 * pad if title_h else 0.0
    if head_h:
        panel = Box(m, m, width, head_h)
        items.append(_solid("panel-title", panel, pal.panel, z=1))
        items.append(
            text_on_panel(
                "title",
                _inset(panel, pad),
                "{{title}}",
                palette=pal,
                font_size=title_pt,
                weight="bold",
                kind="title",
                z=2,
            )
        )
    foot_h = _foot_height(page, width)
    body_y = m + head_h + (g if head_h else 0.0)
    foot_y = page.height - m - foot_h
    body = Box(m, body_y, width, foot_y - g - body_y)
    frame = body
    legend_pt = _legend_pt(page)
    if _legend_rows(page):
        options: list[tuple[Box, Box]] = []
        # More room for a dense key comes from columns, not map overlays.
        for share in (0.25, 0.32, 0.4):
            rail_w = body.w * share
            options.append(
                (
                    Box(body.x, body.y, body.w - rail_w - g, body.h),
                    Box(body.x + body.w - rail_w, body.y, rail_w, body.h),
                )
            )
        key_h = min(_legend_need(page, legend_pt, body.w), body.h * 0.4)
        options.append(
            (
                Box(body.x, body.y, body.w, body.h - key_h - g),
                Box(body.x, body.y + body.h - key_h, body.w, key_h),
            )
        )

        def score(option: tuple[Box, Box]) -> tuple:
            map_box, key_box = option
            size, _, _ = _legend_plan(page, legend_pt, key_box)
            _, _, need = _legend_fit(page, size, key_box.w, key_box.h)
            return (
                need <= key_box.h + 1e-6,
                size,
                _fit_area(page.aspect, map_box),
            )

        frame, legend_box = max(options, key=score)
        items += [
            replace(item, bg=pal.panel)
            for item in _legend(page, legend_box, pal, legend_pt, z=3)
        ]
    items += _foot(page, Box(m, foot_y, width, foot_h), pal, z=5)
    return (
        (FrameSlot(MAIN_FRAME_ID, frame, frame_bg=pal.page, frame_stroke=pal.page),),
        items,
        _page_style(pal),
    )


BASE_TEMPLATES: tuple[LayoutTemplate, ...] = (
    LayoutTemplate(
        "classic",
        N_("Daylight"),
        N_("A balanced everyday sheet: title, large map, legend, scale and source."),
        _classic,
        category=ESSENTIAL,
        best_for=N_("Best for: everyday maps, reports and emails."),
        includes=(N_("legend"), N_("scale bar"), N_("source line")),
    ),
    LayoutTemplate(
        "legend-below",
        N_("Clearline"),
        N_("A wide map with the legend in columns below: for long legends."),
        _legend_below,
        category=ESSENTIAL,
        best_for=N_("Best for: wide maps with long legends."),
        includes=(N_("legend band"), N_("scale bar"), N_("source line")),
    ),
    LayoutTemplate(
        "map-only",
        N_("Bare Map"),
        N_("Just the map and its data credits, for placing in another document."),
        _map_only,
        category=ESSENTIAL,
        best_for=N_("Best for: pasting into a report, slide or web page."),
        includes=(N_("source line"),),
    ),
    LayoutTemplate(
        "report-figure",
        N_("Caption", "layout template name: a report figure, caption under the map"),
        N_("A report figure: the map with a caption, scale bar and source under it."),
        _report_figure,
        category=ESSENTIAL,
        best_for=N_("Best for: reports and papers that caption their figures."),
        includes=(N_("caption"), N_("scale bar"), N_("source line")),
    ),
    LayoutTemplate(
        "field-map",
        N_("Fieldbook"),
        N_("A survey sheet with a labelled grid and a title block: scale, CRS, date."),
        _field_map,
        category=CHARACTERFUL,
        best_for=N_("Best for: field surveys and site records."),
        includes=(
            N_("grid"),
            N_("title block"),
            N_("north arrow"),
            N_("scale bar"),
            N_("legend"),
        ),
    ),
    LayoutTemplate(
        "poster",
        N_("Broadside"),
        N_("A bold poster: big headline, dominant map, story and legend in a rail."),
        _poster,
        category=CHARACTERFUL,
        best_for=N_("Best for: posters and wall displays."),
        includes=(N_("headline"), N_("notes panel"), N_("legend"), N_("scale bar")),
    ),
    LayoutTemplate(
        "locator",
        N_("Near & Far"),
        N_("The map with a locator inset that shows where it is."),
        _main_and_locator,
        category=CHARACTERFUL,
        best_for=N_("Best for: maps of a small area that need locating."),
        includes=(N_("locator"), N_("legend"), N_("scale bar"), N_("source line")),
    ),
    LayoutTemplate(
        "story-map",
        N_("On the Map"),
        N_("The map fills the page; the title and story sit on a panel over it."),
        _story_map,
        category=CHARACTERFUL,
        best_for=N_("Best for: presentations and maps with a story."),
        includes=(N_("notes panel"), N_("locator"), N_("scale bar")),
    ),
    LayoutTemplate(
        "dashboard",
        N_("Signal"),
        N_(
            "A data-led page: the map, with its notes, legend and scale bar. "
            "Add Live counts for its key numbers."
        ),
        _dashboard,
        category=CHARACTERFUL,
        best_for=N_("Best for: data summaries and briefings with key statistics."),
        includes=(N_("notes"), N_("legend"), N_("scale bar")),
    ),
    LayoutTemplate(
        "dark",
        N_("After Hours"),
        N_("A dark page with the map to the edges and text on dark panels."),
        _dark,
        category=ESSENTIAL,
        best_for=N_("Best for: dark-room screens and projected displays."),
        includes=(N_("legend"), N_("scale bar"), N_("source line")),
    ),
)


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


#: A scale bar's least box height: QGIS's bar under labels up to 12 pt
#: needs about this much, so the built bar keeps the planned box (`_bar_h`
#: gives a big sheet's larger labels more).
_SCALEBAR_H = 14.0
#: The largest a scale bar's labels grow on a big sheet, as a legend's.
_SCALEBAR_MAX_PT = 18.0
_NORTH_W = 8.0
_NORTH_H = 12.0
#: A frame line drawn in no colour: the runtime draws a frame edge unless
#: the design says otherwise, and this is "otherwise".
_NO_STROKE = "#00000000"
#: Room left under an estimated text height, for descenders and rounding.
_TEXT_SLACK = 0.6
#: QGIS's legend metrics at its defaults (`canvas_cartograph` scales them
#: down only when the rows do not fit): symbol width, the space between
#: rows, and the legend's own inner margin.
_LEGEND_SYMBOL_W = 7.0
_LEGEND_SYMBOL_H = 4.0
_LEGEND_ROW_GAP = 2.5
#: What a legend measures in QGIS beyond its rows (its margins, the space
#: an empty title still keeps).
_LEGEND_FRAME_H = 6.0
_LEGEND_BOX_SPACE = 2.0
#: More of QGIS's legend metrics (`QgsLegendRenderer.minimumSize` at 6.5 to
#: 25 pt): the space above a class list's heading, the gap between a
#: symbol and its label, and a row's text in ems -- its first line, and
#: each line it wraps onto after that.
_LEGEND_HEADING_GAP = 3.0
_LEGEND_LABEL_GAP = 2.0
_LEGEND_FIRST_LINE_EM = 0.9
_LEGEND_LINE_EM = 1.38
#: Kept off a wrapped row's width, so a line QGIS measures a little longer
#: than the estimate still fits beside its symbol.
_LEGEND_WRAP_SLACK = 0.5
#: The narrowest a legend column's wrapped text gets, in ems: narrower and
#: a long layer name becomes a stack of single words.
_LEGEND_MIN_WRAP_EM = 9.0
#: Scales of the estimated layer heights `_legend_height` balances columns
#: at, to find every arrangement QGIS might settle on.
_LEGEND_BALANCE_SPREAD = (0.9, 0.95, 1.0, 1.05, 1.1)
#: Up to how many pieces each is also tried a little taller or shorter
#: alone; more than this are only scaled together.
_LEGEND_FEW_GROUPS = 12
#: What a legend's rows are allowed over the estimate: real fonts wrap a
#: line or so differently from it (to within 6 % in the measurements, on
#: QGIS 3.44 and 4.2).
_LEGEND_SAFETY = 1.08
#: QGIS's default grid label size, in points (not set by the builder), and
#: a digit's advance in it (DejaVu Sans: 0.636 em).
_GRID_LABEL_PT = 10.0
_DIGIT_EM = 0.64


def _page_style(palette: PagePalette) -> _Style:
    """The page colours a design chose: its paper, written to the layout and
    carried to the HTML -- where an unchosen white would become the theme's
    paper. Every text item sets its own ink, so only a dark page names one
    for the page as well (QGIS keeps no page ink; the builder says so)."""
    style = {"bg": palette.page}
    if palette.dark:
        style["color"] = palette.ink
    return style


def _main(frame: Box, palette: PagePalette, *, stroke: str | None = None) -> FrameSlot:
    """The main frame with the design's map background and a quiet edge."""
    return FrameSlot(
        MAIN_FRAME_ID,
        frame,
        frame_bg=palette.page,
        frame_stroke=stroke or palette.hairline,
    )


def _solid(item_id: str, box: Box, colour: str, *, z: int) -> ShapeSpec:
    """A filled panel or rule, its edge in its own colour: a shape with no
    edge colour gets the runtime's dark default line, which on a thin rule
    hides the fill altogether."""
    return ShapeSpec(
        id=item_id, box=box, fill=colour, stroke=colour, stroke_width=0.1, z=z
    )


def _title(box: Box, font_pt: float, palette: PagePalette, *, z: int) -> TextSpec:
    return TextSpec(
        id="title",
        box=box,
        body="{{title}}",
        kind="title",
        font_size=font_pt,
        weight="bold",
        color=palette.ink,
        z=z,
    )


def _notes(box: Box, font_pt: float, palette: PagePalette, *, z: int) -> TextSpec:
    return TextSpec(
        id="notes",
        box=box,
        body="{{notes}}",
        font_size=font_pt,
        color=palette.ink,
        z=z,
    )


def _north(box: Box, palette: PagePalette, *, z: int) -> NorthSpec:
    return NorthSpec(id="north", box=box, for_=MAIN_FRAME_ID, color=palette.ink, z=z)


def _scale_bar(
    box: Box, palette: PagePalette, *, font_pt: float, z: int
) -> ScalebarSpec:
    """Two segments: room for QGIS's labels at any width, never crowded;
    the labels `font_pt` (`scalebar_pt`)."""
    return ScalebarSpec(
        id="scalebar",
        box=box,
        for_=MAIN_FRAME_ID,
        segments=2,
        color=palette.ink,
        font_size=font_pt,
        z=z,
    )


def scalebar_pt(page: _Page, factor: float = 1.0) -> float:
    """A scale bar's label size on `page`: the page's caption size, at most
    `_SCALEBAR_MAX_PT`, times `factor` -- so an A5 bar's labels are as
    small as its captions and an A1 bar's grow with the sheet, rather than
    QGIS's 12 pt everywhere."""
    return round(min(page.pt("caption"), _SCALEBAR_MAX_PT) * factor, 2)


def scalebar_height(font_pt: float) -> float:
    """The height in mm QGIS draws a scale bar at with `font_pt` labels --
    its bar, the gap and the labels (`QgsLayoutItemScaleBar` measured at
    6.5 to 24 pt) -- and a little to spare."""
    return round(8.6 + 0.41 * font_pt, 2)


def _bar_h(page: _Page) -> float:
    """The scale bar's box height on `page`, for its `scalebar_pt` labels."""
    return max(_SCALEBAR_H, scalebar_height(scalebar_pt(page)))


def _fit_title(
    text: str, width: float, max_pt: float, min_pt: float, *, max_lines: int
) -> tuple[float, float]:
    """A bold title's size and box height in a `width` mm column: `max_pt`
    when it fits in `max_lines`, smaller for a longer one, down to `min_pt`
    -- and there as many lines as it takes, never clipped. (0 high for no
    title.)"""
    if not text.strip():
        return max_pt, 0.0
    width *= _TITLE_LINE_FILL
    tallest = max_lines * max_pt * PT_MM * TITLE_LINE_HEIGHT + 0.01
    size = fit_text_size(
        text,
        Box(0.0, 0.0, width, tallest),
        max_pt,
        min_pt,
        bold=True,
        line_height=TITLE_LINE_HEIGHT,
        max_lines=max_lines,
    )
    height = text_height(text, width, size, bold=True, line_height=TITLE_LINE_HEIGHT)
    return size, height + _TEXT_SLACK


def _fit_body(
    text: str, width: float, max_pt: float, min_pt: float, max_h: float
) -> tuple[float, float]:
    """Running text's size and box height in a `width` mm column: `max_pt`
    when it fits `max_h`, smaller down to `min_pt` when not. Text longer
    still than `max_h` holds at `min_pt` keeps `max_h` -- the page's other
    pieces keep their room. (0 high for no text.)"""
    if not text.strip():
        return max_pt, 0.0
    size = max_pt
    if text_height(text, width, size) + _TEXT_SLACK > max_h:
        size = fit_text_size(
            text, Box(0.0, 0.0, width, max_h - _TEXT_SLACK), max_pt, min_pt
        )
    return size, min(text_height(text, width, size) + _TEXT_SLACK, max(max_h, 0.0))


@functools.lru_cache(maxsize=8192)
def _text_width(text: str, font_pt: float, *, bold: bool = False) -> float:
    """The width in mm `text` sets to on one line (the `text_lines` estimate).
    Cached: the legend fitting measures the same names many times a plan."""
    em = font_pt * PT_MM * (_BOLD_FACTOR if bold else 1.0)
    return sum((_SPACE_EM if ch == " " else _char_em(ch)) * em for ch in text)


#: The most of a page's content height a heading may take.
_HEADING_MAX_SHARE = 0.3


class _Heading:
    """A page's title -- under a short accent mark, when the design has
    one -- sized for a `width` mm column: as large as the page's title size
    in two lines, smaller for a longer title. Nothing at all for a page
    with no title."""

    def __init__(
        self,
        page: _Page,
        width: float,
        palette: PagePalette,
        *,
        accent: bool = True,
        max_height: float | None = None,
    ) -> None:
        self.page = page
        self.palette = palette
        self.gap = page.spacing.gutter
        self.font_pt, self.title_h = _fit_title(
            page.title, width, page.pt("title"), MINIMUM_TYPE.title, max_lines=2
        )
        self.accent_h = max(0.8 * page.scale, 0.6) if accent and self.title_h else 0.0
        self.height = 0.0
        if self.title_h:
            self.height = self.title_h + self.gap
            if self.accent_h:
                self.height += self.accent_h + self.gap * 0.6
            if max_height is not None and self.height > max_height:
                # A title too long even at its smallest size keeps only the
                # room it is allowed; the page's other pieces keep theirs.
                self.title_h -= self.height - max_height
                self.height = max_height

    def items(self, x: float, y: float, width: float) -> list[CartographItem]:
        if not self.title_h:
            return []
        items: list[CartographItem] = []
        if self.accent_h:
            items.append(
                _solid(
                    "rule-accent",
                    Box(x, y, min(12.0 * self.page.scale, width), self.accent_h),
                    self.palette.accent,
                    z=1,
                )
            )
            y += self.accent_h + self.gap * 0.6
        items.append(
            _title(Box(x, y, width, self.title_h), self.font_pt, self.palette, z=2)
        )
        return items


# -- legends ----------------------------------------------------------------


#: The largest a legend's text grows on a big sheet: beyond it the text
#: outgrows QGIS's fixed-size symbols beside it.
_LEGEND_MAX_PT = 18.0


def _legend_pt(page: _Page) -> float:
    return min(page.pt("legend"), _LEGEND_MAX_PT)


def _legend_rows(page: _Page) -> int:
    return legend_row_count(page.data.layer_names, page.data.legend_labels)


def _legend_text_h(font_pt: float, lines: int) -> float:
    """A legend row's text, `lines` lines of it, in mm as QGIS sets it."""
    return (_LEGEND_FIRST_LINE_EM + (lines - 1) * _LEGEND_LINE_EM) * font_pt * PT_MM


def _legend_row_h(font_pt: float, lines: int = 1) -> float:
    """A legend row's height in QGIS: its symbol or its text (`lines` lines
    of it), whichever is taller, and the space above it."""
    return max(_LEGEND_SYMBOL_H, _legend_text_h(font_pt, lines)) + _LEGEND_ROW_GAP


def _legend_width(page: _Page, font_pt: float) -> float:
    """One legend column wide enough for its longest row: a layer heading
    (bold) or a class label beside its symbol."""
    data = page.data
    widest = 0.0
    for index, name in enumerate(data.layer_names):
        labels = data.legend_labels[index] if index < len(data.legend_labels) else ()
        if len(labels) > 1:
            widest = max(widest, _text_width(name, font_pt, bold=True))
            widest = max(
                widest,
                *(
                    _text_width(label, font_pt) + _LEGEND_SYMBOL_W + 2.0
                    for label in labels
                ),
            )
        else:
            widest = max(widest, _text_width(name, font_pt) + _LEGEND_SYMBOL_W + 2.0)
    return widest + 2 * _LEGEND_BOX_SPACE + 1.0


def _legend_key(page: _Page) -> _LegendKey:
    """The page's legend content, hashable: its layer names and each one's
    class labels."""
    data = page.data
    return tuple(data.layer_names), tuple(
        tuple(labels) for labels in data.legend_labels
    )


def _legend_groups(page: _Page) -> list[list[tuple[str, bool]]]:
    return _legend_groups_of(_legend_key(page))


def _legend_groups_of(key: _LegendKey) -> list[list[tuple[str, bool]]]:
    """Each layer's legend texts, as QGIS lays them out and keeps them
    together in a column: one row for a layer of a single symbol (its
    name), or its name as a heading (`True`) over a row per class."""
    layer_names, legend_labels = key
    groups: list[list[tuple[str, bool]]] = []
    for index, name in enumerate(layer_names):
        labels = legend_labels[index] if index < len(legend_labels) else ()
        if len(labels) > 1:
            groups.append([(name, True), *((label, False) for label in labels)])
        else:
            groups.append([(name, False)])
    return groups


def legend_wrap_width(width: float, columns: int) -> float:
    """The width in mm a legend's row text wraps at in `columns` columns
    across a `width` mm box -- the `LegendSpec.wrap_width` for it: a
    column's share of the box, less QGIS's spacing and the symbol."""
    columns = max(columns, 1)
    column_w = (width - (columns + 1) * _LEGEND_BOX_SPACE) / columns
    return round(
        max(column_w - _LEGEND_SYMBOL_W - _LEGEND_LABEL_GAP - _LEGEND_WRAP_SLACK, 1.0),
        2,
    )


def _legend_groups_sized(
    key: _LegendKey, font_pt: float, wrap: float, *, split: bool = False
) -> list[tuple[float, float]]:
    """Each piece QGIS keeps together in a legend column, its rows wrapped
    at `wrap` mm: the space QGIS keeps above it, and its own height. A
    piece is a whole layer -- or, `split` (QGIS's "split layers"), each
    class row, the layer's heading going with its first."""
    sized = []
    for group in _legend_groups_of(key):
        above = 0.0
        height = 0.0
        for index, (text, heading) in enumerate(group):
            lines = max(1, text_lines(text, wrap, font_pt))
            if heading:
                gap, own = _LEGEND_HEADING_GAP, _legend_text_h(font_pt, lines)
            else:
                gap = _LEGEND_ROW_GAP
                own = _legend_row_h(font_pt, lines) - _LEGEND_ROW_GAP
            if split and index > 1:
                sized.append((above, height))
                above, height = gap, own
                continue
            if index:
                height += gap
            else:
                above = gap
            height += own
        sized.append((above, height))
    return sized


def _legend_columns_of(
    groups: Sequence[tuple[float, float]], columns: int
) -> list[int]:
    """The column QGIS puts each of `groups` in (each the space above it
    and its height, a layer kept whole) given `columns`: a port of its own
    rule, `QgsLegendRenderer::setColumns` -- a first pass that breaks a
    column once it passes the average height left for the columns to come,
    then up to two passes that shift a layer back into a shorter column."""
    count = len(groups)
    if not count:
        return []
    columns = max(1, min(columns, count))
    heights = [height for _, height in groups]
    spaces = [above for above, _ in groups]
    total_heights = sum(heights)
    total_spaces = sum(spaces)
    average_group = total_heights / count
    average_space = total_spaces / count if count > columns else 0.0
    breaks = columns - 1
    tallest = max(heights)
    column_of = [0] * count
    column = placed = auto_breaks = 0
    current = worst = 0.0
    remaining_heights, remaining_spaces = total_heights, total_spaces
    for index, (space, height) in enumerate(groups):
        remaining_heights -= height
        remaining_spaces -= space
        grown = current + (space if placed else 0.0) + height
        left_groups = count - index
        left_columns = breaks + 1 - auto_breaks
        left_breaks = left_columns - 1
        average_left_space = (
            remaining_spaces / (left_groups - 1) if left_groups > 1 else 0.0
        )
        rest = remaining_heights + remaining_spaces - left_breaks * average_left_space
        target = (grown + rest) / left_columns
        per_column = math.ceil(target / (average_group + average_space))
        target = per_column * (average_group + average_space) - average_space
        can = placed > 0 and column < columns - 1 and auto_breaks < breaks
        should = (
            grown > target and placed > 0 and grown > tallest and grown > worst
        ) or count - index < columns - column
        if can and should:
            column += 1
            auto_breaks += 1
            placed = 0
            current = height
        else:
            current = grown
        column_of[index] = column
        placed += 1
        worst = max(worst, current)

    def column_heights() -> tuple[dict[int, float], dict[int, int]]:
        sums: dict[int, float] = {}
        counts: dict[int, int] = {}
        for index, (space, height) in enumerate(groups):
            sums[column_of[index]] = sums.get(column_of[index], 0.0) + space + height
            counts[column_of[index]] = counts.get(column_of[index], 0) + 1
        return sums, counts

    for _ in range(2):
        sums, counts = column_heights()
        average = sum(sums.values()) / len(sums)
        tallest_column = max(sums.values())
        changed = False
        next_candidate = 1
        for index, (space, height) in enumerate(groups):
            own = column_of[index]
            if own < next_candidate:
                continue
            if (
                counts[own] >= 2
                and sums[own - 1] < average
                and (sums[own - 1] + height) * 0.9 < tallest_column
            ):
                column_of[index] = own - 1
                sums[own - 1] += height + space
                counts[own - 1] += 1
                sums[own] -= height
                counts[own] -= 1
                changed = True
            else:
                next_candidate = own + 1
        if not changed:
            break
    return column_of


def _legend_columns_h(
    groups: Sequence[tuple[float, float]], column_of: Sequence[int]
) -> float:
    """The tallest column `groups` make placed in the columns `column_of`."""
    drawn: dict[int, float] = {}
    for index, (space, height) in enumerate(groups):
        own = column_of[index]
        first = own not in drawn
        drawn[own] = drawn.get(own, 0.0) + (0.0 if first else space) + height
    return max(drawn.values(), default=0.0)


def _legend_height(
    page: _Page, font_pt: float, width: float, columns: int, *, split: bool = False
) -> float:
    """The legend's height in `columns` columns across a `width` mm box:
    its rows wrapped at the columns' width (`legend_wrap_width`), each
    layer kept whole in a column -- or, `split`, its class rows free to run
    on into the next -- and the columns balanced as QGIS does (measured
    against QGIS's legend renderer)."""
    return _legend_height_of(
        _legend_key(page), font_pt, round(width, 3), columns, split
    )


@functools.lru_cache(maxsize=4096)
def _legend_height_of(
    key: _LegendKey, font_pt: float, width: float, columns: int, split: bool
) -> float:
    """`_legend_height` for the legend content `key`, remembered: designs
    ask the same question many times while they settle a page."""
    groups = _legend_groups_sized(
        key, font_pt, legend_wrap_width(width, columns), split=split
    )
    if not groups:
        return 0.0
    # QGIS's rule tips a layer into the next column on small differences,
    # and the estimate of each layer's height is not exact: the tallest
    # column any nearby estimate gives -- every layer a little taller or
    # shorter, together or (a few layers) one at a time -- is the one
    # planned for.
    heights = [height for _, height in groups]
    trials = [heights]
    for f in _LEGEND_BALANCE_SPREAD:
        trials.append([height * f for height in heights])
        if len(heights) > _LEGEND_FEW_GROUPS:
            continue
        for index in range(len(heights)):
            trial = list(heights)
            trial[index] *= f
            trials.append(trial)
    column_h = 0.0
    for trial in trials:
        arranged = _legend_columns_of(
            [(above, h) for (above, _), h in zip(groups, trial)], columns
        )
        column_h = max(column_h, _legend_columns_h(groups, arranged))
    if split:
        # Many small pieces: QGIS's balance can leave one column longer than
        # any trial does -- by up to half a piece, over every design, page
        # and test legend in QGIS 4.2 and 3.44; a whole piece's allowance
        # left legend strips half empty.
        column_h += 0.5 * (max(heights) + _LEGEND_ROW_GAP)
    return column_h * _LEGEND_SAFETY + _LEGEND_ROW_GAP + _LEGEND_FRAME_H


def _legend_columns(
    page: _Page, font_pt: float, width: float, *, split: bool = False
) -> int:
    """The most legend columns `width` mm holds: one per layer at most (a
    layer stays whole in its column) or, `split`, one per row, each wide
    enough that its rows wrap no narrower than `_LEGEND_MIN_WRAP_EM` ems
    -- or than its longest word, which QGIS never breaks."""
    texts = [text for group in _legend_groups(page) for text, _ in group]
    if not texts:
        return 1
    widest = max(_text_width(text, font_pt) for text in texts)
    longest_word = max(
        _text_width(word, font_pt) for text in texts for word in text.split() or [""]
    )
    least = min(max(_LEGEND_MIN_WRAP_EM * font_pt * PT_MM, longest_word), widest)
    pieces = len(texts) if split else len(page.data.layer_names)
    most = 1
    while most < pieces:
        if legend_wrap_width(width, most + 1) < least:
            break
        most += 1
    return most


def _legend_fit(
    page: _Page, font_pt: float, width: float, height: float
) -> tuple[int, float, float]:
    """The fewest legend columns across `width` mm in which every row fits
    `height` mm at `font_pt`, the width its rows wrap at and the height it
    needs -- or, when no arrangement fits, the one that needs least.

    Layers are kept whole in a column first, as QGIS draws them; then a
    long class list may run on into the next column, which QGIS does once
    the rows overflow (`canvas_cartograph._fit_legend` turns on "split
    layers" before it ever makes the text smaller)."""
    best: tuple[int, float, float] | None = None
    for split in (False, True):
        for columns in range(1, _legend_columns(page, font_pt, width, split=split) + 1):
            need = _legend_height(page, font_pt, width, columns, split=split)
            fit = (columns, legend_wrap_width(width, columns), need)
            if need <= height + 1e-6:
                return fit
            if best is None or need < best[2] - 1e-6:
                best = fit
    return best if best is not None else (1, legend_wrap_width(width, 1), 0.0)


def _legend_need(page: _Page, font_pt: float, width: float) -> float:
    """The least height the legend needs across `width` mm at `font_pt`,
    in whichever number of columns needs least."""
    return _legend_fit(page, font_pt, width, 0.0)[2]


def _legend_plan(
    page: _Page, font_pt: float, box: Box, floor: float = MINIMUM_TYPE.legend
) -> tuple[float, int, float]:
    """The legend's size, columns and wrap width in `box`: `font_pt` in
    the fewest columns its rows fit down (`_legend_fit`). A legend the
    design could not give room enough is set smaller, a quarter point at
    a time, until it fits -- but never below `floor` (the design's
    readable minimum, `MINIMUM_TYPE.legend` unless it sets its own), and
    never left for QGIS to shrink when the planner can choose instead. A
    legend that does not fit even at `floor` keeps it: the entries that do
    not fit are left out (`canvas_cartograph._fit_legend`), never drawn
    smaller."""
    size = font_pt
    while True:
        columns, wrap, need = _legend_fit(page, size, box.w, box.h)
        least = min(font_pt, floor)
        if need <= box.h + 1e-6 or size <= least:
            return size, columns, wrap
        size = max(least, round(size - 0.25, 2))


#: The least share of the room a map splits with its legend -- along the
#: side the two share, the gap between them left out -- that the map keeps
#: when a long legend asks for more: half. A design gives a crowded legend
#: more columns, a wider column or a strip of its own first, and then the
#: map yields, but only until the two are the same size: past that the
#: legend would be the page's subject and the map its illustration. What
#: still does not fit is left out of the legend and reported
#: (`canvas_cartograph._fit_legend`), never set below the readable floor.
MAP_MIN_SHARE = 0.5


def legend_room_cap(shared: float, gap: float = 0.0) -> float:
    """The most of `shared` mm -- a run the map and its legend split, with
    a `gap` between them -- that the legend may take (`MAP_MIN_SHARE`)."""
    return max(shared - gap, 0.0) * (1.0 - MAP_MIN_SHARE)


def _band_layout(
    page: _Page,
    legend_pt: float,
    base_h: float,
    beside_w: float,
    under_w: float,
    room: float,
) -> tuple[bool, float]:
    """A band under the map holding a fixed part `base_h` high (a title
    block, a locator) and the legend -- beside it, `beside_w` wide, or
    under it, `under_w` wide -- where the map and the band share `room` mm
    of height: whether the legend goes under, and the band's height.

    The legend may take as much as leaves the map `MAP_MIN_SHARE` of the
    room. Where it fits only one way, that way; where it fits both, the
    lower band; where it fits neither, the way that holds more of it."""
    g = page.spacing.gutter
    beside_need = _legend_need(page, legend_pt, beside_w)
    under_need = _legend_need(page, legend_pt, under_w)
    beside_cap = legend_room_cap(room, g)
    under_cap = legend_room_cap(room - base_h - g, g)
    fits_beside = beside_need <= beside_cap + 1e-6
    fits_under = under_need <= under_cap + 1e-6
    if fits_beside != fits_under:
        under = fits_under
    elif fits_beside:
        under = base_h + g + under_need < max(base_h, beside_need)
    else:
        under = under_need * beside_cap < beside_need * under_cap
    if under:
        return True, base_h + g + min(under_need, under_cap)
    return False, max(base_h, min(beside_need, beside_cap))


def _legend(
    page: _Page, box: Box, palette: PagePalette, font_pt: float, *, z: int
) -> list[LegendSpec]:
    """The legend in `box` -- in the fewest columns its rows fit down, its
    long rows wrapped to the columns' width (`_legend_plan`) -- or nothing
    for no layers or a box too small."""
    size, columns, wrap = _legend_plan(page, font_pt, box)
    legend = legend_or_none(
        page.data.layer_names,
        box,
        columns=columns,
        bg=palette.page,
        color=palette.ink,
        font_size=size,
        wrap_width=wrap,
        z=z,
    )
    return [legend] if legend is not None else []


# -- the foot: scale bar, north arrow when the map is turned, credits ------


def _scale_bar_w(page: _Page) -> float:
    """A scale bar's width: wide enough that QGIS's labels never crowd."""
    return 50.0 * min(max(page.scale, 1.0), 2.0)


def _row_bar_w(page: _Page, width: float) -> float:
    return min(_scale_bar_w(page), max(width * 0.4, 45.0), width)


def _fits_row(page: _Page, width: float) -> bool:
    """Whether the source line still has a useful width beside the bar."""
    north_w = _NORTH_W + page.spacing.gutter if page.rotated else 0.0
    left = width - _row_bar_w(page, width) - page.spacing.gutter - north_w
    if not page.credits:
        return True
    needed = text_height(page.credits, left, page.pt("credit")) + _TEXT_SLACK
    return left >= 30.0 and needed <= _bar_h(page)


def _foot_height(page: _Page, width: float) -> float:
    """The foot's height under a `width` mm map: one row, or the scale bar
    over the source line where a row would squeeze it."""
    if _fits_row(page, width):
        return _bar_h(page)
    return _stacked_foot_height(page, width)


def _foot(
    page: _Page, box: Box, palette: PagePalette, *, z: int
) -> list[CartographItem]:
    """The foot in `box`, as `_foot_height` planned it."""
    if _fits_row(page, box.w):
        return _foot_row(page, box, palette, z=z)
    return _stacked_foot(page, box, palette, z=z)


def _foot_row(
    page: _Page,
    box: Box,
    palette: PagePalette,
    *,
    z: int,
    credits: bool = True,
) -> list[CartographItem]:
    """The scale bar at the left of `box`, a north arrow beside it for a
    turned map, and the source line set right, on the bar's baseline."""
    g = page.spacing.gutter
    north_w = _NORTH_W + g if page.rotated else 0.0
    bar_w = (
        _row_bar_w(page, box.w) if credits else min(_scale_bar_w(page), box.w - north_w)
    )
    items: list[CartographItem] = [
        _scale_bar(
            Box(box.x, box.y, bar_w, _bar_h(page)),
            palette,
            font_pt=scalebar_pt(page),
            z=z,
        )
    ]
    x = box.x + bar_w + g
    if page.rotated:
        items.append(_north(Box(x, box.y, _NORTH_W, _NORTH_H), palette, z=z + 1))
        x += _NORTH_W + g
    if credits and page.credits:
        width = box.x + box.w - x
        font_pt = page.pt("credit")
        height = min(text_height(page.credits, width, font_pt) + _TEXT_SLACK, box.h)
        credit = source_line(
            page.credits,
            Box(x, box.y + box.h - height, width, height),
            palette=palette,
            font_size=font_pt,
            align="end",
        )
        if credit is not None:
            items.append(credit)
    return items


def _stacked_foot_height(page: _Page, width: float) -> float:
    credits_h = text_height(page.credits, width, page.pt("credit"))
    return _bar_h(page) + (
        page.spacing.gutter / 2 + credits_h + _TEXT_SLACK if credits_h else 0.0
    )


def _stacked_foot(
    page: _Page, box: Box, palette: PagePalette, *, z: int
) -> list[CartographItem]:
    """The scale bar (and a north arrow for a turned map) over the source
    line, in a column `box`."""
    g = page.spacing.gutter
    north_w = _NORTH_W + g if page.rotated else 0.0
    bar_w = min(_scale_bar_w(page), box.w - north_w)
    items: list[CartographItem] = [
        _scale_bar(
            Box(box.x, box.y, bar_w, _bar_h(page)),
            palette,
            font_pt=scalebar_pt(page),
            z=z,
        )
    ]
    if north_w:
        items.append(
            _north(Box(box.x + bar_w + g, box.y, _NORTH_W, _NORTH_H), palette, z=z + 1)
        )
    if page.credits:
        font_pt = page.pt("credit")
        credits_h = text_height(page.credits, box.w, font_pt) + _TEXT_SLACK
        y = box.y + _bar_h(page) + g / 2
        credit = source_line(
            page.credits,
            Box(box.x, y, box.w, max(min(credits_h, box.y + box.h - y), 1.0)),
            palette=palette,
            font_size=font_pt,
        )
        if credit is not None:
            items.append(credit)
    return items


def _degree_grid(page: _Page, frame: Box) -> GraticuleSpec:
    """Degrees on a geographic map, labelled inside the frame so nothing
    sits on the page round it."""
    data = page.data
    return GraticuleSpec(
        id="graticule",
        box=frame,
        for_=MAIN_FRAME_ID,
        crs="EPSG:4326",
        interval=nice_interval(min(data.extent_width, data.extent_height) / 4),
        labels="inside",
        z=1,
    )


def _grid_label_room(page: _Page, interval: float) -> tuple[float, float]:
    """Room in mm for a grid's labels outside the frame: at its sides, as
    wide as its longest coordinate, and above and below, one line."""
    data = page.data
    decimals = max(0, -math.floor(math.log10(interval)))
    if data.crs_is_geographic:
        digits = 4
    elif data.extent_center is not None:
        cx, cy = data.extent_center
        reach = max(
            abs(cx) + data.extent_width / 2, abs(cy) + data.extent_height / 2, 1.0
        )
        negative = min(cx - data.extent_width / 2, cy - data.extent_height / 2) < 0
        digits = len(str(int(reach))) + (1 if negative else 0)
    else:
        digits = 8
    chars = digits + (decimals + 1 if decimals else 0)
    em = _GRID_LABEL_PT * PT_MM
    return chars * _DIGIT_EM * em + 2.5, em * TITLE_LINE_HEIGHT + 2.0


# -- design pieces ----------------------------------------------------------


def _grid_interval(page: _Page, frame: Box, interval: float, label_w: float) -> float:
    """`interval`, or the next 1-2-5 step up until grid lines stand far
    enough apart on the page that labels `label_w` mm long never meet."""
    data = page.data
    units_per_mm = max(data.extent_width / frame.w, data.extent_height / frame.h)
    least = label_w * units_per_mm
    while interval < least:
        exponent = math.floor(math.log10(interval))
        step = round(interval / 10.0**exponent, 6)
        interval = (2.0 if step < 1.5 else 5.0 if step < 3.5 else 10.0) * 10.0**exponent
    return interval


class _Caption:
    """Caption's zone under the map, for a frame `width` mm wide: the title
    as the caption's lead and the notes as its text, at a reading measure,
    with the scale bar and source line in a column beside it when there is
    room, or in a row under it."""

    def __init__(
        self, page: _Page, width: float, measure: float, page_h: float
    ) -> None:
        self.page = page
        g = page.spacing.gutter
        side_w = min(60.0 * min(page.scale, 2.0), width - measure - g)
        self.side_w = side_w if side_w >= 45.0 else 0.0
        self.text_w = measure if self.side_w else width
        self.lead_pt, self.lead_h = _fit_title(
            page.title,
            self.text_w,
            round(page.pt("subtitle", "title") * 1.1, 2),
            MINIMUM_TYPE.subtitle,
            max_lines=3,
        )
        self.notes_pt, self.notes_h = _fit_body(
            page.notes, self.text_w, page.pt("body"), MINIMUM_TYPE.body, page_h * 0.3
        )
        self.text_h = (
            self.lead_h
            + (g / 2 if self.lead_h and self.notes_h else 0.0)
            + self.notes_h
        )
        if self.side_w:
            self.foot_h = _stacked_foot_height(page, self.side_w)
            self.height = max(self.text_h, self.foot_h)
        else:
            self.foot_h = _foot_height(page, width)
            self.height = self.text_h + (g if self.text_h else 0.0) + self.foot_h

    def items(self, box: Box) -> list[CartographItem]:
        page = self.page
        pal = DAYLIGHT
        g = page.spacing.gutter
        items: list[CartographItem] = []
        y = box.y
        if self.lead_h:
            items.append(
                _title(Box(box.x, y, self.text_w, self.lead_h), self.lead_pt, pal, z=3)
            )
            y += self.lead_h + g / 2
        if self.notes_h:
            items.append(
                _notes(
                    Box(box.x, y, self.text_w, self.notes_h), self.notes_pt, pal, z=4
                )
            )
        if self.side_w:
            side = Box(box.x + box.w - self.side_w, box.y, self.side_w, self.foot_h)
            items += _stacked_foot(page, side, pal, z=5)
        else:
            row_y = box.y + self.text_h + (g if self.text_h else 0.0)
            items += _foot(page, Box(box.x, row_y, box.w, self.foot_h), pal, z=5)
        return items


class _TitleBlock:
    """Fieldbook's ruled title block: the title with the north arrow at its
    right, a rule, then scale, CRS, and author and date, then the scale
    bar across the block's foot.

    The ruled metadata-cell treatment is adapted from Nikolaos Sorkos's
    QLayoutDesigner title block (MIT, a433740). Its notice is shipped in
    licenses/QLayoutDesigner-MIT.txt; no external assets or dependencies.
    """

    def __init__(self, page: _Page, palette: PagePalette) -> None:
        self.page = page
        self.palette = palette
        sp = page.spacing
        self.pad = sp.pad * 1.5
        self.gap = sp.gutter
        self.title_max = max(MINIMUM_TYPE.title, round(page.pt("title") * 0.75, 2))
        self.row_pt = page.pt("body")
        self.row_h = self.row_pt * PT_MM * TEXT_LINE_HEIGHT + _TEXT_SLACK

    def _title_fit(self, width: float) -> tuple[float, float]:
        return _fit_title(
            self.page.title,
            width - 2 * self.pad - _NORTH_W - self.gap,
            self.title_max,
            MINIMUM_TYPE.title,
            max_lines=3,
        )

    def _head_h(self, width: float) -> float:
        return max(self._title_fit(width)[1], _NORTH_H)

    def height(self, width: float) -> float:
        rule = self.page.spacing.rule
        return (
            2 * self.pad
            + self._head_h(width)
            + 1.5 * self.gap
            + rule
            + 3 * self.row_h
            + self.gap
            + _bar_h(self.page)
        )

    def items(self, box: Box) -> list[CartographItem]:
        page, pal, pad, g = self.page, self.palette, self.pad, self.gap
        sp = page.spacing
        inner = _inset(box, pad)
        items: list[CartographItem] = [
            ShapeSpec(
                id="title-block",
                box=box,
                stroke=pal.rule,
                stroke_width=sp.rule,
                z=2,
            )
        ]
        title_pt, title_h = self._title_fit(box.w)
        if title_h:
            title_w = inner.w - _NORTH_W - g
            items.append(
                _title(Box(inner.x, inner.y, title_w, title_h), title_pt, pal, z=3)
            )
        items.append(
            _north(
                Box(inner.x + inner.w - _NORTH_W, inner.y, _NORTH_W, _NORTH_H), pal, z=4
            )
        )
        y = inner.y + self._head_h(box.w) + g * 0.75
        items.append(_solid("rule-block", Box(box.x, y, box.w, sp.rule), pal.rule, z=5))
        y += sp.rule + g * 0.75
        author = page.data.author.strip()
        for index, body in enumerate(
            (
                "Scale 1:{{scale}}",
                "{{crs}}",
                f"{author} · {{{{date}}}}" if author else "{{date}}",
            )
        ):
            if index:
                items.append(
                    _solid(
                        f"rule-metadata-{index}",
                        Box(inner.x, y, inner.w, sp.rule),
                        pal.hairline,
                        z=5,
                    )
                )
            items.append(
                TextSpec(
                    id=f"block-{index}",
                    box=Box(inner.x, y, inner.w, self.row_h),
                    body=body,
                    font_size=self.row_pt,
                    color=pal.ink,
                    z=6 + index,
                )
            )
            y += self.row_h
        items.append(
            _scale_bar(
                Box(
                    inner.x,
                    inner.y + inner.h - _bar_h(page),
                    min(_scale_bar_w(page) * 1.1, inner.w),
                    _bar_h(page),
                ),
                pal,
                font_pt=scalebar_pt(page),
                z=10,
            )
        )
        return items


def _rail(
    page: _Page, rail: Box, palette: PagePalette, story_pt: float, *, z: int
) -> list[CartographItem]:
    """Broadside's rail: the story, the legend, the scale bar at its foot."""
    g = page.spacing.gutter
    items: list[CartographItem] = []
    foot_y = rail.y + rail.h - _bar_h(page)
    notes_pt, notes_h = _rail_notes(page, rail.w, rail.h, story_pt)
    y = rail.y
    if notes_h:
        items.append(_notes(Box(rail.x, y, rail.w, notes_h), notes_pt, palette, z=z))
        y += notes_h + g * 1.5
    items += _legend(
        page,
        Box(rail.x, y, rail.w, foot_y - g - y),
        palette,
        _legend_pt(page),
        z=z + 1,
    )
    items += _foot_row(
        page, Box(rail.x, foot_y, rail.w, _bar_h(page)), palette, z=z + 2, credits=False
    )
    return items


def _rail_notes(
    page: _Page, width: float, height: float, story_pt: float
) -> tuple[float, float]:
    """The story's size and height in a rail `width` x `height` mm: up to
    45 % of the room over the scale bar beside a legend -- less, down to a
    fifth, for a legend that needs it -- and all of it alone."""
    g = page.spacing.gutter
    room = height - _bar_h(page) - g
    share = 1.0
    if page.data.layer_names:
        legend_h = _legend_need(page, _legend_pt(page), width)
        share = min(0.45, max(0.2, (room - legend_h - g * 1.5) / room))
    return _fit_body(page.notes, width, story_pt, MINIMUM_TYPE.body, room * share)


def _rail_holds_legend(
    page: _Page,
    width: float,
    height: float,
    story_pt: float,
    font_pt: float | None = None,
) -> bool:
    """Whether a rail `width` x `height` mm has room under its story for
    every legend row at `font_pt` (the page's legend size by default)."""
    g = page.spacing.gutter
    notes_h = _rail_notes(page, width, height, story_pt)[1]
    room = height - _bar_h(page) - g - (notes_h + g * 1.5 if notes_h else 0.0)
    return _legend_need(page, font_pt or _legend_pt(page), width) <= room


class _PosterBand:
    """Broadside's band under the map on a portrait sheet: the story with
    the scale bar under it at the left, the legend at the right -- the
    story narrower when the legend needs the width."""

    def __init__(self, page: _Page, body: Box, story_pt: float) -> None:
        self.page = page
        g = page.spacing.gutter
        has_legend = bool(page.data.layer_names)
        self.legend_pt = _legend_pt(page)
        # The left column holds the story over the scale bar, or the bar.
        north_w = _NORTH_W + g if page.rotated else 0.0
        legend_h = 0.0
        for share in (0.55, 0.45, 0.35) if has_legend else (1.0,):
            self.notes_w = body.w * share
            self.notes_pt, self.notes_h = _fit_body(
                page.notes, self.notes_w, story_pt, MINIMUM_TYPE.body, body.h * 0.22
            )
            self.left_w = self.notes_w if self.notes_h else _scale_bar_w(page) + north_w
            self.legend_w = body.w - self.left_w - 2 * g
            if has_legend:
                legend_h = _legend_need(page, self.legend_pt, self.legend_w)
            if not self.notes_h or legend_h <= body.h * 0.3:
                break
        # The map keeps half the body's height (`MAP_MIN_SHARE`).
        legend_h = min(legend_h, legend_room_cap(body.h, 1.5 * g))
        left_h = self.notes_h + (g if self.notes_h else 0.0) + _bar_h(page)
        self.height = max(left_h, legend_h)

    def items(self, band: Box, palette: PagePalette, *, z: int) -> list[CartographItem]:
        page = self.page
        items: list[CartographItem] = []
        if self.notes_h:
            items.append(
                _notes(
                    Box(band.x, band.y, self.notes_w, self.notes_h),
                    self.notes_pt,
                    palette,
                    z=z,
                )
            )
        items += _foot_row(
            page,
            Box(band.x, band.y + band.h - _bar_h(page), self.left_w, _bar_h(page)),
            palette,
            z=z + 2,
            credits=False,
        )
        if not page.data.layer_names:
            # Notes alone take the band's width: no legend column to place.
            return items
        legend_x = band.x + band.w - self.legend_w
        items += _legend(
            page,
            Box(legend_x, band.y, self.legend_w, band.h),
            palette,
            self.legend_pt,
            z=z + 1,
        )
        return items


def _figure_block(
    page: _Page,
    index: int,
    figure: tuple[str, str],
    x: float,
    y: float,
    width: float,
    palette: PagePalette,
) -> tuple[list[CartographItem], float]:
    """One key figure: an accent mark, the value large, what it counts under
    it. Returns its items and its height."""
    g = page.spacing.gutter
    value, label = figure
    mark_h = max(0.8 * page.scale, 0.6)
    items: list[CartographItem] = [
        _solid(
            f"rule-figure-{index}",
            Box(x, y, min(8.0 * page.scale, width), mark_h),
            palette.accent,
            z=6,
        )
    ]
    top = y
    y += mark_h + g * 0.5
    value_pt, value_h = _fit_title(
        value,
        width,
        round(page.pt("display", "notes") * 0.8, 2),
        MINIMUM_TYPE.title,
        max_lines=1,
    )
    items.append(
        TextSpec(
            id=f"figure-{index}",
            box=Box(x, y, width, value_h),
            body=value,
            font_size=value_pt,
            weight="bold",
            color=palette.ink,
            z=7,
        )
    )
    y += value_h
    if label:
        label_pt = page.pt("body")
        label_h = text_height(label, width, label_pt, max_lines=3) + _TEXT_SLACK
        items.append(
            TextSpec(
                id=f"figure-{index}-label",
                box=Box(x, y, width, label_h),
                body=label,
                font_size=label_pt,
                color=palette.muted,
                z=7,
            )
        )
        y += label_h
    return items, y - top


def _figure_stack(
    page: _Page,
    figures: Sequence[tuple[str, str]],
    column: Box,
    palette: PagePalette,
) -> tuple[list[CartographItem], float]:
    """Signal's figures one under another at the top of `column`; returns
    them and where the column goes on."""
    g = page.spacing.gutter
    items: list[CartographItem] = []
    y = column.y
    for index, figure in enumerate(figures):
        block, height = _figure_block(
            page, index, figure, column.x, y, column.w, palette
        )
        items += block
        y += height + g * 1.5
    return items, y


def _figure_row(
    page: _Page,
    figures: Sequence[tuple[str, str]],
    row: Box,
    palette: PagePalette,
) -> tuple[list[CartographItem], float]:
    """Signal's figures side by side across `row`, in equal cells; returns
    them and where the page goes on under them."""
    g = page.spacing.gutter
    gap = 1.5 * g
    cell_w = (row.w - gap * (len(figures) - 1)) / len(figures)
    items: list[CartographItem] = []
    tallest = 0.0
    for index, figure in enumerate(figures):
        x = row.x + index * (cell_w + gap)
        block, height = _figure_block(page, index, figure, x, row.y, cell_w, palette)
        items += block
        tallest = max(tallest, height)
    return items, row.y + tallest


def clear_area(
    frame: Box,
    overlays: Sequence[Box],
    aspect: float,
    *,
    gap: float = 0.0,
    min_share: float = 0.45,
) -> Box | None:
    """The part of `frame` clear of every overlay, kept `gap` mm from each,
    in which a view of `aspect` (width / height) comes out largest: the
    `FrameSlot.focus` for a design that lays panels over its map, so they
    cover only the view's surroundings. `None` with no overlays, or when
    the clearest part would show the view at less than `min_share` of the
    area it gets in the whole frame -- then it is better centred under the
    panels than shrunk."""
    if not overlays:
        return None
    right, bottom = frame.x + frame.w, frame.y + frame.h
    lefts = {frame.x} | {o.x + o.w + gap for o in overlays}
    rights = {right} | {o.x - gap for o in overlays}
    tops = {frame.y} | {o.y + o.h + gap for o in overlays}
    bottoms = {bottom} | {o.y - gap for o in overlays}
    best: Box | None = None
    best_area = 0.0
    for x0 in lefts:
        for x1 in rights:
            x0c, x1c = max(x0, frame.x), min(x1, right)
            if x1c - x0c < 1.0:
                continue
            for y0 in tops:
                for y1 in bottoms:
                    y0c, y1c = max(y0, frame.y), min(y1, bottom)
                    if y1c - y0c < 1.0:
                        continue
                    candidate = Box(x0c, y0c, x1c - x0c, y1c - y0c)
                    if any(_boxes_overlap(candidate, o) for o in overlays):
                        continue
                    w, h = _fit(aspect, candidate.w, candidate.h)
                    if w * h > best_area + 1e-9:
                        best, best_area = candidate, w * h
    whole_w, whole_h = _fit(aspect, frame.w, frame.h)
    if best is None or best_area < min_share * whole_w * whole_h:
        return None
    return best


def _boxes_overlap(a: Box, b: Box) -> bool:
    return (
        a.x + 1e-6 < b.x + b.w
        and b.x + 1e-6 < a.x + a.w
        and a.y + 1e-6 < b.y + b.h
        and b.y + 1e-6 < a.y + a.h
    )


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


# --------------------------------------------------------------------------
# Helpers for designs: text that fits, pieces that are there only with real
# content, text that reads over any map, frames shaped by the extent
# --------------------------------------------------------------------------

#: Millimetres per typographic point.
PT_MM = 25.4 / 72
#: Character advances, in ems, a little wider than DejaVu Sans -- the
#: widest of the sans-serif faces QGIS and browsers fall back to -- so an
#: estimate errs towards one line too many: room to spare, never a clip.
_WIDE_CHARS = frozenset("mwMW@%")
_NARROW_CHARS = frozenset("iljtf.,:;!|'()[]")
_WIDE_EM = 0.95
_CAPITAL_EM = 0.74
_LOWER_EM = 0.58
_NARROW_EM = 0.34
_SPACE_EM = 0.34
_BOLD_FACTOR = 1.1
#: The share of a title box's width its estimate may fill. The advances
#: above come within about 2% of DejaVu Sans Bold on running text (a little
#: under on lowercase words), and a title that wraps one line more than
#: estimated overflows its box -- so titles are broken a little early.
_TITLE_LINE_FILL = 0.95
#: Line heights the runtime sets (`cartograph.css`): titles 1.2, text 1.4.
TITLE_LINE_HEIGHT = 1.2
TEXT_LINE_HEIGHT = 1.4


@functools.lru_cache(maxsize=4096)
def _char_em(ch: str) -> float:
    if ch in _WIDE_CHARS:
        return _WIDE_EM
    if ch in _NARROW_CHARS:
        return _NARROW_EM
    if ch.isupper() or ch.isdigit():
        return _CAPITAL_EM
    return _LOWER_EM


@functools.lru_cache(maxsize=32768)
def text_lines(text: str, width: float, font_pt: float, *, bold: bool = False) -> int:
    """How many lines `text` wraps to in a box `width` mm wide at `font_pt`:
    a greedy word wrap over estimated character widths, a word longer than a
    line broken across lines, each newline a new line. 0 for no text."""
    if not text.strip():
        return 0
    em = font_pt * PT_MM * (_BOLD_FACTOR if bold else 1.0)
    space = _SPACE_EM * em
    lines = 0
    for paragraph in text.splitlines():
        lines += 1
        used = 0.0
        for word in paragraph.split():
            advances = [_char_em(ch) * em for ch in word]
            length = sum(advances)
            if used and used + space + length <= width:
                used += space + length
                continue
            if used:
                lines += 1
            used = 0.0
            for advance in advances:
                if used and used + advance > width:
                    lines += 1
                    used = 0.0
                used += advance
    return lines


def text_height(
    text: str,
    width: float,
    font_pt: float,
    *,
    bold: bool = False,
    line_height: float = TEXT_LINE_HEIGHT,
    max_lines: int | None = None,
) -> float:
    """The height in mm `text` needs in a `width` mm box at `font_pt`, at
    most `max_lines` lines when given; 0 for no text."""
    lines = text_lines(text, width, font_pt, bold=bold)
    if max_lines is not None:
        lines = min(lines, max_lines)
    return lines * font_pt * PT_MM * line_height


def title_box_height(
    text: str, width: float, font_pt: float, *, max_lines: int = 3
) -> float:
    """A bold title's box height: one line at least -- so the box keeps its
    place in the design -- up to `max_lines` for a long title."""
    lines = max(1, min(text_lines(text, width, font_pt, bold=True), max_lines))
    return lines * font_pt * PT_MM * TITLE_LINE_HEIGHT


def fit_text_size(
    text: str,
    box: Box,
    max_pt: float,
    min_pt: float,
    *,
    bold: bool = False,
    line_height: float = TEXT_LINE_HEIGHT,
    max_lines: int | None = None,
    step: float = 0.5,
) -> float:
    """The largest size from `max_pt` down to `min_pt`, in `step`s, at which
    `text` fits `box` (in at most `max_lines` lines when given); `min_pt`
    when none does -- never below it, so long text wraps rather than shrinks
    out of reading. A headline adapting to a long title is this."""
    size = max_pt
    while size > min_pt:
        lines = text_lines(text, box.w, size, bold=bold)
        fits_lines = max_lines is None or lines <= max_lines
        if fits_lines and lines * size * PT_MM * line_height <= box.h:
            return size
        size = round(size - step, 4)
    return min_pt


def legend_or_none(
    layer_names: Sequence[str],
    box: Box,
    *,
    min_w: float = 20.0,
    min_h: float = 12.0,
    item_id: str = "legend",
    columns: int | None = None,
    bg: str | None = None,
    color: str | None = None,
    font_size: float | None = None,
    wrap_width: float | None = None,
    z: int | None = None,
) -> LegendSpec | None:
    """A legend for the main map in `box`, or `None` when there are no
    layers to list or the box is too small to read (the designs' long-
    standing 20 x 12 mm floor). The rest are `LegendSpec` fields."""
    if not layer_names or box.w <= min_w or box.h <= min_h:
        return None
    return LegendSpec(
        id=item_id,
        box=box,
        for_=MAIN_FRAME_ID,
        columns=columns,
        bg=bg,
        color=color,
        font_size=font_size,
        wrap_width=wrap_width,
        z=z,
    )


def source_line(
    credits: str,
    box: Box,
    *,
    palette: PagePalette | None = None,
    font_size: float = 7.0,
    align: str | None = None,
    on_panel: bool = False,
) -> TextSpec | None:
    """The data credits line in `box`, or `None` when the layers credit no
    one -- never an empty line. With a `palette` it is set in its muted ink,
    and with `on_panel` on its opaque panel (for a line over the map)."""
    if not credits.strip():
        return None
    color = None
    bg = None
    if palette is not None:
        color = palette.panel_ink if on_panel else palette.muted
        bg = palette.panel if on_panel else None
    return _attribution(box, font_size=font_size, color=color, bg=bg, align=align)


def text_on_panel(
    item_id: str,
    box: Box,
    body: str,
    *,
    palette: PagePalette,
    font_size: float,
    weight: str | None = None,
    kind: str = "text",
    align: str | None = None,
    z: int | None = None,
) -> TextSpec:
    """Text that reads whatever the map under it shows: on the palette's
    OPAQUE panel in its panel ink, so its contrast never depends on the
    user's map colours. Raises `ValueError` for a palette whose panel is
    see-through."""
    panel = parse_color(palette.panel)
    if panel is None or panel[3] < 1.0:
        raise ValueError(f"palette {palette.name!r} has no opaque panel")
    return TextSpec(
        id=item_id,
        box=box,
        body=body,
        kind=kind,
        font_size=font_size,
        weight=weight,
        align=align,
        color=palette.panel_ink,
        bg=palette.panel,
        z=z,
    )


def frame_for_extent(
    aspect: float,
    area: Box,
    *,
    min_aspect: float | None = None,
    max_aspect: float | None = None,
    align: str = "center",
) -> Box:
    """The largest frame of the extent's `aspect` (width / height) inside
    `area`, its shape held within `min_aspect`..`max_aspect` -- so a long
    river gets a long frame but a round extent never an absurdly thin one
    (the frame pads the extent out to its box) -- and placed in `area` by
    `align`: "start" (top or left), "center" or "end"."""
    if not (aspect > 0) or math.isinf(aspect):
        raise ValueError(f"aspect must be a positive finite number, got {aspect!r}")
    if align not in ("start", "center", "end"):
        raise ValueError(f"align {align!r} is not start, center or end")
    shape = aspect
    if min_aspect is not None:
        shape = max(shape, min_aspect)
    if max_aspect is not None:
        shape = min(shape, max_aspect)
    w, h = _fit(shape, area.w, area.h)
    share = {"start": 0.0, "center": 0.5, "end": 1.0}[align]
    return Box(area.x + (area.w - w) * share, area.y + (area.h - h) * share, w, h)


def _fit_area(aspect: float, box: Box) -> float:
    """The area a view of `aspect` (w/h) covers when fitted into `box`."""
    w, h = _fit(aspect, box.w, box.h)
    return w * h


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
    "BASE_TEMPLATES",
    "LOCATOR_FRAME_ID",
    "MAIN_FRAME_ID",
    "MAP_MIN_SHARE",
    "PAGE_SIZE_ORDER",
    "FrameSlot",
    "LayoutTemplate",
    "TemplateInput",
    "TemplatePlan",
    "legend_room_cap",
    "plan_template",
    "template_spec",
]
