"""`core/layout_builder.py`: a `CartographSpec` back into a `QgsPrintLayout`.

The proof is the round trip: the survey layout is read and prepared into a
spec, built into a new layout, and read again -- and the two specs agree.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import fields, replace

import pytest
from cartograph_fixtures import all_static, build_survey_layout

from nika_onlymap_exporter.core.cartograph_export import (
    _camera_for_extent,
    _frame_extent_wgs84,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_import import (
    CartographImportError,
    parse_cartograph_html,
)
from nika_onlymap_exporter.core.cartograph_ir import (
    AtlasSpec,
    Box,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    ImageSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.expression_tokens import translate_label
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_builder import (
    build_layout,
    import_cartograph_file,
    label_text_from_tokens,
    live_frame_extent,
    unique_layout_name,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle
from nika_onlymap_exporter.writers.cartograph_writer import (
    CartographRuntimeBundle,
    write_cartograph_html,
)

qgis_core = pytest.importorskip("qgis.core")

_BOX_TOLERANCE_MM = 0.01
_DEGREE_TOLERANCE = 1e-7

_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    b'<rect width="8" height="8"/></svg>'
)


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


@pytest.fixture
def survey(project, tmp_path):
    return build_survey_layout(project, tmp_path)


def _prepare(layout, project, runtime, frame_modes=None) -> CartographSpec:
    return prepare_cartograph(
        layout, project, runtime, FidelityReportBuilder(), frame_modes=frame_modes
    ).spec


def _canonical_ids(spec: CartographSpec, layout) -> dict[str, str]:
    """Spec id -> a name stable across the round trip.

    The reader names items after their uuid, which a rebuilt layout does not
    share; the builder sets each item's QGIS id to the original spec id, so a
    rebuilt item's `id()` *is* the original name. An original item (no QGIS
    id) keeps its spec id.
    """
    by_uuid = {item.uuid(): item for item in layout.items() if hasattr(item, "uuid")}
    names: dict[str, str] = {}
    for item in spec.items:
        _prefix, _, uuid = item.id.partition("-")
        if isinstance(item, GraticuleSpec):
            continue
        layout_item = by_uuid.get(uuid)
        names[item.id] = (
            layout_item.id() if layout_item is not None else ""
        ) or item.id
    for item in spec.items:
        if isinstance(item, GraticuleSpec):
            names[item.id] = "graticule-of-" + names[item.for_]
    return names


def _canonical(spec: CartographSpec, layout) -> dict[str, object]:
    names = _canonical_ids(spec, layout)

    def rename(value: object) -> object:
        if isinstance(value, str):
            for old, new in names.items():
                value = value.replace(old, new)
        return value

    out: dict[str, object] = {}
    for item in spec.items:
        values = {
            f.name: rename(getattr(item, f.name))
            for f in fields(item)
            if f.name not in ("id", "image_bytes", "crs_def")
        }
        out[names[item.id]] = (type(item).__name__, values)
    return out


def _assert_equivalent(a: dict[str, object], b: dict[str, object]) -> None:
    assert a.keys() == b.keys()
    for key in a:
        kind_a, values_a = a[key]  # type: ignore[misc]
        kind_b, values_b = b[key]  # type: ignore[misc]
        assert kind_a == kind_b, key
        for name, value_a in values_a.items():
            value_b = values_b[name]
            if name == "box":
                for axis in "xywh":
                    assert getattr(value_b, axis) == pytest.approx(
                        getattr(value_a, axis), abs=_BOX_TOLERANCE_MM
                    ), (key, axis)
            elif name == "corners" and value_a is not None:
                for (lon_a, lat_a), (lon_b, lat_b) in zip(value_a, value_b):
                    assert lon_b == pytest.approx(lon_a, abs=_DEGREE_TOLERANCE), key
                    assert lat_b == pytest.approx(lat_a, abs=_DEGREE_TOLERANCE), key
            elif name == "center" and value_a is not None:
                assert value_b == pytest.approx(value_a, abs=1e-6), key
            elif isinstance(value_a, float):
                assert value_b == pytest.approx(value_a, abs=1e-6), (key, name)
            else:
                assert value_b == value_a, (key, name)


def _frame_id(layout, map_item) -> str:
    """The id the reader gives `map_item` within `layout`."""
    from qgis.core import QgsLayoutItem

    from nika_onlymap_exporter.core.cartograph_ir import element_id, item_id_tokens

    tokens = item_id_tokens(
        [(i.uuid(), i.id()) for i in layout.items() if isinstance(i, QgsLayoutItem)]
    )
    return element_id("frame", map_item.uuid(), tokens)


class TestSurveyRoundTrip:
    def test_read_build_read_gives_an_equivalent_spec(
        self, survey, project, runtime
    ) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project, name="round trip")
        again = _prepare(built.layout, project, runtime, built.frame_modes)

        _assert_equivalent(
            _canonical(spec, survey.layout), _canonical(again, built.layout)
        )

    def test_only_the_static_legend_rows_are_reported(
        self, survey, project, runtime
    ) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project)
        assert len(built.issues) == 1
        assert built.issues[0].subject.startswith("<om-legend")

    def test_item_ids_are_the_spec_ids(self, survey, project, runtime) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project)
        placed = {item.id for item in spec.items if not isinstance(item, GraticuleSpec)}
        assert placed == set(built.items_by_id)
        for spec_id, item in built.items_by_id.items():
            assert item.id() == spec_id

    def test_grid_and_overview_and_legend_links(self, survey, project, runtime) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project)
        main_id = next(
            i.id
            for i in spec.items
            if isinstance(i, FrameSpec) and i.overview_of is None
        )
        inset_id = next(
            i.id for i in spec.items if isinstance(i, FrameSpec) and i.overview_of
        )
        main = built.items_by_id[main_id]
        inset = built.items_by_id[inset_id]
        grid = main.grids().asList()[0]
        assert (grid.intervalX(), grid.intervalY()) == (500, 500)
        assert inset.overviews().asList()[0].linkedMap() is main
        legend_id = next(i.id for i in spec.items if isinstance(i, LegendSpec))
        assert built.items_by_id[legend_id].linkedMap() is main
        assert built.layout.referenceMap() is main

    def test_frame_crs_and_extent_match(self, survey, project, runtime) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project)
        for original in (survey.main_map, survey.inset_map):
            rebuilt = built.items_by_id[_frame_id(survey.layout, original)]
            assert rebuilt.crs().authid() == original.crs().authid()
            a, b = original.extent(), rebuilt.extent()
            for getter in ("xMinimum", "yMinimum", "xMaximum", "yMaximum"):
                assert getattr(b, getter)() == pytest.approx(
                    getattr(a, getter)(), abs=0.01
                )

    def test_frame_modes_survive_for_reexport(self, survey, project, runtime) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        spec = replace(
            spec,
            items=tuple(
                replace(i, dpi=300.0) if isinstance(i, FrameSpec) else i
                for i in spec.items
            ),
        )
        built = build_layout(spec, project)
        assert set(built.frame_modes.values()) == {"static"}
        assert set(built.frame_dpis.values()) == {300}

    def test_rotated_static_frame_round_trips(self, survey, project, runtime) -> None:
        survey.main_map.setMapRotation(30)
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        built = build_layout(spec, project)
        again = _prepare(built.layout, project, runtime, built.frame_modes)
        _assert_equivalent(
            _canonical(spec, survey.layout), _canonical(again, built.layout)
        )
        rebuilt = built.items_by_id[_frame_id(survey.layout, survey.main_map)]
        assert rebuilt.mapRotation() == pytest.approx(30)

    def test_through_the_html_file(self, survey, project, runtime) -> None:
        """Writer -> importer -> builder ends where the spec alone does."""
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        html = write_cartograph_html(
            spec,
            CartographRuntimeBundle(cartograph_js=b"//", cartograph_css="/**/"),
        )
        imported = parse_cartograph_html(html)
        assert imported.issues == ()
        built = build_layout(imported.spec, project)
        again = _prepare(built.layout, project, runtime, built.frame_modes)
        _assert_equivalent(
            _canonical(spec, survey.layout), _canonical(again, built.layout)
        )


def _live_project(project):
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsRectangle,
        QgsVectorLayer,
    )

    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857&field=lot:string", "lots", "memory")
    features = []
    for i, lot in enumerate(("A", "B", "C")):
        feature = QgsFeature(layer.fields())
        feature.setAttribute(0, lot)
        x = 11_550_000 + i * 2000
        feature.setGeometry(
            QgsGeometry.fromRect(QgsRectangle(x, 140_000, x + 1500, 141_500))
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    project.addMapLayer(layer)
    return layer


def _live_spec() -> CartographSpec:
    return CartographSpec(
        cartograph_id="live",
        title="Lots",
        size="A4",
        orientation="landscape",
        items=(
            FrameSpec(
                id="main",
                box=Box(10, 20, 200, 120),
                center=(103.79, 1.27),
                zoom=13.5,
                bearing=0.0,
            ),
        ),
    )


class TestLiveFrames:
    def test_camera_round_trips(self, project, runtime) -> None:
        _live_project(project)
        spec = _live_spec()
        built = build_layout(spec, project)
        again = _prepare(built.layout, project, runtime, built.frame_modes)
        frame = next(i for i in again.items if isinstance(i, FrameSpec))
        assert frame.mode == "live"
        assert frame.zoom == pytest.approx(13.5, abs=1e-6)
        assert frame.center == pytest.approx((103.79, 1.27), abs=1e-6)
        assert frame.box == Box(10, 20, 200, 120)

    @pytest.mark.parametrize(
        ("center", "zoom"), [((0.0, 0.0), 2.0), ((-73.9, 40.7), 11.0), ((150, -33), 6)]
    )
    def test_extent_is_the_inverse_of_the_export_camera(
        self, project, center, zoom
    ) -> None:
        """`_camera_for_extent` gives back the zoom and longitude exactly.

        Its latitude is the degree midpoint, not the Mercator centre the
        runtime uses, so it only agrees to the extent's latitude span.
        """
        from qgis.core import QgsLayoutItemMap, QgsPrintLayout

        box = Box(0, 0, 150, 100)
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
        map_item.attemptResize(
            qgis_core.QgsLayoutSize(150, 100, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        map_item.setExtent(live_frame_extent(center, zoom, box))
        extent = _frame_extent_wgs84(map_item, project)
        (lon, lat), got_zoom = _camera_for_extent(extent, box.w)
        assert got_zoom == pytest.approx(zoom, abs=1e-7)
        assert lon == pytest.approx(center[0], abs=1e-7)
        assert lat == pytest.approx(center[1], abs=(extent.north - extent.south) / 50)

    def test_frame_without_camera_falls_back_to_layer_extent(self, project) -> None:
        layer = _live_project(project)
        spec = replace(
            _live_spec(),
            items=(FrameSpec(id="main", box=Box(10, 20, 200, 120)),),
        )
        built = build_layout(spec, project)
        extent = built.items_by_id["main"].extent()
        assert all(
            math.isfinite(v)
            for v in (extent.xMinimum(), extent.yMinimum(), extent.width())
        )
        assert extent.contains(layer.extent())
        assert any("camera" in issue.detail for issue in built.issues)

    def test_frame_layers_lock_the_layer_set(self, project) -> None:
        layer = _live_project(project)
        built = build_layout(_live_spec(), project, frame_layers={"main": [layer]})
        map_item = built.items_by_id["main"]
        assert map_item.keepLayerSet()
        assert [each.id() for each in map_item.layers()] == [layer.id()]

    def test_pitch_and_src_are_reported(self, project) -> None:
        _live_project(project)
        frame = replace(_live_spec().items[0], pitch=20.0, src="other.html")
        built = build_layout(replace(_live_spec(), items=(frame,)), project)
        details = " ".join(issue.detail for issue in built.issues)
        assert "pitch" in details and "other.html" in details


def _furniture_spec(**atlas_kw) -> CartographSpec:
    svg_uri = "data:image/svg+xml;base64," + base64.b64encode(_SVG).decode()
    return CartographSpec(
        cartograph_id="furniture",
        title="Lots",
        attribution="© NIKA",
        size="A3",
        orientation="portrait",
        items=(
            FrameSpec(
                id="main", box=Box(10, 20, 200, 120), center=(103.8, 1.27), zoom=13
            ),
            FrameSpec(
                id="inset",
                box=Box(220, 20, 60, 60),
                center=(103.8, 1.27),
                zoom=8,
                overview_of="main",
                overview_stroke="#ff0000",
            ),
            GraticuleSpec(
                id="grid",
                box=Box(10, 20, 200, 120),
                for_="main",
                interval=0.01,
                kind="cross",
                labels="inside",
                format="dms",
            ),
            LegendSpec(
                id="legend",
                box=Box(220, 90, 60, 40),
                for_="main",
                title="Key",
                columns=2,
                bg="#fafafa",
                stroke="#333333",
            ),
            ScalebarSpec(
                id="bar",
                box=Box(10, 150, 60, 10),
                for_="main",
                segments=3,
                segment_length=2000.0,
                kind="double",
            ),
            NorthSpec(id="north", box=Box(190, 150, 12, 14), for_="main", kind="arrow"),
            NorthSpec(id="rose", box=Box(205, 150, 12, 14), for_="inset", kind="rose"),
            TextSpec(
                id="title",
                box=Box(10, 5, 200, 12),
                body=(
                    "{{title}} 1:{{frame.main.scale}} ({{frame.inset.crs}}) "
                    "{{date}} {{bogus}}"
                ),
                kind="title",
                font_size=18,
                weight="bold",
                align="center",
                color="#123456",
                z=9,
                rotation=15.0,
            ),
            TextSpec(
                id="credit",
                box=Box(10, 170, 200, 6),
                body="{{attribution}}",
                kind="attribution",
                hidden=True,
                locked=True,
            ),
            ImageSpec(id="logo", box=Box(250, 150, 20, 20), src=svg_uri),
            ShapeSpec(
                id="box",
                box=Box(10, 180, 30, 10),
                fill="#ff0000",
                fill_opacity=0.5,
                stroke="#000000",
                stroke_width=0.5,
                radius=2,
            ),
            ShapeSpec(id="ring", box=Box(50, 180, 30, 10), kind="ellipse"),
            ShapeSpec(
                id="pointer",
                box=Box(90, 180, 30, 10),
                kind="arrow",
                stroke="#00ff00",
                stroke_width=1,
            ),
        ),
        atlas=AtlasSpec(for_="main", **atlas_kw) if atlas_kw else None,
    )


class TestFurniture:
    def test_page_size_from_preset(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        page = built.layout.pageCollection().page(0).pageSize()
        assert (page.width(), page.height()) == (297, 420)
        assert built.layout.pageCollection().pageCount() == 1

    def test_every_placed_box_is_the_spec_box(self, project) -> None:
        spec = _furniture_spec()
        built = build_layout(spec, project)
        # A refresh is what opening the layout designer does; nothing may
        # resize itself to its content afterwards (a legend would).
        built.layout.refresh()
        for item in spec.items:
            if isinstance(item, GraticuleSpec) or item.rotation:
                continue
            layout_item = built.items_by_id[item.id]
            pos, size = layout_item.positionWithUnits(), layout_item.sizeWithUnits()
            got = (pos.x(), pos.y(), size.width(), size.height())
            if isinstance(item, ScalebarSpec):
                got = got[:2]  # QGIS sizes a bar to its segments (reported)
                want = (item.box.x, item.box.y)
            else:
                want = (item.box.x, item.box.y, item.box.w, item.box.h)
            assert got == pytest.approx(want, abs=_BOX_TOLERANCE_MM), item.id

    def test_rotation_turns_about_the_box_centre(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        label = built.items_by_id["title"]
        assert label.itemRotation() == pytest.approx(15)
        centre = label.mapToScene(label.rect().center())
        assert (centre.x(), centre.y()) == pytest.approx((110, 11), abs=1e-6)

    def test_z_lock_and_visibility(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        assert built.items_by_id["title"].zValue() == 9
        assert built.items_by_id["main"].zValue() == 1
        credit = built.items_by_id["credit"]
        assert credit.isLocked() and not credit.isVisible()

    def test_graticule_mapping(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        grid = built.items_by_id["main"].grids().asList()[0]
        grid_class = qgis_core.QgsLayoutItemMapGrid
        assert grid.name() == "grid"
        assert grid.style() == grid_class.Cross
        assert grid.crs().authid() == "EPSG:4326"
        assert (grid.intervalX(), grid.intervalY()) == (0.01, 0.01)
        assert grid.annotationEnabled()
        assert grid.annotationPosition(grid_class.Left) == grid_class.InsideMapFrame
        assert grid.annotationFormat() == grid_class.DegreeMinuteSecond

    def test_overview_and_scalebar(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        overview = built.items_by_id["inset"].overviews().asList()[0]
        assert overview.linkedMap() is built.items_by_id["main"]
        assert overview.frameSymbol().symbolLayer(0).strokeColor().name() == "#ff0000"
        bar = built.items_by_id["bar"]
        assert bar.style() == "Double Box"
        assert bar.units() == qgis_core.QgsUnitTypes.DistanceKilometers
        assert bar.unitsPerSegment() == pytest.approx(2.0)
        assert bar.numberOfSegments() == 3
        assert bar.linkedMap() is built.items_by_id["main"]

    def test_legend(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        legend = built.items_by_id["legend"]
        assert legend.linkedMap() is built.items_by_id["main"]
        assert legend.title() == "Key" and legend.columnCount() == 2
        assert legend.hasBackground() and legend.backgroundColor().name() == "#fafafa"
        assert legend.frameEnabled()

    def test_north_arrows_use_qgis_svgs(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        north, rose = built.items_by_id["north"], built.items_by_id["rose"]
        assert north.picturePath().endswith("layout_default_north_arrow.svg")
        assert rose.picturePath().endswith("NorthArrow_05.svg")
        for picture, frame in ((north, "main"), (rose, "inset")):
            assert not picture.isMissingImage()
            assert picture.linkedMap() is built.items_by_id[frame]
            assert picture.northMode() == qgis_core.QgsLayoutItemPicture.GridNorth

    def test_label_tokens_become_expressions(self, project) -> None:
        project.setTitle("Lots")
        built = build_layout(_furniture_spec(), project)
        label = built.items_by_id["title"]
        assert label.text() == (
            "[% @project_title %] 1:[% round(map_get(item_variables('main'), "
            "'map_scale')) %] "
            "([% map_get(item_variables('inset'), 'map_crs') %]) "
            "[% format_date(now(),'yyyy-MM-dd') %] {{bogus}}"
        )
        assert built.layout.referenceMap() is built.items_by_id["main"]
        assert any("{{bogus}}" in issue.detail for issue in built.issues)
        text_format = label.textFormat()
        assert text_format.size() == 18 and text_format.font().bold()
        assert text_format.color().name() == "#123456"
        assert built.items_by_id["credit"].text() == "© NIKA"

    def test_label_evaluates_in_qgis(self, project) -> None:
        project.setTitle("Lots")
        built = build_layout(_furniture_spec(), project)
        label = built.items_by_id["title"]
        text = label.currentText()
        # The scale is a number in QGIS itself, not blank: a bare
        # `@map_scale` is not in a label's context, the named map's is.
        assert re.match(r"Lots 1:\d+ ", text), text
        assert "EPSG:3857" in text

    def test_title_differing_from_the_project_is_written_as_text(self, project) -> None:
        project.setTitle("Something else")
        built = build_layout(_furniture_spec(), project)
        assert built.items_by_id["title"].text().startswith("Lots 1:")
        assert any("project title" in issue.detail for issue in built.issues)

    def test_embedded_image(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        logo = built.items_by_id["logo"]
        assert logo.picturePath().startswith("base64:")
        assert not logo.isMissingImage()

    def test_image_written_to_image_dir(self, project, tmp_path) -> None:
        built = build_layout(_furniture_spec(), project, image_dir=tmp_path / "img")
        logo = built.items_by_id["logo"]
        assert logo.picturePath() == str(tmp_path / "img" / "logo.svg")
        assert (tmp_path / "img" / "logo.svg").read_bytes() == _SVG
        assert not logo.isMissingImage()

    def test_relative_image_resolves_against_source_dir(
        self, project, tmp_path
    ) -> None:
        (tmp_path / "logo.svg").write_bytes(_SVG)
        spec = replace(
            _furniture_spec(),
            items=(ImageSpec(id="logo", box=Box(1, 1, 5, 5), src="logo.svg"),),
        )
        built = build_layout(spec, project, source_dir=tmp_path)
        assert built.items_by_id["logo"].picturePath() == str(tmp_path / "logo.svg")
        missing = build_layout(spec, project)
        assert "logo" not in missing.items_by_id
        assert missing.issues

    def test_shapes(self, project) -> None:
        built = build_layout(_furniture_spec(), project)
        rect, ring = built.items_by_id["box"], built.items_by_id["ring"]
        shape_class = qgis_core.QgsLayoutItemShape
        assert rect.shapeType() == shape_class.Rectangle
        assert ring.shapeType() == shape_class.Ellipse
        fill = rect.symbol().symbolLayer(0)
        assert fill.color().name() == "#ff0000"
        assert fill.color().alphaF() == pytest.approx(0.5, abs=0.01)
        assert fill.strokeWidth() == pytest.approx(0.5)
        assert rect.cornerRadius().length() == pytest.approx(2)
        arrow = built.items_by_id["pointer"]
        assert isinstance(arrow, qgis_core.QgsLayoutItemPolyline)
        assert arrow.endMarker() == qgis_core.QgsLayoutItemPolyline.ArrowHead
        assert arrow.symbol().color().name() == "#00ff00"

    def test_atlas_with_its_layer(self, project) -> None:
        _live_project(project)
        built = build_layout(
            _furniture_spec(
                layer="lots",
                filter="\"lot\" <> 'B'",
                sort="-lot",
                filename="lot-{{atlas.lot}}",
                page_name="{{atlas.lot}}",
            ),
            project,
        )
        atlas = built.layout.atlas()
        assert atlas.enabled() and atlas.coverageLayer().name() == "lots"
        assert atlas.filterFeatures() and atlas.filterExpression() == "\"lot\" <> 'B'"
        assert atlas.sortFeatures() and not atlas.sortAscending()
        assert atlas.sortExpression() == '"lot"'
        assert (
            atlas.filenameExpression() == "'lot-' || attribute(@atlas_feature, 'lot')"
        )
        assert built.items_by_id["main"].atlasDriven()
        assert atlas.updateFeatures() == 2

    def test_atlas_without_its_layer_is_reported(self, project) -> None:
        built = build_layout(_furniture_spec(layer="nowhere"), project)
        assert not built.layout.atlas().enabled()
        assert any("nowhere" in issue.detail for issue in built.issues)


class TestNames:
    def test_unique_layout_name(self, project) -> None:
        assert unique_layout_name(project, "carto: map") == "carto: map"
        build_layout(_live_spec(), project, name="carto: map", add_to_project=True)
        assert unique_layout_name(project, "carto: map") == "carto: map (2)"

    def test_add_to_project_uses_a_unique_name(self, project) -> None:
        first = build_layout(_live_spec(), project, name="x", add_to_project=True)
        second = build_layout(_live_spec(), project, name="x", add_to_project=True)
        assert first.layout.name() == "x"
        assert second.layout.name() == "x (2)"
        assert project.layoutManager().layoutByName("x (2)") is second.layout


class TestTokens:
    @pytest.mark.parametrize(
        "body",
        [
            "1:{{frame.main.scale}}",
            "{{frame.main.crs}}",
            "{{title}} -- {{date}}",
            "Lot {{atlas.lot}} ({{atlas.index}} of {{atlas.count}})",
        ],
    )
    def test_reader_translates_the_label_back(self, body: str) -> None:
        """The reader's `translate_label` turns our label text back into the
        same tokens: the two functions are inverses on every token."""
        text, unmapped = label_text_from_tokens(
            body,
            reference_frame_id="main",
            frame_ids=["main"],
            title="T",
            title_is_project_title=True,
            attribution="",
            notes="",
        )
        assert unmapped == ()
        translated = translate_label(
            text, frame_id="main", frame_is_live=True, in_atlas=True
        )
        assert translated.frozen_expressions == ()
        assert translated.template == body

    def test_unknown_frame_is_left_as_text(self) -> None:
        text, unmapped = label_text_from_tokens(
            "{{frame.gone.scale}}",
            reference_frame_id="main",
            frame_ids=["main"],
            title="",
            title_is_project_title=True,
            attribution="",
            notes="",
        )
        assert text == "{{frame.gone.scale}}" and unmapped == ("{{frame.gone.scale}}",)


class TestImportFile:
    def test_imports_into_the_project(self, survey, project, runtime, tmp_path) -> None:
        spec = _prepare(survey.layout, project, runtime, all_static(survey.layout))
        path = tmp_path / "survey.html"
        html = write_cartograph_html(
            spec, CartographRuntimeBundle(cartograph_js=b"//", cartograph_css="/**/")
        )
        path.write_text(html.replace("<om-legend ", '<om-legend data-x="1" '), "utf-8")
        built = import_cartograph_file(path, project)
        assert project.layoutManager().layoutByName("Survey UTM") is built.layout
        details = [issue.detail for issue in built.issues]
        assert "data-x" in details[0]  # the parser's findings come first
        assert any("written-out row" in detail for detail in details[1:])

    def test_unreadable_file(self, project, tmp_path) -> None:
        with pytest.raises(CartographImportError):
            import_cartograph_file(tmp_path / "missing.html", project)

    def test_embedded_images_are_written_beside_the_file(
        self, project, tmp_path
    ) -> None:
        path = tmp_path / "sheet.html"
        html = write_cartograph_html(
            replace(_furniture_spec(), atlas=None),
            CartographRuntimeBundle(
                cartograph_js=b"//", cartograph_css="", core_js=b"", core_css=""
            ),
        )
        path.write_text(html, "utf-8")
        built = import_cartograph_file(path, project)
        logo = built.items_by_id["logo"]
        assert logo.picturePath() == str(tmp_path / "sheet.images" / "logo.svg")
        assert not logo.isMissingImage()


def test_first_font_family_reads_a_css_font_list() -> None:
    from nika_onlymap_exporter.core.layout_builder import first_font_family as first

    assert first('"Sans Serif", sans-serif') == "Sans Serif"
    assert first("'Open Sans',Arial") == "Open Sans"
    assert first("serif") == "serif"


class TestInkAndGridLabels:
    """The furniture colours a design sets reach QGIS, text included, and a
    grid labels as many decimals as its interval needs."""

    @staticmethod
    def _built(project):
        spec = CartographSpec(
            cartograph_id="ink",
            size="A4",
            orientation="landscape",
            items=(
                FrameSpec(
                    id="main", box=Box(10, 10, 200, 150), center=(103.8, 1.3), zoom=12
                ),
                GraticuleSpec(
                    id="metres",
                    box=Box(10, 10, 200, 150),
                    for_="main",
                    crs="EPSG:32648",
                    interval=500.0,
                ),
                GraticuleSpec(
                    id="degrees",
                    box=Box(10, 10, 200, 150),
                    for_="main",
                    crs="EPSG:4326",
                    interval=0.2,
                ),
                LegendSpec(
                    id="legend", box=Box(220, 10, 60, 40), for_="main", color="#eeeeee"
                ),
                ScalebarSpec(
                    id="bar", box=Box(220, 60, 60, 12), for_="main", color="#ffffff"
                ),
            ),
        )
        return build_layout(spec, project)

    def test_legend_and_scale_bar_text_take_the_colour(self, project) -> None:
        from qgis.core import QgsLegendStyle

        built = self._built(project)
        legend = built.items_by_id["legend"]
        for style in (QgsLegendStyle.Title, QgsLegendStyle.SymbolLabel):
            assert legend.rstyle(style).textFormat().color().name() == "#eeeeee"
        assert built.items_by_id["bar"].textFormat().color().name() == "#ffffff"
        assert not any("color" in issue.detail for issue in built.issues)

    def test_a_legend_font_size_sizes_its_rows_and_scales_its_headings(
        self, project
    ) -> None:
        from qgis.core import QgsLegendStyle

        spec = CartographSpec(
            cartograph_id="legend-size",
            size="A4",
            orientation="landscape",
            items=(
                FrameSpec(
                    id="main", box=Box(10, 10, 200, 150), center=(103.8, 1.3), zoom=12
                ),
                LegendSpec(
                    id="legend", box=Box(220, 10, 60, 40), for_="main", font_size=9
                ),
            ),
        )
        built = build_layout(spec, project)
        legend = built.items_by_id["legend"]
        sizes = {
            style: legend.rstyle(style).textFormat().size()
            for style in (QgsLegendStyle.Title, QgsLegendStyle.SymbolLabel)
        }
        assert sizes[QgsLegendStyle.SymbolLabel] == pytest.approx(9.0)
        # QGIS's 16 pt title over 12 pt rows keeps that proportion.
        assert sizes[QgsLegendStyle.Title] == pytest.approx(12.0)
        assert not any("font-size" in issue.detail for issue in built.issues)

    def test_a_legend_wrap_width_and_a_scale_bar_text_size_reach_qgis(
        self, project
    ) -> None:
        """Both QGIS-only: long legend rows wrap at the width a template
        planned, and a scale bar's labels take its size (both QGIS 3.44 and
        4.x); a spec without them keeps QGIS's defaults."""
        spec = CartographSpec(
            cartograph_id="wrap",
            size="A4",
            orientation="landscape",
            items=(
                FrameSpec(
                    id="main", box=Box(10, 10, 200, 150), center=(103.8, 1.3), zoom=12
                ),
                LegendSpec(
                    id="legend", box=Box(220, 10, 60, 40), for_="main", wrap_width=31.5
                ),
                LegendSpec(id="plain", box=Box(220, 60, 60, 40), for_="main"),
                ScalebarSpec(
                    id="bar", box=Box(220, 110, 60, 12), for_="main", font_size=7.5
                ),
                ScalebarSpec(id="plain-bar", box=Box(220, 130, 60, 14), for_="main"),
            ),
        )
        built = build_layout(spec, project)
        settings = built.items_by_id["legend"].legendSettings()
        assert settings.autoWrapLinesAfter() == pytest.approx(31.5)
        assert built.items_by_id["plain"].legendSettings().autoWrapLinesAfter() == 0
        bar = built.items_by_id["bar"].textFormat()
        assert bar.size() == pytest.approx(7.5)
        assert bar.sizeUnit() == qgis_core.QgsUnitTypes.RenderPoints
        assert built.items_by_id["plain-bar"].textFormat().size() == pytest.approx(12)

    def test_grid_labels_have_the_decimals_their_interval_needs(self, project) -> None:
        built = self._built(project)
        grids = built.items_by_id["main"].grids().asList()
        precision = {round(g.intervalX(), 3): g.annotationPrecision() for g in grids}
        assert precision == {500.0: 0, 0.2: 1}
