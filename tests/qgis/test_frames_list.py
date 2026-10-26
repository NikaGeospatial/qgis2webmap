"""The Cartograph panel's map frames list: a card per map item.

Needs PyQGIS and a Qt application; runs headless with QT_QPA_PLATFORM=offscreen.
The cards are driven the way the user drives them - a combo moved, a button
clicked - and checked for what they show and what they report.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

DPI_CHOICES = (96, 150, 300)


def _rows():
    from nika_onlymap_exporter.ui.cartograph.frames_list import FrameRow

    return [
        FrameRow("map1", "Main map", "EPSG:3857", "live", 150, None),
        FrameRow("map2", "Inset", "EPSG:4326", "static", 300, 3),
    ]


@pytest.fixture
def frames(qgis_app):
    from nika_onlymap_exporter.ui.cartograph.frames_list import FramesList

    widget = FramesList(DPI_CHOICES)
    yield widget
    widget.deleteLater()


class Recorder:
    def __init__(self, frames) -> None:
        self.modes: list[tuple[str, str]] = []
        self.dpis: list[tuple[str, int]] = []
        self.matches: list[str] = []
        frames.mode_changed.connect(lambda i, m: self.modes.append((i, m)))
        frames.dpi_changed.connect(lambda i, d: self.dpis.append((i, d)))
        frames.match_canvas_requested.connect(self.matches.append)
        self.located: list[str] = []
        self.selected: list[str] = []
        self.issues: list[str] = []
        frames.locate_requested.connect(self.located.append)
        frames.frame_selected.connect(self.selected.append)
        frames.issues_requested.connect(self.issues.append)


def test_cards_show_name_crs_mode_and_dpi(frames):
    frames.set_frames(_rows())
    main, inset = frames.card("map1"), frames.card("map2")
    assert main.name_label.text() == "Main map"
    assert main.crs_label.text() == "EPSG:3857"
    assert inset.name_label.text() == "Inset"
    assert inset.crs_label.text() == "EPSG:4326"
    assert main.mode_combo.currentData() == "live"
    assert inset.mode_combo.currentData() == "static"
    assert main.dpi_combo.currentData() == 150
    assert inset.dpi_combo.currentData() == 300
    assert [inset.dpi_combo.itemText(i) for i in range(3)] == [
        "96 dpi",
        "150 dpi",
        "300 dpi",
    ]
    assert [main.mode_combo.itemText(i) for i in range(2)] == ["Interactive", "Image"]
    assert [a.text() for a in main.actions_button.menu().actions()] == [
        "Use canvas extent",
        "Locate on page",
    ]
    assert main.property("role") == "frameCard"
    assert main.name_label.property("role") == "frameName"
    assert main.crs_label.property("role") == "hint"
    assert main.issues_button.property("role") == "frameIssues"


def test_resolution_row_only_in_image_mode(frames):
    frames.set_frames(_rows())
    live, image = frames.card("map1"), frames.card("map2")
    assert live.resolution_row.isHidden()
    assert not image.resolution_row.isHidden()
    assert image.resolution_label.text() == "Resolution"
    # No permanent heading or filled card around the entries.
    labels = {label.text() for label in frames.findChildren(type(live.name_label))}
    assert "In the HTML" not in labels


def test_compact_structure(frames):
    """Name, type and actions on one line; CRS and badge on the next."""
    frames.set_frames(_rows())
    frames.resize(360, 400)
    frames.show()
    card = frames.card("map2")
    top = lambda w: w.mapTo(card, w.rect().topLeft()).y()  # noqa: E731
    assert top(card.name_label) == top(card.mode_combo) == top(card.actions_button)
    assert top(card.crs_label) > top(card.name_label)
    assert top(card.issues_button) > top(card.name_label)
    assert abs(top(card.crs_label) - top(card.issues_button)) < 12
    assert top(card.resolution_row) > top(card.crs_label)


def test_the_help_line_hides_the_intro(frames):
    assert frames.intro.isHidden()
    frames.help_button.click()
    assert not frames.intro.isHidden()
    frames.help_button.click()
    assert frames.intro.isHidden()


def test_combos_ignore_the_wheel_and_take_no_click_focus(frames):
    from qgis.PyQt.QtCore import QPoint, QPointF, Qt
    from qgis.PyQt.QtGui import QWheelEvent

    frames.set_frames(_rows())
    card = frames.card("map2")
    for combo in (card.mode_combo, card.dpi_combo):
        assert combo.focusPolicy() == Qt.FocusPolicy.TabFocus
        before = combo.currentIndex()
        event = QWheelEvent(
            QPointF(5, 5),
            QPointF(5, 5),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        combo.wheelEvent(event)
        assert not event.isAccepted()
        assert combo.currentIndex() == before


def test_filling_the_cards_reports_no_edit(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    frames.set_frames(_rows())
    assert record.modes == [] and record.dpis == []


def test_user_switching_mode_reports_it_and_shows_or_hides_dpi(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    main = frames.card("map1")
    main.mode_combo.setCurrentIndex(main.mode_combo.findData("static"))
    assert record.modes == [("map1", "static")]
    assert not main.resolution_row.isHidden()
    main.mode_combo.setCurrentIndex(main.mode_combo.findData("live"))
    assert record.modes[-1] == ("map1", "live")
    assert main.resolution_row.isHidden()


def test_user_picking_dpi_reports_it(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    inset = frames.card("map2")
    inset.dpi_combo.setCurrentIndex(inset.dpi_combo.findData(96))
    assert record.dpis == [("map2", 96)]


def test_use_canvas_view_reports_and_follows_canvas(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    frames.card("map2").canvas_action.trigger()
    assert record.matches == ["map2"]
    frames.set_canvas_available(False)
    assert not frames.card("map1").canvas_action.isEnabled()
    assert not frames.card("map2").canvas_action.isEnabled()
    # New cards start in the canvas state already known.
    frames.set_frames(_rows())
    assert not frames.card("map1").canvas_action.isEnabled()
    frames.set_canvas_available(True)
    assert frames.card("map1").canvas_action.isEnabled()


def test_locate_selects_and_reports(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    frames.card("map2").locate_action.trigger()
    assert record.located == ["map2"]


def test_choosing_an_entry_selects_its_frame_once(frames):
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtTest import QTest

    record = Recorder(frames)
    frames.set_frames(_rows())
    frames.show()
    QTest.mouseClick(frames.card("map2").name_label, Qt.MouseButton.LeftButton)
    assert record.selected == ["map2"]
    assert frames.card("map2").property("selected") is True
    QTest.mouseClick(frames.card("map2").crs_label, Qt.MouseButton.LeftButton)
    assert record.selected == ["map2"]  # already the chosen one
    # A frame selected on the page marks its entry, without reporting.
    frames.set_selected("map1")
    assert frames.card("map1").property("selected") is True
    assert frames.card("map2").property("selected") is False
    assert record.selected == ["map2"]
    frames.set_selected(None)
    assert frames.card("map1").property("selected") is False


def test_issue_badge(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    main, inset = frames.card("map1"), frames.card("map2")
    assert main.issues_button.isHidden()
    assert not inset.issues_button.isHidden()
    assert inset.issues_button.text() == "3 issues \u2192"
    frames.set_issue_count("map2", 1)
    assert inset.issues_button.text() == "1 issue \u2192"
    assert "Inset" in inset.issues_button.accessibleName()
    inset.issues_button.click()
    assert record.issues == ["map2"]
    frames.set_issue_count("map1", 0)
    assert main.issues_button.isHidden()
    frames.set_issue_count("map1", None)
    assert main.issues_button.isHidden()
    frames.set_issue_count("gone", 4)  # an unknown map is ignored
    assert "Checks" in inset.issues_button.toolTip()


def test_empty_note_only_for_a_layout_without_maps(frames):
    frames.set_frames(None)
    assert frames.empty_note.isHidden()
    frames.set_frames([])
    assert not frames.empty_note.isHidden()
    assert frames.empty_note.text() == "This layout has no maps on it yet."
    frames.set_frames(_rows())
    assert frames.empty_note.isHidden()
    frames.set_frames(None)
    assert frames.empty_note.isHidden()


def test_summary(frames):
    from nika_onlymap_exporter.ui.cartograph.frames_list import FrameRow

    assert frames.summary() == ""
    frames.set_frames([])
    assert frames.summary() == "No maps"
    rows = [*_rows(), FrameRow("map3", "Third", "EPSG:3857", "live", 96, None)]
    frames.set_frames(rows)
    assert frames.summary() == "2 interactive maps, 1 image"
    frames.set_frames(rows[:1])
    assert frames.summary() == "1 interactive map"
    frames.set_frames(rows[1:2])
    assert frames.summary() == "1 image"
    card = frames.card("map2")
    card.mode_combo.setCurrentIndex(card.mode_combo.findData("live"))
    assert frames.summary() == "1 interactive map"
    frames.set_frames(None)
    assert frames.summary() == ""


def _shown(frames, kind):
    return [card for card in frames.findChildren(kind) if not card.isHidden()]


def test_replacing_frames_drops_the_old_cards(frames):
    from nika_onlymap_exporter.ui.cartograph.frames_list import FrameCard

    record = Recorder(frames)
    frames.set_frames(_rows())
    old = frames.card("map1")
    frames.set_frames(_rows()[1:])
    # Old cards are hidden at once and deleted by Qt on the next turn of the
    # event loop, so only the shown ones count.
    assert len(_shown(frames, FrameCard)) == 1
    with pytest.raises(KeyError):
        frames.card("map1")
    assert frames.card("map2") is not old
    # The old card is gone from view, and its controls no longer report.
    old.mode_combo.setCurrentIndex(1)
    old.dpi_combo.setCurrentIndex(0)
    old.canvas_action.trigger()
    assert record.modes == [] and record.dpis == [] and record.matches == []
    # Same id, new card: only the new one reports.
    frames.set_frames(_rows())
    stale = frames.card("map2")
    frames.set_frames(_rows())
    stale.canvas_action.trigger()
    assert record.matches == []
    frames.card("map2").canvas_action.trigger()
    assert record.matches == ["map2"]
    assert len(_shown(frames, FrameCard)) == 2


def test_intro_note_kept(frames):
    assert frames.intro.text().startswith("Interactive maps are panned and zoomed")
    assert frames.intro.wordWrap()
    assert frames.intro.property("role") == "hint"


def test_refreshing_a_card_reports_no_edit(frames):
    from nika_onlymap_exporter.ui.cartograph.frames_list import FrameRow

    record = Recorder(frames)
    frames.set_frames(_rows())
    inset = frames.card("map2")
    inset.set_values(FrameRow("map2", "Inset", "EPSG:4326", "live", 96, 0))
    assert record.modes == [] and record.dpis == []
    assert inset.mode_combo.currentData() == "live"
    assert inset.dpi_combo.currentData() == 96
    assert inset.resolution_row.isHidden()
    assert inset.issues_button.isHidden()
