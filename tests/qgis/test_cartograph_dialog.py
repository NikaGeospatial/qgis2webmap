"""The Cartograph (Beta) output's dialog-side behaviour.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen,
same as `tests/qgis/test_dialog.py` next to it. Fixtures are local to this file
rather than added to `tests/qgis/conftest.py`, which a parallel branch is also
touching (see `specs/cartograph/plan.md`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
import types

import pytest

qgis_core = pytest.importorskip("qgis.core")


class FakeIface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


def _dialog(project):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    return MainDialog(FakeIface(), None)


def _add_layout(project, name, maps):
    """A print layout with one `QgsLayoutItemMap` per `(item_id, crs)` in
    `maps`. `item_id` becomes the item's `id()`, so rows are recognisable in
    assertions without walking the layout again."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsLayoutItemMap, QgsPrintLayout
    from qgis.PyQt.QtCore import QRectF

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    for index, (item_id, crs) in enumerate(maps):
        map_item = QgsLayoutItemMap(layout)
        map_item.attemptSetSceneRect(QRectF(0, index * 100, 100, 100))
        map_item.setCrs(QgsCoordinateReferenceSystem(crs))
        map_item.setId(item_id)
        layout.addLayoutItem(map_item)
    project.layoutManager().addLayout(layout)
    return layout


@pytest.fixture
def fake_cartograph_export(monkeypatch):
    """Fakes `core.cartograph_export`'s two halves.

    `calls` records one entry per build: the options it was given and whether
    each half ran on the GUI thread, so a test can tell a burst collapsed into
    one build and that the layout was only touched where Qt allows it. Each
    build also records one fidelity row, so the Fidelity tab has something
    cartograph-shaped to show.
    """
    from qgis.PyQt.QtCore import QThread
    from qgis.PyQt.QtWidgets import QApplication

    import nika_onlymap_exporter.core.cartograph_export as export_module
    import nika_onlymap_exporter.ui.cartograph.controller as controller_module

    calls: list[dict] = []
    should_fail = {"value": False}
    html_text = {"value": "<html>cartograph</html>"}

    def on_gui_thread() -> bool:
        return QThread.currentThread() is QApplication.instance().thread()

    def prepare_cartograph(
        layout,
        project,
        runtime,
        report,
        *,
        frame_modes,
        frame_dpis,
        static_dpi=200,
        title_override=None,
    ):
        report.approximated("Map frame 'Overview'", "Rendered as a static raster.")
        calls.append(
            {
                "frame_modes": dict(frame_modes),
                "frame_dpis": dict(frame_dpis),
                "prepare_on_gui_thread": on_gui_thread(),
            }
        )
        return types.SimpleNamespace(
            spec=types.SimpleNamespace(
                size=None, width=420.0, height=297.0, orientation="landscape"
            )
        )

    def finish_cartograph(prepared, project, report):
        calls[-1]["finish_on_gui_thread"] = on_gui_thread()
        if should_fail["value"]:
            raise RuntimeError("synthetic export failure")
        return html_text["value"]

    monkeypatch.setattr(export_module, "prepare_cartograph", prepare_cartograph)
    monkeypatch.setattr(export_module, "finish_cartograph", finish_cartograph)

    class _Provider:
        version = "fake"

        def load(self):
            return types.SimpleNamespace(version="fake")

    monkeypatch.setattr(
        controller_module, "ensure_runtime", lambda parent=None: _Provider()
    )

    return types.SimpleNamespace(
        calls=calls, should_fail=should_fail, html_text=html_text
    )


def _saved_once(qgis_app, project, fake, tmp_path):
    """A dialog on Cartograph whose first autosave has landed."""
    _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
    dialog = _dialog(project)
    dialog.output_cartograph_button.setChecked(True)
    target = tmp_path / "survey.html"
    dialog._cartograph.file_edit.setText(str(target))
    dialog._cartograph._on_file_edited(str(target))
    assert _wait_for(qgis_app, lambda: target.exists())
    return dialog


def _wait_for(qgis_app, predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


class TestOutputSwitch:
    def test_toggles_tabs_and_corner(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.core.settings import DialogOutput

        dialog = _dialog(project)
        try:
            assert dialog.state.output is DialogOutput.WEB_MAP
            assert dialog.tabs.tabText(0) == "Map"
            assert dialog.tabs.tabText(2) == "Appearance"
            assert dialog.tabs.cornerWidget() is dialog._web_corner

            dialog.output_cartograph_button.setChecked(True)
            assert dialog.state.output is DialogOutput.CARTOGRAPH
            assert dialog.tabs.tabText(0) == "Cartograph"
            assert dialog.tabs.tabText(2) == "Frames"
            assert dialog.tabs.widget(0) is dialog._cartograph_tab_widget
            assert dialog.tabs.widget(2) is dialog._frames_tab_widget
            assert dialog.tabs.cornerWidget() is not dialog._web_corner

            dialog.output_web_button.setChecked(True)
            assert dialog.state.output is DialogOutput.WEB_MAP
            assert dialog.tabs.tabText(0) == "Map"
            assert dialog.tabs.widget(0) is dialog._map_tab_widget
            assert dialog.tabs.cornerWidget() is dialog._web_corner
        finally:
            dialog.close()

    def test_the_installed_corner_is_the_visible_one_after_round_trips(
        self, qgis_app, project
    ) -> None:
        """`setCornerWidget` hides whatever corner it replaces - including
        the same widget, which once left Export's label off-centre."""
        dialog = _dialog(project)
        try:
            dialog.show()
            web = dialog._web_corner
            cartograph = dialog._cartograph.build_corner()
            for output_button, shown, gone in (
                (dialog.output_cartograph_button, cartograph, web),
                (dialog.output_web_button, web, cartograph),
                (dialog.output_cartograph_button, cartograph, web),
            ):
                output_button.setChecked(True)
                qgis_app.processEvents()
                assert dialog.tabs.cornerWidget() is shown
                assert shown.isVisible()
                assert not gone.isVisible()
        finally:
            dialog.close()

    def test_layers_tab_is_untouched_by_the_switch(self, qgis_app, project) -> None:
        """Layers stays at index 1 in both modes - shared between outputs,
        per the plan."""
        dialog = _dialog(project)
        try:
            layers_widget = dialog.tabs.widget(1)
            dialog.output_cartograph_button.setChecked(True)
            assert dialog.tabs.widget(1) is layers_widget
            assert dialog.tabs.tabText(1) == "Layers"
        finally:
            dialog.close()

    def test_cartograph_corner_has_no_export_button(self, qgis_app, project) -> None:
        """Issue #3's document model: the cartograph autosaves, so its corner
        has no Export button at all - unlike the web map's, which keeps one."""
        dialog = _dialog(project)
        try:
            assert hasattr(dialog, "export_button")
            dialog.output_cartograph_button.setChecked(True)
            corner = dialog.tabs.cornerWidget()
            assert corner.findChild(type(dialog.export_button), "export_button") is None
            dialog.output_web_button.setChecked(True)
            assert dialog.tabs.cornerWidget() is dialog._web_corner
            assert hasattr(dialog, "export_button")
        finally:
            dialog.close()

    def test_make_a_cartograph_share_row_flips_the_switch(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import DialogOutput

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        dialog = _dialog(project)
        try:
            dialog.share_actions["cartograph"].trigger()
            assert dialog.state.output is DialogOutput.CARTOGRAPH
        finally:
            dialog.close()


class TestFramesTab:
    def test_two_maps_get_the_crs_based_default(self, qgis_app, project) -> None:
        _add_layout(
            project,
            "Survey",
            [("Overview", "EPSG:4326"), ("Detail", "EPSG:32648")],
        )
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            assert dialog._cartograph.layout_combo.currentText() == "Survey"
            assert dialog._cartograph.frames_tree.topLevelItemCount() == 2

            names = {
                dialog._cartograph.frames_tree.topLevelItem(i).text(0) for i in range(2)
            }
            assert names == {"Overview", "Detail"}

            choices = dialog.state.frame_choices("Survey")
            modes_by_name = {}
            for item_id, choice in choices.items():
                modes_by_name[item_id] = choice.mode
            assert "static" in modes_by_name.values()
            assert "live" in modes_by_name.values()
        finally:
            dialog.close()

    def test_empty_layout_shows_the_empty_note(self, qgis_app, project) -> None:
        _add_layout(project, "Blank", [])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            assert dialog._cartograph.frames_tree.topLevelItemCount() == 0
            assert not dialog._cartograph.frames_empty_note.isHidden()
        finally:
            dialog.close()

    def test_no_layouts_disables_the_combo_and_shows_the_empty_state(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            assert dialog._cartograph.layout_combo.isEnabled() is False
            assert not dialog._cartograph.layout_empty_note.isHidden()
        finally:
            dialog.close()


class TestAutosave:
    def test_a_burst_of_changes_writes_once(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            target = tmp_path / "survey.html"
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(target))
            # The burst: several edits in quick succession, each restarting
            # the debounce timer rather than queuing its own build.
            for _ in range(5):
                cartograph._on_file_edited(str(target))

            assert _wait_for(qgis_app, lambda: target.exists())
            assert len(fake_cartograph_export.calls) == 1
        finally:
            dialog.close()

    def test_writes_are_atomic_and_a_failure_leaves_the_old_file_intact(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            target = tmp_path / "survey.html"
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(qgis_app, lambda: target.exists())
            first_content = target.read_text()
            assert first_content == fake_cartograph_export.html_text["value"]
            # No temp files left lying beside the target.
            assert list(tmp_path.glob("*.tmp")) == []

            fake_cartograph_export.calls.clear()
            fake_cartograph_export.should_fail["value"] = True
            cartograph.schedule_autosave()
            assert _wait_for(qgis_app, lambda: fake_cartograph_export.calls)
            # Give the (failing) job a moment to actually finish.
            assert _wait_for(
                qgis_app,
                lambda: "Could not build" in cartograph.cartograph_status_label.text(),
            )
            assert target.read_text() == first_content
            assert list(tmp_path.glob("*.tmp")) == []
        finally:
            dialog.close()

    def test_status_line_reads_not_saved_yet_before_the_first_build(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            assert dialog._cartograph.cartograph_status_label.text() == "Not saved yet"
        finally:
            dialog.close()

    def test_status_line_reports_size_after_a_save(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            target = tmp_path / "survey.html"
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(qgis_app, lambda: target.exists())
            text = cartograph.cartograph_status_label.text()
            assert text.startswith("Saved")
            assert "B" in text
        finally:
            dialog.close()


class TestBuildThreadsAndJobs:
    def test_an_unexpected_error_lands_on_the_status_line(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        import nika_onlymap_exporter.core.cartograph_export as export_module

        def explode(*_args, **_kwargs):
            raise RuntimeError("a layout item nobody anticipated")

        monkeypatch.setattr(export_module, "prepare_cartograph", explode)
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(tmp_path / "survey.html"))
            cartograph._run_autosave()
            assert "nobody anticipated" in cartograph.cartograph_status_label.text()
            assert not cartograph.busy
        finally:
            dialog.close()

    def test_the_layout_is_read_on_the_gui_thread_and_layers_off_it(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            (call,) = fake_cartograph_export.calls
            assert call["prepare_on_gui_thread"] is True
            assert call["finish_on_gui_thread"] is False
        finally:
            dialog.close()

    def test_each_frames_dpi_reaches_the_export(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(
            project, "Survey", [("Main", "EPSG:32648"), ("Inset", "EPSG:32648")]
        )
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            cartograph = dialog._cartograph
            rows = list(cartograph._frame_rows.items())
            rows[0][1]["dpi"].setCurrentIndex(rows[0][1]["dpi"].findData(150))
            rows[1][1]["dpi"].setCurrentIndex(rows[1][1]["dpi"].findData(300))
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(qgis_app, lambda: target.exists())

            assert fake_cartograph_export.calls[-1]["frame_dpis"] == {
                rows[0][0]: 150,
                rows[1][0]: 300,
            }
        finally:
            dialog.close()

    def test_no_build_starts_while_a_web_map_job_runs(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """Two jobs walking the same layers is what takes QGIS down."""
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            target = tmp_path / "survey.html"
            dialog._job = object()  # a web-map job in flight
            dialog._cartograph.file_edit.setText(str(target))
            dialog._cartograph._on_file_edited(str(target))
            _wait_for(qgis_app, lambda: False, timeout=1.5)
            assert fake_cartograph_export.calls == []

            dialog._job = None
            assert _wait_for(qgis_app, lambda: target.exists())
        finally:
            dialog._job = None
            dialog.close()

    def test_a_web_map_job_waits_for_a_cartograph_build(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            dialog._cartograph._job = object()  # a cartograph build in flight
            assert dialog._start_job(lambda _p: None, lambda _r: None, "x") is False
        finally:
            dialog._cartograph._job = None
            dialog.close()


class TestFidelityFollowsTheOutput:
    def test_switching_back_to_the_web_map_drops_the_cartographs_report(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph_summary = dialog.fidelity_panel._summary
            cartograph_message = dialog.fidelity_strip.message
            assert cartograph_summary is not None
            assert "1" in cartograph_message.text  # one row changed

            dialog.output_web_button.setChecked(True)
            assert dialog.fidelity_panel._summary is not cartograph_summary
            assert dialog.fidelity_strip.message != cartograph_message

            dialog.output_cartograph_button.setChecked(True)
            assert dialog.fidelity_panel._summary is cartograph_summary
            assert dialog.fidelity_strip.message == cartograph_message
        finally:
            dialog.close()

    def test_web_map_staleness_does_not_overwrite_the_cartographs_strip(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            shown = dialog.fidelity_strip.label.text()
            dialog._fidelity_is_stale = True  # a layer changed in QGIS
            assert dialog.fidelity_strip.label.text() == shown
        finally:
            dialog.close()

    def test_a_web_map_check_starting_off_screen_leaves_the_strip_alone(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """A live preview can still start a web-map read while Cartograph is
        on screen; its "Checking..." must not replace the cartograph's line."""
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            shown = dialog.fidelity_strip.message
            headline = dialog.fidelity_panel.headline.text()
            dialog._begin_fidelity_check()
            assert dialog.fidelity_strip.message == shown
            assert dialog.fidelity_panel.headline.text() == headline
        finally:
            dialog.close()

    def test_opening_fidelity_on_cartograph_never_reads_the_web_map(
        self, qgis_app, project, monkeypatch
    ) -> None:
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            reads = []
            monkeypatch.setattr(
                dialog, "_ensure_export", lambda *a, **k: reads.append(a)
            )
            dialog.tabs.setCurrentWidget(dialog._fidelity_page)
            assert reads == []
        finally:
            dialog.close()


class TestCartographShareMenu:
    def _ready_dialog(self, project, tmp_path):
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        dialog.output_cartograph_button.setChecked(True)
        cartograph = dialog._cartograph
        target = tmp_path / "survey.html"
        target.write_text("<html>pre-existing</html>")
        cartograph.file_edit.setText(str(target))
        cartograph._update_share_enabled()
        return dialog, target

    def test_rows_needing_the_file_are_gated_on_it(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            actions = dialog._cartograph.cartograph_share_actions
            for key in (
                "copy_path",
                "reveal",
                "open_browser",
                "export_pdf",
                "export_png",
            ):
                assert not actions[key].isEnabled(), key

            dialog._cartograph.file_edit.setText(str(tmp_path / "survey.html"))
            (tmp_path / "survey.html").write_text("<html></html>")
            dialog._cartograph._update_share_enabled()
            for key in (
                "copy_path",
                "reveal",
                "open_browser",
                "export_pdf",
                "export_png",
            ):
                assert actions[key].isEnabled(), key
        finally:
            dialog.close()

    def test_export_qpt_is_gated_on_a_selected_layout_not_the_file(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            assert not dialog._cartograph.cartograph_share_actions[
                "export_qpt"
            ].isEnabled()
            _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
            dialog._cartograph.refresh_layouts()
            assert dialog._cartograph.cartograph_share_actions["export_qpt"].isEnabled()
        finally:
            dialog.close()

    def test_the_hand_off_rows_open_the_dialog_on_the_saved_file(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import handoff_dialog

        shown = []
        monkeypatch.setattr(
            handoff_dialog.HandoffDialog,
            "exec",
            lambda self: shown.append(self.context) or 0,
        )
        dialog, target = self._ready_dialog(project, tmp_path)
        try:
            actions = dialog._cartograph.cartograph_share_actions
            for key in ("enhance", "critique"):
                assert actions[key].isEnabled(), key
                actions[key].trigger()
            assert [(c.output, c.file_path, c.task, c.source_name) for c in shown] == [
                ("cartograph", target, "enhance", "Survey"),
                ("cartograph", target, "critique", "Survey"),
            ]
        finally:
            dialog.close()

    def test_hand_off_rows_wait_for_a_saved_file(
        self, qgis_app, project, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            dialog._cartograph.file_edit.setText(str(tmp_path / "missing.html"))
            dialog._cartograph._update_share_enabled()
            actions = dialog._cartograph.cartograph_share_actions
            for key in ("enhance", "critique"):
                assert not actions[key].isEnabled(), key
        finally:
            dialog.close()

    def test_copy_file_path_puts_the_path_on_the_clipboard(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, target = self._ready_dialog(project, tmp_path)
        try:
            from qgis.PyQt.QtGui import QGuiApplication

            dialog._cartograph.on_copy_path()
            assert QGuiApplication.clipboard().text() == str(target)
        finally:
            dialog.close()

    def test_open_in_browser_and_exports_open_a_url(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, _target = self._ready_dialog(project, tmp_path)
        try:
            opened = []
            monkeypatch.setattr(
                "nika_onlymap_exporter.ui.cartograph.controller.QDesktopServices.openUrl",
                lambda url: opened.append(url.toString()),
            )
            dialog._cartograph.on_open_in_browser()
            dialog._cartograph.on_export_pdf()
            dialog._cartograph.on_export_png()
            assert len(opened) == 3
            assert any("print=1" in url for url in opened)
            assert any("export=png" in url and "dpi=300" in url for url in opened)
        finally:
            dialog.close()

    def test_reveal_opens_the_parent_folder(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, target = self._ready_dialog(project, tmp_path)
        try:
            opened = []
            monkeypatch.setattr(
                "nika_onlymap_exporter.ui.cartograph.controller.QDesktopServices.openUrl",
                lambda url: opened.append(url.toLocalFile()),
            )
            dialog._cartograph.on_reveal()
            assert opened == [str(target.parent)]
        finally:
            dialog.close()


class TestRealAutosave:
    """No fakes: the dialog, the real export and the real runtime.

    The faked tests above proved the controller's plumbing but stood in for
    both the runtime and the export, which is how a provider got passed where
    the export needed the loaded bundle and nobody noticed.
    """

    def test_a_real_layout_autosaves_a_real_cartograph(
        self, qgis_app, project, tmp_path
    ) -> None:
        import os

        from cartograph_fixtures import build_survey_layout

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR (see CONTRIBUTING.md)")
        survey = build_survey_layout(project, tmp_path)
        project.layoutManager().addLayout(survey.layout)
        dialog = _dialog(project)
        try:
            dialog.output_cartograph_button.setChecked(True)
            cartograph = dialog._cartograph
            cartograph.layout_combo.setCurrentText(survey.layout.name())
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))

            assert _wait_for(qgis_app, lambda: target.exists(), timeout=60)
            html = target.read_text(encoding="utf-8")
            assert "<om-cartograph" in html
            assert html.count("<om-frame") == 2
            assert cartograph._last_error == ""
            assert "Saved" in cartograph.cartograph_status_label.text()
        finally:
            dialog.close()
