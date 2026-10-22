"""`core/runtime_legend.py`: the runtime's legend labels, mirrored, and the
overrides that make a live frame's derived legend read like QGIS's.

The expected labels are the ones OnlyMap 0.13.1 drew in Chromium for the
same expressions (the Night Desk / Daylight comparisons): "< 1", "1 - 2"
(an en dash), "≥ 4" for a threshold scale, the value itself for a category.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    CartographSpec,
    FrameSpec,
    LegendRowSpec,
    LegendSpec,
)
from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    GeometryKind,
    GraduatedClassSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.runtime_legend import (
    HEADING_SWATCH_COLOR,
    ClassedLayer,
    RuntimeLegend,
    derive_runtime_legend,
    describe_runtime_legend_changes,
    fit_frame_legends,
    fit_legend_rows,
    format_class_bound,
)

BLUE = SymbolSpec(fill_color=Color(r=8, g=81, b=156))
LINE = SymbolSpec(stroke_color=Color(r=85, g=85, b=85), stroke_width=1.9)


def _layer(layer_id, geometry, renderer) -> ExportLayer:
    return ExportLayer(
        layer_id=layer_id,
        name=layer_id,
        geometry_kind=geometry,
        source_kind=SourceKind.MEMORY,
        renderer=renderer,
    )


def _flood() -> ExportLayer:
    classes = tuple(
        GraduatedClassSpec(
            lower=k, upper=k + 1, label=f"{k}\u2013{k + 1} m", symbol=BLUE
        )
        for k in range(5)
    )
    return _layer(
        "flood",
        GeometryKind.POLYGON,
        RendererSpec(kind=RendererKind.GRADUATED, field_name="depth", classes=classes),
    )


def _soil(labels=("Clay", "Loam")) -> ExportLayer:
    categories = tuple(
        CategorySpec(value=value, label=label, symbol=BLUE)
        for value, label in zip(("Clay", "Loam"), labels)
    )
    return _layer(
        "soil",
        GeometryKind.POINT,
        RendererSpec(
            kind=RendererKind.CATEGORIZED, field_name="soil", categories=categories
        ),
    )


def _single(layer_id, geometry, symbol=LINE) -> ExportLayer:
    return _layer(
        layer_id, geometry, RendererSpec(kind=RendererKind.SINGLE, symbol=symbol)
    )


class TestDeriveRuntimeLegend:
    def test_threshold_classes_are_labelled_from_their_breaks(self) -> None:
        legend = derive_runtime_legend(
            "scale($depth, threshold, ['#eff3ff', '#bdd7e7', '#6baed6', "
            "'#3182bd', '#08519c'], domain=[1, 2, 3, 4])"
        )
        assert legend == RuntimeLegend(
            "classes", ("< 1", "1 \u2013 2", "2 \u2013 3", "3 \u2013 4", "≥ 4")
        )

    def test_categories_are_labelled_by_value(self) -> None:
        legend = derive_runtime_legend(
            "$use == 'Housing' ? '#d95f02' : $use == 7 ? '#a6c96a' : "
            "$use == 'O\\'Neil' ? '#000000' : '#888888cc'"
        )
        assert legend == RuntimeLegend("categories", ("Housing", "7", "O'Neil"))

    def test_a_chain_without_a_quoted_fallback_derives_nothing(self) -> None:
        assert derive_runtime_legend("$use == 'a' ? '#000000' : null") is None

    def test_a_constant_colour_derives_nothing(self) -> None:
        assert derive_runtime_legend("'#3b7dd8'") is None

    def test_a_sequential_scale_is_a_ramp(self) -> None:
        legend = derive_runtime_legend(
            "scale($v, sequential, ['#000000', '#ffffff'], domain=[0, 10])"
        )
        assert legend == RuntimeLegend("ramp")

    def test_bounds_print_like_javascripts_en_us_locale(self) -> None:
        assert format_class_bound(1) == "1"
        assert format_class_bound(1000) == "1,000"
        assert format_class_bound(2.5) == "2.5"
        assert format_class_bound(0.333333) == "0.33"
        assert format_class_bound(1.005) == "1.01"
        assert format_class_bound(-1234.5) == "-1,234.5"


class TestFitLegendRows:
    def test_graduated_classes_get_their_qgis_labels_back(self) -> None:
        rows = fit_legend_rows([LegendRowSpec(layer="flood", order=0)], [_flood()])
        relabels = {(r.match, r.label) for r in rows if r.match is not None}
        assert relabels == {
            ("< 1", "0\u20131 m"),
            ("1 \u2013 2", "1\u20132 m"),
            ("2 \u2013 3", "2\u20133 m"),
            ("3 \u2013 4", "3\u20134 m"),
            ("≥ 4", "4\u20135 m"),
        }

    def test_a_classed_layer_is_headed_without_a_visible_swatch(self) -> None:
        rows = fit_legend_rows([LegendRowSpec(layer="flood", order=3)], [_flood()])
        assert rows[0] == LegendRowSpec(
            layer="flood", order=3, color=HEADING_SWATCH_COLOR, shape="line"
        )

    def test_lines_and_points_get_their_own_swatch_shape(self) -> None:
        layers = [
            _single("roads", GeometryKind.LINE),
            _single("wells", GeometryKind.POINT, BLUE),
            _single("parcels", GeometryKind.POLYGON, BLUE),
        ]
        rows = fit_legend_rows(
            [
                LegendRowSpec(layer=layer.layer_id, order=i)
                for i, layer in enumerate(layers)
            ],
            layers,
        )
        assert [r.shape for r in rows] == ["line", "point", None]
        assert all(r.color is None for r in rows)

    def test_categories_labelled_as_their_values_need_no_rows(self) -> None:
        rows = fit_legend_rows([LegendRowSpec(layer="soil", order=0)], [_soil()])
        assert [r.match for r in rows] == [None]

    def test_a_category_label_unlike_its_value_is_restored(self) -> None:
        rows = fit_legend_rows(
            [LegendRowSpec(layer="soil", order=0)], [_soil(("Heavy clay", "Loam"))]
        )
        assert LegendRowSpec(layer="soil", match="Clay", label="Heavy clay") in rows

    def test_a_grouped_class_reads_as_one_row_and_the_catch_all_as_none(self) -> None:
        categories = (
            CategorySpec(
                value="Clay", label="Heavy soil", symbol=BLUE, extra_values=("Loam",)
            ),
            CategorySpec(value=None, label="", symbol=BLUE, is_default=True),
        )
        layer = _layer(
            "soil",
            GeometryKind.POINT,
            RendererSpec(
                kind=RendererKind.CATEGORIZED, field_name="soil", categories=categories
            ),
        )
        rows = fit_legend_rows([LegendRowSpec(layer="soil", order=0)], [layer])

        assert LegendRowSpec(layer="soil", match="Clay", label="Heavy soil") in rows
        assert LegendRowSpec(layer="soil", match="Loam", hide=True) in rows
        assert not any(r.match is None and r.hide for r in rows)

    def test_hand_edited_rows_are_rekeyed_on_the_runtime_label(self) -> None:
        """The reader keys a renamed or hidden class on its QGIS label; the
        runtime only matches its own."""
        rows = fit_legend_rows(
            [
                LegendRowSpec(layer="flood", order=0),
                LegendRowSpec(layer="flood", match="0\u20131 m", label="Dry"),
                LegendRowSpec(layer="flood", match="4\u20135 m", hide=True),
            ],
            [_flood()],
        )
        assert LegendRowSpec(layer="flood", match="< 1", label="Dry") in rows
        assert LegendRowSpec(layer="flood", match="≥ 4", hide=True) in rows
        assert [r for r in rows if r.match == "< 1"] == [
            LegendRowSpec(layer="flood", match="< 1", label="Dry")
        ]

    def test_a_hidden_layer_row_is_left_alone(self) -> None:
        rows = fit_legend_rows(
            [LegendRowSpec(layer="roads", hide=True)],
            [_single("roads", GeometryKind.LINE)],
        )
        assert rows == (LegendRowSpec(layer="roads", hide=True),)

    def test_rows_for_other_layers_are_untouched(self) -> None:
        row = LegendRowSpec(layer="elsewhere", order=1)
        rows = fit_legend_rows([row], [_flood()])
        assert rows[0] == row
        assert all(r.layer == "flood" for r in rows[1:])


class TestFitFrameLegends:
    def test_only_this_frames_derived_legends_change(self) -> None:
        def legend(item_id, frame, derived=True):
            return LegendSpec(
                id=item_id,
                box=Box(0, 0, 10, 10),
                for_=frame,
                derived=derived,
                rows=(LegendRowSpec(layer="roads", order=0),),
            )

        frames = tuple(
            FrameSpec(
                id=frame_id, box=Box(0, 0, 50, 50), mode="live", center=(0, 0), zoom=3
            )
            for frame_id in ("main", "inset")
        )
        spec = CartographSpec(
            cartograph_id="c",
            width=100,
            height=100,
            items=(
                *frames,
                legend("a", "main"),
                legend("b", "inset"),
                legend("c", "main", False),
            ),
        )
        fitted = fit_frame_legends(spec, "main", [_single("roads", GeometryKind.LINE)])
        shapes = [
            item.rows[0].shape for item in fitted.items if isinstance(item, LegendSpec)
        ]
        assert shapes == ["line", None, None]


class TestDescribeRuntimeLegendChanges:
    def test_single_symbol_one_column_legends_differ_in_nothing(self) -> None:
        assert describe_runtime_legend_changes([], 1) == []

    def test_classed_point_layers_and_columns_are_named(self) -> None:
        changes = describe_runtime_legend_changes(
            [ClassedLayer("point", categorized=True, class_count=14)], 2
        )
        text = " ".join(changes)
        assert "square" in text
        assert "'other'" in text
        assert "+N more" in text
        assert "columns" in text

    def test_graduated_polygons_only_name_the_field_heading(self) -> None:
        changes = describe_runtime_legend_changes(
            [ClassedLayer("polygon", categorized=False, class_count=5)], 1
        )
        assert len(changes) == 1
        assert "field" in changes[0]
