"""Closing the window while a worker is still running never destroys the worker.

A QThread destroyed while its thread runs aborts the whole interpreter, so the
cases that used to do that run in a subprocess (`close_lifecycle_probe.py`) and
are judged by its exit code and what it reports. Closing by the Close button,
Escape, the window manager, a language rebuild and a plugin unload all go
through it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from close_lifecycle_fixtures import FakeIface

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtCore import QEvent  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

PROBE = Path(__file__).with_name("close_lifecycle_probe.py")


def _wait_for(qgis_app, predicate, timeout=15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        qgis_app.processEvents()
        time.sleep(0.02)
    return predicate()


def _probe(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    scratch = tmp_path / f"probe-{scenario}"
    scratch.mkdir()
    return subprocess.run(
        [sys.executable, str(PROBE), str(scratch), scenario],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class TestAWorkerStillRunningIsNeverDestroyed:
    @pytest.mark.parametrize(
        "scenario",
        [
            "close_button",
            "escape",
            "window_close",
            "deferred_delete_twice",
            "language",
            "unload",
            "cartograph_unload",
        ],
    )
    def test_the_process_survives_and_no_late_result_arrives(
        self, tmp_path, scenario
    ) -> None:
        done = _probe(tmp_path, scenario)
        assert done.returncode == 0, (
            f"{scenario}: exit {done.returncode}\n{done.stdout[-800:]}\n"
            f"{done.stderr[-1500:]}"
        )
        import json

        result = json.loads(done.stdout.strip().splitlines()[-1])
        assert result["worker_done"] is True
        assert result["callbacks"] == []
        assert result["running_jobs"] == 0
        assert "Destroyed while thread is still running" not in done.stderr


class TestShutdownIsIdempotent:
    def test_closing_twice_and_then_unloading_is_harmless(
        self, qgis_app, project, tmp_path
    ) -> None:
        import nika_onlymap_exporter

        plugin = nika_onlymap_exporter.classFactory(FakeIface())
        plugin.initGui()
        plugin.run()
        dialog = plugin.dialog
        dialog.close()
        dialog.close()
        dialog._shutdown()
        qgis_app.processEvents()
        plugin.unload()
        plugin.unload()
        assert plugin.dialog is None

    def test_a_result_arriving_after_close_is_dropped(
        self, qgis_app, project, monkeypatch
    ) -> None:
        import nika_onlymap_exporter.ui.main_dialog as main_dialog_module
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        monkeypatch.setattr(main_dialog_module, "SHUTDOWN_WAIT_MS", 0)
        dialog = MainDialog(FakeIface(), None)
        arrived: list[str] = []

        def work(_progress):
            time.sleep(0.4)
            return "late"

        assert dialog._start_job(work, arrived.append, "Slow")
        dialog.close()
        dialog.deleteLater()
        QApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        _wait_for(qgis_app, lambda: False, timeout=1.5)
        assert arrived == []
