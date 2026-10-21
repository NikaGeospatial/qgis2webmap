"""`core/layout_reader.py` against the survey-utm fixture layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from cartograph_fixtures import all_static, build_survey_layout

from nika_onlymap_exporter.core.cartograph_ir import (
    GraticuleSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.fidelity_summary import Topic, subject_head, topic_of
from nika_onlymap_exporter.core.layout_reader import read_layout

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def survey(project, tmp_path):
    return build_survey_layout(project, tmp_path)


class TestPage:
    def test_reads_page_one_dimensions(self, survey) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)

        assert reading.orientation == "landscape"
        assert reading.page_width > reading.page_height
        assert reading.page_width == pytest.approx(297.0)
        assert reading.page_height == pytest.approx(210.0)

    def test_a_second_page_is_left_out_not_blocking(self, survey) -> None:
        """The cartograph is page 1 and is saved anyway, so the other pages
        are "Not exported", with the atlas named as the way to many pages."""
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)

        left_out = report.by_status(FidelityStatus.UNSUPPORTED)
        row = next(item for item in left_out if item.subject == "Page count")
        assert "page 1" in row.detail and "atlas" in row.detail
        assert not report.has_blockers


class TestFrames:
    def test_both_maps_default_to_live_in_a_utm_project_of_vectors(
        self, survey
    ) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)

        assert len(reading.frames) == 2
        assert all(frame.mode == "live" for frame in reading.frames)

    def test_a_rotated_utm_map_defaults_to_static(self, survey) -> None:
        survey.main_map.setMapRotation(15)
        reading = read_layout(survey.layout, FidelityReportBuilder())
        modes = {f.map_item.uuid(): f.mode for f in reading.frames}
        assert modes[survey.main_map.uuid()] == "static"
        assert modes[survey.inset_map.uuid()] == "live"

    def test_final_geometry_is_read_back_after_setextent_resize(self, survey) -> None:
        # setExtent() grows the item's aspect to match the extent (issue #1's
        # "verified headlessly" caveat) -- the box read here must be the
        # final 180x? size, never the 180x140 the fixture originally asked
        # attemptResize() for.
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)
        main = next(
            f for f in reading.frames if f.map_item.uuid() == survey.main_map.uuid()
        )
        assert main.box.w == pytest.approx(survey.main_map.sizeWithUnits().width())
        assert main.box.h == pytest.approx(survey.main_map.sizeWithUnits().height())

    def test_inset_overview_links_to_the_main_frame(self, survey) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)
        main = next(
            f for f in reading.frames if f.map_item.uuid() == survey.main_map.uuid()
        )
        inset = next(
            f for f in reading.frames if f.map_item.uuid() == survey.inset_map.uuid()
        )
        assert inset.overview_of == main.frame_id

    def test_live_default_for_web_mercator(self, project, tmp_path) -> None:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsFillSymbol,
            QgsGeometry,
            QgsLayoutItemMap,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsRectangle,
            QgsUnitTypes,
            QgsVectorLayer,
        )

        wm = QgsCoordinateReferenceSystem("EPSG:3857")
        project.setCrs(wm)
        layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "parcels", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1000, 1000)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
        project.addMapLayer(layer)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(100, 100, QgsUnitTypes.LayoutMillimeters))
        map_item.setCrs(wm)
        map_item.setLayers([layer])
        extent = layer.extent()
        extent.grow(500)
        map_item.setExtent(extent)
        map_item.refresh()

        report = FidelityReportBuilder()
        reading = read_layout(layout, report)
        assert reading.frames[0].mode == "live"

    def test_frame_modes_override_is_keyed_by_item_uuid(
        self, project, tmp_path
    ) -> None:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsFillSymbol,
            QgsGeometry,
            QgsLayoutItemMap,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsRectangle,
            QgsUnitTypes,
            QgsVectorLayer,
        )

        wm = QgsCoordinateReferenceSystem("EPSG:3857")
        project.setCrs(wm)
        layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "parcels", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1000, 1000)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
        project.addMapLayer(layer)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(100, 100, QgsUnitTypes.LayoutMillimeters))
        map_item.setCrs(wm)
        map_item.setLayers([layer])
        extent = layer.extent()
        extent.grow(500)
        map_item.setExtent(extent)
        map_item.refresh()

        report = FidelityReportBuilder()
        reading = read_layout(layout, report, frame_modes={map_item.uuid(): "static"})
        assert reading.frames[0].mode == "static"


class TestFurniture:
    def test_item_types_present(self, survey) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)
        kinds = {type(item) for item in reading.furniture}
        assert kinds == {
            GraticuleSpec,
            LegendSpec,
            ScalebarSpec,
            NorthSpec,
            TextSpec,
            ShapeSpec,
        }

    def test_static_legend_rows_are_literal_and_not_derived(self, survey) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(
            survey.layout, report, frame_modes=all_static(survey.layout)
        )
        legend = next(
            item for item in reading.furniture if isinstance(item, LegendSpec)
        )
        assert legend.derived is False
        assert len(legend.rows) == 1
        assert legend.rows[0].layer == "parcels"
        assert legend.rows[0].color == "#eba941"

    def test_label_expression_is_frozen_on_a_static_frame(self, survey) -> None:
        report = FidelityReportBuilder()
        reading = read_layout(
            survey.layout, report, frame_modes=all_static(survey.layout)
        )
        label = next(item for item in reading.furniture if isinstance(item, TextSpec))
        # @project_title has an exact dynamic equivalent, so it stays a token;
        # @map_scale is frozen to a literal number on a static frame (the
        # runtime's scale and QGIS's scale() disagree by ~0.1%, so the frame's
        # own raster and a live {{scale}} token would silently drift apart).
        assert "{{title}}" in label.body
        assert "{{frame." not in label.body
        assert "round(@map_scale)" not in label.body  # placeholder was assembled
        assert re.search(r"scale 1:\d+", label.body)

    def test_triangle_shape_is_unsupported(self, project, tmp_path) -> None:
        from qgis.core import (
            QgsLayoutItemShape,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsUnitTypes,
        )

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)
        shape.setShapeType(QgsLayoutItemShape.Shape.Triangle)
        shape.attemptResize(QgsLayoutSize(20, 20, QgsUnitTypes.LayoutMillimeters))

        report = FidelityReportBuilder()
        reading = read_layout(layout, report)
        assert not any(isinstance(item, ShapeSpec) for item in reading.furniture)
        unsupported = report.by_status(FidelityStatus.UNSUPPORTED)
        assert any("Triangle" in item.detail for item in unsupported)

    def test_unsupported_item_is_named_by_class(self, survey) -> None:
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        unsupported = report.by_status(FidelityStatus.UNSUPPORTED)
        assert any("QgsLayoutItemPolygon" in item.detail for item in unsupported)

    def test_fidelity_lists_exactly_the_expected_items(self, survey) -> None:
        """Nothing extra, nothing missing -- the fidelity report is the
        plugin's non-negotiable contract that nothing vanishes silently."""
        report = FidelityReportBuilder()
        read_layout(survey.layout, report, frame_modes=all_static(survey.layout))

        heads = {subject_head(item.subject) for item in report.items}
        assert heads == {
            "Page count",
            "Map frame",
            "Locator inset",
            "Grid",
            "Legend",
            "Scale bar",
            "Picture",
            "Text",
            "Shape",
            "Layout item",
        }

    def test_fidelity_subjects_read_like_the_web_maps(self, survey) -> None:
        """The Fidelity tab shows subjects as they are: item names the
        designer shows, never a uuid, and each under the Layout items topic."""
        report = FidelityReportBuilder()
        read_layout(survey.layout, report, frame_modes=all_static(survey.layout))

        subjects = {item.subject for item in report.items}
        assert "Map frame 'Map 1'" in subjects
        assert not any("{" in subject for subject in subjects)
        assert {topic_of(subject) for subject in subjects} == {Topic.LAYOUT}


def _web_mercator_layout(project):
    """One EPSG:3857 map item -- live by default -- with a legend and a
    map-scale label pointing at it."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    wm = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(wm)
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "parcels", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1000, 1000)))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(100, 100, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(wm)
    map_item.setLayers([layer])
    extent = layer.extent()
    extent.grow(500)
    map_item.setExtent(extent)
    map_item.refresh()

    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.setLinkedMap(map_item)
    legend.attemptResize(QgsLayoutSize(40, 30, QgsUnitTypes.LayoutMillimeters))

    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText("1:[% @map_scale %]")
    label.attemptResize(QgsLayoutSize(40, 10, QgsUnitTypes.LayoutMillimeters))
    return layout, map_item


class TestFrameModeOverrideReachesFurniture:
    """A frame forced static must get static furniture: a literal legend and
    a frozen scale, not ones that wait for a live map that never mounts."""

    def test_legend_on_a_frame_forced_static_is_literal(self, project) -> None:
        layout, map_item = _web_mercator_layout(project)
        reading = read_layout(
            layout, FidelityReportBuilder(), frame_modes={map_item.uuid(): "static"}
        )
        legend = next(i for i in reading.furniture if isinstance(i, LegendSpec))
        assert legend.derived is False
        assert [row.layer for row in legend.rows] == ["parcels"]

    def test_label_on_a_frame_forced_static_is_frozen(self, project) -> None:
        layout, map_item = _web_mercator_layout(project)
        reading = read_layout(
            layout, FidelityReportBuilder(), frame_modes={map_item.uuid(): "static"}
        )
        label = next(i for i in reading.furniture if isinstance(i, TextSpec))
        assert "{{frame." not in label.body

    def test_legend_on_a_live_frame_stays_derived(self, project) -> None:
        layout, _ = _web_mercator_layout(project)
        reading = read_layout(layout, FidelityReportBuilder())
        legend = next(i for i in reading.furniture if isinstance(i, LegendSpec))
        assert legend.derived is True


class TestShapeAndPictureDetail:
    def test_shape_stroke_and_radius_are_read_in_millimetres(self, project) -> None:
        from qgis.core import (
            QgsFillSymbol,
            QgsLayoutItemShape,
            QgsLayoutMeasurement,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsUnitTypes,
        )

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)
        shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        shape.setSymbol(
            QgsFillSymbol.createSimple(
                {
                    "color": "#ffffff",
                    "outline_color": "#ff0000",
                    "outline_width": "0.8",
                    "outline_width_unit": "MM",
                }
            )
        )
        shape.setCornerRadius(QgsLayoutMeasurement(1, QgsUnitTypes.LayoutCentimeters))
        shape.attemptResize(QgsLayoutSize(40, 20, QgsUnitTypes.LayoutMillimeters))

        reading = read_layout(layout, FidelityReportBuilder())
        spec = next(i for i in reading.furniture if isinstance(i, ShapeSpec))
        assert spec.fill == "#ffffff"
        assert spec.stroke == "#ff0000"
        assert spec.stroke_width == pytest.approx(0.8)
        assert spec.radius == pytest.approx(10.0)

    def test_shape_with_no_outline_has_no_stroke(self, project) -> None:
        from qgis.core import (
            QgsFillSymbol,
            QgsLayoutItemShape,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsUnitTypes,
        )

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)
        shape.attemptResize(QgsLayoutSize(40, 20, QgsUnitTypes.LayoutMillimeters))
        shape.setSymbol(
            QgsFillSymbol.createSimple({"color": "#ffffff", "outline_style": "no"})
        )

        reading = read_layout(layout, FidelityReportBuilder())
        spec = next(i for i in reading.furniture if isinstance(i, ShapeSpec))
        assert spec.stroke is None

    def test_jpeg_picture_keeps_its_own_media_type(self, project, tmp_path) -> None:
        from qgis.core import (
            QgsLayoutItemPicture,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsUnitTypes,
        )
        from qgis.PyQt.QtGui import QColor, QImage

        from nika_onlymap_exporter.core.cartograph_ir import ImageSpec

        path = tmp_path / "logo.jpg"
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        assert image.save(str(path), "JPG")

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        picture = QgsLayoutItemPicture(layout)
        layout.addLayoutItem(picture)
        picture.setPicturePath(str(path))
        picture.attemptResize(QgsLayoutSize(20, 20, QgsUnitTypes.LayoutMillimeters))

        reading = read_layout(layout, FidelityReportBuilder())
        spec = next(i for i in reading.furniture if isinstance(i, ImageSpec))
        assert spec.src.startswith("data:image/jpeg;base64,")

    def test_a_zero_size_item_is_left_out_not_fatal(self, project) -> None:
        """One degenerate item must not take the whole export down."""
        from qgis.core import QgsLayoutItemShape, QgsPrintLayout

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        shape = QgsLayoutItemShape(layout)
        layout.addLayoutItem(shape)  # never resized: 0 x 0

        report = FidelityReportBuilder()
        reading = read_layout(layout, report)
        assert reading.furniture == ()
        unsupported = report.by_status(FidelityStatus.UNSUPPORTED)
        assert any("no size" in item.detail for item in unsupported)


def test_a_stepped_scale_bar_is_changed_not_kept(survey) -> None:
    """Drawn as the runtime's single box: a change the Fidelity tab shows."""
    survey.scalebar.setStyle("stepped")
    report = FidelityReportBuilder()
    read_layout(survey.layout, report)
    rows = [item for item in report.items if item.subject.startswith("Scale bar")]
    assert rows and all(item.status is FidelityStatus.APPROXIMATED for item in rows)


def test_a_frozen_map_scale_is_the_maps_own_scale(survey) -> None:
    """`@map_scale` frozen on a static frame must read what QGIS shows for the
    map item, not a figure off by the mm-to-pixel factor."""
    report = FidelityReportBuilder()
    reading = read_layout(survey.layout, report, frame_modes=all_static(survey.layout))
    label = next(item for item in reading.furniture if isinstance(item, TextSpec))
    frozen = int(re.search(r"scale 1:(\d+)", label.body).group(1))
    assert frozen == pytest.approx(survey.main_map.scale(), rel=0.01)


def _set_page_fill(layout, color: str) -> None:
    symbol = qgis_core.QgsFillSymbol.createSimple(
        {"color": color, "style": "solid", "outline_style": "no"}
    )
    layout.pageCollection().page(0).setPageStyleSymbol(symbol)


def _set_text_color(label, color: str) -> None:
    from qgis.PyQt.QtGui import QColor

    text_format = label.textFormat()
    text_format.setColor(QColor(color))
    label.setTextFormat(text_format)


def _contrast_rows(report: FidelityReportBuilder) -> list:
    return [item for item in report.items if "contrast" in item.detail]


class TestBackgrounds:
    """Page 1's fill becomes `<om-cartograph bg>` and a map item's background
    `<om-frame frame-bg>` -- except QGIS's defaults, so ordinary layouts
    write the markup they always did."""

    def test_the_default_white_page_and_maps_carry_no_colour(self, survey) -> None:
        reading = read_layout(survey.layout, FidelityReportBuilder())
        assert reading.bg is None
        assert [frame.frame_bg for frame in reading.frames] == [None, None]

    def test_a_coloured_page_is_carried(self, survey) -> None:
        _set_page_fill(survey.layout, "#224466")
        assert read_layout(survey.layout, FidelityReportBuilder()).bg == "#224466"

    def test_a_translucent_page_keeps_its_alpha(self, survey) -> None:
        _set_page_fill(survey.layout, "34,68,102,128")
        assert read_layout(survey.layout, FidelityReportBuilder()).bg == "#22446680"

    def test_a_transparent_page_keeps_the_theme(self, survey) -> None:
        _set_page_fill(survey.layout, "34,68,102,0")
        assert read_layout(survey.layout, FidelityReportBuilder()).bg is None

    def test_a_patterned_page_is_its_main_colour_and_says_so(self, survey) -> None:
        symbol = qgis_core.QgsFillSymbol.createSimple(
            {"color": "#224466", "style": "dense4", "outline_style": "no"}
        )
        survey.layout.pageCollection().page(0).setPageStyleSymbol(symbol)
        report = FidelityReportBuilder()
        assert read_layout(survey.layout, report).bg == "#224466"
        rows = [item for item in report.items if item.subject == "Page background"]
        assert rows and rows[0].status is FidelityStatus.APPROXIMATED

    def test_a_coloured_map_background_is_carried(self, survey) -> None:
        from qgis.PyQt.QtGui import QColor

        survey.main_map.setBackgroundColor(QColor("#14161a"))
        survey.inset_map.setBackgroundEnabled(False)
        survey.inset_map.setBackgroundColor(QColor("#14161a"))
        reading = read_layout(survey.layout, FidelityReportBuilder())
        bgs = {frame.map_item.uuid(): frame.frame_bg for frame in reading.frames}
        assert bgs[survey.main_map.uuid()] == "#14161a"
        # Switched off in QGIS: the frame shows nothing of its own.
        assert bgs[survey.inset_map.uuid()] is None


class TestTextContrast:
    """A label hard to read against what it sits on in the HTML is a
    Changed row with the fix; its colour is never touched."""

    def test_light_text_on_the_default_page_is_flagged(self, survey) -> None:
        _set_text_color(survey.title_label, "#eeeeee")
        report = FidelityReportBuilder()
        reading = read_layout(survey.layout, report)
        rows = _contrast_rows(report)
        assert len(rows) == 1
        row = rows[0]
        assert row.status is FidelityStatus.APPROXIMATED
        assert row.subject == f"Text '{survey.title_label.displayName()}'"
        assert "light text on a light page" in row.detail
        assert "Give the page a dark background, or darken the text." in row.detail
        label = next(item for item in reading.furniture if isinstance(item, TextSpec))
        assert label.color == "#eeeeee"

    def test_dark_text_on_the_default_page_is_not(self, survey) -> None:
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert _contrast_rows(report) == []

    def test_light_text_on_a_dark_page_is_not(self, survey) -> None:
        _set_page_fill(survey.layout, "#14161a")
        _set_text_color(survey.title_label, "#e8e6e3")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert _contrast_rows(report) == []

    def test_dark_text_on_a_dark_page_says_lighten(self, survey) -> None:
        _set_page_fill(survey.layout, "#14161a")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        (row,) = _contrast_rows(report)
        assert "dark text on a dark page" in row.detail
        assert "or lighten the text." in row.detail

    def test_light_text_over_a_live_map_names_the_unloaded_map(self, survey) -> None:
        """Over a live frame the browser shows the frame's background until
        the map draws -- and always, without WebGL."""
        survey.title_label.attemptMove(
            qgis_core.QgsLayoutPoint(20, 40, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        survey.title_label.setZValue(survey.main_map.zValue() + 1)
        _set_text_color(survey.title_label, "#e8e6e3")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        (row,) = _contrast_rows(report)
        assert "light text on a light map frame background" in row.detail
        assert "wherever the map has not drawn" in row.detail
        assert "Give the map item a dark background" in row.detail

    def test_over_a_dark_map_background_it_reads(self, survey) -> None:
        from qgis.PyQt.QtGui import QColor

        survey.title_label.attemptMove(
            qgis_core.QgsLayoutPoint(20, 40, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        survey.title_label.setZValue(survey.main_map.zValue() + 1)
        survey.main_map.setBackgroundColor(QColor("#14161a"))
        _set_text_color(survey.title_label, "#e8e6e3")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert _contrast_rows(report) == []

    def test_over_a_static_frame_the_raster_is_not_judged(self, survey) -> None:
        survey.title_label.attemptMove(
            qgis_core.QgsLayoutPoint(20, 40, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        survey.title_label.setZValue(survey.main_map.zValue() + 1)
        _set_text_color(survey.title_label, "#e8e6e3")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report, frame_modes=all_static(survey.layout))
        assert _contrast_rows(report) == []

    def test_a_translucent_dark_plate_behind_the_text_counts(self, survey) -> None:
        survey.shape.attemptMove(
            qgis_core.QgsLayoutPoint(5, 2, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        survey.shape.attemptResize(
            qgis_core.QgsLayoutSize(200, 20, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        survey.shape.setSymbol(
            qgis_core.QgsFillSymbol.createSimple(
                {"color": "20,22,26,209", "outline_style": "no"}
            )
        )
        survey.shape.setZValue(survey.title_label.zValue() - 1)
        _set_text_color(survey.title_label, "#e8e6e3")
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert _contrast_rows(report) == []

    def test_mid_grey_is_enough_for_a_large_title_not_for_body_text(
        self, survey
    ) -> None:
        """WCAG's two minimums: 3:1 for large text, 4.5:1 for body text."""
        _set_text_color(survey.title_label, "#888888")  # about 3.4:1 here
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert len(_contrast_rows(report)) == 1

        text_format = survey.title_label.textFormat()
        text_format.setSize(24)
        text_format.setSizeUnit(qgis_core.QgsUnitTypes.RenderPoints)
        survey.title_label.setTextFormat(text_format)
        report = FidelityReportBuilder()
        read_layout(survey.layout, report)
        assert _contrast_rows(report) == []
