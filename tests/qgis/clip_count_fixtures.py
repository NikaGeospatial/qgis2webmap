"""A layout whose map is clipped to a triangle, for the Live count clipping tests.

Two atlas pages: a triangle (0,0)-(10,0)-(0,10) and a square (20,0)-(30,10).
Two point layers, each with markers a different colour. `clipped` is the layer
the atlas clips; `free` is left out of the clipping (the atlas is set to clip
only `clipped`). Page one: `clipped` has (2,2) inside the triangle and (8,8)
outside it; `free` has (1,6) inside and (6,6) outside. Page two: one point of
each layer inside the square.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core import live_count as lc

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

TRIANGLE = "Polygon((0 0,10 0,0 10,0 0))"
SQUARE = "Polygon((20 0,30 0,30 10,20 10,20 0))"
CLIPPED_POINTS = ((2.0, 2.0), (8.0, 8.0), (22.0, 2.0))
FREE_POINTS = ((1.0, 6.0), (6.0, 6.0), (22.0, 7.0))
RED = (255, 0, 0)
BLUE = (0, 0, 255)


@dataclass
class ClipCounts:
    project: QgsProject
    layout: QgsPrintLayout
    clipped: QgsVectorLayer
    free: QgsVectorLayer
    coverage: QgsVectorLayer
    map_item: QgsLayoutItemMap
    clipped_total: QgsLayoutItemLabel
    free_total: QgsLayoutItemLabel


def point_layer(name: str, points, colour: str, crs: str = "EPSG:4326"):
    layer = QgsVectorLayer(f"Point?crs={crs}&field=name:string", name, "memory")
    features = []
    for index, (x, y) in enumerate(points):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"{name} {index}"])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    symbol = QgsMarkerSymbol.createSimple(
        {"color": colour, "outline_style": "no", "size": "4"}
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def coverage_layer(*wkts: str) -> QgsVectorLayer:
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326&field=name:string", "cov", "memory")
    features = []
    for index, wkt in enumerate(wkts):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"page {index}"])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def counter(layout, row: int, caption: str, follow, layer) -> QgsLayoutItemLabel:
    y = 130.0 + 12.0 * row
    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText(caption)
    label.attemptSetSceneRect(QRectF(10, y, 60, 8))
    number = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(number)
    number.setId(caption)
    number.attemptSetSceneRect(QRectF(80, y, 20, 8))
    lc.write_spec(
        number,
        lc.CountSpec(
            map_uuid=follow.uuid(), layer_id=layer.id(), layer_name=layer.name()
        ),
    )
    return number


def build_clip_counts(
    project: QgsProject, *, atlas_clipping: bool = True
) -> ClipCounts:
    clipped = point_layer("clipped", CLIPPED_POINTS, "#ff0000")
    free = point_layer("free", FREE_POINTS, "#0000ff")
    coverage = coverage_layer(TRIANGLE, SQUARE)
    project.addMapLayers([clipped, free, coverage])
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("clip counts")
    project.layoutManager().addLayout(layout)
    item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptSetSceneRect(QRectF(10, 10, 100, 100))
    item.setCrs(WGS84)
    item.setLayers([clipped, free])
    item.setKeepLayerSet(True)
    item.setId("map")
    item.setAtlasDriven(True)
    item.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
    item.setAtlasMargin(0.1)
    settings = item.atlasClippingSettings()
    settings.setEnabled(atlas_clipping)
    settings.setRestrictToLayers(True)
    settings.setLayersToClip([clipped])
    clipped_total = counter(layout, 0, "CLIPPED", item, clipped)
    free_total = counter(layout, 1, "FREE", item, free)
    atlas = layout.atlas()
    atlas.setCoverageLayer(coverage)
    atlas.setEnabled(True)
    atlas.setSortFeatures(True)
    atlas.setSortExpression("name")
    return ClipCounts(
        project, layout, clipped, free, coverage, item, clipped_total, free_total
    )
