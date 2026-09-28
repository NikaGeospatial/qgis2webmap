"""QGIS scale visibility to the runtime's `visible-zoom-range`.

The conversion is inverted as well as logarithmic, which is exactly the kind of
thing that gets silently reversed - the attribute this replaced paired the two
ends straight across and emitted min > max. So the direction, the reference
point and every one-sided or degenerate range each get a test.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus, ScaleRange
from nika_onlymap_exporter.core.zoom_range import (
    ZOOM_CEILING,
    ZOOM_FLOOR,
    translate_scale_range,
    web_zoom_for_scale,
)

# The scale of a 96-dpi screen showing deck.gl's zoom 0 at the equator: a
# 512-pixel world. The old 256-pixel slippy-map figure is twice this.
ZOOM_0_SCALE = 40_075_016.686 / 512 / (0.0254 / 96)


def mercator_factor(latitude: float) -> float:
    """What QGIS's readout is, relative to true scale, in EPSG:3857."""
    return 1.0 / math.cos(math.radians(latitude))


class TestWebZoomForScale:
    def test_zoom_zero_is_the_512_pixel_world(self) -> None:
        assert web_zoom_for_scale(ZOOM_0_SCALE) == pytest.approx(0.0)

    def test_a_street_scale_is_about_zoom_18(self) -> None:
        # 1:1,000 on a 96-dpi screen is ~0.26 m a pixel, zoom 18.2 in deck.gl.
        assert web_zoom_for_scale(1_000) == pytest.approx(18.17, abs=0.01)

    def test_halving_the_denominator_adds_one_zoom_level(self) -> None:
        assert web_zoom_for_scale(500) - web_zoom_for_scale(1_000) == pytest.approx(1.0)

    def test_it_is_inverted_not_proportional(self) -> None:
        """A smaller denominator is more zoomed in, so a higher zoom."""
        assert web_zoom_for_scale(1_000) > web_zoom_for_scale(1_000_000)

    def test_true_scale_needs_a_lower_zoom_away_from_the_equator(self) -> None:
        # Web Mercator stretches by 1/cos: at 60 degrees the same ground scale
        # is reached one whole zoom level earlier.
        assert web_zoom_for_scale(1_000, latitude=60.0) == pytest.approx(
            web_zoom_for_scale(1_000) - 1.0
        )

    def test_a_mercator_readout_is_the_same_at_every_latitude(self) -> None:
        # EPSG:3857's scale readout already carries Mercator's stretch, so the
        # latitude cancels out: QGIS and the web map switch together.
        at_sixty = web_zoom_for_scale(1_000, 60.0, mercator_factor(60.0))
        assert at_sixty == pytest.approx(web_zoom_for_scale(1_000))


class TestTranslateScaleRange:
    def test_no_scale_visibility_emits_and_reports_nothing(self) -> None:
        result = translate_scale_range(ScaleRange())
        assert result.zoom_range is None
        assert result.status is None

    def test_switched_on_without_limits_is_kept_with_no_range(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=0.0, max_scale=0.0))
        assert result.zoom_range is None
        assert result.status is FidelityStatus.PRESERVED

    def test_both_limits_map_minimum_scale_to_minimum_zoom(self) -> None:
        """QGIS's minimum scale is its most zoomed-OUT limit, so it is the
        lower zoom. Pairing them straight across is the old bug."""
        result = translate_scale_range(ScaleRange(min_scale=1_000_000, max_scale=1_000))
        assert result.zoom_range is not None
        low, high = result.zoom_range
        assert low == pytest.approx(web_zoom_for_scale(1_000_000), abs=0.005)
        assert high == pytest.approx(web_zoom_for_scale(1_000), abs=0.005)
        assert low < high

    def test_only_a_zoomed_out_limit_leaves_the_top_open(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=50_000, max_scale=0.0))
        assert result.zoom_range is not None
        low, high = result.zoom_range
        assert low == pytest.approx(web_zoom_for_scale(50_000), abs=0.005)
        assert high == ZOOM_CEILING
        assert "closer than 1:50,000" in result.detail

    def test_only_a_zoomed_in_limit_leaves_the_bottom_open(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=0.0, max_scale=50_000))
        assert result.zoom_range is not None
        low, high = result.zoom_range
        assert low == ZOOM_FLOOR
        assert high == pytest.approx(web_zoom_for_scale(50_000), abs=0.005)
        assert "further than 1:50,000" in result.detail

    def test_the_detail_states_both_scales_and_both_zooms(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=1_000_000, max_scale=1_000))
        assert "1:1,000,000" in result.detail
        assert "1:1,000" in result.detail
        assert "zoom 8.2 and 18.2" in result.detail

    def test_an_empty_qgis_range_is_not_emitted(self) -> None:
        """QGIS never draws a layer whose zoomed-out limit is not more zoomed
        out than its zoomed-in one; the runtime would reject `min >= max` and
        show it everywhere, so nothing is emitted and the user is told."""
        result = translate_scale_range(ScaleRange(min_scale=1_000, max_scale=5_000))
        assert result.zoom_range is None
        assert result.status is FidelityStatus.UNSUPPORTED

    def test_equal_limits_are_an_empty_range_too(self) -> None:
        # QGIS draws while max <= scale < min, which no scale satisfies here.
        result = translate_scale_range(ScaleRange(min_scale=5_000, max_scale=5_000))
        assert result.zoom_range is None
        assert "contains no scale" in result.detail

    def test_a_range_beyond_the_most_zoomed_out_view_is_not_emitted(self) -> None:
        # Visible only when zoomed out past 1:1e12 - further out than zoom 0.
        result = translate_scale_range(ScaleRange(min_scale=0.0, max_scale=1e12))
        assert result.zoom_range is None
        assert result.status is FidelityStatus.UNSUPPORTED
        assert "outside the zoom levels" in result.detail

    def test_a_range_beyond_the_most_zoomed_in_view_is_not_emitted(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=0.001, max_scale=0.0))
        assert result.zoom_range is None
        assert result.status is FidelityStatus.UNSUPPORTED

    def test_an_enormous_zoomed_out_limit_clamps_to_zoom_zero(self) -> None:
        result = translate_scale_range(ScaleRange(min_scale=1e12, max_scale=1_000))
        assert result.zoom_range is not None
        assert result.zoom_range[0] == ZOOM_FLOOR
        assert result.zoom_range[1] == pytest.approx(18.17, abs=0.01)

    def test_non_finite_limits_count_as_no_limit(self) -> None:
        result = translate_scale_range(
            ScaleRange(min_scale=math.inf, max_scale=math.nan)
        )
        assert result.zoom_range is None
        assert result.status is FidelityStatus.PRESERVED

    def test_every_emitted_range_satisfies_the_runtime(self) -> None:
        """The runtime ignores anything but two finite numbers, min < max."""
        for low_scale in (0.0, 10.0, 1e3, 1e5, 1e7, 1e9, 1e12):
            for high_scale in (0.0, 1.0, 1e2, 1e4, 1e6, 1e8):
                result = translate_scale_range(
                    ScaleRange(min_scale=low_scale, max_scale=high_scale), 45.0
                )
                if result.zoom_range is None:
                    continue
                low, high = result.zoom_range
                assert math.isfinite(low) and math.isfinite(high)
                assert low < high

    def test_a_small_map_is_reported_as_kept(self) -> None:
        result = translate_scale_range(
            ScaleRange(min_scale=1_000_000, max_scale=1_000),
            latitude=10.0,
            edges=[(9.9, 1.0), (10.1, 1.0)],
        )
        assert result.status is FidelityStatus.PRESERVED

    def test_a_tall_map_states_how_far_the_switch_drifts(self) -> None:
        # Centre 30N, edges at the equator and 60N: log2(cos30/cos60) = 0.79.
        result = translate_scale_range(
            ScaleRange(min_scale=1_000_000, max_scale=1_000),
            latitude=30.0,
            edges=[(0.0, 1.0), (60.0, 1.0)],
        )
        assert result.status is FidelityStatus.APPROXIMATED
        assert "up to 0.8 zoom levels" in result.detail
        assert "30.0°N" in result.detail

    def test_a_mercator_project_never_drifts(self) -> None:
        result = translate_scale_range(
            ScaleRange(min_scale=1_000_000, max_scale=1_000),
            latitude=30.0,
            qgis_scale_factor=mercator_factor(30.0),
            edges=[(0.0, mercator_factor(0.0)), (60.0, mercator_factor(60.0))],
        )
        assert result.status is FidelityStatus.PRESERVED

    def test_the_latitude_moves_the_range(self) -> None:
        equator = translate_scale_range(ScaleRange(min_scale=1e6, max_scale=1e3))
        sixty = translate_scale_range(
            ScaleRange(min_scale=1e6, max_scale=1e3), latitude=60.0
        )
        assert equator.zoom_range is not None and sixty.zoom_range is not None
        assert sixty.zoom_range[0] == pytest.approx(equator.zoom_range[0] - 1.0, 0.01)
