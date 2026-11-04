---
title: What gets exported
seo_title: What QGIS styling survives the export
description: >-
  Layers, symbology, labels, popups, height, terrain and attribution - what is preserved, what is approximated, and what is not exported.
lead_image:
  src: /images/dialog-fidelity-tab.png
  alt: The Fidelity tab - the Whole map group with the basemap marked Changed, the Parcels layer exact, and the selected row's sentence below the list.
---

# What gets exported

The plugin supports a deliberately narrow set, well, rather than everything,
badly. Whatever is not translated exactly appears in the **Fidelity** tab - it is
never dropped silently.

Where that line falls differently from the established plugin's is set out in
[QGIS2WebMap or qgis2web](qgis2web-comparison.md).

## Layers

| | |
|---|---|
| Vector points, lines, polygons | Yes |
| Layer order and groups, including nested groups | Yes |
| Layer visibility and opacity | Yes |
| Scale-dependent visibility | Yes - converted to the matching web zoom levels; the Fidelity tab shows the zooms, and how far they drift towards the edges of a tall map |
| Rasters from a file on disk (GeoTIFF) | Yes - inside a single HTML file up to 100 MB of imagery, or in Folder and Share ZIP. Colours are drawn in as QGIS shows them |
| Vector layers from a web service (WFS, OGC API Features, SensorThings, ArcGIS feature services) | Yes - the features are downloaded once and embedded, so the map does not call the service. It is a snapshot, not a live feed |
| XYZ tile layers (`{z}/{x}/{y}`, and `{q}` quadkeys) | Yes - as live layers that need an internet connection. An address carrying an access key is refused rather than published |
| WMS layers | Yes, when the service offers Web Mercator (EPSG:3857) - as live layers the map asks for Web Mercator tiles, whatever CRS the project uses; a layer made of several sublayers stacks them as QGIS does. They need an internet connection, and the service must allow web pages to load its images (CORS), or the layer stays empty. Whether it offers EPSG:3857 is read from what QGIS stored when it loaded the layer; exporting never contacts the service. A time-enabled (WMS-T) layer that follows QGIS's time is exported as a fixed snapshot of the time QGIS draws at export - the map has no time slider; move QGIS's temporal controller and export again for another time. Its legend shows the picture the service draws for it, as QGIS's legend does - the style's LegendURL or a GetLegendGraphic request, one picture for each sublayer of a layer made of several (QGIS's legend shows only one of them: the top one's on QGIS 4, the bottom one's on 3.44) - loaded live like the tiles, so it needs an internet connection; offline the legend lists the layer by name. A contextual WMS legend is shown whole, not just what is in view. Not exported: a service without EPSG:3857, a layer that needs a login, a WMS-T layer exported without a QGIS map view (Processing, a cartograph frame) or whose temporal matching is not to the start or end of the range, and one with a reference time |
| WMTS layers | Yes, on a Web Mercator tile grid (such as GoogleMapsCompatible) - as live layers, with the same internet and CORS conditions as WMS. The legend shows the style's LegendURL picture when the service gives one, live as for WMS. A layer on another grid is not exported; add it again on a Web Mercator grid if the service offers one |
| Rasters QGIS reads over the web (a COG at `/vsicurl/https://...` or an `https://` address) | Yes, when it is a 3-band colour composite in Web Mercator (EPSG:3857) that QGIS draws as the file holds it, compressed with DEFLATE, JPEG, WEBP or nothing - as a live layer the map reads straight from the address, carrying nothing. It needs an internet connection, and the server must allow web pages to read the file (CORS) and to ask for parts of it (Range requests), or the layer stays empty. The export never downloads the raster: everything is read from what QGIS already holds. Not exported: another CRS, a style QGIS draws from the values (a colour ramp, palette, grey stretch, transparency or colour adjustment), LZW, ZSTD or LERC compression (which the map cannot decode yet), an address carrying an access key, and a cloud store such as `/vsis3/`. For those, save a local copy (right-click the layer, Export > Save As...) and add the file, and it is carried inside the map |
| WCS and ArcGIS map services | Not exported. Add the service's WMS, WMTS or XYZ address if it has one, or save the layer as a GeoTIFF (Export > Save As...) |
| Attribute-only tables | Not exported - nothing to draw |

Data in any CRS is reprojected to WGS84 on the way out.

## Size limits on the free plan

Exported maps are drawn by the [OnlyMap runtime](https://onlymap.nikaplanet.com/),
and its free plan has three limits:

| | |
|---|---|
| Layers per map | 5 |
| Features per layer | 25,000 |
| Fetched data per map | 20 MB |

**None of them applies to a map opened locally.** The limits are for pages served
over `http(s)` from a real domain. Everywhere else the runtime lifts all three:

- a file opened from disk, or any address that is not `http`/`https`
- `localhost`, anything ending `.localhost`, any `127.x` address, `::1`,
  `0.0.0.0`

So a Standalone HTML file you send to someone, and anything you open from your
own disk, draws every layer and every feature. **The attribution badge stays** -
the exemption lifts limits, never the credit.

They apply when the map is served from a real domain: a Folder or Share ZIP
export published to a web server. A paid licence lifts them there too.

**Uncapped is not the same as licensed.** Whether you *may* use an exported map
commercially is a licensing question, unaffected by which limits happen to
apply - see [Using a paid OnlyMap licence](#using-a-paid-onlymap-licence).

**On a hosted map the two feature limits behave differently, and the second is the
dangerous one.**

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
  and says how many features are missing. It flags them for any export, since it
  cannot know whether you will later host the file.

The Fidelity tab is now the only place this is reported. Exporting used to push a
warning into the QGIS message bar and mount the runtime's error panel in the
exported map as well; both were removed, because on a local map there is nothing
to warn about - the runtime validated those exports, found nothing wrong, and
showed a success badge over the legend for its trouble. **If you publish to a web
server, check the Fidelity tab first.**

To stay within the limits on a hosted map without a licence: tick **Export only
the features in this view** on the Map tab, which leaves out everything outside
the current QGIS view and is the quickest way to bring a very large layer under
the feature cap; split the project across several maps; or filter the layer in
QGIS.

A feature is kept if any part of it touches the view, and is exported whole -
nothing is cut at the edge. Clipping reports what it removed, per layer, in the **Fidelity** tab - features
that are not there leave no gap on the map, so this is the one thing an export
cannot show you for itself.

## Using a paid OnlyMap licence

A key lifts the limits on a hosted map and removes the on-map badge. A map opened
locally is already uncapped, so a key changes nothing there - but see the note on
commercial use below, which is a separate question from what the runtime
enforces.

Because that leaves a key mattering only for hosted maps, the export dialog has
no field for one. Supply it either way round instead:

- set the `ONLYMAP_LICENSE_KEY` environment variable, which is also the only
  route that works on a machine with no QGIS profile - a server, a container, CI;
- or pass the **OnlyMap licence key** parameter to the *Export to OnlyMap web
  map* Processing algorithm.

The environment variable is picked up by the dialog too, so a key set there once
applies to every export. The parameter applies to the run it is passed to.

The key is stored on your computer, not in the `.qgz`, so it follows you between
projects and is never sent to anyone you share a project file with. It is written
into each map you export with it - that is how the runtime reads it, and it is
safe: the key is signed and domain-locked rather than secret.

**A key is tied to the domains it was issued for.** The runtime checks the key
against the domain the map is served from, and a **Standalone HTML file opened by
double-clicking has no domain at all** - so a key issued for your organisation's
domain does not apply to it, and the map runs on the free plan. That no longer
costs you anything in size: a local map is uncapped either way. It is why the
badge can still appear on a file you emailed.

So a paid licence earns its keep when you **host the map** (Folder or Share ZIP,
published to a web server on a domain the key covers).

**Commercial use is a separate question from the limits.** OnlyMap's licence is
explicit that technical limits are a convenience, not the grant: their
"presence, absence, or failure" changes nothing about what you are allowed to
do, and commercial use - including distributing the map inside a packaged or
installable application - requires a commercial key regardless. A Standalone
HTML export for paid or commercial work therefore needs a licence even though
nothing in it will be capped or refuse to draw. Nothing in this plugin checks
that and nothing will; take the question to
[onlymap.nikaplanet.com](https://onlymap.nikaplanet.com/) rather than to us.

The Map tab reads your key and says which domains it covers and whether it has
expired.

**Batch and model exports** use the same key. The Processing algorithm takes an
optional *OnlyMap licence key* parameter; leave it blank and it falls back to the
`ONLYMAP_LICENSE_KEY` environment variable, then to the key saved in the dialog.
The environment variable is the route that works on a machine with no QGIS
profile - a server, a container, or CI. How to run those exports is in
[exporting without the dialog](batch-export.md).

## Symbology

| | |
|---|---|
| Single symbol | Yes |
| Categorized | Yes, with a category legend |
| Graduated | Yes, keeping your exact class breaks |
| Per-class symbol size, line width and outline colour | Yes, each class keeps its own |
| Line cap and join style | Yes, where you chose rounded |
| Marker shapes (square, star, triangle, …) | Yes - drawn by QGIS itself |
| SVG markers, including parametrised fill and stroke | Yes - drawn by QGIS itself |
| Stacked symbol layers on a **point** | Yes - drawn by QGIS itself |
| Stacked symbol layers on a line or polygon | Top layer only - the one you see - and reported |
| Rule-based | Yes - one legend class per rule, with QGIS deciding which rule draws each feature; see [Rule-based styling](#rule-based-styling) |
| Embedded-symbol renderers | Not yet, reported by name |
| Sizes in pixels, points, millimetres or inches | Yes - converted to screen pixels, so an 8 px marker stays 8 px |
| Sizes in map units or metres on the ground | Yes - marker size, line and outline width, and rasterised marker icons keep their distance on the ground and zoom with the map as in QGIS. Map units of a geographic CRS (degrees) are approximated - fixed at the size they have where the layer fills the opening view, and reported; so is a marker picture that also has a size in screen units, or classes that mix units |
| Symbol opacity | Yes - multiplied with the colour's own transparency, and with the layer opacity once |
| Sizes or opacity set from data per feature | Not yet - drawn at the symbol's static value, reported |
| Dashed and dotted lines | Yes - the line style dropdown and custom dash patterns alike |
| Markers along a line, or filling a polygon | Not yet, reported |
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

### Rule-based styling

A rule-based layer is exported as one class per rule that draws a symbol. When
you export, QGIS itself works out which rule draws each feature - nested rules
within their parent's filter, ELSE rules, rules with no filter, switched-off
rules drawing nothing - and the answer is written into the exported data as a
property called `Style` (`Style_2` and so on if the layer already has a field of
that name). Each class is drawn with its rule's symbol the way a categorized
class is, and the legend lists the rules by their labels, or by their filter
for a rule with no label. Popups and labels are not affected, and the `Style`
property never shows in a popup. Features no rule draws are left out, as in
QGIS.

Worth knowing, and each said in the Fidelity tab when it applies:

- **A feature two rules draw shows one symbol.** QGIS draws a feature once for
  every rule that matches it, one symbol over another; the web map draws it
  once, with the symbol of the last of those rules - the one on top in QGIS.
  The Fidelity tab says how many features this affects.
- **Scale ranges on rules are not kept.** A rule limited to a scale range draws
  at every zoom.
- **Filters that read the map view are worked out once.** A filter using
  `@map_scale`, `@map_extent`, an atlas variable or another value that follows
  what QGIS's map shows is evaluated at export with no map view, where the
  value is empty.
- **Nested rules are listed one after another** in the legend, without the
  rules that group them, and **symbol levels** are not kept: features are drawn
  in the order of the data.

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

**The relief carries your basemap.** With both a basemap and relief chosen, the
basemap's imagery drapes over the terrain surface, so the mountains look like
the map rather than a plain grey model — and your own symbology drapes over
them, painted onto the slopes. Extruded layers drape flat too - their colour
paints the mountains, but column height does not show while relief is on, and
the Fidelity tab says so. Two caveats: Liberty and Bright are
vector-only styles with no raster imagery to drape, so with those the relief
stays grey (the Fidelity tab says so); and Positron's draped imagery comes from
NASA's earthdata.nasa.gov even though its flat tiles come from openfreemap.org —
relief adds that third party, and the Fidelity tab names every host involved.

**Labels are unreadable over relief.** The map library stops honouring their
text size once relief is on, so the words shrink to a few pixels and squash
while the layer's own markers keep their size — the text is there, and too
small to read at any size you set. The Fidelity tab names the affected layers.
Turn relief off if the labels matter more than the terrain.

**Polygon outlines do not draw over relief** either — the fills paint the
surface but their borders disappear, so overlapping translucent areas are
hard to tell apart. The Fidelity tab names the affected layers. Turn relief
off if the outlines matter more.

Relief detail ends at roughly a 1:2.5 km scale: the public elevation tiles
stop there, and past it the terrain would blank rather than magnify. A relief
export therefore stops the camera at that scale — zooming further glides back
to the closest view that still renders. Maps whose story needs street-level
zoom should leave relief off.

If your project has its own terrain and you leave this off, the Fidelity tab
says so rather than exporting a flat map silently.

## Labels

Text, font, colour, halo and background are translated, along with the
placement quadrant, offset and rotation you set in QGIS.

**Text case** (uppercase, lowercase, capitalise, title case) and **line
breaking** — both the wrap character you set and automatic wrapping at a
character count — are applied to the label text itself, because that is what
QGIS does too.

**Multi-part features** are labelled where QGIS labels them: every point of a
multipoint, and a multi-part line or polygon once, on its longest or largest
part — or once on every part when **Label every part of multi-part features**
is ticked in the layer's label placement settings.

**Lines** are labelled at half their length along them, measured as QGIS
measures them: in degrees in a project whose CRS is geographic, and in Web
Mercator otherwise, which is exact for an EPSG:3857 project and close for
other projected ones. With **Repeat** set in the label placement, labels repeat
where QGIS repeats them: the line is cut into equal pieces at least the repeat
distance long — stretched to a whole multiple of it where the label is longer
— and each piece is labelled at its middle. A distance in millimetres, points,
pixels or inches is taken at the view where the layer fills the map, so it
matches QGIS there and the repeats spread apart as you zoom in; one in map
units or metres on the ground is kept as set. A map unit scale on the distance
changes nothing, as in QGIS 4.2. With no Repeat set, a long line is labelled
again every 250 pixels at that view, either side of the label QGIS draws at
its middle, so a line keeps a label in view as you zoom in; a line shorter
than three spacings keeps its one label. The repeats are fixed along the line,
so a layer's labels show from the zoom where no repeat overlaps the next. At
most 50 labels sit on one line and 5,000 on one layer; past either the repeats
spread further apart, and the Fidelity tab says so.

**Formatted numbers** apply as in QGIS: a label that reads as a number
(including one from an expression) gets the decimal places and plus sign set,
rounded and written in QGIS's number format for its language, with its
decimal and thousands separators. The text is worked out at export, in the
language QGIS runs in then. Data-defined number formatting is not carried; the
layer's own setting is used, and the Fidelity tab says so.

Collision handling, callouts and curved placement are not: the web map draws
every label, so where a layer's labels would pile up they wait for a closer
zoom - the one at which most of them have room - and the Fidelity tab names it.
Italic is dropped, because the web renderer builds its font from a family and a
weight only; that is reported.

**Dense point layers cluster.** A point layer of 100 or more points that would
overlap when zoomed out merges them into count bubbles, which split as you zoom
in; clicking one zooms to its points. A bubble's size follows the log of how
many points it holds, so a bubble of 5,000 is a step bigger than one of 500
rather than swallowing the map. Its labels appear once separate places have
stopped merging, and its popups open on points, not bubbles. Points at exactly
the same spot stay one bubble showing how many there are. The Fidelity tab
gives the zoom at which the points show one by one. Untick **Cluster dense
points** on the layer's **Points** row in the **Layers** tab to show every
point instead. A layer with a filter (see *Filter by* below) does not cluster.

**Names beside every point.** On a point layer's **Points** row, **Names**
picks a field whose value shows beside each point with a halo, without clicking
for a popup. It replaces the layer's QGIS labels, and like them the names
appear from the zoom where most have room. Accented and other non-ASCII
letters are collected from the names field itself.

**Labels from an expression** are worked out for each feature when the map is
exported, the way QGIS works them out, and the text is saved with the map: it
shows the data as it was at export and does not change if the data changes
later. Text case and line breaking apply to it as to a field's labels. Only the
text is saved, not the fields the expression reads, so a field hidden from the
popups stays out of the map. A feature the expression gives no value for (NULL)
or fails on has no label, as in QGIS. Variables QGIS sets only while it draws a
map, a layout or an atlas (`@map_scale`, `@map_extent`, `@atlas_feature` and
the like) have no value at export, and an expression using `now()` or `rand()`
keeps the value it gave at export; the Fidelity tab names both.
Rule-based labelling with a single unfiltered rule is translated like simple
labelling; anything with several rules, filters or scale ranges is reported and
shows no labels. A layer set to act only as an obstacle for other labels shows
none of its own. Accented and other non-ASCII letters are collected from every
label on the layer, however long it is.

Labels can be turned off per layer on the **Layers** tab.

## Popups

Field names, aliases, and fields you hid in the QGIS attribute table are all
respected. Popups are labelled by default, and can be turned off per layer.

A field whose name contains anything besides unaccented letters, digits and
underscores (a space, a parenthesis, an accented letter) cannot be referenced
by the web popup template, so it is renamed in the exported data —
`Last Known Eruption` becomes `Last_Known_Eruption`, `Höhe` becomes `H_he` —
while the popup label keeps the original spelling. The Fidelity tab lists
every rename. A field the map draws with (a category field, a label field,
an extrusion height) keeps its own name in the data, since the map reads it
there; the popup reads a second copy of its value under the safe name.

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

### Links and pictures

A value that is a web address (`http://` or `https://`) or an email address
(`mailto:`) shows as a link that opens in a new tab. Nothing else becomes a
link: a `javascript:`, `data:` or `file:` value stays text. No attribute value
can add anything to the page - whatever a value holds, it is shown as text, a
link or a picture.

Pictures follow the field's widget in QGIS (Layer Properties > Attributes
Form). A field whose widget is **Attachment**, with its **Integrated document
viewer** set to **Image**, shows its pictures in the popup, fitted to the
popup's width:

- **A picture on this computer is carried with the map** - inside the file
  for Standalone HTML, beside `index.html` for Folder and Share ZIP - so it
  shows without an internet connection. A relative path is found where QGIS
  finds it: beside the project file, or under the widget's default path. A
  picture over 1280 pixels across is scaled down to that, and one over 1 MB
  is saved again as JPEG (PNG when transparent); your files are not changed.
  One picture can be up to 1 MB after that, and one map carries up to 20 MB
  of pictures.
- **A picture that is a web address is not carried.** It loads each time
  the popup opens, so it shows only with an internet connection.

A picture that cannot be carried - not found, not a picture, or over a
limit - shows its path instead, and the Fidelity tab lists what was carried,
made smaller or left out. An Attachment field that shows its path as a link
without the full path shows the link by its file name, as QGIS does. A path in
a field without the Attachment widget stays text.

Only the pictures of features the map carries, in fields its popups show, are
read or carried: a field set to *Do not show this field*, or a layer with
popups off, carries none.

**Links, and pictures that are web addresses, need an internet connection when
the map is viewed.** Everything the map carries, its pictures included, works
offline.

### Settings for one layer only

Expanding a layer also reveals a **This layer only** row, where three map-wide
settings can be overridden for that layer alone:

- whether its popups open on hover or on click,
- its highlight colour,
- its coordinate precision — including keeping full precision for one layer
  while the rest of the map is rounded.

A point layer also has a **Points** row: **Names** shows a field's value beside
every point, with a halo, and **Cluster dense points** (on by default) can be
unticked to show every point rather than count bubbles.

**Filter by**, on the **This layer only** row, gives the web map a panel that
filters the layer by one of its fields: a slider for a number or a date, a tick
box per value for text of up to 50 values. The panel sits top left, under the
layer switcher, one per filtered layer, headed by the field's alias. It works
in a map opened from a file and over http. What it cannot do is reported in the
Fidelity tab: a feature with no value in the field is hidden while the filter
is there, a filtered layer's points do not merge into count bubbles (the filter
cannot count the bubbles again), and the layer's labels are not filtered.

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

Legend, layer switcher, zoom controls, a scale bar and a measure tool are **on
by default**. The layer switcher is left out of maps that have only one layer
to switch, since it would hold a single ticked box; labels count as an entry of
their own, so a labelled layer is enough to bring it back.

The measure tool measures distances and areas on the ground, in metric,
imperial or nautical units, whatever CRS the project uses. It works in a map
opened from a file, served from a website and in the Preview. It is not drawn
in a cartograph's interactive frames, which are maps on a printed page.

A WMS or WMTS layer's legend entry carries the picture the service draws for
its legend, under the layer's name, as in QGIS. The reader's browser loads it
from the service when the map opens, so it needs an internet connection (it
needs no CORS, unlike the tiles); when it cannot load, the legend simply lists
the layer by name. A tall legend picture keeps its size, so its text stays
readable, and scrolls; a wide one is scaled to the legend. A site that sets a
Content Security Policy must allow images from the service - see
[hosting](hosting.md#if-your-site-sets-a-content-security-policy).

On the **Appearance** tab you can also:

- **Show the map name and the project description** over the map, in a corner
  of your choosing. The description is the abstract from
  *Project Properties → Metadata*; nothing appears if the project has none.
  Corners are shared with the map controls — the layer switcher is top left,
  the legend top right, the zoom, scale and measure tool bottom left, and the
  OnlyMap credit bottom right.
- **Set the size of the legend and the layer switcher**, a width and a height
  in pixels for each. Left on Auto, a panel is as wide as its contents and at
  most half the map tall; set, it is that size, but never bigger than the map,
  so the page does not scroll.
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

**Readers can switch basemap** if you tick *Let readers switch basemap* under
the basemap. The map then carries a Basemap panel listing every basemap above,
the one you chose ticked, and switching to another fetches that one's tiles
from its own provider - so the Fidelity tab names every provider the panel can
reach. A Standalone HTML or Share ZIP map leaves OpenStreetMap out of the list,
since its servers refuse maps opened from a file (unless it is the basemap you
chose). Not with relief, which drapes the basemap's imagery over the terrain
instead of showing the basemap.
