"""Whether the expressions in a layer's or a map item's saved XML can be keyed.

The build caches (`core/cartograph_export.py`) reuse a result while its key
holds, and the key is the saved XML, the data's revision and the expression
variables. An expression whose answer depends on anything else -- the clock,
chance, another layer, a file, the environment, or a function registered from
Python whose body no key can see -- would let a cached result outlive the value
it was made from. So every function an expression calls must be one of QGIS's
own built-ins known to answer from its arguments and the feature alone
(`KEYABLE_FUNCTIONS`), and every variable it reads must be one the key holds
or one the feature or the drawing itself sets; anything else, including any
name this module does not know, means "do not cache".

The XML is not parsed for expressions, since QGIS keeps them in many places
under many names. Every attribute value and text is read as if it were one,
except the parts known never to be evaluated (a CRS, metadata, names, aliases,
a category's label): a false alarm costs one cache miss, a missed expression a
wrong page.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import functools
import hashlib
import re
import threading

# Parses QGIS's own layer XML only, and refuses entity declarations first.
import xml.etree.ElementTree as ElementTree  # nosec B405
from collections import OrderedDict
from collections.abc import Callable, Collection, Iterator

#: QGIS's built-in functions (and their aliases) that answer from their
#: arguments and the feature alone, lower-cased: QGIS matches names without
#: regard to case. A function registered from Python cannot take a built-in's
#: name (`QgsExpression.registerFunction` refuses it), so a name here that the
#: running QGIS has built in (see `_builtins`) is always QGIS's own. Left out
#: on purpose: the clock (`now`), chance (`rand`, `randf`, `uuid`), other
#: features and layers (aggregates, `get_feature`, `overlay_*`,
#: `layer_property`, `is_layer_visible`, `decode_uri`, `load_layer`,
#: `represent_value`, `display_expression`, `maptip`, `is_selected`,
#: `raster_value`), files and the environment (`env`, `exif`, `file_exists`,
#: `file_size`, `mime_type`, magnetic models), state the key does not hold
#: (`project_color`, `ramp_color`, `var`, `eval`, layout and form functions),
#: and anything QGIS adds later until it is looked at.
KEYABLE_FUNCTIONS = frozenset(
    {
        # Conditionals
        "coalesce",
        "if",
        "nullif",
        "regexp_match",
        "try",
        # Math
        "abs",
        "acos",
        "asin",
        "atan",
        "atan2",
        "ceil",
        "clamp",
        "cos",
        "degrees",
        "exp",
        "floor",
        "ln",
        "log",
        "log10",
        "max",
        "min",
        "pi",
        "radians",
        "round",
        "scale_cubic_bezier",
        "scale_exponential",
        "scale_linear",
        "scale_polynomial",
        "scale_exp",
        "sin",
        "sqrt",
        "tan",
        # Conversions
        "extract_degrees",
        "extract_minutes",
        "extract_seconds",
        "from_base64",
        "hash",
        "md5",
        "sha256",
        "to_base64",
        "to_bool",
        "tobool",
        "to_date",
        "todate",
        "to_datetime",
        "todatetime",
        "to_decimal",
        "todecimal",
        "to_dm",
        "todm",
        "to_dms",
        "todms",
        "to_int",
        "toint",
        "to_interval",
        "tointerval",
        "to_real",
        "toreal",
        "to_string",
        "tostring",
        "to_time",
        "totime",
        # Date and time, without the clock
        "age",
        "convert_timezone",
        "datetime_from_epoch",
        "day",
        "day_of_week",
        "epoch",
        "get_timezone",
        "hour",
        "make_date",
        "make_datetime",
        "make_interval",
        "make_time",
        "minute",
        "month",
        "second",
        "set_timezone",
        "timezone_from_id",
        "timezone_id",
        "week",
        "year",
        # String
        "ascii",
        "char",
        "concat",
        "concat_ws",
        "format",
        "format_date",
        "format_number",
        "left",
        "length",
        "lower",
        "lpad",
        "ltrim",
        "regexp_replace",
        "regexp_substr",
        "repeat",
        "replace",
        "reverse",
        "right",
        "rpad",
        "rtrim",
        "strpos",
        "substr",
        "substr_count",
        "title",
        "trim",
        "unaccent",
        "upper",
        "wordwrap",
        # Fuzzy matching
        "hamming_distance",
        "levenshtein",
        "longest_common_substring",
        "soundex",
        # Arrays
        "array",
        "array_all",
        "array_append",
        "array_cat",
        "array_contains",
        "array_count",
        "array_distinct",
        "array_filter",
        "array_find",
        "array_first",
        "array_foreach",
        "array_get",
        "array_insert",
        "array_intersect",
        "array_last",
        "array_length",
        "array_majority",
        "array_max",
        "array_mean",
        "array_median",
        "array_min",
        "array_minority",
        "array_prepend",
        "array_prioritize",
        "array_remove_all",
        "array_remove_at",
        "array_replace",
        "array_reverse",
        "array_slice",
        "array_sort",
        "array_sum",
        "array_to_string",
        "generate_series",
        "geometries_to_array",
        "regexp_matches",
        "string_to_array",
        # Maps
        "from_json",
        "json_to_map",
        "hstore_to_map",
        "map",
        "map_akeys",
        "map_avals",
        "map_concat",
        "map_delete",
        "map_exist",
        "map_get",
        "map_insert",
        "map_prefix_keys",
        "map_to_hstore",
        "map_to_html_dl",
        "map_to_html_table",
        "to_json",
        "map_to_json",
        "url_encode",
        # Colour, without the project's and the style library's colours
        "color_cmyk",
        "color_cmyka",
        "color_cmykf",
        "color_grayscale_average",
        "color_hsl",
        "color_hsla",
        "color_hslf",
        "color_hsv",
        "color_hsva",
        "color_hsvf",
        "color_mix",
        "color_mix_rgb",
        "color_part",
        "color_rgb",
        "color_rgba",
        "color_rgbf",
        "create_ramp",
        "darker",
        "lighter",
        "set_color_part",
        # CRS
        "crs_from_text",
        "crs_to_authid",
        # Record and attributes: the feature's own
        "attribute",
        "attributes",
        "feature_id",
        # General
        "with_variable",
        # Files and paths: text operations on a path, no file read
        "base_file_name",
        "file_name",
        "file_path",
        "file_suffix",
        # Geometry, without the overlays (which read other layers)
        "affine_transform",
        "angle_at_vertex",
        "apply_dash_pattern",
        "area",
        "azimuth",
        "bearing",
        "boundary",
        "bounds",
        "bounds_height",
        "bounds_width",
        "buffer",
        "buffer_by_m",
        "centroid",
        "close_line",
        "closest_point",
        "collect_geometries",
        "combine",
        "concave_hull",
        "contains",
        "convex_hull",
        "convexhull",
        "crosses",
        "densify_by_count",
        "densify_by_distance",
        "difference",
        "disjoint",
        "distance",
        "distance_to_vertex",
        "end_point",
        "equals",
        "equals_exact",
        "equals_fuzzy",
        "equals_topological",
        "extend",
        "exterior_ring",
        "extrude",
        "flip_coordinates",
        "force_polygon_ccw",
        "force_polygon_cw",
        "force_rhr",
        "geom_from_gml",
        "geomfromgml",
        "geom_from_wkb",
        "geom_from_wkt",
        "geomfromwkt",
        "geom_to_wkb",
        "geom_to_wkt",
        "geomtowkt",
        "geometry",
        "geometry_n",
        "geometry_type",
        "hausdorff_distance",
        "inclination",
        "interior_ring_n",
        "intersection",
        "intersects",
        "intersects_bbox",
        "bbox",
        "is_closed",
        "is_empty",
        "is_empty_or_null",
        "is_multipart",
        "is_valid",
        "length3d",
        "line_interpolate_angle",
        "line_interpolate_point",
        "line_interpolate_point_by_m",
        "line_locate_m",
        "line_locate_point",
        "line_merge",
        "line_substring",
        "m",
        "m_at",
        "m_max",
        "m_min",
        "main_angle",
        "make_circle",
        "make_ellipse",
        "make_line",
        "make_point",
        "make_point_m",
        "make_polygon",
        "make_rectangle_3points",
        "make_regular_polygon",
        "make_square",
        "make_triangle",
        "make_valid",
        "minimal_circle",
        "nodes_to_points",
        "num_geometries",
        "num_interior_rings",
        "num_points",
        "num_rings",
        "offset_curve",
        "order_parts",
        "oriented_bbox",
        "overlaps",
        "perimeter",
        "point_n",
        "point_on_surface",
        "pole_of_inaccessibility",
        "project",
        "relate",
        "rotate",
        "roundness",
        "scale",
        "segments_to_lines",
        "shared_paths",
        "shortest_line",
        "simplify",
        "simplify_vw",
        "single_sided_buffer",
        "sinuosity",
        "smooth",
        "square_wave",
        "straight_distance_2d",
        "start_point",
        "sym_difference",
        "symdifference",
        "tapered_buffer",
        "touches",
        "transform",
        "translate",
        "triangular_wave",
        "union",
        "wave",
        "wedge_buffer",
        "within",
        "x",
        "x_at",
        "xat",
        "x_max",
        "xmax",
        "x_min",
        "xmin",
        "y",
        "y_at",
        "yat",
        "y_max",
        "ymax",
        "y_min",
        "ymin",
        "z",
        "z_at",
        "z_max",
        "z_min",
    }
)

#: Keywords a bracket may follow, which the call pattern finds as names.
_KEYWORDS = frozenset(
    {"and", "or", "not", "in", "is", "like", "ilike", "case", "when", "then", "else"}
)

#: The `$` forms that read only the feature (and, for measures, the project's
#: ellipsoid and units, which are project variables and so in the key).
#: `$now`, `$uuid`, `$scale` and the old atlas ones are not.
KEYABLE_DOLLAR_NAMES = frozenset(
    {
        "area",
        "currentfeature",
        "geometry",
        "id",
        "length",
        "perimeter",
        "pi",
        "x",
        "x_at",
        "y",
        "y_at",
        "z",
    }
)

#: Variables the feature or the drawing itself sets (the feature, its parts, the
#: symbol being drawn, an array function's element, a map item's own settings),
#: whatever the scopes the key holds. The global, project and layer variables
#: are keyed and passed in by the caller; an atlas's, a layout's or a form's
#: are not, nor is anything not named here.
KEYABLE_CONTEXT_VARIABLES = frozenset(
    {
        "cluster_color",
        "cluster_size",
        "element",
        "feature",
        "geometry",
        "geometry_part_count",
        "geometry_part_num",
        "geometry_point_count",
        "geometry_point_num",
        "geometry_ring_num",
        "grid_axis",
        "grid_number",
        "id",
        "layer",
        "layer_crs",
        "layer_crs_ellipsoid",
        "layer_id",
        "layer_name",
        "parent",
        "value",
        "vector_tile_zoom",
        "zoom_level",
    }
)

#: Prefixes of the variables a symbol being drawn (`@symbol_color`) or a map
#: item (`@map_scale`, from the item's own saved settings) sets.
KEYABLE_CONTEXT_PREFIXES = ("symbol_", "map_")

#: Parts of the XML that are never evaluated as an expression: a CRS, the
#: layer's metadata, its names and aliases, its source (its filter is checked
#: on its own, see `has_unkeyable_expression`), its custom properties (the
#: layer's variables among them, whose values are plain text), map tips (the
#: export does not read them).
_SKIPPED_ELEMENTS = frozenset(
    {
        "abstract",
        "aliases",
        "attribution",
        "crs",
        "customproperties",
        "dataUrl",
        "datasource",
        "keywordList",
        "layername",
        "legendUrl",
        "mapTip",
        "metadataUrls",
        "resourceMetadata",
        "shortname",
        "spatialrefsys",
        "srs",
        "title",
    }
)

#: Attributes never evaluated, on any element: an option's or a field's name,
#: identifiers.
_SKIPPED_ATTRIBUTES = frozenset({"name", "id", "uuid"})

#: Attributes never evaluated, on the elements named: a class's label and
#: value, a rule's label, the field a default or constraint is about.
_SKIPPED_ATTRIBUTES_OF = {
    "category": frozenset({"label", "value"}),
    "range": frozenset({"label"}),
    "rule": frozenset({"label", "description"}),
    "default": frozenset({"field"}),
    "constraint": frozenset({"field"}),
    "constraintExpression": frozenset({"field", "desc"}),
}

# A quoted string or field name, with its doubled quotes: what is inside is
# never a call.
_QUOTED_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
# Names start with any letter or an underscore, not only an ASCII one: a
# function registered from Python may be called `área`.
_CALL_RE = re.compile(r"(?<![\w$])([^\W\d]\w*)\s*\(")
_DOLLAR_RE = re.compile(r"\$([^\W\d]\w*)")
_VARIABLE_RE = re.compile(r"@([^\W\d]\w*)")
_BOUND_RE = re.compile(r"with_variable\s*\(\s*'(\w+)'", re.IGNORECASE)


def _texts(element: ElementTree.Element) -> Iterator[str]:
    """Every attribute value and text under `element` that may be an
    expression."""
    if element.tag in _SKIPPED_ELEMENTS:
        return
    skipped = _SKIPPED_ATTRIBUTES_OF.get(element.tag, frozenset())
    for name, value in element.attrib.items():
        if name not in _SKIPPED_ATTRIBUTES and name not in skipped:
            yield value
    if element.text:
        yield element.text
    for child in element:
        yield from _texts(child)
        if child.tail:
            yield child.tail


#: What expressions read, as (functions, `$` names, variables), the functions
#: and `$` names lower-cased.
Reads = tuple[frozenset[str], frozenset[str], frozenset[str]]


def _add_reads(
    text: str, functions: set[str], dollars: set[str], variables: set[str]
) -> None:
    """Add what the expressions in `text` read. A variable an expression binds
    itself (`with_variable('size', ...)`) is not counted where it is bound."""
    if "(" not in text and "$" not in text and "@" not in text:
        return
    bound = set(_BOUND_RE.findall(text))
    bare = _QUOTED_RE.sub(" ", text)
    functions.update(name.lower() for name in _CALL_RE.findall(bare))
    dollars.update(name.lower() for name in _DOLLAR_RE.findall(bare))
    variables.update(set(_VARIABLE_RE.findall(bare)) - bound)


def expression_reads(xml: str) -> Reads | None:
    """The functions, `$` names and variables the expressions in `xml` read;
    None when `xml` is not readable XML."""
    if "<!ENTITY" in xml:
        # QGIS never writes entity declarations, so refusing them costs no
        # real layer and rules out entity expansion from a crafted project.
        return None
    try:
        root = ElementTree.fromstring(xml)  # nosec B314
    except ElementTree.ParseError:
        return None
    functions: set[str] = set()
    dollars: set[str] = set()
    variables: set[str] = set()
    for text in _texts(root):
        _add_reads(text, functions, dollars, variables)
    return frozenset(functions), frozenset(dollars), frozenset(variables)


class _ReadsMemo:
    """`expression_reads` by the digest of the XML: a layer's XML is read on
    every build and seldom changes. Bounded; safe from any thread."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[bytes, Reads | None] = OrderedDict()

    def reads(self, xml: str) -> Reads | None:
        digest = hashlib.sha256(xml.encode("utf-8")).digest()
        with self._lock:
            if digest in self._entries:
                self._entries.move_to_end(digest)
                return self._entries[digest]
        found = expression_reads(xml)
        with self._lock:
            self._entries[digest] = found
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return found


_MEMO = _ReadsMemo()


def has_unkeyable_expression(
    xml: str, keyed_variables: Callable[[], Collection[str]], extra: str = ""
) -> bool:
    """True when an expression in `xml`, or in the expression text `extra`
    (a layer's filter), calls a function or reads a variable a key cannot
    vouch for. `keyed_variables` gives the names whose values the key holds
    (the global, project and layer variables); it is called only when an
    expression reads a variable the drawing does not set itself."""
    reads = _MEMO.reads(xml)
    if reads is None:
        return True
    functions, dollars, variables = (set(part) for part in reads)
    _add_reads(extra, functions, dollars, variables)
    return _unkeyable(functions, dollars, variables, keyed_variables)


def is_unkeyable_expression(
    expression: str, keyed_variables: Callable[[], Collection[str]]
) -> bool:
    """`has_unkeyable_expression` for one expression's text (a widget's
    filter)."""
    functions: set[str] = set()
    dollars: set[str] = set()
    variables: set[str] = set()
    _add_reads(expression, functions, dollars, variables)
    return _unkeyable(functions, dollars, variables, keyed_variables)


@functools.lru_cache(maxsize=1)
def _builtins() -> tuple[frozenset[str], frozenset[str]]:
    """The lists' functions and `$` names that are built into the running
    QGIS, which a function registered from Python can neither take the name
    of nor replace: a name on the lists that this QGIS does not have (a
    function added in a later version) could be a Python one here. Outside
    QGIS (the unit tests), the lists as they are."""
    try:
        from qgis.core import QgsExpression
    except ImportError:
        return KEYABLE_FUNCTIONS, KEYABLE_DOLLAR_NAMES
    names = {name.lower() for name in QgsExpression.BuiltinFunctions()}
    return (
        KEYABLE_FUNCTIONS & names,
        frozenset(name for name in KEYABLE_DOLLAR_NAMES if f"${name}" in names),
    )


def _unkeyable(
    functions: set[str],
    dollars: set[str],
    variables: set[str],
    keyed_variables: Callable[[], Collection[str]],
) -> bool:
    keyable_functions, keyable_dollars = _builtins()
    if not functions - _KEYWORDS <= keyable_functions or not dollars <= keyable_dollars:
        return True
    others = {
        name
        for name in variables
        if name not in KEYABLE_CONTEXT_VARIABLES
        and not name.startswith(KEYABLE_CONTEXT_PREFIXES)
    }
    return bool(others) and not others <= set(keyed_variables())
