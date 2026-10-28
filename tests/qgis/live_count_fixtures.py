"""A small dataset whose counts can be worked out by hand, for the Live count
tests.

Thirty sites on a regular grid: six columns (x = 0, 2, ... 10) by five rows
(y = 0, 2, ... 8). Their Status is Active for 17, Closed for 10, empty text for
1 and NULL for 2; `cap` (a whole number) is 5 for 5 sites, NULL for 4 and 10
for the rest. A second layer holds three squares.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsRectangle,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core import live_count as lc

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: The grid: (x, y, status, cap) for each site, in a fixed order.
SITES: list[tuple[float, float, str | None, int | None]] = []
for _i in range(30):
    _col, _row = _i % 6, _i // 6
    if _i in (7, 20):
        _status: str | None = None
    elif _i == 13:
        _status = ""
    elif _i % 3 == 0:
        _status = "Closed"
    else:
        _status = "Active"
    _cap: int | None = None if _i in (1, 9, 17, 25) else (5 if _i % 5 == 0 else 10)
    SITES.append((_col * 2.0, _row * 2.0, _status, _cap))


def count_sites(
    extent: QgsRectangle | None = None, status: object = "any", cap: object = "any"
) -> int:
    """The expected count, worked out from `SITES` alone (not from QGIS)."""
    total = 0
    for x, y, site_status, site_cap in SITES:
        if extent is not None and not (
            extent.xMinimum() <= x <= extent.xMaximum()
            and extent.yMinimum() <= y <= extent.yMaximum()
        ):
            continue
        if status != "any" and site_status != status:
            continue
        if cap != "any" and site_cap != cap:
            continue
        total += 1
    return total


def build_sites() -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=status:string&field=cap:integer",
        "Monitoring sites",
        "memory",
    )
    assert layer.isValid()
    features = []
    for index, (x, y, status, cap) in enumerate(SITES):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"Site {index}", status, cap])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    layer.setRenderer(
        QgsCategorizedSymbolRenderer(
            "status",
            [
                QgsRendererCategory(
                    "Active", QgsMarkerSymbol.createSimple({}), "Active"
                ),
                QgsRendererCategory(
                    "Closed", QgsMarkerSymbol.createSimple({}), "Closed"
                ),
                QgsRendererCategory(
                    None, QgsMarkerSymbol.createSimple({}), "No status"
                ),
                QgsRendererCategory("", QgsMarkerSymbol.createSimple({}), "Empty"),
            ],
        )
    )
    return layer


def build_squares() -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:4326&field=name:string", "Squares", "memory"
    )
    assert layer.isValid()
    features = []
    for name, (x0, y0, x1, y1) in {
        "west": (-4.0, 1.0, -2.0, 3.0),
        "middle": (3.0, 3.0, 5.0, 5.0),
        "wide": (-6.0, 6.0, 15.0, 7.0),
    }.items():
        feature = QgsFeature(layer.fields())
        feature.setAttributes([name])
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x0, y0, x1, y1)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


@dataclass
class CountPage:
    project: object
    layout: QgsPrintLayout
    sites: QgsVectorLayer
    squares: QgsVectorLayer
    frame: QgsLayoutItemMap


#: Exactly the shape of the frame below (120 x 100 mm), so a view of this
#: extent is not padded: the whole grid and a margin.
WHOLE = QgsRectangle(-1, -1, 11, 9)


def on_disk(layer: QgsVectorLayer, folder: Path) -> QgsVectorLayer:
    """`layer` saved to a GeoPackage in `folder` and loaded from there, so a
    saved project can find it again (a memory layer does not survive)."""
    from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter

    path = str(folder / f"{layer.name().replace(' ', '_')}.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer.name()
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, QgsCoordinateTransformContext(), options
    )
    assert result[0] == QgsVectorFileWriter.WriterError.NoError, result
    loaded = QgsVectorLayer(f"{path}|layername={layer.name()}", layer.name(), "ogr")
    assert loaded.isValid()
    loaded.setRenderer(layer.renderer().clone())
    return loaded


def build_page(
    project, name: str = "Count page", folder: Path | None = None
) -> CountPage:
    sites, squares = build_sites(), build_squares()
    if folder is not None:
        sites, squares = on_disk(sites, folder), on_disk(squares, folder)
    project.addMapLayer(squares)
    project.addMapLayer(sites)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    project.layoutManager().addLayout(layout)
    frame = QgsLayoutItemMap(layout)
    frame.setId("Main map")
    layout.addLayoutItem(frame)
    frame.attemptSetSceneRect(QRectF(10, 10, 120, 100))
    frame.setCrs(WGS84)
    frame.zoomToExtent(WHOLE)
    return CountPage(project, layout, sites, squares, frame)


def add_counter(
    page: CountPage,
    layer: QgsVectorLayer | None = None,
    frame: QgsLayoutItemMap | None = None,
    mode: str = lc.MODE_ALL,
    field: str = "",
    value: str | None = None,
    null: bool = False,
) -> QgsLayoutItemLabel:
    """A configured counter's number label on the page (no caption or rule)."""
    layer = layer or page.sites
    frame = frame or page.frame
    number = QgsLayoutItemLabel(page.layout)
    number.setId("Live count")
    page.layout.addLayoutItem(number)
    number.attemptSetSceneRect(QRectF(140, 10, 40, 14))
    lc.write_spec(
        number,
        lc.CountSpec(
            map_uuid=frame.uuid(),
            layer_id=layer.id(),
            layer_name=layer.name(),
            mode=mode,
            field=field,
            value=value,
            value_is_null=null,
        ),
    )
    return number


def count_of(page: CountPage, number: QgsLayoutItemLabel) -> lc.CountOutcome:
    return lc.evaluate(page.layout, number)
