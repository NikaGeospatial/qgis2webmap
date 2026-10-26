"""The locator inset: drawn from the data on hand, quiet, and outlining the
main map -- and the undoable helpers that edit one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer

from nika_onlymap_exporter.core.canvas_cartograph import (
    cartograph_from_view,
    template_preview,
)
from nika_onlymap_exporter.core.locator_context import (
    MAX_ZOOM_OUT,
    MIN_CONTEXT_RATIO,
    default_context_layers,
    fit_locator_to_context,
    has_wider_context,
    omit_locator,
    set_locator_context_layers,
)

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import (  # noqa: E402
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemMap,
    QgsPalLayerSettings,
    QgsRectangle,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)


def _region(project, width: float, name: str = "region") -> QgsVectorLayer:
    """A polygon layer `width` m wide round the parcels (UTM 48N)."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:32648", name, "memory")
    feature = QgsFeature()
    centre_x, centre_y = 365600, 145300
    feature.setGeometry(
        QgsGeometry.fromRect(
            QgsRectangle(
                centre_x - width / 2,
                centre_y - width / 2,
                centre_x + width / 2,
                centre_y + width / 2,
            )
        )
    )
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    project.addMapLayer(layer)
    return layer


def _feature(wkt: str) -> QgsFeature:
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    return feature


def _points(project) -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:32648&field=name:string", "places", "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromWkt("POINT(365600 145300)"))
    feature["name"] = "Town"
    layer.dataProvider().addFeatures([feature])
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    project.addMapLayer(layer)
    return layer


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _build(project, layers, template_id="locator"):
    # The view is always the parcels' (the layer named "parcels").
    parcels = next(layer for layer in layers if layer.name() == "parcels")
    return cartograph_from_view(
        _grow(parcels.extent()),
        UTM,
        layers,
        project,
        template_id=template_id,
        page_size="A4",
        orientation="landscape",
    )


def _grow(extent: QgsRectangle) -> QgsRectangle:
    grown = QgsRectangle(extent)
    grown.scale(1.1)
    return grown


def _ratio(result) -> float:
    inset = result.layout.itemById("locator").extent()
    main = result.layout.itemById("main").extent()
    return max(inset.width() / main.width(), inset.height() / main.height())


class TestTheViewComesFromTheContext:
    def test_wider_layers_set_how_much_the_inset_shows(self, project, parcels) -> None:
        region = _region(project, 3000)
        result = _build(project, [parcels, region])
        inset = result.layout.itemById("locator")
        main = result.layout.itemById("main")
        # Not the old fixed 8x: the region's own reach, with a margin.
        assert MIN_CONTEXT_RATIO < _ratio(result) < MAX_ZOOM_OUT
        assert inset.extent().contains(region.extent())
        assert inset.extent().contains(main.extent())
        assert has_wider_context(inset) is True

    def test_a_different_region_gives_a_different_view(self, project, parcels) -> None:
        near = _build(project, [parcels, _region(project, 2500, "near")])
        far = _build(project, [parcels, _region(project, 5000, "far")])
        assert _ratio(far) > _ratio(near) * 1.3

    def test_a_vast_region_is_held_to_the_most_an_inset_shows(
        self, project, parcels
    ) -> None:
        result = _build(project, [parcels, _region(project, 900_000)])
        main = result.layout.itemById("main").extent()
        inset = result.layout.itemById("locator").extent()
        assert inset.width() <= MAX_ZOOM_OUT * main.width() * 1.001
        assert inset.contains(main)

    def test_no_wider_context_is_marked_and_still_outlines(
        self, project, parcels
    ) -> None:
        result = _build(project, [parcels])
        inset = result.layout.itemById("locator")
        assert has_wider_context(inset) is False
        assert len(inset.overviews().asList()) == 1
        assert inset.overviews().asList()[0].linkedMap() is result.layout.itemById(
            "main"
        )

    def test_the_main_map_is_never_changed_by_the_inset(self, project, parcels) -> None:
        plain = _build(project, [parcels])
        wide = _build(project, [parcels, _region(project, 5000)])
        a = plain.layout.itemById("main").extent()
        b = wide.layout.itemById("main").extent()
        assert a.width() == pytest.approx(b.width())


class TestWhatTheInsetDraws:
    def test_points_stay_on_the_main_map_only(self, project, parcels) -> None:
        places = _points(project)
        region = _region(project, 3000)
        assert default_context_layers([places, region, parcels]) == [region, parcels]
        result = _build(project, [places, region, parcels])
        inset = result.layout.itemById("locator")
        assert places not in inset.layersToRender()
        assert places in result.layout.itemById("main").layersToRender()

    def test_a_busy_network_does_not_bury_the_outline(self, project, parcels) -> None:
        roads = QgsVectorLayer("LineString?crs=EPSG:32648", "roads", "memory")
        roads.dataProvider().addFeatures(
            [_feature("LINESTRING(360000 140000, 372000 151000)")]
        )
        region = _region(project, 3000)
        assert default_context_layers([roads, region, parcels]) == [region, parcels]
        result = _build(project, [roads, region, parcels])
        assert roads not in result.layout.itemById("locator").layersToRender()
        assert roads in result.layout.itemById("main").layersToRender()

    def test_a_page_of_only_lines_still_has_an_inset_of_them(self, project) -> None:
        roads = QgsVectorLayer("LineString?crs=EPSG:32648", "roads", "memory")
        assert default_context_layers([roads]) == [roads]

    def test_a_page_of_only_points_still_has_an_inset_of_them(self, project) -> None:
        places = _points(project)
        assert default_context_layers([places]) == [places]

    def test_labels_are_off_in_the_inset_and_nowhere_else(
        self, project, parcels
    ) -> None:
        labelled = _region(project, 3000, "labelled")
        settings = QgsPalLayerSettings()
        settings.fieldName = "'x'"
        settings.isExpression = True
        labelled.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        labelled.setLabelsEnabled(True)
        result = _build(project, [parcels, labelled])
        inset = result.layout.itemById("locator")
        main = result.layout.itemById("main")
        assert 'labelsEnabled="0"' in inset.layerStyleOverrides()[labelled.id()]
        # The layer's own style, and the main map, keep their labels.
        assert labelled.labelsEnabled()
        assert not main.keepLayerStyles() or labelled.id() not in (
            main.layerStyleOverrides()
        )

    def test_nothing_remote_is_added(self, project, parcels) -> None:
        result = _build(project, [parcels, _region(project, 3000)])
        for layer in result.layout.itemById("locator").layersToRender():
            assert layer.providerType() in ("memory", "gdal", "ogr")

    def test_the_picker_preview_draws(self, project, parcels) -> None:
        image = template_preview(
            _grow(parcels.extent()),
            UTM,
            [parcels, _region(project, 3000)],
            project,
            template_id="locator",
            width_px=300,
        )
        assert image.width() == 300

    def test_every_design_with_a_locator_builds_one(self, project, parcels) -> None:
        from nika_onlymap_exporter.core.layout_templates import template_ids

        built = 0
        for template_id in template_ids():
            result = _build(project, [parcels], template_id)
            if result.layout.itemById("locator") is None:
                continue
            built += 1
            inset = result.layout.itemById("locator")
            assert has_wider_context(inset) is False
            assert inset.overviews().asList()
        assert built >= 3


class TestEditingAnInset:
    def _inset(self, project, parcels):
        region = _region(project, 3000)
        result = _build(project, [parcels, region])
        return result, result.layout.itemById("locator"), region

    def test_fitting_to_context_is_one_undo_step(self, project, parcels) -> None:
        result, inset, _region_layer = self._inset(project, parcels)
        bigger = _region(project, 6000, "bigger")
        project.addMapLayer(bigger)
        before = QgsRectangle(inset.extent())
        stack = result.layout.undoStack().stack()
        index = stack.index()
        view = fit_locator_to_context(inset, [bigger])
        assert view is not None and view.has_context
        assert stack.index() == index + 1
        assert inset.extent().width() > before.width() * 1.3
        stack.undo()
        assert inset.extent().width() == pytest.approx(before.width())
        assert stack.index() == index

    def test_choosing_the_layers_is_undoable(self, project, parcels) -> None:
        result, inset, region = self._inset(project, parcels)
        stack = result.layout.undoStack().stack()
        index = stack.index()
        set_locator_context_layers(inset, [region])
        assert [layer.id() for layer in inset.layersToRender()] == [region.id()]
        stack.undo()
        assert region.id() in [layer.id() for layer in inset.layersToRender()]
        assert len(inset.layersToRender()) == 2
        assert stack.index() == index

    def test_the_inset_can_be_left_out_and_brought_back(self, project, parcels) -> None:
        result, inset, _region_layer = self._inset(project, parcels)
        stack = result.layout.undoStack().stack()
        omit_locator(inset)
        assert result.layout.itemById("locator") is None
        stack.undo()
        restored = result.layout.itemById("locator")
        assert isinstance(restored, QgsLayoutItemMap)


class TestSayingSo:
    def test_checks_explains_an_inset_with_no_wider_context(
        self, project, parcels
    ) -> None:
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.layout_reader import read_layout

        result = _build(project, [parcels])
        report = FidelityReportBuilder()
        read_layout(result.layout, report)
        notes = [i for i in report.items if "no wider context" in i.detail]
        assert len(notes) == 1 and "delete it" in notes[0].detail

    def test_checks_is_quiet_when_the_context_is_there(self, project, parcels) -> None:
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.layout_reader import read_layout

        result = _build(project, [parcels, _region(project, 3000)])
        report = FidelityReportBuilder()
        read_layout(result.layout, report)
        assert not [i for i in report.items if "no wider context" in i.detail]

    def test_the_picker_preview_carries_the_flag(self, project, parcels) -> None:
        from nika_onlymap_exporter.core.canvas_cartograph import (
            PREVIEW_LOCATOR_NO_CONTEXT_KEY,
        )

        def flag(layers):
            return template_preview(
                _grow(parcels.extent()),
                UTM,
                layers,
                project,
                template_id="locator",
                width_px=200,
            ).text(PREVIEW_LOCATOR_NO_CONTEXT_KEY)

        assert flag([parcels]) == "1"
        assert flag([parcels, _region(project, 3000)]) == "0"
