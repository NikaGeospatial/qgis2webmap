---
title: The dialog, tab by tab
description: >-
  Every control in the QGIS2WebMap export dialog - Map, Layers, Appearance,
  Fidelity and Help - and what each one changes for whoever opens your map.
lead_image:
  src: /images/dialog-tabs.png
  alt: The export dialog's tab bar - Map, Layers, Appearance, Fidelity, Help.
---

# The dialog, tab by tab

Open it with **Web → QGIS2WebMap by NIKA → Create web map**, or the toolbar
button.

The dialog is **non-modal**: QGIS stays usable behind it. Reorder, rename or
restyle layers in the Layers panel and the plugin follows immediately. There is
no refresh button because there is nothing to refresh.

Five tabs, and two things that stay on screen whichever tab you are on: the
[fidelity strip](#the-fidelity-strip) and the [button row](#the-button-row).

Settings are remembered **per project**, stored in the `.qgz` alongside it, so
they travel with the project rather than following you between projects. Two
exceptions are remembered per machine instead, because they are habits rather
than properties of a map: **Live preview** and the last folder you exported to.

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

### How to share it

Three mutually exclusive output modes. There is more on choosing between them in
[sharing a map](sharing.md).

| Mode | Produces | The recipient |
|---|---|---|
| **Standalone HTML** | One `.html` file | Double-clicks it. Nothing else needed |
| **Share ZIP** | A `.zip` with the map and a short README | Extracts it, opens `index.html` |
| **Folder** | `index.html`, `onlymap.js` and a README, side by side | Serves it over `http(s)` |

Underneath is a line of measured size advice, not a generic warning: it reports
how large your map's data actually is and says when Standalone HTML would become
slow to open or awkward to email.

**Folder does not open by double-clicking.** Browsers refuse to load the runtime
from a `file://` page when it sits in a separate file, so a folder has to be
served. Pick Standalone HTML if you want something that opens off a disk.

### Filepath

Where the export is written. Type a path or press **Browse**. The folder is
remembered between exports.

The file is named after the map name, with anything that is not a letter, digit,
space, hyphen or underscore replaced — so a layer called `Site A / B` cannot
silently write into a subdirectory.

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

At the bottom of the tab, a plain sentence saying what pressing **Export** will
produce, given every choice above. It is a consequence, not a setting.

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
- Unticking **Include** or **Popups** makes the file smaller, which matters most
  for Standalone HTML.

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
it takes a moment on a large project and shows a progress bar while it does. It
rebuilds when the project changes.

Three columns: the item, its verdict, and the detail. Long details open onto
their own row when clicked. Problems are sorted to the top, because a report
opening on a wall of *Kept* buries what matters.

| Verdict | Meaning |
|---|---|
| **Blocked** | The export must not proceed. Export stays disabled while this is present |
| **Not exported** | Cannot be represented, and was left out. The map is still produced |
| **Changed** | Exported, but approximated — the detail says how |
| **Rasterised** | Drawn as an image because the style has no live equivalent |
| **Kept** | Survives exactly as set in QGIS |

Worth reading before you send a map on, and **essential before you host one**:
this tab is what names every layer over the free plan's
[size limits](supported-features.md#size-limits-on-the-free-plan).

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

### Live preview

A per-machine habit, remembered between projects. Ticking it opens nothing on its
own — it only decides what **Preview** does when you press it.

With it **on**, the map is served from your own machine and the open browser tab
updates by itself as you change settings, keeping your position on the map.
Change a colour or switch a layer off and watch it happen.

With it **off**, Preview writes a file and opens that. The tab only changes when
you press Preview again.

If the preview cannot be served — a firewall, a locked-down machine — it falls
back to a file automatically and tells you so.

### Preview

Builds the map and opens it in your default browser. **Nothing else in the plugin
ever opens a browser.**

### Export

Writes the real artifact. Greyed out when the export cannot be produced
correctly, with the reason beside it rather than a silent refusal:

| The reason says | Fix |
|---|---|
| *Add a vector layer to the project to export.* | The project has no exportable layer |
| *Tick at least one layer to include.* | Everything is unticked on the Layers tab |
| A blocked item | Open the Fidelity tab; it names the layer and why |

### Open exported map

Enabled once an export succeeds. It opens **the real file** — the one you would
send someone — rather than a preview copy, so what you check is what they get.

### Progress and Cancel

A bar appears only while something is running, naming the layer it is on, with
**Cancel** beside it. Its appearance is the signal that work is happening rather
than that the plugin has hung. One job runs at a time: pressing Export twice does
not export twice.

---

## Next

- [Your first export](first-export.md) — the same ground as a walkthrough
- [What gets exported](supported-features.md) — what each verdict is based on
- [Sharing a map](sharing.md) — choosing between the three output modes
- [Troubleshooting](troubleshooting.md) — when a control is not doing what this
  page says
