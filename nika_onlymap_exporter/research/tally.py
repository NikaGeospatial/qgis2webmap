"""The weekly tally: counted locally, sent at most once per ISO week.

A week's counters are reported only once that week is over, at the next dialog
open or export, and never twice. The distinct and recurring map counts come
from local map keys (see `fingerprint.map_key`), which are not sent.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TypedDict

from .envelope import KIND_TALLY, SCHEMA_VERSION, Environment
from .profile import Profile, ProfileWire

READ_ERROR = "read_error"
BLOCKED = "blocked"
WRITE_ERROR = "write_error"
RUNTIME_DOWNLOAD = "runtime_download"
PUBLISH_ERROR = "publish_error"
OTHER = "other"

FAILURE_CLASSES = (
    READ_ERROR,
    BLOCKED,
    WRITE_ERROR,
    RUNTIME_DOWNLOAD,
    PUBLISH_ERROR,
    OTHER,
)


def iso_week(day: date) -> str:
    """`YYYY-Www`, using the ISO year - 2027-01-01 is in `2026-W53`."""
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


@dataclass
class WeekCounters:
    exports: int = 0
    previews: int = 0
    publishes: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    # Local map keys seen this week. Never sent; only their count is.
    maps: list[str] = field(default_factory=list)

    def add_failure(self, failure_class: str) -> None:
        name = failure_class if failure_class in FAILURE_CLASSES else OTHER
        self.failures[name] = self.failures.get(name, 0) + 1

    def add_map(self, key: str) -> None:
        if key not in self.maps:
            self.maps.append(key)

    def to_stored(self) -> dict[str, object]:
        return {
            "exports": self.exports,
            "previews": self.previews,
            "publishes": self.publishes,
            "failures": dict(self.failures),
            "maps": list(self.maps),
        }

    @classmethod
    def from_stored(cls, stored: object) -> WeekCounters:
        if not isinstance(stored, Mapping):
            return cls()
        failures_raw = stored.get("failures")
        failures = (
            {
                str(name): count
                for name, count in failures_raw.items()
                if name in FAILURE_CLASSES and _is_count(count)
            }
            if isinstance(failures_raw, Mapping)
            else {}
        )
        maps_raw = stored.get("maps")
        maps = (
            [key for key in maps_raw if isinstance(key, str)]
            if isinstance(maps_raw, list)
            else []
        )
        return cls(
            exports=_count(stored.get("exports")),
            previews=_count(stored.get("previews")),
            publishes=_count(stored.get("publishes")),
            failures=failures,
            maps=maps,
        )


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _count(value: object) -> int:
    return value if isinstance(value, int) and _is_count(value) else 0


class TallyWire(TypedDict):
    schema: int
    kind: str
    plugin_version: str
    qgis_version: str
    os: str
    profile: ProfileWire
    week: str
    exports: int
    previews: int
    publishes: int
    distinct_maps: int
    failures: dict[str, int]
    recurring_maps: int


def recurring_count(
    week: str, counters: WeekCounters, first_week: Mapping[str, str]
) -> int:
    """Maps exported this week that were also exported in an earlier week.

    "Exported in two or more distinct weeks", counted as of the week being
    reported: a map is recurring once its first week is before this one.
    """
    return sum(1 for key in counters.maps if first_week.get(key, week) < week)


def build_tally(
    week: str,
    counters: WeekCounters,
    first_week: Mapping[str, str],
    env: Environment,
    profile: Profile,
) -> TallyWire:
    return {
        "schema": SCHEMA_VERSION,
        "kind": KIND_TALLY,
        "plugin_version": env.plugin_version,
        "qgis_version": env.qgis_version,
        "os": env.os,
        "profile": profile.to_wire(),
        "week": week,
        "exports": counters.exports,
        "previews": counters.previews,
        "publishes": counters.publishes,
        "distinct_maps": len(counters.maps),
        "failures": {name: counters.failures.get(name, 0) for name in FAILURE_CLASSES},
        "recurring_maps": recurring_count(week, counters, first_week),
    }


def completed_weeks(weeks: Mapping[str, WeekCounters], today: date) -> list[str]:
    """Weeks with counters that are over, oldest first."""
    current = iso_week(today)
    return sorted(week for week in weeks if week < current)
