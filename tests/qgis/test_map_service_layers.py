"""WMS and WMTS layers, read from real QGIS layers on a local fake service.

QGIS will not even create a WMS layer without a service answering its
GetCapabilities, so `tests/fake_map_services.py` runs one on 127.0.0.1 for the
module. What these pin is the QGIS side: how QGIS writes the source string,
that the capabilities it fetched are found again in its cache without a
request, and what the Fidelity tab says for each outcome. The template logic
itself is unit-tested in `tests/unit/test_map_services.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from nika_onlymap_exporter.core.export_ir import (
    AssetDisposition,
    ExportProject,
    FidelityStatus,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer

qgis_core = pytest.importorskip("qgis.core")

# Nothing listens on the discard port: QGIS's capabilities request fails at
# once, which is what a project opened offline looks like.
OFFLINE = "http://127.0.0.1:9/wms"


@pytest.fixture(scope="module")
def services():
    from fake_map_services import FakeMapServices

    with FakeMapServices() as running:
        yield running


def _wms(
    url: str,
    layers=("target",),
    crs="EPSG:25832",
    extra="",
    name="WMS",
    image_format="image/png",
):
    source = (
        f"crs={crs}&dpiMode=7&format={quote(image_format, safe='')}"
        + "".join(f"&layers={quote(layer)}" for layer in layers)
        + "&styles" * len(layers)
        + extra
        + f"&url={quote(url, safe='')}"
    )
    return qgis_core.QgsRasterLayer(source, name, "wms")


def _wmts(url: str, matrix_set="GoogleMapsCompatible", extra=""):
    capabilities = quote(f"{url}?SERVICE=WMTS&REQUEST=GetCapabilities", safe="")
    source = (
        f"contextualWMSLegend=0&crs=EPSG:3857&dpiMode=7&format=image/png"
        f"&layers=target&styles=default&tileMatrixSet={matrix_set}{extra}"
        f"&url={capabilities}"
    )
    return qgis_core.QgsRasterLayer(source, "WMTS", "wms")


def _read(layer):
    report = FidelityReportBuilder()
    return read_layer(layer, report), report


def _rows(report, status: FidelityStatus) -> str:
    return " ".join(item.detail for item in report.items if item.status is status)


class TestWms:
    def test_a_130_layer_in_a_projected_crs_becomes_a_web_mercator_template(
        self, qgis_app, services
    ) -> None:
        """Issue #2: an EPSG:25832 project's WMS, asked for EPSG:3857 tiles."""
        layer = _wms(services.url("cors/wms"))
        assert layer.isValid()
        exported, report = _read(layer)

        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.url == (
            services.url("cors/wms") + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
            "&LAYERS=target&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE"
            "&CRS=EPSG:3857&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-3857}"
        )
        assert exported.tiles.tile_size == 512
        assert exported.source_kind is SourceKind.SERVICE
        (dependency,) = exported.dependencies
        assert dependency.disposition is AssetDisposition.REMOTE
        assert dependency.identifier == exported.tiles.url
        row = _rows(report, FidelityStatus.APPROXIMATED)
        assert "EPSG:3857" in row and "EPSG:25832" in row
        assert "CORS" in row

    def test_a_111_service_is_asked_with_srs(self, qgis_app, services) -> None:
        layer = _wms(services.url("cors/wms111"))
        assert layer.isValid()
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert "VERSION=1.1.1" in exported.tiles.url
        assert "&SRS=EPSG:3857&" in exported.tiles.url
        assert "CRS=" not in exported.tiles.url

    def test_reading_never_contacts_the_service(self, qgis_app, services) -> None:
        layer = _wms(services.url("cors/wms"))
        before = len(services.requests)
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert len(services.requests) == before

    def test_a_service_without_web_mercator_is_refused(
        self, qgis_app, services
    ) -> None:
        layer = _wms(services.url("cors/wms"), layers=("utm_only",))
        assert layer.isValid()
        exported, report = _read(layer)
        assert exported is None
        assert "does not offer Web Mercator (EPSG:3857)" in _rows(
            report, FidelityStatus.UNSUPPORTED
        )

    def test_without_capabilities_a_projected_layer_is_refused(self, qgis_app) -> None:
        """Offline and never cached: there is nothing to tell whether the
        service offers EPSG:3857, so the export does not guess."""
        exported, report = _read(_wms(OFFLINE))
        assert exported is None
        assert "no stored copy" in _rows(report, FidelityStatus.UNSUPPORTED)

    def test_without_capabilities_a_web_mercator_layer_still_exports(
        self, qgis_app
    ) -> None:
        """QGIS only offers a CRS the service lists, so a layer saved in
        EPSG:3857 is evidence enough; the version is the source's or 1.3.0."""
        exported, _ = _read(_wms(OFFLINE, crs="EPSG:3857", extra="&Version=1.1.1"))
        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.url.startswith(OFFLINE + "?SERVICE=WMS&VERSION=1.1.1&")
        assert "&SRS=EPSG:3857&" in exported.tiles.url

    def test_the_issue_2_source_string_reads(self, qgis_app, services) -> None:
        """The reporter's exact keys, `Request=GetMap&Version=1.3.0` among them."""
        source = (
            "Request=GetMap&Version=1.3.0&crs=EPSG%3A25832&dpiMode=7"
            "&format=image%2Fpng&layers=target&stepHeight=3000&stepWidth=3000"
            f"&styles&tilePixelRatio=0&url={quote(services.url('cors/wms'), safe='')}"
        )
        layer = qgis_core.QgsRasterLayer(source, "ST: ALKIS Gebäude (WMS)", "wms")
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert "LAYERS=target&" in exported.tiles.url
        assert "stepHeight" not in exported.tiles.url

    def test_opacity_scale_range_and_attribution_travel(
        self, qgis_app, services
    ) -> None:
        layer = _wms(services.url("cors/wms"))
        layer.setOpacity(0.4)
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(50_000)
        layer.setMaximumScale(1_000)
        layer.serverProperties().setAttribution("© GeoBasis-DE / Fake")
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.opacity == pytest.approx(0.4)
        assert exported.scale_range.min_scale == 50_000
        assert exported.attribution == "© GeoBasis-DE / Fake"

    def test_a_cartograph_frame_counts_it_as_imagery(self, qgis_app, services) -> None:
        """Image tiles are what a projected live frame can warp into its
        plane; a WMS names its format as a parameter, not an extension."""
        from nika_onlymap_exporter.core.cartograph_export import (
            is_image_tile_template,
        )

        exported, _ = _read(_wms(services.url("cors/wms")))
        assert exported is not None
        assert exported.tiles is not None
        assert is_image_tile_template(exported.tiles.url)

    def test_a_non_image_format_is_refused(self, qgis_app, services) -> None:
        layer = _wms(services.url("cors/wms"), image_format="text/html")
        exported, report = _read(layer)
        assert exported is None
        assert "not as images" in _rows(report, FidelityStatus.UNSUPPORTED)


class TestCredentials:
    """No login travels, and no key reaches the model or the report."""

    @pytest.mark.parametrize(
        "extra",
        [
            "&username=bob&password=SENTINEL-pw",
            "&authcfg=SENTINEL1",
            "&http-header:X-Api-Key=SENTINEL-pw",
            "&referer=https://SENTINEL.example",
        ],
    )
    def test_a_login_refuses_the_layer(self, qgis_app, services, extra) -> None:
        exported, report = _read(_wms(services.url("cors/wms"), extra=extra))
        assert exported is None
        assert "login" in _rows(report, FidelityStatus.UNSUPPORTED)
        assert "SENTINEL" not in repr([(i.subject, i.detail) for i in report.items])

    def test_a_key_in_the_address_blocks(self, qgis_app, services) -> None:
        """`IgnoreGetMapUrl` makes QGIS (and so the map) ask the layer's own
        address, key and all, rather than the one the capabilities name."""
        layer = _wms(
            services.url("cors/wms") + "?token=SENTINEL-pw",
            crs="EPSG:3857",
            extra="&IgnoreGetMapUrl=1",
        )
        exported, report = _read(layer)
        assert exported is not None
        (dependency,) = exported.dependencies
        assert dependency.disposition is AssetDisposition.BLOCKING
        assert "access key" in (dependency.note or "")
        rendered = repr(exported.snapshot()) + repr(dependency) + repr(report.items)
        assert "SENTINEL" not in rendered


class TestTime:
    def test_a_wms_t_following_qgis_time_is_refused(self, qgis_app, services) -> None:
        layer = _wms(
            services.url("cors/wms"),
            extra="&type=wmst&timeDimensionExtent=2020-01-01/2020-12-31/P1D",
        )
        exported, report = _read(layer)
        assert exported is None
        assert "Server default" in _rows(report, FidelityStatus.UNSUPPORTED)

    def test_a_wms_t_on_the_server_default_exports(self, qgis_app, services) -> None:
        """`allowTemporalUpdates=false` is the Server default choice: QGIS
        sends no TIME, and neither does the map."""
        layer = _wms(
            services.url("cors/wms"),
            extra="&type=wmst&allowTemporalUpdates=false"
            "&timeDimensionExtent=2020-01-01/2020-12-31/P1D",
        )
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert "TIME=" not in exported.tiles.url


class TestWmts:
    def test_the_google_grid_becomes_a_tile_template(self, qgis_app, services) -> None:
        layer = _wmts(services.url("cors/wmts"))
        assert layer.isValid()
        exported, report = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.url == (
            services.url("cors/wmts") + "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            "&LAYER=target&STYLE=default&FORMAT=image/png"
            "&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}"
            "&TILECOL={x}"
        )
        assert (exported.tiles.min_zoom, exported.tiles.max_zoom) == (0, 18)
        assert exported.tiles.tile_size == 256
        assert "CORS" in _rows(report, FidelityStatus.APPROXIMATED)

    def test_another_grid_is_refused_by_name(self, qgis_app, services) -> None:
        layer = _wmts(services.url("cors/wmts"), matrix_set="UTM32")
        assert layer.isValid()
        exported, report = _read(layer)
        assert exported is None
        assert "tile grid 'UTM32'" in _rows(report, FidelityStatus.UNSUPPORTED)


class TestOtherServices:
    def test_an_arcgis_map_service_says_what_to_do(self, qgis_app) -> None:
        layer = qgis_core.QgsRasterLayer(
            "crs='EPSG:3857' format='PNG32' layer='0' url='http://127.0.0.1:9/rest'",
            "ArcGIS",
            "arcgismapserver",
        )
        exported, report = _read(layer)
        assert exported is None
        assert "add the layer that way" in _rows(report, FidelityStatus.UNSUPPORTED)


def test_the_scan_says_it_needs_the_internet(qgis_app, services) -> None:
    """The Fidelity rows a recipient-facing export shows for a WMS layer."""
    from nika_onlymap_exporter.packaging.dependency_scanner import scan

    exported, report = _read(_wms(services.url("cors/wms")))
    assert exported is not None
    result = scan(ExportProject(title="t", layers=(exported,)), report)
    assert result.can_export
    assert "127.0.0.1" in " ".join(result.remote_dependencies)
    assert "needs an internet connection" in _rows(report, FidelityStatus.APPROXIMATED)
