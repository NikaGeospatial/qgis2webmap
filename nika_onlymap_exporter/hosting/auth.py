"""Signing in to NIKA from the desktop, and where the token is kept.

The device flow, because a QGIS plugin has no business showing a password
field. The plugin never sees a credential:

1. `POST /desktop/auth/start` hands back a `flowId`, a secret `verifier`, and
   an `authorizeUrl` that already carries the flow id.
2. The user's own browser opens that URL and signs in there; `/desktop/auth/
   complete` is the browser's half of the handshake and is never called from
   here.
3. `POST /desktop/auth/poll` is asked with BOTH the flow id and the verifier,
   until it reports `completed` or `expired`. The verifier is what stops anyone
   who merely learns a flow id from collecting someone else's session, so it is
   sent on every poll and never put in a URL.
4. The completed poll already carries `session.token` - the long-lived `desk_`
   token every publish call needs. There is no separate exchange step:
   `/desktop/session/exchange` exists for a client that already holds a browser
   session cookie, which a QGIS plugin never does.

**The token is scoped to what this plugin does.** `start` asks for
`REQUESTED_SCOPES` - reading your identity and publishing maps - and the issued
token carries only those. It cannot reach billing, cannot create API keys and
cannot spend an AI allowance, so the blast radius of the plaintext file below is
"someone can publish maps to your account", not "someone can spend your money".
The browser shows what is being approved before you agree to it.

**Where the token lives, and where it must never live.** In `QSettings`, by
exactly the argument `core.settings` makes for the licence key, only harder:
that key is signed, domain-locked and meant to be served to browsers, whereas
this one is a bearer credential for the user's account. A `.qgz` gets emailed,
committed and handed to a contractor. Nothing in this module touches
`QgsProject`, and `core.settings.save_state` has no key for it, so there is no
code path that could write one into a project file.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .client import (
    AuthRequiredError,
    HostingClient,
    HostingError,
    HttpRequest,
    JsonObject,
    Transport,
    decode_json,
    require_https,
    resolve_api_base,
    server_message,
    urllib_transport,
)

TOKEN_SETTING = "qgis2webmap/hostingToken"

# The headless route, mirroring `ONLYMAP_LICENSE_KEY`. A CI job or a container
# has no QGIS profile to read, and this is the only way it can publish.
TOKEN_ENV = "NIKA_HOSTING_TOKEN"

# What a desktop token looks like. Shape only - the server is the authority on
# whether it is still valid - but it catches the paste of the wrong string
# before it is stored and fails on the next publish.
TOKEN_PREFIX = "desk_"

# Shown in the account's device list and stored against the session, so the
# user can tell which client they approved and revoke it by name later.
DEVICE_NAME = "QGIS plugin"

# **What this plugin asks to be allowed to do, and nothing more.**
#
# `identity` reads who you are signed in as; `maps` publishes and manages your
# hosted maps. Deliberately absent is the scope covering AI usage: this plugin
# has no AI features, and a token that cannot spend an allowance cannot be used
# to spend one if it is copied off this machine.
#
# That matters here more than in NIKA's other desktop clients. The token below
# is written to a `QSettings` file in the clear, because a QGIS plugin has no
# keychain to put it in - so the realistic threat is not someone attacking the
# API, it is someone reading a file on a shared or backed-up machine. Asking for
# less is the only mitigation available on this side.
#
# The server narrows this to scopes it recognises and is the authority on what
# is actually granted; asking for more here would not obtain more.
REQUESTED_SCOPES = ("identity", "maps")

# If the server names no interval, poll every five seconds: often enough that
# approving in the browser feels immediate, rarely enough to be polite.
DEFAULT_POLL_SECONDS = 5.0

# Give up after this long even if the server claims a longer window. Someone who
# has not finished signing in within five minutes has walked away, and a poll
# loop that outlives the dialog is a thread holding a dead widget.
MAX_POLL_SECONDS = 300.0


class SignInCancelledError(HostingError):
    """The user stopped waiting, or turned the request down in the browser.

    Not a failure to report: it unwinds the flow and is shown as "nothing was
    published", the same way `JobCancelledError` is treated in `background_job`.
    """


class SignInTimeoutError(HostingError):
    """The approval window closed before the browser confirmed anything."""


@dataclass(frozen=True)
class DeviceFlow:
    """A pending sign-in. `authorize_url` is what the browser must open."""

    flow_id: str
    # Secret. Proves this plugin started the flow, so learning a flow id is not
    # enough to claim the resulting session. Never logged, never in a URL.
    verifier: str
    authorize_url: str
    interval_seconds: float = DEFAULT_POLL_SECONDS
    # Absolute ISO-8601 instant from the server, not a duration. Kept as the
    # server sent it and only parsed for the deadline, so a clock that
    # disagrees slightly cannot extend the window.
    expires_at: str = ""

    @property
    def deadline_seconds(self) -> float:
        """How long to keep asking, whichever of the two limits is shorter."""
        remaining = _seconds_until(self.expires_at)
        if remaining is None:
            return MAX_POLL_SECONDS
        return max(0.0, min(remaining, MAX_POLL_SECONDS))


# Returns True to keep waiting, False to give up. The poll loop's only link to
# a Cancel button, so it stays free of Qt.
KeepWaiting = Callable[[], bool]


def looks_like_token(value: str) -> bool:
    return value.strip().startswith(TOKEN_PREFIX) and len(value.strip()) > len(
        TOKEN_PREFIX
    )


def load_token() -> str:
    """The stored token, or an empty string."""
    from qgis.PyQt.QtCore import QSettings

    with contextlib.suppress(Exception):
        return str(QSettings().value(TOKEN_SETTING, "", type=str) or "").strip()
    return ""


def save_token(value: str) -> None:
    """Store the token, or remove the entry when handed nothing.

    Blank removes rather than storing an empty string, so "signed out" is one
    state in the settings file rather than two - the same rule
    `core.settings.save_license_key` follows.
    """
    from qgis.PyQt.QtCore import QSettings

    settings = QSettings()
    text = (value or "").strip()
    with contextlib.suppress(Exception):
        if text:
            settings.setValue(TOKEN_SETTING, text)
        else:
            settings.remove(TOKEN_SETTING)


def clear_token() -> None:
    """Forget the stored token.

    Called on a 401. A token the server has rejected is worse than no token: it
    makes every publish fail at the upload stage rather than sending the user
    through a sign-in that would fix it.
    """
    save_token("")


def resolve_token(explicit: str | None = None) -> str | None:
    """The token to publish with, from the most specific source that has one.

    Precedence, identical to `core.settings.resolve_license_key` so there is one
    rule to learn rather than two:

    1. **`explicit`** - passed in by a caller that already has one.
    2. **`NIKA_HOSTING_TOKEN`** - the environment, the only headless route.
    3. **The stored token** - what signing in from the dialog saved.

    Returns `None` rather than an empty string when there is none, so "not
    signed in" is a single check at every call site.
    """
    for candidate in (explicit, os.environ.get(TOKEN_ENV), load_token()):
        text = (candidate or "").strip()
        if text:
            return text
    return None


class AuthClient:
    """The device flow against one API base. No token: this is how one is got."""

    def __init__(
        self,
        api_base: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.api_base = resolve_api_base(api_base)
        self._transport: Transport = transport or urllib_transport

    def _post(self, path: str, payload: JsonObject, what: str) -> JsonObject:
        url = require_https(f"{self.api_base}{path}")
        response = self._transport(
            HttpRequest(
                method="POST",
                url=url,
                body=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        )
        if not 200 <= response.status < 300:
            detail = server_message(response)
            raise HostingError(
                f"Signing in to NIKA failed while {what} "
                f"(HTTP {response.status})." + (f"\n\n{detail}" if detail else "")
            )
        return decode_json(response, what)

    def start(self) -> DeviceFlow:
        data = self._post(
            "/desktop/auth/start",
            {"deviceName": DEVICE_NAME, "scopes": list(REQUESTED_SCOPES)},
            "starting the sign-in",
        )
        flow_id = _text(data, "flowId")
        verifier = _text(data, "verifier")
        authorize_url = _text(data, "authorizeUrl")
        if not flow_id or not verifier or not authorize_url:
            raise HostingError(
                "The sign-in server did not return a usable sign-in request. "
                "Nothing was published."
            )
        # The server names the interval in milliseconds.
        interval_ms = _number(data, "pollIntervalMs")
        return DeviceFlow(
            flow_id=flow_id,
            verifier=verifier,
            authorize_url=require_https(authorize_url),
            interval_seconds=(interval_ms / 1000.0)
            if interval_ms
            else DEFAULT_POLL_SECONDS,
            expires_at=_text(data, "expiresAt"),
        )

    def poll_once(self, flow: DeviceFlow) -> str | None:
        """One poll. Returns the session token once approved, else `None`.

        Split from the loop so the waiting - the part that needs a clock and a
        cancel button - is testable without either.
        """
        # Both halves, every time: the flow id names the request and the
        # verifier proves it is ours. A mismatched verifier is a 403, which
        # `_post` surfaces rather than treating as "not yet approved".
        data = self._post(
            "/desktop/auth/poll",
            {"flowId": flow.flow_id, "verifier": flow.verifier},
            "waiting for approval",
        )
        status = _text(data, "status").lower()
        if status == "expired":
            raise SignInTimeoutError(
                "The sign-in request expired before it was approved. Press Host "
                "again to start a new one."
            )
        if status != "completed":
            return None

        # A completed poll carries the desktop token directly - there is no
        # exchange step for this client.
        session = data.get("session")
        token = _text(session, "token") if isinstance(session, dict) else ""
        if not token:
            raise HostingError(
                "The sign-in was approved but no session came back. Press "
                "Host again to retry."
            )
        return token

    def wait_for_approval(
        self,
        flow: DeviceFlow,
        keep_waiting: KeepWaiting | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> str:
        """Poll until approved, refused, timed out or cancelled.

        `sleep` and `now` are injected so the timeout path is a unit test rather
        than a five-minute one.
        """
        wait = sleep or time.sleep
        clock = now or time.monotonic
        started = clock()
        deadline = flow.deadline_seconds

        while True:
            if keep_waiting is not None and not keep_waiting():
                raise SignInCancelledError(
                    "Sign-in was cancelled. Nothing was published."
                )
            token = self.poll_once(flow)
            if token:
                return token
            if clock() - started >= deadline:
                raise SignInTimeoutError(
                    "NIKA did not confirm the sign-in in time. Press Host again "
                    "to start a new one."
                )
            wait(max(flow.interval_seconds, 1.0))


def sign_in(
    client: AuthClient | None = None,
    on_verification: Callable[[DeviceFlow], None] | None = None,
    keep_waiting: KeepWaiting | None = None,
) -> str:
    """The whole flow, ending with the token stored.

    `on_verification` is where the caller opens the browser; it runs before any
    waiting so the user is looking at the right page while the poll loop starts.
    """
    auth = client or AuthClient()
    flow = auth.start()
    if on_verification is not None:
        on_verification(flow)
    # `wait_for_approval` already returns the desktop token: a completed poll
    # carries it, so there is nothing left to exchange.
    token = auth.wait_for_approval(flow, keep_waiting=keep_waiting)
    save_token(token)
    return token


def authorized_client(
    token: str | None = None,
    api_base: str | None = None,
    transport: Transport | None = None,
) -> HostingClient:
    """A publishing client, or a refusal naming sign-in as the fix."""
    resolved = resolve_token(token)
    if not resolved:
        raise AuthRequiredError("Sign in to NIKA before publishing.")
    return HostingClient(resolved, api_base=api_base, transport=transport)


def _seconds_until(iso_instant: str) -> float | None:
    """Seconds from now to an ISO-8601 instant, or `None` if unreadable.

    `None` rather than zero on a value we cannot parse: an unreadable expiry
    should fall back to the local ceiling, not abandon a sign-in the user is
    part-way through.
    """
    text = iso_instant.strip()
    if not text:
        return None
    with contextlib.suppress(ValueError):
        # `fromisoformat` accepts the offset form but not a trailing Z.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed - datetime.now(timezone.utc)).total_seconds()
    return None


def _text(data: JsonObject, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _number(data: JsonObject, key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _int(data: JsonObject, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
