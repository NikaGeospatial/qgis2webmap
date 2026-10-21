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
    assert [inset.dpi_combo.itemText(i) for i in range(3)] == ["96", "150", "300"]
    assert main.match_button.text() == "Use canvas view"
    assert main.property("role") == "frameCard"
    assert main.name_label.property("role") == "frameName"
    assert main.crs_label.property("role") == "hint"
    assert main.issues_label.property("role") == "frameIssues"
    assert main.match_button.property("role") == "secondary"
    assert main.match_button.property("hasMenu") is False


def test_dpi_list_hidden_while_live(frames):
    frames.set_frames(_rows())
    assert frames.card("map1").dpi_combo.isHidden()
    assert not frames.card("map2").dpi_combo.isHidden()


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
    assert not main.dpi_combo.isHidden()
    main.mode_combo.setCurrentIndex(main.mode_combo.findData("live"))
    assert record.modes[-1] == ("map1", "live")
    assert main.dpi_combo.isHidden()


def test_user_picking_dpi_reports_it(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    inset = frames.card("map2")
    inset.dpi_combo.setCurrentIndex(inset.dpi_combo.findData(96))
    assert record.dpis == [("map2", 96)]


def test_use_canvas_view_reports_and_follows_canvas(frames):
    record = Recorder(frames)
    frames.set_frames(_rows())
    frames.card("map2").match_button.click()
    assert record.matches == ["map2"]
    frames.set_canvas_available(False)
    assert not frames.card("map1").match_button.isEnabled()
    assert not frames.card("map2").match_button.isEnabled()
    # New cards start in the canvas state already known.
    frames.set_frames(_rows())
    assert not frames.card("map1").match_button.isEnabled()
    frames.set_canvas_available(True)
    assert frames.card("map1").match_button.isEnabled()


def test_issue_chip(frames):
    frames.set_frames(_rows())
    main, inset = frames.card("map1"), frames.card("map2")
    assert main.issues_label.isHidden()
    assert not inset.issues_label.isHidden()
    assert inset.issues_label.text() == "3 changes"
    frames.set_issue_count("map2", 1)
    assert inset.issues_label.text() == "1 change"
    frames.set_issue_count("map1", 0)
    assert not main.issues_label.isHidden()
    assert main.issues_label.text() == "No changes"
    assert main.issues_label.property("muted") is True
    frames.set_issue_count("map1", None)
    assert main.issues_label.isHidden()
    frames.set_issue_count("gone", 4)  # an unknown map is ignored
    assert "Fidelity tab" in inset.issues_label.toolTip()


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
    assert frames.summary() == "2 live, 1 static"
    frames.set_frames(rows[:1])
    assert frames.summary() == "1 live"
    frames.set_frames(rows[1:2])
    assert frames.summary() == "1 static"
    card = frames.card("map2")
    card.mode_combo.setCurrentIndex(card.mode_combo.findData("live"))
    assert frames.summary() == "1 live"
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
    old.match_button.click()
    assert record.modes == [] and record.dpis == [] and record.matches == []
    # Same id, new card: only the new one reports.
    frames.set_frames(_rows())
    stale = frames.card("map2")
    frames.set_frames(_rows())
    stale.match_button.click()
    assert record.matches == []
    frames.card("map2").match_button.click()
    assert record.matches == ["map2"]
    assert len(_shown(frames, FrameCard)) == 2


def test_intro_note_kept(frames):
    assert frames.intro.text().startswith("One row per map on the selected layout.")
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
    assert inset.dpi_combo.isHidden()
    assert inset.issues_label.text() == "No changes"
