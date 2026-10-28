"""Paging the layout through its QGIS atlas for the on-screen preview.

The Cartograph panel's page is the layout itself, so showing page N of a series
is QGIS's own atlas preview: seeking the atlas moves every atlas-driven map and
sets the feature that labels and `@atlas_*` variables read. Seeking leaves the
maps where the last page put them, and the layout is the user's document, so
this object remembers what each map showed before the first page and puts it
back on `release()` - without touching how the atlas drives the maps, which the
user may have edited in between.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.core import (
    QgsFeature,
    QgsLayoutItemMap,
    QgsPrintLayout,
    QgsVectorLayerUtils,
)
from qgis.PyQt import sip

from .layout_reader import _MapState


class AtlasPreview:
    """One layout's atlas preview. GUI thread only."""

    def __init__(self, layout: QgsPrintLayout) -> None:
        self._layout = layout
        self._maps: list[tuple[QgsLayoutItemMap, _MapState]] = []
        self._previewed = -1
        self._feature = QgsFeature()
        self._context_layer = None
        self.active = False
        # Whether any page was shown since `begin`: the maps were moved.
        self.moved = False

    def begin(self) -> None:
        """Remember what the layout shows now. Idempotent."""
        if self.active or sip.isdeleted(self._layout):
            return
        atlas = self._layout.atlas()
        context = self._layout.reportContext()
        self._previewed = atlas.currentFeatureNumber()
        self._feature = QgsFeature(context.feature())
        self._context_layer = context.layer()
        self._maps = [
            (item, _MapState.of(item))
            for item in self._layout.items()
            if isinstance(item, QgsLayoutItemMap)
        ]
        self.moved = False
        self.active = True

    def prepare(self) -> int:
        """Re-read which features have pages (filter, sort), without moving
        the maps. Returns how many."""
        atlas = self._layout.atlas()
        atlas.updateFeatures()
        return int(atlas.count())

    def go_to(self, index: int) -> bool:
        """Show page `index` (0-based) in the layout."""
        if not self.active or sip.isdeleted(self._layout):
            return False
        shown = bool(self._layout.atlas().seekTo(index))
        if shown:
            self.moved = True
        return shown

    def current_feature_id(self) -> int | None:
        feature = self._layout.reportContext().feature()
        return int(feature.id()) if feature.isValid() else None

    def find_page(self, feature_id: int, count: int, hint: int = 0) -> int | None:
        """The page now showing `feature_id`, or `None` when it has none.
        `hint` is tried first: most edits leave the page where it was."""
        order = [hint, *range(count)] if 0 <= hint < count else range(count)
        for index in order:
            if not self.go_to(index):
                continue
            if self.current_feature_id() == feature_id:
                return index
        return None

    def page_name(self, index: int) -> str:
        """The page's name from the atlas's page-name setting, else the
        coverage layer's display text; empty when neither says anything."""
        atlas = self._layout.atlas()
        name = str(atlas.nameForPage(index)).strip()
        if name:
            return name
        layer = atlas.coverageLayer()
        feature = self._layout.reportContext().feature()
        if layer is None or not feature.isValid():
            return ""
        text = str(QgsVectorLayerUtils.getFeatureDisplayString(layer, feature)).strip()
        # A bare feature id says nothing a page number does not.
        return "" if text == str(feature.id()) else text

    def release(self) -> None:
        """Put the layout back as `begin` found it."""
        if not self.active:
            return
        self.active = False
        layout = self._layout
        if sip.isdeleted(layout):
            return
        atlas = layout.atlas()
        context = layout.reportContext()
        # `seekTo` is what moves the atlas's page; -1 (no page yet) is
        # reached by asking for it, which QGIS answers with False.
        atlas.seekTo(self._previewed)
        context.setFeature(self._feature)
        context.setLayer(self._context_layer)
        # Last: seeking a page moves the atlas's maps again.
        for item, state in self._maps:
            if not sip.isdeleted(item):
                state.restore_view(item)
        self._maps = []
