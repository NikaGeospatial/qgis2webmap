---
title: The Cartograph dialog, tab by tab
seo_title: The QGIS2WebMap cartograph dialog
description: >-
  Every control of the Cartograph (Beta) output - the live page, the print
  layout list, the Layout, Atlas and Checks tabs, where the file saves, the
  header buttons and the Share menu - and what each one does to the file.
lead_image:
  src: /images/cartograph-dialog.png
  alt: The dialog on Cartograph (Beta) - the print layout drawn on the left, and on the right the Print layout list, the Layout, Atlas and Checks tabs, and the file name with its save status.
---

# The Cartograph dialog, tab by tab

Choose **Cartograph** from the title menu at the top of the plugin's dialog
(**Web map ▾**) and the dialog becomes one screen: the print layout's
page on the left and a panel of settings on the right. The header holds
**colour vision**, **Preview in browser** and **Share**, and there is no Export
button. The web map's tabs are described in
[the dialog, tab by tab](the-dialog.md). The **Settings** and **Help** icons
at the right of the header are shared and work the same way.

Other ways in:

- **Web → QGIS2WebMap by NIKA → Create cartograph...**, or **Ctrl+Alt+C**
  (**Cmd+Alt+C** on macOS), opens the dialog straight on Cartograph (Beta).
- In the layout designer, **Layout → Export as cartograph (HTML)...** does the
  same and selects the layout you have open.
- On the web map, **Share → Make a cartograph...** builds a print layout from
  the map canvas and switches over - see [one-click layouts](one-click-layout.md).

## The document model, in one paragraph

A cartograph is **a file that is always saved**, not something an Export
button produces. Selecting a layout saves it at once, and every change after
that - in the panel, in the print layout itself, or to the style or data of a
layer it draws - rewrites the file about 600 ms later, through a temporary
file that is swapped in, so it is never half written. Preview, Share and
the NIKA hand-off all work on that file. The dialog's title names it. Closing
the dialog leaves it where it is, and reopening the plugin on the same project
picks it up again.

Settings are stored with the project, per layout: each layout remembers its
own file, frames, page fields and print settings.

---

## The page

The left side shows the selected layout, captioned **Layout preview — QGIS ·** and its
page size - *Layout preview — QGIS · A4 landscape*, for example. It is QGIS's own
drawing of the print layout, live: a change in the layout designer, a
restyled layer or a new title shows at once.

It is not the HTML. The cartograph can differ in places - a north arrow the
browser draws its own way, the page's theme - and the **Checks** tab lists
every difference. Beside the caption, a link such as **2 changes in the HTML**
opens Checks. To see the real cartograph, press **Preview in browser**. **Open layout editor**,
beside the caption, opens the layout in QGIS's own layout designer. The Page
settings (title, attribution, theme) change the HTML, so they show in
**Preview in browser**, not in this preview.

Under the page, the zoom bar: **Fit page**, **−**, the zoom level and **+**.
100% is the page at its real size on your screen. The mouse wheel zooms too.

Click an item on the page to select it, drag it to move it, or drag one of its
corner or edge handles to resize it; each drag is one undo step. With a title,
label or legend selected, the zoom bar also shows its text size: **A−** and
**A+** change it one point at a time, also as undo steps.

Left of them, **Select** and **Pan** (Select is on when the page opens). With
**Select** you click an item to select it, drag it to move it, or drag a
handle to resize it, with the same snapping and **Shift**/**Alt** keys as the
layout designer; map frames move and resize like any other item, and a locked
item stays where it is. Hold **Space** or drag with the middle button to pan
while Select is on, or choose **Pan** to drag the page around without
touching an item. Every move or resize is an undo step on the layout, so
**Ctrl+Z** and **Ctrl+Shift+Z** work on the page, the cartograph saves itself
after each edit, and the page stops fitting to the window once you have
clicked on it (**Fit page** fits it again). The layout is the same one the
layout designer shows, so an edit in either appears in both.

With no print layout in the project, the page says so and offers
**From canvas...** and **Import cartograph...** instead.

---

## Print layout

The top of the panel. The list holds the project's print layouts and follows
the Layout Manager as you add, rename or remove them. **Add...** beside it
makes another:

- **From canvas...** - opens the
  [template picker](one-click-layout.md#choose-a-template), then builds a
  layout named `carto: <project title>` from the map canvas in the template
  you chose, adds it to the project and selects it. Greyed out when there is
  no map canvas.
- **Import cartograph...** - turns a cartograph HTML file back into a new QGIS
  print layout, and selects it. Whatever QGIS cannot hold is listed when the
  import finishes. See [one-click layouts](one-click-layout.md#importing-a-cartograph-back-into-qgis).

Both are also at the end of the list itself.

![The panel's Print layout list, with the Add... menu open on From canvas... and Import cartograph....](images/cartograph-import.png)

**Browse templates...**, on its own line under the list, is the same
as **From canvas...**: it opens the template picker, which remembers the design
you chose last time, and builds another layout. The layout shown is kept as it
is; to change its text, move, resize or edit the items in the layout preview. A layout made from a
template says **Based on** and the template's name under the button. Greyed out when there is no map canvas.

Below the list are three tabs - **Layout**, **Atlas** and **Checks** - and,
at the foot of the panel, [where the file saves](#where-it-saves).

---

## Layout

Three sections that open and close. A closed section shows a few words about
what is in it, such as *Default theme* or *1 live, 1 static*.

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

### Map frames

One card per map item on the layout:

- its name - the map's ID in the layout designer - and, at the right, how many
  items on it the cartograph changes (**No changes**, or *2 changes*; Checks
  has the detail);
- its CRS, or *Unknown CRS*;
- **In the HTML** - **Live - pan and zoom**: an interactive map drawn in the
  browser, in Web Mercator or in the map's own projected CRS
  ([which one](live-or-static-frames.md#which-crs-a-live-frame-draws-in)).
  **Static - an image**: a picture of the map as QGIS draws it, in its own
  CRS;
- the image resolution, for a static frame only: **150**, **200** (the
  default) or **300 dpi**, never below the saved export resolution;
- **Match QGIS canvas** - sets the map's extent to what the QGIS canvas
  shows. It is an edit to your layout, so the layout designer's **Undo** takes
  it back.

![The Map frames section with two cards: Locator, EPSG:3857, Live, and Main map, EPSG:32648, Static at 200 dpi.](images/cartograph-03-frames.png)

A map starts **Live** when the browser can draw it where QGIS does - in
EPSG:3857 or EPSG:4326, or an unrotated projected map of vector layers and
image tiles - and **Static** otherwise. (The Main map in the picture was
switched to Static by hand.)
[Live or static frames](live-or-static-frames.md) is the whole story; the
short version is that live costs some styling and file size, and static costs
interactivity.

The cards follow the layout: add or delete a map in the layout designer and
the list rebuilds.

### Layers

The same list as the web map's Layers tab (search box included), with the same **Include**,
**Popups** and **Labels** columns and **Set every field to**. For a
cartograph it sets how the layers in **live** frames behave:

- **Popups** and the per-field popup settings apply. A layer with Popups
  unticked, or a field set to *Do not show this field*, has those values left
  out of the file - not merely hidden.
- **Labels** applies: unticked, the layer's labels are not drawn in live
  frames.
- **Include** chooses the layers of the map frame named at the top of the
  section (**Layers for:**, a choice of frame when the layout has several).
  Unticking a layer takes it off that map in the layout itself, so it
  disappears from the preview, the HTML, the image and the PDF, and from the
  legends linked to that map. **Undo** in the layout (Ctrl+Z) puts it all back,
  in its old place. A map that followed the QGIS Layers panel or a map theme
  gets its own list of layers the first time you change a tick, drawn with the
  styles it had; the note under the selector says which it is. If the layout's
  atlas takes its pages from a layer the map no longer draws, a warning says so
  and the atlas buttons stay off until it is ticked again. The web map keeps
  its own Include choices, which switching output never touches.

Static frames are pictures drawn by QGIS, so none of this changes them.
Changing anything here saves the cartograph again.

---

## Atlas

One page per feature of a coverage layer, in one file. The tab edits the
layout's own atlas - the same settings as QGIS's Atlas panel - so every change
goes on the layout's undo stack, and **Undo** in the layout designer takes it
back. The tab reads **Atlas (on)** while the atlas is on.
[Atlas cartographs](cartograph-atlas.md) explains the rules.

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
| A warning line | Why the cartograph will not page, when it will not - for example *Set the atlas map to Live in Map frames* (Map frames, on the Layout tab) |
| **Export all pages (PNG, ZIP)...** | Opens the cartograph, which renders every page and downloads them as one ZIP |
| **Print all pages...** | Opens the cartograph with every page laid out for one print. Choose **Save as PDF** for a multi-page PDF, each page at its true size in a Chromium-based browser |

The two buttons stay greyed out until the saved cartograph has atlas pages.

---

## Checks

The cartograph's report: every layout item that changed or was left out on
the way to the HTML, with the verdicts **Kept**, **Changed**, **Rasterised**,
**Not exported** and **Blocked**. The tab's title carries the count -
**Checks (2)** - and it refreshes after each save. Before the first save it
reads *Not checked yet*, and **Check now** saves the cartograph to check it.

Each problem is one readable line; click it to read the whole sentence and,
where the fix is in the dialog, a button that goes there - **Show in Layers**,
for example. **Filter** and the search box work as on the web map's
[Fidelity tab](the-dialog.md#fidelity).

Layout items are listed under **Whole map**, and the layers the frames draw
get their own groups - a static frame's layers marked **Rasterised**, since
they are part of its picture. The verdicts are explained in
[what a cartograph keeps](cartograph-supported-features.md).

---

## Where it saves

The foot of the panel names the file: its name in bold, its folder under it,
and two links - **Change...** to save it somewhere else, and **Show in
folder**. Under them, the save status:

- *Saved just now · 3.3 MB* - the file on disk matches what you see;
- *Changes pending* - you edited something and the file has not been
  rewritten yet;
- *Saving the cartograph...* - the file is being rewritten. These two are about
  the cartograph's HTML file only; the dialog never saves your QGIS project;
- *Not saved yet* - the first save is still running;
- a sentence starting *Could not...* - the save failed, and why. The previous
  file is left untouched.

By default a cartograph saves in **Documents/QGIS2WebMap/Cartographs**, named
after the project and the layout - `parcels-Survey.html` for the layout
*Survey* in `parcels.qgz`. Anything other than letters, digits, dots and
hyphens becomes a hyphen. A project that has never been saved has no name
yet, so the layout's name alone is used, and the cartograph still saves at
once. If a file of that name is already there, the new one gets a number:
`parcels-Survey (2).html`. To use another folder for every new map, see
**Settings → Saving** in [the dialog, tab by tab](the-dialog.md#settings).

A cartograph is always **one standalone HTML file**. There is no Share ZIP or
Folder type as there is for the web map: the page, its static images and the
data of its live maps are all inside it.

---

## The header

At the top right, reachable whatever the panel shows:

- **Colour vision** (the contrast icon) - opens the
  [Colour vision](colour-vision.md) dialog: simulate colour-vision deficiencies
  on the canvas and in the browser, and check which legend classes become
  indistinguishable. Stays pressed while a simulation is on, and its tooltip
  names it.
- **Preview in browser** - opens the saved cartograph in your default browser.
- **Share** - opens the [Share menu](#the-share-menu).

Then the **Settings** and **Help** icons, as on the web map.

![The header on Cartograph: the colour vision icon, Preview in browser and Share, then the Settings and Help icons.](images/cartograph-corner.png)

The browser opens the file through a small server on your own machine
(`127.0.0.1`) rather than as `file://`, because some browsers refuse to run a
live map from a file path. Nothing leaves your computer.

---

## The Share menu

The header's **Share** button. Outputs come first, then the file
actions, then the greyed NIKA rows.

| Row | What it does | Available |
|---|---|---|
| **Export PDF...** (**Ctrl+P**) | Opens the cartograph and its print dialog at true page size, with its background colours kept even when the browser's **Background graphics** is off - or, with a CMYK or ICC profile chosen in Print settings, has QGIS write the PDF | Once the file exists |
| **Export PNG...** | Opens the cartograph, which renders an image of the page at the saved resolution and downloads it - page 1 of an atlas; the Atlas tab exports every page | Once the file exists |
| **Export QGIS layout template (.qpt)...** | Saves the source layout as a `.qpt` template, to load into another project | Once a layout is selected |
| **Print settings...** | Page size, resolution, bleed, crop marks, safe zone and colour profile for Export PDF | Once a layout is selected |
| **Open in browser** (**Ctrl+O**) | Opens the cartograph in your browser, as Preview does | Once the file exists |
| **Copy file path** | Puts the cartograph's path on the clipboard | Once the file exists |
| **Reveal in Finder** / **Show in Explorer** / **Show in file manager** | Opens the folder the cartograph is in | Once the file exists |
| **Prepare enhancement prompt...**, **Prepare critique prompt...** (NIKA hand-off) | Shown greyed out: the hand-off is not available for cartographs yet | Not yet |

**Ctrl+O** and **Ctrl+P** work from anywhere in the dialog, without opening
the menu.

Export PDF and Export PNG are **derived** from the cartograph rather than being
the export: the browser makes them from the saved file. Both wait until every
raster in the page has drawn - a note at the foot of the page says so - and if
one cannot draw, they stop with a message rather than print or save a page
without it. Rasters draw with no internet connection. Both are covered in
[printing a cartograph](printing-a-cartograph.md).

There is deliberately no Share ZIP, no Folder, and no report row. A cartograph
travels as its one file, and the Checks tab is always one click away.

---

## Processing

For models and batch runs, the Processing Toolbox has **QGIS2WebMap by NIKA →
Cartograph → Export print layout to cartograph (Beta)**. It runs the same
export as the dialog, once, with these parameters:

| Parameter | What it is |
|---|---|
| **Print layout** | The layout to export |
| **Frames** | **Automatic** (live where it matches QGIS, static otherwise - the dialog's default), **All live** or **All static** |
| **Static frame resolution (dpi)** | 72 to 600; 200 by default, as in the dialog |
| **Title** | Replaces the project title; blank keeps it |
| **Export even if some items are blocked** | Off by default, so a **Blocked** item - an atlas on a static map, say - stops the run and names it. Pages after the first are *Not exported*, not Blocked: they are logged and page 1 is written |
| **Cartograph** | Where to write the `.html` |

Every check is written to the log. Print settings, the Page fields, per-map
choices from Map frames and the Layers section's popup and label choices are
not used: the algorithm takes only its own parameters.

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
