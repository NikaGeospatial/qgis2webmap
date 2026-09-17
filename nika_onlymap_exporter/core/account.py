"""Who the plugin is signed in to NIKA as, and how to say it in one line.

**Why this is not in `hosting/`.** Everything here is pure: parsing one JSON
document, choosing a label, and reading and writing four `QSettings` strings. It
holds no token, opens no socket and imports nothing from the hosting package, so
it can be tested at the unit tier on a machine with no network and exercised by
the dialog on a build where hosting is not present at all.

**Why the identity is cached rather than fetched on demand.** `GET /me` is the
busiest endpoint on the control plane and assembles far more than a name: the
wallet, the billing summary, seat lines and onboarding state. Asking it every
time the dialog opens - which is every time somebody exports anything - would
spend all of that to render one line of text that changes perhaps twice a year.
So the answer is stored beside the token and refreshed at the moments it can
actually have changed: signing in, and asking for it deliberately.

**The cache is not authority.** It says who we *were* told we are. The server
decides what a token may do on every call, and a publish that comes back 401 is
what proves the session is gone - not anything read from here. Whenever the
token is cleared this cache must be cleared with it, or the dialog would go on
naming somebody who is no longer signed in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

# One prefix, four keys, all under the group `hosting.auth` already writes the
# token into. Grouped so that signing out can remove them as a set and cannot
# leave one behind to contradict the others.
SETTING_GROUP = "qgis2webmap"
SETTING_EMAIL = f"{SETTING_GROUP}/accountEmail"
SETTING_NAME = f"{SETTING_GROUP}/accountName"
SETTING_ORG = f"{SETTING_GROUP}/accountOrg"
SETTING_PLAN = f"{SETTING_GROUP}/accountPlan"

# How long a label may run before the middle is elided. The button sits in the
# bottom row beside Discord and the status commentary, and an address like
# `firstname.lastname@some-department.example.ac.uk` would push the status text
# out of the dialog. Elided in the middle rather than the end because the domain
# is what distinguishes a work account from a personal one - the exact thing
# somebody checks this button to find out.
MAX_LABEL_CHARS = 28


@dataclass(frozen=True)
class AccountIdentity:
    """The parts of `/me` this plugin has any use for.

    Deliberately four strings. `/me` also carries the wallet, the billing state,
    an onboarding checklist and an admin flag, none of which belong in a QGIS
    dialog; parsing only these four means a change to any of the rest cannot
    break this.

    Every field defaults to empty rather than `None`, so a partial response
    degrades to a shorter label instead of an exception. A server that stops
    sending `org` should cost the user the organisation name, not the button.
    """

    email: str = ""
    display_name: str = ""
    org_name: str = ""
    plan_label: str = ""

    @property
    def is_known(self) -> bool:
        """Whether this names anybody at all.

        The plan alone does not count: "Free plan" with no idea whose it is
        answers none of the questions somebody opens this menu to ask.
        """
        return bool(self.email or self.display_name)

    @property
    def label(self) -> str:
        """What the button says. Address first, because it is the identifier.

        A display name is friendlier but not unique - two accounts can both say
        "Alex" - and the whole purpose of showing this is telling two accounts
        apart. The name is the fallback for an account that somehow has no
        address, never the preference.
        """
        return elide_middle(self.email or self.display_name, MAX_LABEL_CHARS)

    @property
    def plan_line(self) -> str:
        """The second line of the menu header, or empty when nothing is known.

        Empty rather than "Unknown plan": a menu that admits it does not know is
        worse than one that simply does not raise the subject.
        """
        parts = [part for part in (self.plan_label, self.org_name) if part]
        return " · ".join(parts)


def elide_middle(text: str, limit: int) -> str:
    """Shorten to `limit` characters, cutting from the middle.

    Kept separate and pure because it is the one piece of this file with an
    off-by-one worth testing. The ellipsis counts toward the limit, so the
    result never exceeds what the caller asked for.
    """
    clean = text.strip()
    if len(clean) <= limit or limit <= 1:
        return clean
    # One character for the ellipsis; the remainder splits with the bias toward
    # the tail, which carries the domain.
    keep = limit - 1
    head = keep // 2
    tail = keep - head
    return f"{clean[:head]}…{clean[len(clean) - tail :]}"


def parse_me_response(payload: object) -> AccountIdentity:
    """Read an identity out of a `GET /me` body.

    Takes `object` rather than a dict type so that a malformed body - a list, a
    bare string, `None` from a failed decode - is answered with an empty
    identity at the top of the function instead of an `AttributeError` several
    lines into it. There is no useful difference between "the server sent
    nonsense" and "we do not know who you are", and both must leave the dialog
    usable.

    Field names follow `MeResponse` in the control plane's shared contracts:
    `user.email`, `user.name`, `org.name` and `plan.label`. `plan.label` is the
    human wording ("Free", "Pro") as opposed to `plan.id`, which is a slug.
    """
    if not isinstance(payload, dict):
        return AccountIdentity()
    user = _mapping(payload.get("user"))
    org = _mapping(payload.get("org"))
    plan = _mapping(payload.get("plan"))
    return AccountIdentity(
        email=_text(user.get("email")),
        display_name=_text(user.get("name")),
        org_name=_text(org.get("name")),
        plan_label=_text(plan.get("label")),
    )


def load_identity() -> AccountIdentity:
    """The cached identity, or an empty one.

    Never raises. This is called while building the dialog, and a profile with
    an unreadable settings file must still get a working export dialog - it
    simply gets one that offers to sign in.
    """
    from qgis.PyQt.QtCore import QSettings

    with contextlib.suppress(Exception):
        settings = QSettings()
        return AccountIdentity(
            email=_setting(settings, SETTING_EMAIL),
            display_name=_setting(settings, SETTING_NAME),
            org_name=_setting(settings, SETTING_ORG),
            plan_label=_setting(settings, SETTING_PLAN),
        )
    return AccountIdentity()


def save_identity(identity: AccountIdentity) -> None:
    """Cache an identity, removing any field it does not carry.

    Removal rather than storing an empty string, matching
    `settings.save_license_key`: "not known" is then one state in the settings
    file rather than two, and a later read cannot tell a blank apart from an
    absence and get them confused.
    """
    from qgis.PyQt.QtCore import QSettings

    settings = QSettings()
    pairs = (
        (SETTING_EMAIL, identity.email),
        (SETTING_NAME, identity.display_name),
        (SETTING_ORG, identity.org_name),
        (SETTING_PLAN, identity.plan_label),
    )
    with contextlib.suppress(Exception):
        for key, value in pairs:
            text = (value or "").strip()
            if text:
                settings.setValue(key, text)
            else:
                settings.remove(key)


def clear_identity() -> None:
    """Forget who we were signed in as.

    **Must be called wherever the token is cleared**, including the 401 path
    that discards a token the server has rejected. A cache that outlived its
    token would leave the dialog naming an account whose session is gone, which
    is a worse answer than naming nobody.
    """
    save_identity(AccountIdentity())


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _setting(settings: object, key: str) -> str:
    value = settings.value(key, "", type=str)  # type: ignore[attr-defined]
    return str(value or "").strip()
