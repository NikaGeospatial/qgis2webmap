"""Raster colour-ramp legends whose range QGIS cannot print.

A layer saved with no finite minimum and maximum on its renderer (a raster
whose statistics were never computed, or were all nodata) still draws from its
colour ramp, but QGIS's own legend prints that range with the number
formatter, so the legend says "nan" twice. That text is QGIS's and not ours to
rewrite; the cartograph's HTML legend never prints a raster's range, so it is
unaffected. What this module does is find those layers so the checks can say
so instead of leaving a literal "nan" unexplained.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from qgis.core import (
    QgsColorRampLegendNode,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap, QgsMapLayer


def _ramp_range(
    legend_node: QgsColorRampLegendNode, layer: QgsMapLayer | None
) -> tuple[float, float] | None:
    """The range QGIS prints beside a ramp: the node's own where QGIS 4 tells
    us, else what its renderer holds (a pseudocolour renderer's colour ramp
    shader, a grey renderer's contrast stretch)."""
    if hasattr(legend_node, "minimum"):
        return (legend_node.minimum(), legend_node.maximum())
    renderer = layer.renderer() if isinstance(layer, QgsRasterLayer) else None
    if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
        shader = renderer.shader().rasterShaderFunction() if renderer.shader() else None
        if shader is not None:
            return (shader.minimumValue(), shader.maximumValue())
    elif isinstance(renderer, QgsSingleBandGrayRenderer):
        enhancement = renderer.contrastEnhancement()
        if enhancement is not None:
            return (enhancement.minimumValue(), enhancement.maximumValue())
    return None


def layers_with_unavailable_range(
    legend: QgsLayoutItemLegend, map_item: QgsLayoutItemMap
) -> list[str]:
    """Names of the map's raster layers whose colour-ramp entry in `legend`
    has a minimum or maximum that is not a finite number."""
    drawn = {layer.id() for layer in map_item.layersToRender()}
    model = legend.model()
    names: list[str] = []
    for node in model.rootGroup().findLayers():
        if node.layerId() not in drawn:
            continue
        for legend_node in model.layerLegendNodes(node):
            if not isinstance(legend_node, QgsColorRampLegendNode):
                continue
            bounds = _ramp_range(legend_node, node.layer())
            if bounds is not None and not all(math.isfinite(b) for b in bounds):
                names.append(node.name())
                break
    return names
