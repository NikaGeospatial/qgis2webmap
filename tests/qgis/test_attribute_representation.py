"""Editor-widget formatting must not change what the map draws with.

A Value Map shows "Camp" for a stored 1. The export used to write the shown
text in the field's own property, so a categorized renderer on that field was
asked to match 1 against "Camp": no category matched, the renderer's exclusions
dropped every feature, and the layer exported empty. The stored value now
stays under the field's name for drawing and filtering, and the shown value
travels beside it for the popup alone.

Every case runs through the two real read paths - the single-file web map
(`read_project`) and the cartograph's live frame (`prepare_live_reads` ->
`finish_cartograph_full`) - and asserts what a reader gets: which features are
in the file, the colour each is drawn in, and the text its popup shows.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Any

import pytest

from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph_full,
    prepare_cartograph,
    prepare_live_reads,
)
from nika_onlymap_exporter.core.export_ir import PopupFieldMode
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.manifest_builder import build_manifest
from nika_onlymap_exporter.core.project_reader import read_project
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

RED, BLUE, GREEN = "#e41a1c", "#377eb8", "#4daf4a"
CAMP_FORD = {"map": [{"Camp": 1}, {"Ford": 2}]}


@dataclass
class Exported:
    """What a reader gets of one layer: its features and its generated markup."""

    properties: list[dict[str, Any]]
    markup: str
    report: FidelityReportBuilder

    def kinds(self, field: str = "kind") -> list[Any]:
        return [p.get(field) for p in self.properties]

    def fill(self, properties: dict[str, Any]) -> str:
        """The colour the generated style draws a feature with."""
        match = re.search(r'get-fill-color="([^"]*)"', self.markup)
        assert match, "the layer has no fill-colour expression"
        expression = html_lib.unescape(match.group(1))
        for field, literal, colour in re.findall(
            r"\$(\w+) == ('[^']*'|[-\d.]+) \? '(#[0-9a-fA-F]+)'", expression
        ):
            wanted = literal[1:-1] if literal.startswith("'") else float(literal)
            value = properties.get(field)
            if value == wanted or (
                isinstance(wanted, float)
                and isinstance(value, (int, float))
                and float(value) == wanted
            ):
                return colour
        return re.findall(r"'(#[0-9a-fA-F]+)'", expression)[-1]

    def graduated_fill(self, properties: dict[str, Any]) -> str:
        """The colour of the class a numeric field's stored value falls in."""
        expression = html_lib.unescape(
            re.search(r'get-fill-color="([^"]*)"', self.markup).group(1)
        )
        match = re.fullmatch(
            r"scale\(\$(\w+), threshold, \[(.*?)\], domain=\[(.*?)\]\)", expression
        )
        assert match, expression
        field, colours, domain = match.groups()
        value = properties.get(field)
        assert isinstance(value, (int, float)), properties
        edges = [float(edge) for edge in domain.split(",") if edge.strip()]
        palette = re.findall(r"#[0-9a-fA-F]+", colours)
        return palette[sum(1 for edge in edges if value >= edge)]

    def popup(self, properties: dict[str, Any]) -> dict[str, str]:
        """label -> text the popup shows for a feature."""
        rows = re.findall(
            r'<span class="om-popup-label">(.*?)</span>'
            r'<span class="om-popup-value">\{\{(\w+)\}\}</span>',
            self.markup,
        )
        return {
            html_lib.unescape(label): str(properties.get(name, ""))
            for label, name in rows
        }


@pytest.fixture
def fake_runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core js placeholder",
        css=b"/* core css placeholder */",
        version="0.12.0",
        sha256="0" * 64,
        cartograph_javascript=b"// cartograph js placeholder",
        cartograph_css=b"/* cartograph css placeholder */",
    )


def _layer(
    project,
    rows: list[tuple[Any, ...]],
    fields: str = "kind:integer&field=note:string",
    name: str = "sites",
):
    """A point layer in Web Mercator, one feature per row, 1 km apart."""
    layer = qgis_core.QgsVectorLayer(
        f"Point?crs=EPSG:3857&field={fields}", name, "memory"
    )
    assert layer.isValid()
    features = []
    for number, row in enumerate(rows):
        feature = qgis_core.QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        feature.setGeometry(
            qgis_core.QgsGeometry.fromPointXY(
                qgis_core.QgsPointXY(1000.0 * number, 0.0)
            )
        )
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    project.addMapLayer(layer)
    return layer


def _categorized(layer, field: str, categories) -> None:
    built = [
        qgis_core.QgsRendererCategory(
            value,
            qgis_core.QgsMarkerSymbol.createSimple({"color": colour}),
            str(value),
            render,
        )
        for value, colour, render in categories
    ]
    layer.setRenderer(qgis_core.QgsCategorizedSymbolRenderer(field, built))


def _value_map(layer, field: str, config=CAMP_FORD) -> None:
    layer.setEditorWidgetSetup(
        layer.fields().indexOf(field),
        qgis_core.QgsEditorWidgetSetup("ValueMap", config),
    )


def _places_layer():
    """A table of places a Value Relation can look up, in group a or b."""
    places = qgis_core.QgsVectorLayer(
        "None?field=id:integer&field=title:string&field=grp:string",
        "places",
        "memory",
    )
    rows = []
    for key, title, group in (
        (1, "Harbour", "a"),
        (2, "Ridge", "b"),
        (3, "Zed", "a"),
        (4, "Alpha", "b"),
    ):
        feature = qgis_core.QgsFeature(places.fields())
        feature.setAttributes([key, title, group])
        rows.append(feature)
    places.dataProvider().addFeatures(rows)
    return places


def _value_relation(places):
    return qgis_core.QgsEditorWidgetSetup(
        "ValueRelation",
        {
            "Layer": places.id(),
            "LayerName": "places",
            "LayerSource": places.source(),
            "LayerProviderName": "memory",
            "Key": "id",
            "Value": "title",
            "AllowMulti": False,
            "AllowNull": False,
            "OrderByValue": False,
            "UseCompleter": False,
            "NofColumns": 1,
            "FilterExpression": "",
        },
    )


def _web_map(project, **options) -> Exported:
    report = FidelityReportBuilder()
    export = read_project(project, report, **options)
    layer = next(c for c in export.exportable_layers if c.name == "sites")
    return Exported(
        [f["properties"] for f in layer.geojson["features"]],
        build_manifest(export),
        report,
    )


def _frame(project, fake_runtime, layer_settings=None) -> Exported:
    """The same layer as a live cartograph frame reads it."""
    from atlas_fixtures import add_map

    layer = project.mapLayersByName("sites")[0]
    layout = qgis_core.QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("attribute-representation")
    add_map(layout, [layer])
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(layout, project, fake_runtime, report)
    prepared = prepare_live_reads(
        prepared, project, report, layer_settings=layer_settings
    )
    finished = finish_cartograph_full(
        prepared, project, report, layer_settings=layer_settings
    )
    frame_layer = next(
        c for c in finished.projects[0].exportable_layers if c.name == "sites"
    )
    return Exported(
        [f["properties"] for f in frame_layer.geojson["features"]],
        finished.html,
        report,
    )


@pytest.fixture(params=["web map", "cartograph frame"])
def export(request, project, fake_runtime, qgis_app):
    """Export the project's `sites` layer through either read path."""

    def run(**options) -> Exported:
        if request.param == "web map":
            return _web_map(project, **options)
        return _frame(project, fake_runtime, options.get("layer_settings"))

    return run


ROWS = [(1, "first"), (2, "second")]


class TestCategorizedField:
    def test_without_formatting_both_features_keep_their_colour(
        self, project, export
    ) -> None:
        layer = _layer(project, ROWS)
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, True)])

        out = export()

        assert out.kinds() == [1, 2]
        assert [out.fill(p) for p in out.properties] == [RED, BLUE]

    def test_a_value_map_keeps_both_features_and_their_colours(
        self, project, export
    ) -> None:
        """The reported bug: formatted values emptied the export."""
        layer = _layer(project, ROWS)
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, True)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds() == [1, 2]
        assert [out.fill(p) for p in out.properties] == [RED, BLUE]
        assert [out.popup(p)["kind"] for p in out.properties] == ["Camp", "Ford"]
        assert [out.popup(p)["note"] for p in out.properties] == ["first", "second"]
        assert not any("no features" in item.detail for item in out.report.items), (
            "the report blamed an empty source"
        )

    def test_a_switched_off_category_still_removes_its_features(
        self, project, export
    ) -> None:
        layer = _layer(project, [(1, "a"), (2, "b"), (1, "c")])
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, False)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds() == [1, 1]
        assert [out.popup(p)["kind"] for p in out.properties] == ["Camp", "Camp"]

    def test_an_unmatched_value_is_dropped_only_when_the_other_class_is_off(
        self, project, export
    ) -> None:
        layer = _layer(project, [(1, "a"), (9, "stray")])
        _categorized(layer, "kind", [(1, RED, True), ("", GREEN, False)])
        _value_map(layer, "kind")

        out = export()

        # QGIS draws nothing for 9 here: no category claims it and the
        # all-other-values category is switched off.
        assert out.kinds() == [1]

    def test_an_unmatched_value_is_kept_when_the_other_class_is_on(
        self, project, export
    ) -> None:
        layer = _layer(project, [(1, "a"), (9, "stray")])
        _categorized(layer, "kind", [(1, RED, True), ("", GREEN, True)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds() == [1, 9]
        assert out.fill(out.properties[0]) == RED
        assert out.fill(out.properties[1]) == GREEN
        # A key the Value Map does not list is shown the way QGIS shows it.
        assert out.popup(out.properties[1])["kind"] != ""

    def test_a_null_value_keeps_the_other_classes_behaviour(
        self, project, export
    ) -> None:
        layer = _layer(project, [(1, "a"), (None, "blank")])
        _categorized(layer, "kind", [(1, RED, True), ("", GREEN, True)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds() == [1, None]
        assert out.fill(out.properties[1]) == GREEN

    def test_a_null_value_is_dropped_with_the_other_class_off(
        self, project, export
    ) -> None:
        layer = _layer(project, [(1, "a"), (None, "blank")])
        _categorized(layer, "kind", [(1, RED, True), ("", GREEN, False)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds() == [1]


class TestFormattedFieldUsedElsewhere:
    def test_a_field_only_a_popup_reads_shows_its_description(
        self, project, export
    ) -> None:
        layer = _layer(project, ROWS)
        _value_map(layer, "kind")

        out = export()

        assert [out.popup(p)["kind"] for p in out.properties] == ["Camp", "Ford"]
        # The stored value is still in the file under the field's own name.
        assert out.kinds() == [1, 2]

    def test_graduated_classes_use_the_stored_numbers(self, project, export) -> None:
        layer = _layer(
            project, [(1.5, "a"), (6.5, "b")], fields="size:double&field=note:string"
        )
        _value_map(layer, "size", {"map": [{"Small": 1.5}, {"Big": 6.5}]})
        layer.setRenderer(
            qgis_core.QgsGraduatedSymbolRenderer(
                "size",
                [
                    qgis_core.QgsRendererRange(
                        0,
                        3,
                        qgis_core.QgsMarkerSymbol.createSimple({"color": RED}),
                        "lo",
                    ),
                    qgis_core.QgsRendererRange(
                        3,
                        9,
                        qgis_core.QgsMarkerSymbol.createSimple({"color": BLUE}),
                        "hi",
                    ),
                ],
            )
        )

        out = export()

        assert out.kinds("size") == [1.5, 6.5]
        assert [out.graduated_fill(p) for p in out.properties] == [RED, BLUE]
        assert [out.popup(p)["size"] for p in out.properties] == ["Small", "Big"]

    def test_a_value_relation_keeps_the_key_for_drawing(self, project, export) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        layer = _layer(project, ROWS)
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, True)])
        layer.setEditorWidgetSetup(0, _value_relation(places))

        out = export()

        assert out.kinds() == [1, 2]
        assert [out.fill(p) for p in out.properties] == [RED, BLUE]
        assert [out.popup(p)["kind"] for p in out.properties] == ["Harbour", "Ridge"]

    def test_a_value_relation_shows_unknown_and_null_keys_like_qgis(
        self, project, export
    ) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        layer = _layer(project, [(1, "a"), (9, "b"), (None, "c")])
        layer.setEditorWidgetSetup(0, _value_relation(places))

        out = export()

        assert [out.popup(p)["kind"] for p in out.properties] == [
            "Harbour",
            "(9)",
            "NULL",
        ]


class TestValueRelationMatchesQgis:
    """The popup text of a Value Relation equals QGIS's own `representValue`.

    The oracle is the formatter itself, called with the layer's own widget
    setup for each feature's stored value; the export must show the same text
    and keep the stored value untouched.
    """

    @staticmethod
    def _oracle(layer, field: str, values) -> list[str]:
        index = layer.fields().indexOf(field)
        setup = layer.fields().field(index).editorWidgetSetup()
        formatter = qgis_core.QgsApplication.fieldFormatterRegistry().fieldFormatter(
            setup.type()
        )
        return [
            formatter.representValue(layer, index, setup.config(), None, value)
            for value in values
        ]

    def _check(self, project, export, setup, field, rows, fields) -> None:
        layer = _layer(project, rows, fields=fields)
        layer.setEditorWidgetSetup(layer.fields().indexOf(field), setup)
        stored = [feature[field] for feature in layer.getFeatures()]
        expected = self._oracle(layer, field, stored)

        out = export()

        assert [out.popup(p)[field] for p in out.properties] == expected
        assert out.kinds(field) == stored

    def _config(self, places, **overrides):
        setup = _value_relation(places)
        config = setup.config()
        config.update(overrides)
        return qgis_core.QgsEditorWidgetSetup("ValueRelation", config)

    @pytest.mark.parametrize(
        "filter_expression",
        [
            "\"grp\" = 'a'",
            '"id" > 1',
            '"id" < 0',
            "current_value('kind') = 1",
            "\"id\" = current_value('kind')",
            "@undefined_variable = 1",
            "this is ((not an expression",
        ],
    )
    def test_filter_expression(self, project, export, filter_expression) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        setup = self._config(places, FilterExpression=filter_expression)
        rows = [(key, "n") for key in (1, 2, 3, 4, 9)]

        self._check(
            project, export, setup, "kind", rows, "kind:integer&field=note:string"
        )

    def test_filter_expression_hides_a_key_outside_it(self, project, export) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        setup = self._config(places, FilterExpression="\"grp\" = 'a'")
        layer = _layer(project, [(1, "a"), (2, "b")])
        layer.setEditorWidgetSetup(0, setup)

        out = export()

        assert [out.popup(p)["kind"] for p in out.properties] == ["Harbour", "(2)"]

    @pytest.mark.parametrize("order_by_value", [False, True])
    @pytest.mark.parametrize(
        "stored",
        [
            "{1,2}",
            "{2,1}",
            "{1,3,4}",
            "{4,3,1}",
            "{ 1 , 2 }",
            "{1,1}",
            "{9}",
            "{1,9}",
            "{}",
            "",
            "[1,2]",
            None,
        ],
    )
    def test_multi_select_integer_keys(
        self, project, export, stored, order_by_value
    ) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        setup = self._config(places, AllowMulti=True, OrderByValue=order_by_value)

        self._check(
            project,
            export,
            setup,
            "tags",
            [(1, stored)],
            "kind:integer&field=tags:string",
        )

    @pytest.mark.parametrize("allow_multi", [False, True])
    @pytest.mark.parametrize(
        "stored",
        [
            "a,b",
            "x}",
            "k1",
            "it's",
            "zz",
            '{"a,b",k1}',
            "{k2,k1}",
            '{"x}",k1}',
            '{"it\'s"}',
            '["k1","k2"]',
            "{a,b}",
        ],
    )
    def test_text_keys_with_commas_and_braces(
        self, project, export, stored, allow_multi
    ) -> None:
        places = qgis_core.QgsVectorLayer(
            "None?field=id:string&field=title:string", "words", "memory"
        )
        rows = []
        for key, title in (
            ("a,b", "Comma"),
            ("x}", "Brace"),
            ("k1", "One"),
            ("k2", "Two"),
            ("it's", "Quote"),
        ):
            feature = qgis_core.QgsFeature(places.fields())
            feature.setAttributes([key, title])
            rows.append(feature)
        places.dataProvider().addFeatures(rows)
        project.addMapLayer(places)
        config = _value_relation(places).config()
        config.update(LayerName="words", AllowMulti=allow_multi)
        setup = qgis_core.QgsEditorWidgetSetup("ValueRelation", config)

        self._check(
            project,
            export,
            setup,
            "tags",
            [(1, stored)],
            "kind:integer&field=tags:string",
        )

    @pytest.mark.parametrize("order_by_value", [False, True])
    @pytest.mark.parametrize("key_type", ["integer", "string"])
    def test_multi_select_order_follows_the_widget_ordering(
        self, project, export, key_type, order_by_value
    ) -> None:
        """Unsorted rows, mixed-case texts, a repeated key: QGIS's order."""
        related = qgis_core.QgsVectorLayer(
            f"None?field=id:{key_type}&field=title:string", "shuffled", "memory"
        )
        rows = []
        for key, title in (
            (10, "ten"),
            (9, "Nine"),
            (2, "two"),
            (100, "Hundred"),
            (9, "again"),
        ):
            feature = qgis_core.QgsFeature(related.fields())
            feature.setAttributes([key, title])
            rows.append(feature)
        related.dataProvider().addFeatures(rows)
        project.addMapLayer(related)
        config = _value_relation(related).config()
        config.update(
            LayerName="shuffled", AllowMulti=True, OrderByValue=order_by_value
        )
        setup = qgis_core.QgsEditorWidgetSetup("ValueRelation", config)

        self._check(
            project,
            export,
            setup,
            "tags",
            [(1, "{100,2,9,10}")],
            "kind:integer&field=tags:string",
        )

    @pytest.mark.parametrize("stored", [[1, 2], [2, 1], [], [9], [1, 9], None])
    def test_multi_select_list_field(self, project, export, stored) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        setup = self._config(places, AllowMulti=True)

        self._check(
            project,
            export,
            setup,
            "tags",
            [(1, stored)],
            "kind:integer&field=tags:integerlist",
        )

    @pytest.mark.parametrize("stored", ["1", 1.0, 1.5, "abc", 3.0])
    def test_a_text_field_holding_integer_keys(self, project, export, stored) -> None:
        places = _places_layer()
        project.addMapLayer(places)
        setup = self._config(places, UseCompleter=True, OrderByValue=True)

        self._check(
            project,
            export,
            setup,
            "note",
            [(1, stored)],
            "kind:integer&field=note:string",
        )

    def test_an_empty_related_text_shows_empty_not_the_key(
        self, project, export
    ) -> None:
        places = qgis_core.QgsVectorLayer(
            "None?field=id:integer&field=title:string", "blank", "memory"
        )
        rows = []
        for key, title in ((1, ""), (3, "Zed")):
            feature = qgis_core.QgsFeature(places.fields())
            feature.setAttributes([key, title])
            rows.append(feature)
        places.dataProvider().addFeatures(rows)
        project.addMapLayer(places)
        config = _value_relation(places).config()
        config.update(LayerName="blank", AllowMulti=True)
        setup = qgis_core.QgsEditorWidgetSetup("ValueRelation", config)

        self._check(
            project,
            export,
            setup,
            "tags",
            [(1, "{1,3}")],
            "kind:integer&field=tags:string",
        )


class TestHiddenAndCollidingNames:
    def test_hidden_in_the_popup_settings(self, project, export) -> None:
        layer = _layer(project, ROWS)
        _value_map(layer, "kind")
        layer_id = layer.id()

        from nika_onlymap_exporter.core.settings import LayerSettings

        out = export(
            layer_settings={
                layer_id: LayerSettings(fields={"kind": PopupFieldMode.HIDDEN.value})
            },
        )

        for properties in out.properties:
            assert set(properties) == {"note"}, properties

    def test_hidden_in_the_attribute_table(self, project, export) -> None:
        layer = _layer(project, ROWS)
        _value_map(layer, "kind")
        table = layer.attributeTableConfig()
        columns = table.columns()
        for column in columns:
            if column.name == "kind":
                column.hidden = True
        table.setColumns(columns)
        layer.setAttributeTableConfig(table)

        out = export()

        for properties in out.properties:
            assert set(properties) == {"note"}, properties

    def test_a_hidden_field_the_map_draws_with_keeps_only_its_raw_value(
        self, project, export
    ) -> None:
        from nika_onlymap_exporter.core.settings import LayerSettings

        layer = _layer(project, ROWS)
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, True)])
        _value_map(layer, "kind")

        out = export(
            layer_settings={
                layer.id(): LayerSettings(fields={"kind": PopupFieldMode.HIDDEN.value})
            },
        )

        for properties in out.properties:
            assert set(properties) == {"kind", "note"}, properties
        assert [out.fill(p) for p in out.properties] == [RED, BLUE]

    def test_popups_off_carry_no_display_copy(self, project, export) -> None:
        from nika_onlymap_exporter.core.settings import LayerSettings

        layer = _layer(project, ROWS)
        _value_map(layer, "kind")

        out = export(layer_settings={layer.id(): LayerSettings(popup=False)})

        assert out.properties == [{}, {}]

    def test_a_real_field_with_the_copys_name_is_not_overwritten(
        self, project, export
    ) -> None:
        layer = _layer(
            project,
            [(1, "real one"), (2, "real two")],
            fields="kind:integer&field=kind_shown:string",
        )
        _categorized(layer, "kind", [(1, RED, True), (2, BLUE, True)])
        _value_map(layer, "kind")

        out = export()

        assert out.kinds("kind_shown") == ["real one", "real two"]
        popups = [out.popup(p) for p in out.properties]
        assert [p["kind"] for p in popups] == ["Camp", "Ford"]
        assert [p["kind_shown"] for p in popups] == ["real one", "real two"]
        assert [out.fill(p) for p in out.properties] == [RED, BLUE]


class TestEmptyExportDiagnosis:
    def test_features_hidden_by_the_symbology_are_not_called_an_empty_source(
        self, project, export
    ) -> None:
        layer = _layer(project, [(7, "a"), (8, "b")])
        _categorized(layer, "kind", [(1, RED, True), ("", GREEN, False)])

        out = export()

        details = " ".join(item.detail for item in out.report.items)
        assert "no features" not in details
        assert "symbology" in details
