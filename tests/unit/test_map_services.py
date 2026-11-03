"""WMS and WMTS sources as runtime tile templates (`core.map_services`).

The capabilities documents are the fake services' (`tests/fake_map_services.py`),
the same ones QGIS loads in the QGIS tier, plus small hand-written ones for the
cases a real service gets wrong or does differently.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from html import escape
from urllib.parse import parse_qsl

import pytest
from fake_map_services import (
    tile_bounds_3857,
    wms_capabilities_111,
    wms_capabilities_130,
    wmts_capabilities,
)

from nika_onlymap_exporter.core.map_services import (
    HALF_WORLD,
    WmtsRefusal,
    WmtsTiles,
    capabilities_url,
    is_image_tile_template,
    parse_tile_dimensions,
    parse_wms_capabilities,
    prepare_url,
    source_pairs,
    values,
    web_mercator_code,
    wms_get_map_template,
    wms_legend_url,
    wms_legend_urls,
    wmts_legend_url,
    wmts_tiles,
)

BASE = "https://maps.example.org/wms"


class TestSourceString:
    def test_repeated_layers_and_styles_stay_in_order(self) -> None:
        pairs = source_pairs(
            "crs=EPSG:25832&format=image/png&layers=a&layers=b&styles=&styles=x"
            "&url=https://h/wms"
        )
        assert values(pairs, "layers") == ["a", "b"]
        assert values(pairs, "styles") == ["", "x"]

    def test_the_capabilities_address_is_the_one_qgis_fetched(self) -> None:
        """`QgsWmsProvider::prepareUri` + `SERVICE=WMS&REQUEST=GetCapabilities`,
        which is the key QGIS's network cache keeps the document under."""
        assert capabilities_url("https://h/wms") == (
            "https://h/wms?SERVICE=WMS&REQUEST=GetCapabilities"
        )
        assert capabilities_url("https://h/wms?") == (
            "https://h/wms?SERVICE=WMS&REQUEST=GetCapabilities"
        )
        assert capabilities_url("https://h/wms?map=%2Fa.map") == (
            "https://h/wms?map=/a.map&SERVICE=WMS&REQUEST=GetCapabilities"
        )
        wmts = "https://h/wmts?SERVICE=WMTS&REQUEST=GetCapabilities"
        assert capabilities_url(wmts) == wmts
        assert prepare_url("https://h/1.0.0/WMTSCapabilities.xml").endswith(".xml")

    def test_tile_dimensions(self) -> None:
        assert parse_tile_dimensions("Time=2020;elevation=0") == {
            "Time": "2020",
            "elevation": "0",
        }
        assert parse_tile_dimensions("") == {}


class TestWebMercatorCodes:
    @pytest.mark.parametrize(
        "code",
        [
            "EPSG:3857",
            "urn:ogc:def:crs:EPSG::3857",
            "urn:ogc:def:crs:EPSG:6.18.3:3857",
            "http://www.opengis.net/def/crs/EPSG/0/3857",
        ],
    )
    def test_forms_of_3857(self, code: str) -> None:
        assert web_mercator_code(code) == "EPSG:3857"

    def test_aliases_keep_their_own_code(self) -> None:
        assert web_mercator_code("EPSG:900913") == "EPSG:900913"
        assert web_mercator_code("EPSG:102100") == "EPSG:102100"

    @pytest.mark.parametrize("code", ["EPSG:25832", "EPSG:4326", "CRS:84", ""])
    def test_other_systems(self, code: str) -> None:
        assert web_mercator_code(code) is None


class TestWmsCapabilities:
    def test_130(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        assert parsed.version == "1.3.0"
        assert parsed.get_map_url == BASE + "?"
        assert parsed.offered_web_mercator(["target"]) == "EPSG:3857"
        assert parsed.offered_web_mercator(["utm_only"]) is None
        # Every layer of the request must offer it.
        assert parsed.offered_web_mercator(["target", "utm_only"]) is None

    def test_111(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_111(BASE).encode())
        assert parsed is not None
        assert parsed.version == "1.1.1"
        assert parsed.offered_web_mercator(["target"]) == "EPSG:3857"

    def test_a_layer_inherits_its_parents_systems(self) -> None:
        document = (
            "<WMS_Capabilities version='1.3.0' xmlns='http://www.opengis.net/wms'>"
            "<Capability><Layer><CRS>EPSG:3857</CRS>"
            "<Layer><Name>child</Name><CRS>EPSG:25832</CRS></Layer>"
            "</Layer></Capability></WMS_Capabilities>"
        )
        parsed = parse_wms_capabilities(document.encode())
        assert parsed is not None
        assert parsed.offered_web_mercator(["child"]) == "EPSG:3857"

    def test_an_alias_is_used_only_without_3857(self) -> None:
        document = (
            "<WMT_MS_Capabilities version='1.1.1'><Capability><Layer>"
            "<Name>a</Name><SRS>EPSG:4326 EPSG:900913</SRS></Layer>"
            "<Layer><Name>b</Name><SRS>EPSG:900913</SRS><SRS>EPSG:3857</SRS>"
            "</Layer></Capability></WMT_MS_Capabilities>"
        )
        parsed = parse_wms_capabilities(document.encode())
        assert parsed is not None
        assert parsed.offered_web_mercator(["a"]) == "EPSG:900913"
        assert parsed.offered_web_mercator(["b"]) == "EPSG:3857"

    @pytest.mark.parametrize(
        "document",
        [None, b"", b"not xml", b"<html><body>Proxy login</body></html>"],
    )
    def test_anything_else_is_no_capabilities(self, document) -> None:
        assert parse_wms_capabilities(document) is None

    def test_an_unlisted_layer_is_not_assumed(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        assert parsed.offered_web_mercator(["nope"]) is None
        assert parsed.offered_web_mercator([]) is None


class TestGetMapTemplate:
    def test_130_asks_with_crs(self) -> None:
        template = wms_get_map_template(
            BASE, "1.3.0", ["target"], [""], "image/png", "EPSG:3857"
        )
        assert template == (
            "https://maps.example.org/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
            "&LAYERS=target&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE"
            "&CRS=EPSG:3857&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-3857}"
        )

    def test_111_asks_with_srs(self) -> None:
        template = wms_get_map_template(
            BASE, "1.1.1", ["target"], [""], "image/png", "EPSG:3857"
        )
        assert "&SRS=EPSG:3857&" in template
        assert "CRS=" not in template
        assert "VERSION=1.1.1" in template

    def test_names_are_encoded_one_by_one(self) -> None:
        """A comma inside a name must not split it; the list's own commas stay."""
        template = wms_get_map_template(
            BASE,
            "1.3.0",
            ["Gebäude, alt", "b&c"],
            ["", "rot/grün"],
            "image/png",
            "EPSG:3857",
        )
        assert "LAYERS=Geb%C3%A4ude%2C%20alt,b%26c&" in template
        assert "STYLES=,rot%2Fgr%C3%BCn&" in template

    def test_the_services_own_parameters_are_kept_and_ours_win(self) -> None:
        template = wms_get_map_template(
            BASE + "?map=/srv/a.map&version=1.1.1&Layers=x",
            "1.3.0",
            ["target"],
            [""],
            "image/png",
            "EPSG:3857",
        )
        assert template.startswith(BASE + "?map=/srv/a.map&SERVICE=WMS&")
        assert template.count("VERSION") == 1
        assert "version=1.1.1" not in template
        assert "Layers=x" not in template

    def test_jpeg_is_not_asked_for_transparency(self) -> None:
        """QGIS's rule: some servers refuse TRANSPARENT with JPEG."""
        jpeg = wms_get_map_template(BASE, "1.3.0", ["a"], [""], "image/jpeg", "X")
        assert "TRANSPARENT" not in jpeg

    def test_extra_parameters_pass_through(self) -> None:
        template = wms_get_map_template(
            BASE,
            "1.3.0",
            ["a"],
            [""],
            "image/png",
            "EPSG:3857",
            extra=[("FILTER", 'a:"x" = 1')],
        )
        assert "&FILTER=a:%22x%22%20%3D%201&" in template

    def test_a_wms_t_time_is_asked_for_and_replaces_the_services_own(self) -> None:
        template = wms_get_map_template(
            BASE + "?TIME=1999-01-01",
            "1.3.0",
            ["a"],
            [""],
            "image/png",
            "EPSG:3857",
            time="2020-03-05T00:00:00Z/2020-03-06T00:00:00Z",
        )
        assert "&TIME=2020-03-05T00:00:00Z/2020-03-06T00:00:00Z&" in template
        assert template.count("TIME=") == 1

    def test_without_a_time_the_services_own_is_kept(self) -> None:
        template = wms_get_map_template(
            BASE + "?TIME=1999-01-01", "1.3.0", ["a"], [""], "image/png", "EPSG:3857"
        )
        assert template.startswith(BASE + "?TIME=1999-01-01&SERVICE=WMS&")
        assert template.count("TIME=") == 1


def _fill(template: str, x: int, y: int, z: int) -> str:
    """The runtime's own filling (0.13.3 `fillTileTemplate`), for the check
    that a WMS tile's bbox is that tile's extent."""
    size = 2 * HALF_WORLD / 2**z
    west, north = -HALF_WORLD + x * size, HALF_WORLD - y * size
    bbox = ",".join(str(v) for v in (west, north - size, west + size, north))
    return (
        template.replace("{bbox-epsg-3857}", bbox)
        .replace("{z}", str(z))
        .replace("{x}", str(x))
        .replace("{y}", str(y))
    )


def test_the_bbox_token_is_the_tiles_extent() -> None:
    """The fake server draws from the bbox it is given; this pins that the
    tile it is asked for and the extent it is given are the same."""
    template = wms_get_map_template(BASE, "1.3.0", ["t"], [""], "image/png", "X")
    filled = _fill(template, 34, 21, 6)
    bbox = filled.rpartition("BBOX=")[2].split(",")
    assert [float(v) for v in bbox] == pytest.approx(tile_bounds_3857(34, 21, 6))


WMTS = "https://tiles.example.org/wmts"


def _wmts(**overrides) -> WmtsTiles | WmtsRefusal:
    arguments = dict(
        document=wmts_capabilities(WMTS).encode(),
        layer="target",
        style="default",
        image_format="image/png",
        matrix_set="GoogleMapsCompatible",
        dimensions={},
    )
    arguments.update(overrides)
    return wmts_tiles(**arguments)  # type: ignore[arg-type]


def _matrix_set(identifiers, tile=256, first=0, crs="urn:ogc:def:crs:EPSG::3857"):
    """A one-layer WMTS whose set `s` has a matrix per identifier, at zooms
    `first`, `first + 1`, ... (or at the zooms given as `(zoom, name)`)."""
    levels = [
        item if isinstance(item, tuple) else (first + index, item)
        for index, item in enumerate(identifiers)
    ]
    matrices = "".join(
        f"<TileMatrix><ows:Identifier>{name}</ows:Identifier>"
        f"<ScaleDenominator>{559082264.0287178 * 256 / tile / 2**z}"
        f"</ScaleDenominator><TopLeftCorner>{-HALF_WORLD} {HALF_WORLD}"
        f"</TopLeftCorner><TileWidth>{tile}</TileWidth><TileHeight>{tile}"
        f"</TileHeight><MatrixWidth>{2**z}</MatrixWidth><MatrixHeight>{2**z}"
        "</MatrixHeight></TileMatrix>"
        for z, name in levels
    )
    return (
        "<Capabilities xmlns='http://www.opengis.net/wmts/1.0' "
        "xmlns:ows='http://www.opengis.net/ows/1.1' version='1.0.0'><Contents>"
        "<Layer><ows:Identifier>l</ows:Identifier>"
        "<ResourceURL format='image/png' resourceType='tile' "
        "template='https://t.example/{TileMatrixSet}/{TileMatrix}/{TileCol}/"
        "{TileRow}.png'/></Layer>"
        f"<TileMatrixSet><ows:Identifier>s</ows:Identifier><ows:SupportedCRS>{crs}"
        f"</ows:SupportedCRS>{matrices}</TileMatrixSet></Contents></Capabilities>"
    ).encode()


class TestWmts:
    def test_kvp_on_the_google_grid(self) -> None:
        tiles = _wmts()
        assert isinstance(tiles, WmtsTiles)
        assert tiles.template == (
            WMTS + "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=target"
            "&STYLE=default&FORMAT=image/png&TILEMATRIXSET=GoogleMapsCompatible"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        )
        assert (tiles.min_zoom, tiles.max_zoom, tiles.tile_size) == (0, 18, 256)

    def test_another_grid_is_refused_by_name(self) -> None:
        refusal = _wmts(matrix_set="UTM32")
        assert refusal == WmtsRefusal("grid", grid="UTM32")

    def test_rest_with_prefixed_identifiers(self) -> None:
        names = [f"EPSG:3857:{z}" for z in range(3, 8)]
        tiles = _wmts(document=_matrix_set(names, first=3), layer="l", matrix_set="s")
        assert isinstance(tiles, WmtsTiles)
        assert tiles.template == "https://t.example/s/EPSG%3A3857%3A{z}/{x}/{y}.png"
        assert (tiles.min_zoom, tiles.max_zoom) == (3, 7)

    def test_512_pixel_tiles_keep_their_size(self) -> None:
        tiles = _wmts(
            document=_matrix_set(["0", "1", "2"], tile=512), layer="l", matrix_set="s"
        )
        assert isinstance(tiles, WmtsTiles)
        assert tiles.tile_size == 512

    @pytest.mark.parametrize(
        "identifiers",
        [
            ["00", "01", "02"],  # zero-padded: `{z}` cannot pad
            ["a", "b", "c"],
            [(0, "0"), (1, "1"), (3, "3")],  # a level missing
            ["1", "2", "3"],  # names one off the zoom
        ],
    )
    def test_identifiers_z_cannot_name_are_refused(self, identifiers) -> None:
        refusal = _wmts(document=_matrix_set(identifiers), layer="l", matrix_set="s")
        assert isinstance(refusal, WmtsRefusal)

    def test_a_projected_grid_is_refused(self) -> None:
        document = _matrix_set(["0", "1"], crs="urn:ogc:def:crs:EPSG::25832")
        refusal = _wmts(document=document, layer="l", matrix_set="s")
        assert refusal == WmtsRefusal("grid", grid="s")

    def test_no_document_is_no_template(self) -> None:
        assert _wmts(document=None) == WmtsRefusal("no capabilities")

    def test_a_time_dimension_needs_a_fixed_value(self) -> None:
        document = wmts_capabilities(WMTS).replace(
            "<Format>image/png</Format>",
            "<Format>image/png</Format><Dimension><ows:Identifier>Time"
            "</ows:Identifier><Default>2020</Default><Value>2020</Value></Dimension>",
        )
        assert _wmts(document=document.encode()) == WmtsRefusal("time")
        fixed = _wmts(document=document.encode(), dimensions={"Time": "2020"})
        assert isinstance(fixed, WmtsTiles)
        assert "&Time=2020&" in fixed.template

    def test_a_rest_address_without_an_image_extension_is_refused(self) -> None:
        """The runtime draws a template as images only when the address says
        so; anything else it decodes as data, and the layer fails."""
        document = _matrix_set(["0", "1"]).replace(b"{TileRow}.png", b"{TileRow}")
        refusal = _wmts(document=document, layer="l", matrix_set="s")
        assert refusal == WmtsRefusal("not imagery")

    def test_a_rest_token_nothing_fills_is_refused(self) -> None:
        document = _matrix_set(["0", "1"]).replace(
            b"{TileRow}.png", b"{TileRow}.png?d={Elevation}"
        )
        refusal = _wmts(document=document, layer="l", matrix_set="s")
        assert refusal == WmtsRefusal("no tile address")


def test_the_google_scale_set_spans_the_world() -> None:
    """The constant the fake grid and the check share is the OGC's."""
    ground = 559082264.0287178 * 0.00028 * 256
    assert ground == pytest.approx(2 * HALF_WORLD)
    assert math.isclose(HALF_WORLD, 20037508.342789244)


class TestImageTemplates:
    """The runtime's own test of what it draws as images (0.13.3)."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://t.example/{z}/{x}/{y}.png",
            "https://t.example/{z}/{x}/{y}.jpeg?k=1",
            BASE + "?SERVICE=WMS&FORMAT=image/png&BBOX={bbox-epsg-3857}",
            BASE + "?SERVICE=WMS&FORMAT=image%2Fjpeg&BBOX={bbox-epsg-3857}",
            "https://h/MapServer/export?bbox={bbox-epsg-3857}&format=png",
        ],
    )
    def test_imagery(self, url: str) -> None:
        assert is_image_tile_template(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://h/MapServer/tile/{z}/{y}/{x}",
            "https://t.example/{z}/{x}/{y}.pbf",
            BASE + "?FORMAT=text/html&BBOX={bbox-epsg-3857}",
        ],
    )
    def test_not_imagery(self, url: str) -> None:
        assert not is_image_tile_template(url)

    def test_every_wms_template_is_imagery(self) -> None:
        for image_format in ("image/png", "image/jpeg", "image/png; mode=8bit"):
            template = wms_get_map_template(
                BASE, "1.3.0", ["a"], [""], image_format, "EPSG:3857"
            )
            assert is_image_tile_template(template), image_format


def _legend_capabilities(layers: str, request: str = "", version="1.3.0"):
    """A WMS whose `<Capability>` holds `layers`, and `request` in its
    `<Request>` (GetLegendGraphic, when a test needs one)."""
    parsed = parse_wms_capabilities(
        (
            f"<WMS_Capabilities version='{version}' "
            "xmlns='http://www.opengis.net/wms' "
            "xmlns:xlink='http://www.w3.org/1999/xlink'><Capability>"
            f"<Request>{request}</Request>{layers}</Capability></WMS_Capabilities>"
        ).encode()
    )
    assert parsed is not None
    return parsed


def _legend_url(href: str, image_format: str = "image/png") -> str:
    return (
        f"<LegendURL><Format>{image_format}</Format><OnlineResource "
        f"xlink:href='{escape(href)}'/></LegendURL>"
    )


def _style(name: str, *legend_urls: str) -> str:
    return f"<Style><Name>{name}</Name>{''.join(legend_urls)}</Style>"


GET_LEGEND_GRAPHIC = (
    "<GetLegendGraphic><Format>image/png</Format><DCPType><HTTP><Get>"
    f"<OnlineResource xlink:href='{BASE}?'/></Get></HTTP></DCPType>"
    "</GetLegendGraphic>"
)


class TestWmsLegend:
    """The legend picture QGIS's legend shows (`QgsWmsProvider::
    getLegendGraphicUrl` and `getLegendGraphicFullURL`)."""

    def test_130_builds_a_get_legend_graphic_request(self) -> None:
        """The fake service's `sld:GetLegendGraphic`; `target`'s default
        style has no LegendURL of its own."""
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        assert wms_legend_url(parsed, "target", "", "image/png") == (
            BASE + "?SERVICE=WMS&VERSION=1.3.0&SLD_VERSION=1.1.0"
            "&REQUEST=GetLegendGraphic&FORMAT=image/png&LAYER=target&STYLE="
            "&TRANSPARENT=true"
        )

    def test_111_uses_the_same_names_and_its_own_version(self) -> None:
        """GetLegendGraphic is the SLD profile's in both versions, so only
        VERSION differs; no CRS or SRS, since no view is sent."""
        parsed = parse_wms_capabilities(wms_capabilities_111(BASE).encode())
        assert parsed is not None
        url = wms_legend_url(parsed, "target", "", "image/png")
        assert url is not None
        params = dict(parse_qsl(url.partition("?")[2], keep_blank_values=True))
        assert params["VERSION"] == "1.1.1"
        assert params["LAYER"] == "target"
        assert params["STYLE"] == ""
        assert not {"SRS", "CRS", "BBOX", "LAYERS", "STYLES"} & set(params)

    def test_the_chosen_styles_legend_url(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        url = wms_legend_url(parsed, "target", "outlined", "image/png")
        assert url is not None
        assert url.startswith(
            "https://maps.example.org/legends/outlined.png?SERVICE=WMS&"
        )
        assert "&STYLE=outlined&" in url

    def test_the_default_style_is_the_one_named_default_else_the_first(
        self,
    ) -> None:
        named = _legend_capabilities(
            "<Layer><Name>a</Name>"
            + _style("first", _legend_url("https://h/first.png"))
            + _style("default", _legend_url("https://h/default.png"))
            + "</Layer>"
        )
        for drawn_in in ("", "default"):
            url = wms_legend_url(named, "a", drawn_in, "image/png")
            assert url is not None
            assert url.startswith("https://h/default.png?")
        unnamed = _legend_capabilities(
            "<Layer><Name>a</Name>"
            + _style("first", _legend_url("https://h/first.png"))
            + _style("second", _legend_url("https://h/second.png"))
            + "</Layer>"
        )
        url = wms_legend_url(unnamed, "a", "", "image/png")
        assert url is not None
        assert url.startswith("https://h/first.png?")

    def test_only_an_image_legend_url_counts(self) -> None:
        parsed = _legend_capabilities(
            "<Layer><Name>a</Name>"
            + _style(
                "s",
                _legend_url("https://h/legend.html", "text/html"),
                _legend_url("https://h/legend.png"),
            )
            + "</Layer>"
        )
        url = wms_legend_url(parsed, "a", "s", "image/png")
        assert url is not None
        assert url.startswith("https://h/legend.png?")

    def test_without_a_legend_url_the_get_legend_graphic_address(self) -> None:
        """An unknown style, and a style without a LegendURL, fall back."""
        parsed = _legend_capabilities(
            "<Layer><Name>a</Name>" + _style("plain") + "</Layer>",
            GET_LEGEND_GRAPHIC,
        )
        for style in ("plain", "nope"):
            url = wms_legend_url(parsed, "a", style, "image/png")
            assert url is not None
            assert url.startswith(BASE + "?SERVICE=WMS&")
            assert f"&STYLE={style}&" in url

    def test_with_neither_there_is_no_legend(self) -> None:
        """QGIS asks nowhere, and so does the map."""
        parsed = _legend_capabilities("<Layer><Name>a</Name></Layer>")
        assert wms_legend_url(parsed, "a", "", "image/png") is None

    def test_a_legend_urls_own_parameters_are_kept_whatever_their_case(
        self,
    ) -> None:
        """GeoServer writes its LegendURLs as GetLegendGraphic requests; QGIS
        adds only what they lack, and decodes them first."""
        href = (
            "https://gs.example/wms?request=GetLegendGraphic&format=image%2Fpng"
            "&width=20&height=20&layer=topp%3Astates"
        )
        parsed = _legend_capabilities(
            "<Layer><Name>topp:states</Name>"
            + _style("population", _legend_url(href))
            + "</Layer>"
        )
        assert wms_legend_url(parsed, "topp:states", "population", "image/jpeg") == (
            "https://gs.example/wms?request=GetLegendGraphic&format=image/png"
            "&width=20&height=20&layer=topp:states&SERVICE=WMS&VERSION=1.3.0"
            "&SLD_VERSION=1.1.0&STYLE=population&TRANSPARENT=true"
        )

    def test_an_inherited_legend_url_names_the_child(self) -> None:
        """`QgsWmsCapabilities::parseLayer`: a parent's style is the child's,
        its LegendURL's LAYER rewritten to the child's name."""
        parsed = _legend_capabilities(
            "<Layer><Name>parent</Name>"
            + _style("s", _legend_url("https://h/legend?LAYER=parent&x=1"))
            + "<Layer><Name>child</Name></Layer></Layer>"
        )
        url = wms_legend_url(parsed, "child", "s", "image/png")
        assert url is not None
        assert url.startswith("https://h/legend?x=1&LAYER=child&SERVICE=WMS&")

    def test_a_childs_style_replaces_an_inherited_one_of_its_name(self) -> None:
        parsed = _legend_capabilities(
            "<Layer><Name>parent</Name>"
            + _style("s", _legend_url("https://h/parent.png"))
            + "<Layer><Name>child</Name>"
            + _style("s", _legend_url("https://h/child.png"))
            + "</Layer></Layer>"
        )
        url = wms_legend_url(parsed, "child", "s", "image/png")
        assert url is not None
        assert url.startswith("https://h/child.png?")

    def test_names_and_format_are_encoded(self) -> None:
        parsed = _legend_capabilities("", GET_LEGEND_GRAPHIC)
        url = wms_legend_url(parsed, "Gebäude & Co", "rot/grün", "image/png; mode=8bit")
        assert url is not None
        assert "&LAYER=Geb%C3%A4ude%20%26%20Co&" in url
        assert "&STYLE=rot%2Fgr%C3%BCn&" in url
        assert "&FORMAT=image/png%3B%20mode%3D8bit&" in url

    def test_a_layer_ignoring_the_capabilities_addresses_is_asked_at_its_own(
        self,
    ) -> None:
        """`IgnoreGetMapUrl`: QGIS asks the layer's own address, LegendURL or not."""
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        url = wms_legend_url(
            parsed, "target", "outlined", "image/png", url="https://own.example/wms"
        )
        assert url is not None
        assert url.startswith("https://own.example/wms?SERVICE=WMS&")

    def test_one_picture_per_sublayer_under_its_title(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        found = wms_legend_urls(
            parsed, ["target", "boundary"], ["outlined", ""], "image/png"
        )
        assert [title for title, _ in found] == ["Target area", "Boundary"]
        assert "/legends/outlined.png?" in found[0][1]
        assert "&LAYER=boundary&STYLE=&" in found[1][1]

    def test_a_sublayer_without_a_legend_is_skipped(self) -> None:
        parsed = _legend_capabilities(
            "<Layer><Name>a</Name>"
            + _style("s", _legend_url("https://h/a.png"))
            + "</Layer><Layer><Name>b</Name></Layer>"
        )
        found = wms_legend_urls(parsed, ["a", "b"], ["s", ""], "image/png")
        assert [title for title, _ in found] == ["a"]


# How QGIS stores a WMTS layer's source: the address percent-encoded, so a
# KVP capabilities address does not read as a WMTS one, and a RESTful one does.
KVP_SOURCE = "layers=l&styles=s&tileMatrixSet=g&url=https://h/wmts%3FSERVICE%3DWMTS"
REST_SOURCE = (
    "layers=l&styles=s&tileMatrixSet=g&url=https://h/1.0.0/WMTSCapabilities.xml"
)


class TestWmtsLegend:
    def test_the_styles_legend_url_as_qgis_asks_it(self) -> None:
        """QGIS adds GetLegendGraphic's parameters unless the layer's source
        reads as a WMTS address; VERSION is the WMTS's own."""
        document = wmts_capabilities(WMTS).encode()
        rest = wmts_legend_url(document, "target", "default", "image/png", REST_SOURCE)
        assert rest == "https://tiles.example.org/legends/outlined.png"
        kvp = wmts_legend_url(document, "target", "default", "image/png", KVP_SOURCE)
        assert kvp == (
            "https://tiles.example.org/legends/outlined.png?SERVICE=WMS"
            "&VERSION=1.0.0&SLD_VERSION=1.1.0&REQUEST=GetLegendGraphic"
            "&FORMAT=image/png&LAYER=target&STYLE=default&TRANSPARENT=true"
        )

    def _document(self, legends: str) -> bytes:
        return (
            "<Capabilities xmlns='http://www.opengis.net/wmts/1.0' "
            "xmlns:ows='http://www.opengis.net/ows/1.1' "
            "xmlns:xlink='http://www.w3.org/1999/xlink' version='1.0.0'>"
            "<Contents><Layer><ows:Identifier>l</ows:Identifier><Style>"
            f"<ows:Identifier>s</ows:Identifier>{legends}</Style></Layer>"
            "</Contents></Capabilities>"
        ).encode()

    def test_the_layers_format_is_preferred(self) -> None:
        document = self._document(
            "<ows:legendURL><format>image/png</format><href>https://h/a.png</href>"
            "</ows:legendURL><ows:legendURL><format>image/jpeg</format>"
            "<href>https://h/b.jpg</href></ows:legendURL>"
        )
        jpeg = wmts_legend_url(document, "l", "s", "image/jpeg", REST_SOURCE)
        assert jpeg == "https://h/b.jpg"
        other = wmts_legend_url(document, "l", "s", "image/gif", REST_SOURCE)
        assert other == "https://h/a.png"

    def test_without_a_legend_url_or_the_style_there_is_none(self) -> None:
        document = self._document("")
        assert wmts_legend_url(document, "l", "s", "image/png", REST_SOURCE) is None
        other_style = wmts_legend_url(
            wmts_capabilities(WMTS).encode(), "target", "x", "image/png", REST_SOURCE
        )
        assert other_style is None
        assert wmts_legend_url(None, "l", "s", "image/png", REST_SOURCE) is None
