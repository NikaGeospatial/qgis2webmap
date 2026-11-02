"""Where the OnlyMap runtime bytes come from.

**The runtime is fetched, not vendored, and that is a licensing decision.**

QGIS requires that "all code included in any plugin should be made clearly and
easily available in source form", and refuses plugins that ship binaries. The
OnlyMap runtime is a 7.9 MB minified build of a closed-source library: nobody
can review it, so it must not travel inside a plugin that plugins.qgis.org
redistributes. Fetching keeps the plugin wholly GPL with public source, and lets
the user obtain the runtime from NIKA's own channel under NIKA's own licence.

Two implementations:

* `LocalRuntime` reads from a directory on disk - `ONLYMAP_RUNTIME_DIR`, a
  contributor's `node_modules`, or a manually installed runtime pack. This is
  the offline and development path.
* `FetchingRuntime` downloads the npm tarball once, verifies it against
  `runtime/runtime-lock.json`, and caches it per machine. Every later export is
  offline.

`default_provider()` prefers a local copy and falls back to fetching, so a
machine that already has the runtime never reaches for the network.

Pure Python: no PyQGIS, no Qt, no third-party packages. The licence prompt and
the progress UI live in `ui/`, because this module has to stay importable by
tests and by the Processing algorithm running headless.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .asset_embedder import adopt_stored_gzip, gzip_bytes, gzip_probe

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PACKAGE_ROOT / "runtime"
LOCK_FILE = RUNTIME_DIR / "runtime-lock.json"

# Filenames inside the OnlyMap package.
RUNTIME_JS = "onlymap.standalone.js"
RUNTIME_CSS = "onlymapjs.css"
RUNTIME_LICENCE = "LICENSE.md"
# The cartograph runtime, shipped in the same `dist/` since 0.8.0. Optional on
# a loaded bundle (see `RuntimeBundle`): the web-map export never needs these,
# so a runtime fetched or vendored before the cartograph feature existed must
# keep working for it rather than failing to load at all.
RUNTIME_CARTOGRAPH_JS = "cartograph.standalone.js"
RUNTIME_CARTOGRAPH_CSS = "cartograph.css"
# The runtime's attribute contract. Optional: the map works without it, but
# the test suite's attribute-contract checks and AI-assisted editing both read
# it, so it is cached beside the runtime when the package ships it.
RUNTIME_SCHEMA = "onlymapjs.html-data.json"

# **A fetched cache keeps the four pinned files gzipped**, as `<name>.gz`: 9.5 MB
# on disk becomes 2.6 MB. Every reader goes through `stored_path` and
# `read_runtime_file`, which take either form, so a cache written before this,
# `ONLYMAP_RUNTIME_DIR` pointing at a package's plain `dist/`, and a fetched
# cache all load the same way; integrity is always checked on the uncompressed
# bytes, against the same `runtime-lock.json` hashes. The licence and the schema
# stay plain: people and tools read them directly.
GZIP_SUFFIX = ".gz"
STORED_GZIPPED = (
    RUNTIME_JS,
    RUNTIME_CSS,
    RUNTIME_CARTOGRAPH_JS,
    RUNTIME_CARTOGRAPH_CSS,
)
# Beside the gzipped files: the `gzip_probe` of each script a single-file export
# embeds gzipped (the stylesheets go in plain), so the export may embed the
# stored stream instead of compressing the runtime again. Never an integrity
# record - a missing or wrong one only means compressing as before.
COMPRESSION_RECORD = "compression.json"
EMBEDDED_GZIPPED = (RUNTIME_JS, RUNTIME_CARTOGRAPH_JS)

# npm is the canonical channel: the same one the library's own users install
# from, so the plugin never becomes a second, staler distribution path. The
# registry serves it anonymously - no account, no token.
NPM_PACKAGE = "@nika-js/onlymap"
NPM_REGISTRY = "https://registry.npmjs.org"

# Paths inside the npm tarball. npm wraps everything in `package/`.
TARBALL_PREFIX = "package"

# A decompression-bomb guard. The real tarball is ~5.7 MB and expands to ~9.5 MB;
# anything an order of magnitude past that is not the runtime.
MAX_MEMBER_BYTES = 64 * 1024 * 1024

# **The one place this number lives.** It is a promise about someone's bandwidth,
# shown in the licence dialog where they decide whether to accept - and it was
# wrong for a whole release because the 0.3.3 -> 0.5.11 bump left four
# hand-written copies saying "about 3 MB" for a download that had become 4.5 MB.
# It drifted again on the 0.6.1 -> 0.6.20 bump (4.5 -> 4.8 MB) and the 0.6.20 ->
# 0.12.0 bump (4.8 -> 5.4 MB) and the 0.12.1 -> 0.13.0 bump (5.4 -> 5.6 MB)
# and the 0.13.1 -> 0.13.2 bump (5.6 -> 5.7 MB), which is why the figure is
# measured from the tarball at pin time rather than remembered.
#
# It describes the *compressed tarball*, which is what actually crosses the
# network, not the ~9.5 MB the files occupy once unpacked. `tests/unit` asserts the
# user guide quotes this same string, so a bump cannot update the code and leave
# the docs behind. Update it in `scripts/lock_runtime.py`'s output when re-pinning.
RUNTIME_DOWNLOAD_SIZE = "about 5.7 MB"

DOWNLOAD_TIMEOUT_SECONDS = 120

# Called with (bytes_so_far, total_bytes_or_None) during a download, so the UI
# can show a bar. Pure data - the callback is the only thing this module knows
# about the outside world.
ProgressCallback = Callable[[int, "int | None"], None]

# Fetches a URL and returns its bytes. The seam exists because `urllib` knows
# nothing about QGIS's proxy configuration, and the users most likely to need
# this plugin - government and corporate GIS - are the ones most likely to sit
# behind a proxy they configured in QGIS and nowhere else. `ui/runtime_setup.py`
# supplies a `QgsBlockingNetworkRequest` implementation; this module keeps a
# stdlib one so it stays importable and testable without PyQGIS.
Downloader = Callable[[str, "ProgressCallback | None"], bytes]


class RuntimeUnavailableError(RuntimeError):
    """The runtime could not be obtained.

    Raised rather than returning a placeholder: an artifact missing its runtime
    is a blank page for the recipient, and that is precisely the silent failure
    this project refuses to ship.
    """


class RuntimeNotAcceptedError(RuntimeUnavailableError):
    """The runtime is not on this machine and its licence has not been accepted.

    Distinct from a download failure because the caller must do something quite
    different about it: show the licence and ask, rather than retry. The
    OnlyMap licence says access to the package "does not by itself grant any
    rights", so downloading it silently on a user's behalf would be helping
    them acquire something they never agreed to.
    """


class RuntimeDownloadError(RuntimeUnavailableError):
    """The download failed, or produced bytes that are not the pinned build."""


class CorruptRuntimeFileError(RuntimeUnavailableError):
    """A gzipped runtime file is truncated, fails its CRC, or is oversized."""


@dataclass(frozen=True)
class RuntimeBundle:
    """Everything an artifact needs from the runtime."""

    javascript: bytes
    css: bytes
    version: str
    sha256: str
    # Set by whichever provider loaded real bytes and could compare them against
    # `runtime-lock.json`. A test double leaves it empty rather than having every
    # writer test assert around a mismatch it deliberately caused.
    lock_warnings: tuple[str, ...] = ()
    # The cartograph dist files, for the cartograph writer path. `None` rather
    # than `b""` when absent - a runtime vendored or cached before cartograph
    # existed has no opinion on these files at all, which is different from
    # shipping them empty. The web-map export never reads these two fields.
    cartograph_javascript: bytes | None = None
    cartograph_css: bytes | None = None

    @property
    def total_bytes(self) -> int:
        return len(self.javascript) + len(self.css)


class RuntimeProvider(Protocol):
    def load(self) -> RuntimeBundle: ...

    def preflight(self) -> None:
        """Raise if `load()` would refuse, cheaply and without side effects.

        Separate from `load()` so a caller can check a precondition *before*
        doing expensive work it would have to throw away - see
        `FetchingRuntime.preflight`.
        """


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stored_path(directory: Path, name: str) -> Path | None:
    """Where one runtime file is on disk: plain, or gzipped beside its name.

    Plain wins when both are present: it is the form a person put there.
    """
    for candidate in (directory / name, directory / (name + GZIP_SUFFIX)):
        if candidate.is_file():
            return candidate
    return None


def _gunzip_bounded(stored: bytes, limit: int | None = None) -> bytes | None:
    """One complete gzip member's contents, or `None` if it is not exactly that.

    Bounded, so a crafted file cannot expand past `limit`; `wbits=31` makes zlib
    check the trailer's CRC-32 and length, so a flipped bit is refused rather
    than loaded. A stream that ends early, or carries anything after its one
    member, is refused too: neither is something this module wrote.
    """
    limit = MAX_MEMBER_BYTES if limit is None else limit
    decoder = zlib.decompressobj(wbits=31)
    try:
        data = decoder.decompress(stored, limit + 1)
    except zlib.error:
        return None
    if len(data) > limit or not decoder.eof or decoder.unused_data:
        return None
    return data


# SHA-256 of each gzipped file's contents, keyed by what `os.stat` says about the
# file it was read from. An export asks `is_cached` up to three times before it
# loads; decompressing 9 MB for each would add 0.17 s to every export, so a file
# whose size, inode and times have not moved since it was last decompressed is
# not decompressed again just to be hashed. Any rewrite moves its ctime.
_GZIP_DIGESTS: dict[tuple[str, int, int, int, int, int], str] = {}


def _stat_key(path: Path) -> tuple[str, int, int, int, int, int]:
    info = path.stat()
    return (
        str(path),
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_ino,
        info.st_dev,
    )


def _read_stored(path: Path) -> tuple[bytes, bytes | None]:
    """A runtime file's bytes, and the gzip they came from when it was stored so."""
    if path.name.endswith(GZIP_SUFFIX):
        key = _stat_key(path)
        if key[1] > MAX_MEMBER_BYTES:
            raise CorruptRuntimeFileError(f"{path} is larger than any runtime file.")
        stored = path.read_bytes()
        data = _gunzip_bounded(stored)
        if data is None:
            raise CorruptRuntimeFileError(
                f"{path} is damaged (truncated, corrupt or oversized)."
            )
        _GZIP_DIGESTS[key] = sha256_of(data)
        return data, stored
    return path.read_bytes(), None


def _stored_digest(path: Path) -> str:
    """SHA-256 of a runtime file's uncompressed contents."""
    if path.name.endswith(GZIP_SUFFIX):
        known = _GZIP_DIGESTS.get(_stat_key(path))
        if known is not None:
            return known
    return sha256_of(_read_stored(path)[0])


def read_runtime_file(directory: Path, name: str) -> bytes | None:
    """One runtime file's uncompressed bytes, plain or gzipped; `None` if absent.

    For callers outside this module - tests, scripts - that need one file
    rather than a whole `RuntimeBundle`. Raises `CorruptRuntimeFileError` for a
    damaged gzipped file rather than returning bytes it could not verify.
    """
    path = stored_path(directory, name)
    return None if path is None else _read_stored(path)[0]


def discover_runtime_dir() -> Path | None:
    """Find a directory containing the runtime's `dist` files.

    Checked in order of authority: an explicit override, the plugin's own
    vendored copy, then a node_modules tree relative to the current working
    directory. The env var exists so a contributor can point at any build
    without editing code.

    **No absolute developer paths.** This ships to macOS and Windows users whose
    home directories look nothing like a NIKA checkout, so a hardcoded
    `~/Nika/...` candidate is dead weight everywhere except one laptop -- and
    worse, it hides a missing vendored runtime during development, so the real
    packaging gap only surfaces on a user's machine. The relative candidates
    below resolve for a contributor running from a checkout and resolve to
    nothing for an end user, which is the honest outcome.
    """
    override = os.environ.get("ONLYMAP_RUNTIME_DIR")
    if override:
        path = Path(override)
        return path if stored_path(path, RUNTIME_JS) is not None else None

    candidates = [
        RUNTIME_DIR,
        Path("node_modules/@nika-js/onlymap/dist"),
        PACKAGE_ROOT.parent / "node_modules/@nika-js/onlymap/dist",
    ]
    return next((c for c in candidates if stored_path(c, RUNTIME_JS) is not None), None)


def default_cache_dir() -> Path:
    """Where a fetched runtime is kept, per machine.

    Stdlib only, so this module stays importable without PyQGIS. The plugin
    passes the QGIS profile directory explicitly when it has one, which keeps
    the runtime with the rest of that profile's state; this is the fallback for
    Processing runs, tests, and anything headless.

    `ONLYMAP_RUNTIME_CACHE` overrides it, which is what a locked-down machine
    with a manually installed runtime pack uses.
    """
    override = os.environ.get("ONLYMAP_RUNTIME_CACHE")
    if override:
        return Path(override)

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")

    return base / "qgis2webmap" / "onlymap-runtime"


def cached_runtime_dir(version: str, cache_dir: Path | None = None) -> Path:
    """The directory one pinned version's files live in.

    Keyed by version so a runtime bump downloads alongside the old one rather
    than overwriting it - a half-written replacement would otherwise leave the
    machine with no working runtime at all. The old one goes only once the new
    one is in place and verified (`prune_superseded_runtimes`).
    """
    return (cache_dir or default_cache_dir()) / version


def licence_marker(version: str, cache_dir: Path | None = None) -> Path:
    """Where acceptance of one version's licence is recorded.

    Beside the version directory rather than inside it, because the two have
    different lifecycles: installing replaces the directory wholesale, and a
    marker kept within it would be destroyed by the very fetch it authorised -
    so a user who later cleared the runtime would be asked to accept again with
    no record that they already had.
    """
    return (cache_dir or default_cache_dir()) / f".licence-accepted-{version}"


def licence_accepted(version: str, cache_dir: Path | None = None) -> bool:
    """Whether the user has accepted the runtime licence, for any version.

    Asked once, at the first download: a runtime upgrade that comes with a
    plugin update downloads without asking again.
    """
    if licence_marker(version, cache_dir).is_file():
        return True
    try:
        return any(
            marker.is_file()
            for marker in (cache_dir or default_cache_dir()).glob(".licence-accepted-*")
        )
    except OSError:
        return False


def record_licence_acceptance(version: str, cache_dir: Path | None = None) -> None:
    """Record that the user accepted the licence, naming the version they saw."""
    marker = licence_marker(version, cache_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"package": NPM_PACKAGE, "version": version}), encoding="utf-8"
    )


class LocalRuntime:
    """Reads the runtime from a directory on disk."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    def preflight(self) -> None:
        """Nothing to check: a local runtime carries no licence gate.

        Present so every provider answers `preflight()` and callers need no
        `hasattr` dance - a local copy is either there or `load()` says so.
        """

    def load(self) -> RuntimeBundle:
        directory = self._directory or discover_runtime_dir()
        if directory is None:
            raise RuntimeUnavailableError(
                "The OnlyMap runtime was not found. Set ONLYMAP_RUNTIME_DIR to a "
                "directory containing "
                f"{RUNTIME_JS}, or install the runtime with the plugin."
            )

        js_path = stored_path(directory, RUNTIME_JS)
        css_path = stored_path(directory, RUNTIME_CSS)

        if js_path is None:
            raise RuntimeUnavailableError(f"{directory / RUNTIME_JS} is missing.")
        if css_path is None:
            raise RuntimeUnavailableError(
                f"{directory / RUNTIME_CSS} is missing. The stylesheet is required, "
                "not optional: the no-JavaScript fallback is gated by a pure-CSS "
                "rule, so without it a mail preview shows a blank frame instead of "
                "an explanation."
            )

        reads = {RUNTIME_JS: _read_stored(js_path), RUNTIME_CSS: _read_stored(css_path)}
        reads.update(_read_cartograph_stored(directory))
        _offer_stored_gzip(directory, reads)
        javascript = reads[RUNTIME_JS][0]
        css = reads[RUNTIME_CSS][0]
        cartograph_javascript, cartograph_css = _cartograph_pair(reads)
        return RuntimeBundle(
            javascript=javascript,
            css=css,
            version=_read_version(directory),
            sha256=sha256_of(javascript),
            lock_warnings=lock_mismatches(
                javascript, css, cartograph_javascript, cartograph_css
            ),
            cartograph_javascript=cartograph_javascript,
            cartograph_css=cartograph_css,
        )


def tarball_url(version: str) -> str:
    """The registry URL for one published version.

    Built by hand rather than by asking the registry for its metadata: the
    layout is stable and documented, and one fewer request is one fewer thing
    to fail behind a corporate proxy.
    """
    unscoped = NPM_PACKAGE.split("/")[-1]
    return f"{NPM_REGISTRY}/{NPM_PACKAGE}/-/{unscoped}-{version}.tgz"


def download_tarball(
    version: str,
    on_progress: ProgressCallback | None = None,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    downloader: Downloader | None = None,
) -> bytes:
    """Fetch one published version's tarball over HTTPS.

    HTTPS is not optional and is not configurable: these bytes end up inside
    every map the user exports, so a downgrade to plain HTTP would let anyone
    on the path choose what the recipient's browser runs.

    `downloader` lets the plugin route this through QGIS's network stack, so a
    user behind a proxy gets the proxy they already configured.
    """
    url = tarball_url(version)
    if not url.startswith("https://"):  # pragma: no cover - defensive
        raise RuntimeDownloadError(f"refusing to fetch the runtime over {url}")

    if downloader is not None:
        return downloader(url, on_progress)

    request = urllib.request.Request(url, headers={"User-Agent": "QGIS2WebMap-by-NIKA"})

    try:
        # B310 is suppressed on the call below. The scheme is checked
        # immediately above: B310 exists to catch `urlopen` on an
        # attacker-controlled string that could name `file://` or a custom
        # handler, and this URL is built by `tarball_url` from a version in our
        # own lock file and rejected unless it is https. QGIS's plugin scanner
        # runs Bandit as a blocking check, so the pragma records a reviewed
        # finding rather than leaving one outstanding.
        #
        # This comment must not begin with the pragma word itself: Bandit reads
        # the rest of such a line as a list of test IDs and warns once per word,
        # burying real findings in its output.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            total = response.getheader("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None

            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_MEMBER_BYTES:
                    raise RuntimeDownloadError(
                        "the runtime download exceeded its size limit; "
                        "refusing to continue."
                    )
                chunks.append(chunk)
                if on_progress is not None:
                    on_progress(received, total_bytes)
    except urllib.error.URLError as exc:
        raise RuntimeDownloadError(
            f"Could not download the OnlyMap runtime from {url}.\n\n{exc.reason}\n\n"
            "The download is needed once per machine; every export afterwards "
            "works offline. If this machine has no internet access, install the "
            "runtime pack manually and point ONLYMAP_RUNTIME_DIR at it."
        ) from exc

    return b"".join(chunks)


def extract_runtime(tarball: bytes, destination: Path) -> None:
    """Write the runtime's files out of an npm tarball.

    Members are read by exact name and written by us. `extractall` is not used
    and should not be: it honours whatever paths the archive claims, so a
    hostile tarball can write outside the destination ("tar slip"). Naming the
    three files we want removes that entire class of problem, and everything
    else in the package is of no interest anyway.
    """
    destination.mkdir(parents=True, exist_ok=True)

    wanted = {
        f"{TARBALL_PREFIX}/dist/{RUNTIME_JS}": RUNTIME_JS,
        f"{TARBALL_PREFIX}/dist/{RUNTIME_CSS}": RUNTIME_CSS,
        f"{TARBALL_PREFIX}/{RUNTIME_LICENCE}": RUNTIME_LICENCE,
        # the schema moved from dist/ to the package root in 0.6.2; accept
        # either, and treat it as optional below - a map draws without it
        f"{TARBALL_PREFIX}/dist/{RUNTIME_SCHEMA}": RUNTIME_SCHEMA,
        f"{TARBALL_PREFIX}/{RUNTIME_SCHEMA}": RUNTIME_SCHEMA,
        # cartograph dist files, optional like the schema: a web-map-only
        # export never needs them, so a tarball from before 0.8.0 extracting
        # without them is not an error.
        f"{TARBALL_PREFIX}/dist/{RUNTIME_CARTOGRAPH_JS}": RUNTIME_CARTOGRAPH_JS,
        f"{TARBALL_PREFIX}/dist/{RUNTIME_CARTOGRAPH_CSS}": RUNTIME_CARTOGRAPH_CSS,
    }

    written: set[str] = set()
    with tempfile.TemporaryDirectory() as scratch:
        archive_path = Path(scratch) / "runtime.tgz"
        archive_path.write_bytes(tarball)

        with tarfile.open(archive_path, "r:gz") as archive:
            for member_name, output_name in wanted.items():
                try:
                    member = archive.getmember(member_name)
                except KeyError:
                    continue
                if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                    continue
                handle = archive.extractfile(member)
                if handle is None:  # pragma: no cover - defensive
                    continue
                (destination / output_name).write_bytes(handle.read())
                written.add(output_name)

    missing = {RUNTIME_JS, RUNTIME_CSS} - written
    if missing:
        raise RuntimeDownloadError(
            f"the downloaded package did not contain {', '.join(sorted(missing))}."
        )


def read_lock() -> dict:
    """The pinned runtime build, or `{}` when the lock file is absent."""
    if not LOCK_FILE.exists():
        return {}
    try:
        return json.loads(LOCK_FILE.read_text())
    except (OSError, ValueError):  # pragma: no cover - corrupt lock file
        return {}


def lock_mismatches(
    javascript: bytes,
    css: bytes,
    cartograph_javascript: bytes | None = None,
    cartograph_css: bytes | None = None,
) -> tuple[str, ...]:
    """Where the loaded runtime differs from `runtime-lock.json`.

    Empty when it matches, or when there is no lock file to check against.

    This is a *warning* channel rather than a hard failure: a contributor
    deliberately testing an unreleased runtime build should not be blocked, and
    the mismatch surfaces in the fidelity report instead. It becomes a hard
    check the day the runtime is fetched over the network, where an unexpected
    hash means something quite different.

    The cartograph files are checked only when they are present. A bundle
    without them (an older cached runtime, or a local directory from before
    the cartograph feature existed) still draws every web map, and these
    warnings surface in the web map's own fidelity report - so absence is not
    a mismatch. The cartograph path refuses a bundle without them itself, and
    `FetchingRuntime.is_cached` re-fetches a cache that lacks them.
    """
    lock = read_lock()
    files = lock.get("files") or {}
    if not files:
        return ()

    mismatches = []
    for name, data in (
        (RUNTIME_JS, javascript),
        (RUNTIME_CSS, css),
        (RUNTIME_CARTOGRAPH_JS, cartograph_javascript),
        (RUNTIME_CARTOGRAPH_CSS, cartograph_css),
    ):
        expected = (files.get(name) or {}).get("sha256")
        if not expected or data is None:
            continue
        if expected != sha256_of(data):
            mismatches.append(
                f"{name} does not match the build pinned in runtime-lock.json "
                f"(expected {lock.get('version', 'unknown')})."
            )
    return tuple(mismatches)


def _read_cartograph_files(directory: Path) -> tuple[bytes | None, bytes | None]:
    """The cartograph dist files from a runtime directory, if both are there.

    Deliberately all-or-nothing: a directory with only one of the two is not a
    usable cartograph runtime, and reporting it as "half present" would just
    push the same judgement call onto every caller.
    """
    return _cartograph_pair(_read_cartograph_stored(directory))


def _read_cartograph_stored(
    directory: Path,
) -> dict[str, tuple[bytes, bytes | None]]:
    js_path = stored_path(directory, RUNTIME_CARTOGRAPH_JS)
    css_path = stored_path(directory, RUNTIME_CARTOGRAPH_CSS)
    if js_path is None or css_path is None:
        return {}
    return {
        RUNTIME_CARTOGRAPH_JS: _read_stored(js_path),
        RUNTIME_CARTOGRAPH_CSS: _read_stored(css_path),
    }


def _cartograph_pair(
    reads: dict[str, tuple[bytes, bytes | None]],
) -> tuple[bytes | None, bytes | None]:
    if RUNTIME_CARTOGRAPH_JS not in reads:
        return None, None
    return reads[RUNTIME_CARTOGRAPH_JS][0], reads[RUNTIME_CARTOGRAPH_CSS][0]


def _store_gzipped(directory: Path) -> None:
    """Replace each plain pinned file in `directory` with its gzipped form.

    Each stream is decompressed and compared with the bytes it was made from
    before the plain file goes, so what is published is what was verified.
    """
    probes: dict[str, str] = {}
    for name in STORED_GZIPPED:
        plain = directory / name
        if not plain.is_file():
            continue
        data = plain.read_bytes()
        stored = gzip_bytes(data)
        if _gunzip_bounded(stored) != data:  # pragma: no cover - defensive
            raise RuntimeDownloadError(f"{name} did not survive compression.")
        (directory / (name + GZIP_SUFFIX)).write_bytes(stored)
        plain.unlink()
        if name in EMBEDDED_GZIPPED:
            probes[name] = gzip_probe(data)
    (directory / COMPRESSION_RECORD).write_text(
        json.dumps({"probes": probes}), encoding="utf-8"
    )


def _offer_stored_gzip(
    directory: Path, reads: dict[str, tuple[bytes, bytes | None]]
) -> None:
    """Hand the embedder each gzipped file it may reuse; see `adopt_stored_gzip`."""
    if not any(stored is not None for _, stored in reads.values()):
        return
    try:
        record = json.loads((directory / COMPRESSION_RECORD).read_text("utf-8"))
        probes = record["probes"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    if not isinstance(probes, dict):
        return
    for name in EMBEDDED_GZIPPED:
        data, stored = reads.get(name, (b"", None))
        probe = probes.get(name)
        if stored is not None and isinstance(probe, str):
            adopt_stored_gzip(data, stored, probe)


def _read_version(directory: Path) -> str:
    """The version of the bytes actually loaded, not the version pinned.

    The manifest beside the loaded runtime wins over `runtime-lock.json`. This
    ordering matters because the version travels into the artifact's provenance
    comment, which is what tells a person or an AI assistant which attribute
    vocabulary the exported map speaks. With `ONLYMAP_RUNTIME_DIR` pointing at a
    build other than the pinned one, reporting the pin would describe bytes that
    are not there - worse than reporting "unknown".
    """
    package_json = directory.parent / "package.json"
    if package_json.exists():
        try:
            version = json.loads(package_json.read_text()).get("version")
            if version:
                return str(version)
        except (OSError, ValueError):  # pragma: no cover
            pass

    locked = read_lock().get("version")
    if locked:
        return str(locked)

    return "unknown"


class FetchingRuntime:
    """Downloads the runtime once per machine, then reads it from the cache.

    The order matters and is the whole design:

    1. Already cached? Read it. No network, no prompt. This is every export
       after the first, on any project, forever.
    2. Licence not accepted for this version? Raise `RuntimeNotAcceptedError`
       so the caller can show the terms. Never download first and ask later.
    3. Download, verify the SHA-256 against `runtime-lock.json`, extract.

    Verification happens before the bytes are cached, so a corrupted or
    substituted download never becomes the thing a later export silently reads.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        on_progress: ProgressCallback | None = None,
        version: str | None = None,
        downloader: Downloader | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._on_progress = on_progress
        self._downloader = downloader
        self._version = version or str(read_lock().get("version") or "")

    @property
    def version(self) -> str:
        return self._version

    def cached_dir(self) -> Path:
        return cached_runtime_dir(self._version, self._cache_dir)

    def is_cached(self) -> bool:
        """Whether this version is fully on disk and intact, by the lock's terms.

        Checks the core files (JavaScript and stylesheet) unconditionally, plus
        the cartograph files when `runtime-lock.json` pins them. The second half
        is what makes a cache written by an older plugin version - before the
        cartograph feature added those entries to the lock - count as *not*
        cached: `load()` then re-fetches and re-verifies it instead of silently
        running without cartograph support or crashing when a writer asks for
        it. When the lock pins this version, each file's bytes must also match
        its pinned SHA-256, so a truncated or edited file is re-fetched rather
        than loaded. Custom runtimes (`ONLYMAP_RUNTIME_DIR`) never reach this
        check; they are read by `LocalRuntime` as they are.
        """
        directory = self.cached_dir()
        lock = read_lock()
        # Only a lock for this exact version can say what its bytes must be.
        pinned = (
            (lock.get("files") or {}) if lock.get("version") == self._version else {}
        )
        required = [RUNTIME_JS, RUNTIME_CSS]
        required += [
            name
            for name in (RUNTIME_CARTOGRAPH_JS, RUNTIME_CARTOGRAPH_CSS)
            if name in pinned
        ]
        return all(
            _file_intact(directory, name, (pinned.get(name) or {}).get("sha256"))
            for name in required
        )

    def licence_text(self) -> str | None:
        """The licence as published with the runtime, if it has been fetched."""
        path = self.cached_dir() / RUNTIME_LICENCE
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    def preflight(self) -> None:
        """Raise now if `load()` would refuse, without reading or downloading.

        The licence gate is a precondition, and a precondition that fires at the
        *end* of a job reads as a failure rather than a prompt. A first
        Processing run spent 50 seconds reading a project and translating its
        symbology before being told the runtime licence had not been accepted -
        knowable before any of that work started.

        Deliberately does not fetch: the point is to answer cheaply. A cached
        runtime, or one whose licence is already accepted, returns quietly and
        `load()` does the real work.
        """
        if not self._version:
            raise RuntimeUnavailableError(
                "No runtime version is pinned. runtime/runtime-lock.json is "
                "missing or unreadable, so there is nothing to fetch."
            )
        if not self.is_cached() and not licence_accepted(
            self._version, self._cache_dir
        ):
            raise RuntimeNotAcceptedError(
                f"The OnlyMap runtime ({NPM_PACKAGE} {self._version}) is not "
                "installed on this machine, and its licence has not been "
                "accepted yet."
            )

    def load(self) -> RuntimeBundle:
        self.preflight()
        if not self.is_cached():
            self.fetch()

        return LocalRuntime(self.cached_dir()).load()

    def _install_complete(self, directory: Path) -> bool:
        """Whether `directory` holds every file the lock pins for this version.

        Stricter than `is_cached` and than `lock_mismatches`, which both let an
        unpinned or absent optional file pass: this is what authorises deleting
        other versions, so a lock that does not pin this version, or any pinned
        file missing or different, says no.
        """
        lock = read_lock()
        if lock.get("version") != self._version:
            return False
        files = lock.get("files") or {}
        if not {RUNTIME_JS, RUNTIME_CSS} <= set(files):
            return False
        return all(
            isinstance(entry, dict)
            and bool(entry.get("sha256"))
            and _file_intact(directory, name, entry["sha256"])
            for name, entry in files.items()
        )

    def fetch(
        self,
        on_progress: ProgressCallback | None = None,
        downloader: Downloader | None = None,
    ) -> Path:
        """Download, verify and cache. Returns the directory written.

        Both arguments override what the constructor was given, so a caller can
        supply them at the point it has a progress dialog and a cancellation
        token to bind them to, rather than when it built the provider.
        """
        tarball = download_tarball(
            self._version,
            on_progress or self._on_progress,
            downloader=downloader or self._downloader,
        )

        destination = self.cached_dir()
        # Staged beside the target and moved into place, so an interrupted
        # download cannot leave a half-extracted directory that `is_cached`
        # would later believe.
        staging = destination.with_name(destination.name + ".partial")
        if staging.exists():
            _remove_tree(staging)

        extract_runtime(tarball, staging)

        javascript = (staging / RUNTIME_JS).read_bytes()
        css = (staging / RUNTIME_CSS).read_bytes()
        cartograph_javascript, cartograph_css = _read_cartograph_files(staging)
        mismatches = lock_mismatches(
            javascript, css, cartograph_javascript, cartograph_css
        )
        if mismatches:
            _remove_tree(staging)
            raise RuntimeDownloadError(
                "The downloaded runtime does not match the build this plugin "
                "was tested against, so it has not been installed:\n\n"
                + "\n".join(mismatches)
            )
        _store_gzipped(staging)

        if destination.exists():  # a re-fetch of an incomplete or corrupt cache
            _remove_tree(destination)
        staging.rename(destination)

        # Only once this version is wholly in place and verified, and never in a
        # root someone filled by hand (`ONLYMAP_RUNTIME_CACHE`).
        if self._install_complete(destination) and not (
            self._cache_dir is None and os.environ.get("ONLYMAP_RUNTIME_CACHE")
        ):
            prune_superseded_runtimes(destination.parent, self._version)
        return destination


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _file_intact(directory: Path, name: str, expected: str | None) -> bool:
    """Whether one runtime file is present, readable and, if pinned, matches."""
    path = stored_path(directory, name)
    if path is None:
        return False
    if not expected and not path.name.endswith(GZIP_SUFFIX):
        return True  # nothing to compare a plain file against
    try:
        digest = _stored_digest(path)
    except (OSError, CorruptRuntimeFileError):
        return False
    return not expected or digest == expected


# A cache entry this module wrote: a release version's directory, holding only
# files it writes. Pre-release names (`1.0.0-rc.1`) are never recognised, so
# they are never pruned.
_RELEASE_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
_OWNED_FILES = frozenset(
    {
        RUNTIME_JS,
        RUNTIME_CSS,
        RUNTIME_LICENCE,
        RUNTIME_SCHEMA,
        RUNTIME_CARTOGRAPH_JS,
        RUNTIME_CARTOGRAPH_CSS,
        COMPRESSION_RECORD,
        *(name + GZIP_SUFFIX for name in STORED_GZIPPED),
    }
)


def _release(name: str) -> tuple[int, int, int] | None:
    match = _RELEASE_VERSION.fullmatch(name)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _owned_runtime_dir(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    return all(
        child.name in _OWNED_FILES and not child.is_symlink() and child.is_file()
        for child in path.iterdir()
    )


def prune_superseded_runtimes(cache_dir: Path, installed: str) -> list[Path]:
    """Delete cached runtime versions older than `installed`; return what went.

    Called by `fetch` after `installed` is in place and fully verified. Only
    directories named for an *older* release and holding nothing but files this
    module writes are removed: never `installed` itself, a newer version
    (another QGIS on this machine may run a newer plugin from the same cache),
    a `.partial` install in progress, a licence marker, a symlink, or anything
    else. An older plugin that still needs its version downloads it again; an
    exported map carries its own copy of the runtime and is unaffected.

    Best effort by design: a directory another process is reading (Windows
    refuses to delete an open file), or one that vanished meanwhile, is left
    and skipped, and a failure here never fails the install that called it.
    """
    current = _release(installed)
    if current is None:
        return []
    removed: list[Path] = []
    try:
        entries = sorted(cache_dir.iterdir())
    except OSError:
        return removed
    for entry in entries:
        version = _release(entry.name)
        if version is None or version >= current:
            continue
        try:
            if not _owned_runtime_dir(entry):
                continue
            shutil.rmtree(entry)
        except OSError:
            continue
        removed.append(entry)
    return removed


def default_provider(
    cache_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> RuntimeProvider:
    """A local copy if there is one, otherwise fetch.

    Local first so a contributor's `ONLYMAP_RUNTIME_DIR`, a vendored copy, and a
    manually installed runtime pack all keep working with no network and no
    prompt - which is also what makes CI and the offline story the same code
    path rather than a special case.
    """
    local = discover_runtime_dir()
    if local is not None:
        return LocalRuntime(local)
    return FetchingRuntime(cache_dir=cache_dir, on_progress=on_progress)
