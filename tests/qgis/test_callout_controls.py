"""The Edit tab's statistic callout, driven as a user would: select a callout
on the page, change its words or remove it, undo. Adding is the Add item menu
(`test_detail_map.py` and `test_edit_tab_empty.py`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtTest import QTest

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.ui.cartograph.callouts import add_callout, layout_callouts
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


def _add(layout, value, label="", y=20.0) -> None:
    """What a click on the page does after Add item > Statistic callout."""
    assert add_callout(layout, QRectF(20, y, 50, 30), value, label)


class TestAnyLayout:
    def test_a_selected_callout_shows_its_form_on_any_layout(
        self, qgis_app, parcels, project
    ) -> None:
        for template in ("dashboard", "classic"):
            layout = _layout(parcels, project, template, template)
            _add(layout, "214", "parcels")
            controls = _controls(layout)
            try:
                _select(layout, controls, "figure-0")
                assert controls.callout_controls.isVisibleTo(controls)
                assert controls.callout_controls.value_edit.text() == "214"
            finally:
                controls.close()


class TestSelectedOnThePage:
    def _three(self, parcels, project):
        layout = _layout(parcels, project)
        controls = _controls(layout)
        panel = controls.callout_controls
        for n, (value, label) in enumerate(
            (("214", "parcels"), ("12 km", "of road"), ("9", "wells"))
        ):
            _add(layout, value, label, y=20.0 + 40 * n)
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

    def test_remove_takes_that_one_and_undo_brings_it_back(
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

        from nika_onlymap_exporter.ui.cartograph.callouts import callout_index

        layout = _layout(parcels, project)
        _add(layout, "12 km", "of road")
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
