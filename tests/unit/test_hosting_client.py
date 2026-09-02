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
    ALLOWED_FILENAMES,
    AuthRequiredError,
    HostingClient,
    HostingError,
    HttpResponse,
    UploadFile,
    UploadTarget,
    _required_int,
    require_https,
    resolve_api_base,
)

START_PAYLOAD = {
    "mapId": "map_1",
    "version": 3,
    "uploadUrls": [
        {"filename": "index.html", "url": "https://uploads.example/index"},
    ],
    "licenseKey": None,
    "publicUrl": "https://maps.nika.eco/map_1",
    "expiresAt": "2027-01-01T00:00:00Z",
    "expiresIn": 86400,
}

FINALIZE_PAYLOAD = {
    "mapId": "map_1",
    "publicUrl": "https://maps.nika.eco/map_1",
    "version": 3,
}


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
        transport = FakeTransport(
            ok(
                {
                    **START_PAYLOAD,
                    "uploadUrls": [{"filename": "index.html", "url": url}],
                }
            )
        )
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="HTTPS"):
            client.start_publish("Map", [UploadFile("index.html", 10)])

    def test_uploading_to_a_plain_http_url_is_refused(self) -> None:
        client = HostingClient("desk_x", "https://api.example", FakeTransport())
        with pytest.raises(HostingError, match="HTTPS"):
            client.upload(UploadTarget("index.html", "http://uploads/x"), b"x")

    def test_require_https_passes_an_https_url_through(self) -> None:
        assert require_https("https://a/b") == "https://a/b"


class TestStartPublish:
    def test_the_free_tier_is_a_null_licence_key_not_an_error(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)

        start = client.start_publish("My map", [UploadFile("index.html", 12)])

        assert start.license_key is None
        assert start.is_free_tier
        assert start.map_id == "map_1"
        assert start.public_url == "https://maps.nika.eco/map_1"

    def test_an_empty_licence_key_reads_as_the_free_tier(self) -> None:
        """A server sending "" must not be mistaken for a licensed account."""
        transport = FakeTransport(ok({**START_PAYLOAD, "licenseKey": ""}))
        client = HostingClient("desk_x", "https://api.example", transport)
        assert client.start_publish("M", [UploadFile("index.html", 1)]).is_free_tier

    def test_a_licence_key_comes_back_intact(self) -> None:
        transport = FakeTransport(ok({**START_PAYLOAD, "licenseKey": "om_live_a.b"}))
        client = HostingClient("desk_x", "https://api.example", transport)
        start = client.start_publish("M", [UploadFile("index.html", 1)])
        assert start.license_key == "om_live_a.b"
        assert not start.is_free_tier

    def test_the_token_travels_as_a_bearer_header(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        HostingClient("desk_secret", "https://api.example", transport).start_publish(
            "M", [UploadFile("index.html", 1)]
        )
        assert transport.requests[0].headers["Authorization"] == "Bearer desk_secret"

    def test_a_map_id_is_sent_only_when_republishing(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD), ok(START_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)

        client.start_publish("M", [UploadFile("index.html", 1)])
        client.start_publish("M", [UploadFile("index.html", 1)], map_id="map_1")

        first = json.loads(transport.requests[0].body or b"{}")
        second = json.loads(transport.requests[1].body or b"{}")
        assert "mapId" not in first
        assert second["mapId"] == "map_1"

    def test_the_body_names_each_file_and_its_size(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        HostingClient("desk_x", "https://api.example", transport).start_publish(
            "M", [UploadFile("index.html", 12), UploadFile("thumbnail.png", 34)]
        )
        body = json.loads(transport.requests[0].body or b"{}")
        assert body["files"] == [
            {"filename": "index.html", "sizeBytes": 12},
            {"filename": "thumbnail.png", "sizeBytes": 34},
        ]

    def test_a_missing_map_id_in_the_reply_is_a_readable_failure(self) -> None:
        transport = FakeTransport(ok({**START_PAYLOAD, "mapId": ""}))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="mapId"):
            client.start_publish("M", [UploadFile("index.html", 1)])


class TestFilenameAllowlist:
    @pytest.mark.parametrize("name", ALLOWED_FILENAMES)
    def test_every_allowed_name_is_accepted(self, name: str) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)
        client.start_publish("M", [UploadFile(name, 1)])
        assert transport.requests

    @pytest.mark.parametrize(
        "name", ["README.txt", "index.htm", "data.geojson", "../index.html", "sub/x.js"]
    )
    def test_anything_else_is_refused_before_a_request_is_made(self, name: str) -> None:
        transport = FakeTransport()
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="cannot be published"):
            client.start_publish("M", [UploadFile(name, 1)])
        assert transport.requests == []

    def test_publishing_nothing_is_refused(self) -> None:
        client = HostingClient("desk_x", "https://api.example", FakeTransport())
        with pytest.raises(HostingError, match="nothing to publish"):
            client.start_publish("M", [])


class TestUpload:
    def test_a_presigned_put_carries_no_authorization_header(self) -> None:
        """The signature is the authorization; the token belongs nowhere else."""
        transport = FakeTransport(HttpResponse(200, b""))
        client = HostingClient("desk_secret", "https://api.example", transport)

        client.upload(UploadTarget("index.html", "https://uploads/x"), b"hello")

        request = transport.requests[0]
        assert request.method == "PUT"
        assert request.body == b"hello"
        assert "Authorization" not in request.headers

    def test_a_transient_failure_is_retried_and_can_succeed(self) -> None:
        transport = FakeTransport(
            HttpResponse(503, b"slow down"), HttpResponse(200, b"")
        )
        client = HostingClient("desk_x", "https://api.example", transport)
        client.upload(UploadTarget("index.html", "https://uploads/x"), b"x")
        assert len(transport.requests) == 2

    def test_exhausted_retries_surface_a_clear_error(self) -> None:
        transport = FakeTransport(*[HttpResponse(500, b"boom")] * 3)
        client = HostingClient("desk_x", "https://api.example", transport)

        with pytest.raises(HostingError) as caught:
            client.upload(UploadTarget("index.html", "https://uploads/x"), b"x")

        message = str(caught.value)
        assert "index.html" in message
        assert "3 attempts" in message
        assert "Nothing was published" in message
        assert len(transport.requests) == 3

    def test_a_network_error_is_retried_then_reported(self) -> None:
        transport = FakeTransport(*[HostingError("connection reset")] * 3)
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="connection reset"):
            client.upload(UploadTarget("index.html", "https://uploads/x"), b"x")
        assert len(transport.requests) == 3

    def test_a_rejected_signature_is_not_retried(self) -> None:
        """Re-PUTting tens of megabytes into a certain 403 helps nobody."""
        transport = FakeTransport(HttpResponse(403, b"expired"))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError):
            client.upload(UploadTarget("index.html", "https://uploads/x"), b"x")
        assert len(transport.requests) == 1

    def test_a_missing_upload_slot_is_named(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)
        start = client.start_publish("M", [UploadFile("index.html", 1)])
        with pytest.raises(HostingError, match=r"thumbnail\.png"):
            start.target_for("thumbnail.png")


class TestFinalize:
    def test_it_returns_the_public_url(self) -> None:
        transport = FakeTransport(
            ok(
                {
                    "mapId": "map_1",
                    "publicUrl": "https://maps.nika.eco/map_1",
                    "version": 3,
                    "expiresAt": "2027-01-01T00:00:00Z",
                }
            )
        )
        client = HostingClient("desk_x", "https://api.example", transport)

        result = client.finalize("map_1", "3", title="My map")

        assert result.public_url == "https://maps.nika.eco/map_1"
        assert result.version == 3
        assert json.loads(transport.requests[0].body or b"{}")["title"] == "My map"


class TestFailureReporting:
    def test_a_401_asks_for_a_fresh_sign_in(self) -> None:
        transport = FakeTransport(HttpResponse(401, b'{"message":"expired"}'))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(AuthRequiredError, match="Sign in again"):
            client.start_publish("M", [UploadFile("index.html", 1)])

    def test_the_servers_own_explanation_is_shown(self) -> None:
        transport = FakeTransport(HttpResponse(402, b'{"message":"plan limit"}'))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="plan limit"):
            client.start_publish("M", [UploadFile("index.html", 1)])

    def test_an_unreadable_reply_does_not_raise_a_json_error(self) -> None:
        transport = FakeTransport(HttpResponse(200, b"<html>proxy</html>"))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="not readable"):
            client.start_publish("M", [UploadFile("index.html", 1)])


class TestVersionIsNumericOnTheWire:
    """`version` is a JSON number, not a string.

    The control plane returns it as an integer (`apps/control-plane/src/maps/
    routes.ts`). An earlier client read it with a string-only reader, which
    silently saw it as absent and failed EVERY publish on the first call with a
    message blaming the server for omitting a field it had sent. The fixtures
    above now mirror the wire format; these pin the reader itself.
    """

    def test_an_integer_version_is_read(self) -> None:
        assert _required_int({"version": 7}, "version", "x") == 7

    def test_a_numeric_string_is_accepted_too(self) -> None:
        assert _required_int({"version": "7"}, "version", "x") == 7

    def test_a_missing_or_unusable_version_is_a_readable_failure(self) -> None:
        for bad in ({}, {"version": None}, {"version": "latest"}, {"version": True}):
            with pytest.raises(HostingError, match="usable 'version'"):
                _required_int(bad, "version", "starting the upload")


class TestRuntimePinning:
    """The runtime is named, never uploaded."""

    def test_onlymap_js_is_refused_before_a_round_trip(self) -> None:
        """The server mints no upload slot for it, so sending it is pointless.

        Checked here as well so the refusal costs a message rather than a failed
        publish after the map has already been staged.
        """
        transport = FakeTransport(ok(START_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)
        with pytest.raises(HostingError, match="onlymap.js"):
            client.start_publish("M", [UploadFile("onlymap.js", 8_282_853)])
        assert transport.requests == []

    def test_the_declared_runtime_version_reaches_both_calls(self) -> None:
        # `start` persists nothing, so `finalize` has to declare it again — the
        # answer to THAT call is the one written to the database.
        transport = FakeTransport(ok(START_PAYLOAD), ok(FINALIZE_PAYLOAD))
        client = HostingClient("desk_x", "https://api.example", transport)

        client.start_publish(
            "M", [UploadFile("index.html", 1)], runtime_version="0.6.20"
        )
        client.finalize("map_1", 4, runtime_version="0.6.20")

        assert json.loads(transport.requests[0].body or b"{}")["runtimeVersion"] == "0.6.20"
        assert json.loads(transport.requests[1].body or b"{}")["runtimeVersion"] == "0.6.20"

    def test_no_runtime_version_is_omitted_rather_than_sent_as_null(self) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        HostingClient("desk_x", "https://api.example", transport).start_publish(
            "M", [UploadFile("index.html", 1)]
        )
        assert "runtimeVersion" not in json.loads(transport.requests[0].body or b"{}")

    def test_a_substitution_is_reported_at_start_not_only_at_finalize(self) -> None:
        """So the user hears about it while they can still stop."""
        transport = FakeTransport(
            ok({**START_PAYLOAD, "runtimeVersion": "0.6.20", "runtimeSubstituted": True})
        )
        start = HostingClient("desk_x", "https://api.example", transport).start_publish(
            "M", [UploadFile("index.html", 1)], runtime_version="99.0.0"
        )
        assert start.runtime_version == "0.6.20"
        assert start.runtime_substituted is True

    def test_the_finalize_warning_is_carried_through_verbatim(self) -> None:
        transport = FakeTransport(
            ok({**FINALIZE_PAYLOAD, "runtimeWarning": "built against 99.0.0"})
        )
        result = HostingClient("desk_x", "https://api.example", transport).finalize(
            "map_1", 4
        )
        assert result.runtime_warning == "built against 99.0.0"

    def test_no_warning_reads_as_None_rather_than_an_empty_string(self) -> None:
        transport = FakeTransport(ok(FINALIZE_PAYLOAD))
        result = HostingClient("desk_x", "https://api.example", transport).finalize(
            "map_1", 4
        )
        assert result.runtime_warning is None
