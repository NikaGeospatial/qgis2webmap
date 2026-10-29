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
    is lower than `wanted` exactly when `limited`). `by_working_picture` when
    the frame's own picture would have fitted at `wanted` and it is the
    larger picture it is drawn through (see `working_pixel_size`) that did
    not."""

    dpi: int
    wanted: int
    limited: bool
    by_working_picture: bool = False


def frame_pixel_size(width_mm: float, height_mm: float, dpi: float) -> tuple[int, int]:
    """The whole-pixel size of a `width_mm` x `height_mm` frame drawn at
    `dpi` (at least one pixel a side). Every allocation of a frame's picture
    takes its size from here, so the limits are checked on the very numbers
    that are allocated."""
    return (
        max(1, math.floor(width_mm / _MM_PER_INCH * dpi + 0.5)),
        max(1, math.floor(height_mm / _MM_PER_INCH * dpi + 0.5)),
    )


def working_pixel_size(
    width_mm: float,
    height_mm: float,
    bounds_width_mm: float,
    bounds_height_mm: float,
    dpi: float,
) -> tuple[int, int]:
    """The whole-pixel size of the picture a frame with a blend mode or
    effect is first drawn onto: its layers over the item's whole bounding box
    (`bounds_*_mm`, the frame line included, so a little larger than the
    `width_mm` x `height_mm` frame itself), at the scale the frame's own
    rounded pixel size works out to. `AsyncFrameRender` allocates exactly this
    size, so the limits are checked on the same numbers."""
    frame_width_px, frame_height_px = frame_pixel_size(width_mm, height_mm, dpi)
    destination_dpi = (
        min(frame_width_px / width_mm, frame_height_px / height_mm) * _MM_PER_INCH
    )
    return (
        math.floor(bounds_width_mm / _MM_PER_INCH * destination_dpi + 0.5),
        math.floor(bounds_height_mm / _MM_PER_INCH * destination_dpi + 0.5),
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
    frame_dpi: int,
    export_dpi: int | None,
    width_mm: float,
    height_mm: float,
    working_bounds_mm: tuple[float, float] | None = None,
) -> FrameResolution:
    """The resolution for a static frame `width_mm` x `height_mm` whose own
    choice is `frame_dpi`, on a page saved at `export_dpi` (`None` when there
    is none to follow): the higher of the two, or when that would make a
    picture over the limits, the highest whole dpi whose picture, rounded to
    whole pixels, is within them. A frame drawn through a larger working
    picture (`working_bounds_mm`, the item's bounding box) keeps that one
    within the limits too."""

    def fits(dpi: int) -> bool:
        if not fits_frame_limits(*frame_pixel_size(width_mm, height_mm, dpi)):
            return False
        return working_bounds_mm is None or fits_frame_limits(
            *working_pixel_size(width_mm, height_mm, *working_bounds_mm, dpi)
        )

    wanted = max(frame_dpi, export_dpi or 0)
    if width_mm <= 0 or height_mm <= 0:
        return FrameResolution(wanted, wanted, False)
    if fits(wanted):
        return FrameResolution(wanted, wanted, False)
    long_mm, short_mm = (
        (max(width_mm, working_bounds_mm[0]), max(height_mm, working_bounds_mm[1]))
        if working_bounds_mm is not None
        else (width_mm, height_mm)
    )
    allowed_by_side = MAX_FRAME_SIDE_PX * _MM_PER_INCH / max(long_mm, short_mm)
    allowed_by_area = _MM_PER_INCH * (MAX_FRAME_PIXELS / (long_mm * short_mm)) ** 0.5
    # The bound ignores rounding, which can add up to half a pixel a side, so
    # step down until the rounded size really fits.
    dpi = min(wanted - 1, math.floor(min(allowed_by_side, allowed_by_area)))
    while dpi > 1 and not fits(dpi):
        dpi -= 1
    dpi = max(dpi, 1)
    # Rounding can also let one more dpi fit than the bound allows.
    while dpi + 1 < wanted and fits(dpi + 1):
        dpi += 1
    # A frame too large even at 1 dpi has no supported resolution: it is left
    # as asked, unflagged, and the allocation guard refuses it.
    by_working_picture = fits_frame_limits(*frame_pixel_size(width_mm, height_mm, wanted))
    return FrameResolution(dpi, wanted, dpi < wanted, dpi < wanted and by_working_picture)
