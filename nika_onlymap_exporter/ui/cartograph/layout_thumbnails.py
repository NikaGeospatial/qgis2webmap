"""Thumbnails of the layouts that have been shown, kept in memory.

A thumbnail is the layout view's own pixels at the moment the layout was on
show (the preview panel); nothing here ever renders a layout. The chooser asks
for one per layout and draws a neutral placeholder for a layout that has not
been shown yet. Kept for the QGIS session, keyed by the layout's stable id
(`core/layout_identity.py`), so a rename keeps its thumbnail.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections import OrderedDict

from qgis.PyQt.QtCore import QRect, Qt
from qgis.PyQt.QtGui import QImage, QPixmap

from .layout_header import THUMBNAIL_SIZE

#: Layouts remembered; the oldest is dropped past this.
MAX_THUMBNAILS = 64

_CACHE: OrderedDict[str, QImage] = OrderedDict()


def remember(key: str, image: QImage) -> None:
    _CACHE[key] = image
    _CACHE.move_to_end(key)
    while len(_CACHE) > MAX_THUMBNAILS:
        _CACHE.popitem(last=False)


def recall(key: str | None) -> QPixmap | None:
    image = _CACHE.get(key) if key else None
    return QPixmap.fromImage(image) if image is not None else None


def clear() -> None:
    _CACHE.clear()


def grab_page(view: object, layout: object) -> QImage | None:
    """The page as the layout view shows it now, scaled to a thumbnail, or None
    when the view is not showing it or a map on it is still drawing (a grab
    then would keep a half-drawn page)."""
    if view.currentLayout() is not layout:  # type: ignore[attr-defined]
        return None
    pages = layout.pageCollection()
    if not pages.pageCount():
        return None
    for item in layout.items():
        is_drawing = getattr(item, "isDrawing", None)
        if callable(is_drawing) and is_drawing():
            return None
    scene_rect = pages.page(0).sceneBoundingRect()
    box = view.mapFromScene(scene_rect).boundingRect()  # type: ignore[attr-defined]
    viewport = view.viewport()  # type: ignore[attr-defined]
    box = box.intersected(QRect(0, 0, viewport.width(), viewport.height()))
    if box.width() < 8 or box.height() < 8:
        return None
    image = viewport.grab(box).toImage()
    return image.scaled(
        THUMBNAIL_SIZE * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
