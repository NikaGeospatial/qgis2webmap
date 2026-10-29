"""The resolution a static map frame's picture is drawn at, from the saved
export resolution (pure Python: no `qgis`, no Qt).

A static frame is a picture QGIS draws once; the browser's PNG, print and
atlas routes then place that picture on a page rendered at the cartograph's
saved `dpi`. A picture drawn at a lower resolution than the page is enlarged
there and goes soft, so a frame is drawn at the saved resolution unless its
own choice is higher. Both are capped by what a browser canvas and a
machine's memory can hold: a picture is never drawn past `MAX_FRAME_SIDE_PX`
a side or `MAX_FRAME_PIXELS` in all, whatever resolution was asked for, and
then it is drawn at the highest resolution that fits (same physical size,
extent and aspect ratio) and the report says so.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The resolution the runtime renders pages at when a cartograph names none
#: (`PrintSettings.dpi`'s default; the writer's `dpi` attribute is absent).
DEFAULT_EXPORT_DPI = 300

#: The most pixels one frame's picture may have, and the longest side:
#: a browser canvas stops at 16384 a side, and a picture this size is already
#: a few hundred MB decoded.
MAX_FRAME_PIXELS = 100_000_000
MAX_FRAME_SIDE_PX = 16_000

_MM_PER_INCH = 25.4


class FrameTooLargeError(ValueError):
    """A picture size over the frame limits reached an allocation."""


@dataclass(frozen=True)
class FrameResolution:
    """`dpi` a frame is drawn at; `limited` when the wanted resolution was
    more than the size limits allow and `dpi` is what they allow (so `dpi`
    is lower than `wanted` exactly when `limited`)."""

    dpi: int
    wanted: int
    limited: bool


def frame_pixel_size(width_mm: float, height_mm: float, dpi: float) -> tuple[int, int]:
    """The whole-pixel size of a `width_mm` x `height_mm` frame drawn at
    `dpi` (at least one pixel a side). Every allocation of a frame's picture
    takes its size from here, so the limits are checked on the very numbers
    that are allocated."""
    return (
        max(1, math.floor(width_mm / _MM_PER_INCH * dpi + 0.5)),
        max(1, math.floor(height_mm / _MM_PER_INCH * dpi + 0.5)),
    )


def fits_frame_limits(width_px: int, height_px: int) -> bool:
    """Whether a picture of this size is within both frame limits."""
    return (
        max(width_px, height_px) <= MAX_FRAME_SIDE_PX
        and width_px * height_px <= MAX_FRAME_PIXELS
    )


def check_frame_size(width_px: int, height_px: int) -> None:
    """Refuse a picture size over the frame limits. The last line, at every
    place a frame's picture is allocated; the sizes reaching it have already
    been brought within the limits by `frame_resolution`."""
    if not fits_frame_limits(width_px, height_px):
        raise FrameTooLargeError(
            f"a {width_px} x {height_px} px picture is over the frame limits "
            f"({MAX_FRAME_SIDE_PX} px a side, {MAX_FRAME_PIXELS} px in all)"
        )


def frame_resolution(
    frame_dpi: int, export_dpi: int | None, width_mm: float, height_mm: float
) -> FrameResolution:
    """The resolution for a static frame `width_mm` x `height_mm` whose own
    choice is `frame_dpi`, on a page saved at `export_dpi` (`None` when there
    is none to follow): the higher of the two, or when that would make a
    picture over the limits, the highest whole dpi whose picture, rounded to
    whole pixels, is within them."""
    wanted = max(frame_dpi, export_dpi or 0)
    if width_mm <= 0 or height_mm <= 0:
        return FrameResolution(wanted, wanted, False)
    if fits_frame_limits(*frame_pixel_size(width_mm, height_mm, wanted)):
        return FrameResolution(wanted, wanted, False)
    allowed_by_side = MAX_FRAME_SIDE_PX * _MM_PER_INCH / max(width_mm, height_mm)
    allowed_by_area = _MM_PER_INCH * (MAX_FRAME_PIXELS / (width_mm * height_mm)) ** 0.5
    # The bound ignores rounding, which can add up to half a pixel a side, so
    # step down until the rounded size really fits.
    dpi = min(wanted - 1, math.floor(min(allowed_by_side, allowed_by_area)))
    while dpi > 1 and not fits_frame_limits(
        *frame_pixel_size(width_mm, height_mm, dpi)
    ):
        dpi -= 1
    dpi = max(dpi, 1)
    # Rounding can also let one more dpi fit than the bound allows.
    while dpi + 1 < wanted and fits_frame_limits(
        *frame_pixel_size(width_mm, height_mm, dpi + 1)
    ):
        dpi += 1
    # A frame too large even at 1 dpi has no supported resolution: it is left
    # as asked, unflagged, and the allocation guard refuses it.
    return FrameResolution(dpi, wanted, dpi < wanted)
