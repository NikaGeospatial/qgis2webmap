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
* **EPSG:3857 without the network.** The runtime's `COGLayer` asks
  `https://epsg.io/<code>.json` for its file's CRS, so a page with no network
  drew no raster. Every COG written here is in Web Mercator, and the stand-in
  answers that one request from the definition the page carries
  (`web_mercator_projjson`); other codes still go to the network.
* **What was read, and what failed.** The stand-in notes each raster it
  serves and the time it last served one, and keeps any raster error the
  runtime leaves unhandled, in `window.qgis2webmapRasters`. The runtime's own
  readiness counts a raster layer as drawn before it has even read its file,
  so the cartograph's print and PNG export wait on this instead
  (`cartograph_writer._RASTER_GATE`).
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

from .raster_cog import WEB_MERCATOR_EPSG
from .web_mercator_projjson import WEB_MERCATOR_PROJJSON

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
  const crsDefinitions = { "%(epsg)s": %(projjson)s };
  const tracker = {
    names: [...files.keys()], read: new Set(), lastRead: 0, failed: [],
  };
  window.qgis2webmapRasters = tracker;
  window.addEventListener("unhandledrejection", (event) => {
    const message = String((event.reason && event.reason.message) || event.reason);
    if (/\\b(tiff|cog|projjson|projection|crs)\\b/i.test(message)) {
      tracker.failed.push(message);
    }
  });
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url =
      typeof input === "string" ? input : (input && input.url) || String(input);
    const crs = /^https:\\/\\/epsg\\.io\\/(\\d+)\\.json$/.exec(url);
    if (crs && crsDefinitions[crs[1]]) {
      return Promise.resolve(new Response(JSON.stringify(crsDefinitions[crs[1]]), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    }
    let name;
    try {
      name = decodeURIComponent(url.split(/[?#]/)[0].split("/").pop());
    } catch (error) {
      return realFetch(input, init);
    }
    const bytes = files.get(name);
    if (!bytes) return realFetch(input, init);
    tracker.read.add(name);
    tracker.lastRead = performance.now();
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
      if (start >= size || start > end) {
        return Promise.resolve(new Response(null, { status: 416, headers: {
          "Content-Range": `bytes */${size}`,
        } }));
      }
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
    stand_in = _STAND_IN % {
        "type": RASTER_SCRIPT_TYPE,
        "epsg": WEB_MERCATOR_EPSG,
        # JSON is a JavaScript expression; `<` escaped so no text in it can
        # close the script element.
        "projjson": WEB_MERCATOR_PROJJSON.replace("<", "\\u003c"),
    }
    return "\n".join([*blocks, stand_in])
