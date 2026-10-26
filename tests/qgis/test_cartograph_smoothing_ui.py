"""Map frames > actions > "Smooth when enlarged": offered for the continuous
rasters a map draws, applied to that map only, undoable and saved.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtTest import QTest
from test_cartograph_dialog import (
    _add_layout,
    _choose_cartograph,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)
from test_raster_smoothing import _gradient_ramp, _gradient_raster, _paletted

from nika_onlymap_exporter.core.raster_smoothing import smoothing_on


def _setup(project, tmp_path):
    from qgis.core import QgsLayoutItemMap

    smooth = _gradient_ramp(_gradient_raster(tmp_path, "elevation"))
    classes = _paletted(_gradient_raster(tmp_path, "landcover"))
    project.addMapLayers([smooth, classes])
    layout = _add_layout(
        project, "Survey", [("main", "EPSG:4326"), ("inset", "EPSG:4326")]
    )
    maps = {i.id(): i for i in layout.items() if isinstance(i, QgsLayoutItemMap)}
    for item in maps.values():
        item.setLayers([classes, smooth])
    dialog = _dialog(project)
    dialog.show()
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    cartograph._autosave_timer.stop()
    cartograph.open_section("frames")
    return dialog, cartograph, layout, maps, smooth, classes


def _open_actions(qgis_app, card):
    menu = card.actions_button.menu()
    menu.popup(card.actions_button.mapToGlobal(QPoint(0, card.actions_button.height())))
    qgis_app.processEvents()
    return menu


def _click(menu, action) -> None:
    QTest.mouseClick(
        menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center()
    )


def _choose_smoothing(qgis_app, card, layer_name: str) -> None:
    menu = _open_actions(qgis_app, card)
    parent_action = card.smoothing_menu_action
    submenu = card.smoothing_menu
    _click(menu, parent_action)
    qgis_app.processEvents()
    action = next(a for a in submenu.actions() if a.text() == layer_name)
    _click(submenu, action)
    qgis_app.processEvents()
    menu.close()


class TestSmoothingFromTheFrameCard:
    def test_only_continuous_rasters_are_offered_and_only_for_the_chosen_map(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, cartograph, layout, maps, smooth, classes = _setup(project, tmp_path)
        try:
            card = cartograph.frames_list.card(maps["main"].uuid())
            _open_actions(qgis_app, card).close()
            assert card.smoothing_menu_action.isVisible()
            assert [a.text() for a in card.smoothing_menu.actions()] == ["elevation"]

            _choose_smoothing(qgis_app, card, "elevation")

            assert smoothing_on(maps["main"], smooth)
            assert not smoothing_on(maps["inset"], smooth)
            # The layer's own style is the project's, and unchanged.
            assert not smoothing_on(_plain_map(project, smooth), smooth)
            assert not smoothing_on(maps["main"], classes)
        finally:
            dialog.close()

    def test_it_is_one_undo_step_and_is_ticked_when_the_menu_opens_again(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, cartograph, layout, maps, smooth, _classes = _setup(project, tmp_path)
        try:
            card = cartograph.frames_list.card(maps["main"].uuid())
            stack = layout.undoStack().stack()
            before = stack.index()
            _choose_smoothing(qgis_app, card, "elevation")
            assert stack.index() == before + 1
            _open_actions(qgis_app, card).close()
            assert card.smoothing_menu.actions()[0].isChecked()

            stack.undo()
            assert not smoothing_on(maps["main"], smooth)
            stack.redo()
            assert smoothing_on(maps["main"], smooth)

            _choose_smoothing(qgis_app, card, "elevation")
            assert not smoothing_on(maps["main"], smooth)
        finally:
            dialog.close()

    def test_a_map_with_no_continuous_raster_has_no_entry(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, cartograph, layout, maps, _smooth, classes = _setup(project, tmp_path)
        try:
            maps["inset"].setLayers([classes])
            card = cartograph.frames_list.card(maps["inset"].uuid())
            _open_actions(qgis_app, card).close()
            assert not card.smoothing_menu_action.isVisible()
        finally:
            dialog.close()


def _plain_map(project, layer):
    from qgis.core import QgsLayoutItemMap, QgsPrintLayout

    layout = QgsPrintLayout(project)
    item = QgsLayoutItemMap(layout)
    item.setLayers([layer])
    _plain_map.keep = layout  # the layout owns the item
    return item
