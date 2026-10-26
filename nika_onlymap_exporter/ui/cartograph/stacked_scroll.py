"""One scrolling surface for the panel, whatever the window's height.

The panel is a column - layout list, tabs, save lines - and each tab used to
scroll by itself inside a column that scrolled as a whole, so a short window
held a scroll area inside a scroll area and the way to the end of either was
hard to find. Now only one of them scrolls at a time:

- Tall enough for the column, the tabs scroll and the column stays put.
- Too short, the tabs stop scrolling and grow to their full content, and the
  column scrolls with everything in it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtWidgets import QScrollArea, QTabWidget, QWidget

# Pixels of margin the tab widget keeps around its page that Qt does not report.
_SLACK = 10


def _content_height(area: QScrollArea) -> int:
    """The height `area`'s content needs at the width it has now."""
    content = area.widget()
    if content is None:
        return 0
    width = max(area.viewport().width(), 1)
    layout = content.layout()
    if layout is not None:
        height = (
            layout.totalHeightForWidth(width)
            if layout.hasHeightForWidth()
            else layout.totalMinimumSize().height()
        )
    else:
        height = content.sizeHint().height()
    return max(height, content.minimumSizeHint().height())


def _inner_areas(page: QWidget) -> list[QScrollArea]:
    """The scroll areas a tab page scrolls with: itself, or the visible
    ones inside it."""
    if isinstance(page, QScrollArea):
        return [page]
    return [
        area
        for area in page.findChildren(QScrollArea)
        if area.isVisibleTo(page) and area.widget() is not None
    ]


def _page_height(page: QWidget) -> int:
    """What the whole tab page needs when none of its areas scroll."""
    areas = _inner_areas(page)
    frame = 2 * page.style().pixelMetric(page.style().PixelMetric.PM_DefaultFrameWidth)
    if page in areas:
        return _content_height(page) + frame
    layout = page.layout()
    base = 0
    if layout is not None:
        base = layout.totalMinimumSize().height()
        if layout.hasHeightForWidth():
            base = max(base, layout.totalHeightForWidth(max(page.width(), 1)))
    shrunk = sum(area.minimumSizeHint().height() for area in areas)
    return base - shrunk + sum(_content_height(area) for area in areas)


class StackedScrolling(QObject):
    """Switches the panel between "tabs scroll" and "column scrolls".

    `column_height(width)` is what the column asks for at `width` while the
    tabs are at `base_min_height`, their size in the first mode.
    """

    def __init__(
        self,
        outer: QScrollArea,
        tabs: QTabWidget,
        base_min_height: int,
        column_height: object,
    ) -> None:
        super().__init__(tabs)
        self._outer = outer
        self._tabs = tabs
        self._base = base_min_height
        self._column_height = column_height
        self.stacked = False
        self._refreshing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refresh)
        outer.viewport().installEventFilter(self)
        tabs.currentChanged.connect(lambda _index: self.schedule())
        for index in range(tabs.count()):
            self.watch(tabs.widget(index))

    def watch(self, page: QWidget | None) -> None:
        """Re-measure when `page` or what it scrolls is laid out again."""
        if page is None:
            return
        page.installEventFilter(self)
        for area in _inner_areas(page):
            area.installEventFilter(self)
            content = area.widget()
            if content is not None:
                content.installEventFilter(self)

    def schedule(self) -> None:
        self._timer.start()

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is not None and event.type() in (
            QEvent.Type.LayoutRequest,
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self.schedule()
        return False

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            # Measured with the tabs at their ordinary minimum, so the answer
            # does not depend on which mode the last measurement chose.
            self._tabs.setMinimumHeight(self._base)
            need = self._column_height(self._outer.viewport().width())  # type: ignore[operator]
            self.stacked = self._outer.viewport().height() < need
            self._apply()
        finally:
            self._refreshing = False

    def _apply(self) -> None:
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self.stacked
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        for index in range(self._tabs.count()):
            page = self._tabs.widget(index)
            if page is None:
                continue
            self.watch(page)
            for area in _inner_areas(page):
                area.setVerticalScrollBarPolicy(policy)
        height = self._base
        current = self._tabs.currentWidget()
        if self.stacked and current is not None:
            # The tab widget's own height beyond its page: the bar and margins.
            bar = max(
                self._tabs.tabBar().sizeHint().height(),
                self._tabs.height() - current.height() if current.height() else 0,
            )
            height = max(self._base, bar + _page_height(current) + _SLACK)
        if self._tabs.minimumHeight() != height:
            self._tabs.setMinimumHeight(height)
