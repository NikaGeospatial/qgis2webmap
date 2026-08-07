---
title: Privacy
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
- No basemap tiles fetched from a server - unless you choose a basemap; see
  below.
- No fonts, scripts or styles loaded from a CDN - everything is inside the
  file.
- No identifier of any kind for whoever opens the map.

You can check this yourself: open an exported map in a browser, open the
developer tools Network tab, and reload. You will see exactly one request,
to NIKA's telemetry service.

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

## The one exception: basemaps

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
provider.

## What is in the file

The map's data, taken from your QGIS project. If a layer came from a database or
a service that needed a username or password, **the credentials are not written
into the map** - only the features that were read.

Be aware that the data itself is in the file, in full. If a layer contains
information that should not be shared, do not include that layer in the export.
The Fidelity tab lists exactly which layers were included.

## The one thing the plugin downloads

The plugin itself makes exactly one network request in its entire life: the
first time you build a map, it downloads the OnlyMap runtime from npm — about
4.5 MB, once per computer, after showing you the licence and asking. See
[installation](installation.md).

That request sends nothing about you or your data. It is an anonymous download
of a public package: no account, no token, no identifier, and nothing about
your project, your layers or your machine. npm can see that some computer at
your IP address downloaded a public file, which is what any software download
looks like.

After that, the plugin never contacts anything again. Exporting is entirely
local — your data is read from disk and written into the file, and no part of
it leaves your computer.

## If you publish or host a map

Publishing to NIKA hosting, and asking an AI assistant to modify a map, are
separate actions that you start yourself. Nothing is uploaded automatically.

## Source

The plugin is GPL-2.0-or-later. You can read exactly what it does at
[github.com/NikaGeospatial/qgis2webmap](https://github.com/NikaGeospatial/qgis2webmap).
