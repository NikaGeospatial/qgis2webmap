"""Where a label sits.

OnlyMap draws labels with deck.gl's `TextLayer`, which takes a **point** per
label. QGIS labels a feature of any geometry, so a line or polygon needs one
representative point computed for it.

Why a separate, reduced FeatureCollection rather than reusing the layer's own
data: the geometry layer's GeoJSON is the single largest thing in the artifact,
and a `TextLayer` needs none of it -- only a coordinate and the label string.
Emitting label points as their own small collection keeps a labelled export from
paying for its geometry twice, which on a polygon layer is the difference
between a few kilobytes and a few megabytes.

Pure Python: no PyQGIS, no Qt, so this is unit-testable without QGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# The property name label points carry. Deliberately not the source field name:
# the field could be called "type" or "id" and collide with something the
# runtime reads, and the TextLayer only ever needs one string per point.
LABEL_PROPERTY = "label"


def _ring_centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float] | None:
    """Area-weighted centroid of a closed ring, by the shoelace formula.

    The vertex average is the tempting one-liner and it is wrong: it pulls the
    point toward wherever the vertices happen to be dense, so a polygon with a
    finely-tesselated edge labels off to that side. Area weighting is stable
    against how the ring was digitised.

    Returns `None` for a degenerate (zero-area) ring so the caller can fall back
    rather than divide by zero.
    """
    if len(ring) < 3:
        return None

    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, [*ring[1:], ring[0]]):
        cross = x0 * y1 - x1 * y0
        area2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    if area2 == 0.0:
        return None

    return cx / (3.0 * area2), cy / (3.0 * area2)


def _vertex_average(coordinates: Sequence[Sequence[float]]) -> tuple[float, float]:
    xs = [float(c[0]) for c in coordinates]
    ys = [float(c[1]) for c in coordinates]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _middle_vertex(coordinates: Sequence[Sequence[float]]) -> tuple[float, float]:
    """The vertex nearest the middle of a line.

    Not the midpoint of the bounding box, and not the centroid: both can land
    far off a curved or L-shaped line. A vertex is always on the feature.
    """
    middle = coordinates[len(coordinates) // 2]
    return float(middle[0]), float(middle[1])


def representative_point(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """A point at which to draw this geometry's label, or `None`.

    `None` means the geometry is empty or of a kind 0.1.0 does not label; the
    caller drops the label rather than guessing a coordinate.
    """
    if not geometry:
        return None

    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not coordinates:
        return None

    if kind == "Point":
        return float(coordinates[0]), float(coordinates[1])

    if kind == "MultiPoint":
        return float(coordinates[0][0]), float(coordinates[0][1])

    if kind == "LineString":
        return _middle_vertex(coordinates)

    if kind == "MultiLineString":
        # The longest part by vertex count: the label belongs on the piece a
        # reader is most likely to be looking at.
        longest = max(coordinates, key=len)
        return _middle_vertex(longest)

    if kind == "Polygon":
        outer = coordinates[0]
        # An empty or two-vertex outer ring reaches here from real data (a
        # cleared geometry, a digitising mistake). `_ring_centroid` returns None
        # for it, so the fallback has to be guarded too or it divides by zero
        # and takes the whole export down for one unlabellable feature.
        if len(outer) < 3:
            return None
        return _ring_centroid(outer) or _vertex_average(outer)

    if kind == "MultiPolygon":
        # Largest part by absolute shoelace area, so a country labels on its
        # mainland rather than on an offshore island.
        best_ring = None
        best_area = -1.0
        for polygon in coordinates:
            ring = polygon[0]
            if len(ring) < 3:
                continue
            area = abs(
                sum(
                    x0 * y1 - x1 * y0
                    for (x0, y0), (x1, y1) in zip(ring, [*ring[1:], ring[0]])
                )
            )
            if area > best_area:
                best_area = area
                best_ring = ring
        if best_ring is None:
            return None
        return _ring_centroid(best_ring) or _vertex_average(best_ring)

    if kind == "GeometryCollection":
        for member in geometry.get("geometries", ()):
            point = representative_point(member)
            if point is not None:
                return point

    return None


def build_label_collection(
    geojson: dict[str, Any] | None,
    field_name: str,
) -> dict[str, Any] | None:
    """A point FeatureCollection carrying one label per labelled feature.

    Features whose label value is empty are skipped outright -- deck.gl renders
    an empty string as an invisible glyph that still consumes a picking slot,
    and a map full of those makes hover feel broken.

    Returns `None` when nothing would be labelled, so the caller can omit the
    `TextLayer` entirely rather than emit an empty one.
    """
    if not geojson:
        return None

    features = []
    for feature in geojson.get("features", ()):
        properties = feature.get("properties") or {}
        raw = properties.get(field_name)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue

        point = representative_point(feature.get("geometry"))
        if point is None:
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # Rounded to ~1cm. Label anchors do not need more, and full
                    # float repr would bloat the payload for no visible gain.
                    "coordinates": [round(point[0], 7), round(point[1], 7)],
                },
                "properties": {LABEL_PROPERTY: text},
            }
        )

    if not features:
        return None

    return {"type": "FeatureCollection", "features": features}


def collect_character_set(collection: dict[str, Any] | None) -> str | None:
    """The distinct characters the labels actually use.

    `TextLayer` builds a font atlas from a fixed ASCII range by default, so any
    character outside it renders blank. Declaring the exact set is what makes
    accented, Cyrillic or CJK labels appear at all.

    Returns `None` when every character is plain ASCII, so the common case emits
    no attribute.
    """
    if not collection:
        return None

    characters: set[str] = set()
    for feature in collection.get("features", ()):
        characters.update(str(feature["properties"][LABEL_PROPERTY]))

    if all(ord(c) < 128 for c in characters):
        return None

    # Sorted so the attribute is byte-stable across runs -- a deterministic
    # artifact is what makes a regression visible in a diff.
    return "".join(sorted(characters))
