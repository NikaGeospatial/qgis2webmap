"""Where a label sits.

OnlyMap draws labels with deck.gl's `TextLayer`, which takes a **point** per
label. QGIS labels a feature of any geometry, so a line or polygon needs one
representative point computed for it - or for each of its parts, when QGIS
labels every part of a multi-part feature.

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

import math
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
    # Read each vertex by index, never `for (x0, y0) in ...`. A layer whose
    # wkbType carries Z - digitised with Z on, draped over a DEM, imported from
    # CityGML - exports `[x, y, z]`, and destructuring two names out of three
    # raised ValueError and aborted the whole export. The elevation is not a
    # label coordinate, so it is dropped here the same way every other function
    # in this module drops it.
    for start, end in zip(ring, [*ring[1:], ring[0]]):
        x0, y0 = start[0], start[1]
        x1, y1 = end[0], end[1]
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


def _line_length(coordinates: Sequence[Sequence[float]]) -> float:
    """Planar length of a line in its own coordinates, Z dropped."""
    return sum(
        math.hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(coordinates, coordinates[1:])
    )


def representative_point(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """A point at which to draw this geometry's label once, or `None`.

    One point for the whole geometry; `label_anchors` is every point QGIS
    labels it at, which for a MultiPoint is each of its points.

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
        # The longest part, measured along it, as QGIS picks it when it labels
        # a multi-part line once. Not the part with the most vertices: a long
        # straight road of two vertices beside a short winding spur of fifty
        # labelled the spur.
        parts = [part for part in coordinates if part]
        if not parts:
            return None
        return _middle_vertex(max(parts, key=_line_length))

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
            # Indexed, not destructured, for the Z reason in `_ring_centroid`.
            area = abs(
                sum(
                    start[0] * end[1] - end[0] * start[1]
                    for start, end in zip(ring, [*ring[1:], ring[0]])
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


def label_anchors(
    geometry: dict[str, Any] | None, per_part: bool = False
) -> list[tuple[float, float]]:
    """Every point QGIS draws a label of this geometry at, in part order.

    Measured against QGIS 4.2 by rendering a multi-part feature of each kind
    and reading back where its labels landed (`QgsLabelingResults`):

    * a MultiPoint is labelled at every one of its points, whether or not
      "Label every part of multi-part features" (`per_part`) is on;
    * a MultiLineString or MultiPolygon is labelled once, on its longest or
      largest part, unless `per_part` is on, and then once on every part.

    Empty for a geometry `representative_point` would not label.
    """
    if not geometry:
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or ()

    if kind == "MultiPoint":
        return [
            (float(point[0]), float(point[1]))
            for point in coordinates
            if len(point) >= 2
        ]

    if per_part and kind in ("MultiLineString", "MultiPolygon"):
        single = "LineString" if kind == "MultiLineString" else "Polygon"
        parts = (
            representative_point({"type": single, "coordinates": part})
            for part in coordinates
        )
        return [point for point in parts if point is not None]

    if per_part and kind == "GeometryCollection":
        return [
            point
            for member in geometry.get("geometries", ())
            for point in label_anchors(member, per_part)
        ]

    point = representative_point(geometry)
    return [] if point is None else [point]


def apply_capitalization(text: str, capitalization: str) -> str:
    """QGIS's text case, applied to the string rather than to a style.

    The web renderer has no `text-transform`, and QGIS does not have one either
    - it changes the glyphs it draws. So doing it here is not a workaround, it
    is the same operation in the same place.

    `title` uses `str.title()`'s word boundaries deliberately: QGIS's own title
    case is `QgsStringUtils`, whose rules for apostrophes and hyphens differ in
    edge cases ("O'Brien"), and reimplementing them from the outside would drift
    against a version we do not control. The difference shows on a handful of
    names; getting the common case right and staying predictable is worth more.
    """
    if capitalization == "upper":
        return text.upper()
    if capitalization == "lower":
        return text.lower()
    if capitalization == "capitalize":
        # QGIS's "Force first letter to capital" raises the first letter and
        # leaves the rest of the string exactly as the data has it, which is
        # NOT `str.capitalize()` - that lowercases the remainder and would turn
        # "USA Route" into "Usa route".
        return text[:1].upper() + text[1:] if text else text
    if capitalization == "title":
        return text.title()
    return text


def apply_wrapping(text: str, wrap_char: str, auto_wrap_length: int) -> str:
    """Turn QGIS's line breaking into real newlines, which deck.gl honours.

    Two independent mechanisms, and QGIS applies the explicit one first:

    * `wrap_char` is a character the author types to force a break (often `|`).
    * `auto_wrap_length` breaks lines longer than N characters, on whitespace.

    Emitting `\\n` rather than a `text-max-width` attribute keeps the break
    where the author put it. A width-based wrap would re-flow on a different
    font and put the break somewhere else.
    """
    if wrap_char:
        for character in wrap_char:
            text = text.replace(character, "\n")

    if auto_wrap_length and auto_wrap_length > 0:
        text = "\n".join(
            _wrap_line(line, auto_wrap_length) for line in text.split("\n")
        )
    return text


def _wrap_line(line: str, width: int) -> str:
    """Greedy wrap on whitespace, never mid-word.

    `textwrap` is not used: it collapses runs of whitespace and strips, so a
    label's own spacing would change even when it was short enough to need no
    wrapping at all.
    """
    if len(line) <= width:
        return line

    lines: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def build_label_collection(
    geojson: dict[str, Any] | None,
    field_name: str,
    capitalization: str = "none",
    wrap_char: str = "",
    auto_wrap_length: int = 0,
    per_part: bool = False,
) -> dict[str, Any] | None:
    """A point FeatureCollection carrying a labelled feature's label at each
    point QGIS draws it at (`label_anchors`): one per feature, or one per part
    of a multi-part feature with `per_part`, and always one per point of a
    MultiPoint.

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

        # Case first, then wrapping: uppercasing after a wrap would not change
        # where the breaks fell, but wrapping after uppercasing does - "st" and
        # "ST" are the same width in characters, and QGIS counts characters too.
        text = apply_capitalization(text, capitalization)
        text = apply_wrapping(text, wrap_char, auto_wrap_length)

        for point in label_anchors(feature.get("geometry"), per_part):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        # Rounded to ~1cm. Label anchors do not need more, and
                        # full float repr would bloat the payload for no
                        # visible gain.
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
