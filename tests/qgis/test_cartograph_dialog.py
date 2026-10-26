"""The Cartograph (Beta) output's dialog-side behaviour.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen,
same as `tests/qgis/test_dialog.py` next to it. Fixtures are local to this file
rather than added to `tests/qgis/conftest.py`, which a parallel branch is also
touching (see `specs/cartograph/plan.md`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")


class FakeIface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


def _dialog(project):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    return MainDialog(FakeIface(), None)


def _choose_cartograph(dialog) -> None:
    """Pick Cartograph from the title's menu, as a click on its row does."""
    from nika_onlymap_exporter.core.settings import DialogOutput

    dialog.output_switcher.actions_by_output[DialogOutput.CARTOGRAPH].trigger()


def _click_new_from_template(dialog) -> None:
    """Click "New from template..." the way a user does: on the button's
    label side, not its menu arrow."""
    from qgis.PyQt.QtCore import QPoint, Qt
    from qgis.PyQt.QtTest import QTest

    button = dialog._cartograph.new_from_template_button
    QTest.mouseClick(
        button, Qt.MouseButton.LeftButton, pos=QPoint(10, button.height() // 2)
    )


def _choose_web(dialog) -> None:
    from nika_onlymap_exporter.core.settings import DialogOutput

    dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()


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


@pytest.fixture(autouse=True)
def picked_template(monkeypatch):
    """The template picker is modal; a test that reaches it would wait for a
    click forever. It answers Classic on A4 fitted to the map unless a test
    sets `choice` (`None` is Cancel), and records each time it was shown."""
    from nika_onlymap_exporter.ui.cartograph import template_picker

    class Picker:
        def __init__(self) -> None:
            self.choice: tuple[str, str, str] | None = ("classic", "A4", "auto")
            self.shown = 0
            self.previews: list[object] = []
            self.canvas_keys: list[str] = []
            self.planners: list[object] = []

        def ask(self, render, parent=None, project=None, canvas_key="", planner=None):
            self.shown += 1
            self.previews.append(render("story-map", "A4", "auto", 120))
            self.canvas_keys.append(canvas_key)
            self.planners.append(planner)
            return self.choice

    picker = Picker()
    monkeypatch.setattr(template_picker, "ask_template", picker.ask)
    return picker


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
        export_dpi=None,
        title_override=None,
    ):
        report.approximated("Map frame 'Overview'", "Rendered as a static raster.")
        calls.append(
            {
                "frame_modes": dict(frame_modes),
                "frame_dpis": dict(frame_dpis),
                "export_dpi": export_dpi,
                "prepare_on_gui_thread": on_gui_thread(),
            }
        )
        from nika_onlymap_exporter.core.cartograph_ir import CartographSpec
        from nika_onlymap_exporter.writers.cartograph_writer import (
            CartographRuntimeBundle,
        )

        return export_module.PreparedCartograph(
            spec=CartographSpec(
                cartograph_id="fake", width=420.0, height=297.0, orientation="landscape"
            ),
            live_frames=(),
            bundle=CartographRuntimeBundle(cartograph_js=b"", cartograph_css=""),
        )

    def finish_cartograph_full(
        prepared, project, report, *, writer=None, layer_settings=None
    ):
        calls[-1]["finish_on_gui_thread"] = on_gui_thread()
        calls[-1]["writer"] = writer
        if should_fail["value"]:
            raise RuntimeError("synthetic export failure")
        return export_module.FinishedCartograph(html=html_text["value"], projects=())

    monkeypatch.setattr(export_module, "prepare_cartograph", prepare_cartograph)
    monkeypatch.setattr(export_module, "finish_cartograph_full", finish_cartograph_full)

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
    _choose_cartograph(dialog)
    target = tmp_path / "survey.html"
    dialog._cartograph.file_edit.setText(str(target))
    dialog._cartograph._on_file_edited(str(target))
    assert _wait_for(qgis_app, lambda: target.exists())
    return dialog


def _drawn(qgis_app, preview):
    """The image of a preview, once a background one (`PendingPreview`) is
    done; `None` if it was not drawn."""
    from qgis.PyQt.QtGui import QImage

    if preview is None or isinstance(preview, QImage):
        return preview
    images: list[object] = []
    preview.finished.connect(images.append)
    assert _wait_for(qgis_app, lambda: images, timeout=60.0)
    return images[0]


def _wait_for(qgis_app, predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


class TestOutputSwitch:
    def test_toggles_the_screen_and_corner(self, qgis_app, project) -> None:
        """The web map is tabs; the cartograph is its workspace in their place."""
        from nika_onlymap_exporter.core.settings import DialogOutput

        dialog = _dialog(project)
        try:
            assert dialog.state.output is DialogOutput.WEB_MAP
            assert dialog.views.currentWidget() is dialog.tabs
            assert dialog.tabs.tabText(0) == "Map"
            assert dialog.tabs.tabText(2) == "Appearance"
            assert dialog.active_actions() is dialog._web_corner

            _choose_cartograph(dialog)
            assert dialog.state.output is DialogOutput.CARTOGRAPH
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            assert dialog.active_actions() is not dialog._web_corner

            _choose_web(dialog)
            assert dialog.state.output is DialogOutput.WEB_MAP
            assert dialog.views.currentWidget() is dialog.tabs
            assert dialog.tabs.tabText(0) == "Map"
            assert dialog.tabs.widget(0) is dialog._map_tab_widget
            assert dialog.active_actions() is dialog._web_corner
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
            for choose, shown, gone in (
                (_choose_cartograph, cartograph, web),
                (_choose_web, web, cartograph),
                (_choose_cartograph, cartograph, web),
            ):
                choose(dialog)
                qgis_app.processEvents()
                assert dialog.active_actions() is shown
                assert shown.isVisible()
                assert not gone.isVisible()
        finally:
            dialog.close()

    def test_the_title_names_the_output_and_its_menu_switches_it(
        self, qgis_app, project
    ) -> None:
        """`Web map ▾` is the heading; a row in its menu is the switch."""
        from nika_onlymap_exporter.core.settings import DialogOutput
        from nika_onlymap_exporter.ui.output_switcher import _OutputRow

        dialog = _dialog(project)
        try:
            dialog.show()
            switcher = dialog.output_switcher
            assert switcher.text() == "Web map"
            assert switcher.property("lineIcon") == "web"
            assert not switcher.beta_chip.isVisibleTo(dialog)
            rows = switcher.menu().findChildren(_OutputRow)
            assert [r.name.text() for r in rows] == ["Web map", "Cartograph"]
            assert all(r.description.text() for r in rows)
            assert [r.tick.text() for r in rows] == ["✓", ""]

            rows[1]._choose()
            assert dialog.state.output is DialogOutput.CARTOGRAPH
            assert switcher.text() == "Cartograph"
            assert switcher.property("lineIcon") == "page"
            assert switcher.beta_chip.isVisibleTo(dialog)
            assert [r.tick.text() for r in rows] == ["", "✓"]
        finally:
            dialog.close()

    def test_a_menu_row_chooses_by_keyboard(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        from nika_onlymap_exporter.core.settings import DialogOutput
        from nika_onlymap_exporter.ui.output_switcher import _OutputRow

        dialog = _dialog(project)
        try:
            row = dialog.output_switcher.menu().findChildren(_OutputRow)[1]
            QTest.keyClick(row, Qt.Key.Key_Return)
            assert dialog.state.output is DialogOutput.CARTOGRAPH
        finally:
            dialog.close()

    @pytest.mark.parametrize("title", ["Map", "Layers", "Appearance", "Fidelity"])
    def test_a_round_trip_returns_to_the_same_web_tab(
        self, qgis_app, project, title
    ) -> None:
        """Web map, Cartograph, web map: the tab that was open is open again,
        even Layers or Fidelity, which spend the trip in the cartograph's
        panel."""
        dialog = _dialog(project)
        try:
            page = dialog._pages[title]
            dialog.tabs.setCurrentWidget(page)
            _choose_cartograph(dialog)
            _choose_web(dialog)
            assert dialog.tabs.currentWidget() is page
        finally:
            dialog.close()

    def test_the_actions_are_in_the_header_not_the_tab_bar(
        self, qgis_app, project
    ) -> None:
        from qgis.PyQt.QtWidgets import QToolButton

        dialog = _dialog(project)
        try:
            assert dialog.tabs.cornerWidget() is None
            header = dialog.output_switcher.parentWidget()
            assert dialog._web_corner.parentWidget() is header
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph.build_corner()
            assert cartograph.parentWidget() is header
            buttons = cartograph.findChildren(QToolButton)
            # Styled as the web map's: line icons, Share's one chevron.
            assert all(b.property("role") == "corner" for b in buttons)
            assert all(b.property("lineIcon") for b in buttons)
            share = dialog._cartograph.cartograph_share_button
            assert share.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
            assert "open_browser" in dialog._cartograph.cartograph_share_actions
            assert dialog._cartograph.accessibility_button.accessibleName()
        finally:
            dialog.close()

    def test_a_project_saved_on_cartograph_opens_on_it(self, qgis_app, project) -> None:
        """It used to fail to open: the cartograph drew its report before the
        dialog had built the strip the report goes on."""
        from nika_onlymap_exporter.core.settings import (
            DialogOutput,
            load_state,
            save_state,
        )

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        state = load_state(project)
        state.output = DialogOutput.CARTOGRAPH
        save_state(project, state)
        dialog = _dialog(project)
        try:
            assert dialog.output_switcher.output() is DialogOutput.CARTOGRAPH
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            assert dialog.fidelity_strip.label.text()
            cartograph = dialog._cartograph
            checks = cartograph._tab_keys.index("checks")
            assert cartograph.panel_tabs.tabToolTip(checks)
        finally:
            dialog.close()

    def test_layers_is_one_widget_shared_by_both_outputs(
        self, qgis_app, project
    ) -> None:
        """Both outputs configure layers the same way, per the plan: the web
        map's Layers tab becomes the cartograph's Layers section, the same
        widget, and comes back to index 1."""
        dialog = _dialog(project)
        try:
            layers_widget = dialog._layers_page
            _choose_cartograph(dialog)
            section = dialog._cartograph.layers_section
            assert layers_widget.parentWidget() is section.body
            _choose_web(dialog)
            assert dialog.tabs.widget(1) is dialog._layers_scroll
            assert dialog._layers_scroll.widget() is layers_widget
            assert dialog.tabs.tabText(1) == "Layers"
        finally:
            dialog.close()

    def test_cartograph_corner_has_no_export_button(self, qgis_app, project) -> None:
        """Issue #3's document model: the cartograph autosaves, so its corner
        has no Export button at all - unlike the web map's, which keeps one."""
        dialog = _dialog(project)
        try:
            assert hasattr(dialog, "export_button")
            _choose_cartograph(dialog)
            corner = dialog.active_actions()
            assert corner.findChild(type(dialog.export_button), "export_button") is None
            _choose_web(dialog)
            assert dialog.active_actions() is dialog._web_corner
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
    def test_two_maps_get_their_defaults(self, qgis_app, project) -> None:
        layout = _add_layout(
            project,
            "Survey",
            [("Overview", "EPSG:4326"), ("Detail", "EPSG:32648")],
        )
        # A rotated UTM map cannot draw in its own CRS, so it starts static.
        layout.itemById("Detail").setMapRotation(15)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert cartograph.layout_combo.currentText() == "Survey"
            assert len(cartograph._frame_rows) == 2

            names = {
                cartograph.frames_list.card(item_id).name_label.text()
                for item_id in cartograph._frame_rows
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
            _choose_cartograph(dialog)
            assert dialog._cartograph._frame_rows == {}
            assert not dialog._cartograph.frames_list.empty_note.isHidden()
        finally:
            dialog.close()

    def test_no_layouts_shows_the_empty_state(self, qgis_app, project) -> None:
        """No layout to pick: the list is empty, the preview says so and offers
        "New from template..." clearly, and the settings that belong to a
        layout are hidden rather than left to type into."""
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            combo = cartograph.layout_combo
            assert combo.currentText() == ""
            assert combo.count() == 0
            template = cartograph.page_preview.template_button
            assert template.text() == "New from template..."
            assert template.isVisibleTo(cartograph.page_preview)
            assert not cartograph.layout_empty_note.isHidden()
            assert not cartograph.page_preview.showing_page()
            for section in cartograph.sections.values():
                assert not section.isVisibleTo(cartograph.workspace)
            assert not cartograph.title_edit.isVisibleTo(cartograph.workspace)
            assert cartograph.file_edit.isHidden()
        finally:
            dialog.close()


class TestAutosave:
    def test_a_burst_of_changes_writes_once(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
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
            _choose_cartograph(dialog)
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

    def test_a_burst_of_restyles_refreshes_the_preview_once(
        self, qgis_app, project, fake_cartograph_export
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            layout = cartograph._current_layout
            assert layout is not None
            refreshes: list[None] = []
            layout.refreshed.connect(lambda: refreshes.append(None))
            for _ in range(5):
                cartograph.on_project_content_changed()
            assert refreshes == []
            assert _wait_for(qgis_app, lambda: refreshes)
            qgis_app.processEvents()
            assert len(refreshes) == 1
        finally:
            dialog.close()

    def test_status_line_reads_not_saved_yet_before_the_first_build(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            assert dialog._cartograph.cartograph_status_label.text().startswith(
                "Not saved yet"
            )
        finally:
            dialog.close()

    def test_status_line_reports_size_after_a_save(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
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

    def test_status_line_is_honest_while_a_save_is_pending_or_running(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        import threading

        import nika_onlymap_exporter.core.cartograph_export as export_module

        gate = threading.Event()
        gate.set()
        real = export_module.finish_cartograph_full

        def gated(*args, **kwargs):
            gate.wait(20)
            return real(*args, **kwargs)

        monkeypatch.setattr(export_module, "finish_cartograph_full", gated)
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph = dialog._cartograph
            label = cartograph.cartograph_status_label
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            cartograph._autosave_timer.stop()
            cartograph._update_status_label()
            assert label.text().startswith("Saved")

            gate.clear()
            cartograph.schedule_autosave()  # an edit, still in its debounce
            assert label.text() == "Changes pending"
            cartograph._autosave_timer.stop()
            cartograph._run_autosave()
            assert cartograph._job is not None
            assert label.text() == "Saving the cartograph..."
            qgis_app.processEvents()
            cartograph._on_status_tick()  # the 1 s refresh must not undo it
            assert label.text() == "Saving the cartograph..."

            gate.set()
            assert _wait_for(qgis_app, lambda: label.text().startswith("Saved"))
            for text in ("Changes pending", "Saving the cartograph..."):
                assert text not in label.text()
            assert "project" not in label.text().lower()
        finally:
            gate.set()
            dialog.close()


class TestBuildThreadsAndJobs:
    def test_the_users_licence_key_reaches_the_build(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        """The dialog's writer, which carries the key, not a free default."""
        import nika_onlymap_exporter.ui.main_dialog as main_dialog_module

        monkeypatch.setattr(
            main_dialog_module.MainDialog, "_license_key", lambda self: "paid-key"
        )
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            writer = fake_cartograph_export.calls[-1]["writer"]
            assert writer is not None
            assert writer.license_policy.license_key == "paid-key"
        finally:
            dialog.close()

    def test_an_open_browser_preview_follows_the_layout_and_each_save(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        """The preview server shows the layout on screen: switching layouts
        and saving an edit each publish the saved file again."""
        _add_layout(project, "A", [("Map", "EPSG:4326")])
        _add_layout(project, "B", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            monkeypatch.setattr(cartograph, "_open_url", lambda url: None)
            cartograph.layout_combo.setCurrentText("A")
            a = tmp_path / "a.html"
            cartograph.file_edit.setText(str(a))
            cartograph._on_file_edited(str(a))
            assert _wait_for(qgis_app, lambda: a.exists())
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            cartograph.on_open_in_browser()
            server = cartograph._preview_server
            assert server is not None
            assert server.published()[0] == a.resolve()
            before = server.revision

            fake_cartograph_export.html_text["value"] = "<html>A edited</html>"
            cartograph.schedule_autosave()
            assert _wait_for(qgis_app, lambda: server.revision > before)
            assert server.published()[0] == a.resolve()

            cartograph.layout_combo.setCurrentText("B")
            b = cartograph._current_path()
            assert _wait_for(qgis_app, lambda: b.exists())
            assert _wait_for(qgis_app, lambda: server.published()[0] == b.resolve())
            assert cartograph._preview_server is server
        finally:
            dialog.close()

    def test_a_build_landing_after_a_switch_saves_only_its_own_layout(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        """Layout A's save finishing once B is selected writes A's file and
        nothing of B's: not B's status line, Checks, or a print A asked for."""
        import threading

        import nika_onlymap_exporter.core.cartograph_export as export_module

        gate = threading.Event()
        gate.set()
        finish = export_module.finish_cartograph_full

        def gated(*args, **kwargs):
            gate.wait(20)
            return finish(*args, **kwargs)

        monkeypatch.setattr(export_module, "finish_cartograph_full", gated)
        _add_layout(project, "A", [("Map", "EPSG:4326")])
        _add_layout(project, "B", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            cartograph.layout_combo.setCurrentText("A")
            a = tmp_path / "a.html"
            cartograph.file_edit.setText(str(a))
            cartograph._on_file_edited(str(a))
            assert _wait_for(qgis_app, lambda: a.exists())
            assert _wait_for(
                qgis_app, lambda: not cartograph._autosave_timer.isActive()
            )

            gate.clear()
            fake_cartograph_export.html_text["value"] = "<html>A again</html>"
            cartograph._run_autosave()
            assert cartograph._job is not None
            cartograph.on_export_png()  # waits for A's save, then is dropped
            cartograph.layout_combo.setCurrentText("B")
            b_path = cartograph._current_path()
            cartograph._autosave_timer.stop()  # B's own save is not the point
            gate.set()
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            cartograph._autosave_timer.stop()

            assert a.read_text() == "<html>A again</html>"
            assert not b_path.exists()
            assert cartograph._last_saved_at is None
            assert cartograph._last_summary is None
            assert cartograph.cartograph_status_label.text().startswith("Not saved")
            assert opened == []
            # B's own save does not run the export asked of A either.
            cartograph._run_autosave()
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            assert b_path.exists()
            assert opened == []
        finally:
            gate.set()
            dialog.close()

    def test_an_export_right_after_an_edit_opens_the_saved_edit(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph = dialog._cartograph
            target = cartograph._current_path()
            opened = []
            monkeypatch.setattr(
                cartograph,
                "_open_url",
                lambda url: opened.append((url.query(), target.read_text())),
            )
            fake_cartograph_export.html_text["value"] = "<html>edited</html>"
            cartograph.schedule_autosave()  # an edit, still in its debounce
            cartograph.on_export_png()
            assert opened == []
            assert "opens when saved" in dialog.status_label.text()
            assert _wait_for(qgis_app, lambda: opened)
            assert opened == [("export=png", "<html>edited</html>")]
            assert "opens when saved" not in dialog.status_label.text()
        finally:
            dialog.close()

    def test_print_settings_export_waits_past_a_build_already_running(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        """The build in flight predates the new settings; only the save that
        applies them may be printed."""
        import threading

        import nika_onlymap_exporter.core.cartograph_export as export_module
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.ui.cartograph import print_settings_dialog

        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        gate = threading.Event()
        finish = export_module.finish_cartograph_full

        def gated(*args, **kwargs):
            gate.wait(20)
            return finish(*args, **kwargs)

        monkeypatch.setattr(export_module, "finish_cartograph_full", gated)
        try:
            cartograph = dialog._cartograph
            calls = fake_cartograph_export.calls
            opened = []
            monkeypatch.setattr(
                cartograph,
                "_open_url",
                lambda url: opened.append((url.query(), len(calls))),
            )
            monkeypatch.setattr(
                print_settings_dialog,
                "ask_print_settings",
                lambda *a, **k: (
                    print_settings_dialog.ACTION_EXPORT,
                    PrintSettings(bleed="5mm"),
                ),
            )
            builds = len(calls)
            cartograph._run_autosave()  # in flight, without the bleed
            cartograph.on_print_settings()
            gate.set()
            assert _wait_for(qgis_app, lambda: opened)
            # Opened by the second build, the one started after the settings.
            assert opened == [("print=1", builds + 2)]
        finally:
            gate.set()
            dialog.close()

    def test_a_failed_save_cancels_the_page_waiting_on_it(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph = dialog._cartograph
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            fake_cartograph_export.should_fail["value"] = True
            cartograph.schedule_autosave()
            cartograph.on_export_pdf()
            assert _wait_for(
                qgis_app,
                lambda: (
                    cartograph.cartograph_status_label.text().startswith(
                        "Could not build"
                    )
                    or "Could not build" in cartograph.cartograph_status_label.text()
                ),
            )
            fake_cartograph_export.should_fail["value"] = False
            builds = len(fake_cartograph_export.calls)
            cartograph.schedule_autosave()  # a later, unrelated save
            assert _wait_for(
                qgis_app, lambda: len(fake_cartograph_export.calls) > builds
            )
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            assert opened == []
        finally:
            dialog.close()

    def test_a_failed_disk_write_never_opens_the_older_file(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        """The disk refusing the save, not the worker: the waiting export is
        dropped, a second click saves again rather than opening the old file,
        and once the disk takes it the export opens the new one."""
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph = dialog._cartograph
            target = cartograph._current_path()
            write = cartograph._write_atomic
            disk = {"full": True}

            def write_or_fail(path, content):
                if disk["full"]:
                    raise OSError("No space left on device")
                write(path, content)

            monkeypatch.setattr(cartograph, "_write_atomic", write_or_fail)
            opened = []
            monkeypatch.setattr(
                cartograph,
                "_open_url",
                lambda url: opened.append((url.query(), target.read_text())),
            )
            status = cartograph.cartograph_status_label

            def settled():
                return cartograph._job is None and not (
                    cartograph._autosave_timer.isActive()
                )

            fake_cartograph_export.html_text["value"] = "<html>edited</html>"
            cartograph.schedule_autosave()
            cartograph.on_export_png()  # waits for the save, which fails
            assert _wait_for(qgis_app, lambda: "No space left" in status.text())
            assert _wait_for(qgis_app, settled)
            assert cartograph._open_after_save == []
            assert "opens when saved" not in dialog.status_label.text()

            cartograph.on_export_png()  # idle, but the file is the old one
            assert opened == []
            assert _wait_for(qgis_app, settled)
            assert "No space left" in status.text()
            assert opened == []

            disk["full"] = False
            cartograph.on_export_png()
            assert _wait_for(qgis_app, lambda: opened)
            assert opened == [("export=png", "<html>edited</html>")]
            cartograph.on_export_png()  # current now: opens at once
            assert len(opened) == 2
        finally:
            dialog.close()

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
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(tmp_path / "survey.html"))
            cartograph._autosave_timer.stop()  # as when the debounce fires
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
            _choose_cartograph(dialog)
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
            # No print settings saved: the runtime's own 300 dpi, which a
            # static frame is never drawn below.
            assert fake_cartograph_export.calls[-1]["export_dpi"] == 300
        finally:
            dialog.close()

    def test_no_build_starts_while_a_web_map_job_runs(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """Two jobs walking the same layers is what takes QGIS down."""
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
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

            _choose_web(dialog)
            assert dialog.fidelity_panel._summary is not cartograph_summary
            assert dialog.fidelity_strip.message != cartograph_message

            _choose_cartograph(dialog)
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
            _choose_cartograph(dialog)
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
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        target = tmp_path / "survey.html"
        target.write_text("<html>pre-existing</html>")
        cartograph.file_edit.setText(str(target))
        # Saved and idle: no edit is waiting to be written, so the share
        # actions open the file now rather than after a save.
        cartograph._autosave_timer.stop()
        cartograph._edits_saved = cartograph._edits_made
        cartograph._update_share_enabled()
        return dialog, target

    def test_rows_needing_the_file_are_gated_on_it(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
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

    def test_export_and_share_lists_outputs_before_file_actions(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert cartograph.cartograph_share_button.text() == "Share"
            rows = [
                a.text()
                for a in cartograph.cartograph_share_button.menu().actions()
                if not a.isSeparator()
            ]
            assert rows[:7] == [
                "Export PDF...",
                "Export PNG...",
                "Export QGIS layout template (.qpt)...",
                "Print settings...",
                "Open in browser",
                "Copy file path",
                rows[6],
            ]
            assert rows[7:] == [
                "Prepare enhancement prompt...",
                "Prepare critique prompt...",
            ]
        finally:
            dialog.close()

    def test_export_qpt_is_gated_on_a_selected_layout_not_the_file(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            assert not dialog._cartograph.cartograph_share_actions[
                "export_qpt"
            ].isEnabled()
            _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
            dialog._cartograph.refresh_layouts()
            assert dialog._cartograph.cartograph_share_actions["export_qpt"].isEnabled()
        finally:
            dialog.close()

    def test_the_hand_off_rows_are_greyed_out_and_hosting_is_not_offered(
        self, qgis_app, project, tmp_path
    ) -> None:
        """The hand-off is not ready for cartographs, so its rows stay greyed
        out even with the file saved; hosting is for web maps only."""
        dialog, _target = self._ready_dialog(project, tmp_path)
        try:
            actions = dialog._cartograph.cartograph_share_actions
            assert actions["open_browser"].isEnabled()
            for key in ("enhance", "critique"):
                assert not actions[key].isEnabled(), key
                assert actions[key].toolTip() == "Not available for cartographs yet"
            assert "publish" not in actions
            menu = dialog._cartograph.cartograph_share_button.menu()
            labels = [action.text() for action in menu.actions()]
            assert not any("hosting" in label.lower() for label in labels), labels
        finally:
            dialog.close()

    def test_hand_off_rows_wait_for_a_saved_file(
        self, qgis_app, project, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
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
            assert any("export=png" in url for url in opened)
        finally:
            dialog.close()

    @pytest.mark.parametrize(
        "name",
        [
            "plan two.html",
            "plan \u00fcber \u5730\u56fe.html",
            "plan #2.html",
            "plan 100%.html",
            pytest.param(
                "plan ?.html",
                marks=pytest.mark.skipif(
                    sys.platform == "win32", reason="Windows forbids ? in names"
                ),
            ),
        ],
    )
    def test_every_action_reaches_the_served_file(
        self, qgis_app, project, tmp_path, monkeypatch, name
    ) -> None:
        import urllib.request

        dialog, _target = self._ready_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            target = tmp_path / name
            target.write_text("<html>plan</html>", encoding="utf-8")
            cartograph.file_edit.setText(str(target))
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            cartograph.on_open_in_browser()
            cartograph.on_export_png()
            cartograph._open_all_pages("zip")
            assert [url.query() for url in opened] == [
                "",
                "export=png",
                "atlas=zip",
            ]
            for url in opened:
                assert url.scheme() == "http"
                assert url.path() == f"/{name}"
                assert not url.hasFragment()
                encoded = bytes(url.toEncoded()).decode("ascii")
                with urllib.request.urlopen(encoded, timeout=10) as response:
                    assert response.read() == b"<html>plan</html>"
        finally:
            dialog.close()

    def test_every_action_keeps_its_query_without_the_server(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph import controller

        def refuse(_self) -> str:
            raise OSError("no port")

        monkeypatch.setattr(controller.PreviewServer, "start", refuse)
        dialog, target = self._ready_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            cartograph.on_open_in_browser()
            cartograph.on_export_pdf()
            cartograph.on_export_png()
            cartograph._open_cvd_preview("deutan")
            cartograph._open_all_pages("zip")
            # Twice: a server that failed once is not reused unstarted.
            cartograph._open_all_pages("print")
            assert [url.toLocalFile() for url in opened] == [str(target)] * 6
            assert [url.query() for url in opened] == [
                "",
                "print=1",
                "export=png",
                "cvd=deutan",
                "atlas=zip",
                "atlas=print",
            ]
            status = dialog.status_label.text()
            assert "Could not start the local preview server (no port)" in status
            assert "127.0.0.1" in status
        finally:
            dialog.close()

    def test_reveal_before_the_folder_exists_opens_the_nearest_one(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, _target = self._ready_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            cartograph.file_edit.setText(str(tmp_path / "not yet" / "x.html"))
            opened = []
            monkeypatch.setattr(
                "nika_onlymap_exporter.ui.cartograph.controller.QDesktopServices.openUrl",
                lambda url: opened.append(url.toLocalFile()),
            )
            cartograph.on_reveal()
            assert opened == [str(tmp_path)]
            assert dialog.status_label.text() == f"Opened {tmp_path}"
        finally:
            dialog.close()

    def test_the_pdf_tooltip_names_the_route_print_settings_chose(
        self, qgis_app, project, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings

        dialog, _target = self._ready_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            row = cartograph.cartograph_share_actions["export_pdf"]
            assert row.toolTip() == (
                "Opens the cartograph and prints it at true page size"
            )
            layout = cartograph.state.cartograph_layout
            cmyk = PrintSettings(profile="cmyk")
            cartograph.state.cartograph_print[layout] = cmyk.to_json()
            cartograph._update_share_enabled()
            assert "CMYK or ICC PDF with QGIS" in row.toolTip()
            cartograph.state.cartograph_print[layout] = PrintSettings().to_json()
            cartograph._update_share_enabled()
            assert row.toolTip() == (
                "Opens the cartograph and prints it at true page size"
            )
        finally:
            dialog.close()

    def test_an_opened_message_goes_once_the_file_or_layout_changes(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, _target = self._ready_dialog(project, tmp_path)
        _add_layout(project, "Other", [("Map", "EPSG:4326")])
        try:
            cartograph = dialog._cartograph
            monkeypatch.setattr(
                "nika_onlymap_exporter.ui.cartograph.controller.QDesktopServices.openUrl",
                lambda url: None,
            )
            cartograph.on_reveal()
            assert dialog.status_label.text().startswith("Opened")
            cartograph._on_file_edited(str(tmp_path / "elsewhere.html"))
            assert dialog.status_label.text() == ""
            cartograph.on_reveal()
            cartograph.layout_combo.setCurrentText("Other")
            assert dialog.status_label.text() == ""
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
            _choose_cartograph(dialog)
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
            # A follow-up save may be debouncing: "Changes pending" until it lands.
            assert _wait_for(
                qgis_app,
                lambda: "Saved" in cartograph.cartograph_status_label.text(),
            )
        finally:
            dialog.close()


class TestPrintSettings:
    """Share > Print settings: kept per layout, applied on every save, and
    deciding which way Export PDF goes."""

    def _real_dialog(self, qgis_app, project, tmp_path, *, static=False):
        import os

        from cartograph_fixtures import build_survey_layout

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR")
        survey = build_survey_layout(project, tmp_path)
        survey.layout.pageCollection().deletePage(1)
        project.layoutManager().addLayout(survey.layout)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph.layout_combo.setCurrentText(survey.layout.name())
        if static:
            for choice in dialog.state.frame_choices(survey.layout.name()).values():
                choice.mode = "static"
        target = tmp_path / "survey.html"
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        assert _wait_for(qgis_app, lambda: target.exists(), timeout=60)
        return dialog, target

    def test_saved_settings_reach_the_file(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.ui.cartograph import print_settings_dialog

        dialog, target = self._real_dialog(qgis_app, project, tmp_path)
        try:
            chosen = PrintSettings(bleed="3mm", crop_marks=True)
            monkeypatch.setattr(
                print_settings_dialog,
                "ask_print_settings",
                lambda *a, **k: (print_settings_dialog.ACTION_SAVE, chosen),
            )
            dialog._cartograph.on_print_settings()
            # The settings themselves, not any new save: an autosave already
            # under way when the layout opened can land first.
            assert _wait_for(
                qgis_app,
                lambda: 'bleed="3"' in target.read_text(encoding="utf-8"),
                timeout=60,
            )
            html = target.read_text(encoding="utf-8")
            assert 'crop-marks=""' in html
            assert dialog.state.cartograph_print["survey-utm"] == chosen.to_json()
        finally:
            dialog.close()

    def test_rgb_export_prints_once_the_settings_are_saved(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.ui.cartograph import print_settings_dialog

        dialog, target = self._real_dialog(qgis_app, project, tmp_path)
        try:
            opened = []
            monkeypatch.setattr(
                dialog._cartograph, "_open_url", lambda url: opened.append(url)
            )
            monkeypatch.setattr(
                print_settings_dialog,
                "ask_print_settings",
                lambda *a, **k: (
                    print_settings_dialog.ACTION_EXPORT,
                    PrintSettings(bleed="5mm"),
                ),
            )
            dialog._cartograph.on_print_settings()
            assert opened == []  # not before the file has the new settings
            assert _wait_for(qgis_app, lambda: opened, timeout=60)
            assert opened[0].query() == "print=1"
            assert 'bleed="5"' in target.read_text(encoding="utf-8")
        finally:
            dialog.close()

    def test_print_after_save_keeps_its_query_without_the_server(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.ui.cartograph import (
            controller,
            print_settings_dialog,
        )

        def refuse(_self) -> str:
            raise OSError("no port")

        monkeypatch.setattr(controller.PreviewServer, "start", refuse)
        dialog, target = self._real_dialog(qgis_app, project, tmp_path)
        try:
            opened = []
            monkeypatch.setattr(dialog._cartograph, "_open_url", opened.append)
            monkeypatch.setattr(
                print_settings_dialog,
                "ask_print_settings",
                lambda *a, **k: (print_settings_dialog.ACTION_EXPORT, PrintSettings()),
            )
            dialog._cartograph.on_print_settings()
            assert _wait_for(qgis_app, lambda: opened, timeout=60)
            assert opened[0].toLocalFile() == str(target)
            assert opened[0].query() == "print=1"
        finally:
            dialog.close()

    def test_cmyk_export_goes_through_qgis(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog

        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.exporters import print_pdf

        # QGIS's exporter prints only an all-static layout; the survey's maps
        # would otherwise start live in their own CRS.
        dialog, _target = self._real_dialog(qgis_app, project, tmp_path, static=True)
        try:
            cmyk = PrintSettings(profile="cmyk")
            dialog.state.cartograph_print["survey-utm"] = cmyk.to_json()
            pdf = tmp_path / "survey.pdf"
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName", lambda *a, **k: (str(pdf), "")
            )
            calls = []
            monkeypatch.setattr(
                print_pdf,
                "export_print_pdf",
                lambda layout, settings, path, page=None: (
                    calls.append((layout.name(), path))
                    or print_pdf.PrintPdfResult(path=path, warnings=())
                ),
            )
            opened = []
            monkeypatch.setattr(
                dialog._cartograph, "_open_url", lambda url: opened.append(url)
            )
            dialog._cartograph.on_export_pdf()
            assert calls == [("survey-utm", pdf)]
            assert opened == []
        finally:
            dialog.close()

    def _native_pdf_setup(self, qgis_app, project, tmp_path, monkeypatch, **page):
        from qgis.PyQt.QtWidgets import QFileDialog

        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.exporters import print_pdf
        from nika_onlymap_exporter.ui.cartograph import print_settings_dialog

        dialog, _target = self._real_dialog(qgis_app, project, tmp_path, static=True)
        cartograph = dialog._cartograph
        dialog.state.cartograph_print["survey-utm"] = PrintSettings(
            profile="cmyk"
        ).to_json()
        choice = dialog.state.page_choice("survey-utm")
        for key, value in page.items():
            setattr(choice, key, value)
        pdf = tmp_path / "survey.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(pdf), "")
        )
        exports = []

        def fake_export(layout, settings, path, page=None):
            exports.append(path)
            return print_pdf.PrintPdfResult(
                path=path,
                warnings=(
                    print_pdf.attribution_warning(),
                    print_pdf.theme_warning(),
                    "Only page 1 is printed.",
                ),
            )

        monkeypatch.setattr(print_pdf, "export_print_pdf", fake_export)
        opened = []
        monkeypatch.setattr(cartograph, "_open_url", opened.append)
        asked = []

        def answer_with(answer):
            def fake(**kwargs):
                asked.append(kwargs)
                return answer

            monkeypatch.setattr(
                print_settings_dialog, "ask_native_pdf_differences", fake
            )

        return dialog, cartograph, exports, opened, asked, answer_with

    def test_native_pdf_without_attribution_or_theme_asks_nothing(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, cartograph, exports, _o, asked, answer_with = self._native_pdf_setup(
            qgis_app, project, tmp_path, monkeypatch, title="A title"
        )
        try:
            answer_with("cancel")
            cartograph.on_export_pdf()
            assert asked == []  # the title is applied by the exporter
            assert len(exports) == 1
        finally:
            dialog.close()

    @pytest.mark.parametrize(
        "page", [{"attribution": "Data: X"}, {"theme": "dark"}], ids=["attr", "theme"]
    )
    def test_native_pdf_asks_when_attribution_or_theme_is_set(
        self, qgis_app, project, tmp_path, monkeypatch, page
    ) -> None:
        dialog, cartograph, exports, _o, asked, answer_with = self._native_pdf_setup(
            qgis_app, project, tmp_path, monkeypatch, **page
        )
        try:
            answer_with("continue")
            cartograph.on_export_pdf()
            assert len(asked) == 1
            assert bool(asked[0]["attribution"]) == ("attribution" in page)
            assert asked[0]["themed"] == ("theme" in page)
            assert len(exports) == 1
            # Told in the dialog, so the post-export line does not repeat it.
            text = dialog.status_label.text()
            assert "Only page 1 is printed." in text
            assert "attribution" not in text and "theme" not in text
        finally:
            dialog.close()

    def test_native_pdf_cancel_writes_nothing(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, cartograph, exports, opened, _a, answer_with = self._native_pdf_setup(
            qgis_app, project, tmp_path, monkeypatch, attribution="Data: X"
        )
        try:
            answer_with(None)
            cartograph.on_export_pdf()
            assert exports == [] and opened == []
            assert not (tmp_path / "survey.pdf").exists()
        finally:
            dialog.close()

    def test_native_pdf_browser_choice_opens_the_print_url(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, cartograph, exports, opened, _a, answer_with = self._native_pdf_setup(
            qgis_app, project, tmp_path, monkeypatch, theme="dark"
        )
        try:
            answer_with("browser")
            cartograph.on_export_pdf()
            assert exports == []
            assert len(opened) == 1 and "print=1" in opened[0].toString()
        finally:
            dialog.close()

    def test_native_pdf_add_attribution_is_one_undo_step_then_exports(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from qgis.core import QgsLayoutItemLabel

        dialog, cartograph, exports, _o, _a, answer_with = self._native_pdf_setup(
            qgis_app, project, tmp_path, monkeypatch, attribution="Data: X"
        )
        try:
            layout = cartograph._current_layout

            def labels():
                return [
                    i.text()
                    for i in layout.items()
                    if isinstance(i, QgsLayoutItemLabel)
                ]

            stack = layout.undoStack().stack()
            before, count = labels(), stack.index()
            answer_with("add-attribution")
            cartograph.on_export_pdf()
            assert labels().count("Data: X") == before.count("Data: X") + 1
            assert stack.index() == count + 1
            assert len(exports) == 1
            assert "attribution" not in dialog.status_label.text()
            stack.undo()
            assert labels() == before
        finally:
            dialog.close()

    def test_native_pdf_dialog_offers_each_choice_and_never_the_title(
        self, qgis_app, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        from nika_onlymap_exporter.ui.cartograph import print_settings_dialog as psd

        shown = {}

        def fake_exec(box):
            shown["text"] = box.text()
            shown["buttons"] = {b.text(): b for b in box.buttons()}
            shown["pick"](box)

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(
            QMessageBox, "clickedButton", lambda box: shown["buttons"][shown["want"]]
        )
        for want, expected in (
            ("Continue", psd.NATIVE_CONTINUE),
            ("Use browser RGB export instead", psd.NATIVE_BROWSER),
            ("Add attribution to the layout", psd.NATIVE_ADD_ATTRIBUTION),
            ("Cancel", None),
        ):
            shown["want"], shown["pick"] = want, lambda box: None
            assert psd.ask_native_pdf_differences(attribution="x", themed=True) == (
                expected
            )
        assert "title" not in shown["text"].lower()
        assert "attribution" in shown["text"] and "theme" in shown["text"]
        shown["want"] = "Continue"
        psd.ask_native_pdf_differences(attribution="", themed=True)
        assert "Add attribution to the layout" not in shown["buttons"]

    def test_print_settings_row_needs_a_layout(self, qgis_app, project) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            row = dialog._cartograph.cartograph_share_actions["print_settings"]
            assert not row.isEnabled()
        finally:
            dialog.close()


class TestPageFieldsAndTitle:
    def test_title_attribution_and_theme_reach_the_file(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        try:
            cartograph = dialog._cartograph
            before = target.stat().st_mtime_ns
            cartograph.title_edit.setText("Parcel survey")
            cartograph.title_edit.textEdited.emit("Parcel survey")
            cartograph.attribution_edit.setText("Survey: NIKA 2026")
            cartograph.attribution_edit.textEdited.emit("Survey: NIKA 2026")
            cartograph.theme_combo.setCurrentIndex(
                cartograph.theme_combo.findData("sepia")
            )
            assert _wait_for(
                qgis_app, lambda: target.stat().st_mtime_ns != before, timeout=60
            )
            assert _wait_for(
                qgis_app,
                lambda: 'theme="sepia"' in target.read_text(encoding="utf-8"),
                timeout=60,
            )
            html = target.read_text(encoding="utf-8")
            assert 'title="Parcel survey"' in html
            assert 'attribution="Survey: NIKA 2026"' in html
        finally:
            dialog.close()

    def test_page_fields_are_per_layout_and_survive_reopening(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.settings import load_state

        _add_layout(project, "One", [("A", "EPSG:4326")])
        _add_layout(project, "Two", [("B", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.layout_combo.setCurrentText("One")
            cartograph.title_edit.setText("First")
            cartograph.title_edit.textEdited.emit("First")
            cartograph.layout_combo.setCurrentText("Two")
            assert cartograph.title_edit.text() == ""
            cartograph.layout_combo.setCurrentText("One")
            assert cartograph.title_edit.text() == "First"
        finally:
            dialog.close()
        assert load_state(project).page_choice("One").title == "First"

    def test_the_window_title_names_the_cartograph_file(
        self, qgis_app, project, tmp_path
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            dialog._cartograph.file_edit.setText(str(tmp_path / "parcels.html"))
            dialog._cartograph._on_file_edited(str(tmp_path / "parcels.html"))
            assert dialog.windowTitle().startswith("parcels.html")
            _choose_web(dialog)
            assert dialog.windowTitle() == "QGIS2WebMap by NIKA"
        finally:
            dialog.close()


class TestFramesTabExtras:
    def test_use_canvas_view_sets_the_item_extent_undoably(
        self, qgis_app, project
    ) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
        from qgis.gui import QgsMapCanvas

        layout = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        canvas = QgsMapCanvas()
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        canvas.resize(400, 300)
        canvas.setExtent(QgsRectangle(100.0, 0.0, 104.0, 3.0))

        class Iface(FakeIface):
            def mapCanvas(self):  # noqa: N802 - mirrors the QGIS interface
                return canvas

        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        dialog = MainDialog(Iface(), None)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            (item_id,) = cartograph._frame_rows
            action = cartograph.frames_list.card(item_id).canvas_action
            assert action.isEnabled()
            action.trigger()
            from qgis.core import QgsLayoutItemMap

            map_item = next(
                i for i in layout.items() if isinstance(i, QgsLayoutItemMap)
            )
            shown = canvas.extent()
            assert map_item.extent().contains(shown.center())
            assert layout.undoStack().stack().count() >= 1
        finally:
            dialog.close()

    def test_use_canvas_view_is_disabled_without_a_canvas(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            (item_id,) = cartograph._frame_rows
            action = cartograph.frames_list.card(item_id).canvas_action
            assert not action.isEnabled()
        finally:
            dialog.close()

    def test_each_frame_shows_its_count_of_issues(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, _target = TestPrintSettings()._real_dialog(
            qgis_app, project, tmp_path, static=True
        )
        try:
            cartograph = dialog._cartograph
            badges = [
                cartograph.frames_list.card(item_id).issues_button
                for item_id in cartograph._frame_rows
            ]
            assert badges
            # Both survey frames are static (UTM), which is a "Changed" row each.
            for badge in badges:
                assert not badge.isHidden()
                count, word, _arrow = badge.text().split()
                assert int(count) >= 1 and word in ("issue", "issues")
        finally:
            dialog.close()


class TestCanvasAndImport:
    def _canvas_dialog(self, project):
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsGeometry,
            QgsRectangle,
            QgsVectorLayer,
        )
        from qgis.gui import QgsMapCanvas

        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "parks", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(103.7, 1.25, 103.9, 1.4)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        project.addMapLayer(layer)
        project.setTitle("Parks")
        canvas = QgsMapCanvas()
        canvas.resize(400, 300)
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        canvas.setLayers([layer])
        canvas.setExtent(layer.extent())

        class Iface(FakeIface):
            def mapCanvas(self):  # noqa: N802 - mirrors the QGIS interface
                return canvas

        return MainDialog(Iface(), None), canvas

    def test_current_canvas_creates_and_selects_a_carto_layout(
        self, qgis_app, project
    ) -> None:
        dialog, _canvas = self._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            _click_new_from_template(dialog)
            name = cartograph.layout_combo.currentText()
            assert name.startswith("Daylight \u00b7 ")
            assert project.layoutManager().layoutByName(name) is not None
            assert dialog.state.cartograph_layout == name
            assert len(cartograph._frame_rows) == 1
        finally:
            dialog.close()

    def test_the_picked_template_is_the_layout_built(
        self, qgis_app, project, picked_template
    ) -> None:
        picked_template.choice = ("locator", "A3", "portrait")
        dialog, _canvas = self._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            _click_new_from_template(dialog)
            assert picked_template.shown == 1
            # The picker's previews draw the real page for this canvas.
            (preview,) = picked_template.previews
            preview = _drawn(qgis_app, preview)
            assert preview is not None and not preview.isNull()
            layout = project.layoutManager().layoutByName(
                dialog._cartograph.layout_combo.currentText()
            )
            page = layout.pageCollection().page(0).pageSize()
            assert (round(page.width()), round(page.height())) == (297, 420)
            assert layout.itemById("locator") is not None
        finally:
            dialog.close()

    def test_cancelling_the_picker_builds_nothing(
        self, qgis_app, project, picked_template
    ) -> None:
        picked_template.choice = None
        dialog, _canvas = self._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            before = len(project.layoutManager().layouts())
            _click_new_from_template(dialog)
            assert picked_template.shown == 1
            assert len(project.layoutManager().layouts()) == before
        finally:
            dialog.close()

    def test_the_picker_s_key_holds_until_the_canvas_or_project_changes(
        self, qgis_app, project, picked_template
    ) -> None:
        """The key the controller hands the picker: the same for the same
        canvas, so pages drawn before are shown again; new after anything a
        preview shows changes."""
        from qgis.PyQt.QtGui import QColor

        picked_template.choice = None
        dialog, canvas = self._canvas_dialog(project)
        layer = canvas.layers()[0]

        def key() -> str:
            dialog._cartograph.create_from_canvas()
            return picked_template.canvas_keys[-1]

        def restyle() -> None:
            layer.renderer().symbol().setColor(QColor("#ff00ff"))
            layer.triggerRepaint()

        def edit() -> None:
            layer.startEditing()
            layer.changeGeometry(
                next(layer.getFeatures()).id(),
                qgis_core.QgsGeometry.fromRect(
                    qgis_core.QgsRectangle(103.7, 1.25, 103.8, 1.3)
                ),
            )

        def abstract() -> None:
            metadata = project.metadata()
            metadata.setAbstract("Parks of the west.")
            project.setMetadata(metadata)

        changes = {
            "style": restyle,
            "feature edited": edit,
            "extent": lambda: canvas.zoomByFactor(0.5),
            "title": lambda: project.setTitle("Lakes"),
            "abstract": abstract,
            "figures": lambda: project.setCustomVariables({"nika_figure_1": "214"}),
            "layer toggled off": lambda: canvas.setLayers([]),
        }
        try:
            _choose_cartograph(dialog)
            first = key()
            assert first
            assert key() == first
            for name, change in changes.items():
                before = key()
                change()
                assert key() != before, name
        finally:
            layer.rollBack()
            project.setCustomVariables({})
            dialog.close()

    def test_reopening_the_picker_draws_nothing_already_drawn(
        self, qgis_app, project, monkeypatch
    ) -> None:
        """Through the real controller and picker: the second opening shows
        the pages the first drew, and draws none again; a restyled layer
        draws them afresh."""
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtGui import QColor

        from nika_onlymap_exporter.ui.cartograph import template_picker

        calls: list[str] = []
        drawn: list[set[str]] = []

        def ask(render, parent=None, project=None, canvas_key="", planner=None):
            def counted(template_id, *args, **kwargs):
                calls.append(template_id)
                return render(template_id, *args, **kwargs)

            picker = template_picker.TemplatePicker(
                counted,
                qsettings=QSettings(),
                canvas_key=canvas_key,
                project=project,
                planner=planner,
            )
            picker.resize(900, 600)
            picker.show()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and (
                picker._queue
                or picker._inflight is not None
                or picker._timer.isActive()
                or picker._scroll_rest.isActive()
            ):
                qgis_app.processEvents()
            drawn.append({t for t, row in picker.rows.items() if row.drawn})
            picker.reject()
            picker.deleteLater()
            return None

        monkeypatch.setattr(template_picker, "ask_template", ask)
        template_picker._SHARED_PAGES.clear()
        dialog, canvas = self._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            dialog._cartograph.create_from_canvas()
            first = list(calls)
            assert first and drawn[0] == set(first)
            calls.clear()
            dialog._cartograph.create_from_canvas()
            assert calls == []
            assert drawn[1] == drawn[0]
            canvas.layers()[0].renderer().symbol().setColor(QColor("#ff00ff"))
            dialog._cartograph.create_from_canvas()
            assert sorted(calls) == sorted(first)
        finally:
            template_picker._SHARED_PAGES.clear()
            dialog.close()

    def test_the_picker_plans_this_project_s_pages(
        self, qgis_app, project, picked_template
    ) -> None:
        """The planner the controller passes lays out the page from the
        canvas and project, as the preview does: Marginalia includes its
        notes once the project has an abstract."""
        from nika_onlymap_exporter.core.layout_templates import get_template
        from nika_onlymap_exporter.core.template_cues import plan_cues

        picked_template.choice = None
        dialog, _canvas = self._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            dialog._cartograph.create_from_canvas()
            plan = picked_template.planners[-1]("marginalia", "A4", "auto", figures=())
            cues = plan_cues(get_template("marginalia"), plan)
            assert plan.title_text == "Parks"
            assert "notes" in cues.can_show
            metadata = project.metadata()
            metadata.setAbstract("Parks of the west.")
            project.setMetadata(metadata)
            dialog._cartograph.create_from_canvas()
            plan = picked_template.planners[-1]("marginalia", "A4", "auto", figures=())
            assert "notes" in plan_cues(get_template("marginalia"), plan).includes
        finally:
            dialog.close()

    def test_make_a_cartograph_builds_from_the_canvas(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.core.settings import DialogOutput

        dialog, _canvas = self._canvas_dialog(project)
        try:
            dialog.share_actions["cartograph"].trigger()
            assert dialog.output_switcher.output() is DialogOutput.CARTOGRAPH
            assert dialog._cartograph.layout_combo.currentText().startswith(
                "Daylight \u00b7 "
            )
        finally:
            dialog.close()

    def test_without_a_canvas_new_from_template_says_why(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert not cartograph.new_from_template_button.isEnabled()
            assert "no map canvas" in cartograph.new_from_template_button.toolTip()
            assert not cartograph.page_preview.template_button.isEnabled()
        finally:
            dialog.close()

    def test_import_turns_a_cartograph_back_into_a_layout(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog

        dialog, target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        try:
            monkeypatch.setattr(
                QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), "")
            )
            from qgis.PyQt.QtWidgets import QMessageBox

            monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: 0)
            before = {layout.name() for layout in project.layoutManager().layouts()}
            dialog._cartograph.on_import_cartograph()
            after = {layout.name() for layout in project.layoutManager().layouts()}
            (new_name,) = after - before
            assert dialog._cartograph.layout_combo.currentText() == new_name
            assert len(dialog._cartograph._frame_rows) == 2
        finally:
            dialog.close()


class TestLayoutTextSizes:
    """The Layout tab of a layout made from a template: no text size sliders
    (sizes are set in the template picker or by editing the preview), the
    "Based on" hint and the layout editor link."""

    def _template_dialog(self, project, picked_template, template="classic"):
        picked_template.choice = (template, "A4", "landscape")
        dialog, _canvas = TestCanvasAndImport()._canvas_dialog(project)
        _choose_cartograph(dialog)
        _click_new_from_template(dialog)
        cartograph = dialog._cartograph
        layout = project.layoutManager().layoutByName(
            cartograph.layout_combo.currentText()
        )
        return dialog, cartograph, layout

    def test_the_layout_tab_has_no_text_size_sliders(
        self, qgis_app, project, picked_template
    ) -> None:
        from qgis.PyQt.QtWidgets import QSlider

        from nika_onlymap_exporter.ui.cartograph.template_picker import (
            TextSizeControls,
        )

        dialog, cartograph, _layout = self._template_dialog(project, picked_template)
        try:
            assert not hasattr(cartograph, "size_controls")
            assert cartograph.page_section.body.findChildren(TextSizeControls) == []
            assert cartograph.page_section.body.findChildren(QSlider) == []
        finally:
            dialog.close()

    def test_based_on_names_the_template_only_for_a_template_layout(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog, cartograph, layout = self._template_dialog(
            project, picked_template, template="field-map"
        )
        try:
            label = cartograph.based_on_label
            assert label.text() == "Based on Fieldbook"
            assert not label.isHidden()
            plain = _add_layout(project, "plain", [("m", "EPSG:4326")])
            cartograph._select_layout(plain.name())
            assert label.isHidden()
            cartograph._select_layout(layout.name())
            assert not label.isHidden()
        finally:
            dialog.close()

    def test_open_layout_editor_hands_the_selected_layout_to_qgis(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog, cartograph, layout = self._template_dialog(project, picked_template)
        try:
            opened: list[object] = []
            dialog.iface.openLayoutDesigner = opened.append
            cartograph._update_editor_link()
            link = cartograph.page_pane.editor_link
            assert link.isEnabled()
            link.click()
            assert opened == [layout]
            del dialog.iface.openLayoutDesigner
            cartograph._update_editor_link()
            assert not link.isEnabled()
            assert link.toolTip()
        finally:
            dialog.close()


class TestLayoutTemplateExport:
    """Export the selected layout as a .qpt, with an honest success message."""

    def _dialog_on(self, project, layout_name):
        _add_layout(project, layout_name, [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        dialog._cartograph.refresh_layouts()
        combo = dialog._cartograph.layout_combo
        index = combo.findText(layout_name)
        assert index >= 0
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        assert dialog.state.cartograph_layout == layout_name
        return dialog

    def test_the_suggested_file_name_is_a_safe_file_name(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog

        dialog = self._dialog_on(project, "Survey /2026")
        try:
            suggested = []
            monkeypatch.setattr(
                QFileDialog,
                "getSaveFileName",
                lambda *a, **k: (suggested.append(a[2]) or "", ""),
            )
            dialog._cartograph.on_export_qpt()
            assert Path(suggested[0]).name == "Survey-2026.qpt"
        finally:
            dialog.close()

    def test_a_failed_save_is_reported_not_announced_as_saved(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

        dialog = self._dialog_on(project, "Survey")
        try:
            # A folder that does not exist: the layout cannot be written there.
            target = tmp_path / "no-such-folder" / "Survey.qpt"
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "")
            )
            warnings = []
            monkeypatch.setattr(
                QMessageBox,
                "warning",
                lambda *a, **k: warnings.append(a[2]) or 0,
            )
            dialog.status_label.setText("")
            dialog._cartograph.on_export_qpt()
            assert len(warnings) == 1
            assert str(target) in warnings[0]
            assert "Saved" not in dialog.status_label.text()
            assert not target.exists()
        finally:
            dialog.close()


class TestAccessibility:
    def test_the_button_opens_one_colour_vision_dialog_fed_by_the_save(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, _target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        try:
            cartograph = dialog._cartograph
            assert cartograph.accessibility_button.isEnabled()
            cartograph.accessibility_button.click()
            first = cartograph._colour_vision
            assert first is not None and first.isVisible()
            cartograph.accessibility_button.click()
            assert cartograph._colour_vision is first
            # The survey's static legend is literal rows: the check sees them.
            assert [e.color for e in cartograph._legend_entries] == ["#eba941"]
            # The hand-off is not ready for cartographs, here as on Share.
            assert not first.ask_button.isEnabled()
            assert first.ask_button.toolTip() == "Not available for cartographs yet"
        finally:
            dialog.close()

    def test_a_simulation_checks_the_button_and_names_the_mode(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, _target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        try:
            cartograph = dialog._cartograph
            cartograph.accessibility_button.click()
            cartograph._colour_vision.set_condition("deuteranopia")
            assert cartograph.accessibility_button.isChecked()
            assert "deuteranopia" in cartograph.accessibility_button.toolTip().lower()
            cartograph._colour_vision.set_condition(None)
            assert not cartograph.accessibility_button.isChecked()
        finally:
            dialog.close()

    def test_the_browser_preview_opens_with_the_cvd_trigger(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        dialog, _target = TestPrintSettings()._real_dialog(qgis_app, project, tmp_path)
        try:
            opened = []
            monkeypatch.setattr(dialog._cartograph, "_open_url", opened.append)
            dialog._cartograph._open_cvd_preview("tritanopia")
            assert _wait_for(qgis_app, lambda: opened, timeout=60)
            assert opened[0].query() == "cvd=tritanopia"
        finally:
            dialog.close()


class TestWhereItSaves:
    def test_an_unsaved_project_saves_in_the_cartographs_folder_at_once(
        self, qgis_app, project, fake_cartograph_export
    ) -> None:
        """No browsing first: the Cartographs folder, named after the layout."""
        from nika_onlymap_exporter.core.save_folders import cartographs_folder

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            target = cartographs_folder() / "Survey.html"
            assert dialog._cartograph.file_edit.text() == str(target)
            assert _wait_for(qgis_app, lambda: target.exists())
        finally:
            dialog.close()

    def test_a_saved_project_names_it_after_both(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.save_folders import cartographs_folder

        project.setFileName(str(tmp_path / "parcels.qgz"))
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            target = cartographs_folder() / "parcels-Survey.html"
            assert dialog._cartograph.file_edit.text() == str(target)
            assert _wait_for(qgis_app, lambda: target.exists())
        finally:
            dialog.close()
            project.setFileName("")

    def test_another_maps_file_is_never_overwritten(
        self, qgis_app, project, fake_cartograph_export
    ) -> None:
        """A cartograph saves itself, so a taken name would be overwritten
        unasked: it gets a number instead."""
        from nika_onlymap_exporter.core.save_folders import cartographs_folder

        taken = cartographs_folder() / "Survey.html"
        taken.parent.mkdir(parents=True)
        taken.write_text("another project's cartograph")
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            target = cartographs_folder() / "Survey (2).html"
            assert dialog._cartograph.file_edit.text() == str(target)
            assert _wait_for(qgis_app, lambda: target.exists())
            assert taken.read_text() == "another project's cartograph"
        finally:
            dialog.close()


class TestSavesFollowTheProject:
    def test_a_layer_restyled_in_qgis_saves_again(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """Also the guard for closing mid-build: the dialog closes while this
        rebuild runs, which once aborted the process at exit."""
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            before = len(fake_cartograph_export.calls)
            dialog.content_watcher.changed.emit()
            assert _wait_for(
                qgis_app, lambda: len(fake_cartograph_export.calls) > before
            )
        finally:
            dialog.close()

    def test_a_layers_tab_change_saves_again(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import LayerSettings

        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            before = len(fake_cartograph_export.calls)
            dialog.state.layers["some-layer"] = LayerSettings(popup=False)
            assert _wait_for(
                qgis_app, lambda: len(fake_cartograph_export.calls) > before
            )
        finally:
            dialog.close()

    def test_switching_layouts_resets_the_status_and_saves(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        _add_layout(project, "Second", [("Other", "EPSG:4326")])
        dialog = _saved_once(qgis_app, project, fake_cartograph_export, tmp_path)
        try:
            cartograph = dialog._cartograph
            assert cartograph._last_summary is not None
            other = (
                "Survey"
                if cartograph.layout_combo.currentText() == "Second"
                else "Second"
            )
            cartograph.layout_combo.setCurrentText(other)
            assert cartograph._last_summary is None
            assert cartograph.cartograph_status_label.text().startswith("Not saved")
        finally:
            dialog.close()


class TestAtlasTab:
    def _atlas_dialog(self, project, tmp_path):
        from atlas_fixtures import build_atlas_layout

        built = build_atlas_layout(project)
        project.layoutManager().addLayout(built.layout)
        project.setFileName(str(tmp_path / "parcels.qgz"))
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        dialog._cartograph.layout_combo.setCurrentText(built.layout.name())
        return dialog, built

    def test_the_tab_is_the_cartographs_alone(self, qgis_app, project) -> None:
        """Atlas is a tab of the cartograph's panel, after Layout; the web map
        has no tab for it."""
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()

            def titles() -> list[str]:
                return [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]

            cartograph = dialog._cartograph
            atlas = cartograph.atlas_tab
            tabs = cartograph.panel_tabs
            assert "Atlas" not in titles()
            assert not atlas.isVisible()
            _choose_cartograph(dialog)
            cartograph.open_section("atlas")
            qgis_app.processEvents()
            assert atlas.isVisible()
            index = tabs.indexOf(tabs.currentWidget())
            assert index == tabs.indexOf(cartograph.layout_scroll) + 1
            assert tabs.currentWidget().isAncestorOf(atlas)
            _choose_web(dialog)
            assert "Atlas" not in titles()
            assert not atlas.isVisible()
        finally:
            dialog.close()

    def _rotated_utm_atlas(self, project, tmp_path, *, atlas_on=True):
        """The atlas layout, its map in a rotated UTM zone: a CRS whose
        Interactive/Image default is Image unless the atlas drives the map."""
        from atlas_fixtures import build_atlas_layout
        from qgis.core import QgsCoordinateReferenceSystem

        built = build_atlas_layout(project)
        built.main_map.setCrs(QgsCoordinateReferenceSystem("EPSG:32648"))
        built.main_map.setMapRotation(15)
        built.atlas.setEnabled(atlas_on)
        project.layoutManager().addLayout(built.layout)
        project.setFileName(str(tmp_path / "parcels.qgz"))
        return built

    def _open_on(self, project, built):
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        dialog._cartograph.layout_combo.setCurrentText(built.layout.name())
        return dialog

    def test_an_atlas_driven_frame_opens_interactive_and_exports_pages(
        self, qgis_app, project, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import FidelityStatus
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.layout_reader import (
            atlas_page_count,
            read_layout,
        )

        built = self._rotated_utm_atlas(project, tmp_path)
        dialog = self._open_on(project, built)
        try:
            cartograph = dialog._cartograph
            item_id = built.main_map.uuid()
            card = cartograph.frames_list.card(item_id)
            assert card.mode_combo.currentData() == "live"
            assert card.mode_combo.currentText() == "Interactive"
            choices = dialog.state.frame_choices(built.layout.name())
            report = FidelityReportBuilder()
            reading = read_layout(
                built.layout,
                report,
                frame_modes={i: c.mode for i, c in choices.items()},
            )
            assert reading.atlas is not None
            assert not [
                i
                for i in report.by_status(FidelityStatus.BLOCKED)
                if "No atlas pages" in i.detail
            ]
            assert atlas_page_count(built.layout) == 3
        finally:
            dialog.close()
            project.setFileName("")

    def test_an_explicit_image_choice_survives_reopening(
        self, qgis_app, project, tmp_path
    ) -> None:
        built = self._rotated_utm_atlas(project, tmp_path)
        dialog = self._open_on(project, built)
        try:
            card = dialog._cartograph.frames_list.card(built.main_map.uuid())
            card.mode_combo.setCurrentIndex(card.mode_combo.findData("static"))
            assert (
                dialog.state.frame_choices(built.layout.name())[
                    built.main_map.uuid()
                ].mode
                == "static"
            )
        finally:
            dialog.close()
        dialog = self._open_on(project, built)
        try:
            card = dialog._cartograph.frames_list.card(built.main_map.uuid())
            assert card.mode_combo.currentData() == "static"
        finally:
            dialog.close()
            project.setFileName("")

    def test_without_an_atlas_the_default_is_unchanged(
        self, qgis_app, project, tmp_path
    ) -> None:
        built = self._rotated_utm_atlas(project, tmp_path, atlas_on=False)
        dialog = self._open_on(project, built)
        try:
            card = dialog._cartograph.frames_list.card(built.main_map.uuid())
            assert card.mode_combo.currentData() == "static"
        finally:
            dialog.close()
            project.setFileName("")

    def test_it_mirrors_the_layouts_atlas(self, qgis_app, project, tmp_path) -> None:
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            tab = dialog._cartograph.atlas_tab
            assert tab.enabled_check.isChecked()
            assert tab.coverage_combo.currentLayer() is built.parcels
            assert tab.map_combo.currentData() == built.main_map.uuid()
            assert tab.count_label.text() == "3 pages in QGIS"
        finally:
            dialog.close()
            project.setFileName("")

    def test_an_edit_changes_the_layout_undoably_and_saves(
        self, qgis_app, project, tmp_path, fake_cartograph_export
    ) -> None:
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            tab = dialog._cartograph.atlas_tab
            before = len(fake_cartograph_export.calls)
            tab.filter_edit.setExpression(""""lot" <> 'A-1'""")
            tab.filter_edit.fieldChanged.emit(tab.filter_edit.expression())
            assert built.atlas.filterFeatures()
            assert tab.count_label.text() == "2 pages in QGIS"
            assert _wait_for(
                qgis_app, lambda: len(fake_cartograph_export.calls) > before
            )
            built.layout.undoStack().stack().undo()
            assert not built.atlas.filterFeatures()
        finally:
            dialog.close()
            project.setFileName("")

    @pytest.mark.parametrize(
        ("key", "read", "typed"),
        [
            (
                "filter_edit",
                lambda a: (a.filterFeatures(), a.filterExpression()),
                "\"lot\" = 'A-1'",
            ),
            (
                "sort_edit",
                lambda a: (a.sortFeatures(), a.sortExpression()),
                'lower("lot")',
            ),
            (
                "filename_edit",
                lambda a: (True, a.filenameExpression()),
                "'sheet_' || \"lot\"",
            ),
        ],
    )
    @pytest.mark.parametrize("finish", ["Return", "Tab"])
    def test_enter_applies_a_typed_expression_like_tab(
        self, qgis_app, project, tmp_path, key, read, typed, finish
    ) -> None:
        """Real key presses in the field's line edit: Enter keeps the text
        and applies it as one undo step, exactly as Tab does."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QComboBox

        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            dialog.show()
            cartograph = dialog._cartograph
            cartograph.open_section("atlas")
            tab = cartograph.atlas_tab
            tab.advanced_section.set_open(True)
            qgis_app.processEvents()
            widget = getattr(tab, key)
            line = widget.findChild(QComboBox).lineEdit()
            line.setFocus()
            undo = built.layout.undoStack().stack()
            steps = undo.count()
            line.selectAll()
            QTest.keyClicks(line, typed)
            QTest.keyClick(line, getattr(Qt.Key, f"Key_{finish}"))
            qgis_app.processEvents()
            assert line.text() == typed
            assert widget.expression() == typed
            assert read(built.atlas)[1] == typed
            assert undo.count() == steps + 1
            # Leaving the field afterwards must not add a second step.
            QTest.keyClick(line, Qt.Key.Key_Tab)
            qgis_app.processEvents()
            assert line.text() == typed
            assert undo.count() == steps + 1
            undo.undo()
            assert read(built.atlas)[1] != typed
        finally:
            dialog.close()
            project.setFileName("")

    def test_choosing_the_same_coverage_layer_again_is_not_an_edit(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            tab = dialog._cartograph.atlas_tab
            undo = built.layout.undoStack().stack()
            steps = undo.count()
            tab.coverage_combo.layerChanged.emit(built.parcels)
            tab.coverage_combo.layerChanged.emit(tab.coverage_combo.currentLayer())
            assert undo.count() == steps
        finally:
            dialog.close()
            project.setFileName("")

    @pytest.mark.parametrize("sort", ["lot", 'lower("lot")'])
    def test_sort_direction_and_expression_are_separate_undoable_edits(
        self, qgis_app, project, tmp_path, sort
    ) -> None:
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            atlas = built.atlas
            atlas.setSortFeatures(True)
            atlas.setSortExpression(sort)
            tab = dialog._cartograph.atlas_tab
            undo = built.layout.undoStack().stack()
            steps = undo.count()
            tab.show_layout(built.layout)
            # Showing is not editing.
            assert tab.sort_edit.currentField()[0] == sort
            assert (atlas.sortFeatures(), atlas.sortExpression()) == (True, sort)
            assert atlas.sortAscending() and undo.count() == steps

            tab.descending_check.setChecked(True)
            assert (atlas.sortFeatures(), atlas.sortExpression()) == (True, sort)
            assert not atlas.sortAscending()
            undo.undo()
            assert atlas.sortAscending() and atlas.sortExpression() == sort
            assert not tab.descending_check.isChecked()
            undo.redo()
            assert not atlas.sortAscending()
            assert tab.descending_check.isChecked()

            tab.sort_edit.setExpression('upper("size")')
            tab.sort_edit.fieldChanged.emit(tab.sort_edit.expression())
            assert atlas.sortExpression() == 'upper("size")'
            assert not atlas.sortAscending()
            undo.undo()
            assert atlas.sortExpression() == sort
            assert tab.sort_edit.currentField()[0] == sort

            # A plain field picked in the widget is stored bare, as QGIS does.
            tab.sort_edit.setField("area")
            tab.sort_edit.fieldChanged.emit("area")
            assert atlas.sortExpression() == "area"
        finally:
            dialog.close()
            project.setFileName("")

    def test_the_direction_shown_is_the_one_stored_even_unsorted(
        self, qgis_app, project, tmp_path
    ) -> None:
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            atlas = built.atlas
            atlas.setSortFeatures(False)
            atlas.setSortAscending(False)
            tab = dialog._cartograph.atlas_tab
            tab.show_layout(built.layout)
            # Unsorted: the direction is kept but there is nothing to apply it to.
            assert tab.descending_check.isChecked()
            assert not tab.descending_check.isEnabled()
            tab.sort_edit.setField("lot")
            tab.sort_edit.fieldChanged.emit("lot")
            assert tab.descending_check.isEnabled()
            assert (atlas.sortFeatures(), atlas.sortAscending()) == (True, False)
            assert tab.descending_check.isChecked()
        finally:
            dialog.close()
            project.setFileName("")

    def test_the_followed_map_undoes_and_redoes_whole(
        self, qgis_app, project, tmp_path
    ) -> None:
        from atlas_fixtures import add_map
        from qgis.core import QgsLayoutItemMap

        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            main = built.main_map
            inset = add_map(built.layout, [built.parcels], x=170, w=40, h=40)
            inset.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
            inset.setAtlasMargin(0.3)
            tab = dialog._cartograph.atlas_tab
            tab.show_layout(built.layout)
            undo = built.layout.undoStack().stack()
            saves = []
            tab.changed.connect(lambda: saves.append(1))

            def state():
                return (
                    main.atlasDriven(),
                    inset.atlasDriven(),
                    inset.atlasScalingMode(),
                    round(inset.atlasMargin(), 6),
                    tab.map_combo.currentData(),
                )

            fixed = QgsLayoutItemMap.AtlasScalingMode.Fixed
            auto = QgsLayoutItemMap.AtlasScalingMode.Auto
            # An unrelated edit first: an Undo that missed the maps would
            # take this back instead.
            tab.filter_edit.setExpression("\"lot\" <> 'A-1'")
            tab.filter_edit.fieldChanged.emit(tab.filter_edit.expression())
            before = (True, False, fixed, 0.3, main.uuid())
            assert state() == before

            tab.map_combo.setCurrentIndex(tab.map_combo.findData(inset.uuid()))
            after = (False, True, auto, 0.1, inset.uuid())
            assert state() == after
            saves.clear()
            undo.undo()
            assert state() == before
            assert built.atlas.filterFeatures()
            assert saves, "an Undo is saved like any edit"
            undo.redo()
            assert state() == after

            tab.map_combo.setCurrentIndex(tab.map_combo.findData(""))
            assert state()[:2] == (False, False)
            assert tab.map_combo.currentData() == ""
            undo.undo()
            assert state() == after
            undo.undo()
            undo.undo()
            assert not built.atlas.filterFeatures()
            assert state() == before
        finally:
            dialog.close()
            project.setFileName("")

    def test_another_tabs_undo_step_is_not_an_atlas_change(
        self, qgis_app, project, tmp_path
    ) -> None:
        from qgis.core import QgsLayoutPoint, QgsUnitTypes

        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            tab = dialog._cartograph.atlas_tab
            undo = built.layout.undoStack()
            emitted = []
            tab.changed.connect(lambda: emitted.append(1))
            label = built.label
            undo.beginCommand(label, "Move label")
            label.attemptMove(QgsLayoutPoint(20, 5, QgsUnitTypes.LayoutMillimeters))
            undo.endCommand()
            undo.stack().undo()
            undo.stack().redo()
            assert emitted == []
        finally:
            dialog.close()
            project.setFileName("")

    def test_the_all_pages_buttons_say_why_only_while_greyed_out(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            tab = dialog._cartograph.atlas_tab
            buttons = (tab.zip_button, tab.print_button)
            greyed = ["The saved cartograph has no atlas pages yet"] * 2
            ready = [
                "Opens the cartograph and downloads every page as one ZIP",
                "Opens the cartograph with every page ready to print - choose "
                "Save as PDF for one multi-page PDF",
            ]
            for pages, tips in (
                (None, greyed),
                (3, ready),
                (None, greyed),
            ):
                tab.show_outcome((), pages=pages)
                assert [b.isEnabled() for b in buttons] == [pages is not None] * 2
                assert [b.toolTip() for b in buttons] == tips
        finally:
            dialog.close()

    def test_a_static_atlas_map_shows_why_it_will_not_page(
        self, qgis_app, project, tmp_path
    ) -> None:
        import os

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR")
        dialog, built = self._atlas_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            (item_id,) = [
                key for key in cartograph._frame_rows if key == built.main_map.uuid()
            ]
            mode = cartograph._frame_rows[item_id]["mode"]
            mode.setCurrentIndex(mode.findData("static"))
            tab = cartograph.atlas_tab
            assert _wait_for(
                qgis_app, lambda: not tab.problem_label.isHidden(), timeout=60
            )
            assert "Interactive" in tab.problem_label.text()
            assert not tab.zip_button.isEnabled()
        finally:
            dialog.close()
            project.setFileName("")

    def _type_filter(self, tab, expression: str) -> None:
        tab.advanced_section.set_open(True)
        tab.window()._cartograph.open_section("atlas")
        tab.filter_edit.setExpression(expression)
        tab.filter_edit.fieldChanged.emit(tab.filter_edit.expression())

    def test_an_invalid_filter_says_so_beside_the_field_and_blocks_export(
        self, qgis_app, project, tmp_path, fake_cartograph_export
    ) -> None:
        dialog, _built = self._atlas_dialog(project, tmp_path)
        try:
            dialog.show()
            tab = dialog._cartograph.atlas_tab
            buttons = (tab.zip_button, tab.print_button)
            # A saved atlas with pages, as the last good save left it.
            tab.show_outcome((), pages=3)
            assert [b.isEnabled() for b in buttons] == [True, True]
            self._type_filter(tab, '"lot" =')
            assert tab.filter_error_label.isVisible()
            assert tab.filter_error_label.text().startswith("Not a valid expression")
            assert not tab.sort_error_label.isVisible()
            # Not "0 pages in QGIS", and no export from the earlier save.
            assert tab.count_label.text() == ""
            assert not tab.saved_label.isVisible()
            assert [b.isEnabled() for b in buttons] == [False, False]
            assert "Correct" in buttons[0].toolTip()
            # An unknown field is as invalid as a typo.
            self._type_filter(tab, '"nothing_here" = 1')
            assert tab.filter_error_label.isVisible()
        finally:
            dialog.close()
            project.setFileName("")

    def test_valid_zero_match_and_subset_are_told_apart_from_invalid(
        self, qgis_app, project, tmp_path, fake_cartograph_export
    ) -> None:
        dialog, _built = self._atlas_dialog(project, tmp_path)
        try:
            dialog.show()
            tab = dialog._cartograph.atlas_tab
            buttons = (tab.zip_button, tab.print_button)
            self._type_filter(tab, "\"lot\" = 'nowhere'")
            assert not tab.filter_error_label.isVisible()
            assert tab.count_label.text() == "0 pages in QGIS"
            tab.show_outcome((), pages=0)
            assert tab.saved_label.isVisible()
            assert "no feature matches" in tab.saved_label.text()
            assert [b.isEnabled() for b in buttons] == [False, False]
            assert "No feature matches" in buttons[0].toolTip()

            self._type_filter(tab, "\"size\" = 'small'")
            assert tab.count_label.text() == "2 pages in QGIS"
            tab.show_outcome((), pages=2)
            assert tab.saved_label.text() == "2 pages saved in the cartograph"
            assert [b.isEnabled() for b in buttons] == [True, True]

            # Recovery: invalid, then corrected.
            self._type_filter(tab, '"size" =')
            assert [b.isEnabled() for b in buttons] == [False, False]
            self._type_filter(tab, "\"lot\" = 'A-1'")
            assert not tab.filter_error_label.isVisible()
            assert tab.count_label.text() == "1 page in QGIS"
            tab.show_outcome((), pages=1)
            assert [b.isEnabled() for b in buttons] == [True, True]
            assert tab.saved_label.text() == "1 page saved in the cartograph"
        finally:
            dialog.close()
            project.setFileName("")

    def test_the_advanced_expressions_sit_below_the_basic_choices(
        self, qgis_app, project, tmp_path, fake_cartograph_export
    ) -> None:
        dialog, _built = self._atlas_dialog(project, tmp_path)
        try:
            dialog.show()
            dialog._cartograph.open_section("atlas")
            tab = dialog._cartograph.atlas_tab
            qgis_app.processEvents()

            def top(widget) -> int:
                return widget.mapTo(tab, widget.rect().topLeft()).y()

            section = tab.advanced_section
            assert top(tab.coverage_combo) < top(tab.map_combo) < top(section)
            assert top(section) < top(tab.count_label) < top(tab.zip_button)
            # Opens by itself when an expression is set or wrong.
            section.set_open(False)
            tab.show_layout(tab._layout)
            assert not section.is_open()
            tab._layout.atlas().setFilterFeatures(True)
            tab._layout.atlas().setFilterExpression('"lot" =')
            tab.show_layout(tab._layout)
            assert section.is_open()
        finally:
            dialog.close()
            project.setFileName("")

    def test_a_saved_atlas_follows_the_filter_through_to_the_pages_it_pages(
        self, qgis_app, project, tmp_path
    ) -> None:
        """Real saves: invalid writes no atlas, zero matches writes an atlas
        with no pages, a subset pages those parcels only, and correcting the
        filter brings the right parcels back."""
        import os
        import re

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR")
        dialog, _built = self._atlas_dialog(project, tmp_path)
        try:
            dialog.show()
            cartograph = dialog._cartograph
            tab = cartograph.atlas_tab
            buttons = (tab.zip_button, tab.print_button)
            assert _wait_for(qgis_app, lambda: tab.zip_button.isEnabled(), timeout=60)

            def saved(expression: str):
                before = cartograph._builds_started
                self._type_filter(tab, expression)
                assert _wait_for(
                    qgis_app,
                    lambda: (
                        cartograph._builds_started > before and cartograph._job is None
                    ),
                    timeout=90,
                )
                text = cartograph._current_path().read_text(encoding="utf-8")
                atlas = re.search(r"<om-atlas[^>]*>", text)
                return atlas.group(0) if atlas else None

            assert saved('"lot" =') is None
            assert [b.isEnabled() for b in buttons] == [False, False]
            assert tab.filter_error_label.isVisible()
            assert tab.problem_label.isVisible()

            nowhere = saved("\"lot\" = 'nowhere'")
            assert nowhere is not None and 'filter="false"' in nowhere
            assert [b.isEnabled() for b in buttons] == [False, False]
            assert "no feature matches" in tab.saved_label.text()

            small = saved("\"size\" = 'small'")
            assert small is not None and "d.id == 2" in small
            assert [b.isEnabled() for b in buttons] == [True, True]
            assert tab.saved_label.text() == "2 pages saved in the cartograph"

            assert saved('"lot" =') is None
            assert [b.isEnabled() for b in buttons] == [False, False]
            fixed = saved("\"lot\" = 'A-1'")
            assert fixed is not None and "d.id == 2" in fixed
            assert [b.isEnabled() for b in buttons] == [True, True]
            assert tab.saved_label.text() == "1 page saved in the cartograph"
        finally:
            dialog.close()
            project.setFileName("")

    def test_all_pages_buttons_open_the_atlas_triggers(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        import os

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR")
        dialog, _built = self._atlas_dialog(project, tmp_path)
        try:
            cartograph = dialog._cartograph
            tab = cartograph.atlas_tab
            assert _wait_for(qgis_app, lambda: tab.zip_button.isEnabled(), timeout=60)
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            tab.zip_button.click()
            tab.print_button.click()
            # Both, in order, even when a save is still landing.
            assert _wait_for(qgis_app, lambda: len(opened) == 2, timeout=60)
            assert [url.query() for url in opened] == ["atlas=zip", "atlas=print"]
        finally:
            dialog.close()
            project.setFileName("")


class TestCartographTab:
    """The tab is the page, live, beside the layout list and its settings."""

    def test_the_preview_is_the_selected_layout(self, qgis_app, project) -> None:
        first = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        second = _add_layout(project, "Parcels", [("Main", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.layout_combo.setCurrentText("Survey")
            assert cartograph.page_preview.showing_page()
            assert cartograph.page_preview.view.currentLayout() is first
            cartograph.layout_combo.setCurrentText("Parcels")
            assert cartograph.page_preview.view.currentLayout() is second
            assert not cartograph.file_edit.isHidden()
        finally:
            dialog.close()

    def test_a_restyled_layer_redraws_the_preview(self, qgis_app, project) -> None:
        layout = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            refreshed: list[bool] = []
            layout.refreshed.connect(lambda: refreshed.append(True))
            dialog._cartograph.on_project_content_changed()
            assert _wait_for(qgis_app, lambda: refreshed)
        finally:
            dialog.close()

    def test_import_is_on_the_new_from_template_menu(
        self, qgis_app, project, monkeypatch
    ) -> None:
        """The list holds layouts only; Import is the secondary action on the
        New from template button's menu."""
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            combo = cartograph.layout_combo
            assert [combo.itemText(i) for i in range(combo.count())] == ["Survey"]
            calls: list[str] = []
            monkeypatch.setattr(
                cartograph, "on_import_cartograph", lambda: calls.append("import")
            )
            # Connected at build time, so reconnect to reach the stand-in.
            cartograph.import_action.triggered.disconnect()
            cartograph.import_action.triggered.connect(cartograph.on_import_cartograph)
            cartograph.import_action.trigger()
            assert calls == ["import"]
            assert combo.currentText() == "Survey"
            assert dialog.state.cartograph_layout == "Survey"
        finally:
            dialog.close()

    def test_change_saves_to_the_chosen_file(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            target = tmp_path / "chosen"
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "")
            )
            cartograph.file_edit.change_button.click()
            assert cartograph.file_edit.text() == str(target) + ".html"
            assert dialog.state.cartograph_files["Survey"] == str(target) + ".html"
            assert str(target) in cartograph.file_edit.label.toolTip()
        finally:
            dialog.close()

    def test_the_footer_count_is_the_web_maps_alone(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        project.addMapLayer(make_memory_layer("roads"))
        dialog = _dialog(project)
        try:
            assert "exported" in dialog.status_label.text()
            _choose_cartograph(dialog)
            assert dialog.status_label.text() == ""
            # A layer added while on Cartograph recounts the web map's layers.
            project.addMapLayer(make_memory_layer("rivers"))
            dialog._update_export_readiness()
            assert dialog.status_label.text() == ""
            _choose_web(dialog)
            assert "exported" in dialog.status_label.text()
        finally:
            dialog.close()


class TestWorkspace:
    """The cartograph is one screen - the page, and a panel beside it whose
    tabs are Layout (in sections), Atlas and Checks - in place of the web
    map's tabs. Layers and the checks are shared: tabs on the web map; on the
    cartograph, the Layers section and the Checks tab."""

    def test_cartograph_shows_the_workspace_and_no_tabs(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()
            _choose_cartograph(dialog)
            qgis_app.processEvents()
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            assert dialog._cartograph_workspace.isVisible()
            assert dialog._cartograph.page_preview.isVisible()
            assert not dialog.tabs.isVisible()
            _choose_web(dialog)
            qgis_app.processEvents()
            assert dialog.tabs.isVisible()
            assert not dialog._cartograph_workspace.isVisible()
        finally:
            dialog.close()

    def test_layers_and_checks_move_into_the_panel_and_back(
        self, qgis_app, project
    ) -> None:
        """The same two widgets each way, never copies; back on the web map
        they are tabs again where they were, twice over."""
        dialog = _dialog(project)
        try:
            layers, checks = dialog._layers_page, dialog._fidelity_page
            cartograph = dialog._cartograph
            for _round in range(2):
                assert dialog.tabs.widget(1) is dialog._layers_scroll
                assert dialog.tabs.widget(3) is checks
                _choose_cartograph(dialog)
                assert dialog.tabs.indexOf(layers) < 0
                assert dialog.tabs.indexOf(checks) < 0
                assert layers.parentWidget() is cartograph.layers_section.body
                assert checks.parentWidget() is cartograph.checks_page
                _choose_web(dialog)
                titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
                assert titles == ["Map", "Layers", "Appearance", "Fidelity"]
                assert dialog.tabs.widget(1) is dialog._layers_scroll
                assert dialog.tabs.widget(3) is checks
        finally:
            dialog.close()

    def test_the_open_section_and_tab_show_the_shared_widgets(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.open_section("layers")
            qgis_app.processEvents()
            assert dialog.layer_tree.isVisible()
            cartograph.open_section("checks")
            qgis_app.processEvents()
            assert dialog.fidelity_panel.isVisible()
        finally:
            dialog.close()

    def test_the_strip_is_hidden_on_cartograph(self, qgis_app, project) -> None:
        """The Checks tab's title says the same, so the strip would be said
        twice."""
        dialog = _dialog(project)
        try:
            dialog.show()
            assert dialog.fidelity_strip.isVisible()
            _choose_cartograph(dialog)
            assert not dialog.fidelity_strip.isVisible()
            _choose_web(dialog)
            assert dialog.fidelity_strip.isVisible()
        finally:
            dialog.close()

    def test_a_dialog_opening_on_cartograph_has_no_strip(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.settings import (
            DialogOutput,
            load_state,
            save_state,
        )

        state = load_state(project)
        state.output = DialogOutput.CARTOGRAPH
        save_state(project, state)
        dialog = _dialog(project)
        try:
            dialog.show()
            assert not dialog.fidelity_strip.isVisible()
            _choose_web(dialog)
            assert dialog.fidelity_strip.isVisible()
        finally:
            dialog.close()

    def test_which_sections_are_open_survives_a_new_dialog(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            sections = dialog._cartograph.sections
            # Page starts open, the rest closed.
            assert sections["page"].is_open()
            assert not sections["frames"].is_open()
            sections["page"].header.click()
            sections["frames"].header.click()
        finally:
            dialog.close()
        dialog = _dialog(project)
        try:
            sections = dialog._cartograph.sections
            assert not sections["page"].is_open()
            assert sections["frames"].is_open()
            assert not sections["page"].body.isVisibleTo(sections["page"])
            assert sections["frames"].body.isVisibleTo(sections["frames"])
        finally:
            dialog.close()

    def test_page_summary_is_the_theme(self, qgis_app, project) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            combo = cartograph.theme_combo
            assert combo.currentText() == "Default"
            assert cartograph.page_section.summary_text() == "Default theme"
            combo.setCurrentIndex(1)
            assert cartograph.page_section.summary_text() == (
                f"{combo.itemText(1)} theme"
            )
        finally:
            dialog.close()

    def test_frames_summary_follows_each_frames_mode(self, qgis_app, project) -> None:
        _add_layout(
            project, "Survey", [("Overview", "EPSG:4326"), ("Inset", "EPSG:4326")]
        )
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert cartograph.frames_section.summary_text() == "2 interactive maps"
            (inset,) = (
                item_id
                for item_id in cartograph._frame_rows
                if cartograph.frames_list.card(item_id).name_label.text() == "Inset"
            )
            card = cartograph.frames_list.card(inset)
            card.mode_combo.setCurrentIndex(card.mode_combo.findData("static"))
            assert (
                cartograph.frames_section.summary_text() == "1 interactive map, 1 image"
            )
            assert dialog.state.frame_choices("Survey")[inset].mode == "static"
        finally:
            dialog.close()

    def test_atlas_tab_title_turns_on_with_the_atlas(self, qgis_app, project) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            index = cartograph._tab_keys.index("atlas")

            def shown() -> tuple[str, str]:
                return tabs.tabText(index), tabs.tabToolTip(index)

            off = ("Atlas", "One page per feature: off")
            assert shown() == off
            cartograph.atlas_tab.enabled_check.setChecked(True)
            assert shown() == ("Atlas (on)", "One page per feature: on")
            cartograph.atlas_tab.enabled_check.setChecked(False)
            assert shown() == off
        finally:
            dialog.close()

    def test_checks_tab_before_any_check_and_with_nothing_to_report(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            index = cartograph._tab_keys.index("checks")

            def shown() -> tuple[str, str]:
                return tabs.tabText(index), tabs.tabToolTip(index)

            assert shown() == ("Checks", "Not checked yet.")
            # A saved cartograph whose report changes nothing.
            cartograph._last_summary = types.SimpleNamespace(change_count=0)
            cartograph._update_checks_summary()
            assert shown() == ("Checks", "No differences from the QGIS layout.")
            # The count goes in the title; the tooltip says it in words.
            cartograph._last_summary = types.SimpleNamespace(change_count=1)
            cartograph._update_checks_summary()
            assert shown() == (
                "Checks (1)",
                "1 difference from the QGIS layout to review",
            )
            cartograph._last_summary = types.SimpleNamespace(change_count=3)
            cartograph._update_checks_summary()
            assert shown() == (
                "Checks (3)",
                "3 differences from the QGIS layout to review",
            )
        finally:
            dialog.close()

    def test_checks_tab_counts_the_changes_once_saved(
        self, qgis_app, project, tmp_path
    ) -> None:
        import re

        dialog, _target = TestPrintSettings()._real_dialog(
            qgis_app, project, tmp_path, static=True
        )
        try:
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            index = cartograph._tab_keys.index("checks")
            assert _wait_for(
                qgis_app, lambda: tabs.tabToolTip(index) != "Not checked yet."
            )
            match = re.fullmatch(r"Checks \((\d+)\)", tabs.tabText(index))
            assert match is not None, tabs.tabText(index)
            count = int(match.group(1))
            assert count >= 1
            assert tabs.tabToolTip(index).startswith(f"{count} difference")
        finally:
            dialog.close()

    def test_layers_summary_counts_the_included_layers(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        project.addMapLayer(make_memory_layer("rivers", features=[("b", [3.0, 4.0])]))
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            section = dialog._cartograph.layers_section
            assert section.summary_text() == "2 of 2 layers included"
            dialog.layer_tree.topLevelItem(0).setCheckState(1, Qt.CheckState.Unchecked)
            assert section.summary_text() == "1 of 2 layers included"
        finally:
            dialog.close()

    def test_show_tab_opens_the_panel_on_cartograph_and_a_tab_on_the_web_map(
        self, qgis_app, project
    ) -> None:
        """On the cartograph, Fidelity opens the Checks tab and Layers opens
        its section, back on the Layout tab."""
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            assert not cartograph.layers_section.is_open()
            assert tabs.currentWidget() is cartograph.layout_scroll
            dialog._show_tab("Fidelity")
            assert tabs.currentWidget() is cartograph.checks_page
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            dialog._show_tab("Layers")
            assert cartograph.layers_section.is_open()
            assert tabs.currentWidget() is cartograph.layout_scroll
            assert dialog.views.currentWidget() is dialog._cartograph_workspace

            _choose_web(dialog)
            dialog._show_tab("Layers")
            assert dialog.views.currentWidget() is dialog.tabs
            assert dialog.tabs.currentWidget() is dialog._layers_scroll
            dialog._show_tab("Fidelity")
            assert dialog.tabs.currentWidget() is dialog._fidelity_page
        finally:
            dialog.close()

    def test_show_in_layers_from_a_check_opens_the_layers_section(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("roads", features=[("a", [1.0, 2.0])])
        project.addMapLayer(layer)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert not cartograph.layers_section.is_open()
            dialog.fidelity_panel.show_layer_requested.emit(layer.id())
            assert cartograph.layers_section.is_open()
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            current = dialog.layer_tree.currentItem()
            assert current is not None
            from qgis.PyQt.QtCore import Qt

            assert current.data(0, Qt.ItemDataRole.UserRole) == layer.id()
        finally:
            dialog.close()

    @pytest.mark.parametrize("output", ["web", "cartograph"])
    def test_escape_on_settings_or_help_goes_back_not_away(
        self, qgis_app, project, output
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        dialog = _dialog(project)
        try:
            if output == "cartograph":
                _choose_cartograph(dialog)
            dialog.show()
            home = dialog.views.currentWidget()
            for button in (dialog.settings_button, dialog.help_button):
                button.click()
                QTest.keyClick(dialog, Qt.Key.Key_Escape)
                assert dialog.isVisible()
                assert dialog.views.currentWidget() is home
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            assert not dialog.isVisible()
        finally:
            dialog.close()

    def test_at_the_minimum_size_the_panel_scrolls_instead_of_overlapping(
        self, qgis_app, project
    ) -> None:
        """720 x 320 is the dialog's floor (Windows must be able to shrink
        it); the panel column scrolls there, and only there."""
        from nika_onlymap_exporter.ui.main_dialog import (
            MINIMUM_DIALOG_HEIGHT,
            MINIMUM_DIALOG_WIDTH,
        )

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.layout_combo.setCurrentText("Survey")
            dialog.show()
            bar = cartograph.panel_scroll.verticalScrollBar()

            def overlapping() -> bool:
                tabs, footer = cartograph.panel_tabs, cartograph.file_edit
                return tabs.geometry().intersects(footer.geometry())

            dialog.resize(MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT)
            # The panel re-measures itself in a zero-delay step.
            for _ in range(5):
                qgis_app.processEvents()
            assert dialog.height() == MINIMUM_DIALOG_HEIGHT
            assert not overlapping()
            assert bar.maximum() > 0
            dialog.resize(1152, 768)
            for _ in range(5):
                qgis_app.processEvents()
            assert not overlapping()
            assert bar.maximum() == 0
        finally:
            dialog.close()

    @pytest.mark.parametrize("output", ["web", "cartograph"])
    def test_settings_and_help_open_beside_and_close_back(
        self, qgis_app, project, output
    ) -> None:
        """Each button opens its page; pressing it again, or Back, returns
        to the output's own screen."""
        dialog = _dialog(project)
        try:
            if output == "cartograph":
                _choose_cartograph(dialog)
                home = dialog._cartograph_workspace
            else:
                home = dialog.tabs
            assert dialog.views.currentWidget() is home

            dialog.settings_button.click()
            assert dialog.views.currentWidget() is dialog._aside
            assert dialog._aside_stack.currentWidget() is dialog._settings_page
            assert dialog.aside_heading.text() == "Settings"
            assert dialog.settings_button.isChecked()
            assert not dialog.help_button.isChecked()

            dialog.help_button.click()
            assert dialog.views.currentWidget() is dialog._aside
            assert dialog._aside_stack.currentWidget() is dialog._help_page
            assert dialog.aside_heading.text() == "Help"
            assert dialog.help_button.isChecked()
            assert not dialog.settings_button.isChecked()

            dialog.help_button.click()
            assert dialog.views.currentWidget() is home
            assert not dialog.help_button.isChecked()

            dialog.settings_button.click()
            dialog.aside_back.click()
            assert dialog.views.currentWidget() is home
            assert not dialog.settings_button.isChecked()

            dialog._show_tab("Help")
            assert dialog._aside_stack.currentWidget() is dialog._help_page
            assert dialog.help_button.isChecked()
        finally:
            dialog.close()

    def test_switching_output_closes_settings(self, qgis_app, project) -> None:
        dialog = _dialog(project)
        try:
            dialog.settings_button.click()
            _choose_cartograph(dialog)
            assert dialog.views.currentWidget() is dialog._cartograph_workspace
            assert not dialog.settings_button.isChecked()
            dialog.help_button.click()
            _choose_web(dialog)
            assert dialog.views.currentWidget() is dialog.tabs
            assert not dialog.help_button.isChecked()
        finally:
            dialog.close()

    def test_layers_and_checks_go_compact_on_cartograph_only(
        self, qgis_app, project
    ) -> None:
        """The panel is a third of the dialog wide: the rows that share a
        line on the web map stack, and the search box asks for less room."""
        from qgis.PyQt.QtWidgets import QBoxLayout, QHeaderView

        from nika_onlymap_exporter.ui.fidelity_panel import (
            COMPACT_SEARCH_WIDTH,
            DETAIL_COLUMN,
            SEARCH_WIDTH,
        )

        across = QBoxLayout.Direction.LeftToRight
        down = QBoxLayout.Direction.TopToBottom
        dialog = _dialog(project)
        try:
            panel = dialog.fidelity_panel
            header = dialog.layer_tree.header()

            def state():
                return (
                    dialog._bulk_row.direction(),
                    panel._top.direction(),
                    panel.search.minimumWidth(),
                    header.sectionResizeMode(1),
                    panel.tree.isColumnHidden(DETAIL_COLUMN),
                )

            web = (
                across,
                across,
                SEARCH_WIDTH,
                QHeaderView.ResizeMode.Interactive,
                False,
            )
            compact = (
                down,
                down,
                COMPACT_SEARCH_WIDTH,
                QHeaderView.ResizeMode.ResizeToContents,
                True,
            )
            assert state() == web
            _choose_cartograph(dialog)
            assert state() == compact
            _choose_web(dialog)
            assert state() == web
        finally:
            dialog.close()


class TestPanel:
    """The panel beside the page: the layout list with its Rename... button,
    then the Layout, Atlas and Checks tabs, and its first width."""

    def test_settings_hide_the_outputs_actions_and_back_restores_them(
        self, qgis_app, project
    ) -> None:
        """Preview, Share and Export mean nothing on Settings or Help."""
        dialog = _dialog(project)
        try:
            dialog.show()
            corner = dialog.active_actions()
            assert corner.isVisibleTo(dialog)
            dialog.settings_button.click()
            assert not dialog._web_corner.isVisibleTo(dialog)
            assert not dialog._cartograph.build_corner().isVisibleTo(dialog)
            assert dialog.aside_heading.text() == "Settings"
            assert dialog.aside_back.property("role") == "asideBack"
            assert dialog.aside_back.accessibleName() == "Back"
            dialog.aside_back.click()
            assert dialog._web_corner.isVisibleTo(dialog)
            _choose_cartograph(dialog)
            dialog.help_button.click()
            assert not dialog._cartograph.build_corner().isVisibleTo(dialog)
            dialog.aside_back.click()
            assert dialog._cartograph.build_corner().isVisibleTo(dialog)
            assert not dialog._web_corner.isVisibleTo(dialog)
        finally:
            dialog.close()

    def test_the_title_is_the_switcher_alone_and_the_preview_says_html(
        self, qgis_app, project
    ) -> None:
        from qgis.PyQt.QtWidgets import QLabel

        dialog = _dialog(project)
        try:
            row = dialog.output_switcher.parentWidget()
            assert not any(
                label.text() == "Create:" for label in row.findChildren(QLabel)
            )
            assert dialog.output_switcher.toolTip() == "Choose what to make"
            preview = dialog._cartograph.cartograph_preview_button
            assert preview.text() == "Preview in browser"
        finally:
            dialog.close()

    def test_the_change_count_beside_the_page_opens_checks(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.show()
            _choose_cartograph(dialog)
            pane = dialog._cartograph.page_pane
            pane.set_change_count(0)
            assert not pane.changes_link.isVisibleTo(dialog)
            pane.set_change_count(2)
            assert pane.changes_link.isVisibleTo(dialog)
            assert pane.changes_link.text() == "2 changes in the HTML"
            pane.set_change_count(1)
            assert pane.changes_link.text() == "1 change in the HTML"
            dialog._cartograph.panel_tabs.setCurrentIndex(0)
            pane.changes_link.click()
            assert dialog._cartograph.panel_tabs.currentWidget() is (
                dialog._cartograph.checks_page
            )
        finally:
            dialog.close()

    def test_no_control_is_ringed_when_the_dialog_opens(
        self, qgis_app, project
    ) -> None:
        """The focus starts on the screen, not on its first control, whose
        ring read as something selected."""
        dialog = _dialog(project)
        try:
            dialog.show()
            dialog.activateWindow()
            _wait_for(qgis_app, lambda: False, timeout=0.2)
            assert dialog.views.hasFocus() or dialog.focusWidget() is dialog.views
        finally:
            dialog.close()

    def test_the_dialog_opens_a_fifth_larger_when_the_screen_allows(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.ui.main_dialog import (
            DEFAULT_DIALOG_HEIGHT,
            DEFAULT_DIALOG_WIDTH,
        )

        assert (DEFAULT_DIALOG_WIDTH, DEFAULT_DIALOG_HEIGHT) == (1152, 768)
        dialog = _dialog(project)
        try:
            screen = dialog.screen().availableGeometry()
            assert dialog.width() == min(1152, max(720, int(screen.width() * 0.88)))
            assert dialog.height() == min(768, max(320, int(screen.height() * 0.85)))
        finally:
            dialog.close()

    def test_the_share_menu_names_nika_rows_for_what_they_do(
        self, qgis_app, project
    ) -> None:
        """Same order and words as the web map's: file and export rows, then
        the hand-offs (greyed out for now) - no hosting row, which is for web
        maps only - and a greyed row says what it needs instead of looking
        broken."""
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            rows, section = [], None
            for action in cartograph.cartograph_share_button.menu().actions():
                if action.isSeparator():
                    section = action.text() or None
                    continue
                rows.append((section, action.text()))
            texts = [text for _section, text in rows]
            assert ("NIKA hand-off", "Prepare enhancement prompt...") in rows
            assert ("NIKA hand-off", "Prepare critique prompt...") in rows
            assert texts.index("Export PDF...") < texts.index(
                "Prepare enhancement prompt..."
            )
            assert texts[-1] == "Prepare critique prompt..."
            actions = cartograph.cartograph_share_actions
            # No layout yet: greyed, with what it needs.
            assert not actions["export_pdf"].isEnabled()
            assert "print layout" in actions["export_pdf"].toolTip()
            assert not actions["export_qpt"].isEnabled()
            assert actions["export_qpt"].toolTip() == "Choose a print layout first"
        finally:
            dialog.close()

    def test_every_tab_fits_the_narrowest_panel(self, qgis_app, project) -> None:
        """Nothing runs off the panel's edge at its minimum width: the Atlas
        form and the Checks filters once did, cutting their controls off."""
        from qgis.PyQt.QtWidgets import QScrollArea

        from nika_onlymap_exporter.ui.cartograph.controller import PANEL_MIN_WIDTH

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.resize(900, 600)
            dialog.show()
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            for section in cartograph.sections.values():
                section.set_open(True)
            workspace = cartograph.workspace
            workspace.setSizes([workspace.width() - PANEL_MIN_WIDTH, PANEL_MIN_WIDTH])
            qgis_app.processEvents()
            panel = workspace.widget(1)
            assert panel.minimumSizeHint().width() <= PANEL_MIN_WIDTH
            tabs = cartograph.panel_tabs
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                qgis_app.processEvents()
                page = tabs.widget(index)
                if isinstance(page, QScrollArea):
                    needed = page.widget().minimumSizeHint().width()
                    room = page.viewport().width()
                else:
                    needed, room = page.minimumSizeHint().width(), page.width()
                assert needed <= room, tabs.tabText(index)
            # Measured with the test's font, the Atlas form fits either way;
            # with a wider system font, side-by-side labels ran off the edge.
            from qgis.PyQt.QtWidgets import QFormLayout

            atlas = cartograph.atlas_tab
            forms = atlas.settings_box.findChildren(QFormLayout)
            assert forms
            for form in forms:
                assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
            assert atlas.zip_button.y() < atlas.print_button.y()
        finally:
            dialog.close()

    def test_the_tabs_are_layout_atlas_checks_edit(self, qgis_app, project) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            titles = [tabs.tabText(i) for i in range(tabs.count())]
            assert titles == ["Layout", "Atlas", "Checks", "Edit"]
            assert cartograph._tab_keys == ("layout", "atlas", "checks", "edit")
            assert tabs.widget(0) is cartograph.layout_scroll
            assert tabs.widget(1).widget() is cartograph.atlas_tab
            assert tabs.widget(2) is cartograph.checks_page
            assert tabs.widget(3).widget() is cartograph.page_pane.controls
            # The Layout tab's sections, and no others.
            assert list(cartograph.sections) == ["page", "frames", "layers"]
        finally:
            dialog.close()

    def test_open_section_picks_a_tab_or_a_layout_section(
        self, qgis_app, project
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            tabs = cartograph.panel_tabs
            page = cartograph.sections["page"]
            page.set_open(False)
            cartograph.open_section("atlas")
            assert tabs.currentIndex() == 1
            cartograph.open_section("checks")
            assert tabs.currentIndex() == 2
            assert not page.is_open()
            cartograph.open_section("page")
            assert tabs.currentIndex() == 0
            assert page.is_open()
        finally:
            dialog.close()

    def test_the_panel_scroll_areas_take_no_focus(self, qgis_app, project) -> None:
        """The controls inside are the Tab stops; a focused scroll area shows
        nothing."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QScrollArea

        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            scrolls = [cartograph.layout_scroll, cartograph.panel_tabs.widget(1)]
            for scroll in scrolls:
                assert isinstance(scroll, QScrollArea)
                assert scroll.focusPolicy() == Qt.FocusPolicy.NoFocus
        finally:
            dialog.close()

    def test_page_section_groups_the_browser_only_fields(
        self, qgis_app, project
    ) -> None:
        dialog = _dialog(project)
        try:
            cartograph = dialog._cartograph
            section = cartograph.page_section
            assert section.header.text() == "Browser appearance"
            note = cartograph.page_page_note
            assert "web page" in note.text()
            assert "not the layout preview" in note.text()
            body = section.body_layout
            positions = {
                body.itemAt(i).widget() or body.itemAt(i).layout(): i
                for i in range(body.count())
            }
            fields = [item for item in positions if not hasattr(item, "text")]
            assert fields
            assert positions[note] < min(positions[f] for f in fields)
            assert cartograph.cartograph_preview_button.text() == "Preview in browser"
        finally:
            dialog.close()

    def test_layers_help_describes_include_for_the_active_output(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.settings import DialogOutput

        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            tip = dialog.layers_info_button.toolTip()
            assert "selected map frame" in tip
            assert "data is not written" not in tip
            dialog.output_switcher.actions_by_output[DialogOutput.WEB_MAP].trigger()
            tip = dialog.layers_info_button.toolTip()
            assert "data is not written" in tip
            assert "map frame" not in tip
        finally:
            dialog.close()

    def test_each_tab_has_one_scrolling_surface_in_the_smallest_window(
        self, qgis_app, project
    ) -> None:
        from qgis.PyQt.QtWidgets import QScrollArea

        from nika_onlymap_exporter.core.export_ir import FidelityItem, FidelityStatus
        from nika_onlymap_exporter.core.fidelity_summary import summarise
        from nika_onlymap_exporter.ui.main_dialog import (
            MINIMUM_DIALOG_HEIGHT,
            MINIMUM_DIALOG_WIDTH,
        )

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph.layout_combo.setCurrentText("Survey")
            dialog.show()
            rows = [
                FidelityItem(f"Symbology {n}", FidelityStatus.APPROXIMATED, "x.", "r")
                for n in range(8)
            ]
            dialog._fidelity_page.show_summary(summarise(rows, {"r": "roads"}))
            for size in ((MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT), (1000, 520)):
                dialog.resize(*size)
                for index in range(cartograph.panel_tabs.count()):
                    cartograph.panel_tabs.setCurrentIndex(index)
                    for _ in range(20):
                        qgis_app.processEvents()
                    surfaces = [
                        area
                        for area in dialog.findChildren(QScrollArea)
                        if area.isVisible()
                        and area.height() > 40
                        and area.verticalScrollBar().maximum() > 0
                    ]
                    assert len(surfaces) <= 1, (size, index, len(surfaces))
            dialog.resize(MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT)
            for _ in range(20):
                qgis_app.processEvents()
            status = cartograph.cartograph_status_label
            assert status.isVisible()
            spot = status.mapTo(dialog, status.rect().bottomLeft())
            assert 0 <= spot.y() <= dialog.height()
            assert not cartograph.panel_scroll.isAncestorOf(status)
        finally:
            dialog.close()

    def test_the_tabs_keep_room_in_a_short_window(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.ui.cartograph.controller import (
            PANEL_TABS_MIN_HEIGHT,
        )

        dialog = _dialog(project)
        try:
            tabs = dialog._cartograph.panel_tabs
            assert tabs.minimumHeight() == PANEL_TABS_MIN_HEIGHT
        finally:
            dialog.close()

    def test_layers_summary_says_layer_for_one(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            section = dialog._cartograph.layers_section
            assert section.summary_text() == "1 of 1 layer included"
            dialog.layer_tree.topLevelItem(0).setCheckState(1, Qt.CheckState.Unchecked)
            assert section.summary_text() == "0 of 1 layer included"
        finally:
            dialog.close()

    @staticmethod
    def _stand_ins(monkeypatch) -> list[str]:
        """The controller's two ways to make a layout, recorded instead of
        run. Patched on the class before the dialog is built, since the
        menu connects to the bound methods then."""
        from nika_onlymap_exporter.ui.cartograph.controller import (
            CartographController,
        )

        calls: list[str] = []
        monkeypatch.setattr(
            CartographController,
            "create_from_canvas",
            lambda self, *_args: calls.append("canvas"),
        )
        monkeypatch.setattr(
            CartographController,
            "on_import_cartograph",
            lambda self, *_args: calls.append("import"),
        )
        return calls

    def test_the_button_has_a_menu_arrow_carrying_import(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QToolButton

        calls = self._stand_ins(monkeypatch)
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            button = cartograph.new_from_template_button
            assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
            (action,) = button.menu().actions()
            assert action.text() == "Import cartograph..."
            assert action is cartograph.import_action
            # Import needs no canvas.
            assert not button.isEnabled() and action.isEnabled()
            action.trigger()
            assert calls == ["import"]
        finally:
            dialog.close()

    def test_new_layout_from_template_is_always_in_sight(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        calls = self._stand_ins(monkeypatch)
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog, _canvas = TestCanvasAndImport()._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            button = cartograph.new_from_template_button
            # Named for what it does: another layout, never a change of the
            # one shown.
            assert button.text() == "New from template..."
            assert "kept as it is" in button.toolTip()
            assert button.isEnabled() and not button.isHidden()
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
            # The keyboard reaches it: Space presses the focused button.
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert calls == ["canvas"]
            # A click on its label side does the same.
            _click_new_from_template(dialog)
            assert calls == ["canvas", "canvas"]
        finally:
            dialog.close()

    def test_new_from_template_opens_the_picker_and_adds_a_layout(
        self, qgis_app, project, picked_template
    ) -> None:
        original = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog, _canvas = TestCanvasAndImport()._canvas_dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph._select_layout("Survey")
            before = len(project.layoutManager().layouts())
            _click_new_from_template(dialog)
            assert picked_template.shown == 1
            layouts = project.layoutManager().layouts()
            assert len(layouts) == before + 1
            assert project.layoutManager().layoutByName("Survey") is original
            assert original.itemById("Overview") is not None
        finally:
            dialog.close()

    def test_new_layout_from_template_without_a_canvas_says_why(
        self, qgis_app, project, monkeypatch
    ) -> None:
        self._stand_ins(monkeypatch)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            button = dialog._cartograph.new_from_template_button
            assert not button.isEnabled()
            assert "no map canvas" in button.toolTip()
        finally:
            dialog.close()

    def test_show_in_folder_reveals_the_cartograph(
        self, qgis_app, project, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.controller import (
            CartographController,
        )

        revealed: list[bool] = []
        monkeypatch.setattr(
            CartographController,
            "on_reveal",
            lambda self, *_args: revealed.append(True),
        )
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            save = dialog._cartograph.file_edit
            assert save.text()
            assert not save.reveal_button.isHidden()
            save.reveal_button.click()
            assert revealed == [True]
        finally:
            dialog.close()

    @staticmethod
    def _panel_width(dialog) -> int:
        return dialog._cartograph.workspace.sizes()[1]

    @pytest.mark.parametrize(
        ("window_width", "panel_width"), [(1000, 350), (1600, 430)]
    )
    def test_the_first_width_follows_the_window(
        self, qgis_app, project, window_width, panel_width
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.controller import PANEL_WIDE_WINDOW

        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.resize(window_width, 700)
            dialog.show()
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert _wait_for(qgis_app, lambda: cartograph._panel_sized)
            workspace_width = cartograph.workspace.width()
            assert (workspace_width < PANEL_WIDE_WINDOW) == (panel_width == 350)
            assert self._panel_width(dialog) == panel_width
        finally:
            dialog.close()

    def test_the_first_width_is_set_once(self, qgis_app, project) -> None:
        """After that the width is the user's, through switching away and
        back."""
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        try:
            dialog.resize(1000, 700)
            dialog.show()
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            assert _wait_for(qgis_app, lambda: cartograph._panel_sized)
            total = sum(cartograph.workspace.sizes())
            cartograph.workspace.setSizes([total - 500, 500])
            dialog.resize(1600, 700)
            _choose_web(dialog)
            _choose_cartograph(dialog)
            for _ in range(10):
                qgis_app.processEvents()
            cartograph.fit_panel_to_window()
            assert self._panel_width(dialog) == 500
        finally:
            dialog.close()


class TestDesignerEditsAutosave:
    """The layout designer's edits are undo commands: they never emit the
    layout's `changed`, so the controller follows the undo stack and added
    items as well."""

    def _ready(self, project, qgis_app):
        from qgis.core import QgsLayoutItemLabel, QgsLayoutItemMap

        layout = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        label = QgsLayoutItemLabel(layout)
        layout.addLayoutItem(label)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        map_item = next(i for i in layout.items() if isinstance(i, QgsLayoutItemMap))
        cartograph._autosave_timer.stop()
        return dialog, cartograph, layout, label, map_item

    @staticmethod
    def _command(layout, item, change) -> None:
        layout.undoStack().beginCommand(item, "designer edit")
        change()
        layout.undoStack().endCommand()

    @staticmethod
    def _counts(cartograph, action) -> tuple[int, bool]:
        cartograph._autosave_timer.stop()
        before = cartograph._edits_made
        action()
        return cartograph._edits_made - before, cartograph._autosave_timer.isActive()

    def test_a_label_text_edit_and_a_map_move_count(self, qgis_app, project) -> None:
        dialog, cartograph, layout, label, map_item = self._ready(project, qgis_app)
        try:
            made, started = self._counts(
                cartograph,
                lambda: self._command(layout, label, lambda: label.setText("New")),
            )
            assert made >= 1 and started
            made, started = self._counts(
                cartograph,
                lambda: self._command(
                    layout, map_item, lambda: map_item.attemptMoveBy(5, 5)
                ),
            )
            assert made >= 1 and started
        finally:
            dialog.close()

    def test_adding_removing_undoing_and_redoing_each_count(
        self, qgis_app, project
    ) -> None:
        from qgis.core import QgsLayoutItemLabel

        dialog, cartograph, layout, _label, _map = self._ready(project, qgis_app)
        try:
            extra = QgsLayoutItemLabel(layout)
            for step, action in (
                ("add", lambda: layout.addLayoutItem(extra)),
                ("remove", lambda: layout.removeLayoutItem(extra)),
                ("undo", lambda: layout.undoStack().stack().undo()),
                ("redo", lambda: layout.undoStack().stack().redo()),
            ):
                made, started = self._counts(cartograph, action)
                assert made >= 1 and started, step
        finally:
            dialog.close()

    def test_deleting_the_shown_layout_leaves_the_dialog_running(
        self, qgis_app, project
    ) -> None:
        """Deleting a layout clears its undo stack as it is destroyed, which
        moves the index the tab watches: under PyQt6 a slot that touches the
        half-destroyed layout aborts QGIS."""
        dialog, cartograph, layout, _label, _map = self._ready(project, qgis_app)
        try:
            project.layoutManager().removeLayout(layout)
            qgis_app.processEvents()
            assert cartograph._current_layout is None
        finally:
            dialog.close()

    def test_a_drag_in_the_page_preview_autosaves_and_undoes(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """The page preview edits through QGIS's select tool: the move is an
        undo command, so the autosave follows it and so does an undo."""
        from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QMouseEvent
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication

        dialog, cartograph, layout, _label, map_item = self._ready(project, qgis_app)
        try:
            dialog.resize(1200, 800)
            dialog.show()
            assert _wait_for(qgis_app, lambda: cartograph.page_pane.preview.isVisible())
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            cartograph._autosave_timer.stop()
            fake_cartograph_export.calls.clear()
            view = cartograph.page_pane.preview.view
            viewport = view.viewport()
            centre = view.mapFromScene(map_item.sceneBoundingRect().center())
            before = map_item.pos()
            stack = layout.undoStack().stack()
            count = stack.count()
            QTest.mouseMove(viewport, centre)
            QTest.mousePress(
                viewport,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                centre,
            )
            for step in range(1, 9):
                # Sent with the button held: Qt 5's QTest.mouseMove has none.
                point = centre + QPoint(step * 8, step * 5)
                QApplication.sendEvent(
                    viewport,
                    QMouseEvent(
                        QEvent.Type.MouseMove,
                        QPointF(point),
                        QPointF(viewport.mapToGlobal(point)),
                        Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                qgis_app.processEvents()
            QTest.mouseRelease(
                viewport,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                centre + QPoint(64, 40),
            )
            assert map_item.pos() != before
            assert stack.count() == count + 1
            assert cartograph._autosave_timer.isActive()
            assert _wait_for(qgis_app, lambda: target.exists())
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            assert len(fake_cartograph_export.calls) == 1
            stack.undo()
            assert map_item.pos() == before
            assert cartograph._autosave_timer.isActive()
        finally:
            dialog.close()

    def test_adding_editing_and_deleting_in_the_preview_autosave(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        """The preview's Add menu, selected-item controls and Delete are undo
        commands on the layout: the autosave follows each, and the controls
        share the Map frames list's choices."""
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtTest import QTest

        dialog, cartograph, layout, _label, map_item = self._ready(project, qgis_app)
        try:
            dialog.resize(1200, 800)
            dialog.show()
            pane = cartograph.page_pane
            assert _wait_for(qgis_app, lambda: pane.preview.isVisible())
            assert pane.controls._frames is cartograph.frames_list
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            pane._fill_add_menu()
            text = next(a for a in pane.add_menu.actions() if a.text() == "Text")
            made, started = self._counts(cartograph, text.trigger)
            assert made == 0 and pane.preview.placer.active
            viewport = pane.preview.view.viewport()
            point = pane.preview.view.mapFromScene(QPointF(150, 150))
            made, started = self._counts(
                cartograph,
                lambda: QTest.mouseClick(
                    viewport,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                    point,
                ),
            )
            assert made >= 1 and started
            added = [i for i in layout.items() if isinstance(i, QgsLayoutItemLabel)]
            fresh = next(i for i in added if i is not _label)
            edit = pane.controls.text_edit
            edit.selectAll()
            QTest.keyClicks(edit, "Added in the preview")
            made, started = self._counts(
                cartograph, lambda: QTest.keyClick(edit, Qt.Key.Key_Return)
            )
            assert made >= 1 and started and fresh.text() == "Added in the preview"
            made, started = self._counts(cartograph, pane.controls.delete_button.click)
            assert made >= 1 and started and fresh not in layout.items()
            layout.deselectAll()
            map_item.setSelected(True)
            qgis_app.processEvents()
            mode = pane.controls.mode_combo
            assert pane.controls.pages.currentWidget() is pane.controls.map_page
            mode.setCurrentIndex(1)
            made, started = self._counts(cartograph, lambda: mode.activated.emit(1))
            assert made >= 1 and started
            card = cartograph.frames_list.card(map_item.uuid())
            assert card.mode() == "static"
            assert (
                cartograph.state.frame_choices(cartograph.state.cartograph_layout)[
                    map_item.uuid()
                ].mode
                == "static"
            )
        finally:
            dialog.close()

    def test_a_burst_of_edits_is_one_build(
        self, qgis_app, project, fake_cartograph_export, tmp_path
    ) -> None:
        dialog, cartograph, layout, label, _map = self._ready(project, qgis_app)
        try:
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            cartograph._autosave_timer.stop()
            for index in range(5):
                self._command(layout, label, lambda i=index: label.setText(f"t{i}"))
            assert _wait_for(qgis_app, lambda: target.exists())
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            assert len(fake_cartograph_export.calls) == 1
        finally:
            dialog.close()

    def test_the_controllers_own_export_is_not_an_edit(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        import nika_onlymap_exporter.core.cartograph_export as export_module

        dialog, cartograph, layout, label, _map = self._ready(project, qgis_app)
        try:
            target = tmp_path / "survey.html"
            cartograph.file_edit.setText(str(target))
            prepare = export_module.prepare_cartograph

            def mutating(*args, **kwargs):
                # An export that touches the layout through the undo stack.
                self._command(layout, label, lambda: label.setText("scratch"))
                layout.changed.emit()
                return prepare(*args, **kwargs)

            monkeypatch.setattr(export_module, "prepare_cartograph", mutating)
            cartograph._autosave_timer.stop()
            before = cartograph._edits_made
            cartograph._run_autosave()
            assert _wait_for(qgis_app, lambda: target.exists())
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            assert cartograph._edits_made == before
            assert not cartograph._autosave_timer.isActive()
        finally:
            dialog.close()


class TestRenamingALayout:
    """A rename in QGIS keeps what was set under the old name."""

    def _two(self, project):
        _add_layout(project, "First", [("Map", "EPSG:4326")])
        second = _add_layout(project, "Second", [("Map", "EPSG:4326")])
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph.layout_combo.setCurrentText("Second")
        return dialog, cartograph, second

    def test_the_second_layout_keeps_its_settings_and_selection(
        self, qgis_app, project, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import FrameChoice, load_state

        dialog, cartograph, second = self._two(project)
        try:
            target = tmp_path / "second.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            cartograph.title_edit.setText("Kept title")
            cartograph.title_edit.textEdited.emit("Kept title")
            state = dialog.state
            state.frame_choices("Second")["Map"] = FrameChoice(mode="static", dpi=300)
            state.cartograph_print["Second"] = '{"bleed": "3mm"}'
            cartograph._autosave_timer.stop()

            second.setName("Renamed")

            assert cartograph.layout_combo.current_layout() == "Renamed"
            assert state.cartograph_layout == "Renamed"
            assert cartograph.file_edit.text() == str(target)
            assert state.cartograph_files["Renamed"] == str(target)
            assert state.page_choice("Renamed").title == "Kept title"
            assert state.frame_choices("Renamed")["Map"].dpi == 300
            assert state.cartograph_print["Renamed"] == '{"bleed": "3mm"}'
            for kept in (
                state.cartograph_files,
                state.cartograph_frames,
                state.cartograph_print,
                state.cartograph_page,
            ):
                assert "Second" not in kept
            # Reopening the project reads them back under the new name.
            reopened = load_state(project)
            assert reopened.cartograph_layout == "Renamed"
            assert reopened.cartograph_files["Renamed"] == str(target)
            assert reopened.cartograph_print["Renamed"] == '{"bleed": "3mm"}'
            assert reopened.page_choice("Renamed").title == "Kept title"
            assert reopened.frame_choices("Renamed")["Map"].dpi == 300
        finally:
            dialog.close()

    def test_a_build_in_flight_still_lands_and_runs_the_queued_page(
        self, qgis_app, project, fake_cartograph_export, tmp_path, monkeypatch
    ) -> None:
        import threading

        import nika_onlymap_exporter.core.cartograph_export as export_module

        gate = threading.Event()
        finish = export_module.finish_cartograph_full

        def gated(*args, **kwargs):
            gate.wait(20)
            return finish(*args, **kwargs)

        monkeypatch.setattr(export_module, "finish_cartograph_full", gated)
        dialog, cartograph, second = self._two(project)
        try:
            opened = []
            monkeypatch.setattr(cartograph, "_open_url", opened.append)
            target = tmp_path / "second.html"
            target.write_text("<html>earlier save</html>")
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            cartograph._autosave_timer.stop()
            cartograph._run_autosave()
            assert cartograph._job is not None
            cartograph.on_export_png()  # queued behind the build

            second.setName("Renamed")
            gate.set()

            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            cartograph._autosave_timer.stop()
            assert target.exists()
            assert cartograph._last_saved_at is not None
            assert cartograph._edits_saved >= 1
            assert _wait_for(qgis_app, lambda: opened)
            assert cartograph.layout_combo.current_layout() == "Renamed"
        finally:
            gate.set()
            dialog.close()


@pytest.mark.parametrize(
    ("project", "layout", "expected"),
    [
        ("", "CON", "CON-map.html"),
        ("parcels.qgz", "con.txt", "parcels-con.txt-map.html"),
        ("parcels.qgz", "Survey", "parcels-Survey.html"),
    ],
)
def test_default_name_avoids_windows_device_names(
    tmp_path: Path, project: str, layout: str, expected: str
) -> None:
    from nika_onlymap_exporter.ui.cartograph.controller import (
        default_cartograph_path,
    )

    path = default_cartograph_path(project, layout, tmp_path)
    assert path == tmp_path / expected


class TestLayerDisclosure:
    """The arrow on a layer in the Layers list, on the web map and inside the
    Cartograph panel, at a compact and a normal dialog size."""

    @staticmethod
    def _open(qgis_app, project, make_memory_layer, mode, size):
        # Added first, so listed above it: the layer under test sits at the
        # bottom, where its settings start below the visible part of a short
        # list.
        for name in ("Rivers", "Roads", "Towns"):
            project.addMapLayer(make_memory_layer(name))
        project.addMapLayer(
            make_memory_layer(
                "Viewpoints",
                fields="name:string&field=value:integer",
                features=[("a", [1.0, 2.0])],
            )
        )
        if mode == "cartograph":
            _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = _dialog(project)
        dialog.resize(*size)
        dialog.show()
        if mode == "cartograph":
            _choose_cartograph(dialog)
            dialog._cartograph.open_section("layers")
        else:
            dialog.tabs.setCurrentWidget(dialog._layers_scroll)
        for _ in range(5):
            qgis_app.processEvents()
        return dialog

    @pytest.mark.parametrize("mode", ["web", "cartograph"])
    @pytest.mark.parametrize("size", [(720, 320), (1000, 700)])
    @pytest.mark.parametrize("how", ["mouse", "right", "plus"])
    def test_expanding_shows_whole_compact_rows(
        self, qgis_app, project, make_memory_layer, mode, size, how
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QBoxLayout, QComboBox, QWidget

        dialog = self._open(qgis_app, project, make_memory_layer, mode, size)
        try:
            tree = dialog.layer_tree
            layer = tree.topLevelItem(tree.topLevelItemCount() - 1)
            assert layer.text(0) == "Viewpoints"
            assert not layer.isExpanded()
            if mode == "web":
                # A short window scrolls the tab; the user scrolls it to the list.
                dialog._layers_scroll.ensureWidgetVisible(tree)
            tree.scrollToItem(layer)  # as a user scrolls to the layer first
            for _ in range(3):
                qgis_app.processEvents()
            if how == "mouse":
                # The branch arrow sits in the indentation left of the item's rect.
                rect = tree.visualItemRect(layer)
                QTest.mouseClick(
                    tree.viewport(),
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                    QPoint(rect.left() - tree.indentation() // 2, rect.center().y()),
                )
            else:
                tree.setCurrentItem(layer)
                tree.setFocus()
                QTest.keyClick(
                    tree, Qt.Key.Key_Right if how == "right" else Qt.Key.Key_Plus
                )
            for _ in range(5):
                qgis_app.processEvents()
            assert layer.isExpanded()
            viewport = tree.viewport().rect()
            first_row = tree.visualItemRect(layer.child(0))
            assert viewport.height() >= 150, "the list keeps a usable height"
            assert viewport.contains(first_row.topLeft()), "first row in view"
            for index in range(layer.childCount()):
                child = layer.child(index)
                row = tree.itemWidget(child, 0)
                if row is None:
                    continue
                controls = [w for w in row.findChildren(QWidget) if w.isVisibleTo(row)]
                # A combo's popup list reports ~190 px; the row is the
                # controls it holds, one line each when stacked.
                heights = [
                    c.sizeHint().height() for c in controls if c.parentWidget() is row
                ]
                stacked = row.layout().direction() == QBoxLayout.Direction.TopToBottom
                needed = sum(heights) if stacked else max(heights)
                assert needed <= row.height() <= needed + 24, (
                    index,
                    row.height(),
                    needed,
                )
                for combo in row.findChildren(QComboBox):
                    left = combo.mapTo(tree.viewport(), combo.rect().topLeft()).x()
                    right = left + combo.width()
                    # Squeezed to a stub ("Pop") is as cut off as one that
                    # runs past the edge.
                    assert combo.width() >= min(
                        combo.sizeHint().width(), viewport.width() - 60
                    ), (index, combo.width(), combo.sizeHint().width())
                    assert left >= 0 and right <= viewport.right() + 1, (
                        index,
                        left,
                        right,
                        viewport.width(),
                    )
        finally:
            dialog.close()

    @pytest.mark.parametrize("mode", ["web", "cartograph"])
    @pytest.mark.parametrize("size", [(720, 320), (1000, 700)])
    def test_a_raster_row_has_no_arrow_and_its_boxes_line_up(
        self, qgis_app, project, make_memory_layer, make_raster_layer, mode, size
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest

        project.addMapLayer(make_raster_layer("Elevation"))
        dialog = self._open(qgis_app, project, make_memory_layer, mode, size)
        try:
            tree = dialog.layer_tree
            rows = {
                tree.topLevelItem(i).text(0): tree.topLevelItem(i)
                for i in range(tree.topLevelItemCount())
            }
            raster, vector = rows["Elevation"], rows["Viewpoints"]
            assert raster.childCount() == 0, "nothing to disclose on a raster"
            assert vector.childCount() > 0
            for name in ("Elevation", "Viewpoints"):
                tree.scrollToItem(rows[name])
                qgis_app.processEvents()
            # The same click on the arrow's spot opens a vector layer's rows
            # and does nothing to a raster.
            for item in (raster, vector):
                tree.scrollToItem(item)
                rect = tree.visualItemRect(item)
                QTest.mouseClick(
                    tree.viewport(),
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                    QPoint(rect.left() - tree.indentation() // 2, rect.center().y()),
                )
                qgis_app.processEvents()
            assert not raster.isExpanded()
            assert vector.isExpanded()
            vector.setExpanded(False)
            qgis_app.processEvents()
            # Include, popup and label boxes sit at the same x on both rows.
            for column in (1, 2, 3):
                assert (
                    tree.visualRect(tree.indexFromItem(raster, column)).left()
                    == tree.visualRect(tree.indexFromItem(vector, column)).left()
                )
        finally:
            dialog.close()

    def test_columns_stay_put_when_the_scrollbar_would_appear(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._open(qgis_app, project, make_memory_layer, "web", (1000, 700))
        try:
            tree = dialog.layer_tree
            width = tree.viewport().width()
            tree.expandAll()  # taller than the tree, so a scrollbar is needed
            for _ in range(5):
                qgis_app.processEvents()
            assert tree.verticalScrollBar().maximum() > 0
            assert tree.viewport().width() == width
        finally:
            dialog.close()

    def test_expansion_survives_a_layer_refresh(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._open(qgis_app, project, make_memory_layer, "web", (1000, 700))
        try:
            tree = dialog.layer_tree
            tree.expandItem(tree.topLevelItem(0))
            dialog.refresh_layers()
            for _ in range(3):
                qgis_app.processEvents()
            assert tree.topLevelItem(0).isExpanded()
        finally:
            dialog.close()


def _pick_layout(dialog, name: str) -> None:
    """Choose `name` in the layout list as a user does: open the list and
    click its row."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtTest import QTest

    combo = dialog._cartograph.layout_combo
    combo.showPopup()
    view = combo.view()
    row = combo.findText(name)
    assert row >= 0
    rect = view.visualRect(view.model().index(row, 0))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    combo.hidePopup()


def _rename_through_dialog(dialog, typed: str, *, press_twice: bool = False):
    """Click "Rename...", type `typed` in the box and click OK. Returns the
    inline message the dialog showed (None if it accepted at once)."""
    from qgis.PyQt.QtCore import QPoint, Qt, QTimer
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QApplication, QDialogButtonBox

    shown: list[str | None] = []

    def drive() -> None:
        box = QApplication.activeModalWidget()
        assert box is not None
        box.name_edit.setText(typed)
        ok = box.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        QTest.mouseClick(ok, Qt.MouseButton.LeftButton)
        if box.isVisible():
            shown.append(box.message.text() if not box.message.isHidden() else "")
            box.reject()
        else:
            shown.append(None)

    QTimer.singleShot(0, drive)
    button = dialog._cartograph.rename_layout_button
    QTest.mouseClick(
        button, Qt.MouseButton.LeftButton, pos=QPoint(8, button.height() // 2)
    )
    return shown[0]


class TestLayoutSelectorAndTemplates:
    """The layout list holds layouts; "New from template..." makes a new one,
    named for its design and the project; Rename... renames the one shown."""

    def _dialog(self, project, picked_template, title="Grand Canyon"):
        dialog, _canvas = TestCanvasAndImport()._canvas_dialog(project)
        project.setTitle(title)
        _choose_cartograph(dialog)
        return dialog

    def test_the_list_holds_only_the_projects_layouts(
        self, qgis_app, project, picked_template
    ) -> None:
        _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        _add_layout(project, "Parcels", [("Map", "EPSG:4326")])
        dialog = self._dialog(project, picked_template)
        try:
            combo = dialog._cartograph.layout_combo
            names = sorted(
                layout.name() for layout in project.layoutManager().layouts()
            )
            assert sorted(combo.itemText(i) for i in range(combo.count())) == names
            assert all(combo.itemData(i) in (None, "") for i in range(combo.count()))
            picked_template.choice = ("legend-below", "A4", "landscape")
            _click_new_from_template(dialog)
            names = sorted(
                layout.name() for layout in project.layoutManager().layouts()
            )
            assert sorted(combo.itemText(i) for i in range(combo.count())) == names
            assert "Clearline · Grand Canyon" in names
        finally:
            dialog.close()

    def test_a_template_makes_a_named_new_layout_and_a_second_gets_a_number(
        self, qgis_app, project, picked_template
    ) -> None:
        original = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        dialog = self._dialog(project, picked_template)
        try:
            picked_template.choice = ("legend-below", "A4", "landscape")
            _click_new_from_template(dialog)
            assert dialog._cartograph.layout_combo.currentText() == (
                "Clearline · Grand Canyon"
            )
            _click_new_from_template(dialog)
            assert dialog._cartograph.layout_combo.currentText() == (
                "Clearline · Grand Canyon (2)"
            )
            picked_template.choice = ("locator", "A4", "landscape")
            _click_new_from_template(dialog)
            names = {layout.name() for layout in project.layoutManager().layouts()}
            assert "Near & Far · Grand Canyon" in names
            assert project.layoutManager().layoutByName("Survey") is original
        finally:
            dialog.close()

    def test_an_untitled_project_still_gets_a_sensible_name(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog = self._dialog(project, picked_template, title="")
        try:
            picked_template.choice = ("field-map", "A4", "landscape")
            _click_new_from_template(dialog)
            assert dialog._cartograph.layout_combo.currentText() == (
                "Fieldbook · Untitled map"
            )
        finally:
            dialog.close()

    def test_picking_a_layout_switches_and_keeps_the_edits_of_the_other(
        self, qgis_app, project, picked_template
    ) -> None:
        from qgis.core import QgsLayoutItemLabel

        first = _add_layout(project, "First", [("Map", "EPSG:4326")])
        _add_layout(project, "Second", [("Map", "EPSG:4326")])
        dialog = self._dialog(project, picked_template)
        try:
            cartograph = dialog._cartograph
            _pick_layout(dialog, "First")
            assert cartograph.layout_combo.currentText() == "First"
            assert cartograph.state.cartograph_layout == "First"
            label = QgsLayoutItemLabel(first)
            label.setText("Edited in A")
            first.addLayoutItem(label)
            cartograph.title_edit.setText("A's title")
            cartograph.title_edit.textEdited.emit("A's title")
            cartograph._autosave_timer.stop()

            _pick_layout(dialog, "Second")
            assert cartograph.state.cartograph_layout == "Second"
            assert cartograph._current_layout is project.layoutManager().layoutByName(
                "Second"
            )
            assert cartograph.title_edit.text() != "A's title"

            _pick_layout(dialog, "First")
            assert cartograph.state.cartograph_layout == "First"
            assert cartograph.title_edit.text() == "A's title"
            texts = [
                item.text()
                for item in first.items()
                if isinstance(item, QgsLayoutItemLabel)
            ]
            assert "Edited in A" in texts
        finally:
            dialog.close()

    def test_rename_changes_the_layout_everywhere_it_shows(
        self, qgis_app, project, picked_template
    ) -> None:
        _add_layout(project, "First", [("Map", "EPSG:4326")])
        second = _add_layout(project, "Second", [("Map", "EPSG:4326")])
        dialog = self._dialog(project, picked_template)
        try:
            cartograph = dialog._cartograph
            _pick_layout(dialog, "Second")
            cartograph._autosave_timer.stop()
            button = cartograph.rename_layout_button
            assert button.accessibleName() == "Rename layout"
            assert _rename_through_dialog(dialog, "Gorge sheet") is None
            assert second.name() == "Gorge sheet"
            combo = cartograph.layout_combo
            assert combo.currentText() == "Gorge sheet"
            assert combo.findText("Second") < 0
            assert cartograph.state.cartograph_layout == "Gorge sheet"
            assert "Gorge sheet" in dialog.status_label.text()
        finally:
            dialog.close()

    def test_rename_refuses_an_empty_or_taken_name_inline(
        self, qgis_app, project, picked_template
    ) -> None:
        _add_layout(project, "First", [("Map", "EPSG:4326")])
        second = _add_layout(project, "Second", [("Map", "EPSG:4326")])
        dialog = self._dialog(project, picked_template)
        try:
            cartograph = dialog._cartograph
            _pick_layout(dialog, "Second")
            cartograph._autosave_timer.stop()
            assert "needs a name" in _rename_through_dialog(dialog, "   ")
            assert "already exists" in _rename_through_dialog(dialog, "First")
            assert second.name() == "Second"
            assert cartograph.layout_combo.currentText() == "Second"
        finally:
            dialog.close()

    def test_the_rename_button_is_off_with_no_layout(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog = self._dialog(project, picked_template)
        try:
            assert not dialog._cartograph.rename_layout_button.isEnabled()
        finally:
            dialog.close()

    def test_switching_to_a_layout_with_a_dropped_item_reports_it_in_checks(
        self, qgis_app, project, tmp_path, picked_template
    ) -> None:
        import os

        from qgis.core import QgsLayoutItemLegend, QgsRectangle
        from qgis.PyQt.QtCore import QRectF

        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR")
        for name in ("Plain", "Loose legend"):
            made = _add_layout(project, name, [("Map", "EPSG:4326")])
            made.itemById("Map").setExtent(QgsRectangle(103.7, 1.25, 103.9, 1.4))
        loose = project.layoutManager().layoutByName("Loose legend")
        legend = QgsLayoutItemLegend(loose)
        legend.setLinkedMap(None)
        legend.attemptSetSceneRect(QRectF(120, 10, 60, 60))
        loose.addLayoutItem(legend)
        dialog = self._dialog(project, picked_template)
        try:
            cartograph = dialog._cartograph
            _pick_layout(dialog, "Plain")
            _pick_layout(dialog, "Loose legend")
            assert _wait_for(
                qgis_app, lambda: cartograph._last_summary is not None, timeout=60
            ), cartograph._last_error
            rows = " ".join(
                repr(row)
                for group in cartograph._last_summary.groups
                for row in group.changes
            ).lower()
            assert "legend" in rows and "left out" in rows
        finally:
            dialog.close()


class TestEditTab:
    """The selected item's editing lives in the panel's Edit tab, after
    Checks: a double-click or Add item brings it up, a single click does not."""

    def _ready(self, project, qgis_app, size=(1280, 800)):
        from qgis.core import QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap
        from qgis.PyQt.QtCore import QRectF

        layout = _add_layout(project, "Survey", [("Overview", "EPSG:4326")])
        map_item = next(i for i in layout.items() if isinstance(i, QgsLayoutItemMap))
        map_item.attemptSetSceneRect(QRectF(10, 20, 120, 100))
        label = QgsLayoutItemLabel(layout)
        label.setText("A title")
        label.attemptSetSceneRect(QRectF(10, 5, 100, 12))
        layout.addLayoutItem(label)
        legend = QgsLayoutItemLegend(layout)
        legend.setLinkedMap(map_item)
        legend.attemptSetSceneRect(QRectF(170, 30, 60, 50))
        layout.addLayoutItem(legend)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph._autosave_timer.stop()
        dialog.resize(*size)
        dialog.show()
        _wait_for(qgis_app, lambda: cartograph.page_pane.preview.isVisible())
        for _ in range(30):
            qgis_app.processEvents()
        return (
            dialog,
            cartograph,
            layout,
            {"map": map_item, "text": label, "legend": legend},
        )

    @staticmethod
    def _point(cartograph, item):
        view = cartograph.page_pane.preview.view
        return view.mapFromScene(item.sceneBoundingRect().center())

    @staticmethod
    def _click(qgis_app, cartograph, item, double=False) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        viewport = cartograph.page_pane.preview.view.viewport()
        point = TestEditTab._point(cartograph, item)
        QTest.mouseMove(viewport, point)
        qgis_app.processEvents()
        click = QTest.mouseDClick if double else QTest.mouseClick
        if double:
            QTest.mouseClick(
                viewport,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                point,
            )
        click(
            viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
        )
        for _ in range(10):
            qgis_app.processEvents()

    @staticmethod
    def _tab(cartograph) -> str:
        return cartograph._tab_keys[cartograph.panel_tabs.currentIndex()]

    def test_the_tab_follows_checks_and_its_title_never_changes(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            tabs = cartograph.panel_tabs
            index = cartograph._tab_keys.index("edit")
            assert index == cartograph._tab_keys.index("checks") + 1
            assert tabs.tabText(index) == "Edit"
            controls = cartograph.page_pane.controls
            layout.deselectAll()
            qgis_app.processEvents()
            assert "Select an item on the page to edit it." in controls.heading.text()
            assert controls.body.isHidden() and controls.delete_button.isHidden()
            items["legend"].setSelected(True)
            qgis_app.processEvents()
            assert controls.heading.text() == "Selected: Legend"
            assert not controls.body.isHidden()
            assert tabs.tabText(index) == "Edit"
        finally:
            dialog.close()

    def test_a_single_click_selects_without_switching_tabs(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph, _layout, items = self._ready(project, qgis_app)
        try:
            cartograph.open_section("layout")
            self._click(qgis_app, cartograph, items["legend"])
            assert items["legend"].isSelected()
            assert self._tab(cartograph) == "layout"
            assert cartograph.page_pane.controls.heading.text() == "Selected: Legend"
        finally:
            dialog.close()

    @pytest.mark.parametrize(
        ("name", "page"),
        [("map", "map_page"), ("legend", "legend_page"), ("text", "text_page")],
    )
    def test_a_double_click_brings_up_the_edit_tab_on_the_right_page(
        self, qgis_app, project, name, page
    ) -> None:
        dialog, cartograph, _layout, items = self._ready(project, qgis_app)
        try:
            cartograph.open_section("checks")
            self._click(qgis_app, cartograph, items[name], double=True)
            assert self._tab(cartograph) == "edit"
            controls = cartograph.page_pane.controls
            assert controls.pages.currentWidget() is getattr(controls, page)
            assert controls.selected_item() is items[name]
        finally:
            dialog.close()

    def test_add_item_brings_up_the_edit_tab(self, qgis_app, project) -> None:
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtTest import QTest

        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            cartograph.open_section("layout")
            pane = cartograph.page_pane
            pane._fill_add_menu()
            next(a for a in pane.add_menu.actions() if a.text() == "Text").trigger()
            # Choosing alone does not move the user; the click that places does.
            assert self._tab(cartograph) == "layout"
            point = pane.preview.view.mapFromScene(QPointF(150, 150))
            QTest.mouseClick(
                pane.preview.view.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                point,
            )
            for _ in range(10):
                qgis_app.processEvents()
            assert self._tab(cartograph) == "edit"
            assert pane.controls.pages.currentWidget() is pane.controls.text_page
            fresh = [
                i
                for i in layout.items()
                if isinstance(i, QgsLayoutItemLabel) and i is not items["text"]
            ]
            assert len(fresh) == 1 and fresh[0].isSelected()
        finally:
            dialog.close()

    def test_the_delete_button_deletes_in_one_undo_step(
        self, qgis_app, project
    ) -> None:
        from qgis.core import QgsLayoutItemLegend

        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            items["legend"].setSelected(True)
            qgis_app.processEvents()
            stack = layout.undoStack().stack()
            count = stack.count()
            cartograph.page_pane.controls.delete_button.click()
            assert items["legend"] not in layout.items()
            assert stack.count() == count + 1
            stack.undo()
            # Undo brings back an equal item (a new object).
            assert any(isinstance(i, QgsLayoutItemLegend) for i in layout.items())
        finally:
            dialog.close()

    def test_the_delete_key_on_the_page_still_deletes(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            self._click(qgis_app, cartograph, items["text"])
            view = cartograph.page_pane.preview.view
            view.setFocus()
            stack = layout.undoStack().stack()
            count = stack.count()
            QTest.keyClick(view, Qt.Key.Key_Backspace)
            assert items["text"] not in layout.items()
            assert stack.count() == count + 1
        finally:
            dialog.close()

    def test_a_legend_shows_one_text_size_control(self, qgis_app, project) -> None:
        """The hosted legend editor has its own Text size, so the A-/A+ row
        stays for text items only."""
        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            controls = cartograph.page_pane.controls
            items["legend"].setSelected(True)
            qgis_app.processEvents()
            assert controls.text_size_row.isHidden()
            layout.deselectAll()
            items["text"].setSelected(True)
            qgis_app.processEvents()
            assert not controls.text_size_row.isHidden()
        finally:
            dialog.close()

    def test_a_plus_in_the_tab_is_one_font_step_and_one_undo(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph, layout, items = self._ready(project, qgis_app)
        try:
            label = items["text"]
            label.setSelected(True)
            qgis_app.processEvents()
            controls = cartograph.page_pane.controls
            assert not controls.text_size_row.isHidden()
            size = label.textFormat().size()
            stack = layout.undoStack().stack()
            count = stack.count()
            controls.text_bigger_button.click()
            assert label.textFormat().size() == pytest.approx(size + 1)
            assert stack.count() == count + 1
            assert controls.text_size_label.text() == f"{size + 1:g} pt"
            stack.undo()
            assert label.textFormat().size() == pytest.approx(size)
        finally:
            dialog.close()

    def test_the_preview_bar_has_no_text_size_or_mode_buttons(
        self, qgis_app, project
    ) -> None:
        dialog, cartograph, _layout, _items = self._ready(project, qgis_app)
        try:
            pane = cartograph.page_pane
            for name in (
                "select_button",
                "pan_button",
                "text_size_bar",
                "text_bigger_button",
                "text_smaller_button",
            ):
                assert not hasattr(pane, name), name
        finally:
            dialog.close()

    @pytest.mark.parametrize("size", [(1280, 800), (720, 320)])
    def test_the_legend_editor_has_room_and_nothing_overlaps(
        self, qgis_app, project, size
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, QRect
        from qgis.PyQt.QtWidgets import (
            QAbstractButton,
            QAbstractScrollArea,
            QAbstractSpinBox,
            QComboBox,
            QLabel,
            QWidget,
        )

        dialog, cartograph, _layout, items = self._ready(project, qgis_app, size)
        try:
            cartograph.open_section("edit")
            items["legend"].setSelected(True)
            for _ in range(30):
                qgis_app.processEvents()
            controls = cartograph.page_pane.controls
            editor = controls._legend_editor
            assert editor is not None and editor.isVisible()
            # The tab scrolls rather than squeezing the editor.
            assert editor.height() >= editor.minimumSizeHint().height()
            leaves = (
                QAbstractButton,
                QAbstractScrollArea,
                QAbstractSpinBox,
                QComboBox,
                QLabel,
            )
            boxes = []
            for w in editor.findChildren(QWidget):
                if isinstance(w, leaves) and w.isVisibleTo(editor):
                    top_left = w.mapTo(editor, QPoint(0, 0))
                    boxes.append((w, QRect(top_left, w.size())))
            assert boxes
            for i, (a, ra) in enumerate(boxes):
                for b, rb in boxes[i + 1 :]:
                    if a.isAncestorOf(b) or b.isAncestorOf(a):
                        continue
                    inter = ra.intersected(rb)
                    assert (
                        inter.isEmpty() or inter.width() <= 1 or inter.height() <= 1
                    ), (
                        type(a).__name__,
                        a.text() if hasattr(a, "text") else "",
                        type(b).__name__,
                        b.text() if hasattr(b, "text") else "",
                        ra,
                        rb,
                    )
        finally:
            dialog.close()

    @pytest.mark.parametrize("name", ["text", "map", "legend"])
    def test_a_short_window_scrolls_the_edit_tab_like_the_others(
        self, qgis_app, project, name
    ) -> None:
        from qgis.PyQt.QtWidgets import QScrollArea

        dialog, cartograph, _layout, items = self._ready(project, qgis_app, (720, 320))
        try:
            cartograph.open_section("edit")
            items[name].setSelected(True)
            for _ in range(30):
                qgis_app.processEvents()
            surfaces = [
                area
                for area in dialog.findChildren(QScrollArea)
                if area.isVisible()
                and area.height() > 40
                and area.verticalScrollBar().maximum() > 0
            ]
            assert len(surfaces) == 1
            controls = cartograph.page_pane.controls
            assert controls.delete_button.isVisibleTo(dialog)
        finally:
            dialog.close()
