"""The Layers panel only offers what the selected map frame can do.

An Interactive frame is a live map, so popups and labels are real HTML
features; an Image frame is rasterised from the QGIS layout, so those settings
cannot change it. Include always works, stored choices survive a round trip
through Image, and a raster layer never gets vector popup or label options.
The dialog is driven like a user: the frame is chosen in the "Layers for:"
bar or the Frames card, cells are clicked.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402, F811

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QPoint, QRectF, Qt
from qgis.PyQt.QtTest import QTest
from test_cartograph_layer_membership import (  # noqa: F401 - fixtures
    PEAKS,
    _add_map,
    _click_include,
    _item,
    _ticked,
    dialog,
    runtime,
    world,
)

from nika_onlymap_exporter.core.settings import DialogOutput


def _set_mode(dialog, item_id: str, mode: str) -> None:
    combo = dialog._cartograph._frame_rows[item_id]["mode"]
    combo.setCurrentIndex(combo.findData(mode))


def _choose_frame(dialog, name: str) -> None:
    bar = dialog._cartograph.membership.bar
    index = bar.frame_combo.findText(name)
    bar.frame_combo.setCurrentIndex(index)
    bar.frame_combo.activated.emit(index)


def _cell_pos(dialog, name: str, column: int) -> QPoint:
    tree = dialog.layer_tree
    item = _item(dialog, name)
    tree.scrollToItem(item)
    rect = tree.visualItemRect(item)
    x = tree.header().sectionViewportPosition(column) + 10
    return QPoint(x, rect.center().y())


def _click_cell(dialog, name: str, column: int) -> None:
    QTest.mouseClick(
        dialog.layer_tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=_cell_pos(dialog, name, column),
    )


def _inactive(dialog, name: str, column: int) -> bool:
    from nika_onlymap_exporter.ui.main_dialog import CELL_INACTIVE_ROLE

    return bool(_item(dialog, name).data(column, CELL_INACTIVE_ROLE))


def _peaks_settings(world, dialog):
    return dialog.state.for_layer(world.layers[PEAKS].id())


def _field_rows_enabled(dialog, name: str) -> list[bool]:
    item = _item(dialog, name)
    return [
        dialog.layer_tree.itemWidget(item.child(i), 0).isEnabled()
        for i in range(item.childCount())
    ]


class TestInteractiveFrame:
    def test_popups_and_labels_work(self, world, dialog) -> None:
        main = world.main.uuid()
        _set_mode(dialog, main, "live")
        settings = _peaks_settings(world, dialog)
        assert not _inactive(dialog, PEAKS, 2)
        assert not _inactive(dialog, PEAKS, 3)
        before = settings.popup
        _click_cell(dialog, PEAKS, 2)
        assert settings.popup is (not before)
        assert all(_field_rows_enabled(dialog, PEAKS))
        assert "interactive" in dialog.frame_behaviour_hint.text()
        assert not dialog.frame_behaviour_hint.isHidden()


class TestImageFrame:
    def test_popups_and_labels_are_greyed_and_inert_but_include_works(
        self, world, dialog
    ) -> None:
        _set_mode(dialog, world.main.uuid(), "static")
        settings = _peaks_settings(world, dialog)
        popup, label = settings.popup, settings.label
        assert _inactive(dialog, PEAKS, 2)
        assert _inactive(dialog, PEAKS, 3)

        _click_cell(dialog, PEAKS, 2)
        _click_cell(dialog, PEAKS, 3)
        assert (settings.popup, settings.label) == (popup, label)
        # The keyboard is no way round it.
        dialog.layer_tree.setCurrentItem(_item(dialog, PEAKS), 2)
        QTest.keyClick(dialog.layer_tree, Qt.Key.Key_Space)
        assert settings.popup == popup

        assert not any(_field_rows_enabled(dialog, PEAKS))
        assert not dialog.bulk_apply_button.isEnabled()
        assert "image" in dialog.frame_behaviour_hint.text()
        assert "image" in dialog.layers_info_button.toolTip()
        assert "no effect" in _item(dialog, PEAKS).toolTip(2)

        _click_include(dialog, PEAKS)
        assert not _ticked(dialog, PEAKS)

    def test_choices_come_back_when_the_frame_is_interactive_again(
        self, world, dialog
    ) -> None:
        main = world.main.uuid()
        settings = _peaks_settings(world, dialog)
        _click_cell(dialog, PEAKS, 2)  # popups off, while interactive
        settings.fields["n"] = "hidden"
        assert settings.popup is False

        _set_mode(dialog, main, "static")
        assert _item(dialog, PEAKS).checkState(2) == Qt.CheckState.Unchecked
        _set_mode(dialog, main, "live")

        assert not _inactive(dialog, PEAKS, 2)
        assert settings.popup is False
        assert settings.fields["n"] == "hidden"
        assert settings.label is True
        assert _item(dialog, PEAKS).checkState(3) == Qt.CheckState.Checked
        assert _item(dialog, PEAKS).checkState(2) == Qt.CheckState.Unchecked
        assert all(_field_rows_enabled(dialog, PEAKS))
        assert dialog.bulk_apply_button.isEnabled()

    def test_the_selected_frame_decides(self, world, dialog) -> None:
        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        dialog._cartograph.membership.layout_changed()
        dialog._cartograph.refresh_layouts()
        _set_mode(dialog, inset.uuid(), "static")
        _set_mode(dialog, world.main.uuid(), "live")

        _choose_frame(dialog, "main")
        assert not _inactive(dialog, PEAKS, 2)
        assert "interactive" in dialog.frame_behaviour_hint.text()
        _choose_frame(dialog, "inset")
        assert _inactive(dialog, PEAKS, 2)
        assert "image" in dialog.frame_behaviour_hint.text()
        _choose_frame(dialog, "main")
        assert not _inactive(dialog, PEAKS, 2)

    def test_web_map_is_unchanged(self, world, dialog) -> None:
        _set_mode(dialog, world.main.uuid(), "static")
        dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()
        assert not _inactive(dialog, PEAKS, 2)
        assert not _inactive(dialog, PEAKS, 3)
        assert all(_field_rows_enabled(dialog, PEAKS))
        assert dialog.bulk_apply_button.isEnabled()
        assert dialog.frame_behaviour_hint.isHidden()
        assert "data is not written" in dialog.layers_info_button.toolTip()
        settings = _peaks_settings(world, dialog)
        before = settings.popup
        _click_cell(dialog, PEAKS, 2)
        assert settings.popup is (not before)


class TestRasterLayers:
    @pytest.fixture
    def with_raster(self, world, dialog, make_raster_layer):
        raster = make_raster_layer("Elevation")
        world.project.addMapLayer(raster)
        dialog.refresh_layers()
        return raster

    @pytest.mark.parametrize("mode", ["live", "static"])
    def test_no_popup_or_label_boxes_in_either_mode(
        self, world, dialog, with_raster, mode
    ) -> None:
        _set_mode(dialog, world.main.uuid(), mode)
        item = _item(dialog, "Elevation")
        for column in (2, 3):
            assert item.data(column, Qt.ItemDataRole.CheckStateRole) is None
        settings = dialog.state.for_layer(with_raster.id())
        before = (settings.popup, settings.label)
        _click_cell(dialog, "Elevation", 2)
        _click_cell(dialog, "Elevation", 3)
        assert (settings.popup, settings.label) == before
        assert item.childCount() == 0
        assert "raster" in item.toolTip(2)

    def test_the_boxes_return_in_the_web_map(self, world, dialog, with_raster) -> None:
        dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()
        item = _item(dialog, "Elevation")
        assert item.data(2, Qt.ItemDataRole.CheckStateRole) is not None
        assert item.data(3, Qt.ItemDataRole.CheckStateRole) is not None
