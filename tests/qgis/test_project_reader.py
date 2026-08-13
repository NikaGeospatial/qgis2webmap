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
        assert "Labels do not appear over relief" in details
        assert "'peaks'" in details

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
        assert "Labels do not appear over relief" not in " ".join(
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
