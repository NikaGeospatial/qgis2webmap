"""The measure tool, used the way a reader uses it.

The widget is the runtime's own (`<om-widget type="measure">`); what is ours is
that it is on the exported map, in a corner where it covers nothing, and that
it still works in the three places a map is opened: from a file, over http,
and in the dialog's Preview - for a QGIS project in a geographic CRS and one in
a projected CRS. So these tests click two points on the map and read the
distance the reader is shown. The projects are real QGIS ones, built by
`measure_fixture_builder.py`.

The known distance is worked out here, from the coordinates, rather than read
off the scale bar - the scale bar is the one piece of the runtime known to be
wrong (by a factor of two, upstream), and a test anchored to it would agree
with a wrong measure tool.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_exported_map import open_map, require_webgl, wait_until_drawn

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.ui.live_server import PreviewServer
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

# Two points on one parallel, 1.2 degrees of longitude apart, and a feature
# well away from the line between them so placing them opens no popup. The
# fixture builder's projects hold a point at FEATURE too.
START = (0.7, 51.5)
END = (-0.5, 51.5)
FEATURE = (0.1, 51.1)

MEASURE = 'om-widget[type="measure"]'
READOUT = f"{MEASURE} .readout"


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance on the mean Earth radius, in kilometres."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _project(settings: ExportSettings) -> ExportProject:
    layer = ExportLayer(
        layer_id="sites",
        name="Sites",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(FEATURE)},
                    "properties": {"name": "Wye"},
                }
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=31, g=119, b=180), radius=8.0),
        ),
        popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("name"),)),
    )
    return ExportProject(
        title="Measure tool map",
        layers=(layer,),
        extent=Extent(west=-1.0, south=50.9, east=1.0, north=51.9),
        settings=settings,
    )


@pytest.fixture(scope="module")
def measure_map(runtime, tmp_path_factory):
    """The default export, written once: the measure tool is on by default."""
    destination = tmp_path_factory.mktemp("measure")
    return OnlyMapWriter(runtime_provider=runtime).write(
        _project(ExportSettings()), destination
    )


@pytest.fixture(scope="module")
def qgis_maps(tmp_path_factory) -> tuple[Path, dict]:
    """The real-QGIS maps, from CI's download or built here with QGIS's Python."""
    prebuilt = os.environ.get("MEASURE_FIXTURES_DIR")
    if prebuilt:
        maps = Path(prebuilt) / "maps.json"
        if not maps.exists():
            pytest.fail(f"MEASURE_FIXTURES_DIR has no maps.json: {prebuilt}")
        return Path(prebuilt), json.loads(maps.read_text())
    qgis_python = os.environ.get("QGIS_PYTHON")
    if not qgis_python:
        pytest.skip("set QGIS_PYTHON to QGIS's interpreter to build the fixtures")
    out_dir = tmp_path_factory.mktemp("measure-maps")
    builder = Path(__file__).resolve().parent / "measure_fixture_builder.py"
    completed = subprocess.run(
        [qgis_python, str(builder), str(out_dir)],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    maps = out_dir / "maps.json"
    assert maps.exists(), completed.stdout + completed.stderr
    return out_dir, json.loads(maps.read_text())


@pytest.fixture
def opened(request, page, qgis_maps):
    """A real-QGIS map, opened the way the test is parametrised with: from a
    file, over http, or as the dialog's Preview is (served, with its hook)."""
    crs, how = request.param
    root, maps = qgis_maps
    entry = root / maps[crs]["preview" if how == "preview" else "file"]
    page.set_viewport_size({"width": 1000, "height": 800})
    if how == "file":
        open_map(page, entry)
        yield page
        return
    server = PreviewServer(entry.parent)
    server.start()
    try:
        page.goto(f"http://127.0.0.1:{server.port}/{entry.name}")
        page.wait_for_function(
            "() => customElements.get('om-map') !== undefined", timeout=30_000
        )
        yield page
    finally:
        server.stop()


def _screen_point(page, lon_lat: tuple[float, float]) -> tuple[float, float]:
    """Where a coordinate is on screen, by the map's own projection."""
    box = page.locator("om-map").bounding_box()
    assert box is not None
    x, y = page.evaluate(
        "(c) => document.querySelector('om-map').project(c)", list(lon_lat)
    )
    assert 0 < x < box["width"] and 0 < y < box["height"], f"{lon_lat} is off screen"
    return box["x"] + x, box["y"] + y


def _click_at(page, lon_lat: tuple[float, float]) -> None:
    """Move there the way a hand does, then click."""
    x, y = _screen_point(page, lon_lat)
    page.mouse.move(x, y, steps=15)
    page.mouse.click(x, y)
    page.wait_for_timeout(150)


def _visible_popups(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('om-overlay')]
             .filter(o => !o.id.startsWith('__')
                       && getComputedStyle(o).visibility !== 'hidden'
                       && o.getBoundingClientRect().width > 0)
             .map(o => o.id)"""
    )


def _kilometres(readout: str) -> float:
    match = re.search(r"Total:\s*([\d.,]+)\s*(km|m)\b", readout)
    assert match, f"no distance in the readout: {readout!r}"
    value = float(match.group(1).replace(",", ""))
    return value if match.group(2) == "km" else value / 1000


def _measure(page) -> str:
    """Measure START to END and return what the reader is shown."""
    page.locator(f'{MEASURE} button[data-mode="distance"]').click(timeout=10_000)
    _click_at(page, START)
    _click_at(page, END)
    # Enter finishes the line where it is. Until then the total follows the
    # pointer to wherever it goes next - the runtime's live preview.
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    return page.locator(READOUT).inner_text(timeout=10_000)


@pytest.mark.parametrize(
    "opened",
    [
        (crs, how)
        for crs in ("geographic", "projected")
        for how in ("file", "http", "preview")
    ],
    indirect=True,
    ids=lambda p: "-".join(p),
)
class TestMeasuringADistance:
    def test_it_reads_the_distance_between_two_clicks(self, opened) -> None:
        page = opened
        require_webgl(page)
        wait_until_drawn(page)

        shown = _kilometres(_measure(page))

        expected = _haversine_km(START, END)
        # The runtime measures on the ellipsoid, this on a sphere - a few
        # tenths of a percent apart here - and each click lands on a whole
        # pixel, a fraction of a kilometre at these zooms. 2% is far inside
        # the error a wrong projection or unit would make.
        assert shown == pytest.approx(expected, rel=0.02), (
            f"measured {shown} km between points {expected:.1f} km apart"
        )

    def test_the_finished_line_stays_put(self, opened) -> None:
        page = opened
        require_webgl(page)
        wait_until_drawn(page)

        finished = _measure(page)
        page.mouse.move(500, 700, steps=15)
        page.wait_for_timeout(200)

        assert page.locator(READOUT).inner_text() == finished


class TestTheToolOnTheMap:
    def test_measuring_opens_no_popup_and_popups_return_after(
        self, page, measure_map
    ) -> None:
        """A click while measuring places a point; it must not also open the
        popup of whatever is under it - and switching the tool off must give
        clicks back to the popups."""
        page.set_viewport_size({"width": 1000, "height": 800})
        open_map(page, measure_map.entry_path)
        require_webgl(page)
        wait_until_drawn(page)

        page.locator(f'{MEASURE} button[data-mode="distance"]').click(timeout=10_000)
        _click_at(page, FEATURE)
        assert _visible_popups(page) == []

        # The same button again leaves measuring.
        page.locator(f'{MEASURE} button[data-mode="distance"]').click(timeout=10_000)
        _click_at(page, FEATURE)
        page.wait_for_function(
            """() => [...document.querySelectorAll('om-overlay')].some(o =>
                 !o.id.startsWith('__') && getComputedStyle(o).visibility !== 'hidden'
                 && o.getBoundingClientRect().width > 0)""",
            timeout=10_000,
        )

    def test_it_covers_no_other_control(self, page, measure_map) -> None:
        page.set_viewport_size({"width": 1000, "height": 800})
        open_map(page, measure_map.entry_path)
        require_webgl(page)
        wait_until_drawn(page)

        boxes = page.evaluate(
            """() => [...document.querySelectorAll(
                 'om-widget, .om-caption, .om-credit, [data-om-badge]')]
               .map(e => { const r = e.getBoundingClientRect();
                 return {name: e.getAttribute('type') || e.className
                           || 'badge', x: r.x, y: r.y, w: r.width, h: r.height}; })
               .filter(b => b.w > 0 && b.h > 0)"""
        )
        tool = next(b for b in boxes if b["name"] == "measure")
        for other in boxes:
            if other is tool:
                continue
            overlaps = (
                tool["x"] < other["x"] + other["w"]
                and other["x"] < tool["x"] + tool["w"]
                and tool["y"] < other["y"] + other["h"]
                and other["y"] < tool["y"] + tool["h"]
            )
            assert not overlaps, f"the measure tool covers {other['name']}"

    @pytest.mark.parametrize("scale", [0.85, 1.5, 2.0])
    def test_bigger_controls_do_not_grow_into_it(
        self, page, runtime, tmp_path, scale
    ) -> None:
        """Size scaled each control from its corner with a transform, which
        moves nothing out of the way: unlifted, the zoom controls grew up
        through the measure tool's readout at the largest size. The corner now
        narrows upward, the zoom controls above the measure tool."""
        result = OnlyMapWriter(runtime_provider=runtime).write(
            _project(ExportSettings(chrome_scale=scale)), tmp_path
        )
        page.set_viewport_size({"width": 1200, "height": 900})
        open_map(page, result.entry_path)
        require_webgl(page)
        wait_until_drawn(page)

        tool = page.locator(MEASURE).bounding_box()
        zoom = page.locator('om-widget[type="zoom-controls"]').bounding_box()
        assert tool is not None and zoom is not None
        assert zoom["y"] + zoom["height"] <= tool["y"], (
            f"at size {scale} the zoom controls reach into the measure tool"
        )

    def test_switched_off_it_is_not_on_the_page(self, page, runtime, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=runtime).write(
            _project(ExportSettings(show_measure=False)), tmp_path
        )
        open_map(page, result.entry_path)
        require_webgl(page)
        wait_until_drawn(page)

        assert page.locator(MEASURE).count() == 0
        assert page.get_by_text("Measure distance").count() == 0
