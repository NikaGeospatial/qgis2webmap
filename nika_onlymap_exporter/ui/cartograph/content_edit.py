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
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from qgis.PyQt.QtWidgets import QApplication, QGraphicsRectItem, QLabel, QWidget

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
#: The layout view's selection handles are scene items at or above this z.
_HANDLES_Z = 10000.0
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


def _difference(shown: QPixmap, bare: QPixmap) -> QPixmap:
    """`shown` with every pixel that equals `bare` made transparent."""
    shown_image = shown.toImage().convertToFormat(QImage.Format.Format_RGB32)
    bare_image = bare.toImage().convertToFormat(QImage.Format.Format_RGB32)
    delta = QImage(shown_image)
    painter = QPainter(delta)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
    painter.drawImage(0, 0, bare_image)
    # Any difference at all becomes full opacity: add it to itself until it
    # saturates.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    for _ in range(8):
        painter.drawImage(0, 0, QImage(delta))
    painter.end()
    chrome = shown_image.convertToFormat(QImage.Format.Format_ARGB32)
    chrome.setAlphaChannel(delta.convertToFormat(QImage.Format.Format_Grayscale8))
    result = QPixmap.fromImage(chrome)
    result.setDevicePixelRatio(shown.devicePixelRatio())
    return result


class _DragPicture(QWidget):
    """What the viewport showed when a drag began, kept over it for the drag.

    Redrawing the real page on every pointer move is what made a fast drag lag:
    the page view repaints the whole viewport, and with a colour-vision
    simulation on, every repaint also runs the simulation filter over the
    viewport and everything inside it (50-80 ms). This widget paints the
    picture the user already sees, with the frame's part of it shifted by the
    pointer's travel, which is a plain copy. It sits beside the view, not in
    it (the filter would run over it too), and is opaque, so Qt leaves the
    view underneath unpainted while it is up.
    """

    def __init__(self, view: QgsLayoutView) -> None:
        super().__init__(view.window())
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._picture = QPixmap()
        self._still = QPixmap()
        self._chrome = QPixmap()
        self._clip = QPainterPath()
        self._dirty = QRect()
        self._shift = QPointF(0, 0)
        self.hide()

    def show_over(
        self,
        shown: QPixmap,
        bare: QPixmap,
        frame: QPolygonF,
        background: QColor,
    ) -> None:
        """`shown` is the viewport as the user sees it; `bare` the same without
        what is drawn over the map (selection handles, the banner); `frame` is
        the map's outline in viewport pixels."""
        self._picture = bare
        self._chrome = _difference(shown, bare)
        # Everything but the frame never changes during the drag; the frame's
        # own area, where the picture slides, starts as the item background.
        self._still = QPixmap(shown)
        self._clip = QPainterPath()
        self._clip.addPolygon(frame)
        painter = QPainter(self._still)
        painter.fillPath(self._clip, background)
        painter.end()
        self._dirty = frame.boundingRect().toAlignedRect().adjusted(-2, -2, 2, 2)
        self._shift = QPointF(0, 0)
        viewport = self._view.viewport()
        self.setGeometry(
            QRect(viewport.mapTo(self.parentWidget(), QPoint(0, 0)), viewport.size())
        )
        self.show()
        self.raise_()

    def shift_to(self, shift: QPointF) -> None:
        self._shift = shift
        # Only the frame's area changes; the rest of the picture stands still.
        self.update(self._dirty)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        if event is None:
            return
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.drawPixmap(0, 0, self._still)
        painter.save()
        # Only what the frame showed slides, and only inside the frame: the
        # page around it must never slide into view.
        sliding = self._clip.translated(self._shift)
        painter.setClipPath(
            self._clip.intersected(sliding), Qt.ClipOperation.IntersectClip
        )
        painter.drawPixmap(self._shift, self._picture)
        painter.restore()
        # What sits over the map (handles, banner) stays where it is.
        painter.drawPixmap(0, 0, self._chrome)
        painter.end()


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
        self._press_at = QPoint()
        self._burst_id = _BURST_ID_BASE
        self._burst_at = 0.0
        self._burst_index = -1
        self._banner = _Banner(view.viewport())
        self._banner.hide()
        self._drag_picture = _DragPicture(view)
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
                # From the release position itself, so a MouseMove that never
                # arrived cannot leave the content behind the pointer.
                self._set_offset_from(at)
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
        self._press_at = QPoint(at)
        self._view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        # One grab as the user sees it, one without what is drawn over the map
        # (the banner, the selection handles): that one is what slides.
        viewport = self._view.viewport()
        shown = viewport.grab()
        banner_was_shown = self._banner.isVisible()
        self._banner.hide()
        handles = [
            thing
            for thing in self._view.scene().items()
            if isinstance(thing, QGraphicsRectItem)
            and thing.isVisible()
            and thing.zValue() >= _HANDLES_Z
        ]
        for thing in handles:
            thing.hide()
        bare = viewport.grab()
        for thing in handles:
            thing.show()
        self._banner.setVisible(banner_was_shown)
        frame = self._view.mapFromScene(item.mapToScene(item.rect()))
        background = (
            item.backgroundColor()
            if item.hasBackground()
            else QColor(Qt.GlobalColor.white)
        )
        self._drag_picture.show_over(
            shown,
            bare,
            QPolygonF([QPointF(corner) for corner in frame]),
            background,
        )
        # The view under the picture would only repaint, filter and all, for
        # nothing the user can see; it catches up in one paint on release.
        viewport.setUpdatesEnabled(False)

    def _item_delta(self, item: QgsLayoutItemMap, scene: QPointF) -> QPointF:
        """How far `scene` is from the press, in the item's own coordinates
        (so a rotated frame pans the way the pointer goes)."""
        if self._start_scene is None:
            return QPointF(0, 0)
        return item.mapFromScene(scene) - item.mapFromScene(self._start_scene)

    def _set_offset_from(self, at: QPoint) -> None:
        item = self._map
        if item is None or sip.isdeleted(item) or self._start_scene is None:
            return
        self._offset = self._item_delta(item, QPointF(self._view.mapToScene(at)))

    def _drag_to(self, at: QPoint) -> None:
        # Like the layout designer's "Move item content" tool: the map's cached
        # picture is shifted under the frame while the pointer moves, and the
        # extent changes (one re-render) only on release.
        item = self._map
        if item is None or sip.isdeleted(item) or self._start_scene is None:
            return
        self._set_offset_from(at)
        item.setMoveContentPreviewOffset(self._offset.x(), self._offset.y())
        self._drag_picture.shift_to(QPointF(at - self._press_at))

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
        # Gone only after the content moved, so the view repaints once, in the
        # state the release leaves it in.
        if not sip.isdeleted(self._view):
            viewport = self._view.viewport()
            viewport.setUpdatesEnabled(True)
            viewport.repaint()
        self._drag_picture.hide()
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
