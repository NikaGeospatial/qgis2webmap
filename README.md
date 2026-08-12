<p align="center">
  <img src="docs/assets/banner.svg" alt="QGIS2WebMap by NIKA - turn a QGIS project into a portable web map" width="820">
</p>

# QGIS2WebMap by NIKA

**Turn a QGIS project into a portable OnlyMap web map.**

### [Read the documentation →](https://nikageospatial.github.io/qgis2webmap/)

Installation, your first export, every dialog option, hosting and
troubleshooting — the full guides, rendered and searchable.

A QGIS plugin that converts a finished QGIS project into an interactive web map
that opens locally. No account, no web server, no coding. The default export is a
**single HTML file** another person can double-click and use without QGIS
installed.

Built by [NIKA](https://nikaplanet.com), powered by
[OnlyMap](https://www.nikaplanet.com/onlymap) - the upstream map library this
plugin exports to, documented at
[onlymap.nikaplanet.com](https://onlymap.nikaplanet.com/).

> **Status: early release.** `0.1.2` is published on the
> [QGIS Plugin Repository](https://plugins.qgis.org/plugins/nika_onlymap_exporter/)
> and under active development. The repository layout and task plan follow
> [onlymap-js issue #29](https://github.com/NikaGeospatial/onlymap-js/issues/29).

---

## Privacy

**Exported maps send one anonymous usage report when they load, and nothing
else.** That report can include the runtime version, feature and widget usage
counts, and the hostname of the page the map is running from — never the
map's data or who opened it. Aside from that one report, an exported file
works with no internet connection, on a machine that has never had QGIS
installed. Publishing to NIKA hosting and AI-assisted enhancement are separate,
explicit actions you start yourself — nothing is uploaded automatically.

See the [privacy page](https://nikageospatial.github.io/qgis2webmap/privacy.html)
for exactly what is and isn't sent.

---

## What it produces

| Mode | What the recipient does | When it is used |
|---|---|---|
| **Standalone HTML** | Double-click one file | Default. All resources embed and no remote resource is required |
| **Share ZIP** | Extract, then open `index.html` | Data too large for one practical HTML file, or a mail filter that quarantines `.html` |
| **Folder** | Copy to a web server, or open `index.html` | Publishing the result yourself |

Compression is **lossless** — no coordinate precision is discarded to shrink a
file. Where a project genuinely will not fit one file, the plugin recommends the
next mode rather than degrading the data.

Two further modes are designed but **not in `0.1.0`**: a Large Local Package
with a bundled launcher, for tiled or range-request assets, and Publish with
OnlyMap for one-click hosting. See [issue #29][issue] for the full output
policy.

[issue]: https://github.com/NikaGeospatial/onlymap-js/issues/29

---

## Installation

**Plugins → Manage and Install Plugins… → All**, search `QGIS2WebMap`, and press
**Install Plugin**. The listing is
[plugins.qgis.org/plugins/nika_onlymap_exporter](https://plugins.qgis.org/plugins/nika_onlymap_exporter/).

To run an unreleased build instead:

```bash
git clone https://github.com/NikaGeospatial/qgis2webmap.git
cd qgis2webmap
python scripts/package_plugin.py
```

Then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, and
choose `dist/qgis2webmap-<version>.zip`.

Requires QGIS 3.44 or newer (QGIS 4 supported).

**The first export downloads the map runtime** — about 4.5 MB, once per computer,
after showing you its licence. Everything works offline after that. The runtime
is the code that draws the map in a browser and is built into every map you
export; it is a separate commercial product with its own licence, which is why
it is fetched rather than bundled into this GPL plugin. See
[installation](docs/installation.md) for the offline and proxy paths.

---

## Documentation

Everything below is published as a website at
**[nikageospatial.github.io/qgis2webmap](https://nikageospatial.github.io/qgis2webmap/)**
— read it there rather than as raw Markdown. The same guides ship inside the
plugin's Help tab, so they work offline too.

- [Installation](docs/installation.md)
- [Your first export](docs/first-export.md)
- [The dialog, tab by tab](docs/the-dialog.md)
- [Sharing a map](docs/sharing.md)
- [Host with OnlyMap](docs/hosting.md)
- [Enhance a map with AI](docs/enhance-with-ai.md)
- [What gets exported](docs/supported-features.md)
- [QGIS2WebMap or qgis2web](docs/qgis2web-comparison.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Privacy](docs/privacy.md)

Deeper articles live at [NIKA Documentation](https://docs.nikaplanet.com).

## What gets exported

Vector points, lines and polygons from GeoPackage, Shapefile, GeoJSON and CSV
sources, with single-symbol, categorized and graduated styling; layer order,
groups, visibility, scale ranges, field aliases and hidden fields; labels,
popups, and data attribution. Every map ships a legend, layer switcher, zoom
controls and a scale bar.

Anything that will not survive the trip is listed in the **Fidelity** tab
*before* you export, classified as preserved, approximated, raster-fallback,
unsupported or blocked. Nothing is dropped silently. Full list:
[what gets exported](docs/supported-features.md).

### Size limits if you host the map

The OnlyMap runtime that draws your map has a free plan with three limits:
**5 layers per map**, **25,000 features per layer** and **20 MB of fetched data**.

**None of them applies to a map opened locally** — a file you double-click, a map
you email someone, `localhost` or `127.0.0.1` all draw everything, with no
licence key. The limits apply only to a page served over `http(s)` from a real
domain.

So they matter when you **publish the map to a web server**. There, layers past
the fifth render nothing, and a layer past 25,000 features is truncated to its
first 25,000 while still looking complete. Nothing warns you at export time,
because for almost every export there would be nothing to warn about: check the
**Fidelity** tab, which names every layer over a limit, before you host.
An OnlyMap licence key lifts the limits and removes the on-map badge — supply it
through the `ONLYMAP_LICENSE_KEY` environment variable or the Processing
algorithm's licence parameter. Note that lifted limits are a technical
convenience and not a licence grant: commercial use needs a key either way. See
[size limits on the free plan](docs/supported-features.md#size-limits-on-the-free-plan).

## Enhance with AI

An exported map is a readable HTML document, not a compiled bundle — so an AI
assistant can edit it. Point Claude Code or Codex at the OnlyMap skill and ask
for filters, charts, stories or custom branding, and the map stays portable.
See [enhance a map with AI](docs/enhance-with-ai.md).

## Host with OnlyMap

Every exported map carries a **Host with OnlyMap** link. Hosting is always an
explicit action you start: the artifact never uploads anything on its own, and
publishing asks you to confirm you are authorised to and to choose Public,
Unlisted or Private. See [hosting](docs/hosting.md).

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
python scripts/verify_package.py dist/qgis2webmap-*.zip  # check its shape
python -m pytest tests/unit                                        # unit tests
```

Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Support

- Questions and bug reports: [GitHub issues](https://github.com/NikaGeospatial/qgis2webmap/issues)
- Documentation: [NIKA Documentation](https://docs.nikaplanet.com)
- Email: support@nikaplanet.com

---

## Licence

The plugin is **GPL-2.0-or-later** — see [`LICENSE`](LICENSE).

The OnlyMap runtime embedded in exported maps is separately licensed; see
[nikaplanet.com/onlymap](https://www.nikaplanet.com/onlymap).

---

QGIS2WebMap is built by NIKA and is **not endorsed by QGIS.org**.
QGIS® is a trademark of the QGIS project.
