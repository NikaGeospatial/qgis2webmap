---
layout: home
title: QGIS2WebMap by NIKA
description: >-
  A QGIS plugin that turns a finished project into an interactive web map that
  opens locally. No account, no web server, no coding.

hero:
  eyebrow: QGIS plugin
  headline_before: "Send someone your map, "
  headline_em: "not your QGIS project"
  headline_after: "."
  lede: >-
    QGIS2WebMap exports a finished QGIS project as one HTML file. The person you
    send it to double-clicks it and gets your map — panning, zooming, layer
    switching, popups — with no QGIS, no account and no internet connection.
  actions:
    - label: Install the plugin
      url: /installation.html
      style: primary
    - label: Walk through a first export
      url: /first-export.html

verdicts:
  caption: Before you export, every property of your project is given one of these five verdicts
  items:
    - key: Kept
      tone: kept
      text: Survives the trip exactly as you set it in QGIS.
    - key: Changed
      tone: changed
      text: Exported, but approximated — and the report says how.
    - key: Rasterised
      tone: changed
      text: Drawn as an image because the style has no live equivalent.
    - key: Not exported
      tone: blocked
      text: Left out. Named, so you find out here rather than later.
    - key: Blocked
      tone: blocked
      text: Would produce a broken map, so Export stays disabled.

sections:
  - title: Start here
    lede: >-
      Three guides, in the order you will need them. Roughly twenty minutes from
      a fresh install to a map you can email.
    cards:
      - step: STEP 1
        title: Install the plugin
        text: >-
          Install from a zip, and understand the one download the plugin ever
          makes. Offline and proxy paths included.
        url: /installation.html
      - step: STEP 2
        title: Your first export
        text: >-
          Compose the map, pick your layers, preview it live, and export. The
          full path from QGIS canvas to shareable file.
        url: /first-export.html
      - step: STEP 3
        title: Share the map
        text: >-
          Which of the three output modes to pick, and why an email filter is
          usually the thing that decides it.
        url: /sharing.html

  - title: Reference
    lede: >-
      What the controls do, what survives the export, and what happens to your
      data along the way.
    cards:
      - title: The dialog, tab by tab
        text: >-
          Every control on the Map, Layers, Appearance, Fidelity and Help tabs,
          and what each one changes for the recipient.
        url: /the-dialog.html
      - title: What gets exported
        text: >-
          Layers, symbology, labels, popups, height, terrain and attribution —
          preserved, approximated or not at all.
        url: /supported-features.html
      - title: Privacy
        text: >-
          What is in the exported file, what leaves your machine, and how to
          check the claim yourself in a browser.
        url: /privacy.html
      - title: Troubleshooting
        text: >-
          The plugin does not appear, the export is greyed out, the map is
          blank, the file was blocked in transit.
        url: /troubleshooting.html

  - title: Going further
    cards:
      - title: Host with OnlyMap
        text: >-
          Put the map on the web. What you are asked before anything is
          published, and what the free plan's limits mean once you do.
        url: /hosting.html
      - title: Enhance a map with AI
        text: >-
          An exported map is readable HTML, so an assistant can add filters,
          charts or branding without breaking portability.
        url: /enhance-with-ai.html
      - title: OnlyMap documentation
        text: >-
          The upstream map library your exported maps run on, documented in
          full at onlymap.nikaplanet.com.
        url: https://onlymap.nikaplanet.com/
---

## In short

1. Build and style your map in QGIS as usual, and save the project.
2. **Web → QGIS2WebMap by NIKA → Create web map**, or the toolbar button.
3. Give the map a name, tick the layers to include, and press **Export**.

The plugin exports the project as it is, so there is no source to browse for and
nothing to configure twice. What you see on the QGIS canvas is what leaves.

## What you get

| Mode | What the recipient does | When to use it |
|---|---|---|
| **Standalone HTML** | Double-clicks one file | The default. Everything is embedded, nothing is fetched |
| **Share ZIP** | Extracts it, opens `index.html` | The data is too large for one practical file, or a mail filter quarantines `.html` |
| **Folder** | Serves it, or opens `index.html` | You are publishing the map on your own web server |

Compression is lossless. No coordinate precision is discarded to shrink a file
unless you ask for it, and where a project genuinely will not fit in one file the
plugin recommends the next mode rather than quietly degrading the data.

## Exported maps make no network requests

Opening an exported map sends nothing anywhere. No analytics, no beacons, no
fonts or scripts from a CDN, no identifier of any kind. It works with the network
cable unplugged, on a machine that has never had QGIS installed.

There is one exception and it is yours to make: choosing a basemap means the
recipient's browser fetches tiles from that provider. The dialog warns when you
do, and the Fidelity tab names the provider. See [privacy](privacy.md).

## Requirements

QGIS 3.44 or newer, on Windows or macOS. QGIS 4 is supported.

The first export downloads the OnlyMap runtime — about 4.5 MB, once per computer,
after showing you its licence. Everything works offline after that, and exporting
itself never touches the network. See [installation](installation.md) for the
offline and proxy paths.

---

Built by [NIKA](https://nikaplanet.com), powered by
[OnlyMap](https://www.nikaplanet.com/onlymap) — the map library exported maps run
on, documented at [onlymap.nikaplanet.com](https://onlymap.nikaplanet.com/).
QGIS2WebMap is not endorsed by QGIS.org.
