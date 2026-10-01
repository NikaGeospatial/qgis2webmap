"""The Fidelity tab, the strip under every tab, and the warning before export.

What users said they valued in the old tab, and what this keeps: being told
the truth - nothing that changes is hidden, and Kept stays one click away -
the detail of every item, and a line on every tab saying how many things
change. What it fixes: a flat three-column table of fifty rows where the one
that mattered was somewhere in the middle, details cut off at 110 characters,
the same sentence once per class, and a report that went on looking current
after the settings under it had changed.

**Layout.** A summary line and a line of facts about the whole map, then a
tree grouped by layer with the problems first - `core.fidelity_summary` does
the grouping, counting and filtering, so everything said here is unit-tested
without Qt. Each row carries its whole sentence, wrapped, in a Detail column;
each layer's own line carries what the export knows about it
(`core.report_facts`); a merged "(5 classes)" row opens into its classes.
Checkboxes for each verdict and each topic, and a search box, narrow the list -
Kept is off by default - and the choice is remembered per machine. Layers that
came through exactly are one line each. Selecting a row fills a pane below the
list with its name, the subjects behind it and, where the fix lives in this
dialog, a button that goes there.

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

from qgis.PyQt.QtCore import QModelIndex, QRect, QSettings, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QFont, QIcon, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_ir import FidelityItem, FidelityStatus
from ..core.fidelity_summary import (
    VERDICT_LABELS,
    ReportFilter,
    ReportGroup,
    ReportRow,
    ReportSummary,
    StripMessage,
    Tone,
    Topic,
)
from ..core.report_facts import ReportFacts, facts_line, facts_lines

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

# The detail pane's share of the height. The sentences are in the list now, so
# the pane below it needs only a few lines.
TREE_STRETCH = 5
DETAIL_STRETCH = 1
DETAIL_MIN_HEIGHT = 60
SEARCH_WIDTH = 220

EXACT_TEXT = "✓ exact"
SELECT_HINT = "Select a row to see where it applies and where to change it."
FILTER_HINT = "Tick more of the boxes above, or clear the search."
SAME_AS_ABOVE = "As above."

# Filter choices, one QSettings key each under this prefix.
FILTER_KEY = "qgis2webmap/fidelityFilter/"

# The verdict checkboxes, worst first, the order the rows are in.
FILTER_VERDICTS = (
    FidelityStatus.BLOCKED,
    FidelityStatus.UNSUPPORTED,
    FidelityStatus.APPROXIMATED,
    FidelityStatus.RASTER_FALLBACK,
    FidelityStatus.PRESERVED,
)
VERDICT_TIPS = {
    FidelityStatus.BLOCKED: "Cannot be carried into the map at all.",
    FidelityStatus.UNSUPPORTED: "Cannot be represented, and was left out.",
    FidelityStatus.APPROXIMATED: "Exported, but approximated - the detail says how.",
    FidelityStatus.RASTER_FALLBACK: "Drawn into the map as an image.",
    FidelityStatus.PRESERVED: (
        "Survives exactly as set in QGIS. Hidden by default so the changes "
        "stand out; nothing is left out of the report."
    ),
}

ROW_ROLE = Qt.ItemDataRole.UserRole
DETAIL_COLUMN = 2


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

    The dialog decides when to check and hands over a `ReportSummary`, with
    the facts it knows about each layer; this only shows them.
    `check_requested` asks for a fresh check, and the two `..._requested`
    jumps ask the dialog to go somewhere.
    """

    check_requested = pyqtSignal()
    show_layer_requested = pyqtSignal(str)
    show_tab_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary: ReportSummary | None = None
        self._facts = ReportFacts()
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

        # The whole map in one line: what the map is, not what changes in it.
        self.map_facts = QLabel("", self)
        self.map_facts.setWordWrap(True)
        self.map_facts.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.map_facts.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.map_facts.setVisible(False)
        layout.addWidget(self.map_facts)

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

        layout.addLayout(self._build_filters())

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)

        self.tree = QTreeWidget(splitter)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["What", "Result", "Detail"])
        self.tree.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        # Each row's whole sentence, wrapped, in the list itself: the old table
        # cut it at 110 characters, and keeping it only in a side pane meant
        # reading the report one click at a time.
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setItemDelegate(_WrappingDelegate(self.tree))
        # Names and verdicts size to their words - eliding "Symbology of
        # 'al..." is what made the old table unreadable on similarly named
        # layers - and the sentence takes the rest of the width.
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(True)
        # Row heights are measured once, against the widths at that moment; a
        # wrapped sentence needs measuring again whenever its column changes.
        header.sectionResized.connect(self._on_column_resized)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        splitter.addWidget(self.tree)

        splitter.addWidget(self._build_detail_pane(splitter))
        splitter.setStretchFactor(0, TREE_STRETCH)
        splitter.setStretchFactor(1, DETAIL_STRETCH)
        layout.addWidget(splitter, 1)

        self.show_not_checked()

    def _build_filters(self) -> QVBoxLayout:
        """Verdict and topic checkboxes and a search box, remembered per machine.

        Kept is one of the verdicts, off by default so the changes stand out;
        it replaces the old "Show what is kept" toggle. Hiding a verdict or a
        topic folds rows away, it never drops them from the report - the
        headline and the strip go on counting everything.
        """
        settings = QSettings()
        rows = QVBoxLayout()
        rows.setSpacing(2)

        verdict_row = QHBoxLayout()
        verdict_row.addWidget(QLabel("Show:", self))
        self.verdict_checks: dict[FidelityStatus, QCheckBox] = {}
        for status in FILTER_VERDICTS:
            check = QCheckBox(VERDICT_LABELS[status], self)
            check.setChecked(
                _setting_bool(
                    settings,
                    FILTER_KEY + status.value,
                    status is not FidelityStatus.PRESERVED,
                )
            )
            check.setToolTip(VERDICT_TIPS[status])
            check.toggled.connect(
                lambda on, key=FILTER_KEY + status.value: self._on_filter(key, on)
            )
            verdict_row.addWidget(check)
            self.verdict_checks[status] = check
        # The old name, for the one toggle that existed before the rest.
        self.kept_toggle = self.verdict_checks[FidelityStatus.PRESERVED]
        verdict_row.addStretch(1)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search the report")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(SEARCH_WIDTH)
        self.search.textChanged.connect(self._rebuild)
        verdict_row.addWidget(self.search)
        rows.addLayout(verdict_row)

        topic_row = QHBoxLayout()
        topic_row.addWidget(QLabel("About:", self))
        self.topic_checks: dict[Topic, QCheckBox] = {}
        for topic in Topic:
            key = FILTER_KEY + "topic-" + topic.name.lower()
            check = QCheckBox(_button_text(topic.value), self)
            check.setChecked(_setting_bool(settings, key, True))
            check.toggled.connect(lambda on, key=key: self._on_filter(key, on))
            topic_row.addWidget(check)
            self.topic_checks[topic] = check
        topic_row.addStretch(1)
        rows.addLayout(topic_row)
        return rows

    def _build_detail_pane(self, parent: QWidget) -> QWidget:
        """The selected row in full, with where its fix lives in this dialog.

        Below the list rather than beside it: the sentence is in the list now,
        so the pane no longer needs half the width - only room for the row's
        name, its classes and the button that goes to the fix.
        """
        area = QScrollArea(parent)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        pane = QWidget(area)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 4, 0, 0)

        heading = QHBoxLayout()
        self.detail_icon = QLabel(pane)
        heading.addWidget(self.detail_icon)
        self.detail_title = QLabel("", pane)
        self.detail_title.setWordWrap(True)
        font = QFont(self.detail_title.font())
        font.setBold(True)
        self.detail_title.setFont(font)
        heading.addWidget(self.detail_title)
        self.detail_verdict = QLabel("", pane)
        self.detail_verdict.setWordWrap(True)
        heading.addWidget(self.detail_verdict, 1)
        self.jump_button = QPushButton("", pane)
        self.jump_button.clicked.connect(self._on_jump)
        self.jump_button.setVisible(False)
        heading.addWidget(self.jump_button)
        layout.addLayout(heading)

        self.detail_text = QLabel(SELECT_HINT, pane)
        self.detail_text.setWordWrap(True)
        self.detail_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_text.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.detail_text)
        layout.addStretch(1)

        area.setWidget(pane)
        area.setMinimumHeight(DETAIL_MIN_HEIGHT)
        return area

    # ---- states ----------------------------------------------------------

    def show_not_checked(self) -> None:
        self._summary = None
        self._set_headline("Not checked yet.", quiet=True)
        self._show_button("Check now")
        self.stale_banner.setVisible(False)
        self.map_facts.setVisible(False)
        self._update_filter_counts()
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
        self.map_facts.setVisible(False)
        self._update_filter_counts()
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

    def show_summary(
        self, summary: ReportSummary, facts: ReportFacts | None = None
    ) -> None:
        """Show a report, with what the export knows about each layer if given."""
        self._summary = summary
        self._facts = facts or ReportFacts()
        self._set_headline(summary.headline(), quiet=not summary.change_count)
        self._show_button(None)
        self.stale_banner.setVisible(False)
        map_line = facts_line(self._facts.map)
        self.map_facts.setText(f"Map: {map_line}" if map_line else "")
        self.map_facts.setToolTip(facts_lines(self._facts.map))
        self.map_facts.setVisible(bool(map_line))
        self._update_filter_counts()
        self._rebuild()

    def set_out_of_date(self, stale: bool) -> None:
        """Mark the report on screen as describing a project that has moved."""
        if self._summary is None:
            return
        self.stale_banner.setVisible(stale)
        self._show_button("Check again" if stale else None)

    # ---- filters ---------------------------------------------------------

    def report_filter(self) -> ReportFilter:
        """What the checkboxes and the search box currently ask for."""
        return ReportFilter(
            verdicts=frozenset(
                status
                for status, check in self.verdict_checks.items()
                if check.isChecked()
            ),
            topics=frozenset(
                topic for topic, check in self.topic_checks.items() if check.isChecked()
            ),
            text=self.search.text(),
        )

    def _on_filter(self, key: str, on: bool) -> None:
        with contextlib.suppress(Exception):
            QSettings().setValue(key, on)
        self._update_filter_counts()
        self._rebuild()

    def _update_filter_counts(self) -> None:
        """Each checkbox says how many rows it stands for.

        Verdicts count the whole report; topics count the rows the ticked
        verdicts show, so a topic's number is what ticking it would add.
        """
        summary = self._summary
        if summary is None:
            for status, check in self.verdict_checks.items():
                check.setText(VERDICT_LABELS[status])
            for topic, check in self.topic_checks.items():
                check.setText(_button_text(topic.value))
            return
        verdicts = summary.verdict_counts()
        for status, check in self.verdict_checks.items():
            check.setText(f"{VERDICT_LABELS[status]} ({verdicts[status]})")
        topics = summary.topic_counts(self.report_filter().verdicts)
        everything = summary.topic_counts()
        for topic, check in self.topic_checks.items():
            check.setText(_button_text(f"{topic.value} ({topics[topic]})"))
        # Other is a fallback for a subject nobody sorted; a box for nothing
        # only raises the question of what it might be.
        self.topic_checks[Topic.OTHER].setVisible(bool(everything[Topic.OTHER]))

    # ---- tree ------------------------------------------------------------

    def _rebuild(self, *_args: object) -> None:
        summary = self._summary
        if summary is None:
            return
        report_filter = self.report_filter()
        self.tree.clear()
        first_problem: QTreeWidgetItem | None = None
        quiet = QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText))

        for group_index, group in enumerate(summary.groups):
            facts = self._facts.for_layer(group.layer_id)
            group_text = f"{group.title} {facts_line(facts)}"
            if not report_filter.shows_group(group, group_text):
                continue
            rows = report_filter.rows(group, group_text)
            shown = {id(row) for row in rows}
            parent = QTreeWidgetItem(self.tree)
            parent.setText(0, group.title)
            parent.setText(1, EXACT_TEXT if group.is_exact else group.tally())
            parent.setText(2, facts_line(facts))
            parent.setForeground(2, quiet)
            parent.setIcon(0, theme_icon(VERDICT_ICONS[group.worst]))
            parent.setData(0, ROW_ROLE, (group_index, -1, -1))
            parent.setToolTip(0, group.title)
            if facts:
                parent.setToolTip(2, facts_lines(facts))
            _align_top(parent)
            for row_index, row in enumerate(group.rows):
                if id(row) not in shown:
                    continue
                child = QTreeWidgetItem(parent)
                child.setText(0, row.label)
                child.setText(1, row.verdict)
                child.setText(2, row.detail)
                child.setIcon(0, theme_icon(VERDICT_ICONS[row.status]))
                child.setToolTip(0, row.label)
                child.setData(0, ROW_ROLE, (group_index, row_index, -1))
                _align_top(child)
                if row.is_merged:
                    self._add_merged_items(child, group_index, row_index, row)
                if first_problem is None and not row.is_kept:
                    first_problem = child
            parent.setExpanded(bool(rows))

        if self.tree.topLevelItemCount() == 0:
            self._placeholder_row(
                "Nothing matches these filters",
                "The report has rows the filters above hide. Tick more of them, "
                "or clear the search.",
            )
            self._show_detail(
                "", None, "Nothing matches these filters. " + FILTER_HINT, None
            )
            return

        # Open on the first problem, so the pane is never blank when there is
        # something to read.
        target = first_problem or self.tree.topLevelItem(0)
        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self._show_detail("", None, SELECT_HINT, None)

    def _add_merged_items(
        self,
        row_item: QTreeWidgetItem,
        group_index: int,
        row_index: int,
        row: ReportRow,
    ) -> None:
        """One line per item behind a merged row, folded under it.

        "(5 classes)" says how many; expanding it says which, so no item of
        the report is reachable only as a number.
        """
        labels = row.item_labels or row.subjects
        for item_index, (label, subject) in enumerate(zip(labels, row.subjects)):
            item = QTreeWidgetItem(row_item)
            item.setText(0, label)
            item.setText(1, row.verdict)
            item.setText(2, SAME_AS_ABOVE)
            item.setIcon(0, theme_icon(VERDICT_ICONS[row.status]))
            item.setForeground(
                2, QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText))
            )
            item.setToolTip(0, subject)
            item.setData(0, ROW_ROLE, (group_index, row_index, item_index))
            _align_top(item)
        row_item.setExpanded(False)

    def _on_column_resized(self, column: int, _old: int, _new: int) -> None:
        if column == DETAIL_COLUMN:
            self.tree.scheduleDelayedItemsLayout()

    def _placeholder_row(self, text: str, detail: str) -> None:
        item = QTreeWidgetItem(self.tree)
        item.setText(0, text)
        item.setText(1, "")
        item.setText(2, detail)
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
        group_index, row_index, item_index = where
        group = summary.groups[group_index]
        if row_index < 0:
            facts = self._facts.for_layer(group.layer_id)
            text = (
                "Nothing about this layer changes on the way out."
                if group.is_exact
                else SELECT_HINT
            )
            if facts:
                text += "\n\n" + facts_lines(facts)
            tone = Tone.QUIET if group.is_exact else _tone_of(group.worst)
            verdict = EXACT_TEXT if group.is_exact else group.tally()
            self._show_detail(
                group.title, tone, text, jump_for(group, None), verdict_text=verdict
            )
            return
        row = group.rows[row_index]
        label = row.label
        if item_index >= 0 and item_index < len(row.subjects):
            labels = row.item_labels or row.subjects
            label = labels[item_index]
        title = label if group.is_whole_map else f"{group.title}: {label}"
        text = row.detail
        if row.is_merged:
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
        self.detail_verdict.setText(f"- {verdict_text}" if title else verdict_text)
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


def _align_top(item: QTreeWidgetItem) -> None:
    """Pin every column's text to the top, so a short name lines up with the
    first line of a wrapped sentence rather than its middle."""
    for column in range(item.columnCount() or 3):
        item.setTextAlignment(
            column, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )


# PyQt ships no stubs, so the delegate is `Any` to mypy; the ignore is that boundary.
class _WrappingDelegate(QStyledItemDelegate):  # type: ignore[misc, unused-ignore]
    """Measures a wrapped cell against the width its column really has.

    QTreeView asks a delegate for a row's height without saying how wide the
    cell is, so a sentence measured as one endless line got a one-line row and
    the rest of it was cut off. The column's width is known to the header, and
    that is what the text is wrapped to here.
    """

    def __init__(self, tree: QTreeWidget) -> None:
        super().__init__(tree)
        self._tree = tree

    def initStyleOption(  # noqa: N802 - Qt override
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        super().initStyleOption(option, index)
        # Icons at the top too, beside the first line rather than mid-row.
        option.decorationAlignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

    def sizeHint(  # noqa: N802 - Qt override
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        hint = super().sizeHint(option, index)
        if index.column() != DETAIL_COLUMN:
            return hint
        width = self._tree.header().sectionSize(DETAIL_COLUMN)
        if width <= 0:
            return hint
        sized = QStyleOptionViewItem(option)
        self.initStyleOption(sized, index)
        # A rect with no height counts as invalid, and the style only wraps
        # to a valid one.
        sized.rect = QRect(0, 0, width, max(option.rect.height(), 1))
        sized.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        style = self._tree.style()
        wrapped = style.sizeFromContents(
            QStyle.ContentsType.CT_ItemViewItem, sized, QSize(), self._tree
        )
        return QSize(width, max(hint.height(), wrapped.height()))


def _button_text(text: str) -> str:
    """Text for a checkbox: "&" marks a keyboard shortcut there, "&&" is one."""
    return text.replace("&", "&&")


def _setting_bool(settings: QSettings, key: str, default: bool) -> bool:
    """A stored checkbox state, or `default` if it is missing or unreadable."""
    try:
        return bool(settings.value(key, default, type=bool))
    except Exception:
        return default


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
