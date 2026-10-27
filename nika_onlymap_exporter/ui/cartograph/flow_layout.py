"""A layout that wraps its widgets onto further lines when they do not fit.

For a row of buttons in the narrow control panel: on a wide panel they sit on
one line, on a narrow one the last ones move down, and the row never asks for
more width than its widest widget.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QRect, QSize, Qt
from qgis.PyQt.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem | None) -> None:  # noqa: N802 - Qt override
        if item is not None:
            self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _place(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + self._gap
                line_height = 0
            if apply:
                width = min(hint.width(), area.width())
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += hint.width() + self._gap
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def flow_row(parent: QWidget, widgets: list[QWidget], spacing: int = 6) -> QWidget:
    """`widgets` in a wrapping row, seated in a plain widget of their own."""
    row = QWidget(parent)
    # Fixed in height (its width-dependent height still applies): spare room
    # in the page goes below the rows, not into a gap inside one.
    row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout = FlowLayout(row, spacing)
    for widget in widgets:
        widget.setParent(row)
        layout.addWidget(widget)
    return row
