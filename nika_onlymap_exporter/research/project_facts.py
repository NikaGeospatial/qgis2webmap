"""The few facts a map report needs that the export model does not carry.

The layer's *format* (a file extension or a provider key), whether it has a
date or time field, and whether the project's time settings are in use. All of
it is layer metadata that QGIS already holds in memory: no feature is read, and
the source string is reduced to an allowlisted format token on the spot - the
path itself is never kept.

`collect_facts` needs PyQGIS and imports it inside the function, so this module
stays importable - and `format_from_source` testable - without QGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .map_report import LayerFacts, ProjectFacts, normalise_format

if TYPE_CHECKING:  # pragma: no cover - typing only
    from qgis.core import QgsProject

_PROVIDER_FORMATS = {
    "delimitedtext": "csv",
    "gpx": "gpx",
    "spatialite": "spatialite",
    "memory": "memory",
    "postgres": "postgres",
    "postgresraster": "postgres",
    "mssql": "mssql",
    "oracle": "oracle",
    "wfs": "wfs",
    "oapif": "wfs",
    "arcgisfeatureserver": "arcgis",
    "arcgismapserver": "arcgis",
}

_TIME_TYPE_NAMES = frozenset({"date", "time", "datetime", "timestamp"})


def format_from_source(provider: str, source: str) -> str:
    """An allowlisted format token for a layer, from its provider and source."""
    key = (provider or "").strip().lower()
    if key in _PROVIDER_FORMATS:
        return _PROVIDER_FORMATS[key]
    if key == "wms":
        lowered = (source or "").lower()
        if "type=xyz" in lowered:
            return "xyz"
        if "tilematrixset=" in lowered:
            return "wmts"
        return "wms"
    if key in ("ogr", "gdal"):
        path = (source or "").split("|", 1)[0]
        name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
        return normalise_format(name.rsplit(".", 1)[-1] if "." in name else "")
    return "other"


def _has_time_field(layer: object) -> bool:
    fields = getattr(layer, "fields", None)
    if fields is None:
        return False
    for field in fields():
        is_date_or_time = getattr(field, "isDateOrTime", None)
        if callable(is_date_or_time) and is_date_or_time():
            return True
        if (field.typeName() or "").lower() in _TIME_TYPE_NAMES:
            return True
    return False


def _temporal_active(layer: object) -> bool:
    properties = getattr(layer, "temporalProperties", None)
    if properties is None:
        return False
    try:
        return bool(properties().isActive())
    except (AttributeError, RuntimeError, TypeError):
        return False


def _project_time_set(project: QgsProject) -> bool:
    """Whether Project Properties -> Temporal has a time range set."""
    try:
        return not project.timeSettings().temporalRange().isInfinite()
    except (AttributeError, RuntimeError, TypeError):
        return False


def collect_facts(project: QgsProject, layer_ids: Iterable[str]) -> ProjectFacts:
    """Read the facts for `layer_ids` from a `QgsProject`. GUI thread only.

    `temporal` is on when the project has a time range set, or any exported
    layer has its temporal properties switched on - either is the project
    being set up to show change over time.
    """
    facts: dict[str, LayerFacts] = {}
    temporal = _project_time_set(project)
    for layer_id in layer_ids:
        layer = project.mapLayer(layer_id)
        if layer is None:
            continue
        try:
            provider = layer.providerType() or ""
            source = layer.source() or ""
            facts[layer_id] = LayerFacts(
                format=format_from_source(provider, source),
                has_time_field=_has_time_field(layer),
            )
            temporal = temporal or _temporal_active(layer)
        except (AttributeError, RuntimeError, TypeError):
            continue
    return ProjectFacts(layers=facts, temporal=temporal)
