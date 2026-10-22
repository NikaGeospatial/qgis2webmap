"""The helpers `core/layout_templates.py` gives its designs: text that fits
its box, pieces present only with real content, text that reads over any
map, and frames shaped by the extent.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from nika_onlymap_exporter.core.cartograph_ir import Box, LegendSpec
from nika_onlymap_exporter.core.layout_templates import (
    MAIN_FRAME_ID,
    PT_MM,
    TITLE_LINE_HEIGHT,
    fit_text_size,
    frame_for_extent,
    legend_or_none,
    source_line,
    text_height,
    text_lines,
    text_on_panel,
    title_box_height,
)
from nika_onlymap_exporter.core.page_style import (
    DAYLIGHT,
    PALETTES,
    composite,
    contrast_ratio,
    parse_color,
)

LONG_TITLE = (
    "Flood-prone parcels along the Kranji reservoir catchment, surveyed September 2026"
)


class TestTextMeasure:
    def test_no_text_no_lines(self) -> None:
        assert text_lines("", 100, 10) == 0
        assert text_lines("   ", 100, 10) == 0
        assert text_height("", 100, 10) == 0

    def test_a_short_title_is_one_line(self) -> None:
        assert text_lines("Parcels", 100, 18, bold=True) == 1

    def test_narrower_or_bigger_means_more_lines(self) -> None:
        wide = text_lines(LONG_TITLE, 250, 18, bold=True)
        narrow = text_lines(LONG_TITLE, 100, 18, bold=True)
        bigger = text_lines(LONG_TITLE, 250, 30, bold=True)
        assert wide < narrow and wide < bigger

    def test_bold_runs_wider(self) -> None:
        text = "word " * 30
        assert text_lines(text, 100, 10, bold=True) >= text_lines(text, 100, 10)

    def test_newlines_and_overlong_words(self) -> None:
        assert text_lines("one\ntwo\n\nfour", 200, 10) == 4
        # A lower-case letter at 10 pt is estimated at about 2 mm: 8 to a
        # line 18 mm wide, so 25 of them take four lines.
        assert text_lines("x" * 25, 18, 10) == 4

    def test_height_is_lines_times_leading(self) -> None:
        lines = text_lines(LONG_TITLE, 100, 12)
        assert text_height(LONG_TITLE, 100, 12) == pytest.approx(
            lines * 12 * PT_MM * 1.4
        )
        assert text_height(LONG_TITLE, 100, 12, max_lines=1) == pytest.approx(
            12 * PT_MM * 1.4
        )


class TestTitleBox:
    def test_at_least_one_line_at_most_max(self) -> None:
        one = 18 * PT_MM * TITLE_LINE_HEIGHT
        assert title_box_height("", 100, 18) == pytest.approx(one)
        assert title_box_height("Parcels", 100, 18) == pytest.approx(one)
        assert title_box_height(LONG_TITLE, 60, 18, max_lines=2) == pytest.approx(
            2 * one
        )

    def test_a_long_title_gets_a_taller_box(self) -> None:
        assert title_box_height(LONG_TITLE, 120, 24) > title_box_height(
            "Parcels", 120, 24
        )


class TestFitTextSize:
    def test_short_text_keeps_the_largest_size(self) -> None:
        assert fit_text_size("Parcels", Box(0, 0, 150, 20), 34, 18, bold=True) == 34

    def test_long_text_shrinks_and_then_fits(self) -> None:
        box = Box(0, 0, 150, 25)
        size = fit_text_size(LONG_TITLE, box, 34, 12, bold=True)
        assert 12 < size < 34
        assert text_height(LONG_TITLE, box.w, size, bold=True) <= box.h

    def test_never_below_the_minimum(self) -> None:
        assert fit_text_size(LONG_TITLE * 5, Box(0, 0, 40, 8), 20, 9) == 9

    def test_a_line_cap_is_kept(self) -> None:
        size = fit_text_size(LONG_TITLE, Box(0, 0, 150, 200), 34, 8, max_lines=1)
        assert text_lines(LONG_TITLE, 150, size) == 1


class TestConditionalPieces:
    def test_no_layers_or_no_room_no_legend(self) -> None:
        assert legend_or_none((), Box(0, 0, 60, 60)) is None
        assert legend_or_none(("roads",), Box(0, 0, 60, 10)) is None
        assert legend_or_none(("roads",), Box(0, 0, 15, 60)) is None

    def test_a_legend_for_the_main_map(self) -> None:
        legend = legend_or_none(
            ("roads",), Box(0, 0, 60, 60), columns=2, font_size=8.0, z=3
        )
        assert isinstance(legend, LegendSpec)
        assert (legend.id, legend.for_, legend.columns, legend.font_size, legend.z) == (
            "legend",
            MAIN_FRAME_ID,
            2,
            8.0,
            3,
        )

    def test_no_credits_no_source_line(self) -> None:
        assert source_line("", Box(0, 0, 100, 4)) is None
        assert source_line("  ", Box(0, 0, 100, 4)) is None

    def test_a_source_line_in_the_palettes_ink(self) -> None:
        line = source_line("© OSM", Box(0, 0, 100, 4), palette=DAYLIGHT)
        assert line is not None
        assert (line.body, line.kind, line.color, line.bg) == (
            "{{attribution}}",
            "attribution",
            DAYLIGHT.muted,
            None,
        )
        over_map = source_line(
            "© OSM", Box(0, 0, 100, 4), palette=DAYLIGHT, on_panel=True
        )
        assert over_map is not None
        assert (over_map.color, over_map.bg) == (DAYLIGHT.panel_ink, DAYLIGHT.panel)


class TestTextOnPanel:
    @pytest.mark.parametrize("name", sorted(PALETTES))
    def test_opaque_and_readable_over_any_map(self, name: str) -> None:
        colours = PALETTES[name]
        text = text_on_panel(
            "note", Box(0, 0, 60, 20), "{{notes}}", palette=colours, font_size=9
        )
        bg = parse_color(text.bg or "")
        ink = parse_color(text.color or "")
        assert bg is not None and bg[3] == 1.0
        assert ink is not None
        assert contrast_ratio(composite(ink, bg), bg) >= 4.5

    def test_a_see_through_panel_is_refused(self) -> None:
        veiled = replace(DAYLIGHT, name="veiled", panel=DAYLIGHT.veil)
        with pytest.raises(ValueError, match="opaque panel"):
            text_on_panel("note", Box(0, 0, 60, 20), "x", palette=veiled, font_size=9)


class TestFrameForExtent:
    AREA = Box(10, 20, 200, 100)

    def _inside(self, box: Box) -> bool:
        area = self.AREA
        return (
            box.x >= area.x - 1e-9
            and box.y >= area.y - 1e-9
            and box.x + box.w <= area.x + area.w + 1e-9
            and box.y + box.h <= area.y + area.h + 1e-9
        )

    def test_the_extent_shape_fills_the_area(self) -> None:
        box = frame_for_extent(2.0, self.AREA)
        assert (box.w, box.h) == pytest.approx((200, 100))
        assert self._inside(box)

    def test_a_round_extent_is_held_to_a_minimum_length(self) -> None:
        box = frame_for_extent(1.0, self.AREA, min_aspect=1.6)
        assert box.w / box.h == pytest.approx(1.6)
        assert self._inside(box)

    def test_a_very_long_extent_is_held_to_a_maximum(self) -> None:
        box = frame_for_extent(20.0, self.AREA, max_aspect=4.0)
        assert box.w / box.h == pytest.approx(4.0)
        assert self._inside(box)

    @pytest.mark.parametrize(
        ("align", "x"), [("start", 10.0), ("center", 60.0), ("end", 110.0)]
    )
    def test_alignment(self, align: str, x: float) -> None:
        assert frame_for_extent(1.0, self.AREA, align=align).x == pytest.approx(x)

    def test_bad_input_is_refused(self) -> None:
        with pytest.raises(ValueError):
            frame_for_extent(0.0, self.AREA)
        with pytest.raises(ValueError):
            frame_for_extent(1.0, self.AREA, align="middle")
