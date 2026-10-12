"""The live preview server: what it serves, what it refuses, and how it stops.

No PyQGIS here - the server is deliberately stdlib-only so it can be tested
without QGIS, and so a bug in it cannot be blamed on the Qt event loop.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import http.client
import queue
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


def _get_range(server: PreviewServer, path: str, byte_range: str):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5.0)
    try:
        connection.request("GET", path, headers={"Range": byte_range})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class TestByteRanges:
    """A raster preview is a COG, and a COG is read in byte ranges.

    The runtime's reader asks for `Range: bytes=N-M` and slices what comes back
    at the offset it asked for. Answer with 200 and the whole file and it
    slices the wrong bytes: the map draws the vectors, the legend lists the
    rasters, and the browser console says "Failed to decode deflate".
    """

    @pytest.fixture
    def tif(self, served):
        server, root = served
        payload = bytes(range(256)) * 400  # 102,400 bytes, every offset distinct
        (root / "layer.tif").write_bytes(payload)
        return server, payload

    def test_a_range_gets_exactly_those_bytes(self, tif) -> None:
        server, payload = tif
        status, headers, body = _get_range(server, "/layer.tif", "bytes=1000-1099")
        assert status == 206
        assert body == payload[1000:1100]
        assert headers["Content-Range"] == f"bytes 1000-1099/{len(payload)}"
        assert headers["Content-Length"] == "100"

    def test_an_open_ended_range_runs_to_the_end(self, tif) -> None:
        server, payload = tif
        status, _, body = _get_range(server, "/layer.tif", "bytes=102000-")
        assert status == 206
        assert body == payload[102000:]

    def test_a_suffix_range_is_the_last_bytes(self, tif) -> None:
        server, payload = tif
        status, _, body = _get_range(server, "/layer.tif", "bytes=-64")
        assert status == 206
        assert body == payload[-64:]

    def test_a_range_past_the_end_is_clamped(self, tif) -> None:
        server, payload = tif
        status, _, body = _get_range(server, "/layer.tif", "bytes=102300-999999")
        assert status == 206
        assert body == payload[102300:]

    def test_a_range_starting_past_the_end_is_refused(self, tif) -> None:
        server, payload = tif
        status, headers, _ = _get_range(server, "/layer.tif", "bytes=999999-")
        assert status == 416
        assert headers["Content-Range"] == f"bytes */{len(payload)}"

    def test_without_a_range_the_whole_file_comes_back(self, tif) -> None:
        server, payload = tif
        assert _get(server, "/layer.tif") == (200, payload)

    def test_a_range_cannot_escape_the_preview_directory(self, served) -> None:
        server, _ = served
        status, _, _ = _get_range(server, "/../../etc/passwd", "bytes=0-10")
        assert status == 404


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


class TestIdleDetection:
    """A closed tab should stop the server; a reload must not.

    The server used to outlive its audience - it ran until the dialog closed,
    with nothing on screen saying a socket was open on the user's machine.
    """

    def test_a_server_nobody_has_opened_is_not_idle(self, tmp_path) -> None:
        """Started moments ago and not yet reached is an ordinary state.

        Reporting it as idle would let a caller shut the server down in the gap
        between starting it and the browser arriving.
        """
        server = PreviewServer(tmp_path)
        assert server.idle_seconds is None

    def test_a_connected_page_is_not_idle(self, tmp_path) -> None:
        server = PreviewServer(tmp_path)
        inbox: queue.Queue[str] = queue.Queue()
        server.register(inbox)
        assert server.idle_seconds is None

    def test_idle_time_starts_when_the_last_page_goes(self, tmp_path) -> None:
        server = PreviewServer(tmp_path)
        inbox: queue.Queue[str] = queue.Queue()
        server.register(inbox)
        server.unregister(inbox)

        idle = server.idle_seconds
        assert idle is not None
        assert idle >= 0

    def test_a_reload_does_not_read_as_idle(self, tmp_path) -> None:
        """The race this whole design exists for.

        `notify_reload` makes the page reload, which drops its `EventSource` and
        opens a new one - so the client count passes through zero on *every*
        rebuild. A caller acting on "no clients right now" would kill the server
        on the user's first edit.
        """
        server = PreviewServer(tmp_path)
        first: queue.Queue[str] = queue.Queue()
        server.register(first)

        # The reload: the old stream ends, the new one opens.
        server.unregister(first)
        second: queue.Queue[str] = queue.Queue()
        server.register(second)

        assert server.idle_seconds is None, "a reconnect must clear the idle clock"

    def test_the_clock_restarts_after_a_reconnect(self, tmp_path) -> None:
        """A second close is measured from itself, not from the first."""
        server = PreviewServer(tmp_path)
        first: queue.Queue[str] = queue.Queue()
        server.register(first)
        server.unregister(first)
        time.sleep(0.05)

        second: queue.Queue[str] = queue.Queue()
        server.register(second)
        server.unregister(second)

        idle = server.idle_seconds
        assert idle is not None
        assert idle < 0.05, "the clock should time the latest close, not the first"
