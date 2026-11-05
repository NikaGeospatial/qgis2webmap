"""WMS and WMTS sources as runtime tile templates (`core.map_services`).

The capabilities documents are the fake services' (`tests/fake_map_services.py`),
the same ones QGIS loads in the QGIS tier, plus small hand-written ones for the
cases a real service gets wrong or does differently.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
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
    TIME_TOKEN,
    WmtsRefusal,
    WmtsTiles,
    bbox_token,
    capabilities_url,
    feature_info_crs,
    feature_info_format,
    is_image_tile_template,
    parse_tile_dimensions,
    parse_time_dimension,
    parse_wms_capabilities,
    prepare_url,
    runtime_knows_crs,
    source_pairs,
    values,
    web_mercator_code,
    wms_feature_info_template,
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

    def test_a_document_declaring_an_entity_is_refused(self) -> None:
        """No capabilities document needs one; refusing it rules out entity
        expansion from a crafted service before the parser sees it."""
        document = (
            '<?xml version="1.0"?><!DOCTYPE WMS_Capabilities ['
            '<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
            + wms_capabilities_130(BASE).split("?>", 1)[-1]
        )
        assert parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parse_wms_capabilities(document.encode()) is None

    def test_a_doctype_without_entities_still_reads(self) -> None:
        """WMS 1.1.1 services often carry a DOCTYPE with an internal subset of
        element declarations; that is not an entity and must not be refused."""
        document = (
            '<?xml version="1.0"?><!DOCTYPE WMT_MS_Capabilities SYSTEM '
            '"http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd" '
            "[<!ELEMENT VendorSpecificCapabilities EMPTY>]>"
            + wms_capabilities_111(BASE).split("?>", 1)[-1]
        )
        parsed = parse_wms_capabilities(document.encode())
        assert parsed is not None
        assert parsed.offered_web_mercator(["target"]) == "EPSG:3857"

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
        jpeg = wms_get_map_template(
            BASE, "1.3.0", ["a"], [""], "image/jpeg", "EPSG:3857"
        )
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

    def test_a_slider_time_is_the_runtimes_token(self) -> None:
        template = wms_get_map_template(
            BASE, "1.3.0", ["a"], [""], "image/png", "EPSG:3857", time=TIME_TOKEN
        )
        assert "&TIME={time}&" in template

    @pytest.mark.parametrize(
        ("crs", "token"),
        [
            ("EPSG:3857", "{bbox-epsg-3857}"),
            # An older alias is asked as itself, in the same metres.
            ("EPSG:900913", "{bbox-epsg-3857}"),
            ("EPSG:25832", "{bbox-epsg-25832}"),
            ("EPSG:4326", "{bbox-epsg-4326}"),
            ("CRS:84", "{bbox-crs84}"),
        ],
    )
    def test_the_box_is_asked_in_the_requests_own_crs(self, crs, token) -> None:
        template = wms_get_map_template(BASE, "1.3.0", ["a"], [""], "image/png", crs)
        assert template.endswith(
            f"&CRS={crs}&WIDTH={{width}}&HEIGHT={{height}}&BBOX={token}"
        )

    def test_a_crs_with_no_token_is_not_written(self) -> None:
        assert bbox_token("IGNF:LAMB93") is None
        with pytest.raises(ValueError):
            wms_get_map_template(BASE, "1.3.0", ["a"], [""], "image/png", "IGNF:X")

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
    template = wms_get_map_template(
        BASE, "1.3.0", ["t"], [""], "image/png", "EPSG:3857"
    )
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

    def test_a_national_grid_is_addressed_by_its_own_matrices(self) -> None:
        """OnlyMap 0.13.4: `{TileMatrix}`/`{TileRow}`/`{TileCol}` and the grid
        as the capabilities state it, which the runtime warps into place."""
        tiles = _wmts(matrix_set="UTM32")
        assert isinstance(tiles, WmtsTiles)
        assert tiles.template == (
            WMTS + "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=target"
            "&STYLE=default&FORMAT=image/png&TILEMATRIXSET=UTM32"
            "&TILEMATRIX={TileMatrix}&TILEROW={TileRow}&TILECOL={TileCol}"
        )
        assert (tiles.min_zoom, tiles.max_zoom, tiles.tile_size) == (0, 9, None)
        grid = json.loads(tiles.tile_matrix_set or "")
        assert grid["supportedCRS"] == "urn:ogc:def:crs:EPSG::25832"
        assert len(grid["tileMatrix"]) == 10
        assert grid["tileMatrix"][0] == {
            "identifier": "0",
            "scaleDenominator": 17471320.75,
            "topLeftCorner": "-46133.17 6301219.54",
            "tileWidth": 256,
            "tileHeight": 256,
            "matrixWidth": 1,
            "matrixHeight": 1,
        }

    def test_the_web_mercator_grid_carries_no_matrix_set(self) -> None:
        tiles = _wmts()
        assert isinstance(tiles, WmtsTiles)
        assert tiles.tile_matrix_set is None

    def test_a_grid_in_a_crs_the_map_cannot_read_is_refused(self) -> None:
        document = _matrix_set(["0", "1"], crs="IGNF:LAMB93")
        refusal = _wmts(document=document, layer="l", matrix_set="s")
        assert refusal == WmtsRefusal("grid", grid="s")

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
    def test_identifiers_z_cannot_name_go_by_their_matrix(self, identifiers) -> None:
        """Web Mercator named another way: no `{z}` names its matrices, so the
        grid is given as any other is."""
        tiles = _wmts(document=_matrix_set(identifiers), layer="l", matrix_set="s")
        assert isinstance(tiles, WmtsTiles)
        assert "{TileMatrix}" in tiles.template and "{z}" not in tiles.template
        grid = json.loads(tiles.tile_matrix_set or "")
        assert [m["identifier"] for m in grid["tileMatrix"]] == [
            item[1] if isinstance(item, tuple) else item for item in identifiers
        ]

    def test_a_rest_address_on_a_projected_grid(self) -> None:
        document = _matrix_set(["0", "1"], crs="urn:ogc:def:crs:EPSG::25832")
        tiles = _wmts(document=document, layer="l", matrix_set="s")
        assert isinstance(tiles, WmtsTiles)
        assert tiles.template == (
            "https://t.example/s/{TileMatrix}/{TileCol}/{TileRow}.png"
        )
        assert tiles.tile_matrix_set is not None

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

    def test_a_rest_address_without_an_extension_is_imagery(self) -> None:
        """OnlyMap 0.13.4 draws any template as images unless it names a
        vector format; 0.13.3 needed the address to say so."""
        document = _matrix_set(["0", "1"]).replace(b"{TileRow}.png", b"{TileRow}")
        tiles = _wmts(document=document, layer="l", matrix_set="s")
        assert isinstance(tiles, WmtsTiles)
        assert tiles.template.endswith("/{x}/{y}")

    def test_a_rest_address_naming_a_vector_format_is_refused(self) -> None:
        document = _matrix_set(["0", "1"]).replace(b"{TileRow}.png", b"{TileRow}.pbf")
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
    """The runtime's own test of what it draws as images (0.13.4)."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://t.example/{z}/{x}/{y}.png",
            "https://t.example/{z}/{x}/{y}.jpeg?k=1",
            BASE + "?SERVICE=WMS&FORMAT=image/png&BBOX={bbox-epsg-3857}",
            BASE + "?SERVICE=WMS&FORMAT=image%2Fjpeg&BBOX={bbox-epsg-3857}",
            "https://h/MapServer/export?bbox={bbox-epsg-3857}&format=png",
            # No extension: an ArcGIS cache, a Google-style address, TMS.
            "https://h/MapServer/tile/{z}/{y}/{x}",
            "https://mt1.example/vt/lyrs=s&x={x}&y={y}&z={z}",
            "https://t.example/tms/{z}/{x}/{-y}",
        ],
    )
    def test_imagery(self, url: str) -> None:
        assert is_image_tile_template(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://t.example/{z}/{x}/{y}.pbf",
            "https://t.example/{z}/{x}/{y}.mvt",
            "https://h/FeatureServer/0/query?f=json&bbox={bbox-epsg-3857}",
            "https://h/tiles/{z}/{x}/{y}?format=pbf",
            "https://h/wfs?outputFormat=application/json&BBOX={bbox-epsg-3857}",
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


class TestTimeDimension:
    """The times a WMS-T's slider steps, spelled as the runtime spells them
    (OnlyMap 0.13.4 `parseTimeDimension`), so the step QGIS draws is found."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "2020-01-01T00:00:00Z/2020-01-03T00:00:00Z/P1D",
                [
                    "2020-01-01T00:00:00Z",
                    "2020-01-02T00:00:00Z",
                    "2020-01-03T00:00:00Z",
                ],
            ),
            # A date-only start steps in dates; month ends are kept or cut.
            (
                "2020-01-31/2020-04-30/P1M",
                ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"],
            ),
            ("2019/2021/P1Y", ["2019", "2020", "2021"]),
            # Instants are kept as written; a start/end with no period is not read.
            (
                " 2020-05-01 ,2020-06-01/2020-06-02,2020-07-01",
                ["2020-05-01", "2020-07-01"],
            ),
            (
                "2020-01-01T00:00/2020-01-01T01:00/PT30M",
                [
                    "2020-01-01T00:00:00Z",
                    "2020-01-01T00:30:00Z",
                    "2020-01-01T01:00:00Z",
                ],
            ),
            ("2020-01-01/2020-01-02/P", []),
            ("", []),
        ],
    )
    def test_steps(self, text, expected) -> None:
        assert parse_time_dimension(text) == expected

    def test_a_limit_stops_the_count(self) -> None:
        steps = parse_time_dimension("2000-01-01/2030-01-01/P1D", limit=2000)
        assert len(steps) == 2000
        assert steps[-1] == "2005-06-22"


@pytest.mark.parametrize(
    ("code", "known"),
    [
        ("EPSG:25832", True),
        ("urn:ogc:def:crs:EPSG::2056", True),
        ("EPSG:32733", True),  # a UTM zone, written from its code
        ("CRS:84", True),
        ("EPSG:3067", False),
        ("EPSG:31467", False),
        ("IGNF:LAMB93", False),
    ],
)
def test_which_crs_the_runtime_places_without_a_lookup(code, known) -> None:
    assert runtime_knows_crs(code) is known


# PDOK's BRT Achtergrondkaart WMTS capabilities, as served on 2026-11-04: a
# national grid (EPSG:28992), and Web Mercator with zero-padded matrix names.
PDOK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "pdok-brt-wmts-capabilities.xml"
)


class TestARealNationalWmts:
    def test_the_dutch_grid_travels_as_the_capabilities_state_it(self) -> None:
        tiles = wmts_tiles(
            PDOK.read_bytes(), "standaard", "default", "image/png", "EPSG:28992", {}
        )
        assert isinstance(tiles, WmtsTiles)
        assert "&TILEMATRIXSET=EPSG%3A28992&TILEMATRIX={TileMatrix}&" in tiles.template
        grid = json.loads(tiles.tile_matrix_set or "")
        assert grid["supportedCRS"] == "urn:ogc:def:crs:EPSG::28992"
        identifiers = [matrix["identifier"] for matrix in grid["tileMatrix"]]
        assert identifiers == [f"{z:02d}" for z in range(15)]
        assert (tiles.min_zoom, tiles.max_zoom) == (0, 14)

    def test_zero_padded_web_mercator_names_now_export(self) -> None:
        """`00`..`19`: no `{z}` names them, which 0.3.2 refused."""
        tiles = wmts_tiles(
            PDOK.read_bytes(), "standaard", "default", "image/png", "EPSG:3857", {}
        )
        assert isinstance(tiles, WmtsTiles)
        assert tiles.tile_matrix_set is not None


# --------------------------------------------------------------------------
# GetFeatureInfo: what a click asks
# --------------------------------------------------------------------------

FEATURE_INFO = Path(__file__).resolve().parents[1] / "fixtures" / "feature_info"


def _capabilities(name: str):
    parsed = parse_wms_capabilities((FEATURE_INFO / name).read_bytes())
    assert parsed is not None
    return parsed


def _params(url: str) -> dict[str, str]:
    return dict(parse_qsl(url.partition("?")[2], keep_blank_values=True))


# Each real service's capabilities, the layer asked, and what the request
# sent to it on 2026-11-05 carried (tests/fixtures/README.md), its BBOX the
# one asked for the recorded hit.
REAL_SERVICES = [
    pytest.param(
        "geoserver-dwd-capabilities.xml",
        "1.3.0",
        "EPSG:4326",
        "Warngebiete_Kreise",
        "https://maps.dwd.de/geoserver/dwd/ows",
        {"SERVICE": "WMS", "CRS": "EPSG:4326", "I": "50", "J": "50"},
        "application/json",
        "52.0795,11.5695,52.1805,11.6705",
        id="geoserver",
    ),
    pytest.param(
        "mapserver-pdok-capabilities.xml",
        "1.3.0",
        "EPSG:3857",
        "Gemeentegebied",
        "https://service.pdok.nl/kadaster/brk-bestuurlijke-gebieden/wms/v1_0",
        {"CRS": "EPSG:3857", "I": "50", "J": "50"},
        "application/json",
        "544960.5,6866799.7,545970.5,6867809.7",
        id="mapserver",
    ),
    pytest.param(
        "arcgis-usgs-capabilities.xml",
        "1.1.1",
        "EPSG:4326",
        "19",
        "https://carto.nationalmap.gov:443/arcgis/services/govunits/MapServer/WMSServer",
        {"SRS": "EPSG:4326", "X": "50", "Y": "50"},
        "application/geo+json",
        "-105.0405,39.6895,-104.9395,39.7905",
        id="arcgis",
    ),
    pytest.param(
        "qgis-solothurn-capabilities.xml",
        "1.3.0",
        "EPSG:3857",
        "ch.so.alw.strukturverbesserungen.gemeinde_flurreglemente",
        "https://geo.so.ch/api/v1/featureinfo/somap",
        {"CRS": "EPSG:3857", "I": "50", "J": "50"},
        "text/xml",
        "838772.4,5977000.0,839277.4,5977505.0",
        id="qgis-server",
    ),
]


class TestFeatureInfo:
    @pytest.mark.parametrize(
        ("name", "version", "crs", "layer", "address", "sent", "info_format", "bbox"),
        REAL_SERVICES,
    )
    def test_a_real_service_is_asked_as_it_was_asked(
        self, name, version, crs, layer, address, sent, info_format, bbox
    ) -> None:
        """The request built from each real service's capabilities, filled with
        the box asked on the day, is the one whose answer is recorded: at the
        GetFeatureInfo address the capabilities give (Solothurn's is not its
        GetMap's), for the answer the service lists that the popup reads best."""
        parsed = _capabilities(name)
        assert parsed.layer_queryable[layer] is True
        chosen = feature_info_format(parsed.feature_info_formats)
        assert chosen == info_format
        assert parsed.get_feature_info_url is not None
        assert feature_info_crs(parsed, [layer], crs, version) == crs
        template = wms_feature_info_template(
            prepare_url(parsed.get_feature_info_url).rstrip("?&"),
            version,
            [layer],
            [""],
            "image/png",
            crs,
            [layer],
            chosen,
        )
        filled = (
            template.replace(bbox_token(crs) or "", bbox)
            .replace("{width}", "101")
            .replace("{height}", "101")
            .replace("{i}", "50")
            .replace("{j}", "50")
        )
        assert filled.partition("?")[0] == address
        params = _params(filled)
        assert params == {
            **params,
            **sent,
            "VERSION": version,
            "REQUEST": "GetFeatureInfo",
            "LAYERS": layer,
            "QUERY_LAYERS": layer,
            "STYLES": "",
            "FORMAT": "image/png",
            "INFO_FORMAT": info_format,
            "FEATURE_COUNT": "10",
            "WIDTH": "101",
            "HEIGHT": "101",
            "BBOX": bbox,
        }

    def test_a_layer_in_a_group_that_answers_nothing_still_answers(self) -> None:
        """Solothurn's: `queryable` is the layer's own, and inherited only
        where a layer says nothing."""
        parsed = _capabilities("qgis-solothurn-capabilities.xml")
        assert parsed.layer_queryable["ch.so.alw.strukturverbesserungen"] is False
        assert parsed.layer_queryable["ch.so.agi.grundbuchplan_oereb"] is False
        assert parsed.layer_queryable["somap"] is True
        document = (
            "<WMS_Capabilities version='1.3.0' xmlns='http://www.opengis.net/wms'>"
            "<Capability><Layer queryable='1'><Name>root</Name>"
            "<Layer><Name>says_nothing</Name></Layer>"
            "<Layer queryable='0'><Name>off</Name>"
            "<Layer><Name>off_child</Name></Layer></Layer>"
            "</Layer><Layer><Name>no_root</Name></Layer>"
            "</Capability></WMS_Capabilities>"
        )
        parsed = parse_wms_capabilities(document.encode())
        assert parsed is not None
        assert parsed.layer_queryable == {
            "root": True,
            "says_nothing": True,
            "off": False,
            "off_child": False,
            "no_root": False,
        }
        assert parsed.get_feature_info_url is None
        assert parsed.feature_info_formats == ()

    @pytest.mark.parametrize(
        ("formats", "chosen"),
        [
            # GeoServer's list: JSON, wherever it is listed.
            (
                [
                    "text/plain",
                    "application/vnd.ogc.gml",
                    "text/html",
                    "application/json",
                ],
                "application/json",
            ),
            (
                ["application/vnd.esri.wms_raw_xml", "application/geo+json"],
                "application/geo+json",
            ),
            (
                ["application/json; subtype=geojson"],
                "application/json; subtype=geojson",
            ),
            # Then GML or the server's own XML, in the service's order.
            (
                ["text/html", "text/xml; subtype=gml/3.1.1", "application/vnd.ogc.gml"],
                "text/xml; subtype=gml/3.1.1",
            ),
            (
                ["text/plain", "application/vnd.esri.wms_raw_xml"],
                "application/vnd.esri.wms_raw_xml",
            ),
            # Then text, then a page.
            (["text/html", "text/plain"], "text/plain"),
            (["text/html"], "text/html"),
            # The capabilities' own type and exceptions are no answer.
            (["application/vnd.ogc.wms_xml", "application/vnd.ogc.se_xml"], None),
            (["text/javascript", "image/png"], None),
            ([], None),
        ],
    )
    def test_the_answer_asked_for(self, formats, chosen) -> None:
        assert feature_info_format(formats) == chosen

    def test_130_names_the_pixel_i_j_and_111_x_y(self) -> None:
        common = (["boundary", "target"], ["", "outlined"], "image/png")
        new = wms_feature_info_template(
            BASE, "1.3.0", *common, "EPSG:3857", ["target"], "application/geo+json", 7
        )
        old = wms_feature_info_template(
            BASE, "1.1.1", *common, "EPSG:4326", ["target"], "text/plain"
        )
        assert new == (
            BASE + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
            "&LAYERS=boundary,target&STYLES=,outlined&FORMAT=image/png"
            "&TRANSPARENT=TRUE&QUERY_LAYERS=target"
            "&INFO_FORMAT=application/geo%2Bjson&FEATURE_COUNT=7&CRS=EPSG:3857"
            "&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-3857}&I={i}&J={j}"
        )
        assert old.endswith(
            "&QUERY_LAYERS=target&INFO_FORMAT=text/plain&FEATURE_COUNT=10"
            "&SRS=EPSG:4326&WIDTH={width}&HEIGHT={height}&BBOX={bbox-epsg-4326}"
            "&X={i}&Y={j}"
        )
        assert "CRS=" not in old and "&I=" not in old

    def test_the_getmap_it_repeats_keeps_its_own_parts(self) -> None:
        """The service's own parameters, FILTER and a WMS-T's time, as the
        layer's GetMap carries them; ours replace any the address names."""
        template = wms_feature_info_template(
            BASE + "?map=/srv/a.map&QUERY_LAYERS=x&info_format=text/html",
            "1.3.0",
            ["target"],
            [""],
            "image/jpeg",
            "CRS:84",
            ["target"],
            "application/json",
            extra=[("FILTER", "(a)")],
            time=TIME_TOKEN,
        )
        assert template.startswith(BASE + "?map=/srv/a.map&SERVICE=WMS&")
        params = _params(template)
        assert params["QUERY_LAYERS"] == "target"
        assert params["INFO_FORMAT"] == "application/json"
        assert "info_format" not in params
        assert "TRANSPARENT" not in params
        assert (params["FILTER"], params["TIME"]) == ("(a)", TIME_TOKEN)
        assert (params["CRS"], params["BBOX"]) == ("CRS:84", "{bbox-crs84}")

    def test_a_click_is_asked_in_the_tiles_crs_or_in_degrees(self) -> None:
        parsed = parse_wms_capabilities(wms_capabilities_130(BASE).encode())
        assert parsed is not None
        for crs in ("EPSG:3857", "EPSG:900913", "EPSG:4326", "CRS:84"):
            assert feature_info_crs(parsed, ["target"], crs, "1.3.0") == crs
        # Tiles warped from EPSG:25832: the same point, in longitude and latitude.
        assert feature_info_crs(parsed, ["target"], "EPSG:25832", "1.3.0") == (
            "EPSG:4326"
        )
        assert feature_info_crs(parsed, ["utm_only"], "EPSG:25832", "1.3.0") is None
        assert (
            feature_info_crs(parsed, ["target", "utm_only"], "EPSG:25832", "1.3.0")
            is None
        )
        only_crs84 = parse_wms_capabilities(
            b"<WMS_Capabilities version='1.3.0'><Capability><Layer><Name>a</Name>"
            b"<CRS>EPSG:2056</CRS><CRS>CRS:84</CRS></Layer></Capability>"
            b"</WMS_Capabilities>"
        )
        assert only_crs84 is not None
        assert feature_info_crs(only_crs84, ["a"], "EPSG:2056", "1.3.0") == "CRS:84"
        # WMS 1.1.1 has no CRS:84.
        assert feature_info_crs(only_crs84, ["a"], "EPSG:2056", "1.1.1") is None

    def test_a_crs_the_page_cannot_place_a_click_in_is_refused(self) -> None:
        with pytest.raises(ValueError):
            wms_feature_info_template(
                BASE, "1.3.0", ["a"], [""], "image/png", "EPSG:25832", ["a"], "text/xml"
            )

    def test_the_fake_service_answers_as_its_capabilities_say(self) -> None:
        for document in (wms_capabilities_130(BASE), wms_capabilities_111(BASE)):
            parsed = parse_wms_capabilities(document.encode())
            assert parsed is not None
            assert parsed.get_feature_info_url == BASE + "?"
            assert parsed.layer_queryable == {
                "target": True,
                "boundary": False,
                "utm_only": True,
            }
            assert feature_info_format(parsed.feature_info_formats) == (
                "application/json"
            )
