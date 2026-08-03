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
  qgis/qgis:ltr python3 -m pytest tests/fixtures
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
