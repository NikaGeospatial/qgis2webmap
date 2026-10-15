"""Build the cartograph acceptance fixtures with real QGIS, for the browser tier.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but
no PyQGIS, and QGIS's has PyQGIS but no Playwright, so the two halves of the
acceptance check meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/cartograph_fixture_builder.py OUT_DIR

`tests/browser/test_cartograph_acceptance.py` runs this itself when
`QGIS_PYTHON` names that interpreter. It writes:

- `survey-static.html`: issue #1's acceptance layout (UTM 48N, 500 m grid,
  locator inset, legend, scale bar, north arrow, a `@map_scale` title) on an
  A3 landscape page, both frames static.
- `story-live.html`: one Web Mercator map, live, with a scale token label.
- `atlas-live.html`: a live atlas over three parcels, one page each.
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
        expected["story"] = _write_story(QgsProject.instance(), out_dir)
        QgsProject.instance().clear()
        expected["atlas"] = _write_atlas(QgsProject.instance(), out_dir)
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


def _write_survey(project, out_dir: Path) -> dict:
    from cartograph_fixtures import build_survey_layout
    from qgis.core import QgsLayoutItemPage
    from qgis.PyQt.QtCore import QPointF

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

    survey = build_survey_layout(project, Path(tempfile.mkdtemp()))
    pages = survey.layout.pageCollection()
    pages.deletePage(1)
    pages.page(0).setPageSize("A3", QgsLayoutItemPage.Orientation.Landscape)

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
        survey.layout, project, _runtime(), report, static_dpi=150
    )
    (out_dir / "survey-static.html").write_text(html, encoding="utf-8")
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
