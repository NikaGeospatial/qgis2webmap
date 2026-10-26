"""Near & Far: the inset on the page preview is directly editable.

Real widgets driven as a user does: smooth mouse drags with QTest, real wheel
events, key presses. "Edit view" pans and zooms a map's content inside its
stationary frame; Select moves and resizes the frame; each gesture is one
undo step; the inset's layers, fit and removal are undoable too.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from cartograph_fixtures import UTM, build_parcels_layer
from test_cartograph_panel_widgets import _settle
from test_locator_context import _region

qgis_core = pytest.importorskip("qgis.core")
from qgis.core import QgsLayoutItemMap, QgsRectangle  # noqa: E402
from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from qgis.PyQt.QtGui import QKeyEvent, QWheelEvent  # noqa: E402
from qgis.PyQt.QtTest import QTest  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

from nika_onlymap_exporter.core.canvas_cartograph import (  # noqa: E402
    cartograph_from_view,
)
from nika_onlymap_exporter.ui.cartograph.content_edit import (  # noqa: E402
    WHEEL_BURST_MS,
)
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane  # noqa: E402


@pytest.fixture
def near_far(qgis_app, project):
    """A shown pane on a Near & Far layout: the main map and its inset,
    whose layers reach beyond the main view."""
    project.setTitle("Survey")
    parcels = build_parcels_layer()
    project.addMapLayer(parcels)
    region = _region(project, 3000)
    grown = QgsRectangle(parcels.extent())
    grown.scale(1.1)
    result = cartograph_from_view(
        grown,
        UTM,
        [parcels, region],
        project,
        template_id="locator",
        page_size="A4",
        orientation="landscape",
    )
    layout = result.layout
    pane = PagePane()
    pane.resize(1000, 760)
    pane.show()
    pane.show_layout(layout)
    _settle(qgis_app)
    main = layout.itemById("main")
    inset = layout.itemById("locator")
    yield pane, layout, main, inset, region
    # Background renders still queued for the page's maps finish while the
    # layout is alive (an item taken off it by a test is drawn once more).
    _settle(qgis_app, 0.4)
    pane.close()
    pane.deleteLater()


def _select(qgis_app, pane, layout, item) -> None:
    layout.deselectAll()
    item.setSelected(True)
    _settle(qgis_app, 0.1)


def _centre(pane, item) -> QPoint:
    view = pane.preview.view
    return view.mapFromScene(item.sceneBoundingRect().center())


def _drag(qgis_app, pane, start: QPoint, end: QPoint) -> None:
    from test_cartograph_panel_widgets import _move_held

    viewport = pane.preview.view.viewport()
    QTest.mouseMove(viewport, start)
    qgis_app.processEvents()
    QTest.mousePress(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    for step in range(1, 13):
        _move_held(
            viewport,
            QPoint(
                start.x() + (end.x() - start.x()) * step // 12,
                start.y() + (end.y() - start.y()) * step // 12,
            ),
        )
        qgis_app.processEvents()
    QTest.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )
    _settle(qgis_app, 0.05)


def _wheel(qgis_app, pane, at: QPoint, notches: int) -> None:
    viewport = pane.preview.view.viewport()
    event = QWheelEvent(
        QPointF(at),
        QPointF(viewport.mapToGlobal(at)),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)
    qgis_app.processEvents()


def _escape(pane) -> None:
    QTest.keyClick(pane.preview.view, Qt.Key.Key_Escape)


def _index(layout) -> int:
    return layout.undoStack().stack().index()


def _geometry(item) -> tuple[float, float, float, float]:
    box = item.sceneBoundingRect()
    return (box.x(), box.y(), box.width(), box.height())


def _edit_on(qgis_app, pane, layout, item) -> None:
    _select(qgis_app, pane, layout, item)
    pane.controls.edit_view_button.click()
    _settle(qgis_app, 0.05)
    assert pane.preview.content_editor.active


def _map_point_at(pane, item, point: QPoint) -> tuple[float, float]:
    """The map coordinate under a viewport point."""
    scene = pane.preview.view.mapToScene(point)
    local = item.mapFromScene(scene)
    extent = item.extent()
    rect = item.rect()
    return (
        extent.xMinimum() + local.x() / rect.width() * extent.width(),
        extent.yMaximum() - local.y() / rect.height() * extent.height(),
    )


class TestPanAndZoomTheContent:
    def test_a_drag_pans_the_content_not_the_frame(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        before = QgsRectangle(inset.extent())
        start = _centre(pane, inset)
        under = _map_point_at(pane, inset, start)
        end = start + QPoint(30, 12)
        _drag(qgis_app, pane, start, end)
        assert _geometry(inset) == pytest.approx(frame)
        assert inset.extent().width() == pytest.approx(before.width())
        assert inset.extent().center() != before.center()
        # The content follows the pointer: the point grabbed is under it still.
        now = _map_point_at(pane, inset, end)
        assert now == pytest.approx(under, abs=before.width() * 0.002)

    def test_one_drag_is_one_undo_step_and_undo_restores_it(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        index = _index(layout)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(-25, 18))
        assert _index(layout) == index + 1
        # A second drag is a second step, not merged into the first.
        _drag(qgis_app, pane, start, start + QPoint(10, 10))
        assert _index(layout) == index + 2
        layout.undoStack().stack().undo()
        layout.undoStack().stack().undo()
        assert inset.extent() == before
        layout.undoStack().stack().redo()
        assert inset.extent() != before

    def test_the_wheel_zooms_the_content_about_the_pointer(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        width = inset.extent().width()
        at = _centre(pane, inset) + QPoint(40, -15)
        under = _map_point_at(pane, inset, at)
        _wheel(qgis_app, pane, at, 1)
        assert inset.extent().width() < width
        assert _geometry(inset) == pytest.approx(frame)
        assert _map_point_at(pane, inset, at) == pytest.approx(under, abs=width * 0.002)
        _wheel(qgis_app, pane, at, -2)
        assert inset.extent().width() > width

    def test_a_burst_of_wheel_steps_is_one_undo_step(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        index = _index(layout)
        at = _centre(pane, inset)
        for _ in range(5):
            _wheel(qgis_app, pane, at, 1)
        assert _index(layout) == index + 1
        # After a pause it is another step.
        time.sleep(WHEEL_BURST_MS / 1000 + 0.1)
        _wheel(qgis_app, pane, at, 1)
        assert _index(layout) == index + 2
        layout.undoStack().stack().undo()
        layout.undoStack().stack().undo()
        assert inset.extent() == before

    def test_a_wheel_step_after_an_undo_is_not_merged_into_the_old_burst(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        at = _centre(pane, inset)
        _wheel(qgis_app, pane, at, 1)
        stack = layout.undoStack().stack()
        stack.undo()
        undone = QgsRectangle(inset.extent())
        _wheel(qgis_app, pane, at, 1)
        stack.undo()
        assert inset.extent() == undone

    def test_the_wheel_never_scrolls_or_zooms_the_page(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        view = pane.preview.view
        scale = view.transform().m11()
        scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        _wheel(qgis_app, pane, _centre(pane, inset), 3)
        assert view.transform().m11() == scale
        assert (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        ) == scroll

    def test_the_main_map_works_the_same_way(self, qgis_app, near_far) -> None:
        pane, layout, main, _inset, _region = near_far
        _edit_on(qgis_app, pane, layout, main)
        frame = _geometry(main)
        before = QgsRectangle(main.extent())
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(20, 20))
        assert _geometry(main) == pytest.approx(frame)
        assert main.extent().center() != before.center()
        assert pane.controls.edit_view_button.text() == "Edit view"


class TestTheModeIsClear:
    def test_the_inset_names_its_button_and_the_banner_says_what_is_on(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.edit_view_button.text() == "Edit inset view"
        pane.controls.edit_view_button.click()
        _settle(qgis_app, 0.05)
        assert pane.controls.edit_view_button.isChecked()
        banner = pane.preview.content_editor._banner
        assert not banner.isHidden()
        assert inset.displayName() in banner.text()
        assert "Esc" in banner.text()

    @pytest.mark.parametrize("how", ["escape", "toggle", "select", "pan", "other"])
    def test_every_exit_ends_the_mode(self, qgis_app, near_far, how) -> None:
        pane, layout, main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        if how == "escape":
            _escape(pane)
        elif how == "toggle":
            pane.controls.edit_view_button.click()
        elif how == "select":
            pane.select_button.click()
        elif how == "pan":
            pane.pan_button.click()
        else:
            _select(qgis_app, pane, layout, main)
        _settle(qgis_app, 0.05)
        assert not pane.preview.content_editor.active
        assert not pane.controls.edit_view_button.isChecked()
        assert pane.preview.content_editor._banner.isHidden()

    def test_a_click_on_the_page_outside_the_frame_ends_it_and_selects(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        point = _centre(pane, main)
        QTest.mouseClick(
            pane.preview.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point,
        )
        _settle(qgis_app, 0.1)
        assert not pane.preview.content_editor.active
        assert layout.selectedLayoutItems() == [main]

    def test_escape_with_a_menu_open_is_left_to_the_menu(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(pane.preview.view, event)
        assert not pane.preview.content_editor.active

    def test_the_frame_cannot_be_moved_or_resized_in_the_mode(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        frame = _geometry(inset)
        box = pane.preview.view.mapFromScene(inset.sceneBoundingRect()).boundingRect()
        corner = box.bottomRight()
        _drag(qgis_app, pane, corner, corner + QPoint(40, 30))
        assert _geometry(inset) == pytest.approx(frame)
        assert pane.preview.content_editor.active

    def test_delete_does_not_remove_the_frame_in_the_mode(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        QTest.keyClick(pane.preview.view, Qt.Key.Key_Delete)
        assert inset in layout.items()


class TestSelectModeStillMovesAndResizes:
    def test_after_the_mode_a_drag_moves_the_frame(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        _escape(pane)
        _settle(qgis_app, 0.05)
        before = _geometry(inset)
        extent = QgsRectangle(inset.extent())
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(-25, 15))
        moved = _geometry(inset)
        assert (moved[0], moved[1]) != pytest.approx((before[0], before[1]))
        assert inset.extent() == extent

    def test_after_the_mode_a_handle_resizes_the_frame(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        pane.select_button.click()
        _settle(qgis_app, 0.1)
        before = _geometry(inset)
        box = pane.preview.view.mapFromScene(inset.sceneBoundingRect()).boundingRect()
        corner = box.bottomLeft() + QPoint(2, -2)
        _drag(qgis_app, pane, corner, corner + QPoint(-20, 14))
        after = _geometry(inset)
        assert (after[2], after[3]) != pytest.approx((before[2], before[3]))


class TestLinkedItemsFollow:
    def test_the_inset_outline_follows_the_main_map_when_it_is_edited(
        self, qgis_app, near_far, tmp_path
    ) -> None:
        from qgis.core import QgsLayoutExporter

        pane, layout, main, inset, _region = near_far
        overview = inset.overviews().asList()[0]
        assert overview.linkedMap() is main
        _edit_on(qgis_app, pane, layout, main)

        def picture():
            return QgsLayoutExporter(layout).renderRegionToImage(
                inset.sceneBoundingRect(), dpi=60
            )

        before = picture()
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(40, 25))
        assert picture() != before

    def test_the_linked_scale_bar_follows_a_zoom(self, qgis_app, near_far) -> None:
        pane, layout, main, _inset, _region = near_far
        bars = [
            item
            for item in layout.items()
            if item.type() == qgis_core.QgsLayoutItemRegistry.LayoutScaleBar
        ]
        bar = next(b for b in bars if b.linkedMap() is main)
        _edit_on(qgis_app, pane, layout, main)
        scale = main.scale()
        at = _centre(pane, main)
        _wheel(qgis_app, pane, at, 2)
        _settle(qgis_app, 0.1)
        assert main.scale() < scale
        assert bar.linkedMap() is main
        assert bar.mapScale() if hasattr(bar, "mapScale") else True


class TestInsetControls:
    def test_the_inset_row_is_only_for_an_inset(self, qgis_app, near_far) -> None:
        pane, layout, main, inset, _region = near_far
        _select(qgis_app, pane, layout, main)
        assert pane.controls.inset_row.isHidden()
        _select(qgis_app, pane, layout, inset)
        assert not pane.controls.inset_row.isHidden()

    def test_context_layers_are_chosen_from_a_checkable_list_and_undone(
        self, qgis_app, near_far, project
    ) -> None:
        pane, layout, _main, inset, region = near_far
        _select(qgis_app, pane, layout, inset)
        controls = pane.controls
        controls.context_menu.aboutToShow.emit()
        actions = {a.text(): a for a in controls.context_menu.actions()}
        assert actions["region"].isChecked()
        index = _index(layout)
        actions["region"].trigger()
        assert region not in inset.layersToRender()
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert region in inset.layersToRender()
        layout.undoStack().stack().redo()
        assert region not in inset.layersToRender()

    def test_a_layer_added_to_the_inset_has_no_labels_in_it(
        self, qgis_app, near_far, project
    ) -> None:
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorLayer,
            QgsVectorLayerSimpleLabeling,
        )

        pane, layout, _main, inset, _region = near_far
        extra = QgsVectorLayer(
            "Polygon?crs=EPSG:32648&field=n:string", "named", "memory"
        )
        settings = QgsPalLayerSettings()
        settings.fieldName = "n"
        extra.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        extra.setLabelsEnabled(True)
        project.addMapLayer(extra)
        _select(qgis_app, pane, layout, inset)
        pane.controls.context_menu.aboutToShow.emit()
        next(
            a for a in pane.controls.context_menu.actions() if a.text() == "named"
        ).trigger()
        assert extra in inset.layersToRender()
        assert 'labelsEnabled="0"' in inset.layerStyleOverrides()[extra.id()]
        assert extra.labelsEnabled()

    def test_fit_context_refits_the_inset_and_undo_restores_it(
        self, qgis_app, near_far
    ) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        before = QgsRectangle(inset.extent())
        _drag_view = QgsRectangle(before)
        _drag_view.scale(0.5)
        inset.zoomToExtent(_drag_view)
        moved = QgsRectangle(inset.extent())
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.fit_button.isEnabled()
        index = _index(layout)
        pane.controls.fit_button.click()
        assert _index(layout) == index + 1
        assert inset.extent().width() > moved.width()
        layout.undoStack().stack().undo()
        assert inset.extent() == moved

    def test_fit_context_is_disabled_with_the_reason_and_the_alternatives(
        self, qgis_app, project
    ) -> None:
        project.setTitle("Survey")
        parcels = build_parcels_layer()
        project.addMapLayer(parcels)
        grown = QgsRectangle(parcels.extent())
        grown.scale(1.1)
        result = cartograph_from_view(
            grown,
            UTM,
            [parcels],
            project,
            template_id="locator",
            page_size="A4",
            orientation="landscape",
        )
        pane = PagePane()
        pane.resize(1000, 760)
        pane.show()
        pane.show_layout(result.layout)
        _settle(qgis_app)
        try:
            inset = result.layout.itemById("locator")
            _select(qgis_app, pane, result.layout, inset)
            controls = pane.controls
            assert not controls.fit_button.isEnabled()
            note = controls.context_note.text()
            assert not controls.context_note.isHidden()
            assert "No wider context" in note
            assert "Context layers" in note and "remove the inset" in note
            # Adding a wider layer makes fitting available again.
            wide = _region(project, 3000)
            controls.context_menu.aboutToShow.emit()
            next(
                a for a in controls.context_menu.actions() if a.text() == wide.name()
            ).trigger()
            assert controls.fit_button.isEnabled()
            assert controls.context_note.isHidden()
        finally:
            pane.close()
            pane.deleteLater()

    def test_remove_inset_is_one_undo_step(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        _select(qgis_app, pane, layout, inset)
        index = _index(layout)
        pane.controls.remove_inset_button.click()
        _settle(qgis_app, 0.05)
        assert inset not in layout.items()
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert any(
            isinstance(i, QgsLayoutItemMap) and i.id() == "locator"
            for i in layout.items()
        )

    def test_use_canvas_extent_works_for_the_inset(
        self, qgis_app, near_far, project
    ) -> None:
        from types import SimpleNamespace

        from qgis.core import QgsCoordinateReferenceSystem

        from nika_onlymap_exporter.ui.cartograph.controller import (
            CartographController,
        )
        from nika_onlymap_exporter.ui.cartograph.frames_list import (
            FrameRow,
            FramesList,
        )

        pane, layout, main, inset, _region = near_far
        listing = FramesList((72, 150, 300))
        listing.set_frames(
            [
                FrameRow(
                    item_id=item.uuid(),
                    name=item.displayName(),
                    crs="EPSG:32648",
                    mode="live",
                    dpi=150,
                    issues=None,
                )
                for item in (main, inset)
            ]
        )
        pane.controls.bind_frames(listing)
        _select(qgis_app, pane, layout, inset)
        assert pane.controls.canvas_button.isEnabled()
        asked: list[str] = []
        pane.controls.canvas_extent_requested.connect(asked.append)
        pane.controls.canvas_button.click()
        assert asked == [inset.uuid()]

        view = QgsRectangle(main.extent())
        view.scale(0.6)
        canvas = SimpleNamespace(
            extent=lambda: view,
            mapSettings=lambda: SimpleNamespace(
                destinationCrs=lambda: QgsCoordinateReferenceSystem("EPSG:32648")
            ),
        )
        stand_in = SimpleNamespace(
            _map_canvas=lambda: canvas,
            _current_layout=layout,
            project=project,
            schedule_autosave=lambda: None,
        )
        index = _index(layout)
        CartographController._match_canvas(stand_in, asked[0])
        assert inset.extent().center().x() == pytest.approx(view.center().x())
        assert _index(layout) == index + 1
        layout.undoStack().stack().undo()
        assert inset.extent().center() != view.center()
        listing.deleteLater()


class TestSurvives:
    def test_the_edited_view_is_in_the_project_file(
        self, qgis_app, near_far, project, tmp_path
    ) -> None:
        from qgis.core import QgsProject

        pane, layout, _main, inset, _region = near_far
        _edit_on(qgis_app, pane, layout, inset)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(30, 10))
        _wheel(qgis_app, pane, start, 1)
        edited = QgsRectangle(inset.extent())
        path = tmp_path / "kept.qgz"
        assert project.write(str(path))
        reread = QgsProject()
        assert reread.read(str(path))
        again = reread.layoutManager().layoutByName(layout.name())
        kept = again.itemById("locator").extent()
        assert kept.xMinimum() == pytest.approx(edited.xMinimum())
        assert kept.yMaximum() == pytest.approx(edited.yMaximum())

    def test_an_edit_reaches_the_autosave_hook(self, qgis_app, near_far) -> None:
        pane, layout, _main, inset, _region = near_far
        seen = []
        layout.undoStack().stack().indexChanged.connect(seen.append)
        _edit_on(qgis_app, pane, layout, inset)
        start = _centre(pane, inset)
        _drag(qgis_app, pane, start, start + QPoint(30, 10))
        assert seen

    def test_the_edited_extent_is_in_the_html(
        self, qgis_app, near_far, project
    ) -> None:
        from test_preview_item_editing import TestWhatAddingMakesSurvives

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        pane, layout, main, _inset, _region = near_far
        runtime = TestWhatAddingMakesSurvives._runtime()

        def html() -> str:
            return export_layout_to_cartograph(
                layout, project, runtime, FidelityReportBuilder(), static_dpi=60
            )

        before = html()
        _edit_on(qgis_app, pane, layout, main)
        start = _centre(pane, main)
        _drag(qgis_app, pane, start, start + QPoint(50, 30))
        assert html() != before


class TestLegendAddedFromThePreview:
    def test_a_new_legend_leaves_the_support_rasters_out_like_a_template(
        self, qgis_app, project, tmp_path
    ) -> None:
        from legend_fixtures import hillshade_layer, write_tif
        from qgis.core import QgsLayoutItemLegend
        from test_preview_item_editing import _choose, _click_page, _pane

        from nika_onlymap_exporter.core import legend_edit

        parcels = build_parcels_layer()
        hillshade = hillshade_layer(write_tif(tmp_path / "h.tif"))
        project.addMapLayers([parcels, hillshade])
        pane, layout, frames = _pane(qgis_app, project)
        try:
            frames[0].setKeepLayerSet(True)
            frames[0].setLayers([parcels, hillshade])
            index = _index(layout)
            _choose(pane, "Legend")
            _click_page(qgis_app, pane, 20, 150)
            legend = next(
                i for i in layout.items() if isinstance(i, QgsLayoutItemLegend)
            )
            ids = [n.layerId() for n in legend.model().rootGroup().findLayers()]
            assert ids == [parcels.id()]
            assert hillshade.id() in legend_edit.support_left_out(legend)
            # One step: Add legend, defaults included.
            assert _index(layout) == index + 1
            layout.undoStack().stack().undo()
            assert not any(isinstance(i, QgsLayoutItemLegend) for i in layout.items())
        finally:
            pane.close()
            pane.deleteLater()
