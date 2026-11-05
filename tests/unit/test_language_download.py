"""Downloading the language packs: one zip from the release, all or nothing.

Every language comes in one release asset. The plugin follows GitHub's
redirect only to GitHub's own hosts, accepts the zip only when it holds exactly
the packs `i18n/packs.json` pins, and swaps the whole set into place at once.
Nothing here touches the network: the fetcher is a stand-in that answers as
GitHub does (`tests/fixtures/github-release-asset-redirect.json`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from nika_onlymap_exporter.ui import languages
from nika_onlymap_exporter.ui.languages import LanguageDownloadError, Reply

REPO_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPO_ROOT / "nika_onlymap_exporter" / "i18n"
GITHUB = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "github-release-asset-redirect.json").read_text(
        encoding="utf-8"
    )
)
FILE_HOST_URL = GITHUB["location"] + "?sp=r&sig=recorded-without-its-signature"


def _package_plugin():
    spec = importlib.util.spec_from_file_location(
        "_package_plugin_for_languages", REPO_ROOT / "scripts" / "package_plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def released_zip(tmp_path_factory) -> bytes:
    """The asset as the release packaging step builds it."""
    outdir = tmp_path_factory.mktemp("dist")
    return _package_plugin().build_language_packs(outdir).read_bytes()


@pytest.fixture
def installed_plugin(monkeypatch, tmp_path):
    """A plugin as a user has it: no packs beside the code, an empty cache."""
    empty = tmp_path / "plugin-i18n"
    empty.mkdir()
    monkeypatch.setattr(languages, "I18N_DIR", empty)
    monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path / "cache" / "runtime"))
    return languages


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _repo_packs() -> dict[str, bytes]:
    return {
        f"qgis2webmap_{code}.qm": (I18N_DIR / f"qgis2webmap_{code}.qm").read_bytes()
        for code in languages.pinned_packs()
    }


def like_github(body: bytes, calls: list[str]):
    """Answers the release URL with GitHub's recorded redirect, then `body`."""

    def fetch(url: str) -> Reply:
        calls.append(url)
        if url == languages.asset_url():
            return Reply(GITHUB["status"], b"", FILE_HOST_URL)
        assert url == FILE_HOST_URL, f"asked for {url}"
        return Reply(200, body)

    return fetch


def _installed(plugin) -> list[str]:
    folder = plugin.install_dir()
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def _cache_leftovers(plugin) -> list[str]:
    parent = plugin.install_dir().parent
    if not parent.exists():
        return []
    return sorted(p.name for p in parent.iterdir() if p.name.startswith("."))


class TestWhere:
    def test_the_url_is_the_release_asset_on_github(self):
        assert languages.asset_url("0.3.3") == (
            "https://github.com/NikaGeospatial/qgis2webmap/releases/download/"
            "v0.3.3/qgis2webmap-language-packs-0.3.3.zip"
        )

    def test_the_packager_builds_the_asset_the_plugin_asks_for(self, tmp_path):
        built = _package_plugin().build_language_packs(tmp_path)
        assert built.name == languages.asset_name()

    @pytest.mark.parametrize(
        ("url", "trusted"),
        [
            (languages.asset_url("0.3.3"), True),
            (FILE_HOST_URL, True),
            ("https://objects.githubusercontent.com/x", True),
            ("http://github.com/x", False),
            ("https://github.com.evil.example/x", False),
            ("https://githubusercontent.com.evil.example/x", False),
            ("https://evilgithubusercontent.com/x", False),
            ("https://cdn.jsdelivr.net/gh/NikaGeospatial/qgis2webmap@v0.3.3/x", False),
            ("https://github.com:8443/x", False),
            ("https://someone@github.com/x", False),
        ],
    )
    def test_only_github_over_https_is_asked(self, url, trusted):
        assert languages._trusted(url) is trusted


class TestOneDownload:
    def test_one_press_installs_every_language(self, installed_plugin, released_zip):
        calls: list[str] = []
        path = installed_plugin.download("es", like_github(released_zip, calls))
        assert calls == [installed_plugin.asset_url(), FILE_HOST_URL]
        assert path == installed_plugin.installed_pack("es")
        for code in installed_plugin.pinned_packs():
            assert installed_plugin.installed_pack(code) is not None, code
        assert installed_plugin.offer("auto", "fr_FR") is None
        assert _cache_leftovers(installed_plugin) == []

    def test_a_second_download_replaces_the_set(self, installed_plugin, released_zip):
        installed_plugin.download("es", like_github(released_zip, []))
        stray = installed_plugin.install_dir() / "qgis2webmap_de.qm"
        stray.write_bytes(b"left by something else")
        installed_plugin.download("fr", like_github(released_zip, []))
        assert _installed(installed_plugin) == sorted(_repo_packs())
        assert _cache_leftovers(installed_plugin) == []

    def test_a_tampered_file_on_disk_is_not_trusted(
        self, installed_plugin, released_zip
    ):
        path = installed_plugin.download("fr", like_github(released_zip, []))
        path.write_bytes(path.read_bytes() + b"x")
        assert installed_plugin.installed_pack("fr") is None

    def test_an_unknown_language_is_refused_before_any_request(self, installed_plugin):
        calls: list[str] = []
        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("de", like_github(b"", calls))
        assert calls == []


class TestRedirects:
    def test_a_redirect_off_github_is_not_followed(self, installed_plugin):
        calls: list[str] = []

        def fetch(url: str) -> Reply:
            calls.append(url)
            return Reply(302, b"", "https://cdn.example.org/packs.zip")

        with pytest.raises(LanguageDownloadError, match=r"cdn\.example\.org"):
            installed_plugin.download("es", fetch)
        assert calls == [installed_plugin.asset_url()]

    def test_a_redirect_to_plain_http_is_not_followed(self, installed_plugin):
        calls: list[str] = []

        def fetch(url: str) -> Reply:
            calls.append(url)
            return Reply(302, b"", "http://release-assets.githubusercontent.com/x")

        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("es", fetch)
        assert len(calls) == 1

    def test_a_redirect_loop_ends(self, installed_plugin):
        calls: list[str] = []

        def fetch(url: str) -> Reply:
            calls.append(url)
            return Reply(302, b"", installed_plugin.asset_url())

        with pytest.raises(LanguageDownloadError, match="redirected"):
            installed_plugin.download("es", fetch)
        assert len(calls) == installed_plugin.MAX_REDIRECTS + 1

    def test_a_missing_asset_says_so(self, installed_plugin):
        with pytest.raises(LanguageDownloadError, match="404"):
            installed_plugin.download("es", lambda url: Reply(404))
        assert _installed(installed_plugin) == []


def _changed(name: str, data: bytes | None) -> bytes:
    """The released packs with `name` replaced by `data`, or removed if None."""
    members = _repo_packs()
    if data is None:
        del members[name]
    else:
        members[name] = data
    return _zip(members)


def _moved(name: str, to: str) -> bytes:
    """The released packs with `name`, its bytes intact, stored as `to`."""
    members = _repo_packs()
    members[to] = members.pop(name)
    return _zip(members)


def _differing_pack() -> bytes:
    name = "qgis2webmap_fr.qm"
    return _changed(name, (I18N_DIR / name).read_bytes() + b"x")


class TestRefusedWhole:
    """Any fault refuses the whole download and leaves the cache as it was."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(_differing_pack, id="a-pack-differs"),
            pytest.param(lambda: _changed("README.txt", b"hello"), id="unknown"),
            # The right bytes under the wrong name: only the name refuses them.
            pytest.param(
                lambda: _moved("qgis2webmap_es.qm", "../qgis2webmap_es.qm"),
                id="path-out",
            ),
            pytest.param(
                lambda: _moved("qgis2webmap_es.qm", "/tmp/qgis2webmap_es.qm"),
                id="absolute",
            ),
            pytest.param(
                lambda: _moved("qgis2webmap_es.qm", "i18n/qgis2webmap_es.qm"),
                id="in-a-folder",
            ),
            pytest.param(lambda: _changed("qgis2webmap_fr.qm", None), id="missing"),
            pytest.param(lambda: b"<html>Not Found</html>", id="not-a-zip"),
        ],
    )
    def test_refused(self, installed_plugin, body):
        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("es", like_github(body(), []))
        assert _installed(installed_plugin) == []
        assert _cache_leftovers(installed_plugin) == []

    def test_a_truncated_download_is_refused(self, installed_plugin, released_zip):
        body = released_zip[: len(released_zip) // 2]
        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("es", like_github(body, []))
        assert _installed(installed_plugin) == []

    def test_a_damaged_member_is_refused(self, installed_plugin, released_zip):
        # Same length, one byte of compressed data flipped: the zip still
        # opens, and the member fails as it is read.
        info = zipfile.ZipFile(io.BytesIO(released_zip)).infolist()[0]
        at = info.header_offset + 30 + len(info.filename) + 100
        body = bytearray(released_zip)
        body[at] ^= 0xFF
        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("es", like_github(bytes(body), []))
        assert _installed(installed_plugin) == []

    def test_a_zip_bomb_is_refused_before_it_is_unpacked(self, installed_plugin):
        # Megabytes of zeros compress to a few KB: the claimed size is what
        # gives it away, before anything is unpacked.
        bomb = b"\0" * (installed_plugin.MAX_PACK_BYTES + 1)
        body = _changed("qgis2webmap_es.qm", bomb)
        assert len(body) < 2 * 1024 * 1024
        with pytest.raises(LanguageDownloadError, match="too large"):
            installed_plugin.download("es", like_github(body, []))
        assert _installed(installed_plugin) == []

    def test_an_oversized_download_is_refused(self, installed_plugin, monkeypatch):
        monkeypatch.setattr(installed_plugin, "MAX_DOWNLOAD_BYTES", 1024)
        with pytest.raises(LanguageDownloadError, match="too large"):
            installed_plugin.download("es", like_github(b"x" * 1025, []))

    def test_a_refused_download_keeps_the_installed_set(
        self, installed_plugin, released_zip
    ):
        installed_plugin.download("es", like_github(released_zip, []))
        with pytest.raises(LanguageDownloadError):
            installed_plugin.download("es", like_github(b"not a zip", []))
        for code in installed_plugin.pinned_packs():
            assert installed_plugin.installed_pack(code) is not None, code


class TestAtomicInstall:
    def test_a_failed_swap_leaves_the_old_set(
        self, installed_plugin, released_zip, monkeypatch
    ):
        installed_plugin.download("es", like_github(released_zip, []))
        before = _installed(installed_plugin)
        real_rename = Path.rename

        def failing_rename(self, target):
            # The new set taking its place fails; putting the old one back works.
            if Path(target) == installed_plugin.install_dir() and not str(
                self
            ).endswith("-old"):
                raise OSError("disk went away")
            return real_rename(self, target)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "rename", failing_rename)
            with pytest.raises(LanguageDownloadError, match="disk went away"):
                installed_plugin.download("fr", like_github(released_zip, []))
        assert _installed(installed_plugin) == before
        assert installed_plugin.installed_pack("fr") is not None
        assert _cache_leftovers(installed_plugin) == []

    def test_a_failed_write_installs_nothing(
        self, installed_plugin, released_zip, monkeypatch
    ):
        real_write = Path.write_bytes
        written: list[str] = []

        def failing_write(self, data):
            written.append(self.name)
            if len(written) == 3:
                raise OSError("disk full")
            return real_write(self, data)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_bytes", failing_write)
            with pytest.raises(LanguageDownloadError, match="disk full"):
                installed_plugin.download("es", like_github(released_zip, []))
        assert _installed(installed_plugin) == []
        assert _cache_leftovers(installed_plugin) == []


def test_packs_installed_for_another_version_are_left_alone(
    installed_plugin, released_zip, monkeypatch
):
    """0.3.2 kept its packs in its own folder; 0.3.3 neither reads nor removes it."""
    older = installed_plugin.install_dir().parent / "0.3.2"
    older.mkdir(parents=True)
    (older / "qgis2webmap_es.qm").write_bytes(b"0.3.2's pack")
    monkeypatch.setattr(installed_plugin, "plugin_version", lambda: "0.3.3")
    assert installed_plugin.installed_pack("es") is None
    installed_plugin.download("es", like_github(released_zip, []))
    assert installed_plugin.installed_pack("es").parent.name == "0.3.3"
    assert (older / "qgis2webmap_es.qm").read_bytes() == b"0.3.2's pack"
    assert os.listdir(older) == ["qgis2webmap_es.qm"]
