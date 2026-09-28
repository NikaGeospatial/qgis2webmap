"""What the server says about a project's hosted map, and what Host should do.

The Host button used to read one thing: whether the project carries a
`hostedMapId`. That is a record of the past, not of the map. A map taken down
from the dashboard, deleted, or belonging to an organisation the user is no
longer signed in to still left the id in the `.qgz`, the button still said
Republish, and pressing it ended in a refusal with nothing the dialog could do
about it - a project that could never be published again.

So the state is asked for. `GET /maps/{id}` is the management API's own read,
open to a desktop token under the `maps` scope, and it answers with the map's
status - or with the 404 and 403 that mean the id is no good any more. This
module turns that answer into a `RemoteMapState`, and a state into the action
the Host control offers:

* `host`        - no map yet: publishing creates one.
* `republish`   - a map this user can update: live, paused, expired or stopped.
                  A republish brings an expired or stopped map back.
* `host_as_new` - the stored map was taken down, no longer exists, or belongs
                  to another organisation. Publishing detaches the stale id and
                  creates a fresh map; the old address is left as it is.

When the server cannot be asked - offline, signed out, not checked yet - the
action falls back to the stored id, which is exactly what the button did
before, so being offline is never worse than it was.

**No Qt here, and no network.** Parsing a reply and choosing a label are pure,
so they are tested directly; the dialog owns the timer and the thread and keeps
its binding thin, because the button this drives is expected to move into a
different control.

What the check SENDS: the map id in the path and the sign-in token in the
`Authorization` header, to the same NIKA API a publish uses. Nothing about the
project's contents.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

# Where the map stands, as far as this plugin can tell.
MapPresence = Literal[
    "none",
    "unchecked",
    "live",
    "paused",
    "expired",
    "stopped",
    "taken_down",
    "missing",
    "other_org",
    "offline",
    "signed_out",
]
PRESENCE_NONE: MapPresence = "none"
PRESENCE_UNCHECKED: MapPresence = "unchecked"
PRESENCE_LIVE: MapPresence = "live"
PRESENCE_PAUSED: MapPresence = "paused"
PRESENCE_EXPIRED: MapPresence = "expired"
PRESENCE_STOPPED: MapPresence = "stopped"
PRESENCE_TAKEN_DOWN: MapPresence = "taken_down"
PRESENCE_MISSING: MapPresence = "missing"
PRESENCE_OTHER_ORG: MapPresence = "other_org"
PRESENCE_OFFLINE: MapPresence = "offline"
PRESENCE_SIGNED_OUT: MapPresence = "signed_out"

# The server's `map.status` vocabulary, mapped onto ours. `unrecoverable` is a
# takedown whose restore window closed: for publishing it is the same answer.
_STATUS_TO_PRESENCE: dict[str, MapPresence] = {
    "live": PRESENCE_LIVE,
    "paused": PRESENCE_PAUSED,
    "expired": PRESENCE_EXPIRED,
    "stopped": PRESENCE_STOPPED,
    "takendown": PRESENCE_TAKEN_DOWN,
    "unrecoverable": PRESENCE_TAKEN_DOWN,
}

# States the server has actually answered, as opposed to "could not ask".
_ANSWERED = frozenset(
    {
        PRESENCE_LIVE,
        PRESENCE_PAUSED,
        PRESENCE_EXPIRED,
        PRESENCE_STOPPED,
        PRESENCE_TAKEN_DOWN,
        PRESENCE_MISSING,
        PRESENCE_OTHER_ORG,
    }
)

# The stored id is dead for publishing purposes.
_NEEDS_NEW_MAP = frozenset({PRESENCE_TAKEN_DOWN, PRESENCE_MISSING, PRESENCE_OTHER_ORG})

HostAction = Literal["host", "republish", "host_as_new"]
ACTION_HOST: HostAction = "host"
ACTION_REPUBLISH: HostAction = "republish"
ACTION_HOST_AS_NEW: HostAction = "host_as_new"

# The arrow marks the one control in the dialog whose press ends with something
# leaving the machine; every label keeps it.
_LABELS: dict[HostAction, str] = {
    ACTION_HOST: "Host ↗",
    ACTION_REPUBLISH: "Republish ↗",
    ACTION_HOST_AS_NEW: "Host as new map ↗",
}


@dataclass(frozen=True)
class RemoteMapState:
    """One answer about one map id. `map_id` is empty for PRESENCE_NONE."""

    map_id: str
    presence: MapPresence
    current_release: int | None = None
    has_password: bool = False
    #: The permanent address, when the server named one.
    public_url: str = ""

    @property
    def is_answer(self) -> bool:
        """Whether the server actually said this, rather than could not be asked."""
        return self.presence in _ANSWERED

    @property
    def needs_new_map(self) -> bool:
        return self.presence in _NEEDS_NEW_MAP


def unpublished() -> RemoteMapState:
    return RemoteMapState(map_id="", presence=PRESENCE_NONE)


def _json_object(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_code(payload: dict[str, object]) -> str:
    envelope = payload.get("error")
    if not isinstance(envelope, dict):
        return ""
    code = envelope.get("code")
    return code if isinstance(code, str) else ""


def parse_map_state(map_id: str, status: int, body: bytes) -> RemoteMapState:
    """Read `GET /maps/{id}`'s reply into a state. Never raises.

    The two refusals that matter are told apart by `error.code`, never by status
    alone: a 403 is ALSO what an expired or under-scoped desktop token gets, and
    reading that as "another organisation's map" would offer to fork a perfectly
    good map because a token lapsed.
    """
    payload = _json_object(body)
    if 200 <= status < 300:
        raw = payload.get("map")
        if not isinstance(raw, dict):
            # A 200 with no map is not an answer this client can act on; the
            # stored id keeps deciding, as it would offline.
            return RemoteMapState(map_id=map_id, presence=PRESENCE_OFFLINE)
        server_status = raw.get("status")
        presence = _STATUS_TO_PRESENCE.get(
            server_status if isinstance(server_status, str) else ""
        )
        if presence is None:
            # A status newer than this plugin. Updating an unknown state is not
            # a decision to make on a guess; treat it as "could not tell".
            return RemoteMapState(map_id=map_id, presence=PRESENCE_OFFLINE)
        release = raw.get("currentRelease")
        return RemoteMapState(
            map_id=map_id,
            presence=presence,
            current_release=(
                release
                if isinstance(release, int) and not isinstance(release, bool)
                else None
            ),
            has_password=raw.get("hasPassword") is True,
            public_url=_permanent_address(raw),
        )

    code = _error_code(payload)
    if status == 404 and code == "map_not_found":
        return RemoteMapState(map_id=map_id, presence=PRESENCE_MISSING)
    if status == 403 and code == "map_forbidden":
        return RemoteMapState(map_id=map_id, presence=PRESENCE_OTHER_ORG)
    if status in (401, 403):
        return RemoteMapState(map_id=map_id, presence=PRESENCE_SIGNED_OUT)
    # 5xx, 429, a proxy's page, a 404 from something that is not our API: none
    # of them says anything about the map.
    return RemoteMapState(map_id=map_id, presence=PRESENCE_OFFLINE)


def _permanent_address(raw: dict[str, object]) -> str:
    addresses = raw.get("addresses")
    if not isinstance(addresses, list):
        return ""
    for entry in addresses:
        if isinstance(entry, dict) and entry.get("form") == "id":
            url = entry.get("url")
            return url if isinstance(url, str) else ""
    return ""


def host_action(stored_map_id: str, state: RemoteMapState) -> HostAction:
    """What pressing Host does, for this project and this answer.

    The answer counts only when it is about the id the project holds NOW: a
    reply that arrives after a different project was opened describes somebody
    else's map.
    """
    if not stored_map_id:
        return ACTION_HOST
    if state.map_id == stored_map_id and state.needs_new_map:
        return ACTION_HOST_AS_NEW
    return ACTION_REPUBLISH


def host_button_label(action: HostAction) -> str:
    return _LABELS[action]


def host_button_tooltip(stored_map_id: str, state: RemoteMapState) -> str:
    """Why the button says what it says, in one or two sentences."""
    common = (
        " Asks you to sign in the first time, and always confirms what is about "
        "to be uploaded before anything leaves this machine."
    )
    if not stored_map_id:
        return "Publish this map to NIKA and get a public link." + common
    about_this = state.map_id == stored_map_id
    presence = state.presence if about_this else PRESENCE_UNCHECKED
    if presence in _NEEDS_NEW_MAP:
        return (
            new_map_reason(state)
            + " Pressing this publishes the project as a new map with a new address."
            + common
        )
    if presence == PRESENCE_PAUSED:
        return (
            "Update this project's hosted map. It is paused: republishing updates "
            "it, and it stays paused until it is resumed from the dashboard." + common
        )
    if presence in (PRESENCE_EXPIRED, PRESENCE_STOPPED):
        return (
            "Update this project's hosted map. It is not currently online; "
            "republishing brings it back." + common
        )
    if presence == PRESENCE_LIVE:
        return (
            "Update this project's hosted map at the address it already has." + common
        )
    if presence == PRESENCE_SIGNED_OUT:
        return (
            "Update this project's hosted map. Sign in to see its current state."
            + common
        )
    return (
        "Update this project's hosted map. Its current state could not be checked."
        + common
    )


def new_map_reason(state: RemoteMapState) -> str:
    """Why the project's map cannot be updated, as a sentence."""
    if state.presence == PRESENCE_TAKEN_DOWN:
        return "The map this project was published as has been taken down."
    if state.presence == PRESENCE_OTHER_ORG:
        return (
            "The map this project was published as belongs to another NIKA "
            "organisation than the one you are signed in to."
        )
    return "The map this project was published as no longer exists."


def presence_for_refusal_code(code: str) -> MapPresence | None:
    """The state a publish refusal proves, for the codes that prove one."""
    return {
        "map_taken_down": PRESENCE_TAKEN_DOWN,
        "map_not_found": PRESENCE_MISSING,
        "map_forbidden": PRESENCE_OTHER_ORG,
    }.get(code)
