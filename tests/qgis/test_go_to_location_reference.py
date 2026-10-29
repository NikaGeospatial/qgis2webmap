# ruff: noqa: F811
"""Go to location: a map frame's reference scale (what 1x means) is stored on the
frame, so it stays the same however often the dialog is reopened.

The tests drive the real dialog from the Cartograph panel's ⋯ menu and read the
frame's actual QgsLayoutItemMap.scale(), the layout's undo stack and the project
saved to disk.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("qgis.core")

from go_to_location_fixtures import (
    choose_layer,
    fill_coordinates,
    search,
)
from qgis.core import QgsLayoutItemMap, QgsPrintLayout, QgsProject, QgsReadWriteContext
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtXml import QDomDocument
from test_cartograph_dialog import picked_template  # noqa: F401 - autouse fixture
from test_cartograph_export import fake_runtime  # noqa: F401 - fixture
from test_go_to_location import (  # noqa: F401 - fixtures the dialog tests share
    Setup,
    canyon,
    fresh_session,
)

PROPERTY = "nika/reference-scale"
START = 100_000  # the Canyon frame is 200 mm wide and shows 20 km


def _click(widget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def _is(item, scale: float) -> bool:
    return math.isclose(item.scale(), scale, rel_tol=1e-6)


def _stored(item) -> float | None:
    value = item.customProperty(PROPERTY, "")
    return float(value) if value != "" else None


def _go_coordinates(canyon, preset=None, typed=None, lat="36.20", lon="-112.20"):
    """Open the dialog, pick a preset or type a scale, Go at a coordinate pair.
    Returns what the dialog showed when it opened: (field text, chosen preset)."""
    seen = {}

    def script(dialog) -> None:
        dialog.tabs.setCurrentIndex(0)  # the dialog reopens on the last tab used
        seen["open"] = (dialog.scale_edit.text(), dialog.zoom_control.selected())
        if preset is not None:
            _click(dialog.zoom_control.buttons[preset])
        if typed is not None:
            dialog.scale_edit.setFocus()
            dialog.scale_edit.selectAll()
            QTest.keyClicks(dialog.scale_edit, typed)
            dialog.cancel_button.setFocus()
            QApplication.processEvents()
        fill_coordinates(dialog, lat, lon)
        _click(dialog.go_button)

    canyon.open(script)
    return seen["open"]


def _go_feature(canyon, text, preset=None, typed=None):
    seen = {}

    def script(dialog) -> None:
        seen["open"] = (dialog.scale_edit.text(), dialog.zoom_control.selected())
        if preset is not None:
            _click(dialog.zoom_control.buttons[preset])
        if typed is not None:
            dialog.scale_edit.setFocus()
            dialog.scale_edit.selectAll()
            QTest.keyClicks(dialog.scale_edit, typed)
            dialog.cancel_button.setFocus()
            QApplication.processEvents()
        choose_layer(dialog, canyon.world.viewpoints)
        search(dialog, text)
        _click(dialog.go_button)

    canyon.open(script)
    return seen["open"]


def _cancel_dialog(canyon, preset=None):
    seen = {}

    def script(dialog) -> None:
        seen["open"] = (dialog.scale_edit.text(), dialog.zoom_control.selected())
        if preset is not None:
            _click(dialog.zoom_control.buttons[preset])
        _click(dialog.cancel_button)

    canyon.open(script)
    return seen["open"]


class TestTheReferenceSurvivesReopening:
    def test_the_ten_step_sequence_from_a_known_scale(self, canyon) -> None:
        item = canyon.world.main
        assert _is(item, START)

        # 1. 2x -> 1:50,000
        assert _go_coordinates(canyon, preset=2) == ("100,000", 1)
        assert _is(item, 50_000)
        # 2. reopening: 2x selected, field shows the actual scale
        # 3. 1x -> back to the original
        assert _go_coordinates(canyon, preset=1) == ("50,000", 2)
        assert _is(item, START)
        # 4. reopen and 2x, repeatedly: always 1:50,000, never compounding
        assert _go_coordinates(canyon, preset=2) == ("100,000", 1)
        for _ in range(3):
            assert _is(item, 50_000)
            assert _go_coordinates(canyon, preset=2) == ("50,000", 2)
        assert _is(item, 50_000)
        # 5. a custom scale is applied exactly, shown on reopen, no preset
        _go_coordinates(canyon, typed="30000")
        assert _is(item, 30_000)
        assert _cancel_dialog(canyon) == ("30,000", None)
        assert _is(item, 30_000)
        # 6. 1x returns to the original
        _go_coordinates(canyon, preset=1)
        assert _is(item, START)
        # 7. a different feature, and coordinates
        assert _go_feature(canyon, "hance", preset=3) == ("100,000", 1)
        assert _is(item, 25_000)
        assert _go_feature(canyon, "mather", preset=1) == ("25,000", 3)
        assert _is(item, START)
        _go_coordinates(canyon, preset=4)
        assert _is(item, 12_500)
        _go_coordinates(canyon, preset=0)
        assert _is(item, 200_000)
        _go_coordinates(canyon, preset=1)
        assert _is(item, START)
        assert _stored(item) == START

    def test_presets_are_the_documented_scales_whatever_the_current_one_is(
        self, canyon
    ) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=3)  # now 25,000; the reference is 100,000
        expected = {0: 200_000, 1: 100_000, 2: 50_000, 3: 25_000, 4: 12_500}
        for index, scale in expected.items():
            _go_coordinates(canyon, preset=index)
            assert _is(item, scale)

    def test_a_different_start_scale_gives_different_presets(self, canyon) -> None:
        item = canyon.world.main
        item.setScale(40_000)
        _go_coordinates(canyon, preset=2)
        assert _is(item, 20_000)
        _go_coordinates(canyon, preset=1)
        assert _is(item, 40_000)
        assert _stored(item) == pytest.approx(40_000)

    def test_a_fractional_reference_selects_1x_when_unchanged_and_4x_after_4x(
        self, canyon
    ) -> None:
        item = canyon.world.main
        item.setScale(111_420.7)
        exact = item.scale()
        assert _cancel_dialog(canyon) == ("111,421", 1)
        assert _go_coordinates(canyon, preset=3) == ("111,421", 1)
        assert _is(item, 27_855)
        assert _cancel_dialog(canyon) == ("27,855", 3)
        _go_coordinates(canyon, preset=1)
        assert math.isclose(item.scale(), exact, rel_tol=1e-9)

    def test_fitting_a_feature_leaves_the_reference_alone(self, canyon) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=2)
        assert _stored(item) == START
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            choose_layer(dialog, canyon.world.areas)
            search(dialog, "u basin")
            seen["fit"] = dialog.fit_check.isChecked()
            _click(dialog.go_button)

        canyon.open(script)
        assert seen["fit"]
        assert not _is(item, 50_000)  # the fit chose its own scale
        assert _stored(item) == START
        _go_coordinates(canyon, preset=1)
        assert _is(item, START)


class TestCancelAndUndo:
    def test_opening_and_cancelling_writes_nothing(self, canyon) -> None:
        item = canyon.world.main
        canyon.project.setDirty(False)
        count = canyon.stack.count()
        index = canyon.stack.index()
        for preset in (None, 2, 4):
            _cancel_dialog(canyon, preset=preset)
        assert item.customProperty(PROPERTY, "") == ""
        assert canyon.stack.count() == count and canyon.stack.index() == index
        assert not canyon.project.isDirty()
        assert _is(item, START)

    def test_cancelling_later_keeps_the_stored_reference(self, canyon) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=2)
        _cancel_dialog(canyon, preset=4)
        assert _stored(item) == START and _is(item, 50_000)

    def test_undo_of_the_first_go_removes_the_reference_and_redo_restores_both(
        self, canyon
    ) -> None:
        item = canyon.world.main
        count = canyon.stack.count()
        _go_coordinates(canyon, preset=2)
        assert canyon.stack.count() == count + 1  # one command for both
        assert _stored(item) == START and _is(item, 50_000)
        canyon.stack.undo()
        assert item.customProperty(PROPERTY, "") == ""
        assert _is(item, START)
        canyon.stack.redo()
        assert _stored(item) == START and _is(item, 50_000)

    def test_a_later_go_never_replaces_the_reference_and_undo_keeps_it(
        self, canyon
    ) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=2)
        _go_coordinates(canyon, typed="30000")
        _go_feature(canyon, "hance", preset=3)
        assert _stored(item) == START
        canyon.stack.undo()
        assert _stored(item) == START and _is(item, 30_000)

    def test_a_pan_does_not_change_the_reference(self, canyon) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=2)
        extent = item.extent()
        extent.setXMinimum(extent.xMinimum() + 500)
        extent.setXMaximum(extent.xMaximum() + 500)
        item.setExtent(extent)
        assert _stored(item) == START
        assert _cancel_dialog(canyon) == ("50,000", 2)


class TestEachFrameKeepsItsOwn:
    def test_two_frames_have_separate_references(self, canyon) -> None:
        main, inset = canyon.world.main, canyon.world.inset
        inset_start = inset.scale()
        _go_coordinates(canyon, preset=2)
        seen = {}

        def script(dialog) -> None:
            seen["open"] = (dialog.scale_edit.text(), dialog.zoom_control.selected())
            _click(dialog.zoom_control.buttons[3])
            fill_coordinates(dialog, "36.10", "-112.10")
            _click(dialog.go_button)

        canyon.open(script, inset)
        assert seen["open"][1] == 1
        assert _is(inset, inset_start / 4)
        assert _stored(main) == START
        assert _stored(inset) == pytest.approx(inset_start)
        # main still returns to its own reference, inset to its own
        _go_coordinates(canyon, preset=1)
        assert _is(main, START)
        canyon.open(
            lambda dialog: (
                _click(dialog.zoom_control.buttons[1]),
                fill_coordinates(dialog, "36.10", "-112.10"),
                _click(dialog.go_button),
            ),
            inset,
        )
        assert _is(inset, inset_start)


class TestItTravelsWithTheLayout:
    def _round_trip(self, canyon, tmp_path):
        path = str(tmp_path / "reference.qgz")
        assert canyon.project.write(path)
        reopened = QgsProject()
        assert reopened.read(path)
        layout = reopened.layoutManager().layoutByName("Canyon")
        item = next(
            i
            for i in layout.items()
            if isinstance(i, QgsLayoutItemMap) and i.id() == "Main map"
        )
        return reopened, item

    def test_saving_and_reopening_the_project_keeps_the_reference(
        self, canyon, tmp_path
    ) -> None:
        _go_coordinates(canyon, preset=2)
        _go_coordinates(canyon, typed="30000")
        _project, reopened = self._round_trip(canyon, tmp_path)
        assert _stored(reopened) == START
        assert _is(reopened, 30_000)

    def test_a_frame_never_asked_has_no_reference_after_a_reopen(
        self, canyon, tmp_path
    ) -> None:
        _project, reopened = self._round_trip(canyon, tmp_path)
        assert reopened.customProperty(PROPERTY, "") == ""

    def test_a_copied_frame_and_a_duplicated_layout_carry_it(self, canyon) -> None:
        item = canyon.world.main
        _go_coordinates(canyon, preset=2)
        document = QDomDocument()
        element = document.createElement("copy")
        document.appendChild(element)
        item.writeXml(element, document, QgsReadWriteContext())
        pasted = QgsLayoutItemMap(canyon.world.layout)
        assert pasted.readXml(
            element.firstChildElement("LayoutItem"), document, QgsReadWriteContext()
        )
        assert _stored(pasted) == START
        manager = canyon.project.layoutManager()
        copy = manager.duplicateLayout(canyon.world.layout, "Canyon copy")
        assert isinstance(copy, QgsPrintLayout)
        twin = next(
            i
            for i in copy.items()
            if isinstance(i, QgsLayoutItemMap) and i.id() == "Main map"
        )
        assert _stored(twin) == START
        # and the two layouts do not share it afterwards
        twin.setCustomProperty(PROPERTY, "12345.0")
        assert _stored(item) == START


class TestOlderDocuments:
    def test_a_frame_without_a_reference_starts_from_its_current_scale(
        self, canyon
    ) -> None:
        item = canyon.world.main
        item.setScale(60_000)  # as if zoomed before references were stored
        assert _cancel_dialog(canyon) == ("60,000", 1)
        _go_coordinates(canyon, preset=2)
        assert _is(item, 30_000)
        assert _stored(item) == pytest.approx(60_000)

    @pytest.mark.parametrize("bad", ["abc", "0", "-5", "nan", "inf"])
    def test_an_unusable_stored_value_is_ignored(self, canyon, bad) -> None:
        item = canyon.world.main
        item.setCustomProperty(PROPERTY, bad)
        assert _cancel_dialog(canyon) == ("100,000", 1)
