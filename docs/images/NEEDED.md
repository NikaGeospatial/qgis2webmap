# Screenshots still needed

Excluded from the published site (see `docs/_config.yml`). This is the shot list
for the guides; drop the files in this folder under exactly these names.

## Read this before adding an image to a guide

**Inline `![...](images/foo.png)` is the way to add an image.** Paths are
relative to the guide, and the site serves them from `/images/`.

The plugin's Help tab renders these same `.md` files through Qt's
`QTextDocument`, which has no base URL and no PNGs in the zip - so
`strip_images()` in `nika_onlymap_exporter/ui/main_dialog.py` removes every
image at read time. **The website gets the pictures; the Help tab gets the
text.** That split is deliberate: shipping the images would take the zip from
223 KB to about 2.1 MB for a reader already sitting in front of the dialog the
screenshots depict.

Two rules follow, and both have already bitten:

- **Indent an image inside a numbered list by three spaces.** An image flush to
  the margin ends the list, and the numbering restarts at 1 after it.
- **Write real alt text.** It is the only thing a screen reader gets, and it is
  dropped along with the image in the Help tab, so it must not be carrying
  information the prose does not already state.

`lead_image:` front matter still works and still renders above the title - use
it for a guide whose single best image is scene-setting rather than
step-by-step. Do not set one that duplicates an inline image on the same page.

`tests/unit/test_docs.py::TestHelpTabImages` fails if any image survives into
the Help text, and also if the guides stop carrying images at all.

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

Annotations were drawn with `scripts/annotate_screenshot.py`, which implements
the house style below. Re-run it against the raw capture rather than editing a
PNG by hand — the raw shots are the source, the annotated files are output.

- `exported-map.png` — the Alaska demo in a browser. Landing page hero.
- `dialog-tabs.png` — the tab bar. Lead image on `the-dialog.md`.
- `install-01-plugins-menu.png` — **Plugins** menu, boxed.
- `install-02-search-result.png` — search field and result row, numbered.
- `install-03-install-button.png` — **Install Plugin**, boxed.
- `install-05-installed-tab.png` — **Installed** tab and the ticked checkbox.
- `dialog-map-tab.png`, `dialog-layers-tab.png`, `dialog-appearance-tab.png`,
  `dialog-fidelity-tab.png` — one per tab.
- `fidelity-strip.png` — the "2 things change on export" strip.
- `export-01-map-name.png`, `export-02-formats.png`,
  `export-03-summary-line.png`, `export-04-layers-expanded.png`,
  `export-05-live-preview.png`, `export-06-fidelity-report.png` — annotated.
- `live-preview.png` — QGIS and the browser side by side.

**Two of these carried personal data and were redacted, not re-shot.** The
Filepath field read `/home/abhijay/…` and the QGIS Browser panel listed saved
database connections by name. `annotate_screenshot.py --redact` paints a flat
block rather than blurring: a blur of a short string is often reversible, and on
a public site reversible means published. **Check every new capture for this** -
the Filepath field and the Browser panel are the two that bite.

## Priority 2 — `first-export.md`

| File | Shot |
|---|---|
| `export-blocked.png` | The **Export** button greyed out with its reason printed beside it. Needs a project that actually blocks — the demo project exports cleanly, so this one cannot be derived from the shots already taken |

## "Going further" — `index.md` and `hosting.md`

The landing page's *Going further* block is three cards of pure text, and
`hosting.md` behind it is unillustrated end to end. These are the shots that
would fix that. None is annotated — they are showing an outcome, not a button.

| File | Page | Shot |
|---|---|---|
| `hosted-map-in-browser.png` | `hosting.md` | The same demo map open at a **real https:// address**, full browser window with the URL bar left in. This is the whole point of the page — the URL bar is the message, so unlike every other browser shot here, do not crop it |
| `hosting-free-plan-limits.png` | `hosting.md` | The dialog or the OnlyMap account view where the free plan's layer and feature limits are stated, so "where the limits start" has a face |
| `map-before-after-ai.png` | `enhance-with-ai.md` | Two crops side by side of the same map. **Both files exist**: `~/Downloads/map.html` is the plain "before"; `~/Downloads/map-after-ai.html` is the "after", with airports recoloured by `USE` and an elevation filter added. Open each, screenshot, hand both back to be composited. Note the filter widget is a **numeric range control only** - `format` accepts number or date, so a text field renders as a meaningless slider |
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
