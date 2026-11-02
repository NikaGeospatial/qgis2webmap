"""Edit what a layout legend lists, with QGIS's own legend facilities.

A legend is an ordinary `QgsLayoutItemLegend` in manual mode: its layer tree
holds one node per listed layer, `QgsMapLayerLegendUtils` keeps a layer's class
order and class labels on that node, and the node's `legend/title-label`
property is the layer's heading. Everything here edits only that, so:

- Legend visibility is not map visibility: taking an entry out of a legend
  never touches the map item's layers or the project's layer tree.
- A label edit is a display label; the source layer keeps its name.
- A removed entry is not lost: a layer the legend's map draws and the legend
  does not list, or a class off a node's order, is *restorable* (`entries`
  lists them unchecked). A removed layer's labels and class edits are kept on
  the legend (`REMOVED_STATE_PROPERTY`) and come back with it.
- Each public edit is one undo command on the layout's stack.

`apply_template_defaults` is what a new template legend gets: technical support
layers (a hillshade's grey intensity, a web basemap) are left out, restorable,
and an elevation ramp is headed with its unit when the layer says what it is.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from qgis.core import (
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsLayoutItemLegend,
    QgsLegendStyle,
    QgsMapLayer,
    QgsMapLayerLegendUtils,
    QgsMapLayerType,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtCore import Qt

from ..qt_compat import legend_is_manual as is_manual
from ..qt_compat import set_legend_manual
from .frame_layers import rendered_layers
from .raster_legend import ramp_range

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsPrintLayout, QgsProject

#: The legend's own heading for a layer, as QGIS's legend editor writes it.
TITLE_PROPERTY = "legend/title-label"
#: On a legend: JSON `{layer_id: {property: value}}`, the node properties
#: (heading, class order and labels) of layers taken out of it.
REMOVED_STATE_PROPERTY = "qgis2webmap/legend_removed_state"
#: On a legend: JSON `{layer_id: {"group": [names], "after": layer_id}}`, where
#: each layer its map stopped drawing was listed, so it is listed there again
#: when the map draws it again. A layer the legend did not list is not here.
PARKED_PROPERTY = "qgis2webmap/legend_parked_with_map"
#: On a legend: JSON list of layer ids a new template left out as support.
SUPPORT_LEFT_OUT_PROPERTY = "qgis2webmap/legend_support_left_out"
#: On a layer: its unit ("m"), when the data does not say it in its name.
UNITS_PROPERTY = "qgis2webmap/units"

RANGE_UNAVAILABLE = "range unavailable"

_SUPPORT_NAME = re.compile(
    r"hill\s*-?\s*shad|shaded\s+relief|relief\s+shad|slope\s+shad|multi-?\s*directional"
    r"|\bbase\s*-?\s*map\b|\bbackdrop\b|\bbackground\b|open\s*street\s*map|\bosm\b"
    r"|\bsatellite\b|\bimagery\b|\bbasemap\b",
    re.IGNORECASE,
)
_TILE_PROVIDERS = frozenset({"wms", "wmts", "xyz", "arcgismapserver", "wcs"})
_HAS_UNIT = re.compile(r"[(\[][^()\[\]]{1,12}[)\]]\s*$")


class EntryKind(Enum):
    LAYER = "layer"
    CLASS = "class"


@dataclass(frozen=True)
class LegendEntry:
    """One row the legend editor lists.

    `index` is the class's place in the layer's own legend (None for a layer
    row). `label` is what the legend shows, `source_label` what the layer or
    its renderer calls it. `included` is False for a restorable entry. `note`
    says something the label cannot (an unavailable ramp range); `support` is
    set on layers a template left out as technical.
    """

    layer_id: str
    kind: EntryKind
    index: int | None
    label: str
    source_label: str
    included: bool
    editable: bool = True
    note: str = ""
    support: bool = False


def _scope_layers(
    legend: QgsLayoutItemLegend, project: QgsProject
) -> list[QgsMapLayer]:
    """The layers a legend may list: its linked map's, else the project's."""
    map_item = legend.linkedMap()
    if map_item is not None:
        return list(rendered_layers(map_item))
    return [
        node.layer()
        for node in project.layerTreeRoot().findLayers()
        if node.layer() is not None
    ]


def _ensure_manual(legend: QgsLayoutItemLegend, project: QgsProject) -> None:
    """An automatic legend lists every project layer whatever its map draws;
    editing it makes it manual, holding the map's layers."""
    if is_manual(legend):
        return
    set_legend_manual(legend)
    keep = {layer.id() for layer in _scope_layers(legend, project)}
    for node in legend.model().rootGroup().findLayers():
        if node.layerId() not in keep and node.parent() is not None:
            node.parent().removeChildNode(node)


# ---- Reading -------------------------------------------------------------


def _layer_nodes(legend: QgsLayoutItemLegend) -> list[QgsLayerTreeLayer]:
    return list(legend.model().rootGroup().findLayers())


def _node_for(legend: QgsLayoutItemLegend, layer_id: str) -> QgsLayerTreeLayer | None:
    for node in _layer_nodes(legend):
        if node.layerId() == layer_id:
            return node
    return None


def _source_nodes(layer: QgsMapLayer) -> list:
    layer_legend = layer.legend()
    if layer_legend is None:
        return []
    return list(layer_legend.createLayerTreeModelLegendNodes(QgsLayerTreeLayer(layer)))


def _text(value: object) -> str:
    # A legend node with no label answers a null QVariant on QGIS 3.
    if value is None or getattr(value, "isNull", lambda: False)():
        return ""
    return str(value)


def _source_class_label(legend_node: object, layer: QgsMapLayer) -> tuple[str, bool]:
    """The label of a source legend node, and whether it can be edited. A
    colour ramp has no label of its own (it prints its range)."""
    label = _text(legend_node.data(Qt.ItemDataRole.DisplayRole))  # type: ignore[attr-defined]
    if label:
        return label, True
    return "Colour ramp", False


def _ramp_note(legend_node: object, layer: QgsMapLayer) -> str:
    from qgis.core import QgsColorRampLegendNode

    if not isinstance(legend_node, QgsColorRampLegendNode):
        return ""
    bounds = ramp_range(legend_node, layer)
    if bounds is not None and not all(math.isfinite(b) for b in bounds):
        return RANGE_UNAVAILABLE
    return ""


def support_left_out(legend: QgsLayoutItemLegend) -> set[str]:
    raw = legend.customProperty(SUPPORT_LEFT_OUT_PROPERTY)
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return set()
    return {str(item) for item in value} if isinstance(value, list) else set()


def _layer_title(node: QgsLayerTreeLayer, layer: QgsMapLayer | None) -> str:
    return _text(node.customProperty(TITLE_PROPERTY)) or (
        layer.name() if layer is not None else node.name()
    )


def _class_entries(
    node: QgsLayerTreeLayer, layer: QgsMapLayer, included: bool, props: dict[str, str]
) -> list[LegendEntry]:
    sources = _source_nodes(layer)
    if len(sources) <= 1:
        return []  # drawn as the layer's own row
    if included:
        order = (
            list(QgsMapLayerLegendUtils.legendNodeOrder(node))
            if QgsMapLayerLegendUtils.hasLegendNodeOrder(node)
            else list(range(len(sources)))
        )
    else:
        order = _order_from_props(props, len(sources))
    present = [i for i in order if 0 <= i < len(sources)]
    absent = [i for i in range(len(sources)) if i not in present]
    entries = []
    for index in [*present, *absent]:
        source_label, editable = _source_class_label(sources[index], layer)
        if included:
            user = QgsMapLayerLegendUtils.legendNodeUserLabel(node, index)
        else:
            user = props.get(f"legend/label-{index}", "")
        entries.append(
            LegendEntry(
                layer_id=layer.id(),
                kind=EntryKind.CLASS,
                index=index,
                label=_text(user) or source_label,
                source_label=source_label,
                included=included and index in present,
                editable=editable,
                note=_ramp_note(sources[index], layer),
            )
        )
    return entries


def _order_from_props(props: dict[str, str], count: int) -> list[int]:
    raw = props.get("legend/node-order", "")
    if not raw:
        return list(range(count))
    try:
        return [int(part) for part in raw.split(",") if part != ""]
    except ValueError:
        return list(range(count))


def _removed_state(legend: QgsLayoutItemLegend) -> dict[str, dict[str, str]]:
    raw = legend.customProperty(REMOVED_STATE_PROPERTY)
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_removed_state(
    legend: QgsLayoutItemLegend, state: dict[str, dict[str, str]]
) -> None:
    if state:
        legend.setCustomProperty(REMOVED_STATE_PROPERTY, json.dumps(state))
    else:
        legend.removeCustomProperty(REMOVED_STATE_PROPERTY)


def entries(legend: QgsLayoutItemLegend, project: QgsProject) -> list[LegendEntry]:
    """Every entry the legend can list, in its order: the included layers
    (each followed by its classes, left-out ones last and unchecked), then
    the restorable layers its map draws."""
    result: list[LegendEntry] = []
    support = support_left_out(legend)
    listed: set[str] = set()
    for node in _layer_nodes(legend):
        layer = node.layer()
        if layer is None or node.layerId() in listed:
            continue
        listed.add(node.layerId())
        result.append(
            LegendEntry(
                layer_id=node.layerId(),
                kind=EntryKind.LAYER,
                index=None,
                label=_layer_title(node, layer),
                source_label=layer.name(),
                included=True,
            )
        )
        result += _class_entries(node, layer, True, {})
    state = _removed_state(legend)
    for layer in _scope_layers(legend, project):
        if layer.id() in listed:
            continue
        listed.add(layer.id())
        props = state.get(layer.id(), {})
        result.append(
            LegendEntry(
                layer_id=layer.id(),
                kind=EntryKind.LAYER,
                index=None,
                label=_text(props.get(TITLE_PROPERTY)) or layer.name(),
                source_label=layer.name(),
                included=False,
                support=layer.id() in support,
            )
        )
    return result


def restorable(legend: QgsLayoutItemLegend, project: QgsProject) -> list[LegendEntry]:
    """What "Restore removed" would bring back: left-out layers and classes."""
    return [
        entry
        for entry in entries(legend, project)
        if not entry.included
        and (entry.kind is EntryKind.LAYER or _listed(entry, legend))
    ]


def _listed(entry: LegendEntry, legend: QgsLayoutItemLegend) -> bool:
    return _node_for(legend, entry.layer_id) is not None


# ---- Editing -------------------------------------------------------------


@contextlib.contextmanager
def _command(
    layout: QgsPrintLayout | None, legend: QgsLayoutItemLegend, text: str
) -> Iterator[None]:
    """One undo step on the layout's stack covering the legend's change."""
    stack = layout.undoStack() if layout is not None else None
    if stack is not None:
        stack.beginCommand(legend, text)
    try:
        yield
    finally:
        if stack is not None:
            stack.endCommand()
        _redraw(legend)


def _redraw(legend: QgsLayoutItemLegend) -> None:
    legend.invalidateCache()
    if legend.resizeToContents():
        legend.adjustBoxSize()
    legend.refresh()


def _refresh_node(legend: QgsLayoutItemLegend, node: QgsLayerTreeLayer) -> None:
    legend.model().refreshLayerLegend(node)


def _insert_position(
    legend: QgsLayoutItemLegend, project: QgsProject, layer: QgsMapLayer
) -> tuple[QgsLayerTreeGroup, int]:
    """Where a restored layer goes: after the nearest listed layer above it
    in its map's order, else at the top."""
    scope = [other.id() for other in _scope_layers(legend, project)]
    above = scope[: scope.index(layer.id())] if layer.id() in scope else []
    for neighbour in reversed(above):
        node = _node_for(legend, neighbour)
        if node is not None and node.parent() is not None:
            parent = node.parent()
            return parent, parent.children().index(node) + 1
    root = legend.model().rootGroup()
    return root, 0


def _remove_layer(legend: QgsLayoutItemLegend, node: QgsLayerTreeLayer) -> None:
    state = _removed_state(legend)
    state[node.layerId()] = {
        key: _text(node.customProperty(key))
        for key in node.customProperties()
        if key.startswith("legend/")
    }
    _save_removed_state(legend, state)
    if node.parent() is not None:
        node.parent().removeChildNode(node)


def _restore_layer(
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    layer: QgsMapLayer,
    place: tuple[QgsLayerTreeGroup, int] | None = None,
) -> None:
    parent, position = place or _insert_position(legend, project, layer)
    node = parent.insertLayer(position, layer)
    state = _removed_state(legend)
    for key, value in state.pop(layer.id(), {}).items():
        node.setCustomProperty(key, value)
    _save_removed_state(legend, state)
    support = support_left_out(legend)
    if layer.id() in support:
        support.discard(layer.id())
        legend.setCustomProperty(SUPPORT_LEFT_OUT_PROPERTY, json.dumps(sorted(support)))
    _refresh_node(legend, node)


def _parked(legend: QgsLayoutItemLegend) -> dict[str, dict[str, object]]:
    raw = legend.customProperty(PARKED_PROPERTY)
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_parked(
    legend: QgsLayoutItemLegend, parked: dict[str, dict[str, object]]
) -> None:
    if parked:
        legend.setCustomProperty(PARKED_PROPERTY, json.dumps(parked))
    else:
        legend.removeCustomProperty(PARKED_PROPERTY)


def left_out_of_legend(legend: QgsLayoutItemLegend, layer_id: str) -> bool:
    """Whether the legend was edited to leave this layer out (by the user or
    by a template), as opposed to never having listed it."""
    return layer_id in _removed_state(legend) or layer_id in support_left_out(legend)


def park_layer(legend: QgsLayoutItemLegend, layer_id: str) -> None:
    """Take a layer off the legend because its map stopped drawing it, noting
    where it stood. Its headings, class labels and order are kept as for any
    removed layer, and `unpark_layer` puts it all back. Not an undo step."""
    node = _node_for(legend, layer_id)
    parent = node.parent() if node is not None else None
    if node is None or parent is None:
        return
    siblings = [
        child.layerId()
        for child in parent.children()
        if isinstance(child, QgsLayerTreeLayer)
    ]
    index = siblings.index(layer_id)
    path: list[str] = []
    group = parent
    while group is not None and group.parent() is not None:
        path.insert(0, group.name())
        group = group.parent()
    parked = _parked(legend)
    parked[layer_id] = {"group": path, "after": siblings[index - 1] if index else ""}
    _save_parked(legend, parked)
    _remove_layer(legend, node)


def unpark_layer(
    legend: QgsLayoutItemLegend, project: QgsProject, layer: QgsMapLayer
) -> bool:
    """List a layer again if the legend listed it when its map dropped it,
    in the group and place it had. A layer the legend never listed (or the
    user took out) stays out. Returns whether it was listed."""
    spec = _parked(legend).get(layer.id())
    if spec is None or _node_for(legend, layer.id()) is not None:
        return False
    parked = _parked(legend)
    del parked[layer.id()]
    _save_parked(legend, parked)
    group: QgsLayerTreeGroup = legend.model().rootGroup()
    for name in spec.get("group") or []:  # type: ignore[attr-defined]
        found = group.findGroup(str(name))
        if found is None:
            break
        group = found
    after = _node_for(legend, str(spec.get("after") or ""))
    if after is not None and after.parent() is group:
        place = (group, group.children().index(after) + 1)
    elif not spec.get("after"):
        place = (group, 0)
    else:
        place = None
    _restore_layer(legend, project, layer, place)
    return True


def set_layer_listed(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    layer_id: str,
    listed: bool,
    text: str = "",
) -> bool:
    """List or unlist a layer in this legend only. False when nothing changed.
    `text` names the undo step (it is formatted with `name`)."""
    node = _node_for(legend, layer_id)
    layer = project.mapLayer(layer_id)
    if (node is not None) == listed or (listed and layer is None):
        return False
    name = layer.name() if layer is not None else layer_id
    text = (
        text
        or ("Show {name} in the legend" if listed else "Hide {name} from the legend")
    ).format(name=name)
    with _command(layout, legend, text):
        _ensure_manual(legend, project)
        node = _node_for(legend, layer_id)
        if listed and node is None and layer is not None:
            _restore_layer(legend, project, layer)
        elif not listed and node is not None:
            _remove_layer(legend, node)
    return True


def set_class_listed(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    layer_id: str,
    index: int,
    listed: bool,
    text: str = "",
) -> bool:
    """List or unlist one class (legend node) of a listed layer."""
    node = _node_for(legend, layer_id)
    layer = project.mapLayer(layer_id)
    if node is None or layer is None:
        return False
    count = len(_source_nodes(layer))
    if not 0 <= index < count:
        return False
    current = (
        list(QgsMapLayerLegendUtils.legendNodeOrder(node))
        if QgsMapLayerLegendUtils.hasLegendNodeOrder(node)
        else list(range(count))
    )
    if (index in current) == listed:
        return False
    if not listed and len(current) <= 1:
        return False  # a layer with every class off is "hide the layer"
    text = (
        text
        or (
            "Show a class of {name} in the legend"
            if listed
            else "Hide a class of {name} from the legend"
        )
    ).format(name=layer.name())
    with _command(layout, legend, text):
        _ensure_manual(legend, project)
        node = _node_for(legend, layer_id)
        if node is None:
            return False
        if listed:
            # Back at its natural place among the classes still listed.
            position = sum(1 for other in current if other < index)
            current.insert(position, index)
        else:
            current.remove(index)
        QgsMapLayerLegendUtils.setLegendNodeOrder(node, current)
        _refresh_node(legend, node)
    return True


def set_entry_label(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    entry: LegendEntry,
    text: str,
    undo_text: str = "Change a legend label",
) -> bool:
    """Set the label this legend shows for an entry; the source layer and
    its renderer keep their names. An empty text, or the source's own,
    clears the override."""
    node = _node_for(legend, entry.layer_id)
    if node is None or not entry.editable:
        return False
    text = text.strip()
    override = "" if text == entry.source_label else text
    if override == (
        _text(node.customProperty(TITLE_PROPERTY))
        if entry.kind is EntryKind.LAYER
        else _text(QgsMapLayerLegendUtils.legendNodeUserLabel(node, entry.index or 0))
    ):
        return False
    with _command(layout, legend, undo_text):
        _ensure_manual(legend, project)
        node = _node_for(legend, entry.layer_id)
        if node is None:
            return False
        if entry.kind is EntryKind.LAYER:
            if override:
                node.setCustomProperty(TITLE_PROPERTY, override)
            else:
                node.removeCustomProperty(TITLE_PROPERTY)
        else:
            QgsMapLayerLegendUtils.setLegendNodeUserLabel(
                node, entry.index or 0, override
            )
        _refresh_node(legend, node)
    return True


def move_entry(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    entry: LegendEntry,
    step: int,
    undo_text: str = "Move a legend entry",
) -> bool:
    """Move a listed layer past its neighbouring layer, or a listed class past
    its neighbouring class (`step` is -1 up, +1 down). False at an end."""
    node = _node_for(legend, entry.layer_id)
    if node is None or not entry.included or step not in (-1, 1):
        return False
    if entry.kind is EntryKind.CLASS:
        if not QgsMapLayerLegendUtils.hasLegendNodeOrder(node):
            layer = project.mapLayer(entry.layer_id)
            order = list(range(len(_source_nodes(layer)))) if layer else []
        else:
            order = list(QgsMapLayerLegendUtils.legendNodeOrder(node))
        if entry.index not in order:
            return False
        at = order.index(entry.index)
        to = at + step
        if not 0 <= to < len(order):
            return False
        with _command(layout, legend, undo_text):
            _ensure_manual(legend, project)
            node = _node_for(legend, entry.layer_id)
            if node is None:
                return False
            order[at], order[to] = order[to], order[at]
            QgsMapLayerLegendUtils.setLegendNodeOrder(node, order)
            _refresh_node(legend, node)
        return True

    nodes = _layer_nodes(legend)
    at = next((i for i, n in enumerate(nodes) if n.layerId() == entry.layer_id), -1)
    to = at + step
    if at < 0 or not 0 <= to < len(nodes):
        return False
    other = nodes[to]
    parent = node.parent()
    if parent is None or other.parent() is not parent:
        return False
    with _command(layout, legend, undo_text):
        _ensure_manual(legend, project)
        node = _node_for(legend, entry.layer_id)
        other = _node_for(legend, nodes[to].layerId())
        if node is None or other is None or node.parent() is not other.parent():
            return False
        parent = node.parent()
        copy = node.clone()
        parent.removeChildNode(node)
        other = _node_for(legend, nodes[to].layerId())
        if other is None:
            return False
        base = parent.children().index(other)
        parent.insertChildNode(base + (1 if step > 0 else 0), copy)
        copy_node = _node_for(legend, entry.layer_id)
        if copy_node is not None:
            _refresh_node(legend, copy_node)
    return True


def restore_removed(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    undo_text: str = "Restore the legend's removed entries",
) -> int:
    """Put back every restorable layer and class, as one undo step. Returns
    how many entries came back."""
    pending = restorable(legend, project)
    if not pending:
        return 0
    count = 0
    with _command(layout, legend, undo_text):
        _ensure_manual(legend, project)
        for entry in pending:
            if entry.kind is EntryKind.LAYER:
                layer = project.mapLayer(entry.layer_id)
                if layer is not None and _node_for(legend, entry.layer_id) is None:
                    _restore_layer(legend, project, layer)
                    count += 1
        by_layer = {e.layer_id for e in pending if e.kind is EntryKind.CLASS}
        for layer_id in by_layer:
            node = _node_for(legend, layer_id)
            layer = project.mapLayer(layer_id)
            if node is not None and layer is not None:
                total = len(_source_nodes(layer))
                had = (
                    len(QgsMapLayerLegendUtils.legendNodeOrder(node))
                    if QgsMapLayerLegendUtils.hasLegendNodeOrder(node)
                    else total
                )
                QgsMapLayerLegendUtils.setLegendNodeOrder(node, list(range(total)))
                _refresh_node(legend, node)
                count += total - had
    return count


def set_columns(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    columns: int,
    undo_text: str = "Change legend columns",
) -> bool:
    columns = max(1, int(columns))
    if legend.columnCount() == columns:
        return False
    with _command(layout, legend, undo_text):
        legend.setColumnCount(columns)
        legend.setSplitLayer(columns > 1)
    return True


def row_spacing_mm(legend: QgsLayoutItemLegend) -> float:
    return float(
        legend.rstyle(QgsLegendStyle.Style.Symbol).margin(QgsLegendStyle.Side.Top)
    )


def set_row_spacing(
    layout: QgsPrintLayout | None,
    legend: QgsLayoutItemLegend,
    millimetres: float,
    undo_text: str = "Change legend spacing",
) -> bool:
    millimetres = max(0.0, float(millimetres))
    if abs(row_spacing_mm(legend) - millimetres) < 0.005:
        return False
    with _command(layout, legend, undo_text):
        for style_id in (QgsLegendStyle.Style.Symbol, QgsLegendStyle.Style.SymbolLabel):
            legend.rstyle(style_id).setMargin(QgsLegendStyle.Side.Top, millimetres)
    return True


# ---- Template defaults ---------------------------------------------------


def support_reason(layer: QgsMapLayer) -> str:
    """Why `layer` is a technical support layer for a legend ("hillshade",
    "basemap"), or "" for one a reader should see. Only rasters and tiles
    qualify, by name or by being a web tile service: an arbitrary raster
    stays listed."""
    if isinstance(layer, QgsRasterLayer):
        provider = layer.dataProvider().name() if layer.dataProvider() else ""
        if provider in _TILE_PROVIDERS:
            return "basemap"
        match = _SUPPORT_NAME.search(layer.name())
        if match:
            hill = re.search(r"shad|relief|multi", match.group(0), re.IGNORECASE)
            return "hillshade" if hill else "basemap"
        return ""
    if layer.type() == QgsMapLayerType.VectorTileLayer:
        return "basemap"
    return ""


def raster_units(layer: QgsMapLayer) -> str:
    """The unit of a colour-ramp raster's values: the layer's units property,
    else the unit its data file declares for the band; "" when unknown."""
    if not isinstance(layer, QgsRasterLayer):
        return ""
    declared = _text(layer.customProperty(UNITS_PROPERTY)).strip()
    if declared:
        return declared
    renderer = layer.renderer()
    band_of = getattr(renderer, "inputBand", None) or getattr(renderer, "band", None)
    band = band_of() if band_of is not None else 1
    if layer.dataProvider() is None or layer.dataProvider().name() != "gdal":
        return ""
    try:
        from osgeo import gdal

        dataset = gdal.Open(layer.source().split("|")[0])
        unit = dataset.GetRasterBand(max(int(band), 1)).GetUnitType() if dataset else ""
    except Exception:  # unit lookup is a courtesy; never block a legend
        return ""
    return _text(unit).strip()


def _wants_unit(layer: QgsMapLayer) -> bool:
    renderer = layer.renderer() if isinstance(layer, QgsRasterLayer) else None
    return isinstance(renderer, QgsSingleBandPseudoColorRenderer) or (
        isinstance(renderer, QgsSingleBandGrayRenderer)
        and bool(_text(layer.customProperty(UNITS_PROPERTY)))
    )


def title_with_unit(layer: QgsMapLayer) -> str:
    """The layer's name with its unit in brackets when it has one that the
    name does not already show; else its name."""
    name = layer.name()
    if _HAS_UNIT.search(name) or not _wants_unit(layer):
        return name
    unit = raster_units(layer)
    return f"{name} ({unit})" if unit else name


def apply_template_defaults(legend: QgsLayoutItemLegend) -> list[str]:
    """What a new template legend lists: support layers (hillshade, basemap)
    are left out - restorable - and a colour-ramp raster gets its unit in its
    heading. A legend of nothing but support layers is left whole. Returns
    the ids left out. Not an undo step: the legend is being built."""
    root = legend.model().rootGroup()
    nodes = root.findLayers()
    layers = [(node, node.layer()) for node in nodes if node.layer() is not None]
    support = [(n, ly) for n, ly in layers if support_reason(ly)]
    left_out: list[str] = []
    if support and len(support) < len(layers):
        for node, layer in support:
            _remove_layer(legend, node)
            left_out.append(layer.id())
        legend.setCustomProperty(SUPPORT_LEFT_OUT_PROPERTY, json.dumps(left_out))
    for node in root.findLayers():
        layer = node.layer()
        if layer is None or node.customProperty(TITLE_PROPERTY):
            continue
        titled = title_with_unit(layer)
        if titled != layer.name():
            node.setCustomProperty(TITLE_PROPERTY, titled)
    legend.invalidateCache()
    return left_out
