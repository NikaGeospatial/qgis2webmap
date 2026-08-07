# ArcGIS Pro port — research findings

Researched 2026-08-07. Inputs: a coupling audit of this codebase, plus two web
research passes against current Esri documentation. This is the fact base for
the design doc; it makes no decisions.

Version baseline: **ArcGIS Pro 3.7** (released 2026-05-14, patch 3.7.1 on
2026-07-07). 3.6 (2025-11-13) and 3.5 are still in wide use. CIM version string
is `'V3'` for all of Pro 3.x.

---

## 1. What ports, measured

12,385 lines in `nika_onlymap_exporter/`.

| Side | Lines | % | Contents |
|---|---:|---:|---|
| **Downstream — ports verbatim** | 5,581 | 45% | `export_ir`, `extent_math`, `fidelity_report`, `label_points`, `license_policy`, `manifest_builder`, `symbol_atlas`, `exporters/*`, `packaging/*`, `writers/onlymap_writer`, `ui/live_server`, `ui/preview` |
| **Upstream — rewrite against arcpy** | 2,764 | 22% | `project_reader`, `layer_reader`, `renderer_translator`, `labeling_translator`, `popup_translator`, `elevation_translator`, `symbol_rasterizer` |
| **Shell — replaced, not translated** | 4,040 | 33% | `main_dialog` (2,436), `settings`, `runtime_setup`, `plugin`, `processing/*`, `background_job`, `layer_watcher` |

`export_ir.py` is confirmed genuinely QGIS-free — every field is str/float/tuple/
enum/nested-IR, no `Qgs*` type leaks in. The IR boundary holds, which is what
makes the 45% real rather than aspirational.

**Zero third-party dependencies anywhere in the package.** Even PNG work goes
through Qt's `QImage`/`QBuffer`, not Pillow.

### Three files that lie about their coupling

- `elevation_translator.py` imports nothing from QGIS — it duck-types via
  `getattr` to stay importable headless. It is nonetheless the most
  QGIS-specific file in `core/`, reading the `qgis_25d_height` project variable.
  Belongs on the rewrite side despite a clean import block.
- `labeling_translator.py` and `popup_translator.py` import `QgsVectorLayer`
  only under `TYPE_CHECKING`, then call its API throughout the body. An
  import-grep port estimate misclassifies all three as portable.

### Two that are better than they look

- `manifest_builder.py` — the largest file in the tree at 1,423 lines, and 100%
  pure.
- `settings.py` — 529 lines, only ~15 touch QGIS/Qt.

---

## 2. The platform

### `.pyt` is still the answer

Esri's own comparison page has not been superseded. `.atbx` is the current
*binary toolbox container* (successor to `.tbx`) for wizard-built script tools,
not a competitor to `.pyt`. Esri's guidance is audience-based and unchanged
since 10.1: experienced Python developers → `.pyt`.

Skeleton: `Toolbox` class with `self.tools = [...]`, then per-tool
`getParameterInfo` / `isLicensed` / `updateParameters` / `updateMessages` /
`execute` / `postExecute`. Tool and toolbox names must be alphanumeric only.

**Multi-module layout.** Import resolution: script folder → toolbox folder →
`PYTHONPATH` → `PATH`. For a package beside the `.pyt`, the accepted pattern is
`sys.path.append(...)` first — Esri's own KB calls this "a very non-Pythonic
hack" and offers nothing better.

**The dev-loop gotcha.** Pro auto-refreshes the `.pyt` when its file changes but
does **not** re-import sibling modules. Editing anything behind the shim
requires restarting Pro or a manual `importlib.reload`. For a 12k-line package
behind a thin `.pyt` this is a real, recurring cost.

### The GP form ceiling

Available: `GPValueTable` (flat typed columns, one row per entry),
`GPMultiValue`, `GPComposite`, `GPString` with ValueList filter, `GPBoolean`,
`GPFeatureLayer`, file/folder browse, Range/Field/Workspace filters.
`updateParameters` gives cascading enable/disable and dynamic repopulation;
`updateMessages` gives custom validation. Parameter **categories** produce
collapsible sections (required parameters must stay out of them).

Not available: **no colour picker**, no nested per-row tables, no tabs. The
`controlCLSID` escape hatch only swaps in Esri's own registered controls — it is
not a route to arbitrary custom UI. There is no supported way to attach a
Python-drawn UI to a GP tool.

Ribbon buttons, dockpanes and custom panes remain .NET-only. No 2025 or 2026
UC announcement, and nothing in the 3.5/3.6/3.7 release notes, changes this.
(Negative result — absence of evidence, not a confirmed permanent "never".)

### Distribution

A bare `.pyt` works directly via Catalog's "Add Toolbox" or `arcpy.AddToolbox()`.
No packaging required, no code signing available.

**The trust prompt:** opening a `.pyt` warns that it runs third-party code. 3.6
stopped re-prompting on refresh; 3.7 added a "never ask again for this toolbox"
checkbox. A mis-clicked "don't trust" is recovered via right-click → Refresh
Python Toolbox Access Permissions.

Other channels: `.gpkx` geoprocessing package (built from a *successful run's
history entry* — designed for shipping a completed analysis, an awkward fit for
shipping a general-purpose tool); an add-in wrapper (needs Visual Studio even
when the logic is Python); an ArcGIS Online item (adds a portal-login
dependency).

**There is no plugin-repository analogue.** No Esri-curated catalogue of
community Python toolboxes. Distribution is GitHub releases and word of mouth.

### Environment

Pro 3.6+ ships **Python 3.13.7** in `arcgispro-py3`, with 200+ packages
including **Pillow, requests, numpy 2.2, pandas, scipy**. Earlier Pro versions
pin different Python/numpy versions — the same version-drift trap as
`qgisMinimumVersion`.

The default env is **read-only**. Adding a dependency means the user clones the
environment first — multi-step friction most analysts will not do unprompted.
The alternative is vendoring pure-Python code. Since Pillow and requests are
both already present, this may never bite us.

No documented prohibition on writing to user-writable dirs at runtime, so the
fetch-and-cache runtime story should survive — target `%LOCALAPPDATA%`, never
the Pro install tree. *Unverified: no Esri page explicitly blesses this.*

### Execution

GP tools run on a background thread by default, falling back to foreground when
edits are pending or when invoked from Notebooks / the Python window / an
add-in. `arcpy.SetProgressor` and `AddMessage`/`AddWarning`/`AddError` cover
progress and messaging. Cancellation is a **cooperative poll** of
`arcpy.env.isCancelled` — a tool blocked in a synchronous call never notices.

### Testing — the hard one

**arcpy requires a licensed ArcGIS Pro install and is Windows-only.** There is
no pip-installable arcpy, no container, no GitHub Actions path. (The `arcgis`
package is pip-installable but is a REST wrapper, not a substitute.) Teams
either run a self-hosted licensed Windows runner or — far more commonly — never
run arcpy in CI at all.

Practice is to keep arcpy at the edge and unit-test the arcpy-free core with
`pytest` + mocks. That is exactly this codebase's existing architecture rule
("if it can be pure, make it pure"), which means the 45% downstream keeps its
current test coverage unchanged. The 22% upstream loses CI entirely.

---

## 3. Reading the project

`layer.getDefinition('V3')` returns a native Python object graph via `arcpy.cim`
(not raw JSON). The spec is published and actively maintained at
`github.com/Esri/cim-spec`, currently at 3.7.0.

**The gotcha that will bite:** "The CIM only persists non-default properties."
Freshly-added layers and fields may have no CIM entry at all until touched in
the UI. Field metadata in particular should come from `arcpy.ListFields` /
`da.Describe` rather than trusting `CIMFeatureTable.fieldDescriptions` alone.

Confirmed Python-accessible: renderers (`CIMSimpleRenderer`,
`CIMUniqueValueRenderer`, `CIMClassBreaksRenderer`), all symbol and symbol-layer
classes, `CIMLabelClass` with both Maplex and standard placement,
`CIMFeatureExtrusion`, `CIMPopupInfo`, `CIMFeatureTable`.

### Geometry

True curves are real (circular arc, Bézier, elliptic arc) and `arcpy.Geometry`
objects **flatten them** — curve segments are only visible via `SHAPE@JSON` /
`Geometry.JSON` as `curvePaths`/`curveRings`.

`arcpy.FeaturesToJSON_conversion` has a `geoJSON` option and a **"Project to
WGS84" parameter**, and Esri states curved features are **automatically
densified** during conversion. Always pass the reprojection flag — without it
the tool emits an Esri-style `crs` tag that is not valid GeoJSON.
`__geo_interface__` is not implemented on arcpy geometry; don't reach for it.
*Unverified: multipatch behaviour through this path.*

### Arcade

`expressionEngine` is per-`CIMLabelClass`; values are `Arcade` / `VBScript` /
`JScript` / `Python`. Arcade is the default in Pro 3.x and VBScript is visibly
deprecating (Windows 11 24H2 requires an optional feature for it).

**There is no Python Arcade evaluator.** Not in arcpy, not in the ArcGIS API for
Python, not on PyPI. A fast-path parser must be written from scratch. The
patterns worth handling:

- `$feature.NAME` / `$feature["NAME"]`
- `+` concatenation
- template literals `` `${$feature.FIELD}` ``
- `Concatenate([...], sep)`
- `IIf(cond, a, b)`, including nested
- `DomainName($feature, 'field')`
- `Text(value, format)`

Anything past that degrades to "unsupported, export raw field".

### Renderers with no analogue, and how to degrade

| Renderer | Degrade |
|---|---|
| `CIMDictionaryRenderer` | Pre-resolve each unique attribute combination against the `.stylx` into a concrete symbol, then treat as unique-value |
| `CIMHeatMapRenderer` | Client-side heatmap layer fed the same weights |
| `CIMDotDensityRenderer` | Precompute dot positions at export time, emit as a static point layer |
| `CIMChartRenderer` | Rasterise each unique chart to a sprite, or drop to popup-only |
| `CIMProportionalRenderer` | Directly portable — same mechanism as a size visual variable |
| `CIMSubtypeGroupLayer` | Flatten to N feature layers plus a discriminator field |
| Visual variables (colour/size/rotation/transparency) | Map to deck.gl accessors when field-driven; evaluate in Python at export time when expression-driven |

### Layer types

Cursor-readable and exportable: simple feature layers, feature service layers
(needs connectivity, snapshot semantics), joined/related layers (cursors honour
joins and definition queries), query layers (needs the DB connection),
annotation (via the `ANNO@` token, giving a CIM text graphic), subtype group
layers (per sub-layer).

Must be flagged unsupported: map image layers, vector tile layers, scene layers
without an associated feature layer, mosaic/raster, group layers (recurse only).
*Unverified: dimension layers.*

---

## 4. The marker atlas — every route

This is the crux. `symbol_rasterizer.py` calls `QgsSymbol.asImage()` and lets
QGIS draw its own markers; there is no `asImage()` in arcpy. Esri's own
community Idea "ArcGIS Pro: Export Symbol to Image" is still open, and the
workaround discussed in that thread is screenshotting the preview window —
which confirms nothing simpler than the routes below exists.

| # | Route | Verdict |
|---|---|---|
| A | **SymbolServer REST** (`generateImage`) | Real, but requires a licensed ArcGIS Enterprise federated with Portal, and only accepts *named symbols from a published web style* — not arbitrary project CIM. Rules itself out. |
| B | **arcpy.mp legend/layout export** | Build a throwaway layout with a legend scoped to one class, `exportToPNG`, crop. Works; slow, fragile across symbol types, needs a Pro session. |
| C | **Pro SDK `StyleItem.PreviewImage` / `SymbolFactory`** | First-party, correct, `PatchHeight`/`PatchWidth` control. **.NET only.** Usable as a subprocess bridge — reintroduces the dependency the port exists to avoid. |
| D | **ArcGIS API for Python `SymbolService`** | Thin wrapper over route A. Same Enterprise dependency. |
| E | **`.stylx` = SQLite** | Confirmed: `ITEMS` table, `content` column holds symbol JSON, readable with plain `sqlite3`. **No evidence it caches rasterised previews** — treat "stylx has thumbnails" as likely false pending direct schema inspection. Gets us JSON we already have; does not solve rasterisation. |
| F | **Render `CIMCharacterMarker` from the Esri fonts** | Esri marker fonts ship with Pro and register in `C:\Windows\Fonts`. Given `fontFamilyName` + `characterIndex` + colours, Pillow's `ImageFont` (or `freetype-py` for true outlines) rasterises the glyph offline. Solves this symbol type cleanly. **Check font redistribution licensing.** |
| G | **Headless ArcGIS JS SDK** | `esri/symbols/CIMSymbol` in the Maps SDK for JavaScript is a genuine working CIM rasteriser — Esri's own `cim-symbol-builder-js` demonstrates it. Drivable via Puppeteer/Playwright against offscreen canvas. Costs a headless Chromium, a JS SDK key, and WebGL. |
| H | **Hand-rolled CIM rasteriser in Python** | `CIMSolidFill`, `CIMSolidStroke`, `CIMPictureMarker` (base64 embedded, free), `CIMHatchFill`, simple `CIMVectorMarker` are geometrically simple enough to draw with Pillow straight from the published spec. Pure Python, offline, no server. Fidelity ceiling on complex multi-layer markers with effects. |

**No pure-Python CIM rasteriser exists on GitHub.** Searches for "cim renderer",
"stylx parser", "arcgis symbol to svg" surfaced only Esri's JS tooling and
unrelated projects.

The plausible stack is **H + F**, with picture markers free, falling back to
plain shapes for what neither covers — and C or G held in reserve if fidelity
proves insufficient. Note that Pillow being preinstalled removes what would
otherwise have been a second problem: we currently have no imaging dependency at
all because Qt supplied it.

---

## 5. Live preview

Confirmed: **arcpy has no event, callback or observer system** for map, layer or
project changes. `Metadata.reload()` moves *toward* the saved-on-disk state and
explicitly discards unsaved changes.

The harder constraint: `arcpy.mp.ArcGISProject("CURRENT")` is documented to work
**only from inside the application**. An external headless Python process cannot
obtain a `CURRENT` handle at all. So the live-reload preview cannot run as a
sidecar process watching the project — the polling loop must live inside the Pro
process.

`CURRENT` is a live in-memory handle, so changes Pro has applied to its own
object model (a renderer edit through the UI) should be visible without a save.
*Unverified and worth testing directly: whether uncommitted edit-session feature
geometry edits are also visible.*

---

### Can the preview live inside Pro?

Asked because both QGIS blockers were environment-specific (nixpkgs pre-disabling
Chromium's GPU stack via `QTWEBENGINE_CHROMIUM_FLAGS`, and `pyqt6-webengine`
missing from the closure) and neither transfers to Windows. See
[[embedded-webgl-preview-not-viable]].

| Surface | Renders HTML | WebGL | Verdict |
|---|---|---|---|
| .NET add-in dockpane + WebView2 | Yes — official Esri sample | Undocumented either way | Only credible candidate; must be spiked |
| Pro's notebook pane | Partially | Documented broken | Dead |
| GP Results HTML viewer | Static GP reports only | No evidence | Wrong tool |
| Catalog / metadata / Pop-up panes | Sanitised only | No | Dead |
| `arcpy.mp` layout elements | N/A — print canvas | No | Dead |

**Pro moved off CEF to WebView2 at 2.9**, and CEF is unsupported from 3.x. Esri
ships a `WebViewBrowser` sample in `Esri/arcgis-pro-sdk-community-samples`
(`Framework/WebViewBrowser/`) showing a dockpane hosting an Edge WebView2
control — targets `net10.0-windows`, needs Pro 3.7 SDK and Visual Studio 2026.
Pro pins its own WebView2 runtime in `Pro\bin\WebView` rather than tracking
Evergreen.

Nobody has published whether WebGL survives inside a Pro-hosted WebView2. That
is the load-bearing gap — not contrary evidence, just no evidence. Generic
WebView2-in-host-app GPU problems (software-rendering fallback, corruption) are
documented on Microsoft's tracker, and Pro is DirectX-heavy, so contention is a
real risk class. A five-minute add-in loading a deck.gl demo settles it.

Two secondary findings:

- The notebook pane is disqualified by evidence, not absence of it: `arcgis`
  MapView (WebGL) is reported blank in Pro 3.2 and there is an Esri bug record
  "Map Viewer is unavailable in ArcGIS Pro Notebooks" (000136038). `ipywidgets.Output`
  is also broken there (Esri/arcgis-python-api#779).
- **WebView2's `SetVirtualHostNameToFolderMapping`** serves a local folder as
  `https://appassets/...`, giving normal same-origin semantics and predictable
  CSP — better than raw `file://` for our `unsafe-eval` requirement, and it
  removes the need for a local HTTP server for the *rendering* half. See
  [[csp-unsafe-eval-breaks-the-map]].

There is **no documented Python↔add-in bridge.** Esri community threads cover
*packaging* a `.pyt` inside an add-in and calling tools via `ExecuteToolAsync`,
not talking to a live dockpane. A bridge would be ours to invent: file-watching,
a local endpoint, or named pipes.

## 6. Settings persistence

`QgsProject.writeEntry`/`readEntry` has a partial analogue:
**`CIMBaseLayer.CustomProperties`** — a documented array of string key/value
pairs, "developers are fully responsible for stored content", read/written via
`getDefinition`/`setDefinition` and persisted in the `.aprx`. Per-layer settings
map onto it directly (JSON-serialise into a value for the per-field mode dict).

For the ~21 map-wide settings there is no confirmed equivalent. `aprx.metadata`
is Dublin-Core-shaped only. `CIMMap.customProperties` is *pattern-inferred, not
source-confirmed* — verify against the SDK reference before designing around it.
Fallbacks: a sidecar JSON next to the `.aprx`, or a table in the default
geodatabase.

Pro's Geoprocessing History logs every run's parameters per project, but there
is no arcpy API to read it back, and it is a run log rather than app config.

*Unverified: `aprx.filePath` behaviour on a new, never-saved project.*

---

## 7. Corrections to the 2026-08-06 scoping

- **Pillow and requests ship with Pro.** The imaging half of the marker problem
  is free; only symbol rendering remains. Earlier scoping treated the whole
  raster path as a loss.
- **The ArcGIS JS SDK is a working CIM rasteriser** (route G). This was not on
  the earlier list of three routes.
- **The Esri marker fonts are self-renderable** (route F) — a fourth route,
  and the cleanest one for character markers specifically.
- **Live preview is worse than "polling instead of signals".** It is
  "cannot run outside the Pro process at all".
- **CI is not degraded, it is absent** for anything touching arcpy.
- **`.gpkx` is not a general distribution format** — it packages a completed run.

---

## 8. Must be verified on a Windows box with Pro

Nothing below is answerable from documentation.

1. Does a background thread / HTTP server started in `execute()` survive after
   the tool reports Succeeded? (Blocks the whole preview architecture.)
2. `.stylx` `ITEMS` schema — is there a preview-image blob column?
3. Does `CURRENT` see uncommitted edit-session geometry edits, or only
   symbology/UI state?
4. Does `CIMMap` have a `customProperties` bag that round-trips a save?
5. `aprx.filePath` on an unsaved project.
6. `webbrowser.open()` from inside a GP tool — the only source warning against
   it is a 2013 ArcMap-era post, likely stale, but untested.
7. Multipatch through `FeaturesToJSON`'s GeoJSON path.
8. Dimension-layer cursor support.
