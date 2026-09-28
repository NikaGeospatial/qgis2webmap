"""Grouping and counting the fidelity report for the Fidelity tab and strip.

The tab is only as honest as these numbers: a count that does not count what it
says, or a row that disappears in the merge, is the silent loss the report
exists to prevent. So every rule here has a test, and the conservation of rows
has its own.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import FidelityItem, FidelityStatus
from nika_onlymap_exporter.core.fidelity_summary import (
    WHOLE_MAP,
    ReportState,
    Tone,
    needs_attention,
    strip_message,
    summarise,
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
