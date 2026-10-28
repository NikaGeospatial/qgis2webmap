"""Render a static cartograph frame: a region of a `QgsPrintLayout` -> PNG +
its four georeferenced corners.

This is the production version of `specs/cartograph/spikes/render.py`, which
proved (`specs/cartograph/spike-findings.md` #2) that a region render at a
chosen DPI, placed by its extent's four corners transformed to EPSG:4326,
lands with **zero** error against QGIS's own `mapToItemCoords` on an
unrotated map. The two things the spike did not have to deal with, because
its layout had exactly one item, are the two reasons this module exists
rather than just inlining the spike:

- **Furniture bleed.** Any other layout item overlapping the map item's
  rectangle (a legend corner, a label) would get baked into the raster if it
  were visible during the render. Every other item is hidden first and its
  visibility restored afterwards -- including on an exception, so a render
  failure cannot leave the layout's other items permanently hidden under the
  caller.
- **Rotation.** `item.extent()` is an axis-aligned bounding box; a rotated
  map's rendered rectangle is not axis-aligned in map coordinates. The map
  item's own `visibleExtentPolygon()` returns the four map-CRS points that
  correspond to the item's own four corners (verified headlessly by rotating
  a square extent 30 degrees and checking the first returned point against
  the hand-rotated top-left corner) -- `tl, tr, br, bl`, matching the
  cartograph runtime's own corner order, with no further reasoning about
  rotation direction needed here.
- **The map's own grids and overviews.** The runtime draws `<om-graticule>`
  and `overview-of` over a static frame itself, so they are switched off for
  the render too; baking them in as well would draw each one twice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemMap,
    QgsLayoutMeasurementConverter,
    QgsLayoutRenderContext,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import (
    QBuffer,
    QIODevice,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QImage, QPainter, QPainterPath, QPen, QTransform

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

LonLat = tuple[float, float]

# QGIS's own setting that makes a layout export use raster clipping masks
# (`QgsLayoutItemMap.settingForceRasterMasks`, not exposed to Python).
_FORCE_RASTER_MASKS_KEY = "qgis/layout/force-raster-masks"


@dataclass(frozen=True)
class StaticFrameRender:
    """Everything `core/cartograph_export.py` needs to build a static
    `FrameSpec`: the raster, its placement, and its CRS."""

    png_bytes: bytes
    corners: tuple[LonLat, LonLat, LonLat, LonLat]
    crs_authid: str
    crs_def: str


@contextlib.contextmanager
def _selection_untouched(layout: QgsPrintLayout) -> Iterator[None]:
    """Hiding a layout item deselects it (Qt), and the Edit tab follows the
    layout's selection, so a render that hides the other items would empty
    the tab after every map change. The selection is taken first, the
    layout's signals are held while the items are hidden and shown again, and
    the same items are selected again before the signals are released: to
    everything listening, the selection never changed. Nothing is repainted
    in between (no event loop turns inside the render's hiding window)."""
    selected = [
        item for item in layout.selectedLayoutItems() if isinstance(item, QgsLayoutItem)
    ]
    was_blocked = layout.blockSignals(True)
    try:
        yield
    finally:
        for item in selected:
            if not sip.isdeleted(item) and item.isVisible():
                item.setSelected(True)
        layout.blockSignals(was_blocked)


def render_static_frame(
    layout: QgsPrintLayout,
    map_item: QgsLayoutItemMap,
    *,
    dpi: int = 200,
) -> StaticFrameRender:
    """Render `map_item`'s own rectangle at `dpi`, with every other item on
    the layout, and the map's own grids and overviews, hidden for the
    duration of the render."""
    others = [
        item
        for item in layout.items()
        if isinstance(item, QgsLayoutItem) and item is not map_item
    ]
    previous_visibility = {item.uuid(): item.isVisible() for item in others}
    overlays = [*map_item.grids().asList(), *map_item.overviews().asList()]
    previous_enabled = [overlay.enabled() for overlay in overlays]

    with _selection_untouched(layout):
        try:
            for item in others:
                item.setVisible(False)
            for overlay in overlays:
                overlay.setEnabled(False)
            png_bytes = _render_region(layout, map_item, dpi)
        finally:
            for item in others:
                item.setVisible(previous_visibility[item.uuid()])
            for overlay, enabled in zip(overlays, previous_enabled):
                overlay.setEnabled(enabled)

    crs = map_item.crs()
    project = layout.project() or QgsProject.instance()
    return StaticFrameRender(
        png_bytes=png_bytes,
        corners=frame_corners_wgs84(map_item, project),
        crs_authid=crs.authid(),
        crs_def=crs.toProj(),
    )


def frame_corners_wgs84(
    map_item: QgsLayoutItemMap, project: QgsProject
) -> tuple[LonLat, LonLat, LonLat, LonLat]:
    """The map item's tl/tr/br/bl corners in lon/lat: its own
    `visibleExtentPolygon()`, so a rotated map's corners turn with it."""
    transform = QgsCoordinateTransform(map_item.crs(), WGS84, project)
    corners_map = [(point.x(), point.y()) for point in map_item.visibleExtentPolygon()][
        :4
    ]
    corners = tuple(_to_wgs84(transform, x, y) for x, y in corners_map)
    return corners  # type: ignore[return-value]  # always length 4, checked below


def _to_wgs84(transform: QgsCoordinateTransform, x: float, y: float) -> LonLat:
    point = transform.transform(QgsPointXY(x, y))
    return (point.x(), point.y())


def _render_region(
    layout: QgsPrintLayout, map_item: QgsLayoutItemMap, dpi: int
) -> bytes:
    converter = QgsLayoutMeasurementConverter()
    converter.setDpi(layout.renderContext().dpi())
    size = converter.convert(map_item.sizeWithUnits(), QgsUnitTypes.LayoutMillimeters)

    # The raster is the item in its own local space, w x h; the cartograph
    # rotates the placed frame itself, so a rotated item rendered by its
    # scene position would be cropped and then turned a second time. Rendered
    # unrotated about its centre, and put back exactly afterwards.
    rotation = map_item.itemRotation()
    original_position = map_item.positionWithUnits()
    # The item's opacity travels as the placed frame's own CSS opacity
    # (`FrameSpec.opacity`); left on the map here it would be baked into the
    # picture as well, and the frame would be drawn translucent twice.
    opacity = map_item.itemOpacity()
    # The item has to be drawn to be rendered: a hidden frame is exported
    # hidden but still needs its raster for when it is shown.
    hidden_ancestors = _hidden_chain(map_item)
    try:
        for hidden in hidden_ancestors:
            hidden.setVisible(True)
        map_item.setItemOpacity(1.0)
        if rotation:
            map_item.setItemRotation(0, True)
        position = converter.convert(
            map_item.positionWithUnits(), QgsUnitTypes.LayoutMillimeters
        )
        pixel_size = QSize(
            round(size.width() / 25.4 * dpi), round(size.height() / 25.4 * dpi)
        )
        region = QRectF(position.x(), position.y(), size.width(), size.height())
        image = QgsLayoutExporter(layout).renderRegionToImage(region, pixel_size, dpi)
    finally:
        map_item.setItemOpacity(opacity)
        if rotation:
            map_item.setItemRotation(rotation, False)
            map_item.attemptMove(original_position)
        for hidden in hidden_ancestors:
            hidden.setVisible(False)

    return encode_png(image)


def _hidden_chain(item: QgsLayoutItem) -> list[QgsLayoutItem]:
    """`item` and the groups it sits in, those of them that are hidden."""
    chain: list[QgsLayoutItem] = []
    current: QgsLayoutItem | None = item
    while current is not None:
        if not current.isVisible():
            chain.append(current)
        current = current.parentGroup()
    return chain


class AsyncFrameRender(QObject):
    """`render_static_frame`'s picture, drawn without holding the GUI thread.

    The slow part of a static frame is its layers, and QGIS draws them in
    worker threads when asked to start a render job instead of waiting for it
    (`QgsLayoutItemMap` itself waits, which is what froze the dialog for the
    seconds a dense map takes). So the map is drawn the way the item draws it
    while a layout is exported - same settings, same painter geometry, same
    background and frame, the same two ways of drawing it (straight onto the
    page, or onto an intermediate picture when a layer has a blend mode or
    effect) - but through a job that reports when it is done.

    The item's state is read on the GUI thread, inside `start`, with the same
    hiding and neutralising `render_static_frame` does, and put back before
    `start` returns: the layout is never left changed across an event-loop
    turn. After that the job owns only an image and a painter; it never
    touches the layout. `supports` says which items this reproduces exactly;
    any other item goes through the synchronous path, so the page is the same
    either way.
    """

    finished = pyqtSignal()

    def __init__(
        self,
        layout: QgsPrintLayout,
        map_item: QgsLayoutItemMap,
        dpi: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._layout = layout
        self._map_item = map_item
        self._dpi = dpi
        self._image: QImage | None = None
        self._painter: QPainter | None = None
        self._job: QgsMapRendererCustomPainterJob | None = None
        self._done = False
        # Set when the layers are drawn onto an intermediate picture that is
        # laid over the page afterwards (see `start`).
        self._layers_image: QImage | None = None
        self._layers_painter: QPainter | None = None
        self._layers_offset = QPointF()
        self._dots_per_mm = 1.0
        self._page_transform = QTransform()
        self._frame_pen: QPen | None = None
        self._frame_rect = QRectF()
        self._flags = Qgis.RenderContextFlags()
        self.corners: tuple[LonLat, LonLat, LonLat, LonLat] | None = None
        self.crs_authid = ""
        self.crs_def = ""

    @staticmethod
    def supports(layout: QgsPrintLayout, map_item: QgsLayoutItemMap) -> bool:
        """Whether the item paints nothing but its background, layers and
        frame - the parts this reproduces."""
        if map_item.itemClippingSettings().isActive():
            return False
        if map_item.atlasClippingSettings().enabled():
            return False
        if map_item.blendMode() != QPainter.CompositionMode.CompositionMode_SourceOver:
            return False
        if map_item.drawAnnotations():
            project = layout.project() or QgsProject.instance()
            if (
                project.annotationManager().annotations()
                or project.mainAnnotationLayer().items()
            ):
                return False
        return not any(
            isinstance(item, QgsLayoutItem) and map_item.isLabelBlockingItem(item)
            for item in layout.items()
        )

    @property
    def done(self) -> bool:
        return self._done

    def start(self) -> None:
        """Take the item's state and start drawing. GUI thread."""
        layout, map_item = self._layout, self._map_item
        others = [
            item
            for item in layout.items()
            if isinstance(item, QgsLayoutItem) and item is not map_item
        ]
        previous_visibility = {item.uuid(): item.isVisible() for item in others}
        overlays = [*map_item.grids().asList(), *map_item.overviews().asList()]
        previous_enabled = [overlay.enabled() for overlay in overlays]
        converter = QgsLayoutMeasurementConverter()
        converter.setDpi(layout.renderContext().dpi())
        size = converter.convert(
            map_item.sizeWithUnits(), QgsUnitTypes.LayoutMillimeters
        )
        rotation = map_item.itemRotation()
        original_position = map_item.positionWithUnits()
        opacity = map_item.itemOpacity()
        hidden_ancestors = _hidden_chain(map_item)
        with _selection_untouched(layout):
            try:
                for item in others:
                    item.setVisible(False)
                for overlay in overlays:
                    overlay.setEnabled(False)
                for hidden in hidden_ancestors:
                    hidden.setVisible(True)
                map_item.setItemOpacity(1.0)
                if rotation:
                    map_item.setItemRotation(0, True)
                self._begin(size.width(), size.height())
            finally:
                for item in others:
                    item.setVisible(previous_visibility[item.uuid()])
                for overlay, enabled in zip(overlays, previous_enabled):
                    overlay.setEnabled(enabled)
                map_item.setItemOpacity(opacity)
                if rotation:
                    map_item.setItemRotation(rotation, False)
                    map_item.attemptMove(original_position)
                for hidden in hidden_ancestors:
                    hidden.setVisible(False)

    def _begin(self, width_mm: float, height_mm: float) -> None:
        """What `QgsLayoutItemMap.paint` does for an exported layout, up to
        starting the layers' job. The layout is already neutralised."""
        layout, map_item, dpi = self._layout, self._map_item, self._dpi
        context = layout.renderContext()
        self._flags = context.renderContextFlags()
        smooth = bool(context.flags() & QgsLayoutRenderContext.Flag.FlagAntialiasing)
        pixel_size = QSize(round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi))
        image = QImage(pixel_size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        # `QgsLayoutExporter.renderRegionToImage` takes the resolution from
        # the image and region sizes, averaged over both axes.
        resolution = (
            (pixel_size.width() / width_mm + pixel_size.height() / height_mm) / 2 * 25.4
        )
        image.setDotsPerMeterX(_round_half_up(resolution / 25.4 * 1000))
        image.setDotsPerMeterY(_round_half_up(resolution / 25.4 * 1000))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, smooth)
        # `QGraphicsScene.render` keeps the region's aspect ratio and puts its
        # corner on the image's.
        ratio = min(pixel_size.width() / width_mm, pixel_size.height() / height_mm)
        painter.scale(ratio, ratio)
        self._page_transform = painter.transform()
        rect_mm = QRectF(0, 0, width_mm, height_mm)
        self._frame_rect = rect_mm
        if map_item.frameEnabled():
            self._frame_pen = map_item.pen()
        if context.flags() & QgsLayoutRenderContext.Flag.FlagLosslessImageRendering:
            painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering, True)

        extent = map_item.extent()
        map_to_mm = map_item.mapUnitsToLayoutUnits()
        forced_vector = (
            context.rasterizedRenderingPolicy()
            == Qgis.RasterizedRenderingPolicy.ForceVector
        )
        if map_item.containsAdvancedEffects() and not forced_vector:
            # A layer with a blend mode or effect has to meet the background
            # in one flat picture, which is then laid over the page.
            bounds = map_item.boundingRect()
            dots_per_mm = ratio
            destination_dpi = ratio * 25.4
            layers_size = QSize(
                _round_half_up(bounds.width() / 25.4 * destination_dpi),
                _round_half_up(bounds.height() / 25.4 * destination_dpi),
            )
            layers_image = QImage(layers_size, QImage.Format.Format_ARGB32)
            layers_image.fill(Qt.GlobalColor.transparent)
            layers_image.setDotsPerMeterX(_round_half_up(1000 * destination_dpi / 25.4))
            layers_image.setDotsPerMeterY(_round_half_up(1000 * destination_dpi / 25.4))
            layers_painter = QPainter(layers_image)
            top_left = QPointF(-bounds.left(), -bounds.top())
            paint_rect = QRect(
                _round_half_up(top_left.x() * dots_per_mm),
                _round_half_up(top_left.y() * dots_per_mm),
                _round_half_up(width_mm * dots_per_mm),
                _round_half_up(height_mm * dots_per_mm),
            )
            layers_painter.setClipRect(paint_rect)
            layers_painter.translate(paint_rect.topLeft())
            if map_item.hasBackground():
                layers_painter.scale(dots_per_mm, dots_per_mm)
                self._fill_background(layers_painter, rect_mm)
                layers_painter.scale(1 / dots_per_mm, 1 / dots_per_mm)
            settings = map_item.mapSettings(
                extent,
                QSizeF(paint_rect.size()),
                layers_image.logicalDpiX(),
                True,
            )
            self._layers_image, self._layers_painter = layers_image, layers_painter
            self._layers_offset = QPointF(
                -top_left.x() * dots_per_mm, -top_left.y() * dots_per_mm
            )
            self._dots_per_mm = dots_per_mm
            target = layers_painter
        else:
            if map_item.hasBackground():
                self._fill_background(painter, rect_mm)
            painter.setClipRect(rect_mm)
            dots_per_mm = image.logicalDpiX() / 25.4
            settings = map_item.mapSettings(
                extent,
                QSizeF(
                    extent.width() * map_to_mm * dots_per_mm,
                    extent.height() * map_to_mm * dots_per_mm,
                ),
                image.logicalDpiX(),
                True,
            )
            painter.scale(1 / dots_per_mm, 1 / dots_per_mm)
            target = painter
        _as_export_settings(settings, layout)
        job = QgsMapRendererCustomPainterJob(settings, target)
        self._image, self._painter, self._job = image, painter, job
        project = layout.project() or QgsProject.instance()
        crs = map_item.crs()
        self.corners = frame_corners_wgs84(map_item, project)
        self.crs_authid = crs.authid()
        self.crs_def = crs.toProj()
        job.finished.connect(self._on_job_finished)
        job.start()

    def _fill_background(self, painter: QPainter, rect_mm: QRectF) -> None:
        """`QgsLayoutItem.drawBackground`."""
        painter.save()
        painter.setBrush(self._map_item.brush())
        painter.setPen(Qt.PenStyle.NoPen)
        self._set_hints(painter)
        painter.drawPath(_rect_path(rect_mm))
        painter.restore()

    def _set_hints(self, painter: QPainter) -> None:
        """`QgsRenderContext.setPainterFlagsUsingContext`."""
        flags = self._flags
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            bool(flags & Qgis.RenderContextFlag.Antialiasing),
        )
        painter.setRenderHint(
            QPainter.RenderHint.LosslessImageRendering,
            bool(flags & Qgis.RenderContextFlag.LosslessImageRendering),
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            bool(flags & Qgis.RenderContextFlag.HighQualityImageTransforms),
        )

    def _on_job_finished(self) -> None:
        painter = self._painter
        if painter is not None:
            if self._layers_painter is not None and self._layers_image is not None:
                self._layers_painter.end()
                self._layers_painter = None
                painter.setTransform(self._page_transform)
                painter.scale(1 / self._dots_per_mm, 1 / self._dots_per_mm)
                painter.drawImage(self._layers_offset, self._layers_image)
                painter.scale(self._dots_per_mm, self._dots_per_mm)
            else:
                painter.setTransform(self._page_transform)
                painter.setClipping(False)
            if self._frame_pen is not None:
                # The item's own frame, drawn over its layers as QGIS does.
                painter.save()
                painter.setPen(self._frame_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                self._set_hints(painter)
                painter.drawPath(_rect_path(self._frame_rect))
                painter.restore()
            painter.end()
            self._painter = None
        self._done = True
        self.finished.emit()

    def cancel(self) -> None:
        """Stop drawing; the picture is abandoned. Blocks until the job's
        threads have stopped, so the images and painters may then go."""
        job, self._job = self._job, None
        if job is not None and not self._done:
            with contextlib.suppress(TypeError, RuntimeError):
                job.finished.disconnect(self._on_job_finished)
            job.cancel()
        for painter in (self._layers_painter, self._painter):
            if painter is not None:
                painter.end()
        self._layers_painter = self._painter = None
        self._done = True

    def take_image(self) -> QImage:
        """The finished picture."""
        if not self._done or self._image is None:
            raise RuntimeError("the frame has not finished drawing")
        return self._image


def _rect_path(rect: QRectF) -> QPainterPath:
    """`QgsLayoutItem.framePath`: QGIS fills and strokes the item as a path,
    which rasterises its edges differently from `QPainter.drawRect`."""
    path = QPainterPath()
    path.addRect(rect)
    return path


def _round_half_up(value: float) -> int:
    """`std::round`, which QGIS rounds its pixel sizes with (Python's own
    `round` goes to the even neighbour)."""
    return math.floor(value + 0.5)


def _as_export_settings(settings: QgsMapSettings, layout: QgsPrintLayout) -> None:
    """Turn `QgsLayoutItemMap.mapSettings` into what the item uses while a
    layout is exported. The item picks its settings by whether the layout is
    being previewed, which it is here, outside the exporter; an export draws
    without the preview's optimisations and raster masks, and says so to the
    layers (`RendererUsage.Export`). Without this the picture differs from the
    exported one in line ends and dash phases."""
    context = layout.renderContext()
    settings.setDpiTarget(-1.0)
    settings.setDevicePixelRatio(1.0)
    simplify = context.simplifyMethod()
    no_simplification = Qgis.VectorRenderingSimplificationFlag.NoSimplification
    settings.setFlag(
        Qgis.MapSettingsFlag.UseRenderingOptimization,
        simplify.simplifyHints() != no_simplification,
    )
    settings.setSimplifyMethod(simplify)
    settings.setMaskSettings(context.maskSettings())
    settings.setRendererUsage(Qgis.RendererUsage.Export)
    settings.setFlag(
        Qgis.MapSettingsFlag.ForceRasterMasks,
        bool(QgsSettings().value(_FORCE_RASTER_MASKS_KEY, False, type=bool)),
    )


def encode_png(image: QImage) -> bytes:
    """`image` as PNG bytes - the encoding `_render_region` ends with. Safe
    off the GUI thread: it touches nothing but the image."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())
