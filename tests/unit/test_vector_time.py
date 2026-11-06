"""A vector layer's time slider, worked out from its features' time spans.

What these pin is the mapping in `core.vector_time`: at every slider time,
the features the runtime's slider shows (a window both of whose ends are
included, in the 32-bit floats it filters in) are the ones QGIS's own filter
draws for the frame starting there (`drawn`, whose rules the QGIS tier checks
against QGIS itself in `tests/qgis/test_vector_time_layers.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import struct

import pytest

from nika_onlymap_exporter.core.export_ir import LayerTimeSpec
from nika_onlymap_exporter.core.fidelity_report import (
    FidelityReportBuilder,
    FidelityStatus,
)
from nika_onlymap_exporter.core.vector_time import (
    DAY_MS,
    MIN_SWEEP_MS,
    SPAN_PROPERTY,
    Frame,
    SliderPlan,
    TimeRequest,
    VectorTime,
    apply_time,
    date_style,
    describe_frame,
    drawn,
    float32_margin,
    plan_slider,
    sweep_ms,
)

HOUR = 3_600_000
# 2020-03-05 00:00 UTC, a time of the magnitude real data has.
B = 1_583_366_400_000
FRAME = Frame(B, B + DAY_MS)


def _f32(value: float) -> float:
    """`value` as the runtime's filter holds it: a 32-bit float."""
    return float(struct.unpack("f", struct.pack("f", value))[0])


def _shown(plan: SliderPlan, t: int) -> set[int]:
    """The features the runtime's slider at `t` shows: `low <= f <= t`, with
    `low` the window before `t`, in 32-bit floats as the graphics card
    compares them."""
    low = plan.low if plan.window is None else t - plan.window
    return {
        index
        for index, value in enumerate(plan.values)
        if value is not None and _f32(low) <= _f32(value) <= _f32(t)
    }


def _qgis(spans, t: int, accumulate: bool, end_inclusive: bool) -> set[int]:
    frame = Frame(t, t + FRAME.length, FRAME.includes_end)
    if accumulate:
        return {
            index
            for index, span in enumerate(spans)
            if span is not None and drawn((span[0], None), frame, True)
        }
    return {
        index for index, span in enumerate(spans) if drawn(span, frame, end_inclusive)
    }


# Times on the frame grid, on its edges and between them.
GRID = [B + k * DAY_MS for k in range(-3, 4)]
HALVES = [B + k * DAY_MS + 6 * HOUR for k in range(-3, 3)]
INSTANTS = [(t, t) for t in GRID + HALVES]


@pytest.mark.parametrize(
    ("name", "spans", "accumulate", "end_inclusive"),
    [
        # A point in time: QGIS draws it from the frame's start on, its end
        # included whatever the limit (`>=`).
        ("instant", INSTANTS, False, True),
        # With an event duration of 12 hours, either limit.
        ("event", [(t, t + 12 * HOUR) for t in GRID + HALVES], False, False),
        ("event, end included", [(t, t + 12 * HOUR) for t in GRID], False, True),
        # Start and end two days apart, ending exactly on frame edges.
        ("interval", [(t, t + 2 * DAY_MS) for t in GRID + HALVES], False, False),
        ("interval, end included", [(t, t + 2 * DAY_MS) for t in GRID], False, True),
        # Accumulate features: everything that began before the frame's end,
        # and a feature with no time at every time.
        ("accumulate", [*INSTANTS, (None, None)], True, True),
        # Features QGIS never draws (an expression's NULL) stay hidden.
        ("never drawn", [*INSTANTS, None], False, True),
    ],
)
def test_the_slider_shows_what_qgis_draws_at_every_frame(
    name, spans, accumulate, end_inclusive
) -> None:
    plan = plan_slider(spans, FRAME, accumulate, end_inclusive)
    assert isinstance(plan, SliderPlan), plan
    assert plan.start == B
    assert _shown(plan, B) == _qgis(spans, B, accumulate, end_inclusive)
    assert _shown(plan, B), "the opening frame draws something"
    # Every frame the controller steps to, and slider times between frames,
    # over the slider's span: its thumb goes no further.
    found = [value for value in plan.values if value is not None]
    for step in range(-4, 5):
        for offset in (0, 6 * HOUR, 18 * HOUR):
            t = B + step * DAY_MS + offset
            if not min(found) <= t <= max(found):
                continue
            assert _shown(plan, t) == _qgis(spans, t, accumulate, end_inclusive), (
                name,
                step,
                offset,
            )


def test_at_either_end_of_its_range_the_slider_shows_one_day() -> None:
    """The thumb's ends sit on a feature's carried time, a whole frame from
    the next: only the one frame shows, in the runtime's floats."""
    spans = [(t, t) for t in GRID]
    plan = plan_slider(spans, FRAME, False, True)
    assert isinstance(plan, SliderPlan)
    values = [value for value in plan.values if value is not None]
    assert _shown(plan, max(values)) == {len(GRID) - 1}
    assert _shown(plan, min(values)) == {0}


def test_a_frame_that_includes_its_end_draws_a_feature_on_it() -> None:
    frame = Frame(B, B + DAY_MS, includes_end=True)
    spans = [(B + DAY_MS, B + DAY_MS), (B + DAY_MS + HOUR, B + DAY_MS + HOUR)]
    plan = plan_slider(spans, frame, False, True)
    assert isinstance(plan, SliderPlan)
    assert _shown(plan, B) == {0}


def test_the_edges_hold_in_the_runtime_floats() -> None:
    """Without the margin a date on the next frame's start rounds onto the
    slider's edge and shows in two frames."""
    plan = plan_slider([(B, B), (B + DAY_MS, B + DAY_MS)], FRAME, False, True)
    assert isinstance(plan, SliderPlan)
    assert _shown(plan, B) == {0}
    assert _shown(plan, B + DAY_MS) == {1}


def test_open_ended_features_are_counted_and_hidden() -> None:
    spans = [(B, B + DAY_MS), (B, None), (None, B + DAY_MS)]
    plan = plan_slider(spans, FRAME, False, False)
    assert isinstance(plan, SliderPlan)
    assert plan.open_ended == 2
    assert plan.values[1:] == (None, None)


@pytest.mark.parametrize(
    ("spans", "frame", "reason"),
    [
        ([(B, B + DAY_MS), (B, B + 2 * DAY_MS)], FRAME, "different lengths"),
        ([None, (None, None)], FRAME, "no feature has a time"),
        ([(B, None)], FRAME, "both a start and an end"),
        ([(B, B)], Frame(B, B + 1_000), "shorter than the map can tell apart"),
        ([(B, B - 2 * DAY_MS)], FRAME, "end before they start"),
    ],
)
def test_without_one_shift_there_is_no_slider(spans, frame, reason) -> None:
    plan = plan_slider(spans, frame, False, False)
    assert isinstance(plan, str) and reason in plan


def test_the_float32_margin_tells_an_edge_from_a_time_on_it() -> None:
    for value in (B, B + 12_345, 4_102_444_800_000, 86_400_000):
        margin = float32_margin(value)
        assert _f32(value) != _f32(value + margin)
        assert _f32(value) != _f32(value - margin)
    # A few minutes for today's times, so only times that close to an edge
    # can show in the neighbouring frame.
    assert float32_margin(B) <= 10 * 60_000


def test_a_sweep_plays_one_frame_per_controller_frame() -> None:
    values = [B, B + 9 * DAY_MS, None]
    assert sweep_ms(values, DAY_MS, 1.0) == 10_000
    assert sweep_ms(values, DAY_MS, 2.0) == 5_000
    assert sweep_ms([B], DAY_MS, 1.0) == MIN_SWEEP_MS


@pytest.mark.parametrize(
    ("frame", "midnight", "style"),
    [
        (FRAME, True, "date"),
        (Frame(B, B + 7 * DAY_MS), True, "date"),
        (Frame(B, B + 6 * HOUR), True, "datetime"),
        (FRAME, False, "datetime"),
    ],
)
def test_whole_days_from_midnight_label_dates(frame, midnight, style) -> None:
    assert date_style(frame, midnight) == style


def test_frame_lengths_read_in_words() -> None:
    assert describe_frame(DAY_MS) == "a 1-day frame"
    assert describe_frame(6 * HOUR) == "a 6-hour frame"
    assert describe_frame(8 * HOUR) == "an 8-hour frame"
    assert describe_frame(18 * HOUR) == "an 18-hour frame"
    assert describe_frame(14 * DAY_MS) == "a 2-week frame"
    assert describe_frame(90 * 60_000) == "a 90-minute frame"


def _request(**changes) -> TimeRequest:
    values = {
        "start": '"t"',
        "end": None,
        "fixed": 0,
        "accumulate": False,
        "end_inclusive": True,
        "open_ends": True,
        "duration_field": None,
        "frame": FRAME,
        "starts_at_midnight": True,
        "fps": 1.0,
        "columns": frozenset({"t"}),
        "fields": None,
        "context": None,
    }
    values.update(changes)
    return TimeRequest(**values)


def _collection(spans) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "qgis_time_ms": "a field of the same name",
                    SPAN_PROPERTY: None if span is None else list(span),
                },
            }
            for span in spans
        ],
    }


class TestApplyTime:
    def test_a_slider_carries_the_times_under_a_free_name(self) -> None:
        report = FidelityReportBuilder()
        geojson, spec = apply_time(
            _collection([(B, B), (B + DAY_MS, B + DAY_MS)]),
            VectorTime(_request(), begin_text="2020-03-05", end_text="2020-03-06"),
            "Quakes",
            "q",
            True,
            report,
        )
        assert isinstance(spec, LayerTimeSpec)
        assert spec.field_name == "qgis_time_ms_"
        assert (spec.start, spec.date_style) == (B, "date")
        # A day, less the few minutes that keep its edges off the round times.
        assert spec.window is not None and DAY_MS - 600_000 < spec.window < DAY_MS
        for feature in geojson["features"]:
            assert SPAN_PROPERTY not in feature["properties"]
            assert feature["properties"]["qgis_time_ms"] == "a field of the same name"
        rows = [item.detail for item in report.items]
        assert any(
            "opens at 2020-03-05" in row and "a 1-day frame" in row for row in rows
        )
        assert any("does not reach the layer's labels" in row for row in rows)

    @pytest.mark.parametrize("accumulate", [False, True])
    def test_a_snapshot_keeps_what_qgis_draws_now(self, accumulate) -> None:
        """A map with no controls: no slider, the features of the frame QGIS
        draws."""
        report = FidelityReportBuilder()
        spans = [(B - DAY_MS, B - DAY_MS), (B, B), (B + DAY_MS, B + DAY_MS), None]
        geojson, spec = apply_time(
            _collection(spans),
            VectorTime(
                _request(accumulate=accumulate),
                slider=False,
                begin_text="2020-03-05",
                end_text="2020-03-06",
            ),
            "Quakes",
            "q",
            False,
            report,
        )
        assert spec is None
        kept = [f["properties"].get(SPAN_PROPERTY) for f in geojson["features"]]
        assert len(kept) == (2 if accumulate else 1)
        (row,) = [i for i in report.items if i.status is FidelityStatus.APPROXIMATED]
        assert "fixed snapshot of 2020-03-05 to 2020-03-06" in row.detail
        assert "cartograph's interactive frame" in row.detail

    def test_different_lengths_are_a_snapshot_saying_why(self) -> None:
        report = FidelityReportBuilder()
        geojson, spec = apply_time(
            _collection([(B, B + DAY_MS), (B - 3 * DAY_MS, B - DAY_MS)]),
            VectorTime(_request(end='"e"'), begin_text="a", end_text="b"),
            "Works",
            "w",
            False,
            report,
        )
        assert spec is None
        assert len(geojson["features"]) == 1
        assert "different lengths of time" in report.items[0].detail
