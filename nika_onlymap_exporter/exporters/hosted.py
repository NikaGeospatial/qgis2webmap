"""NIKA hosting: the artifact lands on a public URL instead of a disk.

The destination issue #29 was holding the seam open for. Nothing in
`writers/` changes to add it - this exporter receives an `ArtifactResult` that
is already correct and moves its bytes somewhere else.

**The hosted artifact is what gets published**, not the standalone single file
and not the folder tier's unbundled output. A hosted map is fetched over HTTP by
definition, which is the one place a page can load its layer data from a file
beside it rather than carrying it inline - so that is what the hosted tier
writes, with each data file referenced by its content digest.

**The manifest decides what is published.** Staging copies exactly the files it
names, whatever they are: the page, its data, its icons. An earlier version
copied a hardcoded list of three filenames, which meant a map whose data lived
in a sibling file published as a map with no data in it and said nothing.

**The runtime is never uploaded.** `onlymap.js` is 8.3 MB and byte-identical for
every map built against the same OnlyMap release, so a hosted page does not
carry a copy of it at all: `index.html` loads it from the CDN with an
`integrity` attribute pinning its digest - see `packaging/cdn_runtime.py` - and
there is no sibling runtime file in the artifact for this exporter to find. The
manifest still names the version and its digest, because a publisher has to
know which runtime a release was built against. Two consequences worth knowing:
the upload is 8.3 MB smaller than an unbundled folder on disk, and the per-map
size cap is spent entirely on the map.

**A file the server already holds is not uploaded at all.** The store is
content addressed, so `start` hands back a target only for digests it is
missing; republishing a map whose data did not change sends the page and
nothing else.

**Publishing is two acts, and they are separated on purpose.** `prepare` sends
the manifest and gets back the upload targets and the account's tier - no map
data leaves the machine. `publish` is what actually uploads. The gap between
them is where the free-tier truncation warning goes, and it exists because a
warning that arrives after the upload is not a warning.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core.export_ir import OutputMode
from ..hosting.client import (
    HostingClient,
    HostingError,
    PublishResult,
    PublishStart,
)
from ..hosting.manifest import ManifestFile, PublishManifest
from ..hosting.thumbnail import THUMBNAIL_FILENAME
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome

# The thumbnail is this exporter's own addition rather than the writer's, so it
# is this module that has to describe it to the manifest.
THUMBNAIL_ROLE = "thumbnail"
THUMBNAIL_MEDIA_TYPE = "image/png"

# What a file is sent as when the manifest names no type. The server sniffs
# nothing; a wrong type here is a map the browser downloads instead of
# rendering, which is why the manifest carries a `mediaType` per file.
DEFAULT_MEDIA_TYPE = "application/octet-stream"

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

    `manifest` is the one that was sent, not the one that came in: sizes and
    digests in it are of the staged bytes, after the licence key was stripped.
    """

    start: PublishStart
    manifest: PublishManifest
    files: tuple[tuple[str, Path], ...]
    title: str

    @property
    def is_free_tier(self) -> bool:
        return self.start.is_free_tier

    @property
    def total_bytes(self) -> int:
        return sum(entry["size"] for entry in self.manifest["files"])

    @property
    def upload_bytes(self) -> int:
        """What actually goes over the wire once dedup is accounted for."""
        return sum(
            entry["size"]
            for entry in self.manifest["files"]
            if self.start.target_for(entry["sha256"]) is not None
        )


class HostedExporter:
    """Publishes an unbundled artifact to NIKA and returns its public URL."""

    def __init__(
        self,
        client: HostingClient,
        title: str,
        manifest: PublishManifest,
        thumbnail_png: bytes = b"",
        map_id: str | None = None,
        release_n: int | None = None,
        on_progress: PublishProgress | None = None,
        force: bool = False,
    ) -> None:
        self.client = client
        self.title = title
        # Built by the writer from what it actually wrote. This exporter adds
        # the thumbnail to it and re-measures the staged bytes; it invents no
        # other entry.
        self.manifest = manifest
        self.thumbnail_png = thumbnail_png
        # Present means republish: the server keeps the address and bumps the
        # release, which is what makes a corrected map keep the link that has
        # already been shared.
        self.map_id = map_id
        # The release this plugin last saw. Declaring it is what turns "someone
        # else published in the meantime" into a question instead of a silent
        # overwrite.
        self.release_n = release_n
        self.on_progress = on_progress
        # Only ever True because the user answered the conflict question with
        # "overwrite". Never a default and never decided here.
        self.force = force

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
        """Stage the files locally and reserve a release. Uploads nothing.

        Staged into `destination` rather than uploaded straight from the
        writer's output for the same reason `build_artifact` stages: the set of
        bytes that goes online is then a directory somebody can look at, and
        what is published is decided by what was copied rather than by trusting
        what the writer happened to leave behind.
        """
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = result.entry_path.parent

        declared = list(self.manifest["files"])
        if not declared:
            raise HostingError(
                "The built map's manifest names no files, so there is nothing "
                "to publish. Nothing was uploaded."
            )

        staged: list[tuple[ManifestFile, Path]] = []
        for entry in declared:
            relative = _safe_relative_path(entry["path"])
            source = source_dir / relative
            if not source.is_file():
                raise HostingError(
                    f"The built map is missing {entry['path']}, which its "
                    "manifest names. Nothing was uploaded; rebuild the map "
                    "and try again."
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged.append((entry, target))

        entry_name = self.manifest["entry"]
        if not any(item["path"] == entry_name for item, _ in staged):
            raise HostingError(
                f"The built map has no {entry_name}, so there is nothing to "
                "publish. Nothing was uploaded."
            )

        # The hosted artifact must carry NO licence key. The server owns that
        # attribute now: `nika-host` sets or strips `license-key` on every
        # response, so anything embedded here would be overwritten anyway - and
        # a key travelling in an upload is one that could be lifted from a
        # staged folder, or smuggled in by a hand-edited artifact to claim
        # entitlements the account has not paid for. Stripping it here makes
        # that contract true on our side rather than merely enforced on theirs.
        _strip_license_key(destination / _safe_relative_path(entry_name))

        if self.thumbnail_png:
            thumbnail = destination / THUMBNAIL_FILENAME
            thumbnail.write_bytes(self.thumbnail_png)
            staged.append(
                (
                    ManifestFile(
                        path=THUMBNAIL_FILENAME,
                        role=THUMBNAIL_ROLE,
                        mediaType=THUMBNAIL_MEDIA_TYPE,
                        size=len(self.thumbnail_png),
                        sha256="",
                    ),
                    thumbnail,
                )
            )

        # Measured AFTER `_strip_license_key`, and this order is load-bearing.
        # The size is signed into the presigned URL and the digest is what the
        # server verifies the stored object against, so measuring before the
        # strip would declare bytes that no longer exist: R2 refuses the PUT on
        # a length mismatch, and the release fails verification on a digest one.
        measured: list[ManifestFile] = []
        files: list[tuple[str, Path]] = []
        for entry, path in staged:
            measured.append(
                ManifestFile(
                    path=entry["path"],
                    role=entry["role"],
                    mediaType=entry["mediaType"] or DEFAULT_MEDIA_TYPE,
                    size=path.stat().st_size,
                    sha256=_sha256(path),
                )
            )
            files.append((entry["path"], path))

        sent = PublishManifest(
            schemaVersion=self.manifest["schemaVersion"],
            kind=self.manifest["kind"],
            entry=self.manifest["entry"],
            producer=self.manifest["producer"],
            runtime=self.manifest["runtime"],
            files=measured,
            externalOrigins=list(self.manifest["externalOrigins"]),
            runtimeScriptSources=list(self.manifest["runtimeScriptSources"]),
        )

        self._report(-1, "Reserving the map address...")
        start = self.client.start_publish(
            sent,
            map_id=self.map_id,
            release_n=self.release_n,
            force=self.force,
        )
        return PreparedPublish(
            start=start,
            manifest=sent,
            files=tuple(files),
            title=self.title,
        )

    def publish(self, prepared: PreparedPublish) -> ExportOutcome:
        """Upload what the server is missing, then wait for the map to be live."""
        by_path = {entry["path"]: entry for entry in prepared.manifest["files"]}
        # Only the files that actually need sending are counted, so the bar
        # does not stall at a step that is a dictionary lookup.
        pending = [
            (name, path)
            for name, path in prepared.files
            if prepared.start.target_for(by_path[name]["sha256"]) is not None
        ]
        total = len(pending)
        for index, (name, path) in enumerate(pending):
            entry = by_path[name]
            target = prepared.start.target_for(entry["sha256"])
            if target is None:  # pragma: no cover - `pending` filtered on this
                continue
            # A share of the bar per file rather than per byte: the transport
            # seam hands back no byte progress (the same limitation
            # `make_qgis_downloader` documents), and a bar that moves per file
            # is honest where one stuck at 0% is not.
            percent = int(100 * index / (total + 1))
            self._report(percent, f"Uploading {name} ({index + 1} of {total})...")
            self.client.upload(
                target,
                path.read_bytes(),
                content_type=entry["mediaType"] or DEFAULT_MEDIA_TYPE,
            )

        self._report(int(100 * total / (total + 1)), "Publishing...")
        self.client.complete(prepared.start.release_id)
        # The upload finishing and the map being up are different moments: the
        # server verifies every digest it was promised before it serves a byte.
        self._report(99, "Verifying...")
        published = self.client.await_release(prepared.start.release_id)
        return self._outcome(prepared, published)

    def _outcome(
        self, prepared: PreparedPublish, published: PublishResult
    ) -> ExportOutcome:
        return ExportOutcome(
            # The staging directory, so `ExportOutcome.path` still names
            # something real on disk; the URL is what the user is given.
            path=prepared.files[0][1].parent,
            mode=self.mode,
            # What was UPLOADED, which no longer includes the 8.3 MB runtime,
            # nor anything the server already held.
            size_bytes=prepared.upload_bytes,
            open_instruction=(
                f"Published at {published.public_url} - anyone with the link "
                "can open it."
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
        sent; the reservation is simply never completed.
        """
        prepared = self.prepare(result, destination)
        if confirm is not None and not confirm(prepared):
            raise PublishCancelledError(
                "Publishing was cancelled. Nothing was uploaded."
            )
        return self.publish(prepared)


def _safe_relative_path(path: str) -> PurePosixPath:
    """A manifest path as something safe to join onto a directory.

    The manifest is our own output, so this is not a trust boundary so much as
    a refusal to have one: a path that climbs out of the artifact would write
    wherever it pleased on the way to being staged.
    """
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        raise HostingError(
            f"'{path}' is not a path inside the map, so it cannot be "
            "published. Nothing was uploaded."
        )
    return candidate


def _sha256(path: Path) -> str:
    """The content address of a staged file, in the server's spelling."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
