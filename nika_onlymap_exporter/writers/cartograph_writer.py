"""`CartographSpec` -> a standalone `<om-cartograph>` HTML document.

This is the cartograph sibling of `writers/onlymap_writer.py`, built the same
way the Phase 0 spikes (`specs/cartograph/spikes/live-build.py`,
`static-build.py`) proved out, but as production code: every placed element
gets matching `x/y/w/h` attributes *and* an inline mm `style`, numbers are
formatted deterministically, and the 8 MB map runtime is only embedded when
the page actually needs it.

**Live frames are embedded, not produced, here.** A frame's `<om-map>` child
is built by the reused `OnlyMapWriter` from the vendored `qgis2webmap` core
(onlymap-js#41's "live frames" section) -- a module that imports `qgis` is
nowhere near this file. This writer just takes the already-rendered markup
string, keyed by frame id, and drops it in **as opaque text** -- it never
parses or imports anything from `OnlyMapWriter`. That markup may itself
contain `build_manifest`'s gzipped layer-data blocks
(`<script type="application/x-om-gzip">`, `packaging.asset_embedder.
GZIP_SCRIPT_TYPE`), exactly as a standalone map does, so the bootstrap script
has to inflate those too -- see non-negotiable 1 below.

Non-negotiables verified in the spikes (`specs/cartograph/spike-findings.md`):

1. **The sheet must stay inside a `<template>`** until the loader is wired
   up. Importing the runtime module registers the custom elements
   immediately, and a live `<om-frame>` in the DOM at that moment calls the
   *default* core loader (a relative URL that does not resolve from a blob
   import) -- silently, with no error, no console warning, and
   `validateCartograph` still reporting `valid: true`. The only way to avoid
   it is to never let the markup touch `document.body` before
   `setCoreLoader` runs. The bootstrap therefore clones `template.content`
   into a detached `DocumentFragment` first (cloning alone triggers no
   upgrade), inflates any gzipped layer-data blocks *inside that fragment*
   -- `document.querySelectorAll` cannot see inside a `<template>`, so this
   cannot run against `document` the way the standalone map's bootstrap
   does -- then calls `setCoreLoader`, and only then appends the fragment.
2. **Attributes and inline style must agree**, or the validator raises an
   error. Every placed element therefore writes the *same* formatted mm
   numbers twice: once as `x`/`y`/`w`/`h` and once inside `style=`.
3. **A static-only page never embeds the core.** Shipping 8.6 MB of WebGL
   runtime for a 300 KB survey sheet is exactly the "map bundle gets heavier
   for no reason" failure `docs/cartograph.md` calls out as CI-enforced on
   the runtime side; this writer enforces the plugin-side half of it by
   simply never reading `runtime.core_js` unless a live frame exists.

The one exception to "opaque": a layer drawn in several live frames carries
the same rows in each frame's markup, and the runtime reads rows only from
the layer's own `<script>` child. `_share_repeated_data` keeps one copy in
the file and the bootstrap copies it back into the fragment before it
connects -- the same place, and the same reason, as the gzip inflation.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.cartograph_ir import (
    ATLAS_SOURCE_ATTRIBUTE,
    AtlasSpec,
    Box,
    CartographItem,
    CartographSpec,
    FrameGeoreference,
    FrameSpec,
    GraticuleSpec,
    ImageSpec,
    LegendRowSpec,
    LegendSpec,
    NorthSpec,
    ScalebarSpec,
    ShapeSpec,
    TextSpec,
)
from ..packaging.asset_embedder import GZIP_SCRIPT_TYPE, gzip_base64
from ..packaging.raster_embed import build_raster_embed

#: Shown instead of a live cartograph when the browser runs no JavaScript:
#: its live maps and the inlined runtime need it. (A static-only cartograph
#: reads without JavaScript and needs no such note.)
NOSCRIPT_MESSAGE = (
    "This cartograph needs JavaScript to show its maps. Open it in a web "
    "browser with JavaScript turned on."
)

#: One template id per document; there is only ever one sheet per page.
_TEMPLATE_ID = "sheet"

#: A data block standing in for an identical one elsewhere in the sheet; the
#: bootstrap swaps it for a copy before the sheet connects. Its own script
#: type, so nothing can mistake the empty placeholder for empty rows.
SHARED_DATA_TYPE = "application/x-qgis2webmap-same-data"

#: Below this a placeholder saves too little to be worth the indirection.
_SHARE_MIN_BYTES = 256

#: A layer's inline rows as `build_manifest` writes them. `json_for_script`
#: escapes `</` inside the payload, so the first `</script>` ends it.
_DATA_BLOCK_RE = re.compile(
    r'<script type="(application/json|' + re.escape(GZIP_SCRIPT_TYPE) + r')">'
    r"(.*?)</script>",
    re.DOTALL,
)

#: On a live frame the page script moves per atlas page (see `_ATLAS_CAMERAS`).
ATLAS_CAMERAS_ATTRIBUTE = "data-qgis2webmap-atlas-cameras"

#: On a live frame: where QGIS draws its map, for a browser without WebGL
#: (see `_NO_WEBGL`).
GEOREFERENCE_ATTRIBUTE = "data-qgis2webmap-georef"

#: Shown in a live frame's place when the browser cannot draw maps.
NO_WEBGL_MESSAGE = "This map needs WebGL, which this browser does not provide."


@dataclass(frozen=True)
class CartographRuntimeBundle:
    """The runtime bytes this writer inlines.

    `core_js`/`core_css` are the 8.6 MB map runtime -- required only when the
    spec has at least one live frame; `write_cartograph_html` raises if a
    live frame is present and either is missing, and never reads them at all
    for a static-only spec.
    """

    cartograph_js: bytes
    cartograph_css: str
    core_js: bytes | None = None
    core_css: str | None = None


def write_cartograph_html(
    spec: CartographSpec,
    runtime: CartographRuntimeBundle,
    live_frame_markup: Mapping[str, str] | None = None,
    *,
    embedded_rasters: Sequence[tuple[str, bytes]] = (),
) -> str:
    """Render `spec` into a complete, standalone HTML document string.

    `embedded_rasters` are the live frames' rasters as `(src, bytes)`
    (`packaging/raster_staging.py`): the page carries them and answers the
    runtime's range requests for them itself, exactly as a single-file web
    map does (`packaging/raster_embed.py`), ahead of the runtime script.
    """
    markup_by_frame, has_shared_data = _share_repeated_data(live_frame_markup or {})
    has_live = any(
        isinstance(item, FrameSpec) and item.mode == "live" for item in spec.items
    )
    if has_live and (runtime.core_js is None or runtime.core_css is None):
        raise ValueError(
            "a cartograph with a live frame needs core_js and core_css in the "
            "runtime bundle"
        )

    sheet = _render_sheet(spec, markup_by_frame)
    css = f"{runtime.cartograph_css}\n{PRINT_COLOURS_CSS}"
    if has_live:
        # mypy cannot see the guard above narrowed core_css to `str`.
        css = f"{css}\n{runtime.core_css or ''}"

    title = html.escape(spec.title or spec.cartograph_id, quote=True)
    has_atlas_cameras = any(
        isinstance(item, FrameSpec) and item.atlas_cameras for item in spec.items
    )
    script = _render_script(
        runtime,
        has_live=has_live,
        has_atlas_cameras=has_atlas_cameras,
        has_shared_data=has_shared_data,
    )
    if has_live:
        # Inert until the core loader is set (see the module docstring).
        body = (
            f'<template id="{_TEMPLATE_ID}">{sheet}</template>\n'
            f'<noscript><p style="font:16px sans-serif;margin:2em">'
            f"{html.escape(NOSCRIPT_MESSAGE)}</p></noscript>\n"
        )
        if embedded_rasters:
            body += build_raster_embed(embedded_rasters) + "\n"
    else:
        # No live frame, no loader to wait for: the sheet is in the page from
        # the start, so it reads with JavaScript off too - the runtime's
        # stylesheet places static frames, text and shapes at their mm
        # positions before (or without) the elements upgrading.
        body = f"{sheet}\n"

    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        f"<style>{css}\n{FIT_PAGE_CSS}</style>\n"
        "</head>\n"
        '<body style="margin:0">\n'
        f"{body}"
        f'<script type="module">{script}</script>\n'
        f"<script>{FIT_PAGE_SCRIPT}</script>\n"
        "</body>\n"
        "</html>\n"
    )


#: Browsers print without background colours and images unless the user ticks
#: "Background graphics", which drops a page theme's background, frame fills and
#: legend boxes, leaving light text on white. The page asks for its colours as
#: drawn; the property is inherited, so it reaches the runtime's shadow trees.
PRINT_COLOURS_CSS = "html{print-color-adjust:exact;-webkit-print-color-adjust:exact}"


#: A page is laid out at its true size (A3 landscape is 1587 x 1123 px), which
#: is taller than most windows, so a screen opened with the sheet cut off and
#: the legend out of reach. On screen the page is scaled down, once, to fit the
#: window, centred and never above 100%. The scale is a transform, so the
#: sheet keeps its true layout and every map frame still maps pointer
#: positions through it. `FIT_PAGE_SCRIPT` stands down for `?print=`,
#: `?export=` and `?atlas=`, whose output must stay at true size, and the
#: rules are `@media screen`, so printing never sees them.
FIT_PAGE_ATTRIBUTE = "data-qgis2webmap-fit"
FIT_PAGE_CSS = (
    "@media screen{"
    f"html[{FIT_PAGE_ATTRIBUTE}=fit] body{{overflow:hidden}}"
    f"html[{FIT_PAGE_ATTRIBUTE}=fit] om-cartograph{{position:fixed!important;"
    "left:0;top:0;"
    "transform-origin:0 0;"
    "transform:translate(var(--q2w-fit-x),var(--q2w-fit-y)) scale(var(--q2w-fit-k))}"
    "[data-qgis2webmap-fit-toggle]{position:fixed;right:12px;bottom:12px;"
    "z-index:2147483646;font:600 12px/1 system-ui,sans-serif;padding:7px 10px;"
    "color:#2a1f2d;background:rgba(255,255,255,.92);border:1px solid #bbb;"
    "border-radius:6px;cursor:pointer}"
    "}"
    "@media print{[data-qgis2webmap-fit-toggle]{display:none!important}}"
)
FIT_PAGE_LABEL_FIT = "Fit page"
FIT_PAGE_LABEL_ACTUAL = "Actual size"
FIT_PAGE_SCRIPT = "\n".join(
    [
        "(() => {",
        "  const asked = new URLSearchParams(location.search);",
        "  if (['print', 'export', 'atlas'].some((name) => asked.has(name))) return;",
        "  const root = document.documentElement;",
        f"  const ATTRIBUTE = '{FIT_PAGE_ATTRIBUTE}';",
        "  let button = null;",
        # A reload (the browser preview following a save) keeps the choice.
        "  try {",
        "    const kept = sessionStorage.getItem('qgis2webmapFit');",
        "    if (kept) root.dataset.qgis2webmapFitWanted = kept;",
        "  } catch { /* storage may be blocked */ }",
        "  const apply = () => {",
        "    const sheet = document.querySelector('om-cartograph');",
        "    if (!sheet || !sheet.offsetWidth || !sheet.offsetHeight) return;",
        "    const width = sheet.offsetWidth;",
        "    const height = sheet.offsetHeight;",
        "    const k = Math.min(1,",
        "      innerWidth / width, innerHeight / height);",
        "    const fits = k < 1;",
        "    if (!button) {",
        "      button = document.createElement('button');",
        "      button.type = 'button';",
        "      button.setAttribute('data-qgis2webmap-fit-toggle', '');",
        "      button.setAttribute('data-om-carto-screen-only', '');",
        "      button.addEventListener('click', () => {",
        "        root.dataset.qgis2webmapFitWanted =",
        "          root.dataset.qgis2webmapFitWanted === 'actual' ? 'fit' : 'actual';",
        "        try { sessionStorage.setItem('qgis2webmapFit',",
        "          root.dataset.qgis2webmapFitWanted); } catch { /* blocked */ }",
        "        apply();",
        "        button.blur();",
        "      });",
        "      document.body.appendChild(button);",
        "    }",
        "    const wanted = root.dataset.qgis2webmapFitWanted !== 'actual';",
        "    button.hidden = !fits;",
        f"    button.textContent = wanted ? '{FIT_PAGE_LABEL_ACTUAL}'"
        f" : '{FIT_PAGE_LABEL_FIT}';",
        "    if (!fits || !wanted) { root.removeAttribute(ATTRIBUTE); return; }",
        "    root.style.setProperty('--q2w-fit-k', String(k));",
        "    root.style.setProperty('--q2w-fit-x',",
        "      `${Math.max(0, (innerWidth - width * k) / 2)}px`);",
        "    root.style.setProperty('--q2w-fit-y',",
        "      `${Math.max(0, (innerHeight - height * k) / 2)}px`);",
        "    root.setAttribute(ATTRIBUTE, 'fit');",
        "  };",
        "  const observed = new WeakSet();",
        "  const watch = () => {",
        "    const sheet = document.querySelector('om-cartograph');",
        "    if (sheet && !observed.has(sheet)) {",
        "      observed.add(sheet);",
        "      new ResizeObserver(apply).observe(sheet);",
        "    }",
        "    apply();",
        "  };",
        "  window.addEventListener('resize', apply);",
        "  new MutationObserver(watch).observe(document.body, { childList: true });",
        "  watch();",
        "})();",
    ]
)


# --------------------------------------------------------------------------
# The bootstrap script
# --------------------------------------------------------------------------


def _render_script(
    runtime: CartographRuntimeBundle,
    *,
    has_live: bool,
    has_atlas_cameras: bool = False,
    has_shared_data: bool = False,
) -> str:
    carto_gz = gzip_base64(runtime.cartograph_js)
    lines = [
        "async function inflate(base64) {",
        "  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));",
        "  const stream = new Blob([bytes])",
        "    .stream()",
        '    .pipeThrough(new DecompressionStream("gzip"));',
        "  return await new Response(stream).text();",
        "}",
        "async function blobImport(source) {",
        "  const blob = new Blob([source], { type: 'text/javascript' });",
        "  return await import(URL.createObjectURL(blob));",
        "}",
        f'const CARTO_GZ = "{carto_gz}";',
        "const carto = await blobImport(await inflate(CARTO_GZ));",
    ]
    if not has_live:
        # The sheet is already in the page: importing the runtime upgrades
        # it and installs the URL triggers on it, and that is all.
        indent = "\n      "
        return indent + indent.join(lines) + "\n    "
    lines += [
        # Cloned from the <template> -- still inert, so touching it here
        # (even defining custom elements already happened above) triggers no
        # upgrade: a DocumentFragment cloned from template content stays
        # disconnected until it is actually appended to document.body below.
        f'const template = document.getElementById("{_TEMPLATE_ID}");',
        "const fragment = template.content.cloneNode(true);",
    ]
    if has_shared_data:
        # Before the inflation below, so a shared gzipped block is copied as
        # it is and every copy is inflated like any other block.
        lines += [
            f"const SHARED_SELECTOR = 'script[type=\"{SHARED_DATA_TYPE}\"]';",
            "for (const placeholder of fragment.querySelectorAll(SHARED_SELECTOR)) {",
            "  const key = placeholder.getAttribute('data-qgis2webmap-same-as');",
            "  const source = fragment.querySelector(",
            '    `script[data-qgis2webmap-data="${key}"]`);',
            "  if (!source) { placeholder.remove(); continue; }",
            "  const copy = source.cloneNode(true);",
            "  copy.removeAttribute('data-qgis2webmap-data');",
            "  placeholder.replaceWith(copy);",
            "}",
        ]
    lines += [
        # A live frame's embedded <om-map> may carry its layer data as
        # gzipped base64 (packaging/asset_embedder.py's GZIP_SCRIPT_TYPE),
        # exactly like a standalone map's data blocks. querySelectorAll on
        # `document` cannot see inside a <template>, so this has to run
        # against the cloned fragment, and it has to finish before the core
        # loader ever reads the manifest -- the core upgrades <om-layer> and
        # reads its data the moment the fragment is connected.
        f"const GZIP_SELECTOR = 'script[type=\"{GZIP_SCRIPT_TYPE}\"]';",
        "for (const block of fragment.querySelectorAll(GZIP_SELECTOR)) {",
        "  const json = document.createElement('script');",
        "  json.type = 'application/json';",
        "  json.textContent = await inflate(block.textContent);",
        "  block.replaceWith(json);",
        "}",
    ]
    if has_live:
        if runtime.core_js is None:  # guarded by write_cartograph_html
            raise ValueError("a live cartograph needs core_js")
        core_gz = gzip_base64(runtime.core_js)
        lines += [
            f'const CORE_GZ = "{core_gz}";',
            "carto.setCoreLoader(async () => blobImport(await inflate(CORE_GZ)));",
        ]
    if has_atlas_cameras:
        lines += _ATLAS_CAMERAS
    lines += _NO_WEBGL
    lines.append("document.body.appendChild(fragment);")
    # The runtime installs `?print=1` / `?export=png` / `?cvd=` when it is
    # imported, which is before the sheet leaves its <template>, so it finds
    # no page to act on. Installing again now that the page is connected is
    # what makes the Share menu's Export PDF/PNG work at all.
    lines += _RASTER_GATE
    lines.append("carto.installUrlActions();")
    lines += _ATLAS_ACTIONS
    indent = "\n      "
    return indent + indent.join(lines) + "\n    "


#: Print, PNG export and the atlas jobs capture each live frame once the
#: runtime calls it ready, and the runtime calls a raster layer ready before it
#: has read its file - its readiness is deck's, and a COG layer with nothing
#: read yet has no tiles to wait for. Their first capture had no rasters. So
#: those actions wait here until every raster the page carries has been read
#: by the stand-in (`packaging/raster_embed.py`), no read has happened for a
#: moment, and every map has settled after the last one. A raster that fails
#: or never loads stops the action with a notice instead of a page missing it;
#: a colour-vision preview still applies. Without WebGL the live frames turn
#: static and read no raster, so there is nothing to wait for.
_RASTER_GATE = [
    "const RASTER_WAIT_MS = 60000;",
    "const RASTER_QUIET_MS = 750;",
    "const asked = new URLSearchParams(location.search);",
    "const rasters = window.qgis2webmapRasters;",
    "if (hasWebgl && rasters && rasters.names.length",
    "    && ['print', 'export', 'atlas'].some((name) => asked.has(name))) {",
    "  const page = document.querySelector('om-cartograph');",
    "  const notice = atlasNotice();",
    "  let cancelled = false;",
    "  const dropActions = () => {",
    "    for (const name of ['print', 'export', 'atlas', 'dpi', 'page']) {",
    "      asked.delete(name);",
    "    }",
    "    const search = asked.toString();",
    "    const rest = search ? `?${search}` : '';",
    "    history.replaceState(null, '', location.pathname + rest + location.hash);",
    "  };",
    # Cancel drops the action from the address instead of reloading: a reload
    # would keep `?print=1` and start the same wait again.
    "  notice.progress('Waiting for the rasters to draw...', () => {",
    "    cancelled = true;",
    "    dropActions();",
    "    notice.done();",
    "  });",
    "  let problem;",
    "  try {",
    "    problem = await rastersDrawn(page, () => cancelled);",
    "  } catch (error) {",
    "    problem = `The rasters could not be waited for (${error.message}).`;",
    "  }",
    "  if (cancelled) {",
    "    console.info('[qgis2webmap] waiting for the rasters was cancelled');",
    "  } else if (problem) {",
    "    dropActions();",
    "    console.warn(`[qgis2webmap] ${problem}`);",
    "    notice.problem(`${problem} Nothing was printed or exported, so the result `",
    "      + 'is not missing them. Reload the page to try again.');",
    "  } else {",
    "    notice.done();",
    "  }",
    "}",
    "async function rastersDrawn(page, isCancelled) {",
    "  const deadline = performance.now() + RASTER_WAIT_MS;",
    "  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));",
    # A hidden tab never runs animation frames, so each one gives up soon.
    "  const frame = () => new Promise((resolve) => {",
    "    requestAnimationFrame(resolve);",
    "    setTimeout(resolve, 100);",
    "  });",
    "  await carto.whenPageReady(page, RASTER_WAIT_MS);",
    "  for (;;) {",
    "    if (isCancelled()) return null;",
    "    if (rasters.failed.length) {",
    "      return `A raster could not be drawn: ${rasters.failed[0]}.`;",
    "    }",
    "    const unread = rasters.names.filter((name) => !rasters.read.has(name));",
    "    if (performance.now() > deadline) {",
    "      return unread.length",
    "        ? `These rasters did not load in time: ${unread.join(', ')}.`",
    "        : 'The rasters did not finish drawing in time.';",
    "    }",
    "    const busy = performance.now() - rasters.lastRead < RASTER_QUIET_MS;",
    "    if (unread.length || busy) {",
    "      await pause(100);",
    "      continue;",
    "    }",
    "    const before = rasters.lastRead;",
    "    await Promise.all([...page.querySelectorAll('om-map')].map(",
    "      (map) => map.whenSettled?.({ timeout: RASTER_WAIT_MS })));",
    "    await frame();",
    "    await frame();",
    "    if (rasters.lastRead === before && !rasters.failed.length) return null;",
    "  }",
    "}",
]


#: A browser with no WebGL2 cannot draw a live frame, and the runtime then
#: never settles the frame (upstream request #1): the page would keep an
#: empty box and every `{{frame.*}}` token, and print or PNG export would
#: wait forever. So before the sheet connects, each live frame that carries
#: QGIS's georeference becomes a static frame placed by it, with a note in
#: the map's place -- its scale bar, tokens and grid still read QGIS's map.
_NO_WEBGL = [
    "const hasWebgl = (() => {",
    "  try { return !!document.createElement('canvas').getContext('webgl2'); }",
    "  catch { return false; }",
    "})();",
    "if (!hasWebgl) {",
    f"  const GEOREF = '{GEOREFERENCE_ATTRIBUTE}';",
    "  for (const frame of fragment.querySelectorAll(`om-frame[${GEOREF}]`)) {",
    "    let georef;",
    "    try { georef = JSON.parse(frame.getAttribute(GEOREF)); } catch { continue; }",
    "    for (const name of ['center', 'zoom', 'bearing', 'pitch']) {",
    "      frame.removeAttribute(name);",
    "    }",
    "    frame.setAttribute('mode', 'static');",
    "    frame.setAttribute('corners', JSON.stringify(georef.corners));",
    "    frame.setAttribute('crs', georef.crs);",
    "    if (georef['crs-def']) frame.setAttribute('crs-def', georef['crs-def']);",
    "    const w = Number(frame.getAttribute('w')) || 100;",
    "    const h = Number(frame.getAttribute('h')) || 100;",
    "    const note = document.createElement('img');",
    f"    note.alt = {json.dumps(NO_WEBGL_MESSAGE)};",
    "    const svg = `<svg xmlns='http://www.w3.org/2000/svg' `",
    "      + `viewBox='0 0 ${w} ${h}'><text x='${w / 2}' y='${h / 2}' `",
    "      + `text-anchor='middle' font-family='sans-serif' font-size='3.5' `",
    "      + `fill='#6d5b72'>${note.alt}</text></svg>`;",
    "    note.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);",
    "    const map = frame.querySelector(':scope > om-map');",
    "    if (map) map.replaceWith(note); else frame.append(note);",
    # A legend lists the live map's layers, so with no map it would only
    # print "waiting for its frame's layers".
    '    const forFrame = `om-legend[for="${CSS.escape(frame.id)}"]`;',
    "    for (const legend of fragment.querySelectorAll(forFrame)) {",
    "      legend.setAttribute('hidden', '');",
    "    }",
    "  }",
    "}",
]


#: QGIS's fixed and predefined atlas scales (and points, which QGIS centres
#: at the map's scale even in Auto mode): the runtime can only fit a feature
#: into its frame, so those frames carry `atlas-fit="fixed"` -- the runtime
#: leaves them alone -- and, per feature id, the camera QGIS's own atlas gave
#: them (`core/cartograph_export.py`). On each page this applies the camera
#: through the frame's public `fitBounds`, with the one box whose Web
#: Mercator fit IS that camera, because `fitBounds` also re-derives the
#: frame's corners at once; then the furniture is redrawn so scale bars and
#: `{{frame.*}}` tokens read the new page. Added before the sheet connects:
#: the atlas applies page one as soon as its frame mounts.
_ATLAS_CAMERAS = [
    f"const ATLAS_CAMERAS = '{ATLAS_CAMERAS_ATTRIBUTE}';",
    "const atlasCameras = new WeakMap();",
    "document.addEventListener('om-atlas-page', (event) => {",
    "  const id = event.detail?.feature?.id;",
    "  const sheet = event.target.closest?.('om-cartograph');",
    "  if (id === undefined || id === null || !sheet) return;",
    "  let moved = false;",
    "  for (const frame of sheet.querySelectorAll(`om-frame[${ATLAS_CAMERAS}]`)) {",
    "    if (!atlasCameras.has(frame)) {",
    "      let cameras = {};",
    "      try { cameras = JSON.parse(frame.getAttribute(ATLAS_CAMERAS)) ?? {}; }",
    "      catch { console.warn('[qgis2webmap] unreadable atlas cameras'); }",
    "      atlasCameras.set(frame, cameras);",
    "    }",
    "    const camera = atlasCameras.get(frame)[String(id)];",
    "    if (!camera || typeof frame.fitBounds !== 'function') continue;",
    "    frame.fitBounds(boundsForCamera(frame, camera));",
    "    moved = true;",
    "  }",
    "  if (moved) sheet.refreshFurniture();",
    "});",
    "function boundsForCamera(frame, [lon, lat, zoom]) {",
    "  const world = 512 * 2 ** zoom;",
    "  const pxPerMm = 96 / 25.4;",
    "  const halfWidth = Number(frame.getAttribute('w')) * pxPerMm / 2;",
    "  const halfHeight = Number(frame.getAttribute('h')) * pxPerMm / 2;",
    "  const sin = Math.sin(lat * Math.PI / 180);",
    "  const x = (lon + 180) / 360 * world;",
    "  const y = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world;",
    "  const toLon = (px) => px / world * 360 - 180;",
    "  const toLat = (py) =>",
    "    Math.atan(Math.sinh(Math.PI * (1 - 2 * py / world))) * 180 / Math.PI;",
    "  return [toLon(x - halfWidth), toLat(y + halfHeight),",
    "    toLon(x + halfWidth), toLat(y - halfHeight)];",
    "}",
]


#: Two host triggers of this plugin's own, for an atlas: the runtime's
#: `?export=png&page=N` downloads one sheet per URL, which is no way to take
#: a 50-page series home. `?atlas=zip` downloads every sheet as PNGs in one
#: ZIP; `?atlas=print` renders every sheet and hands them to the browser's
#: print dialog together, so "Save as PDF" makes one multi-page PDF at true
#: page size. Same opt-in as the runtime's own triggers: the root must carry
#: `allow-url-actions`, and an editor host (`data-om-host="editor"`) owns
#: its own print UI. A separate parameter, not `export=`, so the runtime's
#: trigger never sees it.
_ATLAS_ACTIONS = [
    "const atlasAction = new URLSearchParams(location.search).get('atlas');",
    "const sheet = document.querySelector('om-cartograph');",
    "if (atlasAction && sheet && sheet.hasAttribute('allow-url-actions')",
    "    && document.documentElement.dataset.omHost !== 'editor') {",
    "  runAtlasAction(atlasAction).catch((error) => {",
    "    console.warn(`[qgis2webmap] atlas ${atlasAction} failed: ${error.message}`);",
    "    atlasNotice().problem(`The atlas could not be prepared: ${error.message}. `",
    "      + 'Reload the page to try again.');",
    "  });",
    "}",
    # What the reader sees while a long atlas job runs, and when it fails:
    # one line at the foot of the screen, never printed.
    "function atlasNotice() {",
    "  let notice = document.querySelector('[data-qgis2webmap-atlas-notice]');",
    "  if (!notice) {",
    "    notice = document.createElement('div');",
    "    notice.setAttribute('data-qgis2webmap-atlas-notice', '');",
    "    notice.setAttribute('role', 'status');",
    "    notice.style.cssText = 'position:fixed;left:50%;bottom:16px;'",
    "      + 'transform:translateX(-50%);max-width:min(90vw,40em);z-index:2147483647;'",
    "      + 'padding:10px 14px;border-radius:6px;font:14px/1.4 system-ui,sans-serif;'",
    "      + 'background:#1f2933;color:#fff;box-shadow:0 2px 10px rgba(0,0,0,.35);';",
    "    const text = document.createElement('span');",
    "    const button = document.createElement('button');",
    "    button.style.cssText = 'margin-left:12px;font:inherit;cursor:pointer;';",
    "    notice.append(text, button);",
    "    const hide = document.createElement('style');",
    "    hide.textContent = '@media print { [data-qgis2webmap-atlas-notice] '",
    "      + '{ display: none !important } }';",
    "    document.body.append(hide, notice);",
    "  }",
    "  const [text, button] = notice.children;",
    "  return {",
    "    progress(message, onCancel) {",
    "      notice.setAttribute('role', 'status');",
    "      text.textContent = message;",
    "      button.textContent = 'Cancel';",
    "      button.hidden = false;",
    "      button.onclick = onCancel;",
    "    },",
    "    problem(message) {",
    "      notice.setAttribute('role', 'alert');",
    "      notice.style.background = '#8a1c1c';",
    "      text.textContent = message;",
    "      button.textContent = 'Dismiss';",
    "      button.hidden = false;",
    "      button.onclick = () => notice.remove();",
    "    },",
    "    done() { notice.remove(); },",
    "  };",
    "}",
    "async function runAtlasAction(kind) {",
    "  await carto.whenPageReady(sheet);",
    "  const atlas = sheet.atlas;",
    "  if (!atlas) throw new Error('this cartograph has no atlas');",
    "  atlas.refresh?.();",
    "  const query = new URLSearchParams(location.search);",
    # One resolution for every route: the address's `dpi=` when it names one,
    # else the cartograph's own saved `dpi` (what the runtime's PNG export
    # also falls back to), else 300.
    "  const dpiAsked = () => Math.min(600, Math.max(72,",
    "    Number(query.get('dpi')) || Number(sheet.getAttribute('dpi')) || 300));",
    "  const stem = sheet.getAttribute('cartograph-id') || 'cartograph';",
    "  if (kind === 'zip') {",
    "    const dpi = dpiAsked();",
    "    const result = await carto.renderCartographAtlas(sheet, { dpi });",
    "    const link = document.createElement('a');",
    "    link.href = URL.createObjectURL(result.blob);",
    "    link.download = `${stem}-atlas.zip`;",
    "    document.body.appendChild(link);",
    "    link.click();",
    "    link.remove();",
    "    setTimeout(() => URL.revokeObjectURL(link.href), 10000);",
    "    return;",
    "  }",
    "  if (kind !== 'print') throw new Error(`unknown atlas action ${kind}`);",
    # Print-resolution images, one per sheet, at the sheet's own size; the
    # page's `@page` rule (written by the runtime from the sheet) still sets
    # the paper, so Chromium prints every sheet at true size. Sheets are
    # rendered one after another, and every address made for one is revoked
    # whatever happens: when a page fails, when the reader cancels, and when
    # the print dialog closes.
    "  const dpi = dpiAsked();",
    "  const widthMm = sheet.offsetWidth * 25.4 / 96;",
    "  const heightMm = sheet.offsetHeight * 25.4 / 96;",
    "  const sheets = document.createElement('div');",
    "  sheets.setAttribute('data-qgis2webmap-atlas-print', '');",
    "  const style = document.createElement('style');",
    "  const printed = '[data-qgis2webmap-atlas-print]';",
    "  style.textContent = `@media screen { ${printed} { display: none } }`",
    "    + ' @media print { om-cartograph { display: none !important }'",
    "    + ` ${printed} { display: block !important } }`;",
    "  const urls = [];",
    "  const release = () => {",
    "    for (const url of urls.splice(0)) URL.revokeObjectURL(url);",
    "    sheets.remove();",
    "    style.remove();",
    "  };",
    "  const notice = atlasNotice();",
    "  let cancelled = false;",
    "  const total = atlas.count;",
    "  if (!total) throw new Error('this atlas has no pages');",
    "  let index = 0;",
    "  try {",
    "    for (; index < total; index++) {",
    "      notice.progress(`Preparing page ${index + 1} of ${total}`, () => {",
    "        cancelled = true;",
    "      });",
    "      if (cancelled) break;",
    "      const page = await carto.renderCartographAtlas(",
    "        sheet, { dpi, page: index });",
    "      const url = URL.createObjectURL(page.blob);",
    "      urls.push(url);",
    "      const image = new Image();",
    "      image.src = url;",
    "      image.style.cssText = `display:block;width:${widthMm}mm;`",
    "        + `height:${heightMm}mm;break-after:page;`;",
    "      await image.decode();",
    "      sheets.appendChild(image);",
    "    }",
    "  } catch (error) {",
    "    release();",
    "    notice.problem(`Page ${index + 1} of ${total} could not be prepared for `",
    "      + `printing (${error.message}). Nothing was sent to the printer. Reload `",
    "      + 'the page to try again.');",
    "    console.warn('[qgis2webmap] atlas print failed on page '",
    "      + `${index + 1}: ${error.message}`);",
    "    return;",
    "  }",
    "  if (cancelled) {",
    "    release();",
    "    notice.done();",
    "    return;",
    "  }",
    "  notice.done();",
    "  document.body.append(style, sheets);",
    "  window.addEventListener('afterprint', release, { once: true });",
    "  window.print();",
    "}",
]


# --------------------------------------------------------------------------
# Number and attribute formatting
# --------------------------------------------------------------------------


def _format_mm(value: float) -> str:
    """Format a millimetre value with no float noise, deterministically.

    Rounded to four decimal places (sub-micron at page scale -- far finer
    than anything that matters on a printed sheet) and trimmed of trailing
    zeros, so the *attribute* value and the *style* value for the same
    number are always byte-identical, which is what the validator checks.
    """
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite mm value: {value!r}")
    if value == 0:
        value = 0.0  # normalise -0.0
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _format_num(value: float) -> str:
    """Format a plain (non-mm) number the same deterministic way."""
    return _format_mm(value)


def _attr(name: str, value: object) -> str:
    """One ` name="value"` fragment, or `""` when there is nothing to write.

    A boolean `True` writes `name=""` -- present but empty -- rather than the
    bare HTML-boolean spelling (`name`, no `=`). The runtime reads these with
    `hasAttribute`, so both spell the same thing to it, but `name=""` is also
    well-formed XML, which keeps every placed element parseable with ordinary
    XML tooling (the validator and this project's own tests both do).
    """
    if value is None or value is False:
        return ""
    if value is True:
        return f' {name}=""'
    return f' {name}="{html.escape(str(value), quote=True)}"'


def _style(box: Box, opacity: float = 1.0) -> str:
    style = (
        f"left:{_format_mm(box.x)}mm;"
        f"top:{_format_mm(box.y)}mm;"
        f"width:{_format_mm(box.w)}mm;"
        f"height:{_format_mm(box.h)}mm"
    )
    # Only a translucent item names it, so an opaque item's markup is unchanged.
    if opacity < 1.0:
        style += f";opacity:{_format_num(opacity)}"
    return style


def _placement_attrs(
    item_id: str,
    box: Box,
    *,
    rotation: float = 0.0,
    z: int | None = None,
    locked: bool = False,
    hidden: bool = False,
    opacity: float = 1.0,
) -> str:
    """The attributes every placed element shares: `id x y w h` plus the
    optional `rotation z locked hidden`, and the matching inline `style`.
    `opacity` has no attribute in the runtime's vocabulary; it travels in the
    `style` only, as CSS the runtime leaves alone."""
    parts = [
        _attr("id", item_id),
        _attr("x", _format_mm(box.x)),
        _attr("y", _format_mm(box.y)),
        _attr("w", _format_mm(box.w)),
        _attr("h", _format_mm(box.h)),
    ]
    if rotation:
        parts.append(_attr("rotation", _format_num(rotation)))
    if z is not None:
        parts.append(_attr("z", z))
    if locked:
        parts.append(_attr("locked", True))
    if hidden:
        parts.append(_attr("hidden", True))
    parts.append(_attr("style", _style(box, opacity)))
    return "".join(parts)


def _lonlat_json(point: tuple[float, float]) -> str:
    return json.dumps([point[0], point[1]])


def _share_repeated_data(
    markup_by_frame: Mapping[str, str],
) -> tuple[dict[str, str], bool]:
    """Write a data block repeated across live frames once.

    The first copy gets `data-qgis2webmap-data="<key>"`, every other copy
    becomes an empty `SHARED_DATA_TYPE` placeholder naming that key. Text
    equality is an honest identity here: the same rows with the same popup
    and field choices serialise to the same text, and anything that differs
    -- a frame clipped to its own extent -- is simply not shared. Returns the
    rewritten markup and whether anything was shared.
    """
    seen: dict[str, int] = {}
    for markup in markup_by_frame.values():
        for match in _DATA_BLOCK_RE.finditer(markup):
            if len(match.group(2)) >= _SHARE_MIN_BYTES:
                seen[match.group(0)] = seen.get(match.group(0), 0) + 1
    keys: dict[str, str] = {}
    for block, count in seen.items():
        if count > 1:
            keys[block] = f"d{len(keys) + 1}"
    if not keys:
        return dict(markup_by_frame), False

    written: set[str] = set()

    def share(match: re.Match[str]) -> str:
        key = keys.get(match.group(0))
        if key is None:
            return match.group(0)
        if key in written:
            return (
                f'<script type="{SHARED_DATA_TYPE}" '
                f'data-qgis2webmap-same-as="{key}"></script>'
            )
        written.add(key)
        return (
            f'<script type="{match.group(1)}" data-qgis2webmap-data="{key}">'
            f"{match.group(2)}</script>"
        )

    shared = {
        frame_id: _DATA_BLOCK_RE.sub(share, markup)
        for frame_id, markup in markup_by_frame.items()
    }
    return shared, True


def _atlas_cameras_json(item: FrameSpec) -> str:
    """`{"<feature id>": [lon, lat, zoom], ...}`, at the precision the
    runtime's own `fitBounds` writes a camera with."""
    return json.dumps(
        {
            str(camera.feature_id): [
                round(camera.center[0], 8),
                round(camera.center[1], 8),
                round(camera.zoom, 4),
            ]
            for camera in item.atlas_cameras
        },
        separators=(",", ":"),
    )


def _georeference_json(georeference: FrameGeoreference) -> str:
    """`{"corners": [...], "crs": ..., "crs-def": ...}`: the attributes the
    page script gives the frame if it has to turn it static."""
    data: dict[str, object] = {
        "corners": [[lon, lat] for lon, lat in georeference.corners],
        "crs": georeference.crs,
    }
    if georeference.crs_def:
        data["crs-def"] = georeference.crs_def
    return json.dumps(data, separators=(",", ":"))


def _corners_json(corners: tuple[tuple[float, float], ...]) -> str:
    return json.dumps([[lon, lat] for lon, lat in corners])


# --------------------------------------------------------------------------
# Element rendering
# --------------------------------------------------------------------------


def _render_sheet(spec: CartographSpec, markup_by_frame: Mapping[str, str]) -> str:
    attrs = [
        _attr("cartograph-id", spec.cartograph_id),
        _attr("format", "cartograph/2"),
    ]
    if spec.size is not None:
        attrs.append(_attr("size", spec.size))
    elif spec.width is not None and spec.height is not None:
        attrs.append(_attr("width", _format_mm(spec.width)))
        attrs.append(_attr("height", _format_mm(spec.height)))
    else:  # enforced by CartographSpec.__post_init__
        raise ValueError("CartographSpec needs either size or width and height")
    attrs.append(_attr("orientation", spec.orientation))
    attrs.append(_attr("theme", spec.theme))
    attrs.append(_attr("bg", spec.bg))
    attrs.append(_attr("color", spec.color))
    attrs.append(_attr("font", spec.font))
    attrs.append(_attr("title", spec.title or None))
    attrs.append(_attr("attribution", spec.attribution or None))
    attrs.append(_attr("notes", spec.notes))
    attrs.append(_attr("bleed", _format_mm(spec.bleed) if spec.bleed else None))
    attrs.append(_attr("crop-marks", spec.crop_marks))
    attrs.append(
        _attr("safe-zone", _format_mm(spec.safe_zone) if spec.safe_zone else None)
    )
    attrs.append(_attr("dpi", spec.dpi))
    attrs.append(_attr("flatten", spec.flatten))
    attrs.append(_attr("color-profile", spec.color_profile))
    attrs.append(_attr("cvd", spec.cvd if spec.cvd != "none" else None))
    # Always on: a QGIS plugin has no embedded browser, so the only way it can
    # drive print/export is the URL-trigger surface, which is opt-in by design
    # (docs/cartograph.md "Host triggers").
    attrs.append(_attr("allow-url-actions", True))

    body = "".join(_render_item(item, markup_by_frame) for item in spec.items)
    if spec.atlas is not None:
        body += _render_atlas(spec.atlas)

    return f"<om-cartograph{''.join(attrs)}>{body}</om-cartograph>"


def _render_item(item: CartographItem, markup_by_frame: Mapping[str, str]) -> str:
    if isinstance(item, FrameSpec):
        return _render_frame(item, markup_by_frame)
    if isinstance(item, GraticuleSpec):
        return _render_graticule(item)
    if isinstance(item, LegendSpec):
        return _render_legend(item)
    if isinstance(item, ScalebarSpec):
        return _render_scalebar(item)
    if isinstance(item, NorthSpec):
        return _render_north(item)
    if isinstance(item, TextSpec):
        return _render_text(item)
    if isinstance(item, ImageSpec):
        return _render_image(item)
    if isinstance(item, ShapeSpec):
        return _render_shape(item)
    kind_name = type(item).__name__  # pragma: no cover
    raise TypeError(f"unknown cartograph item type: {kind_name}")  # pragma: no cover


def _render_frame(item: FrameSpec, markup_by_frame: Mapping[str, str]) -> str:
    placement = _placement_attrs(
        item.id,
        item.box,
        rotation=item.rotation,
        z=item.z,
        locked=item.locked,
        hidden=item.hidden,
        opacity=item.opacity,
    )
    attrs = [placement]
    if item.mode == "static":
        attrs.append(_attr("mode", "static"))
    # mode="live" is the runtime's own default; omitted rather than written.

    if item.mode == "live":
        attrs.append(_attr("src", item.src))
        if item.center is not None:
            attrs.append(_attr("center", _lonlat_json(item.center)))
        if item.zoom is not None:
            attrs.append(_attr("zoom", _format_num(item.zoom)))
        if item.bearing:
            attrs.append(_attr("bearing", _format_num(item.bearing)))
        if item.pitch:
            attrs.append(_attr("pitch", _format_num(item.pitch)))
    else:
        if item.corners is None:  # enforced by FrameSpec.__post_init__
            raise ValueError(f"static frame {item.id!r} has no corners")
        attrs.append(_attr("crs", item.crs))
        attrs.append(_attr("crs-def", item.crs_def))
        attrs.append(_attr("corners", _corners_json(item.corners)))

    if item.dpi is not None:
        attrs.append(_attr("dpi", _format_num(item.dpi)))
    attrs.append(_attr("overview-of", item.overview_of))
    attrs.append(_attr("overview-stroke", item.overview_stroke))
    attrs.append(_attr("frame-stroke", item.frame_stroke))
    attrs.append(_attr("frame-bg", item.frame_bg))
    attrs.append(_attr("attribution", item.attribution))
    attrs.append(_attr("atlas-fit", item.atlas_fit))
    if item.atlas_margin is not None:
        attrs.append(_attr("atlas-margin", _format_num(item.atlas_margin)))
    if item.atlas_cameras:
        attrs.append(_attr(ATLAS_CAMERAS_ATTRIBUTE, _atlas_cameras_json(item)))
    if item.georeference is not None:
        attrs.append(
            _attr(GEOREFERENCE_ATTRIBUTE, _georeference_json(item.georeference))
        )

    child = ""
    if item.mode == "live":
        child = markup_by_frame.get(item.id, "")
    else:
        child = _render_static_image(item)

    return f"<om-frame{''.join(attrs)}>{child}</om-frame>"


def _render_static_image(item: FrameSpec) -> str:
    if item.image_bytes is not None:
        src = "data:image/png;base64," + base64.b64encode(item.image_bytes).decode(
            "ascii"
        )
    elif item.image_path is not None:
        src = item.image_path
    else:  # enforced by FrameSpec.__post_init__
        raise ValueError(f"static frame {item.id!r} has no image")
    # Self-closed: `<img>` is a void element in HTML, but the self-closing
    # slash is harmless there and keeps this markup parseable as plain XML.
    return f'<img src="{html.escape(src, quote=True)}" alt=""/>'


def _render_graticule(item: GraticuleSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("for", item.for_),
        _attr("crs", item.crs),
        _attr("crs-def", item.crs_def),
    ]
    if item.interval is not None:
        attrs.append(_attr("interval", _format_num(item.interval)))
    if item.interval_x is not None:
        attrs.append(_attr("interval-x", _format_num(item.interval_x)))
    if item.interval_y is not None:
        attrs.append(_attr("interval-y", _format_num(item.interval_y)))
    if item.kind != "solid":
        attrs.append(_attr("kind", item.kind))
    if item.labels != "outside":
        attrs.append(_attr("labels", item.labels))
    attrs.append(_attr("format", item.format))
    attrs.append(_attr("color", item.color))
    return f"<om-graticule{''.join(attrs)}></om-graticule>"


def _render_legend(item: LegendSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("for", item.for_),
        _attr("title", item.title),
    ]
    if item.columns is not None:
        attrs.append(_attr("columns", item.columns))
    if not item.derived:
        # Default is derived=true; only written to turn it off, per the
        # runtime's own vocabulary note (spike-findings.md #1).
        attrs.append(_attr("derived", "false"))
    attrs.append(_attr("bg", item.bg))
    attrs.append(_attr("color", item.color))
    attrs.append(_attr("stroke", item.stroke))
    if item.font_size is not None:
        attrs.append(_attr("font-size", _format_num(item.font_size)))
    if item.padding is not None:
        attrs.append(_attr("padding", _format_num(item.padding)))

    rows = "".join(_render_legend_row(row) for row in item.rows)
    return f"<om-legend{''.join(attrs)}>{rows}</om-legend>"


def _render_legend_row(row: LegendRowSpec) -> str:
    attrs = [
        _attr("layer", row.layer),
        _attr("match", row.match),
        _attr("label", row.label),
    ]
    if row.hide:
        attrs.append(_attr("hide", True))
    if row.order is not None:
        attrs.append(_attr("order", row.order))
    attrs.append(_attr("color", row.color))
    attrs.append(_attr("shape", row.shape))
    return f"<om-legend-row{''.join(attrs)}></om-legend-row>"


def _render_scalebar(item: ScalebarSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("for", item.for_),
    ]
    if item.units != "metric":
        attrs.append(_attr("units", item.units))
    if item.segments is not None:
        attrs.append(_attr("segments", item.segments))
    if item.segment_length is not None:
        attrs.append(_attr("segment-length", _format_num(item.segment_length)))
    if item.kind != "single":
        attrs.append(_attr("kind", item.kind))
    attrs.append(_attr("color", item.color))
    return f"<om-scalebar{''.join(attrs)}></om-scalebar>"


def _render_north(item: NorthSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("for", item.for_),
    ]
    if item.kind != "arrow":
        attrs.append(_attr("kind", item.kind))
    attrs.append(_attr("color", item.color))
    return f"<om-north{''.join(attrs)}></om-north>"


def _render_text(item: TextSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("kind", item.kind),
    ]
    if item.font_size is not None:
        attrs.append(_attr("font-size", _format_num(item.font_size)))
    attrs.append(_attr("weight", item.weight))
    attrs.append(_attr("align", item.align))
    attrs.append(_attr("font", item.font))
    attrs.append(_attr("color", item.color))
    attrs.append(_attr("bg", item.bg))
    # `html.escape` leaves `{` and `}` untouched, so a {{token}} already
    # produced by core/expression_tokens.py passes through unchanged.
    body = html.escape(item.body, quote=False)
    return f"<om-text{''.join(attrs)}>{body}</om-text>"


def _render_image(item: ImageSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
        _attr("src", item.src),
    ]
    if item.fit != "contain":
        attrs.append(_attr("fit", item.fit))
    return f"<om-image{''.join(attrs)}></om-image>"


def _render_shape(item: ShapeSpec) -> str:
    attrs = [
        _placement_attrs(
            item.id,
            item.box,
            rotation=item.rotation,
            z=item.z,
            locked=item.locked,
            hidden=item.hidden,
            opacity=item.opacity,
        ),
    ]
    if item.kind != "rect":
        attrs.append(_attr("kind", item.kind))
    attrs.append(_attr("fill", item.fill))
    if item.fill_opacity is not None:
        attrs.append(_attr("fill-opacity", _format_num(item.fill_opacity)))
    attrs.append(_attr("stroke", item.stroke))
    if item.stroke_width is not None:
        attrs.append(_attr("stroke-width", _format_num(item.stroke_width)))
    if item.radius is not None:
        attrs.append(_attr("radius", _format_num(item.radius)))
    return f"<om-shape{''.join(attrs)}></om-shape>"


def _render_atlas(atlas: AtlasSpec) -> str:
    attrs = [
        _attr("id", atlas.id),
        _attr("for", atlas.for_),
        _attr("layer", atlas.layer),
        _attr("filter", atlas.filter),
        _attr("sort", atlas.sort),
        _attr("filename", atlas.filename),
        _attr("page-name", atlas.page_name),
        _attr("pager", atlas.pager),
        _attr(ATLAS_SOURCE_ATTRIBUTE, _atlas_source_json(atlas)),
    ]
    return f"<om-atlas{''.join(attrs)}></om-atlas>"


def _atlas_source_json(atlas: AtlasSpec) -> str | None:
    """QGIS's own filter and sort, beside what was written from them (see
    `AtlasSpec`). The importer takes the QGIS text back only while
    `written` still matches the attribute, i.e. while nobody has edited it."""
    source: dict[str, object] = {}
    if atlas.filter_source is not None:
        source["filter"] = {"qgis": atlas.filter_source, "written": atlas.filter}
    if atlas.sort_source is not None:
        source["sort"] = {
            "qgis": atlas.sort_source,
            "ascending": atlas.sort_ascending,
            "written": atlas.sort,
        }
    return (
        json.dumps(source, ensure_ascii=False, separators=(",", ":"))
        if source
        else None
    )
