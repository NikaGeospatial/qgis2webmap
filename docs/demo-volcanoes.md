---
layout: demo
title: Living next to a volcano
description: >-
  Fifty-one active Indonesian volcanoes with VEI-scaled hazard rings as an
  interactive web map, exported straight from QGIS with QGIS2WebMap.
image: /assets/demos/volcanoes-poster.png
eyebrow: Demo · Indonesia · hazard rings + SVG markers
lede: >-
  Fifty-one active volcanoes along the arc, each with hazard rings sized
  by the reach of its plausible eruptions — click a volcano for its
  eruptive history.
map: /assets/demos/volcanoes.html
project: /assets/demos/volcanoes-qgis-project.zip
credits: >-
  Volcano attributes from the Smithsonian Global Volcanism Program;
  hazard rings and population estimates by the NIKA team. Basemap ©
  CARTO, © OpenStreetMap contributors.
recipe:
  - setting: Map tab · Basemap
    value: Dark Matter
  - setting: Map tab · Extent
    value: Current canvas view (frame Java, or the whole arc)
---

## Run these yourself

The project download is a small zip with the `.qgz`, its data and a README
with each map's dialog settings. Open it in QGIS 3.44 or newer and export it
through *Web → QGIS2WebMap by NIKA* — you get the same file that is on
stage. The maps fetch basemap tiles at view time; the
[sharing guide](sharing.md) explains what an export contains.
