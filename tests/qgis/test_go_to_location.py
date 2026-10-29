"""Map frames > Go to location...: the dialog, driven from the real ⋯ menu.

Every dialog test opens the dialog the way a user does - the Cartograph panel's
frame card, its ⋯ menu, the "Go to location…" row - and then types, clicks and
presses keys in the real widgets. What is asserted is what the user ends up
with: where the frame looks, what the undo stack and the document did, what the
dialog said.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("qgis.core")

from go_to_location_fixtures import (
    GO_TO_LOCATION,
    HOME,
    build_world,
    choose_crs,
    choose_layer,
    choose_source,
    click_row,
    fid_of,
    fill_coordinates,
    frame_centre,
    go_action,
    open_dialog,
    press,
    rows,
    search,
    type_text,
    wait_for,
    wgs_to_frame,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from test_cartograph_dialog import (
    _choose_cartograph,
    _dialog,
    picked_template,  # noqa: F401 - autouse fixture the dialog tests rely on
)
from test_cartograph_export import fake_runtime  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def fresh_session():
    from nika_onlymap_exporter.ui.cartograph import go_to_location_dialog

    go_to_location_dialog.reset_session()
    yield
    go_to_location_dialog.reset_session()


class Setup:
    """The Cartograph panel on the Canyon-like world."""

    def __init__(self, project, rotation: float = 0.0) -> None:
        self.world = build_world(project, rotation)
        self.dialog = _dialog(project)
        self.dialog.show()
        _choose_cartograph(self.dialog)
        self.cartograph = self.dialog._cartograph
        self.cartograph._autosave_timer.stop()
        self.cartograph.open_section("frames")
        self.project = project

    @property
    def stack(self):
        return self.world.layout.undoStack().stack()

    def card(self, item):
        return self.cartograph.frames_list.card(item.uuid())

    def action(self, item):
        return go_action(self.card(item))

    def open(self, script, item=None):
        return open_dialog(self.action(item or self.world.main), script)

    def quiet(self) -> None:
        """No autosave pending: a layer set the test changed must not start an
        export of a large layer behind the test."""
        QApplication.processEvents()
        self.cartograph._autosave_timer.stop()

    def close(self) -> None:
        self.cartograph._autosave_timer.stop()
        wait_for(lambda: not self.cartograph.busy, 20000, "a running save to end")
        self.dialog.close()


@pytest.fixture
def canyon(qgis_app, project):
    made = Setup(project)
    yield made
    made.close()


def _geometry_state(item):
    return (
        item.pos().x(),
        item.pos().y(),
        item.rect().width(),
        item.rect().height(),
        item.rotation(),
    )


class TestTheMenuRow:
    def test_the_row_replaces_locate_on_page(self, canyon) -> None:
        menu = canyon.card(canyon.world.main).actions_button.menu()
        texts = [a.text() for a in menu.actions() if a.isVisible()]
        assert GO_TO_LOCATION in texts
        assert "Locate on page" not in texts
        # In the place the old row held: second, after Use canvas extent.
        assert texts[:2] == ["Use canvas extent", GO_TO_LOCATION]
        assert go_action(canyon.card(canyon.world.main)).toolTip() == (
            "Center this map on coordinates or find a feature."
        )

    def test_the_dialog_names_the_map_of_the_card_it_came_from(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            seen["title"] = dialog.windowTitle()
            seen["context"] = dialog.context_label.text()
            seen["tabs"] = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]

        canyon.open(script, canyon.world.inset)
        assert seen == {
            "title": "Go to location",
            "context": "Map: Locator",
            "tabs": ["Coordinates", "Feature"],
        }

    def test_a_template_map_id_reads_as_words(self, canyon) -> None:
        # Templates name their main frame `main`; the label says "Main map".
        canyon.world.main.setId("main")
        seen = {}

        def script(dialog) -> None:
            seen["context"] = dialog.context_label.text()

        canyon.open(script, canyon.world.main)
        assert seen["context"] == "Map: Main map"


def _extent_state(item):
    box = item.extent()
    return (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())


def _near(a, b, tolerance=1e-6) -> bool:
    return all(math.isclose(x, y, abs_tol=tolerance) for x, y in zip(a, b, strict=True))


class TestCoordinates:
    def test_latitude_longitude_land_in_the_projected_frame(self, canyon) -> None:
        world = canyon.world
        before = {
            "extent": _extent_state(world.main),
            "scale": world.main.scale(),
            "geometry": _geometry_state(world.main),
            "rotation": world.main.mapRotation(),
            "inset": _extent_state(world.inset),
            "project_crs": canyon.project.crs().authid(),
            "features": world.viewpoints.featureCount(),
            "index": canyon.stack.index(),
        }
        seen = {}

        def script(dialog) -> None:
            seen["focus"] = dialog.focusWidget() is dialog.first_edit
            seen["go_at_start"] = dialog.go_button.isEnabled()
            seen["start"] = (dialog.first_edit.text(), dialog.second_edit.text())
            fill_coordinates(dialog, "36.10", "-112.10")
            seen["go_ready"] = dialog.go_button.isEnabled()
            dialog.go_button.click()

        canyon.open(script)
        assert seen["focus"], "Latitude has focus when the dialog opens"
        assert not seen["go_at_start"] and seen["start"] == ("", "")
        assert seen["go_ready"]
        # Longitude -112.10 / latitude 36.10 is about 99 km west of the UTM 12N
        # central meridian and 4.0 million metres north: a swapped pair could
        # not land anywhere near here.
        centre = frame_centre(world.main)
        assert 395_000 < centre.x() < 410_000
        assert 3_990_000 < centre.y() < 4_005_000
        expected = wgs_to_frame(-112.10, 36.10)
        assert math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 0.01
        # Only the view inside the frame changed.
        assert math.isclose(world.main.scale(), before["scale"], rel_tol=1e-9)
        assert world.main.mapRotation() == before["rotation"]
        assert _geometry_state(world.main) == before["geometry"]
        assert _extent_state(world.inset) == before["inset"]
        assert canyon.project.crs().authid() == before["project_crs"]
        assert world.viewpoints.featureCount() == before["features"]
        # One undoable step: undo restores, redo reproduces.
        assert canyon.stack.index() == before["index"] + 1
        canyon.stack.undo()
        assert _near(_extent_state(world.main), before["extent"], 1e-6)
        assert math.isclose(world.main.scale(), before["scale"], rel_tol=1e-9)
        canyon.stack.redo()
        again = frame_centre(world.main)
        assert math.hypot(again.x() - expected.x(), again.y() - expected.y()) < 0.01
        assert math.isclose(world.main.scale(), before["scale"], rel_tol=1e-9)

    def test_a_rotated_map_keeps_its_rotation_and_scale(
        self, qgis_app, project
    ) -> None:
        setup = Setup(project, rotation=30.0)
        try:
            scale = setup.world.main.scale()

            def script(dialog) -> None:
                fill_coordinates(dialog, "36.20", "-112.20")
                dialog.go_button.click()

            setup.open(script)
            assert setup.world.main.mapRotation() == 30.0
            assert math.isclose(setup.world.main.scale(), scale, rel_tol=1e-9)
            expected = wgs_to_frame(-112.20, 36.20)
            centre = frame_centre(setup.world.main)
            assert (
                math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 0.01
            )
        finally:
            setup.close()

    def test_only_the_frame_of_the_card_changes(self, canyon) -> None:
        world = canyon.world
        main_before = _extent_state(world.main)

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            dialog.go_button.click()

        canyon.open(script, world.inset)
        assert _extent_state(world.main) == main_before
        expected = wgs_to_frame(-112.10, 36.10)
        centre = frame_centre(world.inset)
        assert math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 0.01

    def test_projected_coordinates(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            choose_crs(dialog, "EPSG:32612")
            seen["labels"] = (dialog.first_label.text(), dialog.second_label.text())
            seen["hint"] = dialog.coordinate_hint.text()
            seen["names"] = (
                dialog.first_edit.accessibleName(),
                dialog.second_edit.accessibleName(),
            )
            seen["crs"] = dialog.crs_value.text()
            fill_coordinates(dialog, "401234.5", "3996543.25")
            dialog.go_button.click()

        canyon.open(script)
        assert seen["labels"] == ("Easting (X)", "Northing (Y)")
        assert seen["hint"] == "Enter easting and northing in metres."
        assert seen["names"] == seen["labels"]
        assert "WGS 84 / UTM zone 12N" in seen["crs"] and "EPSG:32612" in seen["crs"]
        centre = frame_centre(canyon.world.main)
        assert math.isclose(centre.x(), 401234.5, abs_tol=1e-6)
        assert math.isclose(centre.y(), 3996543.25, abs_tol=1e-6)

    def test_a_crs_unlike_the_project_and_the_frame(self, canyon) -> None:
        # NAD83 / UTM 12N: not the project's Web Mercator, not the frame's WGS 84 UTM.
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsPointXY,
        )

        nad83 = QgsCoordinateReferenceSystem("EPSG:26912")
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        place = QgsCoordinateTransform(
            wgs, nad83, canyon.project.transformContext()
        ).transform(QgsPointXY(-112.10, 36.10))

        def script(dialog) -> None:
            choose_crs(dialog, "EPSG:26912")
            fill_coordinates(dialog, f"{place.x():.3f}", f"{place.y():.3f}")
            dialog.go_button.click()

        canyon.open(script)
        expected = wgs_to_frame(-112.10, 36.10)
        centre = frame_centre(canyon.world.main)
        # Same ground to within the 1-2 m NAD83 / WGS 84 datum difference.
        assert math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 5.0

    def test_another_geographic_crs_keeps_latitude_longitude(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            choose_crs(dialog, "EPSG:4269")  # NAD83 geographic
            seen["labels"] = (dialog.first_label.text(), dialog.second_label.text())
            seen["crs"] = dialog.crs_value.text()
            fill_coordinates(dialog, "36.10", "-112.10")
            dialog.go_button.click()

        canyon.open(script)
        assert seen["labels"] == ("Latitude", "Longitude")
        assert "NAD83" in seen["crs"] and "EPSG:4269" in seen["crs"]
        expected = wgs_to_frame(-112.10, 36.10)
        centre = frame_centre(canyon.world.main)
        assert math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < 5.0


class TestCoordinateValidation:
    def _messages(self, canyon, first: str, second: str, script_after=None):
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, first, second)
            seen["message"] = dialog.coordinate_message.text()
            seen["go"] = dialog.go_button.isEnabled()
            seen["first"], seen["second"] = (
                dialog.first_edit.text(),
                dialog.second_edit.text(),
            )
            if script_after is not None:
                script_after(dialog, seen)

        before = _extent_state(canyon.world.main)
        index = canyon.stack.index()
        canyon.open(script)
        assert _extent_state(canyon.world.main) == before
        assert canyon.stack.index() == index
        return seen

    def test_empty_fields_disable_go_without_an_error(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.cancel_button.setFocus()
            QApplication.processEvents()
            seen["go"] = dialog.go_button.isEnabled()
            seen["message"] = dialog.coordinate_message.text()
            fill_coordinates(dialog, "36.10", "")
            seen["half_go"] = dialog.go_button.isEnabled()
            seen["half_message"] = dialog.coordinate_message.text()

        canyon.open(script)
        assert seen == {
            "go": False,
            "message": "",
            "half_go": False,
            "half_message": "",
        }

    @pytest.mark.parametrize(
        ("first", "second", "message"),
        [
            ("95", "-112.1", "Latitude must be between -90 and 90."),
            ("-90.0001", "-112.1", "Latitude must be between -90 and 90."),
            ("36.1", "181", "Longitude must be between -180 and 180."),
            ("36.1", "-180.5", "Longitude must be between -180 and 180."),
            ("abc", "-112.1", "Enter a valid latitude."),
            ("36.1", "1.2.3", "Enter a valid longitude."),
            ("36.1", "1e2", "Use decimal degrees only, for example -112.10."),
            ("36°6'N", "-112.1", "Use decimal degrees only, for example 36.10."),
            ("36.1, -112.1", "", "Enter one value in each field."),
        ],
    )
    def test_bad_input_is_explained_and_never_corrected(
        self, canyon, first, second, message
    ) -> None:
        seen = self._messages(canyon, first, second)
        assert seen["message"] == message
        assert seen["go"] is False
        # Nothing was swapped, clamped, wrapped or reformatted.
        assert (seen["first"], seen["second"]) == (first, second)

    def test_a_field_is_only_judged_once_the_user_leaves_it(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.first_edit.setFocus()
            QTest.keyClicks(dialog.first_edit, "95")
            seen["while_typing"] = dialog.coordinate_message.text()
            dialog.second_edit.setFocus()
            QApplication.processEvents()
            seen["after_leaving"] = dialog.coordinate_message.text()
            dialog.first_edit.setFocus()
            QTest.keyClicks(dialog.first_edit, "9")
            seen["typing_again"] = dialog.coordinate_message.text()

        canyon.open(script)
        assert seen["while_typing"] == ""
        assert seen["after_leaving"] == "Latitude must be between -90 and 90."
        assert seen["typing_again"] == ""

    def test_swapped_latitude_and_longitude_are_not_swapped_back(self, canyon) -> None:
        # Longitude typed into Latitude: out of range, refused, never reordered.
        seen = self._messages(canyon, "-112.10", "36.10")
        assert seen["message"] == "Latitude must be between -90 and 90."
        assert seen["go"] is False

    def test_the_users_locale_decides_the_decimal_separator(self, qgis_app) -> None:
        from qgis.PyQt.QtCore import QLocale

        from nika_onlymap_exporter.core.go_to_location import parse_decimal

        german = QLocale(QLocale.Language.German, QLocale.Country.Germany)
        english = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
        assert parse_decimal("36,10", german).value == 36.10
        assert parse_decimal("-112,5", german).value == -112.5
        # "36.10" could be a group separator there: ambiguous, so refused.
        assert parse_decimal("36.10", german).value is None
        assert parse_decimal("36.10", english).value == 36.10
        # And a comma is not a decimal point in English: "36,10" is a pair.
        assert parse_decimal("36,10", english).value is None
        for text in ("", " ", "-", "+", ".", "1,234.5", "NaN", "inf", "0x10", "1 2"):
            assert parse_decimal(text, english).value is None, text
        assert parse_decimal(" +36.1 ", english).value == 36.1
        assert parse_decimal("\u2212112.5", english).value == -112.5


class TestChangingTheCoordinateSystem:
    def test_a_valid_pair_is_converted_to_the_same_place(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            choose_crs(dialog, "EPSG:32612")
            seen["utm"] = (dialog.first_edit.text(), dialog.second_edit.text())
            seen["message"] = dialog.coordinate_message.text()
            seen["go"] = dialog.go_button.isEnabled()
            choose_crs(dialog, "EPSG:4326")
            seen["back"] = (dialog.first_edit.text(), dialog.second_edit.text())

        canyon.open(script)
        expected = wgs_to_frame(-112.10, 36.10)
        easting, northing = (float(v) for v in seen["utm"])
        assert math.hypot(easting - expected.x(), northing - expected.y()) < 0.01
        assert seen["message"] == "" and seen["go"] is True
        # And back: the same ground, to the field's own precision.
        lat, lon = (float(v) for v in seen["back"])
        assert math.isclose(lat, 36.10, abs_tol=1e-6)
        assert math.isclose(lon, -112.10, abs_tol=1e-6)

    @pytest.mark.parametrize(
        ("first", "second"),
        [("36.10", ""), ("", "-112.10"), ("95", "-112.1"), ("x", "y")],
    )
    def test_an_incomplete_or_invalid_pair_is_cleared_and_not_reinterpreted(
        self, canyon, first, second
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, first, second)
            choose_crs(dialog, "EPSG:32612")
            seen["fields"] = (dialog.first_edit.text(), dialog.second_edit.text())
            seen["message"] = dialog.coordinate_message.text()
            seen["go"] = dialog.go_button.isEnabled()

        canyon.open(script)
        assert seen["fields"] == ("", "")
        assert seen["message"] == (
            "Coordinate system changed. Enter coordinates in WGS 84 / UTM zone 12N."
        )
        assert seen["go"] is False

    def test_a_conversion_that_fails_leaves_go_disabled_and_says_why(
        self, canyon, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core import go_to_location as core

        def refuse(*_args, **_kwargs):
            raise core.TransformError("no transformation between these systems")

        monkeypatch.setattr(core, "transform_point", refuse)
        before = (_extent_state(canyon.world.main), canyon.stack.index())
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            seen["go"] = dialog.go_button.isEnabled()
            seen["message"] = dialog.coordinate_message.text()

        canyon.open(script)
        assert seen["go"] is False
        assert seen["message"] == (
            "These coordinates cannot be converted to the map's coordinate system."
        )
        assert (_extent_state(canyon.world.main), canyon.stack.index()) == before

    def test_the_chooser_is_qgis_own_and_changing_to_the_same_crs_does_nothing(
        self, canyon
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            choose_crs(dialog, "EPSG:4326")
            seen["fields"] = (dialog.first_edit.text(), dialog.second_edit.text())
            seen["go"] = dialog.go_button.isEnabled()

        canyon.open(script)
        assert seen == {"fields": ("36.10", "-112.10"), "go": True}


class TestCancel:
    def _untouched(self, canyon, dismiss, tab: int) -> None:
        world = canyon.world
        canyon.project.setDirty(False)
        before = (
            _extent_state(world.main),
            canyon.stack.index(),
            canyon.project.isDirty(),
            world.main.scale(),
        )

        def script(dialog) -> None:
            # A complete, valid destination in both tabs: nothing may apply it.
            fill_coordinates(dialog, "36.20", "-112.20")
            choose_layer(dialog, world.viewpoints)
            search(dialog, "mather")
            assert dialog.go_button.isEnabled()
            dialog.tabs.setCurrentIndex(tab)
            QApplication.processEvents()
            assert dialog.go_button.isEnabled()
            dismiss(dialog)
            QApplication.processEvents()
            assert not dialog.isVisible(), "the dialog closed"

        canyon.open(script)
        assert (
            _extent_state(world.main),
            canyon.stack.index(),
            canyon.project.isDirty(),
            world.main.scale(),
        ) == before

    @pytest.mark.parametrize("tab", [0, 1])
    def test_cancel_leaves_the_document_alone(self, canyon, tab) -> None:
        self._untouched(canyon, lambda dialog: dialog.cancel_button.click(), tab)

    @pytest.mark.parametrize("tab", [0, 1])
    def test_escape_is_cancel(self, canyon, tab) -> None:
        self._untouched(canyon, lambda dialog: press(dialog, Qt.Key.Key_Escape), tab)

    def test_typing_searching_and_switching_tabs_change_nothing(self, canyon) -> None:
        world = canyon.world
        canyon.project.setDirty(False)
        before = (
            _extent_state(world.main),
            canyon.stack.index(),
            canyon.project.isDirty(),
        )

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.20", "-112.20")
            dialog.tabs.setCurrentIndex(1)
            dialog.layer_combo.setCurrentIndex(
                dialog.layer_combo.findData(world.viewpoints.id())
            )
            type_text(dialog.query_edit, "desert")
            dialog.search_button.click()
            wait_for(lambda: dialog.results.count() == 2, what="two Desert Views")
            dialog.results.setCurrentRow(0)
            assert dialog.go_button.isEnabled()
            seen_now = (
                _extent_state(world.main),
                canyon.stack.index(),
                canyon.project.isDirty(),
            )
            assert seen_now == before
            dialog.reject()

        canyon.open(script)


def _frame_point(item, lon, lat):
    return wgs_to_frame(lon, lat)


def _centre_is(item, lon, lat, tolerance=0.05) -> bool:
    expected = wgs_to_frame(lon, lat)
    centre = frame_centre(item)
    return math.hypot(centre.x() - expected.x(), centre.y() - expected.y()) < tolerance


class TestTheFeatureTabLayers:
    def test_several_layers_and_no_memory_ask_for_a_choice(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["shown"] = dialog.layer_combo.currentText()
            seen["offered"] = [
                dialog.layer_combo.itemText(i)
                for i in range(dialog.layer_combo.count())
            ]
            seen["search"] = dialog.search_button.isEnabled()
            seen["go"] = dialog.go_button.isEnabled()

        canyon.open(script)
        assert seen["shown"] == "Choose a layer…"
        assert seen["offered"] == [
            "Choose a layer…",
            "Viewpoints",
            "Trails",
            "Areas",
            "Hidden by style",
        ]
        assert seen["search"] is False and seen["go"] is False

    def test_exactly_one_suitable_layer_is_chosen_for_the_user(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["shown"] = dialog.layer_combo.currentText()
            seen["count"] = dialog.layer_combo.count()

        canyon.open(script, canyon.world.inset)
        assert seen == {"shown": "Viewpoints", "count": 1}

    def test_rasters_tables_and_layers_the_map_does_not_draw_are_left_out(
        self, canyon
    ) -> None:
        from qgis.core import QgsRasterLayer, QgsVectorLayer

        world = canyon.world
        table = QgsVectorLayer("None?field=name:string", "A table", "memory")
        raster = QgsRasterLayer(
            "/vsimem/none.tif", "A raster"
        )  # invalid on purpose: not drawable either
        canyon.project.addMapLayer(table)
        undrawn = QgsVectorLayer(
            "Point?crs=EPSG:4326&field=n:string", "Not drawn", "memory"
        )
        canyon.project.addMapLayer(undrawn)
        world.main.setLayers([world.viewpoints, table, raster, world.trails])
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["offered"] = [
                dialog.layer_combo.itemText(i)
                for i in range(dialog.layer_combo.count())
            ]

        canyon.open(script)
        assert seen["offered"] == ["Choose a layer…", "Viewpoints", "Trails"]

    def test_a_map_without_feature_layers_says_so_and_cannot_search_or_go(
        self, canyon
    ) -> None:
        canyon.world.main.setLayers([])
        canyon.world.main.setKeepLayerSet(True)
        from nika_onlymap_exporter.core.frame_layers import set_frame_layers

        set_frame_layers(canyon.world.main, [])
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["message"] = dialog.feature_message.text()
            seen["enabled"] = (
                dialog.layer_combo.isEnabled(),
                dialog.query_edit.isEnabled(),
                dialog.search_button.isEnabled(),
                dialog.go_button.isEnabled(),
            )

        canyon.open(script)
        assert seen["message"] == "This map has no searchable feature layers."
        assert seen["enabled"] == (False, False, False, False)

    def test_the_last_layer_and_the_last_mode_are_remembered_per_frame_in_the_session(
        self, canyon
    ) -> None:
        world = canyon.world
        properties = world.main.customProperties()
        index = canyon.stack.index()
        seen = {}

        def first(dialog) -> None:
            seen["mode_first"] = dialog.tabs.currentIndex()
            choose_layer(dialog, world.trails)

        def second(dialog) -> None:
            seen["mode_second"] = dialog.tabs.currentIndex()
            seen["layer_second"] = dialog.layer_combo.currentText()

        def other_frame(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["inset_layer"] = dialog.layer_combo.currentText()

        canyon.open(first)
        canyon.open(second)
        canyon.open(other_frame, world.inset)
        assert seen["mode_first"] == 0
        assert seen["mode_second"] == 1
        assert seen["layer_second"] == "Trails"
        assert seen["inset_layer"] == "Viewpoints"
        # Nothing about it was stored in the layout.
        assert world.main.customProperties() == properties
        assert canyon.stack.index() == index


class TestSearching:
    def _search(self, canyon, layer_name, source, text, then=None, item=None):
        """Open, search `text` in `source` of the layer, record what is shown."""
        world = canyon.world
        layer = {
            "viewpoints": world.viewpoints,
            "trails": world.trails,
            "areas": world.areas,
            "styled": world.elsewhere,
        }[layer_name]
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, layer)
            if source:
                choose_source(dialog, source)
            seen["hint"] = dialog.source_hint.text()
            search(dialog, text)
            seen["rows"] = rows(dialog)
            seen["status"] = dialog.status_label.text()
            seen["go"] = dialog.go_button.isEnabled()
            seen["selected"] = dialog.results.currentRow()
            seen["message"] = dialog.feature_message.text()
            if then is not None:
                then(dialog, seen)

        canyon.open(script, item)
        return seen

    def test_the_search_in_choices_and_their_hints(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, canyon.world.viewpoints)
            seen["sources"] = [
                dialog.source_combo.itemText(i)
                for i in range(dialog.source_combo.count())
            ]
            seen["hints"] = []
            for index in range(dialog.source_combo.count()):
                dialog.source_combo.setCurrentIndex(index)
                seen["hints"].append(dialog.source_hint.text())

        canyon.open(script)
        assert seen["sources"] == [
            "Display name",
            "QGIS feature ID",
            "name",
            "station_id",
            "sid",
        ]
        assert seen["hints"] == [
            "Find features by their displayed name.",
            "Use the feature's internal QGIS ID.",
            "Search name.",
            "Search station_id.",
            "Search sid.",
        ]

    def test_a_display_name_search_is_partial_and_case_insensitive(
        self, canyon
    ) -> None:
        seen = self._search(canyon, "viewpoints", "Display name", "MATHER")
        assert seen["rows"] == [("Mather Point", "QGIS ID: 4")]
        assert seen["status"] == "1 match"

    def test_duplicate_names_are_separate_rows_with_their_own_identity(
        self, canyon
    ) -> None:
        world = canyon.world
        seen = self._search(canyon, "viewpoints", "Display name", "desert view")
        assert [r[0] for r in seen["rows"]] == ["Desert View", "Desert View"]
        assert [r[1] for r in seen["rows"]] == ["QGIS ID: 2", "QGIS ID: 3"]
        # Several results: nothing is chosen for the user.
        assert seen["selected"] == -1 and seen["go"] is False
        assert seen["message"] == "Select a feature."

        # Going to the second one lands on the second one's place.
        home = _extent_state(world.main)

        def pick_second(dialog, _seen) -> None:
            click_row(dialog, 1)
            assert dialog.go_button.isEnabled()
            assert _extent_state(world.main) == home  # a click does not move the map
            dialog.go_button.click()

        canyon.project.setDirty(False)
        self._search(canyon, "viewpoints", "Display name", "desert view", pick_second)
        assert _centre_is(world.main, -112.05, 36.15)

    def test_the_query_is_literal_not_a_pattern(self, canyon) -> None:
        found = {}
        for query in ("50%_off", "%", "_", "'", "lookout%", "o_f", "\\", "50%%"):
            found[query] = self._search(canyon, "viewpoints", "Display name", query)[
                "rows"
            ]
        assert [r[0] for r in found["50%_off"]] == ["Lookout 50%_off"]
        assert [r[0] for r in found["%"]] == ["Lookout 50%_off"]
        assert [r[0] for r in found["_"]] == ["Lookout 50%_off"]
        assert [r[0] for r in found["'"]] == ["O'Neil Overlook"]
        # A wildcard typed by the user matches nothing it would only match as a pattern.
        for query in ("lookout%", "o_f", "\\", "50%%"):
            assert found[query] == [], query

    def test_an_empty_display_name_reads_feature_and_its_id(self, canyon) -> None:
        seen = self._search(canyon, "viewpoints", "QGIS feature ID", "6")
        assert seen["rows"] == [("Feature 6", "QGIS ID: 6")]

    def test_the_qgis_feature_id_is_exact_and_whole(self, canyon) -> None:
        seen = self._search(canyon, "viewpoints", "QGIS feature ID", "7")
        assert seen["rows"] == [("Lipan Point", "QGIS ID: 7")]
        # Exactly one result is chosen for the user.
        assert seen["selected"] == 0 and seen["go"] is True
        # 1 does not find 10 or 11, and there is no feature 99.
        assert self._search(canyon, "viewpoints", "QGIS feature ID", "1")["rows"] == [
            ("Hance Trailhead", "QGIS ID: 1")
        ]
        none = self._search(canyon, "viewpoints", "QGIS feature ID", "99")
        assert none["rows"] == [] and none["status"] == "No matching features."
        assert none["go"] is False

    @pytest.mark.parametrize("text", ["7.5", "abc", "1e2", "7,0", "0x7", "7 7"])
    def test_a_whole_number_id_must_be_a_whole_number(self, canyon, text) -> None:
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, canyon.world.viewpoints)
            choose_source(dialog, "QGIS feature ID")
            type_text(dialog.query_edit, text)
            seen["message"] = dialog.feature_message.text()
            seen["search"] = dialog.search_button.isEnabled()
            press(dialog.query_edit, Qt.Key.Key_Return)
            seen["status"] = dialog.status_label.text()

        canyon.open(script)
        assert seen["message"] == "Enter a whole-number ID."
        assert seen["search"] is False
        assert seen["status"] == ""

    def test_an_attribute_id_that_differs_from_the_qgis_id(self, canyon) -> None:
        world = canyon.world
        # The attribute `sid` is 3 on the feature whose QGIS ID is 7, while
        # QGIS ID 3 is a different feature.
        by_attribute = self._search(canyon, "viewpoints", "sid", "3")
        by_qgis_id = self._search(canyon, "viewpoints", "QGIS feature ID", "3")
        assert by_attribute["rows"] == [("Lipan Point", "sid: 3 · QGIS ID: 7")]
        assert by_qgis_id["rows"] == [("Desert View", "QGIS ID: 3")]

        def go(dialog, _seen) -> None:
            dialog.go_button.click()

        self._search(canyon, "viewpoints", "sid", "3", go)
        assert _centre_is(world.main, -111.95, 36.03)

    def test_string_identifiers_exact_first(self, canyon) -> None:
        seen = self._search(canyon, "viewpoints", "station_id", "STA-013")
        assert seen["rows"] == [("Desert View", "station_id: STA-013 · QGIS ID: 2")]
        # Partial text lists every identifier that contains it.
        seen = self._search(canyon, "viewpoints", "station_id", "sta-01")
        assert [r[0] for r in seen["rows"]] == ["Desert View", "Desert View"]
        assert [r[1] for r in seen["rows"]] == [
            "station_id: STA-013 · QGIS ID: 2",
            "station_id: STA-014 · QGIS ID: 3",
        ]

    def test_no_match_keeps_the_query_editable_and_go_off(self, canyon) -> None:
        seen = {}

        def after(dialog, seen) -> None:
            seen["query"] = dialog.query_edit.text()
            seen["editable"] = (
                dialog.query_edit.isEnabled() and not dialog.query_edit.isReadOnly()
            )

        seen = self._search(canyon, "viewpoints", "Display name", "zzz", after)
        assert seen["status"] == "No matching features."
        assert seen["rows"] == [] and seen["go"] is False
        assert seen["query"] == "zzz" and seen["editable"]

    def test_the_whole_layer_is_searched_beyond_the_current_view(self, canyon) -> None:
        world = canyon.world
        before = frame_centre(world.main)

        def go(dialog, _seen) -> None:
            dialog.go_button.click()

        seen = self._search(canyon, "viewpoints", "Display name", "far away", go)
        assert seen["rows"] == [("Far Away Point", "QGIS ID: 9")]
        assert _centre_is(world.main, -110.20, 35.20)
        assert (
            math.hypot(
                frame_centre(world.main).x() - before.x(),
                frame_centre(world.main).y() - before.y(),
            )
            > 100_000
        )


def _frame_geometry(world, layer, name):
    """A feature of `layer`, in the frame's CRS, found by its name."""
    from nika_onlymap_exporter.core import go_to_location as core

    for feature in layer.getFeatures():
        if feature["name"] == name:
            return core.transform_geometry(
                feature.geometry(), layer.crs(), world.main.crs(), world.project
            )
    raise LookupError(name)


class TestNavigatingToAFeature:
    def _go(self, canyon, layer, name, fit=None, item=None):
        """Search `name` in `layer`'s display name and press Go, with the Fit
        whole feature box left alone (None) or set; returns what the box showed."""
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, layer)
            search(dialog, name)
            assert dialog.results.count() == 1, rows(dialog)
            seen["fit_enabled"] = dialog.fit_check.isEnabled()
            seen["fit_checked"] = dialog.fit_check.isChecked()
            if fit is not None:
                assert dialog.fit_check.isEnabled()
                dialog.fit_check.setChecked(fit)
            dialog.go_button.click()

        canyon.open(script, item)
        return seen

    def test_a_point_is_centred_at_the_current_scale(self, canyon) -> None:
        world = canyon.world
        scale = world.main.scale()
        size = (world.main.extent().width(), world.main.extent().height())
        seen = self._go(canyon, world.viewpoints, "Mather Point")
        assert seen == {"fit_enabled": False, "fit_checked": False}
        assert _centre_is(world.main, -112.10, 36.10)
        assert math.isclose(world.main.scale(), scale, rel_tol=1e-9)
        assert math.isclose(world.main.extent().width(), size[0], rel_tol=1e-9)
        assert math.isclose(world.main.extent().height(), size[1], rel_tol=1e-9)

    def test_a_line_is_fitted_by_default_with_a_margin(self, canyon) -> None:
        world = canyon.world
        scale = world.main.scale()
        seen = self._go(canyon, world.trails, "Bright Angel")
        assert seen == {"fit_enabled": True, "fit_checked": True}
        line = _frame_geometry(world, world.trails, "Bright Angel")
        extent = world.main.extent()
        box = line.boundingBox()
        # All of it is in view, with room around it, in the frame's own shape.
        assert extent.contains(box)
        assert (
            extent.width() >= box.width() * 1.19
            or extent.height() >= box.height() * 1.19
        )
        assert math.isclose(
            extent.width() / extent.height(), 2.0, rel_tol=1e-9
        )  # the frame is 200 x 100 mm
        assert not math.isclose(world.main.scale(), scale, rel_tol=1e-3)

    def test_a_line_unfitted_is_centred_on_the_line_at_the_current_scale(
        self, canyon
    ) -> None:
        from qgis.core import QgsGeometry, QgsPointXY

        world = canyon.world
        scale = world.main.scale()
        self._go(canyon, world.trails, "Bright Angel", fit=False)
        line = _frame_geometry(world, world.trails, "Bright Angel")
        centre = frame_centre(world.main)
        distance = line.distance(QgsGeometry.fromPointXY(QgsPointXY(centre)))
        assert distance < 1.0, "the centre is a point on the line"
        assert math.isclose(world.main.scale(), scale, rel_tol=1e-9)
        # The middle along its length.
        middle = line.interpolate(line.length() / 2).asPoint()
        assert math.hypot(centre.x() - middle.x(), centre.y() - middle.y()) < 1.0

    def test_a_polygon_is_centred_on_its_surface_not_its_centroid(self, canyon) -> None:
        from qgis.core import QgsGeometry, QgsPointXY

        world = canyon.world
        polygon = _frame_geometry(world, world.areas, "U Basin")
        assert not polygon.contains(polygon.centroid()), (
            "the fixture's U has an outside centroid"
        )
        self._go(canyon, world.areas, "U Basin", fit=False)
        centre = QgsGeometry.fromPointXY(QgsPointXY(frame_centre(world.main)))
        assert polygon.contains(centre)

    def test_a_polygon_is_fitted_with_its_margin(self, canyon) -> None:
        world = canyon.world
        seen = self._go(canyon, world.areas, "U Basin")
        assert seen["fit_checked"] is True
        polygon = _frame_geometry(world, world.areas, "U Basin")
        extent = world.main.extent()
        assert extent.contains(polygon.boundingBox())
        box = polygon.boundingBox()
        # One side of the feature fills no more than 1 / 1.2 of the frame.
        assert (
            max(box.width() / extent.width(), box.height() / extent.height())
            <= 1 / 1.19
        )

    def test_a_multipart_feature_is_fitted_whole_and_centred_in_its_largest_part(
        self, canyon
    ) -> None:
        from qgis.core import QgsGeometry, QgsPointXY

        world = canyon.world
        whole = _frame_geometry(world, world.areas, "Twin Parcels")
        self._go(canyon, world.areas, "Twin Parcels")
        assert world.main.extent().contains(whole.boundingBox())
        # Unfitted: a point inside the larger of the two parts (the U).
        self._go(canyon, world.areas, "Twin Parcels", fit=False)
        centre = QgsGeometry.fromPointXY(QgsPointXY(frame_centre(world.main)))
        u_shape = _frame_geometry(world, world.areas, "U Basin")
        assert u_shape.contains(centre)

    @pytest.mark.parametrize("rotation", [0.0, 25.0, -70.0, 90.0, 180.0])
    def test_a_fit_respects_the_maps_rotation_and_shape(
        self, qgis_app, project, rotation
    ) -> None:
        from qgis.core import QgsGeometry

        setup = Setup(project, rotation=rotation)
        try:
            world = setup.world
            scale_before = world.main.scale()
            self._go(setup, world.trails, "Bright Angel")
            line = _frame_geometry(world, world.trails, "Bright Angel")
            visible = QgsGeometry.fromQPolygonF(world.main.visibleExtentPolygon())
            # Every vertex of the line is inside what the rotated frame shows.
            assert visible.buffer(0.5, 4).contains(line), rotation
            assert world.main.mapRotation() == rotation
            assert math.isclose(
                world.main.extent().width() / world.main.extent().height(),
                2.0,
                rel_tol=1e-9,
            )
            assert not math.isclose(world.main.scale(), scale_before, rel_tol=1e-3)
        finally:
            setup.close()

    def test_the_frame_itself_never_moves_or_resizes(self, canyon) -> None:
        world = canyon.world
        before = _geometry_state(world.main)
        self._go(canyon, world.areas, "U Basin")
        assert _geometry_state(world.main) == before
        self._go(canyon, world.viewpoints, "Far Away Point")
        assert _geometry_state(world.main) == before

    def test_undo_and_redo_reproduce_the_extent_and_scale(self, canyon) -> None:
        world = canyon.world
        start = _extent_state(world.main)
        start_scale = world.main.scale()
        index = canyon.stack.index()
        self._go(canyon, world.areas, "U Basin")
        went = _extent_state(world.main)
        went_scale = world.main.scale()
        assert canyon.stack.index() == index + 1, "one undoable step"
        assert went != start
        canyon.stack.undo()
        assert _near(_extent_state(world.main), start)
        assert math.isclose(world.main.scale(), start_scale, rel_tol=1e-9)
        canyon.stack.redo()
        assert _near(_extent_state(world.main), went)
        assert math.isclose(world.main.scale(), went_scale, rel_tol=1e-9)

    def test_the_label_of_the_undo_step(self, canyon) -> None:
        self._go(canyon, canyon.world.viewpoints, "Mather Point")
        assert canyon.stack.undoText() == "Go to location"


class TestResultsAndTheirLimits:
    def _show(self, canyon, layer, source, text, then=None):
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, layer)
            if source:
                choose_source(dialog, source)
            search(dialog, text)
            seen["rows"] = rows(dialog)
            seen["status"] = dialog.status_label.text()
            seen["go"] = dialog.go_button.isEnabled()
            seen["message"] = dialog.feature_message.text()
            seen["current"] = dialog.results.currentRow()
            if then is not None:
                then(dialog, seen)

        canyon.open(script)
        return seen

    def test_exact_then_prefix_then_inside_each_alphabetical(self, canyon) -> None:
        layer = _named_layer(
            canyon,
            [
                "Hopi Point",
                "Point Imperial",
                "point",
                "Pointless",
                "Zebra Point",
                "Appoint",
            ],
        )
        seen = self._show(canyon, layer, "Display name", "POINT")
        assert [r[0] for r in seen["rows"]] == [
            "point",
            "Point Imperial",
            "Pointless",
            "Appoint",
            "Hopi Point",
            "Zebra Point",
        ]

    def test_the_best_matches_are_the_ones_shown_when_there_are_more_than_fifty(
        self, canyon
    ) -> None:
        # 60 matches inside the text, then one exact and two prefix matches added
        # after them: a plain "first 50" would miss all three.
        names = [f"Mile {i:03d} Point" for i in range(60)] + [
            "Point",
            "Point Two",
            "Point Three",
        ]
        layer = _named_layer(canyon, names)
        seen = self._show(canyon, layer, "Display name", "point")
        assert len(seen["rows"]) == 50
        assert [r[0] for r in seen["rows"][:3]] == ["Point", "Point Three", "Point Two"]
        assert seen["status"] == "Showing the first 50 matches. Refine your search."
        assert seen["go"] is False and seen["message"] == "Select a feature."

    def test_exactly_fifty_matches_are_not_reported_as_more(self, canyon) -> None:
        layer = _named_layer(canyon, [f"Camp {i:02d}" for i in range(50)])
        seen = self._show(canyon, layer, "Display name", "camp")
        assert len(seen["rows"]) == 50
        assert seen["status"] == "50 matches"

    def test_a_missing_geometry_row_is_marked_and_cannot_be_gone_to(
        self, canyon
    ) -> None:
        world = canyon.world
        index = canyon.stack.index()

        def after(dialog, seen) -> None:
            names = [r[0] for r in rows(dialog)]
            nowhere = names.index("No Location")
            click_row(dialog, nowhere)
            seen["go_on_it"] = dialog.go_button.isEnabled()
            seen["message_on_it"] = dialog.feature_message.text()
            seen["fit"] = dialog.fit_check.isEnabled()
            press(dialog.results, Qt.Key.Key_Return)  # Enter must not go either
            seen["still_open"] = dialog.isVisible()
            click_row(dialog, nowhere - 1)
            seen["go_elsewhere"] = dialog.go_button.isEnabled()
            seen["message_elsewhere"] = dialog.feature_message.text()

        seen = self._show(canyon, world.viewpoints, "station_id", "STA-02", after)
        marked = [r for r in seen["rows"] if r[0] == "No Location"]
        assert marked and "No location" in marked[0][1]
        assert seen["go_on_it"] is False
        assert seen["message_on_it"] == "This feature has no location to display."
        assert seen["fit"] is False and seen["still_open"] is True
        assert seen["go_elsewhere"] is True
        assert seen["message_elsewhere"] == ""
        assert canyon.stack.index() == index

    def test_a_lone_feature_without_a_location_is_shown_with_its_reason(
        self, canyon
    ) -> None:
        seen = self._show(
            canyon, canyon.world.viewpoints, "Display name", "no location"
        )
        assert seen["current"] == 0
        assert seen["go"] is False
        assert seen["message"] == "This feature has no location to display."

    def test_changing_anything_invalidates_the_results(self, canyon) -> None:
        world = canyon.world
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.viewpoints)
            search(dialog, "mather")
            seen["found"] = (dialog.results.currentRow(), dialog.go_button.isEnabled())
            dialog.query_edit.setFocus()
            press(dialog.query_edit, Qt.Key.Key_Backspace)  # "mathe"
            seen["after_query"] = (
                dialog.results.currentRow(),
                dialog.go_button.isEnabled(),
                dialog.results.isEnabled(),
            )
            search(dialog, "mather")
            seen["again"] = dialog.go_button.isEnabled()
            choose_source(dialog, "station_id")
            seen["after_source"] = (
                dialog.results.currentRow(),
                dialog.go_button.isEnabled(),
            )
            choose_source(dialog, "Display name")
            search(dialog, "mather")
            choose_layer(dialog, world.trails)
            seen["after_layer"] = (
                dialog.results.currentRow(),
                dialog.go_button.isEnabled(),
            )
            type_text(dialog.query_edit, "")
            seen["cleared"] = (dialog.results.count(), dialog.status_label.text())

        canyon.open(script)
        assert seen["found"] == (0, True)
        assert seen["after_query"] == (-1, False, False)
        assert seen["again"] is True
        assert seen["after_source"] == (-1, False)
        assert seen["after_layer"] == (-1, False)
        assert seen["cleared"] == (0, "")

    def test_an_old_search_never_replaces_a_newer_one(
        self, canyon, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.core import go_to_location as core

        world = canyon.world
        jobs = []

        def start(layer, plan, generation):
            job = core.SearchJob(generation)
            jobs.append((job, plan.query))
            return job

        monkeypatch.setattr(core, "start_search", start)

        def outcome_for(name):
            feature = next(
                f for f in world.viewpoints.getFeatures() if f["name"] == name
            )
            return core.SearchOutcome(
                [core.Match(world.viewpoints.id(), feature.id(), feature)]
            )

        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.viewpoints)
            type_text(dialog.query_edit, "first")
            dialog.search_button.click()
            type_text(dialog.query_edit, "second")
            dialog.search_button.click()
            (old, _), (new, _) = jobs
            seen["searching"] = dialog.status_label.text()
            # The older search finishes first: its answer is for a question no
            # longer asked.
            old.outcome = outcome_for("Lipan Point")
            old.done = True
            QTest.qWait(150)
            seen["after_old"] = (dialog.results.count(), dialog.status_label.text())
            new.outcome = outcome_for("Mather Point")
            new.done = True
            wait_for(lambda: dialog.results.count() == 1, what="the newer search")
            QTest.qWait(150)
            seen["rows"] = rows(dialog)

        canyon.open(script)
        assert seen["searching"] == "Searching…"
        assert seen["after_old"] == (0, "Searching…")
        assert seen["rows"] == [("Mather Point", "QGIS ID: 4")]

    def test_a_single_click_selects_and_never_moves_the_map(self, canyon) -> None:
        world = canyon.world
        before = _extent_state(world.main)

        def after(dialog, seen) -> None:
            click_row(dialog, 0)
            seen["moved"] = _extent_state(world.main) != before
            seen["go"] = dialog.go_button.isEnabled()
            seen["open"] = dialog.isVisible()

        seen = self._show(canyon, world.viewpoints, "Display name", "desert", after)
        assert seen["moved"] is False and seen["go"] is True and seen["open"] is True

    def test_a_double_click_selects_and_goes(self, canyon) -> None:
        world = canyon.world

        def after(dialog, seen) -> None:
            click_row(dialog, 1, double=True)

        self._show(canyon, world.viewpoints, "Display name", "desert", after)
        assert _centre_is(world.main, -112.05, 36.15)  # the second Desert View

    def test_enter_on_the_results_goes(self, canyon) -> None:
        world = canyon.world

        def after(dialog, seen) -> None:
            click_row(dialog, 0)
            dialog.results.setFocus()
            press(dialog.results, Qt.Key.Key_Return)

        self._show(canyon, world.viewpoints, "Display name", "desert", after)
        assert _centre_is(world.main, -111.98, 36.12)

    def test_arrow_keys_move_the_selection_and_do_not_go(self, canyon) -> None:
        world = canyon.world
        home = _extent_state(world.main)
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.viewpoints)
            search(dialog, "desert")
            dialog.results.setFocus()
            press(dialog.results, Qt.Key.Key_Down)
            seen["first"] = dialog.results.currentRow()
            press(dialog.results, Qt.Key.Key_Down)
            seen["second"] = dialog.results.currentRow()
            seen["go"] = dialog.go_button.isEnabled()
            seen["moved"] = _extent_state(world.main) != home

        canyon.open(script)
        assert seen == {"first": 0, "second": 1, "go": True, "moved": False}


def _named_layer(canyon, names):
    """A point layer in the main frame holding one point per name."""
    from go_to_location_fixtures import _add, _memory_layer, _pt

    layer = _memory_layer(canyon.project, "Point", "Named", "field=name:string")
    _add(
        layer,
        [([name], _pt(-112.0 + i * 0.0001, 36.1)) for i, name in enumerate(names)],
    )
    world = canyon.world
    world.main.setLayers([world.viewpoints, world.trails, world.areas, layer])
    return layer


class TestTheEnterKey:
    """Enter belongs to Search in the query box and to Go on the results."""

    def test_enter_in_the_query_searches_and_never_goes(self, canyon) -> None:
        world = canyon.world
        home = _extent_state(world.main)
        index = canyon.stack.index()
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.viewpoints)
            choose_source(dialog, "QGIS feature ID")
            type_text(dialog.query_edit, "4")  # exactly one result: Go would be ready
            press(dialog.query_edit, Qt.Key.Key_Return)
            wait_for(lambda: dialog.results.count() == 1, what="the search by Enter")
            seen["go_ready"] = dialog.go_button.isEnabled()
            seen["open_after_first_enter"] = dialog.isVisible()
            press(dialog.query_edit, Qt.Key.Key_Enter)  # the keypad one too
            QTest.qWait(150)
            seen["open_after_second_enter"] = dialog.isVisible()

        canyon.open(script)
        assert seen == {
            "go_ready": True,
            "open_after_first_enter": True,
            "open_after_second_enter": True,
        }
        assert _extent_state(world.main) == home
        assert canyon.stack.index() == index

    def test_no_button_is_a_default_button(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            from qgis.PyQt.QtWidgets import QPushButton

            seen["buttons"] = {
                b.text(): (b.isDefault(), b.autoDefault())
                for b in dialog.findChildren(QPushButton)
            }

        canyon.open(script)
        assert seen["buttons"]
        assert not any(default or auto for default, auto in seen["buttons"].values()), (
            seen
        )

    def test_enter_on_a_focused_button_presses_it(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.cancel_button.setFocus()
            press(dialog.cancel_button, Qt.Key.Key_Return)
            seen["closed"] = not dialog.isVisible()

        canyon.open(script)
        assert seen["closed"]

    def test_enter_in_the_longitude_field_goes_when_both_fields_are_valid(
        self, canyon
    ) -> None:
        world = canyon.world

        def script(dialog) -> None:
            type_text(dialog.first_edit, "36.10")
            type_text(dialog.second_edit, "-112.10")
            press(dialog.second_edit, Qt.Key.Key_Return)

        canyon.open(script)
        assert _centre_is(world.main, -112.10, 36.10)

    def test_enter_in_an_invalid_coordinate_field_explains_and_stays(
        self, canyon
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            type_text(dialog.first_edit, "95")
            type_text(dialog.second_edit, "-112.10")
            dialog.second_edit.setFocus()
            press(dialog.second_edit, Qt.Key.Key_Return)
            seen["open"] = dialog.isVisible()
            seen["message"] = dialog.coordinate_message.text()

        canyon.open(script)
        assert seen == {"open": True, "message": "Latitude must be between -90 and 90."}


class TestWhatLayerStylingHides:
    def test_a_feature_its_style_does_not_draw_is_explained_not_enabled(
        self, canyon
    ) -> None:
        world = canyon.world
        renderer_before = world.elsewhere.renderer().clone().dump()
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.elsewhere)
            choose_source(dialog, "kind")
            search(dialog, "hidden")
            seen["message"] = dialog.feature_message.text()
            seen["go"] = dialog.go_button.isEnabled()
            seen["shown_message"] = ""
            dialog.query_edit.clear()
            search(dialog, "shown")
            seen["shown_message"] = dialog.feature_message.text()

        canyon.open(script)
        assert "styling" in seen["message"], seen["message"]
        assert seen["go"] is True
        assert seen["shown_message"] == ""
        # The category stayed hidden: the layer's style was not touched.
        assert world.elsewhere.renderer().clone().dump() == renderer_before
        assert not world.elsewhere.renderer().categories()[1].renderState()

    def test_a_rule_based_style_that_skips_the_feature_is_explained_too(
        self, canyon
    ) -> None:
        from qgis.core import QgsMarkerSymbol, QgsRuleBasedRenderer

        world = canyon.world
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(
            QgsRuleBasedRenderer.Rule(
                QgsMarkerSymbol.createSimple({}), 0, 0, "\"kind\" = 'shown'"
            )
        )
        world.elsewhere.setRenderer(QgsRuleBasedRenderer(root))
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, world.elsewhere)
            choose_source(dialog, "kind")
            search(dialog, "hidden")
            seen["hidden"] = dialog.feature_message.text()
            search(dialog, "shown")
            seen["shown"] = dialog.feature_message.text()

        canyon.open(script)
        assert "styling" in seen["hidden"]
        assert seen["shown"] == ""


class TestWhenTheDocumentChangesUnderTheDialog:
    def _with_a_result(self, dialog, world):
        choose_layer(dialog, world.viewpoints)
        search(dialog, "mather")
        assert dialog.go_button.isEnabled()

    def test_the_frame_removed_disables_go_and_says_so(self, canyon) -> None:
        world = canyon.world
        seen = {}

        def script(dialog) -> None:
            self._with_a_result(dialog, world)
            fill_coordinates(dialog, "36.10", "-112.10")
            world.layout.removeLayoutItem(world.main)
            wait_for(
                lambda: dialog.footer_status.text() != "",
                what="the frame's removal to show",
            )
            seen["footer"] = dialog.footer_status.text()
            seen["feature_go"] = dialog.go_button.isEnabled()
            dialog.tabs.setCurrentIndex(0)
            seen["coordinates_go"] = dialog.go_button.isEnabled()
            seen["still_open"] = dialog.isVisible()

        canyon.open(script)
        assert seen == {
            "footer": "This map frame is no longer available.",
            "feature_go": False,
            "coordinates_go": False,
            "still_open": True,
        }

    def test_the_source_layer_removed_clears_the_result(self, canyon) -> None:
        world = canyon.world
        index = canyon.stack.index()
        seen = {}

        def script(dialog) -> None:
            self._with_a_result(dialog, world)
            canyon.project.removeMapLayer(world.viewpoints.id())
            wait_for(
                lambda: dialog.results.count() == 0, what="the result to be dropped"
            )
            seen["go"] = dialog.go_button.isEnabled()
            seen["message"] = dialog.feature_message.text()
            seen["layers"] = [
                dialog.layer_combo.itemText(i)
                for i in range(dialog.layer_combo.count())
            ]

        canyon.open(script)
        assert seen["go"] is False
        assert seen["message"] == "This layer is no longer available."
        assert "Viewpoints" not in seen["layers"]
        assert canyon.stack.index() == index

    def test_a_feature_deleted_after_the_search_is_never_gone_to_from_cache(
        self, canyon
    ) -> None:
        world = canyon.world
        home = _extent_state(world.main)
        seen = {}

        def script(dialog) -> None:
            self._with_a_result(dialog, world)
            fid = fid_of(world.viewpoints, name="Mather Point")
            world.viewpoints.dataProvider().deleteFeatures([fid])
            dialog.go_button.click()
            seen["open"] = dialog.isVisible()
            seen["message"] = dialog.feature_message.text()
            seen["go"] = dialog.go_button.isEnabled()
            seen["rows"] = dialog.results.isEnabled()

        canyon.open(script)
        assert seen["open"] is True
        assert seen["message"] == "This feature is no longer available."
        assert seen["go"] is False
        assert _extent_state(world.main) == home

    def test_a_feature_that_lost_its_geometry_is_not_gone_to(self, canyon) -> None:
        from qgis.core import QgsGeometry

        world = canyon.world
        home = _extent_state(world.main)
        seen = {}

        def script(dialog) -> None:
            self._with_a_result(dialog, world)
            fid = fid_of(world.viewpoints, name="Mather Point")
            world.viewpoints.dataProvider().changeGeometryValues({fid: QgsGeometry()})
            dialog.go_button.click()
            seen["message"] = dialog.feature_message.text()

        canyon.open(script)
        assert seen["message"] == "This feature has no location to display."
        assert _extent_state(world.main) == home

    def test_the_layer_removed_just_before_go_does_not_move_the_map(
        self, canyon
    ) -> None:
        world = canyon.world
        home = _extent_state(world.main)
        seen = {}

        def script(dialog) -> None:
            self._with_a_result(dialog, world)
            dialog._watch_timer.stop()  # nothing noticed the removal yet
            canyon.project.removeMapLayer(world.viewpoints.id())
            dialog.go_button.click()
            seen["open"] = dialog.isVisible()
            seen["message"] = dialog.feature_message.text()

        canyon.open(script)
        assert seen == {"open": True, "message": "This layer is no longer available."}
        assert _extent_state(world.main) == home


class TestAtlasControlledFrames:
    def _atlas(self, world, enabled=True, driven=True) -> None:
        atlas = world.layout.atlas()
        atlas.setCoverageLayer(world.areas)
        atlas.setEnabled(enabled)
        world.main.setAtlasDriven(driven)

    def test_an_atlas_driven_frame_is_disabled_with_the_exact_reason(
        self, canyon
    ) -> None:
        world = canyon.world
        self._atlas(world)
        home = _extent_state(world.main)
        seen = {}

        def script(dialog) -> None:
            seen["footer"] = dialog.footer_status.text()
            fill_coordinates(dialog, "36.10", "-112.10")
            seen["coordinates_go"] = dialog.go_button.isEnabled()
            choose_layer(dialog, world.viewpoints)
            search(dialog, "mather")
            seen["feature_go"] = dialog.go_button.isEnabled()

        canyon.open(script)
        assert seen == {
            "footer": (
                "This map follows the atlas. "
                "Turn off the map series to choose a fixed location."
            ),
            "coordinates_go": False,
            "feature_go": False,
        }
        assert _extent_state(world.main) == home
        assert world.layout.atlas().enabled(), "the atlas was not switched off"

    @pytest.mark.parametrize(("enabled", "driven"), [(False, True), (True, False)])
    def test_only_a_frame_an_enabled_atlas_drives_is_restricted(
        self, canyon, enabled, driven
    ) -> None:
        world = canyon.world
        self._atlas(world, enabled=enabled, driven=driven)

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            assert dialog.footer_status.text() == ""
            dialog.go_button.click()

        canyon.open(script)
        assert _centre_is(world.main, -112.10, 36.10)

    def test_the_atlas_turned_on_while_open_disables_go(self, canyon) -> None:
        world = canyon.world
        seen = {}

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.10", "-112.10")
            assert dialog.go_button.isEnabled()
            self._atlas(world)
            wait_for(
                lambda: dialog.footer_status.text() != "",
                what="the atlas to be noticed",
            )
            seen["go"] = dialog.go_button.isEnabled()

        canyon.open(script)
        assert seen["go"] is False


def _go_to_coordinates(canyon, lat: str, lon: str, item=None) -> None:
    def script(dialog) -> None:
        fill_coordinates(dialog, lat, lon)
        dialog.go_button.click()

    canyon.open(script, item)


def _go_to_feature(canyon, layer, name: str) -> None:
    def script(dialog) -> None:
        choose_layer(dialog, layer)
        search(dialog, name)
        dialog.go_button.click()

    canyon.open(script)


def _page_pixel_at_frame_centre(world):
    """The page as exported (QGIS's own layout renderer), at the main frame's middle."""
    from qgis.core import QgsLayoutExporter
    from qgis.PyQt.QtCore import QSize

    width_px = 1200
    page = world.layout.pageCollection().page(0).sizeWithUnits()
    height_px = round(width_px * page.height() / page.width())
    image = QgsLayoutExporter(world.layout).renderPageToImage(
        0, QSize(width_px, height_px)
    )
    scale = width_px / page.width()
    centre = world.main.sceneBoundingRect().center()
    return image.pixelColor(round(centre.x() * scale), round(centre.y() * scale))


class TestWhatFollowsTheNewView:
    def test_the_exported_page_shows_the_new_area(self, canyon) -> None:
        world = canyon.world
        # Somewhere with nothing drawn: the middle of the page is blank.
        _go_to_coordinates(canyon, "36.50", "-112.50")
        blank = _page_pixel_at_frame_centre(world)
        assert (blank.red(), blank.green(), blank.blue()) == (255, 255, 255)
        # A viewpoint at the middle: the page now draws its marker there.
        _go_to_feature(canyon, world.viewpoints, "Mather Point")
        marker = _page_pixel_at_frame_centre(world)
        assert (marker.red(), marker.green(), marker.blue()) != (255, 255, 255)

    def test_the_saved_cartograph_carries_the_new_centre(
        self,
        canyon,
        fake_runtime,  # noqa: F811
    ) -> None:
        import re

        from nika_onlymap_exporter.core.cartograph_export import (
            export_layout_to_cartograph,
        )
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder

        world = canyon.world

        def frame_view(item):
            html = export_layout_to_cartograph(
                world.layout, canyon.project, fake_runtime, FidelityReportBuilder()
            )
            frame = re.search(
                rf'<om-frame id="frame_{item.id().lower().replace(" ", "_")}"[^>]*>',
                html,
            )
            assert frame, html[:400]
            centre = re.search(r'center="\[([-\d.]+), ([-\d.]+)\]"', frame.group(0))
            zoom = re.search(r'zoom="([\d.]+)"', frame.group(0))
            return float(centre.group(1)), float(centre.group(2)), float(zoom.group(1))

        before = frame_view(world.main)
        _go_to_coordinates(canyon, "36.20", "-112.20")
        lon, lat, zoom = frame_view(world.main)
        assert math.isclose(lon, -112.20, abs_tol=2e-3)
        assert math.isclose(lat, 36.20, abs_tol=2e-3)
        # The web zoom of a fixed ground scale drifts a little with latitude.
        assert math.isclose(zoom, before[2], abs_tol=0.01), "the scale is kept"
        assert (lon, lat) != before[:2]

    def test_a_live_count_follows_the_frame(self, canyon) -> None:
        from qgis.core import QgsLayoutItemLabel
        from qgis.PyQt.QtCore import QRectF

        from nika_onlymap_exporter.core import live_count as lc

        world = canyon.world
        number = QgsLayoutItemLabel(world.layout)
        number.setId("Live count")
        world.layout.addLayoutItem(number)
        number.attemptSetSceneRect(QRectF(220, 10, 40, 14))
        lc.write_spec(
            number,
            lc.CountSpec(
                map_uuid=world.main.uuid(),
                layer_id=world.viewpoints.id(),
                layer_name=world.viewpoints.name(),
                mode=lc.MODE_ALL,
            ),
        )

        def inside() -> int:
            box = world.main.extent()
            total = 0
            for feature in world.viewpoints.getFeatures():
                if feature.hasGeometry():
                    point = feature.geometry().asPoint()
                    if box.contains(wgs_to_frame(point.x(), point.y())):
                        total += 1
            return total

        wait_for(lambda: number.text() == str(inside()), what="the first count")
        first = inside()
        _go_to_feature(canyon, world.viewpoints, "Far Away Point")
        assert inside() == 1 != first
        wait_for(
            lambda: number.text() == "1",
            what=f"the count to follow the frame (reads {number.text()!r})",
        )
        canyon.stack.undo()
        wait_for(lambda: number.text() == str(first), what="the count to follow Undo")

    def test_the_cartograph_is_saved_again(self, canyon) -> None:
        """The existing save/export mechanism is asked to run (an advisory check
        of the mechanism; the exported centre above is the result)."""
        canyon.cartograph._autosave_timer.stop()
        _go_to_coordinates(canyon, "36.20", "-112.20")
        assert canyon.cartograph._autosave_timer.isActive()

    def test_a_pending_edit_view_gesture_cannot_overwrite_the_destination(
        self, canyon
    ) -> None:
        from qgis.PyQt.QtCore import QPoint

        world = canyon.world
        preview = canyon.cartograph.page_preview
        view = preview.view
        viewport = view.viewport()
        seen = {}

        def script(dialog) -> None:
            # The frame is selected, Edit view is on, and a drag has begun and
            # has not been released yet.
            world.layout.setSelectedItem(world.main)
            preview.edit_content(world.main)
            QApplication.processEvents()
            centre = view.mapFromScene(world.main.sceneBoundingRect().center())
            QTest.mouseMove(viewport, centre)
            QTest.mousePress(
                viewport,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                centre,
            )
            for step in range(1, 9):
                QTest.mouseMove(
                    viewport, QPoint(centre.x() - 6 * step, centre.y() + 4 * step)
                )
                QApplication.processEvents()
            seen["dragging"] = preview.content_editor._dragging
            fill_coordinates(dialog, "36.20", "-112.20")
            dialog.go_button.click()
            QTest.mouseRelease(
                viewport,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                QPoint(centre.x() - 48, centre.y() + 32),
            )
            QApplication.processEvents()

        canyon.open(script)
        assert seen["dragging"], "the drag was under way"
        assert _centre_is(world.main, -112.20, 36.20), "the old drag moved the view"


class TestLargeLayers:
    def _watch_the_loop(self):
        """A 10 ms ticker on the GUI thread; `longest()` is the biggest gap
        between two ticks, i.e. how long the window was unresponsive."""
        import time

        from qgis.PyQt.QtCore import QTimer

        state = {"last": time.monotonic(), "worst": 0.0}

        def tick() -> None:
            now = time.monotonic()
            state["worst"] = max(state["worst"], now - state["last"])
            state["last"] = now

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(tick)
        timer.start()
        return timer, state

    def test_a_search_of_200000_features_keeps_the_window_responsive(
        self, canyon, monkeypatch
    ) -> None:
        import time

        from go_to_location_fixtures import many_points

        from nika_onlymap_exporter.core import go_to_location as core

        world = canyon.world
        big = many_points(canyon.project, 200_000)
        world.main.setLayers([world.viewpoints, big])
        canyon.quiet()
        jobs = []
        real_start = core.start_search

        def recording(layer, plan, generation):
            job = real_start(layer, plan, generation)
            jobs.append(job)
            return job

        monkeypatch.setattr(core, "start_search", recording)
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, big)
            timer, state = self._watch_the_loop()
            try:
                # Rare text: the provider has to look at every row to say "none".
                started = time.monotonic()
                search(dialog, "no such site")
                seen["rare_seconds"] = time.monotonic() - started
                seen["rare_rows"] = dialog.results.count()
                # Common text: thousands of matches, only the first page read.
                search(dialog, "site 0012")
                seen["common_rows"] = dialog.results.count()
                seen["common_status"] = dialog.status_label.text()
                # An exact name among 200 000.
                search(dialog, "Site 123456")
                seen["exact_rows"] = rows(dialog)
            finally:
                timer.stop()
            seen["worst_gap"] = state["worst"]

        canyon.open(script)
        assert seen["rare_rows"] == 0
        assert seen["common_rows"] == 50
        assert (
            seen["common_status"] == "Showing the first 50 matches. Refine your search."
        )
        assert seen["exact_rows"][0][0] == "Site 123456"
        # The window kept answering while the provider scanned: no stall near
        # a tenth of a second, however long the scan took.
        assert seen["worst_gap"] < 0.25, seen
        # And only a page of features was ever read: the rare query read none,
        # the common one at most a limit's worth twice over.
        rare, common, exact = jobs
        assert rare.fetched == 0
        assert common.fetched <= 2 * (core.MAX_SHOWN + 1)
        assert exact.fetched <= 2 * (core.MAX_SHOWN + 1)
        print(
            "worst gap ms:",
            round(seen["worst_gap"] * 1000),
            "rare scan s:",
            round(seen["rare_seconds"], 1),
        )

    def test_a_geopackage_layer_with_a_subset_filter(self, canyon, tmp_path) -> None:
        from live_count_fixtures import on_disk

        world = canyon.world
        on_gpkg = on_disk(world.viewpoints, tmp_path)
        canyon.project.addMapLayer(on_gpkg)
        on_gpkg.setSubsetString("\"name\" != 'Mather Point'")
        subset = on_gpkg.subsetString()
        world.main.setLayers([on_gpkg])
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, on_gpkg)
            search(dialog, "point")
            seen["names"] = [r[0] for r in rows(dialog)]
            search(dialog, "50%_off")
            seen["literal"] = [r[0] for r in rows(dialog)]
            choose_source(dialog, "QGIS feature ID")
            search(dialog, "4")  # Mather Point is feature 4, but not in the subset
            seen["hidden"] = (dialog.results.count(), dialog.status_label.text())

        canyon.open(script)
        assert "Mather Point" not in seen["names"] and "Lipan Point" in seen["names"]
        assert seen["literal"] == ["Lookout 50%_off"]
        assert seen["hidden"] == (0, "No matching features.")
        assert on_gpkg.subsetString() == subset, "the layer's filter was not touched"


class TestTheDialogsLook:
    def test_accessible_names(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            from qgis.PyQt.QtWidgets import QToolButton

            seen["names"] = {
                "change": dialog.crs_button.accessibleName(),
                "latitude": dialog.first_edit.accessibleName(),
                "longitude": dialog.second_edit.accessibleName(),
                "layer": dialog.layer_combo.accessibleName(),
                "search_in": dialog.source_combo.accessibleName(),
                "search": dialog.search_button.accessibleName(),
                "results": dialog.results.accessibleName(),
                "fit": dialog.fit_check.accessibleName(),
                "cancel": dialog.cancel_button.accessibleName(),
                "go": dialog.go_button.accessibleName(),
            }
            seen["clear_query"] = [
                b.accessibleName() for b in dialog.query_edit.findChildren(QToolButton)
            ]
            type_text(dialog.query_edit, "x")
            seen["clear_query_after_typing"] = [
                b.accessibleName() for b in dialog.query_edit.findChildren(QToolButton)
            ]

        canyon.open(script)
        assert seen["names"] == {
            "change": "Change coordinate system",
            "latitude": "Latitude",
            "longitude": "Longitude",
            "layer": "Layer",
            "search_in": "Search in",
            "search": "Search",
            "results": "Results",
            "fit": "Fit whole feature",
            "cancel": "Cancel",
            "go": "Go",
        }
        assert "Clear search" in seen["clear_query"] + seen["clear_query_after_typing"]

    def test_labels_sit_above_their_fields_in_the_specified_order(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            def y(widget):
                return widget.mapTo(dialog, widget.rect().topLeft()).y()

            seen["coordinates"] = [
                y(w)
                for w in (
                    dialog.crs_title,
                    dialog.crs_value,
                    dialog.first_label,
                    dialog.first_edit,
                    dialog.second_label,
                    dialog.second_edit,
                    dialog.coordinate_hint,
                )
            ]
            dialog.tabs.setCurrentIndex(1)
            QApplication.processEvents()
            seen["feature"] = [
                y(w)
                for w in (
                    dialog.layer_combo,
                    dialog.source_combo,
                    dialog.query_edit,
                    dialog.status_label,
                    dialog.results,
                    dialog.fit_check,
                    dialog.scope_hint,
                )
            ]
            seen["footer"] = (
                dialog.cancel_button.geometry().left()
                < dialog.go_button.geometry().left(),
                dialog.cancel_button.geometry().top()
                == dialog.go_button.geometry().top(),
            )
            seen["margins"] = dialog.layout().contentsMargins().left()
            seen["no_default_extras"] = sorted(
                b.text() for b in dialog.findChildren(type(dialog.go_button))
            )

        canyon.open(script)
        assert seen["coordinates"] == sorted(seen["coordinates"])
        assert seen["feature"] == sorted(seen["feature"])
        assert seen["footer"] == (True, True)
        assert seen["margins"] == 16
        # No Apply, Preview, Save or Reset buttons: only these.
        assert seen["no_default_extras"] == ["Cancel", "Change…", "Go", "Search"]

    def test_the_wheel_never_changes_a_closed_dropdown(self, canyon) -> None:
        from qgis.PyQt.QtCore import QPoint, QPointF
        from qgis.PyQt.QtGui import QWheelEvent

        from nika_onlymap_exporter.ui.wheel_guard import (
            install_wheel_guard,
            remove_wheel_guard,
        )

        install_wheel_guard()
        seen = {}
        try:

            def script(dialog) -> None:
                dialog.tabs.setCurrentIndex(1)
                choose_layer(dialog, canyon.world.viewpoints)
                for name in ("layer_combo", "source_combo"):
                    combo = getattr(dialog, name)
                    combo.setCurrentIndex(1)
                    before = combo.currentIndex()
                    for delta in (-120, 120, -120):
                        event = QWheelEvent(
                            QPointF(8, 8),
                            QPointF(combo.mapToGlobal(QPoint(8, 8))),
                            QPoint(0, 0),
                            QPoint(0, delta),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase,
                            False,
                        )
                        QApplication.sendEvent(combo, event)
                    seen[name] = (before, combo.currentIndex())
                    # The keyboard still chooses.
                    combo.setFocus()
                    press(combo, Qt.Key.Key_Down)
                    seen[name + "_key"] = combo.currentIndex()

            canyon.open(script)
        finally:
            remove_wheel_guard()
        assert seen["layer_combo"] == (1, 1) and seen["source_combo"] == (1, 1)
        assert seen["layer_combo_key"] == 2 and seen["source_combo_key"] == 2

    @pytest.mark.parametrize("scale", [1.0, 1.25, 1.6])
    def test_text_is_never_clipped_at_larger_fonts(self, canyon, scale) -> None:
        from qgis.PyQt.QtWidgets import QComboBox, QLineEdit, QPushButton

        base = QApplication.font()
        bigger = QApplication.font()
        bigger.setPointSizeF(base.pointSizeF() * scale)
        QApplication.setFont(bigger)
        seen = {}
        try:

            def script(dialog) -> None:
                for index in (0, 1):
                    dialog.tabs.setCurrentIndex(index)
                    QApplication.processEvents()
                    squeezed = []
                    for widget in dialog.findChildren(
                        (QLineEdit, QComboBox, QPushButton)
                    ):
                        if (
                            widget.isVisibleTo(dialog)
                            and widget.parent() is not None
                            and widget.height() < widget.sizeHint().height() - 1
                        ):
                            squeezed.append(
                                (
                                    widget.accessibleName(),
                                    widget.height(),
                                    widget.sizeHint().height(),
                                )
                            )
                    seen[index] = squeezed
                    seen[f"scroll{index}"] = (
                        dialog.results.horizontalScrollBar().isVisible()
                    )
                    seen[f"fits{index}"] = [
                        w.accessibleName()
                        for w in dialog.findChildren(QPushButton)
                        if w.isVisibleTo(dialog)
                        and w.mapTo(dialog, w.rect().bottomRight()).x() > dialog.width()
                    ]
                seen["size"] = (dialog.width(), dialog.height())

            canyon.open(script)
        finally:
            QApplication.setFont(base)
        assert seen[0] == [] and seen[1] == [], seen
        assert not seen["scroll0"] and not seen["scroll1"]
        assert seen["fits0"] == [] and seen["fits1"] == []

    def test_the_size_follows_the_open_tab(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            seen["coordinates"] = (dialog.width(), dialog.height())
            dialog.tabs.setCurrentIndex(1)
            QApplication.processEvents()
            seen["feature"] = (dialog.width(), dialog.height())
            dialog.tabs.setCurrentIndex(0)
            QApplication.processEvents()
            seen["back"] = (dialog.width(), dialog.height())

        canyon.open(script)
        assert seen["coordinates"][0] == 620 == seen["feature"][0]
        # About 430 and 570 px with the default font: not the taller tab's height twice.
        assert 380 <= seen["coordinates"][1] <= 470, seen
        assert 520 <= seen["feature"][1] <= 620, seen
        # Back on the short tab it is short again (give or take a wrapped line).
        assert seen["back"][0] == 620
        assert abs(seen["back"][1] - seen["coordinates"][1]) <= 30, seen

    def test_a_narrow_screen_and_long_names_never_widen_or_scroll_the_dialog(
        self, canyon, monkeypatch
    ) -> None:
        from qgis.PyQt.QtCore import QSize

        from nika_onlymap_exporter.ui.cartograph import go_to_location_dialog as module

        monkeypatch.setattr(
            module.GoToLocationDialog, "_screen_size", lambda self: QSize(400, 700)
        )
        long_name = "A very long map frame name " * 6
        canyon.world.main.setId(long_name)
        layer = _named_layer(canyon, ["An extraordinarily long feature name " * 6])
        layer.setName("A layer with an extraordinarily long name " * 4)
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            choose_layer(dialog, layer)
            search(dialog, "extraordinarily")
            QApplication.processEvents()
            seen["size"] = (dialog.width(), dialog.height())
            seen["scroll"] = dialog.results.horizontalScrollBar().isVisible()
            seen["tooltip"] = dialog.results.item(0).toolTip()
            seen["context_tooltip"] = dialog.context_label.toolTip()
            seen["inside"] = all(
                w.mapTo(dialog, w.rect().bottomRight()).x() <= dialog.width()
                for w in dialog.findChildren(QPushButton)
                if w.isVisibleTo(dialog)
            )

        from qgis.PyQt.QtWidgets import QPushButton

        canyon.open(script)
        assert seen["size"][0] <= 360 and seen["size"][1] <= 660
        assert seen["scroll"] is False and seen["inside"] is True
        assert "extraordinarily long feature name" in seen["tooltip"]
        assert seen["context_tooltip"] == long_name


class TestRegressions:
    """The three things most likely to be quietly wrong, each with its own test."""

    def test_latitude_first_is_longitude_second_in_the_transform(
        self, qgis_app
    ) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsProject

        from nika_onlymap_exporter.core import go_to_location as core

        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        utm = QgsCoordinateReferenceSystem("EPSG:32612")
        point = core.fields_to_point(36.10, -112.10, wgs)
        assert (point.x(), point.y()) == (-112.10, 36.10)
        moved = core.transform_point(point, wgs, utm, QgsProject.instance())
        expected = wgs_to_frame(-112.10, 36.10)
        assert math.hypot(moved.x() - expected.x(), moved.y() - expected.y()) < 0.01
        assert 395_000 < moved.x() < 410_000 and 3_990_000 < moved.y() < 4_005_000
        # The swapped order is a different place altogether (or none at all).
        swapped = core.fields_to_point(-112.10, 36.10, wgs)
        assert (swapped.x(), swapped.y()) == (36.10, -112.10)
        # A projected pair keeps its order: easting is X, northing is Y.
        projected = core.fields_to_point(401234.5, 3996543.25, utm)
        assert (projected.x(), projected.y()) == (401234.5, 3996543.25)
        # And the typed values come back out in the same order.
        assert core.point_to_fields(point, wgs) == (36.10, -112.10)
        assert core.point_to_fields(projected, utm) == (401234.5, 3996543.25)

    def test_a_result_is_its_layer_and_feature_id_not_its_name(self, canyon) -> None:
        world = canyon.world

        def script(dialog) -> None:
            choose_layer(dialog, world.viewpoints)
            search(dialog, "desert view")
            click_row(dialog, 1)  # QGIS ID 3, at -112.05 / 36.15
            # The two features swap names behind the open dialog: a look-up by
            # name would now find the other one.
            first = fid_of(world.viewpoints, station_id="STA-013")
            second = fid_of(world.viewpoints, station_id="STA-014")
            index = world.viewpoints.fields().indexOf("name")
            world.viewpoints.dataProvider().changeAttributeValues(
                {first: {index: "Renamed"}, second: {index: "Desert View"}}
            )
            dialog.go_button.click()

        canyon.open(script)
        assert _centre_is(world.main, -112.05, 36.15)

    def test_two_layers_with_the_same_name_are_told_apart(self, canyon) -> None:
        from go_to_location_fixtures import _add, _memory_layer, _pt

        world = canyon.world
        twin = _memory_layer(
            canyon.project,
            "Point",
            "Viewpoints",
            "field=name:string&field=station_id:string&field=sid:integer",
        )
        _add(twin, [(["Twin Only", "STA-900", 900], _pt(-112.30, 36.30))])
        world.main.setLayers([world.viewpoints, twin])
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            seen["offered"] = [
                dialog.layer_combo.itemText(i)
                for i in range(dialog.layer_combo.count())
            ]
            choose_layer(dialog, twin)
            search(dialog, "twin only")
            seen["rows"] = rows(dialog)
            dialog.go_button.click()

        canyon.open(script)
        names = seen["offered"][1:]
        assert len(set(names)) == 2 and all(n.startswith("Viewpoints") for n in names)
        assert seen["rows"] == [("Twin Only", "QGIS ID: 1")]
        assert _centre_is(world.main, -112.30, 36.30)

    def test_each_go_is_a_single_undo_step_even_when_repeated(self, canyon) -> None:
        index = canyon.stack.index()
        _go_to_coordinates(canyon, "36.20", "-112.20")
        _go_to_coordinates(canyon, "36.30", "-112.30")
        assert canyon.stack.index() == index + 2
        canyon.stack.undo()
        assert _centre_is(canyon.world.main, -112.20, 36.20)
        canyon.stack.undo()
        assert _centre_is(canyon.world.main, *HOME)
