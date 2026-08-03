"""Fixture projects, built through PyQGIS rather than committed as `.qgz`.

See README.md in this directory for why. Each builder returns a loaded
`QgsProject`; the tests read it exactly as the dialog would.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip(
    "qgis.core", reason="PyQGIS is unavailable; skipping the fixture tier"
)


# `qgis_app` and `project` come from tests/conftest.py - one QgsApplication per
# process, or the interpreter segfaults when two tiers run together.


def _layer(name, geometry, crs, fields, rows):
    """An in-memory layer. No file on disk, so no absolute path to leak."""
    field_spec = "&".join(f"field={f}" for f in fields)
    layer = qgis_core.QgsVectorLayer(
        f"{geometry}?crs={crs}&{field_spec}", name, "memory"
    )
    assert layer.isValid(), f"failed to build fixture layer {name!r}"

    features = []
    for attributes, coordinates in rows:
        feature = qgis_core.QgsFeature(layer.fields())
        for index, value in enumerate(attributes):
            feature.setAttribute(index, value)
        feature.setGeometry(_geometry(geometry, coordinates))
        features.append(feature)

    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def _geometry(kind, coordinates):
    points = [
        qgis_core.QgsPointXY(coordinates[i], coordinates[i + 1])
        for i in range(0, len(coordinates), 2)
    ]
    if kind == "Point":
        return qgis_core.QgsGeometry.fromPointXY(points[0])
    if kind == "LineString":
        return qgis_core.QgsGeometry.fromPolylineXY(points)
    return qgis_core.QgsGeometry.fromPolygonXY([points])


def _label(layer, field_name):
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = field_name
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _categorized(layer, field_name, colours):
    categories = []
    for value, colour in colours.items():
        symbol = qgis_core.QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(qgis_core.QgsColorUtils.colorFromString(colour))
        categories.append(qgis_core.QgsRendererCategory(value, symbol, str(value)))
    layer.setRenderer(qgis_core.QgsCategorizedSymbolRenderer(field_name, categories))


@pytest.fixture
def points_categorized(project):
    """Point layer, categorized renderer, labels, popups and a data credit."""
    layer = _layer(
        "stations",
        "Point",
        "EPSG:4326",
        ["name:string", "kind:string"],
        [
            (["Ashford", "civil"], [0.87, 51.15]),
            (["Barnet", "civil"], [-0.2, 51.65]),
            (["Colchester", "military"], [0.9, 51.89]),
        ],
    )
    _label(layer, "name")
    _categorized(layer, "kind", {"civil": "#1f77b4", "military": "#d62728"})
    layer.serverProperties().setAttribution("© Fixture Survey")
    project.addMapLayer(layer)
    project.setTitle("Stations")
    return project


@pytest.fixture
def lines_graduated(project):
    """Line layer with a graduated renderer and scale-range visibility."""
    layer = _layer(
        "routes",
        "LineString",
        "EPSG:4326",
        ["name:string", "load:double"],
        [
            (["North", 12.5], [0.0, 51.0, 0.5, 51.4]),
            (["South", 88.0], [0.1, 50.6, 0.7, 50.2]),
        ],
    )
    layer.setScaleBasedVisibility(True)
    layer.setMinimumScale(1_000_000)
    layer.setMaximumScale(1_000)
    project.addMapLayer(layer)
    project.setTitle("Routes")
    return project


@pytest.fixture
def polygons_grouped(project):
    """Polygon layer inside a group, with an aliased and a hidden field."""
    layer = _layer(
        "wards",
        "Polygon",
        "EPSG:4326",
        ["name:string", "pop:integer", "internal_id:string"],
        [
            (["Central", 4200, "x-91"], [0.0, 51.0, 0.4, 51.0, 0.4, 51.3, 0.0, 51.3]),
            (["Harbour", 1800, "x-92"], [0.5, 51.0, 0.9, 51.0, 0.9, 51.3, 0.5, 51.3]),
        ],
    )
    layer.setFieldAlias(1, "Population")

    # Hide the internal id the way a user would, through the field's editor
    # configuration, so the reader is exercised rather than a shortcut.
    setup = qgis_core.QgsEditorWidgetSetup("Hidden", {})
    layer.setEditorWidgetSetup(2, setup)

    project.addMapLayer(layer, False)
    group = project.layerTreeRoot().addGroup("Boundaries")
    group.addLayer(layer)
    project.setTitle("Wards")
    return project


@pytest.fixture
def mixed_crs(project):
    """Two layers in different projections; both must land in WGS84."""
    wgs84 = _layer(
        "in_wgs84", "Point", "EPSG:4326", ["name:string"], [(["a"], [10.0, 50.0])]
    )
    utm = _layer(
        "in_utm",
        "Point",
        "EPSG:32633",
        ["name:string"],
        [(["b"], [500000.0, 5540000.0])],
    )
    project.addMapLayer(wgs84)
    project.addMapLayer(utm)
    project.setTitle("Mixed projections")
    return project


@pytest.fixture
def antimeridian(project):
    """Data spanning 180 degrees - the case a naive bounding box ruins."""
    layer = _layer(
        "aleutian_like",
        "Point",
        "EPSG:4326",
        ["name:string"],
        [
            (["west"], [179.5, 52.0]),
            (["east"], [-179.0, 52.5]),
            (["mainland"], [-176.0, 53.0]),
        ],
    )
    project.addMapLayer(layer)
    project.setTitle("Across the antimeridian")
    return project


ALL_FIXTURES = (
    "points_categorized",
    "lines_graduated",
    "polygons_grouped",
    "mixed_crs",
    "antimeridian",
)


def pytest_generate_tests(metafunc):
    """Feed every fixture project to any test asking for `fixture_name`.

    Parametrising from here rather than exporting `ALL_FIXTURES` to the test
    module: `tests/` is not a package, so `from .conftest import ...` fails, and
    a second copy of the list in the test file is exactly the drift this tier
    exists to catch. Adding a fixture stays a one-line change.
    """
    if "fixture_name" in metafunc.fixturenames:
        metafunc.parametrize("fixture_name", ALL_FIXTURES)


@pytest.fixture
def fixture_project(request, fixture_name):
    """The named fixture project, plus its name for assertion messages."""
    return request.getfixturevalue(fixture_name), fixture_name
