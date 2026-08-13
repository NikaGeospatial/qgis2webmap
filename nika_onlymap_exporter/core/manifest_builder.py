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
    ElevationSpec,
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
    # positron moved with the vector styles: the pinned runtime resolves it
    # from tiles.openfreemap.org, not carto.com. Verified against 0.6.1 -
    # naming the wrong third party here makes the fidelity report's privacy
    # statement false.
    "positron": "openfreemap.org",
    "dark-matter": "carto.com",
    "voyager": "carto.com",
    "liberty": "openfreemap.org",
    "bright": "openfreemap.org",
}


# Relief presets the runtime registers, from `src/terrain.ts`. Only the keyless
# one: `maptiler-terrain` needs an API key the exported file would carry in plain
# text, which is the same reason the MapTiler basemaps are absent above.
TERRAIN_PRESETS = ("none", "terrarium")

TERRAIN_HOSTS = {"terrarium": "s3.amazonaws.com"}

# The raster twin of each basemap preset, draped over the relief surface as
# `terrain-texture`. The runtime replaces the basemap while terrain is on, so
# without this the mountains render as a plain grey shell. liberty and bright
# are deliberately absent: openfreemap serves vector styles only, and there is
# no raster edition to drape.
RASTER_TEXTURE_TEMPLATES = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "positron": "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    "dark-matter": "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    "voyager": "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
}

# Who serves those textures - for the fidelity note. Positron's raster twin
# comes from carto.com even though its flat vector style is openfreemap.org,
# so relief genuinely adds a third party the flat map never contacted.
TEXTURE_HOSTS = {
    "osm": "openstreetmap.org",
    "positron": "carto.com",
    "dark-matter": "carto.com",
    "voyager": "carto.com",
}

# Looking straight down, an extruded map and a flat one are the same picture. So
# when anything stands up - the user's own buildings, or a relief surface - the
# map has to open at an angle or the feature is invisible and reads as broken.
# 45 degrees rather than something steeper: it shows the height clearly while
# still letting the reader see the ground plan they recognise from QGIS.
EXTRUDED_PITCH = 45.0


def terrain_note(terrain: str, basemap: str = "none") -> str | None:
    """What switching relief on costs the recipient.

    Deliberately parallel to `basemap_note`: the cost is the same shape - the
    file does not grow, but every recipient's browser fetches tiles from a third
    party - and a user should not have to learn it twice. Relief names more
    hosts than a flat basemap: the DEM tiles, the imagery draped over them,
    and unpkg.com, which the runtime's terrain-mesh decoder loads its worker
    scripts from (measured, not assumed).
    """
    host = TERRAIN_HOSTS.get(terrain)
    if host is None:
        return None
    texture_host = (
        TEXTURE_HOSTS.get(basemap) if basemap in RASTER_TEXTURE_TEMPLATES else None
    )
    if texture_host is not None:
        sources = f"{host} (elevation), {texture_host} (imagery) and unpkg.com"
    else:
        sources = f"{host} (elevation) and unpkg.com"
    note = (
        f"Terrain streams tiles from {sources} as the map is used. The file "
        "does not get any bigger, but the map needs a connection to show "
        "relief, and it opens tilted so the relief is visible."
    )
    if texture_host is None and basemap != "none":
        note += (
            " This basemap has no raster imagery to drape, so the relief "
            "surface renders plain grey - choose OpenStreetMap, Positron, "
            "Dark Matter or Voyager to see the map on the mountains."
        )
    return note


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


def color_hex(color: Color | None) -> str:
    """A colour as a bare `#rrggbb[aa]`, for a plain-valued attribute.

    `color_literal` quotes, because it produces a term for the expression
    language that `get-*` accessors are written in. A scalar attribute is not
    expression-valued, and the runtime hands its raw string to deck.gl untouched:
    quoted, `highlight-color` arrives as the literal 12-character string
    `'#ffffff55'`, deck's `Array.isArray` guard rejects it, and every highlight
    silently renders in deck's default navy. Verified in a browser against
    0.5.12 - bare hex and an RGBA array both resolve, the quoted form does not.
    """
    if color is None:
        return "#888888"
    if color.a >= 1.0:
        return f"#{color.r:02x}{color.g:02x}{color.b:02x}"
    alpha = max(0, min(255, round(color.a * 255)))
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}{alpha:02x}"


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
        return _categorized_expression(renderer, geometry)
    if renderer.kind is RendererKind.GRADUATED and renderer.classes:
        return _graduated_expression(renderer, geometry)

    symbol = renderer.representative_symbol or SymbolSpec()
    fill = symbol.fill_color or (
        symbol.stroke_color if geometry is GeometryKind.LINE else DEFAULT_FILL
    )
    return color_literal(fill)


def _class_color(symbol: SymbolSpec, geometry: GeometryKind) -> Color | None:
    """The colour a class symbol actually draws with.

    A line symbol's colour lives in `stroke_color` and its `fill_color` is
    `None` - the same split the single-symbol path already handles. Reading
    only the fill here exported every categorized or graduated line layer as
    the '#888888' placeholder.
    """
    if geometry is GeometryKind.LINE:
        return symbol.fill_color or symbol.stroke_color
    return symbol.fill_color


def _categorized_expression(renderer: RendererSpec, geometry: GeometryKind) -> str:
    """`$field == 'a' ? '#c1' : $field == 'b' ? '#c2' : '#fallback'`

    The trailing fallback is not optional: it is what the legend renders as the
    "other" row, and without it features outside every class would be undrawn
    with no explanation.
    """
    field = renderer.field_name or ""
    parts = [
        f"${field} == {value_literal(category.value)} "
        f"? {color_literal(_class_color(category.symbol, geometry))}"
        for category in renderer.categories
    ]
    return " : ".join(parts) + f" : {color_literal(DEFAULT_FILL)}"


def _graduated_expression(renderer: RendererSpec, geometry: GeometryKind) -> str:
    """`scale($field, threshold, [colours], domain=[breaks])`

    A threshold scale keeps QGIS's exact class breaks. A `sequential` scale would
    interpolate between the endpoint colours instead, producing a smooth ramp
    where QGIS drew discrete classes - a map that looks different from the one
    the author designed.

    A d3 threshold scale takes N colours and N-1 interior breaks, so the breaks
    are the upper bound of every class except the last.
    """
    field = renderer.field_name or ""
    colors = [color_literal(_class_color(c.symbol, geometry)) for c in renderer.classes]
    breaks = [c.upper for c in renderer.classes[:-1]]

    colors_literal = "[" + ", ".join(colors) + "]"
    domain_literal = "[" + ", ".join(_number(b) for b in breaks) + "]"
    return f"scale(${field}, threshold, {colors_literal}, domain={domain_literal})"


def _number(value: float) -> str:
    """Render a number without a pointless trailing `.0`."""
    if value == int(value):
        return str(int(value))
    return repr(round(value, 6))


def _class_symbols(renderer: RendererSpec) -> list[SymbolSpec]:
    """The per-class symbols, in the order their expression must list them."""
    if renderer.kind is RendererKind.CATEGORIZED:
        return [category.symbol for category in renderer.categories]
    if renderer.kind is RendererKind.GRADUATED:
        return [klass.symbol for klass in renderer.classes]
    return []


def numeric_expression(
    renderer: RendererSpec,
    attribute: str,
    fallback: float | None = None,
) -> str | None:
    """A per-class numeric accessor (`radius`, `stroke_width`), or `None`.

    Mirrors the colour expressions exactly - a ternary chain for categorized, a
    threshold scale for graduated - because sizes classify the same way colours
    do, and QGIS's class breaks must survive both.

    A **threshold** scale rather than `sqrt`/`log`/`pow`: a QGIS graduated
    renderer assigns each class one discrete size, so interpolating between them
    would draw sizes the author never chose. The continuous scales belong to
    QGIS's data-defined size *assistant*, which is a different feature and needs
    the data-defined override reader we do not have yet.

    Returns `None` when every class agrees, or when any class is missing the
    value: one constant is cheaper, reads better in the artifact, and an
    expression evaluating to the same number for every feature is pure noise.
    """
    symbols = _class_symbols(renderer)
    if len(symbols) < 2:
        return None

    values = [getattr(symbol, attribute, None) for symbol in symbols]
    if any(value is None for value in values):
        return None
    numbers = [float(value) for value in values if value is not None]
    if len(set(numbers)) < 2:
        return None

    field = renderer.field_name or ""
    if renderer.kind is RendererKind.CATEGORIZED:
        default = fallback if fallback is not None else numbers[-1]
        parts = [
            f"${field} == {value_literal(category.value)} ? {_number(number)}"
            for category, number in zip(renderer.categories, numbers)
        ]
        return " : ".join(parts) + f" : {_number(default)}"

    breaks = [klass.upper for klass in renderer.classes[:-1]]
    sizes_literal = "[" + ", ".join(_number(n) for n in numbers) + "]"
    domain_literal = "[" + ", ".join(_number(b) for b in breaks) + "]"
    return f"scale(${field}, threshold, {sizes_literal}, domain={domain_literal})"


def icon_expression(renderer: RendererSpec) -> str | None:
    """`get-icon`: which cell of the sprite sheet each feature draws.

    The same three canonical shapes the colour expressions use - a literal for a
    single symbol, an equality ternary chain for categorized, a `threshold`
    scale for graduated - so the accessor reads the same way whether it carries
    a colour or an icon name, and so a reader editing the artifact by hand meets
    one pattern rather than three.

    `None` when any class is missing its icon, which means the atlas was not
    built and the caller must emit nothing.
    """
    symbols = _class_symbols(renderer) or (
        [renderer.symbol] if renderer.symbol is not None else []
    )
    names = [symbol.icon_name for symbol in symbols if symbol is not None]
    if not names or any(name is None for name in names):
        return None

    if renderer.kind is RendererKind.CATEGORIZED:
        field = renderer.field_name or ""
        parts = [
            f"${field} == {value_literal(category.value)} "
            f"? {value_literal(category.symbol.icon_name)}"
            for category in renderer.categories
        ]
        # The trailing fallback is the first class's icon rather than a blank:
        # deck.gl draws nothing at all for an icon name it cannot resolve, so a
        # feature outside every class would silently vanish from the map.
        return " : ".join(parts) + f" : {value_literal(names[0])}"

    if renderer.kind is RendererKind.GRADUATED:
        field = renderer.field_name or ""
        icons = "[" + ", ".join(value_literal(name) for name in names) + "]"
        breaks = "[" + ", ".join(_number(k.upper) for k in renderer.classes[:-1]) + "]"
        return f"scale(${field}, threshold, {icons}, domain={breaks})"

    return value_literal(names[0])


def elevation_expression(elevation: ElevationSpec) -> str | None:
    """`get-elevation` for a raised layer, or `None` if it is not raised.

    Metres straight through, with no `MM_TO_PIXELS` in sight: a QGIS extrusion
    height is a real-world distance, unlike a symbol size, and deck.gl's
    `getElevation` is metres too.
    """
    if not elevation.is_set:
        return None
    if elevation.height_field:
        return f"${elevation.height_field}"
    return _number(elevation.height or 0.0)


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
# Bare, not quoted: `highlight-color` is a plain attribute, not an expression.
# See `color_hex` for what quoting it cost.
DEFAULT_HIGHLIGHT = "#ffffff55"


def dash_attribute(symbol: SymbolSpec) -> str | None:
    """A `dash="[on, off]"` value for a dashed line, or `None` for a solid one.

    Two conversions happen here rather than in the reader, because both are
    facts about this renderer rather than about QGIS:

    * **Units.** The model carries the pattern in pixels; deck.gl's
      `dashArray` is in *line-width units*, so a 2px line dashed 8px-on becomes
      `4`. Emitting pixels would make every dash scale wrongly with width - and
      silently, since both are plain numbers.
    * **Length.** deck.gl takes exactly one on/off pair. QGIS dash-dot patterns
      have four or six entries, so anything longer is cut to its first pair,
      which keeps the line dashed at the right rhythm and loses the dots.

    Returns `None` rather than a solid-looking pattern when the numbers cannot
    produce a visible dash: the runtime rejects a zero dash length outright.
    """
    if not symbol.stroke_dash or symbol.stroke_width <= 0:
        return None

    units = [value / symbol.stroke_width for value in symbol.stroke_dash[:2]]
    if len(units) < 2 or units[0] <= 0:
        return None

    on, off = round(units[0], 3), round(max(units[1], 0.0), 3)
    return f"[{_number(on)}, {_number(off)}]"


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
        # A per-class width when the classes disagree, the representative
        # symbol's constant when they do not.
        width = numeric_expression(
            renderer, "stroke_width", fallback=symbol.stroke_width
        )
        attributes.append(("get-line-width", width or _number(symbol.stroke_width)))
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
            # deck.gl defaults both to false, and so does a default QGIS line
            # (square cap, bevel join), so these are emitted only for lines the
            # user deliberately rounded.
            if symbol.cap_rounded:
                attributes.append(("line-cap-rounded", "true"))
            if symbol.join_rounded:
                attributes.append(("line-joint-rounded", "true"))

        dash = dash_attribute(symbol)
        if dash is not None:
            attributes.append(("dash", dash))

    # Markers QGIS had to draw itself. **Still a `GeoJsonLayer`** - deck.gl's
    # `pointType="icon"` swaps the internal circle sublayer for an icon one,
    # leaving geometry handling, picking, popups and draw order exactly as they
    # were. Switching the whole layer to an `IconLayer` was the obvious-looking
    # alternative and is much worse: an `IconLayer` takes rows with a position
    # accessor, not GeoJSON, so it would mean shipping the point coordinates a
    # second time and would drop any non-point geometry in the same file.
    icons = icon_expression(layer.renderer) if layer.icon_atlas else None
    if layer.icon_atlas is not None and icons is not None:
        attributes.append(("point-type", "icon"))
        attributes.append(("icon-atlas", layer.icon_atlas.data_uri))
        attributes.append(
            ("icon-mapping", json.dumps(layer.icon_atlas.mapping, sort_keys=True))
        )
        attributes.append(("get-icon", icons))
        # The size each cell was measured at, so a marker comes out the size
        # QGIS drew it. Per class when the classes disagree, which is what makes
        # a graduated-by-size layer of icons work.
        size = numeric_expression(layer.renderer, "icon_size", fallback=None)
        if size is None:
            first = next(
                (s.icon_size for s in _class_symbols(layer.renderer) if s.icon_size),
                symbol.icon_size,
            )
            size = _number(first or 0.0)
        attributes.append(("get-icon-size", size))
        # Without this deck.gl reads the size as metres and the markers balloon
        # on zoom in and vanish on zoom out - the same trap as line widths.
        attributes.append(("icon-size-units", "pixels"))
        if symbol.offset_x or symbol.offset_y:
            attributes.append(
                (
                    "get-icon-pixel-offset",
                    f"[{_number(symbol.offset_x)}, {_number(symbol.offset_y)}]",
                )
            )
    elif layer.geometry_kind is GeometryKind.POINT and symbol.radius:
        # QGIS graduated-by-size is the common case this rescues: every class
        # used to export at the representative symbol's radius, so a map whose
        # whole point was "bigger dot means more" came out uniform.
        radius = numeric_expression(renderer, "radius", fallback=symbol.radius)
        attributes.append(("get-point-radius", radius or _number(symbol.radius)))
        attributes.append(("point-radius-units", "pixels"))

    # Raised polygons, from either the layer's 3D view properties or a 2.5D
    # renderer. `extruded` is a `GeoJsonLayer` prop, so this needs no separate
    # layer class - it reaches the `PolygonLayer` sublayer that draws the fill.
    elevation = elevation_expression(layer.elevation)
    if elevation is not None:
        attributes.append(("extruded", "true"))
        attributes.append(("get-elevation", elevation))
        # QGIS's 3D edges, which read as a building outline rather than a
        # rendering artefact, so they are worth carrying.
        if layer.elevation.wireframe:
            attributes.append(("wireframe", "true"))

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
                else color_hex(highlight_color),
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

    collection = build_label_collection(
        layer.geojson,
        labeling.field_name,
        capitalization=labeling.capitalization,
        wrap_char=labeling.wrap_char,
        auto_wrap_length=labeling.auto_wrap_length,
    )
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

    if labeling.bold:
        attributes.append(("text-font-weight", "bold"))

    # A QGIS label buffer is a halo; deck.gl calls the same thing an outline.
    if labeling.halo_width and labeling.halo_color:
        attributes.append(("text-outline-color", color_literal(labeling.halo_color)))
        attributes.append(("text-outline-width", _number(labeling.halo_width)))

    # QGIS's placement quadrant. Emitted only when it differs from the neutral
    # centre, so an ordinary over-point label stays free of noise.
    if labeling.anchor != "middle":
        attributes.append(("get-text-anchor", f"'{labeling.anchor}'"))
    if labeling.baseline != "center":
        attributes.append(("get-text-alignment-baseline", f"'{labeling.baseline}'"))

    if labeling.offset_x or labeling.offset_y:
        attributes.append(
            (
                "get-text-pixel-offset",
                f"[{_number(labeling.offset_x)}, {_number(labeling.offset_y)}]",
            )
        )

    if labeling.rotation:
        attributes.append(("get-text-angle", _number(labeling.rotation)))

    # A QGIS label background is deck.gl's text background: a filled box behind
    # the glyphs. Without the flag the colour and padding are ignored silently.
    if labeling.background_color is not None:
        attributes.append(("text-background", "true"))
        attributes.append(
            ("get-text-background-color", color_literal(labeling.background_color))
        )
        pad_x, pad_y = labeling.background_padding
        if pad_x or pad_y:
            attributes.append(
                ("text-background-padding", f"[{_number(pad_x)}, {_number(pad_y)}]")
            )

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
    on_hover = layer.popup.on_hover if hover is None else hover
    # Scope the overlay to the pick type that opens it. Runtime 0.6.1 added
    # this, for exactly the bug we reported: `handleSelectionChange` writes hover
    # picks and click picks into one `ctx.selection`, and `updatePosition` used to
    # re-anchor *and re-interpolate the template* on any of them. So a popup
    # opened by clicking a river, then left alone while the pointer crossed an
    # airport, kept the river's title and redrew the airport's values into it -
    # blank wherever the two layers' fields differed.
    #
    # With `selection-type`, a pick whose type does not match is inert:
    # `z = !Y || (t ? t.type : i) === Y` in the bundle. A *click* on empty space
    # still dismisses a click popup, because the runtime keeps `lastPickType`
    # alongside the null selection precisely so it can tell that from a hover.
    # The trade is that hovering empty space no longer dismisses one - which is
    # right for a click popup, and for a hover popup the type still matches, so
    # unhover-to-close survives.
    selection_type = "hover" if on_hover else "click"

    return "\n".join(
        [
            f'{indent}<om-overlay id="{escape_attr(overlay_id)}" '
            f'anchor-from="selection" selection-type="{selection_type}" '
            f'visible="false">',
            *_popup_style_element(indent),
            f'{indent}  <div class="om-popup">',
            f'{indent}    <div class="om-popup-title">{escape_attr(layer.name)}</div>',
            *rows,
            f"{indent}  </div>",
            f"{indent}</om-overlay>",
            _popup_behavior(layer, overlay_id, on_hover, indent),
        ]
    )


def build_popup_reset_behaviors(project: ExportProject, indent: str = "    ") -> str:
    """Close every popup before any pick opens one.

    **`show-overlay` sets `visible="true"` and nothing ever sets it back.** The
    runtime dispatches behaviours only when there *is* a pick - there is no
    unhover event - so a popup opened once stayed open for the life of the page.
    Two visible symptoms, both reported from a real project:

    * Hovering a spot where three layers overlap left all three popups stacked
      on the same coordinate, so only the top one could be read.
    * A layer switched off in the layer switcher still showed its popup, because
      nothing had ever told that overlay to close.

    The reset is a `hide-overlay` per popup carrying **no `layer` attribute**, so
    it fires on every pick regardless of which layer was hit. Emitted *before*
    the layers, because `dispatchToBehaviors` walks `:scope > om-behavior` in
    document order and dispatches synchronously: every hide runs, then the one
    scoped `show-overlay` whose layer matched re-opens exactly one popup.

    Scoping each overlay with `layer="..."` is the fix that suggests itself and
    is wrong. An overlay hides when its anchor goes away, and an anchor goes away
    because an *unscoped* overlay follows a null selection. Scope it and it stops
    following, so hovering empty space would leave the last popup stranded on
    screen - trading a stacking bug for a stuck one.

    Only the triggers actually in use are emitted, so an all-click project never
    has a hover behaviour closing its popups, and vice versa.
    """
    targets = [
        f"{layer.layer_id}-popup"
        for layer in project.exportable_layers
        if layer.popup.enabled and layer.popup.visible_fields
    ]
    if not targets:
        return ""

    triggers = sorted(
        {
            "hover" if layer.popup.on_hover else "click"
            for layer in project.exportable_layers
            if layer.popup.enabled and layer.popup.visible_fields
        }
    )

    return "\n".join(
        f'{indent}<om-behavior on="{trigger}" action="hide-overlay" '
        f'target="{escape_attr(target)}"></om-behavior>'
        for trigger in triggers
        for target in targets
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


# **This travels inside the widget, not in the document's stylesheet.**
# `om-widget` moves its children into a shadow root, which document rules never
# reach - the same boundary as the popup's styles, and the same fix.
#
# The class names and the `--om-widget-*` custom properties are the runtime's
# own, deliberately: a custom legend that looked different from the built-in one
# would make a map with SVG markers look like a different product from a map
# without them. Custom properties inherit *through* a shadow root, so a user
# theming the map still themes this.
LEGEND_STYLES = """:host { font-family: system-ui, sans-serif; font-size: 12px; }
.omni-panel {
  background: var(--om-widget-bg, #fff);
  color: var(--om-widget-fg, #1f2937);
  padding: 10px 12px;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
  min-width: 140px;
}
.omni-panel h4 { margin: 0 0 8px; font-size: 12px; }
.omni-row { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
.omni-swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  flex: none;
  display: inline-block;
}
.omni-legend-sub { margin: 2px 0 6px 18px; }
.omni-legend-field {
  font-size: 10px;
  color: var(--om-widget-muted, #6b7280);
  margin-bottom: 2px;
}
.omni-legend-entry {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 0;
  font-size: 11px;
}
.omni-legend-entry .omni-swatch { width: 10px; height: 10px; }
/* An image swatch keeps its own shape, so it must not be squared off by the
   colour swatch's border-radius, and it must not be stretched: a star squashed
   into a 10x10 box is a different marker from the one on the map. */
.omni-legend-entry img.omni-swatch,
.omni-row img.omni-swatch {
  border-radius: 0;
  object-fit: contain;
  background: none;
}"""


def _legend_style_element(indent: str) -> list[str]:
    return [
        f"{indent}  <style>",
        *(f"{indent}    {line}" if line else "" for line in LEGEND_STYLES.splitlines()),
        f"{indent}  </style>",
    ]


def needs_image_legend(project: ExportProject) -> bool:
    """Whether any layer's legend entry has to be a picture rather than a colour.

    The built-in `type="legend"` widget derives its swatches from the styling
    accessors, which is richer than anything we could hand it - it stays
    interactive, it follows layer visibility, and it needs no markup from us. So
    it is kept for every project that does not need image swatches, which is
    almost all of them. Forking it unconditionally would mean maintaining a
    legend forever against a runtime that improves its own.
    """
    return any(layer.icon_atlas is not None for layer in project.exportable_layers)


def _swatch_markup(
    symbol: SymbolSpec, layer: ExportLayer, fallback: Color | None
) -> str:
    """One legend swatch: the marker's own picture, or a colour chip.

    The picture comes from the same rasterisation the map draws from, so the
    legend and the map cannot disagree about what a class looks like - which is
    the entire reason this widget exists rather than a hand-drawn approximation.
    """
    if layer.icon_atlas is not None and symbol.icon_name:
        source = layer.icon_atlas.swatches.get(symbol.icon_name)
        if source:
            return f'<img class="omni-swatch" src="{escape_attr(source)}" alt="">'
    color = symbol.fill_color or symbol.stroke_color or fallback
    return (
        f'<span class="omni-swatch" style="background:'
        f'{escape_attr(color_literal(color).strip(chr(39)))}"></span>'
    )


def _legend_entries(layer: ExportLayer, indent: str) -> list[str]:
    """The per-class rows under a layer, or nothing for a single symbol."""
    renderer = layer.renderer
    rows: list[tuple[str, SymbolSpec]] = []

    if renderer.kind is RendererKind.CATEGORIZED:
        rows = [(c.label or str(c.value), c.symbol) for c in renderer.categories]
    elif renderer.kind is RendererKind.GRADUATED:
        rows = [(k.label, k.symbol) for k in renderer.classes]

    if not rows:
        return []

    lines = [f'{indent}    <div class="omni-legend-sub">']
    if renderer.field_name:
        lines.append(
            f'{indent}      <div class="omni-legend-field">'
            f"{escape_attr(renderer.field_name)}</div>"
        )
    for label, symbol in rows:
        lines.append(
            f'{indent}      <div class="omni-legend-entry">'
            f"{_swatch_markup(symbol, layer, DEFAULT_FILL)}"
            f"<span>{escape_attr(label)}</span></div>"
        )
    # The "other" row matches the trailing fallback in the colour expression:
    # features outside every class are drawn, so they have to be explained.
    if renderer.kind is RendererKind.CATEGORIZED:
        lines.append(
            f'{indent}      <div class="omni-legend-entry">'
            f'<span class="omni-swatch" style="background:'
            f'{escape_attr(color_literal(DEFAULT_FILL).strip(chr(39)))}"></span>'
            f"<span>other</span></div>"
        )
    lines.append(f"{indent}    </div>")
    return lines


def build_legend_widget(project: ExportProject, indent: str = "    ") -> str:
    """A static legend whose swatches are the map's own markers.

    **No script.** An `<om-widget>` with no `type` and no `<script
    type="om/widget">` is a purely static widget: its markup is moved into the
    shadow root and left alone. So this stays markup rather than becoming the
    generated JavaScript this project exists not to write, and it needs nothing
    from the Content Security Policy beyond the `data:` images.

    What it gives up against the built-in widget is reactivity: it cannot grey
    a layer out when the layer switcher hides it. That is why it is used only
    when a layer needs image swatches - see `needs_image_legend`.
    """
    title = "" if project.settings.show_title else project.title
    lines = [f'{indent}<om-widget position="{WIDGET_POSITIONS["legend"]}">']
    lines.extend(_legend_style_element(indent))
    lines.append(f'{indent}  <div class="omni-panel">')
    lines.append(f"{indent}    <h4>{escape_attr(title or 'Legend')}</h4>")

    for layer in project.exportable_layers:
        symbol = layer.renderer.representative_symbol or SymbolSpec()
        lines.append(
            f'{indent}    <div class="omni-row">'
            f"{_swatch_markup(symbol, layer, DEFAULT_FILL)}"
            f"<span>{escape_attr(layer.name)}</span></div>"
        )
        lines.extend(_legend_entries(layer, indent))

    lines.append(f"{indent}  </div>")
    lines.append(f"{indent}</om-widget>")
    return "\n".join(lines)


def build_widget_elements(project: ExportProject, indent: str = "    ") -> str:
    """Widgets, on by default.

    qgis2web defaults seven Appearance options to "None", so a default export is
    a bare map with zoom controls and nothing else - which makes the tool look
    far more primitive than it is. Ours ship useful chrome unless asked not to.
    """
    settings = project.settings
    widgets: list[str] = []

    if settings.show_legend:
        if needs_image_legend(project):
            widgets.append(build_legend_widget(project, indent))
        else:
            # The legend carries the map title only when nothing else is showing
            # it. With the caption on - now the default - the title appeared
            # twice on one screen, once as the caption and once as the legend's
            # heading.
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
        # Telemetry is deliberately left to the runtime's default, which is on.
        # Emitting `telemetry="off"` here - which this did until 0.1.3 - opted
        # every exported map out of the anonymous usage reporting NIKA's licence
        # for the runtime provides for. See section 11 of
        # `runtime/ONLYMAP-LICENSE.md` for what a report contains, and
        # `docs/privacy.md` for how we describe it to the person exporting.
    ]

    # Same guard as the basemap: an unknown preset would make the runtime
    # request a DEM it cannot resolve, and blank relief is worse than none.
    terrain = project.settings.terrain
    has_terrain = terrain != "none" and terrain in TERRAIN_PRESETS
    if has_terrain:
        attributes.append(("terrain", terrain))
        # The runtime replaces the basemap while terrain is on, so the chosen
        # imagery is restored by draping its raster twin over the surface.
        # basemap "none" stays untextured: the user chose no imagery, and
        # relief must not quietly add a network dependency they declined.
        texture = RASTER_TEXTURE_TEMPLATES.get(
            project.settings.basemap
            if project.settings.basemap in BASEMAP_PRESETS
            else BASEMAP
        )
        if texture is not None:
            attributes.append(("terrain-texture", texture))

    # Height is invisible from directly overhead. Tilting is therefore not a
    # style preference but the difference between the extrusion showing and the
    # map looking identical to a flat one - so it follows whatever raises the
    # scene, rather than being a separate control the user has to find.
    if has_terrain or any(
        layer.elevation.is_set for layer in project.exportable_layers
    ):
        attributes.append(("pitch", _number(EXTRUDED_PITCH)))

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

    # First, before any layer or any `show-overlay`: behaviours dispatch in
    # document order, so these have to precede the shows to close the previous
    # popup rather than the one just opened.
    resets = build_popup_reset_behaviors(project, inner)
    if resets:
        sections.append(resets)

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
