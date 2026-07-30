"""Export settings, stored in the QGIS project.

Settings live with the `.qgz`, so reopening a project restores the export
choices. This is the one thing qgis2web gets right in this area - it uses
`QgsProject.writeEntry`/`readEntry` with its own namespace, and there is no
reason to invent a sidecar file.

Per-layer settings are keyed by **layer id, not by widget**. That is what lets
the layer list be rebuilt whenever QGIS changes without losing configuration -
the failure the incumbent works around by making refresh a side effect of a
settings control.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .export_ir import ExportSettings, OutputMode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsProject

SCOPE = "qgis2webmap"

KEY_MAP_NAME = "mapName"
KEY_OUTPUT_MODE = "outputMode"
KEY_WIDGETS = "widgets"
KEY_LAYERS = "layers"


@dataclass
class LayerSettings:
    """Per-layer export choices.

    Mutable by design: this is UI state, not the frozen export model.
    """

    include: bool = True
    popup: bool = True
    label: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {"include": self.include, "popup": self.popup, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayerSettings:
        return cls(
            include=bool(data.get("include", True)),
            popup=bool(data.get("popup", True)),
            label=bool(data.get("label", True)),
        )


@dataclass
class DialogState:
    """Everything the dialog needs to remember between sessions."""

    map_name: str = ""
    output_mode: OutputMode = OutputMode.STANDALONE_HTML
    show_legend: bool = True
    show_layer_switcher: bool = True
    show_zoom_controls: bool = True
    show_scale_bar: bool = True
    layers: dict[str, LayerSettings] = field(default_factory=dict)

    def for_layer(self, layer_id: str) -> LayerSettings:
        """Settings for a layer, creating defaults on first sight.

        Entries for layers no longer in the project are deliberately kept: an
        undo in QGIS brings the layer back, and its configuration should come
        back with it.
        """
        if layer_id not in self.layers:
            self.layers[layer_id] = LayerSettings()
        return self.layers[layer_id]

    def selected_layer_ids(self, available: list[str]) -> frozenset[str]:
        return frozenset(lid for lid in available if self.for_layer(lid).include)

    def to_export_settings(self) -> ExportSettings:
        return ExportSettings(
            output_mode=self.output_mode,
            show_legend=self.show_legend,
            show_layer_switcher=self.show_layer_switcher,
            show_zoom_controls=self.show_zoom_controls,
            show_scale_bar=self.show_scale_bar,
        )


def load_state(project: QgsProject) -> DialogState:
    """Read settings from the project, tolerating anything malformed.

    A corrupt entry must never stop the dialog opening: the worst outcome of
    ignoring it is that the user re-picks a setting.
    """
    state = DialogState()

    name, ok = project.readEntry(SCOPE, KEY_MAP_NAME, "")
    if ok and name:
        state.map_name = name

    mode_value, ok = project.readEntry(SCOPE, KEY_OUTPUT_MODE, "")
    if ok and mode_value:
        with contextlib.suppress(ValueError):
            state.output_mode = OutputMode(mode_value)

    widgets_json, ok = project.readEntry(SCOPE, KEY_WIDGETS, "")
    if ok and widgets_json:
        try:
            widgets = json.loads(widgets_json)
            state.show_legend = bool(widgets.get("legend", True))
            state.show_layer_switcher = bool(widgets.get("layerSwitcher", True))
            state.show_zoom_controls = bool(widgets.get("zoomControls", True))
            state.show_scale_bar = bool(widgets.get("scaleBar", True))
        except (ValueError, AttributeError):
            pass

    layers_json, ok = project.readEntry(SCOPE, KEY_LAYERS, "")
    if ok and layers_json:
        try:
            for layer_id, data in json.loads(layers_json).items():
                state.layers[layer_id] = LayerSettings.from_dict(data)
        except (ValueError, AttributeError):
            pass

    return state


def save_state(project: QgsProject, state: DialogState) -> None:
    """Write settings into the project. Marks it dirty, as any change should."""
    project.writeEntry(SCOPE, KEY_MAP_NAME, state.map_name)
    project.writeEntry(SCOPE, KEY_OUTPUT_MODE, state.output_mode.value)
    project.writeEntry(
        SCOPE,
        KEY_WIDGETS,
        json.dumps(
            {
                "legend": state.show_legend,
                "layerSwitcher": state.show_layer_switcher,
                "zoomControls": state.show_zoom_controls,
                "scaleBar": state.show_scale_bar,
            }
        ),
    )
    project.writeEntry(
        SCOPE,
        KEY_LAYERS,
        json.dumps({k: v.to_dict() for k, v in state.layers.items()}),
    )
