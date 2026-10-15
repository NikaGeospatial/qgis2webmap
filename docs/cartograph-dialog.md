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
button produces. Selecting a layout saves it at once, and every change after
that - on the Cartograph, Layers, Frames or Atlas tab, in the print layout
itself, or to the style or data of a layer it draws - rewrites the file about
600 ms later, through a temporary file that is swapped in, so it is never half
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

**Save to** is the file. In a saved project it starts beside the project
file, named after the layout, with anything other than letters, digits, dots
and hyphens turned into a hyphen - `carto: Parcels` becomes
`carto-Parcels.html`. In a project that has never been saved, the field only
suggests a name and nothing is written until you type a path or press
**Choose…**. The name always ends in `.html`.

A cartograph is always **one standalone HTML file**. There is no Share ZIP or
Folder type as there is for the web map: the page, its static images and the
data of its live maps are all inside it.

### Page

- **Title** - replaces the project title wherever the layout shows it
  (`[% @project_title %]`). Blank keeps the project title.
- **Attribution** - a credit for the page, such as your data sources. It is
  stored on the page, and shown by any text on it that uses the
  `{{attribution}}` token - text added by hand or by an AI agent, for example.
  A label in the QGIS layout does not use the token, so to print a credit from
  QGIS, write it into a layout label.
- **Theme** - the page's look: **Default**, **Dark**, **Sepia**,
  **Blueprint** or **Minimal**. It colours the page and the furniture - legend,
  scale bar, north arrow, grid - not your map data.

### The status line

Under the form: *Not saved yet - choose where to save it above* (an unsaved
project with no path yet), *Saved 2 s ago · 3.2 MB*, or the reason a save
failed. See [your first cartograph](cartograph-first-export.md#4-check-where-it-saves).

---

## Layers

Shared with the web map and shown in both. For a cartograph it sets how the
layers in **live** frames behave:

- **Popups** and the per-field popup settings apply. A layer with Popups
  unticked, or a field set to *Do not show this field*, has those values left
  out of the file - not merely hidden.
- **Labels** applies: unticked, the layer's labels are not drawn in live
  frames.
- **Include** does not choose a cartograph's layers. A frame draws the layers
  its map item draws in the layout - its own locked layer list, or the
  project's visible layers - so change that in the layout designer.

Static frames are pictures drawn by QGIS, so none of this changes them.
Changing anything here saves the cartograph again.

---

## Frames

One row per map item on the layout:

| Column | What it is |
|---|---|
| **Map item** | The map's ID in the layout designer, or its display name |
| **CRS** | The map's CRS, or *Unknown CRS* |
| **Frame** | **Live** - an interactive map drawn in the browser, in Web Mercator. **Static** - an image of the map as QGIS draws it, in its own CRS |
| **DPI** | A static frame's image resolution: **150**, **200** (the default) or **300**. Greyed out for a live frame |
| **Changes** | Fidelity rows about this map that are not *Kept*. Empty until the first save |
| **Use canvas view** | Sets the map's extent to what the QGIS canvas shows. It is an edit to your layout, so the layout designer's **Undo** takes it back |

![The Frames tab with two maps: Locator, EPSG:3857, Live, and Main map, EPSG:32648, Static at 200 dpi.](images/cartograph-03-frames.png)

A map starts **Live** when its CRS is EPSG:3857 or EPSG:4326, and **Static**
otherwise. [Live or static frames](live-or-static-frames.md) is the whole
story; the short version is that live costs fidelity in projected CRSs and
file size, and static costs interactivity.

The rows follow the layout: add or delete a map in the layout designer and the
list rebuilds.

---

## Atlas

One page per feature of a coverage layer, in one file. The tab edits the
layout's own atlas - the same settings as QGIS's Atlas panel - so every change
goes on the layout's undo stack, and **Undo** in the layout designer takes it
back. [Atlas cartographs](cartograph-atlas.md) explains the rules.

![The Atlas tab: Generate an atlas ticked, the coverage layer, the map that follows it, sort, filter and file names, the page count, and the two all-pages buttons.](images/cartograph-atlas-tab.png)

| Control | What it is |
|---|---|
| **Generate an atlas** | Turns the layout's atlas on or off. The rest of the tab is greyed out while it is off |
| **Coverage layer** | One page per feature of this vector layer |
| **Map that follows it** | The map that zooms to each feature in turn. It must be a **Live** frame. Choosing a map that did not follow the atlas before sets it to QGIS's own defaults: fit each feature, with a 10% margin |
| **Sort by** + **Descending** | The field pages are ordered by. Blank keeps the layer's order |
| **Filter** | An expression: only features for which it is true get a page. Blank gives every feature a page |
| **File names** | An expression naming each page's image when all pages are exported |
| *N pages in QGIS* | How many pages QGIS's atlas has. The cartograph can have fewer, because it skips features the map does not draw |
| A warning line | Why the cartograph will not page, when it will not - for example *Set the atlas map to Live in the Frames tab* |
| **Export all pages (PNG, ZIP)...** | Opens the cartograph, which renders every page and downloads them as one ZIP |
| **Print all pages...** | Opens the cartograph with every page laid out for one print. Choose **Save as PDF** for a multi-page PDF, each page at its true size in a Chromium-based browser |

The two buttons stay greyed out until the saved cartograph has atlas pages.

---

## Fidelity

The same tab as for the web map, showing the cartograph's own report: every
layout item that changed or was left out, with the verdicts **Kept**,
**Changed**, **Rasterised**, **Not exported** and **Blocked**. It refreshes
after each save. Before the first save it reads *Not checked yet*, and **Check
now** on the strip saves the cartograph to check it.

Layout items are listed under **Whole map**, and the layers the frames draw
get their own groups, as on the web map - a static frame's layers marked
**Rasterised**, since they are part of its picture. The verdicts are explained in
[what a cartograph keeps](cartograph-supported-features.md).

---

## The corner

Three buttons in the top-right corner of the tab bar, reachable from every tab:

- **Accessibility** - opens the [Colour vision](colour-vision.md) dialog:
  simulate colour-vision deficiencies on the canvas and in the browser, and
  check which legend classes become indistinguishable. Stays pressed while a
  simulation is on, and its tooltip names it.
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
| **Export PNG...** | Opens the cartograph, which renders a 300 dpi image of the page and downloads it - page 1 of an atlas; the Atlas tab exports every page | Once the file exists |
| **Export QGIS layout template (.qpt)...** | Saves the source layout as a `.qpt` template, to load into another project | Once a layout is selected |
| **Print settings...** | Page size, resolution, bleed, crop marks, safe zone and colour profile for Export PDF | Once a layout is selected |

**Ctrl+O** and **Ctrl+P** work from anywhere in the dialog, without opening
the menu.

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
| **Static frame resolution (dpi)** | 72 to 600; 200 by default, as in the dialog |
| **Title** | Replaces the project title; blank keeps it |
| **Export even if some items are blocked** | Off by default, so a **Blocked** item - an atlas on a static map, say - stops the run and names it. Pages after the first are *Not exported*, not Blocked: they are logged and page 1 is written |
| **Cartograph** | Where to write the `.html` |

Every Fidelity row is written to the log. Print settings, the Cartograph tab's
page fields, per-map choices from the Frames tab and the Layers tab's popup
and label choices are not used: the algorithm takes only its own parameters.

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
