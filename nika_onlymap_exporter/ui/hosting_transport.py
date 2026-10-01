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

A map file arrives as an open file rather than as bytes, and goes to
`QgsBlockingNetworkRequest.put` as a `QFile`, which Qt reads as it sends. Read
into a `QByteArray` instead, a 4 GB raster would sit in memory twice - once as
Python bytes and once as Qt's copy - before the first byte left the machine.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from typing import Any, BinaryIO

from ..hosting.client import (
    USER_AGENT,
    HostingError,
    HttpRequest,
    HttpResponse,
    Transport,
    unreachable_message,
)


def make_qgis_transport(feedback: Any = None) -> Transport:
    """A `Transport` backed by QGIS networking. `feedback` makes Cancel work."""

    def send(request: HttpRequest) -> HttpResponse:
        from qgis.core import Qgis, QgsBlockingNetworkRequest, QgsMessageLog
        from qgis.PyQt.QtCore import QByteArray, QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        raw = QNetworkRequest(QUrl(request.url))
        raw.setRawHeader(b"User-Agent", USER_AGENT.encode("ascii"))
        for name, value in request.headers.items():
            raw.setRawHeader(name.encode("ascii"), value.encode("utf-8"))

        fetcher = QgsBlockingNetworkRequest()
        if request.body is None or isinstance(request.body, bytes):
            body = QByteArray(request.body or b"")
            if request.method == "PUT":
                code = fetcher.put(raw, body, feedback)
            elif request.method == "POST":
                code = fetcher.post(raw, body, False, feedback)
            else:
                code = fetcher.get(raw, False, feedback)
        else:
            code = _put_file(fetcher, raw, request, request.body, feedback)

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
            # The plain-English sentence, shared with the stdlib transport. Qt's
            # own wording ("Connection refused", "Host ... not found") is logged
            # rather than shown: it names a symptom, never what to do.
            QgsMessageLog.logMessage(
                f"Hosting request to {request.url} failed: {fetcher.errorMessage()}",
                "QGIS2WebMap",
                level=Qgis.MessageLevel.Warning,
            )
            raise HostingError(unreachable_message(request.url))

        return HttpResponse(status=int(status or 0), body=content)

    return send


def _put_file(
    fetcher: Any, raw: Any, request: HttpRequest, stream: BinaryIO, feedback: Any
) -> Any:
    """PUT a file Qt reads as it sends, rather than a copy of it in memory.

    The file is reopened by name as a `QFile`: Qt can only stream from a
    `QIODevice`, and the Python file object exists to say which file and to
    keep it open for the length of the attempt. Only an upload streams; any
    other method with a file body is a bug in the caller, said so rather than
    sent somewhere as an empty request.
    """
    from qgis.PyQt.QtCore import QFile, QIODevice

    name = getattr(stream, "name", None)
    if request.method != "PUT" or not isinstance(name, (str, bytes)):
        raise HostingError(
            "This upload could not be sent: the plugin passed it in a form the "
            "QGIS network connection cannot stream. Nothing was published."
        )
    device = QFile(os.fsdecode(name))
    if not device.open(QIODevice.OpenModeFlag.ReadOnly):
        raise HostingError(
            f"Could not read {os.path.basename(os.fsdecode(name))} to upload it. "
            "Nothing was published; export the map again and retry."
        )
    try:
        return fetcher.put(raw, device, feedback)
    finally:
        device.close()
