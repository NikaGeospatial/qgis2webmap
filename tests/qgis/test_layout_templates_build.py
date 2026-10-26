"""The layout templates against real QGIS: each becomes a print layout that
exports to a cartograph, previews draw without touching the project, and
the picker chooses, draws its previews lazily and remembers the choice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re

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
from nika_onlymap_exporter.core.layout_builder import import_cartograph_file
from nika_onlymap_exporter.core.layout_reader import read_layout
from nika_onlymap_exporter.core.layout_templates import template_ids
from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

qgis_core = pytest.importorskip("qgis.core")

#: The ten designs this file covers (the newer five: test_template_designs_build).
TEN = template_ids()[:10]


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


def _export(result, project, runtime, report) -> str:
    return finish_cartograph(
        prepare_cartograph(
            result.layout,
            project,
            runtime,
            report,
            frame_modes=result.frame_modes,
            raster_cache=None,
        ),
        project,
        report,
    )


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

    def test_dark_keeps_its_page_and_frame_colours_in_the_html(
        self, parcels, project, runtime
    ) -> None:
        """The dark page and the dark frame reach the HTML, or the light
        title sits on the runtime's pale page wherever the map has not drawn
        -- the bug a real Grand Canyon export showed."""
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dark"
        )
        report = FidelityReportBuilder()
        reading = read_layout(result.layout, report, frame_modes=result.frame_modes)
        assert reading.bg == "#14161a"
        assert [frame.frame_bg for frame in reading.frames] == ["#14161a"]
        html = _export(result, project, runtime, FidelityReportBuilder())
        assert re.search(r'<om-cartograph [^>]*\bbg="#14161a"', html)
        assert re.search(r'<om-frame [^>]*\bframe-bg="#14161a"', html)
        # Light text on that dark ground reads fine: no contrast warning.
        assert not [row for row in report.items if "contrast" in row.detail]

    def test_dark_round_trips_its_colours_through_import(
        self, parcels, project, runtime, tmp_path
    ) -> None:
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dark"
        )
        path = tmp_path / "dark.html"
        path.write_text(_export(result, project, runtime, FidelityReportBuilder()))
        built = import_cartograph_file(path, project)
        reading = read_layout(
            built.layout, FidelityReportBuilder(), frame_modes=built.frame_modes
        )
        assert reading.bg == "#14161a"
        assert [frame.frame_bg for frame in reading.frames] == ["#14161a"]

    @pytest.mark.parametrize("template_id", TEN)
    def test_a_design_keeps_its_page_and_map_colours_in_the_html(
        self, template_id, parcels, project, runtime
    ) -> None:
        """Every design chooses its paper and its map's background -- white
        included -- so the HTML shows them rather than the runtime theme's
        lilac or off-white; and none of its text is too faint to read."""
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id=template_id
        )
        report = FidelityReportBuilder()
        html = _export(result, project, runtime, report)
        page = re.search(r'<om-cartograph [^>]*\bbg="([^"]+)"', html)
        assert page and page.group(1).lower() == result.plan.bg.lower()
        main = re.search(r"<om-frame [^>]*>", html).group(0)
        assert f'frame-bg="{result.plan.frames[0].frame_bg}"' in main
        assert not [row for row in report.items if "contrast" in row.detail]

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


class TestTheRefinedDesigns:
    def test_fieldbook_prints_its_scale_and_crs_in_qgis(
        self, parcels, project, runtime
    ) -> None:
        """The title block's scale and CRS are numbers in the QGIS layout,
        its PDF and PNG -- not the blank a bare `@map_scale` gives a label --
        and live tokens in the HTML."""
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="field-map"
        )
        layout = result.layout
        assert re.fullmatch(r"Scale 1:\d+", layout.itemById("block-0").currentText())
        assert layout.itemById("block-1").currentText() == "EPSG:32648"
        html = _export(result, project, runtime, FidelityReportBuilder())
        assert re.search(r">Scale 1:\{\{frame\.[\w-]+\.scale\}\}<", html)
        assert re.search(r">\{\{frame\.[\w-]+\.crs\}\}<", html)

    @pytest.mark.parametrize("template_id", ["story-map", "dark"])
    def test_the_view_sits_in_the_clear_part_of_the_frame(
        self, template_id, parcels, project
    ) -> None:
        project.setTitle("Kranji parcels: land use survey, 2026")
        view = _view(parcels)
        result = cartograph_from_view(
            view, UTM, [parcels], project, template_id=template_id
        )
        slot = result.plan.frames[0]
        assert slot.focus is not None
        shown = result.layout.itemById("main").extent()
        per_mm = shown.width() / slot.box.w
        assert per_mm == pytest.approx(shown.height() / slot.box.h, rel=1e-3)
        # The canvas view, in page mm inside the frame, is inside the focus.
        left = slot.box.x + (view.xMinimum() - shown.xMinimum()) / per_mm
        top = slot.box.y + (shown.yMaximum() - view.yMaximum()) / per_mm
        right = left + view.width() / per_mm
        bottom = top + view.height() / per_mm
        focus = slot.focus
        assert left >= focus.x - 0.1 and right <= focus.x + focus.w + 0.1
        assert top >= focus.y - 0.1 and bottom <= focus.y + focus.h + 0.1

    def test_signal_shows_the_figures_the_project_gives(self, parcels, project) -> None:
        project.setCustomVariables(
            {"nika_figure_1": "214 | parcels surveyed", "nika_figure_2": " | blank"}
        )
        result = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dashboard"
        )
        layout = result.layout
        assert layout.itemById("figure-0").text() == "214"
        assert layout.itemById("figure-0-label").text() == "parcels surveyed"
        assert layout.itemById("figure-1") is None
        project.setCustomVariables({})
        bare = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dashboard"
        )
        assert bare.layout.itemById("figure-0") is None

    @pytest.mark.parametrize("template_id", ["classic", "dark", "story-map"])
    def test_a_turned_map_gets_a_north_arrow(
        self, template_id, parcels, project
    ) -> None:
        turned = cartograph_from_view(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id=template_id,
            rotation=25.0,
        )
        assert turned.layout.itemById("north") is not None
        upright = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id=template_id
        )
        assert upright.layout.itemById("north") is None


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

    @pytest.mark.parametrize("template_id", template_ids())
    def test_it_does_not_mark_a_clean_project_changed(
        self, parcels, project, template_id
    ) -> None:
        project.setDirty(False)
        template_preview(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id=template_id,
            width_px=120,
        )
        assert not project.isDirty()

    def test_it_leaves_a_changed_project_changed(self, parcels, project) -> None:
        project.setDirty(True)
        template_preview(
            _view(parcels), UTM, [parcels], project, template_id="poster", width_px=120
        )
        assert project.isDirty()

    @staticmethod
    def _wait(qgis_app, pending) -> list:
        """Spin the event loop until the pending preview reports."""
        import time

        got: list = []
        pending.finished.connect(got.append)
        deadline = time.monotonic() + 30
        while not got and time.monotonic() < deadline:
            qgis_app.processEvents()
        return got

    def test_the_background_page_matches_the_direct_one(
        self, qgis_app, parcels, project
    ) -> None:
        from nika_onlymap_exporter.core.canvas_cartograph import (
            PREVIEW_LEGEND_LEFT_OUT_KEY,
            template_preview_async,
        )

        kwargs = {"template_id": "locator", "width_px": 360}
        direct = template_preview(_view(parcels), UTM, [parcels], project, **kwargs)
        pending = template_preview_async(
            _view(parcels), UTM, [parcels], project, **kwargs
        )
        (image,) = self._wait(qgis_app, pending)
        assert image.size() == direct.size()
        assert image.text(PREVIEW_LEGEND_LEFT_OUT_KEY) == direct.text(
            PREVIEW_LEGEND_LEFT_OUT_KEY
        )
        # Not pixel for pixel (the maps are resampled from their caches),
        # but the same page: the mean channel error stays tiny.
        total = 0
        for y in range(direct.height()):
            for x in range(direct.width()):
                a, b = image.pixelColor(x, y), direct.pixelColor(x, y)
                total += abs(a.red() - b.red()) + abs(a.green() - b.green())
                total += abs(a.blue() - b.blue())
        assert total / (direct.width() * direct.height() * 3) < 2.0

    def test_the_background_page_leaves_the_project_clean(
        self, qgis_app, parcels, project
    ) -> None:
        from nika_onlymap_exporter.core.canvas_cartograph import (
            template_preview_async,
        )

        project.setDirty(False)
        layouts = list(project.layoutManager().layouts())
        pending = template_preview_async(
            _view(parcels), UTM, [parcels], project, template_id="poster", width_px=120
        )
        self._wait(qgis_app, pending)
        assert not project.isDirty()
        assert list(project.layoutManager().layouts()) == layouts

    def test_a_cancelled_background_page_never_reports(
        self, qgis_app, parcels, project
    ) -> None:
        from nika_onlymap_exporter.core.canvas_cartograph import (
            template_preview_async,
        )

        pending = template_preview_async(
            _view(parcels), UTM, [parcels], project, template_id="poster", width_px=120
        )
        got: list = []
        pending.finished.connect(got.append)
        pending.cancel()
        assert not pending.active
        for _ in range(50):
            qgis_app.processEvents()
        assert got == []


def _categorized(name: str, categories: int):
    """The parcels as `categories` land-use classes -- one legend heading
    and a row per class, not the single row a plain layer has."""
    layer = build_parcels_layer()
    layer.setName(name)
    layer.setRenderer(
        qgis_core.QgsCategorizedSymbolRenderer(
            "use",
            [
                qgis_core.QgsRendererCategory(
                    f"class-{i}",
                    qgis_core.QgsFillSymbol.createSimple(
                        {"color": f"#{(i * 37) % 256:02x}{(i * 91) % 256:02x}80"}
                    ),
                    f"Land use class {i + 1}",
                )
                for i in range(categories)
            ],
        )
    )
    return layer


class TestTheLegendShowsEveryEntry:
    """A categorized layer's rows all fit inside the legend's planned box --
    nothing is cut off at the box's edge -- whatever the template."""

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    @pytest.mark.parametrize("template_id", template_ids())
    @pytest.mark.parametrize(
        "shape", [(1, 5), (3, 12)], ids=["one-layer-5-classes", "3-layers-12-classes"]
    )
    def test_nothing_is_clipped(self, template_id, orientation, shape, project) -> None:
        layer_count, categories = shape
        layers = [
            _categorized(f"Parcels {k + 1}", categories) for k in range(layer_count)
        ]
        for layer in layers:
            project.addMapLayer(layer)
        result = cartograph_from_view(
            _view(layers[0]),
            UTM,
            layers,
            project,
            template_id=template_id,
            orientation=orientation,
        )
        legend = result.layout.itemById("legend")
        planned = [item for item in result.plan.items if item.id == "legend"]
        if not planned:
            assert legend is None
            return
        # Every class is in the legend, none filtered out.
        model = legend.model()
        rows = sum(
            len(model.layerLegendNodes(node)) for node in model.rootGroup().findLayers()
        )
        assert rows == layer_count * categories
        # Still the template's own legend area...
        box = planned[0].box
        size = legend.sizeWithUnits()
        assert (size.width(), size.height()) == pytest.approx((box.w, box.h), abs=0.05)
        # ...and what QGIS draws in it fits.
        context = qgis_core.QgsLayoutUtils.createRenderContextForLayout(
            result.layout, None
        )
        needed = qgis_core.QgsLegendRenderer(
            model, legend.legendSettings()
        ).minimumSize(context)
        assert needed.width() <= box.w + 0.05, template_id
        assert needed.height() <= box.h + 0.05, template_id


class TestPicker:
    def _picker(self, qgis_app, tmp_path, calls, fail=()):
        from qgis.PyQt.QtCore import QSettings
        from qgis.PyQt.QtGui import QColor, QImage

        from nika_onlymap_exporter.ui.cartograph.template_picker import TemplatePicker

        settings = QSettings(str(tmp_path / "picker.ini"), QSettings.Format.IniFormat)

        def render(
            template_id, page_size, orientation, width_px, figures=(), sizes=None
        ):
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

    @staticmethod
    def _in_reach(picker) -> set[str]:
        """The designs drawn unasked: the chosen one, and the cards in view
        or a short scroll from it (the rest wait until they come near)."""
        return {picker.template_id, *picker.near_ids()}

    def test_the_selected_preview_draws_first_then_those_in_reach(
        self, qgis_app, tmp_path
    ) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            self._drain(qgis_app, picker)
            assert calls[0][0] == "classic"
            assert {c[0] for c in calls} == self._in_reach(picker)
            assert len(calls) == len({c[0] for c in calls})
            assert all(picker.rows[t].drawn for t in self._in_reach(picker))
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
            assert sorted(c[0] for c in calls) == sorted(self._in_reach(picker))
        finally:
            picker.reject()

    def test_a_failed_preview_says_so_and_the_rest_still_draw(
        self, qgis_app, tmp_path
    ) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls, fail=("dark",))
        try:
            # Chosen, so it is drawn whatever is in view.
            for row in range(picker.template_list.count()):
                if picker.template_list.item(row).data(256) == "dark":
                    picker.template_list.setCurrentRow(row)
            self._drain(qgis_app, picker)
            items = {
                picker.template_list.item(r).data(256): picker.template_list.item(r)
                for r in range(picker.template_list.count())
            }
            assert not picker.rows["dark"].drawn
            assert "could not be drawn" in items["dark"].toolTip()
            assert all(
                picker.rows[t].drawn for t in self._in_reach(picker) if t != "dark"
            )
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
            if picker.template_list.item(row).data(256) == "poster":
                picker.template_list.setCurrentRow(row)
        picker.page_combo.setCurrentIndex(picker.page_combo.findData("A3"))
        picker.orientation_combo.setCurrentIndex(
            picker.orientation_combo.findData("portrait")
        )
        picker.accept()
        assert (picker.template_id, picker.page_size, picker.orientation) == (
            "poster",
            "A3",
            "portrait",
        )
        again, _ = self._picker(qgis_app, tmp_path, calls)
        try:
            assert (again.template_id, again.page_size, again.orientation) == (
                "poster",
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

    def test_the_page_settings_have_visible_labels(self, qgis_app, tmp_path) -> None:
        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            texts = [
                label.text()
                for label in picker.findChildren(type(picker.selected_label))
            ]
            assert "Page size" in texts
            assert "Orientation" in texts
        finally:
            picker.reject()

    def test_the_filter_shows_one_category_and_keeps_a_shown_choice(
        self, qgis_app, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.layout_templates import (
            CATEGORIES,
            get_template,
        )

        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            for r in range(picker.template_list.count()):
                if picker.template_list.item(r).data(256) == "poster":
                    picker.template_list.setCurrentRow(r)
            own = get_template("poster").category
            assert own in CATEGORIES
            picker.filter_buttons[own].click()
            assert picker.template_id == "poster"
            for r in range(picker.template_list.count()):
                item = picker.template_list.item(r)
                shown = get_template(item.data(256)).category == own
                assert item.isHidden() == (not shown)
            picker.filter_buttons["all"].click()
            assert not any(
                picker.template_list.item(r).isHidden()
                for r in range(picker.template_list.count())
            )
        finally:
            picker.reject()

    def test_selecting_a_row_updates_the_selected_line(
        self, qgis_app, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.layout_templates import get_template

        calls: list[tuple[str, str, str]] = []
        picker, _settings = self._picker(qgis_app, tmp_path, calls)
        try:
            for r in range(picker.template_list.count()):
                if picker.template_list.item(r).data(256) == "poster":
                    picker.template_list.setCurrentRow(r)
            assert get_template("poster").name in picker.selected_label.text()
        finally:
            picker.reject()
