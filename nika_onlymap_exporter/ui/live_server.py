"""A localhost server for the live preview, with push reload.

**Why a server at all, when the artifact is a file?** Because `file://` gives the
plugin no way to reach the page. Chrome treats file documents as opaque origins,
so the page cannot `fetch` a sibling token file to learn that a rebuild happened
- `preview.py`'s camera script already hit the same wall with `sessionStorage`.
The alternatives were a timed unconditional reload, which re-parses a 5.6 MB
inlined runtime every few seconds, or nothing. A server can simply push.

**Why this is not a fidelity risk.** The live preview is the working loop; the
`file://` path is exercised after export by the real artifact, which is a better
check than a preview copy of it.

Scope is deliberately small: bound to loopback, one directory, read-only, no
directory listing, and it dies with the dialog.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import queue
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The path the injected script opens an EventSource against. Underscored so it
# cannot collide with a file in the preview directory, which is served from the
# same namespace.
RELOAD_PATH = "/__reload"

# How long an idle SSE connection waits before emitting a comment. Without it a
# proxy or the browser may drop a silent connection, and the preview would go
# quietly stale rather than visibly break.
HEARTBEAT_SECONDS = 15.0

# Bound to loopback only. Binding 0.0.0.0 would publish the user's map data to
# their whole network, which is the opposite of what this plugin promises.
BIND_HOST = "127.0.0.1"


class _PreviewHandler(SimpleHTTPRequestHandler):
    """Serves the preview directory, plus the reload event stream."""

    # Kept quiet: the QGIS Python console is not an access log, and a request
    # per asset per reload would bury anything the user needs to see.
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == RELOAD_PATH:
            self._serve_events()
            return
        super().do_GET()

    def do_POST(self) -> None:
        """Read-only by construction.

        The plugin writes the preview; the server only reads it. Refusing writes
        outright means a page served here cannot be talked into modifying the
        user's disk.
        """
        self.send_error(405, "This preview server is read-only")

    def _serve_events(self) -> None:
        server: PreviewServer = self.server.preview  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        inbox: queue.Queue[str] = queue.Queue()
        server.register(inbox)
        try:
            while not server.stopping:
                try:
                    message = inbox.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    # A comment line. The browser ignores it; it exists only to
                    # prove the socket is alive.
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {message}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The user closed the tab. Entirely normal; not worth reporting.
            pass
        finally:
            server.unregister(inbox)


class PreviewServer:
    """Serves one directory on loopback and pushes reload events to it.

    Start it, hand `url` to a browser, call `notify_reload()` after each rebuild,
    and `stop()` when the dialog closes.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._clients: list[queue.Queue[str]] = []
        self._lock = threading.Lock()
        self._stopping = False

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def root(self) -> Path:
        """The directory being served, resolved.

        Exposed so a caller can tell whether a running server is already serving
        the directory it wants, rather than tracking that separately and letting
        the two drift.
        """
        return self._root

    @property
    def port(self) -> int:
        if self._httpd is None:
            raise RuntimeError("the preview server is not running")
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        """The page to open. Explicitly `index.html`, never a directory index."""
        return f"http://{BIND_HOST}:{self.port}/index.html"

    def start(self) -> str:
        """Bind an ephemeral port and serve. Returns `url`.

        Port `0` lets the kernel choose, so two QGIS windows previewing two
        projects cannot collide, and nothing needs a configurable port setting.
        """
        if self._httpd is not None:
            return self.url

        self._stopping = False
        root = self._root

        class _Bound(_PreviewHandler):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, directory=str(root), **kwargs)  # type: ignore[arg-type]

        httpd = ThreadingHTTPServer((BIND_HOST, 0), _Bound)
        # Threads must not keep the QGIS process alive if `stop` is somehow
        # missed - a plugin that prevents QGIS from exiting is unforgivable.
        httpd.daemon_threads = True
        httpd.preview = self  # type: ignore[attr-defined]

        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="qgis2webmap-preview",
            daemon=True,
        )
        self._thread.start()
        return self.url

    def register(self, inbox: queue.Queue[str]) -> None:
        with self._lock:
            self._clients.append(inbox)

    def unregister(self, inbox: queue.Queue[str]) -> None:
        with self._lock:
            if inbox in self._clients:
                self._clients.remove(inbox)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def notify_reload(self) -> None:
        """Tell every connected page to reload itself."""
        with self._lock:
            clients = list(self._clients)
        for inbox in clients:
            inbox.put("reload")

    def stop(self) -> None:
        """Shut down and release every blocked event stream.

        `stopping` is set before `shutdown()` because the SSE handler threads sit
        in a blocking `get`; without the flag they would each wait out a full
        heartbeat before noticing, and `shutdown()` waits for handlers.
        """
        self._stopping = True
        # Wake the blocked handlers now rather than at their next heartbeat.
        with self._lock:
            clients = list(self._clients)
        for inbox in clients:
            inbox.put("stop")

        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)
