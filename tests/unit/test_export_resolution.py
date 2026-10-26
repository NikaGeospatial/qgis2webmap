"""The resolution a static frame is drawn at from the saved one."""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_resolution import (
    DEFAULT_EXPORT_DPI,
    MAX_FRAME_PIXELS,
    MAX_FRAME_SIDE_PX,
    frame_resolution,
)


def test_a_frame_below_the_saved_resolution_is_raised_to_it() -> None:
    result = frame_resolution(200, 300, 277, 144)
    assert (result.dpi, result.limited) == (300, False)


def test_a_frame_above_the_saved_resolution_keeps_its_own() -> None:
    assert frame_resolution(300, 150, 277, 144).dpi == 300


def test_the_default_matches_what_the_runtime_falls_back_to() -> None:
    assert DEFAULT_EXPORT_DPI == 300


@pytest.mark.parametrize("width_mm, height_mm", [(841, 594), (1189, 841), (50, 4000)])
def test_a_huge_frame_stays_within_the_canvas_limits(
    width_mm: float, height_mm: float
) -> None:
    result = frame_resolution(200, 600, width_mm, height_mm)
    assert result.limited and result.wanted == 600
    assert result.dpi >= 200
    side = max(width_mm, height_mm) / 25.4 * result.dpi
    area = width_mm * height_mm / 25.4**2 * result.dpi**2
    assert side <= MAX_FRAME_SIDE_PX * 1.01 or result.dpi == 200
    assert area <= MAX_FRAME_PIXELS * 1.02 or result.dpi == 200


def test_the_frames_own_choice_is_never_lowered_by_the_limit() -> None:
    assert frame_resolution(300, 600, 5000, 5000).dpi == 300
