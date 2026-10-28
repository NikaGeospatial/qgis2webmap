"""Live counts for a layout's atlas pages, calculated as each page is set up.

A counter counts what its map frame shows. In an atlas the frame an atlas
drives shows a different ground on every page, so one calculation before the
export is right for at most one page. QGIS moves the atlas to a feature, sets
the driven maps' extents and only then renders the page, and it announces the
feature (`QgsLayoutAtlas.featureChanged`) between the two; that is the moment
this module recounts, on the layout being exported. Nothing waits for a GUI
timer, and nothing here touches the user's own layout: the caller passes the
copy it exports.

The hook is a plain context manager around any QGIS atlas output (PDF, print).
It records what happened so the caller can refuse to hand over a file whose
numbers were not all updated.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from dataclasses import replace

from qgis.core import QgsFeature, QgsLayout, QgsLayoutItem, QgsLayoutItemMap

from ..ui.i18n import tr
from .live_count import (
    CountState,
    live_counts,
    read_spec,
    refresh_live_counts,
    write_spec,
)


def follows_atlas_page(layout: QgsLayout) -> bool:
    """Whether some counter follows a map the atlas moves from page to page,
    so its number can differ between pages."""
    for number in live_counts(layout):
        spec_map = number.customProperty("nika/live-count/map")
        followed = layout.itemByUuid(str(spec_map)) if spec_map else None
        if isinstance(followed, QgsLayoutItemMap) and followed.atlasDriven():
            return True
    return False


def _item_key(item: QgsLayoutItem) -> tuple[object, ...]:
    rect = item.rect()
    return (
        type(item).__name__,
        round(item.zValue(), 6),
        item.id(),
        round(item.pos().x(), 3),
        round(item.pos().y(), 3),
        round(rect.width(), 3),
        round(rect.height(), 3),
    )


def follow_maps_in_copy(source: QgsLayout, copy: QgsLayout) -> None:
    """Point the counters of `copy` (made by `QgsLayout.clone`, which gives
    every item a new uuid) at the copy's own maps, so they count what the
    copy shows. Call it before the copy's items are moved. A map is matched
    by kind, stacking order, id and place; one that cannot be matched
    uniquely is left alone, and its counter then reads "\N{EM DASH}" rather
    than counting the wrong map."""
    keyed: dict[tuple[object, ...], list[QgsLayoutItem]] = {}
    for item in copy.items():
        if isinstance(item, QgsLayoutItemMap):
            keyed.setdefault(_item_key(item), []).append(item)
    new_uuid: dict[str, str] = {}
    for item in source.items():
        if isinstance(item, QgsLayoutItemMap):
            twins = keyed.get(_item_key(item), [])
            if len(twins) == 1:
                new_uuid[item.uuid()] = twins[0].uuid()
    for number in live_counts(copy):
        spec = read_spec(number)
        if spec.map_uuid in new_uuid:
            write_spec(number, replace(spec, map_uuid=new_uuid[spec.map_uuid]))


class AtlasPageCounts:
    """While active, every atlas feature the layout's atlas moves to gets its
    counters calculated before that page is drawn."""

    def __init__(self, layout: QgsLayout) -> None:
        self._layout = layout
        self.active = bool(live_counts(layout))
        #: How many pages had their counters calculated.
        self.pages_counted = 0
        #: Counters (by id) that could not be calculated on some page.
        self.unavailable: set[str] = set()
        #: The first error a page's calculation raised, if any.
        self.error: str = ""
        self._connected = False

    def __enter__(self) -> AtlasPageCounts:
        if self.active:
            self._layout.atlas().featureChanged.connect(self._on_feature)
            self._connected = True
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._connected:
            with contextlib.suppress(TypeError, RuntimeError):
                self._layout.atlas().featureChanged.disconnect(self._on_feature)
            self._connected = False

    def _on_feature(self, feature: QgsFeature) -> None:
        # Also announced with no feature when the run ends: nothing to count.
        if not feature.isValid():
            return
        try:  # an exception inside a Qt slot would abort QGIS
            outcomes = refresh_live_counts(self._layout)
        except Exception as exc:
            self.error = self.error or str(exc)
            return
        self.pages_counted += 1
        for uuid, outcome in outcomes.items():
            if outcome.state in (CountState.UNAVAILABLE, CountState.INVALID):
                item = self._layout.itemByUuid(uuid)
                self.unavailable.add(item.id() if item is not None else uuid)

    def problem(self, expected_pages: int) -> str:
        """Why the numbers cannot be trusted, or "" when every page's were
        calculated."""
        if not self.active:
            return ""
        if self.error:
            return tr(
                "The Live counts could not be updated for each page: {error}"
            ).format(error=self.error)
        if self.pages_counted != expected_pages:
            return tr(
                "The Live counts were updated for {done} of {total} pages."
            ).format(done=self.pages_counted, total=expected_pages)
        return ""

    def warnings(self) -> tuple[str, ...]:
        if not self.unavailable:
            return ()
        return (tr("A Live count could not be calculated and reads n/a or a dash."),)
