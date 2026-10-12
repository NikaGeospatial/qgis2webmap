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
from .label_points import LABEL_PROPERTY, build_label_collection

# A layer this small reads fine unclustered at any zoom.
CLUSTER_MIN_POINTS = 100

# The share of labels (or markers) that must have room before the layer shows
# them. Not 100%: one stubborn pair in a dense corner should not hide every
# other label until street level.
FIT_SHARE = 0.9

# Past this nothing is gained: the runtime's own zoom range tops out near here.
MAX_ZOOM = 18

# Average glyph width as a share of the font size, for a typical sans-serif.
_GLYPH_WIDTH = 0.6
_LINE_HEIGHT = 1.3
_PADDING_PX = 4.0


def _pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Web Mercator pixel position at `zoom`, 256-px tiles."""
    scale = 256.0 * (2**zoom)
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180.0) / 360.0 * scale
    sin = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * scale
    return x, y


def _share_that_fits(
    boxes: Sequence[tuple[float, float, float, float]],
) -> float:
    """Greedy placement, as a labeller does it: each box in turn, kept unless it
    overlaps one already kept. Returns the share kept. Spatially hashed so a
    25,000-point layer is still quick."""
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
    for (lon, lat), (w, h) in zip(points, sizes):
        key = (round(lon, 7), round(lat, 7))
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

    def fits(zoom: int) -> bool:
        boxes = [
            (*_pixel(lon, lat, zoom), w, h) for (lon, lat), (w, h) in zip(points, sizes)
        ]
        return _share_that_fits(boxes) >= FIT_SHARE

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
    sizes = []
    for _, _, text in labels:
        # A wrapped label is as wide as its longest line and a line taller
        # per break.
        lines = text.split("\n")
        sizes.append(
            (
                max(1, *(len(line) for line in lines)) * _GLYPH_WIDTH * font_px
                + _PADDING_PX,
                len(lines) * _LINE_HEIGHT * font_px + _PADDING_PX,
            )
        )
    return _first_zoom_that_fits(*_distinct(points, sizes))


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


def has_stacks(layer: ExportLayer) -> bool:
    """Whether some of a point layer's points share one spot exactly."""
    positions = _point_positions(layer.geojson)
    if not positions:
        return False
    return len({(round(x, 7), round(y, 7)) for x, y in positions}) < len(positions)


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

    Labels of a clustered layer wait until bubbles stop merging separate
    sites: a count bubble over several places wearing one place's name would
    be wrong. A stack at one spot never splits, and does not hold them back.

    A label limit at or below the layer's own minimum zoom changes nothing on
    the map, so it is left out rather than reported.
    """
    if layer.raster is not None or layer.tiles is not None or not layer.geojson:
        return layer

    clusters: int | None = None
    sites: int | None = None
    if layer.geometry_kind is GeometryKind.POINT:
        positions = _point_positions(layer.geojson)
        if positions is not None:
            symbol = layer.renderer.representative_symbol
            if layer.icon_atlas is not None and symbol is not None and symbol.icon_size:
                radius = symbol.icon_size / 2
            else:
                radius = (symbol.radius if symbol is not None else 0.0) or 4.0
            clusters = cluster_max_zoom(positions, radius)
            if clusters is not None:
                sites = sites_part_zoom(positions, radius)

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
            labels = label_min_zoom(
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
