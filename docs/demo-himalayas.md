---
layout: demo
title: How high do climbers get?
description: >-
  Expedition records for 74 Nepal Himalaya peaks as an interactive web map,
  exported straight from QGIS with QGIS2WebMap — compared side by side with
  QGIS's own rendering.
image: /assets/demos/himalayas-poster.png
eyebrow: Demo · Nepal Himalaya · graduated colour + 3D relief
lede: >-
  Expedition records for 74 peaks, averaged per peak: the colour around
  each summit is *how far up its climbers typically get*, painted straight
  onto *real 3D terrain*.
tip: Ctrl + drag to tilt into 3D
map: /assets/demos/himalayas.html
project: /assets/demos/himalayas-qgis-project.zip
credits: >-
  Data: The Himalayan Database (himalayandatabase.com); peak coordinates
  © OpenStreetMap contributors, ODbL. Basemap and relief imagery © CARTO,
  © OpenStreetMap contributors.
compare:
  viewer: /assets/demos/qgis-side.html
  note: >-
    Comparison view — the left pane is QGIS's own rendering of this
    project, pre-drawn to map tiles; the right pane is the live export.
    The panes share one camera, fenced to the pre-rendered area; the left
    stays flat, exactly as the QGIS canvas would, so with the relief
    tilted the summits lean toward you only on the right.
  home:
    longitude: 86.92
    latitude: 27.96
    zoom: 9.5
  fence:
    west: 86.0
    east: 88.7
    south: 27.1
    north: 28.5
    min_zoom: 6.3
recipe:
  - setting: Plugin version
    value: "**0.1.3 or newer** — earlier versions have no relief option"
  - setting: Map tab · Basemap
    value: "**Voyager**"
  - setting: Map tab · Ground surface
    value: "**Global relief**"
  - setting: Map tab · Extent
    value: "**Current canvas view** (frame Everest–Kangchenjunga first)"
---

## Run these yourself

The project download is a small zip with the `.qgz`, its data and a README
with each map's dialog settings. Open it in QGIS 3.44 or newer and export it
through *Web → QGIS2WebMap by NIKA* — you get the same file that is on
stage. The maps fetch basemap tiles at view time; the
[sharing guide](sharing.md) explains what an export contains.
