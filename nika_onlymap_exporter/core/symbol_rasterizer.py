"""Drawing a layer's markers with QGIS, into one sprite sheet.

Imports PyQGIS and Qt, so it is exercised in `tests/qgis/` rather than CI's unit
tier. The half of this feature that can be tested without QGIS - which layers
need a sheet, and where each icon sits in it - lives in `symbol_atlas`.

**Render, do not translate.** The tempting implementation reads an SVG's
`param(fill)` / `param(outline)` values out of the file and reproduces them in
the browser. That is a re-implementation of QGIS's renderer, and it would have to
grow to cover stacked symbol layers, the ~40 simple-marker shapes, size units and
opacity - each of which is another chance to be subtly wrong. Calling
`QgsSymbol.asImage()` instead makes the pixels correct by construction: whatever
QGIS draws in its own layer panel is what lands in the sheet.

**Measured, not calculated.** The extent a marker actually covers is found by
looking at the rendered alpha rather than derived from `size()` and the symbol's
size unit. A marker's size may be in millimetres, points, pixels or map units,
its stroke extends past its nominal size, and a stack of symbol layers can be
larger than any one of them. Measuring is one rule that covers all of it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize, Qt
from qgis.PyQt.QtGui import QImage, QPainter

from .export_ir import (
    CategorySpec,
    GeometryKind,
    GraduatedClassSpec,
    IconAtlasSpec,
    RendererKind,
    RendererSpec,
)
from .renderer_translator import class_symbols
from .symbol_atlas import (
    SUPERSAMPLE,
    appearance_key,
    capacity_note,
    icon_name,
    layer_symbols,
    needs_atlas,
    over_capacity,
    plan_atlas,
    texture_note,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsFeatureRenderer, QgsSymbol

    from .fidelity_report import FidelityReportBuilder

# The first box a marker is rendered into, in supersampled pixels. Large enough
# that an ordinary 2-6 mm marker never touches the edge, so the common case
# renders exactly once.
INITIAL_BOX_PIXELS = 128

# Doubling stops here. A marker needing more than this is either in map units
# (metres, so its pixel size is meaningless without a scale) or a mistake, and
# either way the layer falls back rather than producing a sheet no GPU accepts.
MAX_BOX_PIXELS = 1024

# A legend swatch's height in CSS pixels. It is rendered at twice that so the
# swatch stays sharp on a high-DPI screen, which is where legends are read.
LEGEND_SWATCH_PIXELS = 16
LEGEND_SWATCH_SUPERSAMPLE = 2


def _alpha_bounds(image: QImage) -> tuple[int, int, int, int] | None:
    """The bounding box of every pixel that is not fully transparent.

    Returns `None` for an entirely blank image, which means the symbol drew
    nothing - an empty SVG path, a marker with zero size, a symbol whose only
    layer is disabled. The caller falls back rather than packing an invisible
    cell that would make the features disappear.
    """
    converted = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = converted.width(), converted.height()

    min_x, min_y = width, height
    max_x = max_y = -1
    for y in range(height):
        row_has_ink = False
        for x in range(width):
            if converted.pixelColor(x, y).alpha() == 0:
                continue
            row_has_ink = True
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
        if row_has_ink:
            if y < min_y:
                min_y = y
            max_y = y

    if max_x < 0:
        return None
    return min_x, min_y, max_x, max_y


def _touches_edge(bounds: tuple[int, int, int, int], box: int) -> bool:
    """Whether the drawing ran into the walls, meaning it was clipped."""
    min_x, min_y, max_x, max_y = bounds
    return min_x == 0 or min_y == 0 or max_x == box - 1 or max_y == box - 1


def render_symbol(symbol: QgsSymbol, supersample: int = SUPERSAMPLE) -> QImage | None:
    """One symbol, drawn at `supersample` times its size and cropped square.

    The crop is square and centred on the box's centre rather than tight around
    the ink, because QGIS centres a marker on its feature: a tight crop of an
    asymmetric marker - a pin, an arrow, anything with an offset symbol layer -
    would move its anchor and slide every feature on the map.

    Returns `None` when the symbol draws nothing at all, or when it is still
    clipped at `MAX_BOX_PIXELS`.
    """
    scaled = symbol.clone()
    size = getattr(scaled, "size", None)
    set_size = getattr(scaled, "setSize", None)
    if size is None or set_size is None:
        return None
    # `setSize` on a marker symbol scales its symbol layers proportionally, in
    # whatever size unit each of them uses, so this stays correct for a stack.
    set_size(float(size()) * supersample)

    box = INITIAL_BOX_PIXELS
    while box <= MAX_BOX_PIXELS:
        image = scaled.asImage(QSize(box, box))
        bounds = _alpha_bounds(image)
        if bounds is None:
            return None
        if not _touches_edge(bounds, box):
            min_x, min_y, max_x, max_y = bounds
            centre = box / 2.0
            # The half-extent that contains the ink on every side of the centre.
            # One pixel of slack so anti-aliased edges are not shaved off.
            half = max(
                centre - min_x, centre - min_y, max_x + 1 - centre, max_y + 1 - centre
            )
            side = max(2, int(2 * half) + 1)
            left = int(centre - side / 2.0)
            top = int(centre - side / 2.0)
            return image.copy(QRect(left, top, side, side))
        box *= 2

    return None


def _png_data_uri(image: QImage) -> str:
    """A QImage as a `data:image/png;base64,...` URI.

    PNG, not JPEG: markers are transparent outside their own outline, and a JPEG
    would render every one of them on an opaque rectangle.
    """
    buffer_data = QByteArray()
    buffer = QBuffer(buffer_data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    encoded = base64.b64encode(bytes(buffer_data)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _swatch_data_uri(image: QImage) -> str:
    """One icon again, on its own, at legend size.

    The legend cannot crop the sprite sheet - a swatch is an `<img>`, and an
    `<img>` shows a whole file - so each icon needs a second, small copy. At 16
    CSS pixels these cost well under a kilobyte each.
    """
    side = LEGEND_SWATCH_PIXELS * LEGEND_SWATCH_SUPERSAMPLE
    scaled = image.scaled(
        QSize(side, side),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _png_data_uri(scaled)


def _compose_sheet(images: list[QImage], plan: Any) -> QImage:
    """Paint every icon into its planned cell on one transparent sheet."""
    sheet = QImage(plan.width, plan.height, QImage.Format.Format_ARGB32)
    sheet.fill(0)

    painter = QPainter(sheet)
    try:
        for placement, image in zip(plan.placements, images):
            painter.drawImage(placement.x, placement.y, image)
    finally:
        painter.end()

    return sheet


def _with_icons(
    renderer: RendererSpec,
    assignments: list[tuple[str, float]],
) -> RendererSpec:
    """Re-emit the spec with each class carrying its atlas name and drawn size.

    Positional against `layer_symbols`, which is the same order the manifest's
    `get-icon` expression is built in.
    """
    if renderer.kind is RendererKind.CATEGORIZED:
        categories = tuple(
            CategorySpec(
                value=category.value,
                label=category.label,
                symbol=replace(category.symbol, icon_name=name, icon_size=size),
            )
            for category, (name, size) in zip(renderer.categories, assignments)
        )
        return replace(renderer, categories=categories)

    if renderer.kind is RendererKind.GRADUATED:
        classes = tuple(
            GraduatedClassSpec(
                lower=klass.lower,
                upper=klass.upper,
                label=klass.label,
                symbol=replace(klass.symbol, icon_name=name, icon_size=size),
            )
            for klass, (name, size) in zip(renderer.classes, assignments)
        )
        return replace(renderer, classes=classes)

    if renderer.symbol is not None and assignments:
        name, size = assignments[0]
        return replace(
            renderer, symbol=replace(renderer.symbol, icon_name=name, icon_size=size)
        )

    return renderer


def _pair_symbols(
    renderer: RendererSpec,
    qgis_symbols: list[QgsSymbol],
) -> list[QgsSymbol] | None:
    """Line the spec's classes up with the QGIS symbols that drew them.

    Normally the two lists are the same length by construction. The exception is
    the unreferenceable-classification-field fallback, which collapses a
    categorized spec to a single symbol while the QGIS renderer still has all
    its classes - there, the first symbol is the one the spec kept.

    `None` means they could not be lined up and the caller must not guess.
    """
    specs = layer_symbols(renderer)
    if len(specs) == len(qgis_symbols):
        return qgis_symbols
    if renderer.kind is RendererKind.SINGLE and len(specs) == 1 and qgis_symbols:
        return [qgis_symbols[0]]
    return None


def build_icon_atlas(
    qgis_renderer: QgsFeatureRenderer | None,
    renderer: RendererSpec,
    geometry: GeometryKind,
    report: FidelityReportBuilder,
    layer_name: str,
    layer_id: str,
) -> tuple[IconAtlasSpec | None, RendererSpec]:
    """Rasterise a layer's markers, or explain why it kept its circles.

    Returns the sheet and a renderer whose classes name their icons. `(None,
    renderer)` - the unchanged renderer - is the common and correct outcome:
    almost every layer draws with circles, and one that does must export
    byte-for-byte what it always has.
    """
    subject = f"Markers of '{layer_name}'"

    if not needs_atlas(renderer, geometry):
        _report_uncarried_markers(renderer, geometry, report, subject, layer_id)
        return None, renderer

    qgis_symbols = _pair_symbols(renderer, class_symbols(qgis_renderer))
    if qgis_symbols is None:
        report.approximated(
            subject,
            "The layer's classes and its QGIS symbols could not be matched up, "
            "so the markers are drawn as plain circles in the layer's own "
            "colours. The map is correct apart from the marker shapes.",
            layer_id,
        )
        return None, renderer

    # One cell per distinct appearance: the same SVG in one colour used by six
    # classes is drawn once and referenced six times.
    cells: dict[tuple[Any, ...], int] = {}
    order: list[QgsSymbol] = []
    for spec, symbol in zip(layer_symbols(renderer), qgis_symbols):
        key = appearance_key(spec)
        if key not in cells:
            cells[key] = len(order)
            order.append(symbol)

    if over_capacity(len(order)):
        report.unsupported(subject, capacity_note(layer_name, len(order)), layer_id)
        return None, renderer

    images: list[QImage] = []
    for symbol in order:
        image = render_symbol(symbol)
        if image is None:
            report.approximated(
                subject,
                "At least one marker rendered as an empty picture, or is far too "
                "large to rasterise, so the layer is drawn with plain circles in "
                "its own colours instead. Check the symbol in QGIS for a zero "
                "size, a missing SVG file, or a size given in map units.",
                layer_id,
            )
            return None, renderer
        images.append(image)

    plan = plan_atlas([image.width() for image in images])
    if not plan.fits:
        report.unsupported(
            subject, texture_note(layer_name, plan.width, plan.height), layer_id
        )
        return None, renderer

    sheet = _compose_sheet(images, plan)
    swatches = {
        placement.name: _swatch_data_uri(image)
        for placement, image in zip(plan.placements, images)
    }

    # Each class points at its cell, and asks for the height that cell was
    # measured at. The sheet is supersampled, so the drawn size is the cell's
    # side divided back down.
    assignments = [
        (
            icon_name(cells[appearance_key(spec)]),
            plan.placements[cells[appearance_key(spec)]].side / SUPERSAMPLE,
        )
        for spec in layer_symbols(renderer)
    ]

    report.preserved(
        subject,
        f"{len(order)} marker{'' if len(order) == 1 else 's'} drawn by QGIS into "
        "the map, so SVG files, parametrised fills, marker shapes and stacked "
        "symbol layers appear as they do in QGIS.",
        layer_id,
    )

    atlas = IconAtlasSpec(
        data_uri=_png_data_uri(sheet),
        mapping=plan.mapping,
        swatches=swatches,
        supersample=SUPERSAMPLE,
    )
    return atlas, _with_icons(renderer, assignments)


def _report_uncarried_markers(
    renderer: RendererSpec,
    geometry: GeometryKind,
    report: FidelityReportBuilder,
    subject: str,
    layer_id: str,
) -> None:
    """Markers on a layer the sheet cannot help.

    A line or polygon layer can still carry marker symbol layers - a marker
    line drawing arrows along a road, a point-pattern fill - and an icon sheet
    does not reach them: deck.gl has no marker-along-a-path. Said once per
    layer, because saying nothing is how qgis2web loses them.
    """
    if geometry is GeometryKind.POINT:
        return
    decorated = [
        symbol
        for symbol in layer_symbols(renderer)
        if symbol.icon_path or symbol.marker_shape
    ]
    if not decorated:
        return
    report.unsupported(
        subject,
        "The symbology places markers along lines or inside polygons (a marker "
        "line or a point-pattern fill). The web map draws the line or polygon "
        "itself, without those markers.",
        layer_id,
    )
