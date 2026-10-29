"""Live count, driven the way a user does: Add item, click the page, set it up
in the Edit tab, then change the map and the layers and read the number the
page shows. Nothing here asks which method produced a number; it waits (with a
bound) for the page to show the right one.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

import pytest
from live_count_fixtures import (
    WHOLE,
    build_page,
    count_sites,
)
from test_cartograph_panel_widgets import _settle
from test_preview_item_editing import _choose, _click_page, _items_of

pytest.importorskip("qgis.core")
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QRectF, QTimer

from nika_onlymap_exporter.core import live_count as lc
from nika_onlymap_exporter.ui.cartograph.layout_page import PagePane

DASH = "\N{EM DASH}"


def _wait_for(qgis_app, check, what: str, seconds: float = 6.0):
    """Run the event loop until `check()` is truthy; fail loudly otherwise."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qgis_app.processEvents()
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _number_shows(qgis_app, number, text: str, seconds: float = 6.0) -> None:
    _wait_for(
        qgis_app,
        lambda: number.text() == text,
        f"the number to read {text!r} (reads {number.text()!r})",
        seconds,
    )


def _pick(combo, data) -> None:
    """Choose an entry the way a click on it does."""
    index = combo.findData(data)
    assert index >= 0, f"no entry {data!r} in {combo.accessibleName()}"
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


def _numbers(layout) -> list:
    return lc.live_counts(layout)


@pytest.fixture
def ui(qgis_app, project):
    page = build_page(project)
    pane = PagePane()
    pane.resize(1000, 760)
    pane.show()
    pane.show_layout(page.layout)
    pane.controls.resize(420, 640)
    pane.controls.show()
    _settle(qgis_app)
    yield page, pane
    _settle(qgis_app, 0.2)
    pane.live_counts.close()
    pane.close()
    pane.deleteLater()


def _add(qgis_app, pane, page, at=(170.0, 30.0), follow: str | None = None):
    """Add item > Live count (for the map called `follow`, when the page has
    several and none is selected), then a click on the page; returns the
    number."""
    before = {n.uuid() for n in _numbers(page.layout)}
    if follow is None:
        _choose(pane, "Live count")
    else:
        _choose(pane, "Live count", f"Follow {follow}")
    _click_page(qgis_app, pane, *at)
    _settle(qgis_app, 0.15)
    new = [n for n in _numbers(page.layout) if n.uuid() not in before]
    assert len(new) == 1
    return new[0]


def _setup(pane):
    return pane.controls.live_setup


def _configured(qgis_app, pane, page):
    """A counter on the page counting all the sites, shown."""
    number = _add(qgis_app, pane, page)
    _pick(_setup(pane).layer_combo, page.sites.id())
    _number_shows(qgis_app, number, "30")
    return number


class TestCreating:
    def test_the_menu_entry_has_its_description(self, ui) -> None:
        _page, pane = ui
        pane._fill_add_menu()
        action = next(a for a in pane.add_menu.actions() if a.text() == "Live count")
        assert action.isEnabled()
        assert action.toolTip() == "Count what's currently shown on the map."

    def test_a_page_without_a_map_explains_and_cannot_add(
        self, qgis_app, project
    ) -> None:
        page = build_page(project)
        page.layout.removeLayoutItem(page.frame)
        pane = PagePane()
        pane.show()
        pane.show_layout(page.layout)
        pane._fill_add_menu()
        action = next(a for a in pane.add_menu.actions() if a.text() == "Live count")
        assert not action.isEnabled()
        assert action.toolTip()
        pane.close()

    def test_with_several_maps_the_user_chooses_which_one_to_follow(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        detail = QgsLayoutItemMap(page.layout)
        detail.setId("Detail map")
        page.layout.addLayoutItem(detail)
        detail.attemptSetSceneRect(QRectF(140, 60, 40, 40))
        detail.setCrs(page.frame.crs())
        detail.zoomToExtent(QgsRectangle(-0.5, -0.5, 4.5, 4.5))
        _settle(qgis_app, 0.1)
        page.layout.deselectAll()
        pane._fill_add_menu()
        entry = next(a for a in pane.add_menu.actions() if a.text() == "Live count")
        assert entry.menu() is not None
        names = [a.text() for a in entry.menu().actions()]
        assert any("Main map" in n for n in names)
        assert any("Detail map" in n for n in names)
        # With one map selected, that map is followed without asking.
        page.layout.deselectAll()
        detail.setSelected(True)
        pane._fill_add_menu()
        entry = next(a for a in pane.add_menu.actions() if a.text() == "Live count")
        assert entry.menu() is None
        number = None
        entry.trigger()
        _click_page(qgis_app, pane, 200.0, 150.0)
        _settle(qgis_app, 0.15)
        number = _numbers(page.layout)[0]
        assert lc.read_spec(number).map_uuid == detail.uuid()
        assert _setup(pane).following_label.isVisibleTo(pane.controls) is True
        assert _setup(pane).following_combo.currentText() == "Detail map"

    def test_a_new_counter_is_selected_and_its_setup_is_open(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _add(qgis_app, pane, page)
        # The counter is selected as one item (the group of its parts).
        assert page.layout.selectedLayoutItems() == [number.parentGroup()]
        assert pane.controls.selected_item() is number
        setup = _setup(pane)
        assert setup.isVisibleTo(pane.controls)
        assert not pane.controls.text_edit.isVisibleTo(pane.controls)
        assert setup.following_text.text() == "Following: Main map"
        assert pane.controls.heading.text() == "Selected: Live count"
        # Nothing is chosen for the user; the number says there is none yet.
        assert setup.layer_combo.currentIndex() == -1
        assert number.text() == DASH
        _wait_for(qgis_app, lambda: setup.message.text(), "the instruction")
        assert "layer" in setup.message.text().lower()

    def test_it_is_a_number_a_caption_and_a_rule(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _add(qgis_app, pane, page)
        parts = lc.parts_of(page.layout, number)
        assert len(parts) == 2
        assert lc.caption_of(page.layout, number) is not None
        top = number.sceneBoundingRect().top()
        caption = lc.caption_of(page.layout, number)
        assert caption.sceneBoundingRect().top() >= top
        assert number.textFormat().size() > caption.textFormat().size()

    def test_deleting_the_number_takes_the_whole_counter(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _add(qgis_app, pane, page)
        counter = {number.uuid(), number.parentGroup().uuid()} | {
            part.uuid() for part in lc.parts_of(page.layout, number)
        }
        before = {i.uuid() for i in _items_of(page.layout, QgsLayoutItem)}
        pane.controls.delete_selected()
        _settle(qgis_app, 0.1)
        assert _numbers(page.layout) == []
        after = {i.uuid() for i in _items_of(page.layout, QgsLayoutItem)}
        assert before - after == counter

    def test_two_counters_get_two_names(self, qgis_app, ui) -> None:
        page, pane = ui
        first = _add(qgis_app, pane, page, (170.0, 30.0))
        second = _add(qgis_app, pane, page, (170.0, 90.0))
        assert first.id() != second.id()


class TestSettingItUp:
    def test_choosing_a_layer_shows_its_count_and_a_caption(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        assert lc.caption_of(page.layout, number).text() == "Monitoring sites"
        assert _setup(pane).summary.text() == "Monitoring sites"
        assert _setup(pane).following_text.text() == "Following: Main map"

    def test_the_number_cannot_be_typed_over_in_the_edit_tab(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        _configured(qgis_app, pane, page)
        assert not pane.controls.text_edit.isVisibleTo(pane.controls)

    def test_matching_a_value(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        setup = _setup(pane)
        assert not setup.field_combo.isVisibleTo(pane.controls)
        _pick(setup.mode_combo, lc.MODE_VALUE)
        assert setup.field_combo.isVisibleTo(pane.controls)
        assert setup.value_combo.isVisibleTo(pane.controls)
        _pick(setup.field_combo, "status")
        _pick(setup.value_combo, "Active")
        _number_shows(qgis_app, number, str(count_sites(status="Active")))
        assert "status: Active" in setup.summary.text()
        # The empty values are offered apart from each other.
        offered = [
            setup.value_combo.itemText(i) for i in range(setup.value_combo.count())
        ]
        assert offered[0] == "(no value)" and "Closed" in offered
        setup.value_combo.setCurrentIndex(0)
        setup.value_combo.activated.emit(0)
        _number_shows(qgis_app, number, str(count_sites(status=None)))

    def test_a_typed_value_is_matched(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        setup = _setup(pane)
        _pick(setup.mode_combo, lc.MODE_VALUE)
        _pick(setup.field_combo, "cap")
        line = setup.value_combo.lineEdit()
        line.setText("5")
        line.setModified(True)
        line.editingFinished.emit()
        _number_shows(qgis_app, number, str(count_sites(cap=5)))
        line.setText("lots")
        line.setModified(True)
        line.editingFinished.emit()
        _number_shows(qgis_app, number, DASH)
        _wait_for(qgis_app, lambda: "lots" in setup.message.text(), "the explanation")

    def test_a_typed_value_still_applies_after_a_yes_no_field_came_and_went(
        self, qgis_app, ui
    ) -> None:
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType

        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.sites.dataProvider().addAttributes([QgsField("ok", QMetaType.Type.Bool)])
        page.sites.updateFields()
        setup = _setup(pane)
        _pick(setup.mode_combo, lc.MODE_VALUE)
        _pick(setup.field_combo, "ok")  # the value box stops being editable
        assert not setup.value_combo.isEditable()
        _pick(setup.field_combo, "cap")  # and becomes editable again
        assert setup.value_combo.isEditable()
        line = setup.value_combo.lineEdit()
        line.setText("5")
        line.setModified(True)
        line.editingFinished.emit()
        _number_shows(qgis_app, number, str(count_sites(cap=5)))
        assert count_sites(cap=5) != 30

    def test_a_layer_the_map_does_not_draw_is_offered_with_a_note_and_counts_zero(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        page.frame.setKeepLayerSet(True)
        page.frame.setLayers([page.squares])
        number = _add(qgis_app, pane, page)
        setup = _setup(pane)
        entry = setup.layer_combo.itemText(setup.layer_combo.findData(page.sites.id()))
        assert "not shown on this map" in entry
        _pick(setup.layer_combo, page.sites.id())
        _number_shows(qgis_app, number, "0")

    def test_the_caption_is_editable(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        setup = _setup(pane)
        setup.caption_edit.setText("Active monitoring sites")
        setup.caption_edit.setModified(True)
        setup.caption_edit.editingFinished.emit()
        assert lc.caption_of(page.layout, number).text() == "Active monitoring sites"

    def test_selecting_the_caption_shows_the_same_setup(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        caption = lc.caption_of(page.layout, number)
        page.layout.deselectAll()
        centre = caption.sceneBoundingRect().center()
        _click_page(qgis_app, pane, centre.x(), centre.y())
        pane.controls.refresh()
        assert _setup(pane).isVisibleTo(pane.controls)
        assert _setup(pane).number() is number

    def test_an_ordinary_text_still_shows_its_words(self, qgis_app, ui) -> None:
        page, pane = ui
        label = QgsLayoutItemLabel(page.layout)
        label.setText("Hello")
        page.layout.addLayoutItem(label)
        label.attemptSetSceneRect(QRectF(150, 150, 40, 10))
        page.layout.deselectAll()
        label.setSelected(True)
        pane.controls.refresh()
        assert pane.controls.text_edit.isVisibleTo(pane.controls)
        assert pane.controls.text_edit.toPlainText() == "Hello"
        assert not _setup(pane).isVisibleTo(pane.controls)


class TestFollowingThePreview:
    def test_panning_and_zooming_the_map_updates_the_number(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 5, 5))
        _number_shows(qgis_app, number, str(count_sites(page.frame.extent())))
        assert number.text() != "30"
        page.frame.zoomToExtent(WHOLE)
        _number_shows(qgis_app, number, "30")

    def test_hiding_and_showing_the_layer(self, qgis_app, ui, project) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        node = project.layerTreeRoot().findLayer(page.sites.id())
        node.setItemVisibilityChecked(False)
        _number_shows(qgis_app, number, "0")
        node.setItemVisibilityChecked(True)
        _number_shows(qgis_app, number, "30")

    def test_hiding_a_category(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        renderer = page.sites.renderer()
        renderer.updateCategoryRenderState(
            renderer.categoryIndexForValue("Closed"), False
        )
        page.sites.emitStyleChanged()  # what the Layers panel does on a click
        _number_shows(qgis_app, number, str(30 - count_sites(status="Closed")))
        renderer.updateCategoryRenderState(
            renderer.categoryIndexForValue("Closed"), True
        )
        page.sites.emitStyleChanged()
        _number_shows(qgis_app, number, "30")

    def test_a_layer_filter(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.sites.setSubsetString("\"status\" = 'Active'")
        _number_shows(qgis_app, number, str(count_sites(status="Active")))
        page.sites.setSubsetString("")
        _number_shows(qgis_app, number, "30")

    def test_editing_the_data(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        assert page.sites.startEditing()
        feature = QgsFeature(page.sites.fields())
        feature.setAttributes(["New", "Active", 1])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5, 5)))
        assert page.sites.addFeature(feature)
        _number_shows(qgis_app, number, "31")
        page.sites.rollBack()
        _number_shows(qgis_app, number, "30")

    def test_a_counter_on_each_of_two_maps(self, qgis_app, ui) -> None:
        page, pane = ui
        detail = QgsLayoutItemMap(page.layout)
        detail.setId("Detail map")
        page.layout.addLayoutItem(detail)
        detail.attemptSetSceneRect(QRectF(140, 60, 40, 40))
        detail.setCrs(page.frame.crs())
        detail.zoomToExtent(QgsRectangle(-0.5, -0.5, 4.5, 4.5))
        overview = _add(qgis_app, pane, page, follow="Main map")
        _pick(_setup(pane).layer_combo, page.sites.id())
        close = _add(qgis_app, pane, page, (200.0, 150.0), follow="Detail map")
        setup = _setup(pane)
        assert setup.following_combo.currentText() == "Detail map"
        _pick(setup.layer_combo, page.sites.id())
        _number_shows(qgis_app, close, str(count_sites(detail.extent())))
        assert overview.text() == "30"
        # Moving only the detail map moves only its counter.
        detail.zoomToExtent(QgsRectangle(3.5, 3.5, 10.5, 8.5))
        _number_shows(qgis_app, close, str(count_sites(detail.extent())))
        assert overview.text() == "30"

    def test_a_rotated_map_does_not_count_the_corners_of_its_rectangle(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.frame.setMapRotation(45)
        _wait_for(qgis_app, lambda: number.text() != "30", "the rotated count")
        assert 0 < int(number.text()) < 30


class TestLongCounts:
    @pytest.fixture
    def crowded(self, qgis_app, ui):
        """A layer far larger than one slice of work, so a count takes many."""
        page, pane = ui
        from qgis.core import QgsVectorLayer

        layer = QgsVectorLayer("Point?crs=EPSG:4326&field=n:integer", "Crowd", "memory")
        features = []
        for i in range(20000):
            feature = QgsFeature(layer.fields())
            feature.setAttributes([i])
            feature.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY((i % 200) / 10.0, (i // 200) / 10.0))
            )
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        return page, pane, layer

    def test_the_interface_stays_alive_while_it_counts(
        self, qgis_app, crowded, monkeypatch
    ) -> None:
        page, pane, layer = crowded
        from nika_onlymap_exporter.ui.cartograph import live_counts

        monkeypatch.setattr(live_counts, "SLICE_SECONDS", 0.0)
        monkeypatch.setattr(lc, "BATCH", 50)
        number = _add(qgis_app, pane, page)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(1)
        _pick(_setup(pane).layer_combo, layer.id())
        _wait_for(qgis_app, lambda: number.text().isdigit(), "the count", 30.0)
        timer.stop()
        assert len(ticks) > 3
        page.frame.zoomToExtent(QgsRectangle(0, 0, 10, 10))
        shown = page.frame.extent()
        expected = sum(
            1
            for i in range(20000)
            if shown.contains(QgsPointXY((i % 200) / 10.0, (i // 200) / 10.0))
        )
        _number_shows(qgis_app, number, str(expected), 30.0)

    def test_an_older_calculation_never_overwrites_a_newer_one(
        self, qgis_app, crowded, monkeypatch
    ) -> None:
        page, pane, layer = crowded
        from nika_onlymap_exporter.ui.cartograph import live_counts

        monkeypatch.setattr(live_counts, "SLICE_SECONDS", 0.0)
        monkeypatch.setattr(lc, "BATCH", 50)
        number = _add(qgis_app, pane, page)
        _pick(_setup(pane).layer_combo, layer.id())
        _wait_for(qgis_app, lambda: number.text().isdigit(), "the first count", 30.0)
        seen: list[str] = []
        # Ask for one view, and while its count is under way, another.
        page.frame.zoomToExtent(QgsRectangle(0, 0, 4, 4))
        _wait_for(qgis_app, lambda: pane.live_counts.running(), "the count to start")
        page.frame.zoomToExtent(QgsRectangle(10, 10, 14, 14))
        final = page.frame.extent()
        expected = sum(
            1
            for i in range(20000)
            if final.contains(QgsPointXY((i % 200) / 10.0, (i // 200) / 10.0))
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            not pane.live_counts.busy() and number.text() == str(expected)
        ):
            qgis_app.processEvents()
            seen.append(number.text())
            time.sleep(0.005)
        assert number.text() == str(expected)
        assert not pane.live_counts.busy()


class TestInvalidSources:
    def test_a_removed_layer_gives_a_dash_and_says_why(
        self, qgis_app, ui, project
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        project.removeMapLayer(page.sites.id())
        _number_shows(qgis_app, number, DASH)
        setup = _setup(pane)
        _wait_for(
            qgis_app,
            lambda: "Monitoring sites" in setup.message.text(),
            "the explanation",
        )
        assert setup.layer_combo.currentIndex() == -1

    def test_a_removed_map_gives_a_dash(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.layout.removeLayoutItem(page.frame)
        _number_shows(qgis_app, number, DASH)

    def test_a_field_that_goes_away_gives_a_dash(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        setup = _setup(pane)
        _pick(setup.mode_combo, lc.MODE_VALUE)
        _pick(setup.field_combo, "cap")
        line = setup.value_combo.lineEdit()
        line.setText("5")
        line.setModified(True)
        line.editingFinished.emit()
        _number_shows(qgis_app, number, str(count_sites(cap=5)))
        assert page.sites.startEditing()
        assert page.sites.deleteAttribute(page.sites.fields().lookupField("cap"))
        _number_shows(qgis_app, number, DASH)
        _wait_for(qgis_app, lambda: "cap" in setup.message.text(), "the explanation")
        page.sites.rollBack()


class TestUndoRedo:
    def test_undo_and_redo_restore_the_setup_and_the_number_together(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        setup = _setup(pane)
        _pick(setup.mode_combo, lc.MODE_VALUE)
        _pick(setup.field_combo, "status")
        _pick(setup.value_combo, "Closed")
        _number_shows(qgis_app, number, str(count_sites(status="Closed")))
        stack = page.layout.undoStack().stack()
        stack.undo()
        # The value is gone again: the counter is not set up, not stale.
        _number_shows(qgis_app, number, DASH)
        assert lc.read_spec(number).value is None
        stack.redo()
        _number_shows(qgis_app, number, str(count_sites(status="Closed")))
        assert lc.read_spec(number).value == "Closed"
        # Undo back past the layer choice: nothing to count.
        for _ in range(4):
            stack.undo()
        _number_shows(qgis_app, number, DASH)
        assert lc.read_spec(number).layer_id == ""
        assert not _setup(pane).layer_combo.currentText()

    def test_a_calculated_number_is_not_an_undo_step(self, qgis_app, ui) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        stack = page.layout.undoStack().stack()
        index = stack.index()
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 5, 5))
        _number_shows(qgis_app, number, str(count_sites(page.frame.extent())))
        assert stack.index() == index


class TestSavingAndReopening:
    def test_everything_comes_back_after_reopening_the_project(
        self, qgis_app, project, tmp_path
    ) -> None:
        page = build_page(project, folder=tmp_path)
        pane = PagePane()
        pane.resize(1000, 760)
        pane.show()
        pane.show_layout(page.layout)
        pane.controls.show()
        _settle(qgis_app)
        number = _add(qgis_app, pane, page)
        setup = _setup(pane)
        _pick(setup.layer_combo, page.sites.id())
        _pick(setup.mode_combo, lc.MODE_VALUE)
        _pick(setup.field_combo, "status")
        _pick(setup.value_combo, "Closed")
        setup.caption_edit.setText("Closed sites")
        setup.caption_edit.setModified(True)
        setup.caption_edit.editingFinished.emit()
        _number_shows(qgis_app, number, str(count_sites(status="Closed")))
        saved = lc.read_spec(number)
        pane.live_counts.close()
        pane.close()
        path = str(tmp_path / "count.qgz")
        assert project.write(path)
        project.clear()
        assert project.read(path)
        layout = project.layoutManager().layoutByName("Count page")
        assert layout is not None
        (reopened,) = lc.live_counts(layout)
        assert lc.read_spec(reopened) == saved
        assert lc.caption_of(layout, reopened).text() == "Closed sites"
        again = PagePane()
        again.resize(1000, 760)
        again.show()
        again.show_layout(layout)
        again.controls.show()
        _settle(qgis_app, 0.3)
        assert reopened.text() == str(count_sites(status="Closed"))
        layout.deselectAll()
        reopened.parentGroup().setSelected(True)
        again.controls.refresh()
        shown = again.controls.live_setup
        assert shown.layer_combo.currentText() == "Monitoring sites"
        assert shown.mode_combo.currentData() == lc.MODE_VALUE
        assert shown.field_combo.currentData() == "status"
        assert shown.value_combo.currentText() == "Closed"
        assert shown.caption_edit.text() == "Closed sites"
        again.live_counts.close()
        again.close()

    def test_a_number_saved_for_another_view_is_corrected_on_opening(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        pane.live_counts.close()
        number.setText("999")  # what a file saved earlier might hold
        pane.show_layout(None)
        pane.live_counts.set_layout(page.layout)
        _number_shows(qgis_app, number, "30")


class TestLeavingThePage:
    def test_the_html_carries_the_number_of_the_exported_view(
        self, qgis_app, ui, project
    ) -> None:
        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.packaging.runtime_manager import RuntimeBundle

        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 5, 5))
        expected = str(count_sites(page.frame.extent()))
        runtime = RuntimeBundle(
            javascript=b"// js",
            css=b"/* css */",
            version="0.12.0",
            sha256="0" * 64,
            cartograph_javascript=b"// cartograph js",
            cartograph_css=b"/* cartograph css */",
        )
        # No event loop has run since the view changed: the export itself must
        # bring the number up to date.
        assert number.text() == "30"
        html = export_layout_to_cartograph(
            page.layout,
            project,
            runtime,
            FidelityReportBuilder(),
            frame_modes={page.frame.uuid(): "static"},
            static_dpi=60,
        )
        assert f">{expected}<" in html
        assert ">30<" not in html

    def test_the_pdf_is_made_from_the_number_of_the_printed_view(
        self, qgis_app, ui, tmp_path
    ) -> None:
        from nika_onlymap_exporter.core.print_settings import PrintSettings
        from nika_onlymap_exporter.exporters.print_pdf import export_print_pdf

        page, pane = ui
        number = _configured(qgis_app, pane, page)
        page.frame.zoomToExtent(QgsRectangle(-1, -1, 5, 5))
        assert number.text() == "30"
        result = export_print_pdf(
            page.layout, PrintSettings(), tmp_path / "page.pdf", None
        )
        assert result.path.exists() and result.path.stat().st_size > 1000
        assert number.text() == str(count_sites(page.frame.extent()))


class TestExistingFiguresAreUntouched:
    def test_a_signal_page_with_manual_figures_opens_with_its_text_as_it_was(
        self, qgis_app, project
    ) -> None:
        from cartograph_fixtures import UTM, build_parcels_layer

        from nika_onlymap_exporter.core.canvas_cartograph import cartograph_from_view

        project.setTitle("Survey")
        parcels = build_parcels_layer()
        project.addMapLayer(parcels)
        extent = parcels.extent()
        extent.grow(200)
        figures = [("214", "parcels surveyed"), ("12 km", "of road")]
        layout = cartograph_from_view(
            extent,
            UTM,
            [parcels],
            project,
            template_id="dashboard",
            figures=figures,
        ).layout
        before = {
            i.id(): i.text()
            for i in _items_of(layout, QgsLayoutItemLabel)
            if i.id().startswith("figure-")
        }
        assert before["figure-0"] == "214"
        pane = PagePane()
        pane.show()
        pane.show_layout(layout)
        _settle(qgis_app, 0.3)
        after = {
            i.id(): i.text()
            for i in _items_of(layout, QgsLayoutItemLabel)
            if i.id().startswith("figure-")
        }
        assert after == before
        assert lc.live_counts(layout) == []
        # They are still ordinary, editable text.
        layout.deselectAll()
        next(
            i for i in _items_of(layout, QgsLayoutItemLabel) if i.id() == "figure-0"
        ).setSelected(True)
        pane.controls.refresh()
        assert pane.controls.text_edit.isVisibleTo(pane.controls)
        assert pane.controls.text_edit.toPlainText() == "214"
        pane.live_counts.close()
        pane.close()


def _drag_on_page(qgis_app, pane, start_mm, end_mm, steps: int = 14) -> None:
    """Drag from one point of the page to another with the real mouse, along
    a smooth path."""
    from qgis.PyQt.QtCore import QPoint, QPointF, Qt
    from qgis.PyQt.QtTest import QTest
    from test_cartograph_panel_widgets import _move_held

    view = pane.preview.view
    viewport = view.viewport()
    first = view.mapFromScene(QPointF(*start_mm))
    last = view.mapFromScene(QPointF(*end_mm))
    QTest.mouseMove(viewport, first)
    qgis_app.processEvents()
    QTest.mousePress(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first
    )
    for step in range(1, steps + 1):
        point = QPoint(
            first.x() + (last.x() - first.x()) * step // steps,
            first.y() + (last.y() - first.y()) * step // steps,
        )
        # Sent with the button held: Qt 5's QTest.mouseMove has none.
        _move_held(viewport, point)
        qgis_app.processEvents()
    QTest.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, last
    )
    _settle(qgis_app, 0.1)


def _corner(item) -> tuple[float, float]:
    rect = item.sceneBoundingRect()
    return round(rect.x(), 3), round(rect.y(), 3)


# Qt 5 keeps layout positions in single precision, so a part can land a
# thousandth of a millimetre from its sibling; a real drift is far larger.
POSITION_TOLERANCE_MM = 0.02


def _moved_together(before: dict, moved: dict) -> tuple[float, float]:
    """The one shift every part made, failing if the parts moved apart."""
    shifts = [(moved[u][0] - before[u][0], moved[u][1] - before[u][1]) for u in before]
    for dx, dy in shifts:
        assert abs(dx - shifts[0][0]) <= POSITION_TOLERANCE_MM, shifts
        assert abs(dy - shifts[0][1]) <= POSITION_TOLERANCE_MM, shifts
    return shifts[0]


class TestOneItemOnThePage:
    """A Live count is one thing to the user: its three parts show together
    and move together."""

    def test_a_counter_is_not_hidden_by_something_already_on_the_page(
        self, qgis_app, ui
    ) -> None:
        from qgis.core import QgsLayoutExporter
        from qgis.PyQt.QtCore import QRect, QSize
        from qgis.PyQt.QtGui import QColor

        page, pane = ui
        # A template's legend or notes box: opaque, and above what a new item
        # is put on by default.
        cover = QgsLayoutItemLabel(page.layout)
        cover.setText("")
        page.layout.addLayoutItem(cover)
        cover.attemptSetSceneRect(QRectF(150.0, 10.0, 80.0, 120.0))
        cover.setBackgroundEnabled(True)
        cover.setBackgroundColor(QColor("#ff00ff"))
        cover.setZValue(100.0)
        number = _add(qgis_app, pane, page, at=(170.0, 30.0))
        _pick(_setup(pane).layer_combo, page.sites.id())
        _number_shows(qgis_app, number, "30")
        page.layout.deselectAll()
        scale = 4
        image = QgsLayoutExporter(page.layout).renderPageToImage(
            0, QSize(297 * scale, 210 * scale)
        )
        box = number.sceneBoundingRect()
        area = QRect(
            int(box.x() * scale),
            int(box.y() * scale),
            int(box.width() * scale),
            int(box.height() * scale),
        )
        seen = {
            image.pixelColor(x, y).name()
            for x in range(area.left(), area.right(), 2)
            for y in range(area.top(), area.bottom(), 2)
        }
        assert seen - {"#ff00ff"}, "the number is hidden behind the other item"

    def test_dragging_the_number_moves_the_caption_and_the_rule_with_it(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        parts = [number, *lc.parts_of(page.layout, number)]
        before = {p.uuid(): _corner(p) for p in parts}
        stack = page.layout.undoStack().stack()
        steps = stack.count()
        spot = number.sceneBoundingRect().center()
        _drag_on_page(
            qgis_app, pane, (spot.x(), spot.y()), (spot.x() + 40.0, spot.y() + 25.0)
        )
        moved = {p.uuid(): _corner(p) for p in parts}
        dx, dy = _moved_together(before, moved)
        assert dx > 10.0 and dy > 5.0
        # One drag, one undo step: undoing it puts every part back.
        assert stack.count() == steps + 1
        stack.undo()
        _settle(qgis_app, 0.1)
        assert {p.uuid(): _corner(p) for p in parts} == before

    def test_dragging_the_caption_moves_the_whole_counter_too(
        self, qgis_app, ui
    ) -> None:
        page, pane = ui
        number = _configured(qgis_app, pane, page)
        caption = lc.caption_of(page.layout, number)
        parts = [number, *lc.parts_of(page.layout, number)]
        before = {p.uuid(): _corner(p) for p in parts}
        spot = caption.sceneBoundingRect().center()
        _drag_on_page(
            qgis_app, pane, (spot.x(), spot.y()), (spot.x() - 30.0, spot.y() + 20.0)
        )
        moved = {p.uuid(): _corner(p) for p in parts}
        dx, dy = _moved_together(before, moved)
        assert (round(dx, 2), round(dy, 2)) != (0.0, 0.0)

    def test_the_parts_stay_together_after_saving_and_reopening(
        self, qgis_app, project, tmp_path
    ) -> None:
        page = build_page(project, folder=tmp_path)
        pane = PagePane()
        pane.resize(1000, 760)
        pane.show()
        pane.show_layout(page.layout)
        pane.controls.show()
        _settle(qgis_app)
        number = _add(qgis_app, pane, page)
        _pick(_setup(pane).layer_combo, page.sites.id())
        _number_shows(qgis_app, number, "30")
        spot = number.sceneBoundingRect().center()
        _drag_on_page(
            qgis_app, pane, (spot.x(), spot.y()), (spot.x() + 30.0, spot.y() + 20.0)
        )
        shape = {
            p.uuid(): _corner(p) for p in [number, *lc.parts_of(page.layout, number)]
        }
        pane.live_counts.close()
        pane.close()
        path = str(tmp_path / "count.qgz")
        assert project.write(path)
        project.clear()
        assert project.read(path)
        layout = project.layoutManager().layoutByName("Count page")
        (reopened,) = lc.live_counts(layout)
        parts = [reopened, *lc.parts_of(layout, reopened)]
        reopened_shape = {p.uuid(): _corner(p) for p in parts}
        assert reopened_shape.keys() == shape.keys()
        assert all(
            abs(reopened_shape[u][i] - shape[u][i]) <= POSITION_TOLERANCE_MM
            for u in shape
            for i in (0, 1)
        )
        # Moving the reopened counter still moves all of it.
        before = {p.uuid(): _corner(p) for p in parts}
        group = reopened.parentGroup()
        x, y = _corner(group)
        group.attemptMove(QgsLayoutPoint(x + 10.0, y + 10.0, layout.units()))
        after = {p.uuid(): _corner(p) for p in parts}
        assert len({(after[u][0] - before[u][0]) for u in before}) == 1
