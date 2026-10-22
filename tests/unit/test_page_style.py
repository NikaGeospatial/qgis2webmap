"""`core/page_style.py`: every named page palette reads, type never gets too
small on a small page, and the module stays free of Qt.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nika_onlymap_exporter.core import page_style
from nika_onlymap_exporter.core.canvas_layout import page_dimensions
from nika_onlymap_exporter.core.page_style import (
    MINIMUM_SPACING,
    MINIMUM_TYPE,
    PALETTES,
    SPACING_A4,
    TYPE_A4,
    TYPE_ROLES,
    PagePalette,
    composite,
    contrast_ratio,
    page_scale,
    palette,
    parse_color,
    spacing_for,
    type_size,
)

WHITE = (1.0, 1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0, 1.0)
PAPER_SIZES = ("A5", "A4", "A3", "A2", "A1", "A0", "Letter", "Legal", "Tabloid")


def _rgba(css: str):
    color = parse_color(css)
    assert color is not None, css
    return color


def _contrast(ink: str, ground: str, *, under=WHITE) -> float:
    ground_rgba = composite(_rgba(ground), under)
    return contrast_ratio(composite(_rgba(ink), ground_rgba), ground_rgba)


@pytest.fixture(params=sorted(PALETTES))
def colours(request) -> PagePalette:
    return PALETTES[request.param]


class TestPalettes:
    def test_both_families_are_named(self) -> None:
        assert {p.name for p in PALETTES.values() if not p.dark} >= {"daylight"}
        assert any(p.dark for p in PALETTES.values())
        assert all(name == p.name for name, p in PALETTES.items())

    def test_every_colour_parses(self, colours: PagePalette) -> None:
        for name, value in vars(colours).items():
            if isinstance(value, str) and name != "name":
                assert parse_color(value) is not None, (colours.name, name, value)
        assert 0 < colours.plate_opacity <= 1

    @pytest.mark.parametrize(
        ("ink", "ground"),
        [
            ("ink", "page"),
            ("muted", "page"),
            ("panel_ink", "panel"),
            ("band_ink", "band"),
            ("ink", "tile"),
        ],
    )
    def test_text_reads_at_body_size(
        self, colours: PagePalette, ink: str, ground: str
    ) -> None:
        """WCAG AA body text, 4.5:1, for every text colour on its ground."""
        ratio = _contrast(getattr(colours, ink), getattr(colours, ground))
        assert ratio >= 4.5, (colours.name, ink, ground, round(ratio, 2))

    @pytest.mark.parametrize("under", [WHITE, BLACK])
    def test_panel_ink_reads_on_the_veil_over_any_map(
        self, colours: PagePalette, under
    ) -> None:
        """A see-through veil over the lightest or darkest map still holds
        its text at 4.5:1."""
        assert _contrast(colours.panel_ink, colours.veil, under=under) >= 4.5

    def test_the_plate_is_the_veil_as_a_shape_fill(self, colours: PagePalette) -> None:
        veil = _rgba(colours.veil)
        plate = composite(_rgba(colours.plate), WHITE, colours.plate_opacity)
        assert contrast_ratio(composite(veil, WHITE), plate) < 1.1

    @pytest.mark.parametrize("mark", ["rule", "accent"])
    def test_rules_and_accents_are_seen(self, colours: PagePalette, mark: str) -> None:
        """WCAG 1.4.11's 3:1 for graphics that carry meaning."""
        assert _contrast(getattr(colours, mark), colours.page) >= 3.0

    def test_dark_means_a_dark_page(self, colours: PagePalette) -> None:
        on_page = _contrast("#ffffff", colours.page)
        assert (on_page > _contrast("#000000", colours.page)) == colours.dark

    def test_lookup_by_name(self) -> None:
        assert palette("daylight") is page_style.DAYLIGHT
        with pytest.raises(ValueError, match="not one of"):
            palette("neon")


class TestType:
    def test_a4_is_the_written_scale(self) -> None:
        width, height = page_dimensions("A4", "portrait")
        for role in TYPE_ROLES:
            assert type_size(role, width, height) == TYPE_A4.size(role)

    @pytest.mark.parametrize("size", PAPER_SIZES)
    @pytest.mark.parametrize("role", TYPE_ROLES)
    def test_never_below_the_minimum(self, size: str, role: str) -> None:
        width, height = page_dimensions(size, "landscape")
        assert type_size(role, width, height) >= MINIMUM_TYPE.size(role)

    def test_a5_keeps_body_and_caption_readable(self) -> None:
        width, height = page_dimensions("A5", "portrait")
        assert type_size("body", width, height) >= 7.0
        assert type_size("caption", width, height) >= 6.5
        assert type_size("credit", width, height) >= 6.0

    def test_bigger_paper_bigger_type(self) -> None:
        sizes = [
            type_size("title", *page_dimensions(s, "portrait")) for s in PAPER_SIZES[:6]
        ]
        assert sizes == sorted(sizes) and sizes[-1] > sizes[1]

    def test_the_roles_run_largest_first(self) -> None:
        for scale in (TYPE_A4, MINIMUM_TYPE):
            values = [scale.size(role) for role in TYPE_ROLES[:4]]
            assert values == sorted(values, reverse=True)
            assert all(scale.size(role) <= scale.body for role in TYPE_ROLES[4:])

    def test_unknown_role_is_refused(self) -> None:
        with pytest.raises(ValueError, match="text role"):
            type_size("footnote", 210, 297)

    def test_page_scale_is_bounded(self) -> None:
        assert page_scale(210, 297) == 1.0
        assert page_scale(10, 10) == page_style.MIN_PAGE_SCALE
        assert page_scale(5000, 5000) == page_style.MAX_PAGE_SCALE


class TestSpacing:
    def test_a4_is_the_written_spacing(self) -> None:
        assert spacing_for(*page_dimensions("A4", "portrait")) == SPACING_A4

    @pytest.mark.parametrize("size", PAPER_SIZES)
    def test_never_below_the_minimum(self, size: str) -> None:
        spacing = spacing_for(*page_dimensions(size, "portrait"))
        for name in ("margin", "gutter", "pad", "rule"):
            assert getattr(spacing, name) >= getattr(MINIMUM_SPACING, name)

    def test_a5_keeps_print_safe_margins_and_gutters(self) -> None:
        spacing = spacing_for(*page_dimensions("A5", "portrait"))
        assert spacing.margin >= 7.0
        assert spacing.gutter >= 3.0
        assert spacing.pad >= 2.0
        assert spacing.rule >= 0.25

    def test_a1_grows(self) -> None:
        assert spacing_for(*page_dimensions("A1", "portrait")).margin > 20


class TestColourArithmetic:
    def test_parses_the_forms_pages_use(self) -> None:
        assert parse_color("#fff") == WHITE
        assert parse_color("#00000000") == (0.0, 0.0, 0.0, 0.0)
        assert parse_color("rgba(255, 255, 255, 0.5)") == (1.0, 1.0, 1.0, 0.5)
        assert parse_color("rgb(0,0,0)") == BLACK
        assert parse_color("teal") is None

    def test_black_on_white_is_21(self) -> None:
        assert contrast_ratio(BLACK, WHITE) == pytest.approx(21.0)


def test_the_module_imports_no_qt_or_qgis() -> None:
    tree = ast.parse(Path(page_style.__file__).read_text(encoding="utf-8"))
    names = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ] + [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert not [n for n in names if n.startswith(("qgis", "PyQt", "Qt"))]
