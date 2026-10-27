"""The request card is a link, never a selected or persisted layout design.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

import pytest
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from test_template_picker import _Renderer, _settings, _shown

from nika_onlymap_exporter.ui.cartograph import template_picker
from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker
from nika_onlymap_exporter.ui.links import TEMPLATE_REQUEST_URL


@pytest.mark.parametrize("key", [None, Qt.Key.Key_Return, Qt.Key.Key_Space])
def test_request_opens_form_without_selecting_or_accepting(
    qgis_app, tmp_path, monkeypatch, key
):
    opened = []
    monkeypatch.setattr(
        template_picker.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    settings = _settings(tmp_path)
    picker = TemplatePicker(_Renderer(), qsettings=settings)
    try:
        _shown(qgis_app, picker)
        picker.choose("locator")
        if key is None:
            QTest.mouseClick(picker.request_button, Qt.MouseButton.LeftButton)
        else:
            picker.request_button.setFocus()
            QTest.keyClick(picker.request_button, key)
        assert opened == [TEMPLATE_REQUEST_URL]
        assert picker.template_id == "locator"
        assert picker.isVisible()
        assert picker.result() == 0
        assert not settings.allKeys()
        last = picker.template_list.item(picker.template_list.count() - 1)
        assert last is picker.request_item
        assert list(picker.rows)[-2:] == ["dark", "night-desk"]
        assert picker.template_list.count() == 12
    finally:
        picker.reject()


def test_request_item_cannot_replace_the_selected_template(qgis_app, tmp_path):
    picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
    try:
        picker.choose("dark")
        picker.template_list.setCurrentItem(picker.request_item)
        assert picker.template_id == "dark"
        assert picker.template_list.currentItem() is picker._items["dark"]
    finally:
        picker.reject()
