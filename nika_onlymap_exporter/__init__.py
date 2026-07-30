"""QGIS2WebMap by NIKA -- QGIS plugin entry point.

Kept deliberately thin: QGIS calls `classFactory` on load and expects a single
controller object back. Everything else lives in `plugin.py` so that importing
this module has no side effects beyond the import of that controller.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


def classFactory(iface):  # noqa: N802 -- name fixed by the QGIS plugin API
    """Return the plugin controller. Called once by QGIS at plugin load.

    :param iface: QgisInterface handed to us by QGIS.
    """
    from .plugin import Qgis2WebMapPlugin

    return Qgis2WebMapPlugin(iface)
