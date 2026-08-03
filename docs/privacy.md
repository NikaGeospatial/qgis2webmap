# Privacy

**Exported maps contain no tracking and make no network requests.**

Opening an exported map sends nothing anywhere. It works with the network cable
unplugged, on a machine that has never had QGIS installed.

## What that means concretely

- No analytics, no beacons, no telemetry.
- No basemap tiles fetched from a server - unless you choose a basemap; see below.
- No fonts, scripts or styles loaded from a CDN - everything is inside the file.
- No identifier of any kind for the map, its author, or whoever opens it.

You can check this yourself: open an exported map in a browser, open the
developer tools Network tab, and reload. It stays empty.

## The one exception: basemaps

There is no basemap by default, and a default export makes no requests at all.

If you choose one on the Map tab, that changes for everyone you send the map to:
their browser fetches tiles from the provider each time they open it. The
provider necessarily sees those requests, including the approximate area being
looked at.

Nothing else about the export changes - no analytics are added, no identifier
travels with it, and the file is no larger. But the "opens offline, contacts
nobody" guarantee only holds with the basemap set to None, so the dialog warns
in red when it is not, and the Fidelity tab names the provider.

## What is in the file

The map's data, taken from your QGIS project. If a layer came from a database or
a service that needed a username or password, **the credentials are not written
into the map** - only the features that were read.

Be aware that the data itself is in the file, in full. If a layer contains
information that should not be shared, do not include that layer in the export.
The Fidelity tab lists exactly which layers were included.

## The one thing the plugin downloads

The claims above are about **exported maps**, and they hold absolutely.

The plugin itself makes exactly one network request in its entire life: the
first time you build a map, it downloads the OnlyMap runtime from npm — about
3 MB, once per computer, after showing you the licence and asking. See
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
