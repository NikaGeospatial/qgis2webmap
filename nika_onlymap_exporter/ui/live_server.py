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
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

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


# `bytes=N-M`, `bytes=N-` or `bytes=-K`: one range, which is all a COG reader
# sends. A comma means several, and those fall back to the whole file.
_SINGLE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class _BoundedReader:
    """A file that ends `length` bytes after where it was opened.

    `SimpleHTTPRequestHandler.do_GET` copies whatever `send_head` returns to
    the socket until it reads empty, then closes it. Handing it the open file
    would send everything after the range as well.
    """

    def __init__(self, handle: BinaryIO, length: int) -> None:
        self._handle = handle
        self._remaining = length

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if size < 0 or size > self._remaining:
            size = self._remaining
        chunk = self._handle.read(size)
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        self._handle.close()


class _PreviewHandler(SimpleHTTPRequestHandler):
    """Serves the preview directory, plus the reload event stream."""

    # Kept quiet: the QGIS Python console is not an access log, and a request
    # per asset per reload would bury anything the user needs to see.
    def log_message(self, format: str, *args: object) -> None:
        pass

    def handle_error(self, request: object, client_address: object) -> None:
        """Swallow the traceback when a browser hangs up mid-response.

        `socketserver` prints an unhandled exception from a handler thread to
        stderr, which in QGIS means a multi-line traceback in the Python
        console. A disconnect is not an error here: a preview page is megabytes,
        and a user who navigates away, hits reload, or closes the tab during the
        load severs the socket by definition. Reporting it would make ordinary
        browsing look like a plugin fault, in the same console `log_message` is
        kept quiet for.

        Anything else still surfaces, because a real failure in the handler is
        worth seeing.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)  # type: ignore[misc]

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == RELOAD_PATH:
            self._serve_events()
            return
        super().do_GET()

    def send_head(self):  # type: ignore[no-untyped-def]  # stdlib signature
        """Answer a single byte range with 206, the way a raster needs.

        A raster preview is a Cloud-Optimized GeoTIFF, and the runtime reads one
        in pieces: `Range: bytes=N-M` for the header, then for each tile. The
        stock handler ignores `Range` and returns 200 with the whole file, which
        the reader then slices at the offset it asked for - wrong bytes, a
        "Failed to decode deflate" in the console, and a map with no rasters.

        Only the single-range forms are handled (`N-M`, `N-`, `-K`); a
        multi-range request gets the whole file, which is what the stock
        handler did for every request. Path handling is the stock handler's,
        so a range can no more leave the preview directory than a plain GET.
        """
        byte_range = self.headers.get("Range", "")
        match = _SINGLE_RANGE.fullmatch(byte_range.strip())
        path = self.translate_path(self.path)
        if match is None or not Path(path).is_file():
            return super().send_head()

        size = Path(path).stat().st_size
        first, last = match.group(1), match.group(2)
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        elif last:
            start = max(size - int(last), 0)
            end = size - 1
        else:
            return super().send_head()

        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        handle = open(path, "rb")  # noqa: SIM115 - closed by do_GET after copy
        handle.seek(start)
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return _BoundedReader(handle, length)

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
        # Whether a page has ever reached this server, and since when none
        # has. Both feed `idle_seconds`; see it for why a boolean will not do.
        self._ever_connected = False
        self._empty_since: float | None = None
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
            self._ever_connected = True
            self._empty_since = None

    def unregister(self, inbox: queue.Queue[str]) -> None:
        with self._lock:
            if inbox in self._clients:
                self._clients.remove(inbox)
            if not self._clients:
                self._empty_since = time.monotonic()

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def idle_seconds(self) -> float | None:
        """How long no page has been listening, or `None` if one is.

        **The reason this is a duration and not a boolean.** `notify_reload`
        makes the open tab reload itself, which tears down its `EventSource` and
        opens a new one - so the client count passes through zero on *every*
        rebuild. Anything that acted on "no clients right now" would shut the
        server down on the user's first edit.

        `None` until a page has ever connected, too: a server started moments
        ago and not yet reached by the browser has no clients for a perfectly
        ordinary reason, and is not idle.

        Monotonic rather than wall-clock, so a clock change mid-session cannot
        make a live preview look abandoned.
        """
        with self._lock:
            if self._clients or not self._ever_connected:
                return None
            if self._empty_since is None:
                return None
            return time.monotonic() - self._empty_since

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
