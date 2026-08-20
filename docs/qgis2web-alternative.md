---
title: Alternatives for QGIS web export
seo_title: A qgis2web alternative for QGIS web maps
description: >-
  If you are looking for an alternative to qgis2web, here are the routes that exist for getting a QGIS project into a browser, and where each one fits.
---

# Alternatives for QGIS web export

Most people arrive at this question the same way: qgis2web is the answer
everybody gives, you have tried it, and something about the result did not fit —
usually the folder of files, or a symbol that came out as a plain circle. This
page lays out what the actual alternatives are, so you can pick on the shape of
the output rather than on which plugin came up first.

For a direct feature-by-feature comparison with qgis2web specifically, see
[QGIS2WebMap or qgis2web](qgis2web-comparison.md). This page is the wider view.

## The routes that exist

**Export plugins.** A plugin reads the saved project and writes a web map:
[qgis2web](https://plugins.qgis.org/plugins/qgis2web/), which is the established
one and produces Leaflet, OpenLayers or Mapbox GL output, and
[QGIS2WebMap](installation.md), which produces a single HTML file drawn by
OnlyMap. No account, no upload, no server-side software in either case. This is
the cheapest route by a distance, and for most people it is the whole answer.

**A hosted web GIS.** ArcGIS Online, Felt, CARTO, GIS Cloud and similar. You
upload your data and the platform renders and serves it. Strong if you need
editing, permissions, live data or dashboards, and if you already pay for one it
is usually the right call. The costs are an account, a data upload to somebody
else's servers, and a per-seat or per-map limit.

**Writing it yourself.** Export GeoJSON, write Leaflet, MapLibre or deck.gl by
hand. Total control, and the only route where you can build something genuinely
custom. It is also a development project, and it does not track your QGIS
styling — the moment the project changes in QGIS, you are hand-editing again.

**A map server.** GeoServer or QGIS Server in front of your data, with a web
client on top. This is the right architecture for an organisation publishing many
maps from a live database. It is a great deal of machinery for one map.

## Where QGIS2WebMap fits

It is an export plugin, and it is built around one specific case: **you need to
hand a map to a person, and the map has to look like your map.**

- **One file.** The default output is a single HTML document. Attach it to an
  email, put it on a USB stick, drop it in a shared folder. It opens off a disk,
  offline, with no unzipping and no web server. Folder and ZIP outputs exist too
  when you need those shapes.
- **Your point symbols survive.** The actual QGIS symbol is rendered and baked
  into the export as an image, including SVG markers and stacked symbol layers.
- **You are told what changed before you send it.** Every layer, symbol and label
  gets a verdict on the **Fidelity** tab — kept, changed, rasterised, not
  exported, or blocked — while you can still act on it.
- **Per-layer popup behaviour.** Hover-versus-click, highlight colour and
  coordinate precision are set per layer, with a map-wide default.
- **Real 3D.** Genuine extruded geometry with lighting, plus an optional global
  relief ground surface.

It is free to install and free to use locally. A paid OnlyMap key is only
involved if you publish a map to a real domain and exceed the free hosting tier —
see [what gets exported](supported-features.md#size-limits-on-the-free-plan) for
exactly where those limits apply, because they do **not** apply to a file
somebody opens from their own disk.

## What it is not

It is not a hosted platform, not a data editor, and not a live-data pipeline. It
reads a saved project and writes a map. If you need viewers to edit features, or
the map to reflect a database that changes hourly, one of the other routes above
is the honest answer.

Rasters are not exported in the current release.

## Trying it

QGIS2WebMap and qgis2web install side by side and do not interfere with each
other, so there is nothing to undo. Install from inside QGIS, open a project you
have already published, and export it — the Fidelity tab will tell you what would
be different before you commit to anything.

- [Install the plugin](installation.md)
- [Your first export](first-export.md)
- [QGIS2WebMap or qgis2web, side by side](qgis2web-comparison.md)
- [The demos](demo-himalayas.md) — real exports with the QGIS project downloadable beside each

---

*qgis2web is an independent project and is not affiliated with NIKA. The
comparison points above are described in more detail, with sources and dates, on
the [comparison page](qgis2web-comparison.md).*
