"""Preview, Share and the Checks tab follow the edits made, not the polling.

Every test here stops the dialog's polling timers first, so only what the
setting-change handlers do themselves can mark the cartograph dirty. Real
widgets are driven: the field combo on the Layers tab, the layer's Popups
tick, the panel's own Check button.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import threading
import urllib.request

import pytest

qgis_core = pytest.importorskip("qgis.core")

from test_cartograph_dialog import (  # noqa: E402, F401
    _choose_cartograph,
    _choose_web,
    _dialog,
    _wait_for,
    fake_cartograph_export,
    picked_template,
)

SECRET_FIELD = "zz_secret_field"
KEPT_FIELD = "zz_kept_field"


def _quiet(dialog) -> None:
    """No polling: only direct change handling may notice an edit."""
    dialog._cartograph._status_timer.stop()
    dialog._fidelity_timer.stop()
    dialog._watch_timer.stop()


def _layer_item(dialog, name: str):
    tree = dialog.layer_tree
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.text(0) == name:
            return item
    raise AssertionError(f"no layer row {name!r}")


def _field_combo(dialog, layer_name: str, field: str):
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QComboBox

    layer_item = _layer_item(dialog, layer_name)
    layer_item.setExpanded(True)
    for index in range(layer_item.childCount()):
        child = layer_item.child(index)
        if child.data(0, Qt.ItemDataRole.UserRole) == field:
            row = dialog.layer_tree.itemWidget(child, 0)
            combo = row.findChild(QComboBox)
            assert combo is not None
            return combo
    raise AssertionError(f"no field row {field!r}")


def _hide_field(dialog, layer_name: str, field: str) -> None:
    from nika_onlymap_exporter.core.settings import PopupFieldMode

    combo = _field_combo(dialog, layer_name, field)
    combo.setCurrentIndex(combo.findData(PopupFieldMode.HIDDEN.value))


def _points_layer(project):
    """A WGS84 point layer with two attribute columns, so its frame is live
    and its popup fields are real."""
    layer = qgis_core.QgsVectorLayer(
        f"Point?crs=EPSG:4326&field={SECRET_FIELD}:string&field={KEPT_FIELD}:string",
        "sites",
        "memory",
    )
    feature = qgis_core.QgsFeature(layer.fields())
    feature.setAttributes(["needle-secret-value", "needle-kept-value"])
    feature.setGeometry(
        qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(103.8, 1.3))
    )
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    project.addMapLayer(layer)
    return layer


def _live_layout(project, layer):
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsLayoutItemMap,
        QgsPrintLayout,
        QgsRectangle,
    )
    from qgis.PyQt.QtCore import QRectF

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Sites")
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptSetSceneRect(QRectF(10, 20, 180, 140))
    map_item.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    map_item.setLayers([layer])
    map_item.setExtent(QgsRectangle(103.7, 1.2, 103.9, 1.4))
    map_item.setId("Overview")
    layout.addLayoutItem(map_item)
    project.layoutManager().addLayout(layout)
    return layout


@pytest.fixture
def opened(monkeypatch):
    """What the browser was asked to open: (url, file text at that moment)."""
    from qgis.PyQt.QtCore import QUrl

    import nika_onlymap_exporter.ui.cartograph.controller as controller_module

    seen: list[tuple[str, str]] = []

    def record(url) -> None:
        # Every preview is served at one stable address whatever the file is
        # called, so ask the server for what it publishes right now. The
        # print route hands the file over untouched, without the reload hook.
        page = QUrl(url)
        page.setQuery("print")
        encoded = bytes(page.toEncoded()).decode("ascii")
        with urllib.request.urlopen(encoded, timeout=10) as response:
            text = response.read().decode("utf-8")
        seen.append((url.toString(), text))

    monkeypatch.setattr(
        controller_module.CartographController,
        "_open_url",
        staticmethod(record),
    )
    return seen


# ---- Real export: the artifact carries the edit --------------------------


class TestImmediateOpenAfterAnEdit:
    @pytest.fixture
    def real(self, qgis_app, project, tmp_path, opened):
        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR (see CONTRIBUTING.md)")
        layer = _points_layer(project)
        _live_layout(project, layer)
        dialog = _dialog(project)
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        target = tmp_path / "sites.html"
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        assert _wait_for(qgis_app, lambda: target.exists(), timeout=90)
        assert _wait_for(
            qgis_app,
            lambda: (
                cartograph._job is None and not (cartograph._autosave_timer.isActive())
            ),
            timeout=90,
        )
        assert SECRET_FIELD in target.read_text(encoding="utf-8")
        _quiet(dialog)
        yield dialog
        dialog.close()

    def _share(self, dialog) -> None:
        dialog._cartograph.cartograph_share_actions["open_browser"].trigger()

    def test_preview_right_after_a_field_is_hidden(
        self, qgis_app, real, opened
    ) -> None:
        dialog = real
        _hide_field(dialog, "sites", SECRET_FIELD)
        dialog._cartograph.preview()
        assert _wait_for(qgis_app, lambda: opened, timeout=90)
        _url, text = opened[0]
        assert SECRET_FIELD not in text
        assert KEPT_FIELD in text

    def test_share_right_after_a_field_is_hidden(self, qgis_app, real, opened) -> None:
        dialog = real
        _hide_field(dialog, "sites", SECRET_FIELD)
        self._share(dialog)
        assert _wait_for(qgis_app, lambda: opened, timeout=90)
        assert SECRET_FIELD not in opened[0][1]
        assert KEPT_FIELD in opened[0][1]

    def test_preview_right_after_popups_are_switched_off(
        self, qgis_app, real, opened
    ) -> None:
        from qgis.PyQt.QtCore import Qt

        dialog = real
        target = dialog._cartograph._current_path()
        before = target.read_text(encoding="utf-8")
        _layer_item(dialog, "sites").setCheckState(2, Qt.CheckState.Unchecked)
        dialog._cartograph.preview()
        assert _wait_for(qgis_app, lambda: opened, timeout=90)
        assert opened[0][1] != before


# ---- Fake export: ordering rules -----------------------------------------


@pytest.fixture
def slow_export(monkeypatch, fake_cartograph_export):  # noqa: F811
    """The fake export, with a gate on its worker half and an artifact that
    records which fields were hidden when the build began."""
    import json

    import nika_onlymap_exporter.core.cartograph_export as export_module

    gate = threading.Event()
    gate.set()
    entered = threading.Event()

    def finish(prepared, project, report, *, writer=None, layer_settings=None):
        fake_cartograph_export.calls[-1]["finish_on_gui_thread"] = False
        entered.set()
        gate.wait(10)
        entered.clear()
        if fake_cartograph_export.should_fail["value"]:
            raise RuntimeError("synthetic export failure")
        fields = {lid: dict(s.fields) for lid, s in (layer_settings or {}).items()}
        return export_module.FinishedCartograph(
            html="<html>" + json.dumps(fields, sort_keys=True) + "</html>",
            projects=(),
        )

    monkeypatch.setattr(export_module, "finish_cartograph_full", finish)
    fake_cartograph_export.gate = gate
    fake_cartograph_export.entered = entered
    return fake_cartograph_export


@pytest.fixture
def fake_dialog(qgis_app, project, tmp_path, slow_export, opened):
    layer = _points_layer(project)
    _live_layout(project, layer)
    dialog = _dialog(project)
    _choose_cartograph(dialog)
    cartograph = dialog._cartograph
    target = tmp_path / "sites.html"
    cartograph.file_edit.setText(str(target))
    cartograph._on_file_edited(str(target))
    assert _wait_for(qgis_app, lambda: target.exists())
    assert _wait_for(
        qgis_app,
        lambda: cartograph._job is None and not cartograph._autosave_timer.isActive(),
    )
    _quiet(dialog)
    yield dialog
    slow_export.gate.set()
    dialog.close()


class TestReleaseRules:
    def test_share_waits_for_the_build_in_flight_and_the_edit_after_it(
        self, qgis_app, fake_dialog, slow_export, opened
    ) -> None:
        dialog = fake_dialog
        cartograph = dialog._cartograph
        slow_export.gate.clear()
        cartograph.schedule_autosave()
        cartograph._autosave_timer.stop()
        cartograph._run_autosave()
        assert _wait_for(qgis_app, slow_export.entered.is_set)
        # An edit made while that build runs.
        _hide_field(dialog, "sites", SECRET_FIELD)
        cartograph.cartograph_share_actions["open_browser"].trigger()
        assert opened == []
        slow_export.gate.set()
        assert _wait_for(qgis_app, lambda: opened)
        assert f'"{SECRET_FIELD}": "hidden"' in opened[0][1]
        assert len(opened) == 1

    def test_a_failed_build_releases_no_stale_artifact(
        self, qgis_app, fake_dialog, slow_export, opened
    ) -> None:
        dialog = fake_dialog
        cartograph = dialog._cartograph
        target = cartograph._current_path()
        old = target.read_text(encoding="utf-8")
        slow_export.should_fail["value"] = True
        _hide_field(dialog, "sites", SECRET_FIELD)
        cartograph.preview()
        assert _wait_for(qgis_app, lambda: cartograph._last_error != "")
        qgis_app.processEvents()
        assert opened == []
        assert cartograph._open_after_save == []
        assert target.read_text(encoding="utf-8") == old


# ---- Checks follow the output on screen -----------------------------------


class TestChecksDispatch:
    def test_cartograph_check_rebuilds_and_never_reads_the_web_map(
        self, qgis_app, fake_dialog, slow_export, monkeypatch
    ) -> None:
        dialog = fake_dialog
        reads: list[object] = []
        monkeypatch.setattr(dialog, "_ensure_export", lambda *a, **k: reads.append(a))
        before = dialog._cartograph._builds_started
        dialog.fidelity_panel.check_requested.emit()
        assert _wait_for(qgis_app, lambda: dialog._cartograph._builds_started > before)
        assert reads == []

    def test_web_map_check_still_reads_the_web_map(
        self, qgis_app, fake_dialog, slow_export, monkeypatch
    ) -> None:
        dialog = fake_dialog
        _choose_web(dialog)
        reads: list[object] = []
        monkeypatch.setattr(dialog, "_ensure_export", lambda *a, **k: reads.append(a))
        before = dialog._cartograph._builds_started
        dialog.fidelity_panel.check_requested.emit()
        assert len(reads) == 1
        assert dialog._cartograph._builds_started == before

    def test_initial_check_button_checks_the_selected_layout(
        self, qgis_app, project, tmp_path, slow_export
    ) -> None:
        layer = _points_layer(project)
        _live_layout(project, layer)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            _quiet(dialog)
            cartograph._autosave_timer.stop()
            assert _wait_for(qgis_app, lambda: cartograph._job is None)
            cartograph.file_edit.setText(str(tmp_path / "sites.html"))
            cartograph._last_summary = None
            cartograph.show_fidelity()
            panel = dialog.fidelity_panel
            assert panel.check_button.isVisibleTo(panel)
            panel.check_button.click()
            assert _wait_for(qgis_app, lambda: cartograph._last_summary is not None)
            assert cartograph._last_error == ""
            assert not panel.check_button.isVisibleTo(panel)
        finally:
            dialog.close()

    def test_try_again_after_a_failed_check(
        self, qgis_app, fake_dialog, slow_export
    ) -> None:
        dialog = fake_dialog
        cartograph = dialog._cartograph
        panel = dialog.fidelity_panel
        slow_export.should_fail["value"] = True
        panel.check_button.setVisible(True)
        panel.check_requested.emit()
        assert _wait_for(qgis_app, lambda: cartograph._last_error != "")
        assert panel.check_button.text() == "Try again"
        assert dialog.fidelity_strip.message.tone.name == "ERROR"
        slow_export.should_fail["value"] = False
        panel.check_button.click()
        assert _wait_for(qgis_app, lambda: cartograph._last_error == "")
        assert _wait_for(qgis_app, lambda: cartograph._job is None)
        assert not panel.check_button.isVisibleTo(panel)
        assert "could not finish" not in panel.headline.text()

    def test_a_stale_report_offers_check_again_and_the_button_works(
        self, qgis_app, fake_dialog, slow_export
    ) -> None:
        dialog = fake_dialog
        cartograph = dialog._cartograph
        panel = dialog.fidelity_panel
        slow_export.gate.clear()
        _hide_field(dialog, "sites", SECRET_FIELD)
        assert panel.check_button.isVisibleTo(panel)
        assert panel.check_button.text() == "Check again"
        slow_export.gate.set()
        assert _wait_for(qgis_app, lambda: cartograph._job is None)
        panel.check_button.click()
        assert _wait_for(
            qgis_app, lambda: cartograph._edits_saved >= cartograph._edits_made
        )
        assert _wait_for(qgis_app, lambda: not panel.check_button.isVisibleTo(panel))


class TestCartographAdvice:
    def test_the_report_has_no_web_map_only_advice(
        self, qgis_app, project, tmp_path, monkeypatch
    ) -> None:
        if not os.environ.get("ONLYMAP_RUNTIME_DIR"):
            pytest.skip("needs ONLYMAP_RUNTIME_DIR (see CONTRIBUTING.md)")
        import nika_onlymap_exporter.ui.cartograph.controller as controller_module

        captured: list[object] = []
        real_summarise = controller_module.summarise

        def spy(items, *args, **kwargs):
            captured.extend(items)
            return real_summarise(items, *args, **kwargs)

        monkeypatch.setattr(controller_module, "summarise", spy)
        layer = _points_layer(project)
        _live_layout(project, layer)
        dialog = _dialog(project)
        try:
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            target = tmp_path / "sites.html"
            cartograph.file_edit.setText(str(target))
            cartograph._on_file_edited(str(target))
            assert _wait_for(qgis_app, lambda: captured, timeout=90) or target.exists()
            assert _wait_for(qgis_app, lambda: target.exists(), timeout=90)
            assert _wait_for(qgis_app, lambda: cartograph._job is None, timeout=90)
            text = " ".join(f"{i.subject} {i.detail}" for i in captured)
            assert "Map name" not in text
            assert "Map title" not in text
        finally:
            dialog.close()
