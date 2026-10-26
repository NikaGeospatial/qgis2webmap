"""The text size of the layout item selected in the page preview.

Resizing a text box changes its box, not its lettering; with the template's
text-size sliders gone, this is how a title, a note or the legend is made
bigger or smaller. A change is one undo command on the layout's own stack - a
rebuild from the template would take every move and edit made on the page.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.core import (
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLegendStyle,
    QgsUnitTypes,
)

from ...core.layout_reader import _POINTS_PER_UNIT, chosen_style_property
from ..i18n import tr

#: What one press of the smaller / bigger button changes, in points.
STEP_POINTS = 1.0
MIN_POINTS = 4.0
MAX_POINTS = 120.0

_LEGEND_TEXT_STYLES = (
    QgsLegendStyle.Title,
    QgsLegendStyle.Group,
    QgsLegendStyle.Subgroup,
    QgsLegendStyle.SymbolLabel,
)


def text_size_points(item: QgsLayoutItem) -> float | None:
    """The size of the item's text in points - a legend's rows, a label's
    lettering - or None for an item that has no text size to change."""
    if isinstance(item, QgsLayoutItemLabel):
        text_format = item.textFormat()
    elif isinstance(item, QgsLayoutItemLegend):
        text_format = item.style(QgsLegendStyle.SymbolLabel).textFormat()
    else:
        return None
    per_unit = _POINTS_PER_UNIT.get(int(text_format.sizeUnit()))
    if per_unit is None or text_format.size() <= 0:
        return None
    return text_format.size() * per_unit


def step_text_size(layout: QgsLayout, item: QgsLayoutItem, bigger: bool) -> bool:
    """One step bigger or smaller, as one undoable edit. Returns whether
    anything changed (at the limit, or for an item with no text size, not)."""
    current = text_size_points(item)
    if current is None:
        return False
    target = min(
        max(round(current + (STEP_POINTS if bigger else -STEP_POINTS)), MIN_POINTS),
        MAX_POINTS,
    )
    if abs(target - current) < 0.01:
        return False
    stack = layout.undoStack()
    stack.beginCommand(item, tr("Change text size"))
    try:
        if isinstance(item, QgsLayoutItemLabel):
            text_format = item.textFormat()
            text_format.setSize(target)
            text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
            item.setTextFormat(text_format)
        elif isinstance(item, QgsLayoutItemLegend):
            _set_legend_size(item, target / current, target)
    finally:
        stack.endCommand()
    return True


def _set_legend_size(legend: QgsLayoutItemLegend, factor: float, rows: float) -> None:
    # Every level of the legend scales with its rows, keeping QGIS's own
    # proportions between headings and rows.
    for style in _LEGEND_TEXT_STYLES:
        legend_style = legend.rstyle(style)
        text_format = legend_style.textFormat()
        text_format.setSize(text_format.size() * factor)
        legend_style.setTextFormat(text_format)
    # What the HTML reads the legend's size from (see `legend_spec`).
    legend.setCustomProperty(chosen_style_property("font-size"), rows)
    legend.adjustBoxSize()
    legend.refresh()
