"""One QGIS vector layer to one `ExportLayer`.

Imports PyQGIS; exercised in `tests/qgis/`.

Normalises everything to **WGS84 GeoJSON**, per issue #29's 0.1.0 scope. That one
decision removes a whole class of downstream work: the writer never learns what a
Shapefile is, and reprojection happens exactly once, here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsJsonExporter,
    QgsMapLayerType,
    QgsWkbTypes,
)

from .elevation_translator import translate_elevation
from .export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ExportLayer,
    GeometryKind,
    LabelingSpec,
    PopupSpec,
    RendererSpec,
    ScaleRange,
    SourceKind,
)
from .fidelity_report import FidelityReportBuilder
from .labeling_translator import translate_labeling
from .popup_translator import translate_popup
from .renderer_translator import translate_renderer
from .symbol_rasterizer import build_icon_atlas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from qgis.core import QgsProject, QgsVectorLayer

WGS84 = "EPSG:4326"

# Full double precision is meaningless for a web map and inflates the artifact
# for nothing. 9 decimal places is roughly 0.1 mm at the equator - far beyond any
# survey requirement, so this is not the lossy quantisation that stays opt-in.
GEOJSON_PRECISION = 9

_GEOMETRY_BY_WKB = {
    QgsWkbTypes.PointGeometry: GeometryKind.POINT,
    QgsWkbTypes.LineGeometry: GeometryKind.LINE,
    QgsWkbTypes.PolygonGeometry: GeometryKind.POLYGON,
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


def clip_request(layer: QgsVectorLayer, extent: Any) -> Any:
    """A feature request limited to `extent`, in the layer's own CRS.

    The extent arrives in WGS84 because that is what the rest of the model
    speaks, but a filter rect is compared against the layer's *source*
    coordinates - so it has to be transformed back, not forward. Getting this
    the wrong way round silently returns nothing, which looks exactly like an
    empty layer.
    """
    from qgis.core import (
        QgsCoordinateTransform,
        QgsFeatureRequest,
        QgsProject,
        QgsRectangle,
    )

    rect = QgsRectangle(extent.west, extent.south, extent.east, extent.north)
    target = layer.crs()
    if target.isValid() and target.authid() != WGS84:
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(WGS84), target, QgsProject.instance()
        )
        rect = transform.transformBoundingBox(rect)

    return QgsFeatureRequest().setFilterRect(rect)


def export_geojson(
    layer: QgsVectorLayer,
    report: FidelityReportBuilder,
    precision: int = GEOJSON_PRECISION,
    clip_extent: Any | None = None,
) -> dict[str, Any] | None:
    """Read every feature as WGS84 GeoJSON, or only those within `clip_extent`.

    `QgsJsonExporter` handles reprojection through `setDestinationCrs`, so this
    is the single place a CRS transform happens in the whole plugin.

    `clip_extent` is how a half-million-point layer becomes an exportable one:
    the features outside it are never read, so the cost is paid by the provider's
    spatial index rather than by us filtering afterwards.
    """
    layer_id = layer.id()
    subject = f"Data of '{layer.name()}'"

    exporter = QgsJsonExporter(layer)
    exporter.setDestinationCrs(QgsCoordinateReferenceSystem(WGS84))
    exporter.setTransformGeometries(True)
    exporter.setIncludeGeometry(True)
    exporter.setIncludeAttributes(True)
    exporter.setPrecision(precision)

    try:
        if clip_extent is None:
            features = list(layer.getFeatures())
        else:
            total = layer.featureCount()
            features = list(layer.getFeatures(clip_request(layer, clip_extent)))
            _report_clip(report, subject, layer_id, len(features), total)
        text = exporter.exportFeatures(features)
        collection = json.loads(text)
    except (OSError, ValueError, RuntimeError) as exc:
        report.blocked(
            subject,
            f"The layer's features could not be read: {exc}. Check that the data "
            "source is reachable and not locked by another program.",
            layer_id,
        )
        return None

    source_crs = layer.crs()
    if source_crs.isValid() and source_crs.authid() != WGS84:
        report.preserved(
            subject,
            f"Reprojected from {source_crs.authid()} to {WGS84} for the web map.",
            layer_id,
        )

    return collection


def drop_hidden_features(
    collection: dict[str, Any],
    renderer: RendererSpec,
) -> dict[str, Any]:
    """Remove features belonging to categories switched off in QGIS.

    The comparison is on the string form of the value: a category value arrives
    from QGIS as a Python scalar while the GeoJSON property may have been
    serialised as a number or a string, and `'1' != 1` would silently keep the
    features the author hid.
    """
    if not renderer.hidden_values or not renderer.field_name:
        return collection

    hidden = {"" if v is None else str(v) for v in renderer.hidden_values}
    field = renderer.field_name
    kept = [
        feature
        for feature in collection.get("features") or ()
        if str((feature.get("properties") or {}).get(field, "")) not in hidden
    ]
    return {**collection, "features": kept}


def read_layer(
    layer: QgsVectorLayer,
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
) -> ExportLayer | None:
    """Read one vector layer into the normalized model.

    Returns `None` for layers 0.1.0 cannot handle at all - rasters, and vector
    layers with no geometry - after recording why.

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

    if layer.type() != QgsMapLayerType.VectorLayer:
        report.unsupported(
            f"Layer '{name}'",
            "Raster layers are not exported in 0.1.0. The layer is omitted from "
            "the map.",
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

    geojson = export_geojson(
        layer,
        report,
        precision=GEOJSON_PRECISION if precision is None else precision,
        clip_extent=clip_extent,
    )
    if geojson is None:
        return None

    # Before counting: a switched-off category means the author does not want
    # those features on the map at all, so they must leave the data, not just
    # the style. The count has to reflect what actually ships.
    renderer = translate_renderer(layer, report)
    geojson = drop_hidden_features(geojson, renderer)

    # After the renderer, because the atlas rasterises the very symbols the
    # renderer just described, and re-emits it with each class naming its icon.
    # Layers that draw with plain circles - almost all of them - get `None` back
    # and the renderer they came in with, untouched.
    icon_atlas, renderer = build_icon_atlas(
        layer.renderer(), renderer, kind, report, name, layer_id
    )

    feature_count = len(geojson.get("features") or ())
    if feature_count == 0:
        report.unsupported(
            f"Layer '{name}'",
            "The layer contains no features. It is exported but will show "
            "nothing - check for an active filter or an empty data source.",
            layer_id,
        )

    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=kind,
        source_kind=source_kind(layer),
        visible=visible,
        opacity=float(layer.opacity()),
        scale_range=scale_range(layer),
        renderer=renderer,
        icon_atlas=icon_atlas,
        labeling=(translate_labeling(layer, report) if with_labels else LabelingSpec()),
        elevation=translate_elevation(layer, report, kind, project),
        # Explicitly disabled, not a default `PopupSpec()`: that one is enabled.
        popup=translate_popup(
            layer, report, field_modes=field_modes, on_hover=popup_on_hover
        )
        if with_popup
        else PopupSpec(enabled=False),
        highlight_color=highlight_color,
        attribution=read_attribution(layer),
        feature_count=feature_count,
        geojson=geojson,
        group_path=group_path,
        dependencies=(describe_source(layer),),
    )
