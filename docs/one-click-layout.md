---
title: One-click layouts and the round trip
seo_title: Make a QGIS print layout from the map canvas in one click
description: >-
  Build a print layout from the QGIS map canvas in one click - title, map,
  legend, scale bar - keep editing it in QGIS, and import a
  cartograph back as a layout.
lead_image:
  src: /images/cartograph-one-click.png
  alt: The Cartograph screen after Add, From canvas - the new layout "carto: Parcel survey" is drawn on the left and selected in the Print layout list, and the bar at the foot says it was created from the canvas.
---

# One-click layouts and the round trip

You do not need a print layout to make a cartograph. One click turns the map
canvas into one - and, unlike tools that go straight to a PDF, it leaves a
real QGIS print layout behind that you can keep editing. A cartograph can also
go the other way, back into QGIS as a layout.

## From the canvas

1. Set the canvas to the view you want on the page: the extent, the layers,
   even a rotation.
2. On Cartograph, press **Add...** beside the **Print layout** list and
   choose **From canvas...**. On the web map, **Share → Make a cartograph...**
   does the same and switches the dialog to Cartograph (Beta).
3. Pick a [template](#choose-a-template), a page size and an orientation, and
   click **Create layout**.

The plugin builds a print layout called **`carto: <project title>`**
(`carto: map` for a project with no title, and `carto: map (2)` if the name
is taken), adds it to the project, and selects it in the Print layout list.

![The Cartograph screen after From canvas: the Print layout list shows "carto: Parcel survey", and the new layout is drawn on the left.](images/cartograph-one-click.png)

## Choose a template

Ten starters: five quiet essentials and five specialised compositions, each
made for a different kind of page. Each preview is the real page drawn
with the map the canvas shows now, so what you pick is what you get.

The picker shows **All**, **Essentials** and **Characterful** at the top to
narrow the list, with **Page size** and **Orientation** beside them. Below is
a scrolling list of the designs: a large preview of each, its name, one line
on what it is for, and what that page includes for this project. A second,
quieter line, **Can also show**, lists what the design would add with more
content - notes from the project's abstract or Signal's key figures; point at it to see where each comes from. Point at a card to see, in its tooltip, what the design is best
for (also listed below). **Create layout** builds the one selected.

The **Text sizes** button beside the buttons opens three sliders, **Title**,
**Legend** and **Notes**, each a smooth slider from 80% to 130% of the design's
text size in steps of 1%; the label shows the percent as you drag, and the page
is redrawn when you let go. The mouse wheel never moves them (it scrolls the
panel), and a click does not leave one focused; Tab reaches them and the arrow
keys move them. They apply to every design (the notes size also sets Signal's key
figures), so a smaller size gives the map more of the page and a larger one
takes it back. The text never goes under the readable minimum for the page, so
a smaller size does nothing once that is reached. 100% is the default, and
the choice is saved with the project. The Layout tab has no such sliders: once a
layout exists, change its text by selecting, moving or resizing the items in the
layout preview, or pick the sizes here before creating it.

![The template picker: the All, Essentials and Characterful buttons, page size and orientation at the top, and the designs as large previews of the current map, each with its name, a one-line description and what it includes.](images/cartograph-templates.png)

**Essentials**

- **Daylight** - a balanced everyday sheet: title, large map, legend beside
  or below it (whichever leaves the map larger), scale bar and source line.
  The safest first choice; the layout described
  [below](#what-the-daylight-layout-contains). *Best for: everyday maps, reports and emails.*
- **Clearline** - a wide map with the legend in columns in a band under it,
  for wide extents and long legends. *Best for: wide maps with long legends.*
- **Bare Map** - just the map and its data credits, with no frame line, for
  placing in another document. The credits stay because most basemap and
  open-data licences require them. *Best for: pasting into a report, slide or web page.*
- **Caption** - a report figure: the map with a caption under it (the title
  as a bold lead, then the project's abstract), the scale bar and the source. *Best for: reports and papers that caption their figures.*
- **After Hours** - a dark page with an uninterrupted map. Its heading,
  legend and scale have their own space, so a dense key never covers the
  terrain. The legend fits beside or below the map at a readable size. *Best for: dark-room screens and projected displays.*

**Characterful**

- **Fieldbook** - a survey sheet with a labelled grid in the map's own units
  and a title block with the scale, CRS, author and date, in a column beside
  the map or a band under it - whichever shows your view larger, so a wide
  view gets a wide frame. *Best for: field surveys and site records.*
- **Broadside** - a poster: a big headline that steps down in size for long
  titles, a dominant map, and the story, legend and scale bar in a rail beside
  it, or in a band below it when that shows your view larger - on a portrait
  page, or for a wide view. *Best for: posters and wall displays.*
- **Near & Far** - the map with a locator inset that outlines where it is,
  beside the map or below it, whichever shows your view larger. *Best for: maps of a small area that need locating.*
- **Signal** - a data-led page: the map with up to three key figures you
  supply. Type them under the list when Signal is selected - a value such as
  *214* and what it counts, such as *parcels surveyed*. They are saved with
  the project. With a substantial legend, Signal can arrange the figures in a
  row or give the key a full-height column to keep its entries readable. With
  no figures the page becomes the map and its notes. *Best for: data summaries and briefings with key figures.*
- **Wayfinder** - a long map for routes, rivers and coasts, with a distance
  scale. It adds no routes or waypoints of its own. *Best for: routes, rivers and coasts.*

Legends stay readable: their text never goes below 8 pt. A crowded legend first gets more room, up to half the space the map
and legend share; if it still cannot fit, it leaves its last entries out.
The card then says *Legend: 3 entries do not fit at a readable size*, and
the **Checks** tab of the new layout says the same until you put them back
(a larger page, shorter layer names, or a bigger legend and the entries
re-added in its item properties).

The **page size** can be A4, A3, Letter, Tabloid, A5, A2, A1, A0 or Legal;
**Automatic** orientation takes whichever shows the map larger. The picker
remembers your last choice.

The panels' text comes from the project: the title from **Project Properties
→ General → Project title**, the story text from **Metadata → Abstract**, the
author from **Metadata**. Templates never put stand-in text on the page: until
there is an abstract, a design leaves its text panel out and gives the map the
room.

Each card has **Preview** to inspect a larger page before creating it. You
can also select a design in the list and press **Space**. Only that page is
rendered at the inspection size; closing it creates no layout and saves no
settings.

**Map framing** controls how the canvas view fits the template. **Fill frame
(crop edges)** fills the main map frame and trims projected raster boundaries
where possible. It crops the view, preserves its centre and projection, and
never changes the source data. Daylight and Caption also expand their map
frames to use the available space in this mode. Choose **Whole view** to keep the entire canvas
extent, with extra surroundings where the frame has a different shape. This
choice is remembered when you create a layout; cancelling changes nothing.

A locator inset is a static frame - a picture of its map, as QGIS draws it,
and it works offline: it draws the area-wide layers of the project (rasters,
polygons and lines, not the points that mark places on the main map), without
labels, and outlines the main map's extent. How much it shows comes from those
layers - their extent, with a margin, up to eight times the main view - rather
than a fixed zoom-out. When the layers reach no further than the main view
there is no wider context to show: the inset then shows only a margin round the
map, the template picker and Checks say so, and you can delete it on the page
(the page keeps working) or add a layer that covers the wider area. A remote
basemap is never added for you.

Continuous rasters, such as elevation, are drawn with the source pixels visible
when enlarged. In **Map frames**, a map's **⋯** menu has **Smooth when
enlarged** for each continuous raster it draws: it blends the pixels for that
map only, never changes the layer's own style, and is not offered for
categorical rasters, whose classes it would blur.

## What the Daylight layout contains

An **A4** page, in whichever orientation gives the map more room, with:

- **the map**, at the canvas's extent, CRS and rotation, showing the layers the
  canvas showed at that moment, in the same order;
- **a title** from the project title, under a short accent mark;
- **a legend** beside or below the map, whichever leaves the map larger, with
  one row per layer;
- **a scale bar** under the map, bottom left;
- **an attribution line** beside the scale bar (under it on a narrow map),
  from the layers' own credits, when they have any;
- **a north arrow**, only when the canvas is rotated;
- **a grid** in degrees, when the canvas CRS is geographic.

Nothing overlaps the map, so when you open the layout in the designer you are
not digging furniture out from on top of it.

The map item keeps **its own list of layers** (QGIS's *Lock layers*).
Switching a layer on in the Layers panel afterwards does not add it to the
page; unlock the layers in the map's item properties if you want the page to
follow the canvas.

Whether the map becomes a live or a static frame follows the usual
[rule](live-or-static-frames.md#the-default): live when the browser can draw
it where QGIS does, static otherwise.

## Keep editing it in QGIS

The new layout is an ordinary print layout. Open it from **Project → Layouts**
and change anything - move the legend, restyle the title, add a logo. The
cartograph follows: each change in the layout designer saves the cartograph
again about half a second later.

**The first save** happens as soon as the new layout is selected, saved
project or not: its cartograph goes in **Documents/QGIS2WebMap/Cartographs**,
named after the project and the layout - `parcels-carto-Parcel-survey.html`
for `parcels.qgz`. **Change...** at the foot of the panel puts it somewhere
else.

## Importing a cartograph back into QGIS

**Add... → Import cartograph...** on Cartograph reads a cartograph HTML file -
one this plugin made, one edited by hand, or one edited by an AI agent - and
builds a new print layout from it:

- named after the cartograph's title (or its file name), made unique;
- every item keeps the ID the cartograph used, so the Items panel shows the
  same names;
- static frames come back at their georeferenced extent and CRS; live frames at
  their camera's view, in the projected CRS the map was drawn in, or in Web
  Mercator when it was drawn in Web Mercator;
- pictures embedded in the file are written to a `<name>.images` folder beside
  it, because a QGIS picture item needs a file to point at;
- labels get their QGIS expressions back - a live scale becomes
  `@map_scale`, the date becomes `format_date(now(), 'yyyy-MM-dd')`, atlas
  fields become `attribute(@atlas_feature, ...)`;
- an atlas comes back as the layout's atlas, when a layer with the same name is
  in the project.

The new layout is selected in the Print layout list, so the cartograph you
see is now made from it.

**The map data is not imported.** A live frame's features travel inside the
cartograph, but a QGIS layout cannot hold data - its maps draw the project's
own layers. Import into the project the cartograph came from, or one with the
same layers.

Whatever QGIS cannot hold is listed when the import finishes, under *Some parts
could not be carried into QGIS*: a live frame's tilt, a legend's written-out
rows, page settings with no layout equivalent, a text token with no QGIS
variable. Nothing is dropped without being named.

## Export QGIS layout template

**Share → Export QGIS layout template (.qpt)...** saves the source layout as a
`.qpt` file - the layout designer's own template format, which keeps every
item exactly. Load it into another project from **Project → Layout
Manager**, under *New from template*.

The suggested file name is the layout's name with characters a file system
rejects (such as `/`) replaced by dashes. If the file cannot be written, the
export shows an error instead of reporting success.
