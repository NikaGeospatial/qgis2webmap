# QGIS2WebMap by NIKA

**Turn a QGIS project into a portable OnlyMap web map.**

A QGIS plugin that converts a finished QGIS project into an interactive web map
that opens locally. No account, no web server, no coding. The default export is a
**single HTML file** another person can double-click and use without QGIS
installed.

Built by [NIKA](https://nikaplanet.com), powered by
[OnlyMap](https://www.nikaplanet.com/onlymap).

> **Status: pre-release.** `0.1.0` is under active development and is not yet
> published to the QGIS Plugin Repository. The repository layout and task plan
> follow [onlymap-js issue #29](https://github.com/NikaGeospatial/onlymap-js/issues/29).

---

## Privacy

**Exported maps contain no tracking and make no network requests.** An exported
file works with no internet connection, on a machine that has never had QGIS
installed. Publishing to NIKA hosting and AI-assisted enhancement are separate,
explicit actions you start yourself — nothing is uploaded automatically.

This is a deliberate default, stated here rather than buried in a privacy page.

---

## What it produces

| Mode | What the recipient does | When it is used |
|---|---|---|
| **Standalone HTML** | Double-click one file | Default. All resources embed and no remote resource is required |
| **Share ZIP** | Extract, then open `index.html` | Data too large for one practical HTML file |
| **Large Local Package** | Run the included launcher, open localhost | Tiled or range-request assets |
| **Publish with OnlyMap** | Open a share link | Explicit, opt-in, with a data-inventory confirmation |

Compression is **lossless** — no coordinate precision is discarded to shrink a
file. Where a project genuinely will not fit one file, the plugin recommends the
next mode rather than degrading the data.

---

## Installation

Not yet on the QGIS Plugin Repository. To try the current build:

```bash
git clone https://github.com/NikaGeospatial/qgis2webmap.git
cd qgis2webmap
python scripts/package_plugin.py
```

Then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, and
choose `dist/nika_onlymap_exporter-<version>.zip`.

Requires QGIS 3.22 or newer (QGIS 4 supported).

---

## Usage

**Web → QGIS2WebMap by NIKA → Create web map**, or the toolbar icon.

Compose the map in QGIS first — load layers, style them, set the canvas to the
extent you want as the default view, and save the project. The plugin exports the
project as it is.

---

## Development

```
nika_onlymap_exporter/     the plugin package (what ships)
├── core/                  project reader, normalized export model, fidelity report
├── writers/               normalized model → OnlyMap manifest
├── exporters/             manifest → standalone HTML / zip / folder / hosting
├── packaging/             asset embedding, dependency scanning, runtime management
├── processing/            QGIS Processing provider
├── templates/             artifact shell + exported READMEs
└── ui/                    the export dialog

docs/                      user documentation, served via GitHub Pages
scripts/                   package_plugin.py, verify_package.py
tests/                     unit / qgis / browser / fixtures
```

Architecture notes: [`docs/architecture.md`](docs/architecture.md) ·
Dialog design: [`docs/ui-design.md`](docs/ui-design.md).

```bash
python scripts/package_plugin.py                                   # build the zip
python scripts/verify_package.py dist/nika_onlymap_exporter-*.zip  # check its shape
python -m pytest tests/unit                                        # unit tests
```

Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Licence

The plugin is **GPL-2.0-or-later** — see [`LICENSE`](LICENSE).

The OnlyMap runtime embedded in exported maps is separately licensed; see
[nikaplanet.com/onlymap](https://www.nikaplanet.com/onlymap).

---

QGIS2WebMap is built by NIKA and is **not endorsed by QGIS.org**.
QGIS® is a trademark of the QGIS project.
