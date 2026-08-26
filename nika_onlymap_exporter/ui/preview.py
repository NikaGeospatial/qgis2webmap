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
from .links import FEATURE_REQUEST_URL
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


# Preview only, and the reason it is a constant rather than a template edit: the
# hook slot is shared. `artifact_builder` passes `terrain_zoom_clamp()` through
# the same parameter on the real export path, so "it is in the preview hook" is
# not by itself what keeps this out of a shipped map. What keeps it out is that
# it is composed here, in the preview module, and nowhere else.
#
# Bottom right, stacked directly above the credit chip, so the two read as one
# group of plugin chrome rather than something dropped in the middle of the map.
#
# That corner is already occupied twice over: the chip itself at `bottom: 12px`,
# and the runtime's `bottom-end` widget slot (provider attribution), which the
# template already lifts above the chip with hard-coded 36px/54px offsets. So
# this has to join that stack rather than ignore it - the offsets below continue
# the same arithmetic, and the slot is pushed up again by exactly this chip's
# height while it exists. `:has()` does that, which also means dismissing the
# chip drops the attribution back on its own with no script involved.
_HOST_CTA_TEMPLATE = """
    <style>
      /* Deliberately unlike the map's own chrome. This is plugin UI shown while
         you are looking at your map; it must never read as something the person
         you send the file to will see. */
      .om-preview-cta {
        position: fixed;
        right: 12px;
        /* 12px inset + the credit chip's height + the runtime's own widget gap.
           Same arithmetic as the template's attribution offsets, and the same
           caveat: measured at 12px/1.5, approximate on purpose. */
        bottom: calc(12px + 36px + 8px);
        /* Above the caption's 10000 and the runtime widgets' 9999: an
           affordance you cannot reach to dismiss is worse than no affordance. */
        z-index: 2147483000;
        display: flex;
        align-items: center;
        gap: 10px;
        height: 34px;
        box-sizing: border-box;
        max-width: min(30rem, calc(100% - 24px));
        padding: 0 8px 0 14px;
        border: 1px solid #d4d4d8;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.97);
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
        /* System stack, never a webfont. A remote font URL here would be the
           one thing in this block that puts a network dependency into a
           preview, and it would read as an offline-promise regression. */
        font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        color: #18181b;
      }
      .om-preview-cta-label {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .om-preview-cta a {
        flex: none;
        padding: 4px 12px;
        border-radius: 999px;
        background: #18181b;
        color: #fff;
        text-decoration: none;
        font-weight: 600;
      }
      .om-preview-cta a:hover,
      .om-preview-cta a:focus-visible {
        background: #3f3f46;
      }
      .om-preview-cta-close {
        flex: none;
        width: 22px;
        height: 22px;
        padding: 0;
        border: 0;
        border-radius: 50%;
        background: transparent;
        color: #71717a;
        font-size: 16px;
        line-height: 1;
        cursor: pointer;
      }
      .om-preview-cta-close:hover,
      .om-preview-cta-close:focus-visible {
        background: #f4f4f5;
        color: #18181b;
      }
      /* A data credit gives the chip a second line, which the template already
         accounts for at 54px. Follow it up. */
      body:has(.om-credit-data) .om-preview-cta {
        bottom: calc(12px + 54px + 8px);
      }
      /* Now get out of the attribution's way. The template lifts the
         `bottom-end` slot to clear the chip; while this chip exists it has to
         clear both, so add this one's height and the same 8px gap. Written as
         `:has()` rather than set from the dismiss handler so removing the chip
         puts the attribution back by itself. */
      body:has(.om-preview-cta) [data-om-widget-slot="bottom-end"] {
        bottom: calc(12px + 36px + 8px + 34px + 8px) !important;
      }
      body:has(.om-preview-cta):has(.om-credit-data)
        [data-om-widget-slot="bottom-end"] {
        bottom: calc(12px + 54px + 8px + 34px + 8px) !important;
      }
      /* A caption pinned to this corner would land on top of the stack. Push
         above it rather than fight for the space - the caption is the author's
         content and this is a temporary affordance. */
      body:has(.om-caption-bottom-right) .om-preview-cta {
        bottom: calc(12px + 36px + 8px + 72px);
      }
      @media (max-width: 420px) {
        .om-preview-cta-label {
          display: none;
        }
      }
    </style>
    <div class="om-preview-cta" role="complementary" aria-label="Preview only">
      <span class="om-preview-cta-label">Want this map online?</span>
      <a href="@FORM_URL@" target="_blank" rel="noopener noreferrer">Host</a>
      <button
        type="button"
        class="om-preview-cta-close"
        aria-label="Dismiss">&times;</button>
    </div>
    <script>
      // Preview only: the "Host" call to action.
      //
      // Hosting does not exist yet, so this opens the feature-request form and
      // says so. It is here rather than in the export because it is a question
      // for the author, not something to ship to whoever they send the map to.
      (function () {
        var cta = document.querySelector(".om-preview-cta");
        if (!cta) return;
        var KEY = "qgis2webmap.hostcta";
        // Storage, not location.hash: CAMERA_SCRIPT owns the fragment and would
        // overwrite this on the first pan. Wrapped because Chrome treats a
        // file:// document as an opaque origin for storage, which is the same
        // reason the camera script carries its own try/catch.
        try {
          if (localStorage.getItem(KEY) === "off") {
            cta.remove();
            return;
          }
        } catch (e) { /* storage unavailable; the chip stays for this view */ }
        cta.querySelector(".om-preview-cta-close").addEventListener(
          "click",
          function () {
            cta.remove();
            try {
              localStorage.setItem(KEY, "off");
            } catch (e) { /* dismissal lasts this page view only */ }
          }
        );
      })();
    </script>
"""

HOST_CTA = _HOST_CTA_TEMPLATE.replace("@FORM_URL@", FEATURE_REQUEST_URL)


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


def compose_preview_hook(
    project: ExportProject, live: bool = False, final: bool = False
) -> str:
    """Everything injected at the template's `@PREVIEW_HOOK@`, for a preview.

    Pulled out of `write_preview` so it can be read without a writer, a runtime
    bundle or a QGIS application - which is what makes the composition testable
    at the unit tier rather than only through a rendered artifact.
    """
    # The relief camera clamp ships in the real artifact (artifact_builder);
    # without it here a relief preview would zoom past where the terrain
    # blanks, behaving unlike the file `final` claims to match byte-for-byte.
    hook = terrain_zoom_clamp(project) + CAMERA_SCRIPT
    if live:
        hook += RELOAD_SCRIPT
    # `final` promises byte-for-byte what ships, so the call to action stays
    # out of it. (`CAMERA_SCRIPT` already bends that promise and no caller
    # passes `final=True` today; not this change's problem to fix.)
    if not final:
        hook += HOST_CTA
    return hook


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

    # Passed through the template's own hook rather than string-matching the
    # rendered output. The runtime contains a literal "</body>" inside a template
    # literal, so a naive replace would paste this script into the middle of the
    # minified library and break the map.
    return writer.write(
        project,
        destination,
        mode=OutputMode.STANDALONE_HTML,
        compress=final,
        preview_hook=compose_preview_hook(project, live=live, final=final),
    )
