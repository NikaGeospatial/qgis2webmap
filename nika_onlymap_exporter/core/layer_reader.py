"""One QGIS layer - vector or raster - to one `ExportLayer`.

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
    ElevationSpec,
    ExportLayer,
    Extent,
    GeometryKind,
    LabelingSpec,
    PopupSpec,
    RasterSpec,
    RendererKind,
    RendererSpec,
    ScaleRange,
    SourceKind,
)
from .fidelity_report import FidelityReportBuilder
from .labeling_translator import translate_labeling
from .popup_translator import rename_untemplatable_fields, translate_popup
from .renderer_translator import translate_renderer
from .symbol_rasterizer import build_icon_atlas

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from qgis.core import (
        QgsMapLayer,
        QgsProject,
        QgsRasterLayer,
        QgsVectorLayer,
    )

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

    What is deliberately *not* translated, and is reported instead:

    * **Colour ramps.** A QGIS singleband-pseudocolour ramp is an arbitrary
      list of stops; `COGLayer` takes a `colormap` whose accepted names the
      runtime's schema does not enumerate. Guessing one would either be
      ignored or draw the raster in colours the author never chose.
    * **Band selection and order.** `COGLayer` has no band attribute at all,
      so a three-band file draws with the bands the file itself declares.
    * **Resampling, brightness/contrast, hillshade and blend mode.** No
      attribute in the schema corresponds to any of them.
    """
    layer_id = layer.id()
    name = layer.name()
    provider = layer.dataProvider()
    provider_key = (layer.providerType() or "").lower()

    if provider_key not in _RASTER_FILE_PROVIDERS or provider is None:
        report.unsupported(
            f"Layer '{name}'",
            "This is a live raster service rather than a file on disk, so there "
            "is nothing to embed in a self-contained map. Save it to a GeoTIFF "
            "and add that instead, or use it as a basemap. The layer is omitted "
            "from the map.",
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
    extent = raster_extent(layer)
    crs = layer.crs()

    raster = RasterSpec(
        path=path,
        band_count=band_count,
        source_crs=crs.authid() if crs.isValid() else None,
        extent=extent,
        pixel_width=int(provider.xSize()),
        pixel_height=int(provider.ySize()),
        resolution_x=float(layer.rasterUnitsPerPixelX()),
        resolution_y=float(layer.rasterUnitsPerPixelY()),
        nodata=raster_nodata(layer),
        rescale_min=rescale_min,
        rescale_max=rescale_max,
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

    if band_count > 1:
        report.unsupported(
            f"Band rendering of '{name}'",
            f"The layer has {band_count} bands. The map draws the bands the "
            "file itself declares - the band numbers, order and per-band "
            "contrast stretch you set in QGIS are not carried, because the map "
            "library has no way to express them. Save the composite you want "
            "as its own GeoTIFF if the arrangement matters.",
            layer_id,
        )
    elif not raster.has_rescale:
        report.approximated(
            f"Contrast of '{name}'",
            "No minimum/maximum stretch could be read from this layer's "
            "renderer, so the map stretches the raster across its own range. "
            "The image may look lighter or darker than it does in QGIS. Set an "
            "explicit min/max in the layer's Symbology tab to pin it.",
            layer_id,
        )

    # Colour is the single biggest raster loss and it applies to every renderer
    # QGIS offers except a plain grey one, so it is stated unconditionally
    # rather than guessed at per renderer.
    renderer_name = type(layer.renderer()).__name__ if layer.renderer() else "unknown"
    if renderer_name not in ("QgsSingleBandGrayRenderer", "QgsMultiBandColorRenderer"):
        report.unsupported(
            f"Colours of '{name}'",
            "The colour ramp or palette you applied to this raster is not "
            "carried into the map: the map library takes a fixed set of named "
            "colour maps rather than arbitrary stops, and picking one for you "
            "would show the data in colours you did not choose. The raster "
            "draws in greyscale. Export a styled RGB GeoTIFF from QGIS "
            "(Raster > Conversion > Translate) if the colours matter.",
            layer_id,
        )

    if layer.hasScaleBasedVisibility():
        report.suppressed_setting(
            f"Scale visibility of '{name}'",
            "This layer is set to show only between two scales. Web maps built "
            "by this plugin show it at every zoom instead - the map runtime "
            "this export is pinned to has no per-layer zoom range. Remove the "
            "scale range if showing it throughout is wrong.",
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
) -> ExportLayer | None:
    """Read one layer into the normalized model.

    Raster layers are handed straight to `read_raster`; everything below is the
    vector path. Returns `None` for layers with no web equivalent at all - a
    mesh or point cloud, a vector layer with no geometry, a raster that is a
    live service rather than a file - after recording why.

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

    labeling = translate_labeling(layer, report) if with_labels else LabelingSpec()
    elevation = translate_elevation(layer, report, kind, project)
    # Explicitly disabled, not a default `PopupSpec()`: that one is enabled.
    popup = (
        translate_popup(layer, report, field_modes=field_modes, on_hover=popup_on_hover)
        if with_popup
        else PopupSpec(enabled=False)
    )

    # Only the attributes something still reads survive: what the popup shows,
    # plus what the map draws with. A value the user hid must leave the file,
    # not merely the popup - see `keep_only_fields`.
    keep = drawing_fields(renderer, labeling, elevation)
    if popup.enabled:
        keep |= {field.name for field in popup.visible_fields}
    geojson = keep_only_fields(geojson, keep)

    # After the strip so only surviving fields are considered; drawing fields
    # are protected because accessors reference them by their real name.
    geojson, popup = rename_untemplatable_fields(
        geojson,
        popup,
        drawing_fields(renderer, labeling, elevation),
        report,
        f"Popup fields of '{name}'",
        layer_id,
    )

    if layer.hasScaleBasedVisibility():
        report.suppressed_setting(
            f"Scale visibility of '{name}'",
            "This layer is set to show only between two scales. Web maps built "
            "by this plugin show it at every zoom instead - the map library has "
            "no per-layer zoom range for vector layers. Split the layer or "
            "remove the scale range if showing it throughout is wrong.",
            layer_id,
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
        labeling=labeling,
        elevation=elevation,
        popup=popup,
        highlight_color=highlight_color,
        attribution=read_attribution(layer),
        feature_count=feature_count,
        geojson=geojson,
        group_path=group_path,
        dependencies=(describe_source(layer),),
    )
