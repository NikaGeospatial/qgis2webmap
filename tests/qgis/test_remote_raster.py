"""A raster QGIS reads over the web is exported live or refused, and never read.

QGIS opens a COG at `/vsicurl/https://...` (or a bare `https://...`) as an
ordinary GDAL layer. The export must never download it: a composite the map
can draw from the address is exported live, and anything else is refused with
a row saying to save a local copy. A local server with Range support serves
1024 x 1024 COGs and counts the requests, so "never read" is measured, not
assumed.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os

import pytest
from fake_cog_server import FakeCogServer

from nika_onlymap_exporter.core.export_ir import (
    AssetDisposition,
    FidelityStatus,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer

qgis_core = pytest.importorskip("qgis.core")
gdal = pytest.importorskip("osgeo.gdal")

# Large enough that QGIS, opening it, reads only the header and overviews: a
# read of any full-resolution pixel is then a request the counts below catch.
SIZE = 1024


def _cog(path, bands: int, epsg: int, compression: str = "DEFLATE") -> bytes:
    from osgeo import osr

    source = gdal.GetDriverByName("MEM").Create("", SIZE, SIZE, bands, gdal.GDT_Byte)
    if epsg == 3857:
        source.SetGeoTransform((1280174, 50, 0, 6841175, 0, -50))
    else:
        source.SetGeoTransform((11.5, 0.0005, 0, 52.3, 0, -0.0005))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    source.SetProjection(srs.ExportToWkt())
    for band in range(1, bands + 1):
        # Noise, so the file is as large as its pixels.
        source.GetRasterBand(band).WriteRaster(
            0, 0, SIZE, SIZE, os.urandom(SIZE * SIZE)
        )
    gdal.GetDriverByName("COG").CreateCopy(
        str(path), source, options=[f"COMPRESS={compression}"]
    )
    return path.read_bytes()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("remote-cog")
    files = {
        "/rgb.tif": _cog(folder / "rgb.tif", 3, 3857),
        "/grey.tif": _cog(folder / "grey.tif", 1, 3857),
        "/rgb4326.tif": _cog(folder / "rgb4326.tif", 3, 4326),
        "/rgb-lzw.tif": _cog(folder / "rgb-lzw.tif", 3, 3857, "LZW"),
    }
    with FakeCogServer(files) as running:
        yield running.base, running.requests


def _read(source: str):
    layer = qgis_core.QgsRasterLayer(source, "Remote", "gdal")
    assert layer.isValid(), source
    return layer


def _only_row(report) -> str:
    (item,) = report.items
    assert item.status is FidelityStatus.UNSUPPORTED
    return item.detail


def _rows(report, status: FidelityStatus) -> str:
    return " ".join(item.detail for item in report.items if item.status is status)


@pytest.mark.parametrize("prefix", ["/vsicurl/", ""])
def test_a_web_mercator_composite_exports_live_without_a_read(
    qgis_app, server, prefix
) -> None:
    base, requests = server
    layer = _read(prefix + base + "/rgb.tif")
    before = len(requests)
    report = FidelityReportBuilder()
    exported = read_layer(layer, report)
    # Its compression is checked too: from the header GDAL already holds.
    assert requests[before:] == [], "the export read the remote file"

    assert exported is not None and exported.raster is not None
    assert exported.raster.remote is True
    assert exported.raster.src == base + "/rgb.tif"
    assert exported.raster.reference == base + "/rgb.tif"
    assert exported.raster.bands == (1, 2, 3)
    assert exported.raster.style_qml is None
    assert exported.source_kind is SourceKind.SERVICE
    (dependency,) = exported.dependencies
    assert dependency.disposition is AssetDisposition.REMOTE
    assert dependency.identifier == base + "/rgb.tif"
    approximated = _rows(report, FidelityStatus.APPROXIMATED)
    assert "live Cloud Optimized GeoTIFF" in approximated
    assert "CORS" in approximated and "Range requests" in approximated
    preserved = _rows(report, FidelityStatus.PRESERVED)
    assert "3-band colour composite" in preserved
    assert not _rows(report, FidelityStatus.UNSUPPORTED)
    assert "missing" not in " ".join(item.detail for item in report.items)


@pytest.mark.parametrize("prefix", ["/vsicurl/", ""])
@pytest.mark.parametrize("name", ["grey.tif", "rgb4326.tif", "rgb-lzw.tif"])
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
    assert requests[before:] == [], "the export read the remote file"
    row = _only_row(report)
    assert base.split("//")[1] in row and "over the web" in row
    assert "never downloads" in row and "Export > Save As" in row
    assert "missing" not in row


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("grey.tif", "QGIS draws its colours from its values"),
        ("rgb4326.tif", "in EPSG:4326, and the web map draws"),
        ("rgb-lzw.tif", "is LZW-compressed, which the web map cannot decode yet"),
    ],
)
def test_each_refusal_says_why(qgis_app, server, name, reason) -> None:
    base, _ = server
    report = FidelityReportBuilder()
    assert read_layer(_read("/vsicurl/" + base + "/" + name), report) is None
    assert reason in _only_row(report)


def test_a_keyed_address_blocks_the_export_without_its_key(qgis_app, server) -> None:
    base, _ = server
    report = FidelityReportBuilder()
    exported = read_layer(_read("/vsicurl/" + base + "/rgb.tif?token=s3cret"), report)
    assert exported is not None and exported.raster is not None
    assert "s3cret" not in exported.raster.src
    (dependency,) = exported.dependencies
    assert dependency.disposition is AssetDisposition.BLOCKING
    assert dependency.note is not None
    assert dependency.note.startswith("The file address for 'Remote' on ")
    assert "s3cret" not in dependency.note + dependency.identifier


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


@pytest.mark.parametrize("mode", ["FOLDER", "STANDALONE_HTML", "SHARE_ZIP"])
def test_packaging_neither_converts_nor_carries_it(
    qgis_app, server, tmp_path, mode
) -> None:
    from nika_onlymap_exporter.core.export_ir import ExportProject, OutputMode
    from nika_onlymap_exporter.core.manifest_builder import (
        build_raster_layer_element,
    )
    from nika_onlymap_exporter.packaging.raster_staging import stage_rasters

    base, requests = server
    exported = read_layer(
        _read("/vsicurl/" + base + "/rgb.tif"), FidelityReportBuilder()
    )
    assert exported is not None and exported.raster is not None
    project = ExportProject(title="Remote", layers=(exported,))

    def never(*args, **kwargs):
        raise AssertionError("a remote raster was handed to the converter")

    before = len(requests)
    report = FidelityReportBuilder()
    staged = stage_rasters(
        project,
        tmp_path / "out",
        getattr(OutputMode, mode),
        report,
        converter=never,
        baker=never,
    )
    assert requests[before:] == []
    assert staged.can_export
    assert staged.files == () and staged.embedded == ()
    assert staged.project.layers == (exported,)
    assert not report.items
    element = build_raster_layer_element(exported, exported.raster)
    assert f'src="{base}/rgb.tif"' in element
    assert 'type="COGLayer"' in element
