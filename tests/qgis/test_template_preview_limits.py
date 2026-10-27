"""Slow maps cannot monopolise the picker or leave an inspection spinning.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from test_template_picker import _Pending, _Renderer, _settings, _shown, _SlowRenderer

from nika_onlymap_exporter.ui.cartograph import (
    template_preview_dialog,
)
from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker
from nika_onlymap_exporter.ui.cartograph.template_preview_dialog import (
    TemplatePreviewDialog,
)


def test_closing_during_a_scroll_does_not_restart_map_rendering(qgis_app, tmp_path):
    renderer = _SlowRenderer(delay_ms=5000)
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))
    _shown(qgis_app, picker)
    picker._on_user_scroll()
    started = len(renderer.started)
    picker.reject()
    QTest.qWait(200)
    assert len(renderer.started) == started
    assert all(pending.cancelled for pending in renderer.pending)


def test_automatic_orientation_is_kept_per_card(qgis_app, tmp_path, monkeypatch):
    monkeypatch.setattr(TemplatePicker, "_render_next", lambda self: None)
    picker = TemplatePicker(_Renderer(), qsettings=_settings(tmp_path))
    try:
        _shown(qgis_app, picker)
        tall = _Renderer(tall=True)("classic", "A4", "auto", 360)
        wide = _Renderer()("legend-below", "A4", "auto", 360)
        picker._store_page("classic", picker._key("classic"), tall)
        before = picker.rows["classic"]._box
        assert before.height() > before.width()
        picker._store_page("legend-below", picker._key("legend-below"), wide)
        assert picker.rows["classic"]._box == before
        other = picker.rows["legend-below"]._box
        assert other.width() > other.height()
    finally:
        picker.reject()


def test_on_demand_preview_is_cached_without_starting_other_map_renders(
    qgis_app, tmp_path, monkeypatch
):
    monkeypatch.setattr(TemplatePicker, "_render_next", lambda self: None)
    renderer = _Renderer()
    picker = TemplatePicker(renderer, qsettings=_settings(tmp_path))

    def close_preview():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, TemplatePreviewDialog):
                assert widget.page.image is not None
                widget.reject()

    try:
        _shown(qgis_app, picker)
        assert not renderer.calls
        for _ in range(2):
            QTimer.singleShot(0, close_preview)
            picker.open_large_preview("locator")
        assert len(renderer.calls) == 1
        assert renderer.calls[0][0] == "locator"
        assert renderer.calls[0][3] >= 960
        assert picker.rows["locator"].drawn
        assert not picker.rows["classic"].drawn
        assert picker.isVisible()
    finally:
        picker.reject()


def test_inspection_timeout_can_retry_and_old_completion_cannot_replace_it(
    qgis_app, monkeypatch
):
    monkeypatch.setattr(template_preview_dialog, "PREVIEW_TIMEOUT_MS", 20)
    parent = TemplatePicker(_Renderer())
    first = _Pending(960, 5000)
    calls = []

    def render(width):
        calls.append(width)
        if len(calls) == 1:
            return first
        return _Renderer()("classic", "A4", "auto", width)

    window = TemplatePreviewDialog("Daylight", "", "A4", render, parent)
    try:
        window.show()
        window.start()
        QTest.qWait(100)
        assert first.cancelled
        assert window.page.image is None
        assert window.retry_button.isVisible()
        assert window.isVisible()
        window.retry_button.click()
        image = window.page.image
        assert image is not None
        first._done()
        assert window.page.image is image
        assert not window.retry_button.isVisible()
    finally:
        window.reject()
        parent.reject()
        window.deleteLater()
        parent.deleteLater()
        QTest.qWait(1)
