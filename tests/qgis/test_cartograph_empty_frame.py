"""A map frame whose last layer was removed stays empty.

QGIS reads an empty layer list as "follow the project's visible layers", so
removing the final layer used to bring every project layer back. The frame must
stay empty everywhere a user can see it: the preview, the inclusion list, the
Live count, the image and PDF, and the HTML - and through undo, redo and a
save and reopen. Frames are independent of one another, and a project layer
the frame never held must not appear.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

# ruff: noqa: E402, F811

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from live_count_fixtures import add_counter, build_page, count_of
from qgis.core import QgsLayoutExporter, QgsLayoutItemMap, QgsPrintLayout
from qgis.PyQt.QtCore import QRectF
from test_cartograph_layer_membership import (  # noqa: F401 - fixtures
    BLUE,
    GREEN,
    PARCELS,
    PEAKS,
    RED,
    TRAILS,
    _add_map,
    _click_include,
    _count_colour,
    _item,
    _region,
    _ticked,
    dialog,
    runtime,
    world,
)

from nika_onlymap_exporter.core import live_count as lc
from nika_onlymap_exporter.core.cartograph_export import export_layout_to_cartograph
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.frame_layers import (
    is_deliberately_empty,
    rendered_layers,
    set_frame_layers,
)
from nika_onlymap_exporter.ui.cartograph.layer_membership import set_layer_included

ALL = (PEAKS, TRAILS, PARCELS)


def _drawn_names(map_item) -> list[str]:
    return [layer.name() for layer in rendered_layers(map_item)]


def _raw_qgis_layers(map_item) -> list[str]:
    """What QGIS itself renders for the frame."""
    return [layer.name() for layer in map_item.layersToRender()]


def _paint(layout: QgsPrintLayout, item: QgsLayoutItemMap) -> dict[str, int]:
    """How many pixels of each layer's colour the frame shows on the page."""
    image = _region(layout, item)
    return {
        "red": _count_colour(image, RED),
        "blue": _count_colour(image, BLUE),
        "green": _count_colour(image, GREEN),
    }


def _empty_the_frame(dialog, names=ALL) -> None:
    for name in names:
        _click_include(dialog, name)


def _html(world, runtime) -> str:
    return export_layout_to_cartograph(
        world.layout, world.project, runtime, FidelityReportBuilder()
    )


def _reopen(world, tmp_path):
    """Save the project to disk and load it again; the layout and its frames
    come back as new objects."""
    path = str(tmp_path / "reopened.qgz")
    layout_name = world.layout.name()
    assert world.project.write(path)
    world.project.clear()
    assert world.project.read(path)
    layout = world.project.layoutManager().layoutByName(layout_name)
    frames = {
        item.id(): item for item in layout.items() if isinstance(item, QgsLayoutItemMap)
    }
    return layout, frames


class TestRemovingTheLastLayer:
    def test_the_frame_stays_empty_and_shows_nothing(
        self, world, dialog, runtime
    ) -> None:
        _empty_the_frame(dialog)

        # QGIS itself, as the preview and a PDF/PNG ask it.
        assert _drawn_names(world.main) == []
        # ...and QGIS itself draws no project layer for it.
        assert world.main.layersToRender() == []
        assert is_deliberately_empty(world.main)
        assert _paint(world.layout, world.main) == {"red": 0, "blue": 0, "green": 0}
        # The inclusion list.
        assert not any(_ticked(dialog, name) for name in ALL)
        assert dialog._cartograph.layers_section.summary_text() == (
            "0 of 3 layers included"
        )
        # The HTML.
        html = _html(world, runtime)
        assert not any(name in html for name in ALL)

    def test_removing_the_only_layer_of_a_frame(self, world, dialog) -> None:
        """A frame that held exactly one layer, with others in the project."""
        world.main.setKeepLayerSet(True)
        world.main.setLayers([world.layers[PEAKS]])
        dialog._cartograph.membership.layout_changed()
        assert _drawn_names(world.main) == [PEAKS]

        _click_include(dialog, PEAKS)

        assert _drawn_names(world.main) == []
        assert _paint(world.layout, world.main) == {"red": 0, "blue": 0, "green": 0}
        # The project layers it never held are untouched and still visible.
        layer_ids = {layer.id() for layer in world.project.mapLayers().values()}
        assert {layer.id() for layer in world.layers.values()} <= layer_ids

    def test_adding_a_layer_back_replaces_the_empty_state(self, world, dialog) -> None:
        _empty_the_frame(dialog)
        _click_include(dialog, TRAILS)

        assert _drawn_names(world.main) == [TRAILS]
        paint = _paint(world.layout, world.main)
        assert paint["blue"] > 0 and paint["red"] == 0 and paint["green"] == 0
        assert _ticked(dialog, TRAILS)
        assert not _ticked(dialog, PEAKS)

    def test_undo_and_redo_keep_the_empty_state(self, world, dialog) -> None:
        _empty_the_frame(dialog)
        stack = world.layout.undoStack().stack()

        stack.undo()
        assert _drawn_names(world.main) == [PARCELS]
        assert _paint(world.layout, world.main)["green"] > 0
        assert _ticked(dialog, PARCELS)

        stack.redo()
        assert _drawn_names(world.main) == []
        assert _paint(world.layout, world.main) == {"red": 0, "blue": 0, "green": 0}
        assert not any(_ticked(dialog, name) for name in ALL)

    def test_saving_and_reopening_keeps_it_empty(self, world, dialog, tmp_path) -> None:
        _empty_the_frame(dialog)
        _layout, frames = _reopen(world, tmp_path)

        assert _drawn_names(frames["main"]) == []
        assert _paint(_layout, frames["main"]) == {"red": 0, "blue": 0, "green": 0}

    def test_two_frames_keep_different_memberships(
        self, world, dialog, tmp_path
    ) -> None:
        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        dialog._cartograph.membership.layout_changed()
        world.main.setKeepLayerSet(True)
        world.main.setLayers([world.layers[PEAKS]])
        inset.setKeepLayerSet(True)
        inset.setLayers([world.layers[TRAILS], world.layers[PEAKS]])
        dialog._cartograph.membership.layout_changed()

        assert set_layer_included(
            world.layout, world.project, world.main, world.layers[PEAKS].id(), False
        )

        assert _drawn_names(world.main) == []
        assert _drawn_names(inset) == [TRAILS, PEAKS]
        assert _paint(world.layout, inset)["blue"] > 0
        _, frames = _reopen(world, tmp_path)
        assert _drawn_names(frames["main"]) == []
        assert _drawn_names(frames["inset"]) == [TRAILS, PEAKS]

    def test_a_frame_following_the_project_still_shows_it(self, world, dialog) -> None:
        """Only a frame that was emptied is empty: the other keeps following
        the Layers panel, including a layer it never chose."""
        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        _empty_the_frame(dialog)

        assert _drawn_names(world.main) == []
        assert sorted(_raw_qgis_layers(inset)) == sorted(ALL)

    def test_the_project_gains_no_layer(self, world, dialog) -> None:
        before = set(world.project.mapLayers())
        _empty_the_frame(dialog)
        assert set(world.project.mapLayers()) == before

    def test_a_users_own_layers_expression_survives_add_back(
        self, world, dialog
    ) -> None:
        """Only the empty marker we set is removed when a layer comes back."""
        from qgis.core import QgsLayoutObject, QgsProperty

        inset = _add_map(world.layout, "inset", QRectF(10, 170, 50, 30))
        mine = QgsProperty.fromExpression("'Trails'")
        inset.dataDefinedProperties().setProperty(
            QgsLayoutObject.DataDefinedProperty.MapLayers, mine
        )
        assert not is_deliberately_empty(inset)
        assert _drawn_names(inset) == [TRAILS]
        set_frame_layers(inset, [])
        assert _drawn_names(inset) == []
        set_frame_layers(inset, [world.layers[PEAKS]])
        prop = inset.dataDefinedProperties().property(
            QgsLayoutObject.DataDefinedProperty.MapLayers
        )
        assert not is_deliberately_empty(inset)
        assert not prop.isActive()

    def test_a_users_layers_expression_is_what_rendered_layers_reports(
        self, world
    ) -> None:
        from qgis.core import QgsLayoutObject, QgsProperty

        world.main.dataDefinedProperties().setProperty(
            QgsLayoutObject.DataDefinedProperty.MapLayers,
            QgsProperty.fromExpression("'Trails'"),
        )
        assert _drawn_names(world.main) == [TRAILS]

    def test_the_legend_lists_nothing(self, world, dialog) -> None:
        _empty_the_frame(dialog)
        names = [node.name() for node in world.legend.model().rootGroup().findLayers()]
        assert names == []


class TestExportedPdf:
    def _pdf_paint(self, layout, path) -> dict[str, int]:
        """Colour pixel counts of the exported PDF's first page, rasterised."""
        pdf_module = pytest.importorskip("PyQt6.QtPdf")
        from qgis.PyQt.QtCore import QSize

        result = QgsLayoutExporter(layout).exportToPdf(
            str(path), QgsLayoutExporter.PdfExportSettings()
        )
        assert result == QgsLayoutExporter.ExportResult.Success
        document = pdf_module.QPdfDocument(None)
        document.load(str(path))
        assert document.pageCount() == 1
        image = document.render(0, QSize(900, 640))
        return {
            "red": _count_colour(image, RED),
            "blue": _count_colour(image, BLUE),
            "green": _count_colour(image, GREEN),
        }

    def test_the_pdf_page_shows_no_layer_once_the_frame_is_emptied(
        self, world, dialog, tmp_path
    ) -> None:
        before = self._pdf_paint(world.layout, tmp_path / "before.pdf")
        assert all(before[colour] > 0 for colour in before)

        _empty_the_frame(dialog)

        after = self._pdf_paint(world.layout, tmp_path / "after.pdf")
        assert after == {"red": 0, "blue": 0, "green": 0}


class TestEmptyFrameAndOtherOutputs:
    def test_a_live_count_following_the_empty_frame_counts_zero(
        self, project, tmp_path
    ) -> None:
        page = build_page(project, folder=tmp_path)
        counter = add_counter(page)
        assert count_of(page, counter).count > 0  # the frame follows the project

        page.frame.setKeepLayerSet(True)
        page.frame.setLayers([page.sites])
        assert set_layer_included(
            page.layout, project, page.frame, page.sites.id(), False
        )

        outcome = count_of(page, counter)
        assert outcome.state is lc.CountState.OK
        assert outcome.count == 0
        lc.refresh_live_counts(page.layout)
        assert counter.text().strip() == "0"

        # And back: the count follows the layer again.
        set_layer_included(page.layout, project, page.frame, page.sites.id(), True)
        assert count_of(page, counter).count > 0

    def test_the_counted_state_survives_reopening(self, project, tmp_path) -> None:
        page = build_page(project, folder=tmp_path)
        counter = add_counter(page)
        page.frame.setKeepLayerSet(True)
        page.frame.setLayers([page.sites])
        set_layer_included(page.layout, project, page.frame, page.sites.id(), False)
        name = page.layout.name()
        path = str(tmp_path / "p.qgz")
        assert project.write(path)
        project.clear()
        assert project.read(path)
        layout = project.layoutManager().layoutByName(name)
        number = next(item for item in layout.items() if item.id() == "Live count")
        assert lc.evaluate(layout, number).count == 0
        assert counter is not None
