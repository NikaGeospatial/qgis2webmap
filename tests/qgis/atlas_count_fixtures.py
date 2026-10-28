"""A layout whose Live counts differ from atlas page to atlas page.

Four sites and two atlas areas: area A holds one site, area B three. An
overview map shows everything (fixed); a detail map follows the atlas. Three
counters sit on the page, each beside a caption so the exported PDF's text can
be read line by line: the overview's total, the detail map's total, and the
detail map's sites of one kind.

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
    QgsLayoutItemPage,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core import live_count as lc

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: (x, y, kind): site 0 is in area A, sites 1-3 in area B.
SITES = ((1.0, 1.0, "x"), (11.0, 1.0, "x"), (12.0, 2.0, "y"), (13.0, 3.0, "y"))
AREAS = (("A", (0.0, 0.0, 3.0, 3.0)), ("B", (10.0, 0.0, 15.0, 5.0)))
WHOLE = QgsRectangle(-1.0, -1.0, 16.0, 6.0)


@dataclass
class AtlasCounts:
    project: QgsProject
    layout: QgsPrintLayout
    sites: QgsVectorLayer
    areas: QgsVectorLayer
    overview: QgsLayoutItemMap
    detail: QgsLayoutItemMap
    overview_total: QgsLayoutItemLabel
    detail_total: QgsLayoutItemLabel
    detail_kind_y: QgsLayoutItemLabel


def _layers() -> tuple[QgsVectorLayer, QgsVectorLayer]:
    sites = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=kind:string", "sites", "memory"
    )
    features = []
    for index, (x, y, kind) in enumerate(SITES):
        feature = QgsFeature(sites.fields())
        feature.setAttributes([f"site {index}", kind])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    sites.dataProvider().addFeatures(features)
    sites.updateExtents()
    areas = QgsVectorLayer("Polygon?crs=EPSG:4326&field=name:string", "areas", "memory")
    features = []
    for name, rect in AREAS:
        feature = QgsFeature(areas.fields())
        feature.setAttributes([name])
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(*rect)))
        features.append(feature)
    areas.dataProvider().addFeatures(features)
    areas.updateExtents()
    return sites, areas


def _map(layout, rect, layers) -> QgsLayoutItemMap:
    item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptSetSceneRect(QRectF(*rect))
    item.setCrs(WGS84)
    item.setLayers(layers)
    item.zoomToExtent(WHOLE)
    return item


def _counter(
    layout, row: int, caption: str, follow, layer, **spec
) -> QgsLayoutItemLabel:
    """A caption on the left and the counter's number on the right, same row."""
    y = 100.0 + 12.0 * row
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
            map_uuid=follow.uuid(), layer_id=layer.id(), layer_name=layer.name(), **spec
        ),
    )
    return number


def build_atlas_counts(
    project: QgsProject, *, second_sheet: bool = False
) -> AtlasCounts:
    """The layout; with `second_sheet`, each atlas feature makes two sheets and
    the second one carries a counter of its own (the detail map's total)."""
    sites, areas = _layers()
    project.addMapLayers([sites, areas])
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("atlas counts")
    project.layoutManager().addLayout(layout)
    overview = _map(layout, (5, 5, 130, 80), [sites])
    overview.setId("overview")
    detail = _map(layout, (150, 5, 130, 80), [sites])
    detail.setId("detail")
    detail.setAtlasDriven(True)
    detail.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Auto)
    detail.setAtlasMargin(0.1)
    overview_total = _counter(layout, 0, "OVERVIEW", overview, sites)
    detail_total = _counter(layout, 1, "DETAIL", detail, sites)
    detail_kind_y = _counter(
        layout, 2, "KINDY", detail, sites, mode=lc.MODE_VALUE, field="kind", value="y"
    )
    if second_sheet:
        second = QgsLayoutItemPage(layout)
        second.setPageSize("A4", QgsLayoutItemPage.Orientation.Landscape)
        layout.pageCollection().addPage(second)
        sheet_two = layout.pageCollection().page(1).pos().y()
        extra = QgsLayoutItemLabel(layout)
        layout.addLayoutItem(extra)
        extra.setText("SECONDSHEET")
        extra.attemptSetSceneRect(QRectF(10, sheet_two + 20, 60, 8))
        number = QgsLayoutItemLabel(layout)
        layout.addLayoutItem(number)
        number.setId("SECONDSHEET")
        number.attemptSetSceneRect(QRectF(80, sheet_two + 20, 20, 8))
        lc.write_spec(
            number,
            lc.CountSpec(
                map_uuid=detail.uuid(), layer_id=sites.id(), layer_name=sites.name()
            ),
        )
    atlas = layout.atlas()
    atlas.setCoverageLayer(areas)
    atlas.setEnabled(True)
    atlas.setSortFeatures(True)
    atlas.setSortExpression("name")
    return AtlasCounts(
        project,
        layout,
        sites,
        areas,
        overview,
        detail,
        overview_total,
        detail_total,
        detail_kind_y,
    )
