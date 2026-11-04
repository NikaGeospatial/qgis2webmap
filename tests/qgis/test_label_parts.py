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


@pytest.mark.parametrize(
    ("project_crs", "map_crs"),
    [
        ("EPSG:32633", None),
        ("EPSG:3035", None),
        ("EPSG:4326", "EPSG:3857"),
        ("EPSG:3857", "EPSG:4326"),
    ],
    ids=["utm", "laea-europe", "frame-in-mercator", "frame-in-degrees"],
)
def test_a_line_label_sits_where_qgis_draws_it_in_its_map_crs(
    project, project_crs, map_crs
) -> None:
    """QGIS places a line's label at half its length as measured in the CRS
    it draws the line in: the project's, or a cartograph frame's own. On a
    line from 40 to 70 degrees north the planes disagree by degrees."""
    from nika_onlymap_exporter.core.manifest_builder import build_label_element
    from nika_onlymap_exporter.core.project_reader import (
        finish_project,
        prepare_project,
    )

    project.setCrs(qgis_core.QgsCoordinateReferenceSystem(project_crs))
    drawn_in = qgis_core.QgsCoordinateReferenceSystem(map_crs or project_crs)
    layer = qgis_core.QgsVectorLayer(
        "LineString?crs=EPSG:4326&field=name:string", "meridian", "memory"
    )
    feature = qgis_core.QgsFeature(layer.fields())
    # Along 15 E, the middle of UTM zone 33.
    feature.setGeometry(qgis_core.QgsGeometry.fromWkt("LineString(15 40, 15 70)"))
    feature["name"] = "X"
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = qgis_core.Qgis.LabelPlacement.Horizontal
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    project.addMapLayer(layer)

    map_settings = qgis_core.QgsMapSettings()
    map_settings.setLayers([layer])
    map_settings.setDestinationCrs(drawn_in)
    to_map = qgis_core.QgsCoordinateTransform(
        layer.crs(), drawn_in, project.transformContext()
    )
    extent = to_map.transformBoundingBox(qgis_core.QgsRectangle(10, 38, 20, 72))
    map_settings.setExtent(extent)
    map_settings.setOutputSize(QtCore.QSize(1000, 1000))
    job = qgis_core.QgsMapRendererSequentialJob(map_settings)
    job.start()
    job.waitForFinished()
    (label,) = [
        label for label in job.takeLabelingResults().allLabels() if not label.isUnplaced
    ]
    qgis_point = to_map.transform(
        qgis_core.QgsPointXY(label.labelRect.center()),
        qgis_core.QgsCoordinateTransform.TransformDirection.ReverseTransform,
    )

    report = FidelityReportBuilder()
    pending = prepare_project(
        project, report, map_crs=None if map_crs is None else drawn_in
    )
    (read,) = finish_project(pending, report).layers
    markup = build_label_element(read)
    payload = json.loads(
        markup.split('<script type="application/json">')[1].split("</script>")[0]
    )
    ((lon, lat),) = [tuple(f["geometry"]["coordinates"]) for f in payload["features"]]
    # A label's height off its anchor is under 0.15 degrees here (QGIS 3.44
    # sets the text box a little lower than 4.2); the planes put it 0.4
    # (degrees and LAEA) to 3.3 degrees (Mercator) apart.
    # Off 15 E in LAEA, whose straight line between the ends is not the
    # meridian: QGIS draws that line, and labels it.
    assert abs(lat - qgis_point.y()) < 0.2, (lat, qgis_point.y())
    assert abs(lon - qgis_point.x()) < 0.2, (lon, qgis_point.x())
