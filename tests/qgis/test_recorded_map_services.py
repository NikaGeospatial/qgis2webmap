"""Real government WMS and WMTS services, replayed offline.

Each case is a layer as a user adds it in QGIS, on a service whose
capabilities were recorded once (`tests/fixtures/map_services/README.md`).
The recording goes into QGIS's own network cache under the service's real
address, so QGIS loads the layer from it as it did from the service, and the
export reads it from there as it always does. A dead proxy fails anything
that would leave the machine, so a case can never pass on the network.

What each pins is the export a real service gave on 2026-11-05, checked by
eye in Chromium and WebKit then: the tile address, the legend pictures and
the time slider. A change in any of them is a change in what readers of a
map of that service see.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import MapTime, read_layer

qgis_core = pytest.importorskip("qgis.core")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

GIBS_TIMES = (
    "2000-02-24/2000-04-25/P1D,2000-04-28/2000-08-06/P1D,2000-08-18/2001-06-15/P1D,"
    "2001-07-03/2002-03-19/P1D,2002-03-28/2003-12-16/P1D,2003-12-24/2008-12-20/P1D,"
    "2008-12-23/2016-02-18/P1D,2016-02-27/2022-10-10/P1D,2022-10-23/2026-11-05/P1D"
)


def _wms(url, layers, crs, fmt="image/png", extra=""):
    return (
        f"crs={crs}&dpiMode=7&format={quote(fmt, safe='')}"
        + "".join(f"&layers={quote(layer)}" for layer in layers)
        + "&styles" * len(layers)
        + extra
        + f"&url={quote(url, safe='')}"
    )


def _wmts(caps, layer, matrix_set, crs, style="default", fmt="image/png", extra=""):
    return (
        f"contextualWMSLegend=0&crs={crs}&dpiMode=7&format={quote(fmt, safe='')}"
        f"&layers={quote(layer)}&styles={quote(style)}{extra}"
        f"&tileMatrixSet={quote(matrix_set)}&url={quote(caps, safe='')}"
    )


def _caps(url: str) -> str:
    return url + "?SERVICE=WMS&REQUEST=GetCapabilities"


PDOK = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi"
NOWCOAST = "https://nowcoast.noaa.gov/geoserver/observations/satellite/ows"
USGS = "https://basemap.nationalmap.gov/arcgis/services/USGSTopo/MapServer/WMSServer"
SOLOTHURN = "https://geo.so.ch/api/wms"
BGS = "https://ogc.bgs.ac.uk/cgi-bin/BGS_Bedrock_and_Superficial_Geology/wms"
NRW = "https://www.wms.nrw.de/geobasis/wms_nw_dtk"
SWISSTOPO = "https://wmts.geo.admin.ch/EPSG/2056/1.0.0/WMTSCapabilities.xml"
PDOK_WMTS = (
    "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0"
    "?request=GetCapabilities&service=WMTS"
)
IGN = "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetCapabilities"
BASEMAPDE = "https://sgx.geodatenzentrum.de/wmts_basemapde/1.0.0/WMTSCapabilities.xml"

GET_MAP = "&CRS=EPSG:3857&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-3857}"
LEGEND_130 = "SERVICE=WMS&VERSION=1.3.0&SLD_VERSION=1.1.0&REQUEST=GetLegendGraphic"

# (fixture, the address QGIS asked for it, the layer's source, the tile
# address the map asks, its legend pictures, and the opening time and the
# first of the slider's times, for a WMS-T)
CASES = {
    # MapServer; a JPEG with no transparency, in Web Mercator for an RD project.
    "pdok-luchtfoto": (
        "map_services/pdok-luchtfoto-wms.xml",
        _caps(PDOK),
        _wms(PDOK, ["Actueel_ortho25"], "EPSG:28992", "image/jpeg"),
        PDOK + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=Actueel_ortho25"
        "&STYLES=&FORMAT=image/jpeg" + GET_MAP,
        [],
        None,
    ),
    # 9,685 days in nine periods with gaps: the slider takes the 2,000 around
    # the day QGIS draws. GIBS itself sends its capabilities `no-store`, so
    # QGIS keeps no copy and a live export asks the layer's own address with
    # no legend; replayed as cached here, for the times.
    "nasa-gibs-wms-t": (
        "map_services/nasa-gibs-wms.xml",
        _caps(GIBS),
        _wms(
            GIBS,
            ["MODIS_Terra_CorrectedReflectance_TrueColor"],
            "EPSG:3857",
            "image/jpeg",
            "&allowTemporalUpdates=true&temporalSource=provider&type=wmst"
            f"&timeDimensionExtent={quote(GIBS_TIMES, safe='')}",
        ),
        GIBS.replace("wms.cgi", "") + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=MODIS_Terra_CorrectedReflectance_TrueColor&STYLES="
        "&FORMAT=image/jpeg&TIME={time}" + GET_MAP,
        [],
        ("2026-10-20", "2021-05-04"),
    ),
    # GeoServer's LegendURL, a GetLegendGraphic of its own.
    "noaa-nowcoast-geoserver": (
        "map_services/noaa-nowcoast-wms.xml",
        _caps(NOWCOAST),
        _wms(NOWCOAST, ["global_longwave_imagery_mosaic"], "EPSG:3857"),
        NOWCOAST + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=global_longwave_imagery_mosaic&STYLES=&FORMAT=image/png"
        "&TRANSPARENT=TRUE" + GET_MAP,
        [
            NOWCOAST + "?service=WMS&version=1.3.0&request=GetLegendGraphic"
            "&format=image/png&width=20&height=20"
            "&layer=global_longwave_imagery_mosaic&SLD_VERSION=1.1.0&STYLE="
            "&TRANSPARENT=true"
        ],
        None,
    ),
    # ArcGIS Server's WMS: a layer named "0", an address naming port 443.
    "usgs-arcgis": (
        "map_services/usgs-topo-wms.xml",
        _caps(USGS),
        _wms(USGS, ["0"], "EPSG:4326"),
        "https://basemap.nationalmap.gov:443/arcgis/services/USGSTopo/MapServer"
        "/WMSServer?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=0&STYLES="
        "&FORMAT=image/png&TRANSPARENT=TRUE" + GET_MAP,
        [
            "https://basemap.nationalmap.gov:443/arcgis/services/USGSTopo/MapServer"
            "/WMSServer?request=GetLegendGraphic&version=1.3.0&format=image/png"
            "&layer=0&SERVICE=WMS&SLD_VERSION=1.1.0&STYLE=&TRANSPARENT=true"
        ],
        None,
    ),
    # QGIS Server: two sublayers on a Swiss-grid project, a legend each.
    "solothurn-qgis-server": (
        "map_services/solothurn-wms.xml",
        _caps(SOLOTHURN),
        _wms(
            SOLOTHURN,
            ["ch.so.agi.hintergrundkarte_sw", "ch.so.ada.denkmalschutz"],
            "EPSG:2056",
        ),
        SOLOTHURN + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=ch.so.agi.hintergrundkarte_sw,ch.so.ada.denkmalschutz&STYLES=,"
        "&FORMAT=image/png&TRANSPARENT=TRUE" + GET_MAP,
        [
            "https://geo.so.ch/api/v1/legend/somap?SERVICE=WMS&VERSION=1.3.0"
            f"&REQUEST=GetLegendGraphic&LAYER={layer}&FORMAT=image/png"
            "&STYLE=default&SLD_VERSION=1.1.0&TRANSPARENT=true"
            for layer in ("ch.so.ada.denkmalschutz", "ch.so.agi.hintergrundkarte_sw")
        ],
        None,
    ),
    # MapServer answering as WMS 1.1.1 (SRS). Its capabilities name http://,
    # which redirects to https:// without CORS: asked where it was added.
    "bgs-mapserver-111": (
        "map_services/bgs-geology-wms-111.xml",
        _caps(BGS),
        _wms(BGS, ["GBR_BGS_625k_BLT"], "EPSG:27700"),
        BGS + "?language=eng&SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        "&LAYERS=GBR_BGS_625k_BLT&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE"
        "&SRS=EPSG:3857&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-3857}",
        [
            BGS + "?language=eng&version=1.1.1&service=WMS&request=GetLegendGraphic"
            "&layer=GBR_BGS_625k_BLT&format=image/png&STYLE=default"
            "&SLD_VERSION=1.1.0&TRANSPARENT=true"
        ],
        None,
    ),
    # Static PNG LegendURLs, a sublayer offering fewer CRSs than its parent.
    "nrw-mapserver": (
        "map_services/nrw-dtk-wms.xml",
        _caps(NRW),
        _wms(NRW, ["nw_dtk_col", "landesgrenze_nrw"], "EPSG:25832"),
        NRW + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=nw_dtk_col,landesgrenze_nrw&STYLES=,&FORMAT=image/png"
        "&TRANSPARENT=TRUE" + GET_MAP,
        [
            f"https://www.wms.nrw.de/legends/geobasis/wms_nw_dtk/{layer}.png?"
            f"{LEGEND_130}&FORMAT=image/png&LAYER={layer}&STYLE=&TRANSPARENT=true"
            for layer in ("landesgrenze_nrw", "nw_dtk_col")
        ],
        None,
    ),
    # REST, the Swiss grid, a Time dimension fixed at "current".
    "swisstopo-2056-rest": (
        "map_services/swisstopo-2056-wmts.xml",
        SWISSTOPO,
        _wmts(
            SWISSTOPO,
            "ch.swisstopo.pixelkarte-farbe",
            "2056_27",
            "EPSG:2056",
            style="ch.swisstopo.pixelkarte-farbe",
            fmt="image/jpeg",
            extra="&tileDimensions=Time%3Dcurrent",
        ),
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default"
        "/current/2056/{TileMatrix}/{TileCol}/{TileRow}.jpeg",
        [],
        None,
    ),
    # KVP on the Dutch RD grid, levels named "00", "01", ...
    "pdok-28992-kvp": (
        "pdok-brt-wmts-capabilities.xml",
        PDOK_WMTS,
        _wmts(PDOK_WMTS, "standaard", "EPSG:28992", "EPSG:28992"),
        "https://service.pdok.nl/kadaster/brt-achtergrondkaart/wmts/v2_0"
        "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=standaard"
        "&STYLE=default&FORMAT=image/png&TILEMATRIXSET=EPSG%3A28992"
        "&TILEMATRIX={TileMatrix}&TILEROW={TileRow}&TILECOL={TileCol}",
        [
            "https://service.pdok.nl/kadaster/brt-achtergrondkaart/wmts/v2_0"
            "/standaard/legend.png?SERVICE=WMS&VERSION=1.0.0&SLD_VERSION=1.1.0"
            "&REQUEST=GetLegendGraphic&FORMAT=image/png&LAYER=standaard"
            "&STYLE=default&TRANSPARENT=true"
        ],
        None,
    ),
    # KVP on a Web Mercator grid named by its zooms, from a 3 MB capabilities.
    "ign-pm-kvp": (
        "map_services/ign-geoplateforme-wmts.xml",
        IGN,
        _wmts(
            IGN, "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "PM_0_19", "EPSG:3857", "normal"
        ),
        "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png"
        "&TILEMATRIXSET=PM_0_19&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
        [
            "https://data.geopf.fr/annexes/ressources/legendes"
            "/GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2-legend.png?SERVICE=WMS"
            "&VERSION=1.0.0&SLD_VERSION=1.1.0&REQUEST=GetLegendGraphic"
            "&FORMAT=image/png&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
            "&STYLE=normal&TRANSPARENT=true"
        ],
        None,
    ),
    # REST on the AdV's UTM grid, its row before its column.
    "basemapde-25832-rest": (
        "map_services/basemapde-wmts.xml",
        BASEMAPDE,
        _wmts(
            BASEMAPDE,
            "de_basemapde_web_raster_farbe",
            "DE_EPSG_25832_ADV",
            "EPSG:25832",
        ),
        "https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0"
        "/de_basemapde_web_raster_farbe/default/DE_EPSG_25832_ADV"
        "/{TileMatrix}/{TileRow}/{TileCol}.png",
        [],
        None,
    ),
}


@pytest.fixture
def offline(qgis_app):
    """QGIS with every request that is not in its cache sent nowhere, and
    every answer a test recorded taken out of the cache again after it."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkProxy

    manager = qgis_core.QgsNetworkAccessManager.instance()
    before = manager.fallbackProxy(), manager.excludeList(), manager.noProxyList()
    manager.setFallbackProxyAndExcludes(
        QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, "127.0.0.1", 9), [], []
    )
    recorded.clear()
    yield manager
    for url in recorded:
        manager.cache().remove(QUrl(url))
    recorded.clear()
    manager.setFallbackProxyAndExcludes(*before)


# The addresses `_record` answered in this test, for `offline` to forget.
recorded: list[str] = []


def _record(manager, url: str, body: bytes) -> None:
    """Put `body` in QGIS's network cache as the answer to `url`, fresh for a
    day: what QGIS keeps after fetching the capabilities itself. The cache is
    the test session's own (`tests/hermetic.py`), and `offline` takes the
    answer out after the test."""
    from qgis.PyQt.QtCore import QDateTime, QUrl
    from qgis.PyQt.QtNetwork import QNetworkCacheMetaData

    recorded.append(url)

    meta = QNetworkCacheMetaData()
    meta.setUrl(QUrl(url))
    meta.setSaveToDisk(True)
    meta.setLastModified(QDateTime.currentDateTimeUtc())
    meta.setExpirationDate(QDateTime.currentDateTimeUtc().addDays(1))
    meta.setRawHeaders([(b"Content-Type", b"text/xml")])
    cache = manager.cache()
    device = cache.prepare(meta)
    device.write(body)
    cache.insert(device)


# The cases whose capabilities say a click on the layer is answered.
CLICKABLE = {"noaa-nowcoast-geoserver", "usgs-arcgis", "bgs-mapserver-111"}

# QGIS 3.44 cannot open this layer from these capabilities, the live service's
# included ("Tile layer or tile matrix set not found", checked 2026-11-05);
# QGIS 4 can. There is nothing for the export to read there.
NEEDS_QGIS_4 = {"pdok-28992-kvp"}


def _as_this_qgis_asks(tiles: str, legend: list[str]) -> tuple[str, list[str]]:
    """The addresses as recorded, from QGIS 4.2, which asks for a layer's
    sublayers the other way round from QGIS 3.44 (`test_map_service_layers`,
    `TestSublayerOrder`, checks the export follows whichever QGIS it runs in)."""
    if qgis_core.Qgis.QGIS_VERSION_INT >= 40000 or "?" not in tiles:
        return tiles, legend
    base, query = tiles.split("?", 1)
    parts = []
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key in ("LAYERS", "STYLES"):
            value = ",".join(value.split(",")[::-1])
        parts.append(f"{key}={value}")
    return f"{base}?{'&'.join(parts)}", legend[::-1]


@pytest.mark.parametrize("case", list(CASES))
def test_a_recorded_service_exports_as_it_did_live(offline, case) -> None:
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone

    if case in NEEDS_QGIS_4 and qgis_core.Qgis.QGIS_VERSION_INT < 40000:
        pytest.skip("QGIS 3.44 cannot open this layer from its capabilities")
    fixture, caps_url, source, tiles, legend, time = CASES[case]
    tiles, legend = _as_this_qgis_asks(tiles, legend)
    _record(offline, caps_url, (FIXTURES / fixture).read_bytes())
    layer = qgis_core.QgsRasterLayer(source, case, "wms")
    assert layer.isValid(), layer.error().summary()

    day = QDateTime(QDate(2026, 10, 20), QTime(0, 0), QTimeZone.utc())
    map_time = MapTime(True, qgis_core.QgsDateTimeRange(day, day.addSecs(86399)))
    report = FidelityReportBuilder()
    exported = read_layer(layer, report, map_time=map_time)

    assert exported is not None, [item.detail for item in report.items]
    assert exported.tiles is not None
    assert exported.tiles.url == tiles
    assert [image.url for image in exported.tiles.legend] == legend
    # A click asks on the scheme the layer was added on, as its tiles do:
    # BGS's capabilities name http://, which a page cannot follow.
    if exported.tiles.feature_info is not None:
        assert exported.tiles.feature_info.startswith("https://"), case
    assert (exported.tiles.feature_info is not None) is (case in CLICKABLE)
    # ArcGIS Server's addresses name port 443; a row names just the host.
    assert ":443" not in " ".join(item.detail for item in report.items)
    if time is None:
        assert exported.tiles.times is None
    else:
        opening, first = time
        assert exported.tiles.time == opening
        assert exported.tiles.times is not None
        times = exported.tiles.times.split(",")
        assert (times[0], len(times)) == (first, 2000)


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857"])
def test_a_service_that_keeps_no_capabilities_says_what_readers_get(
    offline, crs
) -> None:
    """NASA GIBS sends its capabilities `no-store`: QGIS loads the layer from
    them and keeps no copy, as taking the recording out after the load
    leaves it. The legend is a name, and a layer not added in EPSG:3857 is
    asked as QGIS asks; the row says so, and what to do."""
    from qgis.PyQt.QtCore import QUrl

    _record(
        offline, _caps(GIBS), (FIXTURES / "map_services/nasa-gibs-wms.xml").read_bytes()
    )
    source = _wms(
        GIBS, ["MODIS_Terra_CorrectedReflectance_TrueColor"], crs, "image/jpeg"
    )
    layer = qgis_core.QgsRasterLayer(source, "GIBS", "wms")
    assert layer.isValid(), layer.error().summary()
    offline.cache().remove(QUrl(_caps(GIBS)))

    report = FidelityReportBuilder()
    exported = read_layer(layer, report)

    assert exported is not None
    assert exported.tiles is not None
    assert exported.tiles.legend == ()
    assert f"&CRS={crs}&" in exported.tiles.url
    rows = [item for item in report.items if item.subject == "Capabilities of 'GIBS'"]
    assert [row.status for row in rows] == [FidelityStatus.APPROXIMATED]
    detail = rows[0].detail
    assert "the legend lists the layer by its name" in detail
    assert "(NASA GIBS does) never leaves a copy" in detail
    projected = crs != "EPSG:3857"
    assert ("each tile is asked in EPSG:4326" in detail) is projected
    assert ("add the layer again with EPSG:3857 chosen" in detail) is projected
    # Reopening online never helps such a service, so its click row says so.
    (clicks,) = [i.detail for i in report.items if i.subject == "Clicks on 'GIBS'"]
    assert "(NASA GIBS does) never leaves a copy" in clicks


def test_the_network_cache_is_the_test_sessions_own(offline, tmp_path_factory) -> None:
    """What `_record` puts in QGIS's network cache lands in this session's
    temporary directory, never in the cache the user's own QGIS reads."""
    session = tmp_path_factory.getbasetemp().resolve()
    cache = Path(os.environ.get("XDG_CACHE_HOME", "")).resolve()
    assert cache.is_relative_to(session), f"QGIS caches in {cache}"

    def files() -> set[Path]:
        return {path for path in cache.rglob("*") if path.is_file()}

    before = files()
    _record(offline, "https://cache-check.invalid/capabilities", b"<x/>" * 64)
    assert files() - before, "the recorded answer is not in the session's cache"
