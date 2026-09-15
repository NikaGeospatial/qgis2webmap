"""The publish handshake, against a transport that never opens a socket.

Every test here asserts on `HttpRequest` objects. Nothing in this file may
reach the network, and the fake transport is what guarantees it: a call the
test did not anticipate raises rather than escaping to `urllib`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest
from hosting_fakes import FakeTransport, ok

from nika_onlymap_exporter.hosting.client import (
    ALLOW_HTTP_LOOPBACK_ENV,
    AuthRequiredError,
    HostingClient,
    HostingError,
    HttpResponse,
    PublishConflictError,
    PublishRefusedError,
    UploadTarget,
    _required_int,
    _upload_target,
    insecure_loopback_base,
    is_loopback_http_url,
    require_https,
    require_secure_url,
    resolve_api_base,
)
from nika_onlymap_exporter.hosting.manifest import (
    MANIFEST_KIND,
    ManifestFile,
    ProducerInfo,
    PublishManifest,
    RuntimeInfo,
)

PAGE_SHA = "a" * 64
DATA_SHA = "b" * 64

MANIFEST = PublishManifest(
    schemaVersion=1,
    kind=MANIFEST_KIND,
    entry="index.html",
    producer=ProducerInfo(name="qgis2webmap", version="1.4.0"),
    runtime=RuntimeInfo(name="onlymap", version="0.7.4", sha256="c" * 64),
    files=[
        ManifestFile(
            path="index.html",
            role="page",
            mediaType="text/html",
            size=42130,
            sha256=PAGE_SHA,
        ),
        ManifestFile(
            path="data/points.geojson",
            role="data",
            mediaType="application/geo+json",
            size=910,
            sha256=DATA_SHA,
        ),
    ],
    externalOrigins=[],
    runtimeScriptSources=[],
    title="Test Map",
)


def start_payload(*digests: str, **overrides: object) -> dict:
    """A `start` reply offering an upload slot for exactly these digests."""
    payload: dict = {
        "releaseId": "rel_1",
        "mapId": "m" * 25,
        "releaseN": 3,
        "uploads": [
            {
                "sha256": digest,
                "upload": {
                    "mode": "presigned",
                    "url": f"https://uploads.example/{digest[:4]}",
                },
            }
            for digest in digests
        ],
    }
    payload.update(overrides)
    return payload


def client(*responses: object) -> tuple[HostingClient, FakeTransport]:
    transport = FakeTransport(*responses)
    return (
        HostingClient(
            "desk_x", "https://api.example", transport, sleeper=lambda _s: None
        ),
        transport,
    )


def body_of(request) -> dict:
    return json.loads(request.body or b"{}")


class TestHttpsOnly:
    """A downgrade would put the map and the bearer token on the wire."""

    def test_an_http_api_base_is_refused(self) -> None:
        with pytest.raises(HostingError, match="HTTPS"):
            resolve_api_base("http://api.example")

    def test_the_default_base_is_production_over_https(self, monkeypatch) -> None:
        monkeypatch.delenv("NIKA_API_BASE", raising=False)
        assert resolve_api_base().startswith("https://")

    def test_an_environment_override_is_still_checked(self, monkeypatch) -> None:
        monkeypatch.setenv("NIKA_API_BASE", "http://localhost:8787")
        with pytest.raises(HostingError, match="HTTPS"):
            resolve_api_base()

    @pytest.mark.parametrize(
        "url",
        ["http://uploads.example/x", "ftp://uploads.example/x", "//uploads/x"],
    )
    def test_a_non_https_presigned_url_is_refused(self, url: str) -> None:
        """The server produced it, which is not a property this client verifies."""
        api, _transport = client(
            ok(
                start_payload(
                    **{
                        "uploads": [
                            {
                                "sha256": PAGE_SHA,
                                "upload": {"mode": "presigned", "url": url},
                            }
                        ]
                    }
                )
            )
        )
        with pytest.raises(HostingError, match="HTTPS"):
            api.start_publish(MANIFEST)

    def test_uploading_to_a_plain_http_url_is_refused(self) -> None:
        api, _transport = client()
        with pytest.raises(HostingError, match="HTTPS"):
            api.upload(UploadTarget(PAGE_SHA, "http://uploads/x"), b"x")

    def test_require_https_passes_an_https_url_through(self) -> None:
        assert require_https("https://a/b") == "https://a/b"


# Every one of these is a string a substring check would have let through, and
# that is the whole reason the host is parsed rather than matched. Kept as one
# list so that a future loosening of the parse has to walk past all of them.
HOSTILE_LOOPBACK_LOOKALIKES = [
    # A subdomain of an attacker's zone that merely begins with the word.
    "http://localhost.evil.com/maps",
    # The same trick with the address, which a `.startswith` would also pass.
    "http://127.0.0.1.evil.com/maps",
    # Userinfo: the loopback name is a username, and the host is after the `@`.
    "http://localhost@evil.com/maps",
    "http://127.0.0.1@evil.com/maps",
    "http://[::1]@evil.com/maps",
    # And with a password, which puts a second `@`-adjacent decoy in the way.
    "http://localhost:pw@evil.com/maps",
    # A name that really does resolve to 127.0.0.1 in public DNS. Resolution is
    # whoever controls the zone's to change, and the answer checked here would
    # not be the answer `urlopen` gets anyway, so the name is refused as a name.
    "http://127.0.0.1.nip.io/maps",
    "http://localtest.me/maps",
    # Alternative spellings of 127.0.0.1 that resolvers accept and the address
    # parser does not. Refusing them is stricter than necessary and correct.
    "http://2130706433/maps",
    "http://0177.0.0.1/maps",
    "http://127.1/maps",
    # An ordinary remote host, which is what the exemption exists to keep out.
    "http://api.example/maps",
    "http://10.0.0.1/maps",
    # Not even HTTP.
    "ftp://localhost/maps",
    "//localhost/maps",
]

# The forms a developer actually types at the local stack.
GENUINE_LOOPBACK_URLS = [
    "http://localhost:8787",
    "http://localhost",
    "http://LOCALHOST:8787/maps",
    # A fully qualified `localhost.` names the same host.
    "http://localhost.:8787/maps",
    "http://127.0.0.1:8787/maps",
    # The whole of 127.0.0.0/8 is loopback, not just the first address.
    "http://127.9.9.9:8787/maps",
    "http://[::1]:8787/maps",
]


class TestLoopbackExemption:
    """Plain HTTP to this machine, opted into by name, and nowhere else.

    The local dev stack serves `http://localhost:8787` and cannot serve
    anything else, so without this the publish button can never be pressed on
    a developer's machine. Every test here is about keeping that narrow.
    """

    def test_it_is_off_unless_the_variable_is_set(self, monkeypatch) -> None:
        """The default has to be refusal; a developer opts in, nobody else."""
        monkeypatch.delenv(ALLOW_HTTP_LOOPBACK_ENV, raising=False)
        with pytest.raises(HostingError, match="HTTPS"):
            resolve_api_base("http://localhost:8787")

    def test_setting_the_api_base_does_not_grant_it(self, monkeypatch) -> None:
        """The opt-in must not be a side effect of pointing somewhere else.

        Someone aiming the plugin at a staging host over HTTP is exactly the
        person who must not acquire this: their traffic crosses a network.
        """
        monkeypatch.delenv(ALLOW_HTTP_LOOPBACK_ENV, raising=False)
        monkeypatch.setenv("NIKA_API_BASE", "http://localhost:8787")
        with pytest.raises(HostingError, match="HTTPS"):
            resolve_api_base()

    @pytest.mark.parametrize("value", ["", "0", "true", "yes", "on", " 1", "1 "])
    def test_only_the_exact_value_switches_it_on(self, monkeypatch, value: str) -> None:
        """`0` and `false` are what people write when they mean off."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, value)
        with pytest.raises(HostingError, match="HTTPS"):
            resolve_api_base("http://localhost:8787")

    @pytest.mark.parametrize("url", GENUINE_LOOPBACK_URLS)
    def test_a_loopback_base_is_allowed_once_opted_in(
        self, monkeypatch, url: str
    ) -> None:
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        assert require_secure_url(url) == url
        assert is_loopback_http_url(url)

    @pytest.mark.parametrize("url", HOSTILE_LOOPBACK_LOOKALIKES)
    def test_a_lookalike_host_is_refused_even_when_opted_in(
        self, monkeypatch, url: str
    ) -> None:
        """The reason the host is parsed instead of matched as text."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        assert not is_loopback_http_url(url)
        with pytest.raises(HostingError, match="HTTPS"):
            require_secure_url(url)

    def test_a_malformed_authority_is_refused_rather_than_raising(
        self, monkeypatch
    ) -> None:
        """An unparseable URL is not a loopback URL, and must not be a crash."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        assert not is_loopback_http_url("http://[::1/maps")
        assert not is_loopback_http_url("http://localhost:notaport/maps")

    def test_https_is_untouched_by_the_exemption(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        assert require_secure_url("https://api.example/x") == "https://api.example/x"

    def test_the_whole_handshake_runs_against_the_local_stack(
        self, monkeypatch
    ) -> None:
        """The point of the exemption: a publish that completes on loopback."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        transport = FakeTransport(
            ok(
                offering(
                    {
                        "mode": "direct",
                        "method": "PUT",
                        "url": "http://localhost:8787/maps/publish/upload/r/abc",
                    }
                )
            ),
            HttpResponse(204, b""),
        )
        api = HostingClient(
            "desk_x", "http://localhost:8787", transport, sleeper=lambda _s: None
        )
        target = api.start_publish(MANIFEST).target_for(PAGE_SHA)
        assert target is not None
        assert target.url == "http://localhost:8787/maps/publish/upload/r/abc"
        api.upload(target, b"x")
        assert transport.requests[0].url == ("http://localhost:8787/maps/publish/start")
        # The `direct` mode still decides the token, and still gets it: this is
        # our own API, not a storage host.
        assert transport.requests[1].headers["Authorization"] == "Bearer desk_x"

    def test_a_presigned_url_is_never_exempt(self, monkeypatch) -> None:
        """It is the server's claim about a storage host, not this machine."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        with pytest.raises(HostingError, match="HTTPS"):
            _upload_target(
                {
                    "sha256": PAGE_SHA,
                    "upload": {
                        "mode": "presigned",
                        "url": "http://localhost:8787/uploads/x",
                    },
                },
                PAGE_SHA,
                "http://localhost:8787",
            )

    def test_a_presigned_put_is_never_exempt_either(self, monkeypatch) -> None:
        """The second gate, in `upload`, is the one an attacker would reach."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        api, _transport = client()
        with pytest.raises(HostingError, match="HTTPS"):
            api.upload(UploadTarget(PAGE_SHA, "http://localhost:8787/uploads/x"), b"x")

    @pytest.mark.parametrize(
        "url",
        [
            "http://uploads.example/x",
            "http://localhost.evil.com/maps/publish/upload/r/abc",
            "http://localhost@evil.com/maps/publish/upload/r/abc",
        ],
    )
    def test_a_direct_url_to_another_host_is_refused_when_opted_in(
        self, monkeypatch, url: str
    ) -> None:
        """The server names this URL in full now, so it could name anything.

        `direct` is the mode the exemption applies to, but the mode is only
        half of it: a control plane that is misconfigured - or no longer ours -
        must not turn an opt-in for this machine into a general plain-HTTP
        allowance.
        """
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        with pytest.raises(HostingError, match="HTTPS"):
            _upload_target(
                {"sha256": PAGE_SHA, "upload": {"mode": "direct", "url": url}},
                PAGE_SHA,
                "http://localhost:8787",
            )

    def test_the_exemption_is_reported_for_the_base_in_use(self, monkeypatch) -> None:
        """What the confirmation wording asks, so it can say so out loud."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        assert insecure_loopback_base("http://localhost:8787") == (
            "http://localhost:8787"
        )
        assert insecure_loopback_base("https://api.example") is None
        assert insecure_loopback_base("http://evil.com") is None

    def test_nothing_is_reported_when_it_is_off(self, monkeypatch) -> None:
        monkeypatch.delenv(ALLOW_HTTP_LOOPBACK_ENV, raising=False)
        monkeypatch.delenv("NIKA_API_BASE", raising=False)
        assert insecure_loopback_base("http://localhost:8787") is None
        assert insecure_loopback_base() is None


class TestStartPublish:
    def test_the_manifest_is_sent_as_it_was_built(self) -> None:
        """Digests are what the server verifies; nothing here may edit them."""
        api, transport = client(ok(start_payload(PAGE_SHA, DATA_SHA)))

        api.start_publish(MANIFEST)

        assert body_of(transport.requests[0])["manifest"] == MANIFEST
        assert transport.requests[0].url == "https://api.example/maps/publish/start"

    def test_the_token_travels_as_a_bearer_header(self) -> None:
        transport = FakeTransport(ok(start_payload()))
        HostingClient("desk_secret", "https://api.example", transport).start_publish(
            MANIFEST
        )
        assert transport.requests[0].headers["Authorization"] == "Bearer desk_secret"

    def test_the_release_identity_comes_back(self) -> None:
        api, _transport = client(ok(start_payload(PAGE_SHA)))
        start = api.start_publish(MANIFEST)
        assert start.release_id == "rel_1"
        assert start.map_id == "m" * 25
        assert start.release_n == 3

    def test_a_map_id_and_release_are_sent_only_when_republishing(self) -> None:
        api, transport = client(ok(start_payload()), ok(start_payload()))

        api.start_publish(MANIFEST)
        api.start_publish(MANIFEST, map_id="m" * 25, release_n=2)

        first = body_of(transport.requests[0])
        second = body_of(transport.requests[1])
        assert "mapId" not in first
        assert "releaseN" not in first
        assert second["mapId"] == "m" * 25
        assert second["releaseN"] == 2

    def test_force_is_absent_unless_it_was_asked_for(self) -> None:
        api, transport = client(ok(start_payload()), ok(start_payload()))

        api.start_publish(MANIFEST)
        api.start_publish(MANIFEST, map_id="m" * 25, release_n=2, force=True)

        assert "force" not in body_of(transport.requests[0])
        assert body_of(transport.requests[1])["force"] is True

    def test_a_manifest_with_no_files_is_refused_before_a_request(self) -> None:
        api, transport = client()
        empty = PublishManifest(**{**MANIFEST, "files": []})
        with pytest.raises(HostingError, match="nothing to publish"):
            api.start_publish(empty)
        assert transport.requests == []

    def test_a_missing_release_id_is_a_readable_failure(self) -> None:
        api, _transport = client(ok(start_payload(**{"releaseId": ""})))
        with pytest.raises(HostingError, match="releaseId"):
            api.start_publish(MANIFEST)

    def test_a_missing_release_number_is_a_readable_failure(self) -> None:
        api, _transport = client(ok(start_payload(**{"releaseN": None})))
        with pytest.raises(HostingError, match="releaseN"):
            api.start_publish(MANIFEST)

    def test_the_free_tier_is_a_null_licence_key_not_an_error(self) -> None:
        api, _transport = client(ok(start_payload(**{"licenseKey": None})))
        start = api.start_publish(MANIFEST)
        assert start.license_key is None
        assert start.is_free_tier

    def test_an_empty_licence_key_reads_as_the_free_tier(self) -> None:
        """A server sending "" must not be mistaken for a licensed account."""
        api, _transport = client(ok(start_payload(**{"licenseKey": ""})))
        assert api.start_publish(MANIFEST).is_free_tier

    def test_a_licence_key_comes_back_intact(self) -> None:
        api, _transport = client(ok(start_payload(**{"licenseKey": "om_live_a.b"})))
        start = api.start_publish(MANIFEST)
        assert start.license_key == "om_live_a.b"
        assert not start.is_free_tier


class TestDeduplication:
    """The store is content addressed. Bytes it holds are never sent again."""

    def test_a_file_with_no_upload_target_has_none(self) -> None:
        api, _transport = client(ok(start_payload(PAGE_SHA)))
        start = api.start_publish(MANIFEST)
        assert start.target_for(PAGE_SHA) is not None
        # Not an error: the server already holds these bytes.
        assert start.target_for(DATA_SHA) is None

    def test_an_empty_uploads_array_is_a_successful_answer(self) -> None:
        """Republishing a map nothing changed in sends no bytes at all."""
        api, _transport = client(ok(start_payload()))
        start = api.start_publish(MANIFEST)
        assert start.uploads == ()
        assert start.target_for(PAGE_SHA) is None

    def test_a_target_carries_the_digest_it_is_for(self) -> None:
        api, _transport = client(ok(start_payload(DATA_SHA)))
        target = api.start_publish(MANIFEST).target_for(DATA_SHA)
        assert target is not None
        assert target.sha256 == DATA_SHA
        assert target.url.startswith("https://")


# The local stack's answer: a relative path, because Miniflare cannot presign.
DIRECT_UPLOAD = {"mode": "direct", "method": "PUT", "url": "/maps/publish/upload/r/abc"}


def offering(upload: object) -> dict:
    """A `start` reply whose single slot carries exactly this `upload` value."""
    return start_payload(**{"uploads": [{"sha256": PAGE_SHA, "upload": upload}]})


class TestUploadModes:
    """`upload.mode` says how the bytes travel. It is read, never inferred."""

    def test_a_presigned_target_keeps_the_absolute_url_it_was_given(self) -> None:
        api, _transport = client(
            ok(
                offering(
                    {
                        "mode": "presigned",
                        "url": "https://uploads.example/x",
                        "headers": {"If-None-Match": "*"},
                    }
                )
            )
        )
        target = api.start_publish(MANIFEST).target_for(PAGE_SHA)
        assert target is not None
        assert target.mode == "presigned"
        assert target.url == "https://uploads.example/x"
        assert target.headers == {"If-None-Match": "*"}
        assert not target.is_authenticated

    def test_a_direct_target_is_joined_onto_the_api_base(self) -> None:
        """It arrives relative, and is only ever a route on the API itself."""
        api, _transport = client(ok(offering(DIRECT_UPLOAD)))
        target = api.start_publish(MANIFEST).target_for(PAGE_SHA)
        assert target is not None
        assert target.mode == "direct"
        assert target.url == "https://api.example/maps/publish/upload/r/abc"
        assert target.is_authenticated

    def test_a_direct_path_cannot_escape_a_plain_http_base(self) -> None:
        """The relative form is exempt from the scheme check; the base is not.

        This is the whole of the relaxation, so it is the thing worth pinning:
        a client that somehow reached a plain-HTTP base still cannot be talked
        into a plain-HTTP upload by a relative path.
        """
        with pytest.raises(HostingError, match="HTTPS"):
            _upload_target(
                {"sha256": PAGE_SHA, "upload": DIRECT_UPLOAD},
                PAGE_SHA,
                "http://localhost:8787",
            )

    def test_an_unrecognised_mode_is_refused_by_name(self) -> None:
        """A mode this plugin cannot perform is a version mismatch, not a default."""
        api, _transport = client(
            ok(offering({"mode": "multipart", "url": "https://uploads.example/x"}))
        )
        with pytest.raises(HostingError, match="multipart"):
            api.start_publish(MANIFEST)

    def test_a_missing_mode_is_refused_rather_than_assumed_presigned(self) -> None:
        api, _transport = client(ok(offering({"url": "https://uploads.example/x"})))
        with pytest.raises(HostingError, match="no upload mode"):
            api.start_publish(MANIFEST)

    def test_a_slot_with_no_upload_object_is_refused(self) -> None:
        """Silently skipping it would fail verification much later, elsewhere."""
        api, _transport = client(
            ok(start_payload(**{"uploads": [{"sha256": PAGE_SHA}]}))
        )
        with pytest.raises(HostingError, match="saying how to send it"):
            api.start_publish(MANIFEST)

    def test_a_direct_put_carries_the_bearer_token(self) -> None:
        """It is an ordinary authenticated endpoint; without the token it is a 401."""
        transport = FakeTransport(ok(offering(DIRECT_UPLOAD)), HttpResponse(204, b""))
        api = HostingClient(
            "desk_secret", "https://api.example", transport, sleeper=lambda _s: None
        )

        target = api.start_publish(MANIFEST).target_for(PAGE_SHA)
        assert target is not None
        api.upload(target, b"hello")

        assert transport.requests[1].headers["Authorization"] == "Bearer desk_secret"

    def test_a_presigned_put_from_the_same_client_carries_none(self) -> None:
        """The pair, in one place: only the mode decides, never the URL's shape."""
        presigned = {"mode": "presigned", "url": "https://uploads.example/x"}
        transport = FakeTransport(ok(offering(presigned)), HttpResponse(200, b""))
        api = HostingClient(
            "desk_secret", "https://api.example", transport, sleeper=lambda _s: None
        )

        target = api.start_publish(MANIFEST).target_for(PAGE_SHA)
        assert target is not None
        api.upload(target, b"hello")

        assert "Authorization" not in transport.requests[1].headers
        assert "desk_secret" not in json.dumps(dict(transport.requests[1].headers))


# The five fields a 409 carries, as one dict, so the two wire shapes below are
# provably the same content in two places rather than two hand-typed literals
# that could drift apart and make the equality test meaningless.
CONFLICT_DETAILS = {
    "currentRelease": 9,
    "publishedBy": "sam@example.org",
    "publishedAt": "2026-09-01T10:11:12Z",
    "wasRollback": False,
    "rolledBackTo": None,
}


def conflict_body(nested: bool = True, flat: bool = True) -> bytes:
    """A 409 body in either shape, or in both as the server sends it today.

    `nested` is `error.details`, which is canonical and what every route in the
    API uses. `flat` is the deprecated duplicate at the top level, kept by the
    server until the oldest plugin in the field reads the nested form.
    """
    body: dict = {}
    if nested:
        body["error"] = {
            "code": "release_conflict",
            "message": "This map is already at release 9. Your copy is older.",
            "details": dict(CONFLICT_DETAILS),
        }
    if flat:
        body.update(CONFLICT_DETAILS)
    return json.dumps(body).encode("utf-8")


def conflict_from(body: bytes) -> PublishConflictError:
    api, _transport = client(HttpResponse(409, body))
    with pytest.raises(PublishConflictError) as caught:
        api.start_publish(MANIFEST, map_id="m" * 25, release_n=4)
    return caught.value


def attributes_of(conflict: PublishConflictError) -> dict:
    return {
        "message": str(conflict),
        "current_release": conflict.current_release,
        "published_by": conflict.published_by,
        "published_at": conflict.published_at,
        "was_rollback": conflict.was_rollback,
        "rolled_back_to": conflict.rolled_back_to,
    }


class TestConflict:
    """Someone else published while this plugin was not looking."""

    CONFLICT = conflict_body()

    def test_a_409_is_raised_with_the_servers_account_of_it(self) -> None:
        api, _transport = client(HttpResponse(409, self.CONFLICT))

        with pytest.raises(PublishConflictError) as caught:
            api.start_publish(MANIFEST, map_id="m" * 25, release_n=4)

        conflict = caught.value
        assert conflict.current_release == 9
        assert conflict.published_by == "sam@example.org"
        assert conflict.published_at == "2026-09-01T10:11:12Z"
        assert conflict.was_rollback is False
        assert conflict.rolled_back_to is None
        assert "Nothing was published" in str(conflict)

    def test_a_rollback_conflict_says_so(self) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "release_conflict",
                    "message": "This map was rolled back to release 3.",
                    "details": {
                        "currentRelease": 3,
                        "wasRollback": True,
                        "rolledBackTo": 3,
                    },
                }
            }
        ).encode("utf-8")
        api, _transport = client(HttpResponse(409, body))

        with pytest.raises(PublishConflictError) as caught:
            api.start_publish(MANIFEST, map_id="m" * 25, release_n=5)

        assert caught.value.was_rollback is True
        assert caught.value.rolled_back_to == 3
        assert "rolled back" in str(caught.value)

    def test_the_override_is_the_same_call_with_force(self) -> None:
        api, transport = client(
            HttpResponse(409, self.CONFLICT), ok(start_payload(PAGE_SHA))
        )

        with pytest.raises(PublishConflictError):
            api.start_publish(MANIFEST, map_id="m" * 25, release_n=4)
        api.start_publish(MANIFEST, map_id="m" * 25, release_n=4, force=True)

        assert transport.requests[1].url == transport.requests[0].url
        assert body_of(transport.requests[1])["force"] is True

    def test_a_conflict_is_still_a_hosting_error(self) -> None:
        """Callers that only catch `HostingError` must not see a crash."""
        api, _transport = client(HttpResponse(409, self.CONFLICT))
        with pytest.raises(HostingError):
            api.start_publish(MANIFEST)


class TestConflictWireShapes:
    """The 409 in both places it can be written, producing one error.

    `error.details` is canonical and the flat top-level copy is a deprecated
    duplicate the server still emits. This pair is what makes removing the
    fallback a deliberate act: the day the duplicate goes, the flat-only test
    below fails and somebody has to decide, rather than the reader quietly
    starting to report release 0 and no attribution - which reads to a
    publisher as a colleague having overwritten their work.
    """

    def test_the_canonical_and_the_deprecated_shapes_agree(self) -> None:
        nested = conflict_from(conflict_body(nested=True, flat=False))
        flat = conflict_from(conflict_body(nested=False, flat=True))
        assert attributes_of(nested) == attributes_of(flat)

    def test_both_together_is_what_the_server_sends_today(self) -> None:
        both = conflict_from(conflict_body())
        alone = conflict_from(conflict_body(nested=True, flat=False))
        assert attributes_of(both) == attributes_of(alone)

    def test_the_nested_form_is_read_when_the_two_disagree(self) -> None:
        """Which one wins, asserted rather than left to dict ordering.

        The two can only disagree through a server bug, but if they ever do,
        the canonical location is the one to believe - and a test that did not
        say so would pass whichever way the merge happened to fall.
        """
        body = json.dumps(
            {
                "currentRelease": 2,
                "publishedBy": "stale@example.org",
                "error": {
                    "code": "release_conflict",
                    "message": "already at release 9",
                    "details": dict(CONFLICT_DETAILS),
                },
            }
        ).encode("utf-8")
        conflict = conflict_from(body)
        assert conflict.current_release == 9
        assert conflict.published_by == "sam@example.org"

    def test_a_409_with_nothing_in_it_still_raises_the_question(self) -> None:
        """A body we cannot read is still a conflict; the dialog has to ask."""
        conflict = conflict_from(b"{}")
        assert conflict.current_release == 0
        assert "Nothing was published" in str(conflict)


class TestStructuredRefusals:
    """The other three refusals `start` can answer with.

    Each carries something in `error.details` the publisher needs in order to
    do anything about it. Surfacing only the message would leave the plugin
    unable to say how many maps the plan holds, how far over the size cap this
    map is, or when a rate limit lifts.
    """

    def refusal(self, status: int, code: str, message: str, details: dict):
        body = json.dumps(
            {"error": {"code": code, "message": message, "details": details}}
        ).encode("utf-8")
        api, _transport = client(HttpResponse(status, body))
        with pytest.raises(PublishRefusedError) as caught:
            api.start_publish(MANIFEST)
        return caught.value

    def test_the_map_limit_carries_the_limit(self) -> None:
        """A 403, and deliberately not read as an expired sign-in.

        Before the code was read, this status alone decided the answer: a
        publisher who had filled their plan was told their session was no
        longer valid and sent to sign in again, which fixes nothing.
        """
        refused = self.refusal(
            403,
            "map_limit_reached",
            "Your plan hosts 5 maps. Take one down or upgrade.",
            {"limit": 5},
        )
        assert refused.code == "map_limit_reached"
        assert refused.limit == 5
        assert "5 maps" in str(refused)
        assert not isinstance(refused, AuthRequiredError)

    def test_the_size_refusal_carries_both_numbers(self) -> None:
        refused = self.refusal(
            413,
            "map_too_large",
            "This map is 12.4 MB and the limit is 10 MB.",
            {"limitMb": 10, "actualMb": 12.4},
        )
        assert refused.code == "map_too_large"
        assert refused.limit_mb == 10.0
        # Not rounded to a whole number: "this map is 12 MB and the limit is
        # 10 MB" would be a different, and wrong, sentence.
        assert refused.actual_mb == 12.4

    def test_the_rate_limit_carries_the_instant_not_just_the_clock_time(
        self,
    ) -> None:
        """The message says 14:05 UTC; only the detail says which day."""
        refused = self.refusal(
            429,
            "publish_rate_limited",
            "You have started 20 publishes in the last hour. "
            "Try again after 14:05 UTC.",
            {"retryAt": "2026-09-11T14:05:00.000Z"},
        )
        assert refused.code == "publish_rate_limited"
        assert refused.retry_at == "2026-09-11T14:05:00.000Z"

    def test_an_unknown_refusal_still_arrives_as_one(self) -> None:
        """A code this plugin has never heard of is not a reason to lose the
        server's explanation."""
        refused = self.refusal(400, "something_new", "Nope.", {})
        assert refused.code == "something_new"
        assert refused.limit is None
        assert refused.retry_at == ""
        assert "Nope." in str(refused)

    def test_a_refusal_is_still_a_hosting_error(self) -> None:
        body = json.dumps(
            {"error": {"code": "map_limit_reached", "message": "full", "details": {}}}
        ).encode("utf-8")
        api, _transport = client(HttpResponse(403, body))
        with pytest.raises(HostingError):
            api.start_publish(MANIFEST)

    def test_an_expired_token_is_still_a_sign_in_problem(self) -> None:
        """The whitelist cuts one way only: an auth code on a 401 or 403 must
        not be re-read as a publishing refusal."""
        body = json.dumps(
            {
                "error": {
                    "code": "unauthorized",
                    "message": "This desktop session is no longer valid.",
                    "details": {},
                }
            }
        ).encode("utf-8")
        api, _transport = client(HttpResponse(401, body))
        with pytest.raises(AuthRequiredError):
            api.start_publish(MANIFEST)

    def test_a_403_with_an_auth_code_is_still_a_sign_in_problem(self) -> None:
        body = json.dumps(
            {"error": {"code": "not_a_member", "message": "no", "details": {}}}
        ).encode("utf-8")
        api, _transport = client(HttpResponse(403, body))
        with pytest.raises(AuthRequiredError):
            api.start_publish(MANIFEST)


class TestUpload:
    def test_a_presigned_put_carries_no_authorization_header(self) -> None:
        """The signature is the authorization; the token belongs nowhere else."""
        transport = FakeTransport(HttpResponse(200, b""))
        api = HostingClient("desk_secret", "https://api.example", transport)

        api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"hello")

        request = transport.requests[0]
        assert request.method == "PUT"
        assert request.body == b"hello"
        assert "Authorization" not in request.headers

    def test_the_servers_headers_are_sent_verbatim(self) -> None:
        """They are part of what the signature covers; editing them is a 403."""
        api, transport = client(HttpResponse(200, b""))
        target = UploadTarget(
            PAGE_SHA,
            "https://uploads/x",
            headers={"Content-Type": "text/html", "x-amz-meta-run": "7"},
        )

        api.upload(target, b"hello", content_type="application/octet-stream")

        headers = transport.requests[0].headers
        assert headers["Content-Type"] == "text/html"
        assert headers["x-amz-meta-run"] == "7"
        assert headers["Content-Length"] == "5"

    def test_a_transient_failure_is_retried_and_can_succeed(self) -> None:
        api, transport = client(HttpResponse(503, b"slow down"), HttpResponse(200, b""))
        api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"x")
        assert len(transport.requests) == 2

    def test_a_429_is_retried(self) -> None:
        api, transport = client(HttpResponse(429, b"too many"), HttpResponse(200, b""))
        api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"x")
        assert len(transport.requests) == 2

    def test_exhausted_retries_surface_a_clear_error(self) -> None:
        api, transport = client(*[HttpResponse(500, b"boom")] * 3)

        with pytest.raises(HostingError) as caught:
            api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"x")

        message = str(caught.value)
        assert PAGE_SHA[:12] in message
        assert "3 attempts" in message
        assert "Nothing was published" in message
        assert len(transport.requests) == 3

    def test_a_network_error_is_retried_then_reported(self) -> None:
        api, transport = client(*[HostingError("connection reset")] * 3)
        with pytest.raises(HostingError, match="connection reset"):
            api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"x")
        assert len(transport.requests) == 3

    def test_a_rejected_signature_is_not_retried(self) -> None:
        """Re-PUTting tens of megabytes into a certain 403 helps nobody."""
        api, transport = client(HttpResponse(403, b"expired"))
        with pytest.raises(HostingError):
            api.upload(UploadTarget(PAGE_SHA, "https://uploads/x"), b"x")
        assert len(transport.requests) == 1


class TestComplete:
    def test_it_carries_the_release_id_and_nothing_else(self) -> None:
        api, transport = client(ok({"releaseId": "rel_1"}))

        assert api.complete("rel_1") == "rel_1"

        assert transport.requests[0].url == "https://api.example/maps/publish/complete"
        assert body_of(transport.requests[0]) == {"releaseId": "rel_1"}
        assert transport.requests[0].headers["Authorization"] == "Bearer desk_x"


class TestAwaitRelease:
    """Uploading and being live are different moments; the server verifies."""

    def test_it_polls_until_the_release_is_live(self) -> None:
        api, transport = client(
            ok({"state": "verifying", "mapId": "m" * 25, "releaseN": 3}),
            ok(
                {
                    "state": "live",
                    "mapId": "m" * 25,
                    "releaseN": 3,
                    "publicUrl": "https://maps.nika.eco/mmm",
                }
            ),
        )

        result = api.await_release("rel_1")

        assert result.public_url == "https://maps.nika.eco/mmm"
        assert result.release_n == 3
        assert result.map_id == "m" * 25
        assert [request.method for request in transport.requests] == ["GET", "GET"]
        assert transport.requests[0].url == "https://api.example/maps/releases/rel_1"

    def test_a_failed_release_surfaces_the_servers_error_verbatim(self) -> None:
        api, _transport = client(
            ok(
                {
                    "state": "failed",
                    "mapId": "m" * 25,
                    "releaseN": 3,
                    "error": "digest mismatch for data/points.geojson",
                }
            )
        )

        with pytest.raises(HostingError) as caught:
            api.await_release("rel_1")

        assert "digest mismatch for data/points.geojson" in str(caught.value)

    def test_a_live_release_with_no_address_is_a_readable_failure(self) -> None:
        api, _transport = client(ok({"state": "live", "mapId": "m" * 25}))
        with pytest.raises(HostingError, match="no address"):
            api.await_release("rel_1")

    def test_waiting_forever_is_not_an_option(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "nika_onlymap_exporter.hosting.client.POLL_TIMEOUT_SECONDS", 0.0
        )
        api, _transport = client(ok({"state": "verifying", "mapId": "m" * 25}))
        with pytest.raises(HostingError, match="still verifying"):
            api.await_release("rel_1")

    def test_every_state_is_reported_to_the_caller(self) -> None:
        api, _transport = client(
            ok({"state": "verifying"}),
            ok({"state": "live", "publicUrl": "https://maps.nika.eco/mmm"}),
        )
        seen: list[str] = []

        api.await_release("rel_1", on_progress=lambda status: seen.append(status.state))

        assert seen == ["verifying", "live"]


class TestFailureReporting:
    def test_a_401_asks_for_a_fresh_sign_in(self) -> None:
        api, _transport = client(HttpResponse(401, b'{"message":"expired"}'))
        with pytest.raises(AuthRequiredError, match="Sign in again"):
            api.start_publish(MANIFEST)

    def test_a_403_asks_for_a_fresh_sign_in_too(self) -> None:
        api, _transport = client(HttpResponse(403, b'{"message":"revoked"}'))
        with pytest.raises(AuthRequiredError):
            api.start_publish(MANIFEST)

    @pytest.mark.parametrize("key", ["message", "error", "detail"])
    def test_the_servers_own_explanation_is_shown(self, key: str) -> None:
        body = json.dumps({key: "plan limit"}).encode("utf-8")
        api, _transport = client(HttpResponse(402, body))
        with pytest.raises(HostingError, match="plan limit"):
            api.start_publish(MANIFEST)

    def test_the_envelope_is_read_before_the_bare_keys(self) -> None:
        """`error.message` is where the API puts it; a refusal with no code is
        still a message worth showing."""
        body = json.dumps({"error": {"message": "plan limit"}}).encode("utf-8")
        api, _transport = client(HttpResponse(402, body))
        with pytest.raises(HostingError, match="plan limit"):
            api.start_publish(MANIFEST)

    def test_a_gateway_that_knows_nothing_of_our_envelope_is_still_quoted(
        self,
    ) -> None:
        """A proxy or load balancer refusing on the way through writes its own
        shape, and its explanation is the most useful thing we have."""
        body = json.dumps({"message": "upstream timed out"}).encode("utf-8")
        api, _transport = client(HttpResponse(504, body))
        with pytest.raises(HostingError, match="upstream timed out"):
            api.start_publish(MANIFEST)

    def test_an_unreadable_reply_does_not_raise_a_json_error(self) -> None:
        api, _transport = client(HttpResponse(200, b"<html>proxy</html>"))
        with pytest.raises(HostingError, match="not readable"):
            api.start_publish(MANIFEST)


class TestReleaseNumberIsNumericOnTheWire:
    """`releaseN` is a JSON number, not a string.

    An earlier client read the equivalent field with a string-only reader,
    which silently saw it as absent and failed EVERY publish on the first call
    with a message blaming the server for omitting a field it had sent. The
    fixtures above mirror the wire format; these pin the reader itself.
    """

    def test_an_integer_is_read(self) -> None:
        assert _required_int({"releaseN": 7}, "releaseN", "x") == 7

    def test_a_numeric_string_is_accepted_too(self) -> None:
        assert _required_int({"releaseN": "7"}, "releaseN", "x") == 7

    def test_a_missing_or_unusable_number_is_a_readable_failure(self) -> None:
        for bad in ({}, {"releaseN": None}, {"releaseN": "latest"}, {"releaseN": True}):
            with pytest.raises(HostingError, match="usable 'releaseN'"):
                _required_int(bad, "releaseN", "starting the upload")
