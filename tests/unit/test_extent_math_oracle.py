"""The quicker extent walk gives exactly the extent the original gave.

The original `extent_from_geojson` and `extent_from_longitudes_latitudes` are
kept here, and only here, as the oracle. The map opens on this extent and its
numbers are written into the page, so "the same" means bit for bit - the sign
of a zero included - not merely close.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

import pytest

from nika_onlymap_exporter.core.export_ir import Extent
from nika_onlymap_exporter.core.extent_math import (
    _edge_longitudes,
    extent_from_geojson,
    extent_from_longitudes_latitudes,
    iter_coordinate_runs,
    largest_longitude_gap,
    normalize_longitude,
    union_extents,
)

# --- The original implementation --------------------------------------------


def _old_extent_from_longitudes_latitudes(
    longitudes: Sequence[float], latitudes: Sequence[float]
) -> Extent | None:
    if not longitudes or not latitudes:
        return None
    south = min(latitudes)
    north = max(latitudes)
    gap_start, gap_end, gap_width = largest_longitude_gap(longitudes)
    naive_west = min(normalize_longitude(lon) for lon in longitudes)
    naive_east = max(normalize_longitude(lon) for lon in longitudes)
    naive_width = naive_east - naive_west
    wrapped_width = 360.0 - gap_width
    if gap_width >= 2.0 and wrapped_width < naive_width:
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


def _old_extent_from_geojson(feature_collection: dict) -> Extent | None:
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
    return _old_extent_from_longitudes_latitudes(longitudes, latitudes)


def _exactly(extent: Extent | None) -> tuple[str, ...] | None:
    """Every field as its repr, so 0.0 and -0.0 differ as they do in the page."""
    if extent is None:
        return None
    return (
        repr(extent.west),
        repr(extent.south),
        repr(extent.east),
        repr(extent.north),
        repr(extent.crosses_antimeridian),
    )


# --- Inputs -----------------------------------------------------------------


def _collections() -> list[tuple[str, dict[str, Any]]]:
    rng = random.Random(20261102)

    def lon(west: float, east: float) -> float:
        return round(rng.uniform(west, east), rng.choice((0, 3, 9)))

    def lat() -> float:
        return round(rng.uniform(-89.9, 89.9), 9)

    def ring(west: float, east: float, count: int) -> list[list[float]]:
        points = [[lon(west, east), lat()] for _ in range(count)]
        return [*points, points[0]]

    def feature(geometry: object) -> dict[str, Any]:
        return {"type": "Feature", "geometry": geometry, "properties": {}}

    sets: list[tuple[str, dict[str, Any]]] = [
        ("empty", {"type": "FeatureCollection", "features": []}),
        ("no features key", {"type": "FeatureCollection"}),
        (
            "null and empty geometries",
            {
                "features": [
                    feature(None),
                    feature({}),
                    feature({"type": "Point", "coordinates": []}),
                    feature({"type": "Point", "coordinates": [5.0]}),
                    feature({"type": "Point", "coordinates": [1.5, 2.5]}),
                ]
            },
        ),
        (
            "integers, bools and 3D",
            {
                "features": [
                    feature({"type": "Point", "coordinates": [10, 20]}),
                    feature({"type": "Point", "coordinates": [True, False]}),
                    feature({"type": "Point", "coordinates": [-170.5, 3.0, 812.0]}),
                    feature({"type": "LineString", "coordinates": [[1, 2], [30, 4]]}),
                ]
            },
        ),
        (
            "a point carrying geometries",
            {
                "features": [
                    feature(
                        {
                            "type": "Point",
                            "coordinates": [1.0, 1.0],
                            "geometries": [
                                {"type": "Point", "coordinates": [150.0, -5.0]}
                            ],
                        }
                    ),
                ]
            },
        ),
        (
            "signed zeros",
            {
                "features": [
                    feature({"type": "Point", "coordinates": [-0.0, -0.0]}),
                    feature({"type": "Point", "coordinates": [0.0, 0.0]}),
                    feature(
                        {
                            "type": "LineString",
                            "coordinates": [[-0.0, 1.0], [0.5, -0.0]],
                        }
                    ),
                ]
            },
        ),
        (
            "out of range longitudes",
            {
                "features": [
                    feature({"type": "Point", "coordinates": [180.0, 0.0]}),
                    feature({"type": "Point", "coordinates": [-180.0, 1.0]}),
                    feature({"type": "Point", "coordinates": [190.25, 2.0]}),
                    feature({"type": "Point", "coordinates": [-1e-20, 3.0]}),
                    feature({"type": "Point", "coordinates": [-540.0, 4.0]}),
                ]
            },
        ),
    ]
    for name, west, east in (
        ("regional", 100.0, 110.0),
        ("world", -180.0, 179.999),
        ("antimeridian", 170.0, 190.0),
        ("narrow", 3.0, 3.0001),
    ):
        for count in (1, 5, 200):
            features = []
            for _ in range(count):
                kind = rng.choice(
                    (
                        "Point",
                        "MultiPoint",
                        "LineString",
                        "MultiLineString",
                        "Polygon",
                        "MultiPolygon",
                        "GeometryCollection",
                    )
                )
                if kind == "Point":
                    geometry = {
                        "type": kind,
                        "coordinates": [normalize_longitude(lon(west, east)), lat()],
                    }
                elif kind == "MultiPoint":
                    geometry = {
                        "type": kind,
                        "coordinates": [[lon(west, east), lat()] for _ in range(3)],
                    }
                elif kind == "LineString":
                    geometry = {
                        "type": kind,
                        "coordinates": [
                            [normalize_longitude(lon(west, east)), lat()]
                            for _ in range(rng.randint(2, 6))
                        ],
                    }
                elif kind == "MultiLineString":
                    geometry = {
                        "type": kind,
                        "coordinates": [
                            [[lon(west, east), lat()] for _ in range(3)]
                            for _ in range(2)
                        ],
                    }
                elif kind == "Polygon":
                    geometry = {"type": kind, "coordinates": [ring(west, east, 5)]}
                elif kind == "MultiPolygon":
                    geometry = {
                        "type": kind,
                        "coordinates": [[ring(west, east, 4)], [ring(west, east, 3)]],
                    }
                else:
                    geometry = {
                        "type": kind,
                        "geometries": [
                            {"type": "Point", "coordinates": [lon(west, east), lat()]},
                            {
                                "type": "LineString",
                                "coordinates": [
                                    [lon(west, east), lat()],
                                    [lon(west, east), lat()],
                                ],
                            },
                        ],
                    }
                features.append(feature(geometry))
            sets.append((f"{name} x{count}", {"features": features}))
    # Only points, the shape the fast path is for, across the 180th meridian.
    sets.append(
        (
            "points across the antimeridian",
            {
                "features": [
                    feature(
                        {
                            "type": "Point",
                            "coordinates": [
                                rng.choice((-1, 1)) * rng.uniform(175.0, 180.0),
                                lat(),
                            ],
                        }
                    )
                    for _ in range(500)
                ]
            },
        )
    )
    return sets


COLLECTIONS = _collections()


@pytest.mark.parametrize(
    ("name", "collection"), COLLECTIONS, ids=[name for name, _ in COLLECTIONS]
)
def test_geojson_extent_is_unchanged(name: str, collection: dict[str, Any]) -> None:
    assert _exactly(extent_from_geojson(collection)) == _exactly(
        _old_extent_from_geojson(collection)
    )


def test_longitude_latitude_extent_is_unchanged() -> None:
    rng = random.Random(7)
    cases: list[list[float]] = [
        [],
        [0.0],
        [-0.0, 0.0],
        [0.0, -0.0],
        [5.0, -0.0, 0.0],
        [-180.0, 180.0],
        [179.9, -179.9],
        [-1e-20, 359.99999, 720.5, -900.0],
    ]
    for _ in range(300):
        count = rng.randint(1, 40)
        cases.append(
            [
                rng.choice(
                    (
                        rng.uniform(-180.0, 180.0),
                        rng.uniform(150.0, 210.0),
                        rng.uniform(-540.0, 540.0),
                        round(rng.uniform(-10.0, 10.0), 1),
                        float(rng.randint(-180, 179)),
                    )
                )
                for _ in range(count)
            ]
        )
    for longitudes in cases:
        latitudes = [rng.uniform(-90.0, 90.0) for _ in longitudes]
        assert _exactly(
            extent_from_longitudes_latitudes(longitudes, latitudes)
        ) == _exactly(_old_extent_from_longitudes_latitudes(longitudes, latitudes)), (
            longitudes
        )
        # The union densifies through the same function.
        extent = _old_extent_from_longitudes_latitudes(longitudes, latitudes)
        assert _exactly(union_extents([extent, extent])) == _exactly(_old_union(extent))


def _old_union(extent: Extent | None) -> Extent | None:
    from nika_onlymap_exporter.core.extent_math import _densify_longitudes

    if extent is None:
        return None
    longitudes = _densify_longitudes(extent) * 2
    return _old_extent_from_longitudes_latitudes(
        longitudes, [extent.south, extent.north] * 2
    )
