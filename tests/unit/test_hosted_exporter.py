"""Publishing an artifact, and the consent that has to come before it.

The publish path is exercised end to end against a fake transport: staging,
the manifest, the reservation, every PUT, the completion and the wait for the
release to go live. Nothing here opens a socket.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hosting_fakes import FakeTransport, ok

from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    ExportProject,
    GeometryKind,
    OutputMode,
    SourceKind,
)
from nika_onlymap_exporter.core.license_policy import (
    FREE_TIER_MAX_LAYERS,
    detect_violations,
)
from nika_onlymap_exporter.exporters import hosted
from nika_onlymap_exporter.exporters.hosted import (
    HostedExporter,
    PublishCancelledError,
)
from nika_onlymap_exporter.hosting.client import (
    ALLOW_HTTP_LOOPBACK_ENV,
    HostingClient,
    HostingError,
    HttpRequest,
    HttpResponse,
    PublishConflictError,
    UploadFile,
)
from nika_onlymap_exporter.hosting.consent import (
    basemap_warning_text,
    insecure_transport_text,
    publish_consent_text,
    should_warn_truncation,
    truncation_warning_text,
)
from nika_onlymap_exporter.hosting.manifest import (
    MANIFEST_KIND,
    ManifestFile,
    ProducerInfo,
    PublishManifest,
    RuntimeInfo,
)
from nika_onlymap_exporter.writers.onlymap_writer import ArtifactFile, ArtifactResult

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}

PAGE_HTML = '<om-map license-key="om_live_abc.def" basemap="osm"></om-map>'
POINTS_JSON = '{"type":"FeatureCollection","features":[]}'

MAP_ID = "m" * 25

LIVE_PAYLOAD = {
    "state": "live",
    "mapId": MAP_ID,
    "releaseN": 4,
    "publicUrl": "https://maps.nika.eco/map_1",
}


def make_manifest() -> PublishManifest:
    """What the writer hands over.

    Sizes and digests here are deliberately stale: the exporter re-measures the
    staged bytes, and a test that reused these numbers would not notice if it
    stopped doing so.
    """
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
                mediaType="text/html; charset=utf-8",
                size=0,
                sha256="0" * 64,
            ),
            ManifestFile(
                path="data/points.geojson",
                role="data",
                mediaType="application/geo+json",
                size=0,
                sha256="0" * 64,
            ),
        ],
        externalOrigins=[],
        runtimeScriptSources=[],
        title="Test Map",
    )


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def start_answer(*already_stored: str, **overrides: object):
    """A `start` reply built from the manifest the request just carried.

    The handshake is content addressed, so what gets an upload slot depends on
    the digests that were sent. Any path named in `already_stored` is treated
    as bytes the server already holds and is offered no slot.
    """

    def answer(request: HttpRequest) -> HttpResponse:
        manifest = json.loads(request.body or b"{}")["manifest"]
        payload: dict = {
            "releaseId": "rel_1",
            "mapId": MAP_ID,
            "releaseN": 4,
            "uploads": [
                {
                    "sha256": entry["sha256"],
                    "upload": {
                        "mode": "presigned",
                        "url": f"https://uploads.example/{entry['path']}",
                    },
                }
                for entry in manifest["files"]
                if entry["path"] not in already_stored
            ],
        }
        payload.update(overrides)
        return ok(payload)

    return answer


def make_layer(name: str, features: int = 1) -> ExportLayer:
    return ExportLayer(
        layer_id=f"{name}_id",
        name=name,
        geometry_kind=GeometryKind.POLYGON,
        source_kind=SourceKind.FILE,
        feature_count=features,
        geojson=EMPTY_GEOJSON,
    )


@pytest.fixture
def built(tmp_path: Path) -> ArtifactResult:
    """What the writer leaves behind for a folder export, plus a stray file."""
    source = tmp_path / "build"
    (source / "data").mkdir(parents=True)
    (source / "index.html").write_text(PAGE_HTML, encoding="utf-8")
    (source / "data" / "points.geojson").write_text(POINTS_JSON, encoding="utf-8")
    (source / "onlymap.js").write_text("// runtime", encoding="utf-8")
    # `build_artifact` writes one of these for the folder tier. The manifest
    # does not name it, so it must not be staged.
    (source / "README.txt").write_text("open index.html", encoding="utf-8")
    return ArtifactResult(
        entry_path=source / "index.html",
        mode=OutputMode.FOLDER,
        files=(
            ArtifactFile(source / "index.html", len(PAGE_HTML)),
            ArtifactFile(source / "onlymap.js", 10),
        ),
        runtime_version="0.6.20",
    )


def make_exporter(transport: FakeTransport, **kwargs) -> HostedExporter:
    return HostedExporter(
        HostingClient(
            "desk_x", "https://api.example", transport, sleeper=lambda _s: None
        ),
        title=kwargs.pop("title", "My map"),
        manifest=kwargs.pop("manifest", make_manifest()),
        **kwargs,
    )


def puts(transport: FakeTransport) -> dict[str, HttpRequest]:
    return {
        request.url: request
        for request in transport.requests
        if request.method == "PUT"
    }


def full_publish(*already_stored: str) -> FakeTransport:
    """start, one PUT per file that needs one, complete, then live."""
    return FakeTransport(
        start_answer(*already_stored),
        *[HttpResponse(200, b"")] * (2 - len(already_stored)),
        ok({"releaseId": "rel_1"}),
        ok(LIVE_PAYLOAD),
    )


class TestPublishing:
    def test_a_free_tier_publish_succeeds(self, built, tmp_path) -> None:
        """A null `licenseKey` is the free tier, not a failure to publish."""
        transport = full_publish()
        exporter = make_exporter(transport)

        outcome = exporter.export(built, tmp_path / "upload")

        assert outcome.public_url == "https://maps.nika.eco/map_1"
        assert "https://maps.nika.eco/map_1" in outcome.open_instruction
        assert outcome.mode is OutputMode.FOLDER

    def test_exactly_the_manifest_files_are_staged(self, built, tmp_path) -> None:
        """The allowlist that used to decide this silently dropped data files."""
        transport = full_publish()

        make_exporter(transport).export(built, tmp_path / "upload")

        upload_dir = tmp_path / "upload"
        staged = sorted(
            str(path.relative_to(upload_dir))
            for path in upload_dir.rglob("*")
            if path.is_file()
        )
        # `onlymap.js` is in the build directory and is deliberately NOT staged:
        # the 8.3 MB runtime is stored once per version on the server, so
        # uploading a copy per map would be paying for bytes nobody reads. The
        # README is not staged because the manifest does not name it.
        assert staged == ["data/points.geojson", "index.html"]

    def test_the_manifest_travels_with_the_runtime_it_was_built_against(
        self, built, tmp_path
    ) -> None:
        transport = full_publish()
        make_exporter(transport).export(built, tmp_path / "upload")
        manifest = json.loads(transport.requests[0].body or b"{}")["manifest"]
        assert manifest["runtime"]["version"] == "0.6.20"
        assert manifest["entry"] == "index.html"
        assert manifest["kind"] == MANIFEST_KIND

    def test_a_subdirectory_is_staged_where_the_manifest_says(
        self, built, tmp_path
    ) -> None:
        transport = full_publish()
        make_exporter(transport).export(built, tmp_path / "upload")
        assert (tmp_path / "upload" / "data" / "points.geojson").is_file()

    def test_each_file_is_put_with_the_type_the_manifest_gives_it(
        self, built, tmp_path
    ) -> None:
        """A wrong type here is a map the browser downloads instead of drawing."""
        transport = full_publish()

        make_exporter(transport).export(built, tmp_path / "upload")

        sent = puts(transport)
        page = sent["https://uploads.example/index.html"]
        data = sent["https://uploads.example/data/points.geojson"]
        assert page.headers["Content-Type"].startswith("text/html")
        assert data.headers["Content-Type"] == "application/geo+json"
        # The runtime's type is set by the seeding script, not by a publish —
        # nothing here uploads it, so there is no slot to get wrong.
        assert "https://uploads.example/onlymap.js" not in sent

    def test_republishing_sends_the_stored_map_id_and_release(
        self, built, tmp_path
    ) -> None:
        transport = full_publish()

        make_exporter(transport, map_id=MAP_ID, release_n=3).export(
            built, tmp_path / "upload"
        )

        body = json.loads(transport.requests[0].body or b"{}")
        assert body["mapId"] == MAP_ID
        assert body["releaseN"] == 3

    def test_nothing_is_uploaded_before_the_confirmation(self, built, tmp_path) -> None:
        """The gap between reserving and uploading is where the warnings go."""
        transport = FakeTransport(start_answer())
        exporter = make_exporter(transport)

        with pytest.raises(PublishCancelledError):
            exporter.export(built, tmp_path / "upload", confirm=lambda _p: False)

        assert [request.method for request in transport.requests] == ["POST"]
        assert transport.requests[0].url.endswith("/maps/publish/start")

    def test_the_confirmation_sees_the_tier(self, built, tmp_path) -> None:
        transport = FakeTransport(start_answer(**{"licenseKey": None}))
        seen: list[bool] = []

        with pytest.raises(PublishCancelledError):
            make_exporter(transport).export(
                built,
                tmp_path / "upload",
                confirm=lambda prepared: seen.append(prepared.is_free_tier) or False,
            )

        assert seen == [True]

    def test_a_file_the_manifest_names_but_the_build_lacks_is_refused(
        self, built, tmp_path
    ) -> None:
        (built.entry_path.parent / "data" / "points.geojson").unlink()
        transport = FakeTransport()

        with pytest.raises(HostingError, match=r"data/points\.geojson"):
            make_exporter(transport).export(built, tmp_path / "upload")

        assert transport.requests == []

    def test_a_build_without_its_entry_is_refused(self, built, tmp_path) -> None:
        built.entry_path.unlink()
        transport = FakeTransport()
        with pytest.raises(HostingError, match=r"index\.html"):
            make_exporter(transport).export(built, tmp_path / "upload")
        assert transport.requests == []

    def test_a_path_that_climbs_out_of_the_map_is_refused(
        self, built, tmp_path
    ) -> None:
        manifest = make_manifest()
        manifest["files"][1]["path"] = "../escape.txt"
        transport = FakeTransport()
        with pytest.raises(HostingError, match="not a path inside the map"):
            make_exporter(transport, manifest=manifest).export(
                built, tmp_path / "upload"
            )
        assert transport.requests == []

    def test_an_upload_failure_surfaces_a_clear_error(self, built, tmp_path) -> None:
        transport = FakeTransport(
            start_answer(), *[HttpResponse(500, b"storage down")] * 3
        )
        exporter = make_exporter(transport)

        with pytest.raises(HostingError) as caught:
            exporter.export(built, tmp_path / "upload")

        assert "Nothing was published" in str(caught.value)
        # Completion is never reached, so a half-uploaded release cannot go live.
        assert not any(
            request.url.endswith("/maps/publish/complete")
            for request in transport.requests
        )

    def test_a_conflict_reaches_the_caller_intact(self, built, tmp_path) -> None:
        """The dialog needs the server's account of it, not a flattened string."""
        body = json.dumps(
            {
                "currentRelease": 9,
                "publishedBy": "sam@example.org",
                "publishedAt": "2026-09-01T10:11:12Z",
                "wasRollback": False,
            }
        ).encode("utf-8")
        transport = FakeTransport(HttpResponse(409, body))

        with pytest.raises(PublishConflictError) as caught:
            make_exporter(transport, map_id=MAP_ID, release_n=4).export(
                built, tmp_path / "upload"
            )

        assert caught.value.current_release == 9
        assert caught.value.published_by == "sam@example.org"

    def test_the_override_is_carried_into_the_start_call(self, built, tmp_path) -> None:
        transport = full_publish()
        make_exporter(transport, map_id=MAP_ID, release_n=4, force=True).export(
            built, tmp_path / "upload"
        )
        assert json.loads(transport.requests[0].body or b"{}")["force"] is True

    def test_progress_is_reported_for_every_file(self, built, tmp_path) -> None:
        transport = full_publish()
        steps: list[tuple[int, str]] = []
        exporter = make_exporter(
            transport, on_progress=lambda p, m: steps.append((p, m))
        )

        exporter.export(built, tmp_path / "upload")

        messages = [message for _percent, message in steps]
        assert any("index.html" in message for message in messages)
        assert any("points.geojson" in message for message in messages)
        assert messages[-1] == "Verifying..."

    def test_a_failed_verification_is_reported_with_the_servers_reason(
        self, built, tmp_path
    ) -> None:
        transport = FakeTransport(
            start_answer(),
            HttpResponse(200, b""),
            HttpResponse(200, b""),
            ok({"releaseId": "rel_1"}),
            ok({"state": "failed", "error": "digest mismatch for index.html"}),
        )

        with pytest.raises(HostingError, match=r"digest mismatch for index\.html"):
            make_exporter(transport).export(built, tmp_path / "upload")


class TestDeduplication:
    """The server holds the bytes already; sending them again is waste."""

    def test_a_file_with_no_upload_target_is_not_uploaded(
        self, built, tmp_path
    ) -> None:
        transport = full_publish("data/points.geojson")

        make_exporter(transport).export(built, tmp_path / "upload")

        sent = puts(transport)
        assert "https://uploads.example/index.html" in sent
        assert "https://uploads.example/data/points.geojson" not in sent
        # Staged all the same: the manifest still names it, and the staging
        # directory is meant to be the set of bytes that is online.
        assert (tmp_path / "upload" / "data" / "points.geojson").is_file()

    def test_an_empty_uploads_array_publishes_without_a_single_put(
        self, built, tmp_path
    ) -> None:
        """Republishing a map nothing changed in is a metadata-only operation."""
        transport = FakeTransport(
            start_answer("index.html", "data/points.geojson"),
            ok({"releaseId": "rel_1"}),
            ok(LIVE_PAYLOAD),
        )

        outcome = make_exporter(transport, map_id=MAP_ID, release_n=3).export(
            built, tmp_path / "upload"
        )

        assert puts(transport) == {}
        assert outcome.public_url == "https://maps.nika.eco/map_1"
        assert outcome.size_bytes == 0

    def test_the_reported_size_is_what_actually_went_over_the_wire(
        self, built, tmp_path
    ) -> None:
        transport = full_publish("data/points.geojson")

        outcome = make_exporter(transport).export(built, tmp_path / "upload")

        stripped = (tmp_path / "upload" / "index.html").stat().st_size
        assert outcome.size_bytes == stripped

    def test_progress_counts_only_the_files_that_need_sending(
        self, built, tmp_path
    ) -> None:
        transport = full_publish("data/points.geojson")
        steps: list[tuple[int, str]] = []
        make_exporter(transport, on_progress=lambda p, m: steps.append((p, m))).export(
            built, tmp_path / "upload"
        )
        assert any("(1 of 1)" in message for _percent, message in steps)


class TestTruncationWarning:
    """Truncation found by a map's audience rather than its author is the bug."""

    def over_the_cap(self) -> ExportProject:
        layers = [make_layer(f"L{i}") for i in range(FREE_TIER_MAX_LAYERS + 2)]
        return ExportProject(title="Big", layers=tuple(layers))

    def within_the_cap(self) -> ExportProject:
        return ExportProject(title="Small", layers=(make_layer("L0"),))

    def test_it_fires_for_a_free_account_past_the_caps(self) -> None:
        violations = detect_violations(self.over_the_cap())
        assert violations
        assert should_warn_truncation(None, violations)

    def test_it_does_not_fire_for_a_paid_account(self) -> None:
        """The key lifts the limits, so nothing is lost and nothing is said."""
        violations = detect_violations(self.over_the_cap())
        assert not should_warn_truncation("om_live_payload.signature", violations)

    def test_it_does_not_fire_for_a_free_account_within_the_caps(self) -> None:
        assert not should_warn_truncation(
            None, detect_violations(self.within_the_cap())
        )

    def test_the_wording_names_every_layer_that_will_be_cut(self) -> None:
        violations = detect_violations(self.over_the_cap())
        text = truncation_warning_text(violations)
        assert "L5" in text
        assert "L6" in text
        assert "free" in text.lower()


class TestConsentWording:
    """`docs/hosting.md` commits to naming these publicly. Keep it honest."""

    def text(self) -> str:
        # The real upload list: the page and its thumbnail. `onlymap.js` is not
        # in it because the runtime is never uploaded, so naming it here would
        # be telling the user about bytes that do not leave their machine.
        return publish_consent_text(
            "Field survey",
            [
                UploadFile("index.html", 2_000_000),
                UploadFile("thumbnail.png", 2_000_000),
            ],
            feature_count=1234,
            layer_count=3,
        )

    def test_it_names_the_map_and_the_files(self) -> None:
        text = self.text()
        assert "Field survey" in text
        assert "index.html" in text
        assert "thumbnail.png" in text
        assert "3.8 MB" in text

    def test_the_body_itself_never_varies_by_transport(self, monkeypatch) -> None:
        """The banner is a separate string, prepended by the caller."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        monkeypatch.setenv("NIKA_API_BASE", "http://localhost:8787")
        assert "plain HTTP" not in self.text()
        assert "localhost" not in self.text()

    def test_it_says_nothing_about_transport_on_an_ordinary_publish(
        self, monkeypatch
    ) -> None:
        """The banner is for the exemption only; HTTPS needs no announcement."""
        monkeypatch.delenv(ALLOW_HTTP_LOOPBACK_ENV, raising=False)
        monkeypatch.delenv("NIKA_API_BASE", raising=False)
        assert insecure_transport_text() == ""
        assert insecure_transport_text("https://api.nika.eco") == ""

    def test_a_publish_over_plain_http_says_so_to_the_user(self, monkeypatch) -> None:
        """Silent insecure transport is how this ends up on in production."""
        monkeypatch.setenv(ALLOW_HTTP_LOOPBACK_ENV, "1")
        monkeypatch.setenv("NIKA_API_BASE", "http://localhost:8787")
        text = insecure_transport_text()
        assert "plain HTTP" in text
        assert "http://localhost:8787" in text
        assert ALLOW_HTTP_LOOPBACK_ENV in text

    def test_it_does_not_claim_to_upload_the_runtime(self) -> None:
        """The consent must describe what actually leaves the machine."""
        assert "onlymap.js" not in self.text()


class TestTheBasemapWarning:
    """One preset can lose its tiles once the map is hosted; the rest cannot.

    OpenStreetMap asks sites using their tiles to identify themselves, and a
    hosted map cannot: a browser will not let a page set its User-Agent, and
    these maps are served `Referrer-Policy: no-referrer` on purpose, because a
    map's id is its subdomain and any referrer would hand an unlisted map's
    address to the tile provider.
    """

    @pytest.mark.parametrize(
        "preset", ["none", "positron", "liberty", "bright", "dark-matter", "voyager"]
    )
    def test_an_ordinary_basemap_says_nothing(self, preset) -> None:
        # Empty so the caller can prepend it unconditionally, and so the
        # publish everyone actually does reads exactly as it did before.
        assert basemap_warning_text(preset) == ""

    def test_openstreetmap_is_named_with_what_would_break(self) -> None:
        text = basemap_warning_text("osm")

        assert "OpenStreetMap" in text
        # The consequence, not the mechanism: the map still works, the backdrop
        # is what goes. An author reading this is deciding whether they mind.
        assert "may stop loading" in text

    def test_it_names_an_alternative_that_does_work(self) -> None:
        # A warning with no way forward is a warning that gets clicked past.
        text = basemap_warning_text("osm")
        assert any(name in text for name in ("Positron", "Liberty", "Bright"))

    def test_an_unknown_preset_is_silent_rather_than_guessed_at(self) -> None:
        # `build_manifest` already degrades an unrecognised preset to "none", so
        # inventing a warning here would describe a basemap the map will not use.
        assert basemap_warning_text("carto/positron") == ""
        assert basemap_warning_text("") == ""


class TestTheThumbnail:
    """This exporter's own file, so this exporter describes it."""

    def test_it_is_added_to_the_manifest_and_uploaded(self, built, tmp_path) -> None:
        transport = FakeTransport(
            start_answer(),
            *[HttpResponse(200, b"")] * 3,
            ok({"releaseId": "rel_1"}),
            ok(LIVE_PAYLOAD),
        )

        make_exporter(transport, thumbnail_png=b"\x89PNG-bytes").export(
            built, tmp_path / "upload"
        )

        manifest = json.loads(transport.requests[0].body or b"{}")["manifest"]
        entry = [item for item in manifest["files"] if item["path"] == "thumbnail.png"]
        assert entry and entry[0]["mediaType"] == "image/png"
        assert entry[0]["size"] == len(b"\x89PNG-bytes")
        thumb = puts(transport)["https://uploads.example/thumbnail.png"]
        assert thumb.body == b"\x89PNG-bytes"
        assert thumb.headers["Content-Type"] == "image/png"


class TestNoLicenceKeyIsEverUploaded:
    """The server owns the `license-key` attribute; uploads must not carry one.

    `nika-host` sets or strips it on every response, so an embedded key would be
    overwritten regardless. Stripping here makes the contract true on our side:
    a key never sits in our storage, cannot be lifted from a staged folder, and
    cannot be smuggled in by a hand-edited artifact to claim entitlements the
    account has not paid for.
    """

    def test_an_embedded_key_is_removed(self, tmp_path) -> None:
        page = tmp_path / "index.html"
        page.write_text(
            '<om-map license-key="om_live_abc.def" basemap="osm"></om-map>',
            encoding="utf-8",
        )
        hosted._strip_license_key(page)
        text = page.read_text(encoding="utf-8")
        assert "license-key" not in text
        assert "om_live_" not in text
        # Everything else survives untouched.
        assert 'basemap="osm"' in text

    def test_single_quotes_and_odd_spacing_are_handled(self, tmp_path) -> None:
        page = tmp_path / "index.html"
        page.write_text(
            "<om-map  license-key = 'om_live_x.y' ></om-map>", encoding="utf-8"
        )
        hosted._strip_license_key(page)
        assert "om_live_" not in page.read_text(encoding="utf-8")

    def test_a_map_with_no_key_is_left_byte_identical(self, tmp_path) -> None:
        page = tmp_path / "index.html"
        original = '<om-map basemap="osm"></om-map>'
        page.write_text(original, encoding="utf-8")
        hosted._strip_license_key(page)
        assert page.read_text(encoding="utf-8") == original

    def test_a_missing_file_is_not_an_error(self, tmp_path) -> None:
        # `prepare` calls this before it has checked index.html exists; the
        # missing-entry failure is reported there, with a better message.
        hosted._strip_license_key(tmp_path / "nope.html")

    def test_the_uploaded_page_carries_no_key(self, built, tmp_path) -> None:
        transport = full_publish()

        make_exporter(transport).export(built, tmp_path / "upload")

        page = puts(transport)["https://uploads.example/index.html"]
        assert b"om_live_" not in (page.body or b"")


class TestDeclaredSizesAndDigestsMatchWhatIsUploaded:
    """Declared size and digest must describe the staged bytes, or nothing works.

    The server signs `content-length` into each presigned URL, so the upload is
    bound to the size declared in the manifest, and it verifies every sha256 it
    was promised before the release goes live. Anything that changes a staged
    file after it is measured - stripping a licence key, rewriting a path -
    breaks the publish twice over: an opaque 403 on the PUT, and a failed
    verification if it ever got past that.
    """

    def test_sizes_and_digests_are_of_the_stripped_page(self, built, tmp_path) -> None:
        transport = full_publish()

        make_exporter(transport).export(built, tmp_path / "upload")

        manifest = json.loads(transport.requests[0].body or b"{}")["manifest"]
        page = next(i for i in manifest["files"] if i["path"] == "index.html")
        stripped = (tmp_path / "upload" / "index.html").read_text(encoding="utf-8")
        assert "om_live_" not in stripped
        assert page["size"] == len(stripped.encode("utf-8"))
        assert page["sha256"] == sha256_of(stripped)
        # The manifest that came in carried placeholders; these are measured.
        assert page["sha256"] != "0" * 64

    def test_the_declared_digest_is_the_one_the_bytes_were_sent_under(
        self, built, tmp_path
    ) -> None:
        transport = full_publish()

        make_exporter(transport).export(built, tmp_path / "upload")

        manifest = json.loads(transport.requests[0].body or b"{}")["manifest"]
        data = next(i for i in manifest["files"] if i["path"] == "data/points.geojson")
        assert data["sha256"] == sha256_of(POINTS_JSON)
        body = puts(transport)["https://uploads.example/data/points.geojson"].body
        assert hashlib.sha256(body or b"").hexdigest() == data["sha256"]

    def test_the_strip_precedes_the_measurement_in_prepare(self) -> None:
        """Reordering these two lines would break publishing silently."""
        import inspect

        source = inspect.getsource(hosted.HostedExporter.prepare)
        strip_at = source.index("_strip_license_key")
        measure_at = source.index("path.stat().st_size")
        digest_at = source.index("_sha256(path)")
        assert strip_at < measure_at, (
            "sizes must be measured after stripping, or the declared size will "
            "not match the uploaded bytes and R2 will reject the signature"
        )
        assert strip_at < digest_at, (
            "digests must be taken after stripping, or the release fails "
            "verification against a file nobody ever had"
        )
