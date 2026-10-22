"""Extent computation, including the antimeridian case.

Split into its own pure module so it can be unit-tested in CI without PyQGIS.
The maths here is small but it is the difference between a map that opens on its
data and one that opens on the whole planet.

**The problem.** A bounding box is normally `min(lon) ... max(lon)`. That breaks
for data crossing the 180° meridian: Alaska spans -179.13 to +179.78, so the
naive box is 358.9° wide -- essentially the entire world. The incumbent produces
exactly this, measured at 99.8% of the width of Web Mercator on the QGIS Alaska
sample, and the user opens a regional dataset zoomed out to the whole globe with
stray geometry at the frame edge.

**The fix.** Sort the longitudes and find the largest angular gap between
consecutive points. That gap is empty space; the data occupies the complement.
If the largest gap wraps through the antimeridian, the naive box is correct. If
it does not, the true extent wraps and is `[gap_end ... gap_start]` going east.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .export_ir import Extent

# Below this, a "gap" is noise rather than a real separation. Two degrees is
# comfortably larger than any rounding artefact and far smaller than the ocean
# gaps that make this technique work.
MIN_MEANINGFUL_GAP_DEGREES = 2.0

# An edge whose endpoints are further apart than this is taken to cross the
# antimeridian the short way round (179 to -179 is 2 degrees of ground, not
# 358). Anything narrower is a plain straight edge that covers the longitudes
# between its ends, so a polygon from -100 to +100 occupies 0, not 180.
WRAPPING_EDGE_MIN_SPAN_DEGREES = 270.0


def normalize_longitude(lon: float) -> float:
    """Wrap into [-180, 180).

    Values already in range are returned untouched. The modulo round-trip is not
    exact in binary floating point -- `(179.9 + 180) % 360 - 180` yields
    179.89999999999998 -- and snapshots round to 9 decimal places, so letting that
    error in would produce spurious diffs on data that never needed wrapping.
    """
    if -180.0 <= lon < 180.0:
        return lon
    wrapped = (lon + 180.0) % 360.0 - 180.0
    # (-180 % 360) == 180.0 in Python, which would push -180 to +180.
    return -180.0 if wrapped == 180.0 else wrapped


def largest_longitude_gap(longitudes: Sequence[float]) -> tuple[float, float, float]:
    """Return `(gap_start, gap_end, gap_width)` for the widest empty arc.

    The arc runs eastward from `gap_start` to `gap_end` and contains no data.
    With fewer than two distinct longitudes there is no meaningful gap, so the
    result is a zero-width arc at that longitude.
    """
    values = sorted({normalize_longitude(lon) for lon in longitudes})
    if len(values) < 2:
        only = values[0] if values else 0.0
        return (only, only, 0.0)

    gap_start = values[-1]
    gap_end = values[0]
    # The wrap-around arc, from the easternmost point back to the westernmost.
    gap_width = (values[0] + 360.0) - values[-1]

    for west, east in zip(values, values[1:]):
        width = east - west
        if width > gap_width:
            gap_start, gap_end, gap_width = west, east, width

    return (gap_start, gap_end, gap_width)


def extent_from_longitudes_latitudes(
    longitudes: Sequence[float], latitudes: Sequence[float]
) -> Extent | None:
    """Compute the minimal enclosing extent, handling the antimeridian.

    Returns `None` for empty input -- an absent extent is meaningful (the caller
    falls back to the canvas or to world view) and is not the same as a
    zero-sized one.
    """
    if not longitudes or not latitudes:
        return None

    south = min(latitudes)
    north = max(latitudes)

    gap_start, gap_end, gap_width = largest_longitude_gap(longitudes)

    naive_west = min(normalize_longitude(lon) for lon in longitudes)
    naive_east = max(normalize_longitude(lon) for lon in longitudes)
    naive_width = naive_east - naive_west

    # The wrapped extent is the complement of the empty arc.
    wrapped_width = 360.0 - gap_width

    # Only prefer wrapping when the gap is real and the result is genuinely
    # tighter. Without the tolerance, data that merely happens to be sparse in
    # the middle would be reported as antimeridian-crossing.
    if gap_width >= MIN_MEANINGFUL_GAP_DEGREES and wrapped_width < naive_width:
        return Extent(
            west=gap_end,
            south=south,
            east=gap_start,
            north=north,
            crosses_antimeridian=gap_end > gap_start,
        )

    return Extent(
        west=naive_west,
        south=south,
        east=naive_east,
        north=north,
        crosses_antimeridian=False,
    )


def iter_coordinates(geometry: object) -> Iterable[tuple[float, float]]:
    """Yield `(lon, lat)` from any GeoJSON geometry, at any nesting depth.

    Written against the coordinate arrays rather than the geometry `type` so it
    handles every GeoJSON shape, including `GeometryCollection`, without a case
    per type.
    """
    if isinstance(geometry, dict):
        if "geometries" in geometry:
            for sub in geometry.get("geometries") or ():
                yield from iter_coordinates(sub)
        coords = geometry.get("coordinates")
        if coords is not None:
            yield from iter_coordinates(coords)
        return

    if isinstance(geometry, (list, tuple)):
        # A coordinate pair is a flat sequence of at least two numbers.
        if (
            len(geometry) >= 2
            and isinstance(geometry[0], (int, float))
            and isinstance(geometry[1], (int, float))
        ):
            yield (float(geometry[0]), float(geometry[1]))
            return
        for item in geometry:
            yield from iter_coordinates(item)


def iter_coordinate_runs(geometry: object) -> Iterable[list[tuple[float, float]]]:
    """Yield each line or ring of a GeoJSON geometry as a list of `(lon, lat)`.

    `iter_coordinates` flattens everything, which loses which vertices are joined
    by an edge. A lone point comes out as a run of one.
    """
    if isinstance(geometry, dict):
        for sub in geometry.get("geometries") or ():
            yield from iter_coordinate_runs(sub)
        coords = geometry.get("coordinates")
        if coords is not None:
            yield from iter_coordinate_runs(coords)
        return

    if isinstance(geometry, (list, tuple)):
        if (
            len(geometry) >= 2
            and isinstance(geometry[0], (int, float))
            and isinstance(geometry[1], (int, float))
        ):
            yield [(float(geometry[0]), float(geometry[1]))]
            return
        if geometry and all(
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            for item in geometry
        ):
            yield [(float(item[0]), float(item[1])) for item in geometry]
            return
        for item in geometry:
            yield from iter_coordinate_runs(item)


def _edge_longitudes(start: float, end: float) -> list[float]:
    """Longitudes an edge passes over between its ends, sampled below the gap size.

    Only the interior is returned; the ends are the vertices themselves.
    """
    if abs(end - start) > WRAPPING_EDGE_MIN_SPAN_DEGREES:
        # Cross the antimeridian: walk east from the western end.
        start, end = max(start, end), min(start, end) + 360.0
    step = MIN_MEANINGFUL_GAP_DEGREES / 2.0
    low, high = min(start, end), max(start, end)
    count = int((high - low) / step)
    return [
        low + step * index for index in range(1, count + 1) if low + step * index < high
    ]


def extent_from_geojson(feature_collection: dict) -> Extent | None:
    """Minimal enclosing extent of a GeoJSON FeatureCollection.

    Edges count as well as vertices: a polygon with corners at -100 and +100 and
    nothing between them still occupies the longitudes between, so the empty
    arc the gap search finds is never one a shape actually crosses.
    """
    longitudes: list[float] = []
    latitudes: list[float] = []

    for feature in feature_collection.get("features") or ():
        geometry = feature.get("geometry")
        if not geometry:
            continue
        for run in iter_coordinate_runs(geometry):
            for lon, lat in run:
                longitudes.append(lon)
                latitudes.append(lat)
            for (lon_a, _), (lon_b, _) in zip(run, run[1:]):
                longitudes.extend(_edge_longitudes(lon_a, lon_b))

    return extent_from_longitudes_latitudes(longitudes, latitudes)


def _densify_longitudes(extent: Extent) -> list[float]:
    """Sample an extent's longitude span densely enough to preserve occupancy.

    Contributing only the two corner longitudes would be wrong: the gap
    algorithm would see a wide extent as two isolated points with an enormous
    "empty" arc between them, and wrap it. A layer genuinely spanning -170 to
    +170 would be misread as antimeridian-crossing.

    Sampling below `MIN_MEANINGFUL_GAP_DEGREES` makes any interior gap too small
    to be considered meaningful, so only real separations survive.
    """
    step = MIN_MEANINGFUL_GAP_DEGREES / 2.0
    span = extent.width_degrees
    samples: list[float] = []

    steps = max(1, int(span / step) + 1)
    for index in range(steps + 1):
        samples.append(normalize_longitude(extent.west + span * index / steps))

    return samples


def union_extents(extents: Sequence[Extent | None]) -> Extent | None:
    """Combine per-layer extents into one.

    Each input is densified back into sample longitudes and the whole set is
    recomputed, so a union of two extents that individually do not cross the
    antimeridian can still be detected as crossing when combined -- and a single
    genuinely wide extent is not mistaken for one.
    """
    longitudes: list[float] = []
    latitudes: list[float] = []

    for extent in extents:
        if extent is None:
            continue
        longitudes.extend(_densify_longitudes(extent))
        latitudes.extend([extent.south, extent.north])

    return extent_from_longitudes_latitudes(longitudes, latitudes)
