"""Planning the symbol atlas: which icons a layer needs, and where each one sits.

Pure Python - no PyQGIS, no Qt - so every gating rule and every coordinate in the
sprite sheet is unit-tested in CI. The pixels themselves are drawn by
`symbol_rasterizer`, which needs QGIS; this module decides *what* to draw and
*where to put it*, which is the part that can be got subtly wrong without
anything looking broken.

**Why an atlas at all.** A QGIS point layer may use an SVG file, one of ~40
simple-marker shapes, or a stack of symbol layers. A web renderer draws circles.
qgis2web resolves that by drawing circles and saying nothing - upstream
qgis2web#1218, open since June 2026, with a screenshot of squares becoming
circles. We resolve it by asking QGIS to draw its own markers into a sprite sheet
and pointing deck.gl at them, so the marker on the web map is the marker QGIS
drew, pixel for pixel.

**One appearance, one cell.** The key is the whole drawn picture - file, shape,
colours, stroke width, size, rotation, opacity - because that is what makes two
markers the same picture. It is deliberately *not* keyed on the file alone: the
same SVG in three colours is three pictures. Marker offset is excluded, being a
runtime accessor (`get-icon-pixel-offset`) rather than something drawn into the
cell.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .export_ir import NATIVELY_ROUND_MARKER_SHAPES, GeometryKind, RendererKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from .export_ir import Color, RendererSpec, SymbolSpec

# How many distinct pictures one layer may contribute. Past this the layer keeps
# its circles rather than drawing some markers correctly and some not: a layer
# where half the classes are stars and half are dots reads as a rendering fault,
# where uniform circles plus a fidelity note reads as a stated limitation. It is
# the same whole-layer fallback the unreferenceable-classification-field case
# uses, for the same reason.
MAX_ICONS_PER_LAYER = 256

# The real ceiling is the GPU's maximum texture side, which is 4096 on the
# weakest hardware a recipient might open the map on. Exceeding it does not
# error - the texture is silently downscaled or dropped - so it is checked here.
MAX_ATLAS_PIXELS = 4096

# Icons are rasterised at this multiple of their on-screen size, so they stay
# sharp on a high-DPI display and when the reader zooms the page. Three rather
# than four: four doubles the sheet's byte size over three for a difference no
# one has been able to see.
SUPERSAMPLE = 3

# Sheet cells are named rather than indexed because the name travels into the
# manifest as an expression literal, where `i3` is legible and `3` is not.
ICON_NAME_PREFIX = "i"


def _color_key(color: Color | None) -> tuple[int, int, int, float] | None:
    """A colour as a flat tuple.

    Tuples all the way down, deliberately: the key is used as a dictionary key,
    and a nested `snapshot()` dict would make it unhashable - which fails at the
    first layer that actually needs an atlas, not in any pure test.
    """
    if color is None:
        return None
    return (color.r, color.g, color.b, round(color.a, 4))


def appearance_key(symbol: SymbolSpec) -> tuple[Any, ...]:
    """Everything about a symbol that changes the picture it draws.

    Two symbols with equal keys share one cell. Offset is absent on purpose (a
    runtime accessor), and so is anything the expression language applies per
    feature - adding a field here costs sheet space for nothing.
    """
    return (
        symbol.icon_path,
        symbol.marker_shape,
        _color_key(symbol.fill_color),
        _color_key(symbol.stroke_color),
        round(symbol.stroke_width, 4),
        None if symbol.radius is None else round(symbol.radius, 4),
        round(symbol.rotation, 4),
        round(symbol.opacity, 4),
        symbol.symbol_layer_count,
    )


def is_drawable_as_circle(symbol: SymbolSpec) -> bool:
    """Whether a plain deck.gl circle is an honest rendering of this symbol.

    A circle marker with one symbol layer and no SVG is the case the existing
    `GeoJsonLayer` path already draws correctly, and it is by far the most
    common. Everything else - an SVG file, a named shape that is not a circle,
    a stack of symbol layers - is a picture only QGIS can draw.
    """
    if symbol.icon_path:
        return False
    if symbol.marker_shape and symbol.marker_shape not in NATIVELY_ROUND_MARKER_SHAPES:
        return False
    return symbol.symbol_layer_count <= 1


def layer_symbols(renderer: RendererSpec) -> list[SymbolSpec]:
    """Every symbol a renderer draws with, in the order its expression lists them.

    A single-symbol renderer contributes its one symbol; categorized and
    graduated contribute one per class. The order matters: the manifest's
    `get-icon` expression is positional against exactly this list.
    """
    if renderer.kind is RendererKind.CATEGORIZED:
        return [category.symbol for category in renderer.categories]
    if renderer.kind is RendererKind.GRADUATED:
        return [klass.symbol for klass in renderer.classes]
    if renderer.symbol is not None:
        return [renderer.symbol]
    return []


def needs_atlas(renderer: RendererSpec, geometry: GeometryKind) -> bool:
    """Whether this layer must be drawn with icons rather than circles.

    **Per layer, not per symbol.** If one class uses an SVG, every class in that
    layer is rasterised, including the plain dots. Splitting one QGIS layer
    across two deck.gl layers would make them fight over draw order, and a
    marker drawn behind its own layer's other markers is a worse outcome than a
    dot that came from a sprite sheet.

    Points only: a line or polygon layer has no markers to speak of, and QGIS's
    marker-line and point-pattern-fill symbol layers are a separate feature that
    an icon sheet cannot express.
    """
    if geometry is not GeometryKind.POINT:
        return False
    symbols = layer_symbols(renderer)
    if not symbols:
        return False
    return any(not is_drawable_as_circle(symbol) for symbol in symbols)


@dataclass(frozen=True)
class IconPlacement:
    """One icon's cell in the sheet, in sheet pixels."""

    name: str
    x: int
    y: int
    side: int

    def as_mapping(self) -> dict[str, Any]:
        """deck.gl's `iconMapping` entry for this cell.

        The anchor is the cell's centre because a QGIS marker is centred on its
        feature. deck.gl's default anchor is also the centre, but it is stated
        rather than assumed: a default that changed would move every marker on
        every exported map by half its own size, silently.

        `mask` stays false so the sheet's own colours are drawn. A masked icon
        is a silhouette tinted by `getIconColor`, which would throw away exactly
        the parametrised SVG fills this feature exists to preserve.
        """
        return {
            "x": self.x,
            "y": self.y,
            "width": self.side,
            "height": self.side,
            "anchorX": self.side / 2,
            "anchorY": self.side / 2,
            "mask": False,
        }


@dataclass(frozen=True)
class AtlasPlan:
    """Where every icon sits in the sheet, and how big the sheet is."""

    placements: tuple[IconPlacement, ...]
    cell: int
    columns: int
    width: int
    height: int

    @property
    def mapping(self) -> dict[str, dict[str, Any]]:
        return {p.name: p.as_mapping() for p in self.placements}

    @property
    def fits(self) -> bool:
        return max(self.width, self.height) <= MAX_ATLAS_PIXELS


def icon_name(index: int) -> str:
    return f"{ICON_NAME_PREFIX}{index}"


def plan_atlas(sides: Sequence[int]) -> AtlasPlan:
    """Lay out one square cell per icon in a near-square grid.

    A grid rather than a tight bin-pack: cells differ little in size in practice
    (they are markers), the wasted space is a few kilobytes of transparent PNG
    that compresses to nothing, and a grid is verifiable by arithmetic where a
    packer is verifiable only by looking at the picture.

    Near-square rather than a single row, because a 256-icon row would be
    thousands of pixels wide and hit the texture limit while using a fraction of
    the area a square of the same capacity would.

    Each icon keeps its *own* side inside a uniform cell, centred, so an icon
    smaller than the largest is not stretched: `IconPlacement.side` is the
    icon's true extent and the mapping addresses exactly that sub-rectangle.
    """
    if not sides:
        return AtlasPlan(placements=(), cell=0, columns=0, width=0, height=0)

    cell = max(int(side) for side in sides)
    columns = math.ceil(math.sqrt(len(sides)))
    rows = math.ceil(len(sides) / columns)

    placements = []
    for index, side in enumerate(sides):
        column, row = index % columns, index // columns
        # Centred in its cell: the mapping's anchor is the icon's own centre, so
        # an off-centre icon would draw offset from its feature by the padding.
        inset = (cell - int(side)) // 2
        placements.append(
            IconPlacement(
                name=icon_name(index),
                x=column * cell + inset,
                y=row * cell + inset,
                side=int(side),
            )
        )

    return AtlasPlan(
        placements=tuple(placements),
        cell=cell,
        columns=columns,
        width=columns * cell,
        height=rows * cell,
    )


def over_capacity(count: int) -> bool:
    return count > MAX_ICONS_PER_LAYER


def capacity_note(layer_name: str, count: int) -> str:
    """Why a layer with too many distinct markers kept its circles."""
    return (
        f"'{layer_name}' draws {count} distinct marker appearances, more than "
        f"the {MAX_ICONS_PER_LAYER} that fit one sprite sheet. The layer is "
        "drawn with plain circles in the layer's own colours instead of "
        "rasterising some markers and not others. Reduce the number of classes, "
        "or give several classes the same marker, to keep the shapes."
    )


def texture_note(layer_name: str, width: int, height: int) -> str:
    """Why a layer whose markers are individually enormous kept its circles."""
    return (
        f"'{layer_name}' needs a {width}x{height} pixel sprite sheet for its "
        f"markers, past the {MAX_ATLAS_PIXELS} pixel limit some graphics "
        "hardware imposes. The layer is drawn with plain circles instead, "
        "because a sheet over that limit is dropped by the browser and the "
        "markers would not appear at all. Reduce the marker sizes in QGIS to "
        "keep the shapes."
    )
