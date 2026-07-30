"""Where a finished artifact goes.

The split issue #29 asks for, and the one structural idea qgis2web genuinely gets
right: **writers build the artifact, exporters decide where it lands.** Adding a
destination - hosting, FTP, a cloud bucket - touches no map-generation code.

An exporter must never reinterpret the project. It receives an `ArtifactResult`
that is already correct and moves bytes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.export_ir import OutputMode
from ..writers.onlymap_writer import ArtifactResult


@dataclass(frozen=True)
class ExportOutcome:
    """Where the artifact ended up, and what to tell the user about it."""

    path: Path
    mode: OutputMode
    size_bytes: int
    open_instruction: str

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1024 / 1024

    def summary(self) -> str:
        return f"{self.path.name} - {self.size_mb:.1f} MB. {self.open_instruction}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "path": self.path.name,
            "mode": self.mode.value,
            "sizeBytes": self.size_bytes,
        }


class Exporter(Protocol):
    """Takes a built artifact somewhere useful."""

    @property
    def mode(self) -> OutputMode: ...

    def export(self, result: ArtifactResult, destination: Path) -> ExportOutcome: ...
