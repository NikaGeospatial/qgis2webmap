"""The Edit tab's resizable text box, and the save block in the bottom row.

Real widgets, driven as a user drives them: a press on the grip, a smooth path
of moves, a release.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_cartograph_dialog import (  # noqa: F401
    _add_layout,
    _choose_cartograph,
    _choose_web,
    _dialog,
    fake_cartograph_export,
    picked_template,
)
from test_cartograph_panel_widgets import _settle

qgis_core = pytest.importorskip("qgis.core")


def _controls(qgis_app):
    from nika_onlymap_exporter.ui.cartograph.item_controls import ContextControls

    controls = ContextControls()
    controls.resize(420, 900)
    controls.show()
    _settle(qgis_app, 0.1)
    return controls


def _drag_grip(qgis_app, grip, dy: int) -> None:
    """Press on the grip, move down `dy` px in small steps, release.

    The pointer's screen position is what a mouse reports, fixed in screen
    coordinates: the grip itself moves as the box grows under it.
    """
    from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
    from qgis.PyQt.QtGui import QMouseEvent
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QApplication

    start = QPoint(grip.width() // 2, grip.height() // 2)
    origin = grip.mapToGlobal(start)
    QTest.mousePress(
        grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    steps = 10
    for step in range(1, steps + 1):
        screen = QPoint(origin.x(), origin.y() + dy * step // steps)
        QApplication.sendEvent(
            grip,
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(grip.mapFromGlobal(screen)),
                QPointF(screen),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        qgis_app.processEvents()
    QTest.mouseRelease(
        grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    qgis_app.processEvents()


class TestTextBoxHeight:
    def _min_px(self, controls) -> int:
        from nika_onlymap_exporter.ui.cartograph.item_controls import TEXT_BOX_LINES_MIN

        line, chrome = controls._text_line_metrics()
        return TEXT_BOX_LINES_MIN * line + chrome

    def test_the_default_is_much_taller_than_three_lines(self, qgis_app) -> None:
        controls = _controls(qgis_app)
        try:
            assert controls.text_edit.height() >= self._min_px(controls) * 2
            line, _chrome = controls._text_line_metrics()
            assert controls.text_edit.height() >= 8 * line
        finally:
            controls.close()

    def test_dragging_the_grip_changes_the_height_only(self, qgis_app) -> None:
        controls = _controls(qgis_app)
        try:
            edit = controls.text_edit
            width, height = edit.width(), edit.height()
            _drag_grip(qgis_app, controls.text_grip, 60)
            assert edit.height() == pytest.approx(height + 60, abs=2)
            assert edit.width() == width
            _drag_grip(qgis_app, controls.text_grip, -30)
            assert edit.height() == pytest.approx(height + 30, abs=2)
        finally:
            controls.close()

    def test_the_height_is_clamped_both_ways(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.cartograph.item_controls import TEXT_BOX_LINES_MAX

        controls = _controls(qgis_app)
        try:
            _drag_grip(qgis_app, controls.text_grip, -5000)
            assert controls.text_edit.height() == self._min_px(controls)
            _drag_grip(qgis_app, controls.text_grip, 20000)
            line, chrome = controls._text_line_metrics()
            assert controls.text_edit.height() == TEXT_BOX_LINES_MAX * line + chrome
        finally:
            controls.close()

    def test_the_height_is_remembered_by_the_next_dialog(self, qgis_app) -> None:
        first = _controls(qgis_app)
        try:
            _drag_grip(qgis_app, first.text_grip, 80)
            chosen = first.text_edit.height()
        finally:
            first.close()
        second = _controls(qgis_app)
        try:
            assert second.text_edit.height() == chosen
        finally:
            second.close()

    def test_the_grip_ignores_the_wheel_and_never_takes_focus(self, qgis_app) -> None:
        from qgis.PyQt.QtCore import QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QWheelEvent
        from qgis.PyQt.QtWidgets import QApplication

        controls = _controls(qgis_app)
        try:
            grip = controls.text_grip
            assert grip.focusPolicy() == Qt.FocusPolicy.NoFocus
            before = controls.text_edit.height()
            event = QWheelEvent(
                QPointF(4, 4),
                QPointF(grip.mapToGlobal(QPoint(4, 4))),
                QPoint(0, 0),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(grip, event)
            assert not event.isAccepted()
            assert controls.text_edit.height() == before
        finally:
            controls.close()


def _cartograph_dialog(project, name="Survey"):
    _add_layout(project, name, [("Overview", "EPSG:4326")])
    dialog = _dialog(project)
    _choose_cartograph(dialog)
    return dialog


class TestSaveBlockInTheBottomRow:
    def test_it_sits_in_the_dialogs_row_not_the_panel(self, qgis_app, project) -> None:
        dialog = _cartograph_dialog(project)
        try:
            dialog.show()
            _settle(qgis_app, 0.2)
            cartograph = dialog._cartograph
            bar = cartograph.save_bar
            assert not cartograph.workspace.isAncestorOf(bar)
            assert not cartograph.panel_scroll.isAncestorOf(cartograph.file_edit)
            assert bar.isVisible()
            assert dialog._button_row.indexOf(bar) >= 0
            close = dialog.close_button
            # One row: the same band as Close, to its left.
            assert abs(bar.geometry().center().y() - close.geometry().center().y()) <= 6
            assert bar.geometry().right() < close.geometry().left()
            # The status sits between the name and the buttons.
            label = cartograph.cartograph_status_label
            edit = cartograph.file_edit
            assert edit.label.geometry().right() <= label.geometry().left() + 1
            assert label.geometry().right() <= edit.change_button.geometry().left() + 1
            assert edit.change_button.geometry().right() < edit.reveal_button.x()
        finally:
            dialog.close()

    def test_the_panel_got_the_blocks_height_back(self, qgis_app, project) -> None:
        dialog = _cartograph_dialog(project)
        try:
            dialog.show()
            _settle(qgis_app, 0.2)
            cartograph = dialog._cartograph
            scroll = cartograph.panel_scroll
            bottom = scroll.mapTo(dialog, scroll.rect().bottomLeft()).y()
            # The scroll area ends where the dialog's lower rows begin: no
            # save block (name, folder, buttons) is stacked under it any more.
            bar_top = cartograph.save_bar.mapTo(
                dialog, cartograph.save_bar.rect().topLeft()
            ).y()
            assert bottom < bar_top
            assert scroll.parentWidget().layout().count() == 1
            assert not scroll.parentWidget().isAncestorOf(cartograph.file_edit)
        finally:
            dialog.close()

    def test_it_is_hidden_in_web_map_mode_and_back_again(
        self, qgis_app, project
    ) -> None:
        dialog = _cartograph_dialog(project)
        try:
            dialog.show()
            _settle(qgis_app, 0.1)
            bar = dialog._cartograph.save_bar
            assert bar.isVisible()
            for _ in range(2):
                _choose_web(dialog)
                _settle(qgis_app, 0.05)
                assert not bar.isVisible()
                assert dialog.status_label.isVisible()
                _choose_cartograph(dialog)
                _settle(qgis_app, 0.05)
                assert bar.isVisible()
        finally:
            dialog.close()

    def test_a_long_name_elides_first_and_the_buttons_stay_whole(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.ui.main_dialog import (
            MINIMUM_DIALOG_HEIGHT,
            MINIMUM_DIALOG_WIDTH,
        )

        dialog = _cartograph_dialog(project)
        try:
            dialog.show()
            edit = dialog._cartograph.file_edit
            name = "carto-grand-canyon-dense-labels-Access-water-terrain.html"
            edit.setText(f"/home/someone/Documents/QGIS2WebMap/Cartographs/{name}")
            dialog.resize(MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT)
            _settle(qgis_app, 0.2)
            assert edit.label.text() != name and "…" in edit.label.text()
            assert name in edit.label.toolTip()
            for button in (edit.change_button, edit.reveal_button):
                assert button.isVisible()
                assert button.width() >= button.sizeHint().width()
                assert button.geometry().right() <= edit.rect().right()
            assert (
                dialog.close_button.geometry().left()
                > edit.reveal_button.mapTo(
                    dialog, edit.reveal_button.rect().topRight()
                ).x()
            )
        finally:
            dialog.close()

    def test_the_tab_order_runs_left_to_right_into_close(
        self, qgis_app, project
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        dialog = _cartograph_dialog(project)
        try:
            dialog.show()
            _settle(qgis_app, 0.1)
            edit = dialog._cartograph.file_edit
            chain = []
            widget = edit.change_button
            for _ in range(12):
                if widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                    chain.append(widget)
                widget = widget.nextInFocusChain()
            assert chain[:2] == [edit.change_button, edit.reveal_button]
            assert chain[2] is dialog.close_button
        finally:
            dialog.close()

    def test_the_saved_age_still_ticks_in_the_row(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph import controller

        dialog = _cartograph_dialog(project)
        try:
            cartograph = dialog._cartograph
            cartograph._autosave_timer.stop()
            now = [10_000.0]
            monkeypatch.setattr(controller.time, "time", lambda: now[0])
            cartograph._last_saved_at = now[0]
            cartograph._last_saved_bytes = 2_600_000
            cartograph._update_status_label()
            label = cartograph.cartograph_status_label
            assert label.text() == "Saved just now · 2.5 MB"
            assert cartograph.file_edit.isAncestorOf(label)
            now[0] += 5 * 60
            cartograph._status_age_timer.timeout.emit()
            assert label.text() == "Saved 5 min ago · 2.5 MB"
        finally:
            dialog.close()
