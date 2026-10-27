"""The Edit tab's statistic callouts: add, change and remove them on the
selected layout (`callouts.py`).

Shown for a layout made from a design that has callouts (Signal) or already
carrying some. With nothing selected -- or anything other than a callout -- it
offers the next one: a value and, optionally, what it counts. With a callout
selected on the page it shows that callout's words to change, and removes it.
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
    CALLOUT_LIMIT,
    LABEL_MAX_LENGTH,
    VALUE_MAX_LENGTH,
    add_callout,
    callout_index,
    can_add_callout,
    change_callout,
    find_part,
    layout_callouts,
    remove_callout,
    supports_callouts,
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
        self._shown_a_callout = False

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(4)
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
        self.value_edit.returnPressed.connect(self._on_enter)
        self.label_edit.returnPressed.connect(self._on_enter)
        self.value_edit.editingFinished.connect(self._on_changed)
        self.label_edit.editingFinished.connect(self._on_changed)
        self.value_edit.textChanged.connect(self._sync_buttons)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.add_button = QPushButton(tr("Add callout"), self)
        self.add_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.add_button.clicked.connect(self._on_add)
        self.remove_button = QPushButton(tr("Remove callout"), self)
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.remove_button.clicked.connect(self._on_remove)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        column.addLayout(row)
        self.setVisible(False)

    # ---- showing the layout ------------------------------------------------

    def show_for(self, layout: QgsLayout | None, item: QgsLayoutItem | None) -> None:
        """Follow the layout and the one selected item (a callout's value or
        line, or anything else, or nothing)."""
        if layout is None or sip.isdeleted(layout) or not supports_callouts(layout):
            self._layout = None
            self.setVisible(False)
            return
        self._layout = layout
        index = callout_index(item) if item is not None else None
        words = {c.index: c for c in layout_callouts(layout)}
        self._index = index if index in words else None
        count = len(words)
        self._loading = True
        try:
            self.heading.setText(tr("Statistic callouts"))
            if self._index is not None:
                chosen = words[self._index]
                self.value_edit.setText(chosen.value)
                self.label_edit.setText(chosen.label)
                self.hint.setText(
                    tr(
                        "{n} of {max} callouts. Lettering: use the buttons above."
                    ).format(n=count, max=CALLOUT_LIMIT)
                )
            else:
                # Words half-typed for the next callout stay while the user
                # clicks around; a selected callout's are not carried over.
                if self._shown_a_callout:
                    self.value_edit.clear()
                    self.label_edit.clear()
                self.hint.setText(
                    tr("{n} of {max} callouts. A value, and what it counts.").format(
                        n=count, max=CALLOUT_LIMIT
                    )
                    if count
                    else tr(
                        "Optional: up to {max} big numbers with a short line "
                        "under each."
                    ).format(max=CALLOUT_LIMIT)
                )
        finally:
            self._loading = False
        self._shown_a_callout = self._index is not None
        # Only where it applies: with nothing selected (to add the next one) or
        # with one of its callouts selected. Another item has its own controls.
        self.setVisible(item is None or self._index is not None)
        self._sync_buttons()

    def _sync_buttons(self, *_args: object) -> None:
        layout = self._layout
        selected = self._index is not None
        adding = (
            not selected
            and layout is not None
            and can_add_callout(layout)
            and bool(self.value_edit.text().strip())
        )
        self.add_button.setVisible(not selected)
        self.add_button.setEnabled(adding)
        self.add_button.setToolTip(
            tr("Add a statistic callout to this layout.")
            if layout is not None and can_add_callout(layout)
            else tr("This layout already has three callouts.")
        )
        self.remove_button.setVisible(selected)
        self.value_edit.setEnabled(
            selected or (layout is not None and can_add_callout(layout))
        )
        self.label_edit.setEnabled(self.value_edit.isEnabled())

    # ---- the edits ------------------------------------------------------------

    def _on_enter(self) -> None:
        if self._index is None:
            self._on_add()

    def _on_add(self) -> None:
        layout = self._layout
        if layout is None or self._loading:
            return
        if add_callout(layout, self.value_edit.text(), self.label_edit.text()):
            self.value_edit.clear()
            self.label_edit.clear()
            self._select_last(layout)
            self.edited.emit()

    def _select_last(self, layout: QgsLayout) -> None:
        """Select the callout just added, so the lettering buttons are at
        hand for it."""
        last = layout_callouts(layout)[-1].index
        item = find_part(layout, last, False)
        if item is not None:
            layout.deselectAll()
            item.setSelected(True)

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
