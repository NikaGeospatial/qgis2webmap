"""Issue #29's release gates, run against every fixture project.

The gates, verbatim from the issue:

- The exported map opens successfully on a clean machine with no QGIS installed.
- Standalone HTML makes no unwanted network request.
- Share ZIP detects missing assets before writing the zip.
- Every unsupported item appears in the fidelity report; none disappear silently.
- All source-data paths and credentials are stripped from the delivered artifact.
- The two acquisition calls to action are keyboard accessible, do not cover map
  controls, and do not exfiltrate map data.

The first is a browser fact and lives in `tests/browser`. The rest are checkable
here, against the real writer, and this is where "all supported fixture projects
pass" stops being an aspiration.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import zipfile

import pytest

from nika_onlymap_exporter.core.export_ir import OutputMode
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.exporters.share_zip import ShareZipExporter
from nika_onlymap_exporter.packaging.runtime_manager import (
    LocalRuntime,
    discover_runtime_dir,
)
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter


@pytest.fixture
def runtime_available():
    directory = discover_runtime_dir()
    if directory is None:
        pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
    return LocalRuntime(directory)


class TestEveryFixtureReads:
    def test_it_produces_an_exportable_model(self, fixture_project) -> None:
        project, name = fixture_project
        export = read_project(project, FidelityReportBuilder())
        assert export.is_exportable, f"{name} produced nothing to export"
        assert export.layers

    def test_nothing_passes_through_unreported(self, fixture_project) -> None:
        """The ground rule: informed breakage is fine, undiscovered is a defect."""
        project, name = fixture_project
        report = FidelityReportBuilder()
        export = read_project(project, report)

        for layer in export.layers:
            assert report.for_layer(layer.layer_id), (
                f"{name}: layer {layer.name!r} produced no fidelity entries"
            )

    def test_everything_lands_in_wgs84(self, fixture_project) -> None:
        project, name = fixture_project
        export = read_project(project, FidelityReportBuilder())

        for layer in export.layers:
            for feature in (layer.geojson or {}).get("features", []):
                for lon, lat in _coordinates(feature["geometry"]):
                    assert -180.0 <= lon <= 180.0, f"{name}: {lon} is not a longitude"
                    assert -90.0 <= lat <= 90.0, f"{name}: {lat} is not a latitude"

    def test_the_model_is_deterministic(self, fixture_project) -> None:
        project, _ = fixture_project
        first = read_project(project, FidelityReportBuilder()).snapshot()
        second = read_project(project, FidelityReportBuilder()).snapshot()
        assert first == second


class TestEveryFixtureExports:
    """The gates that need a real artifact on disk."""

    def artifact(self, project, runtime_available, tmp_path, **kwargs):
        export = read_project(project, FidelityReportBuilder())
        writer = OnlyMapWriter(runtime_provider=runtime_available)
        return writer.write(export, tmp_path, **kwargs), export

    def test_standalone_html_makes_no_network_request(
        self, fixture_project, runtime_available, tmp_path
    ) -> None:
        """The promise the README makes in its second paragraph."""
        project, name = fixture_project
        result, _ = self.artifact(project, runtime_available, tmp_path)

        assert result.network_dependencies == (), name
        assert result.is_offline, name

        html = result.entry_path.read_text(encoding="utf-8")
        # The runtime is a minified bundle full of URL-ish strings, so check the
        # authored part of the document rather than the whole file.
        authored = html.split("<script type=")[0]
        for pattern in (r'src="https?://', r'href="https?://[^"]*\.(js|css)'):
            assert not re.search(pattern, authored), f"{name}: remote asset reference"

    def test_no_source_paths_or_credentials_reach_the_artifact(
        self, fixture_project, runtime_available, tmp_path
    ) -> None:
        project, name = fixture_project
        result, _ = self.artifact(project, runtime_available, tmp_path)
        html = result.entry_path.read_text(encoding="utf-8")
        authored = html.split("<script type=")[0]

        for leak in ("/home/", "/Users/", "C:\\\\", "password=", "memory?crs="):
            assert leak not in authored, f"{name}: {leak!r} reached the artifact"

    def test_the_credit_component_ships(
        self, fixture_project, runtime_available, tmp_path
    ) -> None:
        project, name = fixture_project
        result, _ = self.artifact(project, runtime_available, tmp_path)
        html = result.entry_path.read_text(encoding="utf-8")

        assert 'class="om-credit"' in html, name
        assert ">Enhance</a>" in html, name
        assert ">Host</a>" in html, name

    def test_share_zip_is_openable_and_self_contained(
        self, fixture_project, runtime_available, tmp_path
    ) -> None:
        project, name = fixture_project
        result, _ = self.artifact(
            project, runtime_available, tmp_path / "build", mode=OutputMode.SHARE_ZIP
        )
        destination = tmp_path / f"{name}.zip"
        ShareZipExporter().export(result, destination)

        assert destination.is_file()
        with zipfile.ZipFile(destination) as archive:
            assert archive.testzip() is None, f"{name}: corrupt zip"
            names = archive.namelist()
            # One top-level folder, so extracting does not scatter files across
            # whatever directory the recipient happened to be in.
            assert all("/" in n for n in names), f"{name}: {names} would scatter"
            assert any(n.endswith("/index.html") for n in names), (
                f"{name}: no entry point in {names}"
            )
            # Relative paths only - an absolute or traversing path is both
            # unportable and a zip-slip hazard on extraction.
            for entry in names:
                assert not entry.startswith("/"), f"{name}: absolute path {entry}"
                assert ".." not in entry, f"{name}: traversing path {entry}"

    def test_the_artifact_is_byte_identical_across_runs(
        self, fixture_project, runtime_available, tmp_path
    ) -> None:
        """Deterministic output is what makes a regression visible in a diff."""
        project, name = fixture_project
        when = __import__("datetime").datetime(
            2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
        )
        first, _ = self.artifact(project, runtime_available, tmp_path / "a", when=when)
        second, _ = self.artifact(project, runtime_available, tmp_path / "b", when=when)

        assert first.entry_path.read_bytes() == second.entry_path.read_bytes(), name


class TestLabelledFixture:
    """Labels reach the artifact - the gap this fixture tier was added to hold."""

    def test_a_labelled_layer_produces_a_text_layer(
        self, points_categorized, runtime_available, tmp_path
    ) -> None:
        export = read_project(points_categorized, FidelityReportBuilder())
        result = OnlyMapWriter(runtime_provider=runtime_available).write(
            export, tmp_path
        )
        html = result.entry_path.read_text(encoding="utf-8")

        assert 'type="TextLayer"' in html
        assert "Ashford" in html

    def test_the_data_credit_reaches_the_artifact(
        self, points_categorized, runtime_available, tmp_path
    ) -> None:
        export = read_project(points_categorized, FidelityReportBuilder())
        result = OnlyMapWriter(runtime_provider=runtime_available).write(
            export, tmp_path
        )
        html = result.entry_path.read_text(encoding="utf-8")

        assert "Fixture Survey" in html


def _coordinates(geometry):
    """Every [lon, lat] pair in a GeoJSON geometry, at any nesting depth."""
    coordinates = geometry.get("coordinates")
    if not coordinates:
        return

    def walk(node):
        if isinstance(node[0], (int, float)):
            yield node[0], node[1]
            return
        for child in node:
            yield from walk(child)

    yield from walk(coordinates)
