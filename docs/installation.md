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

The plugin never closes QGIS and never installs anything on your system. If QGIS
closes unexpectedly, that is a bug worth
[reporting](https://github.com/NikaGeospatial/qgis2webmap/issues).
