"""The Edit tab's empty state, the statistic callout entry in the Add item
menu, and the selected item's header and map controls.

Real widgets, judged by what is visible: what shows with nothing selected, how
a callout is created, where the header and the buttons sit.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from qgis.PyQt.QtCore import QRectF
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


def _callout_words(layout):
    from nika_onlymap_exporter.ui.cartograph.callouts import layout_callouts

    return [(c.value, c.label) for c in layout_callouts(layout)]


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
            controls.callout_controls,
            controls.callout_controls.value_edit,
        ):
            assert not _visible(controls, hidden), hidden

    def test_existing_callouts_do_not_bring_the_form_back(self, qgis_app, pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.callouts import add_callout

        pane, layout = pane
        assert add_callout(layout, QRectF(20, 20, 50, 30), "12 km", "of road")
        layout.deselectAll()
        pane.controls.refresh()
        assert not _visible(pane.controls, pane.controls.callout_controls)
        assert pane.controls.heading.text() == NO_ITEM_TEXT


class TestStatisticCalloutMenuEntry:
    def test_offered_on_every_layout(self, qgis_app, parcels, project, pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

        pane, _ = pane
        action = _menu_action(pane, "Statistic callout")
        assert action is not None and action.isEnabled()
        classic = PagePane()
        try:
            classic.show()
            classic.show_layout(_layout(parcels, project, "classic"))
            other = _menu_action(classic, "Statistic callout")
            assert other is not None and other.isEnabled()
        finally:
            classic.close()

    def test_a_click_on_the_page_places_it_and_selects_it(self, qgis_app, pane) -> None:
        from test_preview_item_editing import _click_page

        pane, layout = pane
        controls = pane.controls
        _menu_action(pane, "Statistic callout").trigger()
        assert layout.selectedLayoutItems() == []
        _click_page(qgis_app, pane, 60.0, 150.0)
        assert len(_callout_words(layout)) == 1
        selected = layout.selectedLayoutItems()
        assert len(selected) == 1
        form = controls.callout_controls
        assert _visible(controls, form)
        assert _visible(controls, form.remove_button)
        assert _visible(controls, controls.body)
        assert _visible(controls, controls.delete_button)

    def test_selecting_an_existing_callout_shows_its_fields(self, pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.callouts import (
            add_callout,
            find_part,
        )

        pane, layout = pane
        assert add_callout(layout, QRectF(20, 20, 50, 30), "12 km", "of road")
        layout.deselectAll()
        find_part(layout, 0, False).setSelected(True)
        controls = pane.controls
        controls.refresh()
        form = controls.callout_controls
        assert _visible(controls, form)
        assert form.value_edit.text() == "12 km"
        assert form.label_edit.text() == "of road"
        assert _visible(controls, controls.text_size_row)
        assert _visible(controls, controls.bold_button)

    def test_the_entry_stays_enabled_with_many_callouts(self, pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.callouts import add_callout

        pane, layout = pane
        for n in range(5):
            assert add_callout(layout, QRectF(20, 20 + 20 * n, 50, 30), str(n), "")
        action = _menu_action(pane, "Statistic callout")
        assert action is not None and action.isEnabled()

    def test_picking_something_else_hides_the_form(self, pane) -> None:
        from nika_onlymap_exporter.ui.cartograph.callouts import add_callout

        pane, layout = pane
        assert add_callout(layout, QRectF(20, 20, 50, 30), "5", "")
        controls = pane.controls
        item = _item_of(layout, "map")
        item.setSelected(True)
        controls.refresh()
        assert not _visible(controls, controls.callout_controls)
        layout.deselectAll()
        controls.refresh()
        assert not _visible(controls, controls.callout_controls)


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
