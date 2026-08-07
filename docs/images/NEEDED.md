# Screenshots still needed

Excluded from the published site (see `docs/_config.yml`). This is the shot list
for the guides; drop the files in this folder under exactly these names and the
image lines can be added to the Markdown.

Two constraints on every shot:

- **No real project data.** These go on a public site. Use a demo project.
- **Take both a light and a dark one** where the QGIS theme is visible, or take
  everything in QGIS's default light theme and we leave it at that. Mixed is
  worse than either.

Capture at 2x / HiDPI if the machine allows it, PNG, and crop to the dialog
rather than the whole desktop unless the shot is about where something lives in
QGIS.

## Priority 1 — the dialog guide (`the-dialog.md`)

| File | Shot |
|---|---|
| `dialog-map-tab.png` | The whole dialog on the **Map** tab, a project loaded, map name filled in, Standalone HTML selected, size line visible |
| `dialog-layers-tab.png` | The **Layers** tab with 4–6 layers, one expanded to show its per-field popup settings |
| `dialog-appearance-tab.png` | The **Appearance** tab, scrolled so Map controls, Caption and Control colours are all in frame |
| `dialog-fidelity-tab.png` | The **Fidelity** tab on a project that genuinely produces a mix — at least one *Changed* and one *Not exported*, sorted with problems on top |
| `fidelity-strip.png` | Tight crop of the strip above the buttons reading e.g. `3 things change on export.` with **What changes?** beside it |

## Priority 2 — first export (`first-export.md`)

| File | Shot |
|---|---|
| `menu-entry.png` | The QGIS **Web** menu open, showing **QGIS2WebMap by NIKA → Create web map** |
| `export-blocked.png` | The **Export** button greyed out with a reason printed beside it |
| `live-preview.png` | QGIS and a browser side by side, the same map in both, **Live preview** ticked |

## Priority 3 — landing page and sharing

| File | Shot |
|---|---|
| `exported-map.png` | An **exported map open in a browser**, wide, with legend, layer switcher, zoom and scale bar visible and a popup open. This is the single most valuable image on the site — it is what the product produces |
| `install-from-zip.png` | **Plugins → Manage and Install Plugins → Install from ZIP** with the plugin zip selected |

## Nice to have

| File | Shot |
|---|---|
| `runtime-licence-prompt.png` | The one-off OnlyMap runtime download prompt, showing the licence |
| `share-zip-contents.png` | An extracted Share ZIP, showing `index.html` and the README next to each other |

## Also outstanding, not a screenshot

`nika_onlymap_exporter/icons/qgis2webmap.svg` is a placeholder mark, and
`docs/assets/mark.svg` is a copy of it. Both need replacing with the approved
NIKA/OnlyMap mark before the plugin repository submission. The site's masthead
and favicon read from `docs/assets/mark.svg`.
