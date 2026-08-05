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
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from .export_ir import (
    Color,
    ExportSettings,
    ExtentSource,
    OutputMode,
    OverlayCorner,
    PopupFieldMode,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsProject

SCOPE = "qgis2webmap"

KEY_MAP_NAME = "mapName"
KEY_OUTPUT_MODE = "outputMode"
KEY_WIDGETS = "widgets"
KEY_LAYERS = "layers"

# The OnlyMap licence key, in QSettings rather than the project.
#
# **Deliberately not stored with the `.qgz`.** Everything else in this module
# describes the map and belongs with it; a licence key describes the *person*.
# They bought it, they carry it between projects, and a project file gets
# emailed, committed to a repository and handed to a contractor. Writing a
# purchased key into a file that travels is how someone gives it away without
# noticing.
#
# It is not a secret in the cryptographic sense - the key is signed,
# domain-locked and meant to be served to browsers, which is why writing it into
# an exported map is safe. That is not a reason to scatter copies of it through
# project files.
LICENSE_KEY_SETTING = "qgis2webmap/licenseKey"


def load_license_key() -> str:
    """The user's OnlyMap licence key, or an empty string."""
    from qgis.PyQt.QtCore import QSettings

    with contextlib.suppress(Exception):
        return str(QSettings().value(LICENSE_KEY_SETTING, "", type=str) or "").strip()
    return ""


# Where a key can come from besides the dialog. Set on a machine that has no
# QGIS profile to read - CI, a container, `qgis_process` on a server - which is
# exactly where the dialog's stored key is unavailable and a batch export would
# otherwise silently produce free-plan maps.
LICENSE_KEY_ENV = "ONLYMAP_LICENSE_KEY"


def resolve_license_key(explicit: str | None = None) -> str | None:
    """The key to export with, from the most specific source that has one.

    Precedence, decided once and documented so three sources cannot become
    three bug reports:

    1. **`explicit`** - a Processing parameter. Named in the algorithm, so it
       travels with a saved model and can differ per run.
    2. **`ONLYMAP_LICENSE_KEY`** - the environment. The only route that works
       headless.
    3. **The stored key** - what the dialog saved for this user.

    Returns `None` for the free plan rather than an empty string, so callers can
    pass the result straight to `default_policy`.
    """
    for candidate in (explicit, os.environ.get(LICENSE_KEY_ENV), load_license_key()):
        text = (candidate or "").strip()
        if text:
            return text
    return None


def save_license_key(value: str) -> None:
    """Store the key, or clear it when handed nothing.

    Blank removes the entry rather than storing an empty string, so "no key" is
    one state in the settings file rather than two.
    """
    from qgis.PyQt.QtCore import QSettings

    settings = QSettings()
    text = (value or "").strip()
    with contextlib.suppress(Exception):
        if text:
            settings.setValue(LICENSE_KEY_SETTING, text)
        else:
            settings.remove(LICENSE_KEY_SETTING)


@dataclass
class LayerSettings:
    """Per-layer export choices.

    Mutable by design: this is UI state, not the frozen export model.
    """

    include: bool = True
    popup: bool = True
    label: bool = True
    # Field name -> `PopupFieldMode` value. Only fields the user has explicitly
    # chosen a mode for appear here; anything absent keeps whatever
    # `translate_popup` derived from the layer's own QGIS configuration.
    fields: dict[str, str] = field(default_factory=dict)

    # **Overrides, not values: `None` means "whatever the map-wide setting says".**
    # qgis2web has carried these three as global-only since 2015 - issues #131
    # (precision), #132 (highlight) and #133 (hover popups) all ask for exactly
    # this and all are still open. A tri-state is what makes "most layers behave
    # alike, one does not" expressible without configuring every layer.
    popup_on_hover: bool | None = None
    highlight_color: str | None = None
    # `None` inherits; `INHERIT_PRECISION_OFF` (0) is an explicit "keep full
    # precision for this layer" even when the map-wide setting rounds. 0 is free
    # to use as that marker because 1 is the smallest precision anyone can pick.
    quantize_precision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "include": self.include,
            "popup": self.popup,
            "label": self.label,
            "fields": dict(self.fields),
        }
        # Written only when set, so a project configured before overrides
        # existed round-trips unchanged and inherits everything.
        if self.popup_on_hover is not None:
            data["popupOnHover"] = self.popup_on_hover
        if self.highlight_color is not None:
            data["highlightColor"] = self.highlight_color
        if self.quantize_precision is not None:
            data["quantizePrecision"] = self.quantize_precision
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayerSettings:
        hover = data.get("popupOnHover")
        highlight = data.get("highlightColor")
        return cls(
            include=bool(data.get("include", True)),
            popup=bool(data.get("popup", True)),
            label=bool(data.get("label", True)),
            fields=_read_field_modes(data.get("fields")),
            popup_on_hover=None if hover is None else bool(hover),
            highlight_color=None if highlight is None else str(highlight),
            quantize_precision=_read_layer_precision(data.get("quantizePrecision")),
        )

    def resolved_hover(self, map_wide: bool) -> bool:
        return map_wide if self.popup_on_hover is None else self.popup_on_hover

    def resolved_highlight(self, map_wide: str) -> Color | None:
        source = map_wide if self.highlight_color is None else self.highlight_color
        return parse_hex_color(source)

    def resolved_precision(self, map_wide: int | None) -> int | None:
        if self.quantize_precision is None:
            return map_wide
        if self.quantize_precision == PRECISION_FULL:
            return None
        return self.quantize_precision


# QGIS coordinates are degrees here, so 15 places is far past any instrument.
MIN_PRECISION = 1
MAX_PRECISION = 15

# A per-layer "keep everything", distinct from inheriting the map-wide choice.
PRECISION_FULL = 0


def _read_layer_precision(raw: Any) -> int | None:
    """A per-layer precision override: None inherits, 0 means full precision."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value == PRECISION_FULL:
        return PRECISION_FULL
    if not MIN_PRECISION <= value <= MAX_PRECISION:
        return None
    return value


MIN_CHROME_SCALE = 0.75
MAX_CHROME_SCALE = 2.0


def _read_scale(raw: Any) -> float:
    """A persisted chrome scale, clamped to something legible.

    Out of range or unparseable falls back to 1.0 rather than raising: a bad
    value here would otherwise render the map controls unusably small or large,
    which is harder to recover from than simply ignoring it.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not MIN_CHROME_SCALE <= value <= MAX_CHROME_SCALE:
        return 1.0
    return round(value, 2)


def _read_precision(raw: Any) -> int | None:
    """A persisted coordinate precision, or None for "maintain"."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if not MIN_PRECISION <= value <= MAX_PRECISION:
        return None
    return value


def parse_hex_color(value: str) -> Color | None:
    """`#rrggbb` or `#rrggbbaa` to a `Color`, or None for anything unusable.

    Stored as text rather than a `Color` because that is what a QGIS colour
    button hands back and what goes into the project file; an empty string means
    "not set", which is what keeps a default export byte-identical.

    The eight-digit form is CSS order (alpha last), matching what `color_literal`
    emits, so a colour can round-trip through the manifest and back unchanged.
    """
    text = (value or "").strip().lstrip("#")
    if len(text) not in (6, 8):
        return None
    try:
        alpha = int(text[6:8], 16) / 255.0 if len(text) == 8 else 1.0
        return Color(
            r=int(text[0:2], 16),
            g=int(text[2:4], 16),
            b=int(text[4:6], 16),
            a=alpha,
        )
    except ValueError:
        return None


def _read_field_modes(raw: Any) -> dict[str, str]:
    """Field-mode overrides from persisted JSON, dropping anything unusable.

    A mode this build does not know - a hand-edited project, or one written by a
    newer plugin - falls back to the default rather than raising, for the same
    reason `load_state` tolerates corruption: a bad entry must never stop the
    dialog opening.
    """
    if not isinstance(raw, dict):
        return {}

    modes: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name:
            continue
        try:
            modes[name] = PopupFieldMode(value).value
        except ValueError:
            continue
    return modes


# Settings that change what `read_project` produces - the exported data itself,
# or a note in the fidelity report about it. Changing one of these invalidates a
# cached read.
DATA_FIELDS = (
    "map_name",
    "popup_on_hover",
    "basemap",
    "terrain",
    "highlight_color",
    "quantize_precision",
    "extent_source",
    "clip_to_extent",
)

# Settings that only restyle the map's chrome or decide how the artifact is
# packaged. None of them can change a single feature, so toggling one must never
# cost a re-read of every layer - which is what made unticking "Legend" freeze
# the dialog for as long as reading the project takes.
CHROME_FIELDS = (
    "output_mode",
    "show_legend",
    "show_layer_switcher",
    "show_zoom_controls",
    "show_scale_bar",
    "show_title",
    "show_abstract",
    "title_corner",
    "chrome_scale",
    "widget_background",
    "widget_foreground",
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
    popup_on_hover: bool = False
    # On by default: a map that does not say what it is arrives with no context,
    # and testing found people expected the title without having to look for a
    # setting. Top centre because every corner already holds map chrome.
    show_title: bool = True
    show_abstract: bool = False
    title_corner: OverlayCorner = OverlayCorner.TOP_CENTER
    # Off by default. A basemap does not change the file size - tiles are
    # streamed - but it ends the "opens offline, contacts nobody" guarantee, so
    # it has to be chosen rather than inherited.
    basemap: str = "none"
    # Off by default for the same reason as the basemap, plus one of its own: it
    # tilts the map, which changes what the recipient sees first.
    terrain: str = "none"
    # A multiplier on the size of the map's chrome. 1.0 is the runtime default.
    chrome_scale: float = 1.0
    widget_background: str = ""
    widget_foreground: str = ""
    highlight_color: str = ""
    quantize_precision: int | None = None
    extent_source: ExtentSource = ExtentSource.DATA
    clip_to_extent: bool = False
    layers: dict[str, LayerSettings] = field(default_factory=dict)

    def snapshot(self) -> str:
        """A deterministic string that changes whenever any setting does.

        The live preview compares this rather than hooking each widget's signal:
        there are eighteen change handlers and only some of them mark state
        dirty, so a signal-based approach would already miss half the settings
        and would silently rot as new ones are added. Deriving it from the
        dataclass fields means a setting added tomorrow is watched for free.

        **It watches this dialog's state, not the QGIS project.** Restyling a
        layer in QGIS does not change any field here, so the live preview will
        not notice it; the layer tree is watched separately for add, remove and
        rename. Catching style edits would mean subscribing to every layer's
        repaint, which is a much larger change than it looks.
        """
        return self._snapshot_of(DATA_FIELDS + CHROME_FIELDS)

    def data_snapshot(self) -> str:
        """The part of `snapshot` that can change the exported data.

        Equal snapshots mean a previous `read_project` result is still exactly
        what a fresh read would produce, so it can be reused. That is what keeps
        a chrome checkbox from re-reading half a million features.
        """
        return self._snapshot_of(DATA_FIELDS)

    def _snapshot_of(self, names: tuple[str, ...]) -> str:
        return json.dumps(
            {
                **{
                    name: getattr(self, name).value
                    if isinstance(getattr(self, name), Enum)
                    else getattr(self, name)
                    for name in names
                },
                # Per-layer settings are always included: every one of them
                # (include, popups, labels, field modes, precision) changes the
                # data, so there is no chrome-only half to leave out.
                "layers": {k: v.to_dict() for k, v in sorted(self.layers.items())},
            },
            sort_keys=True,
        )

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
            popup_on_hover=self.popup_on_hover,
            show_title=self.show_title,
            show_abstract=self.show_abstract,
            title_corner=self.title_corner,
            basemap=self.basemap,
            terrain=self.terrain,
            chrome_scale=self.chrome_scale,
            widget_background=parse_hex_color(self.widget_background),
            widget_foreground=parse_hex_color(self.widget_foreground),
            highlight_color=parse_hex_color(self.highlight_color),
            quantize_precision=self.quantize_precision,
            extent_source=self.extent_source,
            clip_to_extent=self.clip_to_extent,
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
            state.popup_on_hover = bool(widgets.get("popupOnHover", False))
            # Defaults to on, matching `DialogState`. A project saved before the
            # caption existed has no `showTitle` key at all, and should pick up
            # the new default rather than be pinned to the old one by its silence.
            state.show_title = bool(widgets.get("showTitle", True))
            state.show_abstract = bool(widgets.get("showAbstract", False))
            state.basemap = str(widgets.get("basemap", "none") or "none")
            state.terrain = str(widgets.get("terrain", "none") or "none")
            state.chrome_scale = _read_scale(widgets.get("chromeScale"))
            state.widget_background = str(widgets.get("widgetBackground", "") or "")
            state.widget_foreground = str(widgets.get("widgetForeground", "") or "")
            state.highlight_color = str(widgets.get("highlightColor", "") or "")
            state.quantize_precision = _read_precision(widgets.get("quantizePrecision"))
            with contextlib.suppress(ValueError):
                state.title_corner = OverlayCorner(widgets.get("titleCorner"))
            with contextlib.suppress(ValueError):
                state.extent_source = ExtentSource(widgets.get("extentSource"))
            state.clip_to_extent = bool(widgets.get("clipToExtent", False))
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
                "popupOnHover": state.popup_on_hover,
                "showTitle": state.show_title,
                "showAbstract": state.show_abstract,
                "titleCorner": state.title_corner.value,
                "basemap": state.basemap,
                "terrain": state.terrain,
                "chromeScale": state.chrome_scale,
                "widgetBackground": state.widget_background,
                "widgetForeground": state.widget_foreground,
                "highlightColor": state.highlight_color,
                "quantizePrecision": state.quantize_precision,
                "extentSource": state.extent_source.value,
                "clipToExtent": state.clip_to_extent,
            }
        ),
    )
    project.writeEntry(
        SCOPE,
        KEY_LAYERS,
        json.dumps({k: v.to_dict() for k, v in state.layers.items()}),
    )
