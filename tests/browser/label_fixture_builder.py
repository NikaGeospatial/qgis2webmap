"""Build a map labelled from a QGIS expression, and the text QGIS draws for it.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but no
PyQGIS, so the two halves meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/label_fixture_builder.py OUT_DIR

`tests/browser/test_exported_map.py` runs this itself when `QGIS_PYTHON` names
that interpreter. CI's QGIS job runs it once and uploads the directory; the
browser job (no QGIS) points `LABEL_FIXTURES_DIR` at the download. It writes:

- `expression-labels/`: a standalone map, through the real reader and writer,
  of four points in rows a degree apart, labelled in red by
  `repeat('W', "n")` - one, four and twelve letters, and NULL (no label) for
  the fourth.
- `expected.json`: its `entry`, and per point its position and the text QGIS's
  own labelling engine draws there on a render of the same layer (`None` where
  it draws none).
- `accented-names/`: a map of points labelled "Zürich" whose Names (the Layers
  tab's Points row) show another field, "ØÅÆ", every glyph of which the label
  field lacks; `expected.json`'s `names` gives its `entry` and the points.

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

EXPRESSION = "repeat('W', \"n\")"
# Rows a degree apart: at the zoom the map opens on, a label is far shorter
# than the gap to the next row.
POINTS = [(0.0, 0.5, 1), (2.0, 1.5, 4), (0.0, 2.5, 12), (2.0, 3.5, None)]


def main(out_dir: Path) -> int:
    from hermetic import start_qgis

    scratch = Path(tempfile.mkdtemp())
    app = start_qgis(scratch)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = _write_map(out_dir)
        expected["names"] = _write_names_map(out_dir)
        (out_dir / "expected.json").write_text(json.dumps(expected, indent=2))
    finally:
        app.exitQgis()
    return 0


def _write_map(out_dir: Path) -> dict:
    from qgis.core import (
        Qgis,
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsMapRendererSequentialJob,
        QgsMapSettings,
        QgsMarkerSymbol,
        QgsPalLayerSettings,
        QgsProject,
        QgsRectangle,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
        QgsVectorLayerSimpleLabeling,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.export_ir import ExportSettings
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.project_reader import read_project
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )
    from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=n:integer", "Peaks", "memory")
    features = []
    for lon, lat, n in POINTS:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(f"Point({lon} {lat})"))
        feature["n"] = n
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    # Black markers, so the only red on the map is the label text.
    layer.setRenderer(
        QgsSingleSymbolRenderer(
            QgsMarkerSymbol.createSimple(
                {"name": "circle", "size": "2", "color": "0,0,0"}
            )
        )
    )
    settings = QgsPalLayerSettings()
    settings.fieldName = EXPRESSION
    settings.isExpression = True
    settings.placement = Qgis.LabelPlacement.OverPoint
    text_format = settings.format()
    text_format.setColor(QColor(255, 0, 0))
    text_format.setSize(20)
    text_format.setSizeUnit(Qgis.RenderUnit.Pixels)
    font = text_format.font()
    font.setBold(True)
    text_format.setFont(font)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    project.addMapLayer(layer)

    # What QGIS draws, by the point each label sits on.
    view = QgsMapSettings()
    view.setLayers([layer])
    view.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    view.setExtent(QgsRectangle(-3, 0, 5, 4))
    view.setOutputSize(QSize(1600, 800))
    job = QgsMapRendererSequentialJob(view)
    job.start()
    job.waitForFinished()
    drawn = {
        round(label.labelRect.center().y(), 1): label.labelText
        for label in job.takeLabelingResults().allLabels()
        if not label.isUnplaced
    }

    report = FidelityReportBuilder()
    exported = read_project(
        project,
        report,
        settings=ExportSettings(show_title=False, show_legend=False),
    )
    runtime = LocalRuntime(discover_runtime_dir())
    result = OnlyMapWriter(runtime_provider=runtime).write(
        exported, out_dir / "expression-labels"
    )
    return {
        "entry": str(Path(result.entry_path).relative_to(out_dir)),
        "points": [
            {"lon": lon, "lat": lat, "text": drawn.get(round(lat, 1))}
            for lon, lat, _n in POINTS
        ],
        "fidelity": [item.detail for item in report.items],
    }


# Names a degree apart, far from each other at the zoom the map opens on.
NAME_POINTS = [(0.0, 0.5), (2.0, 1.5), (0.0, 2.5)]


def _write_names_map(out_dir: Path) -> dict:
    from qgis.core import (
        Qgis,
        QgsFeature,
        QgsGeometry,
        QgsMarkerSymbol,
        QgsPalLayerSettings,
        QgsProject,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
        QgsVectorLayerSimpleLabeling,
    )
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.export_ir import ExportSettings
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.project_reader import read_project
    from nika_onlymap_exporter.core.settings import LayerSettings
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )
    from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

    project = QgsProject.instance()
    project.clear()
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=other:string", "Towns", "memory"
    )
    features = []
    for lon, lat in NAME_POINTS:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(f"Point({lon} {lat})"))
        feature.setAttributes(["Zürich", "ØÅÆ"])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    layer.setRenderer(
        QgsSingleSymbolRenderer(
            QgsMarkerSymbol.createSimple(
                {"name": "circle", "size": "2", "color": "0,0,0"}
            )
        )
    )
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    text_format = settings.format()
    text_format.setColor(QColor(255, 0, 0))
    text_format.setSize(20)
    text_format.setSizeUnit(Qgis.RenderUnit.Pixels)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    project.addMapLayer(layer)

    exported = read_project(
        project,
        FidelityReportBuilder(),
        settings=ExportSettings(show_title=False, show_legend=False),
        layer_settings={layer.id(): LayerSettings(name_field="other")},
    )
    runtime = LocalRuntime(discover_runtime_dir())
    result = OnlyMapWriter(runtime_provider=runtime).write(
        exported, out_dir / "accented-names"
    )
    return {
        "entry": str(Path(result.entry_path).relative_to(out_dir)),
        "points": [[lon, lat] for lon, lat in NAME_POINTS],
    }


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
