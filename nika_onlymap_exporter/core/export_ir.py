"""The normalized export model -- the boundary between QGIS and everything else.

This module is **pure Python**. It imports no PyQGIS and no Qt, which is what
lets the whole model be unit-tested in CI where neither is available, and what
keeps `Qgs*` objects from leaking into the writer and packaging layers.

Everything here is frozen and uses tuples rather than lists, so a model is
hashable, cannot be mutated behind a caller's back, and serialises to a
byte-stable snapshot. `snapshot()` on any node returns plain JSON-ready data with
deterministic key order -- that determinism is Task 2's acceptance criterion, and
it is also what makes `ArtifactResult` reproducible later.

Design rule inherited from issue #29: nothing a QGIS project expresses may vanish
silently. Anything we cannot translate becomes a `FidelityItem` with a named
reason rather than an omission.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Bumped when the shape of a snapshot changes, so stored snapshots stay
# comparable and a stale fixture fails loudly instead of subtly.
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class GeometryKind(str, Enum):
    """Geometry families we translate. Deliberately coarser than QGIS's WKB
    types -- the renderer cares about point/line/polygon, not about whether a
    polygon is multi-part or Z-aware."""

    POINT = "point"
    LINE = "line"
    POLYGON = "polygon"
    UNKNOWN = "unknown"


class SourceKind(str, Enum):
    """Where a layer's data comes from. Drives packaging decisions: only
    `FILE` and `MEMORY` can be fully embedded in a portable artifact."""

    FILE = "file"
    DATABASE = "database"
    SERVICE = "service"
    MEMORY = "memory"
    UNKNOWN = "unknown"


class RendererKind(str, Enum):
    """The symbology shapes 0.1.0 translates. `UNSUPPORTED` is a first-class
    outcome, not an error -- it carries a reason and lands in the report."""

    SINGLE = "single"
    CATEGORIZED = "categorized"
    GRADUATED = "graduated"
    UNSUPPORTED = "unsupported"


class ClassificationMethod(str, Enum):
    """How a graduated renderer split its range. Recorded because the class
    breaks alone do not tell a reader whether the classification was sensible,
    and because OnlyMap's `color-scale-type` needs a hint from it."""

    EQUAL_INTERVAL = "equal_interval"
    QUANTILE = "quantile"
    NATURAL_BREAKS = "natural_breaks"
    STANDARD_DEVIATION = "standard_deviation"
    PRETTY_BREAKS = "pretty_breaks"
    UNKNOWN = "unknown"


class FidelityStatus(str, Enum):
    """What happened to a property on its way out of QGIS.

    `BLOCKED` is distinct from `UNSUPPORTED`: unsupported means we cannot
    represent it and carried on; blocked means the export must not proceed.
    """

    PRESERVED = "preserved"
    APPROXIMATED = "approximated"
    RASTER_FALLBACK = "raster_fallback"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


class PopupFieldMode(str, Enum):
    """How one attribute renders in a popup.

    Note the default used by `PopupFieldSpec` is `INLINE_WITH_DATA`, not
    `NO_LABEL`. qgis2web defaults to no label, which produces popups of bare
    values (`42 / 22 / 1,569 / NORTHWAY`) that read as broken -- see the
    evaluation's §5.6.
    """

    NO_LABEL = "no_label"
    INLINE_ALWAYS = "inline_always"
    INLINE_WITH_DATA = "inline_with_data"
    HEADER_ALWAYS = "header_always"
    HEADER_WITH_DATA = "header_with_data"
    HIDDEN = "hidden"


class OverlayCorner(str, Enum):
    """Where a title or abstract block sits over the map.

    Screen corners, not map coordinates: a caption pinned to a longitude would
    slide off as soon as the reader panned.

    The two centre positions exist because **all four corners are already
    occupied** by map chrome: the layer switcher top-left, the legend top-right,
    the zoom controls and scale bar bottom-left, and the credit component
    bottom-right. A caption in any corner therefore lands on top of something,
    which is precisely what testing found. The centres are the only free space.
    """

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class ExtentSource(str, Enum):
    """Which extent the exported map opens on.

    `DATA` stays the default: it is antimeridian-aware, which the canvas extent
    cannot be, and it guarantees every feature is visible. `CANVAS` matches what
    the author had on screen, which is what qgis2web does and the only thing it
    offers.
    """

    DATA = "data"
    CANVAS = "canvas"


class AssetDisposition(str, Enum):
    """What packaging can do with a dependency."""

    EMBEDDABLE = "embeddable"
    COPYABLE = "copyable"
    REMOTE = "remote"
    BLOCKING = "blocking"


class OutputMode(str, Enum):
    """The artifact tiers. `STANDALONE_HTML` is the default and the product
    promise; the others exist for data that genuinely will not fit one file, or
    for deliverability (some mail filters quarantine `.html` attachments)."""

    STANDALONE_HTML = "standalone_html"
    SHARE_ZIP = "share_zip"
    FOLDER = "folder"


# --------------------------------------------------------------------------
# Small value types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Color:
    """RGBA with 0-255 channels and 0.0-1.0 alpha.

    Alpha is separated from the channels because QGIS carries symbol opacity and
    layer opacity independently, and both have to survive into CSS.
    """

    r: int
    g: int
    b: int
    a: float = 1.0

    def as_css(self) -> str:
        if self.a >= 1.0:
            return f"#{self.r:02x}{self.g:02x}{self.b:02x}"
        return f"rgba({self.r}, {self.g}, {self.b}, {self.a:.3f})"

    def snapshot(self) -> dict[str, Any]:
        return {"r": self.r, "g": self.g, "b": self.b, "a": round(self.a, 4)}


@dataclass(frozen=True)
class Extent:
    """A geographic bounding box in WGS84 degrees.

    `crosses_antimeridian` is the load-bearing field. A naive min/max box over
    dateline-crossing data spans ~360° of longitude, which is how the incumbent
    opens a regional dataset at 99.8% of the width of the world -- measured on the
    QGIS Alaska sample, which spans -179.13 to +179.78. When this flag is set,
    `west` is east of `east` and consumers must interpret the box as wrapping.
    """

    west: float
    south: float
    east: float
    north: float
    crosses_antimeridian: bool = False

    @property
    def width_degrees(self) -> float:
        if self.crosses_antimeridian:
            return (180.0 - self.west) + (self.east + 180.0)
        return self.east - self.west

    @property
    def center(self) -> tuple[float, float]:
        if self.crosses_antimeridian:
            lon = self.west + self.width_degrees / 2.0
            if lon > 180.0:
                lon -= 360.0
        else:
            lon = (self.west + self.east) / 2.0
        return (lon, (self.south + self.north) / 2.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "west": round(self.west, 9),
            "south": round(self.south, 9),
            "east": round(self.east, 9),
            "north": round(self.north, 9),
            "crossesAntimeridian": self.crosses_antimeridian,
        }


@dataclass(frozen=True)
class ScaleRange:
    """QGIS scale-dependent visibility, as denominators (1:`min`-1:`max`).

    QGIS uses scale denominators where smaller means more zoomed in; OnlyMap uses
    zoom levels where larger means more zoomed in. The inversion is the
    translator's job, not this model's -- we store what QGIS said.
    """

    min_scale: float | None = None
    max_scale: float | None = None

    @property
    def is_set(self) -> bool:
        return self.min_scale is not None or self.max_scale is not None

    def snapshot(self) -> dict[str, Any]:
        return {"minScale": self.min_scale, "maxScale": self.max_scale}


# --------------------------------------------------------------------------
# Symbology
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSpec:
    """One resolved symbol, flattened to what a web renderer can express.

    QGIS symbols are trees of symbol layers with blend modes, offsets, and
    data-defined overrides. We flatten to the common subset and record whatever
    was lost as a `FidelityItem` -- flattening silently is the failure mode we are
    trying to avoid.
    """

    fill_color: Color | None = None
    stroke_color: Color | None = None
    stroke_width: float = 0.0
    stroke_dash: tuple[float, ...] = ()
    radius: float | None = None
    opacity: float = 1.0
    icon_path: str | None = None
    marker_shape: str | None = None
    symbol_layer_count: int = 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "fillColor": self.fill_color.snapshot() if self.fill_color else None,
            "strokeColor": self.stroke_color.snapshot() if self.stroke_color else None,
            "strokeWidth": round(self.stroke_width, 6),
            "strokeDash": list(self.stroke_dash),
            "radius": self.radius,
            "opacity": round(self.opacity, 4),
            "iconPath": self.icon_path,
            "markerShape": self.marker_shape,
            "symbolLayerCount": self.symbol_layer_count,
        }


# Marker shapes a circle-based renderer draws correctly without an icon atlas.
# QGIS offers around forty; everything outside this set has to be either drawn as
# an icon or reported as approximated. qgis2web silently collapses all of them to
# circles - upstream qgis2web#1218, open since June 2026.
NATIVELY_ROUND_MARKER_SHAPES = frozenset({"circle"})


@dataclass(frozen=True)
class CategorySpec:
    """One class of a categorized renderer."""

    value: str | int | float | None
    label: str
    symbol: SymbolSpec

    def snapshot(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label,
            "symbol": self.symbol.snapshot(),
        }


@dataclass(frozen=True)
class GraduatedClassSpec:
    """One numeric range of a graduated renderer."""

    lower: float
    upper: float
    label: str
    symbol: SymbolSpec

    def snapshot(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "label": self.label,
            "symbol": self.symbol.snapshot(),
        }


@dataclass(frozen=True)
class RendererSpec:
    """Normalized symbology for one layer.

    Exactly one of `symbol` / `categories` / `classes` is populated, selected by
    `kind`. `UNSUPPORTED` populates none of them and sets `unsupported_reason`.
    """

    kind: RendererKind
    field_name: str | None = None
    symbol: SymbolSpec | None = None
    categories: tuple[CategorySpec, ...] = ()
    classes: tuple[GraduatedClassSpec, ...] = ()
    classification: ClassificationMethod = ClassificationMethod.UNKNOWN
    ramp_name: str | None = None
    unsupported_reason: str | None = None
    # Category values whose class is switched off in QGIS. Carried here rather
    # than resolved at translation time because the reader needs it to drop the
    # matching features from the exported data - an expression alone cannot hide
    # them, and leaving them in draws them in the "other" fallback colour.
    hidden_values: tuple[str | int | float | None, ...] = ()

    @property
    def representative_symbol(self) -> SymbolSpec | None:
        """A symbol carrying this renderer's non-colour geometry.

        Colour varies per class and is expressed as an accessor, but stroke
        width and marker radius are single values on the layer. A categorized or
        graduated renderer has no top-level `symbol`, so reading that directly
        loses the width and radius entirely - the layer draws at defaults and
        nothing says why.

        The first class is the representative. If classes differ in width or
        size, that difference is reported by the translator rather than guessed
        at here.
        """
        if self.symbol is not None:
            return self.symbol
        if self.categories:
            return self.categories[0].symbol
        if self.classes:
            return self.classes[0].symbol
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "fieldName": self.field_name,
            "symbol": self.symbol.snapshot() if self.symbol else None,
            "categories": [c.snapshot() for c in self.categories],
            "classes": [c.snapshot() for c in self.classes],
            "classification": self.classification.value,
            "rampName": self.ramp_name,
            "unsupportedReason": self.unsupported_reason,
            "hiddenValues": list(self.hidden_values),
        }


@dataclass(frozen=True)
class LabelingSpec:
    """Layer labelling, when enabled.

    `character_set` exists because OnlyMap's `TextLayer` ships an ASCII-only font
    atlas by default; glyphs outside it render blank unless declared. The reader
    collects the distinct characters actually present in the label field.
    """

    enabled: bool = False
    field_name: str | None = None
    font_family: str | None = None
    font_size: float | None = None
    color: Color | None = None
    halo_color: Color | None = None
    halo_width: float = 0.0
    character_set: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "fieldName": self.field_name,
            "fontFamily": self.font_family,
            "fontSize": self.font_size,
            "color": self.color.snapshot() if self.color else None,
            "haloColor": self.halo_color.snapshot() if self.halo_color else None,
            "haloWidth": round(self.halo_width, 4),
            "characterSet": self.character_set,
        }


# --------------------------------------------------------------------------
# Popups
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PopupFieldSpec:
    """One attribute's presentation in a popup."""

    name: str
    alias: str | None = None
    mode: PopupFieldMode = PopupFieldMode.INLINE_WITH_DATA

    @property
    def display_name(self) -> str:
        return self.alias or self.name

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "alias": self.alias, "mode": self.mode.value}


@dataclass(frozen=True)
class PopupSpec:
    """A layer's popup configuration."""

    enabled: bool = True
    fields: tuple[PopupFieldSpec, ...] = ()
    # Resolved per layer, not read from `ExportSettings`: qgis2web#133 has asked
    # for exactly this since 2015 and it is still open there.
    on_hover: bool = False

    @property
    def visible_fields(self) -> tuple[PopupFieldSpec, ...]:
        return tuple(f for f in self.fields if f.mode is not PopupFieldMode.HIDDEN)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "fields": [f.snapshot() for f in self.fields],
            "onHover": self.on_hover,
        }


# --------------------------------------------------------------------------
# Assets and fidelity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetDependency:
    """Something the artifact needs that is not the layer's own geometry.

    `credentials_detected` is deliberately a flag rather than the credential:
    knowing a source carries a password is enough to block or warn, and the
    secret itself must never enter the model, a snapshot, or an artifact.
    """

    identifier: str
    disposition: AssetDisposition
    size_bytes: int | None = None
    licence: str | None = None
    attribution: str | None = None
    credentials_detected: bool = False
    note: str | None = None

    @property
    def is_portable(self) -> bool:
        return self.disposition in (
            AssetDisposition.EMBEDDABLE,
            AssetDisposition.COPYABLE,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "disposition": self.disposition.value,
            "sizeBytes": self.size_bytes,
            "licence": self.licence,
            "attribution": self.attribution,
            "credentialsDetected": self.credentials_detected,
            "note": self.note,
        }


@dataclass(frozen=True)
class FidelityItem:
    """One thing that happened to one property during translation.

    `subject` names what it was about (a layer name, `"project.extent"`, a
    setting key). `detail` explains it in a sentence a user can act on -- these
    strings surface directly in the Fidelity tab and the exported report, so they
    are user-facing copy, not debug output.
    """

    subject: str
    status: FidelityStatus
    detail: str
    layer_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "status": self.status.value,
            "detail": self.detail,
            "layerId": self.layer_id,
        }


# --------------------------------------------------------------------------
# Layers and project
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportLayer:
    """One QGIS layer, normalized.

    `geojson` holds WGS84 GeoJSON as a plain dict -- 0.1.0 normalises everything
    to it, so the writer never has to know what the source format was.
    `feature_count` is stored separately because it is needed for licence-cap
    evaluation before any geometry is read.
    """

    layer_id: str
    name: str
    geometry_kind: GeometryKind
    source_kind: SourceKind
    visible: bool = True
    opacity: float = 1.0
    scale_range: ScaleRange = field(default_factory=ScaleRange)
    renderer: RendererSpec = field(
        default_factory=lambda: RendererSpec(kind=RendererKind.UNSUPPORTED)
    )
    labeling: LabelingSpec = field(default_factory=LabelingSpec)
    popup: PopupSpec = field(default_factory=PopupSpec)
    attribution: str | None = None
    feature_count: int = 0
    geojson: dict[str, Any] | None = None
    group_path: tuple[str, ...] = ()
    dependencies: tuple[AssetDependency, ...] = ()
    # None keeps the manifest's own default. Per layer for the same reason as
    # `PopupSpec.on_hover` - qgis2web#132, open since 2015.
    highlight_color: Color | None = None

    def snapshot(self, include_geometry: bool = False) -> dict[str, Any]:
        """Snapshot the layer.

        Geometry is excluded by default: a fixture snapshot exists to detect
        drift in *interpretation*, and embedding megabytes of coordinates would
        make the diffs unreadable while adding nothing.
        """
        data: dict[str, Any] = {
            "layerId": self.layer_id,
            "name": self.name,
            "geometryKind": self.geometry_kind.value,
            "sourceKind": self.source_kind.value,
            "visible": self.visible,
            "opacity": round(self.opacity, 4),
            "scaleRange": self.scale_range.snapshot(),
            "renderer": self.renderer.snapshot(),
            "labeling": self.labeling.snapshot(),
            "popup": self.popup.snapshot(),
            "attribution": self.attribution,
            "featureCount": self.feature_count,
            "groupPath": list(self.group_path),
            "dependencies": [d.snapshot() for d in self.dependencies],
            "highlightColor": (
                self.highlight_color.snapshot() if self.highlight_color else None
            ),
        }
        if include_geometry:
            data["geojson"] = self.geojson
        return data


@dataclass(frozen=True)
class ExportSettings:
    """Per-export choices the user made in the dialog.

    `quantize_precision` and `simplify_tolerance` are the only lossy options and
    both default to off. Compression is lossless and unconditional, so it is not
    a setting -- see the readiness assessment §4.2.
    """

    output_mode: OutputMode = OutputMode.STANDALONE_HTML
    show_legend: bool = True
    show_layer_switcher: bool = True
    show_zoom_controls: bool = True
    show_scale_bar: bool = True
    quantize_precision: int | None = None
    simplify_tolerance: float | None = None
    popup_on_hover: bool = False
    # Kept in step with `DialogState`: a Processing run and a dialog export of
    # the same project must produce the same map, and these are the defaults a
    # headless caller gets when it passes no settings at all.
    show_title: bool = True
    show_abstract: bool = False
    title_corner: OverlayCorner = OverlayCorner.TOP_CENTER
    widget_background: Color | None = None
    widget_foreground: Color | None = None
    # None leaves the runtime's own highlight alone. qgis2web has no equivalent:
    # it reuses `mapSettings.selectionColor()`, the QGIS *editing selection*
    # colour, as a web hover cue - opaque yellow out of the box, overridable
    # only from Project Properties, outside the plugin entirely.
    highlight_color: Color | None = None
    extent_source: ExtentSource = ExtentSource.DATA

    @property
    def has_lossy_transform(self) -> bool:
        return (
            self.quantize_precision is not None or self.simplify_tolerance is not None
        )

    @property
    def has_widget_colors(self) -> bool:
        return self.widget_background is not None or self.widget_foreground is not None

    def snapshot(self) -> dict[str, Any]:
        return {
            "outputMode": self.output_mode.value,
            "showLegend": self.show_legend,
            "showLayerSwitcher": self.show_layer_switcher,
            "showZoomControls": self.show_zoom_controls,
            "showScaleBar": self.show_scale_bar,
            "quantizePrecision": self.quantize_precision,
            "simplifyTolerance": self.simplify_tolerance,
            "popupOnHover": self.popup_on_hover,
            "showTitle": self.show_title,
            "showAbstract": self.show_abstract,
            "titleCorner": self.title_corner.value,
            "widgetBackground": (
                self.widget_background.snapshot() if self.widget_background else None
            ),
            "widgetForeground": (
                self.widget_foreground.snapshot() if self.widget_foreground else None
            ),
            "highlightColor": (
                self.highlight_color.snapshot() if self.highlight_color else None
            ),
            "extentSource": self.extent_source.value,
        }


@dataclass(frozen=True)
class ExportProject:
    """A whole QGIS project, normalized and ready to write.

    `layers` is in **draw order, bottom first** -- the same order the writer emits
    as document order, since OnlyMap stacks `<om-layer>` children in document
    order. Reversing this is the classic way to get an upside-down map.
    """

    title: str
    layers: tuple[ExportLayer, ...] = ()
    abstract: str | None = None
    extent: Extent | None = None
    background_color: Color | None = None
    source_crs: str | None = None
    settings: ExportSettings = field(default_factory=ExportSettings)
    fidelity: tuple[FidelityItem, ...] = ()

    @property
    def total_features(self) -> int:
        return sum(layer.feature_count for layer in self.layers)

    @property
    def exportable_layers(self) -> tuple[ExportLayer, ...]:
        return tuple(layer for layer in self.layers if layer.geojson is not None)

    @property
    def blocking_items(self) -> tuple[FidelityItem, ...]:
        return tuple(i for i in self.fidelity if i.status is FidelityStatus.BLOCKED)

    @property
    def is_exportable(self) -> bool:
        return bool(self.exportable_layers) and not self.blocking_items

    def snapshot(self, include_geometry: bool = False) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "title": self.title,
            "abstract": self.abstract,
            "extent": self.extent.snapshot() if self.extent else None,
            "backgroundColor": (
                self.background_color.snapshot() if self.background_color else None
            ),
            "sourceCrs": self.source_crs,
            "settings": self.settings.snapshot(),
            "layers": [
                layer.snapshot(include_geometry=include_geometry)
                for layer in self.layers
            ],
            "fidelity": [item.snapshot() for item in self.fidelity],
        }
