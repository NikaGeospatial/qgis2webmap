"""The quicker extent walk gives exactly the extent the original gave.

The original `extent_from_geojson` and `extent_from_longitudes_latitudes` are
kept here, and only here, as the oracle. The map opens on this extent and its
numbers are written into the page, so "the same" means bit for bit - the sign
of a zero included - not merely close.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

import pytest

from nika_onlymap_exporter.core import extent_math
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
    return _old_extent_from_longitudes_latitudes(*_old_walk(feature_collection))


def _old_walk(feature_collection: dict) -> tuple[list[float], list[float]]:
    """The longitudes and latitudes the original walk handed on."""
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
    return longitudes, latitudes


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


# --- One float either side of every boundary --------------------------------


def _ulps(value: float, count: int = 2) -> list[float]:
    """`value` and the `count` floats next to it on each side, in order."""
    below, above = [value], [value]
    for _ in range(count):
        below.append(math.nextafter(below[-1], -math.inf))
        above.append(math.nextafter(above[-1], math.inf))
    return [*reversed(below[1:]), value, *above[1:]]


def _walked_alike(monkeypatch: pytest.MonkeyPatch, collection: dict[str, Any]) -> None:
    """The quicker walk hands on the very longitudes and latitudes the
    original did, in order and sign, not merely ones giving the same extent:
    a skipped edge must be one that had nothing to sample."""
    handed: list[tuple[list[float], list[float]]] = []
    real = extent_math.extent_from_longitudes_latitudes

    def recording(
        longitudes: Sequence[float], latitudes: Sequence[float]
    ) -> Extent | None:
        handed.append((list(longitudes), list(latitudes)))
        return real(longitudes, latitudes)

    monkeypatch.setattr(extent_math, "extent_from_longitudes_latitudes", recording)
    extent = extent_from_geojson(collection)
    monkeypatch.undo()
    longitudes, latitudes = _old_walk(collection)
    assert [list(map(repr, part)) for part in handed[0]] == [
        list(map(repr, longitudes)),
        list(map(repr, latitudes)),
    ]
    assert _exactly(extent) == _exactly(_old_extent_from_geojson(collection))


def _edge(west: float, east: float) -> dict[str, Any]:
    return {
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[west, 0.0], [east, 1.0]],
                },
                "properties": {},
            }
        ]
    }


# Edge ends one sampling step apart. The last pair's difference rounds to
# exactly one step though the true one is a little more, and its one sample
# lies just inside the far end: an edge exactly one step long cannot be
# skipped.
ONE_STEP_EDGES = [
    (0.0, 1.0),
    (10.25, 11.25),
    (-0.5, 0.5),
    (-180.0, -179.0),
    (179.0, 180.0),
    (-0.625 - 2**-53, 0.375),
]

# Edge ends where the edge starts to be taken the short way across the
# antimeridian, 270 degrees apart, and ends whose way across is one step.
WRAPPING_EDGES = [
    (-135.0, 135.0),
    (-180.0, 90.0),
    (-90.0, 180.0),
    (179.5, -179.5),
    (-180.0, 180.0),
]


def test_an_edge_of_exactly_one_step_can_carry_a_sample() -> None:
    low, high = ONE_STEP_EDGES[-1]
    assert high - low == extent_math.MIN_MEANINGFUL_GAP_DEGREES / 2.0
    assert _edge_longitudes(low, high) == [high - 2**-53]


@pytest.mark.parametrize("backwards", [False, True], ids=["eastward", "westward"])
@pytest.mark.parametrize(("start", "end"), ONE_STEP_EDGES + WRAPPING_EDGES, ids=repr)
def test_edges_a_float_from_a_boundary_walk_alike(
    monkeypatch: pytest.MonkeyPatch, start: float, end: float, backwards: bool
) -> None:
    """Each edge with its far end moved up to two floats either way: just
    short of one step, the edges the quicker walk skips, and just past it;
    either side of where an edge is taken across the antimeridian."""
    for moved in _ulps(end):
        ends = (moved, start) if backwards else (start, moved)
        _walked_alike(monkeypatch, _edge(*ends))


def test_the_wrap_boundary_is_live() -> None:
    """Up to 270 degrees an edge covers the longitudes between its ends: one
    float further its span still rounds to 270 and only gains the sample its
    far end moved past. Two floats further it goes the other way round."""
    ends = _ulps(135.0)[2:]
    assert [len(_edge_longitudes(-135.0, end)) for end in ends] == [269, 270, 89]


POINT_COORDINATES: list[object] = [
    [10, 20],
    [10.0, 20.0],
    [-0, 0],
    [2**53 + 1, -(2**53) - 1],
    [True, False],
    (10.5, 20.5),
    [1.0, 2.0, 3.0, 4.0],
    *([lon, 0.0] for lon in _ulps(-180.0)),
    *([lon, 0.0] for lon in _ulps(180.0)),
    *([lon, 0.0] for lon in _ulps(0.0)),
    [1.0, "2"],
    ["1", 2.0],
    [1.0, None],
    [1.0],
    [[1.0, 2.0]],
    [[1.0, 2.0], [3.0, 4.0]],
]


@pytest.mark.parametrize("coordinates", POINT_COORDINATES, ids=repr)
def test_point_coordinates_walk_alike(
    monkeypatch: pytest.MonkeyPatch, coordinates: object
) -> None:
    """The Point shortcut on every kind of coordinate it may meet: integers
    and bools (converted as the walker converts them), a tuple, extra
    dimensions, longitudes a float either side of the ends of the range and
    of zero, and coordinates it must leave to the walker. Alone, and beside
    a point at the far side of the world, so the range ends decide the gap."""
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
    }
    far = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-90.0, 0.0]},
    }
    _walked_alike(monkeypatch, {"features": [point]})
    _walked_alike(monkeypatch, {"features": [point, far]})


def test_a_coordinate_past_any_float_fails_alike() -> None:
    collection = {
        "features": [{"geometry": {"type": "Point", "coordinates": [10**400, 0]}}]
    }
    with pytest.raises(OverflowError):
        _old_extent_from_geojson(collection)
    with pytest.raises(OverflowError):
        extent_from_geojson(collection)
