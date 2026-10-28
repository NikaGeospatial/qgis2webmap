"""Near & Far: the inset on the page preview is directly editable.

Real widgets driven as a user does: smooth mouse drags with QTest, real wheel
events, key presses. "Edit view" pans and zooms a map's content inside its
stationary frame; Select moves and resizes the frame; each gesture is one
undo step; the inset's layers, fit and removal are undoable too.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from test_cartograph_panel_widgets import _a4_layout, _settle
from test_locator_context import _region

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import QgsLayoutItemMap, QgsRectangle  # noqa: E402
from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from qgis.PyQt.QtGui import (  # noqa: E402
    QColor,
    QKeyEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from qgis.PyQt.QtTest import QTest  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QLabel  # noqa: E402

from nika_onlymap_exporter.core.canvas_cartograph import (  # noqa: E402
    cartograph_from_view,
)
from nika_onlymap_exporter.ui.cartograph.content_edit import (  # noqa: E402
    WHEEL_BURST_MS,
    _difference,
)
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane  # noqa: E402


@pytest.fixture
def near_far(qgis_app, project):
    """A shown pane on a Near & Far layout: the main map and its inset,
    whose layers reach beyond the main view."""
    project.setTitle("Survey")
    parcels = build_parcels_layer()
    project.addMapLayer(parcels)
    region = _region(project, 3000)
    grown = QgsRectangle(parcels.extent())
    grown.scale(1.1)
    result = cartograph_from_view(
        grown,
        UTM,
        [parcels, region],
        project,
        template_id="locator",
        page_size="A4",
        orientation="landscape",
    )
    layout = result.layout
    pane = PagePane()
    pane.resize(1000, 760)
    pane.show()
    pane.show_layout(layout)
    _settle(qgis_app)
    main = layout.itemById("main")
    inset = layout.itemById("locator")
    yield pane, layout, main, inset, region
    # Background renders still queued for the page's maps finish while the
    # layout is alive (an item taken off it by a test is drawn once more).
    _settle(qgis_app, 0.4)
    pane.close()
    pane.deleteLater()


def _select(qgis_app, pane, layout, item) -> None:
    layout.deselectAll()
    item.setSelected(True)
    _settle(qgis_app, 0.1)


def _centre(pane, item) -> QPoint:
    view = pane.preview.view
    return view.mapFromScene(item.sceneBoundingRect().center())


def _drag(qgis_app, pane, start: QPoint, end: QPoint) -> None:
    from test_cartograph_panel_widgets import _move_held

    viewport = pane.preview.view.viewport()
    QTest.mouseMove(viewport, start)
    qgis_app.processEvents()
    QTest.mousePress(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    for step in range(1, 13):
        _move_held(
            viewport,
            QPoint(
                start.x() + (end.x() - start.x()) * step // 12,
                start.y() + (end.y() - start.y()) * step // 12,
            ),
        )
        qgis_app.processEvents()
    QTest.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )
    _settle(qgis_app, 0.05)


def _wheel(qgis_app, pane, at: QPoint, notches: int) -> None:
    viewport = pane.preview.view.viewport()
    event = QWheelEvent(
        QPointF(at),
        QPointF(viewport.mapToGlobal(at)),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)
    qgis_app.processEvents()


def _wheel_raw(qgis_app, pane, at: QPoint, pixel: QPoint, angle: QPoint) -> None:
    viewport = pane.preview.view.viewport()
    event = QWheelEvent(
        QPointF(at),
        QPointF(viewport.mapToGlobal(at)),
        pixel,
        angle,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)
    qgis_app.processEvents()


def _escape(pane) -> None:
    QTest.keyClick(pane.preview.view, Qt.Key.Key_Escape)


def _index(layout) -> int:
    return layout.undoStack().stack().index()


def _geometry(item) -> tuple[float, float, float, float]:
    box = item.sceneBoundingRect()
    return (box.x(), box.y(), box.width(), box.height())


def _edit_on(qgis_app, pane, layout, item) -> None:
    _select(qgis_app, pane, layout, item)
    pane.controls.edit_view_button.click()
    _settle(qgis_app, 0.05)
    assert pane.preview.content_editor.active


def _map_point_at(pane, item, point: QPoint) -> tuple[float, float]:
    """The map coordinate under a viewport point."""
    scene = pane.preview.view.mapToScene(point)
    local = item.mapFromScene(scene)
    extent = item.extent()
    rect = item.rect()
    return (
        extent.xMinimum() + local.x() / rect.width() * extent.width(),
        extent.yMaximum() - local.y() / rect.height() * extent.height(),
    )


class TestPanAndZoomTheContent:
    def test_a_drag_pans_the_content_not_the_frame(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        before = QgsRectangle(inset.extent())
        start = _centre(pane, inset)
        under = _map_point_at(pane, inset, start)
        end = start + QPoint(30, 12)
        _drag(qgis_app, pane, start, end)
        assert _geometry(inset) == pytest.approx(frame)
        assert inset.extent().width() == pytest.approx(before.width())
        assert inset.extent().center() != before.center()
        # The content follows the pointer: the point grabbed is under it still.
        now = _map_point_at(pane, inset, end)
        assert now == pytest.approx(under, abs=before.width() * 0.002)

    def test_one_drag_is_one_undo_step_and_undo_restores_it(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        index = _index(layout)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(-25, 18))
        assert _index(layout) == index + 1
        # A second drag is a second step, not merged into the first.
        _drag(qgis_app, pane, start, start + QPoint(10, 10))
        assert _index(layout) == index + 2
        layout.undoStack().stack().undo()
        layout.undoStack().stack().undo()
        assert inset.extent() == before
        layout.undoStack().stack().redo()
        assert inset.extent() != before

    def test_the_wheel_zooms_the_content_about_the_pointer(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        width = inset.extent().width()
        at = _centre(pane, inset) + QPoint(40, -15)
        under = _map_point_at(pane, inset, at)
        _wheel(qgis_app, pane, at, 1)
        assert inset.extent().width() < width
        assert _geometry(inset) == pytest.approx(frame)
        assert _map_point_at(pane, inset, at) == pytest.approx(under, abs=width * 0.002)
        _wheel(qgis_app, pane, at, -2)
        assert inset.extent().width() > width

    def test_a_trackpad_with_only_pixels_zooms_in_proportion(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        at = _centre(pane, inset) + QPoint(40, -15)
        under = _map_point_at(pane, inset, at)
        width = inset.extent().width()
        _wheel_raw(qgis_app, pane, at, QPoint(0, 6), QPoint(0, 0))
        small = inset.extent().width()
        assert small < width
        # A tiny swipe is a small zoom, far below one full notch (x1/1.2).
        assert small > width / 1.2
        assert _geometry(inset) == pytest.approx(frame)
        assert _map_point_at(pane, inset, at) == pytest.approx(under, abs=width * 0.002)
        _wheel_raw(qgis_app, pane, at, QPoint(0, -60), QPoint(0, 0))
        assert inset.extent().width() > small

    def test_the_angle_wins_when_both_are_reported(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        width = inset.extent().width()
        _wheel_raw(qgis_app, pane, _centre(pane, inset), QPoint(0, 6), QPoint(0, 120))
        assert inset.extent().width() == pytest.approx(width / 1.2, rel=1e-3)

    def test_a_pixel_burst_is_one_undo_step(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        index = _index(layout)
        at = _centre(pane, inset)
        for _ in range(8):
            _wheel_raw(qgis_app, pane, at, QPoint(0, 8), QPoint(0, 0))
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert inset.extent() == before

    def test_a_burst_of_wheel_steps_is_one_undo_step(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        index = _index(layout)
        at = _centre(pane, inset)
        for _ in range(5):
            _wheel(qgis_app, pane, at, 1)
        assert _index(layout) == index + 1
        # After a pause it is another step.
        time.sleep(WHEEL_BURST_MS / 1000 + 0.1)
        _wheel(qgis_app, pane, at, 1)
        assert _index(layout) == index + 2
        layout.undoStack().stack().undo()
        layout.undoStack().stack().undo()
        assert inset.extent() == before

    def test_a_wheel_step_after_an_undo_is_not_merged_into_the_old_burst(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        at = _centre(pane, inset)
        _wheel(qgis_app, pane, at, 1)
        stack = layout.undoStack().stack()
        stack.undo()
        undone = QgsRectangle(inset.extent())
        _wheel(qgis_app, pane, at, 1)
        stack.undo()
        assert inset.extent() == undone

    def test_the_wheel_never_scrolls_or_zooms_the_page(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        view = pane.preview.view
        scale = view.transform().m11()
        scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        _wheel(qgis_app, pane, _centre(pane, inset), 3)
        assert view.transform().m11() == scale
        assert (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        ) == scroll

    def test_the_main_map_works_the_same_way(self, qgis_app, near_far) -> None:
        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        frame = _geometry(main)
        before = QgsRectangle(main.extent())
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(20, 20))
        assert _geometry(main) == pytest.approx(frame)
        assert main.extent().center() != before.center()
        assert pane.controls.edit_view_button.text() == "Done editing view"


def _drag_path(start: QPoint, end: QPoint, steps: int = 12) -> list[QPoint]:
    return [
        QPoint(
            start.x() + (end.x() - start.x()) * step // steps,
            start.y() + (end.y() - start.y()) * step // steps,
        )
        for step in range(1, steps + 1)
    ]


def _similar(a, b, tolerance: int = 40) -> bool:
    """Two same-sized images whose pixels differ by little (a re-render
    redraws labels and edges a shade differently)."""
    if a.size() != b.size():
        return False
    total = 0
    for y in range(a.height()):
        for x in range(a.width()):
            p, q = a.pixelColor(x, y), b.pixelColor(x, y)
            total += abs(p.red() - q.red()) + abs(p.green() - q.green())
    return total / (a.width() * a.height()) < tolerance


class TestTheDragIsLive:
    """The content follows the pointer during a drag with no re-render, as the
    layout designer's own Move item content tool does; the change is applied
    once, on release."""

    @staticmethod
    def _spy(monkeypatch, item) -> list[tuple[float, float]]:
        offsets: list[tuple[float, float]] = []
        real = item.setMoveContentPreviewOffset

        def record(dx: float, dy: float) -> None:
            offsets.append((dx, dy))
            real(dx, dy)

        monkeypatch.setattr(item, "setMoveContentPreviewOffset", record)
        return offsets

    @staticmethod
    def _press(pane, at: QPoint) -> None:
        viewport = pane.preview.view.viewport()
        QTest.mouseMove(viewport, at)
        QTest.mousePress(
            viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at
        )

    @pytest.mark.parametrize("rotation", [0.0, 30.0])
    def test_the_offset_tracks_the_pointer_and_nothing_is_applied_mid_drag(
        self, qgis_app, near_far, monkeypatch, rotation
    ) -> None:
        from test_cartograph_panel_widgets import _move_held

        pane, layout, main, _inset, _region = near_far
        main.setItemRotation(rotation)
        _settle(qgis_app, 0.2)
        _edit_on(qgis_app, pane, layout, main)
        offsets = self._spy(monkeypatch, main)
        extent = QgsRectangle(main.extent())
        changed: list[int] = []
        main.extentChanged.connect(lambda: changed.append(1))
        index = _index(layout)
        view = pane.preview.view
        start = _centre(pane, main)
        self._press(pane, start)
        for point in _drag_path(start, start + QPoint(40, 22)):
            _move_held(view.viewport(), point)
            qgis_app.processEvents()
            assert not main.isDrawing() or not changed
            local = main.mapFromScene(view.mapToScene(point))
            origin = main.mapFromScene(view.mapToScene(start))
            assert offsets[-1] == pytest.approx(
                (local.x() - origin.x(), local.y() - origin.y())
            )
        assert main.extent() == extent
        assert changed == []
        assert _index(layout) == index
        QTest.mouseRelease(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start + QPoint(40, 22),
        )
        _settle(qgis_app, 0.2)
        assert main.extent() != extent
        assert _index(layout) == index + 1
        assert layout.undoStack().stack().undoText() == "Pan map content"

    @pytest.mark.parametrize("rotation", [0.0, 30.0])
    def test_release_moves_the_extent_by_exactly_the_pointer_delta(
        self, qgis_app, near_far, rotation
    ) -> None:
        pane, layout, main, _inset, _region = near_far
        main.setItemRotation(rotation)
        _settle(qgis_app, 0.2)
        _edit_on(qgis_app, pane, layout, main)
        view = pane.preview.view
        start = _centre(pane, main)
        end = start + QPoint(35, -20)
        before = main.extent().center()
        delta = main.mapFromScene(view.mapToScene(end)) - main.mapFromScene(
            view.mapToScene(start)
        )
        scale = main.extent().width() / main.rect().width()
        _drag(qgis_app, pane, start, end)
        after = main.extent().center()
        # The content goes with the pointer, so the extent goes the other way
        # (map y runs up, item y runs down); the pointer delta counts once.
        assert after.x() - before.x() == pytest.approx(-delta.x() * scale, rel=1e-3)
        assert after.y() - before.y() == pytest.approx(delta.y() * scale, rel=1e-3)

    def test_no_shift_is_left_over_the_new_extent_on_release(
        self, qgis_app, near_far, monkeypatch
    ) -> None:
        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        offsets = self._spy(monkeypatch, main)
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(20, 10))
        # QGIS draws the old picture at the new extent by itself: a shift still
        # set after the move would show the content at twice the drag.
        assert any(o != (0, 0) for o in offsets[:-1])
        assert offsets[-1] == (0, 0)

    @staticmethod
    def _frame(pane):
        return pane.preview.view.viewport().grab().toImage()

    @staticmethod
    def _wait_rendered(qgis_app, item) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and item.isDrawing():
            _settle(qgis_app, 0.05)
        _settle(qgis_app, 0.2)

    def test_the_picture_follows_the_pointer_mid_drag(self, qgis_app, near_far) -> None:
        """What is on screen mid-drag: the map picture is the one from before
        the press, moved by the pointer's displacement; the frame is still."""
        from test_cartograph_panel_widgets import _move_held

        pane, layout, main, _inset, _region = near_far
        self._wait_rendered(qgis_app, main)
        _edit_on(qgis_app, pane, layout, main)
        view = pane.preview.view
        box = view.mapFromScene(main.sceneBoundingRect()).boundingRect()
        before = self._frame(pane)
        start = box.center()
        self._press(pane, start)
        shift = QPoint(0, 0)
        for point in _drag_path(start, start + QPoint(36, 24), 6):
            _move_held(view.viewport(), point)
            qgis_app.processEvents()
        shift = QPoint(36, 24)
        _settle(qgis_app, 0.2)
        mid = self._frame(pane)
        # A patch well inside the part of the old picture that is still shown.
        patch = QRect(
            box.right() - shift.x() - 90, box.bottom() - shift.y() - 90, 60, 60
        )
        moved = patch.translated(shift)
        assert box.contains(patch) and box.contains(moved)
        assert mid.copy(moved) == before.copy(patch)
        # ... and it is a real picture shown, not a flat fill that matches anyway.
        assert before.copy(patch) != before.copy(patch.translated(shift))
        # The frame's own edge has not moved.
        assert view.mapFromScene(main.sceneBoundingRect()).boundingRect() == box
        QTest.mouseRelease(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start + shift,
        )
        self._wait_rendered(qgis_app, main)
        after = self._frame(pane)
        # After the one real move the same landmark is at the same place: no
        # snap-back and no second shift.
        assert _similar(after.copy(moved), before.copy(patch))

    def test_a_lost_last_move_still_ends_at_the_release_position(
        self, qgis_app, near_far
    ) -> None:
        from test_cartograph_panel_widgets import _move_held

        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        view = pane.preview.view
        start = _centre(pane, main)
        before = main.extent().center()
        scale = main.extent().width() / main.rect().width()
        self._press(pane, start)
        _move_held(view.viewport(), start + QPoint(10, 5))
        qgis_app.processEvents()
        end = start + QPoint(40, 25)  # no move event reached here
        QTest.mouseRelease(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            end,
        )
        _settle(qgis_app, 0.2)
        delta = main.mapFromScene(view.mapToScene(end)) - main.mapFromScene(
            view.mapToScene(start)
        )
        assert main.extent().center().x() - before.x() == pytest.approx(
            -delta.x() * scale, rel=1e-3
        )

    def test_escape_mid_drag_restores_without_an_undo_step(
        self, qgis_app, near_far, monkeypatch
    ) -> None:
        from test_cartograph_panel_widgets import _move_held

        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        offsets = self._spy(monkeypatch, main)
        extent = QgsRectangle(main.extent())
        index = _index(layout)
        view = pane.preview.view
        start = _centre(pane, main)
        self._press(pane, start)
        for point in _drag_path(start, start + QPoint(30, 30), 6):
            _move_held(view.viewport(), point)
            qgis_app.processEvents()
        _escape(pane)
        QTest.mouseRelease(
            view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        )
        _settle(qgis_app, 0.1)
        assert not pane.preview.content_editor.active
        assert main.extent() == extent
        assert _index(layout) == index
        assert offsets[-1] == (0, 0)

    def test_a_wheel_burst_is_one_undo_step_with_the_right_zoom(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        width = main.extent().width()
        index = _index(layout)
        at = _centre(pane, main) + QPoint(30, -10)
        under = _map_point_at(pane, main, at)
        for _ in range(4):
            _wheel(qgis_app, pane, at, 1)
        _settle(qgis_app, 0.1)
        assert _index(layout) == index + 1
        assert main.extent().width() == pytest.approx(width / 1.2**4, rel=1e-6)
        assert _map_point_at(pane, main, at) == pytest.approx(under, abs=width * 0.002)


class TestTheModeIsClear:
    def test_the_inset_names_its_button_and_the_banner_says_what_is_on(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.edit_view_button.text() == "Edit inset view"
        pane.controls.edit_view_button.click()
        _settle(qgis_app, 0.05)
        assert pane.controls.edit_view_button.isChecked()
        banner = pane.preview.content_editor._banner
        assert not banner.isHidden()
        assert inset.displayName() in banner.text()
        assert "Esc" in banner.text()

    @pytest.mark.parametrize("how", ["escape", "toggle", "other", "delete", "layout"])
    def test_every_exit_ends_the_mode(self, qgis_app, near_far, how) -> None:
        pane, layout, main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        if how == "escape":
            _escape(pane)
        elif how == "toggle":
            pane.controls.edit_view_button.click()
        elif how == "other":
            _select(qgis_app, pane, layout, main)
        elif how == "delete":
            pane.controls.delete_button.click()
        else:
            pane.show_layout(_a4_layout(layout.project()))
        _settle(qgis_app, 0.05)
        assert not pane.preview.content_editor.active
        assert not pane.controls.edit_view_button.isChecked()
        assert pane.preview.content_editor._banner.isHidden()

    def test_a_click_on_the_page_outside_the_frame_ends_it_and_selects(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        point = _centre(pane, main)
        QTest.mouseClick(
            pane.preview.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point,
        )
        _settle(qgis_app, 0.1)
        assert not pane.preview.content_editor.active
        assert layout.selectedLayoutItems() == [main]

    def test_escape_with_a_menu_open_is_left_to_the_menu(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(pane.preview.view, event)
        assert not pane.preview.content_editor.active

    def test_the_frame_cannot_be_moved_or_resized_in_the_mode(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        box = pane.preview.view.mapFromScene(inset.sceneBoundingRect()).boundingRect()
        corner = box.bottomRight()
        _drag(qgis_app, pane, corner, corner + QPoint(40, 30))
        assert _geometry(inset) == pytest.approx(frame)
        assert pane.preview.content_editor.active

    def test_delete_does_not_remove_the_frame_in_the_mode(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        QTest.keyClick(pane.preview.view, Qt.Key.Key_Delete)
        assert inset in layout.items()


class TestSelectModeStillMovesAndResizes:
    def test_after_the_mode_a_drag_moves_the_frame(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        _escape(pane)
        _settle(qgis_app, 0.05)
        before = _geometry(inset)
        extent = QgsRectangle(inset.extent())
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(-25, 15))
        moved = _geometry(inset)
        assert (moved[0], moved[1]) != pytest.approx((before[0], before[1]))
        assert inset.extent() == extent

    def test_after_the_mode_a_handle_resizes_the_frame(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        _escape(pane)
        _settle(qgis_app, 0.1)
        before = _geometry(inset)
        box = pane.preview.view.mapFromScene(inset.sceneBoundingRect()).boundingRect()
        corner = box.bottomLeft() + QPoint(2, -2)
        _drag(qgis_app, pane, corner, corner + QPoint(-20, 14))
        after = _geometry(inset)
        assert (after[2], after[3]) != pytest.approx((before[2], before[3]))


class TestLinkedItemsFollow:
    def test_the_inset_outline_follows_the_main_map_when_it_is_edited(
        self, qgis_app, near_far, tmp_path
    ) -> None:
        from qgis.core import QgsLayoutExporter

        pane, layout, main, inset, _region = near_far
        overview = inset.overviews().asList()[0]
        assert overview.linkedMap() is main
        _edit_on(qgis_app, pane, layout, main)

        def picture():
            return QgsLayoutExporter(layout).renderRegionToImage(
                inset.sceneBoundingRect(), dpi=60
            )

        before = picture()
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(40, 25))
        assert picture() != before

    def test_the_linked_scale_bar_follows_a_zoom(self, qgis_app, near_far) -> None:
        pane, layout, main, _inset, _region = near_far
        bars = [
            item
            for item in layout.items()
            if item.type() == qgis_core.QgsLayoutItemRegistry.LayoutScaleBar
        ]
        bar = next(b for b in bars if b.linkedMap() is main)
        _edit_on(qgis_app, pane, layout, main)
        scale = main.scale()
        at = _centre(pane, main)
        _wheel(qgis_app, pane, at, 2)
        _settle(qgis_app, 0.1)
        assert main.scale() < scale
        assert bar.linkedMap() is main
        assert bar.mapScale() if hasattr(bar, "mapScale") else True


class TestInsetControls:
    def test_the_inset_row_is_only_for_an_inset(self, qgis_app, near_far) -> None:
        pane, layout, main, inset, _region = near_far
        _select(qgis_app, pane, layout, main)
        assert pane.controls.inset_row.isHidden()
        _select(qgis_app, pane, layout, inset)
        assert not pane.controls.inset_row.isHidden()

    def test_context_layers_are_chosen_from_a_checkable_list_and_undone(
        self, qgis_app, near_far, project
    ) -> None:
        pane, layout, _main, inset, region = near_far
        _select(qgis_app, pane, layout, inset)
        controls = pane.controls
        controls.context_menu.aboutToShow.emit()
        actions = {a.text(): a for a in controls.context_menu.actions()}
        assert actions["region"].isChecked()
        index = _index(layout)
        actions["region"].trigger()
        assert region not in inset.layersToRender()
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert region in inset.layersToRender()
        layout.undoStack().stack().redo()
        assert region not in inset.layersToRender()

    def test_a_layer_added_to_the_inset_has_no_labels_in_it(
        self, qgis_app, near_far, project
    ) -> None:
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorLayer,
            QgsVectorLayerSimpleLabeling,
        )

        pane, layout, _main, inset, _region = near_far
        extra = QgsVectorLayer(
            "Polygon?crs=EPSG:32648&field=n:string", "named", "memory"
        )
        settings = QgsPalLayerSettings()
        settings.fieldName = "n"
        extra.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        extra.setLabelsEnabled(True)
        project.addMapLayer(extra)
        _select(qgis_app, pane, layout, inset)
        pane.controls.context_menu.aboutToShow.emit()
        next(
            a for a in pane.controls.context_menu.actions() if a.text() == "named"
        ).trigger()
        assert extra in inset.layersToRender()
        assert 'labelsEnabled="0"' in inset.layerStyleOverrides()[extra.id()]
        assert extra.labelsEnabled()

    def test_fit_context_refits_the_inset_and_undo_restores_it(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        _drag_view = QgsRectangle(before)
        _drag_view.scale(0.5)
        inset.zoomToExtent(_drag_view)
        moved = QgsRectangle(inset.extent())
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.fit_button.isEnabled()
        index = _index(layout)
        pane.controls.fit_button.click()
        assert _index(layout) == index + 1
        assert inset.extent().width() > moved.width()
        layout.undoStack().stack().undo()
        assert inset.extent() == moved

    def test_fit_context_is_disabled_with_the_reason_and_the_alternatives(
        self, qgis_app, project
    ) -> None:
        project.setTitle("Survey")
        parcels = build_parcels_layer()
        project.addMapLayer(parcels)
        grown = QgsRectangle(parcels.extent())
        grown.scale(1.1)
        result = cartograph_from_view(
            grown,
            UTM,
            [parcels],
            project,
            template_id="locator",
            page_size="A4",
            orientation="landscape",
        )
        pane = PagePane()
        pane.resize(1000, 760)
        pane.show()
        pane.show_layout(result.layout)
        _settle(qgis_app)
        try:
            inset = result.layout.itemById("locator")
            _select(qgis_app, pane, result.layout, inset)
            controls = pane.controls
            assert not controls.fit_button.isEnabled()
            note = controls.context_note.text()
            assert not controls.context_note.isHidden()
            assert "No wider context" in note
            assert "Context layers" in note and "remove the inset" in note
            # Adding a wider layer makes fitting available again.
            wide = _region(project, 3000)
            controls.context_menu.aboutToShow.emit()
            next(
                a for a in controls.context_menu.actions() if a.text() == wide.name()
            ).trigger()
            assert controls.fit_button.isEnabled()
            assert controls.context_note.isHidden()
        finally:
            pane.close()
            pane.deleteLater()

    def test_remove_inset_is_one_undo_step(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        index = _index(layout)
        pane.controls.remove_inset_button.click()
        _settle(qgis_app, 0.05)
        assert inset not in layout.items()
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert any(
            isinstance(i, QgsLayoutItemMap) and i.id() == "locator"
            for i in layout.items()
        )

    def test_use_canvas_extent_works_for_the_inset(
        self, qgis_app, near_far, project
    ) -> None:
        from types import SimpleNamespace

        from qgis.core import QgsCoordinateReferenceSystem

        from nika_onlymap_exporter.ui.cartograph.controller import (
            CartographController,
        )
        from nika_onlymap_exporter.ui.cartograph.frames_list import (
            FrameRow,
            FramesList,
        )

        pane, layout, main, inset, _region = near_far
        listing = FramesList((72, 150, 300))
        listing.set_frames(
            [
                FrameRow(
                    item_id=item.uuid(),
                    name=item.displayName(),
                    crs="EPSG:32648",
                    mode="live",
                    dpi=150,
                    issues=None,
                )
                for item in (main, inset)
            ]
        )
        pane.controls.bind_frames(listing)
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.canvas_button.isEnabled()
        asked: list[str] = []
        pane.controls.canvas_extent_requested.connect(asked.append)
        pane.controls.canvas_button.click()
        assert asked == [inset.uuid()]

        view = QgsRectangle(main.extent())
        view.scale(0.6)
        canvas = SimpleNamespace(
            extent=lambda: view,
            mapSettings=lambda: SimpleNamespace(
                destinationCrs=lambda: QgsCoordinateReferenceSystem("EPSG:32648")
            ),
        )
        stand_in = SimpleNamespace(
            _map_canvas=lambda: canvas,
            _current_layout=layout,
            project=project,
            schedule_autosave=lambda: None,
        )
        index = _index(layout)
        CartographController._match_canvas(stand_in, asked[0])
        assert inset.extent().center().x() == pytest.approx(view.center().x())
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert inset.extent().center() != view.center()
        listing.deleteLater()


class TestSurvives:
    def test_the_edited_view_is_in_the_project_file(
        self, qgis_app, near_far, project, tmp_path
    ) -> None:
        from qgis.core import QgsProject

        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(30, 10))
        _wheel(qgis_app, pane, start, 1)
        edited = QgsRectangle(inset.extent())
        path = tmp_path / "kept.qgz"
        assert project.write(str(path))
        reread = QgsProject()
        assert reread.read(str(path))
        again = reread.layoutManager().layoutByName(layout.name())
        kept = again.itemById("locator").extent()
        assert kept.xMinimum() == pytest.approx(edited.xMinimum())
        assert kept.yMaximum() == pytest.approx(edited.yMaximum())

    def test_an_edit_reaches_the_autosave_hook(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        seen = []
        layout.undoStack().stack().indexChanged.connect(seen.append)
        _edit_on(qgis_app, pane, layout, inset)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(30, 10))
        assert seen

    def test_the_edited_extent_is_in_the_html(
        self, qgis_app, near_far, project
    ) -> None:
        from test_preview_item_editing import TestWhatAddingMakesSurvives

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        pane, layout, main, _inset, _region = near_far
        runtime = TestWhatAddingMakesSurvives._runtime()

        def html() -> str:
            return export_layout_to_cartograph(
                layout, project, runtime, FidelityReportBuilder(), static_dpi=60
            )

        before = html()
        _edit_on(qgis_app, pane, layout, main)
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(50, 30))
        assert html() != before


class TestLegendAddedFromThePreview:
    def test_a_new_legend_leaves_the_support_rasters_out_like_a_template(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import hillshade_layer, write_tif
        from qgis.core import QgsLayoutItemLegend
        from test_preview_item_editing import _choose, _click_page, _pane

        from nika_onlymap_exporter.core import legend_edit

        parcels = build_parcels_layer()
        hillshade = hillshade_layer(write_tif(tmp_path / "h.tif"))
        project.addMapLayers([parcels, hillshade])
        pane, layout, frames = _pane(qgis_app, project)
        try:
            frames[0].setKeepLayerSet(True)
            frames[0].setLayers([parcels, hillshade])
            index = _index(layout)
            _choose(pane, "Legend")
            _click_page(qgis_app, pane, 20, 150)
            legend = next(
                i for i in layout.items() if isinstance(i, QgsLayoutItemLegend)
            )
            ids = [n.layerId() for n in legend.model().rootGroup().findLayers()]
            assert ids == [parcels.id()]
            assert hillshade.id() in legend_edit.support_left_out(legend)
            # One step: Add legend, defaults included.
            assert _index(layout) == index + 1
            layout.undoStack().stack().undo()
            assert not any(isinstance(i, QgsLayoutItemLegend) for i in layout.items())
        finally:
            pane.close()
            pane.deleteLater()


def _frame_has_rendered_map(pane, item) -> bool:
    """The map frame on screen shows real map pixels, not the blank page or
    the grey "rendering" placeholder: several distinct colours (the fixture's
    parcels, region and outlines), never one flat colour."""
    view = pane.preview.view
    viewport = view.viewport()
    box = view.mapFromScene(item.sceneBoundingRect()).boundingRect()
    box = box.intersected(viewport.rect())
    shot = viewport.grab().toImage().copy(box).scaled(60, 60)
    colours = {shot.pixel(x, y) for x in range(60) for y in range(60)}
    return len(colours) > 3


def _wait_for_rendered_map(qgis_app, pane, item, seconds: float = 40.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _settle(qgis_app, 0.2)
        if not item.isDrawing() and _frame_has_rendered_map(pane, item):
            return
    pytest.fail("the map never finished rendering real pixels on the page")


def _richest_patch(image, box: QRect, size: int = 24) -> QRect:
    """The size x size square inside `box` with the most distinct colours,
    kept clear of the frame's edge, where the selection handles and rotate
    icons are drawn: they stay put in a drag, so they are not a landmark."""
    best, best_rect = -1, QRect()
    for y in range(box.top() + 40, box.bottom() - size - 110, 12):
        for x in range(box.left() + 40, box.right() - size - 100, 12):
            colours = {
                image.pixel(x + i, y + j)
                for i in range(0, size, 3)
                for j in range(0, size, 3)
            }
            if len(colours) > best:
                best, best_rect = len(colours), QRect(x, y, size, size)
    return best_rect


def _patch_difference(a, rect_a: QRect, b, rect_b: QRect) -> float:
    total = 0
    for i in range(rect_a.width()):
        for j in range(rect_a.height()):
            pa = a.pixelColor(rect_a.x() + i, rect_a.y() + j)
            pb = b.pixelColor(rect_b.x() + i, rect_b.y() + j)
            total += abs(pa.red() - pb.red()) + abs(pa.green() - pb.green())
            total += abs(pa.blue() - pb.blue())
    return total / (rect_a.width() * rect_a.height() * 3)


class TestAFastDragStaysSmooth:
    """A flick across a large page keeps up with the pointer: the picture is
    at the latest pointer position and the event loop is never held up, also
    with a colour-vision simulation on (its filter makes every full repaint of
    the view cost tens of milliseconds)."""

    #: Per move (send + the event loop turning once, repaint included).
    MOVE_BUDGET_MS = 30.0
    #: Longest stall of the event loop during the whole flick.
    STALL_BUDGET_MS = 60.0

    @pytest.mark.parametrize("simulate", [False, True])
    def test_a_flick_follows_the_pointer_without_stalling(
        self, qgis_app, near_far, simulate
    ) -> None:
        from qgis.gui import QgsPreviewEffect
        from qgis.PyQt.QtCore import QTimer
        from test_cartograph_panel_widgets import _move_held

        pane, layout, main, _inset, _region = near_far
        pane.resize(1500, 1000)
        view = pane.preview.view
        if simulate:
            view.setPreviewModeEnabled(True)
            view.setPreviewMode(QgsPreviewEffect.PreviewMode.PreviewDeuteranope)
        _settle(qgis_app, 0.5)
        _wait_for_rendered_map(qgis_app, pane, main)
        _edit_on(qgis_app, pane, layout, main)
        # Edit view is really on: its button says so and the banner shows.
        assert pane.controls.edit_view_button.text() == "Done editing view"
        viewport = view.viewport()
        assert any(
            label.isVisible() and label.text().startswith("Editing the view inside")
            for label in viewport.findChildren(QLabel)
        )
        frame_before = _geometry(main)
        box = view.mapFromScene(main.sceneBoundingRect()).boundingRect()
        before = viewport.grab().toImage()
        patch = _richest_patch(before, box)
        start = QPoint(patch.center())
        shift = QPoint(70, 90)
        gaps: list[float] = []
        last = [time.perf_counter()]

        def beat() -> None:
            now = time.perf_counter()
            gaps.append((now - last[0]) * 1000)
            last[0] = now

        heart = QTimer()
        heart.setInterval(5)
        heart.timeout.connect(beat)
        QTest.mouseMove(viewport, start)
        QTest.mousePress(
            viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
        )
        qgis_app.processEvents()
        heart.start()
        last[0] = time.perf_counter()
        per_move: list[float] = []
        steps = 60
        for step in range(1, steps + 1):
            # Out and back: a big sweep that ends at `shift` from the press.
            wobble = 90 if step < steps else 0
            point = start + QPoint(
                shift.x() * step // steps,
                shift.y() * step // steps + (wobble * (step % 7)) // 7,
            )
            began = time.perf_counter()
            _move_held(viewport, point)
            qgis_app.processEvents()
            per_move.append((time.perf_counter() - began) * 1000)
            time.sleep(0.004)  # a flick sends a move every few milliseconds
        end = start + shift
        _move_held(viewport, end)
        qgis_app.processEvents()
        heart.stop()
        _settle(qgis_app, 0.1)
        # What is on screen: the window as composed, the viewport's part of it.
        spot = viewport.mapTo(pane, QPoint(0, 0))
        mid = pane.grab().toImage().copy(QRect(spot, viewport.size()))
        QTest.mouseRelease(
            viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
        )
        _settle(qgis_app, 0.2)
        per_move.sort()
        median = per_move[len(per_move) // 2]
        assert median < self.MOVE_BUDGET_MS, per_move
        assert max(gaps) < self.STALL_BUDGET_MS, max(gaps)
        # The picture mid-drag is at the latest pointer position.
        assert _geometry(main) == frame_before  # the content moves, not the frame
        # Nothing of the page around the frame slides into it: the strip the
        # shift uncovers (under the title) is one flat colour.
        strip = QRect(box.left() + shift.x() + 4, box.top() + 3, 60, shift.y() - 6)
        flat = {
            mid.pixel(x, y)
            for x in range(strip.left(), strip.right(), 3)
            for y in range(strip.top(), strip.bottom(), 3)
        }
        assert len(flat) <= 2, len(flat)
        moved = QRect(patch.translated(shift))
        assert _patch_difference(before, patch, mid, moved) < 6
        assert _patch_difference(before, patch, mid, patch) > _patch_difference(
            before, patch, mid, moved
        )


class TestTheHandleLayerSitsWhereTheHandlesAre:
    """The layer that stays put mid-drag is cut from two grabs; on a screen
    with a device pixel ratio above 1 (125 % scaling) it came out stretched, so
    the handles were missing and map bits showed in the uncovered strip."""

    @pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0])
    def test_only_the_changed_pixels_are_kept_where_they_are(self, qgis_app, ratio):
        bare = QPixmap(int(200 * ratio), int(120 * ratio))
        bare.setDevicePixelRatio(ratio)
        bare.fill(QColor("#c8b080"))
        shown = QPixmap(bare)
        painter = QPainter(shown)
        painter.fillRect(20, 30, 24, 16, QColor("#2060d0"))  # a "handle"
        painter.end()
        layer = _difference(shown, bare).toImage()
        assert layer.devicePixelRatio() == pytest.approx(ratio)
        changed = QRect(
            round(20 * ratio), round(30 * ratio), round(24 * ratio), round(16 * ratio)
        )
        for x in range(layer.width()):
            for y in range(layer.height()):
                inside = changed.contains(x, y)
                alpha = layer.pixelColor(x, y).alpha()
                margin = 2  # edge rounding
                if inside and changed.adjusted(
                    margin, margin, -margin, -margin
                ).contains(x, y):
                    assert alpha == 255, (x, y)
                elif not changed.adjusted(-margin, -margin, margin, margin).contains(
                    x, y
                ):
                    assert alpha == 0, (x, y)
