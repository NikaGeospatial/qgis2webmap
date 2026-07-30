"""End-to-end reading of a QGIS project into the normalized model.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

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
