"""Settings reaching the preview and the saved project, and the preview server
stopping once nobody is watching.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


def _dialog(project, make_memory_layer):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    class FakeIface:
        def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
            return None

    project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
    return MainDialog(FakeIface(), None)


def test_a_reused_read_carries_the_settings_of_now(
    qgis_app, project, make_memory_layer
) -> None:
    """A colour changed after the last read used to never reach the page."""
    dialog = _dialog(project, make_memory_layer)
    dialog._cached_export, dialog._cached_report = dialog._read_current_project()
    dialog._cached_signature = dialog.state.data_snapshot()
    dialog.state.widget_background = "#40e0d0"

    seen = []
    assert dialog._ensure_export(lambda export, _report: seen.append(export), "x")
    background = seen[0].settings.widget_background
    assert background is not None
    assert background.as_css() == "#40e0d0"
    dialog.close()


def test_saving_the_project_keeps_settings_made_with_the_dialog_open(
    qgis_app, project, make_memory_layer, tmp_path
) -> None:
    from nika_onlymap_exporter.core.settings import load_state

    dialog = _dialog(project, make_memory_layer)
    dialog.state.map_name = "Saved while open"
    path = tmp_path / "p.qgs"
    assert project.write(str(path))
    dialog._shut_down = True  # nothing more from the dialog
    project.clear()
    assert project.read(str(path))
    assert load_state(project).map_name == "Saved while open"
    dialog.close()


def test_the_preview_stops_once_no_tab_watches_it(
    qgis_app, project, make_memory_layer
) -> None:
    from nika_onlymap_exporter.ui import main_dialog

    class Unwatched:
        stopped = False

        def idle_seconds(self) -> float:
            return main_dialog.LIVE_IDLE_STOP_SECONDS + 1

        def stop(self) -> None:
            Unwatched.stopped = True

    dialog = _dialog(project, make_memory_layer)
    dialog._server = Unwatched()
    dialog._poll_for_changes()
    assert dialog._server is None
    assert Unwatched.stopped
    assert "tab was closed" in dialog.status_label.text()
    dialog.close()
