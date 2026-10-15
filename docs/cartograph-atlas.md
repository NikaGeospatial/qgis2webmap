---
title: Atlas cartographs
seo_title: A QGIS atlas as one HTML file, one page per feature
description: >-
  Turn a QGIS print layout's atlas into one cartograph with a page per feature
  - how the pages are turned, what the atlas needs, what changes from QGIS,
  and exporting every page.
---

# Atlas cartographs

A QGIS atlas makes one page per feature of a coverage layer: a sheet per
parcel, per site, per corridor segment. A cartograph keeps the atlas in **one
file**. The reader opens it once and turns the pages in the browser, and the
map, the scale bar and every page-dependent label update as they go.

<!-- verify: this page was written from the specification and the atlas
reader while the Atlas tab was being built - check control names, the export
route and the page-count wording against the finished build -->

## Setting it up

1. In the layout designer, set up the atlas as usual: **Atlas → Atlas
   Settings**, tick **Generate an atlas**, choose the **coverage layer**, and
   on the main map's item properties tick **Controlled by atlas**.
2. Open the cartograph on that layout. On the **Frames** tab, make sure the
   map the atlas controls is **Live**.
3. Make sure that map **draws the coverage layer**. To keep it from showing,
   tick **Hide coverage layer** in the atlas settings rather than removing it
   from the map.
4. Check the **Atlas** tab: it shows the coverage layer and how many pages the
   atlas has.

The cartograph saves itself as usual. One file holds every page.

## Why the map has to be live

A static frame is a picture, rendered once. It would show the same place on
every page. The cartograph turns pages by moving a live map to each feature,
and it finds the features in that map's own data - so the coverage layer has
to be in it.

When either is missing, nothing half-works: no atlas is written, and the
Fidelity tab marks it **Blocked** with the fix - *Set the atlas map to Live in
the Frames tab*, or *Add the coverage layer ... to map ...'s layers*.

## Turning the pages

In the browser, a reader can:

- press **←** and **→**, **Page Up** and **Page Down**, **Home** and **End**;
- use the pager at the foot of the sheet - `‹ 3 / 12 · Lot 12 ›`.

The pager is for the screen only: it is never printed and never appears in an
exported image.

On every page the map refits to that feature, the scale bar is worked out
again (the scale really does change from page to page), and labels using the
feature's fields, `@atlas_featurenumber` or `@atlas_totalfeatures` show that
page's values.

## What changes from QGIS

| In QGIS | In the cartograph |
|---|---|
| Map scaling **Margin around feature** | Kept |
| Map scaling **Fixed scale** | Each feature is fitted into the frame instead, so the scale changes from page to page. Marked **Changed** |
| Map scaling **Predefined scale** | Fitted exactly instead of snapping to the nearest listed scale. Marked **Changed** |
| **Hide coverage layer** | Kept on live frames |
| **Only show the current atlas feature** | Not kept - every feature of the layer is drawn. Marked **Changed** |
| **Filter with** | Kept when the expression has a cartograph equivalent; otherwise left out, so every feature gets a page. Named on the Fidelity tab |
| **Sort by** | Kept when it sorts by a field. An expression cannot be kept, and pages follow the layer's own order |
| **Output filename expression** | Kept when it can be translated; otherwise pages are named `page-1`, `page-2`, ... |
| **Page name** | Kept when it can be translated; otherwise the pager shows page numbers |
| A label expression that depends on the atlas feature in another way | Reads the same on every page. Named on the Fidelity tab |

The cartograph can have **fewer pages** than QGIS's atlas. It skips features
the map does not draw - a category switched off in the layer's style, for
example - so it never prints a sheet for a feature nobody can see.

## Exporting every page

**Export PNG...** in the Share menu renders pages at 300 dpi. For an atlas, the
browser renders the pages one after another through the same live map and
downloads them as **one ZIP**, named by the filename pattern, because browsers
block a run of separate downloads.

<!-- verify: how the all-pages ZIP is started from the plugin - Export PNG's
tooltip still says "300 dpi image of the first page", and the runtime's URL
trigger renders one page per request (?export=png&page=N) -->

To print the series, see
[printing a cartograph](printing-a-cartograph.md#printing-an-atlas).

## Limits

- A layer whose features live on a server (a tiled or streamed layer) cannot
  drive an atlas: the cartograph needs the features inside the file.
- Very large atlases are slow to export as images, because every page is
  rendered in turn by one map. They are not slow to open.
