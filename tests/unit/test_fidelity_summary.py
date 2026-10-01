"""Grouping and counting the fidelity report for the Fidelity tab and strip.

The tab is only as honest as these numbers: a count that does not count what it
says, or a row that disappears in the merge, is the silent loss the report
exists to prevent. So every rule here has a test, and the conservation of rows
has its own.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from pathlib import Path

from nika_onlymap_exporter.core.export_ir import FidelityItem, FidelityStatus
from nika_onlymap_exporter.core.fidelity_summary import (
    TOPIC_BY_HEAD,
    WHOLE_MAP,
    ReportFilter,
    ReportState,
    Tone,
    Topic,
    needs_attention,
    strip_message,
    subject_head,
    summarise,
    topic_of,
)

KEPT = FidelityStatus.PRESERVED
CHANGED = FidelityStatus.APPROXIMATED
IMAGE = FidelityStatus.RASTER_FALLBACK
LOST = FidelityStatus.UNSUPPORTED
BLOCKED = FidelityStatus.BLOCKED


def item(
    subject: str, status: FidelityStatus, detail: str = "d", layer_id: str | None = None
) -> FidelityItem:
    return FidelityItem(
        subject=subject, status=status, detail=detail, layer_id=layer_id
    )


NAMES = {"r": "Roads", "v": "Volcanoes", "p": "Parks"}
ORDER = ["v", "r", "p"]


def volcanoes_like() -> list[FidelityItem]:
    """The shape of the real volcanoes demo: per-class repeats, a lost
    basemap, labels changed, and a layer that came through exactly."""
    stacked = "The symbol stacks 3 symbol layers; only the top one is translated."
    return [
        item("Symbology of 'Parks'", KEPT, "Single symbol translated.", "p"),
        *[
            item(f"Symbology of 'Volcanoes', range {n}", CHANGED, stacked, "v")
            for n in (1, 2, 3, 4)
        ],
        item("Symbology of 'Volcanoes'", KEPT, "Graduated with 4 classes.", "v"),
        item("Labels on 'Volcanoes'", CHANGED, "Collision handling differs.", "v"),
        item("Symbology of 'Roads'", KEPT, "Single symbol translated.", "r"),
        item("Popup fields of 'Roads'", LOST, "A field is hidden.", "r"),
        item("Basemap", LOST, "The export has no basemap."),
        item("Map title", KEPT, "The map is titled 'X'."),
    ]


class TestGrouping:
    def test_whole_map_comes_first_then_layers(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        assert [group.title for group in summary.groups] == [
            WHOLE_MAP,
            "Roads",
            "Volcanoes",
            "Parks",
        ]

    def test_problems_first_then_qgis_order_then_exact_layers_last(self) -> None:
        """Roads (Not exported) outranks Volcanoes (Changed) although QGIS lists
        Volcanoes first; Parks has nothing to say and goes last."""
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        titles = [group.title for group in summary.layer_groups]
        assert titles == ["Roads", "Volcanoes", "Parks"]

    def test_equal_severity_follows_the_qgis_layer_order(self) -> None:
        items = [
            item("Labels on 'Roads'", CHANGED, "x", "r"),
            item("Labels on 'Volcanoes'", CHANGED, "x", "v"),
        ]
        summary = summarise(items, NAMES, ORDER)
        assert [g.title for g in summary.layer_groups] == ["Volcanoes", "Roads"]

    def test_row_labels_drop_the_layer_name_inside_its_group(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        roads = summary.layer_groups[0]
        assert [row.label for row in roads.rows] == ["Popup fields", "Symbology"]

    def test_a_layer_subject_is_labelled_layer(self) -> None:
        summary = summarise([item("Layer 'Roads'", BLOCKED, "gone", "r")], NAMES)
        assert summary.layer_groups[0].rows[0].label == "Layer"

    def test_an_unnamed_layer_falls_back_to_its_quoted_subject(self) -> None:
        summary = summarise([item("Labels on 'Gone'", CHANGED, "x", "zz")])
        assert summary.layer_groups[0].title == "Gone"

    def test_problems_sort_above_kept_within_a_group(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        volcanoes = summary.layer_groups[1]
        assert [row.status for row in volcanoes.rows] == [CHANGED, CHANGED, KEPT]

    def test_an_exact_layer_is_marked_exact(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        parks = summary.layer_groups[-1]
        assert parks.is_exact
        assert parks.tally() == "exact"


class TestMerging:
    def test_identical_per_class_rows_become_one(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        volcanoes = summary.layer_groups[1]
        merged = volcanoes.rows[0]
        assert merged.label == "Symbology (4 classes)"
        assert len(merged.subjects) == 4

    def test_different_details_are_never_merged(self) -> None:
        items = [
            item("Symbology of 'Roads', class 1", CHANGED, "one thing", "r"),
            item("Symbology of 'Roads', class 2", CHANGED, "another", "r"),
        ]
        rows = summarise(items, NAMES).layer_groups[0].rows
        assert [row.label for row in rows] == [
            "Symbology, class 1",
            "Symbology, class 2",
        ]

    def test_different_verdicts_are_never_merged(self) -> None:
        items = [
            item("Symbology of 'Roads', class 1", CHANGED, "same", "r"),
            item("Symbology of 'Roads', class 2", LOST, "same", "r"),
        ]
        assert len(summarise(items, NAMES).layer_groups[0].rows) == 2

    def test_rows_about_different_layers_are_never_merged(self) -> None:
        items = [
            item("Labels on 'Roads'", CHANGED, "same", "r"),
            item("Labels on 'Parks'", CHANGED, "same", "p"),
        ]
        summary = summarise(items, NAMES)
        assert len(summary.layer_groups) == 2

    def test_no_item_is_lost_in_the_merge(self) -> None:
        """Every report item lands in exactly one row."""
        items = volcanoes_like()
        summary = summarise(items, NAMES, ORDER)
        subjects = [
            s for group in summary.groups for row in group.rows for s in row.subjects
        ]
        assert sorted(subjects) == sorted(i.subject for i in items)


class TestCounts:
    def test_the_headline_counts_rows_listed_not_entries_behind_them(self) -> None:
        """Four merged class rows are one thing a reader finds."""
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        # Basemap, Roads popups, Volcanoes symbology (merged), Volcanoes labels.
        assert summary.change_count == 4
        assert summary.headline() == (
            "3 layers · 1 layer and the whole map need attention · 4 things change"
        )

    def test_the_headline_for_the_spec_example(self) -> None:
        items = [item("Popup fields of 'Roads'", LOST, "x", "r")] + [
            item(f"Labels on '{name}'", CHANGED, "x", lid)
            for lid, name in (("v", "Volcanoes"), ("p", "Parks"))
        ]
        assert summarise(items, NAMES).headline() == (
            "3 layers · 1 needs attention · 3 things change"
        )

    def test_a_clean_report_says_so(self) -> None:
        summary = summarise([item("Symbology of 'Roads'", KEPT, "x", "r")], NAMES)
        assert summary.headline() == "1 layer · everything is kept exactly"
        assert summary.strip_text() == ""

    def test_rasterised_is_a_change_but_not_a_loss(self) -> None:
        summary = summarise([item("Markers of 'Roads'", IMAGE, "x", "r")], NAMES)
        assert summary.change_count == 1
        assert "attention" not in summary.headline()

    def test_group_tally_is_worst_first(self) -> None:
        items = [
            item("Labels on 'Roads'", CHANGED, "a", "r"),
            item("Popup fields of 'Roads'", LOST, "b", "r"),
            item("Symbology of 'Roads'", CHANGED, "c", "r"),
        ]
        assert summarise(items, NAMES).layer_groups[0].tally() == (
            "1 not exported · 2 changed"
        )

    def test_kept_count_is_available_for_the_toggle(self) -> None:
        assert summarise(volcanoes_like(), NAMES, ORDER).kept_count == 4

    def test_empty_report(self) -> None:
        summary = summarise([])
        assert summary.is_empty
        assert summary.strip_text() == ""


class TestStripText:
    def test_changes_only(self) -> None:
        items = [item("Labels on 'Roads'", CHANGED, "x", "r")]
        assert summarise(items, NAMES).strip_text() == "1 thing changes on export."

    def test_attention_names_its_noun_without_the_headline(self) -> None:
        items = [
            item("Popup fields of 'Roads'", LOST, "x", "r"),
            item("Labels on 'Roads'", CHANGED, "y", "r"),
        ]
        assert summarise(items, NAMES).strip_text() == (
            "1 layer needs attention · 2 things change on export."
        )

    def test_blocked_layers_are_counted_as_layers(self) -> None:
        """The old strip said "N layers cannot be exported" and counted report
        entries, so two blocked rows about one layer read as two layers."""
        items = [
            item("Layer 'Roads'", BLOCKED, "gone", "r"),
            item("Data source of 'Roads'", BLOCKED, "missing", "r"),
        ]
        assert summarise(items, NAMES).strip_text() == (
            "1 layer cannot be exported · 2 things change on export."
        )

    def test_a_whole_map_blocker_is_counted_as_a_thing(self) -> None:
        items = [
            item("Project layers", BLOCKED, "There is nothing to export."),
            item("Layer 'Roads'", BLOCKED, "gone", "r"),
        ]
        assert (
            summarise(items, NAMES)
            .strip_text()
            .startswith("2 things cannot be exported")
        )


def test_only_losses_need_attention() -> None:
    assert needs_attention(BLOCKED)
    assert needs_attention(LOST)
    assert not needs_attention(CHANGED)
    assert not needs_attention(IMAGE)
    assert not needs_attention(KEPT)


class TestStripMessage:
    """Every state the strip can be in has words, a tone and a fitting button."""

    def test_before_any_check_it_says_so_rather_than_nothing(self) -> None:
        """Silence before a check read as "nothing changes"."""
        message = strip_message(ReportState.NOT_CHECKED)
        assert message.text == "Not checked yet."
        assert message.button == "Check now"
        assert message.tone is Tone.QUIET

    def test_while_checking_there_is_nothing_to_press(self) -> None:
        assert strip_message(ReportState.CHECKING).button is None

    def test_an_out_of_date_report_is_never_shown_as_current(self) -> None:
        summary = summarise([item("Labels on 'Roads'", CHANGED, "x", "r")], NAMES)
        message = strip_message(ReportState.OUT_OF_DATE, summary)
        assert "Out of date" in message.text
        assert "1 thing" not in message.text
        assert message.button == "Check again"
        assert message.tone is Tone.WARNING

    def test_a_failed_check_is_an_error_not_a_verdict(self) -> None:
        message = strip_message(ReportState.FAILED, error="disk on fire")
        assert message.text == "The check could not finish: disk on fire"
        assert message.tone is Tone.ERROR

    def test_a_clean_current_report_says_nothing(self) -> None:
        summary = summarise([item("Symbology of 'Roads'", KEPT, "x", "r")], NAMES)
        message = strip_message(ReportState.CURRENT, summary)
        assert message.text == ""
        assert message.button is None

    def test_tone_and_button_follow_the_worst_verdict(self) -> None:
        cases = [
            (CHANGED, Tone.INFO, "See what changes"),
            (IMAGE, Tone.INFO, "See what changes"),
            (LOST, Tone.WARNING, "See what changes"),
            (BLOCKED, Tone.ERROR, "Review problems"),
        ]
        for status, tone, button in cases:
            summary = summarise([item("Layer 'Roads'", status, "x", "r")], NAMES)
            message = strip_message(ReportState.CURRENT, summary)
            assert (message.tone, message.button) == (tone, button), status


class TestNothingIsLost:
    def test_a_merged_row_keeps_every_item_label(self) -> None:
        """ "(4 classes)" has to open into the four classes it stands for."""
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        volcanoes = next(g for g in summary.groups if g.title == "Volcanoes")
        merged = next(row for row in volcanoes.rows if row.is_merged)
        assert merged.item_labels == tuple(
            f"Symbology, range {n}" for n in (1, 2, 3, 4)
        )
        assert len(merged.item_labels) == len(merged.subjects)

    def test_an_item_recorded_twice_is_listed_twice(self) -> None:
        twice = [item("Symbology of 'Roads'", CHANGED, "Dashes.", "r")] * 2
        (row,) = summarise(twice, NAMES, ORDER).groups[0].rows
        assert row.item_labels == ("Symbology", "Symbology")

    def test_every_item_is_reachable_through_its_row(self) -> None:
        items = volcanoes_like()
        summary = summarise(items, NAMES, ORDER)
        reachable = [
            subject
            for group in summary.groups
            for row in group.rows
            for subject in row.subjects
        ]
        assert sorted(reachable) == sorted(i.subject for i in items)


# Every subject the package records, as written in the source: the literal
# start of a report call's first argument, of a `subject = ...` assignment, or
# of a licence violation's `subject=`.
_SUBJECT_IN_SOURCE = re.compile(
    r"""(?:\.(?:preserved|approximated|unsupported|blocked|raster_fallback|"""
    r"""record|suppressed_setting)\(\s*|subject\s*=\s*)f?"([^"{]+)""",
)
PACKAGE = Path(__file__).resolve().parents[2] / "nika_onlymap_exporter"


def recorded_subject_heads() -> set[str]:
    heads: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        for match in _SUBJECT_IN_SOURCE.finditer(path.read_text(encoding="utf-8")):
            heads.add(subject_head(match.group(1)))
    return heads


class TestTopics:
    def test_the_subjects_translators_record_are_sorted_by_topic(self) -> None:
        assert topic_of("Symbology of 'Roads', class 3") is Topic.SYMBOLOGY
        assert topic_of("Markers of 'Roads'") is Topic.SYMBOLOGY
        assert topic_of("Scale visibility of 'Roads'") is Topic.SYMBOLOGY
        assert topic_of("Labels on 'Roads'") is Topic.LABELS
        assert topic_of("Popup fields of 'Roads'") is Topic.POPUPS
        assert topic_of("Layer 'Roads'") is Topic.DATA
        assert topic_of("Feature count in 'Roads'") is Topic.DATA
        assert topic_of("Layer count") is Topic.MAP
        assert topic_of("Basemap") is Topic.MAP

    def test_a_subject_nobody_listed_is_other(self) -> None:
        assert topic_of("Something new about 'Roads'") is Topic.OTHER

    def test_a_layer_named_like_a_topic_does_not_confuse_it(self) -> None:
        assert topic_of("Labels on 'Basemap'") is Topic.LABELS

    def test_every_subject_in_the_source_has_a_topic(self) -> None:
        """A new kind of report row must be given a topic, or it hides in Other."""
        heads = recorded_subject_heads()
        # The scan must find the subjects or it proves nothing.
        assert {"Symbology", "Labels", "Basemap", "Artifact size"} <= heads
        assert sorted(heads - set(TOPIC_BY_HEAD)) == []


class TestFilter:
    def summary(self):
        return summarise(volcanoes_like(), NAMES, ORDER)

    def visible(self, report_filter: ReportFilter) -> list[str]:
        return [
            f"{group.title}/{row.label}"
            for group in self.summary().groups
            for row in report_filter.rows(group)
        ]

    def test_by_default_kept_is_hidden_and_everything_else_shown(self) -> None:
        shown = self.visible(ReportFilter())
        assert "Whole map/Basemap" in shown
        assert "Whole map/Map title" not in shown
        assert "Roads/Popup fields" in shown

    def test_a_verdict_can_be_hidden(self) -> None:
        only_lost = ReportFilter(verdicts=frozenset({LOST}))
        assert self.visible(only_lost) == ["Whole map/Basemap", "Roads/Popup fields"]

    def test_a_topic_can_be_hidden(self) -> None:
        no_map = ReportFilter(topics=frozenset(Topic) - {Topic.MAP})
        assert "Whole map/Basemap" not in self.visible(no_map)
        assert "Roads/Popup fields" in self.visible(no_map)

    def test_search_reads_label_detail_and_subject(self) -> None:
        assert self.visible(ReportFilter(text="COLLISION")) == ["Volcanoes/Labels"]
        assert self.visible(ReportFilter(text="range 3")) == [
            "Volcanoes/Symbology (4 classes)"
        ]

    def test_searching_a_layer_name_shows_its_rows(self) -> None:
        summary = self.summary()
        roads = next(g for g in summary.groups if g.title == "Roads")
        assert ReportFilter(text="roads").rows(roads, roads.title)

    def test_an_exact_layer_stays_listed_unless_searched_away(self) -> None:
        parks = next(g for g in self.summary().groups if g.title == "Parks")
        assert ReportFilter().shows_group(parks, "Polygons")
        assert ReportFilter(text="polygon").shows_group(parks, "Polygons")
        assert not ReportFilter(text="roads").shows_group(parks, "Polygons")

    def test_a_group_with_nothing_left_is_not_listed(self) -> None:
        roads = next(g for g in self.summary().groups if g.title == "Roads")
        only_changed = ReportFilter(verdicts=frozenset({CHANGED}))
        assert not only_changed.shows_group(roads)


class TestFilterCounts:
    def test_verdict_counts_count_rows_like_the_headline(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        counts = summary.verdict_counts()
        assert counts[CHANGED] == 2  # four ranges merged into one, and labels
        assert counts[LOST] == 2
        assert counts[KEPT] == 4
        assert counts[BLOCKED] == 0
        changes = sum(n for status, n in counts.items() if status is not KEPT)
        assert changes == summary.change_count

    def test_topic_counts_follow_the_verdicts_ticked(self) -> None:
        summary = summarise(volcanoes_like(), NAMES, ORDER)
        assert summary.topic_counts()[Topic.MAP] == 2
        problems = frozenset({LOST, CHANGED})
        assert summary.topic_counts(problems)[Topic.MAP] == 1
        assert summary.topic_counts(problems)[Topic.SYMBOLOGY] == 1
