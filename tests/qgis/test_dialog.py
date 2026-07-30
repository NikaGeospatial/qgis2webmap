"""Dialog behaviour: settings persistence, the live layer list, and preview.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import OutputMode
from nika_onlymap_exporter.core.settings import (
    DialogState,
    LayerSettings,
    load_state,
    save_state,
)

qgis_core = pytest.importorskip("qgis.core")


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
        self, project, make_memory_layer
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
        self, project, make_memory_layer, tmp_path
    ) -> None:
        """Camera persistence is a preview affordance, not part of a deliverable."""
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project
        from nika_onlymap_exporter.packaging.artifact_builder import build_artifact

        project.addMapLayer(make_memory_layer("pts", features=[("a", [1.0, 2.0])]))
        export = read_project(project, FidelityReportBuilder())

        _result, outcome = build_artifact(export, tmp_path / "map.html")
        assert "om-view-changed" not in outcome.path.read_text()


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


class TestHelpTab:
    """Help must show the same guides the website serves."""

    def test_loads_every_bundled_guide(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.main_dialog import HELP_PAGES, load_help_markdown

        markdown = load_help_markdown()
        assert len(markdown) > 1000
        for title, _filename in HELP_PAGES:
            assert title in markdown

    def test_strips_website_front_matter(self, qgis_app) -> None:
        """YAML front matter is for Jekyll; Qt would render it as text."""
        from nika_onlymap_exporter.ui.main_dialog import load_help_markdown

        assert "title: QGIS2WebMap" not in load_help_markdown()

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
        assert "no tracking" in text
        assert "Sharing a map" in text
        dialog.close()
