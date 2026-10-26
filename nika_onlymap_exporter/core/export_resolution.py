"""The resolution a static map frame's picture is drawn at, from the saved
export resolution (pure Python: no `qgis`, no Qt).

A static frame is a picture QGIS draws once; the browser's PNG, print and
atlas routes then place that picture on a page rendered at the cartograph's
saved `dpi`. A picture drawn at a lower resolution than the page is enlarged
there and goes soft, so a frame is drawn at the saved resolution unless its
own choice is higher -- never lower, and never past what a browser canvas
and a machine's memory can hold.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class FrameResolution:
    """`dpi` a frame is drawn at; `limited` when the wanted resolution was
    more than the size limits allow and `dpi` is what they allow."""

    dpi: int
    wanted: int
    limited: bool


def frame_resolution(
    frame_dpi: int, export_dpi: int, width_mm: float, height_mm: float
) -> FrameResolution:
    """The resolution for a static frame `width_mm` x `height_mm` whose own
    choice is `frame_dpi`, on a page saved at `export_dpi`
    (`DEFAULT_EXPORT_DPI` when the cartograph saves none)."""
    wanted = max(frame_dpi, export_dpi)
    if width_mm <= 0 or height_mm <= 0:
        return FrameResolution(wanted, wanted, False)
    allowed_by_side = MAX_FRAME_SIDE_PX * _MM_PER_INCH / max(width_mm, height_mm)
    allowed_by_area = _MM_PER_INCH * (MAX_FRAME_PIXELS / (width_mm * height_mm)) ** 0.5
    allowed = int(min(allowed_by_side, allowed_by_area))
    if wanted <= allowed:
        return FrameResolution(wanted, wanted, False)
    # Never below what the frame itself asked for, even when that is over
    # the limit: that was the user's own choice, as before.
    return FrameResolution(max(allowed, frame_dpi), wanted, True)
