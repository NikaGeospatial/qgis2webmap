"""A WMS layer, exported and drawn by the real runtime from a local fake WMS.

The fake service (`tests/fake_map_services.py`) draws one crimson rectangle,
given in Web Mercator metres, into whatever bbox it is asked for. So a tile
shows the rectangle in the right place only when the bbox the map asked for
is that tile's own extent - which these tests check twice: in the request log,
against the tile's Web Mercator bounds, and on screen, where the crimson must
cover exactly the rectangle's projected corners. A service sending no CORS
header must leave only its own layer empty and say why.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from fake_map_services import (
    HALF_WORLD,
    TARGET_3857,
    FakeMapServices,
    tile_bounds_3857,
)

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    LegendImage,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
    TileSpec,
)
from nika_onlymap_exporter.core.map_services import (
    WMS_TILE_SIZE,
    parse_wms_capabilities,
    wms_get_map_template,
    wms_legend_urls,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

EARTH_RADIUS = 6378137.0
# Issue #2's project CRS, for the projected-map case.
UTM32 = "EPSG:25832"
UTM32_DEF = "+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs +type=crs"


def lonlat(x: float, y: float) -> tuple[float, float]:
    """Web Mercator metres to longitude/latitude degrees."""
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2)
    return lon, lat


WEST, SOUTH = lonlat(TARGET_3857[0], TARGET_3857[1])
EAST, NORTH = lonlat(TARGET_3857[2], TARGET_3857[3])
# A blue point well outside the rectangle: "the rest of the map draws".
STATION = (WEST - 0.08, SOUTH - 0.05)


@pytest.fixture(scope="module")
def services():
    with FakeMapServices() as running:
        yield running


def _wms_layer(url: str, layers=("target",), time: str | None = None) -> ExportLayer:
    template = wms_get_map_template(
        url,
        "1.3.0",
        list(layers),
        [""] * len(layers),
        "image/png",
        "EPSG:3857",
        time=time,
    )
    return ExportLayer(
        layer_id="wms",
        name="Fake WMS",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        renderer=RendererSpec(kind=RendererKind.UNSUPPORTED),
        tiles=TileSpec(template, 0, 19, WMS_TILE_SIZE),
    )


def _station_layer() -> ExportLayer:
    return ExportLayer(
        layer_id="stations",
        name="Stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(STATION)},
                    "properties": {"name": "station"},
                }
            ],
        },
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=20, g=60, b=230), radius=12.0),
        ),
    )


def _export(runtime, directory, layer: ExportLayer, settings=None):
    project = ExportProject(
        title="WMS",
        layers=(layer, _station_layer()),
        extent=Extent(
            west=WEST - 0.15, south=SOUTH - 0.1, east=EAST + 0.1, north=NORTH + 0.1
        ),
        settings=settings or ExportSettings(basemap="none"),
    )
    return OnlyMapWriter(runtime_provider=runtime).write(project, directory).entry_path


SETTLE = """
async () => {
  const el = document.querySelector('om-map');
  await el.ready;
  const settled = await el.whenSettled({ timeout: 30000 });
  if (!settled.settled) throw new Error('the map did not settle');
}
"""

MEASURE = """
async ([west, south, east, north]) => {
  const el = document.querySelector('om-map');
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
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const box = el.getBoundingClientRect();
  const ratio = img.width / box.width;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let crimson = 0, blue = 0;
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const i = (y * canvas.width + x) * 4;
      const [r, g, b, a] = [data[i], data[i + 1], data[i + 2], data[i + 3]];
      if (r > 190 && g < 60 && b > 30 && b < 100 && a > 200) {
        crimson++;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
      if (b > 190 && r < 70 && g < 110 && a > 200) blue++;
    }
  }
  // Where the rectangle's outline lands, from the map's own projection:
  // its corners and edge midpoints (a projected map bends the edges).
  const outline = [];
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    const lon = west + (east - west) * t, lat = south + (north - south) * t;
    outline.push([lon, south], [lon, north], [west, lat], [east, lat]);
  }
  const points = outline.map((p) => el.project(p));
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  return {
    crimson,
    blue,
    found: crimson
      ? [minX / ratio, minY / ratio, (maxX + 1) / ratio, (maxY + 1) / ratio]
      : null,
    expected: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
  };
}
"""


def _checks(page):
    return page.evaluate("() => window.__omChecks")


def _open(page, entry):
    page.set_viewport_size({"width": 900, "height": 700})
    page.add_init_script(
        "window.__omChecks = [];"
        "document.addEventListener('om-validation-error',"
        " (e) => window.__omChecks.push(e.detail), true);"
    )
    page.goto(entry.as_uri())
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.evaluate(SETTLE)
    return _measure(page)


def _measure(page):
    return page.evaluate(MEASURE, [WEST, SOUTH, EAST, NORTH])


def _measure_drawn(page, timeout_s: float = 20.0):
    """Measure once the service's tiles have drawn.

    `whenSettled` resolves before the runtime has drawn tiles it fetches
    through its own template loader (the bbox and quadkey kind) on a Web
    Mercator map, so the first snapshot can still be empty; the tiles arrive
    a moment later. Polled rather than slept, and bounded.
    """
    import time

    deadline = time.monotonic() + timeout_s
    measured = _measure(page)
    while measured["crimson"] == 0 and time.monotonic() < deadline:
        page.wait_for_timeout(250)
        measured = _measure(page)
    return measured


def _tile_errors(page) -> list[dict]:
    return [
        error
        for detail in _checks(page)
        for error in detail.get("errors", [])
        if error.get("element") == "om-layer#wms"
    ]


def _tile_of(bbox: list[float]) -> tuple[int, int, int]:
    """The (x, y, z) whose Web Mercator bounds `bbox` must be."""
    size = bbox[2] - bbox[0]
    z = round(math.log2(2 * HALF_WORLD / size))
    x = round((bbox[0] + HALF_WORLD) / size)
    y = round((HALF_WORLD - bbox[3]) / size)
    return x, y, z


def _get_maps(services, path: str) -> list[dict[str, str]]:
    return [
        request["params"]
        for request in services.requests
        if request["path"] == path and request["params"].get("REQUEST") == "GetMap"
    ]


class TestWmsTiles:
    def test_each_request_is_one_web_mercator_tile(
        self, page, runtime, services, tmp_path
    ) -> None:
        before = len(services.requests)
        entry = _export(runtime, tmp_path, _wms_layer(services.url("cors/wms")))
        _open(page, entry)
        measured = _measure_drawn(page)

        requests = [r for r in _get_maps(services, "/cors/wms")]
        assert len(services.requests) > before
        assert requests, "the map asked the WMS for nothing"
        for params in requests:
            assert params["CRS"] == "EPSG:3857"
            assert params["WIDTH"] == params["HEIGHT"] == str(WMS_TILE_SIZE)
            bbox = [float(v) for v in params["BBOX"].split(",")]
            x, y, z = _tile_of(bbox)
            assert bbox == pytest.approx(tile_bounds_3857(x, y, z), abs=1e-6)

        assert _tile_errors(page) == []
        assert measured["crimson"] > 0
        # The drawn rectangle covers the rectangle's projected outline, to
        # within a pixel and a half of tile filtering at its edges.
        assert measured["found"] == pytest.approx(measured["expected"], abs=1.5)
        assert measured["blue"] > 0

    def test_on_a_projected_map_it_lands_in_the_plane(
        self, page, runtime, services, tmp_path
    ) -> None:
        """Issue #2's EPSG:25832, as a cartograph's live frame draws it: the
        Web Mercator tiles are warped into the plane."""
        settings = ExportSettings(basemap="none", map_crs=UTM32, map_crs_def=UTM32_DEF)
        entry = _export(
            runtime, tmp_path, _wms_layer(services.url("cors/wms")), settings
        )
        _open(page, entry)
        measured = _measure_drawn(page)

        assert _tile_errors(page) == []
        assert measured["crimson"] > 0
        assert measured["found"] == pytest.approx(measured["expected"], abs=2.5)
        assert measured["blue"] > 0

    def test_a_wms_t_snapshot_asks_every_tile_for_its_time(
        self, page, runtime, services, tmp_path
    ) -> None:
        """A WMS-T layer exported at the time QGIS drew: every tile the map
        asks for carries that TIME, and the layer draws in place."""
        moment = "2020-03-05T00:00:00Z"
        before = len(services.requests)
        layer = _wms_layer(services.url("cors/wms"), time=moment)
        entry = _export(runtime, tmp_path, layer)
        _open(page, entry)
        measured = _measure_drawn(page)

        requests = [
            request["params"]
            for request in services.requests[before:]
            if request["path"] == "/cors/wms"
            and request["params"].get("REQUEST") == "GetMap"
        ]
        assert requests, "the map asked the WMS for nothing"
        assert {params.get("TIME") for params in requests} == {moment}
        assert _tile_errors(page) == []
        assert measured["crimson"] > 0
        assert measured["found"] == pytest.approx(measured["expected"], abs=1.5)


class TestWmsFailures:
    @staticmethod
    def _failure(page) -> str:
        """The layer's one reported cause, once it has been reported. Each
        report repeats the earlier ones, so causes are compared by text."""
        page.wait_for_function(
            "() => window.__omChecks.some((d) => (d.errors || []).some("
            "(e) => e.element === 'om-layer#wms'))",
            timeout=15_000,
        )
        (message,) = {error["message"] for error in _tile_errors(page)}
        return message

    def test_a_service_without_cors_leaves_only_its_layer_empty(
        self, page, runtime, services, tmp_path
    ) -> None:
        entry = _export(runtime, tmp_path, _wms_layer(services.url("nocors/wms")))
        _open(page, entry)

        assert "Access-Control-Allow-Origin" in self._failure(page)
        assert _get_maps(services, "/nocors/wms"), "the map never asked"
        measured = _measure(page)
        assert measured["crimson"] == 0
        assert measured["blue"] > 0

    def test_a_service_exception_is_quoted(
        self, page, runtime, services, tmp_path
    ) -> None:
        layer = _wms_layer(services.url("cors/wms"), layers=("missing",))
        _open(page, _export(runtime, tmp_path, layer))

        assert "Layer 'missing' is not defined" in self._failure(page)
        measured = _measure(page)
        assert measured["crimson"] == 0
        assert measured["blue"] > 0


class TestWmtsTiles:
    def test_google_grid_tiles_draw_in_place(
        self, page, runtime, services, tmp_path
    ) -> None:
        """A WMTS on the GoogleMapsCompatible grid, its template built from the
        service's own capabilities as the reader builds it."""
        from urllib.request import urlopen

        from nika_onlymap_exporter.core.map_services import WmtsTiles, wmts_tiles

        url = services.url("cors/wmts") + "?SERVICE=WMTS&REQUEST=GetCapabilities"
        with urlopen(url) as response:  # the local fake service
            found = wmts_tiles(
                response.read(),
                "target",
                "default",
                "image/png",
                "GoogleMapsCompatible",
                {},
            )
        assert isinstance(found, WmtsTiles)
        layer = replace(
            _wms_layer(services.url("cors/wms")),
            tiles=TileSpec(found.template, found.min_zoom, found.max_zoom, 256),
        )
        _open(page, _export(runtime, tmp_path, layer))
        measured = _measure_drawn(page)

        assert _tile_errors(page) == []
        assert measured["crimson"] > 0
        assert measured["found"] == pytest.approx(measured["expected"], abs=1.5)
        assert any(r["params"].get("REQUEST") == "GetTile" for r in services.requests)


def _legend_layer(services, service="cors/wms", layers=("target",), styles=None):
    """A WMS layer with the legend pictures the reader finds for it, from
    the fake service's own capabilities."""
    from urllib.request import urlopen

    url = services.url(service)
    with urlopen(url + "?SERVICE=WMS&REQUEST=GetCapabilities") as response:
        parsed = parse_wms_capabilities(response.read())  # the local fake service
    assert parsed is not None
    found = wms_legend_urls(
        parsed, list(layers), list(styles or [""] * len(layers)), "image/png"
    )
    layer = _wms_layer(url, layers=layers)
    assert layer.tiles is not None
    legend = tuple(LegendImage(address, title) for title, address in found)
    return replace(layer, tiles=replace(layer.tiles, legend=legend))


def _with_legend(layer: ExportLayer, *urls: str) -> ExportLayer:
    assert layer.tiles is not None
    legend = tuple(LegendImage(url) for url in urls)
    return replace(layer, tiles=replace(layer.tiles, legend=legend))


LEGEND_STATE = """
async () => {
  const widget = [...document.querySelectorAll('om-widget')].find(
    (w) => w.shadowRoot && w.shadowRoot.querySelector('.omni-panel'));
  const root = widget.shadowRoot;
  const images = [...root.querySelectorAll('.omni-legend-graphic img')];
  // Each picture loads or fails by itself; wait for both, bounded.
  await Promise.all(images.map((img) => img.complete ? null : new Promise((done) => {
    img.addEventListener('load', done);
    img.addEventListener('error', done);
    setTimeout(done, 15000);
  })));
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { width: r.width, height: r.height, top: r.top, bottom: r.bottom };
  };
  return {
    rows: [...root.querySelectorAll('.omni-row')].map((row) => ({
      text: row.textContent, ...box(row),
    })),
    fields: [...root.querySelectorAll('.omni-legend-field')].map((f) => f.textContent),
    images: images.map((img) => ({
      natural: [img.naturalWidth, img.naturalHeight],
      shown: box(img),
      graphic: {
        clientHeight: img.parentElement.clientHeight,
        scrollHeight: img.parentElement.scrollHeight,
      },
    })),
    panel: box(root.querySelector('.omni-panel')),
    viewport: [innerWidth, innerHeight],
  };
}
"""


def _legend(page, url: str) -> dict:
    page.set_viewport_size({"width": 900, "height": 700})
    page.goto(url)
    page.wait_for_function(
        "() => [...document.querySelectorAll('om-widget')].some("
        "(w) => w.shadowRoot && w.shadowRoot.querySelector('.omni-panel'))",
        timeout=30_000,
    )
    return page.evaluate(LEGEND_STATE)


def _legend_requests(services, before: int) -> list[dict]:
    return [
        request
        for request in services.requests[before:]
        if request["params"].get("REQUEST") == "GetLegendGraphic"
        or request["path"].endswith("/legends/outlined.png")
    ]


class TestWmsLegend:
    """The service's legend picture, in the exported page's legend."""

    @pytest.mark.parametrize("service", ["cors/wms", "nocors/wms", "cors/wms111"])
    def test_the_picture_loads_from_a_file(
        self, page, runtime, services, tmp_path, service
    ) -> None:
        """Opened from a file, as an exported map usually is. A picture needs
        no CORS, so a service that sends none still shows its legend."""
        from fake_map_services import LEGEND_SIZES

        before = len(services.requests)
        entry = _export(runtime, tmp_path, _legend_layer(services, service))
        state = _legend(page, entry.as_uri())

        (image,) = state["images"]
        assert image["natural"] == list(LEGEND_SIZES["target"])
        assert image["shown"]["width"] == LEGEND_SIZES["target"][0]
        (asked,) = _legend_requests(services, before)
        assert asked["params"]["LAYER"] == "target"
        assert [row["text"] for row in state["rows"]] == ["Fake WMS", "Stations"]
        # The picture sits under its layer's row, inside the panel.
        assert image["shown"]["top"] >= state["rows"][0]["bottom"]
        assert image["shown"]["bottom"] <= state["panel"]["bottom"]

    def test_the_picture_loads_when_the_map_is_served(
        self, page, runtime, services, tmp_path
    ) -> None:
        """Served over http, from the Preview's own server."""
        from fake_map_services import OUTLINED_LEGEND_SIZE

        from nika_onlymap_exporter.ui.live_server import PreviewServer

        layer = _legend_layer(services, styles=("outlined",))
        entry = _export(runtime, tmp_path, layer)
        server = PreviewServer(entry.parent)
        server.start()
        try:
            state = _legend(page, f"http://127.0.0.1:{server.port}/{entry.name}")
        finally:
            server.stop()
        (image,) = state["images"]
        assert image["natural"] == list(OUTLINED_LEGEND_SIZE)

    def test_each_sublayer_has_its_picture_under_its_name(
        self, page, runtime, services, tmp_path
    ) -> None:
        from fake_map_services import LEGEND_SIZES

        layer = _legend_layer(services, layers=("target", "boundary"))
        state = _legend(page, _export(runtime, tmp_path, layer).as_uri())
        assert state["fields"] == ["Target area", "Boundary"]
        assert [image["natural"] for image in state["images"]] == [
            list(LEGEND_SIZES["target"]),
            list(LEGEND_SIZES["boundary"]),
        ]

    def test_a_tall_picture_keeps_its_size_and_scrolls(
        self, page, runtime, services, tmp_path
    ) -> None:
        """`boundary`'s legend is 600 px tall: shown at its own size, so its
        text stays legible, in a box that scrolls rather than a panel that
        runs off the screen."""
        from fake_map_services import LEGEND_SIZES

        layer = _legend_layer(services, layers=("boundary",))
        state = _legend(page, _export(runtime, tmp_path, layer).as_uri())
        (image,) = state["images"]
        assert image["natural"] == list(LEGEND_SIZES["boundary"])
        assert image["shown"]["width"] == LEGEND_SIZES["boundary"][0]
        assert image["graphic"]["clientHeight"] <= 240
        assert image["graphic"]["scrollHeight"] >= LEGEND_SIZES["boundary"][1]
        assert state["panel"]["bottom"] <= state["viewport"][1]

    def test_a_sized_legend_sizes_its_pictures(
        self, page, runtime, services, tmp_path
    ) -> None:
        """Given a height, the legend scrolls as a whole: the tall picture is
        no longer cut to its own 240px box, and the legend stays on the map."""
        from fake_map_services import LEGEND_SIZES

        layer = _legend_layer(services, layers=("boundary",))
        settings = ExportSettings(basemap="none", legend_width=200, legend_height=500)
        state = _legend(page, _export(runtime, tmp_path, layer, settings).as_uri())
        (image,) = state["images"]
        assert image["graphic"]["clientHeight"] == LEGEND_SIZES["boundary"][1]
        host = page.evaluate(
            """() => {
                const host = [...document.querySelectorAll('om-widget')].find(
                    (w) => w.shadowRoot && w.shadowRoot.querySelector('.omni-panel'));
                const box = host.getBoundingClientRect();
                return [box.width, box.height, host.scrollHeight > host.clientHeight];
            }"""
        )
        assert host == [200, 500, True]

    def test_a_picture_that_fails_leaves_the_legend_readable(
        self, page, runtime, services, tmp_path
    ) -> None:
        """A service that answers with an error, and one that cannot be
        reached (offline): no picture, no broken-image box, and the rows
        exactly where a legend without pictures puts them."""
        plain = _wms_layer(services.url("cors/wms"))
        reference = _legend(page, _export(runtime, tmp_path / "plain", plain).as_uri())

        failing = _with_legend(
            plain,
            _legend_layer(services)
            .tiles.legend[0]
            .url.replace(  # type: ignore[union-attr]
                "LAYER=target", "LAYER=missing"
            ),
            "http://127.0.0.1:9/legend.png",
        )
        state = _legend(page, _export(runtime, tmp_path / "failing", failing).as_uri())

        assert [image["natural"] for image in state["images"]] == [[0, 0], [0, 0]]
        for image in state["images"]:
            # No height is no broken-image box: nothing shows.
            assert image["shown"]["height"] == 0
            assert image["graphic"]["clientHeight"] == 0
        assert [row["text"] for row in state["rows"]] == ["Fake WMS", "Stations"]
        for row, expected in zip(state["rows"], reference["rows"]):
            assert row["height"] == pytest.approx(expected["height"], abs=0.5)
        assert state["panel"]["height"] == pytest.approx(
            reference["panel"]["height"], abs=8
        )
