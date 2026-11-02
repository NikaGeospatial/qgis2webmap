"""The quicker declutter search gives exactly the zooms the original gave.

The original implementation is kept here, and only here, as the oracle: the
zooms it chose are what shipped, and the faster one must choose the same on
every input - dense, sparse, stacked, at the poles and across the 180th
meridian, empty and single.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections.abc import Sequence

import pytest

from nika_onlymap_exporter.core import declutter
from nika_onlymap_exporter.core.declutter import (
    CLUSTER_MIN_POINTS,
    FIT_SHARE,
    MAX_ZOOM,
    _clashes_allowed,
    _enough_fit,
    _point_positions,
    _unit,
    cluster_max_zoom,
    declutter_noting_stacks,
    label_min_zoom,
    sites_part_zoom,
)
from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    GeometryKind,
    LabelingSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.label_points import (
    LABEL_PROPERTY,
    build_label_collection,
)

# --- The original implementation, verbatim apart from names -----------------


def _old_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2**zoom)
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180.0) / 360.0 * scale
    sin = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * scale
    return x, y


def _old_share_that_fits(boxes: Sequence[tuple[float, float, float, float]]) -> float:
    if not boxes:
        return 1.0
    cell = max(max(w for _, _, w, _ in boxes), max(h for _, _, _, h in boxes), 1.0)
    grid: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    kept = 0
    for x, y, w, h in boxes:
        left, top, right, bottom = x - w / 2, y - h / 2, x + w / 2, y + h / 2
        gx, gy = int(x // cell), int(y // cell)
        clash = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ol, ot, orr, ob in grid.get((gx + dx, gy + dy), ()):
                    if left < orr and right > ol and top < ob and bottom > ot:
                        clash = True
                        break
                if clash:
                    break
            if clash:
                break
        if not clash:
            grid.setdefault((gx, gy), []).append((left, top, right, bottom))
            kept += 1
    return kept / len(boxes)


def _old_distinct(points, sizes):  # type: ignore[no-untyped-def]
    boxes: dict[tuple[float, float], tuple[float, float]] = {}
    for (lon, lat), (w, h) in zip(points, sizes):
        key = (round(lon, 7), round(lat, 7))
        kept = boxes.get(key)
        boxes[key] = (w, h) if kept is None else (max(kept[0], w), max(kept[1], h))
    return list(boxes), list(boxes.values())


def _old_fits(points, sizes, zoom: int) -> bool:  # type: ignore[no-untyped-def]
    boxes = [
        (*_old_pixel(lon, lat, zoom), w, h) for (lon, lat), (w, h) in zip(points, sizes)
    ]
    return _old_share_that_fits(boxes) >= FIT_SHARE


def _old_first_zoom_that_fits(points, sizes) -> int | None:  # type: ignore[no-untyped-def]
    if _old_fits(points, sizes, 0):
        return None
    low, high = 0, MAX_ZOOM
    while high - low > 1:
        middle = (low + high) // 2
        if _old_fits(points, sizes, middle):
            high = middle
        else:
            low = middle
    return high


def _old_label_min_zoom(labels, font_px: float) -> int | None:  # type: ignore[no-untyped-def]
    if len(labels) < 2:
        return None
    points = [(lon, lat) for lon, lat, _ in labels]
    sizes = []
    for _, _, text in labels:
        lines = text.split("\n")
        sizes.append(
            (
                max(1, *(len(line) for line in lines)) * 0.6 * font_px + 4.0,
                len(lines) * 1.3 * font_px + 4.0,
            )
        )
    return _old_first_zoom_that_fits(*_old_distinct(points, sizes))


def _old_cluster_max_zoom(points, radius_px: float) -> int | None:  # type: ignore[no-untyped-def]
    if len(points) < CLUSTER_MIN_POINTS:
        return None
    side = 2 * radius_px + 4.0
    zoom = _old_first_zoom_that_fits(points, [(side, side)] * len(points))
    return None if zoom is None else max(0, zoom - 1)


def _old_sites_part_zoom(points, radius_px: float) -> int | None:  # type: ignore[no-untyped-def]
    if len(points) < CLUSTER_MIN_POINTS:
        return None
    side = 2 * radius_px + 4.0
    sites, sizes = _old_distinct(points, [(side, side)] * len(points))
    zoom = _old_first_zoom_that_fits(sites, sizes)
    return None if zoom is None else max(0, zoom - 1)


def _old_has_stacks(layer: ExportLayer) -> bool:
    positions = _point_positions(layer.geojson)
    if not positions:
        return False
    return len({(round(x, 7), round(y, 7)) for x, y in positions}) < len(positions)


def _old_declutter_layer(layer: ExportLayer, default_font_px: float) -> ExportLayer:
    if layer.raster is not None or layer.tiles is not None or not layer.geojson:
        return layer
    clusters: int | None = None
    sites: int | None = None
    if layer.geometry_kind is GeometryKind.POINT and layer.cluster_allowed:
        positions = _point_positions(layer.geojson)
        if positions is not None:
            symbol = layer.renderer.representative_symbol
            if layer.icon_atlas is not None and symbol is not None and symbol.icon_size:
                radius = symbol.icon_size / 2
            else:
                radius = (symbol.radius if symbol is not None else 0.0) or 4.0
            clusters = _old_cluster_max_zoom(positions, radius)
            if clusters is not None:
                sites = _old_sites_part_zoom(positions, radius)
    labels: int | None = None
    labeling = layer.labeling
    if labeling.enabled and labeling.field_name:
        collection = build_label_collection(
            layer.geojson,
            labeling.field_name,
            capitalization=labeling.capitalization,
            wrap_char=labeling.wrap_char,
            auto_wrap_length=labeling.auto_wrap_length,
        )
        if collection is not None:
            labels = _old_label_min_zoom(
                [
                    (
                        float(f["geometry"]["coordinates"][0]),
                        float(f["geometry"]["coordinates"][1]),
                        str(f["properties"][LABEL_PROPERTY]),
                    )
                    for f in collection["features"]
                ],
                labeling.font_size or default_font_px,
            )
            if sites is not None:
                labels = max(labels or 0, sites + 1)
            floor = layer.visible_zoom_range[0] if layer.visible_zoom_range else 0.0
            if labels is not None and labels <= floor:
                labels = None
    if clusters is None and labels is None:
        return layer
    return dataclasses.replace(layer, cluster_max_zoom=clusters, label_min_zoom=labels)


# --- Inputs -----------------------------------------------------------------


def _point_sets() -> list[tuple[str, list[tuple[float, float]]]]:
    """Seeded point sets covering every shape the search meets."""
    rng = random.Random(20261102)
    sets: list[tuple[str, list[tuple[float, float]]]] = [
        ("empty", []),
        ("single", [(103.8, 1.35)]),
        ("one spot", [(10.0, 20.0)] * 150),
    ]
    for decimals in (9, 7):
        for count in (2, 11, 20, 100, 101, 400):
            for spread in (0.0005, 0.05, 5.0, 90.0):
                lon0 = rng.uniform(-170.0, 170.0)
                lat0 = rng.uniform(-70.0, 70.0)
                points = [
                    (
                        round(lon0 + rng.uniform(-spread, spread), decimals),
                        round(lat0 + rng.uniform(-spread, spread) / 2, decimals),
                    )
                    for _ in range(count)
                ]
                sets.append((f"{decimals}dp n={count} spread={spread}", points))
        # Repeat samples at a few sites, shuffled among singletons.
        sites = [
            (
                round(rng.uniform(-60, 60), decimals),
                round(rng.uniform(-40, 40), decimals),
            )
            for _ in range(8)
        ]
        stacked = [rng.choice(sites) for _ in range(160)] + [
            (
                round(rng.uniform(-60, 60), decimals),
                round(rng.uniform(-40, 40), decimals),
            )
            for _ in range(60)
        ]
        rng.shuffle(stacked)
        sets.append((f"{decimals}dp stacks", stacked))
        # Across the 180th meridian.
        sets.append(
            (
                f"{decimals}dp antimeridian",
                [
                    (
                        round(
                            rng.choice((-1, 1)) * rng.uniform(179.0, 180.0), decimals
                        ),
                        round(rng.uniform(-10, 10), decimals),
                    )
                    for _ in range(300)
                ],
            )
        )
        # At and past the Mercator limit, where latitudes clamp.
        sets.append(
            (
                f"{decimals}dp poles",
                [
                    (
                        round(rng.uniform(-180, 180), decimals),
                        round(rng.choice((-1, 1)) * rng.uniform(84.0, 90.0), decimals),
                    )
                    for _ in range(300)
                ]
                + [(0.0, 90.0), (0.0, -90.0), (-180.0, 85.05112878)] * 5,
            )
        )
    # Exactly the same position written as 0.0 and -0.0.
    sets.append(("signed zeros", [(0.0, 0.0), (-0.0, -0.0)] * 60))
    # Pairs a hair closer than two markers at zoom 17, each end written past
    # the 7 decimals stacks are told apart by. Rounded to the sites, the
    # pairs part one zoom sooner than the points do: no stacks, and still not
    # the same search.
    near = 12 * 360 / (256.0 * 2**17)
    sets.append(
        (
            "rounding parts the sites",
            [
                point
                for i in range(60)
                for point in (
                    (10.00000004 + i, 0.0),
                    (10.00000004 + i + near - 1e-9, 0.0),
                )
            ],
        )
    )
    return sets


POINT_SETS = _point_sets()
IDS = [name for name, _ in POINT_SETS]


# --- The checks -------------------------------------------------------------


@pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
def test_scaled_positions_are_the_same_pixels(
    name: str, points: list[tuple[float, float]]
) -> None:
    """Bit for bit, not approximately: a box that just touches another must
    touch it in both."""
    for lon, lat in points:
        ux, uy = _unit(lon, lat)
        for zoom in range(MAX_ZOOM + 1):
            scale = 256.0 * (2**zoom)
            assert (ux * scale, uy * scale) == _old_pixel(lon, lat, zoom), (
                name,
                lon,
                lat,
                zoom,
            )


class TestEveryZoomDecidesAlike:
    """The per-zoom verdict, the step every search is built from."""

    @pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
    def test_markers(self, name: str, points: list[tuple[float, float]]) -> None:
        units = [_unit(lon, lat) for lon, lat in points]
        for side in (12.0, 64.0):
            sizes = [(side, side)] * len(points)
            halves = [(side / 2, side / 2)] * len(points)
            for zoom in range(MAX_ZOOM + 1):
                expected = _old_fits(points, sizes, zoom)
                assert (
                    _enough_fit(units, halves, 256.0 * (2**zoom), max(side, 1.0))
                    is expected
                ), (name, side, zoom)

    @pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
    def test_labels_of_mixed_sizes(
        self, name: str, points: list[tuple[float, float]]
    ) -> None:
        rng = random.Random(name)
        sizes = [(rng.uniform(4, 120), rng.uniform(8, 40)) for _ in points]
        units = [_unit(lon, lat) for lon, lat in points]
        cell = max([1.0, *(w for w, _ in sizes), *(h for _, h in sizes)])
        halves = [(w / 2, h / 2) for w, h in sizes]
        for zoom in range(MAX_ZOOM + 1):
            expected = _old_fits(points, sizes, zoom)
            assert _enough_fit(units, halves, 256.0 * (2**zoom), cell) is expected, (
                name,
                zoom,
            )


@pytest.mark.parametrize("backwards", [False, True], ids=["forwards", "backwards"])
@pytest.mark.parametrize("vertical", [False, True], ids=["row", "column"])
@pytest.mark.parametrize("gap", [0.0, -1e-9, 1e-9], ids=["touching", "over", "apart"])
def test_boxes_that_just_touch_decide_alike(
    vertical: bool, gap: float, backwards: bool
) -> None:
    """Where the comparison itself decides: a row (or column) of boxes exactly
    one box apart touches without overlapping, and a hair closer every other
    one clashes - placed in either direction, so each edge is the one met.
    Positions on exact binary fractions, so nothing rounds."""
    scale = 256.0 * 2**10
    side, height = 12.0, 20.0
    step = (height if vertical else side) + gap
    units = [
        (0.25, 0.25 + i * step / scale) if vertical else (0.25 + i * step / scale, 0.25)
        for i in range(200)
    ]
    if backwards:
        units.reverse()
    boxes = [(ux * scale, uy * scale, side, height) for ux, uy in units]
    expected = _old_share_that_fits(boxes) >= FIT_SHARE
    assert expected is (gap >= 0.0)
    halves = [(side / 2, height / 2)] * len(units)
    assert _enough_fit(units, halves, scale, height) is expected


@pytest.mark.parametrize("gap", [0, -1, 1], ids=["touching", "over", "apart"])
def test_markers_that_just_touch_cluster_alike(gap: int) -> None:
    """The same edge case through the whole search: on the equator, at
    longitudes on exact binary fractions of the world, markers 12 px apart at
    zoom 10 just touch there, a 2**-20 of a pixel closer they overlap."""
    step = 12 * 2**20 + gap  # in 2**-20 px at zoom 10, of 2**38 per world
    points = [(-180.0 + 360.0 * (2**30 + i * step) / 2**38, 0.0) for i in range(150)]
    assert cluster_max_zoom(points, 4.0) == _old_cluster_max_zoom(points, 4.0)
    assert sites_part_zoom(points, 4.0) == _old_sites_part_zoom(points, 4.0)
    labels = [(lon, lat, "Twelve px") for lon, lat in points]
    assert label_min_zoom(labels, 1.0) == _old_label_min_zoom(labels, 1.0)


@pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
@pytest.mark.parametrize("radius", [2.0, 4.0, 15.0])
def test_cluster_and_site_zooms_match(
    name: str, points: list[tuple[float, float]], radius: float
) -> None:
    assert cluster_max_zoom(points, radius) == _old_cluster_max_zoom(points, radius)
    assert sites_part_zoom(points, radius) == _old_sites_part_zoom(points, radius)


@pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
def test_label_zooms_match(name: str, points: list[tuple[float, float]]) -> None:
    rng = random.Random(name)
    words = ["A", "Peak", "Long label text", "Two\nlines", "Ünïcødé name"]
    labels = [(lon, lat, rng.choice(words)) for lon, lat in points]
    for font_px in (8.0, 12.0, 20.0):
        assert label_min_zoom(labels, font_px) == _old_label_min_zoom(labels, font_px)


def _layer(points: list[tuple[float, float]], **overrides: object) -> ExportLayer:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": f"Place {i % 37}"},
        }
        for i, (lon, lat) in enumerate(points)
    ]
    fields: dict[str, object] = dict(
        layer_id="places",
        name="Places",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=len(points),
        geojson={"type": "FeatureCollection", "features": features},
        renderer=RendererSpec(kind=RendererKind.SINGLE, symbol=SymbolSpec(radius=4)),
        labeling=LabelingSpec(enabled=True, field_name="name"),
    )
    fields.update(overrides)
    return ExportLayer(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(("name", "points"), POINT_SETS, ids=IDS)
def test_whole_layer_matches_and_stacks_are_noted(
    name: str, points: list[tuple[float, float]]
) -> None:
    for overrides in (
        {},
        {"labeling": LabelingSpec()},
        {"visible_zoom_range": (6.0, 22.0)},
        {"cluster_allowed": False},
        # Text small enough that the labels wait on the sites, not on room.
        {"labeling": LabelingSpec(enabled=True, field_name="name", font_size=1)},
        {
            "renderer": RendererSpec(
                kind=RendererKind.SINGLE, symbol=SymbolSpec(radius=12)
            ),
            "labeling": LabelingSpec(enabled=True, field_name="name", font_size=3),
        },
    ):
        layer = _layer(points, **overrides)
        result, stacked = declutter_noting_stacks(layer, 12.0)
        expected = _old_declutter_layer(layer, 12.0)
        assert result == expected, (name, overrides)
        assert (result is layer) is (expected is layer)
        # The report asks only of a clustered layer.
        if result.cluster_max_zoom is not None:
            assert stacked is _old_has_stacks(result), (name, overrides)
        assert declutter.declutter_layer(layer, 12.0) == expected


def test_the_clash_budget_is_the_share_rule_exactly() -> None:
    """`_clashes_allowed(n)` is the largest c with (n - c) / n >= FIT_SHARE,
    for every layer size up to a quarter of a million points."""
    for count in range(1, 250_001):
        allowed = _clashes_allowed(count)
        assert (count - allowed) / count >= FIT_SHARE, count
        assert allowed == count or (count - allowed - 1) / count < FIT_SHARE, count


# --- One float either side of every boundary --------------------------------


def _ulps(value: float, count: int = 2) -> list[float]:
    """`value` and the `count` floats next to it on each side, in order."""
    below, above = [value], [value]
    for _ in range(count):
        below.append(math.nextafter(below[-1], -math.inf))
        above.append(math.nextafter(above[-1], math.inf))
    return [*reversed(below[1:]), value, *above[1:]]


@pytest.mark.parametrize("backwards", [False, True], ids=["forwards", "backwards"])
@pytest.mark.parametrize("vertical", [False, True], ids=["row", "column"])
def test_boxes_one_float_from_touching_decide_alike(
    vertical: bool, backwards: bool
) -> None:
    """Two boxes exactly touching, then the second one float closer or
    further: one float closer clashes, touching or further does not. In a row
    the pair shares a grid cell, in a column it straddles two. Pixels sit in
    one binade and the scale is a power of two, so every edge is exact."""
    scale = 256.0 * 2**2
    side, height = 12.0, 20.0
    first = (1000.25, 1000.25)
    touching = first[1] + height if vertical else first[0] + side
    verdicts = []
    for along in _ulps(touching):
        second = (first[0], along) if vertical else (along, first[1])
        pixels = [second, first] if backwards else [first, second]
        boxes = [(x, y, side, height) for x, y in pixels]
        expected = _old_share_that_fits(boxes) >= FIT_SHARE
        units = [(x / scale, y / scale) for x, y in pixels]
        halves = [(side / 2, height / 2)] * 2
        assert _enough_fit(units, halves, scale, height) is expected, along
        verdicts.append(expected)
    assert verdicts == [False, False, True, True, True]


@pytest.mark.parametrize("vertical", [False, True], ids=["row", "column"])
@pytest.mark.parametrize(
    ("boundary", "scale"),
    [(12.0 * 50, 256.0 * 2**2), (12.0 * 5_000_000, 256.0 * 2**18)],
    ids=["zoom 2", "zoom 18"],
)
def test_boxes_around_grid_cell_edges_decide_alike(
    vertical: bool, boundary: float, scale: float
) -> None:
    """Every pair of positions within two floats of a cell edge, or of a box
    side either way of it: pairs in one cell, in neighbouring cells and two
    apart, a float short of touching, touching and a float past it. One cell
    is one box side, so a clash across an edge is only found through the
    neighbouring cell's key, at zoom 18's pixel range as much as at zoom 2's."""
    side = 12.0
    across = 300.5 * scale / 1024
    places = [
        *_ulps(boundary - side),
        *_ulps(boundary - side / 2),
        *_ulps(boundary),
        *_ulps(boundary + side),
    ]
    verdicts = set()
    clashes_across_an_edge = 0
    for a in places:
        for b in places:
            pixels = (
                [(across, a), (across, b)] if vertical else [(a, across), (b, across)]
            )
            boxes = [(x, y, side, side) for x, y in pixels]
            expected = _old_share_that_fits(boxes) >= FIT_SHARE
            units = [(x / scale, y / scale) for x, y in pixels]
            halves = [(side / 2, side / 2)] * 2
            assert _enough_fit(units, halves, scale, side) is expected, (a, b)
            verdicts.add(expected)
            if not expected and a // side != b // side:
                clashes_across_an_edge += 1
    assert verdicts == {True, False}
    assert clashes_across_an_edge


def _on_seventh_decimals(count: int) -> list[float]:
    """Longitudes 0.001 apart, each exactly the float `round(_, 7)` gives."""
    return [round(10.0 + index * 0.001, 7) for index in range(count)]


def _rounding_layers() -> list[tuple[str, list[tuple[float, float]], bool]]:
    """Point layers whose positions are, or are a float or two off, what
    rounding to seven decimals gives - whether the sites are the points
    themselves turns on exactly that. The flag is whether they are."""
    lons = _on_seventh_decimals(150)
    halfway = [float(f"{lon:.7f}5") for lon in _on_seventh_decimals(30)]
    return [
        ("on the seventh decimal", [(lon, 5.0) for lon in lons], True),
        (
            "a float above",
            [(math.nextafter(lon, math.inf), 5.0) for lon in lons],
            False,
        ),
        (
            "a float below",
            [(math.nextafter(lon, -math.inf), 5.0) for lon in lons],
            False,
        ),
        (
            "one point a float off",
            [(math.nextafter(lons[0], math.inf), 5.0)] + [(x, 5.0) for x in lons[1:]],
            False,
        ),
        (
            "latitude a float off",
            [(lon, math.nextafter(5.0, -math.inf)) for lon in lons],
            False,
        ),
        (
            "stacks a float apart",
            [
                point
                for lon in lons[:75]
                for point in ((lon, 5.0), (math.nextafter(lon, math.inf), 5.0))
            ],
            False,
        ),
        (
            "either side of half-way",
            [(lon, 5.0) for middle in halfway for lon in _ulps(middle)],
            False,
        ),
    ]


ROUNDING_LAYERS = _rounding_layers()


@pytest.mark.parametrize(
    ("name", "points", "sites_are_points"),
    ROUNDING_LAYERS,
    ids=[name for name, _, _ in ROUNDING_LAYERS],
)
def test_sites_a_float_from_the_seventh_decimal_match(
    name: str, points: list[tuple[float, float]], sites_are_points: bool
) -> None:
    """The site search is skipped only when rounding to seven decimals leaves
    every point where it is; a float either way, or a pair one float apart
    or either side of a half-way, must still come out as the original."""
    assert (declutter._spots(points) == points) is sites_are_points
    if name == "either side of half-way":
        # Each five floats round to two spots, so a stack is two piles.
        assert len(set(declutter._spots(points))) == 2 * len(points) // 5
    for overrides in (
        {},
        {"labeling": LabelingSpec(enabled=True, field_name="name", font_size=1)},
    ):
        layer = _layer(points, **overrides)
        result, stacked = declutter_noting_stacks(layer, 12.0)
        assert result == _old_declutter_layer(layer, 12.0), (name, overrides)
        assert result.cluster_max_zoom is not None, name
        assert stacked is _old_has_stacks(result), (name, overrides)


@pytest.mark.parametrize("count", [10, 11, 19, 20, 99, 100, 101, 1000])
@pytest.mark.parametrize("where", ["first", "last", "spread"])
def test_one_clash_either_side_of_the_budget_decides_alike(
    count: int, where: str
) -> None:
    """Exactly the clashes the share allows fit; one more does not, wherever
    in the order the clashing boxes come."""
    allowed = _clashes_allowed(count)
    scale = 256.0 * 2**12
    side = 12.0
    for clashes in (allowed - 1, allowed, allowed + 1):
        if not 0 <= clashes < count:
            continue
        apart = [(100.0 + 100.0 * index, 100.0) for index in range(count - clashes)]
        repeats = [apart[0]] * clashes
        if where == "first":
            pixels = [apart[0], *repeats, *apart[1:]]
        elif where == "last":
            pixels = [*apart, *repeats]
        else:
            pixels = list(apart)
            for number, repeat in enumerate(repeats):
                pixels.insert(1 + number * 2, repeat)
        boxes = [(x, y, side, side) for x, y in pixels]
        expected = _old_share_that_fits(boxes) >= FIT_SHARE
        assert expected is (clashes <= allowed)
        units = [(x / scale, y / scale) for x, y in pixels]
        halves = [(side / 2, side / 2)] * len(pixels)
        assert _enough_fit(units, halves, scale, side) is expected, (count, clashes)
