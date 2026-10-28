"""The Edit tab's statistic callout: with a callout selected on the page, its
words (a value and, optionally, what it counts) to change, and a way to remove
it (`callouts.py`). A callout is added from the Add item menu like any item.
The lettering is the Edit tab's own text controls (A- / A+, bold, alignment,
colour) on the selected value or line, not a separate set here.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.core import QgsLayout, QgsLayoutItem
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .callouts import (
    LABEL_MAX_LENGTH,
    VALUE_MAX_LENGTH,
    callout_index,
    change_callout,
    find_part,
    layout_callouts,
    remove_callout,
)


class CalloutControls(QFrame):
    """Add, change and remove the layout's statistic callouts."""

    #: A callout was added, changed or removed (the page's own refresh
    #: follows its undo stack).
    edited = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "calloutControls")
        self._layout: QgsLayout | None = None
        self._index: int | None = None
        self._loading = False

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(8)
        self.heading = QLabel(self)
        self.heading.setProperty("role", "panelHeading")
        self.heading.setWordWrap(True)
        column.addWidget(self.heading)
        self.hint = QLabel(self)
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        column.addWidget(self.hint)

        self.value_edit = QLineEdit(self)
        self.value_edit.setPlaceholderText(tr("Value, such as 214"))
        self.value_edit.setAccessibleName(tr("Callout value"))
        self.value_edit.setMaxLength(VALUE_MAX_LENGTH)
        self.label_edit = QLineEdit(self)
        self.label_edit.setPlaceholderText(tr("What it counts"))
        self.label_edit.setAccessibleName(tr("What the callout counts"))
        self.label_edit.setMaxLength(LABEL_MAX_LENGTH)
        column.addWidget(self.value_edit)
        column.addWidget(self.label_edit)
        self.value_edit.editingFinished.connect(self._on_changed)
        self.label_edit.editingFinished.connect(self._on_changed)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.remove_button = QPushButton(tr("Remove callout"), self)
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.remove_button.clicked.connect(self._on_remove)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        column.addLayout(row)
        self.setVisible(False)

    # ---- showing the layout ------------------------------------------------

    def show_for(self, layout: QgsLayout | None, item: QgsLayoutItem | None) -> None:
        """Follow the layout and the one selected item: shown only when it is
        a callout's value, line or rule."""
        if layout is None or sip.isdeleted(layout):
            self._layout = None
            self._index = None
            self.setVisible(False)
            return
        self._layout = layout
        index = callout_index(item) if item is not None else None
        words = {c.index: c for c in layout_callouts(layout)}
        self._index = index if index in words else None
        if self._index is None:
            self.setVisible(False)
            return
        self._loading = True
        try:
            self.heading.setText(tr("Statistic callout"))
            chosen = words[self._index]
            self.value_edit.setText(chosen.value)
            self.label_edit.setText(chosen.label)
            self.hint.setText(tr("Lettering: use the buttons above."))
        finally:
            self._loading = False
        self.setVisible(True)

    # ---- the edits ------------------------------------------------------------

    def _on_changed(self) -> None:
        layout, index = self._layout, self._index
        if layout is None or index is None or self._loading:
            return
        if change_callout(
            layout, index, self.value_edit.text(), self.label_edit.text()
        ):
            self.edited.emit()
        else:
            self.show_for(layout, find_part(layout, index, False))

    def _on_remove(self) -> None:
        layout, index = self._layout, self._index
        if layout is None or index is None:
            return
        layout.deselectAll()
        if remove_callout(layout, index):
            self.edited.emit()
