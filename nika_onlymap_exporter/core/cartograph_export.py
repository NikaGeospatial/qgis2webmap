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
export algorithms are no-threading for the same reason. `finish_cartograph`
only reads layers (the same `read_project` the web-map export runs on a
worker thread) and assembles the HTML, so the dialog runs it off the GUI
thread. `export_layout_to_cartograph` runs both, for callers already on one
thread.

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
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsMapLayerStyle,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from ..exporters.frame_raster import StaticFrameRender, render_static_frame
from ..packaging.runtime_manager import RuntimeBundle
from ..writers.cartograph_writer import CartographRuntimeBundle, write_cartograph_html
from ..writers.onlymap_writer import OnlyMapWriter
from .cartograph_ir import CartographItem, CartographSpec, FrameSpec
from .export_ir import ExportProject, Extent
from .extent_math import extent_from_longitudes_latitudes
from .fidelity_report import FidelityReportBuilder
from .layout_reader import AtlasRequirement, FrameMode, MapFrameReading, read_layout
from .project_reader import read_project
from .zoom_range import EQUATOR_METRES, WORLD_PIXELS_AT_ZOOM_0

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

#: A CSS pixel, same definition `core/zoom_range.py` uses, so a live frame's
#: camera is measured in the runtime's own units.
_CSS_PIXELS_PER_MM = 96.0 / 25.4

_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


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
        for layer in map_item.layersToRender():
            self._watch(layer)
        key = _static_frame_key(layout, map_item, dpi, project, self._generations)
        slot = (layout.name(), map_item.uuid())
        cached = self._entries.get(slot)
        if cached is not None and cached[0] == key:
            return cached[1]
        render = render_static_frame(layout, map_item, dpi=dpi)
        self.render_count += 1
        self._entries.pop(slot, None)
        self._entries[slot] = (key, render)
        while len(self._entries) > self.max_frames:
            del self._entries[next(iter(self._entries))]
        return render

    def clear(self) -> None:
        """Forget every raster and stop listening to every layer."""
        self._entries.clear()
        for layer, bump, forget in self._watched.values():
            connections = [(signal, bump) for signal in _layer_change_signals(layer)]
            connections.append((layer.willBeDeleted, forget))
            for signal, slot in connections:
                # TypeError/RuntimeError: already gone with its layer.
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
    title_override: str | None = None,
    writer: OnlyMapWriter | None = None,
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
        title_override=title_override,
    )
    return finish_cartograph(prepared, project, report, writer=writer)


def prepare_cartograph(
    layout: QgsPrintLayout,
    project: QgsProject,
    runtime: RuntimeBundle,
    report: FidelityReportBuilder,
    *,
    frame_modes: Mapping[str, FrameMode] | None = None,
    frame_dpis: Mapping[str, int] | None = None,
    static_dpi: int = 200,
    title_override: str | None = None,
    raster_cache: StaticFrameCache | None = _DEFAULT_CACHE,
) -> PreparedCartograph:
    """Everything that touches `layout`. GUI thread only.

    `frame_modes` and `frame_dpis` are keyed by map item uuid; a static frame
    missing from `frame_dpis` renders at `static_dpi`.

    `raster_cache` reuses static-frame rasters across calls (the module's
    shared cache by default); `None` renders every static frame afresh.

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

    for frame in reading.frames:
        if frame.mode == "static":
            dpi = frame_dpis.get(frame.map_item.uuid(), static_dpi)
            items.append(_build_static_frame(layout, project, frame, dpi, raster_cache))
        else:
            frame_spec, source = _build_live_frame(frame, project)
            items.append(frame_spec)
            live_frames.append(source)

    items.sort(key=lambda item: item.z if item.z is not None else 0)

    title = title_override if title_override is not None else (project.title() or "")
    spec = CartographSpec(
        cartograph_id=_cartograph_id(layout),
        title=title,
        width=reading.page_width,
        height=reading.page_height,
        orientation=reading.orientation,
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
    project: QgsProject,
    report: FidelityReportBuilder,
    *,
    writer: OnlyMapWriter | None = None,
) -> str:
    """Render each live frame's `<om-map>` and write the document. Reads
    layers only, never the layout, so it is safe on a worker thread."""
    return finish_cartograph_full(prepared, project, report, writer=writer).html


def finish_cartograph_full(
    prepared: PreparedCartograph,
    project: QgsProject,
    report: FidelityReportBuilder,
    *,
    writer: OnlyMapWriter | None = None,
) -> FinishedCartograph:
    """`finish_cartograph`, also returning each live frame's `ExportProject`.

    `writer` renders the live frames' `<om-map>`s; pass one built with the
    user's licence policy (`OnlyMapWriter(license_policy=...)`, as the web-map
    export does) so a paid key reaches the cartograph's maps too. `None`
    keeps the default (free) policy.
    """
    writer = writer or OnlyMapWriter()
    spec = prepared.spec
    live_markup: dict[str, str] = {}
    projects: list[ExportProject] = []
    for source in prepared.live_frames:
        export_project = read_project(
            project,
            report,
            layer_order=source.layer_order,
            canvas_extent=source.extent,
        )
        projects.append(export_project)
        requirement = prepared.atlas_requirement
        if (
            requirement is not None
            and requirement.frame_id == source.frame_id
            and not _atlas_layer_survived(export_project, requirement, report)
        ):
            spec = _without_atlas(spec)
        verdict = writer.license_policy.evaluate(export_project)
        live_markup[source.frame_id] = writer.render_map_element(
            _as_rendered_in_frame(export_project, source), verdict
        )
    return FinishedCartograph(
        html=write_cartograph_html(spec, prepared.bundle, live_markup),
        projects=tuple(projects),
    )


def _as_rendered_in_frame(
    export_project: ExportProject, source: LiveFrameSource
) -> ExportProject:
    """The frame's layers under their `<om-layer id>`s, hidden ones hidden."""
    dom_ids = dict(source.layer_dom_ids)
    hidden = set(source.hidden_layer_ids)
    if not hidden and all(
        dom_ids.get(layer.layer_id, layer.layer_id) == layer.layer_id
        for layer in export_project.layers
    ):
        return export_project
    layers = tuple(
        replace(
            layer,
            layer_id=dom_ids.get(layer.layer_id, layer.layer_id),
            visible=layer.visible and layer.layer_id not in hidden,
        )
        for layer in export_project.layers
    )
    return replace(export_project, layers=layers)


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
            "Layout item 'Atlas'",
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
                "Layout item 'Atlas'",
                f"The atlas reads the field '{field_name}' of "
                f"'{requirement.layer_name}', but that field is not in the "
                "exported data (a field hidden in the layer's attribute table "
                "is left out of the export), so what uses it -- a {{atlas."
                f"{field_name}}} label, the page names, the sort or the filter "
                "-- does not work. Show the field in the layer's attribute "
                "table to keep it.",
            )
    return True


def _without_atlas(spec: CartographSpec) -> CartographSpec:
    items = tuple(
        replace(item, atlas_fit=None, atlas_margin=None)
        if isinstance(item, FrameSpec) and item.atlas_fit is not None
        else item
        for item in spec.items
    )
    return replace(spec, atlas=None, items=items)


def _cartograph_id(layout: QgsPrintLayout) -> str:
    name = layout.name() or "cartograph"
    sanitized = _ID_SANITIZE_RE.sub("-", name).strip("-")
    return sanitized or "cartograph"


def _build_static_frame(
    layout: QgsPrintLayout,
    project: QgsProject,
    frame: MapFrameReading,
    static_dpi: int,
    raster_cache: StaticFrameCache | None,
) -> FrameSpec:
    if raster_cache is None:
        render = render_static_frame(layout, frame.map_item, dpi=static_dpi)
    else:
        render = raster_cache.render(layout, frame.map_item, static_dpi, project)
    return FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="static",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        crs=render.crs_authid,
        crs_def=render.crs_def,
        corners=render.corners,
        image_bytes=render.png_bytes,
        dpi=float(static_dpi),
        overview_of=frame.overview_of,
    )


def _build_live_frame(
    frame: MapFrameReading, project: QgsProject
) -> tuple[FrameSpec, LiveFrameSource]:
    map_item = frame.map_item
    extent = _frame_extent_wgs84(map_item, project)
    center, zoom = _camera_for_extent(extent, frame.box.w)
    frame_spec = FrameSpec(
        id=frame.frame_id,
        box=frame.box,
        mode="live",
        rotation=frame.rotation,
        z=frame.z,
        locked=frame.locked,
        hidden=frame.hidden,
        center=center,
        zoom=zoom,
        bearing=map_item.mapRotation(),
        overview_of=frame.overview_of,
        atlas_fit=frame.atlas_fit,
        atlas_margin=frame.atlas_margin,
    )
    source = LiveFrameSource(
        frame_id=frame.frame_id,
        layer_order=tuple(layer_id for layer_id, _dom_id in frame.layer_dom_ids),
        extent=extent,
        layer_dom_ids=frame.layer_dom_ids,
        hidden_layer_ids=frame.hidden_layer_ids,
    )
    return frame_spec, source


def _frame_extent_wgs84(map_item, project: QgsProject) -> Extent:
    """The map item's axis-aligned extent, reprojected to WGS84.

    Reuses `core/extent_math.py`'s antimeridian-aware union rather than a
    naive min/max over the transformed corners, for the same reason every
    other reader in this plugin does: a naive box over a dateline-crossing
    view opens at the whole planet's width.
    """
    crs = map_item.crs()
    transform = QgsCoordinateTransform(crs, WGS84, project)
    rect = map_item.extent()
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
        raise ValueError(f"could not compute an extent for map item {map_item.id()!r}")
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


#: Web Mercator's latitude limit; beyond it `y` is infinite.
_MAX_MERCATOR_LATITUDE = 85.0511287798066


def _mercator_mid_latitude(south: float, north: float) -> float:
    """The latitude halfway between `south` and `north` in Mercator `y`."""

    def to_y(latitude: float) -> float:
        latitude = max(-_MAX_MERCATOR_LATITUDE, min(_MAX_MERCATOR_LATITUDE, latitude))
        return math.log(math.tan(math.pi / 4.0 + math.radians(latitude) / 2.0))

    mid_y = (to_y(south) + to_y(north)) / 2.0
    return math.degrees(2.0 * math.atan(math.exp(mid_y)) - math.pi / 2.0)
