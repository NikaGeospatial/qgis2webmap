"""Subprocess probe: close the window while a worker is still running.

A QThread destroyed while running aborts the whole process (`qFatal`), so the
scenario cannot run inside the pytest process: the test starts this script and
reads its exit code and last output line. `test_close_lifecycle.py` is the
caller; run by hand as

    python close_lifecycle_probe.py <scratch-dir> <scenario>

Scenarios name how the window is closed while an 6 s worker is running:
close_button, escape, window_close, deferred_delete_twice, language, unload,
cartograph_unload.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

SLOW_S = 6.0
WAIT_MS = 200


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
        FakeIface,
        LabelExport,
        add_survey_layout,
    )
    from qgis.core import QgsProject
    from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
    from qgis.PyQt.QtTest import QTest
    from waiting import wait_until

    import nika_onlymap_exporter
    import nika_onlymap_exporter.ui.cartograph.controller as controller_module
    import nika_onlymap_exporter.ui.main_dialog as main_dialog_module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(main_dialog_module, "SHUTDOWN_WAIT_MS", WAIT_MS)
    monkeypatch.setattr(controller_module, "SHUTDOWN_WAIT_MS", WAIT_MS)
    for name in ("UNLOAD_SAVE_WAIT_MS",):
        if hasattr(main_dialog_module, name):
            monkeypatch.setattr(main_dialog_module, name, 300)

    plugin = nika_onlymap_exporter.classFactory(FakeIface())
    plugin.initGui()
    callbacks: list[str] = []
    worker_done = threading.Event()

    if scenario == "cartograph_unload":
        project = QgsProject.instance()
        export = LabelExport()
        export.install(monkeypatch)
        export.delay_s = SLOW_S
        worker_done = export.worker_done
        layout, _label = add_survey_layout(project)
        target = scratch / "survey.html"
        plugin.run_cartograph(layout.name())
        dialog = plugin.dialog
        cartograph = dialog._cartograph
        cartograph.show_layout_named(layout.name())
        cartograph.file_edit.setText(str(target))
        cartograph._on_file_edited(str(target))
        deadline = time.monotonic() + 10
        while not export.worker_started.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert export.worker_started.is_set(), "the slow build never started"
    else:
        plugin.run()
        dialog = plugin.dialog
        working = threading.Event()

        def slow_work(_progress):
            working.set()
            try:
                time.sleep(SLOW_S)
                return "late result"
            finally:
                worker_done.set()

        started = dialog._start_job(
            slow_work, lambda result: callbacks.append(result), "Slow"
        )
        assert started, "the job did not start"
        wait_until(app, working.is_set, "the slow job to be running")

    if scenario == "close_button":
        QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    elif scenario == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    elif scenario == "window_close":
        dialog.close()
    elif scenario == "deferred_delete_twice":
        dialog.close()
        dialog.close()
        dialog._shutdown()
    elif scenario == "language":
        plugin._reopen_dialog()
    elif scenario in ("unload", "cartograph_unload"):
        plugin.unload()
    else:
        print(f"unknown scenario {scenario}")
        return 2

    # What the plugin schedules on `finished`, delivered now: the moment the
    # old build destroyed the running thread with the dialog.
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        app.processEvents()

    if scenario == "language":
        wait_until(
            app,
            lambda: plugin.dialog is not None and plugin.dialog is not dialog,
            "the dialog to be rebuilt",
        )
        assert plugin.dialog is not None, "the dialog was not rebuilt"
        plugin.dialog.close()

    deadline = time.monotonic() + SLOW_S + 8
    while not worker_done.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    spin(app, 0.6)
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        app.processEvents()

    from nika_onlymap_exporter.ui import background_job

    running = getattr(background_job, "running_jobs", lambda: None)()
    plugin.unload()
    print(
        json.dumps(
            {
                "callbacks": callbacks,
                "worker_done": worker_done.is_set(),
                "running_jobs": running,
            }
        )
    )
    sys.stdout.flush()
    from hermetic import stop_qgis

    stop_qgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
