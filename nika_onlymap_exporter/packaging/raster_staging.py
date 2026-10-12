"""Getting a raster's pixels out of the user's disk and into the artifact.

`raster_cog.py` knows how to turn a GeoTIFF into a Cloud-Optimized GeoTIFF.
`RasterSpec` knows the difference between the file QGIS reads (`path`) and the
reference the artifact carries (`src`). Nothing joined the two, so a raster
layer reached the manifest with `src` unset and `<om-layer src="...">` pointing
at `/home/someone/ortho.tif` -- a path that exists on exactly one machine in the
world and not on the recipient's. This module is the join.

**Placement is a property of the output mode, not of the raster.** The three
tiers put a payload in three different places and only one of them can hold a
raster comfortably:

* **Folder** writes the COG beside `index.html`. This is the tier rasters were
  designed for: the file is served over HTTP, so the runtime can do exactly
  what a COG exists for and fetch the handful of byte ranges it needs. Size is
  bounded by the disk, not by the artifact.
* **Share ZIP** writes the same sibling into the archive. It travels, but see
  `SIBLING_FETCH_NOTE`: the recipient has to serve the extracted folder rather
  than double-click into it, and that is said out loud in the fidelity report
  rather than discovered as a blank map.
* **Standalone HTML** has no sibling to write, so the converted file travels
  inside the page and the page answers the reader's byte-range requests from
  it - `packaging.raster_embed`. Converted in a temporary directory, handed to
  the writer as bytes, and refused past a size: the rule and the evidence live
  in `dependency_scanner.STANDALONE_RASTER_MAX_BYTES`, because what a tier can
  carry is one subject and belongs in one file.

**Nothing is ever dropped quietly.** `CONTRIBUTING.md` is explicit that a
silently broken artifact is the one unacceptable outcome, and a raster is the
easiest thing in this codebase to lose silently: it has no features to count, so
an export that omits it looks exactly like an export that worked. Every path out
of this module therefore either sets `src` to something the recipient can fetch
or records a `blocked` item with a remedy in it, and a blocked item stops the
write.

**The GDAL half is injectable.** `converter` defaults to `raster_cog.to_cog`,
and the unit tier -- which runs under a plain interpreter with no `osgeo` --
passes a fake. That is the same seam `raster_cog` draws internally between its
probe and its rules, for the same reason: the interesting decisions here are
about placement and size, and none of them need a real raster to test.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.export_ir import ExportLayer, ExportProject, OutputMode
from ..core.fidelity_report import FidelityReportBuilder
from .dependency_scanner import (
    STANDALONE_RASTER_MAX_BYTES,
    format_megabytes,
    standalone_raster_reason,
)
from .raster_bake import StyleBakeError
from .raster_cog import (
    CancelCheck,
    CogError,
    CogResult,
    ProgressCallback,
    gdal_available,
    to_cog,
)

# Which tiers can put a file next to `index.html`. Standalone cannot by
# definition -- `StandaloneHtmlExporter` copies the entry file and nothing else,
# so a sibling written for it would be silently left behind in the staging
# directory, which is precisely the failure this module exists to prevent.
SIBLING_MODES = frozenset({OutputMode.FOLDER, OutputMode.SHARE_ZIP})

SIBLING_FETCH_NOTE = (
    "The raster travels as a separate file next to index.html. Browsers refuse "
    "to read a sibling file from a folder opened by double-click, so this map "
    "has to be served: upload the folder to a web server, or run "
    "`npx http-server` inside it and open the address it prints. The rest of "
    "the map draws either way; only the raster needs the server."
)
# Not `python3 -m http.server`, which this note used to name: it ignores
# `Range` and answers every request with the whole file, and the runtime reads
# a COG in byte ranges, so the raster came out as "Failed to decode deflate"
# and nothing on the map. Measured on Python 3.12 and 3.14; `http-server`
# answers 206.


def raster_file_name(layer_id: str, index: int) -> str:
    """The sibling file's name. Derived from the layer id, not the layer name.

    Layer names are free text -- they contain slashes, colons, emoji and, on a
    project translated from another tool, occasionally a whole path. A QGIS
    layer id is already an identifier, but it is not *only* safe characters, so
    it is still reduced here. `index` disambiguates: two layers whose ids
    reduce to the same slug (or to nothing at all) must not overwrite each
    other's pixels, and a collision would be invisible -- the second raster
    would simply draw the first one's image.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", layer_id).strip("-").lower()
    slug = slug[:48] or "raster"
    return f"layer-{index}-{slug}.tif"


@dataclass(frozen=True)
class StagedRaster:
    """What happened to one raster layer's pixels.

    Kept as data rather than folded straight into the report so the writer can
    add the sibling files to its `ArtifactResult` and a test can assert on the
    numbers, which are the ones that decide whether an artifact is sendable.
    """

    layer_id: str
    layer_name: str
    source_path: str
    source_bytes: int
    cog_bytes: int
    file_name: str
    # True when the source was already a web-ready COG and nothing was
    # converted -- `to_cog` skipped and the file was copied as it stood.
    # Worth carrying: it is the difference between an export that took two
    # minutes and one that took two seconds, and users ask.
    reused_source: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "layerId": self.layer_id,
            "sourceBytes": self.source_bytes,
            "cogBytes": self.cog_bytes,
            "fileName": self.file_name,
            "reusedSource": self.reused_source,
        }


@dataclass(frozen=True)
class RasterStagingResult:
    """The project with its rasters resolved, plus what was written for them.

    `project` is a new `ExportProject`: every `RasterSpec` that could be staged
    comes back with `src` and `is_cog` filled in, and everything else is the
    object that went in. Returning a project rather than mutating one keeps the
    model frozen all the way through packaging, so a caller that ignores the
    result gets the old behaviour rather than a half-updated model.
    """

    project: ExportProject
    files: tuple[Path, ...] = ()
    staged: tuple[StagedRaster, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    # The single-file tier's rasters: `(src, bytes)` for the writer to put in
    # the page, where `files` holds the other tiers' siblings.
    embedded: tuple[tuple[str, bytes], ...] = ()

    @property
    def can_export(self) -> bool:
        return not self.blocking_reasons

    def snapshot(self) -> dict[str, Any]:
        return {
            "staged": [item.snapshot() for item in self.staged],
            "files": [path.name for path in self.files],
            "blockingReasons": list(self.blocking_reasons),
        }


# `to_cog` with the arguments this module always passes, so a test can
# substitute the GDAL half without reproducing its full signature.
Converter = Callable[
    [str, str, "ProgressCallback | None", "CancelCheck | None"], CogResult
]


def _convert(
    source: str,
    destination: str,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCheck | None,
) -> CogResult:
    return to_cog(
        source, destination, on_progress=on_progress, should_cancel=should_cancel
    )


#: Renders a QGIS style into a raster's pixels. Source path, QML, destination;
#: returns the path written. Injectable for the same reason `Converter` is: the
#: unit tier has neither QGIS nor a raster, and the decisions worth testing here
#: are about what happens when it succeeds and when it fails.
Baker = Callable[[str, str, str], str]


def _bake(source: str, style_qml: str, destination: str) -> str:
    from .raster_bake import bake

    return bake(source, style_qml, destination)


def stage_rasters(
    project: ExportProject,
    destination: Path,
    mode: OutputMode,
    report: FidelityReportBuilder,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    converter: Converter | None = None,
    baker: Baker | None = None,
) -> RasterStagingResult:
    """Convert every raster layer and point its `RasterSpec.src` at the result.

    `destination` is the directory the artifact is being assembled in -- the
    same one the writer puts `index.html` in -- because the COG *is* part of the
    artifact and writing it anywhere else would mean copying it twice.

    A mode that cannot carry a sibling is refused **before** the conversion
    runs, not after: turning a gigabyte orthophoto into a COG takes minutes, and
    spending them to then say "this tier cannot hold it" is a cruel way to
    deliver an answer that was knowable at the start.

    Progress is reported across all rasters as one run: each raster contributes
    100 units to a total of `100 * n`, so a two-raster export moves 0-200 rather
    than twice from 0 to 100. `should_cancel` reaches GDAL unchanged.
    """
    rasters = [layer for layer in project.layers if layer.raster is not None]
    if not rasters:
        return RasterStagingResult(project=project)

    if mode is OutputMode.STANDALONE_HTML:
        # Converted beside nothing: the bytes go into the page. Staged in a
        # directory of our own so a failure leaves nothing in the user's.
        with tempfile.TemporaryDirectory(prefix="qgis2webmap-embed-") as work:
            staged_here = stage_rasters(
                project,
                Path(work),
                OutputMode.FOLDER,
                report,
                on_progress=on_progress,
                should_cancel=should_cancel,
                converter=converter,
                baker=baker,
            )
            if not staged_here.can_export:
                return staged_here
            total = sum(path.stat().st_size for path in staged_here.files)
            if total > STANDALONE_RASTER_MAX_BYTES:
                return _too_large_for_one_page(project, rasters, report, total)
            embedded = tuple(
                (path.name, path.read_bytes()) for path in staged_here.files
            )
        for layer in rasters:
            report.preserved(
                f"Raster '{layer.name}'",
                "Carried inside the page, so the single file opens by "
                "double-click with its imagery and needs no server.",
                layer.layer_id,
            )
        return dataclasses.replace(staged_here, files=(), embedded=embedded)

    if mode not in SIBLING_MODES:
        return _tier_cannot_carry(project, rasters, report)

    if converter is None:
        if not gdal_available():
            return _gdal_missing(project, rasters, report)
        converter = _convert

    destination.mkdir(parents=True, exist_ok=True)
    total_units = len(rasters) * 100

    layers: list[ExportLayer] = []
    files: list[Path] = []
    staged: list[StagedRaster] = []
    blocking: list[str] = []
    position = 0

    for layer in project.layers:
        if layer.raster is None:
            layers.append(layer)
            continue

        step = _scaled_progress(on_progress, position, total_units)
        position += 1

        outcome = _stage_one(
            layer=layer,
            index=position - 1,
            destination=destination,
            report=report,
            converter=converter,
            baker=baker or _bake,
            on_progress=step,
            should_cancel=should_cancel,
        )
        layers.append(outcome.layer)
        if outcome.staged is not None:
            staged.append(outcome.staged)
        if outcome.file is not None:
            files.append(outcome.file)
        if outcome.blocking_reason is not None:
            blocking.append(outcome.blocking_reason)

    if files and mode is OutputMode.SHARE_ZIP:
        # Said once for the archive rather than once per raster: the constraint
        # is a property of how the recipient opens the artifact, not of any
        # individual file.
        report.approximated("Raster files", SIBLING_FETCH_NOTE)

    return RasterStagingResult(
        project=dataclasses.replace(project, layers=tuple(layers)),
        files=tuple(files),
        staged=tuple(staged),
        blocking_reasons=tuple(blocking),
    )


@dataclass(frozen=True)
class _Outcome:
    """One layer's result. Internal: four returns with no name is worse."""

    layer: ExportLayer
    staged: StagedRaster | None = None
    file: Path | None = None
    blocking_reason: str | None = None


def _stage_one(
    layer: ExportLayer,
    index: int,
    destination: Path,
    report: FidelityReportBuilder,
    converter: Converter,
    baker: Baker,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCheck | None,
) -> _Outcome:
    raster = layer.raster
    assert raster is not None  # guarded by the caller; narrows for mypy

    file_name = raster_file_name(layer.layer_id, index)
    target = destination / file_name

    # The colours first, if this layer's are QGIS's doing rather than the
    # file's. The bake writes a plain RGBA GeoTIFF into a temporary directory
    # and `to_cog` converts that instead of the user's original; nothing below
    # this block knows the difference, which is the point.
    #
    # Staged through a temporary directory rather than beside the artifact so a
    # failure part-way leaves no half-rendered GeoTIFF in the user's output, and
    # so the intermediate - which can be several times the size of the COG - is
    # reclaimed as soon as the conversion has read it.
    with tempfile.TemporaryDirectory(prefix="qgis2webmap-bake-") as bake_dir:
        source = raster.path
        baked = False
        if raster.style_qml is not None:
            try:
                source = baker(
                    raster.path, raster.style_qml, str(Path(bake_dir) / "styled.tif")
                )
                baked = True
            except StyleBakeError as exc:
                # Not blocking. A raster drawn from its values is a worse map
                # than one drawn in its own colours, but it is still the map -
                # and `read_raster` has already promised the colours, so the
                # correction has to be louder than a debug line.
                report.unsupported(
                    f"Colours of '{layer.name}'", str(exc), layer.layer_id
                )

        try:
            result = converter(source, str(target), on_progress, should_cancel)
        except CogError as exc:
            # Every `CogError` message is written to be shown to a user and
            # carries its own next step, so it is passed through rather than
            # re-worded into something vaguer. Blocking, not omitting: a map
            # missing a layer the author put in it is the silent failure this
            # project refuses.
            reason = str(exc)
            report.blocked(f"Raster '{layer.name}'", reason, layer.layer_id)
            return _Outcome(layer=layer, blocking_reason=reason)

        # Never `target`: `to_cog` skips a source that is already a web-ready
        # COG and hands back the source path, so assuming our own path here
        # would read a file that was never written.
        produced = Path(result.destination)

        if produced != target:
            # The skip path, and the only copy this module makes: `to_cog`
            # declined to rewrite an already web-ready COG, so the artifact
            # takes the source file as it stands. Still a copy rather than a
            # reference -- the artifact has to be self-contained, and the user's
            # file is not ours to move. Inside the `with` because on the baked
            # path `produced` may BE the temporary file, which vanishes on exit.
            shutil.copyfile(produced, target)

    for warning in result.warnings:
        report.approximated(f"Raster '{layer.name}'", warning, layer.layer_id)

    report.preserved(f"Raster '{layer.name}'", result.describe(), layer.layer_id)

    # What the page has to say: the file's name, next to it.
    src = file_name

    staged = StagedRaster(
        layer_id=layer.layer_id,
        layer_name=layer.name,
        source_path=raster.path,
        source_bytes=result.source_bytes,
        cog_bytes=result.destination_bytes,
        file_name=file_name,
        reused_source=result.skipped,
    )
    return _Outcome(
        # `is_cog` is True on every path that reaches here, including the skip:
        # the file `src` names either came out of the COG driver or was already
        # a COG when `to_cog` declined to rewrite it. The field describes what
        # the artifact points at, not what the user's disk held.
        layer=dataclasses.replace(
            layer,
            raster=dataclasses.replace(
                raster,
                src=src,
                is_cog=True,
                # Cleared when the bake did not happen, and this is not
                # bookkeeping: `manifest_builder` reads `style_qml` to decide
                # whether the file's bands are colours or measurements, and
                # therefore whether `min`/`max`/`nodata` still describe it. A
                # failed bake that left this set would publish an unstyled
                # raster with every attribute that makes it readable suppressed.
                style_qml=raster.style_qml if baked else None,
            ),
        ),
        staged=staged,
        file=target,
    )


def _tier_cannot_carry(
    project: ExportProject,
    rasters: list[ExportLayer],
    report: FidelityReportBuilder,
) -> RasterStagingResult:
    """Refuse a single-file export of a raster, before spending GDAL's time.

    The reason text comes from `dependency_scanner.standalone_raster_reason` so
    that the dialog's "why is this tier greyed out" and this refusal are the
    same sentence rather than two accounts of one rule. `scan` normally catches
    this first; the check is repeated here because this is the module that
    would otherwise write a `src` nothing can fetch, and a guard that depends
    on being called in the right order is not a guard.
    """
    reason = standalone_raster_reason(project) or (
        "This output mode cannot carry a raster layer."
    )
    for layer in rasters:
        report.blocked(f"Raster '{layer.name}'", reason, layer.layer_id)
    return RasterStagingResult(project=project, blocking_reasons=(reason,))


def _too_large_for_one_page(
    project: ExportProject,
    rasters: list[ExportLayer],
    report: FidelityReportBuilder,
    total: int,
) -> RasterStagingResult:
    """Refuse a single file whose converted rasters are past the limit.

    Measured, so this can disagree with the scanner's estimate in the safe
    direction only: a source that compressed well passes here after the
    estimate let it through, and one that did not is caught.
    """
    reason = (
        f"The raster imagery comes to {format_megabytes(total)} once converted, "
        "too large to pack into a single HTML file (the limit is "
        f"{format_megabytes(STANDALONE_RASTER_MAX_BYTES)}). Choose Folder or "
        "Share ZIP as the export format instead."
    )
    for layer in rasters:
        report.blocked(f"Raster '{layer.name}'", reason, layer.layer_id)
    return RasterStagingResult(project=project, blocking_reasons=(reason,))


def _gdal_missing(
    project: ExportProject,
    rasters: list[ExportLayer],
    report: FidelityReportBuilder,
) -> RasterStagingResult:
    """Blocked, once per raster layer, with the reason a user can act on.

    Blocking rather than exporting the vectors and dropping the pixels. In
    QGIS this state cannot happen -- GDAL ships with it -- so reaching it means
    the export is running under a Python that is not the one inside QGIS, where
    the right answer is to say so rather than hand back a map that is quietly
    missing a layer.
    """
    reasons: list[str] = []
    for layer in rasters:
        reason = (
            f"'{layer.name}' is a raster layer, and converting it to a "
            "Cloud-Optimized GeoTIFF needs GDAL's Python bindings, which are "
            "not available in this Python. Run the export from inside QGIS, "
            "which ships GDAL, or remove the raster layer from the project."
        )
        report.blocked(f"Raster '{layer.name}'", reason, layer.layer_id)
        reasons.append(reason)
    return RasterStagingResult(project=project, blocking_reasons=tuple(reasons))


def _scaled_progress(
    on_progress: ProgressCallback | None, position: int, total_units: int
) -> ProgressCallback | None:
    """Map one raster's 0-100 into its slice of the whole run's progress."""
    if on_progress is None:
        return None
    offset = position * 100

    def step(done: int, _total: int | None) -> None:
        on_progress(offset + done, total_units)

    return step
