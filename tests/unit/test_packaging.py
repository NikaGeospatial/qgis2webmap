"""Packaging: compression, dependency scanning, and the three exporters.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import gzip
import zipfile

import pytest

from nika_onlymap_exporter.core.export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    OutputMode,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.artifact_builder import build_artifact
from nika_onlymap_exporter.packaging.asset_embedder import (
    DATA_COMPRESSION_THRESHOLD_BYTES,
    GZIP_SCRIPT_TYPE,
    build_bootstrap,
    gzip_base64,
    should_compress_data,
)
from nika_onlymap_exporter.packaging.dependency_scanner import (
    measure_data_bytes,
    scan,
    standalone_ineligible_reason,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle, sha256_of
from nika_onlymap_exporter.writers.onlymap_writer import (
    ExportBlockedError,
    OnlyMapWriter,
)

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"name": "a"},
        }
    ],
}


class FakeRuntime:
    def load(self) -> RuntimeBundle:
        js = b"globalThis.__onlymap_loaded = true;" * 200
        return RuntimeBundle(
            javascript=js,
            css=b"om-map:not(:defined) om-fallback { display: block; }",
            version="0.0.0-test",
            sha256=sha256_of(js),
        )


def make_layer(**overrides) -> ExportLayer:
    defaults = dict(
        layer_id="pts",
        name="Points",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=1,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=255, g=0, b=0)),
        ),
    )
    defaults.update(overrides)
    return ExportLayer(**defaults)


def make_project(layers=None, **overrides) -> ExportProject:
    defaults = dict(
        title="Test map",
        layers=tuple([make_layer()] if layers is None else layers),
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
    )
    defaults.update(overrides)
    return ExportProject(**defaults)


class TestCompression:
    def test_round_trips_losslessly(self) -> None:
        import base64

        original = b'{"type":"FeatureCollection"}' * 100
        restored = gzip.decompress(base64.b64decode(gzip_base64(original)))
        assert restored == original

    def test_compression_beats_base64_overhead(self) -> None:
        """Base64 adds a third; gzip has to save more than that to be worth it."""
        repetitive = b'{"key":"value"}' * 500
        assert len(gzip_base64(repetitive)) < len(repetitive)

    def test_compression_is_reproducible(self) -> None:
        """Regression: the gzip header defaults its mtime to *now*.

        Two exports of one project differed by four bytes inside a base64 blob,
        which broke byte-for-byte comparison of artifacts and wrote the export
        time into a file that is supposed to carry nothing incidental.
        """
        data = b'{"type":"FeatureCollection"}' * 100
        assert gzip_base64(data) == gzip_base64(data)

    def test_no_timestamp_is_embedded(self) -> None:
        """Bytes 4-8 of a gzip stream are the modified time. They must be zero."""
        import base64

        raw = base64.b64decode(gzip_base64(b"x" * 100))
        assert raw[4:8] == b"\x00\x00\x00\x00"

    def test_small_data_stays_readable(self) -> None:
        assert should_compress_data(1024) is False

    def test_large_data_is_compressed(self) -> None:
        assert should_compress_data(DATA_COMPRESSION_THRESHOLD_BYTES + 1) is True

    def test_bootstrap_inflates_data_before_importing_the_runtime(self) -> None:
        """Order is the whole trick: importing defines the elements, which
        immediately upgrade every layer and read its inline data."""
        script = build_bootstrap("QUJD")
        data_step = script.index(GZIP_SCRIPT_TYPE)
        import_step = script.index("await import(runtimeUrl)")
        assert data_step < import_step

    def test_bootstrap_releases_the_blob_url(self) -> None:
        assert "revokeObjectURL" in build_bootstrap("QUJD")


class TestDependencyScanner:
    def test_measures_serialised_data(self) -> None:
        assert measure_data_bytes(make_project()) > 0

    def test_empty_project_is_blocking(self) -> None:
        result = scan(make_project(layers=[]), FidelityReportBuilder())
        assert result.can_export is False
        assert any("no layers" in r for r in result.blocking_reasons)

    def test_remote_dependency_is_reported_not_blocking(self) -> None:
        layer = make_layer(
            dependencies=(
                AssetDependency(
                    identifier="https://example.com/wms",
                    disposition=AssetDisposition.REMOTE,
                ),
            )
        )
        report = FidelityReportBuilder()
        result = scan(make_project([layer]), report)
        assert result.can_export is True
        assert result.is_offline is False
        assert any("internet connection" in i.detail for i in report.items)

    def test_blocking_dependency_stops_the_export(self) -> None:
        layer = make_layer(
            dependencies=(
                AssetDependency(
                    identifier="missing.svg",
                    disposition=AssetDisposition.BLOCKING,
                    note="missing.svg could not be found.",
                ),
            )
        )
        result = scan(make_project([layer]), FidelityReportBuilder())
        assert result.can_export is False

    def test_credentials_are_noted_but_never_captured(self) -> None:
        layer = make_layer(
            dependencies=(
                AssetDependency(
                    identifier="postgres:roads",
                    disposition=AssetDisposition.EMBEDDABLE,
                    credentials_detected=True,
                ),
            )
        )
        report = FidelityReportBuilder()
        result = scan(make_project([layer]), report)
        assert result.credentials_detected == ("Points",)
        assert any("needs no credentials" in i.detail for i in report.items)


class TestStandaloneEligibility:
    """Issue #29: the single file is the default only when it can travel."""

    def test_an_ordinary_project_is_eligible(self) -> None:
        assert standalone_ineligible_reason(make_project()) is None

    def test_an_oversized_project_says_why_and_names_the_size(self) -> None:
        big = dict(GEOJSON)
        big["features"] = GEOJSON["features"] * 300_000
        reason = standalone_ineligible_reason(make_project([make_layer(geojson=big)]))

        assert reason is not None
        assert "MB" in reason
        assert "Share ZIP" in reason


class TestWriterCompression:
    def test_compressed_artifact_is_much_smaller(self, tmp_path) -> None:
        writer = OnlyMapWriter(runtime_provider=FakeRuntime())
        plain = writer.write(make_project(), tmp_path / "plain", compress=False)
        packed = writer.write(make_project(), tmp_path / "packed", compress=True)
        assert packed.total_bytes < plain.total_bytes
        assert packed.compressed is True

    def test_stylesheet_is_never_compressed(self, tmp_path) -> None:
        """The fallback's CSS gate has to work before any script runs."""
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, compress=True
        )
        assert "om-map:not(:defined)" in result.entry_path.read_text()

    def test_blocked_export_raises_before_writing(self, tmp_path) -> None:
        destination = tmp_path / "out"
        with pytest.raises(ExportBlockedError):
            OnlyMapWriter(runtime_provider=FakeRuntime()).write(
                make_project(layers=[]), destination
            )
        assert not (destination / "index.html").exists()


class TestExporters:
    def _writer(self) -> OnlyMapWriter:
        return OnlyMapWriter(runtime_provider=FakeRuntime())

    def test_standalone_produces_one_file(self, tmp_path) -> None:
        _, outcome = build_artifact(
            make_project(), tmp_path / "map.html", writer=self._writer()
        )
        assert outcome.path.suffix == ".html"
        assert outcome.path.is_file()
        assert "Double-click" in outcome.open_instruction

    def test_standalone_corrects_a_wrong_suffix(self, tmp_path) -> None:
        _, outcome = build_artifact(
            make_project(), tmp_path / "map.txt", writer=self._writer()
        )
        assert outcome.path.suffix == ".html"

    def test_zip_nests_everything_in_one_folder(self, tmp_path) -> None:
        """Extracting must not scatter files across the recipient's Downloads."""
        _, outcome = build_artifact(
            make_project(),
            tmp_path / "map.zip",
            mode=OutputMode.SHARE_ZIP,
            writer=self._writer(),
        )
        with zipfile.ZipFile(outcome.path) as archive:
            names = archive.namelist()
        assert all(n.startswith("map/") for n in names), names
        assert "map/index.html" in names

    def test_zip_and_folder_carry_a_readme(self, tmp_path) -> None:
        _, outcome = build_artifact(
            make_project(),
            tmp_path / "map.zip",
            mode=OutputMode.SHARE_ZIP,
            writer=self._writer(),
        )
        with zipfile.ZipFile(outcome.path) as archive:
            readme = archive.read("map/README.txt").decode()
        assert "HOW TO OPEN IT" in readme
        assert "sends one anonymous usage report to NIKA" in readme

    def test_a_title_that_looks_like_a_token_is_not_substituted_twice(
        self, tmp_path
    ) -> None:
        """The README builder gets the same single-pass rule as the HTML writer.

        Replacing tokens in a loop rescans content already inserted, so a
        project titled "@GENERATOR@" had the provenance line pasted into its own
        heading. The title must survive verbatim.
        """
        _, outcome = build_artifact(
            make_project(title="@GENERATOR@"),
            tmp_path / "mapdir",
            mode=OutputMode.FOLDER,
            writer=self._writer(),
        )
        readme = (outcome.path / "README.txt").read_text()
        assert "@GENERATOR@" in readme
        assert readme.count("OnlyMap runtime") == 1

    def test_folder_export_contains_the_entry_file(self, tmp_path) -> None:
        _, outcome = build_artifact(
            make_project(),
            tmp_path / "mapdir",
            mode=OutputMode.FOLDER,
            writer=self._writer(),
        )
        assert (outcome.path / "index.html").is_file()
        assert (outcome.path / "README.txt").is_file()

    def test_failure_leaves_nothing_behind(self, tmp_path) -> None:
        destination = tmp_path / "map.html"
        with pytest.raises(ExportBlockedError):
            build_artifact(make_project(layers=[]), destination, writer=self._writer())
        assert not destination.exists()
