"""Markers drawn by QGIS itself, into a sprite sheet.

Needs a real PyQGIS, because the entire point of this module is that QGIS does
the drawing: a test with a stubbed renderer would verify our arithmetic and
nothing about whether a star comes out looking like a star.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64

import pytest

from nika_onlymap_exporter.core.export_ir import (
    FidelityStatus,
    GeometryKind,
    RendererKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.renderer_translator import (
    class_symbols,
    translate_renderer,
)
from nika_onlymap_exporter.core.symbol_atlas import layer_symbols
from nika_onlymap_exporter.core.symbol_rasterizer import (
    build_icon_atlas,
    render_symbol,
)

qgis_core = pytest.importorskip("qgis.core")
QtGui = pytest.importorskip("qgis.PyQt.QtGui")


def marker(shape: str = "star", color: str = "#ff0000", size: float = 4.0):
    return qgis_core.QgsMarkerSymbol.createSimple(
        {"name": shape, "color": color, "size": str(size)}
    )


def categorized_layer(make_memory_layer, symbols_by_value):
    """A point layer categorized on `name`, one class per given symbol."""
    layer = make_memory_layer(
        "points",
        features=[(value, [float(i), 0.0]) for i, value in enumerate(symbols_by_value)],
    )
    categories = [
        qgis_core.QgsRendererCategory(value, symbol, value)
        for value, symbol in symbols_by_value.items()
    ]
    layer.setRenderer(qgis_core.QgsCategorizedSymbolRenderer("name", categories))
    return layer


def atlas_for(layer, report=None):
    report = report or FidelityReportBuilder()
    spec = translate_renderer(layer, report)
    atlas, renderer = build_icon_atlas(
        layer.renderer(), spec, GeometryKind.POINT, report, layer.name(), layer.id()
    )
    return atlas, renderer, report


def decode(data_uri: str) -> QtGui.QImage:
    assert data_uri.startswith("data:image/png;base64,")
    payload = base64.b64decode(data_uri.split(",", 1)[1])
    image = QtGui.QImage()
    assert image.loadFromData(payload, "PNG"), "the sheet is not a readable PNG"
    return image


class TestRenderSymbol:
    """One symbol to one square picture."""

    def test_a_marker_renders_something(self, qgis_app) -> None:
        image = render_symbol(marker())
        assert image is not None
        assert image.width() > 0

    def test_the_picture_is_square(self, qgis_app) -> None:
        """The crop is centred on the symbol's own centre, because QGIS centres
        a marker on its feature - a tight crop would move the anchor."""
        image = render_symbol(marker("triangle"))
        assert image.width() == image.height()

    def test_it_is_supersampled_well_past_the_drawn_size(self, qgis_app) -> None:
        """A 4 mm marker is about 15 screen pixels. Rendering at that size would
        look soft on any high-DPI display."""
        image = render_symbol(marker(size=4.0))
        assert image.width() > 30

    def test_a_bigger_marker_makes_a_bigger_picture(self, qgis_app) -> None:
        small = render_symbol(marker(size=2.0))
        large = render_symbol(marker(size=8.0))
        assert large.width() > small.width()

    def test_a_zero_sized_marker_draws_nothing(self, qgis_app) -> None:
        """`None` rather than a blank cell: an invisible icon would make every
        feature in the layer disappear with nothing said."""
        assert render_symbol(marker(size=0.0)) is None

    def test_the_marker_is_not_clipped(self, qgis_app) -> None:
        """A marker larger than the first render box has to grow the box rather
        than come out with its points sliced off."""
        image = render_symbol(marker("star", size=30.0))
        assert image is not None
        edges = [
            image.pixelColor(0, 0).alpha(),
            image.pixelColor(image.width() - 1, image.height() - 1).alpha(),
        ]
        assert edges == [0, 0]

    def test_a_line_symbol_is_declined(self, qgis_app) -> None:
        """No `setSize`, so there is nothing to scale and nothing to centre."""
        line = qgis_core.QgsLineSymbol.createSimple({"color": "#ff0000"})
        assert render_symbol(line) is None


class TestClassSymbolPairing:
    """The atlas pairs specs with QGIS symbols positionally.

    That is only safe while `class_symbols` applies exactly the skip rules the
    translator does, so the two are pinned against each other here rather than
    trusted to stay in step.
    """

    def test_single(self, qgis_app, make_memory_layer) -> None:
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker()))
        spec = translate_renderer(layer, FidelityReportBuilder())
        assert len(class_symbols(layer.renderer())) == len(layer_symbols(spec)) == 1

    def test_categorized(self, qgis_app, make_memory_layer) -> None:
        layer = categorized_layer(
            make_memory_layer, {"a": marker("star"), "b": marker("square")}
        )
        spec = translate_renderer(layer, FidelityReportBuilder())
        assert len(class_symbols(layer.renderer())) == len(layer_symbols(spec)) == 2

    def test_a_switched_off_class_is_skipped_by_both(
        self, qgis_app, make_memory_layer
    ) -> None:
        """An unchecked class is dropped from the spec. If it stayed in the
        QGIS list, every icon after it would be off by one - a map where the
        classes wear each other's markers."""
        layer = categorized_layer(
            make_memory_layer,
            {"a": marker("star"), "b": marker("square"), "c": marker("triangle")},
        )
        renderer = layer.renderer()
        renderer.updateCategoryRenderState(1, False)
        spec = translate_renderer(layer, FidelityReportBuilder())
        assert len(class_symbols(renderer)) == len(layer_symbols(spec)) == 2


class TestBuildIconAtlas:
    def test_a_circle_layer_gets_no_atlas(self, qgis_app, make_memory_layer) -> None:
        """The common case must export byte-for-byte what it always has."""
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker("circle")))
        atlas, renderer, _ = atlas_for(layer)
        assert atlas is None
        assert renderer.symbol.icon_name is None

    def test_a_star_layer_gets_one(self, qgis_app, make_memory_layer) -> None:
        """qgis2web draws this as a circle and says nothing - upstream
        qgis2web#1218."""
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker("star")))
        atlas, renderer, _ = atlas_for(layer)
        assert atlas is not None
        assert renderer.symbol.icon_name in atlas.mapping

    def test_the_sheet_is_a_real_png(self, qgis_app, make_memory_layer) -> None:
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker("star")))
        atlas, _, _ = atlas_for(layer)
        image = decode(atlas.data_uri)
        assert image.width() > 0 and image.height() > 0

    def test_every_mapped_cell_is_inside_the_sheet(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = categorized_layer(
            make_memory_layer,
            {
                "a": marker("star", size=3.0),
                "b": marker("square", size=6.0),
                "c": marker("triangle", size=9.0),
            },
        )
        atlas, _, _ = atlas_for(layer)
        image = decode(atlas.data_uri)
        for cell in atlas.mapping.values():
            assert cell["x"] + cell["width"] <= image.width()
            assert cell["y"] + cell["height"] <= image.height()

    def test_every_cell_actually_has_a_marker_in_it(
        self, qgis_app, make_memory_layer
    ) -> None:
        """A cell of transparent pixels is an invisible feature. The centre is
        checked because a QGIS marker is centred on its anchor."""
        layer = categorized_layer(
            make_memory_layer, {"a": marker("star"), "b": marker("square")}
        )
        atlas, _, _ = atlas_for(layer)
        image = decode(atlas.data_uri)
        for name, cell in atlas.mapping.items():
            centre = image.pixelColor(
                int(cell["x"] + cell["width"] / 2), int(cell["y"] + cell["height"] / 2)
            )
            assert centre.alpha() > 0, f"cell {name} is empty"

    def test_qgis_applies_its_own_colours(self, qgis_app, make_memory_layer) -> None:
        """The reason for rendering rather than translating: the pixels are the
        pixels QGIS drew, so parametrised fills need no re-implementation."""
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(
            qgis_core.QgsSingleSymbolRenderer(marker("square", color="#00ff00"))
        )
        atlas, renderer, _ = atlas_for(layer)
        image = decode(atlas.data_uri)
        cell = atlas.mapping[renderer.symbol.icon_name]
        centre = image.pixelColor(
            int(cell["x"] + cell["width"] / 2), int(cell["y"] + cell["height"] / 2)
        )
        assert (centre.red(), centre.green(), centre.blue()) == (0, 255, 0)

    def test_identical_classes_share_one_cell(
        self, qgis_app, make_memory_layer
    ) -> None:
        """Six classes drawn with the same marker cost one cell, not six."""
        layer = categorized_layer(
            make_memory_layer,
            {"a": marker("star"), "b": marker("star"), "c": marker("star")},
        )
        atlas, renderer, _ = atlas_for(layer)
        assert len(atlas.mapping) == 1
        names = {c.symbol.icon_name for c in renderer.categories}
        assert len(names) == 1

    def test_the_same_shape_in_two_colours_does_not(
        self, qgis_app, make_memory_layer
    ) -> None:
        layer = categorized_layer(
            make_memory_layer,
            {"a": marker("star", "#ff0000"), "b": marker("star", "#0000ff")},
        )
        atlas, _, _ = atlas_for(layer)
        assert len(atlas.mapping) == 2

    def test_each_class_asks_for_the_size_it_was_drawn_at(
        self, qgis_app, make_memory_layer
    ) -> None:
        """A graduated-by-size layer of icons: without per-class sizes every
        class draws the same and the map's whole point is lost."""
        layer = categorized_layer(
            make_memory_layer,
            {"a": marker("star", size=2.0), "b": marker("star", size=8.0)},
        )
        _, renderer, _ = atlas_for(layer)
        sizes = [c.symbol.icon_size for c in renderer.categories]
        assert sizes[0] < sizes[1]

    def test_the_drawn_size_is_about_what_qgis_shows(
        self, qgis_app, make_memory_layer
    ) -> None:
        """A 4 mm marker is ~15 px at 96 dpi. Generous bounds: the cell carries
        the stroke and a pixel of anti-aliasing slack, so it is a little larger
        than the nominal size by design."""
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker("square", size=4.0)))
        _, renderer, _ = atlas_for(layer)
        assert 10.0 < renderer.symbol.icon_size < 25.0

    def test_a_legend_swatch_exists_for_every_icon(
        self, qgis_app, make_memory_layer
    ) -> None:
        """The legend cannot crop the sheet, so each icon needs its own image -
        cut from the same rendering, so legend and map cannot diverge."""
        layer = categorized_layer(
            make_memory_layer, {"a": marker("star"), "b": marker("square")}
        )
        atlas, _, _ = atlas_for(layer)
        assert set(atlas.swatches) == set(atlas.mapping)
        for swatch in atlas.swatches.values():
            assert decode(swatch).width() > 0

    def test_the_outcome_is_reported(self, qgis_app, make_memory_layer) -> None:
        """Nothing a QGIS project expresses may vanish silently - and nothing it
        *kept* should go unrecorded either."""
        layer = make_memory_layer("points", features=[("a", [0.0, 0.0])])
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(marker("star")))
        _, _, report = atlas_for(layer)
        markers = [i for i in report.items if i.subject.startswith("Markers of")]
        assert markers
        assert markers[0].status is FidelityStatus.PRESERVED

    def test_a_line_layer_with_markers_is_reported_not_rasterised(
        self, qgis_app, make_memory_layer
    ) -> None:
        """deck.gl has no marker-along-a-path, so an arrow-decorated road loses
        its arrows. Saying nothing is how qgis2web loses them."""
        layer = make_memory_layer(
            "roads", geometry="LineString", features=[("a", [0.0, 0.0, 1.0, 1.0])]
        )
        line = qgis_core.QgsLineSymbol.createSimple({"color": "#ff0000"})
        line.changeSymbolLayer(0, qgis_core.QgsMarkerLineSymbolLayer())
        layer.setRenderer(qgis_core.QgsSingleSymbolRenderer(line))
        report = FidelityReportBuilder()
        spec = translate_renderer(layer, report)
        atlas, _ = build_icon_atlas(
            layer.renderer(), spec, GeometryKind.LINE, report, layer.name(), layer.id()
        )
        assert atlas is None

    def test_a_categorized_layer_keeps_its_class_order(
        self, qgis_app, make_memory_layer
    ) -> None:
        """The manifest's `get-icon` expression is positional, so a reordering
        here silently swaps the markers between classes."""
        layer = categorized_layer(
            make_memory_layer,
            {"a": marker("star"), "b": marker("square"), "c": marker("triangle")},
        )
        _, renderer, _ = atlas_for(layer)
        assert renderer.kind is RendererKind.CATEGORIZED
        assert [c.value for c in renderer.categories] == ["a", "b", "c"]
        assert [c.symbol.icon_name for c in renderer.categories] == ["i0", "i1", "i2"]
