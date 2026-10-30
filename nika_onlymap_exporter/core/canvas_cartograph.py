"""One-click mode, the PyQGIS half: the map canvas -> a cartograph spec AND a
real `QgsPrintLayout` in the project.

Issue #1 section 4, step 5 is the differentiator this module exists for: the
canvas is not exported straight to HTML, it becomes a print layout named
"<design> · <project title>" that the user can keep editing in QGIS -- and the
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
import time
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
    QgsRectangle,
    QgsRendererCategory,
    QgsTextRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QObject, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QPainter

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
    FrameSlot,
    TemplateInput,
    TemplatePlan,
    TextSizes,
    get_template,
    plan_template,
    template_spec,
    text_percent,
)
from .legend_edit import apply_template_defaults
from .locator_context import (
    LocatorView,
    draw_quietly,
    fit_to_aspect,
    has_wider_context,
    locator_view,
    mark_context,
)

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: The main frame's id in the spec and the map item's id in the layout.
FRAME_ID = MAIN_FRAME_ID
#: What the layout is called after, when the project has no title.
_UNTITLED = "Untitled map"
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
#: The `QImage.text()` key a template preview carries "1" under when its
#: locator inset has no wider context to show (`locator_lacks_context`),
#: else "0": the picker says so beside the thumbnail.
PREVIEW_LOCATOR_NO_CONTEXT_KEY = "locator-no-context"
#: Only a bound for the search; the box's width stops it long before.
_LEGEND_MAX_COLUMNS = 12
_LEGEND_TEXT_STYLES = (
    QgsLegendStyle.Style.Title,
    QgsLegendStyle.Style.Group,
    QgsLegendStyle.Style.Subgroup,
    QgsLegendStyle.Style.SymbolLabel,
)
_LEGEND_SIDES = (
    QgsLegendStyle.Side.Top,
    QgsLegendStyle.Side.Bottom,
    QgsLegendStyle.Side.Left,
    QgsLegendStyle.Side.Right,
)


def template_layout_name(
    design_name: str, title: str, untitled: str = _UNTITLED
) -> str:
    """ "<project title> — <design>" - what a layout made from a template is
    called, so the list says both which map and which design. A project
    without a title says `untitled` instead. This is the layout's name in the
    project, an organising label: the cartograph's own title is separate and a
    later edit of it never renames the layout."""
    return f"{title.strip() or untitled} \u2014 {design_name}"


@dataclass(frozen=True)
class CanvasCartograph:
    """The one-click result.

    - `layout` is already in the project's layout manager, uniquely named
      "<design> · <title>" -- select it as the cartograph's source.
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
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
    layout_name: str | None = None,
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
        figures=figures,
        text_sizes=text_sizes,
        layout_name=layout_name,
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
    figures: Sequence[tuple[str, str]] | None = None,
    text_sizes: TextSizes | None = None,
    layout_name: str | None = None,
) -> CanvasCartograph:
    """`cartograph_from_canvas` for a view given as plain values -- the same
    thing without a widget, for callers (and tests) that have no canvas.

    `extent` is the *unrotated* extent in `crs` (`QgsMapSettings.extent()`),
    `rotation` the canvas rotation in degrees, `layers` top layer first.
    Raises `ValueError` for an empty or non-finite extent, an invalid CRS or
    an unknown template, page size or orientation.
    """
    # Figures are only what the caller gives: a new page reads nothing from
    # the project (older versions kept key figures in project variables; a
    # layout made then keeps its figures as ordinary text). Key numbers on a
    # new page are Live counts, added with Add item.
    drawn = tuple(figures or ())
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
        figures=drawn,
        text_sizes=text_sizes,
        layout_name=layout_name,
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
            figures=drawn,
        ),
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


#: The layout custom property a layout made from a template carries: the
#: view and choices it was built from (JSON, `TemplateRecord`).
TEMPLATE_PROPERTY = "nika/template"


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


def _record_template(layout: QgsPrintLayout, record: TemplateRecord) -> None:
    layout.setCustomProperty(TEMPLATE_PROPERTY, json.dumps(dataclasses.asdict(record)))


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
    prepared: TemplateInput | None = None,
) -> QImage:
    """The page `template_id` would make of this view, drawn by QGIS
    `width_px` wide -- the template picker's thumbnail. The layout is built
    outside the project and thrown away; nothing is added to it.

    `figures` are the key figures as the picker's fields hold them, not yet
    saved; `None` draws none. `text_sizes` is
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
            prepared=prepared,
        )
        height_px = max(1, round(width_px * plan.page_height / plan.page_width))
        no_context = "1" if locator_lacks_context(built.layout) else "0"
        image = QgsLayoutExporter(built.layout).renderPageToImage(
            0, QSize(width_px, height_px)
        )
    image.setText(PREVIEW_LEGEND_LEFT_OUT_KEY, str(left_out))
    image.setText(PREVIEW_LOCATOR_NO_CONTEXT_KEY, no_context)
    return image


def locator_lacks_context(layout: QgsLayout) -> bool:
    """Whether the layout has a locator inset whose layers reach no further
    than the map it outlines (`core/locator_context.py`)."""
    return any(
        isinstance(item, QgsLayoutItemMap) and has_wider_context(item) is False
        for item in layout.items()
    )


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
        self._no_context = "1" if locator_lacks_context(built.layout) else "0"
        self._waiting = {
            id(item)
            for item in built.layout.items()
            if isinstance(item, QgsLayoutItemMap)
        }
        self._done = False
        self._retired_at = 0.0
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
        return self._layout is not None and not self._done

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
            if self in _RETIRING:
                # Cancelled and now idle: let go of it after this signal
                # has returned, not inside the map item that sent it.
                QTimer.singleShot(0, self._retired_idle)
            else:
                self._finish()

    def _retired_idle(self) -> None:
        if self in _RETIRING:
            _RETIRING.remove(self)
        self._release()

    def _finish(self) -> None:
        if self._done or self._layout is None:
            return
        self._done = True
        image = self._paint()
        image.setText(PREVIEW_LEGEND_LEFT_OUT_KEY, str(self._left_out))
        image.setText(PREVIEW_LOCATOR_NO_CONTEXT_KEY, self._no_context)
        self._release()
        self.finished.emit(image)

    def cancel(self) -> None:
        """Stop wanting the page: no result is emitted from here on.

        QGIS cannot be asked from Python to abandon a map's render job
        without waiting for it: deleting a layout whose map is still
        drawing blocks the GUI thread until the job has stopped, and
        a slow layer only stops between two of its features. So a page that
        is still drawing is not deleted here. It is kept (`_RETIRING`)
        until its maps have reported, then dropped with nothing left to
        wait for, and `render_slot_busy()` tells callers not to start
        another heavy page meanwhile."""
        self._done = True
        if self._layout is not None and self._waiting:
            self._retired_at = time.monotonic()
            _RETIRING.append(self)
        else:
            self._release()

    def _release(self) -> None:
        layout, self._layout = self._layout, None
        if layout is not None:
            with self._project_kept():
                pass


#: Cancelled pages whose maps are still drawing: kept alive until they have
#: reported (`PendingPreview.cancel`). Normally empty or one long, as only one
#: page is drawn at a time and the next waits for this to be empty.
_RETIRING: list[PendingPreview] = []
#: A page that has not reported this long after being cancelled is given up
#: on: dropped, whatever the wait for the GUI thread.
_RETIRE_LIMIT_S = 120.0


def render_slot_busy() -> bool:
    """Whether a cancelled page is still drawing in the background, and so
    holds the render slot: a new page waits, so abandoned work never piles up
    behind the one the user wants."""
    now = time.monotonic()
    for pending in list(_RETIRING):
        if now - pending._retired_at > _RETIRE_LIMIT_S:
            _RETIRING.remove(pending)
            pending._release()
    return bool(_RETIRING)


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
    prepared: TemplateInput | None = None,
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
            prepared=prepared,
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
    and the picker's check of what each page includes. `figures` are only those
    given; `text_sizes` `None` takes the project's (`project_text_sizes`)."""
    from .template_framing import project_framing

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
        figures=tuple(figures or ()),
        extent_center=(extent.center().x(), extent.center().y()),
        fill_frame=project_framing(project) == "fill",
    )
    sizes = project_text_sizes(project) if text_sizes is None else text_sizes
    return sizes.apply(data)


def prepared_input(
    prepared: TemplateInput,
    project: QgsProject,
    page_size: str,
    orientation: str,
) -> TemplateInput:
    """`view_input` for another page setting, from an input prepared once
    for this view: the layers' legend labels, credits and text are read
    once per picker, not once per page drawn. Only what a page setting
    changes is replaced -- the size, the turn and the map framing, which the
    picker varies while the view stays."""
    from .template_framing import project_framing

    return dataclasses.replace(
        prepared,
        page_size=page_size,
        orientation=orientation,
        fill_frame=project_framing(project) == "fill",
    )


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
    layout_name: str | None = None,
    prepared: TemplateInput | None = None,
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
            layout_name=layout_name,
            prepared=prepared,
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
    layout_name: str | None = None,
    prepared: TemplateInput | None = None,
) -> _Built:
    if not crs.isValid():
        raise ValueError("the canvas has no valid CRS")
    if extent.isNull() or not extent.isFinite() or extent.isEmpty():
        raise ValueError("the canvas extent is empty")

    title = project.title().strip()
    plan = plan_template(
        template_id,
        prepared_input(prepared, project, page_size, orientation)
        if prepared is not None
        else view_input(
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
    frames: dict[str, FrameSpec] = {}
    locators: dict[str, LocatorView] = {}
    main_view: QgsRectangle | None = None
    for slot in plan.frames:
        spec_frame, view, locator = _frame(
            slot, extent, rotation, crs, project, layers, main_view
        )
        frames[slot.id] = spec_frame
        if slot.id == FRAME_ID:
            main_view = view
        if locator is not None:
            locators[slot.id] = locator
    spec = template_spec(
        plan,
        frames,
        cartograph_id=_ID_SANITIZE_RE.sub("-", title).strip("-") or "cartograph",
    )
    built = build_layout(
        spec,
        project,
        name=layout_name or template_layout_name(get_template(template_id).name, title),
        frame_layers={
            slot.id: list(locators[slot.id].layers)
            if slot.id in locators
            else list(layers)
            for slot in plan.frames
        },
        add_to_project=add_to_project,
    )
    for slot_id, locator in locators.items():
        inset = built.items_by_id[slot_id]
        if isinstance(inset, QgsLayoutItemMap):
            draw_quietly(inset)
            mark_context(inset, locator.has_context)
    left_out = 0
    for item in plan.items:
        if isinstance(item, LegendSpec):
            legend = built.items_by_id[item.id]
            if isinstance(legend, QgsLayoutItemLegend):
                apply_template_defaults(legend)
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
    text_format = legend.rstyle(QgsLegendStyle.Style.Subgroup).textFormat()
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
    layers: Sequence[QgsMapLayer] = (),
    main_view: QgsRectangle | None = None,
) -> tuple[FrameSpec, QgsRectangle, LocatorView | None]:
    """The frame for `slot` as four corners in the canvas CRS, the view it
    shows, and for a locator inset what it draws and shows of the wider
    context (`core/locator_context.py`). `main_view` is what the main map
    shows, which an inset outlines and shows the surroundings of."""
    view = QgsRectangle(extent)
    locator: LocatorView | None = None
    if slot.overview_of is not None and slot.zoom_out != 1.0:
        locator = locator_view(
            QgsRectangle(main_view if main_view is not None else extent),
            crs,
            layers,
            project,
            slot.box.w / slot.box.h,
        )
        view = locator.extent
    elif slot.focus is not None:
        view = _view_around_focus(view, slot.box, slot.focus, rotation)
    if slot.id == FRAME_ID:
        from .template_framing import fill_view, project_framing

        if project_framing(project) == "fill":
            view = fill_view(
                view, slot.box.w / slot.box.h, crs, layers, project, rotation
            )
    if slot.id == FRAME_ID:
        # What the map item shows is the view padded out to the frame's
        # shape, which is what an inset outlines.
        view = fit_to_aspect(view, slot.box.w / slot.box.h)
    spec = FrameSpec(
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
    return spec, view, locator


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


#: The project variables the title, legend and notes size steps are kept in
#: (whole percent; older versions stored a step id, still read); unset is 100.
TEXT_SIZE_VARIABLES = ("nika_size_title", "nika_size_legend", "nika_size_notes")


def project_text_sizes(project: QgsProject) -> TextSizes:
    """The title, legend and notes size steps saved in the project
    (`TEXT_SIZE_VARIABLES`); 100 for any that is unset."""
    variables = project.customVariables()
    title, legend, notes = (
        text_percent(variables.get(name)) for name in TEXT_SIZE_VARIABLES
    )
    return TextSizes(title, legend, notes)


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
    author and text size variables; today's date (two designs print it);
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
        repr(project_text_sizes(project)),
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
