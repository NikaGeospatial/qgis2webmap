"""Atlas "Include pages" as a field and a value, and the clear button on every
optional field.

The filter is judged by what the stored expression selects on a layer with
awkward values (a quote, the text "NULL", a real null, numbers, yes/no), and
by what the tab shows for an expression that was already there. Clearing is
judged by the stored QGIS atlas, through a tab switch, a save and reopen, and
Undo and Redo. Everything drives the real tab's real controls.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: F811 - tests take the imported `series` fixture by name
from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from atlas_fixtures import WEB_MERCATOR
from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLayoutAtlas,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication, QComboBox
from test_cartograph_dialog import (
    fake_cartograph_export,  # noqa: F401 - fixture
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)
from test_cartograph_series import series  # noqa: F401 - fixture

from nika_onlymap_exporter.core.atlas_filter import (
    Matching,
    condition_expression,
    read_condition,
)
from nika_onlymap_exporter.core.atlas_series import SeriesState
from nika_onlymap_exporter.ui.cartograph.atlas_tab import default_filename_expression

#: (name, count, ok): an apostrophe, the TEXT "NULL", a real null row, a plain
#: row. `count` is an integer field and `ok` a yes/no field.
ROWS = (
    ("O'Brien", 1, True),
    ("NULL", 2, False),
    (None, None, None),
    ("plain", 3, True),
)


def awkward_layer() -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=name:string&field=count:integer&field=ok:boolean",
        "awkward",
        "memory",
    )
    features = []
    for index, (name, count, ok) in enumerate(ROWS):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([name, count, ok])
        x = index * 1000
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, 0, x + 400, 400)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def selected_names(layer: QgsVectorLayer, expression: str) -> list[str | None]:
    request = QgsFeatureRequest().setFilterExpression(expression)
    return [f["name"] for f in layer.getFeatures(request)]


class TestTypedConditions:
    """What each choice selects, read from the layer itself."""

    def test_a_text_with_a_quote_matches_exactly_that_row(self, qgis_app) -> None:
        layer = awkward_layer()
        expression = condition_expression(layer, "name", "O'Brien", False)
        assert selected_names(layer, expression) == ["O'Brien"]

    def test_a_number_is_a_number_not_text(self, qgis_app) -> None:
        layer = awkward_layer()
        for typed in ("2", "2.0", " 2 "):
            expression = condition_expression(layer, "count", typed, False)
            assert selected_names(layer, expression) == ["NULL"], typed
        assert "'" not in condition_expression(layer, "count", "2", False)

    def test_a_real_null_and_the_text_null_are_different_things(self, qgis_app) -> None:
        layer = awkward_layer()
        null_row = condition_expression(layer, "name", "", True)
        assert selected_names(layer, null_row) == [None]
        null_text = condition_expression(layer, "name", "NULL", False)
        assert selected_names(layer, null_text) == ["NULL"]

    def test_a_yes_no_field_matches_by_truth(self, qgis_app) -> None:
        layer = awkward_layer()
        yes = condition_expression(layer, "ok", "true", False)
        no = condition_expression(layer, "ok", "false", False)
        assert selected_names(layer, yes) == ["O'Brien", "plain"]
        assert selected_names(layer, no) == ["NULL"]

    def test_a_value_the_field_cannot_hold_is_refused_in_words(self, qgis_app) -> None:
        layer = awkward_layer()
        with pytest.raises(ValueError, match="not a number"):
            condition_expression(layer, "count", "many", False)
        with pytest.raises(ValueError, match="not a number"):
            condition_expression(layer, "count", "nan", False)
        with pytest.raises(ValueError, match="Yes or No"):
            condition_expression(layer, "ok", "maybe", False)

    def test_every_choice_is_read_back_as_the_same_choice(self, qgis_app) -> None:
        layer = awkward_layer()
        for choice in (
            Matching("name", "O'Brien"),
            Matching("name", "NULL"),
            Matching("name", is_null=True),
            Matching("name", 'say "hi" \\ now'),
            Matching("count", "2"),
            Matching("count", "-7"),
            Matching("ok", "true"),
            Matching("ok", "false"),
        ):
            expression = condition_expression(
                layer, choice.field, choice.value, choice.is_null
            )
            assert read_condition(layer, expression) == choice, expression

    def test_other_expressions_are_custom_and_never_claimed(self, qgis_app) -> None:
        layer = awkward_layer()
        for expression in (
            '"name" = \'plain\' AND "count" = 3',
            '"count" > 1',
            "\"name\" <> 'plain'",
            "upper(\"name\") = 'PLAIN'",
            '"name" IS NOT NULL',
            '"name" = NULL',
            "\"count\" = '3'",  # text compared with a number field
            '"name" = 3',  # a number compared with a text field
            "\"missing\" = 'x'",
            '"name" =',
            "",
        ):
            assert read_condition(layer, expression) is None, expression

    def test_spelling_differences_do_not_change_what_is_recognised(
        self, qgis_app
    ) -> None:
        layer = awkward_layer()
        assert read_condition(layer, "name='plain'") == Matching("name", "plain")
        assert read_condition(layer, '"name"  IS  null') == Matching(
            "name", is_null=True
        )


def _awkward_series(series_factory):
    s = series_factory(atlas_on=True)
    layer = awkward_layer()
    s.project.addMapLayer(layer)
    s.built.maps[0].setLayers([layer, s.built.parcels])
    s.choose_layer(layer)
    s.tab.advanced_section.set_open(True)
    return s, layer


def _choose_mode(s, mode: str) -> None:
    combo = s.tab.filter_mode_combo
    index = combo.findData(mode)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    s.settle()


def _choose_field(s, field: str) -> None:
    combo = s.tab.filter_field_combo
    combo.setField(field)
    combo.activated.emit(combo.currentIndex())
    s.settle()


def _choose_value_item(s, text: str) -> None:
    combo = s.tab.filter_value_combo
    index = combo.findText(text)
    assert index >= 0, text
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    s.settle()


def _type_value(s, text: str) -> None:
    combo = s.tab.filter_value_combo
    line = combo.lineEdit()
    line.setText(text)
    line.editingFinished.emit()
    s.settle()


def _stored_filter(s) -> tuple[bool, str]:
    atlas = s.atlas
    return atlas.filterFeatures(), atlas.filterExpression()


class TestIncludePagesChoice:
    def test_the_default_is_all_features_and_nothing_else_is_shown(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        tab = s.tab
        assert tab.filter_mode_combo.currentText() == "All features"
        assert [tab.filter_mode_combo.itemText(i) for i in range(3)] == [
            "All features",
            "Matching a value",
            "Custom expression",
        ]
        assert not tab.filter_match.isVisible()
        assert not tab.filter_edit.isVisible()
        assert not tab.filter_clear.isVisible()
        assert _stored_filter(s) == (False, "")

    def test_a_field_and_a_value_make_a_real_comparison(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        assert s.tab.filter_match.isVisible()
        # A field alone filters nothing yet, and says nothing is filtered.
        _choose_field(s, "size")
        assert _stored_filter(s) == (False, "")
        assert s.tab.status().pages == 3
        _choose_value_item(s, "large")
        assert _stored_filter(s) == (True, "\"size\" = 'large'")
        assert s.tab.status().pages == 1
        assert s.tab.filter_count_label.text() == "1 page matches"
        assert s.tab.status_label.text() == "1 page ready"
        assert s.tab.export_button.isEnabled()

    def test_the_preview_follows_the_filter(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        _choose_value_item(s, "small")
        assert s.tab.status().pages == 2
        assert s.page_text().startswith("1 of 2")

    def test_a_typed_value_that_matches_nothing_says_so(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        _type_value(s, "enormous")
        assert _stored_filter(s) == (True, "\"size\" = 'enormous'")
        assert s.tab.status().state is SeriesState.NO_MATCH
        assert s.tab.filter_count_label.text() == "No features match your filter."
        assert s.tab.filter_count_label.isVisible()
        assert not s.tab.export_button.isEnabled()
        assert s.tab.preview_page() is None

    def test_a_value_the_field_cannot_hold_is_reported_beside_the_value(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "area")
        _type_value(s, "wide")
        tab = s.tab
        assert tab.filter_error_label.isVisible()
        assert "not a number" in tab.filter_error_label.text()
        assert tab.status().state is SeriesState.INVALID
        assert not tab.export_button.isEnabled()
        assert _stored_filter(s) == (False, "")
        _type_value(s, "500000")
        assert not tab.filter_error_label.isVisible()
        assert _stored_filter(s) == (True, '"area" = 500000')
        assert tab.status().pages == 1 and tab.export_button.isEnabled()

    def test_a_real_null_and_the_text_null_are_chosen_differently(self, series) -> None:
        s, layer = _awkward_series(series)
        _choose_mode(s, "value")
        _choose_field(s, "name")
        _choose_value_item(s, "(no value)")
        assert _stored_filter(s) == (True, '"name" IS NULL')
        assert selected_names(layer, s.atlas.filterExpression()) == [None]
        assert s.tab.status().pages == 1
        _type_value(s, "NULL")
        assert _stored_filter(s) == (True, "\"name\" = 'NULL'")
        assert selected_names(layer, s.atlas.filterExpression()) == ["NULL"]
        _type_value(s, "O'Brien")
        assert selected_names(layer, s.atlas.filterExpression()) == ["O'Brien"]

    def test_numbers_and_yes_no_fields_get_typed_values(self, series) -> None:
        s, _layer = _awkward_series(series)
        _choose_mode(s, "value")
        _choose_field(s, "count")
        _type_value(s, "3")
        assert _stored_filter(s) == (True, '"count" = 3')
        _choose_field(s, "ok")
        assert not s.tab.filter_value_combo.isEditable()
        _choose_value_item(s, "No")
        assert _stored_filter(s) == (True, '"ok" = FALSE')
        assert s.tab.status().pages == 1

    def test_a_simple_existing_condition_is_shown_as_a_choice(self, series) -> None:
        s = series(atlas_on=True)
        s.atlas.setFilterFeatures(True)
        s.atlas.setFilterExpression("\"size\" = 'small'")
        s.tab.show_layout(s.built.layout)
        tab = s.tab
        assert tab.filter_mode_combo.currentText() == "Matching a value"
        assert tab.filter_field_combo.currentField() == "size"
        assert tab.filter_value_combo.lineEdit().text() == "small"
        assert tab.filter_match.isVisible() and not tab.filter_edit.isVisible()
        assert s.undo.count() == s.baseline

    def test_any_other_expression_stays_custom_and_is_never_rewritten(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        written = "\"size\" = 'small' AND \"lot\" <> 'C-3'  "
        s.atlas.setFilterFeatures(True)
        s.atlas.setFilterExpression(written)
        s.tab.show_layout(s.built.layout)
        tab = s.tab
        assert tab.filter_mode_combo.currentText() == "Custom expression"
        assert tab.filter_edit.isVisible() and not tab.filter_match.isVisible()
        # Looking, refreshing and switching tabs and back change nothing.
        s.cartograph.open_section("checks")
        s.cartograph.open_section("atlas")
        s.settle()
        assert _stored_filter(s) == (True, written)
        assert s.undo.count() == s.baseline
        assert tab.status().pages == 1

    def test_a_filter_written_in_the_custom_editor_stays_in_the_editor(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.type_filter("\"size\" = 'small'")  # simple, but typed as an expression
        assert s.tab.filter_mode_combo.currentText() == "Custom expression"
        assert s.tab.filter_edit.isVisible()
        assert _stored_filter(s) == (True, "\"size\" = 'small'")

    def test_switching_to_a_value_choice_replaces_a_written_expression_undoably(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.type_filter("\"lot\" LIKE 'A%'")
        _choose_mode(s, "value")
        assert _stored_filter(s) == (False, "")
        assert s.tab.status().pages == 3
        s.undo.undo()
        s.settle()
        assert _stored_filter(s) == (True, "\"lot\" LIKE 'A%'")
        assert s.tab.filter_mode_combo.currentText() == "Custom expression"

    def test_a_recognised_condition_keeps_its_value_when_the_mode_is_reselected(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        _choose_value_item(s, "large")
        _choose_mode(s, "value")
        assert _stored_filter(s) == (True, "\"size\" = 'large'")

    def test_the_choice_undoes_and_redoes_whole(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        _choose_value_item(s, "large")
        s.undo.undo()
        s.settle()
        assert _stored_filter(s) == (False, "")
        assert s.tab.filter_mode_combo.currentText() == "All features"
        s.undo.redo()
        s.settle()
        assert _stored_filter(s) == (True, "\"size\" = 'large'")
        assert s.tab.filter_mode_combo.currentText() == "Matching a value"
        assert s.tab.filter_value_combo.lineEdit().text() == "large"


def _populate_every_optional_field(s) -> None:
    atlas = s.atlas
    atlas.setFilterFeatures(True)
    atlas.setFilterExpression("\"size\" = 'small'")
    atlas.setSortFeatures(True)
    atlas.setSortExpression("lot")
    atlas.setSortAscending(False)
    atlas.setPageNameExpression('"lot" || "size"')
    atlas.setFilenameExpression("'sheet_' || \"lot\"")
    s.tab.show_layout(s.built.layout)
    s.tab.advanced_section.set_open(True)
    s.settle()


def _stored_optionals(atlas: QgsLayoutAtlas) -> dict[str, object]:
    return {
        "filter": (atlas.filterFeatures(), atlas.filterExpression()),
        "sort": (
            atlas.sortFeatures(),
            atlas.sortExpression(),
            atlas.sortAscending(),
        ),
        "page names": atlas.pageNameExpression(),
        "filenames": atlas.filenameExpression(),
    }


def _defaults() -> dict[str, object]:
    """What each optional field is when cleared (QGIS's own defaults)."""
    return {
        "filter": (False, ""),
        "sort": (False, "", True),
        "page names": "",
        "filenames": default_filename_expression(),
    }


class TestClearingOptionalFields:
    BUTTONS = (
        ("filter_clear", "Clear page filter", "filter"),
        ("sort_clear", "Clear page order", "sort"),
        ("page_name_clear", "Clear page names", "page names"),
        ("filename_clear", "Clear export filenames", "filenames"),
    )

    def test_a_populated_field_has_a_named_clear_button_and_an_empty_one_has_none(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.atlas.setPageNameExpression("")
        s.tab.show_layout(s.built.layout)
        s.tab.advanced_section.set_open(True)
        s.settle()
        assert not any(
            getattr(s.tab, name).isVisible() for name, _label, _key in self.BUTTONS
        )
        _populate_every_optional_field(s)
        for name, label, _key in self.BUTTONS:
            button = getattr(s.tab, name)
            assert button.isVisible(), name
            assert button.accessibleName() == label
            assert not button.icon().isNull(), name
            # Tab reaches it; a click leaves no focus ring.
            assert button.focusPolicy() == Qt.FocusPolicy.TabFocus

    def test_the_buttons_never_sit_over_a_field(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        pairs = (
            (s.tab.filter_mode_combo, s.tab.filter_clear),
            (s.tab.sort_edit, s.tab.sort_clear),
            (s.tab.page_name_edit, s.tab.page_name_clear),
            (s.tab.filename_edit, s.tab.filename_clear),
        )
        for field, button in pairs:
            field_box = field.mapTo(s.tab, QPoint(0, 0))
            button_box = button.mapTo(s.tab, QPoint(0, 0))
            assert field_box.x() + field.width() <= button_box.x(), field

    @pytest.mark.parametrize("name,label,key", BUTTONS)
    def test_clearing_resets_the_stored_atlas_not_just_the_text(
        self, series, name, label, key
    ) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        before = _stored_optionals(s.atlas)
        getattr(s.tab, name).click()
        s.settle()
        after = _stored_optionals(s.atlas)
        assert after[key] == _defaults()[key]
        # Only the field that was cleared changed.
        for other in before:
            if other != key:
                assert after[other] == before[other], other
        assert not getattr(s.tab, name).isVisible()

    def test_the_filter_clear_includes_every_feature_again(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        assert s.tab.status().pages == 2
        s.tab.filter_clear.click()
        s.settle()
        assert s.tab.status().pages == 3
        assert s.tab.filter_mode_combo.currentText() == "All features"
        assert not s.tab.filter_match.isVisible()
        assert not s.tab.filter_edit.isVisible()

    def test_the_page_order_clear_restores_the_default_order(self, series) -> None:
        s = series(atlas_on=True)
        s.atlas.setSortFeatures(True)
        s.atlas.setSortExpression("lot")
        s.atlas.setSortAscending(False)
        s.tab.show_layout(s.built.layout)
        s.tab.advanced_section.set_open(True)
        s.settle()
        s.tab.go_to_page(1)
        assert s.page_text().startswith("2 of 3 \u00b7 B-2")  # C-3, B-2, A-1
        s.tab.sort_clear.click()
        s.settle()
        s.tab.go_to_page(1)
        assert s.page_text().startswith("2 of 3 \u00b7 A-1")  # C-3, A-1, B-2
        assert s.tab.sort_edit.expression() == ""
        assert not s.tab.direction_combo.isEnabled()

    def test_the_filename_clear_restores_qgis_own_automatic_names(self, series) -> None:
        s = series(atlas_on=True)
        automatic = s.atlas.filenameExpression()
        assert automatic.strip()
        s.atlas.setFilenameExpression("'sheet_' || \"lot\"")
        s.tab.show_layout(s.built.layout)
        s.tab.advanced_section.set_open(True)
        s.settle()
        s.tab.filename_clear.click()
        s.settle()
        assert s.atlas.filenameExpression() == automatic

    def test_clearing_by_keyboard_works(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        s.tab.page_name_clear.setFocus(Qt.FocusReason.TabFocusReason)
        assert s.tab.page_name_clear.hasFocus()
        QTest.keyClick(s.tab.page_name_clear, Qt.Key.Key_Space)
        s.settle()
        assert s.atlas.pageNameExpression() == ""
        assert s.tab.page_name_edit.expression() == ""

    def test_a_cleared_field_stays_cleared_through_a_tab_switch(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        for name, _label, _key in self.BUTTONS:
            getattr(s.tab, name).click()
            s.settle()
        s.cartograph.open_section("checks")
        s.cartograph.open_section("atlas")
        s.tab.show_layout(s.built.layout)
        s.settle()
        assert _stored_optionals(s.atlas) == _defaults()
        assert s.tab.filter_mode_combo.currentText() == "All features"
        for edit in (
            s.tab.filter_edit,
            s.tab.sort_edit,
            s.tab.page_name_edit,
        ):
            assert edit.expression() == ""
        assert s.tab.filename_edit.expression() == default_filename_expression()

    def test_cleared_settings_stay_cleared_in_a_saved_and_reopened_project(
        self, series, tmp_path
    ) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        for name, _label, _key in self.BUTTONS:
            getattr(s.tab, name).click()
            s.settle()
        target = tmp_path / "cleared.qgz"
        assert s.project.write(str(target))
        other = QgsProject()
        assert other.read(str(target))
        layout = next(
            pl for pl in other.layoutManager().printLayouts() if pl.name() == "series"
        )
        assert isinstance(layout, QgsPrintLayout)
        assert _stored_optionals(layout.atlas()) == _defaults()

    def test_clearing_undoes_and_redoes(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        populated = _stored_optionals(s.atlas)
        for name, _label, _key in self.BUTTONS:
            getattr(s.tab, name).click()
            s.settle()
        assert _stored_optionals(s.atlas) == _defaults()
        for _ in self.BUTTONS:
            s.undo.undo()
        s.settle()
        assert _stored_optionals(s.atlas) == populated
        assert s.tab.filter_clear.isVisible() and s.tab.sort_clear.isVisible()
        assert s.tab.sort_edit.expression().strip('"') == "lot"
        for _ in self.BUTTONS:
            s.undo.redo()
        s.settle()
        assert _stored_optionals(s.atlas) == _defaults()
        assert not s.tab.filter_clear.isVisible()

    def test_each_clear_is_one_undo_step(self, series) -> None:
        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        before = s.undo.count()
        s.tab.sort_clear.click()
        s.settle()
        assert s.undo.count() == before + 1


class TestResettingASourceChoice:
    def test_clearing_the_layer_returns_to_unconfigured_and_says_what_is_next(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        s.settle()
        assert s.tab.coverage_clear.isVisible()
        assert s.tab.coverage_clear.accessibleName() == "Clear layer"
        s.tab.coverage_clear.click()
        s.settle()
        assert s.atlas.coverageLayer() is None
        assert s.tab.status().state is SeriesState.CHOOSE_LAYER
        assert s.tab.status_label.text() == "Choose a layer to create pages."
        assert s.tab.coverage_combo.currentLayer() is None
        assert not s.tab.coverage_clear.isVisible()
        assert not s.tab.export_button.isEnabled()
        # No other layer was picked for the user, and the map is still theirs.
        assert s.built.maps[0].atlasDriven()
        s.undo.undo()
        s.settle()
        assert s.atlas.coverageLayer() is s.built.parcels

    def test_clearing_the_map_returns_to_unconfigured_and_says_what_is_next(
        self, series
    ) -> None:
        s = series(maps=2, atlas_on=True)
        s.settle()
        assert s.tab.map_clear.isVisible()
        assert s.tab.map_clear.accessibleName() == "Clear map"
        s.tab.map_clear.click()
        s.settle()
        assert not any(m.atlasDriven() for m in s.built.maps)
        assert s.tab.status().state is SeriesState.CHOOSE_MAP
        assert s.tab.status_label.text() == "Choose which map to update."
        assert s.tab.map_combo.currentIndex() == -1
        assert not s.tab.map_clear.isVisible()
        assert s.atlas.coverageLayer() is s.built.parcels
        s.undo.undo()
        s.settle()
        assert s.built.maps[0].atlasDriven()

    def test_a_cleared_source_stays_cleared_after_a_tab_switch(self, series) -> None:
        s = series(atlas_on=True)
        s.tab.coverage_clear.click()
        s.settle()
        s.cartograph.open_section("checks")
        s.cartograph.open_section("atlas")
        s.tab.show_layout(s.built.layout)
        s.settle()
        assert s.atlas.coverageLayer() is None
        assert s.tab.status().state is SeriesState.CHOOSE_LAYER


class TestWheelOnTheIncludePagesControls:
    def test_the_wheel_never_changes_the_new_closed_dropdowns(self, series) -> None:
        from nika_onlymap_exporter.ui.wheel_guard import (
            install_wheel_guard,
            remove_wheel_guard,
        )

        s = series(atlas_on=True)
        tab = s.tab
        tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        _choose_value_item(s, "large")
        s.app.processEvents()
        install_wheel_guard()  # the plugin installs it once, at start-up
        try:
            for widget in (
                tab.filter_mode_combo,
                tab.filter_field_combo,
                tab.filter_value_combo,
            ):
                assert widget.isVisible(), type(widget).__name__
                before = (widget.currentIndex(), widget.currentText())
                targets = [widget]
                line = widget.lineEdit()
                if line is not None:
                    targets.append(line)  # the nested child Qt delivers to
                for target in targets:
                    centre = QPoint(target.width() // 2, target.height() // 2)
                    QApplication.sendEvent(
                        target,
                        QWheelEvent(
                            QPointF(centre),
                            QPointF(target.mapToGlobal(centre)),
                            QPoint(0, 0),
                            QPoint(0, -120),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase,
                            False,
                        ),
                    )
                assert (widget.currentIndex(), widget.currentText()) == before
        finally:
            remove_wheel_guard()
        assert _stored_filter(s) == (True, "\"size\" = 'large'")
        assert isinstance(tab.filter_mode_combo, QComboBox)

    def test_typing_and_keyboard_choice_still_work_in_the_value_box(
        self, series
    ) -> None:
        s = series(atlas_on=True)
        tab = s.tab
        tab.advanced_section.set_open(True)
        _choose_mode(s, "value")
        _choose_field(s, "size")
        combo = tab.filter_value_combo
        combo.setFocus()
        QTest.keyClicks(combo.lineEdit(), "small")
        QTest.keyClick(combo.lineEdit(), Qt.Key.Key_Return)
        s.settle()
        assert _stored_filter(s) == (True, "\"size\" = 'small'")
        assert WEB_MERCATOR.isValid()


class TestOneClearActionAndEqualWidths:
    def test_each_populated_field_has_exactly_one_clear_action(self, series) -> None:
        from qgis.PyQt.QtWidgets import QLineEdit

        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        tab = s.tab
        pairs = (
            (tab.filter_edit, tab.filter_clear),
            (tab.sort_edit, tab.sort_clear),
            (tab.page_name_edit, tab.page_name_clear),
            (tab.filename_edit, tab.filename_clear),
        )
        for edit, ours in pairs:
            assert ours.isVisible()
            lines = edit.findChildren(QLineEdit)
            assert lines, "the picker's line edit was not found"
            assert not any(line.isClearButtonEnabled() for line in lines)

    def test_the_plain_fields_span_the_same_width_as_the_picker_boxes(
        self, series
    ) -> None:
        from qgis.PyQt.QtWidgets import QComboBox

        s = series(atlas_on=True)
        _populate_every_optional_field(s)
        tab = s.tab
        _choose_mode(s, "value")
        _choose_field(s, "size")
        for _ in range(3):
            s.app.processEvents()
            tab._equalise_widths()
        picker_box = tab.sort_edit.findChild(QComboBox)
        picker_right = picker_box.mapTo(tab, QPoint(picker_box.width(), 0)).x()
        for name in (
            "filter_mode_combo",
            "filter_field_combo",
            "filter_value_combo",
            "direction_combo",
        ):
            widget = getattr(tab, name)
            right = widget.mapTo(tab, QPoint(widget.width(), 0)).x()
            assert abs(right - picker_right) <= 1, (name, right, picker_right)
        assert abs(tab.filter_mode_combo.width() - tab.filter_field_combo.width()) <= 1
