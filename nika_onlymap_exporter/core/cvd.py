"""Colour-vision simulation and the legend colour check (issue #4 §2).

Pure Python, no PyQGIS and no Qt, so CI proves it without either.

**This is a port, not a design.** The OnlyMap cartograph runtime ships the same
check as `lintLegendColours` (`src/cartograph/cvd.ts`, runtime 0.12.0), and the
two must report the same pairs: the plugin's Colour vision dialog and a
cartograph's own validator disagreeing about whether a palette is safe would
leave the author trusting whichever one they happened to look at. Every number
and every rule below is the runtime's:

- Viénot, Brettel & Mollon (1999) dichromat matrices, applied in **linear**
  RGB. The issue text proposed Machado (2009) matrices on gamma-encoded sRGB
  with a Euclidean threshold of 48; the runtime replaced that, because those
  matrices are invertible - no two distinct colours ever map to the same output
  - so a check built on them can never report a collapse at all. The Viénot
  matrices project onto the dichromat's own colour plane: protan and deutan
  have identical red and green rows, tritan identical green and blue rows,
  and that singularity is the confusion axis disappearing.
- CIEDE2000 distance, threshold 10 ("hard to tell apart at swatch size").
- Pairs are compared within one layer only, every pair rather than adjacent
  ones - including graduated ramps, which the issue wanted checked between
  neighbours only. The runtime does not special-case ramps, and matching it
  matters more than the refinement.
- Pairs already closer than the threshold under normal vision are skipped:
  that is a palette bug of a different kind, and reporting it as a
  colour-vision finding would send the author looking in the wrong place.

`tests/fixtures/cvd-palette.json` pins exact pairs and distances, so a drift
from the runtime fails here first.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from .export_ir import Color, ExportLayer, ExportProject, GeometryKind, RendererKind

Condition = Literal["protanopia", "deuteranopia", "tritanopia", "achromatopsia"]
Rgb = tuple[float, float, float]
Lab = tuple[float, float, float]

# The runtime's own order. It is also the order collapses are reported in and
# the tie-break for the worst condition, so it is not cosmetic.
CONDITIONS: tuple[Condition, ...] = (
    "protanopia",
    "deuteranopia",
    "tritanopia",
    "achromatopsia",
)

# Row-major 3x3, in LINEAR RGB. Copied from the runtime's `CVD_MATRICES`; the
# repeated rows are the simulation, not a typo.
MATRICES: dict[Condition, tuple[float, ...]] = {
    "protanopia": (
        0.11238,
        0.88762,
        0.0,
        0.11238,
        0.88762,
        0.0,
        0.00401,
        -0.00401,
        1.0,
    ),
    "deuteranopia": (
        0.29275,
        0.70725,
        0.0,
        0.29275,
        0.70725,
        0.0,
        -0.02234,
        0.02234,
        1.0,
    ),
    "tritanopia": (
        1.0,
        0.14461,
        -0.14461,
        0.0,
        0.85659,
        0.14341,
        0.0,
        0.85659,
        0.14341,
    ),
    "achromatopsia": (
        0.2126,
        0.7152,
        0.0722,
        0.2126,
        0.7152,
        0.0722,
        0.2126,
        0.7152,
        0.0722,
    ),
}

# Below this CIEDE2000 distance two swatches are hard to tell apart.
COLLAPSE_THRESHOLD = 10.0

# The colour the web map draws a class with no colour of its own in -
# `manifest_builder.color_literal(None)`. A legend derived from the map shows
# that grey for the class, so the check has to see it too.
NO_COLOUR = "#888888"


# --------------------------------------------------------------------------
# Colour spaces
# --------------------------------------------------------------------------


def srgb_to_linear(channel: float) -> float:
    """One 0-255 sRGB channel to 0-1 linear light."""
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value: float) -> float:
    """0-1 linear light to a 0-255 sRGB channel, clamped and NOT rounded:
    the runtime keeps the fraction, and rounding here would move distances
    that sit near the threshold to the other side of it."""
    c = min(1.0, max(0.0, value))
    return 255 * (c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def linear_rgb_to_xyz(linear: Rgb) -> tuple[float, float, float]:
    """Linear sRGB to CIE XYZ (D65), Y of white = 1."""
    r, g, b = linear
    return (
        r * 0.4124564 + g * 0.3575761 + b * 0.1804375,
        r * 0.2126729 + g * 0.7151522 + b * 0.072175,
        r * 0.0193339 + g * 0.119192 + b * 0.9503041,
    )


def xyz_to_lab(xyz: tuple[float, float, float]) -> Lab:
    """CIE XYZ to CIELAB against the D65 white the runtime uses."""
    x, y, z = xyz

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def srgb_to_lab(rgb: Rgb) -> Lab:
    """A 0-255 sRGB colour to CIELAB."""
    linear = (srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]), srgb_to_linear(rgb[2]))
    return xyz_to_lab(linear_rgb_to_xyz(linear))


def parse_colour(text: str) -> Rgb | None:
    """`#rgb`, `#rrggbb`, `rgb()` or `rgba()` to channels; `None` otherwise.

    The runtime's `parseColor`, rule for rule - including returning `None` for
    `#rrggbbaa` - so a legend row the runtime skips is skipped here too.
    """
    value = text.strip().lower()
    hex_match = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            return (
                int(digits[0] * 2, 16),
                int(digits[1] * 2, 16),
                int(digits[2] * 2, 16),
            )
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    rgb_match = re.match(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)", value)
    if rgb_match:
        try:
            return (
                float(rgb_match.group(1)),
                float(rgb_match.group(2)),
                float(rgb_match.group(3)),
            )
        except ValueError:  # "1.2.3" matches the pattern and is not a number
            return None
    return None


def hex_colour(rgb: Rgb) -> str:
    """`#rrggbb`, rounding each channel - for display, never for the check."""
    r, g, b = (max(0, min(255, round(channel))) for channel in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# Simulation and distance
# --------------------------------------------------------------------------


def simulate(rgb: Rgb, condition: Condition) -> Rgb:
    """`rgb` as a reader with `condition` sees it, in 0-255 sRGB (unrounded)."""
    m = MATRICES[condition]
    r, g, b = srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]), srgb_to_linear(rgb[2])
    return (
        linear_to_srgb(m[0] * r + m[1] * g + m[2] * b),
        linear_to_srgb(m[3] * r + m[4] * g + m[5] * b),
        linear_to_srgb(m[6] * r + m[7] * g + m[8] * b),
    )


def ciede2000(lab1: Lab, lab2: Lab) -> float:
    """CIEDE2000 colour difference (kL = kC = kH = 1).

    Written to Sharma, Wu & Dalal (2005), whose test table pins it, and in the
    same order of operations as the runtime's `deltaE00`.
    """
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    rad = math.pi / 180
    deg = 180 / math.pi

    avg_c = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(avg_c**7 / (avg_c**7 + 25**7)))
    ap1, ap2 = a1 * (1 + g), a2 * (1 + g)
    cp1, cp2 = math.hypot(ap1, b1), math.hypot(ap2, b2)

    def hue(ap: float, bp: float) -> float:
        if ap == 0 and bp == 0:
            return 0.0
        angle = math.atan2(bp, ap) * deg
        return angle if angle >= 0 else angle + 360

    hp1, hp2 = hue(ap1, b1), hue(ap2, b2)

    d_l = l2 - l1
    d_c = cp2 - cp1
    dh = 0.0
    if cp1 * cp2 != 0:
        dh = hp2 - hp1
        if dh > 180:
            dh -= 360
        elif dh < -180:
            dh += 360
    d_h = 2 * math.sqrt(cp1 * cp2) * math.sin((dh * rad) / 2)

    avg_l = (l1 + l2) / 2
    avg_cp = (cp1 + cp2) / 2
    avg_h = hp1 + hp2
    if cp1 * cp2 != 0:
        if abs(hp1 - hp2) > 180:
            avg_h += 360 if hp1 + hp2 < 360 else -360
        avg_h /= 2

    t = (
        1
        - 0.17 * math.cos((avg_h - 30) * rad)
        + 0.24 * math.cos(2 * avg_h * rad)
        + 0.32 * math.cos((3 * avg_h + 6) * rad)
        - 0.2 * math.cos((4 * avg_h - 63) * rad)
    )
    s_l = 1 + (0.015 * (avg_l - 50) ** 2) / math.sqrt(20 + (avg_l - 50) ** 2)
    s_c = 1 + 0.045 * avg_cp
    s_h = 1 + 0.015 * avg_cp * t
    r_t = (
        -2
        * math.sqrt(avg_cp**7 / (avg_cp**7 + 25**7))
        * math.sin(60 * math.exp(-(((avg_h - 275) / 25) ** 2)) * rad)
    )
    return math.sqrt(
        (d_l / s_l) ** 2
        + (d_c / s_c) ** 2
        + (d_h / s_h) ** 2
        + r_t * (d_c / s_c) * (d_h / s_h)
    )


def delta_e00(rgb1: Rgb, rgb2: Rgb) -> float:
    """CIEDE2000 between two 0-255 sRGB colours - the runtime's `deltaE00`."""
    return ciede2000(srgb_to_lab(rgb1), srgb_to_lab(rgb2))


# --------------------------------------------------------------------------
# The legend check
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LegendEntry:
    """One legend class: what the reader sees as a swatch and a label.

    `layer` is the grouping key - rows are only compared within one layer,
    because a reader tells two layers' swatches apart by which block of the
    legend they sit in. It is the layer id when the entry comes from the export
    model, so two layers that share a name are still two groups; `layer_name`
    is what the dialog shows. `None` groups like the runtime's literal rows.
    """

    label: str
    color: str
    layer: str | None = None
    layer_name: str | None = None

    @property
    def display_layer(self) -> str:
        return self.layer_name if self.layer_name is not None else (self.layer or "")


@dataclass(frozen=True)
class Collapse:
    """One pair that becomes indistinguishable under one condition."""

    condition: Condition
    a: LegendEntry
    b: LegendEntry
    # CIEDE2000 after simulation - smaller is worse.
    distance: float


@dataclass(frozen=True)
class PairFinding:
    """One collapsing pair across every condition it collapses under.

    `condition` and `distance` are the condition the pair is CLOSEST under:
    the worst case for that pair, which is what a fix has to beat.
    """

    a: LegendEntry
    b: LegendEntry
    condition: Condition
    distance: float
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class LegendLint:
    """The check's answer. `collapses` and `worst_condition` mirror the
    runtime's `LegendColourLint` field for field and in the same order."""

    collapses: tuple[Collapse, ...] = ()
    # The condition with the most collapses; ties go to the earlier one in
    # `CONDITIONS`, as in the runtime. `None` when nothing collapses.
    worst_condition: Condition | None = None

    def under(self, condition: Condition) -> tuple[Collapse, ...]:
        return tuple(c for c in self.collapses if c.condition == condition)

    def pairs(self) -> tuple[PairFinding, ...]:
        """One finding per pair, in the order each pair first collapsed."""
        grouped: dict[tuple[LegendEntry, LegendEntry], list[Collapse]] = {}
        for collapse in self.collapses:
            grouped.setdefault((collapse.a, collapse.b), []).append(collapse)
        findings: list[PairFinding] = []
        for (a, b), collapses in grouped.items():
            worst = min(collapses, key=lambda c: c.distance)
            findings.append(
                PairFinding(
                    a=a,
                    b=b,
                    condition=worst.condition,
                    distance=worst.distance,
                    conditions=tuple(c.condition for c in collapses),
                )
            )
        return tuple(findings)


def lint_legend_colours(
    entries: Iterable[LegendEntry], threshold: float = COLLAPSE_THRESHOLD
) -> LegendLint:
    """Which legend classes become indistinguishable under colour-vision
    deficiency. The runtime's `lintLegendColours`; see the module docstring.

    Entries whose colour cannot be parsed are left out, as the runtime does -
    a check that guessed at a colour would report pairs nobody can see.
    """
    groups: dict[str, list[tuple[LegendEntry, Rgb]]] = {}
    for entry in entries:
        rgb = parse_colour(entry.color)
        if rgb is None:
            continue
        groups.setdefault(entry.layer or "", []).append((entry, rgb))

    collapses: list[Collapse] = []
    per_condition: dict[Condition, int] = {}
    for condition in CONDITIONS:
        for group in groups.values():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    (entry_a, rgb_a), (entry_b, rgb_b) = group[i], group[j]
                    if delta_e00(rgb_a, rgb_b) < threshold:
                        continue  # already too close for anyone
                    distance = delta_e00(
                        simulate(rgb_a, condition), simulate(rgb_b, condition)
                    )
                    if distance < threshold:
                        collapses.append(
                            Collapse(condition, entry_a, entry_b, distance)
                        )
                        per_condition[condition] = per_condition.get(condition, 0) + 1

    worst: Condition | None = None
    worst_count = 0
    for condition, count in per_condition.items():
        if count > worst_count:
            worst, worst_count = condition, count
    return LegendLint(collapses=tuple(collapses), worst_condition=worst)


# --------------------------------------------------------------------------
# Legend classes from the export model
# --------------------------------------------------------------------------


def _css(color: Color | None) -> str:
    """Opaque `#rrggbb`. Alpha is dropped: it changes how dark a swatch prints
    over white, not its hue, and the runtime cannot parse `#rrggbbaa` anyway."""
    if color is None:
        return NO_COLOUR
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}"


def legend_entries(layers: Iterable[ExportLayer]) -> tuple[LegendEntry, ...]:
    """One `LegendEntry` per legend class of every vector layer, in order.

    The colour is the one the web map draws the class with, by the same rule as
    `manifest_builder`: a categorized or graduated line takes its fill or else
    its stroke, any other class its fill (grey when it has none), and a single
    symbol its fill or else its stroke. Rasters, tile services and renderers we
    could not translate have no classes to compare and are left out, as are
    categories switched off in QGIS - they are not drawn, so not in the legend.
    A layer listed twice (two frames showing it) is listed once.
    """
    entries: list[LegendEntry] = []
    seen: set[str] = set()
    for layer in layers:
        if layer.raster is not None or layer.tiles is not None:
            continue
        if layer.layer_id in seen:
            continue
        seen.add(layer.layer_id)
        renderer = layer.renderer
        is_line = layer.geometry_kind is GeometryKind.LINE

        def entry(label: str, color: str, layer: ExportLayer = layer) -> LegendEntry:
            return LegendEntry(
                label=label, color=color, layer=layer.layer_id, layer_name=layer.name
            )

        if renderer.kind is RendererKind.SINGLE and renderer.symbol is not None:
            symbol = renderer.symbol
            entries.append(
                entry(layer.name, _css(symbol.fill_color or symbol.stroke_color))
            )
        elif renderer.kind is RendererKind.CATEGORIZED:
            hidden = set(renderer.hidden_values)
            for category in renderer.categories:
                if category.value in hidden:
                    continue
                symbol = category.symbol
                colour = symbol.fill_color or (symbol.stroke_color if is_line else None)
                label = category.label or (
                    "" if category.value is None else str(category.value)
                )
                entries.append(entry(label, _css(colour)))
        elif renderer.kind is RendererKind.GRADUATED:
            for graduated in renderer.classes:
                symbol = graduated.symbol
                colour = symbol.fill_color or (symbol.stroke_color if is_line else None)
                label = graduated.label or f"{graduated.lower:g} - {graduated.upper:g}"
                entries.append(entry(label, _css(colour)))
    return tuple(entries)


def legend_entries_for_projects(
    projects: Sequence[ExportProject],
) -> tuple[LegendEntry, ...]:
    """`legend_entries` over every layer of several export models - one per
    live frame of a cartograph - with shared layers listed once."""
    return legend_entries(layer for project in projects for layer in project.layers)
