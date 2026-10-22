"""QGIS render units, as screen pixels.

Pure Python - no PyQGIS, no Qt - so the arithmetic is unit-tested in CI. The
units are read by value (`Qgis.RenderUnit`, `QgsUnitTypes.RenderUnit`), the way
the capitalization table in `labeling_translator` is, so PyQt5 and PyQt6 agree.

**The conversion.** The web runtime's pixel is a CSS pixel, 1/96 inch. So a
millimetre is 96 / 25.4 pixels, a point 96 / 72, an inch 96, and a pixel is a
pixel. This is QGIS's own conversion at 96 dpi (`QgsRenderContext` with a scale
factor of 96 / 25.4 painter units per millimetre), which the QGIS-side tests
check against `QgsRenderContext.convertToPainterUnits`.

**Map units have no fixed size on screen.** A size in map units, or in metres on
the ground (`MetersInMapUnits`), grows with the zoom in QGIS. The web map draws
sizes in pixels, so such a size is fixed at what it measures at one reference
scale, described by `MapUnitScale`, and the caller says so in the fidelity
report. Percentages and unknown units are not convertible at all.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .export_ir import MM_TO_PIXELS

# Screen pixels per unit, at the 96 dpi CSS assumes. `Qgis.RenderUnit` by
# value, as `_CAPITALIZATION_BY_VALUE` below does, so PyQt5 and PyQt6 agree:
# 0 millimetres, 2 pixels, 4 points, 5 inches. Map units (1) and percentages
# (3) have no fixed size on screen and are left out on purpose.
_PIXELS_PER_UNIT = {0: MM_TO_PIXELS, 2: 1.0, 4: 96.0 / 72.0, 5: 96.0}

MAP_UNITS = 1
METERS_IN_MAP_UNITS = 7

# One CSS pixel in metres on the screen: the web's pixel is 1/96 inch.
CSS_PIXEL_METRES = 0.0254 / 96.0


@dataclass(frozen=True)
class MapUnitScale:
    """What one map unit and one pixel are on the ground at the reference scale."""

    metres_per_pixel: float
    metres_per_map_unit: float

    @property
    def scale_denominator(self) -> float:
        """The reference scale as the N of 1:N, for the CSS pixel."""
        return self.metres_per_pixel / CSS_PIXEL_METRES


def unit_key(unit: Any) -> int | None:
    """`unit` as the integer value of `Qgis.RenderUnit`, or `None` if unreadable."""
    try:
        return int(getattr(unit, "value", unit))
    except (TypeError, ValueError):
        return None


def is_map_dependent(unit: Any) -> bool:
    """Whether `unit` is a size on the ground rather than on the screen."""
    return unit_key(unit) in (MAP_UNITS, METERS_IN_MAP_UNITS)


def to_pixels(
    value: float, unit: Any, scale: MapUnitScale | None = None
) -> float | None:
    """`value` in QGIS `unit`, as screen pixels; `None` when it has no fixed size.

    QGIS sizes label text in points by default and the web map in pixels, so
    copying the number across drew a 9 pt label at 9 px. Map units convert only
    when a reference `scale` is given.
    """
    key = unit_key(unit)
    if key is None:
        return None
    if key == MAP_UNITS and scale is not None:
        return float(value) * scale.metres_per_map_unit / scale.metres_per_pixel
    if key == METERS_IN_MAP_UNITS and scale is not None:
        return float(value) / scale.metres_per_pixel
    factor = _PIXELS_PER_UNIT.get(key)
    return None if factor is None else float(value) * factor
