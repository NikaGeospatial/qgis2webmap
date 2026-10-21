"""The Cartograph screen's own widgets, one at a time: the page pane (caption
and zoom bar over the live page), the two-line save location, and a panel
section laid out right to left.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.
The dialog-level wiring of the same widgets is in `test_cartograph_dialog.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")


def _settle(qgis_app, seconds: float = 0.2) -> None:
    """Let the page's zero-delay fit timers and any resize run."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.01)


def _a4_layout(project):
    from qgis.core import QgsPrintLayout

    layout = QgsPrintLayout(project)
    # QGIS's default page: one A4 sheet, landscape.
    layout.initializeDefaults()
    layout.setName("Survey")
    project.layoutManager().addLayout(layout)
    return layout


@pytest.fixture
def pane(qgis_app, project):
    """A shown page pane on an A4 landscape layout, fitted."""
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    widget = PagePane()
    widget.resize(600, 500)
    widget.show()
    widget.show_layout(_a4_layout(project))
    _settle(qgis_app)
    yield widget
    widget.close()
    widget.deleteLater()


def _scale(pane) -> float:
    return pane.preview.view.transform().m11()


def _percent(pane) -> int:
    text = pane.zoom_label.text()
    assert text.endswith("%"), text
    return int(text[:-1])


class TestPagePane:
    def test_the_caption_names_the_layout_and_its_page(self, pane) -> None:
        assert pane.caption.text() == "QGIS layout · A4 landscape"
        assert not pane.caption.isHidden()
        assert not pane.zoom_bar.isHidden()

    def test_without_a_layout_the_caption_and_zoom_bar_hide(
        self, qgis_app, pane
    ) -> None:
        pane.show_layout(None)
        _settle(qgis_app)
        assert not pane.preview.showing_page()
        assert pane.caption.isHidden()
        assert pane.zoom_bar.isHidden()

    def test_the_page_is_not_the_first_tab_stop(self, pane) -> None:
        """A click still focuses the page; Tab goes to the zoom bar."""
        from qgis.PyQt.QtCore import Qt

        assert pane.preview.view.focusPolicy() == Qt.FocusPolicy.ClickFocus

    def test_the_zoom_buttons_change_the_level_shown(self, qgis_app, pane) -> None:
        fitted = _percent(pane)
        assert fitted > 0
        pane.zoom_in_button.click()
        _settle(qgis_app)
        zoomed_in = _percent(pane)
        assert zoomed_in > fitted
        pane.zoom_out_button.click()
        pane.zoom_out_button.click()
        _settle(qgis_app)
        assert _percent(pane) < zoomed_in
        assert _percent(pane) < fitted

    def test_fit_page_refits_after_the_user_zoomed(self, qgis_app, pane) -> None:
        fitted = _scale(pane)
        pane.zoom_in_button.click()
        _settle(qgis_app)
        assert pane.preview._user_zoomed
        assert _scale(pane) > fitted * 1.05
        pane.fit_button.click()
        _settle(qgis_app)
        assert not pane.preview._user_zoomed
        # The same fit as before the zoom: refitted once the zoomed page's
        # scroll bars have gone, which took 2.4% off the first fit.
        assert _scale(pane) == pytest.approx(fitted, rel=0.002)

    def test_a_resize_alone_keeps_the_page_fitting(self, qgis_app, pane) -> None:
        """The view reports a zoom change when it is only resized; that is
        not the user zooming, so the page fits the new size."""
        small = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert not pane.preview._user_zoomed
        assert _scale(pane) > small * 1.2

    def test_the_wheel_is_a_user_zoom_that_stays(self, qgis_app, pane) -> None:
        from qgis.PyQt.QtCore import QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QWheelEvent
        from qgis.PyQt.QtWidgets import QApplication

        viewport = pane.preview.view.viewport()
        centre = QPointF(viewport.width() / 2, viewport.height() / 2)
        wheel = QWheelEvent(
            centre,
            QPointF(viewport.mapToGlobal(centre.toPoint())),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(viewport, wheel)
        assert pane.preview._user_zoomed
        # Their zoom stays through a resize.
        kept = _scale(pane)
        pane.resize(1000, 800)
        _settle(qgis_app)
        assert _scale(pane) == pytest.approx(kept, rel=0.01)


@pytest.fixture
def save_location(qgis_app):
    from nika_onlymap_exporter.ui.cartograph.layout_page import SaveLocation

    widget = SaveLocation()
    widget.setPlaceholderText("Choose where the cartograph is saved")
    widget.resize(700, 60)
    widget.show()
    qgis_app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


class TestSaveLocation:
    def test_the_name_then_the_folder_from_home(self, qgis_app, save_location) -> None:
        folder = Path.home() / "Documents" / "QGIS2WebMap"
        path = folder / "parcels-Survey.html"
        save_location.setText(str(path))
        qgis_app.processEvents()
        assert save_location.label.text() == "parcels-Survey.html"
        assert save_location.label.toolTip() == str(path)
        assert save_location.folder_label.text() == str(
            Path("~") / "Documents" / "QGIS2WebMap"
        )
        assert save_location.folder_label.toolTip() == str(folder)
        assert not save_location.folder_label.isHidden()
        assert not save_location.reveal_button.isHidden()
        assert save_location.reveal_button.text() == "Show in folder"

    def test_a_folder_outside_home_is_shown_whole(
        self, qgis_app, save_location, tmp_path
    ) -> None:
        if tmp_path.is_relative_to(Path.home()):
            pytest.skip("the temporary folder is under home on this machine")
        save_location.setText(str(tmp_path / "a.html"))
        qgis_app.processEvents()
        assert save_location.folder_label.text() == str(tmp_path)

    def test_with_no_path_only_the_placeholder_shows(
        self, qgis_app, save_location
    ) -> None:
        save_location.setText(str(Path.home() / "a.html"))
        save_location.setText("")
        qgis_app.processEvents()
        assert save_location.label.text() == "Choose where the cartograph is saved"
        assert save_location.folder_label.isHidden()
        assert save_location.reveal_button.isHidden()
        assert not save_location.change_button.isHidden()

    def test_show_in_folder_asks_for_it(self, qgis_app, save_location) -> None:
        save_location.setText(str(Path.home() / "a.html"))
        asked: list[bool] = []
        save_location.reveal_requested.connect(lambda: asked.append(True))
        save_location.reveal_button.click()
        assert asked == [True]


class TestPanelSectionDirection:
    def _section(self, direction):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QWidget

        from nika_onlymap_exporter.ui.cartograph.panel_section import PanelSection

        parent = QWidget()
        parent.setLayoutDirection(direction)
        # Qt5's enums have no `.name`; the key only has to differ per direction.
        key = "rtl" if direction == Qt.LayoutDirection.RightToLeft else "ltr"
        section = PanelSection(f"direction-{key}", "Map frames", parent)
        return parent, section

    def test_right_to_left_the_header_keeps_its_width_and_points_left(
        self, qgis_app
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QSizePolicy

        parent, section = self._section(Qt.LayoutDirection.RightToLeft)
        assert section.isRightToLeft()
        policy = section.header.sizePolicy().horizontalPolicy()
        assert policy == QSizePolicy.Policy.Preferred
        row = section.layout().itemAt(0).layout()
        assert row.stretch(row.indexOf(section.header)) == 0
        # A stretch between the header and the summary at the far end.
        assert row.itemAt(1).spacerItem() is not None
        assert row.indexOf(section.summary) == 2
        assert not section.is_open()
        assert section.header.arrowType() == Qt.ArrowType.LeftArrow
        section.set_open(True)
        assert section.header.arrowType() == Qt.ArrowType.DownArrow
        section.set_open(False)
        assert section.header.arrowType() == Qt.ArrowType.LeftArrow
        parent.deleteLater()

    def test_left_to_right_the_header_stretches_and_points_right(
        self, qgis_app
    ) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QSizePolicy

        parent, section = self._section(Qt.LayoutDirection.LeftToRight)
        policy = section.header.sizePolicy().horizontalPolicy()
        assert policy == QSizePolicy.Policy.Expanding
        row = section.layout().itemAt(0).layout()
        assert row.stretch(row.indexOf(section.header)) == 1
        assert row.indexOf(section.summary) == 1
        assert section.header.arrowType() == Qt.ArrowType.RightArrow
        parent.deleteLater()
