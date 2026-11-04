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

from urllib.parse import parse_qsl, quote, unquote, urlsplit

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
    styles=None,
):
    source = (
        f"crs={crs}&dpiMode=7&format={quote(image_format, safe='')}"
        + "".join(f"&layers={quote(layer)}" for layer in layers)
        + (
            "".join(f"&styles={quote(style)}" for style in styles)
            if styles is not None
            else "&styles" * len(layers)
        )
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


def _read(layer, map_time=None):
    report = FidelityReportBuilder()
    return read_layer(layer, report, map_time=map_time), report


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


def _qgis_get_map(services, layer) -> tuple[str, str]:
    """The LAYERS and STYLES QGIS itself sends when it draws `layer` in Web
    Mercator: the ground truth, from what reached the service."""
    from qgis.PyQt.QtCore import QSize

    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setExtent(qgis_core.QgsRectangle(*TARGET))
    settings.setOutputSize(QSize(32, 32))
    before = len(services.requests)
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    (get_map,) = [
        r for r in services.requests[before:] if r["params"].get("REQUEST") == "GetMap"
    ]
    return get_map["params"]["LAYERS"], get_map["params"]["STYLES"]


# Around `fake_map_services.TARGET_3857`, where every sublayer draws.
TARGET = (1280174, 6813860, 1308004, 6841175)


class TestSublayerOrder:
    """A WMS layer of several sublayers stacks them as QGIS does: the web map
    asks for LAYERS and STYLES in the order QGIS's own GetMap does."""

    @pytest.mark.parametrize("service", ["cors/wms", "cors/wms111"])
    @pytest.mark.parametrize(
        ("layers", "styles"),
        [
            (("target", "boundary"), ("", "")),
            (("target", "boundary"), ("outlined", "")),
            (("target", "boundary", "utm_only"), ("outlined", "", "")),
            (("boundary", "target", "utm_only"), ("", "outlined", "")),
        ],
    )
    def test_the_template_asks_as_qgis_asks(
        self, qgis_app, services, service, layers, styles
    ) -> None:
        made = _wms(
            services.url(service), layers=layers, styles=styles, crs="EPSG:3857"
        )
        # And the same layer read back from its own source string, which QGIS
        # 4.2 lists the other way round: a project saved and opened again.
        for layer in (made, qgis_core.QgsRasterLayer(made.source(), "again", "wms")):
            assert layer.isValid()
            exported, _ = _read(layer)
            assert exported is not None
            assert exported.tiles is not None
            ours = dict(parse_qsl(urlsplit(exported.tiles.url).query))

            assert (ours["LAYERS"], ours["STYLES"]) == _qgis_get_map(services, layer)

    def test_the_legend_lists_the_top_sublayer_first(self, qgis_app, services) -> None:
        """Top first, as a legend lists layers - the reverse of the GetMap,
        which names the bottom one first - each picture in its own style."""
        layer = _wms(
            services.url("cors/wms"),
            layers=("target", "boundary", "utm_only"),
            styles=("outlined", "", ""),
            crs="EPSG:3857",
        )
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        asked_layers, asked_styles = _qgis_get_map(services, layer)
        drawn = dict(zip(asked_layers.split(","), asked_styles.split(","), strict=True))

        listed = []
        for image in exported.tiles.legend:
            query = dict(parse_qsl(urlsplit(image.url).query, keep_blank_values=True))
            listed.append((query["LAYER"], query["STYLE"]))
        assert [name for name, _ in listed] == asked_layers.split(",")[::-1]
        assert all(drawn[name] == style for name, style in listed)


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


def _qgis_legend_request(layer):
    """The legend address QGIS itself asks for `layer`'s legend, and the
    picture it got: the ground truth. Taken from QGIS's network log, since
    QGIS may answer from its cache without reaching the service."""
    asked: list[str] = []

    def log(parameters) -> None:
        asked.append(parameters.request().url().toString())

    from qgis.PyQt.QtCore import QEventLoop, QTimer
    from qgis.PyQt.QtGui import QImage

    got: list[QImage] = []
    loop = QEventLoop()
    # The overload that hands over the parameters; the older one, of three
    # arguments, is the default and would call `log` wrongly.
    created = qgis_core.QgsNetworkAccessManager.instance().requestAboutToBeCreated[
        qgis_core.QgsNetworkRequestParameters
    ]
    created.connect(log)
    # What QGIS's legend node does: the provider's fetcher, with no map view.
    fetcher = layer.dataProvider().getLegendGraphicFetcher(None)
    try:
        fetcher.finish.connect(lambda image: (got.append(image), loop.quit()))
        fetcher.error.connect(lambda _message: loop.quit())
        QTimer.singleShot(15_000, loop.quit)
        fetcher.start()
        if not got:
            loop.exec()
    finally:
        created.disconnect(log)
    (url,) = asked
    return url, got[0] if got else QImage()


def _same_request(url: str):
    """`url` as path and parameters, decoded, in order."""
    parts = urlsplit(url)
    return parts.netloc, parts.path, parse_qsl(parts.query, keep_blank_values=True)


def _as_qgis_asks(url: str, layer):
    """`_same_request` of the legend address QGIS asked for `layer`, its
    FORMAT as QGIS 4 asks it. QGIS 3.44 writes the format its source string
    holds into the request as it stands, still percent-encoded where the
    source is ("image%2Fpng"), so the service gets the code, not the format;
    the map asks the format it names, as QGIS 4 does."""
    netloc, path, query = _same_request(url)
    if qgis_core.Qgis.QGIS_VERSION_INT >= 40000:
        return netloc, path, query
    stored = dict(
        part.split("=", 1) for part in layer.source().split("&") if "=" in part
    )["format"]
    asked = []
    for key, value in query:
        if key == "FORMAT":
            assert value == stored
            value = unquote(value)
        asked.append((key, value))
    return netloc, path, asked


class TestLegend:
    """The legend picture the map shows is the one QGIS's legend shows."""

    @pytest.mark.parametrize(
        ("service", "styles"),
        [
            ("cors/wms", None),
            ("cors/wms", ("outlined",)),
            ("cors/wms", ("default",)),
            ("cors/wms111", None),
            ("cors/wms111", ("outlined",)),
        ],
    )
    def test_it_is_the_address_qgis_asks(
        self, qgis_app, services, service, styles
    ) -> None:
        from fake_map_services import LEGEND_SIZES, OUTLINED_LEGEND_SIZE

        layer = _wms(services.url(service), crs="EPSG:3857", styles=styles)
        assert layer.isValid()
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        (image,) = exported.tiles.legend
        assert image.label == ""

        qgis_url, picture = _qgis_legend_request(layer)
        assert _same_request(image.url) == _as_qgis_asks(qgis_url, layer)
        outlined = styles == ("outlined",)
        expected = OUTLINED_LEGEND_SIZE if outlined else LEGEND_SIZES["target"]
        assert (picture.width(), picture.height()) == expected
        assert ("/legends/outlined.png" in image.url) is outlined

    def test_reading_the_legend_never_contacts_the_service(
        self, qgis_app, services
    ) -> None:
        layer = _wms(services.url("cors/wms"), crs="EPSG:3857")
        before = len(services.requests)
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.legend
        assert len(services.requests) == before

    def test_each_sublayer_has_its_own_picture(self, qgis_app, services) -> None:
        """QGIS's legend shows one sublayer's picture - the top one's on
        QGIS 4, the bottom one's on 3.44; the map's lists the top one first
        and shows that one exactly so, and the other one's beside it."""
        layer = _wms(
            services.url("cors/wms"),
            layers=("target", "boundary"),
            styles=("outlined", ""),
            crs="EPSG:3857",
        )
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        first, second = exported.tiles.legend
        qgis_url, _ = _qgis_legend_request(layer)
        shown = first if qgis_core.Qgis.QGIS_VERSION_INT >= 40000 else second
        assert _same_request(shown.url) == _as_qgis_asks(qgis_url, layer)
        # In the source string's order, which QGIS's own sublayer list
        # follows; each under the title the capabilities give it.
        names = {"target": "Target area", "boundary": "Boundary"}
        for image in (first, second):
            named = dict(parse_qsl(urlsplit(image.url).query))["LAYER"]
            assert image.label == names.pop(named)
        assert names == {}

    def test_a_wmts_styles_legend_url(self, qgis_app, services) -> None:
        layer = _wmts(services.url("cors/wmts"))
        assert layer.isValid()
        exported, _ = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        (image,) = exported.tiles.legend
        assert image.url.startswith(services.url("cors/legends/outlined.png"))
        qgis_url, picture = _qgis_legend_request(layer)
        assert _same_request(image.url) == _as_qgis_asks(qgis_url, layer)
        assert not picture.isNull()

    def test_a_key_never_reaches_the_legend(self, qgis_app, services) -> None:
        """A layer asked at its own keyed address (`IgnoreGetMapUrl`): its
        legend would be asked there too, so the picture is left out."""
        layer = _wms(
            services.url("cors/wms") + "?token=SENTINEL-pw",
            crs="EPSG:3857",
            extra="&IgnoreGetMapUrl=1",
        )
        exported, report = _read(layer)
        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.legend == ()
        assert "access key" in " ".join(
            item.detail
            for item in report.items
            if item.subject == "Legend picture of 'WMS'"
            and item.status is FidelityStatus.UNSUPPORTED
        )
        assert "SENTINEL" not in repr(exported.snapshot()) + repr(report.items)

    @pytest.mark.parametrize(
        "address",
        [
            "file:///home/someone/secret.png",
            "data:image/png;base64,iVBORw0KGgo=",
            "javascript:alert(1)",
            "legends/outlined.png",
        ],
    )
    def test_only_a_web_address_reaches_the_legend(self, qgis_app, address) -> None:
        """The capabilities name the address, and nothing makes it a web one:
        only http and https reach the legend, whose `<img src>` it becomes."""
        from nika_onlymap_exporter.core.layer_reader import _service_legend

        report = FidelityReportBuilder()
        web = "https://maps.example.org/legend.png"
        images = _service_legend(
            "WMS", [("Target", web), ("Other", address)], report, "wms"
        )

        assert [image.url for image in images] == [web]
        (row,) = report.items
        assert row.status is FidelityStatus.UNSUPPORTED
        assert row.subject == "Legend picture of 'WMS'"
        assert "not a web address" in row.detail
        assert address not in row.detail

    def test_a_contextual_legend_is_known(self, qgis_app, services) -> None:
        on = _wms(
            services.url("cors/wms"), crs="EPSG:3857", extra="&contextualWMSLegend=1"
        )
        off = _wms(
            services.url("cors/wms"), crs="EPSG:3857", extra="&contextualWMSLegend=0"
        )
        for layer, contextual in ((on, True), (off, False)):
            exported, _ = _read(layer)
            assert exported is not None
            assert exported.tiles is not None
            assert exported.tiles.contextual_legend is contextual

    def test_without_capabilities_there_is_no_legend(self, qgis_app) -> None:
        """Nothing says where the service draws its legend, so no guess."""
        exported, report = _read(_wms(OFFLINE, crs="EPSG:3857"))
        assert exported is not None
        assert exported.tiles is not None
        assert exported.tiles.legend == ()
        assert not [i for i in report.items if i.subject.startswith("Legend")]


class TestCartographLegend:
    """A cartograph's legend is the runtime's `<om-legend>`, whose rows are a
    swatch and a label: the WMS layer keeps its row, and the report says the
    service's picture is not in it."""

    def _report(self, project, services, legend_hides_it=False):
        from atlas_fixtures import add_map, build_parcels
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsLayoutItemLegend,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsUnitTypes,
        )

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle
        from nika_onlymap_exporter.qt_compat import set_legend_manual

        project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        parcels = build_parcels()
        wms = _wms(services.url("cors/wms"), crs="EPSG:3857")
        project.addMapLayers([parcels, wms])
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        map_item = add_map(layout, [parcels, wms])
        legend = QgsLayoutItemLegend(layout)
        layout.addLayoutItem(legend)
        legend.setLinkedMap(map_item)
        legend.attemptResize(QgsLayoutSize(80, 60, QgsUnitTypes.LayoutMillimeters))
        if legend_hides_it:
            set_legend_manual(legend)
            legend.model().rootGroup().removeLayer(wms)
        runtime = RuntimeBundle(
            javascript=b"// core js placeholder",
            css=b"/* core css placeholder */",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"// cartograph js placeholder",
            cartograph_css=b"/* cartograph css placeholder */",
        )
        report = FidelityReportBuilder()
        export_layout_to_cartograph(layout, project, runtime, report)
        return report

    def test_the_legend_says_the_picture_is_not_shown(self, project, services) -> None:
        report = self._report(project, services)
        rows = [
            item.detail
            for item in report.items
            if item.subject == "Legend 'WMS'"
            and item.status is FidelityStatus.APPROXIMATED
        ]
        assert rows and "cannot show a picture" in rows[0]
        # The web map's row is for the web map's legend, which this is not.
        assert not [i for i in report.items if i.subject == "Legend picture of 'WMS'"]

    def test_a_legend_without_the_layer_says_nothing(self, project, services) -> None:
        report = self._report(project, services, legend_hides_it=True)
        assert not [i for i in report.items if i.subject == "Legend 'WMS'"]


YEAR_EXTENT = (
    "&type=wmst&timeDimensionExtent=2020-01-01T00:00:00Z/2020-12-31T00:00:00Z/P1D"
)


def _utc(day: int, hour: int = 0):
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone

    return QDateTime(QDate(2020, 3, day), QTime(hour, 0), QTimeZone.utc())


def _no_time():
    from qgis.PyQt.QtCore import QDateTime

    return qgis_core.QgsDateTimeRange(QDateTime(), QDateTime())


def _qgis_time(services, layer, map_time) -> str | None:
    """The TIME QGIS itself sends when it draws `layer` with the map set to
    `map_time`, or None when it sends none: the ground truth."""
    from qgis.PyQt.QtCore import QSize

    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setExtent(qgis_core.QgsRectangle(1280174, 6813860, 1308004, 6841175))
    settings.setOutputSize(QSize(32, 32))
    if map_time.is_temporal:
        settings.setIsTemporal(True)
        settings.setTemporalRange(map_time.range)
    before = len(services.requests)
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    (get_map,) = [
        r for r in services.requests[before:] if r["params"].get("REQUEST") == "GetMap"
    ]
    return get_map["params"].get("TIME")


class TestTime:
    """A WMS-T layer that follows QGIS's time is a snapshot of the time QGIS
    draws: the map must ask the service for exactly the TIME QGIS sends."""

    @pytest.mark.parametrize(
        ("extra", "temporal", "method"),
        [
            ("", True, None),
            ("", True, "MatchExactUsingEndOfRange"),
            ("&enableTime=false", True, None),
            ("&time=2020-02-01T00:00:00Z/2020-02-03T00:00:00Z", False, None),
            ("&time=2020-02-01T00:00:00Z/2020-02-03T00:00:00Z", True, None),
            ("", False, None),
            # Past the service's own extent: QGIS still asks, and so does the map.
            ("", "2031", None),
        ],
    )
    def test_the_map_asks_for_the_time_qgis_draws(
        self, qgis_app, services, extra, temporal, method
    ) -> None:
        from nika_onlymap_exporter.core.layer_reader import MapTime

        layer = _wms(
            services.url("cors/wms"), crs="EPSG:3857", extra=YEAR_EXTENT + extra
        )
        assert layer.isValid()
        if method is not None:
            layer.temporalProperties().setIntervalHandlingMethod(
                getattr(qgis_core.Qgis.TemporalIntervalMatchMethod, method)
            )
        if temporal == "2031":
            later = _utc(5).addYears(11)
            map_time = MapTime(True, qgis_core.QgsDateTimeRange(later, later))
        else:
            map_time = MapTime(
                is_temporal=temporal,
                range=qgis_core.QgsDateTimeRange(_utc(5), _utc(6))
                if temporal
                else _no_time(),
            )
        before = len(services.requests)
        exported, report = _read(layer, map_time=map_time)
        assert len(services.requests) == before, "reading contacted the service"
        expected = _qgis_time(services, layer, map_time)

        assert exported is not None
        assert exported.tiles is not None
        asked = dict(parse_qsl(urlsplit(exported.tiles.url).query)).get("TIME")
        assert asked == expected
        row = " ".join(
            item.detail
            for item in report.items
            if item.subject == "Time of 'WMS'"
            and item.status is FidelityStatus.APPROXIMATED
        )
        assert "fixed snapshot" in row and "no time slider" in row
        if expected is not None:
            assert expected.split("/")[0] in row
        else:
            assert "default time" in row

    def test_the_canvas_time_is_what_the_temporal_controller_set(
        self, qgis_app
    ) -> None:
        """What the dialog reads: the canvas's own map settings, which the
        temporal controller sets, and which an unchanged view reads equal to
        (the read cache is keyed on it)."""
        from qgis.gui import QgsMapCanvas

        from nika_onlymap_exporter.core.project_reader import map_time_from_canvas

        canvas = QgsMapCanvas()
        try:
            assert map_time_from_canvas(canvas).is_temporal is False
            frame = qgis_core.QgsDateTimeRange(_utc(5), _utc(6))
            canvas.setTemporalRange(frame)
            read = map_time_from_canvas(canvas)
            assert read.is_temporal is True
            assert read.range == frame
            assert read == map_time_from_canvas(canvas)
            canvas.setTemporalRange(_no_time())
            assert map_time_from_canvas(canvas).is_temporal is False
        finally:
            canvas.deleteLater()

    def test_without_a_qgis_view_it_is_refused(self, qgis_app, services) -> None:
        """No canvas to read the time from (Processing, a cartograph frame)."""
        exported, report = _read(_wms(services.url("cors/wms"), extra=YEAR_EXTENT))
        assert exported is None
        assert "no QGIS map view" in _rows(report, FidelityStatus.UNSUPPORTED)

    @pytest.mark.parametrize(
        "method",
        [
            "FindClosestMatchToStartOfRange",
            "FindClosestMatchToEndOfRange",
            "MatchUsingWholeRange",
        ],
    )
    def test_a_matching_it_does_not_repeat_is_refused(
        self, qgis_app, services, method
    ) -> None:
        from nika_onlymap_exporter.core.layer_reader import MapTime

        layer = _wms(services.url("cors/wms"), crs="EPSG:3857", extra=YEAR_EXTENT)
        layer.temporalProperties().setIntervalHandlingMethod(
            getattr(qgis_core.Qgis.TemporalIntervalMatchMethod, method)
        )
        map_time = MapTime(True, qgis_core.QgsDateTimeRange(_utc(5, 7), _utc(5, 7)))
        exported, report = _read(layer, map_time=map_time)
        assert exported is None
        assert "start or end of the range" in _rows(report, FidelityStatus.UNSUPPORTED)

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


class TestTimeInTheReadCache:
    """The dialog's read cache is keyed on the QGIS view's time only when a
    layer follows it: moving the temporal controller in any other project must
    reuse the last read, as it did before WMS-T snapshots."""

    def _dialog(self, canvas):
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

            def mapCanvas(self):  # noqa: N802 - mirrors the QGIS interface
                return canvas

        return MainDialog(FakeIface(), None)

    def _reads_after_moving_time(self, qgis_app, dialog, canvas) -> list[str]:
        """Prime the cache, move the temporal controller, and ask again."""
        canvas.setTemporalRange(qgis_core.QgsDateTimeRange(_utc(5), _utc(5)))
        export, report = dialog._read_current_project(None)
        dialog._cached_export, dialog._cached_report = export, report
        dialog._cached_signature = dialog.state.data_snapshot()
        dialog._cached_canvas_extent = dialog._canvas_extent()
        dialog._cached_map_time = dialog._canvas_time()
        dialog._cached_expressions = dialog._expression_key()

        reads: list[str] = []
        dialog._start_job = lambda *a, **k: reads.append("read") or False
        used: list[object] = []
        dialog._ensure_export(lambda e, r: used.append(e), "Reading", quiet=True)
        assert reads == [] and len(used) == 1, "an unmoved view reuses the read"

        canvas.setTemporalRange(qgis_core.QgsDateTimeRange(_utc(6), _utc(6)))
        dialog._ensure_export(lambda e, r: used.append(e), "Reading", quiet=True)
        if not reads:
            assert len(used) == 2
        return reads

    def test_without_a_layer_that_follows_time_the_cache_survives(
        self, qgis_app, project, services, make_memory_layer
    ) -> None:
        from qgis.gui import QgsMapCanvas

        project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
        # A WMS-T on the Server default takes no time from QGIS either.
        project.addMapLayer(
            _wms(
                services.url("cors/wms"),
                crs="EPSG:3857",
                extra="&type=wmst&allowTemporalUpdates=false"
                "&timeDimensionExtent=2020-01-01/2020-12-31/P1D",
            )
        )
        canvas = QgsMapCanvas()
        dialog = self._dialog(canvas)
        try:
            assert self._reads_after_moving_time(qgis_app, dialog, canvas) == []
        finally:
            dialog.close()
            canvas.deleteLater()

    def test_with_a_layer_that_follows_time_moving_it_re_reads(
        self, qgis_app, project, services
    ) -> None:
        from qgis.gui import QgsMapCanvas

        project.addMapLayer(
            _wms(services.url("cors/wms"), crs="EPSG:3857", extra=YEAR_EXTENT)
        )
        canvas = QgsMapCanvas()
        dialog = self._dialog(canvas)
        try:
            assert self._reads_after_moving_time(qgis_app, dialog, canvas) == ["read"]
        finally:
            dialog.close()
            canvas.deleteLater()


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
