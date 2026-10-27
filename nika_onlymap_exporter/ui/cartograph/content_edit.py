"""Editing what a map frame shows, on the page preview, with the frame held still.

In the layout designer this is the "Move item content" tool. Here it is an
explicit mode of the preview, switched on from the selected map's controls
("Edit view" / "Edit inset view"): a drag pans the map's *content* inside its
frame and the wheel zooms it about the pointer, while the frame itself can
neither move nor resize. The mode ends with Escape, the same button, a click
on anything else on the page, selecting another item, showing another layout
or deleting the map.

Every gesture is one undo step on the layout's own stack, applied through
QgsLayoutItemMap's content operations (`moveContent`, `zoomContent`) so a
rotated map, the linked overview outlines and the scale bars follow:

- a drag is one command, opened at its first move and closed on release;
- a burst of wheel steps (each within `WHEEL_BURST_MS` of the last) is one
  step too, by giving its commands one merge id.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import time

from qgis.core import QgsLayoutItemMap
from qgis.gui import QgsLayoutView
from qgis.PyQt import sip
from qgis.PyQt.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QWheelEvent,
)
from qgis.PyQt.QtWidgets import QApplication, QLabel, QWidget

from ..i18n import tr

#: Wheel steps closer together than this are one undo step.
WHEEL_BURST_MS = 400
#: One wheel notch (120 eighths of a degree) zooms the content by this factor.
WHEEL_NOTCH_FACTOR = 1.2
#: A precision trackpad reports pixels only; this many pixels count as one notch.
WHEEL_PIXELS_PER_NOTCH = 60.0
#: A press this close outside the frame still counts as on it, so the
#: select tool's resize handles round the frame cannot be reached in this mode.
_HANDLE_REACH_PX = 10
#: First merge id of the wheel bursts; QGIS's own item commands use small ids.
_BURST_ID_BASE = 0x4E4B0000


def _local(event: QMouseEvent | QWheelEvent) -> QPoint:
    # Qt 6 has `position()`; Qt 5 (QGIS 3) only `pos()`.
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


class _Banner(QLabel):
    """The note over the page that says the mode is on. Drawn here, in the
    window's highlight colours (the theme's accent and the text on it): a
    label inside the page view takes no fill from the dialog's stylesheet."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setContentsMargins(8, 3, 8, 3)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        palette = self.window().palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
        painter.drawRoundedRect(QRectF(self.rect()), 4, 4)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
        painter.drawText(
            self.contentsRect(), int(Qt.AlignmentFlag.AlignVCenter), self.text()
        )
        painter.end()


def _wheel_steps(event: QWheelEvent) -> float:
    """Notches turned: the angle when the device reports one, else the
    pixel delta of a precision trackpad, scaled so small swipes zoom a little."""
    angle = event.angleDelta().y()
    if angle:
        return angle / 120.0
    return event.pixelDelta().y() / WHEEL_PIXELS_PER_NOTCH


class ContentEditor(QObject):
    """The "drag pans, wheel zooms the content" mode of one layout view."""

    active_changed = pyqtSignal(bool)
    #: The content was changed (a drag ended or a wheel step was applied).
    edited = pyqtSignal()

    def __init__(self, view: QgsLayoutView) -> None:
        super().__init__(view)
        self._view = view
        self._map: QgsLayoutItemMap | None = None
        self._start_scene: QPointF | None = None
        self._offset = QPointF(0, 0)
        self._dragging = False
        self._burst_id = _BURST_ID_BASE
        self._burst_at = 0.0
        self._burst_index = -1
        self._banner = _Banner(view.viewport())
        self._banner.hide()
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)

    # ---- state -------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._map is not None

    def target(self) -> QgsLayoutItemMap | None:
        """The map whose content is being edited."""
        return self._map

    def begin(self, map_item: QgsLayoutItemMap) -> None:
        """Edit the content of `map_item`."""
        if self._view.currentLayout() is None or sip.isdeleted(map_item):
            return
        if self._map is map_item:
            return
        self.finish()
        self._map = map_item
        self._banner.setText(
            tr(
                "Editing the view inside {map}. Drag to pan, wheel to zoom. "
                "Esc to finish."
            ).format(map=map_item.displayName())
        )
        self._banner.adjustSize()
        self._banner.move(8, 8)
        self._banner.show()
        self._banner.raise_()
        self._view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        QApplication.instance().installEventFilter(self)
        self.active_changed.emit(True)

    def finish(self) -> None:
        """Leave the mode (a drag in progress is dropped, not applied)."""
        if self._map is None:
            return
        self._end_drag(apply=False)
        self._map = None
        self._banner.hide()
        if not sip.isdeleted(self._view):
            self._view.viewport().unsetCursor()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.active_changed.emit(False)

    # ---- events ------------------------------------------------------------

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is None or not self.active:
            return False
        try:
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                return self._key(watched, event)
            if watched is not self._view.viewport():
                return False
            if isinstance(event, QWheelEvent):
                return self._wheel(event)
            if isinstance(event, QMouseEvent):
                return self._mouse(event)
        except RuntimeError:
            # The layout, the view or the map went away under the event.
            self.finish()
        return False

    def _key(self, watched: QObject | None, event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Escape:
            return False
        # Only this page's window, and never while a menu or popup has the key.
        if QApplication.activePopupWidget() is not None:
            return False
        widget = watched if hasattr(watched, "window") else None
        if widget is not None and widget.window() is not self._view.window():  # type: ignore[attr-defined]
            return False
        self.finish()
        return True

    def _frame_rect(self) -> QRect | None:
        item = self._map
        if item is None or sip.isdeleted(item):
            return None
        return self._view.mapFromScene(item.sceneBoundingRect()).boundingRect()

    def _on_frame(self, point: QPoint) -> bool:
        rect = self._frame_rect()
        if rect is None:
            return False
        reach = _HANDLE_REACH_PX
        return rect.adjusted(-reach, -reach, reach, reach).contains(point)

    def _mouse(self, event: QMouseEvent) -> bool:
        kind = event.type()
        at = _local(event)
        if self._dragging:
            if kind == QEvent.Type.MouseMove:
                self._drag_to(at)
                return True
            if kind == QEvent.Type.MouseButtonRelease:
                self._end_drag()
                return True
            return True
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False  # middle button pans the page; right is a menu
            if not self._on_frame(at):
                # A click on anything else is the user going back to Select.
                self.finish()
                return False
            self._begin_drag(at)
            return True
        if kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            return self._on_frame(at) and kind != QEvent.Type.MouseMove
        return kind == QEvent.Type.MouseButtonDblClick and self._on_frame(at)

    # ---- drag --------------------------------------------------------------

    def _begin_drag(self, at: QPoint) -> None:
        item, layout = self._map, self._view.currentLayout()
        if item is None or layout is None:
            return
        self._dragging = True
        self._start_scene = QPointF(self._view.mapToScene(at))
        self._offset = QPointF(0, 0)
        self._view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _item_delta(self, item: QgsLayoutItemMap, scene: QPointF) -> QPointF:
        """How far `scene` is from the press, in the item's own coordinates
        (so a rotated frame pans the way the pointer goes)."""
        assert self._start_scene is not None
        return item.mapFromScene(scene) - item.mapFromScene(self._start_scene)

    def _drag_to(self, at: QPoint) -> None:
        # Like the layout designer's "Move item content" tool: the map's cached
        # picture is shifted under the frame while the pointer moves, and the
        # extent changes (one re-render) only on release.
        item = self._map
        if item is None or sip.isdeleted(item) or self._start_scene is None:
            return
        self._offset = self._item_delta(item, QPointF(self._view.mapToScene(at)))
        item.setMoveContentPreviewOffset(self._offset.x(), self._offset.y())
        item.update()

    def _end_drag(self, apply: bool = True) -> None:
        if not self._dragging:
            return
        self._dragging = False
        item, layout = self._map, self._view.currentLayout()
        offset, self._start_scene, self._offset = self._offset, None, QPointF(0, 0)
        if item is not None and not sip.isdeleted(item):
            # As the layout designer's tool does: the preview shift goes first,
            # then the one real move; QGIS draws the cached picture at the new
            # extent itself, so a shift kept over the render would count twice.
            item.setMoveContentPreviewOffset(0, 0)
            if apply and layout is not None and not offset.isNull():
                stack = layout.undoStack()
                stack.beginCommand(item, tr("Pan map content"))
                # The content follows the pointer: the extent moves the other way.
                item.moveContent(-offset.x(), -offset.y())
                stack.endCommand()
                self.edited.emit()
            else:
                item.update()
        if self.active and not sip.isdeleted(self._view):
            self._view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    # ---- wheel -------------------------------------------------------------

    def _wheel(self, event: QWheelEvent) -> bool:
        item, layout = self._map, self._view.currentLayout()
        at = _local(event)
        if item is None or layout is None or not self._on_frame(at):
            return False
        steps = _wheel_steps(event)
        if steps == 0 or self._dragging:
            return True  # held still: the wheel never scrolls what is behind
        factor = WHEEL_NOTCH_FACTOR**steps
        anchor = item.mapFromScene(QPointF(self._view.mapToScene(at)))
        stack = layout.undoStack()
        now = time.monotonic()
        index = stack.stack().index()
        # A new burst: a pause, or any other undo step since the last step.
        if (now - self._burst_at) * 1000 > WHEEL_BURST_MS or index != self._burst_index:
            self._burst_id += 1
        stack.beginCommand(item, tr("Zoom map content"), self._burst_id)
        item.zoomContent(factor, anchor)
        stack.endCommand()
        self._burst_at = now
        self._burst_index = stack.stack().index()
        self.edited.emit()
        return True
