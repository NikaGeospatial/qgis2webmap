"""The one entry point for turning a `QgsPrintLayout` into a cartograph.

Both the dialog and the `exportlayout` Processing algorithm call this single
function, the same way `writers/onlymap_writer.py`'s `write`/`write_preview`
are the one path for the web-map export (this module's docstring reason is
the same one `writers/onlymap_writer.py` gives: a second implementation is
how a fix gets made in one place and not the other).

It is the glue the architecture diagram in `specs/cartograph/
qgis2carto-issues-1-5.md` section 1 draws between three things that each stay
pure on their own:

- `core/layout_reader.py` decides *what* every layout item is and *where* it
  goes, but a map item comes back as a `MapFrameReading`, not a finished
  `FrameSpec` -- it still needs either a raster or a rendered `<om-map>`.
- `exporters/frame_raster.py` renders a static frame's raster and corners.
- `writers/onlymap_writer.py` (qgis2webmap's own writer, reused rather than
  reimplemented) renders a live frame's `<om-map>` markup from the project's
  layers, restricted to the map item's own layer set via
  `layersToRender()`.

This module is where those three results get assembled into one
`CartographSpec` and handed to `writers/cartograph_writer.py`.

**Two halves, for threads.** `prepare_cartograph` does everything that reads
or changes the layout -- reading its items, hiding them for a static render,
rendering it -- and must run on the GUI thread: the layout is a graphics
scene that may be on screen in the layout designer, and QGIS's own layout
export algorithms are no-threading for the same reason. The layers a live
frame draws are read the way the web-map export reads them: `prepare_live_reads`
(`project_reader.prepare_project`) takes the live project's part on the GUI
thread, and `finish_cartograph` completes it from that snapshot - reading the
features and assembling the HTML - so the dialog runs it off the GUI thread
without handing the worker the project. `export_layout_to_cartograph` runs all
three, for callers already on one thread.

**Static rasters are cached.** Autosave rebuilds the whole cartograph after
every edit, and a static frame's region render is by far its slowest part.
`StaticFrameCache` keeps each frame's last render and reuses it while
nothing that could change its pixels has changed -- see `_static_frame_key`
for what that means, and `clear_static_frame_cache` to drop it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsMapLayerStyle,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..exporters.frame_raster import (
    AsyncFrameRender,
    StaticFrameRender,
    frame_corners_wgs84,
    render_static_frame,
)
from ..packaging.dependency_scanner import (
    data_bytes_by_layer,
    report_remote_dependency,
)
from ..packaging.raster_staging import stage_rasters
from ..packaging.runtime_manager import RuntimeBundle
from ..ui.i18n import tr
from ..writers.cartograph_writer import CartographRuntimeBundle, write_cartograph_html
from ..writers.onlymap_writer import OnlyMapWriter
from .cartograph_ir import (
    ATLAS_ORDER_KEY,
    AtlasCamera,
    CartographItem,
    CartographSpec,
    FrameGeoreference,
    FrameSpec,
    LegendSpec,
    element_id,
)
from .export_ir import (
    AssetDisposition,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    FidelityItem,
    OutputMode,
    RasterSpec,
)
from .export_resolution import frame_resolution
from .extent_math import extent_from_longitudes_latitudes
from .fidelity_report import FidelityReportBuilder
from .layer_reader import PendingLayer
from .layout_reader import (
    ATLAS_SUBJECT,
    AtlasRequirement,
    FrameMode,
    MapFrameReading,
    draws_in_own_plane,
    layout_item_tokens,
    layout_state_restored,
    left_out_detail,
    legend_left_out,
    read_layout,
)
from .legend_box_sync import apply_legend_changes
from .project_reader import (
    PendingProject,
    finish_project,
    prepare_project,
    read_project,
)
from .raster_swatch import raster_swatch
from .report_facts import format_bytes
from .runtime_legend import fit_frame_legends
from .runtime_legend_fit import (
    LegendBoxChange,
    fit_frame_legend_boxes,
    with_change_applied,
)
from .settings import LayerSettings
from .zoom_range import EQUATOR_METRES, WORLD_PIXELS_AT_ZOOM_0

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: A live frame is a map on a printed page: the page's own legend, scale bar
#: and north arrow do those jobs, and zoom buttons or a layer menu have no
#: business on paper. The web map's on-map widgets are all off.
_LIVE_FRAME_SETTINGS = ExportSettings(
    show_legend=False,
    show_layer_switcher=False,
    show_zoom_controls=False,
    show_scale_bar=False,
    show_title=False,
    show_abstract=False,
)

#: A CSS pixel, same definition `core/zoom_range.py` uses, so a live frame's
#: camera is measured in the runtime's own units.
_CSS_PIXELS_PER_MM = 96.0 / 25.4

_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class ProjectedPlane:
    """A live frame that can draw in its map's own projected CRS.

    `center`/`zoom` are the frame's camera in that plane, by the runtime's
    own definition (`plane-camera.ts`): the camera stays geographic, and
    zoom means the Web Mercator scale at the centre latitude, so the camera
    here is the plane extent's centre and the zoom that gives the plane
    extent's metres per pixel. `atlas_cameras` likewise, per atlas page.
    Whether the frame's layers can draw in a plane is only known once they
    are read, so `finish_cartograph` decides between this and the frame's
    Web Mercator camera, and reports which (`subject` is the frame's row).
    """

    subject: str
    item_id: str  # the map item's uuid, which its report rows carry
    crs: str
    crs_def: str
    center: tuple[float, float]
    zoom: float
    atlas_cameras: tuple[AtlasCamera, ...] = ()


@dataclass(frozen=True)
class LiveFrameSource:
    """What a live frame still needs read once the layout is out of reach:
    which layers it draws, in its own order, over which WGS84 extent.

    `layer_dom_ids` maps a QGIS layer id to the `<om-layer id>` it gets in
    this frame (they differ when two live frames show the same layer, see
    `cartograph_ir.live_layer_dom_ids`); `hidden_layer_ids` are exported with
    `visible="false"` (the atlas coverage layer under "Hide coverage layer").
    """

    frame_id: str
    layer_order: tuple[str, ...]
    extent: Extent
    layer_dom_ids: tuple[tuple[str, str], ...] = ()
    hidden_layer_ids: tuple[str, ...] = ()
    plane: ProjectedPlane | None = None


@dataclass(frozen=True)
class PreparedCartograph:
    """A cartograph with every layout-dependent part done.

    `items` holds the furniture and every frame's `FrameSpec` (static ones
    with their raster); `live_frames` lists what `finish_cartograph` still has
    to render into `<om-map>` markup. Nothing here refers to the layout.
    """

    spec: CartographSpec
    live_frames: tuple[LiveFrameSource, ...]
    bundle: CartographRuntimeBundle
    # Checked once the coverage layer is read (`finish_cartograph`).
    atlas_requirement: AtlasRequirement | None = None
    # One per live frame, in order, from `prepare_live_reads`; empty means
    # `finish_cartograph` reads the project itself, on its own thread.
    live_reads: tuple[PendingProject, ...] = ()

    @property
    def items(self) -> tuple[CartographItem, ...]:
        return self.spec.items


@dataclass(frozen=True)
class FinishedCartograph:
    """The written document and, for each live frame in document order, the
    `ExportProject` its `<om-map>` was rendered from -- as read, so layer
    ids are QGIS's own (the `<om-layer id>` may carry a frame prefix, see
    `LiveFrameSource.layer_dom_ids`). Empty for a static-only cartograph.
    What the colour-vision check reads its live colours from."""

    html: str
    projects: tuple[ExportProject, ...] = ()
    # The coverage layer's feature ids the written `<om-atlas>` pages, in page
    # order; `None` when the document carries no atlas (off, blocked, or its
    # coverage layer did not survive the export), `()` for an atlas that
    # matches nothing.
    atlas_pages: tuple[int, ...] | None = None


class StaticFrameCache:
    """The last raster of each static frame, reused while it is still right.

    One entry per (layout, map item): a new render for the same frame
    replaces the old one, so memory stays at one raster per frame on screen
    rather than growing with every edit. `max_frames` bounds it across
    layouts too.

    Correctness rests on the key (`_static_frame_key`) plus a per-layer
    generation counter bumped by the layer's own change signals -- the
    same signals QGIS's map canvas cache invalidates on. A stale raster
    after a restyle or an edit would be a silent wrong picture, so anything
    that can change the pixels is in one or the other.
    """

    def __init__(self, max_frames: int = 32) -> None:
        self.max_frames = max_frames
        self.render_count = 0
        self._entries: dict[tuple[str, str], tuple[str, StaticFrameRender]] = {}
        self._generations: dict[str, int] = {}
        self._watched: dict[
            str, tuple[QgsMapLayer, Callable[..., None], Callable[..., None]]
        ] = {}

    def render(
        self,
        layout: QgsPrintLayout,
        map_item: QgsLayoutItemMap,
        dpi: int,
        project: QgsProject,
    ) -> StaticFrameRender:
        entry, cached = self.lookup(layout, map_item, dpi, project)
        if cached is not None:
            return cached
        render = render_static_frame(layout, map_item, dpi=dpi)
        self.store(entry, render)
        return render

    def lookup(
        self,
        layout: QgsPrintLayout,
        map_item: QgsLayoutItemMap,
        dpi: int,
        project: QgsProject,
    ) -> tuple[tuple[tuple[str, str], str], StaticFrameRender | None]:
        """The cache entry a render of this frame now would fill - the frame's
        slot and the digest of everything its pixels depend on - and the raster
        already held for it, if still right. A caller that draws the frame
        itself (see `AsyncFrameRender`) gives the entry back to `store`."""
        for layer in map_item.layersToRender():
            self._watch(layer)
        key = _static_frame_key(layout, map_item, dpi, project, self._generations)
        slot = (layout.name(), map_item.uuid())
        cached = self._entries.get(slot)
        if cached is not None and cached[0] == key:
            return (slot, key), cached[1]
        return (slot, key), None

    def store(
        self, entry: tuple[tuple[str, str], str], render: StaticFrameRender
    ) -> None:
        """Keep `render` as the raster for the entry `lookup` gave."""
        slot, key = entry
        self.render_count += 1
        self._entries.pop(slot, None)
        self._entries[slot] = (key, render)
        while len(self._entries) > self.max_frames:
            del self._entries[next(iter(self._entries))]

    def clear(self) -> None:
        """Forget every raster and stop listening to every layer."""
        self._entries.clear()
        for layer, bump, forget in self._watched.values():
            # A layer QGIS already deleted (a project closing deletes its
            # layers before `cleared`) raises on any attribute, signals
            # included; its connections went with it.
            with contextlib.suppress(TypeError, RuntimeError):
                connections = [
                    (signal, bump) for signal in _layer_change_signals(layer)
                ]
                connections.append((layer.willBeDeleted, forget))
                for signal, slot in connections:
                    with contextlib.suppress(TypeError, RuntimeError):
                        signal.disconnect(slot)
        self._watched.clear()
        self._generations.clear()

    def _watch(self, layer: QgsMapLayer) -> None:
        layer_id = layer.id()
        if layer_id in self._watched:
            return

        def bump(*_args: object) -> None:
            self._generations[layer_id] = self._generations.get(layer_id, 0) + 1

        def forget(*_args: object) -> None:
            bump()
            self._watched.pop(layer_id, None)

        for signal in _layer_change_signals(layer):
            signal.connect(bump)
        layer.willBeDeleted.connect(forget)
        self._watched[layer_id] = (layer, bump, forget)


def _layer_change_signals(layer: QgsMapLayer) -> list:
    """Every signal after which a layer may draw differently: its data (edits,
    commits, a provider reload), its style, and any repaint request (a
    timed refresh, a canvas-wide redraw after a change we cannot see)."""
    signals = [
        layer.dataChanged,
        layer.repaintRequested,
        layer.styleChanged,
        layer.rendererChanged,
    ]
    if isinstance(layer, QgsVectorLayer):
        signals.append(layer.subsetStringChanged)
    return signals


_DEFAULT_CACHE = StaticFrameCache()


def clear_static_frame_cache() -> None:
    """Drop every cached static-frame raster (and its layer listeners) --
    for a project being closed, or a caller that wants a guaranteed fresh
    render."""
    _DEFAULT_CACHE.clear()


def _static_frame_key(
    layout: QgsPrintLayout,
    map_item: QgsLayoutItemMap,
    dpi: int,
    project: QgsProject,
    generations: Mapping[str, int],
) -> str:
    """A digest of everything a static frame's pixels depend on.

    - The map item's own serialised state: extent, rotation, CRS, size,
      locked layers and their style overrides, map theme name, frame and
      background, label settings -- `writeXml` covers every property the
      item has, so a new one QGIS adds is covered too.
    - The DPI the raster is rendered at.
    - For each layer it draws (in order): its full style (renderer,
      labelling, opacity, blending -- `QgsMapLayerStyle`), its feature count
      and extent (edits made below the edit buffer, through the provider,
      raise no layer signal but usually move one of these), and its
      generation counter from the layer's change signals.
    - The map theme's own style overrides, when the item follows a theme:
      the item stores only the theme's name.
    """
    digest = hashlib.sha256()

    def add(text: str) -> None:
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")

    add(layout.name())
    add(map_item.uuid())
    add(str(dpi))
    document = QDomDocument("cartograph-frame")
    element = document.createElement("frame")
    # Attached first: `toString()` serialises only what is in the document,
    # and an unattached element would make every item hash the same.
    document.appendChild(element)
    map_item.writeXml(element, document, QgsReadWriteContext())
    add(document.toString())
    for layer in map_item.layersToRender():
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        add(layer.id())
        add(style.xmlData())
        add(str(generations.get(layer.id(), 0)))
        if isinstance(layer, QgsVectorLayer):
            add(str(layer.featureCount()))
        add(layer.extent().toString())
    if map_item.followVisibilityPreset():
        theme = map_item.followVisibilityPresetName()
        overrides = project.mapThemeCollection().mapThemeStyleOverrides(theme)
        for layer_id in sorted(overrides):
            add(layer_id)
            add(overrides[layer_id])
    return digest.hexdigest()


def export_layout_to_cartograph(
    layout: QgsPrintLayout,
    project: QgsProject,
    runtime: RuntimeBundle,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
    frame_dpis: Mapping[str, int] | None = None,
    static_dpi: int = 200,
    export_dpi: int | None = None,
    title_override: str | None = None,
    writer: OnlyMapWriter | None = None,
    layer_settings: Mapping[str, LayerSettings] | None = None,
) -> str:
    """Read `layout` and render it to a standalone cartograph HTML document,
    all on the calling thread -- which must be the GUI thread (see the module
    docstring). `writer` as for `finish_cartograph_full`."""
    prepared = prepare_cartograph(
        layout,
        project,
        runtime,
        report,
        frame_modes=frame_modes,
        frame_dpis=frame_dpis,
        static_dpi=static_dpi,
        export_dpi=export_dpi,
        title_override=title_override,
    )
    prepared = prepare_live_reads(
        prepared, project, report, layer_settings=layer_settings
    )
    prepared = fit_legends_to_layout(layout, project, prepared, report)
    return finish_cartograph(
        prepared, project, report, writer=writer, layer_settings=layer_settings
    )


def _report_raster_swatches(
    spec: CartographSpec,
    frame_id: str,
    layers: Sequence[ExportLayer],
    report: FidelityReportBuilder,
) -> None:
    """Say, for each styled raster a derived legend lists, that the browser's
    legend shows it as one swatch where QGIS's lists its classes or ramp."""
    if not any(
        isinstance(item, LegendSpec) and item.derived and item.for_ == frame_id
        for item in spec.items
    ):
        return
    for layer in layers:
        swatch = raster_swatch(layer.raster.style_qml) if layer.raster else None
        if swatch is None or not layer.visible:
            continue
        listed = (
            "its colour ramp"
            if swatch.is_ramp
            else f"its {len(swatch.entries)} classes"
        )
        report.approximated(
            f"Legend '{layer.name}'",
            f"QGIS's legend lists {listed}; the browser's legend cannot list "
            "a raster's colours, so it shows one swatch in "
            + ("the ramp's" if swatch.is_ramp else "the first class's")
            + " colour.",
        )


def fit_legends_to_layout(
    layout: QgsPrintLayout,
    project: QgsProject,
    prepared: PreparedCartograph,
    report: FidelityReportBuilder,
) -> PreparedCartograph:
    """Fit each live frame's legends to the web page's legend, in the layout
    itself and in `prepared`. **GUI thread only**, after `prepare_live_reads`.

    The runtime's legend is taller than QGIS's, so a legend box that holds
    QGIS's may clip the web page's. Each such box is made taller, then set in
    more columns, then left without some layers (`runtime_legend_fit`), and
    the layout's legend item is changed the same way (`legend_box_sync`), so
    the layout preview, its prints and the page agree. `finish_cartograph`
    then finds the legends fitting and has nothing to do. Nothing is changed
    for a legend that fits, so a second call over the result changes nothing.
    """
    if not prepared.live_reads:
        return prepared
    spec = prepared.spec
    changes: list[LegendBoxChange] = []
    # The subject the layout reader gave each legend's rows, so a legend's
    # Checks rows sit together; a template's legend that left entries out has
    # a row saying so, which is rewritten below rather than doubled.
    tokens = layout_item_tokens(layout)
    subjects = {
        element_id("legend", item.uuid(), tokens): f"Legend '{item.displayName()}'"
        for item in layout.items()
        if isinstance(item, QgsLayoutItemLegend)
    }
    template_legends = {
        legend_id
        for legend_id, subject in subjects.items()
        if any(
            row.subject == subject and "The legend leaves out" in row.detail
            for row in report.items
        )
    }
    qgis_layer_ids: dict[str, dict[str, str]] = {}
    for source, pending in zip(prepared.live_frames, prepared.live_reads, strict=True):
        base_layers = [
            entry.base if isinstance(entry, PendingLayer) else entry
            for _name, entry in pending.entries
            if entry is not None
        ]
        layers = _layers_in_frame(base_layers, source)
        qgis_layer_ids[source.frame_id] = {
            dom_id: layer_id for layer_id, dom_id in source.layer_dom_ids
        }
        # The legends' rows in the runtime's terms, as the export will have
        # them, which is what the model reads; the changes are made to the
        # spec's own legends.
        keyed = fit_frame_legends(spec, source.frame_id, layers)
        found: list[LegendBoxChange] = []
        fit_frame_legend_boxes(
            keyed,
            source.frame_id,
            layers,
            report,
            grow_on_page_height=spec.height,
            changes=found,
            template_legends=template_legends,
            subjects=subjects,
        )
        spec = replace(
            spec,
            items=tuple(
                next(
                    (
                        with_change_applied(item, change)
                        for change in found
                        if isinstance(item, LegendSpec) and change.legend_id == item.id
                    ),
                    item,
                )
                for item in spec.items
            ),
        )
        changes.extend(found)
    changed = apply_legend_changes(layout, project, changes, qgis_layer_ids)
    for change in changes:
        legend = changed.get(change.legend_id)
        linked = legend.linkedMap() if legend is not None else None
        if change.omitted_layer_ids and change.legend_id in template_legends and linked:
            # One row for the legend: its count now includes what the page
            # needed left out as well.
            report.rewrite_detail(
                subjects[change.legend_id],
                "The legend leaves out",
                left_out_detail(legend_left_out(legend, linked)),
            )
    return replace(prepared, spec=spec)


def prepare_live_reads(
    prepared: PreparedCartograph,
    project: QgsProject,
    report: FidelityReportBuilder,
    *,
    layer_settings: Mapping[str, LayerSettings] | None = None,
) -> PreparedCartograph:
    """Read each live frame's layers off the live project. **GUI thread only.**

    The result carries a `PendingProject` per frame, so `finish_cartograph` can
    run on a worker holding no project or layer.
    """
    reads = tuple(
        prepare_project(
            project,
            report,
            layer_order=source.layer_order,
            canvas_extent=source.extent,
            layer_settings=layer_settings,
            settings=_LIVE_FRAME_SETTINGS,
            report_map_title=False,
        )
        for source in prepared.live_frames
    )
    return replace(prepared, live_reads=reads)


@dataclass
class PendingStaticFrame:
    """A static frame whose picture is still being drawn.

    `prepare_cartograph` starts the drawing (`AsyncFrameRender`) and leaves the
    frame's spec without its image; `with_static_images` puts the pictures in
    once `render` has finished and been encoded, and `remember_static_frames`
    keeps them in the frame cache."""

    frame_id: str
    render: AsyncFrameRender
    cache: StaticFrameCache | None
    cache_entry: tuple[tuple[str, str], str] | None


def with_static_images(
    prepared: PreparedCartograph, png_by_frame: Mapping[str, bytes]
) -> PreparedCartograph:
    """`prepared` with the pictures of its pending static frames put in."""
    items = tuple(
        replace(item, image_bytes=png_by_frame[item.id])
        if isinstance(item, FrameSpec) and item.id in png_by_frame
        else item
        for item in prepared.spec.items
    )
    return replace(prepared, spec=replace(prepared.spec, items=items))


def remember_static_frames(
    pending: Sequence[PendingStaticFrame], png_by_frame: Mapping[str, bytes]
) -> None:
    """Hand the finished pictures to the frame cache, as a synchronous render
    would have. GUI thread (the cache listens to layers there)."""
    for frame in pending:
        render = frame.render
        if frame.cache is None or frame.cache_entry is None:
            continue
        if render.corners is None or frame.frame_id not in png_by_frame:
            continue
        frame.cache.store(
            frame.cache_entry,
            StaticFrameRender(
                png_bytes=png_by_frame[frame.frame_id],
                corners=render.corners,
                crs_authid=render.crs_authid,
                crs_def=render.crs_def,
            ),
        )


def prepare_cartograph(
    layout: QgsPrintLayout,
    project: QgsProject,
    runtime: RuntimeBundle,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
    frame_dpis: Mapping[str, int] | None = None,
    static_dpi: int = 200,
    export_dpi: int | None = None,
    title_override: str | None = None,
    raster_cache: StaticFrameCache | None = _DEFAULT_CACHE,
    pending_static: list[PendingStaticFrame] | None = None,
) -> PreparedCartograph:
    """Everything that touches `layout`. GUI thread only.

    `frame_modes` and `frame_dpis` are keyed by map item uuid; a static frame
    missing from `frame_dpis` renders at `static_dpi`. `export_dpi` is the
    resolution the cartograph is saved at (`core/export_resolution.py`): no
    static frame is drawn below it, since its page is rendered at that
    resolution and a lower picture would be enlarged there. `None` leaves
    each frame at its own choice.

    `raster_cache` reuses static-frame rasters across calls (the module's
    shared cache by default); `None` renders every static frame afresh.

    `pending_static`, when given, keeps the GUI thread free: a static frame
    that is not in the cache is drawn by QGIS's own render job instead of
    being waited for (the picture is identical), its spec is returned without
    its image, and the frame is appended to the list. The caller finishes the
    pictures with `with_static_images` once every `render` is done. A frame
    the job cannot reproduce exactly is still drawn here, synchronously.

    Raises `ValueError` up front when the runtime bundle has no cartograph
    dist files at all -- that is a packaging problem, not a fidelity one, so
    it is not softened into a `FidelityItem` the way a missing layer would be.
    """
    if runtime.cartograph_javascript is None or runtime.cartograph_css is None:
        raise ValueError(
            "the OnlyMap runtime bundle has no cartograph dist files "
            "(cartograph_javascript/cartograph_css); re-fetch or re-lock the "
            "runtime before exporting a cartograph"
        )
    frame_dpis = frame_dpis or {}

    reading = read_layout(layout, report, frame_modes=frame_modes)

    items: list[CartographItem] = list(reading.furniture)
    live_frames: list[LiveFrameSource] = []
    in_plane = {
        frame.frame_id
        for frame in reading.frames
        if frame.mode == "live" and draws_in_own_plane(frame.map_item)
    }
    page_extents = _atlas_page_extents(
        layout,
        [
            frame
            for frame in reading.frames
            if frame.atlas_cameras_from_qgis
            or (frame.atlas_fit is not None and frame.frame_id in in_plane)
        ],
    )

    for frame in reading.frames:
        if frame.mode == "static":
            dpi = frame_dpis.get(frame.map_item.uuid(), static_dpi)
            if export_dpi is not None:
                dpi = _resolution_for(frame, dpi, export_dpi, report)
            items.append(
                _build_static_frame(
                    layout, project, frame, dpi, raster_cache, pending_static
                )
            )
        else:
            frame_spec, source = _build_live_frame(
                frame,
                project,
                page_extents.get(frame.frame_id, ()),
                in_plane=frame.frame_id in in_plane,
            )
            items.append(frame_spec)
            live_frames.append(source)

    # A frame before everything else at its z: QGIS draws a map's grid and
    # any item stacked at the map's own level over the map, never under it.
    items.sort(
        key=lambda item: (
            item.z if item.z is not None else 0,
            0 if isinstance(item, FrameSpec) else 1,
        )
    )

    title = title_override if title_override is not None else (project.title() or "")
    spec = CartographSpec(
        cartograph_id=_cartograph_id(layout),
        title=title,
        width=reading.page_width,
        height=reading.page_height,
        orientation=reading.orientation,
        bg=reading.bg,
        items=tuple(items),
        atlas=reading.atlas,
    )
    has_live = bool(live_frames)
    bundle = CartographRuntimeBundle(
        cartograph_js=runtime.cartograph_javascript,
        cartograph_css=runtime.cartograph_css.decode("utf-8"),
        core_js=runtime.javascript if has_live else None,
        core_css=runtime.css.decode("utf-8") if has_live else None,
    )
    return PreparedCartograph(
        spec=spec,
        live_frames=tuple(live_frames),
        bundle=bundle,
        atlas_requirement=reading.atlas_requirement,
    )


def finish_cartograph(
    prepared: PreparedCartograph,
    project: QgsProject | None,
    report: FidelityReportBuilder,
    *,
    writer: OnlyMapWriter | None = None,
    layer_settings: Mapping[str, LayerSettings] | None = None,
) -> str:
    """Render each live frame's `<om-map>` and write the document. Never reads
    the layout, and with `prepare_live_reads` done never reads the project
    either (`project` may then be `None`), so it is safe on a worker thread."""
    return finish_cartograph_full(
        prepared, project, report, writer=writer, layer_settings=layer_settings
    ).html


def finish_cartograph_full(
    prepared: PreparedCartograph,
    project: QgsProject | None,
    report: FidelityReportBuilder,
    *,
    writer: OnlyMapWriter | None = None,
    layer_settings: Mapping[str, LayerSettings] | None = None,
) -> FinishedCartograph:
    """`finish_cartograph`, also returning each live frame's `ExportProject`.

    `writer` renders the live frames' `<om-map>`s; pass one built with the
    user's licence policy (`OnlyMapWriter(license_policy=...)`, as the web-map
    export does) so a paid key reaches the cartograph's maps too. `None`
    keeps the default (free) policy.

    `layer_settings` are the Layers tab's per-layer choices (popups, popup
    fields, labels), applied to every live frame exactly as to a web map: a
    field the user kept out of a web map's popups stays out of a cartograph's.
    Which layers a frame draws is the layout's own choice, not the tab's.
    """
    writer = writer or OnlyMapWriter()
    spec = prepared.spec
    live_markup: dict[str, str] = {}
    projects: list[ExportProject] = []
    # By file name, which carries the layer id: a raster drawn in two frames
    # is converted to the same bytes and carried once.
    embedded_rasters: dict[str, bytes] = {}
    reported_remote: set[str] = set()
    for index, source in enumerate(prepared.live_frames):
        if prepared.live_reads:
            export_project = finish_project(prepared.live_reads[index], report)
        else:
            if project is None:
                raise ValueError("a project is needed when no live reads were prepared")
            export_project = read_project(
                project,
                report,
                layer_order=source.layer_order,
                canvas_extent=source.extent,
                layer_settings=layer_settings,
                settings=_LIVE_FRAME_SETTINGS,
                report_map_title=False,
            )
        # A live frame never goes through the dependency scan, yet its tile
        # layers are fetched from their host each time the page is opened.
        for layer in export_project.exportable_layers:
            if layer.layer_id in reported_remote:
                continue
            for dependency in layer.dependencies:
                if dependency.disposition is AssetDisposition.REMOTE:
                    reported_remote.add(layer.layer_id)
                    report_remote_dependency(layer, dependency, report)
        export_project, frame_rasters = _embed_rasters(export_project, report)
        for name, data in frame_rasters:
            embedded_rasters.setdefault(name, data)
        requirement = prepared.atlas_requirement
        if requirement is not None and requirement.frame_id == source.frame_id:
            export_project = _with_atlas_order(export_project, requirement)
        projects.append(export_project)
        if (
            requirement is not None
            and requirement.frame_id == source.frame_id
            and not _atlas_layer_survived(export_project, requirement, report)
        ):
            spec = _without_atlas(spec)
        plane = source.plane
        if requirement is not None and requirement.frame_id == source.frame_id:
            spec, plane = _with_exported_feature_ids(
                spec, plane, source.frame_id, export_project, requirement.layer_id
            )
        if plane is not None and _draws_in_plane(plane, export_project, report):
            spec = _with_plane_camera(spec, source.frame_id, plane)
            export_project = replace(
                export_project,
                settings=replace(
                    export_project.settings,
                    map_crs=plane.crs,
                    map_crs_def=plane.crs_def,
                ),
            )
        verdict = writer.license_policy.evaluate(export_project)
        rendered = _as_rendered_in_frame(export_project, source)
        # The frame's legends derive from exactly these layers, so their
        # overrides are put in the runtime's terms here, where the layers'
        # final symbology is known.
        spec = fit_frame_legends(spec, source.frame_id, rendered.layers)
        _report_raster_swatches(spec, source.frame_id, rendered.layers, report)
        spec = fit_frame_legend_boxes(spec, source.frame_id, rendered.layers, report)
        live_markup[source.frame_id] = writer.render_map_element(rendered, verdict)
    _warn_when_inline_data_is_heavy(projects, report)
    requirement = prepared.atlas_requirement
    return FinishedCartograph(
        atlas_pages=(
            requirement.page_ids
            if requirement is not None and spec.atlas is not None
            else None
        ),
        html=write_cartograph_html(
            spec,
            prepared.bundle,
            live_markup,
            embedded_rasters=tuple(embedded_rasters.items()),
        ),
        projects=tuple(projects),
    )


#: Inline vector data past this makes the page hang before it draws: the
#: browser parses all of it and builds every feature on the main thread. A
#: 60 MB page (41 MB of dense polygons) kept a software-rendered browser busy
#: for over a minute with a blank map.
INLINE_VECTOR_DATA_WARN_BYTES = 25 * 1024 * 1024

#: How many of the heaviest layers the warning names.
_HEAVY_LAYERS_NAMED = 3


def _warn_when_inline_data_is_heavy(
    projects: list[ExportProject], report: FidelityReportBuilder
) -> None:
    """Warn when the page's inline vector data is large enough to hang it.

    The page carries each layer once however many frames draw it, so layers
    are counted by id across the live frames.
    """
    sizes: dict[str, int] = {}
    names: dict[str, str] = {}
    for project in projects:
        vector_ids = {
            layer.layer_id
            for layer in project.exportable_layers
            if layer.geojson is not None
        }
        for layer_id, size in data_bytes_by_layer(project).items():
            if layer_id in vector_ids:
                sizes[layer_id] = size
        names.update(
            {layer.layer_id: layer.name for layer in project.exportable_layers}
        )
    total = sum(sizes.values())
    if total <= INLINE_VECTOR_DATA_WARN_BYTES:
        return
    heaviest = sorted(sizes, key=lambda layer_id: sizes[layer_id], reverse=True)
    listed = ", ".join(
        f"'{names[layer_id]}' ({format_bytes(sizes[layer_id])})"
        for layer_id in heaviest[:_HEAVY_LAYERS_NAMED]
    )
    report.approximated(
        tr("Map data size"),
        tr(
            "The page carries {size} of vector data, which can leave the map "
            "blank for a long time while it opens, especially on a computer "
            "without a graphics card. Heaviest: {layers}. Simplify the "
            "geometry of those layers, or set the frame to Image in Map frames."
        ).format(size=format_bytes(total), layers=listed),
    )


@dataclass(frozen=True)
class _EmbeddedRasters:
    """One frame's rasters as converted for the page: each staged
    `RasterSpec` by layer id, the bytes the page carries, and the Fidelity
    rows the conversion wrote, replayed when the result is reused."""

    rasters: Mapping[str, RasterSpec]
    embedded: tuple[tuple[str, bytes], ...]
    rows: tuple[FidelityItem, ...]


#: The last conversions, most recent last. A cartograph autosaves on every
#: edit, and converting a large raster to a COG takes long enough that redoing
#: it each time would stall every save; the key holds everything that changes
#: the bytes, so an entry is only reused while it is still right.
_RASTER_EMBEDS: dict[tuple[object, ...], _EmbeddedRasters] = {}
_RASTER_EMBEDS_MAX = 8


def _embed_rasters(
    export_project: ExportProject, report: FidelityReportBuilder
) -> tuple[ExportProject, tuple[tuple[str, bytes], ...]]:
    """Convert a live frame's rasters into bytes the page carries, as the
    single-file web map does (`packaging/raster_staging.py`), and point each
    `RasterSpec.src` at them. A raster that cannot be carried is left out of
    the frame; the rows `stage_rasters` writes say why. Without this a
    `<om-layer type="COGLayer">` would name the file on the author's machine.
    """
    rasters = [layer for layer in export_project.layers if layer.raster is not None]
    if not rasters:
        return export_project, ()
    key = _raster_embed_key(rasters)
    staged = _RASTER_EMBEDS.pop(key, None) if key is not None else None
    if staged is None:
        before = len(report)
        with tempfile.TemporaryDirectory(prefix="qgis2webmap-carto-") as work:
            result = stage_rasters(
                export_project, Path(work), OutputMode.STANDALONE_HTML, report
            )
        staged = _EmbeddedRasters(
            rasters={
                layer.layer_id: layer.raster
                for layer in result.project.layers
                if layer.raster is not None and layer.raster.src is not None
            },
            embedded=result.embedded,
            rows=report.items[before:],
        )
    else:
        report.extend(staged.rows)
    if key is not None:
        _RASTER_EMBEDS[key] = staged
        while len(_RASTER_EMBEDS) > _RASTER_EMBEDS_MAX:
            _RASTER_EMBEDS.pop(next(iter(_RASTER_EMBEDS)))
    layers = tuple(
        replace(layer, raster=staged.rasters[layer.layer_id])
        if layer.raster is not None and layer.layer_id in staged.rasters
        else layer
        for layer in export_project.layers
        if layer.raster is None or layer.layer_id in staged.rasters
    )
    return replace(export_project, layers=layers), staged.embedded


def _raster_embed_key(rasters: list[ExportLayer]) -> tuple[object, ...] | None:
    """What the converted bytes depend on: each raster's spec (file, style,
    bands, stretch) and its file's size and modification time. None when a
    file cannot be read, which is left to the conversion to report."""
    parts: list[object] = []
    for layer in rasters:
        raster = layer.raster
        if raster is None:
            continue
        try:
            stat = os.stat(raster.path)
        except (OSError, TypeError):
            return None
        parts.append((layer.layer_id, raster, stat.st_size, stat.st_mtime_ns))
    return tuple(parts)


def _draws_in_plane(
    plane: ProjectedPlane,
    export_project: ExportProject,
    report: FidelityReportBuilder,
) -> bool:
    """Whether every layer of a live frame can draw in its map's plane, with
    the frame's fidelity row either way.

    The runtime projects vector rows into a plane and warps image tiles onto
    it; tiles of anything else, and embedded rasters, are indexed in Web
    Mercator and would draw in the wrong place (its validator says so for
    tiles; a raster is not projected at all). One such layer keeps the whole
    frame in Web Mercator, which still draws every layer where it belongs.
    """
    blockers = [
        layer.name
        for layer in export_project.exportable_layers
        if layer.raster is not None
        or (layer.tiles is not None and not is_image_tile_template(layer.tiles.url))
    ]
    if not blockers:
        report.preserved(
            plane.subject,
            f"Exported as an interactive frame, drawn in {plane.crs} as in QGIS.",
            item_id=plane.item_id,
        )
        return True
    names = ", ".join(f"'{name}'" for name in blockers)
    report.approximated(
        plane.subject,
        "Exported as an interactive frame in Web Mercator (EPSG:3857) rather than in "
        f"{plane.crs}, because {names} cannot be drawn in another projection "
        "by the browser (rasters and non-image tiles stay in Web Mercator); "
        "shapes, and distances away from the centre, differ slightly from "
        "QGIS. Set the frame to Image in Map frames to keep QGIS's "
        "projection exactly.",
        item_id=plane.item_id,
    )
    return False


#: The runtime's own test for tiles it warps onto a plane
#: (`data-layer.ts`'s `isImageTileTemplate`).
_IMAGE_TILE_RE = re.compile(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", re.IGNORECASE)


def is_image_tile_template(url: str) -> bool:
    return _IMAGE_TILE_RE.search(url) is not None


def _with_exported_feature_ids(
    spec: CartographSpec,
    plane: ProjectedPlane | None,
    frame_id: str,
    export_project: ExportProject,
    layer_id: str,
) -> tuple[CartographSpec, ProjectedPlane | None]:
    """The atlas cameras re-keyed to the coverage layer's exported ids.

    QGIS's atlas pages are keyed by QGIS's feature id, which the exported
    GeoJSON carries as `id` - unless the layer's ids were replaced to keep a
    hidden key column out of the file (`ExportLayer.feature_ids`).
    """
    layer = next(
        (c for c in export_project.exportable_layers if c.layer_id == layer_id), None
    )
    if layer is None or not layer.feature_ids:
        return spec, plane
    new_ids = dict(layer.feature_ids)

    def renumbered(cameras: tuple[AtlasCamera, ...]) -> tuple[AtlasCamera, ...]:
        return tuple(
            replace(camera, feature_id=new_ids[camera.feature_id])
            for camera in cameras
            if camera.feature_id in new_ids
        )

    items = tuple(
        replace(item, atlas_cameras=renumbered(item.atlas_cameras))
        if isinstance(item, FrameSpec) and item.id == frame_id
        else item
        for item in spec.items
    )
    if plane is not None:
        plane = replace(plane, atlas_cameras=renumbered(plane.atlas_cameras))
    return replace(spec, items=items), plane


def _with_plane_camera(
    spec: CartographSpec, frame_id: str, plane: ProjectedPlane
) -> CartographSpec:
    """`spec` with frame `frame_id` on its plane camera. An atlas frame
    follows QGIS's own pages then: the runtime's feature fit works in Web
    Mercator, and the plane cameras are already QGIS's."""

    def moved(item: CartographItem) -> CartographItem:
        if not isinstance(item, FrameSpec) or item.id != frame_id:
            return item
        if item.atlas_fit is None:
            return replace(item, center=plane.center, zoom=plane.zoom)
        return replace(
            item,
            center=plane.center,
            zoom=plane.zoom,
            atlas_fit="fixed",
            atlas_margin=None,
            atlas_cameras=plane.atlas_cameras,
        )

    return replace(spec, items=tuple(moved(item) for item in spec.items))


def _as_rendered_in_frame(
    export_project: ExportProject, source: LiveFrameSource
) -> ExportProject:
    """The frame's layers under their `<om-layer id>`s, hidden ones hidden."""
    layers = _layers_in_frame(export_project.layers, source)
    if all(new is old for new, old in zip(layers, export_project.layers, strict=True)):
        return export_project
    return replace(export_project, layers=layers)


def _layers_in_frame(
    layers: Sequence[ExportLayer], source: LiveFrameSource
) -> tuple[ExportLayer, ...]:
    """`layers` under the frame's `<om-layer id>`s, hidden ones hidden."""
    dom_ids = dict(source.layer_dom_ids)
    hidden = set(source.hidden_layer_ids)
    return tuple(
        replace(
            layer,
            layer_id=dom_ids.get(layer.layer_id, layer.layer_id),
            visible=layer.visible and layer.layer_id not in hidden,
        )
        if hidden or dom_ids.get(layer.layer_id, layer.layer_id) != layer.layer_id
        else layer
        for layer in layers
    )


def _atlas_layer_survived(
    export_project: ExportProject,
    requirement: AtlasRequirement,
    report: FidelityReportBuilder,
) -> bool:
    """Whether the coverage layer reached the atlas frame's map with the
    fields the atlas reads. A layer that did not make it means an
    `<om-atlas>` the runtime rejects; a field that did not means a token
    that renders as typed on every page."""
    layer = next(
        (
            candidate
            for candidate in export_project.exportable_layers
            if candidate.layer_id == requirement.layer_id
        ),
        None,
    )
    if layer is None or layer.geojson is None:
        report.blocked(
            ATLAS_SUBJECT,
            f"No atlas pages were exported: the coverage layer "
            f"'{requirement.layer_name}' could not be exported into the atlas "
            "map (see its own rows in this report), and the cartograph pages "
            "an atlas through that layer's features. Fix the layer's rows, or "
            "choose another coverage layer.",
        )
        return False
    present: set[str] = set()
    for feature in layer.geojson.get("features") or ():
        present.update((feature.get("properties") or {}).keys())
    for field_name in requirement.fields:
        if field_name not in present:
            report.approximated(
                ATLAS_SUBJECT,
                f"The atlas reads the field '{field_name}' of "
                f"'{requirement.layer_name}', but that field is not in the "
                "exported data (a field hidden in the layer's attribute table "
                "is left out of the export), so what uses it -- a {{atlas."
                f"{field_name}}} label, the page names, the sort or the filter "
                "-- does not work. Show the field in the layer's attribute "
                "table to keep it.",
            )
    return True


def _with_atlas_order(
    export_project: ExportProject, requirement: AtlasRequirement
) -> ExportProject:
    """The coverage layer's features, each carrying its place in the order
    QGIS pages them (`ATLAS_ORDER_KEY`), which is what the atlas sorts by."""
    if not requirement.page_order:
        return export_project
    places = {
        feature_id: place for place, feature_id in enumerate(requirement.page_order)
    }
    layers = []
    for layer in export_project.layers:
        if layer.layer_id == requirement.layer_id and layer.geojson is not None:
            features = [
                {**feature, ATLAS_ORDER_KEY: places[feature["id"]]}
                if feature.get("id") in places
                else feature
                for feature in layer.geojson.get("features") or ()
            ]
            layer = replace(layer, geojson={**layer.geojson, "features": features})
        layers.append(layer)
    return replace(export_project, layers=tuple(layers))


def _without_atlas(spec: CartographSpec) -> CartographSpec:
    items = tuple(
        replace(item, atlas_fit=None, atlas_margin=None, atlas_cameras=())
        if isinstance(item, FrameSpec) and item.atlas_fit is not None
        else item
        for item in spec.items
    )
    return replace(spec, atlas=None, items=items)


def _cartograph_id(layout: QgsPrintLayout) -> str:
    name = layout.name() or "cartograph"
    sanitized = _ID_SANITIZE_RE.sub("-", name).strip("-")
    return sanitized or "cartograph"


def _resolution_for(
    frame: MapFrameReading,
    frame_dpi: int,
    export_dpi: int,
    report: FidelityReportBuilder,
) -> int:
    """The resolution `frame`'s picture is drawn at on a cartograph saved at
    `export_dpi`: never lower, within what a browser canvas holds -- and said
    when the frame is too large to reach it."""
    resolution = frame_resolution(frame_dpi, export_dpi, frame.box.w, frame.box.h)
    if resolution.limited:
        report.approximated(
            f"Map frame {frame.map_item.displayName()}",
            f"Drawn at {resolution.dpi} dpi, not the saved {resolution.wanted} "
            "dpi: at that resolution this frame's picture would be larger than "
            "a browser can draw. Make the map smaller or save a lower "
            "resolution.",
            item_id=frame.frame_id,
        )
    return resolution.dpi


def _build_static_frame(
    layout: QgsPrintLayout,
    project: QgsProject,
    frame: MapFrameReading,
    static_dpi: int,
    raster_cache: StaticFrameCache | None,
    pending: list[PendingStaticFrame] | None = None,
) -> FrameSpec:
    entry = None
    cached = None
    if raster_cache is not None:
        entry, cached = raster_cache.lookup(layout, frame.map_item, static_dpi, project)
    if cached is not None:
        render = cached
    elif pending is not None and AsyncFrameRender.supports(layout, frame.map_item):
        drawing = AsyncFrameRender(layout, frame.map_item, static_dpi)
        drawing.start()
        pending.append(PendingStaticFrame(frame.frame_id, drawing, raster_cache, entry))
        assert drawing.corners is not None  # set by `start`
        render = StaticFrameRender(
            png_bytes=b"",
            corners=drawing.corners,
            crs_authid=drawing.crs_authid,
            crs_def=drawing.crs_def,
        )
    else:
        render = render_static_frame(layout, frame.map_item, dpi=static_dpi)
        if raster_cache is not None and entry is not None:
            raster_cache.store(entry, render)
    return FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="static",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        opacity=frame.opacity,
        crs=render.crs_authid,
        crs_def=render.crs_def,
        corners=render.corners,
        image_bytes=render.png_bytes,
        dpi=float(static_dpi),
        overview_of=frame.overview_of,
        frame_bg=frame.frame_bg,
        frame_stroke=frame.frame_stroke,
    )


def _georeference(
    map_item: QgsLayoutItemMap, project: QgsProject
) -> FrameGeoreference | None:
    """Where QGIS draws `map_item`, as a static frame would be placed: the
    page's fallback when the browser cannot draw the live map. None for a
    CRS with no authority code, which a frame's `crs=` cannot name."""
    crs = map_item.crs()
    if not crs.isValid() or not crs.authid():
        return None
    return FrameGeoreference(
        corners=frame_corners_wgs84(map_item, project),
        crs=crs.authid(),
        crs_def=crs.toProj() or None,
    )


def _build_live_frame(
    frame: MapFrameReading,
    project: QgsProject,
    page_extents: tuple[tuple[int, QgsRectangle], ...] = (),
    *,
    in_plane: bool = False,
) -> tuple[FrameSpec, LiveFrameSource]:
    map_item = frame.map_item
    crs = map_item.crs()
    extent = _frame_extent_wgs84(map_item, project)
    center, zoom = _camera_for_extent(extent, frame.box.w)
    atlas_cameras: tuple[AtlasCamera, ...] = ()
    if frame.atlas_cameras_from_qgis:
        atlas_cameras = tuple(
            AtlasCamera(
                feature_id,
                *_camera_for_extent(_rect_wgs84(crs, rect, project), frame.box.w),
            )
            for feature_id, rect in page_extents
        )
    plane = None
    if in_plane:
        plane_center, plane_zoom = _plane_camera(
            crs, map_item.extent(), frame.box.w, project
        )
        plane = ProjectedPlane(
            subject=f"Map frame '{map_item.displayName()}'",
            item_id=map_item.uuid(),
            crs=crs.authid(),
            crs_def=crs.toProj(),
            center=plane_center,
            zoom=plane_zoom,
            atlas_cameras=tuple(
                AtlasCamera(feature_id, *_plane_camera(crs, rect, frame.box.w, project))
                for feature_id, rect in page_extents
            ),
        )
    frame_spec = FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="live",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        opacity=frame.opacity,
        center=center,
        zoom=zoom,
        bearing=map_item.mapRotation(),
        overview_of=frame.overview_of,
        frame_bg=frame.frame_bg,
        frame_stroke=frame.frame_stroke,
        atlas_fit=frame.atlas_fit,
        atlas_margin=frame.atlas_margin,
        atlas_cameras=atlas_cameras,
        georeference=_georeference(map_item, project),
    )
    source = LiveFrameSource(
        frame_id=frame.frame_id,
        layer_order=tuple(layer_id for layer_id, _dom_id in frame.layer_dom_ids),
        extent=extent,
        layer_dom_ids=frame.layer_dom_ids,
        hidden_layer_ids=frame.hidden_layer_ids,
        plane=plane,
    )
    return frame_spec, source


def _atlas_page_extents(
    layout: QgsPrintLayout,
    frames: list[MapFrameReading],
) -> dict[str, tuple[tuple[int, QgsRectangle], ...]]:
    """Each frame's extent on every atlas page, by QGIS's own atlas, keyed
    by the page's feature id.

    Paging the real atlas rather than re-deriving its rules keeps fixed
    scales, predefined-scale choice and point centring exactly QGIS's,
    including the parts that live in project settings (the predefined scale
    list). The atlas leaves its maps on the last page's extent when it is
    done, and this runs on every autosave, so the extents are put back, and
    the designer's atlas preview returns to the page it was showing.
    """
    if not frames:
        return {}
    atlas = layout.atlas()
    pages: dict[str, list[tuple[int, QgsRectangle]]] = {
        frame.frame_id: [] for frame in frames
    }
    with layout_state_restored(layout):
        if not atlas.beginRender():
            return {}
        try:
            for index in range(atlas.count()):
                if not atlas.seekTo(index):
                    continue
                feature_id = int(layout.reportContext().feature().id())
                for frame in frames:
                    pages[frame.frame_id].append(
                        (feature_id, QgsRectangle(frame.map_item.extent()))
                    )
        finally:
            atlas.endRender()
    return {frame_id: tuple(found) for frame_id, found in pages.items()}


def _frame_extent_wgs84(map_item, project: QgsProject) -> Extent:
    """The map item's axis-aligned extent, reprojected to WGS84.

    Reuses `core/extent_math.py`'s antimeridian-aware union rather than a
    naive min/max over the transformed corners, for the same reason every
    other reader in this plugin does: a naive box over a dateline-crossing
    view opens at the whole planet's width.
    """
    return _rect_wgs84(map_item.crs(), map_item.extent(), project)


def _rect_wgs84(crs, rect: QgsRectangle, project: QgsProject) -> Extent:
    """`rect` in `crs`, as the WGS84 extent `_frame_extent_wgs84` describes."""
    transform = QgsCoordinateTransform(crs, WGS84, project)
    corners = [
        (rect.xMinimum(), rect.yMinimum()),
        (rect.xMinimum(), rect.yMaximum()),
        (rect.xMaximum(), rect.yMinimum()),
        (rect.xMaximum(), rect.yMaximum()),
    ]
    longitudes = []
    latitudes = []
    for x, y in corners:
        point = transform.transform(QgsPointXY(x, y))
        longitudes.append(point.x())
        latitudes.append(point.y())

    extent = extent_from_longitudes_latitudes(longitudes, latitudes)
    if extent is None:  # pragma: no cover - defensive; four corners are never empty
        raise ValueError(f"could not compute an extent for {rect.toString()}")
    return extent


def _camera_for_extent(
    extent: Extent, frame_width_mm: float
) -> tuple[tuple[float, float], float]:
    """A live frame's `center`/`zoom`, from its extent and its box width.

    The zoom maths mirrors `core/zoom_range.py`'s `web_zoom_for_scale` (same
    deck.gl Web Mercator world, 512 CSS pixels wide at zoom 0) run in
    reverse: there, a ground scale is turned into a zoom; here, a ground
    distance (the extent's width) and a pixel distance (the frame's own
    width) are turned into the zoom that makes one cover the other.
    """
    # The Mercator midpoint, not the degree midpoint: the runtime centres the
    # camera in Web Mercator, where a span of latitude is not symmetric about
    # its average (0..60 N centres at ~35.3 N, not 30 N).
    center_lat = _mercator_mid_latitude(extent.south, extent.north)
    if extent.crosses_antimeridian:
        center_lon = extent.east + ((extent.west - extent.east) % 360.0) / 2.0
        if center_lon > 180.0:
            center_lon -= 360.0
        width_degrees = (extent.west - extent.east) % 360.0
    else:
        center_lon = (extent.west + extent.east) / 2.0
        width_degrees = extent.east - extent.west

    frame_width_px = frame_width_mm * _CSS_PIXELS_PER_MM
    if width_degrees <= 0 or frame_width_px <= 0:
        return (center_lon, center_lat), 0.0

    width_metres = (
        width_degrees * (EQUATOR_METRES / 360.0) * math.cos(math.radians(center_lat))
    )
    if width_metres <= 0:
        return (center_lon, center_lat), 0.0

    metres_per_pixel = width_metres / frame_width_px
    metres_per_pixel_at_zoom_0 = (
        EQUATOR_METRES / WORLD_PIXELS_AT_ZOOM_0 * math.cos(math.radians(center_lat))
    )
    zoom = math.log2(metres_per_pixel_at_zoom_0 / metres_per_pixel)
    return (center_lon, center_lat), max(0.0, zoom)


#: deck.gl's rounded circumference of the earth, which its Web Mercator
#: scale (and so the runtime's plane zoom, `plane-camera.ts`) is built on.
#: Not `EQUATOR_METRES`: the two differ by 0.13%, a visible scale error on
#: a printed plate. `_camera_for_extent` needs neither -- its two uses of the
#: constant cancel -- but a plane's metres per pixel are absolute.
_DECK_EARTH_CIRCUMFERENCE = 40.03e6


def _plane_camera(
    crs, rect: QgsRectangle, frame_width_mm: float, project: QgsProject
) -> tuple[tuple[float, float], float]:
    """A live frame's `center`/`zoom` when its map draws in `crs`'s plane.

    The runtime keeps the camera geographic and defines zoom as the Web
    Mercator scale at the centre latitude (`plane-camera.ts`). So the centre
    is the plane extent's centre, unprojected, and the zoom is the one whose
    Mercator metres per pixel at that latitude equal the extent's.
    """
    center = QgsCoordinateTransform(crs, WGS84, project).transform(rect.center())
    frame_width_px = frame_width_mm * _CSS_PIXELS_PER_MM
    metres_per_pixel = (
        rect.width() * plane_metres_per_unit(crs.toProj()) / frame_width_px
    )
    if metres_per_pixel <= 0:
        return (center.x(), center.y()), 0.0
    zoom = math.log2(_plane_metres_per_pixel_at_zoom_0(center.y()) / metres_per_pixel)
    return (center.x(), center.y()), max(0.0, zoom)


def _plane_metres_per_pixel_at_zoom_0(latitude: float) -> float:
    """Ground metres per CSS pixel at zoom 0 in the runtime's plane view:
    the Web Mercator scale at `latitude`, on deck's circumference."""
    return (
        _DECK_EARTH_CIRCUMFERENCE
        / WORLD_PIXELS_AT_ZOOM_0
        * math.cos(math.radians(latitude))
    )


def plane_view_width(
    latitude: float, zoom: float, frame_width_mm: float, proj_definition: str
) -> float:
    """How wide, in plane units, a live frame `frame_width_mm` wide shows
    the plane at `zoom` centred on `latitude`: `_plane_camera` run backwards,
    for importing a cartograph back into QGIS."""
    metres_per_pixel = _plane_metres_per_pixel_at_zoom_0(latitude) / 2.0**zoom
    return (
        frame_width_mm
        * _CSS_PIXELS_PER_MM
        * metres_per_pixel
        / plane_metres_per_unit(proj_definition)
    )


def plane_metres_per_unit(proj_definition: str) -> float:
    """Metres per plane unit, as the runtime prices a proj4 definition
    (`projection.ts`'s `unitsOf`): the equatorial degree for a longitude/
    latitude system, `+to_meter` when given, feet for any foot unit, metres
    otherwise."""
    parameters = dict(
        part[1:].split("=", 1)
        for part in proj_definition.split()
        if part.startswith("+") and "=" in part
    )
    if parameters.get("proj") == "longlat" or parameters.get(
        "units", ""
    ).lower().startswith("deg"):
        # The runtime's `METRES_PER_EQUATORIAL_DEGREE`: 2*pi*6378137 / 360.
        return EQUATOR_METRES / 360.0
    try:
        to_meter = float(parameters.get("to_meter", ""))
    except ValueError:
        to_meter = 0.0
    if to_meter > 0:
        return to_meter
    units = parameters.get("units", "m").lower()
    if "ft" in units or "foot" in units:
        return 0.3048
    return 1.0


#: Web Mercator's latitude limit; beyond it `y` is infinite.
_MAX_MERCATOR_LATITUDE = 85.0511287798066


def _mercator_mid_latitude(south: float, north: float) -> float:
    """The latitude halfway between `south` and `north` in Mercator `y`."""

    def to_y(latitude: float) -> float:
        latitude = max(-_MAX_MERCATOR_LATITUDE, min(_MAX_MERCATOR_LATITUDE, latitude))
        return math.log(math.tan(math.pi / 4.0 + math.radians(latitude) / 2.0))

    mid_y = (to_y(south) + to_y(north)) / 2.0
    return math.degrees(2.0 * math.atan(math.exp(mid_y)) - math.pi / 2.0)
