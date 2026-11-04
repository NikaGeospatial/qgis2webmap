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
    GroundSize,
    RendererKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer
from nika_onlymap_exporter.core.manifest_builder import (
    fill_expression,
    line_color_expression,
    numeric_expression,
)
from nika_onlymap_exporter.core.render_units import (
    COMMON_UNITS_PER_WEB_MERCATOR_METRE,
    MapUnitScale,
)
from nika_onlymap_exporter.core.renderer_translator import (
    HAIRLINE_PIXELS,
    MM_TO_PIXELS,
    class_symbols,
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
    def test_a_heatmap_is_named_in_the_report(self, project, make_memory_layer) -> None:
        layer = make_memory_layer("pts", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsHeatmapRenderer())

        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)

        assert spec.kind is RendererKind.UNSUPPORTED
        assert spec.unsupported_reason == "QgsHeatmapRenderer"
        unsupported = report.by_status(FidelityStatus.UNSUPPORTED)
        assert any("QgsHeatmapRenderer" in i.detail for i in unsupported)


RULE_RED, RULE_GREEN, RULE_BLUE = "#ff0000", "#00ff00", "#0000ff"


def _rule_layer(rules, field_type: str = "string", values=None):
    """A layer on field `v` drawn by `rules`: (filter, colour, active, label)."""
    values = values or ["Index", "Intermediate", "Other", None]

    def renderer():
        root = qgis_core.QgsRuleBasedRenderer.Rule(None)
        for number, (expression, colour, active, label) in enumerate(rules):
            rule = qgis_core.QgsRuleBasedRenderer.Rule(
                marker_symbol(colour, 2.0 + number), 0, 0, expression, label
            )
            rule.setActive(active)
            root.appendChild(rule)
        return qgis_core.QgsRuleBasedRenderer(root)

    return _oracle_layer(field_type, values, renderer)


CONTOURS = [
    ("\"v\" = 'Index'", RULE_RED, True, "Index contour"),
    ("\"v\" = 'Intermediate'", RULE_GREEN, True, "Intermediate contour"),
]


class TestRuleBasedAsCategories:
    @pytest.mark.parametrize(
        "rules",
        [
            CONTOURS,
            [*CONTOURS, ("ELSE", RULE_BLUE, True, "Rest")],
            [("\"v\" IN ('Index', 'Other')", RULE_RED, True, "A"), CONTOURS[1]],
            [CONTOURS[0], (CONTOURS[1][0], RULE_GREEN, False, "off")],
        ],
        ids=["two-rules", "else-rule", "in-list", "switched-off-rule"],
    )
    def test_every_feature_gets_the_colour_qgis_gives_it(self, project, rules) -> None:
        layer = _rule_layer(rules)

        assert _exported_colours(layer) == _qgis_colours(layer)

    def test_it_is_a_categorized_spec_with_the_rule_labels(self, project) -> None:
        spec = translate_renderer(_rule_layer(CONTOURS), FidelityReportBuilder())

        assert spec.kind is RendererKind.CATEGORIZED
        assert spec.field_name == "v"
        assert [c.value for c in spec.categories] == ["Index", "Intermediate"]
        assert [c.label for c in spec.categories] == [
            "Index contour",
            "Intermediate contour",
        ]
        assert spec.unmatched_hidden is True

    def test_a_scale_range_is_reported_not_kept(self, project) -> None:
        layer = _rule_layer(CONTOURS)
        rule = layer.renderer().rootRule().children()[1]
        rule.setMaximumScale(1.0)
        rule.setMinimumScale(150000.0)
        report = FidelityReportBuilder()
        translate_renderer(layer, report)

        assert any(
            "scale range" in item.detail
            for item in report.by_status(FidelityStatus.APPROXIMATED)
        )

    @pytest.mark.parametrize(
        "rules",
        [
            [('"v" > 3', RULE_RED, True, "range")],
            [('"v" = \'a\' AND "n" = 1', RULE_RED, True, "and")],
            [
                ("\"v\" = 'a'", RULE_RED, True, "a"),
                ("\"n\" = 'b'", RULE_GREEN, True, "b"),
            ],
            [
                ("\"v\" = 'a'", RULE_RED, True, "a"),
                ("\"v\" = 'a'", RULE_GREEN, True, "again"),
            ],
            [("\"v\" = 'a'", RULE_RED, True, "a"), ("", RULE_GREEN, True, "all")],
            [("\"nope\" = 'a'", RULE_RED, True, "no field")],
        ],
        ids=["range", "and", "two-fields", "same-value-twice", "no-filter", "no-field"],
    )
    def test_any_other_rules_get_a_class_per_rule(self, project, rules) -> None:
        """Not one field's values: QGIS matches them as the features are read
        (see `TestRuleTree`)."""
        report = FidelityReportBuilder()
        spec = translate_renderer(_rule_layer(rules), report)

        assert spec.kind is RendererKind.CATEGORIZED
        assert spec.field_generated
        assert [c.value for c in spec.categories] == [rule[3] for rule in rules]
        assert not report.by_status(FidelityStatus.UNSUPPORTED)

    def test_the_qgis_symbols_pair_with_the_classes(self, project) -> None:
        layer = _rule_layer([*CONTOURS, ("\"v\" = 'x'", RULE_BLUE, False, "off")])
        spec = translate_renderer(layer, FidelityReportBuilder())

        assert len(class_symbols(layer.renderer())) == len(spec.categories) == 2


YELLOW, MAGENTA, CYAN, SLATE = "#ffff00", "#ff00ff", "#00ffff", "#808080"

# One square per (v, c), 8 units wide and 10 apart along x.
TREE_VALUES = [
    (1, "x"),
    (3, "x"),
    (7, "y"),
    (9, "x"),
    (None, "z"),
    (3, "y"),
    (None, "y"),
    (7, "z"),
]


def _tree_layer(rules, extra_field: str | None = None):
    """Squares of `TREE_VALUES` drawn by the rule tree `rules`, each rule
    `(label, filter, colour or None for no symbol, active, children)`."""
    fields = "field=n:integer&field=v:integer&field=c:string"
    if extra_field:
        fields += f"&field={extra_field}:string"
    layer = qgis_core.QgsVectorLayer(
        f"Polygon?crs=EPSG:3857&{fields}", "tree", "memory"
    )
    features = []
    for number, (value, kind) in enumerate(TREE_VALUES):
        feature = qgis_core.QgsFeature(layer.fields())
        attributes = [number, value, kind]
        feature.setAttributes([*attributes, "real"] if extra_field else attributes)
        feature.setGeometry(
            qgis_core.QgsGeometry.fromRect(
                qgis_core.QgsRectangle(number * 10, 0, number * 10 + 8, 8)
            )
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()

    def append(parent, specs):
        for label, expression, colour, active, children in specs:
            symbol = (
                qgis_core.QgsFillSymbol.createSimple(
                    {"color": colour, "outline_style": "no"}
                )
                if colour
                else None
            )
            rule = qgis_core.QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression, label)
            rule.setActive(active)
            parent.appendChild(rule)
            append(rule, children)

    root = qgis_core.QgsRuleBasedRenderer.Rule(None)
    append(root, rules)
    layer.setRenderer(qgis_core.QgsRuleBasedRenderer(root))
    return layer


def _drawn_colours(layer) -> dict[int, str | None]:
    """The colour QGIS draws on top in the middle of each square, `None`
    where it draws nothing - its own drawing, not its API's answers."""
    count = len(TREE_VALUES)
    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    settings.setOutputSize(QtCore.QSize(40 * count, 40))
    settings.setExtent(qgis_core.QgsRectangle(-1, -1, 10 * count - 1, 9))
    settings.setBackgroundColor(QtGui.QColor("#ffffff"))
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    colours: dict[int, str | None] = {}
    for feature in layer.getFeatures():
        middle = settings.mapToPixel().transform(
            feature.geometry().centroid().asPoint()
        )
        colour = QtGui.QColor(image.pixel(int(middle.x()), int(middle.y()))).name()
        colours[feature["n"]] = None if colour == "#ffffff" else colour
    return colours


def _classes(exported) -> dict[int, str | None]:
    """The class the exported data gives each square, `None` once dropped."""
    field = exported.renderer.field_name
    classes: dict[int, str | None] = {n: None for n in range(len(TREE_VALUES))}
    for feature in exported.geojson["features"]:
        classes[feature["properties"]["n"]] = feature["properties"][field]
    return classes


NESTED = [
    ("Above 1", '"v" > 1', RULE_RED, True, []),
    (
        "Kind x",
        "\"c\" = 'x'",
        None,
        True,
        [
            ("Small x", '"v" < 5', YELLOW, True, []),
            ("Other x", "ELSE", MAGENTA, True, []),
        ],
    ),
    ("Rest", "ELSE", CYAN, True, []),
]
PARENT_AND_CHILD = [
    ("Kind x", "\"c\" = 'x'", SLATE, True, [("Big x", '"v" > 5', RULE_BLUE, True, [])])
]
OVERLAPPING = [
    ("Above 1", '"v" > 1', RULE_RED, True, []),
    ("Above 5", '"v" > 5', RULE_BLUE, True, []),
]
SWITCHED_OFF = [
    ("Off", "\"c\" = 'y'", RULE_GREEN, False, []),
    (
        "Off group",
        "\"c\" = 'x'",
        None,
        False,
        [("Under it", '"v" > 0', RULE_RED, True, [])],
    ),
    ("Rest", "ELSE", CYAN, True, []),
]
NO_FILTER = [
    ("Broken", '"nope" = 1', RULE_RED, True, []),
    ("Everything", "", RULE_GREEN, True, []),
    ("Never", "ELSE", RULE_BLUE, True, []),
]
ALL_OFF = [
    ("Off", "\"c\" = 'y'", RULE_GREEN, False, []),
    (
        "Group",
        "\"c\" = 'x'",
        None,
        True,
        [("Off under it", '"v" > 0', RULE_RED, False, [])],
    ),
    ("Rest", "ELSE", CYAN, False, []),
]


class TestRuleTree:
    """Any rule-based renderer, as one class per rule that draws: which rule
    draws each feature is QGIS's own answer, written into the data. The oracle
    is QGIS's drawing - the colour on top in the middle of each square."""

    @pytest.mark.parametrize(
        "rules",
        [NESTED, PARENT_AND_CHILD, OVERLAPPING, SWITCHED_OFF, NO_FILTER, ALL_OFF],
        ids=[
            "nested-and-else",
            "parent-and-child",
            "overlapping",
            "off",
            "no-filter",
            "all-off",
        ],
    )
    def test_every_feature_gets_the_colour_qgis_draws_on_top(
        self, project, rules
    ) -> None:
        layer = _tree_layer(rules)

        assert _exported_colours(layer) == _drawn_colours(layer)

    def test_each_feature_carries_the_rule_that_draws_it(self, project) -> None:
        exported = read_layer(_tree_layer(NESTED), FidelityReportBuilder())

        assert exported.renderer.field_name == "Style"
        assert _classes(exported) == {
            0: "Small x",
            1: "Small x",  # Above 1 too, under it
            2: "Above 1",
            3: "Other x",  # Above 1 too, under it
            4: "Rest",
            5: "Above 1",
            6: "Rest",
            7: "Above 1",
        }

    def test_switched_off_rules_draw_nothing_and_keep_else_away(self, project) -> None:
        exported = read_layer(_tree_layer(SWITCHED_OFF), FidelityReportBuilder())

        assert [c.label for c in exported.renderer.categories] == ["Rest"]
        # What the switched-off rules pick is not drawn, and not by ELSE either.
        assert _classes(exported) == {
            0: None,
            1: None,
            2: None,
            3: None,
            4: "Rest",
            5: None,
            6: None,
            7: "Rest",
        }

    def test_a_tree_where_nothing_draws_exports_nothing_and_says_so(
        self, project
    ) -> None:
        """Not every feature in the default style: QGIS draws none of them."""
        from nika_onlymap_exporter.core.export_ir import ExportProject, Extent
        from nika_onlymap_exporter.core.manifest_builder import build_manifest

        layer = _tree_layer(ALL_OFF)
        assert set(_drawn_colours(layer).values()) == {None}
        report = FidelityReportBuilder()
        exported = read_layer(layer, report)

        assert exported.geojson["features"] == []
        (row,) = report.by_status(FidelityStatus.UNSUPPORTED)
        assert "none is drawn, as in QGIS" in row.detail
        assert "will show nothing" in row.detail
        assert "default style" not in " ".join(i.detail for i in report.items)
        # It still makes a map: the layer is there, with nothing to draw.
        manifest = build_manifest(
            ExportProject(
                title="Off",
                layers=(exported,),
                extent=Extent(west=0, south=0, east=1, north=1),
            )
        )
        assert f'id="{exported.layer_id}"' in manifest

    def test_the_legend_lists_the_rules_that_draw_by_their_labels(
        self, project
    ) -> None:
        spec = translate_renderer(_tree_layer(NESTED), FidelityReportBuilder())

        labels = ["Above 1", "Small x", "Other x", "Rest"]
        assert [c.label for c in spec.categories] == labels
        # The built-in legend lists a class by its value: the same labels.
        runtime = derive_runtime_legend(fill_expression(spec, GeometryKind.POLYGON))
        assert runtime is not None
        assert list(runtime.labels) == labels

    def test_rules_qgis_lists_alike_are_told_apart(self, project) -> None:
        rules = [
            ("Same", '"v" > 5', RULE_RED, True, []),
            ("Same", '"v" < 2', RULE_BLUE, True, []),
            ("", "ELSE", RULE_GREEN, True, []),
        ]
        layer = _tree_layer(rules)
        spec = translate_renderer(layer, FidelityReportBuilder())

        assert [c.label for c in spec.categories] == ["Same", "Same (2)", "ELSE"]
        assert _exported_colours(layer) == _drawn_colours(layer)

    def test_a_feature_two_rules_draw_is_counted(self, project) -> None:
        report = FidelityReportBuilder()
        read_layer(_tree_layer(OVERLAPPING), report)

        assert any(
            item.detail.startswith("3 features are drawn by more than one rule")
            for item in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_the_class_property_does_not_take_a_fields_name(self, project) -> None:
        exported = read_layer(
            _tree_layer(OVERLAPPING, extra_field="Style"), FidelityReportBuilder()
        )

        assert exported.renderer.field_name == "Style_2"
        properties = [f["properties"] for f in exported.geojson["features"]]
        assert {p["Style"] for p in properties} == {"real"}
        assert {p["Style_2"] for p in properties} == {"Above 1", "Above 5"}

    def test_the_popup_does_not_show_the_class(self, project) -> None:
        exported = read_layer(_tree_layer(NESTED), FidelityReportBuilder())

        assert exported.popup.enabled
        shown = {field.name for field in exported.popup.visible_fields}
        assert shown == {"n", "v", "c"}

    def test_the_fields_the_filters_read_are_read_but_not_written(
        self, project, tmp_path
    ) -> None:
        """No popup keeps no field but the class - and the filters' fields
        must still be read for QGIS to match the rules. From a GeoPackage:
        a memory layer hands back every field whatever is asked for."""
        memory = _tree_layer(NESTED)
        path = str(tmp_path / "tree.gpkg")
        options = qgis_core.QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        error = qgis_core.QgsVectorFileWriter.writeAsVectorFormatV3(
            memory, path, qgis_core.QgsCoordinateTransformContext(), options
        )
        assert error[0] == qgis_core.QgsVectorFileWriter.WriterError.NoError
        layer = qgis_core.QgsVectorLayer(path, "tree", "ogr")
        layer.setRenderer(memory.renderer().clone())
        expected = _classes(read_layer(memory, FidelityReportBuilder()))

        bare = read_layer(layer, FidelityReportBuilder(), with_popup=False)

        properties = [f["properties"] for f in bare.geojson["features"]]
        assert all(set(p) == {"Style"} for p in properties)
        assert [p["Style"] for p in properties] == [
            value for value in expected.values() if value is not None
        ]

    def test_the_qgis_symbols_pair_with_the_classes(self, project) -> None:
        layer = _tree_layer([*NESTED, ("Off", '"v" = 3', RULE_GREEN, False, [])])
        spec = translate_renderer(layer, FidelityReportBuilder())

        symbols = class_symbols(layer.renderer())
        assert [s.color().name() for s in symbols] == [RULE_RED, YELLOW, MAGENTA, CYAN]
        assert len(symbols) == len(spec.categories)

    def test_a_root_symbol_draws_every_feature(self, project) -> None:
        layer = _tree_layer([])
        root = layer.renderer().rootRule()
        root.setSymbol(qgis_core.QgsFillSymbol.createSimple({"color": RULE_GREEN}))

        assert set(_exported_colours(layer).values()) == {RULE_GREEN}

    def test_a_scale_range_is_reported_and_drawn_at_every_zoom(self, project) -> None:
        layer = _tree_layer(OVERLAPPING)
        rule = layer.renderer().rootRule().children()[1]
        rule.setMaximumScale(1.0)
        rule.setMinimumScale(150000.0)
        report = FidelityReportBuilder()
        exported = read_layer(layer, report)

        # Inside the range, as QGIS draws it; outside it QGIS draws the rule
        # under it instead, which the web map does not.
        assert _exported_colours(layer) == _drawn_colours(layer)
        assert "Above 5" in _classes(exported).values()
        assert any(
            "The rule 'Above 5' only draws within a scale range" in item.detail
            for item in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_a_filter_on_the_map_view_is_reported(self, project) -> None:
        rules = [("Zoomed in", '"v" > 1 AND @map_scale < 5000', RULE_RED, True, [])]
        report = FidelityReportBuilder()
        exported = read_layer(_tree_layer(rules), report)

        assert any(
            "The rule 'Zoomed in' reads @map_scale" in item.detail
            for item in report.by_status(FidelityStatus.APPROXIMATED)
        )
        # Worked out with no map view, where @map_scale has no value.
        assert set(_classes(exported).values()) == {None}

    def test_symbol_levels_are_reported(self, project) -> None:
        layer = _tree_layer(OVERLAPPING)
        layer.renderer().setUsingSymbolLevels(True)
        report = FidelityReportBuilder()
        translate_renderer(layer, report)

        assert any(
            item.detail.startswith("Symbol levels are not kept")
            for item in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_nesting_is_reported_only_when_there_is_some(self, project) -> None:
        nested, flat = FidelityReportBuilder(), FidelityReportBuilder()
        translate_renderer(_tree_layer(NESTED), nested)
        translate_renderer(_tree_layer(OVERLAPPING), flat)

        def says(report) -> bool:
            return any(
                item.detail.startswith("Nested rules are listed")
                for item in report.by_status(FidelityStatus.APPROXIMATED)
            )

        assert says(nested) and not says(flat)


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
        # `willRenderFeature` first: a rule-based renderer matches its rules there.
        will_render = renderer.willRenderFeature(feature, context)
        symbol = renderer.symbolForFeature(feature, context)
        if symbol is None and will_render:
            symbols = renderer.symbolsForFeature(feature, context)
            symbol = symbols[0] if symbols else None
        drawn = symbol is not None and will_render
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


class TestGroundSizes:
    """Map-unit sizes the runtime can hold become true ground sizes.

    The oracle is QGIS's own conversion: the pixels a 100 m marker covers at two
    scales, from `QgsRenderContext.convertToPainterUnits`. A ground size must
    equal that at every scale, which is what zooming with the map means.
    """

    @staticmethod
    def _layer(make_memory_layer, crs: str):
        layer = make_memory_layer(
            "pts", crs=crs, features=[("a", [0.0, 0.0]), ("b", [7000.0, 4900.0])]
        )
        layer.updateExtents()
        return layer

    @staticmethod
    def _marker(unit_name: str, size: float = 100.0, stroke_unit: str = "Millimeters"):
        symbol = qgis_core.QgsMarkerSymbol.createSimple(
            {"name": "circle", "size": str(size), "outline_width": "10"}
        )
        symbol_layer = symbol.symbolLayer(0)
        symbol_layer.setSizeUnit(getattr(qgis_core.Qgis.RenderUnit, unit_name))
        symbol_layer.setStrokeWidthUnit(getattr(qgis_core.Qgis.RenderUnit, stroke_unit))
        return symbol

    def _translate(self, layer, symbol):
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(symbol))
        report = FidelityReportBuilder()
        return translate_renderer(layer, report).symbol, report

    @staticmethod
    def _approximated_sizes(report) -> list:
        return [
            i
            for i in report.items
            if i.status is FidelityStatus.APPROXIMATED and "map units" in i.detail
        ]

    @pytest.mark.parametrize("crs", ["EPSG:32648", "EPSG:2263"])
    def test_metres_on_the_ground_are_metres(
        self, qgis_app, make_memory_layer, crs
    ) -> None:
        layer = self._layer(make_memory_layer, crs)
        spec, report = self._translate(layer, self._marker("MetersInMapUnits"))
        assert spec.radius_ground == GroundSize(50.0, "meters")
        assert self._approximated_sizes(report) == []

    def test_metres_in_degrees_are_metres_on_the_projects_ellipsoid(
        self, qgis_app, make_memory_layer
    ) -> None:
        """QGIS can turn metres into degrees only on an ellipsoid."""
        project = qgis_core.QgsProject.instance()
        layer = self._layer(make_memory_layer, "EPSG:4326")
        try:
            # The project ignores an ellipsoid until it has a CRS.
            project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"))
            project.setEllipsoid("EPSG:7030")
            assert project.ellipsoid() == "EPSG:7030"
            spec, report = self._translate(layer, self._marker("MetersInMapUnits"))
            assert spec.radius_ground == GroundSize(50.0, "meters")
            assert self._approximated_sizes(report) == []

            project.setEllipsoid("NONE")
            spec, report = self._translate(layer, self._marker("MetersInMapUnits"))
            assert spec.radius_ground is None
            assert self._approximated_sizes(report)
        finally:
            project.clear()

    def test_metres_on_the_ground_follow_the_projected_metre_in_web_mercator(
        self, qgis_app, make_memory_layer
    ) -> None:
        """QGIS corrects for latitude only in a geographic CRS, so 100 m is 100
        projected metres in EPSG:3857 - and that is what the web map must draw."""
        layer = self._layer(make_memory_layer, "EPSG:3857")
        spec, report = self._translate(layer, self._marker("MetersInMapUnits"))
        assert spec.radius_ground.units == "common"
        assert spec.radius_ground.value == pytest.approx(
            50.0 * COMMON_UNITS_PER_WEB_MERCATOR_METRE
        )
        assert self._approximated_sizes(report) == []

    def test_a_metre_crs_reads_map_units_as_metres(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        spec, report = self._translate(layer, self._marker("MapUnits"))
        assert spec.radius_ground == GroundSize(50.0, "meters")
        assert self._approximated_sizes(report) == []

    def test_a_foot_crs_converts_map_units_to_metres(
        self, qgis_app, make_memory_layer
    ) -> None:
        # US survey feet (EPSG:2263, New York Long Island)
        layer = self._layer(make_memory_layer, "EPSG:2263")
        spec, _ = self._translate(layer, self._marker("MapUnits"))
        factor = qgis_core.QgsUnitTypes.fromUnitToUnitFactor(
            layer.crs().mapUnits(), qgis_core.Qgis.DistanceUnit.Meters
        )
        assert spec.radius_ground.units == "meters"
        assert spec.radius_ground.value == pytest.approx(50.0 * factor)
        assert factor == pytest.approx(0.3048, rel=1e-3)

    def test_web_mercator_map_units_are_deck_common_units(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:3857")
        spec, _ = self._translate(layer, self._marker("MapUnits"))
        assert spec.radius_ground.units == "common"
        assert spec.radius_ground.value == pytest.approx(
            50.0 * COMMON_UNITS_PER_WEB_MERCATOR_METRE
        )

    def test_degrees_stay_approximated_and_are_reported(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer(
            "pts", crs="EPSG:4326", features=[("a", [0.0, 0.0]), ("b", [1.0, 1.0])]
        )
        layer.updateExtents()
        spec, report = self._translate(layer, self._marker("MapUnits", size=0.01))
        assert spec.radius_ground is None
        assert spec.radius > 0
        assert self._approximated_sizes(report)

    def test_each_size_is_judged_on_its_own_unit(
        self, qgis_app, make_memory_layer
    ) -> None:
        """A radius in metres beside a stroke in millimetres, on one marker."""
        layer = self._layer(make_memory_layer, "EPSG:32648")
        spec, report = self._translate(layer, self._marker("MetersInMapUnits"))
        assert spec.radius_ground == GroundSize(50.0, "meters")
        assert spec.stroke_width_ground is None
        assert spec.stroke_width == pytest.approx(10 * MM_TO_PIXELS)
        assert self._approximated_sizes(report) == []

    def test_a_stroke_on_the_ground_is_kept_too(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        spec, _ = self._translate(
            layer, self._marker("Millimeters", stroke_unit="MetersInMapUnits")
        )
        assert spec.stroke_width_ground == GroundSize(10.0, "meters")
        assert spec.radius_ground is None

    def test_a_road_width_on_the_ground_scales_like_qgis(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = make_memory_layer(
            "roads",
            geometry="LineString",
            crs="EPSG:32648",
            features=[("a", [0.0, 0.0, 7000.0, 4900.0])],
        )
        symbol = qgis_core.QgsLineSymbol.createSimple({"line_width": "10"})
        symbol.symbolLayer(0).setWidthUnit(qgis_core.Qgis.RenderUnit.MetersInMapUnits)
        spec, _ = self._translate(layer, symbol)
        assert spec.stroke_width_ground == GroundSize(10.0, "meters")

    @pytest.mark.parametrize("metres_per_pixel", [1.0, 7.0, 40.0])
    def test_the_ground_size_covers_the_pixels_qgis_draws_at_every_scale(
        self, qgis_app, make_memory_layer, metres_per_pixel
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        spec, _ = self._translate(layer, self._marker("MetersInMapUnits"))
        oracle = _unit_oracle_context(metres_per_pixel)
        qgis_radius = (
            oracle.convertToPainterUnits(
                100.0, qgis_core.Qgis.RenderUnit.MetersInMapUnits
            )
            / 2.0
        )
        assert spec.radius_ground.value / metres_per_pixel == pytest.approx(
            qgis_radius, rel=1e-4
        )

    def test_classes_in_different_units_fall_back_and_say_so(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "name",
                [
                    qgis_core.QgsRendererCategory(
                        "a", self._marker("MetersInMapUnits"), "a"
                    ),
                    qgis_core.QgsRendererCategory(
                        "b", self._marker("Millimeters"), "b"
                    ),
                ],
            )
        )
        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)
        assert [c.symbol.radius_ground for c in spec.categories] == [None, None]
        assert any(
            "different units" in i.detail
            for i in report.by_status(FidelityStatus.APPROXIMATED)
        )

    def test_classes_all_on_the_ground_keep_it(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "name",
                [
                    qgis_core.QgsRendererCategory(
                        "a", self._marker("MetersInMapUnits", 100), "a"
                    ),
                    qgis_core.QgsRendererCategory(
                        "b", self._marker("MapUnits", 300), "b"
                    ),
                ],
            )
        )
        spec = translate_renderer(layer, FidelityReportBuilder())
        assert [c.symbol.radius_ground for c in spec.categories] == [
            GroundSize(50.0, "meters"),
            GroundSize(150.0, "meters"),
        ]

    def test_the_layer_says_once_that_sizes_zoom(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = self._layer(make_memory_layer, "EPSG:32648")
        layer.setRenderer(
            qgis_core.QgsSingleSymbolRenderer(self._marker("MetersInMapUnits"))
        )
        report = FidelityReportBuilder()
        read_layer(layer, report)
        assert any(
            "marker size" in i.detail and "zoom" in i.detail
            for i in report.by_status(FidelityStatus.PRESERVED)
        )

    def test_the_25d_renderer_reads_no_sizes(self, qgis_app, make_memory_layer) -> None:
        """Its roof and wall colours are all it takes, so no scale is needed."""
        layer = make_memory_layer(
            "buildings",
            geometry="Polygon",
            crs="EPSG:32648",
            features=[("a", [0.0, 0.0, 100.0, 100.0])],
        )
        renderer = qgis_core.Qgs25DRenderer()
        layer.setRenderer(renderer)
        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)
        assert spec.symbol.radius_ground is None
        assert spec.symbol.stroke_width_ground is None
        assert self._approximated_sizes(report) == []


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
