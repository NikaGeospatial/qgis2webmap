"""A layout whose legend box is too small for the legend the web page draws:
categorised layers under a full-page map, a legend low on the page. Not
conftest.py, like cartograph_fixtures.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsRendererCategory,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from nika_onlymap_exporter.core.cartograph_export import export_layout_to_cartograph
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

MM = QgsUnitTypes.LayoutMillimeters
PAGE_HEIGHT = 210.0
CLASSES = ("Asphalt road", "Gravel road", "Cinder road", "Natural surface")


def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


@dataclass
class FitWorld:
    project: QgsProject
    layout: QgsPrintLayout
    map_item: QgsLayoutItemMap
    legend: QgsLayoutItemLegend
    layers: list[QgsVectorLayer]


def _layer(number: int) -> QgsVectorLayer:
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=c:string", f"Road network {number}", "memory"
    )
    features = []
    for index, value in enumerate(CLASSES):
        feature = QgsFeature(layer.fields())
        x = index * 200.0
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, 0, x + 150, 150)))
        feature.setAttributes([value])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    categories = [
        QgsRendererCategory(
            value,
            QgsFillSymbol.createSimple(
                {"color": QColor.fromHsv(40 * (i + 1), 200, 220).name()}
            ),
            value,
        )
        for i, value in enumerate(CLASSES)
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("c", categories))
    return layer


def build_fit_world(
    project: QgsProject,
    *,
    layer_count: int = 8,
    legend_rect: tuple[float, float, float, float] = (10, 150, 190, 40),
    columns: int = 3,
    auto_size: bool = False,
) -> FitWorld:
    """A 297 x 210 mm page, a live map over all of it, and a legend of
    `layer_count` four-class layers in `legend_rect` (x, y, w, h)."""
    from qgis.core import QgsCoordinateReferenceSystem

    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    layers = [_layer(n) for n in range(1, layer_count + 1)]
    # The first is on top, in the project's tree as in the map.
    for layer in reversed(layers):
        project.addMapLayer(layer)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("fit")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(0, 0, MM))
    map_item.attemptResize(QgsLayoutSize(297, 210, MM))
    map_item.setCrs(project.crs())
    map_item.setLayers(layers)
    map_item.setExtent(QgsRectangle(0, 0, 900, 150))
    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.setLinkedMap(map_item)
    legend.setResizeToContents(auto_size)
    legend.setColumnCount(columns)
    x, y, w, h = legend_rect
    legend.attemptMove(QgsLayoutPoint(x, y, MM))
    legend.attemptResize(QgsLayoutSize(w, h, MM))
    project.layoutManager().addLayout(layout)
    return FitWorld(project, layout, map_item, legend, layers)


def add_label(
    world: FitWorld, x: float, y: float, w: float, h: float
) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(world.layout)
    world.layout.addLayoutItem(label)
    label.setText("Title")
    label.attemptMove(QgsLayoutPoint(x, y, MM))
    label.attemptResize(QgsLayoutSize(w, h, MM))
    return label


def rect_mm(item) -> tuple[float, float, float, float]:
    """An item's x, y, w, h on the page in mm (layout units are mm here)."""
    pos = item.positionWithUnits()
    size = item.sizeWithUnits()
    return (pos.x(), pos.y(), size.width(), size.height())


def export(world: FitWorld, **kwargs: object) -> tuple[str, FidelityReportBuilder]:
    report = FidelityReportBuilder()
    html = export_layout_to_cartograph(
        world.layout, world.project, runtime(), report, **kwargs
    )
    return html, report


def page_legend_box(html: str) -> tuple[float, float, float, float]:
    """The first `<om-legend>`'s x, y, w, h in the page, in mm."""
    tag = re.search(r"<om-legend\b[^>]*>", html)
    assert tag is not None, "no legend on the page"
    values = []
    for name in "xywh":
        match = re.search(rf'\b{name}="([\d.\-]+)"', tag.group(0))
        assert match is not None, name
        values.append(float(match.group(1)))
    return (values[0], values[1], values[2], values[3])


def legend_details(report: FidelityReportBuilder) -> list[str]:
    return [i.detail for i in report.items if "web page's legend" in i.detail]
