"""Live features, exported and loaded by the real runtime from local services.

The fake services (`tests/fake_feature_services.py`) hold twelve stations;
the copy embedded at export holds two. So the map shows which it drew by how
many blue points there are: twelve live, two from the embedded copy, none
when a live-only layer fails. A failed layer that has no copy to show is
named in the page's notice, and the notice goes when the layer loads again.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import functools
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fake_feature_services import (
    COLLECTION,
    STATIONS,
    TYPENAME,
    FakeFeatureServices,
)

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    LiveFeaturesSpec,
    LiveMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.live_features import (
    arcgis_template,
    ogcapi_template,
    wfs_template,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

ARCGIS = "arcgis/rest/services/Stations/FeatureServer/0"
EXTENT = Extent(west=-75.85, south=45.25, east=-75.6, north=45.45)
# The embedded copy: the first two stations.
COPY = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
            "properties": {"name": s["name"], "kind": s["kind"], "depth": s["depth"]},
        }
        for s in STATIONS[:2]
    ],
}


@pytest.fixture(scope="module")
def services():
    with FakeFeatureServices() as running:
        yield running


def _layer(url: str, mode: LiveMode) -> ExportLayer:
    return ExportLayer(
        layer_id="stations",
        name="River stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.SERVICE,
        feature_count=2,
        geojson=COPY
        if mode is LiveMode.LIVE_FIRST
        else {"type": "FeatureCollection", "features": []},
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=20, g=60, b=230), radius=9.0),
        ),
        popup=PopupSpec(fields=(PopupFieldSpec(name="name", alias="Station"),)),
        live=LiveFeaturesSpec(url=url, host="127.0.0.1", service="WFS", mode=mode),
    )


def _export(runtime, directory, layer: ExportLayer, settings=None):
    project = ExportProject(
        title="Rivers",
        layers=(layer,),
        extent=EXTENT,
        settings=settings or ExportSettings(basemap="none"),
    )
    writer = OnlyMapWriter(runtime_provider=runtime)
    return writer.write(project, directory).entry_path


# Blue points on the map's own snapshot, as separate blobs of blue pixels
# counted on a coarse grid (a point is 18px across; points are 50px apart).
COUNT_POINTS = """
async () => {
  const el = document.querySelector('om-map');
  const url = await el.snapshot();
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve; img.onerror = reject; img.src = url;
  });
  const canvas = document.createElement('canvas');
  canvas.width = img.width; canvas.height = img.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const cell = Math.round(20 * img.width / el.getBoundingClientRect().width);
  const cells = new Set();
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const i = (y * canvas.width + x) * 4;
      if (data[i + 2] > 190 && data[i] < 70 && data[i + 1] < 110 && data[i + 3] > 200) {
        cells.add(Math.floor(x / cell) + ',' + Math.floor(y / cell));
      }
    }
  }
  // Join neighbouring cells into blobs.
  const seen = new Set();
  let blobs = 0;
  for (const start of cells) {
    if (seen.has(start)) continue;
    blobs++;
    const stack = [start];
    seen.add(start);
    while (stack.length) {
      const [cx, cy] = stack.pop().split(',').map(Number);
      for (const [dx, dy] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,-1],[1,-1],[-1,1]]) {
        const key = (cx + dx) + ',' + (cy + dy);
        if (cells.has(key) && !seen.has(key)) { seen.add(key); stack.push(key); }
      }
    }
  }
  return blobs;
}
"""

STATE = """
() => {
  const layer = document.querySelector('om-layer#stations');
  const notice = document.querySelector('om-widget[data-q2w-layer-notice]');
  const panel = notice && notice.root && notice.root.querySelector('.q2w-notice');
  return {
    live: layer.getAttribute('data-q2w-live'),
    data: layer.getAttribute('data'),
    notice: panel ? panel.textContent : null,
    errors: window.__layerErrors.length,
  };
}
"""


def _open(page, url: str):
    page.set_viewport_size({"width": 900, "height": 700})
    page.add_init_script(
        "window.__layerErrors = [];"
        "document.addEventListener('om-layer-error',"
        " (e) => window.__layerErrors.push(e.detail), true);"
    )
    page.goto(url)
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.evaluate(
        "async () => { const el = document.querySelector('om-map'); await el.ready;"
        " await el.whenSettled({ timeout: 30000 }); }"
    )


def _wait(page, check, timeout_s: float = 20.0):
    """`check(page)` once it holds, polled and bounded."""
    deadline = time.monotonic() + timeout_s
    value = check(page)
    while not value and time.monotonic() < deadline:
        page.wait_for_timeout(250)
        value = check(page)
    return value


def _points(page, expected: int) -> int:
    counted = [0]

    def check(page):
        counted[0] = page.evaluate(COUNT_POINTS)
        return counted[0] == expected

    _wait(page, check)
    return counted[0]


def _state(page) -> dict:
    return page.evaluate(STATE)


def _drag(page, dx: float) -> None:
    """Drag the map sideways by `dx` pixels, as a reader would: pressed in
    the middle, moved in small steps, released."""
    x, y = page.evaluate(
        "() => { const r = document.querySelector('om-map')"
        ".getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }"
    )
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + 10, steps=25)
    page.mouse.up()
    page.evaluate(
        "async () => { await document.querySelector('om-map')"
        ".whenSettled({ timeout: 30000 }); }"
    )


def _requests(services, path: str, since: int) -> list[dict]:
    return [r for r in services.requests[since:] if r["path"] == path]


class TestLiveFirst:
    def test_wfs_loads_the_view_from_a_file_and_again_after_a_pan(
        self, page, runtime, services, tmp_path
    ) -> None:
        before = len(services.requests)
        url = wfs_template(services.url("cors/wfs"), TYPENAME)
        entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_FIRST))
        _open(page, entry.as_uri())

        assert _points(page, 12) == 12
        asked = _requests(services, "/cors/wfs", before)
        assert asked, "the map asked the WFS for nothing"
        params = asked[0]["params"]
        assert params["TYPENAMES"] == TYPENAME and params["COUNT"] == "1000"
        # Latitude first, with its CRS, and around the opening view.
        south, west, north, east = (float(v) for v in params["BBOX"].split(",")[:4])
        assert params["BBOX"].endswith(",urn:ogc:def:crs:EPSG::4326")
        assert south < 45.35 < north and west < -75.7 < east
        # A page opened from a file asks with Origin "null".
        assert asked[0]["origin"] == "null"
        assert _state(page) == {
            "live": "live-first",
            "data": url,
            "notice": None,
            "errors": 0,
        }

        seen = len(services.requests)
        # Dragged left, the view moves east, past the area already loaded.
        _drag(page, -700)

        def asked_further_east(page):
            for request in _requests(services, "/cors/wfs", seen):
                box = [float(v) for v in request["params"]["BBOX"].split(",")[:4]]
                if box[3] > east + 0.05:
                    return True
            return False

        assert _wait(page, asked_further_east), "the pan asked the WFS nothing new"

    @pytest.mark.parametrize("service", ["ogc", "arcgis"])
    def test_ogcapi_and_arcgis_page_to_every_feature(
        self, page, runtime, services, tmp_path, service: str
    ) -> None:
        """Pages of five: the runtime follows OGC API's next links and
        ArcGIS's resultOffset to all twelve."""
        before = len(services.requests)
        if service == "ogc":
            url = ogcapi_template(services.url("cors/ogc"), COLLECTION)
            url, path = url.replace("limit=1000", "limit=5"), "/cors/ogc"
            path += f"/collections/{COLLECTION}/items"
        else:
            url = arcgis_template(services.url(f"cors/{ARCGIS}"), ["name"])
            url = url.replace("resultRecordCount=1000", "resultRecordCount=5")
            path = f"/cors/{ARCGIS}/query"
        entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_FIRST))
        _open(page, entry.as_uri())

        assert _points(page, 12) == 12
        asked = _requests(services, path, before)
        pages = {
            r["params"].get("offset") or r["params"].get("resultOffset") for r in asked
        }
        assert {"5", "10"} <= pages
        assert _state(page)["live"] == "live-first"

    def test_served_over_http_too(self, page, runtime, services, tmp_path) -> None:
        url = wfs_template(services.url("cors/wfs"), TYPENAME)
        entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_FIRST))
        handler = functools.partial(SimpleHTTPRequestHandler, directory=entry.parent)
        handler.log_message = lambda *args: None  # type: ignore[assignment]
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _open(page, f"http://127.0.0.1:{server.server_port}/{entry.name}")
            assert _points(page, 12) == 12
            assert _state(page)["notice"] is None
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize("failure", ["status 500", "no CORS"])
    def test_a_failing_service_switches_to_the_copy_quietly(
        self, page, runtime, services, tmp_path, failure: str
    ) -> None:
        if failure == "status 500":
            services.failing = True
            url = wfs_template(services.url("cors/wfs"), TYPENAME)
        else:
            url = wfs_template(services.url("nocors/wfs"), TYPENAME)
        try:
            entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_FIRST))
            _open(page, entry.as_uri())
            assert _wait(page, lambda p: _state(p)["live"] == "fell-back")
            assert _points(page, 2) == 2
            state = _state(page)
            assert state["data"] is None and state["notice"] is None
            assert state["errors"] >= 1
        finally:
            services.failing = False


class TestNotice:
    def test_live_only_failing_names_the_layer_and_withdraws_on_recovery(
        self, page, runtime, services, tmp_path
    ) -> None:
        services.failing = True
        try:
            url = wfs_template(services.url("cors/wfs"), TYPENAME)
            entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_ONLY))
            _open(page, entry.as_uri())
            assert _wait(page, lambda p: _state(p)["notice"] is not None)
            assert _state(page)["notice"] == "Could not load: River stations"
            assert _points(page, 0) == 0
            page.screenshot(path=str(tmp_path / "notice.png"))
        finally:
            services.failing = False

        # The service is back; the next view loads, and the notice goes.
        _drag(page, 120)
        assert _wait(page, lambda p: _state(p)["notice"] is None, 30.0)
        _drag(page, -120)
        assert _points(page, 12) == 12
        assert _state(page)["live"] == "live-only"

    @pytest.mark.parametrize("width", [360, 640, 1000])
    def test_the_notice_covers_no_control_and_scrolls_nothing(
        self, page, runtime, services, tmp_path, width: int
    ) -> None:
        url = wfs_template(services.url("nocors/wfs"), TYPENAME)
        settings = ExportSettings(basemap="none", chrome_scale=1.5)
        entry = _export(runtime, tmp_path, _layer(url, LiveMode.LIVE_ONLY), settings)
        _open(page, entry.as_uri())
        page.set_viewport_size({"width": width, "height": 640})
        assert _wait(page, lambda p: _state(p)["notice"] is not None)
        page.wait_for_timeout(500)
        page.screenshot(path=str(tmp_path / f"notice-{width}.png"))
        layout = page.evaluate(
            """() => {
              const notice = document.querySelector('om-widget[data-q2w-layer-notice]');
              const box = notice.getBoundingClientRect();
              const others = [...document.querySelectorAll(
                '[data-om-widget-slot] > *, [data-om-fold-toggle], ' +
                '.om-credit, .om-caption'
              )].filter((el) => el !== notice && !el.contains(notice))
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.width > 0 && r.height > 0);
              const overlaps = others.filter((r) =>
                r.left < box.right && box.left < r.right &&
                r.top < box.bottom && box.top < r.bottom).length;
              const page = document.scrollingElement;
              return {
                overlaps,
                inside: box.left >= 0 && box.right <= innerWidth && box.top >= 0
                  && box.bottom <= innerHeight,
                scrolls: page.scrollHeight > innerHeight
                  || page.scrollWidth > innerWidth,
              };
            }"""
        )
        assert layout == {"overlaps": 0, "inside": True, "scrolls": False}
