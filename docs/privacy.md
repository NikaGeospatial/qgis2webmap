---
title: Privacy
seo_title: What an exported QGIS web map sends
description: >-
  Exported maps send one anonymous usage report when they load, and nothing else. What is sent, what is in the file, and how to verify it.
---

# Privacy

**Exported maps send one anonymous usage report when they load, and nothing else.**

Opening an exported map sends that one report and nothing more. Aside from
that, it works with the network cable unplugged, on a machine that has never
had QGIS installed.

## What that means concretely

- One anonymous usage report each time the map finishes loading. See below
  for exactly what is in it.
- No tiles or pictures fetched from a server - unless you choose a basemap or
  relief, or the map has a live web layer or a popup picture from a web
  address; see below.
- No fonts, scripts or styles loaded from a CDN - everything is inside the
  file, unless you switch relief on; see below.
- No identifier of any kind for whoever opens the map.

You can check this yourself: open an exported map in a browser, open the
developer tools Network tab, and reload. For a map with none of the web
content described below, you will see exactly one request, to NIKA's
telemetry service.

## The one report: usage telemetry

Every exported map sends a small, anonymous usage report each time it
finishes loading. This comes from the OnlyMap runtime the map is built on -
it is not something this plugin adds - and it is described in the runtime's
licence.

The report can include:

- the OnlyMap runtime version
- counts of which features and layers the map uses
- which widgets are on the map, such as the legend or scale bar
- the hostname of the page the map is running from
- a map identifier, only if you set one
- a sanitised description of an error, if the map hit one

**Read the hostname point twice if you host on a private server.** The
hostname does not identify your data or who is viewing the map, but if you
host it at an internal address such as `maps.yourcompany.internal`, that
hostname is what gets sent - and that can reveal which organisation is using
the map.

The report never includes who opened the map, what page or URL they were on,
cookies, the map's data, or anything else about its contents. IP addresses
are not stored.

There is no setting in the plugin to turn this off today. It can be turned
off by hand, by editing `telemetry="off"` into the exported HTML file's
`<om-map>` tag after export - see the licence for the technical detail.

## Basemaps and relief

There is no basemap by default, and a default export makes no basemap
requests at all.

If you choose one on the Map tab, that changes for everyone you send the map to:
their browser fetches tiles from the provider each time they open it. The
provider necessarily sees those requests, including the approximate area being
looked at.

Nothing else about the export changes because of a basemap choice - no
additional identifier travels with it, and the file is no larger. But the
"nothing else is fetched" guarantee only holds with the basemap set to None,
so the dialog warns in red when it is not, and the Fidelity tab names the
provider. With *Let readers switch basemap* ticked, a reader can switch to any
of the other basemaps, so the Fidelity tab names every provider the switcher
can reach.

Relief, on the Map tab, works the same way: the reader's browser fetches
elevation tiles, the imagery draped over them, and the scripts that decode the
terrain (from unpkg.com) while the map is used. The Fidelity tab names each
host.

## Live web layers and their legends

A layer QGIS itself reads from the web - an XYZ, WMS or WMTS layer, a vector
tile layer, or a Cloud Optimized GeoTIFF or PMTiles archive at an `https://`
address - is exported live: the reader's browser asks that layer's own server
for what it draws, so that server sees the requests and the area being looked
at. A vector tile layer drawn in its publisher's style also loads that style
from the publisher when the map opens. A PMTiles archive on your own computer
is carried with the map, and reading it makes no request elsewhere. A WMS or WMTS layer's legend picture is
loaded from the service the same way when the map opens. An address that
carries an access key is never written into the map: such a layer is refused,
and such a legend picture left out.

A vector layer from a WFS, OGC API Features or ArcGIS FeatureServer is live
too, unless it stays embedded (see
[supported features](supported-features.md)): the reader's browser asks that
service for the features in the area being looked at, and again when the map
moves somewhere else, so the service's host sees those requests and the area.
The Fidelity tab names the host for each such layer, under *Live features*,
and says whether the copy embedded at export is there to fall back on. Set the
layer's **Features** to *embedded* on the Layers tab and the map sends nothing
for it. SensorThings layers are never live: their features are read once at
export and carried in the file.

A WMS layer asked in a coordinate system other than Web Mercator, or a WMTS
layer on a national tile grid, needs that coordinate system's definition to
place its pictures. The map carries the UTM zones and the common national
grids itself; for any other, the reader's browser fetches the definition
once from `https://epsg.io/<code>.proj4` when the map opens, so epsg.io sees
that request, and which coordinate system it is for. The Fidelity tab says
which layers do this. Exporting itself still contacts no service: the tile
grid comes from the capabilities QGIS stored when it loaded the layer.

## Popup pictures that are web addresses

A picture a popup shows from a web address (an Attachment field set to show
an Image, holding an `https://` address - see
[Popups](supported-features.md#links-and-pictures)) is not carried in the
file. The reader's browser fetches it from that address when the popup opens,
so its server sees the request. No page address is sent with it. Pictures
stored on your computer are carried inside the map and fetch nothing, and a
link in a popup fetches nothing until someone follows it. Only pictures in
the project's folder, or under the Attachment widget's default path, are
carried: a value pointing anywhere else on your computer - an absolute path,
`..` or a link out of the folder - shows as its path, and the file is not
read.

## Cartographs

A [cartograph](qgis-print-layout-to-html.md) follows the same policy, and
sends even less:

- **A cartograph whose frames are all Image frames sends nothing at all.** It does
  not carry the map runtime, so there is nothing in it to report. Open it with
  the developer tools' Network tab showing and reload: no request.
- **A cartograph with an interactive frame** sends the same one anonymous usage report
  as an exported map, from the map runtime inside that frame, with
  `cartograph` among the features it counts. Same service, nothing new sent.
- On the free plan, every cartograph carries a small credit at the foot of the
  page - *Built with OnlyMap by NIKA · free for non-commercial use*. It is part
  of the free licence's attribution condition, so it prints and appears in an
  exported PNG too. It is text on the page, not a request.

Opening a cartograph from the plugin's **Preview** or **Open in browser** serves
it from a small server on your own machine (`127.0.0.1`), which nothing outside
your computer can reach.

What a cartograph **contains** differs by frame. An image frame holds an image
of the map. An interactive frame holds the features it draws, with the attributes its
popups show - the popup settings in the Layers list decide which -
see [what travels in the file](share-qgis-print-layout-without-qgis.md#what-travels-in-the-file).

The [hand-off to NIKA](enhance-with-ai.md#hand-off-to-nika) sends nothing
either: it finds NIKA desktop by looking on your disk, and the prompt goes onto
your clipboard or into a file you choose.

## What is in the file

The map's data, taken from your QGIS project. If a layer came from a database or
a service that needed a username or password, **the credentials are not written
into the map** - only the features that were read.

Be aware that the data itself is in the file, in full. If a layer contains
information that should not be shared, do not include that layer in the export.
The Fidelity tab lists exactly which layers were included.

## What the plugin downloads

The plugin itself downloads one thing on its own: the first time you build a
map, it downloads the OnlyMap runtime from npm — about 5.9 MB, once per
computer, after showing you the licence and asking. See
[installation](installation.md). The only other download is one you ask for:
a language pack, fetched from jsDelivr when you pick a language other than
English under Settings.

Neither request sends anything about you or your data. Each is an anonymous
download of a public file: no account, no token, no identifier, and nothing
about your project, your layers or your machine. npm, or jsDelivr, can see
that some computer at your IP address downloaded a public file, which is what
any software download looks like.

Beyond those, the plugin never contacts anything, with two exceptions of
QGIS's own making. Exporting is entirely local — your data is read from disk and
written into the file, and no part of it leaves your computer.

The first: a raster QGIS itself reads over the web (a COG at an `https://`
address). To check the web map can draw it, the export reads the file's header,
which GDAL usually still holds from when QGIS opened the layer; if it has let
it go, the export asks that layer's own server for the header again, as QGIS
did. It sends nothing of yours, and never downloads the raster itself.

The second: a vector layer QGIS reads from a web service (WFS, OGC API
Features, SensorThings, an ArcGIS feature service): the export reads its
features through QGIS, as QGIS reads them to draw the layer, so that service
may be asked for them again. Nothing of yours is sent to it. (A map in which
such a layer loads live then asks the service itself, from its reader's
browser - see [live web layers](#live-web-layers-and-their-legends).)

## If you publish or host a map

Publishing to NIKA hosting, and asking an AI assistant to modify a map, are
separate actions that you start yourself. Nothing is uploaded automatically.

## This website

Everything above is about the plugin and the maps it exports. This
documentation site is a separate thing, and it does count its visitors.

It uses PostHog to record which page was read, which site or link you arrived
from, and roughly how long the page was open, along with your country and
browser. Nothing is stored on your device - no cookies, no local storage - so
there is no consent banner to dismiss, and no way for the site to recognise you
on a later visit. You are never identified, and none of this touches the plugin,
your QGIS projects, or any map you export.

## Source

The plugin is GPL-2.0-or-later. You can read exactly what it does at
[github.com/NikaGeospatial/qgis2webmap](https://github.com/NikaGeospatial/qgis2webmap).
