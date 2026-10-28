"""The page preview's first view and its Add item / Fit page control strip: the
page opens fitted to the viewport, stays fitted through resizes and selecting,
and the two buttons sit in a strip under the view that no fitted page enters.

Checks what is on screen (the page's box in the viewport, the buttons' boxes),
not how it got there. Needs PyQGIS; runs with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from qgis.PyQt.QtCore import QRect
from test_cartograph_panel_widgets import (
    _a4_layout,
    _drag,
    _scale,
    _settle,
    _wheel_zoom,
)

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def pane(qgis_app, project):
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    widget = PagePane()
    widget.resize(700, 560)
    widget.show()
    widget.show_layout(_a4_layout(project))
    _settle(qgis_app)
    yield widget
    widget.close()
    widget.deleteLater()


def _page_box(pane):
    """The page's paper, in the viewport's pixels."""
    preview = pane.preview
    page = preview._current_page_rect()
    return preview.view.mapFromScene(page).boundingRect()


def _button_box(pane, button):
    """A floating button's box in the viewport's pixels (the strip is under
    the viewport, so a button's top is at or below the viewport's height)."""
    viewport = pane.preview.view.viewport()
    top_left = viewport.mapFromGlobal(button.mapToGlobal(button.rect().topLeft()))
    return top_left.x(), top_left.y(), button.width(), button.height()


def _strip_box(pane):
    """The control strip's box, in the viewport's pixels."""
    viewport = pane.preview.view.viewport()
    strip = pane.preview._strip
    top_left = viewport.mapFromGlobal(strip.mapToGlobal(strip.rect().topLeft()))
    return top_left.x(), top_left.y(), strip.width(), strip.height()


def _assert_fitted(pane) -> None:
    """The whole page is in view, centred, and fills one dimension of the
    viewport but for a modest margin."""
    viewport = pane.preview.view.viewport()
    box = _page_box(pane)
    assert box.left() >= 0 and box.top() >= 0
    assert box.right() <= viewport.width() and box.bottom() <= viewport.height()
    assert abs(box.center().x() - viewport.width() / 2) <= 2
    assert abs(box.center().y() - viewport.height() / 2) <= 2
    gap_x = viewport.width() - box.width()
    gap_y = viewport.height() - box.height()
    assert min(gap_x, gap_y) <= 40, "the page leaves room it could fill"
    assert max(gap_x, gap_y) >= 0


class TestFirstView:
    def test_a4_landscape_opens_fitted(self, pane) -> None:
        _assert_fitted(pane)

    def test_a_portrait_page_opens_fitted_too(self, qgis_app, project, pane) -> None:
        from qgis.core import QgsLayoutSize, QgsUnitTypes

        layout = pane.preview.view.currentLayout()
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(210, 297, QgsUnitTypes.LayoutMillimeters)
        )
        _settle(qgis_app)
        _assert_fitted(pane)
        box = _page_box(pane)
        assert box.height() > box.width()

    def test_a_pane_shown_hidden_then_sized_fits_the_real_size(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        widget = PagePane()
        widget.resize(30, 20)
        widget.show()
        widget.show_layout(_a4_layout(project))
        _settle(qgis_app)
        widget.resize(900, 650)
        _settle(qgis_app)
        _assert_fitted(widget)
        widget.close()

    def test_a_layout_loaded_while_hidden_fits_when_shown(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        widget = PagePane()
        widget.resize(800, 600)
        widget.show_layout(_a4_layout(project))
        widget.show()
        _settle(qgis_app)
        _assert_fitted(widget)
        widget.close()


class TestStaysFitted:
    def test_resizing_refits(self, qgis_app, pane) -> None:
        for size in ((1000, 760), (420, 400), (800, 520)):
            pane.resize(*size)
            _settle(qgis_app)
            _assert_fitted(pane)

    def test_selecting_an_item_then_resizing_still_refits(
        self, qgis_app, project, pane
    ) -> None:
        """A click on the page (it selects) is not a zoom: the page must not
        stay at its old size when the window is made bigger afterwards."""
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QPoint, QRectF

        layout = pane.preview.view.currentLayout()
        label = QgsLayoutItemLabel(layout)
        label.attemptSetSceneRect(QRectF(20, 20, 80, 20))
        layout.addLayoutItem(label)
        _settle(qgis_app)
        box = pane.preview.view.mapFromScene(label.sceneBoundingRect()).boundingRect()
        start = box.center()
        _drag(qgis_app, pane, start, start + QPoint(3, 2))
        _settle(qgis_app)
        small = _scale(pane)
        pane.resize(1100, 850)
        _settle(qgis_app)
        assert _scale(pane) > small * 1.2
        _assert_fitted(pane)

    def test_a_user_zoom_and_pan_survive_a_resize_until_fit_page(
        self, qgis_app, pane
    ) -> None:
        _wheel_zoom(pane, 120)
        _wheel_zoom(pane, 120)
        _settle(qgis_app)
        zoomed = _scale(pane)
        pane.resize(900, 700)
        _settle(qgis_app)
        assert _scale(pane) == pytest.approx(zoomed, rel=0.01)
        pane.fit_button.click()
        _settle(qgis_app)
        _assert_fitted(pane)

    def test_another_page_size_fits_again_even_after_a_zoom(
        self, qgis_app, pane
    ) -> None:
        from qgis.core import QgsLayoutSize, QgsUnitTypes

        _wheel_zoom(pane, 120)
        _settle(qgis_app)
        layout = pane.preview.view.currentLayout()
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(297, 420, QgsUnitTypes.LayoutMillimeters)
        )
        _settle(qgis_app)
        _assert_fitted(pane)


class TestOverlay:
    def test_the_buttons_sit_in_a_row_at_the_strip_s_right(self, pane) -> None:
        viewport = pane.preview.view.viewport()
        add = _button_box(pane, pane.add_button)
        fit = _button_box(pane, pane.fit_button)
        assert add[1] == fit[1] and add[2:] == fit[2:]
        assert add[0] + add[2] < fit[0], "Add item left of Fit page"
        strip = _strip_box(pane)
        assert strip[0] + strip[2] - (fit[0] + fit[2]) == 8
        assert fit[1] >= viewport.height(), "wholly under the page view"

    def test_the_buttons_do_not_move_or_change_size_with_zoom_pan(
        self, qgis_app, pane
    ) -> None:
        from qgis.PyQt.QtCore import QPoint

        def snapshot():
            return (
                _button_box(pane, pane.add_button),
                _button_box(pane, pane.fit_button),
            )

        before = snapshot()
        for _ in range(3):
            _wheel_zoom(pane, 120)
        _settle(qgis_app)
        view = pane.preview.view
        _drag(qgis_app, pane, QPoint(200, 200), QPoint(120, 150))
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 40)
        _settle(qgis_app)
        assert snapshot() == before

    def test_the_buttons_keep_clear_of_scroll_bars(self, qgis_app, pane) -> None:
        for _ in range(4):
            _wheel_zoom(pane, 120)
        _settle(qgis_app)
        view = pane.preview.view
        assert view.verticalScrollBar().isVisible()
        bar = view.horizontalScrollBar()
        bar_bottom = view.mapTo(pane.preview._page, bar.geometry().bottomLeft()).y()
        strip = pane.preview._strip.geometry()
        assert strip.top() > bar_bottom - 1, "the strip is under the scroll bar"

    def test_clicking_a_button_does_not_select_or_pan(self, qgis_app, pane) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest

        view = pane.preview.view
        before = (_scale(pane), view.mapToScene(QPoint(0, 0)))
        QTest.mouseClick(
            pane.fit_button,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            pane.fit_button.rect().center(),
        )
        _settle(qgis_app)
        assert view.currentLayout().selectedLayoutItems() == []
        assert (_scale(pane), view.mapToScene(QPoint(0, 0))) == before

    def test_a_real_click_lands_on_each_button(self, qgis_app, pane) -> None:
        """Hit-testing as the compositor's mouse does: the widget under the
        pointer is the button itself over a button."""
        from qgis.PyQt.QtWidgets import QApplication

        for button in (pane.add_button, pane.fit_button):
            at = button.mapToGlobal(button.rect().center())
            assert QApplication.widgetAt(at) is button

    def test_clicking_the_real_fit_button_refits(self, qgis_app, pane) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication

        _wheel_zoom(pane, 120)
        _settle(qgis_app)
        button = pane.fit_button
        at = button.mapToGlobal(button.rect().center())
        target = QApplication.widgetAt(at)
        QTest.mouseClick(
            target,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            target.mapFromGlobal(at),
        )
        _settle(qgis_app)
        _assert_fitted(pane)

    def test_the_buttons_are_not_in_a_page_render(self, pane) -> None:
        """A grab of the viewport shows them; the export's own render of the
        layout (no widget involved) cannot, as they are widgets, not items."""
        layout = pane.preview.view.currentLayout()
        assert all(
            not hasattr(i, "text") or i.text() not in ("Add item", "Fit page")
            for i in layout.items()
        )


def _clear_of_strip(pane) -> None:
    """The whole fitted page is in view and none of it is under the controls:
    the page box, in the viewport's pixels, ends above the strip's top."""
    box = _page_box(pane)
    viewport = pane.preview.view.viewport()
    _x, strip_top, _w, _h = _strip_box(pane)
    assert viewport.rect().contains(box)
    assert box.bottom() < strip_top
    for button in (pane.add_button, pane.fit_button):
        bx, by, bw, bh = _button_box(pane, button)
        assert not box.intersects(QRect(bx, by, bw, bh))
    # Still as big as the room above the strip allows.
    assert min(viewport.width() - box.width(), viewport.height() - box.height()) <= 40


class TestPageClearOfTheStrip:
    def _set_size(self, qgis_app, pane, width, height) -> None:
        from qgis.core import QgsLayoutSize, QgsUnitTypes

        layout = pane.preview.view.currentLayout()
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters)
        )
        _settle(qgis_app)

    @pytest.mark.parametrize("sheet", [(297, 210), (210, 297)])
    def test_portrait_and_landscape_pages_open_clear_of_the_controls(
        self, qgis_app, pane, sheet
    ) -> None:
        self._set_size(qgis_app, pane, *sheet)
        _clear_of_strip(pane)

    @pytest.mark.parametrize("sheet", [(297, 210), (210, 297)])
    def test_a_narrow_window_and_a_resized_sidebar_stay_clear(
        self, qgis_app, pane, sheet
    ) -> None:
        self._set_size(qgis_app, pane, *sheet)
        for size in ((320, 520), (480, 360), (1100, 500), (700, 560)):
            pane.resize(*size)  # a sidebar drag changes the pane's width
            _settle(qgis_app)
            _clear_of_strip(pane)

    @pytest.mark.parametrize("sheet", [(297, 210), (210, 297)])
    def test_manual_zoom_then_fit_page_is_clear_of_the_controls(
        self, qgis_app, pane, sheet
    ) -> None:
        self._set_size(qgis_app, pane, *sheet)
        for _ in range(3):
            _wheel_zoom(pane, 120)
        _settle(qgis_app)
        pane.fit_button.click()
        _settle(qgis_app)
        _clear_of_strip(pane)

    def test_the_strip_is_compact(self, pane) -> None:
        _x, _y, _w, height = _strip_box(pane)
        button = pane.fit_button.height()
        assert button <= height <= button + 12
