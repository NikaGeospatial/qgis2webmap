"""PMTiles archives and vector tile services, read from real QGIS layers.

QGIS opens a PMTiles archive through GDAL, one vector layer per layer of the
archive; the export reads it as tiles of that archive, carries a local archive
as each output type can, and keeps a remote one where it is. A vector tile
service added with its publisher's style is exported in that style - only
while QGIS still draws it that way: a restyled layer, or one whose style QGIS
kept no copy of, is left out with a row saying why. The archives and the
service are written and served locally (`tests/fake_tile_archive.py`,
`tests/fake_cog_server.py`), so nothing here needs the network.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fake_cog_server import FakeCogServer
from fake_tile_archive import (
    COMPRESSION_ZSTD,
    TILE_MVT,
    Feature,
    mvt,
    pmtiles,
    png,
    tiles_covering,
)

from nika_onlymap_exporter.core.export_ir import (
    ExportProject,
    ExportSettings,
    Extent,
    FidelityStatus,
    OutputMode,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer
from nika_onlymap_exporter.packaging.tile_archives import ARCHIVE_FETCH_NOTE

qgis_core = pytest.importorskip("qgis.core")

BOX = (8.0, 47.0, 8.6, 47.3)
WATER = [
    Feature(
        "polygon",
        [(8.0, 47.0), (8.4, 47.0), (8.4, 47.3), (8.0, 47.3), (8.0, 47.0)],
        {"kind": "lake"},
    )
]
ROADS = [Feature("line", [(7.9, 47.15), (8.7, 47.15)], {"class": "primary"})]
METADATA = json.dumps(
    {
        "vector_layers": [
            {"id": "water", "fields": {"kind": "String"}},
            {"id": "roads", "fields": {"class": "String"}},
        ]
    }
)


def _tiles(max_zoom: int = 6) -> dict[tuple[int, int, int], bytes]:
    return {
        index: mvt({"water": WATER, "roads": ROADS}, *index)
        for z in range(max_zoom + 1)
        for index in tiles_covering(*BOX, z)
    }


def _archive(path: Path, **options: int) -> Path:
    path.write_bytes(pmtiles(_tiles(), TILE_MVT, BOX, metadata=METADATA, **options))
    return path


def _archive_layer(source: str, layer_name: str = "water"):
    layer = qgis_core.QgsVectorLayer(f"{source}|layername={layer_name}", "Lakes", "ogr")
    assert layer.isValid(), source
    return layer


def _read(layer, **options):
    report = FidelityReportBuilder()
    return read_layer(layer, report, **options), report


def _rows(report, status: FidelityStatus) -> str:
    return " ".join(item.detail for item in report.items if item.status is status)


def _project(*layers) -> ExportProject:
    return ExportProject(
        title="Tiles",
        layers=tuple(layers),
        extent=Extent(west=BOX[0], south=BOX[1], east=BOX[2], north=BOX[3]),
        settings=ExportSettings(basemap="none"),
    )


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return _archive(tmp_path / "Lakes and Roads.pmtiles")


class TestPmtilesArchive:
    def test_a_layer_of_an_archive_is_read_as_its_tiles(
        self, qgis_app, archive
    ) -> None:
        exported, report = _read(_archive_layer(str(archive)))
        assert exported is not None
        assert exported.geojson is None
        tiles = exported.vector_tiles
        assert tiles is not None
        assert tiles.path == str(archive)
        assert tiles.source_layer == "water"
        assert (tiles.min_zoom, tiles.max_zoom) == (0, 6)
        assert "tile by tile" in _rows(report, FidelityStatus.PRESERVED)

    def test_the_folder_carries_the_archive_once_and_draws_each_layer_from_it(
        self, qgis_app, archive, tmp_path: Path, runtime_required
    ) -> None:
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        water, _ = _read(_archive_layer(str(archive), "water"))
        roads, _ = _read(_archive_layer(str(archive), "roads"))
        out = tmp_path / "folder"
        result = OnlyMapWriter().write(
            _project(water, roads), out, mode=OutputMode.FOLDER
        )
        carried = sorted(path.name for path in out.glob("*.pmtiles"))
        assert carried == ["archive-0-lakes-and-roads.pmtiles"]
        assert (out / carried[0]).read_bytes() == archive.read_bytes()
        page = result.entry_path.read_text(encoding="utf-8")
        assert page.count('type="MVTLayer"') == 2
        assert page.count('data="archive-0-lakes-and-roads.pmtiles"') == 2
        assert "{&quot;mvt&quot;: {&quot;layers&quot;: [&quot;water&quot;]}}" in page
        assert "{&quot;mvt&quot;: {&quot;layers&quot;: [&quot;roads&quot;]}}" in page
        assert str(archive) not in page
        # The archive is read where it is, not copied as features.
        assert '"type":"FeatureCollection"' not in page

    def test_a_zip_says_it_has_to_be_served(
        self, qgis_app, archive, tmp_path: Path, runtime_required
    ) -> None:
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        water, _ = _read(_archive_layer(str(archive)))
        result = OnlyMapWriter().write(
            _project(water), tmp_path / "zip", mode=OutputMode.SHARE_ZIP
        )
        notes = [item.detail for item in result.fidelity]
        assert ARCHIVE_FETCH_NOTE in notes

    def test_a_single_file_carries_the_archive_inside_the_page(
        self, qgis_app, archive, tmp_path: Path, runtime_required
    ) -> None:
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        water, _ = _read(_archive_layer(str(archive)))
        out = tmp_path / "single"
        result = OnlyMapWriter().write(_project(water), out)
        assert not list(out.glob("*.pmtiles"))
        page = result.entry_path.read_text(encoding="utf-8")
        assert 'data-src="archive-0-lakes-and-roads.pmtiles"' in page
        assert any("carried inside the page" in item.detail for item in result.fidelity)

    def test_an_archive_a_browser_cannot_decompress_is_left_out(
        self, qgis_app, tmp_path: Path
    ) -> None:
        # GDAL reads zstd tiles, so QGIS adds the layer; no browser can.
        zstd = _archive(tmp_path / "zstd.pmtiles", tile_compression=COMPRESSION_ZSTD)
        exported, report = _read(_archive_layer(str(zstd)))
        assert exported is None
        assert "compressed with zstd" in _rows(report, FidelityStatus.UNSUPPORTED)

    def test_what_needs_features_is_reported(self, qgis_app, archive) -> None:
        layer = _archive_layer(str(archive))
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "kind"
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        exported, report = _read(layer, filter_field="kind")
        assert exported is not None
        unsupported = [
            item.subject
            for item in report.items
            if item.status is FidelityStatus.UNSUPPORTED
        ]
        assert "Labels on 'Lakes'" in unsupported
        assert "Filter on 'Lakes'" in unsupported

    def test_a_remote_archive_stays_where_it_is(self, qgis_app, archive) -> None:
        with FakeCogServer({"/lakes.pmtiles": archive.read_bytes()}) as host:
            layer = _archive_layer(f"/vsicurl/{host.url('/lakes.pmtiles')}")
            exported, _report = _read(layer)
        assert exported is not None
        tiles = exported.vector_tiles
        assert tiles is not None
        assert tiles.url == host.url("/lakes.pmtiles")
        assert not tiles.is_archive
        assert tiles.max_zoom == 6


# --------------------------------------------------------------------------
# Vector tile services
# --------------------------------------------------------------------------

STYLE_LAYERS = [
    {"id": "bg", "type": "background", "paint": {"background-color": "#ffffff"}},
    {
        "id": "lakes",
        "type": "fill",
        "source": "base",
        "source-layer": "water",
        "paint": {"fill-color": "#00c853"},
    },
    {
        "id": "roads",
        "type": "line",
        "source": "base",
        "source-layer": "roads",
        "paint": {"line-color": "#0000ff", "line-width": 6},
    },
    {
        "id": "names",
        "type": "symbol",
        "source": "base",
        "source-layer": "water",
        "layout": {"icon-image": "dot", "text-field": "{kind}"},
    },
]


@pytest.fixture(scope="module")
def service():
    files = {f"/t/{z}/{x}/{y}.pbf": data for (z, x, y), data in _tiles(5).items()}
    sprite = json.dumps({"dot": {"x": 0, "y": 0, "width": 16, "height": 16}})
    for suffix in ("", "@2x"):
        files[f"/sprite{suffix}.json"] = sprite.encode()
        files[f"/sprite{suffix}.png"] = png((255, 0, 0), 16)
    with FakeCogServer(files) as host:
        style = {
            "version": 8,
            "sprite": host.url("/sprite"),
            "sources": {
                "base": {"type": "vector", "tiles": [host.url("/t/{z}/{x}/{y}.pbf")]}
            },
            "layers": STYLE_LAYERS,
        }
        files["/style.json"] = json.dumps(style).encode()
        files["/never-fetched.json"] = files["/style.json"]
        yield host


def _service_layer(host: FakeCogServer, style: str = "/style.json", load=True):
    source = (
        f"styleUrl={quote(host.url(style), safe='')}&type=xyz"
        f"&url={quote(host.url('/t/{z}/{x}/{y}.pbf'), safe='')}&zmax=5&zmin=0"
    )
    layer = qgis_core.QgsVectorTileLayer(source, "Base")
    assert layer.isValid()
    if load:
        # What QGIS does when the layer is added from its connection.
        _message, loaded = layer.loadDefaultStyle()
        assert loaded
    return layer


class TestVectorTileService:
    def test_a_layer_in_its_publishers_style_draws_in_it(
        self, qgis_app, service
    ) -> None:
        layer = _service_layer(service)
        service.requests.clear()
        exported, report = _read(layer)
        assert exported is not None
        tiles = exported.vector_tiles
        assert tiles is not None
        assert tiles.url == service.url("/t/{z}/{x}/{y}.pbf")
        assert tiles.style_url == service.url("/style.json")
        assert (tiles.min_zoom, tiles.max_zoom) == (0, 5)
        assert "publisher's style" in _rows(report, FidelityStatus.PRESERVED)
        approximated = _rows(report, FidelityStatus.APPROXIMATED)
        assert "background layers are not drawn" in approximated
        assert "browser's own fonts" in approximated
        # Read from QGIS's cache: exporting contacts nobody.
        assert service.requests == []

    def test_the_element_names_the_style(self, qgis_app, service) -> None:
        from nika_onlymap_exporter.core.manifest_builder import build_layer_element

        exported, _report = _read(_service_layer(service))
        assert exported is not None
        element = build_layer_element(exported)
        assert 'type="MVTLayer"' in element
        assert f'vector-style="{service.url("/style.json")}"' in element
        assert 'max-zoom="5"' in element
        # Drawn by the style, not by colours of ours.
        assert "get-fill-color" not in element

    def test_a_restyled_layer_is_left_out_rather_than_overwritten(
        self, qgis_app, service
    ) -> None:
        layer = _service_layer(service)
        renderer = layer.renderer().clone()
        styles = renderer.styles()
        lakes = styles[1]
        symbol = lakes.symbol().clone()
        symbol.setColor(symbol.color().darker())
        lakes.setSymbol(symbol)
        styles[1] = lakes
        renderer.setStyles(styles)
        layer.setRenderer(renderer)
        exported, report = _read(layer)
        assert exported is None
        assert "styled differently from its publisher's style" in _rows(
            report, FidelityStatus.UNSUPPORTED
        )

    def test_a_restyled_label_counts_as_restyled(self, qgis_app, service) -> None:
        layer = _service_layer(service)
        layer.setLabeling(None)
        exported, _report = _read(layer)
        assert exported is None

    def test_a_style_qgis_kept_no_copy_of_is_left_out(self, qgis_app, service) -> None:
        layer = _service_layer(service, "/never-fetched.json", load=False)
        exported, report = _read(layer)
        assert exported is None
        assert "no longer has a copy" in _rows(report, FidelityStatus.UNSUPPORTED)
        assert ("GET", "/never-fetched.json", None) not in service.requests

    def test_a_layer_without_a_style_address_is_left_out(
        self, qgis_app, service
    ) -> None:
        source = (
            f"type=xyz&url={quote(service.url('/t/{z}/{x}/{y}.pbf'), safe='')}"
            "&zmax=5&zmin=0"
        )
        exported, report = _read(qgis_core.QgsVectorTileLayer(source, "Bare"))
        assert exported is None
        assert "has none on the web" in _rows(report, FidelityStatus.UNSUPPORTED)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_a_government_style_reads_as_unchanged(qgis_app) -> None:
    """swisstopo's light base map style: two tile sets named by TileJSON,
    expressions, zoom functions and labels. Its addresses are moved to the
    test's server, which has no sprites, as QGIS would find them offline."""
    style = json.loads(
        (FIXTURES / "swisstopo-lightbasemap-style.json").read_text(encoding="utf-8")
    )
    files: dict[str, bytes] = {}
    with FakeCogServer(files) as host:
        prefix = "https://vectortiles.geo.admin.ch/"
        text = json.dumps(style).replace(prefix, host.url("/"))
        files["/styles/style.json"] = text.encode()
        moved = json.loads(text)
        base = moved["sources"]["base_v1.0.0"]["url"].rsplit("/", 1)[0]
        source = (
            f"styleUrl={quote(host.url('/styles/style.json'), safe='')}&type=xyz"
            f"&url={quote(base + '/{z}/{x}/{y}.pbf', safe='')}&zmax=14&zmin=0"
        )
        layer = qgis_core.QgsVectorTileLayer(source, "swisstopo")
        _message, loaded = layer.loadDefaultStyle()
        assert loaded
        exported, report = _read(layer)
    assert exported is not None, [item.detail for item in report.items]
    tiles = exported.vector_tiles
    assert tiles is not None
    # The second of the style's two tile sets.
    assert tiles.style_source == "base_v1.0.0"
    assert "background layers are not drawn" in _rows(
        report, FidelityStatus.APPROXIMATED
    )
