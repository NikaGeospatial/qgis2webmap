# Vector and raster symbology — research findings and backlog

Researched 2026-09-15, revised 2026-09-18. Nothing here is built yet; this is the
menu we choose from, not a plan. The implementation plan for whatever we pick
gets its own document.

**Why this exists.** We wanted to know how much further our symbology support
could go, what the competition actually does, and which gaps are worth closing.
The answer turned out to depend less on the runtime's ceiling than on what our
own translator declines to read.

---

## 0. Freshness — read this before trusting anything below

| Fact | Value | Checked |
|---|---|---|
| Runtime pin | **0.8.1** | 2026-09-18 |
| Vector symbology ceiling | measured against 0.7.6, **unchanged in 0.8.1** | 2026-09-18 |
| Attribute contract diff 0.7.6 → 0.8.1 | **no additions, no removals, on any layer type** | 2026-09-18 |
| `scale()` kinds | identical across both | 2026-09-18 |

0.8.0 is Cartograph (print layouts, `<om-legend>`, `<om-atlas>`, CVD simulation).
It adds nothing to map symbology, so the 0.7.6 measurements still stand. **Re-run
the attribute diff before acting on this document** — the pin moved twice during
the research itself (0.6.20 → 0.7.6 → 0.8.1).

The method that produced the table below: read `onlymapjs.attributes.json` and
the built bundle out of the npm tarball. Per
`raster-support-requires-onlymap-0-7`, **the schema declaring an attribute is not
evidence the runtime honours it** — `COGLayer` existed from 0.3.1 and did not
work until 0.7.0. Where it matters, verify in the browser tier.

---

## 1. The runtime ceiling (verified from the 0.8.1 bundle)

### Expression grammar — the thing everything else hangs off

- `$field` reads, arithmetic, **ternaries** (`ConditionalExpression` compiles)
- `scale($f, KIND, [range], domain=[...], exponent=N)`
- **KIND is exactly**: `sequential`, `diverging`, `threshold`, `linear`, `sqrt`,
  `log`, `pow`. There is no `match`, no `case`, no `ordinal`.
- Colours inside accessors must be quoted strings or RGBA arrays. Bare hex is a
  parse error in an accessor, though fine in a plain attribute.

**Consequences worth internalising:**

- A **categorized** renderer becomes a ternary chain
  `$f=='a' ? c1 : $f=='b' ? c2 : cDefault`.
- A **graduated** renderer becomes `scale($f, threshold, [colours], domain=[breaks])`.
- The runtime's legend engine **pattern-matches the expression's shape** to build
  a legend: `threshold` → class ranges, `sequential`/`diverging` → gradient ramp,
  ternary chain → categories, literal → single swatch. Anything else falls back
  to one swatch. So emitting the *canonical* form buys a correct legend free, and
  a clever-but-unusual equivalent silently costs us the legend.
- Since 0.7.3 a `scale()` whose kind and range disagree is a **validation error**
  rather than an invisible layer. Good for us, but it means we must never emit an
  invalid one.

### Available, and we do not currently use all of it

`classify-by` / `-scale` (quantile, equal-interval, jenks) / `-classes` / `-ramp`
(14 named), `dash` + `dash-justified`, `visible-zoom-range`, `opacity`,
`filter-*`, per-geometry accessors (`get-fill-color`, `get-line-color`,
`get-line-width`, `get-point-radius`, `get-icon-*`, `get-text-*`, `get-elevation`),
units and min/max pixel clamps on every size, `point-type` (circle | icon | text,
combinable), icon atlas + mapping, full `TextLayer` label surface including
`anchor="surface"`, extrusion, and aggregation layers (Heatmap, Hexagon, Grid,
ScreenGrid, H3, Contour, A5, Geohash, S2, Quadkey).

Escape hatches: `parameters` (raw WebGL blend state), `operation`, `extensions`.

### Not in the runtime at all — the hard ceiling

Pattern fills (SVG / line-pattern / point-pattern / hatch), gradient and
shapeburst fills, marker-lines and arrowheads, multi-symbol-layer stacking inside
one layer, symbol levels, true blend modes, paint effects (shadow / blur / glow),
data-defined dash.

### Raster (`COGLayer`)

`bands` (single, or an `[r,g,b]` triple for false colour), `colormap` (14 named) +
`reverse`, `min`/`max` (scalar or per-band triple), `rescale="auto"` from GDAL
statistics, `stretch` (linear | log | sqrt), `gamma`, `nodata`, `identify`
(click reads the pixel). Paletted GeoTIFFs render through their embedded colour
table with an automatic classes legend.

**COG only.** A plain GeoTIFF, ECW, JP2 or ASC must be converted or fall back to
`BitmapLayer`. No hillshade renderer, no per-value transparency list, no
brightness/contrast/saturation.

---

## 2. What we support today

Audited across branches 2026-09-15. Renderer dispatch is one function,
`renderer_translator.translate_renderer`.

**Renderers:** Single, Categorized, Graduated, 2.5D (special-cased, always
reported as approximated). **Everything else** — rule-based, heatmap, point
cluster, point displacement, inverted polygon, merged feature, null symbol,
embedded symbol — hits one catch-all that warns by class name and then renders
**uniform grey `#888888cc`**.

**Symbols:** `_visible_symbol_layer()` takes the **top-most enabled symbol layer
only**; everything beneath is discarded (reported for lines and polygons).
Property reading is duck-typed through a fixed probe list — anything not on that
list is invisible to the translator.

**Points:** circles native; ~40 marker shapes plus SVG and raster markers
rasterise into a sprite atlas (256 distinct appearances, 4096px sheet, **point
geometry only**).

**Lines:** SimpleLine only. Dash patterns are genuinely good — both custom dash
vectors and the Qt named styles, with PyQt5-int / PyQt6-enum handling.

**Fills:** SimpleFill only. Qt brush hatch styles never read.

**Labels:** a solid subset. Rule-based and expression labels explicitly refused.

**3D:** both places QGIS hides height are read; expression heights refused.

**Raster:** reads the layer, converts to COG, and **bakes QGIS's colours into the
pixels** (`da635b6`, 2026-09-18). `bands` is emitted. `colormap` is deliberately
never emitted — it would sit on top of already-baked colours.

### The silent-drop inventory

These produce **no fidelity item at all**. This is our known bug class
(`silent-attribute-drops-are-the-bug-class`) with a fresh set of instances.

| What | What happens |
|---|---|
| FontMarker, EllipseMarker, VectorField, MaskMarker, circular FilledMarker | all pass the `is_drawable_as_circle` gate → **plain circles, silently** |
| ArrowLine | no `width()` → **zero-width line**; arrowheads gone |
| GradientFill, ShapeburstFill | only `color()` probed → flat first colour |
| LinePatternFill, PointPatternFill, RandomMarkerFill, CentroidFill, RasterFill, HashLine, InterpolatedLine, Lineburst | silently flat |
| Diagrams (pie / histogram) | zero code, zero reporting |
| Data-defined overrides | read **nowhere** except 3D extrusion height |
| Size units (mm / px / points / map units) | `MM_TO_PIXELS` applied unconditionally → silently mis-scaled |
| Blend modes, symbol levels, paint effects | not read anywhere |
| Symbol opacity | read into `SymbolSpec.opacity`, **never emitted** on non-atlas layers |
| Marker offset | read, emitted on atlas layers only |
| Flat vs square line cap | collapsed to a boolean, no note |

### Still-live stale claims

**Scale-dependent visibility is suppressed on both vector and raster**
(`layer_reader.py:658` and `:820`), and the raster one gives the reason as *"the
map runtime this export is pinned to has no per-layer zoom range."* The pin is
0.8.1; `visible-zoom-range` has been universal since **0.6.22**. The matching
comment at `manifest_builder.py:723` says the same and names 0.6.20 explicitly.

`docs/supported-features.md` still publishes `Rasters | Not yet` and
`Scale-dependent visibility | No`.

Dead API: `FidelityStatus.RASTER_FALLBACK` and `raster_fallback()` exist, the
dialog has a "Rasterised" label and sort weight, and the docs advertise the
verdict — **with no call site anywhere.**

`QgsNullSymbolRenderer` is a correctness inversion, not a gap: QGIS draws
nothing, we draw grey polygons.

---

## 3. The competition

### qgis2web 4.3.2 (2026-07-25) — actively maintained, not the stale target

v4.0.0 brought QGIS 4 support in April 2026; v4.3.0 added PBF vector tiles in
July. Maintenance passed from Tom Chadwin to Andrea Ordonselli in Nov 2023; the
repo moved to `qgis2web/qgis2web`.

| | qgis2web | Us |
|---|---|---|
| Marker shapes | Leaflet 4, OL 9; rest → circle | ~40 + SVG + raster via atlas |
| Custom dash vectors | **`customDash` appears nowhere — lost** | full |
| 2.5D | OSMBuildings, a separate library | real extrusion |
| Fidelity reporting | one legend fallback string | full per-layer report |
| **Rule-based** | **yes** | no — uniform grey |
| **Multi-symbol-layer** | **yes, loops all layers, both paths** | top layer only |
| **Heatmap renderer** | yes | no |
| **Blend modes** | yes, layer-level, 12 Qt modes | no |
| Raster | **perfect — QGIS rasterises to PNG** | COG + baked colours |

It translates **five** symbol layer types total, and its only data-defined
property is **rotation**.

### The closest competitor is not qgis2web

**QGIS2VectorTiles** — 8,831 downloads since Nov 2025, hand-written MapLibre
converter (`maplibre_converter.py` is 1,934 lines), flattens rule-based via
`convertFromRenderer()`, handles MarkerLine and pattern fills through sprite
generation. It ships **no popup, identify or interactivity code at all.**

Full symbology *plus* interactivity is the opening.

### The architectural split that sets every ceiling

- **Server-side QGIS rendering** (QGIS Cloud, Lizmap, QWC2, G3W-SUITE): literally
  100% fidelity, because QGIS draws the pixels. Cost: a permanent server, raster
  tiles, no offline file, symbols cut at tile boundaries. **Never compete on
  fidelity here** — compete on deployment cost and client-side interactivity.
- **Client-side MapLibre** (Felt, Mapbox, MapTiler): ceiling is the style spec.
- **Client-side bespoke** (CARTO, Atlas, us): ceiling is the layer library.

**QGIS Server has no vector-tile/MVT service, and `GetStyles` emits only basic
SLD.** So there is no server-side path to client-rendered QGIS symbology. Every
client-side attempt must translate out of band. That is the structural gap we
operate in.

**Nobody in the paid market ships a self-contained double-clickable file.**

### Felt is the benchmark converter, and it is open source

`felt/core/fsl_converter.py` in `github.com/felt/qgis-plugin`. Ahead of us on
rule-based, MarkerLine/HashedLine/Arrow, and gradient/shapeburst/line-pattern/
SVG/point-pattern fills. Behind us on 2.5D (explicitly not converted) and label
offset/placement (dropped). Felt's free tier now forbids uploads entirely and the
QGIS plugin is a paid feature.

### New free competitor

**GeoLibre 1.0** (opengeos / Qiusheng Wu, 2026) — rule-based, natural breaks, 3D
extrusion, clustering, heatmap, **diagram symbology**, blend modes, and
**bidirectional QML ↔ SLD ↔ MapLibre interchange**. Did not exist when our
qgis2web comparison page was written.

### ArcGIS — two places we can be better, not merely equal

- **Area-proportional / Flannery sizing does not exist in the ArcGIS web stack.**
  Flannery is Pro-desktop only, circles only, and Esri's own docs say it does not
  survive publishing. Linear interpolation is all their browser offers. QGIS
  gives us the scale type, and the runtime has `sqrt` and `pow`.
- **Hexbins are not in the ArcGIS JS SDK.** `FeatureReductionBinning` is
  geohash-only; the hexagon bin types exist in the spec but no client implements
  them. We ship `H3HexagonLayer` and `A5Layer` today.

Not worth chasing: their 300+ hand-authored colourblind-tested ramps are the real
moat, and they deprecated their own symbology slider widgets in 5.0 while
shipping a legacy shim because the replacement is unfinished.

### Do not reuse GeoStyler

`geostyler-qgis-parser` v4.1.0 is **worse than qgis2web**: only SimpleMarker and
SvgMarker (FontMarker, Ellipse, Raster, Filled all `throw`, killing the entire
style); **no class switch at all** for lines or fills, so MarkerLine, Arrow,
Gradient and Shapeburst are silently read as plain line/fill; zero expression
support; label halos silently dropped on read. It declares **no
`unsupportedProperties` field**, where its sibling SLD and Mapbox parsers both
publish detailed matrices. Its intermediate model cannot represent extrusion,
clustering or heatmap — our shipped work would not survive the pivot.

Mine **GeoCat `bridgestyle`** for architecture instead: it reads live PyQGIS
renderer objects as we do, and every `convert()` returns `(output, warnings, obj)`.

### Two things to steal rather than build

1. `bridgestyle`'s `(output, warnings, obj)` contract.
2. QGIS 3.44's `QgsSldExportContext::errors()` pattern — before 3.44 QGIS
   silently dropped unsupported renderers; since 3.44 it reports them. Our
   3.44-LTR / QGIS-4 floor means it is available to us.

Both are structural fixes for our silent-drop bug class.

---

## 4. The backlog, in four buckets

### Bucket 1 — build instantly, no unknowns

Correcting things already read but never emitted, or claims that went stale.

- [ ] **Scale-dependent visibility** → `visible-zoom-range`, vector and raster.
      `ScaleRange` is already in the IR and `scale_to_zoom()` is already written.
      Delete two suppressions and two stale comments.
- [ ] **Symbol opacity on non-atlas layers** → fold into the RGBA alpha we
      already emit.
- [ ] **`get-icon-angle`** — rotation is already read and already in the atlas
      appearance key; a code comment promises the attribute; it is never written.
- [ ] **`QgsNullSymbolRenderer`** → stop drawing grey where QGIS draws nothing.
- [ ] **Report the silent drops** — FontMarker, EllipseMarker, VectorField,
      MaskMarker, circular FilledMarker, HashLine, InterpolatedLine, Lineburst,
      RasterFill, flat-vs-square cap, marker offset on the circle path.
      *Reporting* is instant; *fixing* is not, and reporting is the honest first
      move.
- [ ] **`docs/supported-features.md`** — two rows are now false.
- [ ] **Retire or wire up `RASTER_FALLBACK`** — the docs advertise a verdict no
      code emits.

### Bucket 2 — real time, size or open questions

- [ ] **Data-defined overrides.** The lead feature. QGIS's assistant stores a
      `QgsProperty` **plus a transformer**, and the transformers map almost
      one-to-one onto `scale()`:

      | QGIS | OnlyMap |
      |---|---|
      | `QgsSizeScaleTransformer` Linear | `scale($f, linear, [min,max], domain=[...])` |
      | …Area | `scale($f, sqrt, …)` |
      | …Flannery | `scale($f, pow, …, exponent=0.57)` |
      | …Exponential | `scale($f, pow, …, exponent=N)` |
      | `QgsColorRampTransformer` | `scale($f, sequential, [stops], domain=[...])` |

      Three tiers, degrading honestly: transformer-backed → exact `scale()`;
      bare field → `$field`; arbitrary expression → a documented subset
      (`CASE WHEN` → ternary chains, which the legend engine already matches),
      everything else reported.

      Targets: size → `get-point-radius`/`get-icon-size`; colour →
      `get-fill-color`/`get-line-color`; stroke width → `get-line-width`;
      rotation → `get-icon-angle`; opacity → RGBA alpha (no separate accessor);
      offset → `get-icon-pixel-offset`.

      Constraints: the sprite atlas is keyed **per class**, so within-class
      variation cannot be baked into a sprite — accessor or report. And the
      legend is derived by static analysis, so canonical `scale()` forms earn a
      legend while arbitrary expressions fall back to one swatch.

      **Unverified:** `QgsSizeScaleTransformer` / `QgsColorRampTransformer` API
      shape. Local PyQGIS is broken on the NixOS box (see §5); check in the
      container tier.

- [ ] **Rule-based renderer.** `convertFromRenderer()` then flatten;
      QGIS2VectorTiles' `rules_flattener.py` is a working reference. Unknowns:
      nested rules, `ELSE` rules, per-rule scale ranges, filter expressions → our
      grammar.
- [ ] **Multi-symbol-layer stacking.** The most architecturally invasive item,
      and it collides with something concrete: **the free plan caps a map at 5
      layers.** A 3-layer symbol across 3 layers blows the cap. Needs a design
      answer before any code. Also touches layer ids, legend, picking and draw
      order.
- [ ] **Heatmap renderer** → `HeatmapLayer`. Map radius units, weight field, ramp.
- [ ] **Label placement depth.** The best-defined gap in the market — Felt drops
      offset and placement, Mango hard-codes placement by geometry type with
      documented duplicate labels at tile seams, GIS Cloud loses halo size and
      forces unknown fonts to Arial. We already translate a quadrant, and the
      runtime's `TextLayer` exposes anchor, baseline, pixel offset and
      `anchor="surface"`.
- [ ] **Map-unit sizing — spike first.** The runtime exposes `line-width-units`,
      `point-radius-units`, `icon-size-units`, and deck.gl supports `meters`. If
      `meters` is accepted, this is nearly free rather than the 1px default
      everyone ships. **Not verified.** A short spike flips this between buckets,
      and it is the cheapest second differentiator on the list — GIS Cloud's own
      source apologises for defaulting map units to 1px.

### Bucket 3 — buildable, but the cost sits in verification

- [ ] **The webkit popup regression.** Blocker on the 0.7+ pin, 0/5 vs 5/5 on
      0.6.20, deterministic. The pin cannot roll back without losing raster, so
      the fix must come from upstream. **Report it now** so it has runway.
- [ ] **Validate every emitted attribute against `onlymapjs.attributes.json`** —
      a machine-readable contract generated from the same registry the runtime
      validates against. This is a direct structural attack on the silent-drop
      bug class, and it is cheap.
- [ ] Anything multi-layer: draw order and picking fail only in a real browser.
- [ ] Raster baking: correctness is visual by definition — the pixel-diff gate.
- [ ] Qt5 / Qt6 enum handling for any new enum we read. We have been bitten
      before (`pyqt5-returns-bare-ints-for-qt-enums`).

**Worth naming:** no public rendering-accuracy benchmark exists between any pair
of QGIS→web converters. We already have a pixel-diff gate. That is both our
verification backbone and something genuinely novel to publish.

### Bucket 4 — not for the initial version

- **Pattern, gradient, shapeburst fills** — no fill-pattern property in the
  runtime at all; pure sprite-texture work with no native path.
- **Marker-lines, arrowheads** — degrade badly as images; Felt reaches them via
  MapLibre's `line-pattern`, which we do not have.
- **Diagrams** (pie / histogram) — only GeoLibre ships them; narrow audience.
- **Blend modes** — qgis2web's is a canvas `globalCompositeOperation` trick;
  deck.gl exposes only raw GL `parameters`. Spike later.
- **Symbol levels** — blocked behind multi-layer stacking anyway.
- **Point cluster / displacement renderers** — clustering is not in the runtime
  for vector points; the aggregation layers are binning, not QGIS-style
  clustering (`point-clustering-is-not-in-the-runtime`). Upstream work.
- **Geometry generators** — already reported well; real support means evaluating
  expressions over geometry.
- **Smart-mapping style suggestion** — structurally unnecessary. QGIS *is* our
  source of truth; we need the stretch heuristic at most, not the suggest-a-style
  half.
- **Chasing Esri's ramp catalogue** — 14 named ramps done properly in light and
  dark is the right target.
- **GeoStyler** — rejected on evidence.

---

## 5. Decisions already made

Abhijay, 2026-09-15:

- **No half-baked fallbacks.** Establish the achievable fidelity level first; if
  it is genuinely lacking, extend then. A strong core beats broad-and-shallow.
- **Raster colour: bake the QGIS ramp into an RGB COG.** Exact fidelity for any
  ramp, at the cost of click-to-read pixel values and dynamic restretch.
  *Implemented 2026-09-18.*
- **Bucket 1 rides with the hosting/raster release.**
- **Data-defined overrides lead the symbology release.**
- **Version numbers are not fixed.** Hosting may take 0.2 with UI after it. Refer
  to releases by name, never by number, until they are cut.

## 6. Open questions

1. **The 5-layer free-plan cap versus multi-symbol-layer stacking.** Unresolved
   and blocking that item.
2. **Does `units="meters"` work?** Flips map-unit sizing between buckets.
3. **How much of the QGIS expression language does the data-defined subset
   cover?** Needs a corpus of real styles.
4. **Should the qgis2web comparison page be revised?** It is strengths-only by
   instruction, and it was written when qgis2web looked stale. It is now
   actively maintained and beats us on four axes. Separately,
   `qgis2web-comparison-page-positioning` already flags a submission risk.

## 7. Environment note

Local PyQGIS is broken on the NixOS box. CONTRIBUTING's recipe assumes the system
`python` matches QGIS's, but QGIS 4.2.1 needs Python 3.14 while `python3` here is
3.12; pointing at a 3.14 interpreter hits an ABI mismatch
(`wrong ELF class`). The container tier is unaffected. **CONTRIBUTING needs
updating**, and any PyQGIS API verification in this document must happen in the
container.
