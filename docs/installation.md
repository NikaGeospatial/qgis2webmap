# Installation

Requires **QGIS 3.22 or newer**. QGIS 4 is supported.

## From the QGIS Plugin Repository

Not yet published. Until then, install from a zip.

## From a zip

1. Download the latest `nika_onlymap_exporter-*.zip` from the
   [releases page](https://github.com/NikaGeospatial/qgis2webmap/releases).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose the file and press **Install Plugin**.

A **Web → QGIS2WebMap by NIKA** menu entry and a toolbar button appear.

## The first export downloads the map runtime

The first time you preview or export a map, the plugin asks to download the
**OnlyMap runtime** — about 4.5 MB, once per computer.

The runtime is the code that draws the map in a browser. It is built into every
map you export, which is exactly what lets someone open your map with no
internet connection and nothing installed. It is a separate commercial product
by NIKA with its own licence, which the plugin shows you before downloading
anything.

After that one download, **everything works offline**, on every project,
forever. Exporting itself never touches the network.

The plugin is free and open source; the runtime is not, which is why it is not
included in the plugin and why you are asked before it is fetched.

### On a computer with no internet access

Some organisations block outside downloads. To install the runtime by hand:

1. On a machine that does have access, download the package from
   [npm](https://www.npmjs.com/package/@nika-js/onlymap) and unpack it.
2. Copy the `dist` folder — you need `onlymap.standalone.js` and
   `onlymapjs.css` — onto the offline machine.
3. Set the environment variable `ONLYMAP_RUNTIME_DIR` to that folder before
   starting QGIS.

The plugin uses a runtime it finds that way and never asks to download.

### Behind a proxy

The download goes through QGIS's own network settings, so a proxy configured in
**Settings → Options → Network** is used automatically. If the download fails,
check there first.

## Building it yourself

```bash
git clone https://github.com/NikaGeospatial/qgis2webmap.git
cd qgis2webmap
python scripts/package_plugin.py
```

The zip lands in `dist/`.

## If it does not appear

- Check **Plugins → Manage and Install Plugins → Installed** and confirm it is
  ticked.
- Look at **View → Panels → Log Messages**, tab `QGIS2WebMap`, for an error.

The plugin never closes QGIS. The only thing it ever installs is the OnlyMap
runtime described above, once, after asking. If QGIS closes unexpectedly, that
is a bug worth
[reporting](https://github.com/NikaGeospatial/qgis2webmap/issues).
