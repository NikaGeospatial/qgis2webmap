"""Compressing what goes inside a single-file artifact.

**Lossless only.** Gzip shrinks the bytes without touching the data, so it is
applied unconditionally where it helps. Coordinate quantisation would shrink the
file further but discards precision permanently, so it stays an explicit opt-in -
see the readiness assessment. Measured on the Alaska project: gzip alone takes a
naive 9.8 MB artifact to 3.7 MB.

**The stylesheet is never compressed.** `<om-fallback>` is gated by a pure-CSS
rule (`om-map:not(:defined)`), which is what makes the no-JavaScript message
appear in mail previews and iOS QuickLook. If the CSS only arrived after a script
inflated it, the fallback would break in exactly the environments it exists for.
72 KB raw is the price and it is worth paying.

**Data compression is size-driven, not always-on.** The manifest and its data are
the part a person or an AI assistant reads and edits; turning a readable GeoJSON
block into a base64 blob costs that for no benefit on a small map. So data is
compressed only when the artifact is large enough for it to matter.

Verified: `DecompressionStream` + a blob URL + dynamic `import()` all work from
`file://` in Chromium. Firefox is checked as part of release verification.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import threading
from collections import OrderedDict

# Below this, compressing the data costs readability and saves little. Above it,
# the file is heading towards being awkward to email and the trade flips.
DATA_COMPRESSION_THRESHOLD_BYTES = 2 * 1024 * 1024

# Script type for a compressed data block. Deliberately not `application/json`:
# nothing must be able to mistake base64 for JSON if load order ever changes.
GZIP_SCRIPT_TYPE = "application/x-om-gzip"

# Level 6, not 9: on a large test project 9 took 2.24 s for 6.12 MB of data blocks
# and 6 took 0.77 s for 6.24 MB (+2%), so 9 cost three times the time for 2%.
GZIP_LEVEL = 6


class _CompressionCache:
    """Small byte-bounded LRU of finished `gzip_base64` results.

    Compression is a pure function of the input bytes and the level, so the
    result is keyed by a sha256 of the bytes plus the level and is safe to
    reuse: the same unchanged runtime assets are compressed on every export in
    a session, which took ~1.3 s of a ~1.5 s small build. Bounded by the size
    of the stored text, not the entry count, so a large layer payload cannot
    pin memory; one result bigger than the whole budget is simply not kept.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple[bytes, int, int], str] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: tuple[bytes, int, int]) -> str | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[bytes, int, int], value: str) -> None:
        if len(value) > self._max_bytes:
            return
        with self._lock:
            if key in self._entries:
                return
            self._entries[key] = value
            self._size += len(value)
            while self._size > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._size -= len(evicted)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0


# Two runtimes (map + Cartograph) plus a few recent layer payloads fit; the
# base64 text is ASCII, so len() is its size in bytes.
_GZIP_CACHE = _CompressionCache(max_bytes=32 * 1024 * 1024)


# How much of a file `gzip_probe` compresses. Enough to pass the deflate window
# and block-splitting points many times over, small enough to cost milliseconds.
GZIP_PROBE_BYTES = 256 * 1024


def gzip_bytes(data: bytes) -> bytes:
    """The gzip stream `gzip_base64` embeds, before base64.

    `mtime=0` is load-bearing, not tidiness. The gzip header carries a modified
    time, and `gzip.compress` defaults it to *now* - so the same project
    exported twice produced two different files, differing only in four bytes
    buried inside a base64 blob. That defeats the deterministic-artifact promise
    in `ArtifactResult`, makes a diff between two exports unreadable, and leaks
    the export time into a file whose whole point is that it carries no
    incidental data about its author.

    The runtime cache stores its files with this same function, so a stored
    file can stand in for compressing the runtime again (`adopt_stored_gzip`).
    """
    return gzip.compress(data, GZIP_LEVEL, mtime=0)


def gzip_probe(data: bytes) -> str:
    """A digest of how this interpreter's `gzip_bytes` compresses `data`'s start.

    Deflate output is not fixed by the format: the same input at the same level
    comes out differently from another zlib release or build (zlib-ng, a SIMD
    fork), and Python versions disagree on one header byte. Recorded when a file
    is stored and compared before the stored stream is reused, so an embedded
    runtime is the bytes this machine would have produced itself.
    """
    return hashlib.sha256(gzip_bytes(data[:GZIP_PROBE_BYTES])).hexdigest()


def _cache_key(data: bytes) -> tuple[bytes, int, int]:
    return (hashlib.sha256(data).digest(), len(data), GZIP_LEVEL)


def gzip_base64(data: bytes) -> str:
    """Compress and base64-encode. Base64 costs 33%; gzip saves far more.

    See `gzip_bytes` for why the output is deterministic.
    """
    key = _cache_key(data)
    cached = _GZIP_CACHE.get(key)
    if cached is not None:
        return cached
    encoded = base64.b64encode(gzip_bytes(data)).decode("ascii")
    _GZIP_CACHE.put(key, encoded)
    return encoded


def adopt_stored_gzip(data: bytes, stored: bytes, probe: str) -> bool:
    """Let `gzip_base64(data)` reuse `stored`, a `gzip_bytes(data)` kept on disk.

    The caller vouches that `stored` decompresses to `data` (the runtime loader
    has just decompressed it). Adopted only when `probe` - recorded beside the
    file when it was written - matches this interpreter's own `gzip_probe`, so a
    cache written by another zlib or Python cannot change the exported bytes;
    otherwise `gzip_base64` compresses as it always has. Compressing the 9 MB
    map runtime is about 0.4 s of a first single-file export.
    """
    key = _cache_key(data)
    if _GZIP_CACHE.get(key) is not None:
        return True
    if gzip_probe(data) != probe:
        return False
    _GZIP_CACHE.put(key, base64.b64encode(stored).decode("ascii"))
    return True


def should_compress_data(total_data_bytes: int) -> bool:
    return total_data_bytes >= DATA_COMPRESSION_THRESHOLD_BYTES


# Inflating the DATA blocks, and nothing else. Split out from the runtime
# bootstrap below because the two are needed in different combinations and were
# once welded together, with consequences: the folder tier writes the runtime as
# a sibling FILE, took the "runtime is a separate file" branch, and so emitted no
# bootstrap at all - while still gzipping its data. Every folder export with more
# than 2 MB of vector data therefore published `<om-layer>` elements whose only
# child was base64 the runtime had no idea how to read. No error, no warning: the
# map drew its rasters, its legend and its layer switcher, and not one feature.
# Found on 2026-09-18 against the Grand Canyon demo.
#
# Order is the whole trick either way: data blocks become real JSON *before* the
# runtime is imported, because importing it defines the custom elements, which
# immediately upgrades every <om-layer> in the document and reads its data.
DATA_INFLATE_TEMPLATE = """
      const inflate = async (text) => {{
        const bytes = Uint8Array.from(atob(text.trim()), (c) => c.charCodeAt(0));
        const stream = new Blob([bytes])
          .stream()
          .pipeThrough(new DecompressionStream("gzip"));
        return await new Response(stream).text();
      }};

      // Layer data first: replacing it after the runtime loads would be too late.
      // A live layer's embedded copy stays one the runtime does not read
      // (`manifest_builder.FALLBACK_SCRIPT_TYPE`), until the page needs it.
      for (const block of document.querySelectorAll(
        'script[type="{gzip_type}"]'
      )) {{
        const json = document.createElement("script");
        json.type = block.hasAttribute("data-q2w-fallback")
          ? "{fallback_type}"
          : "application/json";
        json.textContent = {inflated};
        block.replaceWith(json);
      }}
"""

# What a data block becomes; on a page carrying popup pictures inside, with
# their addresses put in by the function the page's picture script defines
# (`popup_pictures.build_picture_embed`).
_INFLATED = "await inflate(block.textContent)"
PICTURE_RESOLVER = "qgis2webmapPictures"


def _data_inflate(pictures: bool) -> str:
    inflated = f"{PICTURE_RESOLVER}({_INFLATED})" if pictures else _INFLATED
    from ..core.manifest_builder import FALLBACK_SCRIPT_TYPE

    return DATA_INFLATE_TEMPLATE.format(
        gzip_type=GZIP_SCRIPT_TYPE,
        inflated=inflated,
        fallback_type=FALLBACK_SCRIPT_TYPE,
    )


# Then the runtime, for the single-file tier that carries it gzipped inline.
RUNTIME_INFLATE_TEMPLATE = """
      const runtimeSource = await inflate(RUNTIME_GZ);
      const runtimeUrl = URL.createObjectURL(
        new Blob([runtimeSource], {{ type: "text/javascript" }})
      );
      await import(runtimeUrl);
      URL.revokeObjectURL(runtimeUrl);
"""


def build_data_inflater(runtime_import: str) -> str:
    """Inflate the data blocks, then load the runtime from a sibling file.

    For the tier that writes `onlymap.js` beside the page. `await import` rather
    than a bare one so the import cannot begin until inflation has finished:
    `DecompressionStream` is genuinely asynchronous, so a plain
    `import "./onlymap.js"` alongside it is a race the data loses.
    """
    body = _data_inflate(pictures=False)
    return f'{body}\n      await import("./{runtime_import}");\n'


def build_bootstrap(runtime_base64: str, pictures: bool = False) -> str:
    """The module body that inflates the payloads and starts the map.

    For the single-file tier, where the runtime travels gzipped in the page.
    `pictures` when the page carries popup pictures inside it, whose
    addresses go into the data as it is inflated.
    """
    body = _data_inflate(pictures)
    body += RUNTIME_INFLATE_TEMPLATE.format()
    return f'      const RUNTIME_GZ = "{runtime_base64}";\n{body}'
