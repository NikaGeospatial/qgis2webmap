"""Label translation beyond text and colour.

Placement is the part worth testing hard. QGIS names a quadrant by where the
label sits relative to the point; deck.gl wants which edge of the text box is
pinned, which is the mirror image. Getting it backwards puts every label on the
wrong side of its feature, and nothing else in the pipeline would notice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import MM_TO_PIXELS, FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.labeling_translator import (
    read_quadrant,
    translate_labeling,
)

qgis_core = pytest.importorskip("qgis.core")
QtGui = pytest.importorskip("qgis.PyQt.QtGui")
QtCore = pytest.importorskip("qgis.PyQt.QtCore")


def labelled_layer(**settings_overrides):
    """A point layer labelled by `name`, with the given settings applied."""
    layer = qgis_core.QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string", "points", "memory"
    )
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    for key, value in settings_overrides.items():
        setattr(settings, key, value)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


class TestQuadrant:
    """The mapping itself, without needing a layer."""

    def test_above_left_pins_the_end_and_bottom(self) -> None:
        class Fake:
            name = "QuadrantAboveLeft"

        assert read_quadrant(type("S", (), {"quadOffset": Fake()})()) == (
            "end",
            "bottom",
        )

    def test_below_left_is_not_confused_with_left(self) -> None:
        """ "QuadrantBelowLeft" also ends with "Left"; longest suffix must win."""

        class Fake:
            name = "QuadrantBelowLeft"

        assert read_quadrant(type("S", (), {"quadOffset": Fake()})()) == ("end", "top")

    def test_below_right_is_not_confused_with_right(self) -> None:
        class Fake:
            name = "QuadrantBelowRight"

        assert read_quadrant(type("S", (), {"quadOffset": Fake()})()) == (
            "start",
            "top",
        )

    def test_an_unreadable_quadrant_leaves_the_label_centred(self) -> None:
        """A corner is a worse guess than the middle when the enum is opaque."""
        assert read_quadrant(type("S", (), {"quadOffset": 7})()) == ("middle", "center")

    def test_no_quadrant_at_all_is_centred(self) -> None:
        assert read_quadrant(object()) == ("middle", "center")


class TestRealQgisSettings:
    def test_a_plain_labelled_layer_translates(self, qgis_app) -> None:
        spec = translate_labeling(labelled_layer(), FidelityReportBuilder())
        assert spec.enabled is True
        assert spec.field_name == "name"

    def test_offsets_convert_millimetres_to_pixels(self, qgis_app) -> None:
        layer = labelled_layer(xOffset=2.0, yOffset=-3.0)
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.offset_x == pytest.approx(2.0 * MM_TO_PIXELS)
        assert spec.offset_y == pytest.approx(-3.0 * MM_TO_PIXELS)

    def test_rotation_is_read_in_degrees(self, qgis_app) -> None:
        spec = translate_labeling(
            labelled_layer(angleOffset=30.0), FidelityReportBuilder()
        )
        assert spec.rotation == pytest.approx(30.0)

    def test_a_real_quadrant_enum_is_understood(self, qgis_app) -> None:
        """The point of the whole exercise: QGIS's own enum, not a stand-in."""
        layer = labelled_layer()
        settings = layer.labeling().settings()
        settings.quadOffset = qgis_core.QgsPalLayerSettings.QuadrantAboveLeft
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert (spec.anchor, spec.baseline) == ("end", "bottom")

    def test_a_background_is_carried_with_its_padding(self, qgis_app) -> None:
        layer = labelled_layer()
        settings = layer.labeling().settings()
        text_format = settings.format()
        background = text_format.background()
        background.setEnabled(True)
        background.setFillColor(QtGui.QColor("#ffffff"))
        background.setSize(QtCore.QSizeF(2.0, 1.0))
        text_format.setBackground(background)
        settings.setFormat(text_format)
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))

        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.background_color is not None
        assert spec.background_color.r == 255
        assert spec.background_padding[0] == pytest.approx(2.0 * MM_TO_PIXELS)

    def test_no_background_stays_none(self, qgis_app) -> None:
        spec = translate_labeling(labelled_layer(), FidelityReportBuilder())
        assert spec.background_color is None

    def test_a_bold_font_is_recorded(self, qgis_app) -> None:
        layer = labelled_layer()
        settings = layer.labeling().settings()
        text_format = settings.format()
        font = text_format.font()
        font.setBold(True)
        text_format.setFont(font)
        settings.setFormat(text_format)
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))

        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.bold is True


def _with_format(size: float, unit, buffer_size: float = 0.0, buffer_unit=None):
    """A labelled layer whose text format has a size, in a unit, and a buffer."""
    layer = labelled_layer()
    settings = layer.labeling().settings()
    text_format = settings.format()
    text_format.setSize(size)
    text_format.setSizeUnit(unit)
    if buffer_size:
        buffer = text_format.buffer()
        buffer.setEnabled(True)
        buffer.setSize(buffer_size)
        buffer.setSizeUnit(buffer_unit if buffer_unit is not None else unit)
        buffer.setColor(QtGui.QColor("#ffffff"))
        text_format.setBuffer(buffer)
    settings.setFormat(text_format)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    return layer


class TestFieldExpressions:
    """A label typed into the expression box as just a field is that field;
    any other expression is worked out per feature at export."""

    def _expression_layer(self, expression: str):
        return labelled_layer(fieldName=expression, isExpression=True)

    def test_a_quoted_field_label_is_translated_as_the_field(self, qgis_app) -> None:
        report = FidelityReportBuilder()
        spec = translate_labeling(self._expression_layer('"name"'), report)
        assert spec.enabled is True
        assert spec.field_name == "name"
        assert not any(
            item.status is FidelityStatus.UNSUPPORTED for item in report.items
        )

    def test_a_field_with_surrounding_space_is_still_the_field(self, qgis_app) -> None:
        spec = translate_labeling(
            self._expression_layer('  "name"  '), FidelityReportBuilder()
        )
        assert spec.field_name == "name"

    @pytest.mark.parametrize(
        "expression",
        [
            "'Colorado River'",
            "\"name\" || ' (' || \"name\" || ')'",
            'CASE WHEN "name" = \'x\' THEN "name" END',
            'upper("name")',
            '"no_such_field"',
        ],
    )
    def test_anything_else_is_worked_out_at_export(self, qgis_app, expression) -> None:
        """Into a property of its own, which the labels then read as a field
        (`tests/qgis/test_expression_labels.py` checks the text)."""
        report = FidelityReportBuilder()
        spec = translate_labeling(self._expression_layer(expression), report)
        assert spec.enabled is True
        assert spec.expression == expression
        assert spec.field_name == "label_text"
        assert spec.character_set is None
        assert not any(
            item.status is FidelityStatus.UNSUPPORTED for item in report.items
        )

    def test_an_expression_that_does_not_parse_stays_unsupported(
        self, qgis_app
    ) -> None:
        report = FidelityReportBuilder()
        spec = translate_labeling(self._expression_layer('upper("name"'), report)
        assert spec.enabled is False
        assert any(item.status is FidelityStatus.UNSUPPORTED for item in report.items)


class TestSizeUnits:
    """QGIS sizes text in points by default; the web map sizes it in pixels.

    Copying the number across drew a 9 pt label at 9 px - three quarters of
    the size QGIS shows - and a 0.9 mm buffer as 0.9 of whatever the web map
    measures in.
    """

    def test_points_become_pixels(self, qgis_app) -> None:
        layer = _with_format(9.0, qgis_core.Qgis.RenderUnit.Points)
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.font_size == pytest.approx(12.0)

    def test_millimetres_become_pixels(self, qgis_app) -> None:
        layer = _with_format(4.0, qgis_core.Qgis.RenderUnit.Millimeters)
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.font_size == pytest.approx(4.0 * MM_TO_PIXELS)

    def test_pixels_stay_pixels(self, qgis_app) -> None:
        layer = _with_format(14.0, qgis_core.Qgis.RenderUnit.Pixels)
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.font_size == pytest.approx(14.0)

    def test_the_buffer_is_measured_in_its_own_unit(self, qgis_app) -> None:
        layer = _with_format(
            9.0,
            qgis_core.Qgis.RenderUnit.Points,
            buffer_size=0.9,
            buffer_unit=qgis_core.Qgis.RenderUnit.Millimeters,
        )
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.halo_width == pytest.approx(0.9 * MM_TO_PIXELS)

    def test_a_map_unit_size_is_reported_not_guessed(self, qgis_app) -> None:
        layer = _with_format(50.0, qgis_core.Qgis.RenderUnit.MapUnits)
        report = FidelityReportBuilder()
        spec = translate_labeling(layer, report)
        assert spec.font_size is None  # the web map's default size
        assert any("map units" in item.detail for item in report.items)


class TestCapitalizationAndWrapping:
    """QGIS text case and line breaking, read from the label settings."""

    @staticmethod
    def _labelled(make_memory_layer, capitalization=None, wrap_char="", auto_wrap=0):
        layer = make_memory_layer("places", features=[("Zurich", [0.0, 0.0])])
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        settings.wrapChar = wrap_char
        settings.autoWrapLength = auto_wrap
        if capitalization is not None:
            fmt = settings.format()
            fmt.setCapitalization(capitalization)
            settings.setFormat(fmt)
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        return layer

    def test_uppercase_is_read(self, qgis_app, make_memory_layer) -> None:
        layer = self._labelled(
            make_memory_layer,
            capitalization=qgis_core.QgsStringUtils.AllUppercase,
        )
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.capitalization == "upper"

    def test_mixed_case_means_leave_it_alone(self, qgis_app, make_memory_layer) -> None:
        layer = self._labelled(
            make_memory_layer,
            capitalization=qgis_core.QgsStringUtils.MixedCase,
        )
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.capitalization == "none"

    def test_the_wrap_character_is_read(self, qgis_app, make_memory_layer) -> None:
        layer = self._labelled(make_memory_layer, wrap_char="|")
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.wrap_char == "|"

    def test_the_auto_wrap_length_is_read(self, qgis_app, make_memory_layer) -> None:
        layer = self._labelled(make_memory_layer, auto_wrap=12)
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.auto_wrap_length == 12

    def test_the_character_set_covers_the_transformed_text(
        self, qgis_app, make_memory_layer
    ) -> None:
        """`text-character-set` replaces the atlas. Collecting glyphs from the
        raw values while drawing uppercased ones leaves every accented capital
        out - labels present, positioned, and invisible."""
        layer = make_memory_layer("places", features=[("Zürich", [0.0, 0.0])])
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        fmt = settings.format()
        fmt.setCapitalization(qgis_core.QgsStringUtils.AllUppercase)
        settings.setFormat(fmt)
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)

        spec = translate_labeling(layer, FidelityReportBuilder())

        assert spec.character_set is not None
        assert "Ü" in spec.character_set, "the drawn glyph is missing from the atlas"


def accented_layer(count: int, odd_index: int, odd_text: str):
    """`count` ASCII-labelled points, one of which (`odd_index`) has `odd_text`."""
    layer = labelled_layer()
    provider = layer.dataProvider()
    features = []
    for index in range(count):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttributes([odd_text if index == odd_index else f"town {index}"])
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(index % 90, 0))
        )
        features.append(feature)
    provider.addFeatures(features)
    return layer


class TestLabelFidelity:
    def test_a_glyph_first_used_past_5000_features_is_in_the_atlas(
        self, qgis_app
    ) -> None:
        layer = accented_layer(6000, 5500, "Ž")
        spec = translate_labeling(layer, FidelityReportBuilder())
        assert spec.character_set is not None
        assert "Ž" in spec.character_set
        assert "t" in spec.character_set  # the ASCII letters are kept too

    def test_all_ascii_still_needs_no_character_set(self, qgis_app) -> None:
        layer = accented_layer(50, 0, "plain")
        assert translate_labeling(layer, FidelityReportBuilder()).character_set is None

    @staticmethod
    def rule_layer(*rules):
        """A labelled layer whose labelling is rule-based: (filter, settings) each."""
        layer = labelled_layer()
        root = qgis_core.QgsRuleBasedLabeling.Rule(qgis_core.QgsPalLayerSettings())
        for filter_expression, settings in rules:
            root.appendChild(
                qgis_core.QgsRuleBasedLabeling.Rule(
                    settings, filterExp=filter_expression
                )
            )
        layer.setLabeling(qgis_core.QgsRuleBasedLabeling(root))
        layer.setLabelsEnabled(True)
        return layer

    @staticmethod
    def name_settings():
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        return settings

    def test_one_unfiltered_rule_is_translated_like_simple_labelling(
        self, qgis_app
    ) -> None:
        layer = self.rule_layer(("TRUE", self.name_settings()))
        report = FidelityReportBuilder()
        spec = translate_labeling(layer, report)
        assert spec.enabled is True
        assert spec.field_name == "name"
        assert any("Rule-based" in item.detail for item in report.items)

    def test_a_filtered_rule_is_reported_not_silently_dropped(self, qgis_app) -> None:
        layer = self.rule_layer(("\"name\" = 'x'", self.name_settings()))
        report = FidelityReportBuilder()
        assert translate_labeling(layer, report).enabled is False
        assert any(
            item.status is FidelityStatus.UNSUPPORTED
            and "rule-based" in item.detail.lower()
            for item in report.items
        )

    def test_several_rules_are_reported(self, qgis_app) -> None:
        layer = self.rule_layer(
            ("TRUE", self.name_settings()), ("TRUE", self.name_settings())
        )
        report = FidelityReportBuilder()
        assert translate_labeling(layer, report).enabled is False
        assert any(item.status is FidelityStatus.UNSUPPORTED for item in report.items)

    def test_obstacle_only_labelling_draws_no_labels(self, qgis_app) -> None:
        report = FidelityReportBuilder()
        spec = translate_labeling(labelled_layer(drawLabels=False), report)
        assert spec.enabled is False
        assert any("obstacle" in item.detail for item in report.items)
