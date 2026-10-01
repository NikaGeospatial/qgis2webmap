"""What the export knows about each layer and the whole map, said in words.

The fidelity report says what changes. It does not say what a layer *is* - how
many features, which field drives its colours, whether it has labels, at which
zooms it shows, how much data it adds to the file - and those are exactly the
facts a reader needs to judge whether a change matters. "Labels: Changed" means
one thing on a layer of twelve towns and another on fifty thousand parcels.

Every fact here is read from the export model the dialog already holds, so
showing them costs no extra trip into QGIS. Each is a `Fact` with a label and a
self-describing value: the tab prints the values in one line under a layer's
name and the label-value pairs in its tooltip.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .export_ir import (
    ExportLayer,
    ExportProject,
    ExtentSource,
    GeometryKind,
    OutputMode,
    RendererKind,
    SourceKind,
)

GEOMETRY_WORDS = {
    GeometryKind.POINT: "Points",
    GeometryKind.LINE: "Lines",
    GeometryKind.POLYGON: "Polygons",
    GeometryKind.RASTER: "Raster",
    GeometryKind.UNKNOWN: "Unknown geometry",
}

SOURCE_WORDS = {
    SourceKind.FILE: "From a file",
    SourceKind.DATABASE: "From a database",
    SourceKind.SERVICE: "From a web service",
    SourceKind.MEMORY: "Temporary (memory) layer",
}

OUTPUT_WORDS = {
    OutputMode.STANDALONE_HTML: "Standalone HTML",
    OutputMode.SHARE_ZIP: "Share ZIP",
    OutputMode.FOLDER: "Folder",
}

SEPARATOR = " · "


@dataclass(frozen=True)
class Fact:
    """One thing known about a layer or the map. `value` reads on its own."""

    label: str
    value: str


@dataclass(frozen=True)
class ReportFacts:
    """Facts for the whole map, and per layer by layer id."""

    map: tuple[Fact, ...] = ()
    layers: Mapping[str, tuple[Fact, ...]] = field(default_factory=dict)

    def for_layer(self, layer_id: str | None) -> tuple[Fact, ...]:
        if layer_id is None:
            return ()
        return self.layers.get(layer_id, ())


def facts_line(facts: Iterable[Fact]) -> str:
    """The values in one line: "Points · 240 features · Labels from 'name'"."""
    return SEPARATOR.join(fact.value for fact in facts)


def facts_lines(facts: Iterable[Fact]) -> str:
    """One "Label: value" per line, for a tooltip."""
    return "\n".join(f"{fact.label}: {fact.value}" for fact in facts)


def format_bytes(count: int) -> str:
    """ "512 bytes", "12 KB", "3.4 MB" - one decimal only where it helps."""
    if count < 1024:
        return f"{count} byte{'' if count == 1 else 's'}"
    kilobytes = count / 1024
    if kilobytes < 1024:
        return f"{kilobytes:.0f} KB"
    megabytes = kilobytes / 1024
    if megabytes < 10:
        return f"{megabytes:.1f} MB"
    return f"{megabytes:.0f} MB"


def report_facts(
    project: ExportProject, data_bytes: Mapping[str, int] | None = None
) -> ReportFacts:
    """Facts for every layer the export read, and for the map as a whole.

    `data_bytes` is each layer's share of the file by layer id, measured by
    the caller - packaging owns that measurement. A layer missing from it
    simply has no size fact, rather than a guessed one.
    """
    sizes = dict(data_bytes or {})
    total = sum(sizes.values()) if sizes else None
    return ReportFacts(
        map=map_facts(project, total),
        layers={
            layer.layer_id: layer_facts(layer, sizes.get(layer.layer_id))
            for layer in project.layers
        },
    )


def layer_facts(layer: ExportLayer, data_bytes: int | None = None) -> tuple[Fact, ...]:
    """What the export knows about one layer, most telling first."""
    facts = _raster_facts(layer) if layer.raster is not None else _vector_facts(layer)
    if not layer.visible:
        facts.append(Fact("Visibility", "Hidden in QGIS"))
    if layer.opacity < 1.0:
        facts.append(Fact("Opacity", f"{layer.opacity:.0%} opacity"))
    if layer.visible_zoom_range is not None:
        low, high = layer.visible_zoom_range
        facts.append(Fact("Zoom range", f"Shown at zoom {low:g}-{high:g}"))
    if layer.group_path:
        facts.append(Fact("Group", "In " + " > ".join(layer.group_path)))
    source = SOURCE_WORDS.get(layer.source_kind)
    if source:
        facts.append(Fact("Source", source))
    if data_bytes is not None:
        facts.append(Fact("Data", f"{format_bytes(data_bytes)} of data"))
    return tuple(facts)


def map_facts(
    project: ExportProject, data_bytes: int | None = None
) -> tuple[Fact, ...]:
    """What the export knows about the whole map."""
    settings = project.settings
    exported = project.exportable_layers
    rasters = sum(1 for layer in exported if layer.raster is not None)
    layers = _plural(len(exported), "layer")
    if rasters:
        layers += f" ({_plural(len(exported) - rasters, 'vector')}, {rasters} raster)"
    facts = [
        Fact("Layers", layers),
        Fact("Features", _plural(project.total_features, "feature")),
    ]
    if data_bytes is not None:
        facts.append(Fact("Data", f"About {format_bytes(data_bytes)} of data"))
    facts.append(Fact("Output", OUTPUT_WORDS.get(settings.output_mode, "")))
    if project.source_crs:
        facts.append(Fact("Project CRS", f"Project CRS {project.source_crs}"))
    if project.extent is not None:
        where = (
            "the QGIS view"
            if settings.extent_source is ExtentSource.CANVAS
            else "the data"
        )
        facts.append(Fact("Opens on", f"Opens on {where}: {_extent(project)}"))
    if settings.clip_to_extent:
        facts.append(Fact("Clip", "Only features in the QGIS view"))
    if settings.quantize_precision is not None:
        facts.append(
            Fact(
                "Precision",
                f"Coordinates rounded to {settings.quantize_precision} places",
            )
        )
    facts.append(
        Fact(
            "Basemap",
            "No basemap"
            if settings.basemap == "none"
            else f"Basemap '{settings.basemap}'",
        )
    )
    if settings.terrain != "none":
        facts.append(Fact("Relief", f"Relief '{settings.terrain}'"))
    return tuple(fact for fact in facts if fact.value)


def _vector_facts(layer: ExportLayer) -> list[Fact]:
    facts = [
        Fact("Geometry", GEOMETRY_WORDS[layer.geometry_kind]),
        Fact("Features", _plural(layer.feature_count, "feature")),
    ]
    facts.append(Fact("Symbology", _renderer(layer)))
    if layer.icon_atlas is not None:
        count = len(layer.icon_atlas.mapping)
        facts.append(Fact("Markers", f"{_plural(count, 'marker')} drawn as images"))
    if layer.elevation.is_set:
        elevation = layer.elevation
        height = (
            f"height from '{elevation.height_field}'"
            if elevation.height_field
            else f"{elevation.height:g} m high"
        )
        facts.append(Fact("3D", f"Extruded, {height}"))
    labeling = layer.labeling
    if not labeling.enabled:
        facts.append(Fact("Labels", "No labels"))
    elif labeling.field_name:
        facts.append(Fact("Labels", f"Labels from '{labeling.field_name}'"))
    else:
        facts.append(Fact("Labels", "Labelled"))
    popup = layer.popup
    if not popup.enabled:
        facts.append(Fact("Popups", "Popups off"))
    else:
        shown = len(popup.visible_fields)
        total = len(popup.fields)
        hover = " on hover" if popup.on_hover else ""
        facts.append(Fact("Popups", f"Popups{hover} show {shown} of {total} fields"))
    in_file = _fields_in_file(layer)
    if in_file is not None:
        facts.append(Fact("Fields", f"{_plural(in_file, 'field')} in the file"))
    return facts


def _raster_facts(layer: ExportLayer) -> list[Fact]:
    raster = layer.raster
    assert raster is not None  # the caller checked; mypy cannot see it
    facts = [
        Fact("Type", "Raster"),
        Fact("Size", f"{raster.pixel_width} x {raster.pixel_height} pixels"),
        Fact("Bands", _plural(raster.band_count, "band")),
    ]
    if raster.bands:
        facts.append(
            Fact("Composite", "Bands " + ", ".join(str(b) for b in raster.bands))
        )
    if raster.source_crs:
        facts.append(Fact("CRS", raster.source_crs))
    if raster.resolution_x is not None and raster.resolution_y is not None:
        facts.append(
            Fact(
                "Pixel size",
                f"{abs(raster.resolution_x):.6g} x {abs(raster.resolution_y):.6g} "
                "per pixel (CRS units)",
            )
        )
    if raster.nodata is not None:
        facts.append(Fact("No data", f"No-data value {raster.nodata:g}"))
    if raster.style_qml is not None:
        facts.append(Fact("Colours", "QGIS colours baked into the pixels"))
    if raster.is_cog:
        facts.append(Fact("Format", "Already a Cloud Optimized GeoTIFF"))
    return facts


def _renderer(layer: ExportLayer) -> str:
    renderer = layer.renderer
    if renderer.kind is RendererKind.SINGLE:
        return "Single symbol"
    if renderer.kind is RendererKind.CATEGORIZED:
        text = f"Categorized on '{renderer.field_name}', " + _plural(
            len(renderer.categories), "class", "classes"
        )
        if renderer.hidden_values:
            text += f" ({len(renderer.hidden_values)} switched off)"
        return text
    if renderer.kind is RendererKind.GRADUATED:
        method = renderer.classification.value.replace("_", " ")
        text = f"Graduated on '{renderer.field_name}', " + _plural(
            len(renderer.classes), "class", "classes"
        )
        return text if method == "unknown" else f"{text} ({method})"
    return "Symbology not translated"


def _fields_in_file(layer: ExportLayer) -> int | None:
    """Attributes the file carries per feature, from the first feature.

    Every feature of a layer is written with the same properties, so the first
    one speaks for all. `None` when there is no feature to look at.
    """
    geojson = layer.geojson
    if not geojson:
        return None
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        return None
    first = features[0]
    properties = first.get("properties") if isinstance(first, dict) else None
    return len(properties) if isinstance(properties, dict) else 0


def _extent(project: ExportProject) -> str:
    extent = project.extent
    assert extent is not None  # the caller checked; mypy cannot see it
    return (
        f"{_longitude(extent.west)} to {_longitude(extent.east)}, "
        f"{_latitude(extent.south)} to {_latitude(extent.north)}"
    )


def _longitude(value: float) -> str:
    return f"{abs(value):.2f}°{'W' if value < 0 else 'E'}"


def _latitude(value: float) -> str:
    return f"{abs(value):.2f}°{'S' if value < 0 else 'N'}"


def _plural(count: int, noun: str, plural: str | None = None) -> str:
    word = noun if count == 1 else (plural or f"{noun}s")
    return f"{count:,} {word}"
