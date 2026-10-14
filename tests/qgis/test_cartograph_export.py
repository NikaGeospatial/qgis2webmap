"""`core/cartograph_export.py`: the one service function the dialog and the
Processing algorithm both call.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from cartograph_fixtures import build_survey_layout

from nika_onlymap_exporter.core.cartograph_export import export_layout_to_cartograph
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def survey(project, tmp_path):
    return build_survey_layout(project, tmp_path)


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


class TestExportLayoutToCartograph:
    def test_raises_a_clear_error_without_cartograph_dist_files(self, survey) -> None:
        runtime = RuntimeBundle(
            javascript=b"js", css=b"css", version="0.12.0", sha256="0" * 64
        )
        report = FidelityReportBuilder()
        with pytest.raises(ValueError, match="cartograph"):
            export_layout_to_cartograph(survey.layout, survey.project, runtime, report)

    def test_produces_one_om_frame_per_map_item(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(
            survey.layout, survey.project, fake_runtime, report, static_dpi=100
        )
        assert html.count("<om-frame") == 2

    def test_static_only_layout_embeds_no_core_js(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(
            survey.layout, survey.project, fake_runtime, report, static_dpi=100
        )
        # Both map items default to static in a UTM project, so the 8.6 MB
        # live-map core must never be read, let alone embedded. The runtime
        # is embedded gzip+base64 (writers/cartograph_writer.py), so this
        # checks for the bootstrap's own CORE_GZ declaration rather than for
        # plaintext bytes that are never written literally either way.
        assert "const CORE_GZ" not in html
        assert "carto.setCoreLoader" not in html
        assert "const CARTO_GZ" in html

    def test_title_override(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(
            survey.layout,
            survey.project,
            fake_runtime,
            report,
            static_dpi=100,
            title_override="Custom Title",
        )
        assert 'title="Custom Title"' in html

    def test_cartograph_id_is_sanitized_from_layout_name(
        self, survey, fake_runtime
    ) -> None:
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(
            survey.layout, survey.project, fake_runtime, report, static_dpi=100
        )
        match = re.search(r'cartograph-id="([^"]+)"', html)
        assert match is not None
        assert match.group(1) == "survey-utm"

    def test_forwards_the_second_page_block(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        export_layout_to_cartograph(
            survey.layout, survey.project, fake_runtime, report, static_dpi=100
        )
        from nika_onlymap_exporter.core.export_ir import FidelityStatus

        assert any(
            item.subject == "Page count"
            for item in report.by_status(FidelityStatus.BLOCKED)
        )

    def test_live_frame_embeds_an_om_map_and_the_core_runtime(
        self, project, tmp_path
    ) -> None:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsFillSymbol,
            QgsGeometry,
            QgsLayoutItemMap,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsRectangle,
            QgsUnitTypes,
            QgsVectorLayer,
        )

        wm = QgsCoordinateReferenceSystem("EPSG:3857")
        project.setCrs(wm)
        layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "parcels", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(0, 0, 1000, 1000)))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "#eba941"}))
        project.addMapLayer(layer)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("live-only")
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(150, 100, QgsUnitTypes.LayoutMillimeters))
        map_item.setCrs(wm)
        map_item.setLayers([layer])
        extent = layer.extent()
        extent.grow(500)
        map_item.setExtent(extent)
        map_item.refresh()

        runtime = RuntimeBundle(
            javascript=b"// core js placeholder",
            css=b"/* core css placeholder */",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"// cartograph js placeholder",
            cartograph_css=b"/* cartograph css placeholder */",
        )
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(layout, project, runtime, report)

        assert html.count("<om-frame") == 1
        assert "const CORE_GZ" in html
        assert "carto.setCoreLoader" in html
