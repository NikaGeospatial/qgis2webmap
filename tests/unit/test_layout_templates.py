"""The ten layout templates: every design fits its page on every size and
orientation, and each keeps the promises its description makes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools
import re

import pytest

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.layout_templates import (
    LOCATOR_FRAME_ID,
    MAIN_FRAME_ID,
    PAGE_SIZE_ORDER,
    TEXT_LINE_HEIGHT,
    TITLE_LINE_HEIGHT,
    FrameSlot,
    TemplateInput,
    TemplatePlan,
    TextSizes,
    plan_template,
    template_spec,
    text_height,
    text_percent,
)
from nika_onlymap_exporter.core.page_style import MINIMUM_TYPE, parse_color
from nika_onlymap_exporter.core.template_designs import ATLAS_SHEET_EXPRESSION
from nika_onlymap_exporter.core.template_registry import TEMPLATES, template_ids

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


def _area(box: Box) -> float:
    return box.w * box.h


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
_OVERLAID = {"story-map", "dashboard", "field-map"}
#: Boxes that are backgrounds for what sits on them, by design.
_BACKGROUNDS = (
    "border",
    "title-block",
    "band",
    "scale-plate",
    "tile-",
    "rule",
    "panel-",
)


def _contains(outer: Box, inner: Box) -> bool:
    return (
        inner.x >= outer.x - 0.01
        and inner.y >= outer.y - 0.01
        and inner.x + inner.w <= outer.x + outer.w + 0.01
        and inner.y + inner.h <= outer.y + outer.h + 0.01
    )


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


def _view_share(plan: TemplatePlan, data: TemplateInput) -> float:
    """How much of the main frame the canvas view fills; the rest is padding
    the template added. Whitespace inside the view itself is the user's and
    counts as view."""
    box = plan.frames[0].focus or plan.frames[0].box
    aspect = data.extent_width / data.extent_height
    w = min(box.w, box.h * aspect)
    return (w * (w / aspect)) / (box.w * box.h)


class TestFieldbookFraming:
    """Fieldbook kept its block and legend in a column beside the map on a
    landscape sheet, so a wide view got a near-square frame and filled about
    half of it. It now takes the column or a band under the map, whichever
    shows the view larger."""

    @pytest.mark.parametrize("size", ["A4", "A3"])
    @pytest.mark.parametrize("shape", ["wide", "tall"])
    def test_a_long_view_fills_most_of_its_frame(self, shape, size) -> None:
        data = _input(shape, page_size=size)
        plan = plan_template("field-map", data)
        assert _view_share(plan, data) >= 0.7

    @pytest.mark.parametrize("size", ["A4", "A3"])
    @pytest.mark.parametrize("shape", ["wide", "tall"])
    @pytest.mark.parametrize(
        "template_id",
        ["poster", "locator", "dashboard", "town-hall", "night-desk", "atlas-ledger"],
    )
    def test_broadside_and_near_and_far_choose_too(
        self, template_id, shape, size
    ) -> None:
        """Their rail or column beside the map, or band under it, was fixed
        by orientation, so a long view filled a third of the frame."""
        data = _input(shape, page_size=size, notes="Viewpoints along the rim.")
        plan = plan_template(template_id, data)
        assert _view_share(plan, data) >= 0.55

    def test_town_hall_gives_a_sixteen_by_nine_view_the_page_width(self) -> None:
        """Its column was chosen for any view up to 1.8:1, so a 16:9 canvas
        filled half its frame."""
        data = _input("square", extent_width=1600.0, extent_height=900.0)
        plan = plan_template("town-hall", data)
        assert _view_share(plan, data) >= 0.7

    def test_a_square_view_keeps_the_column_on_a_landscape_sheet(self) -> None:
        data = _input("square", orientation="landscape")
        plan = plan_template("field-map", data)
        frame = plan.frames[0].box
        assert frame.w < plan.page_width * 0.7
        assert frame.h > plan.page_height * 0.7


class TestDesigns:
    def test_ten_designs_with_stable_ids(self) -> None:
        assert template_ids()[:10] == (
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

    @pytest.mark.parametrize("shape", list(SHAPES))
    def test_daylight_shows_exactly_the_canvas_view(self, shape: str) -> None:
        """The one-click default: the frame keeps the canvas's shape, so the
        page shows the view as it is, nothing padded on."""
        width, height = SHAPES[shape]
        for size in ("A4", "A3", "A5"):
            frame = plan_template("classic", _input(shape, page_size=size)).frames[0]
            assert frame.box.w / frame.box.h == pytest.approx(width / height)
            assert frame.focus is None

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
        # No frame line round the map: its edge is the page's quiet margin.
        assert plan.frames[0].frame_stroke == "#00000000"

    @pytest.mark.parametrize("template_id", ["classic", "map-only", "dark"])
    def test_a_north_arrow_only_when_the_map_is_turned(self, template_id: str) -> None:
        def norths(rotation: float) -> int:
            plan = plan_template(template_id, _input(rotation=rotation))
            return sum(isinstance(i, NorthSpec) for i in plan.items)

        assert norths(0.0) == 0
        assert norths(360.0) == 0
        assert norths(25.0) == 1

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

    @pytest.mark.parametrize("template_id", ["poster", "story-map", "dashboard"])
    def test_the_text_panel_shows_the_notes_or_is_left_out(
        self, template_id: str
    ) -> None:
        with_notes = plan_template(template_id, _input(notes="Flood risk, 2026."))
        notes = next(i for i in with_notes.items if i.id == "notes")
        assert isinstance(notes, TextSpec) and notes.body == "{{notes}}"
        spec = template_spec(with_notes, _frames(with_notes), cartograph_id="c")
        assert spec.notes == "Flood risk, 2026."
        empty = plan_template(
            template_id, _input(notes="  ", placeholders={"notes": "Écrivez ici."})
        )
        assert not any(i.id == "notes" for i in empty.items)

    @pytest.mark.parametrize("template_id", ["poster", "dashboard"])
    def test_without_notes_the_map_takes_the_panels_room(
        self, template_id: str
    ) -> None:
        kept = plan_template(template_id, _input(notes="Flood risk, 2026."))
        reflowed = plan_template(template_id, _input(layer_names=()))
        assert _area(reflowed.frames[0].box) > _area(kept.frames[0].box)

    def test_the_field_map_names_only_a_real_author(self) -> None:
        def block(plan: TemplatePlan) -> str:
            return next(i.body for i in plan.items if i.id == "block-2")

        assert block(plan_template("field-map", _input())) == "{{date}}"
        named = plan_template("field-map", _input(author="R. Tan"))
        assert block(named) == "R. Tan · {{date}}"

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


#: Words a page may carry that are the template's own, not the user's: a
#: label in front of a live value. Anything else in a text item must come
#: from the project (a `{{token}}`) or not be there at all.
_TEMPLATE_LABELS = {"Scale 1:"}
#: The stand-in words earlier versions drew where content was missing.
_OLD_PLACEHOLDERS = (
    "Describe the map here",
    "Key figure",
    "What the number means",
    "Author",
)


class TestNoPlaceholderReachesThePage:
    """With no title, notes, credits or author, every template still plans
    a page with nothing on it that pretends to be content."""

    @pytest.mark.parametrize(
        ("template_id", "size", "orientation"),
        list(
            itertools.product(
                template_ids(), PAGE_SIZE_ORDER, ("portrait", "landscape")
            )
        ),
    )
    def test_only_project_tokens_and_template_labels(
        self, template_id: str, size: str, orientation: str
    ) -> None:
        stand_ins = {"notes": "Écrivez ici.", "figure": "42", "author": "Moi"}
        plan = plan_template(
            template_id,
            _input(
                title="",
                notes="",
                credits=(),
                page_size=size,
                orientation=orientation,
                placeholders=stand_ins,
            ),
        )
        for item in plan.items:
            if not isinstance(item, TextSpec):
                continue
            if item.body == ATLAS_SHEET_EXPRESSION:
                # Atlas Ledger's sheet number: a QGIS expression of the
                # atlas's own counts, blank on a page that is not one.
                continue
            words = re.sub(r"\{\{[^{}]*\}\}", "", item.body).strip(" ·")
            assert words in _TEMPLATE_LABELS or not words, (item.id, item.body)
            for stand_in in (*_OLD_PLACEHOLDERS, *stand_ins.values()):
                assert stand_in not in item.body, (item.id, item.body)
            # A {{notes}} or {{attribution}} item with nothing behind it would
            # be an empty panel.
            assert "{{notes}}" not in item.body
            assert "{{attribution}}" not in item.body

    @pytest.mark.parametrize("template_id", template_ids())
    def test_no_figure_tiles_without_figures(self, template_id: str) -> None:
        plan = plan_template(template_id, _input(notes="Flood risk, 2026."))
        assert not [
            i.id
            for i in plan.items
            if i.id.startswith("tile-") and i.id != "tile-notes"
        ]


# --------------------------------------------------------------------------
# The ten refined designs, against every page and the content that strains
# them: nothing off the page, no text on text or on the legend, no type
# below the minimum, and every text box tall enough for what it holds.
# --------------------------------------------------------------------------

#: The ten designs this file has always covered (the newer five have their
#: own tests in `test_template_designs.py`).
TEN = (
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
#: Designs that lay furniture over their map on purpose, each piece on an
#: opaque panel.
_OVER_THE_MAP = {"story-map"}

_LONG_TITLE = (
    "Flood-prone parcels along the Kranji reservoir catchment, surveyed "
    "September 2026 for the drainage review"
)
_NOTES = (
    "Parcels surveyed in September 2026, coloured by their recorded use. "
    "Wetland edges were walked on foot; the rest come from the 2025 cadastre."
)
_LONG_NOTES = (
    "Parcels surveyed in September 2026, coloured by their recorded use. "
    "Wetland edges were walked on foot over three days after the monsoon "
    "peak, so they show the wettest the ground gets in an ordinary year. "
    "Every other boundary comes from the 2025 cadastre and was checked "
    "against the March 2026 aerial survey; 14 parcels whose use had changed "
    "were re-recorded on site. Industrial parcels north of the reservoir are "
    "under review and may be reclassified before the drainage plan is "
    "adopted. Questions about the survey go to the field team lead."
)
_CLASSES = ("Housing", "Farm", "Park", "Industry", "Wetland", "Retail", "School")
_FIGURES = (
    ("214", "parcels surveyed in September"),
    ("38 ha", "of wetland edge walked"),
    ("14", "parcels re-recorded on site"),
)
#: The content variants: overrides of `_strained`'s defaults.
VARIANTS: dict[str, dict[str, object]] = {
    "default": {},
    "no title": {"title": ""},
    "long title": {"title": _LONG_TITLE},
    "long layer names": {
        "layer_names": (
            "Recorded land use of each parcel (Sept 2026 field survey, reconciled)",
            "Public and private access roads within the catchment boundary",
        ),
        "legend_labels": (_CLASSES[:5], ()),
    },
    "many legend items": {
        "layer_names": ("Land use", "Survey markers", "Roads", "Zoning overlay"),
        "legend_labels": (
            _CLASSES,
            ("Benchmark", "Boundary stone", "Well", "Gauge", "Tree", "Drain inlet"),
            (),
            _CLASSES[:6],
        ),
    },
    "no notes": {"notes": ""},
    "long notes": {"notes": _LONG_NOTES},
    "no credits": {"credits": ()},
    "key figures": {"figures": _FIGURES},
    "turned map": {"rotation": 25.0},
}


def _strained(shape: str, size: str, orientation: str, **overrides) -> TemplateInput:
    width, height = SHAPES[shape]
    fields: dict[str, object] = {
        "extent_width": width,
        "extent_height": height,
        "crs_is_geographic": False,
        "crs_authid": "EPSG:32648",
        "page_size": size,
        "orientation": orientation,
        "title": "Kranji parcels: land use survey, 2026",
        "layer_names": ("Survey markers", "Roads", "Land use"),
        "legend_labels": ((), (), _CLASSES[:5]),
        "credits": ("© OpenStreetMap contributors", "Survey data © NIKA 2026"),
        "notes": _NOTES,
        "author": "R. Tan",
        "extent_center": (366000.0, 146000.0),
    }
    fields.update(overrides)
    return TemplateInput(**fields)  # type: ignore[arg-type]


def _text_of(item: TextSpec, data: TemplateInput, credits: str) -> str | None:
    """What a text item will show, for the ones whose words are known."""
    known = {
        "{{title}}": data.title.strip(),
        "{{notes}}": data.notes.strip(),
        "{{attribution}}": credits,
    }
    if item.body in known:
        return known[item.body]
    return None if "{{" in item.body else item.body


def _minimum_for(item: TextSpec) -> float:
    if item.kind == "attribution":
        return MINIMUM_TYPE.credit
    if item.kind == "title":
        return MINIMUM_TYPE.subtitle
    return MINIMUM_TYPE.caption


def _readable(plan: TemplatePlan) -> list[tuple[str, Box, object]]:
    """The pieces a reader reads, which must never sit on one another."""
    pieces: list[tuple[str, Box, object]] = [
        (item.id, item.box, item)
        for item in plan.items
        if isinstance(item, (TextSpec, LegendSpec, ScalebarSpec, NorthSpec))
    ]
    pieces += [
        (slot.id, slot.box, slot) for slot in plan.frames if slot.id != MAIN_FRAME_ID
    ]
    return pieces


def _problems(
    template_id: str, size: str, orientation: str, shape: str, variant: str
) -> list[tuple[object, ...]]:
    """Everything wrong with one planned page, empty when it holds up."""
    data = _strained(shape, size, orientation, **VARIANTS[variant])
    plan = plan_template(template_id, data)
    problems: list[tuple[object, ...]] = []
    # On the page.
    for item_id, box in _boxes(plan):
        if (
            box.x < -0.01
            or box.y < -0.01
            or box.x + box.w > plan.page_width + 0.01
            or box.y + box.h > plan.page_height + 0.01
        ):
            problems.append(("off the page", item_id))
    main = plan.frames[0]
    pieces = _readable(plan)
    # Nothing a reader reads on top of anything else...
    for (a_id, a, _), (b_id, b, _) in itertools.combinations(pieces, 2):
        if _overlap(a, b):
            problems.append(("overlap", a_id, b_id))
    for item_id, box, item in pieces:
        if not _overlap(box, main.box):
            continue
        # ...and on the map only where the design lays it there, on an
        # opaque panel that reads whatever the map shows.
        if template_id not in _OVER_THE_MAP:
            problems.append(("on the map", item_id))
        elif isinstance(item, (TextSpec, LegendSpec)):
            colour = parse_color(item.bg or "")
            if colour is None or colour[3] < 1.0:
                problems.append(("see-through over the map", item_id))
        elif not isinstance(item, FrameSlot) and not any(
            other.id.startswith("panel-") and _contains(other.box, box)
            for other in plan.items
        ):
            problems.append(("no panel under it", item_id))
    # Type never below its minimum; every text fits its box.
    for item in plan.items:
        if (
            isinstance(item, LegendSpec)
            and (item.font_size or MINIMUM_TYPE.legend) < MINIMUM_TYPE.legend
        ):
            problems.append(("legend type too small", item.id))
        if not isinstance(item, TextSpec):
            continue
        if item.font_size is None or item.font_size < _minimum_for(item):
            problems.append(("type too small", item.id, item.font_size))
            continue
        text = _text_of(item, data, plan.attribution_text)
        if text is None:
            continue
        bold = item.weight == "bold"
        spacing = TITLE_LINE_HEIGHT if bold else TEXT_LINE_HEIGHT
        needed = text_height(
            text, item.box.w, item.font_size, bold=bold, line_height=spacing
        )
        if item.id == "notes" and variant == "long notes":
            # The longest notes may run past the room a design can give
            # them -- at the minimum size, never smaller.
            if item.font_size < MINIMUM_TYPE.body:
                problems.append(("notes too small", item.font_size))
            continue
        if needed > item.box.h + 0.05:
            problems.append(("text clipped", item.id, round(needed - item.box.h, 2)))
    return problems


class TestTheTenDesignsHoldUp:
    @pytest.mark.parametrize(
        ("template_id", "size", "orientation", "shape"),
        list(
            itertools.product(TEN, PAGE_SIZE_ORDER, ("portrait", "landscape"), SHAPES)
        ),
    )
    def test_every_variant_fits_and_reads(
        self, template_id: str, size: str, orientation: str, shape: str
    ) -> None:
        problems = {
            variant: found
            for variant in VARIANTS
            if (found := _problems(template_id, size, orientation, shape, variant))
        }
        assert not problems

    @pytest.mark.parametrize("template_id", TEN)
    def test_a_light_design_chooses_its_page_and_frame_colours(
        self, template_id: str
    ) -> None:
        """An unchosen white page or map becomes the HTML theme's paper; a
        design's own colours survive the trip."""
        plan = plan_template(template_id, _input())
        assert plan.bg
        for slot in plan.frames:
            assert slot.frame_bg and slot.frame_stroke, slot.id

    @pytest.mark.parametrize("template_id", sorted(_OVER_THE_MAP))
    def test_the_view_sits_clear_of_the_panels(self, template_id: str) -> None:
        for shape, orientation in itertools.product(SHAPES, ("portrait", "landscape")):
            plan = plan_template(
                template_id, _strained(shape, "A4", orientation, rotation=10.0)
            )
            main = plan.frames[0]
            assert main.focus is not None, (shape, orientation)
            assert _contains(main.box, main.focus)
            for item in plan.items:
                if item.id.startswith("panel-") or isinstance(item, LegendSpec):
                    assert not _overlap(item.box, main.focus), (shape, item.id)
            for slot in plan.frames[1:]:
                assert not _overlap(slot.box, main.focus), (shape, slot.id)

    def test_signal_shows_only_real_figures_once_each(self) -> None:
        def figures(plan: TemplatePlan) -> list[str]:
            return [
                i.body
                for i in plan.items
                if isinstance(i, TextSpec)
                and i.id.startswith("figure-")
                and not i.id.endswith("-label")
            ]

        plan = plan_template("dashboard", _input(figures=_FIGURES))
        assert figures(plan) == ["214", "38 ha", "14"]
        repeated = plan_template(
            "dashboard",
            _input(figures=(_FIGURES[0], _FIGURES[0], ("  ", "blank"), _FIGURES[1])),
        )
        assert figures(repeated) == ["214", "38 ha"]
        four = plan_template("dashboard", _input(figures=(*_FIGURES, ("9", "more"))))
        assert len(figures(four)) == 3
        assert figures(plan_template("dashboard", _input())) == []

    @pytest.mark.parametrize("orientation", ["portrait", "landscape"])
    def test_without_figures_signal_gives_the_room_to_the_map(
        self, orientation: str
    ) -> None:
        with_figures = plan_template(
            "dashboard", _input(figures=_FIGURES, orientation=orientation)
        )
        without = plan_template("dashboard", _input(orientation=orientation))
        if orientation == "portrait":
            assert _area(without.frames[0].box) > _area(with_figures.frames[0].box)
        else:
            # The column keeps its width for the legend; the map its size.
            assert _area(without.frames[0].box) >= _area(with_figures.frames[0].box)

    @pytest.mark.parametrize("size", PAGE_SIZE_ORDER)
    def test_a_long_headline_steps_down_and_never_clips(self, size: str) -> None:
        short = plan_template("poster", _input(page_size=size, title="Kranji"))
        long = plan_template("poster", _input(page_size=size, title=_LONG_TITLE * 2))
        (short_title,) = [i for i in short.items if i.id == "title"]
        (long_title,) = [i for i in long.items if i.id == "title"]
        assert isinstance(short_title, TextSpec) and isinstance(long_title, TextSpec)
        assert long_title.font_size < short_title.font_size
        assert long_title.box.h > short_title.box.h

    def test_fieldbook_grid_labels_never_meet(self) -> None:
        for size, orientation, shape in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape"), SHAPES
        ):
            data = _strained(shape, size, orientation)
            plan = plan_template("field-map", data)
            (grid,) = [i for i in plan.items if isinstance(i, GraticuleSpec)]
            frame = plan.frames[0].box
            per_mm = max(data.extent_width / frame.w, data.extent_height / frame.h)
            # A 6-digit northing in QGIS's 10 pt grid labels is ~14 mm.
            assert grid.interval / per_mm >= 14.0, (size, orientation, shape)


#: Projects with little in them: what a page must still be planned from.
_SPARSE = {
    "no layers": {"title": "Survey", "notes": "Housing spread north.", "credits": ()},
    "notes only": {"title": "", "notes": "Housing spread north.", "credits": ()},
    "title only": {"title": "Survey", "notes": "", "credits": ()},
    "nothing": {"title": "", "notes": "", "credits": ()},
}


@pytest.mark.parametrize("template_id", template_ids())
@pytest.mark.parametrize("content", list(_SPARSE))
def test_a_sparse_project_still_gets_a_page(template_id: str, content: str) -> None:
    """No layers, notes only, a title only, nothing at all: every design
    plans every page size and orientation without raising (Broadside once
    gave notes without layers a legend column of negative width)."""
    for size, orientation, extent in itertools.product(
        PAGE_SIZE_ORDER,
        ("portrait", "landscape", "auto"),
        ((4000.0, 1000.0), (2000.0, 2000.0), (1000.0, 3600.0)),
    ):
        plan = plan_template(
            template_id,
            TemplateInput(
                extent_width=extent[0],
                extent_height=extent[1],
                crs_is_geographic=False,
                page_size=size,
                orientation=orientation,
                **_SPARSE[content],  # type: ignore[arg-type]
            ),
        )
        assert not any(isinstance(item, LegendSpec) for item in plan.items)


@pytest.mark.parametrize(
    ("extent", "size", "orientation"),
    [((1.0, 50.0), "A0", "auto"), ((1.0, 1.0), "A5", "landscape")],
)
def test_daylight_with_an_endless_title_stays_on_the_page(
    extent: tuple[float, float], size: str, orientation: str
) -> None:
    """Daylight's heading once grew with every pass of its sizing loop: a
    200-character title raised `Box.w must be positive` on a very tall view
    and ran to y=518 mm on an A5 sheet."""
    plan = plan_template(
        "classic",
        TemplateInput(
            extent_width=extent[0],
            extent_height=extent[1],
            crs_is_geographic=False,
            page_size=size,
            orientation=orientation,
            title="T" * 200,
        ),
    )
    page = Box(0.0, 0.0, plan.page_width, plan.page_height)
    for name, box in _boxes(plan):
        assert box.w > 0 and box.h > 0, name
        assert _contains(page, box), name
    frame = plan.frames[0].box
    assert frame.h >= 0.4 * plan.page_height


class TestTextSizeSteps:
    """The title, legend and notes sizes the user picks, 80 to 130 percent."""

    _SMALLER, _LARGER = 0.8, 1.3
    _ELEMENTS = ("title_scale", "legend_scale", "notes_scale")

    @staticmethod
    def _main_area(plan: TemplatePlan) -> float:
        return _area(plan.frames[0].box)

    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize(
        ("size", "orientation", "shape"),
        [
            ("A4", "portrait", "square"),
            ("A4", "landscape", "wide"),
            ("A3", "auto", "tall"),
            ("A5", "landscape", "square"),
        ],
    )
    @pytest.mark.parametrize("element", (*_ELEMENTS, "all"))
    def test_smaller_gives_the_map_room_and_larger_takes_it(
        self, template_id: str, size: str, orientation: str, shape: str, element: str
    ) -> None:
        names = self._ELEMENTS if element == "all" else (element,)

        def plan_at_standard() -> TemplatePlan:
            return plan_template(
                template_id,
                _strained(shape, size, orientation, figures=_FIGURES),
            )

        def plan_at(factor: float) -> TemplatePlan:
            data = _strained(
                shape,
                size,
                orientation,
                figures=_FIGURES,
                **{name: factor for name in names},
            )
            plan = plan_template(template_id, data)
            for item_id, box in _boxes(plan):
                assert box.w > 0 and box.h > 0, (factor, item_id)
                assert box.x >= -0.01 and box.y >= -0.01, (factor, item_id)
                assert box.x + box.w <= plan.page_width + 0.01, (factor, item_id)
                assert box.y + box.h <= plan.page_height + 0.01, (factor, item_id)
            for item in plan.items:
                if isinstance(item, LegendSpec):
                    assert (item.font_size or 0) >= plan.legend_min_pt, (
                        factor,
                        item.id,
                    )
                elif isinstance(item, TextSpec) and item.font_size:
                    # A piece a design sets below the general minimum at
                    # Standard (a date stamp) is never set lower than there.
                    floor = min(_minimum_for(item), standard_sizes.get(item.id, 99.0))
                    assert item.font_size >= floor, (factor, item.id)
            return plan

        standard_sizes = {
            item.id: item.font_size
            for item in plan_at_standard().items
            if isinstance(item, TextSpec) and item.font_size
        }

        def shown_area(plan):
            # Reflow can change the frame's aspect. Compare the size of the
            # actual map content, not empty padding around a wide/tall view.
            data = _strained(shape, size, orientation, figures=_FIGURES)
            frame = plan.frames[0].focus or plan.frames[0].box
            factor = min(frame.w / data.extent_width, frame.h / data.extent_height)
            return data.extent_width * data.extent_height * factor**2

        smaller, standard, larger = (
            shown_area(plan_at(f)) for f in (self._SMALLER, 1.0, self._LARGER)
        )
        assert smaller >= standard - 0.01
        if element not in {"legend_scale", "all"}:
            # A larger legend may reflow wider and shorter, which can give
            # the map height back; its smaller step still never costs room.
            assert larger <= standard + 0.01

    @pytest.mark.parametrize(
        ("stored", "percent"),
        [
            ("smaller", 80),
            ("small", 90),
            ("standard", 100),
            ("large", 115),
            ("larger", 130),
            (" Large ", 115),
            ("107", 107),
            ("107%", 107),
            (107, 107),
            (106.6, 107),
            ("79", 80),
            (500, 130),
            (-3, 80),
            ("garbage", 100),
            ("", 100),
            (None, 100),
            (True, 100),
            (float("nan"), 100),
            ("inf", 100),
        ],
    )
    def test_a_stored_size_reads_as_a_clamped_percent(
        self, stored: object, percent: int
    ) -> None:
        assert text_percent(stored) == percent
        assert TextSizes(stored, stored, stored) == TextSizes(  # type: ignore[arg-type]
            percent, percent, percent
        )

    def test_the_percent_is_the_scale_the_page_is_planned_with(self) -> None:
        data = TextSizes(80, 107, "larger").apply(  # type: ignore[arg-type]
            _strained("square", "A4", "portrait")
        )
        assert (data.title_scale, data.legend_scale, data.notes_scale) == (
            0.8,
            1.07,
            1.3,
        )

    @pytest.mark.parametrize("template_id", template_ids())
    def test_an_intermediate_percent_sits_between_its_neighbours(
        self, template_id: str
    ) -> None:
        def main_area(percent: int) -> float:
            data = TextSizes(percent, percent, percent).apply(
                _strained("square", "A3", "portrait", figures=_FIGURES)
            )
            return self._main_area(plan_template(template_id, data))

        low, mid, high = main_area(100), main_area(107), main_area(115)
        assert high - 0.01 <= mid <= low + 0.01

    def test_standard_is_the_default_page(self) -> None:
        for template_id in template_ids():
            plain = _strained("square", "A4", "portrait", figures=_FIGURES)
            explicit = _strained(
                "square",
                "A4",
                "portrait",
                figures=_FIGURES,
                title_scale=1.0,
                legend_scale=1.0,
                notes_scale=1.0,
            )
            assert plan_template(template_id, plain) == plan_template(
                template_id, explicit
            )

    @pytest.mark.parametrize("template_id", template_ids())
    def test_each_element_changes_only_its_own_text(self, template_id: str) -> None:
        def sizes(**scales: float) -> dict[str, float]:
            plan = plan_template(
                template_id,
                _strained("square", "A3", "portrait", figures=_FIGURES, **scales),
            )
            return {
                item.id: item.font_size
                for item in plan.items
                if isinstance(item, (TextSpec, LegendSpec)) and item.font_size
            }

        base = sizes()
        changed = {
            name: {k for k, v in sizes(**{name: 1.3}).items() if base.get(k) != v}
            for name in self._ELEMENTS
        }
        # Only a legend changes with the legend step.
        assert all(k.startswith("legend") for k in changed["legend_scale"])
        assert not any(k.startswith("legend") for k in changed["title_scale"])
        assert not any(k.startswith("legend") for k in changed["notes_scale"])
        # ...and each step really is used by the designs that show the piece.
        shown = plan_template(
            template_id, _strained("square", "A3", "portrait", figures=_FIGURES)
        ).items
        if any(isinstance(item, LegendSpec) for item in shown):
            assert changed["legend_scale"]
        if any(
            isinstance(item, TextSpec) and item.body == "{{title}}" for item in shown
        ):
            assert changed["title_scale"]
        if any(
            isinstance(item, TextSpec) and item.body == "{{notes}}" for item in shown
        ):
            assert changed["notes_scale"]
