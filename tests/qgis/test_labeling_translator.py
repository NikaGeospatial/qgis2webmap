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

from nika_onlymap_exporter.core.export_ir import MM_TO_PIXELS
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
