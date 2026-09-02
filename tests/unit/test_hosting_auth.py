"""The device flow, and the one place the token is allowed to live.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ast
import json

import pytest
from hosting_fakes import FakeTransport, ok

from nika_onlymap_exporter.core import settings as core_settings
from nika_onlymap_exporter.hosting import auth
from nika_onlymap_exporter.hosting.client import (
    AuthRequiredError,
    HostingError,
    HttpResponse,
)

TOKEN = "desk_abcdef0123456789"


@pytest.fixture(autouse=True)
def no_stored_token(monkeypatch):
    """No QSettings in a unit-tier test, and no leakage between them."""
    monkeypatch.setattr(auth, "load_token", lambda: "")
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    monkeypatch.delenv("NIKA_API_BASE", raising=False)


class TestTokenResolution:
    """Precedence identical to `core.settings.resolve_license_key`."""

    def test_no_token_anywhere_reads_as_none(self) -> None:
        assert auth.resolve_token() is None

    def test_an_explicit_token_wins(self, monkeypatch) -> None:
        monkeypatch.setenv(auth.TOKEN_ENV, "desk_from_env")
        assert auth.resolve_token(TOKEN) == TOKEN

    def test_the_environment_beats_the_stored_token(self, monkeypatch) -> None:
        monkeypatch.setattr(auth, "load_token", lambda: "desk_stored")
        monkeypatch.setenv(auth.TOKEN_ENV, "desk_from_env")
        assert auth.resolve_token() == "desk_from_env"

    def test_the_stored_token_is_the_last_resort(self, monkeypatch) -> None:
        monkeypatch.setattr(auth, "load_token", lambda: "desk_stored")
        assert auth.resolve_token() == "desk_stored"

    def test_blank_sources_are_skipped_rather_than_returned(self, monkeypatch) -> None:
        monkeypatch.setenv(auth.TOKEN_ENV, "   ")
        monkeypatch.setattr(auth, "load_token", lambda: "desk_stored")
        assert auth.resolve_token("") == "desk_stored"


class TestTokenNeverReachesTheProject:
    """A `.qgz` gets emailed, committed and handed to a contractor."""

    class RecordingProject:
        def __init__(self) -> None:
            self.entries: dict[tuple[str, str], str] = {}

        def writeEntry(self, scope: str, key: str, value) -> bool:  # noqa: N802
            self.entries[(scope, key)] = value
            return True

        def readEntry(self, scope: str, key: str, default: str = ""):  # noqa: N802
            value = self.entries.get((scope, key), default)
            return value, bool(value)

    def test_saving_everything_the_dialog_knows_writes_no_token(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
        project = self.RecordingProject()

        core_settings.save_state(project, core_settings.DialogState(map_name="Map"))
        core_settings.save_hosted_map_id(project, "map_1")

        written = json.dumps(
            {f"{scope}/{key}": value for (scope, key), value in project.entries.items()}
        )
        assert TOKEN not in written
        assert "hostingToken" not in written
        # The map's own identity does belong with the project: it describes the
        # map rather than the person, and it is what makes republishing keep
        # the link that has already been shared.
        assert core_settings.load_hosted_map_id(project) == "map_1"

    def test_the_settings_module_has_no_key_for_a_token(self) -> None:
        """There is no code path that could write one, not merely none today."""
        with open(core_settings.__file__, encoding="utf-8") as handle:
            assert auth.TOKEN_SETTING not in handle.read()

    def test_the_auth_module_never_touches_the_project(self) -> None:
        """Read from the syntax tree, so a docstring saying so cannot pass it."""
        with open(auth.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "writeEntry" not in called
        assert "readEntry" not in called
        assert "QgsProject" not in imported


class TestDeviceFlow:
    """Pinned to the SHAPE the control plane actually returns.

    These payloads mirror `startDesktopAuthFlow` / `pollDesktopAuthFlow` in
    `nika-cf-workers/apps/control-plane/src/index.ts`. An earlier version of
    this client guessed OAuth-ish names (`deviceCode`, `verificationUrl`,
    `expiresIn`) that the server never sends, which would have failed only on
    a live sign-in. Keep these fixtures in step with that handler.
    """

    def start_payload(self, **overrides) -> dict:
        return {
            "flowId": "daf_abc123",
            "verifier": "dav_secret",
            "authorizeUrl": "https://platform.nika.eco/login?desktop_flow=daf_abc123",
            "expiresAt": "2099-01-01T00:00:00.000Z",
            "pollIntervalMs": 1500,
            **overrides,
        }

    def completed_payload(self) -> dict:
        # A completed poll carries the desktop token inline; there is no
        # separate exchange call for this client.
        return {"status": "completed", "session": {"token": TOKEN, "user": {}}}

    def test_start_reads_the_flow_the_server_describes(self) -> None:
        transport = FakeTransport(ok(self.start_payload()))
        flow = auth.AuthClient("https://api.example", transport).start()

        assert flow.flow_id == "daf_abc123"
        assert flow.verifier == "dav_secret"
        assert flow.authorize_url.startswith("https://platform.nika.eco/login")
        assert flow.interval_seconds == 1.5
        assert transport.requests[0].url.endswith("/desktop/auth/start")

    def test_a_plain_http_authorize_url_is_refused(self) -> None:
        transport = FakeTransport(
            ok(self.start_payload(authorizeUrl="http://platform.nika.eco/login"))
        )
        with pytest.raises(HostingError, match="HTTPS"):
            auth.AuthClient("https://api.example", transport).start()

    def test_a_reply_missing_the_verifier_is_a_readable_failure(self) -> None:
        transport = FakeTransport(ok(self.start_payload(verifier="")))
        with pytest.raises(HostingError, match="sign-in request"):
            auth.AuthClient("https://api.example", transport).start()

    def test_every_poll_carries_the_verifier_not_just_the_flow_id(self) -> None:
        """The verifier is what stops a leaked flow id claiming the session."""
        transport = FakeTransport(
            ok(self.start_payload()), ok(self.completed_payload())
        )
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()
        client.wait_for_approval(flow, sleep=lambda _s: None)

        poll = transport.requests[1]
        assert poll.url.endswith("/desktop/auth/poll")
        body = json.loads(poll.body.decode())
        assert body == {"flowId": "daf_abc123", "verifier": "dav_secret"}

    def test_polling_waits_then_returns_the_desktop_token(self) -> None:
        transport = FakeTransport(
            ok(self.start_payload()),
            ok({"status": "pending"}),
            ok({"status": "pending"}),
            ok(self.completed_payload()),
        )
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()

        slept: list[float] = []
        token = client.wait_for_approval(flow, sleep=slept.append)

        assert token == TOKEN
        assert len(slept) == 2

    def test_a_completed_poll_without_a_token_is_a_readable_failure(self) -> None:
        transport = FakeTransport(
            ok(self.start_payload()), ok({"status": "completed", "session": {}})
        )
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()
        with pytest.raises(HostingError, match="no session came back"):
            client.wait_for_approval(flow, sleep=lambda _s: None)

    def test_the_poll_gives_up_rather_than_looping_forever(self) -> None:
        transport = FakeTransport(
            ok(self.start_payload()), *[ok({"status": "pending"})] * 20
        )
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()

        clock = iter([0.0, 400.0, 800.0])
        with pytest.raises(auth.SignInTimeoutError):
            client.wait_for_approval(
                flow, sleep=lambda _s: None, now=lambda: next(clock)
            )

    def test_an_expiry_already_past_does_not_start_a_doomed_poll(self) -> None:
        transport = FakeTransport(
            ok(self.start_payload(expiresAt="2020-01-01T00:00:00.000Z")),
            *[ok({"status": "pending"})] * 5,
        )
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()
        assert flow.deadline_seconds == 0.0
        with pytest.raises(auth.SignInTimeoutError):
            client.wait_for_approval(flow, sleep=lambda _s: None)

    def test_an_unreadable_expiry_falls_back_rather_than_abandoning(self) -> None:
        """A value we cannot parse must not cancel a sign-in in progress."""
        flow = auth.DeviceFlow(
            flow_id="f", verifier="v", authorize_url="https://x", expires_at="nonsense"
        )
        assert flow.deadline_seconds == auth.MAX_POLL_SECONDS

    def test_cancelling_stops_the_poll_without_a_failure(self) -> None:
        transport = FakeTransport(ok(self.start_payload()))
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()
        with pytest.raises(auth.SignInCancelledError):
            client.wait_for_approval(flow, keep_waiting=lambda: False)

    def test_a_server_side_expiry_says_to_start_again(self) -> None:
        transport = FakeTransport(ok(self.start_payload()), ok({"status": "expired"}))
        client = auth.AuthClient("https://api.example", transport)
        flow = client.start()
        with pytest.raises(auth.SignInTimeoutError, match="expired"):
            client.wait_for_approval(flow, sleep=lambda _s: None)

    def test_the_whole_flow_stores_the_token_once(self, monkeypatch) -> None:
        stored: list[str] = []
        monkeypatch.setattr(auth, "save_token", stored.append)
        transport = FakeTransport(
            ok(self.start_payload()), ok(self.completed_payload())
        )
        opened: list[str] = []

        token = auth.sign_in(
            client=auth.AuthClient("https://api.example", transport),
            on_verification=lambda flow: opened.append(flow.authorize_url),
        )

        assert token == TOKEN
        assert stored == [TOKEN]
        assert opened == ["https://platform.nika.eco/login?desktop_flow=daf_abc123"]


class TestAuthorizedClient:
    def test_no_token_names_signing_in_as_the_fix(self) -> None:
        with pytest.raises(AuthRequiredError, match="Sign in"):
            auth.authorized_client()

    def test_a_token_produces_a_client_pointed_at_the_api(self) -> None:
        client = auth.authorized_client(
            TOKEN, api_base="https://api.example", transport=FakeTransport()
        )
        assert client.token == TOKEN
        assert client.api_base == "https://api.example"


class TestFailureMessages:
    def test_a_refused_sign_in_names_the_status(self) -> None:
        transport = FakeTransport(HttpResponse(500, b'{"message":"down"}'))
        with pytest.raises(HostingError, match="down"):
            auth.AuthClient("https://api.example", transport).start()
