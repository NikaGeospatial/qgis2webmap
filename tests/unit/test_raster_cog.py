"""COG detection and conversion.

Two tiers in one file, on purpose. Everything that decides *something* -- what
makes a file a COG, what creation options a conversion asks for, how a failure
is worded -- is a pure function of a `RasterProbe`, and is tested here with no
GDAL anywhere in sight, because `tests/unit` must stay runnable on a plain
Python interpreter. The GDAL-backed tests below `pytest.importorskip` are the
other half: they are the only proof that our reading of GDAL's behaviour is
right, and they run wherever GDAL exists (a QGIS interpreter, or CI's raster
tier) and skip cleanly where it does not.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.packaging import raster_cog as rc


def probe(**overrides: object) -> rc.RasterProbe:
    """A probe of a healthy, ordinary COG, with fields overridden per test."""
    defaults: dict[str, object] = {
        "path": "/data/ortho.tif",
        "driver": "GTiff",
        "width": 4096,
        "height": 4096,
        "band_count": 3,
        "layout": "COG",
        "compression": "DEFLATE",
        "block_size": (512, 512),
        "overview_count": 3,
        "epsg": 3857,
        "has_crs": True,
        "file_bytes": 10_000_000,
    }
    defaults.update(overrides)
    return rc.RasterProbe(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


def test_gdal_layout_is_what_decides_the_verdict():
    verdict = rc.verdict_from_probe(probe())
    assert verdict.is_cog
    assert verdict.is_web_ready
    assert verdict.reasons == ()


def test_a_cog_in_another_crs_is_a_cog_but_not_web_ready():
    verdict = rc.verdict_from_probe(probe(epsg=32631))
    assert verdict.is_cog
    assert verdict.needs_reprojection
    assert not verdict.is_web_ready
    assert "EPSG:32631" in verdict.describe()


def test_an_unidentified_crs_counts_as_needing_reprojection():
    # Assuming Web Mercator for a CRS we cannot name is how imagery ends up in
    # the wrong hemisphere; the verdict must err towards reprojecting.
    verdict = rc.verdict_from_probe(probe(epsg=None))
    assert verdict.needs_reprojection


def test_stripped_geotiff_is_refused_and_says_so():
    verdict = rc.verdict_from_probe(
        probe(layout=None, block_size=(4096, 1), overview_count=0)
    )
    assert not verdict.is_cog
    assert any("strips" in reason for reason in verdict.reasons)
    assert any("overviews" in reason for reason in verdict.reasons)


def test_missing_overviews_are_only_a_problem_for_a_large_raster():
    small = rc.verdict_from_probe(
        probe(layout=None, width=256, height=256, overview_count=0)
    )
    assert not any("overviews" in reason for reason in small.reasons)


def test_non_geotiff_reports_the_format_and_nothing_else():
    verdict = rc.verdict_from_probe(
        probe(driver="PNG", layout=None, block_size=(256, 1))
    )
    assert verdict.reasons == ("it is a PNG file, not a GeoTIFF",)


def test_unreadable_compression_is_disqualifying():
    verdict = rc.verdict_from_probe(probe(layout=None, compression="CCITTFAX4"))
    assert any("CCITTFAX4" in reason for reason in verdict.reasons)


def test_a_tiled_overviewed_geotiff_gdal_still_refuses_gets_a_reason():
    # The fallback sentence is not dead code: a GeoTIFF can be tiled and
    # pyramided and still fail COG layout because its IFDs sit at the end.
    verdict = rc.verdict_from_probe(probe(layout=None))
    assert not verdict.is_cog
    assert len(verdict.reasons) == 1
    assert "range requests" in verdict.reasons[0]


def test_a_corrupt_file_is_a_verdict_not_an_exception():
    verdict = rc.verdict_from_probe(probe(open_error="ortho.tif is truncated."))
    assert not verdict.is_cog
    assert verdict.reasons == ("ortho.tif is truncated.",)


def test_a_file_with_no_bands_is_refused():
    verdict = rc.verdict_from_probe(probe(band_count=0, layout=None))
    assert not verdict.is_cog
    assert "no raster bands" in verdict.reasons[0]


def test_missing_crs_is_a_warning_on_the_verdict_not_a_refusal():
    # Being a COG and being placeable on a map are separate questions; a COG
    # with no CRS is still a COG, and conflating the two would make the
    # explanation wrong.
    verdict = rc.verdict_from_probe(probe(has_crs=False, epsg=None))
    assert verdict.is_cog
    assert any("no CRS" in warning for warning in verdict.warnings)


def test_uncompressed_cog_is_flagged_as_an_opportunity():
    verdict = rc.verdict_from_probe(probe(compression="NONE"))
    assert verdict.is_cog
    assert any("uncompressed" in warning for warning in verdict.warnings)


def test_verdict_snapshot_is_json_ready():
    snapshot = rc.verdict_from_probe(probe()).snapshot()
    assert snapshot["isCog"] is True
    assert snapshot["epsg"] == 3857
    assert isinstance(snapshot["reasons"], list)


# --------------------------------------------------------------------------
# Creation options
# --------------------------------------------------------------------------


def test_creation_options_carry_compression_tiling_and_overviews():
    options = rc.build_creation_options()
    assert "COMPRESS=DEFLATE" in options
    assert "BLOCKSIZE=512" in options
    assert "RESAMPLING=AVERAGE" in options
    assert "OVERVIEWS=AUTO" in options
    assert "TARGET_SRS=EPSG:3857" in options


def test_no_target_srs_is_emitted_when_none_is_asked_for():
    options = rc.build_creation_options(target_epsg=None)
    assert not any(option.startswith("TARGET_SRS") for option in options)


def test_extra_options_come_last_so_a_caller_can_override():
    options = rc.build_creation_options(extra=("COMPRESS=ZSTD",))
    assert options.index("COMPRESS=ZSTD") > options.index("COMPRESS=DEFLATE")


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


def test_a_skipped_conversion_points_the_destination_at_the_source():
    result = rc.CogResult(
        source="/data/ortho.tif",
        destination="/data/ortho.tif",
        source_bytes=100,
        destination_bytes=100,
        skipped=True,
    )
    assert "used unchanged" in result.describe()
    assert result.size_ratio == 1.0


def test_size_ratio_is_none_when_the_source_size_is_unknown():
    result = rc.CogResult(
        source="a", destination="b", source_bytes=0, destination_bytes=5
    )
    assert result.size_ratio is None


def test_result_describe_mentions_the_reprojection():
    result = rc.CogResult(
        source="/data/ortho.tif",
        destination="/out/ortho.tif",
        source_bytes=2_000_000,
        destination_bytes=1_000_000,
        reprojected=True,
        source_epsg=32631,
        overview_count=4,
    )
    text = result.describe()
    assert "EPSG:32631" in text
    assert "4 overview levels" in text


# --------------------------------------------------------------------------
# Degrading without GDAL
# --------------------------------------------------------------------------


def test_importing_this_module_never_needs_gdal():
    # The whole point of the deferred import: this test file has already run
    # a dozen assertions on an interpreter that may have no osgeo at all.
    assert rc.GDAL_MISSING_MESSAGE.startswith("GDAL's Python bindings")


def test_gdal_available_reports_false_rather_than_raising(monkeypatch):
    def refuse() -> object:
        raise rc.GdalUnavailableError(rc.GDAL_MISSING_MESSAGE)

    monkeypatch.setattr(rc, "load_gdal", refuse)
    assert rc.gdal_available() is False
    assert rc.gdal_version() is None
    with pytest.raises(rc.GdalUnavailableError):
        rc.is_cog("/data/ortho.tif")


# --------------------------------------------------------------------------
# Against a real GDAL
# --------------------------------------------------------------------------

# A module-level `importorskip` would skip this *whole file*, taking the pure
# tier above with it -- exactly the coverage that has to keep running on an
# interpreter without GDAL. So the import is soft and each test below carries
# its own marker.
try:
    from osgeo import gdal
except ImportError:  # pragma: no cover - depends on the interpreter
    gdal = None  # type: ignore[assignment]

needs_gdal = pytest.mark.skipif(
    gdal is None, reason="GDAL is unavailable; skipping the raster conversion tier"
)


@pytest.fixture
def plain_geotiff(tmp_path):
    """A stripped, uncompressed EPSG:4326 GeoTIFF -- the worst realistic input."""
    from osgeo import osr

    path = tmp_path / "plain.tif"
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 1200, 900, 1, gdal.GDT_Byte)
    reference = osr.SpatialReference()
    reference.ImportFromEPSG(4326)
    dataset.SetProjection(reference.ExportToWkt())
    dataset.SetGeoTransform([3.3, 0.0005, 0.0, 6.6, 0.0, -0.0005])
    dataset.GetRasterBand(1).Fill(128)
    dataset = None
    return path


@needs_gdal
def test_a_plain_geotiff_is_not_a_cog(plain_geotiff):
    verdict = rc.is_cog(plain_geotiff)
    assert not verdict.is_cog
    assert verdict.reasons
    assert verdict.epsg == 4326


@needs_gdal
def test_conversion_produces_a_reprojected_cog(tmp_path, plain_geotiff):
    ticks: list[tuple[int, int | None]] = []
    destination = tmp_path / "out" / "ortho.tif"

    result = rc.to_cog(
        plain_geotiff,
        destination,
        on_progress=lambda done, total: ticks.append((done, total)),
    )

    assert not result.skipped
    assert result.reprojected
    assert result.source_epsg == 4326
    assert result.destination_epsg == rc.WEB_MERCATOR_EPSG
    assert result.compression == "DEFLATE"
    assert destination.exists()
    assert result.destination_bytes == destination.stat().st_size
    assert ticks and ticks[-1] == (rc.PROGRESS_TOTAL, rc.PROGRESS_TOTAL)

    # The output must satisfy our own detector, or detection and conversion
    # disagree and one of them is wrong.
    assert rc.is_cog(destination).is_web_ready


@needs_gdal
def test_an_already_web_ready_cog_is_not_copied(tmp_path, plain_geotiff):
    first = tmp_path / "first.tif"
    rc.to_cog(plain_geotiff, first)

    second = tmp_path / "second.tif"
    result = rc.to_cog(first, second)

    assert result.skipped
    assert result.destination == str(first)
    assert not second.exists()


@needs_gdal
def test_skip_if_ready_false_forces_a_copy(tmp_path, plain_geotiff):
    first = tmp_path / "first.tif"
    rc.to_cog(plain_geotiff, first)

    second = tmp_path / "second.tif"
    result = rc.to_cog(first, second, skip_if_ready=False)

    assert not result.skipped
    assert second.exists()


@needs_gdal
def test_a_raster_without_a_crs_is_refused_with_a_fixable_message(tmp_path):
    path = tmp_path / "nocrs.tif"
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), 64, 64, 1, gdal.GDT_Byte)
    dataset.GetRasterBand(1).Fill(1)
    dataset = None

    with pytest.raises(rc.MissingCrsError) as error:
        rc.to_cog(path, tmp_path / "out.tif")
    assert "Layer Properties" in str(error.value)


@needs_gdal
def test_a_corrupt_file_produces_a_message_not_a_traceback(tmp_path):
    path = tmp_path / "broken.tif"
    path.write_bytes(b"II*\x00 this is not a tiff")

    verdict = rc.is_cog(path)
    assert not verdict.is_cog
    assert "could not open" in verdict.reasons[0]

    with pytest.raises(rc.RasterOpenError):
        rc.to_cog(path, tmp_path / "out.tif")


@needs_gdal
def test_a_missing_file_says_it_does_not_exist(tmp_path):
    verdict = rc.is_cog(tmp_path / "absent.tif")
    assert verdict.reasons[0].endswith("does not exist.")


@needs_gdal
def test_converting_onto_the_source_is_refused(plain_geotiff):
    with pytest.raises(rc.ConversionError) as error:
        rc.to_cog(plain_geotiff, plain_geotiff)
    assert "in place" in str(error.value)


@needs_gdal
def test_cancellation_is_reported_as_cancellation(tmp_path, plain_geotiff):
    with pytest.raises(rc.ConversionError) as error:
        rc.to_cog(plain_geotiff, tmp_path / "out.tif", should_cancel=lambda: True)
    assert "cancelled" in str(error.value)


@needs_gdal
def test_gdal_version_is_reported():
    assert rc.gdal_available()
    assert rc.gdal_version()
