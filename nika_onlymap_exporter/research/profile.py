"""The "About you" answers: who the maps are for, the sector, what they do.

Stored locally and attached to each report only after the user chose Share. No
identifier links a person's reports together (a map's own reports share a
random `map_id`, which says nothing about who made it); the profile rides along
on each report so answers can be joined to what people build without an ID.

The value lists are the spec's, character for character - the server rejects
anything else, and a mismatch here would silently drop every report.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from .scrub import scrub_other

AUDIENCES: tuple[str, ...] = ("client", "public", "team", "self")

SECTORS: tuple[str, ...] = (
    "agriculture",
    "construction_engineering",
    "energy_utilities",
    "environment_conservation",
    "forestry",
    "government_planning",
    "mining_resources",
    "real_estate_land",
    "transport_logistics",
    "water",
    "disaster_emergency",
    "defence_security",
    "research_education",
    "ngo_development",
    "media_journalism",
    "telecom",
    "insurance_finance",
    "hobby_personal",
    "other",
)

USE_CASES: tuple[str, ...] = (
    "site_design",
    "monitoring",
    "inventory",
    "assessment",
    "reporting",
    "emergency_response",
    "navigation_fieldwork",
    "other",
)

# What the dialog shows for each value. Kept beside the values so a value can
# never be added without a label, and the label never travels on the wire.
AUDIENCE_LABELS: dict[str, str] = {
    "client": "Clients",
    "public": "The public",
    "team": "My team or organisation",
    "self": "Just me",
}

SECTOR_LABELS: dict[str, str] = {
    "agriculture": "Agriculture",
    "construction_engineering": "Construction and engineering",
    "energy_utilities": "Energy and utilities",
    "environment_conservation": "Environment and conservation",
    "forestry": "Forestry",
    "government_planning": "Government and planning",
    "mining_resources": "Mining and natural resources",
    "real_estate_land": "Real estate and land",
    "transport_logistics": "Transport and logistics",
    "water": "Water",
    "disaster_emergency": "Disaster and emergency management",
    "defence_security": "Defence and security",
    "research_education": "Research and education",
    "ngo_development": "NGO and development",
    "media_journalism": "Media and journalism",
    "telecom": "Telecoms",
    "insurance_finance": "Insurance and finance",
    "hobby_personal": "Hobby or personal",
    "other": "Something else",
}

USE_CASE_LABELS: dict[str, str] = {
    "site_design": "Site design",
    "monitoring": "Monitoring change over time",
    "inventory": "Inventory of assets or features",
    "assessment": "Assessment or analysis",
    "reporting": "Reporting",
    "emergency_response": "Emergency response",
    "navigation_fieldwork": "Navigation or fieldwork",
    "other": "Something else",
}


class ProfileWire(TypedDict):
    """The `profile` object on the wire."""

    audience: str | None
    sector: str | None
    use_cases: list[str]
    use_case_other: str | None


@dataclass(frozen=True)
class Profile:
    """The answers, validated: anything outside the lists is dropped."""

    audience: str | None = None
    sector: str | None = None
    use_cases: tuple[str, ...] = field(default_factory=tuple)
    use_case_other: str | None = None

    @classmethod
    def build(
        cls,
        audience: str | None,
        sector: str | None,
        use_cases: Iterable[str],
        use_case_other: str | None,
    ) -> Profile:
        """Normalise raw answers into a profile the server will accept.

        Unknown values are dropped rather than refused: this runs on answers
        read back from a local file, and a hand-edited or older file should
        cost its bad values, not the whole profile.
        """
        chosen = set(use_cases)
        other = scrub_other(use_case_other) or None
        return cls(
            audience=audience if audience in AUDIENCES else None,
            sector=sector if sector in SECTORS else None,
            # Spec order, so the same answers always serialise the same way.
            use_cases=tuple(value for value in USE_CASES if value in chosen),
            use_case_other=other,
        )

    @classmethod
    def from_stored(cls, stored: Mapping[str, object]) -> Profile:
        raw_cases = stored.get("use_cases")
        cases = (
            [value for value in raw_cases if isinstance(value, str)]
            if isinstance(raw_cases, list)
            else []
        )
        return cls.build(
            _text_or_none(stored.get("audience")),
            _text_or_none(stored.get("sector")),
            cases,
            _text_or_none(stored.get("use_case_other")),
        )

    def to_wire(self) -> ProfileWire:
        return {
            "audience": self.audience,
            "sector": self.sector,
            "use_cases": list(self.use_cases),
            "use_case_other": self.use_case_other,
        }


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
