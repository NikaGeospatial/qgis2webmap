"""Preview: the production writer, a stable path, and the user's own browser.

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

This was measured rather than assumed. On QGIS 4.0.3 / Qt 6.11.1, a
`QWebEngineView` constructs but **can create no WebGL context at all**
(`GL_VENDOR = Disabled`, `BindToCurrentSequence failed`), with
`AA_ShareOpenGLContexts` already set and `--enable-unsafe-swiftshader` making no
difference. An embedded pane would be blank. Separately, `pyqt6-webengine` is not
in the QGIS closure, so most users would have no view to put in it.

**A stable path per project.** qgis2web stamps a new timestamped directory on
every write, so the URL changes each time: the browser's reload button is
useless, the camera resets, and `/tmp` grows without bound. One path per project
means reload works and temp files are reused.

**Served from localhost while you work; a file once you export.** `file://`
leaves the plugin no way to reach the page - Chrome treats file documents as
opaque origins, which `CAMERA_SCRIPT` below already ran into with
`sessionStorage` - so a live preview has to be served. See `live_server.py`.

The origin does differ from the `file://` an artifact is usually opened from, and
that gap is closed deliberately rather than ignored: the *exported* map is opened
over `file://` from the dialog's **Open exported map**, so the shipping bytes are
what get checked on the shipping origin. Testing a preview copy would have been
the weaker check. Live preview is the working loop, not the final word.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from ..core.export_ir import ExportProject, OutputMode
from ..packaging.artifact_builder import terrain_zoom_clamp
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
    path = _preview_path(project_identity)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _preview_path(project_identity: str) -> Path:
    """Where this project's preview lives, whether or not it exists yet."""
    digest = hashlib.sha256(project_identity.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / PREVIEW_DIR_NAME / digest


def remove_preview(project_identity: str) -> None:
    """Delete this project's preview directory.

    Called when the dialog closes: the preview only exists to be served by the
    dialog's own localhost server, so once that stops the files are dead weight.
    Left behind they accumulate forever on Windows, where nothing ever clears
    the temp directory - a few megabytes per project, per machine, for years.
    Errors are swallowed: a locked file is not worth a crash on close.
    """
    import shutil

    shutil.rmtree(_preview_path(project_identity), ignore_errors=True)


def prune_stale_previews(max_age_days: float = 7.0) -> None:
    """Sweep preview directories older than `max_age_days`.

    The close-time removal above misses previews whose dialog never closed
    cleanly - a QGIS crash, a killed process - and previews of projects that
    were renamed (a new identity means a new digest, orphaning the old one).
    This sweep, run at dialog shutdown, bounds how long any of those survive.
    Only paths under our own preview directory are ever touched.
    """
    import shutil
    import time

    root = Path(tempfile.gettempdir()) / PREVIEW_DIR_NAME
    if not root.is_dir():
        return
    cutoff = time.time() - max_age_days * 86400.0
    for entry in root.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


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
    # The relief camera clamp ships in the real artifact (artifact_builder);
    # without it here a relief preview would zoom past where the terrain
    # blanks, behaving unlike the file `final` claims to match byte-for-byte.
    hook = terrain_zoom_clamp(project) + hook

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
