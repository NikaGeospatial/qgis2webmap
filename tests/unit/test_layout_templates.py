"""The ten layout templates: every design fits its page on every size and
orientation, and each keeps the promises its description makes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools

import pytest

from nika_onlymap_exporter.core.canvas_layout import (
    CanvasLayoutInput,
    plan_canvas_layout,
)
from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.layout_templates import (
    LOCATOR_FRAME_ID,
    MAIN_FRAME_ID,
    PAGE_SIZE_ORDER,
    TEMPLATES,
    TemplateInput,
    TemplatePlan,
    plan_template,
    template_ids,
    template_spec,
)

SHAPES = {
    "wide": (4000.0, 1500.0),
    "square": (2000.0, 2000.0),
    "tall": (1200.0, 3600.0),
}


def _input(shape: str = "square", **overrides) -> TemplateInput:
    width, height = SHAPES[shape]
    fields = {
        "extent_width": width,
        "extent_height": height,
        "crs_is_geographic": False,
        "crs_authid": "EPSG:32648",
        "title": "Parcels",
        "layer_names": ("parcels", "roads"),
        "credits": ("© OpenStreetMap contributors",),
    }
    fields.update(overrides)
    return TemplateInput(**fields)


def _frames(plan: TemplatePlan) -> dict[str, FrameSpec]:
    return {
        slot.id: FrameSpec(id=slot.id, box=slot.box, center=(103.8, 1.3), zoom=12)
        for slot in plan.frames
    }


def _boxes(plan: TemplatePlan) -> list[tuple[str, Box]]:
    return [(slot.id, slot.box) for slot in plan.frames] + [
        (item.id, item.box) for item in plan.items
    ]


class TestEveryDesignFitsEveryPage:
    @pytest.mark.parametrize(
        ("template_id", "size", "orientation", "shape"),
        list(
            itertools.product(
                template_ids(),
                PAGE_SIZE_ORDER,
                ("auto", "portrait", "landscape"),
                SHAPES,
            )
        ),
    )
    def test_on_the_page_and_a_valid_cartograph(
        self, template_id: str, size: str, orientation: str, shape: str
    ) -> None:
        plan = plan_template(
            template_id, _input(shape, page_size=size, orientation=orientation)
        )
        assert plan.frames[0].id == MAIN_FRAME_ID
        tolerance = 0.01
        for item_id, box in _boxes(plan):
            assert box.w > 0 and box.h > 0, item_id
            assert box.x >= -tolerance and box.y >= -tolerance, item_id
            assert box.x + box.w <= plan.page_width + tolerance, item_id
            assert box.y + box.h <= plan.page_height + tolerance, item_id
        # The IR validates ids, frame references and page fields.
        spec = template_spec(plan, _frames(plan), cartograph_id="c")
        assert len(spec.items) == len(plan.frames) + len(plan.items)

    @pytest.mark.parametrize("template_id", template_ids())
    def test_the_main_map_takes_a_real_share_of_the_page(
        self, template_id: str
    ) -> None:
        for size in ("A4", "A3", "Letter", "Tabloid"):
            plan = plan_template(template_id, _input(page_size=size))
            frame = plan.frames[0].box
            assert frame.w * frame.h >= 0.25 * plan.page_width * plan.page_height


#: Designs that lay furniture over the map on purpose (story map, dark
#: full-bleed, infographic tiles on a band) are checked only for what must
#: still not collide: no two pieces of furniture on top of each other.
_OVERLAID = {"story-map", "dark", "dashboard", "field-map"}
#: Boxes that are backgrounds for what sits on them, by design.
_BACKGROUNDS = ("border", "title-block", "band", "scale-plate", "tile-", "rule")


def _overlap(a: Box, b: Box) -> bool:
    return (
        a.x + 0.01 < b.x + b.w
        and b.x + 0.01 < a.x + a.w
        and a.y + 0.01 < b.y + b.h
        and b.y + 0.01 < a.y + a.h
    )


class TestNothingCollides:
    @pytest.mark.parametrize(
        ("template_id", "size", "orientation", "shape"),
        list(
            itertools.product(
                template_ids(),
                ("A4", "A3", "Letter", "Tabloid"),
                ("portrait", "landscape"),
                SHAPES,
            )
        ),
    )
    def test_no_two_pieces_overlap(
        self, template_id: str, size: str, orientation: str, shape: str
    ) -> None:
        plan = plan_template(
            template_id, _input(shape, page_size=size, orientation=orientation)
        )
        boxes = [
            (name, box)
            for name, box in _boxes(plan)
            if not name.startswith(_BACKGROUNDS) and name != "graticule"
        ]
        if template_id in _OVERLAID:
            boxes = [(n, b) for n, b in boxes if n != MAIN_FRAME_ID]
        for (name_a, a), (name_b, b) in itertools.combinations(boxes, 2):
            if {name_a, name_b} <= {"title", "subtitle"}:
                continue
            same_tile = name_a.split("-")[:2] == name_b.split("-")[:2]
            assert not _overlap(a, b) or same_tile, (name_a, name_b)


class TestOrientation:
    @pytest.mark.parametrize("template_id", template_ids())
    def test_auto_follows_the_map(self, template_id: str) -> None:
        assert plan_template(template_id, _input("wide")).orientation == "landscape"
        assert plan_template(template_id, _input("tall")).orientation == "portrait"

    def test_a_chosen_orientation_is_kept(self) -> None:
        plan = plan_template("poster", _input("wide", orientation="portrait"))
        assert plan.orientation == "portrait"
        assert plan.page_width < plan.page_height


class TestPage:
    def test_a_runtime_preset_is_named_and_tabloid_is_measured(self) -> None:
        a4 = plan_template("classic", _input(page_size="A4"))
        spec = template_spec(a4, _frames(a4), cartograph_id="c")
        assert spec.size == "A4" and spec.width is None
        tabloid = plan_template("classic", _input("tall", page_size="Tabloid"))
        spec = template_spec(tabloid, _frames(tabloid), cartograph_id="c")
        assert spec.size is None
        assert (spec.width, spec.height) == pytest.approx((279.4, 431.8))

    def test_unknown_names_are_refused(self) -> None:
        with pytest.raises(ValueError, match="template"):
            plan_template("nope", _input())
        with pytest.raises(ValueError, match="page size"):
            plan_template("classic", _input(page_size="B5"))
        with pytest.raises(ValueError, match="orientation"):
            plan_template("classic", _input(orientation="sideways"))
        with pytest.raises(ValueError, match="extent_width"):
            plan_template("classic", _input(extent_width=0.0))


class TestDesigns:
    def test_ten_designs_with_stable_ids(self) -> None:
        assert template_ids() == (
            "classic",
            "legend-below",
            "map-only",
            "report-figure",
            "field-map",
            "poster",
            "locator",
            "story-map",
            "dashboard",
            "dark",
        )
        assert all(t.name and t.description for t in TEMPLATES)

    def test_classic_is_the_one_click_layout(self) -> None:
        data = _input("wide", page_size="A3")
        plan = plan_template("classic", data)
        one_click = plan_canvas_layout(
            CanvasLayoutInput(
                data.extent_width,
                data.extent_height,
                False,
                page_size="A3",
                title=data.title,
                layer_names=data.layer_names,
                credits=data.credits,
            )
        )
        assert plan.frames[0].box == one_click.frame
        assert plan.orientation == one_click.orientation

    def test_legend_below_puts_the_legend_under_the_map(self) -> None:
        plan = plan_template("legend-below", _input("wide"))
        frame = plan.frames[0].box
        (legend,) = [i for i in plan.items if isinstance(i, LegendSpec)]
        assert legend.box.y >= frame.y + frame.h

    def test_map_only_keeps_nothing_but_the_credits(self) -> None:
        plan = plan_template("map-only", _input())
        assert [item.id for item in plan.items] == ["attribution"]
        bare = plan_template("map-only", _input(credits=()))
        assert bare.items == ()

    def test_report_figure_keeps_the_map_aspect_and_has_no_legend(self) -> None:
        plan = plan_template("report-figure", _input("wide"))
        frame = plan.frames[0].box
        assert frame.w / frame.h == pytest.approx(4000 / 1500)
        assert not any(isinstance(i, LegendSpec) for i in plan.items)

    def test_field_map_grids_in_the_map_crs(self) -> None:
        projected = plan_template("field-map", _input())
        (grid,) = [i for i in projected.items if isinstance(i, GraticuleSpec)]
        assert grid.crs == "EPSG:32648" and grid.labels == "outside"
        geographic = plan_template(
            "field-map",
            _input(extent_width=2.0, extent_height=1.0, crs_is_geographic=True),
        )
        (grid,) = [i for i in geographic.items if isinstance(i, GraticuleSpec)]
        assert grid.crs == "EPSG:4326"
        assert grid.interval == pytest.approx(0.2)

    @pytest.mark.parametrize("template_id", ["locator", "story-map"])
    def test_a_locator_outlines_the_main_map(self, template_id: str) -> None:
        plan = plan_template(template_id, _input())
        locator = next(s for s in plan.frames if s.id == LOCATOR_FRAME_ID)
        assert locator.overview_of == MAIN_FRAME_ID
        assert locator.zoom_out > 1
        spec = template_spec(plan, _frames(plan), cartograph_id="c")
        built = next(i for i in spec.items if i.id == LOCATOR_FRAME_ID)
        assert isinstance(built, FrameSpec) and built.overview_of == MAIN_FRAME_ID

    def test_dark_sets_the_page_colours(self) -> None:
        plan = plan_template("dark", _input())
        spec = template_spec(plan, _frames(plan), cartograph_id="c")
        assert spec.bg and spec.color
        assert spec.bg != spec.color

    @pytest.mark.parametrize("template_id", template_ids())
    def test_no_title_means_no_empty_title_box(self, template_id: str) -> None:
        plan = plan_template(template_id, _input(title="  "))
        assert not any(item.id == "title" for item in plan.items)

    @pytest.mark.parametrize("template_id", ["poster", "story-map"])
    def test_the_text_panel_shows_the_notes_or_the_placeholder(
        self, template_id: str
    ) -> None:
        with_notes = plan_template(template_id, _input(notes="Flood risk, 2026."))
        notes = next(i for i in with_notes.items if i.id == "notes")
        assert isinstance(notes, TextSpec) and notes.body == "{{notes}}"
        spec = template_spec(with_notes, _frames(with_notes), cartograph_id="c")
        assert spec.notes == "Flood risk, 2026."
        empty = plan_template(
            template_id, _input(placeholders={"notes": "Écrivez ici."})
        )
        notes = next(i for i in empty.items if i.id == "notes")
        assert isinstance(notes, TextSpec) and notes.body == "Écrivez ici."

    def test_no_layers_means_no_legend(self) -> None:
        for template_id in template_ids():
            plan = plan_template(template_id, _input(layer_names=()))
            assert not any(isinstance(i, LegendSpec) for i in plan.items), template_id


class TestSpecAssembly:
    def test_frames_must_sit_on_their_planned_boxes(self) -> None:
        plan = plan_template("classic", _input())
        frames = _frames(plan)
        moved = FrameSpec(
            id=MAIN_FRAME_ID, box=Box(0, 0, 10, 10), center=(0, 0), zoom=1
        )
        with pytest.raises(ValueError, match="planned"):
            template_spec(plan, {**frames, MAIN_FRAME_ID: moved}, cartograph_id="c")
        with pytest.raises(ValueError, match="no frame"):
            template_spec(plan, {}, cartograph_id="c")

    def test_frames_stack_under_everything_else(self) -> None:
        plan = plan_template("story-map", _input())
        spec = template_spec(plan, _frames(plan), cartograph_id="c")
        frame_z = max(i.z or 0 for i in spec.items if isinstance(i, FrameSpec))
        other_z = min(i.z or 0 for i in spec.items if not isinstance(i, FrameSpec))
        assert frame_z < other_z
