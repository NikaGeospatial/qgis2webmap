"""Publishing an artifact, and the consent that has to come before it.

The publish path is exercised end to end against a fake transport: staging,
the allowlist, the reservation, every PUT and the finalize. Nothing here opens
a socket.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

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
    CONTENT_TYPES,
    HostedExporter,
    PublishCancelledError,
)
from nika_onlymap_exporter.hosting.client import (
    HostingClient,
    HostingError,
    HttpResponse,
    UploadFile,
)
from nika_onlymap_exporter.hosting.consent import (
    publish_consent_text,
    should_warn_truncation,
    truncation_warning_text,
)
from nika_onlymap_exporter.writers.onlymap_writer import ArtifactFile, ArtifactResult

EMPTY_GEOJSON = {"type": "FeatureCollection", "features": []}

# No slot for `onlymap.js`, matching the server: the runtime is stored once per
# version under `runtime/` and no publish is ever offered an upload URL for it.
START_PAYLOAD = {
    "mapId": "map_1",
    "version": 4,
    "uploadUrls": [
        {"filename": "index.html", "url": "https://uploads.example/index"},
        {"filename": "thumbnail.png", "url": "https://uploads.example/thumb"},
    ],
    "licenseKey": None,
    "publicUrl": "https://maps.nika.eco/map_1",
    "runtimeVersion": "0.6.20",
    "runtimeSubstituted": False,
}

FINALIZE_PAYLOAD = {
    "mapId": "map_1",
    "publicUrl": "https://maps.nika.eco/map_1",
    "version": 4,
    "runtimeVersion": "0.6.20",
}


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
    source.mkdir()
    (source / "index.html").write_text("<om-map></om-map>", encoding="utf-8")
    (source / "onlymap.js").write_text("// runtime", encoding="utf-8")
    # `build_artifact` writes one of these for the folder tier, and the server
    # would reject it. It must not reach the upload list.
    (source / "README.txt").write_text("open index.html", encoding="utf-8")
    return ArtifactResult(
        entry_path=source / "index.html",
        mode=OutputMode.FOLDER,
        files=(
            ArtifactFile(source / "index.html", 17),
            ArtifactFile(source / "onlymap.js", 10),
        ),
        runtime_version="0.6.20",
    )


def make_exporter(transport: FakeTransport, **kwargs) -> HostedExporter:
    return HostedExporter(
        HostingClient("desk_x", "https://api.example", transport),
        title=kwargs.pop("title", "My map"),
        **kwargs,
    )


class TestPublishing:
    def test_a_free_tier_publish_succeeds(self, built, tmp_path) -> None:
        """A null `licenseKey` is the free tier, not a failure to publish."""
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        exporter = make_exporter(transport)

        outcome = exporter.export(built, tmp_path / "upload")

        assert outcome.public_url == "https://maps.nika.eco/map_1"
        assert "https://maps.nika.eco/map_1" in outcome.open_instruction
        assert outcome.mode is OutputMode.FOLDER

    def test_only_allowlisted_files_are_staged_and_sent(self, built, tmp_path) -> None:
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        exporter = make_exporter(transport)

        exporter.export(built, tmp_path / "upload")

        # `onlymap.js` is in the build directory and is deliberately NOT staged:
        # the 8.3 MB runtime is stored once per version on the server, so
        # uploading a copy per map would be paying for bytes nobody reads.
        staged = sorted(p.name for p in (tmp_path / "upload").iterdir())
        assert staged == ["index.html"]
        body = json.loads(transport.requests[0].body or b"{}")
        assert [entry["filename"] for entry in body["files"]] == ["index.html"]
        # The version is what the server pins the map to in place of the bytes.
        assert body["runtimeVersion"] == "0.6.20"

    def test_the_thumbnail_is_uploaded_with_the_right_content_type(
        self, built, tmp_path
    ) -> None:
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        exporter = make_exporter(transport, thumbnail_png=b"\x89PNG-bytes")

        exporter.export(built, tmp_path / "upload")

        puts = {
            request.url: request
            for request in transport.requests
            if request.method == "PUT"
        }
        thumb = puts["https://uploads.example/thumb"]
        assert thumb.body == b"\x89PNG-bytes"
        assert thumb.headers["Content-Type"] == CONTENT_TYPES["thumbnail.png"]

    def test_the_page_gets_a_web_content_type(self, built, tmp_path) -> None:
        """A wrong type here is a map the browser downloads instead of drawing."""
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        make_exporter(transport).export(built, tmp_path / "upload")

        types = {
            request.url: request.headers["Content-Type"]
            for request in transport.requests
            if request.method == "PUT"
        }
        assert types["https://uploads.example/index"].startswith("text/html")
        # The runtime's type is set by the seeding script, not by a publish —
        # nothing here uploads it, so there is no slot to get wrong.
        assert "https://uploads.example/js" not in types

    def test_republishing_sends_the_stored_map_id(self, built, tmp_path) -> None:
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        make_exporter(transport, map_id="map_1").export(built, tmp_path / "upload")
        assert json.loads(transport.requests[0].body or b"{}")["mapId"] == "map_1"

    def test_nothing_is_uploaded_before_the_confirmation(self, built, tmp_path) -> None:
        """The gap between reserving and uploading is where the warnings go."""
        transport = FakeTransport(ok(START_PAYLOAD))
        exporter = make_exporter(transport)

        with pytest.raises(PublishCancelledError):
            exporter.export(built, tmp_path / "upload", confirm=lambda _p: False)

        assert [request.method for request in transport.requests] == ["POST"]
        assert transport.requests[0].url.endswith("/maps/publish/start")

    def test_the_confirmation_sees_the_tier(self, built, tmp_path) -> None:
        transport = FakeTransport(ok(START_PAYLOAD))
        seen: list[bool] = []

        with pytest.raises(PublishCancelledError):
            make_exporter(transport).export(
                built,
                tmp_path / "upload",
                confirm=lambda prepared: seen.append(prepared.is_free_tier) or False,
            )

        assert seen == [True]

    def test_a_build_without_an_index_is_refused(self, tmp_path) -> None:
        source = tmp_path / "build"
        source.mkdir()
        (source / "onlymap.js").write_text("// runtime", encoding="utf-8")
        result = ArtifactResult(
            entry_path=source / "index.html", mode=OutputMode.FOLDER
        )

        transport = FakeTransport()
        with pytest.raises(HostingError, match=r"no index\.html"):
            make_exporter(transport).export(result, tmp_path / "upload")
        assert transport.requests == []

    def test_an_upload_failure_surfaces_a_clear_error(self, built, tmp_path) -> None:
        transport = FakeTransport(
            ok(START_PAYLOAD), *[HttpResponse(500, b"storage down")] * 3
        )
        exporter = make_exporter(transport)

        with pytest.raises(HostingError) as caught:
            exporter.export(built, tmp_path / "upload")

        message = str(caught.value)
        assert "index.html" in message
        assert "Nothing was published" in message
        # Finalize is never reached, so a half-uploaded version cannot go live.
        assert not any(
            request.url.endswith("/maps/publish/finalize")
            for request in transport.requests
        )

    def test_progress_is_reported_for_every_file(self, built, tmp_path) -> None:
        transport = FakeTransport(
            ok(START_PAYLOAD),
            HttpResponse(200, b""),
            HttpResponse(200, b""),
            ok(FINALIZE_PAYLOAD),
        )
        steps: list[tuple[int, str]] = []
        exporter = make_exporter(
            transport,
            thumbnail_png=b"\x89PNG-bytes",
            on_progress=lambda p, m: steps.append((p, m)),
        )

        exporter.export(built, tmp_path / "upload")

        messages = [message for _percent, message in steps]
        assert any("index.html" in message for message in messages)
        assert any("thumbnail.png" in message for message in messages)
        assert messages[-1] == "Publishing..."


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

    def test_it_does_not_claim_to_upload_the_runtime(self) -> None:
        """The consent must describe what actually leaves the machine."""
        assert "onlymap.js" not in self.text()

    def test_it_says_anyone_with_the_link_can_open_it(self) -> None:
        assert "Anyone with the link" in self.text()

    def test_it_says_the_attribute_data_is_published(self) -> None:
        assert "attribute data is published" in self.text()

    def test_it_raises_the_republication_question(self) -> None:
        assert "republishing" in self.text()


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


class TestDeclaredSizesMatchWhatIsUploaded:
    """Declared size and uploaded bytes must agree, or R2 refuses the PUT.

    The server signs `content-length` into each presigned URL, so the upload is
    bound to the size we declared at `start`. Anything that changes a staged
    file after its size is taken - stripping a licence key, rewriting a path -
    breaks every publish with an opaque 403.
    """

    def test_sizes_are_measured_after_the_licence_key_is_stripped(
        self, tmp_path
    ) -> None:
        page = tmp_path / "index.html"
        page.write_text(
            '<om-map license-key="om_live_abc.def" basemap="osm"></om-map>',
            encoding="utf-8",
        )
        before = page.stat().st_size
        hosted._strip_license_key(page)
        after = page.stat().st_size

        assert after < before, "stripping must actually shrink the file"
        # The size the server signs has to be this one, not `before`.
        assert after == len(page.read_bytes())

    def test_the_strip_precedes_the_measurement_in_prepare(self) -> None:
        """Reordering these two lines would break publishing silently."""
        import inspect

        source = inspect.getsource(hosted.HostedExporter.prepare)
        strip_at = source.index("_strip_license_key")
        measure_at = source.index("path.stat().st_size")
        assert strip_at < measure_at, (
            "sizes must be measured after stripping, or the declared size will "
            "not match the uploaded bytes and R2 will reject the signature"
        )
