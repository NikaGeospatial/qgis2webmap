"""Everything research keeps on this machine, in one small JSON file.

The choice, the About you answers, the salt, the fingerprints already
reported, the week counters and the outbound queue all live here, in the QGIS
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
from .fingerprint import new_salt
from .profile import Profile
from .queue import QueuedReport
from .tally import WeekCounters

STATE_VERSION = 1

# Bounds on what accumulates. Past them the oldest entries go: a fingerprint
# forgotten only means a map may be reported once more, which is harmless.
MAX_SENT_FINGERPRINTS = 1000
MAX_KNOWN_MAPS = 2000
MAX_TALLIED_WEEKS = 120


@dataclass
class ResearchState:
    consent: str = consent.UNSET
    salt: str = ""
    # True once About you was answered or skipped, so it is never shown again.
    profile_answered: bool = False
    profile: Profile = field(default_factory=Profile)
    email: str | None = None
    contact_sent: bool = False
    # The plugin version whose What's new was last shown.
    whats_new_seen: str | None = None
    sent_fingerprints: list[str] = field(default_factory=list)
    # Local map key -> the ISO week it was first exported in.
    map_first_week: dict[str, str] = field(default_factory=dict)
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
        self.contact_sent = False

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
            "email": self.email,
            "contact_sent": self.contact_sent,
            "whats_new_seen": self.whats_new_seen,
            "sent_fingerprints": list(self.sent_fingerprints),
            "map_first_week": dict(self.map_first_week),
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
            email=_text(stored.get("email")),
            contact_sent=stored.get("contact_sent") is True,
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
            weeks=(
                {
                    str(week): WeekCounters.from_stored(counters)
                    for week, counters in weeks_raw.items()
                }
                if isinstance(weeks_raw, Mapping)
                else {}
            ),
            tallied_weeks=_texts(stored.get("tallied_weeks")),
            queue=[item for item in queued if item is not None],
        )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _texts(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def load_state(path: Path) -> ResearchState:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ResearchState()
    try:
        return ResearchState.from_stored(json.loads(raw))
    except ValueError:
        return ResearchState()


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
