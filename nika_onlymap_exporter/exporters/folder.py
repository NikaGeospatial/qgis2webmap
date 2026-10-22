"""A folder containing the artifact.

Mostly a staging step for the ZIP exporter, and useful on its own when a user
wants to publish the result to a web server themselves.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

from ..core.atomic_write import staged_beside, write_text_atomic
from ..core.export_ir import OutputMode
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


def _write_owned(destination: Path, names: set[str]) -> None:
    write_text_atomic(
        destination / MANIFEST_NAME, json.dumps({"files": sorted(names)}, indent=2)
    )


class FolderExporter:
    @property
    def mode(self) -> OutputMode:
        return OutputMode.FOLDER

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = result.entry_path.parent

        items = sorted(i for i in source_dir.iterdir() if i.is_file())
        new_names = {i.name for i in items}
        previously_owned = _read_owned(destination)

        # Record old and new together before copying: if this export dies part-way,
        # the next one still knows every file that might be ours.
        _write_owned(destination, previously_owned | new_names)
        total = 0
        for item in items:
            target = destination / item.name
            with staged_beside(target) as staged:
                shutil.copy2(item, staged)
            total += target.stat().st_size

        # Only after every new file is in place: drop what the map no longer has.
        for obsolete in previously_owned - new_names:
            with contextlib.suppress(OSError):
                (destination / obsolete).unlink()
        _write_owned(destination, new_names)

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
