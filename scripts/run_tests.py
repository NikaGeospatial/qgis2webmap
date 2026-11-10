#!/usr/bin/env python3
"""Run the QGIS2WebMap test profiles: patch, release, full.

    python3 scripts/run_tests.py patch   [--base REF] [--worktree] [--dry-run]
    python3 scripts/run_tests.py release [--commit REF]
    python3 scripts/run_tests.py full    [--commit REF]
    python3 scripts/run_tests.py stop | update-durations RUN_DIR | groups-check

Profiles (details and environment variables: CONTRIBUTING.md, "Running the tests")
- patch:   the component groups of what changed (tests/test_groups.json), the
           data-loss regressions of any save/close path touched, the matching
           3.44 compatibility checks, the unit tier. Unknown paths -> the whole
           primary suite. Prints every selected test and why.
- release: whole QGIS 4.2 suite (unit + qgis + fixtures, once), the maintained
           focused QGIS 3.44 suite, the whole browser tier (Chromium, Firefox,
           WebKit), translations, lint,
           package + verify, packaged-ZIP smoke on 4.2 and 3.44.
- full:    the same, but the whole QGIS 3.44 suite replaces the focused one
           (it contains it, so the subset is not run twice).

Every run tests an immutable snapshot (`git archive` of one commit), records the
commit and the ZIP's sha256, runs the primary suite as balanced process shards
(each with its own HOME, profile, QSettings, TMPDIR and output dirs), and limits
concurrent heavy workers by count and by available memory.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

REPO = Path(__file__).resolve().parent.parent

if TYPE_CHECKING:
    import test_selection

_spec = importlib.util.spec_from_file_location(
    "q2w_test_selection", REPO / "scripts" / "test_selection.py"
)
assert _spec and _spec.loader
selection = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = selection
_spec.loader.exec_module(selection)

RUNNER_VERSION = 1
HEAVY_WORKERS_DEFAULT = 2
#: Estimated resident memory of one heavy worker, MB (measured; see CONTRIBUTING.md).
MEMORY_ESTIMATE_MB = {
    "shard42": 1300,
    "shard344": 1500,
    "browser": 1600,
    "smoke": 900,
    "fixtures": 1000,
}
MEMORY_RESERVE_MB = 600
FIRST_WORKER_MIN_FREE_MB = 900
#: How long a stage may wait on `Q2W_SLOT_CMD` for a machine-wide slot before it
#: is given up on. Its own `timeout_s` only runs once it has a slot, so on a busy
#: machine the queue is not charged to the stage; this cap is what still ends a
#: run whose queue never moves.
SLOT_WAIT_LIMIT_S = 4 * 3600.0
SMOKE_SUBSET = "classic,map-only,poster,locator,dashboard,dark"
# The plugin zip in dist/, not the language packs zip built beside it
# (qgis2webmap-language-packs-<version>.zip): the digit after the dash.
PLUGIN_ZIP_GLOB = "qgis2webmap-[0-9]*.zip"


def qt6_checker_image(root: Path) -> str:
    """The Qt6 checker image, pinned once, in the workflow that runs it."""
    workflow = (root / ".github" / "workflows" / "checks.yml").read_text(
        encoding="utf-8"
    )
    found = re.search(r"ghcr\.io/qgis/pyqgis4-checker@sha256:[0-9a-f]{64}", workflow)
    if found is None:
        raise SystemExit("checks.yml names no pinned pyqgis4-checker image")
    return found.group(0)


BUILDERS = (
    ("cartograph", "CARTOGRAPH_FIXTURES_DIR", "expected.json"),
    ("ground_size", "GROUND_SIZE_FIXTURES_DIR", "expected.json"),
    ("legend_fit", "LEGEND_FIT_FIXTURES_DIR", "qgis.json"),
    ("label", "LABEL_FIXTURES_DIR", "expected.json"),
    ("rule_based", "RULE_BASED_FIXTURES_DIR", "expected.json"),
    ("measure", "MEASURE_FIXTURES_DIR", "maps.json"),
)

#: Status words used in the summary. Only PASSED counts as a pass.
PASSED, FAILED, INTERRUPTED, SKIPPED, NOT_RUN = (
    "PASSED",
    "FAILED",
    "INTERRUPTED",
    "SKIPPED",
    "NOT RUN",
)


def now_hms() -> str:
    return time.strftime("%H:%M:%S")


def say(message: str) -> None:
    print(f"[{now_hms()}] {message}", flush=True)


# ---- configuration from the environment ------------------------------------------


@dataclass
class Config:
    state_dir: Path
    python42: str
    env42_sh: str | None
    browser_python: str | None
    docker_image: str
    slot_cmd: list[str]
    runtime_dir: str | None
    max_workers: int
    docker_memory_mb: int
    verbose: bool = False
    #: Where Firefox finds an EGL library, if not on the system path (NixOS).
    firefox_library_path: str | None = None

    @classmethod
    def from_env(cls, args: argparse.Namespace) -> Config:
        state = Path(
            getattr(args, "state_dir", None)
            or os.environ.get("Q2W_TEST_HOME")
            or Path.home() / ".cache" / "q2w-tests"
        )
        return cls(
            state_dir=state,
            python42=os.environ.get("Q2W_QGIS42_PYTHON", sys.executable),
            env42_sh=os.environ.get("Q2W_QGIS42_ENV_SH"),
            browser_python=os.environ.get("Q2W_BROWSER_PYTHON"),
            firefox_library_path=os.environ.get("Q2W_FIREFOX_LIBRARY_PATH"),
            docker_image=os.environ.get("Q2W_DOCKER_IMAGE", "q2w-qgis344:local"),
            slot_cmd=shlex.split(os.environ.get("Q2W_SLOT_CMD", "")),
            runtime_dir=os.environ.get("ONLYMAP_RUNTIME_DIR"),
            max_workers=1
            if getattr(args, "serial", False)
            else getattr(args, "max_workers", HEAVY_WORKERS_DEFAULT),
            docker_memory_mb=int(os.environ.get("Q2W_DOCKER_MEMORY_MB", "3000")),
            verbose=getattr(args, "verbose", False),
        )


def sourced_environment(script: str, cwd: Path) -> dict[str, str]:
    """The environment a shell script exports, evaluated with `cwd` as its PWD."""
    out = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(script)} >/dev/null 2>&1; env -0"],
        cwd=cwd,
        capture_output=True,
        check=True,
    ).stdout
    return dict(
        item.decode().split("=", 1) for item in out.split(b"\0") if b"=" in item
    )


# ---- git: commit, snapshot ---------------------------------------------------


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@dataclass
class Candidate:
    commit: str
    tree: str
    plugin_tree: str
    dirty_files: int
    worktree: bool
    snapshot: Path
    snapshot_digest: str = ""


def tree_digest(root: Path) -> str:
    """sha256 over every file's relative path and bytes: proves a snapshot unchanged."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def make_snapshot(cfg: Config, run_id: str, ref: str, worktree: bool) -> Candidate:
    commit = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    dirty = len(git("status", "--porcelain").splitlines())
    target = cfg.state_dir / "snapshots" / run_id
    target.mkdir(parents=True)
    (target / ".q2w-snapshot").write_text(f"{commit}\n")
    root = target / "src"
    root.mkdir()
    if worktree:
        names = git("ls-files", "-z", "-c", "-o", "--exclude-standard").split("\0")
        for name in filter(None, names):
            source = REPO / name
            if source.is_file():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, root / name)
    else:
        archive = subprocess.Popen(
            ["git", "-C", str(REPO), "archive", "--format=tar", commit],
            stdout=subprocess.PIPE,
        )
        assert archive.stdout is not None
        with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
            try:
                tar.extractall(root, filter="data")
            except TypeError:  # Python without extraction filters
                tar.extractall(root)
        if archive.wait() != 0:
            raise SystemExit(f"git archive {commit} failed")
    for path in root.rglob("*"):  # read-only: a test that writes into it fails loudly
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    candidate = Candidate(
        commit=commit,
        tree=git("rev-parse", f"{commit}^{{tree}}"),
        plugin_tree=git("rev-parse", f"{commit}:{selection.PLUGIN}"),
        dirty_files=dirty,
        worktree=worktree,
        snapshot=root,
    )
    candidate.snapshot_digest = tree_digest(root)
    return candidate


def remove_snapshot(candidate: Candidate) -> None:
    """Delete a snapshot this runner made (identified by its marker file only)."""
    parent = candidate.snapshot.parent
    if not (parent / ".q2w-snapshot").exists():
        return
    for path in sorted(parent.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
                path.rmdir()
            else:
                path.parent.chmod(0o755)
                path.unlink()
    with contextlib.suppress(OSError):
        (parent / ".q2w-snapshot").unlink()
        parent.rmdir()


# ---- stages ------------------------------------------------------------------


@dataclass
class Stage:
    name: str
    kind: str  # light | heavy
    command: Callable[[], list[str]]
    env: Callable[[], dict[str, str]]
    cwd: Path
    log: Path
    resource: str = ""  # MEMORY_ESTIMATE_MB key for heavy stages
    depends: list[str] = field(default_factory=list)
    required: bool = True
    pytest_report: Path | None = None
    container: str | None = None
    timeout_s: float = 3600.0
    estimate_s: float = 0.0
    description: str = ""
    targets: list[str] = field(default_factory=list)
    # outcome
    status: str = "pending"
    reason: str = ""
    returncode: int | None = None
    ready_at: float | None = None
    # Handed to the process; with a slot wrapper, `started_at` is later, when the
    # wrapper let the command run, and stays None if it never did.
    launched_at: float | None = None
    started_at: float | None = None
    ended_at: float | None = None
    waited_slot_s: float = 0.0
    waited_memory_s: float = 0.0
    peak_rss_mb: float = 0.0
    process: subprocess.Popen[str] | None = None
    slot_fd: int | None = None
    counters: dict[str, int] = field(default_factory=lambda: {"files": 0, "failed": 0})
    files_total: int = 0
    tests_total: int = 0
    last_progress: float = 0.0
    last_rss_mb: int = 0
    killed_for: str = ""
    result: dict = field(default_factory=dict)

    @property
    def wait_s(self) -> float:
        # A stage stopped while still queued waited until it was stopped.
        start = self.started_at if self.started_at is not None else self.ended_at
        if self.ready_at is None or start is None:
            return 0.0
        return max(0.0, start - self.ready_at)

    @property
    def run_s(self) -> float:
        if self.started_at is None or self.ended_at is None:
            return 0.0
        return max(0.0, self.ended_at - self.started_at)


class Slots:
    """Machine-wide heavy-worker slots (file locks), shared by every runner instance."""

    def __init__(self, directory: Path, count: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.paths = [directory / f"slot-{i}.lock" for i in range(count)]

    def try_acquire(self) -> int | None:
        for path in self.paths:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            return fd
        return None

    @staticmethod
    def release(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def mem_available_mb() -> int:
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return 0


def group_rss_mb() -> dict[int, float]:
    """Resident MB per process group id, from one `ps` call."""
    out = subprocess.run(
        ["ps", "-eo", "pgid=,rss="], capture_output=True, text=True, check=False
    ).stdout
    totals: dict[int, float] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            totals[int(parts[0])] = (
                totals.get(int(parts[0]), 0.0) + int(parts[1]) / 1024
            )
    return totals


def container_rss_mb(name: str) -> float:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    match = re.match(r"([\d.]+)\s*([KMG]i?B)", out)
    if not match:
        return 0.0
    scale = {
        "KiB": 1 / 1024,
        "KB": 1 / 1024,
        "MiB": 1,
        "MB": 1,
        "GiB": 1024,
        "GB": 1024,
    }
    return float(match[1]) * scale.get(match[2], 1)


# ---- the scheduler -----------------------------------------------------------


class Scheduler:
    def __init__(self, cfg: Config, run_dir: Path, stages: list[Stage]) -> None:
        self.cfg = cfg
        self.run_dir = run_dir
        self.stages = stages
        self.by_name = {s.name: s for s in stages}
        self.slots = Slots(cfg.state_dir / "slots", max(1, cfg.max_workers))
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_requested = False
        self.suspended_s = 0.0
        # (when it was noticed, how long) for each suspend, by the wall clock.
        self._suspends: list[tuple[float, float]] = []
        self.suspend_events: list[dict] = []
        self.min_mem_available_mb = mem_available_mb()
        self.started = time.time()
        self._last_wall = time.time()
        self._last_mono = time.monotonic()
        self._last_sample = 0.0
        self._last_notice: dict[str, float] = {}
        self._marker_lock = threading.Lock()

    # -- process control ------------------------------------------------------------

    def _launch(self, stage: Stage) -> None:
        command = stage.command()
        env = stage.env()
        stage.log.parent.mkdir(parents=True, exist_ok=True)
        marker = stage.log.with_suffix(".started")
        wrapped = list(command)
        external_wait = bool(self.cfg.slot_cmd) and stage.kind == "heavy"
        if external_wait:
            wrapped = [
                *self.cfg.slot_cmd,
                sys.executable,
                str(Path(__file__).resolve()),
                "_exec",
                str(marker),
                "--",
                *command,
            ]
        stage.process = subprocess.Popen(
            wrapped,
            cwd=stage.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
        launched = time.time()
        stage.launched_at = launched
        if not external_wait:
            stage.started_at = launched
        stage.status = "running"
        stage.last_progress = time.monotonic()
        threading.Thread(
            target=self._pump, args=(stage, stage.process), daemon=True
        ).start()
        if external_wait:
            threading.Thread(
                target=self._watch_marker, args=(stage, marker), daemon=True
            ).start()
        say(f"START {stage.name}  ({stage.description})")

    def _watch_marker(self, stage: Stage, marker: Path) -> None:
        """With an external slot wrapper, execution starts when the trampoline runs."""
        while stage.process is not None and stage.process.poll() is None:
            if self._read_marker(stage, marker):
                return
            time.sleep(0.5)

    def _read_marker(self, stage: Stage, marker: Path) -> bool:
        # Both the watcher thread and `_reap` read it; count the wait once.
        with self._marker_lock:
            if stage.started_at is not None:
                return True
            if not marker.exists():
                return False
            with contextlib.suppress(ValueError, OSError):
                stage.started_at = float(marker.read_text())
                stage.waited_slot_s += stage.started_at - (stage.ready_at or 0)
            return stage.started_at is not None

    def _pump(self, stage: Stage, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        with open(stage.log, "w", encoding="utf-8", errors="replace") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                # pytest's dots share the line when a report line arrives mid-row.
                at = line.find("Q2W|")
                if at >= 0:
                    self.events.put((stage.name, line[at:].rstrip("\n")))
        self.events.put((stage.name, "EOF"))

    def terminate(self, stage: Stage, reason: str) -> None:
        """Stop only what this runner started: its process group and named container."""
        process = stage.process
        stage.killed_for = reason
        if stage.container:
            subprocess.run(
                ["docker", "kill", stage.container],
                capture_output=True,
                check=False,
            )
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGTERM)

    def _force_kill(self, stage: Stage) -> None:
        process = stage.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

    # -- main loop ------------------------------------------------------------------

    def run(self) -> None:
        def on_signal(signum: int, _frame: object) -> None:
            self.stop_requested = True
            say(f"stop requested (signal {signum}): ending this run's workers only")

        old = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            self._loop()
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)

    def _loop(self) -> None:
        stop_deadline: float | None = None
        while True:
            self._check_suspend()
            self._drain_events()
            self._reap()
            if self.stop_requested:
                if stop_deadline is None:
                    stop_deadline = time.monotonic() + 15
                    for stage in self.stages:
                        if stage.status == "running":
                            self.terminate(stage, "stop requested")
                        elif stage.status == "pending":
                            stage.status = NOT_RUN
                            stage.reason = "run stopped before it started"
                elif time.monotonic() > stop_deadline:
                    for stage in self.stages:
                        self._force_kill(stage)
            else:
                self._timeouts()
                self._launch_ready()
                self._sample()
                self._progress()
            running = [s for s in self.stages if s.status == "running"]
            pending = [s for s in self.stages if s.status == "pending"]
            if not running and (not pending or self.stop_requested):
                break
            time.sleep(0.25)
        for stage in self.stages:
            if stage.status == "pending":
                stage.status = NOT_RUN
                stage.reason = stage.reason or "run stopped before it started"

    def _check_suspend(self) -> None:
        wall, mono = time.time(), time.monotonic()
        jump = (wall - self._last_wall) - (mono - self._last_mono)
        if jump > 5:
            self.suspended_s += jump
            self._suspends.append((wall, jump))
            self.suspend_events.append({"at": now_hms(), "seconds": round(jump)})
            say(
                f"the machine was suspended for about {jump:.0f}s; "
                "timings include it and the run is not hung"
            )
        self._last_wall, self._last_mono = wall, mono

    def _drain_events(self) -> None:
        while True:
            try:
                name, line = self.events.get_nowait()
            except queue.Empty:
                return
            stage = self.by_name[name]
            if line == "EOF":
                continue
            parts = line.split("|")
            kind = parts[1] if len(parts) > 1 else ""
            if kind == "collected":
                stage.tests_total, stage.files_total = int(parts[2]), int(parts[3])
                say(f"{name}: {parts[2]} tests in {parts[3]} files")
            elif kind == "file":
                stage.counters["files"] += 1
                stage.last_rss_mb = int(parts[6]) if len(parts) > 6 else 0
                if self.cfg.verbose:
                    say(f"{name}: {parts[2]}  {parts[3]} tests  {float(parts[4]):.0f}s")
            elif kind == "FAIL":
                stage.counters["failed"] += 1
                say(f"FAIL {name}: {parts[2]} [{parts[3]}] {'|'.join(parts[4:])}")

    def _progress(self) -> None:
        for stage in self.stages:
            if stage.status != "running" or not stage.files_total:
                continue
            if time.monotonic() - self._last_notice.get(stage.name, 0) < 60:
                continue
            self._last_notice[stage.name] = time.monotonic()
            say(
                f"{stage.name}: {stage.counters['files']}/{stage.files_total} files, "
                f"{stage.counters['failed']} failing, rss {stage.last_rss_mb} MB, "
                f"{(time.time() - (stage.started_at or time.time())) / 60:.1f} min"
            )

    def _timeouts(self) -> None:
        """A stage's timeout runs from when it started, not from when it queued.

        With a slot wrapper the process is launched first and only *starts* once
        the wrapper gets a slot, so timing it from the launch charged the queue to
        the stage: on a busy machine it timed out without having run. Until it
        starts, only `SLOT_WAIT_LIMIT_S` applies. Time the machine spent
        suspended is not the stage's either: a laptop that slept overnight
        would otherwise kill every stage running when it woke.
        """
        now = time.time()
        for stage in self.stages:
            if stage.status != "running" or stage.killed_for:
                continue
            if stage.started_at is not None:
                asleep = sum(
                    seconds for at, seconds in self._suspends if at > stage.started_at
                )
                if now - stage.started_at - asleep > stage.timeout_s:
                    say(f"TIMEOUT {stage.name} after {stage.timeout_s / 60:.0f} min")
                    self.terminate(stage, "timeout")
            elif (
                stage.launched_at is not None
                and now - stage.launched_at > SLOT_WAIT_LIMIT_S
            ):
                minutes = SLOT_WAIT_LIMIT_S / 60
                say(f"TIMEOUT {stage.name}: no test slot after {minutes:.0f} min")
                self.terminate(stage, f"timeout: no test slot after {minutes:.0f} min")

    def _sample(self) -> None:
        if time.monotonic() - self._last_sample < 2:
            return
        self._last_sample = time.monotonic()
        self.min_mem_available_mb = min(self.min_mem_available_mb, mem_available_mb())
        groups = None
        for stage in self.stages:
            if stage.status != "running" or stage.process is None:
                continue
            if stage.container:
                if int(time.time()) % 6 < 2:  # `docker stats` is slow; sample sparsely
                    stage.peak_rss_mb = max(
                        stage.peak_rss_mb, container_rss_mb(stage.container)
                    )
                continue
            groups = groups if groups is not None else group_rss_mb()
            stage.peak_rss_mb = max(
                stage.peak_rss_mb, groups.get(stage.process.pid, 0.0)
            )

    def _reap(self) -> None:
        for stage in self.stages:
            process = stage.process
            if stage.status != "running" or process is None:
                continue
            code = process.poll()
            if code is None:
                continue
            stage.returncode = code
            stage.ended_at = time.time()
            # The watcher polls; a stage that finished between polls still started.
            self._read_marker(stage, stage.log.with_suffix(".started"))
            if stage.slot_fd is not None:
                self.slots.release(stage.slot_fd)
                stage.slot_fd = None
            self._conclude(stage)

    def _conclude(self, stage: Stage) -> None:
        stage.result = read_report(stage.pytest_report)
        code = stage.returncode
        if stage.killed_for == "stop requested":
            stage.status, stage.reason = INTERRUPTED, "stopped by request"
        elif stage.killed_for:
            stage.status, stage.reason = FAILED, stage.killed_for
        elif code == 0 and (not stage.pytest_report or stage.result.get("finished")):
            stage.status = PASSED
        else:
            stage.status = FAILED
            if code is not None and code < 0:
                stage.reason = f"killed by signal {-code}" + (
                    " (out of memory?)" if code == -9 else ""
                )
            elif stage.pytest_report and not stage.result.get("finished"):
                stage.reason = (
                    f"exit {code}; the test process never reached its summary"
                )
            else:
                stage.reason = f"exit {code}"
        extra = ""
        if stage.result:
            outcomes = stage.result.get("outcomes", {})
            extra = (
                f" {outcomes.get('passed', 0)} passed, "
                f"{outcomes.get('failed', 0)} failed, "
                f"{outcomes.get('skipped', 0)} skipped"
            )
        say(
            f"END   {stage.name} {stage.status} in {stage.run_s:.0f}s "
            f"(waited {stage.wait_s:.0f}s to start){extra}"
            + (f" - {stage.reason}" if stage.reason else "")
        )

    # -- launching --------------------------------------------------------------------

    def _launch_ready(self) -> None:
        running_heavy = sum(
            1 for s in self.stages if s.status == "running" and s.kind == "heavy"
        )
        for stage in self.stages:
            if stage.status != "pending":
                continue
            blockers = [self.by_name[d] for d in stage.depends]
            failed = [
                b
                for b in blockers
                if b.status in (FAILED, INTERRUPTED, NOT_RUN, SKIPPED)
            ]
            if failed:
                stage.status = NOT_RUN
                stage.reason = "needs " + ", ".join(
                    f"{b.name} ({b.status})" for b in failed
                )
                say(f"NOT RUN {stage.name}: {stage.reason}")
                continue
            if any(b.status != PASSED for b in blockers):
                continue
            if stage.ready_at is None:
                stage.ready_at = time.time()
            if stage.kind == "light":
                self._launch(stage)
                continue
            if running_heavy >= self.cfg.max_workers:
                continue
            need = MEMORY_ESTIMATE_MB.get(stage.resource, 1000)
            free = mem_available_mb()
            if running_heavy == 0:
                enough = free >= min(need + MEMORY_RESERVE_MB, FIRST_WORKER_MIN_FREE_MB)
            else:
                enough = free >= need + MEMORY_RESERVE_MB
            if not enough:
                stage.waited_memory_s += 0.25
                if (
                    time.monotonic() - self._last_notice.get("mem:" + stage.name, 0)
                    > 60
                ):
                    self._last_notice["mem:" + stage.name] = time.monotonic()
                    say(
                        f"{stage.name}: waiting for memory ({free} MB available, "
                        f"needs about {need + MEMORY_RESERVE_MB} MB)"
                    )
                continue
            fd = self.slots.try_acquire()
            if fd is None:
                stage.waited_slot_s += 0.25
                continue
            stage.slot_fd = fd
            running_heavy += 1
            self._launch(stage)


def read_report(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


# ---- building the stages -----------------------------------------------------


class Planner:
    def __init__(self, cfg: Config, candidate: Candidate, run_dir: Path) -> None:
        self.cfg = cfg
        self.cand = candidate
        self.run_dir = run_dir
        self.snap = candidate.snapshot
        self.stages: list[Stage] = []
        self.skipped: list[tuple[str, str]] = []
        self._base_env_42: dict[str, str] | None = None
        self.zip_path: Path | None = None
        self.fixture_dirs: dict[str, str] = {}
        self.run_id = run_dir.name
        self.cand_tag = run_dir.name.rsplit("-", 1)[1][:1]
        self.selection: test_selection.Selection | None = None
        self.base = ""
        self.changes: list[test_selection.Change] = []

    # environments
    def env42(self) -> dict[str, str]:
        if self._base_env_42 is None:
            base = dict(os.environ)
            if self.cfg.env42_sh:
                # Replace, not merge: the script may also unset variables.
                base = sourced_environment(self.cfg.env42_sh, self.snap)
            py = [str(self.snap), str(self.snap / "tests")]
            if base.get("PYTHONPATH"):
                py.append(base["PYTHONPATH"])
            base["PYTHONPATH"] = os.pathsep.join(
                dict.fromkeys(p for p in os.pathsep.join(py).split(os.pathsep) if p)
            )
            self._base_env_42 = base
        return self._base_env_42

    def python42(self) -> str:
        return self.env42().get("PYBIN") or self.cfg.python42

    def runtime_env(self, env: dict[str, str]) -> None:
        if self.cfg.runtime_dir:
            env["ONLYMAP_RUNTIME_DIR"] = self.cfg.runtime_dir

    def worker_dir(self, name: str) -> Path:
        """A short path: a unix socket path is limited to about 100 characters and
        some tests put one under pytest's tmp_path inside this directory."""
        short = re.sub(
            r"[^a-z0-9]", "", name.replace("shard", "s").replace("qgis", "q")
        )
        stamp = self.run_id.rsplit("-", 1)[0]
        path = self.cfg.state_dir / "w" / f"{stamp}{self.cand_tag}-{short}"
        for sub in ("home", "tmp", "xdg-config", "xdg-data", "xdg-state", "xdg-cache"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        return path

    def worker_env(self, base: dict[str, str], name: str) -> dict[str, str]:
        wdir = self.worker_dir(name)
        env = dict(base)
        for leaking in ("QGIS_CUSTOM_CONFIG_PATH", "QGIS2WEBMAP_SAVE_ROOT"):
            env.pop(leaking, None)
        env.update(
            HOME=str(wdir / "home"),
            XDG_CONFIG_HOME=str(wdir / "xdg-config"),
            XDG_DATA_HOME=str(wdir / "xdg-data"),
            XDG_STATE_HOME=str(wdir / "xdg-state"),
            XDG_CACHE_HOME=str(wdir / "xdg-cache"),
            TMPDIR=str(wdir / "tmp"),
            Q2W_WORKER=name,
            Q2W_WORKER_DIR=str(wdir),
            Q2W_REPORT_JSON=str(wdir / "report.json"),
            Q2W_RSS_ABORT_MB=os.environ.get("Q2W_RSS_ABORT_MB", "3500"),
            QT_QPA_PLATFORM="offscreen",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.runtime_env(env)
        return env

    def pytest_args(
        self, wdir: Path, targets: list[str], inside: str | None = None
    ) -> list[str]:
        return [*self.pytest_base(wdir, inside), *targets]

    def pytest_base(self, wdir: Path, inside: str | None = None) -> list[str]:
        root = inside or str(wdir)
        return [
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "q2w_report",
            "-o",
            "faulthandler_timeout=300",
            "-o",
            "tmp_path_retention_policy=failed",
            "--basetemp",
            f"{root}/p",
        ]

    # stage constructors
    def add(self, stage: Stage) -> Stage:
        self.stages.append(stage)
        return stage

    def log(self, name: str) -> Path:
        return self.run_dir / "logs" / f"{name}.log"

    def add_light(self, name: str, command: list[str], description: str, **kw) -> Stage:
        return self.add(
            Stage(
                name=name,
                kind="light",
                command=lambda: command,
                env=lambda: self.worker_env(dict(os.environ), name),
                cwd=self.snap,
                log=self.log(name),
                description=description,
                timeout_s=600,
                **kw,
            )
        )

    def add_primary(
        self, targets: list[str], shards: int, durations: dict
    ) -> list[Stage]:
        out = []
        for index, (items, estimate) in enumerate(
            selection.balance(targets, shards, durations), start=1
        ):
            name = f"qgis42-shard{index}" if shards > 1 else "qgis42"
            wdir = self.worker_dir(name)
            out.append(
                self.add(
                    Stage(
                        name=name,
                        kind="heavy",
                        resource="shard42",
                        command=lambda items=items, wdir=wdir: [  # type: ignore[misc]
                            self.python42(),
                            *self.pytest_args(wdir, items),
                        ],
                        env=lambda name=name: self.worker_env(self.env42(), name),  # type: ignore[misc]
                        cwd=self.snap,
                        log=self.log(name),
                        pytest_report=wdir / "report.json",
                        estimate_s=estimate,
                        timeout_s=max(1800.0, estimate * 2.5 + 600),
                        description=(
                            f"QGIS 4.2, {len(items)} test files, "
                            f"~{estimate / 60:.0f} min est."
                        ),
                        targets=items,
                    )
                )
            )
        return out

    def docker_available(self) -> str:
        if shutil.which("docker") is None:
            return "docker is not installed"
        probe = subprocess.run(
            ["docker", "image", "inspect", self.cfg.docker_image],
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            return (
                f"docker image {self.cfg.docker_image} is missing "
                "(set Q2W_DOCKER_IMAGE; it needs QGIS 3.44 and pytest)"
            )
        return ""

    def docker_command(
        self, name: str, inner: list[str], wdir: Path, env: dict[str, str]
    ) -> list[str]:
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            f"q2w-{self.run_id}-{name}",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "--memory",
            f"{self.cfg.docker_memory_mb}m",
            "--memory-swap",
            f"{self.cfg.docker_memory_mb}m",
        ]
        for key, value in env.items():
            command += ["-e", f"{key}={value}"]
        command += [
            "-v",
            f"{self.snap}:/w:ro",
            "-v",
            f"{wdir}:/tmp",
            "-w",
            "/w",
        ]
        if self.cfg.runtime_dir:
            command += ["-v", f"{self.cfg.runtime_dir}:/rt:ro"]
        for extra in (self.zip_path,):
            if extra is not None:
                command += ["-v", f"{extra.parent}:/dist:ro"]
        return [*command, self.cfg.docker_image, *inner]

    def container_env(self, name: str) -> dict[str, str]:
        return {
            "HOME": "/tmp/home",
            "XDG_CONFIG_HOME": "/tmp/xdg-config",
            "XDG_DATA_HOME": "/tmp/xdg-data",
            "XDG_STATE_HOME": "/tmp/xdg-state",
            "XDG_CACHE_HOME": "/tmp/xdg-cache",
            "TMPDIR": "/tmp/tmp",
            "Q2W_WORKER": name,
            "Q2W_WORKER_DIR": "/tmp",
            "Q2W_REPORT_JSON": "/tmp/report.json",
            "Q2W_RSS_ABORT_MB": os.environ.get("Q2W_RSS_ABORT_MB", "3500"),
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "/w:/w/tests",
            **({"ONLYMAP_RUNTIME_DIR": "/rt"} if self.cfg.runtime_dir else {}),
        }

    def add_container_pytest(
        self, name: str, targets: list[str], estimate: float, description: str
    ) -> Stage | None:
        problem = self.docker_available()
        if problem:
            self.skipped.append((name, problem))
            return None
        wdir = self.worker_dir(name)
        inner = ["python3", *self.pytest_args(wdir, targets, inside="/tmp")]
        return self.add(
            Stage(
                name=name,
                kind="heavy",
                resource="shard344",
                command=lambda: self.docker_command(
                    name, inner, wdir, self.container_env(name)
                ),
                env=lambda: dict(os.environ),
                cwd=self.snap,
                log=self.log(name),
                pytest_report=wdir / "report.json",
                container=f"q2w-{self.run_id}-{name}",
                estimate_s=estimate,
                timeout_s=max(1800.0, estimate * 3 + 600),
                description=description,
                targets=targets,
            )
        )

    def add_package(self) -> Stage:
        outdir = self.run_dir / "dist"
        script = (
            f"{shlex.quote(sys.executable)} scripts/package_plugin.py "
            f"--outdir {shlex.quote(str(outdir))}"
            f" && {shlex.quote(sys.executable)} scripts/verify_package.py "
            f"{shlex.quote(str(outdir))}/{PLUGIN_ZIP_GLOB}"
        )
        return self.add_light(
            "package", ["sh", "-c", script], "build the plugin ZIP and verify its shape"
        )

    def add_site_checks(self) -> None:
        """The two checks plugins.qgis.org runs on an upload, which the
        release workflow runs too: the security scan (Bandit, detect-secrets,
        flake8) and the Qt6 check, in the image the site uses. A release
        round that skips them can tag a ZIP the site refuses."""
        scan_prefix = shlex.split(os.environ.get("Q2W_SCAN_WRAPPER", ""))

        def scan() -> list[str]:
            return [
                *scan_prefix,
                sys.executable,
                str(self.snap / "scripts" / "scan_package.py"),
                str(self.locate_zip()),
            ]

        self.add(
            Stage(
                name="scan",
                kind="light",
                depends=["package"],
                command=scan,
                env=lambda: self.worker_env(dict(os.environ), "scan"),
                cwd=self.snap,
                log=self.log("scan"),
                timeout_s=900,
                description="the site's security scan (Bandit, detect-secrets, flake8)",
            )
        )
        problem = self.docker_available()
        if problem:
            self.skipped.append(("qt6-check", problem))
            return
        image = qt6_checker_image(self.snap)

        def qt6() -> list[str]:
            zip_file = self.locate_zip()
            assert zip_file is not None
            return [
                "docker", "run", "--rm",
                "-u", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{self.snap}:/w:ro",
                "-v", f"{zip_file.parent}:/dist:ro",
                "-w", "/w",
                image,
                "python3", "scripts/check_qt6.py", f"/dist/{zip_file.name}",
            ]  # fmt: skip

        self.add(
            Stage(
                name="qt6-check",
                kind="light",
                depends=["package"],
                command=qt6,
                env=lambda: dict(os.environ),
                cwd=self.snap,
                log=self.log("qt6-check"),
                timeout_s=1200,
                description="the site's Qt6 check, in the image it runs it in",
            )
        )

    def locate_zip(self) -> Path | None:
        found = sorted((self.run_dir / "dist").glob(PLUGIN_ZIP_GLOB))
        if found:
            self.zip_path = found[0]
        return self.zip_path

    def add_smoke42(self, templates: str) -> Stage:
        wdir = self.worker_dir("smoke42")
        return self.add(
            Stage(
                name="zip-smoke-qgis42",
                kind="heavy",
                resource="smoke",
                depends=["package"],
                command=lambda: [
                    self.python42(),
                    str(self.snap / "scripts" / "zip_smoke.py"),
                    str(self.locate_zip()),
                    str(self.snap),
                ],
                env=lambda: {
                    **self.worker_env(self.env42(), "smoke42"),
                    "SMOKE_TEMPLATES": templates,
                },
                cwd=wdir / "tmp",
                log=self.log("zip-smoke-qgis42"),
                timeout_s=900,
                description="packaged ZIP installed and exercised on QGIS 4.2"
                + (f" ({templates})" if templates else " (all templates)"),
            )
        )

    def add_smoke344(self, templates: str) -> Stage | None:
        problem = self.docker_available()
        if problem:
            self.skipped.append(("zip-smoke-qgis344", problem))
            return None
        wdir = self.worker_dir("smoke344")

        def command() -> list[str]:
            zip_file = self.locate_zip()
            assert zip_file is not None
            env = {**self.container_env("smoke344"), "SMOKE_TEMPLATES": templates}
            env["PYTHONPATH"] = ""
            return self.docker_command(
                "zip-smoke-qgis344",
                ["python3", "/w/scripts/zip_smoke.py", f"/dist/{zip_file.name}", "/w"],
                wdir,
                env,
            )

        return self.add(
            Stage(
                name="zip-smoke-qgis344",
                kind="heavy",
                resource="smoke",
                depends=["package"],
                command=command,
                env=lambda: dict(os.environ),
                cwd=self.snap,
                log=self.log("zip-smoke-qgis344"),
                container=f"q2w-{self.run_id}-zip-smoke-qgis344",
                timeout_s=900,
                description="packaged ZIP installed and exercised on QGIS 3.44"
                + (f" ({templates})" if templates else " (all templates)"),
            )
        )

    # browser
    def browser_ready(self) -> str:
        py = self.cfg.browser_python or sys.executable
        probe = subprocess.run(
            [py, "-c", "import playwright, pytest_playwright"],
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            return (
                f"{py} cannot import playwright/pytest-playwright "
                "(set Q2W_BROWSER_PYTHON)"
            )
        if not self.cfg.runtime_dir:
            return "ONLYMAP_RUNTIME_DIR is not set; the browser tier needs the runtime"
        return ""

    def add_browser(
        self,
        targets: list[str],
        need_fixtures: bool = True,
        browsers: tuple[str, ...] = ("chromium",),
    ) -> None:
        problem = self.browser_ready()
        if problem:
            self.skipped.append(("browser", problem))
            return
        depends: list[str] = []
        if need_fixtures:
            self.add(
                Stage(
                    name="browser-fixtures",
                    kind="heavy",
                    resource="fixtures",
                    command=lambda: [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "_fixtures",
                        "--snapshot",
                        str(self.snap),
                        "--cache",
                        str(self.cfg.state_dir / "fixtures"),
                        "--plugin-tree",
                        self.cand.plugin_tree,
                        "--python",
                        self.python42(),
                        "--out-json",
                        str(self.run_dir / "fixtures.json"),
                    ],
                    env=lambda: self.worker_env(self.env42(), "fixtures"),
                    cwd=self.snap,
                    log=self.log("browser-fixtures"),
                    timeout_s=1200,
                    description="build or reuse this candidate's browser fixtures",
                )
            )
            depends = ["browser-fixtures"]
        for browser in browsers:
            self._add_browser_stage(browser, targets, depends)

    def _add_browser_stage(
        self, browser: str, targets: list[str], depends: list[str]
    ) -> None:
        # Chromium keeps the stage name its recorded durations are under.
        name = "browser" if browser == "chromium" else f"browser-{browser}"
        wdir = self.worker_dir(name)

        def env() -> dict[str, str]:
            merged = self.worker_env(dict(os.environ), name)
            merged["PYTHONPATH"] = os.pathsep.join(
                [str(self.snap), str(self.snap / "tests")]
            )
            # The worker HOME is empty, so point Playwright at the real browsers.
            for key in ("PLAYWRIGHT_BROWSERS_PATH",):
                if key in os.environ:
                    merged[key] = os.environ[key]
            if "PLAYWRIGHT_BROWSERS_PATH" not in merged:
                merged["PLAYWRIGHT_BROWSERS_PATH"] = str(
                    Path.home() / ".cache" / "ms-playwright"
                )
            merged["QGIS_PYTHON"] = self.python42()
            fixtures = read_report(self.run_dir / "fixtures.json")
            for key, path in fixtures.get("dirs", {}).items():
                merged[key] = path
            for key in ("WAYLAND_DISPLAY", "DISPLAY", "LD_LIBRARY_PATH"):
                merged.pop(key, None)
            # Firefox draws through EGL (see CONTRIBUTING.md); where the system
            # has no EGL library of its own, the run names one.
            if browser == "firefox" and self.cfg.firefox_library_path:
                merged["LD_LIBRARY_PATH"] = self.cfg.firefox_library_path
            # A release round is meant to draw: a missing WebGL2 fails, not skips.
            merged["Q2W_REQUIRE_WEBGL2"] = "1"
            return merged

        browser_py = self.cfg.browser_python or sys.executable
        self.add(
            Stage(
                name=name,
                kind="heavy",
                resource="browser",
                depends=depends,
                command=lambda: [
                    browser_py,
                    *self.pytest_base(wdir),
                    "--browser",
                    browser,
                    *targets,
                ],
                env=env,
                cwd=self.snap,
                log=self.log(name),
                pytest_report=wdir / "report.json",
                timeout_s=3600,
                estimate_s=520.0,
                description=f"{browser.capitalize()}, {len(targets)} test file(s)",
                targets=targets,
            )
        )


# ---- profiles ----------------------------------------------------------------


def flatten(lines: dict[str, list[str]]) -> list[str]:
    return sorted(lines)


def build_plan(
    args: argparse.Namespace, cfg: Config, cand: Candidate, run_dir: Path
) -> Planner:
    groups = selection.load_groups(cand.snapshot)
    durations = selection.load_durations(cand.snapshot)
    planner = Planner(cfg, cand, run_dir)
    profile = args.profile
    shards = 1 if args.serial else args.shards

    unit_all = selection.list_test_files(cand.snapshot, "unit")
    qgis_all = selection.list_test_files(cand.snapshot, "qgis")
    fixtures_all = selection.list_test_files(cand.snapshot, "fixtures")
    primary_all = unit_all + qgis_all + fixtures_all

    compat_targets = [entry["target"] for entry in groups["compat344"]]

    if profile == "patch":
        if args.base:
            base = args.base
        elif args.worktree:
            base = "HEAD"
        else:
            base = os.environ.get("Q2W_BASE_REF") or default_base()
        changes = selection.changes_from_git(
            REPO, base, cand.commit, include_worktree=args.worktree
        )
        picked = selection.select(
            changes,
            cand.snapshot,
            groups,
            force_broad="--full-suite requested" if args.full_suite else None,
        )
        planner.selection, planner.base, planner.changes = picked, base, changes
        targets = sorted(picked.tests)
        est = sum(selection.estimate_seconds(t, durations) for t in targets)
        patch_shards = shards if shards != 0 else (2 if est > 240 else 1)
        changed_py = [
            c.path for c in changes if c.path.endswith(".py") and c.status != "D"
        ]
        ruff_cmd = ruff_command(cand.snapshot)
        files = " ".join(map(shlex.quote, changed_py))
        if ruff_cmd and changed_py:
            planner.add_light(
                "ruff",
                [
                    "sh",
                    "-c",
                    f"{ruff_cmd} check --no-cache -q {files} && "
                    f"{ruff_cmd} format --no-cache --check -q {files}",
                ],
                f"ruff on {len(changed_py)} changed file(s)",
            )
        elif changed_py:
            planner.skipped.append(("ruff", "ruff was not found (set RUFF)"))
        planner.add_light(
            "translations",
            [sys.executable, "scripts/update_translations.py", "--check"],
            "translation catalogues are current",
        )
        planner.add_primary(targets, max(1, patch_shards), durations)
        if picked.compat:
            planner.add_container_pytest(
                "qgis344-compat",
                sorted(picked.compat),
                sum(selection.estimate_seconds(t, durations) for t in picked.compat),
                f"QGIS 3.44 compatibility, {len(picked.compat)} target(s)",
            )
        if picked.zip_smoke:
            planner.add_package()
            planner.add_smoke42(SMOKE_SUBSET)
            planner.add_smoke344(SMOKE_SUBSET)
        if picked.browser:
            planner.add_browser(sorted(picked.browser))
        return planner

    # release and full
    planner.add_light(
        "translations",
        [sys.executable, "scripts/update_translations.py", "--check"],
        "translation catalogues are current",
    )
    ruff_cmd = ruff_command(cand.snapshot)
    if ruff_cmd:
        planner.add_light(
            "ruff",
            [
                "sh",
                "-c",
                f"{ruff_cmd} check --no-cache -q . && "
                f"{ruff_cmd} format --no-cache --check -q .",
            ],
            "ruff check and format on the whole tree",
        )
    else:
        planner.skipped.append(("ruff", "ruff was not found (set RUFF)"))
    planner.add_package()
    planner.add_site_checks()
    planner.add_primary(primary_all, shards, durations)
    if profile == "release":
        planner.add_container_pytest(
            "qgis344-compat",
            compat_targets,
            sum(selection.estimate_seconds(t, durations) for t in compat_targets),
            f"focused QGIS 3.44 suite, {len(compat_targets)} entries",
        )
        planner.add_smoke344(SMOKE_SUBSET)
    else:
        full344_shards = 1 if args.serial else args.shards
        for index, (items, est) in enumerate(
            selection.balance(primary_all, full344_shards, durations), start=1
        ):
            suffix = f"-shard{index}" if full344_shards > 1 else ""
            planner.add_container_pytest(
                f"qgis344-full{suffix}",
                items,
                est * 0.9,
                f"whole QGIS 3.44 suite, {len(items)} test files",
            )
        planner.add_smoke344(SMOKE_SUBSET)
    planner.add_smoke42("")
    planner.add_browser(["tests/browser"], browsers=("chromium", "firefox", "webkit"))
    return planner


def default_base() -> str:
    try:
        upstream = git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        )
        return git("merge-base", "HEAD", upstream)
    except subprocess.CalledProcessError:
        return git("rev-parse", "HEAD~1")


def ruff_command(snapshot: Path) -> str:
    candidate = os.environ.get("RUFF") or shutil.which("ruff")
    if candidate:
        return shlex.quote(candidate)
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, check=False
    )
    return f"{shlex.quote(sys.executable)} -m ruff" if probe.returncode == 0 else ""


# ---- reporting ---------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_record(stage: Stage) -> dict:
    result = stage.result
    totals = {"setup": 0.0, "call": 0.0, "teardown": 0.0}
    for record in result.get("files", {}).values():
        for key in totals:
            totals[key] += record.get(key, 0.0)
    return {
        "name": stage.name,
        "status": stage.status,
        "reason": stage.reason,
        "returncode": stage.returncode,
        "required": stage.required,
        "wait_slot_s": round(stage.wait_s, 1),
        "wait_memory_s": round(stage.waited_memory_s, 1),
        "run_s": round(stage.run_s, 1),
        "peak_rss_mb_sampled": round(stage.peak_rss_mb),
        "peak_rss_mb_process": result.get("peak_rss_mb"),
        "outcomes": result.get("outcomes", {}),
        "warnings": result.get("warnings", 0),
        "skip_reasons": result.get("skip_reasons", {}),
        "setup_call_teardown_s": {k: round(v, 1) for k, v in totals.items()},
        "tests_total": result.get("tests_total"),
        "log": str(stage.log),
        "description": stage.description,
    }


def final_report(
    args: argparse.Namespace,
    cfg: Config,
    cand: Candidate,
    planner: Planner,
    sched: Scheduler,
    run_dir: Path,
    snapshot_unchanged: bool,
) -> int:
    stages = planner.stages
    zip_info = None
    zip_file = planner.locate_zip()
    package = next((s for s in stages if s.name == "package"), None)
    if zip_file and package and package.status == PASSED:
        zip_info = {"path": str(zip_file), "sha256": sha256_file(zip_file)}
    elapsed = time.time() - sched.started
    required_failed = [
        s for s in stages if s.required and s.status in (FAILED, INTERRUPTED, NOT_RUN)
    ]
    skipped_required = planner.skipped
    interrupted = sched.stop_requested or any(s.status == INTERRUPTED for s in stages)
    all_passed = not required_failed and not skipped_required and snapshot_unchanged
    summary = {
        "runner_version": RUNNER_VERSION,
        "profile": args.profile,
        "command": " ".join(sys.argv),
        "commit": cand.commit,
        "tree": cand.tree,
        "uncommitted_files_not_tested": 0 if cand.worktree else cand.dirty_files,
        "snapshot_is_worktree_copy": cand.worktree,
        "snapshot_digest": cand.snapshot_digest,
        "snapshot_unchanged_after_run": snapshot_unchanged,
        "zip": zip_info,
        "elapsed_s": round(elapsed, 1),
        "suspended_s": round(sched.suspended_s),
        "suspend_events": sched.suspend_events,
        "min_mem_available_mb": sched.min_mem_available_mb,
        "max_heavy_workers": cfg.max_workers,
        "stages": [stage_record(s) for s in stages],
        "stages_skipped_not_configured": [
            {"name": n, "reason": r} for n, r in skipped_required
        ],
        "interrupted": interrupted,
        "verdict": "interrupted"
        if interrupted
        else (
            "passed"
            if all_passed
            else (
                "incomplete" if not required_failed and skipped_required else "failed"
            )
        ),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    with open(cfg.state_dir / "ledger.jsonl", "a") as ledger:
        ledger.write(
            json.dumps(
                {
                    k: summary[k]
                    for k in ("profile", "commit", "zip", "verdict", "elapsed_s")
                }
            )
            + "\n"
        )
    # observed per-file durations, for `update-durations`
    observed: dict[str, float] = {}
    for stage in stages:
        if stage.name.startswith("qgis42"):
            for path, record in stage.result.get("files", {}).items():
                observed[path] = round(
                    record["setup"] + record["call"] + record["teardown"], 1
                )
    (run_dir / "observed-durations.json").write_text(json.dumps(observed, indent=1))

    print()
    say(
        f"==== {args.profile.upper()} SUMMARY  commit {cand.commit[:10]}  "
        f"{elapsed / 60:.1f} min ===="
    )
    if cand.worktree:
        say(
            "NOTE: this tested a copy of the working tree, not a commit; "
            "it cannot approve a release."
        )
    elif cand.dirty_files:
        say(f"NOTE: {cand.dirty_files} uncommitted file(s) are NOT in the snapshot.")
    if sched.suspend_events:
        say(
            f"the machine was suspended {len(sched.suspend_events)}x "
            f"(~{sched.suspended_s:.0f}s total)"
        )
    if zip_info:
        say(f"ZIP BUILT AND VERIFIED: {zip_info['path']}")
        say(f"  sha256 {zip_info['sha256']}")
        say("  (the package has the right shape; this is NOT the release verdict)")
    elif package:
        say(f"ZIP NOT verified: package stage {package.status}")
    for stage in stages:
        outcomes = stage.result.get("outcomes", {})
        counts = (
            f"{outcomes.get('passed', 0)} passed, {outcomes.get('failed', 0)} failed, "
            f"{outcomes.get('skipped', 0)} skipped"
            if outcomes
            else ""
        )
        say(
            f"- {stage.name}: {stage.status}  run {stage.run_s:.0f}s, "
            f"waited {stage.wait_s:.0f}s to start"
            + (
                f" + {stage.waited_memory_s:.0f}s for memory"
                if stage.waited_memory_s >= 1
                else ""
            )
            + (f", peak {stage.peak_rss_mb:.0f} MB" if stage.peak_rss_mb else "")
            + (f"  [{counts}]" if counts else "")
            + (f"  ({stage.reason})" if stage.reason else "")
        )
        if outcomes.get("skipped"):
            for reason, count in list(stage.result.get("skip_reasons", {}).items())[:3]:
                say(f"    skipped x{count}: {reason}")
        if stage.result.get("warnings"):
            say(f"    {stage.result['warnings']} warning(s) recorded")
        if stage.status == FAILED:
            say(f"    log: {stage.log}")
    for name, reason in skipped_required:
        say(f"- {name}: SKIPPED (not configured): {reason}")
    if not snapshot_unchanged:
        say("- the snapshot changed during the run: results are void")
    if interrupted:
        verdict = f"{args.profile.upper()} INTERRUPTED: unfinished stages prove nothing"
        code = 130
    elif all_passed and args.only:
        verdict = (
            f"PARTIAL RUN (--only {args.only}) passed on commit {cand.commit[:10]}: "
            f"this is not a {args.profile} verdict"
        )
        code = 0
    elif all_passed:
        verdict = f"{args.profile.upper()} CHECKS PASSED on commit {cand.commit[:10]}"
        code = 0
    elif not required_failed:
        verdict = (
            f"{args.profile.upper()} INCOMPLETE: every stage that ran passed, "
            "but some were not run"
        )
        code = 3
    else:
        verdict = f"{args.profile.upper()} CHECKS FAILED"
        code = 1
    say(verdict)
    say(f"details: {run_dir}/summary.json")
    return code


# ---- commands ----------------------------------------------------------------


def command_run(args: argparse.Namespace) -> int:
    cfg = Config.from_env(args)
    if args.profile in ("release", "full") and args.worktree:
        raise SystemExit("release/full test a commit, never an unsaved working tree")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%m%d-%H%M%S") + f"-{args.profile}"
    run_dir = cfg.state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    ref = args.commit or "HEAD"
    if args.profile in ("release", "full") and not cfg.runtime_dir:
        raise SystemExit(
            "ONLYMAP_RUNTIME_DIR is not set: without the runtime the writer/browser "
            "tests skip and a green run proves little. Set it to the pinned runtime."
        )
    say(
        f"{args.profile}: snapshotting {ref}"
        + (" (working tree copy)" if args.worktree else "")
    )
    cand = make_snapshot(cfg, run_id, ref, args.worktree)
    say(f"commit {cand.commit}  tree {cand.tree[:12]}  snapshot {cand.snapshot}")
    (cfg.state_dir / "active.json").write_text(
        json.dumps({"pid": os.getpid(), "run": str(run_dir)})
    )
    try:
        planner = build_plan(args, cfg, cand, run_dir)
        if args.only:
            prefixes = tuple(args.only.split(","))
            planner.stages = [s for s in planner.stages if s.name.startswith(prefixes)]
            kept = {s.name for s in planner.stages}
            for stage in planner.stages:
                stage.depends = [d for d in stage.depends if d in kept]
            planner.skipped = []
            say(f"--only {args.only}: the verdict covers just these stages")
        if planner.selection is not None:
            print("\n".join(planner.selection.describe()))
            print(f"base: {planner.base}; changed files: {len(planner.changes)}")
        say("plan:")
        for stage in planner.stages:
            print(f"  {stage.name:22} {stage.kind:5} {stage.description}")
        for name, reason in planner.skipped:
            print(f"  {name:22} SKIPPED: {reason}")
        if args.dry_run:
            say("dry run: nothing executed")
            return 0
        sched = Scheduler(cfg, run_dir, planner.stages)
        sched.run()
        unchanged = tree_digest(cand.snapshot) == cand.snapshot_digest
        code = final_report(args, cfg, cand, planner, sched, run_dir, unchanged)
    finally:
        with contextlib.suppress(OSError):
            (cfg.state_dir / "active.json").unlink()
        if not args.keep_snapshot:
            remove_snapshot(cand)
    return code


def command_stop(args: argparse.Namespace) -> int:
    cfg = Config.from_env(args)
    active = cfg.state_dir / "active.json"
    if not active.exists():
        print("no run in progress")
        return 0
    pid = json.loads(active.read_text())["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"run {pid} is already gone")
        active.unlink()
        return 0
    print(f"asked run {pid} to stop; it ends its own workers and prints its summary")
    return 0


def command_update_durations(args: argparse.Namespace) -> int:
    observed = json.loads((Path(args.run_dir) / "observed-durations.json").read_text())
    path = REPO / selection.DURATIONS_FILE
    data = json.loads(path.read_text())
    data["qgis42_files"] = dict(sorted({**data["qgis42_files"], **observed}.items()))
    path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"updated {len(observed)} file durations in {path}; review and commit them")
    return 0


def command_groups_check(_args: argparse.Namespace) -> int:
    problems = selection.check_groups(REPO)
    print("\n".join(problems) or "tests/test_groups.json is consistent")
    return 1 if problems else 0


def command_exec(args: argparse.Namespace) -> int:
    """Trampoline: record when a slot wrapper let us start, then become the command."""
    Path(args.marker).write_text(repr(time.time()))
    os.execvp(args.rest[0], args.rest)
    return 1  # unreachable


def command_fixtures(args: argparse.Namespace) -> int:
    """Build (or reuse) the browser acceptance fixtures; write their paths as JSON."""
    snap, cache = Path(args.snapshot), Path(args.cache)
    runtime_dir = os.environ.get("ONLYMAP_RUNTIME_DIR", "")
    lock = json.loads(
        (snap / "nika_onlymap_exporter/runtime/runtime-lock.json").read_text()
    )
    # Plain in a package's dist/, gzipped in a fetched cache: hash whichever is there.
    runtime_files = [
        Path(runtime_dir) / stored
        for name in sorted(lock["files"])
        for stored in (name, name + ".gz")
    ]
    runtime_hash = selection.file_digest([p for p in runtime_files if p.exists()])
    builder_files = sorted(
        [
            *(snap / "tests/browser").glob("*_fixture_builder.py"),
            snap / "tests/hermetic.py",
            *(snap / "tests/qgis").glob("*_fixtures.py"),
        ]
    )
    env_id = subprocess.run(
        [
            args.python,
            "-c",
            "from qgis.core import Qgis; import sys; "
            "print(Qgis.version(), sys.version_info[:2])",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    key = selection.fixture_key(
        args.plugin_tree, selection.file_digest(builder_files), runtime_hash, env_id
    )
    entry = cache / key
    entry.mkdir(parents=True, exist_ok=True)
    dirs: dict[str, str] = {}
    built_now: list[str] = []
    for name, variable, proof in BUILDERS:
        final = entry / name
        if (final / proof).exists():
            print(f"Q2W|fixture|{name}|reused|{final}", flush=True)
            dirs[variable] = str(final)
            continue
        scratch = entry / f".build-{name}-{os.getpid()}"
        scratch.mkdir()
        done = subprocess.run(
            [
                args.python,
                str(snap / "tests/browser" / f"{name}_fixture_builder.py"),
                str(scratch),
            ],
            cwd=snap,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        # Only a builder that exited 0 AND wrote its proof file becomes reusable.
        if done.returncode != 0 or not (scratch / proof).exists():
            sys.stderr.write(done.stdout + done.stderr)
            print(f"Q2W|fixture|{name}|FAILED|exit {done.returncode}", flush=True)
            return 1
        os.replace(scratch, final)
        built_now.append(name)
        dirs[variable] = str(final)
        print(f"Q2W|fixture|{name}|built|{final}", flush=True)
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "key": key,
                "plugin_tree": args.plugin_tree,
                "runtime": runtime_hash,
                "env": env_id,
            },
            indent=1,
        )
    )
    Path(args.out_json).write_text(
        json.dumps({"key": key, "dirs": dirs, "built": built_now})
    )
    print(f"fixtures ready (key {key}); built now: {built_now or 'none, all reused'}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = root.add_subparsers(dest="profile", required=True)
    for name in ("patch", "release", "full"):
        p = sub.add_parser(name)
        p.add_argument("--commit", help="commit to test (default HEAD)")
        p.add_argument("--base", help="patch: compare against this ref")
        p.add_argument(
            "--worktree",
            action="store_true",
            help="patch: test a copy of the working tree incl. uncommitted edits",
        )
        p.add_argument(
            "--full-suite",
            action="store_true",
            help="patch: select the whole primary suite",
        )
        p.add_argument(
            "--serial",
            action="store_true",
            help="one process, one worker at a time (diagnosing)",
        )
        p.add_argument(
            "--shards",
            type=int,
            default=2 if name != "patch" else 0,
            help="primary-suite process shards (patch: 0 = decide by size)",
        )
        p.add_argument(
            "--max-workers",
            type=int,
            default=HEAVY_WORKERS_DEFAULT,
            help="heavy workers at once (default 2)",
        )
        p.add_argument(
            "--only",
            help="comma-separated stage-name prefixes to run (benchmarks, reruns); "
            "the verdict then covers only those stages",
        )
        p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
        p.add_argument("--keep-snapshot", action="store_true")
        p.add_argument("--state-dir")
        p.add_argument("--verbose", action="store_true")
    for name in ("stop", "groups-check"):
        p = sub.add_parser(name)
        p.add_argument("--state-dir")
    p = sub.add_parser("update-durations")
    p.add_argument("run_dir")
    p = sub.add_parser("_exec")
    p.add_argument("marker")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p = sub.add_parser("_fixtures")
    for flag in ("--snapshot", "--cache", "--plugin-tree", "--python", "--out-json"):
        p.add_argument(flag, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.profile == "_exec":
        args.rest = (
            [a for a in args.rest if a != "--"]
            if args.rest[:1] == ["--"]
            else args.rest
        )
        return command_exec(args)
    if args.profile == "_fixtures":
        return command_fixtures(args)
    if args.profile == "stop":
        return command_stop(args)
    if args.profile == "update-durations":
        return command_update_durations(args)
    if args.profile == "groups-check":
        return command_groups_check(args)
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
