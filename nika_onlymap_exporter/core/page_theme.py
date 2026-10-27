"""The page themes as colours a QGIS layout can wear.

The cartograph runtime names five themes (`default`, `dark`, `sepia`,
`blueprint`, `minimal`; `core/cartograph_ir.py`). Chosen under "Page settings"
they restyle the PAGE and its furniture in the QGIS layout itself - page
background, text, legend, scale bar, north arrow, frames and rules - so the
preview, the PDF and the web page all show the same thing. Map symbols and
raster colours are never part of a theme.

The colours are the runtime's own (`cartograph.css`), except that every text
colour is pulled just far enough toward black or white to read at WCAG AA
(4.5:1) on the surface it sits on: two of the runtime's own greys (the
`minimal` attribution, the `sepia` muted ink) are below that.

Pure Python, no `qgis` or Qt import.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from .page_style import RGBA, contrast_ratio, parse_color

#: The contrast body text must reach on its surface (WCAG AA).
MINIMUM_CONTRAST = 4.5


def _hex(color: RGBA) -> str:
    r, g, b = (round(max(0.0, min(channel, 1.0)) * 255) for channel in color[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def readable(ink: str, surface: str, minimum: float = MINIMUM_CONTRAST) -> str:
    """`ink` as it is when it already reads on `surface`; otherwise moved toward
    white on a dark surface, black on a light one, until it does."""
    text, behind = parse_color(ink), parse_color(surface)
    if text is None or behind is None:
        return ink
    if contrast_ratio(text, behind) >= minimum:
        return ink
    dark_surface = contrast_ratio(behind, (0.0, 0.0, 0.0, 1.0)) < contrast_ratio(
        behind, (1.0, 1.0, 1.0, 1.0)
    )
    target = 1.0 if dark_surface else 0.0
    for step in range(1, 21):
        mix = step / 20
        moved = tuple(c + (target - c) * mix for c in text[:3])
        candidate: RGBA = (moved[0], moved[1], moved[2], 1.0)
        if contrast_ratio(candidate, behind) >= minimum:
            return _hex(candidate)
    return _hex((target, target, target, 1.0))


@dataclass(frozen=True)
class PageTheme:
    """One theme's colours.

    `page` is the paper. `ink` is body text and `title` the title, both on
    `page`; `muted` is secondary text (the attribution) on `page`. `panel` is
    the surface of a legend, a plate or a map frame, with `panel_ink` text on
    it. `rule` strokes frames and rules; `line_art` draws the scale bar and the
    north arrow; `plate_fill` fills a decorative plate.
    """

    id: str
    page: str
    ink: str
    title: str
    muted: str
    panel: str
    panel_ink: str
    rule: str
    line_art: str
    plate_fill: str
    dark: bool


def _theme(
    theme_id: str,
    *,
    page: str,
    ink: str,
    title: str,
    muted: str,
    panel: str,
    panel_ink: str,
    rule: str,
    line_art: str,
    plate_fill: str,
    dark: bool,
) -> PageTheme:
    return PageTheme(
        id=theme_id,
        page=page,
        ink=readable(ink, page),
        title=readable(title, page),
        muted=readable(muted, page),
        panel=panel,
        panel_ink=readable(panel_ink, panel),
        rule=rule,
        line_art=readable(line_art, page, 3.0),
        plate_fill=plate_fill,
        dark=dark,
    )


#: Colours from the runtime's `om-cartograph[theme=...]` rules.
THEMES: dict[str, PageTheme] = {
    "dark": _theme(
        "dark",
        page="#261a29",
        ink="#d7bfb5",
        title="#f7ece6",
        muted="#b996a6",
        panel="#2f2134",
        panel_ink="#f7ece6",
        rule="#4b3450",
        line_art="#f7ece6",
        plate_fill="#3b2a41",
        dark=True,
    ),
    "sepia": _theme(
        "sepia",
        page="#f4ead5",
        ink="#3a2c1a",
        title="#2a1f0e",
        muted="#6b5638",
        panel="#fbf4e3",
        panel_ink="#3a2c1a",
        rule="#5c4528",
        line_art="#3a2c1a",
        plate_fill="#e8dcc1",
        dark=False,
    ),
    "blueprint": _theme(
        "blueprint",
        page="#0d2e5c",
        ink="#cfe3ff",
        title="#ffffff",
        muted="#7fa6da",
        panel="#11366b",
        panel_ink="#ffffff",
        rule="#cfe3ff",
        line_art="#cfe3ff",
        plate_fill="#11366b",
        dark=True,
    ),
    "minimal": _theme(
        "minimal",
        page="#fafafa",
        ink="#444444",
        title="#111111",
        muted="#999999",
        panel="#ffffff",
        panel_ink="#222222",
        rule="#bbbbbb",
        line_art="#444444",
        plate_fill="#ffffff",
        dark=False,
    ),
}


def theme_colours(theme_id: str) -> PageTheme | None:
    """The colours of a theme, or None for `default` (the layout as it was
    made) and any id this version does not know."""
    return THEMES.get(theme_id)


def surface_contrast(ink: str, surface: str) -> float:
    """The contrast ratio of two CSS colours (1 when either is not one)."""
    text, behind = parse_color(ink), parse_color(surface)
    if text is None or behind is None:
        return 1.0
    return contrast_ratio(text, behind)
