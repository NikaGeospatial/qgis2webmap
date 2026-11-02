"""An edit made in the Edit tab shows in the QGIS preview panel at once.

The check is on pixels - the layout view's viewport before and after - not on
the item's property: the property can change while the view keeps drawing the
old text.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_cartograph_panel_widgets import _maps_drawn, _quiet
from test_preview_item_editing import _items_of, _place, one_map  # noqa: F401

qgis_core = pytest.importorskip("qgis.core")


def _picture(qgis_app, pane):
    from qgis.core import QgsLayoutItemMap

    _quiet(qgis_app, pane)
    _maps_drawn(
        qgis_app, _items_of(pane.preview.view.currentLayout(), QgsLayoutItemMap)
    )
    return pane.preview.view.viewport().grab().toImage()


@pytest.fixture
def big_text(qgis_app, one_map):  # noqa: F811
    """A wide text item with large words, placed and selected."""
    from qgis.core import QgsLayoutItemLabel, QgsUnitTypes
    from qgis.PyQt.QtCore import QRectF

    pane, layout, _frames = one_map
    _place(qgis_app, pane, "Text")
    label = _items_of(layout, QgsLayoutItemLabel)[0]
    text_format = label.textFormat()
    text_format.setSize(30)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    label.setTextFormat(text_format)
    label.setText("Grand Canyon")
    label.attemptSetSceneRect(QRectF(20, 130, 200, 40))
    layout.deselectAll()
    label.setSelected(True)
    pane.controls.refresh()
    return pane, layout, label


def _bold(pane):
    pane.controls.bold_button.click()


def _align_right(pane):
    from qgis.PyQt.QtCore import Qt

    pane.controls.align_buttons[Qt.AlignmentFlag.AlignRight].click()


def _colour(pane):
    from qgis.PyQt.QtGui import QColor

    pane.controls.colour_button.chosen.emit(QColor("#cc2200"))


def _bigger(pane):
    pane.controls.text_bigger_button.click()


def _words(pane):
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtTest import QTest

    edit = pane.controls.text_edit
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, "Lake")
    QTest.keyClick(edit, Qt.Key.Key_Return)


@pytest.mark.parametrize(
    "action", [_bold, _align_right, _colour, _bigger, _words], ids=lambda f: f.__name__
)
def test_the_preview_panel_redraws_after_an_edit(qgis_app, big_text, action) -> None:
    pane, _layout, _label = big_text
    before = _picture(qgis_app, pane)
    action(pane)
    after = _picture(qgis_app, pane)
    assert before != after


class TestBoldToggle:
    """B turns bold on with one click and off with one click."""

    @pytest.fixture(params=["plain", "template"])
    def label_pane(self, request, big_text):
        pane, layout, label = big_text
        if request.param == "template":
            # What an item built from a template carries: a bold font and
            # QGIS's own forced-bold flag, which can only be set, not cleared.
            text_format = label.textFormat()
            text_format.setForcedBold(True)
            label.setTextFormat(text_format)
            pane.controls.refresh()
        return pane, layout, label

    def test_one_click_each_way(self, qgis_app, label_pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.item_controls import is_bold

        pane, _layout, label = label_pane
        button = pane.controls.bold_button
        assert not button.isChecked() or is_bold(label)
        for expected in (True, False, True, False):
            if button.isChecked() != expected:
                button.click()
            assert button.isChecked() is expected
            assert is_bold(label) is expected
            pane.controls.refresh()
            assert button.isChecked() is expected

    def test_off_means_a_regular_weight_in_the_export(
        self, qgis_app, label_pane
    ) -> None:
        from nika_onlymap_exporter.core.layout_reader import _css_font_weight

        pane, _layout, label = label_pane
        button = pane.controls.bold_button
        if not button.isChecked():
            button.click()
        assert _css_font_weight(label.textFormat().font()) == "bold"
        button.click()
        assert not button.isChecked()
        assert _css_font_weight(label.textFormat().font()) is None
        assert not label.textFormat().font().bold()
