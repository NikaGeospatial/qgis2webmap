"""The current print layout, in a compact header, and the chooser for the rest.

The header names the layout (two lines at most), says what it is in a line
(paper, orientation, pages, atlas), and holds the "Layout actions" menu. A
plain "New from template..." button sits under it. "Switch layout..." is the
first entry of the actions menu when the project has more than one layout. The
chooser behind that lists every layout of the project by its full name and the
same line, with a thumbnail when the layout has been shown
(`layout_thumbnails.py`) - it never renders one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from qgis.core import QgsUnitTypes
from qgis.PyQt.QtCore import QRect, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import plural, tr
from .status import page_label

#: The chooser shows its search box from this many layouts on.
SEARCH_FROM = 6

#: Thumbnail size in the chooser, in pixels.
THUMBNAIL_SIZE = QSize(84, 60)


def layout_metadata(layout: object) -> str:
    """ "A3 landscape · 1 page", and "· Atlas on" when the layout is an atlas."""
    pages = layout.pageCollection()  # type: ignore[attr-defined]
    count = int(pages.pageCount())
    parts: list[str] = []
    if count:
        converter = layout.renderContext().measurementConverter()  # type: ignore[attr-defined]
        size = converter.convert(
            pages.page(0).pageSize(), QgsUnitTypes.LayoutUnit.LayoutMillimeters
        )
        width, height = round(size.width(), 1), round(size.height(), 1)
        orientation = "landscape" if width >= height else "portrait"
        named = page_label(SimpleNamespace(width=width, height=height, orientation=""))
        words = tr("landscape") if orientation == "landscape" else tr("portrait")
        parts.append(f"{named} {words}" if named else words)
    parts.append(plural(count, "{n} page", "{n} pages"))
    if layout.atlas().enabled():  # type: ignore[attr-defined]
        parts.append(tr("Atlas on"))
    return " · ".join(parts)


def _wrapped(text: str, metrics: QFontMetrics, width: int, lines: int) -> list[str]:
    """`text` broken to `width` in at most `lines` lines, at a space where
    there is one; the last line is cut with an ellipsis when there is more."""
    remaining = text.strip()
    out: list[str] = []
    while remaining and len(out) < lines:
        if len(out) == lines - 1 or metrics.horizontalAdvance(remaining) <= width:
            out.append(
                metrics.elidedText(remaining, Qt.TextElideMode.ElideRight, width)
            )
            break
        cut = len(remaining)
        while cut > 1 and metrics.horizontalAdvance(remaining[:cut]) > width:
            cut -= 1
        space = remaining.rfind(" ", 0, cut + 1)
        if space > 0:
            cut = space
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return out or [""]


class TwoLineLabel(QLabel):
    """A name shown on up to two lines and cut with an ellipsis after that;
    the whole name is in the tooltip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._rewrap()

    def _rewrap(self) -> None:
        metrics = self.fontMetrics()
        width = max(self.width() - 2, 40)
        lines = _wrapped(self._full, metrics, width, 2)
        super().setText("\n".join(lines))
        self.setFixedHeight(metrics.lineSpacing() * len(lines) + 2)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._rewrap()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == event.Type.FontChange:
            self._rewrap()


def _actions_glyph(button: QToolButton) -> str:
    """ "⋯" where the font has it, else "…" (which every font has)."""
    return "⋯" if QFontMetrics(button.font()).inFontUcs4(0x22EF) else "…"


class LayoutHeader(QWidget):
    """Name, one metadata line, "Layout actions", New from template."""

    rename_requested = pyqtSignal()
    import_requested = pyqtSignal()
    new_from_template_requested = pyqtSignal()
    switch_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.name_label = TwoLineLabel(self)
        self.name_label.setProperty("role", "layoutName")
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        top.addWidget(self.name_label, 1, Qt.AlignmentFlag.AlignTop)

        self.actions_button = QToolButton(self)
        self.actions_button.setText(_actions_glyph(self.actions_button))
        self.actions_button.setProperty("role", "secondary")
        self.actions_button.setProperty("hasMenu", False)
        # Only the dots, in a compact square: hasMenu=false does not stop the
        # shared menu arrow, so this has its own rule.
        self.actions_button.setProperty("layoutActions", True)
        self.actions_button.setAccessibleName(tr("Layout actions"))
        self.actions_button.setToolTip(tr("Layout actions"))
        self.actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        menu = QMenu(self.actions_button)
        menu.setToolTipsVisible(True)
        self.switch_action = menu.addAction(tr("Switch layout..."))
        self.switch_action.setToolTip(tr("Open another print layout of this project"))
        self.switch_separator = menu.addSeparator()
        self.rename_action = menu.addAction(tr("Rename layout..."))
        self.rename_action.setToolTip(
            tr(
                "Change this layout's name in the project. "
                "The map title is not changed."
            )
        )
        self.import_action = menu.addAction(tr("Import cartograph..."))
        self.import_action.setToolTip(
            tr("Turn a cartograph HTML file back into a QGIS print layout")
        )
        # The button's menu arrow would be a second, redundant cue.
        self.actions_button.setMenu(menu)
        self.switch_action.triggered.connect(self.switch_requested)
        self.rename_action.triggered.connect(self.rename_requested)
        self.import_action.triggered.connect(self.import_requested)
        top.addWidget(self.actions_button, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(top)

        self.meta_label = QLabel("", self)
        self.meta_label.setProperty("role", "hint")
        self.meta_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        column.addWidget(self.meta_label)
        column.addSpacing(4)

        self.new_from_template_button = QToolButton(self)
        self.new_from_template_button.setText(tr("New from template..."))
        self.new_from_template_button.setProperty("role", "secondary")
        self.new_from_template_button.setProperty("hasMenu", False)
        self.new_from_template_button.setAccessibleName(tr("New from template"))
        self.new_from_template_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.new_from_template_button.clicked.connect(self.new_from_template_requested)
        column.addWidget(self.new_from_template_button)

    def show_layout(self, name: str, metadata: str, layout_count: int) -> None:
        """The layout shown ("" for none), its line, and how many there are."""
        has_layout = bool(name)
        self.name_label.setVisible(has_layout)
        self.meta_label.setVisible(has_layout)
        self.name_label.set_full_text(name)
        self.meta_label.setText(metadata)
        self.meta_label.setToolTip(metadata)
        self.rename_action.setEnabled(has_layout)
        many = layout_count > 1
        self.switch_action.setVisible(many)
        self.switch_separator.setVisible(many)


@dataclass(frozen=True)
class LayoutEntry:
    """One layout of the project as the chooser lists it."""

    name: str
    metadata: str
    thumbnail: QPixmap | None = None


def _placeholder(widget: QWidget) -> QPixmap:
    """A neutral page for a layout that has not been shown yet."""
    pixmap = QPixmap(THUMBNAIL_SIZE)
    base = widget.palette().window().color()
    pixmap.fill(base)
    painter = QPainter(pixmap)
    ink = widget.palette().windowText().color()
    edge = QColor(ink)
    edge.setAlpha(110)
    painter.setPen(edge)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(
        QRect(6, 6, THUMBNAIL_SIZE.width() - 13, THUMBNAIL_SIZE.height() - 13)
    )
    painter.end()
    return pixmap


class LayoutChooserDialog(QDialog):
    """ "Layouts in this project": every layout, the current one marked.

    Choosing one (double-click, Enter or Open) sets `chosen_name`.
    """

    def __init__(
        self,
        entries: list[LayoutEntry],
        current: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Layouts in this project"))
        self._entries = entries
        self._current = current
        self.chosen_name: str | None = None
        column = QVBoxLayout(self)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(tr("Search layouts"))
        self.search_edit.setAccessibleName(tr("Search layouts"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setVisible(len(entries) > SEARCH_FROM)
        self.search_edit.textChanged.connect(self._filter)
        column.addWidget(self.search_edit)

        self.list = QListWidget(self)
        self.list.setAccessibleName(tr("Layouts in this project"))
        self.list.setIconSize(THUMBNAIL_SIZE)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setUniformItemSizes(False)
        self.list.setSpacing(2)
        self.list.itemActivated.connect(self._open_item)
        # A double-click opens on every platform style, whatever it calls an
        # "activation".
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.currentItemChanged.connect(self._sync_open)
        column.addWidget(self.list, 1)

        self.empty_note = QLabel(tr("No layout matches."), self)
        self.empty_note.setProperty("role", "hint")
        self.empty_note.setVisible(False)
        column.addWidget(self.empty_note)

        self.buttons = QDialogButtonBox(self)
        self.open_button = self.buttons.addButton(
            tr("Open"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._open_current)
        self.buttons.rejected.connect(self.reject)
        column.addWidget(self.buttons)

        self.resize(460, 440)
        self._placeholder = _placeholder(self)
        self._fill()
        (self.search_edit if len(entries) > SEARCH_FROM else self.list).setFocus()

    @staticmethod
    def _text(entry: LayoutEntry, current: bool) -> str:
        line = f"{tr('Current')} · {entry.metadata}" if current else entry.metadata
        return f"{entry.name}\n{line}"

    def _fill(self) -> None:
        self.list.clear()
        for entry in self._entries:
            is_current = entry.name == self._current
            item = QListWidgetItem(self._text(entry, is_current))
            item.setData(Qt.ItemDataRole.UserRole, entry.name)
            item.setIcon(QIcon(entry.thumbnail or self._placeholder))
            item.setToolTip(entry.name)
            if is_current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
        self._filter(self.search_edit.text())
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == self._current:
                self.list.setCurrentRow(row)
                break
        else:
            self._select_first_visible()
        self._fit_rows()

    def _select_first_visible(self) -> None:
        for row in range(self.list.count()):
            if not self.list.item(row).isHidden():
                self.list.setCurrentRow(row)
                return

    def _fit_rows(self) -> None:
        """Every row tall enough for the whole name, wrapped."""
        metrics = QFontMetrics(self.list.font())
        width = max(self.list.viewport().width() - THUMBNAIL_SIZE.width() - 28, 120)
        for row in range(self.list.count()):
            item = self.list.item(row)
            box = metrics.boundingRect(
                QRect(0, 0, width, 10_000), Qt.TextFlag.TextWordWrap, item.text()
            )
            item.setSizeHint(
                QSize(width, max(box.height() + 12, THUMBNAIL_SIZE.height() + 8))
            )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_rows()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._fit_rows()

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        shown = 0
        for row, entry in enumerate(self._entries):
            match = not needle or needle in f"{entry.name} {entry.metadata}".casefold()
            self.list.item(row).setHidden(not match)
            shown += match
        self.empty_note.setVisible(shown == 0)
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self._select_first_visible()
        self._sync_open()

    def _sync_open(self, *_args: object) -> None:
        current = self.list.currentItem()
        self.open_button.setEnabled(current is not None and not current.isHidden())

    def _open_item(self, item: QListWidgetItem) -> None:
        self.chosen_name = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _open_current(self) -> None:
        item = self.list.currentItem()
        if item is not None and not item.isHidden():
            self._open_item(item)

    def visible_names(self) -> list[str]:
        return [
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.list.count())
            if not self.list.item(row).isHidden()
        ]
