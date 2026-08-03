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

import pytest


@pytest.fixture(scope="session")
def qgis_app():
    """A headless QGIS application, started once per test session.

    GUI enabled so widget tests can construct real dialogs; the tiers set
    `QT_QPA_PLATFORM=offscreen` to keep that headless.
    """
    qgis_core = pytest.importorskip(
        "qgis.core", reason="PyQGIS is unavailable; skipping the QGIS test tiers"
    )
    app = qgis_core.QgsApplication([], True)
    qgis_core.QgsApplication.initQgis()
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
