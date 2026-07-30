"""Ties writing and exporting together, and writes the recipient's README.

The plugin's single entry point for "produce an artifact at this path". The
dialog and the Processing provider both call this, so there is one export
implementation rather than two that drift.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..core.export_ir import ExportProject, OutputMode
from ..exporters.base import Exporter, ExportOutcome
from ..exporters.folder import FolderExporter
from ..exporters.share_zip import ShareZipExporter
from ..exporters.standalone_html import StandaloneHtmlExporter
from ..writers.onlymap_writer import ArtifactResult, OnlyMapWriter

README_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "exported-map-README.txt"
)

EXPORTERS: dict[OutputMode, type[Exporter]] = {
    OutputMode.STANDALONE_HTML: StandaloneHtmlExporter,
    OutputMode.SHARE_ZIP: ShareZipExporter,
    OutputMode.FOLDER: FolderExporter,
}


def build_readme(
    project: ExportProject, result: ArtifactResult, open_instruction: str
) -> str:
    """The README that travels with a folder or zip.

    qgis2web ships one of these and it is a good idea worth copying: the
    recipient of a folder has no idea which file to open, and a plain text file
    named README is the one thing they will find.
    """
    template = README_TEMPLATE.read_text(encoding="utf-8")
    size_line = (
        f"This map is {result.total_bytes / 1024 / 1024:.1f} MB and contains "
        f"{sum(layer.feature_count for layer in project.layers):,} map features."
    )
    replacements = {
        "@TITLE@": project.title,
        "@UNDERLINE@": "=" * len(project.title),
        "@OPEN_INSTRUCTION@": open_instruction,
        "@SIZE_LINE@": size_line,
        "@GENERATOR@": "-- " + generator_line_first_line(result),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def generator_line_first_line(result: ArtifactResult) -> str:
    return (
        f"QGIS2WebMap by NIKA, OnlyMap runtime {result.runtime_version} "
        f"(sha256 {result.runtime_sha256[:16]})"
    )


def build_artifact(
    project: ExportProject,
    destination: Path,
    mode: OutputMode = OutputMode.STANDALONE_HTML,
    writer: OnlyMapWriter | None = None,
    compress: bool = True,
) -> tuple[ArtifactResult, ExportOutcome]:
    """Write an artifact and place it at `destination`.

    Staged through a temporary directory so a failure part-way leaves nothing
    half-written where the user asked for their map.
    """
    writer = writer or OnlyMapWriter()
    exporter = EXPORTERS[mode]()

    with tempfile.TemporaryDirectory(prefix="qgis2webmap-") as staging:
        staging_path = Path(staging)
        result = writer.write(project, staging_path, mode=mode, compress=compress)

        # Folder and zip tiers carry a README; a single file has nowhere to put
        # one, and its filename already says what it is.
        if mode in (OutputMode.SHARE_ZIP, OutputMode.FOLDER):
            instruction = (
                "Extract this zip, then open index.html."
                if mode is OutputMode.SHARE_ZIP
                else "Open index.html in this folder."
            )
            (staging_path / "README.txt").write_text(
                build_readme(project, result, instruction), encoding="utf-8"
            )

        outcome = exporter.export(result, destination)

    return result, outcome
