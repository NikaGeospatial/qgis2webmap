"""An editable dropdown's text sits where a plain dropdown's text sits.

Judged on rendered pixels: the same capital letters are drawn in a plain
drop-down, an editable one, and the expression picker and field list the Atlas
tab uses, under the plugin's own theme, light and dark, at several font sizes;
the rows the letters occupy must be the same in every box.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsVectorLayer
from qgis.gui import QgsFieldComboBox, QgsFieldExpressionWidget
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QFont, QImage
from qgis.PyQt.QtWidgets import QComboBox, QFormLayout, QLineEdit, QWidget

from nika_onlymap_exporter.ui import theme

#: Capitals only: no accent above or tail below to blur the rows measured.
TEXT = "HEZ HEZ"
#: Font sizes, relative to the plugin's own, at which the rows must agree.
SCALES = (0.9, 1.0, 1.1, 1.25)


def _text_rows(widget: QWidget, start: int) -> tuple[int, int]:
    """First and last pixel row that holds text, from column `start`."""
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    background = image.pixelColor(start, 4)
    rows = []
    # The first letters only: a caret sits after the last one.
    for y in range(4, image.height() - 4):
        for x in range(start, min(image.width() - 44, start + 28)):
            pixel = image.pixelColor(x, y)
            distance = (
                abs(pixel.red() - background.red())
                + abs(pixel.green() - background.green())
                + abs(pixel.blue() - background.blue())
            )
            if distance > 120:
                rows.append(y)
                break
    assert rows, "no text found in the box"
    return min(rows), max(rows)


def _boxes(scale: float, dark: bool, width: int):
    QSettings().setValue(theme.THEME_KEY, "dark" if dark else "light")
    layer = QgsVectorLayer(
        f"Polygon?crs=EPSG:3857&field={TEXT}:string", "layer", "memory"
    )
    root = QWidget()
    # The boxes only watch the layer: something has to keep it alive.
    root.layer = layer  # type: ignore[attr-defined]
    root.setObjectName("probe")
    root.setFixedWidth(width)
    form = QFormLayout(root)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    plain = QComboBox()
    plain.addItem(TEXT)
    editable = QComboBox()
    editable.setEditable(True)
    editable.addItem(TEXT)
    expression = QgsFieldExpressionWidget()
    expression.setLayer(layer)
    expression.setExpression(f'"{TEXT}"')
    fields = QgsFieldComboBox()
    fields.setLayer(layer)
    fields.setField(TEXT)
    # Where the text starts: after the type icon of the two field boxes.
    boxes = {
        "plain": (plain, 12),
        "editable": (editable, 12),
        "expression picker": (expression, 36),
        "field list": (fields, 36),
    }
    for label, (box, _start) in boxes.items():
        form.addRow(label, box)
    font = QFont(root.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    root.setFont(font)
    theme.apply(root)
    root.show()
    return root, boxes


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("width", [260, 360], ids=["narrow", "wide"])
@pytest.mark.parametrize("scale", SCALES)
def test_editable_text_lines_up_with_plain_dropdown_text(
    qgis_app, scale: float, width: int, dark: bool
) -> None:
    root, boxes = _boxes(scale, dark, width)
    try:
        qgis_app.processEvents()
        rows = {label: _text_rows(box, start) for label, (box, start) in boxes.items()}
        reference = rows["plain"]
        for label, found in rows.items():
            assert found == reference, f"{label}: {found} vs plain {reference}"
    finally:
        root.close()


def test_the_text_does_not_move_when_a_box_takes_focus(qgis_app) -> None:
    root, boxes = _boxes(1.0, False, 330)
    try:
        qgis_app.processEvents()
        for label, (box, start) in boxes.items():
            before = _text_rows(box, start)
            box.setFocus()
            # Focus may select the text, and a highlight is not text.
            for edit in box.findChildren(QLineEdit):
                edit.deselect()
            qgis_app.processEvents()
            assert _text_rows(box, start) == before, label
    finally:
        root.close()


def test_a_long_expression_stays_clear_of_the_arrow_and_the_builder_button(
    qgis_app,
) -> None:
    root, boxes = _boxes(1.0, False, 260)
    try:
        expression, _start = boxes["expression picker"]
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:3857&field=a_long_field_name_to_overrun_the_box:string",
            "layer",
            "memory",
        )
        root.long_layer = layer  # type: ignore[attr-defined]
        expression.setLayer(layer)
        expression.setExpression(
            '"a_long_field_name_to_overrun_the_box" || upper("a_long_field_name_'
            "to_overrun_the_box\") || ' and some more words'"
        )
        qgis_app.processEvents()
        combo = expression.findChild(QComboBox)
        line = combo.lineEdit()
        # The text edit ends before the combo's arrow area, and the combo
        # before the expression builder button beside it.
        assert line.geometry().right() < combo.width() - 20
        assert combo.geometry().right() < expression.width()
    finally:
        root.close()
