"""Where on the zoom scale labels stop piling up, and where points stop merging.

QGIS hides a label that would collide with another; the web map has no such
step and draws them all, so a dense layer zoomed out is a pile of text. The
runtime has no collision setting either. What it does have is a per-layer zoom
range and point clustering, so this works out the zoom at which a layer's
labels mostly fit - and hides them until then - and the zoom at which a dense
point layer's markers have room, clustering them into count bubbles until
then.

Pure, so it is measured rather than guessed: positions go through the same
Web Mercator the map uses, and a label's size is estimated from its text and
font size. Estimated, not measured - the browser's font is not known here -
which is why the rule asks for most labels to fit, not every one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence

from .export_ir import ExportLayer, GeometryKind
from .label_points import (
    GLYPH_WIDTH,
    LABEL_PROPERTY,
    RepeatedLabels,
    build_label_collection_noting_repeats,
)

# A layer this small reads fine unclustered at any zoom.
CLUSTER_MIN_POINTS = 100

# The share of labels (or markers) that must have room before the layer shows
# them. Not 100%: one stubborn pair in a dense corner should not hide every
# other label until street level.
FIT_SHARE = 0.9

# Past this nothing is gained: the runtime's own zoom range tops out near here.
MAX_ZOOM = 18

_LINE_HEIGHT = 1.3
_PADDING_PX = 4.0


def _unit(lon: float, lat: float) -> tuple[float, float]:
    """Web Mercator position on a 1 x 1 world; times `256 * 2**zoom` it is the
    pixel position at `zoom`, 256-px tiles.

    The sine and logarithm do not depend on the zoom, so a layer pays for them
    once rather than once per zoom tried. Scaling afterwards is the same
    arithmetic in the same order as working each zoom out from scratch, so
    the pixels are exactly the ones that would give.
    """
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180.0) / 360.0
    sin = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)
    return x, y


def _clashes_allowed(count: int) -> int:
    """The most of `count` boxes that may be left out with `FIT_SHARE` still kept.

    Found with the very comparison the share is judged by, so stopping one
    clash past it can never disagree with placing every box and dividing.
    """
    allowed = min(count, int(count * (1 - FIT_SHARE)) + 1)
    while allowed > 0 and (count - allowed) / count < FIT_SHARE:
        allowed -= 1
    return allowed


# Cell (gx, gy) is filed under one integer, gx * _ROW + gy, and its eight
# neighbours are fixed offsets from it - no key tuple to build per lookup. Two
# cells could share a key only far beyond any map's pixel range, and even then
# only extra boxes would be looked at: a box overlapping another always lies
# in a neighbouring cell, so a far one never clashes.
_ROW = 1 << 32
_NEIGHBOURS = tuple(dx * _ROW + dy for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def _enough_fit(
    units: Sequence[tuple[float, float]],
    halves: Sequence[tuple[float, float]],
    scale: float,
    cell: float,
) -> bool:
    """Greedy placement, as a labeller does it: each box in turn, kept unless it
    overlaps one already kept. Whether `FIT_SHARE` of them are kept. Spatially
    hashed so a 25,000-point layer is still quick, and stopped at the first
    clash too many: the boxes after it cannot bring the share back.

    `halves` is each box's half width and half height, worked out once for
    every zoom tried."""
    if not units:
        return True
    allowed = _clashes_allowed(len(units))
    grid: dict[int, list[tuple[float, float, float, float]]] = {}
    filed = grid.get
    clashes = 0
    for (ux, uy), (half_w, half_h) in zip(units, halves):
        x, y = ux * scale, uy * scale
        left, top, right, bottom = x - half_w, y - half_h, x + half_w, y + half_h
        key = int(x // cell) * _ROW + int(y // cell)
        for neighbour in _NEIGHBOURS:
            for ol, ot, orr, ob in filed(key + neighbour, ()):
                if left < orr and right > ol and top < ob and bottom > ot:
                    break
            else:
                continue
            break
        else:
            kept = filed(key)
            if kept is None:
                grid[key] = [(left, top, right, bottom)]
            else:
                kept.append((left, top, right, bottom))
            continue
        clashes += 1
        if clashes > allowed:
            return False
    return True


def _distinct(
    points: Sequence[tuple[float, float]], sizes: Sequence[tuple[float, float]]
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """One box per position, as large as the largest there.

    For labels only. Points at the same spot never separate at any zoom, so
    counting each one let a layer of repeat samples at a few sites hold its
    labels back to the last zoom; they are one pile on screen, counted as one.
    Clustering counts every point: a stack is exactly what a count bubble is
    for, and collapsing it would draw thousands of markers on top of each
    other with nothing saying how many.
    """
    boxes: dict[tuple[float, float], tuple[float, float]] = {}
    for key, (w, h) in zip(_spots(points), sizes):
        kept = boxes.get(key)
        boxes[key] = (w, h) if kept is None else (max(kept[0], w), max(kept[1], h))
    return list(boxes), list(boxes.values())


def _first_zoom_that_fits(
    points: Sequence[tuple[float, float]], sizes: Sequence[tuple[float, float]]
) -> int | None:
    """The lowest zoom where `FIT_SHARE` of the boxes have room; `None` at zoom 0.

    A binary search: zooming in only moves points apart, so once they fit they
    keep fitting. Five placements instead of up to nineteen, which is what keeps
    a 25,000-point layer under a second on a live-preview rebuild.
    """

    units = [_unit(lon, lat) for lon, lat in points]
    halves = [(w / 2, h / 2) for w, h in sizes]
    cell = max(
        max((w for w, _ in sizes), default=0.0),
        max((h for _, h in sizes), default=0.0),
        1.0,
    )

    def fits(zoom: int) -> bool:
        return _enough_fit(units, halves, 256.0 * (2**zoom), cell)

    if fits(0):
        return None
    low, high = 0, MAX_ZOOM  # `low` never fits; `high` is the answer if none does.
    while high - low > 1:
        middle = (low + high) // 2
        if fits(middle):
            high = middle
        else:
            low = middle
    return high


def label_min_zoom(
    labels: Sequence[tuple[float, float, str]], font_px: float
) -> int | None:
    """The zoom from which a layer's labels mostly fit, or `None` if they always do.

    `labels` is `(lon, lat, text)` for each label point.
    """
    if len(labels) < 2:
        return None
    points = [(lon, lat) for lon, lat, _ in labels]
    sizes = [_label_box(text, font_px) for _, _, text in labels]
    return _first_zoom_that_fits(*_distinct(points, sizes))


def _label_box(text: str, font_px: float) -> tuple[float, float]:
    """A label's estimated width and height in pixels, padding included."""
    # A wrapped label is as wide as its longest line and a line taller per
    # break.
    lines = text.split("\n")
    return (
        max(1, *(len(line) for line in lines)) * GLYPH_WIDTH * font_px + _PADDING_PX,
        len(lines) * _LINE_HEIGHT * font_px + _PADDING_PX,
    )


def repeats_have_room(
    labels: Sequence[tuple[float, float, str]],
    neighbours: Sequence[tuple[int, int]],
    font_px: float,
) -> int | None:
    """The zoom from which no label overlaps the next one along its line, or
    `None` if none ever does.

    `neighbours` pairs the indexes into `labels` (`(lon, lat, text)`) of labels
    that follow each other along one line (`label_points.RepeatedLabels`).
    Repeats are fixed along the line, so zoomed out they close up into a smear
    of one name; the greedy share in `label_min_zoom` would let a few long
    lines do that among many short ones, so every pair is held to it. Two
    boxes `dx` and `dy` apart in the unit world stop overlapping once one of
    those gaps, times `256 * 2**zoom`, covers the box's width or height.
    """
    zoom = 0
    for first, second in neighbours:
        lon_a, lat_a, text = labels[first]
        lon_b, lat_b, _ = labels[second]
        (xa, ya), (xb, yb) = _unit(lon_a, lat_a), _unit(lon_b, lat_b)
        width, height = _label_box(text, font_px)
        gaps = [
            size / gap
            for size, gap in ((width, abs(xb - xa)), (height, abs(yb - ya)))
            if gap > 0
        ]
        if not gaps:
            return MAX_ZOOM
        needed = math.ceil(math.log2(min(gaps) / 256.0))
        if needed >= MAX_ZOOM:
            return MAX_ZOOM
        zoom = max(zoom, needed)
    return zoom or None


def cluster_max_zoom(
    points: Sequence[tuple[float, float]], radius_px: float
) -> int | None:
    """The last zoom at which a dense point layer should still cluster.

    `None` - draw every point - for a layer under `CLUSTER_MIN_POINTS`, or one
    whose markers have room even zoomed right out.
    """
    if len(points) < CLUSTER_MIN_POINTS:
        return None
    side = 2 * radius_px + _PADDING_PX
    zoom = _first_zoom_that_fits(points, [(side, side)] * len(points))
    return None if zoom is None else max(0, zoom - 1)


def sites_part_zoom(
    points: Sequence[tuple[float, float]], radius_px: float
) -> int | None:
    """The last zoom at which separate sites still merge, stacks counted once.

    What labels wait for. A stack of samples at one spot stays a single bubble
    at every zoom, so waiting for it to split held a layer's labels back to the
    last zoom; a bubble at one spot wearing that spot's name is not the mix-up
    the wait exists to prevent.
    """
    if len(points) < CLUSTER_MIN_POINTS:
        return None
    side = 2 * radius_px + _PADDING_PX
    sites, sizes = _distinct(points, [(side, side)] * len(points))
    zoom = _first_zoom_that_fits(sites, sizes)
    return None if zoom is None else max(0, zoom - 1)


def _spots(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Each point's spot: its position as finely as stacks are told apart."""
    return [(round(lon, 7), round(lat, 7)) for lon, lat in points]


def _point_positions(
    geojson: dict[str, object] | None,
) -> list[tuple[float, float]] | None:
    """Every feature's position, or `None` unless every feature is a Point.

    The runtime clusters Point features only, so a layer mixing in a MultiPoint
    is left unclustered rather than half-clustered.
    """
    if not geojson:
        return None
    positions: list[tuple[float, float]] = []
    for feature in geojson.get("features", ()):  # type: ignore[attr-defined]
        geometry = (feature or {}).get("geometry") or {}
        if geometry.get("type") != "Point":
            return None
        lon, lat = geometry["coordinates"][:2]
        positions.append((float(lon), float(lat)))
    return positions


def declutter_layer(layer: ExportLayer, default_font_px: float) -> ExportLayer:
    """`layer` with its `cluster_max_zoom` and `label_min_zoom` filled in.

    See `declutter_noting_stacks`, which also says whether the clustered
    points include stacks, and what repeating the labels came to.
    """
    return declutter_noting_stacks(layer, default_font_px)[0]


def declutter_noting_stacks(
    layer: ExportLayer, default_font_px: float
) -> tuple[ExportLayer, bool, RepeatedLabels]:
    """`layer` with its `cluster_max_zoom` and `label_min_zoom` filled in,
    whether it clusters points of which some share one spot, from the
    positions already read rather than a second pass over them, and what
    repeating its line labels came to.

    Labels of a clustered layer wait until bubbles stop merging separate
    sites: a count bubble over several places wearing one place's name would
    be wrong. A stack at one spot never splits, and does not hold them back.
    Labels repeated along a line wait until the repeats stop overlapping.

    A label limit at or below the layer's own minimum zoom changes nothing on
    the map, so it is left out rather than reported.
    """
    repeated = RepeatedLabels()
    if layer.raster is not None or layer.tiles is not None or not layer.geojson:
        return layer, False, repeated

    clusters: int | None = None
    sites: int | None = None
    stacked = False
    if layer.geometry_kind is GeometryKind.POINT and layer.cluster_allowed:
        positions = _point_positions(layer.geojson)
        if positions is not None:
            symbol = layer.renderer.representative_symbol
            if layer.icon_atlas is not None and symbol is not None and symbol.icon_size:
                radius = symbol.icon_size / 2
            else:
                radius = (symbol.radius if symbol is not None else 0.0) or 4.0
            clusters = cluster_max_zoom(positions, radius)
            if clusters is not None:
                spots = _spots(positions)
                stacked = len(set(spots)) < len(positions)
                if not stacked and spots == positions:
                    # Every point is its own site, at exactly its own position:
                    # the search over the sites is the one just made.
                    sites = clusters
                else:
                    sites = sites_part_zoom(positions, radius)

    labels: int | None = None
    labeling = layer.labeling
    if labeling.enabled and labeling.field_name:
        font_px = labeling.font_size or default_font_px
        collection, repeated = build_label_collection_noting_repeats(
            layer.geojson,
            labeling.field_name,
            capitalization=labeling.capitalization,
            wrap_char=labeling.wrap_char,
            auto_wrap_length=labeling.auto_wrap_length,
            per_part=labeling.per_part,
            plane=labeling.measure_plane,
            repeat=labeling.repeat,
            font_px=font_px,
        )
        if collection is not None:
            placed = [
                (
                    float(f["geometry"]["coordinates"][0]),
                    float(f["geometry"]["coordinates"][1]),
                    str(f["properties"][LABEL_PROPERTY]),
                )
                for f in collection["features"]
            ]
            labels = label_min_zoom(placed, font_px)
            room = repeats_have_room(placed, repeated.neighbours, font_px)
            if room is not None:
                labels = max(labels or 0, room)
            if sites is not None:
                labels = max(labels or 0, sites + 1)
            floor = layer.visible_zoom_range[0] if layer.visible_zoom_range else 0.0
            if labels is not None and labels <= floor:
                labels = None

    if clusters is None and labels is None:
        return layer, stacked, repeated
    return (
        dataclasses.replace(layer, cluster_max_zoom=clusters, label_min_zoom=labels),
        stacked,
        repeated,
    )
