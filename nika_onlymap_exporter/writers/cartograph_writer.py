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

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import html
import json
from collections.abc import Mapping
from dataclasses import dataclass

from ..core.cartograph_ir import (
    AtlasSpec,
    Box,
    CartographItem,
    CartographSpec,
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

#: One template id per document; there is only ever one sheet per page.
_TEMPLATE_ID = "sheet"


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
) -> str:
    """Render `spec` into a complete, standalone HTML document string."""
    markup_by_frame = live_frame_markup or {}
    has_live = any(
        isinstance(item, FrameSpec) and item.mode == "live" for item in spec.items
    )
    if has_live and (runtime.core_js is None or runtime.core_css is None):
        raise ValueError(
            "a cartograph with a live frame needs core_js and core_css in the "
            "runtime bundle"
        )

    sheet = _render_sheet(spec, markup_by_frame)
    css = runtime.cartograph_css
    if has_live:
        # mypy cannot see the guard above narrowed core_css to `str`.
        css = f"{css}\n{runtime.core_css or ''}"

    title = html.escape(spec.title or spec.cartograph_id, quote=True)
    script = _render_script(runtime, has_live=has_live)

    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        f"<style>{css}</style>\n"
        "</head>\n"
        '<body style="margin:0">\n'
        f'<template id="{_TEMPLATE_ID}">{sheet}</template>\n'
        f'<script type="module">{script}</script>\n'
        "</body>\n"
        "</html>\n"
    )


# --------------------------------------------------------------------------
# The bootstrap script
# --------------------------------------------------------------------------


def _render_script(runtime: CartographRuntimeBundle, *, has_live: bool) -> str:
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
        # Cloned from the <template> -- still inert, so touching it here
        # (even defining custom elements already happened above) triggers no
        # upgrade: a DocumentFragment cloned from template content stays
        # disconnected until it is actually appended to document.body below.
        f'const template = document.getElementById("{_TEMPLATE_ID}");',
        "const fragment = template.content.cloneNode(true);",
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
    lines.append("document.body.appendChild(fragment);")
    # The runtime installs `?print=1` / `?export=png` / `?cvd=` when it is
    # imported, which is before the sheet leaves its <template>, so it finds
    # no page to act on. Installing again now that the page is connected is
    # what makes the Share menu's Export PDF/PNG work at all.
    lines.append("carto.installUrlActions();")
    lines += _ATLAS_ACTIONS
    indent = "\n      "
    return indent + indent.join(lines) + "\n    "


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
    "  runAtlasAction(atlasAction).catch((error) =>",
    "    console.warn(`[qgis2webmap] atlas ${atlasAction} failed: ${error.message}`));",
    "}",
    "async function runAtlasAction(kind) {",
    "  await carto.whenPageReady(sheet);",
    "  const atlas = sheet.atlas;",
    "  if (!atlas) throw new Error('this cartograph has no atlas');",
    "  atlas.refresh?.();",
    "  const query = new URLSearchParams(location.search);",
    "  const stem = sheet.getAttribute('cartograph-id') || 'cartograph';",
    "  if (kind === 'zip') {",
    "    const dpi = Number(query.get('dpi')) || 300;",
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
    # the paper, so Chromium prints every sheet at true size.
    "  const dpi = Number(query.get('dpi')) || 200;",
    "  const widthMm = sheet.offsetWidth * 25.4 / 96;",
    "  const heightMm = sheet.offsetHeight * 25.4 / 96;",
    "  const sheets = document.createElement('div');",
    "  sheets.setAttribute('data-qgis2webmap-atlas-print', '');",
    "  for (let index = 0; index < atlas.count; index++) {",
    "    const page = await carto.renderCartographAtlas(sheet, { dpi, page: index });",
    "    const image = new Image();",
    "    image.src = URL.createObjectURL(page.blob);",
    "    image.style.cssText = `display:block;width:${widthMm}mm;`",
    "      + `height:${heightMm}mm;break-after:page;`;",
    "    await image.decode();",
    "    sheets.appendChild(image);",
    "  }",
    "  const style = document.createElement('style');",
    "  const printed = '[data-qgis2webmap-atlas-print]';",
    "  style.textContent = `@media screen { ${printed} { display: none } }`",
    "    + ' @media print { om-cartograph { display: none !important }'",
    "    + ` ${printed} { display: block !important } }`;",
    "  document.body.append(style, sheets);",
    "  const restore = () => {",
    "    for (const image of sheets.querySelectorAll('img')) {",
    "      URL.revokeObjectURL(image.src);",
    "    }",
    "    sheets.remove();",
    "    style.remove();",
    "  };",
    "  window.addEventListener('afterprint', restore, { once: true });",
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


def _style(box: Box) -> str:
    return (
        f"left:{_format_mm(box.x)}mm;"
        f"top:{_format_mm(box.y)}mm;"
        f"width:{_format_mm(box.w)}mm;"
        f"height:{_format_mm(box.h)}mm"
    )


def _placement_attrs(
    item_id: str,
    box: Box,
    *,
    rotation: float = 0.0,
    z: int | None = None,
    locked: bool = False,
    hidden: bool = False,
) -> str:
    """The attributes every placed element shares: `id x y w h` plus the
    optional `rotation z locked hidden`, and the matching inline `style`."""
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
    parts.append(_attr("style", _style(box)))
    return "".join(parts)


def _lonlat_json(point: tuple[float, float]) -> str:
    return json.dumps([point[0], point[1]])


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
    ]
    return f"<om-atlas{''.join(attrs)}></om-atlas>"
