"""Smoothing a continuous raster when it is drawn larger than its pixels.

A raster whose pixels are bigger than the page's (a 30 m elevation model on a
300 dpi map) is drawn with QGIS's nearest-neighbour resampling by default:
every source pixel a visible block. Bilinear resampling when zoomed in gives a
continuous surface its natural look -- and gives a categorical raster (land
cover classes, a paletted image) colours that are in no class, so only a
continuous raster is ever offered it.

The choice belongs to one map item, never to the layer: it is written as that
map item's own style override for the layer (QGIS saves it with the layout),
so the project's layer style and every other map drawing the layer are
untouched, and turning it off removes the override again.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsMapLayerStyle,
    QgsRasterLayer,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtXml import QDomDocument

NEAREST = "nearestNeighbour"
BILINEAR = "bilinear"
_ELEMENT = "resampling"
_ATTRIBUTE = "zoomedInResamplingMethod"
_STAGE = "resamplingStage"
_PROVIDER_STAGE = "provider"


def _classified(ramp: object) -> bool:
    """Whether a colour ramp shader paints classes (discrete steps or exact
    values) rather than a gradient."""
    kind = getattr(ramp, "colorRampType", None)
    if not callable(kind):
        return False
    method = getattr(Qgis, "ShaderInterpolationMethod", None)
    classes = (
        (method.Discrete, method.Exact)
        if method is not None
        else (QgsColorRampShader.Discrete, QgsColorRampShader.Exact)
    )
    return kind() in classes


def is_continuous_raster(layer: QgsMapLayer) -> bool:
    """Whether `layer` is a raster whose values run on a scale -- elevation,
    temperature, imagery -- so a value between two pixels is meaningful.

    Not a paletted or unique-values raster, nor a single band painted in
    discrete classes: a colour between two classes names no class."""
    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return False
    provider = layer.dataProvider()
    renderer = layer.renderer()
    if provider is None or renderer is None or provider.name() != "gdal":
        return False
    kind = renderer.type()
    if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
        shader = renderer.shader()
        return shader is None or not _classified(shader.rasterShaderFunction())
    return kind in ("singlebandgray", "multibandcolor", "hillshade")


def style_xml(map_item: QgsLayoutItemMap, layer: QgsMapLayer) -> str:
    """The style `map_item` draws `layer` in: its own override when it has
    one, else the layer's current style."""
    if map_item.keepLayerStyles():
        kept = map_item.layerStyleOverrides().get(layer.id())
        if kept:
            return str(kept)
    style = QgsMapLayerStyle()
    style.readFromLayer(layer)
    return str(style.xmlData())


def _resampling(xml: str) -> str:
    document = QDomDocument()
    if not document.setContent(xml):
        return NEAREST
    nodes = document.elementsByTagName(_ELEMENT)
    for index in range(nodes.count()):
        element = nodes.at(index).toElement()
        if element.parentNode().nodeName() == "provider" or nodes.count() == 1:
            value = element.attribute(_ATTRIBUTE)
            enabled = element.attribute("enabled", "true") != "false"
            return value if value and enabled else NEAREST
    return NEAREST


def smoothing_on(map_item: QgsLayoutItemMap, layer: QgsMapLayer) -> bool:
    """Whether `map_item` draws `layer` with bilinear resampling when zoomed
    in -- by its own override, or because the layer is already styled so."""
    return _resampling(style_xml(map_item, layer)) == BILINEAR


def _with_resampling(xml: str, method: str) -> str | None:
    """`xml` with the zoomed-in resampling method set; `None` when the style
    has no provider resampling element to set."""
    document = QDomDocument()
    if not document.setContent(xml):
        return None
    nodes = document.elementsByTagName(_ELEMENT)
    if nodes.count() == 0:
        return None
    element = nodes.at(0).toElement()
    element.setAttribute(_ATTRIBUTE, method)
    element.setAttribute("enabled", "true")
    # Resampling by the provider only takes effect at the provider's stage.
    stage = document.elementsByTagName(_STAGE)
    if stage.count():
        node = stage.at(0)
        while node.hasChildNodes():
            node.removeChild(node.firstChild())
        node.appendChild(document.createTextNode(_PROVIDER_STAGE))
    else:
        holder = document.createElement(_STAGE)
        holder.appendChild(document.createTextNode(_PROVIDER_STAGE))
        element.parentNode().appendChild(holder)
    return str(document.toString())


def set_smoothing(map_item: QgsLayoutItemMap, layer: QgsMapLayer, on: bool) -> bool:
    """Draw `layer` smoothed (or not) when `map_item` enlarges it. Returns
    whether the map item changed.

    Only a continuous raster is touched. The map item keeps its layers and
    every style it already carried; turning smoothing off restores the
    layer's own look by dropping the override, and the map item's own style
    switch with it when nothing else needed it."""
    if not is_continuous_raster(layer):
        return False
    if smoothing_on(map_item, layer) == on:
        return False
    layer_id = layer.id()
    own_layers(map_item)
    overrides = (
        dict(map_item.layerStyleOverrides()) if map_item.keepLayerStyles() else {}
    )
    base = QgsMapLayerStyle()
    base.readFromLayer(layer)
    layer_own = _resampling(str(base.xmlData()))
    if not on and layer_own != BILINEAR:
        # Back to what the layer itself says.
        overrides.pop(layer_id, None)
    else:
        updated = _with_resampling(
            style_xml(map_item, layer), BILINEAR if on else NEAREST
        )
        if updated is None:
            return False
        overrides[layer_id] = updated
    store_overrides(map_item, overrides)
    return True


def own_layers(map_item: QgsLayoutItemMap) -> None:
    """Make the map item's layer list its own, as it is drawn now, so a style
    override can apply: QGIS ignores overrides on a map that follows a map
    theme or the Layers panel. A theme's per-layer styles stay with the map,
    so nothing is restyled, and the layers it draws stay what they are."""
    if map_item.followVisibilityPreset():
        layout = map_item.layout()
        project = layout.project() if layout is not None else None
        drawn = list(map_item.layersToRender())
        overrides = (
            project.mapThemeCollection().mapThemeStyleOverrides(
                map_item.followVisibilityPresetName()
            )
            if project is not None
            else {}
        )
        map_item.setFollowVisibilityPreset(False)
        map_item.setKeepLayerStyles(True)
        map_item.setLayerStyleOverrides(
            {
                layer.id(): overrides[layer.id()]
                for layer in drawn
                if layer.id() in overrides
            }
        )
        map_item.setKeepLayerSet(True)
        map_item.setLayers(drawn)
    elif not map_item.keepLayerSet():
        map_item.setKeepLayerSet(True)
        map_item.setLayers(list(map_item.layersToRender()))


def store_overrides(map_item: QgsLayoutItemMap, overrides: dict[str, str]) -> None:
    map_item.setKeepLayerStyles(bool(overrides))
    map_item.setLayerStyleOverrides(overrides)
    map_item.invalidateCache()


def smoothable_layers(map_item: QgsLayoutItemMap) -> list[QgsRasterLayer]:
    """The continuous rasters `map_item` draws, top first."""
    return [
        layer
        for layer in map_item.layersToRender()
        if isinstance(layer, QgsRasterLayer) and is_continuous_raster(layer)
    ]


__all__ = [
    "is_continuous_raster",
    "set_smoothing",
    "smoothable_layers",
    "smoothing_on",
]
