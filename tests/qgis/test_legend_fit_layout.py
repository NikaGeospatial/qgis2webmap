"""A too-small legend box on a layout is fitted to the web page's legend, and
the QGIS legend item changes with it: the layout preview, its prints and the
page show one box. Driven through the real export on real layouts.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from legend_fit_fixtures import (
    FitWorld,
    add_label,
    build_fit_world,
    export,
    legend_details,
    page_legend_box,
    rect_mm,
)
from qgis.core import QgsLayoutItemLegend, QgsPrintLayout, QgsReadWriteContext
from qgis.PyQt.QtXml import QDomDocument

from nika_onlymap_exporter.core import legend_edit as le


@pytest.fixture
def world(qgis_app, project) -> FitWorld:
    return build_fit_world(project)


def _listed(legend) -> list[str]:
    return [node.layerId() for node in legend.model().rootGroup().findLayers()]


def _intersects(a, b) -> bool:
    return (
        min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > 0.01
        and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > 0.01
    )


class TestTheQgisLegendChangesWithThePage:
    def test_a_too_small_box_grows_upward_and_keeps_its_columns(self, world) -> None:
        before = rect_mm(world.legend)
        _html, report = export(world)
        x, y, w, h = rect_mm(world.legend)
        assert h > before[3] + 5
        assert y + h == pytest.approx(before[1] + before[3], abs=0.01)
        assert (x, w) == (before[0], before[2])
        assert world.legend.columnCount() == 3
        assert any("mm taller" in d for d in legend_details(report))

    def test_the_page_draws_the_very_box_the_layout_has(self, world) -> None:
        html, _report = export(world)
        assert page_legend_box(html) == pytest.approx(rect_mm(world.legend), abs=0.01)

    def test_the_legend_stops_sizing_itself_to_its_contents(
        self, qgis_app, project
    ) -> None:
        auto = build_fit_world(project, auto_size=True)
        assert auto.legend.resizeToContents()
        export(auto)
        assert not auto.legend.resizeToContents()

    def test_a_legend_that_fits_is_left_alone_and_sizing_itself(
        self, qgis_app, project
    ) -> None:
        roomy = build_fit_world(project, legend_rect=(10, 20, 190, 190), auto_size=True)
        before = rect_mm(roomy.legend)
        _html, report = export(roomy)
        assert rect_mm(roomy.legend) == before
        assert roomy.legend.resizeToContents()
        assert legend_details(report) == []

    def test_the_fit_survives_saving_and_reopening_the_layout(self, world) -> None:
        export(world)
        fitted = rect_mm(world.legend)
        document = QDomDocument()
        element = world.layout.writeXml(document, QgsReadWriteContext())
        reopened = QgsPrintLayout(world.project)
        assert reopened.readXml(element, document, QgsReadWriteContext())
        legend = next(i for i in reopened.items() if isinstance(i, QgsLayoutItemLegend))
        assert rect_mm(legend) == pytest.approx(fitted, abs=0.01)
        assert legend.columnCount() == 3


class TestItSettlesInOnePass:
    def test_a_second_export_changes_nothing(self, world) -> None:
        export(world)
        fitted = rect_mm(world.legend)
        stack = world.layout.undoStack().stack()
        count = stack.count()
        html, report = export(world)
        assert rect_mm(world.legend) == fitted
        assert legend_details(report) == []
        assert stack.count() == count
        assert page_legend_box(html) == pytest.approx(fitted, abs=0.01)

    def test_the_fit_adds_no_undo_step(self, world) -> None:
        stack = world.layout.undoStack().stack()
        count, index = stack.count(), stack.index()
        export(world)
        assert (stack.count(), stack.index()) == (count, index)

    def test_a_box_the_user_made_bigger_is_never_shrunk(
        self, qgis_app, project
    ) -> None:
        big = build_fit_world(project, legend_rect=(10, 20, 190, 185))
        before = rect_mm(big.legend)
        export(big)
        assert rect_mm(big.legend) == before


class TestUndoAndRedoStayTheUsers:
    def test_undoing_the_edit_that_overfilled_the_legend_restores_the_box(
        self, world
    ) -> None:
        legend, layers = world.legend, world.layers
        last = layers[-1].id()
        le.set_layer_listed(world.layout, legend, world.project, last, False)
        export(world)
        fitted_without = rect_mm(legend)

        stack = world.layout.undoStack().stack()
        le.set_layer_listed(world.layout, legend, world.project, last, True)
        export(world)
        fitted_with = rect_mm(legend)
        assert fitted_with[3] > fitted_without[3]
        count = stack.count()

        stack.undo()  # the user's step, not the fit's
        assert last not in _listed(legend)
        assert rect_mm(legend) == pytest.approx(fitted_without, abs=0.01)
        export(world)
        assert rect_mm(legend) == pytest.approx(fitted_without, abs=0.01)
        assert stack.count() == count

        stack.redo()
        export(world)
        assert last in _listed(legend)
        assert rect_mm(legend) == pytest.approx(fitted_with, abs=0.01)
        assert stack.count() == count


class TestLimits:
    def test_it_does_not_grow_over_an_item_it_would_newly_overlap(
        self, qgis_app, project
    ) -> None:
        world = build_fit_world(project)
        label = add_label(world, 20, 120, 100, 10)  # bottom edge at 130
        export(world)
        legend_rect = rect_mm(world.legend)
        assert legend_rect[1] >= 130 - 0.01
        assert not _intersects(legend_rect, rect_mm(label))

    def test_it_stops_at_the_page_margin(self, qgis_app, project) -> None:
        world = build_fit_world(project, layer_count=14, columns=1)
        export(world)
        assert rect_mm(world.legend)[1] >= 5 - 0.01

    def test_more_columns_before_leaving_layers_out(self, qgis_app, project) -> None:
        world = build_fit_world(project, legend_rect=(10, 150, 190, 40), columns=2)
        add_label(world, 20, 60, 100, 10)  # 80 mm of room above
        _html, report = export(world)
        assert world.legend.columnCount() == 3
        assert len(_listed(world.legend)) == 8
        assert any("columns" in d for d in legend_details(report))

    def test_left_out_layers_leave_the_qgis_legend_and_can_be_brought_back(
        self, qgis_app, project
    ) -> None:
        world = build_fit_world(project, legend_rect=(10, 150, 190, 40), columns=3)
        add_label(world, 20, 140, 100, 5)  # no room at all above the legend
        original = _listed(world.legend)
        _html, report = export(world)
        listed = _listed(world.legend)
        assert 0 < len(listed) < 8
        # from the end of the legend, like the page
        assert listed == original[: len(listed)]
        left_out = le.restorable(world.legend, world.project)
        assert {e.layer_id for e in left_out if e.index is None} == {
            layer.id() for layer in world.layers
        } - set(listed)
        assert any("left without" in d for d in legend_details(report))
        # and the QGIS legend is the page's: same box, same layers
        assert page_legend_box(_html) == pytest.approx(rect_mm(world.legend), abs=0.01)


class TestImageFramesAreUntouched:
    def test_a_legend_on_an_image_frame_keeps_its_box(self, world) -> None:
        before = rect_mm(world.legend)
        export(world, frame_modes={world.map_item.uuid(): "static"})
        assert rect_mm(world.legend) == before
