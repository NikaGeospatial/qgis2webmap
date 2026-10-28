"""The Atlas tab as "Create a map series": setup, preview, readiness, outputs.

Three parcels (C-3, A-1, B-2, in that feature order, at known places) and four
point places are the independently checkable pages. Everything runs on the real
dialog with the real controls; the cartograph build is faked (`fake_cartograph_
export`) except where a test says otherwise.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("qgis.core")

from atlas_fixtures import PARCELS, PLACES, SeriesLayout, build_series_layout
from qgis.core import (
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QSize
from test_cartograph_dialog import (
    _choose_cartograph,
    _dialog,
    _wait_for,
    fake_cartograph_export,  # noqa: F401 - fixture
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)

from nika_onlymap_exporter.core.atlas_series import SeriesState

LOTS = [lot for lot, *_rest in PARCELS]  # feature order: C-3, A-1, B-2


class _Series:
    """A dialog on the Cartograph with a series layout, and ways to look at it."""

    def __init__(self, qgis_app, project, tmp_path, built: SeriesLayout) -> None:
        self.app = qgis_app
        self.project = project
        self.built = built
        project.layoutManager().addLayout(built.layout)
        project.setFileName(str(tmp_path / "series.qgz"))
        self.dialog = _dialog(project)
        self.dialog.show()
        _choose_cartograph(self.dialog)
        self.cartograph = self.dialog._cartograph
        self.cartograph.show_layout_named(built.layout.name())
        self.cartograph.open_section("atlas")
        self.tab = self.cartograph.atlas_tab
        self.undo = built.layout.undoStack().stack()
        self.settle()
        # Steps the dialog itself took on opening the layout.
        self.baseline = self.undo.count()

    def settle(self) -> None:
        self.app.processEvents()
        self.tab.refresh_now()
        self.app.processEvents()

    def close(self) -> None:
        self.dialog.close()
        self.project.setFileName("")

    @property
    def atlas(self):
        return self.built.layout.atlas()

    def choose_layer(self, layer) -> None:
        self.tab.coverage_combo.setLayer(layer)
        self.settle()

    def type_filter(self, expression: str) -> None:
        self.tab.advanced_section.set_open(True)
        self.tab.filter_edit.setExpression(expression)
        self.tab.filter_edit.fieldChanged.emit(self.tab.filter_edit.expression())
        self.settle()

    def page_text(self) -> str:
        return self.tab.position_label.text()

    def main_extent_center(self) -> tuple[float, float]:
        centre = self.built.maps[0].extent().center()
        return (centre.x(), centre.y())

    def page_image(self):
        layout = self.built.layout
        return QgsLayoutExporter(layout).renderPageToImage(0, QSize(320, 240))


@pytest.fixture
def series(qgis_app, project, tmp_path, fake_cartograph_export):  # noqa: F811
    made: list[_Series] = []

    def make(*, maps: int = 1, atlas_on: bool = False) -> _Series:
        built = build_series_layout(project, maps=maps, atlas_on=atlas_on)
        current = _Series(qgis_app, project, tmp_path, built)
        made.append(current)
        return current

    yield make
    for current in made:
        current.close()


def _parcel_center(lot: str) -> tuple[float, float]:
    """Where the parcel `lot` sits (its bounding box centre), straight from the
    fixture's own table, not from anything the atlas computed."""
    for name, _size, x, width, height in PARCELS:
        if name == lot:
            return (x + width / 2, height / 2)
    raise KeyError(lot)


class TestSetup:
    def test_off_shows_the_explanation_and_the_enable_control_only(
        self, series
    ) -> None:
        s = series()
        tab = s.tab
        assert not tab.enabled_check.isChecked()
        assert tab.enabled_check.text() == "Create a map series"
        assert "separate map for each area or location" in tab.intro_label.text()
        assert not tab.settings_box.isVisible()
        # Showing the tab changed nothing in the document.
        assert not s.atlas.enabled() and s.atlas.coverageLayer() is None
        assert s.undo.count() == s.baseline
        assert not s.built.maps[0].atlasDriven()

    def test_enabling_picks_no_layer_by_itself(self, series) -> None:
        s = series()
        s.tab.enabled_check.setChecked(True)
        s.settle()
        assert s.atlas.enabled() and s.atlas.coverageLayer() is None
        assert s.tab.coverage_combo.currentLayer() is None
        assert s.tab.status_label.text() == "Choose a layer to create pages."
        assert s.tab.status().state is SeriesState.CHOOSE_LAYER
        assert not s.tab.export_button.isEnabled()
        assert not s.tab.print_button.isEnabled()

    def test_one_layer_choice_is_enough_with_one_map(self, series) -> None:
        s = series()
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        tab = s.tab
        assert s.atlas.coverageLayer() is s.built.parcels
        assert s.built.maps[0].atlasDriven()
        assert tab.source_note.text() == "3 areas"
        assert tab.map_summary.text() == "Updating map: Main map"
        assert not tab.map_chooser.isVisible()
        assert tab.status_label.text() == "3 pages ready"
        assert tab.export_button.isEnabled() and tab.print_button.isEnabled()
        assert tab.scope_label.text() == "Exports all 3 pages."
        # Choosing the layer and joining the map is one step to take back.
        s.undo.undo()
        s.settle()
        assert s.atlas.coverageLayer() is None
        assert not s.built.maps[0].atlasDriven()

    def test_points_are_locations_not_areas(self, series) -> None:
        s = series()
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.places)
        assert s.tab.source_note.text() == "4 locations"
        assert s.tab.status_label.text() == "4 pages ready"

    def test_an_existing_valid_choice_is_kept_and_opening_does_not_edit(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.built.maps[0].setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
        s.built.maps[0].setAtlasMargin(0.37)
        s.atlas.setFilenameExpression("'sheet_' || \"lot\"")
        s.tab.show_layout(s.built.layout)
        assert s.tab.coverage_combo.currentLayer() is s.built.parcels
        assert s.atlas.coverageLayer() is s.built.parcels
        main = s.built.maps[0]
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Fixed
        assert main.atlasMargin() == pytest.approx(0.37)
        assert s.atlas.filenameExpression() == "'sheet_' || \"lot\""
        assert s.undo.count() == s.baseline

    def test_disabling_keeps_the_settings(self, series) -> None:
        s = series(atlas_on=True)
        s.type_filter("\"lot\" <> 'A-1'")
        s.tab.enabled_check.setChecked(False)
        s.settle()
        assert not s.atlas.enabled()
        assert s.atlas.coverageLayer() is s.built.parcels
        assert s.atlas.filterExpression() == "\"lot\" <> 'A-1'"
        assert s.built.maps[0].atlasDriven()
        s.tab.enabled_check.setChecked(True)
        s.settle()
        assert s.tab.status().pages == 2

    def test_a_layer_with_no_features_says_so(self, series) -> None:
        from atlas_fixtures import build_parcels

        s = series()
        empty = build_parcels("empty")
        empty.dataProvider().truncate()
        s.project.addMapLayer(empty)
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(empty)
        assert "no features" in s.tab.source_note.text()
        assert s.tab.status().state is SeriesState.LAYER_UNUSABLE
        assert not s.tab.export_button.isEnabled()

    def test_a_removed_layer_is_reported_and_choosing_another_recovers(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.settle()
        assert s.tab.status().state is SeriesState.READY
        s.project.removeMapLayer(s.built.parcels.id())
        assert _wait_for(
            s.app, lambda: s.tab.status().state is SeriesState.LAYER_MISSING
        )
        assert "no longer available" in s.tab.source_note.text()
        assert not s.tab.export_button.isEnabled()
        assert s.tab.enabled_check.isChecked()
        s.choose_layer(s.built.places)
        assert s.tab.status().state is SeriesState.READY
        assert s.atlas.enabled()


class TestMaps:
    def test_several_maps_ask_which_one_and_pick_none_for_the_user(
        self, series
    ) -> None:
        s = series(maps=2)
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        assert not any(item.atlasDriven() for item in s.built.maps)
        assert s.tab.status().state is SeriesState.CHOOSE_MAP
        assert s.tab.status_label.text() == "Choose which map to update."
        assert s.tab.map_chooser.isVisible()
        assert not s.tab.map_summary.isVisible()
        assert not s.tab.export_button.isEnabled()
        second = s.built.maps[1]
        s.tab.map_combo.setCurrentIndex(s.tab.map_combo.findData(second.uuid()))
        s.settle()
        assert second.atlasDriven() and not s.built.maps[0].atlasDriven()
        assert s.tab.status().state is SeriesState.READY

    def test_a_deliberately_selected_map_is_used(self, series) -> None:
        s = series(maps=2)
        s.built.maps[1].setSelected(True)
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        assert s.built.maps[1].atlasDriven() and not s.built.maps[0].atlasDriven()

    def test_an_existing_association_is_kept(self, series) -> None:
        s = series(maps=2, atlas_on=True)
        s.settle()
        assert s.tab.map_combo.currentData() == s.built.maps[0].uuid()
        s.choose_layer(s.built.places)
        assert s.built.maps[0].atlasDriven() and not s.built.maps[1].atlasDriven()

    def test_a_layout_with_no_map_explains_and_offers_templates(
        self, series, monkeypatch
    ) -> None:
        s = series(maps=0)
        asked = []
        monkeypatch.setattr(s.cartograph, "create_from_canvas", lambda: asked.append(1))
        s.tab._host.browse_templates = s.cartograph.create_from_canvas
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        assert s.tab.no_map.isVisible()
        assert "needs a map" in s.tab.no_map_label.text()
        s.tab.templates_button.click()
        assert asked == [1]
        assert not s.tab.export_button.isEnabled()


class TestEnableFollowsTheLayout:
    def test_choosing_a_layer_on_a_series_that_is_off_shows_it_on(self, series) -> None:
        s = series(maps=2)
        assert not s.tab.enabled_check.isChecked()
        s.choose_layer(s.built.parcels)
        assert s.atlas.enabled()
        assert s.tab.enabled_check.isChecked()
        assert s.tab.settings_box.isVisibleTo(s.tab)
        assert s.tab.map_chooser.isVisibleTo(s.tab)


class TestFraming:
    def test_new_series_fits_areas_with_the_default_margin(self, series) -> None:
        s = series()
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        main = s.built.maps[0]
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Auto
        assert main.atlasMargin() == pytest.approx(0.1)

    def test_changing_layer_does_not_reset_framing(self, series) -> None:
        s = series(atlas_on=True)
        main = s.built.maps[0]
        main.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
        main.setAtlasMargin(0.25)
        s.tab.show_layout(s.built.layout)
        s.choose_layer(s.built.places)
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Fixed
        assert main.atlasMargin() == pytest.approx(0.25)

    def test_framing_changes_undo_and_redo(self, series) -> None:
        s = series(atlas_on=True)
        s.settle()
        main = s.built.maps[0]
        combo = s.tab.framing_combo
        combo.setCurrentIndex(
            combo.findData(int(QgsLayoutItemMap.AtlasScalingMode.Fixed))
        )
        s.settle()
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Fixed
        s.undo.undo()
        s.settle()
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Auto
        assert combo.currentData() == int(QgsLayoutItemMap.AtlasScalingMode.Auto)
        s.undo.redo()
        s.settle()
        assert main.atlasScalingMode() == QgsLayoutItemMap.AtlasScalingMode.Fixed


class TestPreview:
    def test_starts_on_page_one_and_the_main_map_follows_the_page(self, series) -> None:
        s = series(atlas_on=True)
        tab = s.tab
        assert s.page_text().startswith("1 of 3")
        assert "C-3" in s.page_text()
        assert not tab.previous_button.isEnabled()
        assert tab.next_button.isEnabled()
        expected = _parcel_center("C-3")
        centre = s.main_extent_center()
        assert centre[0] == pytest.approx(expected[0], abs=1)
        assert centre[1] == pytest.approx(expected[1], abs=1)

    def test_navigating_moves_the_main_renderer_through_every_page(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        tab = s.tab
        steps = s.undo.count()
        images = []
        for index, lot in enumerate(LOTS):
            assert s.page_text().startswith(f"{index + 1} of 3 · {lot}")
            centre = s.main_extent_center()
            expected = _parcel_center(lot)
            assert centre[0] == pytest.approx(expected[0], abs=1)
            assert centre[1] == pytest.approx(expected[1], abs=1)
            assert s.built.label.currentText() == f"Lot {lot} -- page {index + 1}"
            images.append(s.page_image())
            if index < 2:
                tab.next_button.click()
        assert not tab.next_button.isEnabled() and tab.previous_button.isEnabled()
        assert images[0] != images[1] != images[2]
        tab.previous_button.click()
        tab.previous_button.click()
        assert s.page_text().startswith("1 of 3")
        assert s.page_image() == images[0]
        # Moving between pages is not an edit.
        assert s.undo.count() == steps

    def test_navigating_does_not_save_the_cartograph(
        self,
        series,
        fake_cartograph_export,  # noqa: F811
    ) -> None:
        s = series(atlas_on=True)
        s.cartograph._autosave_timer.stop()
        saved = len(fake_cartograph_export.calls)
        s.tab.next_button.click()
        s.tab.next_button.click()
        s.tab.previous_button.click()
        s.app.processEvents()
        assert not s.cartograph._autosave_timer.isActive()
        assert len(fake_cartograph_export.calls) == saved

    def test_leaving_the_tab_puts_the_layout_back(self, series) -> None:
        s = series(atlas_on=True)
        main = s.built.maps[0]
        # The extent the map was authored with: the parcels, grown by 300 m.
        authored = QgsRectangle(s.built.parcels.extent())
        authored.grow(300)
        shown = QgsRectangle(main.extent())
        s.tab.next_button.click()
        assert main.extent() != authored and main.extent() != shown
        s.cartograph.open_section("layout")
        s.app.processEvents()
        assert s.tab.preview_page() is None
        assert main.extent() == authored
        s.cartograph.open_section("atlas")
        s.app.processEvents()
        assert s.tab.preview_page() == 1
        assert s.page_text().startswith("2 of 3")

    def test_the_current_feature_stays_through_a_setting_change(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.next_button.click()  # A-1
        assert "A-1" in s.page_text()
        s.type_filter("\"lot\" <> 'C-3'")
        assert s.page_text().startswith("1 of 2 · A-1")
        # A feature the filter drops starts the series over.
        s.type_filter("\"lot\" = 'B-2'")
        assert s.page_text().startswith("1 of 1 · B-2")

    def test_page_names_fall_back_to_page_numbers(self, series) -> None:
        s = series(atlas_on=True)
        s.atlas.setPageNameExpression("")
        s.tab.show_layout(s.built.layout)
        assert s.page_text().startswith("1 of 3 \u00b7 ")
        assert s.page_text().endswith("C-3") or s.page_text().endswith("Page 1")

    def test_a_filter_matching_nothing_says_so_and_releases_the_preview(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        main = s.built.maps[0]
        s.tab.next_button.click()
        s.type_filter("\"lot\" = 'nowhere'")
        assert s.tab.status_label.text() == "No features match your filter."
        assert s.tab.preview_page() is None
        assert not s.tab.export_button.isEnabled()
        s.type_filter("\"lot\" = 'A-1'")
        assert s.tab.status_label.text() == "1 page ready"
        assert main.atlasDriven()

    def test_an_invalid_expression_is_reported_beside_the_field_and_recovers(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.type_filter('"lot" =')
        tab = s.tab
        assert tab.filter_error_label.isVisible()
        assert tab.filter_error_label.text().startswith("Not a valid expression")
        assert tab.advanced_section.is_open()
        assert tab.status_label.text().startswith("Couldn't prepare the series")
        assert not tab.export_button.isEnabled() and not tab.print_button.isEnabled()
        s.type_filter("\"lot\" = 'A-1'")
        assert not tab.filter_error_label.isVisible()
        assert tab.export_button.isEnabled()

    def test_stale_preparation_never_overwrites_newer_settings(self, series) -> None:
        s = series(atlas_on=True)
        tab = s.tab
        tab.advanced_section.set_open(True)
        for expression in ("\"lot\" = 'A-1'", "\"lot\" <> 'A-1'"):
            tab.filter_edit.setExpression(expression)
            tab.filter_edit.fieldChanged.emit(tab.filter_edit.expression())
        # Two requests queued, none run yet: only the newer setup may show.
        assert tab.status_label.text() == "Preparing preview…"
        assert _wait_for(s.app, lambda: tab.status_label.text() == "2 pages ready")
        assert tab.status().pages == 2

    def test_page_order_by_a_plain_field(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        s.tab.sort_edit.setField("lot")
        s.tab.sort_edit.fieldChanged.emit("lot")
        s.settle()
        # C-3 was showing; it is still showing, now last in A-1, B-2, C-3.
        assert s.page_text().startswith("3 of 3 \u00b7 C-3")
        s.tab.previous_button.click()
        assert s.page_text().startswith("2 of 3 \u00b7 B-2")
        s.tab.direction_combo.setCurrentIndex(1)
        s.settle()
        assert s.atlas.sortAscending() is False
        # B-2 stays showing, now in the middle of C-3, B-2, A-1 as well.
        assert s.page_text().startswith("2 of 3 \u00b7 B-2")
        s.tab.previous_button.click()
        assert s.page_text().startswith("1 of 3 \u00b7 C-3")

    def test_several_sheets_per_feature_are_counted_as_sheets(self, series) -> None:
        s = series(atlas_on=True)
        second = QgsLayoutItemPage(s.built.layout)
        second.setPageSize("A4")
        s.built.layout.pageCollection().addPage(second)
        s.tab.show_layout(s.built.layout)
        assert s.tab.status_label.text() == "3 pages ready (2 sheets each)"
        assert s.tab.scope_label.text() == "Exports all 6 sheets."


class TestLayout:
    def test_the_tab_reads_in_order_of_importance(self, series) -> None:
        """What it produces, the source, the preview, then export and print,
        and only then the optional sections."""
        s = series(atlas_on=True)
        tab = s.tab
        s.app.processEvents()

        def top(widget) -> int:
            return widget.mapTo(tab, widget.rect().topLeft()).y()

        order = [
            tab.intro_label,
            tab.enabled_check,
            tab.coverage_combo,
            tab.map_summary,
            tab.status_label,
            tab.navigator,
            tab.export_button,
            tab.print_button,
            tab.framing_section,
            tab.advanced_section,
        ]
        tops = [top(widget) for widget in order]
        assert tops == sorted(tops) and len(set(tops)) == len(tops)
        # Collapsed until something in them is set or wrong.
        assert not tab.advanced_section.is_open()
        assert not tab.framing_section.is_open()

    def test_nothing_is_clipped_in_a_narrow_sidebar(self, series) -> None:
        s = series(atlas_on=True)
        tab = s.tab
        tab.resize(250, 900)
        s.type_filter('"lot" =')  # also opens Advanced settings
        s.app.processEvents()
        for widget in (
            tab.coverage_combo,
            tab.export_button,
            tab.print_button,
            tab.position_label,
            tab.filter_error_label,
            tab.filter_edit,
        ):
            right = widget.mapTo(tab, widget.rect().topRight()).x()
            assert right <= tab.width(), widget.objectName() or widget


class TestReadiness:
    def _static(self, s) -> None:
        item_id = s.built.maps[0].uuid()
        mode = s.cartograph._frame_rows[item_id]["mode"]
        mode.setCurrentIndex(mode.findData("static"))
        s.settle()

    def test_an_image_map_still_exports_the_native_pdf(self, series) -> None:
        s = series(atlas_on=True)
        self._static(s)
        status = s.tab.status()
        assert status.state is SeriesState.READY
        assert status.pdf.ok
        assert not status.images.ok and not status.html.ok and not status.print.ok
        assert [r.name for r in status.html.needs_interactive] == ["Main map"]
        assert s.tab.export_button.isEnabled()
        assert not s.tab.print_button.isEnabled()
        assert '"Main map"' in s.tab.actions_note.text()
        assert s.tab.actions_note.isVisible() and s.tab.switch_button.isVisible()
        assert s.built.maps[0].atlasDriven()

    def test_the_saved_page_count_gates_nothing(self, series) -> None:
        s = series(atlas_on=True)
        for pages in (None, 0, 7):
            s.tab.show_outcome((), pages=pages)
            assert s.tab.export_button.isEnabled()
            assert s.tab.print_button.isEnabled()

    def test_switch_to_interactive_is_explicit_undoable_and_refreshes(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        self._static(s)
        item_id = s.built.maps[0].uuid()
        s.tab.switch_button.click()
        s.app.processEvents()
        assert s.cartograph._frame_mode(item_id) == "live"
        assert s.tab.status().html.ok and s.tab.print_button.isEnabled()
        assert not s.tab.switch_button.isVisible()
        s.undo.undo()
        s.app.processEvents()
        assert s.cartograph._frame_mode(item_id) == "static"
        assert not s.tab.print_button.isEnabled()
        s.undo.redo()
        s.app.processEvents()
        assert s.cartograph._frame_mode(item_id) == "live"

    def test_nothing_changes_a_maps_mode_but_that_action(self, series) -> None:
        s = series()
        item_id = s.built.maps[0].uuid()
        s.cartograph._frame_rows[item_id]["mode"].setCurrentIndex(1)
        before = s.cartograph._frame_mode(item_id)
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        s.tab.next_button.click()
        s.tab.show_layout(s.built.layout)
        assert s.cartograph._frame_mode(item_id) == before == "static"


class TestExport:
    def test_the_format_choice_explains_each_format_and_blocks_by_format(
        self, series
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph.series_export_dialog import (
            SeriesExportDialog,
        )

        s = series(atlas_on=True)
        item_id = s.built.maps[0].uuid()
        mode = s.cartograph._frame_rows[item_id]["mode"]
        mode.setCurrentIndex(mode.findData("static"))
        s.settle()
        dialog = SeriesExportDialog(
            s.tab._current_status,
            s.tab._switch_maps,
            lambda: (),
            s.tab,
        )
        pdf, images, html = dialog.rows
        assert pdf.radio.isChecked() and pdf.radio.isEnabled()
        assert "PDF" in pdf.radio.text()
        assert not images.radio.isEnabled() and not html.radio.isEnabled()
        assert "Main map" in html.reason.text()
        assert "Interactive" in html.reason.text()
        assert html.switch.isVisibleTo(dialog)
        assert not pdf.switch.isVisibleTo(dialog)
        assert dialog.scope.text() == "Exports all 3 pages."
        assert dialog.chosen_format() == "pdf"
        html.switch.click()
        assert html.radio.isEnabled() and images.radio.isEnabled()
        assert not html.switch.isVisibleTo(dialog)
        html.radio.setChecked(True)
        assert dialog.chosen_format() == "html"
        dialog.close()

    def test_the_series_pdf_has_one_page_per_feature_for_an_image_map(
        self, series, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.exporters.print_pdf import export_print_pdf

        s = series(atlas_on=True)
        item_id = s.built.maps[0].uuid()
        mode = s.cartograph._frame_rows[item_id]["mode"]
        mode.setCurrentIndex(mode.findData("static"))
        s.settle()
        target = tmp_path / "series.pdf"
        export_print_pdf(
            s.built.layout,
            PrintSettings(),
            target,
            s.cartograph.state.page_choice(s.cartograph.state.cartograph_layout),
            atlas=True,
            refresh_counts=False,
        )
        data = target.read_bytes()
        assert len(re.findall(rb"/Type\s*/Page\b(?!s)", data)) == 3

    def test_the_export_choice_runs_the_matching_path(
        self, series, monkeypatch
    ) -> None:
        s = series(atlas_on=True)
        ran: list[str] = []
        s.tab._host.export_pdf = lambda: ran.append("pdf")
        s.tab._host.open_pages = ran.append

        def answering(key: str):
            class _Dialog:
                def __init__(self, *_args: object) -> None:
                    pass

                def exec(self) -> int:
                    return 1

                def chosen_format(self) -> str:
                    return key

            return _Dialog

        for key in ("pdf", "images", "html"):
            monkeypatch.setattr(
                "nika_onlymap_exporter.ui.cartograph.series_export_dialog."
                "SeriesExportDialog",
                answering(key),
            )
            s.tab.export_button.click()
        assert ran == ["pdf", "zip", "html"]

    def test_print_series_opens_every_page_once_saved(
        self, series, monkeypatch
    ) -> None:
        s = series(atlas_on=True)
        target = s.cartograph._current_path()
        opened = []
        monkeypatch.setattr(s.cartograph, "_open_url", opened.append)
        assert _wait_for(s.app, lambda: target is not None and target.exists())
        s.tab.print_button.click()
        assert _wait_for(s.app, lambda: len(opened) == 1)
        assert opened[0].query() == "atlas=print"


class TestPersistence:
    def test_advanced_settings_are_preserved_and_shown(self, series) -> None:
        s = series(atlas_on=True)
        atlas = s.atlas
        atlas.setSortFeatures(True)
        atlas.setSortExpression('upper("lot")')
        atlas.setSortAscending(False)
        atlas.setFilterFeatures(True)
        atlas.setFilterExpression("\"size\" = 'small'")
        atlas.setFilenameExpression("'sheet_' || \"lot\"")
        atlas.setPageNameExpression("\"lot\" || '!'")
        s.tab.show_layout(s.built.layout)
        tab = s.tab
        assert tab.sort_edit.currentField()[0] == 'upper("lot")'
        assert tab.direction_combo.currentData() is False
        assert tab.filter_edit.expression() == "\"size\" = 'small'"
        assert tab.filename_edit.expression() == "'sheet_' || \"lot\""
        assert tab.page_name_edit.expression() == "\"lot\" || '!'"
        assert tab.advanced_section.is_open()
        assert s.undo.count() == s.baseline
        assert atlas.sortExpression() == 'upper("lot")'

    def test_edits_undo_and_redo_whole(self, series) -> None:
        s = series()
        s.tab.enabled_check.setChecked(True)
        s.choose_layer(s.built.parcels)
        s.type_filter("\"lot\" <> 'A-1'")
        assert s.tab.status().pages == 2
        s.undo.undo()  # the filter
        s.settle()
        assert s.tab.status().pages == 3 and not s.atlas.filterFeatures()
        s.undo.undo()  # layer + map
        s.settle()
        assert s.atlas.coverageLayer() is None and not s.built.maps[0].atlasDriven()
        s.undo.redo()
        s.undo.redo()
        s.settle()
        assert s.tab.status().pages == 2

    def test_a_saved_and_reopened_project_shows_the_same_series(
        self, series, tmp_path, qgis_app
    ) -> None:
        from qgis.core import QgsPrintLayout, QgsProject

        s = series(atlas_on=True)
        s.type_filter("\"lot\" <> 'A-1'")
        s.atlas.setFilenameExpression("'sheet_' || \"lot\"")
        target = tmp_path / "reopened.qgz"
        assert s.project.write(str(target))
        other = QgsProject()
        assert other.read(str(target))
        layout = next(
            pl for pl in other.layoutManager().printLayouts() if pl.name() == "series"
        )
        assert isinstance(layout, QgsPrintLayout)
        atlas = layout.atlas()
        assert atlas.enabled() and atlas.coverageLayer().name() == "parcels"
        assert atlas.filterExpression() == "\"lot\" <> 'A-1'"
        assert atlas.filenameExpression() == "'sheet_' || \"lot\""
        assert any(
            isinstance(i, QgsLayoutItemMap) and i.atlasDriven() for i in layout.items()
        )

    def test_source_data_is_never_modified(self, series) -> None:
        s = series(atlas_on=True)
        before = [f.attributes() for f in s.built.parcels.getFeatures()]
        s.tab.next_button.click()
        s.type_filter("\"lot\" <> 'A-1'")
        after = [f.attributes() for f in s.built.parcels.getFeatures()]
        assert before == after
        assert not s.built.parcels.isEditable()
        assert [f["name"] for f in s.built.places.getFeatures()] == [
            name for name, _x, _y in PLACES
        ]
