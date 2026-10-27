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

When the legend does not fit as laid out, more columns are tried while each
stays wide enough to read; when even they do not hold it, layers are left out
from the end of the legend, whole, and the count is reported, as the templates
do for a dense legend.

Pure Python, exercised in the unit tier.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .cartograph_ir import CartographSpec, LegendRowSpec, LegendSpec
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


@dataclass(frozen=True)
class LegendFit:
    """What fitting one legend changed: its columns, and the layers and
    entries it left out (`omitted_layers` names them, `omitted_entries`
    counts a layer's row and each of its classes, as QGIS's own count does)."""

    columns: int
    omitted_layers: tuple[str, ...] = ()
    omitted_entries: int = 0


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
    labels = [
        (qgis[i] if i < len(qgis) and qgis[i] else runtime_label)
        for i, runtime_label in enumerate(runtime.labels)
        if runtime_label not in hidden and not (i < len(qgis) and qgis[i] is None)
    ]
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


def fit_legend_to_box(
    item: LegendSpec, layers: Sequence[ExportLayer]
) -> tuple[LegendSpec, LegendFit]:
    """`item` set to fit its box in the browser, and what that took.

    Unchanged (and `columns` as it was) when it already fits.
    """
    font_mm = (item.font_size * 25.4 / 72) if item.font_size else DEFAULT_FONT_MM
    padding = DEFAULT_PADDING_MM if item.padding is None else item.padding
    available = item.box.h - 2 * padding - SAFETY_MM
    start = item.columns or 1

    def widest_columns() -> int:
        usable = item.box.w - 2 * padding
        most = int((usable + COLUMN_GAP_MM) // (MIN_COLUMN_MM + COLUMN_GAP_MM))
        return max(start, min(start + MAX_EXTRA_COLUMNS, most))

    for columns in range(start, widest_columns() + 1):
        needed, _elements_used = _needed_mm(item, layers, columns, font_mm)
        if needed <= available:
            changed = item if columns == start else replace(item, columns=columns)
            return changed, LegendFit(columns)

    columns = widest_columns()
    _needed, elements = _needed_mm(item, layers, columns, font_mm)
    ordered = [e.layer_id for e in elements if e.layer_id is not None]
    layer_order = list(dict.fromkeys(ordered))
    names = {layer.layer_id: layer.name for layer in layers}
    entries_of = {
        layer_id: sum(e.entries for e in elements if e.layer_id == layer_id)
        for layer_id in layer_order
    }
    omitted: list[str] = []
    rows = list(item.rows)
    current = replace(item, columns=columns)
    # Never the last layer: a legend left empty says less than a clipped one.
    for layer_id in reversed(layer_order[1:]):
        omitted.append(layer_id)
        # The runtime reads the first row that names the layer alone, so the
        # layer's own row (it carries the order) is the one to hide.
        own = next(
            (
                position
                for position, row in enumerate(rows)
                if row.layer == layer_id and row.match is None
            ),
            None,
        )
        if own is None:
            rows.append(LegendRowSpec(layer=layer_id, hide=True))
        else:
            rows[own] = replace(rows[own], hide=True)
        current = replace(item, columns=columns, rows=tuple(rows))
        needed, _elements_used = _needed_mm(current, layers, columns, font_mm)
        if needed <= available:
            break
    return current, LegendFit(
        columns,
        tuple(names[layer_id] for layer_id in reversed(omitted)),
        sum(entries_of[layer_id] for layer_id in omitted),
    )


def fit_frame_legend_boxes(
    spec: CartographSpec,
    frame_id: str,
    layers: Sequence[ExportLayer],
    report: FidelityReportBuilder,
) -> CartographSpec:
    """`spec` with every derived legend of `frame_id` fitted to its box, and
    in `report` each legend that needed more columns or left layers out."""
    items = []
    for item in spec.items:
        if not (
            isinstance(item, LegendSpec) and item.derived and item.for_ == frame_id
        ):
            items.append(item)
            continue
        fitted, fit = fit_legend_to_box(item, layers)
        items.append(fitted)
        subject = f"Legend '{item.id.removeprefix('legend_')}'"
        if fit.omitted_layers:
            report.approximated(
                subject,
                f"The browser's legend is taller than QGIS's, and did not fit "
                f"its box even in {fit.columns} columns, so it leaves out "
                f"{fit.omitted_entries} "
                + ("entry" if fit.omitted_entries == 1 else "entries")
                + " ("
                + ", ".join(fit.omitted_layers)
                + "). Make the legend box taller on the layout to keep them.",
            )
        elif fit.columns != (item.columns or 1):
            report.approximated(
                subject,
                f"The browser's legend is taller than QGIS's, so it is set in "
                f"{fit.columns} columns instead of {item.columns or 1} to fit "
                "its box.",
            )
    return replace(spec, items=tuple(items))
