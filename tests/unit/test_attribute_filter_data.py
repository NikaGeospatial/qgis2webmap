"""`core.attribute_filter`: the filter panel worked out from exported data.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.attribute_filter import (
    CATEGORY_LIMIT,
    FilterRequest,
    build_filter,
)
from nika_onlymap_exporter.core.export_ir import FilterKind


def _collection(field: str, *values: object) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {field: value}}
            for value in values
        ],
    }


class TestNumbers:
    def test_the_range_is_every_value_and_nulls_are_counted(self) -> None:
        outcome = build_filter(
            _collection("n", 3, None, -1.5, 8),
            FilterRequest("n", "N", FilterKind.NUMBER),
        )
        assert outcome.spec is not None
        assert outcome.spec.value_range == (-1.5, 8.0)
        assert outcome.missing == 1

    def test_a_yes_no_is_not_a_number(self) -> None:
        outcome = build_filter(
            _collection("n", True, False), FilterRequest("n", "N", FilterKind.NUMBER)
        )
        assert outcome.spec is None
        assert "No feature has a value for 'N'" in (outcome.refusal or "")


class TestText:
    def test_the_values_are_sorted_and_kept(self) -> None:
        outcome = build_filter(
            _collection("k", "b", "a", "b", None),
            FilterRequest("k", "Kind", FilterKind.TEXT),
        )
        assert outcome.spec is not None
        assert outcome.spec.categories == ("a", "b")
        assert outcome.missing == 1

    def test_more_than_the_limit_is_refused(self) -> None:
        values = [f"v{i}" for i in range(CATEGORY_LIMIT + 1)]
        outcome = build_filter(
            _collection("k", *values), FilterRequest("k", "Kind", FilterKind.TEXT)
        )
        assert outcome.spec is None
        assert f"{CATEGORY_LIMIT + 1} different values" in (outcome.refusal or "")
        exactly = build_filter(
            _collection("k", *values[:-1]), FilterRequest("k", "K", FilterKind.TEXT)
        )
        assert exactly.spec is not None


class TestDates:
    def test_a_copy_in_milliseconds_is_written_beside_the_field(self) -> None:
        outcome = build_filter(
            _collection(
                "when",
                "1970-01-02",
                "1970-01-01T00:00:01.500",
                "1970-01-01T02:00:00+02:00",
                "1970-01-01T00:00:00Z",
                "not a date",
            ),
            FilterRequest("when", "When", FilterKind.DATETIME),
        )
        assert outcome.spec is not None
        assert outcome.spec.field_name == "when_ms"
        stamps = [f["properties"]["when_ms"] for f in outcome.geojson["features"]]
        assert stamps == [86_400_000, 1500, 0, 0, None]
        assert outcome.missing == 1
        # The field itself is as it was, for the popup.
        assert outcome.geojson["features"][0]["properties"]["when"] == "1970-01-02"

    def test_the_copy_never_overwrites_a_field(self) -> None:
        collection = _collection("when", "1970-01-02")
        collection["features"][0]["properties"]["when_ms"] = "mine"
        outcome = build_filter(collection, FilterRequest("when", "W", FilterKind.DATE))
        assert outcome.spec is not None
        assert outcome.spec.field_name == "when_ms_"
        assert outcome.geojson["features"][0]["properties"]["when_ms"] == "mine"
