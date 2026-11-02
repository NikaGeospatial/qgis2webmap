"""`core/cartograph_export.py`'s `LayerScans`: a cartograph build reuses a
layer's label character set and a Value Relation's table while what they read
is unchanged, and scans again after anything that could change them.

Every case compares the build with a fresh one (`live_cache=None`): the reuse
must never change a byte of the page or a row of the report.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from nika_onlymap_exporter.core.cartograph_export import (
    LiveFrameCache,
    finish_cartograph_full,
    prepare_cartograph,
    prepare_live_reads,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

RUNTIME = RuntimeBundle(
    javascript=b"// core js placeholder",
    css=b"/* core css placeholder */",
    version="0.12.0",
    sha256="0" * 64,
    cartograph_javascript=b"// cartograph js placeholder",
    cartograph_css=b"/* cartograph css placeholder */",
)

#: The sites, in feature order: name, the other label field, pop, kind, x, y.
#: The first three and the last three have the same count and extent, so a
#: filter switching between them moves neither.
SITES = (
    ("Zürich", "x", 0, "a", 0, 0),
    ("Bern", "Øx", 10, "b", 1000, 1000),
    ("Basel", "x", 20, "a", 500, 500),
    ("Genf", "x", 30, "b", 0, 0),
    ("Chur", "x", 40, "a", 1000, 1000),
)

#: Python functions registered for one test, whose answer the test changes.
ANSWERS: dict[str, str] = {}


@dataclass
class World:
    project: object
    layout: object
    sites: object  # GeoPackage points, labelled
    kinds: object  # GeoPackage table a Value Relation on `sites.kind` reads


def _gpkg(tmp_path, memory, name: str) -> str:
    from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter

    path = str(tmp_path / f"{name}.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = name
    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        memory, path, QgsCoordinateTransformContext(), options
    )
    assert error[0] == QgsVectorFileWriter.WriterError.NoError, error
    return path


def _relation_config(kinds, **changes) -> dict:
    config = {
        "Layer": kinds.id(),
        "LayerName": kinds.name(),
        "LayerSource": kinds.source(),
        "LayerProviderName": "ogr",
        "Key": "code",
        "Value": "label",
        "AllowMulti": False,
        "AllowNull": True,
        "OrderByValue": False,
        "FilterExpression": "",
        "NofColumns": 1,
        "UseCompleter": False,
    }
    config.update(changes)
    return config


def _set_relation(world: World, **changes) -> None:
    from qgis.core import QgsEditorWidgetSetup

    world.sites.setEditorWidgetSetup(
        world.sites.fields().indexOf("kind"),
        QgsEditorWidgetSetup("ValueRelation", _relation_config(world.kinds, **changes)),
    )


def _set_label(world: World, field: str, is_expression: bool = False, case=None):
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling

    settings = QgsPalLayerSettings()
    settings.fieldName = field
    settings.isExpression = is_expression
    if case is not None:
        text_format = settings.format()
        text_format.setCapitalization(case)
        settings.setFormat(text_format)
    world.sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    world.sites.setLabelsEnabled(True)


@pytest.fixture
def world(project, tmp_path) -> World:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsGeometry,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPointXY,
        QgsPrintLayout,
        QgsRectangle,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    wm = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(wm)
    memory = QgsVectorLayer(
        "Point?crs=EPSG:3857&field=name:string&field=alt:string"
        "&field=pop:integer&field=kind:string",
        "sites",
        "memory",
    )
    features = []
    for name, alt, pop, kind, x, y in SITES:
        feature = QgsFeature(memory.fields())
        feature.setAttributes([name, alt, pop, kind])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    sites = QgsVectorLayer(_gpkg(tmp_path, memory, "sites"), "sites", "ogr")
    table = QgsVectorLayer(
        "None?field=code:string&field=label:string&field=other:string",
        "kinds",
        "memory",
    )
    rows = []
    for row in (("a", "Alpha", "Uno"), ("b", "Beta", "Dos")):
        feature = QgsFeature(table.fields())
        feature.setAttributes(list(row))
        rows.append(feature)
    table.dataProvider().addFeatures(rows)
    kinds = QgsVectorLayer(_gpkg(tmp_path, table, "kinds"), "kinds", "ogr")
    assert sites.isValid() and kinds.isValid()
    project.addMapLayer(sites)
    project.addMapLayer(kinds)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("scan-world")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(150, 100, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(wm)
    map_item.setLayers([sites])
    map_item.setExtent(QgsRectangle(-500, -500, 1500, 1500))
    map_item.refresh()
    world = World(project, layout, sites, kinds)
    _set_label(world, "name")
    _set_relation(world)
    return world


@pytest.fixture
def cache():
    cache = LiveFrameCache()
    yield cache
    cache.clear()


@pytest.fixture
def answers():
    """Registers `q2w_scan_answer()`, which answers `ANSWERS["now"]`."""
    from qgis.core import QgsExpression, qgsfunction

    @qgsfunction(group="Custom", referenced_columns=[])
    def q2w_scan_answer(feature, parent):
        return ANSWERS["now"]

    ANSWERS["now"] = "a"
    yield ANSWERS
    QgsExpression.unregisterFunction("q2w_scan_answer")


def _build(world: World, live_cache):
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(
        world.layout, world.project, RUNTIME, report, raster_cache=None
    )
    assert prepared.live_frames, "the world's map must export as a live frame"
    prepared = prepare_live_reads(
        prepared, world.project, report, live_cache=live_cache
    )
    finished = finish_cartograph_full(prepared, None, report)
    return finished.html, report.items, finished.projects


def _same_as_fresh(world: World, built) -> None:
    fresh = _build(world, None)
    assert built[0] == fresh[0]
    assert built[1] == fresh[1]
    assert built[2] == fresh[2]


def _sites(built):
    (project,) = built[2]
    (layer,) = [layer for layer in project.layers if layer.name == "sites"]
    return layer


def _character_set(built) -> str | None:
    return _sites(built).labeling.character_set


def _shown_kinds(built) -> list[str]:
    return [
        feature["properties"]["kind_shown"]
        for feature in _sites(built).geojson["features"]
    ]


def _rename(layer, feature_index: int, field: str, value: str) -> None:
    feature_id = sorted(feature.id() for feature in layer.getFeatures())[feature_index]
    layer.startEditing()
    assert layer.changeAttributeValue(feature_id, layer.fields().indexOf(field), value)


def _skip_unless_keyable(world: World) -> None:
    """QGIS 3.44 (PyQt5) gives an unset project variable as a NULL `QVariant`,
    which `value_digest` refuses: no build there gets a key, the live frames'
    read included, and every scan is made afresh (correct, not faster)."""
    from nika_onlymap_exporter.core.build_cache import value_digest
    from nika_onlymap_exporter.core.cartograph_export import (
        _expression_variables,
        _qgis_values,
    )

    variables = _expression_variables(world.project)
    if value_digest(variables, _qgis_values({})) is None:
        pytest.skip("this QGIS gives project variables no key can encode")


class TestUnchangedLayersAreNotScannedAgain:
    def test_a_second_build_reuses_both_scans(self, world, cache) -> None:
        _skip_unless_keyable(world)
        first = _build(world, cache)
        assert cache.scan_count == 2
        assert "ü" in (_character_set(first) or "")
        assert _shown_kinds(first) == ["Alpha", "Beta", "Alpha", "Beta", "Alpha"]
        again = _build(world, cache)
        assert cache.scan_count == 2
        assert again == first
        _same_as_fresh(world, again)

    def test_an_edit_of_another_field_reuses_the_relation(self, world, cache) -> None:
        """The table is the other layer's: an edit of the labelled layer
        scans the labels again, not the table."""
        _skip_unless_keyable(world)
        _build(world, cache)
        _rename(world.sites, 1, "name", "Bärn")
        again = _build(world, cache)
        assert cache.scan_count == 3
        assert "ä" in (_character_set(again) or "")
        _same_as_fresh(world, again)


class TestTheLabelsAreScannedAgain:
    def test_after_an_unsaved_edit(self, world, cache) -> None:
        first = _build(world, cache)
        _rename(world.sites, 1, "name", "Bérn")
        again = _build(world, cache)
        assert "é" not in (_character_set(first) or "")
        assert "é" in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_a_saved_edit(self, world, cache) -> None:
        _build(world, cache)
        _rename(world.sites, 1, "name", "Bérn")
        assert world.sites.commitChanges()
        again = _build(world, cache)
        assert "é" in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_undoing_an_edit(self, world, cache) -> None:
        _build(world, cache)
        _rename(world.sites, 1, "name", "Bérn")
        _build(world, cache)
        world.sites.rollBack()
        again = _build(world, cache)
        assert "é" not in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_the_label_field_changes(self, world, cache) -> None:
        _build(world, cache)
        _set_label(world, "alt")
        again = _build(world, cache)
        assert "Ø" in (_character_set(again) or "")
        assert "ü" not in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_the_label_expression_changes(self, world, cache) -> None:
        _set_label(world, '"name"', is_expression=True)
        _build(world, cache)
        _set_label(world, '"alt"', is_expression=True)
        again = _build(world, cache)
        assert "Ø" in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_the_capitalization_changes(self, world, cache) -> None:
        from qgis.core import Qgis

        _build(world, cache)
        _set_label(world, "name", case=Qgis.Capitalization.AllUppercase)
        again = _build(world, cache)
        assert "Ü" in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_after_the_filter_changes(self, world, cache) -> None:
        """Same count, same extent: only the filter itself tells."""
        assert world.sites.setSubsetString('"pop" < 30')
        first = _build(world, cache)
        assert world.sites.setSubsetString('"pop" > 10')
        assert world.sites.featureCount() == 3
        again = _build(world, cache)
        assert "ü" in (_character_set(first) or "")
        assert _character_set(again) is None
        _same_as_fresh(world, again)

    def test_after_a_project_variable_a_labelled_virtual_field_reads(
        self, world, cache
    ) -> None:
        from qgis.core import QgsExpressionContextUtils, QgsField
        from qgis.PyQt.QtCore import QMetaType

        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_mark", "ä")
        world.sites.addExpressionField(
            '@q2w_mark || "pop"', QgsField("marked", QMetaType.Type.QString)
        )
        _set_label(world, "marked")
        _build(world, cache)
        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_mark", "ö")
        again = _build(world, cache)
        assert "ö" in (_character_set(again) or "")
        assert "ä" not in (_character_set(again) or "")
        _same_as_fresh(world, again)

    def test_every_time_a_python_function_is_labelled(
        self, world, cache, answers
    ) -> None:
        """The function's body is no part of any key, so the labels are never
        taken from the cache."""
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        world.sites.addExpressionField(
            "q2w_scan_answer()", QgsField("answer", QMetaType.Type.QString)
        )
        _set_label(world, "answer")
        answers["now"] = "Ä"
        _build(world, cache)
        answers["now"] = "Ö"
        again = _build(world, cache)
        assert "Ö" in (_character_set(again) or "")
        _same_as_fresh(world, again)


class TestTheRelationIsReadAgain:
    def test_after_an_unsaved_edit_of_the_related_layer(self, world, cache) -> None:
        _build(world, cache)
        _rename(world.kinds, 0, "label", "Alfa")
        again = _build(world, cache)
        assert _shown_kinds(again)[0] == "Alfa"
        _same_as_fresh(world, again)

    def test_after_a_saved_edit_of_the_related_layer(self, world, cache) -> None:
        _build(world, cache)
        _rename(world.kinds, 0, "label", "Alfa")
        assert world.kinds.commitChanges()
        again = _build(world, cache)
        assert _shown_kinds(again)[0] == "Alfa"
        _same_as_fresh(world, again)

    def test_after_the_related_layer_is_filtered(self, world, cache) -> None:
        _build(world, cache)
        assert world.kinds.setSubsetString("\"code\" = 'a'")
        again = _build(world, cache)
        assert _shown_kinds(again)[1] == "(b)"
        _same_as_fresh(world, again)

    def test_after_the_widget_shows_another_field(self, world, cache) -> None:
        _build(world, cache)
        _set_relation(world, Value="other")
        again = _build(world, cache)
        assert _shown_kinds(again)[:2] == ["Uno", "Dos"]
        _same_as_fresh(world, again)

    def test_after_the_widget_filter_changes(self, world, cache) -> None:
        _build(world, cache)
        _set_relation(world, FilterExpression="\"code\" = 'b'")
        again = _build(world, cache)
        assert _shown_kinds(again)[:2] == ["(a)", "Beta"]
        _same_as_fresh(world, again)

    def test_every_time_its_filter_calls_a_python_function(
        self, world, cache, answers
    ) -> None:
        _set_relation(world, FilterExpression='"code" = q2w_scan_answer()')
        first = _build(world, cache)
        answers["now"] = "b"
        again = _build(world, cache)
        assert _shown_kinds(first)[:2] == ["Alpha", "(b)"]
        assert _shown_kinds(again)[:2] == ["(a)", "Beta"]
        _same_as_fresh(world, again)


class TestTheScansStayWithinTheirBudget:
    def test_a_scan_over_the_budget_is_not_kept(self, world) -> None:
        cache = LiveFrameCache(scan_bytes=10)
        try:
            _build(world, cache)
            again = _build(world, cache)
            assert cache.scan_count == 4
            assert len(cache._scans) == 0
            _same_as_fresh(world, again)
        finally:
            cache.clear()

    def test_clearing_the_cache_forgets_the_scans(self, world, cache) -> None:
        _build(world, cache)
        cache.clear()
        _build(world, cache)
        assert cache.scan_count == 4
