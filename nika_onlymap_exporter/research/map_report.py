"""The `kind: "map"` report, built from the export the plugin already read.

Everything here comes from the `ExportProject` the export or publish just used,
plus a handful of facts about the layers' *sources* (`ProjectFacts`) that the
export model does not carry. No layer is re-read, and no feature value or
geometry is looked at: the only numbers taken from the data are the feature
count, reported as a band, and the map's extent, reported as a size band and a
whole-degree cell.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from ..core.export_ir import (
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    OutputMode,
    RendererKind,
    SourceKind,
)
from .envelope import KIND_MAP, SCHEMA_VERSION, Environment
from .profile import Profile, ProfileWire
from .scrub import scrub

MAX_LAYERS = 100
MAX_FIELDS = 50
# The server refuses a body over 64 KB with a 413. The report is kept under
# 60 KB so a longer version string or profile can never tip it over.
MAX_REPORT_BYTES = 60 * 1024

LAYER_KINDS = ("point", "line", "polygon", "raster", "mesh", "other")
LAYER_SOURCES = ("file", "database", "web_service", "memory", "other")
LAYER_STYLES = ("single", "categorised", "graduated", "rule", "heatmap", "other")
OUTPUTS = ("html", "zip", "folder", "hosted")
FEATURES_USED = (
    "legend",
    "popups",
    "relief",
    "extrusion",
    "raster",
    "labels",
    "zoom_range",
    "password",
)

# Only formats on this list are named; anything else is `other`. A format is
# read off a file extension or a provider key, and both are free text as far as
# this plugin knows - an allowlist is what keeps a strange one from carrying
# something that is not a format at all.
LAYER_FORMATS = frozenset(
    {
        "gpkg",
        "shp",
        "geojson",
        "json",
        "csv",
        "kml",
        "kmz",
        "gpx",
        "gml",
        "dxf",
        "fgb",
        "parquet",
        "sqlite",
        "spatialite",
        "tab",
        "mif",
        "gdb",
        "tif",
        "jpg",
        "png",
        "jp2",
        "vrt",
        "ecw",
        "asc",
        "nc",
        "wms",
        "wmts",
        "wfs",
        "xyz",
        "arcgis",
        "postgres",
        "mssql",
        "oracle",
        "memory",
        "other",
    }
)

# (upper bound exclusive, band). Checked in order; the last band has no bound.
FEATURE_BANDS = (
    (1, "0"),
    (101, "1-100"),
    (1_001, "101-1k"),
    (10_001, "1k-10k"),
    (100_001, "10k-100k"),
)
FEATURE_BAND_MAX = "100k+"

# (upper bound exclusive in km, band).
EXTENT_BANDS = (
    (2.0, "site"),
    (50.0, "city"),
    (500.0, "region"),
    (3000.0, "country"),
)
EXTENT_BAND_MAX = "world"

EARTH_RADIUS_KM = 6371.0088

_AUTHID = re.compile(r"^[A-Z][A-Z0-9_]{0,15}:[A-Za-z0-9_.\-]{1,24}$")
_PRESET = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,39}$")

_KIND_BY_GEOMETRY = {
    GeometryKind.POINT: "point",
    GeometryKind.LINE: "line",
    GeometryKind.POLYGON: "polygon",
    GeometryKind.RASTER: "raster",
}

_SOURCE_BY_KIND = {
    SourceKind.FILE: "file",
    SourceKind.DATABASE: "database",
    SourceKind.SERVICE: "web_service",
    SourceKind.MEMORY: "memory",
}

_STYLE_BY_RENDERER = {
    RendererKind.SINGLE: "single",
    RendererKind.CATEGORIZED: "categorised",
    RendererKind.GRADUATED: "graduated",
}

# `RendererSpec.unsupported_reason` records the QGIS class name for renderers
# the export does not translate, which is exactly what tells these two apart.
_STYLE_BY_UNSUPPORTED = {
    "QgsRuleBasedRenderer": "rule",
    "QgsHeatmapRenderer": "heatmap",
}

_OUTPUT_BY_MODE = {
    OutputMode.STANDALONE_HTML: "html",
    OutputMode.SHARE_ZIP: "zip",
    OutputMode.FOLDER: "folder",
}


@dataclass(frozen=True)
class LayerFacts:
    """What the export model does not say about a layer's source."""

    format: str = "other"
    has_time_field: bool = False


@dataclass(frozen=True)
class ProjectFacts:
    """Source facts per layer id, and whether the project's time settings are on."""

    layers: Mapping[str, LayerFacts] = field(default_factory=dict)
    temporal: bool = False


class LayerWire(TypedDict):
    name: str
    kind: str
    source: str
    format: str
    features: str
    fields: list[str]
    has_time_field: bool
    labelled: bool
    popup: bool
    style: str


class MapReportWire(TypedDict):
    schema: int
    kind: str
    plugin_version: str
    qgis_version: str
    os: str
    profile: ProfileWire
    map_id: str
    utc_offset_hours: int
    title: str | None
    layers: list[LayerWire]
    crs: str | None
    extent_band: str | None
    cell: list[int] | None
    basemap: str | None
    terrain: str | None
    features_used: list[str]
    output: str
    temporal: bool


def feature_band(count: int) -> str:
    for bound, band in FEATURE_BANDS:
        if count < bound:
            return band
    return FEATURE_BAND_MAX


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def extent_size_km(extent: Extent) -> float:
    """The extent's diagonal on the ground, from its south-west to north-east.

    `Extent` is WGS84 degrees. The longitude span is taken from
    `width_degrees`, which already accounts for a box that wraps the
    antimeridian; a span over 180 degrees is measured the long way round by
    reporting the whole world, since the great-circle distance would fold back.
    """
    width = extent.width_degrees
    if width >= 180.0:
        return math.inf
    return haversine_km(extent.south, 0.0, extent.north, width)


def extent_band(extent: Extent | None) -> str | None:
    if extent is None:
        return None
    size = extent_size_km(extent)
    for bound, band in EXTENT_BANDS:
        if size < bound:
            return band
    return EXTENT_BAND_MAX


def extent_cell(extent: Extent | None) -> list[int] | None:
    """The extent's centre, rounded to whole degrees, as `[lat, lon]`."""
    if extent is None:
        return None
    lon, lat = extent.center
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    lat_cell = max(-90, min(90, math.floor(lat + 0.5)))
    lon_cell = math.floor(lon + 0.5)
    if lon_cell > 180:
        lon_cell -= 360
    if lon_cell < -180:
        lon_cell += 360
    return [int(lat_cell), int(lon_cell)]


def layer_style(layer: ExportLayer) -> str:
    renderer = layer.renderer
    if renderer.kind in _STYLE_BY_RENDERER:
        return _STYLE_BY_RENDERER[renderer.kind]
    return _STYLE_BY_UNSUPPORTED.get(renderer.unsupported_reason or "", "other")


def normalise_format(value: str | None) -> str:
    text = (value or "").strip().lower().lstrip(".")
    if text in ("tiff", "gtiff"):
        text = "tif"
    if text == "jpeg":
        text = "jpg"
    return text if text in LAYER_FORMATS else "other"


def _layer_fields(layer: ExportLayer) -> list[str]:
    """Field names only, from the popup model - never a value.

    `PopupSpec.fields` lists every attribute the layer has (hidden ones
    included, marked by mode) except binary columns no popup can print.
    """
    names: list[str] = []
    for spec in layer.popup.fields:
        name = scrub(spec.name)
        if name and name not in names:
            names.append(name)
        if len(names) >= MAX_FIELDS:
            break
    return names


def build_layer(layer: ExportLayer, facts: LayerFacts | None) -> LayerWire:
    known = facts or LayerFacts()
    fmt = normalise_format(known.format)
    if fmt == "other" and layer.raster is not None:
        # The raster's own file is in the model; its extension is the format.
        fmt = normalise_format(_extension(layer.raster.path))
    is_raster = layer.is_raster
    return {
        "name": scrub(layer.name),
        "kind": _KIND_BY_GEOMETRY.get(layer.geometry_kind, "other"),
        "source": _SOURCE_BY_KIND.get(layer.source_kind, "other"),
        "format": fmt,
        "features": feature_band(max(0, layer.feature_count)),
        "fields": [] if is_raster else _layer_fields(layer),
        "has_time_field": known.has_time_field,
        "labelled": bool(layer.labeling.enabled and layer.labeling.field_name),
        "popup": bool(layer.popup.enabled and layer.popup.visible_fields),
        "style": "other" if is_raster else layer_style(layer),
    }


def _extension(path: str) -> str:
    name = re.split(r"[\\/]", path.split("|", 1)[0])[-1]
    return name.rsplit(".", 1)[-1] if "." in name else ""


def features_used(export: ExportProject, *, password: bool = False) -> list[str]:
    settings = export.settings
    layers = export.exportable_layers
    used = {
        "legend": settings.show_legend,
        "popups": any(
            layer.popup.enabled and layer.popup.visible_fields for layer in layers
        ),
        "relief": _preset(settings.terrain) is not None,
        "extrusion": any(layer.elevation.extruded for layer in layers),
        "raster": any(layer.is_raster for layer in layers),
        "labels": any(
            layer.labeling.enabled and layer.labeling.field_name for layer in layers
        ),
        "zoom_range": any(layer.visible_zoom_range for layer in layers),
        "password": password,
    }
    return [name for name in FEATURES_USED if used[name]]


def _preset(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text or text == "none":
        return None
    return text if _PRESET.match(text) else None


def _crs(authid: str | None) -> str | None:
    text = (authid or "").strip()
    return text if _AUTHID.match(text) else None


def output_name(mode: OutputMode, hosted: bool) -> str:
    return "hosted" if hosted else _OUTPUT_BY_MODE.get(mode, "html")


def build_map_report(
    export: ExportProject,
    env: Environment,
    profile: Profile,
    *,
    map_id: str,
    utc_offset_hours: int,
    facts: ProjectFacts | None = None,
    hosted: bool = False,
    password: bool = False,
) -> MapReportWire:
    """The report for one export or publish. Pure: it reads only its arguments.

    `map_id` is the map's random id from the local state (see
    `fingerprint.new_map_id`), and `utc_offset_hours` the machine's offset
    from `envelope.utc_offset_hours`; the caller supplies both.
    """
    known = facts or ProjectFacts()
    layers = [
        build_layer(layer, known.layers.get(layer.layer_id))
        for layer in export.exportable_layers[:MAX_LAYERS]
    ]
    report: MapReportWire = {
        "schema": SCHEMA_VERSION,
        "kind": KIND_MAP,
        "plugin_version": env.plugin_version,
        "qgis_version": env.qgis_version,
        "os": env.os,
        "profile": profile.to_wire(),
        "map_id": map_id,
        "utc_offset_hours": utc_offset_hours,
        "title": scrub(export.title) or None,
        "layers": layers,
        "crs": _crs(export.source_crs),
        "extent_band": extent_band(export.extent),
        "cell": extent_cell(export.extent),
        "basemap": _preset(export.settings.basemap),
        "terrain": _preset(export.settings.terrain),
        "features_used": features_used(export, password=password),
        "output": output_name(export.settings.output_mode, hosted),
        "temporal": known.temporal,
    }
    return fit_to_limit(report)


def encoded_size(report: object) -> int:
    return len(encode(report))


def encode(report: object) -> bytes:
    """The exact bytes sent: compact JSON, UTF-8."""
    return json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fit_to_limit(report: MapReportWire) -> MapReportWire:
    """Trim a report that would exceed the server's body limit.

    100 layers of 50 sixty-character field names is ~300 KB, far past what the
    server accepts, and a report refused for size is a report lost. Fields go
    first, from the largest layers down: every layer is capped at the highest
    field count that fits, so a layer with 50 fields loses some before one
    with 5 loses any, and every layer keeps its name, kind and style. Only if
    no field is left and it still does not fit are layers dropped, from the
    top of the draw order.

    Both searches are binary, so this encodes the report a dozen times at
    most rather than once per field removed.
    """
    if encoded_size(report) <= MAX_REPORT_BYTES:
        return report
    full = [list(layer["fields"]) for layer in report["layers"]]

    def fits_with_cap(cap: int) -> bool:
        for layer, fields in zip(report["layers"], full):
            layer["fields"] = fields[:cap]
        return encoded_size(report) <= MAX_REPORT_BYTES

    low, high = 0, max((len(fields) for fields in full), default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if fits_with_cap(middle):
            low = middle
        else:
            high = middle - 1
    if fits_with_cap(low):
        return report

    layers = report["layers"]
    low, high = 0, len(layers)
    while low < high:
        middle = (low + high + 1) // 2
        report["layers"] = layers[:middle]
        if encoded_size(report) <= MAX_REPORT_BYTES:
            low = middle
        else:
            high = middle - 1
    report["layers"] = layers[:low]
    return report
