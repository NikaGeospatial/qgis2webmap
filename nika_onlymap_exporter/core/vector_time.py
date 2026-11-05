"""A vector layer that follows QGIS's time, as a time slider on the web map.

QGIS draws such a layer (Layer Properties > Temporal) by filtering its
features to the frame the temporal controller sets:
`QgsVectorLayerTemporalProperties::createFilterString` keeps a feature whose
start is before the frame's end and whose end is after the frame's start (on
or after, for a layer whose features include their end), and "Accumulate
features" keeps every feature that started before the frame's end. A NULL
time is open on that side for the field modes, and never drawn for the
expression mode.

The runtime's slider (`<om-widget type="time-slider" field window>`) filters
one numeric property to the `window` milliseconds before its own time `t`,
both ends included, or, with no window, to everything up to `t`. A feature's
time span maps onto that exactly when every feature lasts equally long (a
point in time is one that lasts nothing): carrying its start shifted back by
the frame's length (`plan_slider`), a slider at `t` shows what QGIS draws for
the frame of the controller's length that begins at `t`. Features lasting differently
long have no such shift, and the layer is then a snapshot of the frame QGIS
draws instead (`drawn`).

The runtime filters on the graphics card, in 32-bit floats, which hold a
2020s time in milliseconds only to about two minutes. So the slider's edges
are placed a few such steps off each frame's (`float32_margin`): a feature at
exactly a frame's start or end, as dates and whole hours are, then lies well
inside or outside the slider's window, as QGIS has it, rather than on its
rounded edge.

Times are compared as QGIS compares them, by their date and time of day
whatever their time zone (`Frame`), and the slider labels them so.

The pure part is here and unit-tested without QGIS; `TimeRequest` reads each
feature's span with QGIS's own expressions, as its filter does.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .export_ir import LayerTimeSpec

if TYPE_CHECKING:
    from .fidelity_report import FidelityReportBuilder

# A feature's span in milliseconds since 1970: its start and end, `None` on a
# side QGIS leaves open (a NULL time in the field modes, which passes that
# side's test). A span of `None` is a feature QGIS never draws.
Span = tuple[int | None, int | None]

DAY_MS = 86_400_000
# How far before its midnight a date the expression mode compares as text
# stands (`_milliseconds`): more than the slider's float margin to 2100.
DATE_LEAD_MS = 3_600_000

# The shortest a played sweep takes, so a slider over a few frames does not
# flash by.
MIN_SWEEP_MS = 2_000


@dataclasses.dataclass(frozen=True)
class Frame:
    """The frame QGIS draws, in milliseconds on QGIS's comparison clock.

    QGIS compares two dates and times by their date and time of day alone,
    whatever zone each is in (`QgsExpressionNodeBinaryOperator` sets both to
    UTC first), and writes the frame's ends into its filter as
    `make_datetime(...)` of their own date and time of day. So every time
    here is a date and time of day counted as UTC: `begin` and `end`, and
    each feature's (`TimeRequest.spans`). A temporal controller frame leaves
    its end out (`includes_end` off).
    """

    begin: int
    end: int
    includes_end: bool = False

    @property
    def length(self) -> int:
        return self.end - self.begin


@dataclasses.dataclass(frozen=True)
class SliderPlan:
    """The slider a layer's spans map onto.

    `values` is each feature's carried time, `None` for one the slider never
    shows; `window` the slider's window (`None` for accumulated features);
    the slider opens at `start`, its filter running from `low`. `open_ended`
    counts the features QGIS draws whose span is open on one side, which the
    slider cannot show.
    """

    values: tuple[int | None, ...]
    window: int | None
    start: int
    low: int
    open_ended: int = 0


def drawn(span: Span | None, frame: Frame, end_inclusive: bool) -> bool:
    """Whether QGIS draws a feature of `span` in `frame` (its filter string).

    `end_inclusive` is the layer's "Include end" limit: a feature then shows
    in a frame that begins exactly at its end.
    """
    if span is None:
        return False
    start, end = span
    if start is not None and not (
        start <= frame.end if frame.includes_end else start < frame.end
    ):
        return False
    return end is None or (end >= frame.begin if end_inclusive else end > frame.begin)


def float32_margin(largest: int) -> int:
    """How far to move times of magnitude up to `largest` milliseconds so that
    a 32-bit float tells a frame's edge from a time exactly on it: four of
    its steps at that magnitude (a float holds 24 significant bits)."""
    if largest <= 0:
        return 0
    step = 2.0 ** (math.floor(math.log2(largest)) - 23)
    return int(4 * max(step, 1.0))


def plan_slider(
    spans: Sequence[Span | None],
    frame: Frame,
    accumulate: bool,
    end_inclusive: bool,
) -> SliderPlan | str:
    """The slider `spans` map onto, or why there is none (a clause that
    follows "because").

    At a slider time `t` the runtime shows a carried value `f` when
    `t - window <= f <= t`. QGIS draws a span `[s, s + L]` in the frame
    `[t, t + d)` when `s` is at most the frame's last start `upper` and at
    least its first, `lower`: `t + d` and `t - L`, each in or out by QGIS's
    rules for the ends. So `f = s + t - upper` and `window = upper - lower`
    agree for every `t`, as long as every span lasts the same `L`. Each edge
    sits a `float32_margin` off its time, on the side that keeps a time
    exactly on it where QGIS has it, since the runtime's floats cannot tell
    the two apart.
    """
    length = frame.length
    if length <= 0:
        return "the frame QGIS draws has no length"
    starts = [span[0] for span in spans if span is not None and span[0] is not None]
    if not starts:
        return "no feature has a time"
    margin = float32_margin(max(abs(s - length) for s in starts))
    if 4 * margin > length:
        return (
            "its frames are shorter than the map can tell apart (it holds a "
            f"time to about {margin // 4 // 1000 or 1} seconds)"
        )
    # A start on the frame's end is drawn only by a frame that includes it.
    upper = frame.end + (margin if frame.includes_end else -margin)
    shift = frame.begin - upper
    start = frame.begin

    if accumulate:
        carried = [
            None if span is None or span[0] is None else span[0] + shift
            for span in spans
        ]
        low = min(value for value in carried if value is not None)
        # A feature with no time is drawn at every time: it takes the
        # slider's first value, which every position of the slider includes.
        accumulated = tuple(
            None if span is None else (low if value is None else value)
            for span, value in zip(spans, carried, strict=True)
        )
        return SliderPlan(accumulated, None, start, low)

    lengths = {
        span[1] - span[0]
        for span in spans
        if span is not None and span[0] is not None and span[1] is not None
    }
    if not lengths:
        return "no feature has both a start and an end time"
    if len(lengths) > 1:
        return "its features last different lengths of time"
    (span_length,) = lengths
    # An end on the frame's start is drawn only for features that include
    # their end. Half the margin: at either end of its range the slider sits
    # on a feature, and a window a margin shorter than the frame still keeps
    # out the feature a whole frame before it.
    lower = frame.begin - span_length + (-1 if end_inclusive else 1) * (margin // 2)
    window = upper - lower
    if window < 1:
        return "its features end before they start"
    open_ended = 0
    values: list[int | None] = []
    for span in spans:
        if span is not None and (span[0] is None or span[1] is None):
            open_ended += 1
        values.append(
            None
            if span is None or span[0] is None or span[1] is None
            else span[0] + shift
        )
    return SliderPlan(tuple(values), window, start, start - window, open_ended)


def sweep_ms(values: Iterable[int | None], frame_length: int, fps: float) -> int:
    """How long one played sweep over `values` takes: one frame of
    `frame_length` per 1/`fps` seconds, as QGIS's controller plays them."""
    found = [value for value in values if value is not None]
    if not found or frame_length <= 0:
        return MIN_SWEEP_MS
    frames = (max(found) - min(found)) / frame_length + 1
    return max(MIN_SWEEP_MS, round(1000 * frames / (fps if fps > 0 else 1.0)))


def date_style(frame: Frame, starts_at_midnight: bool) -> str:
    """`date` for frames of whole days that start at midnight, as a date-only
    layer's are; `datetime` for every other."""
    whole_days = frame.length > 0 and frame.length % DAY_MS == 0
    return "date" if whole_days and starts_at_midnight else "datetime"


def carried_field_name(taken: Iterable[str]) -> str:
    """A property name for the carried time that no field of the layer has."""
    used = set(taken)
    name = "qgis_time_ms"
    while name in used:
        name += "_"
    return name


# QGIS's `make_interval` argument for a duration field in each unit
# (`QgsVectorLayerTemporalProperties::createFilterString`), keyed by the
# `Qgis.TemporalUnit` name. `{}` is the field's quoted name.
_INTERVAL_ARGUMENTS = {
    "Milliseconds": "0,0,0,0,0,0,{}/1000",
    "Seconds": "0,0,0,0,0,0,{}",
    "Minutes": "0,0,0,0,0,{},0",
    "Hours": "0,0,0,0,{},0,0",
    "Days": "0,0,0,{},0,0,0",
    "Weeks": "0,0,{},0,0,0,0",
    "Months": "0,{},0,0,0,0,0",
    "Years": "{},0,0,0,0,0,0",
    "Decades": "10 * {},0,0,0,0,0,0",
    "Centuries": "100 * {},0,0,0,0,0,0",
}


@dataclasses.dataclass(frozen=True)
class TimeRequest:
    """How to read each feature's span, and the frame QGIS draws now.

    Made on the GUI thread from the live layer, used on a worker: copies of
    the layer's fields and expression context and plain values, as
    `RuleMatcher` carries them. `start` and `end` are expressions whose value
    is a feature's start and end, read as QGIS's filter reads them (through
    `to_datetime`, which reads a date as its midnight and a text as QGIS
    parses it), on its comparison clock (`Frame`); `end` is `None` for a
    point in time, which lasts `fixed`
    milliseconds (QGIS's "Event duration"). `open_ends` is on for the field
    modes, where a NULL time leaves its side open, and off for the expression
    mode, where QGIS never draws such a feature.
    """

    start: str
    end: str | None
    fixed: int
    accumulate: bool
    end_inclusive: bool
    open_ends: bool
    # QGIS's start-and-duration mode: a feature with a start but no duration
    # is open-ended, and one with a duration but no start is never drawn.
    duration_field: str | None
    frame: Frame
    starts_at_midnight: bool
    fps: float
    columns: frozenset[str] | None
    fields: Any
    context: Any

    def draws(self, span: Span | None) -> bool:
        """Whether QGIS draws a feature of `span` in the frame it draws now;
        accumulated, whether it began before the frame's end."""
        if self.accumulate:
            return span is not None and drawn((span[0], None), self.frame, True)
        return drawn(span, self.frame, self.end_inclusive)

    def spans(self, features: Iterable[Any]) -> list[Span | None]:
        """Each feature's span. **Safe off the GUI thread**: it reads only the
        features and the copies this request holds."""
        from qgis.core import QgsExpression, QgsExpressionContext

        context = QgsExpressionContext(self.context)
        # The field modes' filter reads a field through `to_datetime`; the
        # expression mode's compares each expression's value as it is.
        read = "to_datetime(({}))" if self.open_ends else "({})"
        start = QgsExpression(read.format(self.start))
        end = QgsExpression(read.format(self.end)) if self.end is not None else None
        duration = (
            QgsExpression(
                f"{QgsExpression.quotedColumnRef(self.duration_field)} IS NULL"
            )
            if self.duration_field
            else None
        )
        for expression in (start, end, duration):
            if expression is not None:
                expression.prepare(context)
        spans: list[Span | None] = []
        for feature in features:
            context.setFeature(feature)
            began = _milliseconds(start.evaluate(context))
            if end is None:
                if began is None:
                    spans.append((None, None) if self.open_ends else None)
                else:
                    spans.append((began, began + self.fixed))
                continue
            ended = _milliseconds(end.evaluate(context))
            if not self.open_ends:
                spans.append(None if began is None or ended is None else (began, ended))
            elif duration is not None and ended is None:
                # `start + duration > begin OR duration IS NULL`: no duration is
                # open, a duration added to no start is NULL, never drawn.
                spans.append((began, None) if duration.evaluate(context) else None)
            else:
                spans.append((began, ended))
        return spans


def _milliseconds(value: object) -> int | None:
    """A time on QGIS's comparison clock (`Frame`), `None` for NULL.

    A date, or a text that is one, is compared with a frame's date and time
    as text (QGIS compares unlike values so), where `2020-03-06` comes just
    before `2020-03-06T00:00:00`: it stands just before its midnight - an
    hour, so the slider's rounded edges (`float32_margin`) keep it on the
    side of a midnight frame edge QGIS has it. Any other text is read as
    `to_datetime` reads it.
    """
    from qgis.core import QgsExpression, QgsExpressionContext
    from qgis.PyQt.QtCore import QDate, QDateTime, Qt, QTime

    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value = QDate.fromString(value, Qt.DateFormat.ISODate)
    if isinstance(value, QDate):
        if not value.isValid():
            return None
        return clock_ms(QDateTime(value, QTime(0, 0))) - DATE_LEAD_MS
    if isinstance(value, str):
        expression = QgsExpression(f"to_datetime({QgsExpression.quotedString(value)})")
        value = expression.evaluate(QgsExpressionContext())
    if not isinstance(value, QDateTime) or not value.isValid():
        return None
    return clock_ms(value)


def clock_ms(moment: Any) -> int:
    """A `QDateTime`'s date and time of day, counted as UTC: how QGIS
    compares it (`Frame`)."""
    from qgis.PyQt.QtCore import QDateTime, Qt

    return int(
        QDateTime(moment.date(), moment.time(), Qt.TimeSpec.UTC).toMSecsSinceEpoch()
    )


def frame_from(time_range: Any) -> tuple[Frame, bool] | None:
    """The frame of a `QgsDateTimeRange` as QGIS's filter compares it, and
    whether it starts at midnight; `None` when either end is open."""
    begin, end = time_range.begin(), time_range.end()
    if not (begin.isValid() and end.isValid()):
        return None
    frame = Frame(clock_ms(begin), clock_ms(end), bool(time_range.includeEnd()))
    midnight = begin.time().hour() == 0 and begin.time().minute() == 0
    return frame, midnight and begin.time().second() == 0 and begin.time().msec() == 0


def interval_expression(field: str, unit_name: str) -> str | None:
    """`make_interval(...)` of a duration field in `unit_name`, as QGIS adds
    it to a feature's start; `None` for a unit QGIS reads no duration in."""
    from qgis.core import QgsExpression

    arguments = _INTERVAL_ARGUMENTS.get(unit_name)
    if arguments is None:
        return None
    return f"make_interval({arguments.format(QgsExpression.quotedColumnRef(field))})"


# The property a feature's span travels in from the feature read to
# `apply_time`, which takes it out again. No field can have the name.
SPAN_PROPERTY = "\x00span"


@dataclasses.dataclass(frozen=True)
class VectorTime:
    """What following QGIS's time does to a vector layer, read on the GUI
    thread: the spans to read (`request`, `None` when the layer is exported
    whole), whether it may get a slider, and whether QGIS does not draw it
    now at all (a fixed temporal range the frame misses)."""

    request: TimeRequest | None = None
    slider: bool = True
    hidden: bool = False
    begin_text: str = ""
    end_text: str = ""


def describe_length(milliseconds: int) -> str:
    """A frame's length in words: `1 day`, `6 hours`, `90 minutes`."""
    for unit, size in (
        ("week", 7 * DAY_MS),
        ("day", DAY_MS),
        ("hour", 3_600_000),
        ("minute", 60_000),
        ("second", 1_000),
    ):
        if milliseconds >= size and milliseconds % size == 0:
            count = milliseconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{milliseconds:,} millisecond{'' if milliseconds == 1 else 's'}"


def read_vector_time(
    layer: Any,
    report: FidelityReportBuilder,
    map_time: Any,
    slider: bool,
) -> VectorTime | None:
    """How `layer` follows QGIS's time in the export, with the Fidelity rows
    said before its features are read. **GUI thread only.**

    `None` for a layer that does not follow time, and for one exported whole
    as QGIS draws it without a time: with no QGIS map view to read the time
    from (`map_time` `None`: Processing, a cartograph's frame), with the
    temporal controller off, and a layer QGIS only redraws. `slider` is off
    where the map has no controls (a cartograph's frame): the layer is then a
    snapshot of the time QGIS draws.
    """
    from qgis.core import (
        Qgis,
        QgsExpression,
        QgsExpressionContext,
        QgsExpressionContextUtils,
        QgsFeatureRequest,
        QgsInterval,
    )

    properties = layer.temporalProperties()
    if properties is None or not properties.isActive():
        return None
    name = layer.name()
    layer_id = layer.id()
    subject = f"Time of '{name}'"
    mode = properties.mode()
    modes = Qgis.VectorTemporalMode
    if mode == modes.RedrawLayerOnly:
        report.approximated(
            subject,
            "QGIS redraws this layer at each time of its temporal controller, "
            "for symbols or labels that read the time. The web map draws it "
            "once, as it was exported, with no time slider.",
            layer_id,
        )
        return None
    if map_time is None:
        report.approximated(
            subject,
            "This layer follows QGIS's time, and this export has no QGIS map "
            "view to read the time from (Processing, a cartograph), so every "
            "feature is exported, as QGIS draws the layer while its temporal "
            "controller is off, with no time slider. The QGIS2WebMap dialog's "
            "web map export gives it one.",
            layer_id,
        )
        return None
    read = frame_from(map_time.range) if map_time.is_temporal else None
    if read is None:
        report.approximated(
            subject,
            "This layer follows QGIS's time, and QGIS's temporal controller is "
            "off, so QGIS draws all its features, and so does the web map, "
            "which has no time slider. Turn the temporal controller on and "
            "export again for one.",
            layer_id,
        )
        return None
    frame, midnight = read
    pattern = "yyyy-MM-dd" if midnight else "yyyy-MM-dd hh:mm"
    begin_text = map_time.range.begin().toString(pattern)
    end_text = map_time.range.end().toString(pattern)

    if mode == modes.FixedTemporalRange:
        fixed = properties.fixedTemporalRange()
        span = " to ".join(
            moment.toString("yyyy-MM-dd hh:mm")
            for moment in (fixed.begin(), fixed.end())
        )
        if properties.isVisibleInTemporalRange(map_time.range):
            report.preserved(
                subject,
                f"QGIS shows this layer from {span} only, which includes the "
                f"time it draws now ({begin_text} to {end_text}), so the web map "
                "shows it. It has no time slider: a slider is given to layers "
                "whose features each have their own time.",
                layer_id,
            )
            return None
        report.approximated(
            subject,
            f"QGIS shows this layer from {span} only, and not at the time it "
            f"draws now ({begin_text} to {end_text}), so the web map opens with "
            "the layer switched off, and switching it on shows all its "
            "features. It has no time slider: a slider is given to layers whose "
            "features each have their own time.",
            layer_id,
        )
        return VectorTime(hidden=True)

    end_inclusive = (
        properties.limitMode() == Qgis.VectorTemporalLimitMode.IncludeBeginIncludeEnd
    )
    fixed_ms = 0
    end: str | None = None
    duration_field: str | None = None
    quoted = QgsExpression.quotedColumnRef
    open_ends = True
    if mode == modes.FeatureDateTimeInstantFromField:
        start = quoted(properties.startField())
        fixed_ms = int(
            QgsInterval(
                properties.fixedDuration(), properties.durationUnits()
            ).seconds()
            * 1000
        )
        # A point in time with no event duration is drawn from the frame's
        # start on, its end included (`>=`), whichever limit is set.
        end_inclusive = end_inclusive or fixed_ms == 0
    elif mode == modes.FeatureDateTimeStartAndEndFromFields:
        start, end = quoted(properties.startField()), quoted(properties.endField())
    elif mode == modes.FeatureDateTimeStartAndDurationFromFields:
        start = quoted(properties.startField())
        interval = interval_expression(
            properties.durationField(), properties.durationUnits().name
        )
        if interval is None:
            report.unsupported(
                subject,
                "This layer's durations are in a unit QGIS reads no time from, "
                "so QGIS draws none of its features while its temporal "
                "controller is on. Every feature is exported, with no time "
                "slider. Choose another unit in the layer's Temporal properties.",
                layer_id,
            )
            return None
        end = f"to_datetime({start}) + {interval}"
        duration_field = properties.durationField()
    elif mode == modes.FeatureDateTimeStartAndEndFromExpressions:
        start, end = properties.startExpression(), properties.endExpression()
        open_ends = False
    else:
        report.unsupported(
            subject,
            "This layer follows QGIS's time in a way the export does not "
            "repeat, so every feature is exported, with no time slider.",
            layer_id,
        )
        return None

    context = QgsExpressionContext(
        QgsExpressionContextUtils.globalProjectLayerScopes(layer)
    )
    columns: set[str] = set()
    every_column = False
    for text in (start, end, quoted(duration_field) if duration_field else None):
        if text is None:
            continue
        used = QgsExpression(text).referencedColumns()
        if QgsFeatureRequest.ALL_ATTRIBUTES in used:
            every_column = True
            break
        columns |= set(used)
    project = layer.project()
    fps = (
        float(project.timeSettings().framesPerSecond()) if project is not None else 1.0
    )
    request = TimeRequest(
        start=start,
        end=end,
        fixed=fixed_ms,
        accumulate=bool(properties.accumulateFeatures())
        and mode == modes.FeatureDateTimeInstantFromField,
        end_inclusive=end_inclusive,
        open_ends=open_ends,
        duration_field=duration_field,
        frame=frame,
        starts_at_midnight=midnight,
        fps=fps,
        columns=None if every_column else frozenset(columns),
        fields=layer.fields(),
        context=context,
    )
    return VectorTime(request, slider, begin_text=begin_text, end_text=end_text)


def apply_time(
    geojson: dict[str, Any],
    time: VectorTime,
    name: str,
    layer_id: str,
    labelled: bool,
    report: FidelityReportBuilder,
) -> tuple[dict[str, Any], LayerTimeSpec | None]:
    """Give a layer's features their slider's carried time, or keep only
    those QGIS draws now, from the spans the feature read left in them
    (`SPAN_PROPERTY`), and say which."""
    request = time.request
    features = list(geojson.get("features") or ())
    spans: list[Span | None] = []
    for feature in features:
        properties = dict(feature.get("properties") or {})
        span = properties.pop(SPAN_PROPERTY, None)
        feature["properties"] = properties
        spans.append(None if span is None else (span[0], span[1]))
    if request is None:
        return geojson, None
    subject = f"Time of '{name}'"
    plan: SliderPlan | str = (
        plan_slider(spans, request.frame, request.accumulate, request.end_inclusive)
        if time.slider
        else "it is drawn in a cartograph's interactive frame, which has no controls"
    )
    if isinstance(plan, str):
        kept = [
            feature
            for feature, span in zip(features, spans, strict=True)
            if request.draws(span)
        ]
        report.approximated(
            subject,
            f"Exported as a fixed snapshot of {time.begin_text} to "
            f"{time.end_text}, the time QGIS draws now: the map shows the "
            f"{len(kept):,} of its {len(features):,} features QGIS draws then, "
            f"and has no time slider, because {plan}. To export another time, "
            "set it in QGIS and export again.",
            layer_id,
        )
        return {**geojson, "features": kept}, None

    field_name = carried_field_name(
        key for feature in features for key in (feature.get("properties") or {})
    )
    for feature, value in zip(features, plan.values, strict=True):
        feature["properties"] = {**feature["properties"], field_name: value}
    frame = describe_length(request.frame.length)
    shows = (
        f"every feature that began before the end of a {frame} frame starting "
        "at the slider's time, as Accumulate features does"
        if plan.window is None
        else f"the features QGIS draws for a {frame} frame starting at the "
        "slider's time"
    )
    report.approximated(
        subject,
        f"Exported with a time slider: the map opens at {time.begin_text}, the "
        f"time QGIS draws now, and the slider plays or scrubs through the "
        f"layer's times, showing {shows}. It moves smoothly rather than frame "
        "by frame, and spans the times of the layer's features rather than "
        "the temporal controller's range. A time within a few minutes of a "
        "frame's edge may show in the frame beside it.",
        layer_id,
    )
    if plan.open_ended:
        report.approximated(
            subject,
            f"{plan.open_ended:,} feature{' has' if plan.open_ended == 1 else 's have'}"
            " no start or no end time, which QGIS draws from or until any time; "
            "the time slider does not show "
            f"{'it' if plan.open_ended == 1 else 'them'}.",
            layer_id,
        )
    if labelled:
        report.approximated(
            subject,
            "The time slider does not reach the layer's labels: a label stays "
            "on the map when its feature is outside the slider's time.",
            layer_id,
        )
    spec = LayerTimeSpec(
        field_name=field_name,
        start=plan.start,
        low=plan.low,
        window=plan.window,
        duration=sweep_ms(plan.values, request.frame.length, request.fps),
        date_style=date_style(request.frame, request.starts_at_midnight),
    )
    return {**geojson, "features": features}, spec
