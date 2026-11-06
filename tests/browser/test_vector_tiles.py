"""PMTiles archives and publisher-styled vector tiles, drawn by the real runtime.

A layer of a PMTiles archive is exported as an `MVTLayer` on the archive, drawn
by its QGIS colours from its own layer of the archive; the archive travels
inside a single file (answered by the page's range stand-in), beside a folder's
page, or stays on its server. A vector tile service is drawn by its publisher's
style (`vector-style`). Each must draw its colours where its features are -
opened from disk and served over http - and never what it was told to leave
out. The tiles and archives are written by `tests/fake_tile_archive.py` and
served from 127.0.0.1 with CORS and range support (`tests/fake_cog_server.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fake_cog_server import FakeCogServer
from fake_tile_archive import TILE_MVT, Feature, mvt, pmtiles, tiles_covering
from test_exported_map import TELEMETRY_ENDPOINT

from nika_onlymap_exporter.core.cartograph_ir import Box, CartographSpec, FrameSpec
from nika_onlymap_exporter.core.export_ir import (
    AssetDependency,
    AssetDisposition,
    CategorySpec,
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    GraduatedClassSpec,
    OutputMode,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
    ValueRange,
    VectorTileSpec,
)
from nika_onlymap_exporter.core.manifest_builder import build_manifest
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

BOX = (8.0, 47.0, 8.6, 47.3)
LAKE = (8.2, 47.22)
RIVER = (8.52, 47.22)
ROAD = (8.425, 47.15)
EMPTY = (8.2, 47.05)
WATER = [
    Feature(
        "polygon",
        [(8.0, 47.1), (8.4, 47.1), (8.4, 47.3), (8.0, 47.3), (8.0, 47.1)],
        {"kind": "lake", "depth": 10},
    ),
    Feature(
        "polygon",
        [(8.45, 47.1), (8.6, 47.1), (8.6, 47.3), (8.45, 47.3), (8.45, 47.1)],
        {"kind": "river", "depth": 3},
    ),
]
ROADS = [Feature("line", [(7.9, 47.15), (8.7, 47.15)], {"class": "primary"})]
TILES = {
    index: mvt({"water": WATER, "roads": ROADS}, *index)
    for z in range(11)
    for index in tiles_covering(*BOX, z)
}
ARCHIVE = pmtiles(TILES, TILE_MVT, BOX)

# The QGIS colour of the archive's water layer, and the publisher's colours.
WATER_FILL = (230, 60, 20)
STYLE_LAKE = (0, 200, 83)
STYLE_RIVER = (255, 0, 255)
STYLE_ROAD = (0, 0, 255)


def _style(host: FakeCogServer) -> bytes:
    def fill(kind: str, color: tuple[int, int, int]) -> dict[str, object]:
        return {
            "id": kind,
            "type": "fill",
            "source": "base",
            "source-layer": "water",
            "filter": ["==", ["get", "kind"], kind],
            "paint": {"fill-color": "#{:02x}{:02x}{:02x}".format(*color)},
        }

    return json.dumps(
        {
            "version": 8,
            "sources": {
                "base": {"type": "vector", "tiles": [host.url("/t/{z}/{x}/{y}.pbf")]}
            },
            "layers": [
                fill("lake", STYLE_LAKE),
                fill("river", STYLE_RIVER),
                {
                    "id": "roads",
                    "type": "line",
                    "source": "base",
                    "source-layer": "roads",
                    "paint": {"line-color": "#0000ff", "line-width": 8},
                },
            ],
        }
    ).encode()


@pytest.fixture(scope="module")
def tile_host():
    files = {f"/t/{z}/{x}/{y}.pbf": data for (z, x, y), data in TILES.items()}
    files["/lakes.pmtiles"] = ARCHIVE
    with FakeCogServer(files) as running:
        files["/style.json"] = _style(running)
        yield running


def _water_layer(tiles: VectorTileSpec, remote: bool) -> ExportLayer:
    """What `vector_tiles.read_pmtiles_layer` builds for the water layer."""
    return ExportLayer(
        layer_id="water",
        name="Water",
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.SERVICE if remote else SourceKind.FILE,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            # An outline wide enough that the archive's roads, were they
            # drawn by this layer, would show in its colour.
            symbol=SymbolSpec(
                fill_color=Color(*WATER_FILL),
                stroke_color=Color(*WATER_FILL),
                stroke_width=6.0,
            ),
        ),
        popup=PopupSpec(enabled=False),
        vector_tiles=tiles,
        dependencies=(
            AssetDependency(
                identifier=tiles.url,
                disposition=(
                    AssetDisposition.REMOTE if remote else AssetDisposition.EMBEDDABLE
                ),
            ),
        ),
    )


def _project(layer: ExportLayer) -> ExportProject:
    return ExportProject(
        title="Tiles",
        layers=(layer,),
        extent=Extent(west=BOX[0], south=BOX[1], east=BOX[2], north=BOX[3]),
        settings=ExportSettings(basemap="none"),
    )


def _write(
    runtime,
    directory: Path,
    layer: ExportLayer,
    mode=OutputMode.STANDALONE_HTML,
    compress: bool = True,
):
    return OnlyMapWriter(runtime_provider=runtime).write(
        _project(layer), directory, mode=mode, compress=compress
    )


SAMPLE = """
async (points) => {
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
  const ratio = img.width / el.getBoundingClientRect().width;
  return points.map((p) => {
    const [x, y] = el.project(p);
    return Array.from(
      ctx.getImageData(Math.round(x * ratio), Math.round(y * ratio), 1, 1).data
    );
  });
}
"""


def _offline(page) -> list[str]:
    """Refuse every request off this machine, as a map opened offline meets
    them, and list what was asked for.

    On the context rather than the page, so a request a worker makes is
    refused too. The runtime's usage report is refused without being listed:
    it is documented, and fails silently offline.
    """
    refused: list[str] = []

    def handle(route) -> None:
        url = route.request.url
        if url.startswith(("http://", "https://")) and urlsplit(url).hostname not in (
            "127.0.0.1",
            "localhost",
        ):
            if not url.startswith(TELEMETRY_ENDPOINT):
                refused.append(url)
            route.abort()
        else:
            route.continue_()

    page.context.route("**/*", handle)
    return refused


def _open(page, address: str) -> None:
    page.set_viewport_size({"width": 900, "height": 700})
    page.goto(address)
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


def _close(color: list[int], expected: tuple[int, int, int], within: int = 12) -> bool:
    return all(abs(a - b) <= within for a, b in zip(color, expected, strict=False))


def _sample_until(page, points, ready, timeout_s: float = 20.0):
    """Sample `points` until `ready(samples)` holds or the time is up."""
    deadline = time.monotonic() + timeout_s
    samples = page.evaluate(SAMPLE, points)
    while not ready(samples) and time.monotonic() < deadline:
        page.wait_for_timeout(250)
        samples = page.evaluate(SAMPLE, points)
    return samples


def _assert_water_drawn(page) -> None:
    lake, river, road, empty = _sample_until(
        page,
        [LAKE, RIVER, ROAD, EMPTY],
        lambda s: _close(s[0], WATER_FILL) and _close(s[1], WATER_FILL),
    )
    assert _close(lake, WATER_FILL), f"the lake is {lake}"
    assert _close(river, WATER_FILL), f"the river is {river}"
    # The roads are another layer of the archive, which this one leaves out:
    # between the lakes the road's line is not drawn in the layer's colour.
    assert not _close(road, WATER_FILL), f"the road between them is {road}"
    assert not _close(empty, WATER_FILL), f"outside the water is {empty}"


@pytest.mark.parametrize("opened", ["file", "http"])
def test_a_remote_archive_draws_its_layer(page, runtime, tile_host, tmp_path, opened):
    address = tile_host.url("/lakes.pmtiles")
    layer = _water_layer(
        VectorTileSpec(url=address, source_layer="water", max_zoom=10), remote=True
    )
    entry = _write(runtime, tmp_path, layer).entry_path
    # The archive's server is the only host the map needs.
    refused = _offline(page)
    if opened == "file":
        _open(page, entry.as_uri())
        _assert_water_drawn(page)
    else:
        with FakeCogServer({"/index.html": entry.read_bytes()}) as site:
            _open(page, site.url("index.html"))
            _assert_water_drawn(page)
    reads = [
        r for r in tile_host.requests if r[1] == "/lakes.pmtiles" and r[0] == "GET"
    ]
    assert reads and all(r[2] for r in reads), "the archive was read unranged"
    assert not refused, f"the map asked other hosts for {refused}"


# Uncompressed is how the Preview writes a page; compressed is the export.
@pytest.mark.parametrize("compress", [True, False], ids=["export", "preview"])
def test_a_single_file_carries_its_archive(page, runtime, tmp_path, compress) -> None:
    """Opened from disk, with nothing served and no internet: the page answers
    the runtime's range requests from the archive it carries, and decodes its
    tiles with the worker it carries."""
    source = tmp_path / "lakes.pmtiles"
    source.write_bytes(ARCHIVE)
    layer = _water_layer(
        VectorTileSpec(
            url=str(source),
            path=str(source),
            source_layer="water",
            size_bytes=len(ARCHIVE),
        ),
        remote=False,
    )
    entry = _write(runtime, tmp_path / "out", layer, compress=compress).entry_path
    assert not list(entry.parent.glob("*.pmtiles"))
    refused = _offline(page)
    _open(page, entry.as_uri())
    _assert_water_drawn(page)
    assert not refused, f"the map asked the internet for {refused}"


def test_a_folder_serves_its_archive_beside_the_page(page, runtime, tmp_path) -> None:
    """Served, with no internet beyond the folder's own server."""
    source = tmp_path / "lakes.pmtiles"
    source.write_bytes(ARCHIVE)
    layer = _water_layer(
        VectorTileSpec(
            url=str(source),
            path=str(source),
            source_layer="water",
            size_bytes=len(ARCHIVE),
        ),
        remote=False,
    )
    result = _write(runtime, tmp_path / "out", layer, OutputMode.FOLDER)
    folder = result.entry_path.parent
    files = {
        f"/{path.relative_to(folder).as_posix()}": path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }
    refused = _offline(page)
    with FakeCogServer(files) as site:
        _open(page, site.url("index.html"))
        _assert_water_drawn(page)
    assert not refused, f"the map asked the internet for {refused}"


@pytest.mark.parametrize("opened", ["file", "http"])
def test_a_service_draws_in_its_publishers_style(
    page, runtime, tile_host, tmp_path, opened
) -> None:
    layer = ExportLayer(
        layer_id="base",
        name="Base",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        popup=PopupSpec(enabled=False),
        vector_tiles=VectorTileSpec(
            url=tile_host.url("/t/{z}/{x}/{y}.pbf"),
            style_url=tile_host.url("/style.json"),
            min_zoom=0,
            max_zoom=10,
        ),
    )
    entry = _write(runtime, tmp_path, layer).entry_path
    points = [LAKE, RIVER, (8.2, 47.15), EMPTY]

    def check() -> None:
        lake, river, road, empty = _sample_until(
            page,
            points,
            lambda s: _close(s[0], STYLE_LAKE) and _close(s[1], STYLE_RIVER),
        )
        # The style's filters pick each lake's colour, and its roads draw.
        assert _close(lake, STYLE_LAKE), f"the lake is {lake}"
        assert _close(river, STYLE_RIVER), f"the river is {river}"
        assert _close(road, STYLE_ROAD, within=40), f"the road is {road}"
        assert not _close(empty, STYLE_LAKE) and not _close(empty, STYLE_RIVER)

    # The service is the only host the map needs.
    refused = _offline(page)
    if opened == "file":
        _open(page, entry.as_uri())
        check()
    else:
        with FakeCogServer({"/index.html": entry.read_bytes()}) as site:
            _open(page, site.url("index.html"))
            check()
    assert not refused, f"the map asked other hosts for {refused}"


def test_a_click_on_an_archive_feature_opens_its_popup(
    page, runtime, tile_host, tmp_path
) -> None:
    """The archive's features carry their fields, so a click shows them."""
    layer = _water_layer(
        VectorTileSpec(
            url=tile_host.url("/lakes.pmtiles"), source_layer="water", max_zoom=10
        ),
        remote=True,
    )
    layer = dataclasses.replace(
        layer, popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("kind"),))
    )
    entry = _write(runtime, tmp_path, layer).entry_path
    _open(page, entry.as_uri())
    _sample_until(page, [LAKE], lambda s: _close(s[0], WATER_FILL))
    for place, kind in ((LAKE, "lake"), (RIVER, "river")):
        x, y = page.evaluate(
            "(p) => { const el = document.querySelector('om-map');"
            " const box = el.getBoundingClientRect(); const [x, y] = el.project(p);"
            " return [box.left + x, box.top + y]; }",
            list(place),
        )
        page.mouse.click(x, y)
        page.wait_for_function(
            """(kind) => [...document.querySelectorAll('om-overlay')].some((o) =>
                 o.shadowRoot && [...o.shadowRoot.querySelectorAll('.om-popup-value')]
                   .some((v) => v.textContent.trim() === kind))""",
            arg=kind,
            timeout=10_000,
        )


def _click(page, place) -> list[str]:
    """Click `place` and return the values the popups then show."""
    x, y = page.evaluate(
        "(p) => { const el = document.querySelector('om-map');"
        " const box = el.getBoundingClientRect(); const [x, y] = el.project(p);"
        " return [box.left + x, box.top + y]; }",
        list(place),
    )
    page.mouse.click(x, y)
    page.wait_for_timeout(1500)
    return page.evaluate(
        """() => [...document.querySelectorAll('om-overlay')].flatMap((o) =>
             o.shadowRoot ? [...o.shadowRoot.querySelectorAll('.om-popup-value')]
               .map((v) => v.textContent.trim()).filter((v) => v) : [])"""
    )


_LAKE_RED = SymbolSpec(
    fill_color=Color(255, 0, 0), stroke_color=Color(255, 0, 0), stroke_width=1.0
)
_OTHER_BLUE = SymbolSpec(
    fill_color=Color(0, 0, 255), stroke_color=Color(0, 0, 255), stroke_width=1.0
)
# What QGIS leaves undrawn, the river, by each way a renderer leaves it out.
UNDRAWN_RIVER = {
    "class-switched-off": RendererSpec(
        kind=RendererKind.CATEGORIZED,
        field_name="kind",
        categories=(CategorySpec("lake", "Lake", _LAKE_RED),),
        hidden_values=("river",),
        unmatched_hidden=True,
    ),
    "switched-off-beside-all-others": RendererSpec(
        kind=RendererKind.CATEGORIZED,
        field_name="kind",
        categories=(
            CategorySpec("lake", "Lake", _LAKE_RED),
            CategorySpec(None, "Other", _OTHER_BLUE, is_default=True),
        ),
        hidden_values=("river",),
    ),
    "a-range-between-drawn-ones": RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name="depth",
        classes=(
            GraduatedClassSpec(0, 2, "Shallow", _OTHER_BLUE),
            GraduatedClassSpec(5, 20, "Deep", _LAKE_RED),
        ),
        value_ranges=(
            ValueRange(0, 2, drawn=True),
            ValueRange(2, 5, drawn=False),
            ValueRange(5, 20, drawn=True),
        ),
    ),
    "outside-every-range": RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name="depth",
        classes=(GraduatedClassSpec(5, 20, "Deep", _LAKE_RED),),
        value_ranges=(ValueRange(0, 5, drawn=False), ValueRange(5, 20, drawn=True)),
    ),
}


@pytest.mark.parametrize("case", list(UNDRAWN_RIVER))
def test_what_qgis_does_not_draw_is_not_drawn(
    page, runtime, tile_host, tmp_path, case
) -> None:
    """An archive's features all stay in it, so the map itself leaves out the
    ones QGIS does not draw - never in the grey of "other values" - and, where
    the runtime's filter can, a click there finds nothing."""
    layer = dataclasses.replace(
        _water_layer(
            VectorTileSpec(
                url=tile_host.url("/lakes.pmtiles"), source_layer="water", max_zoom=10
            ),
            remote=True,
        ),
        renderer=UNDRAWN_RIVER[case],
        popup=PopupSpec(enabled=True, fields=(PopupFieldSpec("kind"),)),
    )
    entry = _write(runtime, tmp_path, layer).entry_path
    _open(page, entry.as_uri())
    lake, river = _sample_until(
        page, [LAKE, RIVER], lambda s: _close(s[0], (255, 0, 0))
    )
    assert _close(lake, (255, 0, 0)), f"the lake is {lake}"
    # With no basemap, nothing drawn is nothing in the map's picture.
    assert river[3] == 0, f"the river is drawn in {river}"
    # Drawn invisible there, so a click still finds it (a row says so).
    if case not in ("switched-off-beside-all-others", "a-range-between-drawn-ones"):
        assert _click(page, RIVER) == []
    assert _click(page, LAKE) == ["lake"]


def test_a_cartograph_frame_draws_tiles_with_no_internet(
    page, runtime, tile_host, tmp_path
) -> None:
    """A cartograph's live frame waits in a template until its loader runs;
    its vector tile layer still gets the decoder the page carries."""
    loaded = runtime.load()
    if loaded.cartograph_javascript is None or loaded.cartograph_css is None:
        pytest.skip("this runtime copy has no cartograph build")
    layer = ExportLayer(
        layer_id="base",
        name="Base",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        popup=PopupSpec(enabled=False),
        vector_tiles=VectorTileSpec(
            url=tile_host.url("/t/{z}/{x}/{y}.pbf"),
            style_url=tile_host.url("/style.json"),
            min_zoom=0,
            max_zoom=10,
        ),
    )
    frame = FrameSpec(
        id="main", box=Box(10, 10, 190, 120), mode="live", center=(8.36, 47.2), zoom=10
    )
    html = write_cartograph_html(
        CartographSpec(cartograph_id="cg-tiles", size="A4", items=(frame,)),
        CartographRuntimeBundle(
            cartograph_js=loaded.cartograph_javascript,
            cartograph_css=loaded.cartograph_css.decode("utf-8"),
            core_js=loaded.javascript,
            core_css=loaded.css.decode("utf-8"),
            mvt_worker=loaded.mvt_worker,
        ),
        {"main": build_manifest(_project(layer))},
    )
    sheet = tmp_path / "sheet.html"
    sheet.write_text(html, encoding="utf-8")

    refused = _offline(page)
    _open(page, sheet.as_uri())
    (lake,) = _sample_until(page, [LAKE], lambda s: _close(s[0], STYLE_LAKE))
    assert _close(lake, STYLE_LAKE), f"the lake is {lake}"
    assert not refused, f"the page asked other hosts for {refused}"
