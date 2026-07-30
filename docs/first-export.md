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

## 4. Choose the layers

On the **Layers** tab, tick the layers to include and whether each has popups and
labels. Settings are remembered per layer, and they survive changes to the
project.

## 5. Preview

**Preview in browser** writes the map and opens it in your default browser. After
making changes in QGIS, press Preview again, or just reload the browser tab - the
address does not change and your position on the map is kept.

## 6. Export

Pick how you want to share it on the Map tab, then press **Export**.

If Export is greyed out, the reason is shown beside it.

## 7. Check the Fidelity tab

Before sending the map on, look at **Fidelity**. It lists everything that
changed on the way out of QGIS - symbology that could not be translated exactly,
settings that will not appear, layers that were left out. Nothing is dropped
silently.
