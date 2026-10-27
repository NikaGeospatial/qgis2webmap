"""Panel polish: the rename dialog's size, the save line's refresh rate, the
file row's buttons, the imported cartograph's page fields, and one Layers note.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from test_cartograph_dialog import (  # noqa: E402, F401
    _add_layout,
    _choose_cartograph,
    _choose_web,
    _dialog,
    _rename_through_dialog,
    _wait_for,
    fake_cartograph_export,
    picked_template,
)


def _open_rename(dialog, probe):
    """Click Rename..., run `probe(box)` while the dialog is open."""
    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QApplication

    seen = []

    def drive() -> None:
        box = QApplication.activeModalWidget()
        seen.append(probe(box))
        box.reject()

    QTimer.singleShot(0, drive)
    dialog._cartograph.rename_action.trigger()
    return seen[0]


class TestRenameDialogSize:
    LONG = "After Hours · Grand Canyon | Access, water & terrain (2)"

    def _dialog(self, project, name):
        _add_layout(project, name, [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        return dialog

    def test_the_box_has_room_for_a_long_name_and_it_is_all_selected(
        self, qgis_app, project
    ) -> None:
        dialog = self._dialog(project, self.LONG)
        try:

            def probe(box):
                metrics = box.name_edit.fontMetrics()
                return (
                    box.width(),
                    box.name_edit.width(),
                    metrics.horizontalAdvance(self.LONG),
                    box.name_edit.selectedText(),
                    box.current_label.text(),
                )

            _width, field, needed, selected, shown = _open_rename(dialog, probe)
            assert field >= needed
            assert selected == self.LONG
            assert self.LONG in shown
        finally:
            dialog.close()

    def test_a_short_name_still_gets_room_for_about_45_characters(
        self, qgis_app, project
    ) -> None:
        dialog = self._dialog(project, "Map")
        try:

            def probe(box):
                return box.name_edit.width(), box.name_edit.fontMetrics()

            field, metrics = _open_rename(dialog, probe)
            assert field >= 40 * metrics.averageCharWidth()
        finally:
            dialog.close()

    def test_typing_replaces_the_selected_name(self, qgis_app, project) -> None:
        from qgis.PyQt.QtTest import QTest

        dialog = self._dialog(project, "Map")
        try:

            def probe(box):
                QTest.keyClicks(box.name_edit, "Fresh")
                return box.new_name()

            assert _open_rename(dialog, probe) == "Fresh"
        finally:
            dialog.close()


class TestSaveLineRefresh:
    def _shown(self, project):
        _add_layout(project, "Map", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        return dialog, dialog._cartograph

    def test_the_age_refreshes_every_30_seconds_not_every_second(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph = self._shown(project)
        try:
            assert cartograph._status_age_timer.interval() == 30_000
            assert cartograph._status_age_timer.isActive()
            _choose_web(dialog)
            assert not cartograph._status_age_timer.isActive()
        finally:
            dialog.close()

    def test_the_one_second_poll_leaves_the_line_alone(self, qgis_app, project) -> None:
        dialog, cartograph = self._shown(project)
        try:
            label = cartograph.cartograph_status_label
            label.setText("sentinel")
            cartograph._on_status_tick()
            assert label.text() == "sentinel"
        finally:
            dialog.close()

    def test_the_age_moves_on_with_the_clock_and_a_save_shows_at_once(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph import controller

        dialog, cartograph = self._shown(project)
        try:
            cartograph._autosave_timer.stop()
            now = [10_000.0]
            monkeypatch.setattr(controller.time, "time", lambda: now[0])
            cartograph._last_saved_at = now[0]
            cartograph._last_saved_bytes = 5 * 1024 * 1024
            cartograph._update_status_label()
            label = cartograph.cartograph_status_label
            assert label.text() == "Saved just now · 5.0 MB"
            now[0] += 20 * 60
            assert label.text().startswith("Saved just now")  # not redrawn yet
            cartograph._status_age_timer.timeout.emit()
            assert label.text() == "Saved 20 min ago · 5.0 MB"
            # A pending edit shows at once, without waiting for any timer.
            cartograph.schedule_autosave()
            assert label.text() == "Changes pending"
            cartograph._autosave_timer.stop()
        finally:
            dialog.close()


class TestSaveLocationButtons:
    def _widget(self, width, path):
        from nika_onlymap_exporter.ui.cartograph.layout_page import SaveLocation

        widget = SaveLocation()
        widget.setText(path)
        widget.resize(width, 120)
        widget.show()
        return widget

    @pytest.mark.parametrize("width", [320, 250])
    def test_both_buttons_are_whole_and_inside_the_row(self, qgis_app, width) -> None:
        widget = self._widget(
            width,
            "/home/someone/Documents/QGIS2WebMap/Cartographs/carto-grand-canyon-terrain-2.html",
        )
        try:
            qgis_app.processEvents()
            for button in (widget.change_button, widget.reveal_button):
                assert button.width() >= button.sizeHint().width()
                assert widget.rect().contains(button.geometry())
                assert button.toolTip() and button.accessibleName()
            assert widget.change_button.y() > widget.folder_label.y()
            assert widget.change_button.geometry().right() < widget.reveal_button.x()
            assert widget.label.toolTip().endswith("terrain-2.html")
        finally:
            widget.close()

    def test_the_buttons_still_report_clicks(self, qgis_app) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        widget = self._widget(320, "/tmp/a.html")
        try:
            got = []
            widget.change_requested.connect(lambda: got.append("change"))
            widget.reveal_requested.connect(lambda: got.append("reveal"))
            QTest.mouseClick(widget.change_button, Qt.MouseButton.LeftButton)
            QTest.mouseClick(widget.reveal_button, Qt.MouseButton.LeftButton)
            assert got == ["change", "reveal"]
        finally:
            widget.close()


class TestImportFillsBrowserAppearance:
    def _imported(self, qgis_app, project, tmp_path, monkeypatch, before=None):
        from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
        from test_cartograph_dialog import TestPrintSettings

        from nika_onlymap_exporter.core.cartograph_import import read_cartograph_file

        dialog, target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        spec = read_cartograph_file(target).spec
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), "")
        )
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: 0)
        if before:
            before(dialog, spec)
        dialog._cartograph.on_import_cartograph()
        return dialog, spec

    def test_title_attribution_and_theme_land_in_the_fields(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, spec = self._imported(qgis_app, project, tmp_path, monkeypatch)
        try:
            assert spec.title
            cartograph = dialog._cartograph
            name = cartograph.current_layout_name()
            page = dialog.state.page_choice(name)
            assert page.title == spec.title
            assert page.attribution == spec.attribution
            assert page.theme == (spec.theme or "default")
            assert cartograph.title_edit.text() == spec.title
            assert cartograph.attribution_edit.text() == spec.attribution
        finally:
            dialog.close()

    def test_what_was_already_typed_for_that_layout_is_kept(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        def before(dialog, spec):
            # The name the import will pick for the layout.
            from nika_onlymap_exporter.core.layout_builder import unique_layout_name

            name = unique_layout_name(project, spec.title)
            dialog.state.page_choice(name).title = "Typed earlier"

        dialog, _spec = self._imported(
            qgis_app, project, tmp_path, monkeypatch, before=before
        )
        try:
            cartograph = dialog._cartograph
            assert cartograph.title_edit.text() == "Typed earlier"
        finally:
            dialog.close()


class TestOneLayersNote:
    def _notes(self, dialog, text):
        from qgis.PyQt.QtWidgets import QLabel

        return [
            label
            for label in dialog.findChildren(QLabel)
            if label.text() == text and label.isVisibleTo(dialog)
        ]

    def test_one_bar_survives_output_round_trips_with_one_note(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.layer_membership import (
            LayerMembershipBar,
        )

        _add_layout(project, "Map", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()
            _choose_cartograph(dialog)
            for _ in range(3):
                _choose_web(dialog)
                _choose_cartograph(dialog)
            dialog._cartograph.open_section("layers")
            qgis_app.processEvents()
            assert len(dialog.findChildren(LayerMembershipBar)) == 1
            bar = dialog._cartograph.membership.bar
            # One frame: no frame selector to show, and one explanation.
            assert not bar.frame_combo.isVisibleTo(dialog)
            assert bar.frame_combo.count() == 1
            assert bar.note.text()
            assert len(self._notes(dialog, bar.note.text())) == 1
        finally:
            dialog.close()

    def test_the_web_maps_follow_the_panel_line_is_not_shown_in_cartograph(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Map", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()
            _choose_cartograph(dialog)
            assert dialog.layers_intro.isHidden()
            _choose_web(dialog)
            assert not dialog.layers_intro.isHidden()
        finally:
            dialog.close()
