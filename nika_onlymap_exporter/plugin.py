"""Plugin controller — QGIS lifecycle only.

Deliberately small, mirroring the one pattern qgis2web gets right: a single
controller that registers exactly one Web-menu action plus a toolbar action, and
tears both down again on unload. All export logic lives behind the writer and
exporter contracts, never here.

Two rules this file exists to enforce:

1. **Never terminate the host application.** No `os._exit`, no `QApplication.quit`,
   no `sys.exit` on any path. A plugin failure must degrade to a message.
2. **Unload must be complete.** Every action added in `initGui` is removed in
   `unload`, so repeated enable/disable cycles leave no duplicate menu entries.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os.path
import traceback
from typing import TYPE_CHECKING

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface

LOG_TAG = "QGIS2WebMap"
MENU_TITLE = "&QGIS2WebMap by NIKA"
ACTION_TEXT = "Create web map"


class Qgis2WebMapPlugin:
    """Registers the plugin's entry points and owns the export dialog."""

    def __init__(self, iface: QgisInterface) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action: QAction | None = None
        self.dialog = None

    # ---- QGIS lifecycle -------------------------------------------------

    def initGui(self) -> None:  # noqa: N802 — QGIS plugin API
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "qgis2webmap.svg"))
        self.action = QAction(icon, ACTION_TEXT, self.iface.mainWindow())
        self.action.setObjectName("qgis2webmapCreateWebMap")
        self.action.setWhatsThis(
            "Export the current QGIS project to a portable OnlyMap web map"
        )
        self.action.triggered.connect(self.run)

        # One Web-menu entry and one toolbar button — nothing else.
        self.iface.addPluginToWebMenu(MENU_TITLE, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self) -> None:
        """Remove everything `initGui` added. Must be idempotent-safe."""
        if self.action is not None:
            self.iface.removePluginWebMenu(MENU_TITLE, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    # ---- Entry point ----------------------------------------------------

    def run(self) -> None:
        """Open the export dialog.

        Wrapped so an unexpected error surfaces as a message and a log entry
        rather than a traceback dialog — and never as a closed QGIS.
        """
        try:
            from .ui.main_dialog import MainDialog

            if self.dialog is None:
                self.dialog = MainDialog(self.iface, self.iface.mainWindow())
                # Non-modal: QGIS stays usable behind the dialog, so a user can
                # reorder layers or restyle while it is open.
                self.dialog.finished.connect(self._on_dialog_finished)

            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"Failed to open the export dialog:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.Critical,
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                "QGIS2WebMap by NIKA",
                "Could not open the export dialog.\n\n"
                f"{exc}\n\n"
                "Details are in the QGIS message log under "
                f'"{LOG_TAG}". Please report this at '
                "https://github.com/NikaGeospatial/qgis2webmap/issues",
            )

    def _on_dialog_finished(self, _result: int) -> None:
        """Drop the reference so the next run rebuilds against a fresh project."""
        if self.dialog is not None:
            self.dialog.deleteLater()
            self.dialog = None
