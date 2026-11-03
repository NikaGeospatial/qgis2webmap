"""Line labels along the line, against where QGIS 4.2 itself draws them.

QGIS labels a line at half its length and, with "Repeat" set, again along it.
The expectation in each case is not a hand-worked coordinate: it is where
QGIS's labelling engine puts the labels on a render of the same layer, at the
scale the export fixes the repeat distance at (the layer's reference scale,
the layer's extent across 1000 x 700 pixels), read back with
`QgsLabelingResults`.

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

RU = qgis_core.Qgis.RenderUnit

# A 10 km line across Web Mercator at 50 degrees north, its vertices crowding
# its western end, which is where the middle vertex would put the label. The
# last vertex is 100 m north so the extent has a height; the reference scale is
# then 10 m (map units) to the pixel.
NORTH = 6_446_276.0
CROWDED = f"LineString(0 {NORTH}, 1000 {NORTH}, 2000 {NORTH}, 10000 {NORTH + 100})"


def _layer(wkt: str, crs: str, text: str = "X", **settings_values):
    layer = qgis_core.QgsVectorLayer(
        f"LineString?crs={crs}&field=name:string", "roads", "memory"
    )
    feature = qgis_core.QgsFeature(layer.fields())
    feature.setGeometry(qgis_core.QgsGeometry.fromWkt(wkt))
    feature["name"] = text
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = qgis_core.Qgis.LabelPlacement.Horizontal
    for name, value in settings_values.items():
        setattr(settings, name, value)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _qgis_label_centres(layer, crs: str) -> list[tuple[float, float]]:
    """Where QGIS draws `layer`'s labels with the layer filling 1000 x 700
    pixels at 96 dpi - the export's reference view - in `crs` units."""
    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(qgis_core.QgsCoordinateReferenceSystem(crs))
    settings.setOutputDpi(96)
    settings.setExtent(layer.extent())
    settings.setOutputSize(QtCore.QSize(1000, 700))
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    return sorted(
        (label.labelRect.center().x(), label.labelRect.center().y())
        for label in job.takeLabelingResults().allLabels()
        if not label.isUnplaced
    )


def _exported(project, layer, crs: str):
    """The label points of the exported map's TextLayer, in `crs` units, and
    the report."""
    project.setCrs(qgis_core.QgsCoordinateReferenceSystem(crs))
    project.addMapLayer(layer)
    report = FidelityReportBuilder()
    exported = read_project(project, report)
    (read,) = exported.layers
    markup = build_label_element(read)
    assert markup, "the layer's labels were not exported"
    payload = json.loads(
        markup.split('<script type="application/json">')[1].split("</script>")[0]
    )
    transform = qgis_core.QgsCoordinateTransform(
        qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"),
        qgis_core.QgsCoordinateReferenceSystem(crs),
        project.transformContext(),
    )
    points = []
    for feature in payload["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        point = transform.transform(qgis_core.QgsPointXY(lon, lat))
        points.append((point.x(), point.y()))
    return sorted(points), report


def _assert_same_places(qgis, exported, tolerance: float) -> None:
    assert len(exported) == len(qgis), (
        f"QGIS draws {len(qgis)} label(s), the export {len(exported)}: "
        f"{qgis} against {exported}"
    )
    for (qx, qy), (ex, ey) in zip(qgis, exported):
        assert math.hypot(qx - ex, qy - ey) < tolerance, (
            f"QGIS labels ({qx:.1f}, {qy:.1f}), the export ({ex:.1f}, {ey:.1f})"
        )


# A label sits on a horizontal line in QGIS, at the middle of its piece of the
# line or within a few pixels of it, where QGIS's candidate positions along a
# short piece are coarse; 5 px at 10 m to the pixel.
TOLERANCE_M = 50.0

REPEATS = {
    "none": {},
    "2000 map units": {"repeatDistance": 2000.0, "repeatDistanceUnit": RU.MapUnits},
    "3000 map units": {"repeatDistance": 3000.0, "repeatDistanceUnit": RU.MapUnits},
    "30 mm": {"repeatDistance": 30.0, "repeatDistanceUnit": RU.Millimeters},
    "300 px": {"repeatDistance": 300.0, "repeatDistanceUnit": RU.Pixels},
    "50 pt": {"repeatDistance": 50.0, "repeatDistanceUnit": RU.Points},
    "1.2 in": {"repeatDistance": 1.2, "repeatDistanceUnit": RU.Inches},
    "2000 m on the ground": {
        "repeatDistance": 2000.0,
        "repeatDistanceUnit": RU.MetersInMapUnits,
    },
}


@pytest.mark.parametrize("repeat", list(REPEATS))
def test_web_mercator_labels_land_where_qgis_draws_them(project, repeat) -> None:
    """In EPSG:3857, with and without a repeat in each unit."""
    values = REPEATS[repeat]
    layer = _layer(CROWDED, "EPSG:3857", **values)
    qgis = _qgis_label_centres(layer, "EPSG:3857")
    exported, _ = _exported(project, layer, "EPSG:3857")
    if not values:
        # No repeat in QGIS: one label, at half the line's length; the export
        # adds its own repeats either side (`test_the_default_*` below).
        assert len(qgis) == 1
        assert min(math.dist(qgis[0], point) for point in exported) < TOLERANCE_M
        return
    assert len(qgis) > 1, "the case must make QGIS repeat"
    _assert_same_places(qgis, exported, TOLERANCE_M)


def test_a_long_label_stretches_the_repeat_as_qgis_does(project) -> None:
    """A repeat shorter than the label: QGIS repeats every whole multiple of
    it that the label fits in, here twice the distance."""
    layer = _layer(
        CROWDED,
        "EPSG:3857",
        text="A long label text here",
        repeatDistance=1000.0,
        repeatDistanceUnit=RU.MapUnits,
    )
    qgis = _qgis_label_centres(layer, "EPSG:3857")
    exported, _ = _exported(project, layer, "EPSG:3857")
    assert len(qgis) == 5
    _assert_same_places(qgis, exported, TOLERANCE_M)


def _size_limit():
    scale = qgis_core.QgsMapUnitScale()
    scale.minSizeMMEnabled = True
    scale.minSizeMM = 50.0
    return scale


def _scale_limit():
    # Drawn at about 1:37,800, past this 1:20,000 limit: map units are sized
    # as they would be at 1:20,000.
    return qgis_core.QgsMapUnitScale(20_000.0, 0.0)


@pytest.mark.parametrize(
    "make_scale", [_size_limit, _scale_limit], ids=["min-size", "min-scale"]
)
def test_the_map_unit_scale_applies_as_in_qgis(project, make_scale) -> None:
    """A map unit scale on the repeat distance: QGIS 4.2 repeats every 1000
    map units with a 50 mm minimum (189 px against 100) or a scale limit set
    as without, and so does the export."""
    layer = _layer(
        CROWDED,
        "EPSG:3857",
        repeatDistance=1000.0,
        repeatDistanceUnit=RU.MapUnits,
        repeatDistanceMapUnitScale=make_scale(),
    )
    qgis = _qgis_label_centres(layer, "EPSG:3857")
    exported, _ = _exported(project, layer, "EPSG:3857")
    _assert_same_places(qgis, exported, TOLERANCE_M)


# QGIS measures a line in EPSG:4326 as a flat plane of degrees.
DIAGONAL = "LineString(0 50, 1 50, 10 60)"


@pytest.mark.parametrize(
    "values",
    [{}, {"repeatDistance": 3.0, "repeatDistanceUnit": RU.MapUnits}],
    ids=["once", "every-3-degrees"],
)
def test_a_geographic_project_measures_in_degrees_like_qgis(project, values) -> None:
    layer = _layer(DIAGONAL, "EPSG:4326", **values)
    qgis = _qgis_label_centres(layer, "EPSG:4326")
    exported, _ = _exported(project, layer, "EPSG:4326")
    # The label box's centre sits a little off a sloping line in QGIS; a
    # tenth of a degree on a 13-degree line.
    if not values:
        assert len(qgis) == 1
        assert min(math.dist(qgis[0], point) for point in exported) < 0.1
        return
    assert len(qgis) == 4
    _assert_same_places(qgis, exported, 0.1)


def test_the_default_repeat_keeps_qgiss_label_and_adds_others(project) -> None:
    """No repeat in QGIS. At 10 m to the pixel the default 250 px is 2500 m:
    QGIS's one label at 5000 m, and two more 2500 m either side of it."""
    layer = _layer(CROWDED, "EPSG:3857")
    (qgis,) = _qgis_label_centres(layer, "EPSG:3857")
    exported, report = _exported(project, layer, "EPSG:3857")
    assert [round(x, -1) for x, _ in exported] == [2500.0, 5000.0, 7500.0]
    assert math.dist(qgis, exported[1]) < TOLERANCE_M
    details = " ".join(item.detail for item in report.items)
    assert "repeats the label every 250 pixels" in details


def test_a_line_short_beside_its_layer_keeps_one_label(project) -> None:
    """Under three default spacings long, a line is labelled once, as in
    QGIS. The spacing follows the layer's reference view, so a 1 km street
    beside the 10 km road keeps its one label at its middle."""
    layer = _layer(CROWDED, "EPSG:3857")
    street = qgis_core.QgsFeature(layer.fields())
    street.setGeometry(
        qgis_core.QgsGeometry.fromWkt(f"LineString(0 {NORTH + 50}, 1000 {NORTH + 50})")
    )
    street["name"] = "Y"
    layer.dataProvider().addFeatures([street])
    layer.updateExtents()
    exported, _ = _exported(project, layer, "EPSG:3857")
    on_street = [x for x, y in exported if abs(y - (NORTH + 50)) < 1]
    assert [round(x) for x in on_street] == [500]
    assert len(exported) == 4


def test_the_qgis_repeat_is_reported(project) -> None:
    layer = _layer(
        CROWDED, "EPSG:3857", repeatDistance=30.0, repeatDistanceUnit=RU.Millimeters
    )
    _, report = _exported(project, layer, "EPSG:3857")
    details = " ".join(item.detail for item in report.items)
    assert "every 30 mm" in details


def test_a_cartograph_live_frame_repeats_the_labels(project) -> None:
    """A cartograph's live frame reads its layers through the same reader, so
    its line labels repeat as on a web map."""
    from nika_onlymap_exporter.core.cartograph_export import (
        finish_cartograph_full,
        prepare_cartograph,
        prepare_live_reads,
    )
    from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

    project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    layer = _layer(
        CROWDED, "EPSG:3857", repeatDistance=2000.0, repeatDistanceUnit=RU.MapUnits
    )
    project.addMapLayer(layer)
    layout = qgis_core.QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("repeats")
    map_item = qgis_core.QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    millimetres = qgis_core.QgsUnitTypes.LayoutMillimeters
    map_item.attemptMove(qgis_core.QgsLayoutPoint(10, 10, millimetres))
    map_item.attemptResize(qgis_core.QgsLayoutSize(150, 100, millimetres))
    map_item.setCrs(project.crs())
    map_item.setLayers([layer])
    extent = layer.extent()
    extent.grow(500)
    map_item.setExtent(extent)
    runtime = RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(layout, project, runtime, report, raster_cache=None)
    assert prepared.live_frames, "the map must export as a live frame"
    prepared = prepare_live_reads(prepared, project, report)
    finished = finish_cartograph_full(prepared, None, report)

    (read,) = [layer for frame in finished.projects for layer in frame.layers]
    assert read.labeling.repeat is not None and read.labeling.repeat.from_qgis
    payload = json.loads(
        build_label_element(read)
        .split('<script type="application/json">')[1]
        .split("</script>")[0]
    )
    assert len(payload["features"]) == 5
    assert f'id="{read.layer_id}-labels"' in finished.html
