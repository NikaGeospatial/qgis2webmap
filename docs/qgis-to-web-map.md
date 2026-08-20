---
title: QGIS to web map
seo_title: How to export QGIS to an interactive web map
description: >-
  How to turn a QGIS project into an interactive web map - the three routes people try, what each one costs, and the short version with QGIS2WebMap.
---

# QGIS to web map

You have a project in QGIS. Someone who does not have QGIS needs to look at it,
click things and zoom in. This page is the short answer to how that is done, and
what each of the usual routes actually costs you.

## The short version

Install [QGIS2WebMap](installation.md), open **Web → QGIS2WebMap by NIKA →
Create web map**, and press **Export**. You get one HTML file. Whoever you send
it to double-clicks it and gets your map — panning, zooming, layer switching,
popups — with no QGIS, no account and no internet connection.

The [first export walkthrough](first-export.md) does this properly, with
screenshots, in about ten minutes.

## What people usually try first, and what it costs

**A screenshot or a print layout.** Fast, and the right answer more often than
people admit — if the question is "what does this look like", a PNG answers it.
It stops being the right answer the moment somebody wants to zoom in, toggle a
layer, or read an attribute. Then you are re-exporting a new picture every time
they ask.

**Sending the project.** A `.qgz` plus its data, and the recipient installs QGIS.
This works and costs nothing, but it asks a non-GIS colleague to install a
desktop GIS to read one map, and it hands over your entire dataset when they only
needed to look at it. It also breaks the moment a layer path does not resolve on
their machine. There is a separate page on
[sharing a map without sending the project](share-qgis-map-without-qgis.md).

**Uploading to a web GIS.** A hosted platform will render your data well, and if
you already pay for one, use it. The costs are an account, a data upload, and
usually a per-viewer or per-map limit — which is a lot of process for "have a
look at this".

**Exporting to a web map.** A plugin reads the project and writes HTML,
JavaScript and data that a browser can draw. No account, no upload, no GIS on the
other end. This is what QGIS2WebMap does, and what
[qgis2web](qgis2web-comparison.md) does.

## What "interactive" gets you

An exported map is not a picture of your project. The recipient can:

- pan and zoom, at any scale you allowed
- switch layers on and off, in the order you set in the QGIS Layers panel
- click a feature and read its attributes in a popup, with the fields you chose
- tilt into 3D, if you enabled extrusion or the global relief ground surface

They cannot edit your data, and they do not get your project file.

## What does not come across

Not everything QGIS can draw has an equivalent in a browser, and the honest part
of this is knowing which parts before you send the map rather than after.

Rasters are not exported in the current release. Vector points, lines and
polygons are, along with layer order, groups, opacity and scale-dependent
visibility. Data in any CRS is reprojected to WGS84 on the way out.

Whatever cannot be translated exactly is listed on the plugin's **Fidelity** tab
*before* the export runs, with a verdict per layer — kept, changed, rasterised,
not exported or blocked. Nothing is dropped silently.
[What gets exported](supported-features.md) has the full list.

## Which output to choose

Three modes, and the choice is usually made by how the map is being delivered
rather than by how big it is:

| | Use it when |
|---|---|
| **Standalone HTML** | You are sending the map to a person. One file, opens off a disk, works offline |
| **Share ZIP** | The same, but their mail filter strips `.html` attachments — which is common |
| **Folder** | You are publishing to a web server |

[Sharing a map](sharing.md) covers the trade-offs. If you are publishing rather
than sending, [Host with OnlyMap](hosting.md) covers that path, including the
Content Security Policy trap that catches people putting a map on an existing
site.

## Where to go next

- [Install the plugin](installation.md) — from inside QGIS, about a minute
- [Your first export](first-export.md) — the full walkthrough
- [What gets exported](supported-features.md) — the fidelity limits in detail
- [QGIS2WebMap or qgis2web](qgis2web-comparison.md) — if you are choosing between them
- [The demos](demo-himalayas.md) — real exports, with the QGIS project downloadable beside each one
