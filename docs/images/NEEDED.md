# Screenshots still needed

Excluded from the published site (see `docs/_config.yml`). This is the shot list
for the guides; drop the files in this folder under exactly these names.

## Read this before adding an image to a guide

**A guide's images can only be declared in its front matter, not written as
`![...]()` in the body.** The `.md` files in `docs/` are rendered twice: by
GitHub Pages here, and by the plugin's Help tab, which uses Qt's `QTextDocument`
with no base URL set. A relative image path resolves to nothing there, so a
reader offline in QGIS gets a broken-image box.

So each guide gets **one lead image**, declared like this and rendered by
`_layouts/default.html` above the title:

```yaml
lead_image:
  src: /images/dialog-tabs.png
  alt: The export dialog's tab bar - Map, Layers, Appearance, Fidelity, Help.
  caption: Optional.
```

The landing page's hero image works the same way, under `hero.image` in
`index.md`.

**Mid-page images are a separate decision.** They need `setBaseUrl` on the Help
tab's document and the PNGs shipped inside the plugin zip, which would take it
from 220 KB to a few MB. Worth doing if the guides need to be properly
illustrated; not done yet, and not a blocker for launch.

## Constraints on every shot

- **No real project data.** These go on a public site. Use a demo project.
- **Pick one QGIS theme and stay in it.** A mix of light and dark shots reads as
  an accident rather than a choice.
- PNG, HiDPI if the machine allows it, cropped to the dialog rather than the
  whole desktop. Crop browser chrome off any shot of an exported map — the URL
  bar of a `127.0.0.1` preview says "preview", not "the file you sent someone".

## Delivered

- `exported-map.png` — the Alaska demo open in a browser. Landing page hero.
- `dialog-tabs.png` — the tab bar. Lead image on `the-dialog.md`.

## Priority 1 — one per tab, for `the-dialog.md`

The tab *bar* is already in hand; what is missing is what each tab **contains**.
Each of these is the whole dialog window with that tab selected:

| File | Shot |
|---|---|
| `dialog-map-tab.png` | **Map** tab, a project loaded, map name filled in, Standalone HTML selected, the size line and the summary line both visible |
| `dialog-layers-tab.png` | **Layers** tab with 4–6 layers, one expanded to show its per-field popup settings |
| `dialog-appearance-tab.png` | **Appearance** tab, scrolled so Map controls, Caption and Control colours are all in frame |
| `dialog-fidelity-tab.png` | **Fidelity** tab on a project that produces a genuine mix — at least one *Changed* and one *Not exported*, sorted with problems on top |
| `fidelity-strip.png` | Tight crop of the strip above the buttons reading e.g. `3 things change on export.` with **What changes?** beside it |

## Priority 2 — `first-export.md`

| File | Shot |
|---|---|
| `export-blocked.png` | The **Export** button greyed out with its reason printed beside it |
| `live-preview.png` | QGIS and a browser side by side showing the same map, **Live preview** ticked |

## Priority 3 — `installation.md`

| File | Shot |
|---|---|
| `install-from-zip.png` | **Plugins → Manage and Install Plugins → Install from ZIP** with the plugin zip selected |
| `runtime-licence-prompt.png` | The one-off OnlyMap runtime download prompt, showing the licence |

## Also outstanding, not a screenshot

`nika_onlymap_exporter/icons/qgis2webmap.svg` and its site copy
`docs/assets/mark.svg` are both placeholder marks. Both need replacing with the
approved NIKA/OnlyMap mark before the plugin repository submission. The site's
masthead inlines the shape from `_layouts/default.html` so it can follow the
theme; `mark.svg` is the favicon and the reference. Keep the two in step.
