"""QGIS's "Formatted numbers" label option, against the text QGIS draws.

QGIS reads a label's text as a number and, when it is one, writes it back to a
set number of decimal places, with a plus sign on a positive number if asked,
in its own language's number format. The expectation is QGIS itself: the text
its labelling engine draws on a render of the same layer, read back with
`QgsLabelingResults`, under each locale the test sets as Qt's default - the
locale QGIS draws with.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest

from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.label_points import LABEL_PROPERTY
from nika_onlymap_exporter.core.manifest_builder import build_label_element
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.core.settings import LayerSettings

qgis_core = pytest.importorskip("qgis.core")
QtCore = pytest.importorskip("qgis.PyQt.QtCore")

# Integers, decimals, a negative, zero, a negative that rounds to zero, a
# million (group separators), halves (QGIS rounds them away from zero) and
# text: "1e3" and " 3.5 " read as numbers to QGIS, "1,5" and "abc" do not.
VALUES = {
    "integer": [12, 0, -7, 1234567],
    "double": [12.5, -3.14159, 0.0, 1234567.891, -0.0004, 2.5, -2.5],
    "string": ["12", " 3.5 ", "abc", "1e3", "1,5", "inf"],
}


@pytest.fixture
def locale():
    """Set Qt's default locale for one test, as QGIS's language does."""
    qt_locale = QtCore.QLocale
    before = qt_locale()
    yield lambda name: qt_locale.setDefault(qt_locale(name))
    qt_locale.setDefault(before)


def _layer(kind: str, decimals: int, plus_sign: bool, expression: str | None = None):
    layer = qgis_core.QgsVectorLayer(
        f"Point?crs=EPSG:3857&field=v:{kind}", "values", "memory"
    )
    features = []
    for index, value in enumerate(VALUES[kind]):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setGeometry(
            qgis_core.QgsGeometry.fromWkt(f"Point({index * 1000} {index * 700})")
        )
        feature["v"] = value
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = expression or "v"
    settings.isExpression = expression is not None
    settings.formatNumbers = True
    settings.decimals = decimals
    settings.plusSign = plus_sign
    settings.placement = qgis_core.Qgis.LabelPlacement.OverPoint
    placement = settings.placementSettings()
    placement.setOverlapHandling(
        qgis_core.Qgis.LabelOverlapHandling.AllowOverlapAtNoCost
    )
    settings.setPlacementSettings(placement)
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _qgis_texts(layer) -> dict[int, str]:
    """The text QGIS draws for each feature, by its position along the row."""
    settings = qgis_core.QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    extent = layer.extent()
    extent.grow(2000)
    settings.setExtent(extent)
    settings.setOutputSize(QtCore.QSize(2000, 1400))
    job = qgis_core.QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    return {
        round(label.labelRect.center().x() / 1000): label.labelText
        for label in job.takeLabelingResults().allLabels()
        if not label.isUnplaced
    }


def _exported(project, layer, layer_settings=None):
    project.addMapLayer(layer)
    report = FidelityReportBuilder()
    (read,) = read_project(
        project,
        report,
        layer_settings=(
            {layer.id(): layer_settings} if layer_settings is not None else None
        ),
    ).layers
    markup = build_label_element(read)
    payload = json.loads(
        markup.split('<script type="application/json">')[1].split("</script>")[0]
    )
    transform = qgis_core.QgsCoordinateTransform(
        qgis_core.QgsCoordinateReferenceSystem("EPSG:4326"),
        layer.crs(),
        project.transformContext(),
    )
    texts = {}
    for feature in payload["features"]:
        point = transform.transform(
            qgis_core.QgsPointXY(*feature["geometry"]["coordinates"])
        )
        texts[round(point.x() / 1000)] = feature["properties"][LABEL_PROPERTY]
    return texts, markup, report


@pytest.mark.parametrize("name", ["en_US", "de_DE", "fr_FR"])
@pytest.mark.parametrize(
    ("kind", "decimals", "plus_sign"),
    [
        ("integer", 2, True),
        ("double", 3, False),
        ("double", 0, True),
        ("string", 2, True),
    ],
)
def test_the_labels_read_as_qgis_draws_them(
    project, locale, name, kind, decimals, plus_sign
) -> None:
    locale(name)
    layer = _layer(kind, decimals, plus_sign)
    qgis = _qgis_texts(layer)
    exported, _, _ = _exported(project, layer)
    assert len(qgis) == len(VALUES[kind])
    assert exported == qgis


def test_an_expression_label_is_formatted_too(project, locale) -> None:
    locale("de_DE")
    layer = _layer("integer", 2, True, expression='"v" * 1000')
    qgis = _qgis_texts(layer)
    exported, _, _ = _exported(project, layer)
    assert qgis[0] == "+12.000,00"
    assert exported == qgis


@pytest.mark.parametrize("kind", ["double", "string"])
def test_names_beside_points_are_formatted_as_the_labels_are(
    project, locale, kind
) -> None:
    """The Layers tab's Names show a field in place of the labels, with the
    labels' Formatted numbers: as QGIS would draw that field as a label."""
    locale("de_DE")
    qgis = _qgis_texts(_layer(kind, 2, True))
    layer = _layer(kind, 2, True, expression="'not the names'")
    exported, _, report = _exported(project, layer, LayerSettings(name_field="v"))
    assert len(qgis) == len(VALUES[kind])
    assert exported == qgis
    assert "formatted as the layer's labels are" in " ".join(
        item.detail for item in report.items if item.subject.startswith("Names")
    )


def test_a_names_locale_separator_reaches_the_font_atlas(project, locale) -> None:
    """As for labels: the page collects the glyphs from the formatted names."""
    locale("fr_FR")
    layer = _layer("integer", 0, False, expression="'not the names'")
    exported, markup, _ = _exported(project, layer, LayerSettings(name_field="v"))
    assert exported[3] == "1\u202f234\u202f567"
    assert "\u202f" in markup.split('character-set="')[1].split('"')[0]


def test_a_locale_separator_reaches_the_font_atlas(project, locale) -> None:
    """fr_FR groups thousands with a narrow no-break space, which the
    runtime's default atlas does not have: it would draw as a gap-less blank
    if the page did not declare it."""
    locale("fr_FR")
    layer = _layer("integer", 0, False)
    exported, markup, _ = _exported(project, layer)
    assert exported[3] == "1\u202f234\u202f567"
    assert "\u202f" in markup.split('character-set="')[1].split('"')[0]


def test_the_formatting_is_reported(project, locale) -> None:
    locale("en_US")
    _, _, report = _exported(project, _layer("double", 1, True))
    details = " ".join(item.detail for item in report.items)
    assert "formatted as QGIS draws them, to 1 decimal place(s), with a plus" in (
        details
    )


def test_without_the_option_numbers_stay_as_the_data_has_them(project) -> None:
    layer = _layer("double", 3, False)
    settings = layer.labeling().settings()
    settings.formatNumbers = False
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    exported, _, _ = _exported(project, layer)
    assert exported[0] == "12.5"
