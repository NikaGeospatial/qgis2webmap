"""A remote COG, exported live and drawn by the real runtime from its address.

A raster QGIS reads over the web is exported as a `COGLayer` on that address
and nothing is carried. The page still needs the raster stand-in
(`packaging/raster_embed.py`) although it embeds no raster: opened from disk,
Chrome starts the runtime's decode workers only from the stand-in's `data:`
URLs, and offline the runtime gets the EPSG:3857 definition only from it -
`epsg.io` is refused here, so the test cannot pass on the real network. The
file is served cross-origin with CORS and Range support, and the page must
draw its pixels where the raster's extent projects, both opened from disk and
served over http.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math

import pytest
from fake_cog_server import FakeCogServer, rgb_cog

from nika_onlymap_exporter.core.export_ir import (
    AssetDependency,
    AssetDisposition,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    RasterSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

EARTH_RADIUS = 6378137.0
ORIGIN = (1280174.0, 6841175.0)
PIXEL = 100.0
SIZE = 256


def lonlat(x: float, y: float) -> tuple[float, float]:
    """Web Mercator metres to longitude/latitude degrees."""
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2)
    return lon, lat


WEST, NORTH = lonlat(*ORIGIN)
EAST, SOUTH = lonlat(ORIGIN[0] + SIZE * PIXEL, ORIGIN[1] - SIZE * PIXEL)


@pytest.fixture(scope="module")
def cog_host():
    data = rgb_cog(ORIGIN, PIXEL, lambda column, row: (column, row, 255 - column))
    with FakeCogServer({"/ortho.tif": data}) as running:
        yield running


def _remote_layer(url: str) -> ExportLayer:
    """What `layer_reader._read_remote_raster` builds for this file."""
    return ExportLayer(
        layer_id="ortho",
        name="Ortho",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        renderer=RendererSpec(kind=RendererKind.UNSUPPORTED),
        raster=RasterSpec(
            path=url,
            src=url,
            remote=True,
            band_count=3,
            source_crs="EPSG:3857",
            extent=Extent(west=WEST, south=SOUTH, east=EAST, north=NORTH),
            pixel_width=SIZE,
            pixel_height=SIZE,
            resolution_x=PIXEL,
            resolution_y=PIXEL,
            bands=(1, 2, 3),
        ),
        dependencies=(
            AssetDependency(identifier=url, disposition=AssetDisposition.REMOTE),
        ),
    )


def _export(runtime, directory, url: str):
    project = ExportProject(
        title="Remote COG",
        layers=(_remote_layer(url),),
        extent=Extent(
            west=WEST - 0.15, south=SOUTH - 0.1, east=EAST + 0.15, north=NORTH + 0.1
        ),
        settings=ExportSettings(basemap="none"),
    )
    result = OnlyMapWriter(runtime_provider=runtime).write(project, directory)
    return result.entry_path


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
  const ratio = img.width / el.getBoundingClientRect().width;
  const corners = [[west, south], [west, north], [east, south], [east, north]]
    .map((p) => el.project(p));
  const xs = corners.map((p) => p[0]), ys = corners.map((p) => p[1]);
  const expected = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  // The background, from a pixel halfway between the map's edge and the raster.
  const at = (x, y) =>
    (Math.round(y * ratio) * canvas.width + Math.round(x * ratio)) * 4;
  const b = at(expected[0] / 2, (expected[1] + expected[3]) / 2);
  const bg = [data[b], data[b + 1], data[b + 2], data[b + 3]];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let drawn = 0;
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const i = (y * canvas.width + x) * 4;
      const diff = Math.abs(data[i] - bg[0]) + Math.abs(data[i + 1] - bg[1])
        + Math.abs(data[i + 2] - bg[2]) + Math.abs(data[i + 3] - bg[3]);
      if (diff > 30) {
        drawn++;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
    }
  }
  return {
    drawn,
    found: drawn
      ? [minX / ratio, minY / ratio, (maxX + 1) / ratio, (maxY + 1) / ratio]
      : null,
    expected,
  };
}
"""


def _open(page, address: str) -> list[str]:
    """Open `address` with `epsg.io` refused; the refused requests."""
    refused: list[str] = []

    def refuse(route) -> None:
        refused.append(route.request.url)
        route.abort()

    page.route("https://epsg.io/**", refuse)
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
    return refused


def _measure_drawn(page, timeout_s: float = 20.0):
    """Measure once the raster has drawn; `whenSettled` can resolve before the
    COG's tiles are decoded. Polled, and bounded."""
    import time

    deadline = time.monotonic() + timeout_s
    measured = page.evaluate(MEASURE, [WEST, SOUTH, EAST, NORTH])
    while measured["drawn"] == 0 and time.monotonic() < deadline:
        page.wait_for_timeout(250)
        measured = page.evaluate(MEASURE, [WEST, SOUTH, EAST, NORTH])
    return measured


@pytest.mark.parametrize("opened", ["file", "http"])
def test_a_remote_cog_draws_its_pixels(
    page, runtime, cog_host, tmp_path, opened
) -> None:
    entry = _export(runtime, tmp_path, cog_host.url("ortho.tif"))
    html = entry.read_text(encoding="utf-8")
    assert 'type="COGLayer"' in html and cog_host.url("ortho.tif") in html
    before = len(cog_host.requests)
    if opened == "file":
        refused = _open(page, entry.as_uri())
    else:
        with FakeCogServer({"/index.html": entry.read_bytes()}) as site:
            refused = _open(page, site.url("index.html"))
    measured = _measure_drawn(page)

    reads = [r for r in cog_host.requests[before:] if r[0] == "GET"]
    assert reads and all(r[2] for r in reads), "the page read the COG unranged"
    assert refused == [], "the runtime asked epsg.io for EPSG:3857"
    assert measured["drawn"] > 0, "the remote COG drew nothing"
    # Its pixels cover exactly where its extent projects, to within a pixel
    # and a half of tile filtering at the edges.
    assert measured["found"] == pytest.approx(measured["expected"], abs=1.5)
