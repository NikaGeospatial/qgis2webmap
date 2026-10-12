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
    ExportLayer,
    ExportProject,
    OutputMode,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.manifest_builder import FILE_OPENED_MODES

# Past this, a single HTML file stops being a practical thing to email: Gmail
# rejects attachments over 25 MB, and many corporate filters are stricter.
SINGLE_FILE_WARN_BYTES = 20 * 1024 * 1024

# The single-file tier carries its rasters inside the page. Not as a `data:`
# URI in `src`, which was measured and does not work: the runtime's COG reader
# issues `Range: bytes=N-M` requests through `fetch` and slices what comes back
# at the offset it asked for, and a `data:` URI answers every request with 200
# and the whole body, so the reader would draw the wrong bytes without an error.
# A `file://` sibling cannot be fetched from a page opened by double-click at
# all. So the page carries the converted file and answers the reader's range
# requests from it itself - `packaging.raster_embed` has the mechanism.
#
# What is left is a size limit, measured on the converted file. The page holds
# the raster as base64, a third larger, and the browser decodes all of it on
# open; past this a single file is slow to open and heavy to hold in memory,
# and Folder or Share ZIP - which stream the raster in pieces - are the
# practical tiers. The scanner tests the source size as an estimate before
# anything is converted; `raster_staging` decides on the real one.
STANDALONE_RASTER_MAX_BYTES = 100 * 1024 * 1024

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
    return sum(
        _raster_layer_bytes(layer)
        for layer in project.exportable_layers
        if layer.raster is not None
    )


def _raster_layer_bytes(layer: ExportLayer) -> int:
    """One raster's source size; see `raster_bytes`. 0 when it cannot be read."""
    raster = layer.raster
    if raster is None:
        return 0
    recorded = next(
        (
            dependency.size_bytes
            for dependency in layer.dependencies
            if dependency.identifier == raster.path
            and dependency.size_bytes is not None
        ),
        None,
    )
    if recorded is not None:
        return recorded
    try:
        return os.path.getsize(raster.path)
    except OSError:
        # A missing raster is already a blocking dependency; adding a guess
        # for it here would only make the size message wrong as well.
        return 0


def data_bytes_by_layer(project: ExportProject) -> dict[str, int]:
    """Each exported layer's share of `measure_data_bytes`, by layer id.

    The same measurement split per layer, so the Fidelity tab can say which
    layer the megabytes are in without a second serialisation of its own.
    """
    sizes: dict[str, int] = {}
    for layer in project.exportable_layers:
        size = _raster_layer_bytes(layer)
        if layer.geojson is not None:
            size += len(
                json.dumps(layer.geojson, separators=(",", ":")).encode("utf-8")
            )
        sizes[layer.layer_id] = size
    return sizes


def measure_data_bytes(project: ExportProject) -> int:
    """Serialised size of all layer data, as it will appear in the artifact.

    Rasters are counted alongside GeoJSON, because to every consumer of this
    number -- the eligibility check, the compression decision, the size warning
    -- a megabyte of pixels weighs exactly as much as a megabyte of
    coordinates. While this counted GeoJSON only, a 400 MB orthophoto was
    invisible: `standalone_ineligible_reason` cheerfully approved a project
    that could not be written into an openable file.
    """
    return sum(data_bytes_by_layer(project).values())


def standalone_raster_reason(project: ExportProject) -> str | None:
    """Why a raster stops this project shipping as one HTML file, or `None`.

    One function, two callers, on purpose: `standalone_ineligible_reason` uses
    it to steer the dialog away from the tier before the user commits, and
    `scan` uses it to refuse the export if they got there anyway. A second copy
    of the rule is how a dialog and an exporter start disagreeing.

    See `STANDALONE_RASTER_MAX_BYTES`: rasters travel inside the page up to a
    size, and only past it does this return a reason. The message names the
    rasters because a project can hold twenty layers and "there is a raster
    somewhere" is not something a user can act on.
    """
    if raster_bytes(project) <= STANDALONE_RASTER_MAX_BYTES:
        return None

    names = [
        layer.name for layer in project.exportable_layers if layer.raster is not None
    ]
    if not names:
        return None

    listed = " and ".join(
        [", ".join(f"'{name}'" for name in names[:-1]), f"'{names[-1]}'"]
        if len(names) > 1
        else [f"'{names[0]}'"]
    )
    return (
        f"The raster imagery in {listed} is about "
        f"{format_megabytes(raster_bytes(project))}, too large to pack into a "
        "single HTML file (the limit is "
        f"{format_megabytes(STANDALONE_RASTER_MAX_BYTES)}). Choose Folder or "
        "Share ZIP as the export format instead."
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
) -> ScanResult:
    """Classify every dependency and decide whether the export can proceed."""
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
                # Those tiles were chosen, so unlike the basemap they cannot be
                # swapped - see `manifest_builder.OSM_FILE_NOTE` for why.
                if (
                    "tile.openstreetmap.org" in dependency.identifier
                    and mode in FILE_OPENED_MODES
                ):
                    report.approximated(
                        f"Data source of '{layer.name}'",
                        "OpenStreetMap's tile servers refuse maps opened "
                        "straight from a file, so recipients who double-click "
                        "this map may see 'Access blocked' tiles in this layer. "
                        "Export as a Folder for a website, or use another tile "
                        "source, to avoid it.",
                        layer.layer_id,
                    )

            if dependency.disposition is AssetDisposition.BLOCKING:
                reason = dependency.note or f"'{dependency.identifier}' is missing."
                blocking.append(reason)
                report.blocked(f"Data source of '{layer.name}'", reason, layer.layer_id)

    if not project.exportable_layers:
        blocking.append(
            "There are no layers to export. Add a vector or raster layer with data."
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
