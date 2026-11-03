"""A local WMS and WMTS that answer like real ones, for the tile-service tests.

Nothing in the suite may reach a real map service, and a WMS layer cannot
even be created in QGIS without one answering its GetCapabilities. So this
serves both, on 127.0.0.1 and a free port, from a thread:

* `/cors/...` sends `Access-Control-Allow-Origin: *`; `/nocors/...` sends no
  CORS header at all, the way the Sachsen-Anhalt ALKIS service of issue #2
  does. Everything else is the same under both.
* `wms` answers WMS 1.3.0 and `wms111` answers WMS 1.1.1, each with layers
  `target` (offered in EPSG:3857, EPSG:25832 and EPSG:4326), `boundary`
  (the same) and `utm_only` (EPSG:25832 only). An unknown layer gets a
  ServiceException, served with status 200 as real servers do.
* Legends: both declare GetLegendGraphic, which draws `LEGEND_SIZES[layer]`.
  `target` has the styles `default`, with no LegendURL, and `outlined`,
  whose LegendURL is `legends/outlined.png` (`OUTLINED_LEGEND_SIZE`);
  `boundary`'s GetLegendGraphic picture is tall, as long legends are.
* `wmts` offers layer `target` on the `GoogleMapsCompatible` grid and on a
  `UTM32` grid, by KVP GetTile and by a RESTful `ResourceURL`; its `default`
  style's LegendURL is the same `legends/outlined.png`.

Every image draws one thing: `TARGET_3857`, a rectangle given in Web
Mercator metres, opaque crimson, on transparency. A tile therefore shows the
rectangle where it truly is only if the bbox it was asked for is the one it
covers - which is what a placement test needs. A request in any other CRS
gets the whole image half-transparent blue, enough to show that QGIS itself
drew the layer. `requests` records every request, decoded, so a test can
compare the BBOX the map asked for with the tile it wanted.

Stdlib only, so it runs in the QGIS tier, the browser tier and QGIS 3.44's
container alike.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

# Around Magdeburg (11.5-11.75 E, 52.05-52.2 N), where issue #2's layers are.
TARGET_3857 = (1280174.0, 6813860.0, 1308004.0, 6841175.0)
TARGET_RGBA = (220, 20, 60, 255)
OTHER_CRS_RGBA = (30, 90, 200, 128)
HALF_WORLD = math.pi * 6378137.0

WMS_LAYERS = {"target": ("EPSG:3857", "EPSG:25832", "EPSG:4326")}
WMS_LAYERS["boundary"] = WMS_LAYERS["target"]
WMS_LAYERS["utm_only"] = ("EPSG:25832",)
WMS_TITLES = {"target": "Target area", "boundary": "Boundary", "utm_only": "UTM only"}
# The (width, height) of each layer's GetLegendGraphic picture, and of the
# `outlined` style's LegendURL picture.
LEGEND_SIZES = {"target": (64, 32), "boundary": (48, 600), "utm_only": (40, 20)}
OUTLINED_LEGEND_SIZE = (40, 40)
LEGEND_RGBA = (20, 120, 60, 255)


def tile_bounds_3857(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """A Web Mercator tile's extent in metres: minx, miny, maxx, maxy."""
    size = 2 * HALF_WORLD / 2**z
    west = -HALF_WORLD + x * size
    north = HALF_WORLD - y * size
    return (west, north - size, west + size, north)


def png(width: int, height: int, pixel) -> bytes:
    """An RGBA PNG whose pixel (column, row) is `pixel(column, row)`."""
    rows = bytearray()
    for row in range(height):
        rows.append(0)
        for column in range(width):
            rows.extend(pixel(column, row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def draw_target(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> bytes:
    """The image a correct server returns for `bbox` (EPSG:3857 metres)."""
    minx, miny, maxx, maxy = bbox
    target_minx, target_miny, target_maxx, target_maxy = TARGET_3857
    clear = (0, 0, 0, 0)
    columns = [
        target_minx <= minx + (maxx - minx) * (c + 0.5) / width <= target_maxx
        for c in range(width)
    ]
    rows = [
        target_miny <= maxy - (maxy - miny) * (r + 0.5) / height <= target_maxy
        for r in range(height)
    ]
    return png(
        width,
        height,
        lambda c, r: TARGET_RGBA if columns[c] and rows[r] else clear,
    )


def _styles(base: str, name: str, xlink: str = "") -> str:
    """`target`'s two styles; no other layer declares any."""
    if name != "target":
        return ""
    legends = base.rpartition("/")[0] + "/legends/outlined.png"
    return (
        "<Style><Name>default</Name><Title>Default</Title></Style>"
        "<Style><Name>outlined</Name><Title>Outlined</Title>"
        "<LegendURL width='40' height='40'><Format>image/png</Format>"
        f"<OnlineResource {xlink}xlink:type='simple' xlink:href='{legends}'/>"
        "</LegendURL></Style>"
    )


def wms_capabilities_130(base: str) -> str:
    layers = "".join(
        f"<Layer queryable='0'><Name>{name}</Name><Title>{WMS_TITLES[name]}</Title>"
        + "".join(f"<CRS>{crs}</CRS>" for crs in crs_list)
        + "<EX_GeographicBoundingBox><westBoundLongitude>5</westBoundLongitude>"
        "<eastBoundLongitude>16</eastBoundLongitude><southBoundLatitude>47"
        "</southBoundLatitude><northBoundLatitude>56</northBoundLatitude>"
        "</EX_GeographicBoundingBox>"
        f"{_styles(base, name)}</Layer>"
        for name, crs_list in WMS_LAYERS.items()
    )
    online = f"<OnlineResource xlink:type='simple' xlink:href='{base}?'/>"
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<WMS_Capabilities version='1.3.0' xmlns='http://www.opengis.net/wms' "
        "xmlns:sld='http://www.opengis.net/sld' "
        "xmlns:xlink='http://www.w3.org/1999/xlink'>"
        "<Service><Name>WMS</Name><Title>Fake WMS</Title>"
        f"{online}</Service><Capability><Request>"
        "<GetCapabilities><Format>text/xml</Format><DCPType><HTTP><Get>"
        f"{online}</Get></HTTP></DCPType></GetCapabilities>"
        "<GetMap><Format>image/png</Format><Format>image/jpeg</Format>"
        f"<DCPType><HTTP><Get>{online}</Get></HTTP></DCPType></GetMap>"
        # 1.3.0 has no GetLegendGraphic of its own; it is the SLD profile's.
        "<sld:GetLegendGraphic><Format>image/png</Format>"
        f"<DCPType><HTTP><Get>{online}</Get></HTTP></DCPType>"
        "</sld:GetLegendGraphic></Request><Exception><Format>XML</Format></Exception>"
        "<Layer><Title>Fake</Title><CRS>EPSG:25832</CRS>"
        f"{layers}</Layer></Capability></WMS_Capabilities>"
    )


_XLINK = "http://www.w3.org/1999/xlink"


def wms_capabilities_111(base: str) -> str:
    layers = "".join(
        f"<Layer queryable='0'><Name>{name}</Name><Title>{WMS_TITLES[name]}</Title>"
        + "".join(f"<SRS>{crs}</SRS>" for crs in crs_list)
        + "<LatLonBoundingBox minx='5' miny='47' maxx='16' maxy='56'/>"
        + _styles(base, name, xlink=f"xmlns:xlink='{_XLINK}' ")
        + "</Layer>"
        for name, crs_list in WMS_LAYERS.items()
    )
    online = (
        f"<OnlineResource xmlns:xlink='{_XLINK}' "
        f"xlink:type='simple' xlink:href='{base}?'/>"
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<WMT_MS_Capabilities version='1.1.1'>"
        f"<Service><Name>OGC:WMS</Name><Title>Fake WMS 1.1.1</Title>{online}"
        "</Service><Capability><Request>"
        "<GetCapabilities><Format>application/vnd.ogc.wms_xml</Format>"
        f"<DCPType><HTTP><Get>{online}</Get></HTTP></DCPType></GetCapabilities>"
        "<GetMap><Format>image/png</Format>"
        f"<DCPType><HTTP><Get>{online}</Get></HTTP></DCPType></GetMap>"
        "<GetLegendGraphic><Format>image/png</Format>"
        f"<DCPType><HTTP><Get>{online}</Get></HTTP></DCPType></GetLegendGraphic>"
        "</Request><Exception><Format>application/vnd.ogc.se_xml</Format>"
        f"</Exception><Layer><Title>Fake</Title>{layers}</Layer>"
        "</Capability></WMT_MS_Capabilities>"
    )


def wmts_capabilities(base: str) -> str:
    # GoogleMapsCompatible: scale denominator 559082264.0287178 at zoom 0,
    # halving per level; top-left at (-HALF_WORLD, HALF_WORLD).
    google = "".join(
        f"<TileMatrix><ows:Identifier>{z}</ows:Identifier>"
        f"<ScaleDenominator>{559082264.0287178 / 2**z}</ScaleDenominator>"
        f"<TopLeftCorner>{-HALF_WORLD} {HALF_WORLD}</TopLeftCorner>"
        "<TileWidth>256</TileWidth><TileHeight>256</TileHeight>"
        f"<MatrixWidth>{2**z}</MatrixWidth><MatrixHeight>{2**z}</MatrixHeight>"
        "</TileMatrix>"
        for z in range(19)
    )
    utm = "".join(
        f"<TileMatrix><ows:Identifier>{z}</ows:Identifier>"
        f"<ScaleDenominator>{17471320.75 / 2**z}</ScaleDenominator>"
        "<TopLeftCorner>-46133.17 6301219.54</TopLeftCorner>"
        "<TileWidth>256</TileWidth><TileHeight>256</TileHeight>"
        f"<MatrixWidth>{2**z}</MatrixWidth><MatrixHeight>{2**z}</MatrixHeight>"
        "</TileMatrix>"
        for z in range(10)
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<Capabilities xmlns='http://www.opengis.net/wmts/1.0' "
        "xmlns:ows='http://www.opengis.net/ows/1.1' "
        "xmlns:xlink='http://www.w3.org/1999/xlink' version='1.0.0'>"
        "<ows:ServiceIdentification><ows:Title>Fake WMTS</ows:Title>"
        "<ows:ServiceType>OGC WMTS</ows:ServiceType>"
        "<ows:ServiceTypeVersion>1.0.0</ows:ServiceTypeVersion>"
        "</ows:ServiceIdentification><ows:OperationsMetadata>"
        "<ows:Operation name='GetCapabilities'><ows:DCP><ows:HTTP>"
        f"<ows:Get xlink:href='{base}?'><ows:Constraint name='GetEncoding'>"
        "<ows:AllowedValues><ows:Value>KVP</ows:Value></ows:AllowedValues>"
        "</ows:Constraint></ows:Get></ows:HTTP></ows:DCP></ows:Operation>"
        "<ows:Operation name='GetTile'><ows:DCP><ows:HTTP>"
        f"<ows:Get xlink:href='{base}?'><ows:Constraint name='GetEncoding'>"
        "<ows:AllowedValues><ows:Value>KVP</ows:Value></ows:AllowedValues>"
        "</ows:Constraint></ows:Get></ows:HTTP></ows:DCP></ows:Operation>"
        "</ows:OperationsMetadata><Contents>"
        "<Layer><ows:Title>target</ows:Title><ows:Identifier>target</ows:Identifier>"
        "<ows:WGS84BoundingBox><ows:LowerCorner>5 47</ows:LowerCorner>"
        "<ows:UpperCorner>16 56</ows:UpperCorner></ows:WGS84BoundingBox>"
        "<Style isDefault='true'><ows:Identifier>default</ows:Identifier>"
        "<LegendURL format='image/png' "
        f"xlink:href='{base.rpartition('/')[0]}/legends/outlined.png'/></Style>"
        "<Format>image/png</Format>"
        "<TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible</TileMatrixSet>"
        "</TileMatrixSetLink><TileMatrixSetLink><TileMatrixSet>UTM32"
        "</TileMatrixSet></TileMatrixSetLink>"
        "<ResourceURL format='image/png' resourceType='tile' "
        f"template='{base}/tiles/{{TileMatrixSet}}/{{TileMatrix}}/{{TileRow}}/"
        "{TileCol}.png'/></Layer>"
        "<TileMatrixSet><ows:Identifier>GoogleMapsCompatible</ows:Identifier>"
        "<ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>"
        "<WellKnownScaleSet>urn:ogc:def:wkss:OGC:1.0:GoogleMapsCompatible"
        f"</WellKnownScaleSet>{google}</TileMatrixSet>"
        "<TileMatrixSet><ows:Identifier>UTM32</ows:Identifier>"
        "<ows:SupportedCRS>urn:ogc:def:crs:EPSG::25832</ows:SupportedCRS>"
        f"{utm}</TileMatrixSet></Contents></Capabilities>"
    )


def _service_exception(message: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<ServiceExceptionReport version='1.3.0' "
        "xmlns='http://www.opengis.net/ogc'>"
        f"<ServiceException code='LayerNotDefined'>{message}</ServiceException>"
        "</ServiceExceptionReport>"
    ).encode()


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        params = {key.upper(): value for key, value in parse_qsl(parts.query)}
        segments = [s for s in parts.path.split("/") if s]
        record = {"path": parts.path, "params": params}
        with self.server.lock:
            self.server.requests.append(record)
        if len(segments) < 2 or segments[0] not in ("cors", "nocors"):
            self._send(404, "text/plain", b"not found")
            return
        base = f"{self.server.base_url}/{segments[0]}/{segments[1]}"
        service = segments[1]
        request = params.get("REQUEST", "").lower()
        if service in ("wms", "wms111"):
            if request == "getcapabilities":
                document = (
                    wms_capabilities_111(base)
                    if service == "wms111"
                    else wms_capabilities_130(base)
                )
                self._send(200, "text/xml", document.encode())
                return
            if request == "getmap":
                self._get_map(params, service)
                return
            if request == "getlegendgraphic":
                self._get_legend_graphic(params)
                return
        if service == "legends" and segments[2:] == ["outlined.png"]:
            width, height = OUTLINED_LEGEND_SIZE
            self._send(200, "image/png", png(width, height, lambda c, r: LEGEND_RGBA))
            return
        if service == "wmts":
            if request == "getcapabilities":
                self._send(200, "text/xml", wmts_capabilities(base).encode())
                return
            if request == "gettile":
                self._get_tile(
                    params.get("TILEMATRIXSET", ""),
                    params.get("TILEMATRIX", ""),
                    params.get("TILEROW", ""),
                    params.get("TILECOL", ""),
                )
                return
            if len(segments) == 7 and segments[2] == "tiles":
                self._get_tile(*segments[3:6], segments[6].split(".")[0])
                return
        self._send(400, "text/plain", b"unsupported request")

    def _get_map(self, params: dict[str, str], service: str) -> None:
        unknown = [
            name
            for name in params.get("LAYERS", "").split(",")
            if name not in WMS_LAYERS
        ]
        if unknown:
            message = f"Layer '{unknown[0]}' is not defined"
            self._send(200, "application/vnd.ogc.se_xml", _service_exception(message))
            return
        crs = params.get("SRS" if service == "wms111" else "CRS", "")
        width = int(params.get("WIDTH", "256"))
        height = int(params.get("HEIGHT", "256"))
        bbox = tuple(float(v) for v in params.get("BBOX", "0,0,1,1").split(","))
        if crs == "EPSG:3857":
            body = draw_target(bbox, width, height)  # type: ignore[arg-type]
        else:
            body = png(width, height, lambda c, r: OTHER_CRS_RGBA)
        self._send(200, "image/png", body)

    def _get_legend_graphic(self, params: dict[str, str]) -> None:
        layer = params.get("LAYER", "")
        if layer not in LEGEND_SIZES:
            message = f"Layer '{layer}' is not defined"
            self._send(200, "application/vnd.ogc.se_xml", _service_exception(message))
            return
        width, height = LEGEND_SIZES[layer]
        self._send(200, "image/png", png(width, height, lambda c, r: LEGEND_RGBA))

    def _get_tile(self, matrix_set: str, matrix: str, row: str, column: str) -> None:
        if matrix_set != "GoogleMapsCompatible":
            self._send(200, "image/png", png(256, 256, lambda c, r: OTHER_CRS_RGBA))
            return
        bounds = tile_bounds_3857(int(column), int(row), int(matrix))
        self._send(200, "image/png", draw_target(bounds, 256, 256))

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.path.startswith("/cors/"):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    base_url: str
    lock: threading.Lock
    requests: list[dict[str, object]]


class FakeMapServices:
    """The services above, running until `close()` (or the `with` block ends)."""

    def __init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._server.lock = threading.Lock()
        self._server.requests = []
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-map-services", daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def url(self, path: str) -> str:
        """`url("cors/wms")` -> `http://127.0.0.1:<port>/cors/wms`."""
        return f"{self.base_url}/{path}"

    @property
    def requests(self) -> list[dict[str, object]]:
        with self._server.lock:
            return list(self._server.requests)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeMapServices:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


if __name__ == "__main__":  # pragma: no cover - manual use
    import time

    services = FakeMapServices()
    print(services.base_url, flush=True)
    while True:
        time.sleep(3600)
