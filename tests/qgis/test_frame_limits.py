"""A static frame's picture is never allocated over the size limits: the
resolution is brought down first (and said), and the render functions refuse an
oversized request themselves.

The oversized cases use arithmetic and a stand-in for the render boundary;
nothing enormous is ever allocated. Results that need a real picture use
limits scaled down for the test.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from cartograph_fixtures import all_static, build_survey_layout

import nika_onlymap_exporter.core.export_resolution as resolution
import nika_onlymap_exporter.exporters.frame_raster as frame_raster
from nika_onlymap_exporter.core.cartograph_export import prepare_cartograph
from nika_onlymap_exporter.core.cartograph_ir import FrameSpec, element_id
from nika_onlymap_exporter.core.export_resolution import (
    MAX_FRAME_PIXELS,
    MAX_FRAME_SIDE_PX,
    FrameTooLargeError,
    frame_pixel_size,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.exporters.frame_raster import (
    AsyncFrameRender,
    render_static_frame,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

#: Far over the real limits for any frame in the survey layout.
HUGE_DPI = 20_000


@pytest.fixture
def survey(project, tmp_path):
    return build_survey_layout(project, tmp_path)


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


def _within(width_px: int, height_px: int) -> bool:
    return (
        max(width_px, height_px) <= MAX_FRAME_SIDE_PX
        and width_px * height_px <= MAX_FRAME_PIXELS
    )


class _StopError(Exception):
    """Raised by the stand-in allocation so nothing is ever allocated."""


class _AllocationLog:
    """Stands in for the two places a frame's picture is allocated: records
    every size asked for and refuses to allocate it."""

    def __init__(self, monkeypatch) -> None:
        self.sizes: list[tuple[int, int]] = []
        log = self
        from qgis.PyQt.QtCore import QSize
        from qgis.PyQt.QtGui import QImage

        class RecordingImage(QImage):
            def __init__(self, *args) -> None:
                if args and isinstance(args[0], QSize):
                    log.sizes.append((args[0].width(), args[0].height()))
                    raise _StopError
                super().__init__(*args)

        class RecordingExporter:
            def __init__(self, layout) -> None:
                pass

            def renderRegionToImage(self, region, size, dpi):  # noqa: N802
                log.sizes.append((size.width(), size.height()))
                raise _StopError

        monkeypatch.setattr(frame_raster, "QImage", RecordingImage)
        monkeypatch.setattr(frame_raster, "QgsLayoutExporter", RecordingExporter)


def _static_frames(prepared) -> list[FrameSpec]:
    return [i for i in prepared.items if isinstance(i, FrameSpec)]


def _prepare(survey, fake_runtime, report=None, **kwargs):
    return prepare_cartograph(
        survey.layout,
        survey.project,
        fake_runtime,
        report if report is not None else FidelityReportBuilder(),
        frame_modes=all_static(survey.layout),
        raster_cache=None,
        **kwargs,
    )


class TestOversizedRequestsNeverReachAllocation:
    def test_a_synchronous_render_refuses_an_oversized_request(
        self, survey, monkeypatch
    ) -> None:
        log = _AllocationLog(monkeypatch)
        with pytest.raises(FrameTooLargeError):
            render_static_frame(survey.layout, survey.main_map, dpi=HUGE_DPI)
        assert log.sizes == []
        assert survey.main_map.isVisible()

    def test_an_asynchronous_render_refuses_an_oversized_request(
        self, survey, monkeypatch
    ) -> None:
        log = _AllocationLog(monkeypatch)
        with pytest.raises(FrameTooLargeError):
            AsyncFrameRender(survey.layout, survey.main_map, HUGE_DPI).start()
        assert log.sizes == []
        assert survey.main_map.isVisible()

    @pytest.mark.parametrize("asynchronous", [False, True])
    @pytest.mark.parametrize("export_dpi", [None, HUGE_DPI])
    def test_an_export_asks_the_allocation_for_a_size_within_the_limits(
        self, survey, fake_runtime, monkeypatch, asynchronous, export_dpi
    ) -> None:
        log = _AllocationLog(monkeypatch)
        frame_dpis = {survey.main_map.uuid(): HUGE_DPI}
        pending: list = [] if asynchronous else None
        with pytest.raises(_StopError):
            _prepare(
                survey,
                fake_runtime,
                frame_dpis=frame_dpis,
                export_dpi=export_dpi,
                pending_static=pending,
            )
        assert log.sizes, "nothing was asked to be allocated"
        assert all(_within(*size) for size in log.sizes), log.sizes


@pytest.fixture
def small_limits(monkeypatch):
    """The same rules at a size a test can really draw."""
    monkeypatch.setattr(resolution, "MAX_FRAME_SIDE_PX", 700)
    monkeypatch.setattr(resolution, "MAX_FRAME_PIXELS", 300_000)


def _decoded_size(png_bytes: bytes) -> tuple[int, int]:
    from qgis.PyQt.QtGui import QImage

    image = QImage.fromData(png_bytes, "PNG")
    assert not image.isNull()
    return image.width(), image.height()


def _finish_pending(qgis_app, pending) -> dict[str, bytes]:
    deadline = time.monotonic() + 60
    while not all(p.render.done for p in pending) and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.02)
    assert all(p.render.done for p in pending), "a frame never finished drawing"
    return {p.frame_id: frame_raster.encode_png(p.render.take_image()) for p in pending}


class TestAReducedFrameIsDrawnAtWhatFitsAndSaysSo:
    @pytest.mark.parametrize("export_dpi", [None, 300])
    def test_synchronous(self, survey, fake_runtime, small_limits, export_dpi) -> None:
        report = FidelityReportBuilder()
        frame_dpis = {survey.main_map.uuid(): 600}
        prepared = _prepare(
            survey, fake_runtime, report, frame_dpis=frame_dpis, export_dpi=export_dpi
        )
        main = {f.id: f for f in _static_frames(prepared)}[
            element_id("frame", survey.main_map.uuid())
        ]
        width_px, height_px = _decoded_size(main.image_bytes)
        assert _within(width_px, height_px)
        assert main.dpi < 600
        assert (width_px, height_px) == frame_pixel_size(
            main.box.w, main.box.h, main.dpi
        )
        details = [i.detail for i in report.items if "Requested 600 dpi" in i.detail]
        assert len(details) == 1
        assert f"drawn at {int(main.dpi)} dpi" in details[0]
        assert f"{width_px} x {height_px}" in details[0]

    def test_asynchronous(self, qgis_app, survey, fake_runtime, small_limits) -> None:
        report = FidelityReportBuilder()
        pending: list = []
        frame_dpis = {survey.main_map.uuid(): 600}
        prepared = _prepare(
            survey,
            fake_runtime,
            report,
            frame_dpis=frame_dpis,
            pending_static=pending,
        )
        assert pending, "the frames were not drawn asynchronously"
        pngs = _finish_pending(qgis_app, pending)
        by_id = {f.id: f for f in _static_frames(prepared)}
        for frame_id, png in pngs.items():
            frame = by_id[frame_id]
            size = _decoded_size(png)
            assert _within(*size)
            assert size == frame_pixel_size(frame.box.w, frame.box.h, frame.dpi)
        main = by_id[element_id("frame", survey.main_map.uuid())]
        assert main.dpi < 600

    def test_an_unreduced_frame_says_nothing(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        _prepare(
            survey,
            fake_runtime,
            report,
            frame_dpis={survey.main_map.uuid(): 100},
            export_dpi=100,
        )
        assert not any("dpi" in i.detail for i in report.items)

    def test_no_message_names_the_same_resolution_twice(
        self, survey, fake_runtime, small_limits
    ) -> None:
        report = FidelityReportBuilder()
        _prepare(
            survey,
            fake_runtime,
            report,
            frame_dpis={survey.main_map.uuid(): 900},
            export_dpi=450,
        )
        import re

        for item in report.items:
            match = re.search(r"Requested (\d+) dpi, drawn at (\d+) dpi", item.detail)
            if match:
                assert match.group(1) != match.group(2)


class TestAnOrdinaryFrameIsUnchanged:
    def test_an_a6_frame_at_150_dpi_is_drawn_in_full(self, survey) -> None:
        from qgis.core import QgsLayoutSize, QgsUnitTypes
        from qgis.PyQt.QtGui import QImage

        item = survey.main_map
        item.attemptResize(QgsLayoutSize(105, 148, QgsUnitTypes.LayoutMillimeters))
        render = render_static_frame(survey.layout, item, dpi=150)
        image = QImage.fromData(render.png_bytes, "PNG")
        assert (image.width(), image.height()) == (620, 874)
        # Real map pixels, not an empty picture.
        opaque = sum(
            1
            for x in range(0, image.width(), 7)
            for y in range(0, image.height(), 7)
            if image.pixelColor(x, y).alpha() > 0
        )
        assert opaque > 100
