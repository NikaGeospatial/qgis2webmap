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
import math
from collections.abc import Iterator
from enum import Enum

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemGroup,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPointXY,
    QgsRectangle,
    QgsUnitTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor

from ...core import live_count
from ...core.layout_builder import _north_svg
from ...core.legend_edit import apply_template_defaults
from ...core.locator_context import is_locator
from ...core.page_style import DAYLIGHT
from ..i18n import tr


class ItemKind(Enum):
    TEXT = "text"
    LEGEND = "legend"
    SCALE_BAR = "scale_bar"
    NORTH_ARROW = "north_arrow"
    LIVE_COUNT = "live_count"


#: Items that draw something about a map and so need to be linked to one.
LINKED_KINDS = (ItemKind.LEGEND, ItemKind.SCALE_BAR, ItemKind.NORTH_ARROW)

#: The box a click (not a drag) gives each kind, in millimetres. A legend and
#: a scale bar size themselves to their contents; only their corner is placed.
DEFAULT_SIZE_MM = {
    ItemKind.TEXT: (60.0, 10.0),
    ItemKind.LEGEND: (50.0, 40.0),
    ItemKind.SCALE_BAR: (50.0, 12.0),
    ItemKind.NORTH_ARROW: (15.0, 15.0),
    ItemKind.LIVE_COUNT: (40.0, 24.0),
}

_DEFAULT_TEXT_POINTS = 12.0


def kind_title(kind: ItemKind) -> str:
    return {
        ItemKind.TEXT: tr("Text"),
        ItemKind.LEGEND: tr("Legend"),
        ItemKind.SCALE_BAR: tr("Scale bar"),
        ItemKind.NORTH_ARROW: tr("North arrow"),
        ItemKind.LIVE_COUNT: tr("Live count"),
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


def bring_to_front(layout: QgsLayout, item: QgsLayoutItem) -> None:
    """Stack `item` above everything already on the page. QGIS stacks a new
    item by the number of items, which a template's legend or notes box (with
    a background and a higher position) is above, so the new item would be
    hidden under it. A group only moves its members: each member is raised
    first, in their stacking order, then the group."""
    members = (
        sorted(item.items(), key=lambda member: member.zValue())
        if isinstance(item, QgsLayoutItemGroup)
        else []
    )
    for part in (*members, item):
        while layout.raiseItem(part):
            pass


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
        elif kind is ItemKind.LIVE_COUNT:
            group = _create_live_count(layout, rect, map_item)
            bring_to_front(layout, group)
            return group
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
        bring_to_front(layout, item)
    return item


#: A Live count's look (Signal's figures): a short accent rule, the number
#: large and bold, its caption smaller and muted under it. In millimetres and
#: points.
_COUNT_MIN_WIDTH_MM = 30.0
_COUNT_RULE_MM = 0.8
_COUNT_NUMBER_MM = 12.0
_COUNT_CAPTION_MM = 8.0
_COUNT_NUMBER_PT = 28.0
_COUNT_CAPTION_PT = 11.0


def _count_label(
    layout: QgsLayout,
    item_id: str,
    text: str,
    rect: QRectF,
    points: float,
    bold: bool,
    colour: str,
) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setId(item_id)
    label.setText(text)
    text_format = label.textFormat()
    text_format.setSize(points)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(colour))
    font = text_format.font()
    font.setBold(bold)
    text_format.setFont(font)
    label.setTextFormat(text_format)
    layout.addLayoutItem(label)
    label.attemptSetSceneRect(rect)
    return label


def _unique_count_name(layout: QgsLayout) -> str:
    """ "Live count", then "Live count 2" ...: a name no item has."""
    taken = {item.id() for item in layout.items() if isinstance(item, QgsLayoutItem)}
    base = tr("Live count")
    name, number = base, 2
    while name in taken:
        name = f"{base} {number}"
        number += 1
    return name


def _create_live_count(
    layout: QgsLayout, rect: QRectF, map_item: QgsLayoutItemMap | None
) -> QgsLayoutItemGroup:
    """A Live count at `rect`'s corner: the number (which holds the setup),
    its caption and the accent rule, following `map_item`, as one group (the
    item the user moves, selects and deletes). The caller's undo step is
    open."""
    scale = layout.convertToLayoutUnits(
        QgsLayoutSize(1, 0, QgsUnitTypes.LayoutMillimeters)
    ).width()
    width = max(rect.width(), _COUNT_MIN_WIDTH_MM * scale)
    x, y = rect.x(), rect.y()
    name = _unique_count_name(layout)
    rule = QgsLayoutItemShape(layout)
    rule.setShapeType(QgsLayoutItemShape.Rectangle)
    rule.setId(f"{name} rule")
    layout.addLayoutItem(rule)
    rule.attemptSetSceneRect(
        QRectF(x, y, min(8 * scale, width), _COUNT_RULE_MM * scale)
    )
    symbol = rule.symbol().clone()
    symbol.setColor(QColor(DAYLIGHT.accent))
    symbol.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
    rule.setSymbol(symbol)
    y += (_COUNT_RULE_MM + 1.5) * scale
    number = _count_label(
        layout,
        name,
        live_count.NO_COUNT_TEXT,
        QRectF(x, y, width, _COUNT_NUMBER_MM * scale),
        _COUNT_NUMBER_PT,
        True,
        DAYLIGHT.ink,
    )
    y += _COUNT_NUMBER_MM * scale
    caption = _count_label(
        layout,
        f"{name} caption",
        "",
        QRectF(x, y, width, _COUNT_CAPTION_MM * scale),
        _COUNT_CAPTION_PT,
        False,
        DAYLIGHT.muted,
    )
    live_count.write_spec(
        number,
        live_count.CountSpec(map_uuid=map_item.uuid() if map_item is not None else ""),
    )
    for part in (caption, rule):
        part.setCustomProperty(live_count.PART_OF_PROPERTY, number.uuid())
    # One item on the page: it moves, selects and deletes as a whole.
    return layout.groupItems([rule, number, caption])


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
        redraw(item)
    return True


def default_rect(layout: QgsLayout, kind: ItemKind, top_left: QPointF) -> QRectF:
    """The box a click at `top_left` gives `kind`, in the layout's own units."""
    width, height = DEFAULT_SIZE_MM[kind]
    size = layout.convertToLayoutUnits(
        QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters)
    )
    return QRectF(top_left.x(), top_left.y(), size.width(), size.height())


def delete_items(layout: QgsLayout, items: list[QgsLayoutItem]) -> None:
    """Remove `items` as one undo step."""
    if not items:
        return
    # A Live count is one thing to the user: taking its number off the page
    # takes its caption and rule too.
    with one_step(layout, tr("Delete items")):
        for item in live_count.with_count_parts(layout, items):
            layout.removeLayoutItem(item)


def redraw(item: QgsLayoutItem) -> None:
    """Make the layout view draw `item` again.

    Setting a property (a label's alignment, text or text format) does not
    repaint the item: QGIS's own widgets call `update()` after the change, and
    the preview panel would otherwise keep drawing the old picture until
    something else invalidated the item's cache."""
    if not sip.isdeleted(item):
        item.refresh()


@contextlib.contextmanager
def item_edit(layout: QgsLayout, item: QgsLayoutItem, text: str) -> Iterator[None]:
    """A change to one item as one undo step. A step that changed nothing is
    not kept by QGIS, so applying an unchanged control leaves no trace."""
    layout.undoStack().beginCommand(item, text)
    try:
        yield
    finally:
        layout.undoStack().endCommand()
        redraw(item)


#: A detail map's frame, against the map it zooms into: this share of its
#: width and height, showing this share of its ground - so the frame is smaller
#: than the source map and shows a closer view (the scale grows by their ratio).
_DETAIL_FRAME_SHARE = 0.4
_DETAIL_GROUND_SHARE = 0.2
_DETAIL_MIN_SIDE_MM = 30.0
_DETAIL_MARGIN_MM = 4.0
_DETAIL_FRAME_MM = 0.3

#: The custom property that marks a map frame as a detail map (the inspector
#: words its controls for choosing an area).
DETAIL_PROPERTY = "nika/detail-map"


def is_detail_map(item: object) -> bool:
    return isinstance(item, QgsLayoutItemMap) and bool(
        item.customProperty(DETAIL_PROPERTY, False)
    )


def detail_source(layout: QgsLayout) -> QgsLayoutItemMap | None:
    """The map a new detail map zooms into: the selected map frame when one
    map is selected, else the layout's primary map (its reference map, else the
    largest map that is not a locator inset), else any map."""
    chosen = [
        i for i in layout.selectedLayoutItems() if isinstance(i, QgsLayoutItemMap)
    ]
    if len(chosen) == 1 and len(layout.selectedLayoutItems()) == 1:
        return chosen[0]
    maps = map_frames(layout)
    if not maps:
        return None
    reference = layout.referenceMap()
    if reference is not None and not is_locator(reference):
        return reference  # type: ignore[no-any-return]
    main = [m for m in maps if not is_locator(m)] or maps
    return max(main, key=lambda m: m.rect().width() * m.rect().height())


def detail_name(layout: QgsLayout) -> str:
    """ "Detail map", then "Detail map 2" ...: a name no map on the page has."""
    taken = {m.id() for m in map_frames(layout)}
    base = tr("Detail map")
    if base not in taken:
        return base
    number = 2
    while f"{base} {number}" in taken:
        number += 1
    return f"{base} {number}"


def detail_rect(layout: QgsLayout, source: QgsLayoutItemMap) -> QRectF:
    """The detail frame's box (layout scene units): a corner of the source map,
    smaller than it, kept on the page."""
    margin = layout.convertToLayoutUnits(
        QgsLayoutSize(_DETAIL_MARGIN_MM, 0, QgsUnitTypes.LayoutMillimeters)
    ).width()
    minimum = layout.convertToLayoutUnits(
        QgsLayoutSize(_DETAIL_MIN_SIDE_MM, 0, QgsUnitTypes.LayoutMillimeters)
    ).width()
    frame = source.sceneBoundingRect()
    width = min(max(frame.width() * _DETAIL_FRAME_SHARE, minimum), frame.width() * 0.8)
    height = min(
        max(frame.height() * _DETAIL_FRAME_SHARE, minimum), frame.height() * 0.8
    )
    rect = QRectF(
        frame.right() - margin - width, frame.bottom() - margin - height, width, height
    )
    return _inside_source(rect, frame)


def _inside_source(rect: QRectF, frame: QRectF) -> QRectF:
    moved = QRectF(rect)
    moved.moveLeft(max(moved.left(), frame.left()))
    moved.moveTop(max(moved.top(), frame.top()))
    return moved


def detail_extent(source: QgsLayoutItemMap, rect: QRectF) -> QgsRectangle:
    """The ground a new detail map shows: around the middle of the source's,
    a fraction of it, in the shape of the detail frame (so nothing is padded)."""
    whole = source.extent()
    width = whole.width() * _DETAIL_GROUND_SHARE
    height = whole.height() * _DETAIL_GROUND_SHARE
    if rect.width() > 0 and rect.height() > 0 and height > 0:
        aspect = rect.width() / rect.height()
        if width / height > aspect:
            width = height * aspect
        else:
            height = width / aspect
    centre = whole.center()
    return QgsRectangle(
        centre.x() - width / 2,
        centre.y() - height / 2,
        centre.x() + width / 2,
        centre.y() + height / 2,
    )


def create_detail_map(layout: QgsLayout, source: QgsLayoutItemMap) -> QgsLayoutItemMap:
    """Add a detail map: another real map frame on the page, closer on an area
    of `source`, as one undo step. It follows the layers and styles the way the
    source does (the project's visible layers unless the source pins its own),
    has its own extent and scale, and draws no link to the source."""
    with one_step(layout, tr("Add {item}").format(item=tr("Detail map").lower())):
        item = QgsLayoutItemMap(layout)
        item.setId(detail_name(layout))
        item.setCustomProperty(DETAIL_PROPERTY, True)
        layout.addLayoutItem(item)
        rect = detail_rect(layout, source)
        extent = detail_extent(source, rect)
        item.setCrs(source.crs())
        item.setKeepLayerSet(source.keepLayerSet())
        if source.keepLayerSet():
            item.setLayers(source.layers())
        item.setKeepLayerStyles(source.keepLayerStyles())
        if source.keepLayerStyles():
            item.setLayerStyleOverrides(source.layerStyleOverrides())
        item.setFollowVisibilityPreset(source.followVisibilityPreset())
        item.setFollowVisibilityPresetName(source.followVisibilityPresetName())
        # The order the template builder uses: the extent on the fresh item
        # (which resizes it), the box back, then the zoom that fits the two.
        item.setExtent(extent)
        item.attemptSetSceneRect(rect)
        item.zoomToExtent(extent)
        item.setFrameEnabled(True)
        item.setFrameStrokeWidth(
            QgsLayoutMeasurement(_DETAIL_FRAME_MM, QgsUnitTypes.LayoutMillimeters)
        )
        item.setFrameStrokeColor(QColor("#444444"))
        item.setBackgroundEnabled(True)
        item.setBackgroundColor(QColor("#ffffff"))
        item.refresh()
        bring_to_front(layout, item)
    return item


_WGS84 = "EPSG:4326"


def _wgs84_transform(item: QgsLayoutItemMap, *, to_map: bool) -> QgsCoordinateTransform:
    """Between the map's CRS and WGS 84, with the project's transform context."""
    layout = item.layout()
    project = layout.project() if layout is not None else None
    wgs84 = QgsCoordinateReferenceSystem(_WGS84)
    source, target = (wgs84, item.crs()) if to_map else (item.crs(), wgs84)
    if project is None:
        return QgsCoordinateTransform(source, target, QgsCoordinateTransformContext())
    return QgsCoordinateTransform(source, target, project.transformContext())


def map_centre_wgs84(item: QgsLayoutItemMap) -> tuple[float, float] | None:
    """The centre of what the map shows, as (latitude, longitude) in WGS 84;
    None when the map's CRS cannot be carried to it."""
    try:
        point = _wgs84_transform(item, to_map=False).transform(item.extent().center())
    except Exception:  # QgsCsException, or a CRS without a valid transform
        return None
    if not (math.isfinite(point.x()) and math.isfinite(point.y())):
        return None
    return point.y(), point.x()


def recentre_map(item: QgsLayoutItemMap, latitude: float, longitude: float) -> bool:
    """Show the same ground size (so the same scale and rotation) centred on
    a WGS 84 point, as one undo step. False, with nothing changed, when the
    point cannot be carried into the map's CRS or lies outside its valid area."""
    layout = item.layout()
    if layout is None or not (-90.0 <= latitude <= 90.0):
        return False
    if not (-180.0 <= longitude <= 180.0):
        return False
    crs = item.crs()
    try:
        bounds = crs.bounds()
    except AttributeError:  # older QGIS without a bounds accessor
        bounds = QgsRectangle()
    if (
        bounds is not None
        and not bounds.isEmpty()
        and not bounds.contains(QgsPointXY(longitude, latitude))
    ):
        return False
    try:
        point = _wgs84_transform(item, to_map=True).transform(
            QgsPointXY(longitude, latitude)
        )
    except Exception:  # QgsCsException, or a CRS without a valid transform
        return False
    if not (math.isfinite(point.x()) and math.isfinite(point.y())):
        return False
    extent = item.extent()
    half_width, half_height = extent.width() / 2.0, extent.height() / 2.0
    target = QgsRectangle(
        point.x() - half_width,
        point.y() - half_height,
        point.x() + half_width,
        point.y() + half_height,
    )
    with item_edit(layout, item, tr("Centre map")):
        item.zoomToExtent(target)
    return True
