"""`core/cartograph_export.py`: the one service function the dialog and the
Processing algorithm both call.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

import pytest
from cartograph_fixtures import build_survey_layout

from nika_onlymap_exporter.core.cartograph_export import (
    export_layout_to_cartograph,
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.cartograph_ir import element_id
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


def _live_layout(project):
    """One EPSG:3857 map item over one polygon: a layout that exports a live
    frame by default."""
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
    return layout


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
        self, project, fake_runtime
    ) -> None:
        layout = _live_layout(project)
        runtime = fake_runtime
        report = FidelityReportBuilder()
        html = export_layout_to_cartograph(layout, project, runtime, report)

        assert html.count("<om-frame") == 1
        assert "const CORE_GZ" in html
        assert "carto.setCoreLoader" in html


class TestPrepareThenFinish:
    """`prepare_cartograph` does everything that reads or changes the layout,
    on the GUI thread; `finish_cartograph` reads layers only, so the dialog
    can run it on a worker thread while the layout stays on screen."""

    def test_finish_needs_nothing_from_the_layout(self, survey, fake_runtime) -> None:
        report = FidelityReportBuilder()
        prepared = prepare_cartograph(
            survey.layout, survey.project, fake_runtime, report
        )
        for item in list(survey.layout.items()):
            if isinstance(item, qgis_core.QgsLayoutItem) and not isinstance(
                item, qgis_core.QgsLayoutItemPage
            ):
                survey.layout.removeLayoutItem(item)

        html = finish_cartograph(prepared, survey.project, report)
        assert html.count("<om-frame") == 2

    def test_finish_runs_on_a_worker_thread(
        self, qgis_app, project, fake_runtime
    ) -> None:
        from qgis.PyQt.QtCore import QThread

        layout = _live_layout(project)
        report = FidelityReportBuilder()
        prepared = prepare_cartograph(layout, project, fake_runtime, report)
        result: dict[str, object] = {}

        class Worker(QThread):
            def run(self) -> None:
                try:
                    result["html"] = finish_cartograph(prepared, project, report)
                except Exception as exc:  # surfaced by the assert below
                    result["error"] = exc

        worker = Worker()
        worker.start()
        assert worker.wait(60_000)
        assert "error" not in result, result.get("error")
        assert "<om-map" in str(result["html"])

    def test_each_static_frame_renders_at_its_own_dpi(
        self, survey, fake_runtime
    ) -> None:
        from qgis.PyQt.QtGui import QImage

        from nika_onlymap_exporter.core.cartograph_ir import FrameSpec

        dpis = {survey.main_map.uuid(): 100, survey.inset_map.uuid(): 300}
        prepared = prepare_cartograph(
            survey.layout,
            survey.project,
            fake_runtime,
            FidelityReportBuilder(),
            frame_dpis=dpis,
        )
        frames = {i.id: i for i in prepared.items if isinstance(i, FrameSpec)}
        for uuid, dpi in dpis.items():
            frame = frames[element_id("frame", uuid)]
            assert frame.dpi == dpi
            image = QImage.fromData(frame.image_bytes, "PNG")
            assert image.width() == round(frame.box.w / 25.4 * dpi)


# --------------------------------------------------------------------------
# Ids: brace-free, token-safe, unique
# --------------------------------------------------------------------------

#: What an id must look like to be usable everywhere it is used: an HTML id,
#: a `#selector`, and inside `{{frame.<id>.scale}}`.
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_ID_ATTR = re.compile(r'\sid="([^"]*)"')
_REFERENCE_ATTR = re.compile(r'\s(?:for|overview-of)="([^"]*)"')


def _two_live_frames_layout(project):
    """Two EPSG:3857 maps over the same two layers, a legend on each, and a
    scale label: the layout where `<om-layer id>`s would collide."""
    from atlas_fixtures import add_map, build_parcels, build_roads
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsUnitTypes,
    )

    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    parcels, roads = build_parcels(), build_roads()
    project.addMapLayers([parcels, roads])
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("two-live")
    main = add_map(layout, [parcels, roads])
    inset = add_map(layout, [parcels, roads], x=170, w=60, h=60)
    layout.setReferenceMap(main)
    for map_item in (main, inset):
        legend = QgsLayoutItemLegend(layout)
        layout.addLayoutItem(legend)
        legend.setLinkedMap(map_item)
        legend.attemptResize(QgsLayoutSize(30, 20, QgsUnitTypes.LayoutMillimeters))
    label = QgsLayoutItemLabel(layout)
    layout.addLayoutItem(label)
    label.setText("1:[% @map_scale %]")
    label.attemptResize(QgsLayoutSize(40, 10, QgsUnitTypes.LayoutMillimeters))
    return layout, main, inset, parcels, roads


class TestIds:
    def test_every_id_in_the_survey_cartograph_is_brace_free_and_safe(
        self, survey, fake_runtime
    ) -> None:
        html = export_layout_to_cartograph(
            survey.layout, survey.project, fake_runtime, FidelityReportBuilder()
        )
        ids = _ID_ATTR.findall(html)
        assert ids
        assert [i for i in ids if not _SAFE_ID.match(i)] == []
        assert "{" not in "".join(_REFERENCE_ATTR.findall(html))

    def test_a_live_frame_scale_label_names_the_frame_by_its_id(
        self, project, fake_runtime
    ) -> None:
        layout, main, *_ = _two_live_frames_layout(project)
        html = export_layout_to_cartograph(
            layout, project, fake_runtime, FidelityReportBuilder()
        )
        frame_id = element_id("frame", main.uuid())
        assert f'<om-frame id="{frame_id}"' in html
        assert f"1:{{{{frame.{frame_id}.scale}}}}" in html
        # The runtime's token pattern, `{{\s*([\w.]+)\s*}}`, must match it.
        assert re.search(r"\{\{\s*([\w.]+)\s*\}\}", f"{{{{frame.{frame_id}.scale}}}}")

    def test_two_live_frames_on_the_same_layers_write_unique_ids(
        self, project, fake_runtime
    ) -> None:
        layout, main, inset, parcels, _roads = _two_live_frames_layout(project)
        html = export_layout_to_cartograph(
            layout, project, fake_runtime, FidelityReportBuilder()
        )
        sheet = html[html.index('<template id="sheet">') : html.index("</template>")]
        ids = _ID_ATTR.findall(sheet)
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert duplicates == []
        main_id = element_id("frame", main.uuid())
        inset_id = element_id("frame", inset.uuid())
        assert f'id="{main_id}__{parcels.id()}"' in sheet
        assert f'id="{inset_id}__{parcels.id()}"' in sheet
        # Each legend's override rows name the layer as its own frame has it.
        assert f'<om-legend-row layer="{main_id}__{parcels.id()}"' in sheet
        assert f'<om-legend-row layer="{inset_id}__{parcels.id()}"' in sheet


# --------------------------------------------------------------------------
# The writer (licence policy) and the per-frame projects
# --------------------------------------------------------------------------


class TestWriterAndProjects:
    def test_a_writer_with_a_licence_policy_is_the_one_used(
        self, project, fake_runtime
    ) -> None:
        from nika_onlymap_exporter.core.license_policy import default_policy
        from nika_onlymap_exporter.writers.onlymap_writer import OnlyMapWriter

        layout = _live_layout(project)
        writer = OnlyMapWriter(license_policy=default_policy("test-key-123"))
        html = export_layout_to_cartograph(
            layout, project, fake_runtime, FidelityReportBuilder(), writer=writer
        )
        assert 'license-key="test-key-123"' in html
        default_html = export_layout_to_cartograph(
            layout, project, fake_runtime, FidelityReportBuilder()
        )
        assert "license-key" not in default_html

    def test_one_project_per_live_frame(self, project, fake_runtime) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            finish_cartograph_full,
        )

        layout, _main, _inset, parcels, roads = _two_live_frames_layout(project)
        report = FidelityReportBuilder()
        prepared = prepare_cartograph(layout, project, fake_runtime, report)
        finished = finish_cartograph_full(prepared, project, report)
        assert len(finished.projects) == 2
        for export_project in finished.projects:
            # As read: QGIS's own layer ids, not the frame-prefixed DOM ids.
            assert {layer.layer_id for layer in export_project.layers} == {
                parcels.id(),
                roads.id(),
            }
        assert finished.html == finish_cartograph(prepared, project, report)

    def test_no_projects_for_a_static_only_cartograph(
        self, survey, fake_runtime
    ) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            finish_cartograph_full,
        )

        report = FidelityReportBuilder()
        prepared = prepare_cartograph(
            survey.layout, survey.project, fake_runtime, report, static_dpi=72
        )
        assert finish_cartograph_full(prepared, survey.project, report).projects == ()


# --------------------------------------------------------------------------
# The static-frame raster cache
# --------------------------------------------------------------------------


class TestStaticFrameCache:
    """Autosave rebuilds the cartograph after every edit; a static frame is
    re-rendered only when something that changes its pixels changed."""

    @pytest.fixture
    def cache(self):
        from nika_onlymap_exporter.core.cartograph_export import StaticFrameCache

        cache = StaticFrameCache()
        yield cache
        cache.clear()

    def _prepare(self, survey, runtime, cache, **kwargs):
        return prepare_cartograph(
            survey.layout,
            survey.project,
            runtime,
            FidelityReportBuilder(),
            static_dpi=72,
            raster_cache=cache,
            **kwargs,
        )

    def test_an_unrelated_edit_reuses_both_rasters(
        self, survey, fake_runtime, cache
    ) -> None:
        first = self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 2
        survey.title_label.setText("A different title")
        second = self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 2
        frames = [i for i in second.items if getattr(i, "image_bytes", None)]
        assert [f.image_bytes for f in frames] == [
            f.image_bytes for f in first.items if getattr(f, "image_bytes", None)
        ]

    def test_a_restyle_rerenders(self, survey, fake_runtime, cache) -> None:
        from qgis.core import QgsFillSymbol

        self._prepare(survey, fake_runtime, cache)
        survey.parcels.renderer().setSymbol(
            QgsFillSymbol.createSimple({"color": "#0000ff"})
        )
        self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 4  # both frames draw the parcels

    def test_an_extent_change_rerenders_only_that_frame(
        self, survey, fake_runtime, cache
    ) -> None:
        self._prepare(survey, fake_runtime, cache)
        extent = survey.main_map.extent()
        extent.grow(100)
        survey.main_map.setExtent(extent)
        self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 3

    def test_a_dpi_change_rerenders_only_that_frame(
        self, survey, fake_runtime, cache
    ) -> None:
        self._prepare(survey, fake_runtime, cache)
        self._prepare(
            survey, fake_runtime, cache, frame_dpis={survey.inset_map.uuid(): 96}
        )
        assert cache.render_count == 3

    def test_a_feature_edit_rerenders(self, survey, fake_runtime, cache) -> None:
        from qgis.core import QgsGeometry, QgsRectangle

        self._prepare(survey, fake_runtime, cache)
        layer = survey.parcels
        feature_id = next(layer.getFeatures()).id()
        layer.startEditing()
        layer.changeGeometry(
            feature_id,
            QgsGeometry.fromRect(QgsRectangle(365000, 145000, 365100, 145100)),
        )
        layer.commitChanges()
        self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 4

    def test_an_edit_that_moves_no_extent_still_rerenders(
        self, survey, fake_runtime, cache
    ) -> None:
        """Same feature count, same extent, same style: only the layer's own
        change signal can say the data changed."""
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QVariant

        layer = survey.parcels
        layer.dataProvider().addAttributes([QgsField("note", QVariant.String)])
        layer.updateFields()
        self._prepare(survey, fake_runtime, cache)
        layer.startEditing()
        layer.changeAttributeValue(
            next(layer.getFeatures()).id(), layer.fields().indexOf("note"), "x"
        )
        layer.commitChanges()
        self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 4

    def test_clear_forces_a_fresh_render(self, survey, fake_runtime, cache) -> None:
        self._prepare(survey, fake_runtime, cache)
        cache.clear()
        self._prepare(survey, fake_runtime, cache)
        assert cache.render_count == 4

    def test_no_cache_renders_every_time(self, survey, fake_runtime) -> None:
        from unittest import mock

        from nika_onlymap_exporter.core import cartograph_export

        with mock.patch.object(
            cartograph_export,
            "render_static_frame",
            wraps=cartograph_export.render_static_frame,
        ) as render:
            self._prepare(survey, fake_runtime, None)
            self._prepare(survey, fake_runtime, None)
        assert render.call_count == 4

    def test_the_shared_cache_is_on_by_default_and_clearable(
        self, survey, fake_runtime
    ) -> None:
        from unittest import mock

        from nika_onlymap_exporter.core import cartograph_export

        cartograph_export.clear_static_frame_cache()
        with mock.patch.object(
            cartograph_export,
            "render_static_frame",
            wraps=cartograph_export.render_static_frame,
        ) as render:
            for _ in range(2):
                prepare_cartograph(
                    survey.layout,
                    survey.project,
                    fake_runtime,
                    FidelityReportBuilder(),
                    static_dpi=72,
                )
            assert render.call_count == 2
            cartograph_export.clear_static_frame_cache()
            prepare_cartograph(
                survey.layout,
                survey.project,
                fake_runtime,
                FidelityReportBuilder(),
                static_dpi=72,
            )
            assert render.call_count == 4
        cartograph_export.clear_static_frame_cache()


class TestLiveCamera:
    def test_the_centre_is_the_mercator_midpoint(self) -> None:
        """The runtime centres in Web Mercator: 0..60 N centres near 35.3 N,
        not at the 30 N average of the degrees."""
        import math

        from nika_onlymap_exporter.core.cartograph_export import _camera_for_extent
        from nika_onlymap_exporter.core.export_ir import Extent

        (lon, lat), _zoom = _camera_for_extent(
            Extent(west=0.0, south=0.0, east=10.0, north=60.0), 100.0
        )

        def y(latitude: float) -> float:
            return math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))

        assert lon == pytest.approx(5.0)
        assert y(lat) == pytest.approx((y(0.0) + y(60.0)) / 2)
        assert lat == pytest.approx(35.26, abs=0.01)


class TestLayersTabChoices:
    """The Layers tab's popup choices apply to live frames as to a web map:
    a field kept out of popups is not in the cartograph either."""

    def test_popups_off_keeps_the_overlay_out_of_the_live_frame(
        self, project, fake_runtime
    ) -> None:
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        from nika_onlymap_exporter.core.settings import LayerSettings

        layout = _live_layout(project)
        layer = next(iter(project.mapLayers().values()))
        layer.dataProvider().addAttributes([QgsField("owner", QMetaType.Type.QString)])
        layer.updateFields()
        feature = next(layer.getFeatures())
        layer.dataProvider().changeAttributeValues({feature.id(): {0: "Ms Tan"}})
        with_popups = export_layout_to_cartograph(
            layout, project, fake_runtime, FidelityReportBuilder()
        )
        without = export_layout_to_cartograph(
            layout,
            project,
            fake_runtime,
            FidelityReportBuilder(),
            layer_settings={layer.id(): LayerSettings(popup=False)},
        )
        assert "Ms Tan" in with_popups
        assert "Ms Tan" not in without
