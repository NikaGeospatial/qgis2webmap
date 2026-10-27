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
    finally:
        app.exitQgis()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
