"""WMS and WMTS layers as tile templates the web map fills itself.

The runtime (OnlyMap 0.13.4) draws a raster `TileLayer` from a template it
fills per tile: `{z}`/`{x}`/`{y}` for a tile grid, `{bbox-epsg-3857}`,
`{width}` and `{height}` for a service that is asked for an extent - a WMS
GetMap - and `{TileMatrix}`/`{TileRow}`/`{TileCol}` with a `tile-matrix-set`
for a WMTS on a grid of its own. Each tile is a Web Mercator tile. A WMS that
offers Web Mercator is asked in it; one that does not is asked in the CRS
QGIS asks it in, with `{bbox-epsg-XXXX}`, and the runtime warps the picture
into place. A WMTS on the Web Mercator grid is addressed by `{z}`; one on any
other grid by its own matrices, which the runtime warps the same way.

**What QGIS knows, and nothing more.** Exporting never contacts a service.
Whether a WMS offers EPSG:3857, which WMS version QGIS speaks to it, and where
a WMTS serves its tiles and on which grid are all in the service's
capabilities document, which QGIS fetched when the layer was loaded and kept
in its network cache. The reader hands that cached copy in (or `None` when
there is none), and this module decides from it. No copy and no other
evidence means a WMTS layer is refused, with a reason, rather than exported on
a guess.

The requests mirror QGIS's own (`QgsWmsProvider::createRequestUrlWMS` and
`createTileRequestsWMTS`): the same GetMap address, parameters and
TRANSPARENT rule, so the web map asks the service what QGIS asks it. The
legend picture is the one QGIS's legend shows
(`QgsWmsProvider::getLegendGraphicUrl` and `getLegendGraphicFullURL`): the
style's LegendURL, or a GetLegendGraphic request, from the same capabilities.
A WMS-T's times are the time dimension QGIS read from the capabilities
(`parse_time_dimension`). A click on a WMS layer asks the service what is
there, as QGIS's Identify tool does: a GetFeatureInfo of the same GetMap,
which the page fills from the click (`wms_feature_info_template`).

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import calendar
import json
import math
import re

# Parses capabilities QGIS stored, and refuses entity declarations first.
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from .iso_time import from_iso

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


def on_https_as_added(address: str, added: str) -> str:
    """`address`, on `https://` when the capabilities name it on `http://`
    but the layer was added on `https://` at the same host.

    Such a service (the British Geological Survey's, 2026-11-05) answers its
    `http://` address with a redirect to `https://`, which QGIS follows. A web
    page cannot: WebKit refuses a redirect that carries no CORS header, and
    a page served over `https://` may not ask `http://` at all. So the map
    asks where the layer was added, which the service answers already.
    """
    asked, given = urlsplit(address), urlsplit(added)
    if (
        asked.scheme == "http"
        and given.scheme == "https"
        and asked.hostname
        and asked.hostname == given.hostname
    ):
        return "https" + address[len("http") :]
    return address


def shown_host(address: str) -> str:
    """The host a Fidelity row names for `address`: without a login, and
    without its port where that is the scheme's own (`:443` on `https://`,
    `:80` on `http://`), which ArcGIS Server's capabilities spell out."""
    parts = urlsplit(address)
    host = parts.netloc.rpartition("@")[2]
    default = {"http": ":80", "https": ":443"}.get(parts.scheme.lower(), "")
    if default and host.endswith(default):
        return host[: -len(default)]
    return host


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


# A template that names a vector format: a vector or JSON extension on the
# path, or a format parameter asking for one (`f=json`, `format=pbf`,
# `outputFormat=application/json`).
_VECTOR_EXTENSION_RE = re.compile(
    r"\.(pbf|mvt|json|geojson|topojson)(\?|$)", re.IGNORECASE
)
_VECTOR_FORMAT_RE = re.compile(
    r"[?&](f|format|outputformat)=(pbf|mvt|json|geojson|application(/|%2F)"
    r"[\w.+%-]*(json|protobuf|vector-tile))(&|$)",
    re.IGNORECASE,
)


def is_image_tile_template(url: str) -> bool:
    """Whether the runtime draws `url`'s tiles as images.

    The runtime's own test (0.13.4, `isImageTileTemplate` and
    `isVectorTileTemplate`): every template counts unless it names a vector
    format, so an address that says nothing about its format - an ArcGIS
    cache's `.../MapServer/tile/{z}/{y}/{x}`, a Google-style `...&x={x}` -
    is imagery. Only image tiles can be warped into a projected map's plane.
    """
    return (
        _VECTOR_EXTENSION_RE.search(url) is None
        and _VECTOR_FORMAT_RE.search(url) is None
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
class WmsStyle:
    """One style a WMS layer offers: its name and its LegendURLs, as
    `(format, address)` in the document's order."""

    name: str
    legend_urls: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WmsCapabilities:
    """What a WMS capabilities document says that a GetMap template, and the
    layer's legend picture, need."""

    version: str
    get_map_url: str | None
    # Every named layer's CRS codes, inherited ones included (a WMS layer
    # inherits its parents' CRSs).
    layer_crs: dict[str, frozenset[str]]
    # The service's GetLegendGraphic address, when it declares one.
    get_legend_graphic_url: str | None = None
    # Every named layer's styles, inherited ones included, and its title.
    layer_styles: dict[str, tuple[WmsStyle, ...]] = field(default_factory=dict)
    layer_titles: dict[str, str] = field(default_factory=dict)
    # The service's GetFeatureInfo address and the answers it offers, in the
    # document's order, and whether each named layer answers it (`queryable`,
    # inherited from its parents as QGIS inherits it).
    get_feature_info_url: str | None = None
    feature_info_formats: tuple[str, ...] = ()
    layer_queryable: dict[str, bool] = field(default_factory=dict)

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
    request = _child(capability, "Request")
    # 1.3.0 declares GetLegendGraphic as the SLD extension's
    # `sld:GetLegendGraphic`; local names make it the same element.
    get_map = _get_address(request, "GetMap")
    get_legend_graphic = _get_address(request, "GetLegendGraphic")
    get_feature_info = _get_address(request, "GetFeatureInfo")
    info = _child(request, "GetFeatureInfo") if request is not None else None
    info_formats = tuple(
        (element.text or "").strip()
        for element in (_children(info, "Format") if info is not None else ())
        if (element.text or "").strip()
    )

    layer_crs: dict[str, frozenset[str]] = {}
    layer_styles: dict[str, tuple[WmsStyle, ...]] = {}
    layer_titles: dict[str, str] = {}
    layer_queryable: dict[str, bool] = {}

    def walk(
        layer: ET.Element,
        inherited: frozenset[str],
        inherited_styles: tuple[WmsStyle, ...],
        inherited_queryable: bool = False,
    ) -> None:
        codes = set(inherited)
        for name in ("CRS", "SRS"):
            for element in _children(layer, name):
                # 1.1.1 allowed several codes in one element, space-separated.
                codes.update((element.text or "").split())
        own = frozenset(codes)
        name = _text(layer, "Name")
        styles = list(inherited_styles)
        if _child(layer, "Name") is not None:
            # As QGIS inherits them: a parent's LegendURL that names a layer
            # names this one instead.
            styles = [_renamed_style(style, name) for style in styles]
        for element in _children(layer, "Style"):
            style = _parse_style(element)
            # A style named like an inherited one replaces it, as QGIS does
            # for the MapServer habit of repeating them.
            styles = [kept for kept in styles if kept.name != style.name]
            styles.append(style)
        # A layer that says nothing answers as its parent does.
        stated = layer.attrib.get("queryable", "").strip().lower()
        queryable = stated in ("1", "true") if stated else inherited_queryable
        if name:
            layer_crs[name] = own
            layer_styles[name] = tuple(styles)
            layer_titles[name] = _text(layer, "Title")
            layer_queryable[name] = queryable
        for child in _children(layer, "Layer"):
            walk(child, own, tuple(styles), queryable)

    for layer in _children(capability, "Layer"):
        walk(layer, frozenset(), ())
    return WmsCapabilities(
        version=root.attrib.get("version", ""),
        get_map_url=get_map,
        layer_crs=layer_crs,
        get_legend_graphic_url=get_legend_graphic,
        layer_styles=layer_styles,
        layer_titles=layer_titles,
        get_feature_info_url=get_feature_info,
        feature_info_formats=info_formats,
        layer_queryable=layer_queryable,
    )


def _get_address(request: ET.Element | None, operation: str) -> str | None:
    """The HTTP GET address a WMS `<Request>` declares for `operation`."""
    element = _child(request, operation) if request is not None else None
    for dcp in _children(element, "DCPType") if element is not None else ():
        http = _child(dcp, "HTTP")
        get = _child(http, "Get") if http is not None else None
        href = _href(_child(get, "OnlineResource")) if get is not None else None
        if href:
            return href
    return None


def _parse_style(element: ET.Element) -> WmsStyle:
    legend_urls = tuple(
        (_text(legend, "Format"), _href(_child(legend, "OnlineResource")) or "")
        for legend in _children(element, "LegendURL")
    )
    return WmsStyle(_text(element, "Name"), legend_urls)


def _renamed_style(style: WmsStyle, layer: str) -> WmsStyle:
    """`style` inherited by `layer`: in each LegendURL with a query, a
    `LAYER` (else `layer`) parameter names `layer` (`QgsWmsCapabilities::
    parseLayer`, which moves it to the end of the query)."""
    renamed = []
    for image_format, href in style.legend_urls:
        base, mark, query = href.partition("?")
        parts = [part for part in query.split("&") if part]
        if mark and query:
            for key in ("LAYER", "layer"):
                if any(part.partition("=")[0] == key for part in parts):
                    parts = [p for p in parts if p.partition("=")[0] != key]
                    parts.append(f"{key}={quote(layer, safe='')}")
                    href = f"{base}?{'&'.join(parts)}"
                    break
        renamed.append((image_format, href))
    return WmsStyle(style.name, tuple(renamed))


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


# The token the runtime fills with the layer's `time`, for a WMS-T whose
# time a slider steps.
TIME_TOKEN = "{time}"


def bbox_token(crs: str) -> str | None:
    """The extent token the runtime fills for a GetMap in `crs`, or `None`
    when it fills none for it.

    `{bbox-epsg-3857}` for any Web Mercator code, filled exactly from the
    tile; `{bbox-epsg-XXXX}` for another EPSG code and `{bbox-crs84}` for
    CRS:84, each the box enclosing the tile there, in the axis order the
    request's version expects, which the runtime warps into the tile.
    """
    if web_mercator_code(crs) is not None:
        return "{bbox-epsg-3857}"
    code = crs.strip()
    if re.fullmatch(r"(OGC:)?CRS:?84", code, re.IGNORECASE):
        return "{bbox-crs84}"
    match = re.fullmatch(r"EPSG:(\d+)", code, re.IGNORECASE)
    return f"{{bbox-epsg-{match.group(1)}}}" if match else None


def wms_get_map_template(
    url: str,
    version: str,
    layers: Sequence[str],
    styles: Sequence[str],
    image_format: str,
    crs: str,
    extra: Sequence[tuple[str, str]] = (),
    time: str | None = None,
) -> str:
    """The GetMap request QGIS would send, in `crs`, as a tile template.

    `url` is the GetMap address with any parameters of its own, which are
    kept (a MapServer `map=`, say) unless they are one this request sets.
    Layer and style names are percent-encoded one by one and joined with a
    literal comma, as the LAYERS list separator must stay. `extra` are
    further parameters QGIS sends verbatim (FILTER, OPACITIES). `time` is
    the WMS-T `TIME` value QGIS sends, when it sends one, or `TIME_TOKEN`
    for the time a slider steps. `crs` must be one `bbox_token` fills.
    """
    bbox = _bbox_or_refuse(crs)
    pairs = _map_request(
        url, version, "GetMap", layers, styles, image_format, extra, time
    )
    crs_key = "SRS" if version.startswith("1.1") else "CRS"
    pairs += [
        (crs_key, crs),
        ("WIDTH", "{width}"),
        ("HEIGHT", "{height}"),
        ("BBOX", bbox),
    ]
    return _join(_base_address(url), pairs)


def _bbox_or_refuse(crs: str) -> str:
    bbox = bbox_token(crs)
    if bbox is None:
        raise ValueError(f"no extent token for {crs}")
    return bbox


def _base_address(url: str) -> str:
    """`url` without its query."""
    return url.partition("?")[0]


def _map_request(
    url: str,
    version: str,
    request: str,
    layers: Sequence[str],
    styles: Sequence[str],
    image_format: str,
    extra: Sequence[tuple[str, str]],
    time: str | None,
    reserved: frozenset[str] = _WMS_RESERVED,
) -> list[tuple[str, str]]:
    """What a GetMap and a GetFeatureInfo share, up to the extent: the
    address's own parameters, the request, its layers, styles, format and
    TRANSPARENT, `extra` and `time` (see `wms_get_map_template`)."""
    query = url.partition("?")[2]
    reserved = reserved | {"TIME"} if time is not None else reserved
    pairs = [
        (_query_value(key), _query_value(found, safe="/:,"))
        for key, found in _kept_url_parameters(query, reserved)
    ]
    pairs += [
        ("SERVICE", "WMS"),
        ("VERSION", _query_value(version)),
        ("REQUEST", request),
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
    if time is not None:
        written = time if time == TIME_TOKEN else _query_value(time, safe=":/")
        pairs.append(("TIME", written))
    return pairs


# --------------------------------------------------------------------------
# WMS GetFeatureInfo: what a click on the layer asks
# --------------------------------------------------------------------------

# The tokens the page's click script fills (`templates/feature-info.html`),
# beside the extent token of the request's CRS: the size of the small map
# the click is in the middle of, and the clicked pixel in it.
CLICK_TOKENS = ("{width}", "{height}", "{i}", "{j}")
# Extent tokens the page fills itself, from the click: Web Mercator metres,
# and longitude and latitude in the order the request's version names them.
_CLICK_BBOX_TOKENS = frozenset({"{bbox-epsg-3857}", "{bbox-epsg-4326}", "{bbox-crs84}"})
# What QGIS asks for when a source string names no feature count.
DEFAULT_FEATURE_COUNT = 10

# Parameters a GetFeatureInfo writes on top of a GetMap's.
_WMS_INFO_RESERVED = _WMS_RESERVED | {
    "QUERY_LAYERS",
    "INFO_FORMAT",
    "FEATURE_COUNT",
    "I",
    "J",
    "X",
    "Y",
}

# Answers that list what was found field by field, so a click on nothing
# shows nothing. JSON first: one shape whatever the server. Then GML and the
# servers' own XML (QGIS Server's, ArcGIS's). The capabilities documents'
# own type (`vnd.ogc.wms_xml`) and the exception type are no answer.
_JSON_FORMAT = re.compile(
    r"application/((geo|vnd\.geo)\+)?json(\s*;.*)?|application/geojson", re.IGNORECASE
)
_XML_FORMAT = re.compile(r".*(gml|xml).*", re.IGNORECASE)
_NOT_AN_ANSWER = re.compile(r".*(wms_xml|se_xml|se_inimage|se_blank).*", re.IGNORECASE)


@dataclass(frozen=True)
class FeatureInfoRequest:
    """The GetMap a layer's tiles are asked with, which a click's
    GetFeatureInfo repeats: see `wms_get_map_template` for each part."""

    url: str
    version: str
    layers: tuple[str, ...]
    styles: tuple[str, ...]
    image_format: str
    crs: str
    extra: tuple[tuple[str, str], ...] = ()
    time: str | None = None


def feature_info_format(formats: Sequence[str]) -> str | None:
    """The answer to ask a click for, of the `formats` a service offers, or
    `None` when it offers none the page can show.

    A structured one first (JSON, then GML or XML), each in the service's
    own order; then plain text, then HTML, which the page shows as written
    since it cannot tell an empty one from a hit.
    """
    offered = [found.strip() for found in formats if found.strip()]
    for wanted in (
        lambda f: bool(_JSON_FORMAT.fullmatch(f)),
        lambda f: bool(_XML_FORMAT.fullmatch(f)) and not _NOT_AN_ANSWER.fullmatch(f),
        lambda f: f.lower() == "text/plain",
        lambda f: f.lower() == "text/html",
    ):
        chosen = next((found for found in offered if wanted(found)), None)
        if chosen is not None:
            return chosen
    return None


def is_structured_format(info_format: str) -> bool:
    """Whether an answer in `info_format` lists what was found field by
    field (JSON, GML or XML), rather than as text or a page."""
    return info_format.lower() not in ("text/plain", "text/html")


def feature_info_crs(
    capabilities: WmsCapabilities, layers: Sequence[str], crs: str, version: str
) -> str | None:
    """The CRS a click asks `layers` in: `crs`, the one their tiles are
    asked in, when the page can place a click in it (Web Mercator, or
    longitude and latitude); otherwise longitude and latitude, when every
    one of `layers` offers them; else `None`.

    The page has no projection of its own to place a click in any other
    CRS, and a point is the same point in any of them.
    """
    if bbox_token(crs) in _CLICK_BBOX_TOKENS:
        return crs
    candidates = (
        ("EPSG:4326",) if version.startswith("1.1") else ("EPSG:4326", "CRS:84")
    )
    for candidate in candidates:
        if layers and all(
            candidate in {code.upper() for code in capabilities.layer_crs.get(name, ())}
            for name in layers
        ):
            return candidate
    return None


def wms_feature_info_template(
    url: str,
    version: str,
    layers: Sequence[str],
    styles: Sequence[str],
    image_format: str,
    crs: str,
    query_layers: Sequence[str],
    info_format: str,
    feature_count: int = DEFAULT_FEATURE_COUNT,
    extra: Sequence[tuple[str, str]] = (),
    time: str | None = None,
) -> str:
    """The GetFeatureInfo request QGIS's Identify tool sends for a click, as
    a template the page fills.

    The GetMap of a small map around the click (`wms_get_map_template`'s
    parameters, in `crs`, which must be one `feature_info_crs` returns) with
    the layers asked about, the answer wanted and how many features, and the
    clicked pixel: `I`/`J` in WMS 1.3.0, `X`/`Y` in 1.1.1. The page fills
    `CLICK_TOKENS` and the extent token, `{bbox-epsg-4326}` latitude first
    in a 1.3.0 request as the runtime fills it for a tile.
    """
    bbox = _bbox_or_refuse(crs)
    if bbox not in _CLICK_BBOX_TOKENS:
        raise ValueError(f"the page cannot place a click in {crs}")
    pairs = _map_request(
        url,
        version,
        "GetFeatureInfo",
        layers,
        styles,
        image_format,
        extra,
        time,
        reserved=_WMS_INFO_RESERVED,
    )
    legacy = version.startswith("1.1")
    pairs += [
        ("QUERY_LAYERS", ",".join(_query_value(name) for name in query_layers)),
        ("INFO_FORMAT", _query_value(info_format, safe="/")),
        ("FEATURE_COUNT", str(feature_count)),
        ("SRS" if legacy else "CRS", crs),
        ("WIDTH", "{width}"),
        ("HEIGHT", "{height}"),
        ("BBOX", bbox),
        ("X" if legacy else "I", "{i}"),
        ("Y" if legacy else "J", "{j}"),
    ]
    return _join(_base_address(url), pairs)


def _legend_address(capabilities: WmsCapabilities, layer: str, style: str) -> str:
    """Where QGIS asks for `layer`'s legend in `style`
    (`QgsWmsProvider::getLegendGraphicUrl`), or `""` when nowhere.

    The style's first image LegendURL - the "default" style's, or the first
    style's, when the layer is drawn in the service's default - else the
    service's GetLegendGraphic address.
    """
    found = ""
    styles = capabilities.layer_styles.get(layer)
    if styles is not None:
        if style and style != "default":
            chosen = next((s for s in styles if s.name == style), None)
        else:
            chosen = next((s for s in styles if s.name == "default"), None)
            chosen = chosen or (styles[0] if styles else None)
        if chosen is not None:
            found = next(
                (
                    href
                    for image_format, href in chosen.legend_urls
                    if image_format.startswith("image/")
                ),
                "",
            )
    found = found or capabilities.get_legend_graphic_url or ""
    return prepare_url(found) if found else ""


def wms_legend_url(
    capabilities: WmsCapabilities,
    layer: str,
    style: str,
    image_format: str,
    url: str | None = None,
) -> str | None:
    """The legend picture QGIS's legend shows for one WMS sublayer, or `None`
    when QGIS has nowhere to ask for one.

    `QgsWmsProvider::getLegendGraphicFullURL` without the contextual legend
    (which follows the map's view): the address from `_legend_address` - or
    `url`, the layer's own address, for a layer set to ignore the addresses
    the capabilities give - with each GetLegendGraphic parameter the address
    does not already carry, whatever its case. A LegendURL that is a plain
    picture gets them too, as from QGIS, and its server ignores them. The
    names are the same in WMS 1.1.1 and 1.3.0; VERSION is the capabilities'.
    """
    address = (
        prepare_url(url)
        if url is not None
        else _legend_address(capabilities, layer, style)
    )
    if not address:
        return None
    return _legend_request(address, capabilities.version, layer, style, image_format)


def _legend_request(
    address: str, version: str, layer: str, style: str, image_format: str
) -> str:
    """`address` with each GetLegendGraphic parameter it does not already
    carry, whatever its case (`QgsWmsProvider::getLegendGraphicFullURL`)."""
    base, _, query = address.rstrip("?&").partition("?")
    kept = parse_qsl(query, keep_blank_values=True)
    present = {key.upper() for key, _ in kept}
    pairs = [
        (_query_value(key), _query_value(found, safe="/:,")) for key, found in kept
    ]
    for key, found, safe in (
        ("SERVICE", "WMS", ""),
        ("VERSION", version, ""),
        # QGIS's own choice: the capabilities do not say which SLD version.
        ("SLD_VERSION", "1.1.0", ""),
        ("REQUEST", "GetLegendGraphic", ""),
        ("FORMAT", image_format, "/"),
        ("LAYER", layer, ""),
        ("STYLE", style, ""),
        ("TRANSPARENT", "true", ""),
    ):
        if key not in present:
            pairs.append((key, _query_value(found, safe=safe)))
    return _join(base, pairs)


def wms_legend_urls(
    capabilities: WmsCapabilities,
    layers: Sequence[str],
    styles: Sequence[str],
    image_format: str,
    url: str | None = None,
) -> list[tuple[str, str]]:
    """`(title, address)` of the legend picture of each of `layers` that has
    one, in order; see `wms_legend_url`.

    QGIS's own legend shows one sublayer's picture only (the top one's on
    QGIS 4, the bottom one's on 3.44); the web map draws them all, so it shows
    each one's, under the title the capabilities give it (its name when they
    give none).
    """
    found: list[tuple[str, str]] = []
    for index, layer in enumerate(layers):
        style = styles[index] if index < len(styles) else ""
        address = wms_legend_url(capabilities, layer, style, image_format, url)
        if address is not None:
            found.append((capabilities.layer_titles.get(layer) or layer, address))
    return found


# --------------------------------------------------------------------------
# WMTS
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WmtsTiles:
    """A WMTS layer as the runtime takes it.

    On the Web Mercator grid the template names its matrix by `{z}` and the
    zooms are the grid's. On any other grid it names it by `{TileMatrix}`,
    `tile_matrix_set` is the grid as the capabilities state it (JSON), and
    the zooms are the indices of its matrices, coarsest first, as the
    runtime counts them; `tile_size` is then `None`, the grid's own.
    """

    template: str
    min_zoom: int
    max_zoom: int
    tile_size: int | None
    tile_matrix_set: str | None = None


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


def _epsg_or_crs84(code: str) -> bool:
    """Whether the runtime reads `code` as a CRS (`grid-tiles.ts`): an EPSG
    code in any of the spellings capabilities use, or CRS84."""
    text = code.strip()
    return bool(
        re.search(r"CRS84|CRS:84", text, re.IGNORECASE)
        or re.search(r"EPSG(::?|/\d+(\.\d+)*/|/0/)(\d+)$", text, re.IGNORECASE)
        or re.fullmatch(r"\d+", text)
    )


def _grid_tile_matrix_set(matrix_set: ET.Element) -> str | None:
    """The tile matrix set as the runtime's `tile-matrix-set` takes it: the
    WMTS TileMatrixSet as GetCapabilities states it, as JSON. `None` when its
    CRS is one the runtime cannot read or a matrix lacks what places it.

    The corner is kept as written, in the CRS's own axis order, which the
    runtime reads that way (latitude first for a geographic CRS, as WMTS
    writes it).
    """
    supported = _text(matrix_set, "SupportedCRS")
    if not _epsg_or_crs84(supported):
        return None
    matrices: list[dict[str, object]] = []
    for matrix in _children(matrix_set, "TileMatrix"):
        try:
            corner = _text(matrix, "TopLeftCorner")
            if len(corner.split()) != 2:
                return None
            for number in corner.split():
                float(number)
            matrices.append(
                {
                    "identifier": _text(matrix, "Identifier"),
                    "scaleDenominator": float(_text(matrix, "ScaleDenominator")),
                    "topLeftCorner": corner,
                    "tileWidth": int(_text(matrix, "TileWidth")),
                    "tileHeight": int(_text(matrix, "TileHeight")),
                    "matrixWidth": int(_text(matrix, "MatrixWidth")),
                    "matrixHeight": int(_text(matrix, "MatrixHeight")),
                }
            )
        except ValueError:
            return None
    if not matrices or any(not m["identifier"] for m in matrices):
        return None
    return json.dumps(
        {"supportedCRS": supported, "tileMatrix": matrices}, separators=(",", ":")
    )


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
    layer's RESTful `ResourceURL`. On the Web Mercator grid, whose matrix
    identifiers are a fixed prefix and the zoom (`3`, `EPSG:3857:3`), the
    matrix is named by `{z}`, as before the runtime knew other grids. Any
    other grid - a national one, or Web Mercator named another way - is
    named by `{TileMatrix}`, `{TileRow}` and `{TileCol}`, with the grid
    itself in `tile_matrix_set`.
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
    matrices: str | None = None
    tile_size: int | None = None
    if grid is None:
        matrices = _grid_tile_matrix_set(set_element)
        if matrices is None:
            return WmtsRefusal("grid", grid=matrix_set)
        prefix, first = "", 0
        last = len(_children(set_element, "TileMatrix")) - 1
        matrix, row, column = "{TileMatrix}", "{TileRow}", "{TileCol}"
    else:
        prefix, zooms, tile_size = grid
        first, last = zooms[0], zooms[-1]
        matrix, row, column = "{z}", "{y}", "{x}"

    def tiles(template: str) -> WmtsTiles:
        return WmtsTiles(template, first, last, tile_size, matrices)

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
            ("TILEMATRIX", _query_value(prefix) + matrix),
        ]
        pairs += [
            (_query_value(key), _query_value(found, safe=":,"))
            for key, found in sorted(dimensions.items())
        ]
        pairs += [("TILEROW", row), ("TILECOL", column)]
        return tiles(_join(base, pairs))

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
    template = template.replace("\0z\0", matrix).replace("\0y\0", row)
    template = template.replace("\0x\0", column)
    if not is_image_tile_template(template):
        # The address names a vector format, so the runtime would decode the
        # tiles as data, whatever format the source string asks for.
        return WmtsRefusal("not imagery")
    return tiles(template)


def wmts_legend_url(
    document: bytes | None, layer: str, style: str, image_format: str, source: str
) -> str | None:
    """The legend picture QGIS's legend shows for a WMTS layer in `style`,
    or `None` when the capabilities give it none.

    `QgsWmsProvider::getLegendGraphicUrl` for a WMTS: the style's LegendURL
    in the layer's format (the last such), else its first - a WMTS has no
    GetLegendGraphic request to build one from. Asked as written when the
    layer's `source` string reads as a WMTS address (`isUrlForWMTS`, on the
    string as stored, so a KVP address QGIS stored encoded does not), and
    otherwise with the GetLegendGraphic parameters, as QGIS asks it.
    """
    root = _parse(document)
    contents = _child(root, "Contents") if root is not None else None
    if contents is None:
        return None
    layer_element = next(
        (
            element
            for element in _children(contents, "Layer")
            if _text(element, "Identifier") == layer
        ),
        None,
    )
    if layer_element is None:
        return None
    style_element = next(
        (
            element
            for element in _children(layer_element, "Style")
            if _text(element, "Identifier") == style
        ),
        None,
    )
    if style_element is None:
        return None
    # QGIS reads the OWS form, `<legendURL>` with `<format>` and `<href>`
    # children, then the first standard `<LegendURL format xlink:href>`.
    legends = [
        (_text(element, "format"), _text(element, "href"))
        for element in _children(style_element, "legendURL")
    ]
    standard = _child(style_element, "LegendURL")
    if standard is not None:
        legends.append((standard.attrib.get("format", ""), _href(standard) or ""))
    matching = [href for found, href in legends if found == image_format]
    href = matching[-1] if matching else (legends[0][1] if legends else "")
    if not href:
        return None
    if is_wmts_url(source):
        return prepare_url(href).rstrip("?&")
    version = root.attrib.get("version", "") if root is not None else ""
    return _legend_request(prepare_url(href), version, layer, style, image_format)


def parse_tile_dimensions(text: str) -> dict[str, str]:
    """QGIS's `tileDimensions=` value, `name=value;name=value`, as a dict."""
    found: dict[str, str] = {}
    for part in text.split(";"):
        if not part:
            continue
        key, _, rest = part.partition("=")
        found[key] = rest
    return found


# The CRSs the runtime places with no lookup (OnlyMap 0.13.4's `crs.ts`
# table, and the UTM zones it writes from their codes); for any other the
# reader's browser asks `https://epsg.io/<code>.proj4` once.
_RUNTIME_CRS_CODES = frozenset(
    {
        2056,
        2154,
        2193,
        2326,
        3006,
        3035,
        3414,
        3577,
        3826,
        3857,
        4167,
        4258,
        4269,
        4283,
        4326,
        5070,
        5179,
        6668,
        7844,
        25832,
        25833,
        27700,
        28992,
        31254,
        31255,
        31256,
        32632,
        32633,
    }
)
_RUNTIME_UTM_RANGES = (
    (32601, 32660),
    (32701, 32760),
    (25828, 25838),
    (26901, 26923),
    (28348, 28358),
    (7846, 7859),
)


def epsg_number(code: str) -> int | None:
    """The EPSG number `code` names, in any spelling capabilities use; 4326
    for CRS:84 and CRS84, which are the same longitude and latitude."""
    text = code.strip()
    if re.search(r"CRS:?84", text, re.IGNORECASE):
        return 4326
    match = re.search(r"EPSG\D*?(\d+)\s*$", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def runtime_knows_crs(code: str) -> bool:
    """Whether the runtime places `code` without asking epsg.io for it."""
    number = epsg_number(code)
    if number is None:
        return False
    return number in _RUNTIME_CRS_CODES or any(
        low <= number <= high for low, high in _RUNTIME_UTM_RANGES
    )


# --------------------------------------------------------------------------
# WMS-T
# --------------------------------------------------------------------------

# The most instants the runtime's time slider steps through
# (`time-dimension.ts`'s `MAX_TIME_STEPS`); it drops any after them.
MAX_TIME_STEPS = 2000

_PERIOD_RE = re.compile(
    r"^P(?:(\d+(?:\.\d+)?)Y)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)W)?"
    r"(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?"
    r"(?:(\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)


def parse_instant(text: str) -> datetime | None:
    """One instant of a time dimension, in UTC, as the runtime reads it: a
    year or a month is its first day, and a time without a zone is UTC."""
    text = text.strip()
    if re.fullmatch(r"\d{4}", text):
        text += "-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    if "T" in text and not re.search(r"(Z|[+-]\d{2}:?\d{2})$", text, re.IGNORECASE):
        text += "Z"
    found = from_iso(text)
    if found is None:
        return None
    if found.tzinfo is None:
        found = found.replace(tzinfo=timezone.utc)
    return found.astimezone(timezone.utc)


def _period(text: str) -> tuple[int, int, float, float, float, float] | None:
    """An ISO 8601 period as (years, months, days, hours, minutes, seconds)."""
    match = _PERIOD_RE.match(text.strip())
    if match is None or text.strip().upper() == "P" or text.strip().endswith("T"):
        return None
    years, months, weeks, days, hours, minutes, seconds = (
        float(part) if part else 0.0 for part in match.groups()
    )
    if not (years.is_integer() and months.is_integer()):
        # The runtime adds a fractional month as JavaScript dates do; no
        # service publishes one, so such an interval is left out here.
        return None
    period = (int(years), int(months), weeks * 7 + days, hours, minutes, seconds)
    return period if any(part > 0 for part in period) else None


def _step(
    start: datetime, period: tuple[int, int, float, float, float, float], count: int
) -> datetime:
    """`start` plus `count` periods, as the runtime counts them: whole months
    first, the day kept or cut to the month's last, then the rest."""
    years, months, days, hours, minutes, seconds = period
    month = start.month - 1 + (years * 12 + months) * count
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    moved = start.replace(year=year, month=month, day=day)
    return moved + timedelta(
        seconds=(days * 86400 + (hours * 60 + minutes) * 60 + seconds) * count
    )


def _written(instant: datetime, precision: str) -> str:
    """`instant` as the runtime writes an interval's step: to the precision
    of the interval's start, seconds dropped of zero milliseconds."""
    text = instant.strftime("%Y-%m-%dT%H:%M:%S")
    milliseconds = instant.microsecond // 1000
    text += f".{milliseconds:03d}Z" if milliseconds else "Z"
    return {"year": text[:4], "month": text[:7], "date": text[:10]}.get(precision, text)


def parse_time_dimension(text: str, limit: int | None = None) -> list[str]:
    """Every instant in a WMS time dimension, in order, spelled as the
    runtime's time slider spells them (`time-dimension.ts`'s
    `parseTimeDimension`), up to `limit`.

    A comma list of single instants (kept as written) and intervals
    `start/end/period` (WMS 1.3.0 Annex C), which step from the start to the
    end. An item the runtime cannot read, and a `start/end` with no period,
    is left out, as the runtime leaves it out.
    """
    found: list[str] = []
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split("/")
        if len(parts) == 1:
            found.append(item)
        elif len(parts) == 3:
            start, end = parse_instant(parts[0]), parse_instant(parts[1])
            period = _period(parts[2])
            if start is None or end is None or period is None:
                continue
            head = parts[0].strip()
            precision = (
                "year"
                if re.fullmatch(r"\d{4}", head)
                else "month"
                if re.fullmatch(r"\d{4}-\d{2}", head)
                else "date"
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head)
                else "datetime"
            )
            count, instant = 0, start
            while instant <= end and (limit is None or len(found) < limit):
                found.append(_written(instant, precision))
                count += 1
                try:
                    instant = _step(start, period, count)
                except (OverflowError, ValueError):
                    break  # past the last year a date can hold
        if limit is not None and len(found) >= limit:
            return found[:limit]
    return found
