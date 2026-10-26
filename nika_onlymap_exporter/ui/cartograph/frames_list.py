"""The layout's map frames as a compact list, one entry per map item.

Each map is two or three short lines in the narrow Cartograph panel:

- its name, how it appears in the HTML (Interactive or Image) and a small
  actions menu (Use canvas extent, Locate on page);
- its CRS, muted, and - only when the cartograph changes or leaves out
  something on it - an "N issues" badge that opens the detail in Checks;
- for an Image, its Resolution.

The Resolution row is hidden rather than greyed out while a frame is
Interactive: a disabled control in a narrow list reads as broken, and it only
means something for an image. Choosing a map's entry selects that frame on
the page, and a frame selected on the page marks its entry.

The widget only shows and reports. What a choice does - saving it, matching
the canvas, selecting the item - is the controller's business, through the
signals below, which fire for the user's edits and never for `set_frames`
filling the entries in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qgis.PyQt.QtCore import QEvent, QObject, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QAction,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QWheelEvent,
)
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
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
    """One map item on the layout, as its entry shows it."""

    item_id: str
    name: str
    crs: str  # already-translated text, e.g. "EPSG:3857" or tr("Unknown CRS")
    mode: str  # LIVE or STATIC
    dpi: int
    issues: int | None  # None = not checked yet


class _ElidedLabel(QLabel):
    """A one-line label that shows "..." where its text does not fit.

    `text()` stays the whole text; only what is painted is cut."""

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        painter = QPainter(self)
        shown = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.width()
        )
        self.style().drawItemText(
            painter,
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.palette(),
            self.isEnabled(),
            shown,
            QPalette.ColorRole.WindowText,
        )


class _QuietCombo(QComboBox):
    """A combo that never answers the mouse wheel and takes focus from the
    keyboard only: a wheel turn over it scrolls the panel behind it, and a
    click leaves no focus ring."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class FrameCard(QFrame):
    """One map item: name and type, CRS and issues, and the image resolution."""

    selected = pyqtSignal(str)  # item_id: the user chose this entry
    _issues: int | None = None

    def __init__(
        self, row: FrameRow, dpi_choices: Sequence[int], parent: QWidget | None
    ) -> None:
        super().__init__(parent)
        self.item_id = row.item_id
        self._name = row.name
        self.setProperty("role", "frameCard")
        self.setProperty("selected", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.name_label = _ElidedLabel(row.name, self)
        self.name_label.setProperty("role", "frameName")
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        # A long name gives way to the controls, not the other way round.
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        head.addWidget(self.name_label, 1)

        self.mode_combo = _QuietCombo(self)
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        # Interactive: readers pan and zoom it; Image: a picture of QGIS's map.
        self.mode_combo.addItem(tr("Interactive"), LIVE)
        self.mode_combo.addItem(tr("Image"), STATIC)
        self.mode_combo.setToolTip(
            tr(
                "Interactive: readers pan and zoom this map in the HTML. "
                "Image: a picture of the map as QGIS draws it."
            )
        )
        head.addWidget(self.mode_combo)

        self.actions_button = QToolButton(self)
        self.actions_button.setText("⋯")
        self.actions_button.setProperty("role", "secondary")
        self.actions_button.setProperty("hasMenu", False)
        # Only the dots: the menu's arrow would double the button's width.
        self.actions_button.setProperty("frameActions", True)
        self.actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        menu = QMenu(self.actions_button)
        self.canvas_action = QAction(tr("Use canvas extent"), menu)
        self.canvas_action.setToolTip(
            tr("Set this map item's extent to what the QGIS map canvas shows")
        )
        self.locate_action = QAction(tr("Locate on page"), menu)
        self.locate_action.setToolTip(tr("Select this map on the layout page"))
        menu.addAction(self.canvas_action)
        menu.addAction(self.locate_action)
        self.actions_button.setMenu(menu)
        head.addWidget(self.actions_button)
        layout.addLayout(head)

        second = QHBoxLayout()
        second.setSpacing(6)
        self.crs_label = QLabel(row.crs, self)
        self.crs_label.setProperty("role", "hint")
        second.addWidget(self.crs_label, 1)
        self.issues_button = QToolButton(self)
        self.issues_button.setProperty("role", "frameIssues")
        self.issues_button.setProperty("hasMenu", False)
        self.issues_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.issues_button.setToolTip(
            tr("Items on this map the cartograph changes or leaves out; see Checks")
        )
        second.addWidget(self.issues_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(second)

        # Only an Image has a resolution; the row is absent for Interactive.
        self.resolution_row = QWidget(self)
        resolution = QHBoxLayout(self.resolution_row)
        resolution.setContentsMargins(0, 0, 0, 0)
        resolution.setSpacing(6)
        self.resolution_label = QLabel(tr("Resolution"), self.resolution_row)
        self.resolution_label.setProperty("role", "hint")
        resolution.addWidget(self.resolution_label, 1)
        self.dpi_combo = _QuietCombo(self.resolution_row)
        # Wide enough for "300 dpi" whatever the style's arrow takes.
        self.dpi_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.dpi_combo.setToolTip(tr("Image resolution"))
        for dpi in dpi_choices:
            self.dpi_combo.addItem(tr("{dpi} dpi").format(dpi=dpi), dpi)
        resolution.addWidget(self.dpi_combo)
        layout.addWidget(self.resolution_row)

        # Choosing any control of an entry chooses its frame, by keyboard too.
        for widget in (
            self.mode_combo,
            self.actions_button,
            self.issues_button,
            self.dpi_combo,
        ):
            widget.installEventFilter(self)

        self.set_identity(row.name, row.crs)
        self.set_values(row)

    def set_identity(self, name: str, crs: str) -> None:
        """Show a renamed or reprojected map item; the controls are untouched."""
        self._name = name
        self.name_label.setText(name)
        self.name_label.setToolTip(name)
        self.crs_label.setText(crs)
        self.setAccessibleName(tr("Map frame {name}").format(name=name))
        self.mode_combo.setAccessibleName(tr("Map type of {name}").format(name=name))
        self.dpi_combo.setAccessibleName(
            tr("Image resolution of {name}").format(name=name)
        )
        self.actions_button.setAccessibleName(
            tr("More actions for {name}").format(name=name)
        )
        self._name_issues()

    def set_values(self, row: FrameRow) -> None:
        """Show `row`'s mode, DPI and issue count without reporting an edit."""
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
        """The Resolution row only for an Image."""
        self.resolution_row.setVisible(self.mode() == STATIC)

    def set_issue_count(self, issues: int | None) -> None:
        """An "N issues" badge, or nothing before the check or without any."""
        self._issues = issues
        if not issues:
            self.issues_button.setText("")
            self.issues_button.setVisible(False)
            return
        self.issues_button.setText(plural(issues, "{n} issue →", "{n} issues →"))
        self.issues_button.setVisible(True)
        self._name_issues()

    def _name_issues(self) -> None:
        """The badge says whose issues they are, for a reader who hears it."""
        if self._issues:
            self.issues_button.setAccessibleName(
                plural(
                    self._issues,
                    "{n} issue on {name}, show in Checks",
                    "{n} issues on {name}, show in Checks",
                    name=self._name,
                )
            )

    def set_marked(self, marked: bool) -> None:
        """Mark this entry as the frame selected on the page."""
        if self.property("selected") == marked:
            return
        self.setProperty("selected", marked)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        self.selected.emit(self.item_id)
        super().mousePressEvent(event)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if event is not None and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        ):
            self.selected.emit(self.item_id)
        return False


class FramesList(QWidget):
    """A short help line, then an entry per map item, or a note that there are
    none."""

    mode_changed = pyqtSignal(str, str)  # item_id, LIVE / STATIC
    dpi_changed = pyqtSignal(str, int)  # item_id, dpi
    match_canvas_requested = pyqtSignal(str)  # item_id
    locate_requested = pyqtSignal(str)  # item_id
    frame_selected = pyqtSignal(str)  # item_id
    issues_requested = pyqtSignal(str)  # item_id

    def __init__(
        self, dpi_choices: Sequence[int], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._dpi_choices = tuple(dpi_choices)
        self._cards: dict[str, FrameCard] = {}
        self._has_layout = False
        self._canvas_available = True
        self._marked: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.help_button = QPushButton(tr("How do these work?"), self)
        self.help_button.setCheckable(True)
        self.help_button.setProperty("role", "checksMore")
        self.help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        layout.addWidget(self.help_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.intro = FittedLabel(
            tr(
                "Interactive maps are panned and zoomed in the browser and "
                "need a CRS the runtime can draw. Images are exported as a "
                "picture at the resolution you choose."
            ),
            self,
        )
        self.intro.setWordWrap(True)
        self.intro.setProperty("role", "hint")
        self.intro.setVisible(False)
        self.help_button.toggled.connect(self.intro.setVisible)
        layout.addWidget(self.intro)

        self._cards_box = QVBoxLayout()
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(2)
        layout.addLayout(self._cards_box)

        self.empty_note = FittedLabel(tr("This layout has no maps on it yet."), self)
        self.empty_note.setWordWrap(True)
        self.empty_note.setVisible(False)
        layout.addWidget(self.empty_note)

    def set_frames(self, rows: list[FrameRow] | None) -> None:
        """Replace the entries. None is no layout at all; [] a layout with no maps."""
        for card in self._cards.values():
            self._cards_box.removeWidget(card)
            # Hidden and left with its parent until Qt deletes it: taking the
            # parent away hands it to Python, which could free it before the
            # deferred delete runs - a crash on the next event loop turn.
            card.hide()
            card.deleteLater()
        self._cards.clear()
        self._has_layout = rows is not None
        self.empty_note.setVisible(rows is not None and not rows)
        for row in rows or ():
            card = FrameCard(row, self._dpi_choices, self)
            card.canvas_action.setEnabled(self._canvas_available)
            self._connect(card)
            self._cards_box.addWidget(card)
            self._cards[row.item_id] = card
        self.set_selected(self._marked)

    def update_identity(self, identities: dict[str, tuple[str, str]]) -> None:
        """Follow a rename or CRS change of maps already listed, in place.

        The entries, and so whichever control has focus, stay; a map not
        listed is ignored (`set_frames` is for a changed set).
        """
        for item_id, (name, crs) in identities.items():
            card = self._cards.get(item_id)
            if card is not None:
                card.set_identity(name, crs)

    def _connect(self, card: FrameCard) -> None:
        card.mode_combo.currentIndexChanged.connect(
            lambda _index, card=card: self._on_mode(card)
        )
        card.dpi_combo.currentIndexChanged.connect(
            lambda _index, card=card: self._on_dpi(card)
        )
        card.canvas_action.triggered.connect(
            lambda _checked=False, card=card: self._on_match(card)
        )
        card.locate_action.triggered.connect(
            lambda _checked=False, card=card: self._on_locate(card)
        )
        card.issues_button.clicked.connect(
            lambda _checked=False, card=card: self._on_issues(card)
        )
        card.selected.connect(lambda _id, card=card: self._on_selected(card))

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

    def _on_locate(self, card: FrameCard) -> None:
        if self._is_current(card):
            self.locate_requested.emit(card.item_id)

    def _on_issues(self, card: FrameCard) -> None:
        if self._is_current(card):
            self.issues_requested.emit(card.item_id)

    def _on_selected(self, card: FrameCard) -> None:
        if self._is_current(card) and self._marked != card.item_id:
            self.set_selected(card.item_id)
            self.frame_selected.emit(card.item_id)

    def set_selected(self, item_id: str | None) -> None:
        """Mark the entry of the frame selected on the page; never reports."""
        self._marked = item_id
        for card_id, card in self._cards.items():
            card.set_marked(card_id == item_id)

    def set_issue_count(self, item_id: str, issues: int | None) -> None:
        card = self._cards.get(item_id)
        if card is not None:
            card.set_issue_count(issues)

    def set_canvas_available(self, available: bool) -> None:
        """ "Use canvas extent" needs a QGIS map canvas to read the extent from."""
        self._canvas_available = available
        for card in self._cards.values():
            card.canvas_action.setEnabled(available)

    def card(self, item_id: str) -> FrameCard:
        return self._cards[item_id]

    def summary(self) -> str:
        """A few words for the closed section: "2 interactive maps, 1 image"."""
        if not self._has_layout:
            return ""
        if not self._cards:
            return tr("No maps")
        modes = [card.mode() for card in self._cards.values()]
        live = modes.count(LIVE)
        static = len(modes) - live
        live_text = plural(live, "{n} interactive map", "{n} interactive maps")
        static_text = plural(static, "{n} image", "{n} images")
        if live and static:
            return tr("{live}, {static}").format(live=live_text, static=static_text)
        return live_text if live else static_text
