"""The colour-vision check: simulation, CIEDE2000 and the legend lint.

The check is a port of the OnlyMap cartograph runtime's `lintLegendColours`,
and its whole value is agreeing with it - so the reference numbers here were
produced by the runtime's own code (node, runtime 0.12.0 `cvd.ts`), not by
this module, and `tests/fixtures/cvd-palette.json` is the fixture both repos
pin. CIEDE2000 is additionally pinned to the published Sharma et al. table,
which is what proves the formula rather than just its agreement with a twin.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nika_onlymap_exporter.core import cvd
from nika_onlymap_exporter.core.cvd import (
    CONDITIONS,
    MATRICES,
    LegendEntry,
    ciede2000,
    delta_e00,
    hex_colour,
    legend_entries,
    legend_entries_for_projects,
    lint_legend_colours,
    parse_colour,
    simulate,
)
from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    ExportProject,
    GeometryKind,
    GraduatedClassSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cvd-palette.json"

RED = (214, 39, 40)  # matplotlib's tab:red
GREEN = (44, 160, 44)  # tab:green
BLUE = (31, 119, 180)  # tab:blue


# Sharma, Wu & Dalal (2005), "The CIEDE2000 color-difference formula:
# implementation notes, supplementary test data, and mathematical
# observations", Table 1: (L1, a1, b1), (L2, a2, b2), dE00. The pairs were
# chosen to hit every branch an implementation gets wrong - hue wrap-around at
# 0/360, the mean-hue correction, achromatic inputs, and the G factor.
SHARMA_2005 = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0010), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0012), 7.2195),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0009, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0010, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0011, -2.4900), 4.7461),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 1.8634, 0.5757), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


class TestCiede2000:
    @pytest.mark.parametrize(("lab1", "lab2", "expected"), SHARMA_2005)
    def test_matches_the_sharma_2005_table(self, lab1, lab2, expected):
        assert ciede2000(lab1, lab2) == pytest.approx(expected, abs=5e-5)

    @pytest.mark.parametrize(("lab1", "lab2", "expected"), SHARMA_2005)
    def test_is_symmetric(self, lab1, lab2, expected):
        assert ciede2000(lab1, lab2) == pytest.approx(ciede2000(lab2, lab1), abs=1e-9)

    def test_rgb_distance_matches_the_runtime(self):
        # Numbers from the runtime's `deltaE00`, so the sRGB -> Lab half
        # (linearisation, D65 matrix, white point) is pinned too.
        assert delta_e00(RED, GREEN) == pytest.approx(71.828253645, abs=1e-6)
        assert delta_e00((0, 0, 0), (255, 255, 255)) == pytest.approx(
            100.000003867, abs=1e-6
        )
        assert delta_e00(RED, RED) == pytest.approx(0.0, abs=1e-12)


class TestMatrices:
    def test_are_the_runtime_matrices(self):
        assert MATRICES == {
            "protanopia": (
                0.11238,
                0.88762,
                0.0,
                0.11238,
                0.88762,
                0.0,
                0.00401,
                -0.00401,
                1.0,
            ),
            "deuteranopia": (
                0.29275,
                0.70725,
                0.0,
                0.29275,
                0.70725,
                0.0,
                -0.02234,
                0.02234,
                1.0,
            ),
            "tritanopia": (
                1.0,
                0.14461,
                -0.14461,
                0.0,
                0.85659,
                0.14341,
                0.0,
                0.85659,
                0.14341,
            ),
            "achromatopsia": (
                0.2126,
                0.7152,
                0.0722,
                0.2126,
                0.7152,
                0.0722,
                0.2126,
                0.7152,
                0.0722,
            ),
        }
        assert CONDITIONS == (
            "protanopia",
            "deuteranopia",
            "tritanopia",
            "achromatopsia",
        )

    def test_are_singular_along_the_confusion_axis(self):
        # The property the module docstring calls load-bearing: an invertible
        # matrix can never make two colours one.
        for condition in ("protanopia", "deuteranopia"):
            m = MATRICES[condition]
            assert m[0:3] == m[3:6]
        tritan = MATRICES["tritanopia"]
        assert tritan[3:6] == tritan[6:9]

    def test_rows_preserve_white(self):
        for condition in CONDITIONS:
            m = MATRICES[condition]
            for row in range(3):
                assert sum(m[row * 3 : row * 3 + 3]) == pytest.approx(1.0, abs=1e-9)


# From the runtime's `simulate`, to nine decimals.
SIMULATE_RUNTIME = {
    (RED, "protanopia"): (86.231941679, 86.231941679, 42.680585294),
    (RED, "deuteranopia"): (126.716102192, 126.716102192, 19.289372573),
    (RED, "tritanopia"): (213.980987855, 39.145030174, 39.145030174),
    (RED, "achromatopsia"): (111.014081745, 111.014081745, 111.014081745),
    (GREEN, "protanopia"): (152.191334647, 152.191334647, 42.724242317),
    (GREEN, "deuteranopia"): (138.458321225, 138.458321225, 50.483700507),
    (GREEN, "tritanopia"): (76.055960283, 149.942629241, 149.942629241),
    (BLUE, "tritanopia"): (0.0, 130.065945239, 130.065945239),
    (BLUE, "achromatopsia"): (113.851851803, 113.851851803, 113.851851803),
}


class TestSimulate:
    @pytest.mark.parametrize(("key", "expected"), list(SIMULATE_RUNTIME.items()))
    def test_matches_the_runtime(self, key, expected):
        rgb, condition = key
        assert simulate(rgb, condition) == pytest.approx(expected, abs=1e-6)

    def test_applies_the_matrix_in_linear_light(self):
        # Applied to gamma-encoded channels instead, deuteranopia would give
        # 0.29275*214 + 0.70725*39 = 90.3 for red's first channel.
        assert simulate(RED, "deuteranopia")[0] == pytest.approx(126.716, abs=1e-3)

    def test_collapses_red_and_green_under_deuteranopia(self):
        assert delta_e00(RED, GREEN) > 60
        assert (
            delta_e00(simulate(RED, "deuteranopia"), simulate(GREEN, "deuteranopia"))
            < 10
        )

    def test_keeps_channels_in_range(self):
        for condition in CONDITIONS:
            for colour in ((255, 255, 255), (0, 0, 0), (0, 0, 255), (255, 0, 0)):
                assert all(0 <= c <= 255 for c in simulate(colour, condition))


class TestParseColour:
    def test_reads_what_the_runtime_reads(self):
        assert parse_colour("#f00") == (255, 0, 0)
        assert parse_colour(" #00FF00 ") == (0, 255, 0)
        assert parse_colour("rgb(0, 0, 255)") == (0, 0, 255)
        assert parse_colour("rgba(10,20,30,0.5)") == (10, 20, 30)

    def test_refuses_what_the_runtime_refuses(self):
        # `#rrggbbaa` included: the runtime cannot read it, so neither may we,
        # or the two would compare different sets of rows.
        for text in ("#d6272880", "rebeccapurple", "linear-gradient(red, blue)", ""):
            assert parse_colour(text) is None

    def test_hex_colour_rounds_for_display(self):
        assert hex_colour((126.716, 126.716, 19.289)) == "#7f7f13"
        assert hex_colour((-1, 300, 0.4)) == "#00ff00"


def entry(label: str, colour: str, layer: str | None = "l") -> LegendEntry:
    return LegendEntry(label=label, color=colour, layer=layer)


class TestLint:
    def test_fixture_reports_exactly_the_runtime_pairs(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        entries = [
            LegendEntry(label=e["label"], color=e["color"], layer=e.get("layer"))
            for e in data["entries"]
        ]
        lint = lint_legend_colours(entries, threshold=data["threshold"])
        actual = [
            {
                "condition": c.condition,
                "layer": c.a.layer,
                "a": c.a.label,
                "b": c.b.label,
                "distance": round(c.distance, 6),
            }
            for c in lint.collapses
        ]
        assert actual == data["expected"]["collapses"]
        assert lint.worst_condition == data["expected"]["worstCondition"]

    def test_fixture_contains_the_red_green_collapse(self):
        # Guards the fixture itself: the issue promises a palette with a known
        # red/green collapse, and the test above would pass on an empty one.
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert {
            "condition": "deuteranopia",
            "layer": "parcels",
            "a": "Residential",
            "b": "Agricultural",
        }.items() <= data["expected"]["collapses"][0].items()

    def test_compares_only_within_a_layer(self):
        lint = lint_legend_colours(
            [
                entry("Approved", "#2ca02c", "permits"),
                entry("Rejected", "#d62728", "zones"),
            ]
        )
        assert lint.collapses == ()
        assert lint.worst_condition is None

    def test_rows_without_a_layer_form_one_group(self):
        lint = lint_legend_colours(
            [entry("Approved", "#2ca02c", None), entry("Rejected", "#d62728", None)]
        )
        assert {c.condition for c in lint.collapses} == {"deuteranopia"}

    def test_skips_pairs_already_too_close_for_normal_vision(self):
        lint = lint_legend_colours([entry("A", "#d62728"), entry("B", "#d72829")])
        assert lint.collapses == ()

    def test_compares_every_pair_not_only_neighbours(self):
        # Red and green with a class between them: the runtime does not limit
        # ramps to adjacent classes, so neither does this.
        lint = lint_legend_colours(
            [entry("Low", "#d62728"), entry("Mid", "#1b2d0a"), entry("High", "#2ca02c")]
        )
        pairs = {(c.a.label, c.b.label) for c in lint.under("deuteranopia")}
        assert ("Low", "High") in pairs

    def test_threshold_is_strict(self):
        red, green = entry("Red", "#d62728"), entry("Green", "#2ca02c")
        distance = delta_e00(
            simulate(RED, "deuteranopia"), simulate(GREEN, "deuteranopia")
        )
        # At exactly the distance the pair is NOT below it ...
        assert (
            lint_legend_colours([red, green], threshold=distance).under("deuteranopia")
            == ()
        )
        # ... and a hair above, it is.
        collapses = lint_legend_colours([red, green], threshold=distance + 1e-9).under(
            "deuteranopia"
        )
        assert [c.distance for c in collapses] == [pytest.approx(distance)]

    def test_worst_condition_counts_collapses_and_ties_go_first(self):
        lint = lint_legend_colours([entry("Red", "#d62728"), entry("Green", "#2ca02c")])
        assert [c.condition for c in lint.collapses] == ["deuteranopia"]
        assert lint.worst_condition == "deuteranopia"
        # One collapse each under deuteranopia and achromatopsia (in separate
        # layers): the tie goes to the earlier condition, as in the runtime.
        tied = lint_legend_colours(
            [
                entry("Red", "#d62728", "a"),
                entry("Green", "#2ca02c", "a"),
                entry("Red", "#d62728", "b"),
                entry("Blue", "#1f77b4", "b"),
            ]
        )
        counts = {c: len(tied.under(c)) for c in CONDITIONS}
        assert counts["deuteranopia"] == counts["achromatopsia"] == 1
        assert tied.worst_condition == "deuteranopia"

    def test_pairs_name_each_pairs_closest_condition(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        entries = [
            LegendEntry(label=e["label"], color=e["color"], layer=e.get("layer"))
            for e in data["entries"]
        ]
        pairs = {
            (p.a.label, p.b.label): p for p in lint_legend_colours(entries).pairs()
        }
        both = pairs[("Agricultural", "Commercial")]
        assert both.conditions == ("tritanopia", "achromatopsia")
        assert both.condition == "tritanopia"
        assert both.distance == pytest.approx(8.876809, abs=1e-6)
        assert len(pairs) == 5

    def test_skips_unparseable_colours(self):
        lint = lint_legend_colours([entry("A", "#d62728"), entry("B", "nonsense")])
        assert lint.collapses == ()


def symbol(fill: Color | None = None, stroke: Color | None = None) -> SymbolSpec:
    return SymbolSpec(fill_color=fill, stroke_color=stroke)


def layer(
    layer_id: str,
    renderer: RendererSpec,
    geometry: GeometryKind = GeometryKind.POLYGON,
    **kwargs,
) -> ExportLayer:
    return ExportLayer(
        layer_id=layer_id,
        name=layer_id.title(),
        geometry_kind=geometry,
        source_kind=SourceKind.MEMORY,
        renderer=renderer,
        **kwargs,
    )


class TestLegendEntries:
    def test_categorized_layer_lists_each_visible_class(self):
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            field_name="use",
            categories=(
                CategorySpec("res", "Residential", symbol(Color(214, 39, 40))),
                CategorySpec("ag", "", symbol(Color(44, 160, 44, 0.5))),
                CategorySpec("gone", "Gone", symbol(Color(0, 0, 0))),
                CategorySpec("none", "No fill", symbol(None, Color(1, 2, 3))),
            ),
            hidden_values=("gone",),
        )
        assert legend_entries([layer("parcels", renderer)]) == (
            LegendEntry("Residential", "#d62728", "parcels", "Parcels"),
            # No label: the value. Alpha dropped.
            LegendEntry("ag", "#2ca02c", "parcels", "Parcels"),
            # A polygon class with no fill draws in the map's placeholder grey.
            LegendEntry("No fill", "#888888", "parcels", "Parcels"),
        )

    def test_line_classes_take_their_stroke(self):
        renderer = RendererSpec(
            kind=RendererKind.GRADUATED,
            field_name="lanes",
            classes=(
                GraduatedClassSpec(0, 2, "Minor", symbol(None, Color(255, 0, 0))),
                GraduatedClassSpec(2, 6.5, "", symbol(None, Color(0, 255, 0))),
            ),
        )
        entries = legend_entries([layer("roads", renderer, GeometryKind.LINE)])
        assert [(e.label, e.color) for e in entries] == [
            ("Minor", "#ff0000"),
            ("2 - 6.5", "#00ff00"),
        ]

    def test_single_symbol_is_one_row_named_after_the_layer(self):
        renderer = RendererSpec(
            kind=RendererKind.SINGLE, symbol=symbol(None, Color(0, 0, 255))
        )
        assert legend_entries([layer("rivers", renderer, GeometryKind.LINE)]) == (
            LegendEntry("Rivers", "#0000ff", "rivers", "Rivers"),
        )

    def test_rasters_unsupported_and_repeats_are_left_out(self):
        single = RendererSpec(kind=RendererKind.SINGLE, symbol=symbol(Color(1, 1, 1)))
        layers = [
            layer("dem", single, raster=RasterSpec(path="/x.tif")),
            layer("odd", RendererSpec(kind=RendererKind.UNSUPPORTED)),
            layer("a", single),
        ]
        project_one = ExportProject(title="", layers=tuple(layers))
        project_two = ExportProject(title="", layers=(layers[2],))
        entries = legend_entries_for_projects([project_one, project_two])
        assert [e.layer for e in entries] == ["a"]

    def test_entries_feed_the_lint(self):
        renderer = RendererSpec(
            kind=RendererKind.CATEGORIZED,
            categories=(
                CategorySpec("a", "Approved", symbol(Color(44, 160, 44))),
                CategorySpec("r", "Rejected", symbol(Color(214, 39, 40))),
            ),
        )
        lint = lint_legend_colours(legend_entries([layer("permits", renderer)]))
        assert [(c.a.label, c.b.label, c.condition) for c in lint.collapses] == [
            ("Approved", "Rejected", "deuteranopia")
        ]
        assert lint.collapses[0].a.display_layer == "Permits"


def test_module_imports_no_qgis():
    source = Path(cvd.__file__).read_text(encoding="utf-8")
    assert "qgis" not in source.replace("qgis2carto", "")
