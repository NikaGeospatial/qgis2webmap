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

# Below this, compressing the data costs readability and saves little. Above it,
# the file is heading towards being awkward to email and the trade flips.
DATA_COMPRESSION_THRESHOLD_BYTES = 2 * 1024 * 1024

# Script type for a compressed data block. Deliberately not `application/json`:
# nothing must be able to mistake base64 for JSON if load order ever changes.
GZIP_SCRIPT_TYPE = "application/x-om-gzip"

GZIP_LEVEL = 9


def gzip_base64(data: bytes) -> str:
    """Compress and base64-encode. Base64 costs 33%; gzip saves far more.

    `mtime=0` is load-bearing, not tidiness. The gzip header carries a modified
    time, and `gzip.compress` defaults it to *now* - so the same project
    exported twice produced two different files, differing only in four bytes
    buried inside a base64 blob. That defeats the deterministic-artifact promise
    in `ArtifactResult`, makes a diff between two exports unreadable, and leaks
    the export time into a file whose whole point is that it carries no
    incidental data about its author.
    """
    return base64.b64encode(gzip.compress(data, GZIP_LEVEL, mtime=0)).decode("ascii")


def compression_ratio(original: bytes) -> float:
    """How much smaller the embedded form is, base64 overhead included."""
    if not original:
        return 1.0
    return len(gzip_base64(original)) / len(original)


def should_compress_data(total_data_bytes: int) -> bool:
    return total_data_bytes >= DATA_COMPRESSION_THRESHOLD_BYTES


# The bootstrap. Order matters and is the whole trick: data blocks are converted
# to real JSON *before* the runtime module is imported, because importing it
# defines the custom elements, which immediately upgrades every <om-layer>
# already in the document and reads its inline data.
BOOTSTRAP_TEMPLATE = """
      const inflate = async (text) => {{
        const bytes = Uint8Array.from(atob(text.trim()), (c) => c.charCodeAt(0));
        const stream = new Blob([bytes])
          .stream()
          .pipeThrough(new DecompressionStream("gzip"));
        return await new Response(stream).text();
      }};

      // Layer data first: replacing it after the runtime loads would be too late.
      for (const block of document.querySelectorAll(
        'script[type="{gzip_type}"]'
      )) {{
        const json = document.createElement("script");
        json.type = "application/json";
        json.textContent = await inflate(block.textContent);
        block.replaceWith(json);
      }}

      // Then the runtime, which defines <om-map> and friends.
      const runtimeSource = await inflate(RUNTIME_GZ);
      const runtimeUrl = URL.createObjectURL(
        new Blob([runtimeSource], {{ type: "text/javascript" }})
      );
      await import(runtimeUrl);
      URL.revokeObjectURL(runtimeUrl);
"""


def build_bootstrap(runtime_base64: str) -> str:
    """The module body that inflates the payloads and starts the map."""
    body = BOOTSTRAP_TEMPLATE.format(gzip_type=GZIP_SCRIPT_TYPE)
    return f'      const RUNTIME_GZ = "{runtime_base64}";\n{body}'


def plain_runtime_script(runtime_source: str) -> str:
    """The uncompressed alternative.

    Kept as a real option, not dead code: if the bootstrap ever proves unreliable
    on a browser we must support, this is the fallback, and it still produces a
    working single file - just a larger one.
    """
    return runtime_source
