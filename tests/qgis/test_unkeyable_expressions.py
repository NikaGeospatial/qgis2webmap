"""The build caches never reuse a result an expression they cannot see into
may have changed: a function registered from Python answers what its body
says, and no part of a key holds its body.

Reported against the 0.3.1 candidate: a GeoPackage layer with a virtual
field calling a Python function gave 10, then 20; both builds had the same
live-frame key, and the cached page kept 10.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.cartograph_export import (
    LiveFrameCache,
    StaticFrameCache,
    finish_cartograph_full,
    prepare_cartograph,
    prepare_live_reads,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.keyable_expressions import (
    KEYABLE_FUNCTIONS,
    _builtins,
    has_unkeyable_expression,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

RUNTIME = RuntimeBundle(
    javascript=b"// core js placeholder",
    css=b"/* core css placeholder */",
    version="0.12.0",
    sha256="0" * 64,
    cartograph_javascript=b"// cartograph js placeholder",
    cartograph_css=b"/* cartograph css placeholder */",
)

ANSWER: dict[str, object] = {}


@pytest.fixture
def answer():
    """Registers `q2w_audit_value()`, which answers `ANSWER["now"]`."""
    from qgis.core import QgsExpression, qgsfunction

    @qgsfunction(group="Custom", referenced_columns=[])
    def q2w_audit_value(feature, parent):
        return ANSWER["now"]

    ANSWER["now"] = 10
    yield ANSWER
    QgsExpression.unregisterFunction("q2w_audit_value")


@pytest.fixture
def world(project, tmp_path):
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransformContext,
        QgsFeature,
        QgsGeometry,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPointXY,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorFileWriter,
        QgsVectorLayer,
    )

    wm = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(wm)
    memory = QgsVectorLayer("Point?crs=EPSG:3857&field=name:string", "sites", "memory")
    features = []
    for index in range(3):
        feature = QgsFeature(memory.fields())
        feature.setAttributes([f"site {index}"])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(index * 300, 300)))
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    path = str(tmp_path / "sites.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "sites"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        memory, path, QgsCoordinateTransformContext(), options
    )
    sites = QgsVectorLayer(path, "sites", "ogr")
    assert sites.isValid()
    project.addMapLayer(sites)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("audit")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(150, 100, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(wm)
    map_item.setLayers([sites])
    map_item.setExtent(QgsRectangle(-200, -200, 800, 800))
    map_item.refresh()
    return project, layout, map_item, sites


def _build(world, live_cache):
    project, layout, _map_item, _sites = world
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(layout, project, RUNTIME, report, raster_cache=None)
    prepared = prepare_live_reads(prepared, project, report, live_cache=live_cache)
    finished = finish_cartograph_full(prepared, None, report)
    return finished.html, report.items, finished.projects


def _values(built, field: str) -> list[object]:
    (project,) = built[2]
    (layer,) = project.layers
    return [feature["properties"][field] for feature in layer.geojson["features"]]


def _as_layer_xml(expression: str) -> str:
    """`expression` where a layer's XML keeps a data-defined property's."""
    from xml.sax.saxutils import quoteattr

    return (
        '<maplayer><Option name="expression" value='
        + quoteattr(expression)
        + "/></maplayer>"
    )


class TestAPythonFunction:
    def test_in_a_virtual_field_is_read_again_by_the_live_frame(
        self, world, answer
    ) -> None:
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        _project, _layout, _map_item, sites = world
        sites.addExpressionField(
            "q2w_audit_value()", QgsField("value", QMetaType.Type.Int)
        )
        cache = LiveFrameCache()
        try:
            first = _build(world, cache)
            answer["now"] = 20
            again = _build(world, cache)
        finally:
            cache.clear()
        assert _values(first, "value") == [10, 10, 10]
        assert _values(again, "value") == [20, 20, 20]
        fresh = _build(world, None)
        assert again[0] == fresh[0]
        assert again[2] == fresh[2]

    def test_in_a_style_draws_the_static_frame_again(self, world, answer) -> None:
        from qgis.core import QgsProperty, QgsSymbolLayer

        project, layout, map_item, sites = world
        answer["now"] = "#ff0000"
        symbol_layer = sites.renderer().symbol().symbolLayer(0)
        symbol_layer.setDataDefinedProperty(
            QgsSymbolLayer.Property.FillColor,
            QgsProperty.fromExpression("q2w_audit_value()"),
        )
        cache = StaticFrameCache()
        try:
            first = cache.render(layout, map_item, 72, project)
            answer["now"] = "#0000ff"
            again = cache.render(layout, map_item, 72, project)
            assert cache.render_count == 2
        finally:
            cache.clear()
        assert again.png_bytes != first.png_bytes

    def test_named_like_a_built_in_is_refused_by_qgis(self) -> None:
        """What the allowlist rests on: a Python function cannot take the
        name of a built-in one, so a name on it is QGIS's own."""
        from qgis.core import QgsExpression, qgsfunction

        @qgsfunction(group="Custom", referenced_columns=[], register=False)
        def upper(feature, parent):
            return "shadowed"

        assert not QgsExpression.registerFunction(upper)
        assert not QgsExpression.unregisterFunction("upper")
        assert QgsExpression("upper('a')").evaluate() == "A"


class TestTheLists:
    def test_only_names_built_into_this_qgis_are_trusted(self) -> None:
        """A listed name this QGIS does not have (a function added in a later
        version) could be a Python function here, so it is not trusted; on
        QGIS 4.2, which the lists were made from, every name is built in."""
        from qgis.core import Qgis, QgsExpression

        built_in = {name.lower() for name in QgsExpression.BuiltinFunctions()}
        functions, dollars = _builtins()
        assert functions == KEYABLE_FUNCTIONS & built_in
        assert {f"${name}" for name in dollars} <= built_in
        missing = sorted(KEYABLE_FUNCTIONS - built_in)
        if Qgis.QGIS_VERSION_INT >= 40200:
            assert missing == []
        for name in missing:
            call = f"{name}()"
            assert has_unkeyable_expression(_as_layer_xml(call), set), call

    @pytest.mark.parametrize(
        "expression",
        [
            "now()",
            "$now",
            "rand(1, 2)",
            "uuid()",
            "aggregate('x', 'sum', \"a\")",
            'sum("a")',
            "get_feature('x', 'a', 1)",
            "env('HOME')",
            "eval('1')",
            "var('x')",
            "@atlas_featureid",
            "@layout_page",
            "q2w_unknown_function()",
            'UPPER(Unknown("x"))',
        ],
    )
    def test_an_expression_reading_outside_the_key_is_unkeyable(
        self, expression
    ) -> None:
        assert has_unkeyable_expression(_as_layer_xml(expression), set)

    @pytest.mark.parametrize(
        "expression",
        [
            "upper(\"name\") || '(' || to_string(@q2w_keyed) || ')'",
            "CASE WHEN \"a\" IN (1, 2) THEN color_rgb(1, 2, 3) ELSE 'x' END",
            "with_variable('size', 2, @size * $area)",
            "array_foreach(array(1, 2), @element * 2)",
            "scale_linear(@map_scale, 1, 2, 3, 4)",
        ],
    )
    def test_a_pure_expression_is_keyable(self, expression) -> None:
        assert not has_unkeyable_expression(
            _as_layer_xml(expression), lambda: {"q2w_keyed"}
        )
