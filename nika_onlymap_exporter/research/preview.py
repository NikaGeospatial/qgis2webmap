""" "See exactly what is sent": the reports, pretty-printed, before any is sent.

Built by the same functions that build the real reports, from the project that
is open when it can be, so what the user reads is what would go - not a
description of it. With no project read yet, an illustrative map stands in and
says so.

Also reads the per-version "What's new" excerpt shipped inside the plugin.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.export_ir import (
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    PopupFieldSpec,
    PopupSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
)
from .map_report import LayerFacts, ProjectFacts
from .service import ResearchService

WHATS_NEW_PATH = Path(__file__).resolve().parent.parent / "whats_new.md"

# Where the research section sits inside the excerpt.
RESEARCH_MARKER = "<!-- research -->"

SAMPLE_NOTE = (
    "No project has been read yet, so this is an ILLUSTRATIVE map. Export or "
    "preview once and this shows the report for your own project."
)


def sample_export() -> ExportProject:
    """A made-up two-layer map, used only to illustrate the report's shape."""
    empty = {"type": "FeatureCollection", "features": []}
    return ExportProject(
        title="Riverside site survey",
        layers=(
            ExportLayer(
                layer_id="sample-boundary",
                name="Site boundary",
                geometry_kind=GeometryKind.POLYGON,
                source_kind=SourceKind.FILE,
                feature_count=1,
                geojson=empty,
                popup=PopupSpec(fields=(PopupFieldSpec(name="name"),)),
                renderer=RendererSpec(kind=RendererKind.SINGLE),
            ),
            ExportLayer(
                layer_id="sample-trees",
                name="Tree survey",
                geometry_kind=GeometryKind.POINT,
                source_kind=SourceKind.FILE,
                feature_count=420,
                geojson=empty,
                popup=PopupSpec(
                    fields=tuple(
                        PopupFieldSpec(name=name)
                        for name in ("species", "height_m", "condition", "surveyed")
                    )
                ),
                renderer=RendererSpec(
                    kind=RendererKind.CATEGORIZED, field_name="species"
                ),
            ),
        ),
        extent=Extent(west=-1.30, south=51.74, east=-1.24, north=51.77),
        source_crs="EPSG:27700",
    )


SAMPLE_FACTS = ProjectFacts(
    layers={
        "sample-boundary": LayerFacts(format="gpkg"),
        "sample-trees": LayerFacts(format="gpkg", has_time_field=True),
    }
)


def _pretty(report: object) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def payload_preview_text(
    service: ResearchService,
    export: ExportProject | None,
    facts: ProjectFacts | None = None,
) -> str:
    """Every kind of report this install could send, as the JSON that would go."""
    parts: list[str] = []
    if export is None or not export.exportable_layers:
        parts.append(SAMPLE_NOTE)
        report = service.preview_map_report(sample_export(), facts=SAMPLE_FACTS)
    else:
        report = service.preview_map_report(export, facts=facts)
    parts.append(
        "After an export or publish, once per map and again only when its "
        "layers change:\n" + _pretty(report)
    )
    tally = _pretty(service.preview_tally())
    parts.append(
        "Once a week, with that week's counts (below: this week so far, or "
        "example numbers if nothing has been counted yet):\n" + tally
    )
    contact = service.preview_contact()
    if contact is not None:
        parts.append(
            "Once, on its own and never linked to your maps, because you gave "
            "an email:\n" + _pretty(contact)
        )
    return "\n\n".join(parts)


def read_whats_new(path: Path = WHATS_NEW_PATH) -> tuple[str, str]:
    """The excerpt as (before the research section, after it). Never raises."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ("", "")
    before, _, after = text.partition(RESEARCH_MARKER)
    return (before.strip(), after.strip())
