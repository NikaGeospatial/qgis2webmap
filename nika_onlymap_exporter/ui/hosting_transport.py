"""Hosting requests, routed through QGIS's own network stack.

The same argument `runtime_setup.make_qgis_downloader` makes, and it applies
harder here: `urllib` reads `http_proxy` from the environment and nothing else,
while the government and corporate GIS users this plugin is aimed at configure
their proxy in **Settings -> Options -> Network** and nowhere else. A publish
that only works for people with no proxy is a publish that fails for exactly
the audience most likely to need hosting.

`QgsBlockingNetworkRequest` also brings the SSL exceptions and stored
credentials their organisation has already accepted, which is not something
this plugin should be reimplementing.

Kept out of `hosting/` so that package stays importable - and testable - with
no Qt at all.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Any

from ..hosting.client import (
    USER_AGENT,
    HostingError,
    HttpRequest,
    HttpResponse,
    Transport,
)


def make_qgis_transport(feedback: Any = None) -> Transport:
    """A `Transport` backed by QGIS networking. `feedback` makes Cancel work."""

    def send(request: HttpRequest) -> HttpResponse:
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QByteArray, QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        raw = QNetworkRequest(QUrl(request.url))
        raw.setRawHeader(b"User-Agent", USER_AGENT.encode("ascii"))
        for name, value in request.headers.items():
            raw.setRawHeader(name.encode("ascii"), value.encode("utf-8"))

        fetcher = QgsBlockingNetworkRequest()
        body = QByteArray(request.body or b"")
        if request.method == "PUT":
            code = fetcher.put(raw, body, feedback)
        elif request.method == "POST":
            code = fetcher.post(raw, body, False, feedback)
        else:
            code = fetcher.get(raw, False, feedback)

        if feedback is not None and feedback.isCanceled():
            raise HostingError("The upload was cancelled. Nothing was published.")

        reply = fetcher.reply()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        content = bytes(reply.content())

        # A transport-level failure with no HTTP status is a connection
        # problem, and must be raised rather than reported as a status the
        # caller would then try to interpret. A 4xx/5xx *is* a status, and the
        # client's own handling of it says far more than
        # `QgsBlockingNetworkRequest`'s generic message.
        if code != QgsBlockingNetworkRequest.ErrorCode.NoError and status is None:
            raise HostingError(
                f"Could not reach {request.url}.\n\n{fetcher.errorMessage()}\n\n"
                "If this computer reaches the internet through a proxy, check "
                "Settings -> Options -> Network in QGIS."
            )

        return HttpResponse(status=int(status or 0), body=content)

    return send
