"""Render-unit arithmetic, without QGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import MM_TO_PIXELS
from nika_onlymap_exporter.core.render_units import MapUnitScale, to_pixels

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
