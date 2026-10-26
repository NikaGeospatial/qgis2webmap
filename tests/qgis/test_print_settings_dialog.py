"""The Print settings dialog (issue #5 S1), as a real Qt dialog.

Every test hands the dialog its own ini-file `QSettings` under `tmp_path`,
never the user's real settings.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core")

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import QDialog

from nika_onlymap_exporter.core.print_settings import (
    BLEED_MM,
    PAGE_CUSTOM,
    PAGE_FROM_LAYOUT,
    PROFILE_CMYK,
    PROFILE_ICC,
    PROFILE_RGB,
    PrintSettings,
)
from nika_onlymap_exporter.ui.cartograph.print_settings_dialog import (
    ACTION_EXPORT,
    ACTION_SAVE,
    BLEED_LABELS,
    PRESETS_KEY,
    PrintSettingsDialog,
    load_presets,
)

RGB_NOTE = (
    "Screens and browsers are RGB. Fine for office printers and posters printed "
    "from a PDF."
)
CMYK_NOTE = (
    "Needs QGIS built on Qt 6.8 or newer and a cartograph with static frames "
    "only; the plugin runs the QGIS PDF/X-4 exporter."
)
ICC_NOTE = (
    "Ask the print shop for their profile (FOGRA39 in Europe, GRACoL in North "
    "America). Embedded as the output intent."
)


@pytest.fixture
def qsettings(tmp_path):
    return QSettings(str(tmp_path / "print.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def make_dialog(qgis_app, qsettings):
    made = []

    def _make(
        settings=None,
        *,
        has_live_frame=False,
        colour_support=(True, ""),
        page=(420, 297),
    ):
        dialog = PrintSettingsDialog(
            settings or PrintSettings(),
            layout_page_mm=page,
            has_live_frame=has_live_frame,
            colour_support=colour_support,
            qsettings=qsettings,
        )
        made.append(dialog)
        return dialog

    yield _make
    for dialog in made:
        dialog.deleteLater()


def _select(combo, data):
    index = combo.findData(data)
    assert index >= 0, data
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


class TestLayout:
    def test_every_bleed_choice_has_a_label(self) -> None:
        assert set(BLEED_LABELS) == set(BLEED_MM)

    def test_from_layout_entry_names_the_layout_page(self, make_dialog) -> None:
        dialog = make_dialog(page=(420, 297))
        assert dialog.size_combo.itemText(0) == "A3 landscape (from layout)"
        odd = make_dialog(page=(333, 222))
        assert odd.size_combo.itemText(0) == "333 x 222 mm (from layout)"

    def test_settings_round_trip_through_the_widgets(self, make_dialog) -> None:
        settings = PrintSettings(
            page="Letter",
            orientation="landscape",
            dpi=600,
            bleed="eighth_inch",
            crop_marks=False,
            safe_zone=False,
            flatten=True,
            embed_fonts=False,
        )
        assert make_dialog(settings).settings() == settings
        custom = PrintSettings(
            page=PAGE_CUSTOM, custom_width_mm=500, custom_height_mm=700
        )
        dialog = make_dialog(custom)
        assert dialog.settings() == custom
        assert not dialog.custom_row.isHidden()
        assert make_dialog(PrintSettings(page=PAGE_FROM_LAYOUT)).custom_row.isHidden()

    def test_crop_marks_are_disabled_without_a_bleed(self, make_dialog) -> None:
        dialog = make_dialog(PrintSettings(bleed="3mm"))
        assert dialog.crop_marks_check.isEnabled()
        _select(dialog.bleed_combo, "none")
        assert not dialog.crop_marks_check.isEnabled()
        assert "bleed" in dialog.crop_marks_check.toolTip()
        # The preference is kept, so a bleed brings the marks back.
        assert dialog.settings().crop_marks is True
        assert dialog.settings().crop_marks_effective is False

    def test_embed_fonts_is_off_and_explained_for_browser_printing(
        self, make_dialog
    ) -> None:
        dialog = make_dialog(PrintSettings(embed_fonts=False))
        assert dialog.settings().profile == PROFILE_RGB
        assert not dialog.embed_fonts_check.isEnabled()
        assert not dialog.embed_fonts_note.isHidden()
        assert "browser" in dialog.embed_fonts_note.text()
        # The preference is kept for the native export.
        assert dialog.settings().embed_fonts is False
        _select(dialog.profile_combo, PROFILE_CMYK)
        assert dialog.embed_fonts_check.isEnabled()
        assert dialog.embed_fonts_note.isHidden()
        assert dialog.settings().embed_fonts is False
        _select(dialog.profile_combo, PROFILE_RGB)
        assert not dialog.embed_fonts_check.isEnabled()

    def test_smaller_sheet_shows_the_clipping_consequence(self, make_dialog) -> None:
        dialog = make_dialog(PrintSettings(page=PAGE_FROM_LAYOUT), page=(420, 297))
        assert dialog.size_note_label.isHidden()
        _select(dialog.size_combo, "A4|portrait")
        assert not dialog.size_note_label.isHidden()
        text = dialog.size_note_label.text()
        assert "without moving or scaling" in text
        assert "420 x 297 mm" in text and "210 x 297 mm" in text
        assert "cut off" in text
        _select(dialog.size_combo, "A2|landscape")
        assert "cut off" not in dialog.size_note_label.text()
        assert not dialog.size_note_label.isHidden()
        _select(dialog.size_combo, PAGE_FROM_LAYOUT)
        assert dialog.size_note_label.isHidden()

    def test_export_is_the_default_button(self, make_dialog) -> None:
        dialog = make_dialog()
        assert dialog.export_button.isDefault()
        assert dialog.export_button.text() == "Export PDF"


class TestProfile:
    def test_note_follows_the_profile(self, make_dialog, tmp_path) -> None:
        dialog = make_dialog()
        assert dialog.note_label.text() == RGB_NOTE
        _select(dialog.profile_combo, PROFILE_CMYK)
        assert dialog.note_label.text() == CMYK_NOTE
        icc = tmp_path / "press.icc"
        dialog._choose_icc_file = lambda: str(icc)
        _select(dialog.profile_combo, PROFILE_ICC)
        assert dialog.note_label.text() == ICC_NOTE
        assert dialog.settings().icc_path == str(icc)
        assert "press.icc" in dialog.profile_combo.currentText()
        _select(dialog.profile_combo, PROFILE_RGB)
        assert dialog.note_label.text() == RGB_NOTE

    def test_cancelled_icc_choice_goes_back(self, make_dialog) -> None:
        dialog = make_dialog()
        _select(dialog.profile_combo, PROFILE_CMYK)
        dialog._choose_icc_file = lambda: ""
        _select(dialog.profile_combo, PROFILE_ICC)
        assert dialog.profile_combo.currentData() == PROFILE_CMYK
        assert dialog.settings().profile == PROFILE_CMYK

    def test_live_frame_disables_cmyk_and_icc_with_a_reason(self, make_dialog) -> None:
        dialog = make_dialog(has_live_frame=True)
        model = dialog.profile_combo.model()
        for profile in (PROFILE_CMYK, PROFILE_ICC):
            index = dialog.profile_combo.findData(profile)
            item = model.item(index)
            assert not item.isEnabled()
            assert "live frame" in item.toolTip()
            assert (
                dialog.profile_combo.itemData(index, Qt.ItemDataRole.ToolTipRole)
                == item.toolTip()
            )
            assert not dialog.profile_available(profile)
            assert "Static" in dialog.profile_tooltip(profile)
        assert dialog.profile_available(PROFILE_RGB)
        assert model.item(dialog.profile_combo.findData(PROFILE_RGB)).isEnabled()

    def test_old_qt_disables_them_with_its_reason(self, make_dialog) -> None:
        reason = (
            "CMYK and ICC PDFs need QGIS built on Qt 6.8 or newer; this QGIS "
            "uses Qt 5.15.13."
        )
        dialog = make_dialog(colour_support=(False, reason))
        assert dialog.profile_tooltip(PROFILE_CMYK) == reason
        assert dialog.profile_tooltip(PROFILE_ICC) == reason

    def test_unavailable_saved_choice_shows_rgb_and_says_why(self, make_dialog) -> None:
        dialog = make_dialog(PrintSettings(profile=PROFILE_CMYK), has_live_frame=True)
        assert dialog.profile_combo.currentData() == PROFILE_RGB
        assert not dialog.downgrade_label.isHidden()
        assert "live frame" in dialog.downgrade_label.text()
        assert make_dialog(
            PrintSettings(profile=PROFILE_CMYK)
        ).downgrade_label.isHidden()


class TestPresets:
    def test_presets_round_trip_through_qsettings(self, make_dialog, qsettings) -> None:
        settings = PrintSettings(
            page="A2", orientation="landscape", bleed="5mm", dpi=600
        )
        dialog = make_dialog(settings)
        dialog._ask_preset_name = lambda: "  Shop A2  "
        dialog.save_preset_button.click()

        assert load_presets(qsettings) == {"Shop A2": settings}
        reread = QSettings(qsettings.fileName(), QSettings.Format.IniFormat)
        assert load_presets(reread) == {"Shop A2": settings}
        assert dialog.preset_combo.currentText() == "Shop A2"

        other = make_dialog(PrintSettings())
        assert other.preset_combo.findData("Shop A2") > 0
        _select(other.preset_combo, "Shop A2")
        assert other.settings() == settings

    def test_corrupt_store_reads_as_no_presets(self, make_dialog, qsettings) -> None:
        qsettings.setValue(PRESETS_KEY, "{not json")
        dialog = make_dialog()
        assert dialog.preset_combo.count() == 1
        assert not dialog.preset_combo.isEnabled()
        dialog._ask_preset_name = lambda: "Fresh"
        dialog.save_preset_button.click()
        assert set(load_presets(qsettings)) == {"Fresh"}

    def test_cancelled_name_saves_nothing(self, make_dialog, qsettings) -> None:
        dialog = make_dialog()
        dialog._ask_preset_name = lambda: ""
        dialog.save_preset_button.click()
        assert load_presets(qsettings) == {}

    def test_key_is_namespaced_to_this_plugin(self) -> None:
        assert PRESETS_KEY == "qgis2webmap/cartograph_print_presets"


class TestButtons:
    @pytest.mark.parametrize(
        ("button", "action"),
        [("export_button", ACTION_EXPORT), ("save_button", ACTION_SAVE)],
    )
    def test_buttons_accept_with_their_action(
        self, make_dialog, button, action
    ) -> None:
        dialog = make_dialog()
        getattr(dialog, button).click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.action == action

    def test_cancel_rejects(self, make_dialog) -> None:
        dialog = make_dialog()
        dialog.cancel_button.click()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.action == ""
