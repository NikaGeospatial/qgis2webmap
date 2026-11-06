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
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .ui.i18n import N_

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.gui import QgisInterface, QgsLayoutDesignerInterface

    from .processing.provider import Qgis2WebMapProvider

LOG_TAG = "QGIS2WebMap"
MENU_TITLE = "&QGIS2WebMap by NIKA"
# Marked for the catalogue here, translated in `initGui`.
ACTION_TEXT = N_("Create web map")
# Opens the dialog. Cmd+Alt+W on macOS, where Qt maps Ctrl to Cmd. Not Ctrl+M:
# QGIS binds that to New Map View (and macOS to Minimize), Ctrl+Shift+M to
# Measure Line and Ctrl+Alt+M to New 3D Map View.
DEFAULT_SHORTCUT = "Ctrl+Alt+W"
# Opens the dialog on Cartograph (Beta). Same family as the web map's; QGIS
# 4.2 binds nothing to it. On layouts where AltGr+C types a letter (AltGr is
# Ctrl+Alt on Windows) it can be changed in Settings > Keyboard Shortcuts.
CARTOGRAPH_SHORTCUT = "Ctrl+Alt+C"

# The Cartograph (Beta) output's own Web-menu entry, beside the web map one.
CARTOGRAPH_ACTION_TEXT = N_("Create cartograph...")

# How long unloading waits, in total, for workers still running to notice they
# were cancelled. Bounded: unloading is QGIS shutting down or reloading.
UNLOAD_JOB_WAIT_MS = 3000
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
        self.provider: Qgis2WebMapProvider | None = None
        # One "Export as cartograph..." action per open layout designer,
        # keyed by the designer itself - a project can have several open at
        # once, each needing its own action naming its own layout.
        self._designer_actions: dict[QgsLayoutDesignerInterface, QAction] = {}
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
        # A wheel turn over a closed dropdown in the plugin's windows scrolls
        # the panel instead of changing the dropdown.
        from .ui.wheel_guard import install_wheel_guard

        install_wheel_guard()

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

        # Cartograph (Beta)'s own Web-menu entry and shortcut - no toolbar
        # icon, so it does not compete with the web map's for one while it is
        # still a Beta.
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
        self.iface.registerMainWindowAction(self.cartograph_action, CARTOGRAPH_SHORTCUT)

        # Print users find the dialog from inside the layout designer, where
        # they already are - per the plan's "Ways in". One action per
        # designer window, since each names the layout it is open on.
        #
        # Wrapped like `initProcessing`: an `iface` too minimal to carry this
        # signal (a test double) must still let the rest of the plugin load,
        # rather than taking `initGui` down with it.
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
        from .ui.wheel_guard import remove_wheel_guard

        remove_wheel_guard()

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
            self.iface.unregisterMainWindowAction(self.cartograph_action)
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

        # Taken off `self` first: closing an open dialog emits `finished`,
        # whose handler also drops `self.dialog`, and the delete below then
        # ran on None - an unload with the dialog open raised.
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            # Issue #4: closing the Colour vision dialog keeps the canvas
            # preview the user chose; unloading the plugin must not leave it.
            with contextlib.suppress(Exception):
                dialog.release_canvas_effects()
            dialog.close_for_unload()
            dialog.deleteLater()

        # A worker that outlived the dialog is stopped and given a moment to end.
        # One that still runs after that is not destroyed and not waited for: the
        # job module keeps it owned until it finishes, with its results dropped,
        # so it calls nothing in this (about to be unloaded) plugin.
        from .ui.background_job import release_all

        release_all(UNLOAD_JOB_WAIT_MS)

        from .ui.i18n import use_language

        use_language("en")

    # ---- Entry point ----------------------------------------------------

    def run(self) -> None:
        """Open the export dialog, on the web map.

        Switched there even when the project was last saved on Cartograph:
        the action is called "Create web map", and Ctrl+Alt+C is the way in
        to the other one.
        """
        dialog = self._open_dialog()
        if dialog is not None:
            dialog.open_on_web_map()

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
            else:
                # Asked for while closing: it stays open.
                self.dialog.keep_open()

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

        The close may be held back while the cartograph saves its last changes,
        so the rebuild happens once the dialog has actually finished
        (`_on_dialog_finished`), not when it was asked to close: opening the
        new one beside a still-saving old one would have two writing the same
        file. If the user cancels the close, nothing is rebuilt.
        """
        if self.dialog is not None:
            self.dialog.reopen_after_close = True
            self.dialog.close()
            return
        self._apply_chosen_language()
        QTimer.singleShot(0, self.run)

    @staticmethod
    def _apply_chosen_language() -> None:
        from qgis.PyQt.QtCore import QSettings

        from .ui import languages
        from .ui.i18n import qgis_locale, use_language

        # The language is applied here, not only at plugin load: the dialog
        # stores the choice and asks to be rebuilt, and rebuilding without
        # switching the catalogue first just draws the old language again.
        setting = QSettings().value(languages.SETTING_KEY, languages.AUTO, type=str)
        use_language(languages.effective(setting, qgis_locale()))

    def _on_dialog_finished(self, _result: int) -> None:
        """Drop the reference so the next run rebuilds against a fresh project,
        and rebuild right away if the dialog was closed to change language."""
        if self.dialog is not None:
            reopen = self.dialog.reopen_after_close
            self.dialog.deleteLater()
            self.dialog = None
            if reopen:
                self._apply_chosen_language()
                QTimer.singleShot(0, self.run)
