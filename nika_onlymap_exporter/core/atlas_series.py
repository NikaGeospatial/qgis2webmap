"""What a map series (the layout's QGIS atlas) can do right now.

The Atlas tab asks this module, not the saved cartograph, whether a series is
set up and which outputs it can make. Readiness is per operation:

- the preview, the native QGIS PDF and printing through QGIS (`native_print`:
  the print dialog, or a PDF to print from) need only a usable source layer, a
  map to update and at least one page;
- the image ZIP, the HTML page and printing from the web page run through the
  cartograph, whose runtime turns pages with an Interactive map, so they also
  need the maps the series updates to be Interactive.

A Live count that follows a map the atlas moves is recalculated for every
page of the QGIS outputs (PDF, native print) but written once into the
cartograph, which has no way to change a number from page to page; those
outputs say so (`Operation.limitation`).

Nothing here edits the layout; the tab owns every edit (and its undo step).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from qgis.core import (
    Qgis,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsPrintLayout,
    QgsReadWriteContext,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..translation import plural, tr
from .atlas_live_counts import follows_atlas_page
from .layout_reader import atlas_page_count, expression_error

#: A frame's mode as the Map frames list stores it.
LIVE = "live"


class SeriesState(Enum):
    """Where setting up a series stands; the first unmet step wins."""

    OFF = "off"
    CHOOSE_LAYER = "choose_layer"
    LAYER_MISSING = "layer_missing"
    LAYER_UNUSABLE = "layer_unusable"
    NO_MAP = "no_map"
    CHOOSE_MAP = "choose_map"
    INVALID = "invalid"
    NO_MATCH = "no_match"
    READY = "ready"


@dataclass(frozen=True)
class MapRef:
    """One map of the layout, by uuid and the name people know it by."""

    item_id: str
    name: str


@dataclass(frozen=True)
class Operation:
    """Whether one output can be made now; when not, why and what fixes it."""

    ok: bool
    reason: str = ""
    # Maps that must be switched to Interactive for this output to work.
    needs_interactive: tuple[MapRef, ...] = ()
    # What this output cannot do right now even though it can be made.
    limitation: str = ""


@dataclass(frozen=True)
class SeriesStatus:
    state: SeriesState
    message: str = ""
    # Pages in QGIS's atlas (features), and sheets each page makes.
    pages: int = 0
    sheets_per_page: int = 1
    maps: tuple[MapRef, ...] = ()
    updated: tuple[MapRef, ...] = ()
    pdf: Operation = field(default_factory=lambda: Operation(False))
    images: Operation = field(default_factory=lambda: Operation(False))
    html: Operation = field(default_factory=lambda: Operation(False))
    # Printing through QGIS (the print dialog, or a PDF handed over to print).
    native_print: Operation = field(default_factory=lambda: Operation(False))
    # Printing from the exported web page.
    print: Operation = field(default_factory=lambda: Operation(False))

    @property
    def sheets(self) -> int:
        return self.pages * self.sheets_per_page


def friendly_map_name(name: str) -> str:
    """A map's layout id as a person would say it: `main` is "Main map",
    `inset_view` "Inset view map"; a name already written as words stays."""
    text = name.strip()
    if not text:
        return text
    if re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", text):
        words = text.replace("_", " ").replace("-", " ")
        text = words[0].upper() + words[1:]
        if not text.lower().endswith("map"):
            text += " map"
    return text


def layer_problem(layer: QgsMapLayer | None) -> str | None:
    """Why `layer` cannot give a page per feature, or `None` when it can."""
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return tr("This layer can't be read.")
    if layer.geometryType() not in (
        Qgis.GeometryType.Point,
        Qgis.GeometryType.Line,
        Qgis.GeometryType.Polygon,
    ):
        return tr("This layer has no locations to show on a map.")
    if layer.featureCount() == 0:
        return tr("This layer has no features, so there are no pages.")
    return None


def feature_summary(layer: QgsVectorLayer) -> str:
    """ "3 areas" / "12 locations" / "4 lines": what the layer will turn into
    pages, in words that fit its geometry."""
    count = int(layer.featureCount())
    kind = layer.geometryType()
    if kind == Qgis.GeometryType.Polygon:
        return plural(count, "{n} area", "{n} areas")
    if kind == Qgis.GeometryType.Point:
        return plural(count, "{n} location", "{n} locations")
    if kind == Qgis.GeometryType.Line:
        return plural(count, "{n} line", "{n} lines")
    return plural(count, "{n} feature", "{n} features")


def stored_coverage_id(layout: QgsPrintLayout) -> str:
    """The coverage layer id the atlas still holds, even when that layer is
    gone from the project (QGIS then answers `None` for the layer itself)."""
    document = QDomDocument()
    root = document.createElement("atlas")
    document.appendChild(root)
    layout.atlas().writeXml(root, document, QgsReadWriteContext())
    return str(root.firstChildElement("Atlas").attribute("coverageLayer"))


def driven_maps(layout: QgsPrintLayout) -> list[QgsLayoutItemMap]:
    """The maps the atlas moves from page to page."""
    return [
        item
        for item in layout.items()
        if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()
    ]


def map_ref(item: QgsLayoutItemMap) -> MapRef:
    return MapRef(item.uuid(), friendly_map_name(item.id() or item.displayName()))


def layout_maps(layout: QgsPrintLayout) -> list[MapRef]:
    return [
        map_ref(item) for item in layout.items() if isinstance(item, QgsLayoutItemMap)
    ]


def series_status(
    layout: QgsPrintLayout | None,
    frame_mode: Callable[[str], str],
    *,
    layer_was_removed: bool = False,
) -> SeriesStatus:
    """The state of `layout`'s series and what each output needs.

    `frame_mode(item_id)` is the map's Interactive/Image choice. Counting
    pages never moves the atlas (`atlas_page_count`).
    """
    if layout is None or not (layout.atlas().enabled() or layer_was_removed):
        return SeriesStatus(SeriesState.OFF)
    atlas = layout.atlas()
    maps = tuple(layout_maps(layout))
    updated = tuple(map_ref(item) for item in driven_maps(layout))
    sheets = max(1, int(layout.pageCollection().pageCount()))
    coverage = atlas.coverageLayer()

    def blocked(state: SeriesState, message: str) -> SeriesStatus:
        none = Operation(False, message)
        return SeriesStatus(
            state,
            message,
            sheets_per_page=sheets,
            maps=maps,
            updated=updated,
            pdf=none,
            images=none,
            html=none,
            native_print=none,
            print=none,
        )

    if coverage is None:
        if layer_was_removed or stored_coverage_id(layout):
            return blocked(
                SeriesState.LAYER_MISSING,
                tr("The selected layer is no longer available."),
            )
        return blocked(SeriesState.CHOOSE_LAYER, tr("Choose a layer to create pages."))
    problem = layer_problem(coverage)
    if problem is not None:
        return blocked(SeriesState.LAYER_UNUSABLE, problem)
    if not maps:
        return blocked(
            SeriesState.NO_MAP,
            tr("This layout needs a map to update on each page."),
        )
    if not updated:
        return blocked(SeriesState.CHOOSE_MAP, tr("Choose which map to update."))
    for text, active in (
        (atlas.sortExpression(), atlas.sortFeatures()),
        (atlas.filterExpression(), atlas.filterFeatures()),
    ):
        if active and text.strip() and expression_error(text, coverage) is not None:
            return blocked(
                SeriesState.INVALID,
                tr("Couldn't prepare the series. Check the highlighted setting."),
            )
    pages = atlas_page_count(layout)
    if pages is None:
        return blocked(
            SeriesState.INVALID,
            tr("Couldn't prepare the series. Check the highlighted setting."),
        )
    if pages == 0:
        return blocked(SeriesState.NO_MATCH, tr("No features match your filter."))

    static = tuple(ref for ref in updated if frame_mode(ref.item_id) != LIVE)
    cartograph_output = _cartograph_operation(
        static, _counts_limitation() if pages > 1 and follows_atlas_page(layout) else ""
    )
    return SeriesStatus(
        SeriesState.READY,
        pages=pages,
        sheets_per_page=sheets,
        maps=maps,
        updated=updated,
        pdf=Operation(True),
        images=cartograph_output,
        html=cartograph_output,
        native_print=Operation(True),
        print=cartograph_output,
    )


def _counts_limitation() -> str:
    return tr(
        "A Live count follows the map that changes on each page. This output "
        "shows the same number on every page; the PDF and Print series give "
        "each page its own."
    )


def _cartograph_operation(
    static: tuple[MapRef, ...], limitation: str = ""
) -> Operation:
    if not static:
        return Operation(True, limitation=limitation)
    names = ", ".join(f"“{ref.name}”" for ref in static)
    return Operation(
        False,
        tr("Needs the map {names} to be Interactive.").format(names=names),
        needs_interactive=static,
        limitation=limitation,
    )
