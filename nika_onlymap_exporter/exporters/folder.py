"""A folder containing the artifact.

Mostly a staging step for the ZIP exporter, and useful on its own when a user
wants to publish the result to a web server themselves.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from ..core.atomic_write import write_text_atomic
from ..core.export_ir import OutputMode
from ..ui.i18n import tr
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome

#: Lists the files this exporter wrote, so a re-export can remove the ones the
#: map no longer has (a raster taken out of the project) without ever touching
#: a file the user put in the folder themselves.
MANIFEST_NAME = ".qgis2webmap-export.json"


def _is_plain_name(name: str) -> bool:
    return name not in ("", ".", "..") and not any(c in name for c in "/\\\x00")


def _read_owned(destination: Path) -> set[str]:
    """File names the previous export recorded. A missing or unreadable manifest
    means nothing is known to be ours, so nothing will be removed."""
    try:
        data = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
        names = data["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    if not isinstance(names, list):
        return set()
    return {n for n in names if isinstance(n, str) and _is_plain_name(n)}


def _manifest_text(names: set[str]) -> str:
    return json.dumps({"files": sorted(names)}, indent=2)


#: Hidden directory, inside the destination so it is on the same filesystem and
#: every move into place is a rename. Only directories with this exact prefix are
#: ever treated as ours.
STAGING_PREFIX = ".q2w-staging-"
_NEW = "new"
_OLD = "old"


class RecoveryIncompleteError(OSError):
    """Backups of the previous export could not be put back; they stay in
    their staging directory, which a later export tries again."""


def _recover_interrupted(destination: Path) -> None:
    """Undo what a crashed export left behind. A staging directory that still
    holds a `old/` copy died part-way through publishing: put those previous
    files back, then drop the directory. Only our own marker-named directories
    are looked at.

    Each restore is a move, so a restored file leaves `old/` at once: a retry
    after a partial failure only sees what is left and can never put a stale
    copy over a file it already restored. A staging directory is removed only
    when `old/` is empty; otherwise it is kept (minus `new/`, which holds only
    files that were never published) and RecoveryIncompleteError names it."""
    kept: list[Path] = []
    for stale in sorted(destination.glob(f"{STAGING_PREFIX}*")):
        if not stale.is_dir():
            continue
        old_dir = stale / _OLD
        if old_dir.is_dir():
            try:
                backups = list(old_dir.iterdir())
            except OSError:
                kept.append(stale)
                continue
            unrestored = False
            for backup in backups:
                try:
                    os.replace(backup, destination / backup.name)
                except OSError:
                    unrestored = True
            if unrestored:
                kept.append(stale)
                shutil.rmtree(stale / _NEW, ignore_errors=True)
                continue
        shutil.rmtree(stale, ignore_errors=True)
    if kept:
        raise RecoveryIncompleteError(
            tr(
                "The previous export could not be fully restored. Its remaining "
                "backups have been kept at {path}. Resolve the permission or "
                "file-access problem and try again."
            ).format(path=", ".join(str(k) for k in kept))
        )


def _publish(staging: Path, destination: Path, names: list[str]) -> None:
    """Move every staged file over its counterpart; index.html goes last, so a page
    never points at assets that are not there yet. Each file it replaces is first
    moved to `old/`; if anything fails, every move is undone and the destination
    is exactly as it was."""
    new_dir = staging / _NEW
    old_dir = staging / _OLD
    old_dir.mkdir()
    ordered = sorted(names, key=lambda n: (n == "index.html", n))
    done: list[tuple[str, bool]] = []  # (name, had a previous version)
    try:
        for name in ordered:
            target = destination / name
            had_previous = target.is_file()
            if had_previous:
                os.replace(target, old_dir / name)
            done.append((name, had_previous))
            os.replace(new_dir / name, target)
    except BaseException as failure:
        incomplete = False
        for name, had_previous in reversed(done):
            target = destination / name
            try:
                if had_previous:
                    os.replace(old_dir / name, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                # Keep going: restore as much as possible.
                incomplete = True
        if incomplete:
            raise _RollbackIncompleteError from failure
        raise


class _RollbackIncompleteError(Exception):
    """Some previous files could not be put back; their backups must be kept."""


class FolderExporter:
    @property
    def mode(self) -> OutputMode:
        return OutputMode.FOLDER

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        """Replace the managed files all-or-nothing. The whole new export is built
        in a hidden staging directory first; only when every file is complete is
        anything in `destination` touched. A failure at any point leaves the
        previous export byte-identical (and removes the staging directory).
        Files the exporter did not write are never moved or deleted."""
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = result.entry_path.parent

        items = sorted(i for i in source_dir.iterdir() if i.is_file())
        new_names = {i.name for i in items}
        _recover_interrupted(destination)
        previously_owned = _read_owned(destination)

        staging = Path(tempfile.mkdtemp(dir=destination, prefix=STAGING_PREFIX))
        try:
            new_dir = staging / _NEW
            new_dir.mkdir()
            total = 0
            for item in items:
                staged = new_dir / item.name
                shutil.copy2(item, staged)
                # A plain copy keeps the file's mode; match the file it replaces.
                try:
                    mode = (destination / item.name).stat().st_mode & 0o777
                except OSError:
                    mode = 0o644
                os.chmod(staged, mode)
                total += staged.stat().st_size
            # Old and new together: if the process dies after this lands, the next
            # export still knows every file that might be ours.
            (new_dir / MANIFEST_NAME).write_text(
                _manifest_text(previously_owned | new_names), encoding="utf-8"
            )
            _publish(staging, destination, [*new_names, MANIFEST_NAME])
        except _RollbackIncompleteError as incomplete:
            # The backups in staging are all that is left of some previous
            # files; the next export puts them back (`_recover_interrupted`).
            raise OSError(
                tr(
                    "The export failed and the previous files could not all be "
                    "restored; they are kept in {path}"
                ).format(path=staging)
            ) from incomplete.__cause__
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(staging, ignore_errors=True)

        # Only after every new file is in place: drop what the map no longer has.
        for obsolete in previously_owned - new_names:
            with contextlib.suppress(OSError):
                (destination / obsolete).unlink()
        # The export is complete; narrowing the manifest is housekeeping.
        with contextlib.suppress(OSError):
            write_text_atomic(destination / MANIFEST_NAME, _manifest_text(new_names))

        return ExportOutcome(
            path=destination,
            mode=self.mode,
            size_bytes=total,
            open_instruction=(
                "Upload this folder to a web server, then open index.html from "
                "there. Keep index.html and onlymap.js together. This tier does "
                "not open straight off your disk - use the single-file export "
                "for that."
            ),
        )
