"""Page style tokens: the named colours, type sizes and spacing a layout
template's page artwork is drawn with.

These are the PAGE's colours -- the paper, its text, panels, plates and
rules a template puts round the user's map -- never the dialog's chrome
(that is `ui/theme.py`) and never the map's own symbology, which stays the
user's. A page is a QGIS layout the user goes on editing, so every value is
a plain CSS colour (`#rrggbb`, or `rgba()` for a see-through veil) that
lands in the layout as an ordinary item colour.

Pure Python, no `qgis` or Qt import: `core/layout_templates.py` plans pages
with it, and tests check every palette's contrast without a QGIS.

Three kinds of token:

* `PagePalette` -- one named colour set. `DAYLIGHT` and `PAPER` are the
  light family, `GRAPHITE` and `AUBERGINE` the dark one; `PALETTES` names
  them all.
* `TypeScale` -- text sizes in points for each role at A4, grown with the
  page by `page_scale` and never below `MINIMUM_TYPE` (so A5 never gets
  unreadable text). `type_size(role, width, height)` does both.
* `Spacing` -- margins, gutters and padding in millimetres for a page,
  from `spacing_for(width, height)`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The page every size is relative to: A4's short side, in mm.
A4_SHORT_SIDE = 210.0
#: How far `page_scale` shrinks below A4 (A5 is 0.705) or grows above it.
MIN_PAGE_SCALE = 0.7
MAX_PAGE_SCALE = 4.0


@dataclass(frozen=True)
class PagePalette:
    """One named set of page colours.

    `ink` is body and title text on `page`; `muted` secondary text (dates,
    captions, credits) on `page`. `panel` is an OPAQUE surface for text that
    must read whatever is under it -- text over a map belongs on it -- with
    `panel_ink` on top. `veil` is a see-through panel (an `rgba()` colour)
    for designs that let the map show through; `plate` and `plate_opacity`
    are the same idea as a shape fill and its opacity, behind a scale bar
    over the map. `rule` draws borders and rules that must be seen, and
    `hairline` quiet dividers that need not be. `accent` is the one
    measured highlight colour, for marks and short rules rather than body
    text. `band` and `band_ink` are a solid title band and its text, and
    `tile` a light (or dark) block under `ink`.
    """

    name: str
    dark: bool
    page: str
    ink: str
    muted: str
    panel: str
    panel_ink: str
    veil: str
    plate: str
    plate_opacity: float
    rule: str
    hairline: str
    accent: str
    band: str
    band_ink: str
    tile: str


#: The everyday light sheet: white paper, aubergine ink (the colour the
#: templates have always drawn rules and borders in).
DAYLIGHT = PagePalette(
    name="daylight",
    dark=False,
    page="#ffffff",
    ink="#2a1f2d",
    muted="#5e5263",
    panel="#ffffff",
    panel_ink="#2a1f2d",
    veil="rgba(255, 255, 255, 0.88)",
    plate="#ffffff",
    plate_opacity=0.88,
    rule="#2a1f2d",
    hairline="#d9cfdc",
    accent="#9a5a12",
    band="#2a1f2d",
    band_ink="#ffffff",
    tile="#f1ebf1",
)

#: A warmer light sheet on NIKA's paper colour.
PAPER = PagePalette(
    name="paper",
    dark=False,
    page="#f8f4ef",
    ink="#261a29",
    muted="#66586a",
    panel="#fffdf9",
    panel_ink="#261a29",
    veil="rgba(255, 253, 249, 0.9)",
    plate="#fffdf9",
    plate_opacity=0.9,
    rule="#261a29",
    hairline="#ddd3cc",
    accent="#9a5a12",
    band="#261a29",
    band_ink="#f7ece6",
    tile="#efe7df",
)

#: A neutral near-black sheet: the dark full-bleed design's colours.
GRAPHITE = PagePalette(
    name="graphite",
    dark=True,
    page="#14161a",
    ink="#e8e6e3",
    muted="#a9a6a2",
    panel="#1d2026",
    panel_ink="#e8e6e3",
    veil="rgba(20, 22, 26, 0.82)",
    plate="#14161a",
    plate_opacity=0.82,
    rule="#a9a6a2",
    hairline="#3a3e46",
    accent="#eba941",
    band="#1d2026",
    band_ink="#e8e6e3",
    tile="#1d2026",
)

#: NIKA's own dark: aubergine paper, blush ink, brass accent.
AUBERGINE = PagePalette(
    name="aubergine",
    dark=True,
    page="#261a29",
    ink="#f7ece6",
    muted="#c4b2bb",
    panel="#2f2134",
    panel_ink="#f7ece6",
    veil="rgba(38, 26, 41, 0.85)",
    plate="#261a29",
    plate_opacity=0.85,
    rule="#c4b2bb",
    hairline="#4b3450",
    accent="#eba941",
    band="#2f2134",
    band_ink="#f7ece6",
    tile="#2f2134",
)

PALETTES: dict[str, PagePalette] = {
    palette.name: palette for palette in (DAYLIGHT, PAPER, GRAPHITE, AUBERGINE)
}


def palette(name: str) -> PagePalette:
    """The palette called `name`; `ValueError` for any other."""
    try:
        return PALETTES[name]
    except KeyError:
        raise ValueError(
            f"page palette {name!r} is not one of {tuple(PALETTES)}"
        ) from None


# --------------------------------------------------------------------------
# Type
# --------------------------------------------------------------------------

#: The text roles a page has, largest first.
TYPE_ROLES = ("display", "title", "subtitle", "body", "caption", "legend", "credit")


@dataclass(frozen=True)
class TypeScale:
    """A size in points for each text role. `display` is a poster headline,
    `title` an ordinary page title, `subtitle` the line under it, `body`
    running text (notes, observations), `caption` short labels and title
    block lines, `legend` legend rows, `credit` the data credits line."""

    display: float
    title: float
    subtitle: float
    body: float
    caption: float
    legend: float
    credit: float

    def size(self, role: str) -> float:
        if role not in TYPE_ROLES:
            raise ValueError(f"text role {role!r} is not one of {TYPE_ROLES}")
        return float(getattr(self, role))


#: Sizes on an A4 page.
TYPE_A4 = TypeScale(
    display=32.0,
    title=18.0,
    subtitle=11.0,
    body=9.0,
    caption=8.0,
    legend=8.0,
    credit=7.0,
)

#: The smallest each role is ever set, whatever the page: on A5 the A4
#: sizes shrink by `page_scale` but stop here, so body text stays 7 pt and
#: captions 6.5 pt -- readable in print.
MINIMUM_TYPE = TypeScale(
    display=18.0,
    title=12.0,
    subtitle=8.0,
    body=7.0,
    caption=6.5,
    legend=6.5,
    credit=6.0,
)


def page_scale(width: float, height: float) -> float:
    """How much bigger than A4 a `width` x `height` mm page is, by its short
    side, within `MIN_PAGE_SCALE`..`MAX_PAGE_SCALE`: 1.0 on A4, about 0.7
    on A5, 2.83 on A1."""
    return max(MIN_PAGE_SCALE, min(MAX_PAGE_SCALE, min(width, height) / A4_SHORT_SIDE))


def type_size(
    role: str, width: float, height: float, scale: TypeScale = TYPE_A4
) -> float:
    """The size in points for `role` on a `width` x `height` mm page:
    `scale`'s A4 size grown or shrunk with the page, never below
    `MINIMUM_TYPE`."""
    return round(
        max(scale.size(role) * page_scale(width, height), MINIMUM_TYPE.size(role)), 2
    )


# --------------------------------------------------------------------------
# Spacing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Spacing:
    """Millimetres for one page: `margin` round the page's edge, `gutter`
    between pieces, `pad` inside a panel or plate round its text, and
    `rule` the width of a rule or border line."""

    margin: float
    gutter: float
    pad: float
    rule: float


#: Spacing on A4, and the least any page gets (A5's margin stays clear of
#: what most printers cannot reach).
SPACING_A4 = Spacing(margin=10.0, gutter=4.0, pad=3.0, rule=0.35)
MINIMUM_SPACING = Spacing(margin=7.0, gutter=3.0, pad=2.0, rule=0.25)


def spacing_for(width: float, height: float) -> Spacing:
    """`SPACING_A4` grown or shrunk with the page by `page_scale`, never
    below `MINIMUM_SPACING`."""
    scale = page_scale(width, height)
    return Spacing(
        *(
            round(max(base * scale, least), 2)
            for base, least in (
                (SPACING_A4.margin, MINIMUM_SPACING.margin),
                (SPACING_A4.gutter, MINIMUM_SPACING.gutter),
                (SPACING_A4.pad, MINIMUM_SPACING.pad),
                (SPACING_A4.rule, MINIMUM_SPACING.rule),
            )
        )
    )


# --------------------------------------------------------------------------
# Colour arithmetic (for checks and for designs choosing ink)
# --------------------------------------------------------------------------

RGBA = tuple[float, float, float, float]

_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)"
)


def parse_color(css: str) -> RGBA | None:
    """`#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()` or `rgba()` as 0-1 floats;
    `None` for anything else."""
    text = css.strip()
    match = _HEX_RE.fullmatch(text)
    if match is not None:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        if len(digits) == 6:
            digits += "ff"
        r, g, b, a = (int(digits[i : i + 2], 16) / 255 for i in range(0, 8, 2))
        return (r, g, b, a)
    match = _RGB_RE.fullmatch(text)
    if match is not None:
        r, g, b = (min(int(match.group(i)), 255) / 255 for i in (1, 2, 3))
        alpha = float(match.group(4)) if match.group(4) is not None else 1.0
        return (r, g, b, max(0.0, min(alpha, 1.0)))
    return None


def composite(top: RGBA, below: RGBA, opacity: float = 1.0) -> RGBA:
    """`top` painted at its own alpha times `opacity` over an opaque `below`."""
    alpha = top[3] * opacity
    return (
        top[0] * alpha + below[0] * (1 - alpha),
        top[1] * alpha + below[1] * (1 - alpha),
        top[2] * alpha + below[2] * (1 - alpha),
        1.0,
    )


def relative_luminance(color: RGBA) -> float:
    """WCAG 2 relative luminance of an opaque colour."""

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGBA, b: RGBA) -> float:
    """WCAG 2 contrast between two opaque colours, 1 to 21."""
    lighter, darker = sorted(
        (relative_luminance(a), relative_luminance(b)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


__all__ = [
    "AUBERGINE",
    "DAYLIGHT",
    "GRAPHITE",
    "MINIMUM_SPACING",
    "MINIMUM_TYPE",
    "PALETTES",
    "PAPER",
    "SPACING_A4",
    "TYPE_A4",
    "TYPE_ROLES",
    "PagePalette",
    "Spacing",
    "TypeScale",
    "composite",
    "contrast_ratio",
    "page_scale",
    "palette",
    "parse_color",
    "relative_luminance",
    "spacing_for",
    "type_size",
]
