"""A click on a WMS layer in an exported map, in a real browser: what the
page asks the service, and what the popup shows of its answer.

Two halves. Reading an answer needs no map, so every reply recorded from a
real government service (`tests/fixtures/feature_info/`, see the README
there) is read in a blank page by the page's own reader, cut from
`templates/feature-info.html`: in every browser, WebGL or not. Everything
else is a click on an exported map whose WMS layers are on the local fake
service (`tests/fake_map_services.py`), which logs each request and answers
a click with the reply a test sets: where the click is asked, which layers
are asked, what the popup shows, and what the reader is told when the
service refuses web pages.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fake_map_services import TARGET_3857, FakeMapServices

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
    TileSpec,
)
from nika_onlymap_exporter.core.map_services import (
    WMS_TILE_SIZE,
    wms_feature_info_template,
    wms_get_map_template,
)
from nika_onlymap_exporter.writers.onlymap_writer import (
    FEATURE_INFO_PATH,
    OnlyMapWriter,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "feature_info"
EARTH_RADIUS = 6378137.0


def _lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2)
    return lon, lat


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    return (
        math.radians(lon) * EARTH_RADIUS,
        EARTH_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
    )


WEST, SOUTH = _lonlat(TARGET_3857[0], TARGET_3857[1])
EAST, NORTH = _lonlat(TARGET_3857[2], TARGET_3857[3])
CLICK = ((WEST + EAST) / 2, (SOUTH + NORTH) / 2)
# A point with a click popup of its own, away from the click.
STATION = (WEST + (EAST - WEST) * 0.2, SOUTH + (NORTH - SOUTH) * 0.2)


# --------------------------------------------------------------------------
# Reading an answer
# --------------------------------------------------------------------------


def _reader_source() -> str:
    text = FEATURE_INFO_PATH.read_text(encoding="utf-8")
    start = text.index("// reader:start")
    end = text.index("// reader:end")
    return text[start:end]


READ = """
([source, body, type]) => {
  const readAnswer = new Function(source + "\\nreturn readAnswer;")();
  return readAnswer(body, type);
}
"""

# Each recorded reply, the Content-Type it came with (the format asked for,
# where the headers were not kept), what it reads as, and fields it must and
# must not show. Geometry, GML's own elements and empty fields are never
# fields.
ANSWERS = [
    ("geoserver-dwd-hit.json", "application/json;charset=UTF-8", "features",
     {"NAME": "Stadt Magdeburg", "WARNCELLID": "115003000"}, ("ZUSATZ",)),
    ("geoserver-dwd-hit-gml3.xml", "application/vnd.ogc.gml/3.1.1", "features",
     {"NAME": "Stadt Magdeburg", "MAX_HEIGHT": "130"}, ("SHAPE", "boundedBy")),
    ("geoserver-dwd-empty.json", "application/json;charset=UTF-8", "empty", {}, ()),
    ("geoserver-dwd-empty.html", "text/html", "empty", {}, ()),
    ("geoserver-dwd-exception.xml", "text/xml;charset=UTF-8", "exception", {}, ()),
    ("mapserver-pdok-hit.json", "application/json", "features",
     {"naam": "Amsterdam", "code": "0363"}, ()),
    ("mapserver-pdok-hit-gml.xml", "application/vnd.ogc.gml", "features",
     {"naam": "Amsterdam", "identificatie": "GM0363"}, ("geom", "name")),
    ("mapserver-pdok-empty.json", "application/json", "empty", {}, ()),
    ("mapserver-pdok-empty.txt", "text/plain", "text", {}, ()),
    ("arcgis-usgs-hit.geojson", "application/geo+json", "features",
     {"COUNTY_NAME": "Denver", "STATE_NAME": "Colorado"}, ()),
    ("arcgis-usgs-hit.xml", "text/xml", "features",
     {"COUNTY_NAME": "Denver", "POPULATION": "715522"}, ()),
    ("arcgis-usgs-hit.html", "text/html", "html", {}, ()),
    ("arcgis-usgs-empty.geojson", "application/geo+json", "empty", {}, ()),
    ("arcgis-usgs-empty.html", "text/html", "empty", {}, ()),
    ("arcgis-usgs-exception.xml", "application/vnd.ogc.se_xml", "exception", {}, ()),
    ("qgis-solothurn-hit.xml", "text/xml; charset=utf-8", "features",
     {"Gemeinde": "Solothurn", "BFS-Nr": "2601"}, ("geometry",)),
    ("qgis-solothurn-empty.xml", "text/xml; charset=utf-8", "empty", {}, ()),
]  # fmt: skip


class TestReadingAnswers:
    @pytest.mark.parametrize(
        ("name", "content_type", "kind", "fields", "absent"),
        ANSWERS,
        ids=[answer[0] for answer in ANSWERS],
    )
    def test_a_real_answer_reads_as_what_it_says(
        self, page, name, content_type, kind, fields, absent
    ) -> None:
        """A hit lists its features' fields; a structured answer that found
        nothing, an HTML page with nothing to read and an exception show
        nothing; plain text is shown as written, whatever it says."""
        page.set_content("<!doctype html><title>reader</title>")
        body = (FIXTURES / name).read_text(encoding="utf-8")
        read = page.evaluate(READ, [_reader_source(), body, content_type])

        assert read["kind"] == kind
        if kind == "features":
            (rows,) = read["features"]
            shown = dict(rows)
            assert shown == {**shown, **fields}
            assert not set(absent) & set(shown)
            assert all(value != "" for value in shown.values())
        if kind == "exception":
            assert read["message"]
        if kind == "text":
            assert "Search returned no results." in read["text"]
        if kind == "html":
            assert "Denver" in read["html"]

    def test_values_are_written_as_text(self, page) -> None:
        page.set_content("<!doctype html><title>reader</title>")
        body = '{"type":"Feature","properties":{"a":"{{b}}","n":3,"o":{"x":1}}}'
        read = page.evaluate(READ, [_reader_source(), body, "application/json"])
        assert read == {
            "kind": "features",
            "features": [[["a", "{{b}}"], ["n", "3"], ["o", '{"x":1}']]],
        }


# --------------------------------------------------------------------------
# Clicking the map
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def services():
    with FakeMapServices() as running:
        yield running


def _wms_layer(
    services,
    layer_id: str,
    name: str,
    *,
    path: str = "cors/wms",
    version: str = "1.3.0",
    crs: str = "EPSG:3857",
    query: str = "target",
    visible_zoom_range: tuple[float, float] | None = None,
) -> ExportLayer:
    url = services.url(path)
    get_map = wms_get_map_template(
        url, version, ["target"], [""], "image/png", "EPSG:3857"
    )
    feature_info = wms_feature_info_template(
        url, version, ["target"], [""], "image/png", crs, [query], "application/json"
    )
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        renderer=RendererSpec(kind=RendererKind.UNSUPPORTED),
        tiles=TileSpec(get_map, 0, 19, WMS_TILE_SIZE, feature_info=feature_info),
        visible_zoom_range=visible_zoom_range,
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
            symbol=SymbolSpec(fill_color=Color(r=20, g=60, b=230), radius=14.0),
        ),
        popup=PopupSpec(fields=(PopupFieldSpec(name="name", alias="Station"),)),
    )


def _export(runtime, directory: Path, *layers: ExportLayer, preview: bool = False):
    """Bottom layer first, as a project lists them for the writer. `preview`
    writes it as the dialog's Preview does, its runtime uncompressed (the
    Preview's own hook needs the QGIS tier, and does nothing to a click)."""
    project = ExportProject(
        title="Clicks",
        layers=layers,
        extent=Extent(
            west=WEST - 0.1, south=SOUTH - 0.1, east=EAST + 0.1, north=NORTH + 0.1
        ),
        settings=ExportSettings(basemap="none"),
    )
    writer = OnlyMapWriter(runtime_provider=runtime)
    return writer.write(project, directory, compress=not preview).entry_path


@pytest.fixture
def served():
    """Serves an export's folder over http, as the Preview does."""
    from nika_onlymap_exporter.ui.live_server import PreviewServer

    servers: list[PreviewServer] = []

    def serve(entry: Path) -> str:
        server = PreviewServer(entry.parent)
        server.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.port}/{entry.name}"

    yield serve
    for server in servers:
        server.stop()


def _open(page, entry: Path, url: str | None = None) -> None:
    page.set_viewport_size({"width": 900, "height": 700})
    page.add_init_script(
        "window.__clicks = [];"
        "document.addEventListener('q2w-feature-info',"
        " (e) => window.__clicks.push(e.detail), true);"
    )
    page.goto(url or entry.as_uri())
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    if not page.evaluate(
        "() => !!document.createElement('canvas').getContext('webgl2')"
    ):
        pytest.skip("no WebGL2 context in this browser/environment")
    page.evaluate(
        """async () => {
          const map = document.querySelector('om-map');
          await map.ready;
          const settled = await map.whenSettled({ timeout: 30000 });
          if (!settled.settled) throw new Error('the map did not settle');
        }"""
    )


def _click(page, lon_lat: tuple[float, float]) -> int:
    """Click there the way a hand does; the number of clicks answered before."""
    answered = page.evaluate("() => window.__clicks.length")
    box = page.locator("om-map").bounding_box()
    assert box is not None
    x, y = page.evaluate(
        "(c) => document.querySelector('om-map').project(c)", list(lon_lat)
    )
    page.mouse.move(box["x"] + x, box["y"] + y, steps=10)
    page.wait_for_timeout(100)
    page.mouse.click(box["x"] + x, box["y"] + y)
    return answered


def _answered(page, before: int) -> dict:
    """The answers to the click after `before`, once they are all in."""
    page.wait_for_function(
        "(n) => window.__clicks.length > n", arg=before, timeout=20_000
    )
    return page.evaluate("(n) => window.__clicks[n]", before)


POPUP = """() => {
  const popup = document.getElementById('q2w-feature-info-popup');
  const root = popup && popup.shadowRoot;
  if (!root) return null;
  const sections = [...root.querySelectorAll('.q2w-info-layer')];
  return {
    visible: getComputedStyle(popup).visibility !== 'hidden',
    layers: sections.map((section) => ({
      title: section.querySelector('.om-popup-title').textContent,
      rows: [...section.querySelectorAll('.om-popup-row')].map((row) => [
        row.querySelector('.om-popup-label').textContent,
        row.querySelector('.om-popup-value').textContent,
      ]),
      text: (section.querySelector('.q2w-info-text') || {}).textContent || null,
      frame: (() => {
        const frame = section.querySelector('iframe');
        if (!frame) return null;
        const sandbox = frame.getAttribute('sandbox');
        return { sandbox, srcdoc: frame.getAttribute('srcdoc') };
      })(),
    })),
  };
}"""


def _feature_info_requests(services, since: int) -> list[dict[str, str]]:
    return [
        request["params"]
        for request in services.requests[since:]
        if request["params"].get("REQUEST") == "GetFeatureInfo"
    ]


def _clicked_point(params: dict[str, str]) -> tuple[float, float]:
    """Where in its CRS a GetFeatureInfo says it was clicked: the middle of
    pixel I, J (X, Y) of its BBOX, in the axis order its version names."""
    bbox = [float(v) for v in params["BBOX"].split(",")]
    legacy = params["VERSION"] == "1.1.1"
    crs = params["SRS" if legacy else "CRS"]
    if crs == "EPSG:4326" and not legacy:
        bbox = [bbox[1], bbox[0], bbox[3], bbox[2]]
    column = int(params["X" if legacy else "I"]) + 0.5
    row = int(params["Y" if legacy else "J"]) + 0.5
    width, height = int(params["WIDTH"]), int(params["HEIGHT"])
    return (
        bbox[0] + (bbox[2] - bbox[0]) * column / width,
        bbox[3] - (bbox[3] - bbox[1]) * row / height,
    )


class TestClicks:
    @pytest.mark.parametrize("how", ["file", "http", "preview"])
    def test_each_layer_is_asked_on_its_own_where_it_was_clicked(
        self, page, runtime, services, tmp_path, served, how
    ) -> None:
        """One request per layer, at the clicked pixel of a small map at the
        map's scale: Web Mercator metres, latitude first for EPSG:4326 in WMS
        1.3.0, longitude first in 1.1.1 and for CRS:84. The answers share one
        popup, the top layer first; the field the service left empty is not
        shown, and one that reads like a popup template is shown as written."""
        layers = [
            _wms_layer(services, "mercator", "Mercator 1.3.0"),
            _wms_layer(services, "lat_first", "EPSG:4326 1.3.0", crs="EPSG:4326"),
            _wms_layer(
                services,
                "legacy",
                "EPSG:4326 1.1.1",
                path="cors/wms111",
                version="1.1.1",
                crs="EPSG:4326",
            ),
            _wms_layer(services, "crs84", "CRS:84 1.3.0", crs="CRS:84"),
        ]
        entry = _export(runtime, tmp_path, *layers, preview=how == "preview")
        _open(page, entry, served(entry) if how != "file" else None)
        before = len(services.requests)
        answers = _answered(page, _click(page, CLICK))

        assert [a["status"] for a in answers["answers"]] == ["features"] * 4
        asked = _feature_info_requests(services, before)
        assert len(asked) == 4
        # One pixel of the small map, in each CRS.
        metres_per_pixel = page.evaluate(
            """() => {
              const map = document.querySelector('om-map');
              const box = map.getBoundingClientRect();
              const a = map.unproject([box.width / 2, box.height / 2]);
              const b = map.unproject([box.width / 2 + 1, box.height / 2]);
              return (b[0] - a[0]) * Math.PI / 180 * 6378137;
            }"""
        )
        clicked = answers["coordinate"]
        for params in asked:
            assert params["QUERY_LAYERS"] == "target"
            assert params["WIDTH"] == params["HEIGHT"] == "101"
            point = _clicked_point(params)
            legacy = params["VERSION"] == "1.1.1"
            crs = params["SRS" if legacy else "CRS"]
            if crs == "EPSG:3857":
                expected = _mercator(*clicked)
                tolerance = metres_per_pixel * 0.6
            else:
                expected = tuple(clicked)
                tolerance = math.degrees(metres_per_pixel / EARTH_RADIUS) * 0.6
            assert point == pytest.approx(expected, abs=tolerance), params
        versions = sorted(
            (p["VERSION"], p.get("CRS") or p.get("SRS") or "") for p in asked
        )
        assert versions == [
            ("1.1.1", "EPSG:4326"),
            ("1.3.0", "CRS:84"),
            ("1.3.0", "EPSG:3857"),
            ("1.3.0", "EPSG:4326"),
        ]
        # And the click was the spot asked for.
        assert clicked == pytest.approx(CLICK, abs=1e-3)

        popup = page.evaluate(POPUP)
        assert popup is not None and popup["visible"]
        assert [layer["title"] for layer in popup["layers"]] == [
            "CRS:84 1.3.0",
            "EPSG:4326 1.1.1",
            "EPSG:4326 1.3.0",
            "Mercator 1.3.0",
        ]
        for layer in popup["layers"]:
            assert layer["rows"] == [["name", "Found in target"], ["note", "{{name}}"]]

    @pytest.mark.parametrize(
        ("reply", "content_type", "shows"),
        [
            ("geoserver-dwd-hit.json", "application/json;charset=UTF-8", "rows"),
            ("geoserver-dwd-empty.json", "application/json;charset=UTF-8", None),
            ("geoserver-dwd-exception.xml", "text/xml;charset=UTF-8", None),
            ("mapserver-pdok-empty.txt", "text/plain", "text"),
            ("arcgis-usgs-hit.html", "text/html", "page"),
        ],
    )
    def test_the_popup_shows_what_the_service_says(
        self, page, runtime, services, tmp_path, reply, content_type, shows
    ) -> None:
        """A hit's fields as feature popup rows; nothing at all for an empty
        answer or an exception; plain text as written; a page in a frame that
        runs none of its scripts and reaches nothing of the map's page."""
        services.set_feature_info(
            "reply", (FIXTURES / reply).read_bytes(), content_type
        )
        try:
            layer = _wms_layer(services, "wms", "Districts", query="reply")
            _open(page, _export(runtime, tmp_path, layer))
            answers = _answered(page, _click(page, CLICK))
        finally:
            services.set_feature_info("reply", None)

        (answer,) = answers["answers"]
        popup = page.evaluate(POPUP)
        if shows is None:
            assert answer["status"] in ("empty", "exception")
            assert popup is None
            return
        assert popup is not None and popup["visible"]
        (section,) = popup["layers"]
        assert section["title"] == "Districts"
        if shows == "rows":
            rows = dict(section["rows"])
            assert rows["NAME"] == "Stadt Magdeburg"
            assert "ZUSATZ" not in rows
        elif shows == "text":
            assert "Search returned no results." in section["text"]
        else:
            assert section["frame"]["sandbox"] == ""
            assert "Denver" in section["frame"]["srcdoc"]

    def test_a_page_the_service_writes_runs_none_of_its_scripts(
        self, page, runtime, services, tmp_path
    ) -> None:
        page_html = (
            b"<html><body><p>Parcel 12</p><script>"
            b"try { parent.document.title = 'ran'; } catch (e) {}"
            b"document.body.setAttribute('data-ran', 'yes');"
            b"</script></body></html>"
        )
        services.set_feature_info("page", page_html, "text/html")
        try:
            layer = _wms_layer(services, "wms", "Parcels", query="page")
            _open(page, _export(runtime, tmp_path, layer))
            _answered(page, _click(page, CLICK))
        finally:
            services.set_feature_info("page", None)

        frame = page.frame_locator("#q2w-feature-info-popup >> iframe")
        assert frame.locator("p").text_content(timeout=10_000) == "Parcel 12"
        page.wait_for_timeout(300)
        assert frame.locator("body").get_attribute("data-ran", timeout=5_000) is None
        assert page.title() == "Clicks"

    def test_a_service_that_refuses_web_pages_is_named_once(
        self, page, runtime, services, tmp_path
    ) -> None:
        """No CORS: the click gets nothing, and the layer notice names the
        layer once however often it is clicked."""
        layer = _wms_layer(services, "wms", "Closed WMS", path="nocors/wms")
        _open(page, _export(runtime, tmp_path, layer))
        for _ in range(2):
            (answer,) = _answered(page, _click(page, CLICK))["answers"]
            assert answer["status"] == "failed"
        assert page.evaluate(POPUP) is None
        lines = page.evaluate(
            """() => {
              const notice = document.querySelector(
                'om-widget[data-q2w-layer-notice]');
              const root = notice && notice.root;
              const panel = root && root.querySelector('.q2w-notice');
              return panel ? [...panel.children].map((row) => row.textContent) : null;
            }"""
        )
        assert lines == ["Could not get details from: Closed WMS"]

    def test_a_click_a_feature_popup_opens_for_asks_no_service(
        self, page, runtime, services, tmp_path
    ) -> None:
        layer = _wms_layer(services, "wms", "Districts")
        _open(page, _export(runtime, tmp_path, layer, _station_layer()))
        before = len(services.requests)
        _click(page, STATION)
        page.wait_for_function(
            """() => [...document.querySelectorAll('om-overlay')].some(
                 (o) => o.id === 'stations-popup'
                   && getComputedStyle(o).visibility !== 'hidden')""",
            timeout=10_000,
        )
        page.wait_for_timeout(500)
        assert _feature_info_requests(services, before) == []
        assert page.evaluate(POPUP) is None
        # Away from the station the service is asked, and the station's popup
        # closes as on any click on nothing.
        (answer,) = _answered(page, _click(page, CLICK))["answers"]
        assert answer["status"] == "features"
        assert len(_feature_info_requests(services, before)) == 1

    def test_a_click_the_measure_tool_takes_asks_nothing(
        self, page, runtime, services, tmp_path
    ) -> None:
        """A distance's two points are a measurement; once it is measured the
        tool lets go of the map, and the next click asks the service."""
        layer = _wms_layer(services, "wms", "Districts")
        _open(page, _export(runtime, tmp_path, layer))
        page.locator('om-widget[type="measure"] button[data-mode="distance"]').click(
            timeout=10_000
        )
        before = len(services.requests)
        _click(page, CLICK)
        page.wait_for_timeout(400)
        _click(page, STATION)
        page.wait_for_timeout(800)
        assert _feature_info_requests(services, before) == []
        (answer,) = _answered(page, _click(page, CLICK))["answers"]
        assert answer["status"] == "features"

    @pytest.mark.parametrize("hidden", ["switched-off", "zoomed-out"])
    def test_a_layer_that_is_not_showing_is_not_asked(
        self, page, runtime, services, tmp_path, hidden
    ) -> None:
        shown = _wms_layer(services, "shown", "Shown")
        other = _wms_layer(
            services,
            "other",
            "Not showing",
            query="target,other",
            visible_zoom_range=(17.0, 20.0) if hidden == "zoomed-out" else None,
        )
        _open(page, _export(runtime, tmp_path, shown, other))
        if hidden == "switched-off":
            page.evaluate(
                "() => document.querySelector('om-map').setLayerVisible('other', false)"
            )
        before = len(services.requests)
        answers = _answered(page, _click(page, CLICK))

        assert [a["layerId"] for a in answers["answers"]] == ["shown"]
        assert [
            p["QUERY_LAYERS"] for p in _feature_info_requests(services, before)
        ] == ["target"]
