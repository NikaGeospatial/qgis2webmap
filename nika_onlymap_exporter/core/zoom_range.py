"""QGIS scale-based visibility, translated into the runtime's zoom range.

QGIS hides a layer outside a pair of scale denominators; the pinned OnlyMap
runtime (0.8.4) hides a layer outside `visible-zoom-range="[min, max]"`, any
layer type, visible while `min <= zoom < max`. The runtime's own check, read out
of the 0.8.4 bundle, is `zoom >= range[0] && zoom < range[1]`, and it ignores
the attribute - showing the layer at every zoom and logging a warning - unless
both ends are finite numbers with `min < max`.

**The two ends line up.** QGIS 4.2's `isInScaleRange` is visible while
`maximumScale <= scale < minimumScale` (measured, not read from the docs), so
the zoomed-out limit is exclusive and the zoomed-in one inclusive. Zoom rises as
the denominator falls, so QGIS's *minimum* scale becomes the runtime's *minimum*
zoom and its *maximum* scale the *maximum* zoom. Only the single boundary value
itself differs, which no one can land on by scrolling.

**Scale to zoom.** The runtime is deck.gl on Web Mercator, whose world is 512
CSS pixels wide at zoom 0 - not the 256 of the old slippy-map tiles, which is a
whole zoom level of difference. A CSS pixel is 1/96 inch. At latitude `phi` one
pixel covers `EQUATOR / 512 / 2**zoom * cos(phi)` metres of ground.

What a QGIS scale *means* depends on the project's CRS: in a metre-based
projection it is the true ground scale, in EPSG:3857 it is the Mercator scale
(true scale divided by `cos(phi)`), and a geographic CRS uses QGIS's own
latitude method. Rather than list CRSs, the reader measures the ratio between
QGIS's readout and true ground scale at the map's centre and hands it in as
`qgis_scale_factor`. That keeps this module pure while the one QGIS-dependent
number is measured by QGIS itself.

**Where it stops being exact.** The web map's scale changes with latitude and a
non-Mercator QGIS canvas's does not, so a layer switches at the right point at
the map's centre and slightly early or late towards its northern and southern
edges. That drift is computed from the map's own extent and stated in the
Fidelity tab, so "approximately" always comes with a number.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .export_ir import FidelityStatus, ScaleRange

# The equator's length on the WGS84 ellipsoid, which is the width of the Web
# Mercator world in metres.
EQUATOR_METRES = 40_075_016.686

# deck.gl's Web Mercator world is 512 pixels wide at zoom 0.
WORLD_PIXELS_AT_ZOOM_0 = 512.0

# One CSS pixel, in metres on the screen: the web's pixel is 1/96 inch.
CSS_PIXEL_METRES = 0.0254 / 96.0

# The open ends of a one-sided range. The runtime needs two finite numbers, so
# "no zoomed-out limit" becomes zoom 0 (as far out as the map goes) and "no
# zoomed-in limit" a zoom no camera reaches. Thirty is far past deck.gl's
# default ceiling of 20 and past any tile pyramid in use.
ZOOM_FLOOR = 0.0
ZOOM_CEILING = 30.0

# Below this much drift across the map, the switch points are reported as kept.
# A tenth of a zoom level is a 7% difference in scale, well inside the error
# every screen's own DPI already introduces.
EXACT_WITHIN_ZOOM_LEVELS = 0.1

# Mercator is undefined at the poles; the web map clips at about 85 degrees.
MAX_MERCATOR_LATITUDE = 85.0511


@dataclass(frozen=True)
class ZoomVisibility:
    """What to emit for one layer, and what to tell the user about it.

    `zoom_range` is `None` when nothing should be emitted. `status` is `None`
    when there is nothing to report - the layer has no scale range at all.
    """

    zoom_range: tuple[float, float] | None
    status: FidelityStatus | None
    detail: str = ""


def web_zoom_for_scale(
    scale_denominator: float,
    latitude: float = 0.0,
    qgis_scale_factor: float = 1.0,
) -> float:
    """The web zoom whose view matches a QGIS scale, at a latitude. Unclamped.

    `qgis_scale_factor` is QGIS's scale readout divided by the true ground
    scale for the same view, measured at `latitude`; 1.0 for a projection in
    metres. Larger denominators give smaller zooms - the inversion that the
    previous `visible-min-zoom` attempt got backwards.
    """
    true_scale = scale_denominator / qgis_scale_factor
    ground_metres_per_pixel = true_scale * CSS_PIXEL_METRES
    metres_per_pixel_at_zoom_0 = (
        EQUATOR_METRES / WORLD_PIXELS_AT_ZOOM_0 * math.cos(math.radians(latitude))
    )
    return math.log2(metres_per_pixel_at_zoom_0 / ground_metres_per_pixel)


def translate_scale_range(
    scale_range: ScaleRange,
    latitude: float = 0.0,
    qgis_scale_factor: float = 1.0,
    edges: Sequence[tuple[float, float]] | None = None,
) -> ZoomVisibility:
    """Turn QGIS's scale visibility into `visible-zoom-range`, with a verdict.

    `edges` holds `(latitude, qgis_scale_factor)` for the map's southern and
    northern edges, used to state how far the switch points drift away from the
    centre. `None` means there is no extent to drift across.
    """
    if not scale_range.is_set:
        return ZoomVisibility(zoom_range=None, status=None)

    latitude = _clamp_latitude(latitude)
    # QGIS stores "no limit" as 0 on either side.
    zoomed_out = _limit(scale_range.min_scale)
    zoomed_in = _limit(scale_range.max_scale)

    if zoomed_out is None and zoomed_in is None:
        return ZoomVisibility(
            zoom_range=None,
            status=FidelityStatus.PRESERVED,
            detail=(
                "Scale-based visibility is switched on with no limits set, so "
                "the layer shows at every zoom - as it does in QGIS."
            ),
        )

    setting = _describe_scales(zoomed_out, zoomed_in)

    if zoomed_out is not None and zoomed_in is not None and zoomed_out <= zoomed_in:
        return ZoomVisibility(
            zoom_range=None,
            status=FidelityStatus.UNSUPPORTED,
            detail=(
                f"The layer is set to show {setting}, a range that contains "
                "no scale, so QGIS never draws it. The web map cannot hide a "
                "layer at every zoom this way, so it is not applied and the "
                "layer shows at every zoom. Fix the range in the layer's "
                "Rendering properties."
            ),
        )

    low = (
        ZOOM_FLOOR
        if zoomed_out is None
        else web_zoom_for_scale(zoomed_out, latitude, qgis_scale_factor)
    )
    high = (
        ZOOM_CEILING
        if zoomed_in is None
        else web_zoom_for_scale(zoomed_in, latitude, qgis_scale_factor)
    )
    low = round(min(max(low, ZOOM_FLOOR), ZOOM_CEILING), 2)
    high = round(min(max(high, ZOOM_FLOOR), ZOOM_CEILING), 2)

    if low >= high:
        return ZoomVisibility(
            zoom_range=None,
            status=FidelityStatus.UNSUPPORTED,
            detail=(
                f"The layer is set to show {setting}, which lies entirely "
                "outside the zoom levels a web map has, so the range is not "
                "applied and the layer shows at every zoom."
            ),
        )

    shown = _describe_zooms(
        low if zoomed_out is not None else None,
        high if zoomed_in is not None else None,
    )
    detail = (
        f"Shown {shown} on the web map - the equivalent of {setting} in QGIS, "
        "on a standard 96-dpi screen."
    )

    drift = _edge_drift(latitude, qgis_scale_factor, edges)
    if drift < EXACT_WITHIN_ZOOM_LEVELS:
        return ZoomVisibility(
            zoom_range=(low, high), status=FidelityStatus.PRESERVED, detail=detail
        )

    return ZoomVisibility(
        zoom_range=(low, high),
        status=FidelityStatus.APPROXIMATED,
        detail=(
            f"{detail} Matched at the map's centre ({_latitude_text(latitude)}). "
            "The web map's scale changes with latitude and this project's does "
            "not, so towards the map's northern and southern edges the layer "
            f"appears or disappears up to {drift:.1f} zoom levels earlier or "
            "later than it would in QGIS."
        ),
    )


def _limit(value: float | None) -> float | None:
    """A QGIS scale limit, with its "no limit" zero turned into `None`."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _clamp_latitude(latitude: float) -> float:
    if not math.isfinite(latitude):
        return 0.0
    return max(-MAX_MERCATOR_LATITUDE, min(MAX_MERCATOR_LATITUDE, latitude))


def _edge_drift(
    latitude: float,
    qgis_scale_factor: float,
    edges: Sequence[tuple[float, float]] | None,
) -> float:
    """How many zoom levels the switch points drift by at the map's edges.

    The zoom is matched at the centre. At another latitude the web map's true
    scale has changed by `cos(edge) / cos(centre)`, and QGIS's - whatever its
    CRS does - by the ratio of its measured factors there. The drift is how far
    those two disagree. For an EPSG:3857 project they agree everywhere: its
    readout is Mercator's, so it follows the web map exactly.
    """
    if not edges:
        return 0.0
    centre = math.cos(math.radians(latitude)) * qgis_scale_factor
    if centre <= 0:
        return 0.0
    drift = 0.0
    for edge_latitude, edge_factor in edges:
        edge = math.cos(math.radians(_clamp_latitude(edge_latitude))) * edge_factor
        if edge > 0:
            drift = max(drift, abs(math.log2(edge / centre)))
    return drift


def _scale_text(denominator: float) -> str:
    return f"1:{denominator:,.0f}"


def _zoom_text(zoom: float) -> str:
    return f"{zoom:.1f}"


def _latitude_text(latitude: float) -> str:
    hemisphere = "N" if latitude >= 0 else "S"
    return f"{abs(latitude):.1f}°{hemisphere}"


def _describe_scales(zoomed_out: float | None, zoomed_in: float | None) -> str:
    if zoomed_out is not None and zoomed_in is not None:
        return f"only between {_scale_text(zoomed_out)} and {_scale_text(zoomed_in)}"
    if zoomed_out is not None:
        return f"only when zoomed in closer than {_scale_text(zoomed_out)}"
    assert zoomed_in is not None
    return f"only when zoomed out further than {_scale_text(zoomed_in)}"


def _describe_zooms(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"between zoom {_zoom_text(low)} and {_zoom_text(high)}"
    if low is not None:
        return f"from zoom {_zoom_text(low)} inwards"
    assert high is not None
    return f"below zoom {_zoom_text(high)}"
