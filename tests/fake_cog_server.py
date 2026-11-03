"""A local web server for remote COGs, and a COG writer that needs no GDAL.

A raster QGIS reads over the web is exported as a live `COGLayer` on its own
address, so the tests need an address: this serves a dict of files on
127.0.0.1 and a free port, from a thread, the way a COG host does - `HEAD`,
`GET` with `Range: bytes=N-M` answered 206 with a `Content-Range`, and CORS
headers that let a page read the file and its ranges. `requests` records
every request as `(method, path, range)`, so a test can show the export read
nothing and the page read only parts.

`rgb_cog` writes a tiled, DEFLATE-compressed RGB GeoTIFF in EPSG:3857 with
the standard library alone, for the browser tier, which has no GDAL. One
tile and no overviews: the smallest file a COG reader opens like any COG.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import struct
import threading
import zlib
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType

_CONTENT_TYPES = {"html": "text/html", "js": "text/javascript", "css": "text/css"}


class FakeCogServer:
    """Serve `files` (path -> bytes) until the `with` block ends."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[tuple[str, str, str | None]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_port}"

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def __enter__(self) -> FakeCogServer:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        files, requests = self.files, self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _headers(self, status: int, length: int, extra: dict[str, str]) -> None:
                self.send_response(status)
                kind = self.path.split("?")[0].rpartition(".")[2]
                self.send_header("Content-Type", _CONTENT_TYPES.get(kind, "image/tiff"))
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header(
                    "Access-Control-Expose-Headers",
                    "Content-Range, Content-Length, Accept-Ranges",
                )
                for name, text in extra.items():
                    self.send_header(name, text)
                self.end_headers()

            def _reply(self, with_body: bool) -> None:
                ranged = self.headers.get("Range")
                requests.append((self.command, self.path, ranged))
                data = files.get(self.path.split("?")[0])
                if data is None:
                    self._headers(404, 0, {})
                    return
                body, status, extra = data, 200, {}
                if ranged:
                    start, _, end = ranged.split("=", 1)[1].partition("-")
                    first = int(start)
                    last = min(int(end or len(data) - 1), len(data) - 1)
                    body, status = data[first : last + 1], 206
                    extra["Content-Range"] = f"bytes {first}-{last}/{len(data)}"
                self._headers(status, len(body), extra)
                if with_body:
                    self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                requests.append(("OPTIONS", self.path, None))
                self._headers(204, 0, {})

            def do_HEAD(self) -> None:
                self._reply(False)

            def do_GET(self) -> None:
                self._reply(True)

        return Handler


def rgb_cog(
    origin: tuple[float, float],
    pixel_size: float,
    pixel: Callable[[int, int], tuple[int, int, int]],
    size: int = 256,
) -> bytes:
    """A `size` x `size` RGB GeoTIFF in EPSG:3857, one DEFLATE tile.

    `origin` is the top-left corner in Web Mercator metres; `pixel(column,
    row)` gives each pixel's colour.
    """
    rows = b"".join(
        bytes(channel for column in range(size) for channel in pixel(column, row))
        for row in range(size)
    )
    tile = zlib.compress(rows)
    # (tag, type, values): SHORT 3, LONG 4, DOUBLE 12. Sorted by tag.
    geokeys = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 3857)
    entries: list[tuple[int, int, tuple[float, ...]]] = [
        (256, 4, (size,)),
        (257, 4, (size,)),
        (258, 3, (8, 8, 8)),
        (259, 3, (8,)),  # Adobe DEFLATE
        (262, 3, (2,)),  # RGB
        (277, 3, (3,)),
        (284, 3, (1,)),  # chunky
        (322, 4, (size,)),
        (323, 4, (size,)),
        (324, 4, (0,)),  # tile offset, patched below
        (325, 4, (len(tile),)),
        (339, 3, (1, 1, 1)),  # unsigned
        (33550, 12, (pixel_size, pixel_size, 0.0)),
        (33922, 12, (0.0, 0.0, 0.0, origin[0], origin[1], 0.0)),
        (34735, 3, geokeys),
    ]
    formats = {3: "H", 4: "I", 12: "d"}
    ifd_size = 2 + 12 * len(entries) + 4
    extra_at = 8 + ifd_size
    extra = b""
    packed_values = []
    for _tag, kind, values in entries:
        data = struct.pack(f"<{len(values)}{formats[kind]}", *values)
        if len(data) <= 4:
            packed_values.append(data.ljust(4, b"\0"))
        else:
            packed_values.append(struct.pack("<I", extra_at + len(extra)))
            extra += data
    tile_at = extra_at + len(extra)
    ifd = struct.pack("<H", len(entries))
    for (tag, kind, values), packed in zip(entries, packed_values, strict=True):
        if tag == 324:
            packed = struct.pack("<I", tile_at)
        ifd += struct.pack("<HHI", tag, kind, len(values)) + packed
    ifd += struct.pack("<I", 0)
    return b"II*\0" + struct.pack("<I", 8) + ifd + extra + tile
