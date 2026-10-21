---
title: One-click layouts and the round trip
seo_title: Make a QGIS print layout from the map canvas in one click
description: >-
  Build a print layout from the QGIS map canvas in one click - title, map,
  legend, scale bar, north arrow - keep editing it in QGIS, and import a
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

Ten designs, modelled on the print layouts GIS users make most - Esri's print
service set and the recurring designs of its map gallery. Each preview is the
real page drawn with the map the canvas shows now, so what you pick is what
you get.

The picker shows **Page size** and **Orientation** at the top, then a strip
of the ten template names, then the designs themselves as a scrolling list of
large previews, one per row. Click a name in the strip to jump to its
preview. **Create layout** builds the one selected.

![The template picker: page size and orientation at the top, the strip of ten template names, and the first large previews of the current map - Classic and Legend below.](images/cartograph-templates.png)

- **Classic** - title, map, legend beside it (or below, whichever leaves the
  map larger), scale bar and north arrow. The layout described
  [below](#what-the-classic-layout-contains).
- **Legend below** - Classic with the legend always in a band under the map.
- **Map only** - just the map and its data credits, for placing in another
  document. The credits stay because most basemap and open-data licences
  require them.
- **Report figure** - the map with a caption, scale bar and north arrow under
  it, and no legend.
- **Field / survey map** - a bordered sheet with a labelled grid in the map's
  own units and a title block with the scale, CRS, author and date.
- **Poster** - a large title band, the map, and a side panel (below the map on
  a portrait page) for the story, the legend, scale bar and north arrow. Text
  and bands grow with the page size.
- **Map with locator** - the map with a locator inset that outlines where it
  is, and the legend under the inset.
- **Story map** - the map fills the page, with the title, a text panel and a
  locator inset laid over it.
- **Infographic** - a bold title band, the map and legend, and three tiles for
  key figures.
- **Dark full-bleed** - a dark page with the map to within a few millimetres of
  its edges, and light text over it.

The **page size** can be A4, A3, Letter, Tabloid, A5, A2, A1, A0 or Legal;
**Automatic** orientation takes whichever shows the map larger. The picker
remembers your last choice.

The panels' text comes from the project: the title from **Project Properties
→ General → Project title**, the story text from **Metadata → Abstract**.
Until there is an abstract, the text panels and the infographic tiles hold a
short placeholder to replace in the layout designer.

Every template except Classic and Legend below gives the map frame the shape
its design needs; the frame then shows a little more around the canvas view
rather than cutting any of it off. A locator inset is a static frame - a
picture of its map, as QGIS draws it.

## What the Classic layout contains

An **A4** page, in whichever orientation gives the map more room, with:

- **the map**, at the canvas's extent, CRS and rotation, showing the layers the
  canvas showed at that moment, in the same order;
- **a legend** beside or below the map, whichever leaves the map larger, with
  one row per layer;
- **a scale bar** under the map, bottom left;
- **a north arrow** above the map, top right;
- **a title** beside the north arrow, from the project title;
- **an attribution line** under the scale bar, from the layers' own credits,
  when they have any;
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
