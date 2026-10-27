"""Making, linking and removing the layout items the page preview adds.

The items are ordinary QGIS layout items - the same classes the layout
designer makes - so everything else on the page (the undo stack, the designer,
the HTML export, the native PDF and PNG) treats them like any other. Each
change here is one logical step on the layout's own undo stack: QGIS pushes a
command for the item being added, and the properties set while making it join
that step in a macro.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from enum import Enum

from qgis.core import (
    Qgis,
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QPointF, QRectF

from ...core.layout_builder import _north_svg
from ...core.legend_edit import apply_template_defaults
from ..i18n import tr


class ItemKind(Enum):
    TEXT = "text"
    LEGEND = "legend"
    SCALE_BAR = "scale_bar"
    NORTH_ARROW = "north_arrow"


#: Items that draw something about a map and so need to be linked to one.
LINKED_KINDS = (ItemKind.LEGEND, ItemKind.SCALE_BAR, ItemKind.NORTH_ARROW)

#: The box a click (not a drag) gives each kind, in millimetres. A legend and
#: a scale bar size themselves to their contents; only their corner is placed.
DEFAULT_SIZE_MM = {
    ItemKind.TEXT: (60.0, 10.0),
    ItemKind.LEGEND: (50.0, 40.0),
    ItemKind.SCALE_BAR: (50.0, 12.0),
    ItemKind.NORTH_ARROW: (15.0, 15.0),
}

_DEFAULT_TEXT_POINTS = 12.0


def kind_title(kind: ItemKind) -> str:
    return {
        ItemKind.TEXT: tr("Text"),
        ItemKind.LEGEND: tr("Legend"),
        ItemKind.SCALE_BAR: tr("Scale bar"),
        ItemKind.NORTH_ARROW: tr("North arrow"),
    }[kind]


def map_frames(layout: QgsLayout) -> list[QgsLayoutItemMap]:
    """The layout's map items, by name (the scene's own order is arbitrary)."""
    return sorted(
        (item for item in layout.items() if isinstance(item, QgsLayoutItemMap)),
        key=lambda item: (map_label(item), item.uuid()),
    )


def map_label(map_item: QgsLayoutItemMap) -> str:
    """A map frame's name for a menu: what the designer's item list shows."""
    return str(map_item.displayName())


@contextlib.contextmanager
def one_step(layout: QgsLayout, text: str) -> Iterator[None]:
    """Everything done inside is a single undo step on the layout's stack."""
    stack = layout.undoStack().stack()
    stack.beginMacro(text)
    try:
        yield
    finally:
        stack.endMacro()


def create_item(
    layout: QgsLayout,
    kind: ItemKind,
    rect: QRectF,
    map_item: QgsLayoutItemMap | None,
) -> QgsLayoutItem:
    """Add one item of `kind` at `rect` (layout scene units) as one undo step.

    `map_item` is the map a legend, scale bar or north arrow is about; the
    caller chooses it (the only map, or the user's pick). The box's size is
    kept for a text box and a north arrow; a legend and a scale bar take the
    size their contents need, from the box's corner.
    """
    title = kind_title(kind)
    with one_step(layout, tr("Add {item}").format(item=title.lower())):
        item: QgsLayoutItem
        if kind is ItemKind.TEXT:
            label = QgsLayoutItemLabel(layout)
            label.setText(tr("Text"))
            text_format = label.textFormat()
            text_format.setSize(_DEFAULT_TEXT_POINTS)
            text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
            label.setTextFormat(text_format)
            item = label
        elif kind is ItemKind.LEGEND:
            legend = QgsLayoutItemLegend(layout)
            item = legend
        elif kind is ItemKind.SCALE_BAR:
            item = QgsLayoutItemScaleBar(layout)
        else:
            picture = QgsLayoutItemPicture(layout)
            picture.setResizeMode(QgsLayoutItemPicture.Zoom)
            picture.setPicturePath(_north_svg("arrow"), QgsLayoutItemPicture.FormatSVG)
            picture.setNorthMode(QgsLayoutItemPicture.GridNorth)
            item = picture
        layout.addLayoutItem(item)
        if map_item is not None and kind in LINKED_KINDS:
            item.setLinkedMap(map_item)
        if isinstance(item, QgsLayoutItemLegend) and map_item is not None:
            _list_map_layers(item, map_item)
        if isinstance(item, QgsLayoutItemScaleBar):
            item.applyDefaultSize()
        if kind in (ItemKind.LEGEND, ItemKind.SCALE_BAR):
            item.attemptMove(QgsLayoutPoint(rect.x(), rect.y(), layout.units()))
        else:
            item.attemptSetSceneRect(rect)
    return item


def _list_map_layers(legend: QgsLayoutItemLegend, map_item: QgsLayoutItemMap) -> None:
    """Give a new legend what a template legend gets: the layers its map
    draws, not every layer of the project, with the technical support
    layers (hillshade, basemap) left out and units in colour-ramp headings."""
    if hasattr(legend, "setSyncMode"):  # QGIS >= 4.0
        legend.setSyncMode(Qgis.LegendSyncMode.Manual)
    else:  # QGIS 3: the same switch, deprecated in 4.0
        legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.removeAllChildren()
    for layer in map_item.layersToRender():
        root.addLayer(layer)
    apply_template_defaults(legend)


def link_map(
    layout: QgsLayout, item: QgsLayoutItem, map_item: QgsLayoutItemMap
) -> bool:
    """Point a legend, scale bar or north arrow at another map, as one undo
    step. Returns whether anything changed."""
    if not hasattr(item, "setLinkedMap") or item.linkedMap() is map_item:
        return False
    layout.undoStack().beginCommand(item, tr("Change linked map"))
    try:
        item.setLinkedMap(map_item)
    finally:
        layout.undoStack().endCommand()
    return True


def default_rect(layout: QgsLayout, kind: ItemKind, top_left: QPointF) -> QRectF:
    """The box a click at `top_left` gives `kind`, in the layout's own units."""
    width, height = DEFAULT_SIZE_MM[kind]
    size = layout.convertToLayoutUnits(
        QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters)
    )
    return QRectF(top_left.x(), top_left.y(), size.width(), size.height())


def delete_items(layout: QgsLayout, items: list[QgsLayoutItem]) -> None:
    """Remove `items` as one undo step. A statistic callout goes whole, the
    others closing up (`callouts.remove_callout`), not one of its parts."""
    if not items:
        return
    from .callouts import callout_index, remove_callout

    callouts = sorted(
        {i for item in items if (i := callout_index(item)) is not None}, reverse=True
    )
    with one_step(layout, tr("Delete items")):
        for index in callouts:
            remove_callout(layout, index)
        for item in items:
            if callout_index(item) is None:
                layout.removeLayoutItem(item)


@contextlib.contextmanager
def item_edit(layout: QgsLayout, item: QgsLayoutItem, text: str) -> Iterator[None]:
    """A change to one item as one undo step. A step that changed nothing is
    not kept by QGIS, so applying an unchanged control leaves no trace."""
    layout.undoStack().beginCommand(item, text)
    try:
        yield
    finally:
        layout.undoStack().endCommand()
