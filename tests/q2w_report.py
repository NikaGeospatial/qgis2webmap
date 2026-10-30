"""Pytest plugin for `scripts/run_tests.py`: live progress and a timing record.

Enabled with `-p q2w_report` (the runner adds `tests/` to PYTHONPATH). It changes
nothing about which tests run or how they pass; it only reports.

- Prints one `Q2W|...` line per finished test file and one per failure, flushed
  at once, so the runner can show progress and failures while the run is going.
  A failure's full traceback is printed the moment it happens (not only in the
  summary at the end), so a later crash cannot swallow it.
- Writes `Q2W_REPORT_JSON` after every file and at the end: setup / call /
  teardown seconds per file, outcome counts, skip reasons, warnings, peak and
  per-file RSS. `finished: false` in the file means the process never reached
  the end of the session (killed, crashed), so the numbers are partial.
- `Q2W_RSS_ABORT_MB=N` ends the run cleanly when this process passes N MB.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from collections import Counter

import pytest

_REPORT_PATH = os.environ.get("Q2W_REPORT_JSON", "")
_ABORT_MB = float(os.environ.get("Q2W_RSS_ABORT_MB", "0") or 0)
_WORKER = os.environ.get("Q2W_WORKER", "")

_state: dict = {
    "worker": _WORKER,
    "started_wall": time.time(),
    "finished": False,
    "exitstatus": None,
    "files_total": 0,
    "tests_total": 0,
    "outcomes": Counter(),
    "skip_reasons": Counter(),
    "warnings": 0,
    "files": {},
    "peak_rss_mb": 0.0,
}
_current_file: list[str | None] = [None]


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except OSError:
        return 0.0


def _peak_rss_mb() -> float:
    # ru_maxrss is kilobytes on Linux, bytes on macOS.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1048576 if sys.platform == "darwin" else 1024)


def _emit(line: str) -> None:
    sys.__stderr__.write(f"Q2W|{line}\n")
    sys.__stderr__.flush()


def _dump(finished: bool = False) -> None:
    if not _REPORT_PATH:
        return
    _state["finished"] = finished
    _state["peak_rss_mb"] = round(_peak_rss_mb(), 1)
    _state["elapsed_s"] = round(time.time() - _state["started_wall"], 1)
    data = dict(_state)
    data["outcomes"] = dict(_state["outcomes"])
    data["skip_reasons"] = dict(_state["skip_reasons"].most_common(20))
    tmp = _REPORT_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=1)
    os.replace(tmp, _REPORT_PATH)


def _file_record(nodeid: str) -> dict:
    path = nodeid.split("::")[0]
    return _state["files"].setdefault(
        path,
        {
            "setup": 0.0,
            "call": 0.0,
            "teardown": 0.0,
            "tests": 0,
            "failed": 0,
            "skipped": 0,
            "rss_end_mb": 0.0,
        },
    )


def pytest_collection_finish(session) -> None:
    files = {item.nodeid.split("::")[0] for item in session.items}
    _state["files_total"] = len(files)
    _state["tests_total"] = len(session.items)
    _emit(f"collected|{len(session.items)}|{len(files)}")
    _dump()


def pytest_runtest_logreport(report) -> None:
    record = _file_record(report.nodeid)
    record[report.when] += report.duration
    counted = report.when == "call" or (report.when == "setup" and not report.passed)
    if counted:
        record["tests"] += 1
        outcome = (
            "skipped" if report.skipped else "failed" if report.failed else "passed"
        )
        _state["outcomes"][outcome] += 1
        record["failed"] += outcome == "failed"
        record["skipped"] += outcome == "skipped"
        if report.skipped and isinstance(report.longrepr, tuple):
            _state["skip_reasons"][str(report.longrepr[2])[:160]] += 1
    elif report.when == "teardown" and report.failed:
        _state["outcomes"]["error"] += 1
        record["failed"] += 1
    if report.failed:
        headline = (report.longreprtext or "").strip().splitlines()[-1:] or [""]
        _emit(f"FAIL|{report.nodeid}|{report.when}|{headline[0][:200]}")
        sys.__stderr__.write(
            f"\n=== {report.nodeid} [{report.when}] FAILED ===\n{report.longreprtext}\n"
        )
        sys.__stderr__.flush()


def pytest_warning_recorded(warning_message, when, nodeid, location) -> None:
    _state["warnings"] += 1


def pytest_runtest_logfinish(nodeid, location) -> None:
    path = nodeid.split("::")[0]
    rss = _rss_mb()
    _file_record(nodeid)["rss_end_mb"] = round(rss, 1)
    if _current_file[0] is not None and _current_file[0] != path:
        _announce_file_done(_current_file[0])
    _current_file[0] = path
    if _ABORT_MB and rss > _ABORT_MB:
        _dump()
        pytest.exit(
            f"RSS {rss:.0f} MB passed the {_ABORT_MB:.0f} MB limit after {nodeid}",
            returncode=3,
        )


def _announce_file_done(path: str) -> None:
    record = _state["files"][path]
    seconds = record["setup"] + record["call"] + record["teardown"]
    _emit(
        f"file|{path}|{record['tests']}|{seconds:.1f}|{record['failed']}"
        f"|{record['rss_end_mb']:.0f}"
    )
    _dump()


def pytest_sessionfinish(session, exitstatus) -> None:
    if _current_file[0] is not None:
        _announce_file_done(_current_file[0])
        _current_file[0] = None
    _state["exitstatus"] = int(exitstatus)
    _dump(finished=True)
    _emit(f"done|{int(exitstatus)}|{round(_peak_rss_mb())}")
