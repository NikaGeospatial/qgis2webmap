"""The runtime licence prompt and the QGIS-stack downloader.

Needs PyQGIS and Qt. The pure fetch/verify logic is covered in
`tests/unit/test_runtime_fetch.py`; this tier covers the parts that only exist
because QGIS does.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.packaging import runtime_manager as rm
from nika_onlymap_exporter.ui.runtime_setup import (
    BUNDLED_LICENCE,
    RuntimeLicenceDialog,
    bundled_licence_text,
    ensure_runtime,
    make_qgis_downloader,
)

qgis_core = pytest.importorskip("qgis.core")


class TestBundledLicence:
    def test_the_licence_ships_with_the_plugin(self) -> None:
        """It has to be readable *before* the runtime is downloaded.

        Shipping the licence is not shipping the software: text is reviewable,
        which is exactly what the minified bundle is not.
        """
        assert BUNDLED_LICENCE.is_file()

    def test_it_is_the_onlymap_licence(self) -> None:
        text = bundled_licence_text()
        assert "OnlyMap" in text
        assert "License" in text or "Licence" in text

    def test_it_states_the_commercial_terms(self) -> None:
        """The clause a QGIS user most needs to see before accepting."""
        text = bundled_licence_text()
        assert "Non-Commercial" in text


class TestLicenceDialog:
    def test_it_cannot_be_accepted_without_ticking(self, qgis_app) -> None:
        """A default button that installs a commercial licence is not consent."""
        dialog = RuntimeLicenceDialog("1.2.3", "# Terms")
        assert not dialog.ok_button.isEnabled()

        dialog.accepted_box.setChecked(True)
        assert dialog.ok_button.isEnabled()

    def test_it_names_what_is_being_installed(self, qgis_app) -> None:
        """The user should be able to see the exact package and version."""
        from qgis.PyQt.QtWidgets import QLabel

        dialog = RuntimeLicenceDialog("1.2.3", "# Terms")
        shown = " ".join(label.text() for label in dialog.findChildren(QLabel))

        assert "1.2.3" in shown
        assert rm.NPM_PACKAGE in shown

    def test_it_says_the_download_happens_once(self, qgis_app) -> None:
        """The one fact that decides whether a user minds."""
        from qgis.PyQt.QtWidgets import QLabel

        dialog = RuntimeLicenceDialog("1.2.3", "# Terms")
        shown = " ".join(label.text() for label in dialog.findChildren(QLabel))

        assert "once" in shown.lower()
        assert "offline" in shown.lower()

    def test_it_distinguishes_the_plugin_from_the_runtime(self, qgis_app) -> None:
        """One is GPL and free; the other is a commercial product."""
        from qgis.PyQt.QtWidgets import QLabel

        dialog = RuntimeLicenceDialog("1.2.3", "# Terms")
        shown = " ".join(label.text() for label in dialog.findChildren(QLabel))

        assert "open source" in shown.lower()
        assert "commercial" in shown.lower()


class TestEnsureRuntime:
    def test_a_local_runtime_needs_no_prompt(self, qgis_app, tmp_path, monkeypatch):
        """Contributors, CI and offline installs never see a dialog."""
        (tmp_path / rm.RUNTIME_JS).write_bytes(b"// runtime")
        (tmp_path / rm.RUNTIME_CSS).write_bytes(b"/* css */")
        monkeypatch.setenv("ONLYMAP_RUNTIME_DIR", str(tmp_path))

        provider = ensure_runtime()

        assert provider is not None
        assert isinstance(provider, rm.LocalRuntime)

    def test_a_runtime_upgrade_does_not_ask_for_the_licence_again(
        self, qgis_app, tmp_path, monkeypatch
    ):
        """Accepted once at the first download; a plugin update that pins a
        newer runtime downloads it without showing the licence again."""
        from nika_onlymap_exporter.ui import runtime_setup

        monkeypatch.setattr(runtime_setup, "discover_runtime_dir", lambda: None)
        monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))
        rm.record_licence_acceptance("0.0.1", tmp_path)

        def no_dialog(*args, **kwargs):
            raise AssertionError("the licence was shown again")

        downloads = []
        monkeypatch.setattr(runtime_setup, "RuntimeLicenceDialog", no_dialog)
        monkeypatch.setattr(
            runtime_setup,
            "_download_with_progress",
            lambda provider, parent: downloads.append(provider.version) or provider,
        )

        provider = ensure_runtime()

        assert provider is not None
        assert downloads == [provider.version]
        assert provider.version != "0.0.1"

    def test_a_cache_missing_only_the_decoder_still_exports_offline(
        self, qgis_app, tmp_path, monkeypatch
    ):
        """A cache from before maps carried the vector tile decoder, on a
        computer with no internet: no licence asked again (the runtime is
        there), no error, and a provider the export can use."""
        import shutil

        from nika_onlymap_exporter.ui import runtime_setup

        source = rm.discover_runtime_dir()
        if source is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        version = rm.read_lock()["version"]
        cache = tmp_path / version
        cache.mkdir()
        for name in (rm.RUNTIME_JS, rm.RUNTIME_CSS, *rm.STORED_GZIPPED):
            stored = rm.stored_path(source, name)
            if stored is not None and name != rm.MVT_WORKER:
                shutil.copy(stored, cache / stored.name)
        monkeypatch.setattr(runtime_setup, "discover_runtime_dir", lambda: None)
        monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))

        def offline(feedback=None):
            def download(url, on_progress=None):
                raise rm.RuntimeDownloadError("no network")

            return download

        def must_not(*args, **kwargs):
            raise AssertionError("the user was interrupted")

        monkeypatch.setattr(runtime_setup, "make_qgis_downloader", offline)
        monkeypatch.setattr(runtime_setup, "RuntimeLicenceDialog", must_not)
        monkeypatch.setattr(runtime_setup.QMessageBox, "critical", must_not)

        provider = ensure_runtime()

        assert isinstance(provider, rm.FetchingRuntime)
        assert provider.is_core_cached() and not provider.is_cached()


class TestQgisDownloader:
    def test_a_cancelled_feedback_aborts(self, qgis_app) -> None:
        """Cancel has to work even though no byte progress is reported."""
        feedback = qgis_core.QgsFeedback()
        feedback.cancel()

        with pytest.raises(rm.RuntimeDownloadError, match="cancelled"):
            make_qgis_downloader(feedback)(
                rm.tarball_url(rm.read_lock().get("version") or "0.0.0")
            )

    def test_a_bad_url_reports_the_proxy_hint(self, qgis_app) -> None:
        """The most likely cause on the machines this plugin targets."""
        with pytest.raises(rm.RuntimeDownloadError, match="proxy"):
            make_qgis_downloader()(
                "https://registry.npmjs.org/@nika-js/onlymap/-/nope-0.0.0.tgz"
            )
