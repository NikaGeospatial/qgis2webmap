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

## Symbology

| | |
|---|---|
| Single symbol | Yes |
| Categorized | Yes, with a category legend |
| Graduated | Yes, keeping your exact class breaks |
| Marker shapes (square, star, triangle, …) | Recorded, and reported if approximated |
| Rule-based, embedded-symbol renderers | Not in 0.1.0, reported by name |
| Stacked symbol layers | Bottom layer only, and reported |

## Labels

Text, font size, colour and halo are translated. Placement rules, collision
handling and curved labels are not - the web renderer places labels itself.

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

No basemap is included in 0.1.0, which is what lets an exported map work with no
internet connection.
