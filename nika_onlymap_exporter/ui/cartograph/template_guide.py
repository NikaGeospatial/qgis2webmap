"""Instant layout guides: paint the template's boxes without reading map data.

These are deliberately labelled diagrams, never stand-ins for a rendered map.
The same plan as the real layout supplies the positions, colours and paper.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from ...core.cartograph_ir import LegendSpec, ScalebarSpec, ShapeSpec, TextSpec
from ...core.layout_templates import TemplatePlan
from ..i18n import tr


def layout_guide(plan: TemplatePlan, width: int) -> QImage:
    scale = width / plan.page_width
    image = QImage(
        width,
        max(1, round(plan.page_height * scale)),
        QImage.Format.Format_ARGB32,
    )
    background = QColor(plan.bg or "#ffffff")
    ink = QColor(plan.color or "#293d42")
    image.fill(background)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def rectangle(box) -> QRectF:
        return QRectF(box.x * scale, box.y * scale, box.w * scale, box.h * scale)

    def label(rect: QRectF, text: str, colour: QColor = ink) -> None:
        painter.setPen(colour)
        font = QFont()
        font.setPixelSize(max(7, round(width / 45)))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    for frame in plan.frames:
        rect = rectangle(frame.box)
        painter.save()
        painter.setClipRect(rect)
        painter.fillRect(rect, QColor("#dce8e2"))
        # A schematic landscape, shared by every guide: no project reads,
        # network tiles or QGIS render jobs are needed to compare designs.
        for row in range(6):
            line = QPainterPath(
                QPointF(rect.left(), rect.top() + row * rect.height() / 5)
            )
            line.cubicTo(
                rect.left() + rect.width() * 0.3,
                rect.top() + (row - 1) * rect.height() / 5,
                rect.left() + rect.width() * 0.6,
                rect.top() + (row + 1) * rect.height() / 5,
                rect.right(),
                rect.top() + row * rect.height() / 5,
            )
            painter.setPen(QPen(QColor("#b4c9bc"), 1))
            painter.drawPath(line)
        painter.setPen(QPen(QColor("#83b7c3"), max(2, width / 100)))
        painter.drawLine(rect.topLeft(), rect.bottomRight())
        label(rect, tr("Inset") if frame.overview_of else tr("Map"))
        painter.restore()
        painter.setPen(QPen(QColor(frame.frame_stroke or "#899c98"), 0.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    for item in sorted(plan.items, key=lambda item: item.z or 0):
        if not hasattr(item, "box"):
            continue
        rect = rectangle(item.box)
        if isinstance(item, ShapeSpec):
            painter.fillRect(rect, QColor(item.fill or background))
            continue
        bg = getattr(item, "bg", None)
        if bg:
            painter.fillRect(rect, QColor(bg))
        colour = QColor(getattr(item, "color", None) or ink)
        if isinstance(item, LegendSpec):
            rows = min(5, max(1, int(rect.height() / 9)))
            for i in range(rows):
                y = rect.top() + 3 + i * 9
                painter.fillRect(QRectF(rect.left() + 2, y, 4, 4), QColor("#779a8b"))
                painter.setPen(QPen(colour, 1))
                painter.drawLine(
                    QPointF(rect.left() + 10, y + 2),
                    QPointF(rect.right() - 3, y + 2),
                )
        elif isinstance(item, ScalebarSpec):
            bar = QRectF(rect.left(), rect.center().y(), rect.width(), 3)
            painter.fillRect(bar, colour)
        elif isinstance(item, TextSpec):
            if item.id == "title":
                label(rect, tr("Title"), colour)
            elif item.id == "notes":
                label(rect, tr("Notes"), colour)
            else:
                painter.setPen(QPen(colour, 1))
                painter.drawLine(
                    QPointF(rect.left() + 1, rect.center().y()),
                    QPointF(rect.right() - 1, rect.center().y()),
                )
    # Always identify the diagram, even if it is viewed without the intro.
    painter.fillRect(QRectF(0, image.height() - 15, width, 15), background)
    label(QRectF(0, image.height() - 15, width, 15), tr("Layout guide"))
    painter.end()
    return image
