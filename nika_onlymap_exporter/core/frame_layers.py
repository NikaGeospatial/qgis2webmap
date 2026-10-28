"""Which layers a map frame really draws, including "none at all".

QGIS cannot store an empty layer set: `QgsLayoutItemMap.layersToRender()`
treats an empty list as "follow the project's visible layers", whatever
`keepLayerSet` says. So a frame whose last layer was removed would suddenly
draw every project layer again.

A deliberately empty frame is therefore stored on the frame itself, with
nothing added to the project: a data-defined `MapLayers` property whose
expression is the empty text. QGIS evaluates that to "no layer names", keeps
the result and never falls back to the project, so the preview and every native
export draw nothing. The property is written into the layout's XML, so the
choice survives saving, reopening, undo and redo. A custom property marks the
data-defined property as ours, so a layers expression the user wrote
themselves is never mistaken for it, nor removed when a layer is added back.

Everything that asks "what does this frame draw" goes through
`rendered_layers`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from qgis.core import QgsLayoutObject, QgsProperty

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsLayoutItemMap, QgsMapLayer

EMPTY_FRAME_PROPERTY = "nika/empty_frame"
_MAP_LAYERS = QgsLayoutObject.DataDefinedProperty.MapLayers
_EMPTY_EXPRESSION = "''"


def is_deliberately_empty(map_item: QgsLayoutItemMap) -> bool:
    """Whether the frame was emptied by us and still is."""
    if not map_item.customProperty(EMPTY_FRAME_PROPERTY, False):
        return False
    prop = map_item.dataDefinedProperties().property(_MAP_LAYERS)
    return prop.isActive() and prop.expressionString() == _EMPTY_EXPRESSION


def rendered_layers(map_item: QgsLayoutItemMap) -> list[QgsMapLayer]:
    """The layers the frame draws, top first: what the preview, the image or
    PDF and the HTML all show. QGIS resolves its own list, a map theme, the
    project's visible layers and any data-defined layers expression."""
    return list(map_item.layersToRender())


def set_frame_layers(map_item: QgsLayoutItemMap, layers: Sequence[QgsMapLayer]) -> None:
    """Give the frame its own layer list; empty means it draws nothing.

    Giving it layers again removes only the empty marker we set, never a layers
    expression of the user's."""
    map_item.setKeepLayerSet(True)
    map_item.setLayers(list(layers))
    if layers:
        if map_item.customProperty(EMPTY_FRAME_PROPERTY, False):
            map_item.dataDefinedProperties().setProperty(_MAP_LAYERS, QgsProperty())
            map_item.removeCustomProperty(EMPTY_FRAME_PROPERTY)
        return
    map_item.dataDefinedProperties().setProperty(
        _MAP_LAYERS, QgsProperty.fromExpression(_EMPTY_EXPRESSION)
    )
    map_item.setCustomProperty(EMPTY_FRAME_PROPERTY, True)


__all__ = [
    "EMPTY_FRAME_PROPERTY",
    "is_deliberately_empty",
    "rendered_layers",
    "set_frame_layers",
]
