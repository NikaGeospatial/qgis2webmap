"""What an export needs, and whether it can be made portable.

Runs **before** anything is written. The point is to answer "will this artifact
actually work for the person who receives it?" while there is still time to say
no, rather than discovering the answer when they open a blank page.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..core.export_ir import (
    AssetDisposition,
    ExportProject,
    OutputMode,
)
from ..core.fidelity_report import FidelityReportBuilder

# Past this, a single HTML file stops being a practical thing to email: Gmail
# rejects attachments over 25 MB, and many corporate filters are stricter.
SINGLE_FILE_WARN_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ScanResult:
    """What the export will contain and whether it can proceed."""

    data_bytes: int
    remote_dependencies: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    credentials_detected: tuple[str, ...] = ()

    @property
    def can_export(self) -> bool:
        return not self.blocking_reasons

    @property
    def is_offline(self) -> bool:
        return not self.remote_dependencies

    def snapshot(self) -> dict[str, Any]:
        return {
            "dataBytes": self.data_bytes,
            "remoteDependencies": list(self.remote_dependencies),
            "blockingReasons": list(self.blocking_reasons),
            "credentialsDetected": list(self.credentials_detected),
        }


def measure_data_bytes(project: ExportProject) -> int:
    """Serialised size of all layer data, as it will appear in the artifact."""
    total = 0
    for layer in project.exportable_layers:
        if layer.geojson is not None:
            total += len(
                json.dumps(layer.geojson, separators=(",", ":")).encode("utf-8")
            )
    return total


def scan(
    project: ExportProject,
    report: FidelityReportBuilder,
    mode: OutputMode = OutputMode.STANDALONE_HTML,
) -> ScanResult:
    """Classify every dependency and decide whether the export can proceed."""
    remote: list[str] = []
    blocking: list[str] = []
    credentials: list[str] = []

    for layer in project.exportable_layers:
        for dependency in layer.dependencies:
            if dependency.credentials_detected:
                credentials.append(layer.name)
                # The credential itself never enters the model, so there is
                # nothing to leak - but the user should know their source needed
                # one, because the recipient will not have it.
                report.preserved(
                    f"Data source of '{layer.name}'",
                    "The source needed a username or password. The features are "
                    "embedded in the map, so the recipient needs no credentials "
                    "and none are written into the file.",
                    layer.layer_id,
                )

            if dependency.disposition is AssetDisposition.REMOTE:
                remote.append(dependency.identifier)
                report.approximated(
                    f"Data source of '{layer.name}'",
                    f"'{dependency.identifier}' stays a live reference, so the "
                    "map needs an internet connection to draw this layer.",
                    layer.layer_id,
                )

            if dependency.disposition is AssetDisposition.BLOCKING:
                reason = dependency.note or f"'{dependency.identifier}' is missing."
                blocking.append(reason)
                report.blocked(f"Data source of '{layer.name}'", reason, layer.layer_id)

    if not project.exportable_layers:
        blocking.append(
            "There are no layers to export. Add a vector layer with features."
        )

    data_bytes = measure_data_bytes(project)

    if mode is OutputMode.STANDALONE_HTML and data_bytes > SINGLE_FILE_WARN_BYTES:
        megabytes = data_bytes / 1024 / 1024
        report.approximated(
            "Artifact size",
            f"The layer data alone is {megabytes:.0f} MB, so a single HTML file "
            "will be awkward to email - most services reject attachments over "
            "25 MB. Share ZIP is a better fit for a map this size.",
        )

    return ScanResult(
        data_bytes=data_bytes,
        remote_dependencies=tuple(remote),
        blocking_reasons=tuple(blocking),
        credentials_detected=tuple(dict.fromkeys(credentials)),
    )
