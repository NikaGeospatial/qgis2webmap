"""The editor for one layout legend: its map, its entries, its look.

A `LegendEditor` edits the legend QGIS draws - it adds nothing of its own to
the layout. The entries list is `core/legend_edit.entries`: a tick puts a
layer or one of its classes in or out of the *legend* (never off the map), the
label is an edit of what the legend shows (never the layer's name), the arrows
reorder, and "Restore removed" puts back everything that was left out. Every
change is one undo step on the layout's stack, so Ctrl+Z in the page or the
layout designer undoes it, and the list follows the stack.

It is a plain widget so a panel can host it: the page preview opens it in a
small window on a double-click of a legend (`LegendEditorWindow`).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib

from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import legend_edit as edit
from ...core.legend_edit import EntryKind, LegendEntry
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from .item_text_size import MAX_POINTS, MIN_POINTS, set_text_size, text_size_points

_ENTRY = Qt.ItemDataRole.UserRole


def _range_note() -> str:
    """Said beside a ramp whose statistics QGIS never computed."""
    return tr("range unavailable \u2014 set min/max in Symbology")


class _Combo(QComboBox):
    """Never answers the wheel (it scrolls the panel); keyboard focus only."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class _Spin(QSpinBox):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setKeyboardTracking(False)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class _DoubleSpin(QDoubleSpinBox):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setKeyboardTracking(False)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is not None:
            event.ignore()


class LegendEditor(QWidget):
    """Edits one `QgsLayoutItemLegend`; `set_legend` chooses which."""

    #: A change was made to the legend (for the host to refresh around it).
    edited = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._legend: QgsLayoutItemLegend | None = None
        self._busy = False
        # True while the tree is announcing a change (inside `itemChanged`,
        # i.e. inside the delegate's `editorEvent`): the tree and its items
        # must stay as they are until Qt is out of that call.
        self._in_item_event = False
        self._refresh_wanted = False
        # Ticks and label edits made in the tree, applied after its event.
        self._pending_edits: list[tuple[LegendEntry, bool, str]] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.map_label = QLabel(tr("Map:"), self)
        row.addWidget(self.map_label)
        self.map_combo = _Combo(self)
        self.map_combo.setAccessibleName(tr("Map frame the legend describes"))
        self.map_label.setBuddy(self.map_combo)
        self.map_combo.activated.connect(self._on_map_chosen)
        row.addWidget(self.map_combo, 1)
        column.addLayout(row)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("legendEntries")
        self.tree.setAccessibleName(tr("Legend entries"))
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.tree.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.tree.setToolTip(
            tr(
                "Tick to show an entry in this legend. Double-click a label to "
                "rename it in the legend only."
            )
        )
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._sync_buttons)
        self.tree.setMinimumHeight(140)
        column.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.up_button = QToolButton(self)
        self.up_button.setArrowType(Qt.ArrowType.UpArrow)
        self.up_button.setAccessibleName(tr("Move the entry up"))
        self.up_button.setToolTip(tr("Move the entry up"))
        self.up_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = QToolButton(self)
        self.down_button.setArrowType(Qt.ArrowType.DownArrow)
        self.down_button.setAccessibleName(tr("Move the entry down"))
        self.down_button.setToolTip(tr("Move the entry down"))
        self.down_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.down_button.clicked.connect(lambda: self._move(1))
        self.restore_button = QPushButton(tr("Restore removed"), self)
        self.restore_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.restore_button.setToolTip(
            tr("Put back every layer and class left out of this legend")
        )
        self.restore_button.clicked.connect(self._restore)
        buttons.addWidget(self.up_button)
        buttons.addWidget(self.down_button)
        buttons.addStretch(1)
        buttons.addWidget(self.restore_button)
        column.addLayout(buttons)

        self.note = FittedLabel("", self)
        self.note.setProperty("role", "hint")
        column.addWidget(self.note)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        # A narrow panel or a larger font puts a field under its label rather
        # than squeezing the label's text ("Row spac...").
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.columns_spin = _Spin(self)
        self.columns_spin.setRange(1, 12)
        self.columns_spin.setAccessibleName(tr("Legend columns"))
        self.columns_spin.valueChanged.connect(self._on_columns)
        form.addRow(tr("Columns"), self.columns_spin)
        self.size_spin = _Spin(self)
        self.size_spin.setRange(int(MIN_POINTS), int(MAX_POINTS))
        self.size_spin.setSuffix(tr(" pt"))
        self.size_spin.setAccessibleName(tr("Legend text size"))
        self.size_spin.valueChanged.connect(self._on_size)
        form.addRow(tr("Text size"), self.size_spin)
        self.spacing_spin = _DoubleSpin(self)
        self.spacing_spin.setRange(0.0, 20.0)
        self.spacing_spin.setDecimals(1)
        self.spacing_spin.setSingleStep(0.5)
        self.spacing_spin.setSuffix(tr(" mm"))
        self.spacing_spin.setAccessibleName(tr("Space between legend rows"))
        self.spacing_spin.valueChanged.connect(self._on_spacing)
        form.addRow(tr("Row spacing"), self.spacing_spin)
        column.addLayout(form)

        self._stack = None
        # A change made elsewhere (Ctrl+Z, the layout designer) re-lists once
        # the stack settles, not once per command of a macro.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(0)
        self._edit_timer.timeout.connect(self._apply_pending_edits)
        self.setMinimumWidth(280)

    # ---- Wiring ----------------------------------------------------------

    def legend(self) -> QgsLayoutItemLegend | None:
        legend = self._legend
        return None if legend is None or sip.isdeleted(legend) else legend

    def _layout(self):  # QgsPrintLayout | None
        legend = self.legend()
        if legend is None:
            return None
        layout = legend.layout()
        return None if layout is None or sip.isdeleted(layout) else layout

    def _project(self):  # QgsProject | None
        layout = self._layout()
        return layout.project() if layout is not None else None

    def set_legend(self, legend: QgsLayoutItemLegend | None) -> None:
        self._unwatch()
        self._legend = legend
        layout = self._layout()
        if layout is not None:
            self._stack = layout.undoStack().stack()
            self._stack.indexChanged.connect(self._schedule_refresh)
        self.refresh()

    def _unwatch(self) -> None:
        if self._stack is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                self._stack.indexChanged.disconnect(self._schedule_refresh)
        self._stack = None

    def _schedule_refresh(self, *_args: object) -> None:
        if not sip.isdeleted(self):
            self._refresh_timer.start()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._unwatch()
        super().closeEvent(event)

    # ---- Showing ---------------------------------------------------------

    def refresh(self) -> None:
        """Re-list from the legend, keeping the selection and the scroll."""
        legend, project = self.legend(), self._project()
        if self._busy:
            return
        if self._in_item_event:
            # Emptying the tree under the click that is still inside Qt's
            # `editorEvent` frees the item Qt is about to use: re-list after.
            self._refresh_wanted = True
            self._refresh_timer.start()
            return
        self._refresh_wanted = False
        self._busy = True
        try:
            scroll = self.tree.verticalScrollBar().value()
            keep = self._current_key()
            self.tree.blockSignals(True)
            self.tree.clear()
            self.setEnabled(legend is not None and project is not None)
            if legend is not None and project is not None:
                self._fill_maps(legend)
                self._fill_entries(legend, project)
                self._fill_look(legend)
            # No row is "current" until the user picks one: Qt would otherwise
            # draw a focus frame round the first row on opening.
            self.tree.setCurrentItem(None)
            self.tree.blockSignals(False)
            self._select_key(keep)
            self.tree.verticalScrollBar().setValue(scroll)
            self._sync_buttons()
        finally:
            self._busy = False

    def _fill_maps(self, legend: QgsLayoutItemLegend) -> None:
        layout = legend.layout()
        maps = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
        linked = legend.linkedMap()
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        for item in maps:
            self.map_combo.addItem(item.displayName(), item.uuid())
        if linked is not None:
            self.map_combo.setCurrentIndex(
                max(self.map_combo.findData(linked.uuid()), 0)
            )
        self.map_combo.setEnabled(len(maps) > 1)
        self.map_combo.blockSignals(False)

    def _fill_entries(self, legend: QgsLayoutItemLegend, project) -> None:
        parent: QTreeWidgetItem | None = None
        removed = 0
        listed = edit.entries(legend, project)
        for entry in listed:
            if entry.kind is EntryKind.LAYER:
                item = QTreeWidgetItem(self.tree)
                parent = item
            else:
                if parent is None:
                    continue
                item = QTreeWidgetItem(parent)
            if not entry.included:
                removed += 1
            self._style_item(item, entry)
        self.tree.expandAll()
        self.restore_button.setEnabled(removed > 0)
        support = [e.label for e in listed if e.support and not e.included]
        lines = []
        if support:
            lines.append(
                plural(
                    len(support),
                    "{names} is left out as a support layer. Tick it to show it.",
                    "{names} are left out as support layers. Tick one to show it.",
                    names=", ".join(support),
                )
            )
        by_layer = {e.layer_id: e.label for e in listed if e.kind is EntryKind.LAYER}
        lines += [
            f"{by_layer.get(e.layer_id, '')}: {_range_note()}"
            for e in listed
            if e.note == edit.RANGE_UNAVAILABLE
        ]
        self.note.setText("\n".join(lines))

    def _style_item(self, item: QTreeWidgetItem, entry: LegendEntry) -> None:
        text = entry.label
        if entry.note:
            short = tr("range unavailable")
            text = f"{text} — {short}" if entry.note == edit.RANGE_UNAVAILABLE else text
        item.setText(0, text)
        item.setData(0, _ENTRY, entry)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        flags |= Qt.ItemFlag.ItemIsUserCheckable
        if entry.editable and entry.included:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        item.setCheckState(
            0, Qt.CheckState.Checked if entry.included else Qt.CheckState.Unchecked
        )
        if entry.kind is EntryKind.LAYER:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        if entry.note == edit.RANGE_UNAVAILABLE:
            item.setToolTip(0, f"{entry.label}: {_range_note()}")
        if entry.label != entry.source_label:
            item.setToolTip(
                0,
                tr("Shown as {label}; the layer is {name}").format(
                    label=entry.label, name=entry.source_label
                ),
            )
        if not entry.included:
            item.setForeground(0, self.palette().placeholderText())

    def _fill_look(self, legend: QgsLayoutItemLegend) -> None:
        for spin, value in (
            (self.columns_spin, legend.columnCount()),
            (self.size_spin, round(text_size_points(legend) or 12)),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.spacing_spin.blockSignals(True)
        self.spacing_spin.setValue(edit.row_spacing_mm(legend))
        self.spacing_spin.blockSignals(False)

    # ---- Selection -------------------------------------------------------

    def _current_key(self) -> tuple[str, int | None] | None:
        item = self.tree.currentItem()
        entry = item.data(0, _ENTRY) if item is not None else None
        return (entry.layer_id, entry.index) if isinstance(entry, LegendEntry) else None

    def _select_key(self, key: tuple[str, int | None] | None) -> None:
        if key is None:
            return
        iterator = self.entry_items()
        for item in iterator:
            entry = item.data(0, _ENTRY)
            if (entry.layer_id, entry.index) == key:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(item)
                self.tree.blockSignals(False)
                return

    def entry_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            items.append(top)
            items.extend(top.child(i) for i in range(top.childCount()))
        return items

    def item_for(self, layer_id: str, index: int | None = None) -> QTreeWidgetItem:
        for item in self.entry_items():
            entry = item.data(0, _ENTRY)
            if entry.layer_id == layer_id and entry.index == index:
                return item
        raise KeyError(layer_id)

    def _selected_entry(self) -> LegendEntry | None:
        item = self.tree.currentItem()
        entry = item.data(0, _ENTRY) if item is not None else None
        return entry if isinstance(entry, LegendEntry) else None

    def _sync_buttons(self, *_args: object) -> None:
        entry = self._selected_entry()
        movable = entry is not None and entry.included
        self.up_button.setEnabled(movable)
        self.down_button.setEnabled(movable)

    # ---- Edits (each one undo step) --------------------------------------

    def _done(self) -> None:
        self.refresh()
        self.edited.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        """Only note what the user did. Qt is still inside the delegate's
        `editorEvent` for this item, so nothing here may change the legend
        (its undo stack refreshes the Edit tab) or touch the tree's items; the
        edit itself runs on the next turn of the event loop."""
        entry = item.data(0, _ENTRY)
        if self._busy or not isinstance(entry, LegendEntry):
            return
        self._in_item_event = True
        try:
            ticked = item.checkState(0) == Qt.CheckState.Checked
            self._pending_edits.append((entry, ticked, item.text(0)))
        finally:
            self._in_item_event = False
        self._edit_timer.start()

    def _apply_pending_edits(self) -> None:
        if sip.isdeleted(self):
            return
        pending, self._pending_edits = self._pending_edits, []
        for entry, ticked, text in pending:
            self._apply_edit(entry, ticked, text)
        QTimer.singleShot(0, self._done)

    def _apply_edit(self, entry: LegendEntry, ticked: bool, text: str) -> None:
        legend, project, layout = self.legend(), self._project(), self._layout()
        if legend is None or project is None:
            return
        # The row may have been ticked more than once before this ran: judge
        # against what the legend lists now, not what the row listed then.
        entry = next(
            (
                e
                for e in edit.entries(legend, project)
                if (e.layer_id, e.index) == (entry.layer_id, entry.index)
            ),
            entry,
        )
        try:
            if ticked != entry.included:
                if entry.kind is EntryKind.LAYER:
                    edit.set_layer_listed(
                        layout,
                        legend,
                        project,
                        entry.layer_id,
                        ticked,
                        tr("Show {name} in the legend")
                        if ticked
                        else tr("Hide {name} from the legend"),
                    )
                else:
                    edit.set_class_listed(
                        layout,
                        legend,
                        project,
                        entry.layer_id,
                        entry.index or 0,
                        ticked,
                        tr("Show a class of {name} in the legend")
                        if ticked
                        else tr("Hide a class of {name} from the legend"),
                    )
            else:
                edit.set_entry_label(
                    layout,
                    legend,
                    project,
                    entry,
                    text,
                    tr("Change a legend label"),
                )
        except Exception:  # a slot must never raise into Qt
            return

    def _move(self, step: int) -> None:
        entry = self._selected_entry()
        legend, project = self.legend(), self._project()
        if entry is None or legend is None or project is None:
            return
        edit.move_entry(
            self._layout(), legend, project, entry, step, tr("Move a legend entry")
        )
        self._done()

    def _restore(self) -> None:
        legend, project = self.legend(), self._project()
        if legend is None or project is None:
            return
        edit.restore_removed(
            self._layout(), legend, project, tr("Restore the legend's removed entries")
        )
        self._done()

    def _on_columns(self, value: int) -> None:
        legend = self.legend()
        if self._busy or legend is None:
            return
        edit.set_columns(self._layout(), legend, value, tr("Change legend columns"))
        self.edited.emit()

    def _on_size(self, value: int) -> None:
        legend, layout = self.legend(), self._layout()
        if self._busy or legend is None or layout is None:
            return
        set_text_size(layout, legend, float(value))
        self.edited.emit()

    def _on_spacing(self, value: float) -> None:
        legend = self.legend()
        if self._busy or legend is None:
            return
        edit.set_row_spacing(self._layout(), legend, value, tr("Change legend spacing"))
        self.edited.emit()

    def _on_map_chosen(self, index: int) -> None:
        legend, layout = self.legend(), self._layout()
        uuid = self.map_combo.itemData(index)
        if legend is None or layout is None or not isinstance(uuid, str):
            return
        target = next(
            (
                i
                for i in layout.items()
                if isinstance(i, QgsLayoutItemMap) and i.uuid() == uuid
            ),
            None,
        )
        if target is None or (
            legend.linkedMap() is not None and legend.linkedMap().uuid() == uuid
        ):
            return
        stack = layout.undoStack()
        stack.beginCommand(legend, tr("Link the legend to another map"))
        try:
            legend.setLinkedMap(target)
        finally:
            stack.endCommand()
        self._done()


class LegendEditorWindow(LegendEditor):
    """A `LegendEditor` as a small tool window, for the page preview to open
    next to a legend; it follows the legend until closed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowTitle(tr("Legend"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
