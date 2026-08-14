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
  # The hero is a transformation, not a picture: the dialog that does the work
  # on the left, its output on the right, and an arrow that names the settings
  # responsible. Composited in HTML rather than baked into one image so the
  # label uses the site's own type and accent, the halves stack on a phone, and
  # the moving half can be a video without carrying the static half's pixels.
  compose:
    arrow: solid
    label: Export
    sublabel: Voyager + Global relief
    left:
      src: /images/hero-qgis-dialog.png
      caption: In QGIS
      alt: >-
        The QGIS2WebMap export dialog open over a QGIS project of the Nepal
        Himalaya, with the basemap set to Voyager and the ground surface set to
        global relief.
    right:
      poster: /images/hero-webmap-placeholder.png
      caption: In a browser
      placeholder: true
      alt: >-
        The same project as a web map in a browser, tilted so the peaks stand
        up in 3D, each summit coloured by how far up its climbers typically get.
    caption: >-
      The 3D ground is an option in the export dialog - global elevation data
      fetched as the map is used, not terrain from the QGIS project. Everything
      else on the right is the project itself: the same layers, the same
      graduated colours, now panning, zooming and answering clicks.

verdicts:
  title: You always know what changed
  lede: >-
    Not everything QGIS can draw has an equivalent on the web. Before you export,
    the plugin checks your whole project and tells you exactly what will be
    different — on the Fidelity tab, while you can still do something about it.
    Every item gets one of five plain answers.
  items:
    - key: Kept
      tone: kept
      text: It comes out exactly as you set it in QGIS.
    - key: Changed
      tone: changed
      text: It comes out close, but not identical. You are told what changed.
    - key: Rasterised
      tone: changed
      text: >-
        It comes out as a picture instead of a live layer, because the web has
        no equivalent for that style.
    - key: Not exported
      tone: blocked
      text: >-
        It is left out. The map still works — you just find out here rather than
        from whoever you sent it to.
    - key: Blocked
      tone: blocked
      text: >-
        Exporting would produce a broken map, so the Export button stays off
        until it is fixed.

sections:
  - title: Start here
    lede: >-
      Three guides, in the order you will need them. Roughly twenty minutes from
      a fresh install to a map you can email.
    cards:
      - step: STEP 1
        title: Install the plugin
        text: >-
          Six clicks inside QGIS — nothing to download by hand. Also covers the
          one permission the plugin asks for later.
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
          Give your map a web address instead of sending a file. What hosting
          asks of you, and where the free plan's limits start.
        url: /hosting.html
      - title: Enhance a map with AI
        text: >-
          Add filters, charts or your own branding by describing them to a
          coding assistant. The map stays a single portable file.
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
