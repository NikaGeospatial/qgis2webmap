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

import math
import re
from dataclasses import dataclass, replace
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
    GeometryKind,
    GraduatedClassSpec,
    GroundSize,
    RendererKind,
    RendererSpec,
    SymbolSpec,
    ValueRange,
    uniform_ground_units,
)
from .fidelity_report import FidelityReportBuilder
from .render_units import (
    CSS_PIXEL_METRES,
    GroundUnits,
    MapUnitScale,
    ground_size,
    is_map_dependent,
    to_pixels,
)
from .symbol_atlas import layer_symbols

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
    QgsGraduatedSymbolRenderer.Mode.EqualInterval: ClassificationMethod.EQUAL_INTERVAL,
    QgsGraduatedSymbolRenderer.Mode.Quantile: ClassificationMethod.QUANTILE,
    QgsGraduatedSymbolRenderer.Mode.Jenks: ClassificationMethod.NATURAL_BREAKS,
    QgsGraduatedSymbolRenderer.Mode.StdDev: ClassificationMethod.STANDARD_DEVIATION,
    QgsGraduatedSymbolRenderer.Mode.Pretty: ClassificationMethod.PRETTY_BREAKS,
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
    scale: MapUnitScale | None = None,
    ground: GroundUnits | None = None,
) -> SymbolSpec:
    """Flatten one QGIS symbol into the web-expressible subset.

    Reads the *top* symbol layer - see `_visible_symbol_layer` for why that is
    the one worth keeping when only one can survive.

    `depth` guards the one recursive case, a geometry generator's sub-symbol.

    Sizes come out in CSS pixels whatever unit the symbol layer uses; `scale` is
    what map units are worth on screen (see `render_units`). A marker radius,
    stroke width or icon size given on the ground is also kept as a `GroundSize`
    where `ground` lets the runtime scale it exactly. Symbol opacity is
    folded into the colours' alpha here, once - nothing downstream reads
    `SymbolSpec.opacity` to apply it again.
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
        symbol_layer, report, subject, layer_id, depth, scale, ground
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
    width_was_read = stroke_width is not None
    sizes = _SizeReader(scale, ground)
    stroke_width, stroke_width_ground = sizes.sized(
        float(stroke_width or 0.0),
        _safe(
            symbol_layer, "widthUnit" if line_width is not None else "strokeWidthUnit"
        ),
    )

    # An outline is switched off with its pen style, not its width: "No Pen"
    # keeps the colour and the 0.26 mm default width, so reading only those
    # drew a dark ring QGIS never draws. A transparent stroke is that same
    # nothing in every class expression the writer builds. A width of 0 with
    # a pen is the other way round -- QGIS's hairline, one device pixel, which
    # a zero width left at deck.gl's default of one metre, too thin to see.
    pen = _pen_style_value(
        symbol_layer, "penStyle" if line_width is not None else "strokeStyle"
    )
    if pen == _NO_PEN:
        stroke_color = _transparent(stroke_color or own_color)
        stroke_width = 0.0
        stroke_width_ground = None
    elif pen is not None and width_was_read and stroke_width == 0.0:
        stroke_width = HAIRLINE_PIXELS
        stroke_width_ground = None

    if line_width is not None:
        # A line symbol's own colour is its stroke, not a fill.
        stroke_color = stroke_color or own_color or fill_color
        fill_color = None
    else:
        fill_color = fill_color or own_color
        # "No Brush" is QGIS's outline-only fill: like "No Pen", the colour
        # is kept and only the style says it is not drawn.
        if _brush_style_value(symbol_layer) == _NO_BRUSH:
            fill_color = _transparent(fill_color)

    radius = _safe(symbol_layer, "size")
    radius_ground = None
    if radius is not None:
        # QGIS marker size is a diameter; deck.gl wants a radius.
        radius, diameter_ground = sizes.sized(
            float(radius), _safe(symbol_layer, "sizeUnit")
        )
        radius /= 2.0
        if diameter_ground is not None:
            radius_ground = GroundSize(
                diameter_ground.value / 2.0, diameter_ground.units
            )

    dash = read_dash_pattern(symbol_layer, stroke_width)
    if len(dash) > 2:
        # deck.gl strokes one on/off pair, so a dash-dot loses its dots. Said
        # here because the line still comes out dashed - it looks right enough
        # that nobody would check, which is exactly when a note is worth having.
        report.approximated(
            subject,
            "The line uses a dash-dot pattern; the exported map draws a plain "
            "dashed line with the same dash length. The dots are not drawn.",
            layer_id,
        )

    icon_path = _safe(symbol_layer, "path")
    marker_shape = read_marker_shape(symbol_layer)

    offset_unit = _safe(symbol_layer, "offsetUnit")
    offset_x = _offset(symbol_layer, "x", sizes, offset_unit)
    offset_y = _offset(symbol_layer, "y", sizes, offset_unit)
    sizes.report(report, subject, layer_id)
    _report_data_defined(symbol_layer, symbol, report, subject, layer_id)

    # Symbol opacity multiplies the colours' own alpha, and layer opacity is
    # carried separately as `<om-layer opacity>`: three factors, each once.
    opacity = float(symbol.opacity())
    fill_color = _with_opacity(fill_color, opacity)
    stroke_color = _with_opacity(stroke_color, opacity)

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
        radius_ground=radius_ground,
        stroke_width_ground=stroke_width_ground,
        opacity=opacity,
        icon_path=str(icon_path) if icon_path else None,
        marker_shape=marker_shape,
        symbol_layer_count=layer_count,
        cap_rounded=read_cap_rounded(symbol_layer),
        join_rounded=read_join_rounded(symbol_layer),
        rotation=float(_safe(symbol_layer, "angle") or 0.0),
        offset_x=offset_x,
        offset_y=offset_y,
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
    scale: MapUnitScale | None = None,
    ground: GroundUnits | None = None,
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
    return translate_symbol(
        sub_symbol, report, subject, layer_id, depth + 1, scale, ground
    )


# Qt's own dash patterns for its named pen styles, in units of the pen width -
# which is how Qt defines them, and why they scale with a thicker line. Kept as
# a table rather than derived, because these are the numbers Qt draws with and
# a line that looks dashed in QGIS should look the same dashed in the browser.
QT_DASH_PATTERNS = {
    "DashLine": (4.0, 2.0),
    "DotLine": (1.0, 2.0),
    "DashDotLine": (4.0, 2.0, 1.0, 2.0),
    "DashDotDotLine": (4.0, 2.0, 1.0, 2.0, 1.0, 2.0),
}


# `Qt::PenStyle`, which is stable across Qt 5 and 6 - it is part of the ABI.
# Solid (1) and NoPen (0) are deliberately absent: neither dashes.
QT_PEN_STYLE_VALUES = {
    2: "DashLine",
    3: "DotLine",
    4: "DashDotLine",
    5: "DashDotDotLine",
}

#: `Qt::NoPen`: the stroke style QGIS offers as "No Pen" / "No line".
_NO_PEN = 0

#: The width QGIS draws a zero-width ("hairline") stroke at: one device pixel,
#: which in a browser is one CSS pixel.
HAIRLINE_PIXELS = 1.0


def _pen_style_value(symbol_layer: Any, method: str) -> int | None:
    """A `Qt::PenStyle` read with `method`, as a plain int; `None` when the
    symbol layer has no such style (gradient fills, SVG markers ...).

    PyQt5 returns a bare int and PyQt6 an enum, as `_pen_style_name` explains.
    """
    style = _safe(symbol_layer, method)
    if style is None:
        return None
    for read in (int, lambda s: s.value):
        try:
            return int(read(style))
        except (AttributeError, TypeError, ValueError):
            continue
    return None


#: `Qt::NoBrush`: the fill style QGIS offers as "No Brush".
_NO_BRUSH = 0


def _brush_style_value(symbol_layer: Any) -> int | None:
    """A fill's `Qt::BrushStyle` as a plain int; `None` for symbol layers
    without one (markers, gradient and pattern fills ...). Read the way
    `_pen_style_value` reads a pen style -- both are plain Qt enums."""
    return _pen_style_value(symbol_layer, "brushStyle")


def _transparent(color: Color | None) -> Color:
    """`color` with its alpha at zero (black when there is none)."""
    if color is None:
        return Color(r=0, g=0, b=0, a=0.0)
    return Color(r=color.r, g=color.g, b=color.b, a=0.0)


def _pen_style_name(symbol_layer: Any) -> str | None:
    """The Qt pen style as a plain name, or `None` for solid or unreadable.

    Both bindings have to be handled, and they disagree: **PyQt5 returns a bare
    `int`** (verified in the QGIS LTR container - `penStyle()` gives `2` for a
    dashed line, with no `.name` and a `str()` of `"2"`), while PyQt6 returns a
    Python enum with `.name` and `.value`. An earlier version read only the name
    and so found nothing on PyQt5, which is every QGIS LTR install - it silently
    exported every dashed line solid, exactly the bug this was fixing.
    """
    style = _safe(symbol_layer, "penStyle")
    if style is None:
        return None

    name = getattr(style, "name", None)
    if isinstance(name, str) and name in QT_DASH_PATTERNS:
        return name

    for read in (int, lambda s: s.value):
        try:
            return QT_PEN_STYLE_VALUES.get(read(style))
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def read_dash_pattern(symbol_layer: Any, stroke_width: float) -> tuple[float, ...]:
    """A line's dash pattern in **pixels**, or `()` when it is solid.

    Two separate things in QGIS produce a dashed line, and reading only one of
    them - which is what this did - misses the common case entirely:

    * **A custom dash pattern**, `useCustomDashPattern` plus `customDashVector`,
      already in millimetres and converted here like every other length.
    * **A named pen style** - the "Dash", "Dot", "Dash Dot" entries in the line
      style dropdown - which sets `penStyle` and leaves the custom vector empty.
      This is what most people actually click, and it exported as a solid line.

    Both come out in pixels so the caller has one unit to reason about. The
    conversion to whatever the target renderer wants belongs to the writer.
    """
    if _safe(symbol_layer, "useCustomDashPattern"):
        pattern = _safe(symbol_layer, "customDashVector") or []
        return tuple(float(v) * MM_TO_PIXELS for v in pattern)

    style = _pen_style_name(symbol_layer)
    pattern = QT_DASH_PATTERNS.get(style or "")
    if not pattern or stroke_width <= 0:
        return ()
    # Qt's patterns are multiples of the pen width, so a 2px line dashes at
    # 8px on, 4px off.
    return tuple(value * stroke_width for value in pattern)


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


def _offset(
    symbol_layer: Any, axis: str, sizes: _SizeReader, unit: Any = None
) -> float:
    """One axis of a marker's offset, in pixels.

    QGIS gives a `QPointF` in `offsetUnit()`. Screen y grows downward in both
    QGIS and deck.gl's pixel offsets, so no sign flip is needed.
    """
    point = _safe(symbol_layer, "offset")
    if point is None:
        return 0.0
    try:
        value = point.x() if axis == "x" else point.y()
    except AttributeError:  # pragma: no cover
        return 0.0
    return sizes.pixels(float(value), unit)


class _SizeReader:
    """Converts one symbol layer's sizes to pixels and remembers what it assumed."""

    def __init__(
        self, scale: MapUnitScale | None, ground: GroundUnits | None = None
    ) -> None:
        self._scale = scale
        self._ground = ground
        self.map_dependent = False
        self.unconvertible = False

    def sized(self, value: float, unit: Any) -> tuple[float, GroundSize | None]:
        """`pixels`, plus the size on the ground when the runtime can hold it.

        A ground size is exact, so it is not counted as approximated (the layer
        says so once, in `report_ground_sizes`); the pixels beside it are only
        what clustering and label offsets still read.
        """
        ground = ground_size(value, unit, self._ground)
        if ground is None:
            return self.pixels(value, unit), None
        pixels = to_pixels(value, unit, self._scale)
        return (value * MM_TO_PIXELS if pixels is None else pixels), ground

    def pixels(self, value: float, unit: Any) -> float:
        # An unreadable unit is QGIS's default, millimetres, as before.
        if unit is None:
            return value * MM_TO_PIXELS
        if is_map_dependent(unit):
            self.map_dependent = True
        pixels = to_pixels(value, unit, self._scale)
        if pixels is None:
            self.unconvertible = True
            return value * MM_TO_PIXELS
        return pixels

    def report(
        self, report: FidelityReportBuilder, subject: str, layer_id: str | None
    ) -> None:
        if self.map_dependent:
            if self._scale is None:
                report.approximated(
                    subject,
                    "A size is given in map units or metres on the ground, which "
                    "change with the zoom in QGIS, and the layer has no extent to "
                    "fix a scale from. It is drawn as if in millimetres.",
                    layer_id,
                )
                self.unconvertible = False
            else:
                report.approximated(
                    subject,
                    "A size is given in map units or metres on the ground, so it "
                    "grows and shrinks with the zoom in QGIS. The web map draws "
                    "fixed pixel sizes: it is fixed at its size at about 1:"
                    f"{round(self._scale.scale_denominator, -1):,.0f}, where this "
                    "layer fills a 1000 pixel wide view.",
                    layer_id,
                )
        if self.unconvertible:
            report.approximated(
                subject,
                "A size is given as a percentage or in a unit the exporter does "
                "not know, so it is drawn as if in millimetres.",
                layer_id,
            )


def _with_opacity(color: Color | None, opacity: float) -> Color | None:
    """`color` with its alpha multiplied by a symbol's opacity."""
    if color is None or opacity >= 1.0:
        return color
    return replace(color, a=color.a * max(opacity, 0.0))


def _report_data_defined(
    symbol_layer: Any,
    symbol: QgsSymbol,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str | None,
) -> None:
    """Say so when sizes, colours or opacity are driven by data per feature.

    The exporter reads each property's static value, so a data-defined size or
    opacity is drawn at the value QGIS shows when no feature overrides it. That
    is a visible difference on exactly the maps that use them, so it is named.
    """
    properties = _safe(symbol_layer, "dataDefinedProperties")
    names: list[str] = []
    if properties is not None:
        definitions = _safe(symbol_layer, "propertyDefinitions") or {}
        for key in properties.propertyKeys():
            if not properties.isActive(key):
                continue
            definition = definitions.get(key)
            names.append(definition.name() if definition is not None else str(key))
    if names:
        report.approximated(
            subject,
            "The symbol sets "
            + ", ".join(sorted(names))
            + " from data per feature. The web map draws one value for all "
            "features - the symbol's static one - so the variation is lost.",
            layer_id,
        )


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
    scale = reference_scale(layer)
    ground = ground_units(layer)

    if renderer is None:
        report.unsupported(subject, "The layer has no renderer.", layer_id)
        return RendererSpec(
            kind=RendererKind.UNSUPPORTED, unsupported_reason="no renderer"
        )

    if _safe(renderer, "type") == "25dRenderer":
        return _translate_25d(renderer, report, subject, layer_id)

    if isinstance(renderer, QgsSingleSymbolRenderer):
        symbol = translate_symbol(
            renderer.symbol(), report, subject, layer_id, scale=scale, ground=ground
        )
        report.preserved(subject, "Single symbol translated.", layer_id)
        return RendererSpec(kind=RendererKind.SINGLE, symbol=symbol)

    if isinstance(renderer, QgsCategorizedSymbolRenderer):
        return _translate_categorized(
            renderer, report, subject, layer_id, scale, ground
        )

    if isinstance(renderer, QgsGraduatedSymbolRenderer):
        return _translate_graduated(renderer, report, subject, layer_id, scale, ground)

    kind_name = type(renderer).__name__
    report.unsupported(
        subject,
        f"This renderer ({kind_name}) is not translated yet. Supported renderers "
        "are single symbol, categorized and graduated. The layer will use a "
        "default style.",
        layer_id,
    )
    return RendererSpec(kind=RendererKind.UNSUPPORTED, unsupported_reason=kind_name)


# The view a layer's map-unit sizes are fixed at: the layer's extent fitted into
# this many CSS pixels, which is roughly how the web map opens on it.
REFERENCE_VIEW_PIXELS = (1000.0, 700.0)

# Ground metres in one degree of latitude, and of longitude at the equator.
_METRES_PER_DEGREE_LAT = 110_540.0
_METRES_PER_DEGREE_LON = 111_320.0


def reference_scale(layer: QgsVectorLayer) -> MapUnitScale | None:
    """What a map unit is worth in pixels where the layer fills the opening view.

    `None` when the layer has no usable extent (empty, or a single point), in
    which case there is no scale to fix a map-unit size at.
    """
    from qgis.core import Qgis, QgsUnitTypes

    try:
        extent = layer.extent()
        if extent.isNull() or extent.isEmpty():
            return None
        crs = layer.crs()
        map_units = crs.mapUnits()
        if map_units == Qgis.DistanceUnit.Degrees:
            middle = math.radians(extent.center().y())
            lon_metres = _METRES_PER_DEGREE_LON * math.cos(middle)
            metres_per_unit = lon_metres
            width = extent.width() * lon_metres
            height = extent.height() * _METRES_PER_DEGREE_LAT
        else:
            metres_per_unit = QgsUnitTypes.fromUnitToUnitFactor(
                map_units, Qgis.DistanceUnit.Meters
            )
            width = extent.width() * metres_per_unit
            height = extent.height() * metres_per_unit
    except (AttributeError, TypeError, ValueError):  # pragma: no cover
        return None
    view_width, view_height = REFERENCE_VIEW_PIXELS
    metres_per_pixel = max(width / view_width, height / view_height)
    if not (metres_per_pixel > CSS_PIXEL_METRES / 1000 and metres_per_unit > 0):
        return None
    return MapUnitScale(
        metres_per_pixel=metres_per_pixel, metres_per_map_unit=metres_per_unit
    )


def ground_units(layer: QgsVectorLayer) -> GroundUnits | None:
    """What a map unit of the layer's CRS is on the ground, where that is fixed."""
    from qgis.core import Qgis, QgsProject, QgsUnitTypes

    try:
        crs = layer.crs()
        if not crs.isValid():
            return None
        if crs.authid() in WEB_MERCATOR_AUTHIDS:
            return GroundUnits(metres_per_map_unit=None, web_mercator=True)
        map_units = crs.mapUnits()
        if map_units == Qgis.DistanceUnit.Degrees:
            return GroundUnits(
                metres_per_map_unit=None,
                ellipsoidal=QgsProject.instance().ellipsoid() != "NONE",
            )
        factor = QgsUnitTypes.fromUnitToUnitFactor(map_units, Qgis.DistanceUnit.Meters)
    except (AttributeError, TypeError, ValueError):  # pragma: no cover
        return None
    return GroundUnits(metres_per_map_unit=factor) if factor > 0 else None


def harmonise_ground_sizes(
    symbols: list[SymbolSpec],
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str | None,
) -> list[SymbolSpec]:
    """Drop ground sizes that the layer's classes cannot all share.

    A layer has one `*-units` attribute per size, so a ground size is emitted
    only when every class has one in the same unit. Where some classes are on
    the ground and others on the screen (or in another ground unit), all of them
    keep their reference-scale pixel size instead, and the report says so.
    """
    if len(symbols) < 2:
        return symbols
    result = list(symbols)
    for ground_field, pixel_field, what in _GROUND_FIELDS:
        has_ground = [getattr(s, ground_field) is not None for s in symbols]
        if not any(has_ground) or uniform_ground_units(
            symbols, ground_field, pixel_field
        ):
            continue
        cleared: dict[str, Any] = {ground_field: None}
        result = [replace(s, **cleared) for s in result]
        report.approximated(
            subject,
            f"The classes give the {what} in different units, and the web map "
            "has one unit per layer, so it is fixed at its size at about the "
            "scale where the layer fills the opening view instead of zooming "
            "as in QGIS.",
            layer_id,
        )
    return result


# The ground sizes a layer shares across its classes: field, the pixel field it
# stands in for, and its name for the report.
_GROUND_FIELDS = (
    ("radius_ground", "radius", "marker size"),
    ("stroke_width_ground", "stroke_width", "stroke width"),
    ("icon_size_ground", "icon_size", "marker size"),
)


# The unit and value accessors of the sizes that make a marker's picture bigger
# or smaller: its size, its outline, its offset and a line's width.
_SIZE_ACCESSORS = (
    ("sizeUnit", "size"),
    ("strokeWidthUnit", "strokeWidth"),
    ("offsetUnit", "offset"),
    ("widthUnit", "width"),
)


def _is_zero(value: Any) -> bool:
    """Whether a size, or a `QPointF` offset, is nothing."""
    if hasattr(value, "x"):
        return not (value.x() or value.y())
    return not value


def symbol_pixels_per_ground_unit(
    symbol: QgsSymbol, context: Any | None, ground: GroundUnits | None
) -> tuple[str, float] | None:
    """How many pixels one ground unit is, if the whole symbol lives on the ground.

    A rasterised marker can zoom with the map only as a whole picture, so every
    size that shapes it - the marker, its outline, its offset, in every symbol
    layer - must be on the ground in one deck.gl unit. One outline in
    millimetres would be scaled with the rest, which QGIS does not do.
    Returns that unit and the pixels one of it measures at `context`'s scale,
    or `None` when the symbol cannot zoom as a picture.
    """
    if context is None:
        return None
    found: tuple[str, float] | None = None
    for index in range(symbol.symbolLayerCount()):
        symbol_layer = symbol.symbolLayer(index)
        if symbol_layer is None or _safe(symbol_layer, "enabled") is False:
            continue
        for unit_accessor, value_accessor in _SIZE_ACCESSORS:
            unit = _safe(symbol_layer, unit_accessor)
            value = _safe(symbol_layer, value_accessor)
            if unit is None or value is None:
                continue
            if _is_zero(value):
                # A zero-width outline with a pen is QGIS's hairline: one
                # device pixel at any zoom, which a scaled picture cannot keep.
                if value_accessor == "strokeWidth" and (
                    _pen_style_value(symbol_layer, "strokeStyle") != _NO_PEN
                ):
                    return None
                continue
            one = ground_size(1.0, unit, ground)
            if one is None or (found is not None and found[0] != one.units):
                return None
            if found is None:
                pixels = float(context.convertToPainterUnits(1.0, unit))
                if pixels <= 0:
                    return None
                found = (one.units, pixels / one.value)
    return found


def report_ground_sizes(
    renderer: RendererSpec,
    atlas: bool,
    geometry: GeometryKind,
    report: FidelityReportBuilder,
    layer_name: str,
    layer_id: str | None,
) -> None:
    """Say once what the layer will draw at its true size on the ground.

    Read off the finished spec rather than while translating, because only then
    is it known whether the classes agreed and whether a symbol atlas took over
    the markers.
    """
    symbols = layer_symbols(renderer)
    shown = []
    marker_fields = (
        ("icon_size_ground", "icon_size")
        if atlas
        else (
            "radius_ground",
            "radius",
        )
    )
    if (atlas or geometry is GeometryKind.POINT) and uniform_ground_units(
        symbols, *marker_fields
    ):
        shown.append("marker size")
    if any(s.stroke_width_ground for s in symbols) and uniform_ground_units(
        symbols, "stroke_width_ground", "stroke_width"
    ):
        shown.append("line width" if geometry is GeometryKind.LINE else "outline width")
    if shown:
        report.preserved(
            f"Symbology of '{layer_name}'",
            f"The {' and '.join(shown)} is given in map units or metres on the "
            "ground. It is kept as that distance, so it grows and shrinks with "
            "the zoom in the web map as it does in QGIS.",
            layer_id,
        )


WEB_MERCATOR_AUTHIDS = frozenset({"EPSG:3857", "EPSG:900913"})


def reference_render_context(layer: QgsVectorLayer) -> Any | None:
    """A QGIS render context at `reference_scale`, for drawing the layer's markers.

    The symbol atlas has QGIS draw the markers, so map-unit sizes need the same
    scale there as in `translate_symbol`: one pixel is `metres_per_pixel` of
    ground, and metres-in-map-units resolve against the layer's CRS and extent.
    `None` when there is no reference scale, which leaves QGIS's own defaults.
    """
    from qgis.core import QgsDistanceArea, QgsMapToPixel, QgsProject, QgsRenderContext

    scale = reference_scale(layer)
    if scale is None:
        return None
    context = QgsRenderContext()
    context.setScaleFactor(MM_TO_PIXELS)
    context.setMapToPixel(
        QgsMapToPixel(scale.metres_per_pixel / scale.metres_per_map_unit)
    )
    context.setExtent(layer.extent())
    area = QgsDistanceArea()
    area.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    # Metres on the ground become degrees only on an ellipsoid; QGIS measures on
    # the project's, and guesses wrongly without one.
    area.setEllipsoid(QgsProject.instance().ellipsoid())
    context.setDistanceArea(area)
    return context


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


@dataclass(frozen=True)
class _ResolvedCategory:
    """What QGIS actually uses one category for.

    `values` are the explicit values it still owns after later categories have
    taken theirs, `catch_all` whether it owns the empty/NULL "all other values"
    slot. A category with neither is never consulted.
    """

    values: tuple[str | int | float, ...]
    catch_all: bool

    @property
    def used(self) -> bool:
        return bool(self.values) or self.catch_all


def category_key(value: object) -> str:
    """The string QGIS hashes a category or feature value under.

    NULL and the empty string share the key `""`: QGIS treats both as the
    catch-all slot. A whole-number float keys as the integer, as Qt prints it.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _category_values(value: Any) -> list[str | int | float | None]:
    """A category's values: several for a grouped class, `None` for the catch-all."""
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    scalars = (_python_value(item) for item in items)
    return [None if scalar == "" else scalar for scalar in scalars]


def _resolve_categories(categories: list[Any]) -> list[_ResolvedCategory]:
    """Settle which category owns each value, as QGIS's lookup table does.

    QGIS keys its table on the value's string, a later category overwriting an
    earlier one with the same key - enabled or not. The caller keeps `categories`
    alive: the symbols the list owns are freed with it.
    """
    owner: dict[str, int] = {}
    for index, category in enumerate(categories):
        for value in _category_values(category.value()):
            owner[category_key(value)] = index

    resolved = []
    for index, category in enumerate(categories):
        values: list[str | int | float] = []
        seen: set[str] = set()
        catch_all = False
        for value in _category_values(category.value()):
            key = category_key(value)
            if owner[key] != index or key in seen:
                continue
            seen.add(key)
            if value is None:
                catch_all = True
            else:
                values.append(value)
        resolved.append(_ResolvedCategory(tuple(values), catch_all))
    return resolved


def _translate_categorized(
    renderer: QgsCategorizedSymbolRenderer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
    scale: MapUnitScale | None = None,
    ground: GroundUnits | None = None,
) -> RendererSpec:
    categories: list[CategorySpec] = []
    hidden: list[str | int | float | None] = []
    switched_off = 0
    has_catch_all = False

    qgis_categories = renderer.categories()
    resolved_categories = _resolve_categories(qgis_categories)
    for index, (category, resolved) in enumerate(
        zip(qgis_categories, resolved_categories)
    ):
        if not resolved.used:
            # A later category with the same value replaced this one, so QGIS
            # never reaches it.
            continue
        if not category.renderState():
            # An unchecked class in QGIS means "do not draw these features". The
            # value is recorded so the reader can drop the matching features -
            # omitting the class from the expression only sends them to the
            # "other" fallback colour, which draws exactly what the author hid.
            hidden.extend(resolved.values)
            switched_off += 1
            continue
        symbol = translate_symbol(
            category.symbol(),
            report,
            f"{subject}, class {index + 1}",
            layer_id,
            scale=scale,
            ground=ground,
        )
        has_catch_all = has_catch_all or resolved.catch_all
        categories.append(
            CategorySpec(
                value=resolved.values[0] if resolved.values else None,
                label=category.label(),
                symbol=symbol,
                extra_values=resolved.values[1:],
                is_default=resolved.catch_all,
            )
        )

    if switched_off:
        report.approximated(
            subject,
            f"{switched_off} categor{'y is' if switched_off == 1 else 'ies are'} "
            "switched off in QGIS; matching features are omitted from the export.",
            layer_id,
        )

    categories = [
        replace(category, symbol=symbol)
        for category, symbol in zip(
            categories,
            harmonise_ground_sizes(
                [c.symbol for c in categories], report, subject, layer_id
            ),
        )
    ]

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
        unmatched_hidden=not has_catch_all,
    )


def _translate_graduated(
    renderer: QgsGraduatedSymbolRenderer,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
    scale: MapUnitScale | None = None,
    ground: GroundUnits | None = None,
) -> RendererSpec:
    classes: list[GraduatedClassSpec] = []
    value_ranges: list[ValueRange] = []

    for index, value_range in enumerate(renderer.ranges()):
        value_ranges.append(
            ValueRange(
                lower=float(value_range.lowerValue()),
                upper=float(value_range.upperValue()),
                drawn=bool(value_range.renderState()),
            )
        )
        if not value_range.renderState():
            # Not exported as a class, but still in `value_ranges`: it claims its
            # values, so the features in it are dropped rather than recoloured.
            continue
        symbol = translate_symbol(
            value_range.symbol(),
            report,
            f"{subject}, range {index + 1}",
            layer_id,
            scale=scale,
            ground=ground,
        )
        classes.append(
            GraduatedClassSpec(
                lower=float(value_range.lowerValue()),
                upper=float(value_range.upperValue()),
                label=value_range.label(),
                symbol=symbol,
            )
        )

    classes = [
        replace(klass, symbol=symbol)
        for klass, symbol in zip(
            classes,
            harmonise_ground_sizes(
                [c.symbol for c in classes], report, subject, layer_id
            ),
        )
    ]

    field_name = renderer.classAttribute()
    fallback = _unreferenceable_field(
        field_name, [c.symbol for c in classes], report, subject, layer_id
    )
    if fallback is not None:
        return fallback

    switched_off = sum(1 for r in value_ranges if not r.drawn)
    if switched_off:
        report.approximated(
            subject,
            f"{switched_off} range{' is' if switched_off == 1 else 's are'} "
            "switched off in QGIS; matching features are omitted from the export.",
            layer_id,
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

    # The method in brackets only when there is one to name: "(unknown)" read
    # as a fault, and the approximated row above already says why it is absent.
    method = (
        ""
        if classification is ClassificationMethod.UNKNOWN
        else f" ({classification.value.replace('_', ' ')})"
    )
    report.preserved(
        subject,
        f"Graduated on '{field_name}' with {len(classes)} "
        f"class{'' if len(classes) == 1 else 'es'}{method}.",
        layer_id,
    )
    return RendererSpec(
        kind=RendererKind.GRADUATED,
        field_name=field_name,
        classes=tuple(classes),
        classification=classification,
        ramp_name=_ramp_name(renderer),
        value_ranges=tuple(value_ranges),
    )


def class_symbols(renderer: QgsFeatureRenderer | None) -> list[QgsSymbol]:
    """The live QGIS symbols behind a `RendererSpec`'s classes, in the same order.

    The symbol atlas has to *draw* what the spec describes, and a `SymbolSpec`
    is a flattened summary rather than something drawable - the whole point of
    rasterising is to let QGIS render the original object. So the atlas needs
    both, paired up, and pairing them positionally is only safe if this walk
    applies exactly the same skip rules `_translate_categorized` does. It does:
    a category with `renderState()` false, or one a later category with the same
    value replaces, is dropped in both - as is a range with `renderState()` false.

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
        categories = renderer.categories()
        return [
            category.symbol().clone()
            for category, resolved in zip(categories, _resolve_categories(categories))
            if resolved.used
            and category.renderState()
            and category.symbol() is not None
        ]

    if isinstance(renderer, QgsGraduatedSymbolRenderer):
        return [
            value_range.symbol().clone()
            for value_range in renderer.ranges()
            if value_range.renderState() and value_range.symbol() is not None
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
