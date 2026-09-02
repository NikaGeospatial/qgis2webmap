"""A picture of the map, for the listing page.

Grabbed from the QGIS canvas the user is already looking at, rather than
rendered again: it is the framing they chose, it costs nothing, and a second
render would need its own extent, its own DPI and its own set of ways to
disagree with what is on screen.

**Captured on every publish, including a republish.** A version bump that keeps
yesterday's thumbnail shows the wrong map on the listing while the link serves
the right one, and nobody looking at the listing can tell.

The size cap is the point of `THUMBNAIL_MAX_DIMENSION`. A canvas on a 4K screen
is a several-megabyte PNG, which on a small map would be the largest object
published - a thumbnail outweighing the map it illustrates is absurd, and it is
the user's upload bandwidth being spent on it.

The Qt import is deferred so `scaled_size` - the only part with a decision in
it - is testable without a QGIS application.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Any

THUMBNAIL_FILENAME = "thumbnail.png"

# Wide enough for a listing card at 2x, small enough that the PNG stays in the
# low hundreds of kilobytes.
THUMBNAIL_MAX_DIMENSION = 1200


def scaled_size(
    width: int, height: int, maximum: int = THUMBNAIL_MAX_DIMENSION
) -> tuple[int, int]:
    """Fit within `maximum` on the long edge, preserving the aspect ratio.

    Never enlarges: a small canvas stays small rather than being blown up into
    a blurry thumbnail that is also bigger to download.
    """
    if width <= 0 or height <= 0:
        return (0, 0)
    longest = max(width, height)
    if longest <= maximum:
        return (width, height)
    scale = maximum / float(longest)
    # At least one pixel each way, so an extreme aspect ratio cannot round a
    # dimension to zero and produce an image Qt refuses to save.
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def capture_canvas(canvas: Any) -> bytes:
    """The canvas as PNG bytes, capped, or empty if it cannot be grabbed.

    Empty rather than raising: a map is publishable without a thumbnail, and
    losing a publish over a picture would be the wrong trade. The caller simply
    leaves `thumbnail.png` out of the file list.

    **Must run on the GUI thread.** `grab()` touches a widget.
    """
    from qgis.PyQt.QtCore import QBuffer, QIODevice, Qt

    try:
        pixmap = canvas.grab()
    except Exception:
        return b""
    if pixmap is None or pixmap.isNull():
        return b""

    width, height = scaled_size(pixmap.width(), pixmap.height())
    if not width or not height:
        return b""
    if (width, height) != (pixmap.width(), pixmap.height()):
        pixmap = pixmap.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return b""
    try:
        if not pixmap.save(buffer, "PNG"):
            return b""
        return bytes(buffer.data())
    finally:
        buffer.close()
