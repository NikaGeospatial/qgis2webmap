"""`core/runtime_legend_fit.py`: a derived legend that fits its box in the
browser, where the runtime's own legend is taller than QGIS's.

The layers are the Grand Canyon demo sheet's legend, whose box (190 x 66 mm,
3 columns) QGIS fills but the browser clipped mid-row.

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
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.runtime_legend_fit import (
    fit_frame_legend_boxes,
    fit_legend_to_box,
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
        assert "leaves out" in details[0] and "Contours" in details[0]

    def test_another_frames_legend_is_untouched(self) -> None:
        spec = self._spec()
        fitted = fit_frame_legend_boxes(
            spec, "main", _demo_layers(), FidelityReportBuilder()
        )
        other = next(i for i in fitted.items if getattr(i, "id", "") == "legend_other")
        assert other == next(i for i in spec.items if i.id == "legend_other")
