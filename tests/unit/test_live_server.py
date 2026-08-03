"""The live preview server: what it serves, what it refuses, and how it stops.

No PyQGIS here - the server is deliberately stdlib-only so it can be tested
without QGIS, and so a bug in it cannot be blamed on the Qt event loop.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import http.client
import threading
import time

import pytest

from nika_onlymap_exporter.ui.live_server import RELOAD_PATH, PreviewServer


@pytest.fixture
def served(tmp_path):
    """A running server over a directory holding one index.html."""
    (tmp_path / "index.html").write_text("<!doctype html><p>map</p>", encoding="utf-8")
    server = PreviewServer(tmp_path)
    server.start()
    try:
        yield server, tmp_path
    finally:
        server.stop()


def _get(server: PreviewServer, path: str, timeout: float = 5.0):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class TestServing:
    def test_it_serves_the_preview_page(self, served) -> None:
        server, _ = served
        status, body = _get(server, "/index.html")
        assert status == 200
        assert b"map" in body

    def test_url_points_at_index_not_a_directory(self, served) -> None:
        server, _ = served
        assert server.url.endswith("/index.html")
        assert server.url.startswith("http://127.0.0.1:")

    def test_it_binds_loopback_only(self, served) -> None:
        """Binding 0.0.0.0 would publish the user's map data to their network."""
        server, _ = served
        assert server.url.startswith("http://127.0.0.1:")

    def test_each_server_gets_its_own_port(self, tmp_path) -> None:
        """Two projects previewed at once must not fight over a port."""
        first, second = PreviewServer(tmp_path), PreviewServer(tmp_path)
        first.start()
        second.start()
        try:
            assert first.port != second.port
        finally:
            first.stop()
            second.stop()


class TestRefusals:
    def test_it_refuses_writes(self, served) -> None:
        """The plugin writes the preview; a served page must not be able to."""
        server, _ = served
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("POST", "/index.html", body=b"x")
            assert connection.getresponse().status == 405
        finally:
            connection.close()

    def test_it_does_not_escape_the_preview_directory(self, served) -> None:
        """`..` must not reach a file the user never meant to serve."""
        server, tmp_path = served
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("private", encoding="utf-8")

        status, body = _get(server, "/../secret.txt")
        assert b"private" not in body
        assert status in (403, 404)

    def test_encoded_traversal_is_also_refused(self, served) -> None:
        """The decoded form must be checked, not the raw string."""
        server, tmp_path = served
        secret = tmp_path.parent / "secret2.txt"
        secret.write_text("private", encoding="utf-8")

        status, body = _get(server, "/%2e%2e/secret2.txt")
        assert b"private" not in body
        assert status in (403, 404)


class TestReload:
    def test_it_pushes_a_reload_to_a_connected_page(self, served) -> None:
        server, _ = served
        received: list[bytes] = []

        def listen() -> None:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=10
            )
            connection.request("GET", RELOAD_PATH)
            response = connection.getresponse()
            # One event is enough; the heartbeat comment is not an event.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                line = response.fp.readline()
                if line.startswith(b"data:"):
                    received.append(line)
                    break
            connection.close()

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()

        deadline = time.monotonic() + 5
        while server.client_count == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.client_count == 1, "the event stream never connected"

        server.notify_reload()
        listener.join(timeout=8)
        assert received and b"reload" in received[0]

    def test_notify_with_no_listeners_is_harmless(self, served) -> None:
        """A preview rebuilt before the browser is open must not raise."""
        server, _ = served
        server.notify_reload()


class TestLifecycle:
    def test_stop_releases_the_event_stream_promptly(self, served) -> None:
        """A blocked SSE handler must not hold the dialog open on close."""
        server, _ = served

        def listen() -> None:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=10
            )
            connection.request("GET", RELOAD_PATH)
            connection.getresponse().read()
            connection.close()

        threading.Thread(target=listen, daemon=True).start()

        deadline = time.monotonic() + 5
        while server.client_count == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.client_count == 1

        started = time.monotonic()
        server.stop()
        # Well under the heartbeat: proof `stop` wakes the handler rather than
        # waiting for it to time out on its own.
        assert time.monotonic() - started < 5

    def test_stop_is_idempotent(self, served) -> None:
        server, _ = served
        server.stop()
        server.stop()

    def test_it_leaves_no_thread_behind(self, tmp_path) -> None:
        """A plugin that leaks threads eventually stops QGIS from exiting."""
        (tmp_path / "index.html").write_text("<p>x</p>", encoding="utf-8")
        before = {t.name for t in threading.enumerate()}

        server = PreviewServer(tmp_path)
        server.start()
        _get(server, "/index.html")
        server.stop()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            leaked = {
                t.name
                for t in threading.enumerate()
                if t.name.startswith("qgis2webmap")
            }
            if not leaked - before:
                break
            time.sleep(0.05)
        remaining = {
            t.name for t in threading.enumerate() if t.name.startswith("qgis2webmap")
        }
        assert not remaining - before, f"threads left running: {remaining}"

    def test_port_before_start_is_an_error_not_a_guess(self, tmp_path) -> None:
        server = PreviewServer(tmp_path)
        with pytest.raises(RuntimeError):
            _ = server.port
