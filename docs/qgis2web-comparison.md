---
title: QGIS2WebMap or qgis2web
description: >-
  qgis2web is the established way to publish a QGIS project to the web, and for many projects it is still the right answer. Here is where the two differ, and when to pick which.
---

# QGIS2WebMap or qgis2web

If you have looked for a way to put a QGIS project on the web, you have met
[qgis2web](https://plugins.qgis.org/plugins/qgis2web/). It has been doing this
job since 2015, it is free, and it is still actively maintained — version 4.3.0
landed in July 2026. It is a good plugin. This page is here because you deserve
a straight answer about which one fits your project, not a sales pitch.

**The short version.** qgis2web produces a folder of files that a web server
serves. We produce one file you can email. qgis2web can carry rasters and live
WMS layers we cannot. We draw your point symbols the way QGIS drew them, and
tell you in advance what will not survive the trip.

## The one difference that decides most projects

qgis2web exports **a folder** — an `index.html`, and beside it separate folders
of JavaScript, CSS, and your data split into one file per layer. That is the
right shape if you are publishing to a website: it is what a web server expects,
and the browser can cache the library across several maps.

It is the wrong shape if you want to send a map to somebody. You cannot email a
folder and expect it to work; you have to zip it, and they have to unzip it, and
they have to keep it together afterwards.

Our default output is a **single HTML file**. Attach it, double-click, done —
offline, with no server and no unzipping. If you want the folder shape for a
website, we produce that too. Pick whichever the situation needs.

## Where we are genuinely better

**Your point symbols survive.** qgis2web draws simple marker shapes as plain
circles in both of its renderers — squares, stars, triangles, and SVG markers
all arrive as dots
([qgis2web#1218](https://github.com/qgis2web/qgis2web/issues/1218), open since
June 2026). We render the actual symbol QGIS drew, including SVG markers and
stacked symbol layers, and bake it into the export as an image. See
[what gets exported](supported-features.md).

**You find out what breaks before you send it.** Every layer, symbol and label
gets a verdict — kept, changed, rasterised, not exported, or blocked — on the
Fidelity tab, before you export. qgis2web has no equivalent report; its own
documentation is honest that "it is hardly possible to create a 1:1 copy of all
desktop features", but you discover which features those were by looking at the
result.

**Popup settings are per-layer.** Hover-versus-click, highlight colour and
coordinate precision can differ per layer, with a map-wide default. In qgis2web
these are one global setting for the whole map — a per-layer version has been
requested since 2015
([qgis2web#131](https://github.com/qgis2web/qgis2web/issues/131), still open).

**3D extrusion is real.** qgis2web's documentation lists 2.5D symbology among
the things it does not translate correctly. We read both places QGIS hides
extrusion height and produce genuine extruded geometry with lighting.

## Where qgis2web is better

This is not a formality. If any of these is on your list, use qgis2web.

- **Rasters.** We do not support raster layers at all in 0.1.x. qgis2web does.
- **Live WMS and WFS layers.** qgis2web can keep a layer remote and queryable.
  Everything in our export is baked in as static data.
- **Tools in the map.** Address search, GPS location, and measurement tools are
  built into qgis2web's output. We do not have them — our exported map is a
  viewer, not a small GIS application.
- **Nothing proprietary, ever.** qgis2web is GPL-2.0 top to bottom with no
  runtime to fetch, no account, and no paid tier at any scale. Our plugin is
  GPL-2.0, but exported maps run on OnlyMap, a separate commercial runtime. It
  is free and uncapped for maps opened locally; **hosting** a map on a public
  domain past the free tier's limits needs a paid key. If depending on a vendor
  is a problem for you, that is a real reason to choose qgis2web, and we would
  rather you knew now.
- **Very large datasets.** qgis2web 4.3 exports PBF vector tiles, which sidesteps
  browser feature limits entirely — at the cost of needing a tile server.
- **Ten years of answers.** When something goes wrong at 5pm, qgis2web has a
  decade of blog posts, forum threads and tutorials behind it. We are on 0.1.x.

## Side by side

| | qgis2web | QGIS2WebMap |
|---|---|---|
| What you get | Folder of files | One HTML file (or a ZIP, or a folder) |
| Send it to someone | Zip the folder first | Attach the file |
| Cost | Free, always | Free locally; paid key to host past the free tier |
| Rasters | Yes | No, not in 0.1.x |
| Live WMS / WFS | Yes | No |
| Search, GPS, measure | Yes | No |
| Point marker shapes | Circles | The symbol QGIS drew |
| Popup settings | One setting per map | Per layer |
| Report before export | None | Fidelity tab |
| 3D extrusion | Not translated | Yes |
| Maturity | Since 2015 | Since 2026 |

## So which one

Use **qgis2web** if you need rasters or live services, want tools inside the map,
or want a stack with no commercial component in it anywhere.

Use **QGIS2WebMap** if you need to hand someone a map that opens, your symbology
has to look like your symbology, and you want to know what changed before you
send it.

Plenty of people will have projects that call for both, and that is fine. They
install side by side.

---

*Written 2026-08-07 against qgis2web 4.3.0. qgis2web is an independent project
and is not affiliated with NIKA; the issues linked above were open on the date
of writing. If something here has become wrong,
[tell us](mailto:support@nikaplanet.com) and we will correct it.*
