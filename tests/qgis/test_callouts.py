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
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core.canvas_cartograph import (
    cartograph_from_view,
    project_figures,
    template_record,
)
from nika_onlymap_exporter.ui.cartograph.callouts import (
    add_callout,
    change_callout,
    layout_callouts,
    migrate_project_figures,
    remove_callout,
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


def _place(layout, figure, x=20.0, y=20.0):
    return add_callout(layout, QRectF(x, y, 50, 30), *figure)


def _plain_layout(project):
    from test_cartograph_panel_widgets import _a4_layout

    return _a4_layout(project)


class TestAddChangeRemove:
    def test_a_callout_goes_on_any_layout(self, parcels, project) -> None:
        for layout in (
            _plain_layout(project),
            cartograph_from_view(
                _view(parcels), UTM, [parcels], project, template_id="classic"
            ).layout,
            _signal(parcels, project, FIGURES[:1], layout_name="signal"),
        ):
            before = len(layout_callouts(layout))
            assert _place(layout, ("214", "parcels surveyed"))
            made = layout_callouts(layout)
            assert len(made) == before + 1
            assert (made[-1].value, made[-1].label) == ("214", "parcels surveyed")

    def test_it_lands_where_it_was_placed_and_is_visible(self, project) -> None:
        layout = _plain_layout(project)
        assert _place(layout, ("214", "parcels"), x=40.0, y=60.0)
        value = layout.itemById("figure-0")
        line = layout.itemById("figure-0-label")
        assert value.pos().x() == pytest.approx(40.0, abs=0.5)
        assert value.pos().y() > 60.0
        assert line.pos().y() > value.pos().y()
        assert value.textFormat().size() > line.textFormat().size()

    def test_adding_moves_nothing_else(self, parcels, project) -> None:
        layout = _signal(parcels, project, [], layout_name="steady")
        before = _rects(layout)
        assert _place(layout, FIGURES[0])
        after = _rects(layout)
        assert {k: v for k, v in after.items() if k in before} == before

    def test_there_is_no_limit(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES)
        for n in range(5):
            assert _place(layout, (str(n), "more"), y=20.0 + 20 * n)
        assert len(layout_callouts(layout)) == 8

    def test_a_blank_one_is_refused(self, project) -> None:
        layout = _plain_layout(project)
        assert not _place(layout, ("  ", "nothing"))
        assert _words(layout) == []

    def test_removing_takes_only_that_one(self, parcels, project) -> None:
        layout = _signal(parcels, project, FIGURES, layout_name="three")
        others = {k: v for k, v in _rects(layout).items() if "figure-1" not in k}
        assert remove_callout(layout, 1)
        assert _words(layout) == [FIGURES[0], FIGURES[2]]
        assert layout.itemById("figure-1") is None
        assert layout.itemById("figure-2").text() == "9"
        assert _rects(layout) == others

    def test_the_freed_number_is_used_again(self, project) -> None:
        layout = _plain_layout(project)
        for figure in FIGURES:
            assert _place(layout, figure)
        assert remove_callout(layout, 1)
        assert _place(layout, ("5", "bridges"))
        assert sorted(c.value for c in layout_callouts(layout)) == sorted(
            ["214", "9", "5"]
        )

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

    def test_a_line_can_be_added_to_a_callout_without_one(self, project) -> None:
        layout = _plain_layout(project)
        assert _place(layout, ("9", ""))
        assert layout.itemById("figure-0-label") is None
        assert change_callout(layout, 0, "9", "wells")
        assert _words(layout) == [("9", "wells")]

    def test_undo_and_redo_each_step(self, project) -> None:
        layout = _plain_layout(project)
        stack = layout.undoStack().stack()
        assert _place(layout, FIGURES[0])
        assert stack.index() == 1
        assert _place(layout, FIGURES[1], y=80.0)
        stack.undo()
        assert _words(layout) == FIGURES[:1]
        stack.redo()
        assert _words(layout) == FIGURES[:2]
        assert remove_callout(layout, 0)
        stack.undo()
        assert _words(layout) == FIGURES[:2]


class TestScopedToTheLayout:
    def test_editing_one_layout_leaves_another(self, parcels, project) -> None:
        first = _signal(parcels, project, FIGURES[:2], layout_name="first")
        second = _signal(parcels, project, FIGURES[:2], layout_name="second")
        before = _rects(second), _words(second)
        _place(first, FIGURES[2])
        change_callout(first, 0, "999", "changed")
        remove_callout(first, 1)
        assert (_rects(second), _words(second)) == before
        assert project.customVariables().get("nika_figure_1") is None


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


class TestExportAndImport:
    @pytest.fixture
    def runtime(self):
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        return RuntimeBundle(
            javascript=b"// core",
            css=b"/* core */",
            version="0.13.1",
            sha256="0" * 64,
            cartograph_javascript=b"// carto",
            cartograph_css=b"/* carto */",
        )

    @staticmethod
    def _html(layout, project, runtime) -> str:
        from nika_onlymap_exporter.core.cartograph_export import (
            finish_cartograph,
            prepare_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        report = FidelityReportBuilder()
        return finish_cartograph(
            prepare_cartograph(
                layout, project, runtime, report, frame_modes={}, raster_cache=None
            ),
            project,
            report,
        )

    def test_the_html_follows_the_edits(self, parcels, project, runtime) -> None:
        layout = _signal(parcels, project, FIGURES[:1])
        assert ">214<" in self._html(layout, project, runtime)
        remove_callout(layout, 0)
        _place(layout, ("215", "parcels surveyed"))
        _place(layout, ("77", "culverts"), y=90.0)
        html = self._html(layout, project, runtime)
        assert ">215<" in html and ">77<" in html and ">culverts<" in html
        assert ">214<" not in html
        remove_callout(layout, 0)
        html = self._html(layout, project, runtime)
        assert ">215<" not in html and ">77<" in html

    def test_an_imported_cartograph_keeps_its_callouts(
        self, parcels, project, runtime, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.layout_builder import import_cartograph_file

        layout = _signal(parcels, project, FIGURES)
        path = tmp_path / "signal.html"
        path.write_text(self._html(layout, project, runtime))
        built = import_cartograph_file(path, project)
        assert _words(built.layout) == FIGURES
        assert change_callout(built.layout, 0, "215", "parcels surveyed")
        assert remove_callout(built.layout, 1)
        assert _words(built.layout) == [("215", "parcels surveyed"), FIGURES[2]]
        assert built.layout.itemById("label_figure_2").text() == "9"
        assert built.layout.itemById("label_figure_1") is None
