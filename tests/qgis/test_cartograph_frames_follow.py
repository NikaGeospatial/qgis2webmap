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

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from test_cartograph_dialog import (
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
            # The chooser names a map as people say it ("Inset map").
            assert combo.itemText(combo.findData(uuid)) == "Inset map"
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


class TestFrameIssuesAndSelection:
    def test_issue_counts_go_by_item_identity_not_by_name(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import FidelityStatus
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        dialog, cartograph, _layout, maps = _setup(project)
        try:
            main, inset = maps["main"].uuid(), maps["inset"].uuid()
            report = FidelityReportBuilder()
            # A row about "main" that happens to name a layer called "inset",
            # and a layer row that only mentions "main" in its text.
            report.raster_fallback(
                "Map frame 'main': layer 'inset'",
                "Drawn into this frame's picture.",
                "layer-1",
                item_id=main,
            )
            report.approximated(
                "Map frame 'main'", "Web Mercator, 'inset' differs.", item_id=main
            )
            report.approximated("Symbology of 'main'", "Changed.", "layer-2")
            report.preserved("Map frame 'inset'", "Kept.", item_id=inset)
            assert report.items[0].status is not FidelityStatus.PRESERVED
            cartograph._count_frame_issues(report)
            assert cartograph._frame_issue_counts == {main: 2, inset: 0}
            assert cartograph.frames_list.card(main).issues_button.text() == (
                "2 issues →"
            )
            assert cartograph.frames_list.card(inset).issues_button.isHidden()
        finally:
            dialog.close()

    def test_a_real_export_tags_every_frame_row_with_its_item(
        self, qgis_app, project
    ) -> None:
        from qgis.core import QgsLayoutItemMap

        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.layout_reader import read_layout

        layout = _add_layout(
            project, "Survey", [("Map", "EPSG:3857"), ("Map 2", "EPSG:32648")]
        )
        # A rotated UTM map cannot draw in its own CRS: it is exported static.
        layout.itemById("Map 2").setMapRotation(15)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        ids = {
            i.id(): i.uuid() for i in layout.items() if isinstance(i, QgsLayoutItemMap)
        }
        frame_rows = [r for r in report.items if r.subject.startswith("Map frame")]
        assert frame_rows, [r.subject for r in report.items]
        assert all(r.item_id in ids.values() for r in frame_rows)
        assert {r.item_id for r in frame_rows} == set(ids.values())

    def test_the_badge_opens_checks_on_that_map(self, qgis_app, project) -> None:
        dialog, cartograph, _layout, maps = _setup(project)
        try:
            item_id = maps["inset"].uuid()
            cartograph.open_section("frames")
            cartograph.frames_list.set_issue_count(item_id, 2)
            cartograph.frames_list.card(item_id).issues_button.click()
            assert cartograph.panel_tabs.currentIndex() == cartograph._tab_keys.index(
                "checks"
            )
            assert dialog.fidelity_panel.search.text() == "'inset'"
        finally:
            dialog.close()

    def test_entries_and_the_page_select_each_other(self, qgis_app, project) -> None:
        dialog, cartograph, layout, maps = _setup(project)
        try:
            cartograph.open_section("frames")
            qgis_app.processEvents()
            inset, main = maps["inset"], maps["main"]
            card = cartograph.frames_list.card(inset.uuid())
            QTest.mouseClick(card.name_label, Qt.MouseButton.LeftButton)
            assert layout.selectedLayoutItems() == [inset]
            # Selecting on the page marks the other entry.
            layout.setSelectedItem(main)
            assert cartograph.frames_list.card(main.uuid()).property("selected")
            assert not card.property("selected")
            # Choosing the entry selects it again.
            QTest.mouseClick(card.name_label, Qt.MouseButton.LeftButton)
            assert layout.selectedLayoutItems() == [inset]
            assert card.property("selected")
            # A non-map item clears the marks.
            layout.deselectAll()
            assert not card.property("selected")
        finally:
            dialog.close()
