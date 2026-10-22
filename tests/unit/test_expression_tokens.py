"""`core/expression_tokens.py`: QGIS `[% %]` label expressions -> tokens.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.expression_tokens import (
    TranslatedLabel,
    translate_atlas_pattern,
    translate_filter_expression,
    translate_label,
    translate_sort_expression,
)


def _translate(text: str, **kwargs: object) -> TranslatedLabel:
    kwargs.setdefault("frame_id", "main")
    kwargs.setdefault("frame_is_live", True)
    return translate_label(text, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Plain text, no expressions
# --------------------------------------------------------------------------


class TestPlainText:
    def test_no_expression_passes_through(self) -> None:
        result = _translate("Lot 12 — parcel survey")
        assert result.template == "Lot 12 — parcel survey"
        assert result.frozen_expressions == ()

    def test_empty_string(self) -> None:
        result = _translate("")
        assert result.template == ""
        assert result.frozen_expressions == ()

    def test_special_characters_are_untouched(self) -> None:
        # No HTML escaping happens here -- that is the writer's job, after
        # assembly. This module only ever deals in plain text.
        text = """Quotes " ' and a backslash \\ and a percent 100%"""
        result = _translate(text)
        assert result.template == text


# --------------------------------------------------------------------------
# @map_scale
# --------------------------------------------------------------------------


class TestMapScale:
    def test_live_frame_is_tokenised(self) -> None:
        result = _translate("1:[% @map_scale %]", frame_is_live=True)
        assert result.template == "1:{{frame.main.scale}}"
        assert result.frozen_expressions == ()

    def test_rounded_live_frame_is_tokenised(self) -> None:
        result = _translate("1:[% round(@map_scale) %]", frame_is_live=True)
        assert result.template == "1:{{frame.main.scale}}"

    def test_static_frame_is_frozen(self) -> None:
        # spike-findings.md: QGIS's item.scale() and the runtime's
        # homography-derived scale disagree by ~0.1% on a static frame, so
        # the value must come from QGIS, not the runtime's token.
        result = _translate("1:[% @map_scale %]", frame_is_live=False)
        assert result.frozen_expressions == ("@map_scale",)
        assert result.assemble(("25 000",)) == "1:25 000"

    def test_different_frame_id(self) -> None:
        result = translate_label(
            "[% @map_scale %]", frame_id="locator", frame_is_live=True
        )
        assert result.template == "{{frame.locator.scale}}"


# --------------------------------------------------------------------------
# @map_crs -- always tokenised, live or static
# --------------------------------------------------------------------------


class TestMapCrs:
    def test_live_frame(self) -> None:
        result = _translate("[% @map_crs %]", frame_is_live=True)
        assert result.template == "{{frame.main.crs}}"

    def test_static_frame_is_also_tokenised(self) -> None:
        # Unlike @map_scale, both modes know the CRS exactly.
        result = _translate("[% @map_crs %]", frame_is_live=False)
        assert result.template == "{{frame.main.crs}}"
        assert result.frozen_expressions == ()

    def test_description_variant(self) -> None:
        result = _translate("[% @map_crs_description %]")
        assert result.template == "{{frame.main.crs}}"

    def test_never_emits_bare_crs_token(self) -> None:
        # {{crs}} is ambiguous once a page has more than one frame -- this
        # module never emits it, regardless of frame count (it has no idea
        # how many frames the page has, so it always frame-qualifies).
        result = _translate("[% @map_crs %]")
        assert "{{crs}}" not in result.template


# --------------------------------------------------------------------------
# @project_title / now() / format_date
# --------------------------------------------------------------------------


class TestProjectTitleAndDate:
    def test_project_title(self) -> None:
        result = _translate("[% @project_title %]")
        assert result.template == "{{title}}"

    def test_now(self) -> None:
        result = _translate("[% now() %]")
        assert result.template == "{{date}}"

    def test_now_with_whitespace(self) -> None:
        result = _translate("[%  now (  )  %]")
        assert result.template == "{{date}}"

    def test_format_date_iso_is_tokenised(self) -> None:
        result = _translate("[% format_date(now(), 'yyyy-MM-dd') %]")
        assert result.template == "{{date}}"
        assert result.frozen_expressions == ()

    def test_format_date_non_iso_is_frozen(self) -> None:
        # The runtime's {{date}} token has no format argument -- it is
        # always ISO. Anything else has to be frozen to QGIS's own render.
        result = _translate("[% format_date(now(), 'dd/MM/yyyy') %]")
        assert result.frozen_expressions == ("format_date(now(), 'dd/MM/yyyy')",)
        assert result.assemble(("14/10/2026",)) == "14/10/2026"


# --------------------------------------------------------------------------
# Atlas tokens
# --------------------------------------------------------------------------


class TestAtlasTokens:
    def test_featurenumber_in_atlas(self) -> None:
        result = _translate("[% @atlas_featurenumber %]", in_atlas=True)
        assert result.template == "{{atlas.index}}"

    def test_totalfeatures_in_atlas(self) -> None:
        result = _translate("[% @atlas_totalfeatures %]", in_atlas=True)
        assert result.template == "{{atlas.count}}"

    def test_attribute_call_in_atlas(self) -> None:
        text = "[% attribute(@atlas_feature, 'lot_id') %]"
        result = _translate(text, in_atlas=True)
        assert result.template == "{{atlas.lot_id}}"

    def test_bare_field_in_atlas(self) -> None:
        result = _translate('[% "lot_id" %]', in_atlas=True)
        assert result.template == "{{atlas.lot_id}}"

    def test_atlas_tokens_outside_atlas_are_frozen(self) -> None:
        result = _translate("[% @atlas_featurenumber %]", in_atlas=False)
        assert result.frozen_expressions == ("@atlas_featurenumber",)

    def test_bare_field_outside_atlas_is_frozen(self) -> None:
        result = _translate('[% "lot_id" %]', in_atlas=False)
        assert result.frozen_expressions == ('"lot_id"',)

    def test_single_quoted_text_is_a_literal_not_a_field(self) -> None:
        """`'lot_id'` is a QGIS string literal: it renders as the text
        itself, never as the feature's value of a field by that name."""
        result = _translate("[% 'lot_id' %]", in_atlas=True)
        assert result.template == "lot_id"
        assert result.frozen_expressions == ()

    def test_unquoted_field_in_atlas(self) -> None:
        result = _translate("[% lot_id %]", in_atlas=True)
        assert result.template == "{{atlas.lot_id}}"

    def test_concatenation_in_atlas_stays_dynamic(self) -> None:
        result = _translate(
            """[% 'Lot ' || "lot" || ' of ' || @atlas_totalfeatures %]""", in_atlas=True
        )
        assert result.template == "Lot {{atlas.lot}} of {{atlas.count}}"
        assert result.frozen_expressions == ()

    def test_a_field_the_runtime_cannot_spell_is_frozen(self) -> None:
        """The runtime's token pattern is ASCII `\\w` only: a token for
        "plot no" would render literally on every page."""
        result = _translate('[% "plot no" %]', in_atlas=True)
        assert result.frozen_expressions == ('"plot no"',)

    @pytest.mark.parametrize("name", ["index", "count", "Index"])
    def test_a_field_named_like_a_runtime_token_is_frozen(self, name: str) -> None:
        result = _translate(f'[% "{name}" %]', in_atlas=True)
        assert result.frozen_expressions == (f'"{name}"',)

    def test_other_functions_of_the_atlas_feature_are_frozen(self) -> None:
        result = _translate('[% upper("lot") %]', in_atlas=True)
        assert result.frozen_expressions == ('upper("lot")',)


# --------------------------------------------------------------------------
# Freezing anything unrecognised
# --------------------------------------------------------------------------


class TestFreezing:
    def test_unrecognised_expression_is_frozen(self) -> None:
        result = _translate('[% "owner_name" || \' — \' || "lot_id" %]')
        assert result.frozen_expressions == ('"owner_name" || \' — \' || "lot_id"',)

    def test_expression_is_stripped_of_surrounding_whitespace(self) -> None:
        result = _translate("[%   1 + 1   %]")
        assert result.frozen_expressions == ("1 + 1",)

    def test_assemble_rejects_mismatched_count(self) -> None:
        result = _translate("[% 1 + 1 %] and [% 2 + 2 %]")
        with pytest.raises(ValueError):
            result.assemble(("2",))


# --------------------------------------------------------------------------
# Multiple blocks, nested brackets, whitespace, unterminated blocks
# --------------------------------------------------------------------------


class TestStructuralParsing:
    def test_multiple_blocks_with_literal_text_between(self) -> None:
        text = "Scale 1:[% @map_scale %] on [% @map_crs %] today"
        result = _translate(text, frame_is_live=True)
        assert result.template == (
            "Scale 1:{{frame.main.scale}} on {{frame.main.crs}} today"
        )

    def test_adjacent_blocks_with_no_text_between(self) -> None:
        result = _translate("[% @project_title %][% now() %]")
        assert result.template == "{{title}}{{date}}"

    def test_nested_square_brackets_inside_an_expression(self) -> None:
        # An expression may itself contain plain [ ] (e.g. array indexing),
        # which must not be mistaken for the block's own %] delimiter.
        result = _translate('[% array_get(array("a","b"), 0) [0] %]')
        assert result.frozen_expressions == ('array_get(array("a","b"), 0) [0]',)

    @pytest.mark.parametrize(
        ("text", "expression"),
        [
            ("[% 'x[y' %]", "'x[y'"),
            ("[% 'x]y' %]", "'x]y'"),
            ("[% 'it''s [' %]", "'it''s ['"),
            ("[% 'a\\'[' %]", "'a\\'['"),
            ('[% "a[b" %]', '"a[b"'),
            ("[% '[' || array(1,2)[0] %]", "'[' || array(1,2)[0]"),
        ],
    )
    def test_brackets_inside_quotes_do_not_nest(self, text, expression) -> None:
        result = _translate(text)
        assert result.frozen_expressions == (expression,)

    def test_a_bracket_in_one_block_does_not_swallow_the_next(self) -> None:
        result = _translate("[% '[' %] and [% ']' %] [% now() %]")
        assert result.frozen_expressions == ("'['", "']'")
        assert result.template.endswith("{{date}}")

    def test_unterminated_block_is_literal_text(self) -> None:
        text = "Loose bracket [% @map_scale without a close"
        result = _translate(text)
        assert result.template == text
        assert result.frozen_expressions == ()

    def test_whitespace_only_label(self) -> None:
        result = _translate("   ")
        assert result.template == "   "

    def test_literal_percent_and_bracket_outside_blocks(self) -> None:
        result = _translate("100% [done] [% now() %]")
        assert result.template == "100% [done] {{date}}"


# --------------------------------------------------------------------------
# Atlas filename / page-name patterns
# --------------------------------------------------------------------------


class TestAtlasPattern:
    def test_qgis_default_filename(self) -> None:
        assert (
            translate_atlas_pattern("'output_'||@atlas_featurenumber")
            == "output_{{atlas.index}}"
        )

    def test_field_and_literals(self) -> None:
        assert (
            translate_atlas_pattern("""'lot-' || "lot" || '-' || name""")
            == "lot-{{atlas.lot}}-{{atlas.name}}"
        )

    def test_concat_function(self) -> None:
        assert (
            translate_atlas_pattern(
                """concat('lot-', "lot", '_', @atlas_featurenumber)"""
            )
            == "lot-{{atlas.lot}}_{{atlas.index}}"
        )

    def test_attribute_of_the_atlas_feature(self) -> None:
        assert (
            translate_atlas_pattern("attribute(@atlas_feature, 'lot')")
            == "{{atlas.lot}}"
        )

    def test_doubled_quote_inside_a_literal(self) -> None:
        assert translate_atlas_pattern("'it''s ' || \"lot\"") == "it's {{atlas.lot}}"

    def test_parentheses_are_transparent(self) -> None:
        assert translate_atlas_pattern("('a' || (\"lot\"))") == "a{{atlas.lot}}"

    @pytest.mark.parametrize(
        "expression",
        [
            "@atlas_pagename",
            'upper("lot")',
            '"lot" + 1',
            "'a' || @project_title",
            "'{{title}}'",
            "",
            "'unterminated",
            "concat('a', upper(\"lot\"))",
        ],
    )
    def test_anything_else_is_untranslatable(self, expression: str) -> None:
        assert translate_atlas_pattern(expression) is None


# --------------------------------------------------------------------------
# Atlas sort
# --------------------------------------------------------------------------


class TestAtlasSort:
    def test_quoted_field_ascending(self) -> None:
        assert translate_sort_expression('"lot"', ascending=True) == "lot"

    def test_bare_field_descending(self) -> None:
        assert translate_sort_expression("lot", ascending=False) == "-lot"

    def test_a_field_with_a_space_is_fine(self) -> None:
        """The runtime's sort reads the row by name, not through a token."""
        assert translate_sort_expression('"plot no"', ascending=True) == "plot no"

    @pytest.mark.parametrize(
        "expression", ['"a,b"', '"-lot"', 'length("lot")', "\"lot\" || 'x'", ""]
    )
    def test_anything_but_a_plain_field_is_untranslatable(
        self, expression: str
    ) -> None:
        assert translate_sort_expression(expression, ascending=True) is None


# --------------------------------------------------------------------------
# Atlas filter -> core expression language
# --------------------------------------------------------------------------


class TestAtlasFilter:
    @pytest.mark.parametrize(
        ("qgis", "core"),
        [
            ("\"lot\" = 'A'", '($lot == "A")'),
            ("\"lot\" <> 'B'", '($lot != "B")'),
            ('"area" > 500', "($area > 500)"),
            ("area >= 1.5e3", "($area >= 1.5e3)"),
            (
                "\"lot\" = 'A' OR \"lot\" = 'C'",
                '(($lot == "A") || ($lot == "C"))',
            ),
            (
                'NOT ("a" = 1 AND "b" < 2)',
                "(!(($a == 1) && ($b < 2)))",
            ),
            ('"lot" IS NULL', "($lot == null)"),
            ('"lot" IS NOT NULL', "($lot != null)"),
            ("\"lot\" IN ('A', 'C')", '($lot == "A" || $lot == "C")'),
            ('"lot" NOT IN (1, 2)', "($lot != 1 && $lot != 2)"),
            ('"a" * 2 + 1 > -3', "((($a * 2) + 1) > (-3))"),
            ("flag = TRUE", "($flag == true)"),
            ("\"name\" = 'O''Brien'", '($name == "O\'Brien")'),
        ],
    )
    def test_translates(self, qgis: str, core: str) -> None:
        assert translate_filter_expression(qgis) == core

    def test_and_binds_tighter_than_or(self) -> None:
        assert (
            translate_filter_expression("a = 1 OR b = 2 AND c = 3")
            == "(($a == 1) || (($b == 2) && ($c == 3)))"
        )

    @pytest.mark.parametrize(
        "expression",
        [
            "$area > 500",
            "\"lot\" LIKE 'A%'",
            "upper(\"lot\") = 'A'",
            "@atlas_featurenumber = 1",
            '"plot no" = 1',
            '"a" BETWEEN 1 AND 2',
            '"a" = 1 AND',
            "\"a\" || 'x' = 'y'",
            "CASE WHEN a THEN 1 END",
            '"a" IS 3',
            "",
        ],
    )
    def test_anything_else_is_untranslatable(self, expression: str) -> None:
        assert translate_filter_expression(expression) is None


# The named map item, as the layout builder writes a scale or CRS label
class TestItemVariables:
    def test_the_labels_own_frame_reads_as_its_token(self) -> None:
        text = (
            "1:[% round(map_get(item_variables('main'), 'map_scale')) %] "
            "[% map_get(item_variables('main'), 'map_crs') %]"
        )
        result = translate_label(text, frame_id="main", frame_is_live=True)
        assert result.template == "1:{{frame.main.scale}} {{frame.main.crs}}"
        assert result.frozen_expressions == ()

    def test_a_static_frame_freezes_its_scale_as_before(self) -> None:
        text = "1:[% round(map_get(item_variables('main'), 'map_scale')) %]"
        result = translate_label(text, frame_id="main", frame_is_live=False)
        assert result.frozen_expressions == ("round(@map_scale)",)

    def test_the_frame_is_known_by_its_qgis_item_id(self) -> None:
        text = "[% map_get(item_variables('main'), 'map_crs') %]"
        result = translate_label(
            text, frame_id="frame_main", frame_is_live=True, frame_item_id="main"
        )
        assert result.template == "{{frame.frame_main.crs}}"

    def test_another_frame_is_frozen(self) -> None:
        text = "[% map_get(item_variables('inset'), 'map_crs') %]"
        result = translate_label(text, frame_id="main", frame_is_live=True)
        assert result.frozen_expressions == (
            "map_get(item_variables('inset'), 'map_crs')",
        )
