"""Whatever is added with Add item is drawn above what the page already holds,
so a template's opaque legend or notes box cannot hide it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest
from live_count_fixtures import build_page

pytest.importorskip("qgis.core")
from qgis.core import QgsLayoutExporter, QgsLayoutItemLabel
from qgis.PyQt.QtCore import QRectF, QSize
from qgis.PyQt.QtGui import QColor

from nika_onlymap_exporter.ui.cartograph.layout_items import (
    ItemKind,
    create_detail_map,
    create_item,
)

COVER = "#ff00ff"
SCALE = 3


def _covered_page(project):
    page = build_page(project)
    layout = page.layout
    # A template's box: opaque, over the whole page, above what a new item is
    # put on by default.
    cover = QgsLayoutItemLabel(layout)
    cover.setText("")
    layout.addLayoutItem(cover)
    cover.attemptSetSceneRect(QRectF(0.0, 0.0, 297.0, 210.0))
    cover.setBackgroundEnabled(True)
    cover.setBackgroundColor(QColor(COVER))
    cover.setZValue(100.0)
    return page


def _visible_pixels(layout, item) -> set[str]:
    layout.deselectAll()
    image = QgsLayoutExporter(layout).renderPageToImage(
        0, QSize(297 * SCALE, 210 * SCALE)
    )
    box = item.sceneBoundingRect()
    return {
        image.pixelColor(x, y).name()
        for x in range(int(box.left() * SCALE), int(box.right() * SCALE), 2)
        for y in range(int(box.top() * SCALE), int(box.bottom() * SCALE), 2)
    }


@pytest.mark.parametrize(
    "kind",
    [
        ItemKind.TEXT,
        ItemKind.LEGEND,
        ItemKind.SCALE_BAR,
        ItemKind.NORTH_ARROW,
        ItemKind.LIVE_COUNT,
    ],
)
def test_an_added_item_is_drawn_above_what_the_page_holds(qgis_app, project, kind):
    page = _covered_page(project)
    item = create_item(page.layout, kind, QRectF(150.0, 100.0, 60.0, 40.0), page.frame)
    assert _visible_pixels(page.layout, item) - {COVER}, f"{kind} is hidden"


def test_an_added_detail_map_is_drawn_above_what_the_page_holds(qgis_app, project):
    page = _covered_page(project)
    item = create_detail_map(page.layout, page.frame)
    assert _visible_pixels(page.layout, item) - {COVER}, "the detail map is hidden"
