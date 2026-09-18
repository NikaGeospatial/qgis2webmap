"""A picture of the map, for the listing page.

Rendered off-screen at the extent the exported map OPENS ON, which is not the
same thing as the QGIS window.

It used to be `canvas.grab()`, on the reasoning that the author's own framing
was the one to keep and that a second render would need "its own extent, its
own DPI and its own set of ways to disagree with what is on screen". That
reasoning inverted once the export grew an extent of its own. `extent_source`
defaults to the DATA extent, so the two framings agree only by luck: an author
zoomed out one step published a card that was mostly empty canvas, letterboxed
in white, advertising a view their map never opens at. Reported 2026-09-18.

So the picture is rendered rather than grabbed, and the canvas is never touched
- no `setExtent`, no refresh, nothing moves under the author mid-publish.
`QgsMapRendererParallelJob` draws to its own image, off-screen, from the same
layers in the same order.

The output takes the EXTENT'S aspect ratio rather than a fixed one, so the
image has no blank margin of its own to explain. The listing's band is a fixed
height with `object-fit: cover`, so the browser crops to whatever shape the card
happens to be - and it can only do that well if every pixel it is given is map.

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

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.export_ir import Extent

# JPEG, not PNG. The picture is a continuous-tone map - shaded relief, a colour
# ramp, an aerial - which is exactly the content PNG is worst at: the Grand
# Canyon card rendered 1,571 KB as a PNG and ~a tenth of that as a JPEG of the
# same pixels. A thumbnail outweighing the map it illustrates is the absurdity
# THUMBNAIL_MAX_DIMENSION exists to prevent, and the format was the bigger half
# of it. No alpha is lost: the render lays the map over an opaque background.
#
# The artifact kind accepts `image/png` and `image/jpeg` for this role, and the
# SERVER picks the stored extension from the media type - so the two have to
# agree here, and `exporters/hosted.THUMBNAIL_MEDIA_TYPE` is the other half.
THUMBNAIL_FILENAME = "thumbnail.jpg"

# High enough that the ramp does not band and the labels stay legible, low
# enough that the point of using JPEG survives.
THUMBNAIL_JPEG_QUALITY = 82

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


# Web Mercator, because that is the projection the exported map draws in. A
# thumbnail rendered in the project's own CRS would be a differently-shaped
# picture of the same data - most visibly at high latitude, where Mercator
# stretches north-south and an unprojected render does not.
WEB_MERCATOR_EPSG = 3857


def mercator_aspect(extent: Extent) -> float:
    """Width divided by height for `extent`, as Web Mercator draws it.

    Computed here rather than read off a projected rectangle so the shape of the
    output is decided by a pure function a unit test can reach. The x axis is
    linear in longitude, so degrees serve directly; the y axis is not, which is
    the whole reason this is not `width_degrees / (north - south)`.

    Latitudes are clamped just inside the poles, where the Mercator y goes to
    infinity and the ratio would collapse to zero.
    """
    limit = 85.05112878
    south = max(-limit, min(limit, extent.south))
    north = max(-limit, min(limit, extent.north))

    def y(latitude: float) -> float:
        return math.log(math.tan(math.radians(45.0 + latitude / 2.0)))

    height = y(north) - y(south)
    width = math.radians(extent.width_degrees)
    if height <= 0.0 or width <= 0.0:
        # A degenerate extent - a single point, or one row of pixels. Square is
        # the honest answer: there is no aspect to preserve.
        return 1.0
    return width / height


def size_for_extent(
    extent: Extent, maximum: int = THUMBNAIL_MAX_DIMENSION
) -> tuple[int, int]:
    """The pixel size to render `extent` at, long edge capped at `maximum`.

    The extent's own shape, so the rendered image is all map and carries no
    blank margin for the listing to crop around.
    """
    aspect = mercator_aspect(extent)
    if aspect >= 1.0:
        return (maximum, max(1, round(maximum / aspect)))
    return (max(1, round(maximum * aspect)), maximum)


def render_extent(layers: Any, extent: Extent, size: tuple[int, int]) -> bytes:
    """`layers` drawn over `extent` as PNG bytes, or empty if it cannot be done.

    Empty rather than raising, for the reason `capture_canvas` returns empty: a
    map is publishable without a picture, and losing a publish over one would be
    the wrong trade.

    `layers` is in QGIS draw order, topmost first - what `layerOrder()` returns
    and what `QgsMapSettings.setLayers` expects. Handing it the reversed list
    draws the basemap over everything.

    An antimeridian-crossing extent is refused rather than drawn: the rectangle
    QGIS would need cannot express the wrap, and the picture it produces is the
    whole world backwards - the exact failure `Extent.crosses_antimeridian`
    exists to prevent elsewhere.
    """
    if extent.crosses_antimeridian:
        return b""
    width, height = size
    if width <= 0 or height <= 0 or not layers:
        return b""

    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsMapRendererParallelJob,
        QgsMapSettings,
        QgsProject,
        QgsRectangle,
    )
    from qgis.PyQt.QtCore import QBuffer, QIODevice, QSize
    from qgis.PyQt.QtGui import QColor

    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        mercator = QgsCoordinateReferenceSystem.fromEpsgId(WEB_MERCATOR_EPSG)
        rect = QgsRectangle(extent.west, extent.south, extent.east, extent.north)
        rect = QgsCoordinateTransform(
            wgs84, mercator, QgsProject.instance()
        ).transformBoundingBox(rect)

        settings = QgsMapSettings()
        settings.setLayers(list(layers))
        settings.setDestinationCrs(mercator)
        settings.setOutputSize(QSize(width, height))
        settings.setExtent(rect)
        # White rather than transparent: the listing composites the picture onto
        # its own surface, and a transparent nodata margin would pick up the
        # card's background in a band that is meant to read as an image.
        settings.setBackgroundColor(QColor(255, 255, 255))

        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image is None or image.isNull():
            return b""

        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            return b""
        try:
            if not image.save(buffer, "JPEG", THUMBNAIL_JPEG_QUALITY):
                return b""
            return bytes(buffer.data())
        finally:
            buffer.close()
    except Exception:
        return b""


def capture_canvas(canvas: Any) -> bytes:
    """The canvas as PNG bytes, capped, or empty if it cannot be grabbed.

    Empty rather than raising: a map is publishable without a thumbnail, and
    losing a publish over a picture would be the wrong trade. The caller simply
    leaves the thumbnail out of the file list.

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
        if not pixmap.save(buffer, "JPEG", THUMBNAIL_JPEG_QUALITY):
            return b""
        return bytes(buffer.data())
    finally:
        buffer.close()
