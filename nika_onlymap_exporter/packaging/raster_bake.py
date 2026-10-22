"""Rendering a QGIS raster style into the pixels, before the COG conversion.

**Why this exists.** `COGLayer` can draw a raster's bands; it cannot reproduce
a QGIS colour ramp. The ramp is an arbitrary list of stops, the runtime takes a
`colormap` from a fixed vocabulary of fourteen names, and QGIS does not even
record which named ramp the author picked - the whole argument is in
`core/raster_style.py`. So the colours are rendered here, by QGIS, into an RGBA
GeoTIFF that `raster_cog.to_cog` then converts exactly as it converts any other
source. Downstream knows nothing about this: it receives a file path.

**The style arrives as QML, not as a layer.** `RasterSpec` is plain data and
stays that way, and the QML round-trip is exact - a layer rebuilt from its own
`exportNamedStyle` renders byte-identically to the original, verified on both
renderer classes in the Grand Canyon demo. It also puts the cost in the right
place: rendering happens during packaging, after the user has committed to the
export, rather than in the reader, which also runs for the dialog's preview.

**Opacity is not baked.** The renderer's opacity is reset to 1 before rendering
because layer opacity is carried as an `<om-layer opacity>` attribute. Leaving
it in would apply it twice - a layer at 0.6 would arrive at 0.36.

**Import is soft**, the same shape `raster_cog` uses for GDAL and
`runtime_manager` uses for the runtime it cannot find: a named exception with a
message a user can act on, never an ImportError traceback out of a background
thread. That keeps this module importable by the unit tier, which has no QGIS.

**Failure does not lose the layer.** `bake` raises; `raster_staging` catches and
falls back to staging the unstyled source, recording the loss in the fidelity
report. A map that draws the data in grey is a worse map; a map missing a layer
is a broken one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..core.export_ir import NoDataRule
from ..core.raster_style import hue_saturation_active

# Pixels rendered per strip when the map renderer, not the file writer, makes
# the copy. A strip is RGBA, so this bounds the working memory at about 64 MB
# however large the raster is.
_STRIP_PIXELS = 16 * 1024 * 1024


class StyleBakeError(RuntimeError):
    """The style could not be rendered into the pixels.

    Carries a message written to be shown to a user, because it reaches the
    fidelity report unmodified.
    """


def _qgis() -> Any:
    """PyQGIS's raster-writing classes, or an explanation of their absence.

    Returned as one namespace rather than unpacked at the call site so the
    class names stay class-shaped; a tuple assignment would make each one a
    lowercase local or a lint suppression.
    """
    try:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransformContext,
            QgsMapRendererCustomPainterJob,
            QgsMapSettings,
            QgsRasterFileWriter,
            QgsRasterLayer,
            QgsRasterRange,
            QgsRectangle,
        )
        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QColor, QImage, QPainter
        from qgis.PyQt.QtXml import QDomDocument
    except ImportError as exc:  # pragma: no cover - QGIS is present in the plugin
        raise StyleBakeError(
            "QGIS itself is needed to render a raster's colours and could not "
            "be loaded, so the layer is drawn from its values instead of its "
            "styling."
        ) from exc
    return SimpleNamespace(
        Color=QColor,
        CoordinateReferenceSystem=QgsCoordinateReferenceSystem,
        Image=QImage,
        MapRendererJob=QgsMapRendererCustomPainterJob,
        MapSettings=QgsMapSettings,
        Painter=QPainter,
        RasterLayer=QgsRasterLayer,
        RasterRange=QgsRasterRange,
        Rectangle=QgsRectangle,
        Size=QSize,
        RasterFileWriter=QgsRasterFileWriter,
        TransformContext=QgsCoordinateTransformContext,
        Document=QDomDocument,
    )


def _parsed(result: Any) -> bool:
    """Whether `QDomDocument.setContent` succeeded, on either Qt.

    Qt5 returns a bool. Qt6 returns a `ParseResult` named tuple - and a failed
    parse is `(False, message, line, column)`, which is a NON-EMPTY tuple and
    therefore truthy. So `if not document.setContent(...)` is not merely
    imprecise on Qt6, it can never fire: a malformed style would sail past the
    guard and reach `importNamedStyle` as an empty document, which applies
    cleanly and renders the raster with no styling at all. Caught by the QGIS
    test tier on 2026-09-18, which is the only tier that runs a real QDomDocument.
    """
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _render_strips(qgis: Any, layer: Any, destination: str) -> None:
    """Paint `layer` with QGIS's own map renderer, one strip at a time, into an
    RGBA GeoTIFF at the raster's native size.

    Used instead of `QgsRasterFileWriter` for a layer with a hue/saturation
    filter, which crashes that writer. The renderer is the reference the
    exported picture is held to, so nothing here is a model of it.
    """
    try:
        from osgeo import gdal
    except ImportError as exc:  # pragma: no cover - GDAL ships with QGIS
        raise StyleBakeError(
            "GDAL's Python bindings are needed to render this raster's colours "
            "and could not be loaded, so the layer is drawn from its values "
            "instead of its styling."
        ) from exc

    provider = layer.dataProvider()
    width, height = int(provider.xSize()), int(provider.ySize())
    extent = provider.extent()
    pixel_width = extent.width() / width
    pixel_height = extent.height() / height
    if abs(pixel_width - pixel_height) > 1e-6 * pixel_width:
        raise StyleBakeError(
            "This raster's pixels are not square, which the renderer cannot "
            "reproduce one-to-one with its hue and saturation settings, so the "
            "layer is drawn from its values instead of its styling."
        )

    dataset = gdal.GetDriverByName("GTiff").Create(
        destination, width, height, 4, gdal.GDT_Byte, ["BIGTIFF=IF_SAFER"]
    )
    if dataset is None:
        raise StyleBakeError(
            "The styled copy of this raster could not be created, so it is "
            "drawn from its values instead of its styling."
        )
    dataset.SetGeoTransform(
        [extent.xMinimum(), pixel_width, 0.0, extent.yMaximum(), 0.0, -pixel_height]
    )
    dataset.SetProjection(layer.crs().toWkt())
    for band, interpretation in enumerate(
        (
            gdal.GCI_RedBand,
            gdal.GCI_GreenBand,
            gdal.GCI_BlueBand,
            gdal.GCI_AlphaBand,
        ),
        start=1,
    ):
        dataset.GetRasterBand(band).SetColorInterpretation(interpretation)

    rows = max(1, _STRIP_PIXELS // width)
    for top in range(0, height, rows):
        count = min(rows, height - top)
        settings = qgis.MapSettings()
        settings.setLayers([layer])
        settings.setDestinationCrs(layer.crs())
        settings.setExtent(
            qgis.Rectangle(
                extent.xMinimum(),
                extent.yMaximum() - (top + count) * pixel_height,
                extent.xMaximum(),
                extent.yMaximum() - top * pixel_height,
            )
        )
        settings.setOutputSize(qgis.Size(width, count))
        settings.setBackgroundColor(qgis.Color(0, 0, 0, 0))
        image = qgis.Image(
            qgis.Size(width, count), qgis.Image.Format.Format_ARGB32_Premultiplied
        )
        image.fill(0)
        painter = qgis.Painter(image)
        qgis.MapRendererJob(settings, painter).renderSynchronously()
        painter.end()
        rgba = image.convertToFormat(qgis.Image.Format.Format_RGBA8888)
        pixels = rgba.constBits()
        pixels.setsize(rgba.sizeInBytes())
        dataset.WriteRaster(
            0,
            top,
            width,
            count,
            bytes(pixels),
            band_list=[1, 2, 3, 4],
            buf_pixel_space=4,
            buf_line_space=4 * width,
            buf_band_space=1,
        )
    dataset.FlushCache()
    dataset = None


def bake(
    source: str,
    style_qml: str,
    destination: str,
    assigned_crs_wkt: str | None = None,
    nodata_rules: tuple[NoDataRule, ...] = (),
) -> str:
    """Render `source` under `style_qml` into an RGBA GeoTIFF at `destination`.

    The result is a plain GeoTIFF, not a COG: `to_cog` runs next and is the one
    place that knows about tiling, overviews and compression. Writing two
    half-converted things would be two places to keep in step.

    The output is in the layer's CRS: the file's own, or `assigned_crs_wkt` when
    the author assigned one in QGIS that the file lacks. Reprojection to Web
    Mercator is `to_cog`'s job and stays there, so this function changes exactly
    one thing about the raster - what colour its pixels are.

    `nodata_rules` restate the no-data masking the author set on the layer,
    which is not part of its style and so is not in `style_qml`.

    What is rendered is the layer's whole pipe, not just its renderer: the QML
    carries the brightness/contrast, hue/saturation and resampling filters too,
    and drawing without them was a visibly different picture.
    """
    qgis = _qgis()

    layer = qgis.RasterLayer(source, Path(source).stem)
    if not layer.isValid():
        raise StyleBakeError(
            f"'{source}' could not be reopened to render its colours, so the "
            "layer is drawn from its values instead of its styling."
        )

    document = qgis.Document()
    if not _parsed(document.setContent(style_qml)):
        raise StyleBakeError(
            "The layer's saved styling could not be read back, so the layer is "
            "drawn from its values instead of its styling."
        )
    applied, message = layer.importNamedStyle(document)
    if not applied:
        raise StyleBakeError(
            "The layer's styling could not be applied to a fresh copy of the "
            f"raster, so it is drawn from its values instead. QGIS said: "
            f"{message or 'no reason given'}."
        )

    if assigned_crs_wkt:
        crs = qgis.CoordinateReferenceSystem.fromWkt(assigned_crs_wkt)
        if not crs.isValid():
            raise StyleBakeError(
                "The coordinate reference system assigned to the layer could "
                "not be read back, so it is drawn from its values instead of "
                "its styling."
            )
        layer.setCrs(crs)

    for rule in nodata_rules:
        layer.dataProvider().setUseSourceNoDataValue(rule.band, rule.use_source)
        layer.dataProvider().setUserNoDataValue(
            rule.band, [qgis.RasterRange(low, high) for low, high in rule.ranges]
        )

    if layer.renderer() is None:
        raise StyleBakeError(
            "The layer has no renderer to draw with, so it is drawn from its "
            "values instead of its styling."
        )
    provider = layer.dataProvider()
    # The layer's own pipe, not one assembled from its provider and renderer:
    # the brightness/contrast, hue/saturation, resampling and no-data stages
    # all shape what QGIS draws. The layer is this function's private copy, so
    # changing its renderer's opacity touches nothing of the user's.
    pipe = layer.pipe()
    # Layer opacity rides on `<om-layer opacity>`; baking it here as well would
    # apply it twice.
    layer.renderer().setOpacity(1.0)

    error = 0
    if hue_saturation_active(layer.hueSaturationFilter()):
        _render_strips(qgis, layer, destination)
    else:
        writer = qgis.RasterFileWriter(destination)
        writer.setOutputFormat("GTiff")
        error = writer.writeRaster(
            pipe,
            provider.xSize(),
            provider.ySize(),
            provider.extent(),
            layer.crs(),
            qgis.TransformContext(),
        )
    # QgsRasterFileWriter.NoError is 0. Compared numerically rather than by the
    # enum so this module needs no QGIS import at module scope.
    if int(error) != 0:
        raise StyleBakeError(
            f"QGIS could not write the styled copy of this raster (code "
            f"{int(error)}), so it is drawn from its values instead of its "
            "styling."
        )
    if not Path(destination).is_file():
        raise StyleBakeError(
            "QGIS reported success but wrote no styled copy of this raster, so "
            "it is drawn from its values instead of its styling."
        )
    return destination
