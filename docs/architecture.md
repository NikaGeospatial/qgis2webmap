# Architecture

The one rule that shapes everything: **QGIS interpretation, manifest generation,
and packaging must stay separate.**

qgis2web fused them, and the cost is measurable — 7,481 lines of renderer-specific
JavaScript-string generation across two parallel families (Leaflet and
OpenLayers), against roughly 1,700 lines of renderer-neutral core. Every symbology
fix has to be made twice, and where it wasn't, output diverged: stroke width
`0.988` survives in its OpenLayers path and rounds to `1.0` in its Leaflet path.

We avoid that with a normalized model in the middle and exactly one renderer.

```
PyQGIS objects
    │
    ▼  core/project_reader.py, core/layer_reader.py
ExportProject / ExportLayer / RendererSpec / PopupSpec / AssetDependency
    │
    ▼  core/fidelity_report.py
FidelityReport          ← every unsupported or approximated property, named
    │
    ▼  writers/onlymap_writer.py, core/manifest_builder.py
OnlyMap manifest        ← declarative <om-map>/<om-layer>/<om-widget> markup
    │
    ▼  packaging/artifact_builder.py, exporters/*
Standalone HTML  |  Share ZIP  |  Folder  |  Hosted
```

One place to test QGIS semantics, one place to build markup, one place to package.

## Layer responsibilities

| Package | Owns | Must not |
|---|---|---|
| `core/` | Reading the QGIS project into plain dataclasses; classifying fidelity | Touch Qt widgets, produce markup, or write files |
| `writers/` | Normalized model → OnlyMap manifest markup | Read `Qgs*` objects or decide where files go |
| `exporters/` | Destination — where an already-built artifact lands | Re-interpret the project |
| `packaging/` | Embedding assets, scanning dependencies, runtime bytes | Know about QGIS |
| `ui/` | The dialog | Contain export logic |
| `processing/` | Processing algorithms, calling the same services as the dialog | Be a second export implementation |

`core/export_ir.py` is the boundary: it holds no `QWidget` and, after project
reading completes, minimises live `Qgs*` references.

### Pure versus QGIS-dependent modules

`core/` splits again along a line worth stating, because it determines what CI
can test. PyQGIS is unavailable on a GitHub runner, so anything importing `qgis`
can only be exercised in `tests/qgis/`. Everything else runs in `tests/unit/` on
every push.

| Pure — testable in CI | Imports PyQGIS — `tests/qgis/` only |
|---|---|
| `export_ir.py` — the dataclasses | `project_reader.py` |
| `extent_math.py` — extent + antimeridian | `layer_reader.py` |
| `fidelity_report.py` — the accumulator | `renderer_translator.py` |
| `license_policy.py` — cap detection and policies | `labeling_translator.py`, `popup_translator.py` |

Two of these are not in issue #29's file list and are deliberate additions:

- **`extent_math.py`** exists so the antimeridian logic — the fix for the
  incumbent's worst first impression — is unit-tested without QGIS. Folding it
  into `project_reader.py` would have made it untestable in CI, which is exactly
  the wrong place for maths that is easy to get subtly wrong.
- **`license_policy.py`** separates cap *detection* (unconditional, feeds the
  Fidelity tab) from *enforcement* (swappable), because whether exports carry a
  licence key is still an open product decision.

The rule for new code: if it can be pure, make it pure. Push PyQGIS access to the
edge so the logic underneath stays testable.

## Why declarative markup, not generated JavaScript

OnlyMapJS is driven by HTML custom elements — `<om-map>`, `<om-layer>`,
`<om-widget>`, `<om-overlay>`, `<om-behavior>`, `<om-fallback>`. So the writer
emits *attributes*, not code. Consequences:

- The 7,481-line problem has **no equivalent** in this design. There is nothing to
  duplicate because there is one renderer and no code generation.
- The artifact is **self-describing and editable** — which is what makes the
  "enhance with AI" path tractable. An agent edits HTML attributes rather than
  reverse-engineering minified output.
- The attribute surface is machine-readable: `onlymapjs.html-data.json` ships in
  the OnlyMap package and enumerates every element and attribute. The manifest
  builder should be written against it, and ideally validated against it in CI, so
  an upstream rename fails our build instead of silently producing a broken map.

**Do not create per-renderer string-generation modules.** If a change feels like
it needs one, the normalized model is missing a field.

## Why the output is an HTML file

The goal is an artifact a recipient can open with **zero setup** — no install, no
extraction, no server, no account. Working backwards from that, the browser is the
only runtime already present on every machine that can render an interactive
WebGL map, and a single `.html` file is the only thing a browser opens by
double-click from a local disk.

Alternatives, and why each fails:

| Option | Why not |
|---|---|
| `.html.gz` | Browsers do not transparently decompress a **local** file. Double-click fails |
| `.mhtml` / `.webarchive` | Chromium-only in practice — Firefox dropped MHTML. Loses the universality that is the entire point |
| `.zip` | Requires extraction. Correct for the Share ZIP tier, wrong as the default — and it is precisely the incumbent's failure mode |
| Custom extension (`.ommap`) + viewer | Requires installing a viewer. That *is* the problem we are solving |
| Electron / Tauri desktop app | Produces a double-clickable binary, but: per-platform builds, 50 MB+, code signing, antivirus false positives, and a recipient must trust an executable. #29 forbids it in the standalone path, correctly |
| Self-extracting archive | Same trust and platform problems as a binary |
| PDF / GeoPDF | No WebGL. Layer toggling at best; not an interactive map |
| SVG | No tiles, no WebGL, no data binding at scale |
| PWA | Needs a server and a service worker. Not a file |
| Notebook (Jupyter / Observable) | Needs a runtime the recipient does not have |

So `.html` is not a compromise — it is the only format that satisfies the
constraint. The differentiation is **what the file contains**: the whole runtime
and all data inline, an `<om-fallback>` for previewers that run no JavaScript, and
a manifest that is readable, declarative markup rather than generated JavaScript —
which is what makes later AI-assisted editing tractable.

### Honest limitations of the format

Recorded so they are designed around rather than discovered late:

- **`file://` is a restricted origin.** No `fetch` of sibling files, no service
  workers, no CORS-dependent features. This is *why* data must be inlined rather
  than loaded — it is the same constraint that forces qgis2web to write GeoJSON
  into `.js` files instead of `.geojson`.
- **Parse cost is real.** Multi-MB of inline JavaScript takes measurable time to
  parse before first paint. It argues for the smallest viable runtime build.
- **No transit compression.** A file copied to a USB stick or attached to an email
  is not gzipped by any server, which is why compression has to happen *inside*
  the artifact.
- **Mail systems are suspicious of HTML attachments.** Some providers and many
  corporate filters quarantine or strip `.html` attachments, because HTML is a
  phishing vector. This is a genuine deliverability risk for the "email someone a
  map" story, and it is an argument for offering the **Share ZIP tier even when
  size does not require it** — a zipped artifact passes filters that a bare
  `.html` does not.
- **Previewers may run no JavaScript.** iOS QuickLook renders HTML attachments
  with scripts disabled. Hence `<om-fallback>` on every export, which the
  OnlyMap stylesheet gates with a pure-CSS `om-map:not(:defined)` rule — and why
  the CSS must be inlined raw rather than inflated by script.
- **WebGL is required and does not degrade.** Old hardware, software rasterisers
  and some locked-down remote-desktop environments will render nothing.
  `<om-fallback>` covers the no-JavaScript case but not the no-WebGL case.

## Symbology fidelity

The renderer choice is settled and defensible on its own: the artifact is built
from `<om-map>` / `<om-layer>` custom elements, so only OnlyMapJS renders it, and
it brings 3D/WebGL capability that Leaflet and OpenLayers do not. Nothing needs to
gate users into it.

What a QGIS user actually judges is whether their map still looks like their map.
That is where the incumbent is weakest, and it is where our effort goes:

- **Nothing is silently swapped.** qgis2web turns every QGIS simple-marker shape
  into a circle (upstream #1218, open since June 2026). We capture the shape and,
  where the renderer cannot draw it, say so in the fidelity report.
- **One renderer means no divergence.** Upstream #1095 has labels working in
  OpenLayers and broken in Leaflet - the direct consequence of two code paths. We
  have one, so that class of bug cannot occur.
- **Unsupported is a named outcome.** An untranslatable renderer is reported with
  its class name, not defaulted quietly (upstream #1041).

The fidelity report is the mechanism, and upstream has no equivalent.

## Non-negotiables

These exist because the alternative was observed to fail in the incumbent:

1. **Never terminate QGIS.** No `os._exit`, `sys.exit`, or `QApplication.quit` —
   enforced by CI.
2. **No dependency installers.** Never shell out to a package manager — enforced
   by CI.
3. **Exports make no network requests.** `telemetry="off"`, no `map-id`, and no
   remote basemap unless explicitly chosen.
4. **Lossless by default.** Gzip shrinks bytes without changing data. Coordinate
   quantisation and geometry simplification are opt-in and reported.
5. **Never write a knowingly broken artifact.** Over a licence cap, missing a
   required asset, or unable to embed — fail with an actionable message.
6. **One writer serves preview and export.** Preview cannot drift from output if
   there is one code path producing both.
7. **Refresh must not mutate settings.** Derive the layer list from the project;
   do not cache a widget tree that then needs refreshing.

## Background

- [`onlymap-js` issue #29](https://github.com/NikaGeospatial/onlymap-js/issues/29)
  — product scope, naming, layout, and the 0.1.0 task plan.
- `qgis2web-evaluation/` — measured evaluation of the incumbent: what to copy,
  what not to reproduce, and the defects to avoid.
- `onlymap-qgis-readiness/` — OnlyMapJS runtime assessment: bundle sizes,
  packaging budgets, licence and telemetry constraints.
