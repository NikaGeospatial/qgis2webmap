"""Every legend the fifteen designs plan holds its rows at a readable size,
long rows wrapped, and every scale bar's labels are sized for the page
(`core/layout_templates.py`, `core/template_designs.py`).

The planner's legend estimate follows QGIS's own legend renderer -- its
row metrics and its rule for sharing layers between columns -- so a box
planned here is one QGIS draws without shrinking the text
(`tests/qgis/test_template_designs_build.py` checks that in QGIS).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import itertools

import pytest

from nika_onlymap_exporter.core import layout_templates as lt
from nika_onlymap_exporter.core.cartograph_ir import LegendSpec, ScalebarSpec
from nika_onlymap_exporter.core.layout_templates import (
    PAGE_SIZE_ORDER,
    TemplateInput,
    TemplatePlan,
    legend_wrap_width,
    plan_template,
    scalebar_height,
    template_ids,
)
from nika_onlymap_exporter.core.page_style import MINIMUM_TYPE, type_size

SHAPES = {"wide": (14000.0, 3500.0), "square": (5000.0, 5000.0)}
SOIL = ("Clay", "Silty clay", "Loam", "Sandy loam", "Sand", "Peat", "Fill", "Laterite")
FLOOD = ("0-1 m", "1-2 m", "2-3 m", "3-4 m", "4-5 m")
USES = ("Housing", "Farmland", "Parkland", "Industry")
LONG = (
    "Trunk and estate roads (LTA road network, July 2026)",
    "Sungei Kranji main channel and its western tributaries",
    "Agricultural and residential land use parcels, 2026 cadastral survey",
    "Planning areas and subzones of the Western Water Catchment (URA Master Plan)",
)
#: The stress legend: long names over six layers, 23 rows.
LONG_MANY = (
    (
        "Soil samples from the September 2026 auger survey, by texture class",
        LONG[0],
        LONG[1],
        "Modelled flood depth in metres for a 1-in-100-year storm event (PUB 2025)",
        LONG[2],
        LONG[3],
    ),
    (SOIL, (), (), FLOOD, USES, ()),
)
LEGENDS: dict[str, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]] = {
    "base": (("Roads", "River", "Land use", "Planning areas"), ((), (), USES, ())),
    "long-names": (LONG, ((), (), USES, ())),
    # 23 rows over six layers.
    "many-rows": (
        ("Soil samples", "Roads", "River", "Flood depth", "Land use", "Areas"),
        (SOIL, (), (), FLOOD, USES, ()),
    ),
}


def _input(
    legend: str, shape: str, size: str, orientation: str, **overrides
) -> TemplateInput:
    names, labels = LEGENDS[legend]
    width, height = SHAPES[shape]
    fields: dict[str, object] = {
        "extent_width": width,
        "extent_height": height,
        "crs_is_geographic": False,
        "crs_authid": "EPSG:32648",
        "page_size": size,
        "orientation": orientation,
        "title": "Kranji parcels: land use survey, 2026",
        "layer_names": names,
        "legend_labels": labels,
        "credits": ("Parcels © NIKA field survey 2026",),
        "notes": "Housing has spread north of the main channel since 2019.",
    }
    fields.update(overrides)
    return TemplateInput(**fields)  # type: ignore[arg-type]


def _page(plan: TemplatePlan, data: TemplateInput) -> lt._Page:
    return lt._Page(
        data, plan.page_width, plan.page_height, data.title, plan.attribution_text
    )


def _legend(plan: TemplatePlan) -> LegendSpec | None:
    return next((i for i in plan.items if isinstance(i, LegendSpec)), None)


class TestEveryLegendFitsItsBox:
    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize("legend", list(LEGENDS))
    def test_on_every_page_the_rows_fit_at_a_readable_size(
        self, template_id: str, legend: str
    ) -> None:
        for size, orientation, shape in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape"), SHAPES
        ):
            case = (size, orientation, shape)
            data = _input(legend, shape, size, orientation)
            plan = plan_template(template_id, data)
            planned = _legend(plan)
            if planned is None:
                continue
            assert planned.font_size is not None, case
            assert planned.font_size >= plan.legend_min_pt, case
            columns = planned.columns or 1
            assert planned.wrap_width == legend_wrap_width(planned.box.w, columns)
            page = _page(plan, data)
            need = min(
                lt._legend_height(
                    page, planned.font_size, planned.box.w, columns, split=split
                )
                for split in (False, True)
            )
            if need > planned.box.h + 1e-6:
                # Only on A5, and only at the design's floor: the rest are
                # left out by QGIS, never drawn smaller (`tests/qgis`).
                assert size == "A5", (case, need, planned.box)
                assert planned.font_size == pytest.approx(plan.legend_min_pt), case

    @pytest.mark.parametrize("template_id", template_ids())
    def test_a_short_legend_keeps_the_page_size(self, template_id: str) -> None:
        """Only a legend that cannot fit any other way is set smaller: an
        ordinary one is the page's legend size (or the design's own)."""
        for size, orientation in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape")
        ):
            plan = plan_template(
                template_id, _input("base", "square", size, orientation)
            )
            planned = _legend(plan)
            if planned is None:
                continue
            page_size = min(
                type_size("legend", plan.page_width, plan.page_height), 18.0
            )
            assert planned.font_size >= page_size - 1e-6, (size, orientation)

    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize("legend", ["long-names", "many-rows"])
    def test_from_a4_up_a_long_legend_reflows_rather_than_shrinks(
        self, template_id: str, legend: str
    ) -> None:
        """A design makes room -- wraps, more columns, a wider column, the
        legend under the map -- before it sets a legend smaller than the
        page's legend size; only on A5 may that run out."""
        for size, orientation, shape in itertools.product(
            [s for s in PAGE_SIZE_ORDER if s != "A5"],
            ("portrait", "landscape"),
            SHAPES,
        ):
            data = _input(legend, shape, size, orientation)
            plan = plan_template(template_id, data)
            planned = _legend(plan)
            if planned is None or template_id == "town-hall":
                continue
            page_size = min(
                type_size("legend", plan.page_width, plan.page_height), 18.0
            )
            assert planned.font_size is not None
            assert planned.font_size >= page_size - 1e-6, (size, orientation, shape)

    @pytest.mark.parametrize("template_id", template_ids())
    def test_a_legend_too_long_for_any_room_stops_at_the_minimum(
        self, template_id: str
    ) -> None:
        """Forty long rows on A5 fit no design: the planner sets the legend
        at the design's floor, never below it."""
        names = tuple(f"{LONG[i % 4]} ({i})" for i in range(40))
        data = _input(
            "base", "square", "A5", "portrait", layer_names=names, legend_labels=()
        )
        plan = plan_template(template_id, data)
        planned = _legend(plan)
        if planned is not None:
            assert planned.font_size == pytest.approx(plan.legend_min_pt)

    def test_clearline_gives_a_long_legend_the_band_s_whole_width(self) -> None:
        """Long names and 31 rows on Letter: beside the scale bar the
        legend would need more than the band may take, so it runs across
        the whole width at the page's legend size, the bar under it."""
        soil = "Soil samples from the September 2026 auger survey, by texture class"
        flood = "Modelled flood depth in metres for a 1-in-100-year storm (PUB 2025)"
        data = _input(
            "base",
            "square",
            "Letter",
            "portrait",
            layer_names=(soil, LONG[0], LONG[1], flood, LONG[2], LONG[3], "Cores"),
            legend_labels=(SOIL, (), (), FLOOD, USES, (), SOIL),
        )
        plan = plan_template("legend-below", data)
        planned = _legend(plan)
        assert planned is not None
        assert planned.box.w == pytest.approx(plan.frames[0].box.w)
        (bar,) = [i for i in plan.items if isinstance(i, ScalebarSpec)]
        assert bar.box.y > planned.box.y + planned.box.h
        assert planned.font_size == pytest.approx(
            type_size("legend", plan.page_width, plan.page_height)
        )


class TestWrapping:
    def test_a_long_name_wraps_rather_than_widening_the_column(self) -> None:
        data = _input("long-names", "square", "A5", "portrait")
        plan = plan_template("classic", data)
        planned = _legend(plan)
        assert planned is not None and planned.wrap_width is not None
        # The wrap leaves room for QGIS's symbol and spacing in the column.
        assert planned.wrap_width <= planned.box.w / (planned.columns or 1) - 9.0
        longest = max(LONG, key=len)
        assert lt._text_width(longest, planned.font_size) > planned.wrap_width

    def test_wrapped_rows_make_the_legend_taller(self) -> None:
        data = _input("long-names", "square", "A4", "portrait")
        page = lt._Page(data, 210.0, 297.0, "", "")
        narrow = lt._legend_height(page, 8.0, 50.0, 1)
        wide = lt._legend_height(page, 8.0, 200.0, 1)
        assert narrow > wide

    def test_columns_are_shared_out_as_qgis_shares_them(self) -> None:
        """A case measured in QGIS 4.2 (a soil list, two lines, a flood
        list, a land-use list, one more line, rows wrapped at 25 mm):
        QGIS puts [soil, roads], [river, flood, land use], [areas]."""
        groups = [(3.0, 67.1), (2.5, 11.7), (2.5, 11.7), (3.0, 51.5)]
        groups += [(3.0, 41.1), (2.5, 19.5)]
        assert lt._legend_columns_of(groups, 3) == [0, 0, 1, 1, 1, 2]
        assert lt._legend_columns_of(groups, 1) == [0] * 6


class TestScaleBarText:
    @pytest.mark.parametrize("template_id", template_ids())
    def test_labels_follow_the_page_and_fit_their_box(self, template_id: str) -> None:
        for size, orientation in itertools.product(
            PAGE_SIZE_ORDER, ("portrait", "landscape")
        ):
            plan = plan_template(
                template_id, _input("base", "square", size, orientation)
            )
            for bar in (i for i in plan.items if isinstance(i, ScalebarSpec)):
                assert bar.font_size is not None, (size, orientation)
                assert bar.font_size >= MINIMUM_TYPE.caption
                assert bar.box.h >= scalebar_height(bar.font_size) - 1e-6

    def test_a5_is_small_and_a1_large(self) -> None:
        def bar_pt(template_id: str, size: str) -> float:
            plan = plan_template(
                template_id, _input("base", "square", size, "portrait")
            )
            (bar,) = [i for i in plan.items if isinstance(i, ScalebarSpec)]
            assert bar.font_size is not None
            return bar.font_size

        for template_id in ("classic", "night-desk", "atlas-ledger"):
            assert bar_pt(template_id, "A5") == pytest.approx(6.5)
            assert bar_pt(template_id, "A4") == pytest.approx(8.0)
            assert bar_pt(template_id, "A1") > 12.0
        # Town Hall's large print: larger than the quiet designs everywhere,
        # and well past QGIS's 12 pt on a big sheet.
        for size in PAGE_SIZE_ORDER:
            assert bar_pt("town-hall", size) > bar_pt("night-desk", size)
        assert bar_pt("town-hall", "A5") < 12.0
        assert bar_pt("town-hall", "A2") > 16.0


class TestReadableFloor:
    def test_each_design_names_its_floor(self) -> None:
        """6.5 pt everywhere but Town Hall, whose large print never goes
        below 9 pt and grows with the sheet (15 % over the page's legend
        size)."""
        for template_id, size in itertools.product(template_ids(), PAGE_SIZE_ORDER):
            plan = plan_template(
                template_id, _input("base", "square", size, "portrait")
            )
            if template_id != "town-hall":
                assert plan.legend_min_pt == MINIMUM_TYPE.legend
                continue
            page_legend = type_size("legend", plan.page_width, plan.page_height)
            assert plan.legend_min_pt >= 9.0
            assert plan.legend_min_pt == pytest.approx(
                max(9.0, round(page_legend * 1.15, 2)), abs=0.01
            )
        a2 = plan_template("town-hall", _input("base", "square", "A2", "portrait"))
        assert a2.legend_min_pt >= 18.0

    @pytest.mark.parametrize("template_id", template_ids())
    def test_the_stress_legend_is_never_planned_below_the_floor(
        self, template_id: str
    ) -> None:
        """Long names over six layers, 23 rows, on every sheet: the legend
        is planned at the design's floor or above -- Town Hall's large print
        included -- whatever the room."""
        LEGENDS["long-many"] = LONG_MANY
        try:
            for size, orientation, shape in itertools.product(
                PAGE_SIZE_ORDER, ("portrait", "landscape"), SHAPES
            ):
                plan = plan_template(
                    template_id, _input("long-many", shape, size, orientation)
                )
                planned = _legend(plan)
                if planned is None:
                    continue
                assert planned.font_size is not None
                assert planned.font_size >= plan.legend_min_pt - 1e-6, (
                    size,
                    orientation,
                )
        finally:
            del LEGENDS["long-many"]


class TestTheMapStaysTheHero:
    @pytest.mark.parametrize("template_id", template_ids())
    def test_a_crowded_legend_never_outgrows_the_map(self, template_id: str) -> None:
        """The stress legend gets more room -- more columns, a strip -- but
        the map gives way only until the two are the same size
        (`MAP_MIN_SHARE`): a legend under or over the map is no taller than
        it, one beside it no wider, one laid over it no more than half its
        height."""
        LEGENDS["long-many"] = LONG_MANY
        try:
            for size, orientation, shape in itertools.product(
                ("A5", "A4", "Letter", "A3"), ("portrait", "landscape"), SHAPES
            ):
                plan = plan_template(
                    template_id, _input("long-many", shape, size, orientation)
                )
                planned = _legend(plan)
                if planned is None:
                    continue
                frame, legend = plan.frames[0].box, planned.box
                case = (size, orientation, shape, frame, legend)
                inside = (
                    frame.x <= legend.x + 0.01
                    and legend.x + legend.w <= frame.x + frame.w + 0.01
                    and frame.y <= legend.y + 0.01
                    and legend.y + legend.h <= frame.y + frame.h + 0.01
                )
                stacked = (
                    legend.x < frame.x + frame.w - 0.01
                    and frame.x < legend.x + legend.w - 0.01
                )
                if template_id == "classic" and stacked and not inside:
                    # Daylight keeps the canvas's shape, so a view wider than
                    # the page leaves its map lower than the room: the legend
                    # takes no more than half the room, not the frame.
                    room = plan.page_height - 2 * lt._MARGIN
                    assert legend.h <= room * lt.MAP_MIN_SHARE + 0.01, case
                elif inside:
                    assert legend.h <= frame.h * lt.MAP_MIN_SHARE + 0.01, case
                elif stacked:
                    # (Within half a millimetre: a band held to its least
                    # corridor shape can stop that short of the room.)
                    assert legend.h <= frame.h + 0.5, case
                else:
                    assert legend.w <= frame.w + 0.5, case
        finally:
            del LEGENDS["long-many"]
