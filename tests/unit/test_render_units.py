"""Render-unit arithmetic, without QGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    MM_TO_PIXELS,
    GroundSize,
    SymbolSpec,
    uniform_ground_units,
)
from nika_onlymap_exporter.core.render_units import (
    COMMON_UNITS_PER_WEB_MERCATOR_METRE,
    GroundUnits,
    MapUnitScale,
    ground_size,
    to_pixels,
)

SCALE = MapUnitScale(metres_per_pixel=2.0, metres_per_map_unit=0.5)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [(0, MM_TO_PIXELS), (2, 1.0), (4, 96 / 72), (5, 96.0), (1, 0.25), (7, 0.5)],
)
def test_one_unit_in_pixels(unit: int, expected: float) -> None:
    assert to_pixels(1.0, unit, SCALE) == pytest.approx(expected)


@pytest.mark.parametrize("unit", [1, 7])
def test_map_units_need_a_scale(unit: int) -> None:
    assert to_pixels(1.0, unit) is None


@pytest.mark.parametrize("unit", [3, 6, "x", None])
def test_percentage_and_unknown_have_no_pixels(unit: object) -> None:
    assert to_pixels(1.0, unit, SCALE) is None


def test_the_reference_scale_is_a_denominator() -> None:
    assert SCALE.scale_denominator == pytest.approx(2.0 / (0.0254 / 96))


METRIC = GroundUnits(metres_per_map_unit=1.0)
FEET = GroundUnits(metres_per_map_unit=0.3048)
DEGREES = GroundUnits(metres_per_map_unit=None)
MERCATOR = GroundUnits(metres_per_map_unit=None, web_mercator=True)


def test_metres_on_the_ground_are_metres_whatever_the_crs() -> None:
    for ground in (None, METRIC, DEGREES, MERCATOR):
        assert ground_size(100.0, 7, ground) == GroundSize(100.0, "meters")


def test_map_units_of_a_metric_or_foot_crs_are_metres() -> None:
    assert ground_size(100.0, 1, METRIC) == GroundSize(100.0, "meters")
    size = ground_size(100.0, 1, FEET)
    assert size is not None and size.units == "meters"
    assert size.value == pytest.approx(30.48)


def test_map_units_of_web_mercator_are_deck_common_units() -> None:
    size = ground_size(100.0, 1, MERCATOR)
    assert size is not None and size.units == "common"
    assert size.value == pytest.approx(100.0 * 512.0 / 40_075_016.686)
    assert pytest.approx(1.2776e-5, rel=1e-3) == COMMON_UNITS_PER_WEB_MERCATOR_METRE


@pytest.mark.parametrize("ground", [None, DEGREES])
def test_map_units_of_degrees_are_not_a_ground_distance(
    ground: GroundUnits | None,
) -> None:
    assert ground_size(1.0, 1, ground) is None


@pytest.mark.parametrize("unit", [0, 2, 3, 4, 5, "x", None])
def test_screen_and_unknown_units_are_not_ground_sizes(unit: object) -> None:
    assert ground_size(1.0, unit, METRIC) is None


def test_classes_share_a_ground_size_only_in_one_unit() -> None:
    def metres(value: float) -> SymbolSpec:
        return SymbolSpec(radius=3.0, radius_ground=GroundSize(value, "meters"))

    common = SymbolSpec(radius=3.0, radius_ground=GroundSize(1.0, "common"))
    screen = SymbolSpec(radius=3.0)
    nothing = SymbolSpec(radius=0.0)
    assert uniform_ground_units([metres(1), metres(2)], "radius_ground", "radius") == (
        "meters"
    )
    assert uniform_ground_units([metres(1), common], "radius_ground", "radius") is None
    assert uniform_ground_units([metres(1), screen], "radius_ground", "radius") is None
    # A size of zero is zero in any unit.
    assert uniform_ground_units([metres(1), nothing], "radius_ground", "radius") == (
        "meters"
    )
    assert uniform_ground_units([screen, screen], "radius_ground", "radius") is None
