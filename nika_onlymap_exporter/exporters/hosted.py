"""NIKA hosting: the artifact lands on a public URL instead of a disk.

The destination issue #29 was holding the seam open for. Nothing in
`writers/` changes to add it - this exporter receives an `ArtifactResult` that
is already correct and moves its bytes somewhere else.

**The unbundled folder artifact is what gets published**, not the standalone
single file. A hosted map is fetched over HTTP by definition, which is the one
place a module script can load from a sibling file, and it is the reason the
folder tier exists.

**The runtime is never uploaded.** `onlymap.js` is 8.3 MB and byte-identical for
every map built against the same OnlyMap release, so NIKA stores one copy per
runtime version and points every map pinned to that version at it. What is sent
is the version string; the exported `index.html` keeps its relative
`import "./onlymap.js"` untouched and the server answers that request with the
shared copy. Two consequences worth knowing: the upload is 8.3 MB smaller than
the folder on disk, and the per-map size cap is spent entirely on the map.

**Publishing is two acts, and they are separated on purpose.** `prepare` sends
a title, filenames and sizes and gets back the presigned URLs and the account's
tier - no map data leaves the machine. `publish` is what actually uploads. The
gap between them is where the free-tier truncation warning goes, and it exists
because a warning that arrives after the upload is not a warning.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.export_ir import OutputMode
from ..hosting.client import (
    ALLOWED_FILENAMES,
    HostingClient,
    HostingError,
    PublishResult,
    PublishStart,
    UploadFile,
)
from ..hosting.thumbnail import THUMBNAIL_FILENAME
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome

# The server sniffs nothing; a wrong type here is a map the browser downloads
# instead of rendering.
CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "thumbnail.png": "image/png",
    "thumbnail.jpg": "image/jpeg",
}

# Reported as (percent, message) while the upload runs. Maps straight onto
# `ui.background_job.Progress.step`.
PublishProgress = Callable[[int, str], None]


class PublishCancelledError(HostingError):
    """The user stopped at the confirmation. Never reported as a failure."""


@dataclass(frozen=True)
class PreparedPublish:
    """A reservation plus the local files it is waiting for.

    Held between the two acts so the caller can put a question on screen -
    the free-tier truncation warning - while nothing has been uploaded and
    walking away still costs nothing.
    """

    start: PublishStart
    files: tuple[tuple[str, Path], ...]
    title: str
    # The build the artifact was generated against, carried from `prepare` to
    # `publish` so `finalize` declares the same version `start` did.
    runtime_version: str = ""

    @property
    def is_free_tier(self) -> bool:
        return self.start.is_free_tier

    @property
    def total_bytes(self) -> int:
        return sum(path.stat().st_size for _, path in self.files)


class HostedExporter:
    """Publishes an unbundled artifact to NIKA and returns its public URL."""

    def __init__(
        self,
        client: HostingClient,
        title: str,
        thumbnail_png: bytes = b"",
        map_id: str | None = None,
        on_progress: PublishProgress | None = None,
    ) -> None:
        self.client = client
        self.title = title
        self.thumbnail_png = thumbnail_png
        # Present means republish: the server keeps the address and bumps the
        # version, which is what makes a corrected map keep the link that has
        # already been shared.
        self.map_id = map_id
        self.on_progress = on_progress

    @property
    def mode(self) -> OutputMode:
        """The artifact shape this consumes, not a tier the dialog offers.

        `FOLDER` because that is the one the writer unbundles; hosting is a
        destination rather than an output mode, and adding it to `OutputMode`
        would put it in the Map tab's radio group where it does not belong.
        """
        return OutputMode.FOLDER

    def _report(self, percent: int, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(percent, message)

    def prepare(self, result: ArtifactResult, destination: Path) -> PreparedPublish:
        """Stage the files locally and reserve a version. Uploads nothing.

        Staged into `destination` rather than uploaded straight from the
        writer's output for the same reason `build_artifact` stages: the set of
        bytes that goes online is then a directory somebody can look at, and the
        filename allowlist is applied by what is copied rather than by trusting
        what the writer happened to leave behind.
        """
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = result.entry_path.parent

        staged: list[tuple[str, Path]] = []
        for item in sorted(source_dir.iterdir()):
            # A README, a stray sidecar, anything the writer gains later: the
            # server would reject it, and there is no reason to send it.
            if not item.is_file() or item.name not in ALLOWED_FILENAMES:
                continue
            target = destination / item.name
            shutil.copy2(item, target)
            staged.append((item.name, target))

        # The hosted artifact must carry NO licence key. The server owns that
        # attribute now: `nika-host` sets or strips `license-key` on every
        # response, so anything embedded here would be overwritten anyway - and
        # a key travelling in an upload is one that could be lifted from a
        # staged folder, or smuggled in by a hand-edited artifact to claim
        # entitlements the account has not paid for. Stripping it here makes
        # that contract true on our side rather than merely enforced on theirs.
        _strip_license_key(destination / "index.html")

        if not any(name == "index.html" for name, _ in staged):
            raise HostingError(
                "The built map has no index.html, so there is nothing to "
                "publish. Nothing was uploaded."
            )

        if self.thumbnail_png:
            thumbnail = destination / THUMBNAIL_FILENAME
            thumbnail.write_bytes(self.thumbnail_png)
            staged.append((THUMBNAIL_FILENAME, thumbnail))

        prepared_files = tuple(sorted(staged))
        # Measured AFTER `_strip_license_key`, and this order is load-bearing.
        # The server signs each declared size into its presigned URL, so R2
        # refuses a body of any other length - declaring a size and then
        # changing the file would make every publish fail the signature with a
        # 403 that says nothing about why.
        files = tuple(
            UploadFile(name, path.stat().st_size) for name, path in prepared_files
        )
        self._report(-1, "Reserving the map address...")
        start = self.client.start_publish(
            self.title,
            files,
            map_id=self.map_id,
            runtime_version=result.runtime_version,
        )
        return PreparedPublish(
            start=start,
            files=prepared_files,
            title=self.title,
            runtime_version=result.runtime_version,
        )

    def publish(self, prepared: PreparedPublish) -> ExportOutcome:
        """Upload every staged file, then make the version live."""
        total = len(prepared.files)
        for index, (name, path) in enumerate(prepared.files):
            # A share of the bar per file rather than per byte: the transport
            # seam hands back no byte progress (the same limitation
            # `make_qgis_downloader` documents), and a bar that moves per file
            # is honest where one stuck at 0% is not.
            percent = int(100 * index / (total + 1))
            self._report(percent, f"Uploading {name} ({index + 1} of {total})...")
            self.client.upload(
                prepared.start.target_for(name),
                path.read_bytes(),
                content_type=CONTENT_TYPES.get(name, "application/octet-stream"),
            )

        self._report(int(100 * total / (total + 1)), "Publishing...")
        published = self.client.finalize(
            prepared.start.map_id,
            prepared.start.version,
            title=prepared.title,
            runtime_version=prepared.runtime_version or None,
        )
        return self._outcome(prepared, published)

    def _outcome(
        self, prepared: PreparedPublish, published: PublishResult
    ) -> ExportOutcome:
        expiry = f" It expires {published.expires_at}." if published.expires_at else ""
        # Appended to the success message rather than raised. The map IS live;
        # running a different runtime build than the one it was authored against
        # is something the owner should know and can fix by republishing from an
        # updated plugin, not a reason to report a failed publish.
        runtime = f" {published.runtime_warning}" if published.runtime_warning else ""
        return ExportOutcome(
            # The staging directory, so `ExportOutcome.path` still names
            # something real on disk; the URL is what the user is given.
            path=prepared.files[0][1].parent,
            mode=self.mode,
            # What was UPLOADED, which no longer includes the 8.3 MB runtime.
            size_bytes=prepared.total_bytes,
            open_instruction=(
                f"Published at {published.public_url} - anyone with the link "
                f"can open it.{expiry}{runtime}"
            ),
            public_url=published.public_url,
        )

    def export(
        self,
        result: ArtifactResult,
        destination: Path,
        confirm: Callable[[PreparedPublish], bool] | None = None,
    ) -> ExportOutcome:
        """Both acts in one call, for callers with nothing to ask in between.

        `confirm` returning False aborts before a single byte of the map is
        sent; the reservation is simply never finalized.
        """
        prepared = self.prepare(result, destination)
        if confirm is not None and not confirm(prepared):
            raise PublishCancelledError(
                "Publishing was cancelled. Nothing was uploaded."
            )
        return self.publish(prepared)


# `license-key="om_live_..."` on the `<om-map>` element, however it is quoted.
# Deliberately narrow: it matches the attribute the manifest builder emits
# (`core/manifest_builder.py`) and nothing else, so a value that merely mentions
# the phrase inside map data is left alone.
_LICENSE_ATTR = re.compile(
    r'\s+license-key\s*=\s*(["\']).*?\1', re.IGNORECASE | re.DOTALL
)


def _strip_license_key(index_html: Path) -> None:
    """Removes any embedded licence key from a staged artifact.

    A no-op in the ordinary case - the dialog exposes no licence field, so a
    hosted build usually has none to begin with. It exists for the case that is
    not ordinary: a user with `ONLYMAP_LICENSE_KEY` set in their environment,
    whose every export would otherwise carry their own key into our storage.
    """
    if not index_html.is_file():
        return
    original = index_html.read_text(encoding="utf-8", errors="surrogateescape")
    stripped = _LICENSE_ATTR.sub("", original)
    if stripped != original:
        index_html.write_text(stripped, encoding="utf-8", errors="surrogateescape")
