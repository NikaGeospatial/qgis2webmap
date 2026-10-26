"""Map frame rows and the Atlas map choice follow edits to existing map items.

A rename or a CRS change of a map already on the layout is an undo command
like any designer edit; Undo and Redo move it back and forth. Both panels show
the change in place, without a layout switch and without rebuilding the rows
(a focused control keeps its focus).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from test_cartograph_dialog import (  # noqa: E402
    _add_layout,
    _choose_cartograph,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)


def _edit(layout, item, change) -> None:
    layout.undoStack().beginCommand(item, "designer edit")
    change()
    layout.undoStack().endCommand()


def _setup(project):
    from qgis.core import QgsLayoutItemMap

    layout = _add_layout(
        project, "Survey", [("main", "EPSG:4326"), ("inset", "EPSG:4326")]
    )
    dialog = _dialog(project)
    dialog.show()
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    cartograph._autosave_timer.stop()
    maps = {i.id(): i for i in layout.items() if isinstance(i, QgsLayoutItemMap)}
    return dialog, cartograph, layout, maps


def _names(cartograph) -> dict[str, str]:
    return {
        item_id: cartograph.frames_list.card(item_id).name_label.text()
        for item_id in cartograph._frame_rows
    }


class TestFramesFollowItemEdits:
    def test_rename_and_crs_change_with_undo_and_redo(self, qgis_app, project) -> None:
        from qgis.core import QgsCoordinateReferenceSystem

        dialog, cartograph, layout, maps = _setup(project)
        try:
            inset = maps["inset"]
            uuid = inset.uuid()
            cards = {i: cartograph.frames_list.card(i) for i in cartograph._frame_rows}
            combo = cartograph.atlas_tab.map_combo
            combo.setCurrentIndex(combo.findData(uuid))
            cartograph.open_section("frames")
            focused = cartograph.frames_list.card(maps["main"].uuid()).mode_combo
            dialog.activateWindow()
            qgis_app.processEvents()
            focused.setFocus()
            qgis_app.processEvents()
            assert focused.window().focusWidget() is focused
            stack = layout.undoStack().stack()

            _edit(layout, inset, lambda: inset.setId("Overview"))
            _edit(
                layout,
                inset,
                lambda: inset.setCrs(QgsCoordinateReferenceSystem("EPSG:3857")),
            )
            card = cartograph.frames_list.card(uuid)
            assert card.name_label.text() == "Overview"
            assert card.crs_label.text() == "EPSG:3857"
            assert combo.itemText(combo.findData(uuid)) == "Overview"
            assert combo.currentData() == uuid
            assert focused.window().focusWidget() is focused
            # The same cards: nothing was rebuilt.
            assert all(cartograph.frames_list.card(i) is c for i, c in cards.items())

            stack.undo()
            assert card.crs_label.text() == "EPSG:4326"
            assert card.name_label.text() == "Overview"
            stack.undo()
            assert card.name_label.text() == "inset"
            assert combo.itemText(combo.findData(uuid)) == "inset"
            stack.redo()
            stack.redo()
            assert card.name_label.text() == "Overview"
            assert card.crs_label.text() == "EPSG:3857"
            assert combo.itemText(combo.findData(uuid)) == "Overview"
            assert combo.currentData() == uuid
            assert cartograph.frames_list.card(uuid) is cards[uuid]
            assert _names(cartograph)[maps["main"].uuid()] == "main"
        finally:
            dialog.close()

    def test_a_frame_choice_survives_a_rename(self, qgis_app, project) -> None:
        dialog, cartograph, layout, maps = _setup(project)
        try:
            main = maps["main"]
            card = cartograph.frames_list.card(main.uuid())
            card.mode_combo.setCurrentIndex(card.mode_combo.findData("static"))
            _edit(layout, main, lambda: main.setId("Overview"))
            assert card.name_label.text() == "Overview"
            assert card.mode_combo.currentData() == "static"
            assert dialog.state.frame_choices("Survey")[main.uuid()].mode == "static"
        finally:
            dialog.close()
