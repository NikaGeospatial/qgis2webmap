"""What an export needs, and whether it can be made portable.

Runs **before** anything is written. The point is to answer "will this artifact
actually work for the person who receives it?" while there is still time to say
no, rather than discovering the answer when they open a blank page.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..core.export_ir import (
    AssetDependency,
    AssetDisposition,
    ExportLayer,
    ExportProject,
    OutputMode,
)
from ..core.fidelity_report import FidelityReportBuilder
from ..core.manifest_builder import FILE_OPENED_MODES, settings_network_hosts
from ..core.popup_media import picture_sources

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
    if raster is None or raster.remote:
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
    """Each exported layer's data, by layer id: its share of
    `measure_data_bytes`, less the popup pictures, which layers can share and
    are counted once for the map.

    The same measurement split per layer, so the Fidelity tab can say which
    layer the megabytes are in without a second serialisation of its own.
    """
    sizes: dict[str, int] = {}
    archives: set[str] = set()
    for layer in project.exportable_layers:
        size = _raster_layer_bytes(layer)
        if layer.geojson is not None:
            size += ascii_json_bytes(layer.geojson_text)
        tiles = layer.vector_tiles
        if tiles is not None and tiles.is_archive and tiles.path not in archives:
            # A tile archive is carried once, under the first layer drawn from it.
            archives.add(tiles.path)
            size += tiles.size_bytes
        sizes[layer.layer_id] = size
    return sizes


def ascii_json_bytes(text: str) -> int:
    """The bytes `json.dumps(..., ensure_ascii=True)` would give, from the
    `ensure_ascii=False` text of the same value.

    The size has always been measured on the ASCII-escaped form, and the
    compression choice and the size messages hang on that number, so it must
    not move; the layer's text is serialised the other way, for the page. The
    two differ only in how a string character is written, one character at a
    time: below U+007F alike (quotes, backslashes and control characters are
    escaped the same way in both), and from U+007F up `\\uXXXX` - six bytes - or
    a surrogate pair of them, twelve, for one beyond U+FFFF.
    """
    if text.isascii() and "\x7f" not in text:
        return len(text)
    beyond_ascii = len(text) - len(text.encode("ascii", "ignore"))
    units = len(text.encode("utf-16-le", "surrogatepass")) // 2
    return len(text) + 5 * (beyond_ascii + text.count("\x7f")) + 6 * (units - len(text))


def measure_data_bytes(project: ExportProject) -> int:
    """Serialised size of all layer data, as it will appear in the artifact.

    Rasters are counted alongside GeoJSON, because to every consumer of this
    number -- the eligibility check, the compression decision, the size warning
    -- a megabyte of pixels weighs exactly as much as a megabyte of
    coordinates. While this counted GeoJSON only, a 400 MB orthophoto was
    invisible: `standalone_ineligible_reason` cheerfully approved a project
    that could not be written into an openable file. Popup pictures count for
    the same reason, each once, as the page carries them
    (`packaging.popup_pictures`).
    """
    pictures = picture_sources(project.exportable_layers)
    return sum(data_bytes_by_layer(project).values()) + sum(
        len(picture.data) for picture in pictures.values()
    )


def standalone_raster_reason(project: ExportProject) -> str | None:
    """Why a raster stops this project shipping as one HTML file, or `None`.

    One function, two callers, on purpose: `standalone_ineligible_reason` uses
    it to steer the dialog away from the tier before the user commits, and
    `scan` uses it to refuse the export if they got there anyway. A second copy
    of the rule is how a dialog and an exporter start disagreeing.

    See `STANDALONE_RASTER_MAX_BYTES`: rasters travel inside the page up to a
    size, and only past it does this return a reason. The size is the source
    file's, an estimate: the converted raster is measured against the same
    limit after the conversion, and that is the check that refuses. The message
    names the rasters because a project can hold twenty layers and "there is a
    raster somewhere" is not something a user can act on.
    """
    if raster_bytes(project) <= STANDALONE_RASTER_MAX_BYTES:
        return None

    names = [
        layer.name
        for layer in project.exportable_layers
        if layer.raster is not None and not layer.raster.remote
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
        f"{format_megabytes(raster_bytes(project))} before conversion, which "
        "may be too large to pack into a single HTML file (the limit is "
        f"{format_megabytes(STANDALONE_RASTER_MAX_BYTES)}, checked again on "
        "the converted raster). Folder or Share ZIP as the export format "
        "carry it without that limit."
    )


def standalone_archive_reason(project: ExportProject) -> str | None:
    """Why the tile archives stop this project shipping as one HTML file,
    or `None`.

    A local PMTiles archive travels inside the page as a raster does, and the
    two share the page's limit (`STANDALONE_RASTER_MAX_BYTES`); an archive is
    carried as it is, so its size is the one that counts.
    """
    archives = {
        layer.vector_tiles.path: layer.vector_tiles.size_bytes
        for layer in project.exportable_layers
        if layer.vector_tiles is not None and layer.vector_tiles.is_archive
    }
    total = sum(archives.values()) + raster_bytes(project)
    if not archives or total <= STANDALONE_RASTER_MAX_BYTES:
        return None
    return (
        f"The tile archives and raster imagery come to about "
        f"{format_megabytes(total)}, which is too large to pack into a single "
        f"HTML file (the limit is {format_megabytes(STANDALONE_RASTER_MAX_BYTES)}). "
        "Folder or Share ZIP as the export format carry them without that limit."
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
    archives = standalone_archive_reason(project)
    if archives is not None:
        return archives

    data_bytes = measure_data_bytes(project)
    if data_bytes > SINGLE_FILE_WARN_BYTES:
        return (
            f"The layer data is {format_megabytes(data_bytes)}. A single HTML "
            f"file over {SINGLE_FILE_WARN_BYTES // 1024 // 1024} MB is slow to "
            "open and awkward to move around, so Share ZIP is the practical "
            "choice."
        )
    return None


def report_remote_dependency(
    layer: ExportLayer,
    dependency: AssetDependency,
    report: FidelityReportBuilder,
    mode: OutputMode = OutputMode.STANDALONE_HTML,
) -> None:
    """The Fidelity rows for a layer that stays a live reference.

    Shared with the cartograph, whose live frames never pass through `scan`
    but fetch their tile layers just the same.
    """
    report.approximated(
        f"Data source of '{layer.name}'",
        f"'{dependency.identifier}' stays a live reference, so the "
        "map needs an internet connection to draw this layer.",
        layer.layer_id,
    )
    # The same refusal as the basemap's - see
    # `manifest_builder.OSM_FILE_NOTE` for why.
    if "tile.openstreetmap.org" in dependency.identifier and mode in FILE_OPENED_MODES:
        report.approximated(
            f"Data source of '{layer.name}'",
            "OpenStreetMap's tile servers refuse maps opened "
            "straight from a file, so recipients who double-click "
            "this map may see 'Access blocked' tiles in this layer. "
            "Export as a Folder for a website, or use another tile "
            "source, to avoid it.",
            layer.layer_id,
        )


def report_service_legend(layer: ExportLayer, report: FidelityReportBuilder) -> None:
    """The Fidelity row for the legend pictures a WMS or WMTS layer's service
    draws, which the web map's legend loads live, as QGIS's does."""
    from urllib.parse import urlsplit

    tiles = layer.tiles
    if tiles is None or not tiles.legend:
        return
    images = tiles.legend
    hosts = " and ".join(dict.fromkeys(urlsplit(image.url).netloc for image in images))
    several = len(images) > 1
    detail = (
        f"The legend shows the {'pictures' if several else 'picture'} {hosts} "
        "draws for this layer, as QGIS's legend does. The map loads "
        f"{'them' if several else 'it'} from the service each time it is "
        "opened, so it needs an internet connection; without one, the legend "
        "lists the layer by its name alone."
    )
    if several:
        detail += (
            " QGIS's legend shows one sublayer's picture only; the map's "
            f"shows one for each of the {len(images)} sublayers, under its name."
        )
    if tiles.contextual_legend:
        detail += (
            " QGIS's legend for this layer shows only what is in view (its "
            "contextual WMS legend); the map's is the layer's whole legend, "
            "which does not change as the map moves."
        )
    report.approximated(f"Legend picture of '{layer.name}'", detail, layer.layer_id)


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
                report_remote_dependency(layer, dependency, report, mode)

            if dependency.disposition is AssetDisposition.BLOCKING:
                reason = dependency.note or f"'{dependency.identifier}' is missing."
                blocking.append(reason)
                report.blocked(f"Data source of '{layer.name}'", reason, layer.layer_id)

        # A popup's web pictures are fetched each time it opens; the reader
        # has already said so in the Fidelity tab.
        remote.extend(layer.popup_remote_pictures)

        # Live features are read from their service as the map is used; the
        # reader has already said so (`core.live_features`).
        if layer.live is not None:
            remote.append(layer.live.host)

        # A map service's legend pictures are fetched from it on every open,
        # like its tiles - but only when there is a legend to show them in.
        if project.settings.show_legend and layer.tiles is not None:
            remote.extend(image.url for image in layer.tiles.legend)
            report_service_legend(layer, report)

        # A click on a WMS layer asks its service what is there; the reader
        # has already said so (`layer_reader._wms_feature_info`).
        if layer.tiles is not None and layer.tiles.feature_info is not None:
            remote.append(layer.tiles.feature_info)

    # The basemap and terrain are not layers, so no layer dependency names them,
    # yet the map fetches their tiles every time it is opened. The Fidelity
    # report already explains each (`project_reader`); listing the hosts here is
    # what stops the result calling a map that needs them "offline".
    remote.extend(settings_network_hosts(project.settings))

    if not project.exportable_layers:
        blocking.append(
            "There are no layers to export. Add a vector or raster layer with data."
        )

    if mode is OutputMode.STANDALONE_HTML:
        # Advisory, not blocking: the size here is the SOURCE file's, and a
        # source that compresses well converts to a COG that fits comfortably,
        # so refusing on it would turn away an export that works. The limit
        # that decides is `raster_staging`'s, on the converted file.
        raster_reason = standalone_raster_reason(project)
        if raster_reason is not None:
            for layer in project.exportable_layers:
                if layer.raster is not None and not layer.raster.remote:
                    report.approximated(
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
        remote_dependencies=tuple(dict.fromkeys(remote)),
        blocking_reasons=tuple(blocking),
        credentials_detected=tuple(dict.fromkeys(credentials)),
    )
