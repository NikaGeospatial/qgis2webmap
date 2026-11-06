"""A layer's filter panel, worked out from the data the layer exports.

The dialog's per-layer "Filter by" names a field; the web map then carries the
runtime's own filter panel for it (`<om-widget type="filter">`) and the layer
the filter it drives (`filter-field`/`filter-range`, or `filter-category` and
its keep-list). A number or a date is a range slider over every feature's
value; text is a tick box per value, all ticked, so the map opens showing
what QGIS shows.

Pure, like `declutter`: it reads only the GeoJSON the layer exports, so the
slider's ends and the tick boxes are exactly the values that travel, and it is
unit-tested without QGIS.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from .export_ir import FilterKind, LayerFilterSpec
from .iso_time import from_iso

# Past this many values a list of tick boxes is a scroll through text rather
# than a filter, and every value would be written into the page twice - once
# as the layer's keep-list, once as a box. A field with more is refused, and
# the Fidelity tab says so, rather than given a panel nobody can use.
CATEGORY_LIMIT = 50


@dataclass(frozen=True)
class FilterRequest:
    """The field a layer is to be filtered by, as the reader found it."""

    field_name: str
    # The field's alias, or its name when it has none.
    title: str
    kind: FilterKind


@dataclass(frozen=True)
class FilterOutcome:
    """What `build_filter` made of a request.

    `spec` is `None` when the field cannot be filtered by, and `refusal` then
    says why, in a sentence for the Fidelity tab. `missing` counts the
    features with no value to filter on.
    """

    geojson: dict[str, Any]
    spec: LayerFilterSpec | None
    refusal: str | None = None
    missing: int = 0


def build_filter(geojson: dict[str, Any], request: FilterRequest) -> FilterOutcome:
    """The filter `request` asks for over `geojson`, and the data it needs.

    A date's slider filters on a copy of it in milliseconds since 1970 (UTC),
    the only form the runtime's slider reads; the copy is written beside the
    field under a name no field uses, and the field itself is left as it was
    for the popup. A date without a time zone is read as UTC, and the panel
    labels its ends in UTC, so it shows the date and time QGIS shows.
    """
    features = list(geojson.get("features") or ())
    values = [(f.get("properties") or {}).get(request.field_name) for f in features]

    if request.kind is FilterKind.TEXT:
        texts = [value for value in values if isinstance(value, str)]
        categories = tuple(sorted(set(texts)))
        if not categories:
            return _refused(geojson, request)
        if len(categories) > CATEGORY_LIMIT:
            return FilterOutcome(
                geojson,
                None,
                f"'{request.title}' has {len(categories):,} different values, more "
                f"than the {CATEGORY_LIMIT} tick boxes a filter panel can usefully "
                "list, so the map has no filter for it. Choose a field with fewer "
                "values, or a number or date field.",
            )
        spec = LayerFilterSpec(
            request.field_name, request.title, request.kind, categories=categories
        )
        return FilterOutcome(geojson, spec, missing=len(values) - len(texts))

    if request.kind is FilterKind.NUMBER:
        numbers = [_number(value) for value in values]
        found = [n for n in numbers if n is not None]
        if not found:
            return _refused(geojson, request)
        spec = LayerFilterSpec(
            request.field_name,
            request.title,
            request.kind,
            value_range=(min(found), max(found)),
        )
        return FilterOutcome(geojson, spec, missing=len(numbers) - len(found))

    times = [_epoch_ms(value) for value in values]
    found = [t for t in times if t is not None]
    if not found:
        return _refused(geojson, request)
    taken = {name for f in features for name in (f.get("properties") or {})}
    stem = re.sub(r"\W+", "_", request.field_name, flags=re.ASCII).strip("_")
    property_name = f"{stem or 'field'}_ms"
    while property_name in taken:
        property_name += "_"
    stamped = {
        **geojson,
        "features": [
            {
                **feature,
                "properties": {
                    **(feature.get("properties") or {}),
                    property_name: time,
                },
            }
            for feature, time in zip(features, times, strict=True)
        ],
    }
    spec = LayerFilterSpec(
        property_name,
        request.title,
        request.kind,
        value_range=(float(min(found)), float(max(found))),
    )
    return FilterOutcome(stamped, spec, missing=len(times) - len(found))


def _refused(geojson: dict[str, Any], request: FilterRequest) -> FilterOutcome:
    return FilterOutcome(
        geojson,
        None,
        f"No feature has a value for '{request.title}', so there is nothing to "
        "filter by and the map has no filter for it.",
    )


def _number(value: object) -> float | None:
    # A bool is an int to Python, and is not a number to filter by.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _epoch_ms(value: object) -> int | None:
    """A date as QGIS writes it to GeoJSON, in milliseconds since 1970 (UTC).

    `2024-03-05` for a date, `2024-03-05T10:30:00.000` for a date and time,
    perhaps with a zone; `None` for NULL or anything else.
    """
    if not isinstance(value, str) or not value:
        return None
    moment = from_iso(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return round(moment.timestamp() * 1000)
