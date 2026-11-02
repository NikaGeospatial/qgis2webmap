"""Fit a live frame's derived legend inside its QGIS box.

The runtime sets a derived legend in the box the layout gave it and clips what
runs past the bottom, mid-row. QGIS trims its own legend to the box
(`canvas_cartograph._fit_legend`), but the browser's is taller than QGIS's: it
adds an "other" row under each categorized layer and a line naming the
classified field above every layer's classes, and sets class text at a size of
its own. So the legend QGIS drew to fit can overrun in the browser.

The heights below are measured, not guessed: the runtime's legend was laid
out in Chromium (OnlyMap 0.13.1) at the default 2.6 mm row text and each
element measured -- a layer row, a classes block's heading and each of its
entries, the gaps CSS leaves between them. The browser balances the legend's
CSS columns itself, which is packed here the way it does: whole elements
(`break-inside: avoid`) into the shortest column height that holds them all.

When the legend does not fit as laid out, its box is made taller first (see
`legend_grow_room` for how far it may grow); when that is not enough, more
columns are tried while each stays wide enough to read; when even they do not
hold it, layers are left out from the end of the legend, whole, and the count
is reported, as the templates do for a dense legend.

Pure Python, exercised in the unit tier.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from .cartograph_ir import (
    Box,
    CartographItem,
    CartographSpec,
    GraticuleSpec,
    LegendRowSpec,
    LegendSpec,
)
from .export_ir import ExportLayer
from .fidelity_report import FidelityReportBuilder
from .manifest_builder import fill_expression
from .runtime_legend import (
    RUNTIME_MAX_CATEGORIES,
    RuntimeLegend,
    derive_runtime_legend,
    qgis_class_labels,
)

#: Row text size when the legend names none, and the measured line height
#: ratio the runtime sets its legend text in.
DEFAULT_FONT_MM = 2.6
LINE_HEIGHT = 1.35
#: Space around the legend's content when it names no `padding`.
DEFAULT_PADDING_MM = 2.0
COLUMN_GAP_MM = 3.0
#: Measured heights, in mm (see the module docstring).
SWATCH_MM = 3.2
ROW_MARGIN_MM = 0.6
CLASSES_HEADING_MM = 3.02
CLASS_ENTRY_MM = 4.03
CLASSES_AFTER_MM = 1.6
CLASS_TEXT_MM = 2.3
CLASS_LINE_MM = CLASS_TEXT_MM * LINE_HEIGHT
#: What a column of text holds per mm of its font size -- the average
#: character is this many ems wide in the wide sans the runtime falls back to.
CHARACTER_EMS = 0.58
#: A column narrower than this is not tried: its rows would wrap more than
#: the columns it saves.
MIN_COLUMN_MM = 34.0
MAX_EXTRA_COLUMNS = 1
#: The browser's balancing is not the ideal packing; keep a little room.
SAFETY_MM = 1.0


#: A grown box never reaches closer to the page's edge than this.
PAGE_MARGIN_MM = 5.0
#: A grown height is rounded up to this, so the height read back from the
#: layout is never a hair under what the model asked for (which would make the
#: next pass grow it again).
HEIGHT_STEP_MM = 0.1
#: Boxes closer than this overlap by nothing visible.
OVERLAP_TOLERANCE_MM = 0.01


@dataclass(frozen=True)
class GrowRoom:
    """How a legend box may grow: first away from the page edge it is anchored
    to (`anchor_bottom`: it keeps its bottom edge and grows upward), by at most
    `available_mm`, and only past that on the other side, by at most
    `other_mm`."""

    anchor_bottom: bool = False
    available_mm: float = 0.0
    other_mm: float = 0.0

    @property
    def total_mm(self) -> float:
        return self.available_mm + self.other_mm


@dataclass(frozen=True)
class LegendFit:
    """What fitting one legend changed: its columns, how much taller its box
    became (`grown_mm`), and the layers and entries it left out
    (`omitted_layers` names them, `omitted_layer_ids` gives their
    `<om-layer id>`s, `omitted_entries` counts a layer's row and each of its
    classes, as QGIS's own count does)."""

    columns: int
    omitted_layers: tuple[str, ...] = ()
    omitted_entries: int = 0
    omitted_layer_ids: tuple[str, ...] = ()
    grown_mm: float = 0.0


def _overlaps(first: Box, second: Box) -> bool:
    return (
        min(first.x + first.w, second.x + second.w) - max(first.x, second.x)
        > OVERLAP_TOLERANCE_MM
        and min(first.y + first.h, second.y + second.h) - max(first.y, second.y)
        > OVERLAP_TOLERANCE_MM
    )


def legend_grow_room(
    item: LegendSpec, others: Sequence[CartographItem], page_height: float
) -> GrowRoom:
    """How far `item`'s box may grow, and which way.

    - Direction: a legend whose centre is in the lower half of the page keeps
      its bottom edge and grows upward; any other keeps its top edge and
      grows downward. Only when that side is used up does it also grow on the
      other side. Its left edge and width stay.
    - It stops at the page margin (`PAGE_MARGIN_MM`), or where it stands if it
      is already past it.
    - It stops short of any visible item it does not overlap yet and would
      newly overlap (a title, text, a scale bar, another legend, an inset, a
      picture). It may grow over the map frame it legends, and over anything
      it already overlaps: it sits on those.
    - A rotated legend does not grow; its box is not what the page shows.
    """
    box = item.box
    anchor_bottom = box.y + box.h / 2 > page_height / 2
    if item.rotation:
        return GrowRoom(anchor_bottom)
    up_limit = PAGE_MARGIN_MM
    down_limit = page_height - PAGE_MARGIN_MM
    for other in others:
        other_box = getattr(other, "box", None)
        if (
            other_box is None
            or other is item
            or getattr(other, "hidden", False)
            or isinstance(other, GraticuleSpec)
            or getattr(other, "id", None) == item.for_
            or _overlaps(box, other_box)
        ):
            continue
        beside = (
            min(box.x + box.w, other_box.x + other_box.w) - max(box.x, other_box.x)
            > OVERLAP_TOLERANCE_MM
        )
        if not beside:
            continue
        if other_box.y + other_box.h <= box.y + OVERLAP_TOLERANCE_MM:
            up_limit = max(up_limit, other_box.y + other_box.h)
        elif other_box.y >= box.y + box.h - OVERLAP_TOLERANCE_MM:
            down_limit = min(down_limit, other_box.y)
    up = max(0.0, box.y - up_limit)
    down = max(0.0, down_limit - (box.y + box.h))
    if anchor_bottom:
        return GrowRoom(True, up, down)
    return GrowRoom(False, down, up)


def _grown(box: Box, height: float, room: GrowRoom) -> Box:
    """`box` made `height` tall: on the side it is anchored away from first,
    on the other side for what that cannot give."""
    extra = max(0.0, height - box.h)
    preferred = min(extra, room.available_mm)
    other = extra - preferred
    top = box.y - (preferred if room.anchor_bottom else other)
    return Box(box.x, top, box.w, height)


@dataclass(frozen=True)
class _Element:
    """One unbreakable piece of the legend: its height with the space after."""

    height: float
    layer_id: str | None = None
    #: What QGIS's own count calls entries: a layer's row, a class's row.
    entries: int = 0


def _lines(text: str, width_mm: float, font_mm: float) -> int:
    per_line = max(1, int(width_mm / (font_mm * CHARACTER_EMS)))
    return max(1, math.ceil(len(text) / per_line))


def _layer_entries(
    layer: ExportLayer, runtime: RuntimeLegend | None, hidden: set[str]
) -> list[str]:
    """The class rows the runtime draws for a layer, as the legend will word
    them -- the QGIS label of each class kept, the catch-all and a "+N more"
    row added as the runtime adds them."""
    if runtime is None or runtime.kind not in ("classes", "categories"):
        return []
    qgis = qgis_class_labels(layer)
    # A row QGIS hides (None) is filtered out, so every label is a string.
    labels = cast(
        "list[str]",
        [
            (qgis[i] if i < len(qgis) and qgis[i] else runtime_label)
            for i, runtime_label in enumerate(runtime.labels)
            if runtime_label not in hidden and not (i < len(qgis) and qgis[i] is None)
        ],
    )
    if runtime.kind == "categories":
        if len(labels) > RUNTIME_MAX_CATEGORIES:
            more = len(labels) - RUNTIME_MAX_CATEGORIES
            labels = [*labels[:RUNTIME_MAX_CATEGORIES], f"+{more} more"]
        labels.append("other")
    return labels


def _elements(
    item: LegendSpec,
    layers: Sequence[ExportLayer],
    column_mm: float,
    font_mm: float,
) -> list[_Element]:
    """The legend's elements in the runtime's order, each layer's row then its
    classes block."""
    hidden_layers = {
        row.layer for row in item.rows if row.match is None and row.hide and row.layer
    }
    row_for = {
        row.layer: row for row in item.rows if row.match is None and not row.hide
    }
    order = {row.layer: row.order for row in row_for.values() if row.order is not None}
    listed = [
        (position, layer)
        for position, layer in enumerate(layers)
        if layer.visible and layer.layer_id not in hidden_layers
    ]
    listed.sort(key=lambda pair: (order.get(pair[1].layer_id, math.inf), pair[0]))

    elements: list[_Element] = []
    row_text_mm = column_mm - SWATCH_MM - 1.5
    for _position, layer in listed:
        override = row_for.get(layer.layer_id)
        title = (override.label if override and override.label else None) or layer.name
        row_line_mm = max(SWATCH_MM, font_mm * LINE_HEIGHT)
        row_mm = row_line_mm * 1 + font_mm * LINE_HEIGHT * (
            _lines(title, row_text_mm, font_mm) - 1
        )
        elements.append(
            _Element(max(row_mm, SWATCH_MM) + ROW_MARGIN_MM, layer.layer_id, 1)
        )
        if layer.raster is not None or layer.tiles is not None:
            continue
        runtime = derive_runtime_legend(
            fill_expression(layer.renderer, layer.geometry_kind)
        )
        hidden = {
            row.match
            for row in item.rows
            if row.layer == layer.layer_id and row.hide and row.match
        }
        entries = _layer_entries(layer, runtime, hidden)
        if not entries:
            continue
        entry_text_mm = column_mm - 4.7 - 2.7 - 1.2 - SWATCH_MM
        block = CLASSES_HEADING_MM
        for label in entries:
            extra = _lines(label, entry_text_mm, CLASS_TEXT_MM) - 1
            block += CLASS_ENTRY_MM + extra * CLASS_LINE_MM
        classes = len(entries) - (1 if runtime and runtime.kind == "categories" else 0)
        elements.append(_Element(block + CLASSES_AFTER_MM, layer.layer_id, classes))
    return elements


def _balanced_height(elements: Sequence[_Element], columns: int) -> float:
    """The shortest column height that holds `elements` in `columns` columns,
    filled in order, none split -- what the browser's balancing aims for."""
    if not elements:
        return 0.0
    low = max(element.height for element in elements)
    high = sum(element.height for element in elements)
    for _step in range(40):
        middle = (low + high) / 2
        used, filled = 1, 0.0
        for element in elements:
            if filled + element.height > middle and filled > 0:
                used, filled = used + 1, 0.0
            filled += element.height
        if used <= columns:
            high = middle
        else:
            low = middle
    return high


def _title_mm(item: LegendSpec, font_mm: float) -> float:
    return font_mm * LINE_HEIGHT + 1.2 if item.title else 0.0


def _needed_mm(
    item: LegendSpec, layers: Sequence[ExportLayer], columns: int, font_mm: float
) -> tuple[float, list[_Element]]:
    padding = DEFAULT_PADDING_MM if item.padding is None else item.padding
    column_mm = (item.box.w - 2 * padding - COLUMN_GAP_MM * (columns - 1)) / columns
    elements = _elements(item, layers, column_mm, font_mm)
    # The title sits in the first column, ahead of the rows.
    packed = [_Element(_title_mm(item, font_mm))] if item.title else []
    return _balanced_height(packed + elements, columns), elements


def _with_layer_hidden(
    rows: Sequence[LegendRowSpec], layer_id: str
) -> tuple[LegendRowSpec, ...]:
    """`rows` with the layer's row hidden. The runtime reads the first row
    that names the layer alone, so the layer's own row (it carries the order)
    is the one to hide."""
    own = next(
        (
            position
            for position, row in enumerate(rows)
            if row.layer == layer_id and row.match is None
        ),
        None,
    )
    if own is None:
        return (*rows, LegendRowSpec(layer=layer_id, hide=True))
    return tuple(
        replace(row, hide=True) if position == own else row
        for position, row in enumerate(rows)
    )


def with_change_applied(item: LegendSpec, change: LegendBoxChange) -> LegendSpec:
    """`item` with `change` made: the box and columns it was fitted to and
    the layers left out."""
    rows = tuple(item.rows)
    for layer_id in change.omitted_layer_ids:
        rows = _with_layer_hidden(rows, layer_id)
    return replace(item, box=change.box, columns=change.columns, rows=rows)


def _needed_box_height(
    item: LegendSpec, layers: Sequence[ExportLayer], columns: int, font_mm: float
) -> float:
    """The box height the browser's legend needs in `columns` columns, rounded
    up to `HEIGHT_STEP_MM`."""
    padding = DEFAULT_PADDING_MM if item.padding is None else item.padding
    needed, _elements_used = _needed_mm(item, layers, columns, font_mm)
    total = needed + 2 * padding + SAFETY_MM
    return math.ceil(total / HEIGHT_STEP_MM - 1e-9) * HEIGHT_STEP_MM


def fit_legend_to_box(
    item: LegendSpec,
    layers: Sequence[ExportLayer],
    room: GrowRoom | None = None,
) -> tuple[LegendSpec, LegendFit]:
    """`item` set to fit its box in the browser, and what that took.

    In this order: the box is made taller, up to `room` (none when `room` is
    omitted), keeping the columns it has; then more columns are tried, the
    box made as tall as each needs; then layers are left out from the end,
    whole. A box is never made shorter. Unchanged (and `columns` as it was)
    when it already fits.
    """
    font_mm = (item.font_size * 25.4 / 72) if item.font_size else DEFAULT_FONT_MM
    padding = DEFAULT_PADDING_MM if item.padding is None else item.padding
    room = room or GrowRoom()
    start = item.columns or 1
    height = item.box.h
    reach = height + room.total_mm + OVERLAP_TOLERANCE_MM / 10

    def widest_columns() -> int:
        usable = item.box.w - 2 * padding
        most = int((usable + COLUMN_GAP_MM) // (MIN_COLUMN_MM + COLUMN_GAP_MM))
        return max(start, min(start + MAX_EXTRA_COLUMNS, most))

    def with_height(candidate: LegendSpec, new_height: float) -> LegendSpec:
        if new_height <= height + OVERLAP_TOLERANCE_MM / 10:
            return candidate
        return replace(candidate, box=_grown(item.box, new_height, room))

    for columns in range(start, widest_columns() + 1):
        needed_height = _needed_box_height(item, layers, columns, font_mm)
        if needed_height <= reach:
            changed = item if columns == start else replace(item, columns=columns)
            fitted = with_height(changed, needed_height)
            return fitted, LegendFit(columns, grown_mm=fitted.box.h - height)

    columns = widest_columns()
    # As tall as it may get; the layers left out below only have to fit this.
    tallest = with_height(replace(item, columns=columns), reach)
    _needed, elements = _needed_mm(tallest, layers, columns, font_mm)
    ordered = [e.layer_id for e in elements if e.layer_id is not None]
    layer_order = list(dict.fromkeys(ordered))
    names = {layer.layer_id: layer.name for layer in layers}
    entries_of = {
        layer_id: sum(e.entries for e in elements if e.layer_id == layer_id)
        for layer_id in layer_order
    }
    omitted: list[str] = []
    rows = list(item.rows)
    current = tallest
    # Never the last layer: a legend left empty says less than a clipped one.
    for layer_id in reversed(layer_order[1:]):
        omitted.append(layer_id)
        rows = list(_with_layer_hidden(rows, layer_id))
        current = replace(tallest, rows=tuple(rows))
        if _needed_box_height(current, layers, columns, font_mm) <= reach:
            break
    # Only as tall as what is left needs, and never shorter than it was.
    final_height = min(
        reach, max(height, _needed_box_height(current, layers, columns, font_mm))
    )
    current = replace(current, box=item.box)
    current = with_height(current, final_height)
    return current, LegendFit(
        columns,
        tuple(names[layer_id] for layer_id in reversed(omitted)),
        sum(entries_of[layer_id] for layer_id in omitted),
        tuple(reversed(omitted)),
        current.box.h - height,
    )


@dataclass(frozen=True)
class LegendBoxChange:
    """What fitting one legend changed, in the terms the layout is changed in:
    the box it now has (page mm), its columns, and the `<om-layer id>`s of
    the layers left out."""

    legend_id: str
    frame_id: str
    box: Box
    columns: int
    omitted_layer_ids: tuple[str, ...] = ()


def _describe_fit(
    item: LegendSpec,
    fit: LegendFit,
    matched_layout: bool,
    omitted_said_elsewhere: bool = False,
) -> str | None:
    """What happened to a legend, for Checks; `None` when there is nothing
    left to say (the entries it left out are reported by the caller)."""
    done = []
    if fit.grown_mm > 0:
        done.append(f"made {fit.grown_mm:.1f} mm taller")
    if fit.columns != (item.columns or 1):
        done.append(f"set in {fit.columns} columns instead of {item.columns or 1}")
    if fit.omitted_layers and not omitted_said_elsewhere:
        count = fit.omitted_entries
        done.append(
            f"left without {count} "
            + ("entry" if count == 1 else "entries")
            + " ("
            + ", ".join(fit.omitted_layers)
            + ")"
        )
    if not done:
        return None
    text = (
        "The web page's legend is taller than QGIS's, so this legend was "
        + (", ".join(done[:-1]) + " and " if len(done) > 1 else "")
        + done[-1]
        + " to fit it."
    )
    if fit.omitted_layers and not omitted_said_elsewhere:
        text += " Make room around the legend on the layout to keep them."
    if matched_layout:
        text += " The layout's legend was changed to match."
    return text


def fit_frame_legend_boxes(
    spec: CartographSpec,
    frame_id: str,
    layers: Sequence[ExportLayer],
    report: FidelityReportBuilder,
    *,
    grow_on_page_height: float | None = None,
    changes: list[LegendBoxChange] | None = None,
    template_legends: Collection[str] = (),
    subjects: Mapping[str, str] | None = None,
) -> CartographSpec:
    """`spec` with every derived legend of `frame_id` fitted to its box, and
    in `report` what each legend needed (see `_describe_fit`).

    With `grow_on_page_height` (the page's height in mm) a legend's box may
    grow (`legend_grow_room`); without it only columns and layers change, as
    nothing else would carry a taller box to the layout. `changes` collects
    what changed per legend, for mirroring into the layout. The legends in
    `template_legends` (ids) already have a Checks row saying what they leave
    out, which the caller rewrites; this adds no second one for it. `subjects`
    gives the Checks subject of a legend by id (the layout reader's, so a
    legend's rows sit together); without one it is named after its id."""
    items = list(spec.items)
    for index, item in enumerate(items):
        if not (
            isinstance(item, LegendSpec) and item.derived and item.for_ == frame_id
        ):
            continue
        room = (
            legend_grow_room(item, items, grow_on_page_height)
            if grow_on_page_height is not None
            else None
        )
        fitted, fit = fit_legend_to_box(item, layers, room)
        if fitted == item:
            continue
        items[index] = fitted
        if changes is not None:
            changes.append(
                LegendBoxChange(
                    item.id,
                    frame_id,
                    fitted.box,
                    fitted.columns or 1,
                    fit.omitted_layer_ids,
                )
            )
        detail = _describe_fit(
            item,
            fit,
            grow_on_page_height is not None,
            item.id in template_legends,
        )
        if detail is not None:
            report.approximated(
                (subjects or {}).get(item.id)
                or f"Legend '{item.id.removeprefix('legend_')}'",
                detail,
            )
    return replace(spec, items=tuple(items))
