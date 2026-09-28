"""The Fidelity tab, the strip under every tab, and the warning before export.

What users said they valued in the old tab, and what this keeps: being told
the truth - nothing that changes is hidden, and Kept stays one click away -
the detail of every item, and a line on every tab saying how many things
change. What it fixes: a flat three-column table of fifty rows where the one
that mattered was somewhere in the middle, details cut off at 110 characters,
the same sentence once per class, and a report that went on looking current
after the settings under it had changed.

**Layout.** A summary line, then a tree grouped by layer with the problems
first - `core.fidelity_summary` does the grouping and counting, so everything
said here is unit-tested without Qt. Layers that came through exactly are one
line each. Kept rows sit behind a "Show what is kept" toggle. Selecting a row
fills a detail pane with the whole sentence, wrapped, and where the fix lives
in this dialog, a button that goes there.

**Colour.** Taken from the palette and from QGIS's own theme icons, never
hardcoded, so the tab reads in light and dark themes alike. Qt's palette has
no warning or error role, so severity is carried by the theme's warning and
critical icons beside full-strength text, and only neutral states use the
dimmer placeholder colour.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from enum import Enum

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QIcon, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_ir import FidelityItem, FidelityStatus
from ..core.fidelity_summary import (
    ReportGroup,
    ReportRow,
    ReportSummary,
    StripMessage,
    Tone,
)

# QGIS theme icons, by resource name. Verified present in QGIS 4.2's compiled
# theme; a name that has gone yields a null icon rather than raising, so a row
# keeps its words and loses only its picture.
TONE_ICONS = {
    Tone.INFO: "mIconInfo.svg",
    Tone.WARNING: "mIconWarning.svg",
    Tone.ERROR: "mIconCritical.svg",
}
VERDICT_ICONS = {
    FidelityStatus.BLOCKED: "mIconCritical.svg",
    FidelityStatus.UNSUPPORTED: "mIconWarning.svg",
    FidelityStatus.APPROXIMATED: "mIconInfo.svg",
    FidelityStatus.RASTER_FALLBACK: "mIconInfo.svg",
    FidelityStatus.PRESERVED: "mIconSuccess.svg",
}

# Whole-map rows whose setting lives on the Map tab, by report subject.
MAP_TAB_SUBJECTS = frozenset(
    {
        "Basemap",
        "Terrain",
        "Relief",
        "Map extent",
        "Map title",
        "Clip to the current view",
        "Coordinate precision",
    }
)
# Whole-map rows about which layers are in the map, which is the Layers tab.
LAYERS_TAB_SUBJECTS = frozenset({"Layer count", "Project layers"})

ICON_SIZE = 16

# The detail pane's share of the width. The tree needs the room for names, the
# pane for sentences; two to one keeps both readable at the dialog's minimum.
TREE_STRETCH = 3
DETAIL_STRETCH = 2

EXACT_TEXT = "✓ exact"
SELECT_HINT = "Select a row to read the whole of it here."

ROW_ROLE = Qt.ItemDataRole.UserRole


def theme_icon(name: str) -> QIcon:
    """A QGIS theme icon, or an empty one if this build has not got it."""
    from qgis.core import QgsApplication

    with contextlib.suppress(Exception):
        return QgsApplication.getThemeIcon(f"/{name}")
    return QIcon()


def jump_for(group: ReportGroup, row: ReportRow | None) -> tuple[str, str] | None:
    """Where in this dialog the fix for a row lives: `(kind, target)`.

    `("layer", layer_id)` opens the layer on the Layers tab; `("tab", name)`
    opens a tab. `None` when the fix is outside the dialog - a project's
    description is set in Project Properties, not here.
    """
    if group.layer_id is not None:
        return ("layer", group.layer_id)
    if row is None:
        return None
    subject = row.subjects[0] if row.subjects else row.label
    if subject in MAP_TAB_SUBJECTS:
        return ("tab", "Map")
    if subject in LAYERS_TAB_SUBJECTS:
        return ("tab", "Layers")
    return None


def jump_label(jump: tuple[str, str]) -> str:
    kind, target = jump
    return "Show in Layers" if kind == "layer" else f"Go to {target} tab"


# PyQt ships no stubs, so QWidget is `Any` to mypy; the ignore is that boundary.
class FidelityPanel(QWidget):  # type: ignore[misc, unused-ignore]
    """The Fidelity tab's page. Knows nothing about reading a project.

    The dialog decides when to check and hands over a `ReportSummary`; this
    only shows it. `check_requested` asks for a fresh check, and the two
    `..._requested` jumps ask the dialog to go somewhere.
    """

    check_requested = pyqtSignal()
    show_layer_requested = pyqtSignal(str)
    show_tab_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary: ReportSummary | None = None
        self._jump: tuple[str, str] | None = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "What survives the export, and what does not - checked before you "
            "export, so nothing is left to discover later.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top = QHBoxLayout()
        self.headline = QLabel("Not checked yet.", self)
        self.headline.setWordWrap(True)
        font = QFont(self.headline.font())
        font.setBold(True)
        self.headline.setFont(font)
        top.addWidget(self.headline, 1)
        self.check_button = QPushButton("Check now", self)
        self.check_button.clicked.connect(self.check_requested.emit)
        top.addWidget(self.check_button)
        layout.addLayout(top)

        # Said above the rows, not instead of them: the old report is still the
        # best description there is until the new one lands, so it stays
        # readable - it just stops claiming to be current.
        self.stale_banner = QWidget(self)
        banner = QHBoxLayout(self.stale_banner)
        banner.setContentsMargins(0, 0, 0, 0)
        self.stale_icon = QLabel(self.stale_banner)
        self.stale_icon.setPixmap(
            theme_icon(TONE_ICONS[Tone.WARNING]).pixmap(QSize(ICON_SIZE, ICON_SIZE))
        )
        banner.addWidget(self.stale_icon)
        self.stale_text = QLabel(
            "Out of date - something changed since this check, so the rows "
            "below describe the map as it was.",
            self.stale_banner,
        )
        self.stale_text.setWordWrap(True)
        banner.addWidget(self.stale_text, 1)
        self.stale_banner.setVisible(False)
        layout.addWidget(self.stale_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget(left)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["What", "Result"])
        self.tree.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        # The names take the width and the verdict sizes to its words: eliding
        # "Symbology of 'al..." is what made the old table unreadable on
        # similarly named layers, and a layer group now says the name once.
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        left_layout.addWidget(self.tree, 1)

        self.kept_toggle = QCheckBox("Show what is kept", left)
        self.kept_toggle.setToolTip(
            "Kept rows are hidden by default so the changes stand out. Nothing "
            "is left out of the report."
        )
        self.kept_toggle.toggled.connect(self._rebuild)
        left_layout.addWidget(self.kept_toggle)
        splitter.addWidget(left)

        splitter.addWidget(self._build_detail_pane(splitter))
        splitter.setStretchFactor(0, TREE_STRETCH)
        splitter.setStretchFactor(1, DETAIL_STRETCH)
        layout.addWidget(splitter, 1)

        self.show_not_checked()

    def _build_detail_pane(self, parent: QWidget) -> QWidget:
        """Full sentences, wrapped. The old table cut them at 110 characters."""
        area = QScrollArea(parent)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        pane = QWidget(area)
        layout = QVBoxLayout(pane)

        self.detail_title = QLabel("", pane)
        self.detail_title.setWordWrap(True)
        font = QFont(self.detail_title.font())
        font.setBold(True)
        self.detail_title.setFont(font)
        layout.addWidget(self.detail_title)

        verdict_row = QHBoxLayout()
        self.detail_icon = QLabel(pane)
        verdict_row.addWidget(self.detail_icon)
        self.detail_verdict = QLabel("", pane)
        self.detail_verdict.setWordWrap(True)
        verdict_row.addWidget(self.detail_verdict, 1)
        layout.addLayout(verdict_row)

        self.detail_text = QLabel(SELECT_HINT, pane)
        self.detail_text.setWordWrap(True)
        self.detail_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_text.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.detail_text)

        self.jump_button = QPushButton("", pane)
        self.jump_button.clicked.connect(self._on_jump)
        self.jump_button.setVisible(False)
        layout.addWidget(self.jump_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        area.setWidget(pane)
        area.setMinimumWidth(200)
        return area

    # ---- states ----------------------------------------------------------

    def show_not_checked(self) -> None:
        self._summary = None
        self._set_headline("Not checked yet.", quiet=True)
        self._show_button("Check now")
        self.stale_banner.setVisible(False)
        self.tree.clear()
        self._placeholder_row(
            "Not checked yet", "Opening this tab reads the project to check."
        )
        self._show_detail("", None, SELECT_HINT, None)

    def show_checking(self) -> None:
        """A placeholder rather than an empty table: "empty" reads as "nothing
        to report", the exact inversion this tab exists to prevent."""
        self._set_headline("Checking what the export changes...", quiet=True)
        self._show_button(None)
        if self._summary is None:
            self.tree.clear()
            self._placeholder_row(
                "Checking...", "Reading the project to see what the export changes."
            )

    def show_error(self, message: str) -> None:
        """Why the check did not finish. Never a verdict: a crash in the report
        says nothing about the map, and showing it as "Blocked" said it did."""
        self._summary = None
        self._set_headline("The check could not finish.", quiet=False)
        self._show_button("Try again")
        self.stale_banner.setVisible(False)
        self.tree.clear()
        self._placeholder_row("Check failed", "See the detail for why.")
        self._show_detail(
            "The check could not finish",
            Tone.ERROR,
            f"The project could not be read: {message}\n\nThe full error is in "
            "the QGIS message log. Nothing about the map is known until a "
            "check finishes, so none of it is shown as a verdict.",
            None,
            verdict_text="Not a verdict on the map",
        )

    def show_summary(self, summary: ReportSummary) -> None:
        self._summary = summary
        self._set_headline(summary.headline(), quiet=not summary.change_count)
        self._show_button(None)
        self.stale_banner.setVisible(False)
        self.kept_toggle.setText(f"Show what is kept ({summary.kept_count})")
        self._rebuild()

    def set_out_of_date(self, stale: bool) -> None:
        """Mark the report on screen as describing a project that has moved."""
        if self._summary is None:
            return
        self.stale_banner.setVisible(stale)
        self._show_button("Check again" if stale else None)

    # ---- tree ------------------------------------------------------------

    def _rebuild(self, *_args: object) -> None:
        summary = self._summary
        if summary is None:
            return
        show_kept = self.kept_toggle.isChecked()
        self.tree.clear()
        first_problem: QTreeWidgetItem | None = None

        for group_index, group in enumerate(summary.groups):
            rows = group.rows if show_kept else group.changes
            if group.is_whole_map and not rows:
                continue
            parent = QTreeWidgetItem(self.tree)
            parent.setText(0, group.title)
            parent.setText(1, EXACT_TEXT if group.is_exact else group.tally())
            parent.setIcon(0, theme_icon(VERDICT_ICONS[group.worst]))
            parent.setData(0, ROW_ROLE, (group_index, -1))
            parent.setToolTip(0, group.title)
            for row_index, row in enumerate(group.rows):
                if row.is_kept and not show_kept:
                    continue
                child = QTreeWidgetItem(parent)
                child.setText(0, row.label)
                child.setText(1, row.verdict)
                child.setIcon(0, theme_icon(VERDICT_ICONS[row.status]))
                child.setToolTip(0, row.label)
                child.setData(0, ROW_ROLE, (group_index, row_index))
                if first_problem is None and not row.is_kept:
                    first_problem = child
            parent.setExpanded(not group.is_exact or show_kept)

        # Open on the first problem, so the pane is never blank when there is
        # something to read.
        target = first_problem or self.tree.topLevelItem(0)
        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self._show_detail("", None, SELECT_HINT, None)

    def _placeholder_row(self, text: str, detail: str) -> None:
        item = QTreeWidgetItem(self.tree)
        item.setText(0, text)
        item.setText(1, "")
        item.setToolTip(0, detail)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        summary = self._summary
        if current is None or summary is None:
            return
        where = current.data(0, ROW_ROLE)
        if not where:
            return
        group_index, row_index = where
        group = summary.groups[group_index]
        if row_index < 0:
            text = (
                "Nothing about this layer changes on the way out."
                if group.is_exact
                else SELECT_HINT
            )
            tone = Tone.QUIET if group.is_exact else _tone_of(group.worst)
            verdict = EXACT_TEXT if group.is_exact else group.tally()
            self._show_detail(
                group.title, tone, text, jump_for(group, None), verdict_text=verdict
            )
            return
        row = group.rows[row_index]
        title = row.label if group.is_whole_map else f"{group.title}: {row.label}"
        text = row.detail
        if len(row.subjects) > 1:
            text += "\n\nApplies to: " + "; ".join(row.subjects) + "."
        self._show_detail(
            title,
            _tone_of(row.status),
            text,
            jump_for(group, row),
            verdict_text=row.verdict,
            icon=VERDICT_ICONS[row.status],
        )

    # ---- detail pane -----------------------------------------------------

    def _show_detail(
        self,
        title: str,
        tone: Tone | None,
        text: str,
        jump: tuple[str, str] | None,
        verdict_text: str = "",
        icon: str | None = None,
    ) -> None:
        self.detail_title.setText(title)
        self.detail_title.setVisible(bool(title))
        name = icon or (TONE_ICONS.get(tone) if tone is not None else None)
        self.detail_icon.setPixmap(
            theme_icon(name).pixmap(QSize(ICON_SIZE, ICON_SIZE)) if name else _empty()
        )
        self.detail_icon.setVisible(bool(name))
        self.detail_verdict.setText(verdict_text)
        self.detail_verdict.setVisible(bool(verdict_text))
        self.detail_text.setText(text)
        self._jump = jump
        self.jump_button.setVisible(jump is not None)
        if jump is not None:
            self.jump_button.setText(jump_label(jump))

    def _on_jump(self) -> None:
        if self._jump is None:
            return
        kind, target = self._jump
        if kind == "layer":
            self.show_layer_requested.emit(target)
        else:
            self.show_tab_requested.emit(target)

    # ---- helpers ---------------------------------------------------------

    def _set_headline(self, text: str, quiet: bool) -> None:
        self.headline.setText(text)
        self.headline.setForegroundRole(
            QPalette.ColorRole.PlaceholderText
            if quiet
            else QPalette.ColorRole.WindowText
        )

    def _show_button(self, label: str | None) -> None:
        self.check_button.setVisible(label is not None)
        if label is not None:
            self.check_button.setText(label)


# PyQt ships no stubs, so QWidget is `Any` to mypy; the ignore is that boundary.
class FidelityStrip(QWidget):  # type: ignore[misc, unused-ignore]
    """The line between the tabs and the buttons, visible from every tab.

    This plugin's one real advantage is telling you what your recipient loses,
    and a tab is read after the decision it should have informed, if at all -
    so the count lives here, always on screen.
    """

    button_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel(self)
        row.addWidget(self.icon)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.button = QPushButton("", self)
        self.button.setFlat(True)
        self.button.clicked.connect(self.button_clicked.emit)
        row.addWidget(self.button)
        self.message: StripMessage | None = None
        self.show_message(StripMessage("", Tone.QUIET, None))

    def show_message(self, message: StripMessage) -> None:
        # Polled twice a second; repainting an unchanged line would be waste.
        if message == self.message:
            return
        self.message = message
        self.label.setText(message.text)
        # Full-strength text for anything worth reading, the placeholder colour
        # only for neutral states: the old strip printed "2 layers cannot be
        # exported" in the dimmest colour the palette has.
        self.label.setForegroundRole(
            QPalette.ColorRole.PlaceholderText
            if message.tone is Tone.QUIET
            else QPalette.ColorRole.WindowText
        )
        name = TONE_ICONS.get(message.tone)
        self.icon.setPixmap(
            theme_icon(name).pixmap(QSize(ICON_SIZE, ICON_SIZE)) if name else _empty()
        )
        self.icon.setVisible(name is not None)
        self.button.setVisible(message.button is not None)
        if message.button is not None:
            self.button.setText(message.button)


class Proceed(Enum):
    """The three answers to "export anyway?"."""

    CONTINUE = "continue"
    REVIEW = "review"
    CANCEL = "cancel"


def problems_box(
    parent: QWidget | None, items: Sequence[FidelityItem], action: str
) -> tuple[QMessageBox, dict[QAbstractButton, Proceed]]:
    """The warning shown before exporting or publishing with Blocked items.

    Built separately from `ask_before` so a test can read what it says without
    a modal loop. `action` is the verb on the button - "Export", "Publish".
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Part of this map will not work")
    count = len(items)
    box.setText(
        f"{count} thing{'' if count == 1 else 's'} cannot be carried into the "
        "map as set up in QGIS:\n\n"
        + "\n".join(f"- {item.subject}: {item.detail}" for item in items)
    )
    box.setInformativeText(
        f"You can {action.lower()} anyway and the map is made without "
        f"{'it' if count == 1 else 'them'}, or review the report first."
    )
    anyway = box.addButton(f"{action} anyway", QMessageBox.ButtonRole.AcceptRole)
    review = box.addButton(
        "Review on the Fidelity tab", QMessageBox.ButtonRole.ActionRole
    )
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(review)
    return box, {
        anyway: Proceed.CONTINUE,
        review: Proceed.REVIEW,
        cancel: Proceed.CANCEL,
    }


def ask_before(
    parent: QWidget | None, items: Sequence[FidelityItem], action: str
) -> Proceed:
    """Warn about Blocked items and let the user decide. CONTINUE when none."""
    if not items:
        return Proceed.CONTINUE
    box, choices = problems_box(parent, items, action)
    box.exec()
    clicked = box.clickedButton()
    return (
        choices.get(clicked, Proceed.CANCEL) if clicked is not None else Proceed.CANCEL
    )


def _tone_of(status: FidelityStatus) -> Tone:
    if status is FidelityStatus.BLOCKED:
        return Tone.ERROR
    if status is FidelityStatus.UNSUPPORTED:
        return Tone.WARNING
    if status is FidelityStatus.PRESERVED:
        return Tone.QUIET
    return Tone.INFO


def _empty() -> QPixmap:
    return QPixmap()
