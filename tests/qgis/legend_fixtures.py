"""Layers and a template cartograph for the legend tests: a hillshade and an
elevation ramp (real GeoTIFFs), a categorised layer of 25 classes and a
single-symbol point layer. Not conftest.py, like cartograph_fixtures.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRectangle,
    QgsRendererCategory,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from nika_onlymap_exporter.core.canvas_cartograph import (
    CanvasCartograph,
    cartograph_from_view,
)

CLASS_COUNT = 25
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
EXTENT = QgsRectangle(0, 0, 10, 10)


def write_tif(
    path: Path, *, unit: str = "", values: tuple[float, float] = (0.0, 2000.0)
) -> Path:
    import numpy
    from osgeo import gdal, osr

    gdal.UseExceptions()

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 20, 20, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((0, 0.5, 0, 10, 0, -0.5))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    low, high = values
    grid = numpy.add.outer(numpy.arange(20), numpy.arange(20)) / 38
    band.WriteArray((low + (high - low) * grid).astype("float32"))
    if unit:
        band.SetUnitType(unit)
    band.FlushCache()
    dataset = None
    return path


def ramp_layer(path: Path, name: str, *, nan_range: bool = False) -> QgsRasterLayer:
    layer = QgsRasterLayer(str(path), name)
    assert layer.isValid()
    shader = QgsColorRampShader()
    shader.setColorRampType(QgsColorRampShader.Interpolated)
    shader.setColorRampItemList(
        [
            QgsColorRampShader.ColorRampItem(0, QColor("#2b8cbe")),
            QgsColorRampShader.ColorRampItem(2000, QColor("#f03b20")),
        ]
    )
    low, high = (float("nan"),) * 2 if nan_range else (0.0, 2000.0)
    shader.setMinimumValue(low)
    shader.setMaximumValue(high)
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)
    return layer


def hillshade_layer(path: Path, name: str = "Hillshade") -> QgsRasterLayer:
    layer = QgsRasterLayer(str(path), name)
    assert layer.isValid()
    layer.setRenderer(QgsSingleBandGrayRenderer(layer.dataProvider(), 1))
    return layer


def to_gpkg(layer: QgsVectorLayer, folder: Path) -> QgsVectorLayer:
    """The layer on disk, so a saved project can open it again."""
    path = folder / f"{layer.name().replace(' ', '_')}.gpkg"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer.name()
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(path), QgsProject.instance().transformContext(), options
    )
    assert result[0] == QgsVectorFileWriter.NoError, result
    stored = QgsVectorLayer(f"{path}|layername={layer.name()}", layer.name(), "ogr")
    assert stored.isValid()
    stored.setRenderer(layer.renderer().clone())
    return stored


def categorised_layer(
    name: str = "Land cover", classes: int = CLASS_COUNT
) -> QgsVectorLayer:
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326&field=c:integer", name, "memory")
    features = []
    for index in range(classes):
        feature = QgsFeature(layer.fields())
        x = index % 5 * 2
        y = index // 5 * 2
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x, y, x + 2, y + 2)))
        feature.setAttributes([index])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    categories = [
        QgsRendererCategory(
            index,
            QgsFillSymbol.createSimple(
                {"color": QColor.fromHsv(index * 14, 200, 220).name()}
            ),
            f"Class {index + 1:02d}",
        )
        for index in range(classes)
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("c", categories))
    return layer


def summits_layer(name: str = "Named summits") -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=n:string", name, "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5, 5)))
    layer.dataProvider().addFeatures([feature])
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({"color": "#aa0000"}))
    return layer


@dataclass
class LegendWorld:
    project: QgsProject
    layout: QgsPrintLayout
    built: CanvasCartograph
    layers: dict[str, object]


def build_world(
    project: QgsProject, folder: Path, *, classes: int = CLASS_COUNT
) -> LegendWorld:
    folder.mkdir(parents=True, exist_ok=True)
    elevation = ramp_layer(
        write_tif(folder / "elevation.tif", unit="m"), "Elevation tint"
    )
    # A name that already states its unit, as the Grand Canyon project does.
    elevation_named = ramp_layer(write_tif(folder / "elev2.tif"), "Relief height (m)")
    hillshade = hillshade_layer(write_tif(folder / "hillshade.tif"))
    land = to_gpkg(categorised_layer(classes=classes), folder)
    summits = to_gpkg(summits_layer(), folder)
    layers = {
        "summits": summits,
        "land": land,
        "elevation": elevation,
        "named": elevation_named,
        "hillshade": hillshade,
    }
    for layer in layers.values():
        project.addMapLayer(layer)
    project.setTitle("Legend test")
    built = cartograph_from_view(
        EXTENT, WGS84, list(layers.values()), project, template_id="classic"
    )
    return LegendWorld(project, built.layout, built, layers)
