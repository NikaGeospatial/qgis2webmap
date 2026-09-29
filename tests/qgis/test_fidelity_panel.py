"""The Fidelity tab, the strip, and the warning before an export or a publish.

Driven through the real dialog and the real widgets: the wording is tested
without Qt in `tests/unit/test_fidelity_summary.py`, and what is left to prove
here is that the dialog says it at the right moment - that a report goes out of
date when the settings under it change, that a failed check is never shown as a
verdict, and that Blocked asks rather than refuses.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses

import pytest

from nika_onlymap_exporter.core.export_ir import FidelityItem, FidelityStatus
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.fidelity_summary import ReportState, summarise

qgis_core = pytest.importorskip("qgis.core")


class FakeIface:
    def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
        return None


@pytest.fixture
def dialog(qgis_app, project, make_memory_layer):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    for name in ("roads", "parks"):
        project.addMapLayer(make_memory_layer(name, features=[("a", [1.0, 2.0])]))
    built = MainDialog(FakeIface(), None)
    yield built
    built.close()


def layer_ids(project) -> dict[str, str]:
    return {layer.name(): layer.id() for layer in project.mapLayers().values()}


def mixed_report(project) -> FidelityReportBuilder:
    ids = layer_ids(project)
    report = FidelityReportBuilder()
    report.preserved("Symbology of 'roads'", "Single symbol translated.", ids["roads"])
    report.unsupported("Popup fields of 'roads'", "A field is hidden.", ids["roads"])
    for n in (1, 2, 3):
        report.approximated(
            f"Symbology of 'roads', range {n}",
            "Only the top symbol layer.",
            ids["roads"],
        )
    report.preserved("Symbology of 'parks'", "Single symbol translated.", ids["parks"])
    report.unsupported("Basemap", "The export has no basemap.")
    return report


def top_level_titles(panel) -> list[str]:
    return [
        panel.tree.topLevelItem(index).text(0)
        for index in range(panel.tree.topLevelItemCount())
    ]


class TestTheTab:
    def test_it_is_grouped_whole_map_first_then_problem_layers(
        self, dialog, project
    ) -> None:
        dialog._show_fidelity(mixed_report(project))
        assert top_level_titles(dialog.fidelity_panel) == [
            "Whole map",
            "roads",
            "parks",
        ]

    def test_an_exact_layer_is_one_line(self, dialog, project) -> None:
        dialog._show_fidelity(mixed_report(project))
        parks = dialog.fidelity_panel.tree.topLevelItem(2)
        assert parks.text(1) == "✓ exact"
        assert parks.childCount() == 0

    def test_kept_rows_are_hidden_but_one_toggle_away(self, dialog, project) -> None:
        """Nothing that happened is deleted from view, only folded."""
        panel = dialog.fidelity_panel
        dialog._show_fidelity(mixed_report(project))
        roads = panel.tree.topLevelItem(1)
        assert [roads.child(i).text(1) for i in range(roads.childCount())] == [
            "Not exported",
            "Changed",
        ]
        assert panel.kept_toggle.text() == "Show what is kept (2)"

        panel.kept_toggle.setChecked(True)
        roads = panel.tree.topLevelItem(1)
        assert "Kept" in [roads.child(i).text(1) for i in range(roads.childCount())]

    def test_a_whole_map_with_nothing_to_say_is_folded_too(
        self, dialog, project
    ) -> None:
        report = FidelityReportBuilder()
        report.preserved("Map title", "The map is titled 'x'.")
        report.unsupported("Labels on 'roads'", "x", layer_ids(project)["roads"])
        dialog._show_fidelity(report)
        assert "Whole map" not in top_level_titles(dialog.fidelity_panel)

        dialog.fidelity_panel.kept_toggle.setChecked(True)
        assert top_level_titles(dialog.fidelity_panel)[0] == "Whole map"

    def test_per_class_repeats_are_one_row(self, dialog, project) -> None:
        dialog._show_fidelity(mixed_report(project))
        roads = dialog.fidelity_panel.tree.topLevelItem(1)
        labels = [roads.child(i).text(0) for i in range(roads.childCount())]
        assert "Symbology (3 classes)" in labels

    def test_the_detail_pane_shows_the_whole_sentence(self, dialog, project) -> None:
        """The old table cut details at 110 characters."""
        long_detail = "A sentence that goes on. " * 20
        report = FidelityReportBuilder()
        report.unsupported(
            "Labels on 'roads'", long_detail, layer_ids(project)["roads"]
        )
        dialog._show_fidelity(report)

        assert dialog.fidelity_panel.detail_text.text() == long_detail
        assert dialog.fidelity_panel.detail_text.wordWrap()

    def test_show_in_layers_selects_that_layer(self, dialog, project) -> None:
        report = FidelityReportBuilder()
        report.unsupported("Labels on 'parks'", "x", layer_ids(project)["parks"])
        dialog._show_fidelity(report)
        panel = dialog.fidelity_panel

        assert panel.jump_button.text() == "Show in Layers"
        panel.jump_button.click()

        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Layers"
        assert dialog.layer_tree.currentItem().text(0) == "parks"

    def test_a_whole_map_setting_jumps_to_the_map_tab(self, dialog, project) -> None:
        report = FidelityReportBuilder()
        report.unsupported("Basemap", "The export has no basemap.")
        dialog._show_fidelity(report)
        panel = dialog.fidelity_panel

        assert panel.jump_button.text() == "Go to Map tab"
        panel.jump_button.click()
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Map"

    def test_a_failed_check_is_not_shown_as_a_verdict(self, dialog) -> None:
        """It used to appear as a "Blocked" row, which is a claim about the map
        that a crash in the report cannot make."""
        dialog._fidelity_state = ReportState.CHECKING
        dialog._on_job_failed("boom", "traceback")  # also shows a modal; see below
        panel = dialog.fidelity_panel
        verdicts = [
            panel.tree.topLevelItem(i).text(1)
            for i in range(panel.tree.topLevelItemCount())
        ]
        assert "Blocked" not in verdicts
        assert panel.headline.text() == "The check could not finish."
        assert "boom" in panel.detail_text.text()
        assert dialog.fidelity_summary.text() == "The check could not finish: boom"


@pytest.fixture(autouse=True)
def no_modals(monkeypatch):
    """`show_failure` and friends would block a headless run forever."""
    from qgis.PyQt.QtWidgets import QMessageBox

    import nika_onlymap_exporter.ui.main_dialog as main_dialog

    monkeypatch.setattr(main_dialog, "show_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)


class TestFreshness:
    def test_before_any_check_the_strip_says_so(self, dialog) -> None:
        """Silence before a check read as "nothing changes"."""
        assert dialog.fidelity_summary.text() == "Not checked yet."
        assert dialog.fidelity_link.text() == "Check now"
        assert not dialog.fidelity_link.isHidden()

    def test_unticking_include_makes_the_report_out_of_date(
        self, dialog, project
    ) -> None:
        """Include, Popup and Label ticks never marked the report stale."""
        from qgis.PyQt.QtCore import Qt

        dialog._show_fidelity(mixed_report(project))
        assert not dialog._fidelity_is_stale

        dialog.layer_tree.topLevelItem(0).setCheckState(1, Qt.CheckState.Unchecked)
        dialog._refresh_fidelity_freshness()  # what the poll does

        assert dialog._fidelity_is_stale
        assert dialog.fidelity_summary.text().startswith("Out of date")
        assert dialog.fidelity_link.text() == "Check again"
        assert dialog.fidelity_panel.stale_banner.isVisibleTo(dialog.fidelity_panel)

    def test_renaming_the_map_makes_the_report_out_of_date(
        self, dialog, project
    ) -> None:
        dialog._show_fidelity(mixed_report(project))
        dialog.name_edit.setText("A different title")
        dialog._refresh_fidelity_freshness()
        assert dialog._fidelity_is_stale

    def test_changing_it_back_makes_it_current_again(self, dialog, project) -> None:
        dialog._show_fidelity(mixed_report(project))
        original = dialog.name_edit.text()
        dialog.name_edit.setText("A different title")
        dialog.name_edit.setText(original)
        dialog._refresh_fidelity_freshness()
        assert not dialog._fidelity_is_stale
        assert not dialog.fidelity_summary.text().startswith("Out of date")

    def test_a_layer_added_in_qgis_makes_it_out_of_date(
        self, dialog, project, make_memory_layer
    ) -> None:
        dialog._show_fidelity(mixed_report(project))
        project.addMapLayer(make_memory_layer("new", features=[("a", [0.0, 0.0])]))
        dialog.refresh_layers()  # what the layer watcher does
        assert dialog.fidelity_summary.text().startswith("Out of date")

    def test_restyling_a_layer_in_qgis_makes_it_out_of_date(
        self, dialog, project
    ) -> None:
        """Restyling was the one change the report could not see, and the
        cached read went on being handed to Export and Host."""
        from qgis.PyQt.QtCore import QCoreApplication

        dialog._show_fidelity(mixed_report(project))
        dialog._cached_export = object()
        dialog._cached_signature = dialog.state.data_snapshot()
        assert not dialog._fidelity_is_stale

        roads = next(
            layer for layer in project.mapLayers().values() if layer.name() == "roads"
        )
        roads.setRenderer(
            qgis_core.QgsSingleSymbolRenderer(
                qgis_core.QgsMarkerSymbol.createSimple({"color": "red"})
            )
        )
        roads.emitStyleChanged()
        QCoreApplication.processEvents()

        assert dialog._fidelity_is_stale
        assert dialog.fidelity_summary.text().startswith("Out of date")
        assert dialog._cached_export is None, "a restyle left the old read in the cache"

    def test_closing_the_dialog_disconnects_from_every_layer(
        self, dialog, project
    ) -> None:
        layers = list(project.mapLayers().values())
        open_counts = [layer.receivers(layer.styleChanged) for layer in layers]
        dialog.close()
        closed_counts = [layer.receivers(layer.styleChanged) for layer in layers]
        assert closed_counts == [count - 1 for count in open_counts]

    def test_the_strip_uses_full_strength_text_for_problems(
        self, dialog, project
    ) -> None:
        """It printed "cannot be exported" in the dimmest colour available."""
        from qgis.PyQt.QtGui import QPalette

        dialog._show_fidelity(mixed_report(project))
        assert dialog.fidelity_summary.foregroundRole() == QPalette.ColorRole.WindowText
        assert dialog.fidelity_link.text() == "See what changes"

    def test_the_strip_counts_what_it_says(self, dialog, project) -> None:
        dialog._show_fidelity(mixed_report(project))
        # Roads' popups, the merged range rows, and the basemap: three rows.
        assert dialog.fidelity_summary.text() == (
            "1 layer and the whole map need attention · 3 things change on export."
        )

    def test_the_poll_stops_with_the_dialog(self, dialog) -> None:
        dialog.close()
        assert not dialog._fidelity_timer.isActive()


def blocked_export(project, make_memory_layer):
    """A real read with a Blocked item added - a layer QGIS could not load."""
    from nika_onlymap_exporter.core.project_reader import read_project

    export = read_project(project, FidelityReportBuilder())
    broken = FidelityItem(
        subject="Layer 'lost'",
        status=FidelityStatus.BLOCKED,
        detail="QGIS could not load this layer.",
        layer_id="lost",
    )
    return dataclasses.replace(export, fidelity=(*export.fidelity, broken))


class TestBlockedWarnsInsteadOfRefusing:
    """Blocked used to disable Export; now it names what breaks and asks."""

    def spy(self, monkeypatch, answer):
        import nika_onlymap_exporter.ui.main_dialog as main_dialog

        calls: list[tuple[list[str], str]] = []

        def fake(_parent, items, action):
            calls.append(([item.subject for item in items], action))
            return answer

        monkeypatch.setattr(main_dialog, "ask_before", fake)
        return calls

    def test_cancel_stops_before_anything_is_written(
        self, dialog, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed

        calls = self.spy(monkeypatch, Proceed.CANCEL)
        reached: list[bool] = []
        monkeypatch.setattr(dialog, "_runtime_ready", lambda: reached.append(True))

        dialog._export_with(blocked_export(project, make_memory_layer), None)

        assert calls == [(["Layer 'lost'"], "Export")]
        assert reached == []

    def test_continue_goes_on_with_the_export(
        self, dialog, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed

        self.spy(monkeypatch, Proceed.CONTINUE)
        reached: list[bool] = []

        def runtime_ready() -> bool:
            reached.append(True)
            return False  # stop here: the rest is the ordinary export

        monkeypatch.setattr(dialog, "_runtime_ready", runtime_ready)
        dialog._export_with(blocked_export(project, make_memory_layer), None)
        assert reached == [True]

    def test_review_opens_the_fidelity_tab(
        self, dialog, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed

        self.spy(monkeypatch, Proceed.REVIEW)
        dialog._export_with(blocked_export(project, make_memory_layer), None)
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Fidelity"

    def test_publishing_asks_the_same_question(
        self, dialog, project, make_memory_layer, monkeypatch
    ) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed

        calls = self.spy(monkeypatch, Proceed.CANCEL)
        dialog._host_with(blocked_export(project, make_memory_layer), None)
        assert calls == [(["Layer 'lost'"], "Publish")]

    def test_a_clean_export_is_not_asked_about(
        self, dialog, project, monkeypatch
    ) -> None:
        """No Blocked item, no question: the real `ask_before` goes straight on."""
        from nika_onlymap_exporter.core.project_reader import read_project

        reached: list[bool] = []

        def runtime_ready() -> bool:
            reached.append(True)
            return False

        monkeypatch.setattr(dialog, "_runtime_ready", runtime_ready)
        dialog._export_with(read_project(project, FidelityReportBuilder()), None)
        assert reached == [True]

    def test_an_empty_map_is_still_refused(self, dialog, project, monkeypatch) -> None:
        """No layer to write is a broken artifact, not a judgement call."""
        from nika_onlymap_exporter.core.project_reader import read_project
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed

        project.removeAllMapLayers()
        calls = self.spy(monkeypatch, Proceed.CONTINUE)
        refused: list[bool] = []
        monkeypatch.setattr(
            dialog, "_warn_not_exportable", lambda _export: refused.append(True)
        )
        dialog._export_with(read_project(project, FidelityReportBuilder()), None)
        assert refused == [True]
        assert calls == []


class TestTheWarning:
    def test_it_names_each_item_and_offers_three_ways_out(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed, problems_box

        items = [
            FidelityItem(
                "Layer 'lost'", FidelityStatus.BLOCKED, "QGIS could not load it.", "x"
            )
        ]
        box, choices = problems_box(None, items, "Export")
        assert "Layer 'lost': QGIS could not load it." in box.text()
        assert sorted(choice.value for choice in choices.values()) == sorted(
            choice.value for choice in Proceed
        )
        labels = {button.text() for button in choices}
        assert "Export anyway" in labels
        assert "Review on the Fidelity tab" in labels
        assert box.defaultButton().text() == "Review on the Fidelity tab"

    def test_no_items_means_no_question(self, qgis_app) -> None:
        from nika_onlymap_exporter.ui.fidelity_panel import Proceed, ask_before

        assert ask_before(None, [], "Export") is Proceed.CONTINUE


def test_summary_and_tab_agree(dialog, project) -> None:
    """The panel shows what the pure summary says, not a second opinion."""
    report = mixed_report(project)
    dialog._show_fidelity(report)
    names = {layer_id: name for name, layer_id in layer_ids(project).items()}
    assert (
        dialog.fidelity_panel.headline.text()
        == summarise(report.items, names).headline()
    )
