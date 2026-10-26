"""What a locator inset draws, and how much of the world it shows.

A locator says where the main map is. It needs *wider context* -- data that
reaches beyond the main map's view -- and it needs to be quiet: the outline
of the main map is the subject, so the inset draws the area's layers in
their own style but without labels, and never as a copy of the main map's
full label set.

The view comes from the data on hand, offline: the extent of the context
layers, padded and held to a sensible multiple of the main view, not a fixed
zoom-out. When the layers do not reach past the main view there is no wider
context to show; the inset is then drawn at a modest fixed margin and marked
(`CONTEXT_PROPERTY`) so Checks can say so and the user can leave it out. A
remote basemap is never added here: that is an explicit choice of the user's.

The helpers that edit an existing inset (`fit_locator_to_context`,
`set_locator_context_layers`, `omit_locator`) are undoable layout-map
operations for the interactive tools to call.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)

from .raster_smoothing import own_layers, store_overrides, style_xml

#: The most of the world an inset shows, per side, relative to the main view.
MAX_ZOOM_OUT = 8.0
#: The context must reach this much wider than the main view (per side, in
#: either direction) to count as wider context at all.
MIN_CONTEXT_RATIO = 1.5
#: How much of the surroundings an inset shows when there is no wider
#: context: only a margin round the main view, with the outline.
NO_CONTEXT_ZOOM_OUT = 2.5
#: Room left round the context layers' extent.
_PADDING = 0.08

#: The locator map item's custom property saying whether its view came from
#: wider context ("layers") or had none to use ("none").
CONTEXT_PROPERTY = "nika/locator-context"
CONTEXT_LAYERS = "layers"
CONTEXT_NONE = "none"


@dataclass(frozen=True)
class LocatorView:
    """The view an inset takes: `extent` in the map CRS, the `layers` it
    draws, and whether those layers gave it any wider context."""

    extent: QgsRectangle
    layers: tuple[QgsMapLayer, ...]
    has_context: bool


def default_context_layers(layers: Sequence[QgsMapLayer]) -> list[QgsMapLayer]:
    """The layers an inset draws by default: the quiet area-wide ones --
    rasters and polygons -- and not the points that mark places on the main
    map, nor lines: a busy network (roads, streams, contours) at the inset's
    scale buries the outline of the main map. Where there are no areas, the
    lines; where there is nothing else, the points themselves."""
    areas = [
        layer
        for layer in layers
        if isinstance(layer, QgsRasterLayer)
        or (
            isinstance(layer, QgsVectorLayer)
            and layer.geometryType() == Qgis.GeometryType.Polygon
        )
    ]
    lines = [
        layer
        for layer in layers
        if isinstance(layer, QgsVectorLayer)
        and layer.geometryType() == Qgis.GeometryType.Line
    ]
    return areas or lines or list(layers)


def _extent_in(
    layer: QgsMapLayer, crs: QgsCoordinateReferenceSystem, project: QgsProject
) -> QgsRectangle | None:
    try:
        extent = QgsCoordinateTransform(layer.crs(), crs, project).transformBoundingBox(
            layer.extent()
        )
    except Exception:  # a layer QGIS cannot place in this CRS adds no context
        return None
    if extent.isNull() or extent.isEmpty() or not extent.isFinite():
        return None
    return extent


def fit_to_aspect(view: QgsRectangle, aspect: float) -> QgsRectangle:
    """`view` widened or heightened, about its centre, to `aspect`
    (width / height): the whole of it stays inside."""
    width, height = view.width(), view.height()
    if width / height < aspect:
        width = height * aspect
    else:
        height = width / aspect
    centre = view.center()
    return QgsRectangle(
        centre.x() - width / 2,
        centre.y() - height / 2,
        centre.x() + width / 2,
        centre.y() + height / 2,
    )


def _scaled(view: QgsRectangle, factor: float) -> QgsRectangle:
    scaled = QgsRectangle(view)
    scaled.scale(factor)
    return scaled


def locator_view(
    main_view: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    aspect: float,
    context_layers: Sequence[QgsMapLayer] | None = None,
) -> LocatorView:
    """The view of an inset for a main map showing `main_view` (in `crs`),
    in a box `aspect` wide per unit high. `context_layers` is the user's
    choice of what it draws; `None` takes the area-wide layers among
    `layers`."""
    chosen = tuple(
        default_context_layers(layers) if context_layers is None else context_layers
    )
    reach = QgsRectangle(main_view)
    for layer in chosen:
        extent = _extent_in(layer, crs, project)
        if extent is not None:
            reach.combineExtentWith(extent)
    ratio = max(reach.width() / main_view.width(), reach.height() / main_view.height())
    has_context = ratio >= MIN_CONTEXT_RATIO
    if has_context:
        view = _scaled(reach, 1 + 2 * _PADDING)
        if ratio * (1 + 2 * _PADDING) > MAX_ZOOM_OUT:
            # Far wider than is useful: the most the inset shows, round the
            # main view.
            view = _scaled(main_view, MAX_ZOOM_OUT)
    else:
        view = _scaled(main_view, NO_CONTEXT_ZOOM_OUT)
    return LocatorView(fit_to_aspect(view, aspect), chosen, has_context)


def draw_quietly(map_item: QgsLayoutItemMap) -> None:
    """Draw `map_item` without labels: an inset is a picture of where, not
    a second copy of the main map's labels. The labels are switched off in
    this map's own style override of each labelled layer, so the layer's
    style and every other map are untouched."""
    labelled = [
        layer
        for layer in map_item.layersToRender()
        if isinstance(layer, QgsVectorLayer) and layer.labelsEnabled()
    ]
    if not labelled:
        return
    own_layers(map_item)
    overrides = (
        dict(map_item.layerStyleOverrides()) if map_item.keepLayerStyles() else {}
    )
    for layer in labelled:
        overrides[layer.id()] = style_xml(map_item, layer).replace(
            'labelsEnabled="1"', 'labelsEnabled="0"', 1
        )
    store_overrides(map_item, overrides)


def mark_context(map_item: QgsLayoutItemMap, has_context: bool) -> None:
    map_item.setCustomProperty(
        CONTEXT_PROPERTY, CONTEXT_LAYERS if has_context else CONTEXT_NONE
    )


def has_wider_context(map_item: QgsLayoutItemMap) -> bool | None:
    """Whether an inset's view came from wider context; `None` for a map
    this module did not make an inset of."""
    value = map_item.customProperty(CONTEXT_PROPERTY)
    if value == CONTEXT_LAYERS:
        return True
    if value == CONTEXT_NONE:
        return False
    return None


# -- editing an existing inset (undoable) -------------------------------


def _main_map(locator: QgsLayoutItemMap) -> QgsLayoutItemMap | None:
    for overview in locator.overviews().asList():
        linked = overview.linkedMap()
        if linked is not None:
            return linked  # type: ignore[no-any-return]
    return None


def _undoable(locator: QgsLayoutItemMap, text: str, change: object) -> None:
    layout = locator.layout()
    stack = layout.undoStack() if layout is not None else None
    if stack is not None:
        stack.beginCommand(locator, text)
    try:
        change()  # type: ignore[operator]
    finally:
        if stack is not None:
            stack.endCommand()


def fit_locator_to_context(
    locator: QgsLayoutItemMap, layers: Sequence[QgsMapLayer] | None = None
) -> LocatorView | None:
    """Fit the inset `locator` to the wider context of `layers` (`None`: the
    layers it draws now), round the map it outlines. Undoable. Returns the
    view, or `None` when the item outlines no map."""
    main = _main_map(locator)
    layout = locator.layout()
    project = layout.project() if layout is not None else None
    if main is None or project is None:
        return None
    chosen = list(layers) if layers is not None else list(locator.layers())
    size = locator.rect()
    view = locator_view(
        main.extent(),
        main.crs(),
        chosen,
        project,
        size.width() / size.height(),
        context_layers=chosen,
    )

    def apply() -> None:
        locator.setCrs(main.crs())
        locator.setExtent(view.extent)
        mark_context(locator, view.has_context)
        locator.invalidateCache()

    _undoable(locator, "Fit inset to context", apply)
    return view


def set_locator_context_layers(
    locator: QgsLayoutItemMap, layers: Sequence[QgsMapLayer]
) -> None:
    """Choose the layers the inset draws. Undoable; the view is left as it
    is (`fit_locator_to_context` refits it)."""

    def apply() -> None:
        locator.setKeepLayerSet(True)
        locator.setFollowVisibilityPreset(False)
        locator.setLayers(list(layers))
        locator.invalidateCache()

    _undoable(locator, "Choose inset layers", apply)


def omit_locator(locator: QgsLayoutItemMap) -> None:
    """Take the inset off the page. Undoable."""
    layout = locator.layout()
    if layout is None:
        return
    stack = layout.undoStack()
    stack.beginMacro("Remove inset")
    try:
        layout.removeLayoutItem(locator)
    finally:
        stack.endMacro()
