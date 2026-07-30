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
