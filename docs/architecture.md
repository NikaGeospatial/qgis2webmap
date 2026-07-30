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
