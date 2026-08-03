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
def make_memory_layer(qgis_app):
    """Factory fixture for in-memory layers.

    Exposed as a fixture rather than imported directly: `tests/` is not a
    package, so `from .conftest import ...` fails, and adding `__init__.py`
    files purely to satisfy an import is worse than using the mechanism pytest
    already provides.
    """
    return _build_memory_layer
