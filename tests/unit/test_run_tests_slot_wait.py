"""A stage's timeout runs from when it starts, not from when it queues.

With `Q2W_SLOT_CMD` (a wrapper that takes one of the machine-wide test slots)
the runner launches a heavy stage straight away and the wrapper holds it until a
slot is free. Timing the stage from the launch charged that queue to the stage,
so on a busy machine a stage timed out without having run a single test - and
every stage that depended on it reported NOT RUN.

These drive the real scheduler loop and the real `_exec` trampoline through a
stand-in wrapper that waits before handing over, so the marker the runner reads
is the one a real slot wrapper would produce.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_tests_under_test", REPO / "scripts" / "run_tests.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


rt = _load()


def _slot_wrapper(wait_s: float) -> list[str]:
    """A stand-in for slot.sh: hold the command for `wait_s`, then run it."""
    return ["sh", "-c", f'sleep {wait_s}; exec "$@"', "slot"]


def _run(tmp_path, monkeypatch, *, slot_cmd, timeout_s, command):
    # Memory is the machine's, not the code under test; never queue on it.
    monkeypatch.setattr(rt, "mem_available_mb", lambda: 1_000_000)
    cfg = rt.Config(
        state_dir=tmp_path / "state",
        python42=sys.executable,
        env42_sh=None,
        browser_python=None,
        docker_image="unused",
        slot_cmd=slot_cmd,
        runtime_dir=None,
        max_workers=1,
        docker_memory_mb=0,
    )
    stage = rt.Stage(
        name="heavy-stage",
        kind="heavy",
        command=lambda: command,
        env=lambda: dict(os.environ),
        cwd=tmp_path,
        log=tmp_path / "logs" / "heavy-stage.log",
        timeout_s=timeout_s,
    )
    rt.Scheduler(cfg, tmp_path, [stage]).run()
    return stage


QUICK = [sys.executable, "-c", "pass"]


class TestTheQueueIsNotChargedToTheStage:
    def test_a_stage_that_waits_longer_than_its_timeout_still_runs(
        self, tmp_path, monkeypatch
    ):
        stage = _run(
            tmp_path,
            monkeypatch,
            slot_cmd=_slot_wrapper(3),
            timeout_s=1.0,
            command=QUICK,
        )
        assert stage.status == rt.PASSED, stage.reason
        assert stage.wait_s >= 2.5
        assert stage.run_s < 2.5

    def test_once_started_the_stage_timeout_still_applies(self, tmp_path, monkeypatch):
        stage = _run(
            tmp_path,
            monkeypatch,
            slot_cmd=_slot_wrapper(1),
            timeout_s=1.0,
            command=[sys.executable, "-c", "import time; time.sleep(60)"],
        )
        assert stage.status == rt.FAILED
        assert stage.reason == "timeout"
        assert stage.started_at is not None

    def test_a_queue_that_never_moves_still_ends(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt, "SLOT_WAIT_LIMIT_S", 1.5)
        stage = _run(
            tmp_path,
            monkeypatch,
            slot_cmd=_slot_wrapper(60),
            timeout_s=3600.0,
            command=QUICK,
        )
        assert stage.status == rt.FAILED
        assert "no test slot" in stage.reason
        # It never ran: the summary must not report the wait as run time.
        assert stage.started_at is None
        assert stage.run_s == 0.0
        assert stage.wait_s >= 1.0


class TestWithoutASlotWrapperNothingChanges:
    """CI sets no `Q2W_SLOT_CMD`: the stage starts at launch, as before."""

    def test_the_timeout_runs_from_the_launch(self, tmp_path, monkeypatch):
        stage = _run(
            tmp_path,
            monkeypatch,
            slot_cmd=[],
            timeout_s=1.0,
            command=[sys.executable, "-c", "import time; time.sleep(60)"],
        )
        assert stage.status == rt.FAILED
        assert stage.reason == "timeout"
        assert stage.started_at == pytest.approx(stage.launched_at)
