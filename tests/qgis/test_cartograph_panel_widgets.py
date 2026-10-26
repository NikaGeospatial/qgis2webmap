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


def _percent(pane) -> int:
    text = pane.zoom_label.text()
    assert text.endswith("%"), text
    return int(text[:-1])


class TestPagePane:
    def test_the_caption_names_the_layout_and_its_page(self, pane) -> None:
        assert pane.caption.text() == "Layout preview — QGIS · A4 landscape"
        assert not pane.caption.isHidden()
        assert not pane.zoom_bar.isHidden()

    def test_without_a_layout_the_caption_and_zoom_bar_hide(
        self, qgis_app, pane
    ) -> None:
        pane.show_layout(None)
        _settle(qgis_app)
        assert not pane.preview.showing_page()
        assert pane.caption.isHidden()
        assert pane.zoom_bar.isHidden()

    def test_the_page_is_not_the_first_tab_stop(self, pane) -> None:
        """A click still focuses the page; Tab goes to the zoom bar."""
        from qgis.PyQt.QtCore import Qt

        assert pane.preview.view.focusPolicy() == Qt.FocusPolicy.ClickFocus

    def test_the_zoom_buttons_change_the_level_shown(self, qgis_app, pane) -> None:
        fitted = _percent(pane)
        assert fitted > 0
        pane.zoom_in_button.click()
        _settle(qgis_app)
        zoomed_in = _percent(pane)
        assert zoomed_in > fitted
        pane.zoom_out_button.click()
        pane.zoom_out_button.click()
        _settle(qgis_app)
        assert _percent(pane) < zoomed_in
        assert _percent(pane) < fitted

    def test_fit_page_refits_after_the_user_zoomed(self, qgis_app, pane) -> None:
        fitted = _scale(pane)
        pane.zoom_in_button.click()
        _settle(qgis_app)
        assert pane.preview._user_zoomed
        assert _scale(pane) > fitted * 1.05
        pane.fit_button.click()
        _settle(qgis_app)
        assert not pane.preview._user_zoomed
        # The same fit as before the zoom: refitted once the zoomed page's
        # scroll bars have gone, which took 2.4% off the first fit.
        assert _scale(pane) == pytest.approx(fitted, rel=0.002)

    def test_a_resize_alone_keeps_the_page_fitting(self, qgis_app, pane) -> None:
        """The view reports a zoom change when it is only resized; that is
        not the user zooming, so the page fits the new size."""
        small = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert not pane.preview._user_zoomed
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
        assert pane.preview._user_zoomed
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

    def test_select_is_the_default_mode(self, editing) -> None:
        from qgis.PyQt.QtCore import Qt

        pane, _layout, _frame = editing
        assert pane.select_button.isChecked()
        assert not pane.pan_button.isChecked()
        assert pane.preview.select_mode()
        assert pane.select_button.toolTip() != ""
        # Reachable by Tab, with a name for a screen reader.
        assert pane.select_button.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert pane.select_button.accessibleName()

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

    def test_pan_mode_moves_the_view_not_the_item(self, qgis_app, editing) -> None:
        pane, layout, frame = editing
        pane.pan_button.click()
        assert not pane.preview.select_mode()
        pane.zoom_in_button.click()
        pane.zoom_in_button.click()
        _settle(qgis_app)
        before, count = frame.pos(), layout.undoStack().stack().count()
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre + type(centre)(60, 40))
        assert frame.pos() == before
        assert layout.undoStack().stack().count() == count
        assert not frame.isSelected()
        pane.select_button.click()
        assert pane.preview.select_mode()

    def test_selecting_stops_the_page_refitting(self, qgis_app, editing) -> None:
        pane, _layout, frame = editing
        centre = self._centre(pane, frame)
        _drag(qgis_app, pane, centre, centre)
        assert pane.preview._user_zoomed
        kept = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert _scale(pane) == pytest.approx(kept, rel=0.01)


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
    def test_the_name_then_the_folder_from_home(self, qgis_app, save_location) -> None:
        folder = Path.home() / "Documents" / "QGIS2WebMap"
        path = folder / "parcels-Survey.html"
        save_location.setText(str(path))
        qgis_app.processEvents()
        assert save_location.label.text() == "parcels-Survey.html"
        assert save_location.label.toolTip() == str(path)
        assert save_location.folder_label.text() == str(
            Path("~") / "Documents" / "QGIS2WebMap"
        )
        assert save_location.folder_label.toolTip() == str(folder)
        assert not save_location.folder_label.isHidden()
        assert not save_location.reveal_button.isHidden()
        assert save_location.reveal_button.text() == "Show in folder"

    def test_a_folder_outside_home_is_shown_whole(
        self, qgis_app, save_location, tmp_path
    ) -> None:
        if tmp_path.is_relative_to(Path.home()):
            pytest.skip("the temporary folder is under home on this machine")
        save_location.setText(str(tmp_path / "a.html"))
        qgis_app.processEvents()
        assert save_location.folder_label.text() == str(tmp_path)

    def test_with_no_path_only_the_placeholder_shows(
        self, qgis_app, save_location
    ) -> None:
        save_location.setText(str(Path.home() / "a.html"))
        save_location.setText("")
        qgis_app.processEvents()
        assert save_location.label.text() == "Choose where the cartograph is saved"
        assert save_location.folder_label.isHidden()
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
        assert pane.text_size_bar.isHidden()
        self._select(qgis_app, layout, items["title"])
        assert not pane.text_size_bar.isHidden()
        base = title.textFormat().size()
        assert pane.text_size_label.text() == f"{base:g} pt"
        self._select(qgis_app, layout, items["legend"])
        assert not pane.text_size_bar.isHidden()
        self._select(qgis_app, layout, items["map"])
        assert pane.text_size_bar.isHidden()
        layout.deselectAll()
        _settle(qgis_app, 0.1)
        assert pane.text_size_bar.isHidden()

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
        pane.text_bigger_button.click()
        _settle(qgis_app)
        assert title.textFormat().size() == pytest.approx(base + 1)
        assert pane.text_size_label.text() == f"{base + 1:g} pt"
        assert stack.count() == steps + 1
        assert title.sceneBoundingRect() == box
        assert _scale(pane) == pytest.approx(scale)
        assert (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        ) == scroll
        pane.text_smaller_button.click()
        pane.text_smaller_button.click()
        assert title.textFormat().size() == pytest.approx(base - 1)
        stack.undo()
        stack.undo()
        stack.undo()
        assert title.textFormat().size() == pytest.approx(base)
        assert pane.text_size_label.text() == f"{base:g} pt"
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
        pane.text_bigger_button.click()
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
