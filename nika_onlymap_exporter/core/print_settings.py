"""Print settings for a cartograph (issue #5): the model, its arithmetic, JSON.

This module is **pure Python**: no `qgis` import, no Qt, so the size and bleed
arithmetic and the preset (de)serialisation are unit-tested where neither is
installed. The dialog (`ui/cartograph/print_settings_dialog.py`) edits a
`PrintSettings`; the two export paths consume it:

- **Path A, the browser** (RGB). `apply_to_spec` maps the settings onto the
  `<om-cartograph>` root's print-shop attributes, and the cartograph is opened
  with `?print=1`. Nothing else here is needed.
- **Path B, QGIS** (CMYK / ICC). `exporters/print_pdf.py` reads the same
  settings and drives `QgsLayoutExporter` on a copy of the source layout.

**The runtime's rules win** (runtime docs, "Print-shop attributes"):

- Crop marks are drawn *in the bleed*, so they need one. A `PrintSettings`
  keeps the user's crop-marks preference even with no bleed -- choosing a
  bleed again brings the marks back -- but `crop_marks_effective` is False
  then, and neither path draws them. `CartographSpec` would reject the
  combination anyway, and the runtime's validator warns about it.
- The safe zone is a screen-only guide, never printed or exported. Path A
  writes it (the runtime hides it on paper); Path B ignores it.
- `color-profile` is an informational tag; the runtime never converts colour.
  The cartograph's colours are always sRGB, whichever profile the QGIS path
  will later print with, so the tag always says so.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields, replace
from typing import TYPE_CHECKING, TypeGuard

from .cartograph_ir import ORIENTATIONS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cartograph_ir import CartographSpec

MM_PER_INCH = 25.4

#: Page presets in PORTRAIT millimetres -- copied from the runtime's
#: `PAGE_PRESETS` (`src/cartograph/layout.ts`) so a preset prints at exactly
#: the size the browser will lay out. Landscape swaps them, as there.
PAGE_PRESETS_MM: dict[str, tuple[float, float]] = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}

#: `PrintSettings.page` values that are not a preset name.
PAGE_FROM_LAYOUT = "layout"
PAGE_CUSTOM = "custom"
PAGE_CHOICES = (PAGE_FROM_LAYOUT, *PAGE_PRESETS_MM, PAGE_CUSTOM)

#: Custom page bounds. The lower bound keeps a typo from producing a page a
#: few hundredths of a millimetre wide; the upper is past A0's long side with
#: room for banners, and well inside what a PDF page box may hold (5080 mm).
CUSTOM_MIN_MM = 10.0
CUSTOM_MAX_MM = 5000.0

RESOLUTIONS = (150, 300, 600)

#: Bleed choices by key. A key rather than the millimetre value is stored so
#: 1/8 in survives a round trip as "1/8 in", not as an anonymous 3.175.
BLEED_MM: dict[str, float | None] = {
    "none": None,
    "3mm": 3.0,
    "5mm": 5.0,
    "eighth_inch": MM_PER_INCH / 8,
}

PROFILE_RGB = "rgb"
PROFILE_CMYK = "cmyk"
PROFILE_ICC = "icc"
PROFILES = (PROFILE_RGB, PROFILE_CMYK, PROFILE_ICC)

#: How far inside the trim the safe-zone guide sits. The dialog offers the
#: guide as on/off only (the issue's mockup has no width field); 5 mm is the
#: inset most print shops quote for "keep text inside here", and a guide is
#: only ever advice -- it is never printed.
DEFAULT_SAFE_ZONE_MM = 5.0

#: What `color-profile` says on a cartograph -- see the module docstring.
CARTOGRAPH_COLOR_PROFILE = "sRGB"

#: Qt release that first shipped `QPdfWriter` CMYK and output intents, which
#: QGIS 3.40's colour-managed PDF export is built on.
MIN_QT_FOR_COLOUR_MANAGEMENT = (6, 8)

#: Version of the JSON this module writes. Read back leniently either way.
JSON_VERSION = 1

Size = tuple[float, float]
#: One crop-mark stroke: x1, y1, x2, y2 in millimetres from the trim corner.
Segment = tuple[float, float, float, float]


@dataclass(frozen=True)
class PrintSettings:
    """Everything the Print settings dialog chooses.

    `page` is `PAGE_FROM_LAYOUT`, a `PAGE_PRESETS_MM` name, or `PAGE_CUSTOM`.
    `orientation` applies to presets only, as in the runtime: a custom size
    is already oriented and the layout's own page is used as it is.
    """

    page: str = PAGE_FROM_LAYOUT
    orientation: str = "portrait"
    custom_width_mm: float = 210.0
    custom_height_mm: float = 297.0
    dpi: int = 300
    bleed: str = "3mm"
    crop_marks: bool = True
    safe_zone: bool = True
    profile: str = PROFILE_RGB
    icc_path: str = ""
    flatten: bool = False
    embed_fonts: bool = True

    def __post_init__(self) -> None:
        for spec_field in fields(self):
            _check_field(spec_field.name, getattr(self, spec_field.name))
        if self.profile == PROFILE_ICC and not self.icc_path:
            raise ValueError("profile='icc' needs an icc_path")

    @property
    def bleed_mm(self) -> float:
        """The bleed in millimetres, 0 for none."""
        return BLEED_MM[self.bleed] or 0.0

    @property
    def crop_marks_effective(self) -> bool:
        """Whether marks are actually drawn: they need a bleed to sit in."""
        return self.crop_marks and self.bleed_mm > 0

    @property
    def uses_qgis_exporter(self) -> bool:
        """CMYK and ICC go through QGIS (Path B); RGB through the browser."""
        return self.profile != PROFILE_RGB

    def trim_size_mm(self, layout_page_mm: Size) -> Size:
        """The finished (trimmed) page size, given the layout's own page."""
        if self.page == PAGE_FROM_LAYOUT:
            return layout_page_mm
        if self.page == PAGE_CUSTOM:
            return (self.custom_width_mm, self.custom_height_mm)
        return preset_size_mm(self.page, self.orientation)

    def sheet_size_mm(self, layout_page_mm: Size) -> Size:
        """The printed sheet: the trim plus the bleed on every side.

        A3 portrait with a 3 mm bleed is 303 x 426 mm.
        """
        width, height = self.trim_size_mm(layout_page_mm)
        bleed = self.bleed_mm
        return (width + 2 * bleed, height + 2 * bleed)

    def to_json(self) -> str:
        """Serialise for a project entry or a `QSettings` value."""
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"version": JSON_VERSION}
        data.update(asdict(self))
        return data

    @classmethod
    def from_json(cls, text: str) -> PrintSettings:
        """Read settings written by `to_json`.

        Raises `ValueError` when `text` is not a JSON object at all -- there
        is nothing to recover. A field that is missing, of the wrong type or
        out of range is *repaired* to its default instead (see `from_dict`),
        so one bad value from a hand-edited project or an older plugin never
        costs the user every other choice they saved.
        """
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"print settings are not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("print settings must be a JSON object")
        return cls.from_dict(data)[0]

    @classmethod
    def from_dict(
        cls, data: dict[str, object]
    ) -> tuple[PrintSettings, tuple[str, ...]]:
        """Build settings from a decoded object, repairing what is wrong.

        Returns the settings and the names of the fields that were repaired.
        Unknown keys are ignored (a newer plugin may have added some).
        """
        defaults = cls()
        values: dict[str, object] = {}
        repaired: list[str] = []
        for spec_field in fields(cls):
            name = spec_field.name
            if name not in data:
                continue
            value = _coerce(data[name], getattr(defaults, name))
            if value is None:
                repaired.append(name)
                continue
            try:
                _check_field(name, value)
            except ValueError:
                repaired.append(name)
                continue
            values[name] = value
        # `values` holds only names from `fields(cls)`, each checked by
        # `_check_field` for its field's type; mypy cannot follow that.
        try:
            return cls(**values), tuple(repaired)  # type: ignore[arg-type]
        except ValueError:
            # Fields valid alone can still clash -- profile "icc" with the
            # path repaired away. Falling back to RGB is the honest repair:
            # the browser path needs nothing that could be missing.
            values["profile"] = PROFILE_RGB
            repaired.append("profile")
            return cls(**values), tuple(repaired)  # type: ignore[arg-type]


def preset_size_mm(name: str, orientation: str) -> Size:
    """A preset's size in mm, swapped for landscape like the runtime does."""
    width, height = PAGE_PRESETS_MM[name]
    return (height, width) if orientation == "landscape" else (width, height)


def apply_to_spec(spec: CartographSpec, settings: PrintSettings) -> CartographSpec:
    """`spec` with the settings written onto its root print attributes.

    The page size override replaces the page box only: placed items keep
    their millimetre positions from the trim corner, exactly as the runtime
    measures them, so a smaller page than the layout's clips what falls off
    it. That is the user's explicit choice in the dialog, not something to
    second-guess here.
    """
    size, width, height = spec.size, spec.width, spec.height
    orientation = spec.orientation
    if settings.page == PAGE_CUSTOM:
        size, width, height = None, settings.custom_width_mm, settings.custom_height_mm
        # Same rule as `core/layout_reader.py`, so a custom page and a layout
        # page of one shape say the same orientation.
        orientation = "landscape" if width >= height else "portrait"
    elif settings.page != PAGE_FROM_LAYOUT:
        size, width, height = settings.page, None, None
        orientation = settings.orientation
    return replace(
        spec,
        size=size,
        width=width,
        height=height,
        orientation=orientation,
        bleed=settings.bleed_mm or None,
        crop_marks=settings.crop_marks_effective,
        safe_zone=DEFAULT_SAFE_ZONE_MM if settings.safe_zone else None,
        dpi=settings.dpi,
        flatten=settings.flatten,
        color_profile=CARTOGRAPH_COLOR_PROFILE,
    )


def crop_mark_segments(
    trim_width: float, trim_height: float, bleed: float
) -> tuple[Segment, ...]:
    """The eight crop-mark strokes, in mm from the trim's top-left corner.

    Mirrors the runtime's `renderPrintMarks` so the QGIS PDF and the browser
    PDF carry the same marks: each corner gets a horizontal and a vertical
    stroke on the trim lines, extended outward, starting a small gap off the
    trim (so a slightly-off cut never shows a mark) and never longer than the
    bleed they sit in. No bleed, no marks.
    """
    if bleed <= 0:
        return ()
    gap = min(bleed * 0.35, 1.5)
    # The runtime draws `gap + min(bleed, 4)` and lets its bleed-sized SVG
    # clip the overshoot; a QGIS item would print it past the sheet edge
    # instead, so the end is clipped here to the same visible stroke.
    end = min(gap + min(bleed, 4.0), bleed)
    segments: list[Segment] = []
    for x in (0.0, trim_width):
        for y in (0.0, trim_height):
            sx = -1.0 if x == 0 else 1.0
            sy = -1.0 if y == 0 else 1.0
            segments.append((x + sx * gap, y, x + sx * end, y))
            segments.append((x, y + sy * gap, x, y + sy * end))
    return tuple(segments)


def parse_version(text: str) -> tuple[int, ...]:
    """'6.8.2' -> (6, 8, 2); stops at the first part that is not a number."""
    parts: list[int] = []
    for part in text.strip().split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(part):
            break
    return tuple(parts)


def qt_supports_colour_management(qt_version: str) -> bool:
    """Whether a Qt of this version can write CMYK PDFs with an output intent."""
    version = parse_version(qt_version)
    return bool(version) and version >= MIN_QT_FOR_COLOUR_MANAGEMENT


# ---- Presets ---------------------------------------------------------------


def presets_to_json(presets: dict[str, PrintSettings]) -> str:
    """Serialise named presets for one `QSettings` value."""
    return json.dumps(
        {
            "version": JSON_VERSION,
            "presets": {name: s.to_dict() for name, s in presets.items()},
        },
        sort_keys=True,
    )


def presets_from_json(text: object) -> dict[str, PrintSettings]:
    """Read presets written by `presets_to_json`. Never raises.

    The store is user-wide `QSettings`, so a corrupt value would otherwise
    break the dialog in every project. Anything that is not the expected
    shape is dropped; a preset with bad fields is repaired field by field
    (`PrintSettings.from_dict`); a preset with an empty name is dropped.
    """
    if not isinstance(text, str) or not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    raw_presets = data.get("presets")
    if not isinstance(raw_presets, dict):
        return {}
    presets: dict[str, PrintSettings] = {}
    for name, raw in raw_presets.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            continue
        presets[name.strip()] = PrintSettings.from_dict(raw)[0]
    return presets


# ---- Helpers ---------------------------------------------------------------


_CHOICES: dict[str, tuple[object, ...]] = {
    "page": PAGE_CHOICES,
    "orientation": ORIENTATIONS,
    "dpi": RESOLUTIONS,
    "bleed": tuple(BLEED_MM),
    "profile": PROFILES,
}


def _check_field(name: str, value: object) -> None:
    """Validate one field on its own; cross-field rules live in the class.

    Separate so `PrintSettings.from_dict` can judge each stored value alone
    and repair just that one.
    """
    if name in _CHOICES:
        if value not in _CHOICES[name] or isinstance(value, bool):
            raise ValueError(f"{name}={value!r} is not one of {_CHOICES[name]}")
    elif name in ("custom_width_mm", "custom_height_mm"):
        if not _is_number(value) or not CUSTOM_MIN_MM <= value <= CUSTOM_MAX_MM:
            raise ValueError(
                f"{name}={value!r} must be between {CUSTOM_MIN_MM} and "
                f"{CUSTOM_MAX_MM} mm"
            )
    elif name in ("crop_marks", "safe_zone", "flatten", "embed_fonts"):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a bool")
    elif name == "icc_path" and not isinstance(value, str):
        raise ValueError("icc_path must be a string")


def _is_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _coerce(value: object, default: object) -> object | None:
    """`value` as the type of `default`, or None when it cannot be.

    JSON has one number type, so an int field accepts 300.0 and a float field
    accepts 300; nothing else is converted -- "300" for a dpi is corrupt data,
    not a near miss.
    """
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, int):
        if _is_number(value) and float(value) == int(value):
            return int(value)
        return None
    if isinstance(default, float):
        return float(value) if _is_number(value) else None
    if isinstance(default, str):
        return value if isinstance(value, str) else None
    return None
