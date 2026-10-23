"""Tab follows the screen, no invisible focus stops, and nothing overflows.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QSettings, Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QImage, QTextTable
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QAbstractScrollArea, QPushButton, QScrollArea

from nika_onlymap_exporter.core.settings import DialogOutput


class _Iface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


@pytest.fixture
def dialog(qgis_app, project, make_memory_layer, monkeypatch):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: True)
    for name in ("roads", "rivers"):
        project.addMapLayer(make_memory_layer(name, features=[("a", [1.0, 2.0])]))
    dlg = MainDialog(_Iface(), None)
    dlg.resize(720, 320)
    dlg.show()
    qgis_app.processEvents()
    yield dlg
    dlg.close()


def _stops(dlg):
    """What Tab would visit, in order: visible, enabled, takes Tab, and not
    a proxy that hands the focus on."""
    stops = []
    widget = dlg.nextInFocusChain()
    while widget is not None and widget is not dlg:
        policy = widget.focusPolicy()
        if (
            widget.isVisible()
            and widget.isEnabled()
            and int(getattr(policy, "value", policy)) & 1
            and widget.focusProxy() is None
        ):
            stops.append(widget)
        widget = widget.nextInFocusChain()
    return stops


def _index(stops, widget):
    return stops.index(widget)


def _show_output(dlg, qgis_app, output):
    dlg._apply_output(output, persist=False)
    qgis_app.processEvents()


class TestTabOrder:
    def test_web_map_header_then_tabs_then_close(self, qgis_app, dialog) -> None:
        _show_output(dialog, qgis_app, DialogOutput.WEB_MAP)
        stops = _stops(dialog)
        header = [
            dialog.output_switcher,
            dialog.preview_button,
            dialog.live_switch,
            dialog.share_button,
            dialog.export_button,
            dialog.settings_button,
            dialog.help_button,
        ]
        order = [_index(stops, w) for w in header]
        assert order == sorted(order)
        tab_bar = dialog.tabs.tabBar()
        assert order[-1] < _index(stops, tab_bar)
        assert _index(stops, tab_bar) < _index(stops, dialog.name_edit)
        assert stops[-1] is dialog.close_button
        check = next(
            w for w in dialog.fidelity_strip.findChildren(QPushButton) if w in stops
        )
        assert _index(stops, dialog.name_edit) < _index(stops, check)
        assert _index(stops, check) < _index(stops, dialog.close_button)

    def test_cartograph_header_then_panel_then_close(self, qgis_app, dialog) -> None:
        _show_output(dialog, qgis_app, DialogOutput.CARTOGRAPH)
        stops = _stops(dialog)
        corner = dialog.active_actions()
        buttons = [
            b for b in corner.findChildren(type(dialog.settings_button)) if b in stops
        ]
        assert buttons
        order = [_index(stops, w) for w in [dialog.output_switcher, *buttons]]
        assert order == sorted(order)
        assert _index(stops, buttons[-1]) < _index(stops, dialog.settings_button)
        assert _index(stops, dialog.settings_button) < _index(stops, dialog.help_button)
        # Everything that is not header chrome comes after Help.
        header_set = {
            dialog.output_switcher,
            dialog.settings_button,
            dialog.help_button,
        }
        for stop in stops:
            if stop not in header_set and stop not in buttons:
                assert _index(stops, stop) > _index(stops, dialog.help_button)
        assert stops[-1] is dialog.close_button

    def test_aside_back_arrow_comes_first(self, qgis_app, dialog) -> None:
        dialog.help_button.click()
        qgis_app.processEvents()
        stops = _stops(dialog)
        assert _index(stops, dialog.aside_back) < _index(stops, dialog.help_browser)
        assert stops[-1] is dialog.close_button

    @pytest.mark.parametrize("output", list(DialogOutput))
    def test_no_scroll_area_is_a_tab_stop(self, qgis_app, dialog, output) -> None:
        _show_output(dialog, qgis_app, output)
        for stop in _stops(dialog):
            assert not isinstance(stop, QScrollArea), stop

    def test_a_tree_tabbed_into_shows_a_current_row(self, qgis_app, dialog) -> None:
        dialog.tabs.setCurrentWidget(dialog._layers_scroll)
        qgis_app.processEvents()
        tree = dialog.layer_tree
        assert not tree.currentIndex().isValid()
        tree.setFocus(Qt.FocusReason.TabFocusReason)
        qgis_app.processEvents()
        assert tree.currentIndex().isValid()


class TestHelpKeyboard:
    def _open(self, dlg, qgis_app):
        dlg.help_button.click()
        dlg.help_browser.setFocus(Qt.FocusReason.TabFocusReason)
        qgis_app.processEvents()

    def test_one_tab_leaves_the_browser(self, qgis_app, dialog) -> None:
        self._open(dialog, qgis_app)
        browser = dialog.help_browser
        assert browser.hasFocus()
        QTest.keyClick(browser, Qt.Key.Key_Tab)
        assert not browser.hasFocus()
        assert dialog.focusWidget() is not None

    def test_the_browser_still_takes_focus_to_scroll(self, qgis_app, dialog) -> None:
        self._open(dialog, qgis_app)
        assert dialog.help_browser.focusPolicy() != Qt.FocusPolicy.NoFocus

    def test_links_are_reachable_with_ctrl_tab(self, qgis_app, dialog) -> None:
        self._open(dialog, qgis_app)
        browser = dialog.help_browser
        clicked: list[QUrl] = []
        browser.anchorClicked.connect(clicked.append)
        QTest.keyClick(browser, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
        assert browser.hasFocus()
        QTest.keyClick(browser, Qt.Key.Key_Return)
        assert len(clicked) == 1

    def test_no_sideways_scrollbar_at_720(self, qgis_app, dialog) -> None:
        self._open(dialog, qgis_app)
        assert not dialog.help_browser.horizontalScrollBar().isVisible()

    def test_tables_are_fitted_to_the_page(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.main_dialog import render_help_document

        document = render_help_document()
        tables = []

        def walk(frame):
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    tables.append(child)
                walk(child)

        walk(document.rootFrame())
        assert tables
        for table in tables:
            width = table.format().width()
            assert width.type() == width.Type.PercentageLength
            assert width.rawValue() == 100
        # Narrower than any dialog: tables wrap instead of setting the width.
        document.setTextWidth(300)
        layout = document.documentLayout()
        for table in tables:
            assert layout.frameBoundingRect(table).width() <= 300


class TestOverflow:
    @pytest.mark.parametrize("output", list(DialogOutput))
    def test_layers_tree_has_no_horizontal_scrollbar(
        self, qgis_app, dialog, output
    ) -> None:
        _show_output(dialog, qgis_app, output)
        if output is DialogOutput.WEB_MAP:
            dialog.tabs.setCurrentWidget(dialog._layers_scroll)
        qgis_app.processEvents()
        tree = dialog.layer_tree
        tree.expandAll()
        qgis_app.processEvents()
        assert not tree.horizontalScrollBar().isVisible()
        assert isinstance(tree, QAbstractScrollArea)

    def test_info_button_says_what_it_is_when_stacked(self, qgis_app, dialog) -> None:
        dialog._set_layers_compact(True)
        assert dialog.layers_info_button.text()
        dialog._set_layers_compact(False)
        assert not dialog.layers_info_button.text()


def test_the_cartograph_layers_tree_fits_its_panel(qgis_app, project, tmp_path) -> None:
    """With a real layout and its layers listed, in the narrow panel, at the
    dialog's minimum size and a common one."""
    from atlas_fixtures import build_atlas_layout
    from test_cartograph_dialog import _choose_cartograph
    from test_cartograph_dialog import _dialog as cartograph_dialog

    from nika_onlymap_exporter.ui.main_dialog import (
        MINIMUM_DIALOG_HEIGHT,
        MINIMUM_DIALOG_WIDTH,
    )

    built = build_atlas_layout(project)
    project.layoutManager().addLayout(built.layout)
    project.setFileName(str(tmp_path / "parcels.qgz"))
    dialog = cartograph_dialog(project)
    try:
        _choose_cartograph(dialog)
        cartograph = dialog._cartograph
        cartograph.layout_combo.setCurrentText(built.layout.name())
        dialog.show()
        cartograph.layers_section.set_open(True)
        tree = dialog.layer_tree
        assert tree.topLevelItemCount() == 2
        for size in ((MINIMUM_DIALOG_WIDTH, MINIMUM_DIALOG_HEIGHT), (1152, 768)):
            dialog.resize(*size)
            qgis_app.processEvents()
            assert tree.isVisible()
            assert tree.horizontalScrollBar().maximum() == 0, size
    finally:
        dialog.close()
        project.setFileName("")


class TestTemplatePickerKeyboard:
    def test_opens_with_a_current_card_and_the_list_focused(
        self, qgis_app, tmp_path
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker

        def render(
            template_id, page_size, orientation, width_px, figures=(), sizes=None
        ):
            image = QImage(width_px, width_px // 2, QImage.Format.Format_ARGB32)
            image.fill(QColor("#336699"))
            return image

        settings = QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat)
        picker = TemplatePicker(render, qsettings=settings)
        try:
            # Nothing chosen, as if nothing could be restored.
            picker.template_list.setCurrentItem(None)
            picker.show()
            qgis_app.processEvents()
            assert picker.template_list.currentItem() is not None
            assert picker.template_list.hasFocus()
        finally:
            picker.close()
