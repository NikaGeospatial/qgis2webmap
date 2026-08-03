# Changelog

All notable changes to QGIS2WebMap by NIKA. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
