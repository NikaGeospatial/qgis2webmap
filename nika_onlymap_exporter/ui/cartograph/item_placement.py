"""Placing a new layout item with a click or a drag on the page preview.

QGIS's own add-item tool needs the layout designer's item registry, which a
plugin dialog does not have, so this is the small mode the preview needs
instead: after "Add" the next press on the page starts the item's box, a drag
sizes it (a rubber band shows it) and the release makes it. A press without a
drag gives the kind's usual size. Escape or a right click leaves the mode. The
item is then selected and the mode is over: one item per choice.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.core import QgsLayoutItemMap
from qgis.gui import QgsLayoutView
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeyEvent, QMouseEvent
from qgis.PyQt.QtWidgets import QRubberBand

from ...qt_compat import event_point
from .layout_items import ItemKind, create_item, default_rect

#: A drag shorter than this many pixels is a click.
_CLICK_SLOP = 6


class ItemPlacer(QObject):
    """The "next click places it" mode of one layout view."""

    placed = pyqtSignal(object)  # the new QgsLayoutItem
    active_changed = pyqtSignal(bool)

    def __init__(self, view: QgsLayoutView) -> None:
        super().__init__(view)
        self._view = view
        self._kind: ItemKind | None = None
        self._map: QgsLayoutItemMap | None = None
        self._start: QPoint | None = None
        self._band: QRubberBand | None = None
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)

    @property
    def active(self) -> bool:
        return self._kind is not None

    def begin(self, kind: ItemKind, map_item: QgsLayoutItemMap | None) -> None:
        """The next press on the page places an item of `kind`."""
        if self._view.currentLayout() is None:
            return
        self.cancel()
        self._kind, self._map = kind, map_item
        self._view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.active_changed.emit(True)

    def cancel(self) -> None:
        was_active = self.active
        self._kind = self._map = self._start = None
        if self._band is not None:
            self._band.hide()
            self._band.deleteLater()
            self._band = None
        if not sip.isdeleted(self._view):
            self._view.viewport().unsetCursor()
        if was_active:
            self.active_changed.emit(False)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is None or not self.active:
            return False
        try:
            if (
                watched is self._view
                and isinstance(event, QKeyEvent)
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
            ):
                self.cancel()
                return True
            if watched is not self._view.viewport() or not isinstance(
                event, QMouseEvent
            ):
                return False
            return self._mouse(event)
        except RuntimeError:
            # The layout or view went away under the event.
            self.cancel()
            return False

    def _mouse(self, event: QMouseEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._start = event_point(event)
                self._band = QRubberBand(
                    QRubberBand.Shape.Rectangle, self._view.viewport()
                )
            else:
                self.cancel()
            return True
        if kind == QEvent.Type.MouseMove:
            if self._start is not None and self._band is not None:
                box = QRect(self._start, event_point(event)).normalized()
                self._band.setGeometry(box)
                self._band.show()
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            if self._start is not None and event.button() == Qt.MouseButton.LeftButton:
                self._finish(self._start, event_point(event))
            return True
        return kind == QEvent.Type.MouseButtonDblClick

    def _finish(self, start: QPoint, end: QPoint) -> None:
        layout = self._view.currentLayout()
        kind, map_item = self._kind, self._map
        self.cancel()
        if layout is None or sip.isdeleted(layout) or kind is None:
            return
        first = self._view.mapToScene(start)
        if (end - start).manhattanLength() < _CLICK_SLOP:
            rect = default_rect(layout, kind, first)
        else:
            rect = QRectF(first, self._view.mapToScene(end)).normalized()
        rect = _inside_page(layout, rect)
        if map_item is not None and sip.isdeleted(map_item):
            map_item = None
        item = create_item(layout, kind, rect, map_item)
        layout.deselectAll()
        item.setSelected(True)
        self.placed.emit(item)


def _inside_page(layout, rect: QRectF) -> QRectF:  # QgsLayout, QRectF
    """`rect` moved back onto the page it starts on, when it overhangs."""
    page = layout.pageCollection().pageAtPoint(rect.topLeft())
    if page is None:
        return rect
    # Not `sceneBoundingRect`: that includes the page's drop shadow.
    area = QRectF(page.pos(), page.rect().size())
    moved = QRectF(rect)
    if moved.right() > area.right():
        moved.moveRight(area.right())
    if moved.bottom() > area.bottom():
        moved.moveBottom(area.bottom())
    if moved.left() < area.left():
        moved.moveLeft(area.left())
    if moved.top() < area.top():
        moved.moveTop(area.top())
    return moved
