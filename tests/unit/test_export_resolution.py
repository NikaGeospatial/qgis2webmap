"""The resolution a static frame is drawn at from the saved one."""

from __future__ import annotations

import math

import pytest

from nika_onlymap_exporter.core.export_resolution import (
    DEFAULT_EXPORT_DPI,
    MAX_FRAME_PIXELS,
    MAX_FRAME_SIDE_PX,
    FrameTooLargeError,
    check_frame_size,
    frame_pixel_size,
    frame_resolution,
)


def test_a_frame_below_the_saved_resolution_is_raised_to_it() -> None:
    result = frame_resolution(200, 300, 277, 144)
    assert (result.dpi, result.limited) == (300, False)


def test_a_frame_above_the_saved_resolution_keeps_its_own() -> None:
    assert frame_resolution(300, 150, 277, 144).dpi == 300


def test_the_default_matches_what_the_runtime_falls_back_to() -> None:
    assert DEFAULT_EXPORT_DPI == 300


A4 = (297.0, 210.0)
A0 = (1189.0, 841.0)


def _picture(result, width_mm: float, height_mm: float) -> tuple[int, int]:
    return frame_pixel_size(width_mm, height_mm, result.dpi)


def _within_limits(width_px: int, height_px: int) -> bool:
    return (
        max(width_px, height_px) <= MAX_FRAME_SIDE_PX
        and width_px * height_px <= MAX_FRAME_PIXELS
    )


def test_an_ordinary_frame_is_not_touched_at_a_normal_resolution() -> None:
    for dpi in (150, 300, 600):
        result = frame_resolution(dpi, dpi, *A4)
        assert (result.dpi, result.wanted, result.limited) == (dpi, dpi, False)
    assert _picture(frame_resolution(300, 300, *A4), *A4) == (3508, 2480)


@pytest.mark.parametrize("wanted", [300, 600])
def test_an_a0_frame_is_brought_within_both_limits(wanted: int) -> None:
    result = frame_resolution(wanted, wanted, *A0)
    assert result.limited and result.wanted == wanted
    assert result.dpi < wanted
    assert _within_limits(*_picture(result, *A0))


def test_the_a0_at_600_dpi_case_is_not_left_at_558_million_pixels() -> None:
    # The reported bug: ~19,866 x 28,087 px, flagged limited, dpi unchanged.
    width_px, height_px = frame_pixel_size(*A0, 600)
    assert width_px * height_px > 500_000_000
    result = frame_resolution(600, 600, *A0)
    assert result.dpi < 600
    assert _within_limits(*_picture(result, *A0))


def test_a_manual_frame_dpi_cannot_bypass_the_limits() -> None:
    # The old floor kept `frame_dpi` however large it was.
    result = frame_resolution(1200, 300, *A0)
    assert result.limited and result.wanted == 1200 and result.dpi < 1200
    assert _within_limits(*_picture(result, *A0))
    # With no saved resolution to follow at all.
    alone = frame_resolution(1200, None, *A0)
    assert alone.limited and _within_limits(*_picture(alone, *A0))


def test_a_long_narrow_frame_is_held_by_the_side_limit() -> None:
    width_mm, height_mm = 50.0, 4000.0
    result = frame_resolution(200, 600, width_mm, height_mm)
    width_px, height_px = _picture(result, width_mm, height_mm)
    assert result.limited and result.dpi < 600
    assert height_px <= MAX_FRAME_SIDE_PX
    # The side limit binds long before the pixel total does.
    assert width_px * height_px < MAX_FRAME_PIXELS // 10


def test_a_large_square_frame_is_held_by_the_pixel_limit() -> None:
    result = frame_resolution(200, 600, 1000.0, 1000.0)
    width_px, height_px = _picture(result, 1000.0, 1000.0)
    assert result.limited and _within_limits(width_px, height_px)
    # 10,000 x 10,000 is the pixel limit; the side limit was not the one.
    assert max(width_px, height_px) < MAX_FRAME_SIDE_PX
    assert width_px * height_px > MAX_FRAME_PIXELS * 0.98


def test_the_result_is_the_highest_resolution_that_fits() -> None:
    result = frame_resolution(200, 900, *A0)
    assert not _within_limits(*frame_pixel_size(*A0, result.dpi + 1))


def test_aspect_ratio_and_physical_size_are_kept() -> None:
    result = frame_resolution(200, 600, *A0)
    width_px, height_px = _picture(result, *A0)
    assert width_px / height_px == pytest.approx(A0[0] / A0[1], rel=1e-3)
    assert width_px / result.dpi * 25.4 == pytest.approx(A0[0], rel=1e-3)


@pytest.mark.parametrize(
    "side_px, expected_limited",
    [
        (MAX_FRAME_SIDE_PX - 1, False),
        (MAX_FRAME_SIDE_PX, False),
        (MAX_FRAME_SIDE_PX + 1, True),
        (MAX_FRAME_SIDE_PX + 200, True),
    ],
)
def test_sizes_either_side_of_the_side_limit(
    side_px: int, expected_limited: bool
) -> None:
    # A narrow frame of 600 dpi whose long side is exactly `side_px` pixels.
    height_mm = side_px / 600 * 25.4
    result = frame_resolution(600, 600, 10.0, height_mm)
    assert result.limited is expected_limited
    assert _within_limits(*frame_pixel_size(10.0, height_mm, result.dpi))


@pytest.mark.parametrize("extra", [-1, 0, 1, 1000])
def test_sizes_either_side_of_the_pixel_limit(extra: int) -> None:
    # A square of 300 dpi with (about) MAX_FRAME_PIXELS + extra pixels.
    side_px = math.isqrt(MAX_FRAME_PIXELS + extra)
    side_mm = side_px / 300 * 25.4
    result = frame_resolution(300, 300, side_mm, side_mm)
    assert _within_limits(*frame_pixel_size(side_mm, side_mm, result.dpi))
    assert result.limited is (not _within_limits(side_px, side_px))


def test_rounding_cannot_carry_a_picture_over_a_limit() -> None:
    # Sweep many sizes: wherever the bound ignoring rounding says "fits",
    # the rounded integers are still checked, and every result is in range.
    for tenth in range(0, 6000, 7):
        height_mm = 1500.0 + tenth / 10
        for width_mm in (299.9, 700.3, 1000.05):
            for wanted in (299, 300, 601, 1200):
                result = frame_resolution(wanted, wanted, width_mm, height_mm)
                width_px, height_px = frame_pixel_size(width_mm, height_mm, result.dpi)
                assert _within_limits(width_px, height_px), (
                    width_mm,
                    height_mm,
                    wanted,
                )
                assert result.limited == (result.dpi < wanted)


def test_a_reduced_resolution_is_never_reported_as_the_requested_one() -> None:
    for wanted in (300, 450, 600, 1200, 2400):
        result = frame_resolution(200, wanted, *A0)
        assert result.limited is (result.dpi != result.wanted)


def test_a_high_frame_dpi_with_a_different_page_dpi() -> None:
    # Frame asks 900, page saved at 150: the frame's own choice is what is
    # wanted, and it is the one that is reduced.
    result = frame_resolution(900, 150, 400.0, 300.0)
    assert result.wanted == 900 and result.limited and result.dpi < 900
    assert _within_limits(*frame_pixel_size(400.0, 300.0, result.dpi))
    # The other way round: a low frame dpi is raised to the page's, then held.
    result = frame_resolution(100, 1200, 400.0, 300.0)
    assert result.wanted == 1200 and result.limited
    assert _within_limits(*frame_pixel_size(400.0, 300.0, result.dpi))


def test_the_allocation_guard_refuses_what_is_over_the_limits() -> None:
    check_frame_size(MAX_FRAME_SIDE_PX, 6250)
    for width_px, height_px in (
        (MAX_FRAME_SIDE_PX + 1, 10),
        (10_001, 10_000),
        (19_866, 28_087),
    ):
        with pytest.raises(FrameTooLargeError):
            check_frame_size(width_px, height_px)


def test_a_frame_too_large_at_any_resolution_is_not_called_reduced() -> None:
    # 1 dpi would still be over the side limit: nothing to reduce to, so the
    # result must not claim a reduction (the guard refuses it later).
    result = frame_resolution(1, 1, 500_000.0, 10.0)
    assert result.dpi == result.wanted == 1 and not result.limited
