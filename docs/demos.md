---
layout: demos
title: Demos
description: >-
  Three interactive web maps exported straight from QGIS with QGIS2WebMap —
  load each one live, then download the QGIS project it came from and export
  it yourself.
image: /assets/demos/himalayas-poster.png

hero:
  eyebrow: Demo gallery
  headline_before: "Three maps, "
  headline_em: "straight out of the dialog"
  headline_after: "."
  lede: >-
    Each map here is a single HTML file exported from QGIS with QGIS2WebMap —
    nothing was edited afterwards. Load one, click around, then download the
    QGIS project it came from and export it yourself.
  actions:
    - label: Install the plugin
      url: /installation.html
      style: primary
    - label: What gets exported
      url: /supported-features.html

demos:
  - id: himalayas
    eyebrow: Nepal Himalaya · graduated colour + 3D relief
    title: How high do climbers get?
    lede: >-
      Expedition records for 74 peaks, averaged per peak: the colour around
      each summit is how far up its climbers typically get, painted straight
      onto real terrain.
    tip: Ctrl + drag to tilt into 3D
    compare:
      viewer: /assets/demos/qgis-side.html
      note: >-
        Comparison view — the left pane is QGIS's own rendering of this
        project, pre-drawn to map tiles; the right pane is the live export.
        The panes share one camera, fenced to the pre-rendered area; the left
        stays flat, exactly as the QGIS canvas would, so with the relief
        tilted the summits lean toward you only on the right.
    alt: >-
      Mountainous 3D terrain with translucent heat-coloured patches draped
      over the massifs around Everest, a legend of climb-height classes in
      the corner.
    poster: /assets/demos/himalayas-poster.png
    map: /assets/demos/himalayas.html
    project: /assets/demos/himalayas-qgis-project.zip
    credits: >-
      Data: The Himalayan Database (himalayandatabase.com); peak coordinates
      © OpenStreetMap contributors, ODbL. Basemap and relief imagery © CARTO,
      © OpenStreetMap contributors.

  - id: singapore
    eyebrow: Singapore · categorized lines + markers
    title: The MRT network
    lede: >-
      The whole network on real track geometry, every line in its official
      colour, every station clickable — interchanges included, right up to
      the closed Circle Line loop.
    alt: >-
      A light map of Singapore with the six MRT lines in their official
      colours and white ring-shaped station markers along them.
    poster: /assets/demos/singapore-poster.png
    map: /assets/demos/singapore.html
    project: /assets/demos/singapore-qgis-project.zip
    credits: >-
      Data: LTA DataMall and data.gov.sg (Singapore Open Data Licence v1.0);
      line geometry © OpenStreetMap contributors, ODbL. Basemap © CARTO,
      © OpenStreetMap contributors.

  - id: volcanoes
    eyebrow: Indonesia · hazard rings + SVG markers
    title: Living next to a volcano
    lede: >-
      Fifty-one active volcanoes along the arc, each with hazard rings sized
      by the reach of its plausible eruptions — click a volcano for its
      eruptive history.
    alt: >-
      A dark map of the Indonesian archipelago with glowing red, orange and
      yellow hazard rings stacked along the volcanic arc.
    poster: /assets/demos/volcanoes-poster.png
    map: /assets/demos/volcanoes.html
    project: /assets/demos/volcanoes-qgis-project.zip
    credits: >-
      Volcano attributes from the Smithsonian Global Volcanism Program;
      hazard rings and population estimates by the NIKA team. Basemap ©
      CARTO, © OpenStreetMap contributors.
---

## Run these yourself

The project download is a small zip with the `.qgz`, its data and a README
with each map's dialog settings. Open it in QGIS 3.44 or newer and export it
through *Web → QGIS2WebMap by NIKA* — you get the same file that is on
stage. The maps fetch basemap tiles at view time; the
[sharing guide](sharing.md) explains what an export contains.
