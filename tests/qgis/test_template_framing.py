"""Fill framing preserves geography while keeping projected edges off-page."""

import pytest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpressionContextUtils,
    QgsPointXY,
    QgsRectangle,
)

from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view
from nika_onlymap_exporter.core.template_framing import (
    FRAMING_VARIABLE,
    fill_view,
    preview_framing,
    project_framing,
)


def test_whole_view_is_unchanged_by_trying_a_fill_preview(project):
    assert project_framing(project) == "whole"
    with preview_framing("fill"):
        assert project_framing(project) == "fill"
    assert project_framing(project) == "whole"
    assert not QgsExpressionContextUtils.projectScope(project).hasVariable(
        FRAMING_VARIABLE
    )


def test_vector_fill_crops_to_frame_aspect_without_recentring(project):
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    extent = QgsRectangle(0, 0, 200, 100)
    result = fill_view(extent, 1, crs, [], project)
    assert result.center() == extent.center()
    assert result.width() == pytest.approx(100)
    assert result.height() == pytest.approx(100)
    assert extent.contains(result)


@pytest.mark.parametrize("rotation", [0, 27, -35])
def test_projected_raster_fills_frame_without_changing_source(
    project, make_raster_layer, rotation
):
    raster = make_raster_layer(
        name="terrain",
        crs="EPSG:4326",
        west=-112.8,
        north=36.6,
        pixel_size=0.01,
        width=110,
        height=55,
    )
    project.addMapLayer(raster)
    crs = QgsCoordinateReferenceSystem("EPSG:32612")
    transform = QgsCoordinateTransform(raster.crs(), crs, project)
    extent = transform.transformBoundingBox(raster.extent())
    source = raster.source()
    with preview_framing("fill"):
        made = cartograph_from_view(
            extent,
            crs,
            [raster],
            project,
            template_id="legend-below",
            orientation="landscape",
            rotation=rotation,
        )
    item = made.layout.itemById("main")
    reverse = QgsCoordinateTransform(crs, raster.crs(), project)
    for point in item.visibleExtentPolygon():
        assert raster.extent().contains(reverse.transform(QgsPointXY(point)))
    assert item.mapRotation() == pytest.approx(rotation)
    assert item.crs() == crs
    assert raster.source() == source
    assert item.extent().width() < extent.width()


@pytest.mark.parametrize("template_id", ["classic", "report-figure"])
def test_fill_uses_page_space_while_whole_keeps_canvas_shape(project, template_id):
    extent = QgsRectangle(300000, 4000000, 320000, 4010000)
    crs = QgsCoordinateReferenceSystem("EPSG:32612")
    project.setTitle("Regional reference")
    with preview_framing("whole"):
        whole = cartograph_from_view(
            extent, crs, [], project, template_id=template_id, orientation="landscape"
        )
    with preview_framing("fill"):
        filled = cartograph_from_view(
            extent, crs, [], project, template_id=template_id, orientation="landscape"
        )
    whole_box, fill_box = whole.plan.frames[0].box, filled.plan.frames[0].box
    assert whole_box.w / whole_box.h == pytest.approx(2)
    assert fill_box.w * fill_box.h > whole_box.w * whole_box.h * 1.1
    item = filled.layout.itemById("main")
    assert item.extent().center().x() == pytest.approx(extent.center().x(), abs=0.01)
    assert item.extent().center().y() == pytest.approx(extent.center().y(), abs=0.01)
    tolerance = QgsRectangle(extent)
    tolerance.grow(0.001)  # CRS round trips can move an edge by a few nanometres.
    assert tolerance.contains(item.extent())
