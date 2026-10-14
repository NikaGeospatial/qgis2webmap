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

import contextlib
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

# The Cartograph (Beta) output's own Web-menu entry, beside the web map one.
CARTOGRAPH_ACTION_TEXT = N_("Create cartograph...")
# Added to the layout designer's own Layout menu, per project - one QGIS
# print layout is open there already, so this skips the Source picker.
DESIGNER_ACTION_TEXT = N_("Export as cartograph (HTML)...")


class Qgis2WebMapPlugin:
    """Registers the plugin's entry points and owns the export dialog."""

    def __init__(self, iface: QgisInterface) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action: QAction | None = None
        self.cartograph_action: QAction | None = None
        self.dialog = None
        self.provider = None
        # One "Export as cartograph..." action per open layout designer,
        # keyed by the designer itself - a project can have several open at
        # once, each needing its own action naming its own layout.
        self._designer_actions: dict[object, QAction] = {}
        self._designer_opened_connected = False
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

        # Cartograph (Beta)'s own Web-menu entry - no toolbar icon and no
        # default shortcut, so it does not compete with the web map's for
        # either while it is still a Beta.
        self.cartograph_action = QAction(
            icon, tr(CARTOGRAPH_ACTION_TEXT), self.iface.mainWindow()
        )
        self.cartograph_action.setObjectName("qgis2webmapCreateCartograph")
        self.cartograph_action.setWhatsThis(
            tr("Export the current QGIS print layout to a portable cartograph (Beta)")
        )
        # Not `connect(self.run_cartograph)`: `triggered` passes `checked`,
        # which would arrive as the layout name.
        self.cartograph_action.triggered.connect(lambda: self.run_cartograph())
        self.iface.addPluginToWebMenu(MENU_TITLE, self.cartograph_action)

        # Print users find the dialog from inside the layout designer, where
        # they already are - per the plan's "Ways in". One action per
        # designer window, since each names the layout it is open on.
        #
        # Wrapped like `initProcessing`: an `iface` too minimal to carry this
        # signal (an older QGIS, or a test double) must still let the rest of
        # the plugin load, rather than taking `initGui` down with it.
        try:
            self.iface.layoutDesignerOpened.connect(self._on_layout_designer_opened)
            self.iface.layoutDesignerWillBeClosed.connect(
                self._on_layout_designer_closed
            )
            self._designer_opened_connected = True
        except Exception:
            QgsMessageLog.logMessage(
                f'Could not hook the layout designer; "{tr(DESIGNER_ACTION_TEXT)}" '
                f"will not appear there:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Warning,
            )

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

        if self.cartograph_action is not None:
            self.iface.removePluginWebMenu(MENU_TITLE, self.cartograph_action)
            self.cartograph_action.deleteLater()
            self.cartograph_action = None

        if self._designer_opened_connected:
            self.iface.layoutDesignerOpened.disconnect(self._on_layout_designer_opened)
            self.iface.layoutDesignerWillBeClosed.disconnect(
                self._on_layout_designer_closed
            )
            self._designer_opened_connected = False

        for designer, action in list(self._designer_actions.items()):
            # pragma: no cover - the designer may already be gone
            with contextlib.suppress(Exception):
                designer.layoutMenu().removeAction(action)
            action.deleteLater()
        self._designer_actions.clear()

        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

        from .ui.i18n import use_language

        use_language("en")

    # ---- Entry point ----------------------------------------------------

    def run(self) -> None:
        """Open the export dialog, on the web map."""
        self._open_dialog()

    def run_cartograph(self, layout_name: str | None = None) -> None:
        """Open the export dialog, switched to Cartograph (Beta).

        `layout_name` comes from the layout designer action - opened with the
        layout that was already on screen selected, so the Source tab needs
        no further choice. The Web-menu action calls this with `None`, which
        leaves whichever layout the Cartograph tab last had selected.
        """
        dialog = self._open_dialog()
        if dialog is not None:
            dialog.open_on_cartograph(layout_name)

    def _open_dialog(self):
        """Open or raise the one dialog, building it if needed.

        Wrapped so an unexpected error surfaces as a message and a log entry
        rather than a traceback dialog -- and never as a closed QGIS. Returns
        the dialog, or `None` if it could not be built.
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
            return self.dialog
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
            return None

    def _on_layout_designer_closed(self, designer) -> None:
        """Drop the action this designer's `layoutMenu()` owns and is about
        to destroy along with the rest of its window - just the bookkeeping,
        so `_designer_actions` cannot grow across a long QGIS session."""
        self._designer_actions.pop(designer, None)

    @staticmethod
    def _designer_layout_name(designer) -> str | None:
        layout = designer.layout()
        return layout.name() if layout is not None else None

    def _on_layout_designer_opened(self, designer) -> None:
        """Add "Export as cartograph (HTML)..." to this designer's Layout menu.

        Wrapped the same as `_open_dialog`: a designer the plugin cannot read
        the layout name from must still open normally, so failure here is a
        log entry, never a dialog and never a dead designer window.
        """
        try:
            from .ui.i18n import tr

            action = QAction(tr(DESIGNER_ACTION_TEXT), designer.layoutMenu())
            action.setObjectName("qgis2webmapExportAsCartograph")
            # The name is read when pressed, not now: the layout can be
            # renamed while its designer stays open.
            action.triggered.connect(
                lambda: self.run_cartograph(self._designer_layout_name(designer))
            )
            designer.layoutMenu().addAction(action)
            self._designer_actions[designer] = action
        except Exception:
            QgsMessageLog.logMessage(
                f"Could not add the cartograph action to the layout "
                f"designer:\n{traceback.format_exc()}",
                LOG_TAG,
                level=Qgis.MessageLevel.Warning,
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
