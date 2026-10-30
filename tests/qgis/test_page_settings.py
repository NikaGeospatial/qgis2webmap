# ruff: noqa: F811
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
from test_cartograph_dialog import (
    TestCanvasAndImport as _CanvasAndImportHelper,  # not collected twice here
)
from test_cartograph_dialog import (
    _add_layout,
    _choose_cartograph,
    _click_new_from_template,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)

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


def _template_dialog(project, picked_template, template="classic"):
    picked_template.choice = (template, "A4", "landscape")
    dialog, _canvas = _CanvasAndImportHelper()._canvas_dialog(project)
    _choose_cartograph(dialog)
    _click_new_from_template(dialog)
    cartograph = dialog._cartograph
    cartograph._autosave_timer.stop()
    return dialog, cartograph, cartograph._current_layout


def _type(edit, text) -> None:
    from qgis.PyQt.QtTest import QTest

    edit.clear()
    edit.textEdited.emit("")
    QTest.keyClicks(edit, text)


class TestPageSettingsInTheDialog:
    def test_typing_a_title_changes_the_preview_text_and_undo_takes_it_back(
        self, qgis_app, project, picked_template
    ) -> None:
        _add_layout(project, "Other", [("Map", "EPSG:4326")])
        dialog, cartograph, layout = _template_dialog(project, picked_template)
        try:
            title = title_item(layout)
            assert "@project_title" in title.text()
            stack = layout.undoStack().stack()
            before = stack.count()
            _type(cartograph.title_edit, "Park atlas")
            assert title.currentText() == "Park atlas"
            assert title.text().count("@project_title") == 1
            assert stack.count() == before + 1
            other = project.layoutManager().layoutByName("Other")
            assert _variable(other) is None
            assert project.title() == "Parks"
            stack.undo()
            assert cartograph.title_edit.text() == ""
            assert title.currentText() == "Parks"
            stack.redo()
            assert cartograph.title_edit.text() == "Park atlas"
            # Switching away and back keeps it, from the layout itself.
            cartograph.show_layout_named("Other")
            assert cartograph.title_edit.text() == ""
            cartograph.show_layout_named(layout.name())
            assert cartograph.title_edit.text() == "Park atlas"
        finally:
            dialog.close()

    def test_choosing_a_theme_restyles_the_layout_and_the_preview(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog, cartograph, layout = _template_dialog(project, picked_template)
        try:
            view = cartograph.page_preview.view
            dialog.show()
            qgis_app.processEvents()
            before = view.viewport().grab().toImage()
            combo = cartograph.theme_combo
            combo.setCurrentIndex(combo.findData("dark"))
            assert current_theme(layout) == "dark"
            page = layout.pageCollection().page(0)
            assert page.pageStyleSymbol().color().name() == THEMES["dark"].page
            after = view.viewport().grab().toImage()
            assert after != before
            stack = layout.undoStack().stack()
            stack.undo()
            assert cartograph.theme_combo.currentData() == "default"
            assert current_theme(layout) == "default"
            stack.redo()
            assert cartograph.theme_combo.currentData() == "dark"
            for text in _texts(layout):
                assert (
                    surface_contrast(
                        text.textFormat().color().name(), surface_of(layout, text)
                    )
                    >= MINIMUM_CONTRAST
                )
        finally:
            dialog.close()

    def test_the_section_and_its_caveat_are_gone(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog, cartograph, _layout = _template_dialog(project, picked_template)
        try:
            assert cartograph.page_section.header.text() == "Page settings"
            assert not hasattr(cartograph, "page_page_note")
        finally:
            dialog.close()

    def test_an_expression_title_is_explained_not_flattened(
        self, qgis_app, project, picked_template
    ) -> None:
        dialog, cartograph, layout = _template_dialog(project, picked_template)
        try:
            title = title_item(layout)
            title.setText("[% 'Sheet ' || @atlas_featurenumber %]")
            cartograph._load_page_fields()
            assert not cartograph.page_hint.isHidden()
            assert "expression" in cartograph.page_hint.text()
            _type(cartograph.title_edit, "Typed")
            assert title.text() == "[% 'Sheet ' || @atlas_featurenumber %]"
        finally:
            dialog.close()

    def test_a_layout_saved_before_gets_its_recorded_fields(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.settings import save_state

        layout = _add_layout(project, "Old", [("Map", "EPSG:4326")])
        dialog = __import__("test_cartograph_dialog")._dialog(project)
        try:
            dialog.state.page_choice("Old").theme = "sepia"
            save_state(project, dialog.state)
            _choose_cartograph(dialog)
            cartograph = dialog._cartograph
            cartograph._autosave_timer.stop()
            cartograph.show_layout_named("Old")
            assert current_theme(layout) == "sepia"
            assert cartograph.theme_combo.currentData() == "sepia"
        finally:
            dialog.close()


class TestSameEverywhere:
    def test_the_web_page_and_the_pdf_carry_the_same_title_and_theme(
        self, survey, project
    ) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.exporters.print_pdf import build_print_layout
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        runtime = RuntimeBundle(
            javascript=b"//",
            css=b"/**/",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"//",
            cartograph_css=b"/**/",
        )
        set_title(survey.layout, "Park atlas", "Edit page title")
        apply_theme(survey.layout, "dark", "Change page theme")
        # The dialog passes the page title as the document's title, as here.
        html = export_layout_to_cartograph(
            survey.layout,
            project,
            runtime,
            FidelityReportBuilder(),
            static_dpi=50,
            title_override=title_state(survey.layout).value,
        )
        assert "Park atlas" in html
        assert THEMES["dark"].page in html.lower()
        copy = build_print_layout(survey.layout, PrintSettings(bleed="none"))
        assert (
            copy.pageCollection().page(0).pageStyleSymbol().color().name()
            == THEMES["dark"].page
        )
        labels = [i for i in copy.items() if isinstance(i, QgsLayoutItemLabel)]
        assert any(label.currentText() == "Park atlas" for label in labels)
