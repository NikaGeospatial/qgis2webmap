"""A frame with a blend-mode layer stays within the picture-size limits through
the intermediate picture it is drawn onto, and a frame the job cannot start
still comes out right on the synchronous path.

The asynchronous job draws such a frame onto a picture a little larger than
the frame (the item's bounding box, frame line included). The limits are
scaled down so every picture here is really allocated and drawn; the frame is
chosen so that its own picture fits at the wanted resolution and the larger
one does not.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from cartograph_fixtures import all_static

import nika_onlymap_exporter.core.export_resolution as resolution
import nika_onlymap_exporter.exporters.frame_raster as frame_raster
from nika_onlymap_exporter.core.cartograph_export import prepare_cartograph
from nika_onlymap_exporter.core.cartograph_ir import FrameSpec
from nika_onlymap_exporter.core.export_resolution import (
    FrameTooLargeError,
    frame_pixel_size,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.exporters.frame_raster import AsyncFrameRender
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

SIDE_LIMIT = 700
#: 100 mm drawn at 177 dpi is 697 px: inside the side limit, while the
#: working picture (100 mm + the frame line) is not.
WANTED_DPI = 177
FRAME_MM = (100.0, 60.0)
MARKER_RGB = (220, 20, 20)


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(resolution, "MAX_FRAME_SIDE_PX", SIDE_LIMIT)
    monkeypatch.setattr(resolution, "MAX_FRAME_PIXELS", 10_000_000)


@pytest.fixture
def fake_runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


def _build(project, *, blend: bool = True):
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsLayoutItemMap,
        QgsLayoutMeasurement,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsMarkerSymbol,
        QgsPointXY,
        QgsPrintLayout,
        QgsRectangle,
        QgsSingleSymbolRenderer,
        QgsUnitTypes,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtGui import QColor, QPainter

    layer = QgsVectorLayer("Point?crs=EPSG:3857", "mark", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5000, 3000)))
    layer.dataProvider().addFeatures([feature])
    symbol = QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "{},{},{}".format(*MARKER_RGB), "size": "12"}
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    if blend:
        layer.setBlendMode(QPainter.CompositionMode.CompositionMode_Multiply)
    project.addMapLayer(layer)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("near limit")
    item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    mm = QgsUnitTypes.LayoutMillimeters
    item.attemptMove(QgsLayoutPoint(20, 20, mm))
    item.attemptResize(QgsLayoutSize(*FRAME_MM, mm))
    item.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    item.setLayers([layer])
    item.setExtent(QgsRectangle(0, 0, 10000, 6000))
    item.setFrameEnabled(True)
    item.setFrameStrokeColor(QColor(0, 90, 0))
    item.setFrameStrokeWidth(QgsLayoutMeasurement(1.2, mm))
    item.refresh()
    return layout, item


def _prepare(layout, project, runtime, report, **kwargs):
    return prepare_cartograph(
        layout,
        project,
        runtime,
        report,
        frame_modes=all_static(layout),
        raster_cache=None,
        **kwargs,
    )


def _finish(qgis_app, pending) -> bytes:
    deadline = time.monotonic() + 60
    while not all(p.render.done for p in pending) and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.02)
    assert all(p.render.done for p in pending), "the frame never finished drawing"
    return frame_raster.encode_png(pending[0].render.take_image())


def _image(png: bytes):
    from qgis.PyQt.QtGui import QImage

    image = QImage.fromData(png, "PNG")
    assert not image.isNull()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _marker_centre(image) -> tuple[float, float]:
    import numpy as np

    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    data = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), -1, 4)
    rgb = data[:, : image.width(), [2, 1, 0]].astype(int)
    hit = np.abs(rgb - np.array(MARKER_RGB)).max(axis=2) <= 45
    ys, xs = np.nonzero(hit)
    assert len(xs), "no marker in the picture"
    return (xs.mean() + 0.5) / image.width(), (ys.mean() + 0.5) / image.height()


def _within(size) -> bool:
    return max(size) <= SIDE_LIMIT


class _Allocations:
    """Records every picture size the frame code allocates (and allocates it)."""

    def __init__(self, monkeypatch) -> None:
        self.sizes: list[tuple[int, int]] = []
        log = self
        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QImage

        class RecordingImage(QImage):
            def __init__(self, *args) -> None:
                if args and isinstance(args[0], QSize):
                    log.sizes.append((args[0].width(), args[0].height()))
                super().__init__(*args)

        monkeypatch.setattr(frame_raster, "QImage", RecordingImage)


def test_the_precondition_the_frame_fits_and_its_working_picture_does_not(
    qgis_app, project, small_limits
) -> None:
    layout, item = _build(project)
    assert AsyncFrameRender.supports(layout, item)
    bounds = AsyncFrameRender.working_bounds_mm(layout, item)
    assert bounds is not None and bounds[0] > FRAME_MM[0]
    assert _within(frame_pixel_size(*FRAME_MM, WANTED_DPI))
    assert not _within(resolution.working_pixel_size(*FRAME_MM, *bounds, WANTED_DPI))


def test_a_frame_without_blend_modes_has_no_working_picture(qgis_app, project) -> None:
    layout, item = _build(project, blend=False)
    assert AsyncFrameRender.working_bounds_mm(layout, item) is None


class TestAsynchronousNearTheLimit:
    def test_no_picture_allocated_is_over_the_limits(
        self, qgis_app, project, small_limits, fake_runtime, monkeypatch
    ) -> None:
        layout, item = _build(project)
        log = _Allocations(monkeypatch)
        pending: list = []
        report = FidelityReportBuilder()
        prepared = _prepare(
            layout,
            project,
            fake_runtime,
            report,
            frame_dpis={item.uuid(): WANTED_DPI},
            pending_static=pending,
        )
        assert pending, "the frame was not drawn asynchronously"
        png = _finish(qgis_app, pending)
        assert log.sizes and all(_within(size) for size in log.sizes), log.sizes
        frame = next(i for i in prepared.items if isinstance(i, FrameSpec))
        assert frame.dpi < WANTED_DPI
        image = _image(png)
        assert (image.width(), image.height()) == frame_pixel_size(
            frame.box.w, frame.box.h, frame.dpi
        )
        centre = _marker_centre(image)
        assert centre == pytest.approx((0.5, 0.5), abs=0.02)

    def test_the_message_is_accurate(
        self, qgis_app, project, small_limits, fake_runtime
    ) -> None:
        layout, item = _build(project)
        report = FidelityReportBuilder()
        pending: list = []
        prepared = _prepare(
            layout,
            project,
            fake_runtime,
            report,
            frame_dpis={item.uuid(): WANTED_DPI},
            pending_static=pending,
        )
        _finish(qgis_app, pending)
        frame = next(i for i in prepared.items if isinstance(i, FrameSpec))
        details = [i.detail for i in report.items if "Requested" in i.detail]
        assert len(details) == 1
        assert (
            f"Requested {WANTED_DPI} dpi, drawn at {int(frame.dpi)} dpi" in details[0]
        )
        width_px, height_px = frame_pixel_size(frame.box.w, frame.box.h, frame.dpi)
        assert f"{width_px} x {height_px}" in details[0]
        # The frame's own picture would have fitted, and the message does not
        # blame it.
        assert "browser" not in details[0]

    def test_a_frame_without_a_working_picture_keeps_the_wanted_resolution(
        self, qgis_app, project, small_limits, fake_runtime
    ) -> None:
        layout, item = _build(project, blend=False)
        report = FidelityReportBuilder()
        pending: list = []
        prepared = _prepare(
            layout,
            project,
            fake_runtime,
            report,
            frame_dpis={item.uuid(): WANTED_DPI},
            pending_static=pending,
        )
        _finish(qgis_app, pending)
        frame = next(i for i in prepared.items if isinstance(i, FrameSpec))
        assert frame.dpi == WANTED_DPI
        assert not any("Requested" in i.detail for i in report.items)

    def test_the_synchronous_export_keeps_the_wanted_resolution(
        self, qgis_app, project, small_limits, fake_runtime
    ) -> None:
        """The synchronous path draws no larger picture, so nothing is lost."""
        layout, item = _build(project)
        report = FidelityReportBuilder()
        prepared = _prepare(
            layout,
            project,
            fake_runtime,
            report,
            frame_dpis={item.uuid(): WANTED_DPI},
        )
        frame = next(i for i in prepared.items if isinstance(i, FrameSpec))
        assert frame.dpi == WANTED_DPI
        image = _image(frame.image_bytes)
        assert (image.width(), image.height()) == frame_pixel_size(
            frame.box.w, frame.box.h, WANTED_DPI
        )
        assert not any("Requested" in i.detail for i in report.items)


class TestTheFallbackIsCorrect:
    """The job refusing to start (its picture is over the limits) is a path
    `frame_resolution` should now keep out of reach; it is still the safety
    net, so it is driven by pretending the working picture is bigger than the
    limits count it."""

    def test_a_refused_job_falls_back_to_a_correct_picture(
        self, qgis_app, project, small_limits, fake_runtime, monkeypatch
    ) -> None:
        layout, item = _build(project)
        real = frame_raster.working_pixel_size

        def too_big(*args):
            width, height = real(*args)
            return width + SIDE_LIMIT, height

        monkeypatch.setattr(frame_raster, "working_pixel_size", too_big)
        # The job refuses on its own, whatever the resolution.
        with pytest.raises(FrameTooLargeError):
            AsyncFrameRender(layout, item, 100).start()
        report = FidelityReportBuilder()
        pending: list = []
        prepared = _prepare(
            layout,
            project,
            fake_runtime,
            report,
            frame_dpis={item.uuid(): 100},
            pending_static=pending,
        )
        assert pending == [], "the refused job was kept"
        frame = next(i for i in prepared.items if isinstance(i, FrameSpec))
        assert frame.dpi == 100
        image = _image(frame.image_bytes)
        assert (image.width(), image.height()) == frame_pixel_size(
            frame.box.w, frame.box.h, 100
        )
        assert _marker_centre(image) == pytest.approx((0.5, 0.5), abs=0.02)
        assert not any("Requested" in i.detail for i in report.items)
        assert item.isVisible()
