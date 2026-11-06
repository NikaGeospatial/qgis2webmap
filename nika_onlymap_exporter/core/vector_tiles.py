"""Vector tiles, read as tiles: PMTiles archives and vector tile services.

The runtime (OnlyMap 0.13.4 and later) draws an `MVTLayer` from a PMTiles
archive (`data="./tiles.pmtiles"`, read with HTTP range requests) and draws a
vector tile service in its publisher's MapLibre style (`vector-style`). This
module reads the two shapes QGIS gives them:

* **A PMTiles archive is a vector layer.** QGIS 3.44 and 4.2 have no PMTiles
  tile provider; GDAL's PMTiles driver opens the archive, and QGIS adds each of
  its layers as an `ogr` vector layer of its own (`tiles.pmtiles|layername=
  roads`), styled like any other. GDAL hands QGIS the features of one zoom, cut
  at every tile edge, so reading them as features exported pieces of shapes from
  one zoom. Read as tiles instead, the archive travels whole (local) or stays
  where it is (`/vsicurl/https://...`), and the layer draws by its QGIS
  renderer. GDAL reads no raster PMTiles, so QGIS holds no image archive.
* **A vector tile service is a `QgsVectorTileLayer`.** Exported only in its
  publisher's style, and only when that is still what QGIS draws: QGIS turns
  the style into a renderer of its own when the layer is added, and a user who
  restyled the layer must not have the publisher's colours shipped in place of
  theirs (`publisher_style_state`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import re
import struct
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urljoin, urlsplit
from xml.etree import ElementTree

from qgis.core import QgsMapLayerType, QgsProviderRegistry, QgsVectorLayer

from .export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ExportLayer,
    GeometryKind,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    VectorTileSpec,
)
from .fidelity_report import FidelityReportBuilder
from .layer_reader import (
    _cached_capabilities,
    _live_dependency,
    geometry_kind,
    raster_extent,
    read_attribution,
    scale_range,
)
from .manifest_builder import undrawn_left
from .popup_translator import translate_popup
from .renderer_translator import translate_renderer

if TYPE_CHECKING:
    from qgis.core import QgsMapLayer, QgsProject

# The PMTiles v3 header (spec v3): 127 bytes at the start of the archive.
PMTILES_HEADER_BYTES = 127
# The compressions a browser can undo for the runtime: none and gzip. Brotli (3)
# and zstd (4) have no decoder there, so such an archive would draw nothing.
_READABLE_COMPRESSION = {1: "none", 2: "gzip"}
_COMPRESSION_NAMES = {0: "unknown", 3: "brotli", 4: "zstd"}
_PMTILES_MVT = 1


def pmtiles_archive(layer: QgsMapLayer) -> tuple[str, str] | None:
    """`(path, layer name)` of a vector layer GDAL reads from a PMTiles archive,
    or `None` for any other layer. `path` is a file on this computer or a
    `/vsicurl/` address, as QGIS keeps it."""
    if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "ogr":
        return None
    parts = QgsProviderRegistry.instance().decodeUri("ogr", layer.source())
    # A remote archive's `/vsicurl/` comes back apart from its address.
    path = str(parts.get("vsiPrefix") or "") + str(parts.get("path") or "")
    provider = layer.dataProvider()
    storage = provider.storageType() if provider is not None else ""
    if storage != "PMTiles" and not path.lower().endswith(".pmtiles"):
        return None
    return path, str(parts.get("layerName") or "")


def _remote_address(path: str) -> str | None:
    """The web address of a `/vsicurl/` archive, or `None` for a local one."""
    for prefix in ("/vsicurl/", "/vsicurl?url="):
        if path.startswith(prefix):
            address = path[len(prefix) :]
            if urlsplit(address).scheme in ("http", "https"):
                return address
    return None


def read_pmtiles_header(path: str) -> dict[str, int] | None:
    """The archive's tile type, compressions and zoom range, or `None` when
    its header cannot be read or is not a PMTiles v3 one.

    Through GDAL's file layer, so a `/vsicurl/` archive is read from the copy
    GDAL kept when QGIS opened it - the header is the first thing GDAL reads.
    """
    data = b""
    try:
        from osgeo import gdal

        handle = gdal.VSIFOpenL(path, "rb")
        if handle is None:
            return None
        try:
            data = bytes(gdal.VSIFReadL(1, PMTILES_HEADER_BYTES, handle) or b"")
        finally:
            gdal.VSIFCloseL(handle)
    except Exception:
        return None
    if len(data) < PMTILES_HEADER_BYTES or data[:7] != b"PMTiles" or data[7] != 3:
        return None
    internal, tiles, tile_type, min_zoom, max_zoom = struct.unpack_from(
        "<BBBBB", data, 97
    )
    return {
        "internalCompression": internal,
        "tileCompression": tiles,
        "tileType": tile_type,
        "minZoom": min_zoom,
        "maxZoom": max_zoom,
    }


def read_pmtiles_layer(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    *,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
    with_popup: bool = True,
    with_labels: bool = True,
    field_modes: Mapping[str, str] | None = None,
    popup_on_hover: bool = False,
    highlight_color: Color | None = None,
    project: QgsProject | None = None,
    clip_extent: Any | None = None,
    filter_field: str | None = None,
) -> ExportLayer | None:
    """Read a layer of a PMTiles archive as tiles of that archive.

    The layer draws by its QGIS renderer, as any vector layer does, from the
    tiles of its own layer of the archive. What only carried features can
    have - labels, a filter panel, rule-based classes QGIS matches feature by
    feature, markers QGIS draws itself - is reported, not dropped quietly.
    """
    archive = pmtiles_archive(layer)
    if archive is None:
        return None
    path, source_layer = archive
    layer_id = layer.id()
    name = layer.name()
    subject = f"Layer '{name}'"
    remote = _remote_address(path)

    if remote is None and not os.path.isfile(path):
        report.blocked(
            subject,
            f"The PMTiles archive '{os.path.basename(path)}' is missing from "
            "this computer, so there is nothing to carry. Find the file again "
            "(right-click the layer > Change Data Source...), or untick the "
            "layer on the Layers tab.",
            layer_id,
        )
        return None

    header = read_pmtiles_header(path)
    if header is None and remote is None:
        report.unsupported(
            subject,
            "The layer's PMTiles archive could not be read as a version 3 "
            "archive, which is the only kind the web map reads. The layer is "
            "omitted from the map.",
            layer_id,
        )
        return None
    if header is not None:
        unreadable = [
            _COMPRESSION_NAMES.get(code, str(code))
            for code in (header["tileCompression"], header["internalCompression"])
            if code not in _READABLE_COMPRESSION
        ]
        if unreadable:
            report.unsupported(
                subject,
                f"The layer's PMTiles archive is compressed with {unreadable[0]}, "
                "which browsers cannot decompress, so the web map would draw "
                "nothing. Save the layer again as PMTiles from QGIS (right-click "
                "> Export > Save Features As...), which compresses with gzip, "
                "and add that file. The layer is omitted from the map.",
                layer_id,
            )
            return None

    kind = geometry_kind(layer)
    renderer = translate_renderer(layer, report)
    if renderer.field_generated:
        # A rule-based renderer is matched to each feature as it is read,
        # into a property the features carry; tiles carry none of ours.
        representative = renderer.representative_symbol
        renderer = RendererSpec(kind=RendererKind.SINGLE, symbol=representative)
        report.approximated(
            f"Symbology of '{name}'",
            "Rule-based styling is matched feature by feature as features are "
            "exported, and this layer's features stay in its tile archive, so "
            "every feature is drawn in the first rule's symbol.",
            layer_id,
        )
    _report_tile_feature_limits(
        layer, report, name, layer_id, with_labels, clip_extent, filter_field
    )

    popup = (
        translate_popup(
            layer,
            report,
            field_modes=field_modes,
            on_hover=popup_on_hover,
            project=project,
        )
        if with_popup
        else PopupSpec(enabled=False)
    )
    _report_undrawn_features(
        renderer,
        report,
        name,
        layer_id,
        clickable=popup.enabled and bool(popup.visible_fields),
    )

    if remote is not None:
        url, dependency = _live_dependency(remote, name, what="archive address")
    else:
        url = path
        dependency = AssetDependency(
            identifier=path,
            disposition=AssetDisposition.EMBEDDABLE,
            size_bytes=os.path.getsize(path),
        )
    report.preserved(
        subject,
        "Read from its PMTiles archive tile by tile, at every zoom the archive "
        "holds, rather than as features copied into the map.",
        layer_id,
    )
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=kind,
        source_kind=SourceKind.SERVICE if remote is not None else SourceKind.FILE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=renderer,
        popup=popup,
        highlight_color=highlight_color,
        vector_tiles=VectorTileSpec(
            url=url,
            path="" if remote is not None else path,
            source_layer=source_layer,
            min_zoom=header["minZoom"] if header else None,
            max_zoom=header["maxZoom"] if header else None,
            size_bytes=0 if remote is not None else dependency.size_bytes or 0,
            extent=raster_extent(layer),
        ),
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(dependency,),
    )


_OMITTED = "matching features are omitted from the export."


def _report_undrawn_features(
    renderer: RendererSpec,
    report: FidelityReportBuilder,
    name: str,
    layer_id: str,
    clickable: bool,
) -> None:
    """The rows for the features QGIS does not draw, which a layer read as
    tiles leaves in its archive rather than out of the map: the map leaves
    them undrawn (`manifest_builder.undrawn_filter`, `_tile_renderer`), and
    says what it cannot."""
    subject = f"Symbology of '{name}'"
    kept = undrawn_left(renderer)
    if kept == "neighbour colour":
        # The row below says which are drawn after all.
        instead = "matching features stay in the tile archive the map carries."
    elif kept == "clickable" or (
        renderer.kind is RendererKind.CATEGORIZED and not renderer.unmatched_hidden
    ):
        # Undrawn by a class of their own, which the legend lists.
        instead = (
            "matching features are not drawn, though they stay in the tile "
            "archive the map carries, and the legend lists them without a colour."
        )
    else:
        instead = (
            "matching features are not drawn, though they stay in the tile "
            "archive the map carries."
        )
    for item in report.items:
        if item.subject == subject and _OMITTED in item.detail:
            report.rewrite_detail(
                subject, _OMITTED, item.detail.replace(_OMITTED, instead)
            )
            break
    if kept == "clickable" and clickable:
        which = (
            "in a range switched off between drawn ranges, or between two ranges"
            if renderer.kind is RendererKind.GRADUATED
            else "in a class switched off, or in no class"
        )
        report.approximated(
            subject,
            f"The features QGIS does not draw ({which}) are drawn invisible: "
            "they stay in the layer's tile archive, so a click or the pointer "
            "where one lies still finds it and shows its popup.",
            layer_id,
        )
    elif kept == "neighbour colour":
        report.approximated(
            subject,
            "Values in a range switched off between drawn ranges, or between "
            "two ranges, are drawn in the colour of the next range that "
            "claims them: the ranges overlap, which QGIS tests in order and "
            "the map cannot, and the layer's features stay in its tile archive.",
            layer_id,
        )


def _report_tile_feature_limits(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    name: str,
    layer_id: str,
    with_labels: bool,
    clip_extent: Any | None,
    filter_field: str | None,
) -> None:
    """The rows for what a layer read as tiles does without: everything that
    needs its features in the map."""
    if with_labels and layer.labelsEnabled():
        report.unsupported(
            f"Labels on '{name}'",
            "Not exported: the layer's features stay in its tile archive, and "
            "the web map labels only features the map itself carries.",
            layer_id,
        )
    if filter_field:
        report.unsupported(
            f"Filter on '{name}'",
            "Not exported: the filter panel needs every value of the field, "
            "and the layer's features stay in its tile archive.",
            layer_id,
        )
    if clip_extent is not None:
        report.approximated(
            f"Layer '{name}'",
            "The whole tile archive is carried, not only the features in the "
            "view: an archive cannot be cut to an area without being rebuilt.",
            layer_id,
        )
    renderer = layer.renderer()
    symbols = renderer.symbols(_render_context()) if renderer is not None else []
    drawn_by_qgis = any(
        symbol_layer.layerType() != "SimpleMarker"
        or getattr(symbol_layer, "shape", lambda: 0)() != _circle_shape()
        for symbol in symbols
        if symbol.type() == _marker_type()
        for symbol_layer in symbol.symbolLayers()
    )
    if drawn_by_qgis:
        report.approximated(
            f"Symbology of '{name}'",
            "The points are drawn as circles in the symbol's colour: markers "
            "QGIS draws itself (shapes, SVGs, stacked symbols) travel as "
            "pictures for features the map carries, and this layer's stay in "
            "its tile archive.",
            layer_id,
        )


def _render_context() -> Any:
    from qgis.core import QgsRenderContext

    return QgsRenderContext()


def _marker_type() -> Any:
    from qgis.core import Qgis

    return Qgis.SymbolType.Marker


def _circle_shape() -> Any:
    from qgis.core import Qgis

    return Qgis.MarkerShape.Circle


# --------------------------------------------------------------------------
# Vector tile services
# --------------------------------------------------------------------------


class StyleState(str, Enum):
    """Whether a vector tile layer still draws as its publisher's style said.

    `UNCHANGED`: QGIS's style is exactly what QGIS makes of the publisher's.
    `RESTYLED`: it is not - the user changed it, or the publisher's style is
    not the one the layer was added with. `UNKNOWN`: QGIS kept no copy of the
    publisher's style to compare with, and an export reads nothing from the
    network.
    """

    UNCHANGED = "unchanged"
    RESTYLED = "restyled"
    UNKNOWN = "unknown"


def source_parameters(layer: QgsMapLayer) -> dict[str, str]:
    """A vector tile layer's source string as its parameters."""
    return dict(parse_qsl(layer.source() or "", keep_blank_values=True))


def _read_url(url: str) -> bytes | None:
    """What QGIS fetched from `url`, from its network cache only."""
    return _cached_capabilities(url)


def publisher_style(style_url: str) -> dict[str, Any] | None:
    """The publisher's style as QGIS fetched it, or `None` when QGIS kept no
    copy or it is not a style."""
    data = _read_url(style_url)
    if data is None:
        return None
    try:
        style = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return style if isinstance(style, dict) and "layers" in style else None


def _sprites(style: Mapping[str, Any], style_url: str) -> list[tuple[str, str]]:
    """`(category, base address)` of each sprite the style names."""
    sprite = style.get("sprite")
    if isinstance(sprite, str):
        return [("", urljoin(style_url, sprite))]
    if isinstance(sprite, list):
        return [
            (str(entry.get("id", "")), urljoin(style_url, str(entry.get("url", ""))))
            for entry in sprite
            if isinstance(entry, dict) and entry.get("url")
        ]
    return []


def convert_publisher_style(style: Mapping[str, Any], style_url: str) -> Any | None:
    """What QGIS makes of `style` when a layer is added with it: a
    `QgsMapBoxGlStyleConverter` that has converted it, or `None` on failure.

    The steps `QgsVectorTileLayer.loadDefaultStyle` takes, measured on QGIS
    3.44 and 4.2 to give the same renderer and labels: sizes in millimetres at
    96 dpi, and each sprite at twice the resolution (`@2x`, falling back to
    the plain one), read here from QGIS's network cache.
    """
    from qgis.core import (
        Qgis,
        QgsMapBoxGlStyleConversionContext,
        QgsMapBoxGlStyleConverter,
    )
    from qgis.PyQt.QtGui import QImage

    context = QgsMapBoxGlStyleConversionContext()
    context.setTargetUnit(Qgis.RenderUnit.Millimeters)
    context.setPixelSizeConversionFactor(25.4 / 96.0)
    for category, base in _sprites(style, style_url):
        for suffix in ("@2x", ""):
            definitions = _read_url(f"{base}{suffix}.json")
            image = _read_url(f"{base}{suffix}.png")
            if definitions is not None and image is not None:
                try:
                    parsed = json.loads(definitions.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    break
                context.setSprites(QImage.fromData(image), parsed, category)
                break
    converter = QgsMapBoxGlStyleConverter()
    if converter.convert(dict(style), context) != 0:
        return None
    return converter


_UUID = re.compile(r"^\{[0-9a-fA-F-]{36}\}$")


def _canonical(text: str) -> Any:
    """XML as nested tuples with sorted attributes, ignoring the random ids
    QGIS gives symbol layers; a value that is itself XML (a symbol QGIS keeps
    inside an option) is read the same way. Attribute order is not stable
    under Qt 5, so the text alone cannot be compared."""
    try:
        element = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return text

    def walk(node: ElementTree.Element) -> Any:
        attributes = []
        for key, value in sorted(node.attrib.items()):
            if _UUID.match(value):
                value = "{}"
            elif value.lstrip().startswith("<"):
                attributes.append((key, _canonical(value)))
                continue
            attributes.append((key, value))
        return (
            node.tag,
            tuple(attributes),
            (node.text or "").strip(),
            tuple(walk(child) for child in node),
        )

    return walk(element)


def _style_markup(renderer: Any, labeling: Any) -> Any:
    """A vector tile renderer and labeling as comparable markup."""
    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    root = document.createElement("style")
    document.appendChild(root)
    context = QgsReadWriteContext()
    if renderer is not None:
        node = document.createElement("renderer")
        renderer.writeXml(node, context)
        root.appendChild(node)
    if labeling is not None:
        node = document.createElement("labeling")
        labeling.writeXml(node, context)
        root.appendChild(node)
    return _canonical(document.toString())


def publisher_style_state(
    layer: QgsMapLayer, style: Mapping[str, Any] | None, style_url: str
) -> StyleState:
    """Whether `layer` draws exactly as QGIS drew `style` when it was added.

    The publisher's style is converted again, as QGIS converted it, and the
    two renderers and labelings compared whole: any change the user made -
    a colour, a width, a rule switched off, a label - makes them differ. So
    does a publisher who changed the style since, and a project saved by a
    QGIS that converted it differently; both read as restyled, which keeps
    the layer as QGIS draws it rather than risk replacing the user's work.
    """
    if style is None:
        return StyleState.UNKNOWN
    converter = convert_publisher_style(style, style_url)
    if converter is None:
        return StyleState.RESTYLED
    ours = _style_markup(layer.renderer(), layer.labeling())
    theirs = _style_markup(converter.renderer(), converter.labeling())
    return StyleState.UNCHANGED if ours == theirs else StyleState.RESTYLED


def _style_source(style: Mapping[str, Any], url: str) -> str | None:
    """The style's vector source the layer's tiles are (`vector-style-source`):
    `""` for the first, the source's name for another, and `None` when the
    style has several and none can be told to be this layer's.

    A source lists its tile addresses (`tiles`), or names a TileJSON document
    (`url`) that lists them; QGIS keeps no copy of that document, so a TileJSON
    source is this layer's when the tiles sit in the document's own folder, as
    a publisher lays them out (`…/v1.0.0/tiles.json`, `…/v1.0.0/{z}/{x}/{y}`).
    """
    sources = style.get("sources")
    if not isinstance(sources, dict):
        return ""
    vector = [
        (str(key), value)
        for key, value in sources.items()
        if isinstance(value, dict) and value.get("type") == "vector"
    ]
    for index, (key, value) in enumerate(vector):
        tiles = value.get("tiles")
        document = value.get("url")
        if isinstance(tiles, list):
            matched = url in tiles
        elif isinstance(document, str) and "/" in document:
            matched = url.startswith(document.rsplit("/", 1)[0] + "/")
        else:
            matched = False
        if matched:
            return "" if index == 0 else key
    return "" if len(vector) <= 1 else None


# The style layer types the runtime does not draw on a vector tile layer.
_UNDRAWN_STYLE_TYPES = ("background", "raster", "hillshade", "heatmap")


def read_vector_tile_layer(
    layer: QgsMapLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
) -> ExportLayer | None:
    """Read a vector tile service as a live layer in its publisher's style,
    or refuse it with a reason.

    The web map asks the service for tiles itself and draws them by the
    publisher's style (`vector-style`), so nothing is embedded. Only when
    QGIS still draws the layer exactly as that style says: a restyled layer,
    or one whose style QGIS no longer has a copy of, is omitted with a row,
    since the web map cannot draw QGIS's vector tile styling.
    """
    layer_id = layer.id()
    name = layer.name()
    subject = f"Layer '{name}'"
    if layer.type() != QgsMapLayerType.VectorTileLayer:
        return None
    parameters = source_parameters(layer)
    kind = parameters.get("type", "")
    url = parameters.get("url", "")
    style_url = parameters.get("styleUrl", "")

    if kind != "xyz" or not url:
        what = {
            "mbtiles": "an MBTiles file",
            "vtpk": "a vector tile package (.vtpk)",
        }.get(kind, "a source the web map cannot read")
        report.unsupported(
            subject,
            f"This vector tile layer is read from {what}. The web map reads "
            "vector tiles from a PMTiles archive or from a tile address on the "
            "web; convert the tiles to PMTiles, or add the service's own "
            "address, and they export. The layer is omitted from the map.",
            layer_id,
        )
        return None

    tokens = set(re.findall(r"\{(-?[A-Za-z][\w-]*)\}", url))
    if not ({"z", "x"} <= tokens and tokens & {"y", "-y"}) or not tokens <= {
        "z",
        "x",
        "y",
        "-y",
    }:
        report.unsupported(
            subject,
            "This vector tile layer's address uses a pattern the web map "
            "cannot fill (it understands {z}, {x}, {y} and {-y} only). The "
            "layer is omitted from the map.",
            layer_id,
        )
        return None

    if urlsplit(style_url).scheme not in ("http", "https"):
        report.unsupported(
            subject,
            "The web map draws vector tiles in their publisher's style, from "
            "the style address (a style.json URL) the layer was added with, "
            "and this layer has none on the web. Add the layer again with the "
            "service's style URL in its connection, and it exports in that "
            "style. The layer is omitted from the map.",
            layer_id,
        )
        return None

    style = publisher_style(style_url)
    state = publisher_style_state(layer, style, style_url)
    if state is StyleState.UNKNOWN:
        report.unsupported(
            subject,
            "QGIS no longer has a copy of the publisher's style this layer was "
            "added with, so the export cannot tell whether you have restyled "
            "it (an export reads nothing from the internet). Add the layer "
            "again from its connection, and it exports in the publisher's "
            "style. The layer is omitted from the map.",
            layer_id,
        )
        return None
    if state is StyleState.RESTYLED:
        report.unsupported(
            subject,
            "The layer is styled differently from its publisher's style, and "
            "the web map can draw vector tiles only in the publisher's style, "
            "which would lose your changes. The layer is omitted from the map. "
            "To export it in the publisher's style, add the layer again from "
            "its connection.",
            layer_id,
        )
        return None

    if style is None:  # the state is UNKNOWN then, reported above
        return None
    url, dependency = _live_dependency(url, name)
    style_source = _style_source(style, url)
    if style_source is None:
        report.unsupported(
            subject,
            "The publisher's style draws several tile sets, and none of them "
            "is at this layer's address, so the web map cannot tell which part "
            "of the style is this layer's. Add the layer from the address the "
            "style names. The layer is omitted from the map.",
            layer_id,
        )
        return None
    style_url, style_dependency = _live_dependency(
        style_url, name, what="style address"
    )
    report.preserved(
        subject,
        "Drawn in its publisher's style, as QGIS draws it: the style's "
        "colours, line widths, filters and zoom rules.",
        layer_id,
    )
    report.approximated(
        f"Labels on '{name}'",
        "The style's labels, road shields and icons are placed one per "
        "feature, in the browser's own fonts, without QGIS's check that each "
        "has room: where features are dense, such as a road network's "
        "shields, they can pile up on one another, and they sit and look "
        "differently from QGIS's.",
        layer_id,
    )
    undrawn = sorted(
        {
            str(entry.get("type"))
            for entry in style.get("layers", [])
            if isinstance(entry, dict) and entry.get("type") in _UNDRAWN_STYLE_TYPES
        }
    )
    if undrawn:
        report.approximated(
            f"Symbology of '{name}'",
            "The style's "
            + " and ".join(undrawn)
            + " layers are not drawn: the web map draws a vector tile layer's "
            "fills, lines, points, extrusions and labels.",
            layer_id,
        )

    def _zoom(field: str) -> int | None:
        try:
            return int(float(parameters[field]))
        except (KeyError, ValueError):
            return None

    return ExportLayer(
        layer_id=layer_id,
        name=name,
        # As for an XYZ tile layer: drawn whole by the runtime, with no
        # geometry family of its own for the plugin to style.
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=RendererSpec(
            kind=RendererKind.UNSUPPORTED,
            unsupported_reason="Drawn by the publisher's style.",
        ),
        popup=PopupSpec(enabled=False),
        vector_tiles=VectorTileSpec(
            url=url,
            style_url=style_url,
            style_source=style_source,
            min_zoom=_zoom("zmin"),
            max_zoom=_zoom("zmax"),
            extent=raster_extent(layer),
        ),
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(dependency, style_dependency),
    )
