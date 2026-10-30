"""The one swatch the browser's legend can give a styled raster.

QGIS lists a raster's classes, or its colour ramp. The browser's legend can
show that only for a raster it colours itself from a named colormap; the
plugin's rasters arrive already coloured (`RasterSpec.style_qml` is baked into
their pixels, because QGIS's ramps have no names the runtime knows), so the
runtime has no legend for them and draws a grey square. A legend row can still
be given a colour and a shape (`<om-legend-row color shape>`), which is all
that is said here: a ramp's row fades its colour out the way a ramp reads, a
class list's row is a plain square. Pure Python, read from the style document.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

# Parses QGIS's own style XML only, and refuses entity declarations first.
from xml.etree import ElementTree  # nosec B405

#: A grey ramp's swatch: the middle of black to white.
MID_GREY = "#808080"


@dataclass(frozen=True)
class RasterSwatch:
    """What QGIS's legend lists for a raster and the one swatch standing for
    it: `shape` is `ramp` or `polygon`, `color` the swatch's colour, and
    `entries` the classes (colour and label) QGIS lists, empty for a ramp."""

    shape: str
    color: str
    entries: tuple[tuple[str, str], ...] = ()
    is_ramp: bool = False


def _hex(color: str) -> str | None:
    text = color.strip()
    if text.startswith("#") and len(text) in (7, 9):
        return text[:7].lower()
    return None


def raster_swatch(style_qml: str | None) -> RasterSwatch | None:
    """The swatch for a raster styled by `style_qml`: a paletted raster's
    classes, a pseudocolour renderer's classes (discrete or exact) or ramp
    (interpolated), a grey renderer's ramp. `None` for anything else (a
    multiband composite is its own colours) or an unreadable style."""
    if not style_qml or "<!ENTITY" in style_qml:
        # QGIS never writes entity declarations, so refusing them costs no
        # real style and rules out entity expansion from a crafted project.
        return None
    try:
        root = ElementTree.fromstring(style_qml)  # nosec B314
    except ElementTree.ParseError:
        return None
    renderer = root.find(".//rasterrenderer")
    if renderer is None:
        return None
    kind = renderer.get("type")
    if kind == "singlebandgray":
        return RasterSwatch("ramp", MID_GREY, is_ramp=True)
    if kind == "paletted":
        entries = [
            (_hex(entry.get("color", "")), entry.get("label", ""))
            for entry in renderer.iter("paletteEntry")
        ]
    elif kind == "singlebandpseudocolor":
        entries = [
            (_hex(item.get("color", "")), item.get("label", ""))
            for item in renderer.iter("item")
        ]
    else:
        return None
    classes = tuple((color, label) for color, label in entries if color is not None)
    if not classes:
        return None
    shader = renderer.find(".//colorrampshader")
    interpolated = shader is not None and shader.get("colorRampType") == "INTERPOLATED"
    if interpolated:
        return RasterSwatch("ramp", classes[0][0], is_ramp=True)
    return RasterSwatch("polygon", classes[0][0], classes)
