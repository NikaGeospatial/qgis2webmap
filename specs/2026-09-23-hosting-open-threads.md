# Hosting + raster — open threads

Written 2026-09-23. Branch: `feat/hosting-and-raster`, 36 commits ahead of
`origin/main`, now pushed. The worker half lives in `nika-cf-workers` on
`feat/hosted-maps-arch`; its backlog is that repo's
`docs/hosted-maps-open-threads.md`.

## Known issues

1. **Relief cannot be published to hosting, deliberately.**
   `hosted_relief_reason()` in `packaging/dependency_scanner.py:190` refuses any
   hosted publish whose `terrain` is not `none`. Public elevation tiles stop at
   roughly 1:2.5 km and the surface BLANKS past them rather than magnifying, so
   every non-hosted tier ships `artifact_builder.terrain_zoom_clamp`, an inline
   `<script>` that glides the camera back. A hosted page is served under a CSP
   whose `script-src` names only the pinned runtime, so that script is refused
   twice — publish-time conformance rejects the artifact for carrying it, and a
   browser would refuse to run it.

   Re-checked against runtime 0.8.4 on 2026-09-22 and the refusal still holds.
   `<om-map>` exposes a `min-zoom` FLOOR and no ceiling; `terrain-max-zoom` is
   the DEM provider's tile cap, which changes which tiles are requested rather
   than stopping the camera. **The unblock is one upstream addition: `max-zoom`
   on `<om-map>`,** mirroring the `min-zoom` that already exists. Backward
   compatible, and it would let relief publish to hosting with no script. We do
   not edit OnlyMap ourselves — this needs a request drafted for the user to
   forward.

2. **OSM tiles on hosted maps are a standing structural risk with no code fix.**
   Hosted maps are served `Referrer-Policy: no-referrer` on purpose (a map's id
   is its subdomain), and OSM's tile policy wants a valid Referer plus an
   app-identifying User-Agent, which a browser page cannot send.
   `hosting/consent.py` warns rather than refuses. If OSM goes blank on a hosted
   map, that is the known risk, not a regression.

   Note: the "Access blocked" OSM tiles seen on 2026-09-21 were NOT this. They
   were our own probing rate-limiting the developer's IP. OSM was never broken.

3. **No built plugin zip exists anywhere in the repo**, so any installed copy
   predates the GIBS relief fix. Build one before testing relief by hand.

4. **Relief maps now carry no roads, borders or labels.** ASTER is coloured
   shaded relief. This is the accepted cost of leaving CARTO; every labelled
   alternative tested was either licence-restricted (OpenTopoMap and osm.de are
   non-commercial, EOX needs a paid licence) or keyless-but-unsanctioned (Esri's
   `World_Topo_Map` is served from a legacy endpoint their terms say needs an
   account — the same shape as the thing that just broke).

## Two stale comments, left untouched pending permission

The standing rule is never to edit a comment without asking. Both are wrong now:

- the `liberty`/`bright` branch of `terrain_note` still says "choose ... to see
  the map on the mountains", which is no longer what the NASA drape shows;
- `hosting/consent.py:114` still says "Measured 2026-09-18 and it is NOT
  currently blocked".

## Verification state at the last commit

1002 unit tests pass, 13 skipped; `ruff check` and `ruff format --check` clean;
the GIBS change was mutation-tested. `mypy nika_onlymap_exporter` reports 25
pre-existing errors in 7 files — all in QGIS-facing modules (`plugin.py`,
`ui/main_dialog.py`, `hosting/client.py`, `core/labeling_translator.py`,
`core/project_reader.py`, `exporters/hosted.py`,
`packaging/publish_manifest.py`) and none in anything this branch touched. They
come from PyQGIS stubs being absent outside a QGIS interpreter.

Headless test recipe is in `CONTRIBUTING.md` under the NixOS note; the unit tier
needs nothing but pytest.
