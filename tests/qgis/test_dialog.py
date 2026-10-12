"""Dialog behaviour: settings persistence, the live layer list, and preview.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import OutputMode, PopupFieldMode
from nika_onlymap_exporter.core.settings import (
    DialogState,
    LayerSettings,
    load_state,
    save_state,
)

qgis_core = pytest.importorskip("qgis.core")


def _field_items(dialog, layer_item):
    """A layer's field rows, skipping the per-layer options row above them."""
    from qgis.PyQt.QtCore import Qt

    from nika_onlymap_exporter.ui.main_dialog import LAYER_OPTIONS_ROLE

    return [
        layer_item.child(index)
        for index in range(layer_item.childCount())
        if layer_item.child(index).data(0, Qt.ItemDataRole.UserRole)
        != LAYER_OPTIONS_ROLE
    ]


def _options_row(dialog, layer_item):
    """The per-layer overrides row, always the first child of a layer."""
    from qgis.PyQt.QtCore import Qt

    from nika_onlymap_exporter.ui.main_dialog import LAYER_OPTIONS_ROLE

    item = layer_item.child(0)
    assert item.data(0, Qt.ItemDataRole.UserRole) == LAYER_OPTIONS_ROLE
    return dialog.layer_tree.itemWidget(item, 0)


def _mode_combo(dialog, field_item):
    """The popup-mode combo on a field row.

    The row is one spanned widget rather than a per-column one, so the combo
    has to be found inside it - that is what stops the mode labels being
    cropped to the width of the "Include" checkbox column.
    """
    from qgis.PyQt.QtWidgets import QComboBox

    row = dialog.layer_tree.itemWidget(field_item, 0)
    assert row is not None, "field rows carry a spanned widget in column 0"
    combo = row.findChild(QComboBox)
    assert combo is not None, "the field row should hold a mode combo"
    return combo


class TestSettingsPersistence:
    def test_round_trips_through_the_project(self, project) -> None:
        state = DialogState(
            map_name="My map",
            map_file="/tmp/himalaya-peaks.html",
            show_legend=False,
        )
        state.layers["abc"] = LayerSettings(include=False, popup=False, label=True)
        save_state(project, state)

        restored = load_state(project)
        assert restored.map_name == "My map"
        assert restored.map_file == "/tmp/himalaya-peaks.html"
        assert restored.show_legend is False
        assert restored.layers["abc"].include is False
        assert restored.layers["abc"].label is True

    def test_empty_project_yields_sensible_defaults(self, project) -> None:
        state = load_state(project)
        assert state.map_name == ""
        # Empty, never guessed at: a guess would name a file the user never
        # wrote, and autosaving to it would sit a second map beside their real
        # one. See `DialogState.map_file`.
        assert state.map_file == ""
        assert state.output_mode is OutputMode.STANDALONE_HTML
        assert state.show_legend is True

    def test_corrupt_entries_do_not_prevent_opening(self, project) -> None:
        """A bad setting must never stop the dialog; worst case is a re-pick."""
        project.writeEntry("qgis2webmap", "layers", "{{{ not json")
        state = load_state(project)
        assert state.layers == {}

    def test_the_export_type_survives_a_round_trip(self, project) -> None:
        """`outputMode` is the Export as choice again, kept with the project.

        Under the same key 0.1.x wrote, so a project whose last export was a
        zip reopens on Share ZIP rather than losing the choice.
        """
        project.writeEntry("qgis2webmap", "outputMode", OutputMode.SHARE_ZIP.value)
        assert load_state(project).output_mode is OutputMode.SHARE_ZIP

        state = load_state(project)
        state.output_mode = OutputMode.FOLDER
        save_state(project, state)
        assert load_state(project).output_mode is OutputMode.FOLDER

    def test_an_unknown_export_type_falls_back_to_a_single_file(self, project) -> None:
        project.writeEntry("qgis2webmap", "outputMode", "carrier_pigeon")
        assert load_state(project).output_mode is OutputMode.STANDALONE_HTML

    def test_the_map_file_survives_a_round_trip_unchanged(self, project) -> None:
        """Paths carry spaces, accents and separators; none may be mangled."""
        awkward = "/home/someone/Mis Mapas/año 2026/peaks (final).html"
        save_state(project, DialogState(map_file=awkward))
        assert load_state(project).map_file == awkward

    def test_the_map_file_follows_a_moved_project_folder(
        self, project, tmp_path
    ) -> None:
        """An absolute path in a shared .qgz names a folder on the sender's
        machine; the relative form follows the project folder."""
        project.setFileName(str(tmp_path / "work" / "survey.qgz"))
        (tmp_path / "work" / "out").mkdir(parents=True)
        target = tmp_path / "work" / "out" / "peaks.html"
        save_state(project, DialogState(map_file=str(target)))
        assert load_state(project).map_file == str(target)

        # Moved: the old folder is gone, the copy carries its own.
        import shutil

        shutil.move(str(tmp_path / "work"), str(tmp_path / "moved"))
        project.setFileName(str(tmp_path / "moved" / "survey.qgz"))
        assert load_state(project).map_file == str(
            tmp_path / "moved" / "out" / "peaks.html"
        )

    def test_save_as_elsewhere_keeps_the_real_map_file(self, project, tmp_path) -> None:
        """QGIS does not rebase custom entries on Save As, so the relative form
        points beside the copy - where there is no such folder."""
        project.setFileName(str(tmp_path / "work" / "survey.qgz"))
        (tmp_path / "work" / "out").mkdir(parents=True)
        target = tmp_path / "work" / "out" / "peaks.html"
        save_state(project, DialogState(map_file=str(target)))

        project.setFileName(str(tmp_path / "share" / "copy.qgz"))
        assert load_state(project).map_file == str(target)

    def test_save_as_beside_a_map_saved_next_to_the_project(
        self, project, tmp_path
    ) -> None:
        """The picker's default: the map beside the project. After Save As the
        relative form names a file beside the copy that was never written."""
        (tmp_path / "work").mkdir()
        (tmp_path / "share").mkdir()
        project.setFileName(str(tmp_path / "work" / "survey.qgz"))
        target = tmp_path / "work" / "peaks.html"
        save_state(project, DialogState(map_file=str(target)))

        project.setFileName(str(tmp_path / "share" / "copy.qgz"))
        assert load_state(project).map_file == str(target)

    def test_field_modes_round_trip_through_the_project(self, project) -> None:
        state = DialogState()
        state.layers["abc"] = LayerSettings(
            fields={
                "name": PopupFieldMode.HEADER_ALWAYS.value,
                "code": PopupFieldMode.HIDDEN.value,
            }
        )
        save_state(project, state)

        restored = load_state(project).layers["abc"]
        assert restored.fields["name"] == PopupFieldMode.HEADER_ALWAYS.value
        assert restored.fields["code"] == PopupFieldMode.HIDDEN.value

    def test_a_malformed_field_mode_does_not_prevent_opening(self, project) -> None:
        """A mode from a newer build, or a hand edit, must degrade quietly."""
        project.writeEntry(
            "qgis2webmap",
            "layers",
            '{"abc": {"include": true, "fields": {"name": "from_the_future"}}}',
        )
        restored = load_state(project).layers["abc"]
        assert restored.include is True
        assert restored.fields == {}

    def test_the_new_appearance_options_round_trip(self, project) -> None:
        from nika_onlymap_exporter.core.export_ir import ExtentSource, OverlayCorner

        state = DialogState(
            popup_on_hover=True,
            show_title=True,
            show_abstract=True,
            title_corner=OverlayCorner.BOTTOM_RIGHT,
            widget_background="#102a2a",
            widget_foreground="#e6fffb",
            quantize_precision=4,
            extent_source=ExtentSource.CANVAS,
        )
        save_state(project, state)

        restored = load_state(project)
        assert restored.popup_on_hover is True
        assert restored.show_title is True
        assert restored.show_abstract is True
        assert restored.title_corner is OverlayCorner.BOTTOM_RIGHT
        assert restored.widget_background == "#102a2a"
        assert restored.widget_foreground == "#e6fffb"
        assert restored.quantize_precision == 4
        assert restored.extent_source is ExtentSource.CANVAS

    def test_a_nonsense_precision_falls_back_to_maintain(self, project) -> None:
        """Out-of-range or non-numeric must not silently round coordinates."""
        for bad in ('"abc"', "0", "99", "true"):
            project.writeEntry(
                "qgis2webmap", "widgets", f'{{"quantizePrecision": {bad}}}'
            )
            assert load_state(project).quantize_precision is None

    def test_a_nonsense_corner_falls_back_to_the_default(self, project) -> None:
        from nika_onlymap_exporter.core.export_ir import OverlayCorner

        project.writeEntry("qgis2webmap", "widgets", '{"titleCorner": "middle-ish"}')
        # Top centre, because every corner already holds map chrome.
        assert load_state(project).title_corner is OverlayCorner.TOP_CENTER

    def test_settings_for_removed_layers_are_kept(self, project) -> None:
        """QGIS undo restores the layer; its configuration should return too."""
        state = DialogState()
        state.layers["gone"] = LayerSettings(include=False)
        save_state(project, state)
        assert load_state(project).layers["gone"].include is False


class TestDialogState:
    def test_unknown_layer_gets_defaults_on_first_sight(self) -> None:
        state = DialogState()
        assert state.for_layer("new").include is True

    def test_selection_reflects_the_include_flags(self) -> None:
        state = DialogState()
        state.for_layer("a").include = True
        state.for_layer("b").include = False
        assert state.selected_layer_ids(["a", "b"]) == frozenset({"a"})

    def test_export_settings_carry_the_widget_choices(self) -> None:
        state = DialogState(show_legend=False, show_scale_bar=False)
        settings = state.to_export_settings()
        assert settings.show_legend is False
        assert settings.show_scale_bar is False
        assert settings.show_zoom_controls is True


class TestLayerWatcher:
    def test_bursts_collapse_into_one_rebuild(self, qgis_app, project) -> None:
        """One drag fires four signals; the list must rebuild once."""
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerTreeWatcher

        watcher = LayerTreeWatcher(project)
        rebuilds = []
        watcher.changed.connect(lambda: rebuilds.append(1))

        for index in range(3):
            layer = qgis_core.QgsVectorLayer(
                "Point?crs=EPSG:4326&field=n:string", f"L{index}", "memory"
            )
            project.addMapLayer(layer)

        QCoreApplication.processEvents()
        assert len(rebuilds) == 1, f"expected one coalesced rebuild, got {rebuilds}"
        watcher.disconnect_all()

    def test_disconnect_stops_further_signals(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerTreeWatcher

        watcher = LayerTreeWatcher(project)
        rebuilds = []
        watcher.changed.connect(lambda: rebuilds.append(1))
        watcher.disconnect_all()

        project.addMapLayer(
            qgis_core.QgsVectorLayer(
                "Point?crs=EPSG:4326&field=n:string", "after", "memory"
            )
        )
        QCoreApplication.processEvents()
        assert rebuilds == []


class TestPreview:
    def test_preview_path_is_stable_for_a_project(self) -> None:
        """A changing URL is why the incumbent's reload button is useless."""
        from nika_onlymap_exporter.ui.preview import preview_directory

        assert preview_directory("/tmp/a.qgz") == preview_directory("/tmp/a.qgz")

    def test_different_projects_get_different_paths(self) -> None:
        from nika_onlymap_exporter.ui.preview import preview_directory

        assert preview_directory("/tmp/a.qgz") != preview_directory("/tmp/b.qgz")

    def test_preview_injects_camera_persistence(
        self, project, make_memory_layer, runtime_required
    ) -> None:
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project
        from nika_onlymap_exporter.ui.preview import write_preview

        project.addMapLayer(make_memory_layer("pts", features=[("a", [1.0, 2.0])]))
        export = read_project(project, FidelityReportBuilder())

        result = write_preview(export, "test-project-identity")
        html = result.entry_path.read_text()
        assert "om-view-changed" in html
        assert "#camera=" in html

    def test_export_does_not_carry_the_preview_script(
        self, project, make_memory_layer, tmp_path, runtime_required
    ) -> None:
        """Camera persistence is a preview affordance, not part of a deliverable."""
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project
        from nika_onlymap_exporter.packaging.artifact_builder import build_artifact

        project.addMapLayer(make_memory_layer("pts", features=[("a", [1.0, 2.0])]))
        export = read_project(project, FidelityReportBuilder())

        _result, outcome = build_artifact(export, tmp_path / "map.html")
        assert "om-view-changed" not in outcome.path.read_text()


class TestShortWindows:
    """A short window scrolls a tab of stacked cards; it never crushes them."""

    @pytest.mark.parametrize("tab", ["Map", "Appearance", "Settings"])
    def test_nothing_is_squeezed_below_its_minimum_height(
        self, qgis_app, project, make_memory_layer, tab
    ):
        from qgis.PyQt.QtWidgets import (
            QAbstractButton,
            QComboBox,
            QGroupBox,
            QLabel,
            QLineEdit,
        )

        from nika_onlymap_exporter.ui.main_dialog import MINIMUM_DIALOG_HEIGHT

        dialog = TestTabCorner()._dialog(project, make_memory_layer)
        try:
            dialog.show()
            dialog.resize(960, MINIMUM_DIALOG_HEIGHT)
            titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            dialog.tabs.setCurrentIndex(titles.index(tab))
            qgis_app.processEvents()
            crushed = [
                f"{type(w).__name__} {w.height()} < {w.minimumSizeHint().height()}"
                for w in dialog.tabs.currentWidget().findChildren(
                    (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit)
                )
                if w.isVisibleTo(dialog)
                and w.minimumSizeHint().isValid()
                and w.height() < w.minimumSizeHint().height() - 1
            ]
            assert crushed == []
        finally:
            dialog.close()


class TestTabCorner:
    """Preview and Share live on the tab bar, reachable from every tab.

    They used to sit in a row at the foot of the dialog, which is also where the
    Export button once ended up behind the Windows taskbar. The row they left is
    now chrome only - somewhere to ask, somewhere to leave.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_the_corner_holds_preview_export_and_share(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QToolButton

        dialog = self._dialog(project, make_memory_layer)
        try:
            corner = dialog.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            assert corner is not None
            labels = [b.text() for b in corner.findChildren(QToolButton)]
            # The live switch is a bare dot inside Preview, where an arrow sits.
            assert labels == ["Preview", "", "Share", "Export"]
        finally:
            dialog.close()

    def test_preview_is_one_button_that_shows_a_running_preview(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """No menu: one press starts a live preview, and the button says so."""
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        try:
            button = dialog.preview_button
            assert button.menu() is None
            assert button.text() == "Preview"

            (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
            dialog._server = PreviewServer(tmp_path)
            dialog._watch_timer.start()  # a live preview, not a one-off served one
            dialog._show_preview_state()
            assert button.text() == "Live"
            assert button.property("live") is True

            button.click()  # the second press stops it
            assert dialog._server is None
            assert button.text() == "Preview"
            assert button.property("live") is False
        finally:
            dialog.close()

    def test_a_disabled_share_menu_keeps_no_live_shortcut(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """A greyed-out button must not leave its shortcuts armed.

        A disabled `QToolButton` still owns an enabled `QMenu`, and an enabled
        `QAction` keeps its shortcut live on the dialog - so `Ctrl+O` would open
        a map that has nowhere to be written, from a control the user can see is
        unavailable.
        """
        dialog = self._dialog(project, make_memory_layer)
        try:
            # The button itself stays usable: its menu also holds Discord,
            # which needs no map file.
            assert dialog.share_button.isEnabled() is True
            from nika_onlymap_exporter.ui.main_dialog import SHARE_ACTIONS_ON_THE_FILE

            on_the_file = [dialog.share_actions[k] for k in SHARE_ACTIONS_ON_THE_FILE]
            for action in on_the_file:
                assert action.isEnabled() is False
                assert "Map tab" in action.toolTip()

            dialog.path_edit.setText(str(tmp_path / "peaks.html"))
            for action in on_the_file:
                assert action.isEnabled() is True
            # The real wording comes back rather than staying as the refusal.
            assert "clipboard" in dialog.share_actions["copy_path"].toolTip()
        finally:
            dialog.close()

    def test_the_bottom_row_is_chrome_only(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Nothing that acts on the map is left down there."""
        from qgis.PyQt.QtWidgets import QPushButton

        dialog = self._dialog(project, make_memory_layer)
        try:
            labels = {b.text() for b in dialog.findChildren(QPushButton)}
            assert "Close" in labels
            # Discord moved into the Share menu.
            assert "Discord ↗" not in labels
            for gone in ("Export", "Host ↗", "Open exported map", "Preview"):
                assert gone not in labels
        finally:
            dialog.close()


class TestLiveSwitch:
    """The dot beside Preview: off, Preview opens the map once; on, it stays live."""

    def _dialog(self, project, make_memory_layer):
        return TestTabCorner()._dialog(project, make_memory_layer)

    def _wait(self, qgis_app, condition) -> None:
        import time

        deadline = time.monotonic() + 60
        while not condition() and time.monotonic() < deadline:
            qgis_app.processEvents()
            time.sleep(0.02)
        assert condition()

    def test_it_starts_off_and_remembers_being_switched_on(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui.main_dialog import LIVE_PREVIEW_KEY

        dialog = self._dialog(project, make_memory_layer)
        try:
            switch = dialog.live_switch
            assert switch.isChecked() is False
            assert switch.property("lineIcon") == "live-off"
            assert "off" in switch.toolTip()

            switch.click()
            assert switch.property("lineIcon") == "live"
            assert QSettings().value(LIVE_PREVIEW_KEY, type=bool) is True
        finally:
            QSettings().remove(LIVE_PREVIEW_KEY)
            dialog.close()

    def test_off_preview_opens_the_map_once_with_no_server(
        self, qgis_app, project, make_memory_layer, monkeypatch, runtime_required
    ) -> None:
        opened: list[str] = []
        monkeypatch.setattr(
            "nika_onlymap_exporter.ui.main_dialog.QDesktopServices.openUrl",
            lambda url: opened.append(url.toString()) or True,
        )
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.preview_button.click()
            self._wait(qgis_app, lambda: bool(opened) and dialog._job is None)
            assert opened[0].startswith("file://")
            assert dialog._server is None
            assert dialog.preview_button.text() == "Preview"
        finally:
            dialog.close()

    def test_stopping_a_live_preview_leaves_the_switch_on(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui.live_server import PreviewServer
        from nika_onlymap_exporter.ui.main_dialog import LIVE_PREVIEW_KEY

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.live_switch.setChecked(True)
            (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
            dialog._server = PreviewServer(tmp_path)
            dialog._watch_timer.start()  # a live preview, not a one-off served one
            dialog._show_preview_state()
            assert dialog.preview_button.text() == "Live"

            dialog.preview_button.click()
            assert dialog._server is None
            assert dialog.preview_button.text() == "Preview"
            assert dialog.live_switch.isChecked() is True
        finally:
            QSettings().remove(LIVE_PREVIEW_KEY)
            dialog.close()

    def test_switching_off_stops_a_running_preview(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        from qgis.PyQt.QtCore import QSettings

        from nika_onlymap_exporter.ui.live_server import PreviewServer
        from nika_onlymap_exporter.ui.main_dialog import LIVE_PREVIEW_KEY

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.live_switch.setChecked(True)
            (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
            dialog._server = PreviewServer(tmp_path)
            dialog._server.start()
            dialog._watch_timer.start()

            dialog.live_switch.click()
            assert dialog._server is None
            assert dialog.preview_button.text() == "Preview"
        finally:
            QSettings().remove(LIVE_PREVIEW_KEY)
            dialog.close()


class TestRasterExports:
    """Rasters too large for one HTML file are a refusal, not a warning."""

    def test_standalone_with_a_raster_is_refused_without_a_write_anyway(
        self, qgis_app, project, make_memory_layer, make_raster_layer, monkeypatch
    ) -> None:
        import time

        import nika_onlymap_exporter.packaging.dependency_scanner as scanner

        # Past the size a single page can carry: "write it anyway" would only
        # end in the writer's refusal.
        monkeypatch.setattr(scanner, "STANDALONE_RASTER_MAX_BYTES", 1)

        from nika_onlymap_exporter.core.export_ir import OutputMode
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        project.addMapLayer(make_raster_layer(name="elevation"))
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "nika_onlymap_exporter.ui.main_dialog.QMessageBox.warning",
            lambda parent, title, text, *a: warnings.append((title, text)),
        )
        dialog = MainDialog(FakeIface(), None)
        offered: list[str] = []
        # Recorded, not raised: an exception inside a Qt callback aborts the
        # process rather than failing the test.
        monkeypatch.setattr(
            dialog,
            "_confirm_oversized_single_file",
            lambda reason: offered.append(reason) or False,
        )
        try:
            dialog.mode_buttons[OutputMode.STANDALONE_HTML].click()
            dialog.on_export()
            deadline = time.monotonic() + 30
            while not (warnings or offered) and time.monotonic() < deadline:
                qgis_app.processEvents()
                time.sleep(0.02)
            assert offered == [], "offered to write a file that cannot work"
            assert warnings, "the refusal was never shown"
            title, text = warnings[0]
            assert "Folder or Share ZIP" in title
            assert "'elevation'" in text
            assert "Folder or Share ZIP" in text
        finally:
            dialog.close()


class TestShareIsADropdown:
    """For now Share only opens its menu, which also points at Discord."""

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_pressing_share_opens_the_menu(self, qgis_app, project, make_memory_layer):
        from qgis.PyQt.QtWidgets import QToolButton

        dialog = self._dialog(project, make_memory_layer)
        try:
            assert (
                dialog.share_button.popupMode()
                == QToolButton.ToolButtonPopupMode.InstantPopup
            )
        finally:
            dialog.close()

    def test_discord_is_always_there_and_more_is_promised(
        self, qgis_app, project, make_memory_layer
    ):
        dialog = self._dialog(project, make_memory_layer)
        try:
            actions = [a for a in dialog.share_button.menu().actions() if a.text()]
            discord, coming = actions[-2], actions[-1]
            assert "Discord" in discord.text() and discord.isEnabled()
            assert "coming soon" in coming.text().lower()
            assert not coming.isEnabled()
        finally:
            dialog.close()


class TestShareMenuIcons:
    """Icon names have to be real, and cannot be checked by looking.

    A bare PyQGIS environment has no Qt SVG image-format plugin, so *every* SVG
    icon loads null here whether the name is right or not - asserting
    `not icon.isNull()` would pass nothing and prove nothing. The resource,
    however, either exists in the compiled theme or it does not, and that is
    exactly the mistake worth catching: a name invented from memory, or one
    retired by a later QGIS.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_every_icon_name_resolves_to_a_real_resource(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import QFile

        from nika_onlymap_exporter.ui.main_dialog import SHARE_MENU_ICONS

        missing = [
            name
            for name in SHARE_MENU_ICONS.values()
            if not QFile(f":/images/themes/default/{name}").exists()
        ]
        assert not missing, f"icon names not in this QGIS build: {missing}"

    def test_every_menu_row_that_claims_an_icon_has_one_set(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Catches a row added later without its entry in the icon table."""
        from nika_onlymap_exporter.ui.main_dialog import SHARE_MENU_ICONS

        dialog = self._dialog(project, make_memory_layer)
        try:
            assert set(SHARE_MENU_ICONS) == set(dialog.share_actions), (
                "every Share action should name an icon, and no icon should "
                "name an action that does not exist"
            )
        finally:
            dialog.close()


class TestNothingOutlivesTheDialog:
    """A plugin must leave no socket and no thread behind.

    The preview server holds a loopback port and a thread pool for as long as it
    runs. Every way the dialog can go away - the Close button, the window
    manager, `plugin.unload()` when QGIS quits - has to take it with them.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def _running_server(self, dialog, tmp_path):
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        server = PreviewServer(tmp_path)
        server.start()
        dialog._server = server
        return server

    def _port_is_open(self, port: int) -> bool:
        import socket

        probe = socket.socket()
        probe.settimeout(2)
        try:
            probe.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def test_closing_the_dialog_releases_the_port(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        server = self._running_server(dialog, tmp_path)
        port = server.port
        assert self._port_is_open(port), "the server should be listening first"

        dialog.close()

        assert dialog._server is None
        assert not self._port_is_open(port), "the port must not still be accepting"

    def test_plugin_unload_takes_the_server_with_it(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """QGIS quitting unloads plugins; that path must reach the server.

        `unload` calls `dialog.close()`, which is what fires `_shutdown`. If that
        chain ever breaks, a QGIS session would end with a socket still bound.
        """
        from nika_onlymap_exporter.plugin import Qgis2WebMapPlugin

        class FakeIface:
            def mainWindow(self):  # noqa: N802
                return None

            def removePluginWebMenu(self, *a):  # noqa: N802
                pass

            def removeToolBarIcon(self, *a):  # noqa: N802
                pass

        dialog = self._dialog(project, make_memory_layer)
        server = self._running_server(dialog, tmp_path)
        port = server.port

        plugin = Qgis2WebMapPlugin(FakeIface())
        plugin.dialog = dialog
        plugin.unload()

        assert plugin.dialog is None
        assert dialog._shut_down, "unload must reach the dialog's teardown"
        assert not self._port_is_open(port)

    def test_no_worker_thread_survives_the_close(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """A thread outliving the widgets it emits into is a QGIS crash."""
        import threading
        import time

        before = {t.name for t in threading.enumerate()}
        dialog = self._dialog(project, make_memory_layer)
        self._running_server(dialog, tmp_path)

        dialog.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            leaked = {
                t.name
                for t in threading.enumerate()
                if t.name.startswith("qgis2webmap")
            } - before
            if not leaked:
                break
            time.sleep(0.05)

        leaked = {
            t.name for t in threading.enumerate() if t.name.startswith("qgis2webmap")
        } - before
        assert not leaked, f"threads left running: {leaked}"

    def test_shutdown_is_idempotent(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """A window-manager close fires both `finished` and `closeEvent`."""
        dialog = self._dialog(project, make_memory_layer)
        self._running_server(dialog, tmp_path)

        dialog.close()
        dialog._shutdown()
        dialog.close()

        assert dialog._server is None


class TestAutosaveIsGatedOnALiveSession:
    """The plugin must not write to the user's disk uninvited.

    A live preview rebuilds the tab as the user works; it never writes their
    map file. Only Export does that.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_a_live_preview_never_writes_the_map_file(
        self,
        qgis_app,
        project,
        make_memory_layer,
        tmp_path,
        monkeypatch,
        runtime_required,
    ) -> None:
        """Through the real press, the real job and a real edit."""
        import time

        monkeypatch.setattr(
            "nika_onlymap_exporter.ui.main_dialog.QDesktopServices.openUrl",
            lambda url: True,
        )
        dialog = self._dialog(project, make_memory_layer)
        map_file = tmp_path / "peaks.html"

        def wait_for(condition) -> None:
            deadline = time.monotonic() + 60
            while not condition() and time.monotonic() < deadline:
                qgis_app.processEvents()
                time.sleep(0.02)
            assert condition()

        try:
            dialog.path_edit.setText(str(map_file))
            dialog.live_switch.setChecked(True)
            dialog.preview_button.click()
            wait_for(lambda: dialog._server is not None and dialog._job is None)
            assert dialog.preview_button.text() == "Live"

            dialog.state.map_name = "changed"
            dialog._poll_for_changes()
            wait_for(
                lambda: not dialog._rebuild_timer.isActive() and dialog._job is None
            )
            assert dialog._server is not None
            assert not map_file.exists()
        finally:
            dialog.close()

    def test_polling_does_not_stop_a_server_mid_reload(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """The reload race, at the level that would actually bite.

        A rebuild drops the page's `EventSource` and opens a new one, so the
        client count dips to zero every time. The poll must not read that dip as
        an abandoned preview and tear the server down under a working user.
        """
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        try:
            (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
            server = PreviewServer(tmp_path)
            dialog._server = server

            # A page connected and has just dropped, exactly as in a reload.
            import queue

            inbox: queue.Queue[str] = queue.Queue()
            server.register(inbox)
            server.unregister(inbox)

            assert dialog._preview_tab_was_closed() is False
            dialog._poll_for_changes()
            assert dialog._server is server, "a reload must not stop the server"
        finally:
            dialog._server = None
            dialog.close()

    def test_a_long_gone_tab_stops_the_server(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import main_dialog as module
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        try:
            (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
            server = PreviewServer(tmp_path)
            dialog._server = server

            import queue

            inbox: queue.Queue[str] = queue.Queue()
            server.register(inbox)
            server.unregister(inbox)

            # Past the grace period rather than sleeping through it.
            monkeypatch.setattr(module, "PREVIEW_IDLE_STOP_SECONDS", -1.0)

            assert dialog._preview_tab_was_closed() is True
            dialog._poll_for_changes()

            assert dialog._server is None
            assert "stopped" in dialog.status_label.text()
        finally:
            dialog._server = None
            dialog.close()


class TestShareBuildsWhatItNeeds:
    """No Share action should make the user produce a file first.

    Pressing Export before Copy file path was a step that existed only because
    the dialog could not do it itself. Each action now writes the map file when
    it is missing or out of date, and skips the write when it is neither.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_a_current_file_is_used_without_rebuilding(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            destination = tmp_path / "map.html"
            destination.write_text("<p>a map</p>", encoding="utf-8")
            dialog.path_edit.setText(str(destination))
            # What the file on disk claims to have been written from.
            dialog._written_signature = dialog.state.snapshot()

            exported: list[bool] = []
            dialog.on_export = lambda: exported.append(True)

            used: list = []
            dialog._with_current_map_file(used.append)

            assert used == [destination], "the existing file should be used as is"
            assert not exported, "nothing changed, so nothing should be rebuilt"
        finally:
            dialog.close()

    def test_a_stale_file_is_rebuilt_before_it_is_used(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """A map written before the last edit is the bug this prevents.

        0.1.x would happily hand over yesterday's export, because nothing tied
        the file on disk to the settings that produced it.
        """
        dialog = self._dialog(project, make_memory_layer)
        try:
            destination = tmp_path / "map.html"
            destination.write_text("<p>yesterday</p>", encoding="utf-8")
            dialog.path_edit.setText(str(destination))
            dialog._written_signature = dialog.state.snapshot()

            dialog.name_edit.setText("Renamed since it was written")
            assert dialog._map_file_is_stale()

            exported: list[bool] = []
            dialog.on_export = lambda: exported.append(True)

            used: list = []
            dialog._with_current_map_file(used.append)

            assert exported, "a stale file must be rewritten before it is shared"
            assert not used, "the follow-on waits for the write to land"
            assert dialog._export_then is not None
        finally:
            dialog.close()

    def test_a_fresh_share_zip_counts_as_current(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        """Only Standalone HTML used to be recorded, so Show in file manager
        after a ZIP or Folder export ran the whole export again."""
        import time

        for box in ("warning", "information"):
            monkeypatch.setattr(
                f"nika_onlymap_exporter.ui.main_dialog.QMessageBox.{box}",
                lambda *a, **k: None,
            )
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.mode_buttons[OutputMode.SHARE_ZIP].click()
            dialog.path_edit.setText(str(tmp_path / "map.zip"))
            dialog.on_export()
            deadline = time.monotonic() + 60
            while dialog._written_signature is None and time.monotonic() < deadline:
                qgis_app.processEvents()
                time.sleep(0.02)
            assert (tmp_path / "map.zip").exists()
            assert not dialog._map_file_is_stale()
            assert "changed since" not in dialog.save_status.text()

            # A restyle in QGIS changes no dialog setting, yet leaves the file
            # behind the project.
            for layer in project.mapLayers().values():
                layer.emitStyleChanged()
            for _ in range(3):
                qgis_app.processEvents()
            assert dialog._map_file_is_stale()
        finally:
            dialog.close()

    def test_a_qgis_change_during_the_read_is_not_written_over(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        """A restyle mid-export must leave the file, the cache and the report
        out of date, not have the finished job mark them current again."""
        import time

        for box in ("warning", "information"):
            monkeypatch.setattr(
                f"nika_onlymap_exporter.ui.main_dialog.QMessageBox.{box}",
                lambda *a, **k: None,
            )
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.mode_buttons[OutputMode.SHARE_ZIP].click()
            dialog.path_edit.setText(str(tmp_path / "map.zip"))
            dialog.on_export()
            # The read is on a worker; QGIS changes the project meanwhile.
            dialog._project_content_changed()
            deadline = time.monotonic() + 60
            while dialog._job is not None and time.monotonic() < deadline:
                qgis_app.processEvents()
                time.sleep(0.02)
            for _ in range(5):
                qgis_app.processEvents()
            assert (tmp_path / "map.zip").exists()
            assert dialog._map_file_is_stale()
            assert dialog._cached_export is None
            assert dialog._fidelity_is_stale
        finally:
            dialog.close()

    def test_a_folders_size_is_its_contents(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            folder = tmp_path / "peaks"
            (folder / "data").mkdir(parents=True)
            (folder / "index.html").write_text("<p>map</p>", encoding="utf-8")
            (folder / "data" / "big.bin").write_bytes(b"x" * 3 * 1024 * 1024)
            dialog.mode_buttons[OutputMode.FOLDER].click()
            dialog.path_edit.setText(str(folder))
            dialog._update_save_status()
            assert "3.0 MB" in dialog.save_status.text()

            # A folder that is not an export - typing "/" on the way to a
            # path - is never walked.
            walked: list[object] = []
            import pathlib

            real_rglob = pathlib.Path.rglob
            pathlib.Path.rglob = lambda self, *a: walked.append(self) or iter(())
            try:
                dialog.path_edit.setText(str(tmp_path))
                dialog._update_save_status()
            finally:
                pathlib.Path.rglob = real_rglob
            assert walked == []
            assert dialog.save_status.text() == "Not written yet."
        finally:
            dialog.close()

    def test_a_qgis_change_during_a_fidelity_check_leaves_it_out_of_date(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        import time

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._check_fidelity()
            dialog._project_content_changed()
            deadline = time.monotonic() + 60
            while dialog._job is not None and time.monotonic() < deadline:
                qgis_app.processEvents()
                time.sleep(0.02)
            for _ in range(5):
                qgis_app.processEvents()
            assert dialog._fidelity_is_stale
        finally:
            dialog.close()

    def test_a_file_this_session_never_wrote_is_not_trusted(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """An unrelated file at that path says nothing about the project."""
        dialog = self._dialog(project, make_memory_layer)
        try:
            destination = tmp_path / "map.html"
            destination.write_text("<p>someone else's</p>", encoding="utf-8")
            dialog.path_edit.setText(str(destination))

            assert dialog._written_signature is None
            assert dialog._map_file_is_stale()
        finally:
            dialog.close()

    def test_copying_the_path_never_builds(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """Waiting through a rebuild to learn where a file lives is absurd."""
        from qgis.PyQt.QtGui import QGuiApplication

        dialog = self._dialog(project, make_memory_layer)
        try:
            destination = tmp_path / "never-written.html"
            dialog.path_edit.setText(str(destination))

            exported: list[bool] = []
            dialog.on_export = lambda: exported.append(True)

            dialog.on_copy_map_path()

            assert QGuiApplication.clipboard().text() == str(destination)
            assert not exported
            assert not destination.exists()
        finally:
            dialog.close()

    def test_no_map_file_is_refused_with_an_explanation(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import main_dialog as module

        dialog = self._dialog(project, make_memory_layer)
        try:
            told: list[bool] = []
            monkeypatch.setattr(
                module.QMessageBox, "information", lambda *a, **k: told.append(True)
            )
            used: list = []
            dialog._with_current_map_file(used.append)

            assert not used
            assert told, "a refusal has to say what to do about it"
        finally:
            dialog.close()


class TestFidelityStrip:
    """The count is on screen from every tab, not only inside one."""

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_a_clean_export_says_nothing(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """An always-present "0 changes" trains people to ignore the strip."""
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        dialog = self._dialog(project, make_memory_layer)
        dialog._update_fidelity_strip(FidelityReportBuilder())
        assert dialog.fidelity_summary.text() == ""
        dialog.close()

    def test_it_counts_things_that_change(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        dialog = self._dialog(project, make_memory_layer)
        report = FidelityReportBuilder()
        report.unsupported("Layer 'a'", "Uses a renderer that does not travel.")
        dialog._update_fidelity_strip(report)
        assert "1 thing" in dialog.fidelity_summary.text()
        dialog.close()

    def test_blocked_layers_outrank_a_plain_count(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """ "Cannot be exported" is a different message from "will change"."""
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        dialog = self._dialog(project, make_memory_layer)
        report = FidelityReportBuilder()
        report.unsupported("Layer 'a'", "Something changes.")
        report.blocked("Layer 'b'", "Could not be read at all.")
        dialog._update_fidelity_strip(report)
        assert "cannot be exported" in dialog.fidelity_summary.text()
        dialog.close()


class TestExportSummary:
    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_it_names_the_artifact_the_map_file_is(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert "HTML" in dialog.export_summary.text()
        finally:
            dialog.close()

    def test_it_names_the_destination_once_one_is_chosen(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """The field's effect has to be visible without pressing anything."""
        dialog = self._dialog(project, make_memory_layer)
        try:
            destination = tmp_path / "peaks.html"
            dialog.path_edit.setText(str(destination))
            assert str(destination) in dialog.export_summary.text()
        finally:
            dialog.close()

    def test_it_says_nothing_when_no_layer_is_selected(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        dialog = self._dialog(project, make_memory_layer)
        for index in range(dialog.layer_tree.topLevelItemCount()):
            dialog.layer_tree.topLevelItem(index).setCheckState(
                1, Qt.CheckState.Unchecked
            )
        assert dialog.export_summary.text() == ""
        dialog.close()


class TestLivePreviewLifecycle:
    """The dialog owns a server and a thread; both must die with it.

    A leaked server keeps a socket open and a thread alive for the rest of the
    QGIS session, and a timer that fires against a closed dialog is a crash the
    user experiences as QGIS vanishing.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_no_server_starts_until_a_preview_is_asked_for(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Opening the dialog must not open a socket on the user's behalf."""
        dialog = self._dialog(project, make_memory_layer)
        assert dialog._server is None
        dialog.close()

    def test_shutdown_stops_the_server_and_its_thread(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        import threading

        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        dialog._server = PreviewServer(tmp_path)
        dialog._server.start()
        assert dialog._server.port > 0

        dialog.close()

        assert dialog._server is None
        remaining = [
            t.name for t in threading.enumerate() if t.name.startswith("qgis2webmap")
        ]
        assert not remaining, f"threads left running: {remaining}"

    def test_shutdown_stops_the_timers(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """A rebuild fired after close would run against a destroyed dialog."""
        dialog = self._dialog(project, make_memory_layer)
        dialog._watch_timer.start()
        dialog._rebuild_timer.start()

        dialog.close()

        assert not dialog._watch_timer.isActive()
        assert not dialog._rebuild_timer.isActive()

    def test_a_one_off_served_preview_is_never_rebuilt_as_live(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """A raster map's one-off preview is served but not live. A change in
        QGIS used to rewrite it with the auto-reload script added."""
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        dialog._server = PreviewServer(tmp_path)
        dialog._server.start()
        try:
            reads: list[str] = []
            dialog._ensure_export = lambda then, label, quiet=False: reads.append(label)
            dialog._project_content_changed()
            dialog.refresh_layers()
            assert not dialog._rebuild_timer.isActive()
            dialog._rebuild_live_preview()
            assert reads == [], "a one-off preview was rewritten as a live one"
        finally:
            dialog.close()

    def test_a_one_off_served_preview_stops_once_its_tab_closes(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """It used to run until the dialog closed: only a live session watched
        for the tab going away."""
        import time

        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        dialog._server = PreviewServer(tmp_path)
        dialog._server.start()
        try:
            # A page connected, then went away long enough ago.
            dialog._server._ever_connected = True
            dialog._server._empty_since = time.monotonic() - 3600
            dialog._idle_timer.start()
            dialog._stop_when_one_off_tab_closes()
            assert dialog._server is None
            assert not dialog._idle_timer.isActive()
        finally:
            dialog.close()

    def test_pressing_preview_again_stops_the_server(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        dialog._server = PreviewServer(tmp_path)
        dialog._server.start()
        dialog._watch_timer.start()

        dialog.preview_button.click()

        assert dialog._server is None
        assert "stopped" in dialog.status_label.text()
        dialog.close()

    def test_polling_does_nothing_without_a_server(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The watcher must be inert when live preview was never started."""
        dialog = self._dialog(project, make_memory_layer)
        dialog.state.map_name = "changed"
        dialog._poll_for_changes()
        assert not dialog._rebuild_timer.isActive()
        dialog.close()

    def test_share_is_disabled_until_there_is_somewhere_to_write(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """Share needs a destination, not an existing file.

        Requiring the file to exist would leave the menu dead on first open with
        nothing to press to wake it, so the rule is the path.
        """
        dialog = self._dialog(project, make_memory_layer)
        try:
            copy = dialog.share_actions["copy_path"]
            assert copy.isEnabled() is False

            dialog.path_edit.setText(str(tmp_path / "peaks.html"))
            assert copy.isEnabled() is True
        finally:
            dialog.close()

    def test_rows_that_never_touch_the_file_are_always_available(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The hosting waitlist is a demand test; hiding it until a path is
        chosen hid it from everyone who had not exported yet."""
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert dialog._map_file() is None
            assert dialog.share_actions["publish"].isEnabled()
            for key in ("copy_path", "reveal"):
                assert not dialog.share_actions[key].isEnabled(), key

            # A job greys out the file rows only, and the rest survive it.
            dialog._set_busy(True, "working")
            dialog._set_busy(False)
            assert dialog.share_actions["publish"].isEnabled()
        finally:
            dialog.close()

    def test_rows_not_built_yet_are_greyed_out_and_stay_so(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.path_edit.setText(str(tmp_path / "peaks.html"))
            dialog._set_busy(True, "working")
            dialog._set_busy(False)
            for key in ("enhance", "critique", "cartograph"):
                action = dialog.share_actions[key]
                assert not action.isEnabled(), key
                assert action.toolTip().startswith("Coming soon"), key
        finally:
            dialog.close()

    def test_a_refused_port_falls_back_to_a_file_preview(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        """A firewall or sandbox must downgrade the preview, not destroy it.

        The user asked for a preview and one exists on disk; raising a modal and
        opening nothing would be the worst of both.
        """
        dialog = self._dialog(project, make_memory_layer)
        entry = tmp_path / "index.html"
        entry.write_text("<p>x</p>", encoding="utf-8")

        opened: list[str] = []
        monkeypatch.setattr(
            "nika_onlymap_exporter.ui.main_dialog.QDesktopServices.openUrl",
            lambda url: opened.append(url.toString()),
        )

        dialog._fall_back_to_file_preview(entry)

        assert opened, "the preview should still open"
        assert dialog._server is None
        assert dialog.preview_button.text() == "Preview", (
            "the button must not claim a live preview that is not running"
        )
        assert "could not start" in dialog.status_label.text()
        dialog.close()


class TestDialogConstruction:
    """The dialog is real code and has to be executed, not just imported."""

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        for name in ("roads", "places"):
            project.addMapLayer(make_memory_layer(name, features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_builds_all_five_tabs(self, qgis_app, project, make_memory_layer) -> None:
        dialog = self._dialog(project, make_memory_layer)
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert titles == ["Map", "Layers", "Appearance", "Fidelity", "Settings", "Help"]
        dialog.close()

    def test_lists_the_project_layers(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.layer_tree.topLevelItemCount() == 2
        assert dialog.preview_button.isEnabled()
        dialog.close()

    def test_export_disables_with_a_stated_reason(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Never offer an export we know is broken."""
        from qgis.PyQt.QtCore import Qt

        dialog = self._dialog(project, make_memory_layer)
        for index in range(dialog.layer_tree.topLevelItemCount()):
            dialog.layer_tree.topLevelItem(index).setCheckState(
                1, Qt.CheckState.Unchecked
            )
        assert dialog.preview_button.isEnabled() is False
        from nika_onlymap_exporter.ui.main_dialog import SHARE_ACTIONS_ON_THE_FILE

        for key in SHARE_ACTIONS_ON_THE_FILE:
            assert dialog.share_actions[key].isEnabled() is False
        assert "at least one layer" in dialog.status_label.text()
        dialog.close()

    def test_list_follows_qgis_and_keeps_settings(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The incumbent's worst dialog defect, tested end to end."""
        from qgis.PyQt.QtCore import QCoreApplication, Qt

        dialog = self._dialog(project, make_memory_layer)
        first = dialog.layer_tree.topLevelItem(0)
        layer_id = first.data(0, Qt.ItemDataRole.UserRole)
        first.setCheckState(1, Qt.CheckState.Unchecked)

        project.addMapLayer(
            make_memory_layer("added-later", features=[("b", [3.0, 4.0])])
        )
        QCoreApplication.processEvents()

        assert dialog.layer_tree.topLevelItemCount() == 3
        # Settings live outside the widgets, so the rebuild did not reset them.
        assert dialog.state.for_layer(layer_id).include is False
        dialog.close()

    def test_layers_expand_to_one_row_per_field(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        first = dialog.layer_tree.topLevelItem(0)
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QLabel

        # The fixture's layers carry a single "name" field, and every layer
        # gains one options row above its fields.
        fields = _field_items(dialog, first)
        assert len(fields) == 1
        child = fields[0]
        assert child.data(0, Qt.ItemDataRole.UserRole) == "name"
        # And the user can actually read it: the name is drawn by the row widget.
        row = dialog.layer_tree.itemWidget(child, 0)
        assert row.findChild(QLabel).text() == "name"
        dialog.close()

    def test_choosing_a_field_mode_records_it(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        dialog = self._dialog(project, make_memory_layer)
        first = dialog.layer_tree.topLevelItem(0)
        layer_id = first.data(0, Qt.ItemDataRole.UserRole)

        combo = _mode_combo(dialog, _field_items(dialog, first)[0])
        combo.setCurrentIndex(combo.findData(PopupFieldMode.HEADER_ALWAYS.value))

        assert dialog.state.for_layer(layer_id).fields == {
            "name": PopupFieldMode.HEADER_ALWAYS.value
        }
        dialog.close()

    def test_field_modes_survive_a_layer_tree_rebuild(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Same guarantee as the checkboxes: state is keyed by id, not widget."""
        from qgis.PyQt.QtCore import QCoreApplication, Qt

        dialog = self._dialog(project, make_memory_layer)
        first = dialog.layer_tree.topLevelItem(0)
        layer_id = first.data(0, Qt.ItemDataRole.UserRole)
        combo = _mode_combo(dialog, _field_items(dialog, first)[0])
        combo.setCurrentIndex(combo.findData(PopupFieldMode.NO_LABEL.value))

        project.addMapLayer(
            make_memory_layer("added-later", features=[("b", [3.0, 4.0])])
        )
        QCoreApplication.processEvents()

        assert dialog.state.for_layer(layer_id).fields == {
            "name": PopupFieldMode.NO_LABEL.value
        }
        # And the rebuilt widget shows the surviving choice, not the default.
        rebuilt = next(
            dialog.layer_tree.topLevelItem(index)
            for index in range(dialog.layer_tree.topLevelItemCount())
            if dialog.layer_tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            == layer_id
        )
        widget = _mode_combo(dialog, _field_items(dialog, rebuilt)[0])
        assert widget.currentData() == PopupFieldMode.NO_LABEL.value
        dialog.close()

    def test_every_layer_offers_the_three_per_layer_overrides(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """qgis2web #131, #132 and #133 - all open since 2015."""
        from qgis.gui import QgsColorButton
        from qgis.PyQt.QtWidgets import QComboBox

        dialog = self._dialog(project, make_memory_layer)
        row = _options_row(dialog, dialog.layer_tree.topLevelItem(0))

        combos = row.findChildren(QComboBox)
        assert len(combos) == 2, "hover and precision"
        assert row.findChild(QgsColorButton) is not None, "highlight colour"
        # Everything starts on "same as the map", so an untouched project is
        # unchanged by the existence of this row.
        assert all(combo.currentData() is None for combo in combos)
        dialog.close()

    def test_a_per_layer_override_is_recorded(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QComboBox

        dialog = self._dialog(project, make_memory_layer)
        first = dialog.layer_tree.topLevelItem(0)
        layer_id = first.data(0, Qt.ItemDataRole.UserRole)

        hover = _options_row(dialog, first).findChildren(QComboBox)[0]
        hover.setCurrentIndex(hover.findData(True))

        assert dialog.state.for_layer(layer_id).popup_on_hover is True
        # And it beats the map-wide setting rather than merely matching it.
        assert dialog.state.for_layer(layer_id).resolved_hover(False) is True
        dialog.close()

    def test_apply_to_all_layers_sets_every_field(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        dialog.bulk_mode_combo.setCurrentIndex(
            dialog.bulk_mode_combo.findData(PopupFieldMode.HIDDEN.value)
        )
        dialog._on_apply_mode_to_all()

        modes = [
            settings.fields.get("name") for settings in dialog.state.layers.values()
        ]
        assert modes and all(mode == PopupFieldMode.HIDDEN.value for mode in modes)
        dialog.close()


class TestHelpTab:
    """Help must show the same guides the website serves."""

    def test_loads_every_bundled_guide(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.main_dialog import HELP_PAGES, load_help_markdown

        markdown = load_help_markdown()
        assert len(markdown) > 1000
        for title, _filename in HELP_PAGES:
            assert title in markdown

    def test_a_guide_title_is_not_printed_twice(self, qgis_app) -> None:
        """Regression: every guide showed its heading two times over.

        Most guides open with their own H1, because the website renders them as
        standalone pages. Prepending the HELP_PAGES title unconditionally put
        "The dialog, tab by tab" directly above "The dialog, tab by tab" for
        each of the eight that have one.
        """
        from nika_onlymap_exporter.ui.main_dialog import HELP_PAGES, load_help_markdown

        markdown = load_help_markdown()
        for title, _filename in HELP_PAGES:
            assert f"# {title}\n\n# {title}" not in markdown, title
            assert markdown.count(f"\n# {title}\n") <= 1, title

    def test_strips_website_front_matter(self, qgis_app) -> None:
        """YAML front matter is for Jekyll; Qt would render it as text."""
        from nika_onlymap_exporter.ui.main_dialog import load_help_markdown

        assert "title: QGIS2WebMap" not in load_help_markdown()

    def test_rendering_keeps_the_whole_document(self, qgis_app) -> None:
        """Regression: Qt's markdown parser ate over half the Help tab.

        The guides contain literal `<om-map>`, `<om-layer>` and `<script>` in
        code fences. With HTML parsing enabled - Qt's default - those are read
        as real elements and everything after the first one is swallowed, so
        the tab rendered 5,330 characters of 12,439 and showed empty bullets
        where the text had been.
        """
        from nika_onlymap_exporter.ui.main_dialog import (
            load_help_markdown,
            render_help_document,
        )

        source = load_help_markdown()
        rendered = render_help_document().toPlainText()

        # Markdown syntax disappears, so rendered is shorter - but not by half.
        assert len(rendered) > len(source) * 0.7, (
            f"rendering lost {len(source) - len(rendered)} characters; "
            "markdown HTML parsing is probably enabled again"
        )

    def test_content_after_a_code_fence_survives(self, qgis_app) -> None:
        """The specific text the swallowing bug removed."""
        from nika_onlymap_exporter.ui.main_dialog import render_help_document

        rendered = render_help_document().toPlainText()
        for phrase in (
            "Do not edit the runtime",
            "Keep the attribution",
            "a filter or search control",
        ):
            assert phrase in rendered, f"{phrase!r} was lost in rendering"

    def test_every_guide_reaches_the_rendered_document(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.main_dialog import (
            HELP_PAGES,
            render_help_document,
        )

        rendered = render_help_document().toPlainText()
        for title, _filename in HELP_PAGES:
            assert title in rendered, f"the {title!r} guide did not render"

    def test_the_browser_owns_its_document(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Regression: the Help document was freed under its browser.

        `setDocument` does not take ownership, so a parentless document went
        with its last Python reference and the dialog crashed on the next font
        change.
        """
        import gc

        dialog = TestTabCorner()._dialog(project, make_memory_layer)
        try:
            gc.collect()
            document = dialog.help_browser.document()
            assert document.parent() is dialog.help_browser
            assert "Do not edit the runtime" in document.toPlainText()
        finally:
            dialog.close()

    def test_falls_back_to_docs_in_a_git_checkout(self, qgis_app) -> None:
        """An installed plugin has help/; a clone has only docs/."""
        from nika_onlymap_exporter.ui.main_dialog import help_directory

        directory = help_directory()
        assert directory is not None
        assert (directory / "index.md").is_file()

    def test_help_tab_renders_the_guides(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtWidgets import QTextBrowser

        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("pts", features=[("a", [1.0, 2.0])]))
        dialog = MainDialog(FakeIface(), None)
        browser = dialog._help_page.findChild(QTextBrowser)

        text = browser.toPlainText()
        assert "one anonymous usage report" in text
        assert "Sharing a map" in text
        # The website carries the screenshots; this document must not. Checked
        # on the rendered text rather than the source, because this is the only
        # tier where Qt has actually parsed the Markdown - an image Qt could not
        # resolve would surface here and nowhere else.
        assert "![" not in text
        dialog.close()


class TestPreviewInjectionSafety:
    """Regression: the camera script was pasted into the runtime.

    `str.replace("</body>", ...)` replaces every occurrence, and the OnlyMap
    runtime contains a literal `</body>` inside a template literal - so the
    preview script landed in the middle of the minified library. It now goes
    through the template's own hook instead.
    """

    def _preview(self, project, make_memory_layer):
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project
        from nika_onlymap_exporter.ui.preview import write_preview

        project.addMapLayer(make_memory_layer("pts", features=[("a", [1.0, 2.0])]))
        export = read_project(project, FidelityReportBuilder())
        return write_preview(export, "injection-safety-test")

    def test_camera_script_appears_exactly_once(
        self, project, make_memory_layer, runtime_required
    ) -> None:
        html = self._preview(project, make_memory_layer).entry_path.read_text()
        assert html.count('const KEY = "qgis2webmap.camera"') == 1

    def test_it_sits_before_the_runtime_not_inside_it(
        self, project, make_memory_layer, runtime_required
    ) -> None:
        html = self._preview(project, make_memory_layer).entry_path.read_text()
        camera = html.index("qgis2webmap.camera")
        # Anchored on the template's own comment above the runtime block. Using
        # `<script type="module">` would match the camera script's own opening
        # tag, since it is a module too.
        runtime = html.index("The runtime. Inlined so this file works")
        assert camera < runtime, "the preview script must not be inside the runtime"

    def test_the_runtime_body_is_not_split_by_the_hook(
        self, project, make_memory_layer, runtime_required
    ) -> None:
        """The bug this guards: the runtime contains one literal `</body>`."""
        html = self._preview(project, make_memory_layer).entry_path.read_text()
        runtime_start = html.index("The runtime. Inlined so this file works")
        assert "qgis2webmap.camera" not in html[runtime_start:]


class TestDestinationField:
    """The export location, visible before pressing Export.

    Previously the only way to choose one was a file dialog that appeared after
    the button, so until then nothing on screen suggested the location was the
    user's to pick - which is what the team reported as "can I save it
    anywhere?".
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_it_starts_empty_rather_than_guessing(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """A guessed path is worse than no path.

        0.1.x stored only the *folder* of the last export, never the file name.
        Reconstructing a name from it would point at a file the user never
        wrote - and with autosave on, sit a second map beside their real one.
        """
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert dialog.path_edit.text().strip() == ""
            # Empty is not the same as broken: the placeholder has to say the
            # field is the user's to fill, or a blank field on first open reads
            # as the plugin having lost something.
            assert "hoose" in dialog.path_edit.placeholderText()
            assert "hoose" in dialog.save_status.text()
        finally:
            dialog.close()

    def test_a_stored_path_is_restored(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """Reopening the plugin reopens the same file."""
        from nika_onlymap_exporter.core.settings import DialogState, save_state

        destination = tmp_path / "himalaya-peaks.html"
        save_state(project, DialogState(map_file=str(destination)))

        dialog = self._dialog(project, make_memory_layer)
        try:
            assert dialog.path_edit.text() == str(destination)
        finally:
            dialog.close()

    def test_typing_a_path_records_it_on_the_state(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.path_edit.setText(str(tmp_path / "typed.html"))
            assert dialog.state.map_file == str(tmp_path / "typed.html")
        finally:
            dialog.close()

    def test_the_summary_names_the_destination(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        dialog.path_edit.setText(str(tmp_path / "mine.html"))
        assert "mine.html" in dialog.export_summary.text()
        dialog.close()

    def test_a_missing_folder_is_refused_rather_than_written_to(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui import main_dialog as module

        dialog = self._dialog(project, make_memory_layer)
        dialog.path_edit.setText(str(tmp_path / "no" / "such" / "map.html"))
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *a, **k: None)

        assert dialog._resolve_destination() is None
        dialog.close()

    def test_rewriting_the_map_file_is_never_confirmed(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        """The map file is the document. Rewriting it is the whole point.

        0.1.x asked "replace the existing file?" because every export was a
        fresh decision about a fresh destination. Asking that about the file the
        user is editing would be absurd - and with autosave it would be a modal
        every few seconds.
        """
        from nika_onlymap_exporter.ui import main_dialog as module

        existing = tmp_path / "map.html"
        existing.write_text("yesterday's map")

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.path_edit.setText(str(existing))

            asked = []
            monkeypatch.setattr(
                module.QMessageBox,
                "question",
                lambda *a, **k: (
                    asked.append(True) or module.QMessageBox.StandardButton.Cancel
                ),
            )
            assert dialog._resolve_destination() == existing
            assert not asked, "the map file is rewritten, not re-confirmed"
        finally:
            dialog.close()

    def test_no_map_file_is_explained_rather_than_written_somewhere(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        """An empty field must not fall back to a guess or a silent default."""
        from nika_onlymap_exporter.ui import main_dialog as module

        dialog = self._dialog(project, make_memory_layer)
        try:
            told = []
            monkeypatch.setattr(
                module.QMessageBox,
                "information",
                lambda *a, **k: told.append(True),
            )
            assert dialog._resolve_destination() is None
            assert told, "the user has to be told why nothing happened"
        finally:
            dialog.close()


class TestChromeChangesSkipTheRead:
    """Unticking a map control must not re-read half a million features.

    This is the reported freeze: with live preview on - the default - every
    settings change ran a full project read on the GUI thread, including
    changes that cannot affect a single feature.
    """

    def test_chrome_settings_are_absent_from_the_data_snapshot(self) -> None:
        from nika_onlymap_exporter.core.settings import CHROME_FIELDS, DialogState

        state = DialogState()
        before = state.data_snapshot()

        for name in CHROME_FIELDS:
            current = getattr(state, name)
            if isinstance(current, bool):
                setattr(state, name, not current)

        assert state.data_snapshot() == before, (
            "a chrome-only change must leave the cached read valid"
        )

    def test_chrome_settings_still_change_the_full_snapshot(self) -> None:
        """The live preview must still rebuild - it just must not re-read."""
        from nika_onlymap_exporter.core.settings import DialogState

        state = DialogState()
        before = state.snapshot()
        state.show_legend = not state.show_legend
        assert state.snapshot() != before

    def test_data_settings_do_invalidate_the_cache(self) -> None:
        from nika_onlymap_exporter.core.settings import DialogState

        state = DialogState()
        before = state.data_snapshot()
        state.quantize_precision = 4
        assert state.data_snapshot() != before

    def test_a_layer_checkbox_invalidates_the_cache(self) -> None:
        from nika_onlymap_exporter.core.settings import DialogState

        state = DialogState()
        before = state.data_snapshot()
        state.for_layer("roads").popup = False
        assert state.data_snapshot() != before

    def test_the_output_type_never_invalidates_the_cache(self) -> None:
        """Even under OpenStreetMap, whose one report row the type changes:
        that row is redrawn from the same read."""
        from nika_onlymap_exporter.core.settings import DialogState

        state = DialogState(basemap="osm", output_mode=OutputMode.FOLDER)
        before = state.data_snapshot()
        state.output_mode = OutputMode.STANDALONE_HTML
        assert state.data_snapshot() == before

    def test_switching_type_under_osm_redraws_the_warning_without_a_read(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.manifest_builder import OSM_FILE_NOTE

        dialog = TestFidelityStrip()._dialog(project, make_memory_layer)
        try:
            dialog.basemap_combo.setCurrentIndex(dialog.basemap_combo.findData("osm"))
            dialog.mode_buttons[OutputMode.FOLDER].click()
            export, report = dialog._read_current_project(None)
            signature = dialog.state.data_snapshot()
            dialog._cached_export, dialog._cached_report = export, report
            dialog._cached_signature = signature
            dialog._show_fidelity(report, signature)

            def details() -> list[str]:
                summary = dialog._fidelity_summary
                return [row.detail for g in summary.groups for row in g.rows]

            assert OSM_FILE_NOTE not in details()
            reads: list[str] = []
            dialog._start_job = lambda *a, **k: reads.append("read")
            dialog.mode_buttons[OutputMode.STANDALONE_HTML].click()
            assert not dialog._fidelity_is_stale
            assert OSM_FILE_NOTE in details()
            assert reads == []
        finally:
            dialog.close()


class TestOversizedSingleFileIsAWarningNotASwitch:
    """The dialog must never say one thing and write another.

    Reported against 0.1.x: exporting a large layer announced a switch to Share
    ZIP while the radio buttons still showed Standalone HTML selected. There is
    no mode to disagree with any more, so the rule is simply stated.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_the_size_rule_is_stated_on_the_map_tab(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The limit has to inform the choice, not arrive after it."""
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert "20 MB" in dialog.size_note.text()
        finally:
            dialog.close()


class TestLicenseKeyResolution:
    """A key still reaches the writer, but no longer through a dialog field.

    The field was removed once runtime 0.6.0 lifted the caps everywhere this
    dialog's artifacts are opened - a file, localhost, 127.0.0.1. A key now
    arrives from `ONLYMAP_LICENSE_KEY` or the stored setting, the same chain the
    Processing algorithm resolves, so these pin the plumbing rather than a
    widget.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_the_map_tab_has_no_licence_field(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """Pins the removal, so the field cannot return unnoticed."""
        dialog = self._dialog(project, make_memory_layer)
        assert not hasattr(dialog, "license_edit")
        assert not hasattr(dialog, "license_note")
        dialog.close()

    def test_a_stored_key_reaches_the_writer(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_license_key

        save_license_key("om_live_eyJhIjoxfQ.c2ln")
        try:
            dialog = self._dialog(project, make_memory_layer)
            policy = dialog._writer().license_policy
            assert getattr(policy, "license_key", None) == "om_live_eyJhIjoxfQ.c2ln"
            dialog.close()
        finally:
            save_license_key("")

    def test_the_environment_variable_reaches_the_writer(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        """The only route that works on a machine with no QGIS profile."""
        from nika_onlymap_exporter.core.settings import (
            LICENSE_KEY_ENV,
            save_license_key,
        )

        save_license_key("")
        monkeypatch.setenv(LICENSE_KEY_ENV, "om_live_ZW52.c2ln")
        dialog = self._dialog(project, make_memory_layer)
        policy = dialog._writer().license_policy
        assert getattr(policy, "license_key", None) == "om_live_ZW52.c2ln"
        dialog.close()

    def test_the_key_is_not_written_into_the_project_file(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """A `.qgz` gets emailed and committed. A purchased key must not ride along."""
        from nika_onlymap_exporter.core.settings import save_license_key

        save_license_key("om_live_eyJhIjoxfQ.c2ln")
        try:
            dialog = self._dialog(project, make_memory_layer)
            dialog._shutdown()

            entry, _ = project.readEntry("qgis2webmap", "layers")
            assert "om_live_" not in (entry or "")
            for key in ("mapName", "widgets", "outputMode"):
                stored, _ = project.readEntry("qgis2webmap", key)
                assert "om_live_" not in (stored or "")
        finally:
            save_license_key("")

    def test_no_key_means_the_free_tier_policy(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.license_policy import FreeTierPolicy
        from nika_onlymap_exporter.core.settings import save_license_key

        save_license_key("")
        dialog = self._dialog(project, make_memory_layer)
        assert isinstance(dialog._writer().license_policy, FreeTierPolicy)
        dialog.close()


class TestNoWastedHeight:
    """Wrapped text takes the height it needs and no more.

    Qt sizes a word-wrapped label in a form for a narrower width than it ends
    up with, then spreads the surplus as blank space: the basemap warning sat in
    84 px for 28 px of text, and the Map tab scrolled for nothing.
    """

    def test_wrapped_labels_fit_their_text(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtWidgets import QLabel

        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        dialog = MainDialog(FakeIface(), None)
        try:
            dialog.resize(1000, 720)
            dialog.show()
            dialog.basemap_combo.setCurrentIndex(dialog.basemap_combo.findData("osm"))
            for _ in range(5):
                qgis_app.processEvents()
            loose = [
                (label.text()[:40], label.height(), label.heightForWidth(label.width()))
                for label in dialog.tabs.widget(0).findChildren(QLabel)
                if label.wordWrap()
                and label.isVisible()
                and label.text()
                and label.height() > label.heightForWidth(label.width()) + 2
            ]
            assert not loose
        finally:
            dialog.close()


class TestExportIsBack:
    """0.1.4's flow: choose a type, choose where, press Export."""

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_three_types_with_a_single_file_first(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            buttons = dialog.mode_buttons
            assert list(buttons) == [
                OutputMode.STANDALONE_HTML,
                OutputMode.SHARE_ZIP,
                OutputMode.FOLDER,
            ]
            assert buttons[OutputMode.STANDALONE_HTML].isChecked()
        finally:
            dialog.close()

    def test_picking_a_type_reshapes_the_path(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.path_edit.setText(str(tmp_path / "peaks.html"))
            dialog.mode_buttons[OutputMode.SHARE_ZIP].setChecked(True)
            assert dialog.state.output_mode is OutputMode.SHARE_ZIP
            assert dialog.path_edit.text() == str(tmp_path / "peaks.zip")
            dialog.mode_buttons[OutputMode.FOLDER].setChecked(True)
            assert dialog.path_edit.text() == str(tmp_path / "peaks")
            dialog.mode_buttons[OutputMode.STANDALONE_HTML].setChecked(True)
            assert dialog.path_edit.text() == str(tmp_path / "peaks.html")
        finally:
            dialog.close()

    def test_export_writes_where_the_field_says_without_asking(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.mode_buttons[OutputMode.SHARE_ZIP].setChecked(True)
            dialog.path_edit.setText(str(tmp_path / "peaks.zip"))
            assert dialog._resolve_destination() == tmp_path / "peaks.zip"
        finally:
            dialog.close()

    def test_switching_type_mid_read_keeps_the_pressed_destination(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """The type is taken at the press, so the place must be too: otherwise
        a Folder export lands in a folder called peaks.html."""
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog.mode_buttons[OutputMode.FOLDER].setChecked(True)
            dialog.path_edit.setText(str(tmp_path / "peaks"))
            dialog._ensure_export = lambda then, label, quiet=False: None
            dialog.on_export()
            dialog.mode_buttons[OutputMode.STANDALONE_HTML].setChecked(True)
            assert dialog.path_edit.text().endswith("peaks.html")
            assert (
                dialog._resolve_destination(dialog._pending_destination)
                == tmp_path / "peaks"
            )
        finally:
            dialog.close()

    def test_the_corner_is_separate_buttons_with_export_last(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QToolButton

        dialog = self._dialog(project, make_memory_layer)
        try:
            corner = dialog.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            buttons = corner.findChildren(QToolButton)
            assert buttons[-1] is dialog.export_button
            assert all(b.property("role") in ("corner", "cornerPart") for b in buttons)
            # Preview and its live switch are one pill: the same parent frame.
            assert dialog.live_switch.parent() is dialog.preview_button.parent()
            assert dialog.preview_button.parent().property("role") == "cornerGroup"
            assert corner.layout().spacing() > 0
            # Share alone keeps a menu, and shows its arrow.
            assert dialog.share_button.property("hasMenu") is True
            assert dialog.preview_button.property("hasMenu") is False
            assert all(not b.icon().isNull() for b in buttons)
        finally:
            dialog.close()

    def test_export_is_the_filled_button_in_the_corner(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        dialog = self._dialog(project, make_memory_layer)
        try:
            corner = dialog.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            assert dialog.export_button.parent() is corner
            assert dialog.export_button.property("accent") is True
            assert dialog.preview_button.property("accent") is False
        finally:
            dialog.close()

    def test_share_no_longer_duplicates_export(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert "zip" not in dialog.share_actions
            assert "folder" not in dialog.share_actions
        finally:
            dialog.close()


def test_the_map_tab_warns_against_osm_for_a_single_file(
    qgis_app, project, make_memory_layer
) -> None:
    """Said where the basemap is chosen, and only for the types opened from disk."""
    from nika_onlymap_exporter.core.export_ir import OutputMode

    dialog = TestTabCorner()._dialog(project, make_memory_layer)
    try:
        dialog.basemap_combo.setCurrentIndex(dialog.basemap_combo.findData("osm"))
        dialog.mode_buttons[OutputMode.STANDALONE_HTML].click()
        assert "Access blocked" in dialog.basemap_warning.text()
        dialog.mode_buttons[OutputMode.FOLDER].click()
        assert "Access blocked" not in dialog.basemap_warning.text()
    finally:
        dialog.close()


def test_the_map_tab_says_nothing_under_a_basemap_that_works(
    qgis_app, project, make_memory_layer
) -> None:
    """Only OpenStreetMap from a file earns a note; the cost every basemap has
    is in the Fidelity tab, and repeating it under each choice was noise."""
    from nika_onlymap_exporter.core.export_ir import OutputMode

    dialog = TestTabCorner()._dialog(project, make_memory_layer)
    try:
        dialog.mode_buttons[OutputMode.STANDALONE_HTML].click()
        dialog.basemap_combo.setCurrentIndex(dialog.basemap_combo.findData("liberty"))
        assert dialog.basemap_warning.text() == ""
        assert not dialog.basemap_warning.isVisibleTo(dialog)
    finally:
        dialog.close()


def test_layer_row_drop_downs_open_attached(
    qgis_app, project, make_memory_layer
) -> None:
    """Built after the theme attached every other list, and again on each
    layer change, so they used to open as free-floating popups."""
    from qgis.PyQt.QtWidgets import QComboBox

    dialog = TestTabCorner()._dialog(project, make_memory_layer)
    try:
        project.addMapLayer(make_memory_layer("rivers", features=[("b", [3.0, 4.0])]))
        for _ in range(3):
            qgis_app.processEvents()
        combos = dialog.layer_tree.findChildren(QComboBox)
        assert combos
        for combo in combos:
            assert combo.view().window().property("attached"), combo.currentText()
    finally:
        dialog.close()


class TestLayersTabControls:
    def _expanded(self, project, make_memory_layer):
        dialog = TestTabCorner()._dialog(project, make_memory_layer)
        dialog.show()
        dialog.tabs.setCurrentIndex(1)  # laid out only once its tab is shown
        tree = dialog.layer_tree
        for index in range(tree.topLevelItemCount()):
            tree.expandItem(tree.topLevelItem(index))
        for _ in range(3):
            qgis_core.QgsApplication.processEvents()
        return dialog

    def test_no_control_in_a_row_is_cut_off(self, project, make_memory_layer) -> None:
        """The tree sized rows for text and squeezed the combos into them."""
        from qgis.PyQt.QtWidgets import QComboBox

        dialog = self._expanded(project, make_memory_layer)
        try:
            tree = dialog.layer_tree
            checked = 0
            for index in range(tree.topLevelItemCount()):
                layer = tree.topLevelItem(index)
                for child_index in range(layer.childCount()):
                    row = tree.itemWidget(layer.child(child_index), 0)
                    if row is None:
                        continue
                    for combo in row.findChildren(QComboBox):
                        checked += 1
                        assert row.height() >= combo.sizeHint().height()
            assert checked
        finally:
            dialog.close()

    def test_the_column_guide_sits_behind_an_info_button(
        self, project, make_memory_layer
    ) -> None:
        dialog = self._expanded(project, make_memory_layer)
        try:
            button = dialog.layers_info_button
            assert "Include" in button.toolTip()
            assert button.isVisibleTo(dialog)
            assert not button.icon().isNull()
        finally:
            dialog.close()


def test_text_that_points_into_share_names_a_row_it_has(
    qgis_app, project, make_memory_layer
) -> None:
    """The overhaul moved packaging out of Share, but the size note and the
    large-file prompt still sent people to "Share > Export share ZIP"."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "update_translations.py"
    spec = importlib.util.spec_from_file_location("update_translations", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    dialog = TestTabCorner()._dialog(project, make_memory_layer)
    try:
        rows = [a.text().replace("&", "") for a in dialog.share_actions.values()]
    finally:
        dialog.close()
    pointers = [s for s in module.collect() if "Share > " in s]
    for source in pointers:
        after = source.split("Share > ", 1)[1]
        assert any(after.startswith(row) for row in rows), source
