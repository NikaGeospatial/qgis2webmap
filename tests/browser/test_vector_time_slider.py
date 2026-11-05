"""A vector layer's time slider, used the way a reader uses it.

The slider is the runtime's own (`<om-widget type="time-slider" field>`);
what is ours is the carried time, window and opening filter the layer
declares (`core.vector_time`). Each test moves the slider - scrubbing it,
playing it - and reads which points the map then draws, by looking at the
drawn map where each point is: the features QGIS draws for the frame the
slider stands at, edges included. From a file and served, as the Preview
serves it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_attribute_filter import DRAWN
from test_exported_map import open_map, require_webgl, wait_until_drawn

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.vector_time import (
    DAY_MS,
    SPAN_PROPERTY,
    Frame,
    TimeRequest,
    VectorTime,
    apply_time,
)
from nika_onlymap_exporter.ui.live_server import PreviewServer
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

# 2020-03-01 00:00 on QGIS's comparison clock; the frame QGIS draws is the
# day from 2020-03-05.
MARCH_1 = 1_583_020_800_000
OPENING = Frame(MARCH_1 + 4 * DAY_MS, MARCH_1 + 5 * DAY_MS)
# Ten points, one a day from 2020-03-01, west to east.
DAYS = [(f"Mar {day + 1}", -0.9 + day * 0.2) for day in range(10)]

SLIDER = 'om-widget[type="time-slider"]'
STATE = """
(widget) => {
  const root = widget.shadowRoot;
  return {
    value: Number(root.querySelector('.omni-time-scrub').value),
    shown: root.querySelector('.omni-time-val').textContent,
    label: root.querySelector('.omni-time-label').textContent,
    play: root.querySelector('.omni-time-play').getAttribute('aria-label'),
  };
}
"""
SCRUB = """
(widget, value) => {
  const scrub = widget.shadowRoot.querySelector('.omni-time-scrub');
  scrub.value = String(value);
  scrub.dispatchEvent(new Event('input', { bubbles: true }));
}
"""


def _project(spans, accumulate=False, end_inclusive=True):
    """The layer as the reader leaves it: its features' spans, then the
    slider `apply_time` works out from them."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, 51.0]},
                "properties": {"name": name, SPAN_PROPERTY: list(span)},
            }
            for (name, lon), span in zip(DAYS, spans, strict=True)
        ],
    }
    request = TimeRequest(
        start='"t"',
        end=None,
        fixed=0,
        accumulate=accumulate,
        end_inclusive=end_inclusive,
        open_ends=True,
        duration_field=None,
        frame=OPENING,
        starts_at_midnight=True,
        fps=4.0,
        columns=frozenset({"t"}),
        fields=None,
        context=None,
    )
    geojson, time = apply_time(
        geojson,
        VectorTime(request, begin_text="2020-03-05", end_text="2020-03-06"),
        "Events",
        "events",
        False,
        FidelityReportBuilder(),
    )
    assert time is not None
    layer = ExportLayer(
        layer_id="events",
        name="Events",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=len(DAYS),
        geojson=geojson,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=230, g=20, b=20), radius=9.0),
        ),
        time=time,
    )
    return ExportProject(
        title="Time map",
        layers=(layer,),
        extent=Extent(west=-1.1, south=50.8, east=1.1, north=51.2),
        settings=ExportSettings(show_title=False, show_legend=False),
    )


INSTANTS = [(MARCH_1 + day * DAY_MS,) * 2 for day in range(10)]
# Two days each, from each day: the frame from 2020-03-05 overlaps the ones
# from 2020-03-04 and 2020-03-05, and touches the one ending on its start.
TWO_DAYS = [(MARCH_1 + day * DAY_MS, MARCH_1 + (day + 2) * DAY_MS) for day in range(10)]


def _open(page, runtime, tmp_path, project, served=False):
    entry = OnlyMapWriter(runtime_provider=runtime).write(project, tmp_path).entry_path
    server = None
    page.set_viewport_size({"width": 1100, "height": 700})
    if served:
        server = PreviewServer(entry.parent)
        server.start()
        page.goto(f"http://127.0.0.1:{server.port}/{entry.name}")
        page.wait_for_function(
            "() => customElements.get('om-map') !== undefined", timeout=30_000
        )
    else:
        open_map(page, entry)
    require_webgl(page)
    wait_until_drawn(page)
    page.wait_for_function(
        """(selector) => document.querySelector(selector)
             ?.shadowRoot?.querySelector('.omni-time-scrub')""",
        arg=SLIDER,
        timeout=30_000,
    )
    return server


def _drawn(page) -> list[str]:
    return page.evaluate(DRAWN, [[name, lon] for name, lon in DAYS])


@pytest.mark.parametrize("served", [False, True], ids=["file", "served"])
def test_it_opens_at_qgis_time_showing_its_frame(page, runtime, tmp_path, served):
    """2020-03-05's point and no other: not 2020-03-06's, on the frame's end."""
    server = _open(page, runtime, tmp_path, _project(INSTANTS), served)
    try:
        assert _drawn(page) == ["Mar 5"]
        state = page.locator(SLIDER).evaluate(STATE)
        # The thumb on the step nearest it (a five-hundredth of the span).
        assert abs(state["value"] - OPENING.begin) <= 9 * DAY_MS / 1000
        assert state["shown"] == "Mar 5, 2020"
        assert state["label"] == "Events"
    finally:
        if server is not None:
            server.stop()


def test_scrubbing_shows_the_frame_starting_there(page, runtime, tmp_path):
    _open(page, runtime, tmp_path, _project(INSTANTS))
    slider = page.locator(SLIDER)
    # Half way through 2020-03-07: the day from there holds 2020-03-08 00:00.
    slider.evaluate(SCRUB, OPENING.begin + 2 * DAY_MS + DAY_MS // 2)
    assert _drawn(page) == ["Mar 8"]
    slider.evaluate(SCRUB, MARCH_1 + DAY_MS // 2)
    assert _drawn(page) == ["Mar 2"]


def test_playing_sweeps_to_the_last_time(page, runtime, tmp_path):
    _open(page, runtime, tmp_path, _project(INSTANTS))
    slider = page.locator(SLIDER)
    slider.locator(".omni-time-play").click()
    # Ten frames at QGIS's four a second: about three seconds.
    page.wait_for_function(
        """(selector) => document.querySelector(selector).shadowRoot
             .querySelector('.omni-time-play').getAttribute('aria-label') === 'Play'""",
        arg=SLIDER,
        timeout=20_000,
    )
    assert _drawn(page) == ["Mar 10"]


def test_accumulated_features_stay_once_shown(page, runtime, tmp_path):
    _open(page, runtime, tmp_path, _project(INSTANTS, accumulate=True))
    assert _drawn(page) == ["Mar 1", "Mar 2", "Mar 3", "Mar 4", "Mar 5"]
    page.locator(SLIDER).evaluate(SCRUB, OPENING.begin + 3 * DAY_MS + DAY_MS // 2)
    assert _drawn(page) == [name for name, _ in DAYS[:9]]


@pytest.mark.parametrize(
    ("end_inclusive", "shown"),
    [
        # Leaving its end out, the span ending on the frame's start is not
        # drawn; including it, it is - as QGIS's filter has it.
        (False, ["Mar 4", "Mar 5"]),
        (True, ["Mar 3", "Mar 4", "Mar 5"]),
    ],
)
def test_spans_overlapping_the_frame_show(
    page, runtime, tmp_path, end_inclusive, shown
):
    _open(page, runtime, tmp_path, _project(TWO_DAYS, end_inclusive=end_inclusive))
    assert _drawn(page) == shown
