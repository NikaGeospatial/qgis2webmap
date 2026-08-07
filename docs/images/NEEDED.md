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

**Mid-page images are a separate decision, and they are now the blocker.** They
need `setBaseUrl` on the Help tab's document and the PNGs shipped inside the
plugin zip, which would take it from 220 KB to a few MB.

Everything in the "step by step" tables below is a **mid-page** image by
definition — a shot of the button you are being told to click has to sit next to
that step, not float at the top of the page as the single lead image. So
capturing these shots is wasted effort until that work lands. Do the plumbing
first:

1. Call `setBaseUrl` on the Help tab's `QTextDocument` so relative paths resolve.
2. Ship `docs/images/*.png` inside the plugin zip (`HELP_DOCS` in
   `scripts/package_plugin.py` copies the `.md` files already; images need the
   same treatment).
3. Re-check the zip against the plugin repository's size expectations, and
   re-run `scripts/verify_package.py`.
4. Only then switch the guides from one `lead_image` to inline `![...]()`.

## Constraints on every shot

- **No real project data.** These go on a public site. Use a demo project.
- **Pick one QGIS theme and stay in it.** A mix of light and dark shots reads as
  an accident rather than a choice.
- PNG, HiDPI if the machine allows it, cropped to the dialog rather than the
  whole desktop. Crop browser chrome off any shot of an exported map — the URL
  bar of a `127.0.0.1` preview says "preview", not "the file you sent someone".

## Annotation style

Any shot marked **annotated** below carries a drawn overlay. Keep it uniform, or
the guides look assembled by different people:

- **One red rectangle**, `#e03131`, 3 px, square corners, around the single
  thing the reader must click. One box per shot wherever possible.
- **Numbered circles** — same red, white numeral — only where a step genuinely
  has an ordered sequence inside one frame. Number in reading order.
- **No arrows, no drop shadows, no blur.** An arrow is what you reach for when
  the crop is wrong; re-crop instead.
- Never cover a label the reader needs to read against. Box it, don't fill it.
- Draw at final resolution. A box scaled with the image gets fuzzy edges.

Red on a dark QGIS theme is weak — this is the second reason to
[pick one theme](#constraints-on-every-shot) and, if it is dark, to test the red
against it before capturing thirty shots.

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

## Step by step — `installation.md`, annotated

Blocked on the mid-page plumbing above. Every shot here is **annotated**.

| File | Shot | Annotation |
|---|---|---|
| `install-01-plugins-menu.png` | QGIS main window, **Plugins** menu open | Box **Manage and Install Plugins…** |
| `install-02-search-result.png` | The plugin manager with `QGIS2WebMap` typed in the search box | Box the search box and the **QGIS2WebMap by NIKA** row, numbered 1 and 2 |
| `install-03-install-button.png` | Same dialog, our plugin selected, button row in frame | Box **Install Plugin** |
| `install-04-web-menu.png` | QGIS main window, **Web** menu open after install | Box the **QGIS2WebMap by NIKA** submenu entry |
| `install-05-installed-tab.png` | Plugin manager, **Installed** tab, our row with its checkbox ticked | Box the checkbox — this is the fix for "installed but no Web menu" |
| `install-06-log-messages.png` | **View → Panels → Log Messages**, panel open on the QGIS2WebMap tab | Box the panel's QGIS2WebMap tab. Troubleshooting only |

## Step by step — `first-export.md`, annotated

Blocked on the mid-page plumbing above. Every shot here is **annotated**.

| File | Shot | Annotation |
|---|---|---|
| `export-01-map-name.png` | **Map** tab, top section, a name typed in | Box the **Map name** field |
| `export-02-formats.png` | **Map** tab, the "How to share it" block, all three options visible | Numbered 1–3 on Standalone HTML / Share ZIP / Folder |
| `export-03-summary-line.png` | **Map** tab, the size and summary lines above the buttons | Box the summary sentence — this is what tells you what you are about to get |
| `export-04-layers-expanded.png` | **Layers** tab, one layer expanded to its per-field popup settings | Numbered 1–3 on the Include / Popups / Labels columns |
| `export-05-live-preview.png` | Button row, **Live preview** ticked | Box the checkbox |
| `export-06-fidelity-report.png` | **Fidelity** tab with a real mix of verdicts, one row expanded | Box one *Changed* row and one *Not exported* row, numbered 1 and 2 |

Unannotated shots for the same two pages — `qgis-project-styled.png` (a demo
project with 3–4 styled layers, Layers panel in frame) and `plugin-dialog-open.png`
(the dialog open with QGIS still usable behind it) — are worth having but are
scene-setting, not instruction.

## "Going further" — `index.md` and `hosting.md`

The landing page's *Going further* block is three cards of pure text, and
`hosting.md` behind it is unillustrated end to end. These are the shots that
would fix that. None is annotated — they are showing an outcome, not a button.

| File | Page | Shot |
|---|---|---|
| `hosted-map-in-browser.png` | `hosting.md` | The same demo map open at a **real https:// address**, full browser window with the URL bar left in. This is the whole point of the page — the URL bar is the message, so unlike every other browser shot here, do not crop it |
| `hosting-free-plan-limits.png` | `hosting.md` | The dialog or the OnlyMap account view where the free plan's layer and feature limits are stated, so "where the limits start" has a face |
| `map-before-after-ai.png` | `enhance-with-ai.md` | Two crops side by side of the same map — plain on the left, and on the right the same map after an assistant added a filter control and recoloured a layer. The single most useful image on the site and the one nobody can imagine from text |
| `onlymap-docs-site.png` | `index.md` | `onlymap.nikaplanet.com` open in a browser, cropped to the top of a page that shows the attribute reference. Gives the third card something to be |

## Lower value, capture last

| File | Page | Shot |
|---|---|---|
| `host-onlymap-credit-link.png` | `hosting.md` | Exported map in a browser, boxed **Host with OnlyMap** link in the credit |
| `fidelity-blocked-items.png` | `troubleshooting.md` | Fidelity tab showing a **Blocked** verdict, boxed |
| `export-progress-bar.png` | `the-dialog.md` | Export in progress, layer counter and Cancel visible |
| `help-about-qgis-version.png` | `installation.md` | **Help → About** showing the version, for the 3.44 prerequisite |

`enhance-with-ai.md` needs no QGIS screenshots — it is about editing the
exported HTML, and its examples are already code blocks.

## Also outstanding, not a screenshot

`nika_onlymap_exporter/icons/qgis2webmap.svg` and its site copy
`docs/assets/mark.svg` are both placeholder marks. Both need replacing with the
approved NIKA/OnlyMap mark before the plugin repository submission. The site's
masthead inlines the shape from `_layouts/default.html` so it can follow the
theme; `mark.svg` is the favicon and the reference. Keep the two in step.
