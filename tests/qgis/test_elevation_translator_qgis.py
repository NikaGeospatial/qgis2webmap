"""Height translation against live QGIS objects.

`elevation_translator` duck-types every read, so `tests/unit/` already covers
the logic. What only QGIS can tell us is whether the members we probe still
exist and still mean what we think - `PropertyExtrusionHeight` on the 3D symbol,
`"25dRenderer"` as a renderer type string, and the load-bearing assumption that
a 2.5D height set through `QgsExpressionContextUtils` lands in the project's
custom variables where we look for it.

The 3D half skips rather than fails where QGIS was built without 3D support:
that build is exactly the one the duck typing exists to protect.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.elevation_translator import (
    HEIGHT_VARIABLE,
    translate_elevation,
)
from nika_onlymap_exporter.core.export_ir import (
    FidelityStatus,
    GeometryKind,
    RendererKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.renderer_translator import translate_renderer

qgis_core = pytest.importorskip("qgis.core")
QtGui = pytest.importorskip("qgis.PyQt.QtGui")


def polygon_layer():
    return qgis_core.QgsVectorLayer(
        "Polygon?crs=EPSG:4326&field=levels:double", "buildings", "memory"
    )


def read(layer, project=None, kind=GeometryKind.POLYGON):
    report = FidelityReportBuilder()
    return translate_elevation(layer, report, kind, project), report


class TestThreeDViewProperties:
    """The layer's 3D view properties, which is where real extrusion lives."""

    @pytest.fixture()
    def qgis_3d(self):
        return pytest.importorskip("qgis._3d")

    def test_a_constant_extrusion_height_is_read(self, qgis_3d) -> None:
        symbol = qgis_3d.QgsPolygon3DSymbol()
        symbol.setExtrusionHeight(18.0)
        layer = polygon_layer()
        layer.setRenderer3D(qgis_3d.QgsVectorLayer3DRenderer(symbol))

        elevation, report = read(layer)
        assert elevation.extruded is True
        assert elevation.height == pytest.approx(18.0)
        assert elevation.source == "3d-renderer"
        assert FidelityStatus.PRESERVED in [i.status for i in report.items]

    def test_a_data_defined_height_is_read_as_a_field(self, qgis_3d) -> None:
        """The property key we probe by name has to still be there."""
        symbol = qgis_3d.QgsPolygon3DSymbol()
        properties = symbol.dataDefinedProperties()
        properties.setProperty(
            qgis_3d.QgsPolygon3DSymbol.PropertyExtrusionHeight,
            qgis_core.QgsProperty.fromField("levels"),
        )
        symbol.setDataDefinedProperties(properties)
        layer = polygon_layer()
        layer.setRenderer3D(qgis_3d.QgsVectorLayer3DRenderer(symbol))

        elevation, _ = read(layer)
        assert elevation.height_field == "levels"
        assert elevation.height is None

    def test_a_qgis_expression_is_reported_rather_than_translated(
        self, qgis_3d
    ) -> None:
        symbol = qgis_3d.QgsPolygon3DSymbol()
        properties = symbol.dataDefinedProperties()
        properties.setProperty(
            qgis_3d.QgsPolygon3DSymbol.PropertyExtrusionHeight,
            qgis_core.QgsProperty.fromExpression('"levels" * 3'),
        )
        symbol.setDataDefinedProperties(properties)
        layer = polygon_layer()
        layer.setRenderer3D(qgis_3d.QgsVectorLayer3DRenderer(symbol))

        elevation, report = read(layer)
        assert elevation.is_set is False
        assert FidelityStatus.UNSUPPORTED in [i.status for i in report.items]

    def test_edges_become_a_wireframe(self, qgis_3d) -> None:
        symbol = qgis_3d.QgsPolygon3DSymbol()
        symbol.setExtrusionHeight(10.0)
        symbol.setEdgesEnabled(True)
        layer = polygon_layer()
        layer.setRenderer3D(qgis_3d.QgsVectorLayer3DRenderer(symbol))

        elevation, _ = read(layer)
        assert elevation.wireframe is True

    def test_a_layer_with_no_3d_renderer_is_flat(self) -> None:
        elevation, report = read(polygon_layer())
        assert elevation.is_set is False
        assert report.items == ()


class Test25DRenderer:
    """The 2.5D renderer keeps its height on the project, not on the layer.

    That is the non-obvious part, and the reason `translate_elevation` needs a
    project at all. If QGIS ever moves it, this test is what says so.
    """

    def _project_with_height(self, height: str):
        project = qgis_core.QgsProject()
        qgis_core.QgsExpressionContextUtils.setProjectVariable(
            project, HEIGHT_VARIABLE, height
        )
        return project

    def test_the_height_variable_lands_where_we_look_for_it(self) -> None:
        project = self._project_with_height("12")
        assert project.customVariables().get(HEIGHT_VARIABLE) == "12"

    def test_a_numeric_height_is_read(self) -> None:
        layer = polygon_layer()
        layer.setRenderer(
            qgis_core.Qgs25DRenderer.convertFromRenderer(layer.renderer())
        )

        elevation, report = read(layer, self._project_with_height("12"))
        assert elevation.height == pytest.approx(12.0)
        assert elevation.source == "25d-renderer"
        # The fake walls become a real extrusion, so the fixed angle and the
        # painted shadows go; a user should be told rather than left wondering.
        assert FidelityStatus.APPROXIMATED in [i.status for i in report.items]

    def test_a_field_height_is_read(self) -> None:
        layer = polygon_layer()
        layer.setRenderer(
            qgis_core.Qgs25DRenderer.convertFromRenderer(layer.renderer())
        )

        elevation, _ = read(layer, self._project_with_height('"levels"'))
        assert elevation.height_field == "levels"

    def test_the_renderer_type_string_has_not_changed(self) -> None:
        layer = polygon_layer()
        renderer = qgis_core.Qgs25DRenderer.convertFromRenderer(layer.renderer())
        assert renderer.type() == "25dRenderer"

    def test_the_roof_and_wall_colours_are_kept(self) -> None:
        """The colours are on the renderer, not in its symbol.

        Its symbol's top layer is a geometry generator, so translating it the
        normal way returns nothing usable - which is how a 2.5D layer used to
        export grey, the whole map restyled by one setting.
        """
        layer = polygon_layer()
        renderer_25d = qgis_core.Qgs25DRenderer.convertFromRenderer(layer.renderer())
        renderer_25d.setRoofColor(QtGui.QColor(255, 0, 0))
        renderer_25d.setWallColor(QtGui.QColor(0, 0, 255))
        layer.setRenderer(renderer_25d)

        renderer = translate_renderer(layer, FidelityReportBuilder())
        assert renderer.kind is RendererKind.SINGLE
        assert renderer.symbol is not None
        assert (renderer.symbol.fill_color.r, renderer.symbol.fill_color.g) == (255, 0)
        assert renderer.symbol.stroke_color.b == 255

    def test_the_renderer_has_no_embedded_renderer_to_unwrap(self) -> None:
        """Measured, not assumed - the first implementation assumed it did.

        `Qgs25DRenderer` is not a wrapper: `embeddedRenderer()` is the base
        class's null, so there is nothing behind it to fall back on.
        """
        layer = polygon_layer()
        renderer = qgis_core.Qgs25DRenderer.convertFromRenderer(layer.renderer())
        assert renderer.embeddedRenderer() is None
