"""The Cartograph screen's own widgets, one at a time: the page pane (caption
and zoom bar over the live page), the two-line save location, and a panel
section laid out right to left.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.
The dialog-level wiring of the same widgets is in `test_cartograph_dialog.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")


def _settle(qgis_app, seconds: float = 0.2) -> None:
    """Let the page's zero-delay fit timers and any resize run."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.01)


def _a4_layout(project):
    from qgis.core import QgsPrintLayout

    layout = QgsPrintLayout(project)
    # QGIS's default page: one A4 sheet, landscape.
    layout.initializeDefaults()
    layout.setName("Survey")
    project.layoutManager().addLayout(layout)
    return layout


@pytest.fixture
def pane(qgis_app, project):
    """A shown page pane on an A4 landscape layout, fitted."""
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    widget = PagePane()
    widget.resize(600, 500)
    widget.show()
    widget.show_layout(_a4_layout(project))
    _settle(qgis_app)
    yield widget
    widget.close()
    widget.deleteLater()


def _scale(pane) -> float:
    return pane.preview.view.transform().m11()


def _wheel_zoom(pane, delta: int) -> None:
    """A turn of the mouse wheel over the middle of the page."""
    from qgis.PyQt.QtCore import QPoint, QPointF, Qt
    from qgis.PyQt.QtGui import QWheelEvent
    from qgis.PyQt.QtWidgets import QApplication

    viewport = pane.preview.view.viewport()
    centre = QPointF(viewport.width() / 2, viewport.height() / 2)
    QApplication.sendEvent(
        viewport,
        QWheelEvent(
            centre,
            QPointF(viewport.mapToGlobal(centre.toPoint())),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )


class TestPagePane:
    def test_the_caption_names_the_layout_and_its_page(self, pane) -> None:
        assert pane.caption.text() == "Layout preview — QGIS · A4 landscape"
        assert not pane.caption.isHidden()
        assert not pane.add_button.isHidden()
        assert not pane.fit_button.isHidden()

    def test_without_a_layout_the_caption_and_floating_controls_hide(
        self, qgis_app, pane
    ) -> None:
        pane.show_layout(None)
        _settle(qgis_app)
        assert not pane.preview.showing_page()
        assert pane.caption.isHidden()
        assert not pane.add_button.isVisible()
        assert not pane.fit_button.isVisible()

    def test_the_page_is_not_the_first_tab_stop(self, pane) -> None:
        """A click still focuses the page; Tab goes to the zoom bar."""
        from qgis.PyQt.QtCore import Qt

        assert pane.preview.view.focusPolicy() == Qt.FocusPolicy.ClickFocus

    def test_the_wheel_zooms_the_page_in_and_out(self, qgis_app, pane) -> None:
        fitted = _scale(pane)
        _wheel_zoom(pane, 120)
        _settle(qgis_app)
        zoomed_in = _scale(pane)
        assert zoomed_in > fitted
        _wheel_zoom(pane, -120)
        _wheel_zoom(pane, -120)
        _settle(qgis_app)
        assert _scale(pane) < zoomed_in
        assert _scale(pane) < fitted

    def test_fit_page_refits_after_the_user_zoomed(self, qgis_app, pane) -> None:
        fitted = _scale(pane)
        _wheel_zoom(pane, 120)
        _settle(qgis_app)
        assert _scale(pane) > fitted * 1.05
        pane.fit_button.click()
        _settle(qgis_app)
        # The same fit as before the zoom: refitted once the zoomed page's
        # scroll bars have gone, which took 2.4% off the first fit.
        assert _scale(pane) == pytest.approx(fitted, rel=0.002)

    def test_a_resize_alone_keeps_the_page_fitting(self, qgis_app, pane) -> None:
        """The view reports a zoom change when it is only resized; that is
        not the user zooming, so the page fits the new size."""
        small = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert _scale(pane) > small * 1.2

    def test_the_wheel_is_a_user_zoom_that_stays(self, qgis_app, pane) -> None:
        from qgis.PyQt.QtCore import QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QWheelEvent
        from qgis.PyQt.QtWidgets import QApplication

        viewport = pane.preview.view.viewport()
        centre = QPointF(viewport.width() / 2, viewport.height() / 2)
        wheel = QWheelEvent(
            centre,
            QPointF(viewport.mapToGlobal(centre.toPoint())),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(viewport, wheel)
        # Their zoom stays through a resize.
        kept = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert _scale(pane) == pytest.approx(kept, rel=0.01)


def _framed_pane(qgis_app, project):
    """A shown pane on an A4 layout holding one 100 x 80 mm map at 30, 40."""
    from qgis.core import QgsLayoutItemMap
    from qgis.PyQt.QtCore import QRectF

    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    layout = _a4_layout(project)
    frame = QgsLayoutItemMap(layout)
    frame.attemptSetSceneRect(QRectF(30, 40, 100, 80))
    layout.addLayoutItem(frame)
    widget = PagePane()
    widget.resize(700, 600)
    widget.show()
    widget.show_layout(layout)
    _settle(qgis_app)
    return widget, layout, frame


def _viewport_point(pane, x_mm: float, y_mm: float):
    from qgis.PyQt.QtCore import QPointF

    view = pane.preview.view
    return view.mapFromScene(QPointF(x_mm, y_mm))


def _move_held(viewport, point) -> None:
    from qgis.PyQt.QtCore import QEvent, QPointF, Qt
    from qgis.PyQt.QtGui import QMouseEvent
    from qgis.PyQt.QtWidgets import QApplication

    position = QPointF(point)
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseMove,
            position,
            QPointF(viewport.mapToGlobal(point)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def _drag(qgis_app, pane, start, end, buttons=None) -> None:
    """A human drag on the page: press, a path of moves, release."""
    from qgis.PyQt.QtCore import QPoint, Qt
    from qgis.PyQt.QtTest import QTest

    button = Qt.MouseButton.LeftButton
    viewport = pane.preview.view.viewport()
    QTest.mouseMove(viewport, start)
    qgis_app.processEvents()
    QTest.mousePress(viewport, button, Qt.KeyboardModifier.NoModifier, start)
    steps = 8
    for step in range(1, steps + 1):
        point = QPoint(
            start.x() + (end.x() - start.x()) * step // steps,
            start.y() + (end.y() - start.y()) * step // steps,
        )
        # Sent with the button held: Qt 5's QTest.mouseMove has none.
        _move_held(viewport, point)
        qgis_app.processEvents()
    QTest.mouseRelease(viewport, button, Qt.KeyboardModifier.NoModifier, end)
    _settle(qgis_app, 0.05)


class TestEditingThePage:
    @pytest.fixture
    def editing(self, qgis_app, project):
        widget, layout, frame = _framed_pane(qgis_app, project)
        yield widget, layout, frame
        widget.close()
        widget.deleteLater()

    @staticmethod
    def _centre(pane, frame):
        rect = frame.sceneBoundingRect()
        return _viewport_point(pane, rect.center().x(), rect.center().y())

    def test_the_page_is_always_in_select_mode(self, editing) -> None:
        pane, _layout, _frame = editing
        assert pane.preview.select_mode()
        assert not hasattr(pane, "select_button")
        assert not hasattr(pane, "pan_button")

    def test_a_drag_moves_the_item_as_one_undo_step(self, qgis_app, editing) -> None:
        pane, layout, frame = editing
        stack = layout.undoStack().stack()
        before, count = frame.pos(), stack.count()
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre + type(centre)(60, 40))
        assert frame.isSelected()
        assert frame.pos().x() > before.x() + 10
        assert frame.pos().y() > before.y() + 5
        assert stack.count() == count + 1
        stack.undo()
        assert frame.pos().x() == pytest.approx(before.x())
        assert frame.pos().y() == pytest.approx(before.y())
        stack.redo()
        assert frame.pos().x() > before.x() + 10

    def test_ctrl_z_in_the_page_undoes_the_move(self, qgis_app, editing) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, _layout, frame = editing
        before = frame.pos()
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre + type(centre)(60, 40))
        view = pane.preview.view
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert frame.pos().x() == pytest.approx(before.x())
        QTest.keyClick(
            view,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        assert frame.pos().x() > before.x() + 10

    def test_a_corner_handle_resizes_the_item(self, qgis_app, editing) -> None:
        pane, layout, frame = editing
        stack = layout.undoStack().stack()
        # Select it first, so its handles are there to take hold of.
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre)
        assert frame.isSelected()
        count, width = stack.count(), frame.rect().width()
        rect = frame.sceneBoundingRect()
        corner = _viewport_point(pane, rect.right(), rect.bottom())
        # The handle sits just inside the corner.
        corner -= type(corner)(3, 3)
        _drag(qgis_app, pane, corner, corner + type(corner)(40, 30))
        assert frame.rect().width() > width + 5
        assert stack.count() == count + 1
        stack.undo()
        assert frame.rect().width() == pytest.approx(width)

    def test_a_locked_item_does_not_move(self, qgis_app, editing) -> None:
        pane, layout, frame = editing
        frame.setLocked(True)
        before, count = frame.pos(), layout.undoStack().stack().count()
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre + type(centre)(60, 40))
        assert frame.pos() == before
        assert layout.undoStack().stack().count() == count

    def test_selecting_does_not_stop_the_page_fitting(self, qgis_app, editing) -> None:
        """Selecting is not a zoom: the page still fits a window made bigger
        afterwards (it used to stay at its old, tiny size)."""
        pane, _layout, frame = editing
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre)
        small = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert _scale(pane) > small * 1.1


def _handle_rect(pane):
    """The selection's handle box, in view pixels, found in the scene (the
    mouse handles are not exposed to Python)."""
    from qgis.PyQt.QtWidgets import QGraphicsRectItem

    view = pane.preview.view
    boxes = [
        item
        for item in view.scene().items()
        if type(item) is QGraphicsRectItem
        and item.zValue() == 10000
        and item.isVisible()
    ]
    assert len(boxes) == 1
    return view.mapFromScene(boxes[0].sceneBoundingRect()).boundingRect()


def _hover(qgis_app, pane, point) -> None:
    """The pointer arrives at `point` the way a hand brings it: several moves."""
    from qgis.PyQt.QtCore import QPoint
    from qgis.PyQt.QtTest import QTest

    viewport = pane.preview.view.viewport()
    start = QPoint(point.x() + 30, point.y() + 20)
    for step in range(1, 6):
        QTest.mouseMove(
            viewport,
            QPoint(
                start.x() + (point.x() - start.x()) * step // 5,
                start.y() + (point.y() - start.y()) * step // 5,
            ),
        )
        qgis_app.processEvents()


@pytest.fixture
def page(qgis_app, project):
    from qgis.core import (
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
    )
    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    layout = _a4_layout(project)
    items = {}
    frame = QgsLayoutItemMap(layout)
    frame.attemptSetSceneRect(QRectF(30, 60, 150, 100))
    layout.addLayoutItem(frame)
    items["map"] = frame
    title = QgsLayoutItemLabel(layout)
    title.setText("A title")
    title.setBackgroundEnabled(True)
    title.setBackgroundColor(QColor("#ccddee"))
    title.attemptSetSceneRect(QRectF(30, 15, 120, 25))
    layout.addLayoutItem(title)
    items["title"] = title
    legend = QgsLayoutItemLegend(layout)
    legend.attemptSetSceneRect(QRectF(200, 60, 60, 40))
    layout.addLayoutItem(legend)
    legend.setResizeToContents(False)
    items["legend"] = legend
    # The gestures below measure a drag's reach, not snapping: whether a
    # neighbour's edge catches it depends on the pixels the page is shown at.
    layout.snapper().setSnapToItems(False)
    widget = PagePane()
    widget.resize(900, 650)
    widget.show()
    widget.show_layout(layout)
    _settle(qgis_app)
    yield widget, layout, items
    widget.close()
    widget.deleteLater()


class TestResizingInThePage:
    @staticmethod
    def _select(qgis_app, layout, item) -> None:
        layout.deselectAll()
        item.setSelected(True)
        _settle(qgis_app, 0.1)

    @pytest.mark.parametrize("size", [(900, 650), (480, 420)])
    @pytest.mark.parametrize("name", ["map", "title", "legend"])
    def test_the_handles_sit_on_the_item(self, qgis_app, page, name, size) -> None:
        pane, layout, items = page
        pane.resize(*size)
        _settle(qgis_app)
        item = items[name]
        self._select(qgis_app, layout, item)
        view = pane.preview.view
        box = view.mapFromScene(item.sceneBoundingRect()).boundingRect()
        handles = _handle_rect(pane)
        for got, want in (
            (handles.left(), box.left()),
            (handles.top(), box.top()),
            (handles.right(), box.right()),
            (handles.bottom(), box.bottom()),
        ):
            assert abs(got - want) <= 2

    @pytest.mark.parametrize("reach", [-3, 0, 2])
    @pytest.mark.parametrize("name", ["map", "title", "legend"])
    def test_a_corner_drag_resizes_and_keeps_the_far_corner(
        self, qgis_app, page, name, reach
    ) -> None:
        """Taken just inside, on, or just outside the corner: all resize."""
        from qgis.PyQt.QtCore import QPoint, Qt

        pane, layout, items = page
        item = items[name]
        self._select(qgis_app, layout, item)
        stack = layout.undoStack().stack()
        before, steps = item.sceneBoundingRect(), stack.count()
        scale = _scale(pane)
        box = pane.preview.view.mapFromScene(before).boundingRect()
        corner = box.bottomRight() + QPoint(reach, reach)
        _hover(qgis_app, pane, corner)
        cursor = pane.preview.view.viewport().cursor().shape()
        assert cursor == Qt.CursorShape.SizeFDiagCursor
        _drag(qgis_app, pane, corner, corner + QPoint(50, 30))
        after = item.sceneBoundingRect()
        assert after.left() == pytest.approx(before.left(), abs=0.05)
        assert after.top() == pytest.approx(before.top(), abs=0.05)
        # The drag's first step only starts it: most of 50 x 30 px lands.
        assert after.width() - before.width() == pytest.approx(50 / scale, rel=0.2)
        assert after.height() - before.height() == pytest.approx(30 / scale, rel=0.2)
        assert stack.count() == steps + 1
        stack.undo()
        assert item.sceneBoundingRect().width() == pytest.approx(before.width())
        assert item.sceneBoundingRect().height() == pytest.approx(before.height())

    @pytest.mark.parametrize("name", ["map", "title", "legend"])
    def test_a_body_drag_moves_without_resizing(self, qgis_app, page, name) -> None:
        from qgis.PyQt.QtCore import QPoint

        pane, layout, items = page
        item = items[name]
        self._select(qgis_app, layout, item)
        stack = layout.undoStack().stack()
        before, steps = item.sceneBoundingRect(), stack.count()
        scale = _scale(pane)
        centre = pane.preview.view.mapFromScene(before.center())
        _hover(qgis_app, pane, centre)
        _drag(qgis_app, pane, centre, centre + QPoint(60, 40))
        after = item.sceneBoundingRect()
        assert after.width() == pytest.approx(before.width(), abs=0.05)
        assert after.height() == pytest.approx(before.height(), abs=0.05)
        assert after.x() - before.x() == pytest.approx(60 / scale, rel=0.2)
        assert after.y() - before.y() == pytest.approx(40 / scale, rel=0.2)
        assert stack.count() == steps + 1
        stack.undo()
        assert item.sceneBoundingRect().topLeft() == before.topLeft()

    @pytest.mark.parametrize("name", ["map", "title", "legend"])
    def test_a_resize_leaves_the_others_and_the_lettering_alone(
        self, qgis_app, page, name
    ) -> None:
        """Resizing a box is not changing its lettering, and moves nothing else."""
        from qgis.PyQt.QtCore import QPoint

        pane, layout, items = page
        item = items[name]
        self._select(qgis_app, layout, item)
        others = {k: v.sceneBoundingRect() for k, v in items.items() if k != name}
        size = pane.controls.text_size_label.text()
        lettering = item.textFormat().size() if hasattr(item, "textFormat") else None
        box = pane.preview.view.mapFromScene(item.sceneBoundingRect()).boundingRect()
        corner = box.bottomRight()
        _hover(qgis_app, pane, corner)
        _drag(qgis_app, pane, corner, corner - QPoint(30, 20))
        assert item.sceneBoundingRect().width() < box.width()
        assert {
            k: v.sceneBoundingRect() for k, v in items.items() if k != name
        } == others
        if lettering is not None:
            assert item.textFormat().size() == pytest.approx(lettering)
        assert pane.controls.text_size_label.text() == size

    def test_a_press_far_outside_the_box_does_not_resize(self, qgis_app, page) -> None:
        from qgis.PyQt.QtCore import QPoint

        pane, layout, items = page
        item = items["map"]
        self._select(qgis_app, layout, item)
        before = item.sceneBoundingRect()
        box = pane.preview.view.mapFromScene(before).boundingRect()
        away = box.bottomRight() + QPoint(15, 15)
        _drag(qgis_app, pane, away, away + QPoint(50, 30))
        assert item.sceneBoundingRect() == before


@pytest.fixture
def save_location(qgis_app):
    from nika_onlymap_exporter.ui.cartograph.layout_page import SaveLocation

    widget = SaveLocation()
    widget.setPlaceholderText("Choose where the cartograph is saved")
    widget.resize(700, 60)
    widget.show()
    qgis_app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


class TestSaveLocation:
    def test_the_name_with_the_full_path_as_its_tooltip(
        self, qgis_app, save_location
    ) -> None:
        folder = Path.home() / "Documents" / "QGIS2WebMap"
        path = folder / "parcels-Survey.html"
        save_location.setText(str(path))
        qgis_app.processEvents()
        assert save_location.label.text() == "parcels-Survey.html"
        assert save_location.label.toolTip() == str(path)
        assert save_location.label.accessibleDescription() == str(path)
        assert save_location.label.accessibleName()
        assert not save_location.reveal_button.isHidden()
        assert save_location.reveal_button.text() == "Show in folder"

    def test_with_no_path_only_the_placeholder_shows(
        self, qgis_app, save_location
    ) -> None:
        save_location.setText(str(Path.home() / "a.html"))
        save_location.setText("")
        qgis_app.processEvents()
        assert save_location.label.text() == "Choose where the cartograph is saved"
        assert save_location.reveal_button.isHidden()
        assert not save_location.change_button.isHidden()

    def test_show_in_folder_asks_for_it(self, qgis_app, save_location) -> None:
        save_location.setText(str(Path.home() / "a.html"))
        asked: list[bool] = []
        save_location.reveal_requested.connect(lambda: asked.append(True))
        save_location.reveal_button.click()
        assert asked == [True]


class TestPanelSectionDirection:
    def _section(self, direction):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QWidget

        from nika_onlymap_exporter.ui.cartograph.panel_section import PanelSection

        parent = QWidget()
        parent.setLayoutDirection(direction)
        # Qt5's enums have no `.name`; the key only has to differ per direction.
        key = "rtl" if direction == Qt.LayoutDirection.RightToLeft else "ltr"
        section = PanelSection(f"direction-{key}", "Map frames", parent)
        return parent, section

    def test_right_to_left_the_header_keeps_its_width_and_points_left(
        self, qgis_app
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QSizePolicy

        parent, section = self._section(Qt.LayoutDirection.RightToLeft)
        assert section.isRightToLeft()
        policy = section.header.sizePolicy().horizontalPolicy()
        assert policy == QSizePolicy.Policy.Preferred
        row = section.layout().itemAt(0).layout()
        assert row.stretch(row.indexOf(section.header)) == 0
        # A stretch between the header and the summary at the far end.
        assert row.itemAt(1).spacerItem() is not None
        assert row.indexOf(section.summary) == 2
        assert not section.is_open()
        assert section.header.arrowType() == Qt.ArrowType.LeftArrow
        section.set_open(True)
        assert section.header.arrowType() == Qt.ArrowType.DownArrow
        section.set_open(False)
        assert section.header.arrowType() == Qt.ArrowType.LeftArrow
        parent.deleteLater()

    def test_left_to_right_the_header_stretches_and_points_right(
        self, qgis_app
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QSizePolicy

        parent, section = self._section(Qt.LayoutDirection.LeftToRight)
        policy = section.header.sizePolicy().horizontalPolicy()
        assert policy == QSizePolicy.Policy.Expanding
        row = section.layout().itemAt(0).layout()
        assert row.stretch(row.indexOf(section.header)) == 1
        assert row.indexOf(section.summary) == 1
        assert section.header.arrowType() == Qt.ArrowType.RightArrow
        parent.deleteLater()


class TestSelectedTextSize:
    @staticmethod
    def _select(qgis_app, layout, item) -> None:
        layout.deselectAll()
        item.setSelected(True)
        _settle(qgis_app, 0.1)

    def test_the_control_shows_only_for_text_items(self, qgis_app, page) -> None:
        pane, layout, items = page
        title = items["title"]
        assert pane.controls.text_size_row.isHidden()
        self._select(qgis_app, layout, items["title"])
        assert not pane.controls.text_size_row.isHidden()
        base = title.textFormat().size()
        assert pane.controls.text_size_label.text() == f"{base:g} pt"
        # A legend's size is the legend editor's own Text size.
        self._select(qgis_app, layout, items["legend"])
        assert pane.controls.text_size_row.isHidden()
        self._select(qgis_app, layout, items["map"])
        assert pane.controls.text_size_row.isHidden()
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        assert pane.controls.text_size_row.isHidden()

    def test_a_step_is_one_undo_and_keeps_box_zoom_and_scroll(
        self, qgis_app, page
    ) -> None:
        pane, layout, items = page
        title = items["title"]
        self._select(qgis_app, layout, title)
        base = title.textFormat().size()
        stack = layout.undoStack().stack()
        view = pane.preview.view
        box, scale, steps = title.sceneBoundingRect(), _scale(pane), stack.count()
        scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        pane.controls.text_bigger_button.click()
        _settle(qgis_app)
        assert title.textFormat().size() == pytest.approx(base + 1)
        assert pane.controls.text_size_label.text() == f"{base + 1:g} pt"
        assert stack.count() == steps + 1
        assert title.sceneBoundingRect() == box
        assert _scale(pane) == pytest.approx(scale)
        assert (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        ) == scroll
        pane.controls.text_smaller_button.click()
        pane.controls.text_smaller_button.click()
        assert title.textFormat().size() == pytest.approx(base - 1)
        stack.undo()
        stack.undo()
        stack.undo()
        assert title.textFormat().size() == pytest.approx(base)
        assert pane.controls.text_size_label.text() == f"{base:g} pt"
        assert stack.count() == steps + 3

    def test_the_legend_scales_all_its_levels_and_is_marked_chosen(
        self, qgis_app, page
    ) -> None:
        from qgis.core import QgsLegendStyle

        from nika_onlymap_exporter.core.layout_reader import chosen_style_property

        pane, layout, items = page
        legend = items["legend"]
        self._select(qgis_app, layout, legend)
        title_before = legend.style(QgsLegendStyle.Title).textFormat().size()
        rows_before = legend.style(QgsLegendStyle.SymbolLabel).textFormat().size()
        pane.controls.text_bigger_button.click()
        rows = legend.style(QgsLegendStyle.SymbolLabel).textFormat().size()
        assert rows == pytest.approx(rows_before + 1)
        assert legend.style(QgsLegendStyle.Title).textFormat().size() == pytest.approx(
            title_before * rows / rows_before
        )
        assert legend.customProperty(chosen_style_property("font-size")) == rows
        layout.undoStack().stack().undo()
        assert legend.style(QgsLegendStyle.SymbolLabel).textFormat().size() == (
            pytest.approx(rows_before)
        )

    def test_a_narrow_pane_keeps_the_floating_controls_in_view_and_working(
        self, qgis_app, page
    ) -> None:
        from nika_onlymap_exporter.ui import theme

        pane, layout, items = page
        title = items["title"]
        # Themed, as in the dialog.
        pane.setObjectName("PagePaneUnderTest")
        theme.apply(pane)
        self._select(qgis_app, layout, title)
        pane.resize(300, 500)
        _settle(qgis_app, 0.1)
        viewport = pane.preview.view.viewport()
        for button in (pane.fit_button, pane.add_button):
            assert button.toolTip().startswith(button.accessibleName())
        for rect in _control_rects(pane).values():
            assert viewport.rect().contains(rect)
        base = title.textFormat().size()
        pane.controls.text_bigger_button.click()
        assert title.textFormat().size() == pytest.approx(base + 1)

    def test_the_size_has_limits(self, qgis_app, page) -> None:
        pane, layout, items = page
        title = items["title"]
        self._select(qgis_app, layout, title)
        text_format = title.textFormat()
        text_format.setSize(4)
        title.setTextFormat(text_format)
        steps = layout.undoStack().stack().count()
        pane.preview.step_text_size(False)
        assert title.textFormat().size() == pytest.approx(4)
        assert layout.undoStack().stack().count() == steps


def _held_moves(qgis_app, pane, start, end, button) -> None:
    """Press `button` at `start`, move along a path to `end`, release - the
    way `_drag` does, for a button other than the left one."""
    from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
    from qgis.PyQt.QtGui import QMouseEvent
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QApplication

    viewport = pane.preview.view.viewport()
    QTest.mouseMove(viewport, start)
    qgis_app.processEvents()
    QTest.mousePress(viewport, button, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 9):
        point = QPoint(
            start.x() + (end.x() - start.x()) * step // 8,
            start.y() + (end.y() - start.y()) * step // 8,
        )
        QApplication.sendEvent(
            viewport,
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(point),
                QPointF(viewport.mapToGlobal(point)),
                Qt.MouseButton.NoButton,
                button,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        qgis_app.processEvents()
    QTest.mouseRelease(viewport, button, Qt.KeyboardModifier.NoModifier, end)
    _settle(qgis_app, 0.05)


class TestThePageIsAlwaysInSelectMode:
    """With no Select / Pan buttons the page is moved around by the middle
    button or Space + drag (QgsLayoutView's temporary pan tools), the wheel
    and the scroll bars - and none of those touch an item."""

    @staticmethod
    def _zoomed(qgis_app, pane) -> None:
        _wheel_zoom(pane, 120)
        _wheel_zoom(pane, 120)
        _settle(qgis_app)

    @staticmethod
    def _scroll(pane) -> tuple[int, int]:
        view = pane.preview.view
        return view.horizontalScrollBar().value(), view.verticalScrollBar().value()

    def test_there_are_no_mode_buttons(self, page) -> None:
        pane, _layout, _items = page
        assert not hasattr(pane, "select_button")
        assert not hasattr(pane, "pan_button")
        assert pane.preview.select_mode()
        # The way to pan is said where the zoom is.
        for button in (pane.fit_button,):
            assert "Space" in button.toolTip()
            assert "middle mouse button" in button.toolTip()

    def test_a_middle_button_drag_pans_the_page_and_moves_nothing(
        self, qgis_app, page
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt

        pane, layout, items = page
        self._zoomed(qgis_app, pane)
        frame = items["map"]
        frame.setSelected(True)
        _settle(qgis_app, 0.1)
        before, count = frame.pos(), layout.undoStack().stack().count()
        scroll = self._scroll(pane)
        box = pane.preview.view.mapFromScene(frame.sceneBoundingRect()).boundingRect()
        start = box.center()
        _held_moves(
            qgis_app, pane, start, start + QPoint(-60, -40), Qt.MouseButton.MiddleButton
        )
        assert self._scroll(pane) != scroll
        assert frame.pos() == before
        assert layout.undoStack().stack().count() == count
        assert frame.isSelected()
        assert pane.preview.select_mode()

    def test_space_and_a_drag_pans_the_page_and_moves_nothing(
        self, qgis_app, page
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, items = page
        self._zoomed(qgis_app, pane)
        frame = items["map"]
        frame.setSelected(True)
        _settle(qgis_app, 0.1)
        before, count = frame.pos(), layout.undoStack().stack().count()
        scroll = self._scroll(pane)
        view = pane.preview.view
        view.setFocus()
        box = view.mapFromScene(frame.sceneBoundingRect()).boundingRect()
        start = box.center()
        QTest.keyPress(view, Qt.Key.Key_Space)
        _held_moves(
            qgis_app, pane, start, start + QPoint(-60, -40), Qt.MouseButton.LeftButton
        )
        QTest.keyRelease(view, Qt.Key.Key_Space)
        _settle(qgis_app, 0.05)
        assert self._scroll(pane) != scroll
        assert frame.pos() == before
        assert layout.undoStack().stack().count() == count
        # Space released: a drag moves the item again.
        assert pane.preview.select_mode()
        centre = box.center() + QPoint(-60, -40)
        _drag(qgis_app, pane, centre, centre + QPoint(30, 20))
        assert layout.undoStack().stack().count() == count + 1

    def test_the_scroll_bars_and_wheel_pan_and_zoom(self, qgis_app, page) -> None:
        pane, _layout, _items = page
        self._zoomed(qgis_app, pane)
        bar = pane.preview.view.verticalScrollBar()
        bar.setValue(bar.minimum())
        before = self._scroll(pane)
        bar.setValue(bar.minimum() + 20)
        assert self._scroll(pane) != before


class TestSelectingDoesNotDisturbThePage:
    """The selected item's controls are in the Edit tab, so selecting never
    resizes or scrolls the page; and a press with a little jitter on an
    unselected item used to move it."""

    @pytest.mark.parametrize("name", ["map", "legend", "title"])
    def test_a_press_with_one_pixel_of_jitter_moves_nothing(
        self, qgis_app, page, name
    ) -> None:
        from qgis.PyQt.QtCore import QPoint

        pane, layout, items = page
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        item = items[name]
        before, count = item.sceneBoundingRect(), layout.undoStack().stack().count()
        viewport = pane.preview.view.viewport().size()
        centre = pane.preview.view.mapFromScene(item.sceneBoundingRect().center())
        _drag(qgis_app, pane, centre, centre + QPoint(1, 0))
        assert item.isSelected()
        assert item.sceneBoundingRect() == before
        assert layout.undoStack().stack().count() == count
        # Nothing on the page resized, whatever was selected.
        assert pane.preview.view.viewport().size() == viewport

    @pytest.mark.parametrize("name", ["map", "legend", "title"])
    def test_a_pure_click_adds_no_undo_step(self, qgis_app, page, name) -> None:
        pane, layout, items = page
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        item = items[name]
        stack = layout.undoStack().stack()
        count = stack.count()
        centre = pane.preview.view.mapFromScene(item.sceneBoundingRect().center())
        _drag(qgis_app, pane, centre, centre)
        assert item.isSelected()
        assert stack.count() == count, [
            stack.text(i) for i in range(count, stack.count())
        ]

    @pytest.mark.parametrize("name", ["map", "legend", "title"])
    @pytest.mark.parametrize("jitter", [0, 1])
    def test_a_press_nothing_hovered_into_adds_no_undo_step(
        self, qgis_app, page, name, jitter
    ) -> None:
        """A tap, a pen, or a click where the pointer already rests arrives
        with no hover before it. QTest.mouseMove sends none either when the
        cursor is already there, which made the other tests depend on where
        an earlier test left it."""
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest

        pane, layout, items = page
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        item = items[name]
        stack = layout.undoStack().stack()
        count, before = stack.count(), item.sceneBoundingRect()
        viewport = pane.preview.view.viewport()
        centre = pane.preview.view.mapFromScene(item.sceneBoundingRect().center())
        button, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        QTest.mousePress(viewport, button, none, centre)
        qgis_app.processEvents()
        if jitter:
            _move_held(viewport, centre + QPoint(jitter, 0))
            qgis_app.processEvents()
        QTest.mouseRelease(viewport, button, none, centre + QPoint(jitter, 0))
        _settle(qgis_app, 0.05)
        assert item.isSelected()
        assert item.sceneBoundingRect() == before
        assert stack.count() == count, [
            stack.text(i) for i in range(count, stack.count())
        ]


def _control_rects(pane):
    """Where Add item and Fit page are, in the page view's viewport."""
    view = pane.preview.view
    viewport = view.viewport()
    return {
        name: button.rect().translated(
            button.mapTo(view, button.rect().topLeft()) - viewport.pos()
        )
        for name, button in (("add", pane.add_button), ("fit", pane.fit_button))
    }


def _page_px(pane, index: int = 0):
    """Where page `index` is drawn, in the page view's viewport."""
    view = pane.preview.view
    page = view.currentLayout().pageCollection().page(index)
    # The paper: the page's bounding rectangle also holds its shadow.
    return view.mapFromScene(page.mapRectToScene(page.rect())).boundingRect()


def _from_corner(pane) -> dict[str, tuple[int, int]]:
    """Each control's distance from the view's bottom-right corner."""
    viewport = pane.preview.view.viewport()
    return {
        name: (
            viewport.width() - rect.right() - 1,
            viewport.height() - rect.bottom() - 1,
        )
        for name, rect in _control_rects(pane).items()
    }


class TestFloatingControls:
    @pytest.mark.parametrize("size", [(1000, 700), (480, 360)])
    def test_they_are_stacked_in_the_viewports_bottom_right_corner(
        self, qgis_app, pane, size
    ) -> None:
        pane.resize(*size)
        _settle(qgis_app)
        rects = _control_rects(pane)
        viewport = pane.preview.view.viewport()
        assert rects["add"].width() == rects["fit"].width()
        assert rects["add"].left() == rects["fit"].left()
        assert rects["add"].height() >= 32 and rects["fit"].height() >= 32
        assert rects["fit"].top() - rects["add"].bottom() - 1 == 8
        assert viewport.width() - rects["fit"].right() - 1 == 16
        assert viewport.height() - rects["fit"].bottom() - 1 == 16

    def test_the_container_is_no_larger_than_the_buttons(self, qgis_app, pane) -> None:
        overlay = pane.preview._overlay
        union = pane.add_button.geometry().united(pane.fit_button.geometry())
        assert overlay.size() == union.size()

    def test_a_click_beside_or_between_the_buttons_reaches_the_page(
        self, qgis_app, pane
    ) -> None:
        """No invisible surface: with a shape covering the page, a click at
        the gap between the buttons, and one just beside them, select it."""
        from qgis.core import (
            QgsLayoutItemShape,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsUnitTypes,
        )
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication

        view = pane.preview.view
        layout = view.currentLayout()
        shape = QgsLayoutItemShape(layout)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        mm = QgsUnitTypes.LayoutMillimeters
        shape.attemptMove(QgsLayoutPoint(-20, -20, mm))
        shape.attemptResize(QgsLayoutSize(400, 300, mm))
        layout.addLayoutItem(shape)
        _settle(qgis_app)
        rects = _control_rects(pane)
        gap = QPoint(
            rects["add"].center().x(), (rects["add"].bottom() + rects["fit"].top()) // 2
        )
        beside = QPoint(rects["add"].left() - 6, rects["add"].center().y())
        for spot in (gap, beside):
            layout.deselectAll()
            # As a mouse does: to whatever widget is under the pointer.
            at = view.viewport().mapToGlobal(spot)
            target = QApplication.widgetAt(at)
            assert target is not None
            QTest.mouseClick(
                target,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                target.mapFromGlobal(at),
            )
            _settle(qgis_app, 0.1)
            assert layout.selectedLayoutItems() == [shape], spot

    def test_they_follow_the_corner_and_stay_inside_when_it_leaves(
        self, qgis_app, pane
    ) -> None:
        view = pane.preview.view
        for _ in range(4):
            _wheel_zoom(pane, 120)
        _settle(qgis_app)
        viewport = view.viewport().rect()
        bars = (view.horizontalScrollBar(), view.verticalScrollBar())
        for to_end in (False, True):
            for bar in bars:
                bar.setValue(bar.maximum() if to_end else bar.minimum())
            _settle(qgis_app)
            rects = _control_rects(pane)
            union = rects["add"].united(rects["fit"])
            assert viewport.contains(union), (to_end, union, viewport)
            if not to_end:
                # The page's corner is well off the viewport.
                assert not viewport.contains(_page_px(pane).bottomRight())

    def test_they_stay_at_the_viewport_corner_wherever_the_page_is_scrolled(
        self, qgis_app, pane
    ) -> None:
        view = pane.preview.view
        _wheel_zoom(pane, 120)
        viewport = view.viewport()
        for to_end in (True, False):
            for bar in (view.horizontalScrollBar(), view.verticalScrollBar()):
                bar.setValue(bar.maximum() if to_end else bar.minimum())
            _settle(qgis_app)
            fit = _control_rects(pane)["fit"]
            assert viewport.width() - fit.right() - 1 == 16
            assert viewport.height() - fit.bottom() - 1 == 16

    def test_the_removed_zoom_row_gave_its_height_to_the_preview(self, pane) -> None:
        preview = pane.preview
        assert preview.geometry().bottom() + 1 == pane.height()

    @pytest.mark.parametrize("fill", ["#000000", "#ffffff"])
    def test_the_controls_are_opaque_over_a_dark_or_light_map(
        self, qgis_app, pane, fill
    ) -> None:
        """Whatever the map behind them, their label background is theirs."""
        from qgis.core import (
            QgsFillSymbol,
            QgsLayoutItemShape,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsUnitTypes,
        )

        from nika_onlymap_exporter.ui import theme

        pane.setObjectName("PagePaneUnderTest")
        theme.apply(pane)
        layout = pane.preview.view.currentLayout()
        shape = QgsLayoutItemShape(layout)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        mm = QgsUnitTypes.LayoutMillimeters
        shape.attemptMove(QgsLayoutPoint(-20, -20, mm))
        shape.attemptResize(QgsLayoutSize(400, 300, mm))
        shape.setSymbol(
            QgsFillSymbol.createSimple({"color": fill, "outline_style": "no"})
        )
        layout.addLayoutItem(shape)
        for _ in range(3):  # zoomed in: the map fills the view behind them
            _wheel_zoom(pane, 120)
        _settle(qgis_app)
        view = pane.preview.view
        rect = _control_rects(pane)["fit"]
        # The whole view as drawn, buttons included.
        image = view.grab().toImage()
        origin = view.viewport().pos()
        behind = image.pixelColor(
            origin.x() + rect.left() - 4, origin.y() + rect.top() - 4
        )
        assert behind.name() == fill
        # Just inside the button's border, at its left.
        inside = image.pixelColor(
            origin.x() + rect.left() + 3, origin.y() + rect.top() + rect.height() // 2
        )
        assert inside.name() != fill

    @pytest.mark.parametrize("size", [(1280, 800), (480, 360)])
    def test_fit_page_shows_the_whole_page_as_large_as_the_view_allows(
        self, qgis_app, pane, size
    ) -> None:
        pane.resize(*size)
        _settle(qgis_app)
        _wheel_zoom(pane, 120)
        pane.fit_button.click()
        _settle(qgis_app)
        viewport = pane.preview.view.viewport()
        page = _page_px(pane)
        assert viewport.rect().contains(page)
        # One dimension is within about 8 px of the view's edge, both sides.
        slack = min(viewport.width() - page.width(), viewport.height() - page.height())
        assert 10 <= slack <= 24, slack
        # Larger than when the page was fitted above a strip for the buttons.
        old_room = viewport.height() - 2 * (pane.fit_button.height() + 12 + 8)
        assert page.height() > old_room

    def test_the_workspace_is_grey_and_the_page_keeps_its_own_colour(
        self, qgis_app, pane
    ) -> None:
        view = pane.preview.view
        image = view.viewport().grab().toImage()
        page = _page_px(pane)
        assert (
            image.pixelColor(page.center().x(), page.center().y()).name() == "#ffffff"
        )
        assert image.pixelColor(3, 3).name() == "#dfe4e1"

    def test_fit_page_fits_the_current_page_of_a_multi_page_layout(
        self, qgis_app, pane
    ) -> None:
        from qgis.core import QgsLayoutItemPage

        view = pane.preview.view
        layout = view.currentLayout()
        second = QgsLayoutItemPage(layout)
        second.setPageSize("A4", QgsLayoutItemPage.Orientation.Landscape)
        layout.pageCollection().addPage(second)
        pane.fit_button.click()
        _settle(qgis_app)
        viewport = view.viewport().rect()
        first_page = _page_px(pane, 0)
        assert viewport.contains(first_page)
        assert not viewport.contains(_page_px(pane, 1))
        # Move to the second page and fit again: it is the one fitted now.
        view.centerOn(layout.pageCollection().page(1).sceneBoundingRect().center())
        _settle(qgis_app)
        pane.fit_button.click()
        _settle(qgis_app)
        assert viewport.contains(_page_px(pane, 1))
        assert not viewport.contains(_page_px(pane, 0))
        assert _page_px(pane, 1).width() == pytest.approx(first_page.width(), abs=3)

    def test_a_manual_zoom_and_pan_stay_until_fit_page_or_another_layout(
        self, qgis_app, project, pane
    ) -> None:
        view = pane.preview.view
        _wheel_zoom(pane, 120)
        _wheel_zoom(pane, 120)
        bar = view.horizontalScrollBar()
        bar.setValue(bar.minimum())
        _settle(qgis_app)
        kept = (view.transform().m11(), bar.value())
        pane.resize(700, 560)
        _settle(qgis_app)
        assert view.transform().m11() == pytest.approx(kept[0])
        from qgis.core import QgsPrintLayout

        other = QgsPrintLayout(project)
        other.initializeDefaults()
        other.setName("Other")
        project.layoutManager().addLayout(other)
        pane.show_layout(other)
        _settle(qgis_app)
        page = _page_px(pane)
        assert view.viewport().rect().contains(page)

    def test_the_controls_are_not_in_an_export(self, qgis_app, pane, tmp_path) -> None:
        from qgis.core import QgsLayoutExporter
        from qgis.PyQt.QtGui import QImage

        layout = pane.preview.view.currentLayout()
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = 30
        target = tmp_path / "page.png"
        exported = QgsLayoutExporter(layout).exportToImage(str(target), settings)
        assert exported == QgsLayoutExporter.ExportResult.Success
        image = QImage(str(target))
        colours = {
            image.pixelColor(x, y).name()
            for x in range(0, image.width(), 7)
            for y in range(0, image.height(), 7)
        }
        assert colours == {"#ffffff"}  # an empty page, nothing drawn on it


class TestControlsAndColourSimulation:
    def test_colour_simulation_does_not_recolour_the_controls(
        self, qgis_app, pane
    ) -> None:
        from qgis.gui import QgsPreviewEffect

        from nika_onlymap_exporter.ui import theme

        pane.setObjectName("PagePaneUnderTest")
        theme.apply(pane)
        _settle(qgis_app)
        view = pane.preview.view

        def button_pixels() -> list[str]:
            image = view.grab().toImage()
            out = []
            for button in (pane.add_button, pane.fit_button):
                spot = button.mapTo(view, button.rect().topLeft())
                out.append(image.pixelColor(spot.x() + 4, spot.y() + 4).name())
            return out

        plain = button_pixels()
        view.setPreviewModeEnabled(True)
        view.setPreviewMode(QgsPreviewEffect.PreviewMode.PreviewGrayscale)
        _settle(qgis_app)
        assert button_pixels() == plain
