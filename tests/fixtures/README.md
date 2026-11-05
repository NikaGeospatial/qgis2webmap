# Fixture projects

Issue #29's Task 7 acceptance is *"all supported fixture projects pass package,
QGIS, browser, portability, privacy, and fidelity checks."* These are those
projects.

## Why they are built, not committed

A `.qgz` is a zip of XML holding absolute paths to its data. Committing one
means committing paths from whichever machine saved it, and the first person to
open it on another OS gets broken layers. So each fixture is a **script** that
builds the project through PyQGIS, and the checks run against what it builds.

That also keeps the repository free of binary blobs that no reviewer can read in
a diff — the same reason `mydatabase.db` and `symbology-style.db` were removed
from the repository root.

## Running them

They need PyQGIS, so they live behind the same gate as `tests/qgis`:

```bash
docker run --rm -v "$PWD":/work -w /work \
  -e QT_QPA_PLATFORM=offscreen -e PYTHONPATH=/work \
  -e ONLYMAP_RUNTIME_DIR=/runtime \
  -v /path/to/@nika-js/onlymap/dist:/runtime:ro \
  qgis/qgis:4.2 python3 -m pytest tests/fixtures
```

Exporting a real artifact needs the OnlyMap runtime, so without
`ONLYMAP_RUNTIME_DIR` the export checks skip and only the model-level ones run.

## The set

| Fixture | Exercises |
|---|---|
| `points_categorized` | Point layer, categorized renderer, labels, popups, attribution |
| `lines_graduated` | Line layer, graduated renderer, scale-range visibility |
| `polygons_grouped` | Polygon layer in a group, hidden fields, field aliases |
| `mixed_crs` | Two layers in different projections, both reprojected to WGS84 |
| `antimeridian` | Data crossing 180°, the case a naive bounding box ruins |

Add a fixture by adding a builder to `conftest.py` and a row here. Keep each one
narrow: a fixture that exercises everything tells you nothing about what broke.

## Recorded service responses

`swisstopo-lightbasemap-style.json` is swisstopo's light base map style
(`https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/style.json`,
v1.26.0, fetched once on 2026-11-04), a government vector tile style with
expressions, filters, zoom functions and labels. `tests/qgis/test_vector_tile_layers.py`
serves it locally, its addresses moved to the test's own server, to check that a
layer QGIS styled from it reads as unchanged. Style © swisstopo.

`map_services/` holds government WMS and WMTS capabilities, each fetched once
on 2026-11-05 and cut down to the layer (and tile matrix set) a case uses -
other layers, themes, keywords and INSPIRE extensions removed, everything
else as served. `tests/qgis/test_recorded_map_services.py` puts each in QGIS's
network cache under its real address and checks the export it gives, so the
real services are replayed with no network. Sources:

- `pdok-luchtfoto-wms.xml` - PDOK aerial photos, `https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0` (MapServer)
- `nasa-gibs-wms.xml` - NASA GIBS, `https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi` (WMS-T; served `Cache-Control: no-store`)
- `noaa-nowcoast-wms.xml` - NOAA nowCOAST, `https://nowcoast.noaa.gov/geoserver/observations/satellite/ows` (GeoServer)
- `usgs-topo-wms.xml` - USGS National Map, `https://basemap.nationalmap.gov/arcgis/services/USGSTopo/MapServer/WMSServer` (ArcGIS Server)
- `solothurn-wms.xml` - Kanton Solothurn, `https://geo.so.ch/api/wms` (QGIS Server)
- `bgs-geology-wms-111.xml` - British Geological Survey, `https://ogc.bgs.ac.uk/cgi-bin/BGS_Bedrock_and_Superficial_Geology/wms`, asked `VERSION=1.1.1` (MapServer; its addresses on `http://`)
- `nrw-dtk-wms.xml` - Geobasis NRW, `https://www.wms.nrw.de/geobasis/wms_nw_dtk` (MapServer)
- `swisstopo-2056-wmts.xml` - swisstopo, `https://wmts.geo.admin.ch/EPSG/2056/1.0.0/WMTSCapabilities.xml`
- `ign-geoplateforme-wmts.xml` - IGN Géoplateforme, `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetCapabilities` (3 MB served)
- `basemapde-wmts.xml` - BKG basemap.de, `https://sgx.geodatenzentrum.de/wmts_basemapde/1.0.0/WMTSCapabilities.xml`

The PDOK WMTS case reads `pdok-brt-wmts-capabilities.xml`, which the unit tests
read too.
Capabilities © their publishers.
