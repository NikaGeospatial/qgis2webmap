"""Builders for the cartograph test tier's layout fixture.

Not `conftest.py`: a parallel branch owns `ui/`, `plugin.py`,
`core/settings.py` and `tests/qgis/conftest.py` itself, so this module lives
on the side and is imported explicitly by the cartograph test files, the same
way `tests/qgis/conftest.py` already notes other fixtures are exposed rather
than imported from a dotted path (`tests/` is not a package).

Per `tests/fixtures/README.md`'s own rule for this repository -- a project is
a script, not a committed binary, so it opens identically on every machine --
the survey layout here is built the same way: in-memory layers, a layout
built through PyQGIS, nothing written to disk except the one throwaway north-
arrow SVG a `QgsLayoutItemPicture` needs an actual file path for.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPicture,
    QgsLayoutItemPolygon,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
)

#: EPSG:32648 (UTM 48N) parcels, the same CRS/geometry shape as
#: `specs/cartograph/spikes/render.py`'s working static-frame spike, so a
#: test failure here is not explained by a fixture that never matched the
#: spike that proved the placement maths out.
UTM = QgsCoordinateReferenceSystem("EPSG:32648")

_NORTH_ARROW_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
    '<polygon points="12,2 18,22 12,17 6,22" fill="black"/></svg>'
)


@dataclass
class SurveyLayout:
    """Everything a cartograph test needs from the built fixture."""

    project: QgsProject
    layout: QgsPrintLayout
    parcels: QgsVectorLayer
    main_map: QgsLayoutItemMap
    inset_map: QgsLayoutItemMap
    legend: QgsLayoutItemLegend
    scalebar: QgsLayoutItemScaleBar
    north: QgsLayoutItemPicture
    title_label: QgsLayoutItemLabel
    shape: QgsLayoutItemShape
    unsupported: QgsLayoutItemPolygon


def build_parcels_layer() -> QgsVectorLayer:
    """A small in-memory polygon layer in UTM 48N -- the same geometry shape
    `specs/cartograph/spikes/render.py` used to prove the static-frame
    georeferencing out."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:32648", "parcels", "memory")
    provider = layer.dataProvider()
    features = []
    for i in range(3):
        for j in range(2):
            x0, y0 = 365000 + i * 400, 145000 + j * 300
            feature = QgsFeature()
            feature.setGeometry(
                QgsGeometry.fromRect(QgsRectangle(x0, y0, x0 + 350, y0 + 250))
            )
            features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    layer.renderer().setSymbol(
        QgsFillSymbol.createSimple({"color": "#eba941", "outline_color": "#222222"})
    )
    return layer


def build_survey_layout(project: QgsProject, tmp_path: Path) -> SurveyLayout:
    """A page-1 layout exercising every item type `core/layout_reader.py`
    translates, plus a second page and one explicitly unsupported item.

    `tmp_path` is pytest's own per-test scratch directory -- the throwaway
    north-arrow SVG lives there and is gone with the test, never committed.
    """
    project.setCrs(UTM)
    project.setTitle("Survey UTM")

    parcels = build_parcels_layer()
    project.addMapLayer(parcels)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("survey-utm")

    main_map = QgsLayoutItemMap(layout)
    layout.addLayoutItem(main_map)
    main_map.attemptMove(QgsLayoutPoint(10, 24, QgsUnitTypes.LayoutMillimeters))
    main_map.attemptResize(QgsLayoutSize(180, 140, QgsUnitTypes.LayoutMillimeters))
    main_map.setCrs(UTM)
    main_map.setLayers([parcels])
    extent = parcels.extent()
    extent.grow(150)
    main_map.setExtent(extent)  # read back below -- setExtent() resizes the item
    main_map.refresh()
    layout.setReferenceMap(main_map)

    grid = main_map.grids().asList()[0] if main_map.grids().asList() else None
    if grid is None:
        from qgis.core import QgsLayoutItemMapGrid

        grid = QgsLayoutItemMapGrid("grid-1", main_map)
        main_map.grids().addGrid(grid)
    grid.setEnabled(True)
    grid.setIntervalX(500)
    grid.setIntervalY(500)

    inset_map = QgsLayoutItemMap(layout)
    layout.addLayoutItem(inset_map)
    inset_map.attemptMove(QgsLayoutPoint(195, 24, QgsUnitTypes.LayoutMillimeters))
    inset_map.attemptResize(QgsLayoutSize(40, 40, QgsUnitTypes.LayoutMillimeters))
    inset_map.setCrs(UTM)
    inset_map.setLayers([parcels])
    inset_extent = parcels.extent()
    inset_extent.grow(5000)
    inset_map.setExtent(inset_extent)
    inset_map.refresh()

    overview = (
        inset_map.overviews().asList()[0] if inset_map.overviews().asList() else None
    )
    if overview is None:
        overview = QgsLayoutItemMapOverview("overview-1", inset_map)
        inset_map.overviews().addOverview(overview)
    overview.setLinkedMap(main_map)
    overview.setEnabled(True)

    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    legend.setLinkedMap(main_map)
    legend.attemptMove(QgsLayoutPoint(195, 70, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(40, 30, QgsUnitTypes.LayoutMillimeters))

    scalebar = QgsLayoutItemScaleBar(layout)
    layout.addLayoutItem(scalebar)
    scalebar.setLinkedMap(main_map)
    scalebar.setStyle("Single Box")
    scalebar.applyDefaultSize()
    scalebar.attemptMove(QgsLayoutPoint(10, 168, QgsUnitTypes.LayoutMillimeters))

    north_path = tmp_path / "north-arrow.svg"
    north_path.write_text(_NORTH_ARROW_SVG, encoding="utf-8")
    north = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north)
    north.setPicturePath(str(north_path))
    north.setLinkedMap(main_map)
    north.attemptMove(QgsLayoutPoint(195, 115, QgsUnitTypes.LayoutMillimeters))
    north.attemptResize(QgsLayoutSize(15, 15, QgsUnitTypes.LayoutMillimeters))

    title_label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(title_label)
    title_label.setText("[% @project_title %] -- scale 1:[% round(@map_scale) %]")
    title_label.attemptMove(QgsLayoutPoint(10, 5, QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(180, 12, QgsUnitTypes.LayoutMillimeters))

    shape = QgsLayoutItemShape(layout)
    layout.addLayoutItem(shape)
    shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    shape.attemptMove(QgsLayoutPoint(10, 168, QgsUnitTypes.LayoutMillimeters))
    shape.attemptResize(QgsLayoutSize(0, 0, QgsUnitTypes.LayoutMillimeters))
    shape.attemptMove(QgsLayoutPoint(160, 5, QgsUnitTypes.LayoutMillimeters))
    shape.attemptResize(QgsLayoutSize(20, 10, QgsUnitTypes.LayoutMillimeters))

    unsupported = QgsLayoutItemPolygon(layout)
    layout.addLayoutItem(unsupported)
    unsupported.attemptMove(QgsLayoutPoint(10, 190, QgsUnitTypes.LayoutMillimeters))
    unsupported.attemptResize(QgsLayoutSize(20, 10, QgsUnitTypes.LayoutMillimeters))

    layout.pageCollection().extendByNewPage()

    return SurveyLayout(
        project=project,
        layout=layout,
        parcels=parcels,
        main_map=main_map,
        inset_map=inset_map,
        legend=legend,
        scalebar=scalebar,
        north=north,
        title_label=title_label,
        shape=shape,
        unsupported=unsupported,
    )


def all_static(layout: QgsPrintLayout) -> dict[str, str]:
    """Frame modes making every map on `layout` static, for a test about
    static frames: a projected map of vectors would otherwise start live in
    its own CRS (`core/layout_reader.default_mode_for_map`)."""
    return {
        item.uuid(): "static"
        for item in layout.items()
        if isinstance(item, QgsLayoutItemMap)
    }
