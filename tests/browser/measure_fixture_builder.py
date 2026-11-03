"""Build web maps from real QGIS projects, for the measure tool's browser test.

Run with QGIS's own Python (the browser tier's interpreter has Playwright but no
PyQGIS, so the two halves meet through files):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/measure_fixture_builder.py OUT_DIR

`tests/browser/test_measure_tool.py` runs this itself when `QGIS_PYTHON` names
that interpreter; CI's QGIS job runs it once and the browser job points
`MEASURE_FIXTURES_DIR` at the download.

Two projects, one in a geographic CRS (EPSG:4326) and one in a projected one
(British National Grid, EPSG:27700), each read by the real project reader with
the dialog's default settings and written twice: as the Standalone HTML a user
exports, and as the dialog's Preview writes it (`write_preview`, served and
live). `maps.json` names each, relative to OUT_DIR.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

# Where the points are, in WGS84; each project holds them in its own CRS.
POINTS = ((0.1, 51.1), (-0.6, 51.8), (0.9, 51.7))
CASES = {"geographic": "EPSG:4326", "projected": "EPSG:27700"}


def main(out_dir: Path) -> int:
    from hermetic import start_qgis

    scratch = Path(tempfile.mkdtemp())
    app = start_qgis(scratch)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        maps = {name: _write_case(name, crs, out_dir) for name, crs in CASES.items()}
        (out_dir / "maps.json").write_text(json.dumps(maps, indent=2))
    finally:
        app.exitQgis()
    return 0


def _write_case(name: str, crs: str, out_dir: Path) -> dict[str, str]:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsFeature,
        QgsGeometry,
        QgsPointXY,
        QgsProject,
        QgsVectorLayer,
    )

    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.project_reader import read_project
    from nika_onlymap_exporter.core.settings import DialogState
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )
    from nika_onlymap_exporter.ui.preview import write_preview
    from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

    project = QgsProject.instance()
    project.clear()
    layer_crs = QgsCoordinateReferenceSystem(crs)
    project.setCrs(layer_crs)
    to_layer = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), layer_crs, project
    )
    layer = QgsVectorLayer(f"Point?crs={crs}&field=name:string", "Sites", "memory")
    features = []
    for index, (lon, lat) in enumerate(POINTS):
        feature = QgsFeature(layer.fields())
        feature.setAttribute(0, f"Site {index}")
        point = to_layer.transform(QgsPointXY(lon, lat))
        feature.setGeometry(QgsGeometry.fromPointXY(point))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    project.addMapLayer(layer)

    settings = DialogState().to_export_settings()
    web = read_project(
        project, FidelityReportBuilder(), settings=settings, title_override=name
    )
    writer = OnlyMapWriter(runtime_provider=LocalRuntime(discover_runtime_dir()))

    exported = writer.write(web, out_dir / name / "file")
    preview = write_preview(
        web, f"measure-{name}", writer=writer, live=True, served=True
    )
    shutil.copytree(preview.entry_path.parent, out_dir / name / "preview")
    return {
        "file": str(exported.entry_path.relative_to(out_dir)),
        "preview": str(
            (out_dir / name / "preview" / preview.entry_path.name).relative_to(out_dir)
        ),
    }


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
