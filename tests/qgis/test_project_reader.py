"""End-to-end reading of a QGIS project into the normalized model.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import (
    FidelityStatus,
    GeometryKind,
    PopupFieldMode,
    RendererKind,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layer_reader import read_layer
from nika_onlymap_exporter.core.project_reader import (
    DEFAULT_TITLE,
    read_project,
    resolve_title,
)
from nika_onlymap_exporter.core.settings import LayerSettings

qgis_core = pytest.importorskip("qgis.core")


class TestReadLayer:
    def test_reads_a_point_layer(self, project, make_memory_layer) -> None:
        layer = make_memory_layer(
            "airports",
            features=[("NORTHWAY", [-141.9, 62.9]), ("JUNEAU", [-134.5, 58.3])],
        )
        report = FidelityReportBuilder()
        export_layer = read_layer(layer, report)

        assert export_layer is not None
        assert export_layer.name == "airports"
        assert export_layer.geometry_kind is GeometryKind.POINT
        assert export_layer.source_kind is SourceKind.MEMORY
        assert export_layer.feature_count == 2
        assert export_layer.geojson is not None

    def test_attribute_only_table_is_skipped_with_a_reason(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("lookup", geometry="None")
        report = FidelityReportBuilder()
        assert read_layer(layer, report) is None
        details = " ".join(i.detail for i in report.items)
        assert "no geometry" in details

    def test_popup_fields_default_to_labelled(self, project, make_memory_layer) -> None:
        """The incumbent defaults these to no-label, producing bare values."""
        layer = make_memory_layer("pts", features=[("a", [0.0, 0.0])])
        export_layer = read_layer(layer, FidelityReportBuilder())
        assert export_layer is not None
        assert export_layer.popup.fields[0].mode is PopupFieldMode.INLINE_WITH_DATA

    def test_reprojects_to_wgs84_and_says_so(self, project, make_memory_layer) -> None:
        layer = make_memory_layer(
            "utm", crs="EPSG:32633", features=[("a", [500000.0, 6000000.0])]
        )
        report = FidelityReportBuilder()
        export_layer = read_layer(layer, report)

        assert export_layer is not None
        coords = export_layer.geojson["features"][0]["geometry"]["coordinates"]
        # Somewhere in Norway, not the raw easting/northing.
        assert -180 <= coords[0] <= 180
        assert -90 <= coords[1] <= 90
        assert any("Reprojected" in i.detail for i in report.items)

    def test_single_symbol_renderer_is_translated(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("pts", features=[("a", [1.0, 1.0])])
        export_layer = read_layer(layer, FidelityReportBuilder())
        assert export_layer is not None
        assert export_layer.renderer.kind is RendererKind.SINGLE
        assert export_layer.renderer.symbol is not None

    def test_empty_layer_is_exported_but_flagged(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("empty")
        report = FidelityReportBuilder()
        export_layer = read_layer(layer, report)
        assert export_layer is not None
        assert export_layer.feature_count == 0
        assert any("no features" in i.detail for i in report.items)

    def test_features_of_a_switched_off_category_leave_the_data(
        self, project, make_memory_layer
    ) -> None:
        """The report said they were omitted; they were not.

        A category unchecked in QGIS was dropped from the styling expression but
        left in the GeoJSON, so its features rendered in the "other" fallback
        grey - the opposite of what the author asked for and of what the
        fidelity report claimed had happened.
        """
        layer = make_memory_layer(
            "airports",
            fields="kind:string",
            features=[("civil", [0.0, 0.0]), ("military", [1.0, 1.0])],
        )
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "kind",
                [
                    qgis_core.QgsRendererCategory(
                        "civil",
                        qgis_core.QgsMarkerSymbol.createSimple({"color": "#00ff00"}),
                        "Civil",
                    ),
                    qgis_core.QgsRendererCategory(
                        "military",
                        qgis_core.QgsMarkerSymbol.createSimple({"color": "#ff0000"}),
                        "Military",
                    ),
                ],
            )
        )
        layer.renderer().updateCategoryRenderState(1, False)

        export_layer = read_layer(layer, FidelityReportBuilder())

        assert export_layer is not None
        kinds = [f["properties"]["kind"] for f in export_layer.geojson["features"]]
        assert kinds == ["civil"]
        assert export_layer.feature_count == 1


class TestResolveTitle:
    def test_override_wins(self, project, make_memory_layer) -> None:
        project.setTitle("Project title")
        assert resolve_title(project, "Dialog name") == "Dialog name"

    def test_falls_back_to_project_title(self, project, make_memory_layer) -> None:
        project.setTitle("Project title")
        assert resolve_title(project, None) == "Project title"

    def test_falls_back_to_placeholder(self, project, make_memory_layer) -> None:
        assert resolve_title(project, None) == DEFAULT_TITLE

    def test_blank_override_is_ignored(self, project, make_memory_layer) -> None:
        project.setTitle("Project title")
        assert resolve_title(project, "   ") == "Project title"


class TestTerrainNotes:
    def test_labels_under_relief_are_reported(self, project, make_memory_layer) -> None:
        """Measured on the pinned runtime: label text never appears while
        terrain is on, draped or offset. The person exporting must hear it
        from the Fidelity tab, not from a recipient."""
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorLayerSimpleLabeling,
        )

        from nika_onlymap_exporter.core.export_ir import ExportSettings

        layer = make_memory_layer("peaks", features=[("Everest", [86.9, 27.9])])
        settings = QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        project.addMapLayer(layer)

        report = FidelityReportBuilder()
        read_project(project, report, settings=ExportSettings(terrain="terrarium"))
        details = " ".join(i.detail for i in report.items)
        assert "Labels shrink to a few pixels over relief" in details
        assert "'peaks'" in details

    def test_widget_colour_notes(self, project, make_memory_layer) -> None:
        from nika_onlymap_exporter.core.export_ir import Color, ExportSettings

        project.addMapLayer(
            make_memory_layer("peaks", features=[("Everest", [86.9, 27.9])])
        )

        def notes(**colors: Color) -> list[str]:
            report = FidelityReportBuilder()
            read_project(project, report, settings=ExportSettings(**colors))
            return [i.detail for i in report.items if i.subject == "Widget colours"]

        assert notes() == []
        assert "text colour is on Default" in notes(widget_background=Color(0, 0, 0))[0]
        assert (
            "background is on Default"
            in notes(widget_foreground=Color(255, 255, 255))[0]
        )
        pair = notes(
            widget_background=Color(255, 255, 255),
            widget_foreground=Color(200, 200, 200),
        )
        assert len(pair) == 1 and "below the 4.5:1" in pair[0]
        assert (
            notes(
                widget_background=Color(0, 0, 0), widget_foreground=Color(255, 255, 255)
            )
            == []
        )

    def test_no_label_note_without_terrain(self, project, make_memory_layer) -> None:
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorLayerSimpleLabeling,
        )

        layer = make_memory_layer("peaks", features=[("Everest", [86.9, 27.9])])
        settings = QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        project.addMapLayer(layer)

        report = FidelityReportBuilder()
        read_project(project, report)
        assert "Labels shrink to a few pixels over relief" not in " ".join(
            i.detail for i in report.items
        )

    def test_polygon_outlines_under_relief_are_reported(
        self, project, make_memory_layer
    ) -> None:
        """Measured on the pinned runtime: the drape keeps polygon fills but
        drops their outlines, so overlapping translucent areas lose the
        border that tells them apart."""
        from qgis.core import QgsFillSymbol

        from nika_onlymap_exporter.core.export_ir import ExportSettings

        layer = make_memory_layer(
            "zones", geometry="Polygon", features=[("a", [0, 0, 0, 1, 1, 1, 1, 0])]
        )
        layer.renderer().setSymbol(
            QgsFillSymbol.createSimple(
                {"color": "#ff0000", "outline_color": "#ffffff", "outline_width": "0.4"}
            )
        )
        project.addMapLayer(layer)

        report = FidelityReportBuilder()
        read_project(project, report, settings=ExportSettings(terrain="terrarium"))
        details = " ".join(i.detail for i in report.items)
        assert "Polygon outlines do not draw over relief" in details
        assert "'zones'" in details

    def test_no_outline_note_without_terrain(self, project, make_memory_layer) -> None:
        from qgis.core import QgsFillSymbol

        layer = make_memory_layer(
            "zones", geometry="Polygon", features=[("a", [0, 0, 0, 1, 1, 1, 1, 0])]
        )
        layer.renderer().setSymbol(
            QgsFillSymbol.createSimple(
                {"color": "#ff0000", "outline_color": "#ffffff", "outline_width": "0.4"}
            )
        )
        project.addMapLayer(layer)

        report = FidelityReportBuilder()
        read_project(project, report)
        assert "Polygon outlines do not draw over relief" not in " ".join(
            i.detail for i in report.items
        )


class TestReadProject:
    def test_layers_come_out_in_draw_order_bottom_first(
        self, project, make_memory_layer
    ) -> None:
        """QGIS lists top-first; the document must be bottom-first."""
        bottom = make_memory_layer("bottom", features=[("a", [0.0, 0.0])])
        top = make_memory_layer("top", features=[("b", [1.0, 1.0])])
        project.addMapLayer(bottom)
        project.addMapLayer(top)

        # Newest addition sits at the top of the QGIS tree.
        result = read_project(project, FidelityReportBuilder())
        assert [layer.name for layer in result.layers] == ["bottom", "top"]

    def test_custom_layer_order_sets_the_draw_order(
        self, project, make_memory_layer
    ) -> None:
        """ "Control rendering order" overrides the panel; QGIS is the oracle."""
        a = make_memory_layer("a", features=[("x", [0.0, 0.0])])
        b = make_memory_layer("b", features=[("x", [1.0, 1.0])])
        c = make_memory_layer("c", features=[("x", [2.0, 2.0])])
        for layer in (a, b, c):
            project.addMapLayer(layer)
        root = project.layerTreeRoot()
        root.setHasCustomLayerOrder(True)
        root.setCustomLayerOrder([b, c, a])  # top-first

        expected = [layer.name() for layer in reversed(root.layerOrder())]
        assert expected == ["a", "c", "b"]
        result = read_project(project, FidelityReportBuilder())
        assert [layer.name for layer in result.layers] == expected

    def test_custom_order_keeps_group_and_visibility(
        self, project, make_memory_layer
    ) -> None:
        a = make_memory_layer("a", features=[("x", [0.0, 0.0])])
        b = make_memory_layer("b", features=[("x", [1.0, 1.0])])
        project.addMapLayer(a, False)
        project.addMapLayer(b, False)
        root = project.layerTreeRoot()
        group = root.addGroup("Roads")
        group.addLayer(a)
        root.addLayer(b)
        root.findLayer(a.id()).setItemVisibilityChecked(False)
        root.setHasCustomLayerOrder(True)
        root.setCustomLayerOrder([a, b])

        result = read_project(project, FidelityReportBuilder())
        assert [layer.name for layer in result.layers] == ["b", "a"]
        by_name = {layer.name: layer for layer in result.layers}
        assert by_name["a"].group_path == ("Roads",)
        assert by_name["a"].visible is False
        assert by_name["b"].visible is True

    def test_wide_polygon_does_not_open_on_the_dateline(
        self, project, make_memory_layer
    ) -> None:
        from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326&field=name:string", "wide", "memory"
        )
        feature = QgsFeature(layer.fields())
        feature.setAttributes(["w"])
        feature.setGeometry(
            QgsGeometry.fromWkt("POLYGON((-100 -10,100 -10,100 10,-100 10,-100 -10))")
        )
        layer.dataProvider().addFeatures([feature])
        project.addMapLayer(layer)

        result = read_project(project, FidelityReportBuilder())
        assert result.extent is not None
        assert result.extent.crosses_antimeridian is False
        assert (result.extent.west, result.extent.east) == (-100.0, 100.0)

    def test_extent_is_antimeridian_aware(self, project, make_memory_layer) -> None:
        """The finding that motivated `extent_math` — end to end."""
        layer = make_memory_layer(
            "alaska_like",
            features=[
                ("west", [179.5, 52.0]),
                ("east", [-179.0, 71.0]),
                ("mainland", [-150.0, 61.0]),
            ],
        )
        project.addMapLayer(layer)
        result = read_project(project, FidelityReportBuilder())

        assert result.extent is not None
        assert result.extent.crosses_antimeridian is True
        # A naive bounding box would report ~358 degrees.
        assert result.extent.width_degrees < 40.0

    def test_empty_project_is_blocked_not_silently_empty(
        self, project, make_memory_layer
    ) -> None:
        report = FidelityReportBuilder()
        result = read_project(project, report)
        assert result.is_exportable is False
        assert report.has_blockers

    def test_group_membership_is_recorded(self, project, make_memory_layer) -> None:
        layer = make_memory_layer("in_group", features=[("a", [0.0, 0.0])])
        project.addMapLayer(layer, False)
        group = project.layerTreeRoot().addGroup("Basemaps")
        group.addLayer(layer)

        result = read_project(project, FidelityReportBuilder())
        assert result.layers[0].group_path == ("Basemaps",)

    def test_selection_limits_the_export(self, project, make_memory_layer) -> None:
        keep = make_memory_layer("keep", features=[("a", [0.0, 0.0])])
        drop = make_memory_layer("drop", features=[("b", [1.0, 1.0])])
        project.addMapLayer(keep)
        project.addMapLayer(drop)

        result = read_project(
            project, FidelityReportBuilder(), selected_layer_ids=frozenset({keep.id()})
        )
        assert [layer.name for layer in result.layers] == ["keep"]

    def test_snapshot_is_deterministic(self, project, make_memory_layer) -> None:
        """Task 2's acceptance criterion."""
        layer = make_memory_layer("pts", features=[("a", [1.0, 2.0])])
        project.addMapLayer(layer)

        first = read_project(project, FidelityReportBuilder()).snapshot()
        second = read_project(project, FidelityReportBuilder()).snapshot()
        assert first == second

    def test_fidelity_covers_every_layer(self, project, make_memory_layer) -> None:
        """Nothing may pass through unreported."""
        for index in range(3):
            project.addMapLayer(
                make_memory_layer(f"layer{index}", features=[("a", [0.0, 0.0])])
            )

        report = FidelityReportBuilder()
        result = read_project(project, report)

        for layer in result.layers:
            assert report.for_layer(layer.layer_id), (
                f"{layer.name} produced no fidelity entries"
            )

    def test_title_override_notes_the_unused_project_title(
        self, project, make_memory_layer
    ) -> None:
        project.setTitle("Original")
        project.addMapLayer(make_memory_layer("pts", features=[("a", [0.0, 0.0])]))

        report = FidelityReportBuilder()
        read_project(project, report, title_override="Renamed")

        approximated = report.by_status(FidelityStatus.APPROXIMATED)
        assert any("Original" in i.detail for i in approximated)

    def test_a_raster_only_project_opens_on_the_raster(
        self, project, make_raster_layer
    ) -> None:
        """The failure this fixes: the extent came only from `geojson`, so a
        project whose single layer is a raster computed nothing and the map
        opened at world view with the raster somewhere off screen."""
        project.addMapLayer(make_raster_layer(west=4.0, north=52.0, pixel_size=0.25))

        result = read_project(project, FidelityReportBuilder())

        assert result.extent is not None
        assert result.extent.west == pytest.approx(4.0)
        assert result.extent.east == pytest.approx(5.0)
        assert result.extent.north == pytest.approx(52.0)
        assert result.extent.south == pytest.approx(51.0)

    def test_a_raster_widens_the_extent_of_a_mixed_project(
        self, project, make_memory_layer, make_raster_layer
    ) -> None:
        """Both kinds of layer are on the map, so both frame it."""
        project.addMapLayer(make_memory_layer("pts", features=[("a", [10.0, 45.0])]))
        project.addMapLayer(make_raster_layer(west=4.0, north=52.0, pixel_size=0.25))

        result = read_project(project, FidelityReportBuilder())

        assert result.extent is not None
        assert result.extent.west == pytest.approx(4.0)
        assert result.extent.east == pytest.approx(10.0)
        assert result.extent.south == pytest.approx(45.0)
        assert result.extent.north == pytest.approx(52.0)


class TestLayerOrder:
    """`layer_order`, for the cartograph's live frames.

    It takes layer ids top-to-bottom, as `QgsLayoutItemMap.layersToRender()`
    returns them - the opposite direction from the layer tree's own top-first
    listing - and both orders and selects the export.
    """

    def test_order_follows_layer_order_not_the_tree(
        self, project, make_memory_layer
    ) -> None:
        a = make_memory_layer("a", features=[("a", [0.0, 0.0])])
        b = make_memory_layer("b", features=[("b", [1.0, 1.0])])
        c = make_memory_layer("c", features=[("c", [2.0, 2.0])])
        # Added in tree order a, b, c (c ends up topmost in the QGIS panel);
        # the layout wants the reverse: c on top, then a, then b.
        project.addMapLayer(a)
        project.addMapLayer(b)
        project.addMapLayer(c)

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_order=[c.id(), a.id(), b.id()],
        )

        # layer_order is top-to-bottom; the document is bottom-first, so the
        # last-named id (b) is the first one drawn.
        assert [layer.name for layer in result.layers] == ["b", "a", "c"]

    def test_layer_order_implies_selection(self, project, make_memory_layer) -> None:
        keep = make_memory_layer("keep", features=[("a", [0.0, 0.0])])
        drop = make_memory_layer("drop", features=[("b", [1.0, 1.0])])
        project.addMapLayer(keep)
        project.addMapLayer(drop)

        result = read_project(project, FidelityReportBuilder(), layer_order=[keep.id()])

        assert [layer.name for layer in result.layers] == ["keep"]

    def test_an_id_no_longer_in_the_project_is_skipped(
        self, project, make_memory_layer
    ) -> None:
        """A layout can outlive the layer it once pointed at."""
        keep = make_memory_layer("keep", features=[("a", [0.0, 0.0])])
        project.addMapLayer(keep)

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_order=[keep.id(), "no-such-layer-id"],
        )

        assert [layer.name for layer in result.layers] == ["keep"]

    def test_none_leaves_behaviour_unchanged(self, project, make_memory_layer) -> None:
        """The default: today's tree order, today's selection rules."""
        bottom = make_memory_layer("bottom", features=[("a", [0.0, 0.0])])
        top = make_memory_layer("top", features=[("b", [1.0, 1.0])])
        project.addMapLayer(bottom)
        project.addMapLayer(top)

        with_default = read_project(project, FidelityReportBuilder())
        explicit_none = read_project(project, FidelityReportBuilder(), layer_order=None)

        assert [layer.name for layer in with_default.layers] == ["bottom", "top"]
        assert with_default.snapshot() == explicit_none.snapshot()


class TestPerLayerToggles:
    """The dialog's per-layer Popup and Label checkboxes.

    Both were persisted to the project and read back into the dialog, and then
    consulted by nothing: `read_project` never received them, so unticking
    either changed the saved settings and not the exported map. `include` was
    wired (via `selected_layer_ids`); these two were not.
    """

    def labelled_layer(self, make_memory_layer):
        layer = make_memory_layer("towns", features=[("Ashford", [1.0, 2.0])])
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        return layer

    def test_defaults_keep_popups_and_labels(self, project, make_memory_layer) -> None:
        project.addMapLayer(self.labelled_layer(make_memory_layer))

        result = read_project(project, FidelityReportBuilder())

        (layer,) = result.layers
        assert layer.popup.enabled
        assert layer.labeling.enabled

    def test_unticking_popup_drops_it(self, project, make_memory_layer) -> None:
        layer = self.labelled_layer(make_memory_layer)
        project.addMapLayer(layer)

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={layer.id(): LayerSettings(popup=False)},
        )

        assert not result.layers[0].popup.enabled
        assert result.layers[0].labeling.enabled, "labels are a separate choice"

    def test_a_chosen_field_mode_reaches_the_exported_markup(
        self, project, make_memory_layer
    ) -> None:
        """The whole chain in one test: dialog state to shipped popup markup.

        Every link is covered elsewhere, but nothing checked that they are
        actually joined up - which is what a user setting a mode and exporting
        depends on.
        """
        from nika_onlymap_exporter.core.export_ir import PopupFieldMode
        from nika_onlymap_exporter.core.manifest_builder import build_popup_elements

        layer = self.labelled_layer(make_memory_layer)
        project.addMapLayer(layer)

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={
                layer.id(): LayerSettings(
                    fields={"name": PopupFieldMode.HEADER_ALWAYS.value}
                )
            },
        )

        (field,) = result.layers[0].popup.visible_fields
        assert field.mode is PopupFieldMode.HEADER_ALWAYS
        markup = build_popup_elements(result.layers[0])
        assert (
            '<div class="om-popup-row om-popup-row-header om-popup-always">' in markup
        )

    def test_unticking_label_drops_it(self, project, make_memory_layer) -> None:
        layer = self.labelled_layer(make_memory_layer)
        project.addMapLayer(layer)

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={layer.id(): LayerSettings(label=False)},
        )

        assert not result.layers[0].labeling.enabled
        assert result.layers[0].popup.enabled, "popups are a separate choice"

    def test_an_unlisted_layer_keeps_the_defaults(
        self, project, make_memory_layer
    ) -> None:
        """So a caller with no dialog - Processing, a test - need not supply any."""
        project.addMapLayer(self.labelled_layer(make_memory_layer))

        result = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={"other-id": LayerSettings()},
        )

        assert result.layers[0].labeling.enabled


class TestBrokenLayers:
    """A layer QGIS could not load is named, and only when it is exported."""

    def broken(self, project):
        layer = qgis_core.QgsVectorLayer("/nonexistent/roads.shp", "roads", "ogr")
        assert not layer.isValid()
        project.addMapLayer(layer)
        return layer

    def test_the_row_names_the_layer_and_belongs_to_it(
        self, project, make_memory_layer
    ) -> None:
        """It used to say "1 layer(s) could not be loaded", leaving the user to
        find which one."""
        project.addMapLayer(make_memory_layer("ok", features=[("a", [0.0, 0.0])]))
        broken = self.broken(project)
        report = FidelityReportBuilder()
        read_project(project, report)

        blocked = report.by_status(FidelityStatus.BLOCKED)
        assert [item.subject for item in blocked] == ["Layer 'roads'"]
        assert blocked[0].layer_id == broken.id()

    def test_an_unticked_broken_layer_is_not_a_problem(
        self, project, make_memory_layer
    ) -> None:
        ok = make_memory_layer("ok", features=[("a", [0.0, 0.0])])
        project.addMapLayer(ok)
        self.broken(project)
        report = FidelityReportBuilder()
        read_project(project, report, selected_layer_ids=frozenset({ok.id()}))

        assert not report.has_blockers


class TestScaleVisibility:
    """QGIS scale ranges reach the map as a zoom range, and say so."""

    def test_a_scale_range_becomes_a_zoom_range(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer("pts", features=[("a", [0.0, 0.0])])
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(1_000_000)
        layer.setMaximumScale(1_000)
        project.addMapLayer(layer)
        project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
        report = FidelityReportBuilder()

        result = read_project(project, report)

        assert result.layers[0].visible_zoom_range is not None
        low, high = result.layers[0].visible_zoom_range
        # At the equator in EPSG:3857: zoom = log2(295,829,355 / scale).
        assert low == pytest.approx(8.21, abs=0.02)
        assert high == pytest.approx(18.17, abs=0.02)
        rows = [i for i in report.items if i.subject == "Scale visibility of 'pts'"]
        assert [row.status for row in rows] == [FidelityStatus.PRESERVED]
        assert rows[0].layer_id == layer.id()
        assert "every zoom" not in rows[0].detail

    def test_no_scale_range_means_no_zoom_range(
        self, project, make_memory_layer
    ) -> None:
        project.addMapLayer(make_memory_layer("pts", features=[("a", [0.0, 0.0])]))
        result = read_project(project, FidelityReportBuilder())
        assert result.layers[0].visible_zoom_range is None


class TestDeclutter:
    """Dense points cluster and crowded labels wait, and the report says so."""

    def test_a_dense_labelled_point_layer_clusters_and_holds_its_labels(
        self, project, make_memory_layer
    ) -> None:
        layer = make_memory_layer(
            "towns",
            features=[(f"Town {i}", [i * 0.001, 0.0]) for i in range(300)],
        )
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        project.addMapLayer(layer)
        report = FidelityReportBuilder()

        result = read_project(project, report)

        read = result.layers[0]
        assert read.cluster_max_zoom is not None
        assert read.label_min_zoom is not None
        assert read.label_min_zoom > read.cluster_max_zoom
        subjects = {i.subject: i for i in report.items}
        assert subjects["Markers of 'towns'"].status is FidelityStatus.APPROXIMATED
        assert subjects["Labels on 'towns'"].layer_id == layer.id()

    def test_labels_with_no_room_inside_the_layers_range_count_as_off(
        self, project, make_memory_layer
    ) -> None:
        """Left out of the map, so left out everywhere: switcher, facts and the
        terrain note must not treat the layer as labelled."""
        layer = make_memory_layer(
            "towns",
            features=[(f"Town {i}", [i * 0.001, 0.0]) for i in range(50)],
        )
        settings = qgis_core.QgsPalLayerSettings()
        settings.fieldName = "name"
        layer.setLabeling(qgis_core.QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        # Hidden once zoomed in past about 1:1,000,000 - long before 50 labels
        # 110 m apart have room.
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(0)
        layer.setMaximumScale(1_000_000)
        project.addMapLayer(layer)
        report = FidelityReportBuilder()

        read = read_project(project, report).layers[0]

        assert read.labeling.enabled is False
        assert read.label_min_zoom is None
        rows = [i for i in report.items if i.subject == "Labels on 'towns'"]
        assert [row.status for row in rows] == [FidelityStatus.UNSUPPORTED]

    def _towns(self, project, make_memory_layer):
        layer = make_memory_layer(
            "towns",
            features=[(f"Town {i}", [i * 0.001, 0.0]) for i in range(300)],
        )
        project.addMapLayer(layer)
        return layer

    def test_clustering_can_be_switched_off_per_layer(
        self, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import LayerSettings

        layer = self._towns(project, make_memory_layer)
        read = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={layer.id(): LayerSettings(cluster=False)},
        ).layers[0]
        assert read.cluster_max_zoom is None
        assert read_project(project, FidelityReportBuilder()).layers[0].cluster_max_zoom

    def test_names_show_beside_every_point_with_a_halo(
        self, project, make_memory_layer
    ) -> None:
        """The point names without clicking for a popup."""
        from nika_onlymap_exporter.core.settings import LayerSettings

        layer = self._towns(project, make_memory_layer)
        report = FidelityReportBuilder()
        read = read_project(
            project,
            report,
            layer_settings={layer.id(): LayerSettings(name_field="name")},
        ).layers[0]
        assert read.labeling.enabled
        assert read.labeling.field_name == "name"
        assert read.labeling.halo_color is not None
        assert read.labeling.halo_width > 0
        subjects = {i.subject for i in report.items}
        assert "Names on 'towns'" in subjects

    def test_names_ignore_a_field_the_layer_does_not_have(
        self, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.settings import LayerSettings

        layer = self._towns(project, make_memory_layer)
        read = read_project(
            project,
            FidelityReportBuilder(),
            layer_settings={layer.id(): LayerSettings(name_field="gone")},
        ).layers[0]
        assert not read.labeling.enabled

    def test_a_handful_of_points_is_left_alone(
        self, project, make_memory_layer
    ) -> None:
        project.addMapLayer(make_memory_layer("pts", features=[("a", [0.0, 0.0])]))
        result = read_project(project, FidelityReportBuilder())
        assert result.layers[0].cluster_max_zoom is None
        assert result.layers[0].label_min_zoom is None


class TestQgisScaleFactor:
    """What a QGIS scale readout means, measured by QGIS for the project CRS."""

    def factor(self, project, crs: str, longitude: float, latitude: float) -> float:
        from nika_onlymap_exporter.core.project_reader import (
            ScaleBasis,
            _qgis_scale_factor,
        )

        project.setCrs(qgis_core.QgsCoordinateReferenceSystem(crs))
        return _qgis_scale_factor(ScaleBasis.of(project), longitude, latitude)

    def test_web_mercator_reads_one_over_cos_latitude(self, project) -> None:
        assert self.factor(project, "EPSG:3857", 15.0, 60.0) == pytest.approx(
            2.0, rel=0.01
        )

    def test_a_metre_projection_reads_true_scale(self, project) -> None:
        # UTM 33N's central meridian is 15E, where its scale factor is 0.9996.
        assert self.factor(project, "EPSG:32633", 15.0, 60.0) == pytest.approx(
            1.0, abs=0.01
        )

    def test_an_invalid_crs_falls_back_to_true_scale(self, project) -> None:
        from nika_onlymap_exporter.core.project_reader import (
            ScaleBasis,
            _qgis_scale_factor,
        )

        project.setCrs(qgis_core.QgsCoordinateReferenceSystem())
        assert _qgis_scale_factor(ScaleBasis.of(project), 0.0, 0.0) == 1.0


def test_an_exported_tile_layer_is_not_reported_as_a_missing_basemap(
    qgis_app, project
) -> None:
    """The backdrop travels as its own layer now, so nothing is missing."""
    from urllib.parse import quote

    source = "type=xyz&url=" + quote(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png", safe=":/"
    )
    project.addMapLayer(qgis_core.QgsRasterLayer(source, "OSM", "wms"))
    report = FidelityReportBuilder()
    export = read_project(project, report)
    assert any(layer.tiles for layer in export.layers)
    assert not any(item.subject == "Basemap" for item in report.items)


class TestPrepareThenFinish:
    """The split an export runs on: the project is read on the GUI thread, the
    features on a worker that holds neither the project nor its layers."""

    def test_a_worker_finishes_a_prepared_project_after_the_project_is_cleared(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        import threading

        from nika_onlymap_exporter.core.project_reader import (
            finish_project,
            prepare_project,
        )

        project.setTitle("Split read")
        project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
        project.addMapLayer(
            make_memory_layer("roads", features=[("a", [1.0, 2.0]), ("b", [3.0, 4.0])])
        )
        project.addMapLayer(make_memory_layer("towns", features=[("c", [5.0, 6.0])]))
        expected = read_project(project, FidelityReportBuilder())

        report = FidelityReportBuilder()
        pending = prepare_project(project, report)
        project.removeAllMapLayers()
        project.clear()

        done: list = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                done.append(finish_project(pending, report))
            except BaseException as exc:  # reported on the test thread
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(60)

        assert not errors, errors
        (export,) = done
        assert (
            export.snapshot(include_geometry=True)["layers"]
            == (expected.snapshot(include_geometry=True)["layers"])
        )
        assert export.title == "Split read"
        assert export.source_crs == "EPSG:3857"
        # Same findings; only the order within the report moved, because a
        # layer's data notes now follow every layer's translation notes.
        assert sorted(map(str, export.fidelity)) == sorted(map(str, expected.fidelity))

    def test_progress_names_each_layer_as_the_worker_reaches_it(
        self, qgis_app, project, make_memory_layer
    ) -> None:
        from nika_onlymap_exporter.core.project_reader import (
            finish_project,
            prepare_project,
        )

        for name in ("one", "two", "three"):
            project.addMapLayer(make_memory_layer(name, features=[("a", [0.0, 0.0])]))
        report = FidelityReportBuilder()
        seen: list[tuple[int, int, str]] = []
        finish_project(
            prepare_project(project, report),
            report,
            lambda done, total, name: seen.append((done, total, name)),
        )
        assert [(done, total) for done, total, _ in seen] == [(0, 3), (1, 3), (2, 3)]
        assert sorted(name for _, _, name in seen) == ["one", "three", "two"]
