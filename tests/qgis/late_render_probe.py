"""Subprocess probe: a render or job reports after the window that asked for
it is gone.

A signal handler that calls into a deleted widget raises inside a Qt slot, and
PyQt6 turns that into a process abort, so the scenarios cannot run inside the
pytest process: `test_late_render_results.py` starts this script and reads its
exit code and last output line. Run by hand as

    python late_render_probe.py <scratch-dir> <scenario>

Scenarios: picker, preview_dialog, dialog_job, controller_after_shutdown.

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


def job_alive(job) -> bool:
    from qgis.PyQt import sip

    return not sip.isdeleted(job) and job.isRunning()


def settle(app) -> None:
    from qgis.PyQt.QtCore import QCoreApplication, QEvent

    for _ in range(4):
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    scratch = Path(sys.argv[1])
    scenario = sys.argv[2]

    from hermetic import start_qgis

    app = start_qgis(scratch)

    from qgis.PyQt import sip
    from qgis.PyQt.QtCore import QSettings, QSize
    from qgis.PyQt.QtGui import QColor, QImage

    outcome: dict[str, object] = {"scenario": scenario}
    image = QImage(40, 30, QImage.Format.Format_ARGB32)
    image.fill(QColor("#336699"))

    if scenario in ("picker", "preview_dialog"):
        from test_template_picker import _SlowRenderer

        from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker
        from nika_onlymap_exporter.ui.cartograph.template_preview_dialog import (
            TemplatePreviewDialog,
        )

        settings = QSettings(str(scratch / "picker.ini"), QSettings.Format.IniFormat)
        renderer = _SlowRenderer(delay_ms=None)
        picker = TemplatePicker(renderer, qsettings=settings)
        picker.resize(QSize(700, 480))
        picker.show()
        for _ in range(5):
            app.processEvents()
        if scenario == "picker":
            widget = picker
            deadline = time.monotonic() + 10
            while not renderer.pending and time.monotonic() < deadline:
                app.processEvents()
            pending = renderer.pending[0]
        else:
            holder = _SlowRenderer(delay_ms=None)
            widget = TemplatePreviewDialog(
                "Daylight", "", "A4", lambda width: holder(1, "A4", "", width), picker
            )
            widget.show()
            widget.start()
            deadline = time.monotonic() + 10
            while not holder.pending and time.monotonic() < deadline:
                app.processEvents()
            pending = holder.pending[0]
        outcome["render_started"] = True
        widget.reject()
        widget.deleteLater()
        if scenario == "preview_dialog":
            picker.reject()
            picker.deleteLater()
        settle(app)
        outcome["widget_deleted"] = sip.isdeleted(widget)
        # The render finishes after its window is gone.
        pending.finished.emit(image)
        pending.finished.emit(None)
        settle(app)
    elif scenario in ("dialog_job", "controller_after_shutdown"):
        from close_lifecycle_fixtures import FakeIface

        import nika_onlymap_exporter

        plugin = nika_onlymap_exporter.classFactory(FakeIface())
        plugin.initGui()
        if scenario == "dialog_job":
            plugin.run()
            dialog = plugin.dialog
            results: list[object] = []
            release = threading.Event()

            def held(_progress: object) -> str:
                release.wait(20)
                return "done"

            assert dialog._start_job(held, results.append, "Held"), "no job started"
            job = dialog._job
            dialog.close()
            settle(app)
            outcome["dialog_deleted"] = sip.isdeleted(dialog)
            # The job reports to a dialog that is gone.
            job.succeeded.emit("late result")
            job.failed.emit("late failure", "details")
            settle(app)
            outcome["callbacks"] = results
            release.set()
            end = time.monotonic() + 5
            while time.monotonic() < end and job_alive(job):
                app.processEvents()
                time.sleep(0.01)
        else:
            plugin.run_cartograph("")
            dialog = plugin.dialog
            controller = dialog._cartograph
            dialog.close()
            settle(app)
            controller._on_static_frame_drawn()
            controller._on_build_job_succeeded(None)
            controller._on_build_job_failed("late failure", "details")
            settle(app)
        plugin.unload()
    else:
        print(f"unknown scenario {scenario}")
        return 2

    spin_end = time.monotonic() + 0.3
    while time.monotonic() < spin_end:
        app.processEvents()
    print(json.dumps(outcome))
    sys.stdout.flush()
    from hermetic import stop_qgis

    stop_qgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
