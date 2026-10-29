"""Subprocess probe: the project goes away under an open Cartograph dialog.

A segfault in the plugin's layout watching kills the whole process, so the
scenarios cannot run inside pytest: `test_project_clear_under_dialog.py` starts
this script and reads its exit code and last output line. By hand:

    python project_clear_probe.py <scratch-dir> <scenario>

Scenarios: clear_idle, clear_held_close, clear_during_build, read_other,
remove_layout, remove_layout_held_close, remove_layout_with_other. Nothing here
uses the suite's `close_open_dialogs_before_the_project_clears` fixture: the
project is cleared with the dialog still open, as File > New does.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]


def spin(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    scratch = Path(sys.argv[1])
    scenario = sys.argv[2]

    from hermetic import start_qgis

    app = start_qgis(scratch)

    import pytest
    from close_lifecycle_fixtures import (
        LabelExport,
        add_survey_layout,
        edit_label,
        open_saved_cartograph,
    )
    from qgis.core import QgsProject, QgsVectorLayer
    from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
    from qgis.PyQt.QtTest import QTest

    monkeypatch = pytest.MonkeyPatch()
    export = LabelExport()
    export.install(monkeypatch)
    project = QgsProject.instance()
    project.clear()
    other = scratch / "other.qgz"
    assert project.write(str(other))
    target = scratch / "survey.html"

    def wait_for(_app, predicate, timeout=15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            app.processEvents()
            time.sleep(0.02)
        return predicate()

    # A layer in the project, so the layers panel and the frames have something
    # of the project's to watch as well.
    sites = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string", "sites", "memory")
    project.addMapLayer(sites)
    dialog, cartograph, layout, label = open_saved_cartograph(
        app, project, target, wait_for
    )
    if scenario == "remove_layout_with_other":
        add_survey_layout(project, name="Other", text="Other")
    before = (target.stat().st_mtime_ns, target.read_text())
    close_held = building = False

    if scenario in ("clear_held_close", "remove_layout_held_close"):
        export.hold_workers()
        edit_label(layout, label, "New")
        QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
        spin(app, 0.2)
        close_held = dialog._close_result is not None
        building = cartograph._job is not None
    elif scenario == "clear_during_build":
        export.hold_workers()
        edit_label(layout, label, "New")
        spin(app, 1.0)
        building = cartograph._job is not None
    else:
        spin(app, 0.2)

    if scenario in ("clear_idle", "clear_held_close", "clear_during_build"):
        project.clear()
    elif scenario == "read_other":
        assert project.read(str(other))
    elif scenario in (
        "remove_layout",
        "remove_layout_held_close",
        "remove_layout_with_other",
    ):
        project.layoutManager().removeLayout(layout)
    else:
        print(f"unknown scenario {scenario}")
        return 2

    export.release()
    spin(app, 1.5)
    after = (target.stat().st_mtime_ns, target.read_text())
    from qgis.PyQt import sip

    deleted = sip.isdeleted(dialog)
    print(
        json.dumps(
            {
                "file_unchanged": before == after,
                "file_text": after[1],
                "close_held_before": close_held,
                "build_running_before": building,
                "dialog_visible": False if deleted else dialog.isVisible(),
                "dialog_usable": (
                    not deleted
                    and dialog.isVisible()
                    and dialog.close_button.isEnabled()
                ),
                "layout_shown": (
                    not deleted and cartograph._current_layout is not None
                ),
            }
        )
    )
    sys.stdout.flush()
    if not deleted:
        dialog.close_without_saving()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        app.processEvents()
    from hermetic import stop_qgis

    stop_qgis()
    print("clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
