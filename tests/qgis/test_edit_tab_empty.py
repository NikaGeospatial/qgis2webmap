"""The Edit tab's empty state, the Add item menu, Signal's figures as
ordinary text, and the selected item's header and map controls.

Real widgets, judged by what is visible: what shows with nothing selected,
what the Add item menu offers, how a Signal figure is edited and deleted, where
the header and the buttons sit.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from test_cartograph_panel_widgets import _settle
from test_edit_tab_fit import _item_of

pytest.importorskip("qgis.core")

NO_ITEM_TEXT = "Select an item on the page to edit its properties."


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _layout(parcels, project, template_id="dashboard"):
    from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

    extent = parcels.extent()
    extent.grow(200)
    return cartograph_from_view(
        extent, UTM, [parcels], project, template_id=template_id, layout_name="x"
    ).layout


@pytest.fixture
def pane(qgis_app, parcels, project):
    from nika_onlymap_exporter.ui import theme
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    layout = _layout(parcels, project)
    pane = PagePane()
    theme.apply(pane)
    pane.resize(900, 650)
    pane.show()
    pane.show_layout(layout)
    pane.controls.resize(340, 640)
    pane.controls.show()
    layout.deselectAll()
    pane.controls.refresh()
    _settle(qgis_app, 0.1)
    yield pane, layout
    pane.close()
    pane.deleteLater()


def _visible(controls, widget) -> bool:
    return widget.isVisibleTo(controls)


def _menu_action(pane, title):
    pane._fill_add_menu()
    return next((a for a in pane.add_menu.actions() if a.text() == title), None)


class TestNothingSelected:
    def test_only_the_hint_is_visible(self, pane) -> None:
        pane, _ = pane
        controls = pane.controls
        assert controls.heading.text() == NO_ITEM_TEXT
        assert _visible(controls, controls.heading)
        for hidden in (
            controls.delete_button,
            controls.body,
            controls.text_size_row,
        ):
            assert not _visible(controls, hidden), hidden


class TestAddItemMenu:
    def test_it_has_no_statistic_callout_on_any_layout(
        self, qgis_app, parcels, project, pane
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        pane, _ = pane
        pane._fill_add_menu()
        titles = [a.text() for a in pane.add_menu.actions()]
        assert titles and not any("callout" in t.lower() for t in titles)
        classic = PagePane()
        try:
            classic.show()
            classic.show_layout(_layout(parcels, project, "classic"))
            classic._fill_add_menu()
            assert not any(
                "callout" in a.text().lower() for a in classic.add_menu.actions()
            )
        finally:
            classic.close()


@pytest.fixture
def figure_pane(qgis_app, parcels, project):
    """A Signal layout that carries two figures, shown in a page pane."""
    from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
    from nika_onlymap_exporter.ui import theme
    from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

    extent = parcels.extent()
    extent.grow(200)
    layout = cartograph_from_view(
        extent,
        UTM,
        [parcels],
        project,
        template_id="dashboard",
        layout_name="figures",
        figures=[("214", "parcels surveyed"), ("12 km", "of road")],
    ).layout
    pane = PagePane()
    theme.apply(pane)
    pane.resize(900, 650)
    pane.show()
    pane.show_layout(layout)
    pane.controls.resize(340, 640)
    pane.controls.show()
    layout.deselectAll()
    pane.controls.refresh()
    _settle(qgis_app, 0.1)
    yield pane, layout
    pane.close()
    pane.deleteLater()


class TestSignalFiguresAreOrdinaryText:
    """A layout that already holds Signal figures treats each as an ordinary
    item: text you can select, edit and delete one by one."""

    def test_a_figure_value_is_edited_with_the_text_controls(
        self, qgis_app, figure_pane
    ) -> None:
        pane, layout = figure_pane
        controls = pane.controls
        value = layout.itemById("figure-0")
        value.setSelected(True)
        controls.refresh()
        _settle(qgis_app, 0.05)
        assert controls.selected_item() is value
        assert _visible(controls, controls.text_edit)
        assert controls.text_edit.toPlainText() == "214"
        controls.text_edit.setPlainText("215")
        controls._apply_pending_text()
        assert value.text() == "215"
        assert layout.itemById("figure-0-label").text() == "parcels surveyed"
        assert controls.selected_item() is value

    def test_the_line_and_the_rule_are_selectable_and_nothing_crashes(
        self, qgis_app, figure_pane
    ) -> None:
        pane, layout = figure_pane
        controls = pane.controls
        for item_id in ("figure-0-label", "rule-figure-0", "figure-1"):
            layout.deselectAll()
            layout.itemById(item_id).setSelected(True)
            controls.refresh()
            _settle(qgis_app, 0.02)
            assert controls.heading.text()

    def test_delete_removes_only_the_selected_item(self, qgis_app, figure_pane) -> None:
        pane, layout = figure_pane
        controls = pane.controls
        layout.itemById("figure-0").setSelected(True)
        controls.refresh()
        controls.delete_button.click()
        _settle(qgis_app, 0.05)
        assert layout.itemById("figure-0") is None
        for kept in ("figure-0-label", "rule-figure-0", "figure-1", "figure-1-label"):
            assert layout.itemById(kept) is not None, kept
        layout.deselectAll()
        layout.itemById("rule-figure-1").setSelected(True)
        controls.refresh()
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest

        QTest.keyClick(pane.preview.view, Qt.Key.Key_Delete)
        _settle(qgis_app, 0.05)
        assert layout.itemById("rule-figure-1") is None
        assert layout.itemById("figure-1") is not None
        assert layout.itemById("figure-1-label") is not None

    def test_a_deleted_figure_is_gone_from_the_exported_page(self, figure_pane) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            finish_cartograph,
            prepare_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        _pane, layout = figure_pane
        runtime = RuntimeBundle(
            javascript=b"// core",
            css=b"/* core */",
            version="0.13.1",
            sha256="0" * 64,
            cartograph_javascript=b"// carto",
            cartograph_css=b"/* carto */",
        )

        def export() -> str:
            report = FidelityReportBuilder()
            project = layout.project()
            return finish_cartograph(
                prepare_cartograph(
                    layout, project, runtime, report, frame_modes={}, raster_cache=None
                ),
                project,
                report,
            )

        html = export()
        assert ">214<" in html and ">12 km<" in html
        layout.itemById("figure-0").setText("215")
        layout.removeLayoutItem(layout.itemById("figure-1"))
        html = export()
        assert ">215<" in html and ">214<" not in html and ">12 km<" not in html


@pytest.fixture
def map_tab(qgis_app, parcels, project):
    from test_edit_tab_fit import shown_controls

    layout = _layout(parcels, project)
    scroll, controls = shown_controls(layout, "map", 340)
    controls.dpi_combo.clear()
    _settle(qgis_app, 0.2)
    yield scroll, controls
    scroll.close()


def _top_in(scroll, widget) -> int:
    return widget.mapTo(scroll.viewport(), widget.rect().topLeft()).y()


class TestSelectedItemHeader:
    @pytest.mark.parametrize(
        "kind", ["map", "text", "legend", "scale_bar", "north_arrow"]
    )
    def test_sixteen_pixels_clear_for_every_kind(
        self, qgis_app, parcels, project, kind
    ) -> None:
        from test_edit_tab_fit import shown_controls

        scroll, controls = shown_controls(_layout(parcels, project), kind, 340)
        try:
            _settle(qgis_app, 0.2)
            assert _top_in(scroll, controls.head_row) == 16
        finally:
            scroll.close()

    def test_label_and_delete_are_centred_and_delete_is_fully_hit_able(
        self, qgis_app, map_tab
    ) -> None:
        scroll, controls = map_tab
        label, delete = controls.heading, controls.delete_button
        label_mid = label.mapTo(controls, label.rect().center()).y()
        delete_mid = delete.mapTo(controls, delete.rect().center()).y()
        assert abs(label_mid - delete_mid) <= 1
        area = controls.head_row.rect()
        box = delete.geometry()
        assert area.contains(box)
        assert box.height() >= delete.sizeHint().height()
        assert box.width() >= delete.sizeHint().width()
        right = delete.mapTo(scroll.viewport(), delete.rect().topRight()).x()
        assert right <= scroll.viewport().width() - 12 + 1

    def test_content_is_inset_twelve_pixels(self, qgis_app, map_tab) -> None:
        scroll, controls = map_tab
        left = controls.canvas_button.mapTo(
            scroll.viewport(), controls.canvas_button.rect().topLeft()
        ).x()
        right = controls.edit_view_button.mapTo(
            scroll.viewport(), controls.edit_view_button.rect().topRight()
        ).x()
        assert left == 12
        assert right == scroll.viewport().width() - 12 - 1


class TestMapControls:
    @pytest.mark.parametrize("width", [300, 340, 560])
    def test_the_two_buttons_share_one_row_half_and_half(
        self, qgis_app, parcels, project, width
    ) -> None:
        from test_edit_tab_fit import shown_controls

        scroll, controls = shown_controls(_layout(parcels, project), "map", width)
        try:
            _settle(qgis_app, 0.2)
            for editing in (False, True):
                controls.set_editing(editing)
                _settle(qgis_app, 0.1)
                first, second = controls.canvas_button, controls.edit_view_button
                assert first is not second
                assert first.height() == second.height()
                assert _top_in(scroll, first) == _top_in(scroll, second)
                assert abs(first.width() - second.width()) <= 1
                left = first.mapTo(scroll.viewport(), first.rect().topLeft()).x()
                right = second.mapTo(scroll.viewport(), second.rect().topRight()).x()
                assert (
                    left < second.mapTo(scroll.viewport(), second.rect().topLeft()).x()
                )
                assert right <= scroll.viewport().width()
                assert not scroll.horizontalScrollBar().isVisible()
                for button in (first, second):
                    assert button.width() >= button.fontMetrics().horizontalAdvance(
                        button.text()
                    )
        finally:
            scroll.close()

    def test_the_form_fields_line_up_and_expand(self, qgis_app, map_tab) -> None:
        _scroll, controls = map_tab
        fields = (controls.rotation_spin, controls.mode_combo)
        lefts = {f.mapTo(controls, f.rect().topLeft()).x() for f in fields}
        rights = {f.mapTo(controls, f.rect().topRight()).x() for f in fields}
        assert len(lefts) == len(rights) == 1
        assert min(rights) == controls.width() - 12 - 1
        assert fields[0].width() > 120

    @pytest.mark.parametrize("mode, shown", [("live", False), ("static", True)])
    def test_image_resolution_only_for_an_image_map(
        self, qgis_app, parcels, project, mode, shown
    ) -> None:
        from test_edit_tab_fit import shown_controls

        from nika_onlymap_exporter.ui.cartograph.frames_list import (
            FrameRow,
            FramesList,
        )

        layout = _layout(parcels, project)
        scroll, controls = shown_controls(layout, "map", 340)
        try:
            item = controls.selected_item()
            frames = FramesList((72, 150, 300))
            frames.set_frames(
                [FrameRow(item.uuid(), "Main", "EPSG:32648", mode, 150, None)]
            )
            controls.bind_frames(frames)
            controls.refresh()
            _settle(qgis_app, 0.1)
            assert controls.dpi_combo.isVisibleTo(controls) is shown
            assert controls.dpi_label.isVisibleTo(controls) is shown
        finally:
            scroll.close()

    @pytest.mark.parametrize("width", [300, 340, 560])
    def test_no_sideways_scroll_bar_and_no_cut_label(
        self, qgis_app, parcels, project, width
    ) -> None:
        from test_edit_tab_fit import shown_controls

        scroll, controls = shown_controls(_layout(parcels, project), "map", width)
        try:
            _settle(qgis_app, 0.2)
            assert not scroll.horizontalScrollBar().isVisible()
            for label in controls._field_labels:
                if label.isVisibleTo(controls):
                    assert label.width() >= label.fontMetrics().horizontalAdvance(
                        label.text()
                    )
        finally:
            scroll.close()

    def test_the_toggle_reads_done_while_editing_and_goes_back(
        self, qgis_app, map_tab
    ) -> None:
        _scroll, controls = map_tab
        button = controls.edit_view_button
        assert button.text() == "Edit view"
        button.click()
        assert button.text() == "Done editing view"
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
        button.click()
        assert button.text() == "Edit view"
        controls.set_editing(True)
        assert button.text() == "Done editing view"
        controls.set_editing(False)
        assert button.text() == "Edit view"


class TestInspectorFollowsSelection:
    """The Edit tab shows the item that is selected on the page, and nothing
    when none is: through real clicks, whatever QGIS does or does not emit."""

    @staticmethod
    def _click(qgis_app, pane, point) -> None:
        from qgis.PyQt.QtCore import QPoint, Qt
        from qgis.PyQt.QtTest import QTest

        viewport = pane.preview.view.viewport()
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(*point))
        _settle(qgis_app, 0.15)

    def test_a_click_on_bare_page_empties_the_tab(self, qgis_app, pane) -> None:
        pane, layout = pane
        controls = pane.controls
        item = _item_of(layout, "map")
        centre = pane.preview.view.mapFromScene(item.sceneBoundingRect().center())
        self._click(qgis_app, pane, (centre.x(), centre.y()))
        assert [i.uuid() for i in layout.selectedLayoutItems()] == [item.uuid()]
        assert controls.selected_item() is item
        self._click(qgis_app, pane, (8, 8))
        assert layout.selectedLayoutItems() == []
        assert controls.selected_item() is None
        assert controls.heading.text() == NO_ITEM_TEXT
        assert not _visible(controls, controls.body)

    def test_a_layout_built_from_a_template_has_nothing_selected(
        self, qgis_app, parcels, project
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        for template in ("dashboard", "classic", "legend-below"):
            layout = _layout(parcels, project, template)
            assert layout.selectedLayoutItems() == []
            pane = PagePane()
            try:
                pane.show()
                pane.show_layout(layout)
                _settle(qgis_app, 0.1)
                assert layout.selectedLayoutItems() == []
                assert pane.controls.selected_item() is None
                assert pane.controls.heading.text() == NO_ITEM_TEXT
            finally:
                pane.close()

    def test_switching_layout_leaves_no_stale_controls(
        self, qgis_app, parcels, project, pane
    ) -> None:
        pane, layout = pane
        item = _item_of(layout, "text")
        item.setSelected(True)
        _settle(qgis_app, 0.1)
        assert pane.controls.selected_item() is item
        pane.show_layout(_layout(parcels, project, "classic"))
        _settle(qgis_app, 0.1)
        assert pane.controls.selected_item() is None
        assert pane.controls.heading.text() == NO_ITEM_TEXT
