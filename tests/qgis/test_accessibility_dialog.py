"""The Colour vision dialog against a real QGIS: canvas preview, browser
callback, the legend table, the mode signal and the NIKA hand-off.

The canvas half is tested on a real `QgsMapCanvas`, not a double: the preview
enum names changed shape between PyQt5 and PyQt6, and a double would only
prove what this file believes about them. Legend extraction is tested through
the real `read_project`, so a renderer the reader translates differently from
what `core.cvd.legend_entries` expects shows up here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

qgis_core = pytest.importorskip("qgis.core")

from nika_onlymap_exporter.core.cvd import (  # noqa: E402 - after the importorskip
    LegendEntry,
    legend_entries,
)

PERMITS = (
    LegendEntry("Approved", "#2ca02c", "permits", "Permits"),
    LegendEntry("Rejected", "#d62728", "permits", "Permits"),
    LegendEntry("Pending", "#1b2d0a", "permits", "Permits"),
    LegendEntry("Primary", "#d62728", "roads", "Roads"),
)


class Recorder:
    """Collects the calls a callback receives."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, value: object) -> None:
        self.calls.append(value)


@pytest.fixture
def canvas(qgis_app):
    from qgis.gui import QgsMapCanvas

    widget = QgsMapCanvas()
    yield widget
    widget.deleteLater()


@pytest.fixture
def make_dialog(qgis_app, canvas):
    from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
        AccessibilityDialog,
    )

    made = []

    def make(entries=PERMITS, **kwargs):
        kwargs.setdefault("canvas", canvas)
        kwargs.setdefault("open_preview", Recorder())
        kwargs.setdefault("ask_nika", Recorder())
        dialog = AccessibilityDialog(lambda: entries, **kwargs)
        made.append(dialog)
        return dialog

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()


def table_rows(dialog):
    """Each row's cells as text. The swatch columns show only a painted icon,
    so their hex is read from the tooltip, which is also their accessible
    text."""
    return [
        tuple(
            dialog.table.item(row, column).toolTip()
            for column in range(dialog.table.columnCount())
        )
        for row in range(dialog.table.rowCount())
    ]


class TestCanvas:
    def test_mode_round_trip_on_a_real_canvas(self, make_dialog, canvas):
        from qgis.gui import QgsPreviewEffect

        dialog = make_dialog()
        assert not canvas.previewModeEnabled()

        for condition, mode in (
            ("protanopia", QgsPreviewEffect.PreviewProtanope),
            ("deuteranopia", QgsPreviewEffect.PreviewDeuteranope),
            ("tritanopia", QgsPreviewEffect.PreviewTritanope),
            ("achromatopsia", QgsPreviewEffect.PreviewGrayscale),
        ):
            dialog.set_condition(condition)
            assert canvas.previewModeEnabled()
            assert canvas.previewMode() == mode

        dialog.set_condition(None)
        assert not canvas.previewModeEnabled()

    def test_closing_keeps_the_canvas_and_reset_restores_it(self, make_dialog, canvas):
        dialog = make_dialog()
        dialog.show()
        dialog.set_condition("deuteranopia")
        dialog.close_button.click()
        assert not dialog.isVisible()
        assert canvas.previewModeEnabled()
        dialog.reset_canvas()
        assert not canvas.previewModeEnabled()

    def test_reset_leaves_a_preview_the_user_chose_alone(self, make_dialog, canvas):
        from qgis.gui import QgsPreviewEffect

        canvas.setPreviewModeEnabled(True)
        canvas.setPreviewMode(QgsPreviewEffect.PreviewMono)
        dialog = make_dialog()
        dialog.reset_canvas()
        assert canvas.previewModeEnabled()
        assert canvas.previewMode() == QgsPreviewEffect.PreviewMono

    def test_unticking_the_canvas_stops_filtering_it(self, make_dialog, canvas):
        dialog = make_dialog()
        dialog.set_condition("tritanopia")
        dialog.canvas_check.setChecked(False)
        assert not canvas.previewModeEnabled()
        dialog.set_condition("protanopia")
        assert not canvas.previewModeEnabled()
        dialog.canvas_check.setChecked(True)
        assert canvas.previewModeEnabled()

    def test_without_a_canvas_the_target_is_disabled_and_says_why(self, make_dialog):
        dialog = make_dialog(canvas=None)
        assert not dialog.canvas_check.isEnabled()
        assert not dialog.canvas_check.isChecked()
        assert dialog.canvas_check.toolTip()
        dialog.set_condition("deuteranopia")  # must not raise


class TestBrowser:
    def test_receives_the_runtime_cvd_value(self, make_dialog):
        preview = Recorder()
        dialog = make_dialog(open_preview=preview)
        assert preview.calls == []  # opening the dialog opens no tab
        dialog.set_condition("deuteranopia")
        dialog.set_condition("achromatopsia")
        dialog.set_condition(None)
        # The runtime's own `cvd=` vocabulary, and None to reopen it plain.
        assert preview.calls == ["deuteranopia", "achromatopsia", None]

    def test_unticked_browser_is_left_alone(self, make_dialog):
        preview = Recorder()
        dialog = make_dialog(open_preview=preview)
        dialog.browser_check.setChecked(False)
        dialog.set_condition("protanopia")
        assert preview.calls == []
        dialog.browser_check.setChecked(True)
        assert preview.calls == ["protanopia"]

    def test_without_a_preview_the_target_is_disabled(self, make_dialog):
        dialog = make_dialog(open_preview=None)
        assert not dialog.browser_check.isEnabled()
        assert dialog.browser_check.toolTip()


class TestTable:
    def test_rows_match_the_entries_with_notes(self, make_dialog):
        dialog = make_dialog()
        dialog.set_condition("deuteranopia")
        rows = table_rows(dialog)
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("Permits", "Approved", "#2ca02c"),
            ("Permits", "Rejected", "#d62728"),
            ("Permits", "Pending", "#1b2d0a"),
            ("Roads", "Primary", "#d62728"),
        ]
        notes = [r[4] for r in rows]
        assert notes == ["like Rejected", "like Approved", "ok", "ok"]
        # Simulated colours: the runtime's, rounded for display.
        assert rows[1][3] == "#7f7f13"
        assert dialog.summary_label.text() == (
            "1 pair of classes collapses under deuteranopia."
        )

    def test_swatches_are_painted(self, make_dialog):
        dialog = make_dialog()
        icon = dialog.table.item(0, 2).icon()
        assert not icon.isNull()
        image = icon.pixmap(14, 14).toImage()
        centre = image.pixelColor(7, 7)
        assert (centre.red(), centre.green(), centre.blue()) == (0x2C, 0xA0, 0x2C)

    def test_normal_vision_names_the_condition_in_each_note(self, make_dialog):
        dialog = make_dialog()
        notes = [r[4] for r in table_rows(dialog)]
        assert notes[0] == "like Rejected (deuteranopia)"
        assert "most under deuteranopia" in dialog.summary_label.text()

    def test_a_safe_condition_says_so(self, make_dialog):
        dialog = make_dialog()
        dialog.set_condition("protanopia")
        assert [r[4] for r in table_rows(dialog)] == ["ok"] * 4
        assert dialog.summary_label.text() == (
            "All classes stay distinguishable under protanopia."
        )

    def test_refresh_reads_the_provider_again(self, make_dialog):
        entries = list(PERMITS)
        dialog = make_dialog(entries=entries)
        assert dialog.table.rowCount() == 4
        entries.append(LegendEntry("Track", "#808080", "roads", "Roads"))
        dialog.refresh()
        assert dialog.table.rowCount() == 5

    def test_empty_legend_explains_itself(self, make_dialog):
        dialog = make_dialog(entries=())
        assert dialog.table.rowCount() == 0
        assert "Save the cartograph" in dialog.summary_label.text()
        assert not dialog.ask_button.isEnabled()
        assert dialog.ask_button.toolTip()


class TestSignalsAndNika:
    def test_mode_signal_carries_the_condition(self, make_dialog):
        from nika_onlymap_exporter.ui.cartograph.accessibility_dialog import (
            simulating_tooltip,
        )

        dialog = make_dialog()
        seen = []
        dialog.mode_changed.connect(seen.append)
        dialog.set_condition("deuteranopia")
        dialog.set_condition(None)
        assert seen == ["deuteranopia", ""]
        assert simulating_tooltip("deuteranopia") == "Simulating deuteranopia"

    def test_ask_nika_receives_the_findings(self, make_dialog):
        ask = Recorder()
        dialog = make_dialog(ask_nika=ask)
        assert dialog.ask_button.isEnabled()
        dialog.ask_button.click()
        assert len(ask.calls) == 1
        text = ask.calls[0]
        assert "Layer 'Permits'" in text
        assert "'Approved' (#2ca02c) and 'Rejected' (#d62728)" in text
        assert "deuteranopia" in text
        assert "Primary" not in text

    def test_nothing_to_fix_disables_ask(self, make_dialog):
        dialog = make_dialog(entries=PERMITS[2:])
        assert not dialog.ask_button.isEnabled()
        assert dialog.ask_button.toolTip()

    def test_is_a_non_modal_tool_window(self, make_dialog):
        from qgis.PyQt.QtCore import Qt

        dialog = make_dialog()
        assert not dialog.isModal()
        assert dialog.windowFlags() & Qt.WindowType.Tool == Qt.WindowType.Tool


class TestLegendFromProject:
    def test_categorized_layer_through_read_project(self, project, make_memory_layer):
        from nika_onlymap_exporter.core.cvd import lint_legend_colours
        from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
        from nika_onlymap_exporter.core.project_reader import read_project

        layer = make_memory_layer(
            "permits",
            fields="status:string",
            features=[("a", [0.0, 0.0]), ("r", [1.0, 1.0]), ("x", [2.0, 2.0])],
        )
        symbol = qgis_core.QgsMarkerSymbol.createSimple
        layer.setRenderer(
            qgis_core.QgsCategorizedSymbolRenderer(
                "status",
                [
                    qgis_core.QgsRendererCategory(
                        "a", symbol({"color": "#2ca02c"}), "Approved"
                    ),
                    qgis_core.QgsRendererCategory(
                        "r", symbol({"color": "#d62728"}), "Rejected"
                    ),
                    qgis_core.QgsRendererCategory(
                        "x", symbol({"color": "#000000"}), "Withdrawn"
                    ),
                ],
            )
        )
        # Switched off in QGIS: not drawn, so not in the legend either.
        layer.renderer().updateCategoryRenderState(2, False)
        project.addMapLayer(layer)

        export = read_project(project, FidelityReportBuilder())
        entries = legend_entries(export.layers)

        assert [(e.layer_name, e.label, e.color) for e in entries] == [
            ("permits", "Approved", "#2ca02c"),
            ("permits", "Rejected", "#d62728"),
        ]
        assert entries[0].layer == layer.id()
        lint = lint_legend_colours(entries)
        assert [(c.a.label, c.b.label, c.condition) for c in lint.collapses] == [
            ("Approved", "Rejected", "deuteranopia")
        ]
