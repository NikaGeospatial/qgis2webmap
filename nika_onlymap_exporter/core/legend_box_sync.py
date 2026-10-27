"""Carry a fitted legend (`runtime_legend_fit`) into the QGIS layout.

The browser's legend is taller than QGIS's, so a cartograph's legend box is
fitted to it: made taller, set in more columns, or left without some layers.
The QGIS legend item has to change the same way, or the layout preview, its
PDF and PNG prints and the web page would show three different legends. This
mirrors a fit onto the item: its box, its columns, and the layers left out,
which are unlisted with `legend_edit` -- the mechanism the user's own legend
exclusions use -- so they can be brought back from the legend's properties.

Not an undo step. The fit is derived from the layout, redone after every
change, so an undo step of its own would be undone and redone against the
user's own: undoing an edit would bring the fit back at once, and the edit
could never be undone past. The user's own steps record the legend as it was
then, so undoing one restores the legend from before it, and the fit is
worked out again from that. A legend that sizes itself to its contents snaps
back to them at its next change, so a fitted legend stops doing that.

GUI thread only: it changes the layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from qgis.core import (
    QgsLayoutItemLegend,
    QgsLayoutMeasurement,
    QgsUnitTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QRectF

from . import legend_edit
from .cartograph_ir import element_id
from .layout_reader import layout_item_tokens
from .runtime_legend_fit import LegendBoxChange

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsPrintLayout, QgsProject


def _layout_units(layout: QgsPrintLayout, millimetres: float) -> float:
    return float(
        layout.convertToLayoutUnits(
            QgsLayoutMeasurement(millimetres, QgsUnitTypes.LayoutMillimeters)
        )
    )


def apply_legend_changes(
    layout: QgsPrintLayout,
    project: QgsProject,
    changes: Sequence[LegendBoxChange],
    qgis_layer_ids: Mapping[str, Mapping[str, str]],
) -> int:
    """Make each change in `layout`'s legend item. `qgis_layer_ids` gives, per
    frame id, the QGIS layer id behind each `<om-layer id>`. Returns how many
    legends were changed."""
    if not changes:
        return 0
    tokens = layout_item_tokens(layout)
    legends = {
        element_id("legend", item.uuid(), tokens): item
        for item in layout.items()
        if isinstance(item, QgsLayoutItemLegend)
    }
    done = 0
    for change in changes:
        legend = legends.get(change.legend_id)
        if legend is None or sip.isdeleted(legend):
            continue
        # First: an item that sizes itself would resize with every edit below.
        legend.setResizeToContents(False)
        ids = qgis_layer_ids.get(change.frame_id, {})
        for dom_id in change.omitted_layer_ids:
            layer_id = ids.get(dom_id)
            if layer_id is not None:
                legend_edit.set_layer_listed(None, legend, project, layer_id, False)
        legend_edit.set_columns(None, legend, change.columns)
        box = change.box
        legend.attemptSetSceneRect(
            QRectF(
                _layout_units(layout, box.x),
                _layout_units(layout, box.y),
                _layout_units(layout, box.w),
                _layout_units(layout, box.h),
            )
        )
        legend.invalidateCache()
        legend.refresh()
        done += 1
    return done
