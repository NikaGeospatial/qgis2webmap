"""Renderer translation for all three symbology kinds in 0.1.0 scope.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ClassificationMethod,
    FidelityStatus,
    RendererKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.renderer_translator import (
    MM_TO_PIXELS,
    translate_renderer,
    translate_symbol,
)

qgis_core = pytest.importorskip("qgis.core")
QtGui = pytest.importorskip("qgis.PyQt.QtGui")


def marker_symbol(color: str = "#ff0000", size: float = 2.0):
    symbol = qgis_core.QgsMarkerSymbol.createSimple({"color": color, "size": str(size)})
    return symbol


class TestTranslateSymbol:
    def test_reads_colour_and_converts_units(self, qgis_app) -> None:
        symbol = marker_symbol("#3366cc", size=4.0)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.fill_color is not None
        assert (spec.fill_color.r, spec.fill_color.g, spec.fill_color.b) == (
            0x33,
            0x66,
            0xCC,
        )
        # QGIS size is a diameter in mm; the spec carries a pixel radius.
        assert spec.radius == pytest.approx(4.0 * MM_TO_PIXELS / 2.0)

    def test_stacked_symbol_layers_are_flattened_and_reported(self, qgis_app) -> None:
        symbol = marker_symbol()
        symbol.appendSymbolLayer(
            qgis_core.QgsSimpleMarkerSymbolLayer.create({"color": "#00ff00"})
        )
        report = FidelityReportBuilder()
        spec = translate_symbol(symbol, report, "test")

        assert spec.symbol_layer_count == 2
        approximated = report.by_status(FidelityStatus.APPROXIMATED)
        assert any("stacks 2 symbol layers" in i.detail for i in approximated)

    def test_missing_symbol_is_reported_not_crashed(self, qgis_app) -> None:
        report = FidelityReportBuilder()
        spec = translate_symbol(None, report, "test")
        assert spec.fill_color is None
        assert report.by_status(FidelityStatus.UNSUPPORTED)

    def test_alpha_survives_separately_from_channels(self, qgis_app) -> None:
        symbol = marker_symbol()
        symbol.symbolLayer(0).setFillColor(QtGui.QColor(255, 0, 0, 128))
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.fill_color is not None
        assert spec.fill_color.r == 255
        assert 0.4 < spec.fill_color.a < 0.6


class TestCategorized:
    def _categorized_layer(self, make_memory_layer):
        layer = make_memory_layer(
            "airports",
            fields="kind:string",
            features=[("civil", [0.0, 0.0]), ("military", [1.0, 1.0])],
        )
        categories = [
            qgis_core.QgsRendererCategory("civil", marker_symbol("#00ff00"), "Civil"),
            qgis_core.QgsRendererCategory(
                "military", marker_symbol("#ff0000"), "Military"
            ),
        ]
        layer.setRenderer(qgis_core.QgsCategorizedSymbolRenderer("kind", categories))
        return layer

    def test_translates_every_class(self, project, make_memory_layer) -> None:
        layer = self._categorized_layer(make_memory_layer)
        spec = translate_renderer(layer, FidelityReportBuilder())

        assert spec.kind is RendererKind.CATEGORIZED
        assert spec.field_name == "kind"
        assert [c.value for c in spec.categories] == ["civil", "military"]
        assert [c.label for c in spec.categories] == ["Civil", "Military"]

    def test_disabled_class_is_dropped_and_reported(
        self, project, make_memory_layer
    ) -> None:
        """An unchecked class in QGIS means 'do not draw these features'."""
        layer = self._categorized_layer(make_memory_layer)
        renderer = layer.renderer()
        renderer.updateCategoryRenderState(0, False)

        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)

        assert len(spec.categories) == 1
        assert any(
            "switched\noff" in i.detail or "switched off" in i.detail
            for i in report.by_status(FidelityStatus.APPROXIMATED)
        )


class TestGraduated:
    def _graduated_layer(self, make_memory_layer, mode):
        layer = make_memory_layer(
            "rivers",
            geometry="LineString",
            fields="length:double",
            features=[("10", [0.0, 0.0, 1.0, 1.0]), ("90", [2.0, 2.0, 3.0, 3.0])],
        )
        ranges = [
            qgis_core.QgsRendererRange(0.0, 50.0, marker_symbol("#eeeeee"), "0 - 50"),
            qgis_core.QgsRendererRange(
                50.0, 100.0, marker_symbol("#111111"), "50 - 100"
            ),
        ]
        renderer = qgis_core.QgsGraduatedSymbolRenderer("length", ranges)
        # Modern API: classification is an object, not the deprecated mode enum.
        renderer.setClassificationMethod(mode())
        layer.setRenderer(renderer)
        return layer

    def test_translates_ranges_and_classification(
        self, project, make_memory_layer
    ) -> None:
        layer = self._graduated_layer(
            make_memory_layer, qgis_core.QgsClassificationQuantile
        )
        spec = translate_renderer(layer, FidelityReportBuilder())

        assert spec.kind is RendererKind.GRADUATED
        assert spec.field_name == "length"
        assert spec.classification is ClassificationMethod.QUANTILE
        assert [(c.lower, c.upper) for c in spec.classes] == [
            (0.0, 50.0),
            (50.0, 100.0),
        ]

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("QgsClassificationEqualInterval", ClassificationMethod.EQUAL_INTERVAL),
            ("QgsClassificationJenks", ClassificationMethod.NATURAL_BREAKS),
            (
                "QgsClassificationStandardDeviation",
                ClassificationMethod.STANDARD_DEVIATION,
            ),
            ("QgsClassificationPrettyBreaks", ClassificationMethod.PRETTY_BREAKS),
        ],
    )
    def test_every_classification_method_maps(
        self, project, make_memory_layer, mode, expected
    ) -> None:
        layer = self._graduated_layer(make_memory_layer, getattr(qgis_core, mode))
        spec = translate_renderer(layer, FidelityReportBuilder())
        assert spec.classification is expected

    def test_custom_mode_is_approximated_not_dropped(
        self, project, make_memory_layer
    ) -> None:
        """Breaks stay exact even when the method is unrecognised."""
        layer = self._graduated_layer(
            make_memory_layer, qgis_core.QgsClassificationCustom
        )
        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)

        assert spec.classification is ClassificationMethod.UNKNOWN
        assert len(spec.classes) == 2
        assert report.by_status(FidelityStatus.APPROXIMATED)


class TestUnsupportedRenderer:
    def test_rule_based_is_named_in_the_report(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("pts", features=[("a", [0.0, 0.0])])
        root_rule = qgis_core.QgsRuleBasedRenderer.Rule(marker_symbol())
        layer.setRenderer(qgis_core.QgsRuleBasedRenderer(root_rule))

        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)

        assert spec.kind is RendererKind.UNSUPPORTED
        assert spec.unsupported_reason == "QgsRuleBasedRenderer"
        unsupported = report.by_status(FidelityStatus.UNSUPPORTED)
        assert any("QgsRuleBasedRenderer" in i.detail for i in unsupported)


class TestMarkerShape:
    """Guards against upstream qgis2web#1218 - every marker becoming a circle."""

    @pytest.mark.parametrize(
        "shape", ["circle", "square", "triangle", "star", "diamond", "pentagon"]
    )
    def test_shape_is_captured_not_flattened(self, qgis_app, shape) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"name": shape})
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.marker_shape == shape

    def test_non_circle_shape_is_reported(self, qgis_app) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"name": "star"})
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert any(
            "star" in i.detail for i in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_circle_needs_no_note(self, qgis_app) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"name": "circle"})
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert not any(
            "marker shape" in i.detail
            for i in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_line_symbol_has_no_marker_shape(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple({"color": "#000000"})
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.marker_shape is None
