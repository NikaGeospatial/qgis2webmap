---
title: The Cartograph dialog, tab by tab
seo_title: The QGIS2WebMap cartograph dialog
description: >-
  Every control of the Cartograph (Beta) output - the Cartograph, Frames and
  Atlas tabs, the corner buttons and the Share menu - and what each one does to
  the file.
lead_image:
  src: /images/cartograph-dialog.png
  alt: The dialog on Cartograph (Beta) - tabs Cartograph, Layers, Frames, Fidelity, Settings and Help, with Accessibility, Preview and Open in browser in the corner.
---

# The Cartograph dialog, tab by tab

Press **Cartograph (Beta)** beside **Make:** at the top of the plugin's dialog
and three things change: the first tabs become **Cartograph** and **Frames**
(plus **Atlas**), the corner holds **Accessibility · Preview · Open in
browser**, and the Export button goes away. The web map's own tabs are
described in [the dialog, tab by tab](the-dialog.md); **Fidelity**,
**Settings** and **Help** are shared and work the same way.

Other ways in:

- **Web → QGIS2WebMap by NIKA → Create cartograph...** opens the dialog
  straight on Cartograph (Beta).
- In the layout designer, **Layout → Export as cartograph (HTML)...** does the
  same and selects the layout you have open.
- On the web map, **Share → Make a cartograph...** builds a print layout from
  the map canvas and switches over - see [one-click layouts](one-click-layout.md).

## The document model, in one paragraph

A cartograph is **a file that is always saved**, not something an Export
button produces. Every change on the Cartograph, Frames or Atlas tab - and
every change to the print layout itself - rewrites the file about 600 ms
later, through a temporary file that is swapped in, so it is never half
written. Preview, Share and the NIKA hand-off all work on that file. The
dialog's title names it. Closing the dialog leaves it where it is, and
reopening the plugin on the same project picks it up again.

Settings are stored with the project, per layout: each layout remembers its
own file, frames, page fields and print settings.

---

## Cartograph

### Source

- **A print layout** - the list of the project's print layouts. It follows the
  Layout Manager as you add, rename or remove layouts. With none, the tab says
  how to make one (**Project → Layouts → New Layout**).
- **Current canvas (creates a print layout)** - an action rather than a mode:
  one click builds a layout named `carto: <project title>` from the map canvas,
  adds it to the project and selects it in the list above. Greyed out when
  there is no map canvas. See [one-click layouts](one-click-layout.md).
- **Import cartograph...** - turns a cartograph HTML file back into a new QGIS
  print layout, and selects it. Whatever QGIS cannot hold is listed when the
  import finishes. See [one-click layouts](one-click-layout.md#importing-a-cartograph-back-into-qgis).

![The Cartograph tab with Import cartograph... highlighted beside the Source list.](images/cartograph-import.png)

### Cartograph file

**Save to** is the file. It starts beside the project file, named after the
layout, or in your home folder for a project that has never been saved. Press
**Choose…** to pick another place; the name always ends in `.html`.

A cartograph is always **one standalone HTML file**. There is no Share ZIP or
Folder type as there is for the web map: the page, its static images and the
data of its live maps are all inside it.

### Page

- **Title** - replaces the project title wherever the layout shows it
  (`[% @project_title %]`). Blank keeps the project title.
- **Attribution** - a credit line for the page, such as your data sources.
  <!-- verify: where the Attribution field appears on the page - it is written
  as the page's attribution attribute; only an item that uses the attribution
  token shows it, and the plugin's layout reader never emits that token -->
- **Theme** - the page's look: **Default**, **Dark**, **Sepia**,
  **Blueprint** or **Minimal**. It colours the page and the furniture - legend,
  scale bar, north arrow, grid - not your map data.

### The status line

Under the form: *Not saved yet*, *Saved 2 s ago · 3.2 MB*, or the reason a
save failed. See [your first cartograph](cartograph-first-export.md#4-check-where-it-saves).

---

## Layers

Shared with the web map and shown in both. A cartograph's frames draw the
layers their map item draws in the layout - its own layer list, or the
project's visible layers - so this is not where a cartograph's layers are
chosen.

<!-- verify: the Include / Popups / Labels columns have no effect on a
cartograph today - the live-frame export does not pass the dialog's layer
settings, so a live frame keeps every popup field the layer itself defines -->

---

## Frames

One row per map item on the layout:

| Column | What it is |
|---|---|
| **Map item** | The map's ID in the layout designer, or its display name |
| **CRS** | The map's CRS, or *Unknown CRS* |
| **Frame** | **Live** - an interactive map drawn in the browser, in Web Mercator. **Static** - an image of the map as QGIS draws it, in its own CRS |
| **DPI** | A static frame's image resolution: **150** (the default), **200** or **300**. Greyed out for a live frame |
| **Changes** | Fidelity rows about this map that are not *Kept*. Empty until the first save |
| **Use canvas view** | Sets the map's extent to what the QGIS canvas shows. It is an edit to your layout, so the layout designer's **Undo** takes it back |

![The Frames tab with two maps: Locator, EPSG:3857, Live, and Main map, EPSG:32648, Static at 150 dpi.](images/cartograph-03-frames.png)

A map starts **Live** when its CRS is EPSG:3857 or EPSG:4326, and **Static**
otherwise. [Live or static frames](live-or-static-frames.md) is the whole
story; the short version is that live costs fidelity in projected CRSs and
file size, and static costs interactivity.

The rows follow the layout: add or delete a map in the layout designer and the
list rebuilds.

---

## Atlas

<!-- verify: the whole Atlas tab section - written from the specification
while the tab is being built; check every control name and behaviour against
the finished tab -->

The layout's atlas, as the cartograph will page it. One cartograph file holds
every page: the reader turns them with the arrow keys or the pager at the foot
of the sheet. [Atlas cartographs](cartograph-atlas.md) explains the rules.

| Control | What it is |
|---|---|
| **Enabled** | Whether the cartograph pages the layout's atlas |
| **Coverage layer** | One page per feature of this layer |
| **Sort** | The field pages are ordered by |
| **Filter** | Which features get a page |
| **Filename pattern** | How each page is named when the pages are exported as images |
| **Page count** | How many pages QGIS's atlas has. The cartograph can have fewer when the map hides some features |

The atlas only works through a **live** map that draws the coverage layer.
If the map the atlas drives is static, the Fidelity tab marks the atlas
**Blocked** and says to set that map to Live on the Frames tab.

---

## Fidelity

The same tab as for the web map, showing the cartograph's own report: every
layout item that changed or was left out, with the verdicts **Kept**,
**Changed**, **Rasterised**, **Not exported** and **Blocked**. It refreshes
after each save. Before the first save it reads *Not checked yet*, and **Check
now** on the strip saves the cartograph to check it.

Layout items are listed under **Whole map**, and each layer a live frame draws
gets its own group, exactly as on the web map. The verdicts are explained in
[what a cartograph keeps](cartograph-supported-features.md).

---

## The corner

Three buttons in the top-right corner of the tab bar, reachable from every tab:

- **Accessibility** - opens the [Colour vision](colour-vision.md) dialog:
  simulate colour-vision deficiencies on the canvas and in the browser, and
  check which legend classes become indistinguishable. Stays pressed while a
  simulation is on, and its tooltip names it.
  <!-- verify: the Accessibility button is wired to the Colour vision dialog -
  in the current build it is disabled with the tooltip "Coming in a later
  version" -->
- **Preview** - opens the saved cartograph in your default browser.
- **Open in browser** - the same; its **arrow** opens the Share menu.

![The corner: Accessibility, Preview, and Open in browser with its menu arrow highlighted.](images/cartograph-corner.png)

Both open the file through a small server on your own machine
(`127.0.0.1`) rather than as `file://`, because some browsers refuse to run a
live map from a file path. Nothing leaves your computer.

---

## The Share menu

The arrow beside **Open in browser**:

| Row | What it does | Available |
|---|---|---|
| **Copy file path** | Puts the cartograph's path on the clipboard | Once the file exists |
| **Reveal in Finder** / **Show in Explorer** / **Show in file manager** | Opens the folder the cartograph is in | Once the file exists |
| **Open in browser** (**Ctrl+O**) | Opens the cartograph in your browser | Once the file exists |
| **Publish with NIKA hosting... (waitlist)** | Hosting is not live yet; opens a short waitlist form | Always |
| **Enhance with NIKA AI...** | Opens the [hand-off dialog](enhance-with-ai.md#hand-off-to-nika) with a prompt asking an AI agent to improve the cartograph | Once the file exists |
| **Ask NIKA for a critique...** | The same dialog, with a prompt asking for a cartographer's critique | Once the file exists |
| **Export PDF...** (**Ctrl+P**) | Opens the cartograph and its print dialog at true page size - or, with a CMYK or ICC profile chosen in Print settings, has QGIS write the PDF | Once the file exists |
| **Export PNG...** | Opens the cartograph, which renders a 300 dpi image of the page and downloads it | Once the file exists |
| **Export QGIS layout template (.qpt)...** | Saves the source layout as a `.qpt` template, to load into another project | Once a layout is selected |
| **Print settings...** | Page size, resolution, bleed, crop marks, safe zone and colour profile for Export PDF | Once a layout is selected |

<!-- verify: Ctrl+O and Ctrl+P fire from the dialog itself, not only while the
menu is open (the actions are not added to the dialog) -->

Export PDF and Export PNG are **derived** from the cartograph rather than being
the export: the browser makes them from the saved file. Both are covered in
[printing a cartograph](printing-a-cartograph.md).

There is deliberately no Share ZIP, no Folder, and no Fidelity row. A
cartograph travels as its one file, and the Fidelity tab is always one click
away.

---

## Processing

For models and batch runs, the Processing Toolbox has **QGIS2WebMap by NIKA →
Cartograph → Export print layout to cartograph (Beta)**. It runs the same
export as the dialog, once, with these parameters:

| Parameter | What it is |
|---|---|
| **Print layout** | The layout to export |
| **Frames** | **By map CRS** (live in EPSG:3857 and EPSG:4326, static otherwise), **All live** or **All static** |
| **Static frame resolution (dpi)** | 72 to 600; 200 by default |
| **Title** | Replaces the project title; blank keeps it |
| **Export even if some items are blocked** | Off by default, so a Blocked item - a second page, say - stops the run and names it |
| **Cartograph** | Where to write the `.html` |

Every Fidelity row is written to the log. Print settings, the Cartograph tab's
page fields and per-map choices from the Frames tab are not used: the
algorithm takes only its own parameters.

On a computer that has never downloaded the OnlyMap runtime, the algorithm
stops and says so - it cannot show the licence in a batch run. Save one
cartograph or web map from the dialog first.

---

## Next

- [Your first cartograph](cartograph-first-export.md) - the same ground as a
  walkthrough
- [Live or static frames](live-or-static-frames.md)
- [Printing a cartograph](printing-a-cartograph.md)
- [What a cartograph keeps](cartograph-supported-features.md)
- [Troubleshooting](troubleshooting.md#cartographs)
