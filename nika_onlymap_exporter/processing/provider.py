"""The Processing provider.

One provider, one algorithm today. It exists so export is reachable from the
Processing toolbox, the graphical modeller and `qgis_process` on the command
line -- which is what makes batch export possible without a second
implementation of the export itself.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os.path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .export_project import ExportProjectAlgorithm

ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "icons", "qgis2webmap.svg"
)


class Qgis2WebMapProvider(QgsProcessingProvider):
    """Groups the plugin's algorithms in the Processing toolbox."""

    def id(self) -> str:
        return "qgis2webmap"

    def name(self) -> str:
        return "QGIS2WebMap by NIKA"

    def longName(self) -> str:  # noqa: N802
        return "QGIS2WebMap by NIKA - portable OnlyMap web maps"

    def icon(self) -> QIcon:
        return QIcon(ICON_PATH)

    def loadAlgorithms(self) -> None:  # noqa: N802
        self.addAlgorithm(ExportProjectAlgorithm())
