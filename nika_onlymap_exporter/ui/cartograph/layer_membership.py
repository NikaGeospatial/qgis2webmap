"""Which layers a Cartograph map frame draws, and the control that edits them.

The Layers list is shared between the web map and Cartograph. Its Include
column is the web map's export selection (`LayerSettings.include`); a
Cartograph reads the layers its layout's map items really draw
(`QgsLayoutItemMap.layersToRender()`), and the preview *is* that layout. Two
sources of truth made an unticked layer stay on the page while the summary
counted it out. Here, in Cartograph, the Include column is the selected map
frame's own membership instead, and the web map's selection is never touched.

A change is one undo macro on the layout's undo stack (Ctrl+Z in the layout
designer undoes it too): the map item's layer list, and the legends linked to
that map, which QGIS does not narrow to a map's layers by itself.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING

from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...core.frame_layers import rendered_layers, set_frame_layers
from ...core.legend_edit import (
    left_out_of_legend,
    park_layer,
    unpark_layer,
)
from ...qt_compat import legend_is_manual, set_legend_manual
from ..fitted_label import FittedLabel
from ..i18n import plural, tr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsMapLayer, QgsPrintLayout, QgsProject


class MembershipKind(Enum):
    """How a map item decides its layers."""

    CANVAS = "canvas"  # follows the QGIS Layers panel's ticks
    THEME = "theme"  # follows a map theme
    CUSTOM = "custom"  # its own list


def membership_kind(map_item: QgsLayoutItemMap) -> MembershipKind:
    if map_item.followVisibilityPreset():
        return MembershipKind.THEME
    if map_item.keepLayerSet():
        return MembershipKind.CUSTOM
    return MembershipKind.CANVAS


def drawn_layers(map_item: QgsLayoutItemMap) -> list[QgsMapLayer]:
    """The layers the map draws, top first - what the preview, the image or
    PDF and the HTML all read."""
    return list(rendered_layers(map_item))


def layout_legends(
    layout: QgsPrintLayout, map_item: QgsLayoutItemMap
) -> list[QgsLayoutItemLegend]:
    """The legends linked to `map_item`."""
    uuid = map_item.uuid()
    legends = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemLegend):
            continue
        linked = item.linkedMap()
        if linked is not None and linked.uuid() == uuid:
            legends.append(item)
    return legends


def _position_for(
    layer: QgsMapLayer,
    drawn: Sequence[QgsMapLayer],
    project: QgsProject,
    order_hint: Sequence[str] | None,
) -> int:
    """Where `layer` goes in `drawn` (top first) when it comes back.

    The order it was removed from, when that is known and still fits; else
    the Layers panel's own order, so a ticked layer lands where QGIS would
    draw it rather than on top.
    """
    ids = [other.id() for other in drawn]
    if order_hint and layer.id() in order_hint:
        before = order_hint[: order_hint.index(layer.id())]
        for neighbour in reversed(before):
            if neighbour in ids:
                return ids.index(neighbour) + 1
        if any(other in order_hint for other in ids):
            return 0
    tree_order = [other.id() for other in project.layerTreeRoot().layerOrder()]
    rank = {layer_id: index for index, layer_id in enumerate(tree_order)}
    mine = rank.get(layer.id(), len(rank))
    for index, other in enumerate(drawn):
        if rank.get(other.id(), len(rank)) > mine:
            return index
    return len(drawn)


#: On a map item: JSON `{layer_id: style xml}`, the per-map styles of layers
#: the map does not draw now.
INACTIVE_OVERRIDES_PROPERTY = "qgis2webmap/inactive_style_overrides"


def _inactive_overrides(map_item: QgsLayoutItemMap) -> dict[str, str]:
    raw = map_item.customProperty(INACTIVE_OVERRIDES_PROPERTY)
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_inactive_overrides(map_item: QgsLayoutItemMap, stash: dict[str, str]) -> None:
    if stash:
        map_item.setCustomProperty(INACTIVE_OVERRIDES_PROPERTY, json.dumps(stash))
    else:
        map_item.removeCustomProperty(INACTIVE_OVERRIDES_PROPERTY)


def _apply_to_map(
    map_item: QgsLayoutItemMap, project: QgsProject, layers: Sequence[QgsMapLayer]
) -> None:
    """Make `layers` the map's own list, carrying over how it drew before.

    A map that followed the Layers panel or a map theme stops following it:
    the theme's per-layer styles stay with the map, so nothing is restyled.
    """
    kind = membership_kind(map_item)
    stash = _inactive_overrides(map_item)
    if kind is MembershipKind.THEME:
        theme = map_item.followVisibilityPresetName()
        overrides = dict(project.mapThemeCollection().mapThemeStyleOverrides(theme))
        map_item.setFollowVisibilityPreset(False)
        map_item.setKeepLayerStyles(True)
    elif kind is MembershipKind.CUSTOM and map_item.keepLayerStyles():
        overrides = dict(map_item.layerStyleOverrides())
    else:
        overrides = {}
    if kind is not MembershipKind.CANVAS:
        # A layer that leaves the map keeps its style on the map, set aside,
        # and gets it back when it returns (smoothing, suppressed labels).
        ids = {layer.id() for layer in layers}
        stash.update({key: value for key, value in overrides.items() if key not in ids})
        active = {key: value for key, value in overrides.items() if key in ids}
        for layer in layers:
            if layer.id() not in active and layer.id() in stash:
                active[layer.id()] = stash[layer.id()]
        for layer in layers:
            stash.pop(layer.id(), None)
        map_item.setKeepLayerStyles(map_item.keepLayerStyles() or bool(active))
        map_item.setLayerStyleOverrides(active)
        _save_inactive_overrides(map_item, stash)
    set_frame_layers(map_item, layers)
    map_item.invalidateCache()


def _legend_layer_nodes(legend: QgsLayoutItemLegend, layer_id: str) -> list:
    return [
        node
        for node in legend.model().rootGroup().findLayers()
        if node.layerId() == layer_id
    ]


def _insert_new_layer(
    legend: QgsLayoutItemLegend, layer: QgsMapLayer, final_layers: Sequence[QgsMapLayer]
) -> None:
    above = [other.id() for other in final_layers]
    above = above[: above.index(layer.id())]
    for neighbour in reversed(above):
        nodes = _legend_layer_nodes(legend, neighbour)
        if nodes:
            parent = nodes[0].parent()
            parent.insertLayer(parent.children().index(nodes[0]) + 1, layer)
            return
    legend.model().rootGroup().insertLayer(0, layer)


def _sync_legend(
    legend: QgsLayoutItemLegend,
    project: QgsProject,
    layer: QgsMapLayer,
    included: bool,
    final_layers: Sequence[QgsMapLayer],
) -> None:
    """Keep `legend` in step with the map's layers after `layer` changed.

    QGIS lists every project layer in an automatic legend whatever a map
    draws, so leaving a layer out of the map turns that legend into a manual
    one holding only the map's layers (the cartograph's own legend already
    lists just those). A manual legend loses or regains the one layer's entry.
    """
    root = legend.model().rootGroup()
    final_ids = {other.id() for other in final_layers}
    if not included:
        if not _legend_layer_nodes(legend, layer.id()):
            return
        if not legend_is_manual(legend):
            set_legend_manual(legend)
            root = legend.model().rootGroup()
            for node in root.findLayers():
                if node.layerId() not in final_ids and node.parent() is not None:
                    node.parent().removeChildNode(node)
        # Its heading, class labels, left-out classes and place are kept.
        park_layer(legend, layer.id())
    else:
        if not legend_is_manual(legend) or _legend_layer_nodes(legend, layer.id()):
            return
        # A layer the legend listed when its map dropped it comes back as it
        # was; one left out of the legend stays out; any other is new to it.
        if not unpark_layer(legend, project, layer) and not left_out_of_legend(
            legend, layer.id()
        ):
            _insert_new_layer(legend, layer, final_layers)
    legend.invalidateCache()
    adjust = getattr(legend, "adjustBoxSize", None)
    if adjust is not None:
        adjust()
    legend.update()


def set_layer_included(
    layout: QgsPrintLayout,
    project: QgsProject,
    map_item: QgsLayoutItemMap,
    layer_id: str,
    included: bool,
    order_hint: Sequence[str] | None = None,
) -> bool:
    """Put `layer_id` in or out of `map_item`, as one undo step.

    The map's list and its linked legends change together, so Undo and Redo
    move them together. Returns False when nothing needed to change.
    """
    layer = project.mapLayer(layer_id)
    drawn = drawn_layers(map_item)
    if layer is None or (layer_id in {other.id() for other in drawn}) == included:
        return False
    if included:
        new_layers = list(drawn)
        new_layers.insert(_position_for(layer, drawn, project, order_hint), layer)
        text = tr("Add layer {name} to the map").format(name=layer.name())
    else:
        new_layers = [other for other in drawn if other.id() != layer_id]
        text = tr("Remove layer {name} from the map").format(name=layer.name())

    stack = layout.undoStack()
    stack.beginMacro(text)
    try:
        stack.beginCommand(map_item, text)
        _apply_to_map(map_item, project, new_layers)
        stack.endCommand()
        for legend in layout_legends(layout, map_item):
            stack.beginCommand(legend, text)
            _sync_legend(legend, project, layer, included, new_layers)
            stack.endCommand()
    finally:
        stack.endMacro()
    return True


def coverage_layer_left_out(
    layout: QgsPrintLayout, map_item: QgsLayoutItemMap
) -> QgsMapLayer | None:
    """The atlas's coverage layer when this map is the one that follows the
    atlas and no longer draws it, else None."""
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return None
    coverage = atlas.coverageLayer()
    if coverage is None:
        return None
    if not map_item.atlasDriven():
        return None
    if coverage.id() in {layer.id() for layer in drawn_layers(map_item)}:
        return None
    return coverage


class _FrameCombo(QComboBox):
    """A frame list that never answers the wheel (it scrolls the panel) and
    takes focus from the keyboard only."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class LayerMembershipBar(QWidget):
    """Above the Layers list in Cartograph: which map frame the list edits
    (a selector only when the layout has several), what that frame follows,
    and the atlas note."""

    frame_changed = pyqtSignal(str)  # the selected map item's uuid

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 4)
        column.setSpacing(4)

        self._frame_row = QWidget(self)
        row = QHBoxLayout(self._frame_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.frame_label = QLabel(tr("Layers for:"), self._frame_row)
        row.addWidget(self.frame_label)
        self.frame_combo = _FrameCombo(self._frame_row)
        self.frame_combo.setAccessibleName(tr("Map frame whose layers are listed"))
        self.frame_combo.setToolTip(
            tr("The map frame the Include ticks add layers to or remove them from")
        )
        self.frame_label.setBuddy(self.frame_combo)
        row.addWidget(self.frame_combo, 1)
        column.addWidget(self._frame_row)
        self.frame_combo.activated.connect(self._on_activated)

        self.note = FittedLabel("", self)
        self.note.setProperty("role", "hint")
        column.addWidget(self.note)

        self.atlas_note = FittedLabel("", self)
        self.atlas_note.setProperty("role", "hint")
        column.addWidget(self.atlas_note)
        self.atlas_note.hide()

    def _on_activated(self, index: int) -> None:
        uuid = self.frame_combo.itemData(index)
        if isinstance(uuid, str):
            self.frame_changed.emit(uuid)

    def set_frames(self, frames: Sequence[tuple[str, str]], selected: str) -> None:
        """`(uuid, name)` per map frame; the selector shows for two or more."""
        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        for uuid, name in frames:
            self.frame_combo.addItem(name, uuid)
        index = self.frame_combo.findData(selected)
        self.frame_combo.setCurrentIndex(max(index, 0))
        self.frame_combo.blockSignals(False)
        self._frame_row.setVisible(len(frames) > 1)

    def set_kind(self, kind: MembershipKind | None, theme_name: str = "") -> None:
        if kind is MembershipKind.CANVAS:
            text = tr(
                "This map shows the layers ticked in the QGIS Layers panel. "
                "Changing a tick here gives it its own list of layers."
            )
        elif kind is MembershipKind.THEME:
            text = tr(
                "This map shows the map theme {theme}. Changing a tick here "
                "gives it its own list of layers, drawn as the theme styled them."
            ).format(theme=theme_name)
        elif kind is MembershipKind.CUSTOM:
            text = tr("This map has its own list of layers.")
        else:
            text = ""
        self.note.setText(text)

    def set_atlas_note(self, layer_name: str | None) -> None:
        if layer_name:
            self.atlas_note.setText(
                tr(
                    "The atlas still pages through {layer}; it is just not "
                    "drawn on this map."
                ).format(layer=layer_name)
            )
        self.atlas_note.setVisible(bool(layer_name))


def summary_text(included: int, total: int) -> str:
    return (
        plural(
            total,
            "{included} of {n} layer included",
            "{included} of {n} layers included",
            included=included,
        )
        if total
        else ""
    )


class LayerMembership:
    """Ties the bar, the selected frame and the dialog's Include column to
    the current layout. Owned by `CartographController`."""

    def __init__(self, controller) -> None:  # CartographController
        self._controller = controller
        self.bar: LayerMembershipBar | None = None
        self._selected_uuid = ""
        # Per map item: the layer order at the moment a layer was removed.
        self._order_hints: dict[str, dict[str, list[str]]] = {}
        # What the bar and the Include column last showed, so the many layout
        # changes that are not about layers (a moved label) redo nothing.
        self._shown: tuple[object, ...] | None = None
        # True while one tick's undo macro runs: what the layout emits then is
        # one edit, announced once when it is done.
        self.editing = False

    # ---- Wiring ----------------------------------------------------------

    def attach(self, parent: QWidget) -> LayerMembershipBar:
        self.bar = LayerMembershipBar(parent)
        self.bar.frame_changed.connect(self._on_frame_chosen)
        return self.bar

    @property
    def _dialog(self):
        return self._controller.dialog

    def _layout(self):
        layout = self._controller._current_layout
        if layout is None:
            return None
        return None if sip.isdeleted(layout) else layout

    def map_item(self) -> QgsLayoutItemMap | None:
        layout = self._layout()
        if layout is None:
            return None
        frames = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
        for item in frames:
            if item.uuid() == self._selected_uuid:
                return item
        return None

    def is_active(self) -> bool:
        """Whether the Include column edits a map frame right now."""
        from ...core.settings import DialogOutput

        return (
            self._dialog.state.output is DialogOutput.CARTOGRAPH
            and self.map_item() is not None
        )

    # ---- Reading ---------------------------------------------------------

    def drawn_ids(self) -> set[str]:
        item = self.map_item()
        return {layer.id() for layer in drawn_layers(item)} if item else set()

    def included(self, layer_id: str, web_default: bool) -> bool:
        """The Include tick for a layer: the frame's membership in Cartograph,
        the web map's own selection otherwise."""
        if not self.is_active():
            return web_default
        return layer_id in self.drawn_ids()

    def summary(self, available: Sequence[str]) -> str | None:
        if not self.is_active():
            return None
        drawn = self.drawn_ids()
        return summary_text(
            sum(1 for layer_id in available if layer_id in drawn), len(available)
        )

    # ---- Following the layout -------------------------------------------

    def layout_changed(self) -> None:
        """A layout was opened, or its frames or layers changed (an edit, an
        undo or the layout designer)."""
        layout = self._layout()
        if self.bar is None:
            return
        if layout is None:
            self._shown = None
            self._selected_uuid = ""
            self.bar.set_frames([], "")
            self.bar.set_kind(None)
            self.bar.set_atlas_note(None)
            self._dialog.sync_include_column()
            return
        from .frames import layout_map_items

        frames = layout_map_items(layout)
        uuids = [frame.item_id for frame in frames]
        if self._selected_uuid not in uuids:
            self._selected_uuid = self._default_frame(layout, uuids)
        shown = (
            layout.name(),
            tuple((f.item_id, f.name) for f in frames),
            self._selected_uuid,
            self._state_key(),
        )
        if shown == self._shown:
            return
        self._shown = shown
        self.bar.set_frames([(f.item_id, f.name) for f in frames], self._selected_uuid)
        self._show_state()

    def _state_key(self) -> tuple[object, ...]:
        item, layout = self.map_item(), self._layout()
        if item is None or layout is None:
            return ()
        left_out = coverage_layer_left_out(layout, item)
        return (
            tuple(layer.id() for layer in drawn_layers(item)),
            membership_kind(item),
            left_out.id() if left_out else "",
        )

    @staticmethod
    def _default_frame(layout: QgsPrintLayout, uuids: list[str]) -> str:
        """The biggest map: the main one, not an inset."""
        best, best_area = "", -1.0
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap) and item.uuid() in uuids:
                area = item.rect().width() * item.rect().height()
                if area > best_area:
                    best, best_area = item.uuid(), area
        return best

    def _show_state(self) -> None:
        item = self.map_item()
        layout = self._layout()
        if self.bar is None:
            return
        if item is None or layout is None:
            self.bar.set_kind(None)
            self.bar.set_atlas_note(None)
        else:
            self.bar.set_kind(membership_kind(item), item.followVisibilityPresetName())
            left_out = coverage_layer_left_out(layout, item)
            self.bar.set_atlas_note(left_out.name() if left_out else None)
        self._dialog.sync_include_column()

    def _on_frame_chosen(self, uuid: str) -> None:
        self._selected_uuid = uuid
        self._shown = None
        self.layout_changed()
        # The chosen map is the one selected on the page. The page reports
        # the same map back, which `page_selection_changed` finds already
        # chosen, so nothing loops.
        layout = self._layout()
        item = self.map_item()
        if layout is not None and item is not None:
            layout.setSelectedItem(item)

    def page_selection_changed(self, uuid: str | None) -> None:
        """A map frame was selected on the page: the bar follows it. Other
        selections (a label, nothing) leave the bar where it is."""
        if not uuid or uuid == self._selected_uuid or self.bar is None:
            return
        if uuid not in {
            self.bar.frame_combo.itemData(i)
            for i in range(self.bar.frame_combo.count())
        }:
            return
        self._selected_uuid = uuid
        self._shown = None
        self.layout_changed()

    # ---- Editing ---------------------------------------------------------

    def set_included(self, layer_id: str, included: bool) -> None:
        """The Include tick of one layer was changed by the user."""
        item = self.map_item()
        layout = self._layout()
        if item is None or layout is None:
            return
        hints = self._order_hints.setdefault(item.uuid(), {})
        before = [layer.id() for layer in drawn_layers(item)]
        controller = self._controller
        self.editing = True
        try:
            changed = set_layer_included(
                layout,
                controller.project,
                item,
                layer_id,
                included,
                hints.get(layer_id),
            )
        finally:
            self.editing = False
        if changed:
            if not included:
                hints[layer_id] = before
            # The layout's own signals were muted while the macro ran so each
            # step did not start a build; one does now.
            controller._on_layout_content_changed()
        self._shown = None
        self.layout_changed()

    def refresh_layout(self) -> None:
        with contextlib.suppress(Exception):
            layout = self._layout()
            if layout is not None:
                layout.refresh()
