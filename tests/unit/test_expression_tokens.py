"""`core/expression_tokens.py`: QGIS `[% %]` label expressions -> tokens.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.expression_tokens import (
    TranslatedLabel,
    translate_label,
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
