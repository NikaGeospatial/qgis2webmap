"""Reading QGIS height, in both of the places QGIS keeps it.

Runs in the unit tier, not the QGIS one, because `elevation_translator` imports
no PyQGIS - it duck-types every read so a QGIS built without 3D support cannot
break the plugin on import. Doubles here therefore exercise the real code path,
not a mock of it; `tests/qgis/test_elevation_translator.py` then checks the same
functions against live QGIS objects, which is what catches an API that moved.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.elevation_translator import (
    HEIGHT_VARIABLE,
    translate_elevation,
)
from nika_onlymap_exporter.core.export_ir import FidelityStatus, GeometryKind
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder


class FakeProperty:
    def __init__(self, active=False, field="", expression=""):
        self._active = active
        self._field = field
        self._expression = expression

    def isActive(self):  # noqa: N802 - mirrors the QGIS name
        return self._active

    def field(self):
        return self._field

    def expressionString(self):  # noqa: N802 - mirrors the QGIS name
        return self._expression


class FakeProperties:
    def __init__(self, by_key=None):
        self._by_key = by_key or {}

    def property(self, key):
        return self._by_key.get(key, FakeProperty())


class FakePolygon3DSymbol:
    """Stands in for `QgsPolygon3DSymbol`, matching the members we probe."""

    PropertyExtrusionHeight = 1

    def __init__(self, height=0.0, override=None, edges=False, offset=0.0):
        self._height = height
        self._properties = FakeProperties(
            {self.PropertyExtrusionHeight: override} if override else {}
        )
        self._edges = edges
        self._offset = offset

    def extrusionHeight(self):  # noqa: N802 - mirrors the QGIS name
        return self._height

    def dataDefinedProperties(self):  # noqa: N802 - mirrors the QGIS name
        return self._properties

    def edgesEnabled(self):  # noqa: N802 - mirrors the QGIS name
        return self._edges

    def offset(self):
        return self._offset


class FakePoint3DSymbol(FakePolygon3DSymbol):
    """A 3D point symbol is a mesh - a sphere, a cylinder, an imported model."""


class FakeRenderer3D:
    def __init__(self, symbol):
        self._symbol = symbol

    def symbol(self):
        return self._symbol


class Fake2DRenderer:
    def type(self):
        return "25dRenderer"


class FakeLayer:
    def __init__(self, renderer_3d=None, renderer=None):
        self._renderer_3d = renderer_3d
        self._renderer = renderer

    def id(self):
        return "layer1"

    def name(self):
        return "Buildings"

    def renderer3D(self):  # noqa: N802 - mirrors the QGIS name
        return self._renderer_3d

    def renderer(self):
        return self._renderer


class FakeProject:
    def __init__(self, variables=None):
        self._variables = variables or {}

    def customVariables(self):  # noqa: N802 - mirrors the QGIS name
        return self._variables


def statuses(report: FidelityReportBuilder) -> list[FidelityStatus]:
    return [item.status for item in report.items]


def details(report: FidelityReportBuilder) -> str:
    return " ".join(item.detail for item in report.items)


def read(layer, project=None, kind=GeometryKind.POLYGON):
    report = FidelityReportBuilder()
    return translate_elevation(layer, report, kind, project), report


class TestNoHeight:
    def test_a_plain_layer_is_flat(self) -> None:
        elevation, report = read(FakeLayer())
        assert elevation.is_set is False
        assert report.items == ()

    def test_a_3d_renderer_with_no_height_is_flat(self) -> None:
        elevation, _ = read(FakeLayer(FakeRenderer3D(FakePolygon3DSymbol(height=0.0))))
        assert elevation.is_set is False


class TestThreeDRenderer:
    def test_a_constant_height_is_carried(self) -> None:
        elevation, report = read(
            FakeLayer(FakeRenderer3D(FakePolygon3DSymbol(height=15.0)))
        )
        assert elevation.extruded is True
        assert elevation.height == 15.0
        assert elevation.height_field is None
        assert elevation.source == "3d-renderer"
        assert FidelityStatus.PRESERVED in statuses(report)

    def test_a_field_override_becomes_a_field(self) -> None:
        symbol = FakePolygon3DSymbol(
            height=15.0, override=FakeProperty(active=True, field="levels")
        )
        elevation, _ = read(FakeLayer(FakeRenderer3D(symbol)))
        # The constant is discarded: the override is what QGIS draws.
        assert elevation.height is None
        assert elevation.height_field == "levels"

    def test_a_quoted_field_expression_is_unwrapped(self) -> None:
        symbol = FakePolygon3DSymbol(
            override=FakeProperty(active=True, expression='"levels"')
        )
        elevation, _ = read(FakeLayer(FakeRenderer3D(symbol)))
        assert elevation.height_field == "levels"

    def test_a_numeric_expression_becomes_a_constant(self) -> None:
        symbol = FakePolygon3DSymbol(
            override=FakeProperty(active=True, expression="9.5")
        )
        elevation, _ = read(FakeLayer(FakeRenderer3D(symbol)))
        assert elevation.height == 9.5

    def test_a_real_expression_is_reported_not_guessed(self) -> None:
        """`"levels" * 3` is QGIS's language, not OnlyMap's. Wrong is worse."""
        symbol = FakePolygon3DSymbol(
            override=FakeProperty(active=True, expression='"levels" * 3')
        )
        elevation, report = read(FakeLayer(FakeRenderer3D(symbol)))
        assert elevation.is_set is False
        assert FidelityStatus.UNSUPPORTED in statuses(report)
        assert "expression" in details(report)

    def test_edges_become_a_wireframe(self) -> None:
        elevation, _ = read(
            FakeLayer(FakeRenderer3D(FakePolygon3DSymbol(height=5.0, edges=True)))
        )
        assert elevation.wireframe is True

    def test_a_base_height_is_reported(self) -> None:
        """deck.gl extrudes from zero, so a raised base has nowhere to go."""
        elevation, report = read(
            FakeLayer(FakeRenderer3D(FakePolygon3DSymbol(height=5.0, offset=40.0)))
        )
        assert elevation.height == 5.0
        assert FidelityStatus.UNSUPPORTED in statuses(report)
        assert "base height of 40" in details(report)

    def test_a_point_layer_cannot_be_raised(self) -> None:
        """3D point symbols are meshes; a GeoJsonLayer draws none of them."""
        elevation, report = read(
            FakeLayer(FakeRenderer3D(FakePoint3DSymbol(height=5.0))),
            kind=GeometryKind.POINT,
        )
        assert elevation.is_set is False
        assert FidelityStatus.UNSUPPORTED in statuses(report)
        assert "FakePoint3DSymbol" in details(report)

    def test_a_line_layer_cannot_be_raised(self) -> None:
        elevation, report = read(
            FakeLayer(FakeRenderer3D(FakePolygon3DSymbol(height=5.0))),
            kind=GeometryKind.LINE,
        )
        assert elevation.is_set is False
        assert FidelityStatus.UNSUPPORTED in statuses(report)


class Test25DRenderer:
    """The height is a *project* variable, which is the whole trap here."""

    def test_a_field_variable_becomes_a_field(self) -> None:
        elevation, report = read(
            FakeLayer(renderer=Fake2DRenderer()),
            FakeProject({HEIGHT_VARIABLE: '"levels"'}),
        )
        assert elevation.height_field == "levels"
        assert elevation.source == "25d-renderer"
        assert FidelityStatus.APPROXIMATED in statuses(report)
        assert "shadows" in details(report)

    def test_a_numeric_variable_becomes_a_constant(self) -> None:
        elevation, _ = read(
            FakeLayer(renderer=Fake2DRenderer()), FakeProject({HEIGHT_VARIABLE: "10"})
        )
        assert elevation.height == 10.0

    def test_no_project_means_no_height(self) -> None:
        """Rather than reaching for `QgsProject.instance()` and reading someone
        else's map."""
        elevation, report = read(FakeLayer(renderer=Fake2DRenderer()))
        assert elevation.is_set is False
        assert report.items == ()

    def test_an_expression_variable_is_reported(self) -> None:
        elevation, report = read(
            FakeLayer(renderer=Fake2DRenderer()),
            FakeProject({HEIGHT_VARIABLE: '"levels" * 3'}),
        )
        assert elevation.is_set is False
        assert FidelityStatus.UNSUPPORTED in statuses(report)

    def test_the_3d_renderer_wins_when_both_are_set(self) -> None:
        """A layer can carry both. The 3D view is the one that means height."""
        elevation, _ = read(
            FakeLayer(
                renderer_3d=FakeRenderer3D(FakePolygon3DSymbol(height=15.0)),
                renderer=Fake2DRenderer(),
            ),
            FakeProject({HEIGHT_VARIABLE: "99"}),
        )
        assert elevation.height == 15.0
        assert elevation.source == "3d-renderer"
