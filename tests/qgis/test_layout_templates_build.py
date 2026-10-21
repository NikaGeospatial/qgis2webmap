"""The layout templates against real QGIS: each becomes a print layout that
exports to a cartograph, previews draw without touching the project, and
the picker chooses, draws its previews lazily and remembers the choice.

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
from nika_onlymap_exporter.core.cartograph_export import (
    finish_cartograph,
    prepare_cartograph,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.layout_templates import template_ids
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")


@pytest.fixture
def runtime() -> RuntimeBundle:
    return RuntimeBundle(
        javascript=b"// core",
        css=b"/* core */",
        version="0.13.1",
        sha256="0" * 64,
        cartograph_javascript=b"// carto",
        cartograph_css=b"/* carto */",
    )


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _view(parcels):
    extent = parcels.extent()
    extent.grow(200)
    return extent


class TestEveryTemplateBuildsAndExports:
    @pytest.mark.parametrize("template_id", template_ids())
    def test_a_layout_that_exports(
        self, template_id, parcels, project, runtime
    ) -> None:
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id=template_id
        )
        layout = result.layout
        assert project.layoutManager().layoutByName(layout.name()) is layout
        for slot in result.plan.frames:
            item = layout.itemById(slot.id)
            assert item is not None, slot.id
            # The map item is where the template put it, at its planned size.
            size = item.sizeWithUnits()
            assert size.width() == pytest.approx(slot.box.w, abs=0.05)
            assert size.height() == pytest.approx(slot.box.h, abs=0.05)
        main = layout.itemById("main")
        shown = main.extent()
        view = _view(parcels)
        # Padded out to the frame's shape, never cropped.
        assert shown.contains(view) or shown.width() == pytest.approx(view.width())
        report = FidelityReportBuilder()
        html = finish_cartograph(
            prepare_cartograph(
                layout,
                project,
                runtime,
                report,
                frame_modes=result.frame_modes,
                raster_cache=None,
            ),
            project,
            report,
        )
        assert "<om-cartograph" in html
        assert not report.has_blockers

    def test_the_locator_outlines_the_main_map(self, parcels, project) -> None:
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="locator"
        )
        locator = result.layout.itemById("locator")
        overviews = locator.overviews().asList()
        assert len(overviews) == 1
        assert overviews[0].linkedMap() is result.layout.itemById("main")
        assert (
            locator.extent().width()
            > 4 * result.layout.itemById("main").extent().width()
        )
        # Context, not the subject: drawn as QGIS draws it.
        assert result.frame_modes[locator.uuid()] == "static"

    def test_dark_paints_the_page(self, parcels, project) -> None:
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dark"
        )
        page = result.layout.pageCollection().page(0)
        assert page.pageStyleSymbol().color().lightness() < 40

    def test_tabloid_is_a_real_page_size(self, parcels, project) -> None:
        result = cartograph_from_view(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id="poster",
            page_size="Tabloid",
            orientation="portrait",
        )
        size = result.layout.pageCollection().page(0).pageSize()
        assert (size.width(), size.height()) == pytest.approx((279.4, 431.8), abs=0.05)

    def test_the_project_abstract_fills_the_story_panel(self, parcels, project) -> None:
        metadata = project.metadata()
        metadata.setAbstract("Parcels surveyed in 2026.")
        project.setMetadata(metadata)
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="story-map"
        )
        notes = result.layout.itemById("notes")
        assert "Parcels surveyed in 2026." in notes.currentText()


class TestPreview:
    def test_it_draws_the_page_and_leaves_the_project_alone(
        self, parcels, project
    ) -> None:
        before = len(project.layoutManager().layouts())
        image = template_preview(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id="poster",
            width_px=200,
            page_size="A4",
            orientation="portrait",
        )
        assert len(project.layoutManager().layouts()) == before
        assert (image.width(), image.height()) == (200, round(200 * 297 / 210))
        # Not a blank page: the map and the title are on it.
        colours = {
            image.pixelColor(x, y).name()
            for x in range(0, image.width(), 5)
            for y in range(0, image.height(), 5)
        }
        assert len(colours) > 3


class TestPicker:
    def _picker(self, qgis_app, tmp_path, calls, fail=()):
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtGui import QColor, QImage

        from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker

        settings = QSettings(str(tmp_path / "picker.ini"), QSettings.Format.IniFormat)

        def render(template_id, page_size, orientation, width_px):
            calls.append((template_id, page_size, orientation))
            if template_id in fail:
                raise RuntimeError("no")
            image = QImage(width_px, width_px, QImage.Format.Format_ARGB32)
            image.fill(QColor("#336699"))
            return image

        return TemplatePicker(render, qsettings=settings), settings

    @staticmethod
    def _drain(qgis_app, picker) -> None:
        while picker._queue:
            qgis_app.processEvents()
        qgis_app.processEvents()

    def test_every_preview_draws_the_selected_one_first(
        self, qgis_app, tmp_path
    ) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            self._drain(qgis_app, picker)
            assert calls[0][0] == "classic"
            assert sorted(c[0] for c in calls) == sorted(template_ids())
            assert all(row.drawn for row in picker.rows.values())
            assert len(picker.rows) == picker.template_list.count()
        finally:
            picker.reject()

    def test_a_new_page_size_draws_them_again(self, qgis_app, tmp_path) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            self._drain(qgis_app, picker)
            calls.clear()
            picker.page_combo.setCurrentIndex(picker.page_combo.findData("Tabloid"))
            self._drain(qgis_app, picker)
            assert {c[1] for c in calls} == {"Tabloid"}
            assert len(calls) == len(template_ids())
        finally:
            picker.reject()

    def test_a_failed_preview_says_so_and_the_rest_still_draw(
        self, qgis_app, tmp_path
    ) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls, fail=("dark",))
        try:
            self._drain(qgis_app, picker)
            items = {
                picker.template_list.item(r).data(256): picker.template_list.item(r)
                for r in range(picker.template_list.count())
            }
            assert not picker.rows["dark"].drawn
            assert "could not be drawn" in items["dark"].toolTip()
            assert picker.rows["poster"].drawn
        finally:
            picker.reject()

    def test_each_row_names_its_design_and_the_chosen_one_is_ticked(
        self, qgis_app, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.layout_templates import get_template

        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            for template_id, row in picker.rows.items():
                assert row.name.text() == get_template(template_id).name
            for r in range(picker.template_list.count()):
                if picker.template_list.item(r).data(256) == "poster":
                    picker.template_list.setCurrentRow(r)
            ticked = [t for t, row in picker.rows.items() if row.tick.text()]
            assert ticked == ["poster"]
        finally:
            picker.reject()

    def test_the_choice_is_remembered(self, qgis_app, tmp_path) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, settings = self._picker(qgis_app, tmp_path, calls)
        for row in range(picker.template_list.count()):
            if picker.template_list.item(row).data(256) == "story-map":
                picker.template_list.setCurrentRow(row)
        picker.page_combo.setCurrentIndex(picker.page_combo.findData("A3"))
        picker.orientation_combo.setCurrentIndex(
            picker.orientation_combo.findData("portrait")
        )
        picker.accept()
        assert (picker.template_id, picker.page_size, picker.orientation) == (
            "story-map",
            "A3",
            "portrait",
        )
        again, _ = self._picker(qgis_app, tmp_path, calls)
        try:
            assert (again.template_id, again.page_size, again.orientation) == (
                "story-map",
                "A3",
                "portrait",
            )
        finally:
            again.reject()
        settings.setValue("qgis2webmap/cartograph/template", "gone|B9|upside")
        stale, _ = self._picker(qgis_app, tmp_path, calls)
        try:
            assert (stale.template_id, stale.page_size, stale.orientation) == (
                "classic",
                "A4",
                "auto",
            )
        finally:
            stale.reject()
