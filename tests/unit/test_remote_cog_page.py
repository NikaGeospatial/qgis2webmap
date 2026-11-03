"""A page whose only raster is read over the web still gets the raster stand-in.

The stand-in (`packaging/raster_embed.py`) used to be written only beside an
embedded raster, so a page with a remote `COGLayer` and nothing embedded drew
nothing from disk - its decode workers never started - and asked `epsg.io`
for EPSG:3857. It now comes with any `COGLayer`, carrying no raster; a page
with no `COGLayer` and a page with embedded rasters are unchanged.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    Extent,
    GeometryKind,
    RasterSpec,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
)
from nika_onlymap_exporter.core.license_policy import CapVerdict
from nika_onlymap_exporter.packaging.raster_embed import (
    RASTER_SCRIPT_TYPE,
    build_raster_embed,
    has_cog_layer,
)
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle, sha256_of
from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

URL = "https://example.org/ortho.tif"
WORKERS = 'if (location.protocol === "file:") {'


class FakeRuntime:
    def load(self) -> RuntimeBundle:
        js = b"/* fake runtime */"
        return RuntimeBundle(
            javascript=js, css=b"", version="0.0.0", sha256=sha256_of(js)
        )


def _page(*layers: ExportLayer) -> str:
    project = ExportProject(
        title="Remote",
        layers=layers,
        extent=Extent(west=11.0, south=52.0, east=12.0, north=53.0),
    )
    return OnlyMapWriter(runtime_provider=FakeRuntime()).render_html(
        project, FakeRuntime().load(), verdict=CapVerdict(allowed=True)
    )


def _points(name: str = "Points") -> ExportLayer:
    return ExportLayer(
        layer_id="pts",
        name=name,
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.FILE,
        feature_count=0,
        geojson={"type": "FeatureCollection", "features": []},
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=255, g=0, b=0)),
        ),
    )


def _remote() -> ExportLayer:
    return ExportLayer(
        layer_id="ortho",
        name="Ortho",
        geometry_kind=GeometryKind.RASTER,
        source_kind=SourceKind.SERVICE,
        renderer=RendererSpec(kind=RendererKind.UNSUPPORTED),
        raster=RasterSpec(
            path=URL,
            src=URL,
            remote=True,
            band_count=3,
            source_crs="EPSG:3857",
            extent=Extent(west=11.5, south=52.0, east=11.7, north=52.2),
            pixel_width=256,
            pixel_height=256,
            bands=(1, 2, 3),
        ),
    )


def test_a_page_with_only_a_remote_cog_gets_the_workers_and_web_mercator() -> None:
    page = _page(_points(), _remote())
    assert 'type="COGLayer"' in page and URL in page
    assert WORKERS in page
    assert "epsg\\.io" in page
    assert f'<script type="{RASTER_SCRIPT_TYPE}"' not in page


def test_the_stand_in_is_the_embedded_one_without_its_rasters() -> None:
    """One stand-in, not a second copy to keep in step."""
    embedded = build_raster_embed([("r.tif", b"0123")])
    block, _, stand_in = embedded.partition("\n")
    assert f'type="{RASTER_SCRIPT_TYPE}"' in block
    assert build_raster_embed((), cog_layer=True) == stand_in


def test_a_page_without_a_cog_layer_is_unchanged() -> None:
    assert build_raster_embed(()) == ""
    # A layer name cannot spell the attribute: it is escaped.
    page = _page(_points(name='type="COGLayer"'))
    assert not has_cog_layer(page)
    assert WORKERS not in page


def test_embedded_rasters_are_unchanged_by_a_cog_layer() -> None:
    embedded = [("r.tif", b"0123"), ("s.tif", b"4567")]
    assert build_raster_embed(embedded) == build_raster_embed(embedded, cog_layer=True)
