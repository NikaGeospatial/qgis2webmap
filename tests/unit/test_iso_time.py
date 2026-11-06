"""Dates and times read as QGIS 3.44 on Python 3.10 reads them.

Python 3.10's `datetime.fromisoformat` refuses a `Z`, a zone written
`+hhmm` and a fraction of a second that is not 3 or 6 digits; this box runs a
later Python, which takes all three. So the readers are run against a
stand-in with 3.10's rules, or against 3.10 itself when that is what runs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import sys
from datetime import datetime

import pytest

from nika_onlymap_exporter.core import iso_time
from nika_onlymap_exporter.core.attribute_filter import FilterRequest, build_filter
from nika_onlymap_exporter.core.export_ir import FilterKind
from nika_onlymap_exporter.core.map_services import (
    parse_instant,
    parse_time_dimension,
)

# What Python 3.10's `fromisoformat` takes: `isoformat`'s own output.
_PYTHON_310 = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"(?:.\d{2}(?::\d{2}(?::\d{2}(?:\.(?:\d{3}|\d{6}))?)?)?"
    r"(?:[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d{6})?)?)?)?"
)


class _Python310Datetime(datetime):
    @classmethod
    def fromisoformat(cls, date_string: str) -> datetime:  # type: ignore[override]
        if not _PYTHON_310.fullmatch(date_string):
            raise ValueError(f"Invalid isoformat string: {date_string!r}")
        return datetime.fromisoformat(date_string)


@pytest.fixture(autouse=True)
def python_310(monkeypatch):
    if sys.version_info >= (3, 11):
        monkeypatch.setattr(iso_time, "datetime", _Python310Datetime)


def test_a_wms_t_interval_in_utc_steps_through_its_instants() -> None:
    assert parse_time_dimension("2026-10-01T00:00:00Z/2026-10-03T00:00:00Z/P1D") == [
        "2026-10-01T00:00:00Z",
        "2026-10-02T00:00:00Z",
        "2026-10-03T00:00:00Z",
    ]


@pytest.mark.parametrize(
    "written",
    [
        "2026-10-20T00:00:00Z",
        "2026-10-20T00:00:00.000z",
        "2026-10-20T02:00:00+0200",
        "2026-10-20T00:00:00.0Z",
    ],
)
def test_a_wms_t_instant_is_read_in_utc(written: str) -> None:
    found = parse_instant(written)
    assert found is not None
    assert found.isoformat() == "2026-10-20T00:00:00+00:00"


def test_a_date_filter_reads_every_way_a_time_is_written() -> None:
    outcome = build_filter(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"when": value}}
                for value in (
                    "1970-01-01T00:00:01.5Z",
                    "1970-01-01T02:00:00+0200",
                    "1970-01-01T00:00:02Z",
                )
            ],
        },
        FilterRequest("when", "When", FilterKind.DATETIME),
    )
    stamps = [f["properties"]["when_ms"] for f in outcome.geojson["features"]]
    assert stamps == [1500, 0, 2000]
    assert outcome.missing == 0
