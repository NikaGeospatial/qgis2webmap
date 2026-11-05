# Changelog

All notable changes to QGIS2WebMap by NIKA. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.3] - Unreleased

### Added
- **WMS layers in any coordinate system.** A WMS that offers no Web
  Mercator (EPSG:3857) - many national services offer only their own grid,
  such as EPSG:25832 - is now exported: the map asks it for each tile in the
  coordinate system QGIS asks it in, and warps the picture into place, on
  ordinary and projected maps alike. So is a WMS whose capabilities QGIS
  has no stored copy of, in the coordinate system the layer was added in;
  its legend then lists it by name, and the Fidelity tab says so and what to
  do - for a service that tells QGIS not to store them (NASA GIBS does), add
  the layer in EPSG:3857 to have it asked in Web Mercator. A
  service that offers Web Mercator is asked in it, exactly as before. A
  coordinate system the map does not carry (most outside the UTM zones and
  the common national grids) is looked up at epsg.io by the reader's
  browser, once; the Fidelity tab says when. Coordinate systems that are not
  EPSG codes or CRS:84 are still refused, with a row.
- **WMTS layers on national tile grids** (swisstopo, PDOK, MML, AdV and
  any other WMTS grid): exported on the service's own grid, taken from the
  capabilities QGIS stored when it loaded the layer - the export still
  contacts no service - and warped into place. Web Mercator grids whose
  levels are not named by their zoom (PDOK's `00`, `01`, ...), which used to
  be refused, export the same way.
- **A time slider for time-enabled WMS (WMS-T) layers.** A WMS-T that
  follows QGIS's time opens at the time QGIS draws, and a slider (play,
  pause and scrub) steps it through the times the service lists, which QGIS
  read from its capabilities. It sits top left, under the layer switcher.
  When the time QGIS draws is not one of the service's times, QGIS asks for
  no time, or QGIS knows no list of times, the layer is a fixed snapshot as
  before, and the Fidelity tab says why. A service listing more than 2,000
  times gets a slider over the 2,000 around the time QGIS draws.
- **A time slider for vector layers that follow QGIS's time** (Layer
  Properties > Temporal) - point, line and polygon layers with a date, a
  date and time, or a text date. The map opens at the time QGIS draws and
  shows the features QGIS draws for that frame; the slider (play, pause and
  scrub, as a WMS-T's, top left) then shows the features QGIS draws for a
  frame of the temporal controller's length starting at the slider's time,
  playing at the controller's frame rate. Every mode that gives features
  their own time is followed - an instant from a field (with an event
  duration, or Accumulate features), start and end fields, start and
  duration fields, and start and end expressions - when the features last
  equally long; features lasting differently long are a snapshot of the
  time QGIS draws. A layer with a fixed temporal range opens switched off
  when QGIS does not draw it now; one QGIS only redraws, and every layer
  when the temporal controller is off or the export has no QGIS map view
  (Processing, a cartograph), exports all its features, as QGIS draws them
  without a time. The slider moves smoothly rather than frame by frame and
  spans the layer's own times; such a layer stays embedded, its points are
  not merged into count bubbles, and the Fidelity tab says all of this.
- **Live features from WFS, OGC API Features and ArcGIS FeatureServer
  layers.** Such a layer now loads live first: the reader's browser asks the
  service for the features in view, and again when the map moves elsewhere,
  following its paging up to 10,000 at a time, and switches to the copy
  embedded at export whenever the service cannot be read (it refuses maps on
  other sites, is down, or the reader is offline). A layer whose look is
  worked out from its features at export - a rule-based style, classes
  switched off, labels, Value Map or Value Relation values in its popup, a
  filter panel, count bubbles, popup pictures, virtual or joined fields - or
  that QGIS filters, needs a login or is a WFS 1.x, stays embedded as
  before; one over 20 MB is live only. A **Features** choice on the layer's
  *This layer only* row on the Layers tab picks live first, embedded or live
  only, is saved with the project and is used by Processing. The Fidelity tab
  names each live layer's host, and the map lists it among its network
  requests. Cartograph frames still embed.
- **Clicking a WMS layer shows what the service says is there,** as QGIS's
  Identify tool does. A click on a layer that is identifiable in QGIS
  (Project Properties, Data Sources) asks its service about the spot, from
  the reader's browser, about the sublayers the service says answer clicks,
  and only while the layer shows (switched on, and inside its zoom range).
  The answers share one popup in the look of the feature popups, the top
  layer first: each feature's fields, and nothing when the service found
  nothing. A service that answers only as text or as a web page has that
  shown as it is, the page without its scripts; a click a feature popup
  opens for stays that popup's. A service that does not allow web pages to
  read its answers (CORS) is named once in the notice, as "Could not get
  details from". The Fidelity tab has a *Clicks on* row for each such layer,
  and says why for one that cannot answer on the web map. Cartograph frames
  are not clickable.
- **A notice for a layer that cannot load.** When a layer fails in the
  reader's browser and has nothing to show instead - a live-only feature
  layer, or a tile or map service layer - a small "Could not load" notice
  names it, in the bottom-left corner just above the licence notice, and
  goes once the layer loads again.
- **PMTiles archives export as tiles.** A layer QGIS reads from a `.pmtiles`
  archive (which QGIS opens through GDAL, one layer per layer of the archive)
  is drawn on the web map from the archive itself, at every zoom it holds and
  in the layer's QGIS colours, line widths and point sizes, instead of as the
  pieces of one zoom's features that GDAL hands QGIS. A local archive is
  carried with the map: inside a Standalone HTML file (up to 100 MB together
  with any rasters, which opens by double-click), or beside the page in a
  Folder or Share ZIP (which has to be served, as with rasters - the Fidelity
  tab and the zip's README say so); an archive at an `https://` address stays
  there and is read live. The map carries the script that decodes the
  tiles, so a carried archive draws with no internet connection, and no
  archive or vector tile layer contacts anyone but its own server. That
  script (loaders.gl's open-source MVT decoder) is downloaded from npm with
  the OnlyMap runtime, pinned like it; on a computer that already has the
  runtime, the first export downloads just the script (about 240 KB).
  Reported in the Fidelity tab: labels and the filter
  panel are not exported for such a layer, rule-based styling draws in the
  first rule's symbol, markers QGIS draws itself become circles, and an
  archive compressed with zstd or brotli, which browsers cannot read, is left
  out.
- **Vector tile layers export in their publisher's style.** A vector tile
  layer added with a style URL (such as a government base map's `style.json`)
  is drawn on the web map from the same tile service, in that style: its
  colours, line widths, filters, zoom rules and labels. Only while QGIS still
  draws it as the style says: a layer you restyled in QGIS is left out with a
  row saying why, rather than shipped in colours you replaced, as is one whose
  style QGIS kept no copy of. Vector tile layers were not exported before.

### Changed
- **Languages download from the GitHub release, all at once.** Choosing
  any language other than English the first time fetches one file of about
  350 KB from this version's release on GitHub (`github.com`, which hands it
  to `release-assets.githubusercontent.com`) and installs every language, so
  a second language needs no second download. They used to come one at a
  time from jsDelivr, which serves no repository over 50 MB, and this one
  now is. Each language is still checked against the copy
  released with this version, and a download that is not exactly that
  release is refused whole.
- The one-time runtime download is about 6.1 MB (it was 5.9 MB): it now
  includes the vector tile decoder that maps with PMTiles or vector tile
  layers carry, which the licence dialog names.
- The map runtime is OnlyMap 0.13.4. What readers will notice: the scale bar
  now reads the true distance - it read twice the distance it covered, so a
  bar labelled 50 km spanned 25 km - and clicking a cluster bubble of a layer
  with popups zooms in exactly as far as it takes for the bubble to split
  (it zoomed in one step).
- **Tile and WMS services that refuse web pages (CORS) now show.** Their
  Web Mercator tiles are drawn as plain pictures, under every other layer,
  hidden while the map is tilted or rotated, and left out of pictures the
  map saves of itself; they used to stay empty. Services that allow only
  `https://` pages, such as Sachsen-Anhalt's (issue #2), now show in a map
  opened from a file and in **Preview in browser** too. Still empty without
  CORS: a WMS asked in another coordinate system, a WMTS on a national grid,
  and any such layer in a cartograph frame drawn in another projection,
  since those pictures are warped into place. A service on `http://` still
  does not draw in a map served over `https://` unless it also answers on
  `https://`.

### Fixed
- The README inside a Share ZIP or Folder no longer promises that the map
  works offline and sends nothing else when it reads a basemap, relief, live
  features or a web service: it then says the map needs an internet
  connection and names each server the map asks.
- The Fidelity tab names a WMS or WMTS service's host without the port its
  address spells out when that is the usual one: `basemap.nationalmap.gov`,
  not `basemap.nationalmap.gov:443`, for the USGS National Map.
- In Safari and other WebKit browsers the page no longer scrolls a little
  past the map when the legend is one the plugin draws itself (a map with a
  tile or web service layer) and is given a small size or folded at Largest.
  The runtime's hidden status line sat below the window there.
- Tile layers whose addresses carry no file extension (ArcGIS tile caches'
  `.../MapServer/tile/{z}/{y}/{x}`, Google-style addresses) and TMS `{-y}`
  addresses now draw; they exported but showed nothing. A WMTS layer with
  such a tile address, which was refused, now exports, and these layers no
  longer keep a cartograph's interactive frame out of its projected
  coordinate system.
- A WMS layer added on an `https://` address whose capabilities still name
  `http://` (the British Geological Survey's, for one) is now asked on
  `https://` for its tiles and legend, as it was added. It used to be asked
  on `http://`, which such a service only redirects to `https://`, and
  Safari then showed it only as plain pictures under the rest of the map.

## [0.3.2] - 2026-11-05

### Added
- **Rule-based styling exports.** Any rule-based layer is drawn with one
  class per rule that draws a symbol, and the legend lists the rules by their
  label. QGIS itself works out which rule draws each feature when you export
  - nested rules, ELSE rules and switched-off rules behave as in QGIS, and a
  layer whose rules are all switched off draws nothing - and the answer
  travels in the data as a `Style` property that popups do not
  show. Reported in the Fidelity tab where the web map differs: a feature two
  rules draw shows only the symbol on top, scale ranges on rules are not
  kept, filters that read the map view (such as `@map_scale`) are worked out
  once at export, and symbol levels are not kept. Rule-based layers used to
  be refused unless every rule picked values of one field.
- **A measure tool on exported maps**: whoever opens the map can measure a
  distance or an area on it, measured on the ground whatever CRS the project
  uses. On by default, like the other map controls; untick **Measure tool**
  on the Appearance tab, or the Processing algorithm's *Measure tool*
  parameter, to leave it off. Its units - **Metric**, **Imperial** or
  **Nautical** - are chosen beside that switch and saved with the project,
  and the scale bar reads in them too; the map has no units control of its
  own. A distance is two points: the second ends it, and clicks go back to
  the map (popups, panning) until **Distance** is pressed again. An area
  closes on **Ctrl + click** (**Cmd + click** on a Mac) at its last point, as
  well as on a double-click, a double-tap or Enter, and a hint above the tool
  says so while one is being drawn. The panel is compact: its buttons read
  **Distance** and **Area**, the reading shows under them only while there
  is one, with no description of the tool, and **Clear** is a row under
  that. It sits bottom left, just above the licence notice, and is not drawn
  in a cartograph's interactive frames.
- **Filter by**, a new choice on a vector layer's *This layer only* row in
  the Layers tab: the web map gets a panel, top left, that filters the layer
  by that field - a slider for a number or a date, a tick box per value for
  text of up to 50 values (a field with more is refused and the Fidelity tab
  says so); clearing every tick box hides every feature, as no category
  ticked does in QGIS. Headed by the field's alias. The Fidelity tab also says what it
  cannot do: features with no value in the field are hidden while the filter
  is there, a filtered layer's points are not merged into count bubbles, and
  its labels are not filtered.
- **Let readers switch basemap**, a new switch under the basemap on the Map
  tab, off by default: the map carries a Basemap panel, bottom left with the
  zoom controls, scale bar and measure tool, listing the keyless basemaps with the map's own ticked. In a
  Standalone HTML or Share ZIP map it leaves out OpenStreetMap, whose servers
  refuse maps opened from a file. The Fidelity tab and the export's list of
  network dependencies name every provider the panel can reach. Not available
  with no basemap or with a ground surface.
- **A width and a height for the legend and the layer switcher**, beside
  their switches on the Appearance tab: 105 to 600 pixels wide and 50 to
  1000 tall, and never bigger than the map, so the page does not scroll; on a
  narrow screen the width yields, and a panel narrower than its rows wraps
  them. Auto, the default, keeps them exactly as they were. From Auto the
  arrows go either way, from the size Auto gives a typical panel, and **↺**
  at the end of the row puts both boxes back on Auto. A WMS or WMTS legend
  picture follows the legend's size.
- **Exporting without the dialog**, a new guide: batch exports from the
  Processing toolbox, the QGIS Python console and `qgis_process`, and the
  one-time runtime setup they need first.
- **Links and pictures in popups.** A value that is a web or email address
  (`http`, `https` or `mailto`) shows as a link that opens in a new tab;
  other schemes stay text. A field whose QGIS widget is **Attachment** with
  its document viewer set to **Image** shows its pictures, fitted to the
  popup: a picture on this computer is carried with the map (inside a
  Standalone HTML file, beside `index.html` in Folder and Share ZIP, once
  however many features show it, and counted in the map's size), scaled
  down to 1280 pixels across when larger, up to 1 MB each and 20 MB per map;
  a picture that is a web address loads live. Only pictures in the project's
  folder or under the widget's default path are carried, wherever the path
  leads (`..`, an absolute path, a link): a picture anywhere else shows its
  path, and the Fidelity tab names it. Links and web pictures need an
  internet connection when the map is viewed, and the export's list of
  network dependencies names the web pictures; carried pictures work offline.
  The Fidelity tab says what was carried, made smaller or left out, and no
  attribute value can add markup to the page. qgis2web users report broken
  attribute links ([qgis2web#1126](https://github.com/qgis2web/qgis2web/issues/1126))
  and overflowing popup pictures ([qgis2web#1196](https://github.com/qgis2web/qgis2web/issues/1196)).
- **Cloud Optimized GeoTIFFs QGIS reads over the web** (`/vsicurl/https://...`
  or an `https://` address) export as live layers: the map reads the parts it
  draws straight from the address, and nothing is carried or downloaded. For
  a 3-band colour composite in Web Mercator (EPSG:3857) that QGIS draws as
  the file holds it, compressed with DEFLATE, JPEG, WEBP or nothing; the
  server must allow web pages to read the file (CORS) and to ask for parts of
  it (Range requests). LZW, ZSTD and LERC files are refused until the runtime
  decodes them, and so is a file that is not a GeoTIFF (such as a VRT) or
  whose header cannot be read. The header is read off the QGIS window's
  thread, from the copy GDAL kept when QGIS opened the layer, or from the
  server again if GDAL let it go. Works in a map opened from a file too.
- **Time-enabled WMS (WMS-T) layers that follow QGIS's time** export as a
  fixed snapshot: the map asks the service for the time QGIS draws at export
  (from the temporal controller, or the layer's predefined date) and has no
  time slider; the Fidelity tab says which time. Still refused, with a reason:
  an export with no QGIS map view to read the time from (Processing, a
  cartograph frame), a layer matched to the closest or whole range rather
  than the start or end of it, and a layer with a reference time.
- **Labels from an expression** (concatenations, `CASE`, `format_number` and
  any other QGIS expression) export, on web maps and in cartograph live
  frames: the text is worked out for each feature at export, as QGIS works
  it out, and saved with the map, so it shows the data as it was then. Only
  the text is saved, not the fields the expression reads. Variables QGIS sets
  only while drawing (`@map_scale`, atlas variables) have no value at export,
  and the Fidelity tab says so. These layers used to export with no labels.
- **WMS and WMTS legends** ([qgis2web#1015](https://github.com/qgis2web/qgis2web/issues/1015)):
  a WMS or WMTS layer's legend entry shows the picture the service draws for
  it, as QGIS's legend does - the style's LegendURL, or for a WMS a
  GetLegendGraphic request built from what QGIS stored when it loaded the
  layer, one for each sublayer of a layer made of several. The browser loads
  it from the service when the map opens, so it needs an internet
  connection; offline the legend lists the layer by name. An address that
  carries an access key, or that is not a web (`http` or `https`) address, is
  left out. A cartograph's legend still lists the
  layer by name; the Checks tab says so.

### Changed
- **The Processing algorithm exports what the dialog would.** *Export to
  OnlyMap web map* now uses the choices the dialog saved in the project -
  which layers to include, each layer's popup, field and label choices,
  clustering and point names, the map name, the basemap and the rest of the
  Appearance tab - where it used to export every layer with the defaults. A
  title or map control passed to the algorithm still replaces the saved one,
  and a project never opened in the dialog exports as before. A project
  saved with **Export only the features in this view** is refused, with a
  message, since a Processing run has no QGIS view to clip to: it used to
  export every feature. *Export print
  layout to cartograph (Beta)* likewise uses the layout's saved Map frames
  choices and resolutions, the Layers section's popup and label choices,
  Print settings and Browser appearance fields; *Frames* and *Image frame
  resolution*, when passed, replace every map's saved choice. Like the
  dialog's Save, it draws an image frame at least at the resolution the page
  is saved at - the saved Print settings', else 300 dpi - where it used to
  draw it at the frame's own resolution, 200 dpi by default.

- **Line labels repeat along long lines.** With **Repeat** set in a line
  layer's label placement, labels repeat where QGIS repeats them: the line cut
  into equal pieces at least that long, each labelled at its middle, the
  distance in millimetres, points, pixels or inches taken at the view where
  the layer fills the map, and in map units or metres on the ground as set.
  With no Repeat set, a long line is labelled again every 250 pixels at that
  view, either side of the label QGIS draws at its middle, so a line keeps a
  label in view as you zoom in; a line shorter than three spacings keeps its
  one label. The repeats are fixed along the line, so a layer's labels show
  from the zoom where no repeat overlaps the next. At most 50 labels sit on
  one line and 5,000 on one layer; past either the repeats spread out, and the
  Fidelity tab says so.
- **Formatted numbers** in a layer's label settings apply to labels from a
  field or an expression, and to a point layer's **Names**: a label that reads as a number has the decimal
  places and plus sign QGIS draws, in QGIS's number format for its language
  (for example `+1.234,50` in German, with a narrow no-break space between
  thousands in French). The text is worked out at export.

### Fixed
- At Appearance > Size **Largest**, a long legend or layer list no longer
  makes the page scroll: on Auto each panel stops at half the map's height at
  every Size, where the larger Sizes grew it past the half and, at Largest,
  past the map's foot. A legend with a picture swatch or a raster or web
  layer in it, which Size does not grow, reaches half the map at every Size
  too, where it stopped at a quarter of it at Largest.
- The Fidelity tab and the export's list of network dependencies name
  cartocdn.com, the domain the Dark Matter and Voyager basemaps really load
  from. They named carto.com, which the map never contacts.
- A long popup value with nowhere to wrap - a file path, a web address, an
  ID - breaks onto the next line inside the popup instead of running past its
  edge. Text with spaces still wraps between its words.
- A WMS layer made of several sublayers stacks them as QGIS draws them. The
  web map asked the service for them in the reverse order, so the one QGIS
  draws at the bottom covered the others; each sublayer's style stays with it.
- A line's label sits at half its length along it, as QGIS places it. It was
  on the line's middle vertex, which on a line of two vertices is one of its
  ends.
- A line's label is placed along it as measured in the CRS QGIS draws it in:
  the project's, or a cartograph map frame's own. A frame in Web Mercator in a
  project in degrees measured the line in degrees, and a project in any
  projected CRS but Web Mercator in Web Mercator, which put the label of a
  long north-south line degrees away from where QGIS draws it.
- A point layer's **Names** showing a different field from its labels draws
  every accented or other non-ASCII letter of the names. Letters only the
  names field used drew blank.
- Labels on multi-part features land where QGIS draws them
  ([qgis2web#1095](https://github.com/qgis2web/qgis2web/issues/1095)): every
  point of a multipoint is labelled, not only the first; a multi-part line is
  labelled on its longest part, not the one with the most vertices; and
  **Label every part of multi-part features** is honoured, with a label on
  every part of a line or polygon.
- A raster QGIS reads over the web that is not exported live (see Added) is
  refused with one Fidelity row saying why and to save a local copy (Export >
  Save As...), without reading the file. It used to block the export as a
  missing file while also listing it as exported as a Cloud Optimized GeoTIFF.
- OGC API Features and SensorThings layers are described as web services in
  the export, like WFS layers: their features are downloaded once and
  embedded. They were described as read from a database.
- The map opens showing the whole data extent, top to bottom and side to
  side, in the window it is opened in. The opening zoom only fitted the
  extent's width into a nominal 512px, so tall data opened cropped top and
  bottom and a phone cut the sides off wide data. A map whose extent already
  fits opens as before; a map opened with a saved camera (the Preview keeps
  one) or with its `center` or `zoom` edited by hand opens on that.
- On a narrow screen the OnlyMap licence badge, bottom left, no longer
  covers the data credit, bottom right - on a relief map, whose imagery
  credit is long, from about 540px wide down. The credit moves up clear of
  the badge and of whatever else is in the bottom row, and back down when
  there is room.
- The bottom-left corner narrows upward, the widest control at the bottom:
  the measure tool, then the basemap panel, the scale bar and the zoom
  controls, all above the licence notice. Its controls are scaled by **Size**
  so that the corner stacks them at their scaled size, and on a window too
  short for them they fold into their drawer, as on a narrow one, rather than
  reach the top-left corner's panels.
- At a Size above Normal on the Appearance tab, the map controls fold into
  their drawers in time: at Largest the measure tool reached under the
  legend and past the edge of a phone's screen. A bigger size folds them at
  a proportionally wider map, and in a drawer every control is at its Normal
  size, which the drawer has room for - a scaled control overflowed it, and
  the measure tool could not be reached at all.
- The legend lists the top layer first, as QGIS's Layers panel does, with a
  layer's labels right under it. It listed them bottom first, in the order
  the map draws them. The layer switcher's order is still OnlyMap's.
- An export after a preview carries the labels and rule classes of now when
  their expressions read a project or layer variable that has changed since,
  and reads again whenever one reads the clock, chance or another layer
  (`now()`, `rand()`, `aggregate()` and the like). The preview's read was
  reused, with the variable's old value and the preview's time.

## [0.3.1] - 2026-11-03

### Added
- **WMS layers export as live layers** ([#2](https://github.com/NikaGeospatial/qgis2webmap/issues/2)).
  The web map asks the service for Web Mercator (EPSG:3857) tiles itself,
  whatever CRS the project uses, so a WMS layer needs an internet connection
  and the service must allow web pages to load its images (CORS); if it does
  not, only that layer stays empty, and the map page reports why on its
  `om-validation-error` event. A WMS is exported when the capabilities QGIS
  stored for it list EPSG:3857 (or the layer already uses it): exporting
  never contacts the service. Needs the OnlyMap runtime 0.13.3.
- **WMTS layers on a Web Mercator tile grid** (such as GoogleMapsCompatible)
  export as live layers too.
- **XYZ layers with a `{q}` quadkey** (Bing-style) export as live layers.

### Changed
- Clearer Fidelity rows for the map services that are still not exported,
  each saying what to do: a WMS without Web Mercator, a WMTS on another tile
  grid, a WMS-T layer that follows QGIS's time, a service that needs a login,
  ArcGIS map services and WCS. An XYZ address with a token the map cannot
  fill (such as `{s}`) is now refused with a reason instead of exported to
  request the token literally.
- The OnlyMap runtime moves to 0.13.3.
- The web map's **Share** menu is shorter: **Make a cartograph...** and the
  two NIKA hand-off rows (**Prepare enhancement prompt...**, **Prepare
  critique prompt...**) are gone. **New from template...** on Cartograph
  still builds a layout from the map view.
- **Show in file manager** opens the map file's folder straight away instead
  of rebuilding the map first, as **Copy file path** already did.
- In Cartograph, **Save PDF…** is **Ctrl+S** (it was **Ctrl+P**).
- The runtime licence is shown once, at the first download: a later runtime
  version downloads without asking again.
- The downloaded runtime is kept compressed on disk (about 2.8 MB), and older
  runtime versions are deleted once a new one is installed and verified.
- Faster exports and cartograph saves: only the fields the export keeps are
  read from each layer, embedded data is compressed in a third of the time,
  and label decluttering and point clustering do less work. Maps draw the
  same.

### Fixed
- A legend or layer list longer than half the map no longer runs past its
  bottom edge and makes the whole page scroll: it stops at half the map's
  height and scrolls inside its own panel.
- On a relief map the imagery credit wraps to a second line, and the
  attribution above it sat underneath it. The attribution, and the preview's
  Host prompt, now keep clear of the credit at its measured height.
- A raster whose file is already a Cloud-Optimized GeoTIFF compressed with
  LZW, ZSTD or LERC no longer exports as an empty layer: the web map cannot
  decode those compressions, so the file is now converted (to DEFLATE, as
  every other raster is) instead of being copied as it stands.

## [0.3.0] - 2026-10-30

### Added
- **Cartograph (Beta): print layouts as printable HTML.** Choose
  **Cartograph** from the dialog's title menu (**Create: Web map ▾**) to turn
  a QGIS print layout into one HTML page that opens by double-click and
  prints at true page size from Chromium-based browsers.
  **Ctrl+Alt+C** (**Cmd+Alt+C** on macOS) opens the dialog straight on it.
  - **One screen**: the layout's page, live, on the left (*QGIS layout ·
    A4 landscape*, with a zoom bar and a link to the changes in the HTML), and a
    panel on the right - the current layout's header (name, paper,
    pages; a **Layout actions** menu with **Rename layout...** and **Import
    cartograph...**; **New from template...**; **Switch layout...**), then
    the **Layout**, **Atlas**, **Checks** and **Edit** tabs, then the file and
    its save status. **Page settings** (Title, Attribution, Theme) edit the layout itself, so the preview, the web page and the PDF agree. Per-layout choices are kept under a layout id, so a rename cannot detach them.
  - Map items become **Interactive** frames (pan, zoom, popups; in Web
    Mercator, or in the map's own CRS - projected, or geographic such as
    EPSG:4326 - when its layers allow) or **Image** frames (a pixel-exact
    image in the layout's own CRS, always drawn at least at the saved export
    resolution). Maps in EPSG:3857/4326, and other maps that draw in their own
    CRS, default to Interactive, others to Image, and a map the atlas turns is
    always Interactive; each map's card in **Map frames** switches it and
    sets its DPI. A **⋯** menu on the card has **Use canvas extent**, **Go to
    location...** and **Smooth when enlarged** for a continuous raster.
  - Grids, locator insets, legends, scale bars, north arrows, labels (with
    live `{{scale}}`, `{{title}}`, `{{date}}` and atlas tokens), shapes and
    pictures carry over, at the item opacity set in QGIS; the **Checks** tab
    lists everything that changes or is left out, and its title shows the count.
  - **The file saves itself** about a second after each change - in the
    dialog, the layout, a layer's style or the Layers list - by default to
    `Documents/QGIS2WebMap/Cartographs/<project>-<layout>.html`, even before
    the project is saved. It only saves over a file that is its own earlier
    output, or one you chose with **Change...**: if the project names any other
    file, that file is left alone and the cartograph is saved beside it as
    `name (2).html`, with the message under the file name saying where. While
    the window is open, every layout that has a file keeps it up to date, even
    one you switched away from or are editing in the layout designer; closing
    the window first finishes any save in progress; if the save fails, you can
    try again, keep the window open, or choose **Close without saving**.
  - **New from template...** builds a print layout, named
    `<Design> · <project title>`, from the map view in
    one of eleven **layout templates** - six quiet essentials (Daylight,
    Clearline, Bare Map, Caption, After Hours, Night Desk) and five with more
    character (Fieldbook, Broadside, Near & Far, Signal, Wayfinder), with the
    two dark designs last, plus a **Request a template** card that opens a
    suggestion form - each shown as a large
    preview of the current map with a one-line description of what it is for,
    on A4, A3, Letter, Tabloid and more. Templates never put stand-in text on
    the page: a panel with nothing real to show is left out and the map takes
    its room. Each card says what that page includes for this project, and
    what else the design can show with more content (notes, a source line, a
    sheet number). Signal's key numbers are **Live counts** added with
    **Add item**; a page made by an earlier version keeps its figures as
    ordinary text items you edit like any other text.
    The picker only chooses a design, page size, orientation and framing;
    change text sizes with **A-** / **A+** in the **Edit** tab. Legend text never drops below a readable size (8 pt): a legend that still cannot fit leaves
    its last entries out; the card and the **Checks** tab say how many, and a
    bold warning beside the page offers **Adjust legend** or **New from
    template...** to fix it.
    **Framing: fill the frame / whole view** sets how the canvas view fits, a
    card's **Preview** button (or **Space**) opens a large preview, and
    **Import cartograph...** turns a cartograph back into a QGIS layout.
  - **Legends you can edit**: double-click a legend on the page to choose
    which layers and classes it lists, rename them in the legend only (the
    layer keeps its name), reorder them, put removed entries back, and set
    its map, columns, text size and row spacing - one Undo step each. A new
    template's legend leaves out technical support layers (a hillshade, a web
    basemap), restorable from the same list, and heads an elevation ramp with
    its unit. A ramp whose range QGIS never computed is marked in the editor
    rather than restyled.
  - **Edit on the page**: the preview bar has **Fit page**, **Add item**
    (Text, Legend, Scale bar, North arrow, Live count, Detail map - click to
    place, or drag to draw)
    and zoom; pan with Space and drag or the middle mouse button (a press that
    moves under 3 px is a click). The **Edit** tab holds the selected item's
    controls - text words, bold, colour and alignment, **A−**/**A+** lettering
    size, a map's **Edit view**, **Context layers...**, **Fit context**,
    **Remove inset**, **Use canvas extent**, rotation and Interactive/Image,
    the legend editor, a scale bar's or north arrow's map, a **Live count**'s
    layer, field and value, and **Delete**.
    Double-clicking an item or adding one switches to it. A **Detail map**
    inset also takes a centre latitude and longitude.
  - **Live count**: a number that counts what a map frame currently shows -
    a layer's features, or those with one field value, in the frame's displayed
    ground (rotation included) and as the frame draws them (layers, themes,
    filters, switched-off categories and rules, scale limits, and the area an
    atlas page or a clipping setting cuts the map to). It follows the
    map by itself, shows **—** when it is not set up or its source is gone
    (with the reason in the **Edit** tab), and is fixed text in the exported
    page. In a map series the PDF and **Print with QGIS** count each page for
    itself. It replaces the typed-in statistic callout.
  - **Print PDF** (the PDF QGIS writes for CMYK and ICC) keeps text and
    shapes as vectors and rasterises only the transparent or blended layers.
  - **Go to location...** (a map frame's **⋯** menu) shows another place
    inside the frame without moving or resizing the frame. **Coordinates**
    takes a latitude and longitude, or X and Y in a coordinate system you
    choose with **Change...**; **Feature** searches a layer the map draws by
    name, ID or a text or whole-number field, and can fit the whole feature.
    A **Zoom** row (**½×**, **1×**, **2×**, **4×**, **8×**) and an exact
    scale field set the scale. Each map remembers its own reference scale, so
    **1×** always returns to it, however often the dialog is reopened. Nothing
    changes until **Go**, which is one Undo step; **Cancel** or **Esc** leaves
    the map alone.
  - **Empty map frames**: a map whose layers you untick in **Layers** stays
    empty, even after the last one, rather than falling back to drawing the
    project's layers.
  - **Create a map series** (the **Atlas** tab): one page per feature of a
    layer, with QGIS's margin, fixed and predefined scales for the page
    framing. Step through the pages with **Previous** and **Next**; **Export
    series...** makes one PDF, a PNG ZIP or an interactive web page, and
    **Print series...** prints every page in order through QGIS (or saves a
    PDF to print when no printer is found), or from the web page. **Advanced
    settings** holds **Include pages** (all features, **Matching a value**,
    or a custom expression), **Page order**, **Page names** and **Export
    filenames**; each optional field has a clear button. Printing a series
    leaves a project that had no unsaved changes without any.
  - **Save PDF…** is a native QGIS action in the header (**Ctrl+P**): it saves
    every page of the selected layout, current edits included, from a copy of
    the layout, with no HTML file, browser or print dialog. Interactive frames
    are drawn as static maps of their current view in the PDF and stay
    interactive in the HTML. **Share → Save atlas as PDF…** (when an atlas is
    set up) saves one run of pages per feature. The header order is colour
    vision, Save PDF…, Share, Settings, Help.
  - **PDF settings** (Share): page size, resolution, bleed, crop marks,
    safe-zone guide, presets; RGB by default, and CMYK and ICC PDFs where the
    QGIS build supports them. The dialog's **Save PDF…** button saves with them.
  - **Share** has an **Export** section and an **HTML cartograph** section
    (**Preview in browser**, **Copy HTML file path**, **Show HTML in folder**);
    the browser preview is for checking interactive maps and browser behaviour.
  - **Colour vision**: simulate protanopia, deuteranopia, tritanopia and
    achromatopsia on the QGIS canvas and the page, and list the legend
    classes that become indistinguishable.
  - **Show in folder** selects the file in your file manager.
  - **Export as cartograph (HTML)...** in the layout designer's Layout menu,
    **Create cartograph...** in the Web menu, and the **Export print layout
    to cartograph (Beta)** Processing algorithm.
- **Hand off to NIKA**, for web maps and cartographs: **Prepare
  enhancement prompt...** and **Prepare critique prompt...** (under **NIKA
  hand-off** in Share) open a dialog with a ready prompt for NIKA desktop or
  any other agent; nothing is sent from the plugin. The hosting row is now
  **Join NIKA hosting waitlist ↗**, and the disabled "coming soon" row is gone.

### Changed
- **The dialog's title says what it makes**: **Create: Web map ▾** opens a
  menu of the two outputs, each with a one-line description. Preview, Share
  and Export moved up beside it.
- **Settings and Help are icons in the header**, not tabs. Each opens its
  page in place of the main screen, with a back arrow. The web map's tabs are
  now Map, Layers, Appearance and Fidelity.
- **The live-preview switch is labelled**: a **Live** toggle beside Preview
  replaces the unlabelled dot.
- **New maps have somewhere to save from the start**:
  `Documents/QGIS2WebMap/Web maps/<map name>.html`, with ` (2)` added when the
  name is taken; the folder is made on Export. **Settings → Saving** changes
  the folder for every new map.
- **The dialog opens larger**, at 1152 × 768, still shrinking to fit the
  screen.
- **The window closes when you start a new project or open another**, since
  its settings, layers and files belong to the old one; reopen it to start from
  the new project.
- **Closed drop-downs pass the mouse wheel to the panel behind them**, so
  scrolling the panel never changes a choice.
- **Keyboard focus is visible on every control**, and in Arabic the
  dialog's directional styling - arrows, rounded ends, section chevrons - is
  mirrored along with its right-to-left layout.
- **Popups show editor-widget descriptions.** A field with a Value Map or
  Value Relation shows its descriptions in popups (a Value Relation's
  multi-selection in QGIS's own order), while drawing, filtering, labels and
  counting use the stored value, as QGIS does. When a layer's symbology hides
  every feature, the report says so rather than blaming an empty source.

### Fixed
- **Closing during slow work no longer crashes QGIS.** A background job that
  outlasts the closing window is left to finish safely.
- **A failed folder export keeps the previous export.** If a file cannot be
  written (a full disk, say), the folder still holds the last working page and
  its files, not a page that points at missing files.
- **Symbol sizes and opacity.** Marker size, outline width and offset now follow
  their unit (pixels, points, millimetres, inches) instead of all being read as
  millimetres, and symbol opacity is applied to the exported colours once.
  Sizes in map units or metres on the ground keep their ground size and zoom
  with the map as in QGIS (marker size, line and outline width, and the icons of
  rasterised markers), except in a geographic CRS, where a degree has no fixed
  size and they stay fixed at one reference scale and reported; sizes set from
  data per feature are reported too.
- **Labels.** Glyphs are collected from every label, not the first 5,000.
  Rule-based labelling with one unfiltered rule is translated and any other is
  reported; obstacle-only labelling no longer draws labels.
- **Symbol outlines match QGIS** in web maps and cartographs: a fill or
  marker with no outline ("No Pen") no longer gets a dark one, a 0 mm
  outline draws as a one-pixel hairline as in QGIS instead of disappearing,
  and an outline-only fill ("No Brush") is no longer filled.
- **Live legends match QGIS more closely**: lines show as lines, a classed
  layer's heading has no swatch of its own, and graduated classes keep their
  QGIS labels ("0–1 m", not "< 1"). **Checks** lists what the browser still
  draws differently in a legend or scale bar instead of calling it
  preserved.
- **XYZ tile addresses with credentials are blocked.** A `user:password@`
  in the address, and more key-style parameters (Azure `subscription-key`,
  AWS `X-Amz-Signature`/`-Credential`/`-Security-Token`, `sig`, `signature`,
  `password` and others, in any capitalisation), now stop the export like
  `api_key` did; the credential is kept out of the model and the reports.
- **A field name can no longer inject HTML into a popup.** A column the map
  draws with keeps its name in the data, and its popup reads a copy of
  the value under a safe name, so the popup shows the value instead of a raw
  placeholder; names are also written as text, never markup.
- **A hidden key column no longer travels as the feature id.** When a
  GeoPackage's key column is left out of the file (Popups unticked, or the
  field set to *Do not show this field*), features are numbered 1, 2, 3 in
  the export instead; atlas pages follow the new numbers.
- **Categorized and graduated layers draw the features QGIS draws.**
  Features that match no category or fall outside every range, NULL values,
  and values in a switched-off class are left out, as in QGIS, instead of
  appearing in grey or the nearest class. An "all other values" category
  (empty or NULL value) catches everything unmatched, a category holding
  several values keeps all of them, and a value equal to a range's upper
  bound stays in that range. Gaps and overlaps between ranges follow QGIS,
  and thresholds such as 1e-7 are no longer rounded to 0.
- **Rasters keep their QGIS appearance and position in web maps.**
  Brightness, contrast, hue and saturation, transparent colours, an alpha
  band and no-data settings you set in QGIS are now drawn into the exported
  image (a plain colour composite is still carried as it is), and a CRS you
  assigned to a raster that has none, or the wrong one, is used to place it.
- **A large raster no longer blocks a single-file export up front.** The
  file's size on disk only warns; the 100 MB limit is checked on the
  converted raster during the export.
- **Basemap and terrain count as network use**: a map exported with either
  is no longer described as working offline in the export's dependency list.
- **Exports no longer read QGIS's project from a background thread.** The
  dialog and the Processing algorithm now read layer settings and styling on
  the main thread and hand the background job a snapshot, as QGIS advises, so
  a layer removed or edited mid-export can no longer reach a half-finished
  read. Reading the features, converting rasters and writing the file still run
  in the background.
- **Cancel and the progress bar cover the whole export.** The bar shows raster
  conversion as well as reading, and the Processing algorithm reports progress
  and honours its Cancel button. A Cancel before the file is put in place
  leaves the file that was already there untouched; once it is being saved the
  export finishes and reports success rather than "cancelled" over a written
  file. A cancelled raster conversion is a cancellation, not a blocked layer.

## [0.2.1] - 2026-10-13

### Fixed
- **QGIS 3 could close while the Fidelity tab sized its rows.**
- **Message boxes were unreadable on QGIS 3** after switching to the dark theme.
- **Layer-row drop-downs were cut off, and menus opened too narrow, on QGIS 3.**
- **Opening a layer on the Layers list showed mostly blank space.** Its settings
  rows are sized to their controls, stack when the list is narrow (as in the
  Cartograph panel), the list keeps its scrollbar so the tick columns stay put,
  and an opened layer stays open when the list refreshes.

### Changed
- **Release announcements check plugins.qgis.org every 30 minutes**, reading
  the plugin's own page rather than the full index.

## [0.2.0] - 2026-10-12

### Added
- **Raster layers export.** A GeoTIFF goes inside a single HTML file (up to
  100 MB of imagery, still opens by double-click) or beside the page in a
  Folder or Share ZIP, in the colours QGIS draws it.
- **XYZ tile layers export as live layers.** An address carrying an access key
  is refused rather than published.
- **A redesigned dialog**: separate Preview, Share and Export buttons, a
  live-preview switch, a Settings tab, and light and dark themes.
- **Seven languages**: English, Portuguese (Brazil), Chinese (Simplified),
  Hindi, Spanish, Arabic and French.
- **Crowded labels wait for the zoom where they have room.**
- **Dense point layers merge into count bubbles** that split as you zoom in,
  sized on a log scale of their count; clustering can be switched off per
  layer.
- **Point names beside every point**, from a chosen field, with a halo.
- **Scale-dependent visibility carries to the web map.**
- **Ctrl+Alt+W opens the dialog** (Cmd+Alt+W on macOS).
- **The Processing tool can export even if some items are blocked.**

### Changed
- **Labels are sized in the right unit** and stay readable over imagery.
- **Relief imagery moved from CARTO to NASA GIBS.**
- **The OnlyMap runtime moves to 0.13.1.**

### Fixed
- **A labelled 3D polygon layer aborted the export** with "too many values to
  unpack".
- **Large Folder exports drew no features.**
- **Dark-mode message boxes were unreadable.**
- **OpenStreetMap basemaps show "Access blocked"** in a map opened from a file;
  the dialog now warns about it.
- **The Fidelity report stays accurate** when the export type changes or layers
  change in QGIS.
- **The save path follows a moved or shared project.**

## [0.1.4] - 2026-08-26

### Changed
- **The pinned OnlyMap runtime moved from 0.6.1 to 0.6.20.** The pin had been
  held since 2026-08-17 by a regression that stopped the runtime honouring
  `dash` — a dashed line rendered identically to a solid one — which was fixed
  upstream in 0.6.14. The bump also brings a page-wide spacebar fix (every map
  was calling `preventDefault` on it, breaking space-to-scroll and
  space-to-activate on focused buttons, which is an accessibility bug live in
  every map exported so far), line/polygon completion on iOS WebViews, and a
  licence key that cannot verify now saying so instead of silently dropping to
  the free tier. Verified across all four tiers plus a new pixel-diff of every
  rendering path we emit: 8 of 9 scenes byte-identical to 0.6.1, the ninth
  differing on one edge of an extruded polygon by 0.038% of the frame.

### Added
- **A "Host" call to action, in the dialog and in the preview.** Hosting a map
  on NIKA's servers does not exist yet; the button opens a short form so the
  demand can be counted before an auth flow, blob storage and an expiry job get
  built on a hypothesis. The preview's version is a dismissible chip that never
  reaches an exported file — a release gate asserts it stays out.

### Fixed
- **Labels are visible.** Every release up to `0.1.3` exported label layers that
  rendered *nothing*. A bare `TextLayer` reads each row's position through
  `getPosition`, and a row here is a GeoJSON feature, so without an accessor
  pointing at the geometry every label was positioned at `undefined` and the
  layer drew empty — silently, with no console error, the layer still listed in
  the legend and the layer switcher. It survived an earlier round of fixes to
  the label *styling* attributes, because getting a label's colour right does
  nothing for a label with no place to be. Maps already exported are affected
  and need re-exporting; a browser test now counts glyph pixels, with an
  unlabelled control so the count means something.
- **Scale-dependent visibility is no longer claimed, because it never worked.**
  A layer set to show only between two scales was exported with
  `visible-min-zoom`/`visible-max-zoom`, which the map library rejects on a
  vector layer — every affected map logged `Unknown attribute ... likely a
  typo` in the recipient's console and showed the layer at every zoom anyway.
  The two values were also paired the wrong way round, so the minimum came out
  larger than the maximum. The export now says so on the Fidelity tab instead
  of pretending, and the documentation no longer claims the feature. Exported
  maps get a browser test asserting the map library complains about *nothing*,
  which is the check that would have caught this and the label bug both.
- **The hosting page described a product that does not exist.** It opened by
  telling every reader that "every exported map carries a Host with OnlyMap
  link" — removed months ago because it 404ed — then walked through an upload
  confirmation and a visibility table for a service that was never built, and
  closed by claiming nothing in the artifact phones home to NIKA, which the
  usage report contradicts. Rewritten as what is actually true: host it
  yourself anywhere static, NIKA hosting is not built, here is what the map
  does on the network. The plugin listing no longer promises hosting either.
- **The Fidelity tab said labels never appear over relief.** They do — the map
  library stops honouring their pixel size once terrain is on, so they shrink to
  a few pixels and squash while the layer's markers keep their size. The old
  wording was written when labels were invisible everywhere, for the reason
  above. Reported upstream; the tab now describes what actually happens.
- **Every guide in the Help tab printed its title twice.** Most guides open with
  their own heading, because the same files are standalone pages on the website,
  and the tab prepended the title again on top of it.
- **Exported maps stamp the version that made them.** The generator line
  carried a hardcoded `0.1.0` from the first release onwards, so every map
  exported by 0.1.2 and 0.1.3 claimed to come from 0.1.0. The version is now
  read from `metadata.txt` — the file a release always bumps — so a second copy
  cannot drift from it, and a test ties the two together. The line exists for
  whoever opens an exported file later and asks what produced it, which is the
  one reader who has no other way to find out.

## [0.1.3] - 2026-08-17

### Added
- **Textured 3D relief.** The runtime replaces the basemap while relief is on,
  which used to leave a plain grey terrain shell. The exporter now drapes the
  chosen basemap's raster imagery over the relief (`terrain-texture`), and
  vector symbology paints onto the slopes - including extruded layers, whose
  colour drapes over the mountains (column height deliberately does not show
  while relief is on; the Fidelity tab says so). Verified in a browser against
  the pinned 0.6.1 runtime. Liberty and Bright have no
  raster twin and still show plain relief; the Fidelity tab and the dialog
  warning name every host relief streams from, including unpkg.com, which the
  runtime's terrain-mesh decoder loads worker scripts from.

### Fixed
- **A layer's labels are named as its labels.** The companion text layer was
  listed as "Peaks labels", which reads as a layer with that name rather than
  as the labels belonging to "Peaks"; it is now "Peaks (labels)". The two sit
  next to each other in the layer switcher, so the relationship has to be
  legible at a glance.
- **A labelled single-layer map keeps its layer switcher.** The switcher is
  left out when there is nothing to switch, but the test counted layers in the
  QGIS project rather than layers on the map. A labelled layer travels with a
  second, independently toggleable text layer — the legend listed both entries
  while the switcher that would have let a reader hide the labels was suppressed
  on the same screen. Unlabelled single-layer maps still get no switcher.
- **Relief exports say that labels will not appear.** Label text renders
  neither draped nor offset while terrain is on — a runtime limitation,
  measured — so the Fidelity tab now names every labelled layer that will
  draw without its text, instead of the recipient discovering it.
- **Relief exports say that polygon outlines will not draw.** The terrain
  drape keeps polygon fills but drops their borders — a runtime limitation,
  measured — so overlapping translucent areas lose the edge that tells them
  apart. The Fidelity tab now names every outlined polygon layer affected.
- **Relief maps stop the camera where the relief still renders.** The public
  elevation tiles end at z14 and the runtime blanks the surface rather than
  magnifying past it; the runtime also has no camera-cap attribute yet. Relief
  exports now carry a small page script - built only on the element's public
  `om-view-changed` event and consumer-testing `setViewInternal` method - that
  glides the camera back to ~1:2.5 km whenever a gesture passes it. Deleted in
  favour of the real attribute the day the runtime grows one.
- **Per-class marker outline colours survive the export.** Categorized and
  graduated markers emitted only the first class's stroke colour, so a layer
  of white station dots ringed in each class's colour exported ringed all in
  one colour. Outline colours now get the same per-class expression the fill
  has always had.
- **Closing the export dialog removes its live-preview files.** Previews are
  written to the system temp directory to be served by the dialog's own
  localhost server; they used to stay behind after the dialog closed - a few
  megabytes per project, forever, on Windows, where nothing else clears the
  temp directory. The dialog now deletes the project's preview on close and
  sweeps previews older than a week, which also collects those orphaned by a
  crash or a renamed project. The preview server itself already shut down
  cleanly on close.
- **Categorized and graduated line layers no longer export placeholder grey.**
  The class-colour expressions read only the fill colour; a line symbol keeps
  its colour in the stroke, so an MRT network in official line colours came
  out uniformly `#888888`.
- **Extrusions survive sessions that never opened a 3D view.** QGIS registers
  3D renderers lazily, so a project loaded before that parsed its 3D renderer
  XML to nothing and the export silently lost every extrusion. The plugin now
  registers 3D support when it loads.
- **Popup fields the web template cannot reference are renamed instead of
  leaking.** A field named `Last Known Eruption` left the literal
  `{{Last Known Eruption}}` in every popup; such fields are now renamed in the
  exported data, the label keeps the original spelling, and the Fidelity tab
  lists every rename.
- **The fidelity report names the right host for the Positron basemap.** The
  pinned runtime serves positron from openfreemap.org, not carto.com.
- **The runtime's attribute schema is cached beside the fetched runtime**, so
  the test suite's attribute contract checks run against the pinned build
  rather than a stale local mirror.

### Changed
- **New mark.** The folded-plane placeholder is replaced by the QGIS2WebMap
  mark: OnlyMap's stacked map slabs projected from a holographic emitter, with
  an extruded city on the top slab. It ships at **two optical sizes**, because
  QGIS draws toolbar icons at 24 px and at that scale the full artwork's
  0.7-unit streets land at 0.26 px - sub-pixel geometry is averaged away rather
  than drawn, so the detail dissolves into a green blob. The simplified mark
  (`docs/assets/mark.svg`, and the identical plugin icon) drops the emitter and
  glow, uses two slabs, one bold street and a heavier border, and holds together
  at 16 px; the full artwork (`docs/assets/logo.svg`) is for large use only.
- The plugin's `about` text now opens with a link to the documentation site.
- The README carries a logo banner.

## [0.1.2] - 2026-08-06

First public release. `0.1.0` and `0.1.1` were built and submitted but never
published, so everything below is what ships on day one.

### Fixed
- **The runtime lock file no longer trips a secret scanner.** The QGIS plugin
  repository runs detect-secrets over every upload, and a 64-character hex
  string is exactly what an API key looks like to one - so both SHA-256 digests
  in `runtime-lock.json` were reported as "Potential Hex High Entropy String"
  and the upload was held. They are integrity checks over published, public npm
  artifacts, and publishing them is the entire point, so the digests are
  unchanged; each line now carries detect-secrets' own `pragma: allowlist
  secret`, which is the mechanism the tool provides for saying so. The pragma
  only works on the same line as the digest, so `scripts/lock_runtime.py`
  appends it after serialising rather than adding a sibling key. Re-encoding the
  digests as base64 Subresource Integrity was measured first and rejected: it
  merely swaps the hex finding for a Base64 High Entropy String one.

### Changed
- **`qgisMinimumVersion` is now 3.44, corrected from 3.22.** The old figure was
  never true. Running the QGIS tier in older containers rather than only against
  LTR: 3.22.16 gives 69 failures, 3.34.5 gives 64, and 3.36.1 fails too - three
  APIs the reader depends on do not exist there. `QgsJsonExporter`
  `setDestinationCrs` arrives in 3.34, and `QgsMapLayerServerProperties`
  `attribution` later still. 3.44 LTR is the version measured green, so it is the
  one declared. Widening the range again means adding fallbacks for both and
  re-measuring - the exporter one looks free, since older `QgsJsonExporter`
  already emitted WGS84 unconditionally.

- **The pinned OnlyMap runtime moved from 0.6.0 to 0.6.1.** The licence gate is
  byte-identical between the two - the dev-context test and all three caps -
  so `license_policy.py` is untouched. The bundle grew 7.85 MB -> 8.04 MB, and
  the runtime's own `LICENSE.md` is unchanged. All four tiers re-run green
  against it.

  0.6.1 adds **`selection-type` on `<om-overlay>`**, which is the upstream fix
  for the ghost-popup bug we reported: a click-opened popup re-anchored and
  re-interpolated its template on every *hover* pick, so it followed the pointer
  onto other features and rendered them into the wrong template. The exported
  Exported popups now carry it, scoped to whichever pick type opens them.

- **The free-tier cap warning is gone from the export flow.** It pushed a QGIS
  message-bar warning before every over-cap export and listed the breach again
  in the post-export dialog. Since the runtime's 0.6.0 licensing rework the caps
  apply only on a hosted `http(s)` page, so for the Standalone HTML and Share ZIP
  this plugin produces there was nothing to warn about, and a warning that is
  almost always irrelevant trains people to dismiss the one that is not. The
  **Fidelity** tab still names every layer over a limit, and the limits are now
  documented in the README as well as in `docs/supported-features.md`.

- **Exports no longer mount the runtime's validation panel.** `validate` was set
  on `<om-map>` whenever a cap was breached, so the recipient would be told why a
  layer was missing. No layer *is* missing on a local map, so the runtime
  validated a clean map and rendered its green success badge at
  `top: 12px; right: 12px` with `z-index: 9999` - directly over the legend. A
  clean deliverable wore a diagnostic badge for no reason.

### Fixed
- **Qt6-compatible enum spellings throughout.** Sixteen call sites used the
  unscoped forms Qt5 allows - `Qgis.Warning`, `QgsWkbTypes.PointGeometry`,
  `QgsGraduatedSymbolRenderer.EqualInterval`,
  `QgsBlockingNetworkRequest.NoError` - which PyQt6 does not, so they would each
  raise `AttributeError` on a QGIS built against Qt6. All now use the scoped
  spelling, verified to resolve identically on QGIS 3.22 and on 3.44 LTR.

- **A narrower `except` when probing a layer's provider.** `providerType` is a
  C++ binding and can raise once the underlying object is gone, but catching
  bare `Exception` and continuing also swallowed real bugs in this plugin -
  bandit's B112. Now names the three errors that can actually occur.

- **A popup no longer follows the pointer onto other features.** Clicking a
  river and then merely moving the pointer across an airport left the river's
  popup open, still titled "Rivers", with the airport's object interpolated into
  its rows - so every field it named came out blank. Click and hover picks share
  one selection internally, and a selection-anchored overlay re-anchored *and*
  re-rendered its template on any pick, whatever had opened it. There was no
  author-side workaround, so it was reported upstream; runtime 0.6.1 answered it
  with `selection-type`, and exported popups now declare the pick type that
  opens them. A click on empty space still dismisses a click popup.

  What this does *not* change: clicking the same feature a second time still
  does not close its popup. That needs an action the runtime does not have -
  there is no `toggle-overlay`, and nothing can read whether a popup is already
  open for the feature under the cursor.

- **The credit chip no longer sits under the runtime's own attribution.** Runtime
  0.6.0 mounts mandated chrome into its widget slots - the licence badge into
  `bottom-start`, the provider attribution into `bottom-end` - and the
  `bottom-end` container is anchored at exactly the `bottom: 12px; right: 12px`
  our chip claimed. They drew on top of each other. The chip keeps the corner
  and the attribution is lifted above it, reserving one line or two depending on
  whether the map carries a data credit.

- **The bottom-left widget stack is evenly spaced again.** The template forced
  `bottom: 58px !important` on the zoom controls and `30px` on the scale bar, to
  clear a licence notice the runtime once drew loose in that corner. It now puts
  the badge in the `bottom-start` slot alongside them, and that slot spaces all
  three itself - so the offsets only shifted widgets up out of a flex flow that
  still reserved their original space, leaving a ragged gap. Removed.

### Removed
- **The OnlyMap licence field on the Map tab.** With the caps lifted on every
  origin this plugin's artifacts are opened from - a file, `localhost`,
  `127.0.0.1` - a key changed nothing for almost everyone who saw it, while
  reading like a prerequisite. A key still reaches the writer from the
  `ONLYMAP_LICENSE_KEY` environment variable or the Processing algorithm's
  licence parameter, which is also the only route that works on a machine with
  no QGIS profile.

### Added
- **Export only the features in the current QGIS view.** A separate option from
  the one that decides where the map opens, because they are separate decisions:
  choosing to open on the current view used to export every feature anyway, and
  folding the two together would mean anyone framing their working view silently
  shipped a map missing everything outside it. Features outside the view are
  never read, so the provider's spatial index does the work. This is also the
  practical way to bring a very large layer under the free plan's
  25,000-feature limit. Every layer reports what it lost in the Fidelity tab -
  clipped-away features leave no gap on the map, so nothing else could tell you.

- **Batch and model exports use the licence key.** The Processing algorithm
  takes an optional key parameter and otherwise falls back to
  `ONLYMAP_LICENSE_KEY`, then to any key already saved on this computer. It read
  none of them before, so a paying customer's batch export silently produced
  free-plan maps. The environment variable is the only route that works on a machine with
  no QGIS profile - a server, a container, CI.

- **Dashed and dotted lines.** Both ways QGIS produces one now export: the
  line-style dropdown, which sets a Qt pen style, and a hand-built custom dash
  pattern. Only the second was read before, so the dropdown - which is what most
  people actually use - exported solid lines. The pattern is converted from
  pixels to deck.gl's line-width units at the writer, so a dash keeps its rhythm
  when the line gets thicker; a QGIS dash-dot is drawn as a plain dash of the
  same length and reported, because deck.gl strokes exactly one on/off pair.

- **A licence key field for OnlyMap subscribers.** *Superseded later in this
  same unreleased batch - the field was removed once the runtime lifted the caps
  on every origin this plugin exports to. See Removed, above. The plumbing it
  describes is still there and still reachable.* Everything below it already
  existed - `LicensedPolicy`, the verdict's key, the `license-key` attribute on
  `<om-map>` - but nothing ever supplied a key, so every export ran on the free
  plan whether or not the user had paid. The key is stored on the machine rather
  than in the `.qgz`, because a project file gets emailed and committed.

  The field reads the key's own payload and reports the plan, the domains it
  covers and its expiry. It warns about the case that otherwise fails silently:
  a key is matched against `location.hostname`, and a Standalone HTML file
  opened by double-clicking has none, so a domain-locked key does not apply to
  it.

- **A destination field on the Map tab**, with a Browse button, remembering the
  folder between exports and correcting the file extension to match the chosen
  packaging. The location was previously settable only through a file dialog
  that appeared *after* pressing Export, so nothing on screen suggested it was
  the user's to choose.

- **A progress bar and a Cancel button** for reading, previewing and exporting.

- **Label text case and line breaking.** QGIS's uppercase / lowercase /
  capitalise / title case, its wrap character, and its automatic wrap length
  now reach the exported labels. All three are applied to the label *text*
  rather than emitted as styling, because the web renderer has no
  `text-transform` and QGIS does not have one either — it changes the glyphs it
  draws.

  The character-set collection had to move ahead of it: `text-character-set`
  *replaces* the runtime's font atlas, so collecting glyphs from the raw field
  values while drawing uppercased ones would leave every accented capital out of
  the atlas, and those labels would render blank — present, positioned, and
  invisible. Found by diffing our label coverage against GeoLibre's.
- **Real markers.** A point layer using an SVG file, one of QGIS's ~40 marker
  shapes, or a stack of symbol layers is now drawn *by QGIS* into a sprite sheet
  carried inside the exported file, and the map draws from that. Nothing about
  the symbol is re-created in the browser, so parametrised SVG fills, stacked
  markers, sizes and rotations are correct by construction rather than by
  translation.

  This is the incumbent's single most visible gap: qgis2web draws every marker
  as a circle in both of its renderers and says nothing about it
  ([qgis2web#1218](https://github.com/qgis2web/qgis2web/issues/1218)).

  The layer stays a `GeoJsonLayer` and switches its point sublayer with
  `point-type="icon"`. Becoming an `IconLayer` was the obvious-looking route and
  is much worse: an `IconLayer` takes rows with a position accessor rather than
  GeoJSON, so it would mean shipping every coordinate a second time and dropping
  any non-point geometry sharing the file.

  Rasterisation is per layer, not per symbol - one SVG class pulls the layer's
  plain dots in with it - because splitting one QGIS layer across two deck.gl
  layers would make them fight over draw order. A layer needing more than 256
  distinct marker appearances, or a sheet past the 4096-pixel texture limit some
  hardware imposes, keeps its circles and says so, rather than drawing some
  markers correctly and some not.
- **A legend that cannot disagree with the map.** When a layer's markers were
  rasterised, its legend swatches are cut from that same rendering, as a static
  `<om-widget>` carrying image swatches. Every other project keeps the runtime's
  built-in legend, which is interactive and follows layer visibility - forking
  it unconditionally would mean maintaining a legend forever against a runtime
  that improves its own.

  The custom legend carries no script: an `om-widget` with no type and no
  `om/widget` block is purely static, so this stays markup. Hosting it behind a
  strict Content Security Policy now needs `img-src data:` as well as
  `unsafe-eval`; both are documented in the hosting guide.
- **Extruded polygons**, read from both of the unrelated places QGIS keeps a
  height: the layer's 3D View properties (a fixed extrusion height or a
  data-defined one, plus *Show edges* as a wireframe) and the 2.5D renderer,
  whose height is not on the renderer at all but in the project variable
  `qgis_25d_height`.

  A map with anything raised on it now opens tilted. That is not a style
  preference: looking straight down, an extruded map and a flat one are the same
  picture, so without it the feature is invisible and reads as broken.

  Not carried, and each reported: a base height, because deck.gl extrudes from
  ground level; a height written as a QGIS expression rather than one field or
  one number; 3D point symbols, which are meshes with no equivalent; and
  extruded lines, which deck.gl draws with a layer that has no elevation.
- **Opt-in terrain**, defaulting to flat. This is the one setting that is *not*
  a translation of the project: a QGIS terrain is a DEM on the author's disk and
  a web map needs fetchable elevation tiles, so what is offered is global relief
  from a public tileset, with the same recipient-side cost as a basemap. A
  project whose own terrain is not flat gets a fidelity note either way - saying
  the relief is global rather than theirs, or that it exported flat.
- **Per-class symbol size and line width.** A graduated-by-size layer used to
  export every class at one radius, so a map whose entire point was "bigger dot
  means more" came out uniform. Each class now keeps its own size, as a
  threshold scale matching the class breaks already used for colour.

  Threshold rather than a continuous `sqrt`/`log`/`pow` scale on purpose: a QGIS
  graduated renderer assigns each class one discrete size, and interpolating
  between them would draw sizes the author never chose. The continuous scales
  belong to QGIS's data-defined size assistant, which needs a data-defined
  override reader that does not exist yet.
- **Line cap and join style.** Measured against QGIS rather than assumed: a
  default simple line is square-capped and bevel-joined, which is also deck.gl's
  default, so these are emitted only for the lines a user deliberately rounded -
  where the export used to square off every dead end.
- **The rest of the label properties**: the placement quadrant (as a text anchor
  and baseline), pixel offset, rotation, bold, and the background shield with its
  padding. Italic is dropped with a fidelity note, because the web renderer
  builds its font from a family and a weight only.

  Marker rotation and offset are read here and used by the symbol atlas above -
  rotation drawn into the sprite sheet by QGIS, offset emitted as
  `get-icon-pixel-offset`.

### Fixed
- **The hover highlight colour was inert.** `highlight-color` is a plain
  attribute, not one of the `get-*` accessors written in the expression
  language, so the runtime hands its raw string to deck.gl unchanged. We quoted
  it. deck.gl accepts an array or a bare hex and rejects everything else without
  a word, so every export highlighted in deck's default navy while the file, the
  dialog and the unit tests all agreed the chosen colour was there. Now emitted
  bare. The browser tier reads the resolved layer prop, which is the only place
  the difference is visible.

### Changed
- **The free-tier limits no longer apply to a map you open locally**, following
  the pinned runtime's move to 0.6.0. The caps - 5 layers, 25,000 features per
  layer, and a 20 MB fetch limit - now apply only to a page served over
  `http(s)` from a real domain. A file opened from disk, any non-web address,
  `localhost`, `*.localhost`, any `127.x`, `::1` and `0.0.0.0` are all exempt,
  and the attribution badge stays in every case. Since the plugin's default
  output is a single file the recipient double-clicks, most exports are now
  uncapped.

  Everything we say about the caps changed with it. A breach is reported in the
  Fidelity tab as **approximated rather than unsupported**, because it is now a
  conditional consequence of publishing rather than a fact about the artifact -
  reporting a certain failure that will not happen is how people learn to skip
  the tab. The message bar and the licence-key note say the same thing. We still
  detect and report every breach: a Folder or Share ZIP put on a web server is
  capped, and so is the same project exported by someone else.

  One thing got smaller rather than larger: a domain-locked key still does not
  apply to a double-clicked file, but that used to bring the caps back and now
  costs only the on-map badge. The dialog says so in passing instead of in red.

- **Uncapped is not the same as licensed.** The runtime's licence gained a
  clause saying that technical limits are a convenience and that their
  "presence, absence, or failure" grants nothing - commercial use, explicitly
  including distribution inside a packaged or installable application, needs a
  commercial key regardless. `docs/supported-features.md` states this and points
  at NIKA. Nothing in the plugin checks it, and nothing should: it is a legal
  condition, not a technical one.

- **The pinned OnlyMap runtime moved from 0.5.11 to 0.5.12 to 0.6.0** over the
  course of one day. 0.5.12 carried one upstream fix, for `highlight-color`
  being compiled to a function accessor on the library's own selection path;
  0.6.0 is the licensing rework above. The runtime's own `LICENSE.md` ships with
  the plugin and was updated with it.

- **The OnlyMap credit in every exported map points at the documentation site.**
  `onlymap.nikaplanet.com` rather than the product page, so someone who clicks
  the corner credit lands on the reference for the library drawing what they are
  looking at. The NIKA link is unchanged.

- **The size warning no longer talks about email.** Standalone HTML is still
  reported as the largest of the three and still warns past 20 MB, but the
  reason given is that a file that big is slow to open and awkward to move
  around - which is true wherever it goes. Attachment limits are one
  destination among several, and `docs/sharing.md` is where they belong.

- **The destination group is called "Filepath".** Its explanatory line is gone:
  a path field next to a Browse button does not need one.

- **Reading, previewing and exporting run on a worker thread.** All three ran on
  the GUI thread, so a large project froze the whole window for as long as the
  work took - which is indistinguishable from a crash, and was reported as one.
  The pipeline was already safe to run off the main thread: the Processing
  algorithm has no `FlagNoThreading`, so QGIS has been running it in the
  background all along.

- **A chrome-only setting no longer re-reads the project.** With live preview on
  - the default - every settings change ran a full read of every feature of
  every layer, including changes that cannot affect one. Unticking "Legend" now
  reuses the last read. `DialogState` splits into the fields that can change the
  exported data and the fields that only restyle the map.

- **The dialog can be made shorter.** Its minimum height was whatever its
  tallest tab wanted, so on a laptop screen the window could be grown and never
  shrunk, and its foot - with the Export button on it - sat behind the Windows
  taskbar. The tabs scroll, and the opening size is clamped to the screen's
  available area.

- **An oversized Standalone HTML export warns instead of switching itself.** It
  used to move the selection to Share ZIP on the user's behalf while the radio
  buttons stayed on Standalone HTML, so the dialog said one thing and wrote
  another. The size rule is now stated on the Map tab beside the choice it
  constrains, and the export proceeds if that is what the user wants.

- **The marker-shape fidelity note moved to the symbol atlas.** It used to fire
  once per class from the symbol translator, saying a shape *may* be
  approximated. On a point layer that is now false - QGIS draws the shape
  itself - so the note is emitted once per layer, after rasterising, and says
  what actually happened. Markers on a line or polygon layer, which a sprite
  sheet cannot reach, are still reported as unsupported.
- **The pinned OnlyMap runtime moved from 0.3.3 to 0.5.11**, 18 releases in one
  step, with all three tiers re-run against it. The bundle grows from 5.6 MB to
  7.9 MB, which is a one-off download on first export rather than anything the
  exported file carries. The runtime licence is unchanged, so the consent gate
  is unaffected.

  `scripts/check_runtime_updates.py` now runs at the end of every
  `package_plugin.py` build and says when the pin is behind. It is advisory and
  can never fail the build - offline, an unreachable registry and a trimmed
  checkout all degrade to a quiet line - and it never moves the pin, because the
  pinned build is the one every tier is green against.

### Fixed
- **Hidden attributes were written into the exported map anyway.** Unticking
  **Popups**, and setting a field to *Do not show this field*, both suppressed
  only the popup: every attribute value still reached the GeoJSON, in plain
  text, for anyone who opened the artifact in an editor. The dialog and the
  guide meanwhile told users this was how to keep data out of a map they were
  sending someone - a data-protection promise the export did not keep. The
  values are now stripped from the data itself. Fields the map *draws* with -
  a categorised or graduated renderer's field, a label's source, an extrusion
  height - survive being hidden, because removing them would break the drawing
  without concealing anything on screen.

- **The free-tier row cap was documented as dropping a layer; it truncates it.**
  Verified against the runtime bundle: past 25,000 features the layer renders
  its first 25,000 in source order rather than nothing. The map shows the
  recipient a dismissible notice, but its legend, filters and statistics
  describe only the drawn subset. The fidelity report now says how many features
  are missing rather than claiming the layer will not appear.

- **Qt enums were read in a way that works on PyQt6 and silently fails on
  PyQt5**, which is every QGIS LTR install. `penStyle()` returns a bare `int`
  there, with no `.name`, so the first dashed-line implementation found nothing
  and exported solid lines. Enum reads now handle `.name`, `int()` and `.value`.

- **The runtime download size was stated in four hand-written places** and went
  stale on the 0.3.3 → 0.5.11 bump, so the licence dialog promised "about 3 MB"
  for a 4.5 MB download - at the exact moment a user decides whether to accept.
  One constant now, `RUNTIME_DOWNLOAD_SIZE`, with a unit test asserting the
  installation guide quotes it and that no other guide states a different figure.
- **The Processing algorithm failed *after* doing all the work.** A first run on
  a machine where the runtime licence had not been accepted read the project,
  translated its symbology, logged the full fidelity report, and only then
  refused - 50 seconds, all of it discarded. The licence gate is a precondition
  and is now checked before anything is read, so the same message arrives as a
  prompt rather than as a crash. `RuntimeProvider` gained `preflight()`, which
  answers cheaply and never downloads.
- **Popups piled up instead of replacing each other.** Reported from a real
  project: hovering a spot covered by three layers left all three popups stacked
  on the same coordinate, so only the top one could be read - and a layer
  switched off in the layer switcher still showed its popup.

  One cause for both. `show-overlay` sets `visible="true"` and nothing ever sets
  it back, and the runtime dispatches behaviours only when there *is* a pick -
  there is no unhover event. So every popup ever opened stayed open.

  The fix is a `hide-overlay` behaviour per popup, carrying **no `layer`
  attribute** so it fires on every pick, emitted *before* the layers: behaviours
  dispatch in document order and synchronously, so every popup closes and then
  the one whose layer matched re-opens. Only the triggers actually in use are
  emitted, so an all-click project gets no hover behaviour and vice versa.

  Scoping each overlay with `layer="..."` is the fix that suggests itself and is
  wrong: an overlay hides *because* an unscoped one follows a null selection to
  nowhere, so scoping it would have traded a stacking bug for a popup stranded
  on screen after the cursor left every feature.
- **A geometry generator exported silently, in an arbitrary colour.** QGIS's
  geometry generator draws the result of an expression - `buffer($geometry, 50)`,
  `centroid($geometry)` - rather than the feature's own geometry, and that shape
  is not in the exported data at all. The generator answers `color()` like any
  symbol layer, so it flattened to a plain coloured layer and recorded **nothing**
  in the fidelity report: the exact silent loss this project exists to prevent.

  It is now reported by name, with the expression quoted and the Processing-tool
  workaround stated, and the sub-symbol's colours are read so the layer at least
  draws in the colours the author chose. Found by diffing our property coverage
  against GeoLibre's style model.
- **A 2.5D layer exported grey, losing its symbology entirely.** `Qgs25DRenderer`
  fell through to the unsupported branch. It is not a wrapper - `embeddedRenderer()`
  is null - and its symbol is machinery rather than styling: two of its three
  symbol layers are geometry generators that fake the walls, so reading the top
  one the usual way finds nothing usable. Its colours come from `roofColor()` and
  `wallColor()` instead.
- **`docs/supported-features.md` claimed stacked symbol layers export the bottom
  one.** They have always exported the *top* layer - the one actually visible -
  which is what the code does and what the fidelity report says.
- **An opt-in basemap**, defaulting to none: OpenStreetMap, Positron, Dark
  Matter, Voyager, Liberty or Bright. The MapTiler presets the runtime also
  registers are deliberately not offered - they need an API key, which the
  exported file would have to carry in clear text for every recipient to read
  and spend.

  The warning beside it is about the network, not the file: **tiles are streamed,
  so a basemap does not make the export any bigger.** What it ends is the "opens
  offline, contacts nobody" guarantee, and it does so on the recipient's machine
  rather than the author's - which is why it is also written into the fidelity
  report, naming the provider being depended on, rather than living only in a
  dialog the user has already left.

  Tiles are not bundled for offline use and will not be. Beyond the size - the
  Alaska region alone is ~6.5 GB across zoom levels 0-12 - the OpenStreetMap
  [tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
  prohibits bulk downloading and offline use outright.
- **Caption positions for top centre and bottom centre**, and top centre is now
  the default. All four corners were already occupied by map chrome - the layer
  switcher, the legend, the zoom controls and scale bar, the credit component -
  so every corner a user picked collided with something. The centres are the only
  free space, and the position list now says what each corner shares.
- **The map title is drawn by default**, and the legend gives up its own heading
  while it is, so the title appears exactly once rather than twice.
- **A size control for the map chrome** - Small through Largest - scaling the
  legend, layer switcher, zoom controls, scale bar and caption together. The
  credit component is deliberately excluded: attribution carries licence
  obligations, and a control that can shrink it towards illegibility is a
  control for quietly failing to attribute.

  Implemented as `transform: scale()` rather than a font size, because measuring
  proved the obvious approach insufficient. Setting `font-size` on a widget host
  *does* reach inside its shadow root - text went from 12px to 24px at scale 2 -
  but the controls did not follow: the zoom buttons stayed 30x60 and the scale
  bar stayed 81x23, their internals being sized in pixels. Text grew while the
  things you click did not. A transform scales the rendered box and its contents,
  with `transform-origin` per widget so each grows into the map from the corner
  it is pinned to. The fixed bottom-left offsets that stack the zoom controls
  above the scale bar are scaled too - left alone, a scaled scale bar landed on
  top of the zoom controls.
- A **minimum drawn width of 2px for line layers**. deck.gl hit-tests against
  what it drew, so a QGIS hairline exported as a sub-pixel line that was visible
  and effectively impossible to click - making its popup unreachable. Lines only:
  on a polygon this is the outline, and thickening that would eat into small
  features for no benefit, since a polygon is picked by its interior.
- A **fidelity warning when the project has a tile layer and the export has no
  basemap**, which is how a map of bare shapes on white gets sent without anyone
  noticing until the recipient asks what they are looking at.
- **A light and dark theme switcher on the documentation site**, following the
  reader's system preference by default and remembering an explicit choice. It
  lives in `_includes/head-custom.html` rather than in the pages, because the
  plugin's Help tab renders those same Markdown files with Qt and understands no
  Jekyll syntax.
- **One-line explainers under the settings that lacked them** - map name, extent,
  control colours, caption and basemap. Several previously explained themselves
  only in a tooltip, which is invisible unless you already suspect there is
  something to read.
- **Live preview.** The preview is served from `127.0.0.1` on an ephemeral port
  and the open tab reloads itself as settings change, keeping the camera. A
  `Live preview` checkbox controls it, remembered per machine in `QSettings`
  rather than in the project, because it describes how someone works rather than
  what the map is. Nothing opens a browser on its own.

  `file://` is why this needs a server at all: Chrome treats file documents as
  opaque origins, so the page cannot be reached from the plugin - the camera
  script had already hit the same wall with `sessionStorage`. The server is
  loopback-only, serves one directory, refuses writes, and dies with the dialog.
  Change detection compares a snapshot of the dialog state rather than hooking
  eighteen widget signals, only eight of which marked anything dirty; a test
  fails if a field is added to `DialogState` without being added to the snapshot.
- **Open exported map**, enabled after a successful export. This is where the
  `file://` path is exercised - against the bytes that ship, rather than against
  a preview copy of them, which was the weaker check it replaces.
- **An always-visible fidelity strip** above the buttons, so what the export
  changes is readable from every tab instead of only from inside one. It stays
  empty when nothing changes: a permanent "0 changes" trains people to ignore it.
- **An export summary** on the Map tab, naming what pressing Export will produce.
- Visible help under the settings that most needed it, coordinate precision
  above all - the only control here that discards data, and previously explained
  only in a tooltip.
- `scripts/fetch_runtime.py`, used by CI to fetch the pinned runtime through the
  plugin's own download-and-verify path. Six of issue #29's release gates had
  been skipping for want of a runtime, and the code that reaches the network on a
  user's machine was the only code CI never ran.
- Repository bootstrap: plugin lifecycle (one Web-menu action + toolbar icon),
  export-dialog shell with a populated Help tab, NIKA iconography,
  packaging and package-verification scripts, and CI.
- Normalized export model (`core/export_ir.py`) - frozen dataclasses with
  deterministic snapshots, holding no Qt or PyQGIS types.
- Antimeridian-aware extent computation (`core/extent_math.py`), so a project
  crossing the 180th meridian opens on its data rather than on the whole world.
- Fidelity report covering suppressed settings as well as unsupported symbology.
- OnlyMap licence-cap detection, with enforcement behind a swappable policy.
- Project and layer readers translating single, categorized and graduated
  renderers, labels, popups and groups, and normalising all vector data to
  WGS84 GeoJSON.
- Marker shapes are captured rather than flattened to circles.
- OnlyMap manifest builder emitting declarative markup, using the canonical
  expression shapes so the legend renders categories and class ranges without a
  hand-built legend.
- `OnlyMapWriter` producing a self-contained `index.html` with the runtime, the
  styles and the data inlined - no network requests, no tracking - plus an
  `ArtifactResult` recording sizes, runtime provenance and a deterministic
  manifest snapshot.
- Lossless gzip packaging: the runtime is embedded compressed and inflated in the
  browser, taking a 5.45 MB artifact to 1.84 MB. The stylesheet is never
  compressed, because the no-JavaScript fallback depends on a pure-CSS rule.
- Dependency scanning before any bytes are written, so an export that cannot work
  for its recipient fails while there is still time to fix it.
- Three output tiers - Standalone HTML, Share ZIP and folder - with a README for
  the recipient in the tiers that need one.
- Export dialog with Map, Layers, Appearance, Fidelity and Help tabs. The map
  name is set in one place, widgets are on by default, and Export is disabled
  with the reason beside it rather than producing a broken artifact.
- The layer list follows the QGIS Layers panel live. Bursts of signals coalesce
  into one rebuild, and per-layer settings survive it because they are keyed by
  layer id rather than held in widgets - so there is no refresh button.
- Settings persist in the QGIS project, so reopening it restores export choices.
- Preview runs through the production writer to a stable path per project and
  opens in the user's default browser, remembering the camera across reloads.
- User guides for installation, first export, sharing, AI enhancement, supported
  features and privacy. The plugin's Help tab renders the same files the website
  serves, so the two cannot drift.
- GitHub Pages workflow serving `docs/`.
- Labels reach the map. QGIS labelling becomes a companion `TextLayer` fed by
  computed label points, with the font, colour, halo and character set carried
  across. Label points are their own reduced collection, so a labelled polygon
  layer does not embed its geometry twice.
- Layer attribution reaches the map. OnlyMap's own attribution control credits
  the basemap provider and 0.1.0 ships no basemap, so data credits render in the
  artifact's credit component.
- The OnlyMap credit component moved into the map's bottom-right corner, with
  both calls to action, a keyboard-reachable disclosure, and a collapse to the
  mark on small screens. Data credits stay outside the disclosure, because a
  licence obligation behind a click is not discharged.
- Processing provider with an `Export to OnlyMap web map` algorithm, so export
  works from the toolbox, the modeller and `qgis_process`. It adapts the same
  reader, writer and exporters the dialog uses rather than reimplementing them.
- `runtime/runtime-lock.json` pins the OnlyMap build the plugin is tested
  against, regenerated by `scripts/lock_runtime.py`. An unexpected runtime is
  reported as a warning on the export rather than shipped silently.
- Fixture tier (`tests/fixtures`) running five projects through issue #29's
  release gates, and a browser tier (`tests/browser`) checking on Chromium,
  Firefox and WebKit that the map mounts, makes no network request, and that the
  credit component is keyboard-reachable without covering the map controls.
- `docs/hosting.md`, and both new test tiers wired into CI.
- Output-mode eligibility (issue #29): when a project is too large to travel as
  one HTML file, the dialog says exactly why and moves the selection to Share
  ZIP rather than writing a file most mail services will reject.

- The OnlyMap runtime is fetched rather than vendored, once per computer. QGIS
  requires that all code in a plugin be available in source form and refuses
  plugins carrying binaries, so a 5.7 MB minified closed-source bundle cannot
  travel in the zip - the plugin stays wholly GPL with public source, and the
  user obtains the runtime from NIKA's own channel under NIKA's own licence.
  Its terms ship with the plugin and are shown and accepted before anything is
  downloaded; the download is verified against `runtime-lock.json` before it is
  cached; it goes through QGIS's network stack so a configured proxy applies;
  and `ONLYMAP_RUNTIME_DIR` installs it by hand on a machine with no internet.
  Documented as the single deliberate exception to CONTRIBUTING rule 2.
- Per-field popup settings, closing the first of the qgis2web parity gaps. All
  six modes the model already carried are now reachable: value only, label
  beside or above the value, each either always shown or only when the feature
  has data, and hidden. They live on expandable rows in the existing layer list
  - one level of nesting, against the incumbent's two - with a "set every field"
  bulk action, and they persist in the project alongside the other settings.
  A field with no explicit choice still follows the QGIS attribute table, and a
  persisted mode this build cannot parse falls back to the default rather than
  stopping the dialog from opening.

- The rest of the qgis2web parity tranche. **Caption**: the map name and the
  project abstract can be drawn over the map in a chosen corner - the abstract
  was read on every export and thrown away until now. Emitted as a plain
  positioned block rather than an `om-overlay`, because every overlay anchor the
  runtime has is a *map* anchor and a caption pinned to a longitude slides off
  when the reader pans. **Control colours**: background and foreground pickers
  emit `--om-widget-*` custom properties, which reach the widgets' shadow roots
  because custom properties inherit across the boundary where ordinary rules do
  not. **Hover popups**: an Appearance toggle swapping the click binding for a
  hover one. **Coordinate precision**: "maintain" plus 1-15 decimal places,
  threaded into the GeoJSON writer and reported as lossy whenever it is set.
  **Extent**: open on the data (the default, and antimeridian-aware) or on the
  current QGIS view.
- **Per-layer overrides** for hover popups, highlight colour and coordinate
  precision, on a "This layer only" row under each layer. Each starts on "same
  as map", so an untouched project is unaffected and nothing is written to the
  project file until one is set. These are qgis2web's issues
  [#131](https://github.com/qgis2web/qgis2web/issues/131),
  [#132](https://github.com/qgis2web/qgis2web/issues/132) and
  [#133](https://github.com/qgis2web/qgis2web/issues/133) - each asking for one
  of these three to stop being global-only, each open since 2015. A layer can
  keep full coordinate precision while the rest of the map is rounded, which is
  a distinct state from inheriting.
- A **highlight colour** control on the Appearance tab. The highlight was
  already translucent rather than the incumbent's opaque fill, but hardcoded;
  it is now yours to pick, with opacity. qgis2web offers nothing here - it
  reuses `mapSettings.selectionColor()`, the QGIS *editing selection* colour,
  as a web hover cue, and its only workaround lives in Project Properties.
- The folder export now unbundles. It produced a single inlined `index.html`,
  making it identical to the standalone tier and pointless: the runtime is now
  written beside the page as `onlymap.js`, so a served site caches it once
  across every map instead of re-downloading it inside each one. The page drops
  from megabytes to tens of kilobytes. It is HTTP-only by nature - a module
  script cannot be fetched from `file://` - which the README, the exporter and
  the guides all now say.

### Fixed
- The caption drew behind the map controls. It carried `z-index: 3` against
  widgets that set `9999` on their own `:host`, so it appeared in front of the
  credit chip (`z-index: 2`) in the bottom corners and behind the legend and
  scale bar everywhere else - meaning whether the title showed at all depended on
  which corner it was in.
- The three **How to share it** options are radio buttons. They were checkboxes
  that unpicked each other by hand, which promises multi-select without
  delivering it and reads wrong to a screen reader.
- Five preview tests failed, rather than skipping, on a machine with no cached
  runtime. Every other tier guarded on `discover_runtime_dir`; these did not, so
  a red run that only meant "this machine has not fetched the runtime" trained
  people to ignore red.
- Secondary text in the dialog comes from the palette instead of a fixed grey,
  so it stays legible in a dark Qt theme.
- A Bandit pragma written as a prose comment beginning with the pragma word made
  the scanner read the sentence as a list of test IDs and emit a warning per
  word. QGIS runs Bandit as a blocking check, so its output has to stay readable.
- Single-symbol layers showed a grey legend swatch beside correctly-coloured
  geometry. The legend takes its swatch from the layer's `color` shorthand and
  only derives one from an expression it can read structure out of; a single
  symbol compiles to a bare literal, so it fell through to its `#999` default.
- Map chrome no longer piles into one corner. The manifest emitted the schema's
  logical slot names (`top-end`, `bottom-start`), which the shipped runtime does
  not implement - it looks `position` up in a four-entry corner table and falls
  back to `top-left` on a miss, so the legend, layer switcher, zoom controls and
  scale bar all landed on top of each other and the map title, which the legend
  carries, was buried. `WIDGET_POSITIONS` now emits corner aliases, and a test
  pins them.
- Lines exported grey. A line symbol layer answers `fillColor()` and
  `strokeColor()` with an invalid QColor and keeps its real colour in `color()`,
  so every line fell back to the `#888888` placeholder - a neon teal river came
  out drained. Colours are also read from the *top* symbol layer rather than the
  bottom one, so a road with a casing exports the line you see rather than the
  casing hidden under it.
- Popups had no surface of their own and text sat directly on the map. They now
  carry a background, border and shadow. Related: the popup stylesheet lived in
  `map.html`, where it never applied at all - `om-overlay` renders into a shadow
  root that document rules cannot reach - so it travels inside the overlay now.
- The Fidelity tab elided the item name ("Data of 'al...") rather than the
  detail, which is backwards: the name says which layer a note is about. Names
  now size to their content and the detail expands on click.
- Popup-mode combos were cropped to the width of a checkbox column. Field rows
  now span the full width of the layer list.
- The zoom controls and scale bar sat on top of the runtime's own licence
  notice in the bottom-left corner.
- Exports are now byte-for-byte reproducible. `gzip.compress` stamps the current
  time into the gzip header, so the same project exported twice produced two
  different files and leaked the export time into the artifact.
- The per-layer **Popup** and **Label** checkboxes now do something. Both were
  saved to the project and read back into the dialog, and consulted by nothing.
- Runtime discovery no longer contains an absolute developer path, which
  resolved on one machine and hid the missing vendored runtime everywhere else.
- Licence-cap violations and every writer warning now reach the user. The dialog
  evaluated no policy and discarded `ArtifactResult.warnings`, so a six-layer
  project exported with the sixth layer silently dropped for every recipient.
- Label text with accented or non-Latin characters renders again. The declared
  character set carried only the non-ASCII characters, and `text-character-set`
  *replaces* the font atlas rather than extending it, so every ASCII glyph fell
  outside it and labels came out blank.
- The dialog's **Close** button now saves settings. It called `reject()`, which
  on Qt5 never delivers a `QCloseEvent`, so the only code that persisted state
  and disconnected the layer watcher did not run.
- Features in categories switched off in QGIS now leave the exported data. They
  were dropped from the styling expression only, so they still drew in the
  fallback grey while the fidelity report said they had been omitted.
- The dialog refuses to export a project with blocked layers, as the Processing
  algorithm already did. The two entry points behaved differently on identical
  input, and the dialog reported success over a map that was missing data.
- A classification field whose name cannot be referenced in a web-map expression
  (`Land Use`, `2024 total`) now falls back to a single symbol and is reported.
  It previously produced unparseable markup and lost the symbology silently.
- The Processing algorithm stages through a temporary directory and writes the
  recipient's README, by calling the same `build_artifact` as the dialog. It
  left a hidden `.<name>-build/` copy beside every output and shipped zip and
  folder exports without the README.
- Runtime provenance reports the version of the bytes actually embedded rather
  than the version pinned in `runtime-lock.json`.
- A project title containing a template token (`@GENERATOR@`) is no longer
  substituted a second time when building the README.
- A polygon with an empty or two-vertex outer ring drops its label instead of
  raising `ZeroDivisionError` and aborting the whole export.

### Removed
- `mydatabase.db` and `symbology-style.db`, QGIS scratch files committed to the
  repository root by accident, and now ignored.
