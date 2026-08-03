"""Preview: the production writer, a stable path, and the system browser.

Three decisions worth stating, all reacting to something measured in the
incumbent.

**The system browser, not an embedded one.** qgis2web embeds a Chromium via
PyQt WebEngine, and the whole dependency chain is cost with no benefit: the
package is absent on some platforms, its remediation button shells out to
`apt-get` and then calls `os._exit(0)` - closing QGIS with no explanation - and
even when present it forces `--disable-gpu` alongside `--disable-software-
rasterizer`, leaving nothing able to draw. Our runtime is WebGL, which is the
worst case for an embedded Chromium. The external browser is also the *real*
target environment, so it is the more faithful preview.

**A stable path per project.** qgis2web stamps a new timestamped directory on
every write, so the URL changes each time: the browser's reload button is
useless, the camera resets, and `/tmp` grows without bound. One path per project
means reload works and temp files are reused.

**`file://`, not a localhost server.** Previewing over `http://` would test a
different origin with different rules than the artifact the recipient opens -
`fetch` of siblings works there and not from a file, so a bug could sail through
preview and land on the recipient. The missing live-reload is the price of
testing what actually ships.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from ..core.export_ir import ExportProject, OutputMode
from ..writers.onlymap_writer import ArtifactResult, OnlyMapWriter
from .live_server import RELOAD_PATH

PREVIEW_DIR_NAME = "qgis2webmap-preview"

# Injected into preview artifacts only, never into an export. Without it every
# reload throws away the camera and the user re-navigates from scratch, which is
# what makes "just export again" feel expensive.
CAMERA_SCRIPT = """
    <script type="module">
      // Preview only: remember the camera across reloads.
      //
      // Storage on file:// is inconsistent - Chrome treats file documents as
      // opaque origins for some APIs - so the URL fragment is the fallback. It
      // also survives a hard reload, which storage does not always.
      const map = document.querySelector("om-map");
      const KEY = "qgis2webmap.camera";

      const read = () => {
        if (location.hash.startsWith("#camera=")) {
          const [lng, lat, zoom] = location.hash.slice(8).split(",").map(Number);
          if ([lng, lat, zoom].every(Number.isFinite)) return { lng, lat, zoom };
        }
        try {
          const stored = sessionStorage.getItem(KEY);
          if (stored) return JSON.parse(stored);
        } catch (e) { /* storage unavailable on this origin */ }
        return null;
      };

      const write = (c) => {
        const encoded = `${c.lng.toFixed(6)},${c.lat.toFixed(6)},${c.zoom.toFixed(2)}`;
        history.replaceState(null, "", "#camera=" + encoded);
        try {
          sessionStorage.setItem(KEY, JSON.stringify(c));
        } catch (e) { /* storage unavailable */ }
      };

      map.addEventListener("om-view-changed", (e) => {
        const d = e.detail;
        write({ lng: d.longitude, lat: d.latitude, zoom: d.zoom });
      });

      await map.ready;
      const saved = read();
      if (saved) {
        map.setAttribute("center", `[${saved.lng}, ${saved.lat}]`);
        map.setAttribute("zoom", String(saved.zoom));
      }
    </script>
"""


RELOAD_SCRIPT = f"""
    <script>
      // Preview only: reload when the plugin rebuilds the artifact.
      //
      // Opened from a file:// path this does nothing at all, deliberately. The
      // preview file is a real artifact and someone will eventually
      // double-click it; a dead EventSource retrying against a port that was
      // never there would be pure noise.
      (function () {{
        if (location.protocol === "file:") return;
        var attempts = 0;
        function connect() {{
          var source = new EventSource("{RELOAD_PATH}");
          source.onmessage = function (event) {{
            if (event.data === "reload") location.reload();
          }};
          source.onerror = function () {{
            // The dialog closed, or the server went away. Retry briefly and
            // then give up: a tab left open for hours must not sit in an
            // endless reconnect loop against a dead port.
            source.close();
            attempts += 1;
            if (attempts <= 5) setTimeout(connect, 1000);
          }};
        }}
        connect();
      }})();
    </script>
"""


def preview_directory(project_identity: str) -> Path:
    """A stable directory for this project's preview.

    Keyed by a hash of the project's identity so two open projects do not
    overwrite each other's preview, while the same project always reuses one
    path - which is what makes the browser's reload button work.
    """
    digest = hashlib.sha256(project_identity.encode("utf-8")).hexdigest()[:12]
    path = Path(tempfile.gettempdir()) / PREVIEW_DIR_NAME / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_preview(
    project: ExportProject,
    project_identity: str,
    writer: OnlyMapWriter | None = None,
    final: bool = False,
    live: bool = False,
) -> ArtifactResult:
    """Build a preview artifact through the production writer.

    `final` runs the real single-file packaging, so what you see is byte-for-byte
    what ships. The default skips compression, which makes each preview write
    quick while iterating; only the final mode may claim to show what a recipient
    receives.

    `live` adds the reload listener, for previews served by `PreviewServer`. It
    is off by default so that a preview written for any other reason carries
    nothing that expects a server to exist.
    """
    writer = writer or OnlyMapWriter()
    destination = preview_directory(project_identity)
    hook = CAMERA_SCRIPT + RELOAD_SCRIPT if live else CAMERA_SCRIPT

    # Passed through the template's own hook rather than string-matching the
    # rendered output. The runtime contains a literal "</body>" inside a template
    # literal, so a naive replace would paste this script into the middle of the
    # minified library and break the map.
    return writer.write(
        project,
        destination,
        mode=OutputMode.STANDALONE_HTML,
        compress=final,
        preview_hook=hook,
    )
