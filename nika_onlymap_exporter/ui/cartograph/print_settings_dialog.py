"""Share > PDF settings...: the cartograph's PDF dialog (issue #5 S1).

Edits a `core.print_settings.PrintSettings` and says what the user wants done
with it: **Save PDF…** (the default) or **Save** (keep the settings for the
next autosave without exporting). Routing the export is the controller's job;
this dialog only chooses.

**Honest about colour.** Every PDF is written by QGIS (RGB by default).
CMYK and ICC need QGIS built on Qt 6.8+ (`exporters/print_pdf.py`). When that
is missing the two choices stay in the list, disabled, with the reason as
their tooltip: never hidden and never silently swapped for RGB. A saved CMYK
choice the current install cannot honour is shown as RGB with a line saying
why.

**Presets** are user-wide (`QSettings`, one JSON value under
`PRESETS_KEY`), namespaced to this plugin; corrupt stored data reads as no
presets rather than breaking the dialog (`presets_from_json`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.print_settings import (
    BLEED_MM,
    CUSTOM_MAX_MM,
    CUSTOM_MIN_MM,
    PAGE_CUSTOM,
    PAGE_FROM_LAYOUT,
    PAGE_PRESETS_MM,
    PROFILE_CMYK,
    PROFILE_ICC,
    PROFILE_RGB,
    PROFILES,
    RESOLUTIONS,
    PrintSettings,
    preset_size_mm,
    presets_from_json,
    presets_to_json,
)
from .. import theme
from ..fitted_label import FittedLabel
from ..i18n import N_, tr

#: `QSettings` key for the saved presets. The plugin's own namespace, the same
#: `qgis2webmap/` prefix as its other settings -- not qgis2carto's.
PRESETS_KEY = "qgis2webmap/cartograph_print_presets"

#: What the dialog was closed with.
ACTION_EXPORT = "export"
ACTION_SAVE = "save"

BLEED_LABELS = {
    "none": N_("None"),
    "3mm": N_("3 mm"),
    "5mm": N_("5 mm"),
    "eighth_inch": N_("1/8 in"),
}

PROFILE_LABELS = {
    PROFILE_RGB: N_("RGB (sRGB)"),
    PROFILE_CMYK: N_("CMYK PDF/X-4 — via QGIS"),
    PROFILE_ICC: N_("Printer ICC profile..."),
}

#: The note under the profile, word for word from the issue.
PROFILE_NOTES = {
    PROFILE_RGB: N_(
        "Screens and browsers are RGB. Fine for office printers and posters "
        "printed from a PDF."
    ),
    PROFILE_CMYK: N_(
        "Needs QGIS built on Qt 6.8 or newer; the plugin runs the QGIS "
        "PDF/X-4 exporter."
    ),
    PROFILE_ICC: N_(
        "Ask the print shop for their profile (FOGRA39 in Europe, GRACoL in "
        "North America). Embedded as the output intent."
    ),
}

_PRESET_PLACEHOLDER = ""


def load_presets(settings: QSettings) -> dict[str, PrintSettings]:
    """The saved presets; empty when there are none or the value is corrupt."""
    return presets_from_json(settings.value(PRESETS_KEY, "", type=str))


def save_presets(settings: QSettings, presets: dict[str, PrintSettings]) -> None:
    settings.setValue(PRESETS_KEY, presets_to_json(presets))
    settings.sync()


def _from_layout_label(layout_page_mm: tuple[float, float]) -> str:
    width, height = layout_page_mm
    for name in PAGE_PRESETS_MM:
        for orientation in ("portrait", "landscape"):
            preset_w, preset_h = preset_size_mm(name, orientation)
            if abs(preset_w - width) < 0.5 and abs(preset_h - height) < 0.5:
                return tr("{size} (from layout)").format(
                    size=_preset_label(name, orientation)
                )
    return tr("{width} x {height} mm (from layout)").format(
        width=f"{width:g}", height=f"{height:g}"
    )


def _preset_label(name: str, orientation: str) -> str:
    if orientation == "landscape":
        return tr("{name} landscape").format(name=name)
    return tr("{name} portrait").format(name=name)


class PrintSettingsDialog(QDialog):
    """The modal Print settings dialog.

    `layout_page_mm` is the source layout's page 1 size, for the "from
    layout" entry. `colour_support` is
    `exporters.print_pdf.colour_management_support()`'s answer; it is a
    parameter so the controller asks once and tests can choose.
    `qsettings` defaults to the user's `QSettings()`.
    """

    def __init__(
        self,
        settings: PrintSettings,
        *,
        layout_page_mm: tuple[float, float],
        colour_support: tuple[bool, str] | None = None,
        qsettings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("PDF settings"))
        self.setModal(True)
        self.action = ""
        self._qsettings = qsettings if qsettings is not None else QSettings()
        self._icc_path = settings.icc_path
        self._layout_size_mm = layout_page_mm
        self._profile_reasons = self._compute_profile_reasons(colour_support)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_preset_row())
        layout.addWidget(self._build_page_group(layout_page_mm))
        layout.addWidget(self._build_colour_group())
        layout.addWidget(self._build_buttons())

        self._load(settings)
        theme.attach_menus(self)

    # ---- Building ----------------------------------------------------------

    def _compute_profile_reasons(
        self, colour_support: tuple[bool, str] | None
    ) -> dict[str, str]:
        """Profile -> why it is unavailable; absent when it is available."""
        if colour_support is None:
            from ...exporters.print_pdf import colour_management_support

            colour_support = colour_management_support()
        supported, why = colour_support
        if supported:
            return {}
        return {PROFILE_CMYK: why, PROFILE_ICC: why}

    def _build_preset_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.setToolTip(tr("Load saved print settings"))
        self.preset_combo.setAccessibleName(tr("Preset"))
        self.preset_combo.activated.connect(self._on_preset_chosen)
        self.save_preset_button = QPushButton(tr("Save as preset..."))
        self.save_preset_button.setToolTip(
            tr("Keep these settings under a name, for every project")
        )
        self.save_preset_button.clicked.connect(self._on_save_preset)
        row.addWidget(self.preset_combo, 1)
        row.addWidget(self.save_preset_button)
        self._refresh_presets()
        return row

    def _build_page_group(self, layout_page_mm: tuple[float, float]) -> QGroupBox:
        group = QGroupBox(tr("Page"))
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.size_combo = QComboBox()
        self.size_combo.addItem(_from_layout_label(layout_page_mm), PAGE_FROM_LAYOUT)
        for name in PAGE_PRESETS_MM:
            for orientation in ("portrait", "landscape"):
                self.size_combo.addItem(
                    _preset_label(name, orientation), f"{name}|{orientation}"
                )
        self.size_combo.addItem(tr("Custom..."), PAGE_CUSTOM)
        self.size_combo.currentIndexChanged.connect(self._sync_custom_visibility)
        self.size_combo.currentIndexChanged.connect(self._sync_size_note)
        form.addRow(tr("Size"), self.size_combo)

        self.custom_row = QWidget()
        custom = QHBoxLayout(self.custom_row)
        custom.setContentsMargins(0, 0, 0, 0)
        self.custom_width = self._mm_spin(tr("Custom page width"))
        self.custom_height = self._mm_spin(tr("Custom page height"))
        custom.addWidget(self.custom_width)
        custom.addWidget(self.custom_height)
        form.addRow("", self.custom_row)

        # Changing the sheet does not reflow the layout: the consequence is
        # spelled out here, before anything is exported.
        self.size_note_label = FittedLabel()
        form.addRow("", self.size_note_label)
        self.custom_width.valueChanged.connect(self._sync_size_note)
        self.custom_height.valueChanged.connect(self._sync_size_note)

        self.dpi_combo = QComboBox()
        for dpi in RESOLUTIONS:
            self.dpi_combo.addItem(tr("{dpi} dpi").format(dpi=dpi), dpi)
        form.addRow(tr("Resolution"), self.dpi_combo)

        self.bleed_combo = QComboBox()
        for key, label in BLEED_LABELS.items():
            self.bleed_combo.addItem(tr(label), key)
        self.bleed_combo.setToolTip(
            tr("Extra picture past the trim, so a slightly-off cut shows no white edge")
        )
        self.bleed_combo.currentIndexChanged.connect(self._sync_crop_marks)
        form.addRow(tr("Bleed"), self.bleed_combo)

        self.crop_marks_check = QCheckBox(tr("Crop marks"))
        self.safe_zone_check = QCheckBox(tr("Safe zone guide"))
        self.safe_zone_check.setToolTip(
            tr("An on-screen guide inside the trim. Never printed.")
        )
        form.addRow("", self.crop_marks_check)
        form.addRow("", self.safe_zone_check)
        return group

    def _mm_spin(self, name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(CUSTOM_MIN_MM, CUSTOM_MAX_MM)
        spin.setDecimals(1)
        spin.setSuffix(tr(" mm"))
        spin.setAccessibleName(name)
        spin.setToolTip(name)
        return spin

    def _build_colour_group(self) -> QGroupBox:
        group = QGroupBox(tr("Colour"))
        box = QVBoxLayout(group)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.profile_combo = QComboBox()
        model = self.profile_combo.model()
        for index, profile in enumerate(PROFILES):
            self.profile_combo.addItem(tr(PROFILE_LABELS[profile]), profile)
            reason = self._profile_reasons.get(profile)
            if reason:
                item = model.item(index)
                item.setEnabled(False)
                item.setToolTip(reason)
        self._last_profile_index = 0
        self.profile_combo.activated.connect(self._on_profile_activated)
        self.profile_combo.currentIndexChanged.connect(self._sync_note)
        self.profile_combo.currentIndexChanged.connect(self._sync_embed_fonts)
        form.addRow(tr("Profile"), self.profile_combo)
        box.addLayout(form)

        self.note_label = FittedLabel()
        box.addWidget(self.note_label)
        self.downgrade_label = FittedLabel()
        self.downgrade_label.setVisible(False)
        box.addWidget(self.downgrade_label)

        self.flatten_check = QCheckBox(tr("Flatten to image"))
        self.flatten_check.setToolTip(
            tr(
                "Prints one picture instead of text and shapes. Text stops "
                "being selectable; some print shops ask for it."
            )
        )
        self.embed_fonts_check = QCheckBox(tr("Embed fonts"))
        box.addWidget(self.flatten_check)
        box.addWidget(self.embed_fonts_check)
        return group

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox()
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.addButton(
            tr("Save"), QDialogButtonBox.ButtonRole.ApplyRole
        )
        self.save_button.setToolTip(
            tr("Keep these settings for this layout without exporting")
        )
        self.export_button = buttons.addButton(
            tr("Save PDF…"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.export_button.setDefault(True)
        self.export_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(lambda: self._finish(ACTION_SAVE))
        self.export_button.clicked.connect(lambda: self._finish(ACTION_EXPORT))
        return buttons

    # ---- State -------------------------------------------------------------

    def _load(self, settings: PrintSettings) -> None:
        if settings.page in (PAGE_FROM_LAYOUT, PAGE_CUSTOM):
            key = settings.page
        else:
            key = f"{settings.page}|{settings.orientation}"
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(key)))
        self.custom_width.setValue(settings.custom_width_mm)
        self.custom_height.setValue(settings.custom_height_mm)
        self.dpi_combo.setCurrentIndex(max(0, self.dpi_combo.findData(settings.dpi)))
        self.bleed_combo.setCurrentIndex(
            max(0, self.bleed_combo.findData(settings.bleed))
        )
        self.crop_marks_check.setChecked(settings.crop_marks)
        self.safe_zone_check.setChecked(settings.safe_zone)
        self.flatten_check.setChecked(settings.flatten)
        self.embed_fonts_check.setChecked(settings.embed_fonts)
        self._icc_path = settings.icc_path

        profile = settings.profile
        reason = self._profile_reasons.get(profile)
        if reason:
            profile = PROFILE_RGB
            self.downgrade_label.setText(
                tr("Saved as {profile}, which is not available here: {reason}").format(
                    profile=tr(PROFILE_LABELS[settings.profile]), reason=reason
                )
            )
        self.downgrade_label.setVisible(bool(reason))
        self._set_profile(profile)
        self._sync_custom_visibility()
        self._sync_size_note()
        self._sync_crop_marks()
        self._sync_note()
        self._sync_embed_fonts()

    def settings(self) -> PrintSettings:
        """The settings as currently shown."""
        size = str(self.size_combo.currentData())
        page, orientation = size, "portrait"
        if "|" in size:
            page, orientation = size.split("|", 1)
        profile = str(self.profile_combo.currentData())
        return PrintSettings(
            page=page,
            orientation=orientation,
            custom_width_mm=self.custom_width.value(),
            custom_height_mm=self.custom_height.value(),
            dpi=int(self.dpi_combo.currentData()),
            bleed=str(self.bleed_combo.currentData()),
            crop_marks=self.crop_marks_check.isChecked(),
            safe_zone=self.safe_zone_check.isChecked(),
            profile=profile,
            icc_path=self._icc_path,
            flatten=self.flatten_check.isChecked(),
            embed_fonts=self.embed_fonts_check.isChecked(),
        )

    def profile_available(self, profile: str) -> bool:
        return profile not in self._profile_reasons

    def profile_tooltip(self, profile: str) -> str:
        """The tooltip on a profile's list entry: why it is disabled, or ''."""
        return self._profile_reasons.get(profile, "")

    def _set_profile(self, profile: str) -> None:
        index = max(0, self.profile_combo.findData(profile))
        self.profile_combo.setCurrentIndex(index)
        self._last_profile_index = index
        self._sync_icc_label()

    # ---- Reactions ---------------------------------------------------------

    def _sync_custom_visibility(self, *_args: object) -> None:
        self.custom_row.setVisible(self.size_combo.currentData() == PAGE_CUSTOM)

    def _sync_size_note(self, *_args: object) -> None:
        """Say what the chosen sheet does to the items before any export."""
        layout_w, layout_h = self._layout_size_mm
        trim_w, trim_h = self.settings().trim_size_mm(self._layout_size_mm)
        if abs(trim_w - layout_w) < 0.5 and abs(trim_h - layout_h) < 0.5:
            text = ""
        else:
            text = tr(
                "This changes the sheet only, without moving or scaling the "
                "items: they keep their positions from the top-left corner."
            )
            if trim_w < layout_w - 0.5 or trim_h < layout_h - 0.5:
                text += " " + tr(
                    "The layout is {layout_w} x {layout_h} mm and this sheet is "
                    "{sheet_w} x {sheet_h} mm, so anything beyond the sheet is "
                    "cut off."
                ).format(
                    layout_w=f"{layout_w:g}",
                    layout_h=f"{layout_h:g}",
                    sheet_w=f"{trim_w:g}",
                    sheet_h=f"{trim_h:g}",
                )
        self.size_note_label.setText(text)
        self.size_note_label.setVisible(bool(text))

    def _sync_embed_fonts(self, *_args: object) -> None:
        """Embed fonts applies to every PDF, since QGIS writes them all."""
        self.embed_fonts_check.setToolTip(tr("Off turns text into outlines in the PDF"))

    def _sync_crop_marks(self, *_args: object) -> None:
        has_bleed = BLEED_MM.get(str(self.bleed_combo.currentData())) is not None
        self.crop_marks_check.setEnabled(has_bleed)
        self.crop_marks_check.setToolTip(
            tr("Trim marks at the four corners, drawn in the bleed")
            if has_bleed
            else tr("Crop marks are drawn in the bleed: choose a bleed to print them")
        )

    def _sync_note(self, *_args: object) -> None:
        profile = str(self.profile_combo.currentData())
        self.note_label.setText(tr(PROFILE_NOTES.get(profile, "")))

    def _sync_icc_label(self) -> None:
        index = self.profile_combo.findData(PROFILE_ICC)
        label = tr(PROFILE_LABELS[PROFILE_ICC])
        if self._icc_path:
            label = tr("Printer ICC profile: {name}").format(
                name=Path(self._icc_path).name
            )
        self.profile_combo.setItemText(index, label)

    def _on_profile_activated(self, index: int) -> None:
        if self.profile_combo.itemData(index) == PROFILE_ICC:
            chosen = self._choose_icc_file()
            if chosen:
                self._icc_path = chosen
            elif not self._icc_path:
                # Nothing chosen and nothing from before: an ICC choice with
                # no file cannot be exported, so go back to what it was.
                self.profile_combo.setCurrentIndex(self._last_profile_index)
                return
        self._last_profile_index = index
        self._sync_icc_label()

    def _choose_icc_file(self) -> str:
        """Ask for an .icc file; '' when cancelled. Its own method so tests
        can answer it without a native file dialog."""
        start = str(Path(self._icc_path).parent) if self._icc_path else ""
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            tr("Choose the print shop's ICC profile"),
            start,
            tr("ICC profiles (*.icc *.icm)"),
        )
        return chosen

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    # ---- Presets -----------------------------------------------------------

    def _refresh_presets(self, select: str = "") -> None:
        presets = load_presets(self._qsettings)
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem(
            tr("Presets") if presets else tr("No saved presets"), _PRESET_PLACEHOLDER
        )
        for name in sorted(presets, key=str.casefold):
            self.preset_combo.addItem(name, name)
        self.preset_combo.setEnabled(bool(presets))
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(select)))
        self.preset_combo.blockSignals(False)

    def _on_preset_chosen(self, index: int) -> None:
        name = self.preset_combo.itemData(index)
        if not name:
            return
        preset = load_presets(self._qsettings).get(str(name))
        if preset is not None:
            self._load(preset)

    def _ask_preset_name(self) -> str:
        """Ask for a preset name; '' when cancelled. Overridable in tests."""
        name, ok = QInputDialog.getText(self, tr("Save as preset"), tr("Preset name"))
        return name.strip() if ok else ""

    def save_preset(self, name: str) -> None:
        """Store the current settings as preset `name`, replacing any."""
        name = name.strip()
        if not name:
            return
        presets = load_presets(self._qsettings)
        presets[name] = self.settings()
        save_presets(self._qsettings, presets)
        self._refresh_presets(select=name)

    def _on_save_preset(self) -> None:
        self.save_preset(self._ask_preset_name())


def ask_print_settings(
    settings: PrintSettings,
    *,
    layout_page_mm: tuple[float, float],
    parent: QWidget | None = None,
) -> tuple[str, PrintSettings] | None:
    """Show the dialog; `(ACTION_EXPORT | ACTION_SAVE, settings)` or None
    when cancelled."""
    dialog = PrintSettingsDialog(
        settings,
        layout_page_mm=layout_page_mm,
        parent=parent,
    )
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.action, dialog.settings()
    finally:
        dialog.deleteLater()


__all__ = [
    "ACTION_EXPORT",
    "ACTION_SAVE",
    "PRESETS_KEY",
    "PrintSettingsDialog",
    "ask_print_settings",
    "load_presets",
    "save_presets",
]
