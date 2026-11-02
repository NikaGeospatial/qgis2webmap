"""The Colour vision dialog against a real QGIS: the simulation in the layout
preview before Apply, the canvas, the browser Apply flow, the legend table, the
mode signal, where Apply goes, what is remembered, input never left blocked
and the NIKA hand-off.

The canvas half is tested on a real `QgsMapCanvas`, not a double: the preview
enum names changed shape between PyQt5 and PyQt6, and a double would only
prove what this file believes about them. Legend extraction is tested through
the real `read_project`, so a renderer the reader translates differently from
what `core.cvd.legend_entries` expects shows up here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from test_cartograph_panel_widgets import _quiet

qgis_core = pytest.importorskip("qgis.core")

from nika_onlymap_exporter.core.cvd import (  # noqa: E402 - after the importorskip
    LegendEntry,
    legend_entries,
)

PERMITS = (
    LegendEntry("Approved", "#2ca02c", "permits", "Permits"),
    LegendEntry("Rejected", "#d62728", "permits", "Permits"),
    LegendEntry("Pending", "#1b2d0a", "permits", "Permits"),
    LegendEntry("Primary", "#d62728", "roads", "Roads"),
)


class Recorder:
    """Collects the calls a callback receives."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, value: object) -> None:
        self.calls.append(value)


@pytest.fixture
def canvas(qgis_app):
    from qgis.gui import QgsMapCanvas

    widget = QgsMapCanvas()
    yield widget
    widget.deleteLater()


@pytest.fixture
def make_dialog(qgis_app, canvas):
    from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
        AccessibilityDialog,
    )

    made = []

    def make(entries=PERMITS, **kwargs):
        kwargs.setdefault("canvas", canvas)
        kwargs.setdefault("open_preview", Recorder())
        dialog = AccessibilityDialog(lambda: entries, **kwargs)
        made.append(dialog)
        return dialog

    yield make
    from qgis.PyQt import sip

    for dialog in made:
        if not sip.isdeleted(dialog):  # closing destroys the window
            dialog.close()
            dialog.deleteLater()


def table_rows(dialog):
    """Each row's cells as text. The swatch columns show only a painted icon,
    so their hex is read from the tooltip, which is also their accessible
    text."""
    return [
        tuple(
            dialog.table.item(row, column).toolTip()
            for column in range(dialog.table.columnCount())
        )
        for row in range(dialog.table.rowCount())
    ]


SHAPE_COLOUR = "#d62728"


@pytest.fixture
def page(qgis_app, project):
    """A shown page pane whose layout is one big filled red rectangle."""
    from qgis.core import (
        QgsLayoutItemShape,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsUnitTypes,
    )
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    shape = QgsLayoutItemShape(layout)
    shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    shape.attemptMove(QgsLayoutPoint(0, 0, QgsUnitTypes.LayoutMillimeters))
    shape.attemptResize(QgsLayoutSize(297, 210, QgsUnitTypes.LayoutMillimeters))
    symbol = qgis_core.QgsFillSymbol.createSimple(
        {"color": SHAPE_COLOUR, "outline_style": "no"}
    )
    shape.setSymbol(symbol)
    layout.addLayoutItem(shape)
    pane = PagePane()
    pane.resize(700, 500)
    pane.show()
    pane.show_layout(layout)
    _quiet(qgis_app, pane)
    yield pane, layout
    pane.close()
    pane.deleteLater()
    del QColor


def settle(qgis_app, seconds: float = 0.2) -> None:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.01)


def preview_colour(pane) -> str:
    """The colour on screen at the middle of the layout preview's page."""
    viewport = pane.preview.view.viewport()
    image = viewport.grab().toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2).name()


@pytest.fixture
def make_dialog_with_preview(qgis_app, canvas, page):
    from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
        AccessibilityDialog,
    )

    pane, _layout = page
    made = []

    def make(**kwargs):
        kwargs.setdefault("canvas", canvas)
        kwargs.setdefault("preview_view", pane.preview.view)
        kwargs.setdefault("open_preview", Recorder())
        dialog = AccessibilityDialog(lambda: PERMITS, **kwargs)
        made.append(dialog)
        return dialog

    yield make
    from qgis.PyQt import sip

    for dialog in made:
        if not sip.isdeleted(dialog):  # closing destroys the window
            dialog.close()
            dialog.deleteLater()


class TestPreviewBeforeApply:
    def test_the_simulation_shows_in_the_preview_but_not_on_the_canvas(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        normal = preview_colour(pane)
        assert normal == SHAPE_COLOUR
        for condition in ("protanopia", "deuteranopia", "tritanopia", "achromatopsia"):
            dialog.set_condition(condition)
            _quiet(qgis_app, pane)
            assert preview_colour(pane) != normal, condition
            assert not canvas.previewModeEnabled(), condition
        dialog.set_condition(None)
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == normal

    def test_apply_enables_only_with_an_unapplied_change(
        self, make_dialog_with_preview
    ) -> None:
        dialog = make_dialog_with_preview()
        assert not dialog.apply_button.isEnabled()
        dialog.set_condition("deuteranopia")
        assert dialog.apply_button.isEnabled()
        dialog.apply_button.click()
        assert not dialog.apply_button.isEnabled()
        dialog.set_condition("protanopia")
        assert dialog.apply_button.isEnabled()
        dialog.set_condition("deuteranopia")  # back to what is applied
        assert not dialog.apply_button.isEnabled()

    def test_apply_filters_the_canvas_and_keeps_the_dialog_open(
        self, make_dialog_with_preview, canvas
    ) -> None:
        from qgis.gui import QgsPreviewEffect

        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.canvas_check.setChecked(True)
        for condition, mode in (
            ("protanopia", QgsPreviewEffect.PreviewProtanope),
            ("deuteranopia", QgsPreviewEffect.PreviewDeuteranope),
            ("tritanopia", QgsPreviewEffect.PreviewTritanope),
            ("achromatopsia", QgsPreviewEffect.PreviewGrayscale),
        ):
            dialog.set_condition(condition)
            dialog.apply_button.click()
            assert dialog.isVisible()
            assert canvas.previewModeEnabled()
            assert canvas.previewMode() == mode
        dialog.set_condition(None)
        dialog.apply_button.click()
        assert not canvas.previewModeEnabled()

    @pytest.mark.parametrize("how", ["close_button", "escape", "window_close"])
    def test_leaving_discards_what_was_not_applied(
        self, qgis_app, page, make_dialog_with_preview, canvas, how
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        normal = preview_colour(pane)
        dialog.canvas_check.setChecked(True)
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        simulated = preview_colour(pane)
        assert simulated != normal
        dialog.set_condition("tritanopia")
        _quiet(qgis_app, pane)
        assert preview_colour(pane) not in (normal, simulated)

        if how == "close_button":
            dialog.close_button.click()
        elif how == "escape":
            QTest.keyClick(dialog.mode_combo, Qt.Key.Key_Escape)
        else:
            dialog.close()
        _quiet(qgis_app, pane)
        assert not dialog.isVisible()
        assert preview_colour(pane) == simulated
        assert canvas.previewModeEnabled()
        dialog.present()  # the same window comes back, at what was applied
        assert dialog.isVisible()
        assert dialog.current_condition() == "deuteranopia"

    def test_leaving_without_applying_restores_normal_vision(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.set_condition("protanopia")
        dialog.close()
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == SHAPE_COLOUR
        assert not canvas.previewModeEnabled()

    def test_a_preview_mode_the_user_chose_survives_until_replaced(
        self, make_dialog_with_preview, canvas
    ) -> None:
        from qgis.gui import QgsPreviewEffect

        canvas.setPreviewModeEnabled(True)
        canvas.setPreviewMode(QgsPreviewEffect.PreviewMono)
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.canvas_check.setChecked(True)
        dialog.set_condition("deuteranopia")
        dialog.close()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewMono
        dialog.show()
        dialog.canvas_check.setChecked(True)
        dialog.set_condition("tritanopia")
        dialog.apply_button.click()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewTritanope
        dialog.set_condition(None)
        dialog.apply_button.click()
        assert canvas.previewModeEnabled()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewMono

    def test_reset_restores_the_canvas_and_clears_the_preview(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        dialog.reset_canvas()
        _quiet(qgis_app, pane)
        assert not canvas.previewModeEnabled()
        assert preview_colour(pane) == SHAPE_COLOUR

    def test_without_a_canvas_apply_still_records_the_choice(
        self, make_dialog_with_preview
    ) -> None:
        dialog = make_dialog_with_preview(canvas=None)
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()  # must not raise
        assert not dialog.apply_button.isEnabled()

    def test_exports_keep_their_colours_while_a_simulation_is_shown(
        self, qgis_app, page, make_dialog_with_preview, tmp_path
    ) -> None:
        from qgis.core import QgsLayoutExporter
        from qgis.PyQt.QtGui import QImage

        _pane, layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = 30
        target = tmp_path / "page.png"
        result = QgsLayoutExporter(layout).exportToImage(str(target), settings)
        assert result == QgsLayoutExporter.ExportResult.Success
        image = QImage(str(target))
        centre = image.pixelColor(image.width() // 2, image.height() // 2)
        assert centre.name() == SHAPE_COLOUR


class TestWhereApplyGoes:
    def test_the_canvas_box_starts_unchecked_with_its_label(self, make_dialog):
        dialog = make_dialog()
        assert not dialog.canvas_check.isChecked()
        assert dialog.canvas_check.text() == "Also apply to the QGIS map canvas"

    def test_unchecked_apply_changes_the_previews_only(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        _quiet(qgis_app, pane)
        assert preview_colour(pane) != SHAPE_COLOUR
        assert not canvas.previewModeEnabled()

    def test_checked_apply_reaches_the_canvas_too(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        from qgis.gui import QgsPreviewEffect

        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.canvas_check.setChecked(True)
        dialog.set_condition("tritanopia")
        dialog.apply_button.click()
        _quiet(qgis_app, pane)
        assert preview_colour(pane) != SHAPE_COLOUR
        assert canvas.previewModeEnabled()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewTritanope

    def test_unticking_the_box_and_applying_takes_the_canvas_back(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.canvas_check.setChecked(True)
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        assert canvas.previewModeEnabled()
        assert not dialog.apply_button.isEnabled()
        dialog.canvas_check.setChecked(False)
        assert dialog.apply_button.isEnabled()  # the box alone is a change
        dialog.apply_button.click()
        _quiet(qgis_app, pane)
        assert not canvas.previewModeEnabled()
        assert preview_colour(pane) != SHAPE_COLOUR  # the previews keep it

    def test_the_box_alone_is_nothing_to_apply_in_normal_vision(
        self, make_dialog_with_preview
    ) -> None:
        dialog = make_dialog_with_preview()
        dialog.canvas_check.setChecked(True)
        assert not dialog.apply_button.isEnabled()

    def test_leaving_discards_an_unapplied_box_change(
        self, make_dialog_with_preview
    ) -> None:
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.canvas_check.setChecked(True)
        dialog.close()
        dialog.show()
        assert not dialog.canvas_check.isChecked()


class TestRemembered:
    def test_an_applied_preview_simulation_outlives_closing_the_dialog(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        dialog = make_dialog_with_preview()
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.apply_button.click()
        simulated = preview_colour(pane)
        dialog.close()
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == simulated != SHAPE_COLOUR
        dialog = make_dialog_with_preview()  # closing destroys the window
        dialog.show()
        assert dialog.current_condition() == "deuteranopia"
        assert not dialog.apply_button.isEnabled()

    def test_it_survives_switching_layouts(
        self, qgis_app, page, make_dialog_with_preview
    ) -> None:
        pane, layout = page
        dialog = make_dialog_with_preview()
        dialog.set_condition("achromatopsia")
        dialog.apply_button.click()
        simulated = preview_colour(pane)
        assert simulated != SHAPE_COLOUR
        other = layout.clone()
        other.setName("Other")
        pane.show_layout(other)
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == simulated
        pane.show_layout(layout)
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == simulated

    def test_a_new_dialog_starts_where_the_last_one_left_off(
        self, qgis_app, page, make_dialog_with_preview, canvas
    ) -> None:
        pane, _layout = page
        first = make_dialog_with_preview()
        first.set_condition("protanopia")
        first.apply_button.click()
        simulated = preview_colour(pane)
        # A restart: the preview is plain again, and a fresh dialog is built.
        first.reset_canvas()
        _quiet(qgis_app, pane)
        assert preview_colour(pane) == SHAPE_COLOUR
        seen = Recorder()
        second = make_dialog_with_preview()
        second.mode_changed.connect(seen)
        _quiet(qgis_app, pane)
        assert second.applied_condition() == "protanopia"
        assert second.current_condition() == "protanopia"
        assert preview_colour(pane) == simulated
        assert not canvas.previewModeEnabled()
        assert not second.canvas_check.isChecked()

    def test_the_canvas_choice_is_remembered_and_put_back_on_the_canvas(
        self, qgis_app, make_dialog_with_preview, canvas
    ) -> None:
        from qgis.gui import QgsMapCanvas, QgsPreviewEffect

        first = make_dialog_with_preview()
        first.canvas_check.setChecked(True)
        first.set_condition("deuteranopia")
        first.apply_button.click()
        fresh_canvas = QgsMapCanvas()
        try:
            second = make_dialog_with_preview(canvas=fresh_canvas)
            assert second.canvas_check.isChecked()
            assert fresh_canvas.previewModeEnabled()
            assert fresh_canvas.previewMode() == QgsPreviewEffect.PreviewDeuteranope
        finally:
            fresh_canvas.deleteLater()

    def test_applying_normal_vision_forgets_it(
        self, qgis_app, make_dialog_with_preview
    ) -> None:
        first = make_dialog_with_preview()
        first.set_condition("tritanopia")
        first.apply_button.click()
        first.set_condition(None)
        first.apply_button.click()
        second = make_dialog_with_preview()
        assert second.current_condition() is None
        assert second.applied_condition() is None

    def test_an_unapplied_choice_is_not_remembered(
        self, make_dialog_with_preview
    ) -> None:
        first = make_dialog_with_preview()
        first.show()
        first.set_condition("tritanopia")
        first.close()
        assert make_dialog_with_preview().applied_condition() is None

    def test_the_users_own_canvas_mode_survives_a_rebuilt_dialog(
        self, make_dialog_with_preview, canvas
    ) -> None:
        """The language switch rebuilds the whole window: a dialog built over a
        canvas that still carries the last one's simulation must give back the
        user's own View > Preview Mode, not keep the old simulation."""
        from qgis.gui import QgsPreviewEffect

        canvas.setPreviewModeEnabled(True)
        canvas.setPreviewMode(QgsPreviewEffect.PreviewMono)
        first = make_dialog_with_preview()
        first.canvas_check.setChecked(True)
        first.set_condition("deuteranopia")
        first.apply_button.click()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewDeuteranope
        second = make_dialog_with_preview()  # the old one is simply abandoned
        second.canvas_check.setChecked(False)
        second.apply_button.click()
        assert canvas.previewModeEnabled()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewMono


class TestInputIsNeverLeftBlocked:
    """Clicking out of the window after choosing an option, without Apply,
    must leave the window behind it fully usable."""

    @pytest.mark.parametrize("how", ["combo_choice", "combo_then_other_window"])
    def test_a_click_on_the_window_behind_still_lands(
        self, qgis_app, make_dialog, how
    ) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import (
            QApplication,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        host = QWidget()
        host_layout = QVBoxLayout(host)
        behind = QPushButton("Behind", host)
        host_layout.addWidget(behind)
        host.resize(260, 120)
        host.move(0, 0)
        host.show()
        dialog = make_dialog(parent=host)
        dialog.host_alive = host  # the fixture closes the dialog after this test
        dialog.move(host.width() + 40, 0)
        dialog.show()
        clicks = []
        behind.clicked.connect(lambda: clicks.append(1))
        try:
            _quiet(qgis_app)
            # Chosen with the keyboard: no list pop-up opens (the cases below
            # click through the window system, as a real click arrives).
            dialog.mode_combo.setFocus()
            QTest.keyClick(dialog.mode_combo, Qt.Key.Key_Down)
            QTest.keyClick(dialog.mode_combo, Qt.Key.Key_Down)
            assert dialog.current_condition() == "deuteranopia"
            if how == "combo_then_other_window":
                dialog.activateWindow()
            # Through the window system, as a real click arrives.
            window = host.windowHandle()
            QTest.mouseClick(
                window,
                Qt.MouseButton.LeftButton,
                pos=behind.mapTo(host, QPoint(5, 5)),
                delay=30,
            )
            _quiet(qgis_app)
            assert clicks == [1]
            assert QWidget.mouseGrabber() is None
            assert QWidget.keyboardGrabber() is None
            assert QApplication.activeModalWidget() is None
            assert QApplication.activePopupWidget() is None
            # Coherent: still open, or closed with the unapplied choice gone.
            assert dialog.isVisible() or dialog.current_condition() is None
        finally:
            dialog.close()
            host.close()


class TestLeavingKeepsTheOneWindow:
    """Whatever hides the window, what was not applied goes with it, nothing
    is left grabbing input, and the same window shows again."""

    @pytest.mark.parametrize("how", ["close", "close_button", "escape", "stray_hide"])
    def test_a_window_that_goes_drops_the_choice_and_shows_again(
        self, qgis_app, make_dialog, how
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication, QWidget

        host = QWidget()
        host.show()
        dialog = make_dialog(parent=host)
        dialog.show()
        dialog.set_condition("deuteranopia")  # chosen, not applied
        _quiet(qgis_app)
        if how == "close":
            dialog.close()
        elif how == "close_button":
            dialog.close_button.click()
        elif how == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        else:
            dialog.hide()
        _quiet(qgis_app)
        try:
            assert not dialog.isVisible()
            assert dialog.current_condition() is None
            assert QWidget.mouseGrabber() is None
            assert QWidget.keyboardGrabber() is None
            assert QApplication.activeModalWidget() is None
            dialog.present()
            assert dialog.isVisible()
            assert dialog.current_condition() is None
        finally:
            host.close()

    def test_presenting_an_open_window_keeps_it_and_its_pending_choice(
        self, qgis_app, make_dialog
    ) -> None:
        from qgis.PyQt.QtWidgets import QWidget

        host = QWidget()
        host.show()
        dialog = make_dialog(parent=host)
        dialog.show()
        dialog.set_condition("tritanopia")  # chosen, not applied
        try:
            for _press in range(3):
                dialog.present()
                _quiet(qgis_app)
                assert dialog.isVisible()
                assert dialog.current_condition() == "tritanopia"
            assert dialog.applied_condition() is None
        finally:
            host.close()

    def test_a_deactivated_application_does_not_hide_the_window(
        self, qgis_app, make_dialog
    ) -> None:
        from qgis.PyQt.QtCore import QEvent
        from qgis.PyQt.QtWidgets import QApplication

        dialog = make_dialog()
        dialog.show()
        QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
        QApplication.sendEvent(
            QApplication.instance(), QEvent(QEvent.Type.ApplicationDeactivate)
        )
        # Not hidden later either: a bounded wait, as nothing is due to happen.
        settle(qgis_app, 0.1)
        assert dialog.isVisible()


class TestBrowser:
    def test_the_box_starts_unchecked_with_its_label(self, make_dialog) -> None:
        dialog = make_dialog()
        assert not dialog.browser_check.isChecked()
        assert dialog.browser_check.text() == "Also open browser preview when applying"

    def test_nothing_opens_until_apply_is_pressed_with_the_box_checked(
        self, make_dialog
    ) -> None:
        preview = Recorder()
        dialog = make_dialog(open_preview=preview)
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.table.selectRow(1)
        dialog.browser_check.setChecked(True)
        dialog.set_condition("achromatopsia")
        assert preview.calls == []
        dialog.apply_button.click()
        # The runtime's own `cvd=` vocabulary.
        assert preview.calls == ["achromatopsia"]
        dialog.set_condition("protanopia")
        assert preview.calls == ["achromatopsia"]  # not refreshed unapplied

    def test_apply_with_the_box_unchecked_opens_nothing(self, make_dialog) -> None:
        preview = Recorder()
        dialog = make_dialog(open_preview=preview)
        dialog.show()
        dialog.set_condition("protanopia")
        dialog.apply_button.click()
        assert preview.calls == []

    def test_the_box_is_unchecked_again_each_time_the_dialog_opens(
        self, make_dialog
    ) -> None:
        dialog = make_dialog()
        dialog.show()
        dialog.browser_check.setChecked(True)
        dialog.close()
        # Closing destroys the window; the next one opens unchecked.
        dialog = make_dialog()
        dialog.show()
        assert not dialog.browser_check.isChecked()

    def test_without_a_preview_the_target_is_disabled(self, make_dialog):
        dialog = make_dialog(open_preview=None)
        assert not dialog.browser_check.isEnabled()
        assert dialog.browser_check.toolTip()


class TestTable:
    def test_rows_match_the_entries_with_notes(self, make_dialog):
        dialog = make_dialog()
        dialog.set_condition("deuteranopia")
        rows = table_rows(dialog)
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("Permits", "Approved", "#2ca02c"),
            ("Permits", "Rejected", "#d62728"),
            ("Permits", "Pending", "#1b2d0a"),
            ("Roads", "Primary", "#d62728"),
        ]
        notes = [r[4] for r in rows]
        assert notes == ["like Rejected", "like Approved", "ok", "ok"]
        # Simulated colours: the runtime's, rounded for display.
        assert rows[1][3] == "#7f7f13"

    def test_swatches_are_painted(self, make_dialog):
        dialog = make_dialog()
        icon = dialog.table.item(0, 2).icon()
        assert not icon.isNull()
        image = icon.pixmap(14, 14).toImage()
        centre = image.pixelColor(7, 7)
        assert (centre.red(), centre.green(), centre.blue()) == (0x2C, 0xA0, 0x2C)

    def test_normal_vision_names_the_condition_in_each_note(self, make_dialog):
        dialog = make_dialog()
        notes = [r[4] for r in table_rows(dialog)]
        assert notes[0] == "like Rejected (deuteranopia)"

    def test_a_safe_condition_says_so(self, make_dialog):
        dialog = make_dialog()
        dialog.set_condition("protanopia")
        assert [r[4] for r in table_rows(dialog)] == ["ok"] * 4

    def test_no_summary_line_is_shown(self, make_dialog):
        from qgis.PyQt.QtWidgets import QLabel

        dialog = make_dialog()
        dialog.show()
        texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "collapse" not in texts
        assert dialog.empty_label.isHidden()  # there are classes to show

    def test_refresh_reads_the_provider_again(self, make_dialog):
        entries = list(PERMITS)
        dialog = make_dialog(entries=entries)
        assert dialog.table.rowCount() == 4
        entries.append(LegendEntry("Track", "#808080", "roads", "Roads"))
        dialog.refresh()
        assert dialog.table.rowCount() == 5

    def test_empty_legend_explains_itself(self, make_dialog):
        dialog = make_dialog(entries=())
        assert dialog.table.rowCount() == 0
        assert not dialog.empty_label.isHidden()
        assert "Save the cartograph" in dialog.empty_label.text()


class TestSignals:
    def test_mode_signal_carries_the_condition(self, make_dialog):
        from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
            simulating_tooltip,
        )

        dialog = make_dialog()
        seen = []
        dialog.mode_changed.connect(seen.append)
        dialog.set_condition("deuteranopia")
        assert seen == []  # chosen, not applied
        dialog.apply_button.click()
        dialog.set_condition(None)
        dialog.apply_button.click()
        assert seen == ["deuteranopia", ""]
        assert simulating_tooltip("deuteranopia") == "Simulating deuteranopia"

    def test_is_a_non_modal_window_that_never_hides_on_deactivation(self, make_dialog):
        """A Tool window hides itself when the application is deactivated; on
        Wayland that can leave an invisible surface over the parent."""
        from qgis.PyQt.QtCore import Qt

        dialog = make_dialog()
        assert not dialog.isModal()
        assert dialog.windowFlags() & Qt.WindowType.Tool != Qt.WindowType.Tool
        for bad in (
            Qt.WindowType.Popup,
            Qt.WindowType.ToolTip,
            Qt.WindowType.FramelessWindowHint,
            Qt.WindowType.WindowStaysOnTopHint,
        ):
            assert dialog.windowFlags() & bad != bad


class TestLegendFromProject:
    def test_categorized_layer_through_read_project(self, project, make_memory_layer):
        from nika_onlymap_exporter.core.cvd import lint_legend_colours
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project

        layer = make_memory_layer(
            "permits",
            fields="status:string",
            features=[("a", [0.0, 0.0]), ("r", [1.0, 1.0]), ("x", [2.0, 2.0])],
        )
        symbol = qgis_core.QgsMarkerSymbol.createSimple
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "status",
                [
                    qgis_core.QgsRendererCategory(
                        "a", symbol({"color": "#2ca02c"}), "Approved"
                    ),
                    qgis_core.QgsRendererCategory(
                        "r", symbol({"color": "#d62728"}), "Rejected"
                    ),
                    qgis_core.QgsRendererCategory(
                        "x", symbol({"color": "#000000"}), "Withdrawn"
                    ),
                ],
            )
        )
        # Switched off in QGIS: not drawn, so not in the legend either.
        layer.renderer().updateCategoryRenderState(2, False)
        project.addMapLayer(layer)

        export = read_project(project, FidelityReportBuilder())
        entries = legend_entries(export.layers)

        assert [(e.layer_name, e.label, e.color) for e in entries] == [
            ("permits", "Approved", "#2ca02c"),
            ("permits", "Rejected", "#d62728"),
        ]
        assert entries[0].layer == layer.id()
        lint = lint_legend_colours(entries)
        assert [(c.a.label, c.b.label, c.condition) for c in lint.collapses] == [
            ("Approved", "Rejected", "deuteranopia")
        ]
