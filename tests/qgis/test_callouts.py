"""Statistic callouts live on their own layout: Signal draws none, one or
three well; they are added, changed and removed on the page afterwards, as
undo steps, and never reach another layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from qgis.core import (
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core.canvas_cartograph import (
    cartograph_from_view,
    project_figures,
    template_record,
)
from nika_onlymap_exporter.ui.cartograph.callouts import (
    CALLOUT_LIMIT,
    add_callout,
    can_add_callout,
    change_callout,
    layout_callouts,
    migrate_project_figures,
    remove_callout,
    supports_callouts,
)

pytest.importorskip("qgis.core")

UNITS = QgsUnitTypes.LayoutMillimeters
FIGURES = [("214", "parcels surveyed"), ("12 km", "of road"), ("9", "wells")]


@pytest.fixture
def parcels(project):
    project.setTitle("Survey")
    metadata = project.metadata()
    metadata.setAbstract("Parcels surveyed this season.")
    project.setMetadata(metadata)
    layer = build_parcels_layer()
    project.addMapLayer(layer)
    return layer


def _view(parcels):
    extent = parcels.extent()
    extent.grow(200)
    return extent


def _signal(parcels, project, figures, **kwargs):
    return cartograph_from_view(
        _view(parcels),
        UTM,
        [parcels],
        project,
        template_id="dashboard",
        figures=figures,
        **kwargs,
    ).layout


def _items(layout):
    return [i for i in layout.items() if isinstance(i, QgsLayoutItem)]


def _rects(layout) -> dict[str, tuple[float, float, float, float]]:
    """Every item's box in mm. A scale bar draws itself at the width its map's
    scale needs, so only its corner counts."""
    return {
        item.id(): (
            round(item.pos().x(), 1),
            round(item.pos().y(), 1),
            0.0
            if isinstance(item, QgsLayoutItemScaleBar)
            else round(item.rect().width(), 1),
            round(item.rect().height(), 1),
        )
        for item in _items(layout)
        if item.id()
    }


def _words(layout) -> list[tuple[str, str]]:
    return [(c.value, c.label) for c in layout_callouts(layout)]


class TestSignalBalance:
    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_no_empty_boxes_and_nothing_overlaps(self, parcels, project, count) -> None:
        layout = _signal(parcels, project, FIGURES[:count])
        assert _words(layout) == FIGURES[:count]
        page = layout.pageCollection().page(0).pageSize()
        callout_ids = [
            i.id() for i in _items(layout) if i.id().startswith(("figure-", "rule-f"))
        ]
        # three items for a callout with a line, two without: none is empty.
        assert len(callout_ids) == 3 * count
        for item in _items(layout):
            if isinstance(item, QgsLayoutItemLabel) and item.id().startswith("figure-"):
                assert item.text().strip()
        boxes = {
            i.id(): QRectF(i.pos(), i.rect().size())
            for i in _items(layout)
            if i.id() and not i.id().startswith("page")
        }
        sheet = QRectF(0, 0, page.width(), page.height())
        for item_id, box in boxes.items():
            assert sheet.adjusted(-0.1, -0.1, 0.1, 0.1).contains(box), item_id
        blocks = {i: b for i, b in boxes.items() if i in callout_ids}
        others = {
            i: b
            for i, b in boxes.items()
            if i not in callout_ids and i not in ("page", "background")
        }
        for first in blocks:
            for second in others:
                crossing = blocks[first].intersected(others[second])
                assert crossing.width() < 0.1 or crossing.height() < 0.1, (
                    first,
                    second,
                )

    def test_the_map_keeps_a_sensible_share_with_three(self, parcels, project) -> None:
        none = _signal(parcels, project, [], layout_name="none")
        three = _signal(parcels, project, FIGURES, layout_name="three")
        a = none.itemById("main").rect()
        b = three.itemById("main").rect()
        assert b.width() * b.height() > 0.4 * a.width() * a.height()


class TestAddChangeRemove:
    def test_adding_matches_a_page_built_with_them(self, parcels, project) -> None:
        layout = _signal(parcels, project, [], layout_name="grown")
        for index, figure in enumerate(FIGURES, start=1):
            assert can_add_callout(layout)
            assert add_callout(layout, *figure)
            fresh = _signal(
                parcels, project, FIGURES[:index], layout_name=f"fresh {index}"
            )
            assert _rects(layout) == _rects(fresh)
        assert _words(layout) == FIGURES

    def test_a_fourth_is_refused(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES)
        assert not can_add_callout(layout)
        assert not add_callout(layout, "1", "more")
        assert len(layout_callouts(layout)) == CALLOUT_LIMIT

    def test_a_blank_or_repeated_one_is_refused(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES[:1])
        assert not add_callout(layout, "  ", "nothing")
        assert not add_callout(layout, *FIGURES[0])
        assert _words(layout) == FIGURES[:1]

    def test_removing_closes_up_the_rest(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES, layout_name="three")
        assert remove_callout(layout, 1)
        assert _words(layout) == [FIGURES[0], FIGURES[2]]
        assert layout.itemById("figure-2") is None
        assert layout.itemById("figure-1").text() == "9"
        fresh = _signal(parcels, project, [FIGURES[0], FIGURES[2]], layout_name="two")
        assert _rects(layout) == _rects(fresh)

    def test_changing_words_keeps_the_lettering(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES)
        item = layout.itemById("figure-0")
        font = item.textFormat()
        font.setSize(31)
        item.setTextFormat(font)
        before = _rects(layout)
        assert change_callout(layout, 0, "215", "parcels surveyed")
        assert layout.itemById("figure-0").text() == "215"
        assert layout.itemById("figure-0").textFormat().size() == 31
        assert _rects(layout) == before

    def test_clearing_a_line_makes_room_again(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES, layout_name="three")
        assert change_callout(layout, 2, "9", "")
        assert layout.itemById("figure-2-label") is None
        fresh = _signal(
            parcels, project, [*FIGURES[:2], ("9", "")], layout_name="fresh"
        )
        assert _rects(layout) == _rects(fresh)

    def test_what_the_user_moved_stays_put(self, parcels, project) -> None:
        layout = _signal(parcels, project, [], layout_name="moved")
        notes = layout.itemById("notes")
        assert notes is not None
        notes.attemptMove(QgsLayoutPoint(notes.pos().x(), notes.pos().y() + 3, UNITS))
        moved = _rects(layout)["notes"]
        assert add_callout(layout, "214", "parcels")
        assert _rects(layout)["notes"] == moved

    def test_undo_and_redo_each_step(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES[:1])
        stack = layout.undoStack().stack()
        start = _rects(layout)
        assert add_callout(layout, *FIGURES[1])
        added = _rects(layout)
        assert added != start
        stack.undo()
        assert _rects(layout) == start
        assert _words(layout) == FIGURES[:1]
        stack.redo()
        assert _rects(layout) == added
        assert remove_callout(layout, 0)
        stack.undo()
        assert _words(layout) == FIGURES[:2]
        assert _rects(layout) == added

    def test_each_change_is_one_undo_step(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES[:1])
        stack = layout.undoStack().stack()
        before = stack.index()
        add_callout(layout, *FIGURES[1])
        assert stack.index() == before + 1


class TestScopedToTheLayout:
    def test_editing_one_layout_leaves_another(self, parcels, project) -> None:
        first = _signal(parcels, project, FIGURES[:2], layout_name="first")
        second = _signal(parcels, project, FIGURES[:2], layout_name="second")
        before = _rects(second), _words(second)
        add_callout(first, *FIGURES[2])
        change_callout(first, 0, "999", "changed")
        remove_callout(first, 1)
        assert (_rects(second), _words(second)) == before
        assert project.customVariables().get("nika_figure_1") is None

    def test_a_layout_not_from_signal_has_no_callouts(self, parcels, project) -> None:
        classic = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="classic"
        ).layout
        assert not supports_callouts(classic)
        assert not can_add_callout(classic)
        assert not add_callout(classic, "1", "x")


class TestOlderProjects:
    def test_shared_variables_become_the_layouts_own(self, parcels, project) -> None:
        project.setCustomVariables(
            {
                "nika_figure_1": "214 | parcels surveyed",
                "nika_figure_2": "12 km | of road",
                "nika_figure_3": "5 | not on any page",
            }
        )
        layout = _signal(parcels, project, FIGURES[:2], layout_name="old")
        project.setCustomVariables(
            {
                "nika_figure_1": "214 | parcels surveyed",
                "nika_figure_2": "12 km | of road",
                "nika_figure_3": "5 | not on any page",
            }
        )
        before = _rects(layout), _words(layout)
        migrate_project_figures(project)
        assert (_rects(layout), _words(layout)) == before
        variables = project.customVariables()
        assert "nika_figure_1" not in variables
        assert "nika_figure_2" not in variables
        # One no layout shows is kept for the next Signal page.
        assert variables["nika_figure_3"] == "5 | not on any page"

    def test_the_first_new_signal_page_takes_the_variables_once(
        self, parcels, project
    ) -> None:
        project.setCustomVariables({"nika_figure_1": "214 | parcels surveyed"})
        first = cartograph_from_view(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id="dashboard",
            layout_name="a",
        ).layout
        assert _words(first) == [("214", "parcels surveyed")]
        assert project_figures(project) == ()
        second = cartograph_from_view(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id="dashboard",
            layout_name="b",
        ).layout
        assert _words(second) == []

    def test_a_new_project_shows_no_statistics(self, parcels, project) -> None:
        layout = cartograph_from_view(
            _view(parcels), UTM, [parcels], project, template_id="dashboard"
        ).layout
        assert _words(layout) == []
        record = template_record(layout)
        assert record is not None and record.figures == ()
        assert all(
            not isinstance(i, QgsLayoutItemMap) or i.id() for i in _items(layout)
        )
