"""Statistic callouts on a layout: add, change and remove them on any layout.

A callout is a key figure: a value shown large over a short line saying what
it counts. Each lives on the layout it is drawn on -- three ordinary layout
items (`rule-figure-N`, `figure-N`, `figure-N-label`) -- so changing one cannot
change any other layout, and it goes with the layout when the cartograph is
saved, exported or imported. The numbers are the user's own; nothing here
computes or supplies one. They are an ordinary Add item entry, placed by a click
or a drag on the page like the other items; no layout has to come from a
particular template, and adding or removing one moves nothing else.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qgis.core import (
    QgsLayout,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemShape,
    QgsLayoutSize,
    QgsProject,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor

from ...core.canvas_cartograph import FIGURE_VARIABLES, project_figure_rows
from ..i18n import tr
from .layout_items import item_edit, one_step

#: How long a callout's value and its line may be: longer would not fit its
#: block.
VALUE_MAX_LENGTH = 24
LABEL_MAX_LENGTH = 80

#: A callout's item ids: `figure-N` and `figure-N-label` as a template makes
#: them, `label_figure_N` and so on as an imported cartograph names them.
_ITEM_ID = re.compile(
    r"^(?:(?:rule|shape_rule|label)[-_])?figure[-_](\d+)(?:[-_]label)?$"
)
#: A callout's look, in millimetres and points: a short rule, the value large,
#: its line smaller under it.
_MIN_WIDTH_MM = 30.0
_RULE_MM = 0.8
_VALUE_MM = 12.0
_LINE_MM = 8.0
_VALUE_PT = 28.0
_LINE_PT = 11.0
_INK = "#1a1a1a"
_MUTED = "#555555"


@dataclass(frozen=True)
class Callout:
    """One callout as the page shows it."""

    index: int
    value: str
    label: str


def _layout_items(layout: QgsLayout) -> list[QgsLayoutItem]:
    """The layout's items, without the scene's other graphics (the pages)."""
    return [item for item in layout.items() if isinstance(item, QgsLayoutItem)]


def _is_line(item: QgsLayoutItem) -> bool:
    """Whether `item` is a callout's line (what it counts), not its value."""
    return item.id().endswith(("-label", "_label"))


def find_part(layout: QgsLayout, index: int, line: bool) -> QgsLayoutItemLabel | None:
    """Callout `index`'s value (or, with `line`, its line of words)."""
    for item in _layout_items(layout):
        if (
            isinstance(item, QgsLayoutItemLabel)
            and callout_index(item) == index
            and _is_line(item) == line
        ):
            return item
    return None


def callout_index(item: QgsLayoutItem) -> int | None:
    """Which callout `item` belongs to, from its id; `None` for any other
    item."""
    match = _ITEM_ID.match(item.id())
    return int(match.group(1)) if match else None


def layout_callouts(layout: QgsLayout) -> tuple[Callout, ...]:
    """The callouts drawn on `layout`, in order: a value with nothing in it
    is not a callout."""
    found: dict[int, list[str]] = {}
    for item in _layout_items(layout):
        index = callout_index(item)
        if index is None or not isinstance(item, QgsLayoutItemLabel):
            continue
        slot = found.setdefault(index, ["", ""])
        slot[1 if _is_line(item) else 0] = item.text().strip()
    return tuple(
        Callout(index, value, label)
        for index, (value, label) in sorted(found.items())
        if value
    )


def _next_index(layout: QgsLayout) -> int:
    """The lowest callout number no item of the layout uses."""
    used = {
        i for item in _layout_items(layout) if (i := callout_index(item)) is not None
    }
    return next(n for n in range(len(used) + 1) if n not in used)


def default_words() -> tuple[str, str]:
    """What a new callout says until the user types their own."""
    return "100", tr("What it counts")


def add_callout(layout: QgsLayout, rect: QRectF, value: str, label: str) -> bool:
    """Add a callout whose top-left corner and width come from `rect`
    (layout units), as one undo step. Returns whether one was added: not for a
    blank value."""
    value, label = value.strip(), label.strip()
    if not value:
        return False
    index = _next_index(layout)
    with one_step(layout, tr("Add callout")):
        scale = layout.convertToLayoutUnits(
            QgsLayoutSize(1, 0, QgsUnitTypes.LayoutMillimeters)
        ).width()
        width = max(rect.width(), _MIN_WIDTH_MM * scale)
        x, y = rect.x(), rect.y()
        rule = QgsLayoutItemShape(layout)
        rule.setShapeType(QgsLayoutItemShape.Rectangle)
        rule.setId(f"rule-figure-{index}")
        layout.addLayoutItem(rule)
        _set_rect(rule, QRectF(x, y, min(8 * scale, width), _RULE_MM * scale))
        symbol = rule.symbol().clone()
        symbol.setColor(QColor(_INK))
        symbol.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
        rule.setSymbol(symbol)
        y += (_RULE_MM + 1.5) * scale
        _label(
            layout,
            f"figure-{index}",
            value,
            QRectF(x, y, width, _VALUE_MM * scale),
            _VALUE_PT,
            True,
            _INK,
        )
        if label:
            y += _VALUE_MM * scale
            _label(
                layout,
                f"figure-{index}-label",
                label,
                QRectF(x, y, width, _LINE_MM * scale),
                _LINE_PT,
                False,
                _MUTED,
            )
    layout.refresh()
    return True


def _set_rect(item: QgsLayoutItem, rect: QRectF) -> None:
    item.attemptSetSceneRect(rect)


def _label(layout, item_id, text, rect, points, bold, colour) -> None:  # type: ignore[no-untyped-def]
    item = QgsLayoutItemLabel(layout)
    item.setId(item_id)
    item.setText(text)
    text_format = item.textFormat()
    text_format.setSize(points)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(colour))
    font = text_format.font()
    font.setBold(bold)
    text_format.setFont(font)
    item.setTextFormat(text_format)
    layout.addLayoutItem(item)
    _set_rect(item, rect)


def change_callout(layout: QgsLayout, index: int, value: str, label: str) -> bool:
    """Change callout `index`'s words, as one undo step. The lettering the
    user gave it stays; a line added to a callout that had none is made like
    the others. Returns whether anything changed."""
    value, label = value.strip(), label.strip()
    current = layout_callouts(layout)
    target = next((c for c in current if c.index == index), None)
    if target is None or not value or (value, label) == (target.value, target.label):
        return False
    return _set_words(layout, index, value, label)


def remove_callout(layout: QgsLayout, index: int) -> bool:
    """Take callout `index` off the page, as one undo step. Returns whether
    one was removed."""
    if not any(c.index == index for c in layout_callouts(layout)):
        return False
    return _remove_items(layout, index)


# -- the words ----------------------------------------------------------------


def _set_words(layout: QgsLayout, index: int, value: str, label: str) -> bool:
    changed = False
    with one_step(layout, tr("Change callout")):
        for line, text in ((False, value), (True, label)):
            item = find_part(layout, index, line)
            if item is None and line and text:
                value_item = find_part(layout, index, False)
                if value_item is not None:
                    below = value_item.sceneBoundingRect()
                    _label(
                        layout,
                        f"figure-{index}-label",
                        text,
                        QRectF(
                            below.x(),
                            below.bottom(),
                            below.width(),
                            below.height() * _LINE_MM / _VALUE_MM,
                        ),
                        _LINE_PT,
                        False,
                        _MUTED,
                    )
                    changed = True
                continue
            if item is None or item.text() == text:
                continue
            with item_edit(layout, item, tr("Change callout")):
                item.setText(text)
            item.update()
            changed = True
    layout.refresh()
    return changed


def _remove_items(layout: QgsLayout, index: int) -> bool:
    gone = [item for item in _layout_items(layout) if callout_index(item) == index]
    if not gone:
        return False
    with one_step(layout, tr("Remove callout")):
        for item in gone:
            layout.removeLayoutItem(item)
    layout.refresh()
    return True


# -- the project's older, shared figures --------------------------------------


def migrate_project_figures(project: QgsProject) -> None:
    """Older versions kept the key figures in the project's variables
    (`nika_figure_1` ...), shared by every layout. A layout made from them
    already drew them as its own items, so what it shows stays as it is; the
    variables that one of the project's layouts shows are dropped here, so
    that they cannot reach any other layout. Any not shown anywhere stay for
    the next Signal page to take (`canvas_cartograph.cartograph_from_view`)."""
    legacy = project_figure_rows(project)
    if not any(value for value, _label in legacy):
        return
    shown = {
        (callout.value, callout.label)
        for layout in project.layoutManager().layouts()
        for callout in layout_callouts(layout)
    }
    variables = dict(project.customVariables())
    changed = False
    for name, (value, label) in zip(FIGURE_VARIABLES, legacy, strict=True):
        if value and (value, label) in shown and name in variables:
            del variables[name]
            changed = True
    if changed:
        project.setCustomVariables(variables)
