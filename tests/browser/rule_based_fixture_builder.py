"""Build a map of a rule-based layer, and the colour QGIS itself draws for it.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but no
PyQGIS, so the two halves meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/rule_based_fixture_builder.py OUT_DIR

`tests/browser/test_exported_map.py` runs this itself when `QGIS_PYTHON` names
that interpreter. It writes a standalone map of a row of squares drawn by nested
rules, ELSE rules, overlapping rules and switched-off ones through the real
exporter, and `expected.json`: the map's `entry` relative to OUT_DIR and, per
square, its middle and the colour QGIS draws on top there - measured from a real
render of the layer - or `null` where QGIS draws nothing.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

LON, LAT = -0.1, 51.5
# Squares this many degrees wide, one every `STEP` along the parallel.
SIDE, STEP = 0.004, 0.006
VIEW_PIXELS = (1000, 400)

# (v, c) per square.
VALUES = [(1, "x"), (3, "x"), (7, "y"), (9, "x"), (None, "z"), (None, "w")]

# (label, filter, colour or None for no symbol, active, children).
RULES = [
    ("Off", "\"c\" = 'w'", "#00ff00", False, []),
    ("Above 1", '"v" > 1', "#ff0000", True, []),
    (
        "Kind x",
        "\"c\" = 'x'",
        None,
        True,
        [
            ("Small x", '"v" < 5', "#ffff00", True, []),
            ("Other x", "ELSE", "#ff00ff", True, []),
        ],
    ),
    ("Rest", "ELSE", "#00ffff", True, []),
]


def main(out_dir: Path) -> int:
    from hermetic import start_qgis

    scratch = Path(tempfile.mkdtemp())
    app = start_qgis(scratch)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = _write_map(out_dir)
        (out_dir / "expected.json").write_text(json.dumps(expected, indent=2))
    finally:
        app.exitQgis()
    return 0


def _write_map(out_dir: Path) -> dict:
    from qgis.core import (
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsMapRendererSequentialJob,
        QgsMapSettings,
        QgsProject,
        QgsRectangle,
        QgsRuleBasedRenderer,
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
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:4326&field=v:integer&field=c:string", "Rules", "memory"
    )
    squares = []
    for index, (value, kind) in enumerate(VALUES):
        west = LON + index * STEP
        square = QgsRectangle(west, LAT, west + SIDE, LAT + SIDE)
        feature = QgsFeature(layer.fields())
        feature.setAttributes([value, kind])
        feature.setGeometry(QgsGeometry.fromRect(square))
        squares.append((feature, square))
    layer.dataProvider().addFeatures([feature for feature, _square in squares])
    layer.updateExtents()

    def append(parent, specs) -> None:
        for label, expression, colour, active, children in specs:
            symbol = (
                QgsFillSymbol.createSimple({"color": colour, "outline_style": "no"})
                if colour
                else None
            )
            rule = QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression, label)
            rule.setActive(active)
            parent.appendChild(rule)
            append(rule, children)

    root = QgsRuleBasedRenderer.Rule(None)
    append(root, RULES)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    project.addMapLayer(layer)

    exported = read_layer(layer, FidelityReportBuilder(), project=project)
    east = LON + (len(VALUES) - 1) * STEP + SIDE
    web = ExportProject(
        title="Rules",
        layers=(exported,),
        extent=Extent(west=LON, south=LAT, east=east, north=LAT + SIDE),
    )
    runtime = LocalRuntime(discover_runtime_dir())
    result = OnlyMapWriter(runtime_provider=runtime).write(web, out_dir / "rules")

    settings = QgsMapSettings()
    settings.setDestinationCrs(layer.crs())
    settings.setLayers([layer])
    settings.setOutputSize(QSize(*VIEW_PIXELS))
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setExtent(
        QgsRectangle(LON - SIDE, LAT - SIDE, east + SIDE, LAT + 2 * SIDE)
    )
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    drawn = []
    for _feature, square in squares:
        middle = square.center()
        pixel = settings.mapToPixel().transform(middle)
        colour = QColor(image.pixel(int(pixel.x()), int(pixel.y()))).name()
        drawn.append(
            {
                "lon": middle.x(),
                "lat": middle.y(),
                "colour": None if colour == "#ffffff" else colour,
            }
        )
    return {
        "entry": str(Path(result.entry_path).relative_to(out_dir)),
        "squares": drawn,
    }


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
