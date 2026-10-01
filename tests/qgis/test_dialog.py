"""Dialog behaviour: settings persistence, the live layer list, and preview.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

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
            output_mode=OutputMode.SHARE_ZIP,
            show_legend=False,
        )
        state.layers["abc"] = LayerSettings(include=False, popup=False, label=True)
        save_state(project, state)

        restored = load_state(project)
        assert restored.map_name == "My map"
        assert restored.output_mode is OutputMode.SHARE_ZIP
        assert restored.show_legend is False
        assert restored.layers["abc"].include is False
        assert restored.layers["abc"].label is True

    def test_empty_project_yields_sensible_defaults(self, project) -> None:
        state = load_state(project)
        assert state.map_name == ""
        assert state.output_mode is OutputMode.STANDALONE_HTML
        assert state.show_legend is True

    def test_corrupt_entries_do_not_prevent_opening(self, project) -> None:
        """A bad setting must never stop the dialog; worst case is a re-pick."""
        project.writeEntry("qgis2webmap", "outputMode", "not-a-mode")
        project.writeEntry("qgis2webmap", "layers", "{{{ not json")
        state = load_state(project)
        assert state.output_mode is OutputMode.STANDALONE_HTML
        assert state.layers == {}

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


def _points(name: str):
    return qgis_core.QgsVectorLayer(
        "Point?crs=EPSG:4326&field=n:string", name, "memory"
    )


def _restyle(layer) -> None:
    """What Apply in Layer Properties does: a new renderer, then the signal."""
    layer.setRenderer(
        qgis_core.QgsSingleSymbolRenderer(
            qgis_core.QgsMarkerSymbol.createSimple({"color": "red"})
        )
    )
    layer.emitStyleChanged()


class TestLayerContentWatcher:
    """Restyling a layer moves nothing in the tree, so the tree watcher never saw
    it: the Fidelity report and the dialog's cached read stayed "current"."""

    def test_restyles_collapse_into_one_change(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerContentWatcher

        layers = [_points("a"), _points("b")]
        for layer in layers:
            project.addMapLayer(layer)
        watcher = LayerContentWatcher(project)
        changes = []
        watcher.changed.connect(lambda: changes.append(1))

        for layer in layers:
            _restyle(layer)
        QCoreApplication.processEvents()
        assert changes == [1]
        watcher.disconnect_all()

    def test_a_labels_only_apply_counts_as_a_change(self, qgis_app, project) -> None:
        """Label, opacity and scale-range edits change no renderer: Apply sends
        `styleChanged` and nothing else."""
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerContentWatcher

        layer = _points("a")
        project.addMapLayer(layer)
        watcher = LayerContentWatcher(project)
        changes = []
        watcher.changed.connect(lambda: changes.append(1))

        layer.setLabeling(
            qgis_core.QgsVectorLayerSimpleLabeling(qgis_core.QgsPalLayerSettings())
        )
        layer.setLabelsEnabled(True)
        layer.emitStyleChanged()
        QCoreApplication.processEvents()
        assert changes == [1]
        watcher.disconnect_all()

    def test_committed_edits_count_as_a_change(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerContentWatcher

        layer = _points("a")
        project.addMapLayer(layer)
        watcher = LayerContentWatcher(project)
        changes = []
        watcher.changed.connect(lambda: changes.append(1))

        layer.startEditing()
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(1, 2))
        )
        layer.addFeature(feature)
        assert layer.commitChanges()
        QCoreApplication.processEvents()
        assert changes == [1]
        watcher.disconnect_all()

    def test_it_follows_layers_in_and_out_of_the_project(
        self, qgis_app, project
    ) -> None:
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerContentWatcher

        watcher = LayerContentWatcher(project)
        changes = []
        watcher.changed.connect(lambda: changes.append(1))

        late = _points("late")
        project.addMapLayer(late)
        assert late.id() in watcher.watched_layer_ids()
        _restyle(late)
        QCoreApplication.processEvents()
        assert changes == [1], "a layer added after the dialog opened was not watched"

        # Removal releases the layer's connections before QGIS deletes it.
        kept = _points("kept")
        project.addMapLayer(kept)
        project.takeMapLayer(kept)
        assert kept.id() not in watcher.watched_layer_ids()
        assert kept.receivers(kept.styleChanged) == 0
        watcher.disconnect_all()

    def test_disconnect_all_leaves_nothing_connected(self, qgis_app, project) -> None:
        from qgis.PyQt.QtCore import QCoreApplication

        from nika_onlymap_exporter.ui.layer_watcher import LayerContentWatcher

        layer = _points("a")
        project.addMapLayer(layer)
        # Baselines, not zero: QGIS connects to some of these itself.
        before = layer.receivers(layer.styleChanged)
        before_renderer = layer.receivers(layer.rendererChanged)
        watcher = LayerContentWatcher(project)
        assert layer.receivers(layer.styleChanged) == before + 1
        assert layer.receivers(layer.rendererChanged) == before_renderer + 1
        changes = []
        watcher.changed.connect(lambda: changes.append(1))

        watcher.disconnect_all()
        assert layer.receivers(layer.styleChanged) == before
        assert layer.receivers(layer.rendererChanged) == before_renderer
        _restyle(layer)
        after = _points("after")
        project.addMapLayer(after)
        _restyle(after)
        QCoreApplication.processEvents()
        assert changes == []
        assert watcher.watched_layer_ids() == []


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


class TestOutputModeIsExclusive:
    """Three mutually exclusive choices, expressed as such.

    They were checkboxes that unpicked each other by hand, which promises
    multi-select without delivering it and reads wrong to a screen reader.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_they_are_radio_buttons(self, qgis_app, project, make_memory_layer) -> None:
        from qgis.PyQt.QtWidgets import QRadioButton

        dialog = self._dialog(project, make_memory_layer)
        assert dialog.mode_checks
        for button in dialog.mode_checks.values():
            assert isinstance(button, QRadioButton)
        dialog.close()

    def test_exactly_one_is_ever_selected(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        for mode in OutputMode:
            dialog.mode_checks[mode].click()
            chosen = [m for m, b in dialog.mode_checks.items() if b.isChecked()]
            assert chosen == [mode]
            assert dialog.state.output_mode is mode
        dialog.close()

    def test_the_group_enforces_it_rather_than_the_handler(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.mode_group.exclusive()
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

    def test_it_names_the_artifact_the_chosen_mode_produces(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        dialog.mode_checks[OutputMode.SHARE_ZIP].click()
        assert "zip" in dialog.export_summary.text()

        dialog.mode_checks[OutputMode.STANDALONE_HTML].click()
        assert "HTML" in dialog.export_summary.text()
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

    def test_turning_live_preview_off_stops_the_server(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui.live_server import PreviewServer

        dialog = self._dialog(project, make_memory_layer)
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        # Establish the starting state rather than inheriting it: `setChecked`
        # emits nothing when the value already matches, so a box that was
        # already unticked would make this pass for the wrong reason.
        dialog.live_check.setChecked(True)
        dialog._server = PreviewServer(tmp_path)
        dialog._server.start()

        dialog.live_check.setChecked(False)

        assert dialog._server is None
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

    def test_open_exported_map_is_disabled_until_there_is_one(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.open_export_button.isEnabled() is False
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
        assert dialog.live_check.isChecked() is False, "the checkbox must match reality"
        assert dialog._server is None
        assert "could not start" in dialog.status_label.text()
        dialog.close()

    def test_the_fallback_does_not_re_enter_teardown(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        """Clearing the checkbox must not fire `_on_live_toggled` recursively."""
        dialog = self._dialog(project, make_memory_layer)
        entry = tmp_path / "index.html"
        entry.write_text("<p>x</p>", encoding="utf-8")
        monkeypatch.setattr(
            "nika_onlymap_exporter.ui.main_dialog.QDesktopServices.openUrl",
            lambda url: None,
        )

        calls: list[bool] = []
        original = dialog._on_live_toggled
        monkeypatch.setattr(
            dialog, "_on_live_toggled", lambda value: calls.append(value) or original
        )

        dialog._fall_back_to_file_preview(entry)
        assert calls == [], "the signal should be blocked while correcting the box"
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
        assert titles == ["Map", "Layers", "Appearance", "Fidelity", "Help"]
        dialog.close()

    def test_lists_the_project_layers(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.layer_tree.topLevelItemCount() == 2
        assert dialog.export_button.isEnabled()
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
        assert dialog.export_button.isEnabled() is False
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


class TestHelpLabels:
    """A hint sits directly under its control, as tall as its text.

    In a form row a wrapped label took the height of a four-line guess and
    centred one line in it, which left a blank gap above and below every hint
    on the Map and Appearance tabs.
    """

    def _form(self, qgis_app, indent: int = 0):
        from qgis.PyQt.QtWidgets import QCheckBox, QFormLayout, QWidget

        from nika_onlymap_exporter.ui.main_dialog import _help_label

        page = QWidget()
        form = QFormLayout(page)
        check = QCheckBox("Map title", page)
        form.addRow(check)
        hint = _help_label("A sentence that fits on one line.", page, indent)
        form.addRow("", hint)
        page.resize(1100, 400)
        page.show()
        qgis_app.processEvents()
        return page, check, hint

    def test_a_one_line_hint_is_one_line_tall(self, qgis_app) -> None:
        page, _check, hint = self._form(qgis_app)
        line = hint.fontMetrics().lineSpacing()
        assert hint.height() < 2 * line
        page.close()

    def test_the_text_starts_at_the_top_of_the_hint(self, qgis_app) -> None:
        from qgis.PyQt.QtCore import Qt

        page, _check, hint = self._form(qgis_app)
        assert hint.alignment() & Qt.AlignmentFlag.AlignTop
        page.close()

    def test_an_indent_moves_the_text_right_but_not_down(self, qgis_app) -> None:
        """`QLabel.setIndent` also indents from the top when top-aligned."""
        page, _check, hint = self._form(qgis_app, indent=20)
        margins = hint.contentsMargins()
        assert margins.left() == 20
        assert margins.top() == 0
        assert hint.indent() <= 0
        page.close()

    def test_an_empty_basemap_warning_takes_no_row(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        dialog = MainDialog(FakeIface(), None)
        assert dialog.basemap_warning.isHidden()
        dialog.basemap_combo.setCurrentIndex(1)
        assert not dialog.basemap_warning.isHidden()
        assert dialog.basemap_warning.text()
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
        browser = dialog.tabs.widget(4).findChild(QTextBrowser)

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

    def test_it_is_prefilled(self, qgis_app, project, make_memory_layer) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.path_edit.text().strip(), "the field must suggest somewhere"
        dialog.close()

    def test_the_suffix_follows_the_chosen_packaging(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """A zip export must not offer to write a `.html`."""
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        dialog._on_mode_selected(OutputMode.STANDALONE_HTML)
        assert dialog.path_edit.text().endswith(".html")

        dialog._on_mode_selected(OutputMode.SHARE_ZIP)
        assert dialog.path_edit.text().endswith(".zip")

        # A folder is a directory name, so it carries no extension at all.
        dialog._on_mode_selected(OutputMode.FOLDER)
        assert not Path(dialog.path_edit.text()).suffix
        dialog.close()

    def test_changing_mode_keeps_the_folder_the_user_chose(
        self, qgis_app, project, make_memory_layer, tmp_path
    ) -> None:
        """Only the extension is corrected. The location is theirs."""
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        dialog.path_edit.setText(str(tmp_path / "somewhere" / "mine.html"))
        dialog._on_mode_selected(OutputMode.SHARE_ZIP)

        chosen = Path(dialog.path_edit.text())
        assert chosen.parent == tmp_path / "somewhere"
        assert chosen.stem == "mine"
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
        from nika_onlymap_exporter.core.export_ir import OutputMode
        from nika_onlymap_exporter.ui import main_dialog as module

        dialog = self._dialog(project, make_memory_layer)
        dialog.path_edit.setText(str(tmp_path / "no" / "such" / "map.html"))
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *a, **k: None)

        assert dialog._resolve_destination(OutputMode.STANDALONE_HTML) is None
        dialog.close()

    def test_an_existing_file_is_never_replaced_without_asking(
        self, qgis_app, project, make_memory_layer, tmp_path, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import OutputMode
        from nika_onlymap_exporter.ui import main_dialog as module

        existing = tmp_path / "map.html"
        existing.write_text("yesterday's map")

        dialog = self._dialog(project, make_memory_layer)
        dialog.path_edit.setText(str(existing))

        asked = []
        monkeypatch.setattr(
            module.QMessageBox,
            "question",
            lambda *a, **k: (
                asked.append(True) or module.QMessageBox.StandardButton.Cancel
            ),
        )
        assert dialog._resolve_destination(OutputMode.STANDALONE_HTML) is None
        assert asked, "overwriting must be confirmed, not assumed"
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


class TestOversizedSingleFileIsAWarningNotASwitch:
    """The dialog must never say one thing and write another.

    Reported: exporting a large layer announced a switch to Share ZIP while the
    radio buttons still showed Standalone HTML selected.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_selecting_a_mode_moves_the_radio_button(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        dialog._on_mode_selected(OutputMode.SHARE_ZIP)

        assert dialog.mode_checks[OutputMode.SHARE_ZIP].isChecked()
        assert not dialog.mode_checks[OutputMode.STANDALONE_HTML].isChecked()
        dialog.close()

    def test_the_size_rule_is_stated_on_the_map_tab(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The limit has to inform the choice, not arrive after it."""
        from nika_onlymap_exporter.core.export_ir import OutputMode

        dialog = self._dialog(project, make_memory_layer)
        dialog._on_mode_selected(OutputMode.STANDALONE_HTML)
        assert "20 MB" in dialog.size_note.text()

        # Nothing to warn about when the data is not inlined.
        dialog._on_mode_selected(OutputMode.FOLDER)
        assert dialog.size_note.text() == ""
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


class TestHostButtonLabel:
    """The button says what pressing it will do to the map, not what it is.

    A project that has published once keeps its address, so the second press
    replaces what is at it rather than creating one - and a button that goes on
    saying Host is the only warning the user gets that it might not.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def test_an_unpublished_project_offers_to_host(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.host_button.text().startswith("Host")
        dialog.close()

    def test_a_published_project_offers_to_republish(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, "map_abc123")
        dialog = self._dialog(project, make_memory_layer)
        assert dialog.host_button.text().startswith("Republish")
        dialog.close()

    def test_the_label_follows_the_project_rather_than_the_dialog(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        """The dialog is non-modal, so the project can change underneath it."""
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        dialog = self._dialog(project, make_memory_layer)
        assert dialog.host_button.text().startswith("Host")
        save_hosted_map_id(project, "map_abc123")
        dialog._update_host_button()
        assert dialog.host_button.text().startswith("Republish")
        dialog.close()


class TestThePublishConfirmation:
    """That the confirmation actually carries the warnings, not just that the
    strings exist.

    `hosting.consent` is unit-tested and proves nothing about this screen: a
    warning nobody prepends is a warning nobody sees. That exact gap - helper
    covered, caller not - is how a folder export shipped with no vector data at
    all on 2026-09-18, so the caller is pinned here.
    """

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def _informative_text(self, monkeypatch, dialog, basemap: str) -> str:
        """Run `_confirm_publish` against a QMessageBox that records and cancels."""
        from qgis.PyQt.QtWidgets import QMessageBox

        from nika_onlymap_exporter.core.export_ir import ExportSettings

        captured: dict[str, str] = {}
        real_exec = QMessageBox.exec

        def fake_exec(box):
            captured["text"] = box.informativeText()
            # Never show it: an exec() in a headless run would block forever.
            return int(QMessageBox.StandardButton.Cancel)

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)

        class FakeExport:
            title = "Test map"
            settings = ExportSettings(basemap=basemap)
            exportable_layers = ()

        try:
            dialog._confirm_publish(FakeExport(), ())
        finally:
            monkeypatch.setattr(QMessageBox, "exec", real_exec)
        return captured.get("text", "")

    def test_the_osm_warning_reaches_the_screen(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            text = self._informative_text(monkeypatch, dialog, "osm")
        finally:
            dialog.close()

        assert "OpenStreetMap" in text
        # And the consent itself is still there - the warning is prepended to it,
        # never in place of it.
        assert "about to be uploaded" in text

    def test_an_ordinary_basemap_leaves_the_screen_unchanged(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        try:
            text = self._informative_text(monkeypatch, dialog, "positron")
        finally:
            dialog.close()

        assert "OpenStreetMap" not in text
        assert "about to be uploaded" in text
        # No leading blank lines from an empty notice joined in regardless.
        assert text == text.lstrip()


class TestHostButtonFollowsTheServer:
    """The label and the publish path, driven by what the SERVER says.

    A map taken down in the dashboard, deleted, or owned by another organisation
    left its id in the `.qgz`, the button kept saying Republish, and every press
    was refused: a project that could never be published again. Nothing here
    touches the network - the dialog is never shown, so the background watch is
    never started, and the publish stages run against stand-ins.
    """

    MAP_ID = "k7m2qx9vt4bdp3w8n5r2h6j9c"

    def _dialog(self, project, make_memory_layer):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        return MainDialog(FakeIface(), None)

    def _state(self, presence, map_id=None, **fields):
        from nika_onlymap_exporter.hosting.map_state import RemoteMapState

        return RemoteMapState(map_id=map_id or self.MAP_ID, presence=presence, **fields)

    @pytest.mark.parametrize("presence", ["taken_down", "missing", "other_org"])
    def test_a_dead_map_offers_host_as_new(
        self, qgis_app, project, make_memory_layer, presence
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._on_remote_state(self._state(presence))
            assert dialog.host_button.text() == "Host as new map ↗"
            assert "new map" in dialog.host_button.toolTip()
        finally:
            dialog.close()

    @pytest.mark.parametrize("presence", ["live", "paused", "offline", "signed_out"])
    def test_an_updatable_or_unknown_map_stays_republish(
        self, qgis_app, project, make_memory_layer, presence
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._on_remote_state(self._state(presence))
            assert dialog.host_button.text() == "Republish ↗"
        finally:
            dialog.close()

    def test_opening_another_project_forgets_the_last_answer(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._on_remote_state(self._state("taken_down"))
            dialog._on_project_switched()
            assert dialog.host_button.text() == "Republish ↗"
        finally:
            dialog.close()

    def test_the_new_map_notice_reaches_the_confirmation(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        from nika_onlymap_exporter.core.export_ir import ExportSettings

        captured: dict[str, str] = {}
        monkeypatch.setattr(
            QMessageBox,
            "exec",
            lambda box: captured.setdefault("text", box.informativeText()) and 0,
        )

        class FakeExport:
            title = "Test map"
            settings = ExportSettings(basemap="positron")
            exportable_layers = ()

        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._confirm_publish(FakeExport(), (), "OLD MAP WAS TAKEN DOWN.")
        finally:
            dialog.close()
        assert captured["text"].startswith("OLD MAP WAS TAKEN DOWN.")
        assert "about to be uploaded" in captured["text"]

    def _confirmation_notice(self, monkeypatch, dialog, tmp_path) -> list[str]:
        """Reach the publish confirmation and decline it; return its notice."""

        class FakeExport:
            title = "Test map"

        notices: list[str] = []

        def confirm(_export, _files, map_notice=""):
            notices.append(map_notice)
            return False

        monkeypatch.setattr(dialog, "_publish_thumbnail", lambda _export: b"")
        monkeypatch.setattr(dialog, "_confirm_publish", confirm)
        dialog._publish_staging = tmp_path
        dialog._confirm_and_reserve(FakeExport(), "desk_x", None, {"files": []})
        return notices

    def test_republishing_a_held_map_says_it_gives_up_the_hold(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """The server forgets a downgrade's renewal hold on ANY republish, so
        the press that does it has to say so while it can still be cancelled."""
        from datetime import datetime, timezone

        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        hold = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        try:
            dialog._on_remote_state(self._state("paused", downgrade_hold_until=hold))
            tooltip = dialog.host_button.toolTip()
            notices = self._confirmation_notice(monkeypatch, dialog, tmp_path)
        finally:
            dialog.close()
        assert "waiting to come back automatically" in tooltip
        assert len(notices) == 1
        assert "waiting to come back automatically" in notices[0]
        assert (
            "October 2026. Republishing it now takes it off that list." in (notices[0])
        )

    def test_an_ordinary_republish_carries_no_notice(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_hosted_map_id

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        try:
            dialog._on_remote_state(self._state("paused"))
            notices = self._confirmation_notice(monkeypatch, dialog, tmp_path)
        finally:
            dialog.close()
        assert notices == [""]

    def _run_jobs_inline(self, monkeypatch, dialog) -> None:
        """Every stage runs its work and its callback on the spot."""
        from nika_onlymap_exporter.ui import main_dialog

        class InlineProgress:
            def step(self, _percent, _message):
                pass

            def check_cancelled(self):
                pass

        def start_job(work, on_success, label, quiet=False, failure_class="other"):
            on_success(work(InlineProgress()))
            return True

        monkeypatch.setattr(dialog, "_start_job", start_job)
        monkeypatch.setattr(main_dialog, "should_warn_truncation", lambda *_a: False)

    def test_a_refused_republish_can_become_a_new_map(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """The server proves the map is gone; the user agrees; the stale id is
        dropped and the SAME build is reserved again as a new map."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            load_hosted_pending_release,
            save_hosted_map_id,
            save_hosted_release_n,
        )
        from nika_onlymap_exporter.hosting.client import PublishRefusedError

        save_hosted_map_id(project, self.MAP_ID)
        save_hosted_release_n(project, 3)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        asked: list[bool] = []
        monkeypatch.setattr(
            dialog, "_confirm_host_as_new", lambda: asked.append(True) or True
        )
        uploaded: list[object] = []
        monkeypatch.setattr(
            dialog, "_upload", lambda exporter, prepared: uploaded.append(prepared)
        )

        class Start:
            release_id = "rel_new"
            renders_under_caps = False

        class Prepared:
            start = Start()

        class FakeExporter:
            def __init__(self):
                self.map_id = TestHostButtonFollowsTheServer.MAP_ID
                self.release_n = 3
                self.pending_release_id = None
                self.reconciliation = None
                self.force = False
                self.on_progress = None
                self.calls: list[str | None] = []

            def prepare(self, _result, _destination):
                self.calls.append(self.map_id)
                if self.map_id:
                    raise PublishRefusedError("taken down", code="map_taken_down")
                return Prepared()

        exporter = FakeExporter()
        try:
            dialog._reserve(exporter, None, tmp_path, [], force=False)
            assert asked == [True]
            assert exporter.calls == [self.MAP_ID, None]
            assert exporter.release_n is None
            # Still linked to the old map: the new one does not exist until
            # its upload succeeds, and a refusal before then must not leave
            # the project pointing at nothing.
            assert load_hosted_map_id(project) == self.MAP_ID
            assert load_hosted_pending_release(project) == "rel_new"
            assert len(uploaded) == 1
        finally:
            dialog.close()

    def test_a_new_map_the_plan_refuses_leaves_the_project_linked(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """Agreeing to a new map and then hitting the plan's map limit used to
        leave the project cut loose from its old map, permanently once saved."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            load_hosted_release_n,
            save_hosted_map_id,
            save_hosted_release_n,
        )
        from nika_onlymap_exporter.hosting.client import PublishRefusedError
        from nika_onlymap_exporter.ui import main_dialog

        save_hosted_map_id(project, self.MAP_ID)
        save_hosted_release_n(project, 3)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        monkeypatch.setattr(dialog, "_confirm_host_as_new", lambda: True)
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, text, *a, **k: warnings.append((title, text)),
        )

        class FakeExporter:
            map_id = TestHostButtonFollowsTheServer.MAP_ID
            release_n = 3
            pending_release_id = None
            reconciliation = None
            force = False
            on_progress = None

            def prepare(self, _result, _destination):
                if self.map_id:
                    raise PublishRefusedError("gone", code="map_taken_down")
                raise PublishRefusedError("Plan full.", code="map_limit_reached")

        try:
            dialog._reserve(FakeExporter(), None, tmp_path, [], force=False)
            assert load_hosted_map_id(project) == self.MAP_ID
            assert load_hosted_release_n(project) == 3
        finally:
            dialog.close()
        assert warnings == [("Cannot publish", "Plan full.")]

    def test_declining_keeps_the_project_as_it_was(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )
        from nika_onlymap_exporter.hosting.client import PublishRefusedError

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        monkeypatch.setattr(dialog, "_confirm_host_as_new", lambda: False)

        class FakeExporter:
            map_id = TestHostButtonFollowsTheServer.MAP_ID
            release_n = 1
            pending_release_id = None
            reconciliation = None
            force = False
            on_progress = None

            def prepare(self, _result, _destination):
                raise PublishRefusedError("gone", code="map_forbidden")

        try:
            dialog._reserve(FakeExporter(), None, tmp_path, [], force=False)
            assert load_hosted_map_id(project) == self.MAP_ID
            assert dialog.host_button.text() == "Host as new map ↗"
            assert "Nothing left this machine" in dialog.status_label.text()
        finally:
            dialog.close()

    @pytest.mark.parametrize("accepted", [False, True])
    def test_an_expired_free_map_offers_a_new_map(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path, accepted
    ) -> None:
        """`free_map_expired`: the free plan will not revive the map but would
        take a new one, so the server's sentence comes with that offer - not a
        dead-end warning, not a sign-in, not a cleared token. Declining leaves
        everything as it was; accepting reserves the same build as a new map,
        and the project keeps its old id until the new map exists."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )
        from nika_onlymap_exporter.hosting.client import PublishRefusedError
        from nika_onlymap_exporter.ui import main_dialog

        message = (
            "Free maps stay online for 7 days, and this map's ended on "
            "2026-09-20. Publishing again cannot bring it back on the free "
            "plan - talk to us about enterprise hosting to restore it."
        )
        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, text, *a, **k: warnings.append((title, text)),
        )
        cleared: list[bool] = []
        monkeypatch.setattr(main_dialog, "clear_token", lambda: cleared.append(True))
        signed_in: list[object] = []
        monkeypatch.setattr(dialog, "_sign_in_again", signed_in.append)
        asked: list[str] = []
        monkeypatch.setattr(
            dialog,
            "_confirm_expired_as_new",
            lambda refusal: asked.append(refusal) or accepted,
        )
        uploaded: list[object] = []
        monkeypatch.setattr(
            dialog, "_upload", lambda exporter, prepared: uploaded.append(prepared)
        )

        class Start:
            release_id = "rel_new"
            renders_under_caps = False

        class Prepared:
            start = Start()

        class FakeExporter:
            def __init__(self):
                self.map_id = TestHostButtonFollowsTheServer.MAP_ID
                self.release_n = 2
                self.pending_release_id = None
                self.reconciliation = None
                self.force = False
                self.on_progress = None
                self.calls: list[str | None] = []

            def prepare(self, _result, _destination):
                self.calls.append(self.map_id)
                if self.map_id:
                    raise PublishRefusedError(message, code="free_map_expired")
                return Prepared()

        exporter = FakeExporter()
        try:
            dialog._reserve(exporter, None, tmp_path, [], force=False)
            assert asked == [message]
            assert warnings == []
            assert cleared == [] and signed_in == []
            assert load_hosted_map_id(project) == self.MAP_ID
            if accepted:
                assert exporter.calls == [self.MAP_ID, None]
                assert len(uploaded) == 1
            else:
                assert exporter.calls == [self.MAP_ID]
                assert uploaded == []
                assert "Nothing left this machine" in dialog.status_label.text()
        finally:
            dialog.close()

    def test_the_expired_map_question_says_what_happens_to_both_addresses(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        captured: dict[str, str] = {}

        def exec_box(box):
            captured.update(text=box.text(), info=box.informativeText())
            return 0

        monkeypatch.setattr(QMessageBox, "exec", exec_box)
        dialog = self._dialog(project, make_memory_layer)
        try:
            assert dialog._confirm_expired_as_new("SERVER SAYS EXPIRED.") is False
        finally:
            dialog.close()
        assert captured["text"] == "SERVER SAYS EXPIRED."
        assert "The old address stays offline" in captured["info"]
        assert "fresh 7 days" in captured["info"]

    def test_a_password_protected_map_is_not_described_as_open_to_anyone(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.settings import load_hosted_pending_release

        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        shown: dict[str, object] = {}
        monkeypatch.setattr(
            dialog,
            "_show_published",
            lambda url, link_saved=True, protected=False, state=None: shown.update(
                url=url, protected=protected
            ),
        )
        state = self._state("live", has_password=True)

        class Outcome:
            public_url = "https://maps.example/m"
            open_instruction = "Published at x - anyone with the link can open it."

        class FakeClient:
            def map_state(self, _map_id):
                return state

        class FakeExporter:
            client = FakeClient()
            on_progress = None
            stage = "verifying"

            def publish(self, _prepared):
                return Outcome()

        class Start:
            map_id = TestHostButtonFollowsTheServer.MAP_ID
            release_n = 1
            release_id = "rel_1"

        class Prepared:
            start = Start()

        from nika_onlymap_exporter.core.settings import save_hosted_pending_release

        # Settled by the success, so the next publish has nothing to look for.
        save_hosted_pending_release(project, "rel_1")
        try:
            dialog._upload(FakeExporter(), Prepared())
            assert shown["protected"] is True
            assert "password" in dialog.status_label.text()
            assert "anyone" not in dialog.status_label.text()
            assert load_hosted_pending_release(project) == ""
        finally:
            dialog.close()

    def _publish_and_capture(
        self, monkeypatch, dialog, state, map_id=MAP_ID, during=None
    ) -> dict[str, str]:
        """Run the upload stage against stand-ins; return what the box said.

        `map_id` is the map the exporter updates (None for a new map), and
        `during` runs while the upload is in flight - where a user opening
        another project would."""
        from qgis.PyQt.QtWidgets import QMessageBox

        captured: dict[str, str] = {}

        def exec_box(box):
            captured["title"] = box.windowTitle()
            captured["text"] = box.text()
            captured["info"] = box.informativeText()
            return 0

        monkeypatch.setattr(QMessageBox, "exec", exec_box)

        class Outcome:
            public_url = "https://maps.example/m"
            open_instruction = "Published at x - anyone with the link can open it."

        class FakeClient:
            def map_state(self, _map_id):
                return state

        updating = map_id

        class FakeExporter:
            client = FakeClient()
            on_progress = None
            stage = "verifying"
            map_id = updating

            def publish(self, _prepared):
                if during is not None:
                    during()
                return Outcome()

        class Start:
            map_id = TestHostButtonFollowsTheServer.MAP_ID
            release_n = 1
            release_id = "rel_1"

        class Prepared:
            start = Start()

        dialog._upload(FakeExporter(), Prepared())
        captured["status"] = dialog.status_label.text()
        return captured

    def _open_another_project(self, project, make_memory_layer, tmp_path):
        """What File > Open does to `QgsProject.instance()`: same object, new
        contents. Returns a function that does it, for `during`."""

        def switch():
            other = tmp_path / "other.qgz"
            project.clear()
            project.addMapLayer(make_memory_layer("rivers"))
            project.write(str(other))
            project.read(str(other))

        return switch

    @pytest.mark.parametrize("map_id", [MAP_ID, None])
    def test_a_publish_finishing_after_a_project_switch_writes_nothing(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path, map_id
    ) -> None:
        """Project A uploads; the user opens project B. A's address must not
        land in B - B's next Republish would replace A's public map - and B
        must not be saved on A's behalf. The link is still handed over."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            load_hosted_pending_release,
            load_hosted_release_n,
        )

        first = tmp_path / "first.qgz"
        project.write(str(first))
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        persisted: list[bool] = []
        monkeypatch.setattr(
            dialog, "_persist_hosted_link", lambda: persisted.append(True) or True
        )
        dialog._publish_project = dialog._mark_project()
        try:
            shown = self._publish_and_capture(
                monkeypatch,
                dialog,
                self._state("live"),
                map_id=map_id,
                during=self._open_another_project(project, make_memory_layer, tmp_path),
            )
            assert project.fileName().endswith("other.qgz")
            assert load_hosted_map_id(project) == ""
            assert load_hosted_release_n(project) is None
            assert load_hosted_pending_release(project) == ""
            assert dialog.host_button.text() == "Host ↗"
        finally:
            dialog.close()
        assert persisted == []
        assert shown["title"] == "Published - address not saved"
        assert "https://maps.example/m" in shown["info"]
        assert "You opened a different project" in shown["info"]
        assert "'first.qgz'" in shown["info"]

    def test_the_same_project_reopened_still_gets_its_address(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """Reloading the very file the publish started from is not a switch:
        writing the address into it is what the publish was for."""
        from nika_onlymap_exporter.core.settings import load_hosted_map_id

        first = tmp_path / "first.qgz"
        project.write(str(first))
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        monkeypatch.setattr(dialog, "_persist_hosted_link", lambda: True)
        dialog._publish_project = dialog._mark_project()
        try:
            shown = self._publish_and_capture(
                monkeypatch,
                dialog,
                self._state("live"),
                during=lambda: project.read(str(first)),
            )
            assert load_hosted_map_id(project) == self.MAP_ID
        finally:
            dialog.close()
        assert shown["title"] == "Published"

    def test_a_switch_before_the_upload_stops_the_publish(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """Switched while the address was being reserved: no reconciliation is
        written, no pending release, and no upload starts."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            load_hosted_pending_release,
        )
        from nika_onlymap_exporter.exporters.hosted import PendingReconciliation
        from nika_onlymap_exporter.ui import main_dialog

        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, text, *a, **k: warnings.append((title, text)),
        )
        uploaded: list[object] = []
        monkeypatch.setattr(
            dialog, "_upload", lambda exporter, prepared: uploaded.append(prepared)
        )
        switch = self._open_another_project(project, make_memory_layer, tmp_path)

        class Start:
            release_id = "rel_new"
            renders_under_caps = False

        class Prepared:
            start = Start()

        class FakeExporter:
            map_id = None
            release_n = None
            pending_release_id = "rel_old"
            force = False
            on_progress = None
            reconciliation = None

            def prepare(self, _result, _destination):
                switch()
                self.reconciliation = PendingReconciliation(
                    settled=True,
                    adopted_map_id=TestHostButtonFollowsTheServer.MAP_ID,
                    adopted_release_n=4,
                )
                return Prepared()

        dialog._publish_project = dialog._mark_project()
        try:
            dialog._reserve(FakeExporter(), None, tmp_path, [], force=False)
            assert load_hosted_map_id(project) == ""
            assert load_hosted_pending_release(project) == ""
        finally:
            dialog.close()
        assert uploaded == []
        assert [title for title, _text in warnings] == ["Not published"]
        assert "nothing was uploaded" in warnings[0][1]

    def test_a_switch_before_the_confirmation_stops_the_publish(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui import main_dialog

        dialog = self._dialog(project, make_memory_layer)
        warnings: list[str] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, _text, *a, **k: warnings.append(title),
        )
        dialog._publish_project = dialog._mark_project()
        self._open_another_project(project, make_memory_layer, tmp_path)()
        try:
            notices = self._confirmation_notice(monkeypatch, dialog, tmp_path)
        finally:
            dialog.close()
        assert notices == []
        assert warnings == ["Not published"]

    def test_a_publish_onto_a_paused_map_does_not_say_online(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            shown = self._publish_and_capture(
                monkeypatch, dialog, self._state("paused")
            )
        finally:
            dialog.close()
        assert "online" not in shown["text"]
        assert "paused, so visitors can't see it yet" in shown["text"]
        assert "Anyone with this link can open it." not in shown["info"]
        assert "anyone" not in shown["status"]

    def test_a_publish_onto_a_live_map_says_online(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            shown = self._publish_and_capture(monkeypatch, dialog, self._state("live"))
        finally:
            dialog.close()
        assert shown["text"].startswith("Your map is online.")
        assert "Anyone with this link can open it." in shown["info"]
        assert "republishing does not extend it" not in shown["info"]

    def test_a_free_maps_end_date_is_shown_after_publishing(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from datetime import datetime, timezone

        # Midday, so the day is the same on every test machine's calendar.
        expires = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            shown = self._publish_and_capture(
                monkeypatch, dialog, self._state("live", expires_at=expires)
            )
            tooltip = dialog.host_button.toolTip()
        finally:
            dialog.close()
        assert "stays online until" in shown["info"]
        assert "October 2026; republishing does not extend it." in shown["info"]
        assert "republishing does not extend it" in tooltip

    def _upload_failing(
        self, monkeypatch, dialog, failure, release_status, map_id=MAP_ID
    ):
        """Run the upload stage with a publish that raises `failure` while the
        server is checking it; return the warnings and crash boxes shown.

        `map_id` is the map the exporter is updating: None for a new map."""
        from nika_onlymap_exporter.ui import main_dialog

        warnings: list[tuple[str, str]] = []
        crashes: list[str] = []
        counted: list[str] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, text, *a, **k: warnings.append((title, text)),
        )
        monkeypatch.setattr(
            main_dialog, "show_failure", lambda _p, title, _m: crashes.append(title)
        )
        monkeypatch.setattr(dialog._research, "record_failure", counted.append)

        class FakeClient:
            def release_status(self, _release_id):
                return release_status

        updating = map_id

        class FakeExporter:
            client = FakeClient()
            on_progress = None
            stage = "verifying"
            map_id = updating

            def publish(self, _prepared):
                raise failure

        class Start:
            map_id = updating or "n" * 25
            release_n = 1
            release_id = "rel_1"

        class Prepared:
            start = Start()

        dialog._upload(FakeExporter(), Prepared())
        return warnings, crashes, counted

    def test_a_plan_refusal_after_the_upload_is_not_a_crash(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        """The server re-checks the plan when it activates a release and fails
        the release with the refusal's own sentence. That is "Cannot publish",
        not "Something went wrong - please report it"."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )
        from nika_onlymap_exporter.hosting.client import HostingError, ReleaseStatus

        reason = "Your plan hosts 1 map, and you already have 1."
        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            warnings, crashes, counted = self._upload_failing(
                monkeypatch,
                dialog,
                HostingError(
                    f"The hosting server could not publish this map.\n\n{reason}"
                ),
                ReleaseStatus(state="failed", error=reason),
            )
            assert load_hosted_map_id(project) == self.MAP_ID
        finally:
            dialog.close()
        assert warnings == [("Cannot publish", reason)]
        assert crashes == []
        assert counted == []

    @pytest.mark.parametrize(
        ("retry", "title"), [(True, "Not published"), (False, "Cannot publish")]
    )
    def test_a_coded_failure_is_titled_by_whether_trying_again_helps(
        self, qgis_app, project, make_memory_layer, monkeypatch, retry, title
    ) -> None:
        from nika_onlymap_exporter.hosting.client import ReleaseFailedError

        message = "Your map was not published; nothing changed online."
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            warnings, crashes, counted = self._upload_failing(
                monkeypatch,
                dialog,
                ReleaseFailedError(message, "upload_incomplete", retry=retry),
                None,
            )
        finally:
            dialog.close()
        assert warnings == [(title, message)]
        assert crashes == []
        assert counted == []

    def test_losing_contact_after_the_upload_is_not_a_crash(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.hosting.client import (
            VERIFY_CONTACT_LOST_MESSAGE,
            HostingError,
        )

        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            warnings, crashes, _counted = self._upload_failing(
                monkeypatch, dialog, HostingError(VERIFY_CONTACT_LOST_MESSAGE), None
            )
        finally:
            dialog.close()
        assert warnings == [("Upload not confirmed", VERIFY_CONTACT_LOST_MESSAGE)]
        assert crashes == []

    def test_an_expired_upload_link_is_a_message_not_a_crash(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.hosting.client import UploadLinkExpiredError

        message = "Uploading took longer than the 60 minutes NIKA allows."
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            warnings, crashes, counted = self._upload_failing(
                monkeypatch, dialog, UploadLinkExpiredError(message), None
            )
        finally:
            dialog.close()
        assert warnings == [("Not published", message)]
        assert crashes == []
        assert counted == []

    def test_a_previous_publish_still_verifying_is_a_plain_message(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """Raised from `prepare` when the last attempt is still being checked:
        the exporter's own sentence, with no sign-in and no new-map question."""
        from nika_onlymap_exporter.hosting.client import PublishRefusedError
        from nika_onlymap_exporter.ui import main_dialog

        message = "Your last publish is still being checked. Try again shortly."
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            main_dialog.QMessageBox,
            "warning",
            lambda _parent, title, text, *a, **k: warnings.append((title, text)),
        )
        crashes: list[str] = []
        monkeypatch.setattr(
            main_dialog, "show_failure", lambda _p, title, _m: crashes.append(title)
        )

        class FakeExporter:
            map_id = None
            release_n = None
            pending_release_id = "rel_old"
            reconciliation = None
            force = False
            on_progress = None

            def prepare(self, _result, _destination):
                raise PublishRefusedError(message, code="previous_publish_verifying")

        try:
            dialog._reserve(FakeExporter(), None, tmp_path, [], force=False)
        finally:
            dialog.close()
        assert warnings == [("Cannot publish", message)]
        assert crashes == []

    def test_a_new_map_refused_after_the_upload_keeps_the_old_link(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )
        from nika_onlymap_exporter.hosting.client import HostingError, ReleaseStatus

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            self._upload_failing(
                monkeypatch,
                dialog,
                HostingError("The hosting server could not publish this map."),
                ReleaseStatus(state="failed", error="Your plan is full."),
                map_id=None,
            )
            assert load_hosted_map_id(project) == self.MAP_ID
        finally:
            dialog.close()

    def test_a_new_maps_unconfirmed_upload_is_adopted_next_time(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        """The upload of a NEW map went through and contact was lost. The old
        id is let go then - and only then - with the pending release kept, so
        the next press adopts the new map instead of measuring it against the
        old one."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            load_hosted_pending_release,
            save_hosted_map_id,
            save_hosted_pending_release,
        )
        from nika_onlymap_exporter.hosting.client import (
            VERIFY_CONTACT_LOST_MESSAGE,
            HostingError,
        )

        save_hosted_map_id(project, self.MAP_ID)
        save_hosted_pending_release(project, "rel_1")
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            self._upload_failing(
                monkeypatch,
                dialog,
                HostingError(VERIFY_CONTACT_LOST_MESSAGE),
                None,
                map_id=None,
            )
            assert load_hosted_map_id(project) == ""
            assert load_hosted_pending_release(project) == "rel_1"
        finally:
            dialog.close()

    def test_a_new_map_replaces_the_old_link_once_it_exists(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )

        save_hosted_map_id(project, "o" * 25)
        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            self._publish_and_capture(monkeypatch, dialog, self._state("live"))
            assert load_hosted_map_id(project) == self.MAP_ID
        finally:
            dialog.close()

    def test_confirming_a_new_map_does_not_forget_the_old_one_yet(
        self, qgis_app, project, make_memory_layer, monkeypatch, tmp_path
    ) -> None:
        """Host as new map, confirmed: the exporter is told, the project is
        not - a refusal of the new map must leave it linked to the old one."""
        from nika_onlymap_exporter.core.settings import (
            load_hosted_map_id,
            save_hosted_map_id,
        )
        from nika_onlymap_exporter.ui import main_dialog

        class FakeExport:
            title = "Test map"

        save_hosted_map_id(project, self.MAP_ID)
        dialog = self._dialog(project, make_memory_layer)
        reserved: list[object] = []
        monkeypatch.setattr(main_dialog, "detect_violations", lambda _export: [])
        monkeypatch.setattr(dialog, "_publish_thumbnail", lambda _export: b"")
        monkeypatch.setattr(dialog, "_confirm_publish", lambda *_a: True)
        monkeypatch.setattr(
            dialog, "_reserve", lambda exporter, *_a, **_k: reserved.append(exporter)
        )
        dialog._publish_staging = tmp_path
        try:
            dialog._on_remote_state(self._state("taken_down"))
            dialog._confirm_and_reserve(FakeExport(), "desk_x", None, {"files": []})
            assert load_hosted_map_id(project) == self.MAP_ID
        finally:
            dialog.close()
        assert len(reserved) == 1
        assert reserved[0].map_id is None

    def test_a_failure_the_server_did_not_decide_is_still_a_failure(
        self, qgis_app, project, make_memory_layer, monkeypatch
    ) -> None:
        """A release still being checked is no verdict: the original error
        goes on to the failure path rather than being dressed as a refusal."""
        from nika_onlymap_exporter.hosting.client import HostingError, ReleaseStatus

        dialog = self._dialog(project, make_memory_layer)
        self._run_jobs_inline(monkeypatch, dialog)
        try:
            with pytest.raises(HostingError, match="still verifying"):
                self._upload_failing(
                    monkeypatch,
                    dialog,
                    HostingError("The hosting server is still verifying this map"),
                    ReleaseStatus(state="verifying"),
                )
        finally:
            dialog.close()

    def test_cancel_after_the_upload_does_not_claim_nothing_was_written(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        dialog = self._dialog(project, make_memory_layer)

        class FakeExporter:
            stage = "verifying"

        try:
            dialog._active_exporter = FakeExporter()
            dialog._on_job_cancelled()
            assert "Nothing was written" not in dialog.status_label.text()
            assert "most likely update anyway" in dialog.status_label.text()
            # An export's cancel still says what it always did.
            dialog._on_job_cancelled()
            assert dialog.status_label.text() == "Stopped. Nothing was written."
        finally:
            dialog.close()
