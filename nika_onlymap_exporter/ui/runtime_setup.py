"""Getting the OnlyMap runtime onto the machine, once, with consent.

The plugin is GPL and its source is public. The OnlyMap runtime it embeds into
exported maps is neither: it is a closed-source commercial library, so it does
not travel inside the plugin (QGIS requires all plugin code to be available in
source form) and is fetched on first use instead.

That makes this the moment the user acquires a proprietary library. The licence
says access "does not by itself grant any rights", so the terms are shown and
accepted *before* anything is downloaded, not after.

All Qt lives here. `packaging/runtime_manager.py` stays pure Python so the
Processing algorithm can run headless and the tests need no display.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QTextBrowser,
    QVBoxLayout,
)

from ..packaging.runtime_manager import (
    NPM_PACKAGE,
    RUNTIME_DOWNLOAD_SIZE,
    FetchingRuntime,
    RuntimeDownloadError,
    RuntimeProvider,
    RuntimeUnavailableError,
    default_provider,
    discover_runtime_dir,
    record_licence_acceptance,
    tarball_url,
)

# Shipped alongside the plugin so the terms are readable before any download.
# Kept in sync with the published package by scripts/lock_runtime.py.
BUNDLED_LICENCE = (
    Path(__file__).resolve().parent.parent / "runtime" / "ONLYMAP-LICENSE.md"
)

INTRO = (
    "<p><b>QGIS2WebMap needs the OnlyMap runtime to build a map.</b></p>"
    "<p>The runtime is the code that draws the map in a browser. It is embedded "
    "in every map you export, which is what lets someone open your map with no "
    "internet connection and no software installed.</p>"
    f"<p>It is downloaded <b>once on this computer</b> "
    f"({RUNTIME_DOWNLOAD_SIZE}). Every export after that works offline.</p>"
    "<p>QGIS2WebMap is free and open source. The OnlyMap runtime is a separate "
    "commercial product by NIKA with its own licence, shown below.</p>"
)


class RuntimeLicenceDialog(QDialog):
    """Shows the OnlyMap licence and asks the user to accept it."""

    def __init__(self, version: str, licence_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install the OnlyMap runtime")
        self.setMinimumSize(640, 560)

        layout = QVBoxLayout(self)

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        source = QLabel(f"<p>Source: <code>{NPM_PACKAGE} {version}</code></p>")
        source.setWordWrap(True)
        source.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(source)

        terms = QTextBrowser()
        terms.setMarkdown(licence_text)
        terms.setOpenExternalLinks(True)
        layout.addWidget(terms, stretch=1)

        self.accepted_box = QCheckBox("I accept the OnlyMap licence")
        layout.addWidget(self.accepted_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Download and install")
        # Enabled only by the checkbox: a dialog whose default button installs
        # a proprietary licence without a deliberate act is not consent.
        self.ok_button.setEnabled(False)
        self.accepted_box.toggled.connect(self.ok_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def make_qgis_downloader(feedback=None):
    """A downloader that goes through QGIS's own network stack.

    `urllib` reads `http_proxy` from the environment and nothing else. QGIS
    users configure their proxy in **Settings → Options → Network**, and on a
    corporate or government machine that is frequently the only place it is
    configured - so a stdlib download simply times out for exactly the users
    this plugin is aimed at. `QgsBlockingNetworkRequest` honours that
    configuration, along with any SSL exceptions and stored credentials the
    user's organisation has already accepted.

    **No byte progress.** Neither `downloadProgress` nor a `QgsFeedback`
    reports anything for this request on QGIS 3.44 - both were measured at zero
    events for a one-off download of a few megabytes. The caller therefore shows
    an indeterminate
    bar rather than one stuck at 0%, and `feedback` is what makes Cancel work.
    """

    def download(url: str, on_progress=None) -> bytes:
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"QGIS2WebMap-by-NIKA")

        fetcher = QgsBlockingNetworkRequest()
        code = fetcher.get(request, False, feedback)

        if feedback is not None and feedback.isCanceled():
            raise RuntimeDownloadError("The download was cancelled.")

        if code != QgsBlockingNetworkRequest.ErrorCode.NoError:
            raise RuntimeDownloadError(
                f"Could not download the OnlyMap runtime.\n\n"
                f"{fetcher.errorMessage()}\n\n"
                "If this computer reaches the internet through a proxy, check "
                "Settings -> Options -> Network in QGIS."
            )

        return bytes(fetcher.reply().content())

    return download


def bundled_licence_text() -> str:
    """The licence to show before downloading.

    Read from the plugin rather than from the package, because the package is
    the thing we have not downloaded yet. Shipping the licence text is not the
    same as shipping the software: text is reviewable, which is the whole
    objection to the bundle.
    """
    if BUNDLED_LICENCE.is_file():
        return BUNDLED_LICENCE.read_text(encoding="utf-8", errors="replace")
    return (
        "The OnlyMap licence could not be read from this installation.\n\n"
        "It is published with the package at "
        "https://www.npmjs.com/package/@nika-js/onlymap and applies to the "
        "runtime you are about to download."
    )


def ensure_runtime(parent=None) -> RuntimeProvider | None:
    """Make sure a runtime is available, asking and downloading if needed.

    Returns a provider ready to `load()`, or `None` when the user declined or
    the download failed - in which case they have already been told why, and
    the caller should simply not export.

    A local runtime short-circuits everything: contributors, CI, and machines
    with a manually installed runtime pack never see a prompt.
    """
    if discover_runtime_dir() is not None:
        return default_provider()

    provider = FetchingRuntime()
    if provider.is_cached():
        return provider

    if not provider.version:
        QMessageBox.critical(
            parent,
            "OnlyMap runtime",
            "This installation does not pin a runtime version, so the runtime "
            "cannot be downloaded. Please reinstall the plugin.",
        )
        return None

    dialog = RuntimeLicenceDialog(provider.version, bundled_licence_text(), parent)
    # `exec()`, not `exec_()`: PyQt6 removed the trailing-underscore alias, and
    # QGIS 4 ships PyQt6. Likewise every enum below is accessed through its
    # scope (`Qt.TextFormat.RichText`, not `Qt.RichText`) - PyQt6 dropped the
    # unscoped names, and PyQt5 accepts the scoped form, so scoped is the one
    # spelling that works on both.
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    record_licence_acceptance(provider.version)
    return _download_with_progress(provider, parent)


def _download_with_progress(
    provider: FetchingRuntime, parent
) -> FetchingRuntime | None:
    """Run the download behind a cancellable progress dialog.

    Synchronous on purpose. A background thread would need cancellation,
    partial-state cleanup and a way to stop the user starting a second export
    mid-download; a one-off of a few megabytes does not earn that complexity.

    The bar is **indeterminate** (`0, 0`), because QGIS's network stack reports
    no byte progress for this request. A bar frozen at 0% for the whole
    download reads as a hung plugin; a moving indeterminate one is honest about
    what is known - that work is happening, but not how far along it is.
    """
    from qgis.core import QgsFeedback

    progress = QProgressDialog(
        f"Downloading the OnlyMap runtime ({RUNTIME_DOWNLOAD_SIZE})…",
        "Cancel",
        0,
        0,
        parent,
    )
    progress.setWindowTitle("OnlyMap runtime")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)

    feedback = QgsFeedback()
    progress.canceled.connect(feedback.cancel)
    QApplication.processEvents()

    try:
        provider.fetch(downloader=make_qgis_downloader(feedback))
    except RuntimeUnavailableError as exc:
        progress.close()
        if not feedback.isCanceled():
            QMessageBox.critical(
                parent,
                "OnlyMap runtime",
                f"{exc}\n\nDownload URL:\n{tarball_url(provider.version)}",
            )
        return None
    finally:
        progress.close()

    return provider
