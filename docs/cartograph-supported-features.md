---
title: What a cartograph keeps
seo_title: What a QGIS print layout keeps in a cartograph
description: >-
  Every print layout item - maps, grids, insets, legends, scale bars, north
  arrows, pictures, labels, shapes, atlas - and whether the cartograph keeps
  it, changes it or leaves it out.
lead_image:
  src: /images/cartograph-fidelity-tab.png
  alt: The Fidelity tab for a cartograph, listing layout items marked Changed with the reason for each.
---

# What a cartograph keeps

A print layout can hold far more than a web page can draw the same way. The
plugin translates a deliberate set of layout items, and everything it cannot
translate exactly is named on the **Fidelity** tab - nothing is dropped
silently.

The verdicts are the same ones the [web map](supported-features.md) uses:

| Verdict | Meaning |
|---|---|
| **Kept** | Comes across exactly as it is in the layout |
| **Changed** | Comes across, but not identically - the row says how |
| **Rasterised** | Inside a live frame, a layer style drawn as a picture because the web has no equivalent - as on the web map |
| **Not exported** | Left out. The rest of the page is still made |
| **Blocked** | Cannot be made as asked - a second page, or an atlas with nothing to drive it. The rest of the page is still saved, and the row says what to change |

Layers drawn inside a **live** frame are translated exactly as for the web
map, and their rows appear in the same report, one group per layer - see
[what gets exported](supported-features.md). A **static** frame is a picture
QGIS drew, so its layers come across as they look.

## The page

| In the layout | In the cartograph |
|---|---|
| Page 1 - size and orientation | **Kept** |
| Pages 2 and after | **Blocked**. Page 1 is still saved; the other pages are not. Use an [atlas](cartograph-atlas.md) for many pages |
| Item position, size, rotation | **Kept**, in millimetres from the page's top-left corner |
| Stacking order | **Kept** |
| Locked and hidden items | **Kept** - a hidden item stays hidden, in print too |
| Item groups | **Changed** - each item keeps its place; the grouping itself is not kept |
| Item opacity below 100% | **Changed** - drawn fully opaque |
| An item with no size | **Not exported** |

## Maps

| In the layout | In the cartograph |
|---|---|
| Map item, **static** frame | **Changed** - a pixel-exact image in the map's own CRS, at the DPI chosen on the Frames tab |
| Map item, **live** frame | **Kept** - an interactive map. In a CRS other than Web Mercator it is drawn in Web Mercator, and marked **Changed** <!-- verify: the Changed row for a live frame in another CRS comes with the carto/atlas reader --> |
| Map rotation | **Kept** |
| Overview (locator inset showing another map's extent) | **Kept** |
| Map themes and per-item layer styles | Not read yet for **live** frames, which draw the layers' current styles. Use a static frame where a map theme matters <!-- verify: no Fidelity row reports this yet --> |

## Grids

| In the layout | In the cartograph |
|---|---|
| Grid as lines, crosses or markers | **Kept**, in the grid's own CRS, with coordinates outside the frame |
| Frame and annotations only (zebra border) | **Changed** - drawn as crosses |
| Interval in page units | **Changed** - the interval is chosen automatically <!-- verify: carto/atlas reader --> |
| Annotations on some sides only, or inside the map | **Changed** - every side is labelled, outside <!-- verify: carto/atlas reader --> |
| Degrees and minutes | **Changed** - printed as degrees, minutes and seconds <!-- verify: carto/atlas reader --> |
| Custom annotation expression | **Changed** - printed as plain coordinates <!-- verify: carto/atlas reader --> |

## Legends

| In the layout | In the cartograph |
|---|---|
| Legend linked to a **live** map | **Kept** - its rows are worked out from the map as it is drawn, so they always agree with it |
| Legend linked to a **static** map | **Changed** - its rows are written in once, from the layers' styles |
| Legend title and columns | **Kept** |
| Legend not linked to a map | **Not exported** |
| Groups, hidden layer titles, entries reordered or a symbol edited in the legend only | **Changed** - the legend follows the layers' own entries <!-- verify: carto/atlas reader --> |

## Scale bars and north arrows

| In the layout | In the cartograph |
|---|---|
| Scale bar: Single Box, Double Box, Line Ticks Up, Down or Middle | **Kept** - worked out from the frame, in metric, imperial or nautical units |
| Scale bar: Stepped Line, Hollow | Drawn as a single box |
| Scale bar: any other style | **Changed** - drawn as a single box |
| Scale bar: Numeric | **Kept** - a scale text (`1:25 000`) that stays correct |
| Scale bar not linked to a map | **Not exported** |
| North arrow - a picture linked to a map, or whose file name contains "north" | **Changed** - the cartograph draws its own north arrow, pointing to the frame's true north |
| North arrow not linked to a single map, on a page with several | **Not exported** |

## Pictures, labels and shapes

| In the layout | In the cartograph |
|---|---|
| Picture (SVG, PNG, JPEG, ...) | **Kept** - embedded in the file |
| Picture whose file cannot be read | **Not exported** |
| Label text | **Kept** - size and colour too |
| Label: `@map_scale` | **Kept** live on a live map; written in as text on a static map, whose picture never moves |
| Label: `@map_crs`, `@project_title`, today's date in ISO form | **Kept** live |
| Label: `@atlas_featurenumber`, `@atlas_totalfeatures`, the atlas feature's fields | **Kept** live, inside an [atlas](cartograph-atlas.md) |
| Label: any other expression | **Changed** - worked out once when the cartograph is saved, and written in as text |
| Label rendered as HTML | **Changed** - the text is kept, the HTML formatting is not <!-- verify: carto/atlas reader --> |
| Italic text, text buffers, justified or vertically centred text | **Changed** <!-- verify: carto/atlas reader --> |
| Rectangle and ellipse shapes | **Kept** - fill, outline and corner radius |
| Triangle shapes | **Not exported** |
| A shape outline that is not a simple line | **Changed** - the shape is kept without its outline |

## Not exported

These layout items have no cartograph equivalent yet. Each is named on the
Fidelity tab by its type:

- polygons and polylines drawn on the page, and markers;
- HTML frames and attribute tables;
- elevation profiles, 3D maps and charts.

## Atlas

Covered in full on [atlas cartographs](cartograph-atlas.md). In short: kept,
one page per feature, as long as the map it drives is live and draws the
coverage layer - otherwise **Blocked**, with the fix.
