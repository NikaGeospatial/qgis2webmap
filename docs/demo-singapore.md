---
layout: demo
title: The MRT network
description: >-
  Singapore's MRT network on real track geometry as an interactive web map,
  exported straight from QGIS with QGIS2WebMap.
image: /assets/demos/singapore-poster.png
eyebrow: Demo · Singapore · categorized lines + markers
lede: >-
  The whole network on real track geometry, every line in its official
  colour, every station clickable — interchanges included, right up to
  the closed Circle Line loop.
map: /assets/demos/singapore.html
project: /assets/demos/singapore-qgis-project.zip
credits: >-
  Data: LTA DataMall and data.gov.sg (Singapore Open Data Licence v1.0);
  line geometry © OpenStreetMap contributors, ODbL. Basemap © CARTO,
  © OpenStreetMap contributors.
recipe:
  - setting: Map tab · Basemap
    value: Positron
  - setting: Map tab · Extent
    value: Current canvas view (frame the island first)
  - setting: Layers tab · "MRT lines"
    value: Popups off — station clicks then always land on the station
---

## Run these yourself

The project download is a small zip with the `.qgz`, its data and a README
with each map's dialog settings. Open it in QGIS 3.44 or newer and export it
through *Web → QGIS2WebMap by NIKA* — you get the same file that is on
stage. The maps fetch basemap tiles at view time; the
[sharing guide](sharing.md) explains what an export contains.
