"""Signal's key figures are ordinary text and rule items: Signal draws none,
one or three well, they export to the web map as text, and an imported
cartograph brings them back as plain items.

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
)
from qgis.PyQt.QtCore import QRectF

from nika_onlymap_exporter.core.canvas_cartograph import (
    cartograph_from_view,
    template_record,
)

pytest.importorskip("qgis.core")

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


def _words(layout) -> list[tuple[str, str]]:
    """The (value, line) pairs of the figure text items, by their ids."""
    found: dict[str, str] = {}
    for item in _items(layout):
        if isinstance(item, QgsLayoutItemLabel) and item.id().startswith("figure-"):
            found[item.id()] = item.text()
    values = sorted(i for i in found if not i.endswith("-label"))
    return [(found[v], found.get(f"{v}-label", "")) for v in values]


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


class TestOlderProjects:
    """Key figures an older version kept as project variables."""

    def test_a_new_signal_page_ignores_the_variables(self, parcels, project) -> None:
        project.setCustomVariables({"nika_figure_1": "214 | parcels surveyed"})
        layout = cartograph_from_view(
            _view(parcels),
            UTM,
            [parcels],
            project,
            template_id="dashboard",
            layout_name="a",
        ).layout
        assert _words(layout) == []
        # They are not taken, changed or removed either.
        assert project.customVariables() == {"nika_figure_1": "214 | parcels surveyed"}

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

    def test_the_html_carries_the_figures_as_text(
        self, parcels, project, runtime
    ) -> None:
        layout = _signal(parcels, project, FIGURES)
        html = self._html(layout, project, runtime)
        for value, line in FIGURES:
            assert f">{value}<" in html and f">{line}<" in html

    def test_the_html_follows_edits_to_the_text(
        self, parcels, project, runtime
    ) -> None:
        layout = _signal(parcels, project, FIGURES[:1])
        layout.itemById("figure-0").setText("215")
        layout.removeLayoutItem(layout.itemById("figure-0-label"))
        html = self._html(layout, project, runtime)
        assert ">215<" in html
        assert ">214<" not in html and ">parcels surveyed<" not in html

    def test_an_imported_cartograph_brings_the_figures_back_as_text(
        self, parcels, project, runtime, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.layout_builder import import_cartograph_file

        layout = _signal(parcels, project, FIGURES)
        path = tmp_path / "signal.html"
        path.write_text(self._html(layout, project, runtime))
        built = import_cartograph_file(path, project)
        texts = {
            i.text() for i in _items(built.layout) if isinstance(i, QgsLayoutItemLabel)
        }
        for value, line in FIGURES:
            assert value in texts and line in texts
        value_item = built.layout.itemById("label_figure_2")
        assert isinstance(value_item, QgsLayoutItemLabel)
        assert value_item.text() == "9"
