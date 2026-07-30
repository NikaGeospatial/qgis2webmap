"""A zip of the artifact.

Two reasons to choose this over a bare `.html`, and the second is easy to miss:

1. The data is too large for one comfortable file.
2. **Deliverability.** Many mail providers and most corporate filters quarantine
   or strip `.html` attachments, because HTML is a phishing vector. A zip passes
   filters that a bare `.html` does not - so this tier is worth offering even
   when size does not demand it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..core.export_ir import OutputMode
from ..writers.onlymap_writer import ArtifactResult
from .base import ExportOutcome


class ShareZipExporter:
    @property
    def mode(self) -> OutputMode:
        return OutputMode.SHARE_ZIP

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome:
        """`destination` is the target `.zip` path."""
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)

        source_dir = result.entry_path.parent
        stem = destination.stem

        # Everything sits inside one named folder, so extracting never scatters
        # files across the recipient's Downloads directory.
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(source_dir.iterdir()):
                if item.is_file():
                    archive.write(item, f"{stem}/{item.name}")

        return ExportOutcome(
            path=destination,
            mode=self.mode,
            size_bytes=destination.stat().st_size,
            open_instruction=(
                f"Extract it, then open index.html inside the {stem} folder."
            ),
        )
