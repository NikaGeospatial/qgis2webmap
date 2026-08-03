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
        assert load_state(project).title_corner is OverlayCorner.TOP_LEFT

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
        assert "no tracking" in text
        assert "Sharing a map" in text
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
