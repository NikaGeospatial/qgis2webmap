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

import json
import time
from pathlib import Path

import pytest
from fake_cog_server import FakeCogServer
from fake_tile_archive import TILE_MVT, Feature, mvt, pmtiles, tiles_covering

from nika_onlymap_exporter.core.export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    OutputMode,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
    VectorTileSpec,
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
        {"kind": "lake"},
    ),
    Feature(
        "polygon",
        [(8.45, 47.1), (8.6, 47.1), (8.6, 47.3), (8.45, 47.3), (8.45, 47.1)],
        {"kind": "river"},
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
            symbol=SymbolSpec(
                fill_color=Color(*WATER_FILL), stroke_color=Color(*WATER_FILL)
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


def _write(
    runtime, directory: Path, layer: ExportLayer, mode=OutputMode.STANDALONE_HTML
):
    project = ExportProject(
        title="Tiles",
        layers=(layer,),
        extent=Extent(west=BOX[0], south=BOX[1], east=BOX[2], north=BOX[3]),
        settings=ExportSettings(basemap="none"),
    )
    return OnlyMapWriter(runtime_provider=runtime).write(project, directory, mode=mode)


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


def test_a_single_file_carries_its_archive(page, runtime, tmp_path) -> None:
    """Opened from disk, with nothing served: the page answers the runtime's
    range requests from the archive it carries."""
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
    entry = _write(runtime, tmp_path / "out", layer).entry_path
    assert not list(entry.parent.glob("*.pmtiles"))
    _open(page, entry.as_uri())
    _assert_water_drawn(page)


def test_a_folder_serves_its_archive_beside_the_page(page, runtime, tmp_path) -> None:
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
    with FakeCogServer(files) as site:
        _open(page, site.url("index.html"))
        _assert_water_drawn(page)


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

    if opened == "file":
        _open(page, entry.as_uri())
        check()
    else:
        with FakeCogServer({"/index.html": entry.read_bytes()}) as site:
            _open(page, site.url("index.html"))
            check()
