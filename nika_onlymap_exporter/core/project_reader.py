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

from typing import TYPE_CHECKING

from qgis.core import QgsLayerTreeLayer

from .export_ir import Color, ExportProject, ExportSettings, Extent
from .extent_math import extent_from_geojson, union_extents
from .fidelity_report import FidelityReportBuilder
from .layer_reader import read_layer

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


def read_project(
    project: QgsProject,
    report: FidelityReportBuilder,
    settings: ExportSettings | None = None,
    title_override: str | None = None,
    background_color: Color | None = None,
    selected_layer_ids: frozenset[str] | None = None,
) -> ExportProject:
    """Read a whole project into the normalized model.

    `selected_layer_ids` limits the export to layers the user ticked; `None`
    means every layer in the tree.
    """
    settings = settings or ExportSettings()
    root = project.layerTreeRoot()

    # findLayers() is top-first; the map draws bottom-first.
    tree_layers = list(reversed(root.findLayers()))

    layers = []
    skipped_invalid = 0

    for tree_layer in tree_layers:
        if not isinstance(tree_layer, QgsLayerTreeLayer):
            continue

        map_layer = tree_layer.layer()
        if map_layer is None or not map_layer.isValid():
            skipped_invalid += 1
            continue

        if selected_layer_ids is not None and map_layer.id() not in selected_layer_ids:
            continue

        export_layer = read_layer(
            map_layer,
            report,
            group_path=group_path_for(tree_layer),
            visible=tree_layer.isVisible(),
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

    extent = _resolve_extent(layers, report)
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


def _resolve_extent(layers: list, report: FidelityReportBuilder) -> Extent | None:
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
