"""Readable widget colours from two independent settings.

The dialog lets the author set a widget background and a widget text colour
separately, and either may be left on Default. Left alone that makes unreadable
maps: a black background under the runtime's own dark text, or white text on
its own white background. So a colour left on Default is resolved against its
partner here - dark or light, whichever reads - while two colours the author
chose are never changed, only reported when they fall short of WCAG AA.

Pure Python, no `qgis` or Qt import.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from .export_ir import Color
from .page_style import contrast_ratio

# WCAG AA for normal-size text.
MINIMUM_CONTRAST = 4.5

# The runtime's own defaults for a widget with no theme set: the panel surface
# and its primary and secondary text. A Default colour is only replaced when the
# partner makes one of these unreadable.
RUNTIME_BACKGROUND = Color(r=255, g=255, b=255)
RUNTIME_TEXT = Color(r=31, g=41, b=55)
RUNTIME_MUTED_TEXT = Color(r=107, g=114, b=128)

# Pure black and white, because nothing else reaches 4.5:1 on every mid-grey.
DARK_INK = Color(r=0, g=0, b=0)
LIGHT_INK = Color(r=255, g=255, b=255)


@dataclass(frozen=True)
class ResolvedWidgetColors:
    """The colours to emit (None keeps the runtime's own) and what to tell the
    author about them."""

    background: Color | None
    foreground: Color | None
    # Which side was filled in for the author, if any.
    derived: str | None = None
    # Contrast of an explicit pair that falls short, else None.
    explicit_contrast: float | None = None


def _rgb(color: Color) -> tuple[float, float, float, float]:
    return (color.r / 255, color.g / 255, color.b / 255, 1.0)


def contrast_between(first: Color, second: Color) -> float:
    return contrast_ratio(_rgb(first), _rgb(second))


def _readable_partner(chosen: Color) -> Color:
    """Dark or light ink, whichever contrasts more with `chosen`."""
    if contrast_between(chosen, DARK_INK) >= contrast_between(chosen, LIGHT_INK):
        return DARK_INK
    return LIGHT_INK


def resolve_widget_colors(
    background: Color | None, foreground: Color | None
) -> ResolvedWidgetColors:
    if background is not None and foreground is not None:
        ratio = contrast_between(background, foreground)
        return ResolvedWidgetColors(
            background,
            foreground,
            explicit_contrast=ratio if ratio < MINIMUM_CONTRAST else None,
        )

    if background is not None:
        readable = all(
            contrast_between(background, text) >= MINIMUM_CONTRAST
            for text in (RUNTIME_TEXT, RUNTIME_MUTED_TEXT)
        )
        if readable:
            return ResolvedWidgetColors(background, None)
        return ResolvedWidgetColors(
            background, _readable_partner(background), derived="text"
        )

    if foreground is not None:
        if contrast_between(RUNTIME_BACKGROUND, foreground) >= MINIMUM_CONTRAST:
            return ResolvedWidgetColors(None, foreground)
        return ResolvedWidgetColors(
            _readable_partner(foreground), foreground, derived="background"
        )

    return ResolvedWidgetColors(None, None)
