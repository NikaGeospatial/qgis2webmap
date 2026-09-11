"""Turning a raster into something a browser can actually stream.

**Why this module exists at all.** A plain GeoTIFF has no layout a web client
can exploit: the pixels may be stored in strips, the overviews may be in a
sidecar or absent entirely, and the directory that says where everything lives
may sit at the end of the file. A browser asked to draw one has exactly one
option -- download all of it. A 2 GB orthophoto is then a 2 GB download before
the first pixel appears. A Cloud-Optimized GeoTIFF is the same format arranged
so that it can be read piecemeal: internally tiled, overviews built in, and the
IFDs laid out up front so a client can fetch a handful of HTTP ranges and draw
only the tiles it needs. Conversion is therefore not an optimisation pass we
could skip on a slow machine; without it, hosting a raster is not viable.

**GDAL does the work, and GDAL is already here.** It ships inside QGIS, so
using it adds no dependency -- which matters, because `CONTRIBUTING.md` forbids
bundling binaries and plugins.qgis.org rejects plugins that do. Everything
below is `gdal.Translate` with the `COG` driver plus honest reporting around it.

**Import is soft.** `from osgeo import gdal` at module scope would make this
file unimportable wherever GDAL is not installed -- which includes the pure
unit-test tier, where the interesting logic (what makes a verdict, what
creation options we build) can be tested perfectly well without a raster in
sight. So the import is deferred and its failure is a first-class, explainable
state, the same shape `runtime_manager.py` uses for the runtime it cannot find:
a named exception with a message a user can act on, never an ImportError
traceback out of a background thread.

The split that makes that work runs through the whole module: GDAL is read once
into a `RasterProbe` of plain data, and every judgement is a pure function of
that probe. The pure half is unit-tested; the GDAL half is thin enough to read.

No PyQGIS, no Qt, no third-party packages. This module has to stay importable
by the Processing algorithm running headless and by tests running with neither.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# The renderer draws in Web Mercator. A COG in any other CRS would have to be
# warped in the browser, which the runtime does not do, so anything else is
# reprojected on the way out rather than shipped and hoped for.
WEB_MERCATOR_EPSG = 3857

# DEFLATE is the default for a reason that is not "it compresses best". It is
# lossless, so an elevation or classified raster survives the round trip
# unaltered; it is supported by every COG reader in existence, including the
# oldest; and it needs no extra GDAL build options. ZSTD is faster and smaller
# but only in a GDAL built with it, and JPEG/WEBP are lossy -- fine for an
# orthophoto, silently destructive for data. The caller may ask for those; the
# default may not quietly change what the user's numbers mean.
DEFAULT_COMPRESSION = "DEFLATE"

# 512 rather than GDAL's 256 default: at 256 a full-screen view is four times
# as many HTTP range requests, and per-request latency dominates over the wire
# far more than the slightly larger tile bodies do.
DEFAULT_BLOCKSIZE = 512

# AVERAGE, not NEAREST. Zoomed-out imagery decimated by NEAREST shimmers and
# drops thin features entirely; averaging is what makes an overview look like
# the data. It is the wrong choice for categorical rasters (averaging land-use
# codes invents codes that mean nothing), which is why it is an argument.
DEFAULT_OVERVIEW_RESAMPLING = "AVERAGE"

# Compressions a COG may carry that every mainstream client can decode. A file
# using something outside this set is not necessarily broken, but we will not
# call it web-ready without saying so.
WEB_SAFE_COMPRESSIONS = frozenset(
    {"DEFLATE", "LZW", "ZSTD", "JPEG", "WEBP", "LERC", "LERC_DEFLATE", "LERC_ZSTD"}
)

# Lossy codecs. Listed so `to_cog` can say out loud that it is about to throw
# pixel values away, instead of the user discovering it in their elevation
# profile six months later.
LOSSY_COMPRESSIONS = frozenset({"JPEG", "WEBP"})

# Called with (units_done, units_total) as work proceeds -- the same shape as
# `runtime_manager.ProgressCallback`, so the existing progress widgets take
# this without adaptation. GDAL reports a 0.0-1.0 fraction, which we scale to
# whole units out of `PROGRESS_TOTAL`; the alias is *not* imported from
# `runtime_manager` because these two modules are otherwise unrelated and a
# shared import would tie the packaging layer's raster path to its download
# path for nothing but a `Callable` spelling.
ProgressCallback = Callable[[int, "int | None"], None]

PROGRESS_TOTAL = 100

# Consulted between GDAL's progress ticks; returning True aborts the
# conversion. Separate from `ProgressCallback` so the callback keeps the
# void-returning shape the rest of the plugin already uses -- folding
# cancellation into its return value would have made every existing progress
# function the wrong type.
CancelCheck = Callable[[], bool]

GDAL_MISSING_MESSAGE = (
    "GDAL's Python bindings are not available, so rasters cannot be converted "
    "to Cloud-Optimized GeoTIFF. GDAL ships with QGIS: this normally means the "
    "plugin is running under a plain Python interpreter rather than the one "
    "inside QGIS."
)


class CogError(RuntimeError):
    """A raster could not be inspected or converted.

    One base class so a caller can catch the whole family, with subclasses that
    separate the cases needing genuinely different handling -- "install
    something" is not the same problem as "this file is broken", and neither is
    "this file has no idea where on Earth it is".
    """


class GdalUnavailableError(CogError):
    """GDAL's Python bindings could not be imported."""


class RasterOpenError(CogError):
    """GDAL could not open the source, or it holds no raster bands."""


class MissingCrsError(CogError):
    """The source declares no CRS, so it cannot be placed on a web map.

    Distinct because it is the one failure the user can usually fix themselves:
    assign the CRS in QGIS (or supply a world file) and re-export. Guessing on
    their behalf is not on the table -- a raster put in the wrong place on a map
    is worse than one that was honestly refused.
    """


class ConversionError(CogError):
    """`gdal.Translate` failed or was cancelled."""


# --------------------------------------------------------------------------
# Facts read from GDAL, and the judgements made from them
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RasterProbe:
    """Everything we read out of a raster, as plain data.

    The seam that keeps the rest of this module testable without GDAL: the
    reading happens once, in `probe_raster`, and every rule below is a pure
    function of this. It also documents exactly which properties the verdict is
    entitled to reason about, which stopped an earlier draft from reaching back
    into the dataset halfway through forming its answer.
    """

    path: str
    driver: str | None = None
    width: int = 0
    height: int = 0
    band_count: int = 0
    # From the GTiff driver's IMAGE_STRUCTURE domain. "COG" here is GDAL's own
    # verdict, computed from the file's real layout rather than read from a tag,
    # so a COG written by rio-cogeo or any other tool is recognised too.
    layout: str | None = None
    compression: str | None = None
    block_size: tuple[int, int] | None = None
    overview_count: int = 0
    epsg: int | None = None
    has_crs: bool = False
    file_bytes: int = 0
    # Set when GDAL refused to open the file at all. Carried rather than raised
    # so `is_cog` can answer "no, and here is why" for a corrupt file, which is
    # a verdict, not an exception.
    open_error: str | None = None


@dataclass(frozen=True)
class CogVerdict:
    """Whether a file is already a COG, and what is wrong with it if not.

    `reasons` are disqualifying; `warnings` are things worth saying that do not
    make the answer no. Keeping them apart is what lets the UI show "already a
    COG, but uncompressed -- converting will shrink it" without either lying
    about the file or hiding the opportunity.
    """

    path: str
    is_cog: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    probe: RasterProbe | None = None

    @property
    def epsg(self) -> int | None:
        return self.probe.epsg if self.probe is not None else None

    @property
    def needs_reprojection(self) -> bool:
        """True when this file is not in the CRS the renderer draws in.

        `None` (an unknown or non-EPSG CRS) counts as needing it: an
        unidentifiable CRS is exactly the case where assuming Web Mercator puts
        the imagery in the Gulf of Guinea.
        """
        return self.epsg != WEB_MERCATOR_EPSG

    @property
    def is_web_ready(self) -> bool:
        """A COG *and* already in Web Mercator, so it can be hosted as-is."""
        return self.is_cog and not self.needs_reprojection

    def describe(self) -> str:
        """One line a human can act on."""
        if self.is_web_ready:
            return f"{Path(self.path).name} is already a web-ready COG."
        if self.is_cog:
            return (
                f"{Path(self.path).name} is a COG but is in "
                f"{_crs_label(self.epsg)}; it will be reprojected to Web Mercator."
            )
        return f"{Path(self.path).name} is not a COG: " + "; ".join(self.reasons)

    def snapshot(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "isCog": self.is_cog,
            "isWebReady": self.is_web_ready,
            "epsg": self.epsg,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CogResult:
    """What a conversion did -- including deciding not to convert.

    Reports bytes in and out because that number is the whole argument for this
    feature and the user deserves to see it, and reports `skipped` because a
    file that was already a web-ready COG is copied nowhere: `destination` then
    points back at the source, so the caller uploads the original rather than a
    needless multi-gigabyte duplicate. Callers must read `destination` rather
    than assume the path they passed in.
    """

    source: str
    destination: str
    source_bytes: int
    destination_bytes: int
    skipped: bool = False
    reprojected: bool = False
    source_epsg: int | None = None
    destination_epsg: int | None = None
    compression: str | None = None
    overview_count: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def size_ratio(self) -> float | None:
        """Output size as a fraction of input, or None if input size is unknown."""
        if self.source_bytes <= 0:
            return None
        return self.destination_bytes / self.source_bytes

    def describe(self) -> str:
        if self.skipped:
            return (
                f"{Path(self.source).name} was already a web-ready COG; "
                "it was used unchanged."
            )
        moved = (
            f", reprojected from {_crs_label(self.source_epsg)} to Web Mercator"
            if self.reprojected
            else ""
        )
        return (
            f"{Path(self.source).name} converted to COG"
            f"{moved}: {_format_bytes(self.source_bytes)} -> "
            f"{_format_bytes(self.destination_bytes)}, "
            f"{self.overview_count} overview levels."
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "sourceBytes": self.source_bytes,
            "destinationBytes": self.destination_bytes,
            "skipped": self.skipped,
            "reprojected": self.reprojected,
            "sourceEpsg": self.source_epsg,
            "destinationEpsg": self.destination_epsg,
            "compression": self.compression,
            "overviewCount": self.overview_count,
            "warnings": list(self.warnings),
        }


def verdict_from_probe(probe: RasterProbe) -> CogVerdict:
    """Judge a probe. Pure -- this is where the actual COG rules live.

    The primary test is GDAL's own: the GTiff driver reports `LAYOUT=COG` in
    IMAGE_STRUCTURE when it opens a file whose IFDs, tiles and overviews are
    arranged the COG way. That is the same structural analysis
    `validate_cloud_optimized_geotiff.py` performs, done by the driver at open
    time, and preferring it means we agree with GDAL by construction instead of
    maintaining a second opinion that can drift.

    The structural checks below therefore never *grant* COG status -- they only
    explain a refusal. GDAL says no; these say why, in terms the user can fix.
    """
    if probe.open_error is not None:
        return CogVerdict(
            path=probe.path,
            is_cog=False,
            reasons=(probe.open_error,),
            probe=probe,
        )

    if probe.band_count == 0:
        return CogVerdict(
            path=probe.path,
            is_cog=False,
            reasons=("the file holds no raster bands",),
            probe=probe,
        )

    warnings = _probe_warnings(probe)

    if (probe.layout or "").upper() == "COG":
        return CogVerdict(path=probe.path, is_cog=True, warnings=warnings, probe=probe)

    return CogVerdict(
        path=probe.path,
        is_cog=False,
        reasons=_disqualifying_reasons(probe),
        warnings=warnings,
        probe=probe,
    )


def _disqualifying_reasons(probe: RasterProbe) -> tuple[str, ...]:
    """Why GDAL did not call this a COG, in the user's terms.

    Best-effort by design. GDAL's structural verdict already decided the
    outcome; if none of the specific checks below fires -- an internally tiled,
    overviewed GeoTIFF whose IFDs merely sit in the wrong order -- we still owe
    the user a sentence, so the generic fallback is not dead code.
    """
    reasons: list[str] = []

    if (probe.driver or "") != "GTiff":
        reasons.append(f"it is a {probe.driver or 'unrecognised'} file, not a GeoTIFF")
        # No point discussing tiling of a PNG. The format is the whole problem.
        return tuple(reasons)

    if not _is_tiled(probe.block_size):
        reasons.append(
            "it is stored in strips rather than square tiles, so a viewer "
            "cannot fetch part of it"
        )

    if probe.overview_count == 0 and _needs_overviews(probe):
        reasons.append(
            "it has no internal overviews, so a zoomed-out view would read "
            "every pixel at full resolution"
        )

    compression = (probe.compression or "NONE").upper()
    if compression != "NONE" and compression not in WEB_SAFE_COMPRESSIONS:
        reasons.append(
            f"its {compression} compression is not one every web client can decode"
        )

    if not reasons:
        reasons.append(
            "its internal layout is not arranged for range requests (GDAL "
            "does not report it as a COG)"
        )

    return tuple(reasons)


def _probe_warnings(probe: RasterProbe) -> tuple[str, ...]:
    warnings: list[str] = []

    if not probe.has_crs:
        warnings.append(
            "the raster declares no CRS, so it cannot be placed on a map until "
            "one is assigned in QGIS"
        )
    elif probe.epsg is None:
        warnings.append(
            "the raster's CRS has no EPSG code, so it will be reprojected by "
            "its full definition rather than by code"
        )

    if (probe.compression or "NONE").upper() == "NONE":
        warnings.append(
            "the raster is uncompressed; converting will usually make it "
            "substantially smaller"
        )

    return tuple(warnings)


def _is_tiled(block_size: tuple[int, int] | None) -> bool:
    """Whether a GeoTIFF's blocks look like tiles rather than strips.

    A heuristic, and deliberately only used to *explain* a refusal that GDAL has
    already made -- so a wrong guess costs a slightly less precise sentence, not
    a wrong verdict. TIFF requires tile dimensions to be multiples of 16, while
    a stripped image reports one block per N rows of full image width; those two
    facts separate the cases in every file we have seen, and no cheaper test
    does. Tiles need not be square (512x256 is legal), so squareness is not
    required here even though the COG driver always writes square ones.
    """
    if block_size is None:
        return False
    block_width, block_height = block_size
    if block_height <= 1:
        return False
    return block_width % 16 == 0 and block_height % 16 == 0


def _needs_overviews(probe: RasterProbe) -> bool:
    """Whether the absence of overviews is a real problem.

    A raster no larger than one tile is already its own overview -- the COG
    driver itself declines to build levels for one, so demanding them would
    make us disagree with the tool we defer to everywhere else.
    """
    return max(probe.width, probe.height) > DEFAULT_BLOCKSIZE


def build_creation_options(
    compression: str = DEFAULT_COMPRESSION,
    blocksize: int = DEFAULT_BLOCKSIZE,
    overview_resampling: str = DEFAULT_OVERVIEW_RESAMPLING,
    target_epsg: int | None = WEB_MERCATOR_EPSG,
    extra: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """The `KEY=VALUE` list handed to the COG driver. Pure, so it is testable.

    `TARGET_SRS` is what makes reprojection a property of the output rather
    than a separate warp step: the COG driver reprojects, tiles, and builds
    overviews in one pass over the data, where a `gdal.Warp` followed by a
    `gdal.Translate` would write the whole raster to disk twice.

    `OVERVIEWS=AUTO` reuses overviews the source already has and builds them
    when it has none, which on a large source that was already pyramided is the
    difference between seconds and minutes. `extra` is appended last so a caller
    can override any of this; GDAL takes the final occurrence of a key.
    """
    options = [
        f"COMPRESS={compression.upper()}",
        f"BLOCKSIZE={blocksize}",
        f"RESAMPLING={overview_resampling.upper()}",
        "OVERVIEWS=AUTO",
    ]
    if target_epsg is not None:
        options.append(f"TARGET_SRS=EPSG:{target_epsg}")
    options.extend(extra)
    return tuple(options)


# --------------------------------------------------------------------------
# The GDAL half
# --------------------------------------------------------------------------


def load_gdal() -> Any:
    """Return the `gdal` module, or raise `GdalUnavailableError`.

    Deferred rather than imported at module scope so that importing this file
    never fails; see the module docstring. Callers that merely want to know
    whether raster export is possible should use `gdal_available()`.
    """
    try:
        from osgeo import gdal
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise GdalUnavailableError(GDAL_MISSING_MESSAGE) from exc
    return gdal


def gdal_available() -> bool:
    """Whether raster conversion can run here. Never raises.

    Exists so the export dialog can grey out the raster option and say why,
    rather than letting the user configure an export that dies on the first
    layer.
    """
    try:
        load_gdal()
    except GdalUnavailableError:
        return False
    return True


def gdal_version() -> str | None:
    """GDAL's release name, or None. For the fidelity report and bug reports."""
    try:
        gdal = load_gdal()
    except GdalUnavailableError:
        return None
    version: str = gdal.VersionInfo("RELEASE_NAME")
    return version


@contextlib.contextmanager
def _gdal_exceptions(gdal: Any) -> Iterator[None]:
    """Make GDAL raise inside this block, and only inside it.

    `gdal.UseExceptions()` is process-global. Calling it from a plugin flips the
    error behaviour of every other piece of code in the QGIS session -- including
    QGIS's own providers, which were written against the silent-return-None
    convention -- and would be a genuinely nasty thing to do to the host
    application. `ExceptionMgr` (GDAL 3.7+) saves and restores the setting, so
    the blast radius stops at this block. On anything older we do not touch the
    global state at all and fall back to checking return values, which is why
    every call site below still tests for None.
    """
    manager = getattr(gdal, "ExceptionMgr", None)
    if manager is None:  # pragma: no cover - GDAL < 3.7
        yield
        return
    with manager(useExceptions=True):
        yield


def probe_raster(path: str | Path) -> RasterProbe:
    """Read a raster's structural facts. Opens read-only; writes nothing.

    Never raises for a bad file -- a file GDAL cannot open comes back as a probe
    carrying `open_error`, because "this is not a readable raster" is an answer
    `is_cog` needs to report, not an exception it needs to survive. Only a
    missing GDAL raises.
    """
    gdal = load_gdal()
    text = os.fspath(path)

    try:
        file_bytes = os.path.getsize(text)
    except OSError:
        file_bytes = 0

    dataset = None
    try:
        with _gdal_exceptions(gdal):
            dataset = gdal.Open(text, gdal.GA_ReadOnly)
        if dataset is None:
            raise RuntimeError(gdal.GetLastErrorMsg() or "unknown error")
    except Exception as exc:  # GDAL raises RuntimeError; be liberal about it
        return RasterProbe(
            path=text,
            file_bytes=file_bytes,
            open_error=_open_failure_message(text, str(exc)),
        )

    try:
        return _probe_open_dataset(dataset, text, file_bytes)
    finally:
        # Explicit: GDAL releases the file handle when the last reference drops,
        # and on Windows a lingering handle makes the destination un-writable.
        dataset = None


def _probe_open_dataset(dataset: Any, text: str, file_bytes: int) -> RasterProbe:
    structure = dataset.GetMetadata("IMAGE_STRUCTURE") or {}
    band_count = int(dataset.RasterCount)

    block_size: tuple[int, int] | None = None
    overview_count = 0
    if band_count > 0:
        band = dataset.GetRasterBand(1)
        raw_block = band.GetBlockSize()
        block_size = (int(raw_block[0]), int(raw_block[1]))
        overview_count = int(band.GetOverviewCount())

    reference = dataset.GetSpatialRef()
    epsg: int | None = None
    if reference is not None:
        code = reference.GetAuthorityCode(None)
        # A CRS can be perfectly valid and still have no EPSG code (a custom
        # projection, or one only identified deeper in the WKT). int() on that
        # would be a crash where "unknown code" is the correct answer.
        if code is not None and str(code).isdigit():
            epsg = int(code)

    driver = dataset.GetDriver()
    return RasterProbe(
        path=text,
        driver=driver.ShortName if driver is not None else None,
        width=int(dataset.RasterXSize),
        height=int(dataset.RasterYSize),
        band_count=band_count,
        layout=structure.get("LAYOUT"),
        compression=structure.get("COMPRESSION"),
        block_size=block_size,
        overview_count=overview_count,
        epsg=epsg,
        has_crs=reference is not None,
        file_bytes=file_bytes,
    )


def is_cog(path: str | Path) -> CogVerdict:
    """Is this file already a valid Cloud-Optimized GeoTIFF?

    Answers with a verdict rather than a bool: "no" is only useful alongside
    *why*, and the caller needs the CRS anyway to decide whether a file that is
    already a COG still has to be reprojected. See `CogVerdict.is_web_ready`.

    Raises only `GdalUnavailableError`. A missing, corrupt, or non-raster file
    is a verdict with a reason.
    """
    return verdict_from_probe(probe_raster(path))


def to_cog(
    source: str | Path,
    destination: str | Path,
    compression: str = DEFAULT_COMPRESSION,
    blocksize: int = DEFAULT_BLOCKSIZE,
    overview_resampling: str = DEFAULT_OVERVIEW_RESAMPLING,
    target_epsg: int | None = WEB_MERCATOR_EPSG,
    skip_if_ready: bool = True,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    creation_options: tuple[str, ...] = (),
) -> CogResult:
    """Convert a raster to a COG at `destination`, reprojecting if needed.

    Never writes beside the source. The destination is supplied by the caller
    precisely so that nothing lands in the user's data directory: exporting a
    map must not modify the project it was exported from, and a stray
    `ortho.cog.tif` next to `ortho.tif` is a modification the user did not ask
    for and will not think to clean up.

    `skip_if_ready` short-circuits when the source is already a COG in the
    target CRS. Nothing is copied in that case and `CogResult.destination`
    points at the *source*; copying a 2 GB file to prove we looked at it is
    pure cost. Pass False when the caller needs its own copy regardless -- for
    instance to keep an artifact self-contained.

    Progress is reported as (units_done, 100). GDAL's callback fires often on a
    large raster, so a UI should expect to be called many times; `should_cancel`
    is consulted on each tick and aborts the conversion, leaving the partial
    destination for the caller to remove.
    """
    gdal = load_gdal()
    source_text = os.fspath(source)
    destination_text = os.fspath(destination)

    if os.path.abspath(source_text) == os.path.abspath(destination_text):
        raise ConversionError(
            "The COG destination is the source file itself. Converting in "
            "place would destroy the original; give a separate destination."
        )

    probe = probe_raster(source_text)
    if probe.open_error is not None:
        raise RasterOpenError(probe.open_error)
    if probe.band_count == 0:
        raise RasterOpenError(
            f"{source_text} was opened but holds no raster bands, so there is "
            "nothing to convert."
        )

    verdict = verdict_from_probe(probe)

    if target_epsg is not None and not probe.has_crs:
        raise MissingCrsError(
            f"{Path(source_text).name} has no coordinate reference system, so "
            "it cannot be placed on a web map. Assign the correct CRS to the "
            "layer in QGIS (Layer Properties > Source) and export again."
        )

    already_correct_crs = target_epsg is None or probe.epsg == target_epsg
    if skip_if_ready and verdict.is_cog and already_correct_crs:
        return CogResult(
            source=source_text,
            destination=source_text,
            source_bytes=probe.file_bytes,
            destination_bytes=probe.file_bytes,
            skipped=True,
            source_epsg=probe.epsg,
            destination_epsg=probe.epsg,
            compression=probe.compression,
            overview_count=probe.overview_count,
            warnings=verdict.warnings,
        )

    warnings = list(verdict.warnings)
    if compression.upper() in LOSSY_COMPRESSIONS:
        warnings.append(
            f"{compression.upper()} is lossy: pixel values will change. This is "
            "fine for photographic imagery and wrong for elevation or "
            "classified data."
        )

    options = build_creation_options(
        compression=compression,
        blocksize=blocksize,
        overview_resampling=overview_resampling,
        # Already in the right CRS: omit TARGET_SRS rather than ask for a
        # no-op reprojection. GDAL would resample the pixels anyway, which
        # costs time and quality for no change of position.
        target_epsg=None if already_correct_crs else target_epsg,
        extra=creation_options,
    )

    Path(destination_text).parent.mkdir(parents=True, exist_ok=True)

    bridge = _ProgressBridge(on_progress, should_cancel)
    output = None
    try:
        with _gdal_exceptions(gdal):
            output = gdal.Translate(
                destination_text,
                source_text,
                format="COG",
                creationOptions=list(options),
                callback=bridge if bridge.needed else None,
            )
        if output is None:
            raise RuntimeError(gdal.GetLastErrorMsg() or "unknown error")
    except Exception as exc:
        if bridge.cancelled:
            raise ConversionError(
                f"Conversion of {Path(source_text).name} was cancelled."
            ) from None
        raise ConversionError(
            f"GDAL could not convert {Path(source_text).name} to a "
            f"Cloud-Optimized GeoTIFF.\n\n{exc}"
        ) from exc
    finally:
        # Flushes GDAL's write cache and closes the file. Without this the
        # destination can still be short of its final bytes when we stat it
        # below, which produced a "0 byte" report for a perfectly good file.
        output = None

    result_probe = probe_raster(destination_text)
    if result_probe.open_error is not None or (result_probe.layout or "") != "COG":
        raise ConversionError(
            f"GDAL wrote {destination_text} but it did not come back as a "
            "valid COG. The output is unusable; please report this with the "
            f"source raster's details (GDAL {gdal.VersionInfo('RELEASE_NAME')})."
        )

    return CogResult(
        source=source_text,
        destination=destination_text,
        source_bytes=probe.file_bytes,
        destination_bytes=result_probe.file_bytes,
        skipped=False,
        reprojected=not already_correct_crs,
        source_epsg=probe.epsg,
        destination_epsg=result_probe.epsg,
        compression=result_probe.compression,
        overview_count=result_probe.overview_count,
        warnings=tuple(warnings),
    )


class _ProgressBridge:
    """Adapts our callback pair to GDAL's `(fraction, message, user_data)` shape.

    Cancellation is signalled GDAL's way -- by returning 0, which makes
    `Translate` abort -- and *remembered here*, because that is the only way to
    tell the two failures apart afterwards. An earlier version raised a Python
    exception out of the callback instead; SWIG swallows it and re-raises the
    generic `RuntimeError: User terminated`, so a deliberate cancellation was
    reported to the user as "GDAL could not convert your raster". Recording the
    flag on the bridge costs one attribute and keeps "you cancelled this" and
    "GDAL broke" as the different dialogs they should be.
    """

    def __init__(
        self,
        on_progress: ProgressCallback | None,
        should_cancel: CancelCheck | None,
    ) -> None:
        self._on_progress = on_progress
        self._should_cancel = should_cancel
        self.cancelled = False

    @property
    def needed(self) -> bool:
        """False when there is nothing to report, so a headless conversion
        pays nothing for a trip into Python on every tick."""
        return self._on_progress is not None or self._should_cancel is not None

    def __call__(self, fraction: float, message: str, user_data: Any) -> int:
        if self._should_cancel is not None and self._should_cancel():
            self.cancelled = True
            return 0
        if self._on_progress is not None:
            self._on_progress(int(fraction * PROGRESS_TOTAL), PROGRESS_TOTAL)
        return 1


def _open_failure_message(path: str, detail: str) -> str:
    """Turn a GDAL error into something with a next step in it."""
    if not os.path.exists(path):
        return f"{path} does not exist."
    return (
        f"GDAL could not open {Path(path).name} as a raster. The file may be "
        f"truncated, corrupt, or in a format this GDAL build lacks a driver "
        f"for.\n\n{detail}"
    )


def _crs_label(epsg: int | None) -> str:
    if epsg is None:
        return "an unidentified CRS"
    if epsg == WEB_MERCATOR_EPSG:
        return "Web Mercator (EPSG:3857)"
    return f"EPSG:{epsg}"


def _format_bytes(count: int) -> str:
    """Human-sized, matching how the rest of the plugin talks about downloads."""
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.0f} KB"
    if count < 1024 * 1024 * 1024:
        return f"{count / 1024 / 1024:.1f} MB"
    return f"{count / 1024 / 1024 / 1024:.2f} GB"
