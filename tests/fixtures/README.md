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

`feature_info/` holds what four government WMS servers of different software
answered a click (GetFeatureInfo), fetched once on 2026-11-05, each request a
101 x 101 pixel map around the point with I/J (X/Y) = 50, trimmed to what the
tests read (geometry coordinates shortened, capabilities cut to the request
section and the layers asked). `tests/unit/test_map_services.py` builds the
requests from the capabilities; `tests/browser/test_feature_info.py` reads
every answer and replays some through the local fake WMS.

- `geoserver-dwd-*`: GeoServer, Deutscher Wetterdienst
  (`https://maps.dwd.de/geoserver/dwd/ows`), layer `Warngebiete_Kreise`,
  WMS 1.3.0 in EPSG:4326 (latitude first). A hit on Magdeburg (11.62 E,
  52.13 N) as `application/json` and as GML 3.1.1, an empty answer at sea
  (4.0 E, 55.5 N) as JSON and as `text/html` (an empty page with a style),
  and the exception for an unknown QUERY_LAYERS, sent as `text/xml` to a
  JSON request. Sends `Access-Control-Allow-Origin: *`.
- `mapserver-pdok-*`: MapServer, PDOK / Kadaster
  (`https://service.pdok.nl/kadaster/brk-bestuurlijke-gebieden/wms/v1_0`),
  layer `Gemeentegebied`, WMS 1.3.0 in EPSG:3857. A hit on Amsterdam (4.9 E,
  52.37 N) as JSON and as `application/vnd.ogc.gml` (`msGMLOutput`), and an
  empty one in the North Sea (3.0 E, 53.5 N) as JSON and as `text/plain`,
  which still says something ("Search returned no results.").
- `arcgis-usgs-*`: ArcGIS Server, USGS National Map
  (`https://carto.nationalmap.gov/arcgis/services/govunits/MapServer/WMSServer`),
  layer `19` (County or Equivalent), WMS 1.1.1 in EPSG:4326 with X/Y. A hit
  on Denver (-104.99, 39.74) as `application/geo+json`, `text/xml` (fields as
  the attributes of `<FIELDS>`) and `text/html`, an empty one in the Pacific
  (-140, 30) as GeoJSON and HTML, and the 1.1.1 exception for QUERY_LAYERS=999
  (`application/vnd.ogc.se_xml`, with a DOCTYPE).
- `qgis-solothurn-*`: QGIS Server behind its own GetFeatureInfo address,
  Kanton Solothurn (`https://geo.so.ch/api/wms`, answering at
  `https://geo.so.ch/api/v1/featureinfo/somap`), layer
  `ch.so.alw.strukturverbesserungen.gemeinde_flurreglemente`, which offers only
  text/plain, text/html and text/xml, inside a group that is not queryable.
  A hit on Solothurn (7.537 E, 47.208 N, EPSG:3857) and an empty answer in
  Zurich (8.5 E, 47.37 N, CRS:84) as `text/xml` (`<Attribute name value>`).
