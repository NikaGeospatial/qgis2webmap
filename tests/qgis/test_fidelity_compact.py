"""The Checks list in the cartograph's narrow panel (`FidelityPanel.set_compact`).

A third of the dialog wide, the three-column tree scrolled sideways and kept
each sentence in a pane below it. Compact mode is one column of lines that
open in place. What is proved here: the lines are the rows the tree would
show under the same filters, opening one shows its sentence and the button to
its fix, nothing scrolls sideways at 320 px, switching back and forth loses
nothing, and a long report stays a page at a time.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityItem, FidelityStatus
from nika_onlymap_exporter.core.fidelity_summary import Topic, summarise

qgis_core = pytest.importorskip("qgis.core")

NAMES = {"r": "roads", "p": "parks"}


def mixed_items() -> list[FidelityItem]:
    items = [
        FidelityItem(
            "Popup fields of 'roads'",
            FidelityStatus.UNSUPPORTED,
            "A field is hidden. Hidden fields never reach the web map's popups.",
            "r",
        ),
        FidelityItem(
            "Symbology of 'roads'", FidelityStatus.PRESERVED, "Translated.", "r"
        ),
        FidelityItem(
            "Symbology of 'parks'", FidelityStatus.PRESERVED, "Translated.", "p"
        ),
        FidelityItem("Basemap", FidelityStatus.UNSUPPORTED, "The map has no basemap."),
    ]
    items.extend(
        FidelityItem(
            f"Symbology of 'roads', range {n}",
            FidelityStatus.APPROXIMATED,
            "Only the top symbol layer.",
            "r",
        )
        for n in (1, 2, 3)
    )
    return items


@pytest.fixture
def panel(qgis_app):
    from nika_onlymap_exporter.ui.fidelity_panel import FidelityPanel

    built = FidelityPanel()
    built.resize(380, 700)
    built.show()
    built.set_compact(True)
    built.show_summary(summarise(mixed_items(), NAMES))
    qgis_app.processEvents()
    yield built
    built.close()
    built.deleteLater()
    qgis_app.processEvents()


def subjects(panel) -> list[str]:
    return [line.subject.text() for line in panel.compact_rows()]


def tree_rows(panel) -> list[str]:
    """The rows the (hidden) tree shows under the same filters."""
    rows = []
    for index in range(panel.tree.topLevelItemCount()):
        group = panel.tree.topLevelItem(index)
        rows.extend(group.child(i).text(0) for i in range(group.childCount()))
    return rows


def line(panel, subject: str):
    return next(row for row in panel.compact_rows() if row.subject.text() == subject)


def press(widget, key) -> None:
    from qgis.PyQt.QtCore import QEvent, Qt
    from qgis.PyQt.QtGui import QKeyEvent
    from qgis.PyQt.QtWidgets import QApplication

    QApplication.sendEvent(
        widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    )


class TestLines:
    def test_the_list_replaces_the_tree_and_its_pane(self, panel) -> None:
        assert panel.compact_list.isVisibleTo(panel)
        assert not panel.tree.isVisibleTo(panel)
        assert not panel.detail_text.isVisibleTo(panel)

    def test_the_lines_are_the_rows_the_tree_shows(self, panel) -> None:
        assert subjects(panel) == ["Basemap", "Popup fields", "Symbology (3 classes)"]
        assert subjects(panel) == tree_rows(panel)

    def test_a_line_says_verdict_subject_and_the_outcome_first(self, panel) -> None:
        popup = line(panel, "Popup fields")
        assert popup.verdict.text() == "Not exported"
        assert popup.outcome.text() == "A field is hidden."
        assert popup.accessibleName() == "Not exported: roads: Popup fields"

    def test_lines_are_focusable_and_styled_by_role(self, panel) -> None:
        from qgis.PyQt.QtCore import Qt

        popup = line(panel, "Popup fields")
        assert popup.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert popup.property("role") == "checkRow"
        assert popup.verdict.property("tone") == "warning"

    def test_the_detail_is_built_only_when_opened(self, panel) -> None:
        assert all(row.detail is None for row in panel.compact_rows())

    def test_every_label_wraps(self, panel) -> None:
        from qgis.PyQt.QtWidgets import QLabel

        popup = line(panel, "Popup fields")
        popup.set_expanded(True)
        labels = [
            label
            for label in panel.compact_list.widget().findChildren(QLabel)
            if label.isVisibleTo(panel) and label.text() not in ("", "▸", "▾")
        ]
        assert labels
        assert all(label.wordWrap() for label in labels)


class TestOpening:
    def test_enter_opens_onto_the_sentence_and_the_layer_jump(self, panel) -> None:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QLabel, QPushButton

        asked: list[str] = []
        panel.show_layer_requested.connect(asked.append)
        popup = line(panel, "Popup fields")
        press(popup, Qt.Key.Key_Return)

        assert popup.is_expanded()
        assert popup.property("expanded") is True
        texts = [label.text() for label in popup.detail.findChildren(QLabel)]
        assert texts == [
            "A field is hidden. Hidden fields never reach the web map's popups."
        ]
        (button,) = popup.detail.findChildren(QPushButton)
        assert button.text() == "Show in Layers"
        button.click()
        assert asked == ["r"]

    def test_space_closes_it_again(self, panel) -> None:
        from qgis.PyQt.QtCore import Qt

        popup = line(panel, "Popup fields")
        press(popup, Qt.Key.Key_Space)
        assert popup.detail.isVisibleTo(popup)
        press(popup, Qt.Key.Key_Space)
        assert not popup.is_expanded()
        assert not popup.detail.isVisibleTo(popup)

    def test_a_map_setting_jumps_to_the_map_tab(self, panel) -> None:
        from qgis.PyQt.QtWidgets import QLabel, QPushButton

        tabs: list[str] = []
        layers: list[str] = []
        panel.show_tab_requested.connect(tabs.append)
        panel.show_layer_requested.connect(layers.append)
        basemap = line(panel, "Basemap")
        basemap.set_expanded(True)
        # One sentence, already on the line: opening adds only the jump.
        assert basemap.detail.findChildren(QLabel) == []
        (button,) = basemap.detail.findChildren(QPushButton)
        assert button.text() == "Go to Map tab"
        button.click()
        assert (tabs, layers) == (["Map"], [])

    def test_a_merged_line_says_what_it_applies_to(self, panel) -> None:
        from qgis.PyQt.QtWidgets import QLabel

        merged = line(panel, "Symbology (3 classes)")
        merged.set_expanded(True)
        (text,) = merged.detail.findChildren(QLabel)
        assert "Applies to: Symbology of 'roads', range 1;" in text.text()


class TestFilters:
    def test_unticking_a_verdict_hides_its_lines(self, panel) -> None:
        panel.verdict_checks[FidelityStatus.UNSUPPORTED].setChecked(False)
        assert subjects(panel) == ["Symbology (3 classes)"]
        assert subjects(panel) == tree_rows(panel)

    def test_unticking_a_topic_hides_its_lines(self, panel) -> None:
        panel.topic_checks[Topic.MAP].setChecked(False)
        assert "Basemap" not in subjects(panel)
        assert subjects(panel) == tree_rows(panel)

    def test_ticking_kept_shows_kept_lines(self, panel) -> None:
        assert "Symbology" not in subjects(panel)
        panel.kept_toggle.setChecked(True)
        assert subjects(panel).count("Symbology") == 2
        assert subjects(panel) == tree_rows(panel)

    def test_search_narrows_the_lines(self, panel) -> None:
        panel.search.setText("basemap")
        assert subjects(panel) == ["Basemap"]

    def test_filtering_everything_out_says_so(self, panel) -> None:
        panel.search.setText("no such words anywhere")
        assert subjects(panel) == []
        assert panel.compact_note.text() == "Nothing matches these filters"
        assert panel.compact_note.isVisibleTo(panel)

    def test_the_note_goes_once_rows_are_back(self, panel) -> None:
        panel.search.setText("no such words anywhere")
        panel.search.setText("")
        assert not panel.compact_note.isVisibleTo(panel)
        assert subjects(panel)


class TestSwitching:
    def test_back_to_the_web_map_restores_the_tree(self, panel) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import DETAIL_COLUMN

        panel.set_compact(False)
        assert panel.tree.isVisibleTo(panel)
        assert panel.detail_text.isVisibleTo(panel)
        assert not panel.compact_list.isVisibleTo(panel)
        assert not panel.tree.isColumnHidden(DETAIL_COLUMN)

    def test_filters_and_search_survive_switching(self, panel) -> None:
        panel.search.setText("roads")
        panel.verdict_checks[FidelityStatus.APPROXIMATED].setChecked(False)
        for _ in range(3):
            panel.set_compact(False)
            assert tree_rows(panel) == ["Popup fields"]
            panel.set_compact(True)
            assert panel.search.text() == "roads"
            assert subjects(panel) == ["Popup fields"]

    def test_a_filter_changed_while_wide_is_applied_on_return(self, panel) -> None:
        panel.set_compact(False)
        panel.search.setText("basemap")
        panel.set_compact(True)
        assert subjects(panel) == ["Basemap"]
        panel.set_compact(False)
        panel.search.clear()
        panel.verdict_checks[FidelityStatus.UNSUPPORTED].setChecked(False)
        panel.set_compact(True)
        assert subjects(panel) == ["Symbology (3 classes)"]

    def test_a_report_shown_while_wide_is_the_one_listed(self, panel) -> None:
        panel.set_compact(False)
        panel.show_summary(
            summarise(
                [FidelityItem("Terrain", FidelityStatus.UNSUPPORTED, "No terrain.")],
                NAMES,
            )
        )
        panel.set_compact(True)
        assert subjects(panel) == ["Terrain"]

    def test_a_failed_check_says_why_in_the_list(self, panel) -> None:
        panel.show_error("boom")
        assert subjects(panel) == []
        assert panel.compact_note.text() == "Check failed"
        assert "boom" in panel.compact_note_text.text()

    def test_not_checked_says_so(self, panel) -> None:
        panel.show_not_checked()
        assert subjects(panel) == []
        assert panel.compact_note.text() == "Not checked yet"


def test_nothing_scrolls_sideways_at_320_px(qgis_app) -> None:
    from nika_onlymap_exporter.ui.fidelity_panel import FidelityPanel

    long_detail = "A long sentence about what changes on the way out. " * 8
    items = [
        FidelityItem(
            f"Labels on 'a layer with a rather long descriptive name {n}'",
            FidelityStatus.APPROXIMATED,
            long_detail,
            f"l{n}",
        )
        for n in range(4)
    ]
    # And map-wide rows, whose subject is the whole subject, not a short label.
    items.extend(
        FidelityItem(
            f"Picture 'a north arrow with a rather long descriptive name {n}'",
            FidelityStatus.UNSUPPORTED,
            long_detail,
        )
        for n in range(3)
    )
    names = {
        f"l{n}": f"a layer with a rather long descriptive name {n}" for n in range(4)
    }
    built = FidelityPanel()
    built.set_compact(True)
    built.show_summary(summarise(items, names))
    built.resize(320, 700)
    built.show()
    for row in built.compact_rows():
        row.set_expanded(True)
    qgis_app.processEvents()

    area = built.compact_list
    assert built.minimumSizeHint().width() <= 320
    assert area.horizontalScrollBar().maximum() == 0
    assert area.widget().width() <= area.viewport().width()
    for row in built.compact_rows():
        assert row.geometry().right() <= area.viewport().width()
    built.close()
    built.deleteLater()
    qgis_app.processEvents()


def test_a_long_report_is_built_a_page_at_a_time(qgis_app) -> None:
    from nika_onlymap_exporter.ui.fidelity_panel import COMPACT_PAGE, FidelityPanel

    items = [
        FidelityItem(f"Thing {n}", FidelityStatus.UNSUPPORTED, f"Detail {n}.")
        for n in range(250)
    ]
    built = FidelityPanel()
    built.set_compact(True)
    built.show_summary(summarise(items, {}))

    # The Whole map heading takes one line of the first page.
    assert len(built.compact_rows()) == COMPACT_PAGE - 1
    assert built.compact_more.text() == "Show 151 more rows"
    built.compact_more.click()
    assert len(built.compact_rows()) == 2 * COMPACT_PAGE - 1
    assert built.compact_more.text() == "Show 51 more rows"
    built.compact_more.click()
    assert len(built.compact_rows()) == 250
    assert built.compact_more is None
    assert all(row.detail is None for row in built.compact_rows())
    built.close()
    built.deleteLater()
    qgis_app.processEvents()
