"""The five newer page designs (`core/template_designs.py`): each fits every
page size, orientation and extent shape with every kind of content, nothing
collides or clips, type never drops below the minimum, and each keeps the
promise its description makes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools
import re
import subprocess
import sys
from collections.abc import Iterator

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    FrameSpec,
    LegendSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.layout_templates import (
    CHARACTERFUL,
    ESSENTIAL,
    LOCATOR_FRAME_ID,
    MAIN_FRAME_ID,
    PAGE_SIZE_ORDER,
    TITLE_LINE_HEIGHT,
    TemplateInput,
    TemplatePlan,
    plan_template,
    template_spec,
    text_lines,
)
from nika_onlymap_exporter.core.page_style import (
    MINIMUM_TYPE,
    PALETTES,
    contrast_ratio,
    parse_color,
    spacing_for,
)
from nika_onlymap_exporter.core.template_designs import (
    ATLAS_SHEET_EXPRESSION,
    NEW_TEMPLATES,
)
from nika_onlymap_exporter.core.template_registry import (
    TEMPLATES,
    get_template,
    template_ids,
)

NEW_IDS = ("night-desk", "marginalia", "atlas-ledger", "town-hall", "wayfinder")

SHAPES = {
    "wide": (4000.0, 1000.0),
    "square": (2000.0, 2000.0),
    "tall": (1000.0, 3600.0),
    "ribbon": (12000.0, 900.0),
    "needle": (800.0, 9000.0),
}

LONG_TITLE = (
    "Flood-prone parcels along the Kranji reservoir catchment and its western "
    "tributaries, surveyed September 2026"
)
LONG_NOTES = (
    "Parcels surveyed in September 2026 by the catchment field team, coloured "
    "by the use recorded on site. Housing has spread north of the main channel "
    "since the 2019 survey, mostly onto former farmland, while the parkland "
    "along the western tributaries is unchanged. Industrial parcels cluster "
    "near the trunk road. Boundaries follow the 2024 cadastre; uses were "
    "checked against aerial imagery from August 2026 where access was not "
    "possible. Classes are as recorded, not as zoned."
)
MANY_LAYERS = (
    "Soil samples",
    "Roads",
    "River",
    "Flood depth (m)",
    "Land use",
    "Planning areas",
)
MANY_LABELS = (
    ("Clay", "Silty clay", "Loam", "Sandy loam", "Sand", "Peat", "Fill", "Laterite"),
    (),
    (),
    ("0-1 m", "1-2 m", "2-3 m", "3-4 m", "4-5 m"),
    ("Housing", "Farmland", "Parkland", "Industry"),
    (),
)
VARIANTS: dict[str, dict[str, object]] = {
    "base": {},
    "no-title": {"title": ""},
    "long-title": {"title": LONG_TITLE},
    "long-names": {
        "layer_names": (
            "Agricultural and residential land use parcels, 2026 cadastral survey",
            "Sungei Kranji main channel and its western tributaries",
            "Trunk and estate roads (LTA road network, July 2026)",
        ),
        "legend_labels": (("Housing", "Farmland", "Parkland", "Industry"), (), ()),
    },
    "many-legend": {"layer_names": MANY_LAYERS, "legend_labels": MANY_LABELS},
    "no-notes": {"notes": ""},
    "long-notes": {"notes": LONG_NOTES},
    "no-credits": {"credits": ()},
    "no-layers": {"layer_names": (), "legend_labels": ()},
    "one-layer": {"layer_names": ("Parcels",), "legend_labels": ()},
    "bare": {"title": "", "notes": "", "credits": (), "layer_names": ()},
}


def _input(shape: str = "square", variant: str = "base", **overrides) -> TemplateInput:
    width, height = SHAPES[shape]
    fields: dict[str, object] = {
        "extent_width": width,
        "extent_height": height,
        "crs_is_geographic": False,
        "crs_authid": "EPSG:32648",
        "title": "Kranji parcels: land use survey, 2026",
        "layer_names": ("Roads", "River", "Land use"),
        "legend_labels": ((), (), ("Housing", "Farmland", "Parkland", "Industry")),
        "credits": ("Parcels © NIKA field survey 2026", "© OpenStreetMap contributors"),
        "notes": "Housing has spread north of the main channel since 2019.",
    }
    fields.update(VARIANTS[variant])
    fields.update(overrides)
    return TemplateInput(**fields)  # type: ignore[arg-type]


def _frames(plan: TemplatePlan) -> dict[str, FrameSpec]:
    return {
        slot.id: FrameSpec(id=slot.id, box=slot.box, center=(103.8, 1.3), zoom=12)
        for slot in plan.frames
    }


def _boxes(plan: TemplatePlan) -> list[tuple[str, Box]]:
    return [(slot.id, slot.box) for slot in plan.frames] + [
        (item.id, item.box) for item in plan.items
    ]


def _overlap(a: Box, b: Box) -> bool:
    return (
        a.x + 0.01 < b.x + b.w
        and b.x + 0.01 < a.x + a.w
        and a.y + 0.01 < b.y + b.h
        and b.y + 0.01 < a.y + a.h
    )


def _every_page() -> Iterator[tuple[str, str, str]]:
    return itertools.product(("portrait", "landscape", "auto"), SHAPES, VARIANTS)


def _text_of(item: TextSpec, data: TemplateInput) -> str:
    """What a text item shows: its tokens filled in with the input's text,
    the date at its longest, the sheet number at three digits each."""
    credits = " · ".join(c for c in data.credits if c.strip())
    body = item.body.replace(ATLAS_SHEET_EXPRESSION, "888 / 888")
    for token, value in (
        ("{{title}}", data.title),
        ("{{notes}}", data.notes),
        ("{{attribution}}", credits),
        ("{{date}}", "2026-12-31"),
    ):
        body = body.replace(token, value)
    return body


#: The least a text item may be set at, by its role.
def _least_size(item: TextSpec) -> float:
    if item.kind == "title":
        return MINIMUM_TYPE.title
    if item.kind == "attribution" or item.id == "date":
        return MINIMUM_TYPE.credit
    if item.id == "notes":
        return MINIMUM_TYPE.body
    return MINIMUM_TYPE.caption


class TestRegistry:
    def test_the_five_follow_the_ten(self) -> None:
        assert template_ids()[-5:] == NEW_IDS
        assert len(set(template_ids())) == len(TEMPLATES)
        assert TEMPLATES[-5:] == NEW_TEMPLATES

    def test_names_descriptions_category_and_cues(self) -> None:
        expected = {
            "night-desk": ("Night Desk", ESSENTIAL),
            "marginalia": ("Marginalia", CHARACTERFUL),
            "atlas-ledger": ("Atlas Ledger", CHARACTERFUL),
            "town-hall": ("Town Hall", CHARACTERFUL),
            "wayfinder": ("Wayfinder", CHARACTERFUL),
        }
        for template_id, (name, category) in expected.items():
            template = get_template(template_id)
            assert (template.name, template.category) == (name, category)
            assert template.description.endswith(".")
            assert len(template.description) <= 80
            assert template.includes and all(cue for cue in template.includes)

    def test_every_design_says_what_it_is_best_for(self) -> None:
        """The picker's "Best for" line is there on all fifteen, one line."""
        assert len(TEMPLATES) == 15
        for template in TEMPLATES:
            assert template.best_for.startswith("Best for: "), template.id
            assert len(template.best_for) > 20, template.id
            assert template.best_for.endswith("."), template.id
            assert len(template.best_for) <= 70, template.id

    @pytest.mark.parametrize(
        "first",
        [
            "nika_onlymap_exporter.core.template_designs",
            "nika_onlymap_exporter.core.layout_templates",
            "nika_onlymap_exporter.core.canvas_layout",
        ],
    )
    def test_either_module_may_be_imported_first(self, first: str) -> None:
        """Whichever template module loads first, the collection lists all
        fifteen, once each."""
        code = (
            f"import {first}\n"
            "from nika_onlymap_exporter.core import template_registry as lt\n"
            "ids = lt.template_ids()\n"
            "print(len(ids), len(set(ids)), lt.get_template('wayfinder').name)\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.split()
        assert out == ["15", "15", "Wayfinder"]


class TestEveryPage:
    @pytest.mark.parametrize(
        ("template_id", "size"), list(itertools.product(NEW_IDS, PAGE_SIZE_ORDER))
    )
    def test_on_the_page_nothing_collides_nothing_clips(
        self, template_id: str, size: str
    ) -> None:
        for orientation, shape, variant in _every_page():
            data = _input(shape, variant, page_size=size, orientation=orientation)
            plan = plan_template(template_id, data)
            case = (orientation, shape, variant)
            assert plan.frames[0].id == MAIN_FRAME_ID
            for item_id, box in _boxes(plan):
                assert box.w > 0 and box.h > 0, (case, item_id)
                assert box.x >= -0.01 and box.y >= -0.01, (case, item_id)
                assert box.x + box.w <= plan.page_width + 0.01, (case, item_id)
                assert box.y + box.h <= plan.page_height + 0.01, (case, item_id)
            # No design lays anything over the map, or one piece on another.
            for (a_id, a), (b_id, b) in itertools.combinations(_boxes(plan), 2):
                assert not _overlap(a, b), (case, a_id, b_id)
            for item in plan.items:
                if isinstance(item, TextSpec):
                    self._fits(item, data, case)
                if isinstance(item, LegendSpec):
                    assert item.font_size is not None, case
                    assert item.font_size >= MINIMUM_TYPE.legend, case
            spec = template_spec(plan, _frames(plan), cartograph_id="c")
            assert len(spec.items) == len(plan.frames) + len(plan.items)

    @staticmethod
    def _fits(item: TextSpec, data: TemplateInput, case: tuple[str, ...]) -> None:
        assert item.font_size is not None, (case, item.id)
        assert item.font_size >= _least_size(item), (case, item.id, item.font_size)
        text = _text_of(item, data)
        bold = item.weight == "bold"
        lines = text_lines(text, item.box.w, item.font_size, bold=bold)
        line_height = TITLE_LINE_HEIGHT if item.kind == "title" else 1.4
        needed = lines * item.font_size * 25.4 / 72 * line_height
        assert needed <= item.box.h + 0.01, (case, item.id, lines, item.box)

    @pytest.mark.parametrize("template_id", NEW_IDS)
    def test_the_map_is_the_largest_thing_on_the_page(self, template_id: str) -> None:
        for size, shape in itertools.product(
            ("A4", "A3", "Letter", "Tabloid", "A5", "A1"), ("wide", "square", "tall")
        ):
            plan = plan_template(template_id, _input(shape, page_size=size))
            frame = plan.frames[0].box
            share = frame.w * frame.h / (plan.page_width * plan.page_height)
            # Wayfinder's band is long by design: a quarter of the sheet.
            least = 0.25 if template_id == "wayfinder" else 0.3
            assert share >= least, (size, shape, share)
            for item_id, box in _boxes(plan)[1:]:
                assert box.w * box.h < frame.w * frame.h, (size, shape, item_id)

    @pytest.mark.parametrize("template_id", NEW_IDS)
    def test_auto_follows_the_map(self, template_id: str) -> None:
        assert plan_template(template_id, _input("wide")).orientation == "landscape"
        assert plan_template(template_id, _input("tall")).orientation == "portrait"


class TestOnlyRealContent:
    @pytest.mark.parametrize(
        ("template_id", "variant"), list(itertools.product(NEW_IDS, VARIANTS))
    )
    def test_text_is_project_tokens_or_the_sheet_number(
        self, template_id: str, variant: str
    ) -> None:
        stand_ins = {"notes": "Écrivez ici.", "figure": "42", "author": "Moi"}
        for size, orientation in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape")
        ):
            data = _input(
                variant=variant,
                page_size=size,
                orientation=orientation,
                placeholders=stand_ins,
            )
            plan = plan_template(template_id, data)
            for item in plan.items:
                if not isinstance(item, TextSpec):
                    continue
                if item.body == ATLAS_SHEET_EXPRESSION:
                    continue
                words = re.sub(r"\{\{[^{}]*\}\}", "", item.body).strip()
                assert not words, (item.id, item.body)
                for stand_in in stand_ins.values():
                    assert stand_in not in item.body
            bodies = {i.body for i in plan.items if isinstance(i, TextSpec)}
            assert ("{{title}}" in bodies) == bool(data.title.strip())
            assert ("{{attribution}}" in bodies) == bool(data.credits)
            legends = [i for i in plan.items if isinstance(i, LegendSpec)]
            assert bool(legends) == bool(data.layer_names)


def _plan(template_id: str, shape: str = "square", variant: str = "base", **kw):
    return plan_template(template_id, _input(shape, variant, **kw))


def _item(plan: TemplatePlan, item_id: str):
    return next(item for item in plan.items if item.id == item_id)


class TestColours:
    @pytest.mark.parametrize("template_id", NEW_IDS)
    def test_each_design_chooses_its_page_and_frame_colours(
        self, template_id: str
    ) -> None:
        """Explicit colours reach the HTML whatever its theme: a light page
        is as white (or paper) there as in QGIS."""
        plan = _plan(template_id)
        spec = template_spec(plan, _frames(plan), cartograph_id="c")
        assert spec.bg and spec.color
        assert all(slot.frame_bg and slot.frame_stroke for slot in plan.frames)
        page = parse_color(spec.bg)
        assert page is not None
        for item in plan.items:
            if isinstance(item, TextSpec) and item.color:
                ink = parse_color(item.color)
                assert ink is not None
                assert contrast_ratio(ink, page) >= 4.5, item.id
            if isinstance(item, LegendSpec):
                assert item.bg == spec.bg and item.color == spec.color

    def test_night_desk_is_a_dark_sheet_with_light_ink(self) -> None:
        plan = _plan("night-desk")
        page = parse_color(plan.bg or "")
        ink = parse_color(plan.color or "")
        assert page is not None and ink is not None
        assert PALETTES["aubergine"].page == plan.bg
        assert contrast_ratio(ink, page) >= 7.0
        scalebar = next(i for i in plan.items if isinstance(i, ScalebarSpec))
        assert scalebar.color == plan.color

    @pytest.mark.parametrize("template_id", NEW_IDS[1:])
    def test_the_light_designs_are_light(self, template_id: str) -> None:
        plan = _plan(template_id)
        assert plan.bg in (PALETTES["daylight"].page, PALETTES["paper"].page)


class TestNightDesk:
    def test_an_inset_map_with_generous_margins(self) -> None:
        for size, orientation in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape")
        ):
            plan = _plan("night-desk", page_size=size, orientation=orientation)
            frame = plan.frames[0].box
            margin = min(
                frame.x,
                frame.y,
                plan.page_width - frame.x - frame.w,
                plan.page_height - frame.y - frame.h,
            )
            assert margin >= 11.0, (size, orientation, margin)
            assert _item(plan, "title").box.y + _item(plan, "title").box.h < frame.y

    def test_no_title_no_brass_rule(self) -> None:
        plan = _plan("night-desk", variant="no-title")
        assert not [i for i in plan.items if i.id in ("title", "accent")]


class TestMarginalia:
    @pytest.mark.parametrize("orientation", ["portrait", "landscape"])
    def test_the_note_sits_in_the_outside_margin(self, orientation: str) -> None:
        plan = _plan("marginalia", orientation=orientation)
        frame = plan.frames[0].box
        notes = _item(plan, "notes")
        assert notes.body == "{{notes}}"
        assert notes.box.x >= frame.x + frame.w
        # A narrow margin, never a second column as wide as the map.
        assert notes.box.w <= 0.4 * frame.w

    @pytest.mark.parametrize("orientation", ["portrait", "landscape"])
    def test_without_notes_the_map_takes_the_margin(self, orientation: str) -> None:
        kept = _plan("marginalia", orientation=orientation)
        bare = _plan("marginalia", variant="no-notes", orientation=orientation)
        assert not any(i.id in ("notes", "accent") for i in bare.items)
        assert bare.frames[0].box.w > kept.frames[0].box.w * 1.2
        assert bare.frames[0].box.w == pytest.approx(
            bare.page_width - 2 * bare.frames[0].box.x
        )

    def test_long_notes_stay_readable(self) -> None:
        for size in PAGE_SIZE_ORDER:
            plan = _plan("marginalia", variant="long-notes", page_size=size)
            notes = _item(plan, "notes")
            assert notes.font_size >= MINIMUM_TYPE.body


class TestAtlasLedger:
    def test_a_locator_outlines_the_main_map(self) -> None:
        plan = _plan("atlas-ledger")
        (main, locator) = plan.frames
        assert locator.id == LOCATOR_FRAME_ID and locator.overview_of == MAIN_FRAME_ID
        assert locator.zoom_out > 1
        assert locator.box.w * locator.box.h < 0.15 * main.box.w * main.box.h

    def test_the_header_names_the_map_and_the_sheet(self) -> None:
        plan = _plan("atlas-ledger")
        title = _item(plan, "title")
        sheet = _item(plan, "sheet")
        assert title.body == "{{title}}"
        assert sheet.body == ATLAS_SHEET_EXPRESSION
        assert sheet.align == "end"
        assert abs(sheet.box.y - title.box.y) < 0.01
        # The sheet number stays even without a title: it is blank on a
        # single page and filled in on atlas pages.
        untitled = _plan("atlas-ledger", variant="no-title")
        assert _item(untitled, "sheet").body == ATLAS_SHEET_EXPRESSION

    def test_the_sheet_expression_is_what_the_export_translates(self) -> None:
        from nika_onlymap_exporter.core.expression_tokens import translate_label

        live = translate_label(
            ATLAS_SHEET_EXPRESSION, frame_id="main", frame_is_live=True, in_atlas=True
        )
        assert live.template == "{{atlas.index}} / {{atlas.count}}"
        assert not live.frozen_expressions
        single = translate_label(
            ATLAS_SHEET_EXPRESSION, frame_id="main", frame_is_live=True, in_atlas=False
        )
        assert single.frozen_expressions


class TestTownHall:
    def test_large_type_throughout(self) -> None:
        for size, shape, variant in itertools.product(
            PAGE_SIZE_ORDER, SHAPES, ("base", "many-legend", "long-names")
        ):
            plan = _plan("town-hall", shape, variant, page_size=size)
            legend = next(i for i in plan.items if isinstance(i, LegendSpec))
            if (size, variant) == ("A5", "many-legend") and legend.font_size < 9.0:
                # Large print cannot hold 23 rows beside a map on a small
                # sheet: the legend is set smaller to fit its box, never
                # below the minimum (rather than QGIS shrinking it more).
                assert legend.font_size >= MINIMUM_TYPE.legend, (size, shape)
            else:
                assert legend.font_size >= 9.0, (size, shape, variant)
            title = _item(plan, "title")
            assert title.font_size >= 18.0 * min(1.0, plan.page_width / 210), size
            credits = _item(plan, "attribution")
            assert credits.font_size >= 8.0
            assert credits.color == plan.color

    def test_bigger_legend_type_than_the_quiet_designs(self) -> None:
        def legend_size(template_id: str) -> float:
            plan = _plan(template_id)
            legend = next(i for i in plan.items if isinstance(i, LegendSpec))
            return legend.font_size or 0.0

        assert legend_size("town-hall") >= 1.4 * legend_size("night-desk")

    def test_no_north_arrow_and_nothing_over_the_map(self) -> None:
        plan = _plan("town-hall", "wide", page_size="A3")
        frame = plan.frames[0].box
        assert not any(i.id == "north" for i in plan.items)
        assert not any(_overlap(frame, i.box) for i in plan.items)


class TestWayfinder:
    def test_a_long_extent_gets_a_long_band(self) -> None:
        for size in PAGE_SIZE_ORDER:
            plan = _plan("wayfinder", "ribbon", page_size=size)
            frame = plan.frames[0].box
            assert plan.orientation == "landscape"
            assert frame.w / frame.h >= 1.8 - 1e-6, size
            assert frame.w / frame.h <= 3.2 + 1e-6, size
            down = _plan("wayfinder", "needle", page_size=size)
            frame = down.frames[0].box
            assert down.orientation == "portrait"
            assert frame.h / frame.w >= 2.2, size
            assert frame.h / frame.w <= 3.2 + 1e-6, size

    def test_a_round_extent_still_gets_a_usable_map(self) -> None:
        for size, orientation in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape")
        ):
            plan = _plan("wayfinder", "square", page_size=size, orientation=orientation)
            frame = plan.frames[0].box
            short, long = sorted((frame.w, frame.h))
            assert long / short <= 3.2 + 1e-6, (size, orientation)
            assert short >= 0.3 * min(plan.page_width, plan.page_height)

    def test_a_distance_scale_and_no_invented_route(self) -> None:
        plan = _plan("wayfinder", "wide", page_size="A3")
        (scalebar,) = [i for i in plan.items if isinstance(i, ScalebarSpec)]
        assert scalebar.kind == "ticks"
        assert scalebar.box.w >= 60.0
        # Only furniture: no shapes but the title's mark, no text but tokens.
        shapes = [i.id for i in plan.items if isinstance(i, ShapeSpec)]
        assert shapes == ["accent"]

    @pytest.mark.parametrize("size", PAGE_SIZE_ORDER)
    @pytest.mark.parametrize("shape", ["wide", "ribbon", "square"])
    def test_the_title_keeps_the_top_margin(self, size: str, shape: str) -> None:
        """A band lower than the room never pushes the title down: no empty
        band over it. The room lets the band grow, still a band (at least
        1.8 : 1), and the source line keeps the sheet's foot."""
        plan = _plan("wayfinder", shape, page_size=size, orientation="landscape")
        spacing = spacing_for(plan.page_width, plan.page_height)
        margin = spacing.margin
        (title,) = [i for i in plan.items if i.id == "title"]
        assert title.box.y == pytest.approx(margin, abs=0.01)
        assert min(b.y for _, b in _boxes(plan)) == pytest.approx(margin, abs=0.01)
        frame = plan.frames[0].box
        assert frame.y == pytest.approx(
            title.box.y + title.box.h + 1.5 * spacing.gutter, abs=0.01
        )
        assert frame.w / frame.h >= 1.8 - 1e-6 or shape == "square"
        bottom = max(b.y + b.h for _, b in _boxes(plan))
        assert bottom == pytest.approx(plan.page_height - margin, abs=0.01)
        (source,) = [i for i in plan.items if i.id == "attribution"]
        assert source.box.y + source.box.h == pytest.approx(bottom, abs=0.01)

    def test_room_a_long_band_leaves_lets_it_grow(self) -> None:
        """A4 landscape and a wide extent: the band is taller than the
        3.2 : 1 the extent alone would get -- as tall as the room under the
        title, which on A4 is still a band -- and the legend strip sits
        right under it."""
        plan = _plan("wayfinder", "ribbon", page_size="A4", orientation="landscape")
        frame = plan.frames[0].box
        assert 1.8 - 1e-6 <= frame.w / frame.h < 3.2 - 0.5
        gutter = spacing_for(plan.page_width, plan.page_height).gutter
        legend = next(i for i in plan.items if isinstance(i, LegendSpec))
        assert legend.box.y == pytest.approx(frame.y + frame.h + gutter, abs=0.01)

    @pytest.mark.parametrize("size", PAGE_SIZE_ORDER)
    @pytest.mark.parametrize("shape", ["wide", "ribbon", "square", "tall"])
    @pytest.mark.parametrize(
        "variant", ["base", "no-title", "long-names", "many-legend", "no-credits"]
    )
    def test_a_landscape_sheet_has_no_empty_band(
        self, size: str, shape: str, variant: str
    ) -> None:
        """Wherever the band stops growing, the page is composed down to its
        foot: no stretch of the page inside its margins, across its whole
        width, that nothing is planned in is taller than 6 % of the page
        (the renders' own measure, `tests/qgis`, allows 10 %, for the room
        a legend's box keeps under its rows)."""
        plan = _plan(
            "wayfinder", shape, variant, page_size=size, orientation="landscape"
        )
        margin = spacing_for(plan.page_width, plan.page_height).margin
        spans = sorted((b.y, b.y + b.h) for _, b in _boxes(plan))
        reached, gap = margin, 0.0
        for top, bottom in spans:
            gap = max(gap, top - reached)
            reached = max(reached, bottom)
        gap = max(gap, plan.page_height - margin - reached)
        assert gap <= 0.06 * plan.page_height, (gap, plan.page_height)
