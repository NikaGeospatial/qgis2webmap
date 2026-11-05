"""The Layers tab's "Filter by", from the QGIS layer to the exported markup.

A real memory layer with a number, a date, a date and time, text and a yes/no
field, read by the real project reader. What is checked is what the web map
gets: the filter panel, the filter its layer declares, the data it filters on,
and the Fidelity rows - and what the dialog offers.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re

import pytest

from nika_onlymap_exporter.core.export_ir import FilterKind
from nika_onlymap_exporter.core.settings import (
    DialogState,
    LayerSettings,
    load_state,
    save_state,
)

qgis_core = pytest.importorskip("qgis.core")

ROWS = (
    # name, population, founded, surveyed, kind, open
    ("Ashford", 74000, (1600, 1, 1), (2024, 3, 5, 10, 30), "town", True),
    ("Barnet", 400, (1800, 6, 2), (2024, 3, 6, 8, 0), "village", False),
    ("Crawley", None, None, None, None, None),
)


def _layer(rows=ROWS, name="Places", alias=True):
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime

    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=population:integer"
        "&field=founded:date&field=surveyed:datetime&field=kind:string"
        "&field=open:boolean",
        name,
        "memory",
    )
    if alias:
        layer.setFieldAlias(layer.fields().indexOf("population"), "People")
    features = []
    for index, (place, people, founded, surveyed, kind, is_open) in enumerate(rows):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(
            [
                place,
                people,
                QDate(*founded) if founded else None,
                QDateTime(QDate(*surveyed[:3]), QTime(*surveyed[3:]))
                if surveyed
                else None,
                kind,
                is_open,
            ]
        )
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(index * 0.1, 51.0)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def _ms(*when: int) -> float:
    """A UTC date (and time) in milliseconds since 1970."""
    from datetime import datetime, timezone

    return datetime(*when, tzinfo=timezone.utc).timestamp() * 1000


def _read(project, layer, field, **settings):
    from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
    from nika_onlymap_exporter.core.project_reader import read_project

    project.addMapLayer(layer)
    report = FidelityReportBuilder()
    state = DialogState(**settings)
    state.for_layer(layer.id()).filter_field = field
    exported = read_project(
        project,
        report,
        settings=state.to_export_settings(),
        layer_settings=state.layers,
    )
    (read,) = exported.layers
    return exported, read, report


def _rows(report, layer_id):
    return [
        (item.status.value, item.detail)
        for item in report.items
        if item.subject.startswith("Filter on") and item.layer_id == layer_id
    ]


class TestTheFilterIsReadFromTheData:
    def test_a_number_is_a_slider_over_every_value(self, project) -> None:
        _exported, layer, report = _read(project, _layer(), "population")
        assert layer.filter is not None
        assert layer.filter.kind is FilterKind.NUMBER
        assert layer.filter.title == "People"
        assert layer.filter.value_range == (400.0, 74000.0)
        statuses = [status for status, _ in _rows(report, layer.layer_id)]
        assert statuses == ["preserved", "approximated"]
        assert "1 feature has no 'People'" in _rows(report, layer.layer_id)[1][1]

    def test_text_is_a_tick_box_per_value(self, project) -> None:
        _exported, layer, _report = _read(project, _layer(), "kind")
        assert layer.filter is not None
        assert layer.filter.kind is FilterKind.TEXT
        assert layer.filter.categories == ("town", "village")

    def test_a_date_filters_on_its_milliseconds(self, project) -> None:
        _exported, layer, _report = _read(project, _layer(), "founded")
        spec = layer.filter
        assert spec is not None and spec.kind is FilterKind.DATE
        assert spec.field_name == "founded_ms"
        assert spec.value_range == (_ms(1600, 1, 1), _ms(1800, 6, 2))
        properties = [f["properties"] for f in layer.geojson["features"]]
        # The field itself is untouched, for the popup.
        assert properties[0]["founded"] == "1600-01-01"
        assert properties[0]["founded_ms"] == _ms(1600, 1, 1)
        assert properties[2]["founded_ms"] is None

    def test_a_date_and_time_keeps_its_time(self, project) -> None:
        _exported, layer, _report = _read(project, _layer(), "surveyed")
        spec = layer.filter
        assert spec is not None and spec.kind is FilterKind.DATETIME
        # Read as UTC, as the panel labels them.
        assert spec.value_range == (_ms(2024, 3, 5, 10, 30), _ms(2024, 3, 6, 8, 0))

    def test_a_hidden_field_still_travels(self, project) -> None:
        from nika_onlymap_exporter.core.export_ir import PopupFieldMode

        layer = _layer()
        project.addMapLayer(layer)
        state = DialogState()
        state.for_layer(layer.id()).filter_field = "kind"
        state.for_layer(layer.id()).fields["kind"] = PopupFieldMode.HIDDEN.value
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project

        report = FidelityReportBuilder()
        exported = read_project(
            project,
            report,
            settings=state.to_export_settings(),
            layer_settings=state.layers,
        )
        (read,) = exported.layers
        assert read.filter is not None
        assert read.geojson["features"][0]["properties"]["kind"] == "town"
        assert "popup row is hidden" in _rows(report, read.layer_id)[0][1]

    def test_too_many_values_is_refused_and_said(self, project) -> None:
        rows = tuple((f"P{i}", i, None, None, f"kind {i}", None) for i in range(51))
        _exported, layer, report = _read(project, _layer(rows), "kind")
        assert layer.filter is None
        ((status, detail),) = _rows(report, layer.layer_id)
        assert status == "unsupported"
        assert "51 different values" in detail

    def test_fifty_values_is_still_a_filter(self, project) -> None:
        rows = tuple((f"P{i}", i, None, None, f"kind {i}", None) for i in range(50))
        _exported, layer, _report = _read(project, _layer(rows), "kind")
        assert layer.filter is not None
        assert len(layer.filter.categories) == 50

    def test_a_field_that_is_gone_is_said(self, project) -> None:
        _exported, layer, report = _read(project, _layer(), "vanished")
        assert layer.filter is None
        ((status, detail),) = _rows(report, layer.layer_id)
        assert status == "unsupported" and "no longer has" in detail

    def test_labels_are_said_not_to_follow(self, project) -> None:
        layer = _layer()
        from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling

        labels = QgsPalLayerSettings()
        labels.fieldName = "name"
        layer.setLabeling(QgsVectorLayerSimpleLabeling(labels))
        layer.setLabelsEnabled(True)
        _exported, read, report = _read(project, layer, "population")
        assert any("labels" in detail for _s, detail in _rows(report, read.layer_id))


class TestAFilteredLayerDoesNotCluster:
    def test_dense_points_stay_points_and_it_is_said(self, project) -> None:
        rows = tuple((f"P{i}", i, None, None, "x", None) for i in range(400))
        layer = _layer(rows)
        # All within a few metres: they would cluster.
        from qgis.core import QgsGeometry, QgsPointXY

        layer.startEditing()
        for index, feature in enumerate(layer.getFeatures()):
            layer.changeGeometry(
                feature.id(),
                QgsGeometry.fromPointXY(QgsPointXY(index * 1e-5, 51.0)),
            )
        layer.commitChanges()
        _exported, read, report = _read(project, layer, "population")
        assert read.cluster_max_zoom is None
        markers = [i.detail for i in report.items if i.subject.startswith("Markers")]
        assert len(markers) == 1 and "not merged into count bubbles" in markers[0]


class TestTheMarkup:
    def test_the_panel_and_the_layer_filter(self, project) -> None:
        from nika_onlymap_exporter.core.manifest_builder import build_manifest

        exported, layer, _report = _read(project, _layer(), "surveyed")
        markup = build_manifest(exported)
        widget = re.search(r'<om-widget type="filter"[^>]*>', markup)
        assert widget is not None
        assert f'layer="{layer.layer_id}"' in widget.group(0)
        assert 'field="surveyed_ms"' in widget.group(0)
        assert 'title="surveyed"' in widget.group(0)
        assert 'format="date" date-style="datetime" time-zone="UTC"' in widget.group(0)
        assert 'position="top-left"' in widget.group(0)
        assert 'filter-field="surveyed_ms"' in markup
        low, high = int(_ms(2024, 3, 5, 10, 30)), int(_ms(2024, 3, 6, 8, 0))
        assert f'filter-range="[{low}, {high}]"' in markup

    def test_text_declares_its_keep_list(self, project) -> None:
        from nika_onlymap_exporter.core.manifest_builder import build_manifest

        exported, _layer_read, _report = _read(project, _layer(), "kind")
        markup = build_manifest(exported)
        assert 'filter-category="kind"' in markup
        assert 'filter-categories=\'["town", "village"]\'' in markup or (
            'filter-categories="[&quot;town&quot;, &quot;village&quot;]"' in markup
        )

    def test_no_filter_no_panel(self, project) -> None:
        from nika_onlymap_exporter.core.manifest_builder import build_manifest

        exported, layer, _report = _read(project, _layer(), None)
        assert layer.filter is None
        markup = build_manifest(exported)
        assert 'type="filter"' not in markup and "filter-" not in markup

    def test_a_cartograph_frame_has_no_filter(self, project) -> None:
        """A frame is a map on a printed page, with no controls."""
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project

        layer = _layer()
        project.addMapLayer(layer)
        state = DialogState()
        state.for_layer(layer.id()).filter_field = "population"
        exported = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings=state.layers,
            layer_order=[layer.id()],
        )
        assert exported.layers[0].filter is None


class TestTheSetting:
    def test_it_round_trips_with_the_project(self, project) -> None:
        state = DialogState()
        state.layers["abc"] = LayerSettings(filter_field="population")
        save_state(project, state)
        assert load_state(project).layers["abc"].filter_field == "population"
        assert "filterField" not in LayerSettings().to_dict()


class TestTheLayersTab:
    def _combo(self, dialog, layer):
        from qgis.PyQt.QtWidgets import QComboBox
        from test_dialog import _options_row

        row = _options_row(dialog, dialog.layer_tree.topLevelItem(0))
        combos = [
            c for c in row.findChildren(QComboBox) if c.itemText(0) == "Filter by: none"
        ]
        assert len(combos) == 1
        return combos[0]

    def test_it_offers_numbers_dates_and_text(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        layer = _layer()
        project.addMapLayer(layer)
        dialog = MainDialog(FakeIface(), None)
        try:
            combo = self._combo(dialog, layer)
            assert [combo.itemText(i) for i in range(combo.count())] == [
                "Filter by: none",
                "Filter by: name",
                "Filter by: People",
                "Filter by: founded",
                "Filter by: surveyed",
                "Filter by: kind",
            ]
            assert "not merged into count bubbles" in combo.toolTip()
            combo.setCurrentIndex(combo.findData("population"))
            assert dialog.state.for_layer(layer.id()).filter_field == "population"
            combo.setCurrentIndex(0)
            assert dialog.state.for_layer(layer.id()).filter_field is None
        finally:
            dialog.close()

    def test_it_opens_on_the_saved_field(self, qgis_app, project) -> None:
        from nika_onlymap_exporter.ui.main_dialog import MainDialog

        class FakeIface:
            def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
                return None

        layer = _layer()
        project.addMapLayer(layer)
        state = DialogState()
        state.for_layer(layer.id()).filter_field = "kind"
        save_state(project, state)
        dialog = MainDialog(FakeIface(), None)
        try:
            assert self._combo(dialog, layer).currentData() == "kind"
        finally:
            dialog.close()


def test_the_widget_attribute_names_are_the_runtimes() -> None:
    """Every attribute written is one the pinned runtime documents."""
    import os
    from pathlib import Path

    runtime = os.environ.get("ONLYMAP_RUNTIME_DIR")
    if not runtime:
        pytest.skip("set ONLYMAP_RUNTIME_DIR")
    # A fetched runtime keeps the contract beside the scripts; a package's
    # plain `dist/` has it one level up, at the package root.
    contract = next(
        p
        for p in (
            Path(runtime) / "onlymapjs.html-data.json",
            Path(runtime).parent / "onlymapjs.html-data.json",
        )
        if p.is_file()
    )
    data = json.loads(contract.read_text())
    tags = {tag["name"]: {a["name"] for a in tag["attributes"]} for tag in data["tags"]}
    assert {"layer", "field", "title", "format", "date-style", "time-zone"} <= tags[
        "om-widget"
    ]
    assert {"filter-field", "filter-range", "filter-category", "filter-categories"} <= (
        tags["om-layer"]
    )
