"""Rasters inside a single HTML file, read the way the runtime reads a COG.

The runtime's `COGLayer` reads its file in pieces: a `Range: bytes=N-M` request
through `fetch` for the header, then one per tile. A single file has nowhere to
put a sibling to fetch, a `data:` URI answers every range with the whole body,
and a page opened by double-click cannot fetch from disk at all. So the page
carries each converted raster as base64 and answers those requests itself:

* **A `fetch` stand-in** serves a request whose last path segment names an
  embedded raster - the same name `RasterSpec.src` carries - from the bytes in
  the page, with a 206 and a `Content-Range` for a range, exactly as a server
  would. Every other request goes to the real `fetch` untouched.
* **Workers from `data:` URLs.** A page opened from disk has an opaque origin,
  and Chrome will not start a module worker from that origin's `blob:` URLs,
  which is how the runtime starts the workers that decode raster tiles. The
  decode failed with no message and the raster drew nothing. Under `file:`
  only, each such worker starts from a `data:` URL holding the same source.

Both run before the runtime is imported, which is why this is a template slot
of its own ahead of the runtime script rather than part of it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from html import escape

# The data blocks' type. Unknown to the browser, so it neither runs nor renders
# them; the stand-in finds them by it and removes them once read.
RASTER_SCRIPT_TYPE = "application/x-om-raster"

_STAND_IN = """      <script>
(() => {
  const files = new Map();
  for (const el of document.querySelectorAll('script[type="%(type)s"]')) {
    const text = atob(el.textContent.trim());
    const bytes = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
    files.set(el.dataset.src, bytes);
    el.remove();
  }
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url =
      typeof input === "string" ? input : (input && input.url) || String(input);
    const name = decodeURIComponent(url.split(/[?#]/)[0].split("/").pop());
    const bytes = files.get(name);
    if (!bytes) return realFetch(input, init);
    const headers = new Headers(
      (init && init.headers) || (input instanceof Request ? input.headers : undefined)
    );
    const range = /bytes=(\\d*)-(\\d*)/.exec(headers.get("Range") || "");
    const size = bytes.length;
    let start = 0, end = size - 1, status = 200;
    if (range && (range[1] || range[2])) {
      if (range[1]) {
        start = +range[1];
        end = range[2] ? Math.min(+range[2], size - 1) : size - 1;
      } else {
        start = Math.max(size - +range[2], 0);
      }
      status = 206;
    }
    const body = bytes.slice(start, end + 1);
    return Promise.resolve(new Response(body, { status, headers: {
      "Content-Type": "image/tiff",
      "Content-Length": String(body.length),
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Accept-Ranges": "bytes",
    } }));
  };
  if (location.protocol === "file:") {
    const RealWorker = window.Worker;
    window.Worker = function (url, options) {
      if (String(url).startsWith("blob:")) {
        const request = new XMLHttpRequest();
        request.open("GET", String(url), false);
        request.send();
        url = "data:text/javascript;charset=utf-8,"
          + encodeURIComponent(request.responseText);
      }
      return new RealWorker(url, options);
    };
    window.Worker.prototype = RealWorker.prototype;
  }
})();
      </script>"""


def build_raster_embed(embedded: Sequence[tuple[str, bytes]]) -> str:
    """The page markup that carries `embedded` rasters; empty when there are none.

    `embedded` is `(src, bytes)` pairs: the name each `COGLayer` asks for and
    the converted file it should get.
    """
    if not embedded:
        return ""
    blocks = [
        f'      <script type="{RASTER_SCRIPT_TYPE}" data-src="{escape(name)}">'
        f"{base64.b64encode(data).decode('ascii')}</script>"
        for name, data in embedded
    ]
    return "\n".join([*blocks, _STAND_IN % {"type": RASTER_SCRIPT_TYPE}])
