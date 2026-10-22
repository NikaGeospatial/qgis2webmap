"""Builders for the cartograph atlas tests.

A new file rather than `cartograph_fixtures.py` or `conftest.py`, per the
shared rule for this test tier: three parcels in EPSG:3857 with a `lot` field
(the coverage layer), a roads layer, and a layout whose main map follows the
atlas. Everything is built by script; nothing is written to disk.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayoutAtlas,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLineSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
)

WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")

#: (lot, area class, x offset, width, height) -- three parcels of different
#: shapes, so a per-feature refit visibly changes the camera.
PARCELS = (
    ("C-3", "small", 0, 200, 800),
    ("A-1", "large", 5000, 1000, 500),
    ("B-2", "small", 10000, 400, 400),
)


@dataclass
class AtlasLayout:
    project: QgsProject
    layout: QgsPrintLayout
    atlas: QgsLayoutAtlas
    parcels: QgsVectorLayer
    roads: QgsVectorLayer
    main_map: QgsLayoutItemMap
    label: QgsLayoutItemLabel


def build_parcels(name: str = "parcels") -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=lot:string&field=size:string&field=area:double",
        name,
        "memory",
    )
    features = []
    for lot, size, x, width, height in PARCELS:
        feature = QgsFeature(layer.fields())
        feature.setAttribute("lot", lot)
        feature.setAttribute("size", size)
        feature.setAttribute("area", float(width * height))
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, 0, x + width, height)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
    return layer


def build_roads() -> QgsVectorLayer:
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "roads", "memory")
    feature = QgsFeature()
    feature.setGeometry(
        QgsGeometry.fromPolylineXY([QgsPointXY(-500, -100), QgsPointXY(11000, -100)])
    )
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#444444"}))
    return layer


def add_map(
    layout: QgsPrintLayout,
    layers: list,
    *,
    x: float = 10,
    y: float = 20,
    w: float = 150,
    h: float = 120,
) -> QgsLayoutItemMap:
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(WEB_MERCATOR)
    map_item.setLayers(layers)
    extent = QgsRectangle(layers[0].extent())
    extent.grow(300)
    map_item.setExtent(extent)
    map_item.refresh()
    return map_item


def build_atlas_layout(project: QgsProject) -> AtlasLayout:
    """A live, atlas-driven main map over the parcels (Auto scaling, 10%
    margin), the atlas on and filenames `'lot_' || "lot"`, and one label
    reading the current feature."""
    project.setCrs(WEB_MERCATOR)
    project.setTitle("Parcel atlas")
    parcels = build_parcels()
    roads = build_roads()
    project.addMapLayers([parcels, roads])

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("parcel-atlas")
    main_map = add_map(layout, [parcels, roads])
    main_map.setAtlasDriven(True)
    main_map.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
    main_map.setAtlasMargin(0.10)
    layout.setReferenceMap(main_map)

    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText(
        'Lot [% "lot" %] -- page [% @atlas_featurenumber %] of '
        "[% @atlas_totalfeatures %]"
    )
    label.attemptMove(QgsLayoutPoint(10, 5, QgsUnitTypes.LayoutMillimeters))
    label.attemptResize(QgsLayoutSize(150, 10, QgsUnitTypes.LayoutMillimeters))

    atlas = layout.atlas()
    atlas.setCoverageLayer(parcels)
    atlas.setEnabled(True)
    atlas.setFilenameExpression("'lot_' || \"lot\"")
    atlas.setPageNameExpression("lot")
    return AtlasLayout(
        project=project,
        layout=layout,
        atlas=atlas,
        parcels=parcels,
        roads=roads,
        main_map=main_map,
        label=label,
    )


#: (lot, rank, score): text and integer fields with NULLs, two per column and
#: not on the same features, so a filter or sort over either has to decide
#: what NULL means. `rank` mixes case and numbers in text ("b10" < "b9" as
#: text, "B2" between "a1" and "b10" for a case-insensitive numeric sort).
RANKED = (
    ("p1", "b10", 1),
    ("p2", None, None),
    ("p3", "B2", 3),
    ("p4", "a1", 2),
    ("p5", None, 5),
    ("p6", "b9", None),
)


def build_ranked_parcels(name: str = "ranked") -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=lot:string&field=rank:string&field=score:integer",
        name,
        "memory",
    )
    features = []
    for index, (lot, rank, score) in enumerate(RANKED):
        feature = QgsFeature(layer.fields())
        feature.setAttribute("lot", lot)
        feature.setAttribute("rank", rank)
        feature.setAttribute("score", score)
        x = index * 1000
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, 0, x + 400, 400)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
    return layer


def use_ranked_parcels(built: AtlasLayout) -> QgsVectorLayer:
    """Make the atlas page the ranked parcels instead of the plain ones."""
    ranked = build_ranked_parcels()
    built.project.addMapLayer(ranked)
    built.main_map.setLayers([ranked, built.roads])
    built.atlas.setCoverageLayer(ranked)
    built.atlas.setFilenameExpression("'lot_' || \"lot\"")
    built.atlas.setPageNameExpression("lot")
    return ranked


def qgis_atlas_page_ids(layout: QgsPrintLayout) -> list[int]:
    """The feature ids of the pages QGIS's own atlas makes, in order: the
    oracle for what the cartograph must page."""
    atlas = layout.atlas()
    if not atlas.beginRender():  # nothing begins an atlas with no pages
        assert atlas.count() == 0
        return []
    try:
        ids = []
        for index in range(atlas.count()):
            assert atlas.seekTo(index)
            ids.append(int(layout.reportContext().feature().id()))
    finally:
        atlas.endRender()
    return ids
