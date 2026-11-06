"""The live preview's reload listener, in a real browser.

The page a live preview serves carries the server revision it was published
as; the server's event stream starts with its own. These drive that listener
(`live_server.reload_listener`) against a real `PreviewServer`, and against a
stream that keeps dropping, and look at what the tab shows.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from nika_onlymap_exporter.ui.live_server import (
    RELOAD_PATH,
    PreviewServer,
    reload_listener,
)


def _page(revision: int) -> str:
    return (
        "<!doctype html><html><body>"
        f"<p id='shown'>{revision}</p>{reload_listener(revision)}"
        "</body></html>"
    )


def _shows(revision: int) -> str:
    return f"document.getElementById('shown').textContent === '{revision}'"


class TestARebuildWhileTheTabReloads:
    def test_it_is_followed_even_before_the_stream_connects(
        self, page, tmp_path
    ) -> None:
        """Rebuild 1 makes the tab reload; rebuild 2 lands after the tab got
        rebuild 1's page and before its stream connected. The tab still ends
        on rebuild 2's page, and then stays."""
        (tmp_path / "index.html").write_text(_page(2), encoding="utf-8")
        server = PreviewServer(tmp_path)
        server.start()
        try:
            server.notify_reload()
            server.notify_reload()  # revision 2 published, nobody listening
            loads: list[int] = []

            def rebuild_one_first(route) -> None:
                loads.append(1)
                if len(loads) == 1:
                    route.fulfill(status=200, content_type="text/html", body=_page(1))
                else:
                    route.continue_()

            page.route("**/index.html", rebuild_one_first)
            page.goto(server.url)
            page.wait_for_function(_shows(2), timeout=15_000)
            page.wait_for_timeout(1_500)
            assert len(loads) == 2
            assert page.evaluate(_shows(2))
        finally:
            server.stop()


class _DroppingStream(BaseHTTPRequestHandler):
    """A page at revision 0, and a stream that answers and hangs up at once."""

    connections: ClassVar[list[int]] = []

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path.startswith(RELOAD_PATH):
            type(self).connections.append(1)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"data: rev:0\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        body = _page(0).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestAStreamThatKeepsDropping:
    def test_a_tab_reconnects_after_every_drop_it_recovered_from(self, page) -> None:
        """The five retries are for a server that is gone, not a lifetime
        allowance: a stream that drops and reconnects - a sleep, a network
        change, more than five times over a long session - keeps the tab
        following."""
        _DroppingStream.connections = []
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _DroppingStream)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/index.html")
            deadline_ms = 20_000
            waited = 0
            while len(_DroppingStream.connections) < 8 and waited < deadline_ms:
                page.wait_for_timeout(250)
                waited += 250
            assert len(_DroppingStream.connections) >= 8
        finally:
            httpd.shutdown()
            httpd.server_close()
