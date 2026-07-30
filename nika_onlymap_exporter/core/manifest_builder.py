"""The normalized model to OnlyMap manifest markup.

Pure Python - no PyQGIS, no Qt - so the entire writer is unit-tested in CI. That
is possible only because `export_ir` is pure too; keep it that way.

**Markup, never code.** Every styling decision becomes an *attribute*. qgis2web
generates JavaScript, twice, in two renderer-specific families totalling ~7,500
lines that have measurably diverged. There is no equivalent here because there is
nothing to generate.

**Write the canonical expression shapes.** OnlyMap's legend widget parses
`get-fill-color` and recognises three forms:

* an equality ternary chain -> a category palette with an "other" row;
* a `threshold` scale -> discrete class ranges (`< b1`, `b1 - b2`, `>= bN`);
* a `sequential`/`diverging` scale -> a gradient ramp.

Anything else falls back to a single swatch. So QGIS categorized maps to the
ternary chain and QGIS graduated to the threshold scale, and the legend comes out
correct without us building one. Deviating from these shapes silently costs the
legend.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from .export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    GeometryKind,
    RendererKind,
    RendererSpec,
    SymbolSpec,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .license_policy import CapVerdict

# Web Mercator scale denominator at zoom 0, 96 dpi, at the equator. Converting
# QGIS scale-visibility into zoom levels needs a reference point and this is the
# conventional one.
SCALE_DENOMINATOR_AT_ZOOM_0 = 559_082_264.0
MAX_WEB_ZOOM = 24

# One deck.gl layer class covers points, lines and polygons, including mixed
# geometry in one file. Using a different class per geometry kind would fragment
# the styling attributes for no benefit.
LAYER_CLASS = "GeoJsonLayer"

DEFAULT_FILL = Color(r=136, g=136, b=136, a=0.8)
DEFAULT_STROKE = Color(r=51, g=51, b=51, a=1.0)

# Deliberate: no basemap in 0.1.0, so an export makes no network request at all.
BASEMAP = "none"


def escape_attr(value: str) -> str:
    """Escape a value for an HTML attribute delimited by double quotes.

    Single quotes are left alone deliberately. `html.escape(quote=True)` turns
    them into `&#x27;`, which is functionally fine - browsers decode entities
    when reading an attribute - but the styling accessors are full of single
    quotes, and `$kind == &#x27;civil&#x27;` is far harder for a person or an AI
    assistant to read and edit than `$kind == 'civil'`. Since the attribute is
    delimited by double quotes, a single quote needs no escaping.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def json_for_script(data: object) -> str:
    """Serialise JSON safely for embedding inside a `<script>` element.

    `json.dumps` leaves `<` literal, so a feature attribute containing
    `</script>` closes the block early: the map breaks, and anything after the
    attribute becomes live markup. QGIS attributes hold arbitrary text - a
    description field, a scraped note - so this is reachable with ordinary data,
    not just malice.

    Escaping as `\u003c` keeps the JSON valid and decodes back to the original
    string. U+2028 and U+2029 are escaped too: they are valid in JSON strings but
    are line terminators in JavaScript source.
    """
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return (
        payload.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def color_literal(color: Color | None) -> str:
    """A colour as an expression-language literal.

    Quoted hex rather than an RGBA array: it is what the legend parser expects in
    the canonical shapes, and it reads far better in an artifact a person or an
    agent may later edit by hand.
    """
    if color is None:
        return "'#888888'"
    if color.a >= 1.0:
        return f"'#{color.r:02x}{color.g:02x}{color.b:02x}'"
    alpha = max(0, min(255, round(color.a * 255)))
    return f"'#{color.r:02x}{color.g:02x}{color.b:02x}{alpha:02x}'"


def value_literal(value: object) -> str:
    """A category value as an expression-language literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def scale_to_zoom(scale_denominator: float) -> float:
    """QGIS scale denominator to web zoom level.

    QGIS thinks in denominators where *larger* means more zoomed out; web maps
    think in zoom levels where larger means more zoomed in. So the relationship
    is inverted as well as logarithmic, which is exactly the kind of conversion
    that gets silently reversed - hence its own function and its own test.
    """
    if scale_denominator <= 0:
        return 0.0
    zoom = math.log2(SCALE_DENOMINATOR_AT_ZOOM_0 / scale_denominator)
    return max(0.0, min(float(MAX_WEB_ZOOM), zoom))


# --------------------------------------------------------------------------
# Styling expressions
# --------------------------------------------------------------------------


def fill_expression(renderer: RendererSpec, geometry: GeometryKind) -> str:
    """Build `get-fill-color` in whichever canonical shape the legend understands."""
    if renderer.kind is RendererKind.CATEGORIZED and renderer.categories:
        return _categorized_expression(renderer)
    if renderer.kind is RendererKind.GRADUATED and renderer.classes:
        return _graduated_expression(renderer)

    symbol = renderer.representative_symbol or SymbolSpec()
    fill = symbol.fill_color or (
        symbol.stroke_color if geometry is GeometryKind.LINE else DEFAULT_FILL
    )
    return color_literal(fill)


def _categorized_expression(renderer: RendererSpec) -> str:
    """`$field == 'a' ? '#c1' : $field == 'b' ? '#c2' : '#fallback'`

    The trailing fallback is not optional: it is what the legend renders as the
    "other" row, and without it features outside every class would be undrawn
    with no explanation.
    """
    field = renderer.field_name or ""
    parts = [
        f"${field} == {value_literal(category.value)} "
        f"? {color_literal(category.symbol.fill_color)}"
        for category in renderer.categories
    ]
    return " : ".join(parts) + f" : {color_literal(DEFAULT_FILL)}"


def _graduated_expression(renderer: RendererSpec) -> str:
    """`scale($field, threshold, [colours], domain=[breaks])`

    A threshold scale keeps QGIS's exact class breaks. A `sequential` scale would
    interpolate between the endpoint colours instead, producing a smooth ramp
    where QGIS drew discrete classes - a map that looks different from the one
    the author designed.

    A d3 threshold scale takes N colours and N-1 interior breaks, so the breaks
    are the upper bound of every class except the last.
    """
    field = renderer.field_name or ""
    colors = [color_literal(c.symbol.fill_color) for c in renderer.classes]
    breaks = [c.upper for c in renderer.classes[:-1]]

    colors_literal = "[" + ", ".join(colors) + "]"
    domain_literal = "[" + ", ".join(_number(b) for b in breaks) + "]"
    return f"scale(${field}, threshold, {colors_literal}, domain={domain_literal})"


def _number(value: float) -> str:
    """Render a number without a pointless trailing `.0`."""
    if value == int(value):
        return str(int(value))
    return repr(round(value, 6))


def line_color_expression(renderer: RendererSpec, geometry: GeometryKind) -> str:
    if geometry is GeometryKind.LINE:
        return fill_expression(renderer, geometry)
    symbol = renderer.representative_symbol or SymbolSpec()
    return color_literal(symbol.stroke_color or DEFAULT_STROKE)


# --------------------------------------------------------------------------
# Element construction
# --------------------------------------------------------------------------


def _attrs_to_string(attributes: list[tuple[str, str | None]], indent: str) -> str:
    rendered = [
        f'{indent}{name}="{escape_attr(value)}"'
        for name, value in attributes
        if value is not None
    ]
    return "\n".join(rendered)


def build_layer_element(
    layer: ExportLayer, indent: str = "    ", compress_data: bool = False
) -> str:
    """One `<om-layer>`, with its data inline as a direct child.

    Inline JSON is what makes a single-file artifact possible: `file://` blocks
    `fetch` of sibling files, so a `data="./x.geojson"` URL cannot work from
    disk. The JSON must be a *direct* child of the layer element.

    With `compress_data`, the block is gzipped base64 under a distinct script
    type, which the artifact's bootstrap converts back to JSON before the runtime
    defines the custom elements. Off by default: readable data is what lets a
    person or an agent edit the map afterwards.
    """
    renderer = layer.renderer
    # Not `renderer.symbol`: categorized and graduated renderers have none, and
    # reading it directly silently drops stroke width and marker radius.
    symbol = renderer.representative_symbol or SymbolSpec()
    inner = indent + "  "

    attributes: list[tuple[str, str | None]] = [
        ("id", layer.layer_id),
        ("type", LAYER_CLASS),
        ("label", layer.name),
        ("get-fill-color", fill_expression(renderer, layer.geometry_kind)),
        ("get-line-color", line_color_expression(renderer, layer.geometry_kind)),
    ]

    if symbol.stroke_width:
        attributes.append(("get-line-width", _number(symbol.stroke_width)))
        # Without this deck.gl treats the width as metres and lines vanish at
        # most zoom levels.
        attributes.append(("line-width-units", "pixels"))

    if layer.geometry_kind is GeometryKind.POINT and symbol.radius:
        attributes.append(("get-point-radius", _number(symbol.radius)))
        attributes.append(("point-radius-units", "pixels"))

    if layer.opacity < 1.0:
        attributes.append(("opacity", _number(layer.opacity)))

    if not layer.visible:
        attributes.append(("visible", "false"))

    # QGIS scale visibility inverts on the way to zoom levels: the most
    # zoomed-out scale becomes the minimum zoom.
    if layer.scale_range.is_set:
        if layer.scale_range.min_scale:
            attributes.append(
                (
                    "visible-min-zoom",
                    _number(scale_to_zoom(layer.scale_range.min_scale)),
                )
            )
        if layer.scale_range.max_scale:
            attributes.append(
                (
                    "visible-max-zoom",
                    _number(scale_to_zoom(layer.scale_range.max_scale)),
                )
            )

    if layer.popup.enabled and layer.popup.visible_fields:
        attributes.append(("pickable", "true"))
        # Hover feedback stays in z-order and is explicitly coloured, unlike the
        # incumbent's opaque overlay drawn above every layer.
        attributes.append(("auto-highlight", "true"))
        attributes.append(("highlight-color", "'#ffffff55'"))

    payload = json_for_script(layer.geojson)

    lines = [f"{indent}<om-layer"]
    lines.append(_attrs_to_string(attributes, inner))
    lines.append(f"{indent}>")
    if compress_data:
        from ..packaging.asset_embedder import GZIP_SCRIPT_TYPE, gzip_base64

        lines.append(f'{inner}<script type="{GZIP_SCRIPT_TYPE}">')
        lines.append(gzip_base64(payload.encode("utf-8")))
    else:
        lines.append(f'{inner}<script type="application/json">')
        lines.append(payload)
    lines.append(f"{inner}</script>")
    lines.append(f"{indent}</om-layer>")
    return "\n".join(lines)


def build_popup_elements(layer: ExportLayer, indent: str = "    ") -> str:
    """An `<om-overlay>` plus the `<om-behavior>` that opens it on click.

    Field labels are emitted because they are the point: qgis2web defaults to no
    label and produces popups of bare values that read as broken.
    """
    if not (layer.popup.enabled and layer.popup.visible_fields):
        return ""

    overlay_id = f"{layer.layer_id}-popup"
    rows = [
        f'{indent}    <div class="om-popup-row">'
        f'<span class="om-popup-label">{escape_attr(field.display_name)}</span>'
        f'<span class="om-popup-value">{{{{{field.name}}}}}</span></div>'
        for field in layer.popup.visible_fields
    ]

    return "\n".join(
        [
            f'{indent}<om-overlay id="{escape_attr(overlay_id)}" '
            f'anchor-from="selection" visible="false">',
            f'{indent}  <div class="om-popup">',
            f'{indent}    <div class="om-popup-title">{escape_attr(layer.name)}</div>',
            *rows,
            f"{indent}  </div>",
            f"{indent}</om-overlay>",
            f'{indent}<om-behavior on="click" layer="{escape_attr(layer.layer_id)}" '
            f'action="show-overlay" target="{escape_attr(overlay_id)}"></om-behavior>',
        ]
    )


def build_widget_elements(project: ExportProject, indent: str = "    ") -> str:
    """Widgets, on by default.

    qgis2web defaults seven Appearance options to "None", so a default export is
    a bare map with zoom controls and nothing else - which makes the tool look
    far more primitive than it is. Ours ship useful chrome unless asked not to.
    """
    settings = project.settings
    widgets: list[str] = []

    if settings.show_legend:
        widgets.append(
            f'{indent}<om-widget type="legend" title="{escape_attr(project.title)}" '
            f'position="top-end"></om-widget>'
        )
    if settings.show_layer_switcher and len(project.layers) > 1:
        widgets.append(
            f'{indent}<om-widget type="layer-switcher" '
            'position="top-start"></om-widget>'
        )
    if settings.show_zoom_controls:
        widgets.append(
            f'{indent}<om-widget type="zoom-controls" position="bottom-start">'
            "</om-widget>"
        )
    if settings.show_scale_bar:
        widgets.append(
            f'{indent}<om-widget type="scale-bar" position="bottom-start"></om-widget>'
        )

    return "\n".join(widgets)


def build_fallback_element(project: ExportProject, indent: str = "    ") -> str:
    """What shows where scripts never run.

    Mail previews and iOS QuickLook render HTML attachments with JavaScript
    disabled. Without this the recipient sees a blank frame and concludes the
    file is broken - the incumbent's single worst sharing failure.
    """
    return "\n".join(
        [
            f"{indent}<om-fallback>",
            f'{indent}  <div class="om-fallback">',
            f"{indent}    <h1>{escape_attr(project.title)}</h1>",
            f"{indent}    <p>This interactive map needs JavaScript. Open this file "
            "in a web browser to view it.</p>",
            f"{indent}  </div>",
            f"{indent}</om-fallback>",
        ]
    )


def build_manifest(
    project: ExportProject,
    cap_verdict: CapVerdict | None = None,
    indent: str = "  ",
    compress_data: bool = False,
) -> str:
    """The complete `<om-map>` element for a project.

    Layers are emitted in the model's order, which is bottom-first - OnlyMap
    stacks `<om-layer>` children in document order, so the first child draws
    first. The model already reversed QGIS's top-first tree.
    """
    inner = indent + "  "
    center_lon, center_lat = (0.0, 0.0)
    zoom = 2.0

    if project.extent is not None:
        center_lon, center_lat = project.extent.center
        zoom = _zoom_for_extent(project.extent.width_degrees)

    attributes: list[tuple[str, str | None]] = [
        ("center", f"[{center_lon:.6f}, {center_lat:.6f}]"),
        ("zoom", _number(round(zoom, 2))),
        ("basemap", BASEMAP),
        # No tracking, no network requests. Stated in the README as a promise.
        ("telemetry", "off"),
    ]

    if cap_verdict is not None and cap_verdict.license_key:
        attributes.append(("license-key", cap_verdict.license_key))

    # Only when something will actually be missing: `validate` mounts the
    # runtime's error panel, which explains the gap to the recipient. A clean
    # export carries no diagnostic badge.
    if cap_verdict is not None and cap_verdict.needs_runtime_validation:
        attributes.append(("validate", ""))

    sections = [
        f"{indent}<om-map",
        _attrs_to_string(attributes, inner),
        f"{indent}>",
    ]

    for layer in project.exportable_layers:
        sections.append(build_layer_element(layer, inner, compress_data=compress_data))
        popup = build_popup_elements(layer, inner)
        if popup:
            sections.append(popup)

    widgets = build_widget_elements(project, inner)
    if widgets:
        sections.append(widgets)

    sections.append(build_fallback_element(project, inner))
    sections.append(f"{indent}</om-map>")
    return "\n".join(sections)


def _zoom_for_extent(width_degrees: float) -> float:
    """A starting zoom that fits the data's longitude span.

    Approximate on purpose: the runtime settles the camera precisely once it has
    measured the viewport. This only has to avoid opening somewhere useless.
    """
    if width_degrees <= 0:
        return 12.0
    zoom = math.log2(360.0 / width_degrees)
    return max(0.0, min(float(MAX_WEB_ZOOM), zoom))
