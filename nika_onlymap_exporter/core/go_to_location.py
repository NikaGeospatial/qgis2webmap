"""Go to location: the logic behind the Map frames > "Go to location..." dialog.

Everything here is separate from the dialog so it can be tested without it:

- **Coordinates.** `parse_decimal` reads one typed coordinate in the user's
  locale and refuses anything ambiguous; `fields_to_point` / `point_to_fields`
  are the only place that decides which typed value is X and which is Y. The
  dialog is latitude-first, QGIS geometry is always X = east/longitude, so a
  geographic CRS swaps the pair and a projected one keeps it.
- **Search.** `searchable_layers` and `search_sources` say what can be searched;
  `SearchPlan` turns a source and a query into safely quoted QGIS expressions
  (the query is only ever passed through `QgsExpression.quotedValue`), and
  `start_search` runs it on a QgsTask so the window stays responsive. Matching
  is done in three disjoint tiers (exact, prefix, anywhere) with a limit on
  each, so a common query reads at most `MAX_SHOWN + 1` rows from the provider
  and never the whole layer.
- **Destination.** `centered_extent` and `fitted_extent` turn a place into the
  extent a map frame should show, keeping the frame's own shape and, when
  asked, its scale and rotation.
- **Applying.** `FrameTarget` is one map frame: it validates the frame, the
  atlas and the layers again at the moment of use and applies the new extent as
  a single undo step on the layout's own stack.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeedback,
    QgsGeometry,
    QgsLayoutItemMap,
    QgsLayoutObject,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRenderContext,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QLocale, QMetaType

from .atlas_series import friendly_map_name
from .frame_layers import rendered_layers

#: Matches shown in the list; one more is read to know whether there are more.
MAX_SHOWN = 50
#: A fitted feature keeps this share of its own size free on every side.
FIT_MARGIN = 0.1
#: The largest whole number a search accepts (a 64-bit signed ID).
MAX_WHOLE_NUMBER = 2**63 - 1
#: Below this share of the current view a fitted feature is "a point": fitting
#: it would zoom in a million times, so the scale is kept instead.
_DEGENERATE_SHARE = 1e-6

LATITUDE_RANGE = (-90.0, 90.0)
LONGITUDE_RANGE = (-180.0, 180.0)

# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

#: Problems `parse_decimal` reports.
EMPTY, INVALID, UNSUPPORTED, PAIR = "empty", "invalid", "unsupported", "pair"

_MINUS_SIGNS = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2012": "-"})
_UNSUPPORTED = re.compile("[\u00b0\u00ba\u2032\u2033'\"]|[^\\W\\d_]", re.UNICODE)


@dataclass(frozen=True)
class ParsedNumber:
    """The outcome of reading one typed coordinate."""

    value: float | None = None
    problem: str = ""  # one of EMPTY, INVALID, UNSUPPORTED, PAIR when no value

    @property
    def ok(self) -> bool:
        return self.value is not None


def _separators(locale: QLocale) -> tuple[str, str]:
    return locale.decimalPoint(), locale.groupSeparator()


def parse_decimal(text: str, locale: QLocale | None = None) -> ParsedNumber:
    """Read one signed decimal number written in `locale`.

    The locale's decimal separator is accepted, and a full stop as well when the
    locale does not use it for grouping. A group separator, an exponent, letters
    and degree signs are refused rather than guessed at: "1,234" is either one
    thousand or 1.234 and the user is asked to say which. Nothing is clamped or
    swapped here.
    """
    locale = locale or QLocale()
    decimal, group = _separators(locale)
    text = text.translate(_MINUS_SIGNS).strip()
    if not text:
        return ParsedNumber(problem=EMPTY)
    accepted = {decimal}
    if decimal != "." and group != ".":
        accepted.add(".")
    separators = "".join(re.escape(s) for s in sorted(accepted))
    shape = re.compile(
        rf"[+-]?(?:\d+(?:[{separators}]\d*)?|[{separators}]\d+)", re.UNICODE
    )
    if shape.fullmatch(text):
        number = text.lstrip("+-")
        normalised = re.sub(rf"[{separators}]", ".", number)
        try:
            value = float(normalised)
        except ValueError:
            return ParsedNumber(problem=INVALID)
        if text.startswith("-"):
            value = -value
        if not math.isfinite(value):
            return ParsedNumber(problem=INVALID)
        return ParsedNumber(value=value)
    pieces = [piece for piece in re.split(r"[;\s,]+", text) if piece]
    if len(pieces) == 2 and all(
        shape.fullmatch(p.translate(_MINUS_SIGNS)) for p in pieces
    ):
        return ParsedNumber(problem=PAIR)
    if _UNSUPPORTED.search(text) and re.search(r"\d", text):
        return ParsedNumber(problem=UNSUPPORTED)
    return ParsedNumber(problem=INVALID)


def parse_whole_number(text: str) -> int | None:
    """A whole-number ID, or None: no decimals, no grouping, nothing truncated."""
    text = text.translate(_MINUS_SIGNS).strip()
    if not re.fullmatch(r"[+-]?\d+", text, re.UNICODE):
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if abs(number) <= MAX_WHOLE_NUMBER else None


def format_number(value: float, geographic: bool, locale: QLocale | None = None) -> str:
    """`value` as text for a coordinate field, in `locale`, without rounding it
    away from the place it names (eight decimals of a degree, three of a unit)."""
    locale = locale or QLocale()
    text = f"{value:.8f}" if geographic else f"{value:.3f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", locale.decimalPoint())


# ---------------------------------------------------------------------------
# Map scale
# ---------------------------------------------------------------------------

#: The zoom presets, as how many times closer than the scale the dialog opened
#: at: 1/2x shows more, 8x is eight times closer.
PRESET_FACTORS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)
#: The scale denominators a typed scale may have (1 : 1 to 1 : 1,000,000,000).
SCALE_RANGE = (1.0, 1e9)
#: A typed scale outside `SCALE_RANGE` (or zero, or negative).
OUT_OF_RANGE = "range"

_SPACES = "\u00a0\u202f\u2009 "
_SCALE_PREFIX = re.compile(r"^\s*1\s*:\s*")


def round_scale(value: float) -> float:
    """A scale denominator as a whole number, halves rounding up: the way every
    scale is shown and, except for 1x, applied."""
    return float(math.floor(value + 0.5))


def preset_scale(baseline: float, factor: float) -> float:
    """The scale denominator `factor` times closer than `baseline`: absolute,
    so asking for the same preset twice never zooms twice. 1x is the baseline
    itself, exactly; the others are the whole number they are shown as."""
    if factor == 1.0:
        return baseline
    return round_scale(baseline / factor)


def preset_for_scale(baseline: float, scale: float) -> int | None:
    """Index of the preset whose shown (whole-number) scale `scale` is, or None
    for a custom scale."""
    shown = round_scale(scale)
    for index, factor in enumerate(PRESET_FACTORS):
        if round_scale(preset_scale(baseline, factor)) == shown:
            return index
    return None


def format_scale(value: float, locale: QLocale | None = None) -> str:
    """A scale denominator as the field shows it: a whole number, grouped."""
    locale = locale or QLocale()
    return locale.toString(round(round_scale(value)))


def parse_scale(text: str, locale: QLocale | None = None) -> ParsedNumber:
    """Read a typed or pasted scale denominator in `locale`: "25,000", "25000"
    or "1:25,000". Empty, non-numeric, zero, negative and out-of-range values
    come back with a problem and no value; nothing is substituted."""
    locale = locale or QLocale()
    decimal, group = _separators(locale)
    cleaned = _SCALE_PREFIX.sub("", text.translate(_MINUS_SIGNS)).strip()
    if not cleaned:
        return ParsedNumber(problem=EMPTY)
    group_chars = ({group} | set(_SPACES)) if group else set(_SPACES)
    if any(c in group_chars for c in cleaned):
        mark = re.escape("".join(sorted(group_chars)))
        grouped = rf"[+-]?\d{{1,3}}(?:[{mark}]\d{{3}})+(?:{re.escape(decimal)}\d*)?"
        if not re.fullmatch(grouped, cleaned, re.UNICODE):
            return ParsedNumber(problem=INVALID)
        cleaned = re.sub(rf"[{mark}]", "", cleaned)
    parsed = parse_decimal(cleaned, locale)
    if parsed.value is None:
        return parsed
    low, high = SCALE_RANGE
    if not low <= parsed.value <= high:
        return ParsedNumber(problem=OUT_OF_RANGE)
    return parsed


def is_geographic(crs: QgsCoordinateReferenceSystem) -> bool:
    return bool(crs.isValid() and crs.isGeographic())


def field_range_problem(
    index: int, value: float, crs: QgsCoordinateReferenceSystem
) -> bool:
    """True when the typed value is out of range for a geographic CRS: field 0 is
    latitude, field 1 longitude. A projected CRS has no range here; the
    transform decides. Nothing is clamped, swapped or wrapped."""
    if not is_geographic(crs):
        return False
    low, high = LATITUDE_RANGE if index == 0 else LONGITUDE_RANGE
    return not low <= value <= high


def fields_to_point(
    first: float, second: float, crs: QgsCoordinateReferenceSystem
) -> QgsPointXY:
    """The typed pair as a QGIS point in `crs`.

    The dialog asks for latitude then longitude (or easting then northing). QGIS
    points are X then Y, X being east; for a geographic CRS that is longitude,
    so the pair is swapped there and only there.
    """
    if is_geographic(crs):
        return QgsPointXY(second, first)
    return QgsPointXY(first, second)


def point_to_fields(
    point: QgsPointXY, crs: QgsCoordinateReferenceSystem
) -> tuple[float, float]:
    """The inverse of `fields_to_point`: the values for the first and second field."""
    if is_geographic(crs):
        return point.y(), point.x()
    return point.x(), point.y()


class TransformError(Exception):
    """A point or geometry could not be moved between two coordinate systems."""


def transform_point(
    point: QgsPointXY,
    source: QgsCoordinateReferenceSystem,
    target: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> QgsPointXY:
    """`point` in `target`, through the project's own transform context."""
    if not source.isValid() or not target.isValid():
        raise TransformError("invalid coordinate system")
    if source == target:
        result = QgsPointXY(point)
    else:
        transform = QgsCoordinateTransform(source, target, project.transformContext())
        try:
            result = transform.transform(point)
        except QgsCsException as error:
            raise TransformError(str(error)) from error
    if not (math.isfinite(result.x()) and math.isfinite(result.y())):
        raise TransformError("the result is not a finite coordinate")
    return result


def transform_geometry(
    geometry: QgsGeometry,
    source: QgsCoordinateReferenceSystem,
    target: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> QgsGeometry:
    """A copy of `geometry` in `target`."""
    result = QgsGeometry(geometry)
    if not source.isValid() or not target.isValid():
        raise TransformError("invalid coordinate system")
    if source == target:
        return result
    transform = QgsCoordinateTransform(source, target, project.transformContext())
    try:
        status = result.transform(transform)
    except QgsCsException as error:
        raise TransformError(str(error)) from error
    if status != Qgis.GeometryOperationResult.Success:
        raise TransformError("the geometry could not be transformed")
    return result


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


def has_location(geometry: QgsGeometry | None) -> bool:
    """Whether a feature's geometry can be navigated to."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return False
    box = geometry.boundingBox()
    return all(
        math.isfinite(v)
        for v in (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
    )


def is_single_point(geometry: QgsGeometry) -> bool:
    """A point feature (not a line, polygon or a multipoint of several places)."""
    if geometry.type() != Qgis.GeometryType.Point:
        return False
    return geometry.constGet().partCount() == 1


def _parts(geometry: QgsGeometry) -> list[QgsGeometry]:
    if not geometry.isMultipart():
        return [geometry]
    return [QgsGeometry(part.clone()) for part in geometry.constGet().parts()]


def representative_point(geometry: QgsGeometry) -> QgsPointXY | None:
    """A place that is on the feature: the point itself, a point on the surface
    of a polygon (never a centroid that falls outside it), the middle of a line
    along its length. A multipart feature uses its largest part."""
    if not has_location(geometry):
        return None
    kind = geometry.type()
    parts = _parts(geometry)
    if kind == Qgis.GeometryType.Point:
        if len(parts) == 1 and not geometry.isMultipart():
            point = geometry.asPoint()
            return QgsPointXY(point)
        surface = geometry.pointOnSurface()
        return surface.asPoint() if not surface.isNull() else None
    if kind == Qgis.GeometryType.Line:
        part = max(parts, key=lambda p: p.length())
        middle = part.interpolate(part.length() / 2.0)
        if middle.isNull():
            return None
        return middle.asPoint()
    part = max(parts, key=lambda p: p.area())
    surface = part.pointOnSurface()
    if surface.isNull():
        return None
    return surface.asPoint()


def _rotate_clockwise(
    point: QgsPointXY, centre: QgsPointXY, degrees: float
) -> QgsPointXY:
    radians = math.radians(degrees)
    dx, dy = point.x() - centre.x(), point.y() - centre.y()
    return QgsPointXY(
        centre.x() + dx * math.cos(radians) + dy * math.sin(radians),
        centre.y() - dx * math.sin(radians) + dy * math.cos(radians),
    )


def centered_extent(current: QgsRectangle, centre: QgsPointXY) -> QgsRectangle:
    """`current`, moved so that `centre` is its middle: same size, so the frame
    keeps its scale (and, being the unrotated extent, its rotation)."""
    return QgsRectangle.fromCenterAndSize(centre, current.width(), current.height())


def fitted_extent(
    geometry: QgsGeometry, current: QgsRectangle, rotation: float
) -> QgsRectangle | None:
    """The extent that shows all of `geometry` (already in the frame's CRS) in a
    frame shaped like `current`, with `FIT_MARGIN` free on every side.

    A rotated map shows the extent turned counter-clockwise by `rotation`, so
    the feature is turned clockwise by it to measure the box it needs, and that
    box's middle is turned back. None when the feature is too small for a fit
    to mean anything (the caller then keeps the scale).
    """
    if current.width() <= 0 or current.height() <= 0 or not has_location(geometry):
        return None
    box = geometry.boundingBox()
    pivot = box.center()
    turned = QgsGeometry(geometry)
    if rotation:
        turned.rotate(rotation, pivot)
    needed = turned.boundingBox()
    width = needed.width() * (1 + 2 * FIT_MARGIN)
    height = needed.height() * (1 + 2 * FIT_MARGIN)
    ratio = current.width() / current.height()
    fit_width = max(width, height * ratio)
    fit_height = fit_width / ratio
    if fit_width <= 0 or fit_width < current.width() * _DEGENERATE_SHARE:
        return None
    centre = needed.center()
    if rotation:
        centre = _rotate_clockwise(centre, pivot, -rotation)
    return QgsRectangle.fromCenterAndSize(centre, fit_width, fit_height)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

DISPLAY, FEATURE_ID, ATTRIBUTE = "display", "fid", "field"


@dataclass(frozen=True)
class SearchSource:
    """What the "Search in" dropdown can be set to."""

    kind: str  # DISPLAY, FEATURE_ID or ATTRIBUTE
    label: str
    field_name: str = ""
    whole_number: bool = False  # integer field or QGIS ID: exact, no substring


_TEXT_TYPES = {QMetaType.Type.QString}
_INTEGER_TYPES = {
    QMetaType.Type.Int,
    QMetaType.Type.UInt,
    QMetaType.Type.LongLong,
    QMetaType.Type.ULongLong,
}


def searchable_layers(item: QgsLayoutItemMap) -> list[QgsVectorLayer]:
    """The vector layers with a location that this frame draws, top first."""
    wanted = (
        Qgis.GeometryType.Point,
        Qgis.GeometryType.Line,
        Qgis.GeometryType.Polygon,
    )
    found: list[QgsVectorLayer] = []
    for layer in rendered_layers(item):
        if (
            isinstance(layer, QgsVectorLayer)
            and not sip.isdeleted(layer)
            and layer.isValid()
            and layer.isSpatial()
            and layer.geometryType() in wanted
        ):
            found.append(layer)
    return found


def search_sources(layer: QgsVectorLayer) -> list[SearchSource]:
    """Display name, QGIS feature ID, then the text and integer fields in order."""
    from ..ui.i18n import tr

    sources = [
        SearchSource(DISPLAY, tr("Display name")),
        SearchSource(FEATURE_ID, tr("QGIS feature ID"), whole_number=True),
    ]
    fields = [
        f
        for f in layer.fields()
        if f.type() in _TEXT_TYPES or f.type() in _INTEGER_TYPES
    ]
    names = [f.displayName() for f in fields]
    for f, shown in zip(fields, names, strict=True):
        label = f"{shown} ({f.name()})" if names.count(shown) > 1 else shown
        sources.append(
            SearchSource(
                ATTRIBUTE,
                label,
                f.name(),
                whole_number=f.type() in _INTEGER_TYPES,
            )
        )
    return sources


def display_expression(layer: QgsVectorLayer) -> str:
    """The layer's own display expression (what its attribute table calls a
    feature), falling back to its display field and then the feature ID."""
    text = layer.displayExpression()
    if text:
        return text
    if layer.displayField():
        return QgsExpression.quotedColumnRef(layer.displayField())
    return "$id"


def display_text(layer: QgsVectorLayer, feature: QgsFeature) -> str:
    """The feature's display name, "" when the expression has none."""
    context = QgsExpressionContext(
        QgsExpressionContextUtils.globalProjectLayerScopes(layer)
    )
    context.setFields(layer.fields())
    context.setFeature(feature)
    expression = QgsExpression(display_expression(layer))
    value = expression.evaluate(context)
    if expression.hasEvalError() or value is None:
        return ""
    text = str(value)
    return "" if text == "NULL" else text.strip()


def text_value_expression(layer: QgsVectorLayer, source: SearchSource) -> str:
    """The expression whose text a text search compares."""
    if source.kind == ATTRIBUTE:
        return QgsExpression.quotedColumnRef(source.field_name)
    return f"({display_expression(layer)})"


def text_tier_filters(value_expression: str, query: str) -> tuple[str, str, str, str]:
    """Filters for text matching: anything containing the query, then the three
    disjoint tiers exact, begins-with and contains-inside. Case-insensitive, and
    the query is a quoted literal: `%`, `_`, quotes and backslashes mean nothing."""
    needle = f"lower({QgsExpression.quotedValue(query)})"
    haystack = f"lower({value_expression})"
    return (
        f"strpos({haystack}, {needle}) > 0",
        f"{haystack} = {needle}",
        f"strpos({haystack}, {needle}) = 1 AND {haystack} != {needle}",
        f"strpos({haystack}, {needle}) > 1",
    )


@dataclass
class Match:
    """One feature found: its identity is the layer's ID and the feature ID."""

    layer_id: str
    fid: int
    feature: QgsFeature
    tier: int | None = None  # 0 exact, 1 prefix, 2 inside; None = not ranked yet


@dataclass
class SearchOutcome:
    matches: list[Match] = field(default_factory=list)
    more: bool = False  # there are matches beyond MAX_SHOWN


class SearchJob:
    """One search on its way: started by `start_search`, polled by the dialog.

    A plain Python object, not a QObject, with no signal into the dialog: the
    dialog looks at `done` on a timer, so a job that outlives its dialog calls
    nothing that was deleted.
    """

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.feedback = QgsFeedback()
        self.done = False
        self.error = ""
        self.outcome: SearchOutcome | None = None
        self.fetched = 0
        self._task: QgsTask | None = None

    def cancel(self) -> None:
        self.feedback.cancel()


class SearchPlan:
    """A source and a query turned into provider requests. Pure: nothing runs."""

    def __init__(self, layer: QgsVectorLayer, source: SearchSource, query: str) -> None:
        self.layer_id = layer.id()
        self.source = source
        self.query = query.strip()
        self.whole_number: int | None = None
        self.value_expression = ""
        if source.whole_number:
            self.whole_number = parse_whole_number(self.query)
        elif source.kind in (DISPLAY, ATTRIBUTE):
            self.value_expression = text_value_expression(layer, source)

    @property
    def valid(self) -> bool:
        return bool(self.query) and (
            not self.source.whole_number or self.whole_number is not None
        )

    def requests(self) -> list[tuple[int | None, QgsFeatureRequest]]:
        """Each request with the tier it finds (None: not tiered)."""
        if self.source.kind == FEATURE_ID:
            if self.whole_number is None:
                return []
            # An expression, not setFilterFid: a fetch by ID would ignore the
            # layer's own subset filter, which must hold here too.
            return [
                (
                    None,
                    QgsFeatureRequest().setFilterExpression(
                        f"$id = {self.whole_number}"
                    ),
                )
            ]
        if self.source.whole_number:
            column = QgsExpression.quotedColumnRef(self.source.field_name)
            request = QgsFeatureRequest().setFilterExpression(
                f"{column} = {self.whole_number}"
            )
            return [(None, request)]
        _any, exact, prefix, inside = text_tier_filters(
            self.value_expression, self.query
        )
        return [
            (0, QgsFeatureRequest().setFilterExpression(exact)),
            (1, QgsFeatureRequest().setFilterExpression(prefix)),
            (2, QgsFeatureRequest().setFilterExpression(inside)),
        ]

    def anywhere_request(self) -> QgsFeatureRequest:
        return QgsFeatureRequest().setFilterExpression(
            text_tier_filters(self.value_expression, self.query)[0]
        )

    def execute(
        self, source: QgsVectorLayerFeatureSource, job: SearchJob
    ) -> SearchOutcome:
        """Run on a worker thread. Reads at most 2 * (MAX_SHOWN + 1) rows."""
        limit = MAX_SHOWN + 1

        def read(request: QgsFeatureRequest, count: int) -> list[QgsFeature]:
            request.setFeedback(job.feedback)
            if count:
                request.setLimit(count)
            found: list[QgsFeature] = []
            for feature in source.getFeatures(request):
                if job.feedback.isCanceled():
                    break
                found.append(QgsFeature(feature))
                job.fetched += 1
                if count and len(found) >= count:
                    break
            return found

        outcome = SearchOutcome()
        if self.source.kind == FEATURE_ID or self.source.whole_number:
            ((tier, request),) = self.requests()
            for feature in read(request, limit):
                outcome.matches.append(
                    Match(self.layer_id, feature.id(), feature, tier)
                )
            return self._trim(outcome)
        # One pass for "contains": when it is complete (at most MAX_SHOWN found)
        # it holds every match and they are ranked afterwards; only when there
        # are more are the three tiers read separately, each topping up to the
        # limit, so the first matches shown are the best ones.
        everything = read(self.anywhere_request(), limit)
        if len(everything) < limit:
            outcome.matches = [Match(self.layer_id, f.id(), f) for f in everything]
            return outcome
        for tier, request in self.requests():
            room = limit - len(outcome.matches)
            if room <= 0 or job.feedback.isCanceled():
                break
            for feature in read(request, room):
                outcome.matches.append(
                    Match(self.layer_id, feature.id(), feature, tier)
                )
        return self._trim(outcome)

    @staticmethod
    def _trim(outcome: SearchOutcome) -> SearchOutcome:
        if len(outcome.matches) > MAX_SHOWN:
            outcome.more = True
            outcome.matches = outcome.matches[:MAX_SHOWN]
        return outcome


def start_search(layer: QgsVectorLayer, plan: SearchPlan, generation: int) -> SearchJob:
    """Begin `plan` on a background task and return the job to poll."""
    job = SearchJob(generation)
    source = QgsVectorLayerFeatureSource(layer)

    def run(_task: QgsTask) -> bool:
        try:
            job.outcome = plan.execute(source, job)
        except Exception as error:  # reported to the dialog, never raised in the thread
            job.error = str(error) or error.__class__.__name__
        finally:
            job.done = True
        return True

    task = QgsTask.fromFunction("Go to location: search", run)
    job._task = task
    QgsApplication.taskManager().addTask(task)
    return job


def rank_matches(
    outcome: SearchOutcome,
    texts: dict[int, str],
    query: str,
) -> list[Match]:
    """Order matches: exact, then prefix, then inside; alphabetical within each
    group, feature ID last, so the order never changes between two searches.

    `texts` maps a feature ID to the text that was matched. Matches the search
    already tiered keep their tier; a complete, untiered result is tiered here.
    """
    needle = query.strip().casefold()

    def tier(match: Match) -> int:
        if match.tier is not None:
            return match.tier
        shown = texts.get(match.fid, "").casefold()
        if shown == needle:
            return 0
        return 1 if shown.startswith(needle) else 2

    return sorted(
        outcome.matches,
        key=lambda m: (tier(m), texts.get(m.fid, "").casefold(), m.fid),
    )


def would_be_drawn(layer: QgsVectorLayer, feature: QgsFeature) -> bool:
    """Whether the layer's renderer draws this feature at all (a categorized or
    rule-based style can leave a category out). True when that cannot be told."""
    renderer = layer.renderer()
    if renderer is None:
        return True
    try:
        clone = renderer.clone()
        scope = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer)
        )
        scope.setFeature(feature)
        context = QgsRenderContext()
        context.setExpressionContext(scope)
        clone.startRender(context, layer.fields())
        try:
            return bool(clone.willRenderFeature(feature, context))
        finally:
            clone.stopRender(context)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# The target frame
# ---------------------------------------------------------------------------

_DATA_DEFINED_VIEW = (
    QgsLayoutObject.DataDefinedProperty.MapXMin,
    QgsLayoutObject.DataDefinedProperty.MapYMin,
    QgsLayoutObject.DataDefinedProperty.MapXMax,
    QgsLayoutObject.DataDefinedProperty.MapYMax,
    QgsLayoutObject.DataDefinedProperty.MapScale,
)


class FrameTarget:
    """One map frame, looked at again every time it is used.

    The layout or the frame can go while the dialog is open, so nothing is
    cached: `item()` finds the frame afresh and says None once it is gone.
    """

    def __init__(
        self,
        layout: object,
        item: QgsLayoutItemMap,
        project: QgsProject,
        before_apply: Callable[[], None] | None = None,
        after_apply: Callable[[], None] | None = None,
    ) -> None:
        self._layout = layout
        self._item = item
        self._uuid = item.uuid()
        self.name = friendly_map_name(item.displayName())
        self.project = project
        self._before_apply = before_apply
        self._after_apply = after_apply

    @property
    def key(self) -> str:
        """Stable for one frame, for remembering things about it in a session."""
        return self._uuid

    def item(self) -> QgsLayoutItemMap | None:
        layout, item = self._layout, self._item
        if (
            layout is None
            or sip.isdeleted(layout)
            or item is None
            or sip.isdeleted(item)
        ):
            return None
        try:
            present = any(i is item for i in layout.items())  # type: ignore[attr-defined]
        except RuntimeError:
            return None
        return item if present else None

    def available(self) -> bool:
        return self.item() is not None

    def follows_atlas(self) -> bool:
        """Only a frame an enabled atlas actually drives: others are free."""
        item = self.item()
        if item is None:
            return False
        try:
            return bool(self._layout.atlas().enabled() and item.atlasDriven())  # type: ignore[attr-defined]
        except RuntimeError:
            return False

    def layers(self) -> list[QgsVectorLayer]:
        item = self.item()
        return searchable_layers(item) if item is not None else []

    def crs(self) -> QgsCoordinateReferenceSystem:
        item = self.item()
        if item is None:
            return QgsCoordinateReferenceSystem()
        return item.crs()

    def extent(self) -> QgsRectangle | None:
        item = self.item()
        return QgsRectangle(item.extent()) if item is not None else None

    def rotation(self) -> float:
        item = self.item()
        return float(item.mapRotation()) if item is not None else 0.0

    def scale(self) -> float | None:
        """The scale denominator the frame shows now, or None when it has none."""
        item = self.item()
        if item is None:
            return None
        value = float(item.scale())
        return value if math.isfinite(value) and value > 0 else None

    def view_is_data_defined(self) -> bool:
        item = self.item()
        if item is None:
            return False
        properties = item.dataDefinedProperties()
        return any(properties.isActive(p) for p in _DATA_DEFINED_VIEW)

    def apply_extent(
        self, extent: QgsRectangle, label: str, scale: float | None = None
    ) -> None:
        """Show `extent` in the frame as one undo step, at `scale` (a scale
        denominator, about the same middle) when one is given. Raises
        `ApplyError`."""
        item = self.item()
        if item is None:
            raise ApplyError("frame")
        if self.follows_atlas():
            raise ApplyError("atlas")
        if self.view_is_data_defined():
            raise ApplyError("data-defined")
        if not (
            math.isfinite(extent.width())
            and math.isfinite(extent.height())
            and extent.width() > 0
            and extent.height() > 0
        ):
            raise ApplyError("extent")
        if self._before_apply is not None:
            self._before_apply()
        stack = self._layout.undoStack()  # type: ignore[attr-defined]
        stack.beginCommand(item, label)
        try:
            item.zoomToExtent(extent)
            if scale is not None:
                self._apply_scale(item, scale)
            item.invalidateCache()
        finally:
            stack.endCommand()
        item.update()
        if self._after_apply is not None:
            self._after_apply()

    @staticmethod
    def _apply_scale(item: QgsLayoutItemMap, scale: float) -> None:
        """Zoom the view about its middle to `scale`; the place, the frame's
        size on the page and its rotation stay as they are."""
        if not (math.isfinite(scale) and scale > 0):
            raise ApplyError("extent")
        now = float(item.scale())
        if math.isfinite(now) and abs(now - scale) > scale * 1e-9:
            item.setScale(scale)


class ApplyError(Exception):
    """Why a destination could not be applied: "frame", "atlas", "data-defined"
    or "extent"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def feature_destination(
    target: FrameTarget,
    layer: QgsVectorLayer,
    fid: int,
    fit: bool,
) -> QgsRectangle:
    """The extent for feature `fid` of `layer`, read fresh from the layer.

    Raises LookupError when the layer or feature is gone or has no location,
    TransformError when its geometry cannot be moved into the frame's CRS, and
    ApplyError("extent") when no destination can be made.
    """
    if sip.isdeleted(layer) or not layer.isValid():
        raise LookupError("layer")
    feature = layer.getFeature(fid)
    if not feature.isValid():
        raise LookupError("feature")
    if not has_location(feature.geometry()):
        raise LookupError("location")
    current = target.extent()
    if current is None:
        raise ApplyError("frame")
    geometry = transform_geometry(
        feature.geometry(), layer.crs(), target.crs(), target.project
    )
    if not has_location(geometry):
        raise TransformError("empty after transform")
    if fit and not is_single_point(geometry):
        fitted = fitted_extent(geometry, current, target.rotation())
        if fitted is not None:
            return fitted
    centre = representative_point(geometry)
    if centre is None:
        raise ApplyError("extent")
    return centered_extent(current, centre)


def fit_available(target: FrameTarget, layer: QgsVectorLayer, fid: int) -> bool:
    """Whether "Fit whole feature" means something for feature `fid`: it has an
    extent worth fitting (not a single point, not a speck). False when it
    cannot be read."""
    try:
        if sip.isdeleted(layer) or not layer.isValid():
            return False
        feature = layer.getFeature(fid)
        current = target.extent()
        if not feature.isValid() or current is None:
            return False
        geometry = transform_geometry(
            feature.geometry(), layer.crs(), target.crs(), target.project
        )
        if not has_location(geometry) or is_single_point(geometry):
            return False
        return fitted_extent(geometry, current, target.rotation()) is not None
    except TransformError:
        return False


def coordinate_destination(
    target: FrameTarget, point: QgsPointXY, source: QgsCoordinateReferenceSystem
) -> QgsRectangle:
    """The extent centred on `point` (given in `source`), keeping the frame's scale."""
    current = target.extent()
    if current is None:
        raise ApplyError("frame")
    centre = transform_point(point, source, target.crs(), target.project)
    return centered_extent(current, centre)


__all__: Sequence[str] = [
    "ATTRIBUTE",
    "DISPLAY",
    "EMPTY",
    "FEATURE_ID",
    "FIT_MARGIN",
    "INVALID",
    "MAX_SHOWN",
    "OUT_OF_RANGE",
    "PAIR",
    "PRESET_FACTORS",
    "SCALE_RANGE",
    "UNSUPPORTED",
    "ApplyError",
    "FrameTarget",
    "Match",
    "ParsedNumber",
    "SearchJob",
    "SearchOutcome",
    "SearchPlan",
    "SearchSource",
    "TransformError",
    "centered_extent",
    "coordinate_destination",
    "display_expression",
    "display_text",
    "feature_destination",
    "field_range_problem",
    "fields_to_point",
    "fit_available",
    "fitted_extent",
    "format_number",
    "format_scale",
    "has_location",
    "is_geographic",
    "is_single_point",
    "parse_decimal",
    "parse_scale",
    "parse_whole_number",
    "point_to_fields",
    "preset_for_scale",
    "preset_scale",
    "rank_matches",
    "representative_point",
    "search_sources",
    "searchable_layers",
    "start_search",
    "text_tier_filters",
    "transform_geometry",
    "transform_point",
    "would_be_drawn",
]
