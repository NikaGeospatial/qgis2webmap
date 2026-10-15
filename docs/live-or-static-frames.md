---
title: Live or static frames
seo_title: Live or static map frames in a QGIS cartograph
description: >-
  When a map in a cartograph stays interactive and when it is a pixel-exact
  image - what each costs in accuracy, file size, printing and privacy, and how
  to choose per map.
lead_image:
  src: /images/cartograph-03-frames.png
  alt: The Frames tab, with one map set to Live and another set to Static.
---

# Live or static frames

Every map item on your print layout becomes a **frame** in the cartograph, and
each frame is one of two kinds. You choose per map, on the **Frames** tab.

| | Live | Static |
|---|---|---|
| What the reader gets | A real map inside the page: pan, zoom, click a feature | A picture of the map, exactly as QGIS draws it |
| Projection | The map's **own CRS** when it is a projected one (UTM, a national grid, an equal-area projection) and its layers are vectors or image tiles; **Web Mercator** (EPSG:3857) otherwise - see [below](#which-crs-a-live-frame-draws-in) | The map's **own CRS**, untouched |
| Styling | Translated, with the web map's [fidelity](supported-features.md) - the Fidelity tab lists what changes | Pixel-exact: whatever QGIS can draw |
| Data in the file | The features, and the attributes their popups show | Only the image |
| Sharpness in print | Re-rendered at print resolution | Fixed at the DPI you chose (150, 200 or 300; 200 by default) |
| Needs WebGL in the reader's browser | Yes | No |
| File size | Larger - the map's data and the map runtime, a few megabytes | Small - an image per frame |
| Can drive an [atlas](cartograph-atlas.md) | Yes | No |

## The default

The plugin starts a map **Live** whenever the browser can draw it where QGIS
does, and **Static** otherwise. You can change either:

- **EPSG:3857 or EPSG:4326** → **Live**. The live frame shows the map in the
  projection it already uses (or close to it), so nothing is lost by making it
  interactive.
- **A projected CRS** - UTM, a national grid, an equal-area map - → **Live**
  when the frame can draw in that CRS (see
  [Which CRS a live frame draws in](#which-crs-a-live-frame-draws-in)): the
  map is not rotated and every layer is a vector layer or image tiles.
- **Anything else** → **Static**: a rotated projected map, or one with a
  raster file or non-image tiles. A static frame keeps every style and every
  layer exactly as QGIS draws it, and reads without WebGL.

Switch a live map to Static when a style matters more than interaction - a
static frame is a picture QGIS drew itself - or when the page must read
without a graphics card.

The same rule is the **Automatic** choice of the
[Processing algorithm](cartograph-dialog.md#processing), which also offers *All live* and
*All static*.

## When to switch a map to Live

- The reader should explore: zoom into a site, click a parcel for its
  attributes.
- The map is in Web Mercator or WGS 84 already.
- It is the map an [atlas](cartograph-atlas.md) turns - an atlas only pages
  through a live frame.

### Which CRS a live frame draws in

A live frame draws in the map's own CRS, exactly where QGIS draws it, when the
map:

- is in a **projected** CRS - UTM, a national grid, an equal-area or conic
  projection - or a **geographic** one such as EPSG:4326, which is drawn as
  QGIS draws it: one scale in degrees on both axes;
- is not rotated;
- carries only vector layers and image tiles (an XYZ layer of `.png`, `.jpg`
  or `.webp` tiles).

The Fidelity tab then marks the frame **Kept**. A live frame has no basemap in
a projected CRS; add an XYZ layer to the map for one.

Otherwise it is redrawn in Web Mercator, and the Fidelity tab marks it
**Changed**, with the reason: shapes, and distances away from the centre,
differ slightly from the layout. That happens for

- a **rotated** map - the rotation is kept, in Web Mercator;
- a map carrying an embedded **raster** or **vector tiles** - the row names
  the layer.

## When to keep a map Static

- The projection matters - a survey plan, a cadastral sheet, anything measured.
- The styling uses something the web map cannot draw. A static frame is drawn
  by QGIS, so it looks exactly as it does in the layout designer.
- The features should not travel. A static frame carries a picture, not
  features: nobody can open the file and read them. (To keep only some
  attributes out of a live frame, the Layers tab's popup settings are enough.)
- The reader may have no WebGL - an old work laptop, a locked-down virtual
  desktop, a mail or chat preview. See below.

## A page with only static frames

When every frame is static, the cartograph does not carry the map runtime at
all. It is a page of images, text and shapes, so it is **small**, opens
instantly and **needs no WebGL**. It also
[sends nothing](privacy.md#cartographs) when it is opened.

It even reads with JavaScript switched off - in a mail client's or file
manager's preview, say - because its images, text and shapes are placed by
the page's own layout rules. A cartograph with a live frame shows a short
note there instead, asking to be opened in a browser.

It is also the only kind of cartograph QGIS can turn into a
[CMYK or ICC-profiled PDF](printing-a-cartograph.md#cmyk-and-print-shop-profiles).

## Resolution of a static frame

The **DPI** column sets how finely a static frame's image is rendered: 150,
200 (the default) or 300. 150 dpi is enough for a screen, 200 for an office
printer; choose 300 for print-shop work. Each step up roughly doubles the
image's share of the file.

Printing does not re-render a static frame, so its DPI is the sharpest it will
ever print. A live frame has no such ceiling: when the page is printed or
exported it is re-rendered at the print resolution.

## Legends, scale bars and labels follow the frame

- A legend on a **live** frame works its rows out from the map as it is drawn,
  so it always agrees with it. A legend on a **static** frame has its rows
  written in once, from the layers' styles; the Fidelity tab marks it
  **Changed**.
- A scale bar, north arrow and grid are worked out from the frame's
  georeference, whichever kind it is.
- A label showing `[% @map_scale %]` stays live on a live frame and updates as
  the reader zooms. On a static frame it is written in as text, because the
  picture never moves.

## The trade, briefly

Make a frame live when the reader gains something from moving it. Leave it
static when the page has to be exactly what you designed - which, for most
print layouts, is most frames.
