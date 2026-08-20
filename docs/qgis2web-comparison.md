---
title: QGIS2WebMap or qgis2web
seo_title: QGIS2WebMap vs qgis2web
description: >-
  What QGIS2WebMap does that the established route does not - one file you can email, point symbols that survive, and a report of what changed before you send it.
---

# QGIS2WebMap or qgis2web

If you have looked for a way to put a QGIS project on the web, you have met
[qgis2web](https://plugins.qgis.org/plugins/qgis2web/) — the established route,
and a capable one. This page is about what QGIS2WebMap does differently, so you
can tell quickly whether those differences matter to your project.

**The short version.** qgis2web produces a folder of files that a web server
serves. We produce one file you can email, we draw your point symbols the way
QGIS drew them, and we tell you in advance what will not survive the trip.

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

## Why use QGIS2WebMap?

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

## Side by side

| | qgis2web | QGIS2WebMap |
|---|---|---|
| What you get | Folder of files | One HTML file (or a ZIP, or a folder) |
| Send it to someone | Zip the folder first | Attach the file |
| Cost | Free | Free locally; paid key to host past the free tier |
| Point marker shapes | Circles | The symbol QGIS drew |
| Popup settings | One setting per map | Per layer |
| Report before export | None | Fidelity tab |
| 3D extrusion | Not translated | Yes |

## So which one

Use **QGIS2WebMap** if you need to hand someone a map that opens, your symbology
has to look like your symbology, and you want to know what changed before you
send it.

The two install side by side, so there is nothing to undo if you want to try
this one on a project you have already published.

---

*Written 2026-08-07 against qgis2web 4.3.0. qgis2web is an independent project
and is not affiliated with NIKA; the issues linked above were open on the date
of writing. If something here has become wrong,
[tell us](mailto:support@nikaplanet.com) and we will correct it.*
