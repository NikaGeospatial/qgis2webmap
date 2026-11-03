"""WMS and WMTS layers as tile templates the web map fills itself.

The runtime (OnlyMap 0.13.3 and later) draws a raster `TileLayer` from a
template it fills per tile: `{z}`/`{x}`/`{y}` for a tile grid, and
`{bbox-epsg-3857}`, `{width}` and `{height}` for a service that is asked for an
extent - a WMS GetMap. Each tile is a Web Mercator tile, so a WMS layer can
travel only when the service offers Web Mercator, and a WMTS layer only when
its tile grid *is* the Web Mercator grid.

**What QGIS knows, and nothing more.** Exporting never contacts a service.
Whether a WMS offers EPSG:3857, which WMS version QGIS speaks to it, and where
a WMTS serves its tiles are all in the service's capabilities document, which
QGIS fetched when the layer was loaded and kept in its network cache. The
reader hands that cached copy in (or `None` when there is none), and this
module decides from it. No copy and no other evidence means the layer is
refused, with a reason, rather than exported on a guess: a WMS asked for a
projection it does not offer draws nothing.

The requests mirror QGIS's own (`QgsWmsProvider::createRequestUrlWMS` and
`createTileRequestsWMTS`): the same GetMap address, parameters and
TRANSPARENT rule, so the web map asks the service what QGIS asks it, in Web
Mercator.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re

# Parses capabilities QGIS stored, and refuses entity declarations first.
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote

# Codes that name Web Mercator. A service that offers only an older alias is
# asked in that alias: the BBOX values are the same metres either way.
WEB_MERCATOR_CODES = (
    "EPSG:3857",
    "EPSG:900913",
    "EPSG:3785",
    "EPSG:102100",
    "EPSG:102113",
)
# Half the Web Mercator world's width, in metres: the grid's top-left corner
# is (-HALF_WORLD, HALF_WORLD).
HALF_WORLD = math.pi * 6378137.0
# The OGC's standardized rendering pixel size, which a WMTS scale denominator
# is measured against.
_PIXEL_METRES = 0.00028

# GetMap parameters this module writes. The same names already in the service
# URL are replaced, as QGIS's `setQueryItem` replaces them.
_WMS_RESERVED = frozenset(
    {
        "SERVICE",
        "VERSION",
        "REQUEST",
        "LAYERS",
        "STYLES",
        "FORMAT",
        "TRANSPARENT",
        "CRS",
        "SRS",
        "BBOX",
        "WIDTH",
        "HEIGHT",
        "FILTER",
        "OPACITIES",
    }
)
_WMTS_RESERVED = frozenset(
    {
        "SERVICE",
        "REQUEST",
        "VERSION",
        "LAYER",
        "STYLE",
        "FORMAT",
        "TILEMATRIXSET",
        "TILEMATRIX",
        "TILEROW",
        "TILECOL",
    }
)
# The tile size a WMS template asks for. The runtime's own default; written
# out so `{width}`/`{height}` are readable in the markup.
WMS_TILE_SIZE = 512


def source_pairs(source: str) -> list[tuple[str, str]]:
    """A `wms` provider source string as its (key, value) pairs, in order.

    Kept as pairs, not a dict: QGIS writes one `layers=` and one `styles=`
    per sublayer.
    """
    return parse_qsl(source or "", keep_blank_values=True)


def values(pairs: Iterable[tuple[str, str]], key: str) -> list[str]:
    """Every value of `key` (case-sensitive, as QGIS reads it)."""
    return [value for name, value in pairs if name == key]


def value(pairs: Iterable[tuple[str, str]], key: str) -> str:
    """The first value of `key`, or `""`."""
    found = values(pairs, key)
    return found[0] if found else ""


def prepare_url(url: str) -> str:
    """The service address the way `QgsWmsProvider::prepareUri` makes it:
    percent-decoded, and ending in `?` or `&` so parameters can follow."""
    url = unquote(url)
    if is_wmts_url(url):
        return url
    if "?" not in url:
        return url + "?"
    if not url.endswith(("?", "&")):
        return url + "&"
    return url


def is_wmts_url(url: str) -> bool:
    """`QgsWmsProvider::isUrlForWMTS`: the URL is a WMTS capabilities address."""
    lowered = url.lower()
    return "service=wmts" in lowered or "/wmtscapabilities.xml" in lowered


def capabilities_url(url: str) -> str:
    """The address QGIS fetched this service's capabilities from - the key
    its network cache keeps them under (`QgsWmsCapabilitiesDownload`)."""
    prepared = prepare_url(url)
    if is_wmts_url(prepared):
        return prepared
    return prepared + "SERVICE=WMS&REQUEST=GetCapabilities"


def web_mercator_code(code: str) -> str | None:
    """`code` as `EPSG:<n>` when it names Web Mercator, else `None`.

    Takes the forms capabilities use: `EPSG:3857`, the URN
    `urn:ogc:def:crs:EPSG::3857` and the URI
    `http://www.opengis.net/def/crs/EPSG/0/3857`.
    """
    match = re.search(r"(\d+)\s*$", code)
    if match is None or "epsg" not in code.lower():
        return None
    normalized = f"EPSG:{match.group(1)}"
    return normalized if normalized in WEB_MERCATOR_CODES else None


_IMAGE_EXTENSION_RE = re.compile(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", re.IGNORECASE)
# A service that names its format as a parameter: a WMS or WMTS `FORMAT=image/`,
# an ArcGIS export's `format=png`.
_IMAGE_FORMAT_RE = re.compile(
    r"[?&]format=(image(/|%2F)|(png|jpe?g|webp)\b)", re.IGNORECASE
)


def is_image_tile_template(url: str) -> bool:
    """Whether the runtime draws `url`'s tiles as images.

    The runtime's own test (0.13.3, `isImageTileTemplate`): an image extension
    on the path or a format parameter. Any other raster template is decoded
    as data and fails, and only image tiles can be warped into a projected
    map's plane.
    """
    return (
        _IMAGE_EXTENSION_RE.search(url) is not None
        or _IMAGE_FORMAT_RE.search(url) is not None
    )


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    found = _children(element, name)
    return found[0] if found else None


def _text(element: ET.Element | None, name: str) -> str:
    child = _child(element, name) if element is not None else None
    return (child.text or "").strip() if child is not None else ""


def _href(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    for key, found in element.attrib.items():
        if _local(key) == "href":
            return found
    return None


def _parse(document: bytes | None) -> ET.Element | None:
    if not document or b"<!ENTITY" in document:
        # No WMS or WMTS capabilities document needs one, so refusing them
        # costs no real service and rules out entity expansion from a crafted
        # one.
        return None
    try:
        return ET.fromstring(document)  # nosec B314
    except ET.ParseError:
        return None


# --------------------------------------------------------------------------
# WMS
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WmsCapabilities:
    """What a WMS capabilities document says that a GetMap template needs."""

    version: str
    get_map_url: str | None
    # Every named layer's CRS codes, inherited ones included (a WMS layer
    # inherits its parents' CRSs).
    layer_crs: dict[str, frozenset[str]]

    def offered_web_mercator(self, layers: Sequence[str]) -> str | None:
        """The Web Mercator code every one of `layers` offers, preferring
        EPSG:3857, or `None` when one of them offers none or is not listed."""
        if not layers or any(name not in self.layer_crs for name in layers):
            return None
        shared = frozenset.intersection(*(self.layer_crs[name] for name in layers))
        offered = {web_mercator_code(code) for code in shared} - {None}
        return next((code for code in WEB_MERCATOR_CODES if code in offered), None)


def parse_wms_capabilities(document: bytes | None) -> WmsCapabilities | None:
    """Read a WMS 1.1.1 or 1.3.0 capabilities document; `None` if it is not one."""
    root = _parse(document)
    roots = ("WMS_Capabilities", "WMT_MS_Capabilities")
    if root is None or _local(root.tag) not in roots:
        return None
    capability = _child(root, "Capability")
    if capability is None:
        return None
    get_map = None
    request = _child(capability, "Request")
    get_map_element = _child(request, "GetMap") if request is not None else None
    if get_map_element is not None:
        for dcp in _children(get_map_element, "DCPType"):
            http = _child(dcp, "HTTP")
            get = _child(http, "Get") if http is not None else None
            href = _href(_child(get, "OnlineResource")) if get is not None else None
            if href:
                get_map = href
                break

    layer_crs: dict[str, frozenset[str]] = {}

    def walk(layer: ET.Element, inherited: frozenset[str]) -> None:
        codes = set(inherited)
        for name in ("CRS", "SRS"):
            for element in _children(layer, name):
                # 1.1.1 allowed several codes in one element, space-separated.
                codes.update((element.text or "").split())
        own = frozenset(codes)
        name = _text(layer, "Name")
        if name:
            layer_crs[name] = own
        for child in _children(layer, "Layer"):
            walk(child, own)

    for layer in _children(capability, "Layer"):
        walk(layer, frozenset())
    return WmsCapabilities(
        version=root.attrib.get("version", ""),
        get_map_url=get_map,
        layer_crs=layer_crs,
    )


def _query_value(text: str, safe: str = "") -> str:
    return quote(text, safe=safe)


def _kept_url_parameters(query: str, reserved: frozenset[str]) -> list[tuple[str, str]]:
    return [
        (key, found)
        for key, found in parse_qsl(query, keep_blank_values=True)
        if key.upper() not in reserved
    ]


def _join(base: str, pairs: Sequence[tuple[str, str]]) -> str:
    query = "&".join(f"{key}={found}" for key, found in pairs)
    return f"{base}?{query}"


def wms_get_map_template(
    url: str,
    version: str,
    layers: Sequence[str],
    styles: Sequence[str],
    image_format: str,
    crs: str,
    extra: Sequence[tuple[str, str]] = (),
) -> str:
    """The GetMap request QGIS would send, in Web Mercator, as a tile template.

    `url` is the GetMap address with any parameters of its own, which are
    kept (a MapServer `map=`, say) unless they are one this request sets.
    Layer and style names are percent-encoded one by one and joined with a
    literal comma, as the LAYERS list separator must stay. `extra` are
    further parameters QGIS sends verbatim (FILTER, OPACITIES).
    """
    base, _, query = url.partition("?")
    pairs = [
        (_query_value(key), _query_value(found, safe="/:,"))
        for key, found in _kept_url_parameters(query, _WMS_RESERVED)
    ]
    crs_key = "SRS" if version.startswith("1.1") else "CRS"
    pairs += [
        ("SERVICE", "WMS"),
        ("VERSION", _query_value(version)),
        ("REQUEST", "GetMap"),
        ("LAYERS", ",".join(_query_value(name) for name in layers)),
        ("STYLES", ",".join(_query_value(name) for name in styles)),
        ("FORMAT", _query_value(image_format, safe="/")),
    ]
    # QGIS's rule, kept with its reason: JPEG has no transparency, and some
    # servers complain when asked for it; others reject lower-case `true`.
    lowered = image_format.lower()
    opaque = "jpeg" in lowered or "jpg" in lowered
    if lowered == "image/x-jpegorpng" or not opaque:
        pairs.append(("TRANSPARENT", "TRUE"))
    pairs += [(key, _query_value(found, safe=":,")) for key, found in extra]
    pairs += [
        (crs_key, crs),
        ("WIDTH", "{width}"),
        ("HEIGHT", "{height}"),
        ("BBOX", "{bbox-epsg-3857}"),
    ]
    return _join(base, pairs)


# --------------------------------------------------------------------------
# WMTS
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WmtsTiles:
    """A WMTS layer on the Web Mercator grid, as the runtime takes it."""

    template: str
    min_zoom: int
    max_zoom: int
    tile_size: int


@dataclass(frozen=True)
class WmtsRefusal:
    """Why a WMTS layer cannot be a tile template. `grid` is set when the
    reason is the tile grid itself, so the reader can say which one."""

    reason: str
    grid: str | None = None


def _web_mercator_zooms(
    matrix_set: ET.Element,
) -> tuple[str, list[int], int] | None:
    """`(identifier prefix, zooms, tile size)` when every matrix of the set is
    a level of the Web Mercator grid, named prefix + zoom; else `None`."""
    supported = _text(matrix_set, "SupportedCRS")
    if web_mercator_code(supported) is None:
        return None
    prefix: str | None = None
    zooms: list[int] = []
    tile_size: int | None = None
    for matrix in _children(matrix_set, "TileMatrix"):
        try:
            width = int(_text(matrix, "MatrixWidth"))
            height = int(_text(matrix, "MatrixHeight"))
            tile_width = int(_text(matrix, "TileWidth"))
            tile_height = int(_text(matrix, "TileHeight"))
            scale = float(_text(matrix, "ScaleDenominator"))
            left, top = (float(v) for v in _text(matrix, "TopLeftCorner").split())
        except ValueError:
            return None
        zoom = round(math.log2(width)) if width > 0 else -1
        if zoom < 0 or width != 2**zoom or height != width:
            return None
        if tile_width != tile_height or tile_size not in (None, tile_width):
            return None
        tile_size = tile_width
        if abs(left + HALF_WORLD) > 1.0 or abs(top - HALF_WORLD) > 1.0:
            return None
        ground = scale * _PIXEL_METRES * tile_width * width
        if abs(ground - 2 * HALF_WORLD) > 2 * HALF_WORLD * 0.01:
            return None
        identifier = _text(matrix, "Identifier")
        number = str(zoom)
        if not identifier.endswith(number):
            return None
        head = identifier[: -len(number)]
        # A prefix that ends in a digit would make "1" + "0" read as "10".
        if prefix not in (None, head) or head[-1:].isdigit():
            return None
        prefix = head
        zooms.append(zoom)
    if not zooms or tile_size is None or prefix is None:
        return None
    zooms.sort()
    if zooms != list(range(zooms[0], zooms[-1] + 1)):
        return None
    return prefix, zooms, tile_size


def _replace_token(template: str, token: str, replacement: str) -> str:
    return re.sub(
        r"\{" + re.escape(token) + r"\}",
        lambda _: replacement,
        template,
        flags=re.IGNORECASE,
    )


def wmts_tiles(
    document: bytes | None,
    layer: str,
    style: str,
    image_format: str,
    matrix_set: str,
    dimensions: dict[str, str],
) -> WmtsTiles | WmtsRefusal:
    """The template for one WMTS layer on `matrix_set`, or why there is none.

    KVP GetTile when the service offers it, as QGIS prefers, otherwise the
    layer's RESTful `ResourceURL`. Matrix identifiers must be a fixed prefix
    and the zoom (`3`, `EPSG:3857:3`), since `{z}` is the only way to name one.
    """
    root = _parse(document)
    if root is None or _local(root.tag) != "Capabilities":
        return WmtsRefusal("no capabilities")
    contents = _child(root, "Contents")
    if contents is None:
        return WmtsRefusal("no capabilities")
    layer_element = next(
        (
            element
            for element in _children(contents, "Layer")
            if _text(element, "Identifier") == layer
        ),
        None,
    )
    set_element = next(
        (
            element
            for element in _children(contents, "TileMatrixSet")
            if _text(element, "Identifier") == matrix_set
        ),
        None,
    )
    if layer_element is None or set_element is None:
        return WmtsRefusal("not listed")
    grid = _web_mercator_zooms(set_element)
    if grid is None:
        return WmtsRefusal("grid", grid=matrix_set)
    prefix, zooms, tile_size = grid

    declared = [_text(d, "Identifier") for d in _children(layer_element, "Dimension")]
    for name in declared:
        # QGIS fills a time dimension from its temporal controls, which the
        # web map has none of; any other is fixed by `tileDimensions` or left
        # to the service's default, as QGIS leaves it.
        if name.lower() == "time" and name not in dimensions:
            return WmtsRefusal("time")

    kvp_url = None
    metadata = _child(root, "OperationsMetadata")
    for operation in _children(metadata, "Operation") if metadata is not None else ():
        if operation.attrib.get("name") != "GetTile":
            continue
        for get in operation.iter():
            if _local(get.tag) != "Get":
                continue
            encodings = [
                (v.text or "").strip().upper()
                for v in get.iter()
                if _local(v.tag) == "Value"
            ]
            if not encodings or "KVP" in encodings:
                kvp_url = _href(get)
                break

    if kvp_url:
        base, _, query = prepare_url(kvp_url).rstrip("?&").partition("?")
        pairs = [
            (_query_value(key), _query_value(found, safe="/:,"))
            for key, found in _kept_url_parameters(query, _WMTS_RESERVED)
        ]
        pairs += [
            ("SERVICE", "WMTS"),
            ("REQUEST", "GetTile"),
            ("VERSION", _query_value(root.attrib.get("version", "1.0.0"))),
            ("LAYER", _query_value(layer)),
            ("STYLE", _query_value(style)),
            ("FORMAT", _query_value(image_format, safe="/")),
            ("TILEMATRIXSET", _query_value(matrix_set)),
            ("TILEMATRIX", _query_value(prefix) + "{z}"),
        ]
        pairs += [
            (_query_value(key), _query_value(found, safe=":,"))
            for key, found in sorted(dimensions.items())
        ]
        pairs += [("TILEROW", "{y}"), ("TILECOL", "{x}")]
        return WmtsTiles(_join(base, pairs), zooms[0], zooms[-1], tile_size)

    resource = next(
        (
            element.attrib.get("template", "")
            for element in _children(layer_element, "ResourceURL")
            if element.attrib.get("resourceType") == "tile"
            and element.attrib.get("format") == image_format
        ),
        "",
    )
    if not resource:
        return WmtsRefusal("no tile address")
    template = resource
    for token, replacement in (
        ("layer", quote(layer, safe="")),
        ("style", quote(style, safe="")),
        ("tilematrixset", quote(matrix_set, safe="")),
        ("tilematrix", quote(prefix, safe="") + "\0z\0"),
        ("tilerow", "\0y\0"),
        ("tilecol", "\0x\0"),
        *((key, quote(found, safe="")) for key, found in dimensions.items()),
    ):
        template = _replace_token(template, token, replacement)
    if re.search(r"\{[^}]*\}", template):
        # A dimension the source fixes no value for: QGIS would request the
        # token literally too, so there is no address that draws.
        return WmtsRefusal("no tile address")
    template = template.replace("\0z\0", "{z}").replace("\0y\0", "{y}")
    template = template.replace("\0x\0", "{x}")
    if not is_image_tile_template(template):
        # Nothing in the address says "image", so the runtime would decode
        # the tiles as data. The format is known, but adding a parameter the
        # service never asked for would be a guess about its URLs.
        return WmtsRefusal("not imagery")
    return WmtsTiles(template, zooms[0], zooms[-1], tile_size)


def parse_tile_dimensions(text: str) -> dict[str, str]:
    """QGIS's `tileDimensions=` value, `name=value;name=value`, as a dict."""
    found: dict[str, str] = {}
    for part in text.split(";"):
        if not part:
            continue
        key, _, rest = part.partition("=")
        found[key] = rest
    return found
