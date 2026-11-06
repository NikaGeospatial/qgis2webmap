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


@pytest.fixture(autouse=True)
def _forget_the_session(monkeypatch):
    """What one test's session learnt about the runtime is not the next's."""
    from nika_onlymap_exporter.ui import runtime_setup

    monkeypatch.setattr(runtime_setup, "_session", {})


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

    @pytest.mark.parametrize("network", ["offline", "sign-in page"])
    def test_a_cache_missing_only_the_decoder_still_exports_offline(
        self, qgis_app, tmp_path, monkeypatch, network
    ):
        """A cache from before maps carried the vector tile decoder, on a
        computer with no internet or behind a network that answers with its
        sign-in page: no licence asked again (the runtime is there), no error,
        and a provider the export can use."""
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
                if network == "offline":
                    raise rm.RuntimeDownloadError("no network")
                return b"<html><body>Sign in to the Wi-Fi</body></html>"

            return download

        def must_not(*args, **kwargs):
            raise AssertionError("the user was interrupted")

        monkeypatch.setattr(runtime_setup, "make_qgis_downloader", offline)
        monkeypatch.setattr(runtime_setup, "RuntimeLicenceDialog", must_not)
        monkeypatch.setattr(runtime_setup.QMessageBox, "critical", must_not)

        provider = ensure_runtime()

        assert isinstance(provider, rm.FetchingRuntime)
        assert provider.is_core_cached() and not provider.is_cached()

    def test_a_first_download_whose_decoder_fails_still_exports(
        self, qgis_app, tmp_path, monkeypatch
    ):
        """The runtime arrives and its decoder worker does not: the export goes
        ahead without the worker, with no error box."""
        import io
        import tarfile

        from nika_onlymap_exporter.ui import runtime_setup

        source = rm.discover_runtime_dir()
        if source is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        version = rm.read_lock()["version"]
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode="w:gz") as archive:
            for name in (*rm.STORED_GZIPPED, rm.RUNTIME_LICENCE):
                data = rm.read_runtime_file(source, name)
                if data is None or name == rm.MVT_WORKER:
                    continue
                folder = "" if name == rm.RUNTIME_LICENCE else "dist/"
                info = tarfile.TarInfo(f"{rm.TARBALL_PREFIX}/{folder}{name}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        monkeypatch.setattr(runtime_setup, "discover_runtime_dir", lambda: None)
        monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))
        rm.record_licence_acceptance(version, tmp_path)

        def registry(feedback=None):
            def download(url, on_progress=None):
                if url == rm.tarball_url(version):
                    return package.getvalue()
                return b"<html><body>Sign in to the Wi-Fi</body></html>"

            return download

        def must_not(*args, **kwargs):
            raise AssertionError("the user was told the export failed")

        monkeypatch.setattr(runtime_setup, "make_qgis_downloader", registry)
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


@pytest.fixture
def fresh_session(monkeypatch, tmp_path):
    """No runtime anywhere, the cache in `tmp_path`."""
    from nika_onlymap_exporter.ui import runtime_setup

    monkeypatch.setattr(runtime_setup, "discover_runtime_dir", lambda: None)
    monkeypatch.setenv("ONLYMAP_RUNTIME_CACHE", str(tmp_path))
    return runtime_setup


def _package_without_worker() -> bytes:
    """The pinned runtime's npm tarball, minus the decoder worker."""
    import io
    import tarfile

    source = rm.discover_runtime_dir()
    if source is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode="w:gz") as archive:
        for name in (*rm.STORED_GZIPPED, rm.RUNTIME_LICENCE):
            data = rm.read_runtime_file(source, name)
            if data is None or name == rm.MVT_WORKER:
                continue
            folder = "" if name == rm.RUNTIME_LICENCE else "dist/"
            info = tarfile.TarInfo(f"{rm.TARBALL_PREFIX}/{folder}{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return package.getvalue()


class TestFailedDownload:
    @pytest.mark.parametrize(
        "failure",
        [
            rm.RuntimeDownloadError("Could not download: no route to the proxy"),
            OSError(28, "No space left on device"),
        ],
        ids=["network", "disk"],
    )
    def test_the_error_box_says_why(
        self, qgis_app, fresh_session, tmp_path, monkeypatch, failure
    ) -> None:
        """A download that fails on its own - no Cancel pressed - tells the
        user the cause and the address, rather than ending as silently as a
        cancel."""
        version = rm.read_lock()["version"]
        rm.record_licence_acceptance(version, tmp_path)
        shown: list[str] = []

        def failing(feedback=None):
            def download(url, on_progress=None):
                raise failure

            return download

        monkeypatch.setattr(fresh_session, "make_qgis_downloader", failing)
        monkeypatch.setattr(
            fresh_session.QMessageBox,
            "critical",
            lambda parent, title, text: shown.append(text),
        )

        assert fresh_session.ensure_runtime() is None
        assert len(shown) == 1
        assert str(failure.args[-1]) in shown[0]
        assert rm.tarball_url(version) in shown[0]

    def test_cancel_during_a_first_download_exports_nothing(
        self, qgis_app, fresh_session, tmp_path, monkeypatch
    ) -> None:
        """Cancel under "Downloading the OnlyMap runtime" stops the export,
        even when it is pressed while the decoder (the last part) downloads."""
        version = rm.read_lock()["version"]
        rm.record_licence_acceptance(version, tmp_path)
        package = _package_without_worker()

        def cancelled_at_the_decoder(feedback=None):
            def download(url, on_progress=None):
                if url == rm.tarball_url(version):
                    return package
                feedback.cancel()
                raise rm.RuntimeDownloadError("The download was cancelled.")

            return download

        def must_not(*args, **kwargs):
            raise AssertionError("a cancel was reported as a failure")

        monkeypatch.setattr(
            fresh_session, "make_qgis_downloader", cancelled_at_the_decoder
        )
        monkeypatch.setattr(fresh_session.QMessageBox, "critical", must_not)

        assert fresh_session.ensure_runtime() is None


class TestSessionMemory:
    """What one QGIS session remembers, so that a save nobody pressed for
    never puts the licence or a download on screen."""

    def _count_prompts(self, monkeypatch, runtime_setup, answer):
        prompts: list[int] = []

        class Licence:
            def __init__(self, *args, **kwargs) -> None:
                prompts.append(1)

            def exec(self):
                return answer()

        monkeypatch.setattr(runtime_setup, "RuntimeLicenceDialog", Licence)
        return prompts

    def test_a_declined_licence_is_asked_again_only_when_pressed_for(
        self, qgis_app, fresh_session, monkeypatch
    ) -> None:
        from qgis.PyQt.QtWidgets import QDialog

        never, first_time, always = (
            fresh_session.Ask.NEVER,
            fresh_session.Ask.FIRST_TIME,
            fresh_session.Ask.ALWAYS,
        )

        prompts = self._count_prompts(
            monkeypatch, fresh_session, lambda: QDialog.DialogCode.Rejected
        )

        assert fresh_session.ensure_runtime(ask=never) is None
        assert prompts == [], "a save nobody pressed for asked"
        assert fresh_session.ensure_runtime(ask=first_time) is None
        assert prompts == [1]
        # Declined: picking another layout, editing, closing ask no more.
        for ask in (first_time, never, first_time):
            assert fresh_session.ensure_runtime(ask=ask) is None
        assert prompts == [1]
        # Export pressed: the user may have changed their mind.
        assert fresh_session.ensure_runtime(ask=always) is None
        assert prompts == [1, 1]

    def test_no_second_licence_inside_the_first(
        self, qgis_app, fresh_session, monkeypatch
    ) -> None:
        """A timer firing inside the licence dialog's own event loop (a close
        waiting on its save, an autosave) asks nothing more."""
        from qgis.PyQt.QtWidgets import QDialog

        inner: list[object] = []

        def answer():
            inner.append(fresh_session.ensure_runtime())
            return QDialog.DialogCode.Rejected

        prompts = self._count_prompts(monkeypatch, fresh_session, answer)

        assert fresh_session.ensure_runtime() is None
        assert prompts == [1]
        assert inner == [None]

    def test_a_decoder_that_did_not_come_is_not_fetched_for_every_export(
        self, qgis_app, fresh_session, tmp_path, monkeypatch
    ) -> None:
        """Runtime installed, decoder worker not (the network refused it):
        later exports this session are made without it, with no download."""
        version = rm.read_lock()["version"]
        rm.record_licence_acceptance(version, tmp_path)
        package = _package_without_worker()
        requests: list[str] = []

        def registry(feedback=None):
            def download(url, on_progress=None):
                requests.append(url)
                if url == rm.tarball_url(version):
                    return package
                raise rm.RuntimeDownloadError("refused")

            return download

        monkeypatch.setattr(fresh_session, "make_qgis_downloader", registry)

        first = fresh_session.ensure_runtime()
        assert isinstance(first, rm.FetchingRuntime)
        asked = len(requests)
        for _ in range(3):
            again = fresh_session.ensure_runtime()
            assert isinstance(again, rm.FetchingRuntime)
        assert len(requests) == asked, "the decoder was downloaded again"


class TestCartographBundle:
    def test_a_decoder_that_arrives_later_reaches_the_next_save(
        self, qgis_app, fresh_session, tmp_path
    ) -> None:
        """The cartograph keeps the runtime it read between saves; a decoder
        worker completed since (by a web map export) is read in, rather than
        every later cartograph loading it from the web."""
        import shutil
        import types

        from nika_onlymap_exporter.ui.cartograph.controller import (
            CartographController,
        )

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
        controller = types.SimpleNamespace(dialog=None, _runtime=None, _last_error="")
        never = fresh_session.Ask.NEVER

        first = CartographController._load_runtime(controller, never)
        assert first is not None and first.mvt_worker is None

        worker = rm.stored_path(source, rm.MVT_WORKER)
        assert worker is not None
        shutil.copy(worker, cache / worker.name)
        second = CartographController._load_runtime(controller, never)
        assert second is not None and second.mvt_worker is not None
