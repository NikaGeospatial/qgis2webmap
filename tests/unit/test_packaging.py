"""Packaging: compression, dependency scanning, and the three exporters.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "qgis.PyQt",
    reason="the plugin's export and preview modules import Qt through ui.i18n; "
    "this file runs in the QGIS CI job (and any QGIS Python)",
)

from nika_onlymap_exporter.core.cancellation import ExportCancelledError
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
from nika_onlymap_exporter.core.portable_names import avoid_windows_reserved
from nika_onlymap_exporter.exporters.folder import FolderExporter
from nika_onlymap_exporter.exporters.share_zip import ShareZipExporter, archive_root
from nika_onlymap_exporter.packaging.artifact_builder import build_artifact
from nika_onlymap_exporter.packaging.asset_embedder import (
    DATA_COMPRESSION_THRESHOLD_BYTES,
    GZIP_SCRIPT_TYPE,
    _CompressionCache,
    build_bootstrap,
    gzip_base64,
    should_compress_data,
)
from nika_onlymap_exporter.packaging.dependency_scanner import (
    data_bytes_by_layer,
    measure_data_bytes,
    scan,
    standalone_ineligible_reason,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle, sha256_of
from nika_onlymap_exporter.writers.onlymap_writer import (
    ArtifactResult,
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

    def test_cached_result_is_byte_identical_to_uncached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        import nika_onlymap_exporter.packaging.asset_embedder as embedder

        data = b'{"type":"FeatureCollection"}' * 200
        # The reference is built with the library directly, not via the cache.
        expected = base64.b64encode(gzip.compress(data, 9, mtime=0)).decode("ascii")
        monkeypatch.setattr(embedder, "_GZIP_CACHE", _CompressionCache(1 << 20))
        first = gzip_base64(data)
        calls: list[int] = []
        real_compress = gzip.compress
        monkeypatch.setattr(
            embedder.gzip,
            "compress",
            lambda *a, **k: calls.append(1) or real_compress(*a, **k),
        )
        second = gzip_base64(data)
        assert first == second == expected
        assert calls == [], "the second call must come from the cache"

    def test_cache_is_invalidated_when_the_input_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        import nika_onlymap_exporter.packaging.asset_embedder as embedder

        monkeypatch.setattr(embedder, "_GZIP_CACHE", _CompressionCache(1 << 20))
        before = gzip_base64(b"runtime v1 " * 50)
        after = gzip_base64(b"runtime v2 " * 50)
        assert before != after
        assert gzip.decompress(base64.b64decode(after)) == b"runtime v2 " * 50

    def test_cache_stays_within_its_byte_budget(self) -> None:
        cache = _CompressionCache(max_bytes=100)
        for index in range(10):
            cache.put((bytes([index]), 1, 9), "x" * 40)
        assert cache._size <= 100
        assert len(cache._entries) == 2
        # Least recently used goes first; the newest survive.
        assert cache.get((bytes([0]), 1, 9)) is None
        assert cache.get((bytes([9]), 1, 9)) == "x" * 40
        # One result bigger than the whole budget is not kept at all.
        cache.put((b"big", 1, 9), "y" * 101)
        assert cache.get((b"big", 1, 9)) is None

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

    def test_the_per_layer_sizes_add_up_to_the_whole(self) -> None:
        project = make_project()
        sizes = data_bytes_by_layer(project)
        assert set(sizes) == {layer.layer_id for layer in project.exportable_layers}
        assert sum(sizes.values()) == measure_data_bytes(project)

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

    @pytest.mark.parametrize(
        ("settings", "hosts"),
        [
            pytest.param({}, (), id="the-default-is-offline"),
            pytest.param({"basemap": "osm"}, ("openstreetmap.org",), id="basemap"),
            pytest.param({"basemap": "liberty"}, ("openfreemap.org",), id="vector"),
            pytest.param(
                {"terrain": "terrarium", "basemap": "voyager"},
                ("s3.amazonaws.com", "earthdata.nasa.gov", "unpkg.com"),
                id="terrain-with-imagery",
            ),
            # The runtime swaps a vector basemap for the relief surface, so only
            # the elevation tiles and the decoder scripts are fetched.
            pytest.param(
                {"terrain": "terrarium", "basemap": "liberty"},
                ("s3.amazonaws.com", "unpkg.com"),
                id="terrain-with-a-vector-basemap",
            ),
            pytest.param(
                {"terrain": "terrarium"},
                ("s3.amazonaws.com", "unpkg.com"),
                id="terrain-alone",
            ),
            pytest.param({"basemap": "nonsense"}, (), id="unknown-preset-is-none"),
            pytest.param(
                {"basemap": "osm", "map_crs": "EPSG:3414", "map_crs_def": "+proj=x"},
                (),
                id="a-projected-map-draws-neither",
            ),
        ],
    )
    def test_a_configured_basemap_or_terrain_is_a_network_dependency(
        self, settings, hosts
    ) -> None:
        """The manifest decides what is fetched; the scan has to say the same,
        or a map that needs the internet is described as offline."""
        from nika_onlymap_exporter.core.export_ir import ExportSettings

        result = scan(
            make_project(settings=ExportSettings(**settings)), FidelityReportBuilder()
        )
        assert result.remote_dependencies == hosts
        assert result.is_offline is (hosts == ())
        assert result.snapshot()["remoteDependencies"] == list(hosts)

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


def _staged_result(directory: Path, files: dict[str, bytes]) -> ArtifactResult:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    return ArtifactResult(
        entry_path=directory / "index.html", mode=OutputMode.STANDALONE_HTML
    )


class TestExportIntegrity:
    """A failed export must not cost the user the file they already had."""

    def test_failed_html_copy_keeps_the_previous_file(self, tmp_path) -> None:
        destination = tmp_path / "map.html"
        destination.write_bytes(b"ORIGINAL VALID MAP")

        def torn_copy(src, dst, *args, **kwargs):
            Path(dst).write_bytes(b"PARTIAL")
            raise OSError("disk full")

        with (
            patch("shutil.copyfile", side_effect=torn_copy),
            pytest.raises(OSError),
        ):
            build_artifact(
                make_project(),
                destination,
                writer=OnlyMapWriter(runtime_provider=FakeRuntime()),
            )
        assert destination.read_bytes() == b"ORIGINAL VALID MAP"
        assert [p.name for p in tmp_path.iterdir()] == ["map.html"]

    @pytest.mark.parametrize(
        ("mode", "name"),
        [
            (OutputMode.STANDALONE_HTML, "map.html"),
            (OutputMode.SHARE_ZIP, "map.zip"),
            (OutputMode.FOLDER, "map"),
        ],
    )
    def test_a_cancel_before_the_commit_leaves_the_destination_untouched(
        self, tmp_path, mode, name
    ) -> None:
        destination = tmp_path / name
        if mode is OutputMode.FOLDER:
            destination.mkdir()
            (destination / "index.html").write_bytes(b"ORIGINAL VALID MAP")
        else:
            destination.write_bytes(b"ORIGINAL VALID MAP")
        committed: list[bool] = []

        with pytest.raises(ExportCancelledError):
            build_artifact(
                make_project(),
                destination,
                mode=mode,
                writer=OnlyMapWriter(runtime_provider=FakeRuntime()),
                should_cancel=lambda: True,
                on_commit=lambda: committed.append(True),
            )

        assert committed == [], "the commit hook ran for a cancelled export"
        if mode is OutputMode.FOLDER:
            assert [p.name for p in destination.iterdir()] == ["index.html"]
            assert (destination / "index.html").read_bytes() == b"ORIGINAL VALID MAP"
        else:
            assert destination.read_bytes() == b"ORIGINAL VALID MAP"
            assert [p.name for p in tmp_path.iterdir()] == [name]

    @pytest.mark.parametrize(
        ("mode", "name"),
        [
            (OutputMode.STANDALONE_HTML, "map.html"),
            (OutputMode.SHARE_ZIP, "map.zip"),
            (OutputMode.FOLDER, "map"),
        ],
    )
    def test_a_cancel_after_the_commit_still_reports_success(
        self, tmp_path, mode, name
    ) -> None:
        stop = {"asked": False}
        calls: list[bool] = []

        def should_cancel() -> bool:
            calls.append(stop["asked"])
            return stop["asked"]

        _, outcome = build_artifact(
            make_project(),
            tmp_path / name,
            mode=mode,
            writer=OnlyMapWriter(runtime_provider=FakeRuntime()),
            should_cancel=should_cancel,
            # The user presses Cancel the instant the commit begins.
            on_commit=lambda: stop.update(asked=True),
        )

        assert outcome.path.exists()
        assert outcome.size_bytes > 0
        # Nothing asked "should I stop?" once the artifact was being placed.
        assert calls and not calls[-1]

    def test_failed_zip_keeps_the_previous_archive(self, tmp_path) -> None:
        destination = tmp_path / "map.zip"
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("map/index.html", "ORIGINAL VALID MAP")
        before = destination.read_bytes()

        with (
            patch("zipfile.ZipFile.write", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            build_artifact(
                make_project(),
                destination,
                mode=OutputMode.SHARE_ZIP,
                writer=OnlyMapWriter(runtime_provider=FakeRuntime()),
            )
        assert destination.read_bytes() == before
        assert [p.name for p in tmp_path.iterdir()] == ["map.zip"]

    def test_folder_reexport_drops_removed_assets_and_keeps_user_files(
        self, tmp_path
    ) -> None:
        folder = tmp_path / "out"
        first = _staged_result(
            tmp_path / "s1", {"index.html": b"v1", "dem.tif": b"raster"}
        )
        FolderExporter().export(first, folder)
        (folder / "notes-from-me.txt").write_text("mine")

        second = _staged_result(tmp_path / "s2", {"index.html": b"v2"})
        FolderExporter().export(second, folder)

        assert not (folder / "dem.tif").exists()
        assert (folder / "index.html").read_bytes() == b"v2"
        assert (folder / "notes-from-me.txt").read_text() == "mine"

    def test_folder_never_deletes_a_file_it_did_not_write(self, tmp_path) -> None:
        folder = tmp_path / "out"
        folder.mkdir()
        (folder / "dem.tif").write_bytes(b"theirs, no manifest yet")
        FolderExporter().export(
            _staged_result(tmp_path / "s", {"index.html": b"v1"}), folder
        )
        assert (folder / "dem.tif").read_bytes() == b"theirs, no manifest yet"

    def test_folder_failure_midway_keeps_old_assets_owned(self, tmp_path) -> None:
        folder = tmp_path / "out"
        FolderExporter().export(
            _staged_result(tmp_path / "s1", {"index.html": b"v1", "dem.tif": b"r"}),
            folder,
        )
        second = _staged_result(tmp_path / "s2", {"index.html": b"v2"})
        with (
            patch("shutil.copy2", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            FolderExporter().export(second, folder)
        # Nothing was removed before the new files were all in place.
        assert (folder / "dem.tif").read_bytes() == b"r"
        assert (folder / "index.html").read_bytes() == b"v1"
        FolderExporter().export(second, folder)
        assert not (folder / "dem.tif").exists()


class TestWindowsReservedNames:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("CON", "CON-map"),
            ("lpt9.html", "lpt9.html-map"),
            ("Aux", "Aux-map"),
            ("CONSOLE", "CONSOLE"),
            ("my CON", "my CON"),
        ],
    )
    def test_reserved_names_get_a_suffix(self, name: str, expected: str) -> None:
        assert avoid_windows_reserved(name) == expected


class TestZipArchiveRoot:
    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            ("..", "map"),
            ("", "map"),
            ("a/b", "a-b"),
            ("a\\..\\b", "a-..-b"),
            ("trailing. ", "trailing"),
            ("CON", "CON-map"),
            ("my map", "my map"),
        ],
    )
    def test_root_is_portable(self, stem: str, expected: str) -> None:
        assert archive_root(stem) == expected

    def test_dots_zip_does_not_write_outside_the_root(self, tmp_path) -> None:
        result = _staged_result(tmp_path / "s", {"index.html": b"x"})
        outcome = ShareZipExporter().export(result, tmp_path / "...zip")
        with zipfile.ZipFile(outcome.path) as archive:
            names = archive.namelist()
        # The stem of "...zip" is ".." on some Pythons and "...zip" on others.
        (name,) = names
        root, _, leaf = name.partition("/")
        assert leaf == "index.html"
        assert root not in ("", ".", "..")
        assert f"{root} folder" in outcome.open_instruction
