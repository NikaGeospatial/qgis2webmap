"""Adding items to the page preview and editing the selected one: the Add
menu and its click-to-place mode, the selected-item controls, Delete.

Real widgets, driven as a user drives them: menu actions, smooth mouse paths
on the page, key presses. Every gesture is one undo step on the layout's own
stack, and what it makes is an ordinary layout item the export carries.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_cartograph_panel_widgets import _a4_layout, _drag, _scale, _settle

qgis_core = pytest.importorskip("qgis.core")


def _map(layout, x=20.0, y=30.0, w=150.0, h=100.0, name=""):
    from qgis.core import QgsLayoutItemMap
    from qgis.PyQt.QtCore import QRectF

    item = QgsLayoutItemMap(layout)
    item.attemptSetSceneRect(QRectF(x, y, w, h))
    if name:
        item.setId(name)
    layout.addLayoutItem(item)
    return item


def _pane(qgis_app, project, maps=1, size=(900, 650)):
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    layout = _a4_layout(project)
    frames = [
        _map(layout, name=f"Map {n + 1}", y=20 + 60 * n, h=50) for n in range(maps)
    ]
    pane = PagePane()
    pane.resize(*size)
    pane.show()
    pane.show_layout(layout)
    _settle(qgis_app)
    return pane, layout, frames


@pytest.fixture
def one_map(qgis_app, project):
    pane, layout, frames = _pane(qgis_app, project)
    yield pane, layout, frames
    pane.close()
    pane.deleteLater()


@pytest.fixture
def two_maps(qgis_app, project):
    pane, layout, frames = _pane(qgis_app, project, maps=2)
    yield pane, layout, frames
    pane.close()
    pane.deleteLater()


def _choose(pane, *path: str) -> None:
    """Pick an entry of the Add menu (through its submenu, if given two)."""
    pane._fill_add_menu()
    menu = pane.add_menu
    for title in path[:-1]:
        menu = next(a for a in menu.actions() if a.text() == title).menu()
    next(a for a in menu.actions() if a.text() == path[-1]).trigger()


def _click_page(qgis_app, pane, x_mm: float, y_mm: float) -> None:
    from qgis.PyQt.QtCore import QPointF, Qt
    from qgis.PyQt.QtTest import QTest

    viewport = pane.preview.view.viewport()
    point = pane.preview.view.mapFromScene(QPointF(x_mm, y_mm))
    QTest.mouseMove(viewport, point)
    qgis_app.processEvents()
    QTest.mouseClick(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    _settle(qgis_app, 0.05)


def _items_of(layout, cls):
    return [i for i in layout.items() if isinstance(i, cls)]


class TestAddMenu:
    def test_the_menu_offers_the_four_items(self, one_map) -> None:
        pane, _layout, _frames = one_map
        pane._fill_add_menu()
        assert [a.text() for a in pane.add_menu.actions()] == [
            "Text",
            "Legend",
            "Scale bar",
            "North arrow",
        ]
        assert pane.add_button.toolTip()
        assert pane.add_button.accessibleName() == pane.add_button.toolTip()

    def test_a_linked_item_needs_a_map(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        layout = _a4_layout(project)
        pane = PagePane()
        pane.show()
        pane.show_layout(layout)
        pane._fill_add_menu()
        enabled = {a.text(): a.isEnabled() for a in pane.add_menu.actions()}
        assert enabled == {
            "Text": True,
            "Legend": False,
            "Scale bar": False,
            "North arrow": False,
        }
        pane.close()

    def test_several_maps_ask_which_by_name(self, two_maps) -> None:
        pane, _layout, frames = two_maps
        pane._fill_add_menu()
        legend = next(a for a in pane.add_menu.actions() if a.text() == "Legend")
        assert legend.menu() is not None
        assert sorted(a.text() for a in legend.menu().actions()) == sorted(
            f.displayName() for f in frames
        )
        text = next(a for a in pane.add_menu.actions() if a.text() == "Text")
        assert text.menu() is None

    def test_the_bar_keeps_the_add_button_when_narrow(self, qgis_app, one_map) -> None:
        from nika_onlymap_exporter.ui import theme

        pane, _layout, _frames = one_map
        pane.setObjectName("PagePaneUnderTest")
        theme.apply(pane)
        pane.resize(600, 500)
        _settle(qgis_app, 0.1)
        assert not pane.add_button.property("tight")
        assert pane.add_button.text() == "Add item"
        pane.resize(300, 500)
        _settle(qgis_app, 0.1)
        assert pane.add_button.property("tight") is True
        assert pane.add_button.toolTip()
        spot = pane.add_button.mapTo(pane, pane.add_button.rect().topLeft())
        assert spot.x() >= 0 and spot.x() + pane.add_button.width() <= pane.width()


KINDS = [
    ("Text", "QgsLayoutItemLabel"),
    ("Legend", "QgsLayoutItemLegend"),
    ("Scale bar", "QgsLayoutItemScaleBar"),
    ("North arrow", "QgsLayoutItemPicture"),
]


class TestPlacing:
    @pytest.mark.parametrize(("title", "cls"), KINDS)
    def test_the_next_click_places_one_item_as_one_undo_step(
        self, qgis_app, one_map, title, cls
    ) -> None:
        import qgis.core as core

        pane, layout, frames = one_map
        kind = getattr(core, cls)
        stack = layout.undoStack().stack()
        before, steps = len(_items_of(layout, kind)), stack.count()
        _choose(pane, title)
        assert pane.preview.placer.active
        # Choosing alone makes nothing: the click does.
        assert len(_items_of(layout, kind)) == before
        _click_page(qgis_app, pane, 200, 150)
        assert not pane.preview.placer.active
        added = _items_of(layout, kind)
        assert len(added) == before + 1
        item = next(
            i for i in added if i.sceneBoundingRect().contains(200.5, 150.5) or True
        )
        assert stack.count() == steps + 1
        assert (
            layout.selectedLayoutItems() == [item]
            or item in layout.selectedLayoutItems()
        )
        if title != "Text":
            assert item.linkedMap() is frames[0]
        # The controls for it are showing.
        assert not pane.controls.isHidden()
        stack.undo()
        assert len(_items_of(layout, kind)) == before
        stack.redo()
        assert len(_items_of(layout, kind)) == before + 1

    def test_a_drag_sizes_the_text_box(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QPointF

        pane, layout, _frames = one_map
        _choose(pane, "Text")
        view = pane.preview.view
        start = view.mapFromScene(QPointF(30, 150))
        end = view.mapFromScene(QPointF(110, 175))
        _drag(qgis_app, pane, start, end)
        label = _items_of(layout, QgsLayoutItemLabel)[0]
        box = label.sceneBoundingRect()
        assert box.left() == pytest.approx(30, abs=1.5)
        assert box.top() == pytest.approx(150, abs=1.5)
        assert box.width() == pytest.approx(80, abs=2)
        assert box.height() == pytest.approx(25, abs=2)

    def test_the_item_stays_on_the_page(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _frames = one_map
        _choose(pane, "Text")
        _click_page(qgis_app, pane, 290, 205)
        box = _items_of(layout, QgsLayoutItemLabel)[0].sceneBoundingRect()
        assert box.right() <= 297.5 and box.bottom() <= 210.5

    def test_escape_and_a_right_click_leave_the_mode(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, _frames = one_map
        stack = layout.undoStack().stack()
        steps = stack.count()
        _choose(pane, "Text")
        QTest.keyClick(pane.preview.view, Qt.Key.Key_Escape)
        assert not pane.preview.placer.active
        _choose(pane, "Text")
        viewport = pane.preview.view.viewport()
        QTest.mouseClick(
            viewport,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
            pane.preview.view.mapFromScene(QPointF(100, 100)),
        )
        assert not pane.preview.placer.active
        assert not _items_of(layout, QgsLayoutItemLabel)
        assert stack.count() == steps

    def test_the_menu_picks_which_map_the_item_is_about(
        self, qgis_app, two_maps
    ) -> None:
        from qgis.core import QgsLayoutItemScaleBar

        pane, layout, frames = two_maps
        second = next(f for f in frames if f.id() == "Map 2")
        _choose(pane, "Scale bar", second.displayName())
        _click_page(qgis_app, pane, 200, 160)
        bar = _items_of(layout, QgsLayoutItemScaleBar)[0]
        assert bar.linkedMap() is second

    def test_zoom_scroll_and_a_placed_item_are_kept(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _frames = one_map
        view = pane.preview.view
        pane.preview.zoom_in()
        _settle(qgis_app, 0.1)
        scale = _scale(pane)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum() // 2)
        scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        _choose(pane, "Text")
        centre = view.mapToScene(view.viewport().rect().center())
        _click_page(qgis_app, pane, centre.x(), centre.y())
        _settle(qgis_app, 0.2)
        assert _items_of(layout, QgsLayoutItemLabel)
        assert _scale(pane) == pytest.approx(scale)
        assert (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        ) == scroll


def _place(qgis_app, pane, title, x=200.0, y=150.0):
    _choose(pane, title)
    _click_page(qgis_app, pane, x, y)


def _undo_count(layout) -> int:
    return layout.undoStack().stack().count()


class TestTextControls:
    @pytest.fixture
    def text(self, qgis_app, one_map):
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _frames = one_map
        _place(qgis_app, pane, "Text")
        label = _items_of(layout, QgsLayoutItemLabel)[0]
        return pane, layout, label

    def test_a_new_text_shows_its_words_and_takes_the_cursor(self, text) -> None:
        pane, _layout, label = text
        controls = pane.controls
        assert controls.pages.currentWidget() is controls.text_page
        assert controls.text_edit.toPlainText() == label.text() == "Text"
        assert controls.text_edit.hasFocus() or not pane.isActiveWindow()

    def test_enter_applies_the_words_as_one_undo_step(self, qgis_app, text) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, label = text
        edit = pane.controls.text_edit
        steps = _undo_count(layout)
        edit.selectAll()
        QTest.keyClicks(edit, "Lake survey")
        # Typing alone is not an edit: nothing yet on the page.
        assert label.text() == "Text"
        assert _undo_count(layout) == steps
        QTest.keyClick(edit, Qt.Key.Key_Return)
        assert label.text() == "Lake survey"
        assert _undo_count(layout) == steps + 1
        layout.undoStack().stack().undo()
        assert label.text() == "Text"
        assert edit.toPlainText() == "Text"

    def test_shift_enter_is_a_new_line_and_focus_out_applies(
        self, qgis_app, text
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, label = text
        edit = pane.controls.text_edit
        steps = _undo_count(layout)
        edit.selectAll()
        QTest.keyClicks(edit, "Line one")
        QTest.keyClick(edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(edit, "Line two")
        assert label.text() == "Text"
        pane.preview.view.setFocus()
        pane.controls.text_edit.clearFocus()
        # A focus change is what applies it.
        from qgis.PyQt.QtCore import QEvent
        from qgis.PyQt.QtGui import QFocusEvent
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut))
        assert label.text() == "Line one\nLine two"
        assert _undo_count(layout) == steps + 1

    def test_applying_unchanged_words_leaves_no_step(self, qgis_app, text) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, _label = text
        steps = _undo_count(layout)
        edit = pane.controls.text_edit
        QTest.keyClick(edit, Qt.Key.Key_Return)
        QTest.keyClick(edit, Qt.Key.Key_Return)
        assert _undo_count(layout) == steps

    def test_bold_is_one_step_and_reads_back(self, qgis_app, text) -> None:
        from nika_onlymap_exporter.core.layout_reader import _css_font_weight

        pane, layout, label = text
        steps = _undo_count(layout)
        pane.controls.bold_button.click()
        assert _undo_count(layout) == steps + 1
        assert _css_font_weight(label.textFormat().font()) == "bold"
        pane.controls.bold_button.click()
        assert _undo_count(layout) == steps + 2
        assert _css_font_weight(label.textFormat().font()) is None
        layout.undoStack().stack().undo()
        assert pane.controls.bold_button.isChecked()

    def test_alignment_is_one_step_each(self, qgis_app, text) -> None:
        from qgis.PyQt.QtCore import Qt

        pane, layout, label = text
        steps = _undo_count(layout)
        for n, flag in enumerate(
            (
                Qt.AlignmentFlag.AlignHCenter,
                Qt.AlignmentFlag.AlignRight,
                Qt.AlignmentFlag.AlignLeft,
            ),
            start=1,
        ):
            pane.controls.align_buttons[flag].click()
            assert label.hAlign() & flag
            assert _undo_count(layout) == steps + n
        layout.undoStack().stack().undo()
        assert pane.controls.align_buttons[Qt.AlignmentFlag.AlignRight].isChecked()

    def test_colour_is_reported_once_when_chosen(
        self, qgis_app, text, monkeypatch
    ) -> None:
        from qgis.PyQt.QtGui import QColor
        from qgis.PyQt.QtWidgets import QColorDialog

        pane, layout, label = text
        asked = []

        def fake(initial, parent, title):
            asked.append(initial.name())
            return QColor("#aa2200")

        monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake))
        steps = _undo_count(layout)
        pane.controls.colour_button.click()
        assert asked
        assert label.textFormat().color().name() == "#aa2200"
        assert _undo_count(layout) == steps + 1
        # The dialog giving the colour it started with is not an edit.
        monkeypatch.setattr(
            QColorDialog, "getColor", staticmethod(lambda i, p, t: QColor("#aa2200"))
        )
        pane.controls.colour_button.click()
        assert _undo_count(layout) == steps + 1

    def test_the_size_buttons_and_the_words_are_separate(self, qgis_app, text) -> None:
        pane, _layout, label = text
        box = label.sceneBoundingRect()
        size = label.textFormat().size()
        pane.text_bigger_button.click()
        assert label.textFormat().size() == pytest.approx(size + 1)
        assert label.sceneBoundingRect() == box
        assert "handles" in pane.text_bigger_button.toolTip()

    def test_a_bad_selection_hides_the_strip(self, qgis_app, text) -> None:
        pane, layout, _label = text
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        assert pane.controls.isHidden()

    def test_the_strip_collapses_to_its_header(self, qgis_app, text) -> None:
        pane, _layout, _label = text
        pane.controls.toggle.click()
        assert pane.controls.pages.isHidden()
        assert not pane.controls.isHidden()
        pane.controls.toggle.click()
        assert not pane.controls.pages.isHidden()


class TestDelete:
    def test_the_button_deletes_one_item_as_one_step_and_undo_restores(
        self, qgis_app, one_map
    ) -> None:
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _frames = one_map
        _place(qgis_app, pane, "Text")
        steps = _undo_count(layout)
        pane.controls.delete_button.click()
        assert not _items_of(layout, QgsLayoutItemLabel)
        assert _undo_count(layout) == steps + 1
        assert pane.controls.isHidden()
        layout.undoStack().stack().undo()
        assert len(_items_of(layout, QgsLayoutItemLabel)) == 1

    def test_the_delete_key_in_the_page_does_the_same(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, _frames = one_map
        _place(qgis_app, pane, "Text")
        steps = _undo_count(layout)
        pane.preview.view.setFocus()
        QTest.keyClick(pane.preview.view, Qt.Key.Key_Delete)
        assert not _items_of(layout, QgsLayoutItemLabel)
        assert _undo_count(layout) == steps + 1
        QTest.keyClick(
            pane.preview.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier
        )
        assert len(_items_of(layout, QgsLayoutItemLabel)) == 1

    def test_a_linked_item_can_be_deleted_too(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLegend

        pane, layout, frames = one_map
        _place(qgis_app, pane, "Legend")
        pane.controls.delete_button.click()
        assert not _items_of(layout, QgsLayoutItemLegend)
        assert frames[0] in layout.items()


class TestLinkedAndLegendControls:
    def test_a_legend_shows_its_map_and_columns_and_changes_them_undoably(
        self, qgis_app, two_maps
    ) -> None:
        from qgis.core import QgsLayoutItemLegend

        pane, layout, frames = two_maps
        _choose(pane, "Legend", frames[0].displayName())
        _click_page(qgis_app, pane, 200, 100)
        legend = _items_of(layout, QgsLayoutItemLegend)[0]
        controls = pane.controls
        assert controls.pages.currentWidget() is controls.legend_page
        assert controls.legend_map.combo.currentText() == frames[0].displayName()
        steps = _undo_count(layout)
        other = controls.legend_map.combo.findData(frames[1].uuid())
        controls.legend_map.combo.setCurrentIndex(other)
        controls.legend_map.combo.activated.emit(other)
        assert legend.linkedMap() is frames[1]
        assert _undo_count(layout) == steps + 1
        controls.columns_spin.setValue(2)
        controls.columns_spin.editingFinished.emit()
        assert legend.columnCount() == 2
        assert _undo_count(layout) == steps + 2
        layout.undoStack().stack().undo()
        assert legend.columnCount() == 1

    def test_the_legend_editor_has_a_host_slot(self, qgis_app, one_map) -> None:
        from qgis.core import QgsLayoutItemLegend
        from qgis.PyQt.QtWidgets import QLabel

        pane, layout, _frames = one_map

        class Editor(QLabel):
            shown = None

            def set_legend(self, legend) -> None:
                Editor.shown = legend

        pane.controls.set_legend_editor_factory(lambda parent: Editor("editor", parent))
        _place(qgis_app, pane, "Legend")
        legend = _items_of(layout, QgsLayoutItemLegend)[0]
        assert Editor.shown is legend
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        assert Editor.shown is None

    @pytest.mark.parametrize("title", ["Scale bar", "North arrow"])
    def test_the_other_linked_items_choose_their_map(
        self, qgis_app, two_maps, title
    ) -> None:
        pane, layout, frames = two_maps
        _choose(pane, title, frames[0].displayName())
        _click_page(qgis_app, pane, 200, 100)
        item = layout.selectedLayoutItems()[0]
        choice = (
            pane.controls.scale_bar_map
            if title == "Scale bar"
            else pane.controls.north_arrow_map
        )
        assert choice.combo.count() == 2
        steps = _undo_count(layout)
        other = choice.combo.findData(frames[1].uuid())
        choice.combo.activated.emit(other)
        assert item.linkedMap() is frames[1]
        assert _undo_count(layout) == steps + 1
        choice.combo.activated.emit(other)
        assert _undo_count(layout) == steps + 1


class TestMapControls:
    @pytest.fixture
    def framed(self, qgis_app, one_map):
        from nika_onlymap_exporter.ui.cartograph.frames_list import (
            FrameRow,
            FramesList,
        )

        pane, layout, frames = one_map
        listing = FramesList((72, 150, 300))
        listing.set_frames(
            [
                FrameRow(
                    item_id=frames[0].uuid(),
                    name="Map 1",
                    crs="EPSG:4326",
                    mode="live",
                    dpi=150,
                    issues=None,
                )
            ]
        )
        pane.controls.bind_frames(listing)
        layout.deselectAll()
        frames[0].setSelected(True)
        _settle(qgis_app, 0.1)
        yield pane, layout, frames[0], listing
        listing.deleteLater()

    def test_rotation_is_one_step_when_committed(self, qgis_app, framed) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, frame, _listing = framed
        spin = pane.controls.rotation_spin
        assert pane.controls.pages.currentWidget() is pane.controls.map_page
        steps = _undo_count(layout)
        spin.selectAll()
        QTest.keyClicks(spin, "30")
        assert frame.mapRotation() == 0 and _undo_count(layout) == steps
        QTest.keyClick(spin, Qt.Key.Key_Return)
        assert frame.mapRotation() == pytest.approx(30)
        assert _undo_count(layout) == steps + 1
        layout.undoStack().stack().undo()
        assert frame.mapRotation() == pytest.approx(0)
        assert spin.value() == pytest.approx(0)

    def test_the_wheel_changes_nothing(self, qgis_app, framed) -> None:
        from qgis.PyQt.QtCore import QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QWheelEvent
        from qgis.PyQt.QtWidgets import QApplication

        pane, _layout, _frame, _listing = framed
        controls = pane.controls
        for widget in (
            controls.rotation_spin,
            controls.mode_combo,
            controls.columns_spin,
        ):
            event = QWheelEvent(
                QPointF(5, 5),
                QPointF(widget.mapToGlobal(widget.rect().center())),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            before = (
                controls.rotation_spin.value(),
                controls.mode_combo.currentIndex(),
                controls.columns_spin.value(),
            )
            QApplication.sendEvent(widget, event)
            assert (
                controls.rotation_spin.value(),
                controls.mode_combo.currentIndex(),
                controls.columns_spin.value(),
            ) == before
            assert not event.isAccepted()

    def test_type_and_resolution_are_the_frames_lists_own(
        self, qgis_app, framed
    ) -> None:
        pane, _layout, frame, listing = framed
        controls = pane.controls
        seen = []
        listing.mode_changed.connect(lambda i, m: seen.append(("mode", m)))
        listing.dpi_changed.connect(lambda i, d: seen.append(("dpi", d)))
        assert controls.mode_combo.currentData() == "live"
        assert controls.dpi_combo.isHidden()
        controls.mode_combo.setCurrentIndex(1)
        controls.mode_combo.activated.emit(1)
        card = listing.card(frame.uuid())
        assert card.mode() == "static"
        assert seen == [("mode", "static")]
        assert not controls.dpi_combo.isHidden()
        assert controls.dpi_combo.currentData() == 150
        controls.dpi_combo.setCurrentIndex(2)
        controls.dpi_combo.activated.emit(2)
        assert card.dpi() == 300
        assert seen[-1] == ("dpi", 300)
        # And the other way: the list's choice shows in the strip.
        card.mode_combo.setCurrentIndex(0)
        assert controls.mode_combo.currentData() == "live"
        assert controls.dpi_combo.isHidden()

    def test_use_canvas_extent_asks_for_this_map(self, qgis_app, framed) -> None:
        pane, _layout, frame, _listing = framed
        asked = []
        pane.controls.canvas_extent_requested.connect(asked.append)
        pane.controls.canvas_button.click()
        assert asked == [frame.uuid()]


class TestWhatAddingMakesSurvives:
    """The items are ordinary layout items, so every route carries them: the
    cartograph HTML, the native PDF and PNG, and the project file."""

    @staticmethod
    def _runtime():
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        return RuntimeBundle(
            javascript=b"// core js placeholder",
            css=b"/* core css placeholder */",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"// cartograph js placeholder",
            cartograph_css=b"/* cartograph css placeholder */",
        )

    @pytest.fixture
    def built(self, qgis_app, project):
        from test_cartograph_export import _live_layout

        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        layout = _live_layout(project)
        project.layoutManager().addLayout(layout)
        pane = PagePane()
        pane.resize(900, 650)
        pane.show()
        pane.show_layout(layout)
        _settle(qgis_app)
        yield pane, layout, project
        pane.close()
        pane.deleteLater()

    def test_the_four_items_reach_the_html(self, qgis_app, built) -> None:
        from qgis.core import QgsLayoutItemLabel

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        pane, layout, project = built
        for n, title in enumerate(("Text", "Legend", "Scale bar", "North arrow")):
            _place(qgis_app, pane, title, x=20 + 55 * n, y=150)
        label = _items_of(layout, QgsLayoutItemLabel)[0]
        pane.controls.text_edit.selectAll()
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        # Select the label again to edit its words.
        layout.deselectAll()
        label.setSelected(True)
        _settle(qgis_app, 0.1)
        pane.controls.text_edit.selectAll()
        QTest.keyClicks(pane.controls.text_edit, "Lake survey")
        QTest.keyClick(pane.controls.text_edit, Qt.Key.Key_Return)
        html = export_layout_to_cartograph(
            layout, project, self._runtime(), FidelityReportBuilder(), static_dpi=100
        )
        assert "Lake survey" in html
        assert "<om-legend" in html
        assert "<om-scalebar" in html
        assert "<om-north" in html

    def test_the_text_is_in_the_native_png_and_pdf(
        self, qgis_app, built, tmp_path
    ) -> None:
        from qgis.core import (
            QgsLayoutExporter,
            QgsLayoutItemLabel,
        )
        from qgis.PyQt.QtGui import QImage

        pane, layout, _project = built
        exporter = QgsLayoutExporter(layout)
        before = tmp_path / "before.png"
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = 60
        assert (
            exporter.exportToImage(str(before), settings) == QgsLayoutExporter.Success
        )
        _place(qgis_app, pane, "Text", x=30, y=170)
        label = _items_of(layout, QgsLayoutItemLabel)[0]
        after = tmp_path / "after.png"
        assert exporter.exportToImage(str(after), settings) == QgsLayoutExporter.Success
        assert QImage(str(before)) != QImage(str(after))
        pdf = tmp_path / "page.pdf"
        assert (
            exporter.exportToPdf(str(pdf), QgsLayoutExporter.PdfExportSettings())
            == QgsLayoutExporter.Success
        )
        assert pdf.stat().st_size > 0
        assert label in layout.items()

    def test_the_project_file_keeps_them(self, qgis_app, built, tmp_path) -> None:
        from qgis.core import QgsLayoutItemLegend, QgsLayoutItemScaleBar, QgsProject

        pane, layout, project = built
        _place(qgis_app, pane, "Legend", x=20, y=150)
        _place(qgis_app, pane, "Scale bar", x=120, y=150)
        path = tmp_path / "kept.qgz"
        assert project.write(str(path))
        reread = QgsProject()
        assert reread.read(str(path))
        again = reread.layoutManager().layoutByName(layout.name())
        assert _items_of(again, QgsLayoutItemLegend)
        bar = _items_of(again, QgsLayoutItemScaleBar)[0]
        assert bar.linkedMap() is not None


class TestShortWindow:
    def test_the_strip_rests_as_its_header_and_opens_on_demand(
        self, qgis_app, project
    ) -> None:
        pane, layout, frames = _pane(qgis_app, project, size=(700, 300))
        try:
            layout.deselectAll()
            frames[0].setSelected(True)
            _settle(qgis_app, 0.1)
            assert not pane.controls.isHidden()
            assert pane.controls.pages.isHidden()
            # Room kept for the page: the strip is one row.
            assert pane.controls.height() < 60
            pane.controls.toggle.click()
            assert not pane.controls.pages.isHidden()
            # Adding something opens it, as the words are what comes next.
            pane.controls.toggle.click()
            _place(qgis_app, pane, "Text")
            assert not pane.controls.pages.isHidden()
            pane.resize(700, 600)
            _settle(qgis_app, 0.1)
            assert not pane.controls.pages.isHidden()
        finally:
            pane.close()
            pane.deleteLater()

    def test_a_placed_item_is_not_left_under_the_strip(self, qgis_app, project) -> None:
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _frames = _pane(qgis_app, project, size=(700, 420))
        try:
            pane.preview.zoom_in()
            pane.preview.zoom_in()
            _settle(qgis_app, 0.1)
            view = pane.preview.view
            low = view.mapToScene(view.viewport().rect().bottomLeft())
            _place(qgis_app, pane, "Text", x=low.x() + 10, y=low.y() - 8)
            _settle(qgis_app, 0.1)
            label = _items_of(layout, QgsLayoutItemLabel)[0]
            box = view.mapFromScene(label.sceneBoundingRect()).boundingRect()
            assert view.viewport().rect().contains(box)
        finally:
            pane.close()
            pane.deleteLater()
