"""Labels from a QGIS expression, worked out per feature at export.

The web map cannot evaluate a QGIS expression, so the exporter works it out
for every feature as it reads them, writes the text into a property of its
own and labels from that. The expectation for the text is QGIS itself: what
its labelling engine draws for the same layer and expression on a render.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.label_points import LABEL_PROPERTY
from nika_onlymap_exporter.core.manifest_builder import build_label_element
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.core.settings import LayerSettings

qgis_core = pytest.importorskip("qgis.core")
QtCore = pytest.importorskip("qgis.PyQt.QtCore")

ROWS = [
    ("Alpha", 1234.5678, 3, (2.0, 5.0)),
    ("Beta", None, 0, (5.0, 5.0)),
    (None, 12.0, 7, (8.0, 5.0)),
]


def _layer(expression: str, rows=ROWS, fields: str = "", **settings_overrides):
    """A point layer of `rows` labelled by `expression`."""
    layer = qgis_core.QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string&field=elev:double&field=n:integer"
        + fields,
        "peaks",
        "memory",
    )
    features = []
    for name, elev, n, (x, y) in rows:
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setGeometry(qgis_core.QgsGeometry.fromWkt(f"Point({x} {y})"))
        feature["name"] = name
        feature["elev"] = elev
        feature["n"] = n
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    settings.placement = qgis_core.Qgis.LabelPlacement.OverPoint
    for key, value in settings_overrides.items():
        setattr(settings, key, value)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _qgis_labels(layer) -> list[tuple[float, str]]:
    """What QGIS's labelling engine draws for `layer`: (x, text), by x."""
    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"))
    settings.setExtent(qgis_core.QgsRectangle(0, 0, 10, 10))
    settings.setOutputSize(QtCore.QSize(1000, 1000))
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    return sorted(
        (round(label.labelRect.center().x()), label.labelText)
        for label in job.takeLabelingResults().allLabels()
        if not label.isUnplaced
    )


def _export(project, layer, layer_settings=None):
    """Read the project; the exported layer, its labels as (x, text), the
    TextLayer markup and the report."""
    project.addMapLayer(layer)
    report = FidelityReportBuilder()
    exported = read_project(
        project,
        report,
        layer_settings=(
            {layer.id(): layer_settings} if layer_settings is not None else None
        ),
    )
    (read,) = exported.layers
    markup = build_label_element(read)
    labels = []
    if markup:
        payload = json.loads(
            markup.split('<script type="application/json">')[1].split("</script>")[0]
        )
        labels = sorted(
            (
                round(feature["geometry"]["coordinates"][0]),
                feature["properties"][LABEL_PROPERTY],
            )
            for feature in payload["features"]
        )
    return read, labels, markup, report.items


@pytest.mark.parametrize(
    "expression",
    [
        "\"name\" || ' (' || \"elev\" || ' m)'",
        "CASE WHEN \"n\" > 2 THEN 'many' ELSE 'few' END",
        'format_number("elev", 1)',
        '"n" / 2',
        '"n" > 2',
        "@layer_name || ': ' || \"name\"",
        "attribute(@feature, 'name')",
        "'x' -- a trailing comment",
    ],
    ids=[
        "concatenation",
        "case",
        "format_number",
        "arithmetic",
        "comparison",
        "layer-variable",
        "attribute-of-feature",
        "trailing-comment",
    ],
)
def test_the_labels_read_as_qgis_draws_them(project, expression) -> None:
    layer = _layer(expression)
    qgis = _qgis_labels(layer)
    _read, exported, _markup, _items = _export(project, layer)
    assert qgis, "the oracle drew no labels; the case tests nothing"
    assert exported == qgis


def test_a_null_result_is_no_label(project) -> None:
    """QGIS draws nothing for NULL, so the map gets no text layer at all."""
    layer = _layer("CASE WHEN \"n\" > 100 THEN 'big' END")
    assert _qgis_labels(layer) == []
    _read, exported, markup, _items = _export(project, layer)
    assert exported == []
    assert markup == ""


def test_the_labels_are_reported_as_worked_out_at_export(project) -> None:
    _read, _labels, _markup, items = _export(project, _layer('upper("name")'))
    details = [item.detail for item in items if item.subject == "Labels on 'peaks'"]
    assert any(
        "worked out for each feature when the map is exported" in d for d in details
    )
    assert not any(item.status is FidelityStatus.UNSUPPORTED for item in items), (
        "an expression label is no longer unsupported"
    )


def test_a_map_scale_variable_is_reported_and_evaluates_without_it(project) -> None:
    """There is no map at export, so @map_scale has no value: the labels are
    what QGIS draws with no map scale, and the report says why they differ
    from what a QGIS canvas shows."""
    layer = _layer("concat(\"name\", ' 1:', @map_scale)")
    _read, exported, _markup, items = _export(project, layer)
    # QGIS draws " 1:" for the unnamed one; the label pipeline trims the
    # space, for an expression's text as for a field's.
    assert [text for _x, text in exported] == ["Alpha 1:", "Beta 1:", "1:"]
    reported = [
        item
        for item in items
        if item.subject == "Labels on 'peaks'" and "@map_scale" in item.detail
    ]
    assert len(reported) == 1
    assert reported[0].status is FidelityStatus.APPROXIMATED


def test_the_clock_is_reported(project) -> None:
    _read, _labels, _markup, items = _export(
        project, _layer("format_date(now(), 'yyyy')")
    )
    assert any("now()" in item.detail for item in items)


def test_text_case_and_wrapping_apply_to_the_worked_out_text(project) -> None:
    layer = _layer(
        '"name" || \'|\' || "n"',
        wrapChar="|",
        rows=[("Zürich", 1.0, 3, (2.0, 5.0))],
    )
    text_format = layer.labeling().settings().format()
    text_format.setCapitalization(qgis_core.Qgis.Capitalization.AllUppercase)
    settings = layer.labeling().settings()
    settings.setFormat(text_format)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))

    _read, exported, markup, _items = _export(project, layer)
    assert exported == [(2, "ZÜRICH\n3")]
    # The atlas is collected from the worked-out text, after the case change.
    assert 'character-set="' in markup
    assert "Ü" in markup.split('character-set="')[1].split('"')[0]


def test_a_failing_feature_has_no_label_and_is_reported(project) -> None:
    layer = _layer(
        'to_int("name")',
        rows=[("12", 1.0, 1, (2.0, 5.0)), ("twelve", 1.0, 1, (5.0, 5.0))],
    )
    assert [text for _x, text in _qgis_labels(layer)] == ["12"]
    _read, exported, _markup, items = _export(project, layer)
    assert [text for _x, text in exported] == ["12"]
    failed = [item for item in items if "could not be worked out" in item.detail]
    assert len(failed) == 1
    assert "1 of 2 feature(s)" in failed[0].detail


def test_an_unreadable_expression_is_reported_and_unlabelled(project) -> None:
    _read, _labels, markup, items = _export(project, _layer('"name" ||'))
    assert markup == ""
    assert any(
        item.status is FidelityStatus.UNSUPPORTED and "cannot be read" in item.detail
        for item in items
    )


class TestTheGeneratedProperty:
    def test_it_never_takes_a_real_fields_name(self, project) -> None:
        layer = _layer('upper("name")', fields="&field=label_text:string")
        read, exported, _markup, _items = _export(project, layer)
        assert read.labeling.field_name == "label_text_"
        properties = read.geojson["features"][0]["properties"]
        assert properties["label_text_"] == "ALPHA"
        assert "label_text" in properties  # the real field, untouched
        assert [text for _x, text in exported] == ["ALPHA", "BETA"]

    def test_it_is_not_in_the_popup(self, project) -> None:
        read, _labels, _markup, _items = _export(project, _layer('upper("name")'))
        assert read.labeling.field_name == "label_text"
        assert "label_text" not in {field.name for field in read.popup.fields}

    def test_a_field_only_the_expression_reads_stays_out_of_the_file(
        self, project
    ) -> None:
        """Only the text is written: a field hidden from the popup that the
        expression reads is read, not exported."""
        layer = _layer('"name" || \' \' || "elev"')
        read, exported, _markup, _items = _export(
            project, layer, LayerSettings(fields={"elev": "hidden"})
        )
        assert [text for _x, text in exported] == ["Alpha 1234.5678"]
        properties = read.geojson["features"][0]["properties"]
        assert "elev" not in properties
        assert properties["label_text"] == "Alpha 1234.5678"

    def test_names_beside_points_replace_the_expression(self, project) -> None:
        layer = _layer('upper("name")')
        read, exported, _markup, _items = _export(
            project, layer, LayerSettings(name_field="name")
        )
        assert read.labeling.expression is None
        assert [text for _x, text in exported] == ["Alpha", "Beta"]
        assert "label_text" not in read.geojson["features"][0]["properties"]


class TestALayerAnotherLayerReads:
    """A label reading another layer (`aggregate`, `get_feature`) is worked
    out on the worker, while the user may remove or edit that layer on the
    GUI thread. QGIS fetches a layer an expression names on the thread that
    owns it; the export must neither crash nor carry a value QGIS never had."""

    COUNT = 3000

    def _layers(self, project):
        lookup = qgis_core.QgsVectorLayer(
            "Point?crs=EPSG:4326&field=key:integer&field=code:string",
            "lookup",
            "memory",
        )
        rows = []
        for key in range(3):
            feature = qgis_core.QgsFeature(lookup.fields())
            feature.setAttributes([key, f"code{key}"])
            feature.setGeometry(qgis_core.QgsGeometry.fromWkt("Point(0 0)"))
            rows.append(feature)
        lookup.dataProvider().addFeatures(rows)
        points = qgis_core.QgsVectorLayer(
            "Point?crs=EPSG:4326&field=n:integer", "points", "memory"
        )
        features = []
        for number in range(self.COUNT):
            feature = qgis_core.QgsFeature(points.fields())
            feature.setAttributes([number])
            feature.setGeometry(
                qgis_core.QgsGeometry.fromWkt(f"Point({number % 100} {number // 100})")
            )
            features.append(feature)
        points.dataProvider().addFeatures(features)
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = (
            "attribute(get_feature('lookup', 'key', \"n\" % 3), 'code')"
            " || '/' || aggregate('lookup', 'count', \"key\")"
        )
        settings.isExpression = True
        points.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        points.setLabelsEnabled(True)
        project.addMapLayers([lookup, points])
        return lookup, points

    def _finish_on_a_worker(self, qgis_app, pending, during):
        """`finish_project` on a thread while the GUI thread runs `during`
        and keeps its event loop turning, as the dialog's does."""
        import threading
        import time

        from nika_onlymap_exporter.core.project_reader import finish_project

        done: list = []
        errors: list[BaseException] = []
        report = FidelityReportBuilder()

        def work() -> None:
            try:
                done.append(finish_project(pending, report))
            except BaseException as exc:  # reported on the test thread
                errors.append(exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        qgis_app.processEvents()
        during()
        deadline = time.monotonic() + 120
        while thread.is_alive() and time.monotonic() < deadline:
            qgis_app.processEvents()
            time.sleep(0.005)
        assert not thread.is_alive(), "the worker never finished"
        assert not errors, errors
        (export,) = done
        (layer,) = [read for read in export.layers if read.name == "points"]
        field = layer.labeling.field_name
        return [f["properties"].get(field) for f in layer.geojson["features"]], report

    @pytest.mark.parametrize("change", ["none", "removed", "edited"])
    def test_a_change_meanwhile_neither_crashes_nor_invents(
        self, qgis_app, project, change
    ) -> None:
        from nika_onlymap_exporter.core.project_reader import prepare_project

        lookup, points = self._layers(project)
        pending = prepare_project(
            project,
            FidelityReportBuilder(),
            selected_layer_ids=frozenset({points.id()}),
        )

        def during() -> None:
            if change == "none":
                return
            if change == "removed":
                project.removeMapLayer(lookup.id())
                return
            lookup.startEditing()
            for feature in lookup.getFeatures():
                lookup.changeAttributeValue(feature.id(), 1, feature["code"].upper())
            lookup.commitChanges()

        texts, _report = self._finish_on_a_worker(qgis_app, pending, during)
        assert len(texts) == self.COUNT
        possible = {None}
        for key in range(3):
            for code in (f"code{key}", f"CODE{key}"):
                possible.add(f"{code}/3")
        assert set(texts) <= possible, set(texts) - possible
        if change == "none":
            # The control: on the worker, the other layer is read at all.
            assert texts[:3] == ["code0/3", "code1/3", "code2/3"]
