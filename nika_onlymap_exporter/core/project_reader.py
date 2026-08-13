"""A QGIS project to an `ExportProject`.

Imports PyQGIS; exercised in `tests/qgis/`.

Two things here are worth reading before changing anything.

**Draw order is reversed on the way out.** QGIS's Layers panel lists the topmost
layer first, and the topmost layer draws *last*. OnlyMap stacks `<om-layer>`
children in document order, so the first child draws first, at the bottom.
Reversing is therefore correct, and forgetting to is the classic way to produce
an upside-down map where the basemap-ish polygon covers everything.

**Extent comes from the data, not the canvas.** The canvas extent seems like the
obvious choice - it is what the author was looking at - but for antimeridian-
crossing data QGIS's own canvas is already near-global, so the export faithfully
reproduces a useless view. See `extent_math`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from qgis.core import QgsLayerTreeLayer

from .export_ir import (
    Color,
    ExportProject,
    ExportSettings,
    Extent,
    ExtentSource,
)
from .extent_math import extent_from_geojson, union_extents
from .fidelity_report import FidelityReportBuilder
from .layer_reader import WGS84, read_layer
from .manifest_builder import TERRAIN_PRESETS, basemap_note, terrain_note
from .settings import LayerSettings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsLayerTreeNode, QgsProject

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
    for XYZ tile layers as well as real WMS, and both are backdrops we do not
    export. Anything unexpected is treated as "no", because a false warning
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


def extent_from_canvas(canvas: object) -> Extent | None:
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


def _project_terrain_kind(project: QgsProject) -> str | None:
    """The project's own terrain source, or `None` if the ground is flat.

    Guarded rather than called directly: `elevationProperties()` and its
    `terrainProvider()` arrived in different QGIS releases, and the plugin
    supports back to 3.22, where neither exists.
    """
    properties = getattr(project, "elevationProperties", lambda: None)()
    provider = getattr(properties, "terrainProvider", lambda: None)()
    kind = getattr(provider, "type", lambda: None)()
    if not isinstance(kind, str) or kind == "flat":
        return None
    return kind


def _report_terrain(
    project: QgsProject,
    settings: ExportSettings,
    report: FidelityReportBuilder,
) -> None:
    """What happened to relief - which is never a translation, always a swap.

    A QGIS terrain is a DEM on the user's disk, and the web map needs elevation
    tiles it can fetch. There is no route from one to the other short of tiling
    and hosting the DEM, so the honest options are global relief from a public
    tileset or none at all. Both are stated rather than assumed.
    """
    note = terrain_note(settings.terrain, basemap=settings.basemap)
    if note is not None:
        report.approximated("Terrain", note)
        kind = _project_terrain_kind(project)
        if kind is not None:
            report.approximated(
                "Terrain",
                f"The relief is global elevation data, not this project's own "
                f"{kind} terrain, which cannot be sent with the map. Heights "
                "will be close but not identical to the QGIS 3D view.",
            )
        return

    kind = _project_terrain_kind(project)
    if kind is not None:
        report.unsupported(
            "Terrain",
            f"This project uses a {kind} terrain, which is not exported - a DEM "
            "on your disk cannot travel with the map. Switch Terrain on under "
            "the Map tab for global relief instead, or the map exports flat.",
        )


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
) -> ExportProject:
    """Read a whole project into the normalized model.

    `selected_layer_ids` limits the export to layers the user ticked; `None`
    means every layer in the tree.

    `layer_settings` carries the dialog's per-layer popup and label checkboxes,
    keyed by layer id. A layer absent from the mapping keeps the defaults, so a
    caller with no dialog (the Processing algorithm, a test) can omit it.

    `progress` is called as `(done, total, layer_name)` before each layer is
    read. Reading is the slow half of an export - every feature of every layer -
    and it is the half a user has no other way to watch, so the dialog drives its
    progress bar from here. A caller that wants none passes `None` and pays
    nothing.
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

    # findLayers() is top-first; the map draws bottom-first.
    tree_layers = list(reversed(root.findLayers()))

    layers = []
    skipped_invalid = 0

    # Counted up front so the progress bar can show "3 of 12" rather than a bar
    # that only ever fills at the end.
    total = sum(
        1
        for tree_layer in tree_layers
        if isinstance(tree_layer, QgsLayerTreeLayer)
        and tree_layer.layer() is not None
        and tree_layer.layer().isValid()
        and (
            selected_layer_ids is None or tree_layer.layer().id() in selected_layer_ids
        )
    )
    done = 0

    for tree_layer in tree_layers:
        if not isinstance(tree_layer, QgsLayerTreeLayer):
            continue

        map_layer = tree_layer.layer()
        if map_layer is None or not map_layer.isValid():
            skipped_invalid += 1
            continue

        if selected_layer_ids is not None and map_layer.id() not in selected_layer_ids:
            continue

        if progress is not None:
            progress(done, total, map_layer.name())
        done += 1

        per_layer = (layer_settings or {}).get(map_layer.id()) or LayerSettings()
        export_layer = read_layer(
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
        )
        if export_layer is not None:
            layers.append(export_layer)

    if skipped_invalid:
        report.blocked(
            "Project layers",
            f"{skipped_invalid} layer(s) could not be loaded - their data source "
            "is missing or unreadable. Fix the broken layers in QGIS, or remove "
            "them, before exporting.",
        )

    if not layers:
        report.blocked(
            "Project layers",
            "There is nothing to export. Add at least one vector layer with "
            "features to the project.",
        )

    if settings.quantize_precision is not None:
        report.approximated(
            "Coordinate precision",
            f"Coordinates are rounded to {settings.quantize_precision} decimal "
            "place(s) to make the file smaller. This is the one setting that "
            "discards data - the exported geometry is no longer identical to "
            "the source.",
        )

    # A basemap is the only setting whose cost falls on the *recipient*, and it
    # cannot be undone once the file is sent - so it belongs in the report the
    # user reads before exporting, not only in the dialog they already left.
    note = basemap_note(settings.basemap)
    if note is not None:
        report.approximated("Basemap", note)
    elif _has_tile_layer(project):
        # The project has a backdrop and the export will not. Silence here is
        # how someone ships a map of bare shapes floating on white and only
        # finds out when the recipient asks what they are looking at.
        report.unsupported(
            "Basemap",
            "This project has a tile layer, but the export has no basemap, so "
            "the map will have a blank background. Choose one under Basemap on "
            "the Map tab if you want a backdrop - note that it makes the map "
            "load tiles from the internet each time it is opened.",
        )

    _report_terrain(project, settings, report)

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

        # Measured, not assumed: label layers render neither draped nor
        # offset while terrain is on - the text simply never appears.
        labelled = [
            layer.name
            for layer in layers
            if layer.labeling.enabled and layer.labeling.field_name
        ]
        if labelled:
            report.unsupported(
                "Terrain",
                "Labels do not appear over relief in the current map runtime. "
                "The labelled layers still draw, but without their text: "
                + ", ".join(f"'{name}'" for name in labelled)
                + ". Turn relief off if the labels matter more.",
            )

    extent = _resolve_extent(layers, report, settings, canvas_extent)
    title = resolve_title(project, title_override)

    _report_project_metadata(project, title, title_override, report)

    return ExportProject(
        title=title,
        layers=tuple(layers),
        abstract=(project.metadata().abstract() or None),
        extent=extent,
        background_color=background_color,
        source_crs=project.crs().authid() if project.crs().isValid() else None,
        settings=settings,
        fidelity=report.items,
    )


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
    """Union of the layers' data extents, antimeridian-aware."""
    per_layer = [
        extent_from_geojson(layer.geojson) for layer in layers if layer.geojson
    ]
    extent = union_extents(per_layer)

    if extent is None:
        report.unsupported(
            "Map extent",
            "No extent could be computed because no layer has features. The map "
            "will open at world view.",
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
    project: QgsProject,
    resolved_title: str,
    title_override: str | None,
    report: FidelityReportBuilder,
) -> None:
    """Say where the title came from, and note an unused project title.

    The incumbent reads a project title and silently drops it. If the dialog's
    field overrides a different project title, the user should be told which one
    reached the map rather than left to discover it in the output.
    """
    project_title = (project.title() or "").strip()

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

    if not (project.metadata().abstract() or "").strip():
        # Not a problem - just absent. Recorded so the Fidelity tab can show what
        # a richer export would have included.
        report.preserved(
            "Map description",
            "No project abstract is set, so the map has no description. Add one "
            "in Project Properties > Metadata if you want one.",
        )
