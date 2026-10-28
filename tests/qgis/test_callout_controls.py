"""The Edit tab's statistic callouts, driven as a user would: type a value,
press Add, select a callout on the page, change or remove it, undo.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.ui.cartograph.callouts import layout_callouts
from nika_onlymap_exporter.ui.cartograph.item_controls import ContextControls
from nika_onlymap_exporter.ui.cartograph.item_text_size import step_text_size
from nika_onlymap_exporter.ui.cartograph.layout_items import delete_items

pytest.importorskip("qgis.core")


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _layout(parcels, project, template_id="dashboard", name="signal"):
    extent = parcels.extent()
    extent.grow(200)
    return cartograph_from_view(
        extent, UTM, [parcels], project, template_id=template_id, layout_name=name
    ).layout


def _controls(layout) -> ContextControls:
    controls = ContextControls()
    controls.set_layout(layout)
    controls.resize(320, 600)
    controls.show()
    return controls


def _type(edit, text: str) -> None:
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)


def _words(layout) -> list[tuple[str, str]]:
    return [(c.value, c.label) for c in layout_callouts(layout)]


def _select(layout, controls, item_id: str) -> None:
    layout.deselectAll()
    layout.itemById(item_id).setSelected(True)
    controls.refresh()


def _open_form(controls: ContextControls) -> None:
    """What "Statistic callout..." in the Add item menu does."""
    controls.begin_callout()


class TestOnlyWhereTheDesignHasThem:
    def test_form_opens_for_signal_never_for_other_designs(
        self, qgis_app, parcels, project
    ) -> None:
        signal = _controls(_layout(parcels, project))
        classic = _controls(_layout(parcels, project, "classic", "classic"))
        try:
            assert not signal.callout_controls.isVisibleTo(signal)
            _open_form(signal)
            _open_form(classic)
            assert signal.callout_controls.isVisibleTo(signal)
            assert not classic.callout_controls.isVisibleTo(classic)
        finally:
            signal.close()
            classic.close()


class TestAdding:
    def test_type_a_value_and_press_add(self, qgis_app, parcels, project) -> None:
        layout = _layout(parcels, project)
        controls = _controls(layout)
        edits: list[int] = []
        controls.edited.connect(lambda: edits.append(1))
        panel = controls.callout_controls
        _open_form(controls)
        try:
            assert not panel.add_button.isEnabled()
            _type(panel.value_edit, "214")
            _type(panel.label_edit, "parcels surveyed")
            assert panel.add_button.isEnabled()
            stack = layout.undoStack().stack()
            before = stack.index()
            QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
            assert _words(layout) == [("214", "parcels surveyed")]
            assert stack.index() == before + 1
            assert edits
            # The new callout is selected, ready for its lettering.
            assert layout.selectedLayoutItems()[0].id() == "figure-0"
            stack.undo()
            assert _words(layout) == []
            stack.redo()
            assert _words(layout) == [("214", "parcels surveyed")]
        finally:
            controls.close()

    def test_a_blank_value_cannot_be_added(self, qgis_app, parcels, project) -> None:
        layout = _layout(parcels, project)
        controls = _controls(layout)
        panel = controls.callout_controls
        _open_form(controls)
        try:
            _type(panel.label_edit, "only a line")
            assert not panel.add_button.isEnabled()
            QTest.keyClick(panel.label_edit, Qt.Key.Key_Return)
            assert _words(layout) == []
        finally:
            controls.close()

    def test_enter_adds_and_a_fourth_is_refused(
        self, qgis_app, parcels, project
    ) -> None:
        layout = _layout(parcels, project)
        controls = _controls(layout)
        panel = controls.callout_controls
        try:
            for index in range(3):
                layout.deselectAll()
                _open_form(controls)
                _type(panel.value_edit, str(index + 1))
                QTest.keyClick(panel.value_edit, Qt.Key.Key_Return)
            assert _words(layout) == [("1", ""), ("2", ""), ("3", "")]
            layout.deselectAll()
            _open_form(controls)
            # At three there is nothing to create: the form stays shut.
            assert not panel.isVisibleTo(controls)
        finally:
            controls.close()


class TestSelectedOnThePage:
    def _three(self, parcels, project):
        layout = _layout(parcels, project)
        controls = _controls(layout)
        panel = controls.callout_controls
        for value, label in (("214", "parcels"), ("12 km", "of road"), ("9", "wells")):
            layout.deselectAll()
            _open_form(controls)
            _type(panel.value_edit, value)
            _type(panel.label_edit, label)
            QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
        return layout, controls, panel

    def test_selecting_one_shows_its_words_and_remove(
        self, qgis_app, parcels, project
    ) -> None:
        layout, controls, panel = self._three(parcels, project)
        try:
            _select(layout, controls, "figure-1-label")
            assert panel.value_edit.text() == "12 km"
            assert panel.label_edit.text() == "of road"
            assert panel.remove_button.isVisibleTo(controls)
            assert not panel.add_button.isVisibleTo(controls)
        finally:
            controls.close()

    def test_changing_the_words_is_one_undo_step(
        self, qgis_app, parcels, project
    ) -> None:
        layout, controls, panel = self._three(parcels, project)
        try:
            _select(layout, controls, "figure-1")
            stack = layout.undoStack().stack()
            before = stack.index()
            _type(panel.value_edit, "13 km")
            QTest.keyClick(panel.value_edit, Qt.Key.Key_Return)
            panel.value_edit.clearFocus()
            assert layout.itemById("figure-1").text() == "13 km"
            assert stack.index() > before
            stack.undo()
            assert layout.itemById("figure-1").text() == "12 km"
        finally:
            controls.close()

    def test_remove_closes_up_and_undo_brings_it_back(
        self, qgis_app, parcels, project
    ) -> None:
        layout, controls, panel = self._three(parcels, project)
        try:
            _select(layout, controls, "figure-0")
            stack = layout.undoStack().stack()
            before = stack.index()
            QTest.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)
            assert _words(layout) == [("12 km", "of road"), ("9", "wells")]
            assert stack.index() == before + 1
            stack.undo()
            assert _words(layout) == [
                ("214", "parcels"),
                ("12 km", "of road"),
                ("9", "wells"),
            ]
        finally:
            controls.close()

    def test_the_delete_key_takes_the_whole_callout(
        self, qgis_app, parcels, project
    ) -> None:
        layout, controls, _panel = self._three(parcels, project)
        try:
            delete_items(layout, [layout.itemById("figure-2")])
            assert _words(layout) == [("214", "parcels"), ("12 km", "of road")]
            assert layout.itemById("rule-figure-2") is None
            assert layout.itemById("figure-2-label") is None
        finally:
            controls.close()

    def test_lettering_uses_the_text_controls(self, qgis_app, parcels, project) -> None:
        layout, controls, _panel = self._three(parcels, project)
        try:
            _select(layout, controls, "figure-0")
            steps: list[bool] = []
            controls.text_size_step.connect(steps.append)
            before = layout.itemById("figure-0").textFormat().size()
            QTest.mouseClick(controls.text_bigger_button, Qt.MouseButton.LeftButton)
            assert steps == [True]
            step_text_size(layout, layout.itemById("figure-0"), True)
            assert layout.itemById("figure-0").textFormat().size() > before
            assert controls.bold_button.isVisibleTo(controls)
            assert controls.colour_button.isVisibleTo(controls)
        finally:
            controls.close()


class TestWhereItShows:
    def test_only_with_nothing_or_a_callout_selected(
        self, qgis_app, parcels, project
    ) -> None:
        from qgis.core import (
            QgsLayoutItemLabel,
            QgsLayoutItemLegend,
            QgsLayoutItemMap,
        )

        from nika_onlymap_exporter.ui.cartograph.callouts import (
            add_callout,
            callout_index,
        )

        layout = _layout(parcels, project)
        assert add_callout(layout, "12 km", "of road")
        controls = _controls(layout)
        try:
            panel = controls.callout_controls
            layout.deselectAll()
            controls.refresh()
            assert not panel.isVisibleTo(controls)
            _select(layout, controls, "figure-0")
            assert panel.isVisibleTo(controls)
            plain_label = next(
                i
                for i in layout.items()
                if isinstance(i, QgsLayoutItemLabel) and callout_index(i) is None
            )
            others = [
                plain_label,
                next(i for i in layout.items() if isinstance(i, QgsLayoutItemMap)),
            ]
            legend = QgsLayoutItemLegend(layout)
            layout.addLayoutItem(legend)
            others.append(legend)
            for item in others:
                layout.deselectAll()
                item.setSelected(True)
                controls.refresh()
                assert not panel.isVisibleTo(controls), type(item).__name__
        finally:
            controls.close()
