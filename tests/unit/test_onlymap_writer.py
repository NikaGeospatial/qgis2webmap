"""The writer and its artifact result.

Needs the OnlyMap runtime on disk; skips cleanly without it, like the QGIS tier.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    GeometryKind,
    OutputMode,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.packaging.runtime_manager import (
    LocalRuntime,
    RuntimeBundle,
    RuntimeUnavailableError,
    discover_runtime_dir,
    sha256_of,
)
from nika_onlymap_exporter.writers.onlymap_writer import (
    OnlyMapWriter,
    generator_line,
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


def make_project(**overrides) -> ExportProject:
    layer = ExportLayer(
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
    defaults = dict(
        title="Test map",
        layers=(layer,),
        extent=Extent(west=0.0, south=0.0, east=10.0, north=10.0),
        settings=ExportSettings(),
    )
    defaults.update(overrides)
    return ExportProject(**defaults)


class FakeRuntime:
    """A stand-in so writer tests do not need the real 5 MB bundle."""

    def load(self) -> RuntimeBundle:
        js = b"/* fake runtime */"
        return RuntimeBundle(
            javascript=js,
            css=b"om-map:not(:defined) om-fallback { display: block; }",
            version="0.0.0-test",
            sha256=sha256_of(js),
        )


class TestGeneratorLine:
    def test_names_both_versions_and_the_privacy_promise(self) -> None:
        line = generator_line(
            FakeRuntime().load(), when=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        )
        assert "QGIS2WebMap by NIKA" in line
        assert "0.0.0-test" in line
        assert "2026-07-30" in line
        assert "no tracking" in line


class TestWriter:
    def test_writes_an_index_html(self, tmp_path) -> None:
        """Always `index.html` - a recipient should never have to choose."""
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.entry_path.name == "index.html"
        assert result.entry_path.exists()

    def test_artifact_inlines_the_runtime_and_the_data(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        html = result.entry_path.read_text()
        assert "/* fake runtime */" in html
        assert '<script type="application/json">' in html
        assert "om-map:not(:defined)" in html, "the fallback CSS gate must be present"

    def test_reports_itself_as_offline(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.is_offline
        assert result.network_dependencies == ()

    def test_records_runtime_provenance(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.runtime_version == "0.0.0-test"
        assert len(result.runtime_sha256) == 64

    def test_two_writes_produce_the_same_manifest_snapshot(self, tmp_path) -> None:
        """Reproducibility: the same project must always describe itself the same."""
        writer = OnlyMapWriter(runtime_provider=FakeRuntime())
        first = writer.write(make_project(), tmp_path / "a")
        second = writer.write(make_project(), tmp_path / "b")
        assert first.manifest_snapshot == second.manifest_snapshot

    def test_lossy_transform_produces_a_warning(self, tmp_path) -> None:
        project = make_project(settings=ExportSettings(quantize_precision=6))
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(project, tmp_path)
        assert any("less precise" in w for w in result.warnings)

    def test_clean_export_has_no_warnings(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path
        )
        assert result.warnings == ()

    def test_title_is_escaped_in_the_document(self, tmp_path) -> None:
        project = make_project(title="Roads & <Paths>")
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(project, tmp_path)
        html = result.entry_path.read_text()
        assert "&amp;" in html
        assert "<Paths>" not in html

    def test_mode_is_recorded(self, tmp_path) -> None:
        result = OnlyMapWriter(runtime_provider=FakeRuntime()).write(
            make_project(), tmp_path, mode=OutputMode.STANDALONE_HTML
        )
        assert result.mode is OutputMode.STANDALONE_HTML


class TestRuntimeManager:
    def test_missing_runtime_raises_rather_than_degrading(self, tmp_path) -> None:
        """A placeholder runtime is a blank page for the recipient."""
        with pytest.raises(RuntimeUnavailableError):
            LocalRuntime(tmp_path).load()

    def test_missing_stylesheet_is_also_fatal(self, tmp_path) -> None:
        """Without the CSS the no-JavaScript fallback silently stops working."""
        (tmp_path / "onlymap.standalone.js").write_bytes(b"x")
        with pytest.raises(RuntimeUnavailableError, match="stylesheet"):
            LocalRuntime(tmp_path).load()

    def test_real_runtime_loads_when_present(self) -> None:
        directory = discover_runtime_dir()
        if directory is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        bundle = LocalRuntime(directory).load()
        assert bundle.total_bytes > 1_000_000
        assert bundle.version != ""
        assert len(bundle.sha256) == 64
