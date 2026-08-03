# Live preview and dialog design

Date: 2026-08-03
Status: approved, not yet implemented

## Why

Two problems, one session's worth of feedback behind them.

The preview makes you leave the plugin. You change a highlight colour, click
Preview, switch windows, look, switch back. That loop is slow enough that people
stop checking, which is how a bad export ships.

The dialog itself works but does not guide. Its most valuable output - what your
recipient loses - sits in a tab you visit afterwards, if you remember to.

An embedded preview pane was the obvious answer and was **measured and rejected**;
see [Rejected: embedded preview](#rejected-embedded-preview).

## Section 1: live preview over localhost

### Architecture

New module `ui/live_server.py`.

- `http.server` on a daemon thread, bound to `127.0.0.1` on an ephemeral port
  (port `0`, kernel-assigned).
- Serves exactly one directory: the existing per-project preview directory from
  `preview_directory()`. No directory listing. Every request path is resolved and
  confined under the root, so `..` cannot escape it.
- Read-only. The server never writes; the plugin writes and the server serves.
- Owned by the dialog. Started lazily the first time a live preview is requested,
  shut down in `_shutdown()` alongside the layer watcher.

New preview hook script, sibling to `CAMERA_SCRIPT` in `ui/preview.py`, injected
through the same `@PREVIEW_HOOK@` template token. It opens an `EventSource`
against the reload endpoint and calls `location.reload()` on a message.

**Preview only. It must never appear in an export.** The existing test
`test_export_does_not_carry_the_preview_script` covers this shape already and
gets a sibling for the reload script.

### Data flow

```
setting changes
  -> dialog marks preview stale, starts/restarts a 400 ms debounce timer
  -> timer fires: write_preview(...) through the production writer
  -> server notifies connected SSE clients
  -> browser tab reloads; CAMERA_SCRIPT restores the camera
```

Rebuilds coalesce. If a rebuild is in flight when the timer fires, the next one
waits for it rather than queueing - a burst of colour-picker changes must not
stack up N full exports.

### Controls

| Control | Behaviour |
|---|---|
| `Live preview` (checkbox) | Opens or reuses the localhost tab and keeps it current |
| `Open exported map` | Enabled after a successful export; opens the real artifact over `file://` |

`Open exported map` replaces the old preview-time browser button. The `file://`
path is still exercised - by the artifact that actually ships, which is a better
check than a preview copy of it.

Nothing opens a browser on its own. The user asked for this explicitly.

### Preference

`QSettings` key `qgis2webmap/livePreview`, default on, stored per machine rather
than in the project file: it describes how someone likes to work, not what the map
is. When off, the server never starts and behaviour is exactly what it is today.

### Error handling

- Port bind fails, or the server thread dies: report it once, in the dialog, next
  to the checkbox; fall back to writing the preview file and saying where it is.
  Never raise into the dialog.
- Browser never connects: the preview file is still written, so the flow degrades
  to today's behaviour rather than to nothing.
- Export failure during a debounced rebuild is reported the same way an explicit
  preview failure is, and does not disable live preview.

### Testing

- Unit: the served root confines path traversal; the reload script is present in
  a preview and absent from an export; the debounce coalesces a burst into one
  rebuild.
- QGIS tier: the dialog starts and stops the server across its lifecycle, and
  leaves no thread behind after `_shutdown()`.
- Not covered by tests: that a real browser reloads. That is a manual check.

## Section 2: the dialog

### Constraint

This is a QGIS dialog. Heavy `QSS` skinning fights the user's Qt theme and
platform, and a plugin that looks alien beside the rest of QGIS reads as worse,
not bolder. No custom skin.

All secondary text derives from `QPalette` roles (`PlaceholderText`, `Disabled`)
rather than hardcoded hex, so it survives light and dark themes. The hardcoded
greys in the *exported* CSS are correct and stay; that reasoning must not leak
into the dialog.

### Thesis

This plugin's differentiator over qgis2web is telling you what your recipient
loses. Make that the spine of the dialog rather than a tab.

### Changes

1. **Output mode becomes radio buttons.** `How to share it` is three mutually
   exclusive options currently built from `QCheckBox` with manual unchecking
   (`ui/main_dialog.py`, `_build_map_tab`). Checkboxes promise multi-select and do
   not deliver it, and it reads wrong to a screen reader. A bug fix, not taste.

2. **Fidelity becomes ambient.** A persistent strip above the button row, visible
   from every tab: `14 layers - 3 things change on export`. Clicking it opens the
   existing detail view. `_fidelity_is_stale` already tracks recomputation. This
   is the one place to spend boldness.

3. **The Map tab's dead space becomes the export summary** - what you are about to
   produce, its tier, its expected size. Currently `addStretch(1)` and nothing.

4. **Spacing and hierarchy discipline.** One consistent form-row spacing and group
   margin. Field help becomes a small muted line rather than a tooltip only;
   several settings currently explain themselves only on hover, which is invisible
   to anyone not hunting for it.

### Not changing

The Help tab renders the shipped guides and works. Leave it.

## Rejected: embedded preview

A preview pane inside the plugin window was the starting proposal. Two probes on
QGIS 4.0.3 / Qt 6.11.1 / PyQt 6.11.0 killed it:

- `QWebEngineView` constructs, but **no WebGL context can be created**. The
  OnlyMap runtime is WebGL, so the pane would be blank.
- `AA_ShareOpenGLContexts` is set by QGIS, so that is not the cause.
- `--enable-unsafe-swiftshader`, `--ignore-gpu-blocklist` and
  `--enable-gpu-rasterization` changed nothing. Error:
  `GL_VENDOR = Disabled, GL_RENDERER = Disabled, BindToCurrentSequence failed`.
  Chromium's GPU stack is off before any flag we add is read.
- Independently: `pyqt6-webengine` **is not in the QGIS closure**. Users have it
  only if they installed it themselves, so most would have no view at all.

We never prompt to install it and never shell out to a package manager. qgis2web
does, then calls `os._exit(0)`, killing QGIS with no explanation.

## Out of scope

Two feedback items from 2026-07-31 are **not** addressed here:

- Hover popups lag as the cursor moves.
- The cursor does not change to indicate a feature is interrogable.

Both are runtime-side hit-testing. The runtime is a sha256-pinned npm tarball we
do not own, and the most we could do from outside is set a blanket cursor on the
map element, which signals nothing about *where* the features are. These need a
conversation with whoever owns OnlyMap, not a workaround here.
