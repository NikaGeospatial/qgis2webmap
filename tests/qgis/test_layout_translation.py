"""`core/layout_reader.py`'s item details: label styling, live legends,
graticules, colours, and the fidelity of live and static frames.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from atlas_fixtures import WEB_MERCATOR, add_map, build_parcels, build_roads

from nika_onlymap_exporter.core.cartograph_ir import (
    GraticuleSpec,
    LegendRowSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_reader import read_layout

qgis_core = pytest.importorskip("qgis.core")

from qgis.core import (  # noqa: E402
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLayerTreeGroup,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLineSymbol,
    QgsMapLayerLegendUtils,
    QgsPrintLayout,
    QgsRendererCategory,
    QgsTextFormat,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402


def _layout(project, *, crs=WEB_MERCATOR):
    project.setCrs(crs)
    parcels = build_parcels()
    roads = build_roads()
    project.addMapLayers([parcels, roads])
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("details")
    map_item = add_map(layout, [roads, parcels])  # roads drawn on top
    if crs != WEB_MERCATOR:
        map_item.setCrs(crs)
    return layout, map_item, parcels, roads


def _label(layout, text="Hello", item_id=""):
    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText(text)
    label.setId(item_id)
    label.attemptMove(QgsLayoutPoint(10, 150, QgsUnitTypes.LayoutMillimeters))
    label.attemptResize(QgsLayoutSize(80, 12, QgsUnitTypes.LayoutMillimeters))
    return label


def _only(reading, kind):
    (item,) = [i for i in reading.furniture if isinstance(i, kind)]
    return item


def _rows(report, status):
    return [f"{i.subject}: {i.detail}" for i in report.by_status(status)]


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


class TestLabelStyle:
    def _text(
        self, project, configure, item_id=""
    ) -> tuple[TextSpec, FidelityReportBuilder]:
        layout, *_ = _layout(project)
        label = _label(layout, item_id=item_id)
        configure(label)
        report = FidelityReportBuilder()
        return _only(read_layout(layout, report), TextSpec), report

    def test_font_family_size_colour_and_alignment(self, project) -> None:
        def configure(label):
            text_format = QgsTextFormat()
            font = QFont("DejaVu Sans")
            text_format.setFont(font)
            text_format.setSize(14)
            text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
            text_format.setColor(QColor("#123456"))
            label.setTextFormat(text_format)
            label.setHAlign(Qt.AlignmentFlag.AlignHCenter)

        text, _ = self._text(project, configure)
        assert text.font == '"DejaVu Sans", sans-serif'
        assert text.font_size == pytest.approx(14)
        assert text.color == "#123456"
        assert text.align == "center"
        assert text.weight is None
        assert text.kind == "text"

    @pytest.mark.parametrize(
        ("flag", "align"),
        [
            (Qt.AlignmentFlag.AlignLeft, "start"),
            (Qt.AlignmentFlag.AlignRight, "end"),
            (Qt.AlignmentFlag.AlignJustify, "start"),
        ],
    )
    def test_alignment_uses_the_runtime_spelling(self, project, flag, align) -> None:
        text, _ = self._text(project, lambda label: label.setHAlign(flag))
        assert text.align == align

    @pytest.mark.parametrize(
        ("weight", "css"),
        [
            (QFont.Weight.Bold, "bold"),
            (QFont.Weight.DemiBold, "600"),
            (QFont.Weight.Light, "300"),
        ],
    )
    def test_weight_comes_from_the_font(self, project, weight, css) -> None:
        def configure(label):
            text_format = label.textFormat()
            font = text_format.font()
            font.setWeight(weight)
            text_format.setFont(font)
            label.setTextFormat(text_format)

        text, _ = self._text(project, configure)
        assert text.weight == css

    def test_a_regular_title_says_so_and_a_bold_one_says_bold(self, project) -> None:
        """The runtime's theme draws a title heavy unless told otherwise."""

        def regular(label):
            label.setCustomProperty("carto/kind", "title")

        def bold(label):
            regular(label)
            text_format = label.textFormat()
            font = text_format.font()
            font.setBold(True)
            text_format.setFont(font)
            label.setTextFormat(text_format)

        from nika_onlymap_exporter.core.layout_reader import TEXT_KIND_PROPERTY

        def titled(configure):
            def apply(label):
                configure(label)
                label.setCustomProperty(TEXT_KIND_PROPERTY, "title")

            return apply

        text, _ = self._text(project, titled(lambda label: None))
        assert (text.kind, text.weight) == ("title", "400")
        text, _ = self._text(project, titled(bold))
        assert (text.kind, text.weight) == ("title", "bold")

    def test_a_regular_plain_text_leaves_the_weight_out(self, project) -> None:
        text, _ = self._text(project, lambda label: None)
        assert (text.kind, text.weight) == ("text", None)

    def test_millimetre_sizes_become_points(self, project) -> None:
        def configure(label):
            text_format = label.textFormat()
            text_format.setSize(5)
            text_format.setSizeUnit(QgsUnitTypes.RenderMillimeters)
            label.setTextFormat(text_format)

        text, _ = self._text(project, configure)
        assert text.font_size == pytest.approx(5 * 72 / 25.4, abs=0.01)

    def test_item_background_becomes_bg_only_when_enabled(self, project) -> None:
        def with_background(label):
            label.setBackgroundEnabled(True)
            label.setBackgroundColor(QColor(255, 255, 0, 128))

        text, _ = self._text(project, with_background)
        assert text.bg == "#ffff0080"
        text, _ = self._text(project, lambda label: label.setBackgroundEnabled(False))
        assert text.bg is None

    @pytest.mark.parametrize(
        ("item_id", "kind"),
        [
            ("Title", "title"),
            ("map_title", "title"),
            ("Credits", "attribution"),
            ("note", "text"),
        ],
    )
    def test_kind_follows_the_item_name(self, project, item_id, kind) -> None:
        text, _ = self._text(project, lambda label: None, item_id=item_id)
        assert text.kind == kind

    def test_italic_is_reported(self, project) -> None:
        def configure(label):
            text_format = label.textFormat()
            font = text_format.font()
            font.setItalic(True)
            text_format.setFont(font)
            label.setTextFormat(text_format)

        _, report = self._text(project, configure)
        assert any("italic" in r for r in _rows(report, FidelityStatus.APPROXIMATED))

    def test_html_labels_keep_their_text(self, project) -> None:
        def configure(label):
            label.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            label.setText("<b>Lot</b> <i>12</i>")

        text, report = self._text(project, configure)
        assert text.body == "Lot 12"
        assert any("HTML" in r for r in _rows(report, FidelityStatus.APPROXIMATED))


# --------------------------------------------------------------------------
# Legends on live frames
# --------------------------------------------------------------------------


def _legend(layout, map_item):
    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.setLinkedMap(map_item)
    legend.attemptResize(QgsLayoutSize(40, 30, QgsUnitTypes.LayoutMillimeters))
    return legend


def _set_manual(legend):
    if hasattr(legend, "setSyncMode"):
        from qgis.core import Qgis

        legend.setSyncMode(Qgis.LegendSyncMode.Manual)
    else:  # QGIS 3
        legend.setAutoUpdateModel(False)


def _categorise(parcels):
    parcels.setRenderer(
        QgsCategorizedSymbolRenderer(
            "size",
            [
                QgsRendererCategory(
                    "small", QgsFillSymbol.createSimple({"color": "red"}), "Small lot"
                ),
                QgsRendererCategory(
                    "large", QgsFillSymbol.createSimple({"color": "blue"}), "Large lot"
                ),
            ],
        )
    )


class TestLiveLegend:
    def test_an_automatic_legend_lists_layers_top_first(self, project) -> None:
        """The runtime derives rows in document (draw) order, bottom first;
        QGIS lists the top layer first -- `order` puts them back."""
        layout, map_item, parcels, roads = _layout(project)
        _legend(layout, map_item)
        legend = _only(read_layout(layout, FidelityReportBuilder()), LegendSpec)
        assert legend.derived
        assert legend.rows == (
            LegendRowSpec(layer=roads.id(), order=0),
            LegendRowSpec(layer=parcels.id(), order=1),
        )

    def test_a_renamed_layer_gets_a_label(self, project) -> None:
        layout, map_item, parcels, roads = _layout(project)
        legend_item = _legend(layout, map_item)
        _set_manual(legend_item)
        node = legend_item.model().rootGroup().findLayer(parcels.id())
        node.setCustomProperty("legend/title-label", "Land parcels")
        legend = _only(read_layout(layout, FidelityReportBuilder()), LegendSpec)
        row = next(
            r for r in legend.rows if r.layer == parcels.id() and r.match is None
        )
        assert row.label == "Land parcels"
        roads_row = next(r for r in legend.rows if r.layer == roads.id())
        assert roads_row.label is None

    def test_a_layer_removed_from_the_legend_is_hidden(self, project) -> None:
        layout, map_item, parcels, roads = _layout(project)
        legend_item = _legend(layout, map_item)
        _set_manual(legend_item)
        root = legend_item.model().rootGroup()
        root.removeChildNode(root.findLayer(roads.id()))
        legend = _only(read_layout(layout, FidelityReportBuilder()), LegendSpec)
        assert LegendRowSpec(layer=roads.id(), hide=True) in legend.rows
        assert LegendRowSpec(layer=parcels.id(), order=0) in legend.rows

    def test_category_edits_become_match_rows(self, project) -> None:
        layout, map_item, parcels, _roads = _layout(project)
        _categorise(parcels)
        legend_item = _legend(layout, map_item)
        _set_manual(legend_item)
        node = legend_item.model().rootGroup().findLayer(parcels.id())
        QgsMapLayerLegendUtils.setLegendNodeUserLabel(node, 0, "Smallholding")
        QgsMapLayerLegendUtils.setLegendNodeOrder(node, [0])  # "Large lot" removed
        legend = _only(read_layout(layout, FidelityReportBuilder()), LegendSpec)
        assert (
            LegendRowSpec(layer=parcels.id(), match="Small lot", label="Smallholding")
            in legend.rows
        )
        assert (
            LegendRowSpec(layer=parcels.id(), match="Large lot", hide=True)
            in legend.rows
        )

    def test_reordered_categories_and_groups_are_reported(self, project) -> None:
        layout, map_item, parcels, _roads = _layout(project)
        _categorise(parcels)
        legend_item = _legend(layout, map_item)
        _set_manual(legend_item)
        root = legend_item.model().rootGroup()
        node = root.findLayer(parcels.id())
        QgsMapLayerLegendUtils.setLegendNodeOrder(node, [1, 0])
        group = QgsLayerTreeGroup("Base")
        root.addChildNode(group)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        rows = _rows(report, FidelityStatus.APPROXIMATED)
        assert any("reorders the entries" in r for r in rows)
        assert any("groups are not kept" in r for r in rows)


class TestLiveLegendChecks:
    """Checks may only call a live legend kept when the browser draws it as
    QGIS does: the runtime's own legend still differs for classed layers."""

    def _legend_rows(self, report):
        return [
            item
            for item in report.items
            if item.subject.startswith("Legend")
            and "derive from the interactive frame" in item.detail
        ]

    def test_single_symbol_layers_in_one_column_are_kept(self, project) -> None:
        layout, map_item, *_ = _layout(project)
        _legend(layout, map_item)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        (row,) = self._legend_rows(report)
        assert row.status is FidelityStatus.PRESERVED

    def test_a_categorized_layer_says_what_the_browser_changes(self, project) -> None:
        layout, map_item, parcels, _roads = _layout(project)
        _categorise(parcels)
        _legend(layout, map_item).setColumnCount(2)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        (row,) = self._legend_rows(report)
        assert row.status is FidelityStatus.APPROXIMATED
        assert "'other' row" in row.detail
        assert "classified field" in row.detail
        assert "columns" in row.detail
        # Polygon classes are squares in QGIS too.
        assert "square" not in row.detail

    def test_a_rule_based_layer_says_it_too(self, project) -> None:
        """It is exported as categories, so the browser changes its legend
        the same way."""
        from qgis.core import QgsRuleBasedRenderer

        layout, map_item, parcels, _roads = _layout(project)
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(
            QgsRuleBasedRenderer.Rule(
                QgsFillSymbol.createSimple({"color": "red"}), 0, 0, "ELSE", "All"
            )
        )
        parcels.setRenderer(QgsRuleBasedRenderer(root))
        _legend(layout, map_item)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        (row,) = self._legend_rows(report)
        assert row.status is FidelityStatus.APPROXIMATED
        assert "'other' row" in row.detail
        assert "classified field" in row.detail


class TestStaticLegendShapes:
    def test_literal_rows_use_the_runtimes_shape_words(self, project) -> None:
        from cartograph_fixtures import all_static

        layout, map_item, _parcels, _roads = _layout(project)
        _legend(layout, map_item)
        legend = _only(
            read_layout(
                layout, FidelityReportBuilder(), frame_modes=all_static(layout)
            ),
            LegendSpec,
        )
        assert {row.shape for row in legend.rows} == {"line", "polygon"}


# --------------------------------------------------------------------------
# Live and static frame fidelity
# --------------------------------------------------------------------------


class TestFrameFidelity:
    def test_a_web_mercator_live_frame_is_exact(self, project) -> None:
        layout, *_ = _layout(project)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        assert "Map frame 'Map 1'" in [
            i.subject for i in report.by_status(FidelityStatus.PRESERVED)
        ]

    def test_a_rotated_geographic_live_frame_is_reprojected(self, project) -> None:
        # Unrotated, a geographic map draws in its own plane (plate carree);
        # a plane has no bearing, so a rotated one is redrawn in Web Mercator.
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        layout, map_item, *_ = _layout(project, crs=wgs84)
        map_item.setMapRotation(15)
        report = FidelityReportBuilder()
        reading = read_layout(layout, report)
        assert reading.frames[0].mode == "live"
        rows = _rows(report, FidelityStatus.APPROXIMATED)
        assert any("Web Mercator" in r and "EPSG:4326" in r for r in rows)

    def test_a_static_frame_rasterises_each_layer_under_that_layer(
        self, project
    ) -> None:
        layout, map_item, parcels, roads = _layout(project)
        report = FidelityReportBuilder()
        read_layout(layout, report, frame_modes={map_item.uuid(): "static"})
        rasterised = report.by_status(FidelityStatus.RASTER_FALLBACK)
        assert {i.layer_id for i in rasterised} == {parcels.id(), roads.id()}

    def test_a_live_frame_rasterises_nothing(self, project) -> None:
        layout, *_ = _layout(project)
        report = FidelityReportBuilder()
        read_layout(layout, report)
        assert report.by_status(FidelityStatus.RASTER_FALLBACK) == ()


# --------------------------------------------------------------------------
# Graticules
# --------------------------------------------------------------------------


def _grid(map_item) -> QgsLayoutItemMapGrid:
    grid = QgsLayoutItemMapGrid("grid", map_item)
    map_item.grids().addGrid(grid)
    grid.setEnabled(True)
    grid.setIntervalX(1000)
    grid.setIntervalY(1000)
    return grid


class TestGraticule:
    def _graticule(
        self, project, configure
    ) -> tuple[GraticuleSpec, FidelityReportBuilder]:
        layout, map_item, *_ = _layout(project)
        configure(_grid(map_item))
        report = FidelityReportBuilder()
        return _only(read_layout(layout, report), GraticuleSpec), report

    def test_dms_annotations(self, project) -> None:
        def configure(grid):
            grid.setAnnotationEnabled(True)
            grid.setAnnotationFormat(
                QgsLayoutItemMapGrid.AnnotationFormat.DegreeMinuteSecond
            )

        graticule, _ = self._graticule(project, configure)
        assert graticule.format == "dms"

    def test_degree_minute_is_approximated_to_dms(self, project) -> None:
        def configure(grid):
            grid.setAnnotationEnabled(True)
            grid.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.DegreeMinute)

        graticule, report = self._graticule(project, configure)
        assert graticule.format == "dms"
        assert any("minutes" in r for r in _rows(report, FidelityStatus.APPROXIMATED))

    def test_decimal_annotations_have_no_format(self, project) -> None:
        def configure(grid):
            grid.setAnnotationEnabled(True)
            grid.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.Decimal)

        graticule, _ = self._graticule(project, configure)
        assert graticule.format is None

    def test_inside_annotations(self, project) -> None:
        def configure(grid):
            grid.setAnnotationEnabled(True)
            for side in (
                QgsLayoutItemMapGrid.BorderSide.Left,
                QgsLayoutItemMapGrid.BorderSide.Right,
                QgsLayoutItemMapGrid.BorderSide.Top,
                QgsLayoutItemMapGrid.BorderSide.Bottom,
            ):
                grid.setAnnotationPosition(
                    QgsLayoutItemMapGrid.AnnotationPosition.InsideMapFrame, side
                )

        graticule, _ = self._graticule(project, configure)
        assert graticule.labels == "inside"

    def test_mixed_annotations_go_outside_and_are_reported(self, project) -> None:
        def configure(grid):
            grid.setAnnotationEnabled(True)
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.AnnotationPosition.InsideMapFrame,
                QgsLayoutItemMapGrid.BorderSide.Left,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame,
                QgsLayoutItemMapGrid.BorderSide.Right,
            )

        graticule, report = self._graticule(project, configure)
        assert graticule.labels == "outside"
        assert any(
            "inside the map" in r for r in _rows(report, FidelityStatus.APPROXIMATED)
        )

    def test_no_annotations(self, project) -> None:
        graticule, _ = self._graticule(
            project, lambda grid: grid.setAnnotationEnabled(False)
        )
        assert graticule.labels == "none"

    def test_colour_comes_from_the_line_symbol(self, project) -> None:
        def configure(grid):
            grid.setLineSymbol(QgsLineSymbol.createSimple({"color": "#ff0000"}))

        graticule, _ = self._graticule(project, configure)
        assert graticule.color == "#ff0000"

    def test_page_unit_intervals_are_left_to_the_runtime(self, project) -> None:
        def configure(grid):
            units = QgsLayoutItemMapGrid.GridUnit
            # `MM` on QGIS 3, `Millimeters` on QGIS 4.
            grid.setUnits(getattr(units, "Millimeters", None) or units.MM)
            grid.setIntervalX(20)
            grid.setIntervalY(20)

        graticule, report = self._graticule(project, configure)
        assert graticule.interval_x is None and graticule.interval_y is None
        assert any(
            "page units" in r for r in _rows(report, FidelityStatus.APPROXIMATED)
        )


# --------------------------------------------------------------------------
# Colours
# --------------------------------------------------------------------------


class TestColours:
    def test_scale_bar_colour(self, project) -> None:
        layout, map_item, *_ = _layout(project)
        scalebar = QgsLayoutItemScaleBar(layout)
        layout.addLayoutItem(scalebar)
        scalebar.setLinkedMap(map_item)
        scalebar.setStyle("Single Box")
        scalebar.applyDefaultSize()
        scalebar.setLineSymbol(QgsLineSymbol.createSimple({"color": "#336699"}))
        bar = _only(read_layout(layout, FidelityReportBuilder()), ScalebarSpec)
        assert bar.color == "#336699"

    def test_north_arrow_colour_from_its_svg_fill(self, project, tmp_path) -> None:
        layout, map_item, *_ = _layout(project)
        svg = tmp_path / "north.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
            '<polygon points="12,2 18,22 12,17 6,22" fill="param(fill) #000"/></svg>',
            encoding="utf-8",
        )
        picture = QgsLayoutItemPicture(layout)
        layout.addLayoutItem(picture)
        picture.setPicturePath(str(svg))
        picture.setLinkedMap(map_item)
        picture.setSvgFillColor(QColor("#aa0000"))
        picture.attemptResize(QgsLayoutSize(10, 10, QgsUnitTypes.LayoutMillimeters))
        north = _only(read_layout(layout, FidelityReportBuilder()), NorthSpec)
        assert north.color == "#aa0000"

    def _shape(self, project, symbol) -> ShapeSpec:
        layout, *_ = _layout(project)
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        shape.attemptResize(QgsLayoutSize(20, 10, QgsUnitTypes.LayoutMillimeters))
        shape.setSymbol(symbol)
        return _only(read_layout(layout, FidelityReportBuilder()), ShapeSpec)

    def test_shape_fill_opacity_from_symbol_and_colour_alpha(self, project) -> None:
        symbol = QgsFillSymbol.createSimple({"color": "255,0,0,128"})
        symbol.setOpacity(0.5)
        shape = self._shape(project, symbol)
        assert shape.fill_opacity == pytest.approx(0.5 * 128 / 255, abs=1e-3)

    def test_an_opaque_shape_writes_no_opacity(self, project) -> None:
        shape = self._shape(project, QgsFillSymbol.createSimple({"color": "#ff0000"}))
        assert shape.fill_opacity is None

    def test_a_shape_with_no_brush_has_no_fill(self, project) -> None:
        symbol = QgsFillSymbol.createSimple({"color": "#ff0000", "style": "no"})
        shape = self._shape(project, symbol)
        assert shape.fill_opacity == 0.0


# --------------------------------------------------------------------------
# Round-trip fixes: rotation, scale bar units and styles, embedded pictures
# --------------------------------------------------------------------------


class TestRotatedPlacement:
    def test_a_rotated_item_keeps_its_unrotated_box(self, project) -> None:
        """QGIS reports a rotated item's rotated top-left corner; the runtime
        rotates the x/y/w/h box about its centre, so the box written must be
        the unrotated one around the same centre."""
        layout, *_ = _layout(project)
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)
        shape.attemptMove(QgsLayoutPoint(10, 150, QgsUnitTypes.LayoutMillimeters))
        shape.attemptResize(QgsLayoutSize(100, 50, QgsUnitTypes.LayoutMillimeters))
        shape.setItemRotation(20)
        spec = _only(read_layout(layout, FidelityReportBuilder()), ShapeSpec)
        assert spec.rotation == pytest.approx(20)
        assert (spec.box.x, spec.box.y) == (pytest.approx(10), pytest.approx(150))
        assert (spec.box.w, spec.box.h) == (pytest.approx(100), pytest.approx(50))


class TestScalebarDetails:
    def _bar(self, project, configure) -> tuple[ScalebarSpec, FidelityReportBuilder]:
        layout, map_item, *_ = _layout(project)
        scalebar = QgsLayoutItemScaleBar(layout)
        layout.addLayoutItem(scalebar)
        scalebar.setLinkedMap(map_item)
        scalebar.setStyle("Single Box")
        scalebar.applyDefaultSize()
        configure(scalebar)
        report = FidelityReportBuilder()
        return _only(read_layout(layout, report), ScalebarSpec), report

    @pytest.mark.parametrize(
        ("unit", "metres"),
        [
            (QgsUnitTypes.DistanceMeters, 2.0),
            (QgsUnitTypes.DistanceKilometers, 2000.0),
            (QgsUnitTypes.DistanceFeet, 2 * 0.3048),
            (QgsUnitTypes.DistanceNauticalMiles, 2 * 1852.0),
        ],
    )
    def test_segment_length_is_in_metres(self, project, unit, metres) -> None:
        def configure(scalebar):
            scalebar.setUnits(unit)
            scalebar.setUnitsPerSegment(2)

        bar, _ = self._bar(project, configure)
        assert bar.segment_length == pytest.approx(metres, rel=1e-6)

    @pytest.mark.parametrize("renderer_id", ["hollow", "stepped"])
    def test_current_style_ids_are_known(self, project, renderer_id) -> None:
        """Known by their current ids, and drawn as the nearest kind - which
        the report says, rather than calling it kept."""
        bar, report = self._bar(project, lambda sb: sb.setStyle(renderer_id))
        assert bar.kind == "single"
        assert any("style" in r for r in _rows(report, FidelityStatus.APPROXIMATED))

    def test_labels_drawn_differently_are_not_called_kept(self, project) -> None:
        def configure(scalebar):
            scalebar.setUnits(QgsUnitTypes.DistanceMeters)
            scalebar.setUnitLabel("m")
            scalebar.setNumberOfSegments(2)
            scalebar.setNumberOfSegmentsLeft(0)
            scalebar.setUnitsPerSegment(700)

        _bar, report = self._bar(project, configure)
        rows = [r for r in _rows(report, FidelityStatus.APPROXIMATED) if "Scale" in r]
        assert rows, _rows(report, FidelityStatus.PRESERVED)
        assert "below it" in rows[0]
        assert "'0 · 700 · 1400 m' rather than '0 · 700 · 1,400 m'" in rows[0]

    def test_a_bar_the_browser_labels_alike_is_kept(self, project) -> None:
        from qgis.core import Qgis

        below = getattr(Qgis, "ScaleBarDistanceLabelVerticalPlacement", None)
        if below is not None:
            below = below.BelowSegment
        else:  # pragma: no cover - before the enum moved to Qgis
            from qgis.core import QgsScaleBarSettings

            below = QgsScaleBarSettings.LabelBelowSegment

        def configure(scalebar):
            scalebar.setUnits(QgsUnitTypes.DistanceMeters)
            scalebar.setUnitLabel("m")
            scalebar.setNumberOfSegments(2)
            scalebar.setNumberOfSegmentsLeft(0)
            scalebar.setUnitsPerSegment(200)
            scalebar.setLabelVerticalPlacement(below)

        _bar, report = self._bar(project, configure)
        assert any(
            r.startswith("Scale bar") for r in _rows(report, FidelityStatus.PRESERVED)
        ), _rows(report, FidelityStatus.APPROXIMATED)

    def test_every_registered_style_is_mapped(self, project) -> None:
        from qgis.core import QgsApplication

        from nika_onlymap_exporter.core import layout_reader

        registry = QgsApplication.scaleBarRendererRegistry()
        for renderer_id in registry.renderers():
            if renderer_id == "Numeric":
                continue
            assert renderer_id in layout_reader._SCALEBAR_KIND_BY_STYLE, renderer_id


_PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestEmbeddedPictures:
    def _image(self, project, path: str):
        from nika_onlymap_exporter.core.cartograph_ir import ImageSpec

        layout, *_ = _layout(project)
        picture = QgsLayoutItemPicture(layout)
        layout.addLayoutItem(picture)
        picture.setPicturePath(path)
        picture.attemptResize(QgsLayoutSize(10, 10, QgsUnitTypes.LayoutMillimeters))
        return _only(read_layout(layout, FidelityReportBuilder()), ImageSpec)

    def test_an_embedded_base64_picture_is_kept(self, project) -> None:
        image = self._image(project, f"base64:{_PNG_1PX}")
        assert image.src == f"data:image/png;base64,{_PNG_1PX}"

    def test_a_suffixless_file_is_typed_by_its_bytes(self, project, tmp_path) -> None:
        svg = tmp_path / "logo"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>',
            encoding="utf-8",
        )
        image = self._image(project, str(svg))
        assert image.src.startswith("data:image/svg+xml;base64,")
