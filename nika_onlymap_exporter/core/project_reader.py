"""A QGIS project to an `ExportProject`.

Imports PyQGIS; exercised in `tests/qgis/`.

Two things here are worth reading before changing anything.

**Draw order is reversed on the way out.** QGIS's Layers panel lists the topmost
layer first, and the topmost layer draws *last*. OnlyMap stacks `<om-layer>`
children in document order, so the first child draws first, at the bottom.
Reversing is therefore correct, and forgetting to is the classic way to produce
an upside-down map where the basemap-ish polygon covers everything. When the
project turns on "Control rendering order", that custom order replaces the
panel's.

**Extent comes from the data, not the canvas.** The canvas extent seems like the
obvious choice - it is what the author was looking at - but for antimeridian-
crossing data QGIS's own canvas is already near-global, so the export faithfully
reproduces a useless view. See `extent_math`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsDistanceArea,
    QgsLayerTreeLayer,
    QgsPointXY,
    QgsRectangle,
    QgsScaleCalculator,
)

from .declutter import declutter_noting_stacks
from .export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    ExtentSource,
    GeometryKind,
)
from .extent_math import extent_from_geojson, union_extents
from .fidelity_report import FidelityReportBuilder
from .layer_reader import (
    WGS84,
    MapTime,
    PendingLayer,
    follows_map_time,
    prepare_layer,
)
from .manifest_builder import (
    DEFAULT_LABEL_SIZE,
    OSM_FILE_NOTE,
    TERRAIN_PRESETS,
    basemap_note,
    labels_never_fit,
    osm_from_file,
    terrain_note,
)
from .settings import LayerSettings
from .widget_colors import resolve_widget_colors
from .zoom_range import CSS_PIXEL_METRES, translate_scale_range

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsLayerTreeNode, QgsProject

    from .cartograph_export import LayerScans

DEFAULT_TITLE = "Untitled map"


def group_path_for(node: QgsLayerTreeNode) -> tuple[str, ...]:
    """Names of the groups containing a node, outermost first."""
    path: list[str] = []
    parent = node.parent()
    while parent is not None and parent.parent() is not None:
        path.append(parent.name())
        parent = parent.parent()
    return tuple(reversed(path))


def _has_tile_layer(project: QgsProject) -> bool:
    """Does the project carry an XYZ/WMS backdrop of its own?

    Detected by provider name rather than layer class: `wms` is what QGIS uses
    for XYZ tile layers as well as real WMS, and both are backdrops. Whether
    one was exported is the caller's question (`layer.tiles`). Anything
    unexpected is treated as "no", because a false warning
    about a missing basemap is worse than a missing one.
    """
    tile_providers = {"wms", "xyz", "arcgismapserver", "arcgisrest"}
    for layer in project.mapLayers().values():
        provider = getattr(layer, "providerType", None)
        if provider is None:
            continue
        try:
            if str(provider()).lower() in tile_providers:
                return True
        # Named rather than bare `Exception` - defensive against provider quirks,
        # but only the ones that can actually happen here. `providerType` is a
        # C++ binding: it raises `RuntimeError` once the underlying object has
        # been deleted, and `AttributeError`/`TypeError` if a layer type carries
        # something other than the callable we probed for. Anything else is a bug
        # in this plugin and should surface rather than be swallowed - which is
        # also what bandit's B112 is warning about.
        except (AttributeError, RuntimeError, TypeError):  # pragma: no cover
            continue
    return False


def resolve_title(project: QgsProject, override: str | None = None) -> str:
    """Pick the exported map title.

    Order: the dialog's Map name field, then the project title, then the project
    file name, then a placeholder. The override wins because the dialog is the
    single place a user sets this - the incumbent splits it across two locations
    in Project Properties and then discards it unless a third setting is changed.
    """
    if override and override.strip():
        return override.strip()

    title = (project.title() or "").strip()
    if title:
        return title

    file_name = project.baseName() or ""
    if file_name.strip():
        return file_name.strip()

    return DEFAULT_TITLE


def _highlight_text(color: Color | None) -> str:
    """The map-wide highlight as the hex text a per-layer override compares to.

    `ExportSettings` carries a parsed `Color` while `LayerSettings` carries the
    text a colour button produced; the override resolution needs one form, and
    text is the one that can also mean "not set".
    """
    if color is None:
        return ""
    alpha = max(0, min(255, round(color.a * 255)))
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}{alpha:02x}"


def extent_from_canvas(canvas: Any) -> Extent | None:
    """The QGIS canvas rectangle, reprojected to WGS84.

    Lives here rather than in the dialog so the Processing algorithm can reach
    it too. Returns None on anything unreadable - a canvas with no valid CRS, or
    a transform the projection library refuses - because opening on the data is
    always a safe answer and a failed export is not.
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform
    from qgis.core import QgsProject as _QgsProject

    try:
        rectangle = canvas.extent()
        source_crs = canvas.mapSettings().destinationCrs()
    except AttributeError:
        return None

    if rectangle is None or rectangle.isEmpty():
        return None

    if source_crs.isValid() and source_crs.authid() != WGS84:
        transform = QgsCoordinateTransform(
            source_crs,
            QgsCoordinateReferenceSystem(WGS84),
            _QgsProject.instance(),
        )
        try:
            rectangle = transform.transformBoundingBox(rectangle)
        except Exception:  # a transform the proj database cannot do
            return None

    return Extent(
        west=rectangle.xMinimum(),
        south=rectangle.yMinimum(),
        east=rectangle.xMaximum(),
        north=rectangle.yMaximum(),
    )


def map_time_from_canvas(canvas: Any) -> MapTime | None:
    """The time the QGIS canvas draws, for WMS-T layers that follow it.

    Beside `extent_from_canvas` for the same reason. `None` on anything
    unreadable, which the reader treats as having no view to read.
    """
    try:
        settings = canvas.mapSettings()
        return MapTime(
            is_temporal=bool(settings.isTemporal()), range=settings.temporalRange()
        )
    except (AttributeError, RuntimeError):
        return None


def needs_map_time(project: QgsProject) -> bool:
    """Whether any layer of `project` reads the time the QGIS view draws.

    Only a WMS-T snapshot that follows QGIS's time does (`follows_map_time`);
    without one the time is neither read nor part of the dialog's read cache,
    so moving the temporal controller costs nothing.
    """
    return any(follows_map_time(layer) for layer in project.mapLayers().values())


def _project_terrain_kind(project: QgsProject) -> str | None:
    """The project's own terrain source, or `None` if the ground is flat."""
    provider = project.elevationProperties().terrainProvider()
    kind = getattr(provider, "type", lambda: None)()
    if not isinstance(kind, str) or kind == "flat":
        return None
    return kind


def _report_terrain(
    project_terrain_kind: str | None,
    settings: ExportSettings,
    report: FidelityReportBuilder,
    map_tab_advice: bool = True,
) -> None:
    """What happened to relief - which is never a translation, always a swap.

    A QGIS terrain is a DEM on the user's disk, and the web map needs elevation
    tiles it can fetch. There is no route from one to the other short of tiling
    and hosting the DEM, so the honest options are global relief from a public
    tileset or none at all. Both are stated rather than assumed.

    `map_tab_advice` is off where there is no Map tab to switch relief on in
    (a cartograph's frames): the row then says what is not exported and
    nothing about where to change it.
    """
    note = terrain_note(settings.terrain, basemap=settings.basemap)
    if note is not None:
        report.approximated("Terrain", note)
        kind = project_terrain_kind
        if kind is not None:
            report.approximated(
                "Terrain",
                f"The relief is global elevation data, not this project's own "
                f"{kind} terrain, which cannot be sent with the map. Heights "
                "will be close but not identical to the QGIS 3D view.",
            )
        return

    kind = project_terrain_kind
    if kind is not None:
        advice = (
            " Switch Terrain on under the Map tab for global relief instead, "
            "or the map exports flat."
            if map_tab_advice
            else " The frame is drawn flat."
        )
        report.unsupported(
            "Terrain",
            f"This project uses a {kind} terrain, which is not exported - a DEM "
            f"on your disk cannot travel with the map.{advice}",
        )


def _report_widget_colors(
    settings: ExportSettings, report: FidelityReportBuilder
) -> None:
    """Say so when the widget colours were completed or cannot be read."""
    colors = resolve_widget_colors(
        settings.widget_background, settings.widget_foreground
    )
    if colors.derived == "text":
        report.approximated(
            "Widget colours",
            "The widget text colour is on Default, which cannot be read on your "
            "background colour, so the text is shown in a contrasting colour "
            "instead. Pick a text colour to choose it yourself.",
        )
    elif colors.derived == "background":
        report.approximated(
            "Widget colours",
            "The widget background is on Default, which cannot show your text "
            "colour, so the background is shown in a contrasting colour "
            "instead. Pick a background colour to choose it yourself.",
        )
    elif colors.explicit_contrast is not None:
        report.approximated(
            "Widget colours",
            "Your widget text and background colours have a contrast of "
            f"{colors.explicit_contrast:.1f}:1, below the 4.5:1 needed to read "
            "them comfortably. They are exported as chosen - change one of "
            "them if the legend or scale bar is hard to read.",
        )


def with_output_notes(
    report: FidelityReportBuilder, settings: ExportSettings
) -> FidelityReportBuilder:
    """`report` with its Basemap row as it reads for `settings`' output type.

    The one row the type changes: OpenStreetMap refuses maps opened from a
    file. Kept out of what a cached read depends on, so switching type is a
    redraw rather than a re-read of every feature.
    """
    general = basemap_note(settings.basemap)
    if settings.basemap != "osm" or general is None:
        return report
    wanted = (
        OSM_FILE_NOTE
        if osm_from_file(settings.basemap, settings.output_mode)
        else general
    )
    updated = FidelityReportBuilder()
    updated.extend(
        tuple(
            dataclasses.replace(item, detail=wanted)
            if item.subject == "Basemap" and item.detail in (OSM_FILE_NOTE, general)
            else item
            for item in report.items
        )
    )
    return updated


def read_project(
    project: QgsProject,
    report: FidelityReportBuilder,
    settings: ExportSettings | None = None,
    title_override: str | None = None,
    background_color: Color | None = None,
    selected_layer_ids: frozenset[str] | None = None,
    layer_settings: Mapping[str, LayerSettings] | None = None,
    canvas_extent: Extent | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    layer_order: Sequence[str] | None = None,
    report_map_title: bool = True,
    map_time: MapTime | None = None,
) -> ExportProject:
    """Read a whole project into the normalized model, on the calling thread.

    `prepare_project` and `finish_project` back to back, for callers that
    are not a background job (the Processing algorithm, the cartograph, tests).
    See `prepare_project` for the arguments and `finish_project` for
    `progress`.
    """
    pending = prepare_project(
        project,
        report,
        settings=settings,
        title_override=title_override,
        background_color=background_color,
        selected_layer_ids=selected_layer_ids,
        layer_settings=layer_settings,
        canvas_extent=canvas_extent,
        layer_order=layer_order,
        report_map_title=report_map_title,
        map_time=map_time,
    )
    return finish_project(pending, report, progress)


def prepare_project(
    project: QgsProject,
    report: FidelityReportBuilder,
    settings: ExportSettings | None = None,
    title_override: str | None = None,
    background_color: Color | None = None,
    selected_layer_ids: frozenset[str] | None = None,
    layer_settings: Mapping[str, LayerSettings] | None = None,
    canvas_extent: Extent | None = None,
    layer_order: Sequence[str] | None = None,
    report_map_title: bool = True,
    scans: LayerScans | None = None,
    map_time: MapTime | None = None,
) -> PendingProject:
    """Read a whole project up to its features. **GUI thread only.**

    `report_map_title` is off for a cartograph's live frames: the title and
    description rows advise on the web map's Map name field, which a frame
    has no use for, and the Terrain row leaves out the advice to change it on the web
    map's Map tab. The Basemap row is left out: a frame's layers are the
    layout's choice.

    `selected_layer_ids` limits the export to layers the user ticked; `None`
    means every layer in the tree.

    `layer_order` is for the cartograph's live frames: layer ids, top-to-bottom
    exactly as `QgsLayoutItemMap.layersToRender()` returns them for one layout
    map item. When given, it both orders and *selects* the exported layers -
    only the ids it names are read, in the order it names them - overriding
    `selected_layer_ids` and the layer tree's own order. `None` (the default)
    leaves both exactly as they were: the web-map export's own behaviour. Map
    themes and style overrides on the layout item are not read yet; a layer
    named in `layer_order` but not found in the project's layer tree is
    skipped rather than raised, since a layout can reference a layer that was
    since removed from the project.

    `layer_settings` carries the dialog's per-layer popup and label checkboxes,
    keyed by layer id. A layer absent from the mapping keeps the defaults, so a
    caller with no dialog (the Processing algorithm, a test) can omit it.

    `scans`, from a cartograph's `LiveFrameCache`, reuses the label and Value
    Relation scans of an earlier call while what they read is unchanged;
    `None` scans every layer afresh.
    """
    settings = settings or ExportSettings()
    root = project.layerTreeRoot()

    # Clipping needs a rectangle, and an antimeridian-crossing view is not one -
    # so it is refused rather than silently applied to the wrong half of the
    # world. The map still opens where the user asked; only the data is whole.
    clip_extent = None
    if settings.clip_to_extent:
        if canvas_extent is None:
            report.unsupported(
                "Clip to the current view",
                "The QGIS view could not be read, so every feature was exported "
                "rather than only those on screen.",
            )
        elif canvas_extent.crosses_antimeridian:
            report.unsupported(
                "Clip to the current view",
                "The current view crosses the 180th meridian, which a single "
                "rectangle cannot describe. Every feature was exported instead.",
            )
        else:
            clip_extent = canvas_extent

    if layer_order is not None:
        # `layer_order` implies selection, and overrides the tree's own order.
        # It is top-to-bottom; the map draws bottom-first, so it is walked in
        # reverse to land in the same bottom-first order the tree branch below
        # produces. A layer named here that no longer exists in the tree (the
        # layout item remembers it, the project does not) is skipped rather
        # than raised - looked up once as a dict, since `findLayers()` is a
        # flat list and would otherwise be rescanned per id.
        by_id: dict[str, QgsLayerTreeLayer] = {}
        for candidate in root.findLayers():
            by_id.setdefault(candidate.layerId(), candidate)
        tree_layers = [by_id[lid] for lid in reversed(layer_order) if lid in by_id]
        effective_selected_ids: frozenset[str] | None = frozenset(layer_order)
    else:
        # findLayers() is top-first; the map draws bottom-first.
        tree_layers = list(reversed(root.findLayers()))
        if root.hasCustomLayerOrder():
            # "Control rendering order" is on: the canvas draws in that order,
            # not the panel's. Sorted rather than rebuilt so the tree nodes
            # keep carrying groups and visibility, and a layer the order does
            # not list (a broken one) stays first and is still reported.
            top_first = [layer.id() for layer in root.customLayerOrder()]
            rank = {layer_id: index for index, layer_id in enumerate(top_first)}
            tree_layers.sort(key=lambda node: -rank.get(node.layerId(), len(rank)))
        effective_selected_ids = selected_layer_ids

    entries: list[tuple[str, ExportLayer | PendingLayer | None]] = []
    # Named, not counted: "1 layer(s) could not be loaded" left the user to
    # find which one, and the Fidelity tab groups a named row under its layer.
    skipped_invalid: list[tuple[str, str]] = []

    for tree_layer in tree_layers:
        if not isinstance(tree_layer, QgsLayerTreeLayer):
            continue

        map_layer = tree_layer.layer()
        # Selection first: a broken layer the user has unticked is not part of
        # the export, so it has no business being reported as a problem with it.
        if (
            effective_selected_ids is not None
            and tree_layer.layerId() not in effective_selected_ids
        ):
            continue

        if map_layer is None or not map_layer.isValid():
            skipped_invalid.append((tree_layer.layerId(), tree_layer.name()))
            continue

        per_layer = (layer_settings or {}).get(map_layer.id()) or LayerSettings()
        prepared = prepare_layer(
            map_layer,
            report,
            group_path=group_path_for(tree_layer),
            visible=tree_layer.isVisible(),
            with_popup=per_layer.popup,
            with_labels=per_layer.label,
            field_modes=per_layer.fields,
            precision=per_layer.resolved_precision(settings.quantize_precision),
            popup_on_hover=per_layer.resolved_hover(settings.popup_on_hover),
            highlight_color=per_layer.resolved_highlight(
                _highlight_text(settings.highlight_color)
            ),
            project=project,
            clip_extent=clip_extent,
            name_field=per_layer.name_field,
            cluster=per_layer.cluster,
            scans=scans,
            map_time=map_time,
        )
        entries.append((map_layer.name(), prepared))

    return PendingProject(
        entries=tuple(entries),
        skipped_invalid=tuple(skipped_invalid),
        settings=settings,
        canvas_extent=canvas_extent,
        background_color=background_color,
        has_tile_layer=_has_tile_layer(project),
        scale_basis=ScaleBasis.of(project),
        terrain_kind=_project_terrain_kind(project),
        title=resolve_title(project, title_override),
        title_override=title_override,
        project_title=(project.title() or "").strip(),
        abstract=project.metadata().abstract() or "",
        source_crs=project.crs().authid() if project.crs().isValid() else None,
        report_map_title=report_map_title,
    )


@dataclasses.dataclass(frozen=True)
class PendingProject:
    """A project read as far as the GUI thread can take it.

    **The boundary a background export runs on.** `prepare_project` walks the
    live `QgsProject` on the GUI thread and translates everything that needs a
    live layer: symbology, labels, popups, sources, the project's title, CRS and
    terrain. What it hands back holds no `QgsProject` and no `QgsMapLayer` -
    only plain values, value-type QGIS copies and per-layer feature sources
    (see `layer_reader.LayerFeatures`). `finish_project` then does the work that scales
    with the data - reading and serialising every feature - plus the
    project-wide steps that depend on it, and runs on a worker. QGIS's task
    guidance says a background job must not touch GUI-owned layers or the
    project, and nothing `finish_project` reaches does.
    """

    entries: tuple[tuple[str, ExportLayer | PendingLayer | None], ...]
    skipped_invalid: tuple[tuple[str, str], ...]
    settings: ExportSettings
    canvas_extent: Extent | None
    background_color: Color | None
    has_tile_layer: bool
    scale_basis: ScaleBasis
    terrain_kind: str | None
    title: str
    title_override: str | None
    project_title: str
    abstract: str
    source_crs: str | None
    report_map_title: bool = True


def finish_project(
    pending: PendingProject,
    report: FidelityReportBuilder,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExportProject:
    """Read the features and complete the model. **Safe off the GUI thread.**

    `progress` is called as `(done, total, layer_name)` before each layer is
    read. Reading is the slow half of an export - every feature of every
    layer - and it is the half a user has no other way to watch, so the
    dialog drives its progress bar from here, and its Cancel from the same
    call raising. A caller that wants none passes `None` and pays nothing.
    """
    settings = pending.settings
    canvas_extent = pending.canvas_extent
    layers: list[ExportLayer] = []
    total = len(pending.entries)
    for done, (name, prepared) in enumerate(pending.entries):
        if progress is not None:
            progress(done, total, name)
        if isinstance(prepared, PendingLayer):
            prepared = prepared.finish(report)
        if prepared is not None:
            layers.append(prepared)
    for layer_id, name in pending.skipped_invalid:
        report.blocked(
            f"Layer '{name}'",
            "QGIS could not load this layer - its data source is missing or "
            "unreadable - so the map would be exported without it. Fix the "
            "layer's source in QGIS, or untick it on the Layers tab.",
            layer_id,
        )

    if not layers:
        report.blocked(
            "Project layers",
            "There is nothing to export. Add at least one vector or raster "
            "layer with data to the project.",
        )

    if settings.quantize_precision is not None:
        report.approximated(
            "Coordinate precision",
            f"Coordinates are rounded to {settings.quantize_precision} decimal "
            "place(s) to make the file smaller. This is the one setting that "
            "discards data - the exported geometry is no longer identical to "
            "the source.",
        )

    _report_widget_colors(settings, report)

    # A basemap is the only setting whose cost falls on the *recipient*, and it
    # cannot be undone once the file is sent - so it belongs in the report the
    # user reads before exporting, not only in the dialog they already left.
    note = basemap_note(settings.basemap)
    if osm_from_file(settings.basemap, settings.output_mode):
        report.approximated("Basemap", OSM_FILE_NOTE)
    elif note is not None:
        report.approximated("Basemap", note)
    elif (
        pending.report_map_title
        and pending.has_tile_layer
        and not any(layer.tiles for layer in layers)
    ):
        # The project has a backdrop and the export will not. Silence here is
        # how someone ships a map of bare shapes floating on white and only
        # finds out when the recipient asks what they are looking at. Not for
        # a cartograph's frames: the layout chooses each frame's layers, so a
        # tile layer left out of one is a choice, not a lost backdrop.
        report.unsupported(
            "Basemap",
            "This project has a tile layer, but the export has no basemap, so "
            "the map will have a blank background. Choose one under Basemap on "
            "the Map tab if you want a backdrop - note that it makes the map "
            "load tiles from the internet each time it is opened.",
        )

    # Before the terrain notes: they name labelled layers, and a layer whose
    # labels never have room is unlabelled from `_declutter` on.
    extent = _resolve_extent(layers, report, settings, canvas_extent)
    layers = _translate_scale_visibility(pending.scale_basis, layers, extent, report)
    layers = _declutter(layers, report)

    _report_terrain(pending.terrain_kind, settings, report, pending.report_map_title)

    # Extrusion and relief cannot coexist: columns would rise from sea level
    # and vanish inside the mountains. Under relief the runtime drapes every
    # layer onto the surface instead, so an extruded layer flattens into
    # colour painted over the terrain - a different picture from the flat
    # export, and worth saying before the user discovers it.
    if settings.terrain != "none" and settings.terrain in TERRAIN_PRESETS:
        raised = [
            layer.name for layer in layers if layer.elevation and layer.elevation.is_set
        ]
        if raised:
            report.approximated(
                "Terrain",
                "Extruded layers drape flat onto the relief surface - their "
                "colour paints the mountains, but the column height does not "
                "show: " + ", ".join(f"'{name}'" for name in raised) + ".",
            )

        # Re-measured 2026-08-26, and the previous wording was wrong. It said
        # labels "simply never appear" over relief - which was true only
        # because labels never appeared ANYWHERE until `get-position` was
        # fixed. They do render over relief. What actually happens is that the
        # runtime stops honouring the text's pixel sizing once terrain is on:
        # the glyphs shrink to a few pixels and squash horizontally while the
        # layer's markers keep their size, so the text is present and
        # unreadable. Reported upstream.
        labelled = [
            layer.name
            for layer in layers
            if layer.labeling.enabled and layer.labeling.field_name
        ]
        if labelled:
            report.approximated(
                "Terrain",
                "Labels shrink to a few pixels over relief and are effectively "
                "unreadable, whatever text size you set: "
                + ", ".join(f"'{name}'" for name in labelled)
                + ". The layers and their markers draw normally. Turn relief "
                "off if the labels matter more than the terrain.",
            )

        # Also measured: the drape keeps polygon fills but drops their
        # outlines, so overlapping translucent areas lose the border that
        # tells them apart.
        outlined = [
            layer.name
            for layer in layers
            if layer.geometry_kind is GeometryKind.POLYGON
            and (symbol := layer.renderer.representative_symbol) is not None
            and symbol.stroke_width > 0
        ]
        if outlined:
            report.unsupported(
                "Terrain",
                "Polygon outlines do not draw over relief in the current map "
                "runtime - the fills paint the surface but their borders "
                "disappear, so overlapping areas are hard to tell apart: "
                + ", ".join(f"'{name}'" for name in outlined)
                + ". Turn relief off if the outlines matter more.",
            )

    if pending.report_map_title:
        _report_project_metadata(
            pending.project_title,
            pending.abstract,
            pending.title,
            pending.title_override,
            report,
        )

    return ExportProject(
        title=pending.title,
        layers=tuple(layers),
        abstract=(pending.abstract or None),
        extent=extent,
        background_color=pending.background_color,
        source_crs=pending.source_crs,
        settings=settings,
        fidelity=report.items,
    )


def _translate_scale_visibility(
    basis: ScaleBasis,
    layers: list[ExportLayer],
    extent: Extent | None,
    report: FidelityReportBuilder,
) -> list[ExportLayer]:
    """Give every layer with a QGIS scale range its web zoom range, and say so.

    Runs after the extent, because a zoom level only corresponds to a scale at
    a particular latitude - the one the map opens on. The pure translation is
    `core.zoom_range`; the one number it cannot work out alone is what QGIS's
    scale readout means in this project's CRS, which is measured here.
    """
    if not any(layer.scale_range.is_set for layer in layers):
        return layers

    longitude, latitude = extent.center if extent is not None else (0.0, 0.0)
    factor = _qgis_scale_factor(basis, longitude, latitude)
    edges = (
        [
            (edge, _qgis_scale_factor(basis, longitude, edge))
            for edge in (extent.south, extent.north)
        ]
        if extent is not None
        else None
    )

    translated: list[ExportLayer] = []
    for layer in layers:
        visibility = translate_scale_range(layer.scale_range, latitude, factor, edges)
        if visibility.status is not None:
            report.record(
                f"Scale visibility of '{layer.name}'",
                visibility.status,
                visibility.detail,
                layer.layer_id,
            )
        if visibility.zoom_range is not None:
            layer = dataclasses.replace(layer, visible_zoom_range=visibility.zoom_range)
        translated.append(layer)
    return translated


def _declutter(
    layers: list[ExportLayer], report: FidelityReportBuilder
) -> list[ExportLayer]:
    """Cluster dense points and hold back crowded labels, and say so.

    After the zoom ranges, because a label limit inside the layer's own range
    changes nothing and is dropped. The working-out is `core.declutter`.
    """
    decluttered: list[ExportLayer] = []
    for layer in layers:
        layer, stacked = declutter_noting_stacks(layer, DEFAULT_LABEL_SIZE)
        if layer.cluster_max_zoom is not None:
            report.approximated(
                f"Markers of '{layer.name}'",
                f"{layer.feature_count:,} points pile up when zoomed out, so they "
                "merge into count bubbles until zoom "
                f"{layer.cluster_max_zoom} and show one by one from zoom "
                f"{layer.cluster_max_zoom + 1}. Clicking a bubble zooms in to "
                "its points."
                + (
                    " Points at exactly the same spot stay one bubble at every "
                    "zoom, showing how many there are."
                    if stacked
                    else ""
                ),
                layer.layer_id,
            )
        if labels_never_fit(layer):
            # What the labelling translator said about them no longer holds.
            report.discard(f"Labels on '{layer.name}'", layer.layer_id)
            report.unsupported(
                f"Labels on '{layer.name}'",
                "QGIS hides labels that would overlap; the web map cannot, and "
                "these labels only have room past the last zoom this layer "
                "shows at, so they are left out. A larger scale range in QGIS, "
                "or shorter or fewer labels, would bring them back.",
                layer.layer_id,
            )
            # Unlabelled from here on, so the switcher, the facts and the
            # terrain note agree with the map rather than with QGIS.
            layer = dataclasses.replace(
                layer,
                labeling=dataclasses.replace(layer.labeling, enabled=False),
                label_min_zoom=None,
            )
        elif layer.label_min_zoom is not None:
            report.approximated(
                f"Labels on '{layer.name}'",
                "QGIS hides labels that would overlap; the web map cannot, so "
                f"these labels show from zoom {layer.label_min_zoom}, where most "
                "of them have room.",
                layer.layer_id,
            )
        decluttered.append(layer)
    return decluttered


@dataclasses.dataclass(frozen=True)
class ScaleBasis:
    """What `_qgis_scale_factor` needs from the project, copied out.

    The CRS and transform context are value types and the scale method is an
    enum, so unlike the project they can cross to a worker thread.
    """

    crs: QgsCoordinateReferenceSystem
    transform_context: QgsCoordinateTransformContext
    method: Any | None

    @classmethod
    def of(cls, project: QgsProject) -> ScaleBasis:
        """**GUI thread only.**"""
        return cls(
            crs=project.crs(),
            transform_context=project.transformContext(),
            method=project.scaleMethod(),
        )


def _qgis_scale_factor(basis: ScaleBasis, longitude: float, latitude: float) -> float:
    """QGIS's scale readout divided by the true ground scale, at one point.

    Measured with QGIS's own `QgsScaleCalculator` - in the project's CRS and
    with the project's scale method - on a small box around the point, against
    the box's true width on the WGS84 ellipsoid. 1.0 in a metre-based
    projection; about `1 / cos(latitude)` in EPSG:3857; whatever QGIS's
    latitude method gives in a geographic CRS. Anything that cannot be measured
    falls back to 1.0, the metre-based case, rather than failing the export.
    """
    crs = basis.crs
    if not crs.isValid():
        return 1.0
    half = 0.005
    pixels = 1000
    try:
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(WGS84), crs, basis.transform_context
        )
        box = transform.transformBoundingBox(
            QgsRectangle(
                longitude - half, latitude - half, longitude + half, latitude + half
            )
        )
        calculator = QgsScaleCalculator(96, crs.mapUnits())
        if basis.method is not None:
            calculator.setMethod(basis.method)
        reported = calculator.calculate(box, pixels)
        distance = QgsDistanceArea()
        distance.setEllipsoid("WGS84")
        metres = distance.measureLine(
            QgsPointXY(longitude - half, latitude),
            QgsPointXY(longitude + half, latitude),
        )
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return 1.0
    true_scale = metres / (pixels * CSS_PIXEL_METRES)
    if not (reported > 0 and true_scale > 0):
        return 1.0
    return float(reported / true_scale)


def _resolve_extent(
    layers: list,
    report: FidelityReportBuilder,
    settings: ExportSettings | None = None,
    canvas_extent: Extent | None = None,
) -> Extent | None:
    """The extent the map opens on.

    The data extent is the default and stays antimeridian-aware. The canvas
    extent matches what the author had on screen - what qgis2web does, and all
    it offers - but it is a plain rectangle from the map canvas, so a view
    spanning the 180th meridian cannot be expressed and the data extent is used
    instead rather than opening on the whole world backwards.
    """
    if settings is not None and settings.extent_source is ExtentSource.CANVAS:
        if canvas_extent is None:
            report.unsupported(
                "Map extent",
                "The current canvas extent could not be read, so the map opens "
                "on the data instead.",
            )
        elif canvas_extent.crosses_antimeridian:
            report.approximated(
                "Map extent",
                "The canvas view crosses the 180th meridian, which a canvas "
                "rectangle cannot describe. The map opens on the data extent "
                "instead, which handles the wrap correctly.",
            )
        else:
            report.preserved(
                "Map extent",
                "The map opens on the extent shown in the QGIS canvas.",
            )
            return canvas_extent

    return _data_extent(layers, report)


def _data_extent(layers: list, report: FidelityReportBuilder) -> Extent | None:
    """Union of the layers' data extents, antimeridian-aware.

    A raster contributes exactly as a vector does, from a different field. It
    has no geometry to measure, but `RasterSpec.extent` is already the layer's
    footprint in WGS84 - `layer_reader` transforms it there precisely so it can
    join this union without a second reprojection. Filtering on `geojson` alone
    meant a raster-only project computed no extent at all and the map opened at
    world view with the raster somewhere off screen, and a mixed project framed
    only its vectors.
    """
    per_layer: list[Extent | None] = []
    for layer in layers:
        if layer.raster is not None:
            # `None` when the source CRS could not be transformed; passed
            # through rather than skipped because `union_extents` already
            # ignores it, and dropping it here would only hide the case.
            per_layer.append(layer.raster.extent)
        elif layer.geojson:
            per_layer.append(extent_from_geojson(layer.geojson))
    extent = union_extents(per_layer)

    if extent is None:
        report.unsupported(
            "Map extent",
            "No extent could be computed because no layer has features or a "
            "readable extent. The map will open at world view.",
        )
        return None

    if extent.crosses_antimeridian:
        report.preserved(
            "Map extent",
            "The data crosses the 180 degree meridian. The map opens on the data "
            f"({extent.width_degrees:.1f} degrees wide) rather than on the whole "
            "world, which is what a simple bounding box would have produced.",
        )
    else:
        report.preserved(
            "Map extent",
            f"The map opens on the data extent ({extent.width_degrees:.1f} "
            "degrees wide).",
        )

    return extent


def _report_project_metadata(
    project_title: str,
    abstract: str,
    resolved_title: str,
    title_override: str | None,
    report: FidelityReportBuilder,
) -> None:
    """Say where the title came from, and note an unused project title.

    The incumbent reads a project title and silently drops it. If the dialog's
    field overrides a different project title, the user should be told which one
    reached the map rather than left to discover it in the output.
    """
    if title_override and project_title and title_override.strip() != project_title:
        report.approximated(
            "Map title",
            f"The exported map is titled '{resolved_title}', from the Map name "
            f"field. The project's own title ('{project_title}') is not used.",
        )
    elif resolved_title == DEFAULT_TITLE:
        report.approximated(
            "Map title",
            "No map name was set and the project has no title, so the map is "
            f"called '{DEFAULT_TITLE}'. Set a Map name to change it.",
        )
    else:
        report.preserved("Map title", f"The map is titled '{resolved_title}'.")

    if not abstract.strip():
        # Not a problem - just absent. Recorded so the Fidelity tab can show what
        # a richer export would have included.
        report.preserved(
            "Map description",
            "The project has no description, so there is none to carry over - "
            "the map matches QGIS. To give the map one, write it under Project "
            "Properties > Metadata > Abstract.",
        )
