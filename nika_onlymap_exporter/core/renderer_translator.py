"""QGIS renderers to `RendererSpec`.

Imports PyQGIS, so it is exercised in `tests/qgis/` rather than CI's unit tier.

**What this deliberately is not.** qgis2web carries ~7,500 lines across two
parallel families of renderer-specific JavaScript-string generators, and the two
have measurably diverged (a stroke width of 0.988 survives one path and rounds to
1.0 in the other). This module produces *data*, never markup and never code. The
writer turns that data into attributes; the translator never knows what an
attribute is.

**Flattening is lossy and must be reported.** A QGIS symbol is a tree of symbol
layers with blend modes, offsets, and data-defined overrides. A web renderer gets
one fill, one stroke, one width. So we take the bottom symbol layer, and record a
fidelity item whenever there was more than one — silently flattening is the
failure mode this project exists to avoid.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
)

from .export_ir import (
    MM_TO_PIXELS,
    CategorySpec,
    ClassificationMethod,
    Color,
    GraduatedClassSpec,
    RendererKind,
    RendererSpec,
    SymbolSpec,
)
from .fidelity_report import FidelityReportBuilder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsFeatureRenderer, QgsVectorLayer

# QGIS 3.26+ exposes classification as a pluggable object with a string id;
# `mode()` and its enum are deprecated and warn on QGIS 4. The ids come from the
# built-in `QgsClassification*` classes and are stable across versions.
_CLASSIFICATION_BY_ID = {
    "EqualInterval": ClassificationMethod.EQUAL_INTERVAL,
    "Quantile": ClassificationMethod.QUANTILE,
    "Jenks": ClassificationMethod.NATURAL_BREAKS,
    "StdDev": ClassificationMethod.STANDARD_DEVIATION,
    "Pretty": ClassificationMethod.PRETTY_BREAKS,
}

# Fallback for QGIS 3.22-3.24, where `classificationMethod()` does not exist.
_CLASSIFICATION_BY_MODE = {
    QgsGraduatedSymbolRenderer.EqualInterval: ClassificationMethod.EQUAL_INTERVAL,
    QgsGraduatedSymbolRenderer.Quantile: ClassificationMethod.QUANTILE,
    QgsGraduatedSymbolRenderer.Jenks: ClassificationMethod.NATURAL_BREAKS,
    QgsGraduatedSymbolRenderer.StdDev: ClassificationMethod.STANDARD_DEVIATION,
    QgsGraduatedSymbolRenderer.Pretty: ClassificationMethod.PRETTY_BREAKS,
}


def classification_of(renderer: QgsGraduatedSymbolRenderer) -> ClassificationMethod:
    """Read the classification method across the QGIS versions we support.

    Prefers the modern object API; falls back to the deprecated enum on older
    QGIS. A method we do not recognise is `UNKNOWN` rather than an error - the
    class breaks are exported exactly as QGIS computed them either way, so an
    unrecognised method costs a legend caption, not correctness.
    """
    method = getattr(renderer, "classificationMethod", None)
    if method is not None:
        try:
            method_id = method().id()
        except (AttributeError, TypeError):
            method_id = None
        if method_id:
            return _CLASSIFICATION_BY_ID.get(method_id, ClassificationMethod.UNKNOWN)

    try:
        return _CLASSIFICATION_BY_MODE.get(
            renderer.mode(), ClassificationMethod.UNKNOWN
        )
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return ClassificationMethod.UNKNOWN


def _color_from_qcolor(qcolor: Any) -> Color | None:
    """Convert a `QColor`, preserving alpha separately from the channels."""
    if qcolor is None or not qcolor.isValid():
        return None
    return Color(
        r=qcolor.red(),
        g=qcolor.green(),
        b=qcolor.blue(),
        a=qcolor.alphaF(),
    )


def _visible_symbol_layer(symbol: QgsSymbol) -> Any | None:
    """The top-most enabled symbol layer - the one the reader actually sees.

    QGIS paints symbol layers bottom-up, so index 0 is drawn *first* and anything
    above it covers it. Reading index 0 therefore reports the colour underneath:
    a river drawn as a neon line over a grey casing exports as the casing, and
    the map comes out drained compared to QGIS.

    Disabled layers are skipped - an unticked top layer is not on the map, so it
    must not decide the colour either.
    """
    if symbol is None:
        return None

    for index in reversed(range(symbol.symbolLayerCount())):
        candidate = symbol.symbolLayer(index)
        if candidate is None:
            continue
        enabled = _safe(candidate, "enabled")
        if enabled is False:
            continue
        return candidate
    return None


def translate_symbol(
    symbol: QgsSymbol | None,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str | None = None,
    depth: int = 0,
) -> SymbolSpec:
    """Flatten one QGIS symbol into the web-expressible subset.

    Reads the *top* symbol layer - see `_visible_symbol_layer` for why that is
    the one worth keeping when only one can survive.

    `depth` guards the one recursive case, a geometry generator's sub-symbol.
    """
    if symbol is None:
        report.unsupported(subject, "The layer has no symbol to translate.", layer_id)
        return SymbolSpec()

    layer_count = symbol.symbolLayerCount()
    symbol_layer = _visible_symbol_layer(symbol)
    if symbol_layer is None:
        report.unsupported(subject, "The symbol has no symbol layers.", layer_id)
        return SymbolSpec()

    generated = _translate_geometry_generator(
        symbol_layer, report, subject, layer_id, depth
    )
    if generated is not None:
        return generated

    if layer_count > 1:
        report.approximated(
            subject,
            f"The symbol stacks {layer_count} symbol layers; only the top one - "
            "the one you see - is translated. Effects built from stacked layers, "
            "such as a casing under a road or a glow behind a line, will not "
            "appear.",
            layer_id,
        )

    fill_color = _color_from_qcolor(_safe(symbol_layer, "fillColor"))
    stroke_color = _color_from_qcolor(_safe(symbol_layer, "strokeColor"))

    # **Every symbol layer answers `color()`; only fills and markers answer
    # `fillColor()`/`strokeColor()` with anything valid.** A line symbol layer
    # returns an invalid QColor from both, so reading only those exported every
    # line as the `#888888` placeholder - the drained grey that made a teal
    # river come out the colour of nothing in particular.
    own_color = _color_from_qcolor(_safe(symbol_layer, "color"))

    # Lines expose their width as `width()`; fills and markers as `strokeWidth()`.
    line_width = _safe(symbol_layer, "width")
    stroke_width = line_width
    if stroke_width is None:
        stroke_width = _safe(symbol_layer, "strokeWidth")
    stroke_width = float(stroke_width or 0.0) * MM_TO_PIXELS

    if line_width is not None:
        # A line symbol's own colour is its stroke, not a fill.
        stroke_color = stroke_color or own_color or fill_color
        fill_color = None
    else:
        fill_color = fill_color or own_color

    radius = _safe(symbol_layer, "size")
    if radius is not None:
        # QGIS marker size is a diameter; deck.gl wants a radius.
        radius = float(radius) * MM_TO_PIXELS / 2.0

    dash: tuple[float, ...] = ()
    if _safe(symbol_layer, "useCustomDashPattern"):
        pattern = _safe(symbol_layer, "customDashVector") or []
        dash = tuple(float(v) * MM_TO_PIXELS for v in pattern)

    icon_path = _safe(symbol_layer, "path")
    marker_shape = read_marker_shape(symbol_layer)

    # No note about the SVG or the shape here. On a point layer the symbol atlas
    # takes over and reports the outcome once for the whole layer - QGIS draws
    # the marker itself, parameter overrides and all - and a per-class note
    # saying the shape "may" be lost, on a layer where it demonstrably was not,
    # is worse than no note. `symbol_atlas.needs_atlas` is what decides; a
    # non-point layer that reaches here with a marker (a marker line, a point
    # pattern fill) is covered by the stacked-symbol-layer note above.

    return SymbolSpec(
        fill_color=fill_color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        stroke_dash=dash,
        radius=radius,
        opacity=float(symbol.opacity()),
        icon_path=str(icon_path) if icon_path else None,
        marker_shape=marker_shape,
        symbol_layer_count=layer_count,
        cap_rounded=read_cap_rounded(symbol_layer),
        join_rounded=read_join_rounded(symbol_layer),
        rotation=float(_safe(symbol_layer, "angle") or 0.0),
        offset_x=_offset(symbol_layer, "x"),
        offset_y=_offset(symbol_layer, "y"),
    )


# How deep a geometry generator's sub-symbol is followed. A generator whose
# sub-symbol is itself a generator is legal in QGIS and the nesting has no fixed
# limit, so the recursion needs a stop that is not "trust the data".
MAX_SUB_SYMBOL_DEPTH = 4


def _translate_geometry_generator(
    symbol_layer: Any,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str | None,
    depth: int,
) -> SymbolSpec | None:
    """A geometry generator, which draws a shape the layer's data does not hold.

    QGIS's geometry generator replaces a feature's geometry with the result of
    an expression - `buffer($geometry, 50)`, `centroid($geometry)`,
    `pole_of_inaccessibility(...)` - and then draws *that*. The exported data is
    the original geometry, so the drawn shape simply is not in the file, and no
    styling attribute can produce it.

    Before this, one of these exported as a plain coloured layer with **nothing
    said**: the generator answers `color()` like any symbol layer, so a buffer
    ring came out as the un-buffered polygon in an arbitrary colour. That is the
    silent loss this project exists to prevent, and it was found by diffing our
    property coverage against GeoLibre's.

    Returns `None` when this is not a generator, so the caller carries on.
    """
    if _safe(symbol_layer, "layerType") != "GeometryGenerator":
        return None

    expression = _safe(symbol_layer, "geometryExpression") or "an expression"
    report.unsupported(
        subject,
        f"The symbology draws a generated geometry (`{expression}`) rather than "
        "the layer's own. The web map has only the original geometry, so the "
        "generated shape cannot be drawn - the features are exported in their "
        "true positions instead. To keep the shape, run the equivalent "
        "Processing tool in QGIS and export the resulting layer.",
        layer_id,
    )

    # The generator's own colour is arbitrary; the sub-symbol holds the styling
    # the author actually chose, so the layer at least draws in their colours.
    sub_symbol = _safe(symbol_layer, "subSymbol")
    if sub_symbol is None or depth >= MAX_SUB_SYMBOL_DEPTH:
        return SymbolSpec()
    return translate_symbol(sub_symbol, report, subject, layer_id, depth + 1)


def read_cap_rounded(symbol_layer: Any) -> bool:
    """Whether the line ends are round, in the one bit deck.gl accepts.

    Qt has three cap styles and deck.gl has a boolean, so flat and square both
    become "not round". Square extends past the endpoint and flat does not, but
    that difference is a half-width of a line - invisible next to getting the
    round/not-round distinction wrong, which shows at every dead end.
    """
    style = _safe(symbol_layer, "penCapStyle")
    if style is None:
        return False
    try:
        from qgis.PyQt.QtCore import Qt

        return bool(style == Qt.PenCapStyle.RoundCap)
    except (ImportError, AttributeError):  # pragma: no cover
        return False


def read_join_rounded(symbol_layer: Any) -> bool:
    """Whether the line joins are round. Miter and bevel both read as not-round."""
    style = _safe(symbol_layer, "penJoinStyle")
    if style is None:
        return False
    try:
        from qgis.PyQt.QtCore import Qt

        return bool(style == Qt.PenJoinStyle.RoundJoin)
    except (ImportError, AttributeError):  # pragma: no cover
        return False


def _offset(symbol_layer: Any, axis: str) -> float:
    """One axis of a marker's offset, in pixels.

    QGIS gives a `QPointF` in millimetres. Screen y grows downward in both QGIS
    and deck.gl's pixel offsets, so no sign flip is needed.
    """
    point = _safe(symbol_layer, "offset")
    if point is None:
        return 0.0
    try:
        value = point.x() if axis == "x" else point.y()
    except AttributeError:  # pragma: no cover
        return 0.0
    return float(value) * MM_TO_PIXELS


def read_marker_shape(symbol_layer: Any) -> str | None:
    """Name the marker shape, e.g. `square`, `star`, `triangle`.

    QGIS offers around forty simple-marker shapes. qgis2web reads none of them
    and every marker comes out a circle - upstream qgis2web#1218, open since
    June 2026, with a side-by-side screenshot of squares becoming circles.

    Capturing the shape here is what makes it possible to draw it correctly
    later, or at minimum to *tell* the user it changed. `encodeShape` is the
    QGIS-blessed enum-to-string mapping, so the names stay stable across
    versions.
    """
    shape = _safe(symbol_layer, "shape")
    if shape is None:
        return None
    try:
        from qgis.core import QgsSimpleMarkerSymbolLayerBase

        return str(QgsSimpleMarkerSymbolLayerBase.encodeShape(shape))
    except (ImportError, AttributeError, TypeError):  # pragma: no cover
        return None


def _safe(obj: Any, method: str) -> Any:
    """Call `obj.method()` if it exists, else return None.

    Symbol-layer classes do not share one interface — `width()` exists on lines,
    `size()` on markers, `strokeWidth()` on fills — so probing is simpler and more
    robust across QGIS versions than a class hierarchy of special cases.
    """
    fn = getattr(obj, method, None)
    if fn is None:
        return None
    try:
        return fn()
    except (TypeError, ValueError):
        return None


def _translate_25d(
    renderer: Any,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> RendererSpec:
    """The 2.5D renderer's colours, which are not in its symbol.

    Its `QgsFillSymbol` is machinery, not styling: two of its three symbol
    layers are geometry generators that extrude and translate the polygon to
    fake walls and a raised roof. Translating that symbol the normal way reads
    the topmost layer - a geometry generator - and comes back with nothing
    usable, which is how a 2.5D layer used to export grey.

    The real colours are two plain accessors on the renderer itself. The roof is
    the fill, because the roof is the face you look at; `elevation_translator`
    separately turns the fake walls into a real extrusion.
    """
    roof = _color_from_qcolor(_safe(renderer, "roofColor"))
    wall = _color_from_qcolor(_safe(renderer, "wallColor"))
    report.approximated(
        subject,
        "The 2.5D renderer's roof colour becomes the layer's fill and its wall "
        "colour the outline. A single colour per layer is all this renderer "
        "offers, so nothing is lost - but shading each wall by its angle is a "
        "QGIS drawing trick that the web map replaces with real lighting.",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.SINGLE,
        symbol=SymbolSpec(fill_color=roof, stroke_color=wall),
    )


def translate_renderer(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
) -> RendererSpec:
    """Translate a layer's renderer, or record precisely why we cannot."""
    renderer: QgsFeatureRenderer | None = layer.renderer()
    layer_id = layer.id()
    subject = f"Symbology of '{layer.name()}'"

    if renderer is None:
        report.unsupported(subject, "The layer has no renderer.", layer_id)
        return RendererSpec(
            kind=RendererKind.UNSUPPORTED, unsupported_reason="no renderer"
        )

    if _safe(renderer, "type") == "25dRenderer":
        return _translate_25d(renderer, report, subject, layer_id)

    if isinstance(renderer, QgsSingleSymbolRenderer):
        symbol = translate_symbol(renderer.symbol(), report, subject, layer_id)
        report.preserved(subject, "Single symbol translated.", layer_id)
        return RendererSpec(kind=RendererKind.SINGLE, symbol=symbol)

    if isinstance(renderer, QgsCategorizedSymbolRenderer):
        return _translate_categorized(renderer, report, subject, layer_id)

    if isinstance(renderer, QgsGraduatedSymbolRenderer):
        return _translate_graduated(renderer, report, subject, layer_id)

    kind_name = type(renderer).__name__
    report.unsupported(
        subject,
        f"{kind_name} is not translated in 0.1.0. Supported renderers are single "
        "symbol, categorized and graduated. The layer will use a default style.",
        layer_id,
    )
    return RendererSpec(kind=RendererKind.UNSUPPORTED, unsupported_reason=kind_name)


# A `$field` reference in an OnlyMap accessor is a bare identifier; the
# expression language has no quoted or bracketed form. QGIS field names have no
# such restriction - shapefile and CSV columns are routinely called "Land Use" -
# and interpolating one straight into `$field == ...` produces markup the parser
# cannot read, which loses the symbology with nothing said about it.
EXPRESSION_SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unreferenceable_field(
    field_name: str,
    symbols: list[SymbolSpec],
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> RendererSpec | None:
    """A single-symbol fallback when the class field cannot be referenced.

    Returns `None` when the field is fine and the caller should carry on. The
    first class's symbol stands in, so the layer still draws with a colour the
    author chose rather than the generic grey.
    """
    if EXPRESSION_SAFE_FIELD.match(field_name):
        return None

    report.unsupported(
        subject,
        f"The classification field '{field_name}' cannot be referenced in a web "
        "map expression - only letters, digits and underscores are allowed, and "
        "it cannot start with a digit. The layer is drawn in a single colour "
        "instead. Rename the field in QGIS to keep the symbology.",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.SINGLE,
        symbol=symbols[0] if symbols else SymbolSpec(),
    )


def _translate_categorized(
    renderer: QgsCategorizedSymbolRenderer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> RendererSpec:
    categories: list[CategorySpec] = []
    hidden: list[str | int | float | None] = []

    for index, category in enumerate(renderer.categories()):
        if not category.renderState():
            # An unchecked class in QGIS means "do not draw these features". The
            # value is recorded so the reader can drop the matching features -
            # omitting the class from the expression only sends them to the
            # "other" fallback colour, which draws exactly what the author hid.
            hidden.append(_python_value(category.value()))
            continue
        symbol = translate_symbol(
            category.symbol(), report, f"{subject}, class {index + 1}", layer_id
        )
        categories.append(
            CategorySpec(
                value=_python_value(category.value()),
                label=category.label(),
                symbol=symbol,
            )
        )

    if hidden:
        count = len(hidden)
        report.approximated(
            subject,
            f"{count} categor{'y is' if count == 1 else 'ies are'} switched "
            "off in QGIS; matching features are omitted from the export.",
            layer_id,
        )

    field_name = renderer.classAttribute()
    fallback = _unreferenceable_field(
        field_name, [c.symbol for c in categories], report, subject, layer_id
    )
    if fallback is not None:
        return fallback

    report.preserved(
        subject,
        f"Categorized on '{field_name}' with {len(categories)} classes.",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.CATEGORIZED,
        field_name=field_name,
        categories=tuple(categories),
        ramp_name=_ramp_name(renderer),
        hidden_values=tuple(hidden),
    )


def _translate_graduated(
    renderer: QgsGraduatedSymbolRenderer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> RendererSpec:
    classes: list[GraduatedClassSpec] = []

    for index, value_range in enumerate(renderer.ranges()):
        symbol = translate_symbol(
            value_range.symbol(), report, f"{subject}, range {index + 1}", layer_id
        )
        classes.append(
            GraduatedClassSpec(
                lower=float(value_range.lowerValue()),
                upper=float(value_range.upperValue()),
                label=value_range.label(),
                symbol=symbol,
            )
        )

    field_name = renderer.classAttribute()
    fallback = _unreferenceable_field(
        field_name, [c.symbol for c in classes], report, subject, layer_id
    )
    if fallback is not None:
        return fallback

    classification = classification_of(renderer)
    if classification is ClassificationMethod.UNKNOWN:
        report.approximated(
            subject,
            "The classification method is custom or unrecognised. The class "
            "breaks are exported exactly as QGIS computed them, so the map is "
            "correct, but the method is not recorded in the legend.",
            layer_id,
        )

    report.preserved(
        subject,
        f"Graduated on '{field_name}' with {len(classes)} classes "
        f"({classification.value}).",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name=field_name,
        classes=tuple(classes),
        classification=classification,
        ramp_name=_ramp_name(renderer),
    )


def class_symbols(renderer: QgsFeatureRenderer | None) -> list[QgsSymbol]:
    """The live QGIS symbols behind a `RendererSpec`'s classes, in the same order.

    The symbol atlas has to *draw* what the spec describes, and a `SymbolSpec`
    is a flattened summary rather than something drawable - the whole point of
    rasterising is to let QGIS render the original object. So the atlas needs
    both, paired up, and pairing them positionally is only safe if this walk
    applies exactly the same skip rules `_translate_categorized` does. It does:
    a category with `renderState()` false is dropped in both.

    Kept here, next to those rules, rather than in the atlas module, so the two
    cannot drift apart unnoticed. `tests/qgis/test_renderer_translator.py` pins
    the lengths against each other for all three renderer kinds.

    **Clones, not the renderer's own symbols.** `renderer.categories()` hands
    back a temporary list whose entries own the `QgsSymbol` pointers; once that
    temporary is collected, a pointer held past the end of the comprehension
    refers to freed memory. Using one crashes the interpreter rather than
    raising - which is exactly what it did, as a segfault inside `clone()` two
    tests after the list was built. Cloning makes Python the owner and the
    lifetime obvious.
    """
    if renderer is None:
        return []

    # The 2.5D renderer is deliberately absent: its symbol is machinery (two of
    # its three symbol layers are geometry generators), it applies to polygons,
    # and `_translate_25d` synthesises a spec from the renderer's own colour
    # accessors rather than from any drawable symbol.
    if _safe(renderer, "type") == "25dRenderer":
        return []

    if isinstance(renderer, QgsSingleSymbolRenderer):
        symbol = renderer.symbol()
        return [symbol.clone()] if symbol is not None else []

    if isinstance(renderer, QgsCategorizedSymbolRenderer):
        return [
            category.symbol().clone()
            for category in renderer.categories()
            if category.renderState() and category.symbol() is not None
        ]

    if isinstance(renderer, QgsGraduatedSymbolRenderer):
        return [
            value_range.symbol().clone()
            for value_range in renderer.ranges()
            if value_range.symbol() is not None
        ]

    return []


def _ramp_name(renderer: Any) -> str | None:
    ramp = _safe(renderer, "sourceColorRamp")
    if ramp is None:
        return None
    return _safe(ramp, "type")


def _python_value(value: Any) -> str | int | float | None:
    """Coerce a QVariant-ish category value to a plain Python scalar.

    Category values reach the manifest as JSON, so anything exotic becomes its
    string form rather than failing to serialise later.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return value
    text = str(value)
    return None if text in ("NULL", "") else text
