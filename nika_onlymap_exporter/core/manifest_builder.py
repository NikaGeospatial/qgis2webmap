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
    PopupFieldMode,
    PopupFieldSpec,
    RendererKind,
    RendererSpec,
    SymbolSpec,
)
from .label_points import (
    LABEL_PROPERTY,
    build_label_collection,
    collect_character_set,
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

# Labels are a second layer, not a property of the first: `GeoJsonLayer` cannot
# draw text, so QGIS labelling becomes a companion `TextLayer` fed by computed
# label points. See `label_points.py` for why it carries its own reduced data.
TEXT_LAYER_CLASS = "TextLayer"

# Fallbacks for a QGIS label whose text format carried no explicit value.
DEFAULT_LABEL_COLOR = Color(r=0, g=0, b=0, a=1.0)
DEFAULT_LABEL_SIZE = 12.0

# The narrowest a line may be drawn, in screen pixels. deck.gl has no separate
# hit width - it picks against the rendered geometry - so this is simultaneously
# the smallest clickable target. Two rather than one: a 1px line at a device
# pixel ratio of 1 is a coin-flip to hit, and the visual difference against a
# QGIS hairline is slight where the usability difference is not.
MIN_LINE_PICK_PIXELS = 2.0

DEFAULT_FILL = Color(r=136, g=136, b=136, a=0.8)
DEFAULT_STROKE = Color(r=51, g=51, b=51, a=1.0)

# The default, and what keeps an export self-contained: no basemap means no
# network request at all, which is the promise the privacy release gate measures.
#
# It is a choice rather than a rule, because a map of a place with no context
# around it is often unreadable. The alternatives are the presets the runtime
# registers; each streams tiles from a third party, so the file does not grow but
# the export stops working offline. `basemap_note` states that in the fidelity
# report rather than leaving the user to discover it.
BASEMAP = "none"

# Presets the runtime accepts, from `onlymapjs.html-data.json`. The MapTiler
# entries are omitted deliberately: they need an API key the exported file would
# have to carry in plain text, where every recipient can read and spend it.
BASEMAP_PRESETS = (
    "none",
    "osm",
    "positron",
    "dark-matter",
    "voyager",
    "liberty",
    "bright",
)

# Where each preset's tiles come from, for the fidelity note. A user deciding
# whether to accept a network dependency should be told who they are depending on.
BASEMAP_HOSTS = {
    "osm": "openstreetmap.org",
    "positron": "carto.com",
    "dark-matter": "carto.com",
    "voyager": "carto.com",
    "liberty": "openfreemap.org",
    "bright": "openfreemap.org",
}


def basemap_note(basemap: str) -> str | None:
    """What choosing this basemap costs the recipient, in plain terms.

    `None` when there is nothing to say, which is the default case.
    """
    if not basemap or basemap == "none":
        return None
    host = BASEMAP_HOSTS.get(basemap, "a third-party tile server")
    return (
        f"The map loads basemap tiles from {host} every time it is opened. "
        "It will not work offline, and each recipient's browser will contact "
        f"{host} directly. The exported file itself is no larger."
    )


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


# White at a third opacity. Deliberately translucent and deliberately *ours*:
# qgis2web reuses `mapSettings.selectionColor()` - the QGIS editing-selection
# colour, opaque yellow by default - as a web hover cue, which fills the map and
# hides everything under it (evaluation 5.7). Overridable on the Appearance tab,
# which qgis2web has never offered: its only workaround is Project Properties.
DEFAULT_HIGHLIGHT = "'#ffffff55'"


def build_layer_element(
    layer: ExportLayer,
    indent: str = "    ",
    compress_data: bool = False,
    highlight_color: Color | None = None,
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

    # `color` is the legend's swatch source. The legend derives its own swatches
    # from a ternary chain or a `scale()`, but a *single* symbol is a bare colour
    # literal it cannot read structure out of, so it fell back to a grey `#999`
    # and every single-symbol layer sat behind the wrong swatch. Emitted only for
    # single symbols: for categorized and graduated layers the derived entries
    # are richer than one colour, and this would override them.
    if renderer.kind is RendererKind.SINGLE:
        swatch = symbol.fill_color or symbol.stroke_color
        if swatch is not None:
            attributes.append(("color", color_literal(swatch).strip("'")))

    if symbol.stroke_width:
        attributes.append(("get-line-width", _number(symbol.stroke_width)))
        # Without this deck.gl treats the width as metres and lines vanish at
        # most zoom levels.
        attributes.append(("line-width-units", "pixels"))
        # A floor on the drawn width, because deck.gl hit-tests against what it
        # drew: a QGIS hairline exports as a sub-pixel line that is technically
        # visible and practically impossible to click, so its popup may as well
        # not exist. Testing found exactly that.
        #
        # It applies to lines only. On a polygon this is the outline, and
        # thickening that would eat into the fill of small features for no
        # benefit - a polygon is picked by its interior.
        if layer.geometry_kind is GeometryKind.LINE:
            attributes.append(("line-width-min-pixels", _number(MIN_LINE_PICK_PIXELS)))

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
        attributes.append(
            (
                "highlight-color",
                DEFAULT_HIGHLIGHT
                if highlight_color is None
                else (color_literal(highlight_color)),
            )
        )

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


def build_label_element(
    layer: ExportLayer, indent: str = "    ", compress_data: bool = False
) -> str:
    """The companion `<om-layer type="TextLayer">` carrying a layer's labels.

    Returns `""` when the layer is not labelled, or when no feature produced a
    usable label point -- an empty TextLayer is pure cost.

    The label layer is deliberately NOT pickable: labels sit on top of their
    features, and a pickable text layer would swallow the clicks meant for the
    geometry underneath, silently breaking popups on exactly the layers a user
    cared enough about to label.
    """
    labeling = layer.labeling
    if not (labeling.enabled and labeling.field_name):
        return ""

    collection = build_label_collection(layer.geojson, labeling.field_name)
    if collection is None:
        return ""

    inner = indent + "  "
    color = labeling.color or DEFAULT_LABEL_COLOR
    size = labeling.font_size or DEFAULT_LABEL_SIZE

    attributes: list[tuple[str, str | None]] = [
        ("id", f"{layer.layer_id}-labels"),
        ("type", TEXT_LAYER_CLASS),
        ("label", f"{layer.name} labels"),
        ("get-text", f"${LABEL_PROPERTY}"),
        ("get-text-color", color_literal(color)),
        ("get-text-size", _number(size)),
        # Without this deck.gl scales text in metres, so labels balloon as the
        # user zooms in and vanish as they zoom out.
        ("text-size-units", "pixels"),
    ]

    if labeling.font_family:
        attributes.append(("text-font-family", labeling.font_family))

    # A QGIS label buffer is a halo; deck.gl calls the same thing an outline.
    if labeling.halo_width and labeling.halo_color:
        attributes.append(("text-outline-color", color_literal(labeling.halo_color)))
        attributes.append(("text-outline-width", _number(labeling.halo_width)))

    # The reader's set is authoritative -- it saw the QGIS field values. The
    # fallback covers label collections built outside a project read.
    character_set = labeling.character_set or collect_character_set(collection)
    if character_set:
        attributes.append(("text-character-set", character_set))

    if not layer.visible:
        attributes.append(("visible", "false"))

    # Labels follow their layer's scale visibility; a label surviving past the
    # geometry it names reads as a rendering bug.
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

    payload = json_for_script(collection)

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


def collect_attributions(project: ExportProject) -> tuple[str, ...]:
    """Every distinct data credit the exported layers carry.

    OnlyMap has no layer-level attribution: its attribution control renders the
    *basemap provider's* credit, and 0.1.0 ships no basemap. So a credit a user
    set in QGIS reaches the recipient only if the artifact renders it itself --
    which is what the template's credit component does with this list.

    Order follows the layers, deduplicated, because that is the draw order the
    user arranged and any other order would look arbitrary.
    """
    return tuple(
        dict.fromkeys(
            layer.attribution.strip()
            for layer in project.exportable_layers
            if layer.attribution and layer.attribution.strip()
        )
    )


# Modes whose row shows even when the feature's value is empty. The two absent
# from this set - `INLINE_WITH_DATA` and `HEADER_WITH_DATA` - are the ones the
# stylesheet hides; see `_popup_row`.
_ALWAYS_MODES = frozenset(
    {
        PopupFieldMode.NO_LABEL,
        PopupFieldMode.INLINE_ALWAYS,
        PopupFieldMode.HEADER_ALWAYS,
    }
)

# Label above the value rather than beside it.
_HEADER_MODES = frozenset(
    {PopupFieldMode.HEADER_ALWAYS, PopupFieldMode.HEADER_WITH_DATA}
)

# **This has to travel inside the overlay, not in the document's stylesheet.**
# `om-overlay` clones its children into a shadow root and renders the popup
# there, and a shadow root does not see document rules - so popup CSS written in
# `map.html` never applies to anything. A `<style>` among those children lands in
# the shadow root with them, which is the only way to reach the rows.
#
# Selectors can stay this plain precisely because of that boundary: nothing else
# in the document can match them, and nothing here can leak out.
POPUP_STYLES = """.om-popup {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  line-height: 1.5;
  max-width: 22rem;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  color: #111827;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
}
.om-popup-title {
  font-weight: 600;
  margin-bottom: 0.35rem;
}
.om-popup-row {
  display: flex;
  gap: 0.75rem;
  justify-content: space-between;
}
.om-popup-label {
  color: #6b7280;
}
.om-popup-value {
  font-variant-numeric: tabular-nums;
}
/* "Label above value": the name gets its own line. */
.om-popup-row-header {
  display: block;
}
.om-popup-row-header .om-popup-label {
  display: block;
  font-weight: 600;
}
/* "Only if it has data" rows hide themselves when this feature has no value.
   That is a per-feature question and this markup is one template shared by every
   feature, so it cannot be settled when the file is written - the runtime
   substitutes an empty string for the field and this rule does the rest. Rows
   that must always show carry .om-popup-always. Without :has() the row shows
   empty instead, which is the behaviour these modes replaced. */
.om-popup-row:not(.om-popup-always):has(.om-popup-value:empty) {
  display: none;
}"""


def _popup_style_element(indent: str) -> list[str]:
    return [
        f"{indent}  <style>",
        *(f"{indent}    {line}" if line else "" for line in POPUP_STYLES.splitlines()),
        f"{indent}  </style>",
    ]


def _popup_row(field: PopupFieldSpec, indent: str) -> str:
    """One attribute's row, its mode expressed entirely in class names.

    **The `*_WITH_DATA` modes are resolved by CSS, not here.** Whether a value is
    empty is a per-feature fact, and the popup is one template interpolated for
    whichever feature was clicked - so this function cannot know. The runtime's
    interpolator is a flat `{{name}}` substitution with no conditional syntax,
    which leaves the value span empty when the value is null. `map.html` hides
    those rows with `:has(.om-popup-value:empty)`.

    `INLINE_WITH_DATA` is the default and deliberately emits no extra class, so
    a default export's markup is byte-identical to what it was before modes
    existed.
    """
    classes = ["om-popup-row"]
    if field.mode in _HEADER_MODES:
        classes.append("om-popup-row-header")
    if field.mode in _ALWAYS_MODES:
        classes.append("om-popup-always")

    label = (
        ""
        if field.mode is PopupFieldMode.NO_LABEL
        else f'<span class="om-popup-label">{escape_attr(field.display_name)}</span>'
    )
    return (
        f'{indent}    <div class="{" ".join(classes)}">'
        f"{label}"
        f'<span class="om-popup-value">{{{{{field.name}}}}}</span></div>'
    )


def build_popup_elements(
    layer: ExportLayer, indent: str = "    ", hover: bool | None = None
) -> str:
    """An `<om-overlay>` plus the `<om-behavior>` that opens it.

    Field labels are emitted because they are the point: qgis2web defaults to no
    label and produces popups of bare values that read as broken. Authors who
    want that can still choose `NO_LABEL` per field.
    """
    if not (layer.popup.enabled and layer.popup.visible_fields):
        return ""

    overlay_id = f"{layer.layer_id}-popup"
    rows = [_popup_row(field, indent) for field in layer.popup.visible_fields]

    return "\n".join(
        [
            f'{indent}<om-overlay id="{escape_attr(overlay_id)}" '
            f'anchor-from="selection" visible="false">',
            *_popup_style_element(indent),
            f'{indent}  <div class="om-popup">',
            f'{indent}    <div class="om-popup-title">{escape_attr(layer.name)}</div>',
            *rows,
            f"{indent}  </div>",
            f"{indent}</om-overlay>",
            _popup_behavior(
                layer,
                overlay_id,
                layer.popup.on_hover if hover is None else hover,
                indent,
            ),
        ]
    )


def _popup_behavior(
    layer: ExportLayer, overlay_id: str, hover: bool, indent: str
) -> str:
    """What opens the popup.

    Hover and click are alternatives, not additions: bound together the popup
    opens under the cursor and then a click "does nothing", because the overlay
    is already open. qgis2web offers hover as a plain toggle for the same
    reason.
    """
    trigger = "hover" if hover else "click"
    return (
        f'{indent}<om-behavior on="{trigger}" '
        f'layer="{escape_attr(layer.layer_id)}" '
        f'action="show-overlay" target="{escape_attr(overlay_id)}"></om-behavior>'
    )


# **Corner aliases, not the logical slot names.** The schema documents eight
# logical positions (`top-start`, `top-end`, ...) plus four legacy corner
# aliases, but the shipped runtime implements only the corners: it looks the
# attribute up in a four-entry table and falls back to `top-left` on a miss. A
# logical name therefore does not place a widget somewhere sensible - it places
# every widget in the same corner, stacked on top of each other, which is what
# an export looked like before this was found.
#
# `tests/unit/test_manifest_builder.py` pins these values for that reason. It is
# the one place we assert on an attribute's *value* rather than its name.
WIDGET_POSITIONS = {
    "legend": "top-right",
    "layer-switcher": "top-left",
    "zoom-controls": "bottom-left",
    "scale-bar": "bottom-left",
}


def build_widget_elements(project: ExportProject, indent: str = "    ") -> str:
    """Widgets, on by default.

    qgis2web defaults seven Appearance options to "None", so a default export is
    a bare map with zoom controls and nothing else - which makes the tool look
    far more primitive than it is. Ours ship useful chrome unless asked not to.
    """
    settings = project.settings
    widgets: list[str] = []

    if settings.show_legend:
        # The legend carries the map title only when nothing else is showing it.
        # With the caption on - now the default - the title appeared twice on
        # one screen, once as the caption and once as the legend's heading.
        legend_title = "" if settings.show_title else project.title
        title_attribute = (
            f'title="{escape_attr(legend_title)}" ' if legend_title else ""
        )
        widgets.append(
            f'{indent}<om-widget type="legend" {title_attribute}'
            f'position="{WIDGET_POSITIONS["legend"]}"></om-widget>'
        )
    if settings.show_layer_switcher and len(project.layers) > 1:
        widgets.append(
            f'{indent}<om-widget type="layer-switcher" '
            f'position="{WIDGET_POSITIONS["layer-switcher"]}"></om-widget>'
        )
    if settings.show_zoom_controls:
        widgets.append(
            f'{indent}<om-widget type="zoom-controls" '
            f'position="{WIDGET_POSITIONS["zoom-controls"]}"></om-widget>'
        )
    if settings.show_scale_bar:
        widgets.append(
            f'{indent}<om-widget type="scale-bar" '
            f'position="{WIDGET_POSITIONS["scale-bar"]}"></om-widget>'
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
        # Falls back to "none" rather than trusting the value: an unknown preset
        # would make the runtime request a style it cannot resolve, and a broken
        # basemap is worse than the absent one the user could have had.
        (
            "basemap",
            project.settings.basemap
            if project.settings.basemap in BASEMAP_PRESETS
            else BASEMAP,
        ),
        # No tracking. With `basemap="none"` there are no network requests at
        # all; a chosen basemap adds tile requests and nothing else.
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
        sections.append(
            build_layer_element(
                layer,
                inner,
                compress_data=compress_data,
                highlight_color=layer.highlight_color,
            )
        )
        popup = build_popup_elements(layer, inner)
        if popup:
            sections.append(popup)

    # Every label layer after every geometry layer, not interleaved: OnlyMap
    # stacks children in document order, so labels emitted next to their own
    # layer would be painted over by whatever draws above it.
    for layer in project.exportable_layers:
        labels = build_label_element(layer, inner, compress_data=compress_data)
        if labels:
            sections.append(labels)

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
