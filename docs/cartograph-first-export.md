---
title: Your first cartograph
seo_title: Export a QGIS print layout to a cartograph
description: >-
  A walkthrough from a QGIS print layout to a single HTML cartograph you can
  email and print - choosing the source, the file, live or static frames, and
  reading the Fidelity tab.

# Opt-in HowTo markup, read by `_layouts/default.html`. Kept in front matter
# because the Help tab renders this file with HTML disabled. Keep the wording
# matching the sections below.
howto:
  name: Export a QGIS print layout to a cartograph
  total_time: PT10M
  steps:
    - name: Make the print layout
      anchor: 1-make-the-print-layout
      text: >-
        Build the layout in QGIS's layout designer as usual - maps, legend,
        scale bar, north arrow, title. Only the first page is exported. If you
        have no layout, the plugin can build one from the map canvas.
    - name: Open the Cartograph output
      anchor: 2-open-the-cartograph-output
      text: >-
        Choose Web, then QGIS2WebMap by NIKA, then Create cartograph. From the
        layout designer, choose Layout, then Export as cartograph (HTML). Or
        open the plugin and press Cartograph (Beta) at the top of the dialog.
    - name: Choose the source
      anchor: 3-choose-the-source
      text: >-
        On the Cartograph tab, pick the print layout from the Source list, or
        choose Current canvas to have one built from the map canvas.
    - name: Check where it saves
      anchor: 4-check-where-it-saves
      text: >-
        The Save to field names the cartograph file, beside the project and
        named after the layout. The file is rewritten about half a second
        after every change, and the line under the form says when it was
        last saved and how large it is.
    - name: Choose live or static frames
      anchor: 5-choose-live-or-static-frames
      text: >-
        On the Frames tab, each map on the layout is Live (an interactive map,
        drawn in Web Mercator) or Static (a pixel-exact image in the layout's
        own CRS, at the DPI you choose).
    - name: Preview it
      anchor: 6-preview-it
      text: >-
        Press Preview, or Open in browser, to open the saved cartograph in
        your browser.
    - name: Read the Fidelity tab
      anchor: 7-read-the-fidelity-tab
      text: >-
        Before you send the file, read the Fidelity tab. It lists every layout
        item that changed or was left out on the way to the cartograph.

lead_image:
  src: /images/cartograph-01-source.png
  alt: The Cartograph tab with the Source list highlighted, showing the print layout "Parcel survey".
---

# Your first cartograph

A **cartograph** is a print layout as one HTML file: it opens by
double-click, prints at the layout's page size, and its maps can stay live.
This page goes from a QGIS print layout to a file you can send. If you have not
installed the plugin yet, start with [installation](installation.md).

## 1. Make the print layout

Build the layout in QGIS's layout designer the way you always would - one or
more maps, a legend, a scale bar, a north arrow, a title. The plugin reads the
layout as it is, and follows it: change the layout later and the cartograph
updates itself.

Two limits worth knowing before you start:

- **Only page 1 is exported.** A layout with more pages still saves, with page 1
  only, and the Fidelity tab marks the rest **Blocked**. An
  [atlas](cartograph-atlas.md) is the way to get many pages.
- **Some items have no cartograph equivalent** - HTML frames, attribute
  tables, polylines and polygons drawn on the page, among others. They are left
  out and named. [What a cartograph keeps](cartograph-supported-features.md)
  has the full list.

No layout at all? Skip to step 3 and choose **Current canvas**.

## 2. Open the Cartograph output

Any of these opens the plugin on Cartograph (Beta):

- **Web → QGIS2WebMap by NIKA → Create cartograph...**
- In the layout designer, **Layout → Export as cartograph (HTML)...** - this
  also selects the layout you have open.
- Open the plugin as usual (**Ctrl+Alt+W**, **Cmd+Alt+W** on macOS) and press
  **Cartograph (Beta)** beside **Make:** at the top.

![The top of the dialog, with Cartograph (Beta) highlighted beside Make:.](images/cartograph-make-switch.png)

The dialog remembers which of the two you used last, per project.

## 3. Choose the source

On the **Cartograph** tab, under **Source**:

- **A print layout** - pick it from the list. Every print layout in the
  project is there, and the list follows the Layout Manager: add, rename or
  delete a layout and it updates.
- **Current canvas (creates a print layout)** - one click builds a new layout
  called `carto: <project title>` from what the map canvas shows, adds it to
  the project, and selects it. See [one-click layouts](one-click-layout.md).

![The Cartograph tab, with the Source list highlighted.](images/cartograph-01-source.png)

**Import cartograph...**, beside the list, goes the other way: it turns a
cartograph file back into a QGIS print layout.

## 4. Check where it saves

**Save to** under **Cartograph file** is where the cartograph lives. It starts
beside your project file, named after the layout - `Parcel survey.html` for a
layout called *Parcel survey*. Press **Choose…** to put it somewhere else.

There is **no Export button**, on purpose. The cartograph is a file that is
always up to date: about half a second after any change - on these tabs, or in
the layout itself - the plugin rewrites it. The line under the form says how
it went:

| It says | It means |
|---|---|
| *Not saved yet* | Nothing has been written. Change any setting, or press **Check now** on the strip at the foot of the dialog |
| *Saved 2 s ago · 3.2 MB* | The file on disk matches what you see |
| A sentence starting *Could not...* | The save failed, and why. The previous file is left untouched |

![The Cartograph tab, with the line "Saved 2 s ago · 3.2 MB" highlighted under the form.](images/cartograph-02-saved.png)

A save writes to a temporary file and swaps it in, so closing QGIS mid-save
never leaves half a file behind. The first save on a computer may ask to
download the OnlyMap runtime, the same one-off download the web map uses.

## 5. Choose live or static frames

The **Frames** tab has one row per map on the layout:

| Column | What it is |
|---|---|
| **Map item** | The map's ID from the layout designer |
| **CRS** | The map's CRS |
| **Frame** | **Live** or **Static** |
| **DPI** | Resolution of a static frame's image: 150, 200 or 300. Greyed out for a live frame |
| **Changes** | How many Fidelity rows about this map are not *Kept* |
| **Use canvas view** | Sets this map's extent to what the QGIS canvas shows |

![The Frames tab, with the Frame column highlighted: the Locator map is Live and the Main map is Static.](images/cartograph-03-frames.png)

The default follows the map's CRS. A map in **EPSG:3857** or **EPSG:4326**
starts **Live**, an interactive map in the page. Anything else - a UTM survey,
a national grid - starts **Static**: an image of the map exactly as QGIS draws
it, in its own CRS. [Live or static frames](live-or-static-frames.md) explains
the trade, including why a page of only static frames is small and needs no
graphics card.

## 6. Preview it

**Preview** in the top-right corner opens the saved cartograph in your default
browser. So does **Open in browser** beside it; its arrow opens the
[Share menu](cartograph-dialog.md#the-share-menu), with PDF and PNG export,
the layout template and the NIKA hand-off.

Nothing opens until there is a saved file. If you press Preview before the
first save, the bar at the foot of the dialog says so.

## 7. Read the Fidelity tab

The **Fidelity** tab lists every layout item that did not come across exactly,
with one of the same verdicts the web map uses - **Kept**, **Changed**,
**Not exported**, **Blocked**. It fills after each save; the strip at the foot
of the dialog sums it up whichever tab you are on.

![The Fidelity tab for a cartograph: a static map frame, its legend, the north arrow and a title with a frozen expression are marked Changed.](images/cartograph-fidelity-tab.png)

Typical rows for a first cartograph:

- *Rendered as a static, pixel-exact raster rather than a live map* - a map you
  left static.
- *Expression `round(@map_scale)` frozen at export time* - a label expression
  the page cannot work out by itself, written in as text.
- *The QGIS north-arrow picture is replaced by the cartograph runtime's own
  north indicator glyph.*

Then send the file. The recipient needs a web browser and nothing else. See
[sharing a cartograph](share-qgis-print-layout-without-qgis.md) and
[printing a cartograph](printing-a-cartograph.md).
