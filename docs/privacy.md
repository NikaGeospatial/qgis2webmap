# Privacy

**Exported maps contain no tracking and make no network requests.**

Opening an exported map sends nothing anywhere. It works with the network cable
unplugged, on a machine that has never had QGIS installed.

## What that means concretely

- No analytics, no beacons, no telemetry.
- No basemap tiles fetched from a server.
- No fonts, scripts or styles loaded from a CDN - everything is inside the file.
- No identifier of any kind for the map, its author, or whoever opens it.

You can check this yourself: open an exported map in a browser, open the
developer tools Network tab, and reload. It stays empty.

## What is in the file

The map's data, taken from your QGIS project. If a layer came from a database or
a service that needed a username or password, **the credentials are not written
into the map** - only the features that were read.

Be aware that the data itself is in the file, in full. If a layer contains
information that should not be shared, do not include that layer in the export.
The Fidelity tab lists exactly which layers were included.

## If you publish or host a map

Publishing to NIKA hosting, and asking an AI assistant to modify a map, are
separate actions that you start yourself. Nothing is uploaded automatically.

## Source

The plugin is GPL-2.0-or-later. You can read exactly what it does at
[github.com/NikaGeospatial/qgis2webmap](https://github.com/NikaGeospatial/qgis2webmap).
