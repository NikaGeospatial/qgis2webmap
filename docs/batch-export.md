---
title: Exporting without the dialog
seo_title: Batch export QGIS projects to web maps
description: >-
  Export web maps unattended - from the Processing toolbox's batch mode, the
  QGIS Python console, or headless with qgis_process - and the one-time
  runtime setup each of them needs first.
---

# Exporting without the dialog

The export is also a Processing algorithm, **QGIS2WebMap by NIKA → Web map →
Export to OnlyMap web map**, so it runs wherever Processing does: in the
toolbox's batch mode, from the QGIS Python console, and with `qgis_process` on
a machine with no QGIS window at all. It is the same export as the dialog's,
not a second one - the same reading of the project and the same file.

Print layouts have an algorithm of their own, described under
[Processing in the Cartograph dialog](cartograph-dialog.md#processing).

## Before the first run

**The runtime has to be on the computer already.** The algorithm cannot show
you the OnlyMap runtime licence - there is nobody to show it to during a batch
run - so on a computer that has never installed the runtime it stops at once
and says so, before reading anything. Export once from
**Web → QGIS2WebMap by NIKA**, signed in to the computer as the same user who
will run the batch: the dialog shows the licence, installs the runtime once,
and every run after that works offline. See [installation](installation.md).

On a server or in a container, where there is no dialog to open, install the
runtime by hand and set the `ONLYMAP_RUNTIME_DIR` environment variable to it,
as described in
[installing on a computer with no internet access](installation.md#on-a-computer-with-no-internet-access).
A runtime found that way needs no licence prompt and no download.

**A licence key is only needed for maps you publish on a website.** Leave it
out and the maps use the free plan, which draws everything in a file opened
locally. To export with a key, set the `ONLYMAP_LICENSE_KEY` environment
variable, or pass the key as the `LICENSE_KEY` parameter; with neither, the key
saved on this computer is used. See
[using a paid OnlyMap licence](supported-features.md#using-a-paid-onlymap-licence).

## The parameters

| Parameter | What it is |
|---|---|
| `MODE` | `0` Standalone HTML (the default), `1` Share ZIP, `2` Folder |
| `TITLE` | The map title; blank uses the map name saved in the dialog, then the project title |
| `LICENSE_KEY` | Optional; see above |
| `LEGEND`, `LAYER_SWITCHER`, `ZOOM_CONTROLS`, `SCALE_BAR`, `MEASURE` | The map controls: `1` shows one and `0` leaves it off; left out, the dialog's saved choice stands (all on in a project never opened in the dialog) |
| `EXPORT_BLOCKED` | Off by default, so a **Blocked** item stops the run and names it; on, the map is written without it and each one is a warning in the log |
| `OUTPUT` | Where to write the map: an `.html` file, a `.zip` file, or a folder |

## What a run exports

A run exports what the dialog would for the same project. The choices made in
**Web → QGIS2WebMap by NIKA** are saved in the project - which layers to
include, each layer's popup, labels and clustering, a web service layer's
Features, the map name, the basemap
and the rest of the Appearance tab - and the algorithm reads them from there. A
project never opened in the dialog exports every layer, hidden ones hidden,
with the dialog's defaults.

The dialog writes its choices into the project when it exports and when it
closes. `qgis_process` and the folder loop below read the project file, so save
the project after that.

A parameter you pass replaces the saved choice: `TITLE` the map name, and
`LEGEND`, `LAYER_SWITCHER`, `ZOOM_CONTROLS`, `SCALE_BAR` and `MEASURE` each its
control. Leave one out, as the commands below do, and the saved choice stands.
The toolbox's own window always sends all five, as its tick boxes show. `MODE`
and `OUTPUT` are always the run's own: the dialog's **Export as** and map file
are not used.

Two dialog choices follow the QGIS view, which a Processing run does not have.
A map set to **Open the map on** *the current QGIS view* opens on its data
instead, and the log says so. A map set to **Export only the features in this
view** is not exported at all: the run stops with a message rather than export
every feature, the ones outside the view included. Untick that box in the dialog
and save the project to export such a project in a batch.

The Fidelity report goes to the Processing log instead of a tab, one line per
item.

## From the Processing toolbox

In the **Processing Toolbox**, right-click **Export to OnlyMap web map** and
choose **Execute as Batch Process…**. Each row is one export of the project
that is open, so this is the way to write several versions of one map in one
go - a Standalone HTML and a Share ZIP, or one with a legend and one without.

To export several projects, use the console or `qgis_process` below.

## From the QGIS Python console

Export the open project (**Plugins → Python Console**):

```python
import processing

processing.run(
    "qgis2webmap:exportwebmap",
    {"MODE": 0, "TITLE": "", "OUTPUT": "/path/to/maps/my-map.html"},
)
```

Export every project in a folder, each to a map beside it. This opens each
project in turn in place of the one you have open, so save your work first:

```python
from pathlib import Path

import processing
from qgis.core import QgsProject

folder = Path("/path/to/projects")
for project_file in sorted(folder.glob("*.qgz")):
    QgsProject.instance().read(str(project_file))
    processing.run(
        "qgis2webmap:exportwebmap",
        {"MODE": 0, "OUTPUT": str(project_file.with_suffix(".html"))},
    )
```

`processing.run` stops with the algorithm's message if a project cannot be
exported - nothing in it to export, or a Blocked item with `EXPORT_BLOCKED`
off.

## Headless, with qgis_process

`qgis_process` comes with QGIS and runs Processing algorithms with no window.
It reads the project named by `--PROJECT_PATH`; the algorithm's parameters
follow the `--`:

```sh
qgis_process run qgis2webmap:exportwebmap --PROJECT_PATH=/data/towns.qgz -- MODE=0 TITLE="Towns" OUTPUT=/data/web/towns.html
```

It loads the plugin from your QGIS profile, so the plugin has to be installed
and enabled there. `qgis_process plugins` lists the plugins it can load, with a
star beside the enabled ones; if **nika_onlymap_exporter** has none, enable it:

```sh
qgis_process plugins enable nika_onlymap_exporter
```

To export every project in a folder:

```sh
for project in /data/*.qgz; do
  qgis_process run qgis2webmap:exportwebmap --PROJECT_PATH="$project" -- MODE=0 OUTPUT="${project%.qgz}.html"
done
```

Where to find `qgis_process`: on Linux it is on the `PATH`. On Windows it is
`qgis_process-qgis.bat` (`qgis_process-qgis-ltr.bat` for a long-term release)
in the `bin` folder of the QGIS installation or of OSGeo4W. On macOS it is
inside the application, at
`/Applications/QGIS.app/Contents/MacOS/bin/qgis_process` (`QGIS-LTR.app` for a
long-term release). It exits with a
non-zero status when an export fails, so a script can stop or carry on.

## Next

- [The dialog, tab by tab](the-dialog.md) - the same choices, interactively
- [What gets exported](supported-features.md)
- [Troubleshooting](troubleshooting.md#the-processing-algorithm-stops-immediately-on-a-new-machine)
