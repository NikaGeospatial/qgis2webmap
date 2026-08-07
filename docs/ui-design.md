---
title: Dialog design
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

## Tabs

| Tab | Contents |
|---|---|
| **Map** | Map name, description, output tier, size readout, Export |
| **Layers** | One list, per-layer settings inline, popup fields in a non-reflowing expander |
| **Appearance** | Widgets — on by default, live toggles |
| **Fidelity** | Populated *before* export: suppressed settings, licence-cap violations, approximated symbology |
| **Help** | About, privacy statement, documentation links |

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
Standalone HTML — 3.7 MB · opens by double-click, no internet needed
```

Rationale: artifact weight is the main practical constraint on a shareable map,
and the incumbent gives no indication of it until the export is on disk. Showing
it while the user configures makes the tier choice self-explanatory.

## Export button

Disabled when the export cannot be produced correctly, with the reason shown next
to it — over a licence cap, a required asset missing, no exportable layers. This
is [`docs/architecture.md`](architecture.md) non-negotiable 5 surfaced in the UI.
