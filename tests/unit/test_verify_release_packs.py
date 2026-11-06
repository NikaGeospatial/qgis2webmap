"""The check that a published release serves the language packs its tag pins.

`scripts/verify_release_packs.py` runs after a release is published: it fetches
the asset as the plugin does and verifies it with the plugin's own code. Here
the release is a stand-in that answers as GitHub does, and its fetcher is
driven against a local server for the one thing a stand-in cannot show: that it
hands redirects back instead of following them.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from nika_onlymap_exporter.ui import languages
from nika_onlymap_exporter.ui.languages import LanguageDownloadError, Reply

REPO_ROOT = Path(__file__).resolve().parents[2]
FILE_HOST = (
    "https://release-assets.githubusercontent.com/github-production-release-asset/1"
)


def _script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _script("verify_release_packs")
VERSION = languages.plugin_version()


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> bytes:
    """The asset as the release packaging step builds and attaches it."""
    built = _script("package_plugin").build_language_packs(
        tmp_path_factory.mktemp("dist")
    )
    return built.read_bytes()


def release_serving(body: bytes | None):
    """A release whose asset URL redirects to GitHub's file host, as GitHub
    does; `None` is a release published without the asset."""

    def fetch(url: str) -> Reply:
        if url == languages.asset_url(VERSION):
            return Reply(404) if body is None else Reply(302, location=FILE_HOST)
        assert url == FILE_HOST, f"asked for {url}"
        return Reply(200, body or b"")

    return fetch


def test_a_release_serving_its_packs_passes(published) -> None:
    assert (
        verify.check(VERSION, languages.pinned_packs(), release_serving(published)) == 6
    )


def test_a_release_without_the_asset_fails() -> None:
    with pytest.raises(LanguageDownloadError, match="HTTP 404"):
        verify.check(VERSION, languages.pinned_packs(), release_serving(None))


def test_a_release_serving_other_packs_fails(published) -> None:
    """Built from packs that are not the tag's (a stale rebuild, say)."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(published)) as source,
        zipfile.ZipFile(buffer, "w") as changed,
    ):
        for info in source.infolist():
            data = source.read(info)
            changed.writestr(
                info, data + b"!" if info.filename.endswith("fr.qm") else data
            )
    with pytest.raises(LanguageDownloadError, match="not the ones released"):
        verify.check(
            VERSION, languages.pinned_packs(), release_serving(buffer.getvalue())
        )


def test_the_fetcher_hands_a_redirect_back() -> None:
    """Following it itself would skip the check of where it leads."""
    asked: list[str] = []

    class Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reply = verify.fetch(f"http://127.0.0.1:{server.server_port}/asset.zip")
    finally:
        server.shutdown()
        server.server_close()
    assert (reply.status, reply.location) == (302, "http://127.0.0.1:9/elsewhere")
    assert asked == ["/asset.zip"]
