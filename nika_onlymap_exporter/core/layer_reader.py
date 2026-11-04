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
    QgsExpression,
    QgsExpressionContext,
    QgsFeatureRequest,
    QgsJsonExporter,
    QgsMapLayerType,
    QgsReadWriteContext,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtXml import QDomDocument

from .attribute_filter import FilterOutcome, FilterRequest, build_filter
from .elevation_translator import translate_elevation
from .export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ElevationSpec,
    ExportLayer,
    Extent,
    FilterKind,
    GeometryKind,
    LabelingSpec,
    LegendImage,
    NoDataRule,
    NumberFormat,
    PopupSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    ScaleRange,
    SourceKind,
    TileSpec,
)
from .fidelity_report import FidelityReportBuilder
from .labeling_translator import (
    collect_character_set,
    expression_label_property,
    format_label_number,
    label_expression_context,
    translate_labeling,
)
from .popup_translator import rename_untemplatable_fields, translate_popup
from .raster_style import composite_bands, hue_saturation_active, should_bake
from .renderer_translator import (
    RuleMatcher,
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
# OGC API Features (`OAPIF`) and SensorThings are web feature services too,
# with their own provider keys since QGIS 3.14 and 3.36.
_SERVICE_PROVIDERS = frozenset(
    {"wfs", "oapif", "sensorthings", "arcgisfeatureserver", "ows", "wms"}
)


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
    attribution = (layer.serverProperties().attribution() or "").strip()
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
class LabelExpression:
    """A label expression to work out for each feature as it is read.

    Built on the GUI thread, where the layer is, and worked out on the worker
    beside the shown values. `context` holds the global, project and layer
    scopes QGIS labels in, short of a map: QGIS's own renderer builds them the
    same way before its job leaves the GUI thread. The layer scope holds the
    layer only weakly, so a layer removed meanwhile leaves an expression that
    asks about it (`layer_property(@layer, ...)`) with NULL, not a crash.
    """

    expression: str
    # The property the text is written under (`LabelingSpec.field_name`).
    property_name: str
    context: QgsExpressionContext
    # The fields the expression reads, which the read must fetch; `None` when
    # it may read any of them (`attribute(@feature, ...)`).
    columns: frozenset[str] | None
    # QGIS's "Formatted numbers", applied to each text that reads as a number.
    number_format: NumberFormat | None = None

    def texts(
        self,
        features: list[QgsFeature],
        report: FidelityReportBuilder,
        layer_name: str,
        layer_id: str,
    ) -> list[dict[str, Any]]:
        """Per feature, its label text under `property_name`, `None` for no
        label. A feature the expression fails on has none, as in QGIS, and
        the failures are reported once."""
        # QGIS draws the result's `QVariant::toString()`, which PyQt6 does not
        # offer; `to_string` is that same conversion, so a number, date or
        # list reads exactly as QGIS draws it ("12" for 12.0, "1e+06" for a
        # million, nothing for an array). On lines of its own, so a trailing
        # `--` comment in the expression cannot swallow the parenthesis.
        expression = QgsExpression(f"to_string(\n{self.expression}\n)")
        context = QgsExpressionContext(self.context)
        expression.prepare(context)
        texts: list[dict[str, Any]] = []
        failed = 0
        first_error = ""
        for feature in features:
            context.setFeature(feature)
            value = expression.evaluate(context)
            if expression.hasEvalError():
                failed += 1
                first_error = first_error or expression.evalErrorString()
                value = None
            if value and self.number_format is not None:
                value = format_label_number(value, self.number_format)
            texts.append({self.property_name: value or None})
        if failed:
            report.approximated(
                f"Labels on '{layer_name}'",
                f"The label expression could not be worked out for {failed:,} "
                f"of {len(features):,} feature(s) ({first_error}), so they have "
                "no label, as in QGIS.",
                layer_id,
            )
        return texts


def label_expression(
    layer: QgsVectorLayer, labeling: LabelingSpec
) -> LabelExpression | None:
    """What working out `labeling`'s expression needs, `None` when its labels
    come from a field or are off. **GUI thread only.**"""
    if not (labeling.enabled and labeling.expression and labeling.field_name):
        return None
    parsed = QgsExpression(labeling.expression)
    columns = parsed.referencedColumns()
    return LabelExpression(
        expression=labeling.expression,
        property_name=labeling.field_name,
        context=label_expression_context(layer),
        columns=(
            None
            if QgsFeatureRequest.ALL_ATTRIBUTES in columns
            else frozenset(str(name) for name in columns)
        ),
        number_format=labeling.number_format,
    )


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
    # The label expression worked out per feature as it is read, `None` for
    # labels from a field or none at all.
    label: LabelExpression | None = None
    # A rule-based layer's rules, which write each feature's class (see
    # `RuleMatcher`), and the indexes of the fields their filters read on top
    # of those kept - read, not written. `None` when they read every field.
    rules: RuleMatcher | None = None
    rule_indexes: tuple[int, ...] | None = ()

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
    label: LabelExpression | None = None,
    rules: RuleMatcher | None = None,
) -> LayerFeatures:
    """Copy out what `export_geojson_from` needs. **GUI thread only.**

    `display_names` is `popup_display_names`: the fields to write a shown-value
    copy of, which is none for a caller that has no popup. `keep` is the names
    of the fields the export keeps, which are then the only ones read, with
    those `label`'s expression reads (written, then dropped by
    `keep_only_fields`) and those `rules` read (read, never written); `None`
    reads them all. `scans` as for `prepare_layer`.
    """
    from qgis.core import QgsProject

    formats = _field_formats(layer, display_names or {}, scans)
    fields = layer.fields()
    attribute_indexes = None
    reads = frozenset() if label is None else label.columns
    if keep is not None and reads is not None:
        # The expression's fields are read but not kept: only its text is
        # written, so a field it reads that the popup hides stays out.
        read = set(keep) | reads
        attribute_indexes = tuple(
            sorted({i for i in map(fields.indexOf, read) if i >= 0})
        )
    rule_indexes: tuple[int, ...] | None = ()
    if rules is not None:
        used = rules.used_attributes
        rule_indexes = (
            None
            if used is None
            else tuple(sorted({i for i in map(fields.indexOf, used) if i >= 0}))
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
        label=label,
        rules=rules,
        rule_indexes=rule_indexes,
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
            # With the fields the rules read, which are not written: unless
            # they read every field, when the request is left reading them all.
            if features.rule_indexes is not None:
                request.setSubsetOfAttributes(
                    sorted({*indexes, *features.rule_indexes})
                )
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
        if features.label is not None:
            _add_display_values(
                collection,
                features.label.texts(collected, report, features.layer_name, layer_id),
            )
        if features.rules is not None:
            _add_rule_classes(
                collection,
                features.rules,
                collected,
                report,
                features.layer_name,
                layer_id,
            )
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
    """Write each feature's shown values (or label text) into its
    properties, in place."""
    exported = collection.get("features") or []
    if len(exported) != len(shown):
        raise RuntimeError("the exporter returned a different number of features")
    for feature, values in zip(exported, shown, strict=True):
        feature["properties"] = {**(feature.get("properties") or {}), **values}


def _add_rule_classes(
    collection: dict[str, Any],
    rules: RuleMatcher,
    collected: list[QgsFeature],
    report: FidelityReportBuilder,
    layer_name: str,
    layer_id: str,
) -> None:
    """Write which rule draws each feature into its properties, in place, and
    say how many features more than one rule draws."""
    exported = collection.get("features") or []
    values, stacked = rules.values(collected)
    if len(exported) != len(values):
        raise RuntimeError("the exporter returned a different number of features")
    for feature, value in zip(exported, values, strict=True):
        feature["properties"] = {
            **(feature.get("properties") or {}),
            rules.field_name: value,
        }
    if stacked:
        report.approximated(
            f"Symbology of '{layer_name}'",
            f"{stacked:,} feature{' is' if stacked == 1 else 's are'} drawn by "
            "more than one rule in QGIS, one symbol over another. The web map "
            "draws each once, with the symbol of the last of those rules - the "
            "one on top.",
            layer_id,
        )


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


def _carries_key(url: str) -> bool:
    """Whether `url` carries an access key: a key parameter, or
    `user:password@host`, which is a credential just as `?key=` is."""
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    return "@" in parts.netloc or any(_is_key_parameter(key) for key, _ in pairs)


def _live_dependency(
    url: str, name: str, what: str = "tile address"
) -> tuple[str, AssetDependency]:
    """`url` without any access key, and the dependency a live layer on it is.

    A keyed address blocks the export: the key would travel with the map.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    keyless = [(key, value) for key, value in pairs if not _is_key_parameter(key)]
    # `user:password@host` is a credential just as `?key=` is.
    host = parts.netloc.rpartition("@")[2]
    keyed = _carries_key(url)
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
            f"The {what} for '{name}' on {host} carries an access key, and "
            "a live layer travels with its address - so the key would be written "
            "into the map for anyone who receives it to copy. Use a keyless "
            "address, or untick the layer on the Layers tab."
            if keyed
            else None
        ),
    )
    return url, dependency


def _source_type(layer: QgsRasterLayer) -> str:
    """The `type=` of a `wms` provider source: `xyz`, `mbtiles`, `wmst`,
    `wmts`, or `""` for a plain WMS (or a WMTS saved without one)."""
    from .map_services import source_pairs, value

    return value(source_pairs(layer.source()), "type")


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
    template the runtime cannot fill (it takes `{z}`, `{x}`, `{y}` and `{-y}`,
    and QGIS's `{q}` quadkey as its `{quadkey}`), and a URL carrying an access
    key, which would be published to everyone the file reaches.
    """
    layer_id = layer.id()
    name = layer.name()
    fields = xyz_template(layer) or {}
    # QGIS's Bing-style quadkey token is the runtime's `{quadkey}`.
    url = fields.get("url", "").replace("{q}", "{quadkey}")

    tokens = set(re.findall(r"\{(-?[A-Za-z][\w-]*)\}", url))
    if not (
        ({"z", "x"} <= tokens and tokens & {"y", "-y"}) or "quadkey" in tokens
    ) or not tokens <= {"z", "x", "y", "-y", "quadkey"}:
        report.unsupported(
            f"Layer '{name}'",
            "This tile layer's address uses a pattern the web map cannot fill "
            "(it understands {z}, {x}, {y}, {-y} and {q} only). The layer is "
            "omitted from the map.",
            layer_id,
        )
        return None

    url, dependency = _live_dependency(url, name)

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


@dataclasses.dataclass(frozen=True)
class MapTime:
    """The time the QGIS map view draws, as its map settings hold it.

    `is_temporal` and `range` are `QgsMapSettings.isTemporal()` and
    `temporalRange()` (a `QgsDateTimeRange`) of the canvas: the temporal
    controller sets both, and an empty range with `is_temporal` off when it is
    switched off. Read on the GUI thread and handed in, as the canvas extent
    is; `None` in its place means there is no view to read it from.
    """

    is_temporal: bool
    range: Any = None


# Source-string keys that carry a login QGIS sends with every request. A web
# page cannot send it without publishing it, and without it the service
# refuses, so such a layer cannot be drawn by the exported map at all.
_LOGIN_KEYS = ("username", "password", "authcfg", "referer")


def _cached_capabilities(url: str) -> bytes | None:
    """The capabilities QGIS fetched from `url`, from its network cache.

    Read from the cache's files only: the load control never reaches the
    network, so an export made offline reads exactly what one made online
    does. `None` when QGIS kept no copy.
    """
    from qgis.core import QgsNetworkAccessManager
    from qgis.PyQt.QtCore import QUrl

    cache = QgsNetworkAccessManager.instance().cache()
    if cache is None:
        return None
    device = cache.data(QUrl(url))
    if device is None:
        return None
    try:
        return bytes(device.readAll())
    finally:
        device.close()


def _service_legend(
    name: str,
    found: list[tuple[str, str]],
    report: FidelityReportBuilder,
    layer_id: str,
) -> tuple[LegendImage, ...]:
    """The legend pictures of a WMS or WMTS layer, from `(title, address)`
    pairs; a title is kept only when there are several pictures to tell
    apart. An address carrying an access key is left out, with a row: the
    browser would load it, key and all, from the page anyone can read.
    """
    from urllib.parse import urlsplit

    images: list[LegendImage] = []
    for title, address in found:
        if _carries_key(address):
            host = urlsplit(address).netloc.rpartition("@")[2]
            report.unsupported(
                f"Legend of '{name}'",
                f"The address of this layer's legend picture on {host} carries "
                "an access key, which the legend would write into the map for "
                "anyone who receives it to copy. The picture is left out of the "
                "legend, which lists the layer by its name.",
                layer_id,
            )
            continue
        images.append(LegendImage(address, title if len(found) > 1 else ""))
    return tuple(images)


def _map_service_layer(
    layer: QgsRasterLayer,
    tiles: TileSpec,
    dependency: AssetDependency,
    group_path: tuple[str, ...],
    visible: bool,
) -> ExportLayer:
    return ExportLayer(
        layer_id=layer.id(),
        name=layer.name(),
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=RendererSpec(
            kind=RendererKind.UNSUPPORTED,
            unsupported_reason="Tile layers carry no vector symbology.",
        ),
        popup=PopupSpec(enabled=False),
        tiles=tiles,
        attribution=read_attribution(layer),
        group_path=group_path,
        dependencies=(dependency,),
    )


def read_map_service(
    layer: QgsRasterLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
    map_time: MapTime | None = None,
) -> ExportLayer | None:
    """Read a WMS or WMTS layer as a live tile layer, or refuse it with a reason.

    The web map asks the service itself, tile by tile, in Web Mercator - see
    `core.map_services`, which builds the request from the layer's source
    string and the capabilities QGIS keeps in its network cache. Like an XYZ
    layer, nothing is embedded and a keyed address blocks the export. Refused
    with a row saying what to do: a login, a format that is not an image, a
    time the layer takes from QGIS's temporal controls, a WMS without Web
    Mercator and a WMTS on another tile grid.

    A WMS-T layer that follows QGIS's time is a snapshot: the template asks
    for the `TIME` QGIS sends now, worked out from `map_time` as QGIS works
    it out (`_wms_snapshot_time`), and is refused when that cannot be told.

    The legend pictures QGIS's legend shows for the layer travel the same
    way, as addresses the browser loads (`TileSpec.legend`).
    """
    from urllib.parse import urlsplit

    from . import map_services

    layer_id = layer.id()
    name = layer.name()
    subject = f"Layer '{name}'"
    pairs = map_services.source_pairs(layer.source())
    url = map_services.value(pairs, "url")
    is_wmts = any(key == "tileMatrixSet" for key, _ in pairs)
    kind = "WMTS" if is_wmts else "WMS"

    if any(key in _LOGIN_KEYS or key.startswith("http-header:") for key, _ in pairs):
        report.unsupported(
            subject,
            f"This {kind} service needs a login or a custom request header "
            "that QGIS sends for you. A web page cannot send it without "
            "publishing it, and the service refuses requests without it. The "
            "layer is omitted from the map.",
            layer_id,
        )
        return None

    image_format = map_services.value(pairs, "format")
    if not image_format.lower().startswith("image/"):
        report.unsupported(
            subject,
            f"This {kind} layer is requested as '{image_format}', not as images, "
            "and the web map can only draw image tiles. Choose an image format "
            "such as image/png for the layer in the Data Source Manager. The "
            "layer is omitted from the map.",
            layer_id,
        )
        return None

    snapshot = _is_wmst_snapshot(pairs)
    time: str | None = None
    if snapshot:
        time, refusal = _wms_snapshot_time(layer, pairs, map_time)
        if refusal is not None:
            report.unsupported(subject, refusal, layer_id)
            return None

    capabilities = _cached_capabilities(map_services.capabilities_url(url))
    if is_wmts:
        matrix_set = map_services.value(pairs, "tileMatrixSet")
        found = map_services.wmts_tiles(
            capabilities,
            layer=(map_services.values(pairs, "layers") or [""])[0],
            style=(map_services.values(pairs, "styles") or [""])[0],
            image_format=image_format,
            matrix_set=matrix_set,
            dimensions=map_services.parse_tile_dimensions(
                map_services.value(pairs, "tileDimensions")
            ),
        )
        if isinstance(found, map_services.WmtsRefusal):
            report.unsupported(
                subject, _wmts_refusal(found.reason, matrix_set), layer_id
            )
            return None
        template, dependency = _live_dependency(found.template, name)
        host = urlsplit(template).netloc
        legend_url = map_services.wmts_legend_url(
            capabilities,
            layer=(map_services.values(pairs, "layers") or [""])[0],
            style=(map_services.values(pairs, "styles") or [""])[0],
            image_format=image_format,
            source=layer.source(),
        )
        tiles = TileSpec(
            template,
            found.min_zoom,
            found.max_zoom,
            found.tile_size,
            legend=_service_legend(
                name, [("", legend_url)] if legend_url else [], report, layer_id
            ),
        )
        report.approximated(
            subject,
            f"Exported as a live WMTS layer: the web map loads its tiles from "
            f"{host} on the Web Mercator tile grid '{matrix_set}', as QGIS does. "
            + _CORS_NOTE,
            layer_id,
        )
        return _map_service_layer(layer, tiles, dependency, group_path, visible)

    parsed = map_services.parse_wms_capabilities(capabilities)
    # Top first, as the source string lists them. QGIS's GetMap names them the
    # other way round, bottom first, since a WMS draws the first one at the
    # bottom: QGIS 3.44 and 4.2 both ask in the reverse of `layer.source()`
    # (4.2 also reverses the source string itself when it loads one, so a
    # project read back stacks the other way in QGIS too). Styles stay paired.
    layers = map_services.values(pairs, "layers")
    styles = map_services.values(pairs, "styles")
    source_crs = map_services.value(pairs, "crs")
    request_crs = map_services.web_mercator_code(source_crs) or (
        parsed.offered_web_mercator(layers) if parsed is not None else None
    )
    if request_crs is None:
        report.unsupported(
            subject,
            _wms_refusal(parsed is not None),
            layer_id,
        )
        return None

    # What QGIS itself speaks to the service, when the layer loaded.
    provider = layer.dataProvider()
    provider_metadata = getattr(provider, "metadata", None)
    metadata = provider_metadata() if callable(provider_metadata) else None
    known = metadata.get("WmsVersion") if isinstance(metadata, dict) else None
    version = (
        known
        or (parsed.version if parsed is not None else "")
        or map_services.value(pairs, "Version")
        or map_services.value(pairs, "version")
        or "1.3.0"
    )
    # QGIS asks the GetMap address the capabilities name, unless the layer
    # is set to ignore it (`QgsWmsProvider::getMapUrl`).
    get_map = url
    if (
        parsed is not None
        and parsed.get_map_url
        and "IgnoreGetMapUrl" not in (key for key, _ in pairs)
    ):
        get_map = parsed.get_map_url
    extra = [
        (key.upper(), ",".join(map_services.values(pairs, key)))
        for key in ("filter", "opacities")
        if map_services.values(pairs, key)
    ]
    template = map_services.wms_get_map_template(
        map_services.prepare_url(get_map).rstrip("?&"),
        version,
        layers[::-1],
        styles[::-1],
        image_format,
        request_crs,
        extra,
        time=time,
    )
    template, dependency = _live_dependency(template, name)
    host = urlsplit(template).netloc
    # The legend pictures QGIS's legend shows, from the same capabilities;
    # none without them, since nothing else says where the service draws one.
    # Top first, as a legend lists layers: the reverse of the GetMap's order.
    legend = (
        map_services.wms_legend_urls(
            parsed,
            layers,
            styles,
            image_format,
            url=url if "IgnoreGetMapUrl" in (key for key, _ in pairs) else None,
        )
        if parsed is not None
        else []
    )
    # QGIS's "Use contextual WMS legend", read as QGIS reads it: a whole
    # number, on when it is not 0. Its legend then shows only what is in view.
    contextual = map_services.value(pairs, "contextualWMSLegend").strip()
    tiles = TileSpec(
        template,
        0,
        19,
        map_services.WMS_TILE_SIZE,
        legend=_service_legend(name, legend, report, layer_id),
        contextual_legend=contextual.lstrip("+-").isdigit() and int(contextual) != 0,
    )
    projection = (
        ""
        if map_services.web_mercator_code(source_crs)
        else f" rather than the {source_crs} QGIS asks for, so text and symbols "
        "the service draws may sit or scale slightly differently"
    )
    report.approximated(
        subject,
        f"Exported as a live WMS layer: the web map asks {host} for each tile "
        f"in Web Mercator (EPSG:3857){projection}. " + _CORS_NOTE,
        layer_id,
    )
    if snapshot:
        report.approximated(
            f"Time of '{name}'",
            (
                f"Exported as a fixed snapshot at {time.replace('/', ' to ')}, "
                "the time QGIS draws now: the web map asks the service for that "
                "time only and has no time slider. To export another time, set "
                "it in QGIS and export again."
            )
            if time is not None
            else (
                "Exported as a fixed snapshot at the service's default time: "
                "QGIS asks the service for no time for this layer now (no "
                "temporal range applies to it and it has no predefined date), "
                "and neither does the web map, which has no time slider."
            ),
            layer_id,
        )
    return _map_service_layer(layer, tiles, dependency, group_path, visible)


def _is_wmst_snapshot(pairs: list[tuple[str, str]]) -> bool:
    """Whether a WMS source string is a WMS-T layer exported as a snapshot."""
    from . import map_services

    return map_services.value(pairs, "type") == "wmst" and map_services.value(
        pairs, "allowTemporalUpdates"
    ).lower() not in ("false", "0")


def follows_map_time(layer: QgsMapLayer) -> bool:
    """Whether reading `layer` uses the time the QGIS map view draws.

    True only for what `_wms_snapshot_time` reads `map_time` for: a WMS-T
    snapshot whose dynamic temporal control is on. For every other layer the
    time is never read, so a project without one does not need it at all.
    """
    from . import map_services

    if (layer.providerType() or "").lower() != "wms":
        return False
    pairs = map_services.source_pairs(layer.source())
    if not _is_wmst_snapshot(pairs):
        return False
    properties = layer.temporalProperties()
    return properties is not None and bool(properties.isActive())


def _wms_snapshot_time(
    layer: QgsRasterLayer,
    pairs: list[tuple[str, str]],
    map_time: MapTime | None,
) -> tuple[str | None, str | None]:
    """`(TIME, None)` with the WMS-T time QGIS sends for `layer` now (`None`
    when it sends none), or `(None, why not)` when that cannot be told.

    The same steps QGIS takes: `QgsRasterLayerRenderer` hands the provider the
    map's range only while the layer's dynamic temporal control is on and the
    map is temporal, and `QgsWmsProvider::addWmstParameters` falls back to the
    layer's predefined `time`, applies the layer's interval matching and
    formats the result. Closest-match matching needs the times the service
    lists, the whole range a rule not repeated here, and a reference time a
    second dimension; all three are refused.
    """
    from qgis.core import Qgis, QgsDateTimeRange
    from qgis.PyQt.QtCore import QDateTime, Qt

    from . import map_services

    retry = (
        "set the layer's Static WMS-T Temporal Range to Server default or a "
        "predefined date in its properties, and export again. The layer is "
        "omitted from the map."
    )
    properties = layer.temporalProperties()
    requested = QgsDateTimeRange(QDateTime(), QDateTime())
    if properties.isActive():
        if map_time is None:
            return None, (
                "This WMS layer has a time dimension and follows QGIS's "
                "temporal controller, and this export has no QGIS map view to "
                "read the time from, so it cannot tell which time to ask the "
                "service for. Export from the QGIS2WebMap dialog, or " + retry
            )
        follows = Qgis.RasterTemporalMode.TemporalRangeFromDataProvider
        if map_time.is_temporal:
            if properties.mode() != follows:
                return None, (
                    "This WMS layer has a time dimension, and its temporal "
                    "settings do not pass QGIS's time to the service, so the "
                    "export cannot tell which time the service draws. Set its "
                    "dynamic temporal control to the provider's time, or " + retry
                )
            requested = map_time.range

    if map_services.value(pairs, "reference_time"):
        return None, (
            "This WMS layer also asks the service for a reference time, which "
            "the web map cannot ask for. The layer is omitted from the map."
        )
    if requested.isInfinite():
        predefined = map_services.value(pairs, "time")
        if predefined:
            parts = predefined.split("/")
            if len(parts) != 2:
                return None, (
                    f"This WMS layer's predefined time '{predefined}' is not a "
                    "start and an end, so the export cannot tell which time QGIS "
                    "asks for. Choose the date again in the layer's properties. "
                    "The layer is omitted from the map."
                )
            requested = QgsDateTimeRange(
                QDateTime.fromString(parts[0], Qt.DateFormat.ISODateWithMs),
                QDateTime.fromString(parts[1], Qt.DateFormat.ISODateWithMs),
            )
    begin, end = requested.begin(), requested.end()
    if not (begin.isValid() and end.isValid()):
        return None, None

    # Only the exact matches are repeated. For the closest matches QGIS
    # picks from the times the service lists, and for the whole range QGIS
    # 4.2 was seen sending no TIME at all, by a rule not worked out here.
    method = properties.intervalHandlingMethod()
    match = Qgis.TemporalIntervalMatchMethod
    if method == match.MatchExactUsingStartOfRange:
        end = begin
    elif method == match.MatchExactUsingEndOfRange:
        begin = end
    else:
        return None, (
            "This WMS layer matches QGIS's time to the service's times in a "
            "way the export does not repeat (only a match to the start or the "
            "end of the range is), so it cannot tell which time QGIS asks for. "
            "Set the layer's matching to the start or end of the range in its "
            "Temporal properties, or " + retry
        )
    enable_time = map_services.value(pairs, "enableTime").lower() not in (
        "false",
        "0",
    )
    pattern = "yyyy-MM-ddThh:mm:ssZ" if enable_time else "yyyy-MM-dd"
    time = begin.toString(pattern)
    if begin != end:
        time += "/" + end.toString(pattern)
    return time, None


# The one condition of a live service that nothing in QGIS can check: QGIS is
# not a web page, so a service that refuses web pages works there.
_CORS_NOTE = (
    "The service must allow web pages to load its images (CORS), or this "
    "layer stays empty while the rest of the map draws; a refused request "
    "shows in the browser's developer tools."
)


def _wms_refusal(has_capabilities: bool) -> str:
    if has_capabilities:
        return (
            "This WMS does not offer Web Mercator (EPSG:3857), the only "
            "projection the web map can ask a WMS for. If the provider also "
            "publishes the layer as XYZ or WMTS tiles in Web Mercator, add that "
            "instead, or save the layer as a GeoTIFF (Export > Save As...) and "
            "add the file. The layer is omitted from the map."
        )
    return (
        "QGIS has no stored copy of this WMS's capabilities, so the export "
        "cannot tell whether it offers Web Mercator (EPSG:3857), and exporting "
        "never contacts the service to find out. Add the layer again from the "
        "Data Source Manager with EPSG:3857 as its coordinate reference system "
        "if the service lists it, and export again. The layer is omitted from "
        "the map."
    )


def _wmts_refusal(reason: str, matrix_set: str) -> str:
    if reason == "grid":
        return (
            f"This WMTS layer uses the tile grid '{matrix_set}', and the web map "
            "can only draw tiles on the Web Mercator grid (such as "
            "GoogleMapsCompatible, EPSG:3857). If the service offers that grid, "
            "add the layer again choosing it in the Data Source Manager. The "
            "layer is omitted from the map."
        )
    if reason == "time":
        return (
            "This WMTS layer has a time dimension, and QGIS asks it for the time "
            "set in its temporal controls, which the web map does not have. The "
            "layer is omitted from the map."
        )
    if reason == "not imagery":
        return (
            "This WMTS layer's tile address has no image file extension or "
            "format parameter, so the web map would read its tiles as data "
            "rather than draw them as images. If the service offers a "
            "key-value (KVP) tile address, or an address ending in .png or "
            ".jpg, add the layer that way. The layer is omitted from the map."
        )
    if reason == "no capabilities":
        return (
            "QGIS has no stored copy of this WMTS's capabilities, which say "
            "where its tiles are, and exporting never contacts the service to "
            "find out. Open the project once while online so QGIS reloads the "
            "service, and export again. The layer is omitted from the map."
        )
    return (
        "The service's capabilities give no tile address the web map can fill "
        "for this WMTS layer. The layer is omitted from the map."
    )


def read_raster(
    layer: QgsRasterLayer,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...] = (),
    visible: bool = True,
    map_time: MapTime | None = None,
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
    if provider_key == "wms" and _source_type(layer) in ("", "wmst", "wmts"):
        return read_map_service(
            layer, report, group_path=group_path, visible=visible, map_time=map_time
        )

    if provider_key == "arcgismapserver":
        report.unsupported(
            f"Layer '{name}'",
            "ArcGIS map services are not exported to the web map. If the "
            "service also publishes a WMS, or XYZ or WMTS tiles in Web Mercator, "
            "add the layer that way and it exports as a live layer; or save it "
            "as a GeoTIFF (Export > Save As...) and add the file. The layer is "
            "omitted from the map.",
            layer_id,
        )
        return None

    if provider_key not in _RASTER_FILE_PROVIDERS or provider is None:
        report.unsupported(
            f"Layer '{name}'",
            "This raster source is not a file the web map can carry, nor a "
            "WMS, WMTS or XYZ service it can ask for tiles. Save the layer as "
            "a GeoTIFF (Export > Save As...) and add the file instead. The "
            "layer is omitted from the map.",
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

    # Before anything that would treat it as a file: a raster QGIS reads over
    # the web is never downloaded, so it is drawn live from its address or
    # refused with one row saying why.
    if _is_network_path(path):
        return _read_remote_raster(layer, path, report, group_path, visible)

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
        _report_composite_colours(report, raster, name, layer_id)
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


# GDAL's network file systems, and the bare addresses GDAL opens as /vsicurl/.
_NETWORK_PREFIXES = (
    "/vsicurl",
    "/vsis3",
    "/vsigs",
    "/vsiaz",
    "/vsiadls",
    "/vsioss",
    "/vsiswift",
    "/vsihdfs",
    "/vsiwebhdfs",
    "http://",
    "https://",
    "ftp://",
)


def _is_network_path(path: str) -> bool:
    """Whether GDAL reads `path` over the network rather than from a disk."""
    lowered = path.lower()
    return lowered.startswith(_NETWORK_PREFIXES) or "/vsicurl" in lowered


def remote_raster_url(path: str) -> str | None:
    """The http(s) address a web page can fetch for a raster GDAL reads as
    `/vsicurl/https://...` or a bare `https://...`, or `None` for any other
    source, including a cloud store or a file inside a remote archive."""
    url = path[len("/vsicurl/") :] if path.startswith("/vsicurl/") else path
    return url if url.lower().startswith(("http://", "https://")) else None


_SAVE_A_LOCAL_COPY = (
    "Save a local copy (right-click the layer, Export > Save As...) and add "
    "the file instead, and it is carried inside the map. The layer is omitted "
    "from the map."
)


def _refuse_remote_raster(
    layer: QgsRasterLayer, path: str, report: FidelityReportBuilder
) -> None:
    """Refuse a raster QGIS reads over the web, without reading it.

    The export carries a raster by converting the file, and never downloads
    one from the web to do it. Only an http(s) address can be drawn live
    (`_read_remote_raster`); a cloud store or other GDAL network path is
    refused here with a row that says how to export it.
    """
    from urllib.parse import urlsplit

    url = remote_raster_url(path)
    where = (
        f"from {urlsplit(url).netloc} over the web"
        if url
        else (f"through GDAL from '{path}', a network location")
    )
    report.unsupported(
        f"Layer '{layer.name()}'",
        f"This raster is read {where}, not from a file on this computer, and "
        "the export never downloads a raster from the network. " + _SAVE_A_LOCAL_COPY,
        layer.id(),
    )


def _report_composite_colours(
    report: FidelityReportBuilder, raster: RasterSpec, name: str, layer_id: str
) -> None:
    """The colour rows of an RGB composite, which the map draws from its bands."""
    bands = raster.bands or ()
    report.preserved(
        f"Colours of '{name}'",
        f"The layer is a {raster.band_count}-band colour composite, so its own "
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


# The compressions the web map was seen to decode from a remote COG in a
# browser. OnlyMap 0.13.3 fails on LZW, ZSTD and LERC tiles in its decode
# worker and the layer stays empty, so a remote COG using anything else is
# refused. A file is converted on export, so this applies only to the web.
_LIVE_COG_COMPRESSIONS = frozenset({"NONE", "DEFLATE", "JPEG", "WEBP"})


def _remote_compression(url: str) -> str | None:
    """The compression of the remote raster QGIS has open at `url`, upper case
    and "NONE" for none, or `None` when it cannot be read.

    Reopening reads the header GDAL already fetched when QGIS opened the layer,
    from GDAL's own cache: no pixel is read, and with the header still cached
    nothing is fetched at all (`test_remote_raster` counts the requests).
    Always through `/vsicurl/`, as QGIS opens a bare address: GDAL given the
    bare address would download the whole file.
    """
    from ..packaging.raster_cog import CogError, probe_raster

    try:
        probe = probe_raster("/vsicurl/" + url)
    except CogError:
        return None
    if probe.open_error is not None:
        return None
    return (probe.compression or "NONE").upper()


def _read_remote_raster(
    layer: QgsRasterLayer,
    path: str,
    report: FidelityReportBuilder,
    group_path: tuple[str, ...],
    visible: bool,
) -> ExportLayer | None:
    """A raster QGIS reads over the web, as a live `COGLayer` or refused.

    Never downloads the file: what is checked is what QGIS already knows
    about the open layer, and the compression in the header GDAL fetched when
    it was opened. The runtime draws the file's own pixels and does not
    reproject, so only an RGB composite in Web Mercator that QGIS draws as the
    file holds it is exported; a style QGIS draws from the values is baked
    for a file, and baking would mean downloading the whole raster.
    """
    from urllib.parse import urlsplit

    url = remote_raster_url(path)
    if url is None:
        _refuse_remote_raster(layer, path, report)
        return None

    layer_id = layer.id()
    name = layer.name()
    subject = f"Layer '{name}'"
    provider = layer.dataProvider()
    host = urlsplit(url).netloc.rpartition("@")[2]
    bands, style_qml = raster_style(layer)
    if style_qml is not None or bands is None:
        report.unsupported(
            subject,
            f"This raster is read from {host} over the web, and QGIS draws its "
            "colours from its values (a colour ramp, palette, grey stretch, "
            "hillshade, transparency or colour adjustment), which the web map "
            "can only show by rendering the whole file, and the export never "
            "downloads a raster from the web. Only a 3-band colour composite "
            "QGIS draws as the file holds it is exported as a live layer. "
            + _SAVE_A_LOCAL_COPY,
            layer_id,
        )
        return None

    crs = layer.crs()
    if crs.authid() != "EPSG:3857" or crs != provider.crs():
        shown = crs.authid() or crs.description() or "an unknown CRS"
        report.unsupported(
            subject,
            f"This raster is read from {host} over the web in {shown}, and the "
            "web map draws a raster it reads over the web only in Web Mercator "
            "(EPSG:3857). A file is reprojected on export, but the export never "
            "downloads a raster from the web. " + _SAVE_A_LOCAL_COPY,
            layer_id,
        )
        return None

    compression = _remote_compression(url)
    if compression is not None and compression not in _LIVE_COG_COMPRESSIONS:
        report.unsupported(
            subject,
            f"This raster is read from {host} over the web and is "
            f"{compression}-compressed, which the web map cannot decode yet, so "
            "the layer would stay empty. A file is converted on export, but the "
            "export never downloads a raster from the web. " + _SAVE_A_LOCAL_COPY,
            layer_id,
        )
        return None

    # A keyed address blocks the export, as a live tile layer's does.
    url, dependency = _live_dependency(url, name, "file address")
    raster = RasterSpec(
        path=url,
        src=url,
        remote=True,
        band_count=int(provider.bandCount()),
        source_crs=crs.authid(),
        extent=raster_extent(layer),
        pixel_width=int(provider.xSize()),
        pixel_height=int(provider.ySize()),
        resolution_x=float(layer.rasterUnitsPerPixelX()),
        resolution_y=float(layer.rasterUnitsPerPixelY()),
        nodata=raster_nodata(layer),
        bands=bands,
    )
    report.approximated(
        subject,
        f"Exported as a live Cloud Optimized GeoTIFF: the web map reads the "
        f"parts of the file it draws from {host}, and nothing is carried inside "
        "the map. The server must allow web pages to read the file (CORS) and "
        "to ask for parts of it (Range requests), or this layer stays empty "
        "while the rest of the map draws; a refused request shows in the "
        "browser's developer tools.",
        layer_id,
    )
    _report_composite_colours(report, raster, name, layer_id)
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
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


def point_names(
    labeling: LabelingSpec,
    field_name: str,
    character_set: str | None,
    formatted_property: str | None = None,
) -> LabelingSpec:
    """`labeling` showing `field_name`, always, with a halo.

    Built on the layer's own labelling when it has one, so its font and size
    carry over; a halo is added only where QGIS set none. `character_set` is
    the names field's own (`collect_character_set`): the labelling's is its
    label field's, and the atlas it builds *replaces* the runtime's, so a name
    with a glyph only the names field uses would draw blank.

    With the labelling's Formatted numbers on, `formatted_property` names the
    property the names are worked out into at export, as a formatted label's
    field is (`translate_labeling`); the page collects their characters then,
    so there is no `character_set`.
    """
    if labeling.number_format is not None and formatted_property is not None:
        return dataclasses.replace(
            labeling,
            enabled=True,
            field_name=formatted_property,
            expression=QgsExpression.quotedColumnRef(field_name),
            character_set=None,
            halo_color=labeling.halo_color or NAME_HALO,
            halo_width=labeling.halo_width or 1.0,
        )
    return dataclasses.replace(
        labeling,
        enabled=True,
        field_name=field_name,
        expression=None,
        number_format=None,
        character_set=character_set,
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
    # The dialog's "Filter by", worked out in `finish` from the data itself.
    filter_request: FilterRequest | None = None

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

        layer_filter = None
        if self.filter_request is not None:
            outcome = build_filter(geojson, self.filter_request)
            geojson, layer_filter = outcome.geojson, outcome.spec
            _report_filter(base, popup, self.filter_request, outcome, report)

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
            filter=layer_filter,
        )


def filter_kind(field: Any) -> FilterKind | None:
    """What a filter by this `QgsField` would be, or `None` for a type no
    filter panel reads: a yes/no, a time of day, a list, a blob."""
    kind = field.type()
    if kind == QMetaType.Type.Bool:
        return None
    if field.isNumeric():
        return FilterKind.NUMBER
    if kind == QMetaType.Type.QDate:
        return FilterKind.DATE
    if kind == QMetaType.Type.QDateTime:
        return FilterKind.DATETIME
    if kind == QMetaType.Type.QString:
        return FilterKind.TEXT
    return None


def _filter_request(
    layer: QgsVectorLayer, field_name: str, report: FidelityReportBuilder
) -> FilterRequest | None:
    """The dialog's "Filter by" for `layer`, or `None` after saying why not."""
    index = layer.fields().lookupField(field_name)
    field = layer.fields().at(index) if index >= 0 else None
    kind = filter_kind(field) if field is not None else None
    if field is None or kind is None:
        report.unsupported(
            f"Filter on '{layer.name()}'",
            f"The layer is set to be filtered by '{field_name}', which "
            + (
                "it no longer has"
                if field is None
                else "is not a number, a date or text"
            )
            + ", so the map has no filter for it. Choose another field under "
            "This layer only on the Layers tab.",
            layer.id(),
        )
        return None
    return FilterRequest(field.name(), field.displayName() or field.name(), kind)


def _report_filter(
    base: ExportLayer,
    popup: PopupSpec,
    request: FilterRequest,
    outcome: FilterOutcome,
    report: FidelityReportBuilder,
) -> None:
    """The Fidelity rows for a layer's filter panel, or for its absence."""
    subject = f"Filter on '{base.name}'"
    spec = outcome.spec
    if spec is None:
        report.unsupported(subject, outcome.refusal or "", base.layer_id)
        return
    how = (
        f"a tick box for each of its {len(spec.categories)} values"
        if spec.kind is FilterKind.TEXT
        else "a slider over the range of its values"
    )
    shown = {field.name for field in popup.visible_fields} if popup.enabled else set()
    carried = (
        ""
        if request.field_name in shown
        else " Its values travel in the map's data, though its popup row is hidden."
    )
    report.preserved(
        subject,
        f"A panel in the map's top-left corner filters the layer by "
        f"'{spec.title}', with {how}.{carried}",
        base.layer_id,
    )
    if outcome.missing:
        report.approximated(
            subject,
            f"{outcome.missing:,} feature{' has' if outcome.missing == 1 else 's have'}"
            f" no '{spec.title}', and the filter hides "
            f"{'it' if outcome.missing == 1 else 'them'}: it keeps a feature by "
            "its value.",
            base.layer_id,
        )
    if base.labeling.enabled:
        report.approximated(
            subject,
            "The filter does not reach the layer's labels: a label stays on "
            "the map when its feature is filtered out.",
            base.layer_id,
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
    map_time: MapTime | None = None,
    filter_field: str | None = None,
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
        map_time=map_time,
        filter_field=filter_field,
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
    map_time: MapTime | None = None,
    filter_field: str | None = None,
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

    `map_time` is the time the QGIS map view draws, for a WMS-T layer that
    follows it; `None` when there is no view (see `MapTime`).

    `filter_field` is the dialog's "Filter by": the field the web map's filter
    panel filters this vector layer by, kept in the data whatever the popup
    shows. `None` is no filter.
    """
    layer_id = layer.id()
    name = layer.name()

    if layer.type() == QgsMapLayerType.RasterLayer:
        # Rasters take none of the vector arguments above: they have no fields
        # to hide, no popup to build, no labels and no geometry to quantise.
        # Passing them along would only make the seam look wider than it is.
        return read_raster(
            layer, report, group_path=group_path, visible=visible, map_time=map_time
        )

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
        # With Formatted numbers on, the names are worked out at export, and
        # the page collects their characters then.
        formatted = labeling.number_format is not None
        if formatted:
            character_set = None
        elif scans is None:
            character_set = collect_character_set(
                layer, name_field, labeling.capitalization
            )
        else:
            character_set = scans.character_set(
                layer, name_field, labeling.capitalization, collect_character_set
            )
        labeling = point_names(
            labeling,
            name_field,
            character_set,
            expression_label_property(layer) if formatted else None,
        )
        numbers = (
            " Names that read as numbers are formatted as the layer's labels are."
            if formatted
            else ""
        )
        report.preserved(
            f"Names on '{name}'",
            f"Each point's '{name_field}' shows beside it with a halo, without "
            "a click. Like labels, the names appear from the zoom where most of "
            f"them have room.{numbers}",
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

    filter_request = (
        _filter_request(layer, filter_field, report) if filter_field else None
    )
    # The filter reads its field by name, like everything that draws.
    drawing = set(drawing_fields(renderer, labeling, elevation))
    if filter_request is not None:
        drawing.add(filter_request.field_name)

    # The primary-key check reads the live layer, so it is made here; the strip
    # it guards happens in `PendingLayer.finish`.
    keep = set(drawing)
    if popup.enabled:
        keep |= {field.name for field in popup.visible_fields}
    keep |= set(display_names)
    renumber_ids = primary_key_is_excluded(layer, keep)
    # The rules are matched as the features are read, from the renderer as it
    # is now - the one just translated.
    rules = (
        RuleMatcher(layer, renderer.field_name)
        if renderer.field_generated and renderer.field_name
        else None
    )
    features = snapshot_features(
        layer,
        clip_extent,
        display_names,
        keep,
        scans,
        label=label_expression(layer, labeling),
        rules=rules,
    )

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
        drawing=frozenset(drawing),
        renumber_ids=renumber_ids,
        filter_request=filter_request,
    )
