# Contributing

## Ground rules

1. **The plugin must never terminate QGIS.** No `os._exit`, `sys.exit`, or
   `QApplication.quit` on any path. Failures degrade to a message plus a log
   entry.
2. **No dependency installers.** The plugin must not shell out to a package
   manager. Everything it needs ships with it.
3. **Never write a knowingly broken artifact.** If an export cannot be produced
   correctly, fail with an actionable message and record it in the fidelity
   report.
4. **Lossless by default.** Compression may shrink bytes; it must not discard
   data. Lossy transforms are opt-in and reported.
5. **Exports make no network requests.** `telemetry="off"`, no `map-id`, no
   remote basemap unless the user explicitly chooses one.

## Architecture

Keep QGIS interpretation, manifest generation and packaging separate — the
normalized export model in `core/export_ir.py` is the boundary. Do not create
per-renderer string-generation modules; see
[`docs/architecture.md`](docs/architecture.md).

## Test tiers

| Tier | Needs | Runs in CI |
|---|---|---|
| `tests/unit` | nothing | yes, every push |
| `tests/qgis` | PyQGIS | no - skips cleanly where QGIS is absent |
| `tests/browser` | a browser | not yet wired |

`tests/qgis` needs QGIS's own Python on `PYTHONPATH`, because PyQGIS is not
pip-installable. On most systems the QGIS Python Console reports the right paths;
on NixOS, read them out of the `qgis` wrapper script:

```bash
QGIS_BIN=$(readlink -f "$(which qgis)")
QGIS_PY=$(grep -o "PYTHONPATH='[^']*'" "$QGIS_BIN" | head -1 | sed "s/PYTHONPATH='//;s/'$//")
QGIS_SHARE=$(dirname "$(dirname "$QGIS_BIN")")/share/qgis/python

PYTHONPATH="$QGIS_SHARE:$QGIS_PY:$PWD" QT_QPA_PLATFORM=offscreen \
  python -m pytest tests/qgis
```

`QT_QPA_PLATFORM=offscreen` keeps it headless. The tier uses in-memory layers
rather than files on disk, so it needs no fixture data and runs identically
everywhere.

## Before opening a PR

```bash
ruff check . && ruff format --check .
python -m pytest tests/unit
python -m pytest tests/qgis          # if you have QGIS - see above
python scripts/package_plugin.py
python scripts/verify_package.py dist/nika_onlymap_exporter-*.zip
```

Commit messages: concise, imperative one-liners.
