"""The plugin opens from a default keyboard shortcut that the user can change.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")


class _Iface:
    """Registers actions the way QGIS's own interface does: straight through
    to the shortcuts manager (`QgisAppInterface::registerMainWindowAction`)."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None

    def registerMainWindowAction(self, action, shortcut):  # noqa: N802
        from qgis.gui import QgsGui

        return QgsGui.shortcutsManager().registerAction(action, shortcut)

    def unregisterMainWindowAction(self, action):  # noqa: N802
        from qgis.gui import QgsGui

        return QgsGui.shortcutsManager().unregisterAction(action)


@pytest.fixture
def plugin(qgis_app):
    import nika_onlymap_exporter

    loaded = nika_onlymap_exporter.classFactory(_Iface())
    loaded.initGui()
    yield loaded
    loaded.unload()


class TestShortcut:
    def test_the_dialog_opens_on_ctrl_alt_w(self, plugin):
        from qgis.gui import QgsGui
        from qgis.PyQt.QtGui import QKeySequence

        assert plugin.action.shortcut() == QKeySequence("Ctrl+Alt+W")
        manager = QgsGui.shortcutsManager()
        assert manager.actionByName("qgis2webmapCreateWebMap") is plugin.action
        assert manager.defaultKeySequence(plugin.action) == "Ctrl+Alt+W"

    def test_a_shortcut_the_user_chose_wins(self, qgis_app):
        from qgis.gui import QgsGui
        from qgis.PyQt.QtGui import QKeySequence

        import nika_onlymap_exporter

        manager = QgsGui.shortcutsManager()
        loaded = nika_onlymap_exporter.classFactory(_Iface())
        loaded.initGui()
        try:
            manager.setKeySequence(loaded.action, "Ctrl+Shift+F9")
        finally:
            loaded.unload()

        again = nika_onlymap_exporter.classFactory(_Iface())
        again.initGui()
        try:
            assert again.action.shortcut() == QKeySequence("Ctrl+Shift+F9")
        finally:
            manager.setKeySequence(again.action, "Ctrl+Alt+W")
            again.unload()

    def test_unloading_releases_the_shortcut(self, plugin):
        from qgis.gui import QgsGui

        plugin.unload()
        assert QgsGui.shortcutsManager().actionByName("qgis2webmapCreateWebMap") is None
