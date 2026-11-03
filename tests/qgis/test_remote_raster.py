"""A raster QGIS reads over the web is refused with one row, and never read.

QGIS opens a COG at `/vsicurl/https://...` (or a bare `https://...`) as an
ordinary GDAL layer. The export must never download it, and until the map can
draw it live it is refused with a row saying to save a local copy. A local
server with Range support serves 64 x 64 COGs and counts the requests, so
"never read" is measured, not assumed.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer

qgis_core = pytest.importorskip("qgis.core")
gdal = pytest.importorskip("osgeo.gdal")


def _cog(path, bands: int, epsg: int) -> bytes:
    from osgeo import osr

    source = gdal.GetDriverByName("MEM").Create("", 64, 64, bands, gdal.GDT_Byte)
    if epsg == 3857:
        source.SetGeoTransform((1280174, 400, 0, 6841175, 0, -400))
    else:
        source.SetGeoTransform((11.5, 0.004, 0, 52.3, 0, -0.004))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    source.SetProjection(srs.ExportToWkt())
    for band in range(1, bands + 1):
        source.GetRasterBand(band).Fill(60 * band)
    gdal.GetDriverByName("COG").CreateCopy(str(path), source)
    return path.read_bytes()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("remote-cog")
    files = {
        "/rgb.tif": _cog(folder / "rgb.tif", 3, 3857),
        "/grey.tif": _cog(folder / "grey.tif", 1, 3857),
        "/rgb4326.tif": _cog(folder / "rgb4326.tif", 3, 4326),
    }
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _body(self) -> tuple[int, bytes, str | None]:
            data = files.get(self.path.split("?")[0], b"")
            ranged = self.headers.get("Range")
            if not ranged:
                return 200, data, None
            start, _, end = ranged.split("=", 1)[1].partition("-")
            first, last = int(start), min(int(end or len(data) - 1), len(data) - 1)
            return 206, data[first : last + 1], f"bytes {first}-{last}/{len(data)}"

        def _reply(self, with_body: bool) -> None:
            requests.append(self.path)
            status, body, content_range = self._body()
            self.send_response(status)
            self.send_header("Content-Type", "image/tiff")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if content_range:
                self.send_header("Content-Range", content_range)
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        def do_HEAD(self) -> None:
            self._reply(False)

        def do_GET(self) -> None:
            self._reply(True)

    running = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    running.daemon_threads = True
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{running.server_port}"
    yield base, requests
    running.shutdown()
    running.server_close()


def _read(source: str):
    layer = qgis_core.QgsRasterLayer(source, "Remote", "gdal")
    assert layer.isValid(), source
    return layer


def _only_row(report) -> str:
    (item,) = report.items
    assert item.status is FidelityStatus.UNSUPPORTED
    return item.detail


@pytest.mark.parametrize("prefix", ["/vsicurl/", ""])
@pytest.mark.parametrize("name", ["rgb.tif", "grey.tif", "rgb4326.tif"])
def test_a_web_raster_is_refused_once_without_a_read(
    qgis_app, server, prefix, name
) -> None:
    """The bug this replaces: a blocking "file is missing" dependency beside
    a Preserved "exported as a Cloud Optimized GeoTIFF" row."""
    base, requests = server
    layer = _read(prefix + base + "/" + name)
    before = len(requests)
    report = FidelityReportBuilder()
    assert read_layer(layer, report) is None
    assert len(requests) == before, "the export read the remote file"
    row = _only_row(report)
    assert base.split("//")[1] in row and "over the web" in row
    assert "never downloads" in row and "Export > Save As" in row
    assert "missing" not in row


def test_a_cloud_store_is_refused_by_its_address() -> None:
    from nika_onlymap_exporter.core.layer_reader import (
        _is_network_path,
        remote_raster_url,
    )

    assert _is_network_path("/vsis3/bucket/ortho.tif")
    assert _is_network_path("/vsizip//vsicurl/https://example.org/a.zip/a.tif")
    assert _is_network_path("HTTPS://example.org/a.tif")
    assert not _is_network_path("/home/me/ortho.tif")
    assert not _is_network_path("C:/data/ortho.tif")
    assert remote_raster_url("/vsis3/bucket/ortho.tif") is None
    assert remote_raster_url("/vsicurl/https://example.org/a.tif") == (
        "https://example.org/a.tif"
    )
