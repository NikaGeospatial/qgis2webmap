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

#: What `raster_staging` injects in place of this module's `bake`. Source path,
#: QML document, destination path; returns the path actually written.
BakeCallable = Any


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
            QgsCoordinateTransformContext,
            QgsRasterFileWriter,
            QgsRasterLayer,
            QgsRasterPipe,
        )
        from qgis.PyQt.QtXml import QDomDocument
    except ImportError as exc:  # pragma: no cover - QGIS is present in the plugin
        raise StyleBakeError(
            "QGIS itself is needed to render a raster's colours and could not "
            "be loaded, so the layer is drawn from its values instead of its "
            "styling."
        ) from exc
    return SimpleNamespace(
        RasterLayer=QgsRasterLayer,
        RasterPipe=QgsRasterPipe,
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


def bake(source: str, style_qml: str, destination: str) -> str:
    """Render `source` under `style_qml` into an RGBA GeoTIFF at `destination`.

    The result is a plain GeoTIFF, not a COG: `to_cog` runs next and is the one
    place that knows about tiling, overviews and compression. Writing two
    half-converted things would be two places to keep in step.

    The output is in the SOURCE's CRS. Reprojection to Web Mercator is
    `to_cog`'s job and stays there, so this function changes exactly one thing
    about the raster - what colour its pixels are.
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

    renderer = layer.renderer()
    if renderer is None:
        raise StyleBakeError(
            "The layer has no renderer to draw with, so it is drawn from its "
            "values instead of its styling."
        )
    renderer = renderer.clone()
    # Layer opacity rides on `<om-layer opacity>`; baking it here as well would
    # apply it twice.
    renderer.setOpacity(1.0)

    provider = layer.dataProvider()
    pipe = qgis.RasterPipe()
    if not pipe.set(provider.clone()) or not pipe.set(renderer):
        raise StyleBakeError(
            "QGIS declined to assemble a render pipeline for this raster, so "
            "it is drawn from its values instead of its styling."
        )

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
