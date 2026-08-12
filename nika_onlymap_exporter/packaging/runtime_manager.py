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
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PACKAGE_ROOT / "runtime"
LOCK_FILE = RUNTIME_DIR / "runtime-lock.json"

# Filenames inside the OnlyMap package.
RUNTIME_JS = "onlymap.standalone.js"
RUNTIME_CSS = "onlymapjs.css"
RUNTIME_LICENCE = "LICENSE.md"
# The runtime's attribute contract. Optional: the map works without it, but
# the test suite's attribute-contract checks and AI-assisted editing both read
# it, so it is cached beside the runtime when the package ships it.
RUNTIME_SCHEMA = "onlymapjs.html-data.json"

# npm is the canonical channel: the same one the library's own users install
# from, so the plugin never becomes a second, staler distribution path. The
# registry serves it anonymously - no account, no token.
NPM_PACKAGE = "@nika-js/onlymap"
NPM_REGISTRY = "https://registry.npmjs.org"

# Paths inside the npm tarball. npm wraps everything in `package/`.
TARBALL_PREFIX = "package"

# A decompression-bomb guard. The real tarball is ~4.5 MB and expands to ~8 MB;
# anything an order of magnitude past that is not the runtime.
MAX_MEMBER_BYTES = 64 * 1024 * 1024

# **The one place this number lives.** It is a promise about someone's bandwidth,
# shown in the licence dialog where they decide whether to accept - and it was
# wrong for a whole release because the 0.3.3 -> 0.5.11 bump left four
# hand-written copies saying "about 3 MB" for a download that had become 4.5 MB.
#
# It describes the *compressed tarball*, which is what actually crosses the
# network, not the ~8 MB the files occupy once unpacked. `tests/unit` asserts the
# user guide quotes this same string, so a bump cannot update the code and leave
# the docs behind. Update it in `scripts/lock_runtime.py`'s output when re-pinning.
RUNTIME_DOWNLOAD_SIZE = "about 4.5 MB"

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
        return path if (path / RUNTIME_JS).exists() else None

    candidates = [
        RUNTIME_DIR,
        Path("node_modules/@nika-js/onlymap/dist"),
        PACKAGE_ROOT.parent / "node_modules/@nika-js/onlymap/dist",
    ]
    return next((c for c in candidates if (c / RUNTIME_JS).exists()), None)


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
    machine with no working runtime at all.
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
    return licence_marker(version, cache_dir).is_file()


def record_licence_acceptance(version: str, cache_dir: Path | None = None) -> None:
    """Record that the user accepted this version's licence.

    Per version, deliberately: a new runtime can carry new terms, and an
    acceptance recorded against 0.5.3 says nothing about 0.6.0.
    """
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

        js_path = directory / RUNTIME_JS
        css_path = directory / RUNTIME_CSS

        if not js_path.exists():
            raise RuntimeUnavailableError(f"{js_path} is missing.")
        if not css_path.exists():
            raise RuntimeUnavailableError(
                f"{css_path} is missing. The stylesheet is required, not optional: "
                "the no-JavaScript fallback is gated by a pure-CSS rule, so without "
                "it a mail preview shows a blank frame instead of an explanation."
            )

        javascript = js_path.read_bytes()
        css = css_path.read_bytes()
        return RuntimeBundle(
            javascript=javascript,
            css=css,
            version=_read_version(directory),
            sha256=sha256_of(javascript),
            lock_warnings=lock_mismatches(javascript, css),
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


def lock_mismatches(javascript: bytes, css: bytes) -> tuple[str, ...]:
    """Where the loaded runtime differs from `runtime-lock.json`.

    Empty when it matches, or when there is no lock file to check against.

    This is a *warning* channel rather than a hard failure: a contributor
    deliberately testing an unreleased runtime build should not be blocked, and
    the mismatch surfaces in the fidelity report instead. It becomes a hard
    check the day the runtime is fetched over the network, where an unexpected
    hash means something quite different.
    """
    lock = read_lock()
    files = lock.get("files") or {}
    if not files:
        return ()

    mismatches = []
    for name, data in ((RUNTIME_JS, javascript), (RUNTIME_CSS, css)):
        expected = (files.get(name) or {}).get("sha256")
        if expected and expected != sha256_of(data):
            mismatches.append(
                f"{name} does not match the build pinned in runtime-lock.json "
                f"(expected {lock.get('version', 'unknown')})."
            )
    return tuple(mismatches)


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
        return (self.cached_dir() / RUNTIME_JS).is_file()

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
        mismatches = lock_mismatches(javascript, css)
        if mismatches:
            _remove_tree(staging)
            raise RuntimeDownloadError(
                "The downloaded runtime does not match the build this plugin "
                "was tested against, so it has not been installed:\n\n"
                + "\n".join(mismatches)
            )

        if destination.exists():  # pragma: no cover - only on a re-fetch
            _remove_tree(destination)
        staging.rename(destination)
        return destination


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


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
