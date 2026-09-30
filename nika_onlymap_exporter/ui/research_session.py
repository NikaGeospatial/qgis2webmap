"""Research sharing, wired to the dialog: storage location, hooks and sending.

The rules live in `research/` and are pure. This module is the plumbing around
them, and it has two jobs beyond plumbing:

* **Never get in the way.** Every entry point catches every exception and logs
  it at Info. A research failure must not be the reason an export or a publish
  fails, and must not put a message box in front of anyone.
* **Send anonymously, asynchronously, through QGIS.** Requests go through
  `QgsNetworkAccessManager`, for the proxy reason `runtime_setup` gives, and
  are fire-and-forget: the dialog never waits on one. They carry no
  Authorization header - this module never touches the hosting sign-in - and
  neither send nor store cookies.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject
from qgis.PyQt.QtCore import QByteArray, QObject, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..core.export_ir import ExportProject
from ..hosting.client import USER_AGENT, resolve_api_base
from ..packaging.runtime_manager import (
    FetchingRuntime,
    discover_runtime_dir,
    licence_accepted,
)
from ..research.envelope import Environment
from ..research.map_report import ProjectFacts
from ..research.project_facts import collect_facts
from ..research.queue import QueuedReport
from ..research.service import RETRY, ResearchService
from ..writers.onlymap_writer import PLUGIN_VERSION

LOG_TAG = "QGIS2WebMap"

RESEARCH_PATH = "/research/reports"


class Poster(Protocol):
    """Sends one body to `url`, then calls `done` with the HTTP status.

    `None` is a transport failure - no answer at all. The default goes through
    QGIS; the tests pass their own, so no test ever reaches a network.
    """

    def __call__(
        self, url: str, body: bytes, done: Callable[[int | None], None]
    ) -> None: ...


def research_state_path() -> Path:
    """Beside the rest of this QGIS profile's settings, in its own folder."""
    return Path(QgsApplication.qgisSettingsDirPath()) / "qgis2webmap" / "research.json"


def current_environment() -> Environment:
    return Environment.build(PLUGIN_VERSION, Qgis.version(), sys.platform)


def research_url() -> str:
    """The same deployment hosting uses, so a dev build reports to dev."""
    return resolve_api_base() + RESEARCH_PATH


def build_request(url: str) -> QNetworkRequest:
    """The request for one report: JSON, the plugin's UA, no credentials at all."""
    request = QNetworkRequest(QUrl(url))
    request.setHeader(
        QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json"
    )
    # Cloudflare answers Python-urllib's default agent with a 1010; the plugin
    # identifies itself the way every other request it makes does.
    request.setRawHeader(b"User-Agent", USER_AGENT.encode("ascii"))
    manual = QNetworkRequest.LoadControl.Manual
    request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute, manual)
    request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute, manual)
    request.setAttribute(QNetworkRequest.Attribute.AuthenticationReuseAttribute, manual)
    return request


def qgis_poster(url: str, body: bytes, done: Callable[[int | None], None]) -> None:
    """POST through QGIS's network manager; `done` runs when it answers."""
    from qgis.core import QgsNetworkAccessManager

    reply = QgsNetworkAccessManager.instance().post(
        build_request(url), QByteArray(body)
    )

    def finished() -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is None and reply.error() != QNetworkReply.NetworkError.NoError:
            log_info(f"Research report not sent: {reply.errorString()}")
        reply.deleteLater()
        done(int(status) if status is not None else None)

    reply.finished.connect(finished)


def log_info(message: str) -> None:
    QgsMessageLog.logMessage(message, LOG_TAG, level=Qgis.MessageLevel.Info)


def _log_failure(action: str) -> None:
    log_info(f"Research sharing: {action} failed, ignored.\n{traceback.format_exc()}")


class ResearchSession(QObject):
    """The dialog's handle on research. Every method is safe to call anywhere."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        path: Path | None = None,
        poster: Poster | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._poster = poster
        self._service: ResearchService | None = None
        self._sending = False
        self._again = False

    # ---- The service -------------------------------------------------------

    def service(self) -> ResearchService | None:
        """Loaded on first use; `None` if even that failed (logged)."""
        if self._service is None:
            try:
                self._service = ResearchService(
                    self._path or research_state_path(), current_environment()
                )
            except Exception:
                _log_failure("loading the local research state")
                return None
        return self._service

    # ---- Hooks -------------------------------------------------------------

    def record_output(
        self,
        export: ExportProject | None,
        identity: str,
        project: QgsProject | None,
        *,
        hosted: bool = False,
        password: bool = False,
    ) -> None:
        """A successful export or publish: count it, maybe queue it, then send."""
        try:
            service = self.service()
            if service is None or export is None or not service.collecting():
                return
            facts = facts_for(project, export)
            service.record_output(
                export, identity, facts=facts, hosted=hosted, password=password
            )
        except Exception:
            _log_failure("recording an export")
        self.flush()

    def record_preview(self) -> None:
        try:
            service = self.service()
            if service is not None:
                service.record_preview()
        except Exception:
            _log_failure("recording a preview")

    def record_failure(self, failure_class: str) -> None:
        try:
            service = self.service()
            if service is not None:
                service.record_failure(failure_class)
        except Exception:
            _log_failure("recording a failure")

    def record_runtime_missing(self) -> None:
        """The runtime is still missing after asking: a failure only if accepted.

        Declining the licence is a choice, not a failure. Accepting it and
        still having no runtime means the download went wrong.
        """
        try:
            if discover_runtime_dir() is not None:
                return
            version = FetchingRuntime().version
            if version and licence_accepted(version):
                self.record_failure("runtime_download")
        except Exception:
            _log_failure("checking the runtime download")

    # ---- Sending -----------------------------------------------------------

    def flush(self) -> None:
        """Send what is queued, one report at a time, without waiting for any."""
        try:
            if self._sending:
                self._again = True
                return
            service = self.service()
            if service is None:
                return
            self._send_next(service)
        except Exception:
            self._sending = False
            _log_failure("sending")

    def _send_next(self, service: ResearchService) -> None:
        pending = service.prepare_flush()
        if not pending:
            self._finish()
            return
        url = research_url()
        item = pending[0]
        self._sending = True
        poster = self._poster or qgis_poster
        poster(
            url,
            item.body.encode("utf-8"),
            lambda status: self._on_answer(service, item, status),
        )

    def _on_answer(
        self, service: ResearchService, item: QueuedReport, status: int | None
    ) -> None:
        try:
            outcome = service.on_response(item.id, status)
            if outcome == RETRY:
                # Offline, the server is having a moment, or it turned this
                # report away without saying why: stop here and try the whole
                # queue again at the next dialog open or export. A report
                # turned away too often is dropped (`GAVE_UP`), and the send
                # carries on with the next one.
                self._finish()
                return
            self._sending = False
            self._send_next(service)
        except Exception:
            self._sending = False
            _log_failure("handling the server's answer")

    def _finish(self) -> None:
        self._sending = False
        if self._again:
            self._again = False
            self.flush()


def facts_for(project: QgsProject | None, export: ExportProject) -> ProjectFacts:
    """Source facts for the exported layers; empty rather than failing."""
    if project is None:
        return ProjectFacts()
    try:
        return collect_facts(
            project, [layer.layer_id for layer in export.exportable_layers]
        )
    except Exception:
        _log_failure("reading layer formats")
        return ProjectFacts()
