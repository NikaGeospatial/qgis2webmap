"""One file, opened by double-click. The default and the product promise.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.export_ir import OutputMode
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome


class StandaloneHtmlExporter:
    """Copies the single entry file to wherever the user asked for it."""

    @property
    def mode(self) -> OutputMode:
        return OutputMode.STANDALONE_HTML

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        """`destination` is the target `.html` path, not a directory."""
        if destination.suffix.lower() != ".html":
            destination = destination.with_suffix(".html")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.entry_path, destination)

        return ExportOutcome(
            path=destination,
            mode=self.mode,
            size_bytes=destination.stat().st_size,
            open_instruction=(
                "Double-click it to open in a browser. No internet connection "
                "and no other files are needed."
            ),
        )
