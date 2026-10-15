"""A cartograph HTML file -> a `CartographSpec`, the first half of the v0.3
round trip (issue #1 section 8: "a cartograph edited in NIKA or by hand comes
back into QGIS as a layout"). `core/layout_builder.py` is the second half.

The input is whatever `writers/cartograph_writer.py` wrote -- the sheet inside
`<template id="sheet">` -- but by the time it comes back it may have been
edited in NIKA or by hand, so the parser is tolerant on purpose:

- `<om-cartograph>` is found wherever it is: inside the template, or placed
  straight in `<body>` the way `docs/cartograph.md`'s own example does.
- **Nothing is dropped silently.** An unknown attribute or element, an item
  that cannot be placed (no box, a number that does not parse, a `for` that
  names no frame), a duplicate id: each one is kept out of the spec *and*
  recorded as a `RoundTripIssue` saying what happened, the same rule
  `core/fidelity_report.py` enforces for the export direction.
- The attribute lists are the runtime's (`onlymapjs.html-data.json`,
  mirrored by `core/cartograph_ir.py`), so "unknown" means unknown to the
  runtime, not merely to this plugin.

Two things the parser reads but does not carry into the spec, by design:

- A live frame's embedded `<om-map>` -- the layer data the writer inlined.
  A layout built back in QGIS draws the project's own layers, so the copy in
  the file is reported once per frame and skipped.
- `<head>`, the bootstrap `<script>` and anything else outside
  `<om-cartograph>` -- page chrome the writer regenerates on every save.

Pure Python: stdlib `html.parser`, no `qgis`, no third-party parser.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote_to_bytes

from .cartograph_ir import (
    AtlasSpec,
    Box,
    CartographItem,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    ImageSpec,
    LegendRowSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)


class CartographImportError(ValueError):
    """The file is not a cartograph at all (no `<om-cartograph>`), or could
    not be read. Everything short of that becomes a `RoundTripIssue`."""


@dataclass(frozen=True)
class RoundTripIssue:
    """One thing that did not come back exactly: `subject` names the element
    (`<om-text id="title">`), `detail` says what happened to it, in plain
    English -- the same subject/detail split a `FidelityItem` uses, so a
    caller can show both in one list."""

    subject: str
    detail: str

    def __str__(self) -> str:
        return f"{self.subject}: {self.detail}"


@dataclass(frozen=True)
class CartographImport:
    """The parsed spec and every issue met on the way."""

    spec: CartographSpec
    issues: tuple[RoundTripIssue, ...] = ()


# --------------------------------------------------------------------------
# Vocabulary (onlymapjs.html-data.json for the pinned runtime)
# --------------------------------------------------------------------------

_PLACEMENT = frozenset(["id", "x", "y", "w", "h", "rotation", "z", "locked", "hidden"])
#: `style` is the inline mm copy of the placement attributes the writer adds
#: for no-JavaScript rendering (non-negotiable 2 in the writer). Attributes
#: are the source of truth, so it is read past without a report.
_IGNORED_EVERYWHERE = frozenset({"style"})

_ROOT_ATTRS = frozenset(
    [
        "cartograph-id",
        "format",
        "size",
        "width",
        "height",
        "orientation",
        "theme",
        "bg",
        "color",
        "font",
        "title",
        "attribution",
        "notes",
        "cvd",
        "bleed",
        "crop-marks",
        "safe-zone",
        "dpi",
        "flatten",
        "color-profile",
        "allow-url-actions",
    ]
)
#: Root attributes the runtime knows that a `CartographSpec` has no field
#: for -- reported as not carried, rather than as unknown.
_ROOT_NOT_CARRIED = {
    "keep-badge": "the badge setting has no QGIS layout equivalent and was not kept.",
}

_ELEMENT_ATTRS: Mapping[str, frozenset[str]] = {
    "om-frame": _PLACEMENT
    | set(
        [
            "src",
            "mode",
            "center",
            "zoom",
            "bearing",
            "pitch",
            "dpi",
            "crs",
            "corners",
            "attribution",
            "crs-def",
            "overview-of",
            "overview-stroke",
            "atlas-fit",
            "atlas-margin",
            "frame-stroke",
            "frame-bg",
        ]
    ),
    "om-graticule": _PLACEMENT
    | set(
        [
            "for",
            "crs",
            "crs-def",
            "interval",
            "interval-x",
            "interval-y",
            "kind",
            "labels",
            "format",
            "color",
        ]
    ),
    "om-legend": _PLACEMENT
    | set(
        [
            "for",
            "title",
            "columns",
            "derived",
            "bg",
            "color",
            "stroke",
            "font-size",
            "padding",
        ]
    ),
    "om-legend-row": frozenset(
        ["layer", "match", "label", "hide", "order", "color", "shape"]
    ),
    "om-scalebar": _PLACEMENT
    | set(["for", "units", "segments", "segment-length", "kind", "color"]),
    "om-north": _PLACEMENT | {"for", "kind", "color"},
    "om-text": _PLACEMENT
    | set(["kind", "font-size", "weight", "align", "font", "color", "bg"]),
    "om-shape": _PLACEMENT
    | set(["kind", "fill", "fill-opacity", "stroke", "stroke-width", "radius"]),
    "om-image": _PLACEMENT | {"src", "fit"},
    "om-atlas": frozenset(
        ["id", "for", "layer", "filter", "sort", "filename", "page-name", "pager"]
    ),
}

#: The id prefix for an item whose markup carried none.
_DEFAULT_ID_PREFIX = {
    "om-frame": "frame",
    "om-graticule": "graticule",
    "om-legend": "legend",
    "om-scalebar": "scalebar",
    "om-north": "north",
    "om-text": "text",
    "om-shape": "shape",
    "om-image": "image",
}

#: HTML void elements: they never get an end tag, so they must never be
#: pushed on the open-element stack.
_VOID_ELEMENTS = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    ]
)

#: How every page-level issue names the root element.
_ROOT_SUBJECT = "<om-cartograph>"

_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_DATA_URI_RE = re.compile(r"^data:([^;,]*)(;base64)?,(.*)$", re.DOTALL)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def read_cartograph_file(path: Path) -> CartographImport:
    """Read and parse the cartograph at `path`.

    Raises `CartographImportError` when the file cannot be read or is not
    UTF-8 (the writer always writes UTF-8) and when it holds no cartograph.
    """
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CartographImportError(f"could not read {path}: {error}") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CartographImportError(f"{path} is not UTF-8 text: {error}") from error
    return parse_cartograph_html(text)


def parse_cartograph_html(text: str) -> CartographImport:
    """Parse cartograph HTML into a spec plus the issues met on the way.

    Raises `CartographImportError` only when there is no `<om-cartograph>`.
    """
    parser = _TreeParser()
    parser.feed(text)
    parser.close()
    if not parser.roots:
        raise CartographImportError("no <om-cartograph> element found in the file")
    issues = _Issues()
    if len(parser.roots) > 1:
        issues.add(
            _ROOT_SUBJECT,
            f"The file holds {len(parser.roots)} cartographs; only the first was "
            "imported, the second and later ones were ignored.",
        )
    return _Converter(issues).convert(parser.roots[0])


# --------------------------------------------------------------------------
# HTML -> a small element tree (only inside <om-cartograph>)
# --------------------------------------------------------------------------


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    duplicate_attrs: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    def all_text(self) -> str:
        """Own text plus every descendant's, in document order."""
        return "".join(self.text)


class _TreeParser(HTMLParser):
    """Builds `_Node` trees for every `<om-cartograph>`; ignores the rest.

    `<template>` needs no special case: `html.parser` does not know it is
    inert and simply reports the tags inside it like any others.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[_Node] = []
        self._stack: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._stack and tag != "om-cartograph":
            return
        node = _Node(tag=tag, attrs={})
        for name, value in attrs:
            if name in node.attrs:
                node.duplicate_attrs.append(name)
            else:
                node.attrs[name] = value
        if self._stack:
            self._stack[-1].children.append(node)
        else:
            self.roots.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS and self._stack and self._stack[-1].tag == tag:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        # Pop back to the matching open element; an end tag with no match
        # (hand-edited markup) is ignored rather than unwinding everything.
        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        # Text belongs to every open element, so an `<om-text>` with stray
        # inline markup still keeps the words inside it.
        for node in self._stack:
            node.text.append(data)


# --------------------------------------------------------------------------
# Element tree -> CartographSpec
# --------------------------------------------------------------------------


class _Issues:
    def __init__(self) -> None:
        self.items: list[RoundTripIssue] = []

    def add(self, subject: str, detail: str) -> None:
        self.items.append(RoundTripIssue(subject, detail))


class _ElementSkippedError(Exception):
    """One element cannot be imported; the reason is already recorded."""


def _subject(node: _Node) -> str:
    element_id = node.attrs.get("id")
    return f'<{node.tag} id="{element_id}">' if element_id else f"<{node.tag}>"


class _Attrs:
    """Typed attribute access for one element; bad values become issues."""

    def __init__(self, node: _Node, issues: _Issues, subject: str) -> None:
        self.node = node
        self.issues = issues
        self.subject = subject

    def text(self, name: str) -> str | None:
        value = self.node.attrs.get(name)
        return value if value not in (None, "") else None

    def flag(self, name: str) -> bool:
        """An HTML boolean: present (valueless, empty or anything but
        "false") is true, absent is false."""
        if name not in self.node.attrs:
            return False
        value = self.node.attrs[name]
        return value is None or value.strip().lower() != "false"

    def number(self, name: str, *, required: bool = False) -> float | None:
        raw = self.text(name)
        if raw is None:
            if required:
                self.issues.add(
                    self.subject,
                    f"Attribute '{name}' is missing, so the element cannot be "
                    "placed and was left out.",
                )
                raise _ElementSkippedError
            return None
        try:
            value = float(raw)
        except ValueError:
            value = float("nan")
        if value != value or value in (float("inf"), float("-inf")):
            self.issues.add(
                self.subject,
                f"Attribute {name}={raw!r} is not a number"
                + (", so the element was left out." if required else "; ignored."),
            )
            if required:
                raise _ElementSkippedError
            return None
        return value

    def integer(self, name: str) -> int | None:
        value = self.number(name)
        if value is None:
            return None
        if value != int(value):
            self.issues.add(
                self.subject,
                f"Attribute {name}={value!r} must be a whole number; rounded.",
            )
        return round(value)

    def json_value(self, name: str) -> object:
        raw = self.text(name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            self.issues.add(
                self.subject, f"Attribute {name}={raw!r} is not valid JSON; ignored."
            )
            return None

    def lonlat(self, name: str) -> tuple[float, float] | None:
        value = self.json_value(name)
        if value is None:
            return None
        point = _as_lonlat(value)
        if point is None:
            self.issues.add(
                self.subject,
                f"Attribute '{name}' must be [lon, lat]; ignored.",
            )
        return point

    def corners(self) -> tuple[tuple[float, float], ...] | None:
        value = self.json_value("corners")
        if value is None:
            return None
        points = (
            [_as_lonlat(point) for point in value] if isinstance(value, list) else []
        )
        if len(points) != 4 or any(point is None for point in points):
            self.issues.add(
                self.subject,
                "Attribute 'corners' must be four [lon, lat] points; ignored.",
            )
            return None
        return tuple(point for point in points if point is not None)


def _as_lonlat(value: object) -> tuple[float, float] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    ):
        return float(value[0]), float(value[1])
    return None


class _Placement(TypedDict):
    rotation: float
    z: int | None
    locked: bool
    hidden: bool


class _FrameShared(TypedDict):
    dpi: float | None
    overview_of: str | None
    overview_stroke: str | None
    frame_stroke: str | None
    frame_bg: str | None
    attribution: str | None
    atlas_fit: str | None
    atlas_margin: float | None


class _Converter:
    def __init__(self, issues: _Issues) -> None:
        self.issues = issues
        self.used_ids: set[str] = set()
        self.id_counters: dict[str, int] = {}

    # -- root -------------------------------------------------------------

    def convert(self, root: _Node) -> CartographImport:
        subject = _ROOT_SUBJECT
        self._report_attributes(
            root, _ROOT_ATTRS, subject, not_carried=_ROOT_NOT_CARRIED
        )
        attrs = _Attrs(root, self.issues, subject)

        page_format = attrs.text("format")
        if page_format is not None and page_format != "cartograph/2":
            self.issues.add(
                subject,
                f"format={page_format!r} is not cartograph/2; read as cartograph/2.",
            )

        title = attrs.text("title") or ""
        cartograph_id = attrs.text("cartograph-id")
        if cartograph_id is None:
            cartograph_id = _ID_SANITIZE_RE.sub("-", title).strip("-") or "cartograph"
            self.issues.add(
                subject,
                f"No cartograph-id; using {cartograph_id!r}.",
            )

        size = attrs.text("size")
        width = attrs.number("width")
        height = attrs.number("height")
        if size is not None and (width is not None or height is not None):
            self.issues.add(
                subject,
                f"Both size={size!r} and width/height are set; the size preset was "
                "kept and width/height ignored.",
            )
            width = height = None
        if size is None and (width is None or height is None):
            self.issues.add(
                subject, "No page size (size or width and height); using A4."
            )
            size, width, height = "A4", None, None

        items = self._items(root)
        frame_ids = {item.id for item in items if isinstance(item, FrameSpec)}
        items = self._resolve_references(items, frame_ids)
        atlas = self._atlas(
            root, {item.id for item in items if isinstance(item, FrameSpec)}
        )

        root_values: dict[str, object] = {
            "cartograph_id": cartograph_id,
            "title": title,
            "attribution": attrs.text("attribution") or "",
            "theme": attrs.text("theme"),
            "size": size,
            "width": width,
            "height": height,
            "orientation": attrs.text("orientation"),
            "bg": attrs.text("bg"),
            "color": attrs.text("color"),
            "font": attrs.text("font"),
            "notes": attrs.text("notes"),
            "bleed": attrs.number("bleed"),
            "crop_marks": attrs.flag("crop-marks"),
            "safe_zone": attrs.number("safe-zone"),
            "dpi": attrs.integer("dpi"),
            "flatten": attrs.flag("flatten"),
            "color_profile": attrs.text("color-profile"),
            "cvd": attrs.text("cvd"),
        }
        spec = _construct_root(root_values, items, atlas, self.issues, subject)
        return CartographImport(spec=spec, issues=tuple(self.issues.items))

    # -- children ---------------------------------------------------------

    def _items(self, root: _Node) -> list[CartographItem]:
        builders: Mapping[str, Callable[[_Node, _Attrs, str], CartographItem]] = {
            "om-frame": self._frame,
            "om-graticule": self._graticule,
            "om-legend": self._legend,
            "om-scalebar": self._scalebar,
            "om-north": self._north,
            "om-text": self._text,
            "om-shape": self._shape,
            "om-image": self._image,
        }
        items: list[CartographItem] = []
        for node in root.children:
            if node.tag == "om-atlas":
                continue  # read once every frame is known, by _atlas
            builder = builders.get(node.tag)
            if builder is None:
                self.issues.add(
                    _subject(node),
                    f"<{node.tag}> is not a cartograph element; left out.",
                )
                continue
            self._report_attributes(node, _ELEMENT_ATTRS[node.tag], _subject(node))
            try:
                item_id = self._item_id(node)
                subject = f'<{node.tag} id="{item_id}">'
                item = builder(node, _Attrs(node, self.issues, subject), item_id)
            except _ElementSkippedError:
                continue
            except ValueError as error:  # a cartograph_ir __post_init__ rule
                self.issues.add(_subject(node), f"Left out: {error}.")
                continue
            items.append(item)
        return items

    def _item_id(self, node: _Node) -> str:
        wanted = node.attrs.get("id") or ""
        if not wanted:
            prefix = _DEFAULT_ID_PREFIX[node.tag]
            count = self.id_counters.get(prefix, 0)
            while True:
                count += 1
                candidate = f"{prefix}-{count}"
                if candidate not in self.used_ids:
                    break
            self.id_counters[prefix] = count
            self.issues.add(_subject(node), f"No id; using {candidate!r}.")
            self.used_ids.add(candidate)
            return candidate
        if wanted not in self.used_ids:
            self.used_ids.add(wanted)
            return wanted
        suffix = 2
        while f"{wanted}-{suffix}" in self.used_ids:
            suffix += 1
        renamed = f"{wanted}-{suffix}"
        self.issues.add(
            _subject(node), f"The id {wanted!r} is used twice; renamed to {renamed!r}."
        )
        self.used_ids.add(renamed)
        return renamed

    def _report_attributes(
        self,
        node: _Node,
        known: frozenset[str],
        subject: str,
        *,
        not_carried: Mapping[str, str] | None = None,
    ) -> None:
        not_carried = not_carried or {}
        for name in node.duplicate_attrs:
            self.issues.add(
                subject, f"Attribute '{name}' appears twice; the first one was used."
            )
        for name in node.attrs:
            if name in known or name in _IGNORED_EVERYWHERE:
                continue
            if name in not_carried:
                self.issues.add(subject, f"Attribute '{name}': {not_carried[name]}")
            else:
                self.issues.add(
                    subject,
                    f"Attribute '{name}' is not part of the cartograph format; "
                    "ignored.",
                )

    def _report_children(
        self, node: _Node, subject: str, allowed: frozenset[str]
    ) -> None:
        for child in node.children:
            if child.tag not in allowed:
                self.issues.add(
                    subject,
                    f"<{child.tag}> inside <{node.tag}> is not supported; ignored.",
                )

    @staticmethod
    def _box(attrs: _Attrs) -> Box:
        values = [attrs.number(name, required=True) for name in ("x", "y", "w", "h")]
        x, y, w, h = (value if value is not None else 0.0 for value in values)
        # `required=True` raises rather than return None, so the fallback is
        # never used; it only gives the type checker floats.
        return Box(x, y, w, h)

    @staticmethod
    def _placement(attrs: _Attrs) -> _Placement:
        return {
            "rotation": attrs.number("rotation") or 0.0,
            "z": attrs.integer("z"),
            "locked": attrs.flag("locked"),
            "hidden": attrs.flag("hidden"),
        }

    # -- each element ------------------------------------------------------

    def _frame(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        box = self._box(attrs)
        mode = attrs.text("mode") or "live"
        shared: _FrameShared = {
            "dpi": attrs.number("dpi"),
            "overview_of": attrs.text("overview-of"),
            "overview_stroke": attrs.text("overview-stroke"),
            "frame_stroke": attrs.text("frame-stroke"),
            "frame_bg": attrs.text("frame-bg"),
            "attribution": attrs.text("attribution"),
            "atlas_fit": attrs.text("atlas-fit"),
            "atlas_margin": attrs.number("atlas-margin"),
        }
        if mode == "static":
            self._report_children(node, attrs.subject, frozenset({"img"}))
            image_bytes, image_path = self._static_image(node, attrs)
            return FrameSpec(
                id=item_id,
                box=box,
                mode="static",
                crs=attrs.text("crs"),
                crs_def=attrs.text("crs-def"),
                corners=attrs.corners(),  # type: ignore[arg-type]  # length 4, checked
                image_bytes=image_bytes,
                image_path=image_path,
                **self._placement(attrs),
                **shared,
            )
        for static_only in ("crs", "crs-def", "corners"):
            if attrs.text(static_only) is not None:
                self.issues.add(
                    attrs.subject,
                    f"Attribute '{static_only}' only applies to a static frame; "
                    "ignored on this live frame.",
                )
        self._report_children(node, attrs.subject, frozenset({"om-map"}))
        embedded_map = next(
            (child for child in node.children if child.tag == "om-map"), None
        )
        if embedded_map is not None:
            self.issues.add(
                attrs.subject,
                "The map embedded in this live frame was not imported; in QGIS "
                "the frame draws the project's own layers.",
            )
        return FrameSpec(
            id=item_id,
            box=box,
            mode=mode,
            src=attrs.text("src"),
            center=attrs.lonlat("center"),
            zoom=attrs.number("zoom"),
            bearing=attrs.number("bearing") or 0.0,
            pitch=attrs.number("pitch") or 0.0,
            plane_crs=_blank_to_none(embedded_map, "crs"),
            plane_crs_def=_blank_to_none(embedded_map, "crs-def"),
            **self._placement(attrs),
            **shared,
        )

    def _static_image(
        self, node: _Node, attrs: _Attrs
    ) -> tuple[bytes | None, str | None]:
        image = next((child for child in node.children if child.tag == "img"), None)
        src = image.attrs.get("src") if image is not None else None
        if not src:
            self.issues.add(
                attrs.subject,
                "This static frame has no <img> to show, so it was left out.",
            )
            raise _ElementSkippedError
        if not src.startswith("data:"):
            return None, src
        decoded = _decode_data_uri(src)
        if decoded is None:
            self.issues.add(
                attrs.subject,
                "The frame's embedded image could not be decoded, so the frame "
                "was left out.",
            )
            raise _ElementSkippedError
        return decoded, None

    def _graticule(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset())
        return GraticuleSpec(
            id=item_id,
            box=self._box(attrs),
            for_=attrs.text("for") or "",
            crs=attrs.text("crs"),
            crs_def=attrs.text("crs-def"),
            interval=attrs.number("interval"),
            interval_x=attrs.number("interval-x"),
            interval_y=attrs.number("interval-y"),
            kind=attrs.text("kind") or "solid",
            labels=attrs.text("labels") or "outside",
            format=attrs.text("format"),
            color=attrs.text("color"),
            **self._placement(attrs),
        )

    def _legend(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset({"om-legend-row"}))
        rows = []
        for child in node.children:
            if child.tag != "om-legend-row":
                continue
            row_subject = f"{attrs.subject} row"
            self._report_attributes(child, _ELEMENT_ATTRS["om-legend-row"], row_subject)
            row = _Attrs(child, self.issues, row_subject)
            rows.append(
                LegendRowSpec(
                    layer=row.text("layer"),
                    match=row.text("match"),
                    label=row.text("label"),
                    hide=row.flag("hide"),
                    order=row.integer("order"),
                    color=row.text("color"),
                    shape=row.text("shape"),
                )
            )
        derived_raw = attrs.text("derived")
        return LegendSpec(
            id=item_id,
            box=self._box(attrs),
            for_=attrs.text("for") or "",
            title=attrs.text("title"),
            columns=attrs.integer("columns"),
            derived=derived_raw is None or derived_raw.strip().lower() != "false",
            bg=attrs.text("bg"),
            color=attrs.text("color"),
            stroke=attrs.text("stroke"),
            font_size=attrs.number("font-size"),
            padding=attrs.number("padding"),
            rows=tuple(rows),
            **self._placement(attrs),
        )

    def _scalebar(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset())
        return ScalebarSpec(
            id=item_id,
            box=self._box(attrs),
            for_=attrs.text("for") or "",
            units=attrs.text("units") or "metric",
            segments=attrs.integer("segments"),
            segment_length=attrs.number("segment-length"),
            kind=attrs.text("kind") or "single",
            color=attrs.text("color"),
            **self._placement(attrs),
        )

    def _north(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset())
        return NorthSpec(
            id=item_id,
            box=self._box(attrs),
            for_=attrs.text("for") or "",
            kind=attrs.text("kind") or "arrow",
            color=attrs.text("color"),
            **self._placement(attrs),
        )

    def _text(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        for child in node.children:
            self.issues.add(
                attrs.subject,
                f"Markup <{child.tag}> inside the text is not supported; only its "
                "words were kept.",
            )
        return TextSpec(
            id=item_id,
            box=self._box(attrs),
            body=node.all_text().strip(),
            kind=attrs.text("kind") or "text",
            font_size=attrs.number("font-size"),
            weight=attrs.text("weight"),
            align=attrs.text("align"),
            font=attrs.text("font"),
            color=attrs.text("color"),
            bg=attrs.text("bg"),
            **self._placement(attrs),
        )

    def _shape(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset())
        return ShapeSpec(
            id=item_id,
            box=self._box(attrs),
            kind=attrs.text("kind") or "rect",
            fill=attrs.text("fill"),
            fill_opacity=attrs.number("fill-opacity"),
            stroke=attrs.text("stroke"),
            stroke_width=attrs.number("stroke-width"),
            radius=attrs.number("radius"),
            **self._placement(attrs),
        )

    def _image(self, node: _Node, attrs: _Attrs, item_id: str) -> CartographItem:
        self._report_children(node, attrs.subject, frozenset())
        return ImageSpec(
            id=item_id,
            box=self._box(attrs),
            src=attrs.text("src") or "",
            fit=attrs.text("fit") or "contain",
            **self._placement(attrs),
        )

    # -- cross-references ---------------------------------------------------

    def _resolve_references(
        self, items: list[CartographItem], frame_ids: set[str]
    ) -> list[CartographItem]:
        """Drop furniture whose `for` names no frame, and clear an
        `overview-of` that names none -- `CartographSpec` would refuse
        either, failing the whole import over one stale reference."""
        resolved: list[CartographItem] = []
        for item in items:
            for_id = getattr(item, "for_", "")
            kind = type(item).__name__.removesuffix("Spec").lower()
            subject = f'<om-{kind} id="{item.id}">'
            if for_id not in ("", None) and for_id not in frame_ids:
                self.issues.add(
                    subject,
                    f"It belongs to frame {for_id!r}, which is not in the file; "
                    "left out.",
                )
                continue
            if (
                isinstance(item, FrameSpec)
                and item.overview_of is not None
                and item.overview_of not in frame_ids
            ):
                self.issues.add(
                    f'<om-frame id="{item.id}">',
                    f"overview-of names frame {item.overview_of!r}, which is "
                    "not in the file; the overview was dropped.",
                )
                item = replace(item, overview_of=None)
            resolved.append(item)
        return resolved

    def _atlas(self, root: _Node, frame_ids: set[str]) -> AtlasSpec | None:
        nodes = [node for node in root.children if node.tag == "om-atlas"]
        if not nodes:
            return None
        if len(nodes) > 1:
            self.issues.add(
                "<om-atlas>",
                "More than one atlas; only the first was imported.",
            )
        node = nodes[0]
        subject = _subject(node)
        self._report_attributes(node, _ELEMENT_ATTRS["om-atlas"], subject)
        attrs = _Attrs(node, self.issues, subject)
        for_id = attrs.text("for") or ""
        if for_id not in frame_ids:
            self.issues.add(
                subject,
                f"The atlas drives frame {for_id!r}, which is not in the file; "
                "left out.",
            )
            return None
        try:
            return AtlasSpec(
                for_=for_id,
                layer=attrs.text("layer") or "",
                id=attrs.text("id"),
                filter=attrs.text("filter"),
                sort=attrs.text("sort"),
                filename=attrs.text("filename"),
                page_name=attrs.text("page-name"),
                pager=attrs.text("pager"),
            )
        except ValueError as error:
            self.issues.add(subject, f"Left out: {error}.")
            return None


def _construct_root(
    values: dict[str, object],
    items: list[CartographItem],
    atlas: AtlasSpec | None,
    issues: _Issues,
    subject: str,
) -> CartographSpec:
    """Build the root, dropping one bad page-level value at a time.

    A root-level value the IR refuses (an unknown theme, `crop-marks` with no
    bleed) must not cost the user the whole sheet, so the offending field is
    reset to its default, reported, and the construction retried.
    """
    defaults: dict[str, object] = {
        "theme": None,
        "orientation": None,
        "cvd": None,
        "bleed": None,
        "safe_zone": None,
        "dpi": None,
        "crop_marks": False,
    }
    for _ in range(len(defaults) + 1):
        try:
            return CartographSpec(
                items=tuple(items),
                atlas=atlas,
                **values,  # type: ignore[arg-type]  # keys are CartographSpec fields
            )
        except ValueError as error:
            message = str(error)
            culprit = next(
                (
                    name
                    for name in defaults
                    if name in message and values.get(name) != defaults[name]
                ),
                None,
            )
            if culprit is None:
                raise CartographImportError(
                    f"invalid cartograph page: {message}"
                ) from error
            issues.add(subject, f"{message}; that setting was ignored.")
            values[culprit] = defaults[culprit]
    raise CartographImportError("invalid cartograph page")  # pragma: no cover


def _decode_data_uri(src: str) -> bytes | None:
    match = _DATA_URI_RE.match(src)
    if match is None:
        return None
    payload = match.group(3)
    if match.group(2):
        try:
            return base64.b64decode(payload, validate=False)
        except (binascii.Error, ValueError):
            return None
    return unquote_to_bytes(payload)


def _blank_to_none(node: _Node | None, name: str) -> str | None:
    """`node`'s attribute `name`, or None when the node or the value is absent
    or blank."""
    if node is None:
        return None
    value = (node.attrs.get(name) or "").strip()
    return value or None
