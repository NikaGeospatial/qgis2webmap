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

import html
import json
import re

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

    def every_field(
        layer, clip_extent, display_names=None, keep=None, scans=None, label=None
    ):
        return real(layer, clip_extent, display_names, scans=scans, label=label)

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


def test_an_expression_label_reads_its_fields_and_writes_only_its_text(
    wide_layer, monkeypatch
) -> None:
    """Its fields are read, hidden or not, so it works out as it would from
    every field; only the text it gives is written."""
    settings = qgis_core.QgsPalLayerSettings()
    settings.fieldName = '"secret" || \' / \' || "wide3"'
    settings.isExpression = True
    wide_layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))

    expected = _read_every_field(wide_layer, monkeypatch)
    found = _read(wide_layer)
    assert found == expected
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)

    pending = layer_reader.prepare_layer(
        wide_layer, FidelityReportBuilder(), field_modes=FIELD_MODES
    )
    assert isinstance(pending, layer_reader.PendingLayer)
    fields = wide_layer.fields()
    read = {fields.at(index).name() for index in pending.features.attribute_indexes}
    assert {"secret", "wide3"} <= read
    properties = found.geojson["features"][0]["properties"]
    assert properties["label_text"].startswith("secret ")
    assert "secret" not in properties and "wide3" not in properties


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

    def every_field(
        layer, clip_extent, display_names=None, keep=None, scans=None, label=None
    ):
        return real(layer, clip_extent, display_names, scans=scans, label=label)

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


def _saved_as_geopackage(layer, tmp_path, name: str):
    path = str(tmp_path / f"{name}.gpkg")
    options = qgis_core.QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    error = qgis_core.QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, qgis_core.QgsCoordinateTransformContext(), options
    )
    assert error[0] == qgis_core.QgsVectorFileWriter.WriterError.NoError, error
    saved = qgis_core.QgsVectorLayer(path, name, "ogr")
    assert saved.isValid()
    return saved


@pytest.mark.parametrize("provider", ["memory", "ogr"])
def test_unsaved_edits_through_joins_and_expression_fields_read_alike(
    project, monkeypatch, tmp_path, provider
) -> None:
    """On a GeoPackage as well as in memory: edits not yet saved - to a kept
    field, to hidden fields that expression fields are made of, to a join key
    that is itself not kept, a feature added and one deleted - with expression
    fields over a hidden field, over `attributes()` and over a joined field.
    Each value comes through the layer's feature source, which fetches what it
    depends on even when that is not itself kept."""
    regions = _regions(project)
    layer = _memory_layer()
    if provider == "ogr":
        regions = _saved_as_geopackage(regions, tmp_path, "regions")
        project.addMapLayer(regions)
        layer = _saved_as_geopackage(layer, tmp_path, "sites")
    project.addMapLayer(layer)
    join = qgis_core.QgsVectorLayerJoinInfo()
    join.setJoinLayer(regions)
    join.setJoinFieldName("code")
    join.setTargetFieldName("kind")
    join.setJoinFieldNamesSubset(["label"])
    join.setPrefix("region_")
    assert layer.addJoin(join)
    for expression, name in (
        ('upper("secret") || \' / \' || "wide3"', "shout"),
        ("attributes()['secret']", "whisper"),
        ("\"region_label\" || ' side'", "where"),
    ):
        layer.addExpressionField(
            expression, qgis_core.QgsField(name, QMetaType.Type.QString)
        )
    layer.setSubsetString('"wide0" < 40')

    assert layer.startEditing()
    fields = layer.fields()
    ids = {feature["name"]: feature.id() for feature in layer.getFeatures()}
    for name, field, value in (
        ("Site 0", "note", "edited, not saved"),
        ("Site 0", "secret", "edited secret"),
        ("Site 1", "kind", 3),
        ("Site 2", "wide3", 999.5),
    ):
        assert layer.changeAttributeValue(ids[name], fields.indexOf(field), value)
    assert layer.deleteFeature(ids["Site 5"])
    added = qgis_core.QgsFeature(fields)
    added["name"] = "Site new"
    added["kind"] = 2
    added["secret"] = "fresh"
    added["wide0"] = 1.0
    added.setGeometry(
        qgis_core.QgsGeometry.fromPointXY(qgis_core.QgsPointXY(2.0e5, 5.1e6))
    )
    assert layer.addFeature(added)

    # The join key and the fields the expressions are made of are not kept.
    modes = {**FIELD_MODES, "kind": PopupFieldMode.HIDDEN.value}

    def read():
        return layer_reader.read_layer(
            layer, FidelityReportBuilder(), field_modes=modes
        )

    real = layer_reader.snapshot_features

    def every_field(
        layer, clip_extent, display_names=None, keep=None, scans=None, label=None
    ):
        return real(layer, clip_extent, display_names, scans=scans, label=label)

    try:
        pending = layer_reader.prepare_layer(
            layer, FidelityReportBuilder(), field_modes=modes
        )
        assert isinstance(pending, layer_reader.PendingLayer)
        read_names = {
            layer.fields().at(index).name()
            for index in pending.features.attribute_indexes
        }
        assert read_names.isdisjoint({"kind", "secret", "wide3"})

        with monkeypatch.context() as patch:
            patch.setattr(layer_reader, "snapshot_features", every_field)
            expected = read()
        found = read()
    finally:
        layer.rollBack()

    assert found == expected
    assert json.dumps(found.geojson) == json.dumps(expected.geojson)
    by_name = {
        f["properties"]["name"]: f["properties"] for f in found.geojson["features"]
    }
    assert "Site 5" not in by_name and "Site 26" in by_name and "Site 27" not in by_name
    edited = by_name["Site 0"]
    assert edited["note"] == "edited, not saved"
    assert edited["whisper"] == "edited secret"
    assert edited["shout"].startswith("EDITED SECRET / ")
    assert by_name["Site 2"]["shout"].endswith(" / 999.5")
    assert by_name["Site 1"]["region_label"] == "East"
    assert by_name["Site 1"]["where"] == "East side"
    assert by_name["Site 3"]["region_label"] == "East"
    assert by_name["Site 4"]["region_label"] is None
    assert by_name["Site new"]["whisper"] == "fresh"
    assert by_name["Site new"]["region_label"] == "South"
    assert all("kind" not in p and "secret" not in p for p in by_name.values())


def _hidden_in_the_attribute_table(layer, names: set[str]) -> None:
    """Hide `names` in QGIS's attribute table, which leaves them out of the
    exported data."""
    config = layer.attributeTableConfig()
    config.update(layer.fields())
    columns = config.columns()
    for column in columns:
        if column.name in names:
            column.hidden = True
    config.setColumns(columns)
    layer.setAttributeTableConfig(config)


@pytest.mark.parametrize(
    ("filter_expression", "sort", "page_name"),
    [
        ('"score" <> 1', None, None),
        (None, ('"rank"', True), None),
        (None, ('"score"', False), None),
        ('"score" > 1 OR "rank" = \'a1\'', ('length("rank")', True), None),
        ("\"lot\" <> 'p3'", ('"score"', True), None),
        ('"rank" IS NOT NULL', ('"lot"', False), None),
        ('"score" IS NULL OR "score" > 2', ('"rank"', False), '"rank"'),
    ],
    ids=[
        "filter on a left-out field",
        "sort on a left-out text field",
        "sort on a left-out number field descending",
        "both on left-out fields",
        "filter on a kept field, sort on a left-out one",
        "filter on a left-out field, sort on a kept one",
        "page names from a left-out field",
    ],
)
def test_an_atlas_on_fields_left_out_of_the_export(
    project, monkeypatch, filter_expression, sort, page_name
) -> None:
    """An atlas whose filter, sort or page names read fields hidden in the
    attribute table, which the export leaves out of the data and so no longer
    reads. The cartograph comes out byte for byte as when every field was
    read, and pages what QGIS's own atlas pages, in its order."""
    from atlas_fixtures import (
        build_atlas_layout,
        qgis_atlas_page_ids,
        use_ranked_parcels,
    )

    from nika_onlymap_exporter.core import cartograph_export
    from nika_onlymap_exporter.core.cartograph_ir import ATLAS_ORDER_KEY
    from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

    runtime = RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )
    built = build_atlas_layout(project)
    ranked = use_ranked_parcels(built)
    _hidden_in_the_attribute_table(ranked, {"score", "rank"})
    atlas = built.atlas
    if filter_expression is not None:
        atlas.setFilterFeatures(True)
        atlas.setFilterExpression(filter_expression)
    if sort is not None:
        atlas.setSortFeatures(True)
        atlas.setSortExpression(sort[0])
        atlas.setSortAscending(sort[1])
    if page_name is not None:
        atlas.setPageNameExpression(page_name)

    def export():
        cartograph_export._DEFAULT_LIVE_CACHE.clear()
        report = FidelityReportBuilder()
        prepared = cartograph_export.prepare_cartograph(
            built.layout, project, runtime, report, raster_cache=None
        )
        finished = cartograph_export.finish_cartograph_full(prepared, project, report)
        return finished, [(i.status, i.subject, i.detail) for i in report.items]

    real = layer_reader.snapshot_features

    def every_field(
        layer, clip_extent, display_names=None, keep=None, scans=None, label=None
    ):
        return real(layer, clip_extent, display_names, scans=scans, label=label)

    with monkeypatch.context() as patch:
        patch.setattr(layer_reader, "snapshot_features", every_field)
        expected, expected_rows = export()
    found, rows = export()

    assert found.html == expected.html
    assert rows == expected_rows
    (export_project,) = found.projects
    (coverage,) = [
        layer for layer in export_project.exportable_layers if layer.name == "ranked"
    ]
    features = coverage.geojson["features"]
    assert all({"score", "rank"}.isdisjoint(f["properties"]) for f in features)
    assert any("field 'rank'" in row[2] for row in rows) is (page_name is not None)

    # The pages, by the runtime's rule: what the filter keeps, in the order
    # carried on each feature.
    from atlas_filter_eval import evaluate_filter

    (element,) = re.findall(r"<om-atlas\b[^>]*>", found.html)
    written_filter = re.search(r'\bfilter="([^"]*)"', element)
    assert (written_filter is not None) is (filter_expression is not None)
    ids = [feature["id"] for feature in features]
    kept = (
        ids
        if written_filter is None
        else [
            i
            for i, ok in zip(
                ids, evaluate_filter(html.unescape(written_filter.group(1)), ids)
            )
            if ok
        ]
    )
    places = {feature["id"]: feature.get(ATLAS_ORDER_KEY) for feature in features}
    if f'sort="{ATLAS_ORDER_KEY}"' in element:
        kept.sort(key=lambda i: (places[i] is None, places[i] or 0))
    want = qgis_atlas_page_ids(built.layout)
    if sort is None:
        kept, want = sorted(kept), sorted(want)
    assert kept == want
