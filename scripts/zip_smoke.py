#!/usr/bin/env python3
"""Install the built plugin ZIP in a throwaway plugins folder and use it for real.

usage: zip_smoke.py PLUGIN_ZIP REPO_DIR       (SMOKE_TEMPLATES=a,b,c limits templates)

Run with QGIS's own Python (4.2 or 3.44). It extracts the ZIP, loads the plugin
from the extracted copy (never from the source tree), opens the Cartograph dialog
and the template picker, then builds and exports every template (or the subset in
SMOKE_TEMPLATES) with the real OnlyMap runtime. REPO_DIR supplies only the test
fixture builders, not any plugin code. Exits 0 on "SMOKE OK".

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> int:
    zip_path, repo = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    plugins = Path(tempfile.mkdtemp(prefix="q2w-smoke-plugins-"))
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(plugins)

    # The plugin under test comes from the ZIP only: drop the source tree from the
    # import path (a PYTHONPATH may name it) and put the extracted copy first.
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != repo]
    sys.path.insert(0, str(plugins))
    sys.path.append(str(repo / "tests"))  # hermetic.py
    sys.path.append(str(repo / "tests" / "qgis"))  # cartograph_fixtures.py

    from hermetic import start_qgis
    from qgis.core import QgsProject
    from qgis.PyQt.QtCore import QSettings
    from qgis.PyQt.QtWidgets import QMessageBox

    work = Path(tempfile.mkdtemp(prefix="q2w-smoke-work-"))
    app = start_qgis(work)
    for name in ("information", "warning", "critical", "question"):
        setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )

    import nika_onlymap_exporter as pkg

    assert str(plugins) in pkg.__file__, pkg.__file__
    from qgis.gui import QgsMapCanvas

    class Iface:
        def __init__(self):
            self.canvas = QgsMapCanvas()

        def __getattr__(self, name):
            return lambda *a, **k: None

        def mainWindow(self):  # noqa: N802 - QGIS interface name
            return None

        def mapCanvas(self):  # noqa: N802 - QGIS interface name
            return self.canvas

    plugin = pkg.classFactory(Iface())
    plugin.initGui()
    from cartograph_fixtures import UTM, build_parcels_layer

    project = QgsProject.instance()
    project.setTitle("Parcel survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    project.setFileName(str(work / "survey.qgz"))

    plugin.run_cartograph()
    for _ in range(20):
        app.processEvents()
    print("dialog open:", plugin.dialog is not None and plugin.dialog.isVisible())

    from nika_onlymap_exporter.core import layout_templates
    from nika_onlymap_exporter.core.canvas_cartograph import (
        cartograph_from_view,
        template_preview,
    )
    from nika_onlymap_exporter.core.cartograph_export import (
        export_layout_to_cartograph,
    )
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.packaging.runtime_manager import LocalRuntime
    from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker

    ids = layout_templates.template_ids()
    print("templates:", len(ids))
    assert len(ids) == 15, ids

    extent = layer.extent()
    extent.grow(200)

    def render(template_id, page_size, orientation, width_px, figures=None):
        return template_preview(
            extent,
            UTM,
            [layer],
            project,
            template_id=template_id,
            width_px=width_px,
            page_size=page_size,
            orientation=orientation,
            figures=figures,
        )

    picker = TemplatePicker(
        render,
        qsettings=QSettings(str(work / "p.ini"), QSettings.Format.IniFormat),
        project=project,
    )
    picker.resize(900, 640)
    picker.show()
    for _ in range(600):
        if not picker._queue and not picker._timer.isActive():
            break
        app.processEvents()
    print("picker cards:", picker.template_list.count())
    # eleven of the fifteen designs plus the template request card
    assert picker.template_list.count() == 12
    picker.reject()

    runtime = LocalRuntime().load()
    bad = []
    chosen = [t for t in os.environ.get("SMOKE_TEMPLATES", "").split(",") if t]
    assert all(t in ids for t in chosen), chosen
    for template_id in chosen or ids:
        built = cartograph_from_view(
            extent, UTM, [layer], project, template_id=template_id
        )
        html = export_layout_to_cartograph(
            built.layout,
            project,
            runtime,
            FidelityReportBuilder(),
            frame_modes=built.frame_modes,
        )
        frames = html.count("<om-frame")
        if frames < 1 or "Key figure" in html or len(html) < 50_000:
            bad.append((template_id, frames, len(html)))
        print(f"  {template_id:13} frames={frames} bytes={len(html)}")
        project.layoutManager().removeLayout(built.layout)
    plugin.unload()
    print("SMOKE", "OK" if not bad else f"FAILED {bad}")
    sys.stdout.flush()
    # QGIS teardown after the dialogs above can segfault; the verdict is printed.
    os._exit(0 if not bad else 1)


if __name__ == "__main__":
    sys.exit(main())
