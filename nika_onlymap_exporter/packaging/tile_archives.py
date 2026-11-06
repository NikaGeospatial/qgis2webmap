"""Carrying a local PMTiles archive with the map, as each output type can.

A layer read from a PMTiles archive on this computer (`core.vector_tiles`)
reaches packaging naming the file where QGIS reads it. The runtime reads an
archive in pieces, with HTTP range requests, exactly as it reads a COG - so
the archive travels the way a raster does (`packaging.raster_staging`):

* **Folder** and **Share ZIP** copy the archive beside `index.html`. A browser
  will not read a sibling file from a page opened by double-click, so a zip,
  which its recipient opens that way, says it has to be served
  (`ARCHIVE_FETCH_NOTE`); a folder goes on a website.
* **Standalone HTML** carries the archive inside the page, and the page
  answers the runtime's range requests from it (`packaging.raster_embed`),
  up to the size one page carries with its rasters
  (`dependency_scanner.STANDALONE_RASTER_MAX_BYTES`).

Each archive is carried once, however many of its layers the map draws.
A remote archive is the web's to serve and is left as it is.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..core.export_ir import ExportLayer, ExportProject, OutputMode
from ..core.fidelity_report import FidelityReportBuilder
from .dependency_scanner import STANDALONE_RASTER_MAX_BYTES, format_megabytes

ARCHIVE_FETCH_NOTE = (
    "The tile archive travels as a separate file next to index.html. Browsers "
    "refuse to read a sibling file from a folder opened by double-click, so "
    "this map has to be served: upload the folder to a web server, or run "
    "`npx http-server` inside it and open the address it prints. The rest of "
    "the map draws either way; only the archive's layers need the server."
)


@dataclass(frozen=True)
class TileArchiveStaging:
    """The project with each local archive's layers pointed at what the page
    reads, and the archives written beside it or to carry inside it.

    `names` is the name the page reads each archive by, by its path here.
    """

    project: ExportProject
    files: tuple[Path, ...] = ()
    embedded: tuple[tuple[str, bytes], ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    names: Mapping[str, str] = field(default_factory=dict)

    @property
    def can_export(self) -> bool:
        return not self.blocking_reasons


def archive_file_name(path: str, index: int) -> str:
    """The carried archive's name: its own, made safe, numbered so two
    archives of the same name cannot overwrite each other."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(path).stem).strip("-").lower()
    return f"archive-{index}-{stem[:48] or 'tiles'}.pmtiles"


def local_archive_layers(project: ExportProject) -> list[ExportLayer]:
    """The layers drawn from an archive on this computer."""
    return [
        layer
        for layer in project.exportable_layers
        if layer.vector_tiles is not None and layer.vector_tiles.is_archive
    ]


def archive_bytes(
    project: ExportProject, carried: Mapping[str, str] | None = None
) -> int:
    """The bytes of every local archive the map carries, each counted once,
    leaving out those already `carried`."""
    sizes = {
        layer.vector_tiles.path: layer.vector_tiles.size_bytes
        for layer in local_archive_layers(project)
        if layer.vector_tiles is not None
        and layer.vector_tiles.path not in (carried or {})
    }
    return sum(sizes.values())


def stage_tile_archives(
    project: ExportProject,
    destination: Path,
    mode: OutputMode,
    report: FidelityReportBuilder,
    embedded_bytes: int = 0,
    carried: Mapping[str, str] | None = None,
) -> TileArchiveStaging:
    """Carry each local archive as `mode` carries files; see the module.

    `embedded_bytes` is what the page already carries (its rasters), which
    counts against the same single-file limit. `carried` is the archives a
    page with several maps (a cartograph's live frames) already carries for
    the maps before this one, by path: each is read by the name it has, and
    neither carried nor counted again, and a new one is numbered after them.
    """
    layers = local_archive_layers(project)
    if not layers:
        return TileArchiveStaging(project=project)

    carried = carried or {}
    new: dict[str, str] = {}
    for layer in layers:
        if layer.vector_tiles is None:
            continue
        path = layer.vector_tiles.path
        if path not in carried and path not in new:
            new[path] = archive_file_name(path, len(carried) + len(new))
    names = {**carried, **new}

    files: list[Path] = []
    embedded: list[tuple[str, bytes]] = []
    if mode is OutputMode.STANDALONE_HTML:
        total = embedded_bytes + archive_bytes(project, carried)
        if total > STANDALONE_RASTER_MAX_BYTES:
            reason = (
                "The tile archives and raster imagery come to "
                f"{format_megabytes(total)}, too large to pack into a single "
                "HTML file (the limit is "
                f"{format_megabytes(STANDALONE_RASTER_MAX_BYTES)}). Choose "
                "Folder or Share ZIP as the export format instead."
            )
            for layer in layers:
                report.blocked(f"Layer '{layer.name}'", reason, layer.layer_id)
            return TileArchiveStaging(project=project, blocking_reasons=(reason,))
        embedded = [(name, Path(path).read_bytes()) for path, name in new.items()]
        for layer in layers:
            report.preserved(
                f"Layer '{layer.name}'",
                "Its tile archive is carried inside the page, so the single "
                "file opens by double-click with this layer and needs no "
                "server.",
                layer.layer_id,
            )
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for path, name in new.items():
            target = destination / name
            shutil.copyfile(path, target)
            files.append(target)
        if mode is OutputMode.SHARE_ZIP:
            report.approximated("Tile archives", ARCHIVE_FETCH_NOTE)

    staged = tuple(
        dataclasses.replace(
            layer,
            vector_tiles=dataclasses.replace(
                layer.vector_tiles, url=names[layer.vector_tiles.path]
            ),
        )
        if layer.vector_tiles is not None and layer.vector_tiles.is_archive
        else layer
        for layer in project.layers
    )
    return TileArchiveStaging(
        project=dataclasses.replace(project, layers=staged),
        files=tuple(files),
        embedded=tuple(embedded),
        names=names,
    )
