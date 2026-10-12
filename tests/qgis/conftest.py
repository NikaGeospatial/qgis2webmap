"""Shared fixtures for the QGIS-dependent test tier.

These tests need a real PyQGIS. CI's `tests/unit` tier runs everywhere; this tier
runs where QGIS is installed, and skips cleanly where it is not, so a contributor
without QGIS still gets a green local run rather than a wall of import errors.

Run with:
    pytest tests/qgis

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools

import pytest

qgis_core = pytest.importorskip(
    "qgis.core", reason="PyQGIS is unavailable; skipping the QGIS test tier"
)


# `qgis_app` and `project` come from tests/conftest.py. They were defined here
# too, and a duplicate session-scoped QgsApplication is not a harmless copy: a
# second instance in one process segfaults the interpreter, so this tier and the
# fixture tier each passed alone and crashed when run together.


def _build_memory_layer(
    name: str,
    geometry: str = "Point",
    crs: str = "EPSG:4326",
    fields: str = "name:string",
    features: list[tuple[str, list[float]]] | None = None,
):
    """Build an in-memory vector layer with optional features.

    Memory layers keep these tests independent of any file on disk, so they run
    identically on every machine and in every checkout.
    """
    layer = qgis_core.QgsVectorLayer(
        f"{geometry}?crs={crs}&field={fields}", name, "memory"
    )
    assert layer.isValid(), f"failed to construct memory layer {name!r}"

    if features:
        provider = layer.dataProvider()
        to_add = []
        for label, coords in features:
            feature = qgis_core.QgsFeature(layer.fields())
            feature.setAttribute(0, label)
            if geometry == "Point":
                geom = qgis_core.QgsGeometry.fromPointXY(
                    qgis_core.QgsPointXY(coords[0], coords[1])
                )
            else:
                points = [
                    qgis_core.QgsPointXY(coords[i], coords[i + 1])
                    for i in range(0, len(coords), 2)
                ]
                geom = (
                    qgis_core.QgsGeometry.fromPolylineXY(points)
                    if geometry == "LineString"
                    else qgis_core.QgsGeometry.fromPolygonXY([points])
                )
            feature.setGeometry(geom)
            to_add.append(feature)
        provider.addFeatures(to_add)
        layer.updateExtents()

    return layer


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory):
    """Point `QSettings` at a scratch directory for the whole tier.

    The dialog stores the live-preview preference in `QSettings`, which is
    machine-wide and persistent. Two consequences, both bad:

    - Tests wrote into the developer's real QGIS configuration.
    - A test that flips the preference changed the *next run's* starting state,
      so `setChecked(False)` on an already-false box emitted no signal and the
      assertion failed - passing or failing depending on run history rather than
      on the code.

    Ini format because it is the only one `setPath` redirects; the native
    backend on each platform ignores it.
    """
    from qgis.PyQt.QtCore import QSettings

    directory = str(tmp_path_factory.mktemp("qsettings"))
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
    yield
    QSettings.setDefaultFormat(previous_format)


@pytest.fixture
def runtime_required() -> None:
    """Skip a test that cannot run without the OnlyMap runtime.

    Anything going through the writer - `write_preview` included - needs the real
    runtime bytes, and `FetchingRuntime` raises rather than degrading when they
    are absent. Every other tier already guards on `discover_runtime_dir`; the
    preview tests did not, so on a machine with no cached runtime they failed
    where the rest of the suite skipped. A failure that only means "this machine
    has not fetched the runtime" trains people to ignore red.
    """
    from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir

    if discover_runtime_dir() is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")


@pytest.fixture
def make_raster_layer(qgis_app, tmp_path):
    """Factory fixture for a small file-backed raster layer.

    A raster cannot be an in-memory layer the way a vector can: the `gdal`
    provider reads a file, and whether that file exists is one of the things
    `read_raster` has to decide on. So this writes a tiny GeoTIFF into the
    test's own `tmp_path` instead. No fixture file enters the repository - the
    pixels are made here and go away with the temporary directory - which keeps
    the tier's "in-memory data, no fixtures" property in spirit if not in
    letter.

    `crs=None` writes the file with no projection, which is how a raster with
    no CRS reaches the reader.

    GDAL's Python bindings ship with QGIS, but they are packaged separately on
    some platforms, so their absence skips rather than errors - the same
    contract this tier has with PyQGIS itself.
    """
    made = itertools.count()

    def _build(
        name: str = "elevation",
        crs: str | None = "EPSG:4326",
        west: float = 4.0,
        north: float = 52.0,
        pixel_size: float = 0.25,
        width: int = 4,
        height: int = 4,
        bands: int = 1,
    ):
        gdal = pytest.importorskip(
            "osgeo.gdal",
            reason="GDAL's Python bindings are unavailable; skipping raster tests",
        )
        # Without this GDAL 3.x warns on every call that the default will
        # change, which turns a passing run into a wall of noise.
        gdal.UseExceptions()

        path = tmp_path / f"{name}_{next(made)}.tif"
        dataset = gdal.GetDriverByName("GTiff").Create(
            str(path), width, height, bands, gdal.GDT_Byte
        )
        dataset.SetGeoTransform([west, pixel_size, 0.0, north, 0.0, -pixel_size])
        if crs is not None:
            reference = qgis_core.QgsCoordinateReferenceSystem(crs)
            assert reference.isValid(), f"unknown CRS {crs!r}"
            dataset.SetProjection(reference.toWkt())
        # Closing is what flushes the header to disk; GDAL has no `close()`.
        dataset = None

        layer = qgis_core.QgsRasterLayer(str(path), name, "gdal")
        assert layer.isValid(), f"failed to construct raster layer {name!r}"
        return layer

    return _build


@pytest.fixture
def make_memory_layer(qgis_app):
    """Factory fixture for in-memory layers.

    Exposed as a fixture rather than imported directly: `tests/` is not a
    package, so `from .conftest import ...` fails, and adding `__init__.py`
    files purely to satisfy an import is worse than using the mechanism pytest
    already provides.
    """
    return _build_memory_layer
