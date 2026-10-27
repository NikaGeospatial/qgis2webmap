---
title: Dialog design
seo_title: Designing the QGIS2WebMap export dialog
description: >-
  Why the export dialog is shaped the way it is, recorded against the specific incumbent failures it exists to avoid.
---

# Dialog design

The incumbent's dialog is functional but hostile in specific, documented ways.
This file records what we do differently and why, so the reasoning survives.

## Principles

1. **Group by task, not by scope.** qgis2web splits per-layer settings (a checkbox
   grid) from global settings (a Setting/Value table) into two panels with two
   interaction models, and puts related options on opposite sides. One conceptual
   task should not span both.
2. **Nothing configured is silently discarded.** Seven of qgis2web's Appearance
   options default to `"None"`, so a project title set in Project Properties
   simply never appears. Anything the user sets must either take effect or say why
   it did not.
3. **State is visible.** No control whose current value cannot be read from the
   control itself.
4. **Never offer an export we know is broken.** Export is disabled with a stated
   reason rather than producing a bad artifact.

## The header: what it makes, and what you do to it

One row at the top, for both outputs:

```
[Web map ▾]                         Preview [Live]  Share ▾  Export   ⚙  ?
```

- **The output is the title.** It used to be a segmented
  `Make: [Web map | Cartograph (Beta)]` on a row of its own above the tabs. It
  read as a second, louder tab bar. Esri, Google, other QGIS plugins and the
  design tools we looked at do not switch a whole set of tabs with a segmented
  control; they use a title that opens a short menu, the way Figma, Notion and
  Google Docs change the file or workspace under the same window. So the
  title opens a menu of the two outputs, each with a one-line description,
  and Cartograph carries a **Beta** chip. Ctrl+Alt+W and Ctrl+Alt+C open the
  dialog straight on either one.
- **The output's actions sit beside it**, at the right, so they are in reach
  from every tab: Preview (with a labelled Live switch), Share and Export for
  the web map; colour vision, Preview and Share for the cartograph.
- **Settings and Help are icons, not tabs.** They are about the plugin, not
  the map, and as tabs they sat beside Map and Layers as if they were part of
  the work. Each opens a page in place of the main screen, with a back arrow.

## Web map tabs

| Tab | Contents |
|---|---|
| **Map** | Map name, Export as and Save to, size readout, save status, basemap, data |
| **Layers** | One list, per-layer settings inline, popup fields in a non-reflowing expander |
| **Appearance** | Widgets — on by default, live toggles |
| **Fidelity** | Populated *before* export: suppressed settings, licence-cap violations, approximated symbology |

Settings (account, language, theme, save folder, about) and Help (the bundled
guides) are pages opened from the header.

## Cartograph: the page and a panel

The cartograph was seven tabs - Cartograph, Layers, Frames, Atlas, Fidelity,
Settings, Help - with no picture of the page anywhere. Every layout tool we
compared leads with the page: QGIS's own layout designer, ArcGIS Pro's layout
view, Felt's print mode, Canva. They put the page in the middle and the
settings in a panel beside it, split into tabs for the longer tasks.

So the cartograph is one screen:

- **Left: the live page.** It is QGIS's own layout canvas, so a change in the
  layout designer or to a layer shows at once. It is labelled
  *Layout preview — QGIS · A4 landscape* because it is QGIS's drawing, not the HTML;
  the two can differ, and saying so beats a preview that quietly disagrees
  with the file. A link beside the caption counts the differences and opens
  Checks. The bar under it has Fit page, Add item and zoom; Space or the middle button pans.
- **Right: the panel.** The current layout in a header (its name on up to
  two lines, then paper, orientation, pages and atlas), with a **Layout
  actions** menu (Rename, Import cartograph), a **New from template...** button
  and, with two or more layouts, **Switch layout...**, which opens a chooser with
  search and thumbnails of the layouts already shown. Then four tabs, the split QGIS's layout designer and
  ArcGIS Pro make beside their page: **Layout** (Page settings, Map frames
  and Layers as sections that open and close), **Atlas**, **Checks**, whose
  title carries the count, and **Edit**, for the item selected on the page. Map
  frames are cards rather than the old six-column table, which scrolled
  sideways a third of the dialog wide.
- **The file at the foot of the panel**, name first and in bold, its folder
  under it. The file saves itself, so where it goes is a detail to change,
  not a question to answer before anything works.

The default window grew from 960 × 640 to 1152 × 768 so the page and the
panel both have room; `_resize_to_fit_screen` still shrinks it to the screen.

## Keyboard and right-to-left

- **Focus is always visible.** Every control that takes keyboard focus draws
  a focus ring, including the header's icon buttons, the menu rows and the
  zoom bar, so the dialog can be used without a mouse.
- **Arabic is laid out right to left**, the whole dialog mirrored, not only
  the text - including the directional styling: arrows, the rounded ends of
  joined buttons, and the chevrons of the panel's sections.

## Map name

A plain text field at the **top of the Map tab** — the first thing in the dialog.

This exists because of a specific incumbent failure: qgis2web reads the map title
from `Project Properties → General` and the abstract from
`Project Properties → Metadata`, then renders neither unless a *second* setting
(`Appearance → Title`, default `"None"`) is also changed. Two locations, two
steps, silent failure. Its own wiki documents only the first step.

Our rules:

- **One place to set it: this field.** The exported title comes from here and
  nowhere else.
- **Prefilled, never silently overwritten.** Initial value is
  `QgsProject.title()`, falling back to the project file stem, then
  `"Untitled map"`.
- **Do not write back to the project title.** Typing in an export dialog must not
  mutate the user's project metadata as a side effect. The value persists as a
  project entry (`QgsProject.writeEntry("qgis2webmap", "mapName", ...)`) so it
  travels with the `.qgz` — the same mechanism qgis2web uses for its own settings,
  which is the one thing it gets right here.
- `QgsProject.titleChanged` updates the **placeholder** only, never the value the
  user typed.

## Live layer list

The list is driven by QGIS and must track it. qgis2web's does not: it reads the
layer tree once at construction, and the only refresh is `Set All → Apply`, whose
actual job is mutating a bulk setting — so picking up a reorder costs a settings
change, and restoring it takes a second Apply.

Verified signals on QGIS 4.0.3 (`QgsLayerTree` / `QgsProject`):

| Signal | Fires on |
|---|---|
| `layerTreeRoot().layerOrderChanged` | draw-order change — the main one |
| `layerTreeRoot().addedChildren` / `removedChildren` | add, remove, or drag between groups (a move is remove + add) |
| `layerTreeRoot().visibilityChanged` | checkbox toggled in the QGIS Layers panel |
| `layerTreeRoot().nameChanged` | layer or group renamed |
| `layerTreeRoot().customLayerOrderChanged` | user enabled a custom draw order independent of the tree |
| `QgsProject.layersAdded` / `layersRemoved` | project layer set changed |
| `QgsProject.cleared` | project closed — close the dialog |

Three implementation rules:

1. **Coalesce.** A single drag fires `willRemoveChildren`, `removedChildren`,
   `addedChildren` and `layerOrderChanged`. Connect them all to a scheduler that
   sets a dirty flag and starts a zero-delay single-shot `QTimer`, so the list
   rebuilds **once** per event-loop turn. Rebuilding per signal thrashes.
2. **Settings survive rebuilds.** Per-layer configuration lives in a dict keyed by
   layer ID, never in the widgets. A rebuild re-binds widgets to existing state,
   so reordering never loses configuration. Entries for removed layers are kept,
   so undo in QGIS restores their settings too.
3. **Disconnect on close.** Every connection made when the dialog opens is undone
   when it closes; a rebuild scheduled against a destroyed dialog must not fire.

There is no refresh button, because there is nothing to refresh.

## Size readout

A persistent readout on the Map tab showing the measured artifact size and the
tier it implies:

```
The map file inlines every feature — 3.7 MB · opens by double-click
```

Rationale: artifact weight is the main practical constraint on a shareable map,
and the incumbent gives no indication of it until the export is on disk. Showing
it while the user configures is what makes the size a decision rather than a
discovery.

## Export as, then Save to, then Export

The Map tab asks for the type first — Standalone HTML, Share ZIP or Folder —
then where it goes, and the Export button writes it there without a picker.
This is 0.1.4's flow, kept on purpose: a later attempt replaced it with a single
"map file" and moved the zip and folder into the Share menu, and testers found
the plugin's main act had disappeared. Exporting is what the plugin is for, so
it is the one filled button.

Switching the type keeps the chosen name (`peaks.html` becomes `peaks.zip` or a
`peaks` folder). A new map's Save to starts in its own folder,
`Documents/QGIS2WebMap/Web maps/<map name>.html`, with ` (2)` added when the
name is taken, and the folder is made on Export. It used to start empty,
because earlier versions remembered only the *folder* of the last export and a
reconstructed file name would name a file the user never wrote. A fixed,
visible default has neither problem: it names a new file, says where, and
never lands on someone else's. Cartographs get the same treatment in
`Cartographs`; the root is a per-user setting (Settings → Saving).

## Preview, Export and Share, in the header

All three live at the right of the header, reachable from whichever tab is
open. They are the things a user does *to* the map, and the tab they happen
to be reading when they decide to do one is not the dialog's business. (They
were in the tab bar's corner before the header took the output switch.)

This reverses an earlier call. The corner previously held a help link, placed
there because "this dialog cannot afford another row" — the same constraint that
produced `_resize_to_fit_screen`, after a fixed height put the Export button
behind the Windows taskbar with no way to reach it. That reasoning still holds
and this does not break it: the two controls moved *up* out of the bottom row,
which emptied rather than grew. The dialog is no taller than it was.

The Share rows that act on the file are disabled while Save to is empty, and
each says why on hover.
A row that needs the file writes it first, so Copy file path or Show in file
manager never point at something stale.

## Disabled with a reason, never silent

Preview, Export and Share's map rows are disabled when the map cannot be produced correctly, with
the reason in the status bar — over a licence cap, a required asset missing, no
exportable layers. This is [`docs/architecture.md`](architecture.md)
non-negotiable 5 surfaced in the UI.
