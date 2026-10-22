"""The five newer designs against real QGIS: each becomes a print layout
that exports to a cartograph with the colours it chose, Night Desk's dark
page survives HTML and back, and Atlas Ledger's sheet number is blank on a
single page and live on atlas pages, in QGIS and in the export alike.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from atlas_fixtures import WEB_MERCATOR, build_parcels
from cartograph_fixtures import UTM, build_parcels_layer

from nika_onlymap_exporter.core.canvas_cartograph import (
    PREVIEW_LEGEND_LEFT_OUT_KEY,
    _wrap_headings,
    cartograph_from_view,
    template_preview,
)
from nika_onlymap_exporter.core.canvas_layout import page_dimensions
from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_ir import LegendSpec, TextSpec
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_builder import import_cartograph_file
from nika_onlymap_exporter.core.layout_reader import (
    LEGEND_LEFT_OUT_PROPERTY,
    read_layout,
)
from nika_onlymap_exporter.core.layout_templates import template_ids
from nika_onlymap_exporter.core.page_style import AUBERGINE, MINIMUM_TYPE, spacing_for
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

NEW_IDS = ("night-desk", "marginalia", "atlas-ledger", "town-hall", "wayfinder")


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.13.1",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    metadata = project.metadata()
    metadata.setAbstract("Housing has spread north of the main channel.")
    project.setMetadata(metadata)
    layer = build_parcels_layer()
    layer.serverProperties().setAttribution("Survey data © NIKA 2026")
    project.addMapLayer(layer)
    return layer


def _view(layer):
    extent = layer.extent()
    extent.grow(200)
    return extent


def _export(layout, project, runtime, frame_modes, report=None) -> str:
    report = FidelityReportBuilder() if report is None else report
    return finish_cartograph(
        prepare_cartograph(
            layout, project, runtime, report, frame_modes=frame_modes, raster_cache=None
        ),
        project,
        report,
    )


def _tag(html: str, element: str, element_id: str) -> str:
    match = re.search(rf'<{element} id="{element_id}"[^>]*>', html)
    assert match is not None, element_id
    return match.group(0)


def _text_body(html: str, element_id: str) -> str:
    match = re.search(rf'<om-text id="{element_id}"[^>]*>([^<]*)<', html)
    assert match is not None, element_id
    return match.group(1)


class TestEveryNewDesign:
    @pytest.mark.parametrize("template_id", NEW_IDS)
    def test_builds_and_exports_with_its_own_colours(
        self, template_id, parcels, project, runtime
    ) -> None:
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id=template_id
        )
        layout = result.layout
        for slot in result.plan.frames:
            size = layout.itemById(slot.id).sizeWithUnits()
            assert size.width() == pytest.approx(slot.box.w, abs=0.05)
            assert size.height() == pytest.approx(slot.box.h, abs=0.05)
        report = FidelityReportBuilder()
        html = _export(layout, project, runtime, result.frame_modes, report)
        assert not report.has_blockers
        page = re.search(r"<om-cartograph [^>]*>", html).group(0)
        assert f'bg="{result.plan.bg}"' in page
        main = _tag(html, "om-frame", "frame_main")
        (slot,) = [s for s in result.plan.frames if s.id == "main"]
        assert f'frame-bg="{slot.frame_bg}"' in main
        assert f'frame-stroke="{slot.frame_stroke}"' in main
        (planned,) = [i for i in result.plan.items if isinstance(i, LegendSpec)]
        legend = _tag(html, "om-legend", "legend_legend")
        assert f'bg="{planned.bg}"' in legend
        assert f'color="{planned.color}"' in legend
        for item in result.plan.items:
            if isinstance(item, TextSpec) and item.color:
                assert f'color="{item.color}"' in _tag(
                    html, "om-text", f"label_{item.id}"
                )
        # No text the design owns is reported too faint to read.
        assert not [
            row
            for row in report.items
            if row.status != FidelityStatus.PRESERVED and "contrast" in row.detail
        ]

    @pytest.mark.parametrize("template_id", NEW_IDS)
    def test_a_preview_leaves_the_project_alone(
        self, template_id, parcels, project
    ) -> None:
        before = len(project.layoutManager().layouts())
        image = template_preview(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id=template_id,
            width_px=300,
        )
        assert not image.isNull() and image.width() == 300
        assert len(project.layoutManager().layouts()) == before


class TestNightDeskRoundTrip:
    def test_the_dark_sheet_survives_html_and_back(
        self, parcels, project, runtime, tmp_path
    ) -> None:
        """Page, map window, legend, scale bar and text keep Night Desk's
        colours through QGIS -> HTML -> QGIS."""
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="night-desk"
        )
        html = _export(result.layout, project, runtime, result.frame_modes)
        assert f'bg="{AUBERGINE.page}"' in re.search(r"<om-cartograph [^>]*>", html)[0]
        main = _tag(html, "om-frame", "frame_main")
        assert f'frame-bg="{AUBERGINE.panel}"' in main
        assert f'frame-stroke="{AUBERGINE.hairline}"' in main
        legend = _tag(html, "om-legend", "legend_legend")
        assert f'bg="{AUBERGINE.page}"' in legend
        assert f'color="{AUBERGINE.ink}"' in legend
        assert f'color="{AUBERGINE.ink}"' in _tag(
            html, "om-scalebar", "scalebar_scalebar"
        )
        assert f'color="{AUBERGINE.ink}"' in _tag(html, "om-text", "label_title")
        assert f'fill="{AUBERGINE.accent}"' in _tag(html, "om-shape", "shape_accent")

        path = tmp_path / "night-desk.html"
        path.write_text(html)
        built = import_cartograph_file(path, project)
        reading = read_layout(
            built.layout, FidelityReportBuilder(), frame_modes=built.frame_modes
        )
        assert reading.bg == AUBERGINE.page
        (frame,) = reading.frames
        assert (frame.frame_bg, frame.frame_stroke) == (
            AUBERGINE.panel,
            AUBERGINE.hairline,
        )
        (back,) = [i for i in reading.furniture if isinstance(i, LegendSpec)]
        assert (back.bg, back.color) == (AUBERGINE.page, AUBERGINE.ink)
        title = next(
            i
            for i in reading.furniture
            if isinstance(i, TextSpec) and i.kind == "title"
        )
        assert title.color == AUBERGINE.ink


@pytest.fixture
def lots(project):
    """Three parcels with a `lot` field, for an atlas over them."""
    project.setCrs(WEB_MERCATOR)
    project.setTitle("Parcel sheets")
    layer = build_parcels()
    project.addMapLayer(layer)
    return layer


def _ledger(lots, project):
    extent = lots.extent()
    extent.grow(300)
    return cartograph_from_view(
        extent, WEB_MERCATOR, [lots], project, template_id="atlas-ledger"
    )


def _live_main(result) -> dict[str, str]:
    modes = dict(result.frame_modes)
    modes[result.layout.itemById("main").uuid()] = "live"
    return modes


class TestAtlasLedger:
    def test_on_a_single_page_the_sheet_number_is_blank(
        self, lots, project, runtime
    ) -> None:
        result = _ledger(lots, project)
        sheet = result.layout.itemById("sheet")
        assert sheet.currentText() == ""
        image = qgis_core.QgsLayoutExporter(result.layout).renderPageToImage(0)
        assert not image.isNull()
        report = FidelityReportBuilder()
        html = _export(result.layout, project, runtime, _live_main(result), report)
        assert _text_body(html, "label_sheet") == ""
        assert _text_body(html, "label_title") == "{{title}}"
        assert "<om-atlas" not in html
        # Blank by design, not a failed expression in Checks.
        sheet_rows = [row for row in report.items if "'sheet'" in row.subject]
        assert sheet_rows
        assert all(row.status == FidelityStatus.PRESERVED for row in sheet_rows)
        locator = _tag(html, "om-frame", "frame_locator")
        assert 'overview-of="frame_main"' in locator

    def test_on_atlas_pages_the_sheet_number_follows_the_page(
        self, lots, project, runtime
    ) -> None:
        result = _ledger(lots, project)
        layout = result.layout
        main = layout.itemById("main")
        main.setAtlasDriven(True)
        main.setAtlasScalingMode(qgis_core.QgsLayoutItemMap.AtlasScalingMode.Auto)
        atlas = layout.atlas()
        atlas.setCoverageLayer(lots)
        atlas.setEnabled(True)

        # QGIS: each page draws its own number.
        sheet = layout.itemById("sheet")
        exporter = qgis_core.QgsLayoutExporter(layout)
        assert atlas.beginRender()
        try:
            shown = []
            for page in range(atlas.count()):
                atlas.seekTo(page)
                shown.append(sheet.currentText())
                assert not exporter.renderPageToImage(0).isNull()
        finally:
            atlas.endRender()
        assert shown == ["1 / 3", "2 / 3", "3 / 3"]

        # The export: the same number as live runtime tokens, the atlas on.
        report = FidelityReportBuilder()
        html = _export(layout, project, runtime, _live_main(result), report)
        assert "<om-atlas" in html
        assert _text_body(html, "label_sheet") == "{{atlas.index}} / {{atlas.count}}"
        assert _text_body(html, "label_title") == "{{title}}"
        assert 'overview-of="frame_main"' in _tag(html, "om-frame", "frame_locator")
        assert not report.has_blockers


class TestFurnitureMatchesQgis:
    def test_a_shape_without_an_outline_has_none_in_the_html(
        self, parcels, project, runtime
    ) -> None:
        """Night Desk's brass rule and hairline have no outline in QGIS; the
        HTML says so, rather than taking the runtime's 0.3 mm theme outline,
        which turned the 0.8 mm brass rule dark."""
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="night-desk"
        )
        html = _export(result.layout, project, runtime, result.frame_modes)
        for shape_id in ("shape_accent", "shape_rule"):
            tag = _tag(html, "om-shape", shape_id)
            assert 'stroke-width="0"' in tag
            assert " stroke=" not in tag


@pytest.fixture
def land_use(project):
    """The parcels coloured by four recorded uses, under a roads layer: a
    legend of a heading, four classes and a line."""
    project.setTitle("Kranji parcels: land use survey, 2026")
    layer = build_parcels_layer()
    layer.dataProvider().addAttributes([qgis_core.QgsField("use", _string_type())])
    layer.updateFields()
    uses = ("Housing", "Farmland", "Parkland", "Industry")
    layer.startEditing()
    for index, feature in enumerate(layer.getFeatures()):
        layer.changeAttributeValue(feature.id(), 0, uses[index % len(uses)])
    layer.commitChanges()
    layer.setRenderer(
        qgis_core.QgsCategorizedSymbolRenderer(
            "use",
            [
                qgis_core.QgsRendererCategory(
                    use, qgis_core.QgsFillSymbol.createSimple({"color": colour}), use
                )
                for use, colour in zip(
                    uses, ("#d95f02", "#a6c96a", "#1b9e77", "#7570b3")
                )
            ],
        )
    )
    roads = qgis_core.QgsVectorLayer("LineString?crs=EPSG:32648", "Roads", "memory")
    project.addMapLayers([layer, roads])
    return [roads, layer]


def _string_type():
    from qgis.PyQt.QtCore import QMetaType

    return QMetaType.Type.QString


#: Long layer names, as real projects carry them: ~70 characters each.
LONG_NAMES = {
    "Roads": "Trunk and estate roads (LTA road network, July 2026)",
    "parcels": "Agricultural and residential land use parcels, 2026 cadastral survey",
    "Soil": "Soil samples from the September 2026 auger survey, by texture class",
    "Flood": "Modelled flood depth in metres for a 1-in-100-year storm event (PUB)",
    "River": "Sungei Kranji main channel and its western tributaries",
}


def _categorized(name, geometry, field, classes, make):
    layer = qgis_core.QgsVectorLayer(
        f"{geometry}?crs=EPSG:32648&field={field}:string", name, "memory"
    )
    layer.setRenderer(
        qgis_core.QgsCategorizedSymbolRenderer(
            field,
            [
                qgis_core.QgsRendererCategory(value, make(colour), value)
                for value, colour in classes
            ],
        )
    )
    return layer


@pytest.fixture(params=["land-use", "long-names", "many-rows"])
def legend_layers(request, land_use, project):
    """The land-use legend (a heading, four classes and a line), the same
    with long layer names, and a long legend: 23 rows over six layers."""
    layers = list(land_use)
    if request.param == "many-rows":
        soil = _categorized(
            "Soil",
            "Point",
            "soil",
            [
                ("Clay", "#8c510a"),
                ("Silty clay", "#bf812d"),
                ("Loam", "#dfc27d"),
                ("Sandy loam", "#f6e8c3"),
                ("Sand", "#c7eae5"),
                ("Peat", "#35978f"),
                ("Fill", "#01665e"),
                ("Laterite", "#543005"),
            ],
            lambda c: qgis_core.QgsMarkerSymbol.createSimple({"color": c}),
        )
        flood = _categorized(
            "Flood",
            "Polygon",
            "depth",
            [
                (f"{k}-{k + 1} m", c)
                for k, c in enumerate(
                    ("#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c")
                )
            ],
            lambda c: qgis_core.QgsFillSymbol.createSimple({"color": c}),
        )
        river = qgis_core.QgsVectorLayer("LineString?crs=EPSG:32648", "River", "memory")
        districts = qgis_core.QgsVectorLayer(
            "Polygon?crs=EPSG:32648", "Planning areas", "memory"
        )
        layers = [soil, layers[0], river, flood, layers[1], districts]
        project.addMapLayers([soil, river, flood, districts])
    if request.param == "long-names":
        for layer in layers:
            layer.setName(LONG_NAMES.get(layer.name(), layer.name()))
    return layers


class TestLegendsArePlannedToFit:
    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize(
        ("size", "orientation"),
        [
            ("A4", "portrait"),
            ("A4", "landscape"),
            ("A5", "portrait"),
            ("A5", "landscape"),
            ("A3", "landscape"),
            ("A1", "portrait"),
        ],
    )
    def test_qgis_draws_the_legend_at_the_planned_size(
        self, template_id, size, orientation, legend_layers, land_use, project
    ) -> None:
        """Every design that has a legend plans a box that holds every row
        at the size it chose -- long rows wrapped, never below the minimum
        legend size -- so QGIS never has to shrink it (`_fit_legend`)."""
        extent = land_use[1].extent()
        extent.grow(200)
        result = cartograph_from_view(
            extent,
            UTM,
            legend_layers,
            project,
            template_id=template_id,
            page_size=size,
            orientation=orientation,
        )
        planned = [i for i in result.plan.items if isinstance(i, LegendSpec)]
        if not planned:
            assert template_id in ("map-only", "report-figure", "story-map")
            return
        (planned,) = planned
        assert planned.font_size >= MINIMUM_TYPE.legend
        legend = result.layout.itemById("legend")
        label = legend.rstyle(qgis_core.QgsLegendStyle.SymbolLabel).textFormat()
        assert label.size() == pytest.approx(planned.font_size)
        assert legend.legendSettings().autoWrapLinesAfter() == pytest.approx(
            planned.wrap_width
        )


class TestHeadingsWrap:
    def test_a_heading_broken_onto_lines_for_qgis_3_is_no_rename(
        self, land_use, project, runtime
    ) -> None:
        """QGIS 3.44 wraps a legend's rows at the planned width but not the
        heading over a class list, so the template breaks that heading onto
        lines itself (`_wrap_headings`); the export still names the layer
        as it is, and the heading fits the column in QGIS."""
        land_use[1].setName(LONG_NAMES["parcels"])
        extent = land_use[1].extent()
        extent.grow(200)
        result = cartograph_from_view(
            extent, UTM, land_use, project, template_id="classic", page_size="A5"
        )
        legend = result.layout.itemById("legend")
        _wrap_headings(legend, 30.0)
        (node,) = [
            n
            for n in legend.model().rootGroup().findLayers()
            if n.layerId() == land_use[1].id()
        ]
        assert "\n" in node.customProperty("legend/title-label")
        assert (
            " ".join(node.customProperty("legend/title-label").split())
            == (LONG_NAMES["parcels"])
        )
        context = qgis_core.QgsLayoutUtils.createRenderContextForLayout(
            result.layout, None
        )
        heading_only = qgis_core.QgsLegendRenderer(
            legend.model(), legend.legendSettings()
        ).minimumSize(context)
        assert heading_only.width() <= 30.0 + 7.0 + 2.0 + 4.0 + 1.0
        html = _export(result.layout, project, runtime, _live_main(result))
        markup = re.search(r"<om-legend .*?</om-legend>", html, re.DOTALL).group(0)
        assert "label=" not in markup


@pytest.fixture
def crowded(land_use, project):
    """The stress legend: long names over six layers, 23 rows -- more than
    any design holds at a readable size on A5."""
    soil = _categorized(
        LONG_NAMES["Soil"],
        "Point",
        "soil",
        [
            ("Clay", "#8c510a"),
            ("Silty clay", "#bf812d"),
            ("Loam", "#dfc27d"),
            ("Sandy loam", "#f6e8c3"),
            ("Sand", "#c7eae5"),
            ("Peat", "#35978f"),
            ("Fill", "#01665e"),
            ("Laterite", "#543005"),
        ],
        lambda c: qgis_core.QgsMarkerSymbol.createSimple({"color": c}),
    )
    flood = _categorized(
        LONG_NAMES["Flood"],
        "Polygon",
        "depth",
        [
            (f"{k}-{k + 1} m", c)
            for k, c in enumerate(
                ("#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c")
            )
        ],
        lambda c: qgis_core.QgsFillSymbol.createSimple({"color": c}),
    )
    river = qgis_core.QgsVectorLayer(
        "LineString?crs=EPSG:32648", LONG_NAMES["River"], "memory"
    )
    districts = qgis_core.QgsVectorLayer(
        "Polygon?crs=EPSG:32648",
        "Planning areas and subzones of the Western Water Catchment (URA 2019)",
        "memory",
    )
    roads, parcels = land_use
    roads.setName(LONG_NAMES["Roads"])
    parcels.setName(LONG_NAMES["parcels"])
    project.addMapLayers([soil, river, flood, districts])
    return [soil, roads, river, flood, parcels, districts]


def _crowded_view(crowded):
    extent = crowded[4].extent()
    extent.grow(200)
    return extent


def _legend_fits(legend) -> bool:
    context = qgis_core.QgsLayoutUtils.createRenderContextForLayout(
        legend.layout(), None
    )
    need = qgis_core.QgsLegendRenderer(
        legend.model(), legend.legendSettings()
    ).minimumSize(context)
    box = legend.sizeWithUnits()
    return need.width() <= box.width() + 0.01 and need.height() <= box.height() + 0.01


def _left_out_rows(report) -> list[str]:
    return [item.detail for item in report.items if "leaves out" in item.detail]


class TestCrowdedLegends:
    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize("orientation", ["portrait", "landscape"])
    def test_never_below_the_floor_and_never_clipped(
        self, template_id, orientation, crowded, project
    ) -> None:
        """A5 and the stress legend: QGIS draws every legend row at the
        design's floor or larger (6.5 pt; Town Hall's 9 pt), every row it
        draws fits the box, and what does not fit is left out from the end
        of the list -- counted on the result and marked on the legend, a
        class list never cut to the one row QGIS would label with the
        layer's name."""
        result = cartograph_from_view(
            _crowded_view(crowded),
            UTM,
            crowded,
            project,
            template_id=template_id,
            page_size="A5",
            orientation=orientation,
        )
        legend = result.layout.itemById("legend")
        if legend is None:
            assert template_id in ("map-only", "report-figure", "story-map")
            return
        label = legend.rstyle(qgis_core.QgsLegendStyle.SymbolLabel).textFormat()
        assert label.size() >= result.plan.legend_min_pt - 1e-6
        if template_id == "town-hall":
            assert label.size() >= 9.0 - 1e-6
        assert _legend_fits(legend)
        marked = legend.customProperty(LEGEND_LEFT_OUT_PROPERTY)
        assert int(marked or 0) == result.legend_left_out
        model = legend.model()
        nodes = model.rootGroup().findLayers()
        shown = sum(max(len(model.layerLegendNodes(node)), 1) for node in nodes)
        total = 8 + 1 + 1 + 5 + 4 + 1
        assert shown + result.legend_left_out == total
        # Whole layers are kept from the top of the list.
        names = [node.name() for node in nodes]
        assert names == [layer.name() for layer in crowded[: len(names)]]
        for node in nodes:
            if len(model.layerLegendNodes(node)) == 1:
                layer = node.layer()
                assert len(layer.renderer().legendSymbolItems()) <= 1, node.name()

    def test_the_checks_say_what_the_legend_left_out(
        self, crowded, project, runtime
    ) -> None:
        """The export's checks name the count and the remedy -- and stop
        once the user has put the entries back."""
        result = cartograph_from_view(
            _crowded_view(crowded),
            UTM,
            crowded,
            project,
            template_id="town-hall",
            page_size="A5",
            orientation="landscape",
        )
        assert result.legend_left_out > 0
        report = FidelityReportBuilder()
        _export(result.layout, project, runtime, _live_main(result), report)
        (detail,) = _left_out_rows(report)
        assert f"leaves out {result.legend_left_out} entries" in detail
        assert "larger page" in detail and "shorten the layer names" in detail
        legend = result.layout.itemById("legend")
        root = legend.model().rootGroup()
        root.removeAllChildren()
        for layer in crowded:
            root.addLayer(layer)
        report = FidelityReportBuilder()
        _export(result.layout, project, runtime, _live_main(result), report)
        assert _left_out_rows(report) == []

    def test_a_legend_that_fits_reports_nothing(
        self, land_use, project, runtime
    ) -> None:
        extent = land_use[1].extent()
        extent.grow(200)
        result = cartograph_from_view(
            extent, UTM, land_use, project, template_id="classic", page_size="A4"
        )
        assert result.legend_left_out == 0
        legend = result.layout.itemById("legend")
        assert legend.customProperty(LEGEND_LEFT_OUT_PROPERTY) is None
        report = FidelityReportBuilder()
        _export(result.layout, project, runtime, _live_main(result), report)
        assert _left_out_rows(report) == []

    def test_the_preview_carries_the_count(self, crowded, project) -> None:
        """The picker's thumbnail says how many entries its page leaves
        out, from the same build (`PREVIEW_LEGEND_LEFT_OUT_KEY`)."""
        args = (_crowded_view(crowded), UTM, crowded, project)
        kwargs = {"page_size": "A5", "orientation": "landscape"}
        for template_id in ("classic", "town-hall", "map-only"):
            image = template_preview(
                *args, template_id=template_id, width_px=200, **kwargs
            )
            built = cartograph_from_view(*args, template_id=template_id, **kwargs)
            assert image.text(PREVIEW_LEGEND_LEFT_OUT_KEY) == str(built.legend_left_out)
        assert built.legend_left_out == 0  # map-only has no legend

    @pytest.mark.parametrize(
        "template_id", ["field-map", "town-hall", "night-desk", "dashboard"]
    )
    def test_automatic_orientation_turns_the_sheet_for_the_legend(
        self, template_id, crowded, project
    ) -> None:
        """ "auto" takes the orientation that shows the map largest unless
        its legend leaves out more than the other's would: it never leaves
        out more than the better of the two, and leaves one layout in the
        project."""
        args = (_crowded_view(crowded), UTM, crowded, project)
        left = {
            orientation: cartograph_from_view(
                *args,
                template_id=template_id,
                page_size="A5",
                orientation=orientation,
            ).legend_left_out
            for orientation in ("portrait", "landscape")
        }
        before = len(project.layoutManager().layouts())
        auto = cartograph_from_view(
            *args, template_id=template_id, page_size="A5", orientation="auto"
        )
        assert auto.legend_left_out == min(left.values())
        assert len(project.layoutManager().layouts()) == before + 1
        assert project.layoutManager().layoutByName(auto.layout.name()) is not None


class TestWayfinderFillsTheSheet:
    @pytest.mark.parametrize("size", ["A5", "A4", "Letter", "A1"])
    @pytest.mark.parametrize("variant", ["land-use", "crowded"])
    def test_no_empty_band_across_a_landscape_sheet(
        self, size, variant, land_use, request, project
    ) -> None:
        """Rendered by QGIS, a landscape Wayfinder of a wide view has no
        empty stretch across the page, inside its margins, taller than
        10 % of the page: the band grows (to 1.8 : 1) and the source line
        keeps the foot. (The 10 % allows the room a legend's box keeps
        under its rows.)"""
        layers = (
            request.getfixturevalue("crowded") if variant == "crowded" else land_use
        )
        extent = layers[-2].extent() if variant == "crowded" else land_use[1].extent()
        extent.grow(200)
        wide = qgis_core.QgsRectangle(
            extent.center().x() - 2 * extent.width(),
            extent.center().y() - extent.height() / 2,
            extent.center().x() + 2 * extent.width(),
            extent.center().y() + extent.height() / 2,
        )
        image = template_preview(
            wide,
            UTM,
            layers,
            project,
            template_id="wayfinder",
            width_px=420,
            page_size=size,
            orientation="landscape",
        ).convertToFormat(_rgb32())
        assert image.width() > image.height()
        width_mm, height_mm = page_dimensions(size, "landscape")
        margin = round(
            spacing_for(width_mm, height_mm).margin / height_mm * image.height()
        )
        background = image.pixel(1, 1)
        run = gap = 0
        for y in range(margin, image.height() - margin):
            empty = all(
                image.pixel(x, y) == background
                for x in range(margin, image.width() - margin)
            )
            run = run + 1 if empty else 0
            gap = max(gap, run)
        assert gap / image.height() <= 0.10, (gap, image.height())


def _rgb32():
    from qgis.PyQt.QtGui import QImage

    return QImage.Format.Format_RGB32
