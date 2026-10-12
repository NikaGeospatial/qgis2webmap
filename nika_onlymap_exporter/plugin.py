"""Plugin controller -- QGIS lifecycle only.

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

from .ui.i18n import N_

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface

LOG_TAG = "QGIS2WebMap"
MENU_TITLE = "&QGIS2WebMap by NIKA"
# Marked for the catalogue here, translated in `initGui`.
ACTION_TEXT = N_("Create web map")
# Opens the dialog. Cmd+Alt+W on macOS, where Qt maps Ctrl to Cmd. Not Ctrl+M:
# QGIS binds that to New Map View (and macOS to Minimize), Ctrl+Shift+M to
# Measure Line and Ctrl+Alt+M to New 3D Map View.
DEFAULT_SHORTCUT = "Ctrl+Alt+W"


class Qgis2WebMapPlugin:
    """Registers the plugin's entry points and owns the export dialog."""

    def __init__(self, iface: QgisInterface) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action: QAction | None = None
        self.dialog = None
        self.provider = None
        # Before anything shows text: the menu entry is created in `initGui`.
        # English unless a language pack is chosen, or QGIS speaks one that is
        # installed; see `ui.languages`.
        try:
            from qgis.PyQt.QtCore import QSettings

            from .ui import languages
            from .ui.i18n import qgis_locale, use_language

            setting = QSettings().value(languages.SETTING_KEY, languages.AUTO, type=str)
            use_language(languages.effective(setting, qgis_locale()))
        except Exception:
            QgsMessageLog.logMessage(
                f"Could not load a translation, staying in English:\n"
                f"{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Warning,
            )

    # ---- QGIS lifecycle -------------------------------------------------

    def initProcessing(self) -> None:  # noqa: N802 -- QGIS plugin API
        """Register the Processing provider.

        Wrapped like `run`: a Processing failure must not stop the menu action
        from working, and must never take QGIS down with it.
        """
        try:
            from qgis.core import QgsApplication

            from .processing.provider import Qgis2WebMapProvider

            self.provider = Qgis2WebMapProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)
        except Exception:
            self.provider = None
            QgsMessageLog.logMessage(
                f"Failed to register the Processing provider:\n"
                f"{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Warning,
            )

    def initGui(self) -> None:  # noqa: N802 -- QGIS plugin API
        # QGIS registers 3D renderers lazily - normally when a 3D view is
        # first opened. A project loaded before that parses its 3D renderer
        # XML to nothing, so an export made in a session that never opened a
        # 3D view silently lost every extrusion: the flagship 2.5D feature
        # produced flat maps. Initialising here, at plugin load, means any
        # project opened afterwards keeps its 3D renderers. Guarded because
        # a QGIS built without 3D support has no `qgis._3d` at all.
        try:
            from qgis._3d import Qgs3D

            Qgs3D.initialize()
        except Exception:  # pragma: no cover - build without 3D support
            # Swallowed deliberately: a QGIS without 3D support is a supported
            # configuration, not an error, and the plugin must still load. It
            # is logged rather than passed over because this is the only clue
            # to why an export came out flat, and at Info because for those
            # builds it is expected rather than a warning about a fault.
            QgsMessageLog.logMessage(
                f"3D support is unavailable, so extrusions will not be "
                f"exported:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Info,
            )

        self.initProcessing()
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "qgis2webmap.svg"))
        from .ui.i18n import tr

        self.action = QAction(icon, tr(ACTION_TEXT), self.iface.mainWindow())
        self.action.setObjectName("qgis2webmapCreateWebMap")
        self.action.setWhatsThis(
            tr("Export the current QGIS project to a portable OnlyMap web map")
        )
        self.action.triggered.connect(self.run)

        # One Web-menu entry and one toolbar button -- nothing else.
        self.iface.addPluginToWebMenu(MENU_TITLE, self.action)
        self.iface.addToolBarIcon(self.action)
        # Through QGIS's shortcut manager rather than `setShortcut`, so it is
        # listed in Settings > Keyboard Shortcuts, where it can be changed or
        # cleared - and a change made there wins over this default.
        self.iface.registerMainWindowAction(self.action, DEFAULT_SHORTCUT)

    def unload(self) -> None:
        """Remove everything `initGui` added. Must be idempotent-safe."""
        if self.provider is not None:
            from qgis.core import QgsApplication

            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

        if self.action is not None:
            self.iface.unregisterMainWindowAction(self.action)
            self.iface.removePluginWebMenu(MENU_TITLE, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

        from .ui.i18n import use_language

        use_language("en")

    # ---- Entry point ----------------------------------------------------

    def run(self) -> None:
        """Open the export dialog.

        Wrapped so an unexpected error surfaces as a message and a log entry
        rather than a traceback dialog -- and never as a closed QGIS.
        """
        try:
            from .ui.main_dialog import MainDialog

            if self.dialog is None:
                self.dialog = MainDialog(self.iface, self.iface.mainWindow())
                # Non-modal: QGIS stays usable behind the dialog, so a user can
                # reorder layers or restyle while it is open.
                self.dialog.finished.connect(self._on_dialog_finished)
                self.dialog.languageChanged.connect(self._reopen_dialog)

            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
        except Exception as exc:
            QgsMessageLog.logMessage(
                f"Failed to open the export dialog:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Critical,
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

    def _reopen_dialog(self) -> None:
        """Rebuild the dialog in the language just chosen.

        Every label is set once, when the dialog is built, so a new language
        needs a new dialog. Closing saves the settings, and the next one reads
        them back, so the user loses nothing but a blink.
        """
        from qgis.PyQt.QtCore import QSettings, QTimer

        from .ui import languages
        from .ui.i18n import qgis_locale, use_language

        if self.dialog is not None:
            self.dialog.close()
        # The language is applied here, not only at plugin load: the dialog
        # stores the choice and asks to be rebuilt, and rebuilding without
        # switching the catalogue first just draws the old language again.
        setting = QSettings().value(languages.SETTING_KEY, languages.AUTO, type=str)
        use_language(languages.effective(setting, qgis_locale()))
        QTimer.singleShot(0, self.run)

    def _on_dialog_finished(self, _result: int) -> None:
        """Drop the reference so the next run rebuilds against a fresh project."""
        if self.dialog is not None:
            self.dialog.deleteLater()
            self.dialog = None
