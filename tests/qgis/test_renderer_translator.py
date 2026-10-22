"""Renderer translation for all three symbology kinds in 0.1.0 scope.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import bisect
import re

import pytest

from nika_onlymap_exporter.core.export_ir import (
    ClassificationMethod,
    FidelityStatus,
    GeometryKind,
    RendererKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer
from nika_onlymap_exporter.core.manifest_builder import (
    fill_expression,
    line_color_expression,
    numeric_expression,
)
from nika_onlymap_exporter.core.render_units import MapUnitScale
from nika_onlymap_exporter.core.renderer_translator import (
    HAIRLINE_PIXELS,
    MM_TO_PIXELS,
    reference_scale,
    translate_renderer,
    translate_symbol,
)
from nika_onlymap_exporter.core.runtime_legend import derive_runtime_legend

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

    def test_an_unknown_method_is_not_printed_as_one(
        self, project, make_memory_layer
    ) -> None:
        """ "with 2 classes (unknown)" read as a fault in the Kept row."""
        layer = self._graduated_layer(
            make_memory_layer, qgis_core.QgsClassificationCustom
        )
        report = FidelityReportBuilder()
        translate_renderer(layer, report)

        (kept,) = report.by_status(FidelityStatus.PRESERVED)
        assert kept.detail == "Graduated on 'length' with 2 classes."

    def test_a_known_method_is_named_in_words(self, project, make_memory_layer) -> None:
        layer = self._graduated_layer(
            make_memory_layer, qgis_core.QgsClassificationEqualInterval
        )
        report = FidelityReportBuilder()
        translate_renderer(layer, report)

        kept = [i.detail for i in report.by_status(FidelityStatus.PRESERVED)]
        assert "Graduated on 'length' with 2 classes (equal interval)." in kept


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


class TestDashPatterns:
    """Both ways QGIS makes a dashed line, read from real symbol layers.

    Only the custom-pattern half was read before, so a line dashed with the
    ordinary style dropdown - which is what most people use - exported solid.
    The enum handling in `_pen_style_name` is the reason this is a QGIS-tier
    test rather than a unit one: PyQt5 and PyQt6 disagree about whether a Qt
    enum has a `.name`, and only a real Qt can settle it.
    """

    @staticmethod
    def _line(**properties):
        base = {"color": "#000000", "line_width": "1.0"}
        base.update(properties)
        return qgis_core.QgsLineSymbol.createSimple(base)

    def test_a_solid_line_has_no_dash(self, qgis_app) -> None:
        spec = translate_symbol(self._line(), FidelityReportBuilder(), "test")
        assert spec.stroke_dash == ()

    def test_the_style_dropdown_produces_a_dash(self, qgis_app) -> None:
        """`line_style=dash` is the dropdown; it sets penStyle, not the vector."""
        spec = translate_symbol(
            self._line(line_style="dash"), FidelityReportBuilder(), "test"
        )
        assert spec.stroke_dash, "a dashed line must not export solid"
        assert len(spec.stroke_dash) == 2

    def test_a_dotted_line_dashes_differently_from_a_dashed_one(self, qgis_app) -> None:
        """If both mapped to the same pattern, the table would be a lie."""
        dashed = translate_symbol(
            self._line(line_style="dash"), FidelityReportBuilder(), "test"
        )
        dotted = translate_symbol(
            self._line(line_style="dot"), FidelityReportBuilder(), "test"
        )
        assert dashed.stroke_dash != dotted.stroke_dash

    def test_a_custom_pattern_is_read_in_pixels(self, qgis_app) -> None:
        symbol = self._line()
        symbol_layer = symbol.symbolLayer(0)
        symbol_layer.setUseCustomDashPattern(True)
        symbol_layer.setCustomDashVector([4.0, 2.0])

        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")
        assert len(spec.stroke_dash) == 2
        # Millimetres in, pixels out, like every other length in the model.
        assert spec.stroke_dash[0] > 4.0

    def test_a_dash_dot_line_is_reported_as_approximated(self, qgis_app) -> None:
        """It still draws dashed, so silence would be the wrong answer."""
        report = FidelityReportBuilder()
        spec = translate_symbol(self._line(line_style="dash dot"), report, "test")

        if len(spec.stroke_dash) > 2:
            assert any("dash-dot" in item.detail for item in report.items)


class TestOutlines:
    """A symbol's outline as QGIS draws it: switched off, hairline or sized.

    The cartograph comparison showed it first: flood polygons drawn with "No
    Pen" in QGIS came out ringed in dark grey in the browser, because the
    outline's colour and its 0.26 mm default width were read and its style
    was not.
    """

    def test_a_fill_with_no_pen_has_a_transparent_outline(self, qgis_app) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple(
            {"color": "#6baed6", "outline_style": "no", "outline_color": "#232323"}
        )
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None
        assert spec.stroke_color.a == 0.0
        assert spec.stroke_width == 0.0
        assert spec.fill_color is not None and spec.fill_color.a == 1.0

    def test_a_marker_with_no_pen_has_a_transparent_outline(self, qgis_app) -> None:
        symbol = marker_symbol()
        symbol.symbolLayer(0).setStrokeStyle(QtCore.Qt.PenStyle.NoPen)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None and spec.stroke_color.a == 0.0
        assert spec.stroke_width == 0.0

    def test_a_fill_with_no_brush_is_outline_only(self, qgis_app) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple(
            {"color": "#6baed6", "style": "no", "outline_color": "#232323"}
        )
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.fill_color is not None and spec.fill_color.a == 0.0
        assert spec.stroke_color is not None and spec.stroke_color.a == 1.0

    def test_a_solid_fill_is_still_drawn(self, qgis_app) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple({"color": "#6baed6"})
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.fill_color is not None and spec.fill_color.a == 1.0

    def test_an_outline_only_layer_writes_a_transparent_fill(
        self, project, make_memory_layer
    ) -> None:
        """End to end through the writer's expression."""
        layer = make_memory_layer("zones", geometry="Polygon", fields="name:string")
        layer.renderer().setSymbol(
            qgis_core.QgsFillSymbol.createSimple(
                {"color": "#6baed6", "style": "no", "outline_color": "#232323"}
            )
        )
        spec = translate_renderer(layer, FidelityReportBuilder())

        assert fill_expression(spec, GeometryKind.POLYGON) == "'#6baed600'"
        assert line_color_expression(spec, GeometryKind.POLYGON) == "'#232323'"

    def test_a_drawn_outline_keeps_its_colour_and_width(self, qgis_app) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple(
            {"color": "#6baed6", "outline_color": "#232323", "outline_width": "0.5"}
        )
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_color is not None and spec.stroke_color.a == 1.0
        assert spec.stroke_width == pytest.approx(0.5 * MM_TO_PIXELS)

    def test_a_zero_width_marker_outline_is_a_hairline(self, qgis_app) -> None:
        """QGIS's default simple marker: width 0, drawn one pixel wide."""
        symbol = marker_symbol()
        assert symbol.symbolLayer(0).strokeWidth() == 0
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_width == HAIRLINE_PIXELS

    def test_a_zero_width_line_is_a_hairline(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple(
            {"line_color": "#555555", "line_width": "0"}
        )
        spec = translate_symbol(symbol, FidelityReportBuilder(), "test")

        assert spec.stroke_width == HAIRLINE_PIXELS

    def test_the_written_outline_is_transparent_for_every_class(
        self, project, make_memory_layer
    ) -> None:
        """End to end through the writer's expression, not just the spec."""
        layer = make_memory_layer("flood", geometry="Polygon", fields="depth:double")
        ranges = [
            qgis_core.QgsRendererRange(
                low,
                low + 1,
                qgis_core.QgsFillSymbol.createSimple(
                    {"color": colour, "outline_style": "no"}
                ),
                f"{low}-{low + 1} m",
            )
            for low, colour in ((0, "#eff3ff"), (1, "#08519c"))
        ]
        layer.setRenderer(qgis_core.QgsGraduatedSymbolRenderer("depth", ranges))
        spec = translate_renderer(layer, FidelityReportBuilder())

        expression = line_color_expression(spec, GeometryKind.POLYGON)
        assert expression.endswith("00'"), expression
        assert "#232323'" not in expression


# --------------------------------------------------------------------------
# QGIS as the oracle for which class a feature gets
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?(?:e-?\d+)?)|(?P<str>'(?:[^'\\]|\\.)*')"
    r"|\$(?P<field>\w+)|(?P<word>[A-Za-z_]\w*)|(?P<op>===|==|>=|<=|&&|\|\||[?:,\[\]()=<>]))"
)


def _tokens(source: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    while position < len(source.rstrip()):
        match = _TOKEN.match(source, position)
        assert match, f"cannot tokenise {source[position:]!r}"
        position = match.end()
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
    return tokens


class _Evaluator:
    """The subset of the runtime's accessor language the translator writes.

    A ternary chain of `==`, `>=`, `<=` joined by `&&`, and a d3 threshold
    `scale`. Equality is JavaScript's loose one for the cases that occur: NULL
    equals only NULL, and a number equals a string that parses to it.
    """

    def __init__(self, source: str, properties: dict) -> None:
        self.tokens, self.at, self.properties = _tokens(source), 0, properties

    def peek(self) -> str:
        return self.tokens[self.at][1] if self.at < len(self.tokens) else ""

    def take(self) -> tuple[str, str]:
        self.at += 1
        return self.tokens[self.at - 1]

    def expect(self, text: str) -> None:
        assert self.take()[1] == text

    def ternary(self):
        condition = self.logical_or()
        if self.peek() != "?":
            return condition
        self.take()
        then = self.ternary()
        self.expect(":")
        otherwise = self.ternary()
        return then if condition else otherwise

    def logical_or(self):
        left = self.logical_and()
        while self.peek() == "||":
            self.take()
            right = self.logical_and()
            left = left or right
        return left

    def logical_and(self):
        left = self.comparison()
        while self.peek() == "&&":
            self.take()
            right = self.comparison()
            left = left and right
        return left

    def comparison(self):
        left = self.primary()
        if self.peek() not in ("==", "===", ">=", "<=", "<", ">"):
            return left
        op = self.take()[1]
        right = self.primary()
        if op in ("==", "==="):
            return self.loose_equals(left, right)
        if left is None or right is None:
            return False
        return {">=": left >= right, "<=": left <= right}[op]

    @staticmethod
    def loose_equals(left, right) -> bool:
        if left is None or right is None:
            return left is right
        if isinstance(left, str) != isinstance(right, str):
            text, number = (left, right) if isinstance(left, str) else (right, left)
            try:
                return float(text or 0) == number
            except ValueError:
                return False
        return left == right

    def array(self) -> list:
        self.expect("[")
        items = []
        while self.peek() != "]":
            items.append(self.primary())
            if self.peek() == ",":
                self.take()
        self.expect("]")
        return items

    def primary(self):
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        if kind == "field":
            return self.properties.get(text)
        if text == "null":
            return None
        if text == "[":
            self.at -= 1
            return self.array()
        if text == "scale":
            return self.scale()
        raise AssertionError(f"unsupported token {text!r}")

    def scale(self):
        self.expect("(")
        value = self.primary()
        self.expect(",")
        self.expect("threshold")
        self.expect(",")
        outputs = self.array()
        self.expect(",")
        self.expect("domain")
        self.expect("=")
        domain = self.array()
        self.expect(")")
        if value is None:
            return None
        return outputs[bisect.bisect_right(domain, float(value))]


def evaluate(source: str, properties: dict):
    evaluator = _Evaluator(source, properties)
    result = evaluator.ternary()
    assert evaluator.at == len(evaluator.tokens), f"trailing input in {source!r}"
    return result


def _oracle_layer(field_type: str, values: list, renderer_for) -> object:
    layer = qgis_core.QgsVectorLayer(
        f"Point?crs=EPSG:4326&field=n:integer&field=v:{field_type}", "oracle", "memory"
    )
    features = []
    for number, value in enumerate(values):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttributes([number, value])
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(number, 0))
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.setRenderer(renderer_for())
    return layer


def _qgis_colours(layer) -> dict[int, str | None]:
    """The colour QGIS itself draws each feature with, `None` when it draws none."""
    renderer = layer.renderer()
    context = qgis_core.QgsRenderContext()
    context.setExpressionContext(qgis_core.QgsExpressionContext())
    renderer.startRender(context, layer.fields())
    colours = {}
    for feature in layer.getFeatures():
        context.expressionContext().setFeature(feature)
        symbol = renderer.symbolForFeature(feature, context)
        drawn = symbol is not None and renderer.willRenderFeature(feature, context)
        colours[feature["n"]] = symbol.color().name() if drawn else None
    renderer.stopRender(context)
    return colours


def _exported_colours(layer) -> dict[int, str | None]:
    """The colour the exported data and style give each feature, by evaluating
    the style expression on the properties that actually ship."""
    exported = read_layer(layer, FidelityReportBuilder())
    expression = fill_expression(exported.renderer, exported.geometry_kind)
    colours = {number: None for number in _qgis_colours(layer)}
    for feature in exported.geojson["features"]:
        properties = feature["properties"]
        colours[properties["n"]] = evaluate(expression, properties).strip("'")
    return colours


RED, GREEN, BLUE, GREY = "#ff0000", "#00ff00", "#0000ff", "#111111"

# (field type, feature values, [(category value, colour, enabled)]).
CATEGORIZED_CASES = {
    "plain": (
        "string",
        ["a", "b", "c", None, ""],
        [("a", RED, True), ("b", GREEN, True)],
    ),
    "unmatched-and-null-undrawn": (
        "string",
        ["a", "x", None, ""],
        [("a", RED, True)],
    ),
    "catch-all-takes-everything-else": (
        "string",
        ["a", "x", None, ""],
        [("a", RED, True), ("", BLUE, True)],
    ),
    "catch-all-is-not-literal-empty-string": (
        "string",
        ["a", "b", "c"],
        [("a", RED, True), ("", BLUE, True)],
    ),
    "disabled-catch-all-excludes-unmatched": (
        "string",
        ["a", "x", None, ""],
        [("a", RED, True), ("", BLUE, False)],
    ),
    "null-class-is-the-catch-all": (
        "string",
        ["a", "x", None, ""],
        [("a", RED, True), (None, BLUE, True)],
    ),
    "disabled-null-class-excludes-null": (
        "string",
        ["a", "x", None, ""],
        [("a", RED, True), (None, BLUE, False)],
    ),
    "disabled-class-hides-its-values": (
        "string",
        ["a", "b", None],
        [("a", RED, False), ("b", GREEN, True)],
    ),
    "disabled-class-is-not-caught-by-catch-all": (
        "string",
        ["a", "b", None],
        [("a", RED, False), ("", BLUE, True)],
    ),
    "later-category-replaces-earlier": (
        "string",
        ["a", "b"],
        [("a", RED, True), ("a", GREEN, False)],
    ),
    "later-enabled-category-replaces-disabled": (
        "string",
        ["a", "b"],
        [("a", RED, False), ("a", GREEN, True)],
    ),
    "last-empty-class-wins": (
        "string",
        ["a", "x", None],
        [("", RED, True), (None, GREEN, True), ("a", BLUE, True)],
    ),
    "grouped-values": (
        "string",
        ["a", "b", "c", None],
        [(["a", "b"], RED, True), ("c", GREEN, True)],
    ),
    "grouped-values-with-catch-all": (
        "string",
        ["a", "b", "c", None],
        [(["a", "b"], RED, True), ("", BLUE, True)],
    ),
    "grouped-values-disabled": (
        "string",
        ["a", "b", "c"],
        [(["a", "b"], RED, False), ("c", GREEN, True)],
    ),
    "grouped-value-overridden-by-later-single": (
        "string",
        ["a", "b", "c"],
        [(["a", "b"], RED, True), ("a", GREEN, False)],
    ),
    "case-matters": ("string", ["a", "A"], [("a", RED, True)]),
    "integers": ("integer", [1, 2, 3, None], [(1, RED, True), (2, GREEN, True)]),
    "integer-zero-is-not-null": ("integer", [0, 2, None], [(0, RED, True)]),
    "doubles": (
        "double",
        [1.0, 2.5, 3.0, None],
        [(1, RED, True), (2.5, GREEN, True), (3.0, BLUE, True)],
    ),
    "integer-field-null-catch-all": (
        "integer",
        [1, 2, None],
        [(1, RED, True), (None, GREEN, True)],
    ),
}


def _categorized(cases_key: str):
    field_type, values, categories = CATEGORIZED_CASES[cases_key]

    def renderer():
        return qgis_core.QgsCategorizedSymbolRenderer(
            "v",
            [
                qgis_core.QgsRendererCategory(
                    value, marker_symbol(colour, 2.0 + number), str(value), on
                )
                for number, (value, colour, on) in enumerate(categories)
            ],
        )

    return _oracle_layer(field_type, values, renderer)


class TestCategorizedMatchesQgis:
    @pytest.mark.parametrize("case", CATEGORIZED_CASES)
    def test_every_feature_gets_the_class_qgis_gives_it(self, project, case) -> None:
        layer = _categorized(case)

        assert _exported_colours(layer) == _qgis_colours(layer)

    @pytest.mark.parametrize("case", CATEGORIZED_CASES)
    def test_a_size_accessor_picks_the_same_class(self, project, case) -> None:
        _assert_sizes_follow_colours(_categorized(case))

    def test_a_catch_all_is_the_fallback_not_an_equality(self, project) -> None:
        layer = _categorized("catch-all-takes-everything-else")
        exported = read_layer(layer, FidelityReportBuilder())
        expression = fill_expression(exported.renderer, exported.geometry_kind)

        assert expression == f"$v == 'a' ? '{RED}' : '{BLUE}'"

    def test_grouped_values_keep_the_legend_derivable(self, project) -> None:
        layer = _categorized("grouped-values")
        exported = read_layer(layer, FidelityReportBuilder())
        legend = derive_runtime_legend(
            fill_expression(exported.renderer, exported.geometry_kind)
        )

        assert legend is not None
        assert legend.labels == ("a", "b", "c")


# (field type, feature values, [(lower, upper, colour, enabled)]).
_EDGES = [-1.0, 0.0, 5.0, 10.0, 10.0000001, 15.0, 20.0, 25.0, 30.0, 100.0, None]
GRADUATED_CASES = {
    "touching-ranges-boundary-stays-low": (
        "double",
        _EDGES,
        [(0, 10, RED, True), (10, 20, GREEN, True)],
    ),
    "gap-between-ranges-draws-nothing": (
        "double",
        _EDGES,
        [(0, 10, RED, True), (15, 20, GREEN, True)],
    ),
    "disabled-range-is-not-drawn": (
        "double",
        _EDGES,
        [(0, 10, RED, False), (10, 20, GREEN, True)],
    ),
    "disabled-range-in-the-middle": (
        "double",
        _EDGES,
        [(0, 10, RED, True), (10, 20, GREEN, False), (20, 30, BLUE, True)],
    ),
    "unsorted-ranges-first-match-wins": (
        "double",
        _EDGES,
        [(10, 20, GREEN, True), (0, 10, RED, True)],
    ),
    "overlapping-ranges-first-match-wins": (
        "double",
        [0.1, 0.2, 0.5, 0.9],
        [(0, 0.5, RED, True), (0.2, 1, GREEN, True)],
    ),
    "single-range": ("double", _EDGES, [(0, 10, RED, True)]),
    "reversed-range-matches-nothing": (
        "double",
        [5.0, 15.0],
        [(10, 0, RED, True), (10, 20, GREEN, True)],
    ),
    "integers-on-the-boundary": (
        "integer",
        [0, 5, 10, 11, 20, 21, None],
        [(0, 10, RED, True), (10, 20, GREEN, True)],
    ),
    "tiny-threshold-is-not-rounded-to-zero": (
        "double",
        [0.0, 5e-8, 1e-7, 1.5e-7, 1.0, 20.0],
        [(0, 1e-7, RED, True), (1e-7, 20, GREEN, True)],
    ),
    "adjacent-doubles-are-told-apart": (
        "double",
        [0.3, 0.30000000000000004, 0.29999999999999993],
        [(0, 0.3, RED, True), (0.3, 1, GREEN, True)],
    ),
    "negative-values": (
        "double",
        [-30.0, -20.0, -10.5, -10.0, -0.0],
        [(-30, -20, RED, True), (-20, -10, GREEN, True)],
    ),
}


def _graduated(case_key: str):
    field_type, values, ranges = GRADUATED_CASES[case_key]

    def renderer():
        return qgis_core.QgsGraduatedSymbolRenderer(
            "v",
            [
                qgis_core.QgsRendererRange(
                    low, high, marker_symbol(colour, 2.0 + number), "", on
                )
                for number, (low, high, colour, on) in enumerate(ranges)
            ],
        )

    return _oracle_layer(field_type, values, renderer)


def _assert_sizes_follow_colours(layer) -> None:
    exported = read_layer(layer, FidelityReportBuilder())
    renderer = exported.renderer
    classes = renderer.categories or renderer.classes
    radius_by_colour = {
        f"#{c.symbol.fill_color.r:02x}{c.symbol.fill_color.g:02x}"
        f"{c.symbol.fill_color.b:02x}": c.symbol.radius
        for c in classes
    }
    expression = numeric_expression(renderer, "radius")
    if expression is None:
        return
    colours = _exported_colours(layer)
    for feature in exported.geojson["features"]:
        properties = feature["properties"]
        assert evaluate(expression, properties) == pytest.approx(
            radius_by_colour[colours[properties["n"]]], abs=1e-6
        ), properties


class TestGraduatedMatchesQgis:
    @pytest.mark.parametrize("case", GRADUATED_CASES)
    def test_every_feature_gets_the_class_qgis_gives_it(self, project, case) -> None:
        layer = _graduated(case)

        assert _exported_colours(layer) == _qgis_colours(layer)

    @pytest.mark.parametrize("case", GRADUATED_CASES)
    def test_a_size_accessor_picks_the_same_class(self, project, case) -> None:
        """Sizes classify through the same chain the colours do."""
        _assert_sizes_follow_colours(_graduated(case))

    def test_touching_ranges_stay_a_threshold_scale_with_a_legend(
        self, project
    ) -> None:
        layer = _graduated("touching-ranges-boundary-stays-low")
        exported = read_layer(layer, FidelityReportBuilder())
        expression = fill_expression(exported.renderer, exported.geometry_kind)

        assert expression.startswith("scale($v, threshold, ")
        legend = derive_runtime_legend(expression)
        assert legend is not None and legend.labels == ("< 10", "\u2265 10")

    def test_a_threshold_is_written_in_full_precision(self, project) -> None:
        layer = _graduated("tiny-threshold-is-not-rounded-to-zero")
        exported = read_layer(layer, FidelityReportBuilder())
        expression = fill_expression(exported.renderer, exported.geometry_kind)

        assert "domain=[1.0000000000000001e-07]" in expression or (
            "domain=[1.0000000000000002e-07]" in expression
        ), expression

    def test_disabled_range_is_reported(self, project) -> None:
        layer = _graduated("disabled-range-is-not-drawn")
        report = FidelityReportBuilder()
        read_layer(layer, report)

        assert any(
            "range is switched off" in i.detail
            for i in report.by_status(FidelityStatus.APPROXIMATED)
        )


RENDER_UNITS = [
    "Millimeters",
    "Points",
    "Pixels",
    "Inches",
    "MapUnits",
    "MetersInMapUnits",
]


def _unit_oracle_context(mupp: float):
    """QGIS's own unit conversion at 96 dpi in a metre CRS (painter unit = CSS px)."""
    context = qgis_core.QgsRenderContext()
    context.setScaleFactor(MM_TO_PIXELS)
    context.setMapToPixel(qgis_core.QgsMapToPixel(mupp))
    context.setExtent(qgis_core.QgsRectangle(0, 0, 7000, 4900))
    area = qgis_core.QgsDistanceArea()
    area.setSourceCrs(
        qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"),
        qgis_core.QgsProject.instance().transformContext(),
    )
    context.setDistanceArea(area)
    return context


class TestSymbolUnits:
    """Sizes follow the symbol layer's unit, checked against QGIS's own conversion.

    The web runtime's px are CSS px at 96 dpi, so the oracle context uses 96 / 25.4
    painter units per millimetre. Map units are fixed at the layer's reference
    scale: here a 7000 m wide extent in a 1000 px view, 7 m per pixel.
    """

    @staticmethod
    def _layer(make_memory_layer):
        layer = make_memory_layer(
            "pts",
            crs="EPSG:3857",
            features=[("a", [0.0, 0.0]), ("b", [7000.0, 4900.0])],
        )
        layer.updateExtents()
        return layer

    @pytest.mark.parametrize("unit_name", RENDER_UNITS)
    def test_size_stroke_and_offset_match_qgis(
        self, qgis_app, make_memory_layer, unit_name
    ) -> None:
        unit = getattr(qgis_core.Qgis.RenderUnit, unit_name)
        layer = self._layer(make_memory_layer)
        symbol = qgis_core.QgsMarkerSymbol.createSimple(
            {"name": "circle", "size": "8", "outline_width": "1"}
        )
        symbol_layer = symbol.symbolLayer(0)
        symbol_layer.setSizeUnit(unit)
        symbol_layer.setStrokeWidthUnit(unit)
        symbol_layer.setOffset(QtCore.QPointF(2, 3))
        symbol_layer.setOffsetUnit(unit)
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(symbol))

        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report).symbol

        oracle = _unit_oracle_context(7.0)
        convert = oracle.convertToPainterUnits
        assert spec.radius * 2 == pytest.approx(convert(8, unit), rel=1e-4)
        assert spec.stroke_width == pytest.approx(convert(1, unit), rel=1e-4)
        assert spec.offset_x == pytest.approx(convert(2, unit), rel=1e-4)
        assert spec.offset_y == pytest.approx(convert(3, unit), rel=1e-4)

        approximated = [
            item
            for item in report.items
            if item.status is FidelityStatus.APPROXIMATED and "map units" in item.detail
        ]
        assert bool(approximated) is (unit_name in ("MapUnits", "MetersInMapUnits"))

    def test_an_8px_marker_is_8px_not_30(self, qgis_app) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"name": "circle", "size": "8"})
        symbol.symbolLayer(0).setSizeUnit(qgis_core.Qgis.RenderUnit.Pixels)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "s")
        assert spec.radius == pytest.approx(4.0)

    def test_line_width_unit_is_honoured(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple({"line_width": "2"})
        symbol.symbolLayer(0).setWidthUnit(qgis_core.Qgis.RenderUnit.Points)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "s")
        assert spec.stroke_width == pytest.approx(2 * 96 / 72)

    def test_map_units_without_a_scale_are_reported_not_hidden(self, qgis_app) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"size": "8"})
        symbol.symbolLayer(0).setSizeUnit(qgis_core.Qgis.RenderUnit.MapUnits)
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "s")
        assert any(i.status is FidelityStatus.APPROXIMATED for i in report.items)

    def test_the_reference_scale_fits_the_extent_into_the_view(
        self, qgis_app, make_memory_layer
    ) -> None:
        scale = reference_scale(self._layer(make_memory_layer))
        assert scale == MapUnitScale(metres_per_pixel=7.0, metres_per_map_unit=1.0)

    def test_a_single_point_layer_has_no_reference_scale(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer("one", features=[("a", [1.0, 1.0])])
        assert reference_scale(layer) is None

    def test_data_defined_size_is_reported(self, qgis_app) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"size": "4"})
        symbol.symbolLayer(0).setDataDefinedProperty(
            qgis_core.QgsSymbolLayer.Property.Size,
            qgis_core.QgsProperty.fromExpression('"v" * 2'),
        )
        report = FidelityReportBuilder()
        translate_symbol(symbol, report, "s")
        assert any("from data per feature" in i.detail for i in report.items)


class TestSymbolOpacity:
    """Symbol opacity multiplies colour alpha exactly once, as QGIS paints it."""

    @staticmethod
    def _centre_alpha(symbol) -> float:
        image = symbol.asImage(QtCore.QSize(41, 41))
        return image.pixelColor(20, 20).alphaF()

    @pytest.mark.parametrize("opacity", [1.0, 0.5, 0.25])
    def test_exported_alpha_is_what_qgis_paints(self, qgis_app, opacity) -> None:
        symbol = qgis_core.QgsMarkerSymbol.createSimple(
            {"name": "circle", "size": "10", "color": "255,0,0,204"}
        )
        symbol.setOpacity(opacity)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "s")
        assert spec.fill_color.a == pytest.approx(self._centre_alpha(symbol), abs=0.01)
        assert spec.fill_color.a == pytest.approx(0.8 * opacity, abs=0.01)

    def test_stroke_alpha_carries_the_opacity_too(self, qgis_app) -> None:
        symbol = qgis_core.QgsLineSymbol.createSimple({"color": "0,0,255"})
        symbol.setOpacity(0.4)
        spec = translate_symbol(symbol, FidelityReportBuilder(), "s")
        assert spec.stroke_color.a == pytest.approx(0.4)

    def test_layer_opacity_is_not_folded_into_the_symbol(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer("pts", features=[("a", [0.0, 0.0])])
        layer.setOpacity(0.5)
        exported = read_layer(layer, FidelityReportBuilder())
        assert exported.opacity == pytest.approx(0.5)
        assert exported.renderer.symbol.fill_color.a == pytest.approx(1.0)
