"""Live features from web feature services: the requests, the mode, the markup.

Pure: the requests are checked against what the runtime reads (OnlyMap
0.13.4's `feature-service.ts`: which service it takes a URL for, how it pages
it, which axis order it fills the box in) and against the real services
recorded in `tests/fixtures/feature_services/`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import (
    Color,
    ExportLayer,
    ExportProject,
    ExportSettings,
    Extent,
    FilterKind,
    GeometryKind,
    LabelingSpec,
    LayerFilterSpec,
    LiveFeaturesSpec,
    LiveMode,
    NumberFormat,
    OverlayCorner,
    RendererKind,
    RendererSpec,
    SourceKind,
    SymbolSpec,
    TileSpec,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.license_policy import detect_violations
from nika_onlymap_exporter.core.live_features import (
    arcgis_template,
    live_host,
    ogcapi_template,
    resolve_live,
    wfs_template,
)
from nika_onlymap_exporter.core.manifest_builder import (
    FALLBACK_SCRIPT_TYPE,
    build_layer_element,
)
from nika_onlymap_exporter.core.settings import LayerSettings
from nika_onlymap_exporter.packaging.asset_embedder import build_data_inflater
from nika_onlymap_exporter.packaging.dependency_scanner import scan
from nika_onlymap_exporter.writers.onlymap_writer import (
    _layer_notice_block,
    loads_layers_from_the_web,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "feature_services"

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-75.7, 45.4]},
            "properties": {"name": "Station 1"},
        }
    ],
}
WFS = "https://data.example.org/wfs"


def _layer(**changes) -> ExportLayer:
    live = changes.pop(
        "live",
        LiveFeaturesSpec(
            url=wfs_template(WFS, "hydro:stations"),
            host="data.example.org",
            service="WFS",
        ),
    )
    layer = ExportLayer(
        layer_id="stations",
        name="Stations",
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.SERVICE,
        feature_count=1,
        geojson=GEOJSON,
        renderer=RendererSpec(
            kind=RendererKind.SINGLE,
            symbol=SymbolSpec(fill_color=Color(r=20, g=60, b=230), radius=6.0),
        ),
        live=live,
    )
    return dataclasses.replace(layer, **changes)


def _resolve(layer: ExportLayer, too_big: int = 10**9):
    report = FidelityReportBuilder()
    return resolve_live(layer, report, too_big), report.items


class TestRequests:
    """What the runtime reads the request as, per service."""

    def test_wfs_is_a_paged_wfs_2_request_latitude_first(self) -> None:
        url = wfs_template(WFS + "?map=x&SERVICE=WFS&typeName=old", "hydro:st")
        # The runtime's own test for a WFS, and its paging: STARTINDEX in steps
        # of COUNT, only for WFS 2.0.
        assert re.search(r"[?&]service=wfs\b", url, re.I)
        assert "VERSION=2.0.0" in url and "COUNT=1000" in url
        assert "TYPENAMES=hydro:st" in url and "typeName=old" not in url
        assert "map=x" in url
        # The box latitude first, with its CRS named, the features in degrees.
        assert "BBOX={bbox-epsg-4326},urn:ogc:def:crs:EPSG::4326" in url
        assert "SRSNAME=EPSG:4326" in url and "OUTPUTFORMAT=application/json" in url
        # No `SRS=`, which the runtime reads as a WFS 1.0 x,y request.
        assert not re.search(r"[?&]SRS=", url, re.I)

    def test_ogcapi_is_the_collections_items_request(self) -> None:
        url = ogcapi_template("https://api.example.org/ogc/", "hydro stations")
        assert re.search(r"/collections/[^/?#]+/items\b", url)
        assert url == (
            "https://api.example.org/ogc/collections/hydro%20stations/items"
            "?f=json&bbox={bbox-crs84}&limit=1000"
        )

    def test_arcgis_asks_for_the_fields_the_map_reads_as_geojson(self) -> None:
        url = arcgis_template(
            "https://services.example.org/rest/services/S/FeatureServer/0/",
            ["kind", "name"],
        )
        assert re.search(r"/(?:FeatureServer|MapServer)/\d+/query\b", url)
        assert "geometry={bbox-crs84}" in url and "inSR=4326" in url
        assert "outSR=4326" in url and "f=geojson" in url
        assert "outFields=kind,name" in url and "resultRecordCount=1000" in url
        assert "outFields" not in arcgis_template("https://s.example.org/x/MapServer/2")

    def test_host_never_carries_a_login(self) -> None:
        assert live_host("https://user:secret@data.example.org/wfs") == (
            "data.example.org"
        )

    @pytest.mark.parametrize(
        ("fixture", "longitude"),
        [("ign-wfs-commune.json", (2.0, 3.0)), ("msc-ogcapi-items.json", (-76, -75))],
    )
    def test_recorded_services_answer_longitude_first(
        self, fixture: str, longitude: tuple[float, float]
    ) -> None:
        """The real WFS and OGC API answers to these requests: GeoJSON,
        longitude first (what the runtime draws), with paging."""
        recorded = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        body = recorded["body"]
        point = body["features"][0]["geometry"]["coordinates"]
        while isinstance(point[0], list):
            point = point[0]
        assert longitude[0] <= point[0] <= longitude[1]
        assert body["numberMatched"] > body["numberReturned"]
        assert recorded["headers"]["access-control-allow-origin"] == "*"

    def test_recorded_arcgis_says_there_is_more_where_the_runtime_looks(self) -> None:
        recorded = json.loads(
            (FIXTURES / "epa-arcgis-query.json").read_text(encoding="utf-8")
        )
        assert recorded["body"]["properties"]["exceededTransferLimit"] is True
        assert recorded["headers"]["access-control-allow-origin"] == "*"


class TestResolve:
    def test_automatic_is_live_first_with_the_copy(self) -> None:
        layer, items = _resolve(_layer())
        assert layer.live is not None and layer.live.mode is LiveMode.LIVE_FIRST
        assert layer.geojson == GEOJSON
        (item,) = items
        assert item.status.value == "approximated"
        assert "data.example.org" in item.detail and "embedded" in item.detail

    def test_too_big_to_carry_is_live_only_and_carries_nothing(self) -> None:
        layer, items = _resolve(_layer(feature_count=40_000), too_big=10)
        assert layer.live is not None and layer.live.mode is LiveMode.LIVE_ONLY
        assert layer.geojson == {"type": "FeatureCollection", "features": []}
        assert "too big" in items[0].detail
        # The free plan's row cap is for rows carried; a live view keeps at
        # most the runtime's 10,000.
        project = ExportProject(title="t", layers=(layer,))
        assert detect_violations(project) == ()

    @pytest.mark.parametrize(
        ("changes", "reason"),
        [
            (
                {
                    "renderer": RendererSpec(
                        kind=RendererKind.CATEGORIZED,
                        field_name="rule",
                        field_generated=True,
                    )
                },
                "rule-based",
            ),
            (
                {
                    "renderer": RendererSpec(
                        kind=RendererKind.CATEGORIZED,
                        field_name="kind",
                        hidden_values=("lake",),
                    )
                },
                "switched off",
            ),
            (
                {"labeling": LabelingSpec(enabled=True, field_name="name")},
                "labels",
            ),
            (
                {
                    "labeling": LabelingSpec(
                        enabled=True, field_name="_label", expression="upper(name)"
                    )
                },
                "expression labels",
            ),
            (
                {
                    "labeling": LabelingSpec(
                        enabled=True, field_name="_label", number_format=NumberFormat()
                    )
                },
                "Formatted numbers",
            ),
            (
                {
                    "filter": LayerFilterSpec(
                        field_name="when",
                        title="When",
                        kind=FilterKind.DATE,
                        value_range=(0.0, 1.0),
                    )
                },
                "Filter by",
            ),
            ({"cluster_max_zoom": 9}, "count bubbles"),
        ],
    )
    def test_a_look_worked_out_at_export_stays_embedded(
        self, changes: dict, reason: str
    ) -> None:
        layer, items = _resolve(_layer(**changes))
        assert layer.live is None
        assert layer.geojson == GEOJSON
        (item,) = items
        assert item.status.value == "preserved"
        assert reason in item.detail

    def test_reasons_found_reading_the_layer_keep_it_embedded_even_if_chosen(
        self,
    ) -> None:
        live = LiveFeaturesSpec(
            url="u",
            host="h",
            service="WFS",
            choice=LiveMode.LIVE_ONLY,
            embedded_only=("the filter QGIS applies to it",),
        )
        layer, items = _resolve(_layer(live=live), too_big=10)
        assert layer.live is None and layer.geojson == GEOJSON
        (item,) = items
        # Not what was asked for, so said as a loss.
        assert item.status.value == "approximated"
        assert "the filter QGIS applies to it" in item.detail

    @pytest.mark.parametrize("mode", list(LiveMode))
    def test_the_choice_is_honoured(self, mode: LiveMode) -> None:
        live = LiveFeaturesSpec(url="u", host="h", service="WFS", choice=mode)
        layer, _items = _resolve(_layer(live=live), too_big=10)
        if mode is LiveMode.EMBEDDED:
            assert layer.live is None and layer.geojson == GEOJSON
        else:
            assert layer.live is not None and layer.live.mode is mode
            assert (layer.geojson == GEOJSON) is (mode is LiveMode.LIVE_FIRST)


class TestMarkup:
    def _element(self, mode: LiveMode, compress: bool = False) -> str:
        layer, _items = _resolve(
            _layer(
                live=LiveFeaturesSpec(
                    url=wfs_template(WFS, "hydro:stations"),
                    host="data.example.org",
                    service="WFS",
                    choice=mode,
                )
            )
        )
        return build_layer_element(layer, compress_data=compress)

    def test_live_first_loads_data_and_carries_the_copy_unread(self) -> None:
        element = self._element(LiveMode.LIVE_FIRST)
        assert 'data="https://data.example.org/wfs?SERVICE=WFS&amp;' in element
        assert "{bbox-epsg-4326}" in element
        assert 'data-q2w-live="live-first"' in element
        # Not `application/json`, which the runtime would read instead of data.
        assert f'<script type="{FALLBACK_SCRIPT_TYPE}">' in element
        assert 'type="application/json"' not in element
        assert '"Station 1"' in element

    def test_live_only_carries_no_features(self) -> None:
        element = self._element(LiveMode.LIVE_ONLY)
        assert 'data-q2w-live="live-only"' in element
        assert "<script" not in element and "Station 1" not in element

    def test_a_compressed_copy_inflates_to_the_fallback_type(self) -> None:
        element = self._element(LiveMode.LIVE_FIRST, compress=True)
        assert "data-q2w-fallback>" in element
        inflater = build_data_inflater("onlymap.js")
        assert 'block.hasAttribute("data-q2w-fallback")' in inflater
        assert FALLBACK_SCRIPT_TYPE in inflater

    def test_the_notice_is_on_a_map_with_a_layer_from_the_web(self) -> None:
        tiles = dataclasses.replace(
            _layer(live=None),
            geometry_kind=GeometryKind.RASTER,
            geojson=None,
            tiles=TileSpec("https://tiles.example.org/{z}/{x}/{y}.png"),
        )
        for layer, expected in (
            (_layer(live=None), False),
            (_layer(), True),
            (tiles, True),
        ):
            project = ExportProject(title="t", layers=(layer,))
            assert loads_layers_from_the_web(project) is expected
            assert bool(_layer_notice_block(project)) is expected

    @pytest.mark.parametrize("corner", list(OverlayCorner))
    def test_the_notice_joins_the_bottom_left_stack_at_any_size(
        self, corner: OverlayCorner
    ) -> None:
        project = ExportProject(
            title="Rivers",
            layers=(_layer(),),
            extent=Extent(west=-76, south=45, east=-75, north=46),
            settings=ExportSettings(title_corner=corner, chrome_scale=1.5),
        )
        block = _layer_notice_block(project)
        # Last in the corner's order: right above the licence badge.
        assert '"position": "bottom-left", "order": 5, "scale": 1.5' in block
        assert "@" not in block.replace("@media", "")


def test_a_live_layer_is_a_network_dependency() -> None:
    layer, _items = _resolve(_layer())
    result = scan(ExportProject(title="t", layers=(layer,)), FidelityReportBuilder())
    assert "data.example.org" in result.remote_dependencies
    embedded, _items = _resolve(_layer(live=None))
    result = scan(ExportProject(title="t", layers=(embedded,)), FidelityReportBuilder())
    assert result.remote_dependencies == ()


@pytest.mark.parametrize("value", [None, "live-first", "embedded", "live-only"])
def test_the_choice_round_trips_with_the_project(value: str | None) -> None:
    settings = LayerSettings(live_features=value)
    data = settings.to_dict()
    assert ("liveFeatures" in data) is (value is not None)
    restored = LayerSettings.from_dict(json.loads(json.dumps(data)))
    assert restored.live_features == value
    assert restored.resolved_live() == (None if value is None else LiveMode(value))


def test_an_unknown_saved_choice_is_automatic() -> None:
    assert LayerSettings.from_dict({"liveFeatures": "sometimes"}).live_features is None
