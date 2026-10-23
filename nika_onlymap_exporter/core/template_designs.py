"""Five more page designs for the template collection: Night Desk,
Marginalia, Atlas Ledger, Town Hall and Wayfinder.

They sit beside the ten in `core/layout_templates.py` and are planned the
same way -- arithmetic only, millimetre boxes out, no `qgis` or Qt import --
from the same helpers and the named page palettes in `core/page_style.py`.
`layout_templates` adds `NEW_TEMPLATES` to its `TEMPLATES` at the end of its
own module, so `get_template()` and the picker see all fifteen.

Each design picks its page and map-frame colours explicitly, so the HTML
page keeps them whatever theme it opens in, and nothing here touches the
user's layers: the map's symbology stays theirs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from dataclasses import replace

from . import layout_templates as _layout_templates
from .cartograph_ir import (
    Box,
    CartographItem,
    LegendSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from .layout_templates import (
    CHARACTERFUL,
    ESSENTIAL,
    LOCATOR_FRAME_ID,
    LOCATOR_ZOOM_OUT,
    MAIN_FRAME_ID,
    N_,
    PT_MM,
    TEXT_LINE_HEIGHT,
    TITLE_LINE_HEIGHT,
    FrameSlot,
    LayoutTemplate,
    _char_em,
    _fit_area,
    _Layout,
    _legend_need,
    _legend_plan,
    _legend_width,
    _Page,
    fit_text_size,
    frame_for_extent,
    legend_or_none,
    legend_room_cap,
    scalebar_height,
    scalebar_pt,
    source_line,
    text_height,
    text_lines,
    title_box_height,
)
from .page_style import (
    AUBERGINE,
    DAYLIGHT,
    MINIMUM_TYPE,
    PAPER,
    PagePalette,
    Spacing,
    spacing_for,
    type_size,
)

#: Atlas Ledger's sheet number: "3 / 12" on an atlas page. QGIS joins the
#: two atlas variables with `||`, which is NULL -- an empty label -- when
#: the layout has no atlas, so a single page shows nothing rather than
#: "0 / 0"; the cartograph export reads the same expression as
#: `{{atlas.index}} / {{atlas.count}}` (`core/expression_tokens.py`).
ATLAS_SHEET_EXPRESSION = "[% @atlas_featurenumber || ' / ' || @atlas_totalfeatures %]"

#: The longest a corridor frame gets, width to height (or height to width
#: on a portrait page): longer and the map is a ribbon nobody can read.
_CORRIDOR_MAX_ASPECT = 3.2
#: The least a landscape corridor frame gets: room the extent leaves on the
#: sheet lets the band grow this much taller, showing more either side of
#: the route. Twice as wide as tall still reads as a band -- a route along
#: it, not a map round a point -- and on every landscape sheet it takes up
#: nearly all the room under the title, so the page has no empty half.
_CORRIDOR_MIN_ASPECT = 1.8


# --------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------


def _text_width(text: str, font_pt: float, *, bold: bool = False) -> float:
    """The width in mm of `text` on one line at `font_pt` -- the same
    generous character estimate `text_lines` wraps with."""
    em = font_pt * PT_MM * (1.1 if bold else 1.0)
    return sum(_char_em(ch) for ch in text) * em


def _one_line(font_pt: float, line_height: float = TEXT_LINE_HEIGHT) -> float:
    return font_pt * PT_MM * line_height


def _legend_in(
    page: _Page,
    box: Box,
    font_pt: float,
    pal: PagePalette,
    *,
    floor: float = MINIMUM_TYPE.legend,
) -> LegendSpec | None:
    """The legend in `box` at `font_pt`: in the fewest columns its rows fit
    down, long rows wrapped to the columns' width (`_legend_plan`), never
    smaller than `floor`; `None` without layers."""
    size, columns, wrap = _legend_plan(page, font_pt, box, floor)
    return legend_or_none(
        page.data.layer_names,
        box,
        bg=pal.page,
        color=pal.ink,
        font_size=size,
        columns=columns,
        wrap_width=wrap,
        min_h=0.0,
        z=3,
    )


def _strip(
    page: _Page,
    width: float,
    legend_pt: float,
    bar: tuple[float, float],
    g: float,
    room: float,
) -> tuple[float, bool]:
    """A strip `width` mm wide holding the legend and the scale bar (`bar`,
    its width and height), at most `room` mm high: its height, and whether
    the bar goes under the legend (`True`) rather than at its right. The
    bar stays beside a legend that fits there; a legend that does not
    takes the strip's whole width, the bar under it, when that is lower.
    A legend that fits neither way gets all the room (`_legend_plan` then
    sets it smaller, never below the minimum)."""
    sb_w, sb_h = bar
    if not page.data.layer_names:
        return sb_h, False
    beside = max(_legend_need(page, legend_pt, width - sb_w - 2 * g), sb_h)
    if beside <= room:
        return beside, False
    under = _legend_need(page, legend_pt, width) + g + sb_h
    if under <= room or under < beside:
        return min(under, room), True
    return room, False


def _strip_items(
    page: _Page,
    strip: Box,
    legend_pt: float,
    bar: tuple[float, float, float],
    g: float,
    pal: PagePalette,
    *,
    under: bool,
    kind: str = "single",
) -> list[CartographItem]:
    """The legend and the scale bar in `strip`, as `_strip` planned them;
    `bar` is the scale bar's width, height and label size."""
    sb_w, sb_h, bar_pt = bar
    items: list[CartographItem] = []
    if under:
        legend_box = Box(strip.x, strip.y, strip.w, strip.h - sb_h - g)
        bar_box = Box(strip.x, strip.y + strip.h - sb_h, sb_w, sb_h)
    else:
        legend_box = Box(strip.x, strip.y, strip.w - sb_w - 2 * g, strip.h)
        bar_box = Box(strip.x + strip.w - sb_w, strip.y, sb_w, sb_h)
    legend = _legend_in(page, legend_box, legend_pt, pal)
    if legend is None:
        bar_box = Box(strip.x, strip.y, sb_w, sb_h)
    else:
        items.append(legend)
    items.append(_scalebar(bar_box, bar_pt, color=pal.ink, kind=kind))
    return items


def _credits_h(page: _Page, width: float, font_pt: float) -> float:
    """The credits' height: as many lines as they wrap to (most are one);
    0 without credits."""
    return text_height(page.credits, width, font_pt)


def _source(
    page: _Page, at: Box, font_pt: float, palette: PagePalette
) -> TextSpec | None:
    """The credits from `at`'s top left, `at.w` wide and as tall as they
    wrap to, in the palette's muted ink; `None` without credits."""
    height = _credits_h(page, at.w, font_pt)
    if not height:
        return None
    return source_line(
        page.credits,
        Box(at.x, at.y, at.w, height),
        palette=palette,
        font_size=font_pt,
    )


def _scalebar(
    box: Box, font_pt: float, *, color: str | None = None, kind: str = "single"
) -> ScalebarSpec:
    """The main map's scale bar in `box`, its labels `font_pt`, with a
    segment for every 32 mm of it at labels up to 12 pt -- wider ones for
    larger labels -- one to four: QGIS may draw the bar at half the box,
    and its labels need about 16 mm a segment at 12 pt not to run into each
    other. A bar of one segment is drawn as a ticked line, not a solid
    block."""
    segment_w = 32.0 * max(font_pt / 12.0, 1.0)
    segments = max(1, min(4, int(box.w // segment_w)))
    if segments == 1:
        kind = "ticks"
    return ScalebarSpec(
        id="scalebar",
        box=box,
        for_=MAIN_FRAME_ID,
        kind=kind,
        segments=segments,
        color=color,
        font_size=font_pt,
        z=4,
    )


def _scalebar_h(page: _Page, font_pt: float) -> float:
    """A scale bar's box: room for QGIS's bar and its `font_pt` labels, a
    little more on a big sheet."""
    return max(11.0 * min(max(page.scale, 1.0), 1.8), scalebar_height(font_pt))


def _style(palette: PagePalette) -> dict[str, str]:
    return {"bg": palette.page, "color": palette.ink}


def _title(box: Box, size: float, palette: PagePalette) -> TextSpec:
    """The page title: bold, in the palette's ink."""
    return TextSpec(
        id="title",
        box=box,
        body="{{title}}",
        kind="title",
        font_size=size,
        weight="bold",
        color=palette.ink,
        z=2,
    )


# --------------------------------------------------------------------------
# Night Desk
# --------------------------------------------------------------------------


def _night_desk(page: _Page) -> _Layout:
    """A calm dark report sheet: NIKA's aubergine page with generous
    margins, a short brass rule over the title, the map inset as a quiet
    window, a restrained legend and scale bar beside or under it, and the
    source and date under a hairline at the foot."""
    pal = AUBERGINE
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    s = page.scale
    m = round(sp.margin * 1.6, 2)
    g = sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    items: list[CartographItem] = []

    # Header: the brass rule and the title, only with a title to head.
    y = m
    if page.title:
        rule_h = 0.8 * s
        items.append(
            ShapeSpec(id="accent", box=Box(m, y, 14.0 * s, rule_h), fill=pal.accent)
        )
        y += rule_h + 2.5 * s
        title_pt = page.pt("title")
        title_h = title_box_height(page.title, content.w, title_pt, max_lines=3)
        items.append(_title(Box(m, y, content.w, title_h), title_pt, pal))
        y += title_h + 2 * g
    body_top = y

    # Foot: a hairline, then the source at the left and the date at the right.
    credit_pt = type_size("credit", w, h)
    date_w = _text_width("2026-12-31", credit_pt) + 2.0
    credit_w = content.w - date_w - g
    foot_h = max(_credits_h(page, credit_w, credit_pt), _one_line(credit_pt))
    foot_y = h - m - foot_h
    rule_y = foot_y - 0.75 * g
    items.append(
        ShapeSpec(
            id="rule",
            box=Box(m, rule_y, content.w, sp.rule),
            fill=pal.hairline,
        )
    )
    items.append(
        TextSpec(
            id="date",
            box=Box(m + content.w - date_w, foot_y, date_w, _one_line(credit_pt)),
            body="{{date}}",
            font_size=credit_pt,
            color=pal.muted,
            align="end",
            z=3,
        )
    )
    credit = source_line(
        page.credits, Box(m, foot_y, credit_w, foot_h), palette=pal, font_size=credit_pt
    )
    if credit is not None:
        items.append(credit)
    body = Box(m, body_top, content.w, rule_y - g - body_top)

    legend_pt = page.pt("legend")
    bar_pt = scalebar_pt(page)
    sb_h = _scalebar_h(page, bar_pt)
    sb_w = min(55.0 * min(s, 2.0), body.w * 0.4)
    column_w = min(max(body.w * 0.22, 48.0), 75.0 * s)
    room = body.h - sb_h - g
    row_h, under = _strip(
        page, body.w, legend_pt, (sb_w, sb_h), g, legend_room_cap(body.h, g)
    )
    use_column = bool(page.data.layer_names) and (
        _legend_need(page, legend_pt, column_w) <= room
    )
    if use_column:
        # Whichever shows the canvas view larger, the column or the row; a
        # tie keeps the column on a landscape sheet, the row on a portrait.
        column_area = _fit_area(
            page.aspect, Box(0, 0, body.w - column_w - 2 * g, body.h)
        )
        row_area = _fit_area(page.aspect, Box(0, 0, body.w, body.h - row_h - g))
        use_column = column_area > row_area or (
            page.landscape and column_area == row_area
        )
    if use_column:
        # The legend and scale bar in a column right of the map, long rows
        # wrapped to its width.
        frame = Box(body.x, body.y, body.w - column_w - 2 * g, body.h)
        column_x = frame.x + frame.w + 2 * g
        legend_box = Box(
            column_x, body.y, column_w, _legend_need(page, legend_pt, column_w)
        )
        legend = _legend_in(page, legend_box, legend_pt, pal)
        if legend is not None:
            items.append(legend)
        scalebar_box = Box(column_x, body.y + body.h - sb_h, min(sb_w, column_w), sb_h)
        items.append(_scalebar(scalebar_box, bar_pt, color=pal.ink))
    else:
        # A row under the map: the legend, the scale bar at its right -- or
        # under it, for a legend that needs the row's whole width.
        frame = Box(body.x, body.y, body.w, body.h - row_h - g)
        row = Box(body.x, frame.y + frame.h + g, body.w, row_h)
        items += _strip_items(
            page, row, legend_pt, (sb_w, sb_h, bar_pt), g, pal, under=under
        )
    return (
        (
            FrameSlot(
                MAIN_FRAME_ID, frame, frame_bg=pal.panel, frame_stroke=pal.hairline
            ),
        ),
        items,
        _style(pal),
    )


# --------------------------------------------------------------------------
# Marginalia
# --------------------------------------------------------------------------


def _marginalia(page: _Page) -> _Layout:
    """An editorial sheet on warm paper: a large title, a large map, and in
    a narrow outside margin the project's own short observation (its notes)
    at the top, with a compact legend and the scale bar at the margin's
    foot, level with the map's. Without notes there is no margin: the map
    takes the page's width, the legend and scale bar a strip under it."""
    pal = PAPER
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    m = round(sp.margin * 1.2, 2)
    g = sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    items: list[CartographItem] = []

    y = m
    if page.title:
        title_pt = round(page.pt("title") * 1.2, 2)
        title_h = title_box_height(page.title, content.w, title_pt, max_lines=3)
        items.append(_title(Box(m, y, content.w, title_h), title_pt, pal))
        y += title_h + 1.5 * g
    credit_pt = type_size("credit", w, h)
    credit_h = _credits_h(page, content.w, credit_pt)
    bottom = h - m - (credit_h + 0.75 * g if credit_h else 0.0)
    body = Box(m, y, content.w, bottom - y)
    credit = _source(page, Box(m, h - m - credit_h, content.w, 1.0), credit_pt, pal)
    if credit is not None:
        items.append(credit)

    legend_pt = page.pt("legend")
    bar_pt = scalebar_pt(page)
    sb_h = _scalebar_h(page, bar_pt)
    notes = _marginal_note(page, body, sp, pal, sb_h) if page.notes else None
    if notes is None:
        strip, strip_items = _legend_strip(page, body, legend_pt, bar_pt, g, pal)
        frame = Box(body.x, body.y, body.w, strip.y - g - body.y)
        return _marginalia_frames(frame, pal), items + strip_items, _style(pal)

    column, note_items, note_bottom = notes
    items += note_items
    frame = Box(body.x, body.y, column.x - 2 * g - body.x, body.h)
    scalebar_box = Box(column.x, column.y + column.h - sb_h, column.w, sb_h)
    room = scalebar_box.y - g - (note_bottom + 2 * g)
    need = _legend_need(page, legend_pt, column.w)
    if page.data.layer_names and room < need:
        # A legend too long for the margin, even with its long rows wrapped,
        # goes in a strip across the foot of the sheet; the map and the
        # note sit above it.
        strip, strip_items = _legend_strip(page, body, legend_pt, bar_pt, g, pal)
        above = Box(body.x, body.y, body.w, strip.y - g - body.y)
        column, note_items, _ = _marginal_note(page, above, sp, pal, sb_h)
        frame = Box(above.x, above.y, column.x - 2 * g - above.x, above.h)
        items = items[: -len(note_items)] + note_items + strip_items
        return _marginalia_frames(frame, pal), items, _style(pal)
    # The legend sits on the scale bar, so the margin reads note at the top,
    # key at the foot.
    if page.data.layer_names:
        legend = _legend_in(
            page,
            Box(column.x, scalebar_box.y - g - need, column.w, need),
            legend_pt,
            pal,
        )
        if legend is not None:
            items.append(legend)
    items.append(_scalebar(scalebar_box, bar_pt, color=pal.ink))
    return _marginalia_frames(frame, pal), items, _style(pal)


def _marginalia_frames(frame: Box, pal: PagePalette) -> tuple[FrameSlot, ...]:
    return (
        FrameSlot(MAIN_FRAME_ID, frame, frame_bg=pal.page, frame_stroke=pal.hairline),
    )


def _marginal_note(
    page: _Page, body: Box, sp: Spacing, pal: PagePalette, sb_h: float
) -> tuple[Box, list[CartographItem], float]:
    """The outside margin for the page's notes: the column's box, the
    accent rule and the note text, and where the note ends."""
    s = page.scale
    g = sp.gutter
    max_pt = page.pt("subtitle")
    min_pt = max(page.pt("body") * 0.85, MINIMUM_TYPE.body)
    rule_h = 0.7 * s
    text_top = body.y + rule_h + 2.0 * s
    share = 0.27 if not page.landscape else 0.21
    column_w = 0.0
    size = min_pt
    note_h = 0.0
    # Widen the margin, a step at a time, until the note fits its share of
    # the column at a readable size.
    while share <= 0.38:
        column_w = max(body.w * share, 34.0)
        room = (body.y + body.h - text_top) * 0.62
        size = fit_text_size(page.notes, Box(0, 0, column_w, room), max_pt, min_pt)
        note_h = text_height(page.notes, column_w, size)
        if note_h <= room:
            break
        share += 0.03
    room = body.y + body.h - text_top - sb_h - g
    note_h = min(note_h, room)
    column = Box(body.x + body.w - column_w, body.y, column_w, body.h)
    items: list[CartographItem] = [
        ShapeSpec(
            id="accent", box=Box(column.x, body.y, 10.0 * s, rule_h), fill=pal.accent
        ),
        TextSpec(
            id="notes",
            box=Box(column.x, text_top, column_w, note_h),
            body="{{notes}}",
            font_size=size,
            color=pal.ink,
            z=3,
        ),
    ]
    return column, items, text_top + note_h


def _legend_strip(
    page: _Page,
    area: Box,
    legend_pt: float,
    bar_pt: float,
    g: float,
    pal: PagePalette,
) -> tuple[Box, list[CartographItem]]:
    """A strip at the foot of `area`: the legend across it, the scale bar
    at its right (under it, for a legend that needs the strip's width).
    Returns the strip's box and its items."""
    s = page.scale
    sb_w = min(55.0 * min(s, 2.0), area.w * 0.35)
    sb_h = _scalebar_h(page, bar_pt)
    height, under = _strip(
        page, area.w, legend_pt, (sb_w, sb_h), g, legend_room_cap(area.h, g)
    )
    if page.data.layer_names:
        height = max(height, 13.0)
    strip = Box(area.x, area.y + area.h - height, area.w, height)
    items = _strip_items(
        page, strip, legend_pt, (sb_w, sb_h, bar_pt), g, pal, under=under
    )
    return strip, items


# --------------------------------------------------------------------------
# Atlas Ledger
# --------------------------------------------------------------------------


def _atlas_ledger(page: _Page) -> _Layout:
    """One sheet of a map series, ruled like a ledger: a head rule, the
    title with the sheet number at the right (filled in on atlas pages,
    blank on a single page), the map large, and a footer band -- a small
    locator outlining the map's place, the legend, the scale bar -- over
    the source line."""
    pal = DAYLIGHT
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    s = page.scale
    m = sp.margin
    g = sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    rule_h = max(sp.rule * 1.6, 0.5)
    items: list[CartographItem] = [
        ShapeSpec(id="rule-head", box=Box(m, m, content.w, rule_h), fill=pal.rule),
    ]
    title_pt = page.pt("title")
    caption_pt = type_size("caption", w, h)
    sheet_w = _text_width("888 / 888", caption_pt) + 2.0
    y = m + rule_h + 0.75 * g
    title_w = content.w - sheet_w - g
    header_h = max(
        title_box_height(page.title, title_w, title_pt, max_lines=3)
        if page.title
        else 0.0,
        _one_line(caption_pt),
    )
    if page.title:
        items.append(_title(Box(m, y, title_w, header_h), title_pt, pal))
    items.append(
        TextSpec(
            id="sheet",
            box=Box(m + content.w - sheet_w, y, sheet_w, _one_line(caption_pt)),
            body=ATLAS_SHEET_EXPRESSION,
            font_size=caption_pt,
            color=pal.muted,
            align="end",
            z=3,
        )
    )
    y += header_h + 0.75 * g
    if page.title:
        # The hairline closes a header; an untitled page has only the head
        # rule over the sheet number.
        items.append(
            ShapeSpec(
                id="rule-title", box=Box(m, y, content.w, sp.rule), fill=pal.hairline
            )
        )
    body_top = y + sp.rule + g

    credit_pt = type_size("credit", w, h)
    credit_h = _credits_h(page, content.w, credit_pt)
    credit = _source(page, Box(m, h - m - credit_h, content.w, 1.0), credit_pt, pal)
    if credit is not None:
        items.append(credit)
    bottom = h - m - (credit_h + 0.75 * g if credit_h else 0.0)
    body = Box(m, body_top, content.w, bottom - body_top)

    legend_pt = page.pt("legend")
    bar_pt = scalebar_pt(page)
    sb_h = _scalebar_h(page, bar_pt)
    column_w = min(max(body.w * 0.24, 50.0), 85.0 * s)
    locator_h = min(column_w * 0.8, body.h * 0.4)
    column_room = body.h - locator_h - sb_h - 2 * g

    def column() -> tuple[Box, Box, Box, Box]:
        """The index column: locator, legend, scale bar, right of the map."""
        # The index column: locator, legend, scale bar, right of the map.
        frame = Box(body.x, body.y, body.w - column_w - g, body.h)
        cx = frame.x + frame.w + g
        locator = Box(cx, body.y, column_w, locator_h)
        legend_box = Box(cx, locator.y + locator.h + g, column_w, column_room)
        scalebar_box = Box(cx, body.y + body.h - sb_h, min(column_w, 55.0 * s), sb_h)
        return frame, locator, legend_box, scalebar_box

    def band() -> tuple[Box, Box, Box, Box]:
        """The index band under the map: locator, legend, scale bar."""
        # The index band under the map: locator, legend, scale bar. Long
        # layer names or many rows: the scale bar moves under the locator
        # and the legend takes its place -- or the legend runs across the
        # sheet under the two, whichever keeps the band lower.
        least_band = max(body.h * 0.18, 30.0 * min(s, 1.0))
        locator_w = min(least_band * 1.25, body.w * 0.3)
        sb_w = min(max(50.0 * min(s, 2.0), 45.0), body.w * 0.3)
        beside_w = body.w - locator_w - sb_w - 2 * g
        stacked_w = body.w - locator_w - g
        options = [(least_band, "beside")]
        if page.data.layer_names:
            options = [
                (max(least_band, _legend_need(page, legend_pt, beside_w)), "beside"),
                (
                    max(
                        least_band + sb_h + g,
                        _legend_need(page, legend_pt, stacked_w),
                    ),
                    "stacked",
                ),
                (
                    least_band + g + _legend_need(page, legend_pt, body.w),
                    "under",
                ),
            ]
        # min() keeps the first of equals: beside, then stacked.
        band_h, where = min(options, key=lambda option: option[0])
        # The map keeps half the height it shares with the band
        # (`MAP_MIN_SHARE`); entries that still do not fit are left out.
        if where == "under":
            band_h = min(
                band_h,
                least_band + g + legend_room_cap(body.h - least_band - g, g),
            )
        else:
            band_h = min(band_h, max(legend_room_cap(body.h, g), least_band))
        frame = Box(body.x, body.y, body.w, body.h - band_h - g)
        band_y = frame.y + frame.h + g
        locator = Box(body.x, band_y, locator_w, least_band)
        right_bar = Box(body.x + body.w - sb_w, band_y + least_band - sb_h, sb_w, sb_h)
        if where == "stacked":
            legend_box = Box(locator.x + locator_w + g, band_y, stacked_w, band_h)
            scalebar_box = Box(
                body.x, band_y + least_band + g, min(sb_w, locator_w), sb_h
            )
        elif where == "under":
            legend_top = band_y + least_band + g
            legend_box = Box(body.x, legend_top, body.w, band_y + band_h - legend_top)
            scalebar_box = right_bar
        else:
            legend_box = Box(locator.x + locator_w + g, band_y, beside_w, band_h)
            scalebar_box = right_bar
        return frame, locator, legend_box, scalebar_box

    # The column on a landscape sheet and the band on a portrait one, unless
    # the other shows the canvas view larger; the column only when the
    # legend fits it. max() keeps the first of equals.
    arrangements = [band]
    if _legend_need(page, legend_pt, column_w) <= column_room:
        arrangements = [column, band] if page.landscape else [band, column]
    frame, locator, legend_box, scalebar_box = max(
        (arrange() for arrange in arrangements),
        key=lambda option: _fit_area(page.aspect, option[0]),
    )
    legend = _legend_in(page, legend_box, legend_pt, pal)
    if legend is not None:
        items.append(legend)
    items.append(_scalebar(scalebar_box, bar_pt, color=pal.ink))
    return (
        (
            FrameSlot(
                MAIN_FRAME_ID, frame, frame_bg=pal.page, frame_stroke=pal.hairline
            ),
            FrameSlot(
                LOCATOR_FRAME_ID,
                locator,
                overview_of=MAIN_FRAME_ID,
                zoom_out=LOCATOR_ZOOM_OUT,
                frame_bg=pal.page,
                frame_stroke=pal.rule,
            ),
        ),
        items,
        _style(pal),
    )


# --------------------------------------------------------------------------
# Town Hall
# --------------------------------------------------------------------------


def _town_hall(page: _Page) -> _Layout:
    """A large-print sheet for a public meeting or a notice board: a big
    plain title, the map with nothing laid over it, a legend in large type
    with room round every row, the scale bar, and the credits set large
    enough to read from the back of the room."""
    pal = PAPER
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    s = page.scale
    m = round(sp.margin * 1.4, 2)
    g = round(sp.gutter * 1.5, 2)
    content = Box(m, m, w - 2 * m, h - 2 * m)
    items: list[CartographItem] = []

    y = m
    if page.title:
        top_pt = round(page.pt("display") * 0.85, 2)
        least_pt = page.pt("title")
        title_pt = _headline_size(
            page.title,
            content.w,
            fit_text_size(
                page.title,
                Box(m, y, content.w, content.h * 0.2),
                top_pt,
                least_pt,
                bold=True,
                line_height=TITLE_LINE_HEIGHT,
                max_lines=3,
            ),
            least_pt,
        )
        title_h = title_box_height(page.title, content.w, title_pt, max_lines=3)
        items.append(_title(Box(m, y, content.w, title_h), title_pt, pal))
        y += title_h + 2 * g
    credit_pt = round(max(type_size("credit", w, h) * 1.3, 8.0), 2)
    credit_h = _credits_h(page, content.w, credit_pt)
    credit = _source(page, Box(m, h - m - credit_h, content.w, 1.0), credit_pt, pal)
    if credit is not None:
        # Credits a room reads: in the full ink, not the muted one.
        credit = replace(credit, color=pal.ink)
        items.append(credit)
    bottom = h - m - (credit_h + g if credit_h else 0.0)
    body = Box(m, y, content.w, bottom - y)

    # Scale bar labels a room reads too: larger than other designs', never
    # below 9 pt.
    bar_pt = round(max(type_size("caption", w, h) * 1.3, 9.0), 2)
    sb_h = _scalebar_h(page, bar_pt)
    has_legend = bool(page.data.layer_names)
    if not has_legend:
        frame = Box(body.x, body.y, body.w, body.h - sb_h - g)
        items.append(
            _scalebar(
                Box(body.x, frame.y + frame.h + g, min(60.0 * s, body.w * 0.4), sb_h),
                bar_pt,
                color=pal.ink,
            )
        )
        return _town_hall_frames(frame, pal), items, _style(pal)
    large_pt = round(max(page.pt("legend") * 1.6, 11.0), 2)
    least_pt = _town_hall_legend_floor(page)
    column_w = min(max(body.w * 0.28, 62.0), 120.0 * s)
    sb_w = min(60.0 * min(s, 2.0), body.w * 0.32)
    column_room = body.h - sb_h - 2 * g
    row_w = body.w - sb_w - 2 * g
    row_fits = _legend_need(page, least_pt, row_w) <= body.h * 0.4
    column_fits = _legend_need(page, least_pt, column_w) <= column_room
    if row_fits:
        row_room = body.h * 0.4
        row_size = _large_legend(page, row_w, row_room, large_pt, least_pt)
        row_h = max(min(_legend_need(page, row_size, row_w), row_room), sb_h)
        if column_fits:
            # Whichever shows the canvas view larger: the column for a view
            # about as tall as the room beside it, the row for a wider one;
            # a tie keeps the column on a landscape sheet, the row on a
            # portrait one.
            column_frame = Box(body.x, body.y, body.w - column_w - 2 * g, body.h)
            row_frame = Box(body.x, body.y, body.w, body.h - row_h - 2 * g)
            column_area = _fit_area(page.aspect, column_frame)
            row_area = _fit_area(page.aspect, row_frame)
            column_fits = column_area > row_area or (
                page.landscape and column_area == row_area
            )
    else:
        column_fits = column_fits and page.landscape
    if column_fits:
        # A column right of the map, as tall as the map.
        frame = Box(body.x, body.y, body.w - column_w - 2 * g, body.h)
        cx = frame.x + frame.w + 2 * g
        size = _large_legend(page, column_w, column_room, large_pt, least_pt)
        legend_h = min(_legend_need(page, size, column_w), column_room)
        legend_box = Box(cx, body.y, column_w, legend_h)
        scalebar_box = Box(cx, body.y + body.h - sb_h, min(column_w, 60.0 * s), sb_h)
    elif row_fits:
        # A row under the map; a wide map on a landscape sheet keeps the
        # page's whole width this way.
        size, legend_h = row_size, row_h
        frame = Box(body.x, body.y, body.w, body.h - legend_h - 2 * g)
        row_y = frame.y + frame.h + 2 * g
        legend_box = Box(body.x, row_y, row_w, legend_h)
        scalebar_box = Box(body.x + body.w - sb_w, row_y, sb_w, sb_h)
    else:
        # Long layer names or many rows: the scale bar under the map, then
        # the legend across the page's whole width, the map giving way until
        # it is as tall as the legend (`MAP_MIN_SHARE`) -- never the type:
        # what does not fit at the large-print floor is left out.
        room = legend_room_cap(body.h - sb_h - 2 * g, g)
        size = _large_legend(page, body.w, room, large_pt, least_pt)
        legend_h = min(_legend_need(page, size, body.w), room)
        frame = Box(body.x, body.y, body.w, body.h - sb_h - legend_h - 3 * g)
        scalebar_box = Box(body.x, frame.y + frame.h + 2 * g, sb_w, sb_h)
        legend_box = Box(body.x, scalebar_box.y + sb_h + g, body.w, legend_h)
    legend = _legend_in(page, legend_box, size, pal, floor=least_pt)
    if legend is not None:
        items.append(legend)
    items.append(_scalebar(scalebar_box, bar_pt, color=pal.ink))
    return _town_hall_frames(frame, pal), items, _style(pal)


#: Town Hall's large-print floor for legend rows, in points: never smaller
#: on any sheet. 9 pt is the size its scale bar labels never go below, so
#: the whole sheet reads at one minimum -- about 1.4 times the 6.5 pt floor
#: of the other designs, a size people read at arm's length or more.
_TOWN_HALL_LEAST_PT = 9.0


def _town_hall_legend_floor(page: _Page) -> float:
    """Town Hall's readable floor for legend rows on `page`: 15 % above the
    page's ordinary legend size, growing with the sheet as the room it is
    read across does, and never below `_TOWN_HALL_LEAST_PT`."""
    return round(
        max(page.pt("legend") * 1.15, _TOWN_HALL_LEAST_PT),
        2,
    )


def _headline_size(text: str, width: float, size: float, least: float) -> float:
    """`size`, or a little smaller (to 85 %, never below `least`) when that
    saves the headline a line -- no lone last word on a line of its own."""
    lines = text_lines(text, width, size, bold=True)
    step = size
    while lines > 1 and step > max(least, size * 0.85):
        step = round(step - 0.5, 2)
        if text_lines(text, width, step, bold=True) < lines:
            return step
    return size


def _large_legend(
    page: _Page, width: float, height: float, large: float, least: float
) -> float:
    """The largest legend size from `large` down to `least` at which every
    row fits `height` across `width` (long rows wrapped); `least` when none
    does."""
    size = large
    while size > least and _legend_need(page, size, width) > height:
        size = max(least, round(size - 0.5, 2))
    return size


def _town_hall_frames(frame: Box, pal: PagePalette) -> tuple[FrameSlot, ...]:
    return (FrameSlot(MAIN_FRAME_ID, frame, frame_bg=pal.page, frame_stroke=pal.rule),)


# --------------------------------------------------------------------------
# Wayfinder
# --------------------------------------------------------------------------


def _wayfinder(page: _Page) -> _Layout:
    """A corridor sheet for a route, river, coast or pipeline: the map as a
    long band across the page (down it, on a portrait page), a compact
    strip beside it with the legend and a long distance scale, the title
    above and the source under. The band follows the extent's own
    proportions, held between the page's shape and 3.2 : 1, so a round
    extent still gets a usable map. The title keeps the top margin and the
    source the foot: room a long band leaves on a landscape sheet lets the
    band grow taller, up to 2 : 1, padding the route with its surroundings,
    and only what is left past that opens over the source line; a narrow
    band on a portrait sheet is centred across it."""
    if page.landscape:
        return _wayfinder_across(page)
    return _wayfinder_down(page)


def _wayfinder_across(page: _Page) -> _Layout:
    pal = DAYLIGHT
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    s = page.scale
    m, g = sp.margin, sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    title_pt = page.pt("title")
    indent = _wayfinder_indent(page, g)
    title_h = (
        title_box_height(page.title, content.w - indent, title_pt, max_lines=3)
        if page.title
        else 0.0
    )
    head = title_h + 1.5 * g if title_h else 0.0
    credit_pt = type_size("credit", w, h)
    credit_h = _credits_h(page, content.w, credit_pt)
    foot = credit_h + 0.75 * g if credit_h else 0.0
    legend_pt = page.pt("legend")
    bar_pt = scalebar_pt(page)
    sb_h = _scalebar_h(page, bar_pt)
    sb_w = min(max(content.w * 0.3, 60.0), 140.0 * s)
    # The legend beside the distance scale, or across the strip with the
    # scale under it when that keeps the strip lower.
    strip_h, under = _strip(
        page,
        content.w,
        legend_pt,
        (sb_w, sb_h),
        g,
        legend_room_cap(content.h - head - foot, g),
    )
    if page.data.layer_names:
        strip_h = max(strip_h, 13.0)
    area = Box(m, m + head, content.w, content.h - head - strip_h - g - foot)
    page_shape = area.w / area.h
    frame = frame_for_extent(
        page.aspect,
        area,
        min_aspect=page_shape,
        max_aspect=max(_CORRIDOR_MAX_ASPECT, page_shape),
        align="start",
    )
    # A band lower than the room grows taller, showing more either side of
    # the route, but stays a band; what room is left opens over the source
    # line, which keeps the sheet's foot -- or, with no title to hang it
    # from, is shared above and below, the band and its strip centred.
    least_shape = max(_CORRIDOR_MIN_ASPECT, page_shape)
    band_h = max(frame.h, frame.w / least_shape)
    top = frame.y if page.title else frame.y + max(area.h - band_h, 0.0) / 2
    frame = Box(frame.x, top, frame.w, band_h)
    strip_y = frame.y + frame.h + g
    items = _wayfinder_title(page, m, m, content.w, title_h, title_pt)
    items += _strip_items(
        page,
        Box(m, strip_y, content.w, strip_h),
        legend_pt,
        (sb_w, sb_h, bar_pt),
        g,
        pal,
        under=under,
        kind="ticks",
    )
    credit = _source(page, Box(m, h - m - credit_h, content.w, 1.0), credit_pt, pal)
    if credit is not None:
        items.append(credit)
    return _wayfinder_frames(frame), items, _style(pal)


def _wayfinder_down(page: _Page) -> _Layout:
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    s = page.scale
    m, g = sp.margin, sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    legend_pt = page.pt("legend")
    bar_pt = scalebar_pt(page)
    sb_h = _scalebar_h(page, bar_pt)
    column_w = min(max(content.w * 0.3, 46.0), 80.0 * s)
    if not page.data.layer_names:
        return _wayfinder_column(page, column_w, legend_pt, bar_pt)
    # Wider, up to as wide as the band beside it (`MAP_MIN_SHARE`), for
    # long layer names.
    widest = legend_room_cap(content.w, g)
    column_w = max(column_w, min(_legend_width(page, legend_pt), widest))
    for width in (column_w, widest):
        frame_h = _wayfinder_sheet(page, width)[0].h
        if _legend_need(page, legend_pt, width) <= frame_h - sb_h - g:
            return _wayfinder_column(page, width, legend_pt, bar_pt)
    # A legend longer than the band even half the sheet wide: the strip
    # under the band, as on a landscape sheet.
    return _wayfinder_across(page)


def _wayfinder_sheet(page: _Page, column_w: float) -> tuple[Box, float, float, float]:
    """A portrait Wayfinder's band beside a `column_w` mm column: the
    frame, the sheet's width (band and column), and the title's height and
    size over it."""
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    m, g = sp.margin, sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    full_pt = page.pt("title")
    indent = _wayfinder_indent(page, g)
    credit_pt = type_size("credit", w, h)
    # The title spans the sheet, which is as wide as the band and its
    # column; a longer title makes the band shorter, and so narrower: settle
    # the two together.
    sheet_w = content.w
    title_h = 0.0
    title_pt = full_pt
    frame = content
    for _ in range(6):
        # A long title over a narrow sheet steps down a little to stay
        # within three lines.
        title_pt = fit_text_size(
            page.title,
            Box(0.0, 0.0, sheet_w - indent, 3 * _one_line(full_pt, TITLE_LINE_HEIGHT)),
            full_pt,
            max(MINIMUM_TYPE.title, full_pt * 0.75),
            bold=True,
            line_height=TITLE_LINE_HEIGHT,
            max_lines=3,
        )
        title_h = (
            title_box_height(page.title, sheet_w - indent, title_pt, max_lines=3)
            if page.title
            else 0.0
        )
        head = title_h + 1.5 * g if title_h else 0.0
        credit_h = _credits_h(page, sheet_w, credit_pt)
        foot = credit_h + 0.75 * g if credit_h else 0.0
        area = Box(m, m + head, content.w - column_w - g, content.h - head - foot)
        page_shape = area.w / area.h
        frame = frame_for_extent(
            page.aspect,
            area,
            min_aspect=min(1 / _CORRIDOR_MAX_ASPECT, page_shape),
            max_aspect=page_shape,
            align="start",
        )
        if math.isclose(frame.w + g + column_w, sheet_w, abs_tol=0.01):
            break
        sheet_w = frame.w + g + column_w
    return frame, sheet_w, title_h, title_pt


def _wayfinder_column(
    page: _Page, column_w: float, legend_pt: float, bar_pt: float
) -> _Layout:
    """A portrait Wayfinder: the band down the sheet, the legend and the
    distance scale in a `column_w` mm column at its right, the sheet
    centred across the page."""
    pal = DAYLIGHT
    w, h = page.width, page.height
    sp = spacing_for(w, h)
    m, g = sp.margin, sp.gutter
    content = Box(m, m, w - 2 * m, h - 2 * m)
    credit_pt = type_size("credit", w, h)
    sb_h = _scalebar_h(page, bar_pt)
    frame, sheet_w, title_h, title_pt = _wayfinder_sheet(page, column_w)
    left = m + (content.w - sheet_w) / 2
    frame = Box(left, frame.y, frame.w, frame.h)
    cx = frame.x + frame.w + g
    items = _wayfinder_title(page, left, m, sheet_w, title_h, title_pt)
    if page.data.layer_names:
        legend_h = min(_legend_need(page, legend_pt, column_w), frame.h - sb_h - g)
        legend = _legend_in(page, Box(cx, frame.y, column_w, legend_h), legend_pt, pal)
        if legend is not None:
            items.append(legend)
    items.append(
        _scalebar(
            Box(cx, frame.y + frame.h - sb_h, column_w, sb_h),
            bar_pt,
            color=pal.ink,
            kind="ticks",
        )
    )
    credit = _source(
        page, Box(left, frame.y + frame.h + 0.75 * g, sheet_w, 1.0), credit_pt, pal
    )
    if credit is not None:
        items.append(credit)
    return _wayfinder_frames(frame), items, _style(pal)


def _wayfinder_indent(page: _Page, g: float) -> float:
    """How far the title sits in from its brass route mark."""
    return 1.2 * page.scale + 0.75 * g


def _wayfinder_title(
    page: _Page, x: float, y: float, width: float, title_h: float, title_pt: float
) -> list[CartographItem]:
    """The title at `x`, `y`, `width` wide, behind a short brass mark the
    height of its first line, like a waymark; nothing without a title."""
    if not page.title:
        return []
    indent = _wayfinder_indent(page, spacing_for(page.width, page.height).gutter)
    box = Box(x, y, width, title_h)
    pal = DAYLIGHT
    return [
        ShapeSpec(
            id="accent",
            box=Box(
                box.x, box.y, 1.2 * page.scale, _one_line(title_pt, TITLE_LINE_HEIGHT)
            ),
            fill=pal.accent,
        ),
        _title(Box(box.x + indent, box.y, box.w - indent, box.h), title_pt, pal),
    ]


def _wayfinder_frames(frame: Box) -> tuple[FrameSlot, ...]:
    return (
        FrameSlot(
            MAIN_FRAME_ID, frame, frame_bg=DAYLIGHT.page, frame_stroke=DAYLIGHT.rule
        ),
    )


NEW_TEMPLATES: tuple[LayoutTemplate, ...] = (
    LayoutTemplate(
        "night-desk",
        N_("Night Desk"),
        N_(
            "A calm dark report sheet: the map inset with wide margins, a quiet legend."
        ),
        _night_desk,
        category=ESSENTIAL,
        includes=(N_("legend"), N_("scale bar"), N_("source line"), N_("date")),
    ),
    LayoutTemplate(
        "marginalia",
        N_("Marginalia"),
        N_("An editorial sheet: a large map with your notes in a narrow margin."),
        _marginalia,
        category=CHARACTERFUL,
        includes=(N_("notes"), N_("legend"), N_("scale bar"), N_("source line")),
    ),
    LayoutTemplate(
        "atlas-ledger",
        N_("Atlas Ledger"),
        N_("One sheet of a map series: locator, sheet number, legend and source."),
        _atlas_ledger,
        category=CHARACTERFUL,
        includes=(
            N_("locator"),
            N_("sheet number"),
            N_("legend"),
            N_("scale bar"),
            N_("source line"),
        ),
    ),
    LayoutTemplate(
        "town-hall",
        N_("Town Hall"),
        N_("Large print for public meetings: a big title and an easy-to-read legend."),
        _town_hall,
        category=CHARACTERFUL,
        includes=(N_("large legend"), N_("scale bar"), N_("source line")),
        legend_floor=_town_hall_legend_floor,
    ),
    LayoutTemplate(
        "wayfinder",
        N_("Wayfinder"),
        N_("A long map for routes, rivers and coasts, with a distance scale."),
        _wayfinder,
        category=CHARACTERFUL,
        includes=(N_("legend strip"), N_("distance scale"), N_("source line")),
    ),
)


# Join the collection: `layout_templates` imports this module at its end, so
# `TEMPLATES`, `get_template()` and `template_ids()` list these five after
# its ten, whichever of the two modules was imported first.
if not any(
    template.id == NEW_TEMPLATES[0].id for template in _layout_templates.TEMPLATES
):
    _layout_templates.TEMPLATES = _layout_templates.TEMPLATES + NEW_TEMPLATES


__all__ = ["ATLAS_SHEET_EXPRESSION", "NEW_TEMPLATES"]
