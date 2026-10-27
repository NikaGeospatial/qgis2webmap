"""Page settings as edits to the QGIS layout: Title, Attribution and Theme.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import build_survey_layout
from qgis.core import (
    QgsExpressionContextUtils,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor

from nika_onlymap_exporter.core.layout_reader import TEXT_KIND_PROPERTY
from nika_onlymap_exporter.core.page_settings import (
    apply_theme,
    attribution_state,
    capture_styles,
    current_theme,
    set_attribution,
    set_title,
    surface_of,
    title_item,
    title_state,
)
from nika_onlymap_exporter.core.page_theme import (
    MINIMUM_CONTRAST,
    THEMES,
    surface_contrast,
)

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def survey(project, tmp_path):
    built = build_survey_layout(project, tmp_path)
    built.title_label.setCustomProperty(TEXT_KIND_PROPERTY, "title")
    built.title_label.setText("[% @project_title %]")
    credit = QgsLayoutItemLabel(built.layout)
    built.layout.addLayoutItem(credit)
    credit.setText("Data: parcels 2026")
    credit.setCustomProperty(TEXT_KIND_PROPERTY, "attribution")
    credit.attemptMove(QgsLayoutPoint(10, 185, QgsUnitTypes.LayoutMillimeters))
    credit.attemptResize(QgsLayoutSize(100, 8, QgsUnitTypes.LayoutMillimeters))
    built.attribution_label = credit
    built.layout.undoStack().stack().clear()
    return built


def _variable(layout) -> str | None:
    scope = QgsExpressionContextUtils.layoutScope(layout)
    return (
        scope.variable("project_title") if scope.hasVariable("project_title") else None
    )


class TestTitle:
    def test_a_project_title_expression_is_kept_and_the_variable_set(
        self, survey
    ) -> None:
        layout, label = survey.layout, survey.title_label
        assert title_state(layout).mode == "variable"
        assert set_title(layout, "Park atlas", "Edit page title")
        assert label.text() == "[% @project_title %]"
        assert label.currentText() == "Park atlas"
        assert survey.project.title() == "Survey UTM"
        assert title_state(layout).value == "Park atlas"

    def test_undo_and_redo(self, survey) -> None:
        layout, label = survey.layout, survey.title_label
        stack = layout.undoStack().stack()
        set_title(layout, "Park atlas", "Edit page title")
        stack.undo()
        assert _variable(layout) is None
        assert label.currentText() == "Survey UTM"
        stack.redo()
        assert label.currentText() == "Park atlas"

    def test_typing_is_one_undo_step(self, survey) -> None:
        layout = survey.layout
        stack = layout.undoStack().stack()
        for typed in ("P", "Pa", "Par", "Park"):
            set_title(layout, typed, "Edit page title")
        assert stack.count() == 1
        stack.undo()
        assert _variable(layout) is None

    def test_blank_returns_to_the_project_title(self, survey) -> None:
        set_title(survey.layout, "Park atlas", "Edit page title")
        set_title(survey.layout, "", "Edit page title")
        assert survey.title_label.currentText() == "Survey UTM"

    def test_a_plain_title_item_is_edited_itself(self, survey) -> None:
        layout, label = survey.layout, survey.title_label
        label.setText("Old heading")
        stack = layout.undoStack().stack()
        assert title_state(layout).value == "Old heading"
        assert set_title(layout, "New heading", "Edit page title")
        assert label.text() == "New heading"
        assert _variable(layout) is None
        stack.undo()
        assert label.text() == "Old heading"
        stack.redo()
        assert label.text() == "New heading"

    def test_another_expression_is_never_flattened(self, survey) -> None:
        layout, label = survey.layout, survey.title_label
        label.setText("[% 'Sheet ' || @atlas_featurenumber %]")
        state = title_state(layout)
        assert state.dynamic
        set_title(layout, "Typed", "Edit page title")
        assert label.text() == "[% 'Sheet ' || @atlas_featurenumber %]"

    def test_an_unmarked_label_is_not_taken_for_the_title(self, survey) -> None:
        survey.title_label.setCustomProperty(TEXT_KIND_PROPERTY, "")
        survey.title_label.setId("Title card")
        assert title_item(survey.layout) is None

    def test_a_template_item_named_title_is_the_title(self, survey) -> None:
        survey.title_label.setCustomProperty(TEXT_KIND_PROPERTY, "")
        survey.title_label.setId("title")
        assert title_item(survey.layout) is survey.title_label

    def test_other_layouts_and_the_project_title_are_untouched(
        self, survey, project
    ) -> None:
        other = QgsPrintLayout(project)
        other.initializeDefaults()
        set_title(survey.layout, "Park atlas", "Edit page title")
        assert _variable(other) is None
        assert project.title() == "Survey UTM"


class TestAttribution:
    def test_a_plain_attribution_item_is_edited_with_undo(self, survey) -> None:
        layout = survey.layout
        stack = layout.undoStack().stack()
        assert attribution_state(layout).value == "Data: parcels 2026"
        assert set_attribution(layout, "Data: cadastre", "Edit page attribution")
        assert survey.attribution_label.text() == "Data: cadastre"
        stack.undo()
        assert survey.attribution_label.text() == "Data: parcels 2026"

    def test_an_expression_is_left_alone(self, survey) -> None:
        survey.attribution_label.setText("[% @project_title %] data")
        assert attribution_state(survey.layout).dynamic
        assert not set_attribution(survey.layout, "x", "Edit page attribution")
        assert survey.attribution_label.text() == "[% @project_title %] data"

    def test_a_layout_without_one_has_nothing_to_edit(self, survey) -> None:
        survey.layout.removeLayoutItem(survey.attribution_label)
        assert attribution_state(survey.layout).mode == "none"
        assert not set_attribution(survey.layout, "x", "Edit page attribution")


def _texts(layout):
    return [item for item in layout.items() if isinstance(item, QgsLayoutItemLabel)]


class TestTheme:
    def test_the_page_and_furniture_take_the_theme(self, survey) -> None:
        layout = survey.layout
        theme = THEMES["dark"]
        assert apply_theme(layout, "dark", "Change page theme")
        assert current_theme(layout) == "dark"
        page = layout.pageCollection().page(0)
        assert page.pageStyleSymbol().color().name() == theme.page
        for text in _texts(layout):
            assert (
                surface_contrast(
                    text.textFormat().color().name(), surface_of(layout, text)
                )
                >= MINIMUM_CONTRAST
            )
        title_style = survey.legend.style(qgis_core.QgsLegendStyle.Style.SymbolLabel)
        assert title_style.textFormat().color().name() != "#000000"
        assert survey.scalebar.textFormat().color().name() != "#000000"
        assert survey.north.svgFillColor().name() != "#ffffff" or theme.dark

    @pytest.mark.parametrize("theme_id", sorted(THEMES))
    def test_every_theme_keeps_text_readable(self, survey, theme_id) -> None:
        layout = survey.layout
        apply_theme(layout, theme_id, "Change page theme")
        for text in _texts(layout):
            ratio = surface_contrast(
                text.textFormat().color().name(), surface_of(layout, text)
            )
            assert ratio >= MINIMUM_CONTRAST, (theme_id, text.text(), ratio)

    def test_map_symbols_and_layers_are_untouched(self, survey) -> None:
        before = survey.parcels.renderer().symbol().color().name()
        layers = [layer.id() for layer in survey.main_map.layers()]
        apply_theme(survey.layout, "dark", "Change page theme")
        assert survey.parcels.renderer().symbol().color().name() == before
        assert [layer.id() for layer in survey.main_map.layers()] == layers

    def test_default_puts_everything_back_exactly(self, survey) -> None:
        layout = survey.layout
        original = capture_styles(layout)
        apply_theme(layout, "sepia", "Change page theme")
        assert capture_styles(layout) != original
        apply_theme(layout, "default", "Change page theme")
        assert capture_styles(layout) == original
        assert current_theme(layout) == "default"

    def test_one_theme_to_another_does_not_stack(self, survey) -> None:
        layout = survey.layout
        apply_theme(layout, "blueprint", "Change page theme")
        direct = capture_styles(layout)
        apply_theme(layout, "sepia", "Change page theme")
        apply_theme(layout, "blueprint", "Change page theme")
        assert capture_styles(layout) == direct

    def test_undo_and_redo_are_one_step_each(self, survey) -> None:
        layout = survey.layout
        stack = layout.undoStack().stack()
        original = capture_styles(layout)
        apply_theme(layout, "dark", "Change page theme")
        themed = capture_styles(layout)
        assert stack.count() == 1
        stack.undo()
        assert capture_styles(layout) == original
        assert current_theme(layout) == "default"
        stack.redo()
        assert capture_styles(layout) == themed
        assert current_theme(layout) == "dark"

    def test_a_colour_changed_since_survives_default(self, survey) -> None:
        layout = survey.layout
        apply_theme(layout, "dark", "Change page theme")
        fmt = survey.attribution_label.textFormat()
        fmt.setColor(QColor("#ff00ff"))
        survey.attribution_label.setTextFormat(fmt)
        apply_theme(layout, "default", "Change page theme")
        assert survey.attribution_label.textFormat().color().name() == "#ff00ff"
        assert survey.title_label.textFormat().color().name() == "#000000"

    def test_another_layout_is_not_themed(self, survey, project) -> None:
        other = QgsPrintLayout(project)
        other.initializeDefaults()
        before = other.pageCollection().page(0).pageStyleSymbol().color().name()
        apply_theme(survey.layout, "dark", "Change page theme")
        assert other.pageCollection().page(0).pageStyleSymbol().color().name() == before
        assert current_theme(other) == "default"

    def test_a_logo_picture_keeps_its_colours(self, survey, tmp_path) -> None:
        from qgis.core import QgsLayoutItemPicture

        svg = tmp_path / "logo.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
            encoding="utf-8",
        )
        logo = QgsLayoutItemPicture(survey.layout)
        survey.layout.addLayoutItem(logo)
        logo.setPicturePath(str(svg))
        logo.setSvgFillColor(QColor("#123456"))
        apply_theme(survey.layout, "dark", "Change page theme")
        assert logo.svgFillColor().name() == "#123456"

    def test_the_map_item_is_still_there(self, survey) -> None:
        apply_theme(survey.layout, "dark", "Change page theme")
        maps = [i for i in survey.layout.items() if isinstance(i, QgsLayoutItemMap)]
        assert len(maps) == 2
