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

## Before opening a PR

```bash
ruff check . && ruff format --check .
python -m pytest tests/unit
python scripts/package_plugin.py
python scripts/verify_package.py dist/nika_onlymap_exporter-*.zip
```

Commit messages: concise, imperative one-liners.
