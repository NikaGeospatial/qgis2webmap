---
title: The dialog, tab by tab
seo_title: The QGIS2WebMap export dialog
description: >-
  Every control in the QGIS2WebMap export dialog - Map, Layers, Appearance,
  Fidelity and Help - and what each one changes for whoever opens your map.
lead_image:
  src: /images/dialog-tabs.png
  alt: The export dialog's tab bar - Map, Layers, Appearance, Fidelity, Help.
---

# The dialog, tab by tab

Open it with **Web → QGIS2WebMap by NIKA → Create web map**, the toolbar
button, or **Ctrl+Alt+W** (**Cmd+Alt+W** on macOS). To change the shortcut, search
for *Create web map* in **Settings → Keyboard Shortcuts**.

The dialog is **non-modal**: QGIS stays usable behind it. Reorder, rename or
restyle layers in the Layers panel and the plugin follows immediately. There is
no refresh button because there is nothing to refresh.

Six tabs, and two things that stay on screen whichever tab you are on: the
[fidelity strip](#the-fidelity-strip) and the [button row](#the-button-row).

Settings are remembered **per project**, stored in the `.qgz` alongside it, so
they travel with the project rather than following you between projects. The
exceptions are remembered per machine instead, because they are habits rather
than properties of a map: the last folder you exported to, and the Settings tab's
choices.
The [Settings tab](#settings) gathers the per-machine ones you can change.

---

## Map

Everything about the map as a whole, and the one place the exported title is set.

### Map name

The first field in the dialog, and the **only** place the exported title comes
from. It is shown on the map and in the browser tab.

Leave it blank to use the project title. The field is prefilled from
**Project Properties → General**, falling back to the project file name and then
to `Untitled map`, and typing here **does not write back to the project** —
exporting a map should not quietly edit your project's metadata.

Setting a name here does not by itself draw it on the map. That is the
**Map title** switch on the [Appearance tab](#caption), which is off by default.

### Export

Choose **what to make** and **where it goes**, then press **Export** in the
top-right corner.

**Export as:**

- **Standalone HTML** — one file that opens by double-clicking, with all of your
  data inside it. The default.
- **Share ZIP** — the same map zipped, with a short README. Use it when a mail
  filter blocks `.html` attachments.
- **Folder** — `index.html`, `onlymap.js` and a README side by side, to copy to a
  web server. A folder has to be served; it does not open by double-clicking.

**Save to** is where the export is written. Type a path or press **Choose**,
which opens beside your project file, named after it. Switching the type keeps
the name: `peaks.html` becomes `peaks.zip` or a `peaks` folder. Pressing Export
writes there without asking again.

For a Standalone HTML export, a line underneath gives measured size advice: how
large your map's data actually is, and when the file would become slow to open
or awkward to email.

A second line says what is on disk:

| It says | It means |
|---|---|
| *Choose where to save, then press Export.* | No path set yet. Nothing has been written |
| *Not written yet.* | The path is set; nothing is there until you export |
| *Last written 20 min ago* | Written earlier. Export again if the project has moved on |

Both the type and the place are saved with the project.

### Basemap

A backdrop drawn behind your data: **None**, OpenStreetMap, Positron, Dark
Matter, Voyager, Liberty or Bright.

**None is the default, and it is the only choice that keeps the export offline.**
Every other option means the recipient's browser fetches tiles from that provider
every time they open the map — so the provider sees those requests, including
roughly where the reader is looking. The dialog warns in red when a basemap is
set, and the Fidelity tab names the provider. Nothing else changes: no
identifier travels with the file, the anonymous usage report is the same either
way, and the file is no larger.

**Ground surface** sits in the same group because it costs the recipient the same
thing. **Flat** is the default; **Global relief** tilts the map so elevation
shows, and fetches terrain tiles to do it. With a basemap chosen too, the
basemap's imagery drapes over the relief — mountains that look like the map,
with your symbology painted onto the slopes. The warning under the control
names every host the relief streams from.

Basemaps needing an API key are deliberately absent. An exported file would have
to carry the key in plain text where every recipient could read it.

### Data

**Open the map on** — where the map is positioned when someone first opens it.
*The data* frames every feature; *the current QGIS view* uses your canvas as it
stands. Either way the reader can pan and zoom anywhere afterwards.

**Export only the features in this view** is a separate decision, and folding the
two together is exactly how you would ship a map missing everything outside your
working extent. This one *removes* data: anything outside the current QGIS view
is left out of the file. It is the practical way to bring a very large layer
under the free plan's 25,000-feature limit, and the Fidelity tab reports how many
features each layer loses.

**Coordinate precision** — the only setting in the dialog that throws data away,
which is why it defaults to *Maintain full precision*. Rounding coordinates makes
the file smaller and cannot be undone. Six decimal places is about 0.1 m at the
equator. When you choose it, the fidelity report says so.

### The summary line

At the bottom of the tab, a plain sentence saying what the map file will
contain, given every choice above. It is a consequence, not a setting.

---

## Layers

One list, driven by the QGIS Layers panel, bottom layer first — the same order
the map draws in.

### The three columns

| Column | Ticked | Unticked |
|---|---|---|
| **Include** | The layer is in the map | The layer is left out, and its data is never written to the file |
| **Popups** | Clicking a feature shows its attributes | Clicking does nothing, and the attribute values are **not written to the file** |
| **Labels** | The text you set in QGIS is drawn beside features | No text. The features themselves are unaffected |

Two of these matter beyond tidiness:

- Unticking **Popups** is how you keep attribute data out of a map you are
  sending someone. The values are not hidden — they never reach the file, so
  there is nothing to recover by opening it in a text editor.
- Unticking **Include** or **Popups** makes the file smaller, which matters
  because the map file carries all of its data inside it.

Unticking any box affects that layer only. The map still works, with less on it.

### Per-field popup settings

Expand a layer to list its fields. Each gets its own setting:

| Setting | Effect |
|---|---|
| **Value only, no label** | The value with no field name beside it |
| **Label beside value — always show** | Field name and value on one line, even when empty |
| **Label beside value — only if it has data** | The same, but the row disappears when the value is empty |
| **Label above value — always show** | Field name on its own line above the value |
| **Label above value — only if it has data** | The same, but hidden when empty |
| **Do not show this field** | The field's values are **removed from the file**, exactly as unticking Popups does for the whole layer |

**One exception, and it is deliberate.** A field the map *draws* with — the one a
categorised or graduated style is based on, the one labels read their text from,
or an extrusion height — stays in the file even when you hide it. Removing it
would conceal nothing, since it is visible on the map either way; it would only
break the drawing.

### Set every field to … Apply to all layers

A bulk action for the tedium of setting a dozen layers by hand. It is a button
rather than a live combo because it overwrites every field in every layer and
should not fire on a stray scroll.

### The list follows QGIS

Reorder, rename, add, remove, group or hide a layer in the QGIS Layers panel and
this list rebuilds. Per-layer settings survive that rebuild — they are keyed to
the layer, not to the row — and settings for a removed layer are kept, so undoing
the removal in QGIS restores its configuration too.

---

## Appearance

How the map looks to the reader. **Everything here is on by default**: an
exported map should be useful without configuring anything.

### Map controls

Four switches — **Legend**, **Layer switcher**, **Zoom controls**, **Scale bar**.
Untick one to leave that control off the exported map.

The layer switcher is the one exception to that. It only appears when the map
has more than one thing to switch between, so a map with a single unlabelled
layer does not get one however this is set — there would be nothing in it but a
single ticked box. Turning labels on counts: a labelled layer travels with its
labels as a second entry, which you can then show and hide on their own.

### Caption

Both off by default, because an unwanted caption drawn over someone's map is
worse than a missing one.

- **Map title** draws the [map name](#map-name) over the map. The legend drops
  its own heading while this is on, so the title never appears twice.
- **Project description** draws the abstract from
  **Project Properties → Metadata**. Nothing appears if the project has none.
- **Position** places the caption. All four corners already hold controls —
  switcher top left, legend top right, zoom and scale bottom left, credit bottom
  right — so the two centre positions are the only genuinely clear space, and
  they are listed first.

### Control colours

**Background** and **Text and icons** apply to the legend, layer switcher, zoom
controls and scale bar. Both start unset, meaning *use the map's own styling*;
leave them that way and a default export is unchanged.

**Size** scales the legend, layer switcher, zoom controls, scale bar and title
together — Small through to *Largest, for presentations and big screens*. The
credit stays fixed, so attribution cannot be shrunk out of legibility.

### Behaviour

**Open popups on hover instead of click** replaces click rather than adding to
it. Bound together, a click on an already-open popup appears to do nothing.

**Highlight under the cursor** is the colour shown when the reader's pointer is
over a feature. Keep some transparency — a solid colour hides whatever the
feature is drawn on top of. Leave it unset for the default, a see-through white.

---

## Fidelity

The reason this plugin exists. It answers one question — *what does my recipient
not get?* — **before** you export, not after.

The report builds when you open the tab, reading every feature of every layer, so
it takes a moment on a large project and shows a progress bar while it does.
Preview and Export read the project too, and refresh the report as they go.

One line at the top sums it up — `5 layers · 1 needs attention · 10 things
change`. A layer *needs attention* when something about it is Blocked or Not
exported. Below it the report is grouped, problems first:

- **Whole map** — settings that belong to no one layer: the basemap, relief,
  where the map opens, its title. Its own line describes the whole map: how
  many layers and features, roughly how much data, the output, where the map
  opens and its basemap.
- **One group per layer**, the layers that lose something before the layers that
  are only changed. A layer that comes through exactly is a single `✓ exact`
  line. Each layer's own line says what it is: its geometry and feature count,
  how it is styled and on which field, its labels, how many fields its popups
  show and how many go into the file, the zooms it shows at, and how much data
  it adds.
- Every row has its whole sentence in the **Detail** column.
- Rows saying the same thing about several classes of one layer are merged into
  one, with the number of classes. Expand it to see each class.

Select a row to see it in the pane below the list, with the classes it applies
to. Where the fix is in this dialog, the pane has a button that goes there:
**Show in Layers** for a layer, **Go to Map tab** for the basemap and the other
map settings.

**Filter**, beside the summary, chooses what the list shows — one tick per
verdict, each with how many rows it has, and one per topic: **Symbology**,
**Labels**, **Popups & fields**, **Data** and **Map settings**. The menu stays
open while you tick, and the button says how many rows are folded away. The search box finds rows by any word
in them, or a layer by its name or by what it is ("polygons", "labels from").
**Kept** rows are hidden by default so the changes stand out; tick **Kept** to
bring them back. Your choices are remembered. Filtering only folds rows away:
the counts at the top and in the strip are always about the whole report.

When something changes after a check — a layer ticked or unticked, the map
renamed, a layer added, restyled or edited and saved in QGIS — the report is
marked **Out of date** rather than going on looking current. **Check again**
refreshes it.

| Verdict | Meaning |
|---|---|
| **Blocked** | Cannot be carried into the map at all — usually a layer QGIS could not load. Export still works, but asks first and names what will be missing |
| **Not exported** | Cannot be represented, and was left out. The map is still produced |
| **Changed** | Exported, but approximated — the detail says how |
| **Rasterised** | Drawn into the map as an image: markers QGIS draws itself, or a raster's colours baked into its pixels. It looks as it does in QGIS, but it is a picture now |
| **Kept** | Survives exactly as set in QGIS |

If the check itself fails, the tab says so and why. That is not a verdict: it
says nothing about your map, only that the check did not finish.

Worth reading before you send a map on.

---

## Settings

Things that are true of every project, rather than of one map:

- **NIKA account** — who this copy of QGIS is signed in as. Publishing to NIKA
  needs an account; exporting a file never does.
- **Language** — *Follow QGIS* (the default), English, or one of Português
  (Brasil), 中文（简体）, हिन्दी, Español, العربية and Français. English is built
  in; the others are a small download the first time you pick one, checked
  against the copy released with this version. *Follow QGIS* uses the language
  QGIS is set to when it is one of these, and otherwise English. When QGIS is
  in one of them and its pack is not downloaded yet, a bar at the top of the
  dialog offers it — nothing is downloaded until you press it. Switching
  reopens the dialog in the new language; QGIS does not need a restart.
- **Appearance** — the dialog's theme: Follow QGIS, Light or Dark.
- **About** — the plugin version and the OnlyMap runtime it is pinned to.

---

## Help

The same guides you are reading now, bundled into the plugin and rendered
offline. **Open the full documentation** opens this site in your browser.

---

## The fidelity strip

Between the tabs and the buttons, always visible, whichever tab you are on:

- `3 things change on export.` — or
- `1 layer cannot be exported.` — or
- nothing at all.

**Nothing at all is the good case.** A permanent "0 things change" trains people
to stop reading it, so a clean export says nothing and the absence is the
message. **What changes?** jumps to the Fidelity tab.

---

## The button row

### Preview, Share and Export

All three sit in the **top-right corner of the tab bar**, so they are within
reach whichever tab you are reading.

**Preview** builds the map and opens it in your default browser, served from
your own machine. The tab then updates by itself as you change settings, keeping
your position on the map - change a colour or switch a layer off and watch it
happen. While it runs the button reads **● Live**; press it again, or close the
tab, to stop. Nothing else in the plugin opens a browser, and a preview never
writes your map file - only Export does.

If the preview cannot be served — a firewall, a locked-down machine — it opens
as a file instead and tells you so. That tab only changes when you press
Preview again.

**Export** writes the map in the type and place chosen on the Map tab. It is
the filled button, last in the row, because it is what the plugin is for.

**Share** is everything else you might do with the finished map. Pressing it
opens the menu:

| Row | What it does |
|---|---|
| **Copy file path** | Puts the map file's path on the clipboard |
| **Show in file manager** | Opens the folder the map file is in |
| **Publish with NIKA hosting** | Hosting is not live yet; this opens a short waitlist form |
| **Enhance with NIKA AI**, **Ask NIKA for a critique** | Coming in a later release |
| **Make a cartograph with QGIS2Carto** | Coming in a later release |
| **Ask on Discord** | Opens the QGIS2WebMap channel on the NIKA Discord |

**Every row that acts on the map needs a Save to path first.** Until you have
set one on the Map tab they are greyed out, and each says so if you hover it.
**Ask on Discord** always works.

**Rows that need the map write it first** when it is missing or out of date,
so Copy file path or Show in file manager never point at something stale.

Preview, Export and Share's map rows are greyed out when the map cannot be produced correctly,
with the reason in the bar at the foot of the dialog rather than a silent
refusal:

| The reason says | Fix |
|---|---|
| *Add a vector layer to the project to export.* | The project has no exportable layer |
| *Tick at least one layer to include.* | Everything is unticked on the Layers tab |
| A blocked item | Open the Fidelity tab; it names the layer and why |

### Progress and Cancel

A bar appears only while something is running, naming the layer it is on, with
**Cancel** beside it. Its appearance is the signal that work is happening rather
than that the plugin has hung. One job runs at a time: pressing Export twice does
not export twice.

---

## Next

- [Your first export](first-export.md) — the same ground as a walkthrough
- [What gets exported](supported-features.md) — what each verdict is based on
- [Sharing a map](sharing.md) — which Share row to reach for, and why
- [Troubleshooting](troubleshooting.md) — when a control is not doing what this
  page says
