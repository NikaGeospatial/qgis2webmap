# What gets exported

0.1.0 supports a deliberately narrow set, well, rather than everything, badly.
Whatever is not translated exactly appears in the **Fidelity** tab - it is never
dropped silently.

## Layers

| | |
|---|---|
| Vector points, lines, polygons | Yes |
| Layer order and groups, including nested groups | Yes |
| Layer visibility and opacity | Yes |
| Scale-dependent visibility | Yes, converted to zoom levels |
| Rasters | Not in 0.1.0 |
| Attribute-only tables | Not exported - nothing to draw |

Data in any CRS is reprojected to WGS84 on the way out.

## Size limits on the free plan

Exported maps are drawn by the OnlyMap runtime, and its free plan has two limits
that apply **inside the exported file, on the recipient's machine**:

| | |
|---|---|
| Layers per map | 5 |
| Features per layer | 25,000 |

They are not lifted by opening the file from your own disk - a `file://` map is
capped exactly like a hosted one.

**The two limits behave differently, and the second is the dangerous one.**

- Past **5 layers**, the extra layers render *nothing*. An obviously missing
  layer is at least obvious.
- Past **25,000 features**, the layer is **truncated, not dropped**. The runtime
  draws the first 25,000 features in source order and discards the rest. It
  shows the recipient a dismissible on-map notice naming the layer and the
  counts ("Showing 25,000 of 500,000 rows..."), so the shortfall is not hidden.
  But the legend, any filter widgets and the map's own statistics describe only
  the part that was drawn - so once the notice is dismissed, the map reads as
  complete while being a subset.

The plugin will not let this happen quietly:

- The **Fidelity** tab names every layer that is over a limit, before you export,
  and says how many features are missing.
- Exporting a project that breaches one shows a warning in the QGIS message bar,
  then exports anyway - the choice stays yours.
- The exported map carries the runtime's own error panel, so a recipient sees an
  explanation in the corner rather than an unexplained gap.

To stay within the limits without a licence, split the project across several
maps, or filter the layer in QGIS so fewer features are exported.

## Using a paid OnlyMap licence

If you have bought an OnlyMap licence, paste the key into **OnlyMap licence** on
the Map tab. It lifts both limits and removes the on-map badge.

The key is stored on your computer, not in the `.qgz`, so it follows you between
projects and is never sent to anyone you share a project file with. It is written
into each map you export with it - that is how the runtime reads it, and it is
safe: the key is signed and domain-locked rather than secret.

**A key is tied to the domains it was issued for, and that has one consequence
worth understanding before you rely on it.** The runtime checks the key against
the domain the map is served from. A **Standalone HTML file opened by
double-clicking has no domain at all**, so a key issued for your organisation's
domain does not apply to it - the map falls back to the free plan and its
limits.

So a paid licence works when you:

- **Host the map** (Folder or Share ZIP, published to a web server on a domain
  the key covers).

It does not work when you:

- **Email someone a standalone file** and they open it locally - unless NIKA
  issued you a key that covers local files.

The Map tab reads your key and says which domains it covers, whether it has
expired, and warns you if you are about to export a standalone file it cannot
cover. Ask NIKA for a key covering local files if that is how you share maps.

## Symbology

| | |
|---|---|
| Single symbol | Yes |
| Categorized | Yes, with a category legend |
| Graduated | Yes, keeping your exact class breaks |
| Per-class symbol size and line width | Yes, each class keeps its own |
| Line cap and join style | Yes, where you chose rounded |
| Marker shapes (square, star, triangle, …) | Yes - drawn by QGIS itself |
| SVG markers, including parametrised fill and stroke | Yes - drawn by QGIS itself |
| Stacked symbol layers on a **point** | Yes - drawn by QGIS itself |
| Stacked symbol layers on a line or polygon | Top layer only - the one you see - and reported |
| Rule-based, embedded-symbol renderers | Not in 0.1.0, reported by name |
| Dashed and dotted lines | Yes - the line style dropdown and custom dash patterns alike |
| Markers along a line, or filling a polygon | Not in 0.1.0, reported |
| 2.5D renderer | Yes - becomes a real extrusion, keeping the roof and wall colours |

### Markers

A point layer using anything a plain circle cannot express — an SVG file, one of
QGIS's ~40 marker shapes, or several symbol layers stacked into one marker — is
**drawn by QGIS** into the exported file as a small image sheet, and the map
draws from that. Nothing about the symbol is re-created in the browser, so a
parametrised SVG comes out in the colours you set it to, and a stacked marker
comes out stacked.

This is the single biggest gap in the incumbent: qgis2web draws every marker as
a circle, in both of its renderers, and says nothing about it
([qgis2web#1218](https://github.com/qgis2web/qgis2web/issues/1218)).

Worth knowing:

- **It applies to the whole layer.** If one class uses an SVG, every class in
  that layer is drawn the same way, plain dots included. Splitting one layer in
  two would make the halves fight over which draws on top.
- **The legend switches with it.** A map with drawn markers gets a legend whose
  swatches are those same markers, so the legend and the map cannot disagree.
  That legend is static, so it does not grey a layer out when you hide it from
  the layer switcher; every other map keeps the interactive legend.
- **Marker size, rotation and offset are kept.** A layer whose classes differ in
  size keeps each class's own size.
- **Very elaborate layers fall back.** Past 256 distinct marker appearances in
  one layer, or markers so large the sheet would exceed what some graphics
  hardware accepts, the layer draws as plain circles in its own colours rather
  than drawing some markers and not others. The Fidelity tab says which, and
  why.
- **If you host the map behind a strict Content Security Policy**, it needs to
  allow `data:` images — see [Hosting](hosting.md).

## Height

Polygons that stand up in QGIS stand up in the export. Both of the places QGIS
keeps a height are read:

- **Layer Properties → 3D View**, where an extrusion height can be a fixed
  number or driven by a field, and *Show edges* becomes a wireframe outline.
- **The 2.5D renderer** on the Symbology tab, whose height is kept as a project
  variable.

A map with anything raised on it **opens tilted**, because looking straight down
at an extruded map and a flat one gives the same picture.

What does not carry: a base height, because the web renderer extrudes from
ground level; a height written as a QGIS expression rather than a single field
or a number; and the 2.5D renderer's fixed viewing angle and painted shadows,
which are replaced by real lighting. 3D point symbols - spheres, cylinders,
imported models - are not drawn at all, and lines cannot be raised. Every one of
these is named in the Fidelity tab rather than left to be discovered.

## Terrain

Relief is **off by default** and, unlike everything else here, it is not read
from your project. A QGIS terrain is a DEM on your disk, and a web map needs
elevation tiles it can fetch, so there is no route from one to the other short
of hosting your DEM yourself.

What the Map tab offers instead is global relief, streamed from a public
elevation tileset. The trade is the same as a basemap's: the file does not get
any bigger, but the map needs a connection to show relief. Switching it on also
tilts the map, for the same reason extrusion does.

If your project has its own terrain and you leave this off, the Fidelity tab
says so rather than exporting a flat map silently.

## Labels

Text, font, colour, halo and background are translated, along with the
placement quadrant, offset and rotation you set in QGIS.

**Text case** (uppercase, lowercase, capitalise, title case) and **line
breaking** — both the wrap character you set and automatic wrapping at a
character count — are applied to the label text itself, because that is what
QGIS does too.

Collision handling, callouts and curved placement are not - the web renderer
resolves overlapping labels with its own logic. Italic is dropped, because the
web renderer builds its font from a family and a weight only; that is reported.

Labels using an expression rather than a single field are not translated in
0.1.0.

Labels can be turned off per layer on the **Layers** tab.

## Popups

Field names, aliases, and fields you hid in the QGIS attribute table are all
respected. Popups are labelled by default, and can be turned off per layer.

Expand a layer on the **Layers** tab to set how each of its fields appears:

| Setting | What the popup shows |
|---|---|
| Value only, no label | The value on its own. |
| Label beside value — always show | Name and value on one line, even when the value is empty. |
| Label beside value — only if it has data | The same, but the row disappears for features with no value. **This is the default.** |
| Label above value — always show | Name on its own line, value beneath it. |
| Label above value — only if it has data | The same, but the row disappears for features with no value. |
| Do not show this field | The field is left out entirely. |

Fields hidden in the QGIS attribute table start on **Do not show this field**;
choosing anything else overrides that.

**Set every field to** below the layer list applies one setting to every field
of every layer at once, replacing the individual choices.

### Settings for one layer only

Expanding a layer also reveals a **This layer only** row, where three map-wide
settings can be overridden for that layer alone:

- whether its popups open on hover or on click,
- its highlight colour,
- its coordinate precision — including keeping full precision for one layer
  while the rest of the map is rounded.

Each starts on *"same as map"*, so a layer you never touch follows the map-wide
choice. This is deliberately something qgis2web has never offered: the same
three settings have been requested there as per-layer options since 2015
([#131](https://github.com/qgis2web/qgis2web/issues/131),
[#132](https://github.com/qgis2web/qgis2web/issues/132),
[#133](https://github.com/qgis2web/qgis2web/issues/133)) and remain global-only.
Without it, "every layer alike except one" means configuring every layer.

The "only if it has data" settings are resolved in the browser as the map is
used, because whether a value is empty depends on the feature you clicked. On
browsers older than about 2023 those rows show empty instead of disappearing;
everything else is unaffected.

## Attribution

A credit set on a layer — in **Layer Properties → QGIS Server → Attribution**,
or as metadata rights — is shown in the map's credit component, in the
bottom-right corner. Credits from several layers are combined, and duplicates
appear once.

They sit outside the collapsing part of that component, so they stay visible on
a phone: attribution hidden behind a click does not discharge a licence
obligation.

If a layer carries no credit, no credit line appears. The plugin cannot invent
one, so if your source requires attribution, set it in QGIS before exporting.

## The map itself

Legend, layer switcher, zoom controls and a scale bar are **on by default**.

On the **Appearance** tab you can also:

- **Show the map name and the project description** over the map, in a corner
  of your choosing. The description is the abstract from
  *Project Properties → Metadata*; nothing appears if the project has none.
  Corners are shared with the map controls — the layer switcher is top left,
  the legend top right, the zoom and scale bottom left, and the OnlyMap credit
  bottom right.
- **Set the colour of the controls**, background and text separately. Left
  alone, they use OnlyMap's own colours.
- **Open popups on hover** instead of on click. Hover replaces click rather
  than adding to it: with both, clicking an already-open popup appears to do
  nothing.
- **Set the highlight colour** shown when the cursor is over a feature. Keep
  some transparency — a solid colour hides whatever the feature sits on top of.
  qgis2web has no control for this at all: it reuses the QGIS *editing
  selection* colour, opaque yellow by default, and the only way to change it is
  Project Properties, outside the plugin entirely.

Each of the last two, plus coordinate precision, can also be set for a single
layer — see *Settings for one layer only* above.

The starting view comes from your data's extent. Data crossing the 180th
meridian - Alaska, Fiji, Chukotka - is handled correctly rather than opening
zoomed out to the whole world.

On the **Map** tab you can open the map on **the current QGIS view** instead.
That is a plain rectangle, so a view spanning the 180th meridian falls back to
the data extent, which handles the wrap properly.

**Coordinate precision** is on the same tab. Full precision is kept unless you
choose otherwise; rounding makes the file smaller and is the one setting here
that throws data away, so it is listed in the fidelity report whenever it is
set. Roughly, six decimal places is 0.1 m at the equator.

## Basemaps

There is no basemap by default, and that is what lets an exported map work with
no internet connection at all.

You can choose one on the Map tab - OpenStreetMap, Positron, Dark Matter,
Voyager, Liberty or Bright. It is worth understanding what that changes, because
it is the only setting whose cost falls on whoever you send the map to:

- **The file does not get any bigger.** Tiles are fetched as the map is used,
  not packed into the export.
- **The map stops working offline.** Without a connection the data still draws,
  but the background does not.
- **Each recipient's browser contacts the tile provider directly** every time
  they open the map.

Tiles cannot be bundled for offline use. Beyond the size - the Alaska region
alone is several gigabytes at usable zoom levels - the OpenStreetMap
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
prohibits bulk downloading and offline use of its tiles.

The Fidelity tab names the provider you are depending on whenever a basemap is
set.
