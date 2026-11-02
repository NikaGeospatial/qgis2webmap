"""What reading a print layout produces, shared by `core/layout_reader.py`
and the parts of the reading split out of it (`layout_contrast`,
`atlas_reader`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qgis.core import QgsLayoutItem, QgsLayoutItemMap

from .cartograph_ir import AtlasSpec, Box, CartographItem

FrameMode = Literal["live", "static"]


@dataclass(frozen=True)
class MapFrameReading:
    """A `QgsLayoutItemMap`, with its placement and mode already decided.

    `map_item` is the live PyQGIS object, not a serialised copy: both
    `exporters/frame_raster.py` (static) and `core/project_reader.py` /
    `writers/onlymap_writer.py` (live, via `layersToRender()`) need the real
    object, and copying its state into a pure dataclass here would just be a
    second, driftable representation of the same thing.
    """

    frame_id: str
    map_item: QgsLayoutItemMap
    box: Box
    rotation: float
    mode: FrameMode
    z: int | None
    locked: bool
    hidden: bool
    opacity: float
    overview_of: str | None
    # Set on a live frame the atlas refits (`<om-frame atlas-fit atlas-margin>`).
    atlas_fit: str | None = None
    atlas_margin: float | None = None
    # The atlas moves this frame as QGIS's own atlas does, page by page
    # (`atlas_fit` is then "fixed"): fixed and predefined scales, and points.
    atlas_cameras_from_qgis: bool = False
    # Live frames only: `(QGIS layer id, <om-layer id>)` for every layer the
    # frame draws -- see `cartograph_ir.live_layer_dom_ids` for why the two
    # can differ -- and the layers to export hidden (the atlas coverage layer
    # under "Hide coverage layer"). Both are empty for a static frame.
    layer_dom_ids: tuple[tuple[str, str], ...] = ()
    hidden_layer_ids: tuple[str, ...] = ()
    # `<om-frame frame-bg>`: the map item's background colour, when it is one
    # QGIS does not give every map by default (see `_map_background`).
    frame_bg: str | None = None
    # `<om-frame frame-stroke>`: the map item's frame colour, when it draws
    # one QGIS's default does not (see `_map_frame_stroke`).
    frame_stroke: str | None = None


@dataclass(frozen=True)
class AtlasRequirement:
    """What the written atlas relies on in the *exported* coverage layer,
    which only `finish_cartograph` can check -- it is the one that reads the
    layer: the layer must reach `frame_id`'s `<om-map>` at all, and `fields`
    (named by the filter, sort, filename, page name and `{{atlas.*}}`
    labels) must survive the export's field pruning."""

    frame_id: str
    layer_id: str
    layer_name: str
    fields: tuple[str, ...] = ()
    # The coverage layer's feature ids in the order QGIS pages them, when the
    # atlas sorts: `finish_cartograph` writes each feature's place into it.
    page_order: tuple[int, ...] = ()
    # The coverage layer's feature ids that get a page, in page order: what
    # the written atlas will page, whatever the atlas sorts or filters by.
    page_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class LayoutReading:
    """Everything `core/cartograph_export.py` needs to assemble a
    `CartographSpec`, short of the two things that need a render:
    `frames` still need their raster or their `<om-map>` markup."""

    page_width: float
    page_height: float
    orientation: str
    frames: tuple[MapFrameReading, ...]
    furniture: tuple[CartographItem, ...]
    atlas: AtlasSpec | None = None
    atlas_requirement: AtlasRequirement | None = None
    # `<om-cartograph bg>`: page 1's background colour, when it is not QGIS's
    # default white (see `_page_background`).
    bg: str | None = None


def _name(item: QgsLayoutItem) -> str:
    """An item's name as the layout designer's Items panel shows it, quoted
    the way every other fidelity subject quotes a layer name."""
    return f"'{item.displayName()}'"
