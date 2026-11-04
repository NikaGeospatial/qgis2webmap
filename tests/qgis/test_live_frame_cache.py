"""`core/cartograph_export.py`'s `LiveFrameCache`: autosave reuses a live
frame's read while nothing it is made from has changed, and reads it again
after anything that could change what the frame carries.

Every case compares the cached build with a fresh one (`live_cache=None`):
the cache must never change a byte of the page or a row of the report.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import pytest

from nika_onlymap_exporter.core.cartograph_export import (
    LiveFrameCache,
    LiveFrameSource,
    finish_cartograph_full,
    prepare_cartograph,
    prepare_live_reads,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.settings import LayerSettings
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


@dataclass
class World:
    project: object
    layout: object
    map_item: object
    note: object
    sites: object  # GeoPackage points
    zones: object  # GeoPackage polygons
    path: str  # the sites' file


def _gpkg(tmp_path, memory, name: str) -> str:
    """`memory` written to its own GeoPackage, whose path is returned."""
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


def _sites_gpkg(tmp_path) -> str:
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    memory = QgsVectorLayer(
        "Point?crs=EPSG:3857&field=name:string&field=pop:integer", "sites", "memory"
    )
    features = []
    for index in range(5):
        feature = QgsFeature(memory.fields())
        feature.setAttributes([f"site {index}", index * 10])
        feature.setGeometry(
            QgsGeometry.fromPointXY(QgsPointXY(100 + index * 150, 100 + index * 100))
        )
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    return _gpkg(tmp_path, memory, "sites")


def _zones_gpkg(tmp_path) -> str:
    from qgis.core import QgsFeature, QgsGeometry, QgsRectangle, QgsVectorLayer

    memory = QgsVectorLayer(
        "Polygon?crs=EPSG:3857&field=label:string", "zones", "memory"
    )
    zone = QgsFeature(memory.fields())
    zone.setAttributes(["north"])
    zone.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1000, 1000)))
    memory.dataProvider().addFeatures([zone])
    return _gpkg(tmp_path, memory, "zones")


@pytest.fixture
def world(project, tmp_path) -> World:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsLayoutItemLabel,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    wm = QgsCoordinateReferenceSystem("EPSG:3857")
    project.setCrs(wm)
    path = _sites_gpkg(tmp_path)
    sites = QgsVectorLayer(path, "sites", "ogr")
    assert sites.isValid()
    zones = QgsVectorLayer(_zones_gpkg(tmp_path), "zones", "ogr")
    assert zones.isValid()
    project.addMapLayer(sites)
    project.addMapLayer(zones)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("cache-world")
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(150, 100, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(wm)
    map_item.setLayers([sites, zones])
    extent = zones.extent()
    extent.grow(500)
    map_item.setExtent(extent)
    map_item.refresh()
    note = QgsLayoutItemLabel(layout)
    note.setText("A note")
    layout.addLayoutItem(note)
    note.attemptMove(QgsLayoutPoint(170, 20, QgsUnitTypes.LayoutMillimeters))
    note.attemptResize(QgsLayoutSize(60, 10, QgsUnitTypes.LayoutMillimeters))
    return World(project, layout, map_item, note, sites, zones, path)


@pytest.fixture
def cache():
    cache = LiveFrameCache()
    yield cache
    cache.clear()


def _build(world: World, live_cache, title=None, layer_settings=None, writer=None):
    report = FidelityReportBuilder()
    prepared = prepare_cartograph(
        world.layout,
        world.project,
        RUNTIME,
        report,
        raster_cache=None,
        title_override=title,
    )
    assert prepared.live_frames, "the world's map must export as a live frame"
    prepared = prepare_live_reads(
        prepared,
        world.project,
        report,
        layer_settings=layer_settings,
        live_cache=live_cache,
    )
    finished = finish_cartograph_full(prepared, None, report, writer=writer)
    return finished.html, report.items, finished.projects


def _same_as_fresh(world: World, built, **kwargs) -> None:
    fresh = _build(world, None, **kwargs)
    assert built[0] == fresh[0]
    assert built[1] == fresh[1]
    assert built[2] == fresh[2]


class TestEditsThatDoNotTouchTheData:
    def test_a_note_edit_reuses_the_read_and_writes_the_same_page(
        self, world, cache
    ) -> None:
        from qgis.core import QgsLayoutPoint, QgsUnitTypes

        first = _build(world, cache)
        world.note.setText("Another note")
        world.note.attemptMove(QgsLayoutPoint(175, 30, QgsUnitTypes.LayoutMillimeters))
        again = _build(world, cache)
        assert cache.read_count == 1
        assert again[0] != first[0]
        _same_as_fresh(world, again)

    def test_a_new_title_reuses_the_read(self, world, cache) -> None:
        _build(world, cache, title="One")
        again = _build(world, cache, title="Two")
        assert cache.read_count == 1
        _same_as_fresh(world, again, title="Two")

    def test_a_licence_key_reuses_the_read_but_not_the_map_element(
        self, world, cache
    ) -> None:
        """The key reaches the `<om-map>`; the read does not depend on it."""
        from nika_onlymap_exporter.core.license_policy import default_policy
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        _build(world, cache)
        writer = OnlyMapWriter(license_policy=default_policy("test-key-123"))
        licensed = _build(world, cache, writer=writer)
        assert cache.read_count == 1
        assert 'license-key="test-key-123"' in licensed[0]
        _same_as_fresh(world, licensed, writer=writer)
        unlicensed = _build(world, cache)
        assert "license-key" not in unlicensed[0]

    def test_a_reused_read_is_never_changed_by_the_builds_using_it(
        self, world, cache
    ) -> None:
        """The cached GeoJSON is shared with every later page, so nothing on
        the way to the page may change it in place."""
        _build(world, cache)
        (held,) = [entry for _key, entry, _size in cache._entries._entries.values()]
        before = copy.deepcopy(held.project)
        for title in ("One", "Two"):
            _build(world, cache, title=title)
        assert held.project == before
        assert cache.read_count == 1


def _edit_a_geometry(world: World) -> None:
    from qgis.core import QgsGeometry, QgsPointXY

    layer = world.sites
    layer.startEditing()
    layer.changeGeometry(
        next(layer.getFeatures()).id(), QgsGeometry.fromPointXY(QgsPointXY(300, 900))
    )


def _edit_an_attribute(world: World) -> None:
    """Same count, same extent, same style, uncommitted: only the layer's
    edit signals say the data changed."""
    layer = world.sites
    layer.startEditing()
    layer.changeAttributeValue(
        next(layer.getFeatures()).id(), layer.fields().indexOf("name"), "renamed"
    )


def _commit_an_attribute(world: World) -> None:
    _edit_an_attribute(world)
    assert world.sites.commitChanges()


def _add_through_the_provider(world: World) -> None:
    """Below the edit buffer, with no layer signal: the count and the file
    move."""
    from qgis.core import QgsFeature, QgsGeometry, QgsRectangle

    zone = QgsFeature(world.zones.fields())
    zone.setAttributes(["inner"])
    zone.setGeometry(QgsGeometry.fromRect(QgsRectangle(100, 100, 200, 200)))
    world.zones.dataProvider().addFeatures([zone])


def _move_through_the_provider(world: World) -> None:
    """Below the edit buffer, with no layer signal: the extent and the file
    move."""
    from qgis.core import QgsGeometry, QgsRectangle

    zone_id = next(world.zones.getFeatures()).id()
    assert world.zones.dataProvider().changeGeometryValues(
        {zone_id: QgsGeometry.fromRect(QgsRectangle(0, 0, 3000, 1000))}
    )
    world.zones.updateExtents()


def _edit_the_file_elsewhere(world: World) -> None:
    """Another layer on the same GeoPackage writes to it: the project's own
    layer hears nothing, but its file changed."""
    from qgis.core import QgsVectorLayer

    other = QgsVectorLayer(world.path, "other", "ogr")
    feature_id = next(other.getFeatures()).id()
    assert other.dataProvider().changeAttributeValues(
        {feature_id: {other.fields().indexOf("name"): "changed on disk"}}
    )
    del other


def _restyle(world: World) -> None:
    from qgis.core import QgsFillSymbol

    world.zones.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#0000ff"}))
    world.zones.triggerRepaint()


def _filter(world: World) -> None:
    assert world.sites.setSubsetString('"pop" > 15')


def _label(world: World) -> None:
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling

    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    world.sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    world.sites.setLabelsEnabled(True)


def _label_from_an_expression(world: World) -> None:
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling

    settings = QgsPalLayerSettings()
    settings.fieldName = "\"name\" || ' (' || \"pop\" || ')'"
    settings.isExpression = True
    world.sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    world.sites.setLabelsEnabled(True)


def _rename(world: World) -> None:
    world.sites.setName("Sites renamed")


def _hide_in_the_tree(world: World) -> None:
    node = world.project.layerTreeRoot().findLayer(world.zones.id())
    node.setItemVisibilityChecked(False)


def _add_a_layer(world: World) -> None:
    from pathlib import Path

    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    memory = QgsVectorLayer("Point?crs=EPSG:3857", "extra", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500, 500)))
    memory.dataProvider().addFeatures([feature])
    path = _gpkg(Path(world.path).parent, memory, "extra")
    extra = QgsVectorLayer(path, "extra", "ogr")
    world.project.addMapLayer(extra)
    world.map_item.setLayers([world.sites, world.zones, extra])


def _remove_a_layer(world: World) -> None:
    world.map_item.setLayers([world.sites])


def _reorder(world: World) -> None:
    world.map_item.setLayers([world.zones, world.sites])


def _move_the_frame(world: World) -> None:
    extent = world.map_item.extent()
    extent.grow(2000)
    world.map_item.setExtent(extent)


def _change_the_frame_crs(world: World) -> None:
    from qgis.core import QgsCoordinateReferenceSystem

    world.map_item.setCrs(QgsCoordinateReferenceSystem("EPSG:3395"))


def _add_a_virtual_field(world: World) -> None:
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QMetaType

    world.sites.addExpressionField(
        'upper("name")', QgsField("shout", QMetaType.Type.QString)
    )


DATA_CHANGES = {
    "a geometry edited in the edit buffer": _edit_a_geometry,
    "an attribute edited in the edit buffer": _edit_an_attribute,
    "an attribute edit committed": _commit_an_attribute,
    "a feature added through the provider": _add_through_the_provider,
    "a feature moved through the provider": _move_through_the_provider,
    "the layer's file written by something else": _edit_the_file_elsewhere,
    "a restyle": _restyle,
    "a filter": _filter,
    "labels switched on": _label,
    "labels from an expression switched on": _label_from_an_expression,
    "a layer renamed": _rename,
    "a layer hidden in the layers panel": _hide_in_the_tree,
    "a layer added to the frame": _add_a_layer,
    "a layer taken out of the frame": _remove_a_layer,
    "the frame's layers reordered": _reorder,
    "the frame's extent": _move_the_frame,
    "the frame's CRS": _change_the_frame_crs,
    "a virtual field added": _add_a_virtual_field,
}


def _label_texts(built, layer_name: str) -> list[str]:
    """The label text a build's live frame carries for `layer_name`."""
    from nika_onlymap_exporter.core.label_points import (
        LABEL_PROPERTY,
        build_label_collection,
    )

    (layer,) = [
        layer
        for project in built[2]
        for layer in project.layers
        if layer.name == layer_name
    ]
    labeling = layer.labeling
    collection = build_label_collection(layer.geojson, labeling.field_name)
    return sorted(f["properties"][LABEL_PROPERTY] for f in collection["features"])


class TestExpressionLabels:
    """A live frame's labels from an expression are worked out as its layers
    are read, and the read is reused while nothing they read changes."""

    def test_the_frame_carries_the_worked_out_text(self, world, cache) -> None:
        _label_from_an_expression(world)
        built = _build(world, cache)
        assert _label_texts(built, "sites") == [
            f"site {index} ({index * 10})" for index in range(5)
        ]

    def test_a_note_edit_reuses_the_read(self, world, cache) -> None:
        _label_from_an_expression(world)
        _build(world, cache)
        world.note.setText("Another note")
        again = _build(world, cache)
        assert cache.read_count == 1
        _same_as_fresh(world, again)

    def test_a_project_variable_the_labels_read_reads_again(self, world, cache) -> None:
        from qgis.core import (
            QgsExpressionContextUtils,
            QgsPalLayerSettings,
            QgsVectorLayerSimpleLabeling,
        )

        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_unit", "a")
        settings = QgsPalLayerSettings()
        settings.fieldName = "\"name\" || ' ' || @q2w_unit"
        settings.isExpression = True
        world.sites.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        world.sites.setLabelsEnabled(True)
        first = _build(world, cache)
        assert _label_texts(first, "sites")[0] == "site 0 a"
        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_unit", "b")
        again = _build(world, cache)
        assert cache.read_count == 2
        assert _label_texts(again, "sites")[0] == "site 0 b"
        _same_as_fresh(world, again)


class TestEditsThatTouchTheData:
    @pytest.mark.parametrize("change", list(DATA_CHANGES), ids=list(DATA_CHANGES))
    def test_the_frame_is_read_again(self, world, cache, change) -> None:
        first = _build(world, cache)
        DATA_CHANGES[change](world)
        again = _build(world, cache)
        assert cache.read_count == 2
        assert again[2] != first[2] or again[0] != first[0]
        _same_as_fresh(world, again)

    def test_a_layers_tab_setting_reads_again(self, world, cache) -> None:
        _build(world, cache)
        settings = {world.sites.id(): LayerSettings(popup=False)}
        again = _build(world, cache, layer_settings=settings)
        assert cache.read_count == 2
        _same_as_fresh(world, again, layer_settings=settings)

    def test_a_project_variable_a_virtual_field_reads_reads_again(
        self, world, cache
    ) -> None:
        from qgis.core import QgsExpressionContextUtils, QgsField
        from qgis.PyQt.QtCore import QMetaType

        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_tag", "a")
        world.sites.addExpressionField(
            "@q2w_tag", QgsField("tag", QMetaType.Type.QString)
        )
        first = _build(world, cache)
        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_tag", "b")
        again = _build(world, cache)
        assert cache.read_count == 2
        assert again[2] != first[2]
        _same_as_fresh(world, again)

    def test_an_edit_of_a_joined_layer_reads_again(
        self, world, cache, tmp_path
    ) -> None:
        """The joined table is in no frame, yet its values are in the page."""
        from qgis.core import QgsFeature, QgsVectorLayer, QgsVectorLayerJoinInfo

        memory = QgsVectorLayer(
            "None?field=name:string&field=owner:string", "owners", "memory"
        )
        row = QgsFeature(memory.fields())
        row.setAttributes(["site 0", "Ana"])
        memory.dataProvider().addFeatures([row])
        table = QgsVectorLayer(_gpkg(tmp_path, memory, "owners"), "owners", "ogr")
        assert table.isValid()
        world.project.addMapLayer(table)
        join = QgsVectorLayerJoinInfo()
        join.setJoinLayer(table)
        join.setJoinFieldName("name")
        join.setTargetFieldName("name")
        join.setUsingMemoryCache(False)
        assert world.sites.addJoin(join)
        first = _build(world, cache)
        table.startEditing()
        table.changeAttributeValue(
            next(table.getFeatures()).id(), table.fields().indexOf("owner"), "Bo"
        )
        again = _build(world, cache)
        assert cache.read_count == 2
        assert again[2] != first[2]
        _same_as_fresh(world, again)

    def test_a_changed_virtual_field_reads_again(self, world, cache) -> None:
        """`updateExpressionField` emits nothing at all; only the layer's saved
        XML shows the new expression."""
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        layer = world.sites
        layer.addExpressionField('"pop" * 2', QgsField("twice", QMetaType.Type.Int))
        first = _build(world, cache)
        layer.updateExpressionField(layer.fields().indexOf("twice"), '"pop" * 3')
        again = _build(world, cache)
        assert cache.read_count == 2
        assert again[2] != first[2]
        _same_as_fresh(world, again)

    def test_undoing_an_edit_reads_again(self, world, cache) -> None:
        _build(world, cache)
        _edit_an_attribute(world)
        _build(world, cache)
        world.sites.rollBack()
        again = _build(world, cache)
        assert cache.read_count == 3
        _same_as_fresh(world, again)


def _rule_based(world: World) -> None:
    """Draw the sites by rules on "pop", a rule-based layer's generated class."""
    from qgis.core import QgsMarkerSymbol, QgsRuleBasedRenderer

    root = QgsRuleBasedRenderer.Rule(None)
    for label, expression, colour in (
        ("Small", '"pop" < 25', "#ff0000"),
        ("Large", "ELSE", "#0000ff"),
    ):
        symbol = QgsMarkerSymbol.createSimple({"color": colour})
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression, label))
    world.sites.setRenderer(QgsRuleBasedRenderer(root))


class TestARuleBasedLayer:
    """Its rules are matched as the features are read, from a clone of the
    renderer the read carries: the key holds that renderer, so the read is
    reused while it is unchanged and read again when a rule changes."""

    def test_a_note_edit_reuses_the_read(self, world, cache) -> None:
        _rule_based(world)
        first = _build(world, cache)
        world.note.setText("Another note")
        again = _build(world, cache)
        assert cache.read_count == 1
        assert again[0] != first[0]
        _same_as_fresh(world, again)

    def test_a_changed_rule_reads_again(self, world, cache) -> None:
        _rule_based(world)
        first = _build(world, cache)
        world.sites.renderer().rootRule().children()[0].setFilterExpression('"pop" < 5')
        again = _build(world, cache)
        assert cache.read_count == 2
        assert again[2] != first[2]
        _same_as_fresh(world, again)


class TestWhatIsNeverReused:
    def test_a_layer_reading_the_clock_is_read_every_time(self, world, cache) -> None:
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        world.sites.addExpressionField(
            "to_string(now())", QgsField("stamp", QMetaType.Type.QString)
        )
        _build(world, cache)
        _build(world, cache)
        # Not compared with a fresh build: the clock has moved on since.
        assert cache.read_count == 0
        assert len(cache._entries) == 0

    def test_a_layer_on_a_database_or_service_is_read_every_time(self, world) -> None:
        """Its data can change with no signal and no file to look at, even
        when its source names a file."""
        from qgis.core import QgsVectorLayer

        from nika_onlymap_exporter.core.cartograph_export import _source_files

        class OnAServer(QgsVectorLayer):
            def providerType(self) -> str:  # noqa: N802 (QGIS's name)
                return "postgres"

        layer = OnAServer(world.path, "served", "ogr")
        assert _source_files(world.sites) is not None
        assert _source_files(layer) is None

    def test_a_memory_layer_is_read_every_time(self, world, cache) -> None:
        """Its provider can be written to with no signal at all."""
        from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

        scratch = QgsVectorLayer("Point?crs=EPSG:3857&field=a:integer", "s", "memory")
        point = QgsFeature(scratch.fields())
        point.setAttributes([1])
        point.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500, 500)))
        scratch.dataProvider().addFeatures([point])
        world.project.addMapLayer(scratch)
        world.map_item.setLayers([world.sites, scratch])
        _build(world, cache)
        scratch.dataProvider().changeAttributeValues(
            {next(scratch.getFeatures()).id(): {0: 2}}
        )
        again = _build(world, cache)
        assert cache.read_count == 0
        _same_as_fresh(world, again)

    def test_a_filter_that_queries_a_table_is_read_every_time(
        self, world, cache
    ) -> None:
        """The table it reads may be in another file the key does not see."""
        assert world.sites.setSubsetString(
            '"fid" IN (SELECT "fid" FROM "sites" WHERE "pop" > 15)'
        )
        _build(world, cache)
        again = _build(world, cache)
        assert cache.read_count == 0
        _same_as_fresh(world, again)

    def test_a_layer_on_a_query_of_other_layers_is_read_every_time(
        self, world, cache
    ) -> None:
        from qgis.core import QgsVectorLayer

        virtual = QgsVectorLayer(
            f"?layer_ref={world.zones.id()}&query=select * from zones", "v", "virtual"
        )
        assert virtual.isValid()
        world.project.addMapLayer(virtual)
        world.map_item.setLayers([virtual])
        _build(world, cache)
        _build(world, cache)
        assert cache.read_count == 0


class TestTheKey:
    """Each part of the key on its own: a change to only that part gives a
    different key. (The output tests above cannot single out a part that a
    second part also catches.)"""

    def _key(self, world: World, cache: LiveFrameCache, **kwargs) -> str | None:
        from nika_onlymap_exporter.core.project_reader import prepare_project

        source = kwargs.pop(
            "source",
            LiveFrameSource(
                frame_id="frame",
                layer_order=(world.sites.id(), world.zones.id()),
                extent=_extent(),
            ),
        )
        layer_settings = kwargs.pop("layer_settings", None)
        tokens = cache.revisions(world.project, source)
        pending = prepare_project(
            world.project,
            FidelityReportBuilder(),
            layer_order=source.layer_order,
            canvas_extent=source.extent,
            layer_settings=layer_settings,
            report_map_title=False,
        )
        return cache.key(world.project, source, pending, layer_settings, tokens)

    def test_the_same_state_gives_the_same_key(self, world, cache) -> None:
        assert self._key(world, cache) is not None
        assert self._key(world, cache) == self._key(world, cache)

    def test_the_layers_generation(self, world, cache) -> None:
        before = self._key(world, cache)
        world.sites.dataChanged.emit()
        assert self._key(world, cache) != before

    def test_the_layers_saved_xml(self, world, cache) -> None:
        before = self._key(world, cache)
        world.sites.setCustomProperty("q2w-test", "x")
        assert self._key(world, cache) != before

    def test_the_layers_feature_count(self, world, cache) -> None:
        before = self._key(world, cache)
        _add_through_the_provider(world)
        assert self._key(world, cache) != before

    def test_the_layers_extent(self, world, cache) -> None:
        before = self._key(world, cache)
        _move_through_the_provider(world)
        assert self._key(world, cache) != before

    def test_the_layers_file(self, world, cache) -> None:
        before = self._key(world, cache)
        _edit_the_file_elsewhere(world)
        assert self._key(world, cache) != before

    def test_the_frame(self, world, cache) -> None:
        source = LiveFrameSource(
            frame_id="frame",
            layer_order=(world.sites.id(), world.zones.id()),
            extent=_extent(),
        )
        before = self._key(world, cache, source=source)
        moved = LiveFrameSource(
            frame_id="frame",
            layer_order=source.layer_order,
            extent=source.extent,
            hidden_layer_ids=(world.zones.id(),),
        )
        assert self._key(world, cache, source=moved) != before

    def test_the_layers_tab_settings(self, world, cache) -> None:
        before = self._key(world, cache)
        settings = {world.sites.id(): LayerSettings(label=False)}
        assert self._key(world, cache, layer_settings=settings) != before

    def test_the_expression_variables(self, world, cache) -> None:
        from qgis.core import QgsExpressionContextUtils

        before = self._key(world, cache)
        QgsExpressionContextUtils.setProjectVariable(world.project, "q2w_v", "1")
        assert self._key(world, cache) != before

    def test_the_project(self, world, cache, project) -> None:
        """Another project object with the same layers never shares a key."""
        before = self._key(world, cache)
        world.project.setProperty("nika_onlymap_cache_session", "another")
        assert self._key(world, cache) != before

    def test_the_project_snapshot(self, world, cache) -> None:
        """A layer's place in the layers panel is in no layer's XML."""
        before = self._key(world, cache)
        _hide_in_the_tree(world)
        assert self._key(world, cache) != before

    def test_a_clear(self, world, cache) -> None:
        """A build that took its key before the project was cleared cannot
        leave an entry a later key matches."""
        before = self._key(world, cache)
        cache.clear()
        assert self._key(world, cache) != before


def _extent():
    from nika_onlymap_exporter.core.export_ir import Extent

    return Extent(west=-0.01, south=-0.01, east=0.02, north=0.02)


class TestMemory:
    def test_clearing_the_project_empties_every_build_cache(self, world, cache) -> None:
        from nika_onlymap_exporter.core import cartograph_export

        _build(world, cache)
        _build(world, cartograph_export._DEFAULT_LIVE_CACHE)
        cartograph_export._RASTER_EMBEDS.put("k", "k", object(), 1)
        assert len(cache._entries) == 1
        assert len(cartograph_export._DEFAULT_LIVE_CACHE._entries) == 1
        world.project.clear()
        assert len(cache._entries) == 0
        assert len(cartograph_export._DEFAULT_LIVE_CACHE._entries) == 0
        assert len(cartograph_export._RASTER_EMBEDS) == 0

    def test_a_read_over_the_budget_is_not_kept(self, world) -> None:
        cache = LiveFrameCache(max_bytes=10)
        try:
            _build(world, cache)
            again = _build(world, cache)
            assert cache.read_count == 2
            assert len(cache._entries) == 0
            _same_as_fresh(world, again)
        finally:
            cache.clear()

    def test_each_frame_keeps_one_entry_however_many_edits(self, world, cache) -> None:
        for index in range(4):
            world.sites.setName(f"sites {index}")
            _build(world, cache)
        assert cache.read_count == 4
        assert len(cache._entries) == 1


class TestAProjectIsNotKeptAlive:
    """A script's own `QgsProject()`, followed by a cache, used to live until
    the interpreter shut down - after QGIS, which crashed on the way out."""

    def test_a_followed_project_can_still_be_deleted(self, qgis_app) -> None:
        import gc
        import weakref

        from nika_onlymap_exporter.core.build_cache import ProjectFollower

        cleared: list[bool] = []
        follower = ProjectFollower(lambda: cleared.append(True))
        project = qgis_core.QgsProject()
        follower.follow(project)
        follower.follow(project)  # following twice is still one connection
        alive = weakref.ref(project)

        del project
        gc.collect()

        assert alive() is None
        # What the cache held for it is dropped with it (a project being
        # deleted is cleared on the way, so this may be heard twice).
        assert cleared
        follower.stop()

    def test_a_cleared_project_still_empties_the_cache(self, qgis_app) -> None:
        from nika_onlymap_exporter.core.build_cache import ProjectFollower

        cleared: list[bool] = []
        follower = ProjectFollower(lambda: cleared.append(True))
        project = qgis_core.QgsProject()
        follower.follow(project)
        project.clear()
        assert cleared == [True]
        follower.stop()
        project.clear()
        assert cleared == [True]  # stopped: no longer listening
