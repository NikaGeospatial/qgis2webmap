"""Everything research keeps on this machine, in one small JSON file.

The choice, the About you answers, the salt, the fingerprints already
reported, each map's `map_id`, the week counters and the outbound queue all
live here, in the QGIS
profile directory (the path is chosen by `ui/research_session.py`; this module
takes it as an argument, which is what lets the tests use `tmp_path`).

**Why a JSON file and not a `QSettings` group.** The queue has to be a file
anyway - fifty reports of up to 64 KB is not what an INI or registry backend is
for - and putting everything beside it means one read, one atomic write and
one thing to delete to forget it all. It also keeps this package free of Qt, so
every rule about what is stored is testable without a QGIS application.

A file that cannot be read or parsed is treated as empty, which means consent
`unset`: a corrupted file can only ever stop collection, never start it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import consent
from .fingerprint import is_map_id, new_map_id, new_salt
from .profile import Profile
from .queue import QueuedReport
from .tally import WeekCounters

STATE_VERSION = 1

# Bounds on what accumulates. Past them the oldest entries go: a fingerprint
# forgotten only means a map may be reported once more, which is harmless.
MAX_SENT_FINGERPRINTS = 1000
MAX_KNOWN_MAPS = 2000
MAX_TALLIED_WEEKS = 120

# The optional email and its `kind: "contact"` report were removed before
# release. A tester's state from a build that had them still may; both are
# dropped on load, and the file is rewritten without them.
RETIRED_KEYS = frozenset({"email", "contact_sent"})
RETIRED_CONTACT_KIND = "contact"


@dataclass
class ResearchState:
    consent: str = consent.UNSET
    salt: str = ""
    # True once About you was answered or skipped, so it is never shown again.
    profile_answered: bool = False
    profile: Profile = field(default_factory=Profile)
    # The plugin version whose What's new was last shown.
    whats_new_seen: str | None = None
    sent_fingerprints: list[str] = field(default_factory=list)
    # Local map key -> the ISO week it was first exported in.
    map_first_week: dict[str, str] = field(default_factory=dict)
    # Local map key -> the random `map_id` its reports carry, oldest first.
    # Keyed like `map_first_week`, so a map that gains a layer keeps its id.
    map_ids: dict[str, str] = field(default_factory=dict)
    weeks: dict[str, WeekCounters] = field(default_factory=dict)
    # Weeks already turned into a tally, so a clock set backwards cannot
    # produce a second report for the same week.
    tallied_weeks: list[str] = field(default_factory=list)
    queue: list[QueuedReport] = field(default_factory=list)

    def ensure_salt(self) -> str:
        if not self.salt:
            self.salt = new_salt()
        return self.salt

    def forget_collected(self) -> None:
        """Drop everything gathered for sending. Kept: the choice and answers."""
        self.queue.clear()
        self.weeks.clear()
        self.tallied_weeks.clear()
        self.sent_fingerprints.clear()
        self.map_first_week.clear()
        self.map_ids.clear()

    def remember_fingerprint(self, fingerprint: str) -> None:
        if fingerprint in self.sent_fingerprints:
            return
        self.sent_fingerprints.append(fingerprint)
        del self.sent_fingerprints[:-MAX_SENT_FINGERPRINTS]

    def remember_map(self, key: str, week: str) -> None:
        if key in self.map_first_week:
            return
        self.map_first_week[key] = week
        if len(self.map_first_week) > MAX_KNOWN_MAPS:
            oldest = sorted(self.map_first_week.items(), key=lambda item: item[1])
            for stale, _ in oldest[: len(self.map_first_week) - MAX_KNOWN_MAPS]:
                del self.map_first_week[stale]

    def map_id_for(self, key: str) -> str:
        """This map's `map_id`, made on first use and reused ever after."""
        existing = self.map_ids.get(key)
        if existing is not None:
            return existing
        map_id = new_map_id()
        self.map_ids[key] = map_id
        # Past the bound the longest-known go first; a map forgotten here only
        # starts a new `map_id` if it is ever reported again.
        while len(self.map_ids) > MAX_KNOWN_MAPS:
            del self.map_ids[next(iter(self.map_ids))]
        return map_id

    def remember_tallied(self, week: str) -> None:
        if week not in self.tallied_weeks:
            self.tallied_weeks.append(week)
            del self.tallied_weeks[:-MAX_TALLIED_WEEKS]

    def to_stored(self) -> dict[str, object]:
        profile = self.profile
        return {
            "version": STATE_VERSION,
            "consent": self.consent,
            "salt": self.salt,
            "profile_answered": self.profile_answered,
            "profile": {
                "audience": profile.audience,
                "sector": profile.sector,
                "use_cases": list(profile.use_cases),
                "use_case_other": profile.use_case_other,
            },
            "whats_new_seen": self.whats_new_seen,
            "sent_fingerprints": list(self.sent_fingerprints),
            "map_first_week": dict(self.map_first_week),
            "map_ids": dict(self.map_ids),
            "weeks": {week: c.to_stored() for week, c in self.weeks.items()},
            "tallied_weeks": list(self.tallied_weeks),
            "queue": [item.to_stored() for item in self.queue],
        }

    @classmethod
    def from_stored(cls, stored: object) -> ResearchState:
        if not isinstance(stored, Mapping):
            return cls()
        profile_raw = stored.get("profile")
        weeks_raw = stored.get("weeks")
        first_raw = stored.get("map_first_week")
        ids_raw = stored.get("map_ids")
        queue_raw = stored.get("queue")
        queued = (
            [QueuedReport.from_stored(item) for item in queue_raw]
            if isinstance(queue_raw, list)
            else []
        )
        return cls(
            consent=consent.normalise(stored.get("consent")),
            salt=_text(stored.get("salt")) or "",
            profile_answered=stored.get("profile_answered") is True,
            profile=(
                Profile.from_stored(profile_raw)
                if isinstance(profile_raw, Mapping)
                else Profile()
            ),
            whats_new_seen=_text(stored.get("whats_new_seen")),
            sent_fingerprints=_texts(stored.get("sent_fingerprints")),
            map_first_week=(
                {
                    str(key): week
                    for key, week in first_raw.items()
                    if isinstance(week, str)
                }
                if isinstance(first_raw, Mapping)
                else {}
            ),
            map_ids=(
                {
                    str(key): map_id
                    for key, map_id in ids_raw.items()
                    if isinstance(map_id, str) and is_map_id(map_id)
                }
                if isinstance(ids_raw, Mapping)
                else {}
            ),
            weeks=(
                {
                    str(week): WeekCounters.from_stored(counters)
                    for week, counters in weeks_raw.items()
                }
                if isinstance(weeks_raw, Mapping)
                else {}
            ),
            tallied_weeks=_texts(stored.get("tallied_weeks")),
            queue=[
                item
                for item in queued
                if item is not None and not _is_retired_contact(item.body)
            ],
        )


def _is_retired_contact(body: str) -> bool:
    """A queued `kind: "contact"` report, from a build that still had the email."""
    try:
        report = json.loads(body)
    except ValueError:
        return False
    return isinstance(report, Mapping) and report.get("kind") == RETIRED_CONTACT_KIND


def holds_retired_contact(stored: object) -> bool:
    """Whether a stored state still has the email, or a contact report queued."""
    if not isinstance(stored, Mapping):
        return False
    if RETIRED_KEYS & stored.keys():
        return True
    queue_raw = stored.get("queue")
    if not isinstance(queue_raw, list):
        return False
    return any(
        item is not None and _is_retired_contact(item.body)
        for item in (QueuedReport.from_stored(raw) for raw in queue_raw)
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _texts(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def load_state(path: Path) -> ResearchState:
    """Read the state. One left with a retired email is rewritten without it."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ResearchState()
    try:
        stored = json.loads(raw)
    except ValueError:
        return ResearchState()
    state = ResearchState.from_stored(stored)
    if holds_retired_contact(stored):
        # Not left on disk until something else happens to save: the address
        # is gone from memory already, and a failed rewrite only means the
        # next save removes it instead.
        with contextlib.suppress(OSError):
            save_state(path, state)
    return state


def save_state(path: Path, state: ResearchState) -> None:
    """Write atomically: a crash mid-write leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state.to_stored(), ensure_ascii=False, indent=1)
    handle, temp_name = tempfile.mkstemp(
        prefix=".research-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
