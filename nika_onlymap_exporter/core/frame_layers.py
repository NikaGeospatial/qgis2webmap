"""Which layers a map frame really draws, including "none at all".

QGIS cannot store an empty layer set: `QgsLayoutItemMap.layersToRender()`
treats an empty list as "follow the project's visible layers", whatever
`keepLayerSet` says. So a frame whose last layer was removed would suddenly
draw every project layer again.

A deliberately empty frame is therefore stored as a layer set holding one
hidden, empty, geometry-less memory layer (the blank layer). It lives in the
project but not in the Layers panel, so QGIS itself renders nothing for the
frame, in the preview and in every native export, and the choice survives
saving, reopening, undo and redo with no hook of ours. Everything that asks
"what does this frame draw" goes through `rendered_layers`, which never
reports the blank layer.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from qgis.core import QgsVectorLayer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsLayoutItemMap, QgsMapLayer, QgsProject

BLANK_LAYER_PROPERTY = "nika/blank_frame_layer"
BLANK_LAYER_NAME = "NIKA blank map layer"


def is_blank_layer(layer: QgsMapLayer) -> bool:
    return bool(layer.customProperty(BLANK_LAYER_PROPERTY, False))


def blank_layer(project: QgsProject) -> QgsMapLayer:
    """The project's blank layer, created (hidden from the Layers panel) on
    first use."""
    for layer in project.mapLayers().values():
        if is_blank_layer(layer):
            return layer
    layer = QgsVectorLayer("None?field=blank:int", BLANK_LAYER_NAME, "memory")
    layer.setCustomProperty(BLANK_LAYER_PROPERTY, True)
    # QGIS asks to save scratch layers when the project closes; this one has
    # nothing worth saving.
    layer.setCustomProperty("skipMemoryLayersCheck", 1)
    project.addMapLayer(layer, False)
    return layer


def layers_for_storage(
    project: QgsProject, layers: Sequence[QgsMapLayer]
) -> list[QgsMapLayer]:
    """The list to hand `setLayers`: `layers`, or the blank layer when there
    are none (an empty list would mean "follow the project")."""
    return list(layers) if layers else [blank_layer(project)]


def rendered_layers(map_item: QgsLayoutItemMap) -> list[QgsMapLayer]:
    """The layers the frame draws, top first: what the preview, the image or
    PDF and the HTML all show. Empty for a deliberately empty frame."""
    return [layer for layer in map_item.layersToRender() if not is_blank_layer(layer)]


def set_frame_layers(
    map_item: QgsLayoutItemMap,
    layers: Sequence[QgsMapLayer],
    project: QgsProject | None = None,
) -> None:
    """Give the frame its own layer list; empty means draws nothing. The
    project defaults to the frame's layout's."""
    if project is None:
        layout = map_item.layout()
        project = layout.project() if layout is not None else None
    map_item.setKeepLayerSet(True)
    if project is None and not layers:
        raise ValueError("an empty layer set needs the frame's project")
    map_item.setLayers(
        layers_for_storage(project, layers) if project is not None else list(layers)
    )


__all__ = [
    "BLANK_LAYER_NAME",
    "BLANK_LAYER_PROPERTY",
    "blank_layer",
    "is_blank_layer",
    "layers_for_storage",
    "rendered_layers",
    "set_frame_layers",
]
