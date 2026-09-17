"""Reading an identity out of `/me`, and saying it in one line.

The fixture below is the real `MeResponse` shape from the control plane's
shared contracts, trimmed to the branches this plugin reads plus enough of the
rest to prove the extra keys are ignored rather than tripped over. If the server
renames `plan.label` or moves `user.email`, this file is what fails.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.account import (
    MAX_LABEL_CHARS,
    AccountIdentity,
    elide_middle,
    parse_me_response,
)


def me_response(**overrides: object) -> dict[str, object]:
    """A realistic `/me` body. Keys this plugin never reads are kept on purpose."""
    payload: dict[str, object] = {
        "appId": "nika",
        "user": {
            "id": "user-1",
            "email": "mapper@example.org",
            "name": "Sam Mapper",
            "image": None,
        },
        "org": {
            "id": "org-1",
            "name": "Coastal Research",
            "workspaceType": "personal",
            "role": "owner",
            "stripeCustomerId": None,
        },
        "developerMode": False,
        "plan": {"id": "free", "label": "Free", "monthlyPriceUsd": 0},
        "upgradePlan": {"id": "pro", "label": "Pro", "monthlyPriceUsd": 20},
        "platformUrl": "https://app.example.org",
        "wallet": {"balanceCents": 0},
        "billing": {"mode": "stripe"},
        "onboarding": {"complete": True},
        "isPlatformAdmin": False,
    }
    payload.update(overrides)
    return payload


class TestReadingTheServersAnswer:
    def test_it_takes_the_four_fields_it_uses(self) -> None:
        identity = parse_me_response(me_response())

        assert identity.email == "mapper@example.org"
        assert identity.display_name == "Sam Mapper"
        assert identity.org_name == "Coastal Research"
        assert identity.plan_label == "Free"

    def test_the_plan_label_is_read_not_the_slug(self) -> None:
        """`plan.id` is a slug; `plan.label` is what a person should be shown."""
        identity = parse_me_response(
            me_response(plan={"id": "team_annual", "label": "Team"})
        )

        assert identity.plan_label == "Team"

    def test_a_missing_section_costs_only_that_section(self) -> None:
        """A server that stops sending `org` must not cost the user the button."""
        payload = me_response()
        del payload["org"]

        identity = parse_me_response(payload)

        assert identity.email == "mapper@example.org"
        assert identity.org_name == ""
        assert identity.is_known

    def test_a_null_display_name_is_not_the_string_none(self) -> None:
        """`user.name` is nullable in the contract."""
        identity = parse_me_response(
            me_response(user={"id": "u", "email": "a@b.co", "name": None})
        )

        assert identity.display_name == ""
        assert identity.email == "a@b.co"

    def test_whitespace_is_stripped(self) -> None:
        identity = parse_me_response(
            me_response(user={"email": "  spaced@example.org \n", "name": " Sam "})
        )

        assert identity.email == "spaced@example.org"
        assert identity.display_name == "Sam"

    def test_nonsense_is_an_empty_identity_not_an_exception(self) -> None:
        """A failed decode must leave the dialog usable, not raise inside it."""
        for payload in (None, [], "signed in", 42, {"user": "not a mapping"}):
            identity = parse_me_response(payload)

            assert identity == AccountIdentity(), payload
            assert not identity.is_known


class TestWhatTheButtonSays:
    def test_the_address_wins_over_the_name(self) -> None:
        """Two accounts can share a display name; the point is telling them apart."""
        identity = AccountIdentity(email="a@b.co", display_name="Sam")

        assert identity.label == "a@b.co"

    def test_the_name_is_the_fallback_when_there_is_no_address(self) -> None:
        identity = AccountIdentity(display_name="Sam Mapper")

        assert identity.label == "Sam Mapper"

    def test_a_long_address_keeps_its_domain(self) -> None:
        """The domain separates a work account from a personal one."""
        identity = AccountIdentity(
            email="firstname.lastname@some-department.example.ac.uk"
        )

        assert len(identity.label) <= MAX_LABEL_CHARS
        assert identity.label.startswith("firstname")
        assert identity.label.endswith("example.ac.uk")

    def test_a_plan_alone_does_not_count_as_knowing_who_you_are(self) -> None:
        assert not AccountIdentity(plan_label="Free").is_known

    def test_the_header_second_line_joins_plan_and_organisation(self) -> None:
        identity = AccountIdentity(
            email="a@b.co", plan_label="Pro", org_name="Coastal Research"
        )

        assert identity.plan_line == "Pro · Coastal Research"

    def test_an_unknown_plan_raises_no_subject(self) -> None:
        """Better to say nothing than to render "Unknown plan"."""
        assert AccountIdentity(email="a@b.co").plan_line == ""

    def test_an_organisation_alone_still_shows(self) -> None:
        identity = AccountIdentity(email="a@b.co", org_name="Coastal Research")

        assert identity.plan_line == "Coastal Research"


class TestElision:
    def test_it_never_exceeds_the_limit_it_was_given(self) -> None:
        for length in range(0, 60):
            assert len(elide_middle("x" * length, 10)) <= 10, length

    def test_a_short_string_is_left_alone(self) -> None:
        assert elide_middle("a@b.co", 28) == "a@b.co"

    def test_it_cuts_the_middle_not_the_tail(self) -> None:
        assert elide_middle("abcdefghij", 5) == "ab…ij"

    def test_an_absurd_limit_degrades_rather_than_crashing(self) -> None:
        assert elide_middle("abcdef", 1) == "abcdef"
        assert elide_middle("abcdef", 0) == "abcdef"
