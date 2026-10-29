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

import pytest


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
