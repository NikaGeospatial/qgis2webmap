"""Smoothing a continuous raster when it is enlarged: one map item's own
choice, never the layer's, and never for classes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.raster_smoothing import (
    is_continuous_raster,
    set_smoothing,
    smoothable_layers,
    smoothing_on,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtGui import QColor  # noqa: E402


def _gradient_raster(tmp_path, name: str = "ramp"):
    """A 4 x 4 single-band raster whose values rise from left to right."""
    gdal = pytest.importorskip("osgeo.gdal")
    gdal.UseExceptions()
    path = tmp_path / f"{name}.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 1, gdal.GDT_Byte)
    dataset.SetGeoTransform([0.0, 1.0, 0.0, 4.0, 0.0, -1.0])
    dataset.SetProjection(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326").toWkt())
    dataset.GetRasterBand(1).WriteArray(
        __import__("numpy").tile(__import__("numpy").array([0, 85, 170, 255]), (4, 1))
    )
    dataset = None
    layer = qgis_core.QgsRasterLayer(str(path), name, "gdal")
    assert layer.isValid()
    return layer


def _paletted(layer):
    classes = [
        qgis_core.QgsPalettedRasterRenderer.Class(
            value, QColor(value, 0, 0), str(value)
        )
        for value in (0, 85, 170, 255)
    ]
    layer.setRenderer(
        qgis_core.QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
    )
    return layer


def _discrete_ramp(layer):
    shader = qgis_core.QgsRasterShader()
    ramp = qgis_core.QgsColorRampShader()
    ramp.setColorRampType(
        qgis_core.Qgis.ShaderInterpolationMethod.Discrete
        if hasattr(qgis_core.Qgis, "ShaderInterpolationMethod")
        else qgis_core.QgsColorRampShader.Discrete
    )
    ramp.setColorRampItemList(
        [
            qgis_core.QgsColorRampShader.ColorRampItem(100, QColor("red")),
            qgis_core.QgsColorRampShader.ColorRampItem(255, QColor("blue")),
        ]
    )
    shader.setRasterShaderFunction(ramp)
    layer.setRenderer(
        qgis_core.QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    )
    return layer


def _gradient_ramp(layer):
    shader = qgis_core.QgsRasterShader()
    ramp = qgis_core.QgsColorRampShader()
    ramp.setColorRampItemList(
        [
            qgis_core.QgsColorRampShader.ColorRampItem(0, QColor("black")),
            qgis_core.QgsColorRampShader.ColorRampItem(255, QColor("white")),
        ]
    )
    shader.setRasterShaderFunction(ramp)
    layer.setRenderer(
        qgis_core.QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    )
    return layer


def _map_over(project, layers, *, own_layers: bool = True):
    layout = qgis_core.QgsPrintLayout(project)
    layout.initializeDefaults()
    item = qgis_core.QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptMove(qgis_core.QgsLayoutPoint(10, 10))
    item.attemptResize(qgis_core.QgsLayoutSize(80, 80))
    item.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"))
    if own_layers:
        item.setLayers(layers)
    item.setExtent(qgis_core.QgsRectangle(0, 0, 4, 4))
    return layout, item


def _row_levels(layout, item) -> list[int]:
    """The grey levels along the middle of the map, left to right, as QGIS
    draws the page at 40 dpi (a 126 px wide map: each source pixel 31 px)."""
    from qgis.PyQt.QtCore import QRectF, QSize

    exporter = qgis_core.QgsLayoutExporter(layout)
    image = exporter.renderRegionToImage(QRectF(10, 10, 80, 80), QSize(120, 120), 40)
    return [image.pixelColor(x, 60).red() for x in range(120)]


@pytest.fixture
def ramp(project, tmp_path):
    layer = _gradient_ramp(_gradient_raster(tmp_path))
    project.addMapLayer(layer)
    return layer


class TestWhichRastersAreOffered:
    def test_a_continuous_raster_is(self, ramp) -> None:
        assert is_continuous_raster(ramp)

    def test_a_plain_grey_band_is(self, project, tmp_path) -> None:
        layer = _gradient_raster(tmp_path, "grey")
        project.addMapLayer(layer)
        assert is_continuous_raster(layer)

    def test_a_paletted_raster_is_not(self, project, tmp_path) -> None:
        layer = _paletted(_gradient_raster(tmp_path, "classes"))
        project.addMapLayer(layer)
        assert not is_continuous_raster(layer)

    def test_a_band_painted_in_discrete_classes_is_not(self, project, tmp_path) -> None:
        layer = _discrete_ramp(_gradient_raster(tmp_path, "steps"))
        project.addMapLayer(layer)
        assert not is_continuous_raster(layer)

    def test_a_vector_layer_is_not(self, project, make_memory_layer) -> None:
        assert not is_continuous_raster(make_memory_layer("points"))

    def test_only_continuous_rasters_are_listed_for_a_map(
        self, project, tmp_path
    ) -> None:
        smooth = _gradient_ramp(_gradient_raster(tmp_path, "smooth"))
        classes = _paletted(_gradient_raster(tmp_path, "classes"))
        project.addMapLayers([smooth, classes])
        _layout, item = _map_over(project, [classes, smooth])
        assert [layer.name() for layer in smoothable_layers(item)] == ["smooth"]


class TestTheChoice:
    def test_smoothing_changes_how_the_map_draws_the_raster(
        self, project, ramp
    ) -> None:
        layout, item = _map_over(project, [ramp])
        blocky = len(set(_row_levels(layout, item)))
        assert set_smoothing(item, ramp, True)
        smooth = len(set(_row_levels(layout, item)))
        # Four source pixels are four flat levels; bilinear fills between.
        assert blocky <= 5
        assert smooth > 40

    def test_the_layer_and_the_project_are_untouched(self, project, ramp) -> None:
        from qgis.core import QgsMapLayerStyle

        before = QgsMapLayerStyle()
        before.readFromLayer(ramp)
        _layout, item = _map_over(project, [ramp])
        set_smoothing(item, ramp, True)
        after = QgsMapLayerStyle()
        after.readFromLayer(ramp)
        assert before.xmlData() == after.xmlData()
        _other_layout, other = _map_over(project, [ramp])
        assert not smoothing_on(other, ramp)

    def test_it_survives_saving_and_reading_the_layout(self, project, ramp) -> None:
        from qgis.core import QgsPrintLayout, QgsReadWriteContext
        from qgis.PyQt.QtXml import QDomDocument

        layout, item = _map_over(project, [ramp])
        set_smoothing(item, ramp, True)
        document = QDomDocument()
        element = layout.writeXml(document, QgsReadWriteContext())
        document.appendChild(element)
        again = QgsPrintLayout(project)
        assert again.readXml(element, document, QgsReadWriteContext())
        read = next(
            i for i in again.items() if isinstance(i, qgis_core.QgsLayoutItemMap)
        )
        assert smoothing_on(read, ramp)

    def test_turning_it_off_restores_the_layers_own_look(self, project, ramp) -> None:
        layout, item = _map_over(project, [ramp])
        set_smoothing(item, ramp, True)
        assert set_smoothing(item, ramp, False)
        assert not smoothing_on(item, ramp)
        assert not item.keepLayerStyles()
        assert len(set(_row_levels(layout, item))) <= 5

    def test_it_affects_only_the_chosen_raster_and_map(self, project, tmp_path) -> None:
        one = _gradient_ramp(_gradient_raster(tmp_path, "one"))
        two = _gradient_ramp(_gradient_raster(tmp_path, "two"))
        project.addMapLayers([one, two])
        _layout, item = _map_over(project, [one, two])
        _other_layout, other = _map_over(project, [one, two])
        set_smoothing(item, one, True)
        assert smoothing_on(item, one)
        assert not smoothing_on(item, two)
        assert not smoothing_on(other, one)

    def test_a_categorical_raster_is_never_smoothed(self, project, tmp_path) -> None:
        layer = _paletted(_gradient_raster(tmp_path, "classes"))
        project.addMapLayer(layer)
        _layout, item = _map_over(project, [layer])
        assert not set_smoothing(item, layer, True)
        assert not smoothing_on(item, layer)
        assert not item.keepLayerStyles()

    def test_the_other_layers_styles_the_map_carried_are_kept(
        self, project, tmp_path
    ) -> None:
        one = _gradient_ramp(_gradient_raster(tmp_path, "one"))
        two = _gradient_ramp(_gradient_raster(tmp_path, "two"))
        project.addMapLayers([one, two])
        _layout, item = _map_over(project, [one, two])
        marker = "<!--kept-->"
        item.setKeepLayerStyles(True)
        item.setLayerStyleOverrides({two.id(): marker})
        set_smoothing(item, one, True)
        assert item.layerStyleOverrides()[two.id()] == marker
        set_smoothing(item, one, False)
        assert item.layerStyleOverrides()[two.id()] == marker
        assert item.keepLayerStyles()

    def test_a_map_following_the_layers_panel_keeps_its_layers(
        self, project, ramp
    ) -> None:
        layout, item = _map_over(project, [ramp], own_layers=False)
        drawn = [layer.id() for layer in item.layersToRender()]
        assert set_smoothing(item, ramp, True)
        assert [layer.id() for layer in item.layersToRender()] == drawn
        assert len(set(_row_levels(layout, item))) > 40
