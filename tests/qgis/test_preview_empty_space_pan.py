"""A left-button drag that starts on empty space - bare page paper or the grey
workspace round it - pans the layout preview; a drag that starts on an item
still selects, moves or resizes it. A plain click on empty space clears the
selection.

Real widgets and real mouse events; every assertion is about what ends up
where on screen (where the page is drawn, where an item sits, what the Edit
tab shows) or on the layout's undo stack.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_cartograph_panel_widgets import _drag, _quiet, _wheel_zoom
from test_cartograph_panel_widgets import page as page

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import QgsRectangle  # noqa: E402
from qgis.PyQt.QtCore import QPoint, QPointF, Qt  # noqa: E402
from qgis.PyQt.QtTest import QTest  # noqa: E402


def _zoomed(qgis_app, pane, centre_mm=(190.0, 130.0)) -> None:
    """Zoomed in, with room to pan every way, centred on bare paper."""
    _wheel_zoom(pane, 120)
    _wheel_zoom(pane, 120)
    _quiet(qgis_app, pane)
    pane.preview.view.centerOn(QPointF(*centre_mm))
    _quiet(qgis_app, pane)


def _page_origin(pane) -> QPoint:
    """Where the page's top-left corner is drawn, in viewport pixels."""
    return pane.preview.view.mapFromScene(QPointF(0, 0))


def _centre(pane) -> QPoint:
    viewport = pane.preview.view.viewport()
    return QPoint(viewport.width() // 2, viewport.height() // 2)


def _mm(pane, x: float, y: float) -> QPoint:
    """A drag distance of x by y millimetres of paper, in pixels."""
    scale = pane.preview.view.transform().m11()
    return QPoint(round(x * scale), round(y * scale))


def _count(layout) -> int:
    return layout.undoStack().stack().count()


def _grey_point(pane) -> QPoint:
    """A viewport point on the workspace, outside the paper."""
    view = pane.preview.view
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().minimum())
    view.verticalScrollBar().setValue(view.verticalScrollBar().minimum())
    corner = _page_origin(pane)
    assert corner.x() > 6 and corner.y() > 6, "no workspace margin to press on"
    return QPoint(corner.x() // 2, corner.y() // 2)


class TestDragFromEmptySpacePans:
    def test_a_drag_from_bare_paper_moves_the_view_by_the_drag(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        _zoomed(qgis_app, pane)
        before_origin = _page_origin(pane)
        positions = {k: v.pos() for k, v in items.items()}
        count = _count(layout)
        start = _centre(pane)
        _drag(qgis_app, pane, start, start + QPoint(-60, -40))
        moved = _page_origin(pane) - before_origin
        assert abs(moved.x() + 60) <= 2 and abs(moved.y() + 40) <= 2, moved
        assert {k: v.pos() for k, v in items.items()} == positions
        assert _count(layout) == count

    def test_a_drag_from_the_grey_workspace_pans_too(self, qgis_app, page) -> None:
        pane, layout, items = page
        _zoomed(qgis_app, pane)
        start = _grey_point(pane)
        before_origin = _page_origin(pane)
        positions = {k: v.pos() for k, v in items.items()}
        count = _count(layout)
        # At the scroll limit the view can only move on, away from it.
        _drag(qgis_app, pane, start, start + QPoint(-20, -15))
        moved = _page_origin(pane) - before_origin
        assert abs(moved.x() + 20) <= 2 and abs(moved.y() + 15) <= 2, moved
        assert {k: v.pos() for k, v in items.items()} == positions
        assert _count(layout) == count

    def test_a_drag_leaves_the_fit_until_fit_page(self, qgis_app, page) -> None:
        pane, _layout, _items = page
        fitted = pane.preview.view.transform().m11()
        start = pane.preview.view.mapFromScene(QPointF(190.0, 130.0))  # bare paper
        _drag(qgis_app, pane, start, start + QPoint(-30, -20))
        pane.resize(1100, 850)
        _quiet(qgis_app, pane)
        # Panned by the user: a bigger window does not refit the page.
        assert pane.preview.view.transform().m11() == pytest.approx(fitted)
        pane.fit_button.click()
        _quiet(qgis_app, pane)
        assert pane.preview.view.transform().m11() > fitted * 1.1

    def test_the_pointer_is_a_closed_hand_while_panning(self, qgis_app, page) -> None:
        pane, _layout, _items = page
        _zoomed(qgis_app, pane)
        viewport = pane.preview.view.viewport()
        start = _centre(pane)
        QTest.mouseMove(viewport, start)
        QTest.mousePress(
            viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
        )
        from test_cartograph_panel_widgets import _move_held

        _move_held(viewport, start + QPoint(-20, -10))
        qgis_app.processEvents()
        assert viewport.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start + QPoint(-20, -10),
        )
        assert viewport.cursor().shape() != Qt.CursorShape.ClosedHandCursor


class TestClickOnEmptySpace:
    def test_a_click_clears_the_selection_and_the_edit_tab_shows_its_empty_state(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        items["title"].setSelected(True)
        _quiet(qgis_app, pane)
        assert not pane.controls.body.isHidden()
        _zoomed(qgis_app, pane)
        origin = _page_origin(pane)
        count = _count(layout)
        centre = _centre(pane)
        _drag(qgis_app, pane, centre, centre)
        assert layout.selectedLayoutItems() == []
        assert pane.controls.body.isHidden()
        assert "Select an item on the page" in pane.controls.heading.text()
        assert _page_origin(pane) == origin
        assert _count(layout) == count

    def test_a_drag_keeps_the_selection(self, qgis_app, page) -> None:
        pane, layout, items = page
        items["title"].setSelected(True)
        _quiet(qgis_app, pane)
        _zoomed(qgis_app, pane)
        centre = _centre(pane)
        _drag(qgis_app, pane, centre, centre + QPoint(-40, -30))
        assert layout.selectedLayoutItems() == [items["title"]]
        assert not pane.controls.body.isHidden()

    def test_a_click_with_a_little_jitter_is_still_a_click(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        items["map"].setSelected(True)
        _quiet(qgis_app, pane)
        _zoomed(qgis_app, pane)
        origin = _page_origin(pane)
        centre = _centre(pane)
        _drag(qgis_app, pane, centre, centre + QPoint(1, 1))
        assert layout.selectedLayoutItems() == []
        assert _page_origin(pane) == origin


class TestDragFromAnItemStillWorksAsBefore:
    @staticmethod
    def _on(pane, item) -> QPoint:
        return pane.preview.view.mapFromScene(item.sceneBoundingRect().center())

    def test_dragging_an_item_moves_it_as_one_move_items_step(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        _zoomed(qgis_app, pane, (105.0, 110.0))
        frame = items["map"]
        layout.deselectAll()
        before, count = frame.pos(), _count(layout)
        origin = _page_origin(pane)
        start = self._on(pane, frame)
        _drag(qgis_app, pane, start, start + _mm(pane, 10, 6))
        assert frame.pos().x() > before.x() + 3  # millimetres
        assert _count(layout) == count + 1
        assert layout.undoStack().stack().text(count) == "Move Items"
        assert _page_origin(pane) == origin  # the view did not pan

    def test_a_corner_handle_still_resizes(self, qgis_app, page) -> None:
        pane, layout, items = page
        _zoomed(qgis_app, pane, (105.0, 110.0))
        frame = items["map"]
        start = self._on(pane, frame)
        _drag(qgis_app, pane, start, start)
        assert frame.isSelected()
        width, count = frame.rect().width(), _count(layout)
        origin = _page_origin(pane)
        rect = frame.sceneBoundingRect()
        view = pane.preview.view
        corner = view.mapFromScene(QPointF(rect.right(), rect.bottom())) - QPoint(3, 3)
        _drag(qgis_app, pane, corner, corner + _mm(pane, 15, 10))
        assert frame.rect().width() > width + 5
        assert _count(layout) == count + 1
        assert _page_origin(pane) == origin

    def test_a_press_just_outside_a_selected_corner_still_resizes(
        self, qgis_app, page
    ) -> None:
        pane, _layout, items = page
        _zoomed(qgis_app, pane, (105.0, 110.0))
        frame = items["map"]
        start = self._on(pane, frame)
        _drag(qgis_app, pane, start, start)
        width = frame.rect().width()
        rect = frame.sceneBoundingRect()
        corner = pane.preview.view.mapFromScene(QPointF(rect.right(), rect.bottom()))
        outside = corner + QPoint(2, 2)
        origin = _page_origin(pane)
        _drag(qgis_app, pane, outside, outside + _mm(pane, 15, 10))
        assert frame.rect().width() > width + 5
        assert _page_origin(pane) == origin


class TestOtherModesAreUnchanged:
    def test_placing_an_item_still_places_it_on_bare_paper(
        self, qgis_app, page
    ) -> None:
        from qgis.core import QgsLayoutItemLabel

        pane, layout, _items = page
        before = len([i for i in layout.items() if isinstance(i, QgsLayoutItemLabel)])
        origin = _page_origin(pane)
        pane._fill_add_menu()
        next(a for a in pane.add_menu.actions() if a.text() == "Text").trigger()
        assert pane.preview.placer.active
        at = pane.preview.view.mapFromScene(QPointF(215.0, 150.0))
        _drag(qgis_app, pane, at, at)
        after = len([i for i in layout.items() if isinstance(i, QgsLayoutItemLabel)])
        assert after == before + 1
        assert _page_origin(pane) == origin

    def test_edit_view_still_pans_the_map_content_not_the_view(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        frame = items["map"]
        frame.setExtent(QgsRectangle(0, 0, 1500, 1000))
        layout.deselectAll()
        frame.setSelected(True)
        _quiet(qgis_app, pane)
        pane.controls.edit_view_button.click()
        _quiet(qgis_app, pane)
        assert pane.preview.content_editor.active
        origin = _page_origin(pane)
        extent = frame.extent().center()
        geometry = frame.sceneBoundingRect()
        start = pane.preview.view.mapFromScene(frame.sceneBoundingRect().center())
        _drag(qgis_app, pane, start, start + QPoint(20, 10))
        assert frame.sceneBoundingRect() == geometry
        assert frame.extent().center() != extent
        assert _page_origin(pane) == origin

    def test_a_press_on_bare_paper_in_edit_view_ends_the_mode_first(
        self, qgis_app, page
    ) -> None:
        """Edit view hands a press beside the frame back to Select (as it
        always did); that press is then an ordinary one, so it pans."""
        pane, layout, items = page
        frame = items["map"]
        frame.setExtent(QgsRectangle(0, 0, 1500, 1000))
        layout.deselectAll()
        frame.setSelected(True)
        _quiet(qgis_app, pane)
        pane.controls.edit_view_button.click()
        _quiet(qgis_app, pane)
        assert pane.preview.content_editor.active
        extent = frame.extent().center()
        outside = pane.preview.view.mapFromScene(QPointF(270.0, 190.0))
        _drag(qgis_app, pane, outside, outside + QPoint(-20, -10))
        assert not pane.preview.content_editor.active
        assert frame.extent().center() == extent
