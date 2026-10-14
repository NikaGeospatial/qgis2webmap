"""A `CartographSpec` -> a new `QgsPrintLayout`: the inverse of
`core/layout_reader.py`.

Issue #1 section 8 (v0.3, "round trip"): a cartograph edited in NIKA or by
hand comes back into QGIS as a print layout, and section 4's one-click mode
leaves a layout behind for the user to keep editing. Both build through this
module, so a layout made from the canvas and a layout made from an imported
file are the same kind of thing.

The mapping is the reader's translation table run backwards, item by item --
see each `_build_*` function. Three rules hold throughout:

- **Ids survive.** Every layout item gets `setId(<spec id>)`, so the Items
  panel shows the names the cartograph used and a caller can find any item
  again by the spec's id (`BuiltLayout.items_by_id`). Map grids and
  overviews, which are not layout items, are named after their spec ids.
- **Final geometry, in the order issue #1 found safe.** `setExtent()` resizes
  a map item to the extent's aspect, and `zoomToExtent()` on a fresh item
  yields a NaN scale (issue #1, "What I verified headlessly", caveat 2). So a
  map item gets `setExtent()` first, *then* its box back, then
  `zoomToExtent()` -- which is only safe once an extent exists, and pads the
  extent to the box instead of resizing the box.
- **Nothing is lost silently.** Whatever QGIS cannot hold -- a literal legend
  row, a pager setting, a pitch -- becomes a `RoundTripIssue`, the same type
  `core/cartograph_import.py` reports with, so a caller shows one list.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote_to_bytes

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsFillSymbol,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPicture,
    QgsLayoutItemPolyline,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLineSymbol,
    QgsMapLayer,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsScaleBarSettings,
    QgsSimpleFillSymbolLayer,
    QgsSymbolLayerUtils,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QFile, QPointF, Qt
from qgis.PyQt.QtGui import QColor, QPolygonF

from .canvas_layout import nice_interval, page_dimensions
from .cartograph_import import RoundTripIssue, read_cartograph_file
from .cartograph_ir import (
    AtlasSpec,
    Box,
    CartographItem,
    CartographSpec,
    FrameSpec,
    GraticuleSpec,
    ImageSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from .layout_reader import FrameMode
from .zoom_range import EQUATOR_METRES, WORLD_PIXELS_AT_ZOOM_0

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")
_MM = QgsUnitTypes.LayoutMillimeters

#: Same CSS pixel `core/cartograph_export.py` measures a live camera in.
_CSS_PIXELS_PER_MM = 96.0 / 25.4
_EARTH_RADIUS = EQUATOR_METRES / (2 * math.pi)

#: `<om-graticule kind>` -> QgsLayoutItemMapGrid.GridStyle; the inverse of
#: the reader's `_GRID_KIND_BY_STYLE` (minus its zebra approximation).
_GRID_STYLE_BY_KIND = {
    "solid": QgsLayoutItemMapGrid.Solid,
    "cross": QgsLayoutItemMapGrid.Cross,
    "markers": QgsLayoutItemMapGrid.Markers,
}

#: `<om-scalebar kind>` -> QGIS scale bar style name; the inverse of the
#: reader's `_SCALEBAR_KIND_BY_STYLE`. QGIS has no plain "line" style, so it
#: is the nearest: the line with ticks in the middle.
_SCALEBAR_STYLE_BY_KIND = {
    "single": "Single Box",
    "double": "Double Box",
    "ticks": "Line Ticks Up",
    "line": "Line Ticks Middle",
}

#: QGIS's own default layout north arrow, a Qt resource compiled into QGIS
#: (3.x and 4.x); `_NORTH_FALLBACK_SVG` is resolved through the SVG search
#: paths should a build ever lack it. The rose is QGIS's compass-rose SVG.
_NORTH_ARROW_RESOURCE = ":/images/north_arrows/layout_default_north_arrow.svg"
_NORTH_FALLBACK_SVG = "arrows/NorthArrow_02.svg"
_NORTH_ROSE_SVG = "arrows/NorthArrow_05.svg"

#: How every atlas issue names its element.
_ATLAS_SUBJECT = "<om-atlas>"

_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_DATA_URI_RE = re.compile(r"^data:([^;,]*)((?:;[^;,]*)*),(.*)$", re.DOTALL)
_IMAGE_SUFFIX_BY_MIME = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_HALIGN = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
    "justify": Qt.AlignmentFlag.AlignJustify,
}


@dataclass(frozen=True)
class BuiltLayout:
    """The new layout and what a caller needs to keep working with it.

    `frame_modes` and `frame_dpis` are keyed by map item uuid, exactly what
    `core/cartograph_export.py`'s `prepare_cartograph` takes -- so re-exporting
    the built layout keeps each frame live or static as the spec had it,
    rather than falling back to the CRS default.
    """

    layout: QgsPrintLayout
    issues: tuple[RoundTripIssue, ...]
    items_by_id: Mapping[str, QgsLayoutItem]
    frame_modes: Mapping[str, FrameMode]
    frame_dpis: Mapping[str, int]


def unique_layout_name(project: QgsProject, base: str) -> str:
    """`base`, or `base (2)`, `base (3)`… -- the first name no layout in
    `project` already has."""
    manager = project.layoutManager()
    if manager.layoutByName(base) is None:
        return base
    index = 2
    while manager.layoutByName(f"{base} ({index})") is not None:
        index += 1
    return f"{base} ({index})"


def build_layout(
    spec: CartographSpec,
    project: QgsProject,
    *,
    name: str | None = None,
    frame_layers: Mapping[str, Sequence[QgsMapLayer]] | None = None,
    image_dir: Path | None = None,
    source_dir: Path | None = None,
    add_to_project: bool = False,
) -> BuiltLayout:
    """Build `spec` into a new `QgsPrintLayout` on `project`.

    - `frame_layers` maps a frame id to the layers that frame draws, in draw
      order (top first, as `QgsLayoutItemMap.setLayers` wants). A frame not
      in it follows the project's layer tree -- every visible layer, in tree
      order -- the way a map item QGIS creates itself does.
    - `image_dir`: where `data:` images are written as files. Without it they
      are embedded in the layout as `base64:` picture paths (QGIS >= 3.26).
    - `source_dir`: what a relative image `src` resolves against -- the
      folder of the cartograph file it came from.
    - `add_to_project`: also add it to the project's layout manager, under a
      unique name (`unique_layout_name`).
    """
    builder = _Builder(spec, project, frame_layers or {}, image_dir, source_dir)
    layout = builder.build(name or spec.title or spec.cartograph_id)
    if add_to_project:
        layout.setName(unique_layout_name(project, layout.name()))
        if not project.layoutManager().addLayout(layout):
            raise ValueError(f"could not add layout {layout.name()!r} to the project")
    return BuiltLayout(
        layout=layout,
        issues=tuple(builder.issues),
        items_by_id=dict(builder.items_by_id),
        frame_modes=dict(builder.frame_modes),
        frame_dpis=dict(builder.frame_dpis),
    )


def import_cartograph_file(path: Path, project: QgsProject) -> BuiltLayout:
    """ "Import cartograph as QGIS layout…": parse the file at `path` and
    build it into a new layout added to `project`.

    The layout is named after the cartograph's title (else the file name),
    made unique. Relative image paths resolve against the file's folder;
    embedded (`data:`) images are written to `<name>.images/` beside the file
    -- the sibling of the `<name>.frames/` folder issue #3 keeps frame
    rasters in -- because `core/layout_reader.py` re-exports a picture by
    reading its file, and an embedded `base64:` picture has none. The
    folder is only created when there is an image to write. The result's
    `issues` hold the parser's findings followed by the builder's. Raises
    `CartographImportError` when the file cannot be read or holds no
    cartograph.
    """
    imported = read_cartograph_file(path)
    built = build_layout(
        imported.spec,
        project,
        name=imported.spec.title or path.stem,
        image_dir=path.parent / f"{path.stem}.images",
        source_dir=path.parent,
        add_to_project=True,
    )
    return BuiltLayout(
        layout=built.layout,
        issues=imported.issues + built.issues,
        items_by_id=built.items_by_id,
        frame_modes=built.frame_modes,
        frame_dpis=built.frame_dpis,
    )


def label_text_from_tokens(
    body: str,
    *,
    reference_frame_id: str | None,
    frame_ids: Sequence[str],
    title: str,
    title_is_project_title: bool,
    attribution: str,
    notes: str,
) -> tuple[str, tuple[str, ...]]:
    """A cartograph text body -> QGIS label text, plus what did not map.

    The inverse of `core/expression_tokens.translate_label` for every token
    it produces (that module stays the one-way reader; this is its mirror):

    - `{{frame.<id>.scale}}` / `{{frame.<id>.crs}}` (and the single-frame
      `{{scale}}` / `{{crs}}`) -> `@map_scale` / `@map_crs`, which a label
      resolves against the layout's reference map; a frame that is *not*
      the reference map is reached through `item_variables('<id>')`.
    - `{{title}}` -> `@project_title` when the page title is the project's
      title, else the page title as literal text -- a label must not start
      showing a different title than the cartograph did.
    - `{{attribution}}` / `{{notes}}` -> the page's text (QGIS has no
      variable for either).
    - `{{date}}` -> `format_date(now(),'yyyy-MM-dd')`, the ISO form the
      runtime's token always prints.
    - `{{atlas.index}}` / `{{atlas.count}}` / `{{atlas.<field>}}` ->
      `@atlas_featurenumber` / `@atlas_totalfeatures` /
      `attribute(@atlas_feature, '<field>')`.

    Anything else stays as its literal `{{…}}` text and is reported.
    """
    unmapped: list[str] = []

    def frame_expression(frame_id: str, variable: str, rounded: bool) -> str | None:
        if frame_id not in frame_ids:
            return None
        if frame_id == reference_frame_id:
            inner = f"@{variable}"
        else:
            inner = f"map_get(item_variables({_quote(frame_id)}), '{variable}')"
        return f"[% round({inner}) %]" if rounded else f"[% {inner} %]"

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        result: str | None
        if token == "title":
            result = "[% @project_title %]" if title_is_project_title else title
        elif token == "attribution":
            result = attribution
        elif token == "notes":
            result = notes
        elif token == "date":
            result = "[% format_date(now(),'yyyy-MM-dd') %]"
        elif token in ("scale", "crs"):
            result = (
                frame_expression(reference_frame_id, f"map_{token}", token == "scale")
                if reference_frame_id is not None
                else None
            )
        elif token.startswith("frame.") and token.count(".") >= 2:
            frame_id, _, what = token[len("frame.") :].rpartition(".")
            result = (
                frame_expression(frame_id, f"map_{what}", what == "scale")
                if what in ("scale", "crs")
                else None
            )
        elif token == "atlas.index":
            result = "[% @atlas_featurenumber %]"
        elif token == "atlas.count":
            result = "[% @atlas_totalfeatures %]"
        elif token.startswith("atlas.") and len(token) > len("atlas."):
            field_name = token[len("atlas.") :]
            result = f"[% attribute(@atlas_feature, {_quote(field_name)}) %]"
        else:
            result = None
        if result is None:
            unmapped.append(match.group(0))
            return match.group(0)
        return result

    return _TOKEN_RE.sub(replace, body), tuple(unmapped)


def live_frame_extent(
    center: tuple[float, float], zoom: float, box: Box
) -> QgsRectangle:
    """The EPSG:3857 extent a live frame's camera shows in its box.

    `core/cartograph_export.py`'s `_camera_for_extent` run backwards: there,
    an extent and a frame width give a zoom; here, a zoom and a frame width
    give the ground width (the latitude's cosine cancels out, so the width
    in degrees depends on the zoom alone). The camera's `center` is the
    *Mercator* centre of the view, as the runtime draws it, so the height is
    laid out symmetrically in Mercator metres, not in degrees.
    """
    lon, lat = center
    frame_px = box.w * _CSS_PIXELS_PER_MM
    width_degrees = frame_px * 360.0 / (WORLD_PIXELS_AT_ZOOM_0 * 2.0**zoom)
    width = math.radians(width_degrees) * _EARTH_RADIUS
    height = width * box.h / box.w
    cx = math.radians(lon) * _EARTH_RADIUS
    clamped = max(-85.05112878, min(85.05112878, lat))
    cy = _EARTH_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(clamped) / 2))
    return QgsRectangle(
        cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _quote(text: str) -> str:
    """A QGIS expression string literal."""
    return "'" + text.replace("'", "''") + "'"


def _color(value: str) -> QColor | None:
    """Any CSS colour QGIS can parse (`#rgb`, names, `rgb()`/`rgba()`)."""
    color = QgsSymbolLayerUtils.parseColor(value)
    return color if color.isValid() else None


def _subject(kind: str, item_id: str) -> str:
    return f'<om-{kind} id="{item_id}">'


def _boxes_differ(a: Box, b: Box, tolerance: float = 0.01) -> bool:
    return any(abs(getattr(a, k) - getattr(b, k)) > tolerance for k in "xywh")


class _Builder:
    def __init__(
        self,
        spec: CartographSpec,
        project: QgsProject,
        frame_layers: Mapping[str, Sequence[QgsMapLayer]],
        image_dir: Path | None,
        source_dir: Path | None,
    ) -> None:
        self.spec = spec
        self.project = project
        self.frame_layers = frame_layers
        self.image_dir = image_dir
        self.source_dir = source_dir
        self.issues: list[RoundTripIssue] = []
        self.items_by_id: dict[str, QgsLayoutItem] = {}
        self.maps: dict[str, QgsLayoutItemMap] = {}
        self.frame_modes: dict[str, FrameMode] = {}
        self.frame_dpis: dict[str, int] = {}
        self.layout = QgsPrintLayout(project)
        self.reference_frame_id: str | None = None

    def issue(self, subject: str, detail: str) -> None:
        self.issues.append(RoundTripIssue(subject, detail))

    # -- page ---------------------------------------------------------------

    def build(self, name: str) -> QgsPrintLayout:
        layout = self.layout
        layout.initializeDefaults()
        layout.setName(name)
        self._build_page()

        frames = [item for item in self.spec.items if isinstance(item, FrameSpec)]
        self.reference_frame_id = self._choose_reference_frame(frames)
        for frame in frames:
            self._build_frame(frame)
        if self.reference_frame_id is not None:
            layout.setReferenceMap(self.maps[self.reference_frame_id])
        for frame in frames:
            self._build_overview(frame)

        for index, item in enumerate(self.spec.items):
            built = self._build_item(item)
            if built is not None:
                self._finish_item(built, item, index)
        if self.spec.atlas is not None:
            self._build_atlas(self.spec.atlas)
        self._report_page_settings()
        return layout

    def _build_page(self) -> None:
        spec = self.spec
        if spec.size is not None:
            width, height = page_dimensions(spec.size, spec.orientation or "portrait")
        elif spec.width is not None and spec.height is not None:
            width, height = spec.width, spec.height
        else:  # enforced by CartographSpec.__post_init__
            raise ValueError("CartographSpec needs either size or width and height")
        page = self.layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(width, height, _MM))
        if spec.bg is not None:
            color = _color(spec.bg)
            if color is not None:
                symbol = QgsFillSymbol.createSimple(
                    {"color": color.name(QColor.NameFormat.HexArgb), "style": "solid"}
                )
                symbol.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
                page.setPageStyleSymbol(symbol)

    def _report_page_settings(self) -> None:
        spec = self.spec
        not_kept = [
            name
            for name, value in (
                ("theme", spec.theme),
                ("color", spec.color),
                ("font", spec.font),
                ("bleed", spec.bleed),
                ("crop-marks", spec.crop_marks),
                ("safe-zone", spec.safe_zone),
                ("dpi", spec.dpi),
                ("flatten", spec.flatten),
                ("color-profile", spec.color_profile),
                ("cvd", spec.cvd if spec.cvd != "none" else None),
            )
            if value
        ]
        if not_kept:
            self.issue(
                "<om-cartograph>",
                "These page settings have no QGIS layout equivalent and were not "
                f"kept: {', '.join(not_kept)}.",
            )

    # -- shared item plumbing -------------------------------------------------

    def _place(self, item: QgsLayoutItem, box: Box) -> None:
        item.attemptMove(QgsLayoutPoint(box.x, box.y, _MM))
        item.attemptResize(QgsLayoutSize(box.w, box.h, _MM))

    def _finish_item(
        self, item: QgsLayoutItem, spec_item: CartographItem, index: int
    ) -> None:
        """What every placed item shares: id, rotation, z, lock, visibility.

        `setItemRotation` turns the item about its centre -- the runtime's
        `transform-origin: center` -- so the box is placed unrotated first.
        """
        item.setId(spec_item.id)
        if spec_item.rotation:
            item.setItemRotation(spec_item.rotation, True)
        # Spec order is document order; an explicit `z` wins, as it does on
        # the page. +1 keeps every item above the page itself (z 0).
        item.setZValue(spec_item.z if spec_item.z is not None else index + 1)
        item.setLocked(spec_item.locked)
        if spec_item.hidden:
            item.setVisibility(False)
        self.items_by_id[spec_item.id] = item

    def _check_box(
        self, item: QgsLayoutItem, wanted: Box, subject: str, why: str
    ) -> None:
        size = item.sizeWithUnits()
        pos = item.positionWithUnits()
        got = Box(pos.x(), pos.y(), max(size.width(), 1e-9), max(size.height(), 1e-9))
        if _boxes_differ(got, wanted):
            self.issue(
                subject,
                f"{why} It is {got.w:.1f} x {got.h:.1f} mm in QGIS instead of "
                f"{wanted.w:.1f} x {wanted.h:.1f} mm.",
            )

    def _build_item(self, item: CartographItem) -> QgsLayoutItem | None:
        if isinstance(item, FrameSpec):
            return self.maps[item.id]
        if isinstance(item, GraticuleSpec):
            self._build_graticule(item)
            return None  # a grid lives on its map item, not on the page
        if isinstance(item, LegendSpec):
            return self._build_legend(item)
        if isinstance(item, ScalebarSpec):
            return self._build_scalebar(item)
        if isinstance(item, NorthSpec):
            return self._build_north(item)
        if isinstance(item, TextSpec):
            return self._build_text(item)
        if isinstance(item, ImageSpec):
            return self._build_image(item)
        if isinstance(item, ShapeSpec):
            return self._build_shape(item)
        raise TypeError(f"unknown cartograph item type: {type(item).__name__}")

    # -- frames ---------------------------------------------------------------

    def _choose_reference_frame(self, frames: list[FrameSpec]) -> str | None:
        """The frame most `{{…scale}}`/`{{…crs}}` tokens name, else the first.

        QGIS labels resolve `@map_scale` against one reference map; picking
        the most-referenced frame keeps the most labels on the short form.
        """
        if not frames:
            return None
        frame_ids = [frame.id for frame in frames]
        counts = dict.fromkeys(frame_ids, 0)
        for item in self.spec.items:
            if isinstance(item, TextSpec):
                for token in _TOKEN_RE.findall(item.body):
                    frame_id, _, what = token.removeprefix("frame.").rpartition(".")
                    if (
                        token.startswith("frame.")
                        and what in ("scale", "crs")
                        and frame_id in counts
                    ):
                        counts[frame_id] += 1
        return max(frame_ids, key=lambda frame_id: counts[frame_id])

    def _build_frame(self, frame: FrameSpec) -> None:
        subject = _subject("frame", frame.id)
        map_item = QgsLayoutItemMap(self.layout)
        self.layout.addLayoutItem(map_item)
        self._place(map_item, frame.box)

        layers = self.frame_layers.get(frame.id)
        if layers is not None:
            map_item.setKeepLayerSet(True)
            map_item.setLayers(list(layers))

        if frame.mode == "static":
            crs, extent, rotation = self._static_extent(frame, subject)
        else:
            crs, extent, rotation = self._live_extent(frame, subject)
        map_item.setCrs(crs)
        if extent is None:
            extent = self._layers_extent(map_item, crs)
        # Issue #1's order: setExtent on the fresh item (which resizes it to
        # the extent's aspect), the box back, and only then zoomToExtent --
        # safe now an extent exists -- which pads the extent to the box.
        map_item.setExtent(extent)
        self._place(map_item, frame.box)
        map_item.zoomToExtent(extent)
        if rotation:
            map_item.setMapRotation(rotation)

        if frame.frame_stroke is not None:
            color = _color(frame.frame_stroke)
            if color is not None:
                map_item.setFrameEnabled(True)
                map_item.setFrameStrokeColor(color)
        if frame.frame_bg is not None:
            color = _color(frame.frame_bg)
            if color is not None:
                map_item.setBackgroundEnabled(True)
                map_item.setBackgroundColor(color)
        if frame.atlas_fit is not None or frame.atlas_margin is not None:
            map_item.setAtlasDriven(True)
            map_item.setAtlasScalingMode(
                QgsLayoutItemMap.Fixed
                if frame.atlas_fit == "fixed"
                else QgsLayoutItemMap.Auto
            )
            if frame.atlas_margin is not None:
                map_item.setAtlasMargin(frame.atlas_margin)
        if frame.attribution:
            self.issue(
                subject,
                "The frame's attribution text has no place on a QGIS map item and "
                "was not kept; add a label for it if it must print.",
            )
        map_item.refresh()

        self.maps[frame.id] = map_item
        self.frame_modes[map_item.uuid()] = (
            "static" if frame.mode == "static" else "live"
        )
        if frame.dpi is not None:
            self.frame_dpis[map_item.uuid()] = round(frame.dpi)

    def _frame_crs(
        self, frame: FrameSpec, subject: str
    ) -> QgsCoordinateReferenceSystem:
        if frame.crs:
            crs = QgsCoordinateReferenceSystem(frame.crs)
            if crs.isValid():
                return crs
        if frame.crs_def:
            crs = QgsCoordinateReferenceSystem.fromProj(frame.crs_def)
            if crs.isValid():
                return crs
        self.issue(
            subject,
            "QGIS does not know the frame's CRS "
            f"({frame.crs or frame.crs_def or 'none'}); the frame was placed in "
            "EPSG:4326.",
        )
        return WGS84

    def _static_extent(
        self, frame: FrameSpec, subject: str
    ) -> tuple[QgsCoordinateReferenceSystem, QgsRectangle | None, float]:
        """The frame CRS, the unrotated extent and the map rotation that put
        the four lon/lat corners back where they were.

        The reader takes `visibleExtentPolygon()`'s tl/tr/br/bl in the map
        CRS and transforms them to EPSG:4326; this transforms them back, then
        recovers the centre (their mean), the size (two edge lengths) and the
        rotation (the top edge's angle -- verified headlessly to equal
        `mapRotation()`).
        """
        crs = self._frame_crs(frame, subject)
        if frame.corners is None:
            self.issue(
                subject,
                "The static frame has no corners, so it shows the full extent of "
                "its layers instead of the cartograph's view.",
            )
            return crs, None, 0.0
        first_lon = frame.corners[0][0]
        transform = QgsCoordinateTransform(WGS84, crs, self.project)
        points = []
        for lon, lat in frame.corners:
            # Unwrap around the first corner, as the runtime does, so a view
            # across the antimeridian does not open to the planet's width.
            lon = first_lon + ((lon - first_lon + 180.0) % 360.0) - 180.0
            point = transform.transform(QgsPointXY(lon, lat))
            points.append((point.x(), point.y()))
        tl, tr, _br, bl = points
        cx = sum(p[0] for p in points) / 4
        cy = sum(p[1] for p in points) / 4
        width = math.hypot(tr[0] - tl[0], tr[1] - tl[1])
        height = math.hypot(bl[0] - tl[0], bl[1] - tl[1])
        rotation = math.degrees(math.atan2(tr[1] - tl[1], tr[0] - tl[0]))
        if not (width > 0 and height > 0) or any(
            not math.isfinite(value) for value in (cx, cy, width, height)
        ):
            self.issue(
                subject,
                "The frame's corners do not form a usable extent in its CRS, so it "
                "shows the full extent of its layers instead.",
            )
            return crs, None, 0.0
        extent = QgsRectangle(
            cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
        )
        return crs, extent, rotation if abs(rotation) > 1e-9 else 0.0

    def _live_extent(
        self, frame: FrameSpec, subject: str
    ) -> tuple[QgsCoordinateReferenceSystem, QgsRectangle | None, float]:
        if frame.src:
            self.issue(
                subject,
                f"The frame showed the map document {frame.src!r}; in QGIS it draws "
                "the project's own layers.",
            )
        if frame.pitch:
            self.issue(
                subject,
                f"QGIS map items cannot tilt; the {frame.pitch:g}° pitch was dropped.",
            )
        if frame.center is None or frame.zoom is None:
            self.issue(
                subject,
                "The live frame has no camera (center and zoom), so it shows the "
                "full extent of its layers instead.",
            )
            return WEB_MERCATOR, None, frame.bearing
        return (
            WEB_MERCATOR,
            live_frame_extent(frame.center, frame.zoom, frame.box),
            frame.bearing,
        )

    def _layers_extent(
        self, map_item: QgsLayoutItemMap, crs: QgsCoordinateReferenceSystem
    ) -> QgsRectangle:
        """The combined extent of what `map_item` draws, in `crs` -- the view
        a frame with no usable camera falls back to."""
        layers = map_item.layers() if map_item.keepLayerSet() else []
        if not layers:
            root = self.project.layerTreeRoot()
            layers = [
                layer
                for layer in root.layerOrder()
                if root.findLayer(layer.id()) is not None
                and root.findLayer(layer.id()).isVisible()
            ]
        extent = QgsRectangle()
        for layer in layers:
            transform = QgsCoordinateTransform(layer.crs(), crs, self.project)
            try:
                layer_extent = transform.transformBoundingBox(layer.extent())
            except Exception:
                continue
            if layer_extent.isFinite() and not layer_extent.isEmpty():
                if extent.isNull():
                    extent = layer_extent
                else:
                    extent.combineExtentWith(layer_extent)
        if extent.isNull() or extent.isEmpty():
            fallback = QgsCoordinateTransform(WGS84, crs, self.project)
            try:
                extent = fallback.transformBoundingBox(QgsRectangle(-170, -80, 170, 80))
            except Exception:
                extent = QgsRectangle(-170, -80, 170, 80)
        return extent

    def _build_overview(self, frame: FrameSpec) -> None:
        if frame.overview_of is None:
            return
        map_item = self.maps[frame.id]
        overview = QgsLayoutItemMapOverview(
            f"overview-of-{frame.overview_of}", map_item
        )
        map_item.overviews().addOverview(overview)
        overview.setLinkedMap(self.maps[frame.overview_of])
        overview.setEnabled(True)
        if frame.overview_stroke is not None:
            color = _color(frame.overview_stroke)
            symbol = overview.frameSymbol()
            if color is not None and symbol is not None:
                symbol = symbol.clone()
                layer = symbol.symbolLayer(0)
                if isinstance(layer, QgsSimpleFillSymbolLayer):
                    layer.setStrokeColor(color)
                    layer.setStrokeStyle(Qt.PenStyle.SolidLine)
                overview.setFrameSymbol(symbol)

    # -- furniture ---------------------------------------------------------

    def _build_graticule(self, item: GraticuleSpec) -> None:
        subject = _subject("graticule", item.id)
        map_item = self.maps[item.for_]
        grid = QgsLayoutItemMapGrid(item.id, map_item)
        map_item.grids().addGrid(grid)
        grid.setEnabled(not item.hidden)
        grid.setStyle(_GRID_STYLE_BY_KIND[item.kind])

        # No crs on <om-graticule> means geographic (docs/cartograph.md).
        crs = WGS84
        if item.crs or item.crs_def:
            crs = QgsCoordinateReferenceSystem(item.crs) if item.crs else crs
            if not crs.isValid() and item.crs_def:
                crs = QgsCoordinateReferenceSystem.fromProj(item.crs_def)
            if not crs.isValid():
                self.issue(
                    subject, "QGIS does not know the grid's CRS; drawn in degrees."
                )
                crs = WGS84
        if crs != map_item.crs():
            grid.setCrs(crs)

        interval_x = item.interval_x or item.interval
        interval_y = item.interval_y or item.interval
        if interval_x is None or interval_y is None:
            automatic = self._automatic_interval(map_item, crs)
            interval_x = interval_x or automatic
            interval_y = interval_y or automatic
        grid.setIntervalX(interval_x)
        grid.setIntervalY(interval_y)

        if item.labels == "none":
            grid.setAnnotationEnabled(False)
        else:
            grid.setAnnotationEnabled(True)
            position = (
                QgsLayoutItemMapGrid.InsideMapFrame
                if item.labels == "inside"
                else QgsLayoutItemMapGrid.OutsideMapFrame
            )
            for side in (
                QgsLayoutItemMapGrid.Left,
                QgsLayoutItemMapGrid.Right,
                QgsLayoutItemMapGrid.Top,
                QgsLayoutItemMapGrid.Bottom,
            ):
                grid.setAnnotationPosition(position, side)
        if item.format == "dms":
            grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinuteSecond)
        if item.color is not None:
            color = _color(item.color)
            symbol = grid.lineSymbol()
            if color is not None and symbol is not None:
                symbol = symbol.clone()
                symbol.setColor(color)
                grid.setLineSymbol(symbol)
        if _boxes_differ(item.box, self._frame_box(item.for_)):
            self.issue(
                subject,
                "A QGIS grid always covers its whole map, so it no longer has its "
                "own box.",
            )
        if item.rotation:
            self.issue(subject, "A QGIS grid cannot be rotated on its own; dropped.")
        map_item.refresh()

    def _frame_box(self, frame_id: str) -> Box:
        frame = next(
            i for i in self.spec.items if isinstance(i, FrameSpec) and i.id == frame_id
        )
        return frame.box

    def _automatic_interval(
        self, map_item: QgsLayoutItemMap, crs: QgsCoordinateReferenceSystem
    ) -> float:
        """About five grid lines across the frame, on the runtime's 1-2-5
        series -- the runtime picks one this way when `interval` is omitted,
        but a QGIS grid needs a number."""
        extent = map_item.extent()
        if crs != map_item.crs():
            transform = QgsCoordinateTransform(map_item.crs(), crs, self.project)
            try:
                extent = transform.transformBoundingBox(extent)
            except Exception:
                return 1.0
        width = extent.width()
        return nice_interval(width / 5) if width > 0 and math.isfinite(width) else 1.0

    def _build_legend(self, item: LegendSpec) -> QgsLayoutItem:
        subject = _subject("legend", item.id)
        legend = QgsLayoutItemLegend(self.layout)
        self.layout.addLayoutItem(legend)
        map_item = self.maps[item.for_]
        legend.setLinkedMap(map_item)
        # The box is the spec's, not whatever the content measures.
        legend.setResizeToContents(False)
        layers = self.frame_layers.get(item.for_)
        if layers is not None:
            if hasattr(legend, "setSyncMode"):  # QGIS >= 4.0
                legend.setSyncMode(Qgis.LegendSyncMode.Manual)
            else:  # QGIS 3: the same switch, deprecated in 4.0
                legend.setAutoUpdateModel(False)
            root = legend.model().rootGroup()
            root.removeAllChildren()
            for layer in layers:
                root.addLayer(layer)
        legend.setTitle(item.title or "")
        if item.columns is not None:
            legend.setColumnCount(item.columns)
        if item.bg is not None and (color := _color(item.bg)) is not None:
            legend.setBackgroundEnabled(True)
            legend.setBackgroundColor(color)
        if item.stroke is not None and (color := _color(item.stroke)) is not None:
            legend.setFrameEnabled(True)
            legend.setFrameStrokeColor(color)
        if not item.derived:
            self.issue(
                subject,
                f"The legend's {len(item.rows)} written-out row(s) were not copied: "
                "a QGIS legend builds its rows from the layers of the map it is "
                "linked to.",
            )
        elif item.rows:
            self.issue(
                subject,
                f"{len(item.rows)} legend row override(s) (renamed, hidden or "
                "reordered rows) were not applied; edit them in the legend's "
                "item properties.",
            )
        not_kept = [
            name
            for name, value in (
                ("color", item.color),
                ("font-size", item.font_size),
                ("padding", item.padding),
            )
            if value is not None
        ]
        if not_kept:
            self.issue(
                subject, f"Legend styling not carried over: {', '.join(not_kept)}."
            )
        self._place(legend, item.box)
        legend.adjustBoxSize()
        self._place(legend, item.box)
        legend.refresh()
        return legend

    def _build_scalebar(self, item: ScalebarSpec) -> QgsLayoutItem:
        subject = _subject("scalebar", item.id)
        bar = QgsLayoutItemScaleBar(self.layout)
        self.layout.addLayoutItem(bar)
        bar.setLinkedMap(self.maps[item.for_])
        bar.setStyle(_SCALEBAR_STYLE_BY_KIND[item.kind])
        if item.kind == "line":
            self.issue(
                subject,
                "QGIS has no plain line scale bar; drawn as a line with ticks.",
            )
        bar.setNumberOfSegmentsLeft(0)
        bar.setNumberOfSegments(item.segments or 4)
        if item.segment_length is not None:
            units, per_segment = _scalebar_units(item.units, item.segment_length)
            bar.setUnits(units)
            bar.setUnitsPerSegment(per_segment)
            bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeFixed)
        else:
            # The runtime snaps an unset segment length to a 1-2-5 value that
            # fits the box; QGIS's fit-width mode is the same idea.
            units, _ = _scalebar_units(item.units, 1.0)
            bar.setUnits(units)
            bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeFitWidth)
            bar.setMinimumBarWidth(item.box.w / 2)
            bar.setMaximumBarWidth(item.box.w)
        bar.setUnitLabel(QgsUnitTypes.toAbbreviatedString(units))
        if item.color is not None:
            self.issue(subject, "The scale bar colour was not carried over.")
        self._place(bar, item.box)
        if item.segment_length is None:
            self._fit_scalebar_width(bar, item.box)
        self._check_box(
            bar, item.box, subject, "QGIS sizes a scale bar to its segments."
        )
        return bar

    def _fit_scalebar_width(self, bar: QgsLayoutItemScaleBar, box: Box) -> None:
        """Shrink a fit-width bar until the whole item fits its box.

        QGIS's minimum/maximum bar width bound the bar alone; the item also
        holds the labels and padding, so a bar allowed the full box width
        spills past it -- into whatever sits beside it. The overshoot is
        measured and taken off the bar, a few times, since a narrower bar can
        pick a shorter label.
        """
        maximum = box.w
        for _ in range(4):
            overshoot = bar.sizeWithUnits().width() - box.w
            if overshoot <= 0.01:
                break
            maximum = max(1.0, maximum - overshoot)
            bar.setMaximumBarWidth(maximum)
            bar.setMinimumBarWidth(min(bar.minimumBarWidth(), maximum / 2))
            self._place(bar, box)

    def _build_north(self, item: NorthSpec) -> QgsLayoutItem:
        picture = QgsLayoutItemPicture(self.layout)
        self.layout.addLayoutItem(picture)
        picture.setResizeMode(QgsLayoutItemPicture.Zoom)
        picture.setPicturePath(_north_svg(item.kind), QgsLayoutItemPicture.FormatSVG)
        picture.setLinkedMap(self.maps[item.for_])
        picture.setNorthMode(QgsLayoutItemPicture.GridNorth)
        if item.color is not None and (color := _color(item.color)) is not None:
            picture.setSvgFillColor(color)
            picture.setSvgStrokeColor(color)
        self._place(picture, item.box)
        picture.refreshPicture()
        return picture

    def _build_text(self, item: TextSpec) -> QgsLayoutItem:
        subject = _subject("text", item.id)
        label = QgsLayoutItemLabel(self.layout)
        self.layout.addLayoutItem(label)
        frame_ids = [i.id for i in self.spec.items if isinstance(i, FrameSpec)]
        text, unmapped = label_text_from_tokens(
            item.body,
            reference_frame_id=self.reference_frame_id,
            frame_ids=frame_ids,
            title=self.spec.title,
            title_is_project_title=(
                not self.spec.title or self.spec.title == self.project.title()
            ),
            attribution=self.spec.attribution,
            notes=self.spec.notes or "",
        )
        if (
            self.spec.title
            and self.spec.title != self.project.title()
            and ("{{title}}" in item.body.replace(" ", ""))
        ):
            self.issue(
                subject,
                "The page title differs from the project title, so it was written "
                "into the label as text rather than as [% @project_title %].",
            )
        for token in unmapped:
            self.issue(
                subject, f"The token {token} has no QGIS equivalent; kept as text."
            )
        label.setText(text)

        text_format = label.textFormat()
        font = text_format.font()
        if item.font:
            font.setFamily(item.font)
        if item.weight is not None:
            font.setBold(_is_bold(item.weight))
        text_format.setFont(font)
        if item.font_size is not None:
            text_format.setSize(item.font_size)
            text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        if item.color is not None and (color := _color(item.color)) is not None:
            text_format.setColor(color)
        label.setTextFormat(text_format)
        if item.align is not None:
            alignment = _HALIGN.get(item.align)
            if alignment is None:
                self.issue(subject, f"Unknown text alignment {item.align!r}; left.")
            else:
                label.setHAlign(alignment)
        if item.bg is not None and (color := _color(item.bg)) is not None:
            label.setBackgroundEnabled(True)
            label.setBackgroundColor(color)
        self._place(label, item.box)
        return label

    def _build_image(self, item: ImageSpec) -> QgsLayoutItem | None:
        subject = _subject("image", item.id)
        resolved = self._picture_source(item, subject)
        if resolved is None:
            return None
        path, picture_format = resolved
        picture = QgsLayoutItemPicture(self.layout)
        self.layout.addLayoutItem(picture)
        if item.fit == "fill":
            picture.setResizeMode(QgsLayoutItemPicture.Stretch)
        else:
            picture.setResizeMode(QgsLayoutItemPicture.Zoom)
            if item.fit == "cover":
                self.issue(
                    subject,
                    "QGIS has no 'cover' fit; the picture is fitted inside its box "
                    "instead.",
                )
        picture.setPicturePath(path, picture_format)
        self._place(picture, item.box)
        picture.refreshPicture()
        if picture.isMissingImage():
            self.issue(subject, "QGIS could not load the picture.")
        return picture

    def _picture_source(self, item: ImageSpec, subject: str) -> tuple[str, int] | None:
        """`(picture path, QgsLayoutItemPicture.Format)` for `item.src`."""
        src = item.src
        match = _DATA_URI_RE.match(src)
        if match is not None:
            mime = match.group(1).lower() or "image/png"
            is_base64 = ";base64" in match.group(2).lower()
            payload = match.group(3)
            picture_format = (
                QgsLayoutItemPicture.FormatSVG
                if mime == "image/svg+xml"
                else QgsLayoutItemPicture.FormatRaster
            )
            try:
                data = (
                    base64.b64decode(payload)
                    if is_base64
                    else unquote_to_bytes(payload)
                )
            except ValueError:
                self.issue(
                    subject, "The embedded picture could not be decoded; left out."
                )
                return None
            if self.image_dir is None:
                return "base64:" + base64.b64encode(data).decode(
                    "ascii"
                ), picture_format
            suffix = _IMAGE_SUFFIX_BY_MIME.get(mime, ".png")
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", item.id).strip("-") or "image"
            target = self.image_dir / f"{safe_id}{suffix}"
            try:
                self.image_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError as error:
                self.issue(
                    subject,
                    f"The picture could not be written to {target} ({error}); it was "
                    "embedded in the layout instead.",
                )
                return "base64:" + base64.b64encode(data).decode(
                    "ascii"
                ), picture_format
            return str(target), picture_format
        if re.match(r"^[a-z][a-z0-9+.-]*://", src, re.IGNORECASE):
            return src, QgsLayoutItemPicture.FormatUnknown
        path = Path(src)
        if not path.is_absolute() and self.source_dir is not None:
            path = self.source_dir / path
        if not path.exists():
            self.issue(subject, f"The picture {src!r} was not found; left out.")
            return None
        return str(path), QgsLayoutItemPicture.FormatUnknown

    def _build_shape(self, item: ShapeSpec) -> QgsLayoutItem:
        subject = _subject("shape", item.id)
        stroke_color = _color(item.stroke) if item.stroke is not None else None
        if item.kind in ("line", "arrow"):
            # The runtime draws both from the box's top-left to its
            # bottom-right corner (cartograph.js `M 0 0 L w h`).
            box = item.box
            polyline = QgsLayoutItemPolyline(
                QPolygonF(
                    [QPointF(box.x, box.y), QPointF(box.x + box.w, box.y + box.h)]
                ),
                self.layout,
            )
            self.layout.addLayoutItem(polyline)
            properties = {
                "line_color": (stroke_color or QColor("black")).name(),
                "line_width": str(item.stroke_width or 0.26),
                "line_width_unit": "MM",
            }
            polyline.setSymbol(QgsLineSymbol.createSimple(properties))
            if item.kind == "arrow":
                polyline.setEndMarker(QgsLayoutItemPolyline.ArrowHead)
            if item.fill is not None or item.radius:
                self.issue(subject, "A line has no fill or corner radius; dropped.")
            return polyline

        shape = QgsLayoutItemShape(self.layout)
        self.layout.addLayoutItem(shape)
        shape.setShapeType(
            QgsLayoutItemShape.Ellipse
            if item.kind == "ellipse"
            else QgsLayoutItemShape.Rectangle
        )
        fill = _color(item.fill) if item.fill is not None else None
        if fill is not None and item.fill_opacity is not None:
            fill.setAlphaF(item.fill_opacity)
        symbol_properties = {
            "color": fill.name(QColor.NameFormat.HexArgb) if fill else "#00000000",
            "style": "solid" if fill is not None else "no",
            "outline_style": "solid" if stroke_color is not None else "no",
            "outline_color": (stroke_color or QColor("black")).name(),
            "outline_width": str(item.stroke_width if item.stroke_width else 0.26),
            "outline_width_unit": "MM",
        }
        shape.setSymbol(QgsFillSymbol.createSimple(symbol_properties))
        if item.radius:
            shape.setCornerRadius(QgsLayoutMeasurement(item.radius, _MM))
        self._place(shape, item.box)
        return shape

    # -- atlas ----------------------------------------------------------------

    def _build_atlas(self, atlas_spec: AtlasSpec) -> None:
        subject = _ATLAS_SUBJECT
        layers = [
            layer
            for layer in self.project.mapLayersByName(atlas_spec.layer)
            if isinstance(layer, QgsVectorLayer)
        ]
        map_item = self.maps[atlas_spec.for_]
        if not map_item.atlasDriven():
            map_item.setAtlasDriven(True)
            map_item.setAtlasScalingMode(QgsLayoutItemMap.Auto)
        if not layers:
            self.issue(
                subject,
                f"The atlas layer {atlas_spec.layer!r} is not a vector layer in this "
                "project, so the atlas was not set up.",
            )
            return
        atlas = self.layout.atlas()
        atlas.setCoverageLayer(layers[0])
        atlas.setEnabled(True)
        if atlas_spec.filter:
            expression = QgsExpression(atlas_spec.filter)
            if expression.hasParserError():
                self.issue(
                    subject,
                    f"The atlas filter {atlas_spec.filter!r} is not a valid QGIS "
                    "expression, so it was not applied.",
                )
            else:
                atlas.setFilterFeatures(True)
                atlas.setFilterExpression(atlas_spec.filter)
        if atlas_spec.sort:
            keys = [key.strip() for key in atlas_spec.sort.split(",") if key.strip()]
            if keys:
                first = keys[0]
                descending = first.startswith("-")
                field_name = first.lstrip("-+")
                atlas.setSortFeatures(True)
                atlas.setSortExpression(QgsExpression.quotedColumnRef(field_name))
                atlas.setSortAscending(not descending)
                if len(keys) > 1:
                    self.issue(
                        subject,
                        f"QGIS sorts an atlas by one key; only {first!r} was kept.",
                    )
        if atlas_spec.filename:
            atlas.setFilenameExpression(_pattern_expression(atlas_spec.filename))
        if atlas_spec.page_name:
            atlas.setPageNameExpression(_pattern_expression(atlas_spec.page_name))
        if atlas_spec.pager == "none":
            self.issue(subject, "The on-sheet pager setting has no QGIS equivalent.")


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------


def _is_bold(weight: str) -> bool:
    text = weight.strip().lower()
    if text in ("bold", "bolder"):
        return True
    try:
        return float(text) >= 600
    except ValueError:
        return False


def _scalebar_units(units: str, segment_metres: float) -> tuple[int, float]:
    """A QGIS distance unit and units-per-segment for a segment length the
    runtime gives in metres -- in the unit a reader expects at that length."""
    if units == "imperial":
        if segment_metres >= 1609.344:
            return QgsUnitTypes.DistanceMiles, segment_metres / 1609.344
        return QgsUnitTypes.DistanceFeet, segment_metres / 0.3048
    if units == "nautical":
        return QgsUnitTypes.DistanceNauticalMiles, segment_metres / 1852.0
    if segment_metres >= 1000:
        return QgsUnitTypes.DistanceKilometers, segment_metres / 1000.0
    return QgsUnitTypes.DistanceMeters, segment_metres


def _north_svg(kind: str) -> str:
    if kind == "rose":
        return QgsSymbolLayerUtils.svgSymbolNameToPath(
            _NORTH_ROSE_SVG, QgsProject.instance().pathResolver()
        )
    if QFile.exists(_NORTH_ARROW_RESOURCE):
        return _NORTH_ARROW_RESOURCE
    return QgsSymbolLayerUtils.svgSymbolNameToPath(
        _NORTH_FALLBACK_SVG, QgsProject.instance().pathResolver()
    )


def _pattern_expression(pattern: str) -> str:
    """A runtime token pattern (`lot-{{atlas.lot}}`) -> a QGIS expression
    (`'lot-' || attribute(@atlas_feature, 'lot')`)."""
    parts: list[str] = []
    position = 0
    for match in _TOKEN_RE.finditer(pattern):
        if match.start() > position:
            parts.append(_quote(pattern[position : match.start()]))
        token = match.group(1)
        if token == "atlas.index":
            parts.append("@atlas_featurenumber")
        elif token == "atlas.count":
            parts.append("@atlas_totalfeatures")
        elif token.startswith("atlas."):
            parts.append(f"attribute(@atlas_feature, {_quote(token[len('atlas.') :])})")
        else:
            parts.append(_quote(match.group(0)))
        position = match.end()
    if position < len(pattern):
        parts.append(_quote(pattern[position:]))
    return " || ".join(parts) if parts else "''"
