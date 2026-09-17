"""What a QGIS raster renderer means in `COGLayer`'s vocabulary.

Separate from `layer_reader` because that module imports `qgis.core` and
therefore cannot be imported by a unit test. Everything here reads the renderer
through `getattr`, so the real QGIS objects and the stand-ins in
`tests/unit/test_raster_style.py` are handled by the same code - which is the
only reason this behaviour has tests at all.

The gap this closes: until 2026-09-17 the exporter emitted `src`, `min`/`max`,
`nodata` and `opacity` for a raster and nothing else, so a project showing a
Viridis-ramped DEM published as the runtime's default grey. `bands`, `colormap`
and `reverse` have been available since the runtime's 0.7.0.
"""

from __future__ import annotations

from typing import Any

# QGIS ramp name -> `COGLayer` sprite colormap. Matched case-insensitively on
# the ramp's name with spaces and punctuation stripped, because QGIS spells them
# for humans ("YlOrRd", "RdBu", "Blue to Red") and the runtime takes lowercase
# identifiers.
#
# Deliberately NOT exhaustive. A ramp absent here yields `None`, which leaves the
# runtime on its own default - a wrong-but-plausible colormap is worse than an
# honest default, because it looks deliberate. `read_raster` records the loss so
# the author is told rather than left to spot it.
_COLORMAP_BY_RAMP_NAME = {
    "viridis": "viridis",
    "plasma": "plasma",
    "inferno": "inferno",
    "magma": "magma",
    "cividis": "cividis",
    "turbo": "turbo",
    "blues": "blues",
    "greens": "greens",
    "oranges": "oranges",
    "purples": "purples",
    "reds": "reds",
    "greys": "gray",
    "grays": "gray",
    "ylorrd": "ylorrd",
    "rdbu": "rdbu",
    "spectral": "spectral",
}


def _normalise_ramp_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def style_from_renderer(
    renderer: Any,
) -> tuple[str | None, tuple[int, ...] | None, bool]:
    """The colormap, band selection and ramp direction `COGLayer` should use.

    Read from the RENDERER for the same reason `raster_rescale` is: what the
    author is looking at in QGIS is the renderer's doing, and a hosted map that
    drops it renders a grey DEM where the project showed a colour ramp. Before
    this existed the exporter emitted `src`, `min`/`max`, `nodata` and `opacity`
    and nothing else, so every styled raster arrived unstyled.

    Three renderers are understood, and anything else returns
    `(None, None, False)` so the runtime's defaults apply:

    * **singlebandpseudocolor** - the band, plus its ramp mapped onto the
      runtime's sprite vocabulary.
    * **multibandcolor** - the three band numbers, in R, G, B order. No
      colormap: a composite is its own colour.
    * **singlebandgray** - the band, with the `gray` colormap named explicitly
      rather than left implicit, so the emitted manifest states the intent.

    Duck-typed via `getattr` rather than `isinstance`, matching the rest of this
    module: it keeps the reader importable, and unit-testable, without QGIS.
    """
    if renderer is None:
        return (None, None, False)

    kind = ""
    type_fn = getattr(renderer, "type", None)
    if callable(type_fn):
        kind = str(type_fn() or "").lower()

    def _band(attr: str) -> int | None:
        fn = getattr(renderer, attr, None)
        if not callable(fn):
            return None
        try:
            value = int(fn())
        except (TypeError, ValueError):
            return None
        # QGIS uses -1 or 0 for "unset"; COGLayer's bands are 1-based.
        return value if value >= 1 else None

    if kind == "multibandcolor":
        triple = tuple(
            band
            for band in (_band("redBand"), _band("greenBand"), _band("blueBand"))
            if band is not None
        )
        # All three or none: a partial triple would be a composite the author
        # never configured, and `COGLayer` validates the shape anyway.
        return (None, triple if len(triple) == 3 else None, False)

    if kind == "singlebandgray":
        band = _band("grayBand")
        return ("gray", (band,) if band else None, False)

    if kind != "singlebandpseudocolor":
        return (None, None, False)

    band = _band("band")
    colormap: str | None = None
    reverse = False

    shader = getattr(renderer, "shader", None)
    shader = shader() if callable(shader) else None
    function = getattr(shader, "rasterShaderFunction", None) if shader else None
    function = function() if callable(function) else None

    ramp_fn = getattr(function, "sourceColorRamp", None) if function else None
    ramp = ramp_fn() if callable(ramp_fn) else None
    if ramp is not None:
        # `QgsGradientColorRamp` exposes no name, so the type string is the only
        # handle on which ramp it is; `QgsCptCityColorRamp` and the style-library
        # ramps do carry one.
        for attr in ("schemeName", "name"):
            fn = getattr(ramp, attr, None)
            if callable(fn):
                candidate = _normalise_ramp_name(str(fn() or ""))
                if candidate in _COLORMAP_BY_RAMP_NAME:
                    colormap = _COLORMAP_BY_RAMP_NAME[candidate]
                    break
        invert = getattr(ramp, "isInverted", None)
        if callable(invert):
            reverse = bool(invert())

    return (colormap, (band,) if band else None, reverse)
