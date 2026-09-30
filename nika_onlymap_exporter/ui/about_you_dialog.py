"""About you: who the maps are for, the sector, what they do. All optional.

Shown once, before the What's new popup, and again only when the user asks from
the Help tab. The answers are stored on this computer and go nowhere unless the
user chooses Share afterwards - which this dialog says, in so many words.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..research import text
from ..research.profile import (
    AUDIENCE_LABELS,
    AUDIENCES,
    SECTOR_LABELS,
    SECTORS,
    USE_CASE_LABELS,
    USE_CASES,
    Profile,
)
from ..research.scrub import MAX_OTHER_LENGTH


def _note(message: str, parent: QWidget) -> QLabel:
    label = QLabel(message, parent)
    label.setWordWrap(True)
    return label


class AboutYouDialog(QDialog):
    """Returns `Accepted` for Save, `Rejected` for Skip or close."""

    def __init__(
        self,
        profile: Profile | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("About you - QGIS2WebMap")
        self.setMinimumWidth(560)
        current = profile or Profile()
        layout = QVBoxLayout(self)

        layout.addWidget(_note(text.ABOUT_YOU_INTRO, self))

        audience_box = QGroupBox("Who are your maps for?", self)
        audience_layout = QVBoxLayout(audience_box)
        self.audience_group = QButtonGroup(self)
        self.audience_buttons: dict[str, QRadioButton] = {}
        for value in AUDIENCES:
            button = QRadioButton(AUDIENCE_LABELS[value], audience_box)
            button.setChecked(current.audience == value)
            self.audience_group.addButton(button)
            self.audience_buttons[value] = button
            audience_layout.addWidget(button)
        layout.addWidget(audience_box)

        form = QFormLayout()
        self.sector_combo = QComboBox(self)
        self.sector_combo.addItem("Choose a sector (optional)", None)
        for value in SECTORS:
            self.sector_combo.addItem(SECTOR_LABELS[value], value)
        if current.sector is not None:
            self.sector_combo.setCurrentIndex(SECTORS.index(current.sector) + 1)
        form.addRow("Sector", self.sector_combo)
        layout.addLayout(form)

        uses_box = QGroupBox("What do your maps do? Tick any that apply.", self)
        uses_layout = QGridLayout(uses_box)
        self.use_case_boxes: dict[str, QCheckBox] = {}
        for index, value in enumerate(USE_CASES):
            box = QCheckBox(USE_CASE_LABELS[value], uses_box)
            box.setChecked(value in current.use_cases)
            self.use_case_boxes[value] = box
            uses_layout.addWidget(box, index // 2, index % 2)
        layout.addWidget(uses_box)

        details = QFormLayout()
        self.other_edit = QLineEdit(current.use_case_other or "", self)
        self.other_edit.setMaxLength(MAX_OTHER_LENGTH)
        self.other_edit.setPlaceholderText("Anything else about what you map")
        details.addRow("In your words", self.other_edit)
        layout.addLayout(details)

        nothing_sent = _note(text.ABOUT_YOU_NOTHING_SENT, self)
        nothing_sent.setTextFormat(Qt.TextFormat.PlainText)
        font = nothing_sent.font()
        font.setBold(True)
        nothing_sent.setFont(font)
        layout.addWidget(nothing_sent)
        # Spare height goes below the questions, not between them.
        layout.addStretch(1)

        buttons = QDialogButtonBox(self)
        save = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        skip = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        if save is not None:
            save.setDefault(True)
        if skip is not None:
            skip.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def profile(self) -> Profile:
        audience = next(
            (value for value, b in self.audience_buttons.items() if b.isChecked()),
            None,
        )
        sector = self.sector_combo.currentData()
        return Profile.build(
            audience,
            sector if isinstance(sector, str) else None,
            [value for value, box in self.use_case_boxes.items() if box.isChecked()],
            self.other_edit.text(),
        )
