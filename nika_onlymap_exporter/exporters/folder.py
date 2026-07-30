"""A folder containing the artifact.

Mostly a staging step for the ZIP exporter, and useful on its own when a user
wants to publish the result to a web server themselves.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.export_ir import OutputMode
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome


class FolderExporter:
    @property
    def mode(self) -> OutputMode:
        return OutputMode.FOLDER

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        destination.mkdir(parents=True, exist_ok=True)
        source_dir = result.entry_path.parent

        total = 0
        for item in source_dir.iterdir():
            if item.is_file():
                target = destination / item.name
                shutil.copy2(item, target)
                total += target.stat().st_size

        return ExportOutcome(
            path=destination,
            mode=self.mode,
            size_bytes=total,
            open_instruction=(
                "Open index.html inside this folder. Keep the folder's contents "
                "together."
            ),
        )
