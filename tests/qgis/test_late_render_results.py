"""A render or job that reports after its window has gone must be harmless.

The picker, the larger-preview window, the main dialog and the Cartograph
controller all listen to objects that outlive them (a background render, a
worker job). A handler that reaches into the deleted window kills QGIS, so each
case runs in a subprocess (`late_render_probe.py`) and must exit cleanly.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

PROBE = Path(__file__).with_name("late_render_probe.py")


@pytest.mark.parametrize(
    "scenario",
    ["picker", "preview_dialog", "dialog_job", "controller_after_shutdown"],
)
def test_a_late_result_after_the_window_is_gone_is_dropped(
    tmp_path: Path, scenario: str
) -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    scratch = tmp_path / scenario
    scratch.mkdir()
    done = subprocess.run(
        [sys.executable, str(PROBE), str(scratch), scenario],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-1500:]
    outcome = json.loads(done.stdout.strip().splitlines()[-1])
    assert outcome["scenario"] == scenario
    assert not outcome.get("callbacks"), "a result reached a window that was gone"
