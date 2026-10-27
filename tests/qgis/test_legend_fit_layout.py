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


def _is_settled(world: FitWorld) -> bool:
    """Whether the legend, as it now is, needs nothing more: a further export
    changes neither its box, its columns nor its layers, and says nothing
    about the legend. That is what "its contents fit its box" comes to, as the
    fit works from the same model of the web page's legend."""
    before = (rect_mm(world.legend), world.legend.columnCount(), _listed(world.legend))
    _html, report = export(world)
    after = (rect_mm(world.legend), world.legend.columnCount(), _listed(world.legend))
    return before == after and legend_details(report) == []


def _inside_page(box) -> bool:
    return (
        box[0] >= 0
        and box[1] >= 0
        and box[0] + box[2] <= 297
        and box[1] + box[3] <= 210
    )


class TestTheQgisLegendChangesWithThePage:
    def test_a_too_small_box_is_made_to_hold_every_layer(self, world) -> None:
        before = rect_mm(world.legend)
        original = _listed(world.legend)
        export(world)
        box = rect_mm(world.legend)
        assert _listed(world.legend) == original
        assert box[3] > before[3]
        assert _inside_page(box)
        assert _is_settled(world)

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

    def test_columns_are_added_before_any_layer_is_left_out(
        self, qgis_app, project
    ) -> None:
        # Two layers in one column are far taller than the box, and there is
        # no room to grow: items touch it above and below. Two columns hold
        # them with plenty to spare.
        world = build_fit_world(
            project, layer_count=2, legend_rect=(10, 150, 190, 45), columns=1
        )
        add_label(world, 20, 140, 100, 10)
        add_label(world, 20, 195, 100, 5)
        original = _listed(world.legend)
        export(world)
        assert world.legend.columnCount() > 1
        assert _listed(world.legend) == original
        assert _is_settled(world)

    def test_a_layer_is_left_out_only_with_the_columns_already_added(
        self, qgis_app, project
    ) -> None:
        world = build_fit_world(project, legend_rect=(10, 150, 190, 40), columns=1)
        add_label(world, 20, 140, 100, 10)
        add_label(world, 20, 190, 100, 5)
        original = _listed(world.legend)
        export(world)
        assert len(_listed(world.legend)) < len(original)
        assert world.legend.columnCount() > 1
        assert _is_settled(world)

    def test_left_out_layers_leave_the_qgis_legend_and_can_be_brought_back(
        self, qgis_app, project
    ) -> None:
        world = build_fit_world(project, legend_rect=(10, 150, 190, 40), columns=3)
        add_label(world, 20, 140, 100, 10)  # touching the legend above
        add_label(world, 20, 190, 100, 5)  # and below: no room at all
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


class TestOneRowForATemplateLegend:
    def test_a_template_legend_that_also_loses_layers_gets_one_left_out_row(
        self, qgis_app, project
    ) -> None:
        from nika_onlymap_exporter.core.layout_reader import LEGEND_LEFT_OUT_PROPERTY

        world = build_fit_world(project, legend_rect=(10, 150, 190, 40), columns=3)
        add_label(world, 20, 140, 100, 5)  # no room above the legend
        # As a template leaves it: two entries out of the QGIS legend.
        world.legend.setCustomProperty(LEGEND_LEFT_OUT_PROPERTY, 2)
        le.set_layer_listed(None, world.legend, project, world.layers[-1].id(), False)
        _html, report = export(world)
        left = [i.detail for i in report.items if "leaves out" in i.detail]
        assert len(left) == 1
        assert "larger page" in left[0]
        shown = len(_listed(world.legend))
        assert shown < 8
        assert not any("left without" in d for d in legend_details(report))
