# ruff: noqa: F811, RUF001, E501
"""Go to location: the shared Zoom row, the one pending operation Go applies,
and a window that keeps its size when the tab changes.

Like the dialog tests, these open the real dialog from the Cartograph panel's
⋯ menu and click, type and press keys in the real widgets. What is asserted is
what the user ends up with: the frame's actual centre and scale, the undo
stack, and where the controls are on screen.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from go_to_location_fixtures import (
    choose_crs,
    choose_layer,
    click_row,
    fill_coordinates,
    frame_centre,
    press,
    search,
    wgs_to_frame,
)
from qgis.PyQt.QtCore import QPoint, QPointF, QSize, Qt
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QApplication
from test_cartograph_dialog import picked_template  # noqa: F401 - autouse fixture
from test_cartograph_export import fake_runtime  # noqa: F401 - fixture
from test_go_to_location import (  # noqa: F401 - fixtures the dialog tests share
    Setup,
    _named_layer,
    canyon,
    fresh_session,
)

BASELINE = 100_000  # the Canyon frame is 200 mm wide and shows 20 km
PRESETS = [(0, 200_000, "200,000"), (1, 100_000, "100,000"), (2, 50_000, "50,000"),
           (3, 25_000, "25,000"), (4, 12_500, "12,500")]  # fmt: skip
SHOTS = os.environ.get("Q2W_ZOOM_SHOTS")


def _click(widget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def _toggle(check) -> None:
    """Click the box itself (the row is much wider than the box and its label)."""
    QTest.mouseClick(
        check, Qt.MouseButton.LeftButton, pos=QPoint(8, check.height() // 2)
    )


def _preset(dialog, index: int) -> None:
    _click(dialog.zoom_control.buttons[index])


def _scale_text(dialog) -> str:
    return dialog.scale_edit.text()


def _type_scale(dialog, text: str, leave: bool = True) -> None:
    edit = dialog.scale_edit
    edit.setFocus()
    edit.selectAll()
    if text:
        QTest.keyClicks(edit, text)
    else:
        press(edit, Qt.Key.Key_Delete)
    if leave:
        dialog.cancel_button.setFocus()
        QApplication.processEvents()


def _wheel(widget, delta: int) -> None:
    point = QPoint(widget.width() // 2, widget.height() // 2)
    QApplication.sendEvent(
        widget,
        QWheelEvent(
            QPointF(point),
            QPointF(widget.mapToGlobal(point)),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )


def _view(item) -> tuple[float, float, float]:
    centre = frame_centre(item)
    return item.scale(), centre.x(), centre.y()


def _same_view(a, b) -> bool:
    return (
        math.isclose(a[0], b[0], rel_tol=1e-6)
        and abs(a[1] - b[1]) < 1e-3
        and abs(a[2] - b[2]) < 1e-3
    )


def _centre_at(item, lon: float, lat: float) -> bool:
    wanted = wgs_to_frame(lon, lat)
    centre = frame_centre(item)
    return abs(centre.x() - wanted.x()) < 1e-3 and abs(centre.y() - wanted.y()) < 1e-3


class TestThePresets:
    def test_the_frames_scale_is_the_baseline_and_one_x_is_chosen(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            seen["text"] = _scale_text(dialog)
            seen["chosen"] = dialog.zoom_control.selected()
            seen["label"] = dialog.zoom_label.text()
            seen["names"] = [b.text() for b in dialog.zoom_control.buttons]
            seen["tips"] = [b.toolTip() for b in dialog.zoom_control.buttons]
            seen["accessible"] = dialog.scale_edit.accessibleName()
            seen["tooltip"] = dialog.scale_edit.toolTip()

        canyon.open(script)
        assert math.isclose(canyon.world.main.scale(), BASELINE, rel_tol=1e-9)
        assert seen["text"] == "100,000" and seen["chosen"] == 1
        assert seen["label"] == "Zoom"
        assert seen["names"] == ["½×", "1×", "2×", "4×", "8×"]
        assert seen["tips"] == [
            "Zoom out to show more surrounding area.",
            "Return to this map's reference scale.",
            "Zoom twice as close.",
            "Zoom four times as close.",
            "Zoom eight times as close.",
        ]
        assert seen["accessible"] == "Map scale"
        assert seen["tooltip"] == "Enter an exact map scale, for example 1:25,000."

    @pytest.mark.parametrize(("index", "scale", "text"), PRESETS)
    def test_each_preset_is_that_many_times_closer_than_the_baseline(
        self, canyon, index, scale, text
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            _preset(dialog, index)
            seen["text"] = _scale_text(dialog)
            seen["chosen"] = dialog.zoom_control.selected()
            fill_coordinates(dialog, "36.20", "-112.20")
            _click(dialog.go_button)

        canyon.open(script)
        assert seen == {"text": text, "chosen": index}
        assert math.isclose(canyon.world.main.scale(), scale, rel_tol=1e-6)
        assert _centre_at(canyon.world.main, -112.20, 36.20)

    def test_a_fractional_baseline_shows_whole_numbers_and_one_x_keeps_it_exactly(
        self, canyon
    ) -> None:
        item = canyon.world.main
        item.setScale(111_420.7)
        exact = item.scale()
        shown = {}

        def script(dialog) -> None:
            shown["open"] = _scale_text(dialog)
            _preset(dialog, 3)
            shown["four"] = _scale_text(dialog)
            _preset(dialog, 1)
            shown["one"] = _scale_text(dialog)
            fill_coordinates(dialog, "36.20", "-112.20")
            _click(dialog.go_button)

        canyon.open(script)
        assert shown == {"open": "111,421", "four": "27,855", "one": "111,421"}
        assert math.isclose(item.scale(), exact, rel_tol=1e-9)  # untouched
        assert _centre_at(item, -112.20, 36.20)

        item.setScale(exact)

        def four(dialog) -> None:
            _preset(dialog, 3)
            fill_coordinates(dialog, "36.20", "-112.20")
            _click(dialog.go_button)

        canyon.open(four)
        assert math.isclose(item.scale(), 27_855, rel_tol=1e-9)

    def test_a_typed_decimal_is_rounded_when_editing_finishes(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            _type_scale(dialog, "30000.5")
            seen["text"] = _scale_text(dialog)
            fill_coordinates(dialog, "36.20", "-112.20")
            _click(dialog.go_button)

        canyon.open(script)
        assert seen["text"] == "30,001"
        assert math.isclose(canyon.world.main.scale(), 30_001, rel_tol=1e-9)

    def test_repeated_and_switched_clicks_never_compound(self, canyon) -> None:
        seen = []

        def script(dialog) -> None:
            for index in (2, 2, 2, 3, 3, 1, 1, 4, 2):
                _preset(dialog, index)
                seen.append((index, _scale_text(dialog)))

        canyon.open(script)
        by_index = {i: t for i, _, t in PRESETS}
        assert seen == [(i, by_index[i]) for i, _ in seen]

    def test_a_typed_scale_that_is_a_preset_selects_it_otherwise_none(
        self, canyon
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            _type_scale(dialog, "50000")
            seen["fifty"] = (dialog.zoom_control.selected(), _scale_text(dialog))
            _type_scale(dialog, "30000")
            seen["thirty"] = (dialog.zoom_control.selected(), _scale_text(dialog))
            _type_scale(dialog, "1:25,000")
            seen["pasted"] = (dialog.zoom_control.selected(), _scale_text(dialog))
            _type_scale(dialog, "100001")
            seen["near"] = dialog.zoom_control.selected()

        canyon.open(script)
        assert seen["fifty"] == (2, "50,000")
        assert seen["thirty"] == (None, "30,000")
        assert seen["pasted"] == (3, "25,000")
        assert seen["near"] is None

    @pytest.mark.parametrize("text", ["", "abc", "0", "-5", "12k", "1e3"])
    def test_an_unusable_scale_blocks_go_and_says_so(self, canyon, text) -> None:
        seen = {}
        canyon.project.setDirty(False)
        before = _view(canyon.world.main)

        def script(dialog) -> None:
            fill_coordinates(dialog, "36.20", "-112.20")
            seen["ready"] = dialog.go_button.isEnabled()
            _type_scale(dialog, text)
            seen["blocked"] = not dialog.go_button.isEnabled()
            seen["note"] = dialog.zoom_note.isVisibleTo(dialog)
            seen["message"] = dialog.zoom_note.text()
            seen["text"] = _scale_text(dialog)
            seen["chosen"] = dialog.zoom_control.selected()
            dialog.first_edit.setFocus()
            press(dialog.first_edit, Qt.Key.Key_Return)  # Enter cannot go either
            seen["still_open"] = dialog.isVisible()
            _preset(dialog, 3)
            seen["recovered"] = dialog.go_button.isEnabled()
            seen["note_after"] = dialog.zoom_note.isVisibleTo(dialog)

        canyon.open(script)
        assert seen["ready"] and seen["blocked"] and seen["note"]
        assert "valid map scale" in seen["message"]
        assert seen["text"] == text  # never silently replaced
        assert seen["chosen"] is None and seen["still_open"]
        assert seen["recovered"] and not seen["note_after"]
        assert _same_view(_view(canyon.world.main), before)

    def test_the_scale_is_read_in_the_locale(self, qgis_app) -> None:
        from qgis.PyQt.QtCore import QLocale

        from nika_onlymap_exporter.core import go_to_location as core

        english, german, french = (
            QLocale("en_US"),
            QLocale("de_DE"),
            QLocale("fr_FR"),
        )
        assert core.parse_scale("25,000", english).value == 25000
        assert core.parse_scale("1 : 25,000", english).value == 25000
        assert core.parse_scale("25000", english).value == 25000
        assert core.parse_scale("25.000", german).value == 25000
        assert core.parse_scale("12.500,5", german).value == 12500.5
        assert core.parse_scale("25 000", french).value == 25000
        assert core.parse_scale("25 000", french).value == 25000
        for bad in ("2,5", "25,00", "1,0000", "0", "-3", "1e5", "x", "", "1:"):
            assert core.parse_scale(bad, english).value is None, bad
        assert core.parse_scale("0", english).value is None
        assert core.parse_scale("2e9", english).value is None
        assert core.format_scale(25000, english) == "25,000"
        assert core.format_scale(25000, german) == "25.000"
        assert core.format_scale(12500.5, english) == "12,501"
        assert core.format_scale(27855.125, english) == "27,855"
        assert core.format_scale(1000000, english) == "1,000,000"

    def test_the_wheel_changes_neither_the_scale_nor_a_preset_nor_a_dropdown(
        self, canyon
    ) -> None:
        from nika_onlymap_exporter.ui.wheel_guard import (
            install_wheel_guard,
            remove_wheel_guard,
        )

        install_wheel_guard()
        seen = {}
        try:

            def script(dialog) -> None:
                _preset(dialog, 3)
                before = (_scale_text(dialog), dialog.zoom_control.selected())
                for delta in (-120, 120, -240):
                    _wheel(dialog.scale_edit, delta)
                    _wheel(dialog.zoom_control.buttons[1], delta)
                seen["zoom"] = before == (
                    _scale_text(dialog),
                    dialog.zoom_control.selected(),
                )
                dialog.tabs.setCurrentIndex(1)
                for name in ("layer_combo", "source_combo"):
                    combo = getattr(dialog, name)
                    combo.setCurrentIndex(min(1, combo.count() - 1))
                    index = combo.currentIndex()
                    for delta in (-120, 120):
                        _wheel(combo, delta)
                    seen[name] = index == combo.currentIndex()

            canyon.open(script)
        finally:
            remove_wheel_guard()
        assert seen == {"zoom": True, "layer_combo": True, "source_combo": True}

    def test_there_are_no_spinner_arrows(self, canyon) -> None:
        from qgis.PyQt.QtWidgets import QAbstractSpinBox, QLineEdit

        seen = {}

        def script(dialog) -> None:
            seen["spin"] = dialog.scale_field.findChildren(QAbstractSpinBox)
            seen["edit"] = isinstance(dialog.scale_edit, QLineEdit)

        canyon.open(script)
        assert seen == {"spin": [], "edit": True}


class TestOneSharedScale:
    def test_the_chosen_scale_follows_the_user_across_tabs_and_results(
        self, canyon
    ) -> None:
        world = canyon.world
        seen = {}

        def script(dialog) -> None:
            _preset(dialog, 3)
            dialog.tabs.setCurrentIndex(1)
            seen["feature_tab"] = (_scale_text(dialog), dialog.zoom_control.selected())
            choose_layer(dialog, world.viewpoints)
            search(dialog, "desert")
            click_row(dialog, 0)
            click_row(dialog, 1)
            seen["after_results"] = (
                _scale_text(dialog),
                dialog.zoom_control.selected(),
            )
            _type_scale(dialog, "30000")
            dialog.tabs.setCurrentIndex(0)
            QApplication.processEvents()
            seen["coordinates_tab"] = (
                _scale_text(dialog),
                dialog.zoom_control.selected(),
            )

        canyon.open(script)
        assert seen["feature_tab"] == ("25,000", 3)
        assert seen["after_results"] == ("25,000", 3)
        assert seen["coordinates_tab"] == ("30,000", None)

    def test_the_baseline_is_captured_once_when_the_dialog_opens(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            _preset(dialog, 2)
            dialog.tabs.setCurrentIndex(1)
            choose_layer(dialog, canyon.world.viewpoints)
            search(dialog, "mather")
            dialog.tabs.setCurrentIndex(0)
            _preset(dialog, 2)
            _preset(dialog, 1)
            seen["text"] = _scale_text(dialog)

        canyon.open(script)
        assert seen["text"] == "100,000"


class TestGoAppliesThePlaceAndTheScaleTogether:
    def test_coordinates_apply_the_centre_and_the_scale_as_one_undo_step(
        self, canyon
    ) -> None:
        item = canyon.world.main
        start = _view(item)
        count = canyon.stack.count()

        def script(dialog) -> None:
            _preset(dialog, 3)
            fill_coordinates(dialog, "36.20", "-112.20")
            _click(dialog.go_button)

        canyon.open(script)
        after = _view(item)
        assert math.isclose(after[0], 25_000, rel_tol=1e-6)
        assert _centre_at(item, -112.20, 36.20)
        assert canyon.stack.count() == count + 1
        canyon.stack.undo()
        assert _same_view(_view(item), start)
        canyon.stack.redo()
        assert _same_view(_view(item), after)

    def test_a_point_feature_applies_its_exact_centre_and_the_scale(
        self, canyon
    ) -> None:
        item = canyon.world.main
        start = _view(item)
        count = canyon.stack.count()

        def script(dialog) -> None:
            _preset(dialog, 4)
            choose_layer(dialog, canyon.world.viewpoints)
            search(dialog, "hance")
            _click(dialog.go_button)

        canyon.open(script)
        after = _view(item)
        assert math.isclose(after[0], 12_500, rel_tol=1e-6)
        assert _centre_at(item, -112.00, 36.05)
        assert canyon.stack.count() == count + 1
        canyon.stack.undo()
        assert _same_view(_view(item), start)
        canyon.stack.redo()
        assert _same_view(_view(item), after)

    def test_the_frames_rotation_and_place_on_the_page_are_kept(
        self, project, qgis_app
    ) -> None:
        made = Setup(project, rotation=30.0)
        try:
            item = made.world.main
            page = (
                item.pos().x(),
                item.pos().y(),
                item.rect().width(),
                item.rect().height(),
            )

            def script(dialog) -> None:
                _preset(dialog, 2)
                fill_coordinates(dialog, "36.20", "-112.20")
                _click(dialog.go_button)

            made.open(script)
            assert math.isclose(item.mapRotation(), 30.0)
            assert math.isclose(item.scale(), 50_000, rel_tol=1e-6)
            assert _centre_at(item, -112.20, 36.20)
            assert page == (
                item.pos().x(),
                item.pos().y(),
                item.rect().width(),
                item.rect().height(),
            )
        finally:
            made.close()

    @pytest.mark.parametrize("tab", [0, 1])
    def test_cancel_after_choosing_a_scale_changes_nothing(self, canyon, tab) -> None:
        item = canyon.world.main
        canyon.project.setDirty(False)
        before = (_view(item), canyon.stack.index(), canyon.project.isDirty())

        def script(dialog) -> None:
            _preset(dialog, 4)
            fill_coordinates(dialog, "36.20", "-112.20")
            choose_layer(dialog, canyon.world.viewpoints)
            search(dialog, "hance")
            dialog.tabs.setCurrentIndex(tab)
            dialog.cancel_button.click()

        canyon.open(script)
        assert (_view(item), canyon.stack.index(), canyon.project.isDirty()) == before


class TestFitWholeFeature:
    def _open_polygon(self, dialog, canyon) -> None:
        choose_layer(dialog, canyon.world.areas)
        search(dialog, "basin")

    def test_a_single_point_has_no_fit_box_and_keeps_the_row(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            choose_layer(dialog, canyon.world.viewpoints)
            search(dialog, "hance")
            seen["fit_shown"] = dialog.fit_check.isVisibleTo(dialog)
            seen["row"] = all(w.isEnabled() for w in dialog._zoom_widgets)
            seen["note"] = dialog.zoom_note.isVisibleTo(dialog)

        canyon.open(script)
        assert seen == {"fit_shown": False, "row": True, "note": False}

    def test_fitting_turns_the_row_off_and_unchecking_gives_the_manual_scale_back(
        self, canyon
    ) -> None:
        seen = {}

        def script(dialog) -> None:
            _preset(dialog, 3)
            dialog.tabs.setCurrentIndex(1)
            self._open_polygon(dialog, canyon)
            seen["fit_shown"] = dialog.fit_check.isVisibleTo(dialog)
            seen["fit_on"] = dialog.fit_check.isChecked()
            seen["row_off"] = not any(w.isEnabled() for w in dialog._zoom_widgets)
            seen["note"] = dialog.zoom_note.text()
            seen["note_shown"] = dialog.zoom_note.isVisibleTo(dialog)
            _toggle(dialog.fit_check)
            seen["row_back"] = all(w.isEnabled() for w in dialog._zoom_widgets)
            seen["restored"] = (_scale_text(dialog), dialog.zoom_control.selected())
            seen["note_gone"] = not dialog.zoom_note.isVisibleTo(dialog)
            _toggle(dialog.fit_check)
            seen["off_again"] = not any(w.isEnabled() for w in dialog._zoom_widgets)
            dialog.tabs.setCurrentIndex(0)
            QApplication.processEvents()
            seen["coordinates_enabled"] = all(
                w.isEnabled() for w in dialog._zoom_widgets
            )
            dialog.tabs.setCurrentIndex(1)
            QApplication.processEvents()
            seen["fit_kept"] = (
                dialog.fit_check.isChecked(),
                not any(w.isEnabled() for w in dialog._zoom_widgets),
            )

        canyon.open(script)
        assert seen["fit_shown"] and seen["fit_on"] and seen["row_off"]
        assert (
            seen["note"] == "Scale is set automatically when fitting the whole feature."
        )
        assert seen["note_shown"]
        assert seen["row_back"] and seen["restored"] == ("25,000", 3)
        assert seen["note_gone"] and seen["off_again"]
        assert seen["coordinates_enabled"] and seen["fit_kept"] == (True, True)

    def test_go_fits_when_fitting_and_uses_the_manual_scale_when_not(
        self, canyon
    ) -> None:
        item = canyon.world.main
        results = {}
        for fit in (True, False):
            item.setExtent(canyon.world.main.extent())

            def script(dialog, fit=fit) -> None:
                _preset(dialog, 4)
                dialog.tabs.setCurrentIndex(1)
                self._open_polygon(dialog, canyon)
                if dialog.fit_check.isChecked() != fit:
                    _toggle(dialog.fit_check)
                _click(dialog.go_button)

            canyon.open(script)
            results[fit] = item.scale()
            canyon.stack.undo()
        assert math.isclose(results[False], 12_500, rel_tol=1e-6)
        assert not math.isclose(results[True], 12_500, rel_tol=1e-2)

    def test_the_fit_choice_survives_another_result(self, canyon) -> None:
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            choose_layer(dialog, canyon.world.areas)
            search(dialog, "twin")
            _toggle(dialog.fit_check)  # fit off
            search(dialog, "basin")
            seen["after"] = dialog.fit_check.isChecked()

        canyon.open(script)
        assert seen["after"] is False


class TestTheSameStartTheSameResult:
    """The frame ends up in the same place whatever was searched before."""

    def _history(self, canyon, steps, finish) -> tuple[float, float, float]:
        item = canyon.world.main
        item.setExtent(self._start)

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            for step in steps:
                step(dialog)
            finish(dialog)

        canyon.open(script)
        return _view(item)

    @staticmethod
    def _look_up(layer_name: str, query: str, row: int | None = None):
        def step(dialog) -> None:
            layer = dialog.target.project.mapLayersByName(layer_name)[0]
            choose_layer(dialog, layer)
            search(dialog, query)
            if row is not None:
                click_row(dialog, row)

        return step

    @staticmethod
    def _with(preset: int | None, custom: str | None):
        def step(dialog) -> None:
            if preset is not None:
                _preset(dialog, preset)
            if custom is not None:
                _type_scale(dialog, custom)

        return step

    @staticmethod
    def _go_with_the_mouse(dialog) -> None:
        _click(dialog.go_button)

    @staticmethod
    def _go_with_enter(dialog) -> None:
        dialog.results.setFocus()
        press(dialog.results, Qt.Key.Key_Return)

    @pytest.mark.parametrize(
        ("target", "lon", "lat"),
        [
            (("Viewpoints", "hance", None), -112.00, 36.05),
            (("Viewpoints", "desert", 0), -111.98, 36.12),
        ],
        ids=["one result", "several results"],
    )
    @pytest.mark.parametrize(
        ("preset", "custom", "scale"),
        [(3, None, 25_000), (None, "30000", 30_000)],
        ids=["closer preset", "exact scale"],
    )
    @pytest.mark.parametrize("enter", [False, True], ids=["Go button", "Enter"])
    def test_first_search_later_search_and_reopening_agree(
        self, canyon, target, lon, lat, preset, custom, scale, enter
    ) -> None:
        self._start = canyon.world.main.extent()
        wanted = self._look_up(*target)
        other = self._look_up("Viewpoints", "mather")
        polygon = self._look_up("Areas", "basin")
        scale_step = self._with(preset, custom)
        finish = self._go_with_enter if enter else self._go_with_the_mouse

        first = self._history(canyon, [scale_step, wanted], finish)
        after_other = self._history(canyon, [other, scale_step, wanted], finish)
        after_polygon = self._history(canyon, [scale_step, polygon, wanted], finish)
        scale_last = self._history(canyon, [wanted, scale_step], finish)
        reopened = self._history(canyon, [scale_step, wanted], finish)

        assert math.isclose(first[0], scale, rel_tol=1e-6)
        assert _centre_at(canyon.world.main, lon, lat)
        histories = {
            "another search first": after_other,
            "a fitted polygon first": after_polygon,
            "the scale chosen last": scale_last,
            "reopened": reopened,
        }
        for name, history in histories.items():
            assert _same_view(first, history), name

    def test_nothing_is_applied_until_go(self, canyon) -> None:
        item = canyon.world.main
        canyon.project.setDirty(False)
        before = (_view(item), canyon.stack.index())

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            self._look_up("Viewpoints", "hance")(dialog)
            _preset(dialog, 4)
            _type_scale(dialog, "5000")

        canyon.open(script)
        assert (_view(item), canyon.stack.index()) == before


@pytest.fixture
def roomy_screen(monkeypatch):
    """A screen tall enough for the dialog's natural size.

    Qt 5's offscreen screen is 800x600 and Qt 6's is 800x800; on the shorter
    one the dialog is capped to the screen and its page scrolls, which is what
    a small real screen does (covered by the small-screen test below) and not
    what these tests are about.
    """
    from nika_onlymap_exporter.ui.cartograph import go_to_location_dialog as module

    monkeypatch.setattr(
        module.GoToLocationDialog, "_screen_size", lambda self: QSize(1024, 1000)
    )


class TestTheWindowStaysPut:
    def _geometry(self, dialog) -> dict[str, object]:
        def y(widget) -> int:
            return widget.mapTo(dialog, widget.rect().topLeft()).y()

        return {
            "size": (dialog.width(), dialog.height()),
            "pos": (dialog.pos().x(), dialog.pos().y()),
            "tab_bar": y(dialog.tabs.tabBar()),
            "zoom": (y(dialog.zoom_control), dialog.zoom_control.height()),
            "scale": (y(dialog.scale_field), dialog.scale_field.height()),
            "footer": (y(dialog.go_button), y(dialog.cancel_button)),
        }

    def test_switching_tabs_changes_nothing_about_the_window(self, canyon) -> None:
        seen = []

        def script(dialog) -> None:
            for index in (0, 1, 0, 1, 0):
                dialog.tabs.setCurrentIndex(index)
                QApplication.processEvents()
                seen.append(self._geometry(dialog))

        canyon.open(script)
        assert all(state == seen[0] for state in seen), seen

    def test_a_size_the_user_chose_is_kept_across_tabs(self, canyon) -> None:
        seen = []

        def script(dialog) -> None:
            dialog.resize(700, 680)
            QApplication.processEvents()
            for index in (1, 0, 1):
                dialog.tabs.setCurrentIndex(index)
                QApplication.processEvents()
                seen.append(self._geometry(dialog))

        canyon.open(script)
        assert all(state["size"] == (700, 680) for state in seen)
        assert all(state == seen[0] for state in seen)

    def test_the_first_size_shows_about_three_results_once_a_search_has_answered(
        self, canyon, roomy_screen
    ) -> None:
        layer = _named_layer(canyon, [f"Spot {i:02d}" for i in range(12)])
        seen = {}

        def script(dialog) -> None:
            seen["width"] = dialog.width()
            dialog.tabs.setCurrentIndex(1)
            choose_layer(dialog, layer)
            search(dialog, "spot")
            QApplication.processEvents()
            row = dialog.results.visualItemRect(dialog.results.item(0)).height()
            seen["rows"] = dialog.results.viewport().height() / row
            seen["scrolls"] = (
                dialog.tabs.currentWidget().verticalScrollBar().isVisible()
            )
            seen["height"] = dialog.height()

        canyon.open(script)
        assert seen["width"] == 620
        assert 2.5 <= seen["rows"] <= 4, seen
        assert seen["scrolls"] is False
        assert seen["height"] < 800  # not the whole 1000 px screen

    def test_inputs_survive_switching_tabs(self, canyon) -> None:
        world = canyon.world
        seen = {}

        def script(dialog) -> None:
            choose_crs(dialog, "EPSG:32612")
            fill_coordinates(dialog, "400000", "4000000")
            _preset(dialog, 3)
            choose_layer(dialog, world.viewpoints)
            search(dialog, "desert")
            click_row(dialog, 1)
            before = self._inputs(dialog)
            for index in (0, 1, 0, 1):
                dialog.tabs.setCurrentIndex(index)
                QApplication.processEvents()
            seen["before"], seen["after"] = before, self._inputs(dialog)

        canyon.open(script)
        assert seen["before"] == seen["after"]
        assert seen["after"]["coords"] == ("400000", "4000000")
        assert seen["after"]["scale"] == ("25,000", 3)
        assert seen["after"]["query"] == "desert" and len(seen["after"]["rows"]) == 2
        assert seen["after"]["selected"] == 1

    @staticmethod
    def _inputs(dialog) -> dict[str, object]:
        return {
            "coords": (dialog.first_edit.text(), dialog.second_edit.text()),
            "crs": dialog.crs_value.text(),
            "query": dialog.query_edit.text(),
            "layer": dialog.layer_combo.currentText(),
            "rows": [
                dialog.results.item(i).text() for i in range(dialog.results.count())
            ],
            "selected": dialog.results.currentRow(),
            "scale": (dialog.scale_edit.text(), dialog.zoom_control.selected()),
            "fit": dialog.fit_check.isChecked(),
        }

    def test_the_results_scroll_in_their_own_area(self, canyon, roomy_screen) -> None:
        layer = _named_layer(canyon, [f"Spot {i:02d}" for i in range(20)])
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            QApplication.processEvents()
            seen["size"] = self._geometry(dialog)
            choose_layer(dialog, layer)
            search(dialog, "spot")
            QApplication.processEvents()
            seen["after"] = self._geometry(dialog)
            seen["list_scrolls"] = dialog.results.verticalScrollBar().maximum() > 0
            seen["page_scrolls"] = (
                dialog.tabs.currentWidget().verticalScrollBar().isVisible()
            )

        canyon.open(script)
        assert seen["size"] == seen["after"]
        assert seen["list_scrolls"] and not seen["page_scrolls"]

    def test_a_small_screen_scrolls_the_page_and_keeps_the_row_and_footer(
        self, canyon, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.cartograph import go_to_location_dialog as module

        monkeypatch.setattr(
            module.GoToLocationDialog, "_screen_size", lambda self: QSize(900, 520)
        )
        seen = {}

        def script(dialog) -> None:
            dialog.tabs.setCurrentIndex(1)
            QApplication.processEvents()
            seen["height"] = dialog.height()
            seen["scrolls"] = (
                dialog.tabs.currentWidget().verticalScrollBar().maximum() > 0
            )
            for name in ("zoom_control", "scale_field", "go_button", "cancel_button"):
                widget = getattr(dialog, name)
                bottom = widget.mapTo(dialog, widget.rect().bottomLeft()).y()
                seen[name] = 0 <= bottom <= dialog.height()
            dialog.tabs.setCurrentIndex(0)
            QApplication.processEvents()
            seen["same"] = dialog.height() == seen["height"]

        canyon.open(script)
        assert seen["height"] <= 520 - 40
        assert seen["scrolls"] and seen["same"]
        assert all(
            seen[n]
            for n in ("zoom_control", "scale_field", "go_button", "cancel_button")
        )


def _row_problems(dialog) -> list[str]:
    """Everything wrong with the Zoom row's layout, in words."""
    problems = []
    parts = [*dialog.zoom_control.buttons, dialog.scale_field, dialog.zoom_label]

    def centre(widget) -> float:
        top = widget.mapTo(dialog, widget.rect().topLeft()).y()
        return top + widget.height() / 2

    middle = centre(dialog.zoom_control.buttons[0])
    for widget in parts:
        if abs(centre(widget) - middle) > 1.01:
            problems.append(f"{widget} is off the row's line")
        corner = widget.mapTo(dialog, widget.rect().bottomRight())
        if corner.x() > dialog.width() or corner.y() > dialog.height():
            problems.append(f"{widget} leaves the dialog")
    for button in dialog.zoom_control.buttons:
        if button.fontMetrics().horizontalAdvance(button.text()) + 4 > button.width():
            problems.append(f"{button.text()} is clipped")
        if button.height() < 30:
            problems.append("a segment is not input height")
    widths = {b.width() for b in dialog.zoom_control.buttons}
    if max(widths) - min(widths) > 1:
        problems.append(f"segments differ in width: {sorted(widths)}")
    edit = dialog.scale_edit
    need = edit.fontMetrics().horizontalAdvance("1,000,000")
    if edit.width() < need:
        problems.append(f"the scale field is {edit.width()} px for {need} px of text")
    if dialog.zoom_control.height() != dialog.scale_field.height():
        problems.append("control and field differ in height")
    return problems


class TestTheRowFitsOnOneLine:
    @pytest.mark.parametrize("width", [520, 620, 900])
    @pytest.mark.parametrize("font", [1.0, 1.25, 1.5])
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_one_row_everywhere(self, project, qgis_app, width, font, theme) -> None:
        from nika_onlymap_exporter.ui import theme as theme_module

        base = QApplication.font()
        bigger = QApplication.font()
        bigger.setPointSizeF(base.pointSizeF() * font)
        QApplication.setFont(bigger)
        from qgis.PyQt.QtCore import QSettings

        QSettings().setValue(theme_module.THEME_KEY, theme)
        made = Setup(project)
        seen = {}
        try:

            def script(dialog) -> None:
                for index in (0, 1):
                    dialog.tabs.setCurrentIndex(index)
                    dialog.resize(width, max(dialog.height(), 560))
                    QApplication.processEvents()
                    seen[index] = _row_problems(dialog)
                    if SHOTS:
                        out = Path(SHOTS)
                        name = f"{theme}-{font}x-{width}-{'feature' if index else 'coordinates'}.png"
                        dialog.grab().save(str(out / name))

            made.open(script)
        finally:
            made.close()
            QApplication.setFont(base)
        assert seen == {0: [], 1: []}
