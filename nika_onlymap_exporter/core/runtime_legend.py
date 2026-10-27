"""Make a live frame's derived legend read like the QGIS one.

The OnlyMap runtime never stores a cartograph legend: it derives the rows
from the frame's own `<om-layer>`s at render time, and the reader only
writes `<om-legend-row>` overrides on top. What it derives is not what QGIS
draws, in ways an override can correct:

* **Class labels.** The runtime labels each class from the `get-fill-color`
  expression itself -- a category by its *value*, a graduated class by its
  breaks ("< 1", "1 - 2", "≥ 4", with an en dash) -- where QGIS shows the
  label the user gave it ("0-1 m"). A `match` + `label` row puts the QGIS
  label back; the match key has to be the runtime's own label, so it is
  computed here exactly as the runtime computes it.
* **The swatch's shape.** A `GeoJsonLayer` with both a fill and a line
  colour gets a square, so roads and rivers sat behind squares. A layer
  row's `shape` puts a line or a point there.
* **A labelled layer's labels.** They are a layer of their own in the
  runtime's map and would be listed as "<name> (labels)"; a hidden row for
  each takes them off.
* **A classed layer's heading.** QGIS heads a layer's classes with its name
  alone; the runtime puts a swatch of the first class's colour beside it.
  A transparent `line` swatch is the runtime's nearest to none.

What no override reaches -- squares for every class, the field name above
the classes, an "other" row under categories, the class text's fixed size,
CSS column breaks -- is said in Checks by `describe_runtime_legend_changes`.

Pure Python: it mirrors the runtime's `deriveLegendSpec` (OnlyMap 0.13.1,
`legend-spec.ts`) over the expression `manifest_builder` writes, so it is
exercised in the unit tier.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .cartograph_ir import CartographSpec, LegendRowSpec, LegendSpec
from .export_ir import ExportLayer, GeometryKind, RendererKind
from .manifest_builder import fill_expression

#: A swatch the reader cannot see: the colour a classed layer's heading row
#: gets so it reads as a heading, as in QGIS.
HEADING_SWATCH_COLOR = "#00000000"

# The runtime's own patterns (legend-spec.ts), ASCII as JavaScript's \w and \d.
_NUMBER = r"-?\d+(?:\.\d+)?(?:e-?\d+)?"
_QUOTED = r"'(?:[^'\\]|\\.)*'"
_SCALE = re.compile(
    r"^scale\(\s*(?:Math\.log\()?\$([A-Za-z_]\w*)\)?\s*,\s*"
    r"(sequential|diverging|threshold)\s*,\s*\[([^\]]*)\]\s*,\s*"
    r"domain=\[([^\]]*)\]\s*\)$",
    re.ASCII,
)
_TERNARY_HEAD = re.compile(r"^\$([A-Za-z_]\w*)\s*==\s*", re.ASCII)


@dataclass(frozen=True)
class RuntimeLegend:
    """What the runtime derives from one `get-fill-color`: its kind
    (`classes`, `categories` or `ramp`) and, for the first two, each entry's
    label in the order the expression lists them."""

    kind: str
    labels: tuple[str, ...] = ()


def _unquote(text: str) -> str | None:
    text = text.strip()
    if not re.fullmatch(_QUOTED, text, re.ASCII):
        return None
    return text[1:-1].replace("\\'", "'").replace("\\\\", "\\")


def _js_number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def format_class_bound(value: float) -> str:
    """A class break as the runtime prints it: JavaScript's
    `toLocaleString("en-US", {maximumFractionDigits: 2})` -- thousands
    separated, at most two decimals, halves rounded away from zero."""
    try:
        rounded = Decimal(repr(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    except InvalidOperation:
        return str(value)
    text = f"{rounded:,f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def derive_runtime_legend(expression: str | None) -> RuntimeLegend | None:
    """The legend the runtime derives from `expression`, `None` when it
    derives none and draws the layer as a single swatch."""
    if not expression:
        return None
    source = expression.strip()
    scale = _SCALE.match(source)
    if scale is not None:
        _field, kind, colors_text, domain_text = scale.groups()
        colors = [_unquote(part) for part in colors_text.split(",") if part.strip()]
        domain = [_js_number(part) for part in domain_text.split(",") if part.strip()]
        if any(color is None for color in colors) or any(
            bound is None for bound in domain
        ):
            return None
        if len(colors) < 2:
            return None
        bounds = [bound for bound in domain if bound is not None]
        if kind == "threshold":
            if len(bounds) != len(colors) - 1:
                return None
            labels = []
            for index in range(len(colors)):
                if index == 0:
                    labels.append(f"< {format_class_bound(bounds[0])}")
                elif index == len(colors) - 1:
                    labels.append(f"≥ {format_class_bound(bounds[index - 1])}")
                else:
                    labels.append(
                        f"{format_class_bound(bounds[index - 1])} \u2013 "
                        f"{format_class_bound(bounds[index])}"
                    )
            return RuntimeLegend("classes", tuple(labels))
        return None if len(bounds) < 2 else RuntimeLegend("ramp")

    head = _TERNARY_HEAD.match(source)
    if head is None:
        return None
    clause = re.compile(
        rf"^\${head.group(1)}\s*==\s*({_QUOTED}|{_NUMBER})\s*\?\s*({_QUOTED})\s*:\s*",
        re.ASCII,
    )
    labels = []
    rest = source
    while (match := clause.match(rest)) is not None:
        value, color = match.groups()
        if _unquote(color) is None:
            return None
        unquoted = _unquote(value)
        labels.append(value if unquoted is None else unquoted)
        rest = rest[match.end() :]
    if not labels or _unquote(rest) is None:
        return None
    return RuntimeLegend("categories", tuple(labels))


def qgis_class_labels(layer: ExportLayer) -> tuple[str | None, ...]:
    """One label per row the runtime derives, in its order.

    The runtime lists a category once per value and has no row for the
    catch-all, which is the expression's fallback. A grouped class's values
    after the first are `None`: `fit_legend_rows` hides those rows, so the group
    reads as the one row QGIS shows.
    """
    renderer = layer.renderer
    if renderer.kind is RendererKind.CATEGORIZED:
        labels: list[str | None] = []
        for category in renderer.categories:
            labels.extend(
                category.label if position == 0 else None
                for position in range(len(category.explicit_values))
            )
        return tuple(labels)
    if renderer.kind is RendererKind.GRADUATED:
        return tuple(klass.label for klass in renderer.classes)
    return ()


def _layer_swatch(
    layer: ExportLayer, runtime: RuntimeLegend | None
) -> tuple[str | None, str | None]:
    """The `color` and `shape` a layer's own row needs, `None` for each the
    runtime already gets right."""
    if runtime is not None and runtime.kind in ("classes", "categories"):
        return HEADING_SWATCH_COLOR, "line"
    if layer.geometry_kind is GeometryKind.LINE:
        return None, "line"
    if layer.geometry_kind is GeometryKind.POINT:
        return None, "point"
    return None, None


def fit_legend_rows(
    rows: Sequence[LegendRowSpec], layers: Sequence[ExportLayer]
) -> tuple[LegendRowSpec, ...]:
    """`rows` -- the reader's overrides, class rows keyed on QGIS labels --
    in the runtime's vocabulary for the frame drawing `layers` (each under
    its `<om-layer id>`), plus the rows that make it read like QGIS."""
    fitted = list(rows)
    for layer in layers:
        if layer.raster is not None or layer.tiles is not None:
            continue
        if layer.labeling.enabled and layer.labeling.field_name:
            # The layer's labels are a layer of their own in the map, which
            # the runtime would list as "<name> (labels)". QGIS lists none.
            fitted.append(LegendRowSpec(layer=f"{layer.layer_id}-labels", hide=True))
        runtime = derive_runtime_legend(
            fill_expression(layer.renderer, layer.geometry_kind)
        )
        color, shape = _layer_swatch(layer, runtime)
        if color is not None or shape is not None:
            fitted = [
                replace(
                    row,
                    color=row.color or color,
                    shape=row.shape or shape,
                )
                if row.layer == layer.layer_id and row.match is None and not row.hide
                else row
                for row in fitted
            ]

        qgis_labels = qgis_class_labels(layer)
        if runtime is None or len(runtime.labels) != len(qgis_labels):
            continue
        runtime_by_qgis = {
            label: runtime_label
            for label, runtime_label in zip(qgis_labels, runtime.labels)
            if label is not None
        }
        targeted: set[str] = set()
        for index, row in enumerate(fitted):
            if row.layer != layer.layer_id or row.match is None:
                continue
            match = runtime_by_qgis.get(row.match, row.match)
            targeted.add(match)
            fitted[index] = replace(row, match=match)
        for qgis_label, runtime_label in zip(qgis_labels, runtime.labels):
            if qgis_label is None:
                # The rest of a grouped class: its first value's row speaks for it.
                fitted.append(
                    LegendRowSpec(layer=layer.layer_id, match=runtime_label, hide=True)
                )
            elif (
                qgis_label
                and qgis_label != runtime_label
                and runtime_label not in targeted
            ):
                fitted.append(
                    LegendRowSpec(
                        layer=layer.layer_id, match=runtime_label, label=qgis_label
                    )
                )
                targeted.add(runtime_label)
    return tuple(fitted)


def fit_frame_legends(
    spec: CartographSpec, frame_id: str, layers: Sequence[ExportLayer]
) -> CartographSpec:
    """`spec` with every derived legend of `frame_id` fitted to `layers`."""
    items = tuple(
        replace(item, rows=fit_legend_rows(item.rows, layers))
        if isinstance(item, LegendSpec) and item.derived and item.for_ == frame_id
        else item
        for item in spec.items
    )
    return replace(spec, items=items)


#: The most categories the runtime lists before a "+N more" row
#: (`MAX_CATEGORY_ENTRIES`).
RUNTIME_MAX_CATEGORIES = 12


@dataclass(frozen=True)
class ClassedLayer:
    """A legend layer drawn with classes, as `describe_runtime_legend_changes`
    needs it: its geometry (`point`, `line` or `polygon`), whether it is
    categorized (rather than graduated) and how many classes it has."""

    geometry: str
    categorized: bool
    class_count: int


def describe_runtime_legend_changes(
    classed: Sequence[ClassedLayer], columns: int
) -> list[str]:
    """Phrases for Checks: what the browser's legend still draws differently
    from QGIS, for a legend whose classed layers are `classed`, laid out in
    `columns` columns. Empty when nothing visibly differs."""
    changes = []
    if any(layer.geometry != "polygon" for layer in classed):
        changes.append("draws every class as a square, whatever its symbol")
    if classed:
        changes.append(
            "names the classified field above each layer's classes and sets "
            "class text at its own size"
        )
    if any(layer.categorized for layer in classed):
        changes.append("adds an 'other' row under each categorized layer")
    if any(
        layer.categorized and layer.class_count > RUNTIME_MAX_CATEGORIES
        for layer in classed
    ):
        changes.append(
            f"lists only the first {RUNTIME_MAX_CATEGORIES} categories of a "
            "layer, then a '+N more' row"
        )
    if columns > 1:
        changes.append(
            "balances its columns itself, so rows can fall in another column "
            "than in QGIS"
        )
    return changes
