"""Where a label sits.

OnlyMap draws labels with deck.gl's `TextLayer`, which takes a **point** per
label. QGIS labels a feature of any geometry, so a line or polygon needs one
representative point computed for it - or for each of its parts, when QGIS
labels every part of a multi-part feature - and a long line several, where its
labels repeat along it.

Why a separate, reduced FeatureCollection rather than reusing the layer's own
data: the geometry layer's GeoJSON is the single largest thing in the artifact,
and a `TextLayer` needs none of it -- only a coordinate and the label string.
Emitting label points as their own small collection keeps a labelled export from
paying for its geometry twice, which on a polygon layer is the difference
between a few kilobytes and a few megabytes.

Pure Python: no PyQGIS, no Qt, so this is unit-testable without QGIS - but for
a line measured in a projected CRS other than Web Mercator, which QGIS projects
(`crs_plane`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import bisect
import itertools
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .export_ir import LineLabelRepeat

# The property name label points carry. Deliberately not the source field name:
# the field could be called "type" or "id" and collide with something the
# runtime reads, and the TextLayer only ever needs one string per point.
LABEL_PROPERTY = "label"

# The planes a line is measured along. QGIS measures a line in its map CRS, as
# a flat plane: "mercator" is Web Mercator, the plane the web map draws in and
# exactly QGIS's in EPSG:3857, and "degrees" is longitude and latitude taken as
# flat, which is QGIS's own measure in a geographic CRS such as EPSG:4326. The
# two put the middle of a long north-south line apart by a few per cent of its
# length. Any other projected CRS is a plane of its own, named by `crs_plane`.
MEASURE_PLANES = ("mercator", "degrees")

_CRS_PLANE = "crs:"

_EARTH_RADIUS = 6_378_137.0
_MAX_MERCATOR_LATITUDE = 85.05112878
_RADIANS = math.pi / 180.0

# The most labels repeats put on one line (or part), and on one layer. QGIS
# places repeats afresh at every scale; the web map's are fixed along the line,
# so a short repeat on a long line, or on a big layer, would be thousands of
# labels carried in the file and drawn at every zoom. Past either cap the
# repeats spread further apart, and the Fidelity tab says so.
MAX_LABELS_PER_LINE = 50
MAX_LABELS_PER_LAYER = 5_000

# Average glyph width as a share of the font size, for a typical sans-serif:
# how wide a label is estimated to be, here and in `declutter`.
GLYPH_WIDTH = 0.6


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


def crs_plane(wkt: str) -> str:
    """The measuring plane of the projected CRS `wkt`, which QGIS projects."""
    return _CRS_PLANE + wkt


_Projection = tuple[
    Callable[[float, float], tuple[float, float]],
    Callable[[float, float], tuple[float, float]],
]
_PROJECTIONS = threading.local()


def _projection(plane: str) -> _Projection:
    """To and from the CRS a `crs_plane` names, through QGIS. One pair per
    thread and plane: a `QgsCoordinateTransform` is not to be shared across
    threads, and making one for every line would cost more than the line."""
    cache: dict[str, _Projection] = getattr(_PROJECTIONS, "cache", None) or {}
    _PROJECTIONS.cache = cache
    if plane not in cache:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsCoordinateTransformContext,
            QgsPointXY,
        )

        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsCoordinateReferenceSystem.fromWkt(plane[len(_CRS_PLANE) :]),
            QgsCoordinateTransformContext(),
        )
        reverse = QgsCoordinateTransform.TransformDirection.ReverseTransform

        def forward(lon: float, lat: float) -> tuple[float, float]:
            point = transform.transform(QgsPointXY(lon, lat))
            return point.x(), point.y()

        def inverse(x: float, y: float) -> tuple[float, float]:
            point = transform.transform(QgsPointXY(x, y), reverse)
            return point.x(), point.y()

        cache[plane] = (forward, inverse)
    return cache[plane]


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    """The "mercator" plane, for a point a CRS plane cannot project."""
    return to_plane((lon, lat), "mercator")


def to_plane(point: Sequence[float], plane: str) -> tuple[float, float]:
    """A longitude and latitude (Z dropped) in the measuring `plane`."""
    lon, lat = float(point[0]), float(point[1])
    if plane == "degrees":
        return lon, lat
    if plane.startswith(_CRS_PLANE):
        try:
            return _projection(plane)[0](lon, lat)
        except Exception:  # QgsCsException: outside the CRS's world
            return _mercator(lon, lat)
    lat = max(-_MAX_MERCATOR_LATITUDE, min(_MAX_MERCATOR_LATITUDE, lat))
    # asinh(tan(lat)) is Mercator's ln(tan(pi/4 + lat/2)), in one call.
    return (
        _EARTH_RADIUS * lon * _RADIANS,
        _EARTH_RADIUS * math.asinh(math.tan(lat * _RADIANS)),
    )


def _from_plane(x: float, y: float, plane: str) -> tuple[float, float]:
    if plane == "degrees":
        return x, y
    if plane.startswith(_CRS_PLANE):
        return _projection(plane)[1](x, y)
    return (
        math.degrees(x / _EARTH_RADIUS),
        math.degrees(2 * math.atan(math.exp(y / _EARTH_RADIUS)) - math.pi / 2),
    )


class _Line:
    """A line in a measuring plane, with how far along it each vertex is."""

    def __init__(self, coordinates: Sequence[Sequence[float]], plane: str) -> None:
        self.plane = plane
        self.vertices = [(float(c[0]), float(c[1])) for c in coordinates if len(c) >= 2]
        # Inlined, and the running length summed in C: this is every vertex of
        # every labelled line, twice per export (to declutter the layer and to
        # write it), about 1.5 s for 500,000 vertices.
        if plane == "degrees":
            self.points = self.vertices
        elif plane.startswith(_CRS_PLANE):
            forward = _projection(plane)[0]
            try:
                self.points = [forward(lon, lat) for lon, lat in self.vertices]
            except Exception:  # QgsCsException: a vertex outside the CRS's world
                self.plane = "mercator"
                self.points = [_mercator(lon, lat) for lon, lat in self.vertices]
        else:
            scale = _EARTH_RADIUS * _RADIANS
            limit = _MAX_MERCATOR_LATITUDE
            self.points = [  # `to_plane`'s arithmetic, inlined
                (
                    lon * scale,
                    _EARTH_RADIUS
                    * math.asinh(math.tan(max(-limit, min(limit, lat)) * _RADIANS)),
                )
                for lon, lat in self.vertices
            ]
        self.along = list(
            itertools.accumulate(
                map(math.dist, self.points, self.points[1:]), initial=0.0
            )
        )

    @property
    def length(self) -> float:
        return self.along[-1]

    def at(self, distance: float) -> tuple[float, float]:
        """The point `distance` along the line, as a longitude and latitude.

        On a vertex it is that vertex exactly, not one through the plane and
        back, which Web Mercator's logarithm moves in the last digit.
        """
        if len(self.points) == 1:
            return self.vertices[0]
        index = min(
            max(bisect.bisect_left(self.along, distance), 1), len(self.along) - 1
        )
        start, end = self.along[index - 1], self.along[index]
        share = 0.0 if end == start else (distance - start) / (end - start)
        if share <= 0.0 or share >= 1.0:
            return self.vertices[index - 1 if share <= 0.0 else index]
        (x0, y0), (x1, y1) = self.points[index - 1], self.points[index]
        return _from_plane(x0 + (x1 - x0) * share, y0 + (y1 - y0) * share, self.plane)


def representative_point(
    geometry: dict[str, Any] | None, plane: str = "mercator"
) -> tuple[float, float] | None:
    """A point at which to draw this geometry's label once, or `None`.

    One point for the whole geometry; `label_anchors` is every point QGIS
    labels it at, which for a MultiPoint is each of its points. A line is
    measured in `plane` (`MEASURE_PLANES`).

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

    if kind in ("LineString", "MultiLineString"):
        # Half the line's length along it, as QGIS 4.2 labels a line: measured
        # by rendering lines whose vertices crowd one end and reading back
        # where the label landed. Not the middle vertex, which on a two-vertex
        # line is one of its ends, and not the middle of the bounding box,
        # which can lie off an L-shaped line.
        lines = _labelled_lines(geometry, False, plane) or []
        return lines[0].at(lines[0].length / 2) if lines else None

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
            point = representative_point(member, plane)
            if point is not None:
                return point

    return None


def _labelled_lines(
    geometry: dict[str, Any] | None, per_part: bool, plane: str
) -> list[_Line] | None:
    """The lines QGIS labels a line geometry along, `None` for another kind.

    A multi-part line is labelled along its longest part, measured along it,
    or along every part with `per_part`. Not the part with the most vertices:
    a long straight road of two vertices beside a short winding spur of fifty
    labelled the spur.
    """
    if not geometry:
        return None
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or ()
    if kind == "LineString":
        parts = [coordinates]
    elif kind == "MultiLineString":
        parts = [part for part in coordinates if part]
    else:
        return None
    lines = [line for line in (_Line(part, plane) for part in parts) if line.points]
    if per_part or not lines:
        return lines
    return [max(lines, key=lambda line: line.length)]


def _repeat_distances(
    length: float, repeat: LineLabelRepeat, label_width: float, scale: float
) -> tuple[list[float], bool]:
    """How far along a line of `length` its labels sit, and whether
    `MAX_LABELS_PER_LINE` cut them short. `repeat.distance` is widened by
    `scale`; `label_width` is the label's width in the same units.

    QGIS's own repeat, measured against QGIS 4.2 by rendering a line and reading
    back where its labels landed: a line longer than the distance is cut into
    as many equal pieces as fit at least that long - stretched to a whole
    multiple of it where the label is longer, so labels never overlap - and
    each piece is labelled at its middle. Three pieces put one label at the
    line's middle; two put none there.

    The web map's default keeps QGIS's single label at the middle and adds
    labels either side of it at the distance, none nearer an end than half of
    it, so a line too short to need repeats is labelled exactly as in QGIS.
    """
    distance = repeat.distance * scale
    middle = length / 2
    if not (distance > 0 and length > distance):
        return [middle], False
    interval = distance * max(1, math.ceil(label_width / distance))
    if repeat.from_qgis:
        # Divided, then floored, as QGIS does: `10 // 0.2` is 49 in floating
        # point where `floor(10 / 0.2)` is the 50 QGIS draws.
        pieces = math.floor(length / interval)
        if pieces <= 1:
            return [middle], False
        capped = pieces > MAX_LABELS_PER_LINE
        pieces = min(pieces, MAX_LABELS_PER_LINE)
        step = length / pieces
        return [step * (index + 0.5) for index in range(pieces)], capped
    side = max(0, math.floor((middle - interval / 2) / interval))
    most = (MAX_LABELS_PER_LINE - 1) // 2
    capped = side > most
    if capped:
        side = most
        interval = length / (2 * side + 1)
    return [middle + step * interval for step in range(-side, side + 1)], capped


def estimated_label_width(text: str, font_px: float) -> float:
    """A label's width in pixels, from its longest line: an estimate, since the
    browser's font is not known at export."""
    return max(len(line) for line in text.split("\n")) * GLYPH_WIDTH * font_px


def label_anchors(
    geometry: dict[str, Any] | None, per_part: bool = False, plane: str = "mercator"
) -> list[tuple[float, float]]:
    """Every point QGIS draws a label of this geometry at, in part order, when
    its labels do not repeat (`build_label_collection` repeats them).

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
            representative_point({"type": single, "coordinates": part}, plane)
            for part in coordinates
        )
        return [point for point in parts if point is not None]

    if per_part and kind == "GeometryCollection":
        return [
            point
            for member in geometry.get("geometries", ())
            for point in label_anchors(member, per_part, plane)
        ]

    point = representative_point(geometry, plane)
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


@dataclass(frozen=True)
class RepeatedLabels:
    """What repeating a layer's line labels came to.

    `neighbours` pairs the indexes of label points that follow each other along
    one line, which `declutter` keeps from overlapping, and `extras` is the
    indexes of every repeat but the one nearest each line's middle, which
    `declutter` leaves out of the share of labels that must have room: they
    are held to that pairwise instead. `lines_capped` counts
    the lines `MAX_LABELS_PER_LINE` spread wider, and `spread` is how many
    times the repeat distance was widened to keep the layer within
    `MAX_LABELS_PER_LAYER` (1 when it was not).
    """

    neighbours: tuple[tuple[int, int], ...] = ()
    extras: frozenset[int] = frozenset()
    lines_capped: int = 0
    spread: float = 1.0


def build_label_collection(
    geojson: dict[str, Any] | None,
    field_name: str,
    capitalization: str = "none",
    wrap_char: str = "",
    auto_wrap_length: int = 0,
    per_part: bool = False,
    plane: str = "mercator",
    repeat: LineLabelRepeat | None = None,
    font_px: float = 12.0,
) -> dict[str, Any] | None:
    """A point FeatureCollection carrying a labelled feature's label at each
    point QGIS draws it at (`label_anchors`): one per feature, or one per part
    of a multi-part feature with `per_part`, and always one per point of a
    MultiPoint - and along a line, one at each `repeat`.

    Features whose label value is empty are skipped outright -- deck.gl renders
    an empty string as an invisible glyph that still consumes a picking slot,
    and a map full of those makes hover feel broken.

    Returns `None` when nothing would be labelled, so the caller can omit the
    `TextLayer` entirely rather than emit an empty one.
    """
    return build_label_collection_noting_repeats(
        geojson,
        field_name,
        capitalization,
        wrap_char,
        auto_wrap_length,
        per_part,
        plane,
        repeat,
        font_px,
    )[0]


def build_label_collection_noting_repeats(
    geojson: dict[str, Any] | None,
    field_name: str,
    capitalization: str = "none",
    wrap_char: str = "",
    auto_wrap_length: int = 0,
    per_part: bool = False,
    plane: str = "mercator",
    repeat: LineLabelRepeat | None = None,
    font_px: float = 12.0,
) -> tuple[dict[str, Any] | None, RepeatedLabels]:
    """`build_label_collection`, and what repeating its line labels came to.

    `font_px` sizes the labels' estimated width, which QGIS keeps repeats
    from crowding closer than.
    """
    if not geojson:
        return None, RepeatedLabels()

    # Each label's text with its fixed points, or the lines it repeats along
    # and its width there; placed once the whole layer's count is known.
    labels: list[tuple[str, list[tuple[float, float]], list[_Line], float]] = []
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

        geometry = feature.get("geometry")
        lines = None if repeat is None else _labelled_lines(geometry, per_part, plane)
        if repeat is None or lines is None:
            labels.append((text, label_anchors(geometry, per_part, plane), [], 0.0))
        else:
            width = estimated_label_width(text, font_px) * repeat.units_per_pixel
            labels.append((text, [], lines, width))

    # Widened by doubling until the layer fits its cap, or no line repeats.
    spread = 1.0
    if repeat is not None:
        fixed = sum(len(points) for _, points, _, _ in labels)
        while True:
            counts = [
                len(_repeat_distances(line.length, repeat, width, spread)[0])
                for _, _, lines, width in labels
                for line in lines
            ]
            if (
                fixed + sum(counts) <= MAX_LABELS_PER_LAYER
                or max(counts, default=1) <= 1
            ):
                break
            spread *= 2

    features: list[dict[str, Any]] = []
    neighbours: list[tuple[int, int]] = []
    extras: set[int] = set()
    capped_lines = 0
    for text, points, lines, width in labels:
        features.extend(_label_point(point, text) for point in points)
        if repeat is None:
            continue
        for line in lines:
            distances, capped = _repeat_distances(line.length, repeat, width, spread)
            capped_lines += capped
            first = len(features)
            features.extend(_label_point(line.at(d), text) for d in distances)
            neighbours.extend(
                (index, index + 1) for index in range(first, len(features) - 1)
            )
            extras.update(range(first, len(features)))
            extras.discard(first + len(distances) // 2)

    if not features:
        return None, RepeatedLabels()

    return (
        {"type": "FeatureCollection", "features": features},
        RepeatedLabels(tuple(neighbours), frozenset(extras), capped_lines, spread),
    )


def _label_point(point: tuple[float, float], text: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            # Rounded to ~1cm. Label anchors do not need more, and full float
            # repr would bloat the payload for no visible gain.
            "coordinates": [round(point[0], 7), round(point[1], 7)],
        },
        "properties": {LABEL_PROPERTY: text},
    }


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
