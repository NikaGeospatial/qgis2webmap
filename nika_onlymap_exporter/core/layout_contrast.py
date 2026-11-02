"""Whether a layout's text stays readable in the HTML: its colour against the
page, frame or item behind it, checked to WCAG 2 contrast and reported.

Split out of `core/layout_reader.py`, which calls `_record_low_contrast_text`
once the furniture is read.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .cartograph_ir import (
    Box,
    CartographItem,
    ImageSpec,
    LegendSpec,
    ShapeSpec,
    TextSpec,
)
from .fidelity_report import FidelityReportBuilder
from .layout_reading import MapFrameReading

#: What the runtime's default theme paints where nothing else does
#: (`cartograph.css`: `--om-carto-bg`, `--om-carto-frame-bg`,
#: `--om-carto-legend-bg`, `--om-carto-shape-fill`). The cartograph is always
#: written in that theme, so these are what a page without its own colours
#: shows in the browser.
_THEME_PAGE_BG = "#fbf8fb"
_THEME_FRAME_BG = "#f1ebf1"
_THEME_LEGEND_BG = "#fbf8fb"
_THEME_SHAPE_FILL = "#f0d9a5"

#: WCAG 2 contrast minimums: large text (18 pt, or 14 pt bold) and body text.
_LARGE_TEXT_CONTRAST = 3.0
_BODY_TEXT_CONTRAST = 4.5

_HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")

_RGBA = tuple[float, float, float, float]


def _parse_hex(css: str | None) -> _RGBA | None:
    """`#rgb`, `#rrggbb` or `#rrggbbaa` (the forms this reader writes) as
    0-1 floats; `None` for anything else, which the contrast check skips."""
    if css is None:
        return None
    match = _HEX_COLOR_RE.fullmatch(css.strip())
    if match is None:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) == 6:
        digits += "ff"
    r, g, b, a = (int(digits[i : i + 2], 16) / 255 for i in range(0, 8, 2))
    return (r, g, b, a)


def _over(top: _RGBA, below: _RGBA, opacity: float = 1.0) -> _RGBA:
    """`top` painted at `opacity` over an opaque `below`."""
    alpha = top[3] * opacity
    return (
        top[0] * alpha + below[0] * (1 - alpha),
        top[1] * alpha + below[1] * (1 - alpha),
        top[2] * alpha + below[2] * (1 - alpha),
        1.0,
    )


def _luminance(color: _RGBA) -> float:
    """WCAG 2 relative luminance."""

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: _RGBA, b: _RGBA) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _contains_centre(box: Box, of: Box) -> bool:
    cx, cy = of.x + of.w / 2, of.y + of.h / 2
    return box.x <= cx <= box.x + box.w and box.y <= cy <= box.y + box.h


def _text_background(
    text: TextSpec,
    below: Sequence[CartographItem | MapFrameReading],
    page_bg: str | None,
) -> tuple[_RGBA, str] | None:
    """The colour the browser shows behind `text`, and what paints it
    (`page`, `frame`, `shape`, `legend`, `text`), or `None` when that is a
    picture (an image or a static frame's raster) no colour check can judge.

    `below` is every item drawn before `text`, bottom first. Each one whose
    box holds the text's centre paints over the colour so far. A live frame
    counts as its `frame-bg` -- what it shows before its map draws, or in a
    browser that cannot draw it -- which is the very case where light text
    over a map ends up on a pale box.
    """
    color = _parse_hex(page_bg or _THEME_PAGE_BG)
    if color is None:
        return None
    color = _over(color, (1.0, 1.0, 1.0, 1.0))
    surface = "page"
    for item in below:
        if getattr(item, "hidden", False) or not _contains_centre(item.box, text.box):
            continue
        if isinstance(item, MapFrameReading):
            if item.mode == "static":
                return None
            fill = _parse_hex(item.frame_bg or _THEME_FRAME_BG)
            if fill is None:
                return None
            color, surface = _over(fill, color), "frame"
        elif isinstance(item, ImageSpec):
            return None
        elif isinstance(item, ShapeSpec) and item.kind in ("rect", "ellipse"):
            opacity = 1.0 if item.fill_opacity is None else item.fill_opacity
            fill = _parse_hex(item.fill or _THEME_SHAPE_FILL)
            if fill is None:
                return None
            if opacity > 0 and fill[3] > 0:
                color, surface = _over(fill, color, opacity), "shape"
        elif isinstance(item, LegendSpec):
            fill = _parse_hex(item.bg or _THEME_LEGEND_BG)
            if fill is None:
                return None
            color, surface = _over(fill, color), "legend"
        elif isinstance(item, TextSpec) and item.bg is not None:
            fill = _parse_hex(item.bg)
            if fill is None:
                return None
            color, surface = _over(fill, color), "text"
    if text.bg is not None:
        fill = _parse_hex(text.bg)
        if fill is None:
            return None
        if fill[3] > 0:
            color, surface = _over(fill, color), "own"
    return color, surface


def _is_large_text(text: TextSpec) -> bool:
    """WCAG's large text: 18 pt, or 14 pt bold. A title without its own size
    takes the theme's title size, which is large."""
    if text.font_size is None:
        return text.kind == "title"
    bold = text.weight == "bold" or (
        text.weight is not None and text.weight.isdigit() and int(text.weight) >= 700
    )
    return text.font_size >= 18 or (bold and text.font_size >= 14)


#: Where low-contrast text sits -> how the sentence names it, and the fix,
#: with `{tone}` filled in as "dark" or "light".
_CONTRAST_WORDING = {
    "page": ("page", "Give the page a {tone} background, or {verb} the text."),
    "frame": (
        "map frame background",
        "Give the map item a {tone} background, or {verb} the text.",
    ),
    "shape": ("shape", "Give the shape behind it a {tone} fill, or {verb} the text."),
    "legend": ("legend", "Give the legend a {tone} background, or {verb} the text."),
    "text": (
        "text box behind it",
        "Give that text box a {tone} background, or {verb} the text.",
    ),
    "own": ("background", "Give it a {tone} background, or {verb} the text."),
}


def _record_low_contrast_text(
    text_subjects: Mapping[str, str],
    frames: Sequence[MapFrameReading],
    furniture: Sequence[CartographItem],
    page_bg: str | None,
    report: FidelityReportBuilder,
) -> None:
    """Warn about a label whose colour is hard to read against what it sits
    on in the HTML: under WCAG's 3:1 for large text, 4.5:1 for body text.

    A warning, never a change: light text on a dark page is as valid as dark
    on light, and only the user knows which they meant. Items are taken in
    the order `core/cartograph_export.py` writes them -- by z, a frame first
    at its own z -- so "behind" means what the page draws first.
    """
    order: list[CartographItem | MapFrameReading] = sorted(
        [*furniture, *frames],
        key=lambda item: (
            item.z if item.z is not None else 0,
            0 if isinstance(item, MapFrameReading) else 1,
        ),
    )
    for index, item in enumerate(order):
        if not isinstance(item, TextSpec) or item.id not in text_subjects:
            continue
        if item.hidden or not item.body.strip():
            continue
        ink = _parse_hex(item.color)
        behind = _text_background(item, order[:index], page_bg)
        if ink is None or behind is None:
            continue
        background, surface = behind
        ink = _over(ink, background)
        ratio = _contrast(ink, background)
        minimum = _LARGE_TEXT_CONTRAST if _is_large_text(item) else _BODY_TEXT_CONTRAST
        if ratio >= minimum:
            continue
        # Light or dark by whichever of black and white text would read
        # better on it; the text is near the same, or it would not be here.
        light = _contrast(background, (0.0, 0.0, 0.0, 1.0)) >= _contrast(
            background, (1.0, 1.0, 1.0, 1.0)
        )
        tone = "light" if light else "dark"
        where, fix = _CONTRAST_WORDING[surface]
        noun = {"title": "The title", "attribution": "The attribution"}.get(
            item.kind, "The text"
        )
        body = " ".join(item.body.split())
        if "{{" not in body:
            body = body if len(body) <= 40 else body[:39].rstrip() + "…"
            noun = f'{noun} "{body}"'
        when = (
            " wherever the map has not drawn (while it loads, or in a browser "
            "that cannot show it)"
            if surface == "frame"
            else ""
        )
        report.approximated(
            text_subjects[item.id],
            f"{noun} is {tone} text on a {tone} {where} in the HTML (contrast "
            f"{ratio:.1f}:1), so it is hard to read{when}. "
            + fix.format(
                tone="dark" if light else "light",
                verb="darken" if light else "lighten",
            ),
        )
