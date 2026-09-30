"""The `kind: "contact"` report: an email the user typed, and nothing else.

Only sent when the user typed an email in About you *and* chose Share. The
server stores it in its own table and never joins it to map reports; this side
keeps it separate too, by sending it as its own report with the profile at the
top level rather than a `profile` object.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from typing import TypedDict

from .envelope import KIND_CONTACT, SCHEMA_VERSION, Environment
from .profile import Profile

MAX_EMAIL_LENGTH = 254

# The server's pattern, verbatim: plausible rather than RFC 5322-complete. An
# address it would refuse is not stored here either, so a contact is never
# queued only to be rejected.
_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class ContactWire(TypedDict):
    schema: int
    kind: str
    plugin_version: str
    qgis_version: str
    os: str
    email: str
    audience: str | None
    sector: str | None
    use_cases: list[str]
    use_case_other: str | None


def normalise_email(text: str | None) -> str | None:
    """A plausible address, trimmed, or `None`. Not a deliverability check."""
    value = (text or "").strip()
    if not value or len(value) > MAX_EMAIL_LENGTH or not _EMAIL.match(value):
        return None
    return value


def build_contact(email: str, env: Environment, profile: Profile) -> ContactWire:
    answers = profile.to_wire()
    return {
        "schema": SCHEMA_VERSION,
        "kind": KIND_CONTACT,
        "plugin_version": env.plugin_version,
        "qgis_version": env.qgis_version,
        "os": env.os,
        "email": email,
        "audience": answers["audience"],
        "sector": answers["sector"],
        "use_cases": answers["use_cases"],
        "use_case_other": answers["use_case_other"],
    }
