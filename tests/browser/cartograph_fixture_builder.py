"""Build the cartograph acceptance fixtures with real QGIS, for the browser tier.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but
no PyQGIS, and QGIS's has PyQGIS but no Playwright, so the two halves of the
acceptance check meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/cartograph_fixture_builder.py OUT_DIR

`tests/browser/test_cartograph_acceptance.py` runs this itself when
`QGIS_PYTHON` names that interpreter. CI's QGIS job runs it once and uploads
the directory; the browser job (no QGIS) points `CARTOGRAPH_FIXTURES_DIR` at the
download instead. It writes:

- `survey-static.html`: issue #1's acceptance layout (UTM 48N, 500 m grid,
  locator inset, legend, scale bar, north arrow, a `@map_scale` title) on an
  A3 landscape page, both frames static.
- `survey-live.html`: the same survey with its main map live, drawn in
  UTM 48N, and where QGIS puts the main map's corners.
- `geo-live.html`: an EPSG:4326 map at 60N, live, drawn as plate carree,
  and where QGIS puts its corners.
- `raster-live.html`: a live map of a raster (a red square on white) whose
  source file is deleted once exported, so it can only draw from the page.
- `story-live.html`: one Web Mercator map, live, with a scale token label.
- `atlas-live.html`: a live atlas over three parcels, one page each.
- `atlas-fixed.html`: the same atlas at QGIS's Fixed scale, with where
  QGIS's own atlas centres each parcel.
- `atlas-raster.html`: an atlas whose live frame draws only an embedded
  raster, one pure colour under each of the three parcels.
- `atlas-undrawn.html`: the atlas whose map no longer draws the coverage
  layer (only the roads), and the page order QGIS's own atlas makes.
- `atlas-ranked.html`: an atlas filtered and sorted over NULLs, and the page
  order QGIS's own atlas makes.
- `shared-live.html`: one layer drawn in two live frames, its rows written
  once.
- `expected.json`: what QGIS itself says the browser must show - the page
  size, where each grid line falls on the main frame in millimetres
  (`mapToItemCoords`, the same measurement the static-frame spike used), and
  the legend's rows.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "qgis"))
sys.path.insert(0, str(HERE.parents[1]))


def main(out_dir: Path) -> int:
    from qgis.core import QgsApplication, QgsProject

    app = QgsApplication([], False)
    app.initQgis()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = {
            "survey": _write_survey(QgsProject.instance(), out_dir),
        }
        QgsProject.instance().clear()
        expected["survey_a4"] = _write_survey(
            QgsProject.instance(),
            out_dir,
            page_size="A4",
            name="survey-a4-static.html",
            landscape=False,
        )
        QgsProject.instance().clear()
        expected["survey_live"] = _write_survey_live(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["geo_live"] = _write_geo_live(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["raster_live"] = _write_raster_live(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["story"] = _write_story(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas"] = _write_atlas(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas_fixed"] = _write_atlas_fixed(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        QgsProject.instance().clear()
        expected["atlas_ranked"] = _write_atlas_ranked(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas_filters"] = _write_atlas_filters(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas_dpi"] = _write_atlas_dpi(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas_raster"] = _write_atlas_raster(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas_undrawn"] = _write_atlas_undrawn(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["shared"] = _write_shared(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["opacity"] = _write_opacity(QgsProject.instance(), out_dir)
        (out_dir / "expected.json").write_text(json.dumps(expected, indent=2))
    finally:
        app.exitQgis()
    return 0


def _runtime():
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )

    directory = discover_runtime_dir()
    if directory is None:
        raise SystemExit("set ONLYMAP_RUNTIME_DIR")
    return LocalRuntime(directory).load()


def _write_survey(
    project,
    out_dir: Path,
    page_size: str = "A3",
    name: str = "survey-static.html",
    landscape: bool = True,
) -> dict:
    from cartograph_fixtures import all_static, build_survey_layout
    from qgis.core import QgsLayoutItemPage
    from qgis.PyQt.QtCore import QPointF

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    survey = build_survey_layout(project, Path(tempfile.mkdtemp()))
    pages = survey.layout.pageCollection()
    pages.deletePage(1)
    orientation = (
        QgsLayoutItemPage.Orientation.Landscape
        if landscape
        else QgsLayoutItemPage.Orientation.Portrait
    )
    pages.page(0).setPageSize(page_size, orientation)

    main_map = survey.main_map
    extent = main_map.extent()
    interval = main_map.grids().asList()[0].intervalX()
    mid_y = (extent.yMinimum() + extent.yMaximum()) / 2
    grid_x_mm = []
    easting = math.ceil(extent.xMinimum() / interval) * interval
    while easting <= extent.xMaximum():
        grid_x_mm.append(main_map.mapToItemCoords(QPointF(easting, mid_y)).x())
        easting += interval

    report = FidelityReportBuilder()
    html = export_layout_to_cartograph(
        survey.layout,
        project,
        _runtime(),
        report,
        frame_modes=all_static(survey.layout),
        static_dpi=150,
    )
    (out_dir / name).write_text(html, encoding="utf-8")
    size = pages.page(0).pageSize()
    return {
        "page_mm": [size.width(), size.height()],
        "main_frame": _frame_id(survey.layout, main_map),
        "grid_x_mm": grid_x_mm,
        "legend_rows": [
            symbol.label() or survey.parcels.name()
            for symbol in survey.parcels.renderer().legendSymbolItems()
        ],
    }


def _write_survey_live(project, out_dir: Path) -> dict:
    from cartograph_fixtures import build_survey_layout
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsPointXY,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    survey = build_survey_layout(project, Path(tempfile.mkdtemp()))
    survey.layout.pageCollection().deletePage(1)
    main_map = survey.main_map
    html = export_layout_to_cartograph(
        survey.layout,
        project,
        _runtime(),
        FidelityReportBuilder(),
        frame_modes={main_map.uuid(): "live", survey.inset_map.uuid(): "static"},
    )
    (out_dir / "survey-live.html").write_text(html, encoding="utf-8")
    extent = main_map.extent()
    to_wgs84 = QgsCoordinateTransform(
        main_map.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project
    )
    corners = []
    for x, y in (
        (extent.xMinimum(), extent.yMaximum()),
        (extent.xMaximum(), extent.yMaximum()),
        (extent.xMaximum(), extent.yMinimum()),
        (extent.xMinimum(), extent.yMinimum()),
    ):
        point = to_wgs84.transform(QgsPointXY(x, y))
        corners.append([point.x(), point.y()])
    size = main_map.sizeWithUnits()
    return {
        "main_frame": _frame_id(survey.layout, main_map),
        "corners": corners,
        "size_mm": [size.width(), size.height()],
    }


def _write_geo_live(project, out_dir: Path) -> dict:
    from cartograph_fixtures import build_geo_layout

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    layout, map_item = build_geo_layout(project)
    html = export_layout_to_cartograph(
        layout,
        project,
        _runtime(),
        FidelityReportBuilder(),
        frame_modes={map_item.uuid(): "live"},
    )
    (out_dir / "geo-live.html").write_text(html, encoding="utf-8")
    extent = map_item.extent()
    size = map_item.sizeWithUnits()
    return {
        "main_frame": _frame_id(layout, map_item),
        # The map is in EPSG:4326, so its extent corners are already lon/lat.
        "corners": [
            [extent.xMinimum(), extent.yMaximum()],
            [extent.xMaximum(), extent.yMaximum()],
            [extent.xMaximum(), extent.yMinimum()],
            [extent.xMinimum(), extent.yMinimum()],
        ],
        "size_mm": [size.width(), size.height()],
    }


def _write_raster_live(project, out_dir: Path) -> dict:
    from osgeo import gdal
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPointXY,
        QgsPrintLayout,
        QgsRasterLayer,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    gdal.UseExceptions()
    mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    # 40 x 40 pixels of 100 m around (1 MN, 6 MN): white, a red middle half.
    size, pixel, west, north = 40, 100.0, 1_000_000.0, 6_000_000.0
    source = Path(tempfile.mkdtemp()) / "square.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(source), size, size, 3, gdal.GDT_Byte
    )
    dataset.SetGeoTransform([west, pixel, 0.0, north, 0.0, -pixel])
    dataset.SetProjection(mercator.toWkt())
    for band, value in ((1, 255), (2, 0), (3, 0)):
        rows = bytearray(b"\xff" * size * size)
        for y in range(10, 30):
            rows[y * size + 10 : y * size + 30] = bytes([value]) * 20
        dataset.GetRasterBand(band).WriteRaster(0, 0, size, size, bytes(rows))
    dataset = None
    raster = QgsRasterLayer(str(source), "square", "gdal")
    project.addMapLayer(raster)
    project.setCrs(mercator)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(20, 20))
    map_item.attemptResize(QgsLayoutSize(120, 120))
    map_item.setCrs(mercator)
    map_item.setLayers([raster])
    map_item.setExtent(raster.extent())
    html = export_layout_to_cartograph(
        layout,
        project,
        _runtime(),
        FidelityReportBuilder(),
        frame_modes={map_item.uuid(): "live"},
    )
    (out_dir / "raster-live.html").write_text(html, encoding="utf-8")
    project.removeMapLayer(raster.id())
    source.unlink()
    to_wgs84 = QgsCoordinateTransform(
        mercator, QgsCoordinateReferenceSystem("EPSG:4326"), project
    )

    def lonlat(x: float, y: float) -> list[float]:
        point = to_wgs84.transform(QgsPointXY(x, y))
        return [point.x(), point.y()]

    return {
        "main_frame": _frame_id(layout, map_item),
        "red": lonlat(west + 20 * pixel, north - 20 * pixel),
        "white": lonlat(west + 5 * pixel, north - 5 * pixel),
    }


def _write_story(project, out_dir: Path) -> dict:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsLayoutItemLabel,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(web_mercator)
    project.setTitle("Story")
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "blocks", "memory")
    feature = QgsFeature()
    # Singapore, so the camera is a real place rather than null island.
    feature.setGeometry(
        QgsGeometry.fromRect(QgsRectangle(11555000, 143000, 11560000, 147000))
    )
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#3a7bd5"}))
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("story")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 20, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(200, 150, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(web_mercator)
    map_item.setLayers([layer])
    extent = layer.extent()
    extent.grow(2000)
    map_item.setExtent(extent)
    map_item.refresh()
    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText("Scale 1:[% @map_scale %]")
    label.attemptMove(QgsLayoutPoint(10, 5, QgsUnitTypes.LayoutMillimeters))
    label.attemptResize(QgsLayoutSize(150, 10, QgsUnitTypes.LayoutMillimeters))

    html = export_layout_to_cartograph(
        layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "story-live.html").write_text(html, encoding="utf-8")
    return {"main_frame": _frame_id(layout, map_item)}


def _write_atlas(project, out_dir: Path) -> dict:
    from atlas_fixtures import build_atlas_layout

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.layout_reader import atlas_page_count

    built = build_atlas_layout(project)
    html = export_layout_to_cartograph(
        built.layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "atlas-live.html").write_text(html, encoding="utf-8")
    return {"pages": atlas_page_count(built.layout)}


def _write_atlas_undrawn(project, out_dir: Path) -> dict:
    from atlas_fixtures import build_atlas_layout, qgis_atlas_page_ids

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    built = build_atlas_layout(project)
    built.main_map.setLayers([built.roads])
    html = export_layout_to_cartograph(
        built.layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "atlas-undrawn.html").write_text(html, encoding="utf-8")
    return {
        "pages": qgis_atlas_page_ids(built.layout),
        "main_frame": _frame_id(built.layout, built.main_map),
    }


def _write_atlas_raster(project, out_dir: Path) -> dict:
    from atlas_fixtures import PARCELS, build_atlas_layout
    from osgeo import gdal
    from qgis.core import QgsFillSymbol, QgsLayoutItemMap, QgsRasterLayer

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    gdal.UseExceptions()
    built = build_atlas_layout(project)
    # One 10 m pixel grid over all three parcels, white except a block of a
    # different pure colour exactly under each parcel: the page shows which
    # parcel's raster it drew.
    colours = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
    pixel, west, north = 10, -500, 1000
    width, height = 1200, 150
    source = Path(tempfile.mkdtemp()) / "parcels.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(source), width, height, 3, gdal.GDT_Byte
    )
    dataset.SetGeoTransform([west, pixel, 0.0, north, 0.0, -pixel])
    dataset.SetProjection(built.parcels.crs().toWkt())
    bands = [bytearray(b"\xff" * width * height) for _ in range(3)]
    for (_lot, _size, x, w, h), colour in zip(PARCELS, colours):
        for row in range((north - h) // pixel, north // pixel):
            for band, value in zip(bands, colour):
                start = row * width + (x - west) // pixel
                band[start : start + w // pixel] = bytes([value]) * (w // pixel)
    for number, rows in enumerate(bands, 1):
        dataset.GetRasterBand(number).WriteRaster(0, 0, width, height, bytes(rows))
    dataset = None
    raster = QgsRasterLayer(str(source), "parcel colours", "gdal")
    project.addMapLayer(raster)
    # The atlas follows the parcels, so the page must carry them; they are
    # painted invisibly so that only the raster puts colour on a sheet.
    built.parcels.renderer().setSymbol(
        QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_style": "no"})
    )
    built.main_map.setLayers([built.parcels, raster])
    built.main_map.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
    html = export_layout_to_cartograph(
        built.layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "atlas-raster.html").write_text(html, encoding="utf-8")
    project.removeMapLayer(raster.id())
    source.unlink()
    return {"pages": len(PARCELS), "colours": [list(c) for c in colours]}


def _write_atlas_fixed(project, out_dir: Path) -> dict:
    from atlas_fixtures import build_atlas_layout
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsLayoutItemMap,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    built = build_atlas_layout(project)
    built.main_map.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
    html = export_layout_to_cartograph(
        built.layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "atlas-fixed.html").write_text(html, encoding="utf-8")
    to_wgs84 = QgsCoordinateTransform(
        built.parcels.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project
    )
    centres = {}
    for feature in built.parcels.getFeatures():
        centre = to_wgs84.transform(feature.geometry().boundingBox().center())
        centres[str(feature.id())] = [centre.x(), centre.y()]
    return {"main_frame": _frame_id(built.layout, built.main_map), "centres": centres}


def _write_atlas_filters(project, out_dir: Path) -> dict:
    """The atlas filter's outcomes, one cartograph each, over the same layout:
    invalid, valid and matching nothing, valid subset, and the invalid filter
    corrected. `pages` are the lots QGIS's own atlas pages for each."""
    from atlas_fixtures import build_atlas_layout, qgis_atlas_page_ids

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    built = build_atlas_layout(project)
    lots = {f.id(): f["lot"] for f in built.parcels.getFeatures()}
    expected: dict[str, list[str] | None] = {}
    for name, expression in (
        ("invalid", '"lot" ='),
        ("zero", "\"lot\" = 'nowhere'"),
        ("subset", "\"size\" = 'small'"),
        ("corrected", "\"lot\" = 'A-1'"),
    ):
        built.atlas.setFilterFeatures(True)
        built.atlas.setFilterExpression(expression)
        html = export_layout_to_cartograph(
            built.layout, project, _runtime(), FidelityReportBuilder()
        )
        (out_dir / f"atlas-filter-{name}.html").write_text(html, encoding="utf-8")
        # QGIS cannot page an invalid filter at all: no pages to compare to.
        expected[name] = (
            None
            if name == "invalid"
            else [lots[i] for i in qgis_atlas_page_ids(built.layout)]
        )
    return expected


def _write_atlas_dpi(project, out_dir: Path) -> dict:
    """One small atlas saved at two resolutions: a 148 x 105 mm sheet, three
    pages, `dpi` 72 and 144 -- so the images of every route can be measured
    against the sheet and the saved resolution."""
    from atlas_fixtures import build_atlas_layout
    from qgis.core import QgsLayoutSize, QgsUnitTypes

    from nika_onlymap_exporter.core.cartograph_export import (
        finish_cartograph,
        prepare_cartograph,
        prepare_live_reads,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    built = build_atlas_layout(project)
    built.layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(148, 105, QgsUnitTypes.LayoutMillimeters)
    )
    built.main_map.attemptResize(QgsLayoutSize(128, 85, QgsUnitTypes.LayoutMillimeters))
    for dpi in (72, 144):
        report = FidelityReportBuilder()
        prepared = prepare_cartograph(built.layout, project, _runtime(), report)
        prepared = replace(prepared, spec=replace(prepared.spec, dpi=dpi))
        prepared = prepare_live_reads(prepared, project, report)
        html = finish_cartograph(prepared, project, report)
        (out_dir / f"atlas-dpi-{dpi}.html").write_text(html, encoding="utf-8")
    return {"page_mm": [148, 105], "pages": 3}


def _write_atlas_ranked(project, out_dir: Path) -> dict:
    from atlas_fixtures import (
        build_atlas_layout,
        qgis_atlas_page_ids,
        use_ranked_parcels,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    built = build_atlas_layout(project)
    use_ranked_parcels(built)
    # NULL scores drop out of the filter in QGIS; NULL ranks sort first.
    built.atlas.setFilterFeatures(True)
    built.atlas.setFilterExpression('"score" <> 1')
    built.atlas.setSortFeatures(True)
    built.atlas.setSortExpression('"rank"')
    built.atlas.setSortAscending(True)
    html = export_layout_to_cartograph(
        built.layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "atlas-ranked.html").write_text(html, encoding="utf-8")
    return {"pages": qgis_atlas_page_ids(built.layout)}


def _write_shared(project, out_dir: Path) -> dict:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(web_mercator)
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=name:string", "blocks", "memory"
    )
    for index in range(12):
        feature = QgsFeature(layer.fields())
        feature.setAttribute("name", f"block {index}")
        x = 11555000 + index * 400
        feature.setGeometry(
            QgsGeometry.fromRect(QgsRectangle(x, 143000, x + 300, 143300))
        )
        layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("shared")
    for x, y, w, h in ((10, 20, 180, 120), (200, 20, 80, 60)):
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
        map_item.setCrs(web_mercator)
        map_item.setLayers([layer])
        extent = layer.extent()
        extent.grow(5000)
        map_item.setExtent(extent)
    html = export_layout_to_cartograph(
        layout, project, _runtime(), FidelityReportBuilder()
    )
    (out_dir / "shared-live.html").write_text(html, encoding="utf-8")
    return {"features": layer.featureCount(), "marker": "block 7"}


def _write_opacity(project, out_dir: Path) -> dict:
    """Translucent items over a solid blue backdrop, and QGIS's own render.

    A 50% label, a live and a static map at 50%, and two red squares in a
    50% group, one itself at 50%, the other opaque (QGIS does not compose the
    group's opacity onto its members, so the opaque one stays opaque). `qgis_png` is
    `QgsLayoutExporter.renderPageToImage` at 96 dpi; `probes` are the page
    positions, in mm, to compare the browser against it.
    """
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsLayoutExporter,
        QgsLayoutItemLabel,
        QgsLayoutItemMap,
        QgsLayoutItemShape,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    mm = QgsUnitTypes.LayoutMillimeters
    mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(mercator)
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "block", "memory")
    feature = QgsFeature()
    # 1 km square on a 10 x 6 km extent: 50 m per mm on a 200 x 120 mm map.
    feature.setGeometry(
        QgsGeometry.fromRect(QgsRectangle(11555000, 143000, 11556000, 144000))
    )
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(
        QgsFillSymbol.createSimple({"color": "#00ff00", "outline_style": "no"})
    )
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("opacity")

    def place(item, x, y, w, h):
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y, mm))
        item.attemptResize(QgsLayoutSize(w, h, mm))

    def shape(x, y, w, h, colour):
        item = QgsLayoutItemShape(layout)
        item.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        place(item, x, y, w, h)
        item.setSymbol(
            QgsFillSymbol.createSimple({"color": colour, "outline_style": "no"})
        )
        return item

    shape(0, 0, 297, 210, "#0000ff")
    label = QgsLayoutItemLabel(layout)
    label.setText("Half")
    label.setBackgroundEnabled(True)
    label.setBackgroundColor(QColor("#ff0000"))
    place(label, 10, 10, 100, 30)
    label.setItemOpacity(0.5)

    def draw_map(x, y, w, h):
        item = QgsLayoutItemMap(layout)
        place(item, x, y, w, h)
        item.setCrs(mercator)
        item.setLayers([layer])
        item.setExtent(QgsRectangle(11550500, 140500, 11560500, 146500))
        item.refresh()
        item.setItemOpacity(0.5)
        return item

    live_map = draw_map(10, 60, 200, 120)
    static_map = draw_map(220, 60, 67, 40)
    square_a = shape(220, 10, 30, 30, "#ff0000")
    square_b = shape(255, 10, 30, 30, "#ff0000")
    group = layout.groupItems([square_a, square_b])
    group.setItemOpacity(0.5)
    square_a.setItemOpacity(0.5)

    page_mm = layout.pageCollection().page(0).pageSize()
    width_px = round(page_mm.width() / 25.4 * 96)
    height_px = round(page_mm.height() / 25.4 * 96)
    image = QgsLayoutExporter(layout).renderPageToImage(0, QSize(width_px, height_px))
    image.save(str(out_dir / "opacity-qgis.png"))

    html = export_layout_to_cartograph(
        layout,
        project,
        _runtime(),
        FidelityReportBuilder(),
        frame_modes={live_map.uuid(): "live", static_map.uuid(): "static"},
    )
    (out_dir / "opacity-live.html").write_text(html, encoding="utf-8")
    return {
        "page_mm": [page_mm.width(), page_mm.height()],
        "live_frame": _frame_id(layout, live_map),
        "probes": {
            "label": [95, 25],
            "live_map_polygon": [110, 120],
            "live_map_background": [30, 75],
            "static_map_polygon": [253.5, 80],
            "static_map_background": [225, 65],
            "group_member_at_half": [235, 25],
            "group_member_opaque": [270, 25],
            "backdrop": [150, 195],
        },
    }


def _frame_id(layout, map_item) -> str:
    from qgis.core import QgsLayoutItem

    from nika_onlymap_exporter.core.cartograph_ir import element_id, item_id_tokens

    tokens = item_id_tokens(
        [(i.uuid(), i.id()) for i in layout.items() if isinstance(i, QgsLayoutItem)]
    )
    return element_id("frame", map_item.uuid(), tokens)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    code = main(Path(sys.argv[1]))
    sys.stdout.flush()
    # QGIS's own teardown can crash after a clean run on some builds; the
    # files are written, so exit without running it twice.
    os._exit(code)
