"""The project is cleared, replaced or loses the layout while the Cartograph
dialog is open - File > New, opening another project, deleting the layout.

QGIS destroys the layouts as it goes; a dialog still watching them used to
crash QGIS from inside that destruction, and a save it was holding its close
for could still land on the old destination. Each scenario runs in a
subprocess (`project_clear_probe.py`) because the failure it guards against is a
segfault, and nothing here relies on the suite's fixture that closes leftover
dialogs before the project clears: the probe clears the project itself, with
the dialog open.

What is asserted is the outcome: the process survives, the cartograph file is
exactly as it was (nothing written after its layout went), and the window is
closed or still usable as each case calls for.

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

PROBE = Path(__file__).with_name("project_clear_probe.py")


def _run(scenario: str, tmp_path: Path) -> tuple[int, dict[str, object]]:
    scratch = tmp_path / scenario
    scratch.mkdir()
    done = subprocess.run(
        [sys.executable, str(PROBE), str(scratch), scenario],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "TMPDIR": str(scratch)},
        stdin=subprocess.DEVNULL,
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    return done.returncode, json.loads(lines[-1]) if lines else {}


# The project goes: the window it was opened for has no project left to be about.
PROJECT_GONE = ["clear_idle", "clear_held_close", "clear_during_build", "read_other"]
# Only the layout goes: the project, and so the window, carry on.
LAYOUT_GONE = ["remove_layout", "remove_layout_with_other"]


@pytest.mark.parametrize("scenario", PROJECT_GONE)
def test_the_window_survives_and_closes_when_the_project_goes(scenario, tmp_path):
    code, result = _run(scenario, tmp_path)
    assert code == 0, f"the process died ({code}) in {scenario}"
    assert result["file_unchanged"], result["file_text"]
    assert result["dialog_visible"] is False
    if scenario in ("clear_held_close", "clear_during_build"):
        # Guards the scenario itself: a probe that never got a build running
        # would pass the outcome checks for no reason.
        assert result["build_running_before"]
    if scenario == "clear_held_close":
        assert result["close_held_before"]


@pytest.mark.parametrize("scenario", LAYOUT_GONE)
def test_the_window_stays_usable_when_only_the_layout_goes(scenario, tmp_path):
    code, result = _run(scenario, tmp_path)
    assert code == 0, f"the process died ({code}) in {scenario}"
    assert result["file_unchanged"], result["file_text"]
    assert result["dialog_usable"]


def test_a_held_close_whose_layout_is_deleted_ends_in_a_close_without_a_write(
    tmp_path,
):
    code, result = _run("remove_layout_held_close", tmp_path)
    assert code == 0
    assert result["close_held_before"] and result["build_running_before"]
    assert result["file_unchanged"], result["file_text"]
    assert result["dialog_visible"] is False
