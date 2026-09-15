"""What an export needs, and whether it can be made portable.

Runs **before** anything is written. The point is to answer "will this artifact
actually work for the person who receives it?" while there is still time to say
no, rather than discovering the answer when they open a blank page.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from ..core.export_ir import (
    AssetDisposition,
    ExportProject,
    OutputMode,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.manifest_builder import TERRAIN_PRESETS

# Past this, a single HTML file stops being a practical thing to email: Gmail
# rejects attachments over 25 MB, and many corporate filters are stricter.
SINGLE_FILE_WARN_BYTES = 20 * 1024 * 1024

# The single-file tier cannot carry a raster at any size. This was measured on
# the pinned runtime rather than assumed, and the finding is worse than the
# size problem it was expected to be:
#
# * `COGLayer`'s `src` is handed to the bundled cogeotiff reader's `fromUrl`,
#   which opens the file as a *chunked* source -- 64 KiB header chunks, and tile
#   reads issued as `Range: bytes=N-M` straight to `fetch`.
# * Its HTTP source checks only `response.ok` and returns `arrayBuffer()`. There
#   is no `allowFullFile` guard anywhere in the bundle and no check for a 206.
# * A `data:` URI answers every request with **200 and the entire body**;
#   browsers ignore `Range` on it (measured in Chromium 151). The reader
#   therefore slices a full-file buffer at an offset meant for a chunk.
#
# So a `data:`-embedded COG larger than the first 64 KiB chunk does not fail --
# it reads the wrong bytes, which is the silent breakage `CONTRIBUTING.md` puts
# above every other consideration. The same measurement rules out the obvious
# fallback: a `file://` sibling cannot be fetched at all from a page opened by
# double-click, and with the browser flag that permits it the response is again
# a 200 full file. Hence a refusal rather than a threshold. If the runtime grows
# an in-memory entry point for `src` (its reader already has `fromArrayBuffer`,
# just not reachable from an attribute), this becomes a size limit instead.
STANDALONE_CARRIES_RASTERS = False

# Past this, a zip stops being a thing you can hand someone. It is ten times
# `SINGLE_FILE_WARN_BYTES` and not the same number, because the zip tier is
# already the answer to "the single file was too big" -- warning at 20 MB would
# fire on every raster export and mean nothing. 200 MB is roughly where the
# free file-transfer services and the patience of a recipient on a hotel
# connection both run out. Note that zipping does not help here: a COG is
# already DEFLATE-compressed internally, so the archive is about the size of the
# raster inside it.
SHARE_ZIP_WARN_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class ScanResult:
    """What the export will contain and whether it can proceed."""

    data_bytes: int
    remote_dependencies: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    credentials_detected: tuple[str, ...] = ()

    @property
    def can_export(self) -> bool:
        return not self.blocking_reasons

    @property
    def is_offline(self) -> bool:
        return not self.remote_dependencies

    def snapshot(self) -> dict[str, Any]:
        return {
            "dataBytes": self.data_bytes,
            "remoteDependencies": list(self.remote_dependencies),
            "blockingReasons": list(self.blocking_reasons),
            "credentialsDetected": list(self.credentials_detected),
        }


def format_megabytes(count: int) -> str:
    """How this module talks about sizes. One spelling, so two messages about
    the same file cannot round it two different ways."""
    return f"{count / 1024 / 1024:.0f} MB"


def raster_bytes(project: ExportProject) -> int:
    """Bytes of raster the artifact has to carry, measured on the source files.

    An estimate, and knowingly the wrong one in both directions: the artifact
    carries the *converted* COG, which is usually smaller than an uncompressed
    source and can be larger than a JPEG-compressed one. The real number only
    exists after GDAL has run, which is minutes of work on a big orthophoto and
    cannot happen on every keystroke in a dialog. The source size is the only
    figure available before then and it is the right order of magnitude, which
    is all an eligibility check needs -- `raster_staging` re-decides on the
    measured size once the conversion is done, and that one is authoritative.

    Read from the layer's own `AssetDependency` where the reader recorded it, so
    the common path touches no disk at all; `os.path.getsize` is the fallback
    for a project assembled without one.
    """
    total = 0
    for layer in project.exportable_layers:
        if layer.raster is None:
            continue
        recorded = next(
            (
                dependency.size_bytes
                for dependency in layer.dependencies
                if dependency.identifier == layer.raster.path
                and dependency.size_bytes is not None
            ),
            None,
        )
        if recorded is not None:
            total += recorded
            continue
        try:
            total += os.path.getsize(layer.raster.path)
        except OSError:
            # A missing raster is already a blocking dependency; adding a guess
            # for it here would only make the size message wrong as well.
            continue
    return total


def measure_data_bytes(project: ExportProject) -> int:
    """Serialised size of all layer data, as it will appear in the artifact.

    Rasters are counted alongside GeoJSON, because to every consumer of this
    number -- the eligibility check, the compression decision, the size warning
    -- a megabyte of pixels weighs exactly as much as a megabyte of
    coordinates. While this counted GeoJSON only, a 400 MB orthophoto was
    invisible: `standalone_ineligible_reason` cheerfully approved a project
    that could not be written into an openable file.
    """
    total = raster_bytes(project)
    for layer in project.exportable_layers:
        if layer.geojson is not None:
            total += len(
                json.dumps(layer.geojson, separators=(",", ":")).encode("utf-8")
            )
    return total


def standalone_raster_reason(project: ExportProject) -> str | None:
    """Why a raster stops this project shipping as one HTML file, or `None`.

    One function, two callers, on purpose: `standalone_ineligible_reason` uses
    it to steer the dialog away from the tier before the user commits, and
    `scan` uses it to refuse the export if they got there anyway. A second copy
    of the rule is how a dialog and an exporter start disagreeing.

    See `STANDALONE_CARRIES_RASTERS` for the measurement behind the refusal.
    The message names the raster because a project can hold twenty layers and
    "there is a raster somewhere" is not something a user can act on.
    """
    if STANDALONE_CARRIES_RASTERS:  # pragma: no cover - a flag, not a branch
        return None

    names = [
        layer.name for layer in project.exportable_layers if layer.raster is not None
    ]
    if not names:
        return None

    listed = ", ".join(f"'{name}'" for name in names)
    plural = "layers" if len(names) > 1 else "layer"
    return (
        f"{listed} {'are' if len(names) > 1 else 'is'} a raster {plural}. A "
        "single HTML file has to carry the pixels as text inside the page, and "
        "a map reading a Cloud-Optimized GeoTIFF that way cannot fetch the "
        "pieces of it that it needs, so it would draw the wrong image rather "
        "than say anything was wrong. Export as a Folder (or Share ZIP) and "
        "put it on a web server instead: the raster travels as its own file, "
        "and the map then loads only the part of it that is on screen."
    )


def hosted_relief_reason(project: ExportProject) -> str | None:
    """Why relief cannot be published to NIKA hosting yet, or `None`.

    Relief is the one feature whose export is not complete without a script. The
    public elevation tiles stop at a scale of roughly 1:2.5 km and the surface
    blanks past them rather than magnifying, so every other tier ships
    `artifact_builder.terrain_zoom_clamp` -- an inline `<script>` that glides the
    camera back to the closest zoom that still renders. A hosted page is served
    under a Content-Security-Policy whose `script-src` names only the pinned
    runtime, so that script is refused twice over: publish-time conformance
    rejects the artifact for carrying it, and a browser would refuse to run it
    if it got through.

    **There is no declarative equivalent to drop it in favour of.** Checked
    against the pinned runtime's own attribute vocabulary
    (`onlymapjs.html-data.json` in @nika-js/onlymap 0.7.6, the build
    `runtime-lock.json` pins): `<om-map>` has `zoom` for the *initial* camera
    and no clamp of any kind, `<om-behavior>` has no view-change trigger and no
    camera action to pair with one, and `terrain-max-zoom` -- the attribute
    whose name suggests it -- is the DEM tileset's own zoom cap, documented
    there as "the provider's REAL limit". Setting it low does not stop the
    camera; it only changes which tiles are asked for, so the map would still
    zoom past the point where the picture holds.

    So the choice is between publishing a relief map that misbehaves at close
    zoom and refusing one, and this project's rule is that the silently broken
    artifact is the unacceptable outcome. It refuses, and names both remedies:
    the other tiers still carry the clamp, and a flat map publishes today.
    """
    # The same test `terrain_zoom_clamp` makes, and it has to be: a value the
    # manifest will not emit is a map with no relief in it, and refusing one of
    # those would block a publish over an attribute nothing ever writes.
    terrain = project.settings.terrain
    if terrain == "none" or terrain not in TERRAIN_PRESETS:
        return None

    return (
        "This map has relief switched on, and a relief map cannot be published "
        "to NIKA hosting yet. Relief detail ends at about a 1:2.5 km scale, and "
        "the exported map needs a small script to stop the camera there -- past "
        "it the terrain blanks instead of magnifying. A hosted map is served "
        "under a security policy that runs no script but the map runtime "
        "itself, so that correction cannot travel with it, and the runtime has "
        "no attribute that does the same job. Turn relief off to publish this "
        "map, or export it as a Folder or Share ZIP and put it on your own web "
        "server, where the correction still works."
    )


def standalone_ineligible_reason(project: ExportProject) -> str | None:
    """Why this project cannot ship as one HTML file, or `None` if it can.

    Issue #29's rule: Standalone HTML is the default *when it is eligible*, and
    when it is not the user is told exactly why rather than handed a file that
    is impractical to send. Pure, so the dialog can call it on every change
    without touching disk.

    The raster clause is a separate rule rather than a bigger number in the
    size one, because it is a different failure at a different threshold --
    namely none. Too much GeoJSON makes a file that works and is annoying to
    send; *any* raster makes a file that draws the wrong image. It is checked
    first so the message names the real problem.
    """
    raster = standalone_raster_reason(project)
    if raster is not None:
        return raster

    data_bytes = measure_data_bytes(project)
    if data_bytes > SINGLE_FILE_WARN_BYTES:
        return (
            f"The layer data is {format_megabytes(data_bytes)}. A single HTML "
            f"file over {SINGLE_FILE_WARN_BYTES // 1024 // 1024} MB is slow to "
            "open and awkward to move around, so Share ZIP is the practical "
            "choice."
        )
    return None


def scan(
    project: ExportProject,
    report: FidelityReportBuilder,
    mode: OutputMode = OutputMode.STANDALONE_HTML,
    hosted: bool = False,
) -> ScanResult:
    """Classify every dependency and decide whether the export can proceed.

    `hosted` is the served destination rather than a fourth tier -- it is
    `OutputMode.FOLDER` plus a Content-Security-Policy -- so it arrives as a
    flag beside the mode. It adds one refusal of its own: see
    `hosted_relief_reason`.
    """
    remote: list[str] = []
    blocking: list[str] = []
    credentials: list[str] = []

    for layer in project.exportable_layers:
        for dependency in layer.dependencies:
            if dependency.credentials_detected:
                credentials.append(layer.name)
                # The credential itself never enters the model, so there is
                # nothing to leak - but the user should know their source needed
                # one, because the recipient will not have it.
                report.preserved(
                    f"Data source of '{layer.name}'",
                    "The source needed a username or password. The features are "
                    "embedded in the map, so the recipient needs no credentials "
                    "and none are written into the file.",
                    layer.layer_id,
                )

            if dependency.disposition is AssetDisposition.REMOTE:
                remote.append(dependency.identifier)
                report.approximated(
                    f"Data source of '{layer.name}'",
                    f"'{dependency.identifier}' stays a live reference, so the "
                    "map needs an internet connection to draw this layer.",
                    layer.layer_id,
                )

            if dependency.disposition is AssetDisposition.BLOCKING:
                reason = dependency.note or f"'{dependency.identifier}' is missing."
                blocking.append(reason)
                report.blocked(f"Data source of '{layer.name}'", reason, layer.layer_id)

    if not project.exportable_layers:
        blocking.append(
            "There are no layers to export. Add a vector layer with features."
        )

    if mode is OutputMode.STANDALONE_HTML:
        # Blocking, not warning. The failure it prevents is a map that draws
        # the wrong pixels without reporting anything, which no amount of
        # warning text at export time makes acceptable -- and the remedy is one
        # change of output mode, which the message names.
        raster_reason = standalone_raster_reason(project)
        if raster_reason is not None:
            blocking.append(raster_reason)
            for layer in project.exportable_layers:
                if layer.raster is not None:
                    report.blocked(
                        f"Layer '{layer.name}'", raster_reason, layer.layer_id
                    )

    if hosted:
        # Blocking for the same reason the standalone raster rule is: the map
        # would be published and would then misbehave at close zoom with
        # nothing anywhere saying why. The remedy is in the message.
        relief_reason = hosted_relief_reason(project)
        if relief_reason is not None:
            blocking.append(relief_reason)
            report.blocked("Relief", relief_reason)

    data_bytes = measure_data_bytes(project)

    if mode is OutputMode.SHARE_ZIP and data_bytes > SHARE_ZIP_WARN_BYTES:
        # A raster changed what "large" means here. Before rasters, layer data
        # was GeoJSON and a project big enough to trip this would have been an
        # extraordinary vector export; one orthophoto passes it on its own. The
        # remedy is a different one too -- there is no larger tier to move up
        # to, so the advice is about how to deliver it rather than how to build
        # it.
        report.approximated(
            "Artifact size",
            f"The map data is {format_megabytes(data_bytes)}, most of it raster "
            "imagery, and zipping does not shrink it further -- a "
            "Cloud-Optimized GeoTIFF is already compressed. Expect a download "
            "rather than an email attachment, and consider exporting a smaller "
            "area or a coarser raster if the recipient's connection is a "
            "concern.",
        )

    if mode is OutputMode.STANDALONE_HTML and data_bytes > SINGLE_FILE_WARN_BYTES:
        # Deliberately a warning and not a blocker, even for a raster over
        # `STANDALONE_RASTER_MAX_BYTES`. The number here is measured on the
        # *source* files, and a source that compresses well converts to a COG
        # that fits comfortably -- refusing on the estimate would turn a
        # perfectly exportable map away. `raster_staging` refuses on the
        # measured size of the converted file instead, which is the only figure
        # that can be right, and it does so before anything is written.
        report.approximated(
            "Artifact size",
            f"The map data is {format_megabytes(data_bytes)}, so a single HTML "
            "file will be slow to open and awkward to move around. Share ZIP is "
            "a better fit for a map this size.",
        )

    return ScanResult(
        data_bytes=data_bytes,
        remote_dependencies=tuple(remote),
        blocking_reasons=tuple(blocking),
        credentials_detected=tuple(dict.fromkeys(credentials)),
    )
