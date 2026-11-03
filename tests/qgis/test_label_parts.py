"""Labels on multi-part features, against what QGIS itself draws.

QGIS labels every point of a MultiPoint, and a multi-part line or polygon once
on its longest or largest part - or once on every part when "Label every part
of multi-part features" is on. The exporter used to label a MultiPoint at its
first point only, a MultiLineString on its part with the most vertices, and to
ignore the setting. The expectation here is not a hand-worked coordinate: it is
where QGIS's own labelling engine puts the labels on a render of the same layer.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math

import pytest

from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.manifest_builder import build_label_element
from nika_onlymap_exporter.core.project_reader import read_project

qgis_core = pytest.importorskip("qgis.core")
QtCore = pytest.importorskip("qgis.PyQt.QtCore")

# Each part is laid out so its label point is unambiguous: three-vertex
# straight lines (the middle vertex is the middle of the line) and squares.
# The long line has fewer vertices than the short one, so "longest" by vertex
# count and by length disagree.
CASES = {
    "MultiPoint": "MultiPoint((1 1),(5 5),(9 2))",
    "MultiLineString": (
        "MultiLineString((1 1, 1.1 1.05, 1.2 1.1, 1.3 1.15, 1.4 1.2),(3 8, 6 8, 9 8))"
    ),
    "MultiPolygon": "MultiPolygon(((1 1,2 1,2 2,1 2,1 1)),((4 4,9 4,9 9,4 9,4 4)))",
}
PLACEMENT = {
    "MultiPoint": "OverPoint",
    "MultiLineString": "Horizontal",
    "MultiPolygon": "OverPoint",
}


def _labelled_layer(kind: str, per_part: bool):
    layer = qgis_core.QgsVectorLayer(
        f"{kind}?crs=EPSG:4326&field=name:string", kind, "memory"
    )
    feature = qgis_core.QgsFeature(layer.fields())
    feature.setGeometry(qgis_core.QgsGeometry.fromWkt(CASES[kind]))
    feature["name"] = "X"
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.labelPerPart = per_part
    settings.placement = getattr(qgis_core.Qgis.LabelPlacement, PLACEMENT[kind])
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _qgis_label_centres(layer) -> list[tuple[float, float]]:
    """Where QGIS's labelling engine draws `layer`'s labels, in degrees."""
    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"))
    settings.setExtent(qgis_core.QgsRectangle(0, 0, 10, 10))
    settings.setOutputSize(QtCore.QSize(1000, 1000))
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    results = job.takeLabelingResults()
    return [
        (label.labelRect.center().x(), label.labelRect.center().y())
        for label in results.allLabels()
        if not label.isUnplaced
    ]


def _exported_anchors(project, layer) -> list[tuple[float, float]]:
    """The label points the exported map's TextLayer carries for `layer`."""
    project.addMapLayer(layer)
    exported = read_project(project, FidelityReportBuilder())
    (read,) = exported.layers
    markup = build_label_element(read)
    assert markup, "the layer's labels were not exported"
    payload = json.loads(
        markup.split('<script type="application/json">')[1].split("</script>")[0]
    )
    return [tuple(f["geometry"]["coordinates"]) for f in payload["features"]]


@pytest.mark.parametrize("per_part", [False, True], ids=["once", "every-part"])
@pytest.mark.parametrize("kind", list(CASES))
def test_labels_land_where_qgis_draws_them(project, kind, per_part) -> None:
    layer = _labelled_layer(kind, per_part)
    qgis = _qgis_label_centres(layer)
    exported = _exported_anchors(project, layer)

    assert len(exported) == len(qgis), (
        f"QGIS draws {len(qgis)} label(s) for this {kind}, the export {len(exported)}"
    )
    # Horizontal line labels sit a hair off the line in QGIS (the text box's
    # centre, not its baseline); a tenth of a degree on a ten-degree view is
    # well inside one label's size and far from any other part.
    for x, y in qgis:
        nearest = min(math.hypot(x - ex, y - ey) for ex, ey in exported)
        assert nearest < 0.1, f"no exported label near QGIS's at ({x:.2f}, {y:.2f})"


def test_the_per_part_setting_changes_the_export_where_qgis_draws_more(
    project,
) -> None:
    """The control for the oracle above: the two settings really differ."""
    once = _qgis_label_centres(_labelled_layer("MultiPolygon", False))
    every = _qgis_label_centres(_labelled_layer("MultiPolygon", True))
    assert (len(once), len(every)) == (1, 2)
