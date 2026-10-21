---
title: QGIS print layout to HTML
seo_title: Turn a QGIS print layout into a printable web page
description: >-
  Export a QGIS print layout as one HTML file that opens by double-click,
  prints at true page size, and keeps its maps live or pixel-exact. No QGIS
  needed to open it.
lead_image:
  src: /images/cartograph-dialog.png
  alt: The export dialog on Cartograph (Beta) - the print layout drawn on the left, and the panel with the layout chosen, its settings, and the file name and save status at its foot.
---

# QGIS print layout to HTML

A QGIS print layout normally ends as a PDF or a PNG. Both are dead ends: the
person you send it to can look at it, and that is all. To change a title, move
the extent or reprint at another size, somebody has to go back to QGIS.

QGIS2WebMap's **Cartograph (Beta)** output turns the layout into a
**cartograph** instead: one HTML file holding the page, its maps and its
furniture - legend, scale bar, north arrow, grid, text, pictures. The person
you send it to double-clicks it and gets the page in their browser. They can
print it at its true size, and maps you leave **live** still pan, zoom and
answer clicks inside the printed page.

## What you get

- **One file.** Everything the page needs is inside it: the layout, map
  images, map data and the code that draws it. Nothing to unzip, nothing to
  install, and no internet connection needed to open it - unless a live map
  draws an online tile layer, which is fetched as it is viewed.
- **True page size in print.** An A3 layout prints as A3 from Chrome, Edge or
  any other Chromium-based browser - see
  [printing a cartograph](printing-a-cartograph.md) for Firefox and Safari.
- **Live or static maps, your choice per map.** A **live** frame is an
  interactive map inside the page; a **static** frame is a pixel-exact image of
  the map as QGIS drew it, in the layout's own CRS. See
  [live or static frames](live-or-static-frames.md).
- **An honest report.** Anything that does not come across exactly is listed
  on the Checks tab before you send the file - see
  [what a cartograph keeps](cartograph-supported-features.md).
- **A round trip.** A cartograph can be imported back into QGIS as a print
  layout, and a cartograph can be made from the map canvas in one click - see
  [one-click layouts](one-click-layout.md).

## How to make one

1. Build the print layout in QGIS as usual, or skip that and let the plugin
   build one from the map canvas.
2. Open **Web → QGIS2WebMap by NIKA → Create cartograph...** - or, inside the
   layout designer, **Layout → Export as cartograph (HTML)...**.
3. Check the file name under **Cartograph file**. The cartograph saves itself
   there after every change; there is no Export button.

[Your first cartograph](cartograph-first-export.md) walks through it with
screenshots.

## Why it is a Beta

The cartograph output is new, and the [list of what it keeps](cartograph-supported-features.md)
is narrower than everything a print layout can hold: one page per layout (an
atlas aside), no HTML frames or attribute tables, and some label styling is
simplified. Nothing is dropped silently - the Checks tab names every change -
but read it before you send a cartograph to a client.

Report anything odd on [our Discord](https://discord.gg/RujwMpednf).

## How it compares

| | PDF from QGIS | Cartograph |
|---|---|---|
| Opens with | A PDF reader | A web browser |
| Maps | Fixed pictures | Live (pan, zoom, click) or pixel-exact pictures, per map |
| Prints at true size | Yes | Yes, from a Chromium-based browser |
| Text stays text | Yes | Yes |
| Re-open in QGIS | No | Yes - import it back as a print layout |
| CMYK for a print shop | Yes, on QGIS built with Qt 6.8 or newer | Through QGIS, from the same dialog - see [printing](printing-a-cartograph.md) |
| Atlas | One PDF, or one file per page | One file, one page per feature, turned in the browser - see [atlas](cartograph-atlas.md) |

If what you need is an interactive map rather than a printed page, the plugin's
other output, the [web map](first-export.md), is the better fit.
