---
title: One-click layouts and the round trip
seo_title: Make a QGIS print layout from the map canvas in one click
description: >-
  Build a print layout from the QGIS map canvas in one click - title, map,
  legend, scale bar, north arrow - keep editing it in QGIS, and import a
  cartograph back as a layout.
lead_image:
  src: /images/cartograph-one-click.png
  alt: The Cartograph tab after one click on Current canvas - the new layout "carto: Parcel survey" is selected, and the status bar says it was created from the canvas.
---

# One-click layouts and the round trip

You do not need a print layout to make a cartograph. One click turns the map
canvas into one - and, unlike tools that go straight to a PDF, it leaves a
real QGIS print layout behind that you can keep editing. A cartograph can also
go the other way, back into QGIS as a layout.

## From the canvas

1. Set the canvas to the view you want on the page: the extent, the layers,
   even a rotation.
2. On the **Cartograph** tab, click **Current canvas (creates a print
   layout)**. On the web map, **Share → Make a cartograph...** does the same and
   switches the dialog to Cartograph (Beta).

The plugin builds a print layout called **`carto: <project title>`**
(`carto: map` for a project with no title, and `carto: map (2)` if the name
is taken), adds it to the project, and selects it as the cartograph's source.

![The Cartograph tab after one click: the Source list shows "carto: Parcel survey", and the status bar reports the new layout.](images/cartograph-one-click.png)

## What the layout contains

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
[rule](live-or-static-frames.md#the-default): live in EPSG:3857 and
EPSG:4326, static otherwise.

## Keep editing it in QGIS

The new layout is an ordinary print layout. Open it from **Project → Layouts**
and change anything - move the legend, restyle the title, add a logo. The
cartograph follows: each change in the layout designer saves the cartograph
again about half a second later.

**The first save** happens as soon as the new layout is selected: in a saved
project its cartograph is `carto-<project title>.html`, beside the project
file. In a project that has never been saved, nothing is written until you
choose where on the Cartograph tab.

## Importing a cartograph back into QGIS

**Import cartograph...** on the Cartograph tab reads a cartograph HTML file -
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

The new layout is selected as the source, so the cartograph you see is now
made from it.

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
