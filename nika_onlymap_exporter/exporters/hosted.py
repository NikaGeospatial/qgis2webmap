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
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ..core.export_ir import OutputMode
from ..hosting.client import (
    STATE_LIVE,
    STATE_VERIFYING,
    AuthRequiredError,
    HostingClient,
    HostingError,
    PublishRefusedError,
    PublishResult,
    PublishStart,
)
from ..hosting.manifest import ManifestFile, PublishManifest, Role
from ..hosting.thumbnail import THUMBNAIL_FILENAME
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome

# The thumbnail is this exporter's own addition rather than the writer's, so it
# is this module that has to describe it to the manifest.
THUMBNAIL_ROLE: Role = "thumbnail"
THUMBNAIL_MEDIA_TYPE = "image/jpeg"

# What a file is sent as when the manifest names no type. The server sniffs
# nothing; a wrong type here is a map the browser downloads instead of
# rendering, which is why the manifest carries a `mediaType` per file.
DEFAULT_MEDIA_TYPE = "application/octet-stream"

# Reported as (percent, message) while the upload runs. Maps straight onto
# `ui.background_job.Progress.step`.
PublishProgress = Callable[[int, str], None]


class PublishCancelledError(HostingError):
    """The user stopped at the confirmation. Never reported as a failure."""


# This plugin's own refusal, not the server's: a FIRST publish whose earlier
# attempt is still being verified. That attempt has no map id the project knows
# of yet, so a new reservation would be one without a map id - a second map -
# and the first would go live beside it a moment later. Raised as a
# `PublishRefusedError` so the dialog shows it as what it is, a sentence to act
# on, rather than as a failure.
REFUSAL_PREVIOUS_PUBLISH_VERIFYING = "previous_publish_verifying"
PREVIOUS_PUBLISH_VERIFYING_MESSAGE = (
    "Your last publish is still being checked by NIKA's server. Try again in a "
    "minute. Nothing was published this time."
)


# How far a publish got, so a cancellation or a failure can say what is true.
# "Nothing was written" was said after every one of them, including a Cancel
# pressed once the upload had finished and the map was going live anyway.
PublishStage = Literal["local", "reserved", "uploading", "completing", "verifying"]
STAGE_LOCAL: PublishStage = "local"
STAGE_RESERVED: PublishStage = "reserved"
STAGE_UPLOADING: PublishStage = "uploading"
STAGE_COMPLETING: PublishStage = "completing"
STAGE_VERIFYING: PublishStage = "verifying"


def cancelled_publish_text(stage: PublishStage) -> str:
    """What is true after Cancel, by how far the publish had got.

    "Nothing was written" was said at every stage, including after the upload
    had finished and the server was already putting the map live.
    """
    if stage == STAGE_UPLOADING:
        return (
            "Stopped part-way through the upload. Nothing new was published: "
            "the map is unchanged, and NIKA discards the partial upload."
        )
    if stage in (STAGE_COMPLETING, STAGE_VERIFYING):
        return (
            "Stopped watching, but the upload had already finished, so the map "
            "will most likely update anyway. Press Republish later to check - "
            "the plugin recognises its own upload."
        )
    return "Stopped before anything was uploaded. Nothing was published."


@dataclass(frozen=True)
class PendingReconciliation:
    """What asking about an earlier, unsettled upload established.

    `settled` means the pending release id can be forgotten: it went live, it
    failed, or the server has never heard of it. `verifying` means the server
    answered and is still checking it, as opposed to not answering at all.
    `adopted_map_id` and `adopted_release_n` are set only when it went LIVE on
    the map this project points at (or on a first publish, where the project
    had no map yet) - the only case where the server's newer release is
    provably this client's own.
    """

    settled: bool
    adopted_map_id: str | None = None
    adopted_release_n: int | None = None
    verifying: bool = False


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
        pending_release_id: str | None = None,
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
        # A release this project uploaded earlier and never saw settle. Asked
        # about before the reservation, so an upload that did go live is
        # adopted instead of being reported back as a colleague's conflict.
        self.pending_release_id = pending_release_id
        self.reconciliation: PendingReconciliation | None = None
        # Written from the worker thread, read by the dialog after a cancel or a
        # failure; a plain attribute is enough for that hand-off.
        self.stage: PublishStage = STAGE_LOCAL

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

        Every file but the page is staged as a hard link where the disk allows
        one, so a 4 GB raster does not take another 4 GB to stage and minutes to
        copy. The page is always a real copy: it is the one file edited here,
        and editing a link would edit the writer's own output with it.
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
            _stage_file(
                source, target, linkable=entry["path"] != self.manifest["entry"]
            )
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
        #
        # Every file but the page keeps the digest the writer's manifest gave
        # it, when the size on disk still agrees: `build_publish_manifest`
        # hashed those exact bytes moments ago, and hashing a large raster a
        # second time costs minutes and proves nothing new. Should the bytes
        # somehow differ anyway, the server's own read-back at `complete` fails
        # the release by name rather than serving them. The page is always
        # re-hashed, because it is the one file this method may have changed.
        measured: list[ManifestFile] = []
        files: list[tuple[str, Path]] = []
        for entry, path in staged:
            size = path.stat().st_size
            reusable = (
                entry["path"] != entry_name
                and entry["size"] == size
                and _is_sha256(entry["sha256"])
            )
            measured.append(
                ManifestFile(
                    path=entry["path"],
                    role=entry["role"],
                    mediaType=entry["mediaType"] or DEFAULT_MEDIA_TYPE,
                    size=size,
                    sha256=entry["sha256"] if reusable else _sha256(path),
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
            title=self.manifest["title"],
        )

        self._report(-1, "Reserving the map address...")
        self.reconciliation = self._reconcile_pending()
        # Only a first publish is held back. A republish names its map, so the
        # release it reserves now lands on the same map as the one being
        # checked, and the newer of the two simply becomes current.
        pending = self.reconciliation
        if pending is not None and pending.verifying and not self.map_id:
            raise PublishRefusedError(
                PREVIOUS_PUBLISH_VERIFYING_MESSAGE,
                code=REFUSAL_PREVIOUS_PUBLISH_VERIFYING,
            )
        start = self.client.start_publish(
            sent,
            map_id=self.map_id,
            release_n=self.release_n,
            force=self.force,
        )
        self.stage = STAGE_RESERVED
        return PreparedPublish(
            start=start,
            manifest=sent,
            files=tuple(files),
            title=self.title,
        )

    def _reconcile_pending(self) -> PendingReconciliation | None:
        """Settle an earlier upload whose outcome this project never saw.

        The proof that a newer release is this client's own is the release id:
        a UUID the server handed to this project's reservation and to nobody
        else. Asking about it by id, and adopting its number only when it is
        live on the map this project points at, is what distinguishes "my own
        upload went live while I was offline" from "a colleague published" -
        which the release NUMBER alone cannot.

        Adopting raises the stored release to at least that number and never
        lowers it. A colleague who published after it still produces the
        conflict, correctly, because their release is higher again.

        An expired sign-in is raised, as everywhere else. Any other failure to
        ask leaves the pending id in place and the publish carries on exactly as
        it would have without it: the worst case is the old false conflict, not
        a lost publish.
        """
        release_id = self.pending_release_id
        if not release_id:
            return None
        try:
            status = self.client.release_status(release_id)
        except AuthRequiredError:
            raise
        except PublishRefusedError:
            # A structured refusal is the server saying it has no such release
            # for this organisation - nothing is pending any more.
            return PendingReconciliation(settled=True)
        except HostingError:
            return PendingReconciliation(settled=False)

        if status.state == STATE_VERIFYING:
            return PendingReconciliation(settled=False, verifying=True)
        if status.state != STATE_LIVE or not status.map_id:
            return PendingReconciliation(settled=True)
        if self.map_id and status.map_id != self.map_id:
            # Live, but on a map this project no longer points at - it was
            # detached since. Nothing to adopt.
            return PendingReconciliation(settled=True)

        adopted_n = max(self.release_n or 0, status.release_n)
        self.map_id = status.map_id
        self.release_n = adopted_n
        return PendingReconciliation(
            settled=True, adopted_map_id=status.map_id, adopted_release_n=adopted_n
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
            self.stage = STAGE_UPLOADING
            # The path, not its bytes: the client streams it from disk.
            self.client.upload(
                target,
                path,
                content_type=entry["mediaType"] or DEFAULT_MEDIA_TYPE,
            )

        self._report(int(100 * total / (total + 1)), "Publishing...")
        self.stage = STAGE_COMPLETING
        self.client.complete(prepared.start.release_id)
        self.stage = STAGE_VERIFYING
        # The upload finishing and the map being up are different moments: the
        # server verifies every digest it was promised before it serves a byte.
        self._report(99, "Verifying...")
        # Each poll reports, which is also what lets Cancel stop the wait.
        published = self.client.await_release(
            prepared.start.release_id,
            on_progress=lambda _status: self._report(99, "Verifying..."),
        )
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


def _stage_file(source: Path, target: Path, *, linkable: bool) -> None:
    """Put one file into the staging directory, as a link where that is safe.

    A link fails across disks, on file systems without them and where a stale
    file is already in the way; each of those falls back to the copy this used
    to make every time.
    """
    if linkable:
        if target.exists():
            target.unlink()
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: str) -> bool:
    return _SHA256_PATTERN.match(value) is not None


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
