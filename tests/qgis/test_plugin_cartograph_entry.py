"""The cartograph's ways in: the Web-menu action and the layout designer's.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QMenu  # noqa: E402


class _Iface(QObject):
    """Carries the two designer signals as real Qt signals, unlike a bare
    stand-in whose `connect` would fail and skip the hook altogether."""

    layoutDesignerOpened = pyqtSignal(object)  # noqa: N815 - QGIS's name
    layoutDesignerWillBeClosed = pyqtSignal(object)  # noqa: N815 - QGIS's name

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


class _Designer:
    def __init__(self, layout) -> None:
        self._layout = layout
        self._menu = QMenu()

    def layout(self):
        return self._layout

    def layoutMenu(self):  # noqa: N802 - mirrors QgsLayoutDesignerInterface
        return self._menu


@pytest.fixture
def plugin_and_iface(qgis_app, monkeypatch):
    import nika_onlymap_exporter

    iface = _Iface()
    loaded = nika_onlymap_exporter.classFactory(iface)
    loaded.initGui()
    opened: list[object] = []
    monkeypatch.setattr(loaded, "run_cartograph", lambda name=None: opened.append(name))
    yield loaded, iface, opened
    loaded.unload()


def _cartograph_action(designer):
    return next(
        action
        for action in designer.layoutMenu().actions()
        if action.objectName() == "qgis2webmapExportAsCartograph"
    )


def test_the_designer_gets_an_export_as_cartograph_action(
    plugin_and_iface, project
) -> None:
    from qgis.core import QgsPrintLayout

    _plugin, iface, opened = plugin_and_iface
    layout = QgsPrintLayout(project)
    layout.setName("Parcel map")
    designer = _Designer(layout)

    iface.layoutDesignerOpened.emit(designer)
    _cartograph_action(designer).trigger()

    assert opened == ["Parcel map"]


def test_the_designer_action_names_the_layout_as_it_is_now(
    plugin_and_iface, project
) -> None:
    """Renamed while its designer is open: the action must open the layout
    under its current name, which is the one the Source combo lists."""
    from qgis.core import QgsPrintLayout

    _plugin, iface, opened = plugin_and_iface
    layout = QgsPrintLayout(project)
    layout.setName("Draft")
    designer = _Designer(layout)
    iface.layoutDesignerOpened.emit(designer)

    layout.setName("Final")
    _cartograph_action(designer).trigger()

    assert opened == ["Final"]


def test_the_web_menu_action_opens_on_the_last_layout(plugin_and_iface) -> None:
    plugin, _iface, opened = plugin_and_iface
    plugin.cartograph_action.trigger()
    assert opened == [None]


def test_unloading_with_the_dialog_open_does_not_raise(qgis_app) -> None:
    """Closing an open dialog emits `finished`, which also clears the
    plugin's reference; unload used to delete through that cleared name."""
    import nika_onlymap_exporter

    iface = _Iface()
    plugin = nika_onlymap_exporter.classFactory(iface)
    plugin.initGui()
    plugin.run()
    assert plugin.dialog is not None and plugin.dialog.isVisible()
    plugin.unload()
    assert plugin.dialog is None


def test_create_web_map_opens_on_the_web_map(qgis_app, project) -> None:
    """Even for a project last saved on Cartograph, and with the dialog
    already open there: the action is called "Create web map"."""
    import nika_onlymap_exporter
    from nika_onlymap_exporter.core.settings import (
        DialogOutput,
        load_state,
        save_state,
    )

    state = load_state(project)
    state.output = DialogOutput.CARTOGRAPH
    save_state(project, state)
    plugin = nika_onlymap_exporter.classFactory(_Iface())
    plugin.initGui()
    try:
        plugin.run()
        assert plugin.dialog.state.output is DialogOutput.WEB_MAP
        assert plugin.dialog.output_switcher.text() == "Web map"
        plugin.run_cartograph()
        assert plugin.dialog.state.output is DialogOutput.CARTOGRAPH
        plugin.run()
        assert plugin.dialog.state.output is DialogOutput.WEB_MAP
    finally:
        plugin.unload()
