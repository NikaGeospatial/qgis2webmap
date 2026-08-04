"""Symbol-atlas planning - pure, so it runs in CI without QGIS.

The pixels are QGIS's problem (`tests/qgis/test_symbol_rasterizer.py`). What is
checked here is everything that decides *whether* a layer gets a sprite sheet and
*where* each icon lands in it, because both fail quietly: a layer that should
have been rasterised just draws circles, and an icon at the wrong coordinates
draws its neighbour.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    GeometryKind,
    GraduatedClassSpec,
    RendererKind,
    RendererSpec,
    SymbolSpec,
)
from nika_onlymap_exporter.core.symbol_atlas import (
    MAX_ATLAS_PIXELS,
    MAX_ICONS_PER_LAYER,
    appearance_key,
    icon_name,
    is_drawable_as_circle,
    layer_symbols,
    needs_atlas,
    over_capacity,
    plan_atlas,
)

RED = Color(r=255, g=0, b=0)
BLUE = Color(r=0, g=0, b=255)

CIRCLE = SymbolSpec(fill_color=RED, marker_shape="circle", radius=5.0)
STAR = SymbolSpec(fill_color=RED, marker_shape="star", radius=5.0)
SVG = SymbolSpec(fill_color=RED, icon_path="/svg/tourist/hotel.svg", radius=5.0)


def categorized(*symbols: SymbolSpec) -> RendererSpec:
    return RendererSpec(
        kind=RendererKind.CATEGORIZED,
        field_name="kind",
        categories=tuple(
            CategorySpec(value=f"v{i}", label=f"L{i}", symbol=symbol)
            for i, symbol in enumerate(symbols)
        ),
    )


def graduated(*symbols: SymbolSpec) -> RendererSpec:
    return RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name="pop",
        classes=tuple(
            GraduatedClassSpec(
                lower=float(i), upper=float(i + 1), label=f"R{i}", symbol=symbol
            )
            for i, symbol in enumerate(symbols)
        ),
    )


class TestDrawableAsCircle:
    """What the existing circle path can draw honestly, and what it cannot."""

    def test_a_plain_circle_needs_no_atlas(self) -> None:
        assert is_drawable_as_circle(CIRCLE)

    def test_an_unshaped_symbol_needs_no_atlas(self) -> None:
        """A renderer that reported no shape at all - a fill or a line symbol
        reaching this code - must not drag a whole layer into rasterisation."""
        assert is_drawable_as_circle(SymbolSpec(fill_color=RED))

    def test_a_named_shape_that_is_not_a_circle_does(self) -> None:
        assert not is_drawable_as_circle(STAR)

    def test_an_svg_marker_does(self) -> None:
        assert not is_drawable_as_circle(SVG)

    def test_stacked_symbol_layers_do(self) -> None:
        """Only the top symbol layer survives flattening, so a stack drawn as a
        circle is missing whatever the other layers contributed."""
        assert not is_drawable_as_circle(
            SymbolSpec(fill_color=RED, marker_shape="circle", symbol_layer_count=3)
        )


class TestNeedsAtlas:
    def test_a_point_layer_of_circles_does_not(self) -> None:
        assert not needs_atlas(categorized(CIRCLE, CIRCLE), GeometryKind.POINT)

    def test_one_svg_class_pulls_the_whole_layer_in(self) -> None:
        """Per layer, not per symbol: splitting one QGIS layer across two
        deck.gl layers would make them fight over draw order."""
        assert needs_atlas(categorized(CIRCLE, SVG, CIRCLE), GeometryKind.POINT)

    def test_lines_and_polygons_never_do(self) -> None:
        """An icon sheet cannot express a marker line or a point-pattern fill,
        so pretending it can would produce a layer of loose markers."""
        for geometry in (GeometryKind.LINE, GeometryKind.POLYGON):
            assert not needs_atlas(categorized(SVG), geometry)

    def test_a_renderer_with_no_symbols_does_not(self) -> None:
        assert not needs_atlas(
            RendererSpec(kind=RendererKind.UNSUPPORTED), GeometryKind.POINT
        )

    def test_a_single_svg_symbol_does(self) -> None:
        assert needs_atlas(
            RendererSpec(kind=RendererKind.SINGLE, symbol=SVG), GeometryKind.POINT
        )


class TestLayerSymbols:
    """The order the manifest's `get-icon` expression is positional against."""

    def test_categorized_follows_its_classes(self) -> None:
        assert layer_symbols(categorized(CIRCLE, STAR, SVG)) == [CIRCLE, STAR, SVG]

    def test_graduated_follows_its_ranges(self) -> None:
        assert layer_symbols(graduated(CIRCLE, STAR)) == [CIRCLE, STAR]

    def test_single_contributes_one(self) -> None:
        assert layer_symbols(RendererSpec(kind=RendererKind.SINGLE, symbol=STAR)) == [
            STAR
        ]


class TestAppearanceKey:
    """One cell per distinct *picture*, not per file and not per class."""

    def test_identical_symbols_share_a_cell(self) -> None:
        assert appearance_key(STAR) == appearance_key(
            SymbolSpec(fill_color=RED, marker_shape="star", radius=5.0)
        )

    def test_the_same_file_in_two_colours_does_not(self) -> None:
        recoloured = SymbolSpec(
            fill_color=BLUE, icon_path=SVG.icon_path, radius=SVG.radius
        )
        assert appearance_key(SVG) != appearance_key(recoloured)

    def test_the_same_marker_at_two_sizes_does_not(self) -> None:
        """Size is baked into the cell rather than applied at runtime, so a
        graduated-by-size layer gets one cell per size. That is what keeps the
        stroke-to-size ratio QGIS drew, which a single normalised cell scaled by
        the runtime would distort."""
        bigger = SymbolSpec(fill_color=RED, marker_shape="star", radius=9.0)
        assert appearance_key(STAR) != appearance_key(bigger)

    def test_offset_does_not_split_a_cell(self) -> None:
        """Offset is a runtime accessor (`get-icon-pixel-offset`), so two
        markers differing only in offset are the same picture."""
        moved = SymbolSpec(
            fill_color=RED, marker_shape="star", radius=5.0, offset_x=4.0, offset_y=-2.0
        )
        assert appearance_key(STAR) == appearance_key(moved)


class TestPlanAtlas:
    def test_an_empty_plan_has_no_pixels(self) -> None:
        plan = plan_atlas([])
        assert (plan.width, plan.height, plan.placements) == (0, 0, ())

    def test_one_icon_fills_one_cell(self) -> None:
        plan = plan_atlas([48])
        assert (plan.width, plan.height) == (48, 48)
        assert plan.placements[0].x == 0
        assert plan.placements[0].y == 0

    def test_cells_are_square_and_sized_by_the_largest_icon(self) -> None:
        plan = plan_atlas([20, 60, 30])
        assert plan.cell == 60
        assert plan.columns == 2
        assert (plan.width, plan.height) == (120, 120)

    def test_a_smaller_icon_is_centred_rather_than_stretched(self) -> None:
        """The mapping addresses the icon's own extent, so a small marker keeps
        its size instead of being blown up to the cell."""
        plan = plan_atlas([60, 20])
        small = plan.placements[1]
        assert small.side == 20
        # Cell 1 starts at x=60; a 20px icon centred in a 60px cell insets 20.
        assert small.x == 80
        assert small.y == 20

    def test_cells_never_overlap(self) -> None:
        plan = plan_atlas([30, 40, 20, 50, 10])
        boxes = [(p.x, p.y, p.x + p.side, p.y + p.side) for p in plan.placements]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                separated = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                assert separated, f"{a} overlaps {b}"

    def test_every_cell_is_inside_the_sheet(self) -> None:
        plan = plan_atlas([31, 44, 12, 44, 44, 9, 20])
        for placement in plan.placements:
            assert placement.x >= 0 and placement.y >= 0
            assert placement.x + placement.side <= plan.width
            assert placement.y + placement.side <= plan.height

    def test_names_are_stable_and_positional(self) -> None:
        plan = plan_atlas([10, 10, 10])
        assert [p.name for p in plan.placements] == [icon_name(i) for i in range(3)]

    def test_the_grid_stays_near_square(self) -> None:
        """A single row of 256 icons would be thousands of pixels wide and hit
        the texture limit using a fraction of a square's capacity."""
        plan = plan_atlas([16] * 100)
        assert plan.columns == 10
        assert plan.width == plan.height


class TestMapping:
    def test_the_anchor_is_the_icon_centre(self) -> None:
        """A QGIS marker is centred on its feature. deck.gl's default anchor is
        also the centre, but a default that changed would move every marker on
        every exported map by half its own size."""
        cell = plan_atlas([40]).mapping["i0"]
        assert cell["anchorX"] == 20
        assert cell["anchorY"] == 20

    def test_icons_are_not_masks(self) -> None:
        """A masked icon is a silhouette tinted by `getIconColor`, which throws
        away the parametrised SVG fills this feature exists to preserve."""
        assert plan_atlas([40]).mapping["i0"]["mask"] is False

    def test_the_mapping_covers_every_placement(self) -> None:
        plan = plan_atlas([10, 20, 30])
        assert set(plan.mapping) == {p.name for p in plan.placements}


class TestLimits:
    def test_a_layer_within_the_cap_is_allowed(self) -> None:
        assert not over_capacity(MAX_ICONS_PER_LAYER)

    def test_one_past_the_cap_is_not(self) -> None:
        assert over_capacity(MAX_ICONS_PER_LAYER + 1)

    def test_an_ordinary_sheet_fits_the_texture_limit(self) -> None:
        assert plan_atlas([64] * 64).fits

    def test_an_enormous_sheet_does_not(self) -> None:
        """Past the limit a browser drops the texture rather than erroring, so
        the markers would simply not appear."""
        plan = plan_atlas([MAX_ATLAS_PIXELS] * 4)
        assert not plan.fits
