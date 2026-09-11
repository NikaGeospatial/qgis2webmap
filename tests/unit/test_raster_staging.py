"""Wiring a raster into an artifact: placement, refusals, and size accounting.

Runs with no GDAL, which is the point of `stage_rasters(converter=...)`: the
decisions worth testing are about where the pixels go and which tiers can hold
them, and none of them need a real GeoTIFF. The conversion itself is covered by
`test_raster_cog.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_packaging import FakeRuntime  # the same stub the writer tests use

from nika_onlymap_exporter.core.export_ir import (
    AssetDependency,
    AssetDisposition,
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    FidelityStatus,
    GeometryKind,
    OutputMode,
    RasterSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.artifact_builder import zip_open_instruction
from nika_onlymap_exporter.packaging.dependency_scanner import (
    measure_data_bytes,
    raster_bytes,
    scan,
    standalone_ineligible_reason,
    standalone_raster_reason,
)
from nika_onlymap_exporter.packaging.hosted_assets import FLAT_NAME_PATTERN
from nika_onlymap_exporter.packaging.publish_manifest import describe_file
from nika_onlymap_exporter.packaging.raster_cog import CogResult, MissingCrsError
from nika_onlymap_exporter.packaging.raster_staging import (
    raster_file_name,
    stage_rasters,
)
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

PIXELS = b"II*\x00fake cloud optimized geotiff" * 64


def write_source(
    tmp_path: Path, name: str = "ortho.tif", payload: bytes = PIXELS
) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def raster_layer(
    source: Path, layer_id: str = "dem", name: str = "Elevation"
) -> ExportLayer:
    return ExportLayer(
        layer_id=layer_id,
        name=name,
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.FILE,
        raster=RasterSpec(
            path=str(source),
            band_count=1,
            source_crs="EPSG:4326",
            pixel_width=256,
            pixel_height=256,
        ),
        dependencies=(
            AssetDependency(
                identifier=str(source),
                disposition=AssetDisposition.EMBEDDABLE,
                size_bytes=source.stat().st_size if source.exists() else None,
            ),
        ),
    )


def vector_layer() -> ExportLayer:
    return ExportLayer(
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


def project_with(*layers: ExportLayer) -> ExportProject:
    return ExportProject(
        title="Test map",
        layers=layers,
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
    )


class FakeConverter:
    """Stands in for `to_cog`. Records what it was asked to do.

    `skip` reproduces the case callers get wrong: `to_cog` hands back the
    *source* path when the file is already a web-ready COG, so anything that
    assumes the destination it passed in reads a file that was never written.
    """

    def __init__(self, skip: bool = False, output: bytes = b"COG-BYTES") -> None:
        self.skip = skip
        self.output = output
        self.calls: list[tuple[str, str]] = []

    def __call__(self, source, destination, on_progress, should_cancel) -> CogResult:
        self.calls.append((source, destination))
        if on_progress is not None:
            on_progress(50, 100)
            on_progress(100, 100)
        if self.skip:
            return CogResult(
                source=source,
                destination=source,
                source_bytes=Path(source).stat().st_size,
                destination_bytes=Path(source).stat().st_size,
                skipped=True,
            )
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(self.output)
        return CogResult(
            source=source,
            destination=destination,
            source_bytes=Path(source).stat().st_size,
            destination_bytes=len(self.output),
        )


class FailingConverter:
    def __call__(self, source, destination, on_progress, should_cancel) -> CogResult:
        raise MissingCrsError("ortho.tif has no coordinate reference system.")


class TestFileNaming:
    def test_unsafe_characters_never_reach_the_filesystem(self) -> None:
        name = raster_file_name("../../etc/passwd", 0)
        assert "/" not in name and ".." not in name
        assert name.endswith(".tif")

    def test_two_layers_cannot_collide(self) -> None:
        """A collision would be invisible: the second raster would simply draw
        the first one's image."""
        assert raster_file_name("!!!", 0) != raster_file_name("???", 1)


class TestSiblingTiers:
    @pytest.mark.parametrize("mode", [OutputMode.FOLDER, OutputMode.SHARE_ZIP])
    def test_the_cog_is_written_beside_the_page_and_referenced_by_name(
        self, tmp_path: Path, mode: OutputMode
    ) -> None:
        source = write_source(tmp_path)
        out = tmp_path / "artifact"
        result = stage_rasters(
            project_with(raster_layer(source)),
            out,
            mode,
            FidelityReportBuilder(),
            converter=FakeConverter(),
        )

        spec = result.project.layers[0].raster
        assert spec is not None
        assert spec.src == result.files[0].name
        assert spec.is_cog is True
        # A relative name, not a path on the exporting machine: the whole bug
        # this module exists to fix.
        assert not Path(spec.src).is_absolute()
        assert result.files[0].read_bytes() == b"COG-BYTES"

    def test_a_skipped_conversion_still_lands_in_the_artifact(
        self, tmp_path: Path
    ) -> None:
        """`to_cog` returns the source path when it declines to convert. The
        artifact still needs its own copy or it ships a broken reference."""
        source = write_source(tmp_path)
        out = tmp_path / "artifact"
        result = stage_rasters(
            project_with(raster_layer(source)),
            out,
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(skip=True),
        )

        assert result.files[0].parent == out
        assert result.files[0].read_bytes() == PIXELS
        assert result.staged[0].reused_source is True

    def test_the_zip_tier_says_the_folder_has_to_be_served(
        self, tmp_path: Path
    ) -> None:
        report = FidelityReportBuilder()
        stage_rasters(
            project_with(raster_layer(write_source(tmp_path))),
            tmp_path / "artifact",
            OutputMode.SHARE_ZIP,
            report,
            converter=FakeConverter(),
        )
        assert any("served" in item.detail for item in report.items)


class TestStandaloneTier:
    """The single file cannot carry a raster at all, and the reason is not size.

    Measured against the pinned runtime: `src` goes to a chunked reader that
    issues `Range` requests, a `data:` URI ignores Range and answers 200 with
    the whole body, and the reader has no guard for that -- so it slices the
    wrong offsets and draws a wrong image without erroring. Refusing is the
    only outcome that is not silently broken.
    """

    def test_a_raster_is_refused_before_anything_is_converted(
        self, tmp_path: Path
    ) -> None:
        converter = FakeConverter()
        report = FidelityReportBuilder()
        out = tmp_path / "artifact"
        result = stage_rasters(
            project_with(raster_layer(write_source(tmp_path))),
            out,
            OutputMode.STANDALONE_HTML,
            report,
            converter=converter,
        )

        assert result.can_export is False
        # Not converted: minutes of GDAL for an answer knowable at the start.
        assert converter.calls == []
        assert result.files == ()
        assert not out.exists()
        assert any(i.status is FidelityStatus.BLOCKED for i in report.items)

    def test_the_refusal_names_the_layer_and_the_tier_that_works(self) -> None:
        reason = standalone_raster_reason(
            project_with(vector_layer(), raster_layer(Path("/nowhere/ortho.tif")))
        )
        assert reason is not None
        assert "'Elevation'" in reason
        assert "Folder" in reason

    def test_a_vector_only_project_keeps_the_single_file_tier(self) -> None:
        assert standalone_raster_reason(project_with(vector_layer())) is None

    def test_the_scanner_blocks_the_same_case_with_the_same_words(
        self, tmp_path: Path
    ) -> None:
        """One rule, two call sites: a dialog that greys the tier out and an
        export that refuses it must not be able to disagree."""
        project = project_with(raster_layer(write_source(tmp_path)))
        result = scan(project, FidelityReportBuilder(), OutputMode.STANDALONE_HTML)
        assert result.can_export is False
        assert result.blocking_reasons[0] == standalone_raster_reason(project)

    def test_the_scanner_allows_the_sibling_tiers(self, tmp_path: Path) -> None:
        project = project_with(raster_layer(write_source(tmp_path)))
        for mode in (OutputMode.FOLDER, OutputMode.SHARE_ZIP):
            result = scan(project, FidelityReportBuilder(), mode)
            assert result.can_export is True


class TestFailures:
    def test_a_conversion_failure_blocks_rather_than_dropping_the_layer(
        self, tmp_path: Path
    ) -> None:
        """CONTRIBUTING.md: a silently broken artifact is the one unacceptable
        outcome, and a dropped raster is invisible - it has no feature count to
        come up short."""
        report = FidelityReportBuilder()
        result = stage_rasters(
            project_with(raster_layer(write_source(tmp_path))),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            report,
            converter=FailingConverter(),
        )

        assert result.can_export is False
        assert "coordinate reference system" in result.blocking_reasons[0]
        assert any(i.status is FidelityStatus.BLOCKED for i in report.items)
        assert result.files == ()

    def test_a_vector_only_project_is_untouched(self, tmp_path: Path) -> None:
        project = project_with(vector_layer())
        result = stage_rasters(
            project, tmp_path, OutputMode.FOLDER, FidelityReportBuilder()
        )
        assert result.project is project
        assert result.staged == ()


class TestProgress:
    def test_progress_spans_every_raster_rather_than_restarting(
        self, tmp_path: Path
    ) -> None:
        first = write_source(tmp_path, "a.tif")
        second = write_source(tmp_path, "b.tif")
        seen: list[tuple[int, int | None]] = []

        stage_rasters(
            project_with(raster_layer(first, "a", "A"), raster_layer(second, "b", "B")),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            on_progress=lambda done, total: seen.append((done, total)),
            converter=FakeConverter(),
        )

        assert seen[0] == (50, 200)
        assert seen[-1] == (200, 200)


class TestSizeAccounting:
    def test_a_raster_is_visible_to_the_size_check(self, tmp_path: Path) -> None:
        """Regression: `measure_data_bytes` counted GeoJSON only, so a 400 MB
        orthophoto weighed nothing and standalone was declared eligible."""
        source = write_source(tmp_path)
        project = project_with(raster_layer(source))
        assert raster_bytes(project) == source.stat().st_size
        assert measure_data_bytes(project) >= source.stat().st_size

    def test_a_missing_dependency_record_falls_back_to_the_file(
        self, tmp_path: Path
    ) -> None:
        source = write_source(tmp_path)
        layer = ExportLayer(
            layer_id="dem",
            name="Elevation",
            geometry_kind=GeometryKind.RASTER,
            source_kind=SourceKind.FILE,
            raster=RasterSpec(path=str(source)),
        )
        assert raster_bytes(project_with(layer)) == source.stat().st_size

    def test_any_raster_makes_standalone_ineligible(self, tmp_path: Path) -> None:
        reason = standalone_ineligible_reason(
            project_with(raster_layer(write_source(tmp_path)))
        )
        assert reason is not None
        assert "Folder" in reason


class TestRecipientInstructions:
    """The README has to describe the artifact the recipient actually got."""

    def test_a_vector_zip_is_still_extract_and_open(self) -> None:
        assert zip_open_instruction(project_with(vector_layer())) == (
            "Extract this zip, then open index.html."
        )

    def test_a_raster_zip_says_to_serve_it(self, tmp_path: Path) -> None:
        instruction = zip_open_instruction(
            project_with(vector_layer(), raster_layer(write_source(tmp_path)))
        )
        assert "http.server" in instruction
        assert "double-click" in instruction


class TestWriterIntegration:
    def test_the_folder_tier_ships_the_cog_and_the_manifest_points_at_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nika_onlymap_exporter.packaging.raster_staging as staging_module

        monkeypatch.setattr(staging_module, "gdal_available", lambda: True)
        monkeypatch.setattr(staging_module, "_convert", FakeConverter())

        source = write_source(tmp_path)
        out = tmp_path / "artifact"
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            project_with(vector_layer(), raster_layer(source)),
            out,
            mode=OutputMode.FOLDER,
            unbundle=True,
        )

        cog = next(f for f in result.files if f.path.suffix == ".tif")
        html = result.entry_path.read_text()
        assert f'src="{cog.path.name}"' in html
        assert str(source) not in html

    def test_the_single_file_tier_refuses_a_raster(self, tmp_path: Path) -> None:
        out = tmp_path / "artifact"
        with pytest.raises(ExportBlockedError) as caught:
            OnlyMapWriter(runtime_provider=FakeRuntime()).write(
                project_with(raster_layer(write_source(tmp_path))),
                out,
                mode=OutputMode.STANDALONE_HTML,
            )
        assert "Folder" in str(caught.value)
        assert not (out / "index.html").exists()

    def test_a_blocked_raster_stops_the_export_before_the_page_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nika_onlymap_exporter.packaging.raster_staging as staging_module

        monkeypatch.setattr(staging_module, "gdal_available", lambda: True)
        monkeypatch.setattr(staging_module, "_convert", FailingConverter())

        out = tmp_path / "artifact"
        with pytest.raises(ExportBlockedError):
            OnlyMapWriter(runtime_provider=FakeRuntime()).write(
                project_with(raster_layer(write_source(tmp_path))),
                out,
                mode=OutputMode.FOLDER,
            )
        assert not (out / "index.html").exists()

    def test_without_gdal_the_export_stops_and_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nika_onlymap_exporter.packaging.raster_staging as staging_module

        monkeypatch.setattr(staging_module, "gdal_available", lambda: False)

        with pytest.raises(ExportBlockedError) as caught:
            OnlyMapWriter(runtime_provider=FakeRuntime()).write(
                project_with(raster_layer(write_source(tmp_path))),
                tmp_path / "artifact",
                mode=OutputMode.FOLDER,
            )
        assert "GDAL" in str(caught.value)


class TestHostedAddressing:
    """A hosted map asks the asset store for a digest, not for a file name.

    The same rule layer data already follows (`packaging/hosted_assets.py`),
    applied to the one other payload an artifact carries. A raster referenced
    by its local name would be a request for a key the store does not hold, and
    a relative one resolves differently depending on whether the link the
    visitor followed ended in a slash - so it would 404 for some readers and
    not others, which is a worse failure than failing for everybody.
    """

    def test_the_page_names_the_digest_of_the_bytes_that_were_written(
        self, tmp_path: Path
    ) -> None:
        source = write_source(tmp_path)
        out = tmp_path / "artifact"
        result = stage_rasters(
            project_with(raster_layer(source)),
            out,
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(),
            hosted=True,
        )

        spec = result.project.layers[0].raster
        assert spec is not None
        digest = hashlib.sha256(result.files[0].read_bytes()).hexdigest()
        assert spec.src == f"/assets/{digest}.tif"

    def test_the_reference_is_root_absolute(self, tmp_path: Path) -> None:
        """`assets/x` would resolve against the visitor's last path segment."""
        source = write_source(tmp_path)
        result = stage_rasters(
            project_with(raster_layer(source)),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(),
            hosted=True,
        )
        spec = result.project.layers[0].raster
        assert spec is not None
        assert spec.src.startswith("/assets/")

    def test_the_file_on_disk_keeps_the_name_a_person_can_read(
        self, tmp_path: Path
    ) -> None:
        """Local name and served key are different on purpose, and a digest
        plus an extension does not fit the flat 64-character rule anyway."""
        source = write_source(tmp_path)
        result = stage_rasters(
            project_with(raster_layer(source)),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(),
            hosted=True,
        )
        name = result.files[0].name
        assert FLAT_NAME_PATTERN.match(name), name
        assert name == raster_file_name("dem", 0)

    def test_the_digest_is_the_one_the_publish_manifest_declares(
        self, tmp_path: Path
    ) -> None:
        """The join the server checks: every asset path in the page has to name
        a digest the manifest carries, or the reference resolves to nothing."""
        source = write_source(tmp_path)
        result = stage_rasters(
            project_with(raster_layer(source)),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(),
            hosted=True,
        )
        spec = result.project.layers[0].raster
        assert spec is not None
        declared = describe_file(result.files[0])
        assert declared["role"] == "raster"
        assert spec.src == f"/assets/{declared['sha256']}.tif"

    def test_a_skipped_conversion_hashes_the_copy_and_not_the_source(
        self, tmp_path: Path
    ) -> None:
        """`to_cog` hands back the user's own path when it declines to rewrite
        an already web-ready COG. The artifact carries its own copy, and the
        digest has to be of that copy - it is the name the page asks for."""
        source = write_source(tmp_path)
        result = stage_rasters(
            project_with(raster_layer(source)),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(skip=True),
            hosted=True,
        )
        spec = result.project.layers[0].raster
        assert spec is not None
        digest = hashlib.sha256(result.files[0].read_bytes()).hexdigest()
        assert spec.src == f"/assets/{digest}.tif"
        assert result.staged[0].sha256 == digest

    def test_an_offline_tier_is_untouched_by_any_of_this(self, tmp_path: Path) -> None:
        """A folder or a zip may be opened from a subdirectory, where a
        root-absolute reference points at the root of the disk."""
        source = write_source(tmp_path)
        result = stage_rasters(
            project_with(raster_layer(source)),
            tmp_path / "artifact",
            OutputMode.FOLDER,
            FidelityReportBuilder(),
            converter=FakeConverter(),
        )
        spec = result.project.layers[0].raster
        assert spec is not None
        assert spec.src == result.files[0].name
        assert result.staged[0].sha256 == ""
