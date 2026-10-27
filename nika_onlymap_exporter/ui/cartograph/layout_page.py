"""The Cartograph tab's three parts: the page, the layout list, the file line.

The tab used to be three stacked boxes - Source, Cartograph file, Page - with
no picture of the page anywhere. Every product we compared (ArcGIS Pro's
layout view, Felt's print mode, Canva) leads with the page itself, so the tab
is now split: `LayoutPreview` on the left is QGIS's own layout canvas, live -
it is the same scene the layout designer draws, so a change there, a restyled
layer or a new title shows at once - and the few settings sit beside it.

- `LayoutPicker` is the layout list: existing print layouts only. Making one
  is `new_layout_button`'s job ("New from template..." and, on its menu,
  "Import cartograph..."), so a row of the list is never an action.
- `SaveLocation` says where the file saves, in one line. The path was a text
  box asked for before anything else; the file saves itself, so where it goes
  is a detail to change, not a question to answer first.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap
from qgis.gui import QgsLayoutView, QgsLayoutViewToolPan, QgsLayoutViewToolSelect
from qgis.PyQt import sip
from qgis.PyQt.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPalette,
    QResizeEvent,
    QShowEvent,
)
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import plural, tr
from .content_edit import ContentEditor
from .item_controls import ContextControls
from .item_placement import ItemPlacer
from .item_text_size import step_text_size, text_size_points
from .layout_items import (
    LINKED_KINDS,
    ItemKind,
    delete_items,
    kind_title,
    map_frames,
    map_label,
)
from .legend_editor import LegendEditor

#: The layout list's narrowest, beside its Add... button in the panel.
PICKER_MIN_WIDTH = 120


class LayoutPicker(QComboBox):
    """The project's print layouts, and nothing else.

    Making a layout is the "New from template..." button's job, not a row of
    this list: every row here is a layout that exists, and choosing one shows
    that layout.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(tr("No print layouts yet"))
        # A long layout name shortens rather than widening the panel past
        # the dialog's edge; the full name is in the open list. An explicit
        # minimum, because a styled combo box otherwise reports its longest
        # item as the least it can take.
        self.setMinimumWidth(PICKER_MIN_WIDTH)

    def set_layouts(self, names: list[str]) -> None:
        """Replace the list. Signals are the caller's to block."""
        self.clear()
        self.addItems(names)
        if not names:
            self.setCurrentIndex(-1)

    def select_layout(self, name: str) -> None:
        """Show `name` without emitting anything - for the caller's own
        refreshes."""
        self.blockSignals(True)
        self.setCurrentIndex(self.findText(name) if name else -1)
        self.blockSignals(False)

    def current_layout(self) -> str:
        """The layout shown, or "" with none."""
        return self.currentText()


class RenameLayoutDialog(QDialog):
    """Asks for a layout's new name. An empty or taken name is refused in
    the dialog itself, which stays open with the reason under the field."""

    def __init__(
        self, current: str, taken: set[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Rename layout"))
        self._current = current
        self._taken = taken
        column = QVBoxLayout(self)
        # The name being replaced, whole and wrapped: a long one runs past the
        # box below, and what is being renamed should be readable.
        self.current_label = QLabel(tr('Renaming "{name}"').format(name=current), self)
        self.current_label.setWordWrap(True)
        self.current_label.setProperty("role", "hint")
        column.addWidget(self.current_label)
        column.addWidget(QLabel(tr("New layout name"), self))
        self.name_edit = QLineEdit(current, self)
        self.name_edit.setAccessibleName(tr("Layout name"))
        column.addWidget(self.name_edit)
        self.message = QLabel("", self)
        self.message.setProperty("role", "hint")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        column.addWidget(self.message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        self.name_edit.returnPressed.connect(self._try_accept)
        self.name_edit.textEdited.connect(lambda _text: self.message.setVisible(False))
        column.addWidget(buttons)
        self.setMinimumWidth(self._wanted_width())
        # After the dialog is built and focused: a click or Tab in must not
        # have left the text unselected.
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _wanted_width(self) -> int:
        """Room to read about 45 characters of a name, or all of the current
        one if it is longer, never wider than most of the screen."""
        metrics = self.name_edit.fontMetrics()
        text = max(
            metrics.horizontalAdvance(self._current),
            45 * metrics.averageCharWidth(),
        )
        wanted = text + 80  # the field's padding and the dialog's margins
        screen = self.screen()
        available = screen.availableGeometry().width() if screen else 1280
        return int(min(wanted, available * 0.8))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # Whatever focus did on show, the name is selected to type over.
        self.name_edit.selectAll()

    def new_name(self) -> str:
        return self.name_edit.text().strip()

    def _try_accept(self) -> None:
        name = self.new_name()
        if not name:
            self._refuse(tr("A layout needs a name."))
        elif name != self._current and name in self._taken:
            self._refuse(
                tr('A layout called "{name}" already exists.').format(name=name)
            )
        else:
            self.accept()

    def _refuse(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(True)
        self.name_edit.setFocus()


def new_layout_button(parent: QWidget, role: str) -> tuple[QToolButton, QAction]:
    """ "New from template..." with a small menu arrow carrying "Import
    cartograph...": the one place a layout is made. Returns the button and the
    import action; the caller connects `clicked` and the action."""
    button = QToolButton(parent)
    button.setText(tr("New from template..."))
    button.setProperty("role", role)
    button.setProperty("hasMenu", True)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    menu = QMenu(button)
    import_action = menu.addAction(tr("Import cartograph..."))
    import_action.setToolTip(
        tr("Turn a cartograph HTML file back into a QGIS print layout")
    )
    button.setMenu(menu)
    button.setAccessibleName(tr("New from template"))
    return button, import_action


def _local_point(event: QMouseEvent) -> QPoint:
    # Qt 6 has `position()`; Qt 5 (QGIS 3) only `pos()`.
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


def _global_point(event: QMouseEvent) -> QPointF:
    if hasattr(event, "globalPosition"):
        return QPointF(event.globalPosition())
    return QPointF(event.globalPos())


# How far the pointer must travel from a press on the page before it is a drag:
# a click with a little hand jitter must neither move an item nor leave an
# empty "Move Items" step on the undo stack.
_DRAG_THRESHOLD_PX = 3

# How far outside a selected item's box a corner or edge handle still reaches.
_EDGE_REACH = 4


class LayoutPreview(QStackedWidget):
    """The layout's page, as the layout designer draws it - or, with no
    layout, the two ways to make one.

    The page is always in QGIS's own select tool, which moves and resizes
    items as undo commands on the layout's stack. The wheel zooms; holding
    Space while dragging pans the page (done here, not by QgsLayoutView's
    temporary pan tool: a left press on a selected item still reached that
    item's handles and moved it on release), as do the middle button
    (QgsLayoutView's own) and the scroll bars.
    """

    new_from_template_requested = pyqtSignal()
    import_requested = pyqtSignal()
    # The selection changed, or an item in it was edited.
    selection_changed = pyqtSignal()
    # A legend on the page was double-clicked (the item, a QgsLayoutItemLegend).
    legend_edit_requested = pyqtSignal(object)
    # An item was added with a click or drag on the page (the item), and
    # whether the page is waiting for that click.
    item_placed = pyqtSignal(object)
    placing_changed = pyqtSignal(bool)
    # A selected item was double-clicked on the page (the item).
    item_double_clicked = pyqtSignal(object)
    # The mode where a drag pans and the wheel zooms a map's content began or
    # ended (whether it is on).
    content_edit_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QgsLayoutView(self)
        self.view.setProperty("role", "layoutPreview")
        self._pan = QgsLayoutViewToolPan(self.view)
        # The select tool takes the view's mouse handles, which exist only
        # once the view has a layout: it is made in `show_layout`, and
        # made before that it crashes on the first wheel turn.
        self._select: QgsLayoutViewToolSelect | None = None
        self.view.setTool(self._pan)
        self.addWidget(self.view)

        empty = QFrame(self)
        empty.setProperty("role", "layoutEmpty")
        column = QVBoxLayout(empty)
        column.addStretch(1)
        heading = QLabel(tr("No print layout yet"), empty)
        heading.setProperty("role", "layoutEmptyHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(heading)
        note = QLabel(
            tr(
                "Pick a template to build one from what the map canvas shows, "
                "or import a cartograph."
            ),
            empty,
        )
        note.setProperty("role", "layoutEmptyNote")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        column.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.template_button, import_action = new_layout_button(empty, "primary")
        self.template_button.clicked.connect(self.new_from_template_requested)
        import_action.triggered.connect(self.import_requested)
        buttons.addWidget(self.template_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        column.addStretch(1)
        self.addWidget(empty)
        self._empty = empty

        # The page fills the space - on first show, on a resize, on another
        # layout - until the user zooms; their zoom then stays, until another
        # layout is chosen.
        # A zoom is the user's when it comes from their wheel or the zoom
        # buttons: the view also reports a zoom change when it is merely
        # resized, which once stopped the page fitting at all.
        self._user_zoomed = False
        self._reaching = False
        self._space_down = False
        self._pan_from: QPoint | None = None
        self._press_at: QPoint | None = None
        self._pointer_at: QPoint | None = None
        self._legend_sizes_at_press: dict[str, tuple[float, float]] = {}
        self.view.viewport().installEventFilter(self)
        # Ctrl+Z / Ctrl+Shift+Z in the page undo and redo its edits, on the
        # layout's own stack as in the designer. A key filter rather than a
        # QShortcut: that one waits for the window to be the active one.
        self.view.installEventFilter(self)
        # Not the first Tab stop: opening the dialog put the focus ring round
        # the page. A click still focuses it; the zoom bar is the keyboard's.
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # After the filters above, so it sees a press first while it is waiting
        # for the click that places a new item.
        self.placer = ItemPlacer(self.view)
        self.placer.placed.connect(self.item_placed)
        self.placer.active_changed.connect(self.placing_changed)
        # Also after the filters above: while it is on, a press on the map
        # being edited is its drag, never the select tool's move or resize.
        self.content_editor = ContentEditor(self.view)
        self.content_editor.active_changed.connect(self._on_content_edit_changed)
        self.content_editor.edited.connect(self.selection_changed)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit)

    def show_layout(self, layout) -> None:  # QgsPrintLayout | None
        if layout is None:
            self.setCurrentWidget(self._empty)
            return
        if self.view.currentLayout() is not layout:
            self.placer.cancel()
            self.content_editor.finish()
            self.view.setCurrentLayout(layout)
            self._select = QgsLayoutViewToolSelect(self.view)
            self._select.setLayout(layout)
            layout.selectionChanged.connect(self._on_selection_changed)
            # An undo or redo may change the selected item's text size.
            layout.undoStack().stack().indexChanged.connect(self._on_selection_changed)
            self.view.setTool(self._select)
            self._user_zoomed = False
        self.setCurrentWidget(self.view)
        self._fit_timer.start()

    def _on_selection_changed(self, *_args: object) -> None:
        # Only the layout on show matters; one left behind may still emit.
        if not sip.isdeleted(self):
            self.selection_changed.emit()

    def text_item(self):  # QgsLayoutItem | None
        """The one selected item whose text size can be stepped, if any."""
        layout = self.view.currentLayout()
        if layout is None or sip.isdeleted(layout):
            return None
        chosen = layout.selectedLayoutItems()
        if len(chosen) != 1 or text_size_points(chosen[0]) is None:
            return None
        return chosen[0]

    def step_text_size(self, bigger: bool) -> bool:
        """The selected item's text one step bigger or smaller, as an undo
        step on the layout. The page keeps its zoom and scroll."""
        layout = self.view.currentLayout()
        item = self.text_item()
        if layout is None or item is None:
            return False
        changed = step_text_size(layout, item, bigger)
        self.selection_changed.emit()
        return changed

    def add_item(self, kind: ItemKind, map_item) -> None:  # QgsLayoutItemMap | None
        """The next click or drag on the page places a new item of `kind`
        (about `map_item`, for a legend, scale bar or north arrow). The page
        stays as the user has it."""
        self._user_zoomed = True
        self.placer.begin(kind, map_item)

    def delete_selected(self) -> bool:
        """Delete the selected items as one undo step."""
        layout = self.view.currentLayout()
        if layout is None or sip.isdeleted(layout):
            return False
        chosen = layout.selectedLayoutItems()
        self.content_editor.finish()
        delete_items(layout, chosen)
        self.selection_changed.emit()
        return bool(chosen)

    def showing_page(self) -> bool:
        return self.currentWidget() is self.view

    def fit_page(self) -> None:
        """Fit page: the whole page again, and fitted from now on, as if
        the user had never zoomed."""
        self._user_zoomed = False
        self._fit()
        # Again once the zoomed page's scroll bars have gone: the first fit
        # was measured with them still taking room.
        self._fit_timer.start()

    def edit_content(self, map_item) -> None:  # QgsLayoutItemMap | None
        """Pan and zoom what `map_item` shows inside its frame (a drag pans,
        the wheel zooms); `None` ends it. The page stays as the user has it."""
        if map_item is None:
            self.content_editor.finish()
            return
        self._user_zoomed = True
        self.placer.cancel()
        self.content_editor.begin(map_item)

    def _on_content_edit_changed(self, on: bool) -> None:
        if not sip.isdeleted(self):
            self.content_edit_changed.emit(on)

    def select_mode(self) -> bool:
        return self._select is not None and self.view.tool() is self._select

    def _step_undo(self, redo: bool) -> None:
        layout = self.view.currentLayout()
        if layout is None:
            return
        stack = layout.undoStack().stack()
        stack.redo() if redo else stack.undo()

    def zoom_in(self) -> None:
        self._user_zoomed = True
        self.view.zoomIn()

    def zoom_out(self) -> None:
        self._user_zoomed = True
        self.view.zoomOut()

    def _fit(self) -> None:
        if not (self.isVisible() and self.showing_page()) or self._user_zoomed:
            return
        self.view.zoomFull()

    def _space_pan(self, watched: QObject | None, event: QEvent) -> bool | None:
        """Hold Space and drag to pan the page; None when `event` is not
        part of that."""
        kind = event.type()
        viewport = self.view.viewport()
        if watched is self.view and isinstance(event, QKeyEvent):
            if event.key() != Qt.Key.Key_Space:
                return None
            if kind == QEvent.Type.KeyPress and self.select_mode():
                if not event.isAutoRepeat():
                    self._space_down = True
                    viewport.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            if kind == QEvent.Type.KeyRelease and self._space_down:
                if not event.isAutoRepeat():
                    self._end_space_pan()
                return True
            return None
        if watched is self.view and kind == QEvent.Type.FocusOut:
            self._end_space_pan()
            return None
        if watched is not viewport or not self._space_down:
            return None
        if not isinstance(event, QMouseEvent):
            return None
        if (
            kind == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._pan_from = _local_point(event)
            viewport.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if self._pan_from is None:
            return None
        if kind == QEvent.Type.MouseMove:
            at = _local_point(event)
            horizontal, vertical = (
                self.view.horizontalScrollBar(),
                self.view.verticalScrollBar(),
            )
            horizontal.setValue(horizontal.value() - (at.x() - self._pan_from.x()))
            vertical.setValue(vertical.value() - (at.y() - self._pan_from.y()))
            self._pan_from = at
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            self._pan_from = None
            viewport.setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        return None

    def _end_space_pan(self) -> None:
        self._space_down = False
        self._pan_from = None
        if not sip.isdeleted(self.view):
            self.view.viewport().unsetCursor()

    def _hover_before_press(self, event: QEvent) -> None:
        """The select tool treats a press it was not hovered into as the start
        of a move, so a plain click then leaves an empty "Move Items" step.
        A mouse always hovers first; a tap, a pen or a click arriving where
        the pointer already rests does not, so the hover is sent first."""
        if not isinstance(event, QMouseEvent):
            return
        kind = event.type()
        at = _local_point(event)
        if (
            kind == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and self.select_mode()
            and not self._reaching
            and self._pointer_at != at
        ):
            self._pointer_at = at  # before sending: the hover passes this filter
            hover = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(at),
                _global_point(event),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                event.modifiers(),
            )
            QApplication.sendEvent(self.view.viewport(), hover)
        elif kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            self._pointer_at = at

    def _within_click_jitter(self, event: QEvent) -> bool:
        """A left-button move that has not yet left the press's neighbourhood
        is held back from the select tool: it is still a click."""
        if not isinstance(event, QMouseEvent) or not self.select_mode():
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and not self._reaching:
                self._press_at = _local_point(event)
            return False
        if kind == QEvent.Type.MouseButtonRelease:
            self._press_at = None
            return False
        if (
            kind != QEvent.Type.MouseMove
            or self._press_at is None
            or not event.buttons() & Qt.MouseButton.LeftButton
        ):
            return False
        away = _local_point(event) - self._press_at
        if abs(away.x()) + abs(away.y()) > _DRAG_THRESHOLD_PX:
            self._press_at = None  # a real drag from here on
            return False
        return True

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is not None:
            panned = self._space_pan(watched, event)
            if panned is not None:
                return panned
        if event is not None and watched is self.view.viewport():
            self._hover_before_press(event)
        if (
            event is not None
            and watched is self.view.viewport()
            and self._within_click_jitter(event)
        ):
            return True
        if (
            event is not None
            and watched is self.view
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            if event.matches(QKeySequence.StandardKey.Undo):
                self._step_undo(redo=False)
                return True
            if event.matches(QKeySequence.StandardKey.Redo):
                self._step_undo(redo=True)
                return True
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.content_editor.active:
                    return True  # editing the view inside a frame, not the frame
                return self.select_mode() and self.delete_selected()
        if (
            event is not None
            and watched is self.view.viewport()
            and event.type() == QEvent.Type.MouseButtonDblClick
            and isinstance(event, QMouseEvent)
            and self.select_mode()
        ):
            self._announce_double_click(_local_point(event))
        if (
            event is not None
            and watched is self.view.viewport()
            and event.type() == QEvent.Type.MouseButtonDblClick
            and isinstance(event, QMouseEvent)
            and self.select_mode()
        ):
            legend = self._legend_at(_local_point(event))
            if legend is not None:
                self.legend_edit_requested.emit(legend)
        if (
            event is not None
            and watched is self.view.viewport()
            and isinstance(event, QMouseEvent)
            and self._take_edge_reach(event)
        ):
            return True
        if (
            event is not None
            and watched is self.view.viewport()
            and isinstance(event, QMouseEvent)
        ):
            self._keep_dragged_legend_size(event)
        if event is not None and (
            event.type() == QEvent.Type.Wheel
            # Having selected or moved something, the page stays as the user
            # has it: an edit's refresh must not fit the page under their hand.
            or (event.type() == QEvent.Type.MouseButtonPress and self.select_mode())
        ):
            self._user_zoomed = True
        return False

    def _keep_dragged_legend_size(self, event: QMouseEvent) -> None:
        """A legend that sizes itself to its contents snaps back to them at
        its next change, which undoes a resize the user dragged. So the
        first time a drag changes such a legend's size, it stops sizing
        itself. The select tool applies a resize when the button is
        released, inside its undo step, so each selected automatic legend is
        watched from the press until just after the release; one Undo then brings
        back both the size and the automatic sizing. Legends nobody resized
        keep sizing themselves."""
        layout = self.view.currentLayout()
        if layout is None or sip.isdeleted(layout) or not self.select_mode():
            return
        if event.type() == QEvent.Type.MouseButtonPress:
            self._unwatch_legends()
            for item in layout.items():
                if (
                    isinstance(item, QgsLayoutItemLegend)
                    and item.resizeToContents()
                    and item.isSelected()
                ):
                    self._legend_sizes_at_press[item.uuid()] = (
                        item.rect().width(),
                        item.rect().height(),
                    )
                    item.sizePositionChanged.connect(self._on_watched_legend_resized)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._unwatch_legends)

    def _on_watched_legend_resized(self) -> None:
        item = self.sender()
        if not isinstance(item, QgsLayoutItemLegend) or sip.isdeleted(item):
            return
        before = self._legend_sizes_at_press.get(item.uuid())
        if before is None or not item.resizeToContents():
            return
        size = item.rect()
        if abs(size.width() - before[0]) + abs(size.height() - before[1]) > 0.01:
            item.setResizeToContents(False)

    def _unwatch_legends(self) -> None:
        layout = self.view.currentLayout()
        if layout is not None and not sip.isdeleted(layout):
            for item in layout.items():
                if (
                    isinstance(item, QgsLayoutItemLegend)
                    and item.uuid() in self._legend_sizes_at_press
                ):
                    with contextlib.suppress(TypeError):  # already disconnected
                        item.sizePositionChanged.disconnect(
                            self._on_watched_legend_resized
                        )
        self._legend_sizes_at_press = {}

    def _legend_at(self, point: QPoint):  # QgsLayoutItemLegend | None
        layout = self.view.currentLayout()
        if layout is None or sip.isdeleted(layout):
            return None
        item = layout.layoutItemAt(QPointF(self.view.mapToScene(point)), False)
        return item if isinstance(item, QgsLayoutItemLegend) else None

    def _announce_double_click(self, point: QPoint) -> None:
        layout = self.view.currentLayout()
        if layout is None or sip.isdeleted(layout):
            return
        scene_point = QPointF(self.view.mapToScene(point))
        for item in layout.selectedLayoutItems():
            if item.sceneBoundingRect().contains(scene_point):
                self.item_double_clicked.emit(item)
                return

    def _selection_view_rect(self) -> QRect | None:
        layout = self.view.currentLayout()
        if layout is None:
            return None
        rect = QRect()
        for item in layout.selectedLayoutItems():
            rect = rect.united(
                self.view.mapFromScene(item.sceneBoundingRect()).boundingRect()
            )
        return None if rect.isNull() else rect

    def _take_edge_reach(self, event: QMouseEvent) -> bool:
        """Let a corner or edge handle be taken a few pixels outside the
        selection too, as on most canvases.

        QGIS only treats the strip just inside a selected item's box as a
        handle; a press on the box line or outside it, where the eye puts
        the corner of a small box, moves the item (or selects a neighbour)
        instead of resizing it. A hover or a left press in that outer
        ring is re-sent from just inside the box. Returns True when it
        was re-sent."""
        if self._reaching or not self.select_mode():
            return False
        kind = event.type()
        hover = (
            kind == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.NoButton
        )
        press = (
            kind == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        )
        if not (hover or press):
            return False
        try:
            box = self._selection_view_rect()
            if box is None:
                return False
            at = _local_point(event)
            reach = _EDGE_REACH
            if not box.adjusted(-reach, -reach, reach, reach).contains(
                at
            ) or box.adjusted(1, 1, -2, -2).contains(at):
                return False
            inside = box.adjusted(reach, reach, -reach, -reach)
            if inside.width() < 1 or inside.height() < 1:
                return False
            moved = QPoint(
                min(max(at.x(), inside.left()), inside.right()),
                min(max(at.y(), inside.top()), inside.bottom()),
            )
            shifted = QMouseEvent(
                event.type(),
                QPointF(moved),
                _global_point(event) + QPointF(moved - at),
                event.button(),
                event.buttons(),
                event.modifiers(),
            )
            self._reaching = True
            try:
                QApplication.sendEvent(self.view.viewport(), shifted)
            finally:
                self._reaching = False
            return True
        except RuntimeError:
            # The layout or view went away under the event.
            return False

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # Around the page, the dialog's own window colour rather than the
        # designer's grey; the theme sets it on the palette. The window's
        # palette, which the theme sets, not this widget's inherited copy.
        self.view.setBackgroundBrush(
            self.window().palette().color(QPalette.ColorRole.Window)
        )
        self._fit_timer.start()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_timer.start()


#: How to move the page, said on the Fit page and zoom buttons.
_PAN_HINT = "Pan: hold Space or the middle mouse button and drag. Zoom: the wheel"


class PagePane(QWidget):
    """The preview with what it is said above it and how to move it below.

    Above: "Layout preview — QGIS · A4 landscape". The page is QGIS's drawing of the
    layout, and the cartograph can differ from it - a north arrow the
    runtime draws its own way, the Page settings applied on export - so it
    is named for what it is; Preview in browser shows the HTML itself. Below: Fit page,
    Add item, zoom out, the zoom level and zoom in, which the wheel and a drag
    also do but nothing on screen said so.

    What can be changed on the selected item is `controls`, which the host
    seats in the panel's Edit tab; `edit_requested` asks it to bring that tab
    up (a double-click on an item, or placing a new one).
    """

    shown = pyqtSignal()
    edit_requested = pyqtSignal()
    changes_requested = pyqtSignal()
    open_editor_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self._change_count = 0
        self._fitting_bar = False
        self.caption = QLabel(self)
        self.caption.setProperty("role", "pageCaption")
        # The caption may clip in a narrow window rather than widen the page
        # pane and squeeze the control panel beside it (its tooltip says it all).
        self.caption.setMinimumWidth(1)
        self.caption.setToolTip(
            tr(
                "QGIS's drawing of the print layout. The cartograph can differ "
                "in places - see Checks - and Preview in browser shows it."
            )
        )
        caption_row = QHBoxLayout()
        caption_row.setSpacing(8)
        caption_row.addWidget(self.caption)
        caption_row.addStretch(1)
        # Beside the caption, so the page and how the HTML differs from it
        # are read together: the differences themselves are in Checks.
        self.changes_link = QPushButton(self)
        self.changes_link.setProperty("role", "link")
        self.changes_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.changes_link.setToolTip(tr("See them in Checks"))
        self.changes_link.clicked.connect(self.changes_requested)
        self.changes_link.setVisible(False)
        caption_row.addWidget(self.changes_link)
        # QGIS's own layout designer for the layout shown; the controller
        # enables it when there is a layout and an iface to open it with.
        self.editor_link = QPushButton(tr("Open layout editor"), self)
        self.editor_link.setProperty("role", "link")
        self.editor_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.editor_link.clicked.connect(self.open_editor_requested)
        self.editor_link.setEnabled(False)
        caption_row.addWidget(self.editor_link)
        column.addLayout(caption_row)
        self.preview = LayoutPreview(self)
        column.addWidget(self.preview, 1)

        self.zoom_bar = QWidget(self)
        bar = QHBoxLayout(self.zoom_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        self.fit_button = self._bar_button(
            tr("Fit page"), tr("Fit the whole page"), tr(_PAN_HINT)
        )
        self.fit_button.clicked.connect(self.preview.fit_page)
        bar.addWidget(self.fit_button)
        # Text, a legend, a scale bar or a north arrow: choosing one waits for
        # the click on the page that places it.
        self.add_button = self._bar_button(
            tr("Add item"),
            tr("Add text, a legend, a scale bar or a north arrow, then click the page"),
        )
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.add_menu = QMenu(self.add_button)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_button.setMenu(self.add_menu)
        # Marked while the page waits for the click that places the item.
        self.add_button.setCheckable(True)
        bar.addWidget(self.add_button)
        bar.addStretch(1)
        self.zoom_out_button = self._bar_button(
            "\N{MINUS SIGN}", tr("Zoom out"), tr(_PAN_HINT)
        )
        self.zoom_out_button.clicked.connect(self.preview.zoom_out)
        bar.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel(self.zoom_bar)
        self.zoom_label.setProperty("role", "hint")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.zoom_label)
        self.zoom_in_button = self._bar_button("+", tr("Zoom in"), tr(_PAN_HINT))
        self.zoom_in_button.clicked.connect(self.preview.zoom_in)
        bar.addWidget(self.zoom_in_button)
        column.addWidget(self.zoom_bar)
        # What can be changed on the selected item: seated by the host in
        # the panel's Edit tab, not in this column.
        self.controls = ContextControls()
        self.destroyed.connect(self.controls.deleteLater)

        # Named in words while there is room, in pictures (the words stay as
        # tooltips) when the bar would cut them short.
        self._word_buttons = (self.fit_button, self.add_button)
        for button, icon in zip(self._word_buttons, ("fit", "add"), strict=True):
            button.setProperty("lineIcon", icon)
            button.setIconSize(QSize(15, 15))
        self._icons_only = False

        self.preview.view.zoomLevelChanged.connect(self._show_zoom)
        self.preview.selection_changed.connect(self._refresh_controls)
        self.preview.item_placed.connect(self._on_item_placed)
        self.controls.edit_view_toggled.connect(self._on_edit_view_toggled)
        self.preview.content_edit_changed.connect(self.controls.set_editing)
        self.controls.text_size_step.connect(self.preview.step_text_size)
        # The legend's editor lives in the Edit tab: the preview's double-click
        # on a legend opens the tab, as for any item.
        self.controls.set_legend_editor_factory(lambda parent: LegendEditor(parent))
        self.preview.legend_edit_requested.connect(
            lambda _legend: self.edit_requested.emit()
        )
        self.preview.placing_changed.connect(self.add_button.setChecked)
        self.preview.item_double_clicked.connect(
            lambda _item: self.edit_requested.emit()
        )
        self.preview.currentChanged.connect(lambda _index: self._show_page_parts())
        self._show_page_parts()

    def set_change_count(self, count: int) -> None:
        """How many things the HTML changes from this page; none hides it."""
        self._change_count = count
        self.changes_link.setText(
            plural(count, "{n} change in the HTML", "{n} changes in the HTML")
        )
        self._show_page_parts()

    def _bar_button(self, text: str, name: str, hint: str = "") -> QToolButton:
        button = QToolButton(self.zoom_bar)
        button.setText(text)
        button.setToolTip(f"{name}. {hint}" if hint else name)
        button.setAccessibleName(name)
        button.setProperty("role", "secondary")
        button.setProperty("hasMenu", False)
        button.setProperty("zoom", True)
        return button

    def show_layout(self, layout) -> None:  # QgsPrintLayout | None
        self.preview.show_layout(layout)
        self.controls.set_layout(layout)
        self.caption.setText(
            tr("Layout preview — QGIS · {page}").format(page=_page_name(layout))
            if layout is not None
            else ""
        )
        self._show_page_parts()

    def _fill_add_menu(self) -> None:
        """Text always; a legend, scale bar or north arrow about the layout's
        map - or, with several, a choice of which."""
        menu = self.add_menu
        menu.clear()
        layout = self.preview.view.currentLayout()
        maps = [] if layout is None or sip.isdeleted(layout) else map_frames(layout)
        for kind in (ItemKind.TEXT, *LINKED_KINDS):
            title = kind_title(kind)
            if kind not in LINKED_KINDS:
                menu.addAction(
                    title, lambda kind=kind: self.preview.add_item(kind, None)
                )
            elif not maps:
                action = menu.addAction(title)
                action.setEnabled(False)
                action.setToolTip(tr("Needs a map on the page"))
            elif len(maps) == 1:
                menu.addAction(
                    title, lambda kind=kind, m=maps[0]: self.preview.add_item(kind, m)
                )
            else:
                sub = menu.addMenu(title)
                for map_item in maps:
                    sub.addAction(
                        map_label(map_item),
                        lambda kind=kind, m=map_item: self.preview.add_item(kind, m),
                    )

    def _refresh_controls(self) -> None:
        """Follow the selection in the Edit tab. A view being edited inside a
        frame ends when the selection is no longer that frame."""
        editor = self.preview.content_editor
        if editor.active:
            layout = self.preview.view.currentLayout()
            chosen = [] if layout is None else layout.selectedLayoutItems()
            if len(chosen) != 1 or chosen[0] is not editor.target():
                editor.finish()
        try:
            self.controls.refresh()
        except RuntimeError:
            # The layout or an item went away under the signal.
            self.controls.set_layout(None)

    def _on_edit_view_toggled(self, on: bool) -> None:
        item = self.controls.selected_item() if on else None
        self.preview.edit_content(item if isinstance(item, QgsLayoutItemMap) else None)

    def _on_item_placed(self, item: object) -> None:
        self._refresh_controls()
        self.edit_requested.emit()

    def _show_page_parts(self) -> None:
        showing = self.preview.showing_page()
        self.caption.setVisible(showing)
        self.changes_link.setVisible(showing and self._change_count > 0)
        self.editor_link.setVisible(showing)
        self.zoom_bar.setVisible(showing)
        self._show_zoom()

    def _fit_bar(self) -> None:
        """Words on Fit page and Add item while the bar has room for them;
        pictures with the same words as tooltips when it has not."""
        if not hasattr(self, "_word_buttons") or self._fitting_bar:
            return
        self._fitting_bar = True
        try:
            self._set_icons_only(False)
            self.zoom_bar.layout().activate()
            short = self.zoom_bar.layout().sizeHint().width() > self.width()
            if short:
                self._set_icons_only(True)
        finally:
            self._fitting_bar = False

    def _set_icons_only(self, icons_only: bool) -> None:
        self._icons_only = icons_only
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if icons_only
            else Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        for button in self._word_buttons:
            button.setToolButtonStyle(style)
            button.setProperty("tight", icons_only)
            button.style().unpolish(button)
            button.style().polish(button)

    def _show_zoom(self, *_args: object) -> None:
        # 100% is the page at its real size on this screen, as QGIS's own
        # layout designer counts it.
        view = self.preview.view
        per_mm = view.physicalDpiX() / 25.4
        level = view.transform().m11() / per_mm if per_mm else 0.0
        self.zoom_label.setText(f"{round(level * 100)}%")

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_bar()

    def changeEvent(self, event: QEvent | None) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event is not None and event.type() == QEvent.Type.FontChange:
            self._fit_bar()

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._fit_bar()
        self.shown.emit()


def _page_name(layout) -> str:  # QgsPrintLayout
    """ "A4 landscape", or "216 x 279 mm portrait", for the first page."""
    from qgis.core import QgsLayoutMeasurementConverter, QgsUnitTypes

    from .status import page_label

    pages = layout.pageCollection()
    if pages.pageCount() == 0:
        return ""
    size = QgsLayoutMeasurementConverter().convert(
        pages.page(0).pageSize(), QgsUnitTypes.LayoutMillimeters
    )
    width, height = size.width(), size.height()
    orientation = "landscape" if width >= height else "portrait"
    spec = SimpleNamespace(width=round(width), height=round(height), size=None)
    spec.orientation = tr(orientation.capitalize()).lower()
    return page_label(spec) or ""


class SaveLocation(QWidget):
    """Where the cartograph saves: its file name, then the folder under it.

        parcels-Survey.html
        ~/Documents/QGIS2WebMap/Cartographs
        [Change...] [Show in folder]

    The name first and whole, because it is what someone sharing the file
    looks for; the folder below, shortened in the middle if it must be, with
    the full path as its tooltip. It was one line - "Saves to" and a path
    cut in the middle - which hid the one part people check.

    Holds the path the way the text box it replaced did - `text()` and
    `setText()` - and emits `textEdited` only when the user picks a file, so
    the controller's rules for a typed path apply unchanged.
    """

    textEdited = pyqtSignal(str)  # noqa: N815 - mirrors QLineEdit's
    change_requested = pyqtSignal()
    reveal_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(0)
        self._path = ""
        self._placeholder = ""
        self.label = QLabel(self)
        self.label.setProperty("role", "saveName")
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setMinimumWidth(1)
        grid.addWidget(self.label, 0, 0)
        self.folder_label = QLabel(self)
        self.folder_label.setProperty("role", "saveLocation")
        self.folder_label.setMinimumWidth(1)
        grid.addWidget(self.folder_label, 1, 0)
        # Their own row: beside the path they were squeezed to a cut-off
        # "Change..." in the narrow panel. Whole-word buttons, side by side.
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(6)
        self.change_button = QPushButton(tr("Change..."), self)
        self.change_button.setToolTip(tr("Choose another file to save to"))
        self.change_button.setAccessibleName(tr("Change the file to save to"))
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.clicked.connect(self.change_requested)
        buttons.addWidget(self.change_button)
        self.reveal_button = QPushButton(tr("Show in folder"), self)
        self.reveal_button.setToolTip(tr("Open the folder holding this file"))
        self.reveal_button.setAccessibleName(tr("Show the file in its folder"))
        self.reveal_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_button.clicked.connect(self.reveal_requested)
        buttons.addWidget(self.reveal_button)
        buttons.addStretch(1)
        grid.addLayout(buttons, 2, 0)
        grid.setColumnStretch(0, 1)

    def text(self) -> str:
        return self._path

    def setText(self, path: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self._path = path
        self._show()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self._placeholder = text
        self._show()

    def placeholderText(self) -> str:  # noqa: N802 - mirrors QLineEdit
        return self._placeholder

    def choose(self, path: str) -> None:
        """A path the user picked: shown, and reported as their edit."""
        self.setText(path)
        self.textEdited.emit(path)

    def _show(self) -> None:
        self.label.setToolTip(self._path)
        self.folder_label.setToolTip(str(Path(self._path).parent) if self._path else "")
        has_path = bool(self._path)
        self.folder_label.setVisible(has_path)
        self.reveal_button.setVisible(has_path)
        self._elide()

    def _elide(self) -> None:
        if not self._path:
            self.label.setText(self._placeholder)
            return
        path = Path(self._path)
        name_metrics = self.label.fontMetrics()
        self.label.setText(
            name_metrics.elidedText(
                path.name, Qt.TextElideMode.ElideMiddle, max(40, self.label.width())
            )
        )
        folder = _home_relative(path.parent)
        metrics = self.folder_label.fontMetrics()
        self.folder_label.setText(
            metrics.elidedText(
                folder, Qt.TextElideMode.ElideMiddle, max(40, self.folder_label.width())
            )
        )

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()


def _home_relative(folder: Path) -> str:
    """`~/Documents/...` rather than `/home/name/Documents/...`: shorter,
    and the part of the path the user recognises."""
    try:
        return str(Path("~") / folder.relative_to(Path.home()))
    except ValueError:
        return str(folder)
