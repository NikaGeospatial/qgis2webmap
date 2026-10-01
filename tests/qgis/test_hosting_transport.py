"""The QGIS-backed transport, against a real server on this machine.

The unit tier proves the client hands the transport an open file. Only a real
`QgsBlockingNetworkRequest` can prove the other half: that QGIS sends that file
as a stream and the bytes arrive whole. Loopback only; nothing leaves the box.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from typing import ClassVar

import pytest

from nika_onlymap_exporter.hosting.client import HttpRequest
from nika_onlymap_exporter.ui.hosting_transport import make_qgis_transport


class _Recorder(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[bytes]] = []

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _Recorder.received.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


class _Unread:
    """An open file that fails the test if Python is asked for its bytes.

    Qt must read the file itself, as it sends; a transport that called `read()`
    here would be holding the whole file in memory first.
    """

    def __init__(self, opened) -> None:
        self.name = opened.name

    def read(self, *_args: object) -> bytes:
        pytest.fail("the transport read the whole file into Python")


@pytest.fixture
def server() -> Iterator[str]:
    _Recorder.received = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/upload"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_file_body_arrives_whole(qgis_app, server, tmp_path) -> None:
    payload = bytes(range(256)) * 8192  # 2 MiB, past any single read buffer
    path = tmp_path / "ortho.tif"
    path.write_bytes(payload)
    send = make_qgis_transport()

    with path.open("rb") as opened:
        response = send(
            HttpRequest(
                method="PUT",
                url=server,
                body=_Unread(opened),
                headers={
                    "Content-Type": "image/tiff",
                    "Content-Length": str(len(payload)),
                },
            )
        )

    assert response.status == 200
    assert _Recorder.received == [payload]


def test_a_bytes_body_still_arrives(qgis_app, server) -> None:
    send = make_qgis_transport()
    response = send(
        HttpRequest(
            method="PUT",
            url=server,
            body=b"hello",
            headers={"Content-Length": "5"},
        )
    )
    assert response.status == 200
    assert _Recorder.received == [b"hello"]


def test_a_file_body_arrives_whole_from_a_worker_thread(
    qgis_app, server, tmp_path
) -> None:
    """The dialog uploads from a `QThread`, where QGIS runs no second thread."""
    from qgis.PyQt.QtCore import QThread

    payload = b"z" * (3 * 1024 * 1024)
    path = tmp_path / "data.geojson"
    path.write_bytes(payload)
    statuses: list[int] = []

    class Worker(QThread):
        def run(self) -> None:
            with path.open("rb") as stream:
                response = make_qgis_transport()(
                    HttpRequest(
                        method="PUT",
                        url=server,
                        body=stream,
                        headers={"Content-Length": str(len(payload))},
                    )
                )
            statuses.append(response.status)

    worker = Worker()
    worker.start()
    assert worker.wait(30_000)

    assert statuses == [200]
    assert _Recorder.received == [payload]
