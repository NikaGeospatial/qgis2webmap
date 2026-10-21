"""The layout's map frames as a narrow column of cards, one per map item.

The Frames tab was a six-column table: map item, CRS, Live/Static, DPI, issue
count and a "Use canvas view" button. It now lives in the Cartograph panel
beside the page, about 340 px wide, where six columns either scroll sideways
or squeeze every cell to an ellipsis. Each map is therefore a small card that
stacks the same facts in three short lines:

- the map's name, with how many of its items the cartograph changes at the
  right (the Fidelity tab has the detail);
- its CRS, muted;
- Live or Static, the DPI when Static, and "Use canvas view".

The DPI list is hidden rather than greyed out while a frame is Live: a
disabled control in a narrow card reads as broken, and it only means
something for a Static frame.

The widget only shows and reports. What a choice does - saving it, matching
the canvas - is the controller's business, through the signals below, which
fire for the user's edits and never for `set_frames` filling the cards in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..fitted_label import FittedLabel
from ..i18n import plural, tr

LIVE, STATIC = "live", "static"


@dataclass(frozen=True)
class FrameRow:
    """One map item on the layout, as the card shows it."""

    item_id: str
    name: str
    crs: str  # already-translated text, e.g. "EPSG:3857" or tr("Unknown CRS")
    mode: str  # LIVE or STATIC
    dpi: int
    issues: int | None  # None = not checked yet


class FrameCard(QFrame):
    """One map item: name and change count, CRS, then its frame settings."""

    def __init__(
        self, row: FrameRow, dpi_choices: Sequence[int], parent: QWidget | None
    ) -> None:
        super().__init__(parent)
        self.item_id = row.item_id
        self.setProperty("role", "frameCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.name_label = QLabel(row.name, self)
        self.name_label.setProperty("role", "frameName")
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        self.name_label.setToolTip(row.name)
        # A long name gives way to the change count, not the other way round.
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        head.addWidget(self.name_label, 1)
        self.issues_label = QLabel(self)
        self.issues_label.setProperty("role", "frameIssues")
        self.issues_label.setToolTip(
            tr(
                "Items on this frame the cartograph changes or leaves out; "
                "see the Fidelity tab"
            )
        )
        head.addWidget(self.issues_label, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(head)

        self.crs_label = QLabel(row.crs, self)
        self.crs_label.setProperty("role", "hint")
        layout.addWidget(self.crs_label)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem(tr("Live"), LIVE)
        self.mode_combo.addItem(tr("Static"), STATIC)
        controls.addWidget(self.mode_combo)

        self.dpi_combo = QComboBox(self)
        # Wide enough for "300" whatever the style's arrow takes.
        self.dpi_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.dpi_combo.setMinimumContentsLength(4)
        for dpi in dpi_choices:
            self.dpi_combo.addItem(str(dpi), dpi)
        controls.addWidget(self.dpi_combo)
        controls.addStretch(1)

        self.match_button = QToolButton(self)
        self.match_button.setText(tr("Use canvas view"))
        self.match_button.setToolTip(
            tr("Set this map item's extent to what the QGIS map canvas shows")
        )
        self.match_button.setProperty("role", "secondary")
        self.match_button.setProperty("hasMenu", False)
        controls.addWidget(self.match_button)
        layout.addLayout(controls)

        self.set_values(row)

    def set_values(self, row: FrameRow) -> None:
        """Show `row`'s mode, DPI and change count without reporting an edit."""
        for combo in (self.mode_combo, self.dpi_combo):
            combo.blockSignals(True)
        try:
            self.mode_combo.setCurrentIndex(0 if row.mode == LIVE else 1)
            found = self.dpi_combo.findData(row.dpi)
            self.dpi_combo.setCurrentIndex(found if found >= 0 else 0)
        finally:
            for combo in (self.mode_combo, self.dpi_combo):
                combo.blockSignals(False)
        self.show_mode()
        self.set_issue_count(row.issues)

    def mode(self) -> str:
        return str(self.mode_combo.currentData())

    def dpi(self) -> int:
        return int(self.dpi_combo.currentData())

    def show_mode(self) -> None:
        """The DPI list only for a Static frame."""
        self.dpi_combo.setVisible(self.mode() == STATIC)

    def set_issue_count(self, issues: int | None) -> None:
        """ "3 changes", a muted "No changes", or nothing before the check."""
        if issues is None:
            self.issues_label.clear()
            self.issues_label.setVisible(False)
            return
        if issues:
            self.issues_label.setText(plural(issues, "{n} change", "{n} changes"))
        else:
            self.issues_label.setText(tr("No changes"))
        self.issues_label.setProperty("muted", issues == 0)
        # A dynamic property only restyles once the style is told.
        self.issues_label.style().unpolish(self.issues_label)
        self.issues_label.style().polish(self.issues_label)
        self.issues_label.setVisible(True)


class FramesList(QWidget):
    """The intro, then a card per map item, or a note that there are none."""

    mode_changed = pyqtSignal(str, str)  # item_id, LIVE / STATIC
    dpi_changed = pyqtSignal(str, int)  # item_id, dpi
    match_canvas_requested = pyqtSignal(str)  # item_id

    def __init__(
        self, dpi_choices: Sequence[int], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._dpi_choices = tuple(dpi_choices)
        self._cards: dict[str, FrameCard] = {}
        self._has_layout = False
        self._canvas_available = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.intro = FittedLabel(
            tr(
                "One row per map on the selected layout. Live frames render in "
                "the browser and need a CRS the runtime can map; Static frames "
                "export a raster image at the DPI you choose."
            ),
            self,
        )
        self.intro.setWordWrap(True)
        self.intro.setProperty("role", "hint")
        layout.addWidget(self.intro)

        self._cards_box = QVBoxLayout()
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(6)
        layout.addLayout(self._cards_box)

        self.empty_note = FittedLabel(tr("This layout has no maps on it yet."), self)
        self.empty_note.setWordWrap(True)
        self.empty_note.setVisible(False)
        layout.addWidget(self.empty_note)

    def set_frames(self, rows: list[FrameRow] | None) -> None:
        """Replace the cards. None is no layout at all; [] a layout with no maps."""
        for card in self._cards.values():
            self._cards_box.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._has_layout = rows is not None
        self.empty_note.setVisible(rows is not None and not rows)
        for row in rows or ():
            card = FrameCard(row, self._dpi_choices, self)
            card.match_button.setEnabled(self._canvas_available)
            self._connect(card)
            self._cards_box.addWidget(card)
            self._cards[row.item_id] = card

    def _connect(self, card: FrameCard) -> None:
        card.mode_combo.currentIndexChanged.connect(
            lambda _index, card=card: self._on_mode(card)
        )
        card.dpi_combo.currentIndexChanged.connect(
            lambda _index, card=card: self._on_dpi(card)
        )
        card.match_button.clicked.connect(
            lambda _checked=False, card=card: self._on_match(card)
        )

    def _is_current(self, card: FrameCard) -> bool:
        """A replaced card may outlive `set_frames` until Qt deletes it."""
        return self._cards.get(card.item_id) is card

    def _on_mode(self, card: FrameCard) -> None:
        if not self._is_current(card):
            return
        card.show_mode()
        self.mode_changed.emit(card.item_id, card.mode())

    def _on_dpi(self, card: FrameCard) -> None:
        if self._is_current(card):
            self.dpi_changed.emit(card.item_id, card.dpi())

    def _on_match(self, card: FrameCard) -> None:
        if self._is_current(card):
            self.match_canvas_requested.emit(card.item_id)

    def set_issue_count(self, item_id: str, issues: int | None) -> None:
        card = self._cards.get(item_id)
        if card is not None:
            card.set_issue_count(issues)

    def set_canvas_available(self, available: bool) -> None:
        """ "Use canvas view" needs a QGIS map canvas to read the extent from."""
        self._canvas_available = available
        for card in self._cards.values():
            card.match_button.setEnabled(available)

    def card(self, item_id: str) -> FrameCard:
        return self._cards[item_id]

    def summary(self) -> str:
        """A few words for the closed section: "2 live, 1 static"."""
        if not self._has_layout:
            return ""
        if not self._cards:
            return tr("No maps")
        modes = [card.mode() for card in self._cards.values()]
        live = modes.count(LIVE)
        static = len(modes) - live
        live_text = plural(live, "{n} live", "{n} live")
        static_text = plural(static, "{n} static", "{n} static")
        if live and static:
            return tr("{live}, {static}").format(live=live_text, static=static_text)
        return live_text if live else static_text
