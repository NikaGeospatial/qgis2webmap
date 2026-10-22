"""Build maps with ground-sized markers, and what QGIS itself draws for them.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but no
PyQGIS, so the two halves meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/ground_size_fixture_builder.py OUT_DIR

`tests/browser/test_exported_map.py` runs this itself when `QGIS_PYTHON` names
that interpreter. It writes, per case, a standalone map of one 100 m radius red
circle through the real exporter, and `expected.json`: for each web zoom, the
radius in CSS pixels that QGIS draws the same circle at - measured from a real
render of the layer, not computed - at the scale the web map shows at that zoom.

Each case's `entry` in `expected.json` is its standalone map, relative to
OUT_DIR. CI's QGIS job runs this once and uploads the directory; the browser
job (no QGIS) points `GROUND_SIZE_FIXTURES_DIR` at the download.

- `utm-metres`: EPSG:32630, size in `MetersInMapUnits`
- `mercator-metres`: EPSG:3857, size in `MetersInMapUnits`, which QGIS draws as
  the Web Mercator metre rather than correcting for latitude
- `mercator-map-units`: EPSG:3857, size in `MapUnits`

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

LON, LAT = -0.1, 51.5
RADIUS_M = 100.0
ZOOMS = (15, 17)
VIEW_PIXELS = (1000, 800)
# The web world is 512 px wide at zoom 0, around the sphere QGIS's Web Mercator
# is projected on.
WORLD_METRES = 2 * math.pi * 6378137.0

CASES = {
    "utm-metres": ("EPSG:32630", "MetersInMapUnits"),
    "mercator-metres": ("EPSG:3857", "MetersInMapUnits"),
    "mercator-map-units": ("EPSG:3857", "MapUnits"),
}


def main(out_dir: Path) -> int:
    from hermetic import start_qgis

    scratch = Path(tempfile.mkdtemp())
    app = start_qgis(scratch)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = {
            name: _write_case(name, *case, out_dir) for name, case in CASES.items()
        }
        (out_dir / "expected.json").write_text(json.dumps(expected, indent=2))
    finally:
        app.exitQgis()
    return 0


def _write_case(name: str, crs: str, unit_name: str, out_dir: Path) -> dict:
    from qgis.core import (
        Qgis,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsDistanceArea,
        QgsFeature,
        QgsGeometry,
        QgsMapRendererSequentialJob,
        QgsMapSettings,
        QgsMarkerSymbol,
        QgsPointXY,
        QgsProject,
        QgsRectangle,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.export_ir import ExportProject, Extent
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.layer_reader import read_layer
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )
    from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

    project = QgsProject.instance()
    project.clear()
    layer_crs = QgsCoordinateReferenceSystem(crs)
    centre = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), layer_crs, project
    ).transform(QgsPointXY(LON, LAT))

    layer = QgsVectorLayer(f"Point?crs={crs}&field=name:string", "Circle", "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(centre))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    # A QGIS marker size is a diameter. No outline, so the red area is the circle.
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "size": str(2 * RADIUS_M),
            "color": "255,0,0",
            "outline_style": "no",
        }
    )
    symbol.symbolLayer(0).setSizeUnit(getattr(Qgis.RenderUnit, unit_name))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    project.addMapLayer(layer)

    report = FidelityReportBuilder()
    exported = read_layer(layer, report, project=project)
    web = ExportProject(
        title=name,
        layers=(exported,),
        extent=Extent(
            west=LON - 0.01, south=LAT - 0.005, east=LON + 0.01, north=LAT + 0.005
        ),
    )
    runtime = LocalRuntime(discover_runtime_dir())
    result = OnlyMapWriter(runtime_provider=runtime).write(web, out_dir / name)

    # What QGIS draws at each web zoom: the web map shows a CSS pixel as
    # `WORLD_METRES / (512 * 2**zoom)` Web Mercator metres, on the ground that
    # times cos(latitude). A map unit of `layer_crs` is `metres_per_map_unit`
    # metres on the ground, measured by QGIS on the ellipsoid.
    area = QgsDistanceArea()
    area.setSourceCrs(layer_crs, project.transformContext())
    area.setEllipsoid("WGS84")
    metres_per_map_unit = area.measureLine(
        centre, QgsPointXY(centre.x() + 1.0, centre.y())
    )
    zooms = {}
    for zoom in ZOOMS:
        ground_per_pixel = WORLD_METRES * math.cos(math.radians(LAT)) / (512 * 2**zoom)
        map_units_per_pixel = ground_per_pixel / metres_per_map_unit
        settings = QgsMapSettings()
        settings.setDestinationCrs(layer_crs)
        settings.setEllipsoid("WGS84")
        settings.setLayers([layer])
        settings.setOutputSize(QSize(*VIEW_PIXELS))
        settings.setOutputDpi(96)
        settings.setBackgroundColor(QColor(255, 255, 255))
        half_w = VIEW_PIXELS[0] * map_units_per_pixel / 2
        half_h = VIEW_PIXELS[1] * map_units_per_pixel / 2
        settings.setExtent(
            QgsRectangle(
                centre.x() - half_w,
                centre.y() - half_h,
                centre.x() + half_w,
                centre.y() + half_h,
            )
        )
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        zooms[str(zoom)] = {
            "radius_px": _radius_of_red(job.renderedImage()),
            "ground_metres_per_pixel": ground_per_pixel,
        }
    return {
        "entry": str(Path(result.entry_path).relative_to(out_dir)),
        "lon": LON,
        "lat": LAT,
        "radius_m": RADIUS_M,
        # QGIS draws a metre in Web Mercator as the projected metre, which is
        # `cos(latitude)` of a metre on the ground; elsewhere it is exact.
        "ground_radius_m": RADIUS_M
        * (metres_per_map_unit if crs == "EPSG:3857" else 1.0),
        "zooms": zooms,
    }


def _radius_of_red(image) -> float:
    """The radius of the circle whose area is the image's red pixels."""
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.red() > 180 and colour.green() < 80 and colour.blue() < 80:
                count += 1
    return math.sqrt(count / math.pi)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
