---
title: Your first export
seo_title: Export a QGIS project to a web map
description: >-
  A walkthrough from a styled QGIS project to a web map you can email, including live preview and the fidelity check.

# Opt-in HowTo markup, read by `_layouts/default.html`. It has to live here
# rather than in the body because the same file is rendered by the plugin's Help
# tab with HTML disabled - see the note in that layout. Keep the wording matching
# the sections below; markup that describes something the reader cannot see is a
# defect, not a bonus.
howto:
  name: Export a QGIS project to an interactive web map
  total_time: PT10M
  steps:
    - name: Build the map in QGIS
      anchor: 1-build-the-map-in-qgis
      text: >-
        Compose the project first - load your layers, style them, and set the
        canvas to the view you want as the map's starting point. Layer order is
        taken from the QGIS Layers panel.
    - name: Open the plugin
      anchor: 2-open-the-plugin
      text: >-
        Choose Web, then QGIS2WebMap by NIKA, then Create web map - or use the
        toolbar button. The dialog is non-modal, so QGIS stays usable behind it.
    - name: Name the map
      anchor: 3-name-the-map
      text: >-
        The Map name field at the top of the Map tab is the title shown on the
        exported map. Leave it blank to use the project title.
    - name: Choose the layers
      anchor: 4-choose-the-layers
      text: >-
        On the Layers tab, tick the layers to include and set how each one
        behaves in the exported map.
    - name: Preview
      anchor: 5-preview
      text: >-
        Press Preview to build the map and open it in your browser. With Live
        preview ticked the tab updates by itself as you change settings.
    - name: Export
      anchor: 6-export
      text: >-
        Pick an output mode on the Map tab - Standalone HTML, Share ZIP or
        Folder - then press Export. Open exported map opens the real file.
    - name: Check the Fidelity tab
      anchor: 7-check-the-fidelity-tab
      text: >-
        Before sending the map on, read the Fidelity tab. It lists everything
        that changed on the way out of QGIS. Nothing is dropped silently.

lead_image:
  src: /images/export-05-live-preview.png
  alt: The export dialog's button row - Live preview ticked, with Preview and Export beside it.
---

# Your first export

## 1. Build the map in QGIS

The plugin exports the project as it is, so compose it first: load your layers,
style them, and set the canvas to the view you want as the map's starting point.

Layer order matters and is taken from the QGIS Layers panel - the topmost layer
draws on top, exactly as on the QGIS canvas.

## 2. Open the plugin

**Web → QGIS2WebMap by NIKA → Create web map**, or the toolbar button.

The dialog is non-modal: QGIS stays usable behind it. Reorder or rename layers in
the Layers panel and the plugin's list follows immediately - there is nothing to
refresh.

## 3. Name the map

The **Map name** field at the top of the Map tab is the title shown on the
exported map. Leave it blank to use the project title.

![The Map tab with the Map name field highlighted.](images/export-01-map-name.png)

## 4. Choose the layers

The plugin exports **the layers already open in this QGIS project**. There is no
file to browse for and no source to pick - build the map you want in QGIS, and
the plugin exports what you built.

![The Layers tab with one layer expanded, and the Include, Popups and Labels columns numbered one to three.](images/export-04-layers-expanded.png)

On the **Layers** tab, each layer has three tick boxes:

| Box | Ticked | Unticked |
|---|---|---|
| **Include** | The layer is in the map | The layer is left out entirely, and its data is not written to the file |
| **Popups** | Clicking a feature shows its attributes | Clicking does nothing, and the attribute values are **not written to the file** |
| **Labels** | Text you set in QGIS is drawn beside features | No text; the features themselves are unaffected |

Two of these are worth knowing about beyond the tidiness:

- Unticking **Popups** is how you keep attribute data out of a map you are
  sending someone. The values are not merely hidden - they never reach the file,
  so there is nothing to find by opening it in a text editor.
- Unticking **Include** or **Popups** makes the exported file smaller, which
  matters most for [Standalone HTML](sharing.md).

Unticking any of them affects that layer only. The map still works, with less on
it. Settings are remembered per layer, and they survive changes to the project.

**Hiding one field rather than all of them.** Expand a layer to list its fields,
each with its own setting. **Do not show this field** removes that field's values
from the file, exactly as unticking Popups does for the whole layer; the other
settings choose how the value is laid out in the popup. Use *Set every field to*
with **Apply to all layers** to set them in bulk.

One exception, and it is deliberate: a field the map *draws* with - the one a
categorised or graduated style is based on, the one labels read their text from,
or an extrusion height - stays in the file even when you hide it. Removing it
would not conceal anything, since it is on the map either way; it would only
break the drawing.

## 5. Preview

**Preview** builds the map and opens it in your default browser. Nothing opens a
browser on its own - only this button does.

With **Live preview** ticked, the map is served from your own machine and the tab
updates by itself as you change settings, keeping your position on the map. Change
a colour or switch a layer off and watch it happen; there is no need to press
Preview again.

Untick **Live preview** to open the map as a file instead. The tab then only
changes when you press Preview again or reload it. The setting is remembered
between projects.

If the preview cannot be served - a firewall or a locked-down machine - it opens
as a file automatically and tells you so. Nothing is lost.

## 6. Export

Pick how you want to share it on the Map tab, then press **Export**.

![The three output modes - Standalone HTML, Share ZIP and Folder - numbered one to three.](images/export-02-formats.png)

The line under those choices says what you are about to produce, and the strip
above the buttons says what will change on the way out. If Export is greyed out,
the reason is shown beside it.

Once an export succeeds, **Open exported map** opens the real file - the one you
would send to someone - so you can check it before sharing.

## 7. Check the Fidelity tab

Before sending the map on, look at **Fidelity**. It lists everything that
changed on the way out of QGIS - symbology that could not be translated exactly,
settings that will not appear, layers that were left out. Nothing is dropped
silently.

![The Fidelity tab with the Not exported and Changed verdicts highlighted.](images/export-06-fidelity-report.png)

That report before the export, rather than a surprise after it, is the main
difference from [qgis2web](qgis2web-comparison.md) if you are choosing between
the two.
