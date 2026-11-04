"""A layer's filter panel, used the way a reader uses it.

The panel is the runtime's own (`<om-widget type="filter">`); what is ours is
the filter its layer declares and the data it reads. So each test moves the
panel's control - a slider by keyboard, a tick box by a click - and reads
which points the map then draws, by looking at the drawn map where each point
is. From a file and over http.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_exported_map import open_map, require_webgl, wait_until_drawn

from nika_onlymap_exporter.core.attribute_filter import FilterRequest, build_filter
from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    FilterKind,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.ui.live_server import PreviewServer
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is unavailable; skipping the browser tier",
)

# name, lon, people, kind, surveyed - one with no values at all.
PLACES = (
    ("Ashford", -0.6, 100, "town", "2024-01-10T00:00:00.000"),
    ("Barnet", -0.2, 500, "village", "2024-02-10T00:00:00.000"),
    ("Crawley", 0.2, 900, "town", "2024-03-10T00:00:00.000"),
    ("Dover", 0.6, None, None, None),
)

PANEL = 'om-widget[type="filter"]'

# Which places the map draws: the snapshot's pixel under each one, red or not.
DRAWN = """
async (places) => {
  const el = document.querySelector('om-map');
  await el.whenSettled({ timeout: 20000 });
  const url = await el.snapshot();
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = url;
  });
  const canvas = document.createElement('canvas');
  canvas.width = img.width;
  canvas.height = img.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const ratio = img.width / el.getBoundingClientRect().width;
  return places.filter(([name, lon]) => {
    const [x, y] = el.project([lon, 51.0]);
    const pixel = ctx.getImageData(Math.round(x * ratio), Math.round(y * ratio), 1, 1);
    const [r, g, b] = pixel.data;
    return r > 190 && g < 60 && b < 60;
  }).map(([name]) => name);
}
"""


def _project(field: str, kind: FilterKind, title: str) -> ExportProject:
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, 51.0]},
                "properties": {
                    "name": name,
                    "people": people,
                    "kind": kind_value,
                    "surveyed": surveyed,
                },
            }
            for name, lon, people, kind_value, surveyed in PLACES
        ],
    }
    # The same step the reader takes on the layer's exported data.
    outcome = build_filter(geojson, FilterRequest(field, title, kind))
    layer = ExportLayer(
        layer_id="places",
        name="Places",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=len(PLACES),
        geojson=outcome.geojson,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=230, g=20, b=20), radius=9.0),
        ),
        filter=outcome.spec,
    )
    return ExportProject(
        title="Filter map",
        layers=(layer,),
        extent=Extent(west=-0.9, south=50.8, east=0.9, north=51.2),
        settings=ExportSettings(show_title=False, show_legend=False),
    )


def _write(runtime, tmp_path, *args):
    return (
        OnlyMapWriter(runtime_provider=runtime)
        .write(_project(*args), tmp_path)
        .entry_path
    )


def _drawn(page) -> list[str]:
    return page.evaluate(DRAWN, [[name, lon] for name, lon, *_ in PLACES])


def _open(page, entry, url: str = "") -> None:
    page.set_viewport_size({"width": 1000, "height": 700})
    if url:
        page.goto(url)
        page.wait_for_function(
            "() => customElements.get('om-map') !== undefined", timeout=30_000
        )
    else:
        open_map(page, entry)
    require_webgl(page)
    wait_until_drawn(page)
    page.wait_for_function(
        """(selector) => document.querySelector(selector)
             ?.shadowRoot?.querySelector('.omni-filter')""",
        arg=PANEL,
        timeout=30_000,
    )


class TestASlider:
    def test_it_opens_showing_every_place_with_a_value(
        self, page, runtime, tmp_path
    ) -> None:
        """Dover has no number, and a filter keeps a point by its value -
        which the Fidelity tab says."""
        _open(page, _write(runtime, tmp_path, "people", FilterKind.NUMBER, "People"))
        assert _drawn(page) == ["Ashford", "Barnet", "Crawley"]
        heading = page.locator(PANEL).locator(".omni-filter-label").text_content()
        assert heading == "People"

    def test_moving_the_slider_hides_what_falls_outside(
        self, page, runtime, tmp_path
    ) -> None:
        _open(page, _write(runtime, tmp_path, "people", FilterKind.NUMBER, "People"))
        low = page.locator(PANEL).locator("input.omni-filter-min")
        low.focus()
        # Up from 100 by a step at a time, as a reader holding the arrow key.
        for _ in range(60):
            page.keyboard.press("ArrowRight")
        assert _drawn(page) == ["Crawley"]
        high = page.locator(PANEL).locator("input.omni-filter-max")
        high.focus()
        page.keyboard.press("Home")
        # Min and max swap when crossed: the range is now 100 to the low end.
        assert _drawn(page) == ["Ashford", "Barnet"]

    def test_a_date_slider_filters_by_date(self, page, runtime, tmp_path) -> None:
        _open(
            page,
            _write(runtime, tmp_path, "surveyed", FilterKind.DATE, "Surveyed"),
        )
        assert _drawn(page) == ["Ashford", "Barnet", "Crawley"]
        ends = page.locator(PANEL).evaluate(
            """el => [...el.shadowRoot.querySelectorAll(
                 '.omni-filter-min-val, .omni-filter-max-val')]
                 .map((end) => end.textContent)"""
        )
        # Dates, not milliseconds, and the dates QGIS shows.
        assert ends == ["Jan 10, 2024", "Mar 10, 2024"]
        page.locator(PANEL).locator("input.omni-filter-max").focus()
        page.keyboard.press("Home")
        assert _drawn(page) == ["Ashford"]

    def test_it_works_when_served(self, page, runtime, tmp_path) -> None:
        entry = _write(runtime, tmp_path, "people", FilterKind.NUMBER, "People")
        server = PreviewServer(entry.parent)
        server.start()
        try:
            _open(page, entry, f"http://127.0.0.1:{server.port}/{entry.name}")
            page.locator(PANEL).locator("input.omni-filter-max").focus()
            page.keyboard.press("Home")
            assert _drawn(page) == ["Ashford"]
        finally:
            server.stop()


class TestTickBoxes:
    def test_unticking_a_value_hides_its_places(self, page, runtime, tmp_path) -> None:
        _open(page, _write(runtime, tmp_path, "kind", FilterKind.TEXT, "Kind"))
        rows = page.locator(PANEL).locator(".omni-filter-check-row")
        assert rows.all_text_contents() == ["town2", "village1"]
        assert _drawn(page) == ["Ashford", "Barnet", "Crawley"]
        page.locator(PANEL).locator("label", has_text="town").click()
        assert _drawn(page) == ["Barnet"]
        page.locator(PANEL).locator("label", has_text="town").click()
        assert _drawn(page) == ["Ashford", "Barnet", "Crawley"]

    def test_unticking_every_value_hides_every_place(
        self, page, runtime, tmp_path
    ) -> None:
        """No value ticked keeps no place, as no category ticked does in QGIS."""
        _open(page, _write(runtime, tmp_path, "kind", FilterKind.TEXT, "Kind"))
        for value in ("town", "village"):
            page.locator(PANEL).locator("label", has_text=value).click()
        assert _drawn(page) == []
        # Still the tick boxes, all cleared: not a slider, nor ticked again.
        boxes = page.locator(PANEL).locator(".omni-filter-check-row input")
        assert [box.is_checked() for box in boxes.all()] == [False, False]
        page.locator(PANEL).locator("label", has_text="village").click()
        assert _drawn(page) == ["Barnet"]
