"""Fixtures shared by every QGIS-dependent tier.

`qgis_app` lives here rather than in each tier's own conftest, and that is not
tidiness. `QgsApplication` must be created once per *process* and never torn
down and recreated: a second instance segfaults the interpreter. With a
session-scoped copy in `tests/qgis/conftest.py` and another in
`tests/fixtures/conftest.py`, each tier passed on its own and running them
together crashed - which is precisely how CI would have run them.

Tiers that need PyQGIS still guard their own imports with `importorskip`, so a
contributor without QGIS keeps a clean skip rather than a collection error.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---- Per-worker isolation (scripts/run_tests.py) --------------------------------
#
# The runner starts each test process ("worker") with its own HOME, XDG
# directories, TMPDIR and pytest basetemp under one directory named by
# Q2W_WORKER_DIR, and labels the process with Q2W_WORKER. A process started any
# other way (plain `pytest`) has neither variable and none of this applies.
#
# Everything a test can leave behind is created under those: `tmp_path`
# (basetemp), `tempfile` (TMPDIR - the live preview folder and the browser
# fixture builders use it), the QGIS profile and QSettings (`tests/hermetic.py`
# derives both from `tmp_path_factory`), the default save root, and HOME. The
# preview server binds port 0, so no port is fixed. Two workers therefore share
# no file and no port. If a worker was started without the variables the runner
# sets, or something escaped its directory, the run fails here, loudly and
# before a test writes into somebody else's files.


def _worker_dir() -> Path | None:
    value = os.environ.get("Q2W_WORKER_DIR")
    return Path(value).resolve() if value else None


def _inside(path: object, root: Path) -> bool:
    try:
        Path(str(path)).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def pytest_configure(config: pytest.Config) -> None:
    root = _worker_dir()
    if root is None:
        return
    tempfile.tempdir = None  # forget a directory cached before TMPDIR was read
    problems = []
    for what, value in (
        ("HOME", Path.home()),
        ("TMPDIR (tempfile.gettempdir())", tempfile.gettempdir()),
        # A pytest the suite starts itself (a crash-isolation child) has no
        # --basetemp; its default lives under the TMPDIR checked above.
        ("--basetemp", config.option.basetemp or tempfile.gettempdir()),
    ):
        if not value or not _inside(value, root):
            problems.append(f"{what} is {str(value)!r}, outside the worker dir {root}")
    if problems:
        raise pytest.UsageError(
            "worker isolation is not in place (Q2W_WORKER="
            f"{os.environ.get('Q2W_WORKER')!r}):\n  " + "\n  ".join(problems)
        )


@pytest.fixture(scope="session")
def inside_worker():
    """`inside_worker(path, what)`: fail if a worker wrote outside its own directory.

    A no-op for a process the runner did not start.
    """
    root = _worker_dir()

    def check(path: object, what: str) -> None:
        if root is not None and path and not _inside(path, root):
            pytest.fail(
                f"{what} is {str(path)!r}, outside this worker's directory "
                f"{root}: two workers could share it",
                pytrace=False,
            )

    return check


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Record where this worker actually put things (isolation evidence)."""
    root = _worker_dir()
    if root is None:
        return
    settings_file = ""
    core = sys.modules.get("qgis.PyQt.QtCore")
    if core is not None:
        try:
            settings_file = core.QSettings().fileName()
        except RuntimeError:
            settings_file = ""
    record = {
        "worker": os.environ.get("Q2W_WORKER"),
        "pid": os.getpid(),
        "home": str(Path.home()),
        "tmpdir": tempfile.gettempdir(),
        "basetemp": str(session.config.option.basetemp or ""),
        "qgis_profile": os.environ.get("QGIS_CUSTOM_CONFIG_PATH", ""),
        "save_root": os.environ.get("QGIS2WEBMAP_SAVE_ROOT", ""),
        "qsettings_file": settings_file,
    }
    record["all_inside_worker_dir"] = all(
        _inside(value, root)
        for key, value in record.items()
        if key.endswith(("home", "tmpdir", "basetemp", "profile", "root", "file"))
        and value
    )
    (root / "isolation.json").write_text(json.dumps(record, indent=1))


@pytest.fixture(scope="session")
def qgis_app(tmp_path_factory):
    """A headless QGIS application, started once per test session.

    GUI enabled so widget tests can construct real dialogs; the tiers set
    `QT_QPA_PLATFORM=offscreen` to keep that headless. Its profile, settings
    and default save folder are in the session's temporary directory
    (`tests/hermetic.py`), never the developer's own.
    """
    qgis_core = pytest.importorskip(
        "qgis.core", reason="PyQGIS is unavailable; skipping the QGIS test tiers"
    )
    from hermetic import start_qgis

    app = start_qgis(tmp_path_factory.mktemp("qgis"))
    yield app

    # Destroy every widget still alive before shutting QGIS down.
    #
    # A dialog that was closed but not deleted keeps its C++ object, and Python
    # frees that during interpreter teardown - after `exitQgis` has torn down the
    # provider and symbol registries the widget's children reach into. The result
    # is a segfault *after* the last test passes, so the suite reports success
    # and then dies with exit 139.
    #
    # It surfaced only when `tests/qgis/test_dialog.py` and `tests/fixtures` ran
    # in one process, which is how CI runs them; either alone left few enough
    # widgets to get away with it.
    from qgis.PyQt.QtWidgets import QApplication

    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, 0)
    QApplication.processEvents()

    qgis_core.QgsProject.instance().clear()
    qgis_core.QgsApplication.exitQgis()


@pytest.fixture
def project(qgis_app):
    """An empty project, cleared before and after each test."""
    from qgis.core import QgsProject

    instance = QgsProject.instance()
    instance.clear()
    yield instance
    instance.clear()


@pytest.fixture(autouse=True)
def release_widgets_made_by_the_test(request):
    """Delete the top-level widgets a test left behind, as soon as it ends.

    A dialog that is only closed (or dropped) keeps its C++ object, its
    children and everything they cache until the process exits. The Cartograph
    dialog tests build roughly a hundred and sixty `MainDialog`s, about 30 MB
    each, so one process holding them passed 3.8 GB and had to be split into
    batches. Deleting what the test created, before the next one starts, keeps
    the whole suite in one process at under a gigabyte.

    Only widgets that did not exist when the test began are touched, and only
    when QGIS has been started for this test.
    """
    if "qgis_app" not in request.fixturenames:
        yield
        return
    from qgis.PyQt.QtCore import QEvent
    from qgis.PyQt.QtWidgets import QApplication

    before = {id(widget) for widget in QApplication.topLevelWidgets()}
    yield
    # Let what the test left pending run while its widgets still exist.
    QApplication.processEvents()
    for widget in QApplication.topLevelWidgets():
        if id(widget) not in before:
            # A dialog holding a close for an unsaved cartograph: close it now.
            getattr(widget, "close_without_saving", widget.close)()
            widget.deleteLater()
    QApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
    QApplication.processEvents()
    gc.collect()


# How long a modal dialog may wait for input before the test is failed.
MODAL_LIMIT_S = 20


@pytest.fixture(autouse=True)
def fail_a_dialog_left_waiting(request):
    """Close a modal dialog nobody answers, and fail the test that opened it.

    A test that opens a real dialog - a file chooser it forgot to stub, or a
    chooser whose simulated click missed - waits in `exec()` for input that
    never comes. Offscreen, that is a silent hang which holds the shared test
    slot until someone kills the run. A timer still fires inside `exec()`'s
    event loop, so it can reject the dialog: the test then fails within
    seconds, naming the dialog, and the rest of the run carries on.
    """
    if "qgis_app" not in request.fixturenames:
        yield
        return
    import time

    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QApplication, QDialog

    seen: dict[int, float] = {}
    left_waiting: list[str] = []

    def check() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        first = seen.setdefault(id(dialog), time.monotonic())
        if time.monotonic() - first < MODAL_LIMIT_S:
            return
        left_waiting.append(f"{type(dialog).__name__} {dialog.windowTitle()!r}")
        if isinstance(dialog, QDialog):
            dialog.reject()
        else:
            dialog.close()

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(check)
    timer.start()
    yield
    timer.stop()
    if left_waiting:
        pytest.fail(
            f"a modal dialog waited over {MODAL_LIMIT_S} s for input and was "
            f"closed: {', '.join(left_waiting)}",
            pytrace=False,
        )
