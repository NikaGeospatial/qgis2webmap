"""The export reads only the fields it keeps, and writes what it always wrote.

Every attribute the file does not keep used to be fetched, serialised and
parsed, then stripped. Now only the kept ones are read; the proof is the same
layer read the old way - every field - giving exactly the same layer, with the
popup, the labels, the categories, a shown value, an expression field built
from a hidden one, a filter and a clip all in play.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

import pytest

from nika_onlymap_exporter.core import layer_reader
from nika_onlymap_exporter.core.export_ir import Extent, PopupFieldMode
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

qgis_core = pytest.importorskip("qgis.core")
from qgis.PyQt.QtCore import QMetaType  # noqa: E402

WIDE = [f"wide{i}" for i in range(40)]


def _memory_layer():
    fields = "&field=".join(
        [
            "name:string",
            "kind:integer",
            "note:string",
            "secret:string",
            *(f"{name}:double" for name in WIDE),
        ]
    )
    layer = qgis_core.QgsVectorLayer(
        f"Point?crs=EPSG:3857&field={fields}", "sites", "memory"
    )
    assert layer.isValid()
    features = []
    for number in range(30):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttributes(
            [
                f"Site {number}",
                number % 4 or None,
                f"note {number}",
                f"secret {number}",
                *(number * 1.5 + i for i in range(len(WIDE))),
            ]
        )
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(
                qgis_core.QgsPointXY(1.0e5 * number, 5.0e6 + 1.0e4 * number)
            )
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def _geopackage_layer(tmp_path):
    source = _memory_layer()
    path = str(tmp_path / "sites.gpkg")
    options = qgis_core.QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    error = qgis_core.QgsVectorFileWriter.writeAsVectorFormatV3(
        source, path, qgis_core.QgsCoordinateTransformContext(), options
    )
    assert error[0] == qgis_core.QgsVectorFileWriter.WriterError.NoError, error
    layer = qgis_core.QgsVectorLayer(path, "sites", "ogr")
    assert layer.isValid()
    return layer


def _style(layer) -> None:
    """Everything that reads a field: a shown value, an expression field from
    a hidden one, categories with one switched off, labels and a filter."""
    fields = layer.fields()
    layer.setEditorWidgetSetup(
        fields.indexOf("kind"),
        qgis_core.QgsEditorWidgetSetup(
            "ValueMap", {"map": [{"Camp": 1}, {"Ford": 2}, {"Hut": 3}]}
        ),
    )
    layer.addExpressionField(
        'upper("secret") || \' / \' || "wide3"',
        qgis_core.QgsField("shout", QMetaType.Type.QString),
    )
    categories = []
    for value, colour in ((1, "#ff0000"), (2, "#00ff00"), (3, "#0000ff")):
        symbol = qgis_core.QgsMarkerSymbol.createSimple({"color": colour})
        categories.append(
            qgis_core.QgsRendererCategory(value, symbol, str(value), value != 3)
        )
    layer.setRenderer(qgis_core.QgsCategorizedSymbolRenderer("kind", categories))
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = "name"
    layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.setSubsetString('"wide0" < 40')


FIELD_MODES = {
    "secret": PopupFieldMode.HIDDEN.value,
    **{name: PopupFieldMode.HIDDEN.value for name in WIDE if name != "wide7"},
}


def _read(layer, **kwargs):
    return layer_reader.read_layer(
        layer, FidelityReportBuilder(), field_modes=FIELD_MODES, **kwargs
    )


def _read_every_field(layer, monkeypatch, **kwargs):
    """The read as it was: no field list, so every column is fetched."""
    real = layer_reader.snapshot_features

    def every_field(layer, clip_extent, display_names=None, keep=None):
        return real(layer, clip_extent, display_names)

    with monkeypatch.context() as patch:
        patch.setattr(layer_reader, "snapshot_features", every_field)
        return _read(layer, **kwargs)


@pytest.fixture(params=["memory", "ogr"])
def wide_layer(request, qgis_app, tmp_path):
    layer = (
        _memory_layer() if request.param == "memory" else _geopackage_layer(tmp_path)
    )
    _style(layer)
    return layer


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"with_popup": False},
        {"clip_extent": Extent(west=1.0, south=40.0, east=12.0, north=48.0)},
        {"name_field": "note"},
    ],
    ids=["popup", "no popup", "clipped", "names"],
)
def test_the_exported_layer_is_unchanged(wide_layer, monkeypatch, kwargs) -> None:
    expected = _read_every_field(wide_layer, monkeypatch, **kwargs)
    found = _read(wide_layer, **kwargs)

    assert expected is not None and found is not None
    assert expected.geojson["features"], "the fixture must export something"
    assert found == expected
    # Byte for byte as the page carries it, key order included.
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)


def test_only_the_kept_fields_are_read(wide_layer) -> None:
    pending = layer_reader.prepare_layer(
        wide_layer, FidelityReportBuilder(), field_modes=FIELD_MODES
    )
    assert isinstance(pending, layer_reader.PendingLayer)
    fields = wide_layer.fields()
    read = {fields.at(index).name() for index in pending.features.attribute_indexes}
    # A GeoPackage's `fid` is a column like the others, shown in the popup.
    assert read - {"fid"} == {"name", "kind", "note", "wide7", "shout"}

    exported = pending.finish(FidelityReportBuilder())
    assert exported is not None
    properties = exported.geojson["features"][0]["properties"]
    # What each reader needs is there, the shown value and the expression
    # field's value - made from a field that was not itself read - included.
    assert set(properties) - {"fid"} == {
        "name",
        "kind",
        "note",
        "wide7",
        "shout",
        "kind_shown",
    }
    assert properties["shout"].startswith("SECRET ")


def test_the_source_is_asked_for_those_fields_only(wide_layer) -> None:
    """Not merely stripped afterwards: the request names them, clipped or not."""
    import dataclasses

    pending = layer_reader.prepare_layer(
        wide_layer, FidelityReportBuilder(), field_modes=FIELD_MODES
    )
    assert isinstance(pending, layer_reader.PendingLayer)
    asked: list = []

    class Recording:
        def __init__(self, source) -> None:
            self.source = source

        def getFeatures(self, request):  # noqa: N802 - QGIS's name
            asked.append(
                (
                    bool(
                        request.flags()
                        & qgis_core.QgsFeatureRequest.Flag.SubsetOfAttributes
                    ),
                    sorted(request.subsetOfAttributes()),
                )
            )
            return self.source.getFeatures(request)

    features = dataclasses.replace(
        pending.features, source=Recording(pending.features.source)
    )
    for clip in (None, Extent(west=1.0, south=40.0, east=12.0, north=48.0)):
        layer_reader.export_geojson_from(
            features, FidelityReportBuilder(), clip_extent=clip
        )
    expected = sorted(pending.features.attribute_indexes)
    assert asked == [(True, expected), (True, expected)]
    assert len(expected) < len(wide_layer.fields()) - 30


def test_a_layer_keeping_no_field_reads_and_writes_none(qgis_app, monkeypatch) -> None:
    """Nothing drawn from a field and no popup: no field is kept, so none is
    read - and the exporter, for which no list means every field, writes none."""
    layer = _memory_layer()
    expected = _read_every_field(layer, monkeypatch, with_popup=False)
    pending = layer_reader.prepare_layer(
        layer, FidelityReportBuilder(), with_popup=False
    )
    assert isinstance(pending, layer_reader.PendingLayer)
    assert pending.features.attribute_indexes == ()
    found = pending.finish(FidelityReportBuilder())
    assert found == expected
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)
    assert all(f["properties"] == {} for f in found.geojson["features"])


def _regions(project):
    regions = qgis_core.QgsVectorLayer(
        "None?field=code:integer&field=label:string&field=unused:string",
        "regions",
        "memory",
    )
    rows = []
    for code, label in ((1, "North"), (2, "South"), (3, "East")):
        row = qgis_core.QgsFeature(regions.fields())
        row.setAttributes([code, label, "x"])
        rows.append(row)
    regions.dataProvider().addFeatures(rows)
    project.addMapLayer(regions)
    return regions


def test_joins_value_relations_and_unsaved_edits_read_alike(
    project, monkeypatch
) -> None:
    """A joined column, a Value Relation shown in the popup and an edit not
    yet saved: each comes through the layer's feature source, which fetches
    what they depend on (the join key, the stored key) even when that is not
    itself kept."""
    regions = _regions(project)
    layer = _memory_layer()
    project.addMapLayer(layer)
    join = qgis_core.QgsVectorLayerJoinInfo()
    join.setJoinLayer(regions)
    join.setJoinFieldName("code")
    join.setTargetFieldName("kind")
    join.setJoinFieldNamesSubset(["label"])
    join.setPrefix("region_")
    assert layer.addJoin(join)
    layer.setEditorWidgetSetup(
        layer.fields().indexOf("kind"),
        qgis_core.QgsEditorWidgetSetup(
            "ValueRelation",
            {
                "Layer": regions.id(),
                "Key": "code",
                "Value": "label",
                "AllowNull": True,
            },
        ),
    )
    assert layer.startEditing()
    first = next(layer.getFeatures())
    assert layer.changeAttributeValue(
        first.id(), layer.fields().indexOf("note"), "edited, not saved"
    )
    # The join key is shown only through the join: hidden itself.
    modes = {**FIELD_MODES, "kind": PopupFieldMode.HIDDEN.value}

    def read(**kwargs):
        return layer_reader.read_layer(
            layer, FidelityReportBuilder(), field_modes=modes, **kwargs
        )

    real = layer_reader.snapshot_features

    def every_field(layer, clip_extent, display_names=None, keep=None):
        return real(layer, clip_extent, display_names)

    with monkeypatch.context() as patch:
        patch.setattr(layer_reader, "snapshot_features", every_field)
        expected = read()
    found = read()

    assert found == expected
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)
    properties = [f["properties"] for f in found.geojson["features"]]
    assert "kind" not in properties[0]
    assert {p["region_label"] for p in properties} >= {"North", "South", "East"}
    assert properties[0]["note"] == "edited, not saved"

    # And the Value Relation's text, with its key kept for the popup.
    del modes["kind"]
    with monkeypatch.context() as patch:
        patch.setattr(layer_reader, "snapshot_features", every_field)
        expected = read()
    found = read()
    assert found == expected
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)
    shown = {f["properties"].get("kind_shown") for f in found.geojson["features"]}
    assert {"North", "South", "East"} <= shown
    layer.rollBack()
