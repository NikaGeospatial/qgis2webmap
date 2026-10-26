"""Inspect a template without creating a layout or persisting picker choices."""

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QApplication
from test_template_picker import _Pending, _Renderer, _settings, _shown

from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker
from nika_onlymap_exporter.ui.cartograph.template_preview_dialog import (
    TemplatePreviewDialog,
)


def test_inspection_draws_only_the_requested_large_page_and_saves_no_choice(
    qgis_app, tmp_path, project
):
    renderer = _Renderer()
    settings = _settings(tmp_path)
    picker = TemplatePicker(renderer, qsettings=settings, project=project)
    _shown(qgis_app, picker)
    variables = dict(project.customVariables())
    inspected = []

    def close_inspection():
        for window in QApplication.topLevelWidgets():
            if isinstance(window, TemplatePreviewDialog):
                assert window.page.image is not None
                inspected.append(window.windowTitle())
                window.reject()

    try:
        QTimer.singleShot(0, close_inspection)
        picker.rows["dark"].preview_button.click()
        assert inspected == ["After Hours"]
        large = [call for call in renderer.calls if call[3] >= 960]
        assert len(large) == 1 and large[0][0] == "dark"
        assert picker.isVisible()
        assert not project.layoutManager().printLayouts()
        assert project.customVariables() == variables
        assert not settings.allKeys()
    finally:
        picker.reject()


def test_closing_the_inspection_cancels_its_pending_render(qgis_app):
    parent = TemplatePicker(_Renderer())
    pending = _Pending(960, 5000)
    window = TemplatePreviewDialog(
        "Near & Far", "", "A4", lambda _width: pending, parent
    )
    try:
        window.show()
        window.start()
        window.reject()
        assert pending.cancelled
        # A late result must not revive a closed inspection.
        pending._done()
        assert not window.isVisible()
        assert window.page.image is None
    finally:
        parent.reject()
