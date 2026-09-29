"""The Host button's view of the server, and the publish desyncs it closes.

Four things, all driven through the real client or exporter over a fake
transport, never a socket:

* `hosting.map_state` - reading `GET /maps/{id}` into a state, and choosing the
  Host action from it. A map taken down, deleted or owned by another
  organisation offers "Host as new map" instead of a Republish that can only be
  refused.
* the release poll surviving a dropped request while the server verifies, which
  used to leave the project a release behind its own upload;
* the next publish adopting that upload by its release id instead of showing a
  "someone else published" conflict about the user's own work;
* the smaller wording fixes - a refused map id is not an expired sign-in, a
  lost release race is not the stale-copy question, and "offline" is said in
  words rather than as `[Errno 111]`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest
from hosting_fakes import FakeTransport, ok

from nika_onlymap_exporter.core.export_ir import OutputMode
from nika_onlymap_exporter.core.settings import (
    KEY_HOSTED_MAP_ID,
    KEY_HOSTED_PENDING_RELEASE,
    KEY_HOSTED_RELEASE_N,
    SCOPE,
    clear_hosted_pending_release,
    detach_hosted_map,
    load_hosted_map_id,
    load_hosted_pending_release,
    load_hosted_release_n,
    save_hosted_map_id,
    save_hosted_pending_release,
    save_hosted_release_n,
)
from nika_onlymap_exporter.exporters.hosted import (
    STAGE_COMPLETING,
    STAGE_LOCAL,
    STAGE_RESERVED,
    STAGE_UPLOADING,
    STAGE_VERIFYING,
    HostedExporter,
    cancelled_publish_text,
)
from nika_onlymap_exporter.hosting import client as client_module
from nika_onlymap_exporter.hosting.client import (
    VERIFY_CONTACT_LOST_MESSAGE,
    AuthRequiredError,
    HostingClient,
    HostingError,
    HttpRequest,
    HttpResponse,
    PublishConflictError,
    PublishRefusedError,
    urllib_transport,
)
from nika_onlymap_exporter.hosting.manifest import (
    MANIFEST_KIND,
    ManifestFile,
    ProducerInfo,
    PublishManifest,
    RuntimeInfo,
)
from nika_onlymap_exporter.hosting.map_state import (
    ACTION_HOST,
    ACTION_HOST_AS_NEW,
    ACTION_REPUBLISH,
    PRESENCE_EXPIRED,
    PRESENCE_LIVE,
    PRESENCE_MISSING,
    PRESENCE_OFFLINE,
    PRESENCE_OTHER_ORG,
    PRESENCE_PAUSED,
    PRESENCE_SIGNED_OUT,
    PRESENCE_STOPPED,
    PRESENCE_TAKEN_DOWN,
    PRESENCE_UNCHECKED,
    RemoteMapState,
    host_action,
    host_button_label,
    host_button_tooltip,
    parse_map_state,
    presence_for_refusal_code,
    unpublished,
)
from nika_onlymap_exporter.writers.onlymap_writer import ArtifactFile, ArtifactResult

MAP_ID = "k7m2qx9vt4bdp3w8n5r2h6j9c"
OTHER_MAP = "p" * 25


def api(*responses: object) -> tuple[HostingClient, FakeTransport, list[float]]:
    transport = FakeTransport(*responses)
    slept: list[float] = []
    return (
        HostingClient("desk_x", "https://api.example", transport, sleeper=slept.append),
        transport,
        slept,
    )


def reply(status: int, payload: object) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode("utf-8"))


def refusal(status: int, code: str, message: str = "no") -> HttpResponse:
    return reply(status, {"error": {"code": code, "message": message}})


def map_reply(status: str, **fields: object) -> HttpResponse:
    body = {
        "map": {
            "id": MAP_ID,
            "status": status,
            "currentRelease": 4,
            "hasPassword": False,
            "addresses": [
                {"form": "id", "url": f"https://maps.example/orgs/o/m-{MAP_ID}"},
                {"form": "alias", "url": "https://maps.example/orgs/o/m"},
            ],
            **fields,
        }
    }
    return reply(200, body)


# ---------------------------------------------------------------------------
# Reading the server's answer
# ---------------------------------------------------------------------------


class TestParseMapState:
    @pytest.mark.parametrize(
        ("server", "presence"),
        [
            ("live", PRESENCE_LIVE),
            ("paused", PRESENCE_PAUSED),
            ("expired", PRESENCE_EXPIRED),
            ("stopped", PRESENCE_STOPPED),
            ("takendown", PRESENCE_TAKEN_DOWN),
            ("unrecoverable", PRESENCE_TAKEN_DOWN),
        ],
    )
    def test_every_server_status_has_a_state(self, server: str, presence) -> None:
        response = map_reply(server)
        state = parse_map_state(MAP_ID, response.status, response.body)
        assert state.presence == presence
        assert state.is_answer

    def test_the_facts_the_dialog_uses_come_through(self) -> None:
        response = map_reply("live", hasPassword=True, currentRelease=9)
        state = parse_map_state(MAP_ID, response.status, response.body)
        assert state.has_password is True
        assert state.current_release == 9
        assert state.public_url == f"https://maps.example/orgs/o/m-{MAP_ID}"

    def test_a_404_map_not_found_is_a_missing_map(self) -> None:
        response = refusal(404, "map_not_found")
        assert parse_map_state(MAP_ID, 404, response.body).presence == PRESENCE_MISSING

    def test_a_permanently_deleted_map_offers_host_as_new(self) -> None:
        """The control plane answers a deleted map's tombstone with exactly the
        404 an unknown id gets (`resolveManagedMap`), so no new code is needed
        here - this pins the reply body it sends so that stays true."""
        body = json.dumps(
            {"error": {"code": "map_not_found", "message": "Unknown map."}}
        ).encode()
        state = parse_map_state(MAP_ID, 404, body)
        assert state.presence == PRESENCE_MISSING
        assert host_action(MAP_ID, state) == ACTION_HOST_AS_NEW
        assert host_button_label(ACTION_HOST_AS_NEW) == "Host as new map ↗"

    def test_a_403_map_forbidden_is_another_organisations_map(self) -> None:
        response = refusal(403, "map_forbidden")
        state = parse_map_state(MAP_ID, 403, response.body)
        assert state.presence == PRESENCE_OTHER_ORG
        assert state.needs_new_map

    @pytest.mark.parametrize(
        "code", ["insufficient_scope", "desktop_token_not_permitted", ""]
    )
    def test_any_other_403_is_a_sign_in_problem_not_a_foreign_map(self, code) -> None:
        """Reading a lapsed token as "another organisation's map" would offer to
        fork a perfectly good map."""
        response = refusal(403, code)
        state = parse_map_state(MAP_ID, 403, response.body)
        assert state.presence == PRESENCE_SIGNED_OUT
        assert not state.needs_new_map

    def test_a_401_is_signed_out(self) -> None:
        assert parse_map_state(MAP_ID, 401, b"").presence == PRESENCE_SIGNED_OUT

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (500, b"oops"),
            (502, b"<html>bad gateway</html>"),
            (429, b"{}"),
            (404, b"<html>not our API</html>"),
            (200, b"{}"),
            (200, b"not json"),
        ],
    )
    def test_anything_that_says_nothing_about_the_map_is_offline(
        self, status, body
    ) -> None:
        assert parse_map_state(MAP_ID, status, body).presence == PRESENCE_OFFLINE

    def test_a_status_newer_than_this_plugin_decides_nothing(self) -> None:
        response = map_reply("archived")
        state = parse_map_state(MAP_ID, response.status, response.body)
        assert state.presence == PRESENCE_OFFLINE
        assert host_action(MAP_ID, state) == ACTION_REPUBLISH


# ---------------------------------------------------------------------------
# Choosing the action
# ---------------------------------------------------------------------------


def state(presence, map_id: str = MAP_ID) -> RemoteMapState:
    return RemoteMapState(map_id=map_id, presence=presence)


class TestHostAction:
    def test_no_stored_map_is_host(self) -> None:
        assert host_action("", unpublished()) == ACTION_HOST
        assert host_button_label(ACTION_HOST) == "Host ↗"

    @pytest.mark.parametrize(
        "presence",
        [
            PRESENCE_LIVE,
            PRESENCE_PAUSED,
            PRESENCE_EXPIRED,
            PRESENCE_STOPPED,
            PRESENCE_OFFLINE,
            PRESENCE_SIGNED_OUT,
            PRESENCE_UNCHECKED,
        ],
    )
    def test_an_updatable_or_unknown_map_is_republish(self, presence) -> None:
        """Offline, signed out and unchecked fall back to the stored id - what
        the button did before - so being offline is never worse than it was."""
        assert host_action(MAP_ID, state(presence)) == ACTION_REPUBLISH

    @pytest.mark.parametrize(
        "presence", [PRESENCE_TAKEN_DOWN, PRESENCE_MISSING, PRESENCE_OTHER_ORG]
    )
    def test_a_dead_map_is_host_as_new(self, presence) -> None:
        assert host_action(MAP_ID, state(presence)) == ACTION_HOST_AS_NEW
        assert host_button_label(ACTION_HOST_AS_NEW) == "Host as new map ↗"

    def test_an_answer_about_another_id_is_ignored(self) -> None:
        """A reply that lands after a different project was opened describes
        somebody else's map."""
        assert (
            host_action(MAP_ID, state(PRESENCE_TAKEN_DOWN, OTHER_MAP))
            == ACTION_REPUBLISH
        )

    def test_every_tooltip_says_why(self) -> None:
        assert "taken down" in host_button_tooltip(MAP_ID, state(PRESENCE_TAKEN_DOWN))
        assert "another NIKA organisation" in host_button_tooltip(
            MAP_ID, state(PRESENCE_OTHER_ORG)
        )
        assert "no longer exists" in host_button_tooltip(
            MAP_ID, state(PRESENCE_MISSING)
        )
        assert "paused" in host_button_tooltip(MAP_ID, state(PRESENCE_PAUSED))
        assert "Sign in" in host_button_tooltip(MAP_ID, state(PRESENCE_SIGNED_OUT))

    def test_refusal_codes_prove_a_state(self) -> None:
        assert presence_for_refusal_code("map_taken_down") == PRESENCE_TAKEN_DOWN
        assert presence_for_refusal_code("map_not_found") == PRESENCE_MISSING
        assert presence_for_refusal_code("map_forbidden") == PRESENCE_OTHER_ORG
        assert presence_for_refusal_code("map_limit_reached") is None


# ---------------------------------------------------------------------------
# Asking the server
# ---------------------------------------------------------------------------


class TestMapStateRequest:
    def test_it_is_one_authenticated_get_of_the_map(self) -> None:
        client, transport, _ = api(map_reply("paused"))
        assert client.map_state(MAP_ID).presence == PRESENCE_PAUSED
        (request,) = transport.requests
        assert request.method == "GET"
        assert request.url == f"https://api.example/maps/{MAP_ID}"
        assert request.headers["Authorization"] == "Bearer desk_x"
        assert request.body is None

    def test_an_unreachable_server_is_a_state_not_an_exception(self) -> None:
        client, _transport, _ = api(HostingError("no route"))
        assert client.map_state(MAP_ID).presence == PRESENCE_OFFLINE

    def test_the_id_cannot_escape_the_path(self) -> None:
        client, transport, _ = api(map_reply("live"))
        client.map_state("../admin")
        assert transport.requests[0].url == "https://api.example/maps/..%2Fadmin"


# ---------------------------------------------------------------------------
# The release poll, which one dropped request used to end
# ---------------------------------------------------------------------------

LIVE = {
    "state": "live",
    "mapId": MAP_ID,
    "releaseN": 5,
    "publicUrl": "https://maps.example/m",
}


class TestAwaitReleaseSurvivesHiccups:
    def test_a_transient_failure_is_retried_with_backoff(self) -> None:
        client, transport, slept = api(
            HostingError("reset"),
            HttpResponse(502, b"<html>bad gateway</html>"),
            ok(LIVE),
        )
        result = client.await_release("rel_1")
        assert result.release_n == 5
        assert len(transport.requests) == 3
        assert slept == [2.0, 4.0]

    def test_backoff_stops_growing_at_its_ceiling(self, monkeypatch) -> None:
        monkeypatch.setattr(client_module, "POLL_TRANSIENT_ATTEMPTS", 7)
        client, _transport, slept = api(*[HostingError("down")] * 6, ok(LIVE))
        client.await_release("rel_1")
        assert slept == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    def test_a_run_of_failures_says_the_upload_finished(self) -> None:
        attempts = client_module.POLL_TRANSIENT_ATTEMPTS
        client, transport, _ = api(*[HostingError("down")] * attempts)
        with pytest.raises(HostingError) as caught:
            client.await_release("rel_1")
        assert str(caught.value) == VERIFY_CONTACT_LOST_MESSAGE
        assert "recognises its own upload" in str(caught.value)
        assert len(transport.requests) == attempts

    def test_an_expired_sign_in_is_not_retried(self) -> None:
        client, transport, _ = api(HttpResponse(401, b"{}"))
        with pytest.raises(AuthRequiredError):
            client.await_release("rel_1")
        assert len(transport.requests) == 1

    def test_a_structured_refusal_is_not_retried(self) -> None:
        client, transport, _ = api(
            refusal(404, "release_not_found", "Unknown release.")
        )
        with pytest.raises(PublishRefusedError):
            client.await_release("rel_1")
        assert len(transport.requests) == 1

    def test_a_hiccup_mid_verification_is_retried_too(self) -> None:
        client, transport, _ = api(
            ok({"state": "verifying"}), HostingError("reset"), ok(LIVE)
        )
        assert client.await_release("rel_1").public_url == "https://maps.example/m"
        assert len(transport.requests) == 3


# ---------------------------------------------------------------------------
# Adopting this client's own upload at the next publish
# ---------------------------------------------------------------------------

PAGE_HTML = "<om-map></om-map>"


@pytest.fixture
def built(tmp_path: Path) -> ArtifactResult:
    source = tmp_path / "build"
    source.mkdir()
    (source / "index.html").write_text(PAGE_HTML, encoding="utf-8")
    return ArtifactResult(
        entry_path=source / "index.html",
        mode=OutputMode.FOLDER,
        files=(ArtifactFile(source / "index.html", len(PAGE_HTML)),),
        runtime_version="0.6.20",
    )


def manifest() -> PublishManifest:
    return PublishManifest(
        schemaVersion=1,
        kind=MANIFEST_KIND,
        entry="index.html",
        producer=ProducerInfo(name="qgis2webmap", version="1.4.0"),
        runtime=RuntimeInfo(name="onlymap", version="0.6.20", sha256="c" * 64),
        files=[
            ManifestFile(
                path="index.html",
                role="page",
                mediaType="text/html",
                size=0,
                sha256="0" * 64,
            )
        ],
        externalOrigins=[],
        runtimeScriptSources=[],
        title="T",
    )


def start_reply(request: HttpRequest) -> HttpResponse:
    return ok({"releaseId": "rel_new", "mapId": MAP_ID, "releaseN": 6, "uploads": []})


def exporter_for(transport: FakeTransport, **kwargs) -> HostedExporter:
    return HostedExporter(
        HostingClient(
            "desk_x", "https://api.example", transport, sleeper=lambda _s: None
        ),
        title="T",
        manifest=manifest(),
        **kwargs,
    )


def start_body(transport: FakeTransport) -> dict:
    (request,) = [
        r for r in transport.requests if r.url.endswith("/maps/publish/start")
    ]
    return json.loads(request.body or b"{}")


class TestReconcilePendingRelease:
    def test_our_own_live_release_is_adopted_before_the_start(
        self, built, tmp_path
    ) -> None:
        """The desync: release 5 went live while the plugin had lost contact,
        the project still says 4. Sending 4 is what produced the false conflict."""
        transport = FakeTransport(ok(LIVE), start_reply)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )

        exporter.prepare(built, tmp_path / "upload")

        assert transport.requests[0].url == "https://api.example/maps/releases/rel_mine"
        assert start_body(transport)["releaseN"] == 5
        outcome = exporter.reconciliation
        assert outcome is not None and outcome.settled
        assert (outcome.adopted_map_id, outcome.adopted_release_n) == (MAP_ID, 5)

    def test_a_first_publish_that_went_live_is_adopted_as_the_map(
        self, built, tmp_path
    ) -> None:
        """Without this the project never learned its map's id, and the next Host
        created a SECOND map."""
        transport = FakeTransport(ok(LIVE), start_reply)
        exporter = exporter_for(transport, pending_release_id="rel_mine")

        exporter.prepare(built, tmp_path / "upload")

        body = start_body(transport)
        assert body["mapId"] == MAP_ID
        assert body["releaseN"] == 5

    def test_adoption_never_lowers_the_stored_release(self, built, tmp_path) -> None:
        transport = FakeTransport(ok(LIVE), start_reply)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=8, pending_release_id="rel_mine"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert start_body(transport)["releaseN"] == 8

    def test_a_colleague_after_us_is_still_a_conflict(self, built, tmp_path) -> None:
        """Adopting our release 5 does not paper over a colleague's release 6."""
        conflict = reply(
            409,
            {
                "error": {
                    "code": "release_conflict",
                    "message": "older",
                    "details": {"currentRelease": 6, "publishedBy": "sam"},
                }
            },
        )
        transport = FakeTransport(ok(LIVE), conflict)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )
        with pytest.raises(PublishConflictError) as caught:
            exporter.prepare(built, tmp_path / "upload")
        assert caught.value.current_release == 6
        assert exporter.release_n == 5

    def test_a_release_still_verifying_stays_pending(self, built, tmp_path) -> None:
        transport = FakeTransport(
            ok({"state": "verifying", "mapId": MAP_ID}), start_reply
        )
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is not None
        assert not exporter.reconciliation.settled
        assert start_body(transport)["releaseN"] == 4

    @pytest.mark.parametrize("server_state", ["failed", "draft"])
    def test_a_release_that_never_went_live_is_forgotten(
        self, built, tmp_path, server_state
    ) -> None:
        transport = FakeTransport(
            ok({"state": server_state, "mapId": MAP_ID}), start_reply
        )
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is not None
        assert exporter.reconciliation.settled
        assert exporter.reconciliation.adopted_release_n is None
        assert start_body(transport)["releaseN"] == 4

    def test_an_unknown_release_is_forgotten(self, built, tmp_path) -> None:
        transport = FakeTransport(refusal(404, "release_not_found"), start_reply)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_x"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is not None and exporter.reconciliation.settled

    def test_a_live_release_on_a_map_we_detached_from_is_not_adopted(
        self, built, tmp_path
    ) -> None:
        transport = FakeTransport(ok({**LIVE, "mapId": OTHER_MAP}), start_reply)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is not None
        assert exporter.reconciliation.adopted_map_id is None
        assert start_body(transport)["mapId"] == MAP_ID

    def test_failing_to_ask_changes_nothing(self, built, tmp_path) -> None:
        transport = FakeTransport(HostingError("offline"), start_reply)
        exporter = exporter_for(
            transport, map_id=MAP_ID, release_n=4, pending_release_id="rel_mine"
        )
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is not None
        assert not exporter.reconciliation.settled
        assert start_body(transport)["releaseN"] == 4

    def test_an_expired_sign_in_is_raised(self, built, tmp_path) -> None:
        transport = FakeTransport(HttpResponse(401, b"{}"))
        exporter = exporter_for(transport, map_id=MAP_ID, pending_release_id="rel_mine")
        with pytest.raises(AuthRequiredError):
            exporter.prepare(built, tmp_path / "upload")

    def test_nothing_pending_asks_nothing(self, built, tmp_path) -> None:
        transport = FakeTransport(start_reply)
        exporter = exporter_for(transport, map_id=MAP_ID, release_n=4)
        exporter.prepare(built, tmp_path / "upload")
        assert exporter.reconciliation is None
        assert len(transport.requests) == 1


# ---------------------------------------------------------------------------
# How far a publish got, for Cancel
# ---------------------------------------------------------------------------


class TestStages:
    def test_the_stage_follows_the_publish(self, built, tmp_path) -> None:
        seen: list[str] = []
        transport = FakeTransport(
            ok(
                {
                    "releaseId": "rel_1",
                    "mapId": MAP_ID,
                    "releaseN": 1,
                    "uploads": [
                        {
                            "sha256": "x",
                            "upload": {
                                "mode": "presigned",
                                "url": "https://u.example/1",
                            },
                        }
                    ],
                }
            ),
            HttpResponse(200, b""),
            ok({"releaseId": "rel_1"}),
            ok(LIVE),
        )
        exporter = exporter_for(transport)
        assert exporter.stage == STAGE_LOCAL
        prepared = exporter.prepare(built, tmp_path / "upload")
        assert exporter.stage == STAGE_RESERVED
        # Every page file is offered a slot here, whatever its digest.
        prepared = type(prepared)(
            start=type(prepared.start)(
                release_id="rel_1",
                map_id=MAP_ID,
                release_n=1,
                uploads=tuple(
                    type(prepared.start.uploads[0])(
                        sha256=entry["sha256"], url="https://u.example/1"
                    )
                    for entry in prepared.manifest["files"]
                ),
            ),
            manifest=prepared.manifest,
            files=prepared.files,
            title=prepared.title,
        )
        exporter.on_progress = lambda _p, _m: seen.append(exporter.stage)
        exporter.publish(prepared)
        assert STAGE_UPLOADING in seen
        assert seen[-1] == STAGE_VERIFYING

    def test_cancel_says_what_is_true_at_each_stage(self) -> None:
        for stage in (STAGE_LOCAL, STAGE_RESERVED):
            assert "Nothing was published" in cancelled_publish_text(stage)
        assert "Nothing new was published" in cancelled_publish_text(STAGE_UPLOADING)
        for stage in (STAGE_COMPLETING, STAGE_VERIFYING):
            text = cancelled_publish_text(stage)
            assert "most likely update anyway" in text
            assert "Nothing was" not in text


# ---------------------------------------------------------------------------
# Smaller wording fixes
# ---------------------------------------------------------------------------


def minimal_manifest() -> PublishManifest:
    return manifest()


class TestRefusalsAreNotSignInFailures:
    def test_another_organisations_map_is_a_refusal_not_an_expired_sign_in(
        self,
    ) -> None:
        """It used to say "Your NIKA sign-in is no longer valid", and signing in
        again changed nothing."""
        client, _transport, _ = api(
            refusal(403, "map_forbidden", "This map belongs to another organisation.")
        )
        with pytest.raises(PublishRefusedError) as caught:
            client.start_publish(minimal_manifest(), map_id=MAP_ID)
        assert caught.value.code == "map_forbidden"

    @pytest.mark.parametrize(
        ("status", "code"), [(409, "map_taken_down"), (404, "map_not_found")]
    )
    def test_the_other_dead_map_answers_are_refusals_too(self, status, code) -> None:
        client, _transport, _ = api(refusal(status, code))
        with pytest.raises(PublishRefusedError) as caught:
            client.start_publish(minimal_manifest(), map_id=MAP_ID)
        assert caught.value.code == code

    def test_an_under_scoped_token_is_still_a_sign_in_failure(self) -> None:
        client, _transport, _ = api(refusal(403, "insufficient_scope"))
        with pytest.raises(AuthRequiredError):
            client.start_publish(minimal_manifest(), map_id=MAP_ID)


class TestLostReleaseRace:
    def test_the_allocation_race_is_not_the_stale_copy_question(self) -> None:
        """Two copies publishing at the same instant: the loser was told it was
        "already at release 0 ... your copy is older" over Publish anyway."""
        client, _transport, _ = api(
            refusal(
                409,
                "release_conflict",
                "Another publish of this map started at the same moment. Try again.",
            )
        )
        with pytest.raises(PublishRefusedError) as caught:
            client.start_publish(minimal_manifest(), map_id=MAP_ID, release_n=3)
        assert "same moment" in str(caught.value)
        assert "release 0" not in str(caught.value)


class TestOfflineInPlainEnglish:
    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            urllib.error.URLError("[Errno -2] Name or service not known"),
            TimeoutError("timed out"),
        ],
    )
    def test_no_errno_reaches_the_user(self, monkeypatch, error) -> None:
        def refuse(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(client_module.urllib.request, "urlopen", refuse)
        with pytest.raises(HostingError) as caught:
            urllib_transport(
                HttpRequest(method="GET", url="https://api-dev.nika.eco/maps")
            )
        message = str(caught.value)
        assert "Errno" not in message
        assert "Could not connect to NIKA's servers (api-dev.nika.eco)" in message
        assert "online" in message


# ---------------------------------------------------------------------------
# What the project remembers
# ---------------------------------------------------------------------------


class FakeProject:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def readEntry(self, scope: str, key: str, default: str = ""):  # noqa: N802
        value = self.entries.get((scope, key))
        return (default, False) if value is None else (value, True)

    def writeEntry(self, scope: str, key: str, value: str) -> bool:  # noqa: N802
        self.entries[(scope, key)] = value
        return True

    def removeEntry(self, scope: str, key: str) -> bool:  # noqa: N802
        self.entries.pop((scope, key), None)
        return True


class TestProjectEntries:
    def test_a_pending_release_round_trips_and_clears(self) -> None:
        project = FakeProject()
        assert load_hosted_pending_release(project) == ""
        save_hosted_pending_release(project, " rel_1 ")
        assert load_hosted_pending_release(project) == "rel_1"
        clear_hosted_pending_release(project)
        assert load_hosted_pending_release(project) == ""

    def test_detaching_leaves_a_project_that_was_never_published(self) -> None:
        project = FakeProject()
        save_hosted_map_id(project, MAP_ID)
        save_hosted_release_n(project, 4)
        save_hosted_pending_release(project, "rel_1")

        detach_hosted_map(project)

        assert load_hosted_map_id(project) == ""
        assert load_hosted_release_n(project) is None
        assert load_hosted_pending_release(project) == ""
        for key in (
            KEY_HOSTED_MAP_ID,
            KEY_HOSTED_RELEASE_N,
            KEY_HOSTED_PENDING_RELEASE,
        ):
            assert (SCOPE, key) not in project.entries
