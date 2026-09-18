"""How a QGIS raster renderer reaches the map: baked, or carried as bands.

**The short version.** A single-band raster has no colours of its own - QGIS
invents them at draw time - so its style is rendered into the pixels before
export. A multiband composite already *is* colour in the file, so it travels as
data and the runtime draws it.

**Why not send the ramp's name.** `COGLayer` takes a `colormap` from a fixed
vocabulary of fourteen names. That would be enough for a project whose ramp is
one of them, except QGIS does not record which named ramp a user picked:
choosing "Viridis" in Symbology copies its stops into an anonymous gradient, and
a project reloaded from disk hands back `sourceColorRamp() is None` with only a
discretised stop list behind it. So a name-matching exporter finds nothing for
almost every real project. This was shipped on 2026-09-17 and measured on
2026-09-18 against the Grand Canyon demo: the ramp name was unreachable, the
attribute was never emitted, and the DEM published grey exactly as before.
Matching the stop colours against the fourteen instead was the other candidate
and is worse - the demo's blue-to-red ramp is near none of them, so the honest
answer would still have been "no colormap", and a near-miss would have drawn the
map in colours nobody chose.

Baking answers all of it at once, and it is what the fidelity report already
told users to do by hand ("Export a styled RGB GeoTIFF from QGIS"). It is exact
for every renderer QGIS has, including the paletted and hillshade ones that
`COGLayer` cannot express at all.

**What it costs, stated plainly.** The published file carries the map's colours
rather than its measured values, so a recipient cannot read elevations out of
it and the runtime cannot restretch it. The fidelity report says so per layer.
Size usually falls rather than rises - a Float32 or Int16 band becomes RGBA
uint8, and DEFLATE likes a smooth ramp: the demo's 2905x1420 DEM went from a
6.6 MB single-band COG to 3.2 MB.

This module stays free of `qgis.core` so it can be unit-tested; `layer_reader`
supplies the renderer and the QML, and `packaging/raster_bake` does the work.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Any

#: The one renderer whose pixels are already the colours the author sees. Every
#: other QGIS raster renderer - pseudocolour, paletted, singleband grey,
#: hillshade, contour - decides colour from values at draw time, and only QGIS
#: can reproduce that decision.
CARRIED_AS_DATA = "multibandcolor"


def renderer_kind(renderer: Any) -> str:
    """The renderer's type string, or `""` for no renderer at all.

    Duck-typed via `getattr` rather than `isinstance` so the real QGIS objects
    and the stand-ins in `tests/unit/test_raster_style.py` take the same path -
    which is the only reason this behaviour has tests.
    """
    if renderer is None:
        return ""
    type_fn = getattr(renderer, "type", None)
    if not callable(type_fn):
        return ""
    return str(type_fn() or "").lower()


def should_bake(renderer: Any) -> bool:
    """Whether this renderer's colours have to be rendered into the pixels.

    True for everything except an RGB composite, INCLUDING the no-renderer case.
    A raster QGIS will not give a renderer for is one we cannot claim to
    reproduce, and baking what QGIS actually draws is the answer that cannot be
    wrong; `raster_bake` degrades to carrying the data if the render fails.
    """
    return renderer_kind(renderer) != CARRIED_AS_DATA


def composite_bands(renderer: Any) -> tuple[int, ...] | None:
    """The R, G, B band numbers of a composite, or `None`.

    1-based, GDAL's convention and `COGLayer`'s. All three or none: a partial
    triple would be a composite the author never configured, and the runtime
    validates the shape anyway.
    """
    if renderer_kind(renderer) != CARRIED_AS_DATA:
        return None

    def band(attr: str) -> int | None:
        fn = getattr(renderer, attr, None)
        if not callable(fn):
            return None
        try:
            value = int(fn())
        except (TypeError, ValueError):
            return None
        # QGIS uses -1 or 0 for "unset"; COGLayer's bands are 1-based.
        return value if value >= 1 else None

    triple = tuple(
        number
        for number in (band("redBand"), band("greenBand"), band("blueBand"))
        if number is not None
    )
    return triple if len(triple) == 3 else None
