"""The cartograph export model -- the boundary between QGIS and the writer.

This module is **pure Python**: no `qgis` import, no Qt. That is what lets a
`CartographSpec` be built and unit-tested in CI where neither is available, and
what keeps `Qgs*` objects from leaking into `writers/cartograph_writer.py`.

Every dataclass here is frozen and uses tuples rather than lists, for the same
reason `core/export_ir.py` does: a model that cannot be mutated behind a
caller's back is safe to pass between the reader, the fidelity report and the
writer without defensive copies.

**The runtime's vocabulary is authoritative.** Element names, attribute names
and enumerated values below are copied from `@nika-js/onlymap`'s
`src/cartograph/schema.ts` (mirrored in `docs/cartograph.md`), not from
`qgis2carto-issues-1-5.md` issue #1's translation table -- the two disagree in
a few places (see `specs/cartograph/spike-findings.md`), and where they do,
this module follows the runtime. Notably:

- There is no `om-shape kind="path"`; `ShapeSpec.kind` is one of
  rect/ellipse/line/arrow.
- `FrameSpec` has no `overview_fill` -- the runtime only exposes
  `overview-stroke`.
- `atlas_margin` is a *fraction* of the feature's extent, never millimetres.

Validation here is deliberately shallow: `__post_init__` rejects values that
are structurally impossible (negative sizes, an unknown `kind`, a corners
tuple that is not exactly 4 points) so a bad spec fails at construction time
rather than producing silently-wrong markup. It does not encode fidelity
policy (e.g. "a derived legend must not point at a static frame") -- that
belongs to the reader and the writer, which have the context to explain *why*
a choice was made.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

# --------------------------------------------------------------------------
# Vocabulary mirrored from @nika-js/onlymap's src/cartograph/schema.ts
# --------------------------------------------------------------------------

#: Built-in theme ids (`CARTOGRAPH_THEMES`).
CARTOGRAPH_THEMES = ("default", "dark", "sepia", "blueprint", "minimal")

#: Page size presets (`PAGE_SIZE_NAMES`). The runtime treats an unknown `size`
#: as A4 silently; this module instead rejects it at construction time.
PAGE_SIZE_NAMES = ("A0", "A1", "A2", "A3", "A4", "A5", "Letter", "Legal")

ORIENTATIONS = ("portrait", "landscape")
FRAME_MODES = ("live", "static")
SCALEBAR_KINDS = ("single", "double", "line", "ticks")
SCALEBAR_UNITS = ("metric", "imperial", "nautical")
NORTH_KINDS = ("arrow", "rose")
GRATICULE_KINDS = ("solid", "cross", "markers")
GRATICULE_LABELS = ("outside", "inside", "none")
GRATICULE_FORMATS = ("dms",)
TEXT_KINDS = ("title", "text", "attribution")

#: `<om-cartograph cvd>` values, long spelling (the runtime also accepts the
#: short `protan`/`deutan`/`tritan`/`achroma` aliases; we always write these).
CVD_MODES = ("none", "protanopia", "deuteranopia", "tritanopia", "achromatopsia")
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow")
IMAGE_FITS = ("contain", "cover", "fill")

LonLat = tuple[float, float]


def _check_choice(value: str | None, allowed: tuple[str, ...], field_name: str) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"{field_name}={value!r} is not one of {allowed}")


def _check_finite(value: float, field_name: str) -> None:
    # NaN never equals itself; this also rejects +-inf, which `isfinite` would
    # catch too but importing `math` for one check is not worth it here.
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name}={value!r} must be a finite number")


# --------------------------------------------------------------------------
# Shared placement geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    """A placed element's position and size, in millimetres from the page's
    top-left -- `x y w h` in the runtime's vocabulary."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            _check_finite(getattr(self, name), name)
        if self.w <= 0:
            raise ValueError(f"Box.w must be positive, got {self.w!r}")
        if self.h <= 0:
            raise ValueError(f"Box.h must be positive, got {self.h!r}")


def _check_corners(corners: tuple[LonLat, ...] | None) -> None:
    if corners is None:
        return
    if len(corners) != 4:
        raise ValueError(f"corners must have exactly 4 points, got {len(corners)}")
    for point in corners:
        if len(point) != 2:
            raise ValueError(f"each corner must be (lon, lat), got {point!r}")
        _check_finite(point[0], "corner.lon")
        _check_finite(point[1], "corner.lat")


# --------------------------------------------------------------------------
# Placed items
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameSpec:
    """A placed map: `<om-frame>`.

    `mode="live"` mounts the frame's own camera (`center`/`zoom`/`bearing`/
    `pitch`) over a map document; `mode="static"` places a georeferenced
    raster by its four `corners` (lon/lat, tl/tr/br/bl order) in `crs`.

    `overview_of` turns this frame into a locator inset drawing another
    frame's footprint -- the runtime calls that an "overview", not an
    "overview_fill"; there is no fill attribute, only `overview_stroke`.
    """

    id: str
    box: Box
    mode: str = "live"
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    # Live frame camera. `src` names an external map document; when absent,
    # the writer embeds a pre-rendered `<om-map>` child for this frame id.
    src: str | None = None
    center: LonLat | None = None
    zoom: float | None = None
    bearing: float = 0.0
    pitch: float = 0.0

    # Static frame georeference.
    crs: str | None = None
    crs_def: str | None = None
    corners: tuple[LonLat, LonLat, LonLat, LonLat] | None = None
    image_bytes: bytes | None = None
    image_path: str | None = None

    # Shared furniture hooks.
    dpi: float | None = None
    overview_of: str | None = None
    overview_stroke: str | None = None
    frame_stroke: str | None = None
    frame_bg: str | None = None
    attribution: str | None = None
    atlas_fit: str | None = None
    atlas_margin: float | None = None

    def __post_init__(self) -> None:
        _check_choice(self.mode, FRAME_MODES, "mode")
        _check_finite(self.rotation, "rotation")
        _check_finite(self.bearing, "bearing")
        _check_finite(self.pitch, "pitch")
        static_only = (
            self.crs,
            self.crs_def,
            self.corners,
            self.image_bytes,
            self.image_path,
        )
        if self.mode == "live":
            if any(static_only):
                raise ValueError("a live frame must not carry static-only fields")
            if self.center is not None and len(self.center) != 2:
                raise ValueError(f"center must be (lon, lat), got {self.center!r}")
        else:  # static
            if self.src is not None or self.center is not None:
                raise ValueError("a static frame must not carry live-only fields")
            _check_corners(self.corners)
            if self.image_bytes is None and self.image_path is None:
                raise ValueError("a static frame needs image_bytes or image_path")
        if self.atlas_fit is not None and self.atlas_fit not in ("feature", "fixed"):
            raise ValueError(
                f"atlas_fit={self.atlas_fit!r} must be 'feature' or 'fixed'"
            )
        if self.atlas_margin is not None and self.atlas_margin < 0:
            raise ValueError(f"atlas_margin must be >= 0, got {self.atlas_margin!r}")


@dataclass(frozen=True)
class GraticuleSpec:
    """A coordinate grid over a frame: `<om-graticule>`."""

    id: str
    box: Box
    for_: str = ""
    crs: str | None = None
    crs_def: str | None = None
    interval: float | None = None
    interval_x: float | None = None
    interval_y: float | None = None
    kind: str = "solid"
    labels: str = "outside"
    format: str | None = None
    color: str | None = None
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.for_:
            raise ValueError("GraticuleSpec.for_ must name the frame it grids")
        _check_choice(self.kind, GRATICULE_KINDS, "kind")
        _check_choice(self.labels, GRATICULE_LABELS, "labels")
        _check_choice(self.format, GRATICULE_FORMATS, "format")
        for name, value in (
            ("interval", self.interval),
            ("interval_x", self.interval_x),
            ("interval_y", self.interval_y),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")


@dataclass(frozen=True)
class LegendRowSpec:
    """One override row inside a legend (`<om-legend-row>`).

    Not a placed element -- the schema gives it no `x y w h`, only content
    overrides targeting a layer's derived rows by their original `label`.
    """

    layer: str | None = None
    match: str | None = None
    label: str | None = None
    hide: bool = False
    order: int | None = None
    color: str | None = None
    shape: str | None = None


@dataclass(frozen=True)
class LegendSpec:
    """A legend panel: `<om-legend>`.

    `derived` is `True` by default -- rows are computed from the frame's own
    layers at render time. Set it `False` (a static frame has no layers to
    derive from) and supply literal `rows`.
    """

    id: str
    box: Box
    for_: str = ""
    title: str | None = None
    columns: int | None = None
    derived: bool = True
    bg: str | None = None
    color: str | None = None
    stroke: str | None = None
    font_size: float | None = None
    padding: float | None = None
    rows: tuple[LegendRowSpec, ...] = ()
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.for_:
            raise ValueError("LegendSpec.for_ must name the frame it legends")
        if self.columns is not None and self.columns <= 0:
            raise ValueError(f"columns must be positive, got {self.columns!r}")
        if self.font_size is not None and self.font_size <= 0:
            raise ValueError(f"font_size must be positive, got {self.font_size!r}")
        if self.padding is not None and self.padding < 0:
            raise ValueError(f"padding must be >= 0, got {self.padding!r}")
        if not self.derived and not self.rows:
            raise ValueError("a non-derived legend needs at least one literal row")


@dataclass(frozen=True)
class ScalebarSpec:
    """A scale bar: `<om-scalebar>`."""

    id: str
    box: Box
    for_: str = ""
    units: str = "metric"
    segments: int | None = None
    segment_length: float | None = None
    kind: str = "single"
    color: str | None = None
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.for_:
            raise ValueError("ScalebarSpec.for_ must name the frame it measures")
        _check_choice(self.units, SCALEBAR_UNITS, "units")
        _check_choice(self.kind, SCALEBAR_KINDS, "kind")
        if self.segments is not None and self.segments <= 0:
            raise ValueError(f"segments must be positive, got {self.segments!r}")
        if self.segment_length is not None and self.segment_length <= 0:
            raise ValueError(
                f"segment_length must be positive, got {self.segment_length!r}"
            )


@dataclass(frozen=True)
class NorthSpec:
    """A north indicator: `<om-north>`."""

    id: str
    box: Box
    for_: str = ""
    kind: str = "arrow"
    color: str | None = None
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.for_:
            raise ValueError("NorthSpec.for_ must name the frame it points from")
        _check_choice(self.kind, NORTH_KINDS, "kind")


@dataclass(frozen=True)
class TextSpec:
    """A text block: `<om-text>`. `body` may contain `{{tokens}}` -- see
    `core/expression_tokens.py`."""

    id: str
    box: Box
    body: str = ""
    kind: str = "text"
    font_size: float | None = None
    weight: str | None = None
    align: str | None = None
    font: str | None = None
    color: str | None = None
    bg: str | None = None
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        _check_choice(self.kind, TEXT_KINDS, "kind")
        if self.font_size is not None and self.font_size <= 0:
            raise ValueError(f"font_size must be positive, got {self.font_size!r}")


@dataclass(frozen=True)
class ImageSpec:
    """A logo or photo: `<om-image>`."""

    id: str
    box: Box
    src: str = ""
    fit: str = "contain"
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.src:
            raise ValueError("ImageSpec.src must not be empty")
        _check_choice(self.fit, IMAGE_FITS, "fit")


@dataclass(frozen=True)
class ShapeSpec:
    """A drawn shape: `<om-shape>`. The runtime has no path kind -- only
    rect/ellipse/line/arrow."""

    id: str
    box: Box
    kind: str = "rect"
    fill: str | None = None
    fill_opacity: float | None = None
    stroke: str | None = None
    stroke_width: float | None = None
    radius: float | None = None
    rotation: float = 0.0
    z: int | None = None
    locked: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        _check_choice(self.kind, SHAPE_KINDS, "kind")
        if self.fill_opacity is not None and not (0.0 <= self.fill_opacity <= 1.0):
            raise ValueError(
                f"fill_opacity must be within [0, 1], got {self.fill_opacity!r}"
            )
        if self.stroke_width is not None and self.stroke_width < 0:
            raise ValueError(f"stroke_width must be >= 0, got {self.stroke_width!r}")
        if self.radius is not None and self.radius < 0:
            raise ValueError(f"radius must be >= 0, got {self.radius!r}")


CartographItem = Union[
    FrameSpec,
    GraticuleSpec,
    LegendSpec,
    ScalebarSpec,
    NorthSpec,
    TextSpec,
    ImageSpec,
    ShapeSpec,
]


@dataclass(frozen=True)
class AtlasSpec:
    """One page per feature: `<om-atlas>`. Not a placed element -- the frames
    it drives carry `atlas_fit`/`atlas_margin` themselves."""

    for_: str = ""
    layer: str = ""
    id: str | None = None
    filter: str | None = None
    sort: str | None = None
    filename: str | None = None
    page_name: str | None = None
    pager: str | None = None

    def __post_init__(self) -> None:
        if not self.for_:
            raise ValueError("AtlasSpec.for_ must name the frame it drives")
        if not self.layer:
            raise ValueError("AtlasSpec.layer must name the coverage layer")
        if self.pager is not None and self.pager not in ("auto", "none"):
            raise ValueError(f"pager={self.pager!r} must be 'auto' or 'none'")


# --------------------------------------------------------------------------
# Page root
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CartographSpec:
    """The page root: `<om-cartograph>`.

    A page carries either a named `size` preset or an explicit `width`/
    `height` in millimetres -- never both, and never neither.
    """

    cartograph_id: str
    title: str = ""
    attribution: str = ""
    theme: str | None = None
    size: str | None = None
    width: float | None = None
    height: float | None = None
    orientation: str | None = None
    bg: str | None = None
    color: str | None = None
    font: str | None = None
    notes: str | None = None
    items: tuple[CartographItem, ...] = field(default_factory=tuple)
    atlas: AtlasSpec | None = None
    # Print-shop controls (runtime "Print-shop attributes"). `bleed` and
    # `safe_zone` are millimetres; `crop_marks` needs a bleed to draw in.
    bleed: float | None = None
    crop_marks: bool = False
    safe_zone: float | None = None
    dpi: int | None = None
    flatten: bool = False
    color_profile: str | None = None
    # Whole-page colour-vision simulation, a review aid (runtime `cvd`).
    cvd: str | None = None

    def __post_init__(self) -> None:
        if not self.cartograph_id:
            raise ValueError("CartographSpec.cartograph_id must not be empty")
        has_size_name = self.size is not None
        has_dimensions = self.width is not None or self.height is not None
        if has_size_name and has_dimensions:
            raise ValueError("set either size or width/height, never both")
        if not has_size_name and not has_dimensions:
            raise ValueError("set either size or width and height")
        if has_dimensions and (self.width is None or self.height is None):
            raise ValueError("width and height must be set together")
        if self.width is not None and self.width <= 0:
            raise ValueError(f"width must be positive, got {self.width!r}")
        if self.height is not None and self.height <= 0:
            raise ValueError(f"height must be positive, got {self.height!r}")
        _check_choice(self.size, PAGE_SIZE_NAMES, "size")
        _check_choice(self.orientation, ORIENTATIONS, "orientation")
        _check_choice(self.theme, CARTOGRAPH_THEMES, "theme")
        _check_choice(self.cvd, CVD_MODES, "cvd")
        if self.bleed is not None and self.bleed < 0:
            raise ValueError(f"bleed must be >= 0, got {self.bleed!r}")
        if self.safe_zone is not None and self.safe_zone < 0:
            raise ValueError(f"safe_zone must be >= 0, got {self.safe_zone!r}")
        if self.dpi is not None and self.dpi <= 0:
            raise ValueError(f"dpi must be positive, got {self.dpi!r}")
        if self.crop_marks and not self.bleed:
            raise ValueError("crop_marks are drawn in the bleed, so they need a bleed")

        seen_ids: set[str] = set()
        frame_ids: set[str] = set()
        for item in self.items:
            if item.id in seen_ids:
                raise ValueError(f"duplicate item id: {item.id!r}")
            seen_ids.add(item.id)
            if isinstance(item, FrameSpec):
                frame_ids.add(item.id)

        for item in self.items:
            for_id = getattr(item, "for_", None)
            if for_id and for_id not in frame_ids:
                kind_name = type(item).__name__
                raise ValueError(
                    f"{kind_name} {item.id!r} refers to unknown frame {for_id!r}"
                )
            if (
                isinstance(item, FrameSpec)
                and item.overview_of is not None
                and item.overview_of not in frame_ids
            ):
                raise ValueError(
                    f"frame {item.id!r} overview_of unknown frame {item.overview_of!r}"
                )

        if self.atlas is not None and self.atlas.for_ not in frame_ids:
            raise ValueError(f"atlas refers to unknown frame {self.atlas.for_!r}")
