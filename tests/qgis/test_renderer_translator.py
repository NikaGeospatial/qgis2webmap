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
QtCore = pytest.importorskip("qgis.PyQt.QtCore")


def marker_symbol(color: str = "#ff0000", size: float = 2.0):
    symbol = qgis_core.QgsMarkerSymbol.createSimple({"color": color, "size": str(size)})
    return symbol


class TestLineColour:
    """Lines kept exporting grey, and no test noticed.

    A line symbol layer answers `fillColor()` and `strokeColor()` with an
    invalid QColor - only `color()` holds its real colour - so reading the first
    two alone produced the `#888888` placeholder for every line in every export.
    On a real project that turned a neon teal river into drained grey, and the
    map looked nothing like QGIS.
    """

    @staticmethod
    def _line(colour: str, width: float = 0.5):
        return qgis_core.QgsLineSymbol.createSimple(
            {"color": colour, "line_width": str(width)}
        )

    def test_a_line_keeps_its_own_colour(self, qgis_app) -> None:
        spec = translate_symbol(self._line("#1de9c8"), FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None
        assert (spec.stroke_color.r, spec.stroke_color.g, spec.stroke_color.b) == (
            0x1D,
            0xE9,
            0xC8,
        )

    def test_a_line_never_falls_back_to_the_placeholder(self, qgis_app) -> None:
        """The symptom, stated directly: grey where QGIS showed a colour."""
        spec = translate_symbol(self._line("#1de9c8"), FidelityReportBuilder(), "test")
        assert spec.stroke_color is not None
        grey = (0x88, 0x88, 0x88)
        assert (spec.stroke_color.r, spec.stroke_color.g, spec.stroke_color.b) != grey

    def test_a_lines_colour_is_a_stroke_not_a_fill(self, qgis_app) -> None:
        spec = translate_symbol(self._line("#1de9c8"), FidelityReportBuilder(), "test")
        assert spec.fill_color is None

    def test_a_casing_stack_exports_the_line_on_top(self, qgis_app) -> None:
        """QGIS paints bottom-up, so index 0 is the casing hidden underneath."""
        symbol = self._line("#9a9a9a", width=1.2)
        symbol.appendSymbolLayer(
            qgis_core.QgsSimpleLineSymbolLayer.create(
                {"color": "#1de9c8", "line_width": "0.5"}
            )
        )

        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None
        assert (spec.stroke_color.r, spec.stroke_color.g, spec.stroke_color.b) == (
            0x1D,
            0xE9,
            0xC8,
        ), "the visible top layer should win, not the casing beneath it"

    def test_a_disabled_top_layer_does_not_decide_the_colour(self, qgis_app) -> None:
        """An unticked layer is not on the map, so it must not set the colour."""
        symbol = self._line("#1de9c8")
        hidden = qgis_core.QgsSimpleLineSymbolLayer.create({"color": "#9a9a9a"})
        hidden.setEnabled(False)
        symbol.appendSymbolLayer(hidden)

        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None
        assert (spec.stroke_color.r, spec.stroke_color.g, spec.stroke_color.b) == (
            0x1D,
            0xE9,
            0xC8,
        )


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
        # The value has to travel out of the translator: dropping the class from
        # the expression alone sends its features to the "other" fallback
        # colour, which draws exactly what the author switched off.
        assert spec.hidden_values == ("civil",)

    def test_a_field_name_that_cannot_be_referenced_falls_back_and_says_so(
        self, project, make_memory_layer
    ) -> None:
        """`$Land Use == ...` is not parseable, and used to ship anyway.

        OnlyMap's expression language has no quoted field form, so a field name
        with a space cannot be referenced at all. Emitting it produced markup
        the runtime could not read: the symbology vanished with nothing said.
        """
        layer = make_memory_layer(
            "parcels",
            fields="Land Use:string",
            features=[("residential", [0.0, 0.0])],
        )
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "Land Use",
                [
                    qgis_core.QgsRendererCategory(
                        "residential", marker_symbol("#00ff00"), "Residential"
                    )
                ],
            )
        )

        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)

        assert spec.kind is RendererKind.SINGLE
        assert spec.symbol is not None
        assert any(
            "Land Use" in i.detail for i in report.by_status(FidelityStatus.UNSUPPORTED)
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

    def test_a_non_circle_shape_is_not_reported_per_class(self, qgis_app) -> None:
        """The shape note moved to the symbol atlas, which reports once per
        layer after actually rasterising - see
        `tests/qgis/test_symbol_rasterizer.py::test_the_outcome_is_reported`.

        Reporting here as well would be both noise and a falsehood: it fired
        once per class, and it said the shape "may" be approximated on layers
        where QGIS had drawn it exactly.
        """
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"name": "star"})
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert not any(
            "marker shape" in i.detail
            for i in report.by_status(FidelityStatus.APPROXIMATED)
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


class TestPenStyleAndMarkerGeometry:
    """Pen styles and marker geometry that used to be dropped on the floor.

    Measured, not assumed: a default QGIS simple line is SquareCap (Qt 16) and
    BevelJoin (Qt 64), so it reads as *not* round and emits nothing - which is
    also deck.gl's default. These reads earn their keep on the lines a user
    deliberately rounded, where the old exporter squared off every dead end.
    """

    def test_a_default_line_is_not_round(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple({"color": "#000000"})
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.cap_rounded is False
        assert spec.join_rounded is False

    def test_a_rounded_line_is_read_as_round(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple({"color": "#000000"})
        layer = symbol.symbolLayer(0)
        layer.setPenCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        layer.setPenJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.cap_rounded is True
        assert spec.join_rounded is True

    def test_a_fill_symbol_has_no_cap_style(self, qgis_app) -> None:
        """A fill layer answers penJoinStyle but not penCapStyle."""
        symbol = qgis_core.QgsFillSymbol.createSimple({"color": "#000000"})
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.cap_rounded is False

    def test_marker_rotation_is_read_in_degrees(self, qgis_app) -> None:
        symbol = marker_symbol()
        symbol.symbolLayer(0).setAngle(45.0)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.rotation == pytest.approx(45.0)

    def test_marker_offset_converts_millimetres_to_pixels(self, qgis_app) -> None:
        symbol = marker_symbol()
        symbol.symbolLayer(0).setOffset(QtCore.QPointF(2.0, -3.0))
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.offset_x == pytest.approx(2.0 * MM_TO_PIXELS)
        assert spec.offset_y == pytest.approx(-3.0 * MM_TO_PIXELS)

    def test_an_unrotated_marker_reads_zero(self, qgis_app) -> None:
        spec = translate_symbol(marker_symbol(), FidelityReportBuilder(), "test")
        assert spec.rotation == pytest.approx(0.0)
        assert spec.offset_x == pytest.approx(0.0)
        assert spec.offset_y == pytest.approx(0.0)


class TestGeometryGenerator:
    """A generated geometry is not in the exported data, and must be said so.

    QGIS's geometry generator replaces a feature's geometry with the result of
    an expression and draws that. The export carries the original geometry, so
    the drawn shape is simply absent - and before this it left *no* fidelity
    item at all, exporting an arbitrary colour with nothing said. Found by
    diffing our property coverage against GeoLibre's.
    """

    @staticmethod
    def _generated(expression: str = "buffer($geometry, 0.1)"):
        symbol = qgis_core.QgsFillSymbol.createSimple({"color": "#ff0000"})
        generator = qgis_core.QgsGeometryGeneratorSymbolLayer.create(
            {"geometryModifier": expression}
        )
        symbol.changeSymbolLayer(0, generator)
        return symbol, generator

    def test_it_is_reported(self, qgis_app) -> None:
        symbol, _ = self._generated()
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert report.by_status(FidelityStatus.UNSUPPORTED)

    def test_the_report_names_the_expression(self, qgis_app) -> None:
        """A user cannot act on "something was lost"; they can act on which
        symbol layer it was."""
        symbol, _ = self._generated("centroid($geometry)")
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        details = " ".join(i.detail for i in report.items)
        assert "centroid" in details

    def test_it_never_passes_silently(self, qgis_app) -> None:
        """The regression, stated directly: it used to record nothing."""
        symbol, _ = self._generated()
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert len(report) > 0

    def test_the_authors_colours_survive_where_possible(self, qgis_app) -> None:
        """The generator's own colour is arbitrary. Its sub-symbol carries what
        the author chose, so the layer draws in their colours rather than in a
        default that looks like a bug."""
        symbol, generator = self._generated()
        generator.setSubSymbol(
            qgis_core.QgsFillSymbol.createSimple({"color": "#1de9c8"})
        )
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert spec.fill_color is not None
        assert (spec.fill_color.r, spec.fill_color.g, spec.fill_color.b) == (
            0x1D,
            0xE9,
            0xC8,
        )

    def test_an_ordinary_symbol_is_unaffected(self, qgis_app) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple({"color": "#1de9c8"})
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "test")
        assert not report.by_status(FidelityStatus.UNSUPPORTED)
