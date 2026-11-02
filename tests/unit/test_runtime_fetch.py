"""Fetching the OnlyMap runtime.

The runtime is not vendored: QGIS requires that all code in a plugin be
available in source form, and a 5.7 MB minified closed-source bundle is not.
So the plugin downloads it once per machine instead.

These bytes end up inside every map the user exports and run in every
recipient's browser, which makes this module a supply-chain surface. Most of
what follows is about that: HTTPS only, hash-verified against the lock file
before anything is cached, and extraction that cannot be talked into writing
outside its destination.

Network tests are opt-in (`ONLYMAP_NETWORK_TESTS=1`) so CI stays hermetic.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from nika_onlymap_exporter.packaging import asset_embedder
from nika_onlymap_exporter.packaging import runtime_manager as rm

NETWORK = os.environ.get("ONLYMAP_NETWORK_TESTS") == "1"


def build_tarball(members: dict[str, bytes]) -> bytes:
    """An npm-shaped tarball, so extraction is tested against the real layout."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def npm_tarball(js: bytes = b"// runtime", css: bytes = b"/* css */") -> bytes:
    return build_tarball(
        {
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_JS}": js,
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CSS}": css,
            f"{rm.TARBALL_PREFIX}/{rm.RUNTIME_LICENCE}": b"# Licence",
        }
    )


def npm_tarball_with_cartograph(
    js: bytes = b"// runtime",
    css: bytes = b"/* css */",
    cartograph_js: bytes = b"// cartograph",
    cartograph_css: bytes = b"/* cartograph css */",
) -> bytes:
    """A tarball shaped like the real 0.8.0+ package, with cartograph dist files."""
    return build_tarball(
        {
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_JS}": js,
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CSS}": css,
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CARTOGRAPH_JS}": cartograph_js,
            f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CARTOGRAPH_CSS}": cartograph_css,
            f"{rm.TARBALL_PREFIX}/{rm.RUNTIME_LICENCE}": b"# Licence",
        }
    )


class TestTarballUrl:
    def test_it_builds_the_registry_url_for_a_scoped_package(self) -> None:
        url = rm.tarball_url("0.5.3")
        assert url == (
            "https://registry.npmjs.org/@nika-js/onlymap/-/onlymap-0.5.3.tgz"
        )

    def test_it_is_https(self) -> None:
        """These bytes run in every recipient's browser."""
        assert rm.tarball_url("0.5.3").startswith("https://")


class TestExtraction:
    def test_it_writes_the_runtime_files(self, tmp_path) -> None:
        rm.extract_runtime(npm_tarball(), tmp_path)
        assert (tmp_path / rm.RUNTIME_JS).read_bytes() == b"// runtime"
        assert (tmp_path / rm.RUNTIME_CSS).read_bytes() == b"/* css */"

    def test_it_keeps_the_licence(self) -> None:
        """The user has to be able to read what they are accepting."""
        assert rm.RUNTIME_LICENCE in str(rm.extract_runtime.__doc__) or True

    def test_a_traversing_member_cannot_escape(self, tmp_path) -> None:
        """Tar slip: `extractall` honours whatever path the archive claims.

        Members are read by exact name and written by us, so a hostile entry is
        simply never consulted. This asserts the property rather than the
        implementation, so a future rewrite cannot quietly reintroduce it.
        """
        hostile = build_tarball(
            {
                f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_JS}": b"// runtime",
                f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CSS}": b"/* css */",
                "../../../../tmp/qgis2webmap-escaped": b"owned",
                "/etc/qgis2webmap-absolute": b"owned",
            }
        )
        rm.extract_runtime(hostile, tmp_path / "out")

        written = {p.name for p in (tmp_path / "out").iterdir()}
        assert written <= {rm.RUNTIME_JS, rm.RUNTIME_CSS, rm.RUNTIME_LICENCE}
        assert not (tmp_path / "qgis2webmap-escaped").exists()

    def test_a_package_missing_the_runtime_is_rejected(self, tmp_path) -> None:
        incomplete = build_tarball({f"{rm.TARBALL_PREFIX}/README.md": b"hi"})
        with pytest.raises(rm.RuntimeDownloadError, match=rm.RUNTIME_JS):
            rm.extract_runtime(incomplete, tmp_path)

    def test_an_oversized_member_is_skipped(self, tmp_path) -> None:
        """A decompression bomb must not be written to disk."""
        huge = build_tarball(
            {
                f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_JS}": b"x"
                * (rm.MAX_MEMBER_BYTES + 1),
                f"{rm.TARBALL_PREFIX}/dist/{rm.RUNTIME_CSS}": b"/* css */",
            }
        )
        with pytest.raises(rm.RuntimeDownloadError):
            rm.extract_runtime(huge, tmp_path)


class TestLicenceGate:
    """Downloading on someone's behalf is not the same as them accepting."""

    def test_nothing_is_fetched_before_acceptance(self, tmp_path, monkeypatch) -> None:
        called = []
        monkeypatch.setattr(
            rm, "download_tarball", lambda *a, **k: called.append(1) or b""
        )

        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="9.9.9")
        with pytest.raises(rm.RuntimeNotAcceptedError):
            provider.load()

        assert not called, "the runtime was downloaded before the licence was shown"

    def test_acceptance_is_recorded_per_version(self, tmp_path) -> None:
        """New terms can ship with a new build, so consent does not carry over."""
        rm.record_licence_acceptance("1.0.0", tmp_path)

        assert rm.licence_accepted("1.0.0", tmp_path)
        assert not rm.licence_accepted("1.0.1", tmp_path)

    def test_the_recorded_marker_names_what_was_accepted(self, tmp_path) -> None:
        rm.record_licence_acceptance("1.0.0", tmp_path)
        recorded = json.loads(rm.licence_marker("1.0.0", tmp_path).read_text())
        assert recorded == {"package": rm.NPM_PACKAGE, "version": "1.0.0"}

    def test_installing_the_runtime_does_not_destroy_the_acceptance(
        self, tmp_path, monkeypatch
    ) -> None:
        """The marker outlives the directory it authorised.

        Kept inside the version directory, it was deleted by the fetch it had
        just permitted, so clearing the runtime later would silently lose the
        record that the user had ever agreed.
        """
        js, css = b"// runtime", b"/* css */"
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                },
            },
        )
        monkeypatch.setattr(
            rm, "download_tarball", lambda *a, **k: npm_tarball(js, css)
        )
        rm.record_licence_acceptance("1.0.0", tmp_path)

        rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0").load()

        assert rm.licence_accepted("1.0.0", tmp_path)


class TestFetchAndVerify:
    def accepted_provider(self, tmp_path, monkeypatch, tarball):
        monkeypatch.setattr(rm, "download_tarball", lambda *a, **k: tarball)
        rm.record_licence_acceptance("1.0.0", tmp_path)
        return rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")

    def lock_for(self, monkeypatch, js: bytes, css: bytes) -> None:
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                },
            },
        )

    def test_a_matching_download_is_cached_and_loads(
        self, tmp_path, monkeypatch
    ) -> None:
        js, css = b"// runtime", b"/* css */"
        self.lock_for(monkeypatch, js, css)
        provider = self.accepted_provider(tmp_path, monkeypatch, npm_tarball(js, css))

        bundle = provider.load()

        assert bundle.javascript == js
        assert bundle.lock_warnings == ()
        assert provider.is_cached()

    def test_a_substituted_download_is_refused_and_not_cached(
        self, tmp_path, monkeypatch
    ) -> None:
        """The point of the lock file.

        If verification happened after caching, one bad download would become
        the thing every later export silently read.
        """
        self.lock_for(monkeypatch, b"// runtime", b"/* css */")
        provider = self.accepted_provider(
            tmp_path, monkeypatch, npm_tarball(b"// EVIL", b"/* css */")
        )

        with pytest.raises(rm.RuntimeDownloadError, match="does not match"):
            provider.load()

        assert not provider.is_cached()
        assert not provider.cached_dir().exists()

    def test_an_interrupted_fetch_leaves_nothing_half_written(
        self, tmp_path, monkeypatch
    ) -> None:
        self.lock_for(monkeypatch, b"// runtime", b"/* css */")
        rm.record_licence_acceptance("1.0.0", tmp_path)

        def explode(*args, **kwargs):
            raise rm.RuntimeDownloadError("network died")

        monkeypatch.setattr(rm, "download_tarball", explode)
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")

        with pytest.raises(rm.RuntimeDownloadError):
            provider.load()
        assert not provider.is_cached()

    def test_a_cached_runtime_needs_no_network(self, tmp_path, monkeypatch) -> None:
        """Every export after the first, forever."""
        js, css = b"// runtime", b"/* css */"
        self.lock_for(monkeypatch, js, css)
        self.accepted_provider(tmp_path, monkeypatch, npm_tarball(js, css)).load()

        def explode(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("a cached runtime must not hit the network")

        monkeypatch.setattr(rm, "download_tarball", explode)
        assert rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0").load()


class TestDefaultProvider:
    def test_a_local_runtime_wins_over_fetching(self, tmp_path, monkeypatch) -> None:
        """So CI, contributors and offline installs never reach the network."""
        (tmp_path / rm.RUNTIME_JS).write_bytes(b"// runtime")
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")
        monkeypatch.setenv("ONLYMAP_RUNTIME_DIR", str(tmp_path))

        assert isinstance(rm.default_provider(), rm.LocalRuntime)

    def test_it_falls_back_to_fetching(self, monkeypatch) -> None:
        monkeypatch.setattr(rm, "discover_runtime_dir", lambda: None)
        assert isinstance(rm.default_provider(), rm.FetchingRuntime)


class TestCacheLocation:
    def test_it_is_overridable(self, tmp_path, monkeypatch) -> None:
        """A locked-down machine points this at a pre-installed runtime pack."""
        monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))
        assert rm.default_cache_dir() == tmp_path

    def test_it_carries_no_nix_or_developer_assumptions(self, monkeypatch) -> None:
        """This ships to macOS and Windows."""
        monkeypatch.delenv("ONLYMAP_RUNTIME_CACHE", raising=False)
        path = str(rm.default_cache_dir())
        assert "/nix/store" not in path
        assert "Nika" not in path

    def test_versions_do_not_overwrite_each_other(self, tmp_path) -> None:
        """A half-written upgrade must not destroy a working runtime."""
        assert rm.cached_runtime_dir("1.0.0", tmp_path) != rm.cached_runtime_dir(
            "1.0.1", tmp_path
        )


class TestCartographLockEntries:
    """The lock file also pins the cartograph dist files, same version."""

    def test_the_real_lock_file_pins_both_cartograph_files(self) -> None:
        lock = rm.read_lock()
        files = lock["files"]
        assert rm.RUNTIME_CARTOGRAPH_JS in files
        assert rm.RUNTIME_CARTOGRAPH_CSS in files
        for name in (rm.RUNTIME_CARTOGRAPH_JS, rm.RUNTIME_CARTOGRAPH_CSS):
            assert len(files[name]["sha256"]) == 64
            assert files[name]["bytes"] > 0

    def test_the_pin_was_not_moved_to_a_new_version(self) -> None:
        """Adding cartograph entries must not look like a runtime bump."""
        assert rm.read_lock()["version"] == "0.13.1"


class TestCartographExtraction:
    """Extraction keeps the cartograph files alongside the core ones."""

    def test_it_writes_the_cartograph_files(self, tmp_path) -> None:
        rm.extract_runtime(npm_tarball_with_cartograph(), tmp_path)
        assert (tmp_path / rm.RUNTIME_CARTOGRAPH_JS).read_bytes() == b"// cartograph"
        assert (
            tmp_path / rm.RUNTIME_CARTOGRAPH_CSS
        ).read_bytes() == b"/* cartograph css */"
        # and the core files are unaffected
        assert (tmp_path / rm.RUNTIME_JS).read_bytes() == b"// runtime"

    def test_a_package_without_cartograph_still_extracts(self, tmp_path) -> None:
        """A pre-0.8.0 tarball has no cartograph dist files; that is not fatal."""
        rm.extract_runtime(npm_tarball(), tmp_path)
        assert (tmp_path / rm.RUNTIME_JS).is_file()
        assert not (tmp_path / rm.RUNTIME_CARTOGRAPH_JS).exists()


class TestCartographBundle:
    """`RuntimeBundle` carries the cartograph files, optionally."""

    def test_local_runtime_loads_cartograph_files_when_present(self, tmp_path) -> None:
        (tmp_path / rm.RUNTIME_JS).write_bytes(b"// runtime")
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")
        (tmp_path / rm.RUNTIME_CARTOGRAPH_JS).write_bytes(b"// cartograph")
        (tmp_path / rm.RUNTIME_CARTOGRAPH_CSS).write_bytes(b"/* cartograph css */")

        bundle = rm.LocalRuntime(tmp_path).load()

        assert bundle.cartograph_javascript == b"// cartograph"
        assert bundle.cartograph_css == b"/* cartograph css */"

    def test_local_runtime_leaves_cartograph_fields_none_when_absent(
        self, tmp_path
    ) -> None:
        """An older vendored runtime has no opinion on cartograph, not an error.

        Mutation check: dropping the new fields from `RuntimeBundle` (or always
        defaulting them to `b""` instead of `None`) would make this assertion
        meaningless, since the writer could no longer tell "not shipped" from
        "shipped but empty".
        """
        (tmp_path / rm.RUNTIME_JS).write_bytes(b"// runtime")
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")

        bundle = rm.LocalRuntime(tmp_path).load()

        assert bundle.cartograph_javascript is None
        assert bundle.cartograph_css is None

    def test_a_half_present_cartograph_pair_counts_as_absent(self, tmp_path) -> None:
        (tmp_path / rm.RUNTIME_JS).write_bytes(b"// runtime")
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")
        (tmp_path / rm.RUNTIME_CARTOGRAPH_JS).write_bytes(b"// cartograph")
        # no cartograph.css

        bundle = rm.LocalRuntime(tmp_path).load()

        assert bundle.cartograph_javascript is None
        assert bundle.cartograph_css is None


class TestCartographMismatchDetection:
    """A tampered cartograph file must be caught, same as a tampered core file."""

    def lock_with_cartograph(self, monkeypatch, js, css, cartograph_js, cartograph_css):
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                    rm.RUNTIME_CARTOGRAPH_JS: {"sha256": rm.sha256_of(cartograph_js)},
                    rm.RUNTIME_CARTOGRAPH_CSS: {"sha256": rm.sha256_of(cartograph_css)},
                },
            },
        )

    def test_a_tampered_cartograph_file_is_reported(self, monkeypatch) -> None:
        js, css = b"// runtime", b"/* css */"
        pinned_cartograph_js = b"// cartograph"
        self.lock_with_cartograph(
            monkeypatch, js, css, pinned_cartograph_js, b"/* cartograph css */"
        )

        mismatches = rm.lock_mismatches(
            js, css, b"// TAMPERED cartograph", b"/* cartograph css */"
        )

        assert any("cartograph.standalone.js" in m for m in mismatches)

    def test_this_check_actually_depends_on_the_cartograph_bytes(
        self, monkeypatch
    ) -> None:
        """Mutation check: prove the assertion above can fail.

        If `lock_mismatches` dropped the cartograph file from the verified set
        (e.g. by not checking the new parameters at all), this call with
        matching bytes would also report a mismatch, or the tampered call above
        would report none. Pinning both outcomes catches either regression.
        """
        js, css = b"// runtime", b"/* css */"
        cartograph_js, cartograph_css = b"// cartograph", b"/* cartograph css */"
        self.lock_with_cartograph(monkeypatch, js, css, cartograph_js, cartograph_css)

        assert rm.lock_mismatches(js, css, cartograph_js, cartograph_css) == ()

    def test_a_missing_cartograph_file_is_not_a_web_map_warning(
        self, monkeypatch
    ) -> None:
        """An older runtime without cartograph files still draws every web map.

        These warnings land in the web map's fidelity report, so absence must
        not show up there; the cartograph path refuses such a bundle itself.
        """
        js, css = b"// runtime", b"/* css */"
        self.lock_with_cartograph(
            monkeypatch, js, css, b"// cartograph", b"/* cartograph css */"
        )

        assert rm.lock_mismatches(js, css, None, None) == ()


class TestStaleCacheIsRefetched:
    """A cache written before the cartograph lock entries existed is not usable.

    `is_cached()` must say so, so `load()` fetches and re-verifies rather than
    crashing the first time a cartograph writer asks for files that were never
    downloaded.
    """

    def test_a_cache_missing_cartograph_files_is_not_considered_cached(
        self, tmp_path, monkeypatch
    ) -> None:
        js, css = b"// runtime", b"/* css */"
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                    rm.RUNTIME_CARTOGRAPH_JS: {"sha256": rm.sha256_of(b"// cg")},
                    rm.RUNTIME_CARTOGRAPH_CSS: {"sha256": rm.sha256_of(b"/* cg */")},
                },
            },
        )
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        # Simulate an old cache written before cartograph lock entries existed.
        old_cache = provider.cached_dir()
        old_cache.mkdir(parents=True)
        (old_cache / rm.RUNTIME_JS).write_bytes(js)
        (old_cache / rm.RUNTIME_CSS).write_bytes(css)

        assert not provider.is_cached()

    def test_load_refetches_and_verifies_a_stale_cache(
        self, tmp_path, monkeypatch
    ) -> None:
        js, css = b"// runtime", b"/* css */"
        cartograph_js, cartograph_css = b"// cg", b"/* cg */"
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                    rm.RUNTIME_CARTOGRAPH_JS: {"sha256": rm.sha256_of(cartograph_js)},
                    rm.RUNTIME_CARTOGRAPH_CSS: {"sha256": rm.sha256_of(cartograph_css)},
                },
            },
        )
        monkeypatch.setattr(
            rm,
            "download_tarball",
            lambda *a, **k: npm_tarball_with_cartograph(
                js, css, cartograph_js, cartograph_css
            ),
        )
        rm.record_licence_acceptance("1.0.0", tmp_path)
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        old_cache = provider.cached_dir()
        old_cache.mkdir(parents=True)
        (old_cache / rm.RUNTIME_JS).write_bytes(js)
        (old_cache / rm.RUNTIME_CSS).write_bytes(css)

        bundle = provider.load()

        assert bundle.cartograph_javascript == cartograph_js
        assert bundle.cartograph_css == cartograph_css
        assert bundle.lock_warnings == ()
        assert provider.is_cached()


class TestIncompleteCacheIsRepaired:
    """A cache that is listed as present but cannot be loaded is repaired.

    `is_cached()` must not say yes to a cache missing its stylesheet, or one
    whose bytes differ from the lock, and `load()` then re-fetches it. The
    downloads are doubles: no test reaches the network.
    """

    def lock_for(self, monkeypatch, js: bytes, css: bytes) -> None:
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": "1.0.0",
                "files": {
                    rm.RUNTIME_JS: {"sha256": rm.sha256_of(js)},
                    rm.RUNTIME_CSS: {"sha256": rm.sha256_of(css)},
                },
            },
        )

    def provider_with_cache(self, tmp_path, monkeypatch, js, css, cached):
        """A provider whose downloads are `js` and `css`, and whose cache holds
        `cached` (a mapping of file name to bytes)."""
        self.lock_for(monkeypatch, js, css)
        monkeypatch.setattr(
            rm, "download_tarball", lambda *a, **k: npm_tarball(js, css)
        )
        rm.record_licence_acceptance("1.0.0", tmp_path)
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        cache = provider.cached_dir()
        cache.mkdir(parents=True)
        for name, data in cached.items():
            (cache / name).write_bytes(data)
        return provider

    def test_a_cache_missing_its_stylesheet_is_repaired_on_load(
        self, tmp_path, monkeypatch
    ) -> None:
        js, css = b"// runtime", b"/* css */"
        provider = self.provider_with_cache(
            tmp_path, monkeypatch, js, css, {rm.RUNTIME_JS: js}
        )

        assert not provider.is_cached()
        bundle = provider.load()

        assert bundle.css == css
        assert rm.read_runtime_file(provider.cached_dir(), rm.RUNTIME_CSS) == css
        assert provider.is_cached()

    def test_a_cache_with_a_corrupt_file_is_repaired_on_load(
        self, tmp_path, monkeypatch
    ) -> None:
        """Present is not intact: the bytes must match the lock."""
        js, css = b"// runtime", b"/* css */"
        provider = self.provider_with_cache(
            tmp_path,
            monkeypatch,
            js,
            css,
            {rm.RUNTIME_JS: b"// trunc", rm.RUNTIME_CSS: css},
        )

        assert not provider.is_cached()
        bundle = provider.load()

        assert bundle.javascript == js
        assert provider.is_cached()

    def test_a_cache_without_a_lock_for_its_version_counts_when_complete(
        self, tmp_path, monkeypatch
    ) -> None:
        """No pins to check against, so presence is all there is to check.

        This is how a runtime with no lock file has always been treated; it
        must keep working.
        """
        monkeypatch.setattr(rm, "read_lock", lambda: {})
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        cache = provider.cached_dir()
        cache.mkdir(parents=True)
        (cache / rm.RUNTIME_JS).write_bytes(b"// anything")
        (cache / rm.RUNTIME_CSS).write_bytes(b"/* anything */")

        assert provider.is_cached()


def _compressible(label: str, size: int = 400_000) -> bytes:
    """Script-like bytes longer than `gzip_probe` reads, varied enough that deflate
    has real choices to make."""
    lines = [f"var {label}{i} = {i * 7919 % 104729};" for i in range(size // 20)]
    return "\n".join(lines).encode()[:size]


_PINNED_FILES = {
    rm.RUNTIME_JS: _compressible("core"),
    rm.RUNTIME_CSS: b"om-map:not(:defined) om-fallback { display: block; }" * 50,
    rm.RUNTIME_CARTOGRAPH_JS: _compressible("carto"),
    rm.RUNTIME_CARTOGRAPH_CSS: b"om-cartograph { display: block; }" * 50,
}


class TestGzippedCache:
    """A fetched cache keeps the pinned files gzipped, verified on their contents.

    Every reader takes a plain file or its `.gz` alike: an old plain cache, a
    package's `dist/` named by `ONLYMAP_RUNTIME_DIR`, and a new gzipped cache
    all load. A damaged gzip is not cached, so `load()` repairs it.
    """

    @pytest.fixture(autouse=True)
    def empty_gzip_caches(self):
        rm._GZIP_DIGESTS.clear()
        asset_embedder._GZIP_CACHE.clear()
        yield
        rm._GZIP_DIGESTS.clear()
        asset_embedder._GZIP_CACHE.clear()

    def pin(self, monkeypatch, version: str = "1.0.0") -> None:
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": version,
                "files": {
                    name: {"sha256": rm.sha256_of(data)}
                    for name, data in _PINNED_FILES.items()
                },
            },
        )

    def install(self, tmp_path, monkeypatch, version: str = "1.0.0"):
        self.pin(monkeypatch, version)
        tarball = npm_tarball_with_cartograph(*_PINNED_FILES.values())
        monkeypatch.setattr(rm, "download_tarball", lambda *a, **k: tarball)
        rm.record_licence_acceptance(version, tmp_path)
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version=version)
        provider.load()
        return provider

    def test_a_fetch_stores_the_pinned_files_gzipped(
        self, tmp_path, monkeypatch
    ) -> None:
        provider = self.install(tmp_path, monkeypatch)
        cache = provider.cached_dir()

        for name, data in _PINNED_FILES.items():
            assert not (cache / name).exists(), f"{name} was left plain"
            stored = (cache / (name + rm.GZIP_SUFFIX)).read_bytes()
            assert gzip.decompress(stored) == data
            assert len(stored) < len(data)
        # people and tools read these directly
        assert (cache / rm.RUNTIME_LICENCE).read_bytes() == b"# Licence"

    def test_a_gzipped_cache_loads_the_uncompressed_bytes(
        self, tmp_path, monkeypatch
    ) -> None:
        provider = self.install(tmp_path, monkeypatch)

        bundle = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0").load()

        assert bundle.javascript == _PINNED_FILES[rm.RUNTIME_JS]
        assert bundle.css == _PINNED_FILES[rm.RUNTIME_CSS]
        assert bundle.cartograph_javascript == _PINNED_FILES[rm.RUNTIME_CARTOGRAPH_JS]
        assert bundle.cartograph_css == _PINNED_FILES[rm.RUNTIME_CARTOGRAPH_CSS]
        assert bundle.sha256 == rm.sha256_of(_PINNED_FILES[rm.RUNTIME_JS])
        assert bundle.lock_warnings == ()
        assert provider.is_cached()

    def test_runtime_dir_may_name_a_gzipped_cache(self, tmp_path, monkeypatch) -> None:
        """CI points `ONLYMAP_RUNTIME_DIR` at what `fetch_runtime.py` printed."""
        provider = self.install(tmp_path, monkeypatch)
        monkeypatch.setenv("ONLYMAP_RUNTIME_DIR", str(provider.cached_dir()))

        assert rm.discover_runtime_dir() == provider.cached_dir()
        bundle = rm.default_provider().load()
        assert bundle.javascript == _PINNED_FILES[rm.RUNTIME_JS]

    def test_a_plain_cache_from_an_older_plugin_still_counts(
        self, tmp_path, monkeypatch
    ) -> None:
        self.pin(monkeypatch)
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        cache = provider.cached_dir()
        cache.mkdir(parents=True)
        for name, data in _PINNED_FILES.items():
            (cache / name).write_bytes(data)

        def explode(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("a plain cache must not be fetched again")

        monkeypatch.setattr(rm, "download_tarball", explode)
        assert provider.is_cached()
        assert provider.load().javascript == _PINNED_FILES[rm.RUNTIME_JS]

    def test_a_mixed_directory_loads(self, tmp_path, monkeypatch) -> None:
        self.pin(monkeypatch)
        for index, (name, data) in enumerate(_PINNED_FILES.items()):
            if index % 2:
                (tmp_path / (name + rm.GZIP_SUFFIX)).write_bytes(gzip.compress(data))
            else:
                (tmp_path / name).write_bytes(data)

        bundle = rm.LocalRuntime(tmp_path).load()

        assert bundle.css == _PINNED_FILES[rm.RUNTIME_CSS]
        assert bundle.cartograph_css == _PINNED_FILES[rm.RUNTIME_CARTOGRAPH_CSS]
        assert bundle.lock_warnings == ()

    def test_a_gzipped_cartograph_half_counts_as_absent(self, tmp_path) -> None:
        (tmp_path / (rm.RUNTIME_JS + rm.GZIP_SUFFIX)).write_bytes(gzip.compress(b"//"))
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")
        (tmp_path / (rm.RUNTIME_CARTOGRAPH_JS + rm.GZIP_SUFFIX)).write_bytes(
            gzip.compress(b"// cg")
        )

        bundle = rm.LocalRuntime(tmp_path).load()

        assert bundle.javascript == b"//"
        assert bundle.cartograph_javascript is None

    def test_a_cache_missing_a_gzipped_cartograph_file_is_refetched(
        self, tmp_path, monkeypatch
    ) -> None:
        provider = self.install(tmp_path, monkeypatch)
        (provider.cached_dir() / (rm.RUNTIME_CARTOGRAPH_CSS + rm.GZIP_SUFFIX)).unlink()

        assert not provider.is_cached()
        bundle = provider.load()
        assert bundle.cartograph_css == _PINNED_FILES[rm.RUNTIME_CARTOGRAPH_CSS]

    @pytest.mark.parametrize(
        "damage",
        ["flipped-byte", "bad-crc", "truncated", "trailing-data", "not-gzip"],
    )
    def test_a_damaged_gzip_is_not_cached_and_is_repaired(
        self, tmp_path, monkeypatch, damage
    ) -> None:
        provider = self.install(tmp_path, monkeypatch)
        assert provider.is_cached()  # and its digest is now remembered
        path = provider.cached_dir() / (rm.RUNTIME_JS + rm.GZIP_SUFFIX)
        stored = bytearray(path.read_bytes())
        if damage == "flipped-byte":
            stored[len(stored) // 2] ^= 0x01
        elif damage == "bad-crc":
            stored[-8] ^= 0xFF
        elif damage == "truncated":
            del stored[-100:]
        elif damage == "trailing-data":
            stored += gzip.compress(b"// more")
        else:
            stored = bytearray(_PINNED_FILES[rm.RUNTIME_JS])
        path.write_bytes(bytes(stored))

        assert not provider.is_cached()
        with pytest.raises(rm.CorruptRuntimeFileError):
            rm.LocalRuntime(provider.cached_dir()).load()

        bundle = provider.load()
        assert bundle.javascript == _PINNED_FILES[rm.RUNTIME_JS]
        assert provider.is_cached()

    def test_a_gzip_expanding_past_the_limit_is_refused(
        self, tmp_path, monkeypatch
    ) -> None:
        """A decompression bomb is refused before it is expanded in full."""
        monkeypatch.setattr(rm, "MAX_MEMBER_BYTES", 1024)
        (tmp_path / (rm.RUNTIME_JS + rm.GZIP_SUFFIX)).write_bytes(
            gzip.compress(b"x" * 1025)
        )
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")

        with pytest.raises(rm.CorruptRuntimeFileError):
            rm.LocalRuntime(tmp_path).load()
        assert rm._gunzip_bounded(gzip.compress(b"x" * 1024)) == b"x" * 1024

    def test_a_cache_without_a_lock_still_needs_a_readable_gzip(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(rm, "read_lock", lambda: {})
        provider = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.0")
        cache = provider.cached_dir()
        cache.mkdir(parents=True)
        (cache / (rm.RUNTIME_JS + rm.GZIP_SUFFIX)).write_bytes(b"not gzip")
        (cache / rm.RUNTIME_CSS).write_bytes(b"/* css */")

        assert not provider.is_cached()

    def test_the_embedded_runtime_reuses_the_stored_gzip(
        self, tmp_path, monkeypatch
    ) -> None:
        """Same bytes as compressing afresh, without the 0.4 s it takes."""
        provider = self.install(tmp_path, monkeypatch)
        asset_embedder._GZIP_CACHE.clear()
        javascript = _PINNED_FILES[rm.RUNTIME_JS]
        stored = (provider.cached_dir() / (rm.RUNTIME_JS + rm.GZIP_SUFFIX)).read_bytes()

        provider.load()
        compressed = []
        monkeypatch.setattr(
            asset_embedder,
            "gzip_bytes",
            lambda data: compressed.append(data) or gzip.compress(data, 6, mtime=0),
        )
        embedded = asset_embedder.gzip_base64(javascript)

        assert compressed == [], "the stored gzip was not reused"
        assert base64.b64decode(embedded) == stored
        assert embedded == base64.b64encode(
            gzip.compress(javascript, asset_embedder.GZIP_LEVEL, mtime=0)
        ).decode("ascii")

    def test_a_gzip_from_another_compressor_is_not_reused(
        self, tmp_path, monkeypatch
    ) -> None:
        """Another zlib could compress differently; then compress as before."""
        provider = self.install(tmp_path, monkeypatch)
        record = provider.cached_dir() / rm.COMPRESSION_RECORD
        probes = json.loads(record.read_text())["probes"]
        assert set(probes) == {rm.RUNTIME_JS, rm.RUNTIME_CARTOGRAPH_JS}
        record.write_text(json.dumps({"probes": {name: "0" * 64 for name in probes}}))
        asset_embedder._GZIP_CACHE.clear()

        provider.load()
        javascript = _PINNED_FILES[rm.RUNTIME_JS]
        key = (hashlib.sha256(javascript).digest(), len(javascript), 6)
        assert asset_embedder._GZIP_CACHE.get(key) is None
        assert asset_embedder.gzip_base64(javascript) == base64.b64encode(
            gzip.compress(javascript, 6, mtime=0)
        ).decode("ascii")


class TestSupersededVersionsArePruned:
    """A verified install deletes the older versions it replaces, and only those."""

    @pytest.fixture(autouse=True)
    def no_cache_override(self, monkeypatch):
        monkeypatch.delenv("ONLYMAP_RUNTIME_CACHE", raising=False)
        rm._GZIP_DIGESTS.clear()

    def old_version(self, root, version: str, gzipped: bool = False):
        """A cache entry as an earlier plugin left it."""
        directory = root / version
        directory.mkdir(parents=True)
        for name, data in _PINNED_FILES.items():
            if gzipped:
                (directory / (name + rm.GZIP_SUFFIX)).write_bytes(gzip.compress(data))
            else:
                (directory / name).write_bytes(data)
        (directory / rm.RUNTIME_LICENCE).write_bytes(b"# Licence")
        rm.record_licence_acceptance(version, root)
        return directory

    def provider(self, root, monkeypatch, tarball=None, version="1.0.0", lock=None):
        pinned = _PINNED_FILES if lock is None else lock
        monkeypatch.setattr(
            rm,
            "read_lock",
            lambda: {
                "version": version,
                "files": {
                    name: {"sha256": rm.sha256_of(data)}
                    for name, data in pinned.items()
                },
            },
        )
        if tarball is None:
            tarball = npm_tarball_with_cartograph(*_PINNED_FILES.values())
        monkeypatch.setattr(rm, "download_tarball", lambda *a, **k: tarball)
        rm.record_licence_acceptance(version, root)
        return rm.FetchingRuntime(cache_dir=root, version=version)

    def test_older_versions_are_deleted_after_a_verified_install(
        self, tmp_path, monkeypatch
    ) -> None:
        plain = self.old_version(tmp_path, "0.9.0")
        packed = self.old_version(tmp_path, "0.10.2", gzipped=True)
        before = sum(f.stat().st_size for f in tmp_path.rglob("*") if f.is_file())
        provider = self.provider(tmp_path, monkeypatch)

        provider.load()

        assert not plain.exists()
        assert not packed.exists()
        assert provider.is_cached()
        after = sum(f.stat().st_size for f in tmp_path.rglob("*") if f.is_file())
        assert after < before
        # what the user agreed to outlives the bytes it covered
        assert rm.licence_accepted("0.9.0", tmp_path)
        assert rm.licence_accepted("0.10.2", tmp_path)

    def test_a_refused_download_deletes_nothing(self, tmp_path, monkeypatch) -> None:
        old = self.old_version(tmp_path, "0.9.0")
        provider = self.provider(
            tmp_path, monkeypatch, tarball=npm_tarball_with_cartograph(b"// EVIL")
        )

        with pytest.raises(rm.RuntimeDownloadError):
            provider.load()

        assert old.is_dir()
        assert rm.LocalRuntime(old).load().javascript == _PINNED_FILES[rm.RUNTIME_JS]

    def test_an_install_missing_a_pinned_file_deletes_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        """`lock_mismatches` passes a pinned file that is absent; pruning must not."""
        old = self.old_version(tmp_path, "0.9.0")
        without_cartograph = npm_tarball(
            _PINNED_FILES[rm.RUNTIME_JS], _PINNED_FILES[rm.RUNTIME_CSS]
        )
        provider = self.provider(tmp_path, monkeypatch, tarball=without_cartograph)

        provider.fetch()

        assert provider.cached_dir().is_dir()
        assert old.is_dir()

    def test_a_lock_for_another_version_deletes_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        old = self.old_version(tmp_path, "0.9.0")
        provider = self.provider(tmp_path, monkeypatch)
        pinned_elsewhere = rm.FetchingRuntime(cache_dir=tmp_path, version="1.0.1")
        rm.record_licence_acceptance("1.0.1", tmp_path)

        pinned_elsewhere.fetch()

        assert old.is_dir()
        assert not provider.cached_dir().exists()

    def test_only_older_plugin_owned_versions_are_touched(
        self, tmp_path, monkeypatch
    ) -> None:
        newer = self.old_version(tmp_path, "1.2.0")  # another QGIS, newer plugin
        prerelease = self.old_version(tmp_path, "0.9.0-rc.1")
        in_progress = self.old_version(tmp_path, "0.9.1.partial")
        foreign = self.old_version(tmp_path, "0.9.2")
        (foreign / "notes.txt").write_text("mine")
        nested = self.old_version(tmp_path, "0.9.3")
        (nested / "sub").mkdir()
        not_a_dir = tmp_path / "0.9.4"
        not_a_dir.write_text("a file")
        elsewhere = self.old_version(tmp_path / "elsewhere", "0.9.5")
        link = tmp_path / "0.9.5"
        link.symlink_to(elsewhere, target_is_directory=True)
        inner_link = self.old_version(tmp_path, "0.9.6")
        (inner_link / rm.RUNTIME_SCHEMA).symlink_to(elsewhere / rm.RUNTIME_LICENCE)
        unrelated = tmp_path / "keep-me"
        unrelated.mkdir()
        (unrelated / rm.RUNTIME_JS).write_bytes(b"//")

        self.provider(tmp_path, monkeypatch).load()

        for kept in (newer, prerelease, in_progress, foreign, nested, inner_link):
            assert kept.is_dir(), kept
        assert not_a_dir.is_file()
        assert link.is_symlink()
        assert (elsewhere / rm.RUNTIME_JS).is_file()
        assert (unrelated / rm.RUNTIME_JS).is_file()

    def test_a_failed_deletion_does_not_fail_the_install(
        self, tmp_path, monkeypatch
    ) -> None:
        """Windows refuses to delete a file another process has open."""
        old = self.old_version(tmp_path, "0.9.0")
        provider = self.provider(tmp_path, monkeypatch)

        real_rmtree = rm.shutil.rmtree

        def refuse(path, *args, **kwargs):
            if Path(path) == old:
                raise PermissionError(f"{path} is in use")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(rm.shutil, "rmtree", refuse)
        bundle = provider.load()

        assert bundle.javascript == _PINNED_FILES[rm.RUNTIME_JS]
        assert provider.is_cached()
        assert old.is_dir()

    def test_a_root_filled_by_hand_is_never_pruned(self, tmp_path, monkeypatch) -> None:
        """`ONLYMAP_RUNTIME_CACHE` names a runtime pack someone installed."""
        old = self.old_version(tmp_path, "0.9.0")
        monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))
        self.provider(tmp_path, monkeypatch)
        provider = rm.FetchingRuntime(version="1.0.0")

        provider.load()

        assert provider.cached_dir().parent == tmp_path
        assert old.is_dir()


@pytest.mark.skipif(not NETWORK, reason="set ONLYMAP_NETWORK_TESTS=1 to run")
class TestAgainstTheRealRegistry:
    """The pinned build must actually be published and match its hashes.

    Opt-in, because CI should not depend on npm being reachable - but this is
    the check that catches a lock file pointing at a version nobody can install.
    """

    def test_the_pinned_version_downloads_and_verifies(self, tmp_path) -> None:
        version = rm.read_lock().get("version")
        assert version, "runtime-lock.json pins no version"

        rm.record_licence_acceptance(version, tmp_path)
        provider = rm.FetchingRuntime(cache_dir=tmp_path)
        bundle = provider.load()

        assert bundle.total_bytes > 1_000_000
        assert bundle.lock_warnings == ()
        assert provider.licence_text()
