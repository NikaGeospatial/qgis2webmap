# ruff: noqa: E501 - the services' XML reads best one element to a line
"""A local WFS, OGC API Features and ArcGIS FeatureServer, for the live tests.

Nothing in the suite may reach a real feature service, and QGIS cannot create
a WFS, OGC API or ArcGIS layer without one answering it. So this serves all
three, on 127.0.0.1 and a free port, from a thread, with the same few points
(`STATIONS`):

* `/cors/...` sends `Access-Control-Allow-Origin: *`; `/nocors/...` sends
  none, as a service refusing cross-origin reads does. Everything else is the
  same under both.
* `wfs` answers WFS 2.0.0: GetCapabilities, DescribeFeatureType, and
  GetFeature as GeoJSON (`OUTPUTFORMAT=application/json`, the box latitude
  first in EPSG:4326, `COUNT`/`STARTINDEX` paging with `numberMatched`) or as
  GML 3.2 for QGIS.
* `ogc` answers OGC API Features: landing page, conformance, the `stations`
  collection and its `items` (`bbox`, `limit`, `offset`, `next` links).
* `arcgis/rest/services/Stations/FeatureServer/0` answers the layer's JSON
  and `query`, as `f=geojson` (paged by `resultOffset`/`resultRecordCount`
  with `exceededTransferLimit`, as ArcGIS Online does) or `f=json` for QGIS.

`failing` makes every request answer 500 until it is cleared, for a service
that is down and comes back. `requests` records every request, decoded.

The shapes follow the real services recorded in `fixtures/feature_services/`
(IGN France's Géoplateforme WFS, Canada's MSC GeoMet OGC API, EPA's EJScreen
FeatureServer). Stdlib only, so it runs in the QGIS and browser tiers alike.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit

# Twelve points in a 4 x 3 grid near Ottawa, 0.05 degrees apart.
STATIONS = tuple(
    {
        "id": index + 1,
        "lon": round(-75.80 + 0.05 * (index % 4), 6),
        "lat": round(45.30 + 0.05 * (index // 4), 6),
        "name": f"Station {index + 1}",
        "kind": "river" if index % 2 else "lake",
        "depth": round(1.5 + index * 0.25, 2),
    }
    for index in range(12)
)
EXTENT = (-75.85, 45.25, -75.60, 45.45)
TYPENAME = "hydro:stations"
COLLECTION = "stations"
ARCGIS_LAYER = "arcgis/rest/services/Stations/FeatureServer/0"


def _inside(station: dict, box: tuple[float, float, float, float]) -> bool:
    west, south, east, north = box
    return west <= station["lon"] <= east and south <= station["lat"] <= north


def _feature(station: dict) -> dict:
    return {
        "type": "Feature",
        "id": station["id"],
        "geometry": {"type": "Point", "coordinates": [station["lon"], station["lat"]]},
        "properties": {
            "name": station["name"],
            "kind": station["kind"],
            "depth": station["depth"],
        },
    }


def _box(text: str, latitude_first: bool) -> tuple[float, float, float, float]:
    values = [float(v) for v in text.split(",")[:4]]
    if latitude_first:
        return (values[1], values[0], values[3], values[2])
    return (values[0], values[1], values[2], values[3])


def _wfs_capabilities(base: str) -> str:
    west, south, east, north = EXTENT
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities version="2.0.0"
  xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:ows="http://www.opengis.net/ows/1.1"
  xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:hydro="http://example.org/hydro">
  <ows:ServiceIdentification><ows:Title>Fake WFS</ows:Title>
    <ows:ServiceType>WFS</ows:ServiceType><ows:ServiceTypeVersion>2.0.0</ows:ServiceTypeVersion>
  </ows:ServiceIdentification>
  <ows:OperationsMetadata>
    <ows:Operation name="GetCapabilities"><ows:DCP><ows:HTTP><ows:Get xlink:href="{base}"/></ows:HTTP></ows:DCP></ows:Operation>
    <ows:Operation name="DescribeFeatureType"><ows:DCP><ows:HTTP><ows:Get xlink:href="{base}"/></ows:HTTP></ows:DCP></ows:Operation>
    <ows:Operation name="GetFeature"><ows:DCP><ows:HTTP><ows:Get xlink:href="{base}"/></ows:HTTP></ows:DCP>
      <ows:Parameter name="outputFormat"><ows:AllowedValues>
        <ows:Value>application/gml+xml; version=3.2</ows:Value><ows:Value>application/json</ows:Value>
      </ows:AllowedValues></ows:Parameter>
    </ows:Operation>
    <ows:Constraint name="ImplementsResultPaging"><ows:NoValues/><ows:DefaultValue>TRUE</ows:DefaultValue></ows:Constraint>
    <ows:Constraint name="CountDefault"><ows:NoValues/><ows:DefaultValue>1000</ows:DefaultValue></ows:Constraint>
  </ows:OperationsMetadata>
  <wfs:FeatureTypeList><wfs:FeatureType>
    <wfs:Name>{TYPENAME}</wfs:Name><wfs:Title>Stations</wfs:Title>
    <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::4326</wfs:DefaultCRS>
    <ows:WGS84BoundingBox><ows:LowerCorner>{west} {south}</ows:LowerCorner>
      <ows:UpperCorner>{east} {north}</ows:UpperCorner></ows:WGS84BoundingBox>
  </wfs:FeatureType></wfs:FeatureTypeList>
  <fes:Filter_Capabilities><fes:Spatial_Capabilities><fes:GeometryOperands>
    <fes:GeometryOperand name="gml:Envelope"/></fes:GeometryOperands>
    <fes:SpatialOperators><fes:SpatialOperator name="BBOX"/></fes:SpatialOperators>
  </fes:Spatial_Capabilities></fes:Filter_Capabilities>
</wfs:WFS_Capabilities>"""


_WFS_SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:hydro="http://example.org/hydro"
  targetNamespace="http://example.org/hydro" elementFormDefault="qualified">
  <xsd:import namespace="http://www.opengis.net/gml/3.2"
    schemaLocation="http://schemas.opengis.net/gml/3.2.1/gml.xsd"/>
  <xsd:complexType name="stationsType"><xsd:complexContent>
    <xsd:extension base="gml:AbstractFeatureType"><xsd:sequence>
      <xsd:element name="geom" type="gml:PointPropertyType" minOccurs="0"/>
      <xsd:element name="name" type="xsd:string" minOccurs="0"/>
      <xsd:element name="kind" type="xsd:string" minOccurs="0"/>
      <xsd:element name="depth" type="xsd:double" minOccurs="0"/>
    </xsd:sequence></xsd:extension>
  </xsd:complexContent></xsd:complexType>
  <xsd:element name="stations" type="hydro:stationsType" substitutionGroup="gml:AbstractFeature"/>
</xsd:schema>"""


def _wfs_gml(stations: list[dict]) -> str:
    members = "".join(
        f"""<wfs:member><hydro:stations gml:id="stations.{s["id"]}">
  <hydro:geom><gml:Point gml:id="p{s["id"]}" srsName="urn:ogc:def:crs:EPSG::4326">
    <gml:pos>{s["lat"]} {s["lon"]}</gml:pos></gml:Point></hydro:geom>
  <hydro:name>{s["name"]}</hydro:name><hydro:kind>{s["kind"]}</hydro:kind>
  <hydro:depth>{s["depth"]}</hydro:depth></hydro:stations></wfs:member>"""
        for s in stations
    )
    count = len(stations)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:hydro="http://example.org/hydro"
  numberMatched="{count}" numberReturned="{count}" timeStamp="2026-11-04T00:00:00Z">
{members}</wfs:FeatureCollection>"""


_ARCGIS_FIELDS = [
    {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
    {"name": "name", "type": "esriFieldTypeString", "alias": "name", "length": 64},
    {"name": "kind", "type": "esriFieldTypeString", "alias": "kind", "length": 16},
    {"name": "depth", "type": "esriFieldTypeDouble", "alias": "depth"},
]


def _arcgis_layer() -> dict:
    west, south, east, north = EXTENT
    return {
        "currentVersion": 11.5,
        "id": 0,
        "name": "Stations",
        "type": "Feature Layer",
        "geometryType": "esriGeometryPoint",
        "objectIdField": "OBJECTID",
        "fields": _ARCGIS_FIELDS,
        "extent": {
            "xmin": west,
            "ymin": south,
            "xmax": east,
            "ymax": north,
            "spatialReference": {"wkid": 4326},
        },
        "maxRecordCount": 1000,
        "supportedQueryFormats": "JSON, geoJSON, PBF",
        "capabilities": "Query",
        "advancedQueryCapabilities": {"supportsPagination": True},
        "drawingInfo": {"renderer": {"type": "simple"}},
    }


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        raw = dict(parse_qsl(parts.query, keep_blank_values=True))
        params = {key.upper(): value for key, value in raw.items()}
        with self.server.lock:
            self.server.requests.append(
                {"path": parts.path, "params": raw, "origin": self.headers["Origin"]}
            )
            failing = self.server.failing
        segments = parts.path.strip("/").split("/")
        if failing:
            self._send(500, "text/plain", b"the service is down")
            return
        base = f"{self.server.base_url}/{'/'.join(segments[:2])}"
        service = segments[1] if len(segments) > 1 else ""
        rest = "/".join(segments[2:])
        if service == "wfs":
            self._wfs(params, base)
        elif service == "ogc":
            self._ogc(rest, raw, base)
        elif service == "arcgis" and "/".join(segments[1:]).startswith(ARCGIS_LAYER):
            self._arcgis("/".join(segments[1:])[len(ARCGIS_LAYER) :], raw)
        else:
            self._send(404, "text/plain", b"no such service")

    # ---- WFS -----------------------------------------------------------

    def _wfs(self, params: dict[str, str], base: str) -> None:
        request = params.get("REQUEST", "").lower()
        if request == "getcapabilities":
            self._send(200, "text/xml", _wfs_capabilities(base).encode())
            return
        if request == "describefeaturetype":
            self._send(200, "text/xml", _WFS_SCHEMA.encode())
            return
        if request != "getfeature":
            self._send(400, "text/plain", b"unsupported request")
            return
        stations = list(STATIONS)
        if params.get("BBOX"):
            text = params["BBOX"]
            stations = [s for s in stations if _inside(s, _box(text, "4326" in text))]
        start = int(params.get("STARTINDEX", "0"))
        count = int(params.get("COUNT", params.get("MAXFEATURES", "1000")))
        page = stations[start : start + count]
        if "json" in params.get("OUTPUTFORMAT", "").lower():
            body = {
                "type": "FeatureCollection",
                "features": [_feature(s) for s in page],
                "numberMatched": len(stations),
                "numberReturned": len(page),
                "crs": {
                    "type": "name",
                    "properties": {"name": "urn:ogc:def:crs:EPSG::4326"},
                },
            }
            self._send(200, "application/json", json.dumps(body).encode())
            return
        self._send(200, "application/gml+xml; version=3.2", _wfs_gml(page).encode())

    # ---- OGC API Features ------------------------------------------------

    def _ogc(self, rest: str, raw: dict[str, str], base: str) -> None:
        collection = {
            "id": COLLECTION,
            "title": "Stations",
            "extent": {"spatial": {"bbox": [list(EXTENT)]}},
            "itemType": "feature",
            "links": [
                {
                    "rel": "items",
                    "type": "application/geo+json",
                    "href": f"{base}/collections/{COLLECTION}/items",
                }
            ],
        }
        if rest == "":
            links = [
                {
                    "rel": "conformance",
                    "type": "application/json",
                    "href": f"{base}/conformance",
                },
                {
                    "rel": "data",
                    "type": "application/json",
                    "href": f"{base}/collections",
                },
            ]
            self._json({"title": "Fake OGC API", "links": links})
        elif rest == "conformance":
            core = "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/"
            self._json({"conformsTo": [core + c for c in ("core", "geojson", "oas30")]})
        elif rest == "collections":
            self._json({"collections": [collection], "links": []})
        elif rest == f"collections/{COLLECTION}":
            self._json(collection)
        elif rest == f"collections/{COLLECTION}/items":
            stations = list(STATIONS)
            if raw.get("bbox"):
                stations = [s for s in stations if _inside(s, _box(raw["bbox"], False))]
            offset = int(raw.get("offset", "0"))
            limit = int(raw.get("limit", "10"))
            page = stations[offset : offset + limit]
            links = []
            if offset + limit < len(stations):
                after = {**raw, "offset": str(offset + limit)}
                links.append(
                    {
                        "rel": "next",
                        "type": "application/geo+json",
                        "href": f"{base}/collections/{COLLECTION}/items?{urlencode(after)}",
                    }
                )
            self._json(
                {
                    "type": "FeatureCollection",
                    "features": [_feature(s) for s in page],
                    "numberMatched": len(stations),
                    "numberReturned": len(page),
                    "links": links,
                },
                "application/geo+json",
            )
        else:
            self._send(404, "text/plain", b"no such resource")

    # ---- ArcGIS FeatureServer ------------------------------------------

    def _arcgis(self, rest: str, raw: dict[str, str]) -> None:
        if rest in ("", "/"):
            self._json(_arcgis_layer())
            return
        if rest != "/query":
            self._send(404, "text/plain", b"no such resource")
            return
        stations = list(STATIONS)
        geometry = raw.get("geometry", "")
        if geometry and not geometry.startswith("{"):
            stations = [s for s in stations if _inside(s, _box(geometry, False))]
        elif geometry:
            envelope = json.loads(geometry)
            box = (
                envelope["xmin"],
                envelope["ymin"],
                envelope["xmax"],
                envelope["ymax"],
            )
            stations = [s for s in stations if _inside(s, box)]
        if raw.get("objectIds"):
            wanted = {int(v) for v in raw["objectIds"].split(",")}
            stations = [s for s in stations if s["id"] in wanted]
        if raw.get("returnIdsOnly") == "true":
            self._json(
                {
                    "objectIdFieldName": "OBJECTID",
                    "objectIds": [s["id"] for s in stations],
                }
            )
            return
        if raw.get("returnCountOnly") == "true":
            self._json({"count": len(stations)})
            return
        offset = int(raw.get("resultOffset", "0"))
        count = int(raw.get("resultRecordCount", "1000"))
        page = stations[offset : offset + count]
        more = offset + count < len(stations)
        if raw.get("f") == "geojson":
            body: dict = {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [_feature(s) for s in page],
            }
            if more:
                body["properties"] = {"exceededTransferLimit": True}
            self._json(body)
            return
        self._json(
            {
                "objectIdFieldName": "OBJECTID",
                "geometryType": "esriGeometryPoint",
                "spatialReference": {"wkid": 4326},
                "fields": _ARCGIS_FIELDS,
                "exceededTransferLimit": more,
                "features": [
                    {
                        "attributes": {
                            "OBJECTID": s["id"],
                            "name": s["name"],
                            "kind": s["kind"],
                            "depth": s["depth"],
                        },
                        "geometry": {"x": s["lon"], "y": s["lat"]},
                    }
                    for s in page
                ],
            }
        )

    # ---- answers -------------------------------------------------------

    def _json(self, body: dict, content_type: str = "application/json") -> None:
        self._send(200, content_type, json.dumps(body).encode())

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
    failing: bool


class FakeFeatureServices:
    """The services above, running until `close()` (or the `with` block ends)."""

    def __init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._server.lock = threading.Lock()
        self._server.requests = []
        self._server.failing = False
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-feature-services", daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def url(self, path: str) -> str:
        """`url("cors/wfs")` -> `http://127.0.0.1:<port>/cors/wfs`."""
        return f"{self.base_url}/{path}"

    @property
    def requests(self) -> list[dict[str, object]]:
        with self._server.lock:
            return list(self._server.requests)

    @property
    def failing(self) -> bool:
        with self._server.lock:
            return self._server.failing

    @failing.setter
    def failing(self, on: bool) -> None:
        with self._server.lock:
            self._server.failing = on

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeFeatureServices:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


if __name__ == "__main__":  # pragma: no cover - manual use
    import time

    services = FakeFeatureServices()
    print(services.base_url, flush=True)
    while True:
        time.sleep(3600)
