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
    qgis_core.QgsApplication.exitQgis()


@pytest.fixture
def project(qgis_app):
    """An empty project, cleared before and after each test."""
    from qgis.core import QgsProject

    instance = QgsProject.instance()
    instance.clear()
    yield instance
    instance.clear()
