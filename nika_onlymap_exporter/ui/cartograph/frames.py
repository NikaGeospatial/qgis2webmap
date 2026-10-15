"""Reading a print layout's map items for the Frames tab.

Deliberately not `core/layout_reader.py` - that module reads a layout into the
full cartograph IR. This is the much smaller thing the Frames tab needs on its
own: the list of map items a layout has, each with its CRS and the Live/Static
default `core/layout_reader.default_mode_for_map` decides for it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsPrintLayout


@dataclass(frozen=True)
class FrameItem:
    """One map item on a layout, as the Frames tab needs it."""

    # The layout item's uuid: stable across a rename, which `id()` is not -
    # `id()` is the user-facing label and is exactly what gets edited.
    item_id: str
    name: str
    crs_authid: str
    default_mode: str = "static"


def layout_map_items(layout: QgsPrintLayout) -> list[FrameItem]:
    """Every `QgsLayoutItemMap` on `layout`, in the order the layout holds them."""
    from qgis.core import QgsLayoutItemMap

    from ...core.layout_reader import default_mode_for_map

    items: list[FrameItem] = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        crs = item.crs()
        authid = crs.authid() if crs is not None and crs.isValid() else ""
        name = item.id() or item.displayName()
        items.append(
            FrameItem(
                item_id=item.uuid(),
                name=name,
                crs_authid=authid,
                default_mode=default_mode_for_map(item),
            )
        )
    return items
