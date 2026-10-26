"""The legend editor widget, driven like a user, and its hook in the page
preview: a double-click on a legend opens it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from legend_fixtures import CLASS_COUNT, build_world
from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication

from nika_onlymap_exporter.core import legend_edit as le
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane
from nika_onlymap_exporter.ui.cartograph.legend_editor import (
    LegendEditor,
)


@pytest.fixture
def world(qgis_app, project, tmp_path):
    return build_world(project, tmp_path / "data")


@pytest.fixture
def legend(world) -> QgsLayoutItemLegend:
    return next(i for i in world.layout.items() if isinstance(i, QgsLayoutItemLegend))


@pytest.fixture
def editor(world, legend):
    widget = LegendEditor()
    widget.resize(340, 560)
    widget.set_legend(legend)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.close()


def _listed(legend) -> list[str]:
    return [n.layerId() for n in legend.model().rootGroup().findLayers()]


def _pump() -> None:
    for _ in range(3):
        QApplication.processEvents()


def _click_check(editor: LegendEditor, item) -> None:
    """Click the item's check box (at the left of its row), like a user."""
    editor.tree.scrollToItem(item)
    rect = editor.tree.visualItemRect(item)
    # The box sits after the branch indent, at the start of the row's content.
    x = rect.left() + 9
    QTest.mouseClick(
        editor.tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, rect.center().y()),
    )
    _pump()


def _stack(world):
    return world.layout.undoStack().stack()


class TestEditorEntries:
    def test_lists_every_entry_with_labels_and_restorables(self, world, editor) -> None:
        names = {e.text(0) for e in editor.entry_items()}
        assert {
            "Named summits",
            "Land cover",
            "Class 01",
            "Elevation tint (m)",
        } <= names
        shade = editor.item_for(world.layers["hillshade"].id())
        assert shade.checkState(0) == Qt.CheckState.Unchecked
        assert editor.restore_button.isEnabled()
        assert "Hillshade" in editor.note.text()

    def test_a_click_on_the_box_hides_and_shows_a_layer(
        self, world, legend, editor
    ) -> None:
        summits = world.layers["summits"]
        before = _stack(world).index()
        _click_check(editor, editor.item_for(summits.id()))
        assert _stack(world).index() == before + 1
        assert summits.id() not in _listed(legend)
        layers = [
            layer.id()
            for m in world.layout.items()
            if isinstance(m, QgsLayoutItemMap)
            for layer in m.layersToRender()
        ]
        assert summits.id() in layers  # still on the map
        assert editor.item_for(summits.id()).checkState(0) == Qt.CheckState.Unchecked
        _click_check(editor, editor.item_for(summits.id()))
        assert summits.id() in _listed(legend)
        assert _stack(world).index() == before + 2
        _stack(world).undo()
        _pump()
        assert summits.id() not in _listed(legend)
        assert editor.item_for(summits.id()).checkState(0) == Qt.CheckState.Unchecked

    def test_a_class_is_hidden_with_one_click(self, world, legend, editor) -> None:
        land = world.layers["land"]
        before = _stack(world).index()
        _click_check(editor, editor.item_for(land.id(), 2))
        assert _stack(world).index() == before + 1
        assert (
            not editor.item_for(land.id(), 2).data(0, Qt.ItemDataRole.UserRole).included
        )

    def test_typing_a_label_edits_the_legend_only(self, world, legend, editor) -> None:
        land = world.layers["land"]
        item = editor.item_for(land.id())
        editor.tree.setCurrentItem(item)
        editor.tree.setFocus()
        QTest.keyClick(editor.tree, Qt.Key.Key_F2)
        _pump()
        QTest.keyClick(
            editor.tree.viewport().focusWidget() or editor.tree,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )
        QTest.keyClicks(
            editor.tree.viewport().focusWidget() or editor.tree, "Cover types"
        )
        before = _stack(world).index()
        QTest.keyClick(
            editor.tree.viewport().focusWidget() or editor.tree, Qt.Key.Key_Return
        )
        _pump()
        assert _stack(world).index() == before + 1
        assert land.name() == "Land cover"
        assert editor.item_for(land.id()).text(0) == "Cover types"
        entry = next(
            e
            for e in le.entries(legend, world.project)
            if e.layer_id == land.id() and e.index is None
        )
        assert entry.label == "Cover types"

    def test_up_and_down_reorder_the_selected_entry(
        self, world, legend, editor
    ) -> None:
        land = world.layers["land"]
        editor.tree.setCurrentItem(editor.item_for(land.id()))
        before = _stack(world).index()
        QTest.mouseClick(editor.up_button, Qt.MouseButton.LeftButton)
        _pump()
        assert _stack(world).index() == before + 1
        assert _listed(legend)[0] == land.id()
        # The selection followed the entry.
        assert editor.tree.currentItem().text(0) == "Land cover"
        QTest.mouseClick(editor.down_button, Qt.MouseButton.LeftButton)
        _pump()
        assert _listed(legend)[1] == land.id()

    def test_restore_brings_back_in_one_step(self, world, legend, editor) -> None:
        before = _stack(world).index()
        QTest.mouseClick(editor.restore_button, Qt.MouseButton.LeftButton)
        _pump()
        assert _stack(world).index() == before + 1
        assert world.layers["hillshade"].id() in _listed(legend)
        assert not editor.restore_button.isEnabled()

    def test_ctrl_z_on_the_layout_updates_the_list(self, world, legend, editor) -> None:
        summits = world.layers["summits"]
        _click_check(editor, editor.item_for(summits.id()))
        _stack(world).undo()
        _pump()
        assert editor.item_for(summits.id()).checkState(0) == Qt.CheckState.Checked

    def test_columns_text_size_and_spacing_each_one_step(
        self, world, legend, editor
    ) -> None:
        before = _stack(world).index()
        editor.columns_spin.setValue(2)
        assert legend.columnCount() == 2
        editor.size_spin.setValue(editor.size_spin.value() + 2)
        editor.spacing_spin.setValue(5.0)
        assert _stack(world).index() == before + 3

    def test_the_wheel_does_not_change_the_controls(self, world, editor) -> None:
        from qgis.PyQt.QtCore import QPoint as Q
        from qgis.PyQt.QtCore import QPointF as P
        from qgis.PyQt.QtGui import QWheelEvent

        columns = editor.columns_spin.value()
        event = QWheelEvent(
            P(5, 5),
            P(5, 5),
            Q(0, 0),
            Q(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(editor.columns_spin, event)
        assert editor.columns_spin.value() == columns

    def test_a_long_legend_scrolls_and_keeps_its_place_after_an_edit(
        self, world, legend, editor
    ) -> None:
        land = world.layers["land"]
        last = editor.item_for(land.id(), CLASS_COUNT - 1)
        editor.tree.scrollToItem(last)
        top = editor.tree.verticalScrollBar().value()
        assert top > 0
        _click_check(editor, last)
        assert abs(editor.tree.verticalScrollBar().value() - top) <= 2

    def test_an_unavailable_range_is_marked_honestly(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import EXTENT, WGS84, ramp_layer, write_tif

        from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

        layer = ramp_layer(
            write_tif(tmp_path / "n.tif"), "Elevation (m)", nan_range=True
        )
        project.addMapLayer(layer)
        built = cartograph_from_view(
            EXTENT, WGS84, [layer], project, template_id="classic"
        )
        legend = next(
            i for i in built.layout.items() if isinstance(i, QgsLayoutItemLegend)
        )
        widget = LegendEditor()
        widget.set_legend(legend)
        text = " ".join(item.text(0) for item in widget.entry_items())
        assert "Colour ramp — range unavailable" in text
        assert "set min/max" not in text  # the long form is in the note below
        assert "range unavailable — set min/max in Symbology" in widget.note.text()
        shader = layer.renderer().shader().rasterShaderFunction()
        assert shader.minimumValue() != shader.minimumValue()
        widget.close()


class TestPreviewHook:
    def test_double_clicking_a_legend_opens_the_editor(self, world, legend) -> None:
        pane = PagePane()
        pane.resize(900, 640)
        pane.show_layout(world.layout)
        pane.show()
        pane.controls.resize(420, 700)
        pane.controls.show()
        preview = pane.preview
        _pump()
        preview._fit()
        _pump()
        centre = preview.view.mapFromScene(QPointF(legend.sceneBoundingRect().center()))
        assert preview.view.viewport().rect().contains(centre)
        QTest.mouseDClick(
            preview.view.viewport(), Qt.MouseButton.LeftButton, pos=centre
        )
        _pump()
        controls = pane.controls
        editor = controls._legend_editor
        assert controls.pages.currentWidget() is controls.legend_page
        assert not controls.pages.isHidden()
        assert isinstance(editor, LegendEditor) and editor.isVisible()
        assert editor.legend() is legend
        pane.close()

    def test_a_double_click_on_the_map_opens_nothing(self, world) -> None:
        pane = PagePane()
        pane.resize(900, 640)
        pane.show_layout(world.layout)
        pane.show()
        pane.controls.resize(420, 700)
        pane.controls.show()
        preview = pane.preview
        _pump()
        preview._fit()
        _pump()
        main = max(
            (i for i in world.layout.items() if isinstance(i, QgsLayoutItemMap)),
            key=lambda m: m.rect().width() * m.rect().height(),
        )
        centre = preview.view.mapFromScene(QPointF(main.sceneBoundingRect().center()))
        QTest.mouseDClick(
            preview.view.viewport(), Qt.MouseButton.LeftButton, pos=centre
        )
        _pump()
        assert (
            pane.controls._legend_editor is None
            or pane.controls._legend_editor.isHidden()
        )
        pane.close()
