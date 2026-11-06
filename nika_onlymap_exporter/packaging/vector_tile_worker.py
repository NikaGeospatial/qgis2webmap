"""The vector tile decoder worker, carried inside every page that needs it.

The runtime's `MVTLayer` - a PMTiles archive, or a tile service drawn in its
publisher's style - decodes its tiles in a worker that loaders.gl starts from
`https://unpkg.com/@loaders.gl/mvt@<version>/dist/mvt-worker.js`, unless the
layer's load options name another (`mvt.workerUrl`). There is no main-thread
decoder to fall back on, so a map that carried its archive inside it still drew
nothing without the internet, and every vector tile map told unpkg.com it had
been opened. The worker comes from the runtime cache instead (`runtime_manager.
MVT_WORKER`), and the page carries it:

* **Once per page.** One base64 block, and a script that points every
  `MVTLayer`'s `load-options` at it, rather than the same 130 KB written into
  each layer's attribute.
* **As a `data:` URL.** A page opened from disk has an opaque origin, and Chrome
  will not start a worker from that origin's `blob:` URLs (see `raster_embed`);
  a `data:` URL starts in every browser, from a file and over http alike.
  loaders.gl uses a URL that does not start with "http" as it is.
* **Before the runtime is imported**, for the reason the data inflater runs
  first: importing it defines the custom elements, which upgrades every
  `<om-layer>` and reads its options. A cartograph's sheet waits in a
  `<template>` until its loader runs, so the script looks inside those too.

A value someone wrote into `load-options` by hand is left as it is.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..core.manifest_builder import VECTOR_TILE_LAYER_CLASS
from .runtime_manager import MVT_WORKER, read_lock

# Where a page that does not carry the worker has the runtime start it from.
DECODER_FALLBACK_HOST = "unpkg.com"

# The data block's type. Unknown to the browser, so it neither runs nor renders
# it; the script finds it by it and removes it once read.
WORKER_SCRIPT_TYPE = "application/x-om-worker"

# The licences the worker's code is under, shipped with the plugin. A copy of
# the worker travels in every page that carries it, so its notices do too.
NOTICE_PATH = (
    Path(__file__).resolve().parent.parent / "licenses" / "mvt-worker-NOTICE.txt"
)

_POINT_LAYERS = """      <script>
(() => {
  const block = document.querySelector('script[type="%(type)s"]');
  if (!block) return;
  const url = "data:text/javascript;base64," + block.textContent.trim();
  block.remove();
  const roots = [
    document,
    ...Array.from(document.querySelectorAll("template"), (t) => t.content),
  ];
  for (const root of roots) {
    for (const layer of root.querySelectorAll('om-layer[type="%(layer)s"]')) {
      let options;
      try {
        options = JSON.parse(layer.getAttribute("load-options") || "{}");
      } catch (error) {
        continue;
      }
      if (options.mvt && options.mvt.workerUrl) continue;
      options.mvt = Object.assign({}, options.mvt, { workerUrl: url });
      layer.setAttribute("load-options", JSON.stringify(options));
    }
  }
})();
      </script>"""


def has_vector_tile_layer(markup: str) -> bool:
    """Whether `markup` (a manifest, or a sheet of frames) has an `MVTLayer`.

    Attribute values are escaped, so no layer name can spell the attribute.
    """
    return f'type="{VECTOR_TILE_LAYER_CLASS}"' in markup


def _source_line() -> str:
    """Which package and version the carried worker is, from the lock."""
    entry = (read_lock().get("workers") or {}).get(MVT_WORKER) or {}
    package = entry.get("package") or "@loaders.gl/mvt"
    version = entry.get("version")
    return f"{MVT_WORKER} from {package}" + (f" {version}" if version else "")


def build_worker_embed(markup: str, worker: bytes | None) -> str:
    """The page markup that carries the vector tile decoder worker; empty when
    `markup` has no vector tile layer, or there is no worker to carry.

    The licence notice goes in as an HTML comment beside the block. The notice
    file is the plugin's own, and `-->` would end the comment early, so it is
    refused rather than escaped into something that is no longer the notice.
    """
    if worker is None or not has_vector_tile_layer(markup):
        return ""
    notice = NOTICE_PATH.read_text(encoding="utf-8").rstrip()
    if "-->" in notice or "--!>" in notice:  # pragma: no cover - our own file
        raise ValueError(f"{NOTICE_PATH.name} cannot go inside an HTML comment")
    return "\n".join(
        [
            f"      <!--\n{_source_line()}, carried so vector tiles draw "
            f"without the internet.\n\n{notice}\n      -->",
            f'      <script type="{WORKER_SCRIPT_TYPE}">'
            f"{base64.b64encode(worker).decode('ascii')}</script>",
            _POINT_LAYERS
            % {"type": WORKER_SCRIPT_TYPE, "layer": VECTOR_TILE_LAYER_CLASS},
        ]
    )
