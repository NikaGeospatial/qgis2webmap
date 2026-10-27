"""`core/runtime_legend_fit.py`: a derived legend that fits its box in the
browser, where the runtime's own legend is taller than QGIS's.

The layers are the Grand Canyon demo sheet's legend, whose box (190 x 66 mm,
3 columns) QGIS fills but the browser clipped mid-row.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import replace

from nika_onlymap_exporter.core.cartograph_ir import (
    Box,
    CartographSpec,
    FrameSpec,
    LegendRowSpec,
    LegendSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.export_ir import (
    CategorySpec,
    Color,
    ExportLayer,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.runtime_legend_fit import (
    PAGE_MARGIN_MM,
    GrowRoom,
    LegendBoxChange,
    fit_frame_legend_boxes,
    fit_legend_to_box,
    legend_grow_room,
    with_change_applied,
)

SWATCH = SymbolSpec(fill_color=Color(r=8, g=81, b=156))


def _classes(layer_id: str, name: str, labels: list[str]) -> ExportLayer:
    categories = tuple(
        CategorySpec(value=label.split()[0], label=label, symbol=SWATCH)
        for label in labels
    )
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.LINE,
        source_kind=SourceKind.MEMORY,
        renderer=RendererSpec(
            kind=RendererKind.CATEGORIZED, field_name="f", categories=categories
        ),
    )


def _single(layer_id: str, name: str) -> ExportLayer:
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.MEMORY,
        renderer=RendererSpec(kind=RendererKind.SINGLE, symbol=SWATCH),
    )


def _demo_layers() -> list[ExportLayer]:
    return [
        _single("highways", "Highways · base 2001"),
        _classes(
            "roads",
            "Roads · NPS 2015 (archived)",
            ["Asphalt road", "Gravel road", "Cinder road", "Natural surface", "None"],
        ),
        _classes(
            "trails",
            "Trails · NPS 2015 (archived)",
            ["1-Minimally Developed", "4-Highly Developed", "5-Fully Developed"],
        ),
        _single("river", "Colorado River · base 2001"),
        _classes("tribs", "Tributaries and washes", ["Named watercourse", "Unnamed"]),
        _classes("stations", "Water-quality stations", ["Stream", "Spring", "Lake"]),
        _single("park", "Park boundary · NPS 2021"),
        _classes("land", "Land tracts", ["Fee", "Less than Fee", "Private", "Public"]),
        _classes("adjacent", "Adjacent land", ["National forest", "Reservation"]),
        _single("village", "Grand Canyon Village"),
        _single("viewpoints", "Viewpoints · demo OSM data"),
        _single("summits", "Named summits · demo OSM data"),
        _single("features", "Named features · base 2001"),
        _classes("contours", "Contours", ["Index (500 m)", "Intermediate (100 m)"]),
    ]


#: Too short for the demo's legend even in four columns.
TIGHT = Box(10, 158, 190, 52)


def _legend(layers: list[ExportLayer], box: Box, columns: int = 3) -> LegendSpec:
    rows = tuple(
        LegendRowSpec(layer=layer.layer_id, order=index)
        for index, layer in enumerate(layers)
    )
    return LegendSpec(
        id="legend_main", box=box, for_="main", columns=columns, rows=rows
    )


class TestFitLegendToBox:
    def test_a_legend_that_fits_is_left_alone(self) -> None:
        layers = _demo_layers()[:4]
        item = _legend(layers, Box(10, 150, 190, 66))
        fitted, fit = fit_legend_to_box(item, layers)
        assert fitted == item
        assert fit.columns == 3
        assert fit.omitted_layers == ()

    def test_a_taller_box_holds_the_whole_demo_legend(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 150, 190, 110))
        fitted, fit = fit_legend_to_box(item, layers)
        assert fit.omitted_layers == ()
        assert fitted.rows == item.rows

    def test_the_demo_legend_in_its_own_box_does_not_run_past_it(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        fitted, fit = fit_legend_to_box(item, layers)
        assert fit.columns == 4
        assert fit.omitted_layers
        hidden = {row.layer for row in fitted.rows if row.hide and row.match is None}
        assert hidden == {
            layer.layer_id for layer in layers if layer.name in fit.omitted_layers
        }

    def test_layers_are_left_out_from_the_end_and_whole(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        _fitted, fit = fit_legend_to_box(item, layers)
        kept = [layer.name for layer in layers if layer.name not in fit.omitted_layers]
        assert kept == [layer.name for layer in layers[: len(kept)]]
        # Contours is a layer row and two classes: three of QGIS's entries.
        assert "Contours" in fit.omitted_layers
        assert fit.omitted_entries >= 3

    def test_a_layers_own_row_is_the_one_hidden(self) -> None:
        """The runtime reads the first row naming a layer alone, which is the
        ordered one -- a second row after it would never be read."""
        layers = _demo_layers()
        fitted, _fit = fit_legend_to_box(_legend(layers, TIGHT), layers)
        own = [row for row in fitted.rows if row.layer == "contours"]
        assert len(own) == 1
        assert own[0].hide and own[0].order is not None

    def test_a_hidden_layer_takes_no_room(self) -> None:
        layers = _demo_layers()
        rows = tuple(
            LegendRowSpec(layer=layer.layer_id, order=index, hide=index > 3)
            for index, layer in enumerate(layers)
        )
        item = LegendSpec(
            id="legend_main",
            box=Box(10, 150, 190, 66),
            for_="main",
            columns=3,
            rows=rows,
        )
        _fitted, fit = fit_legend_to_box(item, layers)
        assert fit.columns == 3
        assert fit.omitted_layers == ()

    def test_the_hidden_class_rows_take_no_room(self) -> None:
        layers = _demo_layers()[:2]
        tight = Box(10, 150, 60, 28)
        shown = _legend(layers, tight, columns=1)
        _fitted, full = fit_legend_to_box(shown, layers)
        assert full.omitted_layers == ("Roads · NPS 2015 (archived)",)
        hidden_classes = LegendSpec(
            id="legend_main",
            box=tight,
            for_="main",
            columns=1,
            rows=(
                *shown.rows,
                *(
                    LegendRowSpec(layer="roads", match=value, hide=True)
                    for value in ("Asphalt", "Gravel", "Cinder", "Natural")
                ),
            ),
        )
        _fitted, trimmed = fit_legend_to_box(hidden_classes, layers)
        assert trimmed.omitted_layers == ()

    def test_the_last_layer_is_never_left_out(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 158, 190, 4))
        fitted, fit = fit_legend_to_box(item, layers)
        assert layers[0].name not in fit.omitted_layers
        assert not next(r for r in fitted.rows if r.layer == "highways").hide


class TestFitFrameLegendBoxes:
    def _spec(self) -> CartographSpec:
        layers = _demo_layers()
        frames = [
            FrameSpec(
                id=name, box=Box(0, 0, 50, 50), mode="live", center=(0, 0), zoom=3
            )
            for name in ("main", "inset")
        ]
        return CartographSpec(
            cartograph_id="c",
            width=210,
            height=297,
            items=(
                *frames,
                _legend(layers, TIGHT),
                LegendSpec(
                    id="legend_other",
                    box=Box(0, 0, 20, 20),
                    for_="inset",
                    columns=1,
                    rows=(LegendRowSpec(layer="roads", order=0),),
                ),
            ),
        )

    def test_the_omitted_layers_and_their_count_are_reported(self) -> None:
        report = FidelityReportBuilder()
        fitted = fit_frame_legend_boxes(self._spec(), "main", _demo_layers(), report)
        legend = next(i for i in fitted.items if getattr(i, "id", "") == "legend_main")
        assert isinstance(legend, LegendSpec) and legend.columns == 4
        details = [
            item.detail for item in report.items if item.subject == "Legend 'main'"
        ]
        assert len(details) == 1
        assert "left without" in details[0] and "Contours" in details[0]
        assert "set in 4 columns instead of 3" in details[0]

    def test_another_frames_legend_is_untouched(self) -> None:
        spec = self._spec()
        fitted = fit_frame_legend_boxes(
            spec, "main", _demo_layers(), FidelityReportBuilder()
        )
        other = next(i for i in fitted.items if getattr(i, "id", "") == "legend_other")
        assert other == next(i for i in spec.items if i.id == "legend_other")


PAGE_H = 210.0


def _text(box: Box, **kwargs: object) -> TextSpec:
    return TextSpec(id="text_note", box=box, body="note", **kwargs)  # type: ignore[arg-type]


class TestLegendGrowRoom:
    def test_a_legend_in_the_lower_half_grows_upward_to_the_margin(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        room = legend_grow_room(item, [item], PAGE_H)
        assert room.anchor_bottom
        assert room.available_mm == 150 - PAGE_MARGIN_MM

    def test_a_legend_in_the_upper_half_grows_downward_to_the_margin(self) -> None:
        item = _legend(_demo_layers(), Box(10, 20, 190, 50))
        room = legend_grow_room(item, [item], PAGE_H)
        assert not room.anchor_bottom
        assert room.available_mm == PAGE_H - PAGE_MARGIN_MM - 70

    def test_it_stops_short_of_an_item_it_would_newly_overlap(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        title = _text(Box(20, 100, 80, 10))
        room = legend_grow_room(item, [item, title], PAGE_H)
        assert room.available_mm == 150 - 110

    def test_an_item_beside_it_is_no_obstacle(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 100, 50))
        beside = _text(Box(120, 100, 50, 10))
        room = legend_grow_room(item, [item, beside], PAGE_H)
        assert room.available_mm == 150 - PAGE_MARGIN_MM

    def test_the_map_it_legends_may_be_grown_over(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        frame = FrameSpec(id="main", box=Box(0, 0, 210, 150), mode="live")
        room = legend_grow_room(item, [frame, item], PAGE_H)
        assert room.available_mm == 150 - PAGE_MARGIN_MM

    def test_an_item_it_already_sits_on_does_not_stop_it(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        panel = ShapeSpec(id="shape_panel", box=Box(0, 140, 210, 70))
        room = legend_grow_room(item, [panel, item], PAGE_H)
        assert room.available_mm == 150 - PAGE_MARGIN_MM

    def test_another_frame_stops_it(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        inset = FrameSpec(id="inset", box=Box(20, 60, 50, 50), mode="live")
        room = legend_grow_room(item, [inset, item], PAGE_H)
        assert room.available_mm == 150 - 110

    def test_a_hidden_item_is_no_obstacle(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        title = _text(Box(20, 100, 80, 10), hidden=True)
        room = legend_grow_room(item, [item, title], PAGE_H)
        assert room.available_mm == 150 - PAGE_MARGIN_MM

    def test_a_legend_already_past_the_margin_does_not_grow(self) -> None:
        item = _legend(_demo_layers(), Box(10, 3, 190, 205))
        room = legend_grow_room(item, [item], PAGE_H)
        assert room.anchor_bottom and room.available_mm == 0

    def test_a_rotated_legend_does_not_grow(self) -> None:
        item = _legend(_demo_layers(), Box(10, 150, 190, 50))
        rotated = replace(item, rotation=15.0)
        assert legend_grow_room(rotated, [rotated], PAGE_H).available_mm == 0


class TestGrowingTheBoxFirst:
    def test_the_box_grows_and_keeps_the_users_columns(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 150, 190, 50))
        room = GrowRoom(anchor_bottom=True, available_mm=140)
        fitted, fit = fit_legend_to_box(item, layers, room)
        assert fitted.columns == 3 and fit.columns == 3
        assert fit.omitted_layers == ()
        assert fitted.box.h > 50 and fit.grown_mm == fitted.box.h - 50
        assert fitted.box.y + fitted.box.h == 200  # bottom edge kept
        assert fitted.box.x == 10 and fitted.box.w == 190

    def test_a_box_anchored_at_the_top_keeps_its_top(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 20, 190, 50))
        fitted, _fit = fit_legend_to_box(item, layers, GrowRoom(False, 140))
        assert fitted.box.y == 20 and fitted.box.h > 50

    def test_it_grows_no_more_than_it_needs(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 20, 190, 50))
        fitted, _fit = fit_legend_to_box(item, layers, GrowRoom(False, 140))
        smaller = fitted.box.h - 1.0
        again, _fit = fit_legend_to_box(
            replace(fitted, box=Box(10, 20, 190, smaller)),
            layers,
            GrowRoom(False, 140),
        )
        assert again.box.h == fitted.box.h

    def test_a_fitted_legend_is_left_alone(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 150, 190, 50))
        room = GrowRoom(True, 140)
        fitted, _fit = fit_legend_to_box(item, layers, room)
        again, fit = fit_legend_to_box(fitted, layers, room)
        assert again == fitted and fit.grown_mm == 0 and fit.omitted_layers == ()

    def test_a_box_the_user_made_bigger_is_never_shrunk(self) -> None:
        layers = _demo_layers()[:3]
        item = _legend(layers, Box(10, 100, 190, 100))
        fitted, fit = fit_legend_to_box(item, layers, GrowRoom(True, 90))
        assert fitted == item and fit.grown_mm == 0

    def test_columns_are_added_only_when_growing_is_not_enough(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, Box(10, 150, 190, 50), columns=2)
        fitted, fit = fit_legend_to_box(item, layers, GrowRoom(True, 35))
        assert fit.columns == 3 and fitted.columns == 3
        assert fit.omitted_layers == ()
        assert 0 < fit.grown_mm <= 35
        assert fitted.box.y + fitted.box.h == 200

    def test_layers_are_left_out_only_when_columns_and_growth_run_out(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        fitted, fit = fit_legend_to_box(item, layers, GrowRoom(True, 3))
        assert fit.omitted_layers
        assert fit.omitted_layer_ids
        assert fitted.box.h <= TIGHT.h + 3 + 0.01
        assert all(
            any(r.layer == layer_id and r.hide for r in fitted.rows)
            for layer_id in fit.omitted_layer_ids
        )

    def test_growing_saves_the_layers_a_fixed_box_would_drop(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        _fixed, fixed_fit = fit_legend_to_box(item, layers)
        grown, grown_fit = fit_legend_to_box(item, layers, GrowRoom(True, 100))
        assert fixed_fit.omitted_layers
        assert grown_fit.omitted_layers == () and grown_fit.columns == 3
        assert grown.box.h > TIGHT.h

    def test_the_second_pass_changes_nothing_even_after_dropping_layers(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        first, _fit = fit_legend_to_box(item, layers, GrowRoom(True, 3))
        second, second_fit = fit_legend_to_box(first, layers, GrowRoom(True, 0))
        assert second == first and second_fit.omitted_layers == ()


class TestFrameLegendGrowth:
    def _spec(self, extra: tuple[TextSpec, ...] = ()) -> CartographSpec:
        layers = _demo_layers()
        return CartographSpec(
            cartograph_id="c",
            width=297,
            height=PAGE_H,
            items=(
                FrameSpec(id="main", box=Box(0, 0, 297, 210), mode="live"),
                _legend(layers, Box(10, 150, 190, 50)),
                *extra,
            ),
        )

    def test_the_change_and_the_report_say_what_happened(self) -> None:
        report = FidelityReportBuilder()
        changes: list[LegendBoxChange] = []
        fitted = fit_frame_legend_boxes(
            self._spec(),
            "main",
            _demo_layers(),
            report,
            grow_on_page_height=PAGE_H,
            changes=changes,
        )
        legend = next(i for i in fitted.items if isinstance(i, LegendSpec))
        assert changes == [LegendBoxChange("legend_main", "main", legend.box, 3, ())]
        assert legend.box.h > 50 and legend.box.y + legend.box.h == 200
        (detail,) = [i.detail for i in report.items if i.subject == "Legend 'main'"]
        assert "mm taller" in detail and "changed to match" in detail

    def test_a_second_pass_over_the_result_reports_nothing(self) -> None:
        first = fit_frame_legend_boxes(
            self._spec(),
            "main",
            _demo_layers(),
            FidelityReportBuilder(),
            grow_on_page_height=PAGE_H,
        )
        report = FidelityReportBuilder()
        changes: list[LegendBoxChange] = []
        second = fit_frame_legend_boxes(
            first,
            "main",
            _demo_layers(),
            report,
            grow_on_page_height=PAGE_H,
            changes=changes,
        )
        assert second == first and changes == [] and not report.items

    def test_without_a_page_height_the_box_is_not_grown(self) -> None:
        changes: list[LegendBoxChange] = []
        fitted = fit_frame_legend_boxes(
            self._spec(),
            "main",
            _demo_layers(),
            FidelityReportBuilder(),
            changes=changes,
        )
        legend = next(i for i in fitted.items if isinstance(i, LegendSpec))
        assert legend.box.h == 50


class TestChangeApplied:
    def test_the_box_columns_and_hidden_layers_are_set(self) -> None:
        layers = _demo_layers()
        item = _legend(layers, TIGHT)
        change = LegendBoxChange(
            "legend_main", "main", Box(10, 100, 190, 110), 4, ("contours", "tribs")
        )
        applied = with_change_applied(item, change)
        assert applied.box == change.box and applied.columns == 4
        hidden = {row.layer for row in applied.rows if row.hide}
        assert hidden == {"contours", "tribs"}
        assert len(applied.rows) == len(item.rows)  # their own rows, not new ones
