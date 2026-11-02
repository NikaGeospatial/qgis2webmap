"""One QGIS layer - vector or raster - to one `ExportLayer`.

Imports PyQGIS; exercised in `tests/qgis/`.

Normalises everything to **WGS84 GeoJSON**, per issue #29's 0.1.0 scope. That one
decision removes a whole class of downstream work: the writer never learns what a
Shapefile is, and reprojection happens exactly once, here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import bisect
import dataclasses
import functools
import json
import re
from collections import Counter, deque
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsFeatureRequest,
    QgsJsonExporter,
    QgsMapLayerType,
    QgsReadWriteContext,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)
from qgis.PyQt.QtXml import QDomDocument

from .elevation_translator import translate_elevation
from .export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ElevationSpec,
    ExportLayer,
    Extent,
    GeometryKind,
    LabelingSpec,
    NoDataRule,
    PopupSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    ScaleRange,
    SourceKind,
    TileSpec,
)
from .fidelity_report import FidelityReportBuilder
from .labeling_translator import translate_labeling
from .popup_translator import rename_untemplatable_fields, translate_popup
from .raster_style import composite_bands, hue_saturation_active, should_bake
from .renderer_translator import (
    category_key,
    ground_units,
    reference_render_context,
    report_ground_sizes,
    translate_renderer,
)
from .symbol_rasterizer import build_icon_atlas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping

    from qgis.core import (
        QgsFeature,
        QgsFieldFormatter,
        QgsMapLayer,
        QgsProject,
        QgsRasterLayer,
        QgsVectorLayer,
    )

    from .cartograph_export import LayerScans

WGS84 = "EPSG:4326"

# Full double precision is meaningless for a web map and inflates the artifact
# for nothing. 9 decimal places is roughly 0.1 mm at the equator - far beyond any
# survey requirement, so this is not the lossy quantisation that stays opt-in.
GEOJSON_PRECISION = 9

_GEOMETRY_BY_WKB = {
    QgsWkbTypes.GeometryType.PointGeometry: GeometryKind.POINT,
    QgsWkbTypes.GeometryType.LineGeometry: GeometryKind.LINE,
    QgsWkbTypes.GeometryType.PolygonGeometry: GeometryKind.POLYGON,
}

# Provider keys that mean "the data lives in a file next to the project".
_FILE_PROVIDERS = frozenset({"ogr", "gdal", "delimitedtext", "gpx", "spatialite"})
_DATABASE_PROVIDERS = frozenset({"postgres", "mssql", "oracle", "db2", "hana"})
_SERVICE_PROVIDERS = frozenset({"wfs", "arcgisfeatureserver", "ows", "wms"})


def geometry_kind(layer: QgsVectorLayer) -> GeometryKind:
    return _GEOMETRY_BY_WKB.get(layer.geometryType(), GeometryKind.UNKNOWN)


def source_kind(layer: QgsVectorLayer) -> SourceKind:
    provider = (layer.providerType() or "").lower()
    if provider == "memory":
        return SourceKind.MEMORY
    if provider in _FILE_PROVIDERS:
        return SourceKind.FILE
    if provider in _DATABASE_PROVIDERS:
        return SourceKind.DATABASE
    if provider in _SERVICE_PROVIDERS:
        return SourceKind.SERVICE
    return SourceKind.UNKNOWN


def scale_range(layer: QgsVectorLayer) -> ScaleRange:
    if not layer.hasScaleBasedVisibility():
        return ScaleRange()
    return ScaleRange(
        min_scale=float(layer.minimumScale()), max_scale=float(layer.maximumScale())
    )


def read_attribution(layer: QgsVectorLayer) -> str | None:
    """Attribution text a map must display for this layer's data.

    `QgsMapLayer.attribution()` is deprecated in QGIS 3.32+; the value moved to
    `serverProperties()`. Metadata rights are a second, independent place a user
    can record a credit, so both are checked - a missing attribution on a
    licensed dataset is a legal problem, not a cosmetic one.
    """
    server_properties = getattr(layer, "serverProperties", None)
    if server_properties is not None:
        attribution = (server_properties().attribution() or "").strip()
        if attribution:
            return attribution

    rights = [r.strip() for r in (layer.metadata().rights() or []) if r.strip()]
    if rights:
        return "; ".join(rights)

    return None


def describe_source(layer: QgsVectorLayer) -> AssetDependency:
    """Classify where the data comes from, without capturing credentials.

    A database or service URI can carry a password. We record only *that* one was
    present - never the value - because knowing is enough to block or warn, and a
    secret must never reach the model, a snapshot, or an artifact.
    """
    kind = source_kind(layer)
    uri = layer.dataProvider().uri() if layer.dataProvider() else None
    has_credentials = bool(uri and (uri.password() or uri.username()))

    if kind in (SourceKind.FILE, SourceKind.MEMORY):
        disposition = AssetDisposition.EMBEDDABLE
        note = None
    elif kind is SourceKind.SERVICE:
        disposition = AssetDisposition.EMBEDDABLE
        note = (
            "Features are downloaded once and embedded, so the exported map does "
            "not call the service. It is a snapshot, not a live feed."
        )
    else:
        disposition = AssetDisposition.EMBEDDABLE
        note = (
            "Features are read from the database once and embedded. The exported "
            "map contains no connection details."
        )

    return AssetDependency(
        identifier=f"{layer.providerType()}:{layer.name()}",
        disposition=disposition,
        credentials_detected=has_credentials,
        note=note,
    )


def _report_clip(
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
    kept: int,
    total: int,
) -> None:
    """Say what the clip removed. Always - including when it removed nothing.

    Dropping features is the one thing in an export that the map itself cannot
    show, because what is missing leaves no gap. A user who clipped to the wrong
    view would otherwise get a confidently incomplete map.
    """
    if total < 0 or kept >= total:
        report.preserved(
            subject,
            "Every feature is inside the current QGIS view, so clipping removed "
            "nothing.",
            layer_id,
        )
        return

    report.approximated(
        subject,
        f"Clipped to the current QGIS view: {kept:,} of {total:,} features are "
        f"exported and {total - kept:,} are left out. The map cannot show what "
        "is missing, so check the view is the one you meant.",
        layer_id,
    )


def clip_request(
    layer: QgsVectorLayer,
    extent: Any,
    transform_context: QgsCoordinateTransformContext | None = None,
) -> Any:
    """A feature request limited to `extent`, in the layer's own CRS.

    The extent arrives in WGS84 because that is what the rest of the model
    speaks, but a filter rect is compared against the layer's *source*
    coordinates - so it has to be transformed back, not forward. Getting this
    the wrong way round silently returns nothing, which looks exactly like an
    empty layer.

    Membership is exact: the provider's spatial index pre-filters by bounding
    box, then `ExactIntersect` keeps only features whose geometry really meets
    the rectangle. Without it a diagonal line or a polygon around a hole
    survives on its bounding box alone. Features are kept or dropped whole;
    nothing is cut.

    Only the layer's CRS is read from `layer`, so the request can be built from
    a `LayerFeatures` snapshot on a worker thread; `transform_context` defaults
    to the running project's, as it always did.
    """
    return _clip_request(layer.crs(), extent, transform_context)


def _clip_request(
    target: QgsCoordinateReferenceSystem,
    extent: Any,
    transform_context: QgsCoordinateTransformContext | None,
) -> Any:
    from qgis.core import (
        QgsCoordinateTransform,
        QgsFeatureRequest,
        QgsProject,
        QgsRectangle,
    )

    rect = QgsRectangle(extent.west, extent.south, extent.east, extent.north)
    if target.isValid() and target.authid() != WGS84:
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(WGS84),
            target,
            transform_context or QgsProject.instance().transformContext(),
        )
        rect = transform.transformBoundingBox(rect)

    request = QgsFeatureRequest().setFilterRect(rect)
    return request.setFlags(QgsFeatureRequest.Flag.ExactIntersect)


@dataclasses.dataclass(frozen=True)
class RelationTable:
    """A Value Relation's cache: `(key, text)` rows in the order QGIS lists them."""

    items: tuple[tuple[str, str], ...] = ()

    @functools.cached_property
    def first(self) -> dict[str, str]:
        """key -> text, the first row winning as in QGIS's own lookup."""
        lookup: dict[str, str] = {}
        for key, text in self.items:
            lookup.setdefault(key, text)
        return lookup


@dataclasses.dataclass(frozen=True)
class FieldFormat:
    """One field's editor-widget formatter, with the inputs it needs copied out.

    A Value Map shows a description rather than its stored key, and a Value
    Relation the other layer's text; formatting needs the layer, which a worker
    must not touch. So the formatter, its configuration and its lookup cache (a
    Value Relation's table of the other layer, read here on the GUI thread) are
    taken up front, and `LayerFeatures.display_values` applies them to the
    features a worker reads.

    The shown value never replaces the stored one: drawing, filtering and
    expressions compare the stored value, so the shown one travels beside it
    under `display_name`, which only the popup reads.
    """

    index: int
    name: str
    display_name: str
    formatter: QgsFieldFormatter
    config: dict[str, Any]
    cache: Any
    # A Value Relation's other layer as its cache of `(key, text)` rows, read on
    # the GUI thread because its own cache cannot be carried into Python; `None`
    # for every other formatter.
    relation: RelationTable | None = None
    null_text: str = "NULL"


@dataclasses.dataclass(frozen=True)
class LayerFeatures:
    """Everything the feature read needs from a vector layer, copied out.

    Taken on the GUI thread, consumed on a worker. `QgsVectorLayer` is a
    GUI-owned object that QGIS's own task documentation says a background job
    must not touch, so the worker is given only what is safe to carry across:
    a `QgsVectorLayerFeatureSource` (QGIS's thread-safe handle on the layer's
    data, made for exactly this), value-type copies of the CRS and transform
    context, and plain values. Nothing here refers back to the layer, which is
    what lets the worker keep running if the user removes it meanwhile.

    `total` is the layer's feature count, read only when a clip needs it for
    the "kept N of M" note - it can be a full scan on a remote provider, which
    is not worth paying when nothing will report it.
    """

    source: QgsVectorLayerFeatureSource
    layer_id: str
    layer_name: str
    crs: QgsCoordinateReferenceSystem
    transform_context: QgsCoordinateTransformContext
    total: int = -1
    # Only the popup-visible fields whose editor widget changes how a value
    # reads. Empty - almost always - means no display copy is written, and
    # `formats_layer` is `None`.
    formats: tuple[FieldFormat, ...] = ()
    # A private clone for the formatters to be called with, never the project's
    # layer. Made only when `formats` is not empty.
    formats_layer: QgsVectorLayer | None = None
    # The indexes of the fields the export keeps, the only ones read from the
    # source: a wide table's other columns would be fetched and serialised
    # only to be stripped (see `keep_only_fields`). `None` reads every field.
    attribute_indexes: tuple[int, ...] | None = None

    def display_values(self, features: list[QgsFeature]) -> list[dict[str, Any]]:
        """Per feature, each formatted field's shown value under its display name."""
        shown: list[dict[str, Any]] = []
        for feature in features:
            attributes = feature.attributes()
            values: dict[str, Any] = {}
            for fmt in self.formats:
                if fmt.relation is not None:
                    value: Any = _relation_text(fmt, attributes[fmt.index])
                else:
                    value = fmt.formatter.representValue(
                        self.formats_layer,
                        fmt.index,
                        fmt.config,
                        fmt.cache,
                        attributes[fmt.index],
                    )
                values[fmt.display_name] = (
                    value if isinstance(value, (str, int, float, bool)) else str(value)
                )
            shown.append(values)
        return shown


def _relation_key(value: object) -> str:
    """A Value Relation key the way QGIS compares it: 2 and 2.0 are one key."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _relation_text(fmt: FieldFormat, value: object) -> str:
    """What a Value Relation shows for `value`, from the table read up front.

    Mirrors QgsValueRelationFieldFormatter.representValue: a multi-select lists
    the texts of the related rows whose key is selected, in the table's
    order (by key, or by text with "order by value"; the selection's order is
    moot), and an empty or null selection is `{}`; a single value shows its
    first matching text, NULL as the null text, and an unknown key as `(key)`.
    """
    from qgis.core import QgsValueRelationFieldFormatter, QgsVariantUtils

    table = fmt.relation
    if table is None:
        return str(value)
    if fmt.config.get("AllowMulti"):
        selected = set(QgsValueRelationFieldFormatter.valueToStringList(value))
        return (
            "{" + ", ".join(text for key, text in table.items if key in selected) + "}"
        )
    if QgsVariantUtils.isNull(value):
        return fmt.null_text
    return table.first.get(_relation_key(value), f"({_relation_key(value)})")


def _selection_literal(keys: list[str]) -> str:
    """`keys` as the quoted array text a multi-select Value Relation stores."""
    quoted = ('"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"' for key in keys)
    return "{" + ",".join(quoted) + "}"


def _split_shown_texts(shown: str, texts: Counter[str]) -> list[str] | None:
    """Cut `{t1, t2, ...}` back into the known `texts`, in the order shown.

    The texts may themselves hold ", ", so this searches (depth first, with an
    explicit stack because a table can have thousands of rows) for a reading
    that uses every text exactly once. `None` when there is none, or none is
    found within a fixed effort; the caller then keeps the layer's own order.
    """
    inner = shown[1:-1]
    total = sum(texts.values())
    remaining = Counter(texts)
    longest = max(map(len, remaining), default=0)
    ends = [m.start() for m in re.finditer(r"(?=, )", inner)] + [len(inner)]
    path: list[str] = []
    path_ends: list[int] = []
    cursors = [bisect.bisect_left(ends, 0)]
    budget = 200_000
    while budget > 0:
        budget -= 1
        depth = len(path)
        if depth == total and path_ends[-1:] == [len(inner)]:
            return path
        start = path_ends[-1] + 2 if path_ends else 0
        found = False
        if depth < total:
            index = cursors[depth]
            while index < len(ends) and ends[index] - start <= longest:
                end = ends[index]
                index += 1
                text = inner[start:end]
                if remaining[text] > 0:
                    cursors[depth] = index
                    remaining[text] -= 1
                    path.append(text)
                    path_ends.append(end)
                    cursors.append(bisect.bisect_left(ends, end + 2))
                    found = True
                    break
        if not found:
            if not path:
                return None
            cursors.pop()
            path_ends.pop()
            remaining[path.pop()] += 1
    return None


def _value_relation_table(
    layer: QgsVectorLayer,
    index: int,
    formatter: QgsFieldFormatter,
    config: dict[str, Any],
) -> RelationTable:
    """The other layer of a Value Relation in QGIS's own order. **GUI thread only.**

    The related rows are read here (honouring the filter expression as
    QgsValueRelationFieldFormatter does with no form open: a filter that needs
    the form is skipped, one that fails to parse keeps no row) but their order
    is QGIS's, not ours: QGIS sorts keys and texts differently from one version
    to the next (3.44 compares texts by code point, 4.2 case-insensitively), and
    the sorted cache cannot be read from Python (its items crash the binding).
    So `representValue` is asked to show a selection of every key, which lists
    the texts in cache order, and that listing is matched back to the rows.
    """
    from qgis.core import (
        QgsExpression,
        QgsExpressionContext,
        QgsExpressionContextUtils,
        QgsFeature,
        QgsFeatureRequest,
        QgsProject,
        QgsValueRelationFieldFormatter,
        QgsVariantUtils,
    )

    related = QgsValueRelationFieldFormatter.resolveLayer(config, QgsProject.instance())
    if related is None:
        return RelationTable()
    key_field, value_field = config.get("Key"), config.get("Value")
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
    filter_expression = str(config.get("FilterExpression") or "")
    if filter_expression and QgsValueRelationFieldFormatter.expressionIsUsable(
        filter_expression, QgsFeature()
    ):
        if QgsExpression(filter_expression).hasParserError():
            return RelationTable()
        request.setExpressionContext(
            QgsExpressionContext(
                QgsExpressionContextUtils.globalProjectLayerScopes(related)
            )
        )
        request.setFilterExpression(filter_expression)
    rows: list[tuple[str, str]] = []
    for feature in related.getFeatures(request):
        key, text = feature[key_field], feature[value_field]
        if QgsVariantUtils.isNull(key):
            continue
        rows.append(
            (_relation_key(key), "" if QgsVariantUtils.isNull(text) else str(text))
        )
    if not rows:
        return RelationTable()

    multi_config = {**config, "AllowMulti": True}
    shown = formatter.representValue(
        layer,
        index,
        multi_config,
        None,
        _selection_literal(list(dict.fromkeys(key for key, _ in rows))),
    )
    texts = _split_shown_texts(str(shown), Counter(text for _, text in rows))
    if texts is None:
        return RelationTable(tuple(rows))
    by_text: dict[str, deque[tuple[str, str]]] = {}
    for row in rows:
        by_text.setdefault(row[1], deque()).append(row)
    return RelationTable(tuple(by_text[text].popleft() for text in texts))


def _display_property(
    field_name: str, taken: set[str], assigned: Mapping[str, str]
) -> str:
    """A property name for `field_name`'s shown value that nothing else uses.

    ASCII word characters only, because the popup template substitutes nothing
    else; `taken` holds every field of the layer, so a real field already called
    `kind_shown` is never overwritten.
    """
    stem = re.sub(r"\W+", "_", field_name, flags=re.ASCII).strip("_") or "field"
    candidate = f"{stem}_shown"
    while candidate in taken or candidate in assigned.values():
        candidate += "_"
    return candidate


def popup_display_names(layer: QgsVectorLayer, popup: PopupSpec) -> dict[str, str]:
    """Field name -> property carrying its shown value, for the popup's fields.

    Only fields a popup shows and whose editor widget formats the value get one,
    so a field the user hid never gains a second copy. **GUI thread only.**
    """
    from qgis.core import QgsApplication

    if not popup.enabled:
        return {}
    registry = QgsApplication.fieldFormatterRegistry()
    fallback_id = registry.fallbackFieldFormatter().id()
    visible = {field.name for field in popup.visible_fields}
    taken = set(layer.fields().names()) | {
        re.sub(r"\W+", "_", name, flags=re.ASCII).strip("_") for name in visible
    }
    names: dict[str, str] = {}
    for field in layer.fields():
        if field.name() not in visible:
            continue
        if (
            registry.fieldFormatter(field.editorWidgetSetup().type()).id()
            == fallback_id
        ):
            continue
        names[field.name()] = _display_property(field.name(), taken, names)
    return names


def popup_reading_display(popup: PopupSpec, names: Mapping[str, str]) -> PopupSpec:
    """`popup` with each named field reading its display property.

    The label keeps the field's own spelling.
    """
    return dataclasses.replace(
        popup,
        fields=tuple(
            dataclasses.replace(f, name=names[f.name], alias=f.alias or f.name)
            if f.name in names
            else f
            for f in popup.fields
        ),
    )


def _field_formats(
    layer: QgsVectorLayer,
    display_names: Mapping[str, str],
    scans: LayerScans | None = None,
) -> tuple[FieldFormat, ...]:
    """The formatters of the fields in `display_names`. **GUI thread only.**"""
    from qgis.core import QgsApplication

    registry = QgsApplication.fieldFormatterRegistry()
    formats = []
    for index, field in enumerate(layer.fields()):
        if field.name() not in display_names:
            continue
        setup = field.editorWidgetSetup()
        formatter = registry.fieldFormatter(setup.type())
        config = setup.config()
        if formatter.id() == "ValueRelation":
            # Its cache cannot be read from Python, so the table is read
            # here and the text looked up on the worker.
            formats.append(
                FieldFormat(
                    index,
                    field.name(),
                    display_names[field.name()],
                    formatter,
                    config,
                    None,
                    (
                        _value_relation_table(layer, index, formatter, config)
                        if scans is None
                        else scans.relation_table(
                            layer, index, formatter, config, _value_relation_table
                        )
                    ),
                    QgsApplication.nullRepresentation(),
                )
            )
            continue
        formats.append(
            FieldFormat(
                index,
                field.name(),
                display_names[field.name()],
                formatter,
                config,
                formatter.createCache(layer, index, config),
            )
        )
    return tuple(formats)


def snapshot_features(
    layer: QgsVectorLayer,
    clip_extent: Any | None,
    display_names: Mapping[str, str] | None = None,
    keep: Iterable[str] | None = None,
    scans: LayerScans | None = None,
) -> LayerFeatures:
    """Copy out what `export_geojson_from` needs. **GUI thread only.**

    `display_names` is `popup_display_names`: the fields to write a shown-value
    copy of, which is none for a caller that has no popup. `keep` is the names
    of the fields the export keeps, which are then the only ones read; `None`
    reads them all. `scans` as for `prepare_layer`.
    """
    from qgis.core import QgsProject

    formats = _field_formats(layer, display_names or {}, scans)
    attribute_indexes = None
    if keep is not None:
        fields = layer.fields()
        attribute_indexes = tuple(
            sorted({i for i in map(fields.indexOf, keep) if i >= 0})
        )
    return LayerFeatures(
        source=QgsVectorLayerFeatureSource(layer),
        layer_id=layer.id(),
        layer_name=layer.name(),
        crs=layer.crs(),
        transform_context=QgsProject.instance().transformContext(),
        total=layer.featureCount() if clip_extent is not None else -1,
        formats=formats,
        formats_layer=layer.clone() if formats else None,
        attribute_indexes=attribute_indexes,
    )


def export_geojson(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    precision: int = GEOJSON_PRECISION,
    clip_extent: Any | None = None,
) -> dict[str, Any] | None:
    """Read every feature as WGS84 GeoJSON, or only those within `clip_extent`.

    Snapshot and read in one call, for callers already on the thread that owns
    `layer`. A background job takes the snapshot on the GUI thread and calls
    `export_geojson_from` on its worker instead.
    """
    return export_geojson_from(
        snapshot_features(layer, clip_extent),
        report,
        precision=precision,
        clip_extent=clip_extent,
    )


def export_geojson_from(
    features: LayerFeatures,
    report: FidelityReportBuilder,
    precision: int = GEOJSON_PRECISION,
    clip_extent: Any | None = None,
) -> dict[str, Any] | None:
    """Read a snapshot's features as WGS84 GeoJSON, or only those within
    `clip_extent`. Safe on a worker thread: it touches no layer.

    `QgsJsonExporter` handles reprojection through `setDestinationCrs`, so this
    is the single place a CRS transform happens in the whole plugin.

    `clip_extent` is how a half-million-point layer becomes an exportable one:
    the features outside it are never read, so the cost is paid by the provider's
    spatial index rather than by us filtering afterwards.
    """
    layer_id = features.layer_id
    subject = f"Data of '{features.layer_name}'"

    exporter = QgsJsonExporter()
    exporter.setSourceCrs(features.crs)
    exporter.setDestinationCrs(QgsCoordinateReferenceSystem(WGS84))
    exporter.setTransformGeometries(True)
    exporter.setIncludeGeometry(True)
    exporter.setIncludeAttributes(True)
    exporter.setPrecision(precision)

    try:
        if clip_extent is None:
            request = QgsFeatureRequest()
        else:
            request = _clip_request(
                features.crs, clip_extent, features.transform_context
            )
        indexes = features.attribute_indexes
        if indexes is not None:
            request.setSubsetOfAttributes(list(indexes))
            # And only those written, rather than every other column as a
            # null. With none kept none is written: an empty list here would
            # mean every field.
            if indexes:
                exporter.setAttributes(list(indexes))
            else:
                exporter.setIncludeAttributes(False)
        collected = list(features.source.getFeatures(request))
        if clip_extent is not None:
            _report_clip(report, subject, layer_id, len(collected), features.total)
        collection = json.loads(exporter.exportFeatures(collected))
        if features.formats:
            _add_display_values(collection, features.display_values(collected))
    except (OSError, ValueError, RuntimeError) as exc:
        report.blocked(
            subject,
            f"The layer's features could not be read: {exc}. Check that the data "
            "source is reachable and not locked by another program.",
            layer_id,
        )
        return None

    source_crs = features.crs
    if source_crs.isValid() and source_crs.authid() != WGS84:
        report.preserved(
            subject,
            f"Reprojected from {source_crs.authid()} to {WGS84} for the web map.",
            layer_id,
        )

    return collection


def _add_display_values(
    collection: dict[str, Any], shown: list[dict[str, Any]]
) -> None:
    """Write each feature's shown values into its properties, in place."""
    exported = collection.get("features") or []
    if len(exported) != len(shown):
        raise RuntimeError("the exporter returned a different number of features")
    for feature, values in zip(exported, shown, strict=True):
        feature["properties"] = {**(feature.get("properties") or {}), **values}


def drop_hidden_features(
    collection: dict[str, Any],
    renderer: RendererSpec,
) -> dict[str, Any]:
    """Remove the features QGIS would not draw.

    Those are the ones in a category or range switched off, and the ones no
    category or range claims: QGIS draws nothing for them, but a colour
    expression always yields a colour, so left in they would be drawn in the
    "other" fallback or clamped into the nearest class.

    A categorized value is compared as QGIS keys it - its string, NULL and the
    empty string alike - because a category value arrives from QGIS as a Python
    scalar while the GeoJSON property may have been serialised as a number or a
    string, and `'1' != 1` would silently keep the features the author hid.
    """
    if not renderer.field_name:
        return collection

    if renderer.kind is RendererKind.GRADUATED and renderer.value_ranges:
        field = renderer.field_name
        ranges = renderer.value_ranges

        def drawn(properties: dict[str, Any]) -> bool:
            value = _as_number(properties.get(field))
            if value is None:
                return False
            for value_range in ranges:
                if value_range.lower <= value <= value_range.upper:
                    return value_range.drawn
            return False

    elif renderer.kind is RendererKind.CATEGORIZED and (
        renderer.hidden_values or renderer.unmatched_hidden
    ):
        field = renderer.field_name
        hidden = {category_key(v) for v in renderer.hidden_values}
        matched = {
            category_key(v)
            for category in renderer.categories
            for v in category.explicit_values
        }

        def drawn(properties: dict[str, Any]) -> bool:
            key = category_key(properties.get(field))
            if key in matched:
                return True
            return key not in hidden and not renderer.unmatched_hidden

    else:
        return collection

    kept = [
        feature
        for feature in collection.get("features") or ()
        if drawn(feature.get("properties") or {})
    ]
    return {**collection, "features": kept}


def _as_number(value: object) -> float | None:
    """A property as the number QGIS compares against a range, `None` for NULL."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def drawing_fields(
    renderer: RendererSpec,
    labeling: LabelingSpec,
    elevation: ElevationSpec,
) -> set[str]:
    """Attribute names the map still needs after the popup has had its say.

    Everything the manifest reads as `$field` and everything the label points
    are built from. A field in here survives even when the user hid it from
    popups, because dropping it would not hide data - it would break the map.
    """
    fields = set()
    if renderer.field_name:
        fields.add(renderer.field_name)
    if labeling.enabled and labeling.field_name:
        fields.add(labeling.field_name)
    if elevation.height_field:
        fields.add(elevation.height_field)
    return fields


def keep_only_fields(
    collection: dict[str, Any],
    keep: set[str],
) -> dict[str, Any]:
    """Strip every attribute except `keep` from each feature.

    Unticking **Popups**, or setting a field to *Do not show this field*, is
    documented as keeping that data out of the exported file - and users act on
    that when sending a map to someone. Hiding it in the UI while writing it
    into the GeoJSON would make the promise false: the values sit in plain text
    for anyone who opens the artifact in an editor. So the strip happens on the
    data itself, once, here.
    """
    features = collection.get("features") or ()
    stripped = [
        {
            **feature,
            "properties": {
                name: value
                for name, value in (feature.get("properties") or {}).items()
                if name in keep
            },
        }
        for feature in features
    ]
    return {**collection, "features": stripped}


def primary_key_is_excluded(layer: QgsVectorLayer, keep: set[str]) -> bool:
    """Whether a primary-key column is among the fields left out of the file.

    QGIS uses a provider's primary key as the feature id (a GeoPackage's `fid`,
    or whatever column it was created with), and the GeoJSON `id` is that
    number, so an excluded key column would still travel as the feature id.
    """
    fields = layer.fields()
    return any(
        fields.at(index).name() not in keep for index in layer.primaryKeyAttributes()
    )


def renumber_feature_ids(
    collection: dict[str, Any],
) -> tuple[dict[str, Any], tuple[tuple[int, int], ...]]:
    """Give each feature a sequential id (1, 2, ...) in place of its QGIS one.

    Returns the collection and the `(old id, new id)` pairs. Picking and
    highlighting only need ids to be unique within the layer.
    """
    features = []
    pairs = []
    for number, feature in enumerate(collection.get("features") or (), start=1):
        if "id" in feature:
            pairs.append((feature["id"], number))
        features.append({**feature, "id": number})
    return {**collection, "features": features}, tuple(pairs)


# Raster providers whose pixels are a file on this machine, which is the only
# case packaging can turn into a Cloud Optimized GeoTIFF. Everything else - wms,
# wcs, xyz, arcgismapserver - is a live service: there is no file to convert,
# and embedding a snapshot of somebody else's tile server is a licensing
# decision this plugin does not get to make on the user's behalf.
_RASTER_FILE_PROVIDERS = frozenset({"gdal"})


def raster_source_path(layer: QgsRasterLayer) -> str:
    """The plain filesystem path behind a GDAL raster layer.

    `QgsMapLayer.source()` can carry provider options after a `|` (a subdataset
    selector, a band index) and GDAL subdataset URIs put the path in the middle
    of a colon-separated string. Only the simple case is handled here, because
    only the simple case is a file `to_cog` can open; anything else fails the
    existence check below and is reported rather than half-converted.
    """
    return (layer.source() or "").split("|", 1)[0].strip()


def raster_extent(layer: QgsRasterLayer) -> Extent | None:
    """The layer's extent in WGS84 degrees, or `None` if it cannot be had.

    Reprojected here rather than left in the source CRS so it can join the
    project extent union without a second, differently-implemented transform.
    Antimeridian handling is deliberately not attempted: a raster in a
    projected CRS has no meaningful wrap, and claiming one would produce a
    box `Extent` cannot describe.
    """
    from qgis.core import QgsCoordinateTransform, QgsProject

    rect = layer.extent()
    if rect.isEmpty():
        return None

    crs = layer.crs()
    if crs.isValid() and crs.authid() != WGS84:
        try:
            transform = QgsCoordinateTransform(
                crs, QgsCoordinateReferenceSystem(WGS84), QgsProject.instance()
            )
            rect = transform.transformBoundingBox(rect)
        except Exception:  # pragma: no cover - QGIS raises its own CRS errors
            return None

    return Extent(
        west=float(rect.xMinimum()),
        south=float(rect.yMinimum()),
        east=float(rect.xMaximum()),
        north=float(rect.yMaximum()),
    )


def raster_nodata_rules(layer: QgsRasterLayer) -> tuple[NoDataRule, ...]:
    """The no-data masking QGIS applies on top of the file, band by band.

    Only what departs from the file: user ranges, and a source no-data value
    the author switched off. Neither is part of the layer's style.
    """
    provider = layer.dataProvider()
    if provider is None:
        return ()
    rules: list[NoDataRule] = []
    for band in range(1, int(provider.bandCount()) + 1):
        ranges = tuple(
            (float(found.min()), float(found.max()))
            for found in provider.userNoDataValues(band)
        )
        source_off = provider.sourceHasNoDataValue(band) and not (
            provider.useSourceNoDataValue(band)
        )
        if ranges or source_off:
            rules.append(
                NoDataRule(
                    band=band,
                    use_source=not source_off,
                    ranges=ranges,
                )
            )
    return tuple(rules)


def alters_pixels(layer: QgsRasterLayer) -> bool:
    """Whether QGIS draws this raster differently from what its file holds, in
    a way a colour composite's bands cannot carry.

    The runtime reads the file, so anything QGIS applies on top of it is lost
    unless it is rendered into the pixels: a transparent colour, an alpha band,
    user no-data values or a no-data value the author switched off, and the
    brightness/contrast and hue/saturation filters of the layer's pipe.
    """
    renderer = layer.renderer()
    if renderer is not None:
        transparency_fn = getattr(renderer, "rasterTransparency", None)
        transparency = transparency_fn() if callable(transparency_fn) else None
        if transparency is not None and (
            transparency.transparentSingleValuePixelList()
            or transparency.transparentThreeValuePixelList()
        ):
            return True
        alpha_fn = getattr(renderer, "alphaBand", None)
        if callable(alpha_fn) and int(alpha_fn()) > 0:
            return True

    if raster_nodata_rules(layer):
        return True

    pipe = layer.pipe()
    if pipe is None:
        return False
    brightness = pipe.brightnessFilter()
    if brightness is not None and (brightness.brightness() or brightness.contrast()):
        return True
    return hue_saturation_active(pipe.hueSaturationFilter())


def raster_style(layer: QgsRasterLayer) -> tuple[tuple[int, ...] | None, str | None]:
    """How this raster's colours reach the map: as bands, or as baked pixels.

    Returns the RGB band triple for a composite - whose pixels are already the
    colours the author sees - and otherwise the layer's style as a QML document
    for `packaging.raster_bake` to render into the pixels. Never both: the whole
    point of the split is in `core.raster_style`, which imports no QGIS and
    therefore holds the decision where a unit test can reach it.

    A style that cannot be serialised returns `(None, None)`, which carries the
    raster as unstyled data. That is a visible loss and `read_raster` reports
    it; it is not a crash and not a guess.
    """
    renderer_fn = getattr(layer, "renderer", None)
    renderer = renderer_fn() if callable(renderer_fn) else None

    if not should_bake(renderer, alters_pixels(layer)):
        return (composite_bands(renderer), None)

    document = QDomDocument()
    # `exportNamedStyle` returns a non-empty string on failure and writes
    # nothing, so both halves are checked: an empty document would apply
    # cleanly later and silently render the raster with no styling at all.
    message = layer.exportNamedStyle(document, QgsReadWriteContext())
    qml = document.toString()
    if message or not qml:
        return (None, None)
    return (None, qml)


def raster_rescale(layer: QgsRasterLayer) -> tuple[float | None, float | None]:
    """The contrast stretch the author was actually looking at.

    Read from the *renderer*, not from band statistics: QGIS applies a stretch
    (often "min/max of the current extent" or a 2%-98% cumulative cut) and the
    picture on screen is that stretch, not the raw range. Handing deck.gl the
    raw range would produce a visibly flatter image than QGIS showed and
    nothing would say why.

    Only the single-band renderers are read. A multi-band colour renderer has
    one stretch per band and `COGLayer` takes a single `rescaleMin`/`rescaleMax`
    pair, so collapsing three into one would be an invention - `read_raster`
    reports that instead.
    """
    renderer = layer.renderer()
    if renderer is None:
        return (None, None)

    # Pseudocolour keeps its range on the renderer itself.
    lower = getattr(renderer, "classificationMin", None)
    upper = getattr(renderer, "classificationMax", None)
    if callable(lower) and callable(upper):
        low, high = float(lower()), float(upper())
        if low == low and high == high and high > low:  # NaN-safe
            return (low, high)

    # Grey renderers keep it on a contrast enhancement.
    enhancement = getattr(renderer, "contrastEnhancement", None)
    if callable(enhancement):
        current = enhancement()
        if current is not None:
            low = float(current.minimumValue())
            high = float(current.maximumValue())
            if low == low and high == high and high > low:
                return (low, high)

    return (None, None)


def raster_nodata(layer: QgsRasterLayer) -> float | None:
    """The value QGIS is masking out on band 1, if there is one.

    Restated on the layer element because a conversion can lose the header
    field that carried it, and an unmasked nodata block draws as a hard slab of
    the extreme colour - which reads as data.
    """
    provider = layer.dataProvider()
    if provider is None or not provider.sourceHasNoDataValue(1):
        return None
    value = float(provider.sourceNoDataValue(1))
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinite
        return None
    return value


# Query parameters that carry an access key. A tile URL travels in the map as
# written, so a key in it is a key handed to everyone the file reaches. Compared
# lower-case with `-` and `_` removed, so `api_key`, `API-Key` and `apikey` are
# one entry, and so are Azure's `subscription-key` and AWS's `X-Amz-Signature`.
_KEY_PARAMETERS = frozenset(
    {
        "key",
        "apikey",
        "accesskey",
        "token",
        "accesstoken",
        "authtoken",
        "sastoken",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "pwd",
        "auth",
        "authkey",
        "sig",
        "signature",
        "subscriptionkey",
        "ocpapimsubscriptionkey",
        "xamzsignature",
        "xamzcredential",
        "xamzsecuritytoken",
        "xgoogsignature",
        "xgoogcredential",
    }
)


def _is_key_parameter(name: str) -> bool:
    return name.lower().replace("-", "").replace("_", "") in _KEY_PARAMETERS


def xyz_template(layer: QgsRasterLayer) -> dict[str, str] | None:
    """The parameters of an XYZ source, or `None` for any other `wms` layer.

    QGIS keeps XYZ tile layers under the `wms` provider with `type=xyz` in a
    URL-encoded source string; a real WMS has no `type`.
    """
    from urllib.parse import parse_qsl

    fields = dict(parse_qsl(layer.source() or "", keep_blank_values=True))
    return fields if fields.get("type") == "xyz" and fields.get("url") else None


def read_tile_service(
    layer: QgsRasterLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
) -> ExportLayer | None:
    """Read an XYZ tile layer as a live reference the web map fetches itself.

    Nothing is embedded - the tiles are the server's - so the map needs a
    connection to draw this layer, and the scanner says so. Two refusals: a
    template the runtime cannot fill (it takes `{z}`, `{x}`, `{y}` and `{-y}`;
    QGIS also allows `{q}` quadkeys), and a URL carrying an access key, which
    would be published to everyone the file reaches.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    layer_id = layer.id()
    name = layer.name()
    fields = xyz_template(layer) or {}
    url = fields.get("url", "")

    if not ("{z}" in url and "{x}" in url and ("{y}" in url or "{-y}" in url)):
        report.unsupported(
            f"Layer '{name}'",
            "This tile layer's address uses a pattern the web map cannot fill "
            "(it understands {z}, {x}, {y} and {-y} only). The layer is omitted "
            "from the map.",
            layer_id,
        )
        return None

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    keyless = [(key, value) for key, value in pairs if not _is_key_parameter(key)]
    # `user:password@host` is a credential just as `?key=` is.
    host = parts.netloc.rpartition("@")[2]
    keyed = len(keyless) != len(pairs) or host != parts.netloc
    if keyed:
        # Never kept, not even in the model: the export is blocked below, but a
        # snapshot or a report must not be able to carry the key either.
        url = urlunsplit(
            parts._replace(netloc=host, query=urlencode(keyless, safe="{}"))
        )
    dependency = AssetDependency(
        # The host only when keyed: the full URL would carry the key into the
        # snapshot and the report.
        identifier=host if keyed else url,
        disposition=AssetDisposition.BLOCKING if keyed else AssetDisposition.REMOTE,
        note=(
            f"The tile address for '{name}' on {host} carries an access key, and "
            "a live layer travels with its address - so the key would be written "
            "into the map for anyone who receives it to copy. Use a keyless "
            "address, or untick the layer on the Layers tab."
            if keyed
            else None
        ),
    )

    def _zoom(field: str, default: int) -> int:
        try:
            return int(float(fields.get(field, default)))
        except ValueError:
            return default

    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.FILE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=RendererSpec(
            kind=RendererKind.UNSUPPORTED,
            unsupported_reason="Tile layers carry no vector symbology.",
        ),
        popup=PopupSpec(enabled=False),
        tiles=TileSpec(url, _zoom("zmin", 0), _zoom("zmax", 19)),
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(dependency,),
    )


def read_raster(
    layer: QgsRasterLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
) -> ExportLayer | None:
    """Read one raster layer into the normalized model.

    Returns `None` for a raster there is no file to carry - a WMS or XYZ layer,
    or a source that has gone missing - after recording why. That is the same
    contract `read_layer` has always had; what changed is that a plain GeoTIFF
    on disk no longer falls into it.

    Everything the model gets is metadata: the pixels stay in the file and
    reach the artifact through packaging, which converts the source to a Cloud
    Optimized GeoTIFF (`packaging/raster_cog`) and fills in `RasterSpec.src`.
    This function must not do that work itself - it would mean importing GDAL
    into the reader, and it would mean converting a file the user may yet
    cancel out of exporting.

    Colour takes one of two routes, decided in `core.raster_style`:

    * **A multiband composite** is carried as data, with its R/G/B band numbers
      on the layer element. Those pixels are already the colours the author
      sees. Per-band contrast stretch is the one thing lost, and is reported.
    * **Everything else** - pseudocolour, paletted, singleband grey, hillshade,
      contour - has its style captured as QML here and rendered into the pixels
      by `packaging.raster_bake` during the export. QGIS invents those colours
      at draw time from an arbitrary stop list, and `COGLayer` has no way to
      express one, so reproducing them means letting QGIS draw.

    Still not translated, and reported rather than guessed: resampling method
    and blend mode, neither of which the runtime's schema has an attribute for.
    """
    layer_id = layer.id()
    name = layer.name()
    provider = layer.dataProvider()
    provider_key = (layer.providerType() or "").lower()

    if provider_key == "wms" and xyz_template(layer) is not None:
        return read_tile_service(layer, report, group_path=group_path, visible=visible)

    if provider_key not in _RASTER_FILE_PROVIDERS or provider is None:
        report.unsupported(
            f"Layer '{name}'",
            "This is a live map service the web map cannot request: it draws "
            "XYZ tiles, but a WMS, WCS or ArcGIS service asks for each image by "
            "its map extent, which it has no way to do. Use the service's XYZ "
            "or WMTS tile address instead if it has one, or save the layer to a "
            "GeoTIFF. The layer is omitted from the map.",
            layer_id,
        )
        return None

    path = raster_source_path(layer)
    if not path:
        report.unsupported(
            f"Layer '{name}'",
            "The layer's data source is not a plain file path, so it cannot be "
            "converted for the web. Export it to a GeoTIFF from QGIS and add "
            "that instead. The layer is omitted from the map.",
            layer_id,
        )
        return None

    band_count = int(provider.bandCount())
    rescale_min, rescale_max = raster_rescale(layer)
    bands, style_qml = raster_style(layer)
    extent = raster_extent(layer)
    crs = layer.crs()
    # Set when the author assigned a CRS in QGIS that the file does not carry
    # (none, or a different one): packaging reopens the file, which would put
    # the raster where its own header says rather than where QGIS draws it.
    assigned_crs = crs.isValid() and crs != provider.crs()

    raster = RasterSpec(
        path=path,
        band_count=band_count,
        source_crs=crs.authid() if crs.isValid() else None,
        assigned_crs_wkt=crs.toWkt() if assigned_crs else None,
        nodata_rules=raster_nodata_rules(layer),
        extent=extent,
        pixel_width=int(provider.xSize()),
        pixel_height=int(provider.ySize()),
        resolution_x=float(layer.rasterUnitsPerPixelX()),
        resolution_y=float(layer.rasterUnitsPerPixelY()),
        nodata=raster_nodata(layer),
        rescale_min=rescale_min,
        rescale_max=rescale_max,
        bands=bands,
        style_qml=style_qml,
        # Left `None` on purpose: answering it needs GDAL. See `RasterSpec`.
        is_cog=None,
    )

    # A missing file is a blocker rather than an omission: the user pointed at
    # something, QGIS drew it from a cache or is drawing nothing, and quietly
    # shipping a map without it is exactly the silent gap this project rejects.
    missing = not _file_exists(path)
    dependency = AssetDependency(
        identifier=path,
        disposition=(
            AssetDisposition.BLOCKING if missing else AssetDisposition.EMBEDDABLE
        ),
        size_bytes=_file_size(path),
        note=(
            f"The raster file '{path}' is missing, so its pixels cannot be put "
            "into the map. Repair the layer's data source in QGIS, or remove "
            "the layer."
            if missing
            else "The raster is converted to a Cloud Optimized GeoTIFF and "
            "carried inside the map, so the recipient needs no access to the "
            "original file."
        ),
    )

    report.preserved(
        f"Layer '{name}'",
        f"The raster is exported as a Cloud Optimized GeoTIFF "
        f"({raster.pixel_width} x {raster.pixel_height} pixels, "
        f"{band_count} band{'s' if band_count != 1 else ''}) and drawn in "
        "place, keeping its position, opacity and place in the layer order.",
        layer_id,
    )

    # Colour, which is the single biggest thing a raster can lose. Three
    # outcomes, and which one applies is decided by `core.raster_style`:
    if style_qml is not None:
        # Rasterised: the colours become pixels, which is exactly the trade the
        # detail spells out.
        report.raster_fallback(
            f"Colours of '{name}'",
            "The colour ramp, classes and contrast stretch you set in QGIS are "
            "rendered into the exported image, so the map shows exactly the "
            "colours QGIS shows. The trade is that the exported file carries "
            "those colours rather than the underlying values, so the map "
            "cannot be restretched or read for measurements afterwards.",
            layer_id,
        )
    elif bands is not None:
        report.preserved(
            f"Colours of '{name}'",
            f"The layer is a {band_count}-band colour composite, so its own "
            f"pixels are the colours you see. Bands {', '.join(str(b) for b in bands)} "
            "are drawn in red, green and blue order as they are in QGIS.",
            layer_id,
        )
        if not raster.has_rescale:
            report.approximated(
                f"Contrast of '{name}'",
                "A composite has one contrast stretch per band and the map "
                "takes a single pair, so the per-band stretch you set in QGIS "
                "is not carried and the map stretches each band across its own "
                "range. The image may look lighter or darker than it does in "
                "QGIS.",
                layer_id,
            )
    else:
        # `raster_style` could not serialise the layer's styling. Rare, and it
        # means the map draws the values rather than the picture - which is the
        # old behaviour, so it is reported the way the old behaviour was.
        report.unsupported(
            f"Colours of '{name}'",
            "This layer's styling could not be read out of QGIS, so the map "
            "draws the raster from its values in greyscale rather than in the "
            "colours you set. Re-applying the style in the layer's Symbology "
            "tab and exporting again usually fixes it.",
            layer_id,
        )

    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.FILE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        # Everything vector stays at its default, which `raster is not None`
        # marks as inapplicable rather than empty - see `RasterSpec`.
        renderer=RendererSpec(
            kind=RendererKind.UNSUPPORTED,
            unsupported_reason="Raster layers carry no vector symbology.",
        ),
        popup=PopupSpec(enabled=False),
        raster=raster,
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(dependency,),
    )


def _file_exists(path: str) -> bool:
    from pathlib import Path

    try:
        return Path(path).is_file()
    except OSError:  # pragma: no cover - an unreadable path is a missing one
        return False


def _file_size(path: str) -> int | None:
    from pathlib import Path

    try:
        return Path(path).stat().st_size
    except OSError:
        return None


# A white halo: names stay readable over imagery, fills and other names.
NAME_HALO = Color(255, 255, 255, 0.9)


def point_names(labeling: LabelingSpec, field_name: str) -> LabelingSpec:
    """`labeling` showing `field_name`, always, with a halo.

    Built on the layer's own labelling when it has one, so its font and size
    carry over; a halo is added only where QGIS set none.
    """
    return dataclasses.replace(
        labeling,
        enabled=True,
        field_name=field_name,
        halo_color=labeling.halo_color or NAME_HALO,
        halo_width=labeling.halo_width or 1.0,
    )


@dataclasses.dataclass(frozen=True)
class PendingLayer:
    """A vector layer read as far as the GUI thread can take it.

    `prepare_layer` fills in everything that needs the live `QgsVectorLayer` -
    symbology, labels, popup, source, the feature snapshot - and leaves the one
    expensive step, reading and serialising every feature, to `finish`. The
    split is the boundary a background export runs on: `prepare_layer` on the
    GUI thread, `finish` on a worker. `finish` reaches no layer, so it is safe
    to run after the layer has been removed from the project.
    """

    base: ExportLayer
    features: LayerFeatures
    precision: int
    clip_extent: Any | None
    drawing: frozenset[str]
    renumber_ids: bool = False

    def finish(self, report: FidelityReportBuilder) -> ExportLayer | None:
        """Read the features and complete the layer. **Safe off the GUI thread.**"""
        base = self.base
        geojson = export_geojson_from(
            self.features,
            report,
            precision=self.precision,
            clip_extent=self.clip_extent,
        )
        if geojson is None:
            return None

        # Before counting: a switched-off category means the author does not want
        # those features on the map at all, so they must leave the data, not just
        # the style. The count has to reflect what actually ships.
        read_count = len(geojson.get("features") or ())
        geojson = drop_hidden_features(geojson, base.renderer)

        # Only the attributes something still reads survive: what the popup shows,
        # plus what the map draws with. A value the user hid must leave the file,
        # not merely the popup - see `keep_only_fields`.
        keep = set(self.drawing)
        if base.popup.enabled:
            keep |= {field.name for field in base.popup.visible_fields}
        # The stored value of a field whose shown value the popup reads stays
        # too: what the popup reads is a copy, and a map or atlas that reads the
        # field by its own name still gets the value it was built against.
        keep |= {fmt.name for fmt in self.features.formats}
        geojson = keep_only_fields(geojson, keep)
        feature_ids: tuple[tuple[int, int], ...] = ()
        if self.renumber_ids:
            geojson, feature_ids = renumber_feature_ids(geojson)

        # After the strip so only surviving fields are considered; drawing fields
        # are protected because accessors reference them by their real name.
        geojson, popup = rename_untemplatable_fields(
            geojson,
            base.popup,
            set(self.drawing),
            report,
            f"Popup fields of '{base.name}'",
            base.layer_id,
        )

        feature_count = len(geojson.get("features") or ())
        if feature_count == 0 and read_count > 0:
            report.unsupported(
                f"Layer '{base.name}'",
                f"The layer has {read_count} feature(s), but every one belongs to "
                "a category or range that is switched off, or that no category "
                "or range claims, so none is drawn. It is exported but will show "
                "nothing - check the layer's symbology.",
                base.layer_id,
            )
        elif feature_count == 0:
            report.unsupported(
                f"Layer '{base.name}'",
                "The layer contains no features. It is exported but will show "
                "nothing - check for an active filter or an empty data source.",
                base.layer_id,
            )

        return dataclasses.replace(
            base,
            popup=popup,
            feature_count=feature_count,
            geojson=geojson,
            feature_ids=feature_ids,
        )


def read_layer(
    layer: QgsMapLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
    with_popup: bool = True,
    with_labels: bool = True,
    field_modes: Mapping[str, str] | None = None,
    precision: int | None = None,
    popup_on_hover: bool = False,
    highlight_color: Color | None = None,
    project: QgsProject | None = None,
    clip_extent: Any | None = None,
    name_field: str | None = None,
    cluster: bool = True,
) -> ExportLayer | None:
    """Read one layer into the normalized model, on the calling thread.

    `prepare_layer` and `PendingLayer.finish` back to back, for callers that are
    not a background job. See `prepare_layer` for the arguments.
    """
    prepared = prepare_layer(
        layer,
        report,
        group_path=group_path,
        visible=visible,
        with_popup=with_popup,
        with_labels=with_labels,
        field_modes=field_modes,
        precision=precision,
        popup_on_hover=popup_on_hover,
        highlight_color=highlight_color,
        project=project,
        clip_extent=clip_extent,
        name_field=name_field,
        cluster=cluster,
    )
    if isinstance(prepared, PendingLayer):
        return prepared.finish(report)
    return prepared


def prepare_layer(
    layer: QgsMapLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
    with_popup: bool = True,
    with_labels: bool = True,
    field_modes: Mapping[str, str] | None = None,
    precision: int | None = None,
    popup_on_hover: bool = False,
    highlight_color: Color | None = None,
    project: QgsProject | None = None,
    clip_extent: Any | None = None,
    name_field: str | None = None,
    cluster: bool = True,
    scans: LayerScans | None = None,
) -> ExportLayer | PendingLayer | None:
    """Read one layer into the normalized model, up to its features. **GUI
    thread only** - it reads the live layer.

    Raster layers are handed straight to `read_raster` and come back complete;
    everything below is the vector path, which comes back as a `PendingLayer`
    whose `finish` reads the features. Returns `None` for layers with no web
    equivalent at all - a mesh or point cloud, a vector layer with no geometry,
    a raster that is a live service rather than a file - after recording why.

    `project` is needed only to read a 2.5D renderer's height, which QGIS keeps
    as a project variable rather than on the layer. Passed in rather than taken
    from `QgsProject.instance()` so a caller reading a project it opened itself
    gets that project's height and not the running QGIS session's.

    `with_popup` and `with_labels` carry the dialog's per-layer checkboxes. They
    suppress the feature *here*, at the point of translation, rather than in the
    manifest builder: a user who unticked labels should not pay for the label
    points in the artifact, and the fidelity report should not list translation
    notes for something the user asked not to export.
    """
    layer_id = layer.id()
    name = layer.name()

    if layer.type() == QgsMapLayerType.RasterLayer:
        # Rasters take none of the vector arguments above: they have no fields
        # to hide, no popup to build, no labels and no geometry to quantise.
        # Passing them along would only make the seam look wider than it is.
        return read_raster(layer, report, group_path=group_path, visible=visible)

    if layer.type() != QgsMapLayerType.VectorLayer:
        report.unsupported(
            f"Layer '{name}'",
            "Only vector and raster layers are exported. Mesh, point-cloud and "
            "annotation layers have no equivalent in the map runtime, so this "
            "layer is omitted from the map.",
            layer_id,
        )
        return None

    kind = geometry_kind(layer)
    if kind is GeometryKind.UNKNOWN:
        report.unsupported(
            f"Layer '{name}'",
            "The layer has no geometry (an attribute-only table), so there is "
            "nothing to draw on a map. It is omitted.",
            layer_id,
        )
        return None

    renderer = translate_renderer(layer, report)

    # After the renderer, because the atlas rasterises the very symbols the
    # renderer just described, and re-emits it with each class naming its icon.
    # Layers that draw with plain circles - almost all of them - get `None` back
    # and the renderer they came in with, untouched.
    icon_atlas, renderer = build_icon_atlas(
        layer.renderer(),
        renderer,
        kind,
        report,
        name,
        layer_id,
        reference_render_context(layer),
        ground_units(layer),
    )
    report_ground_sizes(renderer, icon_atlas is not None, kind, report, name, layer_id)

    labeling = (
        translate_labeling(layer, report, scans) if with_labels else LabelingSpec()
    )
    if (
        name_field
        and kind is GeometryKind.POINT
        and layer.fields().indexOf(name_field) >= 0
    ):
        labeling = point_names(labeling, name_field)
        report.preserved(
            f"Names on '{name}'",
            f"Each point's '{name_field}' shows beside it with a halo, without "
            "a click. Like labels, the names appear from the zoom where most of "
            "them have room.",
            layer_id,
        )
    elevation = translate_elevation(layer, report, kind, project)
    # Explicitly disabled, not a default `PopupSpec()`: that one is enabled.
    popup = (
        translate_popup(layer, report, field_modes=field_modes, on_hover=popup_on_hover)
        if with_popup
        else PopupSpec(enabled=False)
    )
    # A field the popup shows through an editor widget (a Value Map's
    # description) is read from a separate property, so the stored value stays
    # under the field's own name for everything that draws or filters.
    display_names = popup_display_names(layer, popup)
    popup = popup_reading_display(popup, display_names)

    # The primary-key check reads the live layer, so it is made here; the strip
    # it guards happens in `PendingLayer.finish`.
    keep = set(drawing_fields(renderer, labeling, elevation))
    if popup.enabled:
        keep |= {field.name for field in popup.visible_fields}
    keep |= set(display_names)
    renumber_ids = primary_key_is_excluded(layer, keep)
    features = snapshot_features(layer, clip_extent, display_names, keep, scans)

    base = ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=kind,
        source_kind=source_kind(layer),
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=renderer,
        icon_atlas=icon_atlas,
        labeling=labeling,
        elevation=elevation,
        popup=popup,
        highlight_color=highlight_color,
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(describe_source(layer),),
        cluster_allowed=cluster,
    )
    return PendingLayer(
        base=base,
        features=features,
        precision=GEOJSON_PRECISION if precision is None else precision,
        clip_extent=clip_extent,
        drawing=frozenset(drawing_fields(renderer, labeling, elevation)),
        renumber_ids=renumber_ids,
    )
