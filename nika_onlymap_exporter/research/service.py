"""Research, orchestrated: what happens on an export, a choice, a response.

Pure Python. The dialogs and the network call into this and nothing else, so
the rules - above all, that nothing is counted, queued or sent without Share -
are enforced in one place and exercised by the unit tests directly.

Callers in the UI still wrap every call, because research must never be the
reason an export or a publish fails - a full disk or an unwritable profile
directory is an `OSError` here, and a log line there.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from ..core.export_ir import ExportProject
from . import consent
from .contact import ContactWire, build_contact, normalise_email
from .envelope import Environment
from .fingerprint import map_key, structure_fingerprint
from .map_report import MapReportWire, ProjectFacts, build_map_report, encode
from .profile import Profile
from .queue import QueuedReport, enqueue, remove
from .state import ResearchState, load_state, save_state
from .tally import (
    TallyWire,
    WeekCounters,
    build_tally,
    completed_weeks,
    iso_week,
)

# What the server's answer means for a queued report.
SENT = "sent"
ENDED_BY_SERVER = "ended"
REFUSED = "refused"
RETRY = "retry"

# The server has read the report and said no. Sending the same bytes again
# would only repeat that, so these are dropped rather than kept for retry.
_REFUSED_STATUSES = frozenset({400, 413, 415, 422})


def classify_response(status: int | None) -> str:
    """`None` is a transport failure: no HTTP answer at all."""
    if status is None:
        return RETRY
    if 200 <= status < 300:
        return SENT
    if status == 410:
        return ENDED_BY_SERVER
    if status in _REFUSED_STATUSES:
        return REFUSED
    return RETRY


class ResearchService:
    def __init__(
        self,
        path: Path,
        env: Environment,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.path = path
        self.env = env
        self._today = today
        self.state: ResearchState = load_state(path)
        self._apply_hard_stop()

    # ---- Reading --------------------------------------------------------

    @property
    def consent(self) -> str:
        return self.state.consent

    def today(self) -> date:
        return self._today()

    def collecting(self) -> bool:
        return consent.may_collect(self.state.consent, self.today())

    def can_ask(self) -> bool:
        return consent.can_ask(self.state.consent, self.today())

    def needs_profile(self) -> bool:
        return not self.state.profile_answered and self.can_ask()

    def needs_whats_new(self, version: str) -> bool:
        return self.state.whats_new_seen != version

    def pending(self) -> list[QueuedReport]:
        """What may be sent right now - nothing unless the user chose Share."""
        if not self.collecting():
            return []
        return list(self.state.queue)

    # ---- Choices --------------------------------------------------------

    def choose(self, share: bool) -> None:
        """Record Share or Don't share. A no-op once research has ended."""
        if self.state.consent == consent.ENDED:
            return
        if share:
            self.state.consent = consent.SHARE
            self.state.ensure_salt()
            self._queue_contact()
        else:
            self.state.consent = consent.DONT_SHARE
            # Withdrawing drops what was waiting, as well as stopping what is
            # to come: a queued report is one the user no longer agrees to.
            self.state.forget_collected()
        self._save()

    def save_profile(self, profile: Profile, email: str | None) -> None:
        normalised = normalise_email(email)
        if normalised != self.state.email:
            self.state.contact_sent = False
        self.state.profile = profile
        self.state.email = normalised
        self.state.profile_answered = True
        if self.collecting():
            self._queue_contact()
        self._save()

    def skip_profile(self) -> None:
        self.state.profile_answered = True
        self._save()

    def mark_whats_new_seen(self, version: str) -> None:
        self.state.whats_new_seen = version
        self._save()

    # ---- Events ---------------------------------------------------------

    def record_output(
        self,
        export: ExportProject,
        identity: str,
        *,
        facts: ProjectFacts | None = None,
        hosted: bool = False,
        password: bool = False,
    ) -> bool:
        """A successful export or publish. Returns whether a map report was queued."""
        if not self.collecting():
            return False
        self._roll_weeks()
        week = iso_week(self.today())
        counters = self._week(week)
        if hosted:
            counters.publishes += 1
        else:
            counters.exports += 1
        salt = self.state.ensure_salt()
        key = map_key(salt, identity)
        counters.add_map(key)
        self.state.remember_map(key, week)

        queued = False
        fingerprint = structure_fingerprint(salt, identity, export)
        if fingerprint not in self.state.sent_fingerprints:
            report = build_map_report(
                export,
                self.env,
                self.state.profile,
                facts=facts,
                hosted=hosted,
                password=password,
            )
            enqueue(self.state.queue, encode(report).decode("utf-8"))
            self.state.remember_fingerprint(fingerprint)
            queued = True
        self._save()
        return queued

    def record_preview(self) -> None:
        if not self.collecting():
            return
        self._roll_weeks()
        self._week(iso_week(self.today())).previews += 1
        self._save()

    def record_failure(self, failure_class: str) -> None:
        if not self.collecting():
            return
        self._roll_weeks()
        self._week(iso_week(self.today())).add_failure(failure_class)
        self._save()

    def prepare_flush(self) -> list[QueuedReport]:
        """Turn finished weeks into tallies, then return what may be sent."""
        if self.collecting() and self._roll_weeks():
            self._save()
        return self.pending()

    def on_response(self, report_id: str, status: int | None) -> str:
        outcome = classify_response(status)
        if outcome == ENDED_BY_SERVER:
            # The kill switch: research is over, for good.
            self.state.consent = consent.ENDED
            self.state.forget_collected()
        elif outcome in (SENT, REFUSED):
            remove(self.state.queue, report_id)
        self._save()
        return outcome

    # ---- Previews for "See exactly what is sent" -------------------------

    def preview_map_report(
        self,
        export: ExportProject,
        *,
        facts: ProjectFacts | None = None,
        hosted: bool = False,
    ) -> MapReportWire:
        """The report an export of `export` would queue. Changes nothing."""
        return build_map_report(
            export, self.env, self.state.profile, facts=facts, hosted=hosted
        )

    def preview_tally(self) -> TallyWire:
        week = iso_week(self.today())
        counters = self.state.weeks.get(week) or WeekCounters(exports=3, previews=5)
        return build_tally(
            week, counters, self.state.map_first_week, self.env, self.state.profile
        )

    def preview_contact(self) -> ContactWire | None:
        if self.state.email is None:
            return None
        return build_contact(self.state.email, self.env, self.state.profile)

    # ---- Internals ------------------------------------------------------

    def _week(self, week: str) -> WeekCounters:
        return self.state.weeks.setdefault(week, WeekCounters())

    def _roll_weeks(self) -> bool:
        """Queue one tally per finished week, never twice for the same week."""
        rolled = False
        for week in completed_weeks(self.state.weeks, self.today()):
            counters = self.state.weeks.pop(week)
            rolled = True
            if week in self.state.tallied_weeks:
                continue
            tally = build_tally(
                week,
                counters,
                self.state.map_first_week,
                self.env,
                self.state.profile,
            )
            enqueue(self.state.queue, encode(tally).decode("utf-8"))
            self.state.remember_tallied(week)
        return rolled

    def _queue_contact(self) -> None:
        email = self.state.email
        if email is None or self.state.contact_sent or not self.collecting():
            return
        contact = build_contact(email, self.env, self.state.profile)
        enqueue(self.state.queue, encode(contact).decode("utf-8"))
        self.state.contact_sent = True

    def _apply_hard_stop(self) -> None:
        """Past `RESEARCH_ENDS`, anything still waiting is deleted, not sent."""
        if consent.research_over(self.today()) and (
            self.state.queue or self.state.weeks
        ):
            self.state.forget_collected()
            self._save()

    def _save(self) -> None:
        save_state(self.path, self.state)
