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

from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
)

from .export_ir import (
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

# QGIS reports symbol sizes and widths in millimetres by default. Web renderers
# work in pixels; 1 mm at 96 dpi is ~3.78 px. Approximate by nature — recorded in
# the report rather than presented as exact.
MM_TO_PIXELS = 96.0 / 25.4

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


def _first_symbol_layer(symbol: QgsSymbol) -> Any | None:
    if symbol is None or symbol.symbolLayerCount() == 0:
        return None
    return symbol.symbolLayer(0)


def translate_symbol(
    symbol: QgsSymbol | None,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str | None = None,
) -> SymbolSpec:
    """Flatten one QGIS symbol into the web-expressible subset.

    Reads the *bottom* symbol layer, which is the one a stacked symbol draws
    first and the one that carries the base fill in almost every real style.
    """
    if symbol is None:
        report.unsupported(subject, "The layer has no symbol to translate.", layer_id)
        return SymbolSpec()

    layer_count = symbol.symbolLayerCount()
    symbol_layer = _first_symbol_layer(symbol)
    if symbol_layer is None:
        report.unsupported(subject, "The symbol has no symbol layers.", layer_id)
        return SymbolSpec()

    if layer_count > 1:
        report.approximated(
            subject,
            f"The symbol stacks {layer_count} symbol layers; only the bottom one "
            "is translated. Effects built from stacked layers - casing on roads, "
            "hatching, glow - will not appear.",
            layer_id,
        )

    fill_color = _color_from_qcolor(_safe(symbol_layer, "fillColor"))
    stroke_color = _color_from_qcolor(_safe(symbol_layer, "strokeColor"))

    # Lines expose their width as `width()`; fills and markers as `strokeWidth()`.
    stroke_width = _safe(symbol_layer, "width")
    if stroke_width is None:
        stroke_width = _safe(symbol_layer, "strokeWidth")
    stroke_width = float(stroke_width or 0.0) * MM_TO_PIXELS

    # A line symbol's own colour is its stroke, not a fill.
    if stroke_color is None and fill_color is not None and _safe(symbol_layer, "width"):
        stroke_color, fill_color = fill_color, None

    radius = _safe(symbol_layer, "size")
    if radius is not None:
        # QGIS marker size is a diameter; deck.gl wants a radius.
        radius = float(radius) * MM_TO_PIXELS / 2.0

    dash: tuple[float, ...] = ()
    if _safe(symbol_layer, "useCustomDashPattern"):
        pattern = _safe(symbol_layer, "customDashVector") or []
        dash = tuple(float(v) * MM_TO_PIXELS for v in pattern)

    icon_path = _safe(symbol_layer, "path")
    if icon_path:
        report.approximated(
            subject,
            f"Uses the SVG marker '{icon_path}'. The file is embedded, but QGIS "
            "SVG parameter overrides (dynamic fill and stroke) are not applied.",
            layer_id,
        )

    return SymbolSpec(
        fill_color=fill_color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        stroke_dash=dash,
        radius=radius,
        opacity=float(symbol.opacity()),
        icon_path=str(icon_path) if icon_path else None,
        symbol_layer_count=layer_count,
    )


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


def _translate_categorized(
    renderer: QgsCategorizedSymbolRenderer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> RendererSpec:
    categories: list[CategorySpec] = []
    skipped = 0

    for index, category in enumerate(renderer.categories()):
        if not category.renderState():
            # An unchecked class in QGIS means "do not draw these features".
            skipped += 1
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

    if skipped:
        report.approximated(
            subject,
            f"{skipped} categor{'y is' if skipped == 1 else 'ies are'} switched "
            "off in QGIS; matching features are omitted from the export.",
            layer_id,
        )

    report.preserved(
        subject,
        f"Categorized on '{renderer.classAttribute()}' with {len(categories)} classes.",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.CATEGORIZED,
        field_name=renderer.classAttribute(),
        categories=tuple(categories),
        ramp_name=_ramp_name(renderer),
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
        f"Graduated on '{renderer.classAttribute()}' with {len(classes)} classes "
        f"({classification.value}).",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name=renderer.classAttribute(),
        classes=tuple(classes),
        classification=classification,
        ramp_name=_ramp_name(renderer),
    )


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
