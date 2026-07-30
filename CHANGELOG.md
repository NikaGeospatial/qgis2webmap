# Changelog

All notable changes to QGIS2WebMap by NIKA. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository bootstrap: plugin lifecycle (one Web-menu action + toolbar icon),
  export-dialog shell with a populated Help tab, NIKA iconography,
  packaging and package-verification scripts, and CI.
- Normalized export model (`core/export_ir.py`) - frozen dataclasses with
  deterministic snapshots, holding no Qt or PyQGIS types.
- Antimeridian-aware extent computation (`core/extent_math.py`), so a project
  crossing the 180th meridian opens on its data rather than on the whole world.
- Fidelity report covering suppressed settings as well as unsupported symbology.
- OnlyMap licence-cap detection, with enforcement behind a swappable policy.
- Project and layer readers translating single, categorized and graduated
  renderers, labels, popups and groups, and normalising all vector data to
  WGS84 GeoJSON.
- Marker shapes are captured rather than flattened to circles.
- OnlyMap manifest builder emitting declarative markup, using the canonical
  expression shapes so the legend renders categories and class ranges without a
  hand-built legend.
- `OnlyMapWriter` producing a self-contained `index.html` with the runtime, the
  styles and the data inlined - no network requests, no tracking - plus an
  `ArtifactResult` recording sizes, runtime provenance and a deterministic
  manifest snapshot.
- Lossless gzip packaging: the runtime is embedded compressed and inflated in the
  browser, taking a 5.45 MB artifact to 1.84 MB. The stylesheet is never
  compressed, because the no-JavaScript fallback depends on a pure-CSS rule.
- Dependency scanning before any bytes are written, so an export that cannot work
  for its recipient fails while there is still time to fix it.
- Three output tiers - Standalone HTML, Share ZIP and folder - with a README for
  the recipient in the tiers that need one.
- Export dialog with Map, Layers, Appearance, Fidelity and Help tabs. The map
  name is set in one place, widgets are on by default, and Export is disabled
  with the reason beside it rather than producing a broken artifact.
- The layer list follows the QGIS Layers panel live. Bursts of signals coalesce
  into one rebuild, and per-layer settings survive it because they are keyed by
  layer id rather than held in widgets - so there is no refresh button.
- Settings persist in the QGIS project, so reopening it restores export choices.
- Preview runs through the production writer to a stable path per project and
  opens in the user's default browser, remembering the camera across reloads.
- User guides for installation, first export, sharing, AI enhancement, supported
  features and privacy. The plugin's Help tab renders the same files the website
  serves, so the two cannot drift.
- GitHub Pages workflow serving `docs/`.
