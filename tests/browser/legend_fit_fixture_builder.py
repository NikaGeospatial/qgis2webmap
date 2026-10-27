"""Export a layout whose legend box is too small, and record the QGIS legend
item's box after the export, for `test_cartograph_legend_fit_box.py`.

Run with QGIS's own Python (this tier's interpreter has no PyQGIS):

    QT_QPA_PLATFORM=offscreen <qgis-python> \
        tests/browser/legend_fit_fixture_builder.py OUT_DIR

Writes OUT_DIR/page.html and OUT_DIR/qgis.json (`{"box": [x, y, w, h],
"columns": n, "listed": n}`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
for entry in (HERE.parent, HERE.parent / "qgis", HERE.parents[1]):
    sys.path.insert(0, str(entry))


def main(out_dir: Path) -> int:
    from hermetic import start_qgis

    app = start_qgis(Path(tempfile.mkdtemp()))
    try:
        from legend_fit_fixtures import build_fit_world, rect_mm
        from qgis.core import QgsProject

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.packaging.runtime_manager import (
            LocalRuntime,
            discover_runtime_dir,
        )

        directory = discover_runtime_dir()
        if directory is None:
            print("no OnlyMap runtime", file=sys.stderr)
            return 1
        world = build_fit_world(QgsProject.instance())
        html = export_layout_to_cartograph(
            world.layout,
            world.project,
            LocalRuntime(directory).load(),
            FidelityReportBuilder(),
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "page.html").write_text(html, encoding="utf-8")
        (out_dir / "qgis.json").write_text(
            json.dumps(
                {
                    "box": list(rect_mm(world.legend)),
                    "columns": world.legend.columnCount(),
                    "listed": len(world.legend.model().rootGroup().findLayers()),
                }
            )
        )
        _write_raster_case(out_dir)
    finally:
        app.exitQgis()
    return 0


#: The classes of the raster case: (value, colour, label).
RASTER_CLASSES = (
    (0, "#2b8cbe", "Low ground"),
    (1000, "#a8dd8f", "Foothills"),
    (2000, "#f03b20", "High ground"),
)


def _write_raster_case(out_dir: Path) -> None:
    """A layout whose only layer is a paletted raster, exported for the
    legend's swatch; `raster.json` names the colours its classes have."""
    from legend_fixtures import write_tif
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPalettedRasterRenderer,
        QgsPrintLayout,
        QgsProject,
        QgsRasterLayer,
        QgsRectangle,
        QgsUnitTypes,
    )
    from qgis.PyQt.QtGui import QColor

    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.packaging.runtime_manager import (
        LocalRuntime,
        discover_runtime_dir,
    )

    mm = QgsUnitTypes.LayoutMillimeters
    project = QgsProject.instance()
    project.removeAllMapLayers()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    tif = write_tif(out_dir / "bands.tif", values=(0.0, 2000.0))
    layer = QgsRasterLayer(str(tif), "Elevation bands")
    classes = [
        QgsPalettedRasterRenderer.Class(value, QColor(color), label)
        for value, color, label in RASTER_CLASSES
    ]
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
    project.addMapLayer(layer)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("raster-legend")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(0, 0, mm))
    map_item.attemptResize(QgsLayoutSize(297, 150, mm))
    map_item.setCrs(project.crs())
    map_item.setLayers([layer])
    map_item.setExtent(QgsRectangle(0, 0, 10, 10))
    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.setLinkedMap(map_item)
    legend.setResizeToContents(False)
    legend.attemptMove(QgsLayoutPoint(10, 155, mm))
    legend.attemptResize(QgsLayoutSize(120, 45, mm))
    project.layoutManager().addLayout(layout)
    runtime_dir = discover_runtime_dir()
    html = export_layout_to_cartograph(
        layout, project, LocalRuntime(runtime_dir).load(), FidelityReportBuilder()
    )
    (out_dir / "raster.html").write_text(html, encoding="utf-8")
    (out_dir / "raster.json").write_text(
        json.dumps({"first_class_colour": RASTER_CLASSES[0][1]})
    )


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
