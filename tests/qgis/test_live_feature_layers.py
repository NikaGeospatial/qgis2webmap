"""WFS, OGC API Features and ArcGIS FeatureServer layers, exported live.

Real QGIS layers on the local fake services (`tests/fake_feature_services.py`),
read through the export as the dialog and Processing read them: what pins here
is the QGIS side - the source QGIS keeps, the per-layer Features choice saved
with the project, and which layers stay embedded because their look is worked
out from their features. The requests themselves are unit-tested in
`tests/unit/test_live_features.py` and loaded by the runtime in
`tests/browser/test_live_features.py`.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus, LiveMode
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.core.settings import DialogState, load_state, save_state

qgis_core = pytest.importorskip("qgis.core")

ARCGIS = "arcgis/rest/services/Stations/FeatureServer/0"


@pytest.fixture(scope="module")
def services():
    from fake_feature_services import FakeFeatureServices

    with FakeFeatureServices() as running:
        yield running


def _wfs(services, extra: str = ""):
    source = (
        f"pagingEnabled='true' srsname='EPSG:4326' typename='hydro:stations' "
        f"url='{services.url('cors/wfs')}' version='2.0.0'{extra}"
    )
    return qgis_core.QgsVectorLayer(source, "WFS stations", "WFS")


def _ogc(services):
    source = f"typename='stations' url='{services.url('cors/ogc')}'"
    return qgis_core.QgsVectorLayer(source, "OGC stations", "OAPIF")


def _arcgis(services):
    source = f"crs='EPSG:4326' url='{services.url(f'cors/{ARCGIS}')}'"
    return qgis_core.QgsVectorLayer(source, "ArcGIS stations", "arcgisfeatureserver")


MAKERS = {"wfs": _wfs, "ogc": _ogc, "arcgis": _arcgis}


def _export(project, layer, choice: str | None = None, layer_order=None):
    assert layer.isValid(), layer.dataProvider().error().message()
    project.addMapLayer(layer)
    state = DialogState()
    state.for_layer(layer.id()).live_features = choice
    report = FidelityReportBuilder()
    exported = read_project(
        project, report, layer_settings=state.layers, layer_order=layer_order
    )
    (read,) = exported.layers
    rows = [item for item in report.items if item.subject.startswith("Live features")]
    return read, rows


class TestLiveFirst:
    @pytest.mark.parametrize(
        ("service", "request_part"),
        [
            ("wfs", "/cors/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"),
            ("ogc", "/cors/ogc/collections/stations/items?f=json&bbox={bbox-crs84}"),
            ("arcgis", f"/cors/{ARCGIS}/query?where=1%3D1&geometry={{bbox-crs84}}"),
        ],
    )
    def test_a_service_layer_loads_live_with_its_copy(
        self, project, services, service: str, request_part: str
    ) -> None:
        read, rows = _export(project, MAKERS[service](services))

        assert read.live is not None
        assert read.live.mode is LiveMode.LIVE_FIRST
        assert request_part in read.live.url
        assert read.live.host == services.base_url.split("//")[1]
        # The copy: every feature QGIS read, with the fields the popup shows.
        assert read.feature_count == 12
        names = {f["properties"]["name"] for f in read.geojson["features"]}
        assert "Station 7" in names
        (row,) = rows
        assert row.status is FidelityStatus.APPROXIMATED
        assert read.live.host in row.detail

    def test_the_wfs_request_names_the_type_qgis_reads(self, project, services) -> None:
        read, _rows = _export(project, _wfs(services))
        assert "TYPENAMES=hydro:stations" in read.live.url
        assert "BBOX={bbox-epsg-4326},urn:ogc:def:crs:EPSG::4326" in read.live.url

    def test_the_arcgis_request_asks_for_the_popup_fields(
        self, project, services
    ) -> None:
        read, _rows = _export(project, _arcgis(services))
        fields = read.live.url.split("outFields=")[1].split("&")[0].split(",")
        assert {"name", "kind", "depth"} <= set(fields)


class TestTheChoice:
    @pytest.mark.parametrize("service", sorted(MAKERS))
    def test_embedded_keeps_the_features_in_the_file_alone(
        self, project, services, service: str
    ) -> None:
        read, rows = _export(project, MAKERS[service](services), "embedded")
        assert read.live is None and read.feature_count == 12
        assert rows == []

    def test_live_only_carries_no_features(self, project, services) -> None:
        read, rows = _export(project, _ogc(services), "live-only")
        assert read.live is not None and read.live.mode is LiveMode.LIVE_ONLY
        assert read.geojson["features"] == []
        assert "loads live only" in rows[0].detail

    def test_a_cartograph_frame_stays_embedded(self, project, services) -> None:
        layer = _ogc(services)
        read, rows = _export(project, layer, "live-only", layer_order=[layer.id()])
        assert rows == []
        assert read.live is None and read.feature_count == 12

    def test_it_round_trips_with_the_project(self, project) -> None:
        state = DialogState()
        state.for_layer("abc").live_features = "live-only"
        save_state(project, state)
        assert load_state(project).layers["abc"].live_features == "live-only"


class TestEmbeddedOnly:
    """A look worked out from the features at export keeps the layer
    embedded, and the Fidelity tab says why."""

    def _reason(self, project, layer, choice=None) -> str:
        read, rows = _export(project, layer, choice)
        assert read.live is None
        assert read.feature_count > 0
        (row,) = rows
        return row.detail

    def test_a_filter_qgis_applies(self, project, services) -> None:
        layer = _ogc(services)
        layer.setSubsetString("\"kind\" = 'lake'")
        assert "the filter QGIS applies to it" in self._reason(project, layer)

    def test_a_value_map_in_the_popup(self, project, services) -> None:
        layer = _arcgis(services)
        index = layer.fields().indexOf("kind")
        layer.setEditorWidgetSetup(
            index,
            qgis_core.QgsEditorWidgetSetup(
                "ValueMap", {"map": [{"Lake": "lake"}, {"River": "river"}]}
            ),
        )
        assert "Value Map" in self._reason(project, layer)

    def test_a_virtual_field(self, project, services) -> None:
        layer = _wfs(services)
        layer.addExpressionField(
            "depth * 2", qgis_core.QgsField("twice", qgis_core.QMetaType.Type.Double)
        )
        assert "virtual or joined" in self._reason(project, layer)

    def test_labels_even_when_live_was_chosen(self, project, services) -> None:
        layer = _ogc(services)
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        settings.enabled = True
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        read, rows = _export(project, layer, "live-first")
        assert read.live is None
        (row,) = rows
        assert "labels" in row.detail
        # Asked for live and not given it: said as a loss.
        assert row.status is FidelityStatus.APPROXIMATED

    def test_a_rule_based_style(self, project, services) -> None:
        layer = _wfs(services)
        root = qgis_core.QgsRuleBasedRenderer.Rule(None)
        for expression in ("\"kind\" = 'lake'", "\"kind\" = 'river'"):
            symbol = qgis_core.QgsSymbol.defaultSymbol(layer.geometryType())
            root.appendChild(
                qgis_core.QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression)
            )
        layer.setRenderer(qgis_core.QgsRuleBasedRenderer(root))
        assert "rule-based" in self._reason(project, layer)

    def test_a_wfs_pinned_to_version_1(self, project, services) -> None:
        layer = _wfs(services)
        uri = qgis_core.QgsDataSourceUri(layer.source())
        uri.removeParam("version")
        uri.setParam("version", "1.1.0")
        layer.setDataSource(uri.uri(False), layer.name(), "WFS")
        assert "WFS version" in self._reason(project, layer)


class TestProcessing:
    def test_a_run_honours_the_saved_choice(self, project, services, tmp_path) -> None:
        from nika_onlymap_exporter.packaging.runtime_manager import (
            discover_runtime_dir,
        )
        from nika_onlymap_exporter.processing.export_project import (
            ExportProjectAlgorithm,
        )

        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        live_only, automatic = _ogc(services), _arcgis(services)
        project.addMapLayer(live_only)
        project.addMapLayer(automatic)
        state = load_state(project)
        state.for_layer(live_only.id()).live_features = "live-only"
        save_state(project, state)
        context = qgis_core.QgsProcessingContext()
        context.setProject(project)
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        results = algorithm.processAlgorithm(
            {"OUTPUT": str(tmp_path / "map.html")},
            context,
            qgis_core.QgsProcessingFeedback(),
        )
        html = Path(results["OUTPUT"]).read_text(encoding="utf-8")

        assert html.count('data-q2w-live="live-only"') == 1
        assert html.count('data-q2w-live="live-first"') == 1
        assert "/cors/ogc/collections/stations/items?f=json&amp;bbox={bbox-crs84}" in (
            html
        )
        assert 'type="application/x-q2w-fallback+json"' in html
        # The page answers a failing layer.
        assert 'addEventListener("om-layer-error"' in html


class TestTheLayersTab:
    def test_only_a_service_layer_offers_the_choice(
        self, qgis_app, project, services, make_memory_layer
    ) -> None:
        from qgis.PyQt.QtWidgets import QComboBox
        from test_dialog import _options_row

        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        service = _ogc(services)
        project.addMapLayer(service)
        project.addMapLayer(make_memory_layer("Local", features=[("a", [0.8, 51.1])]))
        dialog = MainDialog(FakeIface(), None)
        try:
            offered = {}
            tree = dialog.layer_tree
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                row = _options_row(dialog, item)
                offered[item.text(0)] = [
                    c
                    for c in row.findChildren(QComboBox)
                    if c.itemText(0) == "Features: automatic"
                ]
            assert len(offered["OGC stations"]) == 1
            assert offered["Local"] == []
            combo = offered["OGC stations"][0]
            assert [combo.itemText(i) for i in range(combo.count())] == [
                "Features: automatic",
                "Features: live first",
                "Features: embedded",
                "Features: live only",
            ]
            combo.setCurrentIndex(3)
            assert dialog.state.for_layer(service.id()).live_features == "live-only"
        finally:
            dialog.close()
