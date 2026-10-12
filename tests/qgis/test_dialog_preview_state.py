"""Settings reaching the preview.

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
