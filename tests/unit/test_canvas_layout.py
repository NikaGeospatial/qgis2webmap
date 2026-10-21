"""`core/canvas_layout.py`: the one-click page plan, pure arithmetic.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools

import pytest

from nika_onlymap_exporter.core.canvas_layout import (
    CanvasLayoutInput,
    CanvasLayoutPlan,
    canvas_spec,
    legend_row_count,
    nice_interval,
    page_dimensions,
    plan_canvas_layout,
)
from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    TextSpec,
)

_EPS = 1e-6


def _boxes(plan: CanvasLayoutPlan) -> dict[str, Box]:
    boxes = {"frame": plan.frame, "scalebar": plan.scalebar, "north": plan.north}
    for name in ("legend", "title", "attribution"):
        box = getattr(plan, name)
        if box is not None:
            boxes[name] = box
    return boxes


def _overlap(a: Box, b: Box) -> bool:
    return (
        a.x < b.x + b.w - _EPS
        and b.x < a.x + a.w - _EPS
        and a.y < b.y + b.h - _EPS
        and b.y < a.y + a.h - _EPS
    )


def _inside(box: Box, plan: CanvasLayoutPlan) -> bool:
    m = plan.margin
    return (
        box.x >= m - _EPS
        and box.y >= m - _EPS
        and box.x + box.w <= plan.page_width - m + _EPS
        and box.y + box.h <= plan.page_height - m + _EPS
    )


class TestPageDimensions:
    def test_a4_portrait_and_landscape(self) -> None:
        assert page_dimensions("A4", "portrait") == (210.0, 297.0)
        assert page_dimensions("A4", "landscape") == (297.0, 210.0)

    def test_letter(self) -> None:
        assert page_dimensions("Letter", "portrait") == pytest.approx((215.9, 279.4))

    def test_unknown_size_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            page_dimensions("B5", "portrait")

    def test_unknown_orientation_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            page_dimensions("A4", "sideways")


class TestOrientation:
    def test_wide_extent_is_landscape(self) -> None:
        plan = plan_canvas_layout(CanvasLayoutInput(4000, 1000, False))
        assert plan.orientation == "landscape"
        assert plan.page_width > plan.page_height

    def test_tall_extent_is_portrait(self) -> None:
        plan = plan_canvas_layout(CanvasLayoutInput(1000, 4000, False))
        assert plan.orientation == "portrait"
        assert plan.page_width < plan.page_height

    def test_orientation_flips_as_the_extent_turns(self) -> None:
        wide = plan_canvas_layout(CanvasLayoutInput(1500, 1000, False))
        tall = plan_canvas_layout(CanvasLayoutInput(1000, 1500, False))
        assert (wide.orientation, tall.orientation) == ("landscape", "portrait")

    def test_explicit_orientation_wins(self) -> None:
        plan = plan_canvas_layout(
            CanvasLayoutInput(4000, 1000, False, orientation="portrait")
        )
        assert plan.orientation == "portrait"

    def test_bad_orientation_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_canvas_layout(CanvasLayoutInput(1, 1, False, orientation="up"))

    def test_degenerate_extent_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_canvas_layout(CanvasLayoutInput(0, 1000, False))
        with pytest.raises(ValueError):
            plan_canvas_layout(CanvasLayoutInput(1000, float("nan"), False))


_CASES = [
    CanvasLayoutInput(w, h, geo, page_size=size, title=title, layer_names=layers)
    for (w, h), geo, size, title, layers in itertools.product(
        [(4000, 1000), (1000, 4000), (1000, 1000), (1600, 900), (10, 9)],
        [False, True],
        ["A4", "A3", "Letter", "A5"],
        ["", "Survey"],
        [(), ("roads", "parcels", "rivers", "a much longer layer name")],
    )
]


@pytest.mark.parametrize("data", _CASES)
class TestPlacementInvariants:
    def test_frame_keeps_the_extent_aspect(self, data: CanvasLayoutInput) -> None:
        plan = plan_canvas_layout(data)
        assert plan.frame.w / plan.frame.h == pytest.approx(
            data.extent_width / data.extent_height, rel=1e-9
        )

    def test_everything_inside_the_margins(self, data: CanvasLayoutInput) -> None:
        plan = plan_canvas_layout(data)
        for name, box in _boxes(plan).items():
            assert _inside(box, plan), name

    def test_nothing_overlaps(self, data: CanvasLayoutInput) -> None:
        plan = plan_canvas_layout(data)
        for (name_a, a), (name_b, b) in itertools.combinations(_boxes(plan).items(), 2):
            assert not _overlap(a, b), (name_a, name_b, a, b)


class TestFurniture:
    def test_legend_only_when_there_are_layers(self) -> None:
        assert plan_canvas_layout(CanvasLayoutInput(1000, 800, False)).legend is None
        plan = plan_canvas_layout(
            CanvasLayoutInput(1000, 800, False, layer_names=("a",))
        )
        assert plan.legend is not None

    def test_wide_map_puts_the_legend_below_and_a_tall_one_to_the_right(
        self,
    ) -> None:
        layers = ("a", "b", "c")
        wide = plan_canvas_layout(
            CanvasLayoutInput(
                5000, 1000, False, orientation="portrait", layer_names=layers
            )
        )
        tall = plan_canvas_layout(
            CanvasLayoutInput(
                1000, 5000, False, orientation="landscape", layer_names=layers
            )
        )
        assert wide.legend_position == "below"
        assert wide.legend is not None and wide.legend.y >= wide.frame.y + wide.frame.h
        assert tall.legend_position == "right"
        assert tall.legend is not None and tall.legend.x >= tall.frame.x + tall.frame.w

    def test_a_layer_with_classes_is_a_heading_and_a_row_each(self) -> None:
        assert legend_row_count(("a", "b")) == 2
        assert legend_row_count(("a", "b"), (("a",), ("x", "y", "z"))) == 1 + 4
        # A layer missing from the labels is one row, its name.
        assert legend_row_count(("a", "b"), (("x", "y"),)) == 3 + 1

    def test_the_legend_has_room_for_every_class(self) -> None:
        names = ("Parcels",)
        plain = plan_canvas_layout(
            CanvasLayoutInput(1000, 5000, False, "A4", "landscape", layer_names=names)
        )
        classes = plan_canvas_layout(
            CanvasLayoutInput(
                1000,
                5000,
                False,
                "A4",
                "landscape",
                layer_names=names,
                legend_labels=(
                    ("Residential", "Mixed use and commerce", "Industrial", "Park"),
                ),
            )
        )
        assert plain.legend is not None and classes.legend is not None
        assert classes.legend_position == plain.legend_position == "right"
        assert classes.legend.h == pytest.approx(plain.legend.h + 4 * 6.0)
        # Wide enough for the longest class label, not just the layer name.
        assert classes.legend.w > plain.legend.w

    def test_scalebar_bottom_left_and_north_top_right(self) -> None:
        plan = plan_canvas_layout(CanvasLayoutInput(1600, 900, False, title="T"))
        assert plan.scalebar.x == pytest.approx(plan.frame.x)
        assert plan.scalebar.y > plan.frame.y + plan.frame.h
        assert plan.north.x + plan.north.w == pytest.approx(plan.frame.x + plan.frame.w)
        assert plan.north.y + plan.north.h <= plan.frame.y

    def test_title_and_attribution_only_when_given(self) -> None:
        bare = plan_canvas_layout(CanvasLayoutInput(1600, 900, False))
        assert bare.title is None and bare.attribution is None
        full = plan_canvas_layout(
            CanvasLayoutInput(1600, 900, False, title="T", credits=("OSM", "Esri"))
        )
        assert full.title is not None and full.attribution is not None
        assert full.attribution_text == "OSM · Esri"

    def test_blank_and_duplicate_credits_are_dropped(self) -> None:
        plan = plan_canvas_layout(
            CanvasLayoutInput(1600, 900, False, credits=("OSM", " ", "OSM", "Esri"))
        )
        assert plan.attribution_text == "OSM · Esri"

    def test_graticule_only_for_geographic_crs(self) -> None:
        projected = plan_canvas_layout(CanvasLayoutInput(1600, 900, False))
        geographic = plan_canvas_layout(CanvasLayoutInput(16, 9, True))
        assert projected.graticule_interval is None
        assert geographic.graticule_interval == pytest.approx(2.0)


class TestNiceInterval:
    @pytest.mark.parametrize(
        ("raw", "nice"),
        [(4.0, 5.0), (3.0, 2.0), (0.7, 0.5), (12, 10), (1.0, 1.0), (180, 200)],
    )
    def test_snaps_to_one_two_five(self, raw: float, nice: float) -> None:
        assert nice_interval(raw) == pytest.approx(nice)

    def test_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            nice_interval(0)


class TestCanvasSpec:
    def _frame(self, box: Box) -> FrameSpec:
        return FrameSpec(id="main", box=box, center=(0.0, 0.0), zoom=3.0)

    def test_spec_carries_every_planned_item(self) -> None:
        plan = plan_canvas_layout(
            CanvasLayoutInput(
                16,
                9,
                True,
                title="Survey",
                layer_names=("a",),
                credits=("OSM",),
            )
        )
        spec = canvas_spec(plan, self._frame(plan.frame), cartograph_id="cg")
        kinds = {type(item) for item in spec.items}
        assert kinds == {
            FrameSpec,
            GraticuleSpec,
            LegendSpec,
            ScalebarSpec,
            NorthSpec,
            TextSpec,
        }
        assert spec.size == "A4" and spec.orientation == plan.orientation
        assert spec.title == "Survey" and spec.attribution == "OSM"
        title = next(
            i for i in spec.items if isinstance(i, TextSpec) and i.kind == "title"
        )
        assert title.body == "{{title}}"

    def test_frame_box_must_match_the_plan(self) -> None:
        plan = plan_canvas_layout(CanvasLayoutInput(1600, 900, False))
        with pytest.raises(ValueError):
            canvas_spec(plan, self._frame(Box(0, 0, 1, 1)), cartograph_id="cg")
