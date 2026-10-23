"""One-click mode, the PyQGIS half: the map canvas -> a cartograph spec AND a
real `QgsPrintLayout` in the project.

Issue #1 section 4, step 5 is the differentiator this module exists for: the
canvas is not exported straight to HTML, it becomes a print layout named
"carto: <project title>" that the user can keep editing in QGIS -- and the
cartograph then follows that layout like any other (the dialog selects it as
the source). So the plugin is not a dead end in either direction.

The arithmetic lives in `core/canvas_layout.py` (pure, unit-tested); this
module only reads the canvas, hands the numbers over, and builds the result
through `core/layout_builder.py`, the same builder a cartograph imported from
a file goes through.

**The frame keeps the canvas's own CRS and view.** It is described by its
four corners in that CRS -- exactly how a static frame is georeferenced -- so
the builder puts the map item back on the canvas extent and rotation
bit-for-bit, whatever the CRS. Whether the cartograph then draws it live or
as a raster is an export-time choice, so `CanvasCartograph.frame_modes`
carries the usual default (`core/layout_reader.default_mode_for_map`) for the
caller to pass to `prepare_cartograph`, and the user can still flip it in
Map frames.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayerTreeLayer,
    QgsLayout,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutUtils,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMapLayer,
    QgsMapLayerLegendUtils,
    QgsMapLayerStyle,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsRendererCategory,
    QgsTextRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QObject, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.PyQt.QtXml import QDomDocument

from . import layout_templates
from .cartograph_import import RoundTripIssue
from .cartograph_ir import Box, CartographSpec, FrameSpec, LegendSpec
from .layer_reader import read_attribution
from .layout_builder import BuiltLayout, build_layout
from .layout_reader import (
    LEGEND_LEFT_OUT_PROPERTY,
    WRAPPED_TITLE_PROPERTY,
    FrameMode,
    default_mode_for_map,
)
from .layout_templates import (
    MAIN_FRAME_ID,
    STANDARD_STEP,
    FrameSlot,
    TemplateInput,
    TemplatePlan,
    TextSizes,
    plan_template,
    template_spec,
)

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: The main frame's id in the spec and the map item's id in the layout.
FRAME_ID = MAIN_FRAME_ID
#: What the layout is called when the project has no title.
_UNTITLED = "map"
_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
#: A legend's text, symbols and spacing are drawn smaller to fit the box
#: its template planned in steps of this share of their planned sizes --
#: never below the template's readable floor (`TemplatePlan.legend_min_pt`):
#: past it the entries that do not fit are left out and counted, so the
#: page stays readable and says what it left out.
_LEGEND_SCALE_STEP = 0.05
#: The `QImage.text()` key a template preview carries the number of legend
#: entries left out under (`CanvasCartograph.legend_left_out`), "0" when
#: none: the picker can say so beside the thumbnail.
PREVIEW_LEGEND_LEFT_OUT_KEY = "legend-left-out"
#: Only a bound for the search; the box's width stops it long before.
_LEGEND_MAX_COLUMNS = 12
_LEGEND_TEXT_STYLES = (
    QgsLegendStyle.Title,
    QgsLegendStyle.Group,
    QgsLegendStyle.Subgroup,
    QgsLegendStyle.SymbolLabel,
)
_LEGEND_SIDES = (
    QgsLegendStyle.Top,
    QgsLegendStyle.Bottom,
    QgsLegendStyle.Left,
    QgsLegendStyle.Right,
)


@dataclass(frozen=True)
class CanvasCartograph:
    """The one-click result.

    - `layout` is already in the project's layout manager, uniquely named
      "carto: <title>" -- select it as the cartograph's source.
    - `spec` is what the layout was built from. Its frame is a georeference
      only (corners in the canvas CRS, no raster yet): export from `layout`
      with `prepare_cartograph(..., frame_modes=frame_modes)`, never by
      writing `spec` directly.
    - `frame_modes` is keyed by map item uuid, as `prepare_cartograph` takes.
    - `legend_left_out` is how many legend entries the layout's legend
      leaves out because its box cannot hold them at the template's
      readable floor -- measured by QGIS, 0 when all fit. The layout's
      checks report it too (`layout_reader.LEGEND_LEFT_OUT_PROPERTY`).
    """

    spec: CartographSpec
    layout: QgsPrintLayout
    plan: TemplatePlan
    issues: tuple[RoundTripIssue, ...]
    frame_modes: Mapping[str, FrameMode]
    legend_left_out: int = 0


def cartograph_from_canvas(
    canvas: QgsMapCanvas,
    project: QgsProject,
    *,
    page_size: str = "A4",
    orientation: str = "auto",
    template_id: str = "classic",
    placeholders: Mapping[str, str] | None = None,
    text_sizes: TextSizes | None = None,
) -> CanvasCartograph:
    """Plan and build the one-click cartograph for what `canvas` shows now:
    its extent, rotation, destination CRS and layers (in drawing order), in
    the layout template `template_id` (`core/layout_templates.py`)."""
    settings = canvas.mapSettings()
    return cartograph_from_view(
        settings.extent(),
        settings.destinationCrs(),
        canvas.layers(),
        project,
        rotation=settings.rotation(),
        page_size=page_size,
        orientation=orientation,
        template_id=template_id,
        placeholders=placeholders,
        text_sizes=text_sizes,
    )


def cartograph_from_view(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
    template_id: str = "classic",
    placeholders: Mapping[str, str] | None = None,
    text_sizes: TextSizes | None = None,
) -> CanvasCartograph:
    """`cartograph_from_canvas` for a view given as plain values -- the same
    thing without a widget, for callers (and tests) that have no canvas.

    `extent` is the *unrotated* extent in `crs` (`QgsMapSettings.extent()`),
    `rotation` the canvas rotation in degrees, `layers` top layer first.
    Raises `ValueError` for an empty or non-finite extent, an invalid CRS or
    an unknown template, page size or orientation.
    """
    plan, spec, built, left_out = _build_from_view(
        extent,
        crs,
        layers,
        project,
        rotation=rotation,
        page_size=page_size,
        orientation=orientation,
        template_id=template_id,
        placeholders=placeholders,
        add_to_project=True,
        text_sizes=text_sizes,
    )
    _record_template(
        built.layout,
        TemplateRecord(
            template_id=template_id,
            page_size=page_size,
            orientation=plan.orientation,
            extent=(
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            ),
            crs_wkt=crs.toWkt(),
            layer_ids=tuple(layer.id() for layer in layers),
            rotation=rotation,
            placeholders=dict(placeholders or {}),
            figures=tuple(project_figures(project)),
        ),
        project_text_sizes(project) if text_sizes is None else text_sizes,
    )
    map_item = built.items_by_id[FRAME_ID]
    mode: FrameMode = default_mode_for_map(map_item)
    modes = {map_item.uuid(): mode}
    for slot in plan.frames[1:]:
        # A locator inset is context, not the subject: a static picture
        # keeps it exactly as QGIS draws it at no cost to the page.
        modes[built.items_by_id[slot.id].uuid()] = "static"
    return CanvasCartograph(
        spec=spec,
        layout=built.layout,
        plan=plan,
        issues=built.issues,
        frame_modes=modes,
        legend_left_out=left_out,
    )


#: The layout custom properties a layout made from a template carries: the
#: view and choices it was built from (JSON, `TemplateRecord`), the text
#: size steps it was built with, and a fingerprint of its items as built
#: (`layout_edited`). A layout without the first is not one the template
#: can rebuild.
TEMPLATE_PROPERTY = "nika/template"
TEMPLATE_SIZES_PROPERTY = "nika/template-sizes"
TEMPLATE_FINGERPRINT_PROPERTY = "nika/template-fingerprint"


@dataclass(frozen=True)
class TemplateRecord:
    """What a template-made layout needs to be built again: the design, the
    page, and the canvas view and project values it was made from."""

    template_id: str
    page_size: str
    orientation: str
    extent: tuple[float, float, float, float]
    crs_wkt: str
    layer_ids: tuple[str, ...]
    rotation: float
    placeholders: Mapping[str, str]
    figures: tuple[tuple[str, str], ...]


def _record_template(
    layout: QgsPrintLayout, record: TemplateRecord, sizes: TextSizes
) -> None:
    layout.setCustomProperty(TEMPLATE_PROPERTY, json.dumps(dataclasses.asdict(record)))
    layout.setCustomProperty(
        TEMPLATE_SIZES_PROPERTY, json.dumps(dataclasses.asdict(sizes))
    )
    layout.setCustomProperty(TEMPLATE_FINGERPRINT_PROPERTY, _layout_fingerprint(layout))


def template_record(layout: QgsLayout) -> TemplateRecord | None:
    """The record of a layout made from a template; `None` for any other
    layout, or one whose record cannot be read."""
    raw = layout.customProperty(TEMPLATE_PROPERTY)
    if not raw:
        return None
    try:
        data = json.loads(str(raw))
        return TemplateRecord(
            template_id=str(data["template_id"]),
            page_size=str(data["page_size"]),
            orientation=str(data["orientation"]),
            extent=tuple(float(v) for v in data["extent"]),  # type: ignore[arg-type]
            crs_wkt=str(data["crs_wkt"]),
            layer_ids=tuple(str(v) for v in data["layer_ids"]),
            rotation=float(data["rotation"]),
            placeholders={str(k): str(v) for k, v in data["placeholders"].items()},
            figures=tuple((str(a), str(b)) for a, b in data["figures"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def layout_text_sizes(layout: QgsLayout, project: QgsProject) -> TextSizes:
    """The size steps `layout` was built with; the project's (the picker's)
    when it records none."""
    raw = layout.customProperty(TEMPLATE_SIZES_PROPERTY)
    if raw:
        try:
            data = json.loads(str(raw))
            return TextSizes(
                str(data["title"]), str(data["legend"]), str(data["notes"])
            )
        except (ValueError, KeyError, TypeError):
            pass
    return project_text_sizes(project)


def _layout_fingerprint(layout: QgsLayout) -> str:
    """The items' kind, id, place, size and text, as a short hash: what a
    move, a resize, an added or removed item or a text edit changes."""
    rows = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItem) or not item.id():
            continue
        rect = item.sceneBoundingRect()
        text = item.text() if isinstance(item, QgsLayoutItemLabel) else ""
        rows.append(
            f"{type(item).__name__}|{item.id()}|{rect.x():.2f}|{rect.y():.2f}"
            f"|{rect.width():.2f}|{rect.height():.2f}|{text}"
        )
    rows.sort()
    return hashlib.sha1("\n".join(rows).encode()).hexdigest()


def layout_edited(layout: QgsLayout) -> bool:
    """Whether the user changed `layout` since the template built it. The
    undo stack tells a session's edits, but is empty again once the project
    is reopened; the items' fingerprint stored at the build tells both."""
    return layout.customProperty(TEMPLATE_FINGERPRINT_PROPERTY) != _layout_fingerprint(
        layout
    )


def rebuild_from_template(
    layout: QgsPrintLayout, project: QgsProject, sizes: TextSizes
) -> dict[str, str]:
    """Build `layout` again from its template with the size steps `sizes`,
    in place: the same layout object and name, its items replaced. Returns
    the new map item uuid for each old one, by the item's id, so the choices
    kept per map frame can follow. Raises `ValueError` when `layout` has no
    record or its view cannot be rebuilt; the layout is then untouched."""
    record = template_record(layout)
    if record is None:
        raise ValueError("the layout was not made from a template")
    crs = QgsCoordinateReferenceSystem.fromWkt(record.crs_wkt)
    layers = [
        found
        for found in (project.mapLayer(layer_id) for layer_id in record.layer_ids)
        if found is not None
    ]
    old_ids = {
        item.uuid(): item.id()
        for item in layout.items()
        if isinstance(item, QgsLayoutItemMap)
    }
    with _project_dirty_state_kept(project):
        _plan, _spec, built, _left_out = _build_from_view(
            QgsRectangle(*record.extent),
            crs,
            layers,
            project,
            rotation=record.rotation,
            page_size=record.page_size,
            orientation=record.orientation,
            template_id=record.template_id,
            placeholders=record.placeholders,
            add_to_project=False,
            figures=record.figures,
            text_sizes=sizes,
        )
    name = layout.name()
    document = QDomDocument()
    element = built.layout.writeXml(document, QgsReadWriteContext())
    document.appendChild(element)
    layout.clear()
    loaded = layout.loadFromTemplate(document, QgsReadWriteContext(), True)
    layout.setName(name)
    if not loaded:
        raise ValueError("the rebuilt page could not be loaded into the layout")
    new_by_id = {
        item.id(): item.uuid()
        for item in layout.items()
        if isinstance(item, QgsLayoutItemMap)
    }
    _record_template(layout, record, sizes)
    return {
        old: new_by_id[item_id]
        for old, item_id in old_ids.items()
        if item_id in new_by_id
    }


@contextlib.contextmanager
def _project_dirty_state_kept(project: QgsProject) -> Iterator[None]:
    """A layout made on `project` marks it changed as soon as it is given
    its defaults, whether or not it is ever added: a preview thrown away
    must not ask the user to save. Nothing else touches the project while a
    preview is built (one thread, no event loop), so its flag is put back
    to what it was."""
    was_dirty = project.isDirty()
    try:
        yield
    finally:
        if not was_dirty and project.isDirty():
            project.setDirty(False)


def template_preview(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    template_id: str,
    width_px: int,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
    placeholders: Mapping[str, str] | None = None,
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
) -> QImage:
    """The page `template_id` would make of this view, drawn by QGIS
    `width_px` wide -- the template picker's thumbnail. The layout is built
    outside the project and thrown away; nothing is added to it.

    `figures` are the key figures as the picker's fields hold them, not yet
    saved; `None` takes the project's (`project_figures`). `text_sizes` is
    the same for the title, legend and notes sizes (`project_text_sizes`).
    The image's `text(PREVIEW_LEGEND_LEFT_OUT_KEY)` is how many legend
    entries the page leaves out for want of room ("0" when none)."""
    with _project_dirty_state_kept(project):
        plan, _spec, built, left_out = _build_from_view(
            extent,
            crs,
            layers,
            project,
            rotation=rotation,
            page_size=page_size,
            orientation=orientation,
            template_id=template_id,
            placeholders=placeholders,
            add_to_project=False,
            figures=figures,
            text_sizes=text_sizes,
        )
        height_px = max(1, round(width_px * plan.page_height / plan.page_width))
        image = QgsLayoutExporter(built.layout).renderPageToImage(
            0, QSize(width_px, height_px)
        )
    image.setText(PREVIEW_LEGEND_LEFT_OUT_KEY, str(left_out))
    return image


class PendingPreview(QObject):
    """A template preview whose maps QGIS draws in the background.

    The page is built at once (the cheap part, on the GUI thread) and painted
    the way the layout designer paints it: each map item starts its own
    background render job and shows a placeholder until it is done. The event
    loop stays free meanwhile. When every map has reported, the page is
    painted again from their caches and `finished` carries the image, or
    `None` when it could not be drawn. `cancel()` drops the page and with it
    any job still running; nothing is emitted afterwards.

    The image is what `template_preview` draws, but not pixel for pixel: a
    map's cache is resampled onto the thumbnail instead of drawn straight at
    its size, so edges differ in anti-aliasing.
    """

    finished = pyqtSignal(object)

    def __init__(
        self,
        project: QgsProject,
        built: BuiltLayout,
        size: QSize,
        left_out: int,
        was_dirty: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        #: Whether the project was already changed: if not, nothing this
        #: preview does (building, drawing, dropping the page) may leave it so.
        self._was_dirty = was_dirty
        self._layout: QgsPrintLayout | None = built.layout
        self._size = size
        self._left_out = left_out
        self._waiting = {
            id(item)
            for item in built.layout.items()
            if isinstance(item, QgsLayoutItemMap)
        }
        self._done = False
        # The page is shown by the first paint, which starts the map jobs.
        for item in built.layout.items():
            if isinstance(item, QgsLayoutItemMap):
                # By id, not the item: a closure holding the item would keep
                # its Python wrapper alive after the page is dropped.
                item.previewRefreshed.connect(
                    functools.partial(self._on_map_refreshed, id(item))
                )
        self._paint()
        if not self._waiting:
            QTimer.singleShot(0, self._finish)

    @property
    def active(self) -> bool:
        return self._layout is not None

    def _paint(self) -> QImage:
        layout = self._layout
        if layout is None:
            return QImage()
        image = QImage(self._size, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        page = layout.pageCollection().page(0)
        # Pixels per metre of the page, so the map caches follow the size.
        per_metre = round(self._size.width() / (page.rect().width() / 1000.0))
        image.setDotsPerMeterX(per_metre)
        image.setDotsPerMeterY(per_metre)
        with self._project_kept():
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            layout.render(
                painter,
                QRectF(0, 0, self._size.width(), self._size.height()),
                QRectF(page.pos(), page.rect().size()),
            )
            painter.end()
        return image

    @contextlib.contextmanager
    def _project_kept(self) -> Iterator[None]:
        try:
            yield
        finally:
            if not self._was_dirty and self._project.isDirty():
                self._project.setDirty(False)

    def _on_map_refreshed(self, key: int) -> None:
        with self._project_kept():
            self._waiting.discard(key)
        if not self._waiting:
            self._finish()

    def _finish(self) -> None:
        if self._done or self._layout is None:
            return
        self._done = True
        image = self._paint()
        image.setText(PREVIEW_LEGEND_LEFT_OUT_KEY, str(self._left_out))
        self._release()
        self.finished.emit(image)

    def cancel(self) -> None:
        """Stop drawing: the page is dropped, which ends its map jobs."""
        self._done = True
        self._release()

    def _release(self) -> None:
        layout, self._layout = self._layout, None
        if layout is not None:
            with self._project_kept():
                pass


def template_preview_async(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    template_id: str,
    width_px: int,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
    placeholders: Mapping[str, str] | None = None,
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
) -> PendingPreview:
    """`template_preview`, drawn in the background: see `PendingPreview`.
    The arguments are the same; building the page can raise, as there."""
    was_dirty = project.isDirty()
    with _project_dirty_state_kept(project):
        plan, _spec, built, left_out = _build_from_view(
            extent,
            crs,
            layers,
            project,
            rotation=rotation,
            page_size=page_size,
            orientation=orientation,
            template_id=template_id,
            placeholders=placeholders,
            add_to_project=False,
            figures=figures,
            text_sizes=text_sizes,
        )
    height_px = max(1, round(width_px * plan.page_height / plan.page_width))
    return PendingPreview(
        project, built, QSize(width_px, height_px), left_out, was_dirty
    )


def view_input(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float = 0.0,
    page_size: str = "A4",
    orientation: str = "auto",
    placeholders: Mapping[str, str] | None = None,
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
) -> TemplateInput:
    """What the template planner takes for this view and project: the one
    place a canvas becomes a `TemplateInput`, for the layout, its preview,
    and the picker's check of what each page includes. `figures` and
    `text_sizes` `None` take the project's (`project_figures`,
    `project_text_sizes`)."""
    data = TemplateInput(
        extent_width=extent.width(),
        extent_height=extent.height(),
        crs_is_geographic=crs.isGeographic(),
        crs_authid=crs.authid(),
        crs_def=crs.toProj() or None,
        page_size=page_size,
        orientation=orientation,
        title=project.title().strip(),
        layer_names=tuple(layer.name() for layer in layers),
        credits=tuple(_credits(layers)),
        legend_labels=tuple(_legend_labels(layer) for layer in layers),
        notes=project.metadata().abstract(),
        placeholders=dict(placeholders or {}),
        author=project.metadata().author(),
        rotation=rotation,
        figures=project_figures(project) if figures is None else tuple(figures),
        extent_center=(extent.center().x(), extent.center().y()),
    )
    sizes = project_text_sizes(project) if text_sizes is None else text_sizes
    return sizes.apply(data)


_Built = tuple[TemplatePlan, CartographSpec, BuiltLayout, int]


def _build_from_view(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float,
    page_size: str,
    orientation: str,
    template_id: str,
    placeholders: Mapping[str, str] | None,
    add_to_project: bool,
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
) -> _Built:
    """Plan and build the page; the last value is how many legend entries
    the built legend leaves out (`_fit_legend`).

    "auto" orientation takes the one that shows the map largest -- unless
    its legend leaves entries out and the other orientation's leaves out
    fewer: turning the sheet is more room for the legend too."""

    def build(turn: str, add: bool) -> _Built:
        return _build_once(
            extent,
            crs,
            layers,
            project,
            rotation=rotation,
            page_size=page_size,
            orientation=turn,
            template_id=template_id,
            placeholders=placeholders,
            add_to_project=add,
            figures=figures,
            text_sizes=text_sizes,
        )

    first = build(orientation, add_to_project)
    if orientation != "auto" or not first[3]:
        return first
    other = "portrait" if first[0].orientation == "landscape" else "landscape"
    trial = build(other, False)
    if trial[3] >= first[3]:
        return first
    if not add_to_project:
        return trial
    project.layoutManager().removeLayout(first[2].layout)
    return build(other, True)


def _build_once(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float,
    page_size: str,
    orientation: str,
    template_id: str,
    placeholders: Mapping[str, str] | None,
    add_to_project: bool,
    figures: Sequence[tuple[str, str]] | None,
    text_sizes: TextSizes | None,
) -> _Built:
    if not crs.isValid():
        raise ValueError("the canvas has no valid CRS")
    if extent.isNull() or not extent.isFinite() or extent.isEmpty():
        raise ValueError("the canvas extent is empty")

    title = project.title().strip()
    plan = plan_template(
        template_id,
        view_input(
            extent,
            crs,
            layers,
            project,
            rotation=rotation,
            page_size=page_size,
            orientation=orientation,
            placeholders=placeholders,
            figures=figures,
            text_sizes=text_sizes,
        ),
    )
    frames = {
        slot.id: _frame(slot, extent, rotation, crs, project) for slot in plan.frames
    }
    spec = template_spec(
        plan,
        frames,
        cartograph_id=_ID_SANITIZE_RE.sub("-", title).strip("-") or "cartograph",
    )
    built = build_layout(
        spec,
        project,
        name=f"carto: {title or _UNTITLED}",
        frame_layers={slot.id: list(layers) for slot in plan.frames},
        add_to_project=add_to_project,
    )
    left_out = 0
    for item in plan.items:
        if isinstance(item, LegendSpec):
            legend = built.items_by_id[item.id]
            if isinstance(legend, QgsLayoutItemLegend):
                if item.wrap_width is not None and not legend_headings_wrap():
                    _wrap_headings(legend, item.wrap_width)
                left_out += _fit_legend(legend, plan.legend_min_pt)
    return plan, spec, built, left_out


@functools.lru_cache(maxsize=1)
def legend_headings_wrap() -> bool:
    """Whether this QGIS wraps a class list's heading at a legend's wrap
    width, as it wraps the rows (QGIS 4 does, 3.44 does not): measured
    once, on a throwaway legend of a two-class layer with a two-word name,
    in a project of its own."""
    project = QgsProject()
    layer = QgsVectorLayer("Point?field=c:string", "heading words", "memory")
    layer.setRenderer(
        QgsCategorizedSymbolRenderer(
            "c",
            [
                QgsRendererCategory(value, QgsMarkerSymbol.createSimple({}), value)
                for value in ("a", "b")
            ],
        )
    )
    project.addMapLayer(layer)
    layout = QgsPrintLayout(project)
    legend = QgsLayoutItemLegend(layout)
    layout.addLayoutItem(legend)
    if hasattr(legend, "setSyncMode"):  # QGIS >= 4.0
        legend.setSyncMode(Qgis.LegendSyncMode.Manual)
    else:  # QGIS 3
        legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.removeAllChildren()
    root.addLayer(layer)
    context = QgsLayoutUtils.createRenderContextForLayout(layout, None)

    def height(wrap: float) -> float:
        legend.setAutoWrapLinesAfter(wrap)
        renderer = QgsLegendRenderer(legend.model(), legend.legendSettings())
        return renderer.minimumSize(context).height()

    return height(1.0) > height(0.0) + 0.01


def _wrap_headings(legend: QgsLayoutItemLegend, wrap: float) -> None:
    """Break each class list's heading in `legend` onto lines no wider than
    `wrap` mm, as QGIS 4 does by itself and QGIS 3.44 does not (it wraps
    only the rows under it): the same words in the legend's own copy of
    the layer's title, marked so the export reads no rename."""
    context = QgsLayoutUtils.createRenderContextForLayout(legend.layout(), None)
    text_format = legend.rstyle(QgsLegendStyle.Subgroup).textFormat()
    per_mm = context.scaleFactor() or 1.0

    def width(text: str) -> float:
        return QgsTextRenderer.textWidth(context, text_format, [text]) / per_mm

    model = legend.model()
    for node in model.rootGroup().findLayers():
        if len(model.layerLegendNodes(node)) < 2:
            continue  # one symbol: its name is a row, which QGIS wraps
        title = str(node.customProperty("legend/title-label") or node.name())
        lines: list[str] = []
        line = ""
        for word in title.split():
            longer = f"{line} {word}" if line else word
            if line and width(longer) > wrap:
                lines.append(line)
                line = word
            else:
                line = longer
        lines.append(line)
        if len(lines) > 1:
            node.setCustomProperty("legend/title-label", "\n".join(lines))
            node.setCustomProperty(WRAPPED_TITLE_PROPERTY, True)
    legend.refresh()


def _legend_labels(layer: QgsMapLayer) -> tuple[str, ...]:
    """The labels of the entries `layer` adds to a legend under its name --
    one per class of a categorized or graduated layer."""
    layer_legend = layer.legend()
    if layer_legend is None:
        return ()
    node = QgsLayerTreeLayer(layer)
    return tuple(
        str(legend_node.data(Qt.ItemDataRole.DisplayRole) or "")
        for legend_node in layer_legend.createLayerTreeModelLegendNodes(node)
    )


def _fit_legend(legend: QgsLayoutItemLegend, min_pt: float) -> int:
    """Make every row of `legend` fit the box its template planned, which
    the builder keeps fixed: QGIS clips a legend at its frame, so a layer
    with many classes would lose rows off the bottom. More columns first,
    then smaller text, symbols and spacing -- but never row text below
    `min_pt`, the template's readable floor -- and never a bigger box. A
    legend that already fits is left as it is.

    A legend that does not fit even at `min_pt` leaves out entries from the
    end of its list until it does (`_trim_legend`), rather than clip them
    silently or shrink out of reading: returns how many, and marks the
    legend with the count (`LEGEND_LEFT_OUT_PROPERTY`) for the checks."""
    box = legend.sizeWithUnits()
    context = QgsLayoutUtils.createRenderContextForLayout(legend.layout(), None)

    def measure() -> tuple[bool, bool]:
        """Whether the rows fit the box across, and down."""
        size = QgsLegendRenderer(legend.model(), legend.legendSettings()).minimumSize(
            context
        )
        return size.width() <= box.width(), size.height() <= box.height()

    if all(measure()):
        return 0
    # A class list may run on into the next column, or columns cannot help
    # a legend of one categorized layer.
    legend.setSplitLayer(True)
    base_columns = legend.columnCount()
    defaults = _LegendSizes.read(legend)
    label_pt = defaults.text[-1]
    least = min(1.0, min_pt / label_pt) if label_pt > 0 else 1.0

    def settle() -> bool:
        """Whether the rows fit in some number of columns, which is left
        set: the fewest that fit or, failing that, the most that still fit
        across, to leave as little as possible past the edge."""
        columns = base_columns
        while True:
            legend.setColumnCount(columns)
            across, down = measure()
            if across and down:
                return True
            if not across or columns >= _LEGEND_MAX_COLUMNS:
                break
            columns += 1
        legend.setColumnCount(max(base_columns, columns - (0 if across else 1)))
        return False

    scale = 1.0
    while True:
        defaults.apply(legend, scale)
        if settle():
            legend.refresh()
            return 0
        if scale <= least + 1e-9:
            break
        scale = max(least, round(scale - _LEGEND_SCALE_STEP, 4))
    left_out = _trim_legend(legend, settle)
    legend.setCustomProperty(LEGEND_LEFT_OUT_PROPERTY, left_out)
    legend.refresh()
    return left_out


def _trim_legend(legend: QgsLayoutItemLegend, fits: Callable[[], bool]) -> int:
    """Keep only as many of `legend`'s entries, from the top of its list,
    as `fits()` allows, and return how many were left out. An entry is a
    class row, or a layer's one row; whole layers go from the end of the
    list first, then a class list is cut short -- to two rows at least,
    since QGIS draws a class list of one row as the layer's own row, which
    would label the class with the layer's name. The legend stays an
    ordinary QGIS legend: a layer left out is gone from its item list and a
    class left out is off its node order, both put back in the legend's
    item properties."""
    model = legend.model()
    root = model.rootGroup()
    originals = [child.clone() for child in root.children()]
    entries = [max(len(model.layerLegendNodes(node)), 1) for node in root.findLayers()]
    total = sum(entries)

    def keep(count: int) -> int:
        """Rebuild the legend from its original entries and keep the first
        `count` of them: how many it keeps."""
        root.removeAllChildren()
        root.insertChildNodes(0, [child.clone() for child in originals])
        left = count
        kept = 0
        for node, own in zip(root.findLayers(), entries):
            if left >= own:
                left -= own
                kept += own
            elif own > 1 and left >= 2:
                QgsMapLayerLegendUtils.setLegendNodeOrder(node, list(range(left)))
                model.refreshLayerLegend(node)
                kept += left
                left = 0
            else:
                parent = node.parent()
                if parent is not None:
                    parent.removeChildNode(node)
                left = 0
        return kept

    # The whole list does not fit: the most entries that do, by halving.
    fitting, short = 0, total
    while short - fitting > 1:
        middle = (fitting + short) // 2
        keep(middle)
        if fits():
            fitting = middle
        else:
            short = middle
    kept = keep(fitting)
    fits()
    return total - kept


@dataclass(frozen=True)
class _LegendSizes:
    """A legend's own text sizes, margins, symbol size and spacing, to draw
    it at a share of them."""

    text: tuple[float, ...]
    margins: tuple[tuple[float, ...], ...]
    symbol: tuple[float, float]
    spacing: tuple[float, float]

    @classmethod
    def read(cls, legend: QgsLayoutItemLegend) -> _LegendSizes:
        styles = [legend.rstyle(style) for style in _LEGEND_TEXT_STYLES]
        return cls(
            text=tuple(style.textFormat().size() for style in styles),
            margins=tuple(
                tuple(style.margin(side) for side in _LEGEND_SIDES) for style in styles
            ),
            symbol=(legend.symbolWidth(), legend.symbolHeight()),
            spacing=(legend.boxSpace(), legend.columnSpace()),
        )

    def apply(self, legend: QgsLayoutItemLegend, scale: float) -> None:
        for style_id, size, margins in zip(
            _LEGEND_TEXT_STYLES, self.text, self.margins
        ):
            style = legend.rstyle(style_id)
            text_format = style.textFormat()
            text_format.setSize(size * scale)
            style.setTextFormat(text_format)
            for side, margin in zip(_LEGEND_SIDES, margins):
                style.setMargin(side, margin * scale)
        legend.setSymbolWidth(self.symbol[0] * scale)
        legend.setSymbolHeight(self.symbol[1] * scale)
        legend.setBoxSpace(self.spacing[0] * scale)
        legend.setColumnSpace(self.spacing[1] * scale)


def _frame(
    slot: FrameSlot,
    extent: QgsRectangle,
    rotation: float,
    crs: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> FrameSpec:
    """The frame for `slot`: the canvas view (a locator's zoomed out about
    the same centre), as four corners in the canvas CRS."""
    view = QgsRectangle(extent)
    if slot.zoom_out != 1.0:
        view.scale(slot.zoom_out)
    elif slot.focus is not None:
        view = _view_around_focus(view, slot.box, slot.focus, rotation)
    return FrameSpec(
        id=slot.id,
        box=slot.box,
        mode="static",
        crs=crs.authid() or None,
        crs_def=crs.toProj() or None,
        corners=_corners_wgs84(view, rotation, crs, project),
        # A placeholder, never written: the frame only carries the view here,
        # and the export renders its real raster from the layout (see the
        # `CanvasCartograph.spec` note).
        image_bytes=b"",
    )


def _view_around_focus(
    view: QgsRectangle, box: Box, focus: Box, rotation: float
) -> QgsRectangle:
    """The view the whole frame `box` shows so that `view` -- the canvas --
    fits, centred, in its `focus` part: the same scale, more of the
    surroundings on the side the design covers with a panel. Sizes are in
    the frame's own axes, turned by `rotation` like the frame's corners."""
    scale = max(view.width() / focus.w, view.height() / focus.h)
    # Page mm run down the sheet; map units run up it.
    dx = (box.x + box.w / 2 - (focus.x + focus.w / 2)) * scale
    dy = -(box.y + box.h / 2 - (focus.y + focus.h / 2)) * scale
    angle = math.radians(rotation)
    cx = view.center().x() + dx * math.cos(angle) - dy * math.sin(angle)
    cy = view.center().y() + dx * math.sin(angle) + dy * math.cos(angle)
    half_w, half_h = box.w * scale / 2, box.h * scale / 2
    return QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


#: The project variables a design's key figures come from, in order: each
#: "value | what it is" -- the user's own numbers, never computed here.
FIGURE_VARIABLES = ("nika_figure_1", "nika_figure_2", "nika_figure_3")


def project_figures(project: QgsProject) -> tuple[tuple[str, str], ...]:
    """The key figures the user set as project variables (`FIGURE_VARIABLES`),
    as (value, label) pairs; one with no value is skipped."""
    return tuple(row for row in project_figure_rows(project) if row[0])


def project_figure_rows(project: QgsProject) -> tuple[tuple[str, str], ...]:
    """Every one of `FIGURE_VARIABLES` as a (value, label) pair, in order,
    ("", "") where it is unset -- what the picker's rows show. A label with
    no value is kept here, for the user to finish; `project_figures` and the
    page leave it out."""
    variables = project.customVariables()
    rows = []
    for name in FIGURE_VARIABLES:
        raw = str(variables.get(name) or "").strip()
        value, _, label = raw.partition("|")
        rows.append((value.strip(), label.strip()))
    return tuple(rows)


def set_project_figures(project: QgsProject, rows: Sequence[tuple[str, str]]) -> None:
    """Write `rows` -- (value, label) pairs, in order -- to
    `FIGURE_VARIABLES` in the "value | label" form `project_figure_rows`
    reads, so they are saved with the project. A blank row, or one past the
    end of `rows`, removes its variable; a value alone is written without
    the bar.

    Raises `ValueError` for a value with a "|" in it: it would read back
    split at the bar."""
    variables = dict(project.customVariables())
    for index, name in enumerate(FIGURE_VARIABLES):
        value, label = rows[index] if index < len(rows) else ("", "")
        value, label = value.strip(), label.strip()
        if "|" in value:
            raise ValueError(f"a key figure's value cannot contain '|': {value!r}")
        if not (value or label):
            variables.pop(name, None)
        else:
            variables[name] = f"{value} | {label}".strip() if label else value
    project.setCustomVariables(variables)


#: The project variables the title, legend and notes size steps are kept in
#: (the step ids of `TEXT_SIZE_STEPS`); unset is Standard.
TEXT_SIZE_VARIABLES = ("nika_size_title", "nika_size_legend", "nika_size_notes")


def project_text_sizes(project: QgsProject) -> TextSizes:
    """The title, legend and notes size steps saved in the project
    (`TEXT_SIZE_VARIABLES`); Standard for any that is unset."""
    variables = project.customVariables()
    title, legend, notes = (
        str(variables.get(name) or "").strip() or STANDARD_STEP
        for name in TEXT_SIZE_VARIABLES
    )
    return TextSizes(title, legend, notes)


def set_project_text_sizes(project: QgsProject, sizes: TextSizes) -> None:
    """Save `sizes` to `TEXT_SIZE_VARIABLES`; Standard removes its variable,
    so a project that never changed a size carries none."""
    variables = dict(project.customVariables())
    for name, step in zip(
        TEXT_SIZE_VARIABLES, (sizes.title, sizes.legend, sizes.notes), strict=True
    ):
        if step == STANDARD_STEP:
            variables.pop(name, None)
        else:
            variables[name] = step
    project.setCustomVariables(variables)


def _credits(layers: Sequence[QgsMapLayer]) -> list[str]:
    credits = []
    for layer in layers:
        credit = read_attribution(layer)
        if credit:
            credits.append(credit)
    return credits


def _corners_wgs84(
    extent: QgsRectangle,
    rotation: float,
    crs: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> tuple[
    tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]
]:
    """The view's tl/tr/br/bl corners in lon/lat, in the order and rotation
    sense `exporters/frame_raster.py` reads them from `visibleExtentPolygon()`
    (the top edge turns counter-clockwise in map units by `rotation`)."""
    cx, cy = extent.center().x(), extent.center().y()
    half_w, half_h = extent.width() / 2, extent.height() / 2
    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    transform = QgsCoordinateTransform(crs, WGS84, project)
    corners = []
    for dx, dy in (
        (-half_w, half_h),
        (half_w, half_h),
        (half_w, -half_h),
        (-half_w, -half_h),
    ):
        x = cx + dx * cos_a - dy * sin_a
        y = cy + dx * sin_a + dy * cos_a
        point = transform.transform(QgsPointXY(x, y))
        corners.append((point.x(), point.y()))
    return corners[0], corners[1], corners[2], corners[3]


# --------------------------------------------------------------------------
# What a preview depends on: the template picker's cache key
# --------------------------------------------------------------------------


class LayerRevisions:
    """How many times each vector layer's features changed (one added,
    moved, deleted or edited, in the edit buffer or by rolling it back)
    since it was first `track`ed: an edit leaves the layer's style and
    source as they were, so only this tells a preview drawn before it from
    one drawn after. QGIS signals none of these as `dataChanged`, so the
    edit buffer's own signals are counted."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._tracked: set[str] = set()

    def track(self, layers: Sequence[QgsMapLayer]) -> dict[str, int]:
        """Start counting `layers` not counted yet; each one's count now."""
        for layer in layers:
            layer_id = layer.id()
            if layer_id in self._tracked or not isinstance(layer, QgsVectorLayer):
                continue
            self._tracked.add(layer_id)
            bump = functools.partial(self._bump, layer_id)
            for signal in (layer.layerModified, layer.afterRollBack, layer.dataChanged):
                signal.connect(bump)
            layer.destroyed.connect(functools.partial(self._forget, layer_id))
        return {layer.id(): self._counts.get(layer.id(), 0) for layer in layers}

    def _bump(self, layer_id: str, *_args: object) -> None:
        self._counts[layer_id] = self._counts.get(layer_id, 0) + 1

    def _forget(self, layer_id: str, *_args: object) -> None:
        self._tracked.discard(layer_id)
        self._counts.pop(layer_id, None)


def preview_state_key(
    extent: QgsRectangle,
    crs: QgsCoordinateReferenceSystem,
    layers: Sequence[QgsMapLayer],
    project: QgsProject,
    *,
    rotation: float = 0.0,
    revisions: Mapping[str, int] | None = None,
) -> str:
    """A digest of everything a template preview of this view draws, apart
    from the page settings and figures the picker keys by itself: the
    extent, CRS and rotation; each layer in order -- its id, name, source,
    whole style (symbology, labels, opacity, blend), credit, filter,
    feature count and data `revisions`; the project's title, abstract,
    author and key figure variables; today's date (two designs print it);
    and the set of designs. Any of them changing changes the key, so a
    page drawn before is never shown after."""
    parts = [
        repr(
            (
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            )
        ),
        crs.authid(),
        crs.toWkt(),
        repr(float(rotation)),
        project.title(),
        project.metadata().abstract(),
        project.metadata().author(),
        repr(sorted(project_figure_rows(project))),
        date.today().isoformat(),
        repr(tuple(template.id for template in layout_templates.TEMPLATES)),
    ]
    for layer in layers:
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        parts += [
            layer.id(),
            layer.name(),
            layer.source(),
            style.xmlData(),
            read_attribution(layer) or "",
            str((revisions or {}).get(layer.id(), 0)),
        ]
        if isinstance(layer, QgsVectorLayer):
            parts += [layer.subsetString(), str(layer.featureCount())]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
