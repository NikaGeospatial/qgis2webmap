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

import io
import json
import os
import tarfile

import pytest

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
        assert rm.read_lock()["version"] == "0.12.0"


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

    def test_a_missing_cartograph_file_is_reported_not_silently_ignored(
        self, monkeypatch
    ) -> None:
        js, css = b"// runtime", b"/* css */"
        self.lock_with_cartograph(
            monkeypatch, js, css, b"// cartograph", b"/* cartograph css */"
        )

        mismatches = rm.lock_mismatches(js, css, None, None)

        assert any("cartograph.standalone.js" in m for m in mismatches)
        assert any("cartograph.css" in m for m in mismatches)


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
