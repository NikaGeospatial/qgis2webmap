# Contributing

## Ground rules

1. **The plugin must never terminate QGIS.** No `os._exit`, `sys.exit`, or
   `QApplication.quit` on any path. Failures degrade to a message plus a log
   entry.
2. **No dependency installers.** The plugin must not shell out to a package
   manager, and must not install Python packages, system libraries or anything
   else onto the user's machine.

   **One deliberate exception: the OnlyMap runtime.** It cannot ship with the
   plugin. QGIS requires that "all code included in any plugin should be made
   clearly and easily available in source form" and refuses plugins carrying
   binaries; the runtime is a 5.7 MB minified build of a closed-source library,
   so putting it in the zip would ask plugins.qgis.org to redistribute
   unreviewable proprietary code. It is therefore downloaded once per machine,
   under these conditions:

   - Its licence is shown and accepted **before** anything is downloaded. The
     licence text ships with the plugin, because text is reviewable.
   - The download is verified against `runtime/runtime-lock.json` before it is
     cached, so a substituted build never becomes what later exports read.
   - It goes through QGIS's own network stack, so a user's configured proxy
     applies.
   - Nothing else may use this exception. A new dependency is a design problem,
     not a download.
3. **Never write a *silently* broken artifact.** Anything that will not survive
   the export must appear in the fidelity report before the user exports, and
   the artifact itself must explain any gap to whoever opens it. Informed
   breakage is the user's decision; undiscovered breakage is a defect. Only a
   genuinely unrecoverable export fails outright.
4. **Lossless by default.** Compression may shrink bytes; it must not discard
   data. Lossy transforms are opt-in and reported.
5. **Exports make no network requests.** `telemetry="off"`, no `map-id`, no
   remote basemap unless the user explicitly chooses one. This is about the
   *exported map*, and it is absolute: an artifact must work with the cable
   unplugged. Exporting is also local — the plugin's only network request in
   its whole life is the one-time runtime download in rule 2.

## Architecture

Keep QGIS interpretation, manifest generation and packaging separate — the
normalized export model in `core/export_ir.py` is the boundary. Do not create
per-renderer string-generation modules; see
[`docs/architecture.md`](docs/architecture.md).

## Test tiers

| Tier | Needs | Runs in CI |
|---|---|---|
| `tests/unit` | nothing | yes, every push |
| `tests/qgis` | PyQGIS | yes, in the `qgis/qgis:ltr` container |
| `tests/fixtures` | PyQGIS + the OnlyMap runtime | yes, where the runtime is available |
| `tests/browser` | Playwright + the OnlyMap runtime | yes, on chromium/firefox/webkit |

Every tier skips cleanly when its dependency is absent, so a partial local setup
gives a green run rather than a wall of errors. **Read the skip count**: a green
run that skipped the tier you changed has not tested your change.

`tests/qgis` needs QGIS's own Python, because PyQGIS is not pip-installable.
`QT_QPA_PLATFORM=offscreen` keeps it headless. The tier uses in-memory layers
rather than files on disk, so it needs no fixture data and runs identically
everywhere.

**Docker — works on macOS, Windows and Linux alike, and needs no local QGIS:**

```bash
docker run --rm -v "$PWD":/work -w /work \
  -e QT_QPA_PLATFORM=offscreen -e PYTHONPATH=/work \
  qgis/qgis:ltr python3 -m pytest tests/qgis
```

**With QGIS installed locally**, run against its bundled interpreter:

| Platform | Command |
|---|---|
| macOS | `PYTHONPATH=$PWD QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/bin/python3 -m pytest tests/qgis` |
| Windows | `set PYTHONPATH=%CD%` then `"C:\Program Files\QGIS 3.44\bin\python-qgis-ltr.bat" -m pytest tests/qgis` |
| Linux (distro package) | `PYTHONPATH=$PWD QT_QPA_PLATFORM=offscreen python3 -m pytest tests/qgis` |

### Qt6 / QGIS 4

`qgis/qgis:ltr` is a **Qt5** build, so the container tier alone never exercises
Qt6. QGIS 4.0 moved the application to Qt6 and plugins.qgis.org runs an
automatic Qt6 check on upload, so a Qt6 run is worth doing before a release:

```bash
docker run --rm -v "$PWD":/work -w /work \
  -e QT_QPA_PLATFORM=offscreen -e PYTHONPATH=/work \
  qgis/qgis:latest python3 -m pytest tests/qgis
```

**Verified green on QGIS 4.0.3 "Norrköping" / Qt 6.11.1 / Python 3.14 on
2026-08-12**: 486 unit, 266 qgis+fixtures, all passing, including the
end-to-end Processing export that writes a real HTML artifact. The plugin's
39 modules all import and the export dialog constructs under PyQt6.

Two things that look like failures on a bare PyQGIS environment but are not:

- **A null `QIcon` from an SVG.** If Qt's SVG image-format plugin is not on the
  plugin path, `QImageReader.supportedImageFormats()` has no `svg` entry and
  every SVG icon loads null. Real QGIS ships it; check the format list before
  believing an icon bug.
- **Phantom schema failures on the unit tier** if `ONLYMAP_RUNTIME_DIR` is
  unset - see the runtime section below.

The tier skips cleanly wherever PyQGIS is absent, so a contributor without QGIS
still gets a green local run rather than a wall of import errors.

> **NixOS note.** `qgis` is a wrapper script, so PyQGIS is not on the default
> `PYTHONPATH` and the store path changes on every rebuild. Read it back out of
> the wrapper:
>
> ```bash
> QGIS_BIN=$(readlink -f "$(which qgis)")
> QGIS_PY=$(grep -o "PYTHONPATH='[^']*'" "$QGIS_BIN" | head -1 | sed "s/PYTHONPATH='//;s/'$//")
> QGIS_SHARE=$(dirname "$(dirname "$QGIS_BIN")")/share/qgis/python
> PYTHONPATH="$QGIS_SHARE:$QGIS_PY:$PWD" QT_QPA_PLATFORM=offscreen \
>   python -m pytest tests/qgis
> ```
>
> This is a local convenience only. **Nothing in the plugin may depend on Nix**
> — it ships to macOS, Windows and ordinary Linux, where paths look nothing
> like a Nix store. Absolute developer paths do not belong in shipped code; see
> `discover_runtime_dir` in `packaging/runtime_manager.py` for the intended
> shape (env override, then paths relative to the package).

## Running the tests that need the OnlyMap runtime

The runtime is not vendored yet (see [`docs/architecture.md`](docs/architecture.md)).
Point the writer and packaging tests at a build to run them:

```bash
export ONLYMAP_RUNTIME_DIR=/path/to/@nika-js/onlymap/dist
```

Without it those tests **skip** rather than fail. If you are changing the writer,
check the summary for skips — a green run that skipped the artifact tests has not
tested your change.

The attribute-contract test in `tests/unit/test_manifest_builder.py` checks every
attribute we emit against the runtime's own `onlymapjs.html-data.json`. Point it
at a copy with `ONLYMAP_HTML_DATA` if it is not beside your runtime.

## Running the browser tier

Needs Playwright and the OnlyMap runtime, but **not** QGIS — it builds its
project through the normalized model, so it tests what a recipient receives.

```bash
pip install pytest-playwright && playwright install
ONLYMAP_RUNTIME_DIR=/path/to/dist \
  pytest tests/browser --browser chromium --browser firefox --browser webkit
```

Chrome and Edge are both Chromium: `--browser chromium` covers the engine, and
`--browser-channel msedge` is for the branded run in the release matrix.

Tests that need a rendered map skip where the browser cannot create a WebGL2
context. Headless Firefox has no bundled software renderer (Chromium has
SwiftShader), so on a machine with no GPU it skips one check rather than
reporting a failure the export did not cause.

> **NixOS note.** Playwright's downloaded browsers will not run — they are not
> patched for the Nix dynamic linker, and fail with exit code 127. Use the
> browsers from nixpkgs instead:
>
> ```bash
> export PLAYWRIGHT_BROWSERS_PATH=$(nix build --no-link --print-out-paths nixpkgs#playwright-driver.browsers)
> ```
>
> The nixpkgs `playwright-driver` version must match the `playwright` Python
> package, or it will not find the browser it expects.

## Before opening a PR

```bash
ruff check . && ruff format --check .
python -m pytest tests/unit
python -m pytest tests/qgis          # if you have QGIS - see above
python scripts/package_plugin.py
python scripts/verify_package.py dist/qgis2webmap-*.zip
```

Commit messages: concise, imperative one-liners.
